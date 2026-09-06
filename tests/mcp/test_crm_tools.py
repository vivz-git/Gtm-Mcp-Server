"""The CRM tools as an MCP client actually sees them.

Every test here drives a real in-memory protocol session, so registration,
schema derivation, annotations, lifespan injection, argument validation and
error routing are exercised together. Calling the Python functions directly
would cover none of that, and the guardrails are only worth anything if they
hold on the path a real client takes.

The repository and audit sink are in-memory doubles (``tests/fakes``); the
service, the tools and the protocol are the real thing. The same guardrail and
merge behaviour is re-proved against PostgreSQL in ``tests/integration``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gtm_mcp.audit.events import AuditOperation
from gtm_mcp.context import AppContext
from gtm_mcp.domain.models import Contact, RecordSource
from gtm_mcp.server.app import build_server
from gtm_mcp.services.crm import CrmService
from gtm_mcp.settings import Settings
from mcp import Client, MCPError
from tests.fakes import InMemoryCrmRepository, RecordingAuditSink

pytestmark = [pytest.mark.mcp, pytest.mark.anyio]

CRM_TOOLS = ("crm_query", "sync_to_crm", "save_to_list")
WRITE_TOOLS = ("sync_to_crm", "save_to_list")

CONTACT_ARGS: dict[str, Any] = {
    "contact": {
        "full_name": "Elena Rostova",
        "email": "elena@cloudscale.io",
        "title": "Head of Revenue Operations",
        "company_domain": "cloudscale.io",
    }
}


class CrmHarness:
    """A connected client plus the doubles the server was wired with."""

    def __init__(
        self, client: Client, repo: InMemoryCrmRepository, sink: RecordingAuditSink
    ) -> None:
        """Store the client and the doubles behind it.

        Args:
            client: The connected MCP client.
            repo: The in-memory repository the server is using.
            sink: The audit sink the server is using.
        """
        self.client = client
        self.repo = repo
        self.sink = sink

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a tool and return its structured content.

        Args:
            name: Tool name.
            arguments: Tool arguments.

        Returns:
            The tool's structured result payload.
        """
        result = await self.client.call_tool(name, arguments or {})
        assert result.structured_content is not None, result.content
        return result.structured_content


def _harness_factory(
    repo: InMemoryCrmRepository | None = None,
    sink: RecordingAuditSink | None = None,
    **setting_overrides: Any,
) -> tuple[Settings, AppContext, InMemoryCrmRepository, RecordingAuditSink]:
    """Assemble settings and an application context over in-memory doubles.

    Args:
        repo: Repository double to use; a fresh one when omitted.
        sink: Audit sink double to use; a fresh one when omitted.
        **setting_overrides: Settings fields to override.

    Returns:
        The settings, the context, the repository and the audit sink.
    """
    values: dict[str, Any] = {
        "environment": "test",
        "log_format": "console",
        "enable_write_tools": True,
    }
    values.update(setting_overrides)
    settings = Settings(**values)
    repository = repo if repo is not None else InMemoryCrmRepository()
    audit_sink = sink if sink is not None else RecordingAuditSink()
    context = AppContext(
        settings=settings,
        audit_sink=audit_sink,
        crm=CrmService(repository=repository, audit_sink=audit_sink, settings=settings),
        database_available=True,
    )
    return settings, context, repository, audit_sink


@pytest.fixture
async def crm(request: pytest.FixtureRequest) -> AsyncIterator[CrmHarness]:
    """A server wired to in-memory CRM doubles, driven over a real session.

    Settings overrides are supplied with ``@pytest.mark.settings(...)``, which
    keeps every test's configuration visible at the top of the test rather than
    buried in a builder call.

    Args:
        request: The pytest request, carrying any ``settings`` marker.

    Yields:
        The harness.
    """
    marker = request.node.get_closest_marker("settings")
    overrides: dict[str, Any] = dict(marker.kwargs) if marker is not None else {}
    _, context, repo, sink = _harness_factory(**overrides)
    async with Client(build_server(context=context), raise_exceptions=True) as client:
        yield CrmHarness(client, repo, sink)


# --------------------------------------------------------------------------
# Discoverability
# --------------------------------------------------------------------------


