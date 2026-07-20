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
configured rates record tokens at $0.

### Subscription services

Set `subscription: true` on a flat-rate `llmConnection` (a Claude Code,
Codex, or Gemini subscription login) together with API-equivalent
`cost_per_1m_*` rates. Usage from that service is recorded as
`virtual_cost_usd` instead of `cost_usd` — the real cost stays $0 (budgets
and `max_budget_usd` never count it), while dashboards can still show what
the tokens would have cost via the API, i.e. what the subscription saved.
Every `summary` / `timeseries` / `top` / `conversation_breakdown` row
carries both `cost_usd` and `virtual_cost_usd`.

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
| `usage_conversation` | `conversation_id` (required) | totals + by_agent / by_channel / by_model + recent turns for one conversation, task sub-conversations (`<cid>::task::<tid>`) included |
| `usage_dashboard` | `days`, `bucket`, `group_by` | bundled payload for the global dashboard screen: KPIs (today/7d/30d/window), a timeseries, and top conversations/agents |

## Global Usage & Cost dashboard

"Usage & Costs" in the header action menu (`tasks/io/chat_ui/usage_dashboard.js`)
is a full-panel screen backed by the `usage_dashboard` action: KPI cards
(today / 7d / 30d, tokens, cache-hit rate, a 30-day projection from the
last 7 days' daily average), a stacked daily bar chart (canvas, no external
charting dependency) that can stack on LLM service / agent / model /
channel, and top-10 conversations and agents by cost for the selected
window. Bars and KPIs fall back to tokens when the window has no priced
usage (e.g. subscription-only traffic). Admins get an "All users" toggle;
non-admins are always scoped to their own usage regardless of what they
pass.

## Spend budgets (`core/budget_store.py`)

Cumulative daily/monthly caps, distinct from the per-agent-loop-invocation
`max_budget_usd` LLM-service parameter (a single-turn safety cap, see
`tasks/ai/_alc_base.py`). A budget is scoped to `global`, `user`,
`conversation` (its task sub-conversations included), `agent`, or
`llm_service`, with a `period` (`daily`/`monthly`), a `limit_usd`, and a
`policy`:

- `warn` — never blocks; only drives the threshold notifications below.
- `block` — once period-to-date REAL spend (virtual/subscription cost is
  never counted) reaches the limit, the NEXT external agent turn on that
  scope raises a `Budget exceeded: ...` fatal error (checked once per turn,
  before the LLM call — the turn that crosses the line still completes,
  since its cost is only known once it's done).

At 50/80/100% of the limit, a system notification is posted into the
conversation that triggered the crossing (bell row + toast, same mechanism
as the `PushNotification` tool), deduplicated per (budget, period) so it
fires once per threshold per period. This runs from every ledger write
(`UsageLedger.record`), across all channels — not just `chat`.

Managed from the dashboard's "Budgets" section (progress bars; add/delete
form for admins) or directly via actions: `budget_list` (everyone — non-
admins only ever see budgets that apply to them: `global` or their own
`user` scope), `budget_create` / `budget_update` / `budget_delete`
(admin role required — a budget is a cross-user enforcement rule, never a
self-service limit a user could raise on themselves).

## Live conversation cost gauge

After every turn the server publishes a `usage.updated` SSE event on the
conversation (task sub-conversations publish to their parent) with the
turn's cost/tokens and the conversation totals. The webchat header shows a
cost badge (`usage_cost.js`) hydrated from `usage_conversation` on
conversation open and refreshed live from `usage.updated`; clicking it
opens a breakdown panel (totals, by agent/channel/model, recent turns).
When a conversation only uses services without configured pricing (e.g.
subscription CLI providers), bars scale on tokens instead of cost.

## Migration

On first init the ledger imports the legacy `data/runtime/token_usage.json`
once: each per-user `agent::llm_service` aggregate becomes one synthetic
`migrated` event (day-level and per-model breakdowns of the legacy file
cannot be joined without double counting and are dropped), then the file is
renamed to `token_usage.json.migrated`.
