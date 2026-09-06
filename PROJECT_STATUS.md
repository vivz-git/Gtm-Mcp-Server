# Project Status

Source of truth for where this build actually is. Updated in the same commit as the work it
describes.

**Last updated:** 2026-09-06 · **Phase 2 of 5 complete** · Quality gate green

---

## Current phase

**Phase 2 — Mock CRM & Audit Persistence.** ✅ Complete

The mock CRM persistence layer and durable audit sink are implemented against PostgreSQL,
honoring the `CrmRepository` and `AuditSink` boundary protocols established in Phase 1.
Crucially, the MCP tool layer required **zero changes**.

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
| structlog | 26.1.0 | stderr only |
| ruff / mypy / pytest | 0.16.6 / 2.3.1 / 9.1.1 | |
| PostgreSQL | 18-alpine | via compose |
| MCP Inspector | `@modelcontextprotocol/inspector` v2 | Needs Node >= 22.19 |

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

**Mock CRM Persistence & Schema (Phase 2)**:
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

**Tools** — `server_info` only, which reports implemented versus planned capabilities,
database availability and write-guardrail state. Tool layer required zero changes in Phase 2.

**Tests** — 69 passing across unit (42), mcp (12), and integration (15) markers.

## Verification performed

| Check | Result |
| --- | --- |
| `ruff check .` | Pass |
| `ruff format --check .` | Pass (51 files) |
| `mypy` (strict) | Pass, 43 source files |
| `pytest` (unit + mcp) | 54 passed |
| `pytest` (full suite with PostgreSQL) | 69 passed (15 integration, 12 mcp, 42 unit) |
| `alembic upgrade head` | Clean migration execution |
| `alembic downgrade base` / `upgrade head` | Clean rollback and re-application |
| Seed execution (`scripts/seed.py`) | 6 companies, 14 contacts, 3 lists, 11 memberships seeded; repeat run reports 0 created (fully idempotent) |
| Tool layer isolation | Verified: `src/gtm_mcp/tools/` has 0 changes |

## Next phase

**Phase 3 — External Enrichment.** Not started.

1. Enrichment provider survey: evaluate current vendor options (e.g. Apollo, Clearbit, ZoomInfo)
   on pricing, free-tier limits, rate limits, licensing, and response shapes (D-013).
2. Implement `CompanyEnrichmentProvider` and `ContactEnrichmentProvider` adapters against
   the chosen external API using `httpx2` (D-012).
3. Register enrichment provider in the server lifespan.
4. Implement `search_company` and `search_contact` read-only MCP tools.
5. Integration tests against mock/recorded provider responses.

## Known issues and limitations

| Item | Impact | Plan |
| --- | --- | --- |
| No enrichment provider selected | `search_*` tools cannot be built yet | Research task before Phase 3 (D-013) |
| Guardrail settings read but not yet enforced | No write tool exists to enforce them on | Phase 4 (`sync_to_crm`, `save_to_list`) |
| No authentication on streamable-http | Unsafe to expose remotely as-is | Out of scope until remote hosting is a goal |

## Open questions

1. **Enrichment provider selection** — which vendor provides the best balance of free-tier
   allowance, developer access, and API stability for this portfolio project? Blocks Phase 3.
