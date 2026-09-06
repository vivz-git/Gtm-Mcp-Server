"""Structural guarantees the write path relies on.

These assert properties of the source itself rather than of one execution. Every
behavioural test in this suite proves that the current code does the right
thing; these prove that the *shape* which makes it hard to do the wrong thing is
still in place — no delete anywhere, no SQL in a tool, no second route to the
repository.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from gtm_mcp import ports, tools
from gtm_mcp.services.crm import CrmService

pytestmark = [pytest.mark.unit]

SOURCE_ROOT = Path(inspect.getfile(ports)).parent

#: SQL and ORM constructs that would remove data. None may appear in the source.
DESTRUCTIVE_FRAGMENTS = (
    "delete from",
    "drop table",
    "truncate ",
    "sqlalchemy import delete",
    ".delete()",
)

#: Names that would suggest a destructive capability on the persistence port.
FORBIDDEN_PORT_METHODS = ("delete", "remove", "destroy", "purge", "drop", "truncate", "wipe")


def _python_sources() -> list[Path]:
    """Return every source file in the package.

    Returns:
        Paths of the package's Python modules.
    """
    return sorted(SOURCE_ROOT.rglob("*.py"))


def test_no_module_contains_a_statement_that_could_remove_data() -> None:
    """The no-delete rule is only real if nothing in the tree can express one."""
    offenders: list[str] = []
    for path in _python_sources():
        lowered = path.read_text(encoding="utf-8").lower()
        offenders += [
            f"{path.name}: {fragment}" for fragment in DESTRUCTIVE_FRAGMENTS if fragment in lowered
        ]
    assert not offenders, f"destructive construct found: {offenders}"


def test_the_persistence_port_offers_no_destructive_method() -> None:
    """A capability absent from the interface cannot be reached by any tool."""
    methods = [name for name in dir(ports.CrmRepository) if not name.startswith("_")]
    for name in methods:
        assert not any(fragment in name.lower() for fragment in FORBIDDEN_PORT_METHODS), name


def test_the_tool_layer_imports_no_database_machinery() -> None:
    """Tools that could reach SQLAlchemy would be free to bypass every guardrail."""
    tools_dir = Path(inspect.getfile(tools)).parent
    for path in sorted(tools_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("import sqlalchemy", "from sqlalchemy", "PostgresCrmRepository"):
            assert forbidden not in source, f"{path.name} reaches past the service layer"


def test_the_service_is_the_only_caller_of_a_repository_write_method() -> None:
    """One guarded route in means the guardrails cannot be routed around.

    The service's own methods are named differently from the port's
    (``save_contact_to_list`` versus ``add_contact_to_list``) precisely so this
    check can tell a legitimate call to the service from a call that reaches
    past it to the repository.
    """
    write_methods = ("upsert_contact", "add_contact_to_list")
    service_file = Path(inspect.getfile(CrmService))
    callers: dict[str, list[str]] = {method: [] for method in write_methods}

    for path in _python_sources():
        if path == service_file or path.name == "repository.py" or path.name == "ports.py":
            continue
        source = path.read_text(encoding="utf-8")
        for method in write_methods:
            if f".{method}(" in source:
                callers[method].append(path.name)

    assert callers == {method: [] for method in write_methods}, (
        f"a repository write is reachable outside the guarded service path: {callers}"
    )
