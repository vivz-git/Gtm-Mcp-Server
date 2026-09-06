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
│  Service layer         gtm_mcp/services/                           │
│    EnrichmentService   normalise · call once · shape the result    │
│    (planned) merge policy, write guardrails, audit emission        │
├────────────────────────────────────────────────────────────────────┤
│  Ports                 gtm_mcp/ports.py                            │
│    CompanyEnrichmentProvider · ContactEnrichmentProvider           │
│    CrmRepository  ← no delete method exists here                   │
├──────────────────────────┬─────────────────────────────────────────┤
│  Provider adapters       │  CRM adapters                           │
│  gtm_mcp/providers/      │  gtm_mcp/crm/                           │
│  hunter (live HTTP)      │  PostgresCrmRepository, mock seed       │
│  sample (offline)        │                                         │
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
| `gtm_mcp/domain/identifiers.py` | Domain/URL/name normalisation; `CompanyQuery`, `ContactQuery`. |
| `gtm_mcp/domain/enrichment.py` | `CompanyLookup` / `ContactLookup` and `EnrichmentProvenance`. |
| `gtm_mcp/domain/results.py` | `WriteResult` / `WriteOutcome`: the write-honesty contract. |
| `gtm_mcp/domain/writes.py` | `ContactSyncInput`: what an agent may submit, and may not (D-021). |
| `gtm_mcp/ports.py` | Protocols for enrichment providers and the CRM repository. |
| `gtm_mcp/services/enrichment.py` | Enrichment orchestration and single-call cost discipline. |
| `gtm_mcp/services/crm.py` | Bounded CRM reads, and the **single guarded write path** (D-019). |
| `gtm_mcp/providers/http.py` | Shared outbound HTTP: timeout, bounded retries, backoff. |
| `gtm_mcp/providers/hunter.py` | Hunter adapters for both enrichment ports (live). |
| `gtm_mcp/providers/sample.py` | Offline adapters over a committed synthetic dataset (default). |
| `gtm_mcp/providers/factory.py` | Provider selection from configuration. The only vendor switch. |
| `gtm_mcp/audit/` | `AuditEvent`, `LoggingAuditSink`, `InMemoryAuditSink`, `PostgresAuditSink`. |
| `gtm_mcp/db/engine.py` | Async engine, session factory, bounded startup probe. |
| `gtm_mcp/db/models.py` | SQLAlchemy ORM models: `companies`, `contacts`, `lists`, `list_members`, `audit_log`. |
| `gtm_mcp/crm/` | `PostgresCrmRepository` and reproducible mock CRM seed mechanism. |
| `gtm_mcp/server/` | Server construction and lifespan. |
| `gtm_mcp/tools/` | MCP tool definitions, one module per capability group. |

## Read and write boundary

The six tools split into two classes with different rules.

**Read tools** — `search_company`, `search_contact`, `crm_query`

Annotated `read_only_hint=True`, `destructive_hint=False`, `idempotent_hint=True`. They
touch no persistent state. Results are bounded (`ContactFilter.limit` caps at 100) so no
single call can pull the database into a context window.

The two enrichment tools additionally carry `open_world_hint=True`: they reach a third-party
API whose contents this server does not control. They are *not* free to call speculatively,
because each call may spend a provider credit — the tool descriptions say so, and the
`found: false` result explicitly tells the model not to retry the same identifier.

`crm_query` reads this server's own database, so it carries `open_world_hint=False`: its
contents are enumerable and a call costs nothing. It emits **no audit event**, because there
is no mutation to attest to and a read in the write ledger would dilute it.

Enrichment is deliberately not wired to the CRM. `search_company` and `search_contact`
return records and write nothing; persisting one is a separate, guarded write call, so the
human decision point survives.

**Write tools** — `sync_to_crm`, `save_to_list`

Annotated `read_only_hint=False`, `destructive_hint=False`, `idempotent_hint=True`. Both
reach persistence through one function, `CrmService._execute_write` — the only caller of a
`CrmRepository` write method anywhere in the system (D-019):

