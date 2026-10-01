from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config. Everything comes from GOVWATCH_* env vars (or a local .env)."""

    model_config = SettingsConfigDict(env_prefix="GOVWATCH_", env_file=".env", extra="ignore")

    # api.data.gov keys - the same key usually works for both APIs
    congress_api_key: SecretStr
    regulations_api_key: SecretStr

    database_url: str = "postgresql://govwatch:govwatch@localhost:5432/govwatch"

    # how often the worker polls each source
    poll_interval_seconds: int = Field(default=900, ge=60)
    # how far back to go on the very first run, before any cursor exists
    backfill_days: int = Field(default=7, ge=1, le=365)

    # completeness audits: how often, and how many full days back to reconcile
    audit_interval_seconds: int = Field(default=21600, ge=600)
    audit_window_days: int = Field(default=3, ge=1, le=30)
    # re-ingest an audit window automatically when records turn up missing
    audit_auto_repair: bool = True

    # 0.0.0.0 so prometheus can reach it inside a container; use 127.0.0.1 for local dev
    metrics_host: str = "0.0.0.0"
    metrics_port: int = 9100
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
