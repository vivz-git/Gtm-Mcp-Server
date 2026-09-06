# Project Status

Source of truth for where this build actually is. Updated in the same commit as the work it
describes.

**Last updated:** 2026-09-06 · **Phase 3 of 5 complete** · Quality gate green

---

## Current phase

**Phase 3 — External Enrichment.** ✅ Complete

Two enrichment providers sit behind the ports established in Phase 1, and the first two real
GTM tools — `search_company` and `search_contact` — are registered and callable. Both are
read-only: they return canonical records and write nothing to the CRM.

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

**Tools** — `server_info`, `search_company`, `search_contact`. `server_info` reports which
capabilities are implemented versus planned, which enrichment provider is configured, and
whether that provider is live.

**Tests** — 204 passing across unit (152), mcp (36) and integration (16) markers.

## Verification performed

| Check | Result |
| --- | --- |
| `ruff check .` | Pass |
| `ruff format --check .` | Pass |
| `mypy` (strict) | Pass, 61 source files |
| `pytest -m "not integration"` | 188 passed |
| `pytest` (full suite with PostgreSQL) | 204 passed (152 unit, 36 mcp, 16 integration) |
| Live adapter validation | One call to Hunter Email Finder with the documented no-credit `test-api-key`; response parsed into a canonical contact. Zero credits consumed. |
| Enrichment calls during the test suite | Zero — every provider test runs on a scripted transport |

The live validation earned its keep: it showed that the endpoint can return a *different*
person from the one asked about, and that the adapter was building a record whose
`full_name` came from the query while its name parts came from the response. Fixed, and
covered by `test_the_record_reports_the_person_the_provider_returned`.

## Next phase

**Phase 4 — Write tools.** Not started.

1. `sync_to_crm`: upsert an enriched company or contact into the CRM, through the full
   guardrail sequence (`enable_write_tools`, `dry_run_writes`, `max_write_batch_size`) with
   an audit record for every attempt, including refusals.
2. `save_to_list`: additive, idempotent list membership.
3. `crm_query`: bounded, typed CRM reads.
4. Guardrail tests proving each switch *blocks* something.

## Known issues and limitations

| Item | Impact | Plan |
| --- | --- | --- |
| Default provider is an offline synthetic dataset | Out of the box, search results are demonstration data, not real firmographics | Deliberate. Set `GTM_ENRICHMENT_PROVIDER=hunter` with a key for live data; every result reports `provenance.live` |
| Hunter resolves companies by domain only | `search_company` with a company *name* is refused when running live | Adapter returns an actionable error asking for a domain, rather than guessing one |
| Hunter's company payload has no website field | `Company.website` is unset under the live provider | Left unset rather than synthesised from the domain |
| Free tier is 50 credits/month | ~50 contact searches or ~250 company searches per month | Demonstration budget; documented in D-017 |
| No caching of enrichment results | Repeat lookups re-spend credits | Deferred: a cache is a correctness and staleness decision, not a quick win |
| Guardrail settings read but not yet enforced | No write tool exists to enforce them on | Phase 4 |
| No authentication on streamable-http | Unsafe to expose remotely as-is | Out of scope until remote hosting is a goal |

## Open questions

1. **Enrichment caching and staleness** — a repeat `search_company` for the same domain
   spends a credit again. A cache needs a TTL policy and a way for an agent to force a
   refresh; both are Phase 4-or-later decisions rather than defaults to guess at now.
