# Project Status

Source of truth for where this build actually is. Updated in the same commit as the work it
describes.

**Last updated:** 2026-09-06 · **All 6 of 6 phases complete** · Quality gate green · Production
architecture frozen

---

## Current phase

**Phase 6 — Real MCP client integration and live-agent validation.** ✅ Complete

Phase 5 proved the tools were correct and that a scoring engine could detect an agent misusing
them. Phase 6 answers the question that leaves open: *does this actually work when a real AI
client connects to it?*

- **Committed launch contract.** `.mcp.json` at the repository root, with no absolute path in it
  (D-025). `tests/e2e/` reads that file and spawns the real subprocess, so a change that breaks
  the documented launch fails the test suite rather than someone's client.
- **Verified against real clients, not a simulation.** Claude Code connects, discovers all six
  tools, reads their schemas and calls them; the official MCP Inspector CLI completes the
  handshake and exercises every tool.
- **Live-agent evaluation.** A vendor-neutral `AgentProvider` boundary (`eval/agents.py`) with one
  implementation that drives Claude Code headlessly as a genuine MCP host (D-024). A 12-scenario
  live subset (`eval/live.py`) feeds the **unmodified** Phase 5 scoring engine.
- **Two reports, never averaged.** `latest.*` (deterministic, reproducible) and `live-latest.*`
  (real model, explicitly not reproducible), plus `comparison.md` showing the gap per axis
  (D-026).
- **Canonical demo in three safety modes.** `scripts/demo.py` runs one natural-language request
  through a real agent in safe-read, dry-run and live-write configurations. Nothing is scripted:
  the tool sequence and the closing summary come from the model.
- **No uncontrolled spend.** Every live scenario and demo mode pins the offline enrichment
  provider, so neither can consume a metered credit whatever is in the operator's `.env` (D-027).

### Phase 6 research findings

Verified against the installed tooling, not from documentation alone:

| Finding | Consequence |
| --- | --- |
| `${CLAUDE_PROJECT_DIR}` is not expanded in `.mcp.json` by Claude Code 2.1.263 — `claude mcp list` reports `Missing environment variables: CLAUDE_PROJECT_DIR`, and the server fails with `CONNECTION_CLOSED` | The committed config uses no absolute path at all and relies on the host's working directory (D-025) |
| A project-scoped `.mcp.json` is *pending approval* until accepted interactively | Automated verification and the live harness use `--mcp-config <file> --strict-mcp-config`, which loads a config without that gate |
| `--mcp-config` performs no `${VAR}` expansion | The live harness generates a config with literal values per scenario |
| The MCP SDK stdio client inherits only `DEFAULT_INHERITED_ENV_VARS`, not the full environment | A client configuration must pass `GTM_*` explicitly; verified by launching under that default environment |
| MCP Inspector negotiates the legacy protocol era (`2025-11-25`) by default; the SDK client negotiates `2026-07-28` | The server serves both correctly; no change needed |

## Verified technology

Confirmed on 2026-09-06 from official sources and by introspecting the installed packages,
not from memory or tutorials.

| Component | Version | Note |
| --- | --- | --- |
| MCP specification | `2026-07-28` | Current revision; negotiated live with the SDK client |
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
| Claude Code CLI | 2.1.263 | MCP host used for live evaluation and the demo |
| MCP Inspector | `@modelcontextprotocol/inspector` (npx) | Protocol verification; needs Node 22.19+ |

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

**Real MCP client integration (Phase 6)**
* `.mcp.json` — the committed stdio launch contract, portable and free of absolute paths (D-025).
  `tests/e2e/test_stdio_launch.py` reads that file rather than retyping the command, so a change
  that breaks the documented launch fails the suite.
* New `e2e` pytest marker: six tests that spawn `uv run gtm-mcp-server` as a real subprocess and
  drive it with the SDK's stdio client — handshake, tool discovery, schemas and annotations, an
  enrichment round trip, a refused write with its audit id, an invalid argument surfacing as a
  protocol error, and a full session at `DEBUG` proving stdout stays pure. Deterministic, so they
  run inside the normal gate; they skip cleanly when `uv` is absent.
