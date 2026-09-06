"""Configuration loading and its guardrail defaults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from gtm_mcp.settings import Settings


@pytest.mark.unit
def test_values_are_read_from_prefixed_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GTM_ENVIRONMENT", "production")
    monkeypatch.setenv("GTM_ENABLE_WRITE_TOOLS", "false")
    settings = Settings(_env_file=None)
    assert settings.is_production is True
    assert settings.enable_write_tools is False


@pytest.mark.unit
def test_unknown_settings_are_rejected() -> None:
    """A typo in an env var must fail loudly, not silently disable a guardrail."""
    with pytest.raises(PydanticValidationError):
        Settings(enable_writ_tools=False)  # type: ignore[call-arg]


@pytest.mark.unit
def test_secrets_are_not_exposed_by_repr() -> None:
    """The DSN carries a password and lands in tracebacks and logs."""
    settings = Settings(database_url="postgresql+asyncpg://u:supersecret@localhost/db")
    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings.database_url)
    assert "supersecret" in settings.database_url.get_secret_value()


@pytest.mark.unit
def test_settings_are_immutable() -> None:
    """Configuration must not drift at runtime; a guardrail you can flip is not one."""
    settings = Settings()
    with pytest.raises(PydanticValidationError):
        settings.enable_write_tools = False  # type: ignore[misc]


@pytest.mark.unit
def test_dry_run_is_off_and_batch_size_is_one_by_default() -> None:
    """Conservative write defaults: no surprise batching, no silent no-op mode."""
    settings = Settings()
    assert settings.dry_run_writes is False
    assert settings.max_write_batch_size == 1


@pytest.mark.unit
@pytest.mark.parametrize("bad_size", [0, 26])
def test_write_batch_size_is_bounded(bad_size: int) -> None:
    with pytest.raises(PydanticValidationError):
        Settings(max_write_batch_size=bad_size)


@pytest.mark.unit
def test_server_starts_without_an_enrichment_credential() -> None:
    """Read-only CRM use must not require a third-party key."""
    assert Settings().enrichment_api_key is None
