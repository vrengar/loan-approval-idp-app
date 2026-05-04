"""App Insights / OpenTelemetry telemetry helpers.

Emits one custom event per IDP service call:
  - `di.pages.processed`  — every Document Intelligence analyze/classify call
  - `cu.calls.processed`  — every Content Understanding analyze call

Each row carries: tenantId, model/analyzer, pageCount, estimatedCostUsd,
durationMs, priceVersion, service, plus any caller-supplied extras
(correlationId, splitStrategy, docType, etc.).

These are the *advanced* cost-tracking signals used by
`loadtest/cost-allocation.kql` to allocate spend per SaaS tenant per service.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .config import settings
from .pricing import PRICE_VERSION, estimate_cost_usd

_log = logging.getLogger("idp.telemetry")
_configured = False


def configure() -> None:
    """Wire the OpenTelemetry exporter to Azure Monitor (App Insights).

    Called once at module import time from main.py. Idempotent — safe to call
    multiple times. After this runs, every record sent to a logger under the
    "idp.*" hierarchy is shipped to App Insights as a row on the `traces` table.
    """
    global _configured
    if _configured:
        return
    # Connection string is injected by Container Apps as an env var; fall back
    # to the Settings object for local dev (loaded from .env).
    conn = settings.applicationinsights_connection_string or os.getenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
    )
    if conn:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            # logger_name="idp" attaches the OTel handler to the parent of
            # "idp.api" and "idp.telemetry" so all our logs flow to App Insights.
            configure_azure_monitor(connection_string=conn, logger_name="idp")
            _log.info("Azure Monitor configured.")
        except Exception as exc:  # noqa: BLE001
            # Never let a telemetry failure crash the app — log and keep running.
            _log.warning("Azure Monitor not configured: %s", exc)
    _configured = True


def emit_pages_processed(
    *, tenant_id: str, model: str, pages: int, duration_ms: float, extra: dict[str, Any] | None = None
) -> None:
    """Emit a `di.pages.processed` trace row used for per-tenant cost allocation.

    One row is written per Document Intelligence call (split pass + each segment),
    so a single /process request produces ~N+1 rows. The KQL in
    loadtest/cost-allocation.kql joins these against the unit-price table to
    compute spend by tenant / strategy / model.
    """
    # Compute the dollar estimate inline so it's stamped on the trace itself.
    cost = estimate_cost_usd(model, pages)
    payload = {
        "event": "di.pages.processed",
        "service": "di",
        "priceVersion": PRICE_VERSION,
        "tenantId": tenant_id,
        "model": model,
        "pageCount": pages,
        "estimatedCostUsd": cost,
        "durationMs": round(duration_ms, 2),
    }
    if extra:
        # Caller adds correlationId + stage/docType/pageStart/pageEnd/splitStrategy.
        payload.update(extra)
    # IMPORTANT: pass `extra=payload` (flat) — do NOT wrap under
    # extra={"custom_dimensions": payload}. The Azure Monitor OTel logging
    # handler treats each key in `extra` as its own customDimension on the
    # App Insights `traces` row, which is what makes
    # `tostring(customDimensions.tenantId)` work in KQL.
    _log.info("di.pages.processed", extra=payload)


def emit_cu_call_processed(
    *,
    tenant_id: str,
    analyzer_id: str,
    pricing_key: str,
    pages: int,
    duration_ms: float,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a `cu.calls.processed` trace row for a Content Understanding analyze call.

    Mirrors `emit_pages_processed` so KQL can union both event types and
    summarise per-tenant spend across DI and CU. `pricing_key` is the entry in
    UNIT_PRICE_PER_1K_PAGES (e.g., "cu.prebuilt") used for the cost estimate;
    `analyzer_id` is the actual CU analyzer name (e.g., "prebuilt-payStub.us")
    kept as a separate dimension for human-readable filtering.
    """
    cost = estimate_cost_usd(pricing_key, pages)
    payload = {
        "event": "cu.calls.processed",
        "service": "cu",
        "priceVersion": PRICE_VERSION,
        "tenantId": tenant_id,
        "model": analyzer_id,         # mirror DI's column name for KQL union convenience
        "pricingKey": pricing_key,    # the row in UNIT_PRICE_PER_1K_PAGES used
        "pageCount": pages,
        "estimatedCostUsd": cost,
        "durationMs": round(duration_ms, 2),
    }
    if extra:
        payload.update(extra)
    _log.info("cu.calls.processed", extra=payload)