* `eval/agents.py` — the `AgentProvider` boundary (`AgentRequest`, `AgentRun`, `AgentToolCall`)
  and `ClaudeCodeAgentProvider`, which drives the Claude Code CLI headlessly and parses its
  `stream-json` transcript. Tool calls are matched to their results by tool-use id, not position;
  a transcript with no result event is reported as an error rather than scored as silence.
* `eval/live.py` — `LiveAgentAdapter`, the 12-scenario live subset, per-scenario server
  environment and MCP configuration, CRM reset, and the live runner. Kept conceptually separate
  from `DeterministicAgentAdapter`, which takes an in-memory client a live agent cannot use.
* `eval/tracing.py` — trace assembly extracted so both modes derive `recorded_outcomes` and
  `mutations_count` identically. Two reports that summarise the same tool result differently are
  not comparable.
* `eval/report.py` — mode-aware provenance block, configurable report stem, and
  `render_comparison_report` for `comparison.md`.
* `eval/redaction.py` — `redact_credentials` and `redact_agent_response` for a live agent's free
  text (D-028), plus a fix to the phone pattern, which was eating pieces of UUID record
  identifiers in *both* modes' traces and making them unreadable.
* `scripts/demo.py` — the canonical end-to-end demonstration in three safety modes, plus
  `--unanswerable` for the capability-boundary variant.
* `examples/claude_desktop_config.json` — repaired: the previous file was not valid JSON
  (unescaped backslashes in the Windows path), so anyone who copied it got a parse error.

**Tests** — 357 passing in the standard gate (248 unit, 75 mcp, 6 e2e, 28 eval), 27 integration
tests additionally when PostgreSQL is available. 384 total.

## Verification performed

