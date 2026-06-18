# File Split Plan — enforce ≤ 800 lines per source file

Status: **planned, not implemented** (analysis 2026-06-18). Read-only structural
analysis of every production source file over 800 lines, with a per-file split
that is **semantically, syntactically and runtime valid**. No file is touched
until a per-file plan here is approved.

## Rule & scope

- Rule: every production source file ≤ **800 lines**.
- Scope: **65 files** over 800 lines. Excluded:
  - `tests/` — not production; split separately if at all.
  - Vendored/synced runtime copies (`.pawflow-runtime/`,
    `280890741dad4975/`, `pawflow-relay-desktop/runtime/`) — these are
    **generated** from `pawflow_relay/` + `tools/`. Fixing the source fixes the
    copies; do not edit copies by hand. Re-sync after the source split lands.
- Counts as of analysis: 59 Python files + 6 JS files.

## Mechanisms (how a file is split)

Five mechanisms cover every case. Choice is driven by the file's *shape*, not
its line count.

- **A — Mixin decomposition.** One giant class → several `*Mixin` classes, one
  per responsibility cluster, recombined by multiple inheritance on the host
  class. In-repo precedent: `tasks/ai/agent_loop.py` already composes 9 mixins
  (`AgentActionsMixin`, `AgentStreamingMixin`, …). Used for single-huge-class
  files.
- **B — Dispatcher decomposition.** One huge `_handle_*` method that switches on
  an `action` string → thin dispatcher + one sub-handler module per action
  group. Used for the `tasks/ai/actions/*` action files.
- **C — Package extraction.** A free-function module with clean clusters → a
  package (`name/__init__.py` re-exports), one submodule per cluster. Import
  path stays identical. Used for function-dominated modules.
- **D — Multi-class split.** A module holding several independent classes → one
  module per class (or per class cluster), re-exported from the original path.
- **E — JS module split.** ES-module feature extraction with `import`/`export`;
  no mixins. Used for `chat_ui/*.js` and the VS Code webview.

## Runtime-validity invariants (apply to every split)

1. **Import-path stability.** External imports (`from core.pfp_package import
   install_pfp`, `from tasks.ai.agent_core import AgentCoreMixin`) MUST keep
   working. When a module becomes a package, its `__init__.py` re-exports the
   public surface; when a class moves, the old module re-imports it.
2. **Mixin MRO & shared state.** Split mixins share `self` state (`self._lock`,
   `self._store`, caches). One host class, one MRO, no method-name collisions
   across mixins, no mixin instantiated on its own.
3. **No circular imports.** Clusters that call each other go in the same module
   or communicate through the host. Deliberate late imports stay late and get
   `# noqa: E402` (precedent already in `agent_loop.py`).
4. **Registration preservation.** Every `TaskFactory.register`, handler
   registry entry, `@register_*`, action→handler routing table and provider
   registration must remain reachable and exhaustive after the move.
5. **Bound-method semantics.** Extracted dispatcher sub-handlers were
   `self`-methods — they must stay bound (take `self`/context explicitly) so
   `self.store`, `self.publish`, etc. still resolve.
6. **Test parity.** Full suite green before and after each file (baseline:
   5382 passed). Split one file per commit; CI (lint + test 3.10–3.13) green
   per commit.

## Risk tiers & sequencing

Work low-risk → high-risk so the mechanics are proven before the dangerous
files:

- **Tier 1 (mechanical, low risk)** — package extraction of pure-function
  modules: `pfp_package`, `graphify/extract`, `install_bootstrap`,
  `skill_marketplace`, `fs_actions`, `cli.py`, `command_dispatch`.
- **Tier 2 (multi-class, low–medium)** — one-class-per-module splits:
  `capabilities`, `media`, `task_management`, `web_fetch`, `resource_agent`,
  `http_listener_service`, `agent_executor`, `telegram_bot_service`,
  the two proxy tools, the two session mixins.
- **Tier 3 (mixin decomposition, medium)** — the agent mixins and the big
  stateful classes (precedent exists, but shared state is real):
  `agent_core`, `agent_context`, `agent_compaction`, `agent_utils`,
  `conversation_store`, the LLM provider mixins, the pools/registries.
- **Tier 4 (dispatcher decomposition, high)** — the mega-method action files:
  `service_flow` (one ~4,400-line method), `agent_resource`, `conversation`,
  `context_ops`, `files_fs`.