async def test_all_three_crm_tools_are_listed(crm: CrmHarness) -> None:
    names = {tool.name for tool in (await crm.client.list_tools()).tools}
    assert set(CRM_TOOLS) <= names


async def test_write_tools_are_discoverable_even_when_writes_are_disabled() -> None:
    """An agent should be able to see the capability and be told it is off.

    Hiding the tools would make the server look incapable rather than
    deliberately restricted, and would give the model nothing to report.
    """
    _, context, _, _ = _harness_factory(enable_write_tools=False)
    async with Client(build_server(context=context), raise_exceptions=True) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert set(WRITE_TOOLS) <= names


async def test_write_tools_are_annotated_as_non_readonly_non_destructive_idempotent(
    crm: CrmHarness,
) -> None:
    """The annotations are what a host uses to decide whether to prompt a user."""
    tools = {tool.name: tool for tool in (await crm.client.list_tools()).tools}
    for name in WRITE_TOOLS:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is False, name
        assert annotations.destructive_hint is False, name
        assert annotations.idempotent_hint is True, name


async def test_the_read_tool_is_annotated_read_only(crm: CrmHarness) -> None:
    tools = {tool.name: tool for tool in (await crm.client.list_tools()).tools}
    annotations = tools["crm_query"].annotations
    assert annotations is not None
    assert annotations.read_only_hint is True
    assert annotations.open_world_hint is False


async def test_descriptions_tell_a_model_what_changes_and_what_the_outcome_means(
    crm: CrmHarness,
) -> None:
    """A write tool an agent cannot reason about safely is a hazard, not a feature."""
    tools = {tool.name: tool for tool in (await crm.client.list_tools()).tools}
    for name in WRITE_TOOLS:
        description = tools[name].description or ""
        assert len(description) > 400, f"{name} description is too thin to choose on"
        assert "dry_run" in description, f"{name} does not explain the dry-run outcome"
        assert "unchanged" in description, f"{name} does not explain idempotency"
    assert "read" in (tools["crm_query"].description or "").lower()


async def test_schemas_are_derived_from_type_hints_in_both_directions(crm: CrmHarness) -> None:
    tools = {tool.name: tool for tool in (await crm.client.list_tools()).tools}

    query = tools["crm_query"]
    assert "company_domain" in query.input_schema["properties"]
    assert query.output_schema is not None
    # `count` is a computed field: it only appears when the output schema is
    # derived in serialisation mode (D-018).
    assert "count" in query.output_schema["properties"]

    write = tools["sync_to_crm"]
    assert "contact" in write.input_schema["properties"]
    assert write.output_schema is not None
    assert "success" in write.output_schema["properties"]


async def test_the_injected_context_is_never_a_model_parameter(crm: CrmHarness) -> None:
    tools = {tool.name: tool for tool in (await crm.client.list_tools()).tools}
    for name in CRM_TOOLS:
        assert "ctx" not in tools[name].input_schema.get("properties", {})


# --------------------------------------------------------------------------
# crm_query
# --------------------------------------------------------------------------


async def test_a_query_returns_canonical_records_not_database_internals(crm: CrmHarness) -> None:
    """A leaked ORM field would tie every future client to this schema."""
    crm.repo.seed(
        Contact(
            full_name="Priya Raman",
            title="VP Sales",
            company_domain="cloudscale.io",
            country="US",
            source=RecordSource.CRM,
        )
    )

    payload = await crm.call("crm_query", {"company_domain": "cloudscale.io"})

    assert payload["count"] == 1
    record = payload["contacts"][0]
    assert record["full_name"] == "Priya Raman"
    for leaked in ("id", "company_id", "created_at", "updated_at", "provider_contact_id", "_sa"):
        assert leaked not in record, f"{leaked} leaked out of the persistence layer"


async def test_an_empty_result_is_distinguished_from_a_failure(crm: CrmHarness) -> None:
    payload = await crm.call("crm_query", {"company_domain": "nobody.example"})

    assert payload["count"] == 0
    assert payload["contacts"] == []
    assert "not a failure" in payload["message"]


