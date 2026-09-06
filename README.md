# GTM MCP Server

An [MCP](https://modelcontextprotocol.io) server that exposes go-to-market capabilities —
company and contact enrichment, CRM query, and controlled writes back to a CRM — as tools
an AI agent can call directly from Claude Desktop, Claude Code, or any MCP client.

Built on the **official MCP Python SDK v2** against spec revision **2026-07-28**.

> **Status: foundation complete, GTM tools not yet implemented.**
> The server runs, connects to a real MCP client, and serves one diagnostic tool. The five
> GTM tools below are designed and architecturally provided for, but not written yet. The
> live `server_info` tool reports exactly which capabilities are implemented, so this claim
> is checkable rather than something you have to take on faith.
> See [PROJECT_STATUS.md](PROJECT_STATUS.md) for detail.

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

## Planned capabilities

| Tool | Kind | What it does | Status |
| --- | --- | --- | --- |
| `search_company` | read | Firmographic enrichment from a domain or name | Planned |
| `search_contact` | read | People enrichment from a name and company | Planned |
| `crm_query` | read | Query CRM records with bounded, typed filters | Planned |
| `save_to_list` | write | Add an existing contact to a GTM list | Planned |
| `sync_to_crm` | write | Upsert enriched data into the CRM | Planned |
| `server_info` | read | Report available capabilities and system health | **Implemented** |

## Engineering highlights

**Guardrails that are enforced, not promised.** Every safety property in
[ARCHITECTURE.md](ARCHITECTURE.md) maps to a mechanism and a test:

- No delete exists anywhere — not on the repository port, not in the audit operation enum,
  not in any tool name. A contract test screens the live tool list for destructive naming.
- `WriteResult.success` is a *computed* field derived from the outcome, on a frozen model.
  No call site can set it to `True` after a failure. A dry run is explicitly not a success.
- Guardrail configuration is immutable at runtime and rejects unknown keys, so a typo in an
  environment variable fails loudly rather than silently disabling a safety check.
- Every write attempt is audited, including the ones that were rejected or failed.

**Tool definitions written for a model.** Descriptions state when *not* to call a tool;
parameters carry descriptions and real constraints; errors are split into "the agent can fix
this" and "the server is broken" (`ToolError` vs `MCPError`) so a failure either teaches the
agent something or stays out of its way.

**Schemas derived from types.** No hand-written JSON Schema anywhere — the SDK derives input
and output schemas from Pydantic models and validates returns against them, so the schema
cannot drift from the implementation.

**Protocol-level tests.** MCP tools are tested through a real in-memory client session, so
registration, schema derivation, annotations and lifespan injection are all covered — not
just the Python function underneath.

## Architecture at a glance

```
MCP client  →  server layer  →  tool layer  →  service layer  →  ports  →  adapters
                                                                            ├─ enrichment APIs
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

Verify the server starts and answers:

```bash
uv run pytest                # 55 tests
uv run gtm-mcp-server        # starts on stdio; Ctrl-C to stop
```

### Connect it to an MCP client

**Claude Code**

```bash
claude mcp add gtm -- uv --directory /absolute/path/to/GTM_MCP_PROJ run gtm-mcp-server
```

**Claude Desktop** — merge [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)
into your config file (`%APPDATA%\Claude\claude_desktop_config.json` on Windows,
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS), replacing the
path, then restart the app.

**MCP Inspector** — the official debugging tool, no install required:

```bash
npx @modelcontextprotocol/inspector uv run gtm-mcp-server
```

Then ask the agent *"what GTM capabilities do you have?"* — it will call `server_info`.

## Configuration

Every setting is optional and read from `GTM_`-prefixed environment variables or `.env`; see
[`.env.example`](.env.example) for the full list. The ones that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `GTM_DATABASE_URL` | `postgresql+asyncpg://gtm:gtm@localhost:5432/gtm` | Mock CRM connection |
| `GTM_ENABLE_WRITE_TOOLS` | `true` | `false` runs a strictly read-only deployment |
| `GTM_DRY_RUN_WRITES` | `false` | `true` validates and audits without persisting |
| `GTM_TRANSPORT` | `stdio` | `streamable-http` for remote hosting |
| `GTM_LOG_FORMAT` | `json` | `console` for readable local development logs |

Logs always go to **stderr**; stdout is reserved for the JSON-RPC stream.

## Testing

Tests are written alongside the code and grouped by what they need:

```bash
uv run pytest -m unit             # fast, no I/O
uv run pytest -m mcp              # real in-memory MCP protocol sessions
uv run pytest -m integration      # requires PostgreSQL; skips cleanly without it
uv run pytest --cov               # everything, with coverage
```

The suite verifies behaviour and failure modes, not that code runs. Representative examples:
startup survives an unreachable database instead of crashing; a bad DSN cannot hang startup;
logs never reach stdout; personal data is redacted; `success` cannot be forged on a failed
write.

Full quality gate, matching CI:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Roadmap

1. **Foundation** — server, configuration, error taxonomy, audit contract, logging, test
   harness. ✅ Complete
2. **Mock CRM** — schema, migrations, seed data, `CrmRepository` implementation, durable
   audit sink.
3. **Read tools** — `crm_query`, then `search_company` and `search_contact` once an
   enrichment provider is selected on current evidence (D-013).
4. **Write tools** — `sync_to_crm` and `save_to_list`, with the full guardrail and audit
   path.
5. **Evaluation** — measure whether an agent picks the right tool from a realistic GTM
   request, and whether it interprets write outcomes correctly.

## License

MIT