- **Tier 5 (JS)** — `chat_ui/*.js`, VS Code webview.

---

# Per-file plans

Line ranges are from the analysis snapshot; treat them as cut anchors, confirm
at execution. “→ N files” is the target count, each ≤ 800 lines.

## tasks/ai/actions/ — dispatcher decomposition (Mechanism B)

These files are dominated by one `_handle_*(self, action, body, store, user_id,
flowfile)` method that switches on `action`. The split is: keep a thin
dispatcher that routes `action` → sub-handler functions in sibling modules
under a `tasks/ai/actions/<name>/` package. Sub-handlers take the same
context args explicitly (invariant 5). The routing table must stay exhaustive
(invariant 4).

### service_flow.py (5383) → ~8 files. **Highest risk.**
- Shape: ~43 module helpers (L15–960) + **one ~4,400-line method**
  `_handle_service_flow` (L963–5369) + `_find_http_listener` (L5372).
- Target package `tasks/ai/actions/service_flow/`:
  - `routes.py` — vnc/terminal/code-server route helpers (L15–247, 250–270).
  - `scope.py` — scope/admin/category/sort/connected-state helpers
    (L276–471).
  - `credentials.py` — credential provider/module + token stores (L474–534).
  - `templates.py` — flow-template resolution/storage/rewrite (L537–691).
  - `schema.py` — parameter/services/deploy/one-shot schema builders
    (L694–836).
  - `instances.py` — instance config/restart/binding refresh (L839–960).
  - `handler.py` — the dispatcher: split `_handle_service_flow` by its
    `action ==` branches into `_action_<name>(self, body, store, …)`
    sub-handlers (services list/start/stop, flow deploy/template, vnc/login,
    …). Group sub-handlers into 2–3 modules (`actions_service.py`,
    `actions_flow.py`, `actions_vnc.py`) so each stays ≤ 800.
  - `__init__.py` — re-export `_handle_service_flow` (now thin) + public names.
- Hinge: the mega-method shares many locals across branches — map them before
  cutting; pass explicitly or hang on a small `_Ctx` dataclass. Routing must
  remain exhaustive; add a fallthrough assertion.

### agent_resource.py (2742) → ~4 files.
- Shape: 9 flow-template helpers (L19–288) + `_handle_agent_resource`
  (L291–2741, ~2,450 lines).
- Target `tasks/ai/actions/agent_resource/`: `templates.py` (cache/scan/overlay
  helpers L19–288), `handler.py` (thin dispatcher), `actions_agent.py` +
  `actions_resource.py` (per-action sub-handlers split by branch). `__init__`
  re-exports `_handle_agent_resource` and `invalidate_flow_templates_cache`.

### conversation.py (1486) → 2–3 files.
- Shape: 8 archive/identity helpers (L18–263) + `_handle_conversation`
  (L266–1485).
- Split: `archive.py` (zip/manifest/filestore archive+restore L18–263),
  `handler.py` (dispatcher + per-action sub-handlers). Re-export.

### context_ops.py (1613) → 2–3 files.
- Shape: 9 session-loader helpers (L17–386, cc/codex/gemini session readers) +
  `_handle_context_ops` (L389–1612).
- Split: `session_loaders.py` (L17–386), `handler.py` (dispatcher). Re-export.

### files_fs.py (1015) → 2 files.
- Shape: 10 flow-graph helpers (L16–279) + `_handle_files_fs` (L282–1014).
- Split: `flow_graph.py` (L16–279), `handler.py` (dispatcher). Re-export.
  Marginally over 800 — a clean two-way split suffices.

### command_dispatch.py (2010) → 3 files. (Tier 1 — mostly functions.)
- Shape: many `_parse_*` free functions (L747–1890) + `_handle_command_dispatch`
  (L1895–1948) + `_handle_help` (L1951–2009).
- Split into a `command_dispatch/` package: `parsers_core.py`
  (`_extract_at_agent`, `_parse_command` L747–1285), `parsers_domain.py`
  (agent/skill/pfp/task/goal/service/flow/memory/schedules/… parsers
  L1290–1890), `dispatch.py` (`_handle_command_dispatch`, `_handle_help`).
  Re-export public parser entry points.

## tasks/ai/ — agent loop mixins (Mechanism A)

Precedent: `agent_loop.py` composes these mixins. Split each oversized mixin
into sub-mixins recombined on the same host. Shared state via `self`.

