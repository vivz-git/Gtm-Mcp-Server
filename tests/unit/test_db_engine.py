"""Database connection management."""

from __future__ import annotations

import pytest

from gtm_mcp.db.engine import build_engine, build_session_factory, check_connection
from gtm_mcp.errors import RepositoryError
from gtm_mcp.settings import Settings

pytestmark = pytest.mark.unit

UNREACHABLE_DSN = "postgresql+asyncpg://nobody:nobody@127.0.0.1:1/absent"


@pytest.fixture
def unreachable_settings() -> Settings:
    return Settings(database_url=UNREACHABLE_DSN, db_connect_timeout_seconds=0.25)


def test_engine_does_not_connect_on_construction(unreachable_settings: Settings) -> None:
    """Connections are lazy, so building the server never blocks on the network."""
    assert build_engine(unreachable_settings) is not None


def test_sessions_survive_commit(unreachable_settings: Settings) -> None:
    """expire_on_commit must stay off, or domain objects go stale after a write."""
    factory = build_session_factory(build_engine(unreachable_settings))
    assert factory.kw["expire_on_commit"] is False


@pytest.mark.anyio
async def test_unreachable_database_raises_an_actionable_repository_error(
    unreachable_settings: Settings,
) -> None:
    """The message must tell an operator what to do, not just that it failed."""
    engine = build_engine(unreachable_settings)
    try:
        with pytest.raises(RepositoryError) as excinfo:
            await check_connection(
                engine, timeout_seconds=unreachable_settings.db_connect_timeout_seconds
            )
    finally:
        await engine.dispose()

    assert excinfo.value.code == "repository_error"
    assert "docker compose" in excinfo.value.message or "reachable" in excinfo.value.message


@pytest.mark.anyio
async def test_probe_is_bounded_by_its_timeout(unreachable_settings: Settings) -> None:
    """A black-holed host must not be able to stall server startup."""
    import time

    engine = build_engine(unreachable_settings)
    started = time.perf_counter()
    try:
        with pytest.raises(RepositoryError):
            await check_connection(engine, timeout_seconds=0.25)
    finally:
        await engine.dispose()

    assert time.perf_counter() - started < 2.0
