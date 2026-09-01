# LLM Tool Exposure Modes — choose how an agent reaches its tools

Status: **implemented** (2026-08-22) — see the delivery notes at the end for
what changed against this plan while building it.

Today every agent sees exactly two tools and reaches everything else through
them. MCP publications already offer four surfaces for the same registry. This
plan gives the LLM service the same choice, with today's behaviour as the
default.

## Where we are today

`tasks/ai/_agentctx_p3.py:450` — the comment says it outright:

```python
# Always expose only 2 meta-tools: get_tool_schema + use_tool.
st._gts = GetToolSchemaHandler(st.registry)
st._ut = UseToolHandler(st.registry)
st.tool_defs = [LLMToolDefinition(...), LLMToolDefinition(...)]
```

There is no setting. Every provider, every agent, one surface.

MCP publications, by contrast, already implement four
(`services/mcp_server_endpoint.py:416`):

| Mode | Surface |
|---|---|
| `api` | the meta tools; everything reached via `use_tool` |
| `full` | every tool declared natively, with real behaviour annotations |
| `api_readonly` | meta tools, gateway executes read-only tools only |
| `full_readonly` | native declarations restricted to read-only tools |

plus a `tool_allowlist` (`_publication_allowlist`, line 367) that narrows any
of them.

## Target

**Decisions (user, 2026-08-22):** mirror the MCP agent publications exactly —
same four modes, same `tool_allowlist`, same read-only semantics — and make the
setting available at **both** levels: a default on the LLM service and a
per-agent override.

A `tool_exposure` field taking the same four values, defaulting to `api`. The
resolution of *which* tools exist is unchanged; only how they are advertised
to the model changes.

### Mirroring the MCP side, exactly

`core/mcp_server_store.py:26-39` is the reference and must not be re-specified
here — the same constants get imported, not retyped:

- `_MODES = {"api", "full", "api_readonly", "full_readonly"}` (line 39), with
  the documented meaning of each mode kept verbatim.
- `tool_allowlist`: stored as a JSON array in a text column, parsed to a list
  of non-empty strings, **empty means every tool** (`mcp_server_store.py:265`
  and `_publication_allowlist`, `mcp_server_endpoint.py:367`). Included in this
  change, not deferred — open question 2 is closed.
- Read-only means `ToolApprovalGate.is_read_only_allowed`, the existing single
  definition (`mcp_server_endpoint.py:430`), and in a `*_readonly` mode a write
  tool is **neither advertised nor executable** — the gateway refuses it too
  (`mcp_server_endpoint.py:392`). Whatever that predicate already says about
  memory writes and todolist updates is the answer for agents as well; this
  change introduces no second opinion. Open question 3 is closed.

Note that MCP publications are already keyed per agent instance —
`UNIQUE(conversation_id, agent_name)` (`mcp_server_store.py:100`). The agent
level is therefore the one that matches MCP one-for-one; the LLM-service level
is the addition.

### Two levels, one resolution rule

| Level | Field | Meaning |
|---|---|---|
| `llmConnection` | `tool_exposure` | default for every agent on this service |
| agent | `tool_exposure` | override; empty/unset = inherit the service |

The same pairing applies to `tool_allowlist`. Resolution is a single helper so
the precedence exists in exactly one place: **agent value if set, else service
value, else `api` / empty allowlist**. No merging of allowlists between the two
levels — an override replaces, it does not intersect. Merging would make the
effective surface impossible to read off either screen.

Reuse rather than reimplement:

- `_publication_mode` / `_is_full_mode` / `_is_readonly_mode` semantics, lifted
  out of `services/mcp_server_endpoint.py` into a shared helper so the two
  surfaces cannot drift apart. The MCP endpoint keeps its `mode` key; both call
  the same normaliser.
- `ToolApprovalGate.is_read_only_allowed` — already the single definition of
  "read-only tool" (`mcp_server_endpoint.py:430`).
- The existing registry and per-agent gating decide the candidate set before
  the mode filters it.

## The part that is easy to miss

The prompt contradicts itself if only `tool_defs` changes. Two hardcoded blocks
tell the model that the meta tools are the only way in:

- `tasks/ai/_agentctx_p3.py:426` — *"CRITICAL TOOL RULES: You MUST ONLY use MCP
  tools from the 'pawflow' server: mcp__pawflow__get_tool_schema and
  mcp__pawflow__use_tool… NEVER use built-in tools"*.
- `core/llm_providers/cli_shared.py:602`, in the Bootstrap Contract — *"Prefer
  get_tool_schema/use_tool and do not switch to native provider tools"*.

In `full` mode both are false, and the second one actively tells a CLI agent to
ignore the tools it was just handed. Both must become mode-dependent text. The
distinction they are really making — *PawFlow tools, not the CLI's own built-ins
that hit the wrong filesystem* — still holds in every mode and must survive the
rewording.