### agent_core.py (3165) → ~5 files.
- Shape: 6 module helpers (L30–117) + `AgentCoreMixin` (L120–3164, **5 very
  large methods**).
- Split: `_budget.py` (cost/budget/rate helpers L77–117), then break
  `AgentCoreMixin`'s 5 methods into sub-mixins by phase — e.g.
  `AgentCoreTurnMixin`, `AgentCoreToolMixin`, `AgentCorePreemptMixin` — each in
  its own module, recombined as `AgentCoreMixin(TurnMixin, ToolMixin, …)` in
  `agent_core.py`. Because the methods themselves are huge, also extract
  internal helper functions per method. Hinge: methods call each other via
  `self.` — keep them on one MRO.

### agent_context.py (2035) → ~3 files.
- `AgentContextMixin` (5 methods, L56–2033) + `_find_agent_md` (L26).
- Split methods into `context_assembly`/`context_budget`/`context_sources`
  sub-mixins. Re-export `AgentContextMixin`.

### agent_compaction.py (1583) → 2 files.
- `_select_recent_messages` (L29–83) + `AgentCompactionMixin` (15 methods).
- Split into `AgentCompactionMixin` + `AgentSummaryWriteMixin` (or move the
  selection/window helpers to `_compaction_select.py`).

### agent_utils.py (1323) → 2 files.
- 4 helpers + `_MediaServiceRef` + `AgentUtilsMixin` (43 methods).
- Split methods into two sub-mixins by topic (media/service helpers vs general
  utils); keep `_MediaServiceRef` with the media cluster.

### agent_poller.py (1098) → 2 files.
- `_check_task_limits` + `AgentPollerMixin` (12 methods). Split poll-loop vs
  task-limit/scheduling methods into two sub-mixins.

### agent_actions.py (1082) → 2 files.
- 10 module-level UI-action-status/cache helpers (L76–227) + `AgentActionsMixin`
  (12 methods). Move the UI-action-status + list-cache helpers to
  `_action_status.py`; keep the mixin. Likely lands the mixin file ≤ 800.

### agent_loop.py (932) → 2 files.
- `AgentLoopTask` (23 methods) — already the mixin host. Extract a cohesive
  method cluster (e.g. lifecycle/setup vs run-loop) into one more
  `AgentLoopSetupMixin`. Marginal; small extraction.

### agent_summarize.py (958) → 2 files.
- 4 helpers + `AgentSummarizeMixin` (7 methods). Move text-shaping helpers
  (`_strip_analysis_wrapper`, `_truncate_head`, `_compact_scope_id`) to
  `_summarize_text.py`; mixin file drops under 800.

### agent_streaming.py (902) → 2 files.
- `AgentStreamingMixin` (4 methods, large). Split the 4 methods into
  stream-parse vs stream-emit sub-mixins.

## core/ — big stateful classes (Mechanism A) & function modules (C)

### conversation_store.py (4863) → ~7 files. **High risk.**
- Shape: `ConversationLockedError`, `_ConversationTimedRLock` (L72–144),
  `ConversationStore` (**195 methods**, L147–4862).
- Split into a `core/conversation_store/` package, `__init__.py` re-exporting
  `ConversationStore`, `ConversationLockedError`. Decompose the class into
  sub-mixins by responsibility (group the 195 methods): `_persistence.py`
  (segmented-jsonl read/write), `_locking.py` (lock + `_ConversationTimedRLock`),
  `_messages.py` (append/edit/list), `_compaction.py`, `_indexing.py`,
  `_membership.py`, `_metadata.py`. `ConversationStore(PersistenceMixin,
  LockingMixin, …)` in `_store.py`.
- Hinge: all mixins share `self._lock`/segment handles; one MRO; verify no
  method-name collisions (195 methods — grep for dupes). This file backs the
  conversation store tests — run `test_conversation_store.py` per step.

### pfp_package.py (3399) → ~6 files. (Tier 1, function-dominated.)
- Shape: `PfpError` + ~120 free functions in clean clusters.
- Target `core/pfp/` package, `__init__.py` re-exporting the public API
  (`build_pfp`, `inspect_pfp`, `install_pfp`, `update_pfp`, `uninstall_pfp`,
  `list_installed_packages`, `dev_load_pfp`, …):
  - `build.py` — build/inspect/export/signing (L108–230, 754–868).
  - `install.py` — install/update/uninstall/dev-load + `_install_object`,
    proxy loaders (L241–579, 1450–1861).
  - `capabilities.py` — capability/secret aggregation (L1238–1447).
  - `objects.py` — plan/diff/object helpers (L1051–1235, 2150–2586).
  - `dependencies.py` — version + dependency resolution (L2589–3139).
  - `verify.py` — lock/signature/key/json io (L926–1048, 3216–3397).
  - `ui_extensions.py` — ui-extension validate/manifest/resolve
    (L1864–2122).
