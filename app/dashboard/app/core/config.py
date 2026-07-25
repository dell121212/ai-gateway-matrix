"""Central application settings for professional backend + billing."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Auth: local | token | accounts
    dashboard_auth: Literal["local", "token", "accounts"] = Field(
        default="local", alias="DASHBOARD_AUTH"
    )
    dashboard_token: str = Field(default="", alias="DASHBOARD_TOKEN")

    # Database (same Postgres as LiteLLM, isolated schema)
    database_url: str = Field(
        default="postgresql+asyncpg://litellm:litellm@localhost:5432/litellm",
        alias="PRIVATE_API_DATABASE_URL",
    )
    db_schema: str = Field(default="private_api", alias="PRIVATE_API_DB_SCHEMA")

    # Billing
    credits_per_usd: int = Field(default=1000, alias="CREDITS_PER_USD")
    service_multiplier: float = Field(default=1.0, alias="BILLING_SERVICE_MULTIPLIER")
    minimum_microcredits: int = Field(default=1, alias="BILLING_MINIMUM_MICROCREDITS")
    billing_retry_policy: Literal["successful_only", "all_attempts"] = Field(
        default="successful_only", alias="BILLING_RETRY_POLICY"
    )
    billing_fail_mode: Literal["closed", "open"] = Field(
        default="open", alias="BILLING_FAIL_MODE"
    )
    default_request_mode: Literal["strict", "agent-stream"] = Field(
        default="agent-stream", alias="DEFAULT_REQUEST_MODE"
    )
    store_prompt_content: bool = Field(default=False, alias="STORE_PROMPT_CONTENT")

    # Redis
    redis_host: str = Field(default="127.0.0.1", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str = Field(default="", alias="REDIS_PASSWORD")
    redis_stream_maxlen: int = Field(default=5000, alias="REDIS_STREAM_MAXLEN")
    estimate_publish_interval_ms: int = Field(
        default=300, alias="ESTIMATE_PUBLISH_INTERVAL_MS"
    )

    # Session / security
    session_ttl_hours: int = Field(default=168, alias="SESSION_TTL_HOURS")
    login_rate_limit: int = Field(default=10, alias="LOGIN_RATE_LIMIT_PER_MIN")
    bootstrap_admin_username: str = Field(
        default="admin", alias="BOOTSTRAP_ADMIN_USERNAME"
    )
    bootstrap_admin_password: str = Field(default="", alias="BOOTSTRAP_ADMIN_PASSWORD")
    initial_user_microcredits: int = Field(
        default=10_000_000_000, alias="INITIAL_USER_MICROCREDITS"
    )  # 10_000 credits default

    # Paths
    litellm_upstream_url: str = Field(
        default="http://ai-gateway-matrix:4000", alias="LITELLM_UPSTREAM_URL"
    )
    client_keys_store: str = Field(default="", alias="CLIENT_KEYS_STORE")

    def async_database_url(self) -> str:
        url = os.environ.get("PRIVATE_API_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if url:
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            if url.startswith("postgres://"):
                return url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        # Compose injects POSTGRES_PASSWORD
        pw = os.environ.get("POSTGRES_PASSWORD", "litellm")
        host = os.environ.get("POSTGRES_HOST", "postgres")
        port = os.environ.get("POSTGRES_PORT", "5432")
        return f"postgresql+asyncpg://litellm:{pw}@{host}:{port}/litellm"

    def redis_url(self) -> str:
        host = os.environ.get("REDIS_HOST", self.redis_host)
        port = int(os.environ.get("REDIS_PORT", str(self.redis_port)))
        password = os.environ.get("REDIS_PASSWORD", self.redis_password) or ""
        if password:
            return f"redis://:{password}@{host}:{port}/0"
        return f"redis://{host}:{port}/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
