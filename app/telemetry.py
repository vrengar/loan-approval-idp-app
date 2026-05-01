"""App Insights / OpenTelemetry telemetry helpers.

Emits a custom event `di.pages.processed` per request with:
  tenantId, model, pageCount, estimatedCostUsd, durationMs

This is the *advanced* cost-tracking signal used by `loadtest/cost-allocation.kql`
to allocate spend per SaaS tenant.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .config import settings
from .pricing import estimate_cost_usd

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
