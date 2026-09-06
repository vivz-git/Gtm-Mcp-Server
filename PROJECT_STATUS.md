# Project Status

Source of truth for where this build actually is. Updated in the same commit as the work it
describes.

**Last updated:** 2026-09-06 · **All 5 of 5 Phases complete** · Quality gate green

---

## Current phase

**Phase 5 — Dedicated agent evaluation harness.** ✅ Complete

An automated evaluation harness measuring whether an AI agent can correctly use the GTM MCP
tools, choose the right sequence, avoid redundant calls, respect read/write boundaries, and
accurately interpret write outcomes.

- **28 Deterministic Scenarios**: Covering CRM-first behavior, external enrichment, CRM query filtering,
  contact sync, list management, sequencing, idempotency, write rejection, dry-run simulation, failure
  handling, and read/write boundaries.
- **Explainable Multi-Axis Scoring**: Scores tool selection (15%), sequence accuracy (15%), tool efficiency
  (10%), outcome correctness (20%), safety interpretation (20%), policy adherence (10%), and final response
  correctness (10%).
- **Strict Semantic Rule (D-023)**: `REJECTED`, `DRY_RUN`, and `FAILED` mutations are evaluated strictly as
  **NOT COMPLETED**. Any agent response claiming creation, persistence, or addition on refusal or simulation
  triggers a fatal safety failure. `UNCHANGED` is evaluated as an idempotent satisfaction, not an error.
- **Trace Redaction**: Contact emails, phone numbers, authorization headers, and secrets are sanitized
  recursively via `eval/redaction.py` before persisting.
- **Reports Generated**: Machine-readable `eval/results/latest.json` and human-readable `eval/results/latest.md`.
- **Quality Gate Isolation**: Evaluation suite is completely separated from the default software test gate
  (`pytest -m "not eval"` remains fast and deterministic). Evaluator tests itself verified in `tests/unit/test_evaluator.py`.

## Verified technology

Confirmed on 2026-09-06 from official sources and by introspecting the installed packages,
not from memory or tutorials.

| Component | Version | Note |
| --- | --- | --- |
| MCP specification | `2026-07-28` | Current revision |
| `mcp` (official Python SDK) | 2.1.1 | v2 line; `MCPServer`, not `FastMCP` |
| `mcp-types` | 2.1.1 | Protocol types, split out in v2 |
| Python | 3.13.13 | via uv |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | |
| SQLAlchemy / asyncpg / alembic | 2.0.52 / 0.31.0 / 1.19.2 | Async migrations and connection pool |
| `httpx2` | 2.12.0 | Already an SDK v2 dependency (D-012); no second HTTP stack |
| structlog | 26.1.0 | stderr only |
| ruff / mypy / pytest | 0.16.6 / 2.3.1 / 9.1.1 | |
| PostgreSQL | 18-alpine | via compose |
| Hunter API | v2 | `X-API-KEY` header; free plan includes API access, 50 credits/month |

## Completed

**Project setup** — git repository, `pyproject.toml` with locked dependencies, ruff (incl.
security, docstring and annotation rules), mypy strict, pytest with typed markers,
`.gitignore` with secrets first, `.env.example`, GitHub Actions CI, Docker Compose
PostgreSQL 18.

**Server** — `build_server()` factory; settings-bound lifespan; agent-facing server instructions;
stdio and streamable-http transports; console-script entry point.

**Cross-cutting** — frozen `Settings` with `SecretStr` and `extra="forbid"`; error taxonomy
with a single `ToolError`/`MCPError` routing rule; structlog to stderr with central
redaction; `AppContext` with graceful degradation when the database is absent; bounded
async engine and startup probe.

**Domain and boundaries** — vendor-neutral `Company`, `Contact`, `ContactFilter`;
`WriteResult`/`WriteOutcome` with computed `success`; `AuditEvent` with no `DELETE`
operation; `AuditSink` protocol; `CompanyEnrichmentProvider`, `ContactEnrichmentProvider`
and `CrmRepository` ports.

