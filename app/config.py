"""Configuration loaded from env vars / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    di_endpoint: str = ""
    di_key: str = ""
    classifier_id: str = ""
    applicationinsights_connection_string: str = ""
    tenant_id_header: str = "x-tenant-id"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
