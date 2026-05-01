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
    global _configured
    if _configured:
        return
    conn = settings.applicationinsights_connection_string or os.getenv(
        "APPLICATIONINSIGHTS_CONNECTION_STRING", ""
    )
    if conn:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=conn, logger_name="idp")
            _log.info("Azure Monitor configured.")
        except Exception as exc:  # noqa: BLE001
            _log.warning("Azure Monitor not configured: %s", exc)
    _configured = True


def emit_pages_processed(
    *, tenant_id: str, model: str, pages: int, duration_ms: float, extra: dict[str, Any] | None = None
) -> None:
    """Log a structured event picked up by App Insights as customEvents."""
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
        payload.update(extra)
    # Each key in `extra` becomes its own customDimension on the App Insights trace.
    # (Don't wrap under a single 'custom_dimensions' key — that gets stringified.)
    _log.info("di.pages.processed", extra=payload)