| Check | Result |
| --- | --- |
| `ruff check .` | Pass (0 errors, 99 files) |
| `ruff format --check .` | Pass (99 files) |
| `mypy` (strict) | Pass (86 source files) |
| `pytest -m "not integration"` (standard gate) | 357 passed in 72.3s |
| `pytest -m integration` (real PostgreSQL) | 27 passed in 89.3s |
| `pytest -m e2e` (real stdio subprocess) | 6 passed in 36.6s |
| `pytest -m eval` (evaluator's own tests) | 28 passed |
| `python -m eval.runner` (deterministic harness) | 28 passed, 0 failed — 100.0% pass rate, 100.0% safety, 100.0% policy |
| `python -m eval.live` (real model, 12 scenarios) | 10 passed, 2 failed — 83.3% pass rate, 91.2% composite, **95.8% safety, 100.0% policy**, ~18.3s mean latency, ~$0.55 |
| Live run-to-run variance (3 runs) | 9/12, 9/12, 10/12 — composite 89.8% / 89.0% / 91.2%. Safety 95.8% and policy 100% in all three |
| Claude Code client connection | Connected; all 6 tools discovered, schemas readable, calls executed, structured results returned, errors surfaced |
| MCP Inspector CLI (`initialize`, `tools/list`, `tools/call`) | Pass. `serverInfo` = `gtm-mcp-server` 0.1.0; all 6 tools listed with annotations; `search_company`, `search_contact`, `crm_query`, `sync_to_crm`, `save_to_list` all exercised under the write-safe default |
| Protocol revisions negotiated | `2026-07-28` with the SDK client; `2025-11-25` with the Inspector's default legacy era. Both served correctly |
| stdio purity | Verified by subprocess at `GTM_LOG_LEVEL=DEBUG`: only JSON-RPC on stdout, all logs on stderr, exit code 0 when the host closes stdin |
| Demo — safe read | `sync_to_crm` → `rejected`; agent reported nothing was written and did not attempt `save_to_list` |
| Demo — dry run | `sync_to_crm` → `dry_run`; agent reported nothing was persisted and explained why it could not proceed to the list add |
| Demo — live write | `crm_query` → `search_company` → `search_contact` → `sync_to_crm` (`created`) → `save_to_list` (`created`); 2 audit rows written; agent flagged the synthetic provenance unprompted |
| Destructive-SQL screen over `src/` | No `DELETE`/`DROP`/`TRUNCATE`; only static `text()` literals (probe, partial-index predicates) |
| Enrichment calls during the test suite | Zero — every provider test runs on a scripted transport |
| External API credits spent by evaluation or demo | Zero — both pin `GTM_ENRICHMENT_PROVIDER=sample` |
| Secret scan over tracked files | Clean. Only match is a fixture credential in `tests/unit/test_evaluator.py` asserting that redaction works |
| Machine-specific absolute paths in tracked files | None |
| Redaction check on both evaluation reports | Emails masked in traces (`e***@domain`), phone numbers masked everywhere (25 occurrences in the live report, 0 raw), no credentials, no DSNs, record identifiers left readable |

## Project Completion Summary

All six planned phases are complete, thoroughly tested, and documented. **The production
architecture is frozen at Phase 6.**
1. **Foundation (Phase 1)**: MCP server foundation, MCP SDK v2, leaf `AppContext`, contract tests (`02e8e9c`).
2. **Mock CRM (Phase 2)**: PostgreSQL mock CRM, SQLAlchemy 2.0 ORM, Alembic migrations, seeded CRM, `PostgresCrmRepository`, `PostgresAuditSink` (`fa09030`).
3. **External Enrichment (Phase 3)**: External enrichment research (D-017), Hunter company/contact provider, offline sample provider, canonical enrichment models (`b89219b`).
4. **CRM Read/Write Tools (Phase 4)**: `crm_query`, `sync_to_crm`, `save_to_list`, write controls (`enable_write_tools`, `dry_run_writes`, `max_write_batch_size`), D-019/D-020/D-021 (`43516d3`).
5. **Agent Evaluation Harness (Phase 5)**: Dedicated `eval/` harness, 28 deterministic scenarios, transparent 7-axis scoring, D-022/D-023 safety interpretation rules, trace sanitization, reporting (`82ead6d`).
6. **Real MCP Client Integration (Phase 6)**: Committed `.mcp.json` launch contract, real-subprocess e2e tests, `AgentProvider` boundary and `LiveAgentAdapter`, a 12-scenario live evaluation over real MCP reported separately from the deterministic baseline, a three-mode end-to-end demo, and D-024 through D-029.

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
| No people-search by title | `search_contact` needs a person's name; nothing here finds "the VP of Sales at X" | A real capability gap, not a bug. A correct agent reports it rather than inventing a name — see `scripts/demo.py --unanswerable`. Closing it needs a provider that supports role search |
| Live evaluation scores vary between runs | The same 12 scenarios scored 9/12, 9/12 and 10/12 across three runs | Inherent to measuring a real model. The report is labelled non-reproducible; the deterministic 28-scenario suite remains the regression net |
| Golden expectations use literal-phrase matching | A correct paraphrase ("dry-run mode") can miss a required phrase ("dry run") and cost points | Documented in D-029 and left in place. Replacing it with a semantic check is a change to the *method*, applied to both modes, not a per-scenario patch |
| `.mcp.json` relies on the host's working directory | `${CLAUDE_PROJECT_DIR}` does not expand in Claude Code 2.1.263 | D-024/D-025. The absolute-path form is documented as `claude mcp add --scope local` and deliberately not committed |
| A live evaluation run resets the demo CRM | `python -m eval.live` drops and reseeds the schema before running | Deliberate (D-027), required for comparability. `--no-reset-db` opts out at the cost of reproducibility |

## Open questions

1. **Enrichment caching and staleness** — a repeat `search_company` for the same domain
   spends a credit again. A cache needs a TTL policy and a way for an agent to force a
   refresh; both are decisions rather than defaults to guess at now.
2. **Semantic response scoring** — literal-phrase matching under-credits a correct paraphrase
   (D-029). A model-graded or embedding-based check would fix it, at the cost of making the
   *deterministic* suite non-deterministic. Whether that trade is worth making is the open
   question, not how to implement it.
