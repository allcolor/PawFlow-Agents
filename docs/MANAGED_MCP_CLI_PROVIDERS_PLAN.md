# Managed MCP CLI Providers — Complete Implementation Plan

Status: **implemented; Agy 1.1.26 availability follows Google's documented
Stop-hook contract and transcript path, not protobuf-only evidence**
Scope: add `cc_mcp`, `codex_mcp`, and `agy_mcp` as managed `llmConnection` providers
Primary outcome: let PawFlow run the three official interactive CLIs exactly as it
does today while replacing provider-traffic observation with native lifecycle
hooks and PawFlow MCP tools.

## 1. Decision summary

PawFlow must add three real LLM provider values:

| New provider | Official CLI | Reused managed lifecycle | Canonical credential family |
|---|---|---|---|
| `cc_mcp` | Claude Code (`claude`) | Claude Code interactive pool and tmux path | `claude-code` |
| `codex_mcp` | Codex (`codex`) | Codex interactive pool and tmux path | `codex-app-server` |
| `agy_mcp` | Antigravity/Gemini CLI (`agy`) | Antigravity pool and tmux path | `gemini` |

These providers are normal `runtime_kind=llm` providers. They are not
`runtime_kind=external_mcp` agents, are not terminals launched by an external
client, and do not require a client-kind handshake. PawFlow already knows which
CLI it launched.

The process lifecycle does not change:

1. PawFlow selects the provider and credential slot.
2. PawFlow creates or reuses the provider's current managed container and tmux
   session.
3. PawFlow pastes the prompt into that tmux session.
4. The official CLI calls its vendor through its own native authentication and
   transport.
5. The official CLI calls PawFlow tools through its native MCP client.
6. The existing managed lifecycle hook path reports prompt submission and, after
   the minimal extension in this plan, final text to PawFlow.
7. PawFlow returns a normal `LLMResponse` through the existing agent loop.

