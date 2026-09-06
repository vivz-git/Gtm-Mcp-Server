"""CRM orchestration: bounded reads, and every write through one guarded path.

This service owns the write-control sequence. It exists as a layer rather than
as code inside the tools for one reason that matters more than tidiness: there
is exactly **one** function in this system that can reach
:meth:`~gtm_mcp.ports.CrmRepository.upsert_contact` or
:meth:`~gtm_mcp.ports.CrmRepository.add_contact_to_list`, and that function
checks the guardrails and writes the audit event. A future write tool cannot
bypass ``max_write_batch_size`` by taking a different route, because there is no
other route to take.

The sequence, in order:

1. **Guardrails.** ``enable_write_tools`` first, then the record count against
   ``max_write_batch_size``. A refusal is ``REJECTED``: audited, explained, and
   nothing attempted.
2. **Preconditions.** Business rules that need a read — does this contact exist?
   Runs *before* the dry-run branch, so a dry run validates for real rather than
   rubber-stamping a request that would have failed.
3. **Dry run.** If ``dry_run_writes`` is set, audit the attempt with
   ``dry_run=True`` and return ``DRY_RUN``. No repository write method is called.
4. **Persist.** Delegate to the repository, which owns identity (D-014) and the
   merge policy (D-015).
5. **Audit.** Record the real outcome, including a failure.

Ordering of the mutation and its audit record is discussed in D-019: they are
separate transactions, the mutation comes first, and a failure to audit is
escalated rather than swallowed.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from gtm_mcp.audit.events import AuditEvent, AuditOperation
from gtm_mcp.audit.sinks import AuditSink
from gtm_mcp.domain.models import ContactFilter, ContactQueryResult
from gtm_mcp.domain.results import SUCCESSFUL_OUTCOMES, WriteOutcome, WriteResult
from gtm_mcp.domain.writes import ContactSyncInput
from gtm_mcp.errors import GTMError, NotFoundError, RepositoryError, ValidationError
from gtm_mcp.logging_setup import get_logger
from gtm_mcp.ports import CrmRepository
from gtm_mcp.settings import Settings

_log = get_logger(__name__)

#: Entity type recorded on audit events for contact-scoped operations.
_CONTACT = "contact"


def _is_uuid(value: str) -> bool:
    """Report whether a string is a well-formed UUID.

    Args:
        value: The candidate identifier.

    Returns:
        ``True`` if the value parses as a UUID.
    """
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True


class CrmService:
    """Reads and guarded writes against the CRM, behind the repository port."""

    def __init__(
        self,
        *,
        repository: CrmRepository,
        audit_sink: AuditSink,
        settings: Settings,
    ) -> None:
        """Initialise the service.

        Args:
            repository: The CRM persistence boundary.
            audit_sink: Destination for the audit event every write produces.
            settings: Configuration carrying the write guardrails.
        """
        self._repository = repository
        self._audit = audit_sink
        self._settings = settings

    # -- Reads --------------------------------------------------------------

    async def query_contacts(self, criteria: ContactFilter) -> ContactQueryResult:
        """Return CRM contacts matching bounded filter criteria.

        Strictly read-only: no repository write method is reachable from here,
        and no audit event is emitted, because there is no mutation to attest to.

        Args:
            criteria: Validated filters, including a bounded limit.

        Returns:
            The matching contacts wrapped in a result envelope that distinguishes
            an empty match from a failure.

        Raises:
            RepositoryError: The datastore was unreachable or failed.
        """
        contacts = await self._repository.query_contacts(criteria)
        _log.info("crm_query", matched=len(contacts), limit=criteria.limit)
        return ContactQueryResult(
            filters=criteria,
            contacts=tuple(contacts),
            message=self._describe_query(criteria, matched=len(contacts)),
        )

    @staticmethod
    def _describe_query(criteria: ContactFilter, *, matched: int) -> str:
        """Compose a model-readable summary of what a query matched.

        Args:
            criteria: The filters applied.
            matched: How many contacts were returned.

        Returns:
            A summary that tells an empty result apart from a failure.
        """
        applied = [
            f"{name}={value!r}"
            for name, value in (
                ("company_domain", criteria.company_domain),
                ("title_contains", criteria.title_contains),
                ("country", criteria.country),
                ("list_name", criteria.list_name),
            )
            if value is not None
        ]
        described = ", ".join(applied) if applied else "no filters (whole CRM, newest first)"
        if matched == 0:
            return (
                f"No CRM contact matches {described}. This is a successful query with an "
                f"empty result, not a failure: the CRM holds no such record. Relax a filter "
                f"or use search_contact to look the person up externally."
            )
        truncated = (
            " More may exist beyond the limit; narrow the filters to see them."
            if matched >= criteria.limit
            else ""
        )
        return f"Matched {matched} CRM contact(s) for {described}.{truncated} Nothing was modified."

    # -- Writes -------------------------------------------------------------

    async def sync_contact(self, submission: ContactSyncInput, *, request_id: str) -> WriteResult:
        """Upsert one contact into the CRM through the full guardrail sequence.

        Identity and merge behaviour belong to the repository (D-014, D-015);
        this method contributes the guardrails and the audit record.

        Args:
            submission: The contact as the agent supplied it.
            request_id: MCP request identifier, recorded on the audit event.

        Returns:
            The outcome of the write, never a bare success claim.

        Raises:
            RepositoryError: The datastore or the audit sink failed.
        """
        contact = submission.to_contact()

        return await self._execute_write(
            tool_name="sync_to_crm",
            operation=AuditOperation.UPSERT,
            target_type=_CONTACT,
            target_id=None,
            record_count=1,
            details={
                "identified_by": "email" if contact.email else "none",
                "company_domain": contact.company_domain or "",
            },
            dry_run_summary=(
                f"Validated an upsert of contact '{contact.full_name}'; it would have been "
                f"created or updated in the CRM."
            ),
            perform=lambda: self._repository.upsert_contact(contact),
            request_id=request_id,
        )

    async def save_contact_to_list(
        self, contact_id: str, list_name: str, *, request_id: str
    ) -> WriteResult:
        """Add an existing CRM contact to a named GTM list.

        Additive only. The membership is idempotent, and no operation exists to
        take a contact back out (D-008).

        Args:
            contact_id: Identifier of a contact that already exists in the CRM.
            list_name: Name of the list; created on first use.
            request_id: MCP request identifier, recorded on the audit event.

        Returns:
            The outcome of the write.

        Raises:
            RepositoryError: The datastore or the audit sink failed.
        """
        return await self._execute_write(
            tool_name="save_to_list",
            operation=AuditOperation.LIST_ADD,
            target_type=_CONTACT,
            target_id=contact_id,
            record_count=1,
            details={"list_name": list_name},
            dry_run_summary=(
                f"Validated adding contact '{contact_id}' to list '{list_name}'; the "
                f"membership would have been created."
            ),
            precondition=lambda: self._require_existing_contact(contact_id),
            perform=lambda: self._repository.add_contact_to_list(contact_id, list_name),
            request_id=request_id,
        )

    async def _require_existing_contact(self, contact_id: str) -> None:
        """Fail unless the identifier names a contact already in the CRM.

        Args:
            contact_id: The identifier to check.

        Raises:
            ValidationError: The identifier is not a well-formed CRM identifier.
            NotFoundError: No contact carries that identifier.
        """
        if not _is_uuid(contact_id):
            raise ValidationError(
                f"'{contact_id}' is not a CRM contact identifier. Identifiers are UUIDs as "
                f"returned by crm_query or by a successful sync_to_crm; do not construct one "
                f"from a name or an email address.",
            )
        if await self._repository.get_contact(contact_id) is None:
            raise NotFoundError(
                f"No CRM contact has identifier '{contact_id}'. Nothing was added to any "
                f"list. Find the contact with crm_query, or create it with sync_to_crm first, "
                f"then retry with the identifier that call returned.",
            )

    # -- The single guarded write path --------------------------------------

    async def _execute_write(
        self,
        *,
        tool_name: str,
        operation: AuditOperation,
        target_type: str,
        target_id: str | None,
        record_count: int,
        details: dict[str, str],
        dry_run_summary: str,
        perform: Callable[[], Awaitable[WriteResult]],
        precondition: Callable[[], Awaitable[None]] | None = None,
        request_id: str | None = None,
    ) -> WriteResult:
        """Run one mutation through guardrails, dry-run handling and the audit trail.

        Every write in this system goes through here. Adding a write method that
        does not call this is the one change reviewers of this file should refuse.

        Args:
            tool_name: The MCP tool initiating the write, for the audit record.
            operation: The kind of mutation attempted.
            target_type: Entity type being touched.
            target_id: Identifier of the target, when known before the write.
            record_count: How many records this call would mutate, checked
                against ``max_write_batch_size``.
            details: Small, non-sensitive context for the audit record.
            dry_run_summary: What would have happened, for the dry-run message.
            perform: Performs the mutation via the repository port.
            precondition: Optional business-rule check run before persistence.
            request_id: MCP request identifier, recorded on the audit event.

        Returns:
            The outcome of the attempt, carrying the audit record's identifier.

        Raises:
            RepositoryError: The datastore failed, or the attempt could not be
                audited. Both are server faults an agent cannot correct.
        """
        rejection = self._guardrail_rejection(record_count)
        if rejection is not None:
            return await self._finish(
                WriteResult.rejected(rejection),
                tool_name=tool_name,
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                details=details,
                request_id=request_id,
                error_code="write_rejected",
            )

        if precondition is not None:
            try:
                await precondition()
            except GTMError as exc:
                return await self._finish(
                    WriteResult.failed(exc.message, record_id=target_id),
                    tool_name=tool_name,
                    operation=operation,
                    target_type=target_type,
                    target_id=target_id,
                    details=details,
                    request_id=request_id,
                    error_code=exc.code,
                )

        if self._settings.dry_run_writes:
            return await self._finish(
                WriteResult.dry_run_simulated(dry_run_summary, record_id=target_id),
                tool_name=tool_name,
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                details=details,
                request_id=request_id,
                dry_run=True,
            )

        try:
            result = await perform()
        except RepositoryError:
            # The datastore itself is broken. Audit the attempt, then let the
            # error reach the host: no retry by the model can fix it.
            await self._record(
                AuditEvent(
                    tool_name=tool_name,
                    operation=operation,
                    outcome=WriteOutcome.FAILED,
                    target_type=target_type,
                    target_id=target_id,
                    request_id=request_id,
                    error_code="repository_error",
                    details=details,
                )
            )
            raise
        except GTMError as exc:
            return await self._finish(
                WriteResult.failed(exc.message, record_id=target_id),
                tool_name=tool_name,
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                details=details,
                request_id=request_id,
                error_code=exc.code,
            )

        return await self._finish(
            result,
            tool_name=tool_name,
            operation=operation,
            target_type=target_type,
            target_id=result.record_id or target_id,
            details=details,
            request_id=request_id,
            error_code=None if result.outcome in SUCCESSFUL_OUTCOMES else "write_failed",
        )

    def _guardrail_rejection(self, record_count: int) -> str | None:
        """Return why configuration refuses this write, or ``None`` to proceed.

        Args:
            record_count: How many records the call would mutate.

        Returns:
            A message written for the calling agent, or ``None`` if allowed.
        """
        if not self._settings.enable_write_tools:
            return (
                "Write tools are disabled on this server (enable_write_tools is false), so "
                "NOTHING WAS WRITTEN. This is a server configuration choice, not a problem "
                "with your request: retrying will fail identically. Tell the user that CRM "
                "writes are turned off and that an operator must enable them. Read-only "
                "tools such as crm_query and search_contact still work."
            )
        limit = self._settings.max_write_batch_size
        if record_count < 1:
            return (
                "The request contained no records to write, so nothing was written. Supply "
                "exactly one record."
            )
        if record_count > limit:
            return (
                f"This call would modify {record_count} records, above this server's limit "
                f"of {limit} per call (max_write_batch_size), so NOTHING WAS WRITTEN. Split "
                f"the work into calls of at most {limit} record(s)."
            )
        return None

    async def _finish(
        self,
        result: WriteResult,
        *,
        tool_name: str,
        operation: AuditOperation,
        target_type: str,
        target_id: str | None,
        details: dict[str, str],
        request_id: str | None,
        error_code: str | None = None,
        dry_run: bool = False,
    ) -> WriteResult:
        """Audit an attempt and return the result stamped with the audit id.

        Args:
            result: The outcome to report.
            tool_name: The MCP tool initiating the write.
            operation: The kind of mutation attempted.
            target_type: Entity type being touched.
            target_id: Identifier of the target, when known.
            details: Small, non-sensitive context for the audit record.
            request_id: MCP request identifier.
            error_code: Stable code when the attempt was rejected or failed.
            dry_run: Whether persistence was intentionally skipped.

        Returns:
            The result carrying ``audit_id``.

        Raises:
            RepositoryError: The audit sink failed.
        """
        event = AuditEvent(
            tool_name=tool_name,
            operation=operation,
            outcome=result.outcome,
            target_type=target_type,
            target_id=target_id,
            changed_fields=result.changed_fields,
            request_id=request_id,
            error_code=error_code,
            dry_run=dry_run,
            details=details,
        )
        await self._record(event)
        return result.with_audit_id(event.audit_id)

    async def _record(self, event: AuditEvent) -> None:
        """Write one audit event, escalating any failure.

        An unauditable server is a broken server, so this never swallows. When
        the mutation already succeeded the raised error says so explicitly, so
        nobody reading it concludes the CRM was left untouched (D-019).

        Args:
            event: The event to record.

        Raises:
            RepositoryError: The sink failed to record the event.
        """
        try:
            await self._audit.record(event)
        except Exception as exc:
            mutated = event.outcome in SUCCESSFUL_OUTCOMES
            _log.error(
                "audit_write_failed",
                tool_name=event.tool_name,
                outcome=event.outcome.value,
                crm_state_changed=mutated,
                error=str(exc),
            )
            state = (
                "The CRM change WAS applied but could not be audited"
                if mutated
                else "No CRM change was applied"
            )
            raise RepositoryError(
                f"Failed to record the audit event for {event.tool_name}. {state}. "
                f"The server refuses to report an unauditable write as complete."
            ) from exc