async def test_filters_are_combined_and_reported_back_as_applied(crm: CrmHarness) -> None:
    crm.repo.seed(
        Contact(
            full_name="Priya Raman",
            title="VP Sales",
            company_domain="cloudscale.io",
            country="US",
            source=RecordSource.CRM,
        )
    )
    crm.repo.seed(
        Contact(
            full_name="Tomas Lang",
            title="Engineer",
            company_domain="cloudscale.io",
            country="DE",
            source=RecordSource.CRM,
        )
    )

    payload = await crm.call(
        "crm_query", {"company_domain": "cloudscale.io", "title_contains": "vp"}
    )

    assert payload["count"] == 1
    assert payload["contacts"][0]["full_name"] == "Priya Raman"
    assert payload["filters"]["title_contains"] == "vp"


async def test_the_result_limit_is_bounded_by_the_schema(crm: CrmHarness) -> None:
    """An unbounded query would drag the whole CRM into the model's context."""
    result = await crm.client.call_tool("crm_query", {"limit": 5000})
    assert result.is_error is True


async def test_a_query_never_writes_and_never_audits(crm: CrmHarness) -> None:
    crm.repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))

    await crm.call("crm_query", {})
    await crm.call("crm_query", {"title_contains": "vp"})

    assert crm.repo.write_calls == []
    assert crm.sink.events == []


async def test_the_enrichment_read_tools_still_touch_no_crm_state(crm: CrmHarness) -> None:
    """Phase 3 tools must not have gained a CRM side effect in Phase 4."""
    await crm.client.call_tool("search_company", {"domain_or_name": "cloudscale.io"})
    await crm.client.call_tool(
        "search_contact", {"name": "Elena Rostova", "company": "cloudscale.io"}
    )

    assert crm.repo.write_calls == []
    assert crm.repo.contacts == {}
    assert crm.sink.events == []


# --------------------------------------------------------------------------
# enable_write_tools
# --------------------------------------------------------------------------


@pytest.mark.settings(enable_write_tools=False)
async def test_sync_cannot_mutate_when_writes_are_disabled(crm: CrmHarness) -> None:
    payload = await crm.call("sync_to_crm", CONTACT_ARGS)

    assert payload["outcome"] == "rejected"
    assert payload["success"] is False
    assert crm.repo.contacts == {}
    assert crm.repo.write_calls == []


@pytest.mark.settings(enable_write_tools=False)
async def test_save_to_list_cannot_mutate_when_writes_are_disabled(crm: CrmHarness) -> None:
    contact_id = crm.repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))

    payload = await crm.call("save_to_list", {"contact_id": contact_id, "list_name": "Q4 Pipeline"})

    assert payload["outcome"] == "rejected"
    assert crm.repo.lists == {}
    assert crm.repo.write_calls == []


@pytest.mark.settings(enable_write_tools=False)
async def test_a_rejection_is_audited_through_the_protocol_path(crm: CrmHarness) -> None:
    payload = await crm.call("sync_to_crm", CONTACT_ARGS)

    assert crm.sink.outcomes == ["rejected"]
    event = crm.sink.events[0]
    assert event.tool_name == "sync_to_crm"
    assert event.operation is AuditOperation.UPSERT
    assert event.error_code == "write_rejected"
    assert event.request_id is not None
    assert payload["audit_id"] == event.audit_id


@pytest.mark.settings(enable_write_tools=False)
async def test_a_refusal_is_a_readable_result_not_an_invisible_protocol_error(
    crm: CrmHarness,
) -> None:
    """The model has to see the refusal, or it will keep trying."""
    result = await crm.client.call_tool("sync_to_crm", CONTACT_ARGS)

    assert result.is_error is False
    assert result.structured_content is not None
    assert "enable_write_tools" in result.structured_content["message"]


async def test_enabling_writes_lets_a_valid_sync_through(crm: CrmHarness) -> None:
    payload = await crm.call("sync_to_crm", CONTACT_ARGS)

    assert payload["outcome"] == "created"
    assert payload["success"] is True
    assert len(crm.repo.contacts) == 1


# --------------------------------------------------------------------------
# dry_run_writes
# --------------------------------------------------------------------------


@pytest.mark.settings(dry_run_writes=True)
async def test_a_dry_run_sync_leaves_the_store_untouched(crm: CrmHarness) -> None:
    payload = await crm.call("sync_to_crm", CONTACT_ARGS)

    assert payload["outcome"] == "dry_run"
    assert payload["success"] is False
    assert "NOTHING WAS WRITTEN" in payload["message"]
    assert crm.repo.contacts == {}
    assert crm.repo.write_calls == []


