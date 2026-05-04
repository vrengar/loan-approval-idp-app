"""Configuration loaded from env vars / .env.

All settings come from environment variables (set by Container Apps in prod
or a local `.env` file in dev). Pydantic validates and types them on startup.
Unset values default to empty string so the app can boot and surface a 503
at request time with a helpful message instead of crashing on import.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Azure DI account endpoint, e.g. https://ai-demo-xxxx.cognitiveservices.azure.com/
    di_endpoint: str = ""
    # Optional: only used if managed identity is unavailable. Prod uses MI -> leave blank.
    di_key: str = ""
    # Custom classifier id trained in DI Studio (e.g. "idp-loan-docs-v1").
    # If empty, mode=classifier returns 503 at request time.
    classifier_id: str = ""
    # ----- Azure Content Understanding (Foundry Tools) -----
    # If empty, defaults to `di_endpoint` (same Foundry account hosts both DI and CU).
    cu_endpoint: str = ""
    # Optional: only used if managed identity is unavailable. Prod uses MI -> leave blank.
    cu_key: str = ""
    # ID of a custom CU "router" analyzer that classifies + segments + extracts in one
    # call (Option B). When empty, the `cu` strategy falls back to per-segment routing
    # using the existing splitter + ACU prebuilts (Option A).
    cu_router_analyzer_id: str = ""

    # App Insights connection string (auto-injected by Container Apps when enabled).
    applicationinsights_connection_string: str = ""
    # Header name the API reads to identify the calling SaaS tenant for cost allocation.
    tenant_id_header: str = "x-tenant-id"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Single shared instance imported throughout the app.
settings = Settings()
