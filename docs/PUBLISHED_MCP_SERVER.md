# Published Conversation MCP Servers

PawFlow can publish one existing conversation and one attached agent as an
authenticated inbound MCP server. This is separate from the MCP Repository:
repository entries are outbound servers consumed by PawFlow, while a published
conversation exposes PawFlow tools to Claude Code, Codex, Gemini CLI/Agy,
OpenCode, JCode, Pi, Hermes, and other MCP clients.

## Publish a conversation

1. Select the conversation in the chat UI.
2. Open **Resources → MCP Repository**.
3. Select **Publish/configure this conversation**.
4. Choose an agent already attached to the conversation and save.
   Select how image-producing tools such as `see` return their results:
   **Native MCP images** sends images to the external client's active model;
   **Text descriptions** uses the published agent's vision-capable LLM or its
   configured delegated `vision_llm_service` and returns text only.
5. Create an API key and copy it immediately. PawFlow stores only its SHA-256
   hash and never displays the raw key again.
6. Copy the endpoint and CLI configuration from the dialog.

Only the conversation owner can configure publication, create or revoke keys,
enable or disable the endpoint, or delete it. The conversation and its normal
agent/relay controls remain visible and usable.

Once published, the MCP Repository section replaces the publish link with a
status row — *Published as MCP — agent `<name>` (`<n>` keys)*, or a disabled
variant when the endpoint is switched off. Selecting the row reopens the same
configuration dialog, so an existing publication can always be reviewed and
edited from its conversation.

Every published tool carries MCP behavior annotations (`readOnlyHint`,
`destructiveHint`, `idempotentHint`, `openWorldHint`). Clients such as ChatGPT
treat unannotated tools as unrestricted write actions and may refuse to invoke
them in restricted surfaces; the context/schema readers are declared read-only,
the two send tools as idempotent non-destructive writes, and `use_tool` as a
potentially destructive open-world action.

The endpoint is:

```text
https://pawflow.example/mcp/<opaque-server-id>
```

Requests require both deployment layers when Private Gateway is configured:

```text
Authorization: Bearer <PAWFLOW_MCP_API_KEY>
X-PawFlow-Gateway-Key: <PAWFLOW_GATEWAY_KEY>
```

The API key selects exactly one published conversation and agent. Each tool call
runs with the conversation owner's identity and that agent's configuration,
through the normal PawFlow approval gate, hooks, secret resolution, redaction,
and tool metrics. The selected agent is a capability profile, not the caller:
the MCP client owns the call and receives its result. Calls and results are
appended to the ordinary conversation transcript as display-only audit rows —
every `tools/call` (context reads and schema discovery included), rendered
like a normal agent's tool calls; they are not injected into the selected
agent's context. `get_initial_context`/`get_context_updates` results are
audited as a compact summary (size and cursor), never the full document.

`delegate` and `flash_delegate` remain asynchronous inside PawFlow, but the
published MCP adapter keeps the originating `tools/call` open until every
delegated task has produced its final result. A client may therefore delegate
to the selected published agent itself. The answer is returned to the MCP
client, not injected into or used to wake the capability-profile agent.

The same ownership rule applies when a running call is detached with PawFlow's
**Background** control. PawFlow stops treating the operation as foreground UI
work, while the originating MCP request remains subscribed to the real late
result. The placeholder is never returned as the final MCP result. Replaying
the same request id in the same MCP session reuses the call and its retained
result instead of starting another operation.

Native image output is the default and does not invoke PawFlow's LLM. In text
description mode, only image-producing tool results invoke the configured
vision service. The call fails explicitly if the published agent has no usable
vision service. PawFlow persists the ordinary text and compact image metadata
in the conversation transcript, never inline image base64 payloads.

## ChatGPT connector (URL-key access)

ChatGPT web's developer-mode apps only support OAuth or "No Authentication";
they cannot send an `Authorization` header. For those clients a publication
can issue **connector keys** (prefix `pfmcc_`), which embed the credential in
the endpoint URL instead:

```text
https://pawflow.example/mcp/<opaque-server-id>/k/<pfmcc-connector-key>
```

Setup:

