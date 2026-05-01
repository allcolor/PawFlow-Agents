# PawCode CLI

PawCode is the terminal client for PawFlow. It connects to the same backend and conversations as the web UI and VS Code extension. PawCode is chat-only: relay lifecycle is managed by webchat server resources or the standalone PawFlow Relay client.

## Quick Start

```bash
python -m pawflow_cli --dir .

# Or with an explicit server
PAWFLOW_SERVER=http://localhost:9090 python -m pawflow_cli --dir .
```

PawCode opens the browser for OAuth login when needed, then connects as a conversation client. Start filesystem access separately with `pawflow-relay` or from the webchat resource panel.

## What PawCode Adds

- streaming chat with Rich markdown rendering;
- access to conversations that may already have linked relays;
- the same conversation store as the web UI;
- multi-agent streaming and active-agent status;
- approval dialogs for sensitive actions;
- colored diffs and git-oriented workflows;
- command completion and history search;
- stream-JSON compatibility for Claude-Code-style integrations.

## Common Commands

| Command | Purpose |
|---|---|
| `/new` | Start a new conversation. |
| `/conv` | List conversations. |
| `/resume <id>` | Resume an existing conversation. |
| `/agent list|create|delete|select` | Manage agents. |
| `/msg <agent|ALL> <text>` | Send to a specific agent or all agents. |
| `/btw <agent> <question>` | Ask a side question without interrupting main work. |
| `/stop <agent> [-f]` | Interrupt or force-stop an agent. |
| `/run <command>` | Execute through the conversation's linked relay, if one exists. |
| `/terminal` | Run terminal commands through a linked relay, if available. |
| `/diff` | Show git diff through a linked relay, if available. |
| `/plan <description>` | Enter plan mode. |
| `/call <tool> ...` | Invoke a PawFlow tool directly. |
| `/explore` | Browse files interactively. |
| `/compact` | Compact conversation context. |
| `/model <name>` | Switch model where supported. |
| `/cost` | Show token/cost usage. |

## Shared Conversations

PawCode does not create a separate silo. A conversation can be opened in the web UI, continued in PawCode, and then inspected in VS Code. Events are streamed through the same backend and persisted through the conversation store.

## Stream JSON Mode

PawCode can be used as a stream-JSON frontend for tooling that expects Claude-Code-style input/output:

```bash
echo '{"type":"user","message":{"role":"user","content":"hello"}}' | \
  pawcode --input-format stream-json --output-format stream-json
```

## Relay Behavior

PawCode no longer starts or stops relays. Filesystem, grep/glob, edit, shell, screen, and project graph tools operate through relays already linked to the conversation by the server/webchat or the standalone relay client. See `docs/relay_client.md` for client relay setup.