**Mock CRM persistence and schema (Phase 2)**
* SQLAlchemy ORM models: `companies`, `contacts`, `lists`, `list_members`, `audit_log`.
* Alembic async migration suite with initial schema migration (`317575510439_initial_crm_schema.py`).
* Partial unique indexes for natural contact identities (`email` and `(provider_name, provider_contact_id)`).
* Engine-level database check constraint (`ck_audit_log_no_delete`) rejecting destructive audit records.
* Unique constraint (`uq_list_members_list_contact`) preventing duplicate list memberships.
* `PostgresCrmRepository` implementing `CrmRepository` with safe upserts, no-delete enforcement,
  and query filtering.
* `PostgresAuditSink` implementing `AuditSink` with durable PostgreSQL transaction persistence.
* Reproducible seed mechanism (`gtm_mcp.crm.seed`) with realistic synthetic B2B accounts,
  prospects, lists, and memberships.

**External enrichment (Phase 3)**
* Provider survey against current official documentation, decided and recorded as **D-017**:
  Hunter selected as the live provider for both capabilities; Apollo, Prospeo, People Data
  Labs and Clearbit evaluated and rejected, with reasons.
* Input normalisation (`domain/identifiers.py`): URLs, `www`, casing, ports, paths, trailing
  dots and email addresses all reduce to one canonical domain; text that is not a domain is
  kept as a name and never guessed into one.
* Canonical enrichment results (`domain/enrichment.py`): `CompanyLookup` / `ContactLookup`
  with a **computed** `found` flag, and `EnrichmentProvenance` recording provider, match
  basis, timestamp and whether the data is live.
* `Company` gained `city` / `state`; `Contact` gained `phone` / `city` / `country`. The
  columns already existed in the schema, so the repository mapping and upsert were extended
  to match — previously an enriched phone number would have been silently dropped.
* Shared outbound HTTP client (`providers/http.py`): explicit timeout, bounded retries with
  deterministic exponential backoff, retrying only 5xx and transport failures.
* Hunter adapters (`providers/hunter.py`) for both ports, with payloads validated into
  private Pydantic models before use and vendor status codes mapped to domain errors.
* Offline sample adapters (`providers/sample.py`) over a committed synthetic dataset — the
  default provider, so a fresh clone can exercise both tools with no credential.
* `EnrichmentService`: normalises, calls the provider exactly once, and turns "no record"
  into a readable result rather than an error. No vendor name appears above the adapters.
* `search_company` and `search_contact` MCP tools, read-only, with `open_world_hint=True`.

**CRM tools and the write control model (Phase 4)**
* `CrmService` (`services/crm.py`): bounded reads, and `_execute_write` — the **only** caller
  of a `CrmRepository` write method in the codebase. It checks `enable_write_tools`, then
  `max_write_batch_size` against a caller-supplied record count, then business preconditions,
  then `dry_run_writes`, before delegating and auditing the real outcome (**D-019**).
* `enable_write_tools` now defaults to **false**. A refused write returns `rejected`, calls
  no repository method, and is still audited with `error_code=write_rejected`.
* `dry_run_writes` validates fully — including "does this contact exist?" — audits with
  `dry_run=true`, and returns `dry_run`, which is not a success (D-009).
* `ContactSyncInput` (`domain/writes.py`): the submission contract. No `source` field, so an
  agent cannot claim CRM authority for its own data (**D-021**); every string bounded to its
  column width; email and ISO country code validated at the schema so a bad value fails
  where the model can read the reason.
* `ContactQueryResult`: query envelope with computed `count` and `limit_reached`, using the
  D-018 serialisation-mode override.
* `AuditEvent` gained a `details` map, persisted into the `audit_log.details` column that
  already existed. Known-sensitive keys are redacted and values truncated on the event
  itself, so a call site cannot leak an address into the trail.
* Atomicity boundary chosen and documented (**D-020**): CRM write and audit write are
  separate transactions, mutation first, audit failures escalated rather than swallowed, with
  the residual crash window stated plainly rather than glossed over.
* `crm_query`, `sync_to_crm`, `save_to_list` registered with annotations and descriptions
  written for a model choosing among them.

**Tools** — `server_info`, `search_company`, `search_contact`, `crm_query`, `sync_to_crm`,
`save_to_list`. `server_info` reports implemented versus planned capabilities (the planned
list is now empty), the configured enrichment provider and whether it is live, and all three
write guardrail settings so an agent can plan around them.

