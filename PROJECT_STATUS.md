# Project Status

Source of truth for where this build actually is. Updated in the same commit as the work it
describes.

**Last updated:** 2026-09-06 · **Phase 1 of 5 complete** · Quality gate green

---

## Current phase

**Phase 1 — Foundation.** ✅ Complete

The server builds, starts, serves a real MCP client over stdio, and has a green quality
gate. No GTM business tool is implemented yet; that is Phase 2 onward and is deliberate.

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
| SQLAlchemy / asyncpg / alembic | 2.0.52 / 0.31.0 / 1.19.2 | Alembic installed, not yet used |
| structlog | 26.1.0 | stderr only |
| ruff / mypy / pytest | 0.16.6 / 2.3.1 / 9.1.1 | |
| PostgreSQL | 18-alpine | via compose |
| MCP Inspector | `@modelcontextprotocol/inspector` v2 | Needs Node >= 22.19 |

**v1 to v2 SDK changes that shaped this code:** `FastMCP` renamed to `MCPServer` and moved
to `mcp.server.mcpserver`; protocol fields are snake_case (`input_schema`, `is_error`,
`structured_content`); `McpError` is now `MCPError`; transport options moved from the
constructor to `run()`; `get_context()` is gone in favour of injecting `ctx: Context`;
`httpx` replaced by `httpx2`; a server built without an explicit `version` reports an empty
string rather than the SDK version.

## Completed

**Project setup** — git repository, `pyproject.toml` with locked dependencies, ruff (incl.
security, docstring and annotation rules), mypy strict, pytest with typed markers,
`.gitignore` with secrets first, `.env.example`, GitHub Actions CI, Docker Compose
PostgreSQL 18.

**Server** — `build_server()` factory (not a module singleton, so tests get a fresh
instance); settings-bound lifespan; agent-facing server instructions; stdio and
streamable-http transports; console-script entry point.

**Cross-cutting** — frozen `Settings` with `SecretStr` and `extra="forbid"`; error taxonomy
with a single `ToolError`/`MCPError` routing rule; structlog to stderr with central
redaction; `AppContext` with an actionable failure when the database is absent; bounded
async engine and startup probe.

**Domain and boundaries** — vendor-neutral `Company`, `Contact`, `ContactFilter`;
`WriteResult`/`WriteOutcome` with computed `success`; `AuditEvent` with no `DELETE`
operation; `AuditSink` protocol with logging and in-memory implementations;
`CompanyEnrichmentProvider`, `ContactEnrichmentProvider` and `CrmRepository` ports.

**Tools** — `server_info` only, which reports implemented versus planned capabilities,
database availability and write-guardrail state.

**Tests** — 55 passing across unit, mcp and integration markers.

## Verification performed

| Check | Result |
| --- | --- |
| `ruff check .` | Pass |
| `ruff format --check .` | Pass (34 files) |
| `mypy` (strict) | Pass, 33 source files |
| `pytest` (unit + mcp) | 54 passed |
| `pytest` (with PostgreSQL up) | 55 passed |
| Real stdio subprocess handshake | Initialize, `tools/list`, `tools/call` all succeed |
| stdout cleanliness under stdio | Verified: JSON-RPC only, logs on stderr |
| `docker compose up --wait db` | Healthy |

Three real defects were found by these checks and fixed rather than worked around:

1. `AppContext` under `TYPE_CHECKING` broke tool registration — the SDK evaluates handler
   annotations at runtime. Moving it to a leaf module also resolved an import cycle (D-005).
2. `ConnectionRefusedError` is an `OSError`, not a `SQLAlchemyError`, so it escaped the
   startup probe's handler and crashed startup instead of degrading.
3. The Postgres 18 image expects its volume at `/var/lib/postgresql`, not
   `/var/lib/postgresql/data`; the original compose file exited on boot.

A fourth issue was a design flaw, not a crash: `build_server(settings)` accepted a settings
object while the lifespan re-read the global singleton, so tests could not run against their
own configuration. The lifespan is now a settings-bound factory.

## Next phase

**Phase 2 — Mock CRM.** Not started.

1. SQLAlchemy models: `companies`, `contacts`, `lists`, `list_members`, `audit_log`.
2. Alembic migration for the initial schema.
3. Seed script with realistic B2B data — enough variety that enrichment merges and
   idempotency are meaningfully exercised.
4. `PostgresCrmRepository` implementing `CrmRepository`, upsert-only.
5. `PostgresAuditSink` implementing `AuditSink` (D-011).
6. Integration tests: idempotent upserts, no-null-overwrite merge, audit rows written for
   rejected and failed writes.

**Exit criteria:** repository and audit sink pass integration tests against seeded
PostgreSQL, with no tool layer changes required.

## Known issues and limitations

| Item | Impact | Plan |
| --- | --- | --- |
| Audit trail is log-only | Not queryable; insufficient alone for compliance | Phase 2 (D-011) |
| No enrichment provider selected | `search_*` tools cannot be built yet | Research task before Phase 3 (D-013) |
| No CRM schema or seed data | `crm_query` cannot be built yet | Phase 2 |
| Alembic installed but unused | Dependency ahead of need | First migration in Phase 2 |
| Guardrail settings read but not yet enforced | No write tool exists to enforce them on | Phase 4 |
| No authentication on streamable-http | Unsafe to expose remotely as-is | Out of scope until remote hosting is a goal |
| Integration coverage is thin | Only connectivity is covered | Grows with Phase 2 |

## Open questions

1. **Enrichment provider** — which vendor, on current pricing, free-tier limits, rate
   limits, licensing and coverage? Blocks Phase 3. Requires fresh research, not recall.
2. **Contact identity key** — email when present, but what is the fallback for
   email-less contacts? Affects upsert idempotency; decide in Phase 2.
3. **Merge policy** — when enrichment and CRM disagree on a populated field, does fresher
   data win, or does the CRM stay authoritative? Affects `sync_to_crm`; decide before
   Phase 4.
