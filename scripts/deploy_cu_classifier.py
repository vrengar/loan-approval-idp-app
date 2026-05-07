"""Deploy the CU "router" classifier analyzer for the loan-doc pipeline.

This stands up a single Content Understanding analyzer that, in ONE call,
does split + classify + route-to-extractor for a multi-document loan PDF.
It replaces the app-side splitter (DI prebuilt-layout + keyword regex) and
the per-segment dispatch loop (CU_ANALYZER_BY_TYPE dict) — see the
"Before / After" note at the bottom of this file.

Pre-reqs:
  - CU_ENDPOINT (or DI_ENDPOINT on the same Foundry account) reachable.
  - Signed-in identity has `Cognitive Services User` on the AI Services account.

Usage:
  python scripts/deploy_cu_classifier.py \
      --analyzer-id loan_docs_router

The script is idempotent: it tries PUT first; on a 409/already-exists it
reports the existing definition. Pass `--force` to delete and recreate.

Docs:
  https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/concepts/classifier
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

# Reuse the app's auth helper so we get UAMI / az-CLI fallback for free.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings  # noqa: E402

CU_API_VERSION = "2025-11-01"


# ----------------------------------------------------------------------------
# Analyzer definition.
#
# This is the FULL out-of-the-box pipeline for our loan-doc scenario:
#   - `contentCategories` declares the doc types we care about (Task 1: split
#     boundaries are inferred per category, no training data required).
#   - Each category's `analyzerId` declares the extractor for that type
#     (Task 2: routing lives here, in the service definition — not in app
#     code as a Python dict).
#   - `enableSegment` is set on the per-request analyze body, not in the
#     definition. See `_run_cu_pipeline` in app/main.py.
#
# GA quirk: an `other` category is REQUIRED so unmatched pages have somewhere
# to land. We omit `analyzerId` on `other` so those segments are classified
# but NOT extracted (saves the per-page extraction meter on garbage pages).
# ----------------------------------------------------------------------------
ANALYZER_DEFINITION: dict[str, Any] = {
    # Required at root by CU GA. All document-mode analyzers ultimately
    # inherit from `prebuilt-document` (the base OCR/layout analyzer).
    "baseAnalyzerId": "prebuilt-document",
    "description": (
        "Loan-application packet router. Classifies multi-doc PDFs into paystub, "
        "bank statement, W-2, passport, and driver's license, splits them into "
        "page-range segments, and routes each segment to the matching CU "
        "prebuilt extractor. One API call replaces the app-side splitter + dict."
    ),
    # GA classifier requires the LLM model used for category matching.
    "models": {"completion": "gpt-4.1"},
    # GA shape: classifier fields live under `config`, NOT at root. Matches
    # the official sample notebook (Azure-Samples/azure-ai-content-understanding-python
    # notebooks/classifier.ipynb). enableSegment is set here at definition time;
    # request-time enableSegment also works but config-level is the GA default.
    "config": {
        "returnDetails": True,
        "enableSegment": True,
        "contentCategories": {
        "paystub": {
            "description": (
                "US pay stub or paycheck statement showing employer name, employee "
                "name, pay period dates, gross/net pay, deductions, and YTD totals."
            ),
            "analyzerId": "prebuilt-payStub.us",
        },
        "bank_statement": {
            "description": (
                "Bank account statement listing the account holder, account number, "
                "statement period, opening/closing balances, and a transactions table."
            ),
            "analyzerId": "prebuilt-bankStatement.us",
        },
        "w2": {
            "description": (
                "IRS Form W-2 'Wage and Tax Statement' showing employer EIN, "
                "employee SSN, wages tips and other compensation, and federal/state "
                "tax withheld for a tax year."
            ),
            "analyzerId": "prebuilt-tax.us.w2",
        },
        "passport": {
            "description": (
                "Passport identity / data page showing full name, date of birth, "
                "passport number, issuing country, and machine-readable zone."
            ),
            "analyzerId": "prebuilt-idDocument.passport",
        },
        "drivers_license": {
            "description": (
                "US driver's license or state-issued ID card showing full name, "
                "date of birth, address, license number, and issuing state."
            ),
            "analyzerId": "prebuilt-idDocument",
        },
        "other": {
            # GA requirement: include an `other` bucket so low-confidence pages
            # are not force-fit into one of the labeled categories. No
            # `analyzerId` -> classified-only, no extraction billed.
            "description": (
                "Anything that is not clearly one of the loan document types above "
                "(e.g., cover letters, blank separator pages, unknown forms)."
            ),
        },
        },
    },
}

# ----------------------------------------------------------------------------
# REFERENCE: extending a CU prebuilt with a custom analyzer.
#
# Today our `analyzerId` values point to CU prebuilts as-is. If we ever want
# to add fields beyond what Microsoft's prebuilt schema returns -- including
# LLM-derived "generate" fields like pay_frequency or income_stability that
# DI literally cannot produce -- the change is purely a JSON edit:
#
#   1) PUT a custom analyzer that inherits a prebuilt via baseAnalyzerId.
#   2) Repoint the matching category in this file from the prebuilt id to
#      the new custom analyzer id.
#
# Example (DO NOT deploy as-is; this is documentation):
#
#   CUSTOM_PAYSTUB_ANALYZER = {
#       "analyzerId": "paystub-loan-v1",
#       "baseAnalyzerId": "prebuilt-payStub.us",   # <-- inherits all prebuilt fields
#       "fieldSchema": {
#           "fields": {
#               # method=extract  -> OCR-grounded, same as DI extraction
#               "employer_address": {"type": "string", "method": "extract"},
#
#               # method=generate -> LLM-grounded derivation, no training needed.
#               # This is the CU-only capability that DI cannot match.
#               "pay_frequency": {
#                   "type": "string",
#                   "method": "generate",
#                   "description": (
#                       "Determine pay frequency: weekly, biweekly, "
#                       "semimonthly, or monthly, based on pay period dates."
#                   ),
#               },
#               "income_stability": {
#                   "type": "string",
#                   "method": "generate",
#                   "description": (
#                       "Stable | Variable | Declining, based on YTD gross "
#                       "vs current pay period * elapsed periods."
#                   ),
#               },
#           },
#       },
#   }
#
# Then in ANALYZER_DEFINITION above:
#   "paystub": {"description": "...", "analyzerId": "paystub-loan-v1"}
#
# Net change: zero app code, one JSON deploy. The cu pipeline picks up the
# new fields automatically because it just iterates result.contents[].fields.
# ----------------------------------------------------------------------------


def _endpoint() -> str:
    ep = (settings.cu_endpoint or settings.di_endpoint or "").rstrip("/")
    if not ep:
        sys.exit("CU_ENDPOINT or DI_ENDPOINT must be set.")
    return ep


def _headers() -> dict[str, str]:
    if settings.cu_key:
        return {
            "Ocp-Apim-Subscription-Key": settings.cu_key,
            "Content-Type": "application/json",
        }
    # AAD bearer via DefaultAzureCredential (UAMI in Container Apps, az-cli locally).
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415

    token = DefaultAzureCredential().get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _analyzer_url(analyzer_id: str) -> str:
    return (
        f"{_endpoint()}/contentunderstanding/analyzers/"
        f"{analyzer_id}?api-version={CU_API_VERSION}"
    )


def get_analyzer(analyzer_id: str) -> dict | None:
    r = requests.get(_analyzer_url(analyzer_id), headers=_headers(), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def delete_analyzer(analyzer_id: str) -> None:
    r = requests.delete(_analyzer_url(analyzer_id), headers=_headers(), timeout=30)
    if r.status_code not in (200, 202, 204, 404):
        r.raise_for_status()


def put_analyzer(analyzer_id: str, body: dict[str, Any]) -> dict:
    """Create the analyzer. CU returns 201 + Operation-Location and provisions async."""
    r = requests.put(
        _analyzer_url(analyzer_id),
        headers=_headers(),
        json=body,
        timeout=60,
    )
    if r.status_code not in (200, 201, 202):
        sys.exit(f"PUT failed: HTTP {r.status_code} body={r.text[:800]}")

    # Poll Operation-Location until provisioning completes.
    op = r.headers.get("Operation-Location") or r.headers.get("operation-location")
    if not op:
        return r.json() if r.text else {}
    deadline = time.time() + 180
    while time.time() < deadline:
        poll = requests.get(op, headers=_headers(), timeout=30)
        poll.raise_for_status()
        body = poll.json()
        status = (body.get("status") or "").lower()
        if status == "succeeded":
            return body
        if status in ("failed", "cancelled", "canceled"):
            sys.exit(f"Analyzer provisioning {status}: {body.get('error') or body}")
        time.sleep(2)
    sys.exit("Analyzer provisioning did not complete within 180s.")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--analyzer-id", default="loan_docs_router",
                   help="Analyzer id to create (default: loan_docs_router). Note: CU rejects '-' in analyzer ids.")
    p.add_argument("--force", action="store_true",
                   help="Delete and recreate if it already exists.")
    p.add_argument("--show", action="store_true",
                   help="Just print the existing definition and exit.")
    args = p.parse_args()

    existing = get_analyzer(args.analyzer_id)
    if args.show:
        print(json.dumps(existing or {"error": "not found"}, indent=2))
        return

    if existing and not args.force:
        print(f"Analyzer '{args.analyzer_id}' already exists. Pass --force to recreate.")
        return

    if existing and args.force:
        print(f"Deleting existing analyzer '{args.analyzer_id}'...")
        delete_analyzer(args.analyzer_id)

    print(f"Creating analyzer '{args.analyzer_id}'...")
    result = put_analyzer(args.analyzer_id, ANALYZER_DEFINITION)
    print("Done. Status:", result.get("status", "succeeded"))
    print(f"Set CU_ROUTER_ANALYZER_ID={args.analyzer_id} in your app env.")


if __name__ == "__main__":
    main()
