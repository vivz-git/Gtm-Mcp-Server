# Architecture Decision Record

Numbered, dated decisions with the reasoning that produced them. New decisions are
appended; superseded ones are marked, never deleted, so the reasoning trail survives.

Status values: **Accepted**, **Deferred**, **Superseded**.

---

## D-001 — Python 3.13, managed by uv

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** The MCP SDK requires Python >= 3.10. The development machine has 3.10.11 and
3.14.2 installed; uv can supply any version independently of both.

**Decision.** Target Python 3.13 (pinned in `.python-version`), manage the environment and
lockfile with `uv`.

**Why.** 3.13 has settled binary-wheel support across the whole stack this project needs —
asyncpg, SQLAlchemy, pydantic-core. 3.14 is current, but wheel availability for
C-extension database drivers still lags on Windows, which is the primary development
platform here. uv gives a committed lockfile and reproducible installs without a separate
pyenv/poetry toolchain.

**Consequences.** Contributors need uv. CI installs the same locked versions.

---

## D-002 — Official MCP Python SDK, v2 line, pinned below v3

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** `mcp` is at **2.1.1**. The v1 to v2 transition was a hard break: `FastMCP` was
renamed `MCPServer` and moved from `mcp.server.fastmcp` to `mcp.server.mcpserver`, protocol
types moved to a standalone `mcp-types` package, protocol fields moved from camelCase to
snake_case (`inputSchema` to `input_schema`, `isError` to `is_error`), `McpError` became
`MCPError`, and `httpx` was replaced by `httpx2`. Most MCP material published before
mid-2026 describes the v1 API and does not run on v2.

**Decision.** Build on the official SDK v2 (`mcp[cli]>=2.1.1,<3`). Verify API shape against
the installed package and the official docs, never against tutorials.

**Why.** The official SDK tracks the specification directly. FastMCP 4.x is a capable
third-party alternative but adds a dependency whose release cadence we do not control, for
capability this project does not need. The `<3` ceiling is deliberate: given how much v2
broke, a future v3 must be an explicit, reviewed migration rather than something a
dependency resolver does silently.

**Consequences.** `CLAUDE.md` requires re-verifying SDK APIs before use.

---

## D-003 — Modular monolith with enforced layer boundaries

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Five tools spanning enrichment, CRM persistence and audit.

**Decision.** One deployable process, internally split into tool → service → port → adapter
layers, with dependencies pointing inward only.

**Why.** The tools share a database, an audit trail and a configuration surface, and are
always deployed together. Splitting them into services would add network failure modes and
deployment complexity to buy independent scaling that nothing here needs. The boundaries
that actually matter — swapping an enrichment vendor, swapping the CRM backend — are
satisfied by interfaces, not by process separation.

**Consequences.** Boundaries are a convention that tests and review must uphold, since the
runtime does not enforce them.

---

## D-004 — stdio as the default transport, streamable-http as the option

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Default to stdio; support `streamable-http` via configuration. Do not
implement or document the SSE transport.

**Why.** Local MCP clients (Claude Desktop, Claude Code) spawn servers over stdio, which is
the primary demonstration path. Streamable HTTP is the current transport for remote
servers. SSE is legacy — the SDK still speaks it for older clients, but a new server has no
reason to adopt it.

**Consequences.** Nothing may write to stdout. Enforced by `configure_logging` and by
`tests/unit/test_logging_setup.py`.

---

## D-005 — `AppContext` lives in `gtm_mcp/context.py`, not in the server package

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Found while implementing, not by reading docs: the v2 SDK resolves handler type
annotations **at runtime** in order to derive schemas. A tool signature of
`ctx: Context[AppContext]` therefore fails with `InvalidSignature: Unable to evaluate type
annotations` if `AppContext` is imported only under `TYPE_CHECKING`. Importing it at runtime
from `gtm_mcp.server.lifespan` closed an import cycle:
`server -> app -> tools -> diagnostics -> server`.

**Decision.** `AppContext` lives in its own leaf module that imports nothing from
`gtm_mcp.server`.

**Why.** Breaks the cycle where it originates, rather than papering over it with deferred
imports inside functions.

**Consequences.** Any type named in a handler signature must be importable at runtime.
Recorded in `CLAUDE.md`.

---

## D-006 — Schemas derived from types; no hand-written JSON Schema

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Input and output schemas come from type hints and Pydantic models.
`Annotated[T, Field(description=...)]` supplies per-parameter documentation.

**Why.** The SDK derives both directions from annotations and validates returns against the
derived output schema. A hand-written schema is a second source of truth that drifts from
the implementation silently — and the drift stays invisible until a model makes a malformed
call.

**Consequences.** Return types must be expressible as a schema. Verified by
`test_schemas_are_derived_from_type_hints`.

---

