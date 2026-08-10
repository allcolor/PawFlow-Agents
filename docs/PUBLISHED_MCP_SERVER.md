# Published Conversation MCP Servers

PawFlow can publish one existing conversation and one attached agent as an
authenticated inbound MCP server. This is separate from the MCP Repository:
repository entries are outbound servers consumed by PawFlow, while a published
conversation exposes PawFlow tools to Claude Code, Codex, Gemini CLI/Agy, and
other MCP clients.

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
and tool metrics. Calls and results are appended to the ordinary conversation
transcript; no LLM turn is started.

Native image output is the default and does not invoke PawFlow's LLM. In text
description mode, only image-producing tool results invoke the configured
vision service. The call fails explicitly if the published agent has no usable
vision service. PawFlow persists the ordinary text and compact image metadata
in the conversation transcript, never inline image base64 payloads.

## Local stdio bridge

For client workstations, download `pawflow-mcp-client-VERSION.zip` or
`pawflow-mcp-client-VERSION.tar.gz` from the release. Its guided Windows,
Linux, and macOS installer configures Claude Code, Codex, and Agy, stores both
keys in one private local profile, and writes secret-free user MCP entries.
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

Optional bridge flags are `--relay-dir`, `--client-name`, `--readonly`, and
`--allow-exec`.

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

The Streamable HTTP endpoint currently advertises two MCP tools:

- `get_tool_schema`: list PawFlow tools or return one full schema;
- `use_tool`: execute a named PawFlow tool with `arguments_json`.

The wrapper keeps the MCP tool list small while exposing the exact set available
to the bound conversation and agent. MCP sessions expire after eight hours of
inactivity. Server-initiated SSE is not currently supported.
