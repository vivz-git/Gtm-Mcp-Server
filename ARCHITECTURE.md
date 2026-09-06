# Architecture

How the GTM MCP server is structured, and which properties the structure is there to
guarantee. Decisions and their reasoning live in [DECISIONS.md](DECISIONS.md); current
build state lives in [PROJECT_STATUS.md](PROJECT_STATUS.md).

## Shape

A modular monolith: one process, layered internally, dependencies pointing inward only
(D-003).

```
        MCP client (Claude Desktop / Claude Code / Inspector)
                          │  JSON-RPC over stdio or streamable-http
┌─────────────────────────▼──────────────────────────────────────────┐
│  Server layer          gtm_mcp/server/                             │
│    build_server()      construction, instructions, registration    │
│    make_lifespan()     engine, session factory, audit sink         │
├────────────────────────────────────────────────────────────────────┤
│  Tool layer            gtm_mcp/tools/                              │
│    schemas from type hints · annotations · error translation       │
│    NO business logic                                               │
├────────────────────────────────────────────────────────────────────┤
│  Service layer         gtm_mcp/services/          (next phase)     │
│    orchestration, merge policy, write guardrails, audit emission   │
├────────────────────────────────────────────────────────────────────┤
│  Ports                 gtm_mcp/ports.py                            │
│    CompanyEnrichmentProvider · ContactEnrichmentProvider           │
│    CrmRepository  ← no delete method exists here                   │
├──────────────────────────┬─────────────────────────────────────────┤
│  Provider adapters       │  CRM adapters                           │
│  gtm_mcp/providers/      │  gtm_mcp/crm/                           │
│  external HTTP APIs      │  PostgresCrmRepository, mock seed       │
└──────────────────────────┴─────────────────────────────────────────┘

Cross-cutting: settings.py · errors.py · logging_setup.py · audit/ · context.py
```

Only the tool layer imports from `mcp`. Services and below raise `GTMError` subclasses and
know nothing about JSON-RPC, which is what makes them testable without a protocol session.

## Module map

| Path | Responsibility |
| --- | --- |
| `gtm_mcp/settings.py` | All configuration. The only reader of the environment. |
| `gtm_mcp/errors.py` | Error taxonomy and the single `ToolError` / `MCPError` routing rule. |
| `gtm_mcp/logging_setup.py` | structlog to **stderr**, with central redaction of sensitive keys. |
| `gtm_mcp/context.py` | `AppContext`: state shared by every tool call. Leaf module (D-005). |
| `gtm_mcp/domain/models.py` | Canonical `Company`, `Contact`, `ContactFilter`. Vendor-neutral. |
| `gtm_mcp/domain/results.py` | `WriteResult` / `WriteOutcome`: the write-honesty contract. |
| `gtm_mcp/ports.py` | Protocols for enrichment providers and the CRM repository. |
| `gtm_mcp/audit/` | `AuditEvent`, `LoggingAuditSink`, `InMemoryAuditSink`, `PostgresAuditSink`. |
| `gtm_mcp/db/engine.py` | Async engine, session factory, bounded startup probe. |
| `gtm_mcp/db/models.py` | SQLAlchemy ORM models: `companies`, `contacts`, `lists`, `list_members`, `audit_log`. |
| `gtm_mcp/crm/` | `PostgresCrmRepository` and reproducible mock CRM seed mechanism. |
| `gtm_mcp/server/` | Server construction and lifespan. |
| `gtm_mcp/tools/` | MCP tool definitions, one module per capability group. |

## Read and write boundary

The five planned tools split into two classes with different rules.

**Read tools** — `search_company`, `search_contact`, `crm_query`

Annotated `read_only_hint=True`, `destructive_hint=False`, `idempotent_hint=True`. They
touch no persistent state, so an agent may call them speculatively while exploring. Results
are bounded (`ContactFilter.limit` caps at 100) so no single call can pull the database into
a context window.

**Write tools** — `save_to_list`, `sync_to_crm`

Annotated `read_only_hint=False`, `destructive_hint=False`, `idempotent_hint=True`. Every
one of them passes through the same sequence:

```
validate input (Pydantic)
  → check guardrails      enable_write_tools · dry_run_writes · max_write_batch_size
  → upsert via CrmRepository
  → write AuditEvent      including on rejection and on failure
  → return WriteResult    outcome ∈ {created, updated, unchanged, dry_run, rejected, failed}
```

## What the architecture guarantees, and how

These are enforced by code and covered by tests, not asserted in prose.

| Requirement | Mechanism | Test |
| --- | --- | --- |
| No delete | No method on `CrmRepository`; no `DELETE` in `AuditOperation` | `test_delete_is_not_an_expressible_operation` |
| No destructive tool sneaks in later | Tool-name screen over the live `tools/list` | `test_no_tool_name_implies_a_destructive_operation` |
| Never report false success | `WriteResult.success` is computed from `outcome`, model frozen | `tests/unit/test_results.py` |
| A dry run is not "done" | `DRY_RUN` excluded from `SUCCESSFUL_OUTCOMES` | `test_dry_run_is_not_success` |
| Failures are explicit | Two error channels, one routing rule | `tests/unit/test_errors.py` |
| Every write is audited | Sink called on reject and fail too, not only success | `test_failed_and_rejected_writes_are_still_audited` |
| Guardrails cannot drift at runtime | `Settings` is frozen and forbids unknown keys | `tests/unit/test_settings.py` |
| stdio protocol stream stays clean | All logging to stderr, configured in the lifespan | `test_logs_never_reach_stdout` |
| Personal data stays out of logs | Central redaction processor | `test_sensitive_fields_are_redacted` |
| Startup cannot hang on a bad DSN | Bounded probe (`anyio.fail_after` + driver timeout) | `test_probe_is_bounded_by_its_timeout` |
| No delete at database level | Check constraint `ck_audit_log_no_delete` on `audit_log` | `test_audit_log_check_constraint_rejects_delete_operation` |
| No duplicate list members | Unique constraint `uq_list_members_list_contact` | `test_list_members_enforces_unique_contact_per_list` |
| Zero tool changes for mock CRM | Tool layer untouched; boundary maintained | Contract tests + git status |

## Idempotency and Natural Identity

Both CRM writes are upserts keyed by natural identifiers:
* **Companies**: Keyed by `Company.domain` (normalized, lowercased web domain).
* **Contacts**: Keyed by natural identity (DECISIONS.md D-014):
  1. Normalized email is the primary natural identity when present.
  2. External provider identity (`provider_name`, `provider_contact_id`) provides identity when email is unavailable.
  3. If neither exists, the system **does not falsely claim idempotent identity** (no coalescing on `name + company`).
* **List memberships**: Keyed by `(list_id, contact_id)` with a database unique constraint.

Applying the same input twice leaves the record in the same state and reports `unchanged` the second time. This matters because agents retry: a network hiccup on the response path must not create a duplicate.

## Merge and Conflict Policy

Adopted principle (DECISIONS.md D-015):
> **CRM-curated values are authoritative over enrichment values.**

1. Enrichment may populate missing values (`None` / `NULL`) or update explicitly enrichment-owned fields, but must not silently overwrite manually curated CRM data.
2. An incoming write never overwrites a populated CRM field with `null`.
3. Provenance travels with the data (`RecordSource`) so the merge policy reasons about origin rather than guessing freshness.

## Designing tools for a model, not a developer

The consumer of these tool definitions is a language model choosing among them.

- **Descriptions** state when to call the tool, when *not* to, what it costs, and what it
  changes. `server_info` shows the intended shape.
- **Names** use the GTM vocabulary an agent will have seen in the user's request
  (`search_company`, not `get_org_data`).
- **Parameters** carry `Field(description=...)` and real constraints, so bad calls fail at
  validation with a message the model can act on.
- **Errors** distinguish "you can fix this" from "the server is broken" (D-007).
- **Annotations** let the host decide when to prompt the user for confirmation.
- **Server instructions** tell the agent how to read a `WriteResult`, including that
  `dry_run` means nothing was persisted.

## Extension points

Adding an enrichment vendor: implement `CompanyEnrichmentProvider`, register it in the
lifespan. No tool changes.

Adding a real CRM: implement `CrmRepository` against its API, select it by configuration.
No tool changes.

Durable audit: implement `AuditSink` over the audit table, swap it in the lifespan. No write
path changes.
