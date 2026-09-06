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
EnrichmentProvider = Literal["sample", "hunter"]


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
        default=False,
        description="Master switch, off by default. Write tools stay registered and "
        "discoverable so an agent can see what the server could do, but every mutation "
        "is refused with an audited, explicitly explained rejection until an operator "
        "opts in (D-019).",
    )
    dry_run_writes: bool = Field(
        default=False,
        description="When true, write tools validate the request and audit the attempt "
        "but never persist. The result reports outcome 'dry_run', which is not a success.",
    )
    max_write_batch_size: int = Field(
        default=1,
        ge=1,
        le=25,
        description="Upper bound on records mutated by a single write tool call. Every "
        "write passes its record count through one chokepoint, so a future batch-capable "
        "tool cannot bypass this limit.",
    )

    # --- External enrichment ------------------------------------------------
    # Provider selection (DECISIONS.md D-017). The default needs no credential:
    # 'sample' serves this repository's offline dataset so the server is
    # runnable and demonstrable on a fresh clone. 'hunter' is the live provider
    # and requires enrichment_api_key; selecting it without one fails at
    # startup rather than silently serving sample data.
    enrichment_provider: EnrichmentProvider = Field(
        default="sample",
        description="Which enrichment adapter to run: 'sample' (offline dataset, no "
        "credential) or 'hunter' (live API, requires enrichment_api_key).",
    )
    enrichment_api_key: SecretStr | None = Field(
        default=None,
        description="Credential for the live enrichment provider. Sent as a request "
        "header, never as a query parameter.",
    )
    enrichment_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=120,
        description="Per-request timeout for outbound enrichment calls. Kept above the "
        "provider's own search duration so a billed call is not abandoned mid-flight.",
    )
    enrichment_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Additional attempts after a transient failure (timeout or 5xx). "
        "Rejections and rate limits are never retried, so this cannot multiply credit "
        "spend. 0 disables retrying.",
    )
    enrichment_retry_backoff_seconds: float = Field(
        default=0.5,
        gt=0,
        le=30,
        description="Base delay between retries; attempt n waits backoff * 2^(n-1).",
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