```
tool: validate input (Pydantic; ContactSyncInput bounds every field)
  → CrmService._execute_write
      1. enable_write_tools      false → REJECTED, audited, nothing attempted
      2. max_write_batch_size    record_count > limit → REJECTED, audited
      3. preconditions           e.g. "contact exists?" → FAILED, audited
      4. dry_run_writes          true → DRY_RUN, audited with dry_run=True, no write call
      5. CrmRepository.<write>   identity (D-014) and merge policy (D-015) live here
      6. AuditEvent              the real outcome, including failure
  → WriteResult  outcome ∈ {created, updated, unchanged, dry_run, rejected, failed}
```

Steps 1-4 are why the tools are thin: a tool that checked a guardrail itself would be one
copy-paste away from a tool that forgot to. `max_write_batch_size` is checked against a
`record_count` even though every current tool passes `1`, so a future batch tool inherits
the bound instead of reinventing it.

### Write safety model

| Control | Default | Effect when it triggers |
| --- | --- | --- |
| `enable_write_tools` | **false** | Outcome `rejected`. No repository method called. Audited with `error_code=write_rejected`. The message tells the agent an operator must enable writes and that retrying is pointless. |
| `dry_run_writes` | false | Outcome `dry_run` after full validation. No repository write method called. Audited with `dry_run=true`. `dry_run` is **not** a success (D-009). |
| `max_write_batch_size` | 1 | Outcome `rejected` above the limit; refused at configuration time if set outside 1-25. |

Non-destructive guarantees, in descending order of how hard they are to undo:

1. `CrmRepository` has no delete method, and `AuditOperation` has no `DELETE` member, so a
   deletion cannot be expressed anywhere above the datastore (D-008).
2. `audit_log` carries `CheckConstraint("operation IN ('upsert', 'list_add')")`, so the
   database itself rejects a destructive audit row (D-016).
3. A field omitted from a sync is left alone; nothing overwrites a populated value with
   `null`, and CRM-curated values beat submitted ones (D-015).
4. An agent cannot claim CRM provenance for its own data: `ContactSyncInput` has no `source`
   field and stamps `ENRICHMENT` (D-021).

### Atomicity, stated honestly

The CRM write and its audit record are **separate transactions** (D-020). The mutation runs
first; the audit event records its real outcome. A failure to audit raises rather than being
swallowed, and says explicitly whether the CRM change was applied. A process crash between
the two leaves an un-audited mutation — a one-statement window this design mitigates
(both writes are idempotent) rather than eliminates. Spanning them would require a
transaction handle in the port, which would couple the audit trail to SQL and break the
next `CrmRepository` implementation.

## The enrichment path

```
search_company / search_contact          tool layer: annotations, error translation
  → EnrichmentService                    normalise, call once, shape the result
    → CompanyEnrichmentProvider          port: canonical types, GTMError, no vendor names
      → HunterCompanyProvider            adapter: vendor params, status codes, payload shapes
        → EnrichmentHttpClient           timeout, bounded retry, backoff, JSON decoding
          → api.hunter.io
```

**Input normalisation** (`domain/identifiers.py`). `https://www.Stripe.com/pricing`,
`STRIPE.COM`, `stripe.com.` and `jane@stripe.com` all reduce to `stripe.com`, so one company
is one query and one CRM join key. Text that does not parse as a domain is kept as a *name*
and never turned into one: appending `.com` to a company name would enrich a different
company and report it confidently.

**Provider capability differences stay in adapters.** Hunter resolves companies by domain
only; the sample adapter can also resolve names. The Hunter adapter refuses a name-only query
with an actionable `ValidationError`. Neither the service nor the tool contains a provider
name or a capability branch.

**Cost discipline** (D-017). One tool call performs at most one provider request. There is no
pre-flight lookup, no second attempt under a different identifier, and no automatic fallback
to a second vendor — paying another vendor to answer the same question is an operator
decision, not a default.

**Retry policy.** Transport failures and 5xx are retried, bounded by
`enrichment_max_retries` (default 2), with deterministic exponential backoff
(`0.5s`, `1.0s`). Nothing else is retried. A 429 is a quota, not a hiccup: retrying it
lengthens the block and can spend a second credit. A 400 or 401 fails identically on the
second attempt, so a retry buys nothing and costs something.

**Error mapping.** Vendor status codes become domain errors in the adapter, and the tool
boundary routes them through the one rule in `errors.to_mcp_exception` (D-007):