CCI, Codex and Agy now have managed hook paths. Agy's documented `Stop` payload
names the persistent transcript through `transcriptPath`; the shared hook reads
the final answer there. `finalModelOutput` is used only when present, since it is
not a documented hook field. Its pool supplies proxy-independent container+tmux
liveness. The recorded evidence is documented, not an authenticated observation;
see [Google's hook contract](https://antigravity.google/docs/hooks) and the probe
record in `tests/fixtures/agy_managed_hook_probe.json`.

The only intended provider-path substitution is:

~~~text
current interactive provider:
    managed container + tmux + vendor-traffic MITM/observer + PawFlow MCP

new managed MCP provider:
    managed container + tmux + native lifecycle hooks + PawFlow MCP
~~~

There is no new OAuth implementation, vendor API client, vendor protocol,
terminal owner, or externally registered runtime.

## 2. Naming and configuration boundary

The provider identifiers are exactly:

- `cc_mcp`
- `codex_mcp`
- `agy_mcp`

Do not silently alias them to `external_mcp` or to the existing
`*-interactive` provider names. The provider value selects the new observation
mode while the service remains a normal `llmConnection`.

The existing providers remain distinct during rollout:

- `claude-code-interactive`
- `codex-interactive`
- `antigravity-interactive`

A service may use one path or the other. There is no automatic fallback between
them. A hook/MCP startup failure in a new provider must fail that provider
explicitly; it must never start the matching MITM path behind the user's back.

The new providers reuse the existing credential families and current credential
policies. In particular, this project adds no refresh behavior. If the canonical
credential service disables PawFlow-side refresh, the new provider inherits that
decision unchanged.

## 3. Current components to reuse

### 3.1 Managed CLI lifecycle

The following components already own container and tmux lifecycle and remain the
authority:

- `core/claude_code_interactive_pool.py`
- `core/codex_interactive_pool.py`
- `core/antigravity_observer_pool.py`
- `core/_cci_pool_spawn.py`
- `core/_antigravity_input.py`

Reuse includes:

- session keying by user, conversation, agent, and LLM service;
- credential-slot selection and exclusivity;
- cold launch versus live reuse;
- deterministic session workdirs;
- prompt paste, submission proof, and duplicate-submit protection;
- terminal attachment;
- preemption and force-stop input;
- idle eviction, explicit invalidation, shutdown, and token recovery;
- ephemeral session destruction;
- live-session reporting.

Do not create three parallel process managers. Add a managed MCP observation mode
to the existing lifecycle. Store the mode on the live session/container and
reject reuse when it differs, so the existing pool key and all its callers remain
unchanged.

### 3.2 PawFlow MCP tools

The existing internal MCP bridge remains the canonical tool path:

- `ClaudeCodeSessionMixin._setup_mcp_config` creates the scoped MCP config and
  short-lived internal token;
- `tools/mcp_bridge.py` and `ToolRelayService` expose the conversation's PawFlow
  tools;
- tool execution continues to resolve the conversation's linked/default relays;
- `tool_exposure` and agent tool allowlists remain authoritative.

The managed provider must not start the published-client relay or use the CLI
container as a replacement workspace relay. It must not route tool access
through `MCPServerStore` merely because the external installer uses that store.

### 3.3 Existing managed lifecycle hooks

The implementation starts from the managed hook path that already exists:

- `core/_cci_pool_spawn.py::_write_hook_settings` writes six Claude hooks:
  `UserPromptSubmit`, `Stop`, `StopFailure`, `PreCompact`, `PostCompact`, and
  `SessionEnd`;
- `core/codex_interactive_pool.py::_write_codex_hooks` writes five hooks to
  `.codex/hooks.json`: `UserPromptSubmit`, `Stop`, `PreCompact`,
  `PostCompact`, and `SessionEnd`;
- both invoke `tools/cc_interactive_hook.py`;
- that hook already registers with `CCInteractiveEventService` as
  `client_kind="hook"` using the managed session token, consumes the injected
  prompt marker, and sends the existing `hook` event envelope;
- `core/llm_providers/_cci_turn.py` and
  `core/llm_providers/_codex_interactive_turn.py` already consume those events.

The missing CCI/Codex behavior is narrow: `_compact_input` already preserves
`last_assistant_message`, but the managed coordinators do not use it because
their answer source is currently vendor SSE. Extend the existing hook and
coordinators so a `Stop` event supplies final text, with a bounded local
transcript fallback when the hook field is absent.

Do not make `scripts/mcp-client-hook.py` the managed implementation base and do
not refactor the published external-client installer as a prerequisite. Its AGY
payload/output and transcript parsing can be used as targeted reference code.
If a helper is genuinely shared, follow the existing image pattern: place a
small `tools/*_common.py` helper in the runtime-file copy list, rather than
putting managed runtime code in the separately delivered `pawflow_relay`
package.

When this plan was written, AGY was the remaining implementation gap:
`AntigravityObserverPool` mounted the observer proxy and semantics only and
wrote no lifecycle hooks. The completed managed mode mounts the managed hook,
generates AGY-native hook settings, normalizes `transcriptPath`, and returns
AGY's `injectSteps` shape where required. WP0 must
first prove which hook carries final text, or prove the local transcript
fallback, against the supported official AGY version.

### 3.4 Internal event service

`CCInteractiveEventService` already provides:

- session tokens and conversation/agent binding;
- exclusive consumer epochs;
- bounded event queues;
- prompt-submission receipts;
- injected-prompt deduplication;
- manual prompt capture;
- tool-event publication;
- request ownership and stale-consumer fencing.

Reuse this service and its current hook registration/event envelope. The
mode-gated changes are limited to exposing final text to the active consumer,
recognizing a managed prompt hook as a manual-turn trigger, and selecting a
managed final-hook waiter for manual capture. Do not add a second broker, a new
wire protocol, or hook-supplied consumer epochs/turn receipts.

## 4. Architecture

~~~text
AgentLoopTask
    |
    v
LLMClient(provider = cc_mcp | codex_mcp | agy_mcp)
    |
    v
existing mode-gated interactive turn path
    |
    +--> existing provider pool: acquire/reuse/begin_turn/end_turn/kill
    |         |
    |         +--> paste prompt into managed tmux
    |
    +<-- CCInteractiveEventService <--- native CLI lifecycle hook
    |
    v
LLMResponse(final text, optional native metadata, no replayed tool calls)

official CLI
    |
    +--> vendor endpoint over the CLI's native TLS/auth path
    |
    +--> internal PawFlow MCP bridge --> ToolRelayService --> scoped tools/relays
~~~

Explicitly absent from the new path:

- local CA generation or installation for vendor traffic;
- vendor hostname redirection;
- `ANTHROPIC_BASE_URL` or equivalent values pointing at an observer;
- transparent TLS or HTTP proxy startup;
- vendor request/response decoding;
- proxy log tailing as a response source;
- external terminal registration, heartbeat, or relay ownership;
- `MCPServerStore` publication;
- `external_agent_runtime_router`;
- `route_published_terminal_prompt`;
- `complete_published_terminal_turn`.

Those external-agent components remain correct for user-operated published MCP
clients but are not part of a PawFlow-managed LLM provider.

## 5. Non-negotiable invariants

1. `cc_mcp`, `codex_mcp`, and `agy_mcp` are normal LLM providers.
2. PawFlow launches and owns every corresponding CLI process and tmux session.
3. PawFlow submits web, API, A2A, delegate, and preempt prompts through the same
   managed tmux input path used by the matching interactive provider.
4. Only the official CLI talks to the vendor model service.
5. No new provider reads or parses vendor model traffic.
6. No new provider adds a CA, vendor host override, or transparent proxy.
7. Tools are exposed only through the existing scoped PawFlow MCP bridge.
8. The conversation's relay/tool authorization is unchanged.
9. The external published-agent router is never consulted for these providers.
10. A cold session receives the full PawFlow context exactly once.
11. A reused session receives only the missing delta and the new prompt.
12. A PawFlow-injected prompt is never persisted or submitted twice.
13. A manually typed tmux prompt remains visible in the conversation exactly
    once.
14. Every conversation message created by the path keeps the existing UUID and
    creation timestamp invariant.
15. Managed hooks retain the current `hook` event envelope and authenticated
    session registration.
16. Consumer epochs, request ownership, and prompt-submission proof remain
    server-owned; the hook does not mint or echo them.
17. Only the current fenced consumer may complete a turn, and duplicate/stale
    final hooks are ignored.
18. A final response is persisted by the normal agent-loop path, not by the
    published external-agent completion path.
19. Tools executed by the CLI are not returned to `AgentLoopTask` as a second
    set of tool calls.
20. Force stop is immediate, is not an agent error, and never poisons the next
    loop.
21. A hook/MCP failure never falls back to MITM or another provider.
22. Existing interactive provider behavior and tests remain unchanged.
23. Credential selection, refresh policy, recovery, and lease semantics remain
    those of the canonical provider family.
24. No secrets appear in hook payloads, logs, terminal status, or exceptions.
25. Every user-visible capability is honest; unavailable usage or thinking
    telemetry is not fabricated.

## 6. Provider and session identity

Extend each interactive session/container state with an explicit observation
mode:

~~~text
observation_mode = "mitm" | "managed_mcp"
~~~

Keep the current pool key unchanged: user ID, conversation ID, agent name, and
LLM service ID. Its compatibility check must additionally compare the concrete
provider, observation mode, and relevant launch/config revision stored on the
live state. A mismatch kills and recreates the managed session; it never mutates
a running CLI's wiring or expands the key contract used by `kill_session` and
other callers.

The event service registers the concrete provider, not merely a vendor family.
Status rows and SSE sources therefore report `cc_mcp`, `codex_mcp`, or
`agy_mcp` while credential lookup normalizes to the canonical family.

## 7. Existing managed hook contract and minimal extension

Keep the current event shape produced by `tools/cc_interactive_hook.py`:

~~~json
{
  "type": "hook",
  "hook_event_name": "Stop",
  "input": {
    "last_assistant_message": "..."
  },
  "container_id": "...",
  "timestamp": 0.0
}
~~~

The hook authenticates once during WebSocket registration with its event token,
managed session token, container ID, and `client_kind="hook"`.
`CCInteractiveEventService` supplies trusted user, conversation, agent, provider,
consumer epoch, and active-request ownership from that registration and its
server state. Those fields must not be accepted from hook input.

Required CCI/Codex changes:

1. Preserve and bound `last_assistant_message` from `Stop`.
2. When it is empty, read only the supported local transcript/session source and
   extract the last assistant message; never scrape tmux or vendor traffic.
3. Let the managed-mode turn consumer use that final text as its sole answer
   source.
4. Make duplicate finals harmless through the existing fenced consumer and
   active-turn state.
5. Make one short, bounded delivery retry in the hook; if delivery is still
   lost, let the existing coordinator deadline raise a typed failure.

Do not add durable event spooling, acknowledgement/replay, hook event IDs,
hook-visible consumer epochs, or hook-visible turn receipts. The current hook is
fire-and-forget with a five-second command timeout, and the managed coordinator
timeout is the failure boundary.

AGY reuses this managed transport through a client-aware adapter for its input
and output shapes. The completed development probe records the exact supported
hook names, payload fields, transcript location, and native final-answer source;
`agy_mcp` is enabled from that evidence.

## 8. Prompt and context flow

### 8.1 Cold session

1. The provider calls the existing pool with the existing `before_launch` cold
   context callback.
2. The pool creates the session, internal MCP config, managed hook profile,
   injected-prompt marker, and event-service registration.
3. The CLI starts without a vendor proxy.
4. `_cli_require_cold_context` builds the full initial document exactly as it
   does for the matching interactive provider.
5. The existing prompt builder combines that context and the current user input.
6. `remember_injected_prompt` records the exact text before the existing tmux
   paste/submit path sends it once.
7. `UserPromptSubmit` consumes that marker and provides the existing submission
   proof.
8. `initial_context_loaded` advances under the existing cold-context success
   semantics.

Version 1 does not deliver bootstrap context through `SessionStart` or any hook.
That would change size limits and break the current marker/deduplication
contract. Existing bootstrap read/call elision remains applicable.

### 8.2 Reused session

1. The existing pool returns the compatible live session.
2. `_cli_require_delta_context` builds the missing delta using the current
   interactive-provider rules.
3. The existing prompt builder combines the delta and current input.
4. PawFlow records the combined injected prompt before pasting it once.
5. `UserPromptSubmit` consumes the marker and provides submission proof.
6. The existing context cursor advances under the same success semantics as
   today.

This replaces provider-traffic observation, not PawFlow's conversation
authority.

### 8.3 Manual tmux input

A prompt that has no valid PawFlow marker is manual input. Today
`CCInteractiveEventService._is_provider_request` recognizes only proxy
`request_start`, and `_run_manual_capture` imports the two MITM coordinators.
Managed mode must explicitly change both sites:

- recognize the unmarked managed prompt hook as the orphan/adoption trigger;
- claim the existing capture consumer and persist the manual message once with
  channel `tmux`;
- use the managed final-hook waiter rather than a MITM coordinator;
- publish/persist the final text once through the existing conversation/SSE
  callbacks.

Version 1 manual capture exposes final text and PawFlow MCP activity only. It
must not depend on a vendor request-start event.

## 9. Turn coordinator

Mode-gate the existing interactive turn paths and reuse their event-service
consumer, submission-proof, callback, abort, timeout, activity, and pool-turn
machinery. A small shared managed-final waiter is acceptable; a second event
broker or parallel lifecycle coordinator is not.

Responsibilities:

- claim the existing event-service consumer epoch before submitting the prompt;
- drain only events that the current interactive path already treats as stale;
- record the injected-prompt marker before paste;
- wait for existing `wait_for_prompt_submission` proof;
- in managed mode, ignore proxy response events and wait for the matching
  session's `Stop` final text under the current consumer epoch;
- mirror PawFlow MCP tool rows through the existing block/event path without
  executing them again;
- invoke the text callback at most once and return an `LLMResponse`;
- on missing final text, hook-delivery timeout, or dead session, raise a typed
  non-retryable CLI failure unless the existing classifier explicitly says
  otherwise;
- on abort, stop waiting immediately;
- reject stale consumers/session registrations and duplicate finals;
- touch the existing pool activity clock while waiting;
- always release the consumer and end the pool turn.

The final `LLMResponse` has:

- `content` from the native final hook;
- `tool_calls=[]` because the official CLI already completed its own MCP loop;
- `model` from verified CLI metadata when present, otherwise the configured
  service model;
- `raw.provider` equal to the concrete new provider;
- `raw.telemetry` describing which fields are native and which are unavailable;
- token fields only when a native local hook/transcript source supplied them.

## 10. Streaming, thinking, tools, and usage

The new providers must not claim transport-level parity they do not possess.

### 10.1 Text streaming

The currently supported native lifecycle hooks provide a reliable final answer,
not vendor response deltas. Version 1 therefore emits the final answer as one
text callback immediately before returning `LLMResponse`. The webchat still
receives normal active state, MCP tool activity, and final text.

Do not scrape the terminal pane to manufacture streaming. Do not parse vendor
traffic. Native delta hooks may be added later only through the same normalized
event contract.

### 10.2 Thinking

Thinking/reasoning is emitted only if an official hook provides a documented
field. Otherwise no thinking callback is emitted and the UI reports it as
unavailable. Terminal scraping is forbidden.

### 10.3 Tool calls

The CLI owns the model/tool loop. MCP calls continue through ToolRelayService,
which remains responsible for authorization, execution, cancellation,
background tools, and live tool events. Those PawFlow MCP calls are therefore
visible without parsing vendor traffic. The coordinator may mirror those
existing events to the current request but must deduplicate by tool-call ID.

The final response never asks `AgentLoopTask` to execute those calls again.

Provider-native built-in tools have a different visibility boundary. CCI already
calls `_deny_builtin_tools`, so MCP is the only tool path there. Codex and AGY
built-in tool activity is not visible to PawFlow in version 1 unless their
official managed hooks expose supported `PreToolUse`/`PostToolUse`-equivalent
events. UI/API capability metadata must say so; the implementation must not
fabricate those tool rows.

### 10.4 Usage and context gauge

Accepted native sources are local CLI hook fields and documented local
transcript/session metadata. Existing Codex rollout token-count support may be
reused because it reads native local session data, not vendor traffic.

When a CLI supplies no trustworthy measurement:

- token fields remain zero/unknown rather than estimated from transport;
- cost tracking marks usage unavailable;
- the context gauge shows unavailable, not 0%;
- compaction uses the explicit safe policy documented for that provider and
  never pretends an unknown native window was measured.

The UI and API must expose `usage_source` and `context_source` so an unavailable
measurement cannot be mistaken for a real zero.

## 11. Provider-specific launch configuration

### 11.1 `cc_mcp`

Reuse the Claude pool, workdir, credentials, prompt builder, attachment
materialization, tmux send/verify logic, invalidation, and terminal viewer.

Managed-MCP mode must:

- keep `--strict-mcp-config` and the internal PawFlow MCP server;
- retain the six hooks already installed by `_write_hook_settings`:
  `UserPromptSubmit`, `Stop`, `StopFailure`, `PreCompact`, `PostCompact`,
  and `SessionEnd`;
- extend the existing `Stop` path to supply bounded final text;
- provide only event-service/session credentials to the hook;
- start `claude` against its configured/native vendor endpoint.

It must not generate/install a leaf CA, start `cc_interactive_proxy.py`, set
`NODE_EXTRA_CA_CERTS` for interception, redirect the vendor host, or point
`ANTHROPIC_BASE_URL` at PawFlow.

### 11.2 `codex_mcp`

Reuse `CodexInteractivePool`, its TUI readiness/submission logic, workdir,
credentials, Codex home/config merge, attachment behavior, invalidation, and
terminal viewer.

Managed-MCP mode must:

- install the canonical Codex MCP entry and lifecycle hooks in its isolated
  `CODEX_HOME`;
- use the pool's existing `.codex/hooks.json` writer as the hook source of
  truth; the `config.toml [hooks]` code in `install-mcp-client.py` is for JCode,
  not this Codex pool;
- retain `codex -C <workdir>` and existing safe config merge behavior;
- extend the existing `Stop` hook with bounded final text plus supported local
  transcript fallback;
- retain native rollout context usage when available.

It must not create a local vendor endpoint, start a responses MITM, install a
CA, or infer completion from vendor HTTP events.

### 11.3 `agy_mcp`

Reuse the Antigravity pool's container, workdir, credentials, MCP configuration,
tmux literal-paste logic, attachments, force-stop, invalidation, and terminal
viewer.

Before `agy_mcp` is enabled, WP0 must probe the supported official AGY build and
prove the required hook names, payload fields, `injectSteps` response shape, and
final-answer source. Once proven, managed-MCP mode must:

- generate the existing documented AGY MCP config shapes in the isolated home;
- mount `tools/cc_interactive_hook.py` (plus only any necessary small common
  helper) into the AGY image and make it client-aware;
- install the proven `PreInvocation` and `Stop` hooks in the AGY settings;
- return the proven AGY hook output shape (`injectSteps` when required), while
  keeping version 1 cold/delta context in the pasted prompt;
- normalize `transcriptPath` and extract the final answer from a proven hook
  field or supported local transcript;
- make `AntigravityObserverPool._is_usable` mode-aware so managed mode checks
  container/TUI readiness rather than `_proxy_log_ready`;
- inherit the canonical Gemini credential refresh policy unchanged.

It must not start `ag_observer_proxy.py`, install a CA, alter
`daily-cloudcode-pa.googleapis.com` routing, or tail an observer log.

If the probe cannot establish a reliable final answer, `agy_mcp` stays
unavailable rather than claiming parity with `cc_mcp`.

## 12. Preemption, cancellation, and shutdown

The new providers advertise live preemption only after the existing
provider-specific preemption path and managed final source pass all of the
following per CLI:

- an active request can receive a second PawFlow prompt through the existing
  provider-specific interrupt input;
- the current server-owned request/consumer remains authoritative while
  additional prompts are ordered through the existing path;
- the final answer completes the correct active request once;
- queued messages remain available to the next loop.

Do not introduce a hook-visible receipt queue/ledger.
`reply_to_message_id` belongs to the published external hook and is not part of
this managed provider path. If the existing server-owned state cannot prove
preemption correlation for a CLI, advertise no live preemption for that provider
until it can.

Force stop:

1. sets the client abort flag;
2. sends the existing provider-specific escape/stop action;
3. wakes the managed coordinator;
4. ends the active pool turn;
5. reports no agent error;
6. leaves the session reusable only if the existing pool proves it returned to
   an idle prompt; otherwise it is killed and recreated on the next turn.

Container death, session eviction, conversation edit/compact, branch switch,
service deletion, and server shutdown use the existing pool invalidation paths.
Every path unregisters the event session and revokes its short-lived internal
token.

## 13. Failure semantics

Fail explicitly for:

- unsupported CLI/hook version;
- missing required lifecycle hook support;
- MCP server startup failure;
- hook profile/config generation failure;
- prompt submission not proven;
- final hook missing after the configured timeout;
- final hook with no extractable answer;
- dead container/tmux;
- session-token or consumer-epoch mismatch;
- unavailable credential slot.

Rules:

- no silent MITM fallback;
- no fallback to another provider;
- no automatic re-paste without the existing submission proof;
- no generic retry after the CLI may have accepted a prompt;
- reconcile the existing submission proof, consumer, and session state first;
- preserve partial diagnostic metadata without logging prompts or secrets;
- a provider-native quota/auth error uses the existing failure classifier only
  when the hook supplies unambiguous evidence.

## 14. Security and vendor boundary

The new architecture strengthens the boundary by construction:

- the official CLI is the sole vendor client;
- vendor OAuth and transport behavior are whatever the official CLI performs;
- PawFlow adds no vendor endpoint call or protocol implementation;
- PawFlow MCP is used only through each CLI's documented MCP client;
- lifecycle synchronization uses each CLI's documented hooks;
- prompt submission is normal terminal input through the existing tmux path.

This plan does not claim that PawFlow never stores or materializes credentials:
the matching managed pools already own credential selection and session setup.
It claims the narrower and testable property that the new provider adds no new
OAuth flow and does not intercept or implement vendor model transport.

Add negative launch-command tests proving the absence of proxy binaries, local
CA flags, vendor host overrides, and interception environment variables.

## 15. Service, auth, and UI wiring

Add the three providers to the shared provider registry and dispatch without
copying provider-specific lists into more call sites than necessary.

Required registry work includes:

- `LLMClient.PROVIDERS` and `_LIVE_PREEMPT_SUPPORT` in
  `core/llm_client.py`;
- `INTERACTIVE_CLI_PROVIDERS` in `core/_llm_types.py`;
- `CLI_PROVIDERS` in `core/llm_auth_modes.py`;
- `_SESSION_CONTEXT_PROVIDERS` in
  `core/llm_providers/context_observation.py`;
- `_SHORT_PROVIDER` in `services/llm_credential_oauth.py`, including the concrete
  short aliases for `cc_mcp`, `codex_mcp`, and `agy_mcp`;
- API-key validation and complete/stream/abort/preempt dispatch in
  `core/_llm_client_driver.py`;
- default-model resolution and failure classification in
  `config/default_models.json` and `core/llm_failure_classifier.py`;
- OAuth credential aliases:
  - `cc_mcp -> claude-code`;
  - `codex_mcp -> codex-app-server`;
  - `agy_mcp -> gemini`;
- the provider-to-credential-family map in
  `services/llm_connection.py` and its provider visibility list in
  `get_parameter_rules`, plus service parameter rules and provider labels;
- agent-loop callback/capability gates in
  `tasks/ai/_alc_closures1.py`, `tasks/ai/_alc_closures2.py`,
  `tasks/ai/_alc_iteration.py`, and `tasks/ai/agent_core.py`;
- context and agent-context gates in `tasks/ai/context_usage.py` and
  `tasks/ai/_agentctx_p1.py`;
- usage/status/install-bootstrap actions in `tasks/ai/actions/usage.py`,
  `tasks/ai/actions/_sf_k6.py`, and `tasks/ai/system/install_bootstrap.py`;
- terminal open/grab/status mappings in
  `tasks/io/chat_ui/terminal_commands.js`,
  `tasks/io/chat_ui/grab.js`, and
  `tasks/io/chat_ui/active_agents.js`.

`tests/test_gauge_invariants.py` and
`tests/test_provider_dispatch_signatures.py` contain source-text and dispatch
invariants for these provider gates. Update the explicit assertions, including
`test_dispatch_kwargs_match_signatures`,
`test_the_block_gate_and_the_turn_gate_list_the_same_providers`, and
`test_cli_providers_do_not_force_default_model_flags`, as part of the
registry change, not as an afterthought.

For new mappings that otherwise require repeated conditionals, prefer one small
capability table such as:

~~~python
MANAGED_MCP_PROVIDERS = {
    "cc_mcp": ManagedMcpProviderSpec(...),
    "codex_mcp": ManagedMcpProviderSpec(...),
    "agy_mcp": ManagedMcpProviderSpec(...),
}
~~~

Use it where practical to answer vendor family, pool family, live-preempt
support, credential alias, terminal action, and telemetry capability. Do not
make broad refactoring of existing registries a prerequisite for these three
additions.

UI labels must make the distinction visible:

- Claude Code — MCP hooks
- Codex — MCP hooks
- Antigravity — MCP hooks

The service form must not show MITM-specific settings for these providers. It
must show the same model, credential, MCP tool-exposure, CLI environment,
container, attachment, timeout, and compaction settings that remain applicable.

## 16. Code ownership and expected file groups

The implementation review must account for these concrete groups.

### 16.1 Existing managed hook path

- `tools/cc_interactive_hook.py` extended in place for bounded final extraction
  and client-aware AGY normalization;
- `core/_cci_pool_spawn.py::_write_hook_settings` retained for Claude;
- `core/codex_interactive_pool.py::_write_codex_hooks` retained for Codex;
- AGY runtime-file mounts and settings generation added to
  `core/antigravity_observer_pool.py`;
- an optional small `tools/*_common.py` helper copied with the hook only if
  direct reuse is justified.

No rewrite of `scripts/mcp-client-hook.py` or the published-client installer is
required. Their existing tests remain regression gates.

### 16.2 Managed pools and event service

- `core/_cci_pool_spawn.py`;
- `core/claude_code_interactive_pool.py`;
- `core/codex_interactive_pool.py`;
- `core/antigravity_observer_pool.py`;
- `core/_antigravity_input.py`;
- `services/cc_interactive_event_service.py`;
- `core/llm_providers/_cci_turn.py`;
- `core/llm_providers/_codex_interactive_turn.py`;
- `core/llm_providers/antigravity_interactive.py`.

Changes must be mode-gated so current MITM providers keep their present launch
and response paths. In particular, managed AGY must not inherit
`_saw_proxy_event`, `_NO_PROXY_EVENT_TIMEOUT`, or `_proxy_log_ready` gates.

### 16.3 Provider facade and shared client

- mode-gated existing coordinators plus, at most, one small shared managed-final
  waiter;
- thin provider entrypoints for all three concrete values;
- `core/llm_client.py`;
- `core/_llm_client_driver.py`;
- `core/_llm_types.py`;
- `core/llm_auth_modes.py`;
- `core/llm_failure_classifier.py`;
- `core/llm_providers/context_observation.py` when native measurements apply.

### 16.4 Service and agent loop propagation

- `services/llm_connection.py`;
- `services/llm_credential_oauth.py`;
- `tasks/ai/_agentctx_p1.py`;
- provider-dependent callback/capability gates under `tasks/ai/`;
- live-session/usage/status handlers;
- conversation and agent invalidation paths.

Do not add an `external_mcp` branch to `AgentLoopTask` for these providers. The
normal LLM path must remain authoritative.

### 16.5 UI and documentation

- LLM service schema/form labels;
- active CLI status and context telemetry;
- tmux open/grab commands;
- `config/default_models.json`;
- `docs/llm_providers.md`;
- `docs/CLAUDE_CODE_INTERACTIVE.md`;
- the task/service reference when provider options are enumerated.

### 16.6 Cross-plan ownership and dirty-worktree gate

Claude owns only the managed-hook, managed-pool, MCP-provider-specific turn,
terminal UI, `docs/CLAUDE_CODE_INTERACTIVE.md`, and focused managed-MCP test
files listed in sections 16.1 through 16.5. Assistant owns every shared hot
file: `pyproject.toml`, provider registries and dispatch, auth/failure/context
tables, `services/llm_connection.py`,
`services/llm_credential_oauth.py`, default models, shared agent-loop gates,
shared service UI, `docs/llm_providers.md`, the task/service reference,
`CHANGELOG.md`, `tests/test_gauge_invariants.py`, and
`tests/test_provider_dispatch_signatures.py`. Claude returns patch blocks for
those files; assistant performs one combined integration edit.

Before parallel implementation, freeze the pre-existing `allow_refresh` patch
in a dedicated commit explicitly approved by Quentin. Until then nobody edits
its 13 files: `core/__init__.py`, `core/llm_oauth_credential.py`, the three
CLI session modules, `services/llm_credential_oauth.py`, actions `_sf_k1.py`
and `_sf_k2.py`, service-login/schema-form UI, both shared provider/reference
docs, and `tests/test_llm_credential_oauth.py`.

## 17. Work packages

### WP0 — Pin the executable contract

Deliver:

- provider constants/spec table;
- capability matrix for final text, thinking, usage, context gauge, tools,
  attachments, preemption, terminal input, and manual capture;
- exact current managed-hook event/registration contract;
- AGY probe results for hook names, input/output shapes, transcript path, and
  final-answer availability;
- CCI/Codex `Stop` final-field/transcript fixtures;
- explicit built-in-tool visibility claims per provider;
- negative no-MITM launch assertions;
- failing tests for CCI/Codex registration and one complete turn, plus AGY tests
  enabled only after its probe succeeds.

Exit gate:

- reviewers can trace every supported turn from AgentLoop to tmux, the existing
  hook event service, hook final, and
  `LLMResponse`;
- no design path enters the external published-agent router;
- `agy_mcp` has a proven native final source and is available.

### WP1 — Extend the existing managed hook

Deliver:

- bounded final-message extraction in `tools/cc_interactive_hook.py`;
- supported local transcript fallback;
- one short bounded delivery retry;
- unchanged current registration and `hook` event envelope;
- client-aware AGY input/output handling and image mount, backed by the completed
  WP0 contract.

Exit gate:

- existing CCI/Codex hook behavior and prompt-marker tests remain green;
- `Stop` fixtures deliver one bounded final text;
- published MCP installer/persistent-terminal behavior remains unchanged;
- any enabled AGY hook returns the exact proven output shape.

### WP2 — Add the managed final path to existing consumers

Deliver:

- mode-gated final-hook handling in the existing turn paths;
- reuse of current consumer fencing, prompt-submission proof, timeout, abort,
  callback, and pool-turn cleanup;
- manual-turn trigger and final-only capture without vendor request observation;
- abort/death/failure wakeups.

Exit gate:

- concurrent sessions cannot cross-deliver;
- stale or duplicate finals cannot complete a newer turn;
- a dropped final-hook delivery ends in one typed timeout without prompt replay;
- manual managed input produces one user message and one final assistant message.

### WP3 — Add mode-aware pool launch

Deliver:

- `managed_mcp` observation mode in the existing pool lifecycle;
- mode stored on the session and checked by reuse compatibility without changing
  the existing pool key;
- per-CLI managed hook and MCP config;
- direct native vendor launch with no proxy/CA/host override;
- AGY liveness/readiness independent of proxy-log state;
- existing prompt paste, terminal, credential, eviction, and kill behavior
  reused.

Exit gate:

- command/config snapshot tests prove the expected CLI arguments;
- negative assertions prove no interception artifact appears;
- current interactive provider launch snapshots remain unchanged.

### WP4 — Register `cc_mcp`, `codex_mcp`, and `agy_mcp`

Deliver:

- client provider registry and dispatch;
- thin provider adapters to the shared coordinator;
- credential normalization and auth modes;
- default models and failure classification;
- complete/stream/abort/preempt wiring;
- attachment and cold/delta prompt integration.

Exit gate:

- each provider completes cold and reused turns with a fake official CLI harness;
- tools execute once inside the CLI loop;
- the normal agent loop persists exactly one final answer;
- `agy_mcp` registration is enabled because the WP0 probe and its tests pass.

### WP5 — Operability and UI

Deliver:

- service options and honest capability help;
- live-session provider identity;
- active status, terminal open/grab, force stop, and invalidation;
- unavailable usage/context state rather than false zeroes;
- diagnostics that expose hook name/timestamp plus bounded session and consumer
  state without prompts or secrets.

Exit gate:

- each new provider can be configured, launched, viewed, stopped, invalidated,
  and relaunched from the current UI;
- terminal/manual turns remain synchronized with the conversation.

### WP6 — Documentation and rollout

Deliver:

- update `docs/llm_providers.md` and the task/service reference;
- update `docs/CLAUDE_CODE_INTERACTIVE.md`;
- document capability differences from MITM interactive providers;
- document credential reuse and the no-new-OAuth boundary;
- document troubleshooting for hook delivery, final timeout, and MCP failures;
- update the remote CLI compute plan's provider-capability matrix when remote
  execution implements these providers.

Exit gate:

- docs, schemas, API responses, and UI use the same exact provider names and
  capability claims.

## 18. Test plan

### 18.1 Managed hook tests

Cover:

- current Claude and Codex hook payload shapes;
- Claude `Stop` fixtures with and without `last_assistant_message`;
- direct final field and transcript fallback;
- injected versus manual prompt detection;
- marker expiry and prompt normalization;
- unchanged `hook` event envelope and registration;
- duplicate/out-of-order final delivery;
- one bounded reconnect retry and typed coordinator timeout;
- proven AGY `transcriptPath`/`injectSteps` shapes when AGY is enabled;
- published adapter regression coverage without changing that adapter.

### 18.2 Pool tests

For every new provider:

- cold launch;
- compatible live reuse;
- incompatible mode/config revision forces recreation;
- tmux prompt is marked before paste/Enter;
- submission proof succeeds only for the expected prompt;
- credential slot is not shared illegally;
- token recovery/revocation follows canonical family policy;
- idle eviction and shutdown;
- force stop and next-turn recovery;
- ephemeral session cleanup;
- terminal viewer mapping;
- no proxy process;
- no generated interception CA;
- no vendor host override;
- no interception environment variable.

AGY pool tests additionally prove that managed-mode liveness does not consult
`_proxy_log_ready` and that the hook/runtime files and settings are present.
The official AGY hook/final/liveness probe runs in CI or a development gate,
never dynamically from the UI.

### 18.3 Coordinator tests

Cover:

- exact final correlation;
- one final callback and one `LLMResponse`;
- `tool_calls=[]` after CLI-owned MCP work;
- live MCP tool rows are not duplicated;
- stale consumer fencing;
- wrong session registration/provider rejection;
- final-before-wait race;
- abort while waiting;
- hook failure and dead tmux;
- preemption receipt ordering;
- manual prompt and orphan final;
- `_run_manual_capture` in managed mode without importing or consulting MITM
  coordinators;
- no final content;
- unknown usage and known Codex native usage.

### 18.4 Agent-loop and integration tests

Use a deterministic fake CLI harness that:

- starts in tmux;
- invokes the generated hooks with real JSON fixtures;
- calls the real test MCP bridge;
- emits a final answer;
- supports manual prompt, preempt, and force-stop scenarios.

Assert:

- cold context exactly once;
- delta context on reuse;
- current prompt exactly once;
- one tool execution;
- one assistant message;
- normal done/SSE semantics;
- delegate/A2A return through the normal LLM agent path;
- no `external_terminal` acknowledgement;
- no `MCPServerStore` record or terminal registration;
- no vendor network or proxy component in the test path.

Run the current Claude Code interactive, Codex interactive, Antigravity
interactive, MCP installer, persistent terminal, auth, gauge, active-agent,
terminal, provider-dispatch, and `test_gauge_invariants.py` suites as regression
gates.
Run `tests/test_provider_dispatch_signatures.py` explicitly as a shared-hot
integration gate.

## 19. Rollout

0. With Quentin's explicit approval, commit the existing `allow_refresh`
   patch alone. Keep each implementation in its own functional commit, combine
   shared-hot registry edits once, and add a separate Unreleased changelog note.
1. Land the existing-hook final-text extension and mode-gated consumer changes
   with no behavior change for current providers.
2. Register the new values behind normal static provider configuration; do not
   add a dynamic UI capability-probe subsystem.
3. Enable `cc_mcp` first and exercise cold/reuse/manual/preempt/stop paths.
4. Enable `codex_mcp` after its `.codex/hooks.json` and transcript fixtures pass.
5. Run the explicit development/CI probe against the supported official AGY
   build (completed for Agy 1.1.25). Hook final, `injectSteps`, liveness, and
   refresh-policy tests pass before `agy_mcp` is enabled.
6. Keep existing interactive providers as explicit alternatives during
   evaluation.
7. Remove neither path without a separate user-approved migration decision.

There is no silent service rewrite. Switching a service provider invalidates its
live session and creates a fresh correctly wired CLI on the next turn.

## 20. Acceptance criteria

The feature is complete only when all of the following are true:

- `cc_mcp`, `codex_mcp`, and `agy_mcp` are configurable as `llmConnection`
  services; Agy's required official-CLI hook probe has passed;
- PawFlow launches and supervises the official CLI using the existing pool
  lifecycle;
- prompts are submitted by the existing tmux paste path;
- the CLI uses the existing scoped PawFlow MCP tools;
- completion comes from native hooks, not vendor transport parsing;
- the final result returns through the normal LLM/AgentLoop path;
- tools and assistant output are persisted exactly once;
- cold/reuse/manual/preempt/force-stop/invalidation behaviors are covered;
- managed manual capture no longer depends on proxy `request_start`;
- managed AGY liveness no longer depends on proxy-log readiness;
- unsupported telemetry is honestly unavailable;
- no published external-agent routing or terminal registration occurs;
- no new OAuth or vendor transport implementation exists;
- launch tests prove that proxy, interception CA, and vendor host redirection are
  absent;
- existing interactive and external MCP client suites remain green;
- documentation and UI describe the exact same architecture.

## 21. Source-of-truth references

The implementation must validate this plan against live source at execution
time, especially:

- `core/claude_code_interactive_pool.py`
- `core/codex_interactive_pool.py`
- `core/antigravity_observer_pool.py`
- `core/_cci_pool_spawn.py`
- `tools/cc_interactive_hook.py`
- `core/llm_providers/claude_code_interactive.py`
- `core/llm_providers/_cci_turn.py`
- `core/llm_providers/codex_interactive.py`
- `core/llm_providers/_codex_interactive_turn.py`
- `core/llm_providers/antigravity_interactive.py`
- `services/cc_interactive_event_service.py`
- `core/llm_providers/claude_code_session.py`
- `scripts/install-mcp-client.py`
- `scripts/mcp-client-hook.py`
- `scripts/mcp-session-launcher.py`
- `core/_llm_types.py`
- `core/llm_auth_modes.py`
- `core/llm_failure_classifier.py`
- `core/llm_providers/context_observation.py`
- `services/mcp_terminal_router.py`
- `services/external_agent_runtime_router.py`
- `tests/test_gauge_invariants.py`
- `tests/test_mcp_client_installer.py`
- `tests/test_mcp_persistent_terminal.py`

The architectural boundary is the core review criterion: PawFlow manages the
CLI exactly as it manages the current interactive providers; native hooks plus
MCP replace MITM observation, and nothing else becomes an external runtime.