## Cost, and why `api` stays the default

`full` mode declares every tool schema on every request. That is the reason the
gateway exists: the registry is large, and the declarations sit in the cached
prefix, so the trade is prompt tokens against a round trip per unknown tool.
`api` stays the default and the docs should say plainly that `full` is for
small allowlists or models that handle wide tool arrays well.

This also interacts with `core/tool_selection.py:236`
(`build_tool_selection_hint`), which exists to help a model *choose* a tool to
look up. In `full` mode that hint is redundant and should be dropped rather
than shipped alongside native declarations.

## Work packages

| WP | Content | Verification |
|---|---|---|
| WP0 | Extract the mode normaliser + read-only predicate into a shared module; point `mcp_server_endpoint` at it | existing MCP publication tests stay green |
| WP1 | `tool_exposure` + `tool_allowlist` on `llmConnection` (default `api` / empty) and on the agent, plus the resolution helper | schema/validation tests + precedence matrix (agent set / unset × service set / unset) |
| WP2 | Build `st.tool_defs` from the resolved mode at `_agentctx_p3.py:450` | test per mode: declared tool names, allowlist filtering, read-only filtering |
| WP3 | Mode-dependent prompt text in `_agentctx_p3.py:426` and `cli_shared.py:602`; drop the selection hint in `full` | assert the prompt never claims meta-only while declaring native tools |
| WP4 | Docs (`docs/AGENT_SYSTEM.md`, service reference) + CHANGELOG | `tests/test_docs_version_consistency.py` |

## Open questions

All three are settled (user, 2026-08-22):

1. ~~Where does the setting live?~~ **Both** — service default, per-agent
   override.
2. ~~`tool_allowlist` now or later?~~ **Now**, identical to the MCP side.
3. ~~What does read-only mean for an agent?~~ **Exactly what it means for MCP**:
   `ToolApprovalGate.is_read_only_allowed`, no separate policy.

One consequence worth flagging rather than deciding silently: the MCP
`*_readonly` modes also drop the two messaging meta tools via `_WRITE_META`
(`mcp_server_endpoint.py:413`). Those are MCP-client concepts and have no agent
equivalent, so they simply do not appear on this side — the read-only filter
for an agent is the `is_read_only_allowed` predicate alone. Flagging it because
it is the one place where "exactly like MCP" cannot be literal.

## Delivery notes (2026-08-22)

Two things turned out differently once the code was in front of me. Both are
deliberate departures from the plan above, not oversights.

### The allowlist already existed — no second one was added

The plan said to mirror the MCP `tool_allowlist`. Agents already have a richer
equivalent: `core/tool_mcp_filters.py` holds a conversation-level
`disabled_tools` plus a per-agent override (a custom selection under
`agent_overrides`), already editable from the UI. Adding a second allowlist
would have created two sources of truth for the same question — a bug factory
rather than parity with MCP.

So what shipped is the **mode** at both levels; *which* tools exist stays with
`tool_mcp_filters`, and the read-only filter composes on top of whatever that
already allowed. MCP publications keep their own `tool_allowlist` because they
have no access to conversation filters.

### CLI providers use the same four modes

`tools/mcp_bridge.py` obtains its exact `tools/list` surface from
`ToolRelayService`. `api` modes expose the two meta tools; `full` modes expose
the agent-filtered tools and their complete schemas directly. The relay resolves
the agent override over the linked LLM-service default, carries `agent_name`
through discovery and schema requests, and enforces readonly again at execution
so a forged call cannot bypass the advertised surface. This single path covers
Codex, Claude Code, Gemini/Antigravity, and every other CLI using the bridge.

The shared CLI prompts describe both possible shapes and tell the model to
follow the tools actually advertised by the `pawflow` MCP server.

### What shipped

| Piece | Location |
|---|---|
| Shared vocabulary | `core/tool_exposure.py` — `MODES`, `normalize_mode`, `resolve_mode`, `filter_read_only` |
| MCP consumers repointed at it | `core/mcp_server_store.py`, `services/mcp_server_endpoint.py` |
| Service default | `llmConnection.tool_exposure` |
| Agent override | `AGENT_CONFIG_DEFAULTS["tool_exposure"]`, empty = inherit |
| Surface built from the mode | `tasks/ai/_agentctx_p3.py` |
| UI | agent dialog select in `tasks/io/chat_ui/resources_menus.js` + en/fr/es labels; the service dialog is generated from the parameter schema, so it needed no UI change |
| Server-side validation | `tasks/ai/actions/_agentres_k5.py` — the field allowlist plus a mode check, since an unknown value would otherwise be silently dropped |
| Tests | `tests/test_tool_exposure.py` (25) |
| CLI bridge parity | Server-resolved dynamic surface plus readonly execution guard in `ToolRelayService` and `tools/mcp_bridge.py` |