1. In the publish dialog, use **ChatGPT connector → Create connector key**.
   The full URL (with the embedded key) is shown once; PawFlow stores only
   the key's SHA-256 hash.
2. In ChatGPT, enable **Settings → Security and login → Developer mode**,
   then create a new app from the Plugins page with the connector URL and
   authentication set to **No Authentication**.
3. Copy the **Bootstrap prompt** from the same dialog section and paste it as
   the first message of the ChatGPT conversation. It instructs the remote
   model to load the PawFlow context (`get_initial_context`), keep its cursor
   in sync (`get_context_updates`), persist both sides of the dialogue
   (`send_user_message` / `send_agent_message` with idempotent `message_id`s),
   discover tools lazily (`get_tool_schema` → `use_tool`), and respect the
   one-way limits. The client answers "Ready — PawFlow context loaded" once
   bootstrapped. The prompt is generic: it works for any one-way MCP client,
   not only ChatGPT.

Connector routes are exempt from the Private Gateway challenge (connector
clients cannot send `X-PawFlow-Gateway-Key`); the embedded key is the sole
credential, like a provider callback URL. They are also exempt from the
cross-authority `Origin` rejection (ChatGPT sends `Origin: https://chatgpt.com`;
the DNS-rebinding defense stays on Bearer routes, where the browser is the
threat model), and they tolerate session loss: a connector request with a
missing or stale `Mcp-Session-Id` gets a fresh synthesized session instead of
a 404, because one-way clients replay stale sessions instead of
re-initializing and then surface the 404 as "Resource not found" before
disabling the whole connector. Every published-MCP request is logged at INFO
level (`[published-mcp]`, key redacted) for diagnosis.

Key kinds never cross surfaces: a connector key is only valid as the URL path
segment and is rejected as a Bearer header; a regular API key is only valid as
a Bearer header and is rejected in the path. Revoking a connector key closes
the URL immediately. PawFlow redacts the key segment from its own HTTP logs,
but the URL is still a secret — treat it like an API key, and prefer a tool
allowlist (below) for third-party clients.

This mechanism is transitional: it will be replaced by a spec-compliant OAuth
authorization server (see `CHATGPT_CONNECTOR_PLAN.md`), at which point
connector keys and the `/k/` routes are removed.

## Scheduling tools and one-way clients

`schedule_continuation` and `ScheduleWakeup` promise an autonomous resume,
which needs a return channel to the external client. When the publication has
no registered client terminal, `use_tool` refuses them with an explicit error
instead of scheduling a wake the client could never observe. The refusal
lifts automatically while a client terminal is registered.

Independently, the agent poller never runs an `external_mcp` agent's
scheduled wake through PawFlow's internal LLM loop: the wake prompt is
persisted to the conversation (visible to one-way clients through
`get_context_updates` on their next turn) and injected into the client
terminal when one is available.

## Tool allowlist

Each publication can restrict which PawFlow tools it exposes. The allowlist is
edited in the publish dialog (comma-separated tool names; empty means every
tool) and stored per publication. It applies to both Bearer and connector
traffic: `get_tool_schema` lists and describes only allowlisted tools, and
`use_tool` refuses excluded tools with an explicit error. The conversation
transport tools below are not subject to the allowlist; the read-only
exposure modes are the only thing that removes any of them.

## Exposure modes

Each publication has one of four exposure modes, chosen in the publish dialog
and stored per publication:

| Mode | Advertised surface |
|---|---|
| `api` | The six meta tools; every PawFlow tool is reached through the `use_tool` gateway. |
| `full` | The four conversation transport tools plus every PawFlow tool as a first-class MCP tool with its real behavior annotations; the `use_tool`/`get_tool_schema` shims are dropped. |
| `api_readonly` | The `api` surface minus `send_user_message`/`send_agent_message`; `get_tool_schema` lists and `use_tool` executes only read-only tools, and `use_tool` is honestly annotated `readOnlyHint: true`. |
| `full_readonly` | The `full` surface reduced to `get_initial_context`, `get_context_updates`, and the read-only PawFlow tools. |

Read-only status comes from `ToolApprovalGate.READ_ONLY_ALLOWED`, the same
source of truth as tool approval. The read-only modes enforce the restriction
at execution time as well as at `tools/list` time: a direct or gateway call to
a write tool — the messaging transport tools included — returns an explicit
error and never reaches the runtime.