## D-007 — Two error channels, with an explicit routing rule

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** The SDK offers `ToolError` (returns `is_error=True` with the message in the
tool result, which the **model reads**) and `MCPError` (fails the JSON-RPC request; the
model sees nothing).

**Decision.** Route on one question: *could a better model choice have avoided this?*
Yes → `ToolError`. No → `MCPError`. The rule is implemented once, in
`gtm_mcp.errors.to_mcp_exception`.

**Why.** "Contact not found" is actionable — the agent can retry with a different
identifier. "Database unreachable" is not; showing it to the model invites pointless retries
against a dead dependency.

**Consequences.** Domain code raises `GTMError` subclasses and stays free of protocol
concerns. Tools translate at the boundary. Covered by `tests/unit/test_errors.py`.

---

## D-008 — No delete capability at any layer

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** The `CrmRepository` port exposes no delete method, and `AuditOperation` has no
`DELETE` member. Both writes are upserts.

**Why.** An agent acting on ambiguous instructions is most dangerous when it can destroy
data. Omitting the capability from the interface means no tool can reach for it — a stronger
guarantee than a policy in a prompt or a claim in a README, neither of which is enforced by
anything.

**Consequences.** Removing a record from a list becomes a future additive operation (a
membership status change), not a delete. Covered by
`test_delete_is_not_an_expressible_operation` and
`test_no_tool_name_implies_a_destructive_operation`.

---

## D-009 — `WriteResult.success` is computed, not assignable

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** `success` is a `@computed_field` derived from `outcome`; the model is frozen
and forbids extra fields. `DRY_RUN` is not a success.

**Why.** The requirement "never claim success when the operation failed" becomes enforceable
structurally instead of by discipline. There is no field a call site could set to `True`
after a failure, so the failure mode cannot be reintroduced by a careless edit later.

**Consequences.** Covered by `tests/unit/test_results.py`.

---

## D-010 — Seeded PostgreSQL mock CRM behind a repository port

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** The reference CRM is a seeded PostgreSQL schema reached through
`CrmRepository`. A real CRM (HubSpot, Salesforce) becomes an additional adapter.

**Why.** A demonstration must be reproducible by anyone who clones the repository, which
rules out depending on a paid CRM tenant. PostgreSQL rather than SQLite because the
persistence layer should be exercised against the engine a real deployment would use —
dialect, transactional semantics and JSON support included.

**Consequences.** Integration tests need Docker; they skip cleanly without it.

---

## D-011 — Audit sink behind a Protocol; log sink now, database sink next

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Write paths depend on the `AuditSink` protocol. `LoggingAuditSink` is the
initial implementation; a Postgres-backed sink lands with the CRM phase.

**Why.** Lets the write path be built and tested against a real audit contract before the
audit table exists, with no change to calling code when it does.

**Consequences.** The current sink is *not* sufficient on its own for a compliance story.
That is tracked as a known limitation in `PROJECT_STATUS.md` rather than glossed over in the
README.

---

## D-012 — Reuse `httpx2` rather than adding `httpx`

**Date:** 2026-09-06 · **Status:** Accepted

**Decision.** Outbound enrichment calls will use `httpx2`, which SDK v2 already depends on.

**Why.** v2 replaced `httpx`/`httpx-sse` with `httpx2`. Adding `httpx` back would ship two
HTTP stacks with separate connection pools, timeout defaults and CVE surfaces for no
benefit; `httpx2` is API-compatible.

---

## D-013 — Enrichment provider selection deferred

**Date:** 2026-09-06 · **Status:** Superseded by D-017

**Decision.** No enrichment vendor is chosen yet. `CompanyEnrichmentProvider` and
`ContactEnrichmentProvider` define the boundary; selection happens after a survey of current
pricing, free-tier limits, rate limits, licensing terms and data coverage.

**Why.** Provider choice is the decision most often made badly by copying a tutorial. The
canonical `Company` and `Contact` models cover fields that every serious B2B provider
returns, so the choice can be made late without reshaping the domain.

**Consequences.** The server starts and serves read-only tools with no third-party
credential configured.

---

## D-014 — Contact identity strategy: email primary, provider key secondary, no name-company coalescing

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Upsert idempotency requires identifying whether an incoming contact matches an
existing CRM record. Earlier notes contemplated using name + company as a fallback natural key.

**Decision.**
1. Normalized, lowercased `email` is the primary and strongest natural identity when available.
2. `(provider_name, provider_contact_id)` serves as secondary natural identity when email is
   unavailable (e.g. enrichment vendor leads).
3. If neither exists, the record must not be falsely coalesced based on `name + company`.
   Instead, it is inserted as a distinct record.

