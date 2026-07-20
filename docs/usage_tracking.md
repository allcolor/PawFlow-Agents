# Usage & Cost Tracking

PawFlow records every LLM call as one event in a persistent SQLite ledger
(`core/usage_ledger.py`, `data/system/usage.db`). The ledger is the single
source of truth for tokens and cost — it replaced the former TokenTracker
(JSON aggregates without a conversation dimension) and CostTracker
(in-memory, lost on restart).

## Event model

Each event carries: timestamp, `user_id`, `conversation_id`, `agent_name`,
`llm_service`, `model`, `provider`, `channel`, token counts
(`tokens_in` / `tokens_out` / `cache_read` / `cache_write`), `duration_ms`,
and `cost_usd`.

Cost is **frozen at write time** using the rates configured on the LLM
service (`cost_per_1m_input` / `cost_per_1m_output`, plus optional
`cost_per_1m_cache_read` / `cost_per_1m_cache_write`; cache defaults are
10% / 125% of the input rate). Changing a service's pricing later never
rewrites history. There is no hardcoded price table: services without
configured rates (e.g. subscription CLI providers) record tokens at $0.

Channels attribute where the tokens went:

| Channel | Source |
|---|---|
| `chat` | normal conversation turn |
| `task` | autonomous task iteration (`::task::` sub-conversation) |
| `subagent` | `delegate` / `flash_delegate` sub-agent run |
| `aggregator_advisor` | `llmAggregator` advisor call |
| `realtime` | LiveKit realtime voice/video session (worker metrics) |
| `system` | internal calls (title generation, ...) |
| `migrated` | synthetic events imported from the legacy `token_usage.json` |

## Query surface

Besides the `/cost` command (per-agent tokens and frozen cost) and the
existing `cost` / `get_cost` / `get_usage` actions, four ledger query
actions power dashboards and exports. All accept `days` (window, default
30), `conversation_id`, `agent`, `llm_service`, `channel`, `model`
filters; non-admin callers are always scoped to their own user, admins may
pass `user: "ALL"` or a specific user id.

| Action | Extra params | Returns |
|---|---|---|
| `usage_summary` | — | aggregate tokens/calls/cost for the filter set |
| `usage_timeseries` | `bucket` (hour/day/month), `group_by` (llm_service/agent_name/model/channel/user_id/conversation_id/provider) | bucketed totals, optionally grouped |
| `usage_top` | `dimension`, `order_by` (cost_usd/tokens_in/tokens_out/calls), `limit` | top-N values of one dimension |
| `usage_export` | `format: csv` for CSV, else JSON | raw events, newest first |

## Migration

On first init the ledger imports the legacy `data/runtime/token_usage.json`
once: each per-user `agent::llm_service` aggregate becomes one synthetic
`migrated` event (day-level and per-model breakdowns of the legacy file
cannot be joined without double counting and are dropped), then the file is
renamed to `token_usage.json.migrated`.
