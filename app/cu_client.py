"""Thin wrapper over Azure AI Content Understanding (Foundry Tools) REST API.

ACU GA (api-version=2025-11-01) is hosted on the same Foundry / AI Services
account as Document Intelligence, so we can reuse the existing endpoint and
managed identity. We talk to it over plain HTTPS rather than pulling in the
preview SDK to keep dependencies small.

Auth strategy mirrors di_client.DIClient:
  - If CU_KEY is set -> Ocp-Apim-Subscription-Key (handy for local dev).
  - Otherwise -> DefaultAzureCredential bearer token (UAMI in Container Apps).

This client implements the analyze long-running operation:
  1. POST  /contentunderstanding/analyzers/{id}:analyze        -> 202 + Operation-Location
  2. GET   {operationLocation} until status == "Succeeded"     -> result body

Returned `result` is the raw JSON dict from the service (CU SDK isn't required
for the demo). main.py adapts the relevant fields into per-segment dicts that
match the shape used by the DI strategies.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from .config import settings

# GA API version. Pin explicitly so behaviour is stable across service updates.
CU_API_VERSION = "2025-11-01"

# Conservative default polling cadence. CU analyze for a multi-page PDF
# typically completes in 5-30 s; we sleep briefly between polls.
_POLL_INITIAL_SECONDS = 1.0
_POLL_MAX_SECONDS = 4.0
_POLL_TIMEOUT_SECONDS = 180.0


class CUClient:
    """Minimal Content Understanding REST client used by the `cu` strategy."""

    def __init__(self) -> None:
        endpoint = settings.cu_endpoint or settings.di_endpoint
        if not endpoint:
            raise RuntimeError(
                "CU_ENDPOINT (or DI_ENDPOINT, if you reuse the same Foundry resource) must be set."
            )
        # Normalise: strip any trailing slash so URL composition is predictable.
        self._endpoint = endpoint.rstrip("/")

        self._key = settings.cu_key
        self._token_provider: Any = None
        if not self._key:
            # Lazy import so local dev without azure-identity still works when CU_KEY is set.
            try:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            except ImportError as e:
                raise RuntimeError(
                    "CU_KEY not set and azure-identity is not installed. "
                    "Either set CU_KEY or `pip install azure-identity` for managed-identity auth."
                ) from e
            credential = DefaultAzureCredential()
            # Cognitive Services AAD scope works for both DI and CU on a Foundry account.
            self._token_provider = get_bearer_token_provider(
                credential, "https://cognitiveservices.azure.com/.default"
            )

    # --------------------------------------------------------------- helpers
    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        h: dict[str, str] = {}
        if self._key:
            # Key auth — only for local dev convenience.
            h["Ocp-Apim-Subscription-Key"] = self._key
        else:
            # AAD bearer token — refreshed by the credential provider on each call.
            h["Authorization"] = f"Bearer {self._token_provider()}"
        if content_type:
            h["Content-Type"] = content_type
        return h

    # --------------------------------------------------------------- public API
    def analyze(self, *, analyzer_id: str, content: bytes) -> tuple[dict[str, Any], float]:
        """Submit a binary document to a CU analyzer and return (result, duration_ms).

        Implements the standard async LRO pattern:
          POST :analyze  ->  202 Accepted with Operation-Location header
          GET  {op-loc}  ->  poll until status == "Succeeded" / "Failed"

        The returned dict is the raw JSON `result` payload; main.py adapts it.
        """
        url = (
            f"{self._endpoint}/contentunderstanding/analyzers/"
            f"{analyzer_id}:analyze?api-version={CU_API_VERSION}"
        )
        start = time.perf_counter()

        # CU GA expects JSON with one of {url, base64Source, azureBlobSource}.
        # We always have raw bytes locally (post-PDF-split), so base64-encode.
        # NOTE: octet-stream POST returns 400 ContentEmpty on the GA API.
        import base64
        body = {"base64Source": base64.b64encode(content).decode("ascii")}
        resp = requests.post(
            url,
            headers=self._headers(content_type="application/json"),
            json=body,
            timeout=60,
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"CU analyze submit failed: HTTP {resp.status_code} body={resp.text[:500]}"
            )

        # Sync 200 short-circuit (rare but possible for tiny inputs).
        if resp.status_code == 200:
            duration_ms = (time.perf_counter() - start) * 1000.0
            return resp.json(), duration_ms

        op_url = resp.headers.get("Operation-Location") or resp.headers.get("operation-location")
        if not op_url:
            raise RuntimeError("CU analyze: no Operation-Location header on submit response.")

        # Poll. Exponential-ish backoff capped at _POLL_MAX_SECONDS.
        delay = _POLL_INITIAL_SECONDS
        deadline = start + _POLL_TIMEOUT_SECONDS
        while True:
            poll = requests.get(op_url, headers=self._headers(), timeout=30)
            if poll.status_code != 200:
                raise RuntimeError(
                    f"CU poll failed: HTTP {poll.status_code} body={poll.text[:500]}"
                )
            body = poll.json()
            status = (body.get("status") or "").lower()
            if status == "succeeded":
                duration_ms = (time.perf_counter() - start) * 1000.0
                return body, duration_ms
            if status in ("failed", "cancelled", "canceled"):
                raise RuntimeError(f"CU analyze {status}: {body.get('error') or body}")
            if time.perf_counter() > deadline:
                raise TimeoutError(f"CU analyze did not complete within {_POLL_TIMEOUT_SECONDS}s")
            time.sleep(delay)
            delay = min(delay * 1.5, _POLL_MAX_SECONDS)

    # --------------------------------------------------------------- adapters
    @staticmethod
    def summarize_fields(result: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalise CU result -> the same per-document shape DIClient.summarize_fields returns.

        CU result shape (abridged):
          {
            "result": {
              "contents": [
                {
                  "kind": "document",
                  "fields": { "<name>": { "type": "...", "valueString": "...", "confidence": 0.97 } },
                  ...
                }, ...
              ]
            }
          }

        We flatten each `contents[*]` entry that carries `fields` into:
          { doc_type, confidence, fields: { name: {value, confidence} } }
        so the frontend's existing field renderer works unchanged.
        """
        out: list[dict[str, Any]] = []
        contents = (((result or {}).get("result") or {}).get("contents")) or []
        for item in contents:
            raw_fields = item.get("fields") or {}
            if not raw_fields:
                continue
            fields: dict[str, dict[str, Any]] = {}
            for name, field in raw_fields.items():
                fields[name] = {
                    "value": _extract_value(field),
                    "confidence": field.get("confidence"),
                }
            out.append({
                "doc_type": item.get("category") or item.get("kind") or "document",
                "confidence": item.get("confidence"),
                "fields": fields,
            })
        return out

    @staticmethod
    def page_count(result: dict[str, Any]) -> int:
        """Best-effort total page count from a CU result, for cost estimates."""
        contents = (((result or {}).get("result") or {}).get("contents")) or []
        pages = 0
        for item in contents:
            # CU exposes per-content `pages` ("1-3") or a `pageRange` object;
            # walk both shapes defensively.
            pr = item.get("pageRange") or {}
            if isinstance(pr, dict) and pr.get("end") and pr.get("start"):
                pages = max(pages, int(pr["end"]))
            elif isinstance(item.get("pages"), str) and "-" in item["pages"]:
                try:
                    pages = max(pages, int(item["pages"].split("-")[-1]))
                except ValueError:
                    pass
            elif isinstance(item.get("pages"), int):
                pages = max(pages, item["pages"])
        return pages


def _extract_value(field: dict[str, Any]) -> Any:
    """Pull the typed value out of a CU field, preferring the most readable form."""
    # CU returns one of valueString / valueNumber / valueDate / valueArray / valueObject etc.
    for k in (
        "valueString",
        "valueNumber",
        "valueInteger",
        "valueDate",
        "valueTime",
        "valueBoolean",
        "valueCurrency",
        "valueAddress",
        "content",
    ):
        v = field.get(k)
        if v is not None:
            return v if isinstance(v, (str, int, float, bool)) else str(v)
    return None
