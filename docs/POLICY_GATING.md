# Policy Gating (V0)

Policy gating lets a conversation or an agent run under a **gating service**
that decides, for every relevant tool call, whether the call is within the
mandate the authenticated user actually gave — `allow`, `deny`, or `ask`.
Design and roadmap: `docs/POLICY_GATING_SERVICE_PLAN.md`. This page documents
what is implemented.

## Concepts

- **Authorization context** (`core/authorization_context.py`): the user's
  root request plus later user corrections for one work lineage, versioned
  (revision 1, 2, ...), stored under `data/runtime/authorization-contexts/`.
  Only authenticated user input can add directives; assistant prose, tool
  results, files, web pages and delegate messages are never authority. Each
  context, directive and decision has a UUID and a timestamp.
- **Ingress recording**: when a user message is stamped for an agent
  (`tasks/ai/agent_streaming.py`), a new lineage starts, or — if that agent's
  turn is active (steering, answers to `ask_user`) — the active lineage is
  revised. The reference travels in the message `source.authorization` and in
  the per-agent record `gating_authority` (conversation extra); nothing scans
  the transcript at tool time.
- **Gating service** (`services/gating_service.py`, type `gating`): a policy
  prompt evaluated by an API-backed `llmConnection` and/or ordered
  `gating_script` resources. Options: `llm_scope` (`mutating` default, `all`,
  `none`), `failure_decision` (`ask` default or `deny`, never `allow`),
  `max_tokens`, `timeout_seconds`, `script_timeout_seconds`.
- **Gating script** (resource type `gating_script`): Python source defining
  `evaluate(event)` returning `{"decision": "allow|deny|ask|abstain",
  "reason": ..., "rule_id": ...}`; runs in the relay sandbox, receives only the
  redacted envelope, cannot rewrite the call, defaults to `abstain`, and maps
  failures to its `fail_decision` (`ask`/`deny`). Optional `tools` filter.
- **Bindings** (`core/gating_bindings.py`): conversation binding
  (`gating_link` / `gating_unlink` / `gating_list_available` actions, extra
  `gating_binding`) and optional agent binding (`gating_service` in the
  conversation agent config). No implicit fallback; a missing, disabled,
  wrong-type service — or a script gate without a linked relay — is **broken**
  and fails closed (`ask`). The agent gate can only tighten the conversation
  gate.

## Decision pipeline (primary AgentLoop runtime)

`core/tool_authorization.authorize_tool_call` runs inside the existing
`_authorize` step of `tasks/ai/agent_tool_exec.py`, on the **prepared**
effective call (after MCP unwrapping, pre-tool hooks, variable resolution and
registry preparation), before the legacy permission rules and before
execution:

1. no binding → `legacy`: behaviour unchanged;
2. classification: `internal_ungated` (`get_tool_schema`, `ask_user`,
   `request_confirmation`, `compact_result`) → legacy; `hard_deny`
   (explicit per-tool deny, read-only modes) → denied; `hard_confirm`
   (per-tool `confirm`, `create_tool`/`delete_tool`/`manage_resource`/
   `manage_package`/`store_secret`, publication changes, catastrophic
   commands, protected paths) keeps a human confirmation floor;
3. authority is loaded by reference (newest revision) and a redacted
   envelope is built (secret values and credential-looking keys removed,
   bounded sizes, canonical SHA-256);
4. the conversation gate, then the agent gate evaluate: scripts first
   (`deny`/`ask` short-circuit), then the LLM when `llm_scope` applies;
   per service `deny > ask > allow`, nothing decided → `failure_decision`;
   an LLM `allow` on a missing or truncated mandate is demoted to `ask`;
5. final `allow` executes (replacing the generic confirmation), `deny`
   returns an error to the agent, `ask` opens the normal approval dialog with
   the gate's reason; a hard-confirm `allow` becomes `ask`;
6. one redacted audit record per decision is appended to
   `data/runtime/gating-decisions/<conversation>.jsonl`
   (`core.tool_authorization.list_decisions`).

The gate LLM is called tool-free, at temperature 0, in an ephemeral scope,
and its answer is parsed provider-agnostically (first JSON object, strict
keys). Interactive CLI providers are refused as gate LLMs.

## Webchat

The resources sidebar shows a **Policy gate** section: bind a gating service
to the conversation (`+`), see the effective conversation/agent gates, a
broken binding (red, calls need confirmation), unbind, and open the decision
log (`gating_decisions` action, last 50 redacted records). Gating services
are created under Services like any other service; `gating_script`
resources are managed through the generic resource actions.

## Interim rule for other runtimes

Sub-agent executor, ToolRelay, external AG-UI and realtime voice are not yet
wired to the engine (WP6). Until then, when a gate is bound for the
conversation or agent, those runtimes must refuse the call with an explicit
error rather than run it ungated (plan decision 19).

## Limits of V0

- audit is a JSONL file per conversation (SQLite store, metrics and the
  decision viewer UI come with WP8/WP7);
- a user answer in the approval dialog may still persist `always_allow`
  through the legacy dialog; the gate itself never writes permissions;
- `resource_ref_list` form support for scripts is pending: `scripts` is a
  list of resource names.