The read-only modes exist because some clients (ChatGPT plans that gate write
actions) disable the entire connector for a conversation the moment the model
merely *attempts* a write-annotated tool, which kills the read tools too. A
read-only publication never advertises a write tool, so the client model can
never trigger that shutdown. The allowlist composes with every mode: in the
read-only modes a tool must be both allowlisted and read-only to appear.

The `get_initial_context` Bootstrap Contract and the one-way connector prompt
are mode-aware: writable publications instruct the client to persist messages
with `send_user_message`/`send_agent_message`, while read-only publications
instruct it to never attempt message persistence and to use only the
advertised tools — an instruction to call an unexposed write tool is exactly
what would make a restricted client attempt one.

## Conversation transport tools

Published servers expose four direct conversation tools in addition to
`get_tool_schema` and `use_tool`:

| Tool | Contract |
|---|---|
| `get_initial_context` | Returns the full agent-visible bootstrap document and its current `seq` cursor. |
| `get_context_updates` | Accepts `after_seq` and returns only later agent-visible messages plus the new cursor. |
| `send_user_message` | Canonically persists a local client prompt with a required `message_id`. |
| `send_agent_message` | Canonically persists the external agent's response with a required `message_id`. |

Both write tools are durably idempotent: the duplicate check and append occur
under the conversation lock. A retry with the same `message_id` reports the
existing write and does not publish a second webchat event.

The release launcher wires these tools to client lifecycle hooks. Each client
keeps a locked local `seq` cursor. Server-injected prompts carry a short-lived
`message_id`/SHA-256 marker written before terminal submission, so the prompt
hook injects other context updates while excluding and not repersisting the
prompt already stored by webchat ingress.

## Local stdio bridge

For client workstations, download `pawflow-mcp-client-VERSION.zip` or
`pawflow-mcp-client-VERSION.tar.gz` from the release. Its guided Windows,
Linux, and macOS installer configures Claude Code, Codex, Agy, OpenCode, JCode,
Pi, and Hermes, stores both keys in one private local profile, and writes
secret-free session entries and plugins.
See [PawFlow MCP Client Installer](MCP_CLIENT_INSTALLER.md).

Developers with a full PawFlow checkout can also use the `pawflow-mcp`
console command directly. The bridge proxies stdio JSON-RPC to the Streamable
HTTP endpoint and shares the CLI's selected project directory through a normal
PawFlow relay.

Common environment variables:

| Variable | Required | Purpose |
|---|---:|---|
| `PAWFLOW_MCP_URL` | Yes, unless `--url` is used | Published Streamable HTTP endpoint |
| `PAWFLOW_MCP_API_KEY` | Yes | One-time-created published-server API key |
| `PAWFLOW_GATEWAY_KEY` | When Private Gateway is configured | Deployment gateway authentication |
| `PAWFLOW_RELAY_DIR` | No | Directory to share; defaults to the current directory |
| `PAWFLOW_MCP_CLIENT_NAME` | No | Human-readable CLI name |
| `PAWFLOW_RELAY_INSECURE=1` | Development only | Disable TLS verification |

Optional bridge flags are `--relay-dir`, `--client-name`, `--readonly`,
`--allow-exec`, and `--allow-service-tunnels`. The last flag enables the
existing explicitly-approved FRP service-tunnel capability on the automatic
MCP relay.

### Manual Claude Code, Gemini CLI, and Agy configuration

Use the standard JSON MCP server shape:

```json
{
  "mcpServers": {
    "pawflow": {
      "command": "pawflow-mcp",
      "args": [
        "--url",
        "https://pawflow.example/mcp/srv_example"
      ],
      "env": {
        "PAWFLOW_MCP_API_KEY": "pfmcp_example",
        "PAWFLOW_GATEWAY_KEY": "private-gateway-key"
      }
    }
  }
}
```

### Manual Codex configuration

Use the equivalent TOML entry:

```toml
[mcp_servers.pawflow]
command = "pawflow-mcp"
args = ["--url", "https://pawflow.example/mcp/srv_example"]

[mcp_servers.pawflow.env]
PAWFLOW_MCP_API_KEY = "pfmcp_example"
PAWFLOW_GATEWAY_KEY = "private-gateway-key"
```

These examples are for advanced manual setup. Keep both keys out of
source-controlled project configuration. The release installer is preferred
because its client entries contain no raw key.

## Relay lifecycle

The bridge registers its local directory and claims the conversation's CLI
lease at startup. It fails closed if either operation is rejected. It also
exposes four local MCP tools:

- `pawflow_relay_connect`
- `pawflow_relay_disconnect`
- `pawflow_relay_status`
- `pawflow_relay_reconnect`

The local bridge chooses the directory. The PawFlow server never selects or
widens the host path.

`pawflow_relay_disconnect` removes only the workspace relay; the bridge keeps
its logical CLI lease and heartbeat, so another CLI cannot start against the
same publication. `pawflow_relay_reconnect` reuses that lease. Closing the
bridge releases it.

The same lease owns an optional private terminal registration: session ID,
terminal kind, target, injection secret, and marker path. Public status returns
only readiness, session ID, and kind; it never exposes the target, secret, or
local marker path. POSIX launchers register a `tmux` pane, while Windows
launchers register an authenticated loopback console injector. Disconnect,
lease release, expiry, publication reconfiguration, and client replacement all
clear terminal routing.

Webchat routing reuses the already-connected automatic relay and sends the
internal `mcp_terminal_inject` action only after the canonical user row has been
authorized, hooked, and persisted. This internal action is independent of the
relay's generic shell-execution permission: it can address only the terminal
target registered by the authenticated active client lease. If a published
external agent has no reachable terminal, PawFlow returns HTTP 503 instead of
starting an internal LLM for that agent.

A published server permits one active CLI instance. Starting another instance
while the first lease is fresh returns HTTP 409; use another published
conversation for another concurrent client. The bridge heartbeats every 30
seconds. PawFlow expires the lease after 120 seconds and removes the temporary
service and agent binding after a crash or lost connection.

The CLI relay is linked to the published agent with `auto_default=false`.
Registration never changes the relay selected by the user. Filesystem tool
resolution remains:

1. explicit `relay=<relay-id>` in the call;
2. agent default relay;
3. conversation default relay;
4. the only linked relay when exactly one is available;
5. ambiguity error.

Disconnecting the CLI removes its temporary relay service and binding. Disabling
or deleting the publication does the same.

## Protocol surface

The Streamable HTTP endpoint advertises six MCP tools:

- `get_tool_schema`: list PawFlow tools/families, return one full schema, or
  compare an availability-filtered routing family;
- `use_tool`: execute a named PawFlow tool with `arguments_json`.
- `get_initial_context` and `get_context_updates`: bootstrap and cursor-based
  agent-visible synchronization;
- `send_user_message` and `send_agent_message`: idempotent conversation writes.

The wrapper keeps the MCP tool list small while exposing the exact set available
to the bound conversation and agent. MCP sessions expire after eight hours of
inactivity. Server-initiated SSE is not currently supported; asynchronous and
background results are completed through their original `tools/call` response.

## Delegate and A2A turns into an external MCP agent

An agent configured with `runtime_kind: "external_mcp"` can be the target of a
webchat turn, a same-conversation `delegate`, a cross-conversation delegate, or
an inbound A2A request. PawFlow persists the canonical request first and injects
it into the registered terminal with that request's `msg_id`. The client hook
retains the marker and supplies it as `reply_to_message_id` when it calls
`send_agent_message` with the final response.

That correlation completes the original PawFlow runtime turn. A2A tasks receive
their terminal `done` event, ordinary delegates deliver the private result back
to their caller, and delegates started by a published MCP `tools/call` complete
that still-open call instead of waking the capability-profile agent. Response
`message_id` values remain the idempotency boundary, so a retried write does not
append or broadcast a second assistant message.

Inbound A2A publication of an `external_mcp` agent requires
`context_policy: "shared"`. An isolated A2A context has a different internal
conversation ID and therefore cannot safely reuse the terminal and context feed
bound to the published conversation; PawFlow rejects that configuration at
submission rather than routing it to the wrong context.
