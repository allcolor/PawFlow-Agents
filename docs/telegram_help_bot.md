# Public Telegram Help Bot

A dedicated, public Telegram bot that anyone can talk to. Each origin user gets
their own conversation with a pre-existing help agent. The agent has **no relay**
(no filesystem/shell) and a **web-tools-only allowlist** (`web_search`, `fetch`).
Conversations have a sliding TTL and are purged proactively, and every
message→response is bounded by a hard timeout.

The flow is `telegram.telegram_help_bot:1.0.0`. It is built entirely from generic
tasks — `telegramReceiver`, `executeScript` (using the scope-bounded `pawflow`
facade, see [Multi-Client Conversations](multi_client_conversations.md#the-pawflow-facade-in-executescript)),
`telegramSend`, and `cronTrigger` — with no Telegram-specific task type.

## Deploy

Deploy in **USER scope**: the deploying user owns every conversation the bot
creates, and the `pawflow` facade is authorized against that user. The help
agent definition and its skills must already exist in the store; the flow only
passes the association to each new conversation.

The BotFather token is supplied through the `bot_token` parameter (sensitive);
store it as a secret, never inline.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `bot_token` | — | BotFather token for the dedicated bot (sensitive). |
| `agent_runtime_port` | `pawflow_agent.agent_runtime_in` | Shared agent runtime to submit to. |
| `agent_definition` | — | Existing agent definition (the PawFlow help agent). |
| `instance_name` | = `agent_definition` | Agent instance name in each conversation. |
| `llm_service` | — | LLM service id for the agent. |
| `skills` | `""` | Comma-separated existing skill names to associate. |
| `tools` | `web_search,fetch,read` | Comma-separated tool allowlist (custom mode). `read` lets the agent re-read large `fetch` results spilled to the FileStore (server-side, no relay, conversation-scoped). |
| `model` | `""` | Optional model override. |
| `max_depth` | `1` | Sub-agent depth (1 = none). |
| `conv_ttl_seconds` | `3600` | Sliding per-user conversation TTL. |
| `response_timeout_seconds` | `120` | Hard max for message→response; the turn is force-cancelled past it. |
| `allowed_chat_ids` | `""` | Comma-separated Telegram chat ids accepted as source. Empty = any chat. |
| `sweep_schedule` | `*/5 * * * *` | CRON schedule for proactive TTL purge. |

## How it works

```
telegramReceiver ──▶ executeScript (route) ──▶ telegramSend
cronTrigger ──▶ executeScript (sweep)
```

**route** (per message):

1. **Source gate** — if `allowed_chat_ids` is set and `telegram.chat_id` is not
   in it, the message is silently ignored. A specific group id restricts the bot
   to that group and, because a direct message's chat id is the user's own id,
   also excludes DMs. (To receive every group message rather than only mentions,
   disable the bot's group privacy mode in BotFather.)
2. **Per-user conversation** — keyed by the origin `telegram.user_id` (its own
   context, even inside a shared group), found via the `help_bot_user_key`
   extra. Expired conversations are deleted on access. A new one is created via
   `pawflow.create_conversation` with the configured agent/skills/tools, **no
   relays**, and the TTL; the tool allowlist is applied with
   `pawflow.set_tool_filters`. Existing conversations get their TTL re-armed
   (sliding).
3. **Run** — `pawflow.run_agent(..., timeout=response_timeout_seconds)` submits
   to the shared runtime (which queues the message behind any running turn, so
   message→response order holds) and waits. On timeout the turn is
   force-cancelled and the user is told to retry.
4. The reply is sent back to `telegram.chat_id` (the group, as a reply to the
   message). `telegram.user_id` is never forwarded to the runtime, so the
   unlinked-user guard in `AgentLoopTask` does not reject the public bot.

**sweep** (on the CRON schedule): scans the deploying user's conversations and
deletes any help-bot conversation (`help_bot_user_key` set) whose
`_meta_expires_at` has passed — so a user who never returns does not leak a
conversation forever (lazy expiry alone only reaps on the next message).

## Security notes

- The agent runs **relay-less** and **allowlisted** to web tools only; it cannot
  read/write files or run shell commands.
- All conversation/agent operations go through the `pawflow` facade, bounded to
  the deploying user's scope (`core.flow_runtime_access`).
- `allowed_chat_ids` restricts *where* messages are accepted from; the
  `TelegramBotService.allowed_users` setting is a separate, per-user filter and
  is not used here.
