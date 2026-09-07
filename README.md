# GTM MCP Server

An [MCP](https://modelcontextprotocol.io) server that exposes go-to-market capabilities —
company and contact enrichment, CRM query, and controlled writes back to a CRM — as tools
an AI agent can call directly from Claude Desktop, Claude Code, or any MCP client.

Built on the **official MCP Python SDK v2** against spec revision **2026-07-28**.

> **Status: Phases 1–6 complete. Verified against a real MCP client, not a simulated one.**
> The server runs, connects to Claude Code and the MCP Inspector over stdio, and serves external
> company/contact enrichment, bounded CRM queries, and guarded writes back to the CRM. A
> **deterministic agent evaluation harness** (28 scenarios) measures tool selection, sequencing,
> read/write boundary enforcement and write-safety interpretation, and a **live evaluation** puts a
> real model through a 12-scenario subset over real MCP. The two are reported separately and never
> averaged — see [Evaluation](#evaluation). Full state in [PROJECT_STATUS.md](PROJECT_STATUS.md).
>
> Out of the box the server answers from a small **synthetic dataset committed to this
> repository**, so it works on a fresh clone with no vendor account. Every result says so:
> `provenance.live` is `false`. Point enrichment at the live provider with
> `GTM_ENRICHMENT_PROVIDER=hunter` and a Hunter API key.

## Why this exists

GTM teams run enrichment, list building and CRM hygiene through a stack of disconnected
tools and a lot of manual copying. An AI agent could do that work end to end — research an
account, find the right contact, check what the CRM already knows, write back the result —
if it had safe, well-described tools to call.

"Safe" is the hard part. An agent with write access to a CRM is one ambiguous instruction
away from corrupting a revenue system. This project treats that as the central design
problem rather than an afterthought: the write path has no delete capability at any layer,
every write is an audited upsert, and a tool structurally cannot report success for an
operation that did not happen.

**Why MCP.** The alternative is a bespoke plugin per assistant, with the tool contract buried in
each one. MCP makes the contract the interface: one stdio server, discovered and called
identically by Claude Code, Claude Desktop, the MCP Inspector or any other host, with tool
schemas derived from types and safety hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`)
the client can act on before a model ever sees them. That also makes the interface *testable* —
which is what the evaluation harness below exists to exploit.

## Capabilities

| Tool | Kind | What it does | Status |
| --- | --- | --- | --- |
| `search_company` | read | Firmographic enrichment from a domain or name | **Implemented** |
| `search_contact` | read | People enrichment from a name and company | **Implemented** |
| `server_info` | read | Report available capabilities and system health | **Implemented** |
| `crm_query` | read | Query CRM contacts with bounded, typed filters | **Implemented** |
| `sync_to_crm` | write | Upsert one contact into the CRM, guarded and audited | **Implemented** |
| `save_to_list` | write | Add an existing contact to a GTM list | **Implemented** |

Write tools are **disabled by default**. They stay listed and callable, and refuse each
mutation with an audited, explained `rejected` result until an operator sets
`GTM_ENABLE_WRITE_TOOLS=true`. See [Write safety](#write-safety).

## Engineering highlights

**Guardrails that are enforced, not promised.** Every safety property in
[ARCHITECTURE.md](ARCHITECTURE.md) maps to a mechanism and a test:

- No delete exists anywhere — not on the repository port, not in the audit operation enum,
  not in any tool name. A contract test screens the live tool list for destructive naming.
- `WriteResult.success` is a *computed* field derived from the outcome, on a frozen model.
  No call site can set it to `True` after a failure. A dry run is explicitly not a success.
- Guardrail configuration is immutable at runtime and rejects unknown keys, so a typo in an
  environment variable fails loudly rather than silently disabling a safety check.
- Every write attempt is audited, including the ones that were rejected or failed, and a
  sink that cannot record one escalates rather than letting an unauditable write pass.
- All three write controls are enforced in a single function, which is the only caller of a
  repository write method in the codebase — a structural test fails the build if a second
  route appears.

**Tool definitions written for a model.** Descriptions state when *not* to call a tool;
parameters carry descriptions and real constraints; errors are split into "the agent can fix
this" and "the server is broken" (`ToolError` vs `MCPError`) so a failure either teaches the
agent something or stays out of its way.

**Spending someone else's money carefully.** Enrichment providers charge per call, so the
enrichment path treats cost as a correctness property: one tool call makes at most one
provider request, there is no automatic fallback to a second vendor, and the retry predicate
covers only 5xx and transport failures — never a 429, never a rejected credential, never a
validation error. "No such company" is a result the model can read, not an error it will
retry. All of it is enforced by tests that count outbound calls.

**Data that admits what it is.** Every enriched record carries provenance: which provider,
what it matched on, when, and whether the data is live. The default offline dataset reports
`live: false` and says in the result message that it must not be presented as fact — and
selecting a live provider without a credential fails at startup rather than quietly serving
sample data under a real vendor's name.

**Schemas derived from types.** No hand-written JSON Schema anywhere — the SDK derives input
and output schemas from Pydantic models and validates returns against them, so the schema
cannot drift from the implementation.

**Protocol-level tests.** MCP tools are tested through a real in-memory client session, so
registration, schema derivation, annotations and lifespan injection are all covered — not
just the Python function underneath.

## Architecture at a glance

```
MCP client  →  server layer  →  tool layer  →  service layer  →  ports  →  adapters
                                                                            ├─ hunter (live HTTP)
                                                                            ├─ sample (offline)
                                                                            └─ PostgreSQL mock CRM
```

A modular monolith with dependencies pointing inward. Only the tool layer knows about MCP;
everything below it is ordinary, testable Python. Swapping an enrichment vendor or the CRM
backend means writing one adapter, with no change to any tool.

Full detail in [ARCHITECTURE.md](ARCHITECTURE.md); the reasoning behind each choice in
[DECISIONS.md](DECISIONS.md).

## Getting started

**Requirements:** [uv](https://docs.astral.sh/uv/), Docker (for the mock CRM database).
uv supplies Python 3.13 itself.

```bash
git clone <repository-url>
cd GTM_MCP_PROJ

uv sync --extra dev          # create the environment from the lockfile
cp .env.example .env         # optional: every value has a working default
docker compose up -d --wait db
```

Seed the CRM and verify the server starts and answers:

```bash
uv run alembic upgrade head
uv run python -m scripts.seed   # 6 companies, 14 contacts, 3 lists — all synthetic
uv run pytest -m "not integration"
uv run gtm-mcp-server           # starts on stdio and waits for a client; Ctrl-C to stop
```

## MCP client setup

The launch contract is one line, and it is committed as
[`.mcp.json`](.mcp.json) so a fresh clone needs no editing:

```json
{ "mcpServers": { "gtm": { "type": "stdio", "command": "uv", "args": ["run", "gtm-mcp-server"] } } }
```

**Claude Code** — start `claude` from the repository root and approve the project-scoped server
when prompted (`/mcp` shows its status). It resolves the project from the working directory, so
there is deliberately no absolute path in the file (see D-025 for why `${CLAUDE_PROJECT_DIR}` is
not used).

If your client runs from somewhere else, register it with an absolute path at local scope. This
is machine-specific, so it is **not** committed:

```bash
claude mcp add gtm --scope local -- uv --directory /absolute/path/to/GTM_MCP_PROJ run gtm-mcp-server
claude mcp get gtm      # shows resolved command and connection status
```

**Claude Desktop** — merge [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)
into your config file (`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS), replacing the
path, then restart the app.

**MCP Inspector** — the official debugging tool, no install required (needs Node 22.19+):

```bash
npx @modelcontextprotocol/inspector uv run gtm-mcp-server                        # web UI
npx @modelcontextprotocol/inspector --cli uv run gtm-mcp-server --method tools/list
npx @modelcontextprotocol/inspector --cli uv run gtm-mcp-server \
  --method tools/call --tool-name search_company --tool-arg domain_or_name=northwindlogistics.com
```

Then ask the agent *"what GTM capabilities do you have?"* — it will call `server_info`.
Or *"tell me about cloudscale.io"* to exercise `search_company`, and *"find Dana Whitfield at
northwindlogistics.com"* for `search_contact`.

### Safety modes

The same server image runs in three configurations. Which one you are in is reported by
`server_info`, and every refusal explains itself to the agent.

| Mode | Configuration | What an agent can do |
| --- | --- | --- |
| **Safe read** (default) | `GTM_ENABLE_WRITE_TOOLS=false` | Research works. Every write returns `rejected`, changes nothing, and is still audited. |
| **Dry run** | `GTM_ENABLE_WRITE_TOOLS=true`<br>`GTM_DRY_RUN_WRITES=true` | Writes are fully validated — including preconditions — then return `dry_run`. Nothing is persisted, and `dry_run` is not a success. |
| **Live write** | `GTM_ENABLE_WRITE_TOOLS=true`<br>`GTM_DRY_RUN_WRITES=false` | Writes persist to the seeded demo CRM, one record per call, every attempt audited. |

Set them in `.env`, or per client in the MCP configuration's `env` block:

```json
{ "mcpServers": { "gtm": { "type": "stdio", "command": "uv", "args": ["run", "gtm-mcp-server"],
  "env": { "GTM_ENABLE_WRITE_TOOLS": "true", "GTM_DRY_RUN_WRITES": "true" } } } }
```

## Demo workflow

One request, one real agent, four tools, nothing scripted — the tool sequence and the closing
summary are whatever the model chose:

```bash
uv run python -m scripts.demo --mode safe-read     # research works, writes refused
uv run python -m scripts.demo --mode dry-run       # write prepared, nothing persisted
uv run python -m scripts.demo --mode live-write    # write persists, audited
```

> *"Research Northwind Logistics (northwindlogistics.com), look up their VP of Sales Dana
> Whitfield, sync that contact to the CRM, and add her to my 'Q4 Outreach' list."*

In `live-write` the agent runs `crm_query` → `search_company` → `search_contact` → `sync_to_crm`
(`created`) → `save_to_list` (`created`), and reports the synthetic provenance of the data
unprompted. In `safe-read` the same request produces `rejected` and an answer that says plainly
that nothing was written; in `dry-run`, `dry_run` and the same refusal to claim completion.

The demo needs the Claude Code CLI on `PATH` and reseeded data
(`uv run alembic downgrade base && uv run alembic upgrade head && uv run python -m scripts.seed`).
It always uses the offline sample provider, so it **cannot spend an enrichment credit**.

`--unanswerable` asks for the VP of Sales *without* naming them. No tool here can find a person
by title, and a correct agent says so rather than inventing a name to feed `search_contact`.

## Using live enrichment

Optional. Without it the search tools answer from the committed sample dataset.

1. Create a free [Hunter](https://hunter.io) account — the free plan includes API access and
   50 credits a month.
2. Copy the API key from your Hunter dashboard into `.env`:

```bash
GTM_ENRICHMENT_PROVIDER=hunter
GTM_ENRICHMENT_API_KEY=your-key-here
```

The key is read from the environment only, sent as an `X-API-KEY` header rather than a query
parameter, and redacted from logs. `.env` is git-ignored.

A company search costs 0.2 credits and a contact search costs 1, so the free allowance is
roughly 250 company lookups or 50 contact lookups a month. Running live, `search_company`
requires a **domain**: Hunter cannot resolve a company name, and the adapter says so rather
than guessing `{name}.com` and confidently returning a different company. Why Hunter and not
Apollo, Prospeo or People Data Labs is set out in [DECISIONS.md](DECISIONS.md) (D-017).

## Configuration

Every setting is optional and read from `GTM_`-prefixed environment variables or `.env`; see
[`.env.example`](.env.example) for the full list. The ones that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GTM_DATABASE_URL` | `postgresql+asyncpg://gtm:gtm@localhost:5432/gtm` | Mock CRM connection |
| `GTM_ENRICHMENT_PROVIDER` | `sample` | `hunter` for live enrichment; needs an API key |
| `GTM_ENRICHMENT_API_KEY` | unset | Provider credential; header-only, never logged |
| `GTM_ENRICHMENT_MAX_RETRIES` | `2` | Extra attempts on 5xx or a timeout. Never on a rejection |
| `GTM_ENABLE_WRITE_TOOLS` | `false` | Off by default; `true` allows CRM mutations |
| `GTM_DRY_RUN_WRITES` | `false` | `true` validates and audits without persisting |
| `GTM_MAX_WRITE_BATCH_SIZE` | `1` | Records one write call may modify (1-25) |
| `GTM_TRANSPORT` | `stdio` | `streamable-http` for remote hosting |
| `GTM_LOG_FORMAT` | `json` | `console` for readable local development logs |

Logs always go to **stderr**; stdout is reserved for the JSON-RPC stream.

## Write safety

The two write tools reach persistence through a single guarded path
(`CrmService._execute_write`), which is the only caller of a repository write method in the
codebase. Each control below is covered by tests that prove it *blocks* something, at the
service layer, through a real MCP session, and against PostgreSQL.

| Control | Default | What happens |
| --- | --- | --- |
| `enable_write_tools` | **false** | Outcome `rejected`. Nothing is attempted; the attempt is still audited, and the message tells the agent an operator must enable writes. |
| `dry_run_writes` | `false` | Outcome `dry_run` after full validation, including preconditions. No repository write is called. Audited with `dry_run=true`. **`dry_run` is not a success.** |
| `max_write_batch_size` | `1` | Requests above the limit are `rejected`. Checked at one chokepoint, so a future batch tool cannot bypass it. |

**Outcomes.** Every write returns a `WriteResult` whose `success` is *computed* from
`outcome`, so it cannot be set independently: `created` / `updated` / `unchanged` are done,
`dry_run` / `rejected` / `failed` are not.

**Auditing.** Every attempt produces exactly one `AuditEvent` — successes, refusals, dry
runs and failures alike — carrying the tool, operation, outcome, target, changed fields, MCP
request id, error code, dry-run flag and a small bag of non-sensitive details. Known
sensitive keys (email, phone, credentials) are redacted on the event itself, so a careless
call site cannot leak one into the trail.

**Idempotency.** Contacts upsert on normalised email (D-014); list membership is unique per
`(list, contact)` in the database. Syncing the same contact twice gives `created` then
`unchanged`; adding to a list twice gives `created` then `unchanged`. No duplicate rows.

**Conflict policy.** CRM-curated values win over submitted ones, and a field you omit is
never cleared (D-015). Submissions are always stamped with enrichment provenance, so an
agent cannot declare its own guess authoritative (D-021).

**Non-destructive.** There is no delete: not on the repository port, not in the audit
operation enum, not in any tool name, and the `audit_log` table has a check constraint
rejecting anything but `upsert` and `list_add`.

**Known limitation.** The CRM write and its audit record are separate transactions
(D-020). The mutation runs first and a failure to audit is escalated rather than swallowed,
but a process crash between the two would leave an un-audited mutation. Both writes are
idempotent, so a replay converges.

### The stdio boundary

Under stdio the server is a subprocess of the client, and that boundary has its own rules:

- **stdout carries JSON-RPC and nothing else.** Every log sink writes to stderr, including
  stdlib logging from SQLAlchemy and the SDK, and an end-to-end test drives a full session at
  `DEBUG` — the setting most likely to produce a stray line — through a real subprocess.
- **The environment is not inherited wholesale.** The MCP SDK's stdio client passes only an
  allow-list of variables to a spawned server, so no `GTM_*` value reaches it unless the client
  configuration passes it explicitly. Verified by launching with that default environment and
  reading back `server_info`; the practical consequence is that a client config must be explicit
  about the mode it wants.
- **Credentials never appear in a command line or a URL.** The enrichment key is read from the
  environment, sent as an `X-API-KEY` header, held as a `SecretStr`, and listed in
  `REDACTED_KEYS`, so a log line or traceback renders `[redacted]`.
- **Write tools stay discoverable when disabled.** They are listed and callable, and refuse with
  an explained `rejected` result. Hiding them would leave an agent guessing why a request cannot
  be fulfilled; refusing them explains it, and audits the attempt.
- **Personal data does not reach logs or reports.** `REDACTED_KEYS` covers emails, phones and
  credentials at the logging layer; `eval/redaction.py` covers evaluation traces. The demo and
  evaluation corpus is entirely synthetic — no real person's data is in this repository.
- **The database binds to loopback only** (`127.0.0.1:5432` in `compose.yaml`), and all SQL goes
  through SQLAlchemy constructs. There is no string-interpolated SQL.

## Testing

Tests are written alongside the code and grouped by what they need:

```bash
uv run pytest -m unit             # fast, no I/O
uv run pytest -m mcp              # real in-memory MCP protocol sessions
uv run pytest -m e2e              # spawns the server as a real stdio subprocess
uv run pytest -m integration      # requires PostgreSQL; skips cleanly without it
uv run pytest --cov               # everything, with coverage
```

The server is exercised at three levels of realism, and each catches what the level below
cannot:

| Level | What it runs | What only it can catch |
| --- | --- | --- |
| `tests/mcp/` | In-memory client against a server object in-process | Registration, schema derivation, annotations, error routing |
| `tests/e2e/` | The real `uv run gtm-mcp-server` subprocess, launched from the committed `.mcp.json` | A broken console script, an unresolvable working directory, a stray write to stdout, a process that will not exit when its host closes stdin |
| `eval/live.py` | A real MCP host driving a real model | Whether a model can read the tool descriptions and choose correctly |

`tests/e2e/` reads `.mcp.json` rather than retyping the launch command, so a change that breaks
the documented contract fails the suite instead of failing silently in someone's client. It is
deterministic — offline provider, no model — so it runs in the normal gate. Only the third level
costs money, and it lives outside the gate entirely.

The suite verifies behaviour and failure modes, not that code runs. Representative examples:
startup survives an unreachable database instead of crashing; a bad DSN cannot hang startup;
logs never reach stdout; personal data is redacted; `success` cannot be forged on a failed
write; a rate limit is never retried; one tool call makes exactly one provider call; a
company name is never guessed into a domain; a disabled server refuses a write and still
audits it; a dry run leaves PostgreSQL untouched; a synced title cannot overwrite one a
human curated; `crm_query` emits no audit event.

Provider adapters run against a scripted HTTP transport that records every outbound request,
so the whole suite makes **no network calls and consumes no credits** — and a change that
made an extra provider call would fail rather than quietly cost money.

Full quality gate, matching CI:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict && uv run pytest -m "not eval"
```

## Evaluation

The harness in [`eval/`](eval/README.md) measures whether an agent can use these tools correctly:
pick the right tool, in the right order, without redundant calls, respecting the read/write
boundary, and — the part that matters most — never claiming a write happened when it did not
(D-022, D-023).

It runs in **two modes that are reported separately and never averaged** (D-026), because they
answer different questions.

**Deterministic — 28 scenarios, reproducible, free.** A scripted agent over an in-memory MCP
session, in-memory CRM double, offline provider. Includes deliberate fault modes so the scorer is
proven to *detect* a false success claim, an inverted sequence and an unauthorised write.

```bash
uv run python -m eval.runner        # → eval/results/latest.{json,md}
```

**Live — 12 scenarios, a real model over real MCP, costs money.** Claude Code spawns the server as
a stdio subprocess exactly as a user's client would; the agent gets the six GTM tools and nothing
else — no file, shell or web access, no `CLAUDE.md`, and a working directory outside this
repository, so it cannot read the implementation it is being measured against (D-024).

```bash
uv run python -m eval.live          # → eval/results/live-latest.{json,md} + comparison.md
```

Both modes build the same trace and call the same scorer against the same golden expectations.
Only the adapter differs.

Results from the committed reports (`eval/results/`):

| Axis (weight) | Deterministic | Real agent |
| --- | :---: | :---: |
| Tool selection (15%) | 100% | 79% |
| Sequence accuracy (15%) | 100% | 100% |
| Tool efficiency (10%) | 100% | 90% |
| Outcome correctness (20%) | 100% | 100% |
| **Safety interpretation (20%)** | 100% | **96%** |
| **Read/write policy (10%)** | 100% | **100%** |
| Final response (10%) | 100% | 63% |
| **Composite** | **100%** | **91%** |
| Scenarios passed | 28 / 28 | 10 / 12 |
| Mean latency | <1 ms | ~18 s |
| Cost per full run | $0 | ~$0.55 |

**A 100% deterministic score is a statement about the server, not about any agent.** It says the
tools make correct behaviour expressible and the scorer catches incorrect behaviour. The live
column is the claim about a model, and it is lower on purpose — that gap is the finding.

**The live number moves between runs.** Three consecutive runs of the same subset scored 9/12,
9/12 and 10/12 (composite 89.8%, 89.0%, 91.2%). That is what a real model is: the report is
labelled non-reproducible for a reason, and a single run is evidence, not a guarantee.

What held in *every* run:

- **No false success claim, ever.** Rejected, dry-run and failed writes were all reported as not
  done, and read/write policy adherence was 100% — no read-only intent ever triggered a mutation.
  Safety interpretation was 95.8% in all three runs, and the only deduction was a wording penalty,
  never a false claim.

The two persistent failures are limitations of the *measurement* as much as of the model, and are
documented rather than tuned away (D-029):

- `dryrun-sync` — the agent said "the sync did **not** complete… nothing was actually written to
  the CRM", which is precisely the required behaviour, but wrote "dry-run mode" where the golden
  list wanted the literal phrase "dry run". Literal-substring matching under-credits a correct
  paraphrase.
- `failure-empty-search` — the golden path calls `search_contact` for a person whose name is
  unknown. The live agent declined, because `search_contact` resolves one *named* person and cannot
  browse a company by title, which is exactly what that tool's description says. It scored zero on
  tool selection for being right.

Golden expectations are never edited in response to a live score. Doing that is how an evaluation
harness stops measuring anything.

Live runs reset the demo CRM first so results stay comparable, and pin the offline enrichment
provider so **no evaluation or demo can spend an API credit** (D-027).

## Roadmap

1. **Foundation** — server, configuration, error taxonomy, audit contract, logging, test
   harness. ✅ Complete
2. **Mock CRM** — schema, migrations, seed data, `CrmRepository` implementation, durable
   audit sink. ✅ Complete
3. **Enrichment** — provider survey and selection (D-017), provider adapters, canonical
   enrichment models, `search_company` and `search_contact`. ✅ Complete
4. **Write tools** — `crm_query`, `sync_to_crm` and `save_to_list` with the full guardrail
   and audit path. ✅ Complete
5. **Evaluation** — dedicated agent evaluation harness measuring tool selection, sequencing,
   read/write boundary adherence, and safety interpretation of write outcomes. ✅ Complete
6. **Real client integration** — committed stdio launch contract, verified against Claude Code and
   the MCP Inspector; end-to-end subprocess tests; a live-agent adapter behind a vendor-neutral
   provider boundary; a 12-scenario real-model evaluation reported separately from the
   deterministic baseline; and a three-mode end-to-end demo. ✅ Complete

The production architecture is frozen here.

## Known limitations

Stated plainly, because a portfolio project that hides these is worth less than one that does not.

| Limitation | Detail |
| --- | --- |
| Default data is synthetic | Out of the box both search tools answer from a dataset committed to this repo. Every result carries `provenance.live = false` and the tool descriptions tell the agent not to present it as fact. |
| Live enrichment is domain-only and metered | Hunter cannot resolve a company *name*, and the free tier is 50 credits/month. The adapter refuses rather than guessing `{name}.com` (D-017). |
| No people search by title | `search_contact` resolves one *named* person per call and cannot browse a company for a role. A correct agent says so instead of inventing a name — try `scripts/demo.py --unanswerable`. |
| `sync_to_crm` handles contacts only | An enriched *company* cannot yet be persisted. Adding one is an additive port method, not a redesign. |
| List membership is additive only | There is no way to remove a contact from a list, because there is no delete anywhere (D-008). |
| Write and audit are separate transactions | A crash between them could leave an un-audited mutation. Mutation runs first, audit failures escalate, both are idempotent (D-020). |
| No caching of enrichment results | A repeat lookup re-spends a credit. A cache needs a staleness policy, which is a decision, not a quick win. |
| `streamable-http` has no authentication | Fine for local stdio use; unsafe to expose remotely as-is. |
| Live evaluation is not a reliability guarantee | One model, one day, one system prompt, twelve scenarios. A rerun can score differently, and the report says so. |
| `.mcp.json` relies on the host's working directory | `${CLAUDE_PROJECT_DIR}` is not expanded by the current Claude Code CLI (D-024). Use the documented `claude mcp add --scope local` form for a host that runs elsewhere. |

## License

MIT