@pytest.mark.settings(dry_run_writes=True)
async def test_a_dry_run_list_add_leaves_membership_untouched(crm: CrmHarness) -> None:
    contact_id = crm.repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))

    payload = await crm.call("save_to_list", {"contact_id": contact_id, "list_name": "Q4 Pipeline"})

    assert payload["outcome"] == "dry_run"
    assert crm.repo.lists == {}
    assert crm.repo.write_calls == []


@pytest.mark.settings(dry_run_writes=True)
async def test_a_dry_run_is_recorded_in_the_audit_trail_as_a_dry_run(crm: CrmHarness) -> None:
    await crm.call("sync_to_crm", CONTACT_ARGS)

    assert crm.sink.outcomes == ["dry_run"]
    assert crm.sink.events[0].dry_run is True


async def test_with_dry_run_off_the_same_call_persists(crm: CrmHarness) -> None:
    payload = await crm.call("sync_to_crm", CONTACT_ARGS)

    assert payload["outcome"] == "created"
    assert crm.repo.write_calls == ["upsert_contact"]


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


async def test_syncing_the_same_contact_twice_creates_then_reports_unchanged(
    crm: CrmHarness,
) -> None:
    first = await crm.call("sync_to_crm", CONTACT_ARGS)
    second = await crm.call("sync_to_crm", CONTACT_ARGS)

    assert first["outcome"] == "created"
    assert second["outcome"] == "unchanged"
    assert second["record_id"] == first["record_id"]
    assert len(crm.repo.contacts) == 1
    assert crm.sink.outcomes == ["created", "unchanged"]


async def test_adding_the_same_contact_to_a_list_twice_never_duplicates(crm: CrmHarness) -> None:
    contact_id = crm.repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))
    args = {"contact_id": contact_id, "list_name": "Q4 Pipeline"}

    first = await crm.call("save_to_list", args)
    second = await crm.call("save_to_list", args)

    assert first["outcome"] == "created"
    assert second["outcome"] == "unchanged"
    assert crm.repo.lists["Q4 Pipeline"] == {contact_id}
    assert crm.sink.outcomes == ["created", "unchanged"]


async def test_a_changed_field_reports_updated_and_names_what_changed(crm: CrmHarness) -> None:
    await crm.call("sync_to_crm", CONTACT_ARGS)

    changed = {"contact": {**CONTACT_ARGS["contact"], "title": "VP Revenue Operations"}}
    payload = await crm.call("sync_to_crm", changed)

    assert payload["outcome"] == "updated"
    assert payload["changed_fields"] == ["title"]


# --------------------------------------------------------------------------
# Conflict policy (D-015) through the write path
# --------------------------------------------------------------------------


async def test_a_synced_title_cannot_overwrite_one_a_human_curated(crm: CrmHarness) -> None:
    """The scenario the policy exists for: stale enrichment versus a verified value."""
    crm.repo.seed(
        Contact(
            full_name="Elena Rostova",
            email="elena@cloudscale.io",
            title="VP Sales",
            source=RecordSource.CRM,
        )
    )

    payload = await crm.call(
        "sync_to_crm",
        {
            "contact": {
                "full_name": "Elena Rostova",
                "email": "elena@cloudscale.io",
                "title": "Sales Director",
            }
        },
    )

    stored = next(iter(crm.repo.contacts.values())).contact
    assert stored.title == "VP Sales"
    assert "title" not in payload["changed_fields"]


async def test_an_omitted_field_never_clears_a_populated_one(crm: CrmHarness) -> None:
    crm.repo.seed(
        Contact(
            full_name="Elena Rostova",
            email="elena@cloudscale.io",
            phone="+1-555-0100",
            title="VP Sales",
            source=RecordSource.CRM,
        )
    )

    await crm.call(
        "sync_to_crm", {"contact": {"full_name": "Elena Rostova", "email": "elena@cloudscale.io"}}
    )

    stored = next(iter(crm.repo.contacts.values())).contact
    assert stored.phone == "+1-555-0100"
    assert stored.title == "VP Sales"


