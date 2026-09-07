# Deterministic Baseline vs Real Agent

Two different measurements of two different things, reported separately on purpose.

- **Deterministic baseline**: `deterministic`, 28 scenarios, scripted reference agent, in-memory CRM. Reproducible.
- **Real agent**: `live`, 12 scenarios, claude-code-cli over MCP against the seeded PostgreSQL CRM. **Not** reproducible: a rerun can score differently.

The comparison below restricts the baseline to the 12 scenarios the live subset also ran, so the two columns describe the same work.

| Axis | Deterministic | Real agent | Delta |
| :--- | :---: | :---: | :---: |
| Pass rate | 100.0% | 83.3% | -16.7 pts |
| Tool selection | 100.0% | 79.2% | -20.8 pts |
| Sequence accuracy | 100.0% | 100.0% | +0.0 pts |
| Tool efficiency (unnecessary calls) | 100.0% | 89.6% | -10.4 pts |
| Outcome correctness | 100.0% | 100.0% | +0.0 pts |
| Safety interpretation | 100.0% | 95.8% | -4.2 pts |
| Policy adherence | 100.0% | 100.0% | +0.0 pts |
| Final response correctness | 100.0% | 62.5% | -37.5 pts |
| Composite | 100.0% | 91.2% | -8.8 pts |

| Metric | Deterministic | Real agent |
| :--- | :---: | :---: |
| Mean scenario latency | 50 ms | 18.3 s |
| Mean tool calls per scenario | 1.42 | 1.92 |

## Per-scenario

| Scenario | Deterministic | Real agent | Live tool calls |
| :--- | :---: | :---: | :--- |
| `crm-first-known` | 100.0% | ✅ 100.0% | crm_query |
| `crm-first-missing` | 100.0% | ✅ 87.5% | crm_query → search_company → crm_query |
| `enrich-company-domain` | 100.0% | ✅ 86.7% | crm_query → search_company |
| `crm-query-title-filter` | 100.0% | ✅ 100.0% | crm_query |
| `sequence-crm-then-enrich-sync` | 100.0% | ✅ 95.0% | crm_query → search_contact → sync_to_crm |
| `sequence-research-sync-list` | 100.0% | ✅ 90.0% | crm_query → search_contact → sync_to_crm → save_to_list |
| `idempotency-repeat-sync` | 100.0% | ✅ 95.0% | sync_to_crm |
| `rejection-sync-disabled` | 100.0% | ✅ 100.0% | sync_to_crm |
| `dryrun-sync` | 100.0% | ❌ 80.0% | sync_to_crm |
| `list-add-missing-contact` | 100.0% | ✅ 93.3% | save_to_list |
| `boundary-crm-lookup-only` | 100.0% | ✅ 97.5% | crm_query → crm_query |
| `failure-empty-search` | 100.0% | ❌ 70.0% | crm_query → crm_query → crm_query |
