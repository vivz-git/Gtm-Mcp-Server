# Architecture Decision Record

Numbered, dated decisions with the reasoning that produced them. New decisions are
appended; superseded ones are marked, never deleted, so the reasoning trail survives.

Status values: **Accepted**, **Deferred**, **Superseded**.

---

## D-001 — Python 3.13, managed by uv

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** The MCP SDK requires Python >= 3.10. The development machine has 3.10.11 and
3.14.2 installed; uv can supply any version independently of both.

**Decision.** Target Python 3.13 (pinned in `.python-version`), manage the environment and
lockfile with `uv`.

**Why.** 3.13 has settled binary-wheel support across the whole stack this project needs —
asyncpg, SQLAlchemy, pydantic-core. 3.14 is current, but wheel availability for
C-extension database drivers still lags on Windows, which is the primary development
platform here. uv gives a committed lockfile and reproducible installs without a separate
pyenv/poetry toolchain.

**Consequences.** Contributors need uv. CI installs the same locked versions.

---

## D-002 — Official MCP Python SDK, v2 line, pinned below v3

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** `mcp` is at **2.1.1**. The v1 to v2 transition was a hard break: `FastMCP` was
renamed `MCPServer` and moved from `mcp.server.fastmcp` to `mcp.server.mcpserver`, protocol
types moved to a standalone `mcp-types` package, protocol fields moved from camelCase to
snake_case (`inputSchema` to `input_schema`, `isError` to `is_error`), `McpError` became
`MCPError`, and `httpx` was replaced by `httpx2`. Most MCP material published before
mid-2026 describes the v1 API and does not run on v2.

**Decision.** Build on the official SDK v2 (`mcp[cli]>=2.1.1,<3`). Verify API shape against
the installed package and the official docs, never against tutorials.

**Why.** The official SDK tracks the specification directly. FastMCP 4.x is a capable
third-party alternative but adds a dependency whose release cadence we do not control, for
capability this project does not need. The `<3` ceiling is deliberate: given how much v2
broke, a future v3 must be an explicit, reviewed migration rather than something a
dependency resolver does silently.

**Consequences.** `CLAUDE.md` requires re-verifying SDK APIs before use.

---

## D-003 — Modular monolith with enforced layer boundaries

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Five tools spanning enrichment, CRM persistence and audit.

**Decision.** One deployable process, internally split into tool → service → port → adapter
layers, with dependencies pointing inward only.

**Why.** The tools share a database, an audit trail and a configuration surface, and are
always deployed together. Splitting them into services would add network failure modes and
deployment complexity to buy independent scaling that nothing here needs. The boundaries
that actually matter — swapping an enrichment vendor, swapping the CRM backend — are
satisfied by interfaces, not by process separation.

**Consequences.** Boundaries are a convention that tests and review must uphold, since the
runtime does not enforce them.

---

## D-004 — stdio as the default transport, streamable-http as the option

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Default to stdio; support `streamable-http` via configuration. Do not
implement or document the SSE transport.

**Why.** Local MCP clients (Claude Desktop, Claude Code) spawn servers over stdio, which is
the primary demonstration path. Streamable HTTP is the current transport for remote
servers. SSE is legacy — the SDK still speaks it for older clients, but a new server has no
reason to adopt it.

**Consequences.** Nothing may write to stdout. Enforced by `configure_logging` and by
`tests/unit/test_logging_setup.py`.

---

## D-005 — `AppContext` lives in `gtm_mcp/context.py`, not in the server package

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Found while implementing, not by reading docs: the v2 SDK resolves handler type
annotations **at runtime** in order to derive schemas. A tool signature of
`ctx: Context[AppContext]` therefore fails with `InvalidSignature: Unable to evaluate type
annotations` if `AppContext` is imported only under `TYPE_CHECKING`. Importing it at runtime
from `gtm_mcp.server.lifespan` closed an import cycle:
`server -> app -> tools -> diagnostics -> server`.