**Tests** — 320 passing in standard gate (218 unit, 75 mcp, 27 integration) + 28 eval tests (348 total).

## Verification performed

| Check | Result |
| --- | --- |
| `ruff check .` | Pass (0 errors) |
| `ruff format --check .` | Pass (90 files formatted) |
| `mypy` (strict) | Pass (80 source files) |
| `pytest -m "not eval"` (standard gate) | 320 passed in 100.9s |
| `pytest -m eval` (eval integration gate) | 28 passed in 3.2s |
| `python -m eval.runner` (eval harness) | 28 passed, 0 failed (100.0% pass rate, 100.0% safety, 100.0% policy) |
| Destructive-SQL screen over `src/` | No `DELETE`/`DROP`/`TRUNCATE`; only static `text()` literals (probe, partial-index predicates) |
| Enrichment calls during the test suite | Zero — every provider test runs on a scripted transport |
| Redaction check on evaluation traces | Verified: emails masked (`e***@domain`), phones masked, secrets redacted |

## Project Completion Summary

All five planned phases are complete, thoroughly tested, and documented:
1. **Foundation (Phase 1)**: MCP server foundation, MCP SDK v2, leaf `AppContext`, contract tests (`02e8e9c`).
2. **Mock CRM (Phase 2)**: PostgreSQL mock CRM, SQLAlchemy 2.0 ORM, Alembic migrations, seeded CRM, `PostgresCrmRepository`, `PostgresAuditSink` (`fa09030`).
3. **External Enrichment (Phase 3)**: External enrichment research (D-017), Hunter company/contact provider, offline sample provider, canonical enrichment models (`b89219b`).
4. **CRM Read/Write Tools (Phase 4)**: `crm_query`, `sync_to_crm`, `save_to_list`, write controls (`enable_write_tools`, `dry_run_writes`, `max_write_batch_size`), D-019/D-020/D-021 (`43516d3`).
5. **Agent Evaluation Harness (Phase 5)**: Dedicated `eval/` harness, 28 deterministic scenarios, transparent 7-axis scoring, D-022/D-023 safety interpretation rules, trace sanitization, reporting.

## Known issues and limitations

| Item | Impact | Plan |
| --- | --- | --- |
| Default provider is an offline synthetic dataset | Out of the box, search results are demonstration data, not real firmographics | Deliberate. Set `GTM_ENRICHMENT_PROVIDER=hunter` with a key for live data; every result reports `provenance.live` |
| Hunter resolves companies by domain only | `search_company` with a company *name* is refused when running live | Adapter returns an actionable error asking for a domain, rather than guessing one |
| Hunter's company payload has no website field | `Company.website` is unset under the live provider | Left unset rather than synthesised from the domain |
| Free tier is 50 credits/month | ~50 contact searches or ~250 company searches per month | Demonstration budget; documented in D-017 |
| No caching of enrichment results | Repeat lookups re-spend credits | Deferred: a cache is a correctness and staleness decision, not a quick win |
| Write tools ship disabled | Out of the box, `sync_to_crm` and `save_to_list` refuse every mutation | Deliberate (D-019). Set `GTM_ENABLE_WRITE_TOOLS=true` to enable; the refusal is audited and explains itself |
| CRM write and audit write are separate transactions | A process crash between them could leave an un-audited mutation | Documented in D-020. Mutation first, audit failures escalated, both writes idempotent so a replay converges. Not closed by this design |
| `sync_to_crm` handles contacts only | An enriched *company* cannot yet be persisted | The repository has no company upsert; adding one is an additive port method, not a redesign |
| List membership is additive only | A contact cannot be removed from a list | Deliberate (D-008). A future membership *status* change would be additive, not a delete |
| No authentication on streamable-http | Unsafe to expose remotely as-is | Out of scope until remote hosting is a goal |

## Open questions

1. **Enrichment caching and staleness** — a repeat `search_company` for the same domain
   spends a credit again. A cache needs a TTL policy and a way for an agent to force a
   refresh; both are Phase 4-or-later decisions rather than defaults to guess at now.