**Why.** Common names (e.g., "Alex Chen" or "Sarah Smith") are frequent within large enterprise
accounts (e.g., Google, Amazon, Microsoft). Coalescing on name and company creates severe data
corruption by merging distinct individuals into one record. Enforcing partial unique indexes
in PostgreSQL (`WHERE email IS NOT NULL` and `WHERE provider_name IS NOT NULL AND provider_contact_id IS NOT NULL`)
guarantees natural identity uniqueness while allowing multiple email-less contacts to exist safely.

**Consequences.** Implemented in `PostgresCrmRepository.upsert_contact` and covered by
`test_contact_identity_rule_does_not_coalesce_without_email`.

---

## D-015 — CRM-vs-enrichment conflict policy: CRM is authoritative

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** When external enrichment runs against an existing CRM record, fields may conflict
(e.g., a changed job title or phone number).

**Decision.**
1. CRM-curated data (`RecordSource.CRM`) is authoritative over external enrichment data
   (`RecordSource.ENRICHMENT`). Enrichment may populate empty (`None`/`NULL`) fields or update
   enrichment-owned fields, but must never silently overwrite curated CRM fields.
2. Incoming writes must never overwrite a populated field with `None`/`NULL`.

**Why.** Human sales reps and RevOps operators routinely verify prospect details directly. External
enrichment APIs often lag by months or return generalized scraper titles. Letting an automated
enrichment sync overwrite human-curated data silently destroys business value.

**Consequences.** Implemented in `PostgresCrmRepository.upsert_contact` and covered by
`test_conflict_policy_crm_is_authoritative_over_enrichment` and
`test_never_overwrite_populated_field_with_none`.

---

## D-016 — Database constraints and Alembic migration management

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** The mock CRM runs on PostgreSQL. The write safety invariants established in Phase 1
(no delete, idempotent list membership, audit trail integrity) need durability guarantees.

**Decision.**
1. Manage all schema changes through Alembic async migrations in `alembic/versions`.
2. Enforce list membership uniqueness via `UniqueConstraint("list_id", "contact_id")`.
3. Enforce the non-destructive write invariant at the database engine level via
   `CheckConstraint("operation IN ('upsert', 'list_add')", name="ck_audit_log_no_delete")` on `audit_log`.

**Why.** Code review and application-level checks are necessary but insufficient for data
integrity. Structural constraints enforced by PostgreSQL guarantee that accidental manual queries
or future code bugs cannot delete records or duplicate list memberships.

**Consequences.** Covered by `tests/integration/test_schema_and_migrations.py`.

---

## D-017 — External enrichment provider strategy: Hunter live, offline sample by default

**Date:** 2026-09-06 · **Status:** Accepted · **Supersedes:** D-013 (Deferred)

**Context.** Phase 3 needs a company enrichment source and a contact enrichment source. The
constraint that decided it is not data quality but *reproducibility*: this is a public
portfolio repository, so a reviewer must be able to clone it and exercise both search tools.
A provider whose API is gated behind a sales conversation makes the tools unrunnable for
everyone but the author, and no amount of clean architecture compensates for that.

Evaluated against current official documentation on 2026-09-06.

| Provider | API on free tier | Auth | Company lookup | Contact lookup | Cost per call |
| --- | --- | --- | --- | --- | --- |
| **Hunter** | **Yes** — 50 credits/month, API listed in the free plan | `X-API-KEY` header | `GET /v2/companies/find` (domain) | `GET /v2/email-finder` (name + domain *or* company name) | 0.2 credits company, 1 credit email |
| Apollo | No | `x-api-key` header | `GET /api/v1/organizations/enrich` | `POST /api/v1/people/match` | 1 credit org; 1–9 per person |
| Prospeo | Not documented | `X-KEY` header | `POST /enrich-company` | `POST /enrich-person` | 1 credit matched; 10 with mobile |
| People Data Labs | Yes, 100/month | API key | Company Enrichment | Person Enrichment | 1 credit per successful match |

**Decision.**

1. **Live company provider: Hunter Company Enrichment** (`GET /v2/companies/find`).
2. **Live contact provider: Hunter Email Finder** (`GET /v2/email-finder`).
3. **Default provider: an offline sample dataset shipped in this repository**
   (`GTM_ENRICHMENT_PROVIDER=sample`), with Hunter opt-in via
   `GTM_ENRICHMENT_PROVIDER=hunter` plus `GTM_ENRICHMENT_API_KEY`.

**Why Hunter.**

* Its API is available on the free plan — the only evaluated vendor for which this is
  documented plainly rather than inferred. Apollo's own pricing page states API access is
  offered "on our Custom plans"; its documentation says access "depends on your Apollo plan"
  and that a free account additionally requires registration with a work email address. That
  rules Apollo out as the demonstrable path, whatever the merits of its data.