| Provider condition | Domain error | Channel | Why |
| --- | --- | --- | --- |
| 404, or a 200 with no record | *not an error* | `found: false` result | A fact about the world the agent should act on |
| 400 invalid parameters | `ValidationError` | `ToolError` | The model can fix its input |
| 401 rejected key | `ConfigurationError` | `MCPError` | An operator problem; showing it to the model invites retries |
| 403 rate limit / 429 quota | `RateLimitError` | `ToolError` | The agent can back off and tell the user |
| 451 legal block | `ProviderError` | `ToolError` | Actionable: stop asking |
| 5xx after retries, timeout, malformed body | `ProviderError` | `ToolError` | The agent should report a failed lookup, not invent data |

Raw HTTP failures and stack traces never reach the model. Provider error text is repeated
back only in bounded form (200 characters), because an unbounded echo both burns context and
lets a vendor payload put prose into a prompt.

**Provenance and honest demo data.** Every enriched record carries an `EnrichmentProvenance`
naming the provider, what it matched on, when it was fetched, and — critically — whether the
data is `live`. The default provider is an offline synthetic dataset, so `live` is `false`,
the result message says the data must not be presented as fact, and `server_info` reports
the configured provider. Selecting a live provider without a credential fails at startup
rather than falling back to sample data under a real provider's name.

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
| One tool call spends at most one credit | Service calls the provider once; no fallback | `test_one_search_makes_exactly_one_provider_call` |
| A rejection is never retried | Retry predicate covers only 5xx and transport errors | `test_no_client_error_is_ever_retried` |
| Retries cannot run away | Bounded attempt budget with recorded backoff | `test_retries_are_bounded_and_the_failure_is_reported_honestly` |
| A credential never reaches a URL or log | Key sent as a header; `REDACTED_KEYS` covers the rest | `test_a_company_request_sends_the_key_as_a_header_and_never_in_the_url` |
| No match is not a failure | `found` computed from the record's presence | `test_an_unknown_company_is_a_successful_call_reporting_no_match` |
| Sample data cannot pass for real data | `provenance.live` plus an explicit result message | `test_results_declare_their_provenance_including_whether_data_is_live` |
| A read tool never writes | Enrichment path has no repository dependency | `test_the_read_tools_perform_no_database_work` |
| Writes are off unless enabled | `enable_write_tools` defaults to false | `test_write_tools_are_disabled_unless_an_operator_opts_in` |
| A refusal blocks and is recorded | Guardrail returns `REJECTED` before any repository call | `test_sync_cannot_mutate_when_writes_are_disabled` |
| A dry run persists nothing | No repository write method is reached | `test_a_dry_run_writes_no_row_and_records_a_dry_run` |
| The batch limit cannot be bypassed | One chokepoint; no other caller of a repository write | `test_the_service_is_the_only_caller_of_a_repository_write_method` |
| Every attempt is auditable | Sink called on reject, dry-run, failure and success | `test_a_rejection_is_audited_through_the_protocol_path` |
| An audit failure is never hidden | Sink errors escalate to `RepositoryError` | `test_an_unauditable_write_is_escalated_rather_than_reported_as_done` |
| Repeating a write changes nothing | Upsert keyed on natural identity; membership unique | `test_syncing_twice_creates_one_row_and_then_reports_unchanged` |
| An agent cannot outrank a human | `ContactSyncInput` has no `source` field | `test_a_submitted_contact_is_always_marked_as_enrichment_provenance` |
| `crm_query` never mutates or audits | Read path calls no write method and no sink | `test_a_query_never_writes_and_never_audits` |
| No delete exists in the source | Screen for destructive SQL and ORM constructs | `test_no_module_contains_a_statement_that_could_remove_data` |
| A tool cannot reach the database | Tool modules import no SQLAlchemy | `test_the_tool_layer_imports_no_database_machinery` |

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

Adding an enrichment vendor: implement `CompanyEnrichmentProvider` (and/or
`ContactEnrichmentProvider`) in `gtm_mcp/providers/`, add the branch to
`providers/factory.py`, extend the `EnrichmentProvider` literal in `settings.py`. No service
change and no tool change — demonstrated by the two adapters already behind each port.

Adding a real CRM: implement `CrmRepository` against its API, select it by configuration.
No tool changes.

Durable audit: implement `AuditSink` over the audit table, swap it in the lifespan. No write
path changes.