- Hinge: pure functions, few cross-imports — lowest risk of the giant files.

### claude_code.py (3066) → ~5 files. (Mechanism A.)
- Shape: `_CC401Retry` + `LLMClaudeCodeMixin` (15 **huge** methods).
- Split the mixin by phase into sub-mixins: `_session.py` (already partly in
  `claude_code_session.py`), `_stream.py` (event stream parse/relay),
  `_tools.py` (tool-call unwrap/exec), `_lifecycle.py` (spawn/connect/retry).
  `LLMClaudeCodeMixin` recombines them. Hinge: the `_pub` event path + tool
  unwrap (recently touched) must stay intact.

### gemini.py (1824) → ~3 files.
- `LLMGeminiMixin` (55 methods) + 2 error classes. Split into
  ACP-protocol / streaming / tool-exec sub-mixins.

### codex_app_server.py (1807) → ~3 files.
- `LLMCodexAppServerMixin` (42 methods) + error class. Same pattern: protocol /
  streaming / tooling sub-mixins.

### llm_client.py (1492) → ~3 files. (Mechanism C+D.)
- Shape: dataclasses (`LLMToolDefinition/Call/Result/Message/Response`),
  unwrap/seq helpers, `LLMClient` (37 methods), error classes.
- Split: `types.py` (dataclasses + `unwrap_mcp_tool`/`_decode_str_arg`,
  L78–308), `_seq.py` (persisted-seq helpers L323–441), `client.py`
  (`LLMClient`), `errors.py`. `llm_client.py` re-exports everything (heavily
  imported — invariant 1 critical).

### agent_executor.py (1403) → ~3 files. (Mechanism D.)
- Shape: live-delegate registry functions (L41–113), `AgentTask`/`AgentResult`,
  `SubAgentExecutor` (12 methods), `resolve_agent_task`.
- Split: `live_delegate.py` (registry fns), `executor.py` (`SubAgentExecutor`),
  `types.py` (`AgentTask`/`AgentResult` + depth helpers). Re-export.

### bg_bucket_builder.py (1334) → 2 files.
- `BgBucketBuilder` (38 methods) + `_build_embed_fn`. Split embed/index methods
  vs build/query methods into a base + mixin, or extract the embedding pipeline
  into `_bg_embed.py`.

### pfp_runtime.py (1241) → ~3 files. (Mechanism C+D.)
- Classes (`RelayPackageRuntimeBridge`, `PackageRuntimeHost`) + ~50 free
  functions (invocation builders, secret env, resolvers).
- Split: `bridge.py` (the two classes), `invoke.py` (build_*/invoke_* +
  normalizers), `resolve.py` (package tool/service resolvers + flowfile
  helpers). Re-export.

### service_registry.py (1085) → 2 files.
- `ServiceDef` + `ServiceRegistry` (50 methods) + module helpers. Split
  `ServiceRegistry` into a CRUD/persistence mixin + a
  lookup/scope/runtime mixin; keep `ServiceDef` with helpers in `_defs.py`.

### claude_code_interactive_pool.py (1071) → 2 files.
- `InteractiveContainer` + `InteractiveClaudeCodePool` (49 methods). Split pool
  into container-lifecycle vs request-routing sub-mixins.

### segmented_jsonl.py (931) → 2 files.
- `SegmentedJsonl` (45 methods). Split read/scan vs write/rotate/compact
  methods into a base + mixin.

### repository.py (900) → 2 files.
- `ScopedRepository` (39 methods) + `_copytree_content`. Split CRUD vs
  scope/copy/sync methods. Marginal — small extraction.

### claude_code_interactive.py (922) → 2 files.
- `_CCITurnCoordinator` (17 methods, L107–628) + `LLMClaudeCodeInteractiveMixin`
  (11 methods) + module fns. Move `_CCITurnCoordinator` to
  `_cci_turn.py`; keep mixin + small helpers. Clean two-way split.

