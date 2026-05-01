"""Train (or retrain) the IDP custom classifier from blobs in Azure Storage.

Pre-reqs (one-time):
  1. `python -m app.sample.generate_training_set --count 8 --out samples/training`
  2. Upload `samples/training/` into a blob container (one folder per class).
  3. Your signed-in user (or the SP/MI running this) needs `Cognitive Services User`
     on the DI account. The DI service itself reads the blobs via the SAS we mint here,
     so no extra role on storage is needed for DI.

Usage:
  python scripts/train_classifier.py \
      --classifier-id idp-loan-docs-v1 \
      --container classifier-training

Env vars (set automatically by `azd env get-values`):
  DI_ENDPOINT                       — required
  AZURE_STORAGE_ACCOUNT             — optional; auto-discovered from RG if absent
  AZURE_RESOURCE_GROUP              — used for storage auto-discovery (default IDP-rg)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from azure.ai.documentintelligence import DocumentIntelligenceAdministrationClient
from azure.ai.documentintelligence.models import (
    AzureBlobContentSource,
    BuildDocumentClassifierRequest,
    ClassifierDocumentTypeDetails,
)
from azure.core.credentials import AccessToken, AzureKeyCredential, TokenCredential

CLASSES = ("paystub", "bank_statement", "w2", "passport", "drivers_license")


def _az(*args: str) -> str:
    # `az` is a .cmd shim on Windows; use shell=True so the shim resolves.
    cmd = " ".join(["az", *(f'"{a}"' if " " in a else a for a in args)])
    return subprocess.check_output(cmd, text=True, shell=True).strip()


class AzCliCredential(TokenCredential):
    """Minimal TokenCredential that shells out to `az account get-access-token`.

    Avoids needing the `azure-identity` package (which pulls `cryptography`,
    a Rust/MSVC build on win-arm64 Py 3.13).
    """

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        # Cognitive Services resource is the only one we need here.
        out = _az(
            "account", "get-access-token",
            "--resource", "https://cognitiveservices.azure.com",
            "-o", "json",
        )
        data = json.loads(out)
        # expiresOn is local time string; expires_on (epoch) was added in newer az.
        if "expires_on" in data:
            exp = int(data["expires_on"])
        else:
            # Fall back: parse "YYYY-MM-DD HH:MM:SS.ffffff" (local time).
            ts = data["expiresOn"].split(".")[0]
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            exp = int(dt.timestamp())
        return AccessToken(data["accessToken"], exp)


def discover_storage_account(rg: str) -> str:
    name = _az("storage", "account", "list", "-g", rg, "--query", "[0].name", "-o", "tsv")
    if not name:
        raise SystemExit(f"No storage account found in resource group {rg!r}.")
    return name


def mint_container_sas(account: str, container: str, hours: int = 4) -> str:
    """Mint a user-delegation SAS via `az` (avoids needing the cryptography wheel)."""
    expiry = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _az(
        "storage", "container", "generate-sas",
        "--account-name", account,
        "-n", container,
        "--permissions", "rl",
        "--expiry", expiry,
        "--https-only",
        "--auth-mode", "login",
        "--as-user",
        "-o", "tsv",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classifier-id", required=True, help="Logical id for the new classifier.")
    ap.add_argument("--container", default="classifier-training")
    ap.add_argument("--rg", default=os.getenv("AZURE_RESOURCE_GROUP", "IDP-rg"))
    ap.add_argument("--storage-account", default=os.getenv("AZURE_STORAGE_ACCOUNT", ""))
    ap.add_argument("--endpoint", default=os.getenv("DI_ENDPOINT", ""))
    ap.add_argument("--description", default="IDP loan-doc classifier (paystub/bank/w2/passport/dl).")
    args = ap.parse_args()

    if not args.endpoint:
        sys.exit("DI_ENDPOINT not set. Run `azd env get-values | Out-String | Invoke-Expression` first.")

    account = args.storage_account or discover_storage_account(args.rg)
    print(f"Storage account: {account}")
    print(f"Container:       {args.container}")
    print(f"DI endpoint:     {args.endpoint}")
    print(f"Classifier id:   {args.classifier_id}")

    sas = mint_container_sas(account, args.container)
    container_url = f"https://{account}.blob.core.windows.net/{args.container}?{sas}"

    doc_types = {
        cls: ClassifierDocumentTypeDetails(
            azure_blob_source=AzureBlobContentSource(
                container_url=container_url,
                prefix=f"{cls}/",
            )
        )
        for cls in CLASSES
    }

    di_key = os.environ.get("DI_KEY")
    if di_key:
        print("Using API key auth (DI_KEY env var set).")
        cred = AzureKeyCredential(di_key)
    else:
        print("Using Entra (az CLI) auth.")
        cred = AzCliCredential()
    admin = DocumentIntelligenceAdministrationClient(
        endpoint=args.endpoint,
        credential=cred,
    )

    # If a classifier with this id already exists, delete it so we can re-train.
    try:
        existing = admin.get_classifier(args.classifier_id)
        if existing:
            print(f"Deleting existing classifier {args.classifier_id} ...")
            admin.delete_classifier(args.classifier_id)
    except Exception:
        pass

    print("Submitting build request ...")
    poller = admin.begin_build_classifier(
        BuildDocumentClassifierRequest(
            classifier_id=args.classifier_id,
            description=args.description,
            doc_types=doc_types,
        )
    )
    print("Building (this typically takes 1-5 minutes) ...")
    result = poller.result()
    print(f"\nDone. Classifier ready: {result.classifier_id}")
    print(f"  api version : {result.api_version}")
    print(f"  created     : {result.created_date_time}")
    print(f"  doc types   : {sorted((result.doc_types or {}).keys())}")
    print("\nNext:")
    print(f"  azd env set CLASSIFIER_ID {result.classifier_id}")
    print( "  azd deploy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