* Hunter's contact endpoint matches the shape of `search_contact(name, company)` exactly: it
  accepts a person's name plus **either** an employer domain **or** an employer name.
  Hunter's other people endpoint (`/v2/people/find`) requires an email address or a LinkedIn
  handle, which an agent asked to "find Elena at CloudScale" does not have. Apollo's People
  Enrichment takes the same name-and-company inputs but costs 1–9 credits, with the range
  driven by what it happens to find — hard to reason about on a fixed monthly budget.
* Status semantics are documented and unambiguous, which is what makes honest error mapping
  possible: 400 invalid parameters, 401 bad key, 403 rate limit, 404 no record, 429 monthly
  quota, 451 legal block. Note that 403 and 429 are the reverse of the common convention;
  that inversion lives in the adapter and nowhere else.
* Authentication works as a header (`X-API-KEY`), not only as an `api_key` query parameter,
  so the credential never appears in a URL, a log line or a proxy access log.
* A documented **`test-api-key`** returns a fixed dummy response on Email Finder without
  consuming credits, which allowed the adapter to be validated against the real endpoint
  once, at zero cost. Doing so found a genuine bug: the endpoint can return a *different*
  person from the one asked about, and the adapter was composing a record whose `full_name`
  came from the query while its `first_name` and `last_name` came from the response. The
  record now reports whoever the provider actually returned.

**Why an offline dataset is the default.**

A vendor credential cannot be committed, so a repository whose only provider is live is a
repository whose headline feature nobody else can run. The sample adapter is a real
implementation of the same port: it makes both tools work on a fresh clone, keeps the
protocol tests deterministic and network-free, and — because there are now two adapters
behind each port — demonstrates that the boundary holds rather than merely asserting it.

The honesty cost of shipping synthetic data is paid explicitly. Every result carries
`provenance.live`, false for this adapter; the result message states that the data is
synthetic and must not be presented as fact; `server_info` reports the configured provider
and whether enrichment is live; and selecting a live provider *without* a credential fails at
startup rather than silently falling back to sample data under a real provider's name.

**Rejected alternatives.**

* **Apollo** — the richest firmographics of the four, but API access is not on the free plan
  and a free account needs a work-email registration. Unrunnable for a reviewer.
* **Prospeo** — a well-designed API (clear `NO_MATCH`, `INSUFFICIENT_CREDITS` and
  `INVALID_API_KEY` codes; no charge for a miss or for a repeat lookup within 90 days) and
  the strongest second choice. Rejected only because its published rate-limit tiers are
  Starter, Growth and Pro with no documented free tier, so free-tier behaviour could not be
  confirmed from official documentation. Its endpoints were revamped with the old ones
  retired on 2026-03-01 — a reminder of why vendor behaviour is pinned behind an adapter.
* **People Data Labs** — a real free tier, but on it contact fields are returned as
  true/false availability flags rather than values, which makes it useless for the one thing
  `search_contact` exists to do.
* **Clearbit** — no longer a self-serve API; absorbed into HubSpot Breeze Intelligence.

**Consequences.**

* Hunter resolves companies by **domain only**. A name-only company query is refused by the
  adapter with an actionable `ValidationError` rather than turned into a guessed
  `{name}.com`, which would return confident data about a different company. The sample
  adapter *can* resolve names, so the capability difference is visible at the port, handled
  in the adapters, and absent from the service and tool layers.
* Hunter's company payload carries no website URL, so `Company.website` is left unset by that
  adapter. No field is synthesised to fill a gap the provider did not fill.
* Free-tier arithmetic is worth stating plainly: 50 credits per month is roughly 50
  `search_contact` calls (1 credit each) or 250 `search_company` calls (0.2 each). That is a
  demonstration budget, not a production one.

---

## D-018 — Computed fields require a serialisation-mode output schema

**Date:** 2026-09-06 · **Status:** Accepted

**Context.** Found while wiring `search_company`, not by reading documentation. The SDK
derives a tool's output schema from its return model *and then validates the serialised
result against that schema*. Pydantic puts computed fields in the **serialisation** schema
only, while `extra="forbid"` emits `additionalProperties: false` into both. The server
therefore emitted `found` and immediately rejected its own response:
`Additional properties are not allowed ('found' was unexpected)`.

**Decision.** Any model returned from a tool that carries a `@computed_field` sets
`json_schema_mode_override="serialization"` in its `model_config`. Applied to
`CompanyLookup`, `ContactLookup` and — pre-emptively, since it is the same pattern and its
tools land in Phase 4 — `WriteResult`.

**Why.** The alternatives are worse. Dropping `extra="forbid"` would remove
`additionalProperties: false` and hide the mismatch rather than fix it, leaving the model
reading a schema that does not mention a field it will receive. Turning `found` into a
stored field would abandon the structural guarantee that a result cannot claim a match it
does not carry (D-009).

**Consequences.** Covered by `test_output_schemas_are_derived_and_advertise_the_found_flag`.
A future tool returning a model with a computed field and no override fails loudly at call
time rather than silently.