### claude_code_session.py (976) → 2 files.
- ~11 credential-pool free functions (L31–279) + `ClaudeCodeSessionMixin`
  (10 methods). Move pool functions to `_cc_credentials.py`; keep the mixin.

### codex_session.py (885) → 2 files.
- Same shape as above: pool functions (L38–345) + `CodexSessionMixin`. Move
  functions to `_codex_credentials.py`.

### server_relay_manager.py (882) → 2 files.
- ~17 naming/path/config helpers (L62–261) + `ServerRelayManager` (19 methods).
  Move helpers to `_relay_naming.py`; keep the manager.

### skill_marketplace.py (823) → 2 files. (Tier 1.)
- ~50 free functions: public API (search/import/fetch/resolve) + GitHub/source
  backends. Split: `marketplace.py` (public API + dedupe/rank/preview),
  `_sources.py` (codex/claude/hermes/openclaw/github backends). Re-export.

## core/handlers/ — multi-class handler files (Mechanism D)

Each handler is an independent class registered in a handler registry. Split
one class (or small cluster) per module; keep registration reachable
(invariant 4) by re-exporting from the original module or registering in
`__init__`.

### resource_agent.py (1801) → 4 files.
- `ManageResourceHandler` (L16–553), `SpawnAgentsHandler` (L556–1416),
  `FlashAgentHandler` (L1419–1649), `ShowFileHandler` (L1652–1800). One module
  each (`manage_resource.py`, `spawn_agents.py`, `flash_agent.py`,
  `show_file.py`); `resource_agent.py` re-exports all four.

### web_fetch.py (1426) → 3 files.
- `ExecuteScriptHandler`, `WebSearchHandler` (28 methods, L275–936),
  `ScraplingFetchHandler`. One module each. `WebSearchHandler` alone may need a
  helper-method extraction to stay ≤ 800.

### capabilities.py (1273) → ~3 files.
- `_CapabilityHandlerBase` + **13 small handler classes** (3D, upscale, describe,
  remix, bg-remove, tryon, lipsync, train, s2v, clone-voice, speak, delete-voice).
  Group by domain: `_base.py` (base + shared helpers), `image_caps.py` (image
  handlers), `av_caps.py` (audio/video/voice handlers). Re-export all.

### media.py (997) → 2 files.
- 5 generation handlers + 3 helpers. Split `image.py`
  (ImageGeneration/EditImage/ImageModelInfo) and `av.py`
  (VideoGeneration/AudioGeneration). Re-export.

### task_management.py (972) → 2 files.
- 4 module fns (`wake_agent_poller`, `schedule_agent_task_wake`,
  `_activate_dependents`, `_append_task_log`) + 4 handler classes
  (Link/Assign/Complete/Verify). Split `_task_helpers.py` (the 4 fns) +
  handlers; or `assign.py` (`AssignTaskHandler` is 408 lines) separate from the
  rest. Re-export.

### _fs_base.py (847) → 2 files.
- Module fns (L31–169) + `BaseFsHandler` (39 methods). Move free helpers
  (`_expand_glob_braces`, `find_fs_service`, `get_tool_relay_env`,
  `cap_binary_output`) to `_fs_helpers.py`; keep `BaseFsHandler`. Marginal.

## services/ — mixin (A) & multi-class (D)

### tool_relay_service.py (2544) → ~4 files.
- Module fns (transport markers, var-resolve, redact L57–132), `ToolRelayService`
  (46 methods, L135–2381), secret-resolution fns (L2384–2489), cancel/kill-hook
  registry (L2512–2543).
- Split: `_transport.py` (markers + redact), `_secrets.py`
  (resolve_secrets_env/values + key registry), `_cancel.py` (cancel/kill
  hooks), `service.py` (`ToolRelayService` split into a connection mixin + a
  tool-exec mixin if still > 800). Re-export.

### http_listener_service.py (2055) → ~5 files. (Mechanism D.)
- Many classes: rate limiter, `PendingRequest`, `RouteRegistry`,
  `_RequestHandler` (L399–1008), `_PrefixedSocket`/`_ConcatReader`,
  `_HTTPServerWithRegistry` (L1091–1639), `HTTPListenerService` (L1652–2049).
