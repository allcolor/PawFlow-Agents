# LLM Providers

PawFlow can run agents through both direct API providers and CLI-backed coding agents. Providers are selected per agent through the `llm_service` field, so different agents in the same conversation can use different backends.

## Provider Types

| Provider | Mode | Best for | Notes |
|---|---|---|---|
| `openai` | Direct API | General agents, JSON mode, OpenAI-compatible endpoints | Supports `base_url` for local/self-hosted or compatible APIs. |
| `anthropic` | Direct API | Claude API agents, vision, extended thinking | Uses Anthropic messages/tool APIs. |
| Claude Code | CLI subprocess/container | Coding agents with Claude Code session semantics | Uses MCP/tool bridge and can run containerized. |
| Codex CLI | CLI subprocess/container | Coding agents using Codex sessions | Uses per-user/conversation session state and a Codex container pool. |
| Gemini CLI | CLI subprocess/container | Gemini-backed coding agents | Uses Gemini CLI session state and streaming. |

The direct API providers are normal HTTP clients. The CLI providers launch and manage a provider CLI process, keep session metadata, and route tools through PawFlow's relay/MCP bridge where applicable.

## Agent Configuration

Agents reference an LLM service by id:

```json
{
  "name": "coder",
  "prompt": "You are a pragmatic coding agent.",
  "llm_service": "codex_llm_service",
  "model": "",
  "tools": [],
  "max_depth": 2
}
```

The service id can also be resolved through the expression cascade:

```json
{
  "llm_service": "${llm_default_service}"
}
```

Resolution order is flow -> conversation -> user -> global -> environment.

## OpenAI-Compatible Endpoints

For OpenAI-compatible providers, configure `base_url` on the LLM service. This is the preferred path for local/self-hosted model servers such as vLLM, LM Studio, Ollama-compatible gateways, or commercial APIs that expose an OpenAI-compatible surface.

```json
{
  "type": "llmConnection",
  "provider": "openai",
  "api_key": "${OPENAI_API_KEY}",
  "base_url": "http://localhost:8000/v1",
  "model": "local-model"
}
```

## CLI Provider Sessions

CLI-backed providers have extra lifecycle concerns:

- session directories are keyed by user, conversation, and agent;
- invalidating a conversation clears stale Claude Code, Codex, and Gemini resume pointers;
- live Codex/Gemini/Claude containers can be evicted when an agent or conversation is reset;
- container pools enforce CPU, memory, idle timeout, and max active container settings.

Relevant implementation areas:

- `core/llm_providers/claude_code.py`
- `core/llm_providers/codex.py`
- `core/llm_providers/gemini.py`
- `core/claude_code_pool.py`
- `core/codex_pool.py`
- `core/gemini_pool.py`

## Tooling Differences

| Capability | Direct API providers | CLI providers |
|---|---|---|
| Tool calls | Native tool/function calling through PawFlow | Provider CLI + PawFlow bridge/MCP where applicable |
| Conversation state | PawFlow builds context | Provider CLI may keep/resume its own session |
| Preemption | Messages are queued until turn completion | Some CLI providers can receive injected/preemptive messages |
| Containerization | Not needed | Recommended for isolation and reproducibility |
| Per-agent model switch | Service/model config | Provider CLI args/config |

## Documentation Checklist For New Providers

When adding a provider, document:

1. service type and required secrets;
2. supported model names and default model;
3. whether it is direct API or CLI-backed;
4. streaming support;
5. tool calling support;
6. vision/file support;
7. session persistence behavior;
8. container requirements;
9. cost tracking behavior;
10. known limitations.
