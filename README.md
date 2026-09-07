# GTM MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent research a company,
find the right contact, and safely sync that work into a CRM — with writes off by default,
a dry-run mode, and every attempt audited.

## Demo

> **Placeholder — no recording exists yet.** Do not treat this as a claim that a demo is
> attached. When recorded, replace this block with the actual GIF/video and remove this note.
>
> **What to record:** run `uv run python -m scripts.demo --mode live-write` from a terminal
> next to the Claude Code chat pane, using the prompt below (see
> [Example Workflow](#example-workflow)). Capture the full sequence: the agent calling
> `crm_query` → `search_company` → `search_contact` → `sync_to_crm` (`created`) →
> `save_to_list` (`created`), the tool results as MCP renders them, and the agent's closing
> summary — which should mention, unprompted, that the enrichment data is synthetic
> (`provenance.live: false`). A second short clip of `--mode safe-read` showing a `rejected`
> write would demonstrate the guardrail in the same recording session.

## Why This Exists

GTM teams run enrichment, list building and CRM hygiene through a stack of disconnected
tools and a lot of manual copying: look up a company, find who to talk to, check whether the
CRM already knows them, write back what changed. An AI agent can do that work end to end if
it has well-described, safe tools to call — the research part is easy; the "safe" part is
not. An agent with unrestricted write access to a CRM is one ambiguous instruction away from
corrupting a revenue system, so this project treats write safety as the central design
problem rather than an afterthought.

MCP is the interface choice: one stdio server, discovered and called identically by Claude
Code, Claude Desktop, or the MCP Inspector, with tool schemas and safety hints
(`readOnlyHint`, `destructiveHint`, `idempotentHint`) derived from types rather than
hand-written — which also makes the interface testable, including by a real model (see
[Evaluation](#evaluation)).

## Architecture

```
MCP client (Claude Desktop / Claude Code / Inspector)
              │  JSON-RPC over stdio
┌─────────────▼─────────────────────────────────────────┐
│ tools/     schemas from types · annotations · no logic │
├──────────────────────────────────────────────────────  │
│ services/  EnrichmentService · CrmService (guardrails)  │
├──────────────────────────────────────────────────────  │
│ ports.py   CompanyEnrichmentProvider · CrmRepository    │
│            (no delete method exists here)               │
├──────────────────────────┬───────────────────────────  │
│ providers/               │ crm/                         │
│ hunter (live) · sample   │ PostgresCrmRepository        │
└──────────────────────────┴───────────────────────────  ┘
```

Dependencies point inward only. Only the tool layer imports `mcp`; everything below is
ordinary, testable Python raising domain errors (`GTMError` subclasses), with no knowledge of
JSON-RPC. Swapping an enrichment vendor or the CRM backend is one new adapter, with no tool
change. Full detail in [ARCHITECTURE.md](ARCHITECTURE.md); the reasoning behind each choice in
[DECISIONS.md](DECISIONS.md).

## MCP Tools

| Tool | Read/Write | Purpose | Safety behavior |
| --- | --- | --- | --- |
| `server_info` | read | Reports capabilities, configured provider, and current guardrail settings | — |
| `search_company` | read | Firmographic enrichment from a domain or name | One provider call per invocation; never retries a rejection or guesses a domain |
| `search_contact` | read | People enrichment from a name and company | Same cost discipline; resolves one named person, not a title |
| `crm_query` | read | Query the CRM with bounded, typed filters | No provider cost; emits no audit event (nothing mutated) |
| `sync_to_crm` | write | Upsert one contact into the CRM | Disabled by default (`rejected`); dry-run capable; never overwrites a populated field with `null`; audited every attempt |
| `save_to_list` | write | Add an existing contact to a named list | Same guardrails as above; adding twice reports `unchanged`, not a duplicate |

Write tools stay listed and callable even when disabled — a refusal is audited and explains
itself to the agent, rather than the tool silently disappearing.

## Example Workflow

This is a real, currently supported workflow — not a mockup. One natural-language request,
run against the live tool set with nothing scripted beyond the request itself:

> *"Research Northwind Logistics (northwindlogistics.com), look up their VP of Sales Dana
> Whitfield, sync that contact to the CRM, and add her to my 'Q4 Outreach' list."*

```bash
uv run python -m scripts.demo --mode live-write
```

The agent runs `crm_query` (check what's already known) → `search_company` (enrich the
account) → `search_contact` (find Dana Whitfield) → `sync_to_crm` (`created`) →
`save_to_list` (`created`) — and reports the data's synthetic provenance unprompted. The same
request in `--mode safe-read` produces `rejected` on the write, with an explanation instead of
a fabricated success; `--mode dry-run` validates and reports `dry_run` without persisting
anything.

## Guardrails

Enforced by tests, not just documented:

- **Writes disabled by default.** `GTM_ENABLE_WRITE_TOOLS=false` out of the box; every
  mutation attempt returns `rejected` rather than being silently skipped or hidden.
- **Dry-run mode.** Full validation — including preconditions like "does this contact
  exist?" — runs, then the write returns `dry_run` without touching the database. `dry_run`
  is explicitly not a success (`WriteResult.success` is a computed field, not assignable).
- **Bounded writes.** `max_write_batch_size` (default 1) caps records per call; requests over
  the limit are `rejected`.
- **Idempotency.** Contacts upsert on normalized email (or provider identity when email is
  absent); list membership is unique per `(list, contact)`. Syncing or adding twice reports
  `unchanged` the second time — no duplicate rows.
- **No delete path.** There is no delete method on the CRM port, no `DELETE` audit operation,
  and a database check constraint rejects anything but `upsert`/`list_add`. This is a
  structural guarantee, not a policy that could be bypassed.
- **Audit trail.** Every write attempt — success, rejection, dry run, or failure — produces
  exactly one audit event with the outcome, target, and changed fields; sensitive fields are
  redacted on the event itself.
- **CRM-authoritative conflict policy.** CRM-curated values win over enrichment values; a
  field omitted from a submission is never cleared; an agent cannot mark its own submitted
  data as CRM-sourced (`ContactSyncInput` has no `source` field).

All three write controls are enforced at a single chokepoint (`CrmService._execute_write`),
the only caller of a repository write method in the codebase — a structural test fails if a
second route appears.

## Evaluation

The harness in [`eval/`](eval/README.md) measures whether an agent can use these tools
correctly: right tool, right order, respecting the read/write boundary, and — the part that
matters most — never claiming a write happened when it did not.

It runs in **two modes, reported separately and never averaged**, because they answer
different questions.

### Deterministic evaluation

**28 scenarios**, a scripted agent over an in-memory MCP session, offline provider — free and
fully reproducible. Includes deliberate fault modes so the scorer is proven to *detect* a
false success claim or an unauthorized write.

| Axis | Score |
| --- | :---: |
| Tool selection / Sequence / Outcome / Safety / Policy / Final response | 100% each |
| **Composite** | **100%** |
| Scenarios passed | 28 / 28 |

**A 100% deterministic score is not "100% agent reliability."** It means the tools make
correct behavior expressible and the scorer reliably detects incorrect behavior — a statement
about the *server and the test harness*, not about any model's judgment.

### Real-agent evaluation

**12 scenarios**, Claude Code driving a real model over real MCP as a stdio subprocess — the
agent gets exactly the six GTM tools and nothing else (no filesystem, no shell, no
`CLAUDE.md`). This is the measurement of actual model behavior, from the committed report
(`eval/results/live-latest.md`, run on 2026-09-06):

| Axis (weight) | Score |
| --- | :---: |
| Tool selection (15%) | 79.2% |
| Sequence accuracy (15%) | 100% |
| Tool efficiency (10%) | 89.6% |
| Outcome correctness (20%) | 100% |
| Safety interpretation (20%) | 95.8% |
| Read/write policy (10%) | 100% |
| Final response (10%) | 62.5% |
| **Composite** | **91.2%** |
| Scenarios passed | 10 / 12 |
| Mean latency | ~18.3s |
| Cost per run | ~$0.55 |

What held in every one of three runs so far (89.8% / 89.0% / 91.2% composite): **no false
success claim, ever.** Rejected, dry-run, and failed writes were all reported as not done,
and read/write policy adherence stayed at 100% — no read-only request ever triggered a
mutation. The two recurring failures are documented as measurement limitations rather than
tuned away: one is literal-phrase matching under-crediting a correct paraphrase
(`dryrun-sync`), the other is the agent correctly declining a request no tool can answer
(`failure-empty-search`) and being scored as if it should have guessed. See DECISIONS.md
D-029 for why the golden expectations were not relaxed to flatter the score.

```bash
uv run python -m eval.runner        # deterministic → eval/results/latest.{json,md}
uv run python -m eval.live          # live, costs money → eval/results/live-latest.{json,md}
```

## Quick Start

Requirements: [uv](https://docs.astral.sh/uv/) (supplies Python 3.13 itself) and Docker (for
the mock CRM database).

```bash
git clone https://github.com/vivz-git/Gtm-Mcp-Server
cd Gtm-Mcp-Server

uv sync --extra dev              # environment from the lockfile
cp .env.example .env             # every value has a working default
docker compose up -d --wait db

uv run alembic upgrade head
uv run python -m scripts.seed    # 6 companies, 14 contacts, 3 lists — all synthetic
uv run pytest -m "not integration"
uv run gtm-mcp-server            # starts on stdio, waits for a client; Ctrl-C to stop
```

No API key is required to run every tool: the default provider is a synthetic dataset
committed to this repo, and every result it returns carries `provenance.live: false`.

## Claude MCP Setup

The launch contract is committed as [`.mcp.json`](.mcp.json), so a fresh clone needs no
editing:

```json
{ "mcpServers": { "gtm": { "type": "stdio", "command": "uv", "args": ["run", "gtm-mcp-server"] } } }
```

**Claude Code** — start `claude` from the repository root and approve the project-scoped
server when prompted (`/mcp` shows its status). It resolves the project from the *current
working directory*: `${CLAUDE_PROJECT_DIR}` is not expanded by the current CLI (verified
against 2.1.263 — see DECISIONS.md D-024/D-025), which is why the committed config has no
absolute path. If your client runs from elsewhere, register it explicitly at local scope
instead (machine-specific, so not committed):

```bash
claude mcp add gtm --scope local -- uv --directory /absolute/path/to/repo run gtm-mcp-server
```

**Claude Desktop** — merge [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)
into your config file, replacing the path, then restart the app.

Then ask the agent *"what GTM capabilities do you have?"* to exercise `server_info`, or
*"tell me about cloudscale.io"* for `search_company`.

## Safety Modes

The same server binary runs in three configurations, reported by `server_info`:

| Mode | Configuration | What an agent can do |
| --- | --- | --- |
| **Safe read** (default) | `GTM_ENABLE_WRITE_TOOLS=false` | Research works; every write returns `rejected`, changes nothing, and is still audited |
| **Dry run** | `GTM_ENABLE_WRITE_TOOLS=true`, `GTM_DRY_RUN_WRITES=true` | Writes are fully validated then return `dry_run`; nothing is persisted |
| **Live write** | `GTM_ENABLE_WRITE_TOOLS=true`, `GTM_DRY_RUN_WRITES=false` | Writes persist to the seeded demo CRM, one record per call, every attempt audited |

Set these in `.env`, or per client in the MCP config's `env` block.

## Testing

```bash
uv run ruff check .                    # lint
uv run ruff format --check .           # formatting
uv run mypy --strict                   # type checking
uv run pytest -m "not integration"     # unit + mcp + e2e (integration needs `docker compose up -d db`)
uv run python -m eval.runner           # deterministic evaluation, outside the pytest gate
```

357 tests pass in the standard gate (248 unit, 75 mcp, 6 e2e, 28 eval); 27 additional
integration tests run against real PostgreSQL. MCP tools are tested through a real in-memory
`Client` session, not by calling the Python function directly, so registration, schema
derivation, and annotations are all covered.

## Known Limitations

Stated plainly rather than hidden:

| Limitation | Detail |
| --- | --- |
| Default data is synthetic | Both search tools answer from a dataset committed to this repo unless `GTM_ENRICHMENT_PROVIDER=hunter` is set with a key; every result carries `provenance.live` |
| Live enrichment is domain-only and metered | Hunter cannot resolve a company by name, and the free tier is 50 credits/month |
| No people search by title | `search_contact` resolves one named person per call; a correct agent says so rather than inventing a name (`scripts/demo.py --unanswerable`) |
| `sync_to_crm` handles contacts only | An enriched company cannot yet be persisted; the repository has no company upsert |
| List membership is additive only | No way to remove a contact from a list — there is no delete anywhere |
| Write and audit are separate transactions | A crash between them could leave an un-audited mutation; both are idempotent so a replay converges (D-020) |
| No caching of enrichment results | A repeat lookup re-spends a credit |
| `streamable-http` has no authentication | Fine for local stdio use; unsafe to expose remotely as-is |
| Live evaluation is not a reliability guarantee | One model, one day, twelve scenarios; a rerun can score differently |
| `.mcp.json` relies on the host's working directory | `${CLAUDE_PROJECT_DIR}` is not expanded by the current Claude Code CLI; use `claude mcp add --scope local` for a host that runs elsewhere |

## Project Status

**Complete and frozen for portfolio purposes.** All six planned phases (foundation, mock CRM,
external enrichment, guarded write tools, deterministic evaluation, real MCP client
integration with live-agent evaluation) are implemented, tested, and verified against real
MCP clients (Claude Code, MCP Inspector) — not simulated. Full build history and verification
results in [PROJECT_STATUS.md](PROJECT_STATUS.md); the reasoning behind every non-obvious
choice in [DECISIONS.md](DECISIONS.md).

## License

MIT