- Split: `_ratelimit.py`, `routes.py` (`RouteEntry`/`RouteConflictError`/
  `RouteRegistry`), `request_handler.py` (`_RequestHandler` + socket helpers),
  `server.py` (`_HTTPServerWithRegistry`), `service.py`
  (`HTTPListenerService` + `PendingRequest` + timing helpers). Re-export.

### filesystem_service.py (1646) → ~3 files.
- ~9 ws/relay module fns (L35–270) + `RelayService` (74 methods). Move ws-frame
  + relay-sync helpers to `_relay_ws.py`; split `RelayService` into a
  connection/sync mixin + an fs-op mixin.

### antigravity_observer_pool.py (1592) → ~3 files.
- `AntigravityObserverSession` + `AntigravityObserverPool` (70 methods). Move
  the session class to `_ag_session.py`; split the pool into
  container-lifecycle vs observe/route sub-mixins.

### voicebox_service.py (1120) → 2 files.
- 3 module fns + `VoiceboxService` (44 methods). Split TTS vs STT/clone methods
  into a base + mixin, or extract the HTTP/multipart plumbing to `_voicebox_io.py`.

### telegram_bot_service.py (982) → 2 files.
- `TelegramBotService`, `_BotState`, `TelegramBotPool` + many send/split free
  fns (L659–981). Move the send-channel + text-split helpers to
  `_telegram_send.py`; keep the service + pool.

### _pixazo_base.py (946) → 2 files.
- 6 module fns (catalog/url/error L62–210) + `_PixazoBaseService` (22 methods).
  Move catalog/url/error helpers to `_pixazo_helpers.py`. Marginal.

## engine/

### continuous_executor.py (1481) → 2 files.
- `TaskStats`, `ExecutionResult`, `ContinuousFlowExecutor` (39 methods). Move
  dataclasses to `_exec_types.py`; split the executor into a
  scheduling/poll mixin + a run/result mixin.

### nifi_converter.py (821) → 2 files.
- `ConversionWarning`/`ConversionResult` + `NiFiConverter` (15 methods). Move
  dataclasses + module constants (L1–175) to `_nifi_types.py`; keep the
  converter. Marginal.

## core/ install / graphify (Mechanism C, Tier 1)

### install_bootstrap.py (2037) → ~4 files.
- ~60 free functions in phase clusters. Target `core/install/` package,
  `__init__` re-exporting `ensure_install_bootstrap`, `finalize_install`,
  `get_install_status`, `save_llm_credential`, `prepare_llm_credential_pool`:
  - `cert.py` (self-signed cert + tls/listener config L73–236).
  - `state.py` (load/write/status/secrets L239–397).
  - `gateway.py` (bootstrap/final private gateway + admin user L400–523).
  - `credentials.py` (llm credential pool + specs L526–1214).
  - `finalize.py` (install services + smoke checks + finalize/rollback
    L1217–2036).

### graphify/extract.py (2228) → ~4 files.
- Per-language extractors + generic core. Target `core/graphify/extractors/`:
  - `_base.py` (`LanguageConfig`, `_make_id`, `_find_body`, `_resolve_name`,
    `_extract_generic` L14–954, `collect_files`, `extract` dispatcher).
  - `c_family.py` (c/cpp/csharp/java/kotlin/scala/php imports + walkers).
  - `scripting.py` (python/js/ruby/lua/swift/powershell/elixir).
  - `systems.py` (go/rust/zig + `_resolve_cross_file_imports`).
  Keep the top-level `extract`/`extract_<lang>` names re-exported from
  `extract.py` (invariant 1; the `extract_<lang>` registry must stay intact).

## tools/ (Mechanism C/D, Tier 1–2)

### fs_actions.py (1522) → ~3 files. (Tier 1.)
- ~45 `action_*` + helper functions. Split: `_fs_read.py` (list/read/pdf/
  notebook/stat/exists/search), `_fs_grep.py` (grep + glob helpers),
  `_fs_edit.py` (edit/batch_edit/find_replace/patch + diagnose). Re-export all
  `action_*` (they are the tool entry points — registry must keep resolving).

### cc_interactive_proxy.py (1165) → ~3 files. (Mechanism D.)
- Many observer classes + pipe/main. Split: `wire.py` (WireLogger/AsyncWireLogger
  + scrub/redact), `observers.py` (HTTP/SSE/JSON observers), `proxy.py`
  (`handle_client`, `_pipe_exact`, `main`). Keep `main` entry point.