**Decision.** `AppContext` lives in its own leaf module that imports nothing from
`gtm_mcp.server`.

**Why.** Breaks the cycle where it originates, rather than papering over it with deferred
imports inside functions.

**Consequences.** Any type named in a handler signature must be importable at runtime.
Recorded in `CLAUDE.md`.

---

## D-006 — Schemas derived from types; no hand-written JSON Schema

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Input and output schemas come from type hints and Pydantic models.
`Annotated[T, Field(description=...)]` supplies per-parameter documentation.

**Why.** The SDK derives both directions from annotations and validates returns against the
derived output schema. A hand-written schema is a second source of truth that drifts from
the implementation silently — and the drift stays invisible until a model makes a malformed
call.

**Consequences.** Return types must be expressible as a schema. Verified by
`test_schemas_are_derived_from_type_hints`.

---

## D-007 — Two error channels, with an explicit routing rule

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** The SDK offers `ToolError` (returns `is_error=True` with the message in the
tool result, which the **model reads**) and `MCPError` (fails the JSON-RPC request; the
model sees nothing).

**Decision.** Route on one question: *could a better model choice have avoided this?*
Yes → `ToolError`. No → `MCPError`. The rule is implemented once, in
`gtm_mcp.errors.to_mcp_exception`.

**Why.** "Contact not found" is actionable — the agent can retry with a different
identifier. "Database unreachable" is not; showing it to the model invites pointless retries
against a dead dependency.

**Consequences.** Domain code raises `GTMError` subclasses and stays free of protocol
concerns. Tools translate at the boundary. Covered by `tests/unit/test_errors.py`.

---

## D-008 — No delete capability at any layer

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** The `CrmRepository` port exposes no delete method, and `AuditOperation` has no
`DELETE` member. Both writes are upserts.

**Why.** An agent acting on ambiguous instructions is most dangerous when it can destroy
data. Omitting the capability from the interface means no tool can reach for it — a stronger
guarantee than a policy in a prompt or a claim in a README, neither of which is enforced by
anything.

**Consequences.** Removing a record from a list becomes a future additive operation (a
membership status change), not a delete. Covered by
`test_delete_is_not_an_expressible_operation` and
`test_no_tool_name_implies_a_destructive_operation`.

---

## D-009 — `WriteResult.success` is computed, not assignable

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** `success` is a `@computed_field` derived from `outcome`; the model is frozen
and forbids extra fields. `DRY_RUN` is not a success.

**Why.** The requirement "never claim success when the operation failed" becomes enforceable
structurally instead of by discipline. There is no field a call site could set to `True`
after a failure, so the failure mode cannot be reintroduced by a careless edit later.

**Consequences.** Covered by `tests/unit/test_results.py`.

---

## D-010 — Seeded PostgreSQL mock CRM behind a repository port

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** The reference CRM is a seeded PostgreSQL schema reached through
`CrmRepository`. A real CRM (HubSpot, Salesforce) becomes an additional adapter.

**Why.** A demonstration must be reproducible by anyone who clones the repository, which
rules out depending on a paid CRM tenant. PostgreSQL rather than SQLite because the
persistence layer should be exercised against the engine a real deployment would use —
dialect, transactional semantics and JSON support included.

**Consequences.** Integration tests need Docker; they skip cleanly without it.

---

## D-011 — Audit sink behind a Protocol; log sink now, database sink next

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Write paths depend on the `AuditSink` protocol. `LoggingAuditSink` is the
initial implementation; a Postgres-backed sink lands with the CRM phase.

**Why.** Lets the write path be built and tested against a real audit contract before the
audit table exists, with no change to calling code when it does.

**Consequences.** The current sink is *not* sufficient on its own for a compliance story.
That is tracked as a known limitation in `PROJECT_STATUS.md` rather than glossed over in the
README.

---

## D-012 — Reuse `httpx2` rather than adding `httpx`

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Outbound enrichment calls will use `httpx2`, which SDK v2 already depends on.