async def test_a_sync_may_fill_a_field_the_crm_left_empty(crm: CrmHarness) -> None:
    """The policy protects curated values; it must not freeze the record entirely."""
    crm.repo.seed(
        Contact(
            full_name="Elena Rostova",
            email="elena@cloudscale.io",
            title="VP Sales",
            source=RecordSource.CRM,
        )
    )

    payload = await crm.call(
        "sync_to_crm",
        {
            "contact": {
                "full_name": "Elena Rostova",
                "email": "elena@cloudscale.io",
                "linkedin_url": "https://linkedin.com/in/elena",
            }
        },
    )

    stored = next(iter(crm.repo.contacts.values())).contact
    assert stored.linkedin_url == "https://linkedin.com/in/elena"
    assert payload["changed_fields"] == ["linkedin_url"]
    assert stored.title == "VP Sales"


# --------------------------------------------------------------------------
# Business failures
# --------------------------------------------------------------------------


async def test_adding_an_unknown_contact_to_a_list_fails_visibly_and_is_audited(
    crm: CrmHarness,
) -> None:
    payload = await crm.call(
        "save_to_list",
        {"contact_id": "3f1a5c9e-0000-4000-8000-000000000000", "list_name": "Q4 Pipeline"},
    )

    assert payload["outcome"] == "failed"
    assert payload["success"] is False
    assert "sync_to_crm" in payload["message"]
    assert crm.repo.lists == {}
    assert crm.sink.outcomes == ["failed"]
    assert crm.sink.events[0].error_code == "not_found"


async def test_a_contact_id_that_is_not_an_identifier_is_refused_before_any_write(
    crm: CrmHarness,
) -> None:
    payload = await crm.call(
        "save_to_list", {"contact_id": "elena@cloudscale.io", "list_name": "Q4 Pipeline"}
    )

    assert payload["outcome"] == "failed"
    assert crm.repo.write_calls == []
    assert crm.sink.events[0].error_code == "validation_error"


@pytest.mark.parametrize("bad_list_name", ["", "  ", "'; DROP TABLE contacts; --", "a" * 101])
async def test_a_list_name_outside_the_allowed_shape_is_refused_by_the_schema(
    crm: CrmHarness, bad_list_name: str
) -> None:
    """Identifiers reaching persistence are constrained at the boundary, not trusted."""
    contact_id = crm.repo.seed(Contact(full_name="Priya Raman", source=RecordSource.CRM))

    result = await crm.client.call_tool(
        "save_to_list", {"contact_id": contact_id, "list_name": bad_list_name}
    )

    assert result.is_error is True
    assert crm.repo.lists == {}


async def test_a_malformed_contact_is_refused_by_the_schema_before_the_service(
    crm: CrmHarness,
) -> None:
    result = await crm.client.call_tool(
        "sync_to_crm", {"contact": {"full_name": "Elena", "email": "not-an-email"}}
    )

    assert result.is_error is True
    assert crm.repo.write_calls == []
    assert crm.sink.events == []


# --------------------------------------------------------------------------
# Server faults
# --------------------------------------------------------------------------


async def test_an_unavailable_database_fails_the_request_rather_than_the_model() -> None:
    """No model retry fixes a dead database, so the host sees it and the model does not."""
    settings = Settings(
        environment="test",
        log_format="console",
        enable_write_tools=True,
        database_url="postgresql+asyncpg://nobody:nobody@127.0.0.1:1/gtm_test",
        db_connect_timeout_seconds=0.25,
    )
    async with Client(build_server(settings), raise_exceptions=True) as client:
        for name, arguments in (
            ("crm_query", {}),
            ("sync_to_crm", CONTACT_ARGS),
            ("save_to_list", {"contact_id": "x" * 36, "list_name": "Q4"}),
        ):
            with pytest.raises(MCPError, match="repository_error"):
                await client.call_tool(name, arguments)


async def test_no_error_message_exposes_sql_a_stack_trace_or_a_credential() -> None:
    """Error text reaches the model and the transcript; internals must not."""
    settings = Settings(
        environment="test",
        log_format="console",
        enable_write_tools=True,
        database_url="postgresql+asyncpg://gtm:supersecret@127.0.0.1:1/gtm_test",
        db_connect_timeout_seconds=0.25,
    )
    async with Client(build_server(settings), raise_exceptions=True) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("crm_query", {})

    text = str(caught.value)
    for forbidden in ("supersecret", "SELECT", "Traceback", "sqlalchemy", "asyncpg"):
        assert forbidden not in text
