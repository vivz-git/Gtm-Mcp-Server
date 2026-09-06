"""Integration tests against a real PostgreSQL instance.

Run ``docker compose up -d db`` first. These are skipped, not failed, when no
database is reachable, so the default developer workflow stays fast while CI
still exercises the real driver.
"""

from __future__ import annotations

import pytest

from gtm_mcp.db.engine import build_engine, check_connection
from gtm_mcp.errors import RepositoryError
from gtm_mcp.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.mark.anyio
async def test_configured_database_is_reachable() -> None:
    """The DSN in the environment points at a working PostgreSQL instance."""
    settings = Settings()
    engine = build_engine(settings)
    try:
        await check_connection(engine, timeout_seconds=settings.db_connect_timeout_seconds)
    except RepositoryError as exc:
        pytest.skip(f"no database available: {exc.message}")
    finally:
        await engine.dispose()
