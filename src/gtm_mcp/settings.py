"""Application configuration, loaded from the environment.

All configuration enters the process here. No other module reads ``os.environ``
directly, which keeps the set of knobs discoverable and makes tests able to
construct an explicit ``Settings`` instance instead of mutating global state.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "production"]
LogFormat = Literal["console", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
Transport = Literal["stdio", "streamable-http"]


class Settings(BaseSettings):
    """Runtime configuration for the GTM MCP server.

    Values are read from environment variables prefixed with ``GTM_`` and, for
    local development, from a ``.env`` file. Secrets use ``SecretStr`` so that
    an accidental log or traceback renders ``**********`` rather than the value.
    """

    model_config = SettingsConfigDict(
        env_prefix="GTM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    # --- Identity -----------------------------------------------------------
    server_name: str = Field(
        default="gtm-mcp-server",
        description="Name advertised to MCP clients during initialization.",
    )
    environment: Environment = Field(
        default="local",
        description="Deployment environment; affects logging defaults only.",
    )

    # --- Transport ----------------------------------------------------------
    transport: Transport = Field(
        default="stdio",
        description="MCP transport. stdio for local clients, streamable-http for remote.",
    )
    http_host: str = Field(default="127.0.0.1", description="Bind host for streamable-http.")
    http_port: int = Field(default=8000, ge=1, le=65535, description="Bind port for HTTP.")

    # --- Observability ------------------------------------------------------
    log_level: LogLevel = Field(default="INFO", description="Root log level.")
    log_format: LogFormat = Field(
        default="json",
        description="'json' for machine-readable logs, 'console' for human-readable dev logs.",
    )

    # --- Data ---------------------------------------------------------------
    database_url: SecretStr = Field(
        default=SecretStr("postgresql+asyncpg://gtm:gtm@localhost:5432/gtm"),
        description="Async SQLAlchemy DSN for the mock CRM database.",
    )
    db_pool_size: int = Field(default=5, ge=1, le=50, description="Connection pool size.")
    db_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
        description="Hard cap on a single connection attempt, so a black-holed "
        "database host cannot hang server startup indefinitely.",
    )

    # --- Write guardrails ---------------------------------------------------
    # These are the enforcement points behind the architecture's write-safety
    # rules. They are read at call time by the write-tool boundary, so an
    # operator can run a strictly read-only deployment of the same image.
    enable_write_tools: bool = Field(
        default=True,
        description="Master switch. When false, write tools refuse with an explicit error.",
    )
    dry_run_writes: bool = Field(
        default=False,
        description="When true, write tools validate and audit but never persist.",
    )
    max_write_batch_size: int = Field(
        default=1,
        ge=1,
        le=25,
        description="Upper bound on records mutated by a single write tool call.",
    )

    # --- External enrichment ------------------------------------------------
    # Provider selection is deliberately deferred (see DECISIONS.md D-007).
    # The key is optional so the server starts and serves read-only CRM tools
    # without any third-party credential present.
    enrichment_api_key: SecretStr | None = Field(
        default=None,
        description="Credential for the external enrichment provider, once chosen.",
    )
    enrichment_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=120,
        description="Per-request timeout for outbound enrichment calls.",
    )

    @property
    def is_production(self) -> bool:
        """Whether the server is running in the production environment."""
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that repeated access is free and so that configuration is read
    once at startup rather than re-parsed per tool call. Tests that need a
    different configuration should construct ``Settings(...)`` directly, or call
    ``get_settings.cache_clear()``.
    """
    return Settings()
