# CLAUDE.md — engineering instructions for this repository

Project-specific rules. Read [PROJECT_STATUS.md](PROJECT_STATUS.md) first for where the
build actually is, and [DECISIONS.md](DECISIONS.md) before revisiting a settled decision.

## What this is

An MCP server exposing go-to-market capabilities as agent-callable tools: company and
contact enrichment, CRM query, and controlled writes back to a CRM. It is a portfolio
project, so the engineering quality *is* the deliverable. Correctness, clear boundaries and
honest documentation matter more than feature count.

## Verify current APIs before writing code

The MCP ecosystem moves fast and this repo is pinned to a line that broke compatibility with
everything published before it.

- The SDK is **`mcp` v2** (`MCPServer`, from `mcp.server`). It is **not** `FastMCP`. Nearly
  all MCP tutorials and blog posts describe v1 and do not run here.
- Before using an SDK API you have not used in this repo already: check the installed
  package (`uv run python -c "import inspect, ...; print(inspect.signature(...))"`) or
  <https://py.sdk.modelcontextprotocol.io/>. Do not write it from memory.
- Same rule for any external enrichment API: check the current official docs for pricing,
  auth, rate limits and response shape before coding against it.
- Record anything version-sensitive you discover in `DECISIONS.md`.

Verified on 2026-09-06: MCP spec revision `2026-07-28`; `mcp` 2.1.1; `mcp-types` 2.1.1;
Python 3.13.13.

## Architecture rules

Dependencies point inward: `tools -> services -> ports <- adapters`.

1. **Tools stay thin.** Validate, delegate, translate errors, shape the response. Business
   logic belongs in the service layer.
2. **Only the tool layer imports `mcp`.** Services, domain and adapters raise `GTMError`
   subclasses and know nothing about JSON-RPC.
3. **External systems sit behind a port** (`gtm_mcp/ports.py`). No vendor SDK or raw SQL in
   a tool.
4. **The canonical domain models are vendor-neutral.** Adapters translate; never leak a
   provider's field names inward.
5. **Any type named in a tool signature must be importable at runtime** — the SDK evaluates
   handler annotations to derive schemas, so `TYPE_CHECKING`-only imports raise
   `InvalidSignature` (D-005).
6. **Never write to stdout.** stdout is the JSON-RPC stream under stdio. Log via
   `gtm_mcp.logging_setup`; no `print`.

## Write-tool rules

Non-negotiable, and enforced by tests:

- **No delete, ever** — no delete method on any port, no `DELETE` audit operation, no tool
  name containing delete/remove/destroy/purge/drop/truncate/wipe.
- **Upsert and be idempotent.** The same input applied twice reports `unchanged`.
- **Never overwrite a populated field with `null`.**
- **One record per call** unless `max_write_batch_size` says otherwise.
- **Check guardrails before mutating**: `enable_write_tools`, `dry_run_writes`,
  `max_write_batch_size`.
- **Audit every attempt** — including rejections and failures, not just successes.
- **Return a `WriteResult`.** Never report success for something that did not happen;
  `dry_run` is *not* success.
- Annotate write tools `read_only_hint=False`, `destructive_hint=False`,
  `idempotent_hint=True`.

## Writing tools for a model

The reader of a tool definition is an LLM choosing among several tools.

- Descriptions say when to call it, when not to, and what it changes. One line is not
  enough; a contract test enforces a minimum length.
- Every parameter gets `Field(description=...)` and real constraints (`ge`, `le`,
  `Literal`).
- Schemas come from type hints. **Do not hand-write JSON Schema** (D-006).
- Every tool gets `ToolAnnotations`. A missing annotation fails the contract test.
- Error messages are read by the model: say what was wrong and what to try instead.

## Coding conventions

- Python 3.13, full type annotations, `from __future__ import annotations`.
- Google-style docstrings on every public module, class and function (ruff `D`).
- Pydantic models for anything crossing a boundary; `frozen=True` and `extra="forbid"` by
  default.
- Async all the way down for I/O. No blocking calls in an async handler.
- Line length 100. `ruff format` is the formatter; do not hand-format around it.
- Comments explain *why*, not what. Prefer no comment to a restatement of the code.
- Secrets are `SecretStr`. Never log or interpolate one.

## Testing requirements

Tests are written with the code, not after it.

- `pytest.mark.unit` — fast, no I/O. `integration` — needs PostgreSQL. `mcp` — real
  in-memory protocol session. `eval` — agent tool-selection, excluded from the gate.
- Test **behaviour and failure modes**. A test that only asserts code runs without raising
  is not acceptable.
- Every guardrail needs a test proving it *blocks* something.
- Test MCP tools through a real `Client` session (see `tests/conftest.py`), not by calling
  the Python function directly — that is the only way registration, schema derivation and
  annotations get covered.
- Integration tests skip cleanly when no database is available; they must never fail for
  that reason.

## Security

- Never commit secrets. `.env` is git-ignored; `.env.example` documents the keys.
- Personal data (emails, phone numbers) must not reach logs — add new sensitive keys to
  `REDACTED_KEYS` in `logging_setup.py`.
- All SQL through SQLAlchemy constructs. No string-interpolated SQL.
- Bind the database to loopback only.
- Treat enrichment provider responses as untrusted input: validate into the domain model
  before use.

## Before committing

Run all four. All must pass:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m "not integration"     # add integration once `docker compose up -d db`
```

Then `git status` and `git diff` — check for stray files, credentials, or debug output.

## Git

- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`.
- Subject in the imperative, describing what actually changed. No commit that claims work
  that was not done.
- Commit working increments; do not commit a red test suite.
- Update `PROJECT_STATUS.md` in the same commit as the work it describes.

## Documentation honesty

`README.md` is written for recruiters and interviewers, which makes overstatement expensive.
Describe only what exists. Planned work goes under a roadmap heading, labelled as planned.
If a capability is partial, say which part works. `server_info` reports implemented versus
planned capabilities at runtime, and that list must stay truthful.