**Why.** v2 replaced `httpx`/`httpx-sse` with `httpx2`. Adding `httpx` back would ship two
HTTP stacks with separate connection pools, timeout defaults and CVE surfaces for no
benefit; `httpx2` is API-compatible.

---

## D-013 — Enrichment provider selection deferred

**Date:** 2026-09-06 · **Status:** Deferred

**Decision.** No enrichment vendor is chosen yet. `CompanyEnrichmentProvider` and
`ContactEnrichmentProvider` define the boundary; selection happens after a survey of current
pricing, free-tier limits, rate limits, licensing terms and data coverage.

**Why.** Provider choice is the decision most often made badly by copying a tutorial. The
canonical `Company` and `Contact` models cover fields that every serious B2B provider
returns, so the choice can be made late without reshaping the domain.

**Consequences.** The server starts and serves read-only tools with no third-party
credential configured.

---

## D-014 — Contact identity strategy: email primary, provider key secondary, no name-company coalescing

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Upsert idempotency requires identifying whether an incoming contact matches an
existing CRM record. Earlier notes contemplated using name + company as a fallback natural key.

**Decision.**
1. Normalized, lowercased `email` is the primary and strongest natural identity when available.
2. `(provider_name, provider_contact_id)` serves as secondary natural identity when email is
   unavailable (e.g. enrichment vendor leads).
3. If neither exists, the record must not be falsely coalesced based on `name + company`.
   Instead, it is inserted as a distinct record.

**Why.** Common names (e.g., "Alex Chen" or "Sarah Smith") are frequent within large enterprise
accounts (e.g., Google, Amazon, Microsoft). Coalescing on name and company creates severe data
corruption by merging distinct individuals into one record. Enforcing partial unique indexes
in PostgreSQL (`WHERE email IS NOT NULL` and `WHERE provider_name IS NOT NULL AND provider_contact_id IS NOT NULL`)
guarantees natural identity uniqueness while allowing multiple email-less contacts to exist safely.

**Consequences.** Implemented in `PostgresCrmRepository.upsert_contact` and covered by
`test_contact_identity_rule_does_not_coalesce_without_email`.

---

## D-015 — CRM-vs-enrichment conflict policy: CRM is authoritative

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** When external enrichment runs against an existing CRM record, fields may conflict
(e.g., a changed job title or phone number).

**Decision.**
1. CRM-curated data (`RecordSource.CRM`) is authoritative over external enrichment data
   (`RecordSource.ENRICHMENT`). Enrichment may populate empty (`None`/`NULL`) fields or update
   enrichment-owned fields, but must never silently overwrite curated CRM fields.
2. Incoming writes must never overwrite a populated field with `None`/`NULL`.

**Why.** Human sales reps and RevOps operators routinely verify prospect details directly. External
enrichment APIs often lag by months or return generalized scraper titles. Letting an automated
enrichment sync overwrite human-curated data silently destroys business value.

**Consequences.** Implemented in `PostgresCrmRepository.upsert_contact` and covered by
`test_conflict_policy_crm_is_authoritative_over_enrichment` and
`test_never_overwrite_populated_field_with_none`.

---

## D-016 — Database constraints and Alembic migration management

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** The mock CRM runs on PostgreSQL. The write safety invariants established in Phase 1
(no delete, idempotent list membership, audit trail integrity) need durability guarantees.

**Decision.**
1. Manage all schema changes through Alembic async migrations in `alembic/versions`.
2. Enforce list membership uniqueness via `UniqueConstraint("list_id", "contact_id")`.
3. Enforce the non-destructive write invariant at the database engine level via
   `CheckConstraint("operation IN ('upsert', 'list_add')", name="ck_audit_log_no_delete")` on `audit_log`.

**Why.** Code review and application-level checks are necessary but insufficient for data
integrity. Structural constraints enforced by PostgreSQL guarantee that accidental manual queries
or future code bugs cannot delete records or duplicate list memberships.

**Consequences.** Covered by `tests/integration/test_schema_and_migrations.py`.