### ag_observer_proxy.py (973) → 2 files.
- HTTP1/HTTP2 observers + ~25 extraction free fns + `handle_client`/`main`.
  Split: `_extract.py` (the `_extract_*`/`_semantic_*`/`_normalize_*` fns),
  `proxy.py` (observers + client + main).

## pawflow_cli/ & cli.py

### cli.py (857) → 2 files. (Tier 1.)
- `cmd_*` functions + `main`. Move the bulkier commands (`cmd_start`
  L236–470, `cmd_triggers` L501–611, `cmd_admin_user`) to `cli_commands.py`;
  keep `main` + light commands in `cli.py`. Marginal.

### pawflow_cli/app.py (1435) → ~2 files.
- `PawCode` (33 methods, L57–1160) + module fns + `main`. Split `PawCode` into a
  connection/session mixin + a command/render mixin; keep `main` +
  `_normalize_server_url`/`_prompt_first_run_setup` in `app.py`.

### pawflow_cli/ui/renderer.py (842) → 2 files.
- Module fns (color/diff/summary L41–128) + `TerminalRenderer` (33 methods).
  Move the free helpers to `_render_helpers.py`. Marginal.

## pawflow_relay/ (re-sync vendored copies after)

### worker.py (2372) → ~4 files.
- `FSRelayHandler` (L126–249) + `_make_handler_class` + **`_ws_connect`
  (L284–2367, ~2,080 lines)**. `_ws_connect` is a mega-function with nested
  message handlers. Split: `_fs_handler.py` (`FSRelayHandler` + tmp-allowlist),
  then decompose `_ws_connect` by message-type — extract per-`method` handler
  functions into `_relay_methods.py` / `_relay_fs_methods.py`, leaving
  `_ws_connect` as a thin dispatch loop. Hinge: nested handlers close over
  connection state — pass an explicit conn context object.

### thread.py (1343) → 2 files.
- ~12 container/path free fns (L29–151) + `RelayThread` (27 methods). Move the
  container-naming/apparmor/path helpers to `_relay_container.py`.

## tasks/io/

### telegram_agent_client.py (1723) → ~4 files. (Mechanism C/D.)
- `TelegramAgentClientTask` (L46–467), ~50 free helpers (wizard, formatting,
  tts/stt, dedup), `TelegramConversationBridgeTask` (L892–1233), more helpers.
- Split: `client_task.py` (`TelegramAgentClientTask`), `bridge_task.py`
  (`TelegramConversationBridgeTask`), `_wizard.py` (new-conversation wizard +
  keyboards L470–889), `_render.py` (telegram text/badge/thinking/tts/stt
  helpers L1236–1717). Keep `TaskFactory.register` reachable (invariant 4).

## tasks/io/chat_ui/ + VS Code — JS (Mechanism E)

ES modules — split by feature with `import`/`export`. No mixins; preserve the
module's public exports and any global wiring/event registration.

- **sse.js (2033) → ~3 modules** — connection/reconnect, event-dispatch,
  per-event-type handlers.
- **messages.js (2022) → ~3 modules** — message model/state, render, tool-block
  rendering.
- **conversations.js (1485) → 2 modules** — list/state vs actions.
- **vscode chat.js (1212) → 2 modules** — view vs message handling.
- **terminal.js (926) → 2 modules** — terminal I/O vs UI.
- **commands.js (882) → 2 modules** — command parse vs command UI.

Hinge: confirm the build/bundling (if any) and `<script>`/import wiring so
split modules still load in order; preserve every event listener registration.

---

## Execution checklist (per file)

1. Read the whole file; map shared state, cross-method/function calls, and
   external import sites (`grep` the symbol across the repo).
2. Choose mechanism (A–E) per the shape above; draw module boundaries on
   cohesive clusters, each ≤ 800 lines.
3. Move code; add `__init__.py`/re-exports so every external import path is
   unchanged (invariant 1).
4. Preserve registrations and routing exhaustiveness (invariant 4); keep
   deliberate late imports late with `# noqa: E402`.
5. `python -m compileall` the touched tree; `ruff` clean; run the file's
   targeted tests, then the full suite (baseline 5382 passed).
6. One file per commit; push; wait for CI green (lint + test 3.10–3.13)
   before the next file.
7. After `pawflow_relay/` + `tools/` splits land, re-sync the vendored runtime
   copies (`.pawflow-runtime/`, `280890741dad4975/`,
   `pawflow-relay-desktop/runtime/`) from source.
