# Changelog

All notable changes to PawFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- Interactive session shutdown now closes event readers and releases capture-owned
  activity markers, preventing completed flash delegates from remaining active or
  restarting captures after their containers have been removed.
- Inspecting a flash delegate preserves the selected conversation agent and the
  composer target instead of attempting to select an agent outside the roster.
- Sidebar context menus remain open when incoming messages auto-scroll the chat;
  internal menu scrolling also preserves the popup, while scrolling its source
  panel or the page still dismisses it.

### Changed

- Complete the README provider table with Antigravity / Agy ACP, Cursor ACP,
  Grok Build ACP, and OpenCode, linking their existing configuration guides.
- Remove outdated release highlights and stale provider-selection guidance from
  the README; keep implementation history in the changelog and provider guides.

## [1.0.0-beta.268] — 2026-09-05

### Added

- Native Cursor ACP and Grok Build ACP connectors, with managed containers,
  persistent authentication scoped to each user and service, exact workspace
  mounts, session reuse, cancellation, and PawFlow MCP tool access.
- OpenCode server integration using its SDK v2 HTTP/SSE protocol, isolated
  sessions, native questions and permissions, streaming usage, and managed
  runtime cleanup.
- Cursor, Grok Build and OpenCode installation and version inventory in the
  tools image, plus service actions for native login, credential presence,
  version checks and managed updates.

### Fixed

- Native questions now preserve provider option identities and explicit user
  choices across Antigravity ACP, Cursor, Grok, OpenCode, Claude Code and Codex
  app-server. Cancellation, late replies and disconnects close pending requests
  without granting unrelated permissions or selecting a default answer.
- Claude Code control and hook replies preserve exact question answers;
  serialized stdin writes prevent concurrent prompt and control messages from
  interleaving. Terminal errors remain failures.
- Stateful native providers participate in context tracking and the no-replay
  policy, preventing automatic retries from repeating a session turn.
- Native service authentication status reports configured provider environment
  variables without exposing values or claiming that credentials are valid.

### Changed

- Document Claude Code subprocess and Codex app-server as supported native
  integrations, with their authentication and interaction contracts.
- Document native connector configuration and validation limits. Offline CLI
  checks cover initialization, questions and local transports; authenticated
  model calls and interactive login UI checks remain unverified.

## [1.0.0-beta.267] — 2026-09-04

### Changed

- Provider-specific HTTP headers are no longer hard-coded. `llmConnection`
  services gained `extra_headers`, a JSON object rendered through the
  expression language with a `request.*` scope (`${request.session_id}`,
  `${request.conversation_id}`, `${request.user_id}`, `${request.agent_name}`,
  `${request.request_id}`, `${pawflow.version}`, plus parameters and secrets).
  OpenCode Go's `x-opencode-session` is now
  `{"x-opencode-session": "${request.session_id}"}` on the service; the
  session id is the conversation id inside a conversation and a stable
  per-service id outside one, so background calls no longer fail for lack of
  a conversation. `Authorization`, `x-api-key`, `Content-Type` and transport
  headers cannot be overridden; the PawFlow `User-Agent` can.
- `agy_mcp` is now enabled on Google's published hook contract
  (antigravity.google/docs/hooks) instead of a protobuf identifier: `Stop`
  fires when the execution loop terminates with a camelCase payload that
  names the persistent transcript, and the final answer is read from that
  transcript. `finalModelOutput` is not part of the documented payload and is
  only used when present. `evaluate_probe` reports `evidence_kind`
  (`observed` / `documented` / `schema_only` / `none`) and `final_source`;
  schema or changelog evidence alone never enables the provider. The fixture
  is re-recorded against `agy` 1.1.26 with the documented field lists.

### Fixed

- ACP registry binary imports confine raw command paths before writing, reject
  unsafe version paths, recheck cached archive digests, and stream downloads to
  disk instead of holding large archives in memory.
- WebChat task blocks and terminal output carry explicit projection identities,
  including task iterations, so adding or updating these rows no longer stops
  message reconciliation.
- Project graph format v2 scopes node IDs by source path, tracks nanosecond/size
  fingerprints, and excludes generated/vendor bundles from discovery and ranking.
  Cross-file imports resolve after incremental graph merging; missing or
  ambiguous targets remain separate external references.
  External references are excluded from report and digest hub rankings so
  heavily imported third-party symbols do not displace application hubs.
- The shared non-streaming HTTP transport preserves a configured `User-Agent`
  regardless of header-name casing and adds PawFlow's identity only when absent.
- The `openai` provider no longer kills a turn with
  `TypeError: 'NoneType' object is not iterable` when a gateway sends explicit
  JSON `null` for `delta`, `tool_calls`, `function`, `arguments`, `message` or
  `usage` (observed on OpenCode Go serving `glm-5.3-flash`). Nulls now read as
  absent fields on both the streaming and non-streaming paths.
- `agy_mcp` managed hooks now follow the documented `hooks.json` shape
  (`PreInvocation`/`Stop` handlers listed directly under the event, no
  tool-event wrapper, no undocumented `SessionEnd`) and pass `--event` on each
  handler command line, because Antigravity payloads never name their event.
  Without this the lifecycle hook could not classify a single Agy event.
  `MANAGED_MCP_LAUNCH_REVISION` is bumped so existing managed Agy sessions are
  recreated with the new hook files.

## [1.0.0-beta.266] — 2026-09-04

### Added

- Tiled WebChat workspaces now highlight the focused surface and allow a tile
  title bar to be dragged onto another tile or an empty slot. The resulting DOM
  order and slot assignments persist in `pawflow.workspace.state.v2`, while
  title-bar actions remain independently clickable.

### Fixed

- PawFlow-owned direct LLM HTTP traffic now sends the versioned
  `PawFlow/<version>` User-Agent consistently for inference, model discovery,
  streaming, and OAuth refresh without mixing provider-specific wire dialects.
  OpenCode Go requests under `https://opencode.ai/zen/go/` additionally send the
  required PawFlow conversation ID as `x-opencode-session`; the header cannot
  leak to another host or path, and a missing conversation now fails explicitly.
- WebChat buttons now share the prompt-bar surface contract after theme and
  operator CSS: transparent with no visible border at rest, then an accent
  border, zoom, and accessible tooltip on hover or keyboard focus. Existing
  amplitudes are preserved—1.4x for compact docks and 1.08x for ordinary
  buttons—and semantic state remains visible through glyph or text color.
- The desktop sidebar now slides its complete fixed-width shell, mounted content,
  and grip as one interruptible rail without shifting workspace geometry.
  Disclosure cleanup also restores pre-existing height, opacity, and overflow
  styles, so bounded Resources sections such as Services retain wheel and
  trackpad scrolling after open/close transitions.

## [1.0.0-beta.265] — 2026-09-04

### Added

- Added `antigravity-acp`, an `llmConnection` provider that runs Google's
  official Antigravity ACP server (`agy_acp_server`, ACP registry entry
  `antigravity-acp`) inside the `pawflow-claude-code` image and drives it
  through the shared ACP runtime as a plain ACP client. The server is baked
  into the image with a pinned version and archive digest, `GEMINI_HOME` is
  kept per `(user, service)` so one login serves every conversation, the four
  advertised authentication methods are selectable, environment entries are
  forwarded by name so secrets never enter argv, and PawFlow's MCP bridge is
  always exposed. `AcpProcessSession` gained `stderr_path` so a verbose agent
  can no longer block on an unread stderr pipe. `antigravity-interactive` and
  `agy_mcp` remain available. See `docs/ANTIGRAVITY_ACP.md` and
  `docs/ACP_REGISTRY_ANTIGRAVITY_PLAN.md`.
- Added the `antigravity-acp` server-side login: the service action **Login
  via server (Antigravity ACP)** reuses the Antigravity noVNC dialog, runs
  `docker/claude-code/agy_acp_auth_login.sh` in the `pawflow-claude-code`
  image, and `agy_acp_login.py` drives one `initialize` + `authenticate` round
  trip against the ACP server while the in-container browser completes the
  OAuth redirect. The token stays in the service `GEMINI_HOME`; the action
  result only reports success, the method used, and the advertised methods.
- Added `core/acp/registry.py`, a client for the public ACP registry: HTTPS
  only fetch with a 24 h cache and stale fallback, validation against the
  vendored `registry_schema.json`, quarantine and protocol-matrix
  annotations, digest-verified extraction of `binary` distributions with
  installer formats and path escapes refused, and `acp` service configurations
  for `npx`/`uvx`/`binary` entries with the imported version pinned and an
  update check that never upgrades on its own.
- Added **Import from ACP registry** and **Check ACP registry updates** to
  `acp` services in Resources › Services (`tasks/ai/actions/_sf_acp_registry.py`,
  service actions `acp_registry_catalogue`, `acp_registry_prepare`,
  `acp_registry_prepare_status`, `acp_registry_check_update`). The picker
  shows license, authentication types and quarantine state, offers only
  distributions this server can run, downloads binaries as a polled background
  job, fills the form, and leaves saving to the normal submit. The import is
  recorded in the new `acp_registry` field so updates can be checked without
  ever upgrading in place.
- Added a native WebChat motion and interaction system: shared read/write and
  replaceable-animation ownership, interruptible accessible disclosures, dirty
  keyed transcript projections, stable Resources/workflow reconciliation,
  centralized floating-layer placement and cleanup, generation-owned action
  faces, and visibility-aware live cues. Sidebar, view, and progress motion use
  transforms; reduced motion removes temporal/decorative work; and deterministic
  Chromium gates cover 500/1,000-row streaming, desktop/mobile geometry and
  screenshots, CDP tracing, and a 100-cycle lifecycle soak.
- The `pawflow-claude-code` image now installs `ripgrep` and GNU `time` like
  the server and relay images, so tools running inside it use `rg` instead of
  a slower fallback scan.

### Fixed

- `read_history` now excludes its own nested tool-result copies from normal
  searches before segment decoding, while explicit `role_filter="tool"`
  searches still expose them. Exact-phrase candidates are resolved before the
  keyword fallback, multi-term prefilters reject weak one-token segments, and
  indexed tail reads skip unrelated earlier transcript segments.
- WebChat's top grip now follows the header's painted separation line throughout
  its 500 ms transition instead of drifting on an independently started `top`
  animation. The desktop tab rail also uses a 500 ms counter-transformed content
  layer, so its buttons remain present during both opening and closing instead of
  appearing only after the rail settles. The bottom expander and 300 ms workspace
  tile motion are unchanged.

## [1.0.0-beta.264] — 2026-09-04

### Fixed

- Completed `agy_mcp` as a selectable managed native-hook provider. The Agy
  hook bridge now normalizes the CLI's native `StopHookArgs.finalModelOutput`,
  the provider and manual-capture paths resolve `AntigravityObserverPool`, and
  the pool implements the shared managed-turn lifecycle without falling back to
  tmux scraping or vendor-traffic interception.
- `read_history(action="search")` no longer JSON-decodes every earlier segment
  to calculate absolute result indices, and a server image without ripgrep no
  longer falls back to decoding the full transcript. Segment indexes now retain
  exact display-row counts, old indexes upgrade with a lightweight bounded
  scan, and search uses ripgrep, standard grep, or a dependency-free candidate
  scan before decoding only matching segment neighborhoods.
- In a tiled multi-conversation workspace, background Active Agents polls and
  late CLI terminal-inventory results can no longer repaint the focused
  conversation's shared controls. Grab now invalidates its live-session cache on
  focus changes, rejects stale results by conversation and generation, and only
  renders from the focused `ConversationSession`, so an equally named agent in
  another tile cannot make the button appear or disappear.

## [1.0.0-beta.263] — 2026-09-03

### Added

- Added `cc_mcp` and `codex_mcp` as managed CLI providers that reuse the
  existing interactive pools and PawFlow MCP bridge while taking final answers
  from native lifecycle hooks instead of intercepted vendor traffic. Capability
  reporting distinguishes native, unavailable, and final-only telemetry;
  `agy_mcp` is registered but remains probe-gated until the supported CLI
  proves a trustworthy native final-answer source.
- Added the outbound `acp` provider: an `llmConnection` can launch any Agent
  Client Protocol v1 agent command (`acp_command`, `acp_args`, `acp_cwd`,
  `acp_env`) without a shell, negotiate the official protocol through the
  pinned `agent-client-protocol` SDK, keep the process warm, load persisted
  sessions when the agent advertises them, and expose only the explicitly
  enabled PawFlow MCP bridge and client filesystem methods. Client reads go
  through PawFlow policy and approval rules; a client write additionally
  consumes a matching ACP edit permission. Accepted prompts are never replayed
  (`NO_REPLAY_PROVIDERS`), cancellation reaches the process, and policy-gate
  approvals now accept a `cancel_event` so a cancelled turn resolves as
  cancelled instead of waiting out the approval timeout. The `pawflow-acp`
  console script ships the stdio-to-WebSocket client proxy for published
  PawFlow agents; the inbound server endpoint it targets lands in a later beta
  (docs/ACP_INTEGRATION_PLAN.md WP4).

### Fixed

- OAuth credential pools now honor an explicit `allow_refresh` policy with
  provider-specific defaults. Disabling it prevents PawFlow-managed proactive,
  retry, bearer, and manual refreshes while preserving credentials updated by
  the native CLI; Gemini and shared Antigravity pools default to disabled.
- Importing the background-tool manager no longer keeps short-lived workers
  and test processes alive for its 60-second cleanup interval: the initial
  periodic cleanup timer is now daemonized like every later recurrence.
- Delegate observability is now transcript-backed and survives restarts.
  Shared requests and replies carry the same `task_id`, isolated and flash
  work writes an append-only `sub_agent_trace` before pool submission and a
  terminal update on every exit including preflight failures, and
  `delegate_status` / `delegate_result` scan those rows in streaming mode and
  merge them with the in-memory registry instead of reporting "no live
  delegate" after a server restart.
- A ScratchDir already above its byte or file quota can be inspected and
  emptied again: status, listing, reads, searches and `delete_file` skip the
  quota check during recovery while every operation that can grow the
  directory still fails closed, and confinement and symlink validation stay
  enforced.

- Remote relays no longer lose every filesystem action after a script sync.
  `tools/fs_actions.py` imported the new `fs_http` and `fs_archive` modules at
  module level, but neither file was in the server push list, the relay accept
  list, the CLI/Docker dev mounts, the relay image generator or the MCP client
  installer, so an existing containerized relay received the new facade
  without its siblings and answered `No module named fs_archive` to `list_dir`
  and uploads. Both files are now in every manifest (the CLI reuses the relay
  accept list), `update_scripts` reloads them before the facade, and the
  facade degrades to explicit per-action "upgrade the relay runtime" errors
  when an older relay runtime lacks one of them.
  `tests/test_relay_script_manifests.py` keeps the manifests aligned.
- History search no longer reparses a very large current transcript for every
  no-hit query or ordinary `conversation_search` refresh. Plaintext
  `read_history(search)` calls use ripgrep to select candidate JSONL segments
  before decoding messages, with the exact streaming path retained for
  encrypted logs or prefilter failures. FTS refreshes read only the appended
  reverse-tail rows under the conversation lock; edits, deletions, and other
  generation changes still purge and rebuild the derived index. On a controlled
  60,000-message transcript, no-hit search fell from 488.8 ms to 9.6 ms and a
  one-row index refresh from 491.7 ms to 4.0 ms.
- A human message drained from the pending queue at the end of a
  `delegate_reply` turn now forces a visible user turn. Previously the newest
  queued delegate broadcast re-selected `delegate_reply`, so the agent's
  answers were routed privately to the delegator and every webchat message was
  queued again with "mode mismatch", starving the user until a force stop. A
  batch without a delegate or external request also resets to a user turn
  instead of inheriting the previous turn's mode, and the stale DELEGATE MODE
  hint is removed from the system message. The completed turn's routing mode is
  now snapshotted before that drain, so an interleaved human or tmux message can
  select the next visible user turn without orphaning the shared delegate
  `task_id` that the current response must finish. Idle delegate wake-ups also
  use the persisted CLI answer fallback when the provider returns empty final
  content.
- Force-stopping a turn after a terminal provider failure no longer allows a
  blocked live-preempt request to recreate an older `preempt_rescue` message
  after queue cleanup. The stop path persists its creation-time cutoffs before
  clearing pending work, and enqueue checks the cutoff atomically with the
  append. This prevents the stale message from scheduling a ghost agent
  relaunch while messages created after the stop continue normally; the
  terminal `error_event` remains visible in webchat and reconciles Active
  Agents against server state.

## [1.0.0-beta.262] — 2026-09-03

### Fixed

- PawCode Windows installer generation writes native paths into the NSIS script,
  allowing `makensis` to resolve the staged executable and produce the setup
  asset.

## [1.0.0-beta.261] — 2026-09-03

### Fixed

- PawCode Windows release builds find Chocolatey-installed NSIS under the
  standard `Program Files` directory even before the runner refreshes `PATH`,
  ensuring the setup executable is included in published release assets.

## [1.0.0-beta.260] — 2026-09-02

### Fixed

- Durable one-shot flow runs enqueue their initial FlowFile and enter the
  `running` state before the executor scheduler starts, preventing an empty
  startup window from failing a valid run as drained before injection.

- Codex interactive no longer fails the PawFlow turn on a `response.failed`
  exchange. On the WebSocket Responses transport (Codex 0.152) a transient
  upstream error (`An error occurred while processing your request ...
  request ID ...`) is followed by `response.failed`; Codex retries the sampling
  request itself and continues, while PawFlow raised immediately and showed
  `LLM call failed` for a turn the TUI was still running. The coordinator now
  defers the failure: a later `response.created` clears it, and the turn only
  fails with that detail when the `Stop` hook arrives with the failure standing
  or no retry starts within `PAWFLOW_CODEX_FAILED_EXCHANGE_RETRY_GRACE_SECONDS`
  (default 120).
- PawCode standalone builds exclude optional Pydantic and Rich developer
  integrations, preventing unrelated scientific and CUDA stacks from entering
  the executable and exhausting build memory.
- Relay CLI standalone builds include the vendored `pkg_resources` helpers
  required by its PyInstaller runtime hook, so the packaged executable starts
  reliably across setuptools layouts.

## [1.0.0-beta.259] — 2026-09-02

### Fixed

- Root cause of the recurring SQLite header corruption (`unsupported file
  format` on `mcp_servers`, `scratchdirs`, `ui_surfaces`, `agent_inbox`):
  aborting an in-flight LLM stream closed its TLS socket from another thread,
  the freed descriptor number was reused by the next SQLite store to open,
  and OpenSSL wrote a TLS alert record into page 1 of that database.
  Cross-thread interruption now uses `shutdown()` and only the owning thread
  releases the descriptor (`core/socket_teardown.py`), for LLM stream aborts
  and the relay WebSocket bridge alike.
- `UiSurfaceStore` and `AgentInboxStore` check their main file read-only
  before schema work and fail closed with one CRITICAL log line and a typed
  `SqliteStoreUnavailableError` (HTTP 503 for `ui_surface_list`) instead of a
  traceback on every request; damaged files are preserved untouched.
- The opt-in bootstrap SQLite canary also covers `ui_surfaces` and
  `agent_inbox`, and the diagnostics document records the signature, the
  reproduction, and the header repair procedure.

## [1.0.0-beta.258] — 2026-09-02

### Added

- Published agents can expose SDK-compatible OpenAI Chat Completions, OpenAI
  Responses, and Anthropic Messages endpoints with native streaming and
  non-streaming responses, model discovery, tool calls, and one-shot API keys.
- Standard API sessions now persist canonical request prefixes, run ledgers,
  replay events, response retrieval and deletion, exact immutable checkpoints,
  and busy/stale-safe conversation forks across restarts.
- The Published agents / APIs interface now configures dialects, activation,
  permissions, limits, lifecycle controls, endpoint URLs, and SDK snippets in
  English, French, and Spanish.

### Changed

- A2A, AG-UI, and standard API publications now share fail-closed authentication
  and runtime state, while agent streaming preserves exact run, task, and turn
  identities through continuation, cancellation, and disconnect handling.

### Security

- Standard API access is isolated by publication, key, generation, dialect,
  model, canonicalization, and secret-hash namespaces, with bounded sessions,
  concurrent-run admission, strict-field validation, lease fencing, TTLs, and
  tombstones.

## [1.0.0-beta.257] — 2026-09-01

### Added

- Added an opt-in, fail-fast SQLite bootstrap canary and corruption-diagnostics
  guide that fingerprint both SQLite stores at four startup boundaries without
  mutating the database or its WAL/SHM evidence.

### Changed

- CLI-backed providers now honor the configured `api`, `full`,
  `api_readonly`, and `full_readonly` tool-exposure modes through the exact
  MCP surface advertised by the relay.

### Fixed

- Corrupt ephemeral ScratchDir metadata is quarantined with its WAL/SHM
  sidecars and recreated lazily, while durable published-MCP metadata remains
  fail-closed; both stores now use WAL, full synchronous durability, and
  cell-size validation.
- Multi-conversation workspaces now restore titles, themes, and transcript
  scroll positions per session, and background callbacks can no longer repaint
  shared active-agent surfaces belonging to the focused conversation.

### Security

- Read-only tool exposure is filtered during discovery and schema lookup and is
  enforced again for direct, forged, and hook-replaced execution requests.

## [1.0.0-beta.256] — 2026-09-01

### Added

- Added a durable managed AG-UI/WebMCP execution runtime with transactional
  turn acquisition, journal replay, attach/cancel credentials, run and batch
  claims, terminal deposits, subscriber takeover epochs, catalog identity, and
  restart-safe key-version state.
- Added conversation-owned agent/task Webchat projections with scoped history
  loading, plus explicit runtime-readiness and main-flow guardrails.

### Changed

- The simplified live view now treats each top-level user message, scheduled
  wake-up, and terminal result as a positional boundary, preserving ordered
  multi-agent finals with matching live, reload, and pagination behavior.
- Exact run handles now flow from agent admission through streaming and tool
  relay execution; relay high-water fencing and managed subscribers revalidate
  authority at the effect and frame boundaries.

### Fixed

- Scheduled wake-ups now persist their visible turn boundary before streaming,
  and filtered task/agent projections route actions through their owning
  conversation session.
- Codex Interactive now requires an exact `UserPromptSubmit` or matching MITM
  receipt in production; it retries Enter only when a structurally recognized
  composer still holds the pasted prompt, and live preempts wait for that
  composer before pasting.

### Security

- Managed AG-UI admission, attachment, cancellation, batching, and terminal
  publication now fail closed for stale leases, superseded subscribers,
  mismatched catalog identities, and workers that lose execution authority.

## [1.0.0-beta.255] — 2026-08-31

### Added

- Added a Temporal-inspired durable-execution architecture reference covering
  authoritative run histories, deterministic command replay, task execution
  policies, effect receipts, leases, runtime build pinning, child runs,
  visibility projections, crash testing, and a phased PawFlow-native adoption
  path without a Temporal dependency.

### Changed

- The relay File Explorer now opens as a closable tiled-workspace surface,
  supports native focus/maximize/close behavior, avoids duplicate surfaces when
  reopened, and limits its keyboard shortcuts to the focused tile.

### Fixed

- Relay file previews now pass the already-read browser `Blob` directly to the
  shared viewer, avoiding a CSP-blocked second `fetch(blob:)` while preserving
  authenticated same-origin fetching for FileStore URLs.

## [1.0.0-beta.254] — 2026-08-30

### Fixed

- Desktop inventory and control actions now require an explicit conversation
  identity, so omitting `conversation_id` fails closed instead of bypassing
  conversation-role authorization.
- Docker and forwarded-host Desktop lifecycle operations are serialized, and
  the watchdog rechecks session identity under the lifecycle lock before
  cleanup so a stale health check cannot terminate a replacement session.
- Focusing a conversation tile now transfers ownership of microphone,
  realtime voice, LiveKit, and TTS runtimes; asynchronous callbacks retain
  their originating conversation and cannot mutate or stop a newer session.
- OpenSpace now caches durable messages per open conversation, including
  background SSE events and local echoes, and merges history snapshots without
  erasing newer live rows or projecting them into the wrong room.

## [1.0.0-beta.253] — 2026-08-30

### Changed

- OpenSpace is now a workspace tile in its own right: the quick button and its
  task-bar entry open or focus the tile, the View menu no longer offers an
  `openspace` mode (a legacy stored value resolves server-side to
  `simplified`), and the in-scene Webchat button focuses the Webchat tile
  without touching the conversation view mode.
- Every conversation tile now owns its own task-bar entry: the permanent chat
  button follows the first conversation, additional tiles add closable
  entries, and conversation renames update the entry tooltips.

### Fixed

- Mobile: the sidebar expander now sits flush at the viewport edge when the
  menu and task rail are hidden, instead of floating 35px into the chat text.
- Switching conversation tiles now re-projects the per-conversation surfaces
  living outside the tile — conversation theme, cost badge, pending
  confirmations, context gauges, and the UI surface stack — and re-resolves a
  stale "new conversation" tile title from the conversations cache.
- In the PawFlow Android app, the tile header buttons shift clear of the
  native chrome expander drawn over the top-right corner of the WebView.

## [1.0.0-beta.252] — 2026-08-30

### Added

- Backend Desktop sessions now carry a stable session identity end to end
  (relay, canonical inventory, typed service-flow actions), surfaced in Webchat
  as an Active Desktops dock with backend-truth listing, reattach, and
  exact-session stop confirmation, plus `/desktop list|status|attach|stop`
  subcommands.
- The Webchat workspace can now host several conversations at once: each tile
  runs inside its own conversation session with scoped state, SSE client
  identity, timers, and filtered views, so cross-conversation activity never
  bleeds between tiles.
- The server entrypoint and installer now provision the `pawflow` global flows
  package by default.

### Changed

- Shared delegate request/reply frames now render as agent activity inside the
  simplified Tool view with explicit agent identity, instead of appearing as
  plain user/assistant rows.

### Fixed

- A superseded agent worker (for example a scheduled wake-up claiming the
  session right after a user message) now stops silently through
  `AgentSuperseded` control flow instead of emitting error, done, or cancelled
  events; streaming-mode identity now includes the task id so parallel task
  turns cannot collide.

## [1.0.0-beta.251] — 2026-08-29

### Changed

- Website Creator 1.1.0 now scales full-site builds through explicit crawl,
  contract, template, asset, and resumable batch stages, with relay-backed
  browser, HTTP, and archive operations for larger sites.
- Post-compaction learning now shares one analysis across memory extraction and
  skill discovery, avoiding duplicate model work while preserving both outputs.

### Fixed

- Automatic Project Wiki updates split oversized changed-source sets into
  bounded batches so refreshes stay within request limits and retain durable
  progress.
- Anthropic-compatible cache-read usage is counted as prompt-cache hits, while
  OpenAI-compatible DeepSeek responses now read their provider-specific prompt
  cache hit and miss counters.

## [1.0.0-beta.250] — 2026-08-29

### Changed

- The server image now builds SQLite 3.53.4 from its SHA3-verified official
  source archive, proves Python linkage and FTS5 at build time, and enforces
  SQLite 3.51.3 or newer whenever the container starts.
- Published-MCP lease cleanup now follows the 120-second lease TTL, starts only
  while leases exist, and avoids write transactions when an idle probe finds
  nothing expired.

### Fixed

- A corrupt published-MCP SQLite store now fails closed for MCP authentication
  and management while unrelated listeners, agents, and flows continue to
  restore; PawFlow preserves the database, WAL, and shared-memory evidence and
  emits one actionable critical diagnostic instead of recreating or repeatedly
  probing the store.
- The product-site Help Bot keeps keyboard input and wheel scrolling inside its
  own controls instead of allowing story navigation to consume Space, arrow,
  page, or scroll interactions.

## [1.0.0-beta.249] — 2026-08-28

### Added

- Added a persistent tiled conversation workspace with layouts from one to six
  surfaces, stable Webchat/OpenSpace/tool mounts, task and agent projections,
  focused-agent composer routing, targeted insertion, and maximize/restore.

### Changed

- Claude Code and Codex Interactive turns now share explicit active-turn
  lifetime tracking, while persistent terminal viewers re-resolve replacement
  containers and retry across bounded cold-start gaps.
- The server image now packages and privately caches noVNC assets while
  preserving relay/backend fallbacks for non-Docker deployments.

### Fixed

- Tiled tmux surfaces retain fixed scrollback, workspace surfaces honor the
  configured atmosphere transparency, and unmaximize restores the exact
  previous layout.
- Benign Chromium ResizeObserver loop notifications no longer leave a fatal red
  noVNC overlay over a desktop that is still rendering.
- Live Codex Interactive sessions always rebuild queued or preloaded retriggers
  as deltas instead of leaking a second cold-context request to the agent loop.
- Automatic Project Wiki updates now select at most eight changed files per
  batch by default, keeping large pending manifests below reverse-proxy request
  limits while preserving explicit unbounded internal requests.

## [1.0.0-beta.248] — 2026-08-28

### Added

- Added an operational Workflow Kanban with durable command journaling,
  projections, comments, reviews, attachments, live run updates, localized UI,
  and resilient run controls.
- Added a native PawFlow desktop client with packaged web chat, portable build
  support, and its own validated desktop runtime.
- Added a resumable universal CLI/GUI installer for local and SSH targets,
  including preflight and reachability checks, relay credential storage,
  desktop relay integration, portable platform archives, and release assets.

### Changed

- LLM usage and cost accounting is now recorded after every provider call so
  live totals remain accurate across multi-call agent turns.
- Documented the proposed named execution/deployment profiles and refreshed the
  full PawFlow-versus-Hermes comparison against the current implementation.

### Fixed

- Relay-backed file previews now preserve the correct remote file reference
  across the chat UI preview path.
- Message metadata disclosure icons now render as intended instead of exposing
  literal or malformed chevron content.
- Universal installer builds exclude optional Pydantic and Rich developer
  integrations, preventing unrelated scientific and CUDA stacks from entering
  the standalone executable.

### Security

- Flow execution now persists an authoritative run snapshot and enforces it
  recursively across bridges, replay, migration, and nested tool execution so
  stale or mismatched authority fails closed.

## [1.0.0-beta.247] — 2026-08-27

### Added

- Added optional conversation-scoped Linked Services overrides for automatic
  summary, wiki, memory, embeddings, attachment OCR, skill learning, title, and
  content-review roles. Existing PawFlow parameters and defaults remain
  authoritative when no override is configured, and no service is installed or
  linked automatically.

### Fixed

- Relay binding changes now defer CLI workspace-mount invalidation until active
  turns exit, while dead interactive sessions are still detected during the
  post-Stop drain so Active Agents cannot remain stuck.

## [1.0.0-beta.246] — 2026-08-27

### Added

- Added first-party Website Creator and Media Studio Workflow Agents with
  packaged resources and flows, durable project state, media-capability
  discovery, ComfyUI and FFmpeg execution, and live run inspection.
- Completed the Workflow Agent runtime with durable turn coordination,
  resumable execution, source-backed resources, scoped tool access, operational
  controls, and reusable workflow tasks for agent, group, and media workloads.

### Changed

- Agent, workflow, reviewer, advisor, confirmation, media-generation, and
  synchronization limits now default to unlimited. Only explicit positive
  values impose execution deadlines or iteration caps; success, provider
  failure, cancellation, or an explicit Stop otherwise ends the work.
- Shipped flows, packages, localized authoring forms, and runtime documentation
  now expose the same zero-or-omitted unlimited contract consistently.

### Fixed

- Reviewer handoffs now use stable interaction identities and resume the
  intended final-review route without idempotency collisions or hidden
  phase/pass ceilings.
- AG-UI keeps a bounded connection setup while allowing unlimited streaming
  reads by default, satisfying transport safety without terminating long agent
  runs.

### Security

- Workflow tool execution, confirmations, checkpoints, and resource bindings
  preserve run, turn, actor, and authorization provenance across recovery,
  cancellation, and preemption boundaries.

## [1.0.0-beta.245] — 2026-08-25

### Added

- Added one canonical declarative workflow model over ordinary PawFlow tasks,
  with semantic server-owned editing, deterministic lowering for conditions,
  branches, parallel work, joins, retries, bounded loops, subflows, typed human
  interactions, Workflow Agent calls, and durable one-shot `FlowRun` execution.
- Added versioned multi-view Flow layouts with persisted positions, edge routing,
  annotations, styles, and functional frames shared by editor, viewer, and
  runtime projections.
- Added durable `WorkflowProposal` review and approval, exact draft-revision
  pinning, immutable publication, replay lineage, and shared UiSurface rendering
  across Web, PawCode, and VS Code.
- Added the complete PlanStore migration circuit with five disabled-by-default
  feature flags, idempotent import and activation manifests, single-writer
  cutover, canary verification, compensation, and an operator runbook.

### Changed

- Typed questions and confirmations now use one durable interaction authority
  with schema-validated answers and resumable Web, terminal, and VS Code
  projections.
- The Wiki Agent graph now presents all 23 tasks in five labeled and described
  functional frames, with stable relation identities and stored positions
  instead of one long technical row.

### Fixed

- Preserved the historical zero-output behavior of `splitJSON` for empty
  objects while allowing declarative lowering to request an explicit empty
  relationship.
- Imported waiting runs now trigger the irreversible migration fence on their
  first live recovery or checkpoint write, closing a rollback bypass.

### Security

- Workflow approval and continuation fail closed on stale revisions, actors,
  schemas, authorization snapshots, prepared-call digests, or resource
  references; import, rollback compensation, reads, and outbox acknowledgements
  remain isolated from live-write fencing.
- Legacy PlanStore writers and surfaces are mutually exclusive with canonical
  workflow proposals, preventing silent dual-writer divergence during rollout.

## [1.0.0-beta.244] — 2026-08-25

### Added

- Added exact-version workflow agents with durable run and inbox stores,
  checkpoint/queue/restart preemption, idempotent multi-step LLM calls, strict
  effect authorization, redacted run inspection, operational alerts, and safe
  recovery.
- Added the source-backed Wiki Agent reference workflow, including no-write
  shadow comparison, silent automatic maintenance, server-owned staged cutover,
  PendingQueue migration, authoring UI, and an operator runbook.
- Added bounded multi-agent groups with exact resource bindings, durable inboxes,
  correlated turn identities, a unified run projection, and a first-party
  group-deliberation workflow.
- Added chat authoring and live run inspection for workflow agents, including
  progress, checkpoints, outputs, usage, warnings, cancellation, and localized
  run-state rendering.

### Changed

- The product website now presents a more focused interactive story across its
  landing page, feature pages, demonstrations, and navigation.
- Active Agents now includes workflow runs, while global and
  conversation-scoped appearance controls share one consistent Appearance
  surface.
- Added implementation plans for AnyDoc ingestion, FastCRW packaging, and OOMOL
  OpenConnectors integration.

### Fixed

- Native Claude Code and Codex interactive sessions now keep their own
  pre-compaction context instead of adopting PawFlow-generated summaries when a
  compact event is intercepted.
- ScratchDir guidance and release validation now require copied Python virtual
  environments and explain recovery after an unsafe link, preventing a default
  POSIX venv from leaving the scoped temporary filesystem unusable.

### Security

- Turn-scoped authorization, ordered tool lifecycle events, immutable resource
  provenance, and generation-safe run identities now fail closed on stale or
  mismatched execution context; the new collaboration paths remain disabled by
  default pending rollout gates.

## [1.0.0-beta.243] — 2026-08-23

### Fixed

- ScratchDir quota accounting now accepts confined in-tree symbolic links such
  as a virtualenv's `.venv/lib64 -> lib` alias without double-counting files.

### Security

- ScratchDir usage checks fail closed for escaping, broken, or cyclic symbolic
  links and for a symbolic-link root.

## [1.0.0-beta.242] — 2026-08-23

### Added

- The public website now presents PawFlow through an immersive product story
  and animated how-to experience with dedicated media and audio.

### Changed

- Server-managed relays now use an authenticated private plain-WebSocket
  Docker-bridge endpoint while external relays remain on TLS.

### Fixed

- Relay reconnect recovery no longer flaps or retains stale pending work, and
  concurrent service connection attempts use a short non-blocking claim so
  tool calls never queue behind connection I/O.
- Managed Relay container replacement now waits for Docker's asynchronous
  removal to complete instead of treating an in-progress removal as fatal.

### Security

- Runtime-only secret environment mappings are excluded from hooks,
  transcripts, contexts, compactions, and exports, with an idempotent
  count-only scrub for previously persisted conversation streams.

## [1.0.0-beta.241] — 2026-08-23

### Fixed

- On mobile, secondary composer actions now fill the action panel instead of
  clipping their labels, and Micro and Grab move into that panel to leave more
  width for the prompt while preserving their desktop placement.

## [1.0.0-beta.240] — 2026-08-23

### Added

- Chat appearance preferences now synchronize through a private server-backed
  user store, with inherited global settings, conversation overrides, safe
  image/video uploads in FileStore, cross-device hydration, and cleanup of
  replaced media.
- Long-running skill promotion and conversation imports now use a shared modal
  with real phase labels, blocking transaction state, and explicit dismissible
  errors.

### Changed

- The responsive composer keeps Micro and Grab beside Send, exposes the selected
  agent through a compact conversation-aware button, and groups secondary mobile
  actions without hiding them. Conversation controls now share the action dock's
  dimensions, borders, spring effects, and tooltips; Permissions is an accessible
  button menu instead of a native combo.
- Built-in and extension dialogs now share theme tokens, surfaces, controls,
  tables, focus treatment, and motion. Resource creation forms retain their
  editable parameters when opened from repository actions.
- The desktop task rail is a right-edge overlay with a localized handle tooltip,
  while the mobile Resources/task rail remains coupled to the sidebar drawer.
- OpenSpace gains a Webchat camera transition and always starts from its home
  camera when re-entered; its interaction targets, visitor placement, agent
  selection, and floor navigation are more predictable.

### Fixed

- Atmosphere mode no longer puts the desktop task rail back into document flow,
  so the header and composer keep their full width and opening the left sidebar
  does not shift the rail.
- Conversation resume no longer crashes after the Permissions selector was
  replaced, and the sidebar grip remains available independently of the task
  rail.
- Appearance uploads enforce type and size limits before reading the body, retain
  FileStore category metadata, and safely encode generated media URLs.

## [1.0.0-beta.239] — 2026-08-22

### Added

- The web chat now has an Appearance panel with 75–150% UI scaling,
  global or conversation-scoped image/video backgrounds, overlay, blur,
  saturation, opacity, and motion controls. Background media is stored locally
  per authenticated user, respects reduced-motion and page visibility, and
  continues behind the lower chat chrome.
- Conversation search is available from the composer, `Ctrl`/`Cmd`+`K`, and
  `/search`, while `/` commands and `@` agent mentions use filtered,
  keyboard-navigable pickers backed by the live conversation state.

### Changed

- The chat input is now one responsive composer containing attachment, search,
  command, mention, dictation, terminal grab, and Send actions. Its dock and
  message surfaces become translucent when appearance media is active.
- Fenced code blocks expose an accessible language/copy header, and the memory
  panel uses themed cards while preserving conversation-agent selection for
  filtering, creation, and editing.

## [1.0.0-beta.238] — 2026-08-22

### Fixed

- OpenAI-compatible completion recovery now stops reading immediately after
  the SSE `[DONE]` sentinel, treats known gateway safety labels as
  `content_filter`, and rejects known transport-error finish reasons returned
  by the non-streaming recovery request. Healthy streams and unknown custom
  non-streaming finish reasons remain unchanged.

## [1.0.0-beta.237] — 2026-08-22

### Fixed

- Truncated OpenAI-compatible SSE responses now fall back immediately to the
  same completion in non-streaming mode. This recovers gateways such as
  opencode zen that can repeatedly close a stream after reasoning but before a
  terminal event, instead of replaying the same failing stream until the agent
  turn stops.

## [1.0.0-beta.236] — 2026-08-22

### Fixed

- PFP builds now exclude generated `graphify-out` analysis caches, preventing
  local ignored files from making signed bundled artifacts differ from clean CI
  checkout builds.

## [1.0.0-beta.235] — 2026-08-22

### Fixed

- The bundled ComfyUI operator artifact and catalog are rebuilt from the 1.1.0
  package sources, so release reproducibility checks include its flow tasks,
  durable flows, and official Comfy MCP connection instead of the previous
  1.0.0 bundle.

## [1.0.0-beta.234] — 2026-08-22

### Added

- OAuth for every LLM provider, not just the CLI ones. Authentication was
  decided by provider family — CLI providers could use an OAuth credential
  pool, everything else had to carry an `api_key` — which is not a property of
  the provider: an API endpoint can sit behind an identity provider, and a CLI
  can be pointed at an unauthenticated local gateway. Every provider now has
  the same three modes via `auth_mode`:
  - `none`: the endpoint takes no credential. Explicit on purpose, so a
    forgotten key cannot pass for a deliberate choice; no `Authorization`
    header is sent at all.
  - `api_key`: the existing key or key pool.
  - `oauth`: an access token from the pool named by `credential_service_id`,
    refreshed automatically before it expires.
  - Leaving `auth_mode` empty infers the mode from what is filled in, so every
    service keeps working untouched with nothing to edit.
- A `generic` OAuth credential pool, configured with an identity provider
  (Keycloak, Okta, Auth0, GitLab, or explicit endpoints) plus client
  id/secret. It is accepted by every LLM provider **including the three
  CLIs**, which can be pointed at another OAuth-authenticated backend. The
  identity-provider presets and the token exchange are reused from the
  existing OAuth code rather than duplicated.
  - Token refresh is serialised per pool: identity providers that rotate
    refresh tokens revoke the previous one, so two agents refreshing the same
    pool at once would leave the slower writer storing an already-revoked
    token and kill the pool until someone logged in again.

- An all-in-one `pawflow.comfyui-operator` PFP combines an idempotent bootstrap
  skill, durable versioned flows for readiness, asset provisioning and video
  generation, three deterministic relay tasks, and an explicitly enabled
  stdio connection for the official Comfy MCP. User-scoped `comfyui.*`
  variables retain non-secret preferences; credentials remain in SecretStore.
- Agents can manage plaintext user or conversation variables through
  `manage_variable` (`get`, `list`, `set`, and `delete`), with strict scope and
  name validation. The tool deliberately rejects read-only tool policies and
  directs credentials to `store_secret`.
- Running continuous flows can accept new work through
  `manage_flow(action="invoke")`, including FlowFile content, string attributes,
  and an optional entry task. This preserves the live executor and durable-wait
  state instead of replacing it with a one-shot flow run.

- Tool exposure modes for agents. How an agent's tools are advertised was
  hardcoded to one surface — `get_tool_schema` + `use_tool`, everything else
  reached through them — while MCP publications already offered four. The
  same four modes are now selectable, defaulting to today's behaviour:
  - `api` (default): the two meta tools; smallest prompt, one discovery round
    trip per unknown tool.
  - `full`: every tool declared directly; no round trip, but every schema
    sits in the prompt, so it suits narrow tool sets.
  - `api_readonly` / `full_readonly`: the same two surfaces restricted to
    read-only tools, using the same predicate as an MCP publication.
  - Settable on the LLM service (default for all its agents) and overridable
    per agent in the conversation; an override replaces, it never merges.
  - The vocabulary now lives in one place (`core/tool_exposure.py`) shared
    with MCP publications, so the two surfaces cannot drift apart.
  - *Which* tools exist is unchanged — that stays with the existing
    conversation and per-agent tool filters. CLI providers reach their tools
    through the MCP bridge rather than tool declarations, so they stay on
    `api` and log a warning naming any other configured mode instead of
    silently ignoring it.

- Agent prompts carry a ScratchDir hint. The `scratchdir` tool documents
  itself well — including that it never falls back to `/tmp` — but nothing
  pushed that into the prompt, so an agent only learned it existed by looking
  the tool up, and reached for `/tmp` on the relay or the server container
  instead. That path works right until the container restarts, and nothing in
  it is scoped to the user, conversation or agent. The hint now goes to both
  prompt-assembly paths (API turns and CLI cold starts) and, unlike the
  Scratchpad hint, is unconditional: it has to land before the first write,
  not once a ScratchDir already exists.

### Changed

- **Breaking:** OmniRoute's `auth_mode` service field is renamed
  `omniroute_auth_mode`. `auth_mode` now names the general credential mode
  described above. Update any OmniRoute service configuration that sets it;
  there is no dual read.

### Fixed

- Scheduled continuations are no longer lost when another agent is active in
  the same conversation. The poller now acknowledges a one-shot continuation
  only when its own target agent is already running; activity from a different
  agent no longer consumes the wake-up, and the target agent resumes
  concurrently.

- Skill assignment offered agents that are not in the conversation. A skill is
  assigned to an agent *instance* — it lands in that instance's
  `assigned_skills` inside the conversation — and the server refuses any name
  that is not on the conversation roster. The dialog nonetheless listed every
  repository agent definition on top of the roster, so most entries in the
  dropdown were choices guaranteed to fail. It now offers the conversation's
  agents only, and says so plainly when there are none.

- OpenAI-compatible streams: a provider that cuts the connection is retried
  instead of being taken for a finished answer. A clean EOF is
  indistinguishable from a normal end — `read()` returns empty and the loop
  exits with no exception — so a gateway dropping the connection mid-answer
  produced a response the agent accepted and released the turn on, with
  nothing shown to the user and no error anywhere. Measured against opencode
  zen (`ox-alpha-free`): 17 of 63 calls ended without a valid end-of-stream
  signal, 5 of them after partial text that was then delivered as if
  complete.
  - A stream ending with neither `finish_reason` nor `data: [DONE]` is a
    transport truncation (`stream_truncated`).
  - A `finish_reason` the chat-completions spec does not define is the same
    failure announced in-band: opencode's gateway reports its own upstream
    death as `finish_reason="network_error"` (`provider_stream_error`).
  - Both are retryable — there is no status code to key off, the response was
    a 200 that stopped — and the retry drops whatever the aborted attempt had
    streamed so it is not prefixed onto the answer.
  - A stream that sent `[DONE]` with no `finish_reason` is well formed and
    still returns an empty response: that is a provider with nothing to say,
    not a transport failure.

- Relay shell: a tool the relay account installed for itself is now on `PATH`.
  The shells spawned for `bash` are non-login and non-interactive, so no
  profile file is ever read and `~/bin` / `~/.local/bin` were invisible — a
  binary plainly visible in the home directory answered "command not found"
  on every call, and the only workaround was to re-export `PATH` inside each
  one-shot command. Both exec paths now prepend those two directories when
  they exist, without duplicating an entry the environment already has. A
  `PATH` supplied explicitly in the request's `env` still wins.

## [1.0.0-beta.233] — 2026-08-22

### Fixed

- Claude Code interactive: a live agent is no longer killed while it works.
  Three defects stacked into one silent disappearance — a container evicted
  65 seconds after an active turn, taking its pending background work with
  it, after which the agent never spoke again.
  - Idle eviction had a hardcoded 1800s default. Reaping a live agent is
    destructive and must be asked for: `PAWFLOW_CCI_IDLE_TTL_SECONDS` (and
    its Codex counterpart) now has **no default** — unset means containers
    are never evicted for being idle.
  - A service configured with `timeout: 0` (no timeout) was reaped anyway:
    `LLMClient.timeout` maps both an explicit `0` and an unset value to
    `None`, and the pools tested that value for truth. The new
    `idle_ttl_seconds` property keeps the two apart — `0` disables eviction,
    and a positive `timeout` can only extend an already-enabled TTL, never
    enable one.
  - Idleness was measured from `last_used`, which only moves when a PawFlow
    streaming worker drives the turn. Claude Code also resumes on its own —
    a backgrounded task reporting back, a queued message — through the MITM
    proxy with no coordinator attached, so `last_used` froze while the
    session was demonstrably working. The sweeper now measures idleness from
    the last event the proxy actually observed, and never evicts a session
    with a turn in flight.

## [1.0.0-beta.232] — 2026-08-21

### Fixed

- OpenSpace: the big screen no longer clips the ceiling lamp. The DOM panel
  has no WebGL depth buffer, so an occlusion pass now redraws the fragments in
  front of the screen plane over it. Clicking an agent only selects it:
  opening the Activity panel is reserved for the PC and for a user, each
  clickable having its own raycast marker.
- Flow Runtime Console: "Edit running flow..." works again on instances
  deployed from the UI. That path stored the template's runtime dependency
  scope (`independent`/`conversation`) in the deployment's `flow_scope` and the
  flow's directory id in `flow_fqn`, so opening a runtime draft answered
  `Invalid scope`. Deployments now record the repository FQN and the repository
  scope (`conv|user|global`), and the runtime actions resolve the published
  version through the conv → user → global chain when a stored scope does not
  publish the flow, which also repairs existing deployments. The status bar of
  a running instance now states where the runtime operations live (right-click
  a task for Start/Stop/Configuration, click a queue to inspect, pause or empty
  it).

## [1.0.0-beta.231] — 2026-08-21

### Fixed

- Active Agents: CLI-backed agents are now reported active only while their
  real process/tmux is alive; provider errors (including rate limits) and a
  browser tab becoming visible also force authoritative reconciliation, so a
  dead Claude, Codex, Gemini, or Antigravity runtime cannot remain as a phantom
  active agent.

## [1.0.0-beta.230] — 2026-08-21

### Fixed

- Chat UI: terminal `active_released` and `done` events now force an
  authoritative `list_active` reconciliation even when their turn ID no longer
  matches the locally tracked turn, preventing OpenSpace and the typing status
  from showing an agent as active long after the server released it; OpenSpace
  also no longer turns a human `source.name` fallback into a phantom `user`
  agent or assigns tool activity to that false desk.

## [1.0.0-beta.229] — 2026-08-21

### Added

- ScratchDir: managed relay-backed temporary files scoped by user,
  conversation, agent, and relay, with `fs://scratchdir/` and `/scratch`
  routing, TTL and quotas, fenced cleanup, PFP runtime migration, a cognitive
  UI for usage/tree/renew/clear, and explicit FileStore promotion.

### Changed

- PFP package caches, invocation inputs/outputs, and runtime SDK staging now
  use ScratchDir and fail closed when ScratchDir or the SDK path is missing;
  package execution no longer creates repository-local `.pawflow` state.

## [1.0.0-beta.228] — 2026-08-21

### Added

- Flow editor: a published version can be deleted from the **Versions**
  dialog (🗑, `flow_editor_delete_version` /
  `FlowAuthoringService.delete_version` / `ScopedRepository.delete_flow_version`).
  Versions stay immutable (added by publish or deleted, never edited); the
  last remaining version is refused (delete the flow instead) and deleting
  the latest re-points `latest.json` to the highest remaining version. A
  **Discard draft** button in the canvas deletes the working copy without
  touching any version.
- PFP `ui.v1` (additive): a `ui_extension` may declare `assets.templates`
  (`{slot, path}`) — inert HTML fragments (`.html`, UTF-8, ≤ 64 KiB,
  reviewed like scripts) that the server renders into the chat page before
  JS boot, in any of the ten DOM slots or at the `head` / `body_end` page
  points. Fragments are hash-verified against the signed install record per
  request, never evaluated as templates, never served as URLs, wrapped in
  `data-pf-ext` / `data-pf-template-slot`, kept across client slot renders
  and removed on `unregister()`. The boot manifest lists them as
  `templates`. Docs: `PFP_DEVELOPER_GUIDE.md`, `CHAT_UI_TEMPLATES.md`.

### Changed

- Chat UI page: `tasks/io/chat_ui/template.html` is replaced by a Jinja2
  template tree under `tasks/io/chat_ui/templates/` (`chat.html` skeleton),
  rendered per request by `serve_chat_ui.render_chat_page()` with autoescape
  and `StrictUndefined`. The string-replace markers (`/* JS_PLACEHOLDER */`,
  `{{AGENT_PATH}}`, the extensions placeholder) and the served-HTML string
  cache are gone; the asset version hash now covers templates and CSS
  modules. Tests read the rendered page through
  `tests/chat_ui_testing.rendered_chat_html()`; the DOM contract (ids, PFP
  slot hosts, i18n keys) is pinned by `tests/fixtures/chat_ui_dom_snapshot.json`.
  The skeleton (38 lines) only includes 16 region partials (`head/`,
  `sidebar/`, `dialogs/`, `header/`, `chat/`, `composer/`, `ext/`, `boot/`),
  each one region, ≤ 300 lines. The 1 230-line inline `<style>` became 13
  CSS modules under `tasks/io/chat_ui/css/`, linked in cascade order
  (`_CSS_MODULES`) and served by `serveAssets` at `/chat/js/css/<file>?v=…`
  (cacheable, ~100 KB less per page load); the operator `custom_css` is its
  own `<style id="custom-css">` after the modules, before the theme.
  Docs: `docs/CHAT_UI_TEMPLATES.md`, plan `docs/CHAT_UI_TEMPLATE_PLAN.md`.
- Roadmap, README, project summary, and website now describe what has
  shipped: the manual flow editor and package-backed media providers move to
  *Recently Completed*, mobile work is now iOS + PWA (the Android app ships
  with every release), and the shipped list gains the flow editor and runtime
  console, OpenSpace and the simplified live turn view, policy gating V0,
  AG-UI, A2A, durable confirmations, external secret providers, and
  encryption at rest. `PROJECT_SUMMARY.md` no longer lists shipped items as
  upcoming, and the docs hub links the flow editor, runtime console,
  confirmations, and policy gating references.

### Fixed

- Flow editor: the Versions and Diff dialogs had no close control and stayed
  open behind the canvas after **Edit (draft)** / **View graph**. Every
  authoring dialog now has a ✕, a Close button and closes on Escape, and the
  Versions/Diff dialogs close themselves when they open the graph or the
  editor.

## [1.0.0-beta.227] — 2026-08-21

### Added

- **Policy gating (V0)**: a `gating` service (policy prompt on an API-backed
  LLM and/or sandboxed `gating_script` resources, `llm_scope`,
  `failure_decision`) bound to a conversation (`gating_link`) and/or an agent
  (`gating_service`) decides allow / deny / ask for each tool call against the
  authenticated user's mandate. User messages create or revise a versioned
  authorization context; the central engine runs on the prepared call in the
  main agent runtime, keeps every structural guard and the human floor for
  capability-widening calls, audits each decision, and other runtimes fail
  closed while a gate is bound. Docs: `docs/POLICY_GATING.md`.

### Changed

- OpenSpace bubbles: live user messages now fade out 10 s after they appear
  (the one restored from history at load stays until a live one replaces
  it), and an idle (Zzz) agent always shows its last *message* — the thought
  bubble is put away and the last speech comes back dimmed, unless the viewer
  dismissed it with ✕.
- Context gauges (header battery, active-agents rows, resource panel agents,
  OpenSpace batteries and roster, context editor line) now display the
  percentage **remaining** (100 − used), draining like a battery, and turn
  orange once less than 20% is left. Display only — stored usage values and
  thresholds are unchanged.
- A2A panel: every publication now also shows its AG-UI URL (`/agui/...`)
  with a copy button, and a hint explains the two directions — export through
  a publication, import through the "External AG-UI" agent runtime.

### Fixed

- Service editor model catalog: the live `/models` lookup now sends a
  `PawFlow/<version>` User-Agent. Cloudflare-fronted OpenAI-compatible
  providers (e.g. `opencode.ai`) reject urllib's default `Python-urllib`
  agent with a 403 (error 1010), which made the catalog silently show the
  bundled fallback list; the fallback warning now includes the HTTP status.
- OpenSpace: flash delegates (and any agent whose provider stays quiet
  for more than a few seconds — a long tool run, an unstreamed thinking
  pass) no longer drift back to Zzz while they are working. The
  active-agents tracker (server `list_active` poll + SSE hints) is now the
  liveness reference: a tracked agent is never auto-idled, and an idle avatar
  the tracker still reports wakes back up; the quiet timeout only applies to
  agents the tracker omits.
- Claude Code interactive: a `StopFailure` (for example an upstream `429`
  usage limit on a subscription backend) now ends the turn with a
  non-retryable error that shows the CLI's message in the webchat. The driver
  and agent-level retry loops no longer re-run interactive CLI turns, which
  re-pasted the prompt into the live tmux session (or tripped the cold/delta
  context guard with `DeltaContextRequired`) and left the agent shown as
  working after the CLI had already failed.
- Flow editor: "Edit (draft)" from the repository menu sent the bare flow
  directory id and failed with "Flow name must be qualified"; it now builds
  the qualified `package.name:version` like the Versions/Diff dialogs.

## [1.0.0-beta.226] — 2026-08-21

### Changed

- External AG-UI agents: the Bearer secret and private-endpoint policy are now
  carried only by an `aguiConnection` service. Agent-level
  `agui_auth_secret`/`agui_allow_private` were removed because a conversation
  member with write access could point a direct `agui_url` at their own server
  and receive the conversation owner's secret.

### Fixed

- External AG-UI agents: history replay emits assistant `toolCalls` and drops
  orphan `tool` rows (strict agents rejected the whole run), streamed text is
  persisted when a run errors or is force-stopped, a force-stop issued during
  setup is no longer lost, each turn is settled exactly once, no tool schema is
  advertised when `agui_max_tool_rounds` is 0, the protocol document is saved
  once per run instead of per event, and the runtime dialog labels are
  localized (en/fr/es).
- OpenSpace: clicking an agent avatar or desk now selects that agent in the
  conversation (`cmdAgentSelect`) while keeping the camera focus and the
  activity dialog; visitors and temporary guests outside the roster do not
  trigger a selection.

## [1.0.0-beta.225] — 2026-08-20

### Added

- **External AG-UI conversation participants**: an agent instance can now use
  the full `external_agui` runtime. PawFlow posts AG-UI `RunAgentInput`, streams
  text and reasoning into the canonical transcript, persists state, activities,
  steps, usage, encrypted reasoning values, and interrupt outcomes, and
  serializes runs per conversation member without falling back to a local LLM.
- **Scoped AG-UI connections and safe tool round-trips**: reusable
  `aguiConnection` services resolve by conversation/user/global scope and carry
  the endpoint, SecretStore reference, relay/private-network policy, timeout,
  and bounded tool-round setting. Remote tools are exposed through a strict
  instance allowlist and still pass PawFlow's wrapper checks, permission modes,
  approval gate, and durable tool-call/result pipeline.
- **AG-UI WebChat and OpenSpace integration**: agent create/configure dialogs
  expose direct or scoped AG-UI connections, external participants are labeled
  in the resource and 3D views, protocol activity/state/step/usage events reach
  both interfaces, and custom events offer a cancellable DOM renderer hook with
  a safe JSON fallback.

### Changed

- **Unified external-agent routing and cancellation**: MCP and AG-UI members now
  share the external runtime router across WebChat, channels, polling, streaming,
  force-stop, and A2A. A2A publication of either external runtime requires
  `context_policy: "shared"` so the scoped runtime state remains attached to the
  source conversation.

### Fixed

- **AG-UI protocol lifecycle and replay hardening**: runs must start and
  terminate correctly, chunk identifiers remain correlated across fragments,
  metadata stays separated by event family, state/activity deltas are applied
  durably, queued runs are completed on force-stop, `max_tool_rounds=0` truly
  disables local execution, and remotely resolved tool calls are never replayed
  through PawFlow.

## [1.0.0-beta.224] — 2026-08-20

### Added

- **OpenSpace immersive office environment**: the 3D conversation view now
  builds a richer multi-zone workspace with labeled rooms, architectural
  details, furniture, screens, lighting, and localized camera-control hints.

### Fixed

- **OpenSpace reload and camera controls**: ordered module loading now defines
  the environment before scene initialization, activation waits for the DOM,
  and mobile and desktop input remain responsive after a reload or view
  reselection.
- **Image-description vision routing**: `describe_image` now follows the active
  agent LLM's native vision support or configured `vision_llm_service` fallback
  instead of selecting an image-generation service, with explicit errors when
  neither route is usable.
- **Concurrent capability media sharing**: temporary public FileStore references
  are isolated per execution context, preventing overlapping tool calls from
  replacing or revoking one another's share state.

## [1.0.0-beta.223] — 2026-08-20

### Added

- **OpenSpace modularization and procedural animation**: the 3D webchat view is
  split into seven responsibility-focused classic-script modules (all below 800
  lines) with explicit load order. Chibi agents now retain a procedural rig and
  animate limbs, eyes, mouth, breathing, and blinking from live agent state;
  walking faces the destination at distance-based speed, clicking a participant
  smoothly focuses the camera, and frame-time-driven DPR scaling includes a
  software-WebGL fallback. The tmux wall poster now opens the selected agent's
  live terminal viewer instead of enabling composer grab mode.

### Fixed

- **Flow runtime hot-swap hardening** (`engine/_continuous_exec_control.py`,
  `engine/checkpoint.py`, `tasks/ai/actions/flow_runtime.py`): the removed-queue
  and in-flight preflight checks of `update_flow()` run again after the
  scheduler is stopped, so FlowFiles enqueued or tasks started in the race
  window are never dropped or killed under a `reject` policy; an aborted or
  timed-out update resumes only the scheduler thread instead of calling
  `start()` (which leaked the live worker pools and replayed the last
  checkpoint); a failed rebuild raises `FlowUpdateError` and
  `flow_runtime_update_apply` answers HTTP 500 `runtime_update_failed` rather
  than a misleading 409 `runtime_changed_since_preview`; checkpoint recovery
  restores each queue by `(source, target, relationship)` so two relationships
  between the same tasks no longer merge after a crash.

## [1.0.0-beta.222] — 2026-08-20

### Added

- **Flow Editor — authoring foundation** (`core/flow_authoring.py`,
  `core/flow_definition_validator.py`, `tasks/ai/actions/flow_editor.py`):
  one `FlowAuthoringService` shared by the Web UI, agent tools and the
  CLI in front of `ScopedRepository`. Published versions are immutable —
  editing goes through drafts stored in `data/runtime/flow_editor_drafts/`
  (private per user, monotonic `revision`), saving is optimistically
  locked (`base_revision` mismatch → HTTP 409 `draft_changed_elsewhere`,
  never last-writer-wins), and publishing creates a NEW version
  (`publish_flow_version` / `create_flow`). The JSON definition is the
  source of truth: a load → save round-trip preserves every field,
  including unknown/future ones. `FlowDefinitionValidator` is the single
  static validator (structured `severity/code/entity_type/entity_id/field`
  problems, never resolves `${...}` or opens connections); publish adds a
  full parse. Structured diff (`+`/`~`/`-` tasks, relations keyed by
  `connection_id`, parameters, services, groups, entries/exits; layout
  and name/description flagged without runtime impact). Actions:
  `flow_editor_get/versions/new/fork/create_draft/load_draft/list_drafts/
  save_draft/discard_draft/validate/diff/publish/task_catalog/task_schema
  (schema for the CURRENT parameters)/service_catalog/service_schema`.
  See `docs/flow_editor.md`.
- **Flow Editor — canvas edit mode**: the same `flow_graph.html` opened
  with a `draft_id` (repository menu → *Edit (draft)*) edits the draft:
  the JSON definition is the source of truth and ReactFlow a projection,
  drag positions land in `flow.layout.nodes`, Delete removes tasks/relations
  atomically, undo/redo (one entry per drag), debounced autosave with
  `base_revision` (409 → locked + Reload), Auto Layout, Validate (Problems
  drawer) and Publish (diff count → version prompt). No runtime polling
  while editing. A searchable, categorized processor palette now supports
  zoom-safe drag/drop with deterministic technical ids and immediately opens
  a schema-driven Properties drawer. Tasks keep a separate human label;
  current parameters drive dynamic schemas, existing services are selectable,
  and saving preserves unknown parameters through the shared
  `schema_form.js` renderer used by service dialogs.
  ReactFlow handles are now connectable in edit mode: drawing or clicking a
  connection opens one relationship/queue drawer driven by the source task's
  current `get_output_relationships()`. Create, relationship change and delete
  are atomic undoable operations keyed by stable `connection_id`; duplicates
  are refused. Per-connection count/byte backpressure, FlowFile TTL and
  prioritizer settings are statically validated and now configure the actual
  runtime `Connection`.
  Flow-level metadata, JSON-valued parameters, embedded schema-driven services,
  explicit entries/exits and copyable `${...}` expression assistance are now
  editable from the same toolbar. Embedded services join user/global service
  selectors, service schemas use their current parameters, required service
  fields are validated statically, and Auto Layout plus the latest structured
  Problems report remain directly accessible.
  The repository sidebar now completes the authoring loop: New Flow creates a
  scoped draft; template menus expose writable Edit/Diff, cross-scope Fork and
  immutable Versions; every successful authoring action opens the same canvas,
  whose validated Publish produces a new deployable repository version. UI
  affordances are scope-aware while server-side ownership/admin gates remain
  authoritative.
  Inline Process Groups now execute as part of the normal DAG: the parser
  recursively flattens nested groups, inherits group variables, records task
  provenance, merges internal relations and rejects duplicate ids. Static
  validation descends through nested tasks, relations and ports. The same
  ReactFlow canvas can group a selection, edit group metadata and typed ports,
  and drill into a group read-only without losing original boundary endpoints.
  Version-pinned subflow nodes edit `flow_ref`, parameter mappings, port
  mappings and attribute pass-through in place; parser version/port checks and
  the existing `executeFlow` recursion guard remain authoritative.
  A running repository-backed instance can now create a draft of its exact
  deployed version in the same canvas, publish a new immutable version, preview
  its live impact, then Apply it safely. Removed queues expose their queued
  FlowFile count/bytes and require explicit `drop`; tasks in flight require
  explicit `wait`. A preview token rejects stale consent after any candidate or
  runtime change. Hot-swap preserves surviving queues and pause state strictly
  by `connection_id` (so distinct relationships never merge), then persists the
  applied FQN and layout for restart recovery.

- **Flow Runtime Console**: the Flow Runtime Viewer becomes a NiFi-style
  operations console on running instances. Engine: stable
  `connection_id` per queue, `Connection.pause/resume` (paused queues
  keep accepting upstream but block downstream — and the scheduler is
  pause-aware end to end, including queue-aware tasks), FlowFile access
  by `process_id`. API (`tasks/ai/actions/flow_runtime.py`): task
  start/stop/restart/disable, task details with RAW parameters
  (`${secret}` references never resolved), server-paginated queue
  listing, pause/resume/clear (clear = FlowFiles only, no implicit task
  reset, immediate checkpoint), FlowFile inspection (consumed items
  answer `no_longer_queued`), drop by process_id, and a dedicated
  streaming download route (`/api/flow-runtime/.../content`, never
  JSON/base64). Manual operations are recorded in provenance. UI: edges
  read as queues (`relationship · N · size`, ⏸ dashed grey when paused,
  no animated current), right-click task menu + read-only configuration
  drawer, edge click → Queue Inspector drawer (pause/resume, confirmed
  Empty queue, pagination, attributes/content preview, download, drop).
  The openspace 3D stage mirrors paused/backpressured links. See
  `docs/flow_runtime_console.md`.

- **Durable confirmations**: agents (`request_confirmation` tool) and flows
  (`requestConfirmation` task) can ask the user yes/no, single-choice, or
  multi-choice questions that survive reloads and restarts. The user answers
  whenever — from the actionable inline block in the conversation
  (re-hydrated from the store after reloads) or the pending panel (header ✅
  button with badge, `/confirmations`, openspace poster). On answer the
  agent is woken with the response and continues; a flow resumes through
  the durable signal `confirmation:<request_id>`. Optional expiry
  (`'2h'`, `'3d'`, `'1mo'`...). See `docs/confirmations.md`.
- **Durable flow wait/notify**: `durableWait` parks a FlowFile (serialized
  to `data/confirmations.db`) on a named signal with a configurable timeout
  from seconds to **years** — or forever; `durableNotify` (or any code via
  `notify_signal`) resumes it across flows AND server restarts, re-injected
  at the wait task with `durable.wait.status`/`durable.wait.value`. A
  notify with no waiter is remembered so a later wait passes through
  immediately. The in-memory `waitForSignal`/`notify` pair is unchanged for
  short synchronizations.

- Openspace: a **FileStore TV** by the left wall — clicking it lists the
  conversation's FileStore files; a picked file plays/shows on the TV
  screen (projected DOM panel, native `<video>`/`<audio>` controls kept):
  images display, video/audio auto-play, unknown formats say so on the
  screen and point at the Files menu. Playback stops on room switch and
  view deactivation.
- Openspace: the right-wall poster gallery now covers every side panel —
  todo, cost, context editor, plans, scheduled tasks, file explorer,
  desktop, terminal, and tmux grab join the cognitive posters; posters
  wrap into rows of 9 and the transient resource boards pop above them.
- File explorer: toolbar buttons and an empty-space context menu for
  **New file / New folder** (an empty directory previously offered no way
  to create its first entry), and deleting a directory now confirms with
  an explicit "AND EVERYTHING inside it" warning (relay deletes are
  recursive).

### Removed

- **Legacy admin editor scaffold and `adminAction` task**:
  `tasks/io/admin_editor_actions.py` (`admin_list_task_types`,
  `admin_get_task_schema`, `admin_list_service_types`,
  `admin_get_service_schema`, `admin_save_flow_json` — a second, file-based
  persistence/versioning system next to `ScopedRepository` —,
  `admin_validate_flow`, `admin_auto_layout`) and the whole
  `tasks/io/admin_actions.py` (`AdminActionTask`, task type `adminAction`,
  `POST /admin/api` with `admin_*_flow` / template handlers). An audit found
  no flow, route, UI code or documentation consuming them; the modern
  paths are `deploy_flow` / `start_flow` / `stop_flow` /
  `get_flow_instance`, `flow_runtime_*` and the new `flow_editor_*`
  actions. A custom beta flow that declared `"type": "adminAction"` will
  fail to parse and must be migrated to those actions.

## [1.0.0-beta.221] — 2026-08-20

### Added

- AG-UI: full interactive protocol on isolated publications. Frontend tools
  (`RunAgentInput.tools`) are now REAL callable tools — a call streams as
  `TOOL_CALL_*`, executes in the client, and the client's `role:"tool"`
  result message feeds the next run (tool-based generative UI /
  human-in-the-loop). Shared state: `RunAgentInput.state` seeds the thread,
  every run opens with `STATE_SNAPSHOT`, and the new `agui_state` tool
  streams `STATE_SNAPSHOT`/`STATE_DELTA` (RFC 6902) live. Interrupts: the
  new `agui_interrupt` tool finishes the run with an interrupt outcome and
  `RunAgentInput.resume` answers it. Inline base64 multimodal parts become
  attachments (vision/documents); `forwardedProps` reaches the agent; the
  `GET` descriptor advertises `capabilities`. AG-UI handlers are visible
  only inside their own conversation (`core/agui_tools.py`,
  `origin == "agui"` gate in `core/tool_mcp_filters.py`).

### Fixed

- Openspace now creates a desk for every agent attached to the conversation,
  including idle or rate-limited agents that have emitted no live interaction;
  the room synchronizes from the canonical `list_resources` roster regardless
  of whether that snapshot arrives before or after the 3D view opens.
- Chat UI: thinking text is no longer truncated (nor duplicated) in the
  detail block, the turn view, and the openspace thought bubble. The
  streamed preview is truncated by design (the emitter never flushes its
  final <250-char fragment) and the durable `thinking_content` must
  supersede it — but any `tool_call`/`token`/message finalized the live
  block first, so the durable text used to open a duplicate block next to
  the truncated copy. Finalized preview blocks now stay reachable (per
  agent, prefix-matched, purged at `done`) and the durable text reconciles
  into them; the openspace bubble splices the durable text over the
  tracked preview region instead of regressing at the next coalesced
  flush.
- AG-UI: the first run of any new `threadId` no longer fails with "Unknown
  A2A context for this client" — client-chosen thread ids now get-or-create
  their per-key context (`A2AStore.ensure_named_context`, digest-keyed so
  equal thread ids from different keys/publications never collide).

## [1.0.0-beta.220] — 2026-08-19

### Added

- AG-UI protocol server (https://github.com/ag-ui-protocol/ag-ui): published
  agents are now reachable by any AG-UI client (CopilotKit et al.) at
  `POST /agui/{publication_id}` — one `RunAgentInput` in, a streaming SSE
  run out (`RUN_STARTED`, `TEXT_MESSAGE_*`, `THINKING_*`, `TOOL_CALL_*` +
  `TOOL_CALL_RESULT`, `RUN_FINISHED`/`RUN_ERROR`). Reuses the existing A2A
  publications, Bearer keys and per-client contexts (the AG-UI `threadId` is
  an isolated per-thread conversation with durable server-side history), so
  one publish action serves both protocols. See `docs/agui_integration.md`.

- Openspace 3D view: the floor ring around each agent is now a status
  carousel — brains (🧠) orbit and zoom in/out while the agent thinks, tools
  (🔧🛠️⚙️) spin around it while a tool runs, and Zzz (💤) drift around an
  idle agent. The carousel derives from the live SSE state every frame and
  its sprite textures are disposed on every swap and on desk retirement.

### Fixed

- Relay: the host helper no longer dies silently when an aborted client makes
  `accept()` report `ConnectionAbortedError`/`ConnectionResetError` (observed
  on Windows as repeated "Host helper health check failed: [Errno 104]
  Connection reset by peer"); those failures are transient and the helper
  keeps accepting. The WSL bridge now listens on its own free port instead of
  the Windows helper target port, so loopback route discovery can no longer
  reconnect the bridge to itself.
- Claude Code / Codex interactive: a tmux server crash mid-turn no longer
  hangs the agent forever. The tmux takes the CLI down with it, so no Stop
  hook, proxy event or error ever arrived and the turn coordinator waited
  indefinitely — every new message queued behind an "active" turn that could
  never end, and only a force stop + resend unblocked the conversation. The
  coordinator now probes container/tmux liveness once the event stream has
  been silent for `PAWFLOW_CCI_LIVENESS_PROBE_IDLE_SECONDS` (default 20s,
  never post-Stop); two consecutive dead probes fail the turn with a clear
  error, the pending queue drains, and the next message recreates the
  session. Wired on the request, interrupt, and manual-capture paths of both
  interactive providers; probe errors (slow docker daemon) never count as
  death.

## [1.0.0-beta.219] — 2026-08-19

### Added

- Openspace V4: clickable resource posters on the right wall (one per
  resources-menu entry, each opening the matching panel), a flows chooser
  whose picked flow is projected on a live 3D stage (task blocks colored by
  state, links carrying an animated current that follows queue size and turns
  red under backpressure, polled from `flow_runtime_graph`), per-agent
  ⏸ interrupt / ■ stop controls on the active-agents blackboard, and a
  Resources poster that pops one labeled board per sidebar sub-section —
  clicking a board opens that sub-menu as a live interactive dialog (cloned
  sidebar DOM, every left-menu action works from the scene).
- Openspace V5: office door opening a conversation picker (each conversation
  is a room with a palette seeded from its id), conversation title framed
  above the wall screen, ephemeral desks for flash-delegate guests, walk-and-
  return delegation trips (desk for in-conv delegates, door for `a2a`),
  backface culling + depth sorting for projected panels, robust flow-stage
  close (✕/Escape), and mobile support: pinch zoom, two-finger pan, camera
  D-pad on touch devices, debounced WebGL resize (no more keyboard blink),
  and a composer that returns to its default size after a send. The flow
  stage drills into process groups/subflows (blue blocks; green 3D up-arrow
  pops a level, red 3D ✕ closes), a conversation switch empties the room
  (per-conversation desks), and the stage close has three independent paths
  (in-scene ✕, DOM button, Escape).

## [1.0.0-beta.218] — 2026-08-19

### Added

- Openspace 3D view: the sender's own message now appears immediately in the
  scene (the composer mirrors it directly — the SSE stream never echoes it
  back), and sending attachments makes the user's avatar walk to the target
  agent's desk, drop one folder prop per file, and walk back.
- Openspace 3D view: each tool call now also shows the tool's name in the
  agent's thought bubble alongside the falling desk prop.
- Openspace 3D view: a cinema wall screen behind the visitor row now
  projects the live simplified view. The real transcript DOM is reparented
  onto the screen and perspective-mapped to the wall each frame (projective
  `matrix3d`, same camera as the bubbles), so it keeps streaming and stays
  scrollable; openspace now runs simplified rendering underneath instead of
  classic.
- Openspace 3D view: office decor (plants, rug, couch facing the wall
  screen, water cooler), click-to-walk for the viewer's own avatar (the
  spot becomes its new home), and camera panning via right-drag or
  shift-drag alongside the existing orbit and zoom.
- Openspace 3D view: agents are now chibi mascots (round body, big eyes,
  smile, blush; per-agent ears/horns/antennae from the name hash), each
  carries a battery gauge above its head mirroring the header context
  gauge (% used, same colors and source), and a blackboard on the
  left of the office lists the active agents with their current tool and
  battery, chalk-styled and perspective-projected like the wall screen.

### Fixed

- Windows Relay Desktop: the WSL host bridge died at startup with
  "PAWFLOW_HOST_HELPER_TOKEN is required" — the token was forwarded with a
  `WSLENV` `/w` suffix, which shares a variable only when invoking Windows
  FROM WSL, the opposite of this launch. Bare entries (shared both ways)
  fix the direction; the `--exit-on-stdin-eof` watcher now reads the raw
  stdin fd, removing the "Fatal Python error: _enter_buffered_busy" crash
  at interpreter shutdown.
- Openspace 3D view: bubbles now show the WHOLE message/thought in a
  scrollable body (no more 200-char cut), thinking accumulates across the
  turn with tool names logged inline and resets when the turn ends, bubble
  tails are centered so they point at the avatar, the anchor height matches
  the chibi mascots, and Ctrl+drag raises/lowers the camera target above
  the floor plane (help hint updated).

- Openspace 3D view: working agents now visibly move. The previous "sway"
  rotated a rotationally-symmetric capsule around its vertical axis
  (invisible) and the tool bob was 0.07 units; states now animate with
  lean + bounce rhythms, the PC screen flickers while busy, and the status
  chip pulses. Thought bubbles gained a cloud tail so they read as thoughts.
- Openspace 3D view: thinking never reached the thought bubbles — the SSE
  payload carries the text in `text` (and `thinking` for delegates), while
  openspace read `content`. Agent and sub-agent thinking now stream into
  the thought bubble.
- Openspace 3D view: after a reload, a user message sent with attachments
  showed "[object Object]" in its bubble — stored multimodal content is an
  array of blocks; bubbles and activity logs now extract the text parts.
- Openspace 3D view: bubbles froze after their first turn — once a bubble
  expired, the per-frame expiry sweep kept wiping the stream buffer, so
  the next turn's thinking/speech never accumulated past the 250ms flush.
  The reset now happens exactly once (guarded by the stale class), and a
  new turn starts with a clean thought bubble. A ResizeObserver keeps the
  canvas in sync when the wrap resizes without a window resize (stretched
  canvas = projected panels drifting off their meshes), a controls hint
  sits bottom-left of the scene, and the blackboard shows each agent's
  live avatar state instead of the staler tracker status.

## [1.0.0-beta.217] — 2026-08-19

### Added

- Openspace 3D view: every `tool_call` now drops a tool-specific emoji prop
  onto the working agent's desk (fading away on its `tool_result` or when the
  agent goes idle), and busy agents visibly bob while a tool runs.
- Openspace 3D view: human participants now appear as standing visitor
  avatars facing the desks — one avatar per distinct author in shared
  conversations — with speech bubbles for their messages.

### Fixed

- Webchat mic (STT) no longer disappears for minutes after a deploy restart.
  Root cause chain: the first request to touch a service scope after the
  restart carried the b216 provider-PFP migration synchronously, and the
  migration's package install re-ran the static+LLM review per object even
  though `force=True` discards the verdict — the webchat's `list_stt_services`
  paid ~13.5 minutes for it and the mic button stayed hidden the whole time.
  Three fixes: `ServiceRegistry._ensure_loaded` now runs the provider-PFP
  migration on a background thread instead of the caller's critical path;
  installing a byte-identical official bundled artifact (package/version,
  SHA-256, and developer key all matching the CI-verified bundled catalog)
  skips the static+LLM review and stamps `reviewer: bundled-catalog`
  provenance; and the webchat STT/TTS/realtime-voice service refreshes no
  longer brick on a lost request (a stale in-flight refresh is overridden
  after 20 s, an error resets the in-flight flag, and an empty service list
  right after load is retried up to 3 times).
- PFP Depot rows whose exact package id and version are already installed in
  the visible scope now show an Installed label instead of an install button.

### Changed

- Openspace 3D view: the most recent speech/thought bubble per participant no
  longer disappears — after the linger delay it only dims — and a full history
  render seeds avatars, activity logs, and each participant's last bubble from
  the transcript (deduplicated by `msg_id`, reset on conversation switch).

## [1.0.0-beta.216] — 2026-08-19

### Added

- PFP: the Kling, Pixazo, and Wavespeed media providers now ship as signed,
  bundled packages, with provider migration and runtime rematerialization for
  existing service definitions.
- Website: the published-MCP guide now includes a read-only connector diagram.

### Changed

- CI: GitHub-hosted runners skip `apt` entirely when the required compiler
  toolchain is already installed, avoiding unnecessary mirror stalls.

### Fixed

- PFP: service-instance refresh now handles expected package-resolution and
  unregistered-provider cases explicitly instead of swallowing every exception,
  clearing the Bandit `B112` finding without breaking provider migration.
- Simplified webchat: a provider fallback, background result, pagination row,
  or other `turn_id` change no longer invents adjacent activity blocks without
  a rendered user or system-wake boundary. A resumed execution reuses and
  correctly finalizes the existing positional block, leaving one detail mirror
  and one interactive last message.

## [1.0.0-beta.215] — 2026-08-19

### Changed

- Project wiki: `acknowledge` accepts glob patterns (`app/*`) that expand
  against the pending source set — the recovery path for a manifest poisoned
  by a pre-guard local-surface scan.
- CI: the apt step is bounded (timeouts, retries) and falls back to
  `archive.ubuntu.com` when the Azure mirror hangs, instead of stalling the
  whole test matrix.

### Fixed

- Project wiki: scans and auto-updates are now pinned to the relay container
  surface — `local=true` is rejected (`ValueError`) in `scan_from_relay` and
  `auto_update`, and the maintenance worker plus the panel refresh action pass
  `local=False` explicitly. A scan on the server/host surface indexed the
  deployed runtime tree (`app/data/runtime/...`), and the next relay scan
  reported ~10k phantom "removed" sources, causing the wiki maintainer to
  generate bogus "X removals" pages.

## [1.0.0-beta.214] — 2026-08-19

### Added

- Webchat: Openspace 3D view — a third `chat.view_mode` selectable from the
  View options menu. Low-poly isometric office rendered with a vendored,
  lazily-imported three.js (r170): one desk per agent, live speech/thought
  bubbles, tool-call screen glow, delegation walk animations, selected-agent
  halo, and a per-agent activity dialog (stacked expandable blocks) on PC
  click. Classic rendering keeps running underneath so switching modes is
  instant.

### Changed

- Ruff and Bandit versions are now pinned for reproducible lint results, and
  the release gate is documented separately from the tracked full-tree
  remediation plan.

## [1.0.0-beta.213] — 2026-08-19

### Fixed

- Relay Desktop on Windows now starts its Docker-to-host helper bridge as a
  tracked WSL process instead of relying on Docker Desktop host networking,
  waits for the bridge before registering the relay, keeps capability tokens
  out of process arguments, and shuts the bridge down with its parent.

## [1.0.0-beta.212] — 2026-08-18

### Fixed

- Context menus now use active theme colors instead of hard-coded dark-theme
  values, and Android opens flow graphs in native WebView tabs rather than
  blob-backed iframes that could terminate the app's renderer.
- Simplified conversations now reunify a turn split across loaded history
  pages into one detail block, preserving `USER/SCHEDULE > DETAILS > LAST`
  instead of exposing one block and message per page fragment.

## [1.0.0-beta.211] — 2026-08-18

### Fixed

- Published MCP servers now emit only valid tool definitions and content blocks,
  enforce case-insensitive publication filters without re-exposing dispatch
  shims, distinguish protocol errors from execution failures, propagate nested
  MCP/runtime/HTTP failures through `isError`, and reuse retained results when a
  client replays the same session request instead of executing a tool twice.

## [1.0.0-beta.210] — 2026-08-18

### Added

- The complete blocking release procedure is now version-controlled under
  `docs/` and mirrored by a sourced project wiki page.

### Fixed

- Named configurable identifiers, including relays, services, tools, flow
  tasks, resources, filesystem selectors, tunnel bindings, and published MCP
  filters, now resolve case-insensitively while preserving canonical spelling
  and rejecting ambiguous case-only duplicates.

## [1.0.0-beta.209] — 2026-08-18

### Fixed

- Published MCP connectors now negotiate the Claude web connector's legacy
  `2025-11-25` protocol version instead of falling back to the oldest supported
  version and disconnecting immediately after initialization.

## [1.0.0-beta.208] — 2026-08-18

### Added

- External secret providers now support pluggable provider services, scoped
  access policies, cached resolution, secret-entry references, runtime
  expression expansion, package integration, and operator documentation.
- Published MCP servers can expose multiple conversation agents with isolated
  routing and per-agent tool filtering instead of being limited to one agent.
- Direct Anthropic API services expose model reasoning effort in the service UI
  and send supported `low`, `medium`, `high`, `xhigh`, or `max` values through
  `output_config.effort` for streaming and non-streaming requests.

### Changed

- The evaluation-harness plan now defines a broader verification strategy for
  reward quality, reproducibility, adversarial cases, and release readiness.
- Agent `max_tokens` now limits only the visible terminal answer. Reasoning and
  tool-call turns no longer consume that response budget; total usage remains
  governed by the existing cost budgets, including agent-scoped limits.

### Fixed

- External secret provider configuration is validated strictly, all supported
  runtime paths use the shared resolver, and expression regression coverage
  verifies provider-backed secret expansion.
- Skill reviews accept fenced JSON, retry once after invalid or truncated JSON,
  and fail closed only when the corrected verdict is still invalid.
- Conversation context menus use active theme colors and consistent hover
  states; unsafe branch mutations are hidden while a conversation is running
  instead of appearing as confusing disabled actions.

## [1.0.0-beta.207] — 2026-08-17

### Added

- The PawFlow website now includes real product demos for the Android app,
  ChatGPT MCP workspace access, ComfyUI through a Windows relay, themes,
  Private Gateway, and the workspace menu; the private Android server URL is
  blurred throughout the selector sequence.
- A signed, opt-in `pawflow.comfyui-operator` bundled package ships the
  `operate-comfyui` skill for queue-safe self-hosted ComfyUI installation,
  relay routing, workflow/model/custom-node operation, media QA, and FileStore
  delivery.

### Fixed

- FileStore listings are newest-first and filterable by filename
  glob/substring or content type across `list_dir` and `glob`, so recent
  attachments can be found without scanning an unfiltered dump.
- The release procedure now explicitly forbids `v`-prefixed tags and has a
  CI guard requiring the exact `1.0.0-beta.N` form.

## [1.0.0-beta.206] — 2026-08-17

### Added

- Interactive Project Graph view: the webchat panel's new **View** button
  opens a force-directed canvas tab (custom renderer, no external
  dependencies) that navigates the code graph by capped ego subgraphs —
  overview of the most-connected nodes first, then 1–2-hop neighborhoods
  re-centered by double-click/double-tap, with confidence-colored edges,
  node detail (file, location, degree), and mouse/touch pan-zoom-pinch.
  Backed by `ProjectGraph.ego_subgraph` and a `project_graph_ego` UI action
  (clamped to 300 nodes, depth 3); the blob-tab page requests subgraphs
  from the panel over `postMessage`, so the full 40k-node graph is never
  rendered at once.

### Changed

- The Android client is now documented everywhere users look for clients:
  README client section and release-artifact list, ROADMAP shipped list (the
  "Mobile client (PWA)" item became "Mobile clients" with the native app
  marked shipped), the website product strip/architecture diagram/docs hub,
  and a new "Install the native Android app" how-to recipe with a direct
  APK download link resolved from the latest GitHub release.

### Fixed

- Cognitive-panel dialogs (Project Graph, Knowledge Graph, Project Wiki,
  Scratchpad, Diary, Memories, Context editor) are usable on mobile again:
  the header row now wraps and the close button is pinned to the dialog's
  top-right corner instead of riding at the end of a non-wrapping flex row,
  which pushed it off-screen on narrow viewports. Wiki and Scratchpad
  two-pane layouts stack vertically under 768px.
- The conversation-controls row above the composer scrolls horizontally on
  mobile like the header and action dock, so trailing buttons are no longer
  clipped at the screen edge; the view menu escapes the scrollbox as a
  fixed full-width sheet.

## [1.0.0-beta.205] — 2026-08-17

### Fixed

- The `get_initial_context` Bootstrap Contract and the one-way connector
  prompt are now mode-aware. Read-only publications no longer instruct the
  client to persist messages with `send_user_message`/`send_agent_message` —
  tools they deliberately do not expose; they now tell the client to never
  attempt message persistence and to use only the advertised tools. The full
  modes describe direct tool exposure instead of the dropped `use_tool`
  gateway. A real-registry security invariant test guarantees every tool
  advertised by `full_readonly` is classified read-only by `ToolApprovalGate`.

## [1.0.0-beta.204] — 2026-08-17

### Added

- Published MCP connectors gain two read-only exposure modes, `api_readonly`
  and `full_readonly`, alongside the existing `api` and `full` modes. The
  read-only variants neither advertise nor execute any write tool — the
  `send_user_message`/`send_agent_message` transport tools included — because
  some clients (ChatGPT plans that gate write actions) disable the entire
  connector for a conversation the moment the model merely attempts a
  write-annotated tool, killing reads too. Read-only status comes from the
  `ToolApprovalGate` classification and is enforced at `tools/list`, at
  `get_tool_schema`, and at execution time; in `api_readonly` the `use_tool`
  gateway only executes read-only tools and is honestly annotated
  `readOnlyHint: true`.

## [1.0.0-beta.203] — 2026-08-17

### Added

- Published MCP connectors can choose a tool exposure mode. **API mode** (the
  default) keeps the single `use_tool` gateway. **Full mode** publishes every
  PawFlow tool directly as its own MCP tool, each carrying its real behavior
  annotations (`readOnlyHint`/`destructiveHint`, derived from the
  `ToolApprovalGate` classification), so clients such as ChatGPT can invoke the
  read-only tools even on plans that gate write actions. Direct full-mode calls
  reuse the entire `use_tool` dispatch path (allowlist enforcement, one-way
  return-channel checks, async task handling, and auditing).

### Fixed

- Revoked publication keys are excluded from the publication dialog listing
  instead of accumulating forever; a revoked key can never authenticate again,
  and the rows are retained in the store only as an audit trail.

## [1.0.0-beta.202] — 2026-08-17

### Added

- Published MCP connector tools now declare MCP behavior annotations
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) so
  clients such as ChatGPT no longer treat the read-only context tools as
  unrestricted write actions and refuse to invoke them.
- The resources panel shows a published-MCP status row (agent, enabled state,
  key count) in the MCP Repository section; selecting it reopens the
  publication configuration dialog, which was previously unreachable-looking
  once a conversation had been published.

## [1.0.0-beta.201] — 2026-08-17

### Fixed

- The in-app server update pins `PAWFLOW_RUNTIME_DIR` to the new per-version
  directory inside the mounted host lineage. Previously the installer derived
  it from the updater container's own `$HOME` (`/root`): the host artifacts
  were extracted into the updater's filesystem and lost, and the recreated
  server carried a `host-app-dir` label pointing at a path that does not
  exist on the host, so the next in-app update failed preflight.

## [1.0.0-beta.200] — 2026-08-17

### Fixed

- Adaptive LLM router health is now actually adaptive: plans key candidate
  health on the service's resolved default model (the same resolution the
  client reports), so cooldown/lock state written on failures is read back at
  plan time, and crossing the transient-failure threshold without a provider
  Retry-After now applies a real exponential cooldown (30s doubling, capped at
  30 minutes) instead of an inert deadline of zero.
- Project graph relay builds work again: the extraction script called
  graphify's `extract()` with keyword arguments it never accepted, so every
  build since beta.183 failed with a TypeError; the call now matches the real
  signature and an end-to-end relay-script test guards it.
- Published MCP ChatGPT connector routes survive real one-way clients:
  missing or stale `Mcp-Session-Id` values get a synthesized session instead
  of a 404 (which ChatGPT surfaced as "Resource not found" before disabling
  the connector), the cross-authority `Origin` rejection no longer applies to
  connector-key routes (`Origin: https://chatgpt.com` is legitimate there),
  protocol 2025-06-18 is accepted, and every published-MCP request is logged
  at INFO with the key redacted. Every `tools/call` from an MCP client is now
  audited in the conversation as display-only tool call/result rows like a
  normal agent turn (context reads summarize to size + cursor instead of
  embedding the transcript into itself).
- Chat UI dialogs built from JavaScript (new conversation, import, export,
  file mention) now follow the `--pf-*` theme variable contract instead of
  ad-hoc variables no theme defines, fixing unreadable dark-on-dark dialogs
  on light themes.

## [1.0.0-beta.199] — 2026-08-17

### Fixed

- Installer-driven builds of the local agent CLI image now use the bundled
  Docker Buildx plugin and BuildKit instead of Docker's deprecated legacy
  builder. This removes the per-instruction intermediate-container delay seen
  during admin-UI updates while preserving a warned fallback for older Docker
  environments.

## [1.0.0-beta.198] — 2026-08-16

### Fixed

- Windows/WSL relays using `local=true` now route host-helper traffic through a
  tracked bridge container, wait for authenticated readiness, and re-resolve the
  current Windows route for every connection. Transient route loss recovers
  automatically without restarting the bridge or worker and never reports a
  stale connected state.
- Antigravity tmux submission tests now isolate the Docker commands under test
  from unrelated background subprocess calls, eliminating a Python 3.13 CI race.

### Security

- Every host-helper request is authenticated, including readiness and liveness
  probes used by the relay worker.

## [1.0.0-beta.197] — 2026-08-16

### Fixed

- The `least_recently_used` router strategy now orders candidates by each
  candidate's latest recorded route selection for that router; it previously
  degenerated to plain `ordered` because no recency data reached the policy.
- The router's "Explain last decision" action only reads events recorded by
  that router's own scope and identity, instead of the most recent selection
  from any router sharing the routing store.
- Migration backups of legacy `llmFailover` definitions strip secret-named
  config keys before the backup is written, matching the documented guarantee.
- The redaction-sentinel `nosec` suppression no longer emits bandit parser
  warnings in CI logs.

## [1.0.0-beta.196] — 2026-08-16

### Added

- Adaptive LLM routing with immutable per-turn route plans, deterministic
  candidate selection and handoff, terminal-success affinity, typed failure
  classification, cooldown/probe state, scoped health controls, and
  per-successful-call usage accounting. The service editor exposes filtered
  candidate references, activation, priority, health explanation, and reset.
- Native `omniroute` provider support for OpenAI-compatible OmniRoute
  endpoints, including `model: "auto"`, bounded model discovery, and
  allowlisted routing metadata.
- One-shot migration from persisted `llmFailover` definitions to
  `llmRouter`: global failures stop startup, while user/conversation failures
  are quarantined and disabled with a recoverable backup.
- ChatGPT connector access for published conversations, with copyable
  bootstrap prompts for one-way connector clients and explicit bootstrap
  initialization failure reporting.

### Fixed

- Managed relay reconnects now serialize Docker container creation per name,
  reuse the winning concurrent generation, and refuse to remove containers
  not owned by PawFlow. This prevents duplicate-name races during overlapping
  reconnects while preserving explicit restart semantics.

### Security

- Router definition revisions are canonical SHA-256 digests, routing state is
  scoped by owner and service identity, migration backups exclude secrets, and
  provider response metadata is restricted to an explicit allowlist.

## [1.0.0-beta.195] — 2026-08-15

### Fixed

- UI "Update server" on installer deployments died with exit 127 (`bash:
  scripts/install-pawflow.sh: No such file or directory`): the artifact
  directories the installer extracts to the host never included
  `install-pawflow.sh` itself, only `run-pawflow-docker.sh`, while the updater
  hands over to the installer. The updater now falls back to the copy shipped
  in the updater image at `/app/scripts/install-pawflow.sh`, the preflight
  probes for a reachable installer before the server is told to die, and both
  installers (bash and PowerShell) extract `install-pawflow.sh` into future
  artifact directories. A test pins `IMAGE_ARTIFACTS` to the installer's
  extraction list so the two can no longer drift apart.

## [1.0.0-beta.194] — 2026-08-15

### Fixed

- Project graph builds on Windows relays: the AST extraction script rode
  base64-encoded in the exec command line (~10k chars), over the cmd.exe
  8191-char cap ("The command line is too long"). The script now travels in
  the `PAWFLOW_GRAPH_SCRIPT` env var behind a tiny fixed command, and the
  incremental `PAWFLOW_GRAPH_KNOWN` map is gzip+base64 so large projects stay
  under the 32K per-variable cap.
- Project graph builds on Docker relays: the exec env carries no
  `PYTHONPATH`, so the vendored `graphify` staged at `/opt/pawflow` was never
  importable (`No module named 'graphify'`). The extraction script now
  bootstraps `sys.path` itself, preferring `PAWFLOW_RELAY_CODE_DIR` over
  `/opt/pawflow`.

### Security

- Removed the third-party `graphifyy` PyPI package (typosquat-smelling,
  unrelated to the vendored `core/graphify`) from the relay-dev Dockerfile
  and the relay image catalogs, and fixed the `detect.py` hint that suggested
  installing it.

## [1.0.0-beta.193] — 2026-08-15

### Fixed

- CCI proxy observers: HTTP framing no longer desyncs when a leaf observer
  fails. Brotli-encoded JSON responses (`count_tokens`, `event_logging`) made
  the JSON observer raise mid-parse and corrupted the connection's
  request/response pairing — later SSE responses were observed a full turn
  late under the wrong request id, the coordinator finalized turns without
  their final message, and the webchat only received the final answer and its
  `done` after the next user prompt. Leaf decode/emit failures are now logged
  and swallowed, unsupported content encodings emit `response_ignored`, and a
  terminating chunk split across TCP segments still ends the response body.

## [1.0.0-beta.192] — 2026-08-15

### Added

- Conversations deep-link via `?conversation_id=<id>`: the sidebar's
  right-click menu offers "Open in new tab", and the Android app turns
  `window.open`/`target=_blank` into a new native chat tab instead of
  silently dropping it. The Android chrome grip is a vertical edge tab.

### Fixed

- Private gateway: an explicit `POST /_gateway` submit is handled even when
  the client already holds a valid gateway cookie. The bypass used to let
  the POST fall through to normal routing (no route matches `/_gateway`),
  so the Android app showed a raw 404 JSON instead of the chat when
  reopening a still-authenticated session.
- Android app: downloads from the webchat now work — the WebView hands them
  to the system DownloadManager with the session cookie, into the Downloads
  folder with a notification. Previously the WebView ignored them silently.
- Android app: the webchat composer no longer sits under the system
  navigation bar and typing no longer makes the display jump. targetSdk 35
  enforces edge-to-edge on Android 15+; every screen root now absorbs the
  system bars, display cutout and IME as padding (`setScreen`), and the
  activity declares `adjustResize`.

### Added

- Android app: the native chrome (toolbar + tab strip) folds away to the
  right like a drawer behind a floating `|||` grip in the top-right corner
  of the web area, giving the webchat the whole screen.

## [1.0.0-beta.191] — 2026-08-15

### Added

- Webchat header: the linked-accounts icon no longer shows the username — it
  lives in the icon's hover tooltip and in the Linked accounts dialog. The
  Send button is a paper-plane icon sized like the attachment button; both
  get the dock hover zoom. A new update icon (beside notifications, admins
  only) appears when a PawFlow update is available and opens the Updates
  screen.
- Webchat chrome maximizes the transcript: the header bar, the left sidebar
  (with the tab rail, which folds with it) and the composer drawer (the
  whole zone above the prompt) each fold completely behind a small grip that
  rides the separation line (horizontal bars for the vertical menu, vertical
  bars for the horizontal ones). Sidebar and composer start closed, the
  header starts open. The Active agents box left the composer for a header
  person-icon with an active-count badge; the pending-actions label, the
  status text and the agent context gauge became header icons too (animated
  glyph while actions run, battery-style fill + percentage for context).
  Each icon toggles a popover with the full widget on click. The header
  title is the PawFlow logo linking to pawflow.allcolor.org. The chrome
  grips share the dock's hover zoom.

### Fixed

- Claude Code interactive: the Active Agents panel no longer shows the agent
  working for ~90 seconds after the tmux visibly finished. Aborted
  `/v1/messages` retries whose `request_stop` was lost piled up as "open"
  requests and held the post-Stop drain for the full pending-response cap; a
  fresh `request_start` now supersedes every earlier open request (the CLI's
  main loop streams one at a time).
- Updating the server from the Updates screen now performs the full
  command-line update: the installer-deployment updater hands over to
  `install-pawflow.sh --port <port> --pull-images` (server image, host
  artifacts, CLI tools image, both relay images, old-image cleanup) instead
  of re-implementing only the server pull + restart, which left the relay and
  CLI images behind until three more manual rebuilds.

## [1.0.0-beta.190] — 2026-08-15

### Fixed

- Simplified chat view: two detail blocks can no longer sit adjacent on
  screen. A load-more page boundary that split a turn right after its
  first tool call left an untitled, answerless "Agent activity" shell
  directly above the turn's real block (observed on a real transcript:
  `user > "Agent activity" block > "claude" block`). `turnViewReconcile`
  now folds an answerless block into the block that follows when nothing
  visible separates them — its rows move to the head of the same tabs
  (DOM order is reading order) and the empty shell disappears. A
  non-filable top-level row (approval, error) between the two still keeps
  the blocks apart.

## [1.0.0-beta.189] — 2026-08-14

### Fixed

- Simplified chat view: a load-more page bringing back a turn's own USER
  row (with the turn's block already on screen, built by a newer page) no
  longer strands the turn's narration texts at top level. The user
  boundary adopts the existing state only when its block sits directly
  under it; otherwise the rows after the user row open their own fragment
  block right there. `_turnFileRow` files a text that cannot sit under the
  block into the messages tab instead of leaving it stranded, and
  `_turnCurrentState` derives a fragment identity instead of clobbering an
  on-screen turn. This was the remaining cause of the "consecutive
  top-level agent messages + empty '0s Completed' block" report, still
  reproducible in beta.188 with the conversation's real transcript.

## [1.0.0-beta.188] — 2026-08-14

### Fixed

- Managed relay auto-reconnect: the pool-empty error of a server-managed
  relay ("Managed relay '<id>' is reconnecting.") is now recognized as a
  transport disconnect, so the request retry loop runs and calls
  `ensure_managed_relay_alive()` — the only automatic respawn path for a
  managed container that died or wedged. Previously the error was raised
  straight through and the relay stayed "reconnecting" indefinitely
  (observed ~55 min after a server restart) until a manual UI reconnect.
- Simplified chat view: a load-more page bringing back the older half of a
  turn whose block is already on screen now regroups into its fragment
  block. The fragment keeps owning rows stamped with the turn's real id
  (`state.fragOf`); previously every later row of the same turn read as an
  identity change — narration texts were reclassified as wakeup boundaries
  and left at top level (consecutive top-level agent messages), tool rows
  were filed into the live block far below, and the fragment sat as an
  empty "0s Completed" block.

## [1.0.0-beta.187] — 2026-08-14

### Added

- `delegate_status` / `delegate_result` tools (generalized from the
  flash-only `flash_status`): caller-facing observability for ALL
  delegates — flash agents and named sub-agents. Finished results are
  retained in a bounded ring (full text up to 200k chars, last 100
  results), recorded BEFORE push delivery, so a caller can always PULL a
  finished delegate's output by task_id even when the push path failed.

### Fixed

- Simplified chat view: the block boundary rule is now enforced on
  load-more and injected results. A history page no longer grows stacked
  empty "Agent activity" blocks (orphan blocks are seeded from the row's
  own turn/agent identity); two detail blocks can never sit adjacent (an
  answerless tool-only turn merges into the block that follows); and
  delegate/flash result nudges and background-tool results — user-ROLE
  rows injected by the system — file into the block's tool rows instead
  of acting as user boundaries.
- Claude Code / Codex interactive multi-message drain: a retrigger turn
  carrying N drained user messages (e.g. delegate results preempted while
  the previous turn was ending) only pasted the newest one into the live
  CLI session — the other N−1 were persisted and visible in the webchat
  but never answered. The live delta now renders every not-yet-submitted
  trailing user message in one paste, and each session deduplicates by
  `msg_id`, which also ends double-delivered delegate results (the same
  persisted message re-pasted by a later prompt build).

### Performance

- Hot-path debug logging no longer costs anything when disabled: the
  engine commit path logs `ff.size()` instead of re-reading spilled
  FlowFiles from disk, and CC/CCI event dumps (json.dumps per event,
  wire-frame base64 decode) are gated behind `isEnabledFor(DEBUG)`.
- Pool locks no longer serialize I/O: CCI `ensure_started` probes
  docker/tmux liveness and reads+decrypts the credentials file OUTSIDE
  the global pool lock (beta-186 regression); batch pool reapers
  (claude/codex/gemini) run `docker inspect` outside the lock.
- Dead/evicted CCI containers unregister their event-service session
  (slow memory leak + O(all-sessions) scans in `publish_agent_event`).
- `ConfigStore.load_params`/`load_secrets` cached by file mtime — every
  expression resolution used to re-read and re-decrypt params/secrets
  from disk, per chain.
- `KnowledgeGraph.for_user` instance cache with mtime revalidation —
  prompt builds stop re-parsing the whole JSON file every turn.
- `AgentDiary.read` reads the file tail instead of scanning the whole
  JSONL on every prompt build.
- `ConversationStore.load_agent_context` adds a stat-signature cache for
  contexts too large for the bounded cache — the CLI context gauge stops
  reloading the full context from disk on every LLM call and heartbeat.
- Sub-agent (delegate) transcript persist is now incremental and runs on
  a coalescing worker thread instead of re-serializing and rewriting the
  whole conversation synchronously every iteration.
- CCI stream accumulators (text/thinking/tool-json) switched from
  quadratic string concatenation to append+join; the CC stream
  reader→dispatch queue is bounded (blocking backpressure).

## [1.0.0-beta.186] — 2026-08-14

### Fixed

- Shared Claude Code interactive credential slots: one login can back any
  number of concurrent containers (parent agents, flash delegates, task
  sub-agents). `_claim_pool_slot_locked` now balances launches onto the
  least-loaded slot instead of enforcing 1 login = 1 live container — the
  hard cap that killed five of six flash agents instantly on a
  single-credential pool with "All Claude Code credentials are in use".
  The only credential error left is an empty pool (no `/cls` login).
- Re-checked `_retrigger_after_done` after every post-turn retrigger
  (bounded at 5 per idle transition). A delegate result landing during a
  retrigger turn was drained by that turn's final drain — PendingQueue
  emptied, so the post-idle wake saw nothing — and set the flag again,
  which the one-shot check never re-read: the message was persisted into
  context but no turn ever answered it (how the flash-agent failure
  notices and the surviving auditor's report were silently dropped).

## [1.0.0-beta.185] — 2026-08-14

### Fixed

- Made the active-agents set the single truth for a turn block's "working"
  state in the simplified view: the turn-id mismatch guard now refuses a
  terminal event only while a live successor actually exists, and the
  `list_active` poll reconciles any block left ticking "En cours" after the
  agent finished (reopenable if a racing live row proves the turn is running).
- Enforced the turn-block invariants on load-more/reload reconciliation:
  only the last block on screen may be active, and a turn-identity change
  starts a new block even with no user row. Boundaries are user messages and
  a scheduled wakeup's first assistant message only; a background-tool
  result is filed inside its turn's block like a tool call.
- Timestamped every CCI proxy log line (UTC), so event-delivery lag between
  the container proxy and the server can be diagnosed on one clock.
- Deduplicated observed tool blocks at the CCI proxy source: every
  `/v1/messages` request re-sends the whole conversation history, and
  re-emitting each historical block made event volume — and delivery lag —
  grow with the square of the turn count (root cause of the lost-final-answer
  incident: the event channel lagged the Stop hook by more than a minute).
- Held the CCI post-Stop drain while a response is still owed (an open
  `/v1/messages` request, or a follow-up after `stop_reason=tool_use`),
  bounded by `PAWFLOW_CCI_POST_STOP_PENDING_RESPONSE_CAP_SECONDS` (90s;
  20s when only a follow-up is owed). Finalizing on 2.5s of idle returned
  the turn without its final text and left the stragglers to be drained by
  the NEXT turn under the wrong turn id. The stop latch re-arms itself when
  a delayed response completes with nothing further owed.
- Named only a row with visible content as the turn's `final_msg_id` and
  gauge-patch target on CLI providers: a thinking-only flush could claim the
  marker, making the `turn_final` patch match no transcript row.

### Added

- Folded the composer zone above the prompt (conversation controls + action
  dock) behind a slim centered drawer handle. The drawer is closed by
  default and the choice persists across reloads; the active-agents mount
  stays visible in both states.

- Injected a root `AGENTS.md` into the project prompt supplement alongside
  `.pawflow.md` and `CLAUDE.md`, so agent-facing operating instructions (such
  as the release-procedure wiki pointer) reach every agent bootstrap.

### Removed

- Removed the composer stop button next to Send: the per-agent stop controls
  in the Active Agents panel are the only stop surface.

## [1.0.0-beta.184] — 2026-08-14

### Fixed

- Staged and source-hashed the integrated Graphify package in managed relay
  runtimes, with vendorable relative imports, so automatic Project Graph refresh
  cannot fail with `ModuleNotFoundError` after a server upgrade.
- Executed Project Wiki source scanners in memory instead of writing a temporary
  helper file, including on the server-local surface where relative paths resolve
  from `/` and the service user cannot write root-level files.
- Honored and validated the initial `status` supplied to `todolist` `create`, so
  agents can create active work directly without a second update call.
- Persisted sliding authenticated-session expiry on a five-minute throttle based
  on the last disk write rather than the previous in-memory request, so active
  browser logins survive server restarts while explicit logout remains immediate.

## [1.0.0-beta.183] — 2026-08-13

### Fixed

- Prevented Project Graph builds from leaving `.pawflow_graph_extract.py` in the
  source tree by executing the relay extraction script in memory instead of
  creating a helper file.
- Kept large Project Graph builds below relay output and memory limits with a
  versioned gzip/base64 transport, bounded sequential extraction, and anonymous
  edge spooling, while preserving grouped cross-file resolution for small deltas.
- Kept Project Wiki source batches pending when the summarizer raises or returns
  empty, malformed, or structurally invalid JSON, so failed generation cannot
  acknowledge sources or abort the graph/source maintenance pass.
- Separated Auto Wiki's final JSON response budget from the LLM provider's
  generation ceiling, so internal reasoning can no longer consume the entire
  document budget before any wiki content is emitted.
- Scoped agent skill assignments by user, conversation, and agent instance, so
  assigning a skill in one conversation cannot silently enable it for an agent
  with the same name in another conversation.
- Treated interactive-session consumer replacement as silent supersession rather
  than an LLM failure, and correlated backend cleanup and browser terminal events
  with their owning turn so a compact restart cannot flash a false error or make
  its successor disappear from Active Agents during a long tool call.

## [1.0.0-beta.182] — 2026-08-13

### Fixed

- Restored full-duplex relay WebSockets so an idle receive cannot starve
  filesystem tool commands, tool-relay responses, or Claude Code Interactive
  lifecycle events. This removes the 40–50 second tool-call delays and hook
  timeouts introduced in beta.180 and still present in beta.181.
- Prevented orphan-turn capture from evicting a live Claude Code or Codex
  Interactive coordinator during a slow callback. Request ownership is now an
  explicit epoch lease held through the full turn and released in every provider
  exit path instead of being inferred from recent event polling.

## [1.0.0-beta.181] — 2026-08-13

### Fixed

- Aligned the full release validation suite with the refactored Claude Code
  stream wrapper, summarizer-only skill proposals, reconnect-event relay retries,
  and the single-source package version before creating a verified release tag.

## [1.0.0-beta.180] — 2026-08-13

### Added

- Added explicit conversation-agent selectors to Diary, Scratchpad, and Memory,
  and explicit linked-relay selectors to the Project Graph and Project Wiki panels.

### Fixed

- Loaded existing Project Graph reports automatically, kept stored relay data
  readable while disconnected, exposed Diary in the action menu, and routed all
  cognitive panel actions to their visibly selected agent or relay.
- Routed Wiki maintenance, compaction memory extraction, skill proposals, resume
  summaries, and Skill Curator reviews exclusively through `summarizer_service`;
  CLI-backed maintenance calls now use isolated ephemeral sessions that are
  destroyed immediately on success, failure, or cancellation.
- Anchored managed-relay server-local relative paths in the configured workspace,
  synchronized transport retries on relay registration instead of fixed sleeps,
  and made boot orphan-session reclamation process-wide and one-shot.
- Made cold CLI bootstrap resume unfinished durable todos as active work instead
  of only listing them as context.

## [1.0.0-beta.179] — 2026-08-12

### Changed

- Accelerated relay-backed `grep` and `search` calls with bounded
  `ripgrep --json` streaming while preserving the existing structured response,
  exclusions, context, global limit, and Python fallback. Made `ripgrep` part of
  the required relay image base so minimal generated Docker relays receive the
  same fast path as the official full image.

## [1.0.0-beta.178] — 2026-08-12

### Fixed

- Registered Pocket TTS as a zero-shot voice-clone service so voices created
  with `clone_voice` can be synthesized through the normal `speak` path.
- Loaded canonical FileStore voice references through the owner-scoped ACL,
  rejected missing or malformed references, and preserved authoritative
  filenames and MIME types when registering reusable samples.

### Security

- Kept owner-scoped FileStore voice samples private during clone registration
  instead of creating temporary public gateway-key URLs.

## [1.0.0-beta.177] — 2026-08-12

### Fixed

- Synchronized the documented package version with the shipped release version.

## [1.0.0-beta.176] — 2026-08-12

### Fixed

- Registered the official relay image's nested `tini` as a child subreaper when
  relay launchers also request Docker's init process, removing the misleading
  non-PID-1 warning while preserving zombie reaping for generated relay images.
- Exposed the existing opt-in FRP service-tunnel permission in Relay Desktop and
  wired it through workspace persistence to the relay launcher.
- Added the matching admin-only **Allow tunnels (FRP)** control for managed
  server relays, persisted per relay and applied on reconnect.

## [1.0.0-beta.175] — 2026-08-12

### Added

- Bundled the pinned search-cli 0.9.0 binary in the PawFlow server image and
  added owner-scoped `webSearchConnection` services with UI-configurable
  provider selection, search mode, timeout, fallback behavior, and encrypted
  API keys for all 12 supported providers.
- Documented the complete UI setup flow and the official account portals used
  to obtain each supported provider API key.

### Changed

- Routed `web_search` through the best matching scoped search-cli service
  before PawFlow's no-key backend, with explicit provider and mode selection,
  bounded concurrent fallback requests, and visible fallback diagnostics.
- Kept search-cli in the server image only; relay images remain unchanged.

### Security

- Isolated search-cli configuration and cache directories per call, removed
  inherited provider credentials, injected only scope-resolved service keys,
  disabled search-cli logging and cache persistence, and redacted configured
  secrets from command failures.

## [1.0.0-beta.174] — 2026-08-12

### Added

- Added explicit `external_mcp` conversation agents whose runtime is an
  authenticated published MCP client rather than a PawFlow-managed LLM.
- Added idempotent initial-context, incremental-context, user-message, and
  agent-message tools for published MCP clients, plus persistent terminal
  registration and remote prompt injection on POSIX and Windows.
- Added isolated launch profiles and lifecycle integrations for OpenCode,
  JCode, Pi, and Hermes alongside Claude Code, Codex, and Agy/Gemini; Pi can
  register the published PawFlow tools dynamically.
- Added an optional per-agent LLM-service override for `flash_delegate`, so an
  external MCP agent can still launch temporary LLM work.

### Changed

- Routed webchat, same-conversation delegate, cross-conversation delegate, and
  shared-context A2A turns into an active external MCP terminal and correlated
  its final response with the original PawFlow runtime turn.
- Returned delegate and background results to the published MCP tool caller
  that started them instead of waking the configured capability-profile agent.

### Security

- Bound private terminal routing to the authenticated active-client lease,
  kept terminal targets and injection secrets out of public status, rejected
  unavailable terminals without an internal-LLM fallback, and rejected
  isolated-context A2A publication for external MCP agents.

## [1.0.0-beta.173] — 2026-08-12

### Added

- Added owner-scoped Service Tunnels that connect an approved TCP service on
  one PawFlow relay to a loopback listener on another relay through FRP STCP.
- Added webchat controls, agent actions, relay permissions, service catalogues,
  FRPS Compose deployment, and an operator guide for creating and managing
  tunnels.
- Bundled the pinned FRP 0.70.1 client with managed relay images, Relay Desktop,
  and standalone Relay CLI installers, with SHA-256 verification during builds.

### Security

- Added short-lived HMAC grants enforced by FRPS login and proxy-registration
  hooks, exact approved-target validation, owner isolation, and loopback-only
  access listeners.
- Added automatic grant refresh every 45 minutes and relay-reconnect
  reconciliation while keeping failed or explicitly stopped tunnels fail-closed.

## [1.0.0-beta.172] — 2026-08-11

### Added

- Added a declarative, availability-aware tool-routing registry. Agents now
  receive a compact `## Tool selection` block filtered to their allowed tools;
  `get_tool_schema(family=...)` returns the same family's detailed comparison
  on demand, while `tool_name=...` remains the exact schema contract.
- Added one cross-family tool-selection guide, linked from the README and public
  website, covering delegation, todo/plan/task/flow ownership, file and artifact
  tools, passive continuation versus scheduled wake-up, and user interaction.

### Changed

- Replaced the generic cognitive-tool prompt with canonical routing rules for
  memory, knowledge graph, diary, todo, scratchpad, project graph/wiki, learning,
  and history search; diary now has explicit write triggers and no longer claims
  cross-agent reads are impossible.
- Completed action and parameter hints for todo, scratchpad, and project wiki,
  and aligned README, technical docs, help-agent context, and website guidance
  with the 20 cognitive/work-state tools actually exposed by the runtime.
- Removed documentation promises for unregistered `memory_navigate`,
  `kg_surprises`, `kg_hyperedges`, and `kg_communities` tools.
- Clarified that `consult_agent` is a tool-free bridge for thin interfaces, not
  a recursive self-delegation mechanism for a full agent turn.

## [1.0.0-beta.171] — 2026-08-11

### Added

- Added a relay-scoped Project Wiki with source-hash provenance, automatic
  bounded refreshes, stale-page protection, query/page/status/lint actions, and
  a webchat browser/editor.
- Added a bounded SQLite Scratchpad isolated by user, conversation, and agent,
  with mandatory TTLs, search, CRUD actions, compact context hints, and a
  webchat editor.
- Added connected webchat panels and slash commands for browsing Project Graph,
  Project Wiki, and Scratchpad state.
- Added per-session MCP client bundles and launch commands for Claude Code,
  Codex, Agy/Gemini, plus generic stdio MCP configuration fragments.

### Changed

- Made Project Graph and Project Wiki derived state follow the active relay
  project, refresh after successful relay mutations, and reset cleanly when the
  selected project root changes.
- Promoted recurring skill drafts across distinct conversations through the
  validated user-scope resource path while retaining review and failure safety.
- Stopped the standalone MCP installer from reading or modifying global client
  configuration. One launched local client instance now loads one isolated
  PawFlow session mapped to exactly one published conversation and agent.

## [1.0.0-beta.170] — 2026-08-11

### Fixed

- Collected the complete `winpty` package in Windows Relay Desktop builds,
  including the helper executables required after PTY creation, so host-local
  terminals remain interactive instead of closing on their first input.
- Replaced the packaged Windows PTY import check with an interactive
  spawn/write/read smoke test that catches incomplete native bundles during the
  release workflow.

## [1.0.0-beta.169] — 2026-08-11

### Fixed

- Streamed filesystem, FileStore, relay, HTTP-response, and conversation-import
  transfers through disk-backed paths or bounded chunks instead of materializing
  complete files and ZIP members in server memory.
- Bounded file reads, media inspection, and vision-image resizing while
  preserving pagination, edit-conflict hashes, FileStore metadata, and relay
  text-read behavior.
- Packaged and smoke-tested the Windows PTY backend in Relay Desktop so
  host-local terminals no longer fail with a missing `winpty` module; older
  artifacts now degrade to a redirected terminal process.

## [1.0.0-beta.168] — 2026-08-11

### Fixed

- Restored remote-relay noVNC HTTP delivery by streaming relay HTTP responses
  back to the server, while keeping the authenticated WebSocket tunnel for VNC
  frames.
- Made remote host-screen sessions load their noVNC UI assets from the relay
  runtime instead of requiring a separate noVNC installation on Windows.
- Routed remote desktop audio through the outbound relay connection while
  preserving the direct path used by server-managed desktops.

## [1.0.0-beta.167] — 2026-08-10

### Fixed

- Made Enter insert a newline in the mobile webchat composer, leaving the
  visible Send button as the only mobile submit action. The same contract now
  keeps Grab-mode terminal drafts synchronized, with localized mobile hints.
- Routed remote VNC sessions through the relay's authenticated outbound
  WebSocket, including host-screen sessions whose noVNC backend runs outside
  the relay container.

## [1.0.0-beta.166] — 2026-08-10

### Fixed

- Published MCP `delegate` and `flash_delegate` calls now return their final
  asynchronous results to the originating MCP client instead of waking the
  configured PawFlow capability-profile agent. Delegating to that configured
  agent itself is now valid.
- Backgrounding a published MCP tool in PawFlow now preserves the external MCP
  caller as result owner: the UI detaches while the original `tools/call`
  remains subscribed to the real late result.
- The A2A and published-MCP dialogs now render the translated Close label
  instead of the raw `contextClose` translation key.

## [1.0.0-beta.165] — 2026-08-10

### Added

- Added A2A 1.0 HTTP+JSON agent publication with public Agent Cards,
  per-publication hashed Bearer keys, opaque client contexts, durable tasks,
  asynchronous send/get/list/cancel operations, and isolated or shared context
  policies.
- PawFlow agents can now delegate asynchronously to an agent in another
  writable PawFlow conversation and call generic remote A2A agents through the
  new `a2a` tool.
- Resources → A2A now provides guided multi-agent publication, one-time key
  lifecycle controls, copyable Agent Card/endpoints, and named local or remote
  targets selected without raw conversation IDs.

## [1.0.0-beta.164] — 2026-08-10

### Fixed

- Published MCP conversations now offer explicit native-image and text-description
  output modes for image-producing tools such as `see`. Description mode uses
  the bound agent's vision-capable LLM or delegated vision service, and reports
  a clear error when neither is available.
- Published MCP tool transcripts now persist compact image metadata instead of
  inline base64 payloads, while native MCP clients still receive the complete
  image content blocks.

## [1.0.0-beta.163] — 2026-08-10

### Added

- Conversations can now be published as authenticated Streamable HTTP MCP
  servers bound to one attached agent. Owner-only webchat controls create,
  rotate, and revoke hashed API keys, expose connection details, manage the
  active client lease, and remove the temporary CLI relay.
- Added a local stdio bridge that proxies Claude Code, Codex, Agy, and other MCP
  clients to a published conversation while sharing a client-selected project
  directory through the normal PawFlow relay. The bridge exposes local
  connect, disconnect, status, and reconnect tools.
- Release assets now include reproducible universal MCP client ZIP and tar.gz
  packages with guided Windows, Linux, and macOS installers. The wizard merges
  user-level Claude Code, Codex, and Agy configuration, preserves unrelated
  servers, creates backups, and keeps API and gateway keys in one private
  profile instead of client configuration.

### Changed

- Relay bindings can opt out of automatic default selection. MCP CLI relays
  always use this mode, so starting a client never changes the conversation or
  agent relay chosen by the user.
- A published conversation allows one active logical CLI instance with
  heartbeat-based expiry. Concurrent clients require separate published
  conversations, while reconnects from the same instance retain the lease.

### Fixed

- The bundled stdio bridge now propagates its runtime path to the relay
  subprocess and skips Unix HOME ownership checks on Windows.
- Release metadata now keeps the package version, project summary, changelog,
  and website fallback aligned, preventing the documentation consistency check
  from making every CI Python matrix fail.

## [1.0.0-beta.162] — 2026-08-10

### Added

- The webchat relay details dialog can now explicitly reconnect an editable
  managed server relay. The action replaces only its disposable container and
  preserves the relay workspace, home volume, definition, and bindings;
  standalone Relay Desktop clients remain operator-managed.

### Fixed

- Interactive Claude Code, Antigravity, and Codex STOP turns no longer crash
  when an interrupt arrives before per-turn callbacks have been initialized.
- Managed relay runtime ownership repair no longer follows dangling symlinks
  and tolerates entries that disappear concurrently while the runtime tree is
  being traversed.

## [1.0.0-beta.161] — 2026-08-10

### Changed

- Webchat attachment and relay File Manager uploads now stream the browser's
  native file body in bounded chunks instead of building multipart, FileReader,
  or whole-file base64 copies. FileStore and relay destinations publish
  atomically after the declared content length is complete, and both upload
  surfaces report native progress.
- The File Manager now uses a full-screen mobile layout that keeps its toolbar,
  search, upload status, and horizontally scrollable file table usable on narrow
  displays.

### Fixed

- Canonical FileStore links in chat now resolve through the authenticated
  same-origin file route, and relay previews use the shared media viewer instead
  of decoding binary files through the text-oriented filesystem read action.

## [1.0.0-beta.160] — 2026-08-09

### Added

- Added a phased native HTTP/2 server plan covering ALPN, direct and Caddy
  deployments, transport-neutral routing/auth, SSE, WebSocket, uploads, flow
  control, observability, performance gates, and removal of the legacy stdlib
  transport after parity.

### Fixed

- The PawFlow HTTP origin now responds with persistent HTTP/1.1 instead of the
  `BaseHTTPRequestHandler` HTTP/1.0 default. Framed responses reuse the same
  upstream connection, unframed or streaming responses close explicitly, and
  `/health` now has a content length. This removes the repeated connection and
  TLS setup that made webchat hard reload assets wait several seconds behind
  Caddy.
- Relay Desktop installers now build with PyAutoGUI and Pillow, explicitly
  include their dynamic modules in the frozen relay binary, and run the
  packaged Windows `screen_status` route before artifacts are uploaded. Status
  also probes the real dependencies, so a broken screen backend can no longer
  report itself healthy.

## [1.0.0-beta.159] — 2026-08-09

### Fixed

- Relay Desktop, generated relay-image runtimes, and development mounts now
  package the CUA screen backend alongside the main screen dispatcher. Local
  `screen` actions no longer fail with `No module named 'screen_actions_cua'`
  on packaged Windows relays. The default PawFlow screen mode also avoids
  importing CUA unless that mode is explicitly selected.

## [1.0.0-beta.158] — 2026-08-08

### Fixed

- Claude Code Interactive now applies provider-prefix-aware Anthropic Messages
  endpoint matching throughout the server-side turn coordinator and event
  service, not only in the container proxy. Z.ai and other API-key providers
  now arm request bookkeeping, clear stale Stop latches, track turn boundaries,
  and adopt orphaned turns correctly. Optional wire logging follows the same
  normalized endpoint rule. This supersedes the incomplete beta.157 fix.

## [1.0.0-beta.157] — 2026-08-08

### Fixed

- Cold-session compaction now carries the selected agent LLM service's context
  budget into the initial compact. A separate summarizer service can still write
  the summary, but its own `compact_target_tokens` no longer overrides the
  active service's configured target.
- Claude Code Interactive now observes tool results on Anthropic-compatible
  API-key endpoints with provider-specific path prefixes, including Z.ai.
  Completed native and MCP calls therefore display their results and leave the
  active state instead of remaining permanently in progress.

## [1.0.0-beta.156] — 2026-08-08

### Fixed

- TodoStore now uses static SQLite statements throughout while preserving
  parameter binding, escaped search terms, API field filtering, and atomic
  updates. This removes the Bandit B608 findings that made beta.155 CI fail
  without suppressing the security scanner.

## [1.0.0-beta.155] — 2026-08-08

### Added

- The webchat Todo List now scales to long histories with counted in-progress,
  pending, and completed tabs, debounced search, bounded 20-item pages, and a
  Load more action. Late responses are discarded after search, tab, agent, or
  dialog changes so stale data cannot replace the current view.

### Changed

- Durable todos now use an indexed SQLite store with status/search pagination.
  Existing per-agent JSON documents are imported transactionally on first open
  and removed only after the database commit succeeds. The tool and shared-
  conversation action return page totals, per-status counts, and `has_more`.
- Public documentation, README guidance, and the website now mark Claude Code
  `cc -p` and Codex app-server as legacy agent transports retained for existing
  configurations. New Claude Code and Codex services should use their
  interactive providers; the legacy identifiers remain valid for shared OAuth
  credential pools and internal compatibility paths.

## [1.0.0-beta.154] — 2026-08-08

### Fixed

- Claude Code Interactive and Antigravity tmux viewers now use the same pinned
  220x50 PTY and browser xterm grid as Codex Interactive. The browser receives
  the fixed dimensions instead of independently fitting its terminal, preventing
  sparse, displaced, or garbled rendering without resizing the live provider
  window.
- Claude Code Interactive API-key sessions now preapprove Claude Code's native
  custom-key confirmation using the CLI's own 20-character key suffix marker.
  Cold starts no longer wait for a manual yes/no answer in tmux, and PawFlow
  never persists the complete API key in that marker.

## [1.0.0-beta.153] — 2026-08-08

### Fixed

- Simplified live chat now explicitly marks token-created assistant previews so
  their durable `new_message` can reclaim the same row even after a tool or turn
  boundary rotates the in-memory stream state. Reconciliation remains
  provider-agnostic and never content-deduplicates independent durable messages.

## [1.0.0-beta.152] — 2026-08-08

### Fixed

- Simplified live chat now reconciles a durable assistant `new_message` with
  its just-finalized streaming row when `turn_complete` or a tool call arrives
  first. Exact normalized-text matching prevents the completed detail block
  from retaining two copies while keeping distinct consecutive messages apart.

## [1.0.0-beta.151] — 2026-08-08

### Fixed

- Transcript view now reconstructs canonical tool-call rows from their stored
  tool name and arguments instead of showing empty cards, and hides the separate
  empty assistant anchors that only link canonical child rows.
- Streaming assistant messages whose preview and durable IDs differ now reuse
  the preview row while replacing its DOM, stream, and dedup identities with the
  durable ID, preventing duplicate rows during the turn and after reconnection.
- Active Agents `LIVE` badges now mean that a warm CLI process is being reused.
  Cold starts no longer light the badge, and the server reuse-count poll is the
  single source of truth instead of competing with provider metadata events.

## [1.0.0-beta.150] — 2026-08-08

### Added

- The web chat now presents transient runtime notifications as temporary toasts
  outside the transcript, with an unread badge and an in-memory notification
  center in the header. The center supports details, clearing, responsive
  layouts, reduced-motion preferences, and English, French, and Spanish
  translations.

### Changed

- Compaction, terminal, desktop, help, UI error, service-install, proactive
  agent, and budget notices now use the runtime notification channel instead of
  creating chat rows. Proactive and budget notifications are SSE-only and no
  longer persist messages or enter the LLM context.
- Notification SSE handling is normalized through a single listener, including
  keyed in-place updates for progress events.

## [1.0.0-beta.149] — 2026-08-08

### Fixed

- Claude Code `cc -p` now updates the context gauge from exact native
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens +
  output_tokens` observations during the partial stream. Terminal usage cannot
  overwrite a richer cache-inclusive measurement, and no text-length estimate
  is used.
- Codex Interactive's `LIVE` badge appears as soon as provider metadata reaches
  the browser and remains stable when an incrementally prepared active context
  is merged into the active-turn row.
- Conversation theme responses and SSE events are scoped to the conversation
  that requested them, preventing a late mobile response from replacing the
  theme of the newly selected conversation.

## [1.0.0-beta.148] — 2026-08-08

### Fixed

- The Codex plugin dependency regression test now uses the installed `tomli`
  compatibility reader on Python 3.10 instead of importing Python 3.11's
  `tomllib` unconditionally.

## [1.0.0-beta.147] — 2026-08-08

### Fixed

- Clean and CI installations now install the TOML reader required on Python
  3.10 and the TOML writer used to merge Codex `config.toml` files.

## [1.0.0-beta.146] — 2026-08-08

### Added

- All six CLI providers can receive service-defined environment variables.
  Values support PawFlow expressions resolved for the user and conversation
  when the process starts, while PawFlow-managed runtime variables remain
  authoritative.
- Codex app-server and Codex Interactive services can merge an additional
  `config.toml` and install a `models.json` catalog. This enables custom
  providers such as DeepSeek without replacing PawFlow's MCP, trust, or
  context-management configuration.
- Claude Code `cc -p` now mirrors successful native TaskCreate and TaskUpdate
  calls into the same PawFlow TodoStore used by Claude Code Interactive.

### Fixed

- Claude Code `cc -p` requests partial stream events and emits text and
  thinking deltas live without replaying the completed blocks. Live provider
  usage now reaches the context gauge during the turn.
- The Active Agents `LIVE` badge now follows the provider of the active turn.
  A cold `cc -p` process with `reuse_count=0`, or a registry sampling race,
  can no longer make the badge disappear while the turn is still running.
- Conversation themes are stored and loaded from conversation metadata instead
  of a browser-local cookie map, so Chrome mobile and desktop apply the same
  per-conversation theme and fall back to the browser-global theme only when no
  override exists.
- Opening the left sidebar on a narrow mobile viewport now keeps the composer
  action dock behind the drawer instead of painting it across the resource menu.
- The logout action now uses an inline power SVG instead of the optional U+23FB
  font glyph, avoiding a missing-character box in Chrome on Android.
- CCI hook events unrelated to native task tools no longer initialize the Todo
  adapter, preventing identity-less hook connections from dropping their event.

## [1.0.0-beta.145] — 2026-08-08

### Fixed

- A cold Codex Interactive gauge after a server restart is empty again instead
  of reconstructing the dead TUI window from externalized PawFlow context. The
  unmeasured Claude Code guard remains active alongside the restored Codex
  restart guard.
- The gauge diagnostic now attributes the full token cost, including message
  overhead, when bootstrap read bodies are removed from context accounting.
- The signed `pawflow.avatar-helper` bundled artifact and catalog index are
  regenerated from the current sidebar-accordion source, restoring
  byte-for-byte release reproducibility.

## [1.0.0-beta.144] — 2026-08-08

### Fixed

- Relay Desktop lifecycle calls now send the configured Private Gateway key in
  `X-PawFlow-Gateway-Key`, including startup registration and best-effort
  cleanup. The HTTP Private Gateway accepts the same validated header as its
  WebSocket path while preserving browser challenge-cookie authentication.

## [1.0.0-beta.143] — 2026-08-08

### Added

- The web chat left sidebar is now a keyboard-accessible vertical accordion:
  Conversations owns the available height by default, while Resources expands
  to the full remaining height when selected. The helper-avatar integration and
  `/flows` command use the same controller.
- Active Agents now shows the `LIVE` badge for reused Claude Code Interactive
  and Codex Interactive sessions, with provider-specific labels and reuse
  telemetry.

### Fixed

- Claude Code's context gauge now publishes native stream usage while a turn is
  running instead of waiting until completion.
- CLI bootstrap reads are deduplicated consistently across native tools,
  PawFlow MCP wrappers, visible shell commands, and Codex code-mode pagination.
  The transcript remains intact while local gauges, compaction inputs, and the
  next cold bootstrap avoid counting or embedding the same context twice.
- Claude Code Interactive containers now receive the selected credential's
  `ANTHROPIC_API_KEY`.

## [1.0.0-beta.142] — 2026-08-08

### Fixed

- The context gauge now uses provider-native input usage for every API provider,
  including cache-read and cache-creation tokens, and publishes each observation
  during both streamed and non-streamed turns. Local token estimates remain a
  cold-start fallback and are never promoted to authoritative measurements.
  Measurements now carry explicit `request` or `session` provenance plus an
  incrementing revision, so repeated equal-valued observations still reach the
  live SSE gauge. CLI invalidation also clears the measured numerator, runtime
  window, revision and stale `real_context_size` before rebuilding a cold gauge.

## [1.0.0-beta.141] — 2026-08-07

### Fixed

- The `Context: ~x/y` note the agent reads is now the gauge's number on every
  CLI provider, instead of a recount of what is being sent this turn. The two
  are the same thing only on API providers, where PawFlow resends the whole
  context on every call. On a warm CLI session `_alc_with_provider_system_prompt`
  deliberately drops the system prompt and the history the CLI already holds,
  so the outgoing provider context is just the turn's delta — the note
  announced `~864/800000` to an agent whose session the gauge measured at
  `77965`, and `~602307` for the same conversation one restart earlier while
  its session was still cold. The note did not drift; it silently switched
  quantity with the session state, and an agent deciding how much to read from
  its bootstrap file was doing so two orders of magnitude off.
  - `_alc_inject_dynamic_metadata` reads `compute_context_usage` when
    `_is_cli_provider` is set, and divides by the window that measurement was
    taken against (Codex reports its own; a correct numerator over a configured
    guess is still wrong).
  - It falls back to counting the provider context when no measurement exists
    yet — a cold start, or a provider that has not reported. That is exactly
    the case where the provider context *is* the whole context, so the old
    behaviour was right there and is kept.
  - The note's denominator stays local. `st._max_ctx` also budgets compaction;
    moving it here would have changed what gets compacted.
  - Verified across all six CLI providers: claude-code, claude-code-interactive,
    antigravity-interactive, codex-app-server, codex-interactive and gemini all
    feed `record_observed_cli_context` / `record_observed_wire_usage`, so the
    measurement the note now reads exists on each of them. A test pins that
    list against `_CLI_CONTEXT_PROVIDERS` so a seventh provider cannot be added
    without one.

### Notes

- The gauge itself was already correct and is unchanged. For a CLI provider it
  reports the CLI session's own window — the only context that can actually
  overflow. PawFlow's stored conversation is larger (654k tokens against a
  measured 78k on the conversation above) but is never sent anywhere in one
  piece; compacting against its size would destroy history to protect a window
  that does not exist. Documented in `docs/AGENT_SYSTEM.md` so the distinction
  stops being rediscovered.

## [1.0.0-beta.140] — 2026-08-07

### Fixed

- The measured context gauge now lands *during* the turn instead of only
  after it. beta.139 recorded the observed prompt size once `coord.run()`
  returned, and every live gauge update happens strictly before that: the
  emitter recomputes on each appended message and on heartbeats, all inside
  the turn. The measurement existed but arrived too late, so the UI showed the
  reconstruction for the whole turn — 0% for a claude-code-interactive session
  whose externalized context had outgrown the native read's size ceiling — and
  snapped to the real number only after the last token.
  - The CCI turn coordinator takes a `usage_callback` and fires it at each
    `message_start` that revises the prompt size, so the turn's first API
    exchange already puts a measured number in front of every consumer. It
    publishes a snapshot of the accumulated usage (a `message_delta` carries
    only the output side) and never raises: a gauge update must not be able to
    break a stream.
  - The Antigravity coordinator carries the same callback, fired wherever an
    observed event updates `usage`. Both providers wire it on the turn path
    and the interrupt path.
- The emitter's heartbeat gate follows the measurement.
  `_context_usage_input_signature` keyed only on the PawFlow message list, but
  for an observed CLI provider the gauge moves with no new message at all — a
  long stretch of provider-side work revises the measurement on every
  `message_start`. The signature now includes the observed measurement, so an
  update that moved on its own republishes.
- The reconstructed gauge is now provider-independent, and charges PawFlow's
  own messages instead of the bootstrap file read back. A cold CLI start does
  not discard the agent's context: it renders it into `initial_context.md` and
  hands the provider a path, so the conversation exists twice in the stored
  context. The old accounting dropped the messages and charged the read output
  — `_context_messages` returned `[]` until the provider was seen reading the
  file, then `_strip_for_count` zeroed everything before that read's marker.
  That made the number depend on a native read landing, which is why it was
  useless to providers reporting only at end of turn (`claude-code`,
  `codex-app-server`) or unable to report mid-turn at all (`gemini` ACP). It
  also charged every page of a paginated read as fresh context, because the
  marker is set on the first read only. Now the messages always count and the
  read bodies never do, recognized either by the flag stamped on the result
  when the turn produces it or by the calls' own arguments across the whole
  list. `_CONTEXT_ACCOUNTING_VERSION` moves to 4, invalidating caches written
  under the previous accounting.
- Compaction no longer summarizes the conversation twice. The context builder
  has always excluded bootstrap reads from what it re-serializes; the
  compaction path had no notion of them, so it was handed the read bodies *and*
  the messages they are a copy of, with the duplicate growing by one layer on
  every cold start. Phase 0 of `_compact` applies the same filter.

## [1.0.0-beta.139] — 2026-08-07

### Fixed

- The context gauge is now measured, not estimated, for every provider that
  can report its own prompt size. beta.138 did this for claude-code; four
  others still dropped a measurement they already had.
  - **claude-code-interactive** recorded nothing, so the gauge fell back to
    reconstructing the window from the messages PawFlow holds. That
    accounting only holds while the native read of the externalized context
    returns the whole file in one tool result. Once a conversation's
    `initial_context.md` grew past the native Read tool's 256 KB ceiling, the
    read returned a size error instead of the context and the gauge reported
    0% for a session holding a full window. The MITM proxy already sees the
    exact number in `message_start.usage`; it is recorded on the turn path
    and the interrupt path.
  - **antigravity-interactive** collected the observer's usage and never
    recorded it. Same two call sites.
  - **codex-app-server** and **gemini** divided a character estimate of what
    PawFlow sent by a configured window, seeing neither the provider's system
    prompt nor the session history it resumed. app-server now reads the same
    native rollout the Codex TUI does (scoped to its `thread_id`, native
    context window included); Gemini ACP reads `promptTokenCount` from
    `meta.quota.token_count`, falling back to `totalTokenCount −
    candidatesTokenCount`.
- Cached tokens now count toward the gauge. Prompt occupancy is
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`: a
  gauge built on `input_tokens` alone reported a fraction of a Claude Code
  session's real prompt.
- One recorder for every provider. `record_observed_cli_context` moves to
  `LLMCliSharedMixin`; the Codex mixin defined its own copy and both land on
  `LLMClient`, so the gauge depended on which mixin won the MRO.
- An absent measurement records nothing instead of a 0. A stored 0 is
  indistinguishable from "measured an empty window" for every consumer and
  pinned the gauge instead of letting the reconstruction answer.

## [1.0.0-beta.138] — 2026-08-07

### Fixed

- The claude-code (`claude -p`) context gauge and compact check stayed at
  ~0 while a turn was active: the provider-measured prompt occupancy
  (`_cli_observed_context_tokens_by_stream`) was never recorded for this
  provider, so the reconstructed gauge started at 0 and only grew by the
  delta of each appended message. The provider's own `usage.input_tokens`
  (system prompt, tool schemas and session history included) is now
  recorded at every result event, exactly like the Codex interactive
  rollout, so the live gauge, the compact check and the UI agree.

## [1.0.0-beta.137] — 2026-08-07

### Fixed

- Relay Desktop: a first run behind a private gateway failed with the
  Matrix challenge page because the relay acquired the gateway cookie from
  `/auth/gateway` (a route that does not exist) instead of `/_gateway`
  (the form-encoded challenge endpoint). The relay now reuses
  `acquire_gateway_cookie()` — the same helper the CLI and manager use —
  so every API call carries a valid `_pf_gw` cookie.
- Relay Desktop: the Docker image picker listed every local image
  (alpine, hello-world, stale prealpha tags). It now filters to PawFlow
  relay repositories and merges the officially released GHCR tags, so
  every published relay image (`pawflow-relay-dev` /
  `pawflow-relay-minimal`) is offered even before it is pulled locally.
- Relay Desktop: the default download targeted `pawflow-relay-dev:latest`,
  which exists neither on Docker Hub nor as a GHCR tag. The default now
  resolves to the catalog's `relay_image_version`
  (e.g. `ghcr.io/allcolor/pawflow-relay-dev:2026.07.16`), and an image
  already present locally is no longer re-pulled.

## [1.0.0-beta.136] — 2026-08-07

### Fixed

- The authenticated username is now the label of the linked-accounts button
  itself: the header renders one control `[icon username]` instead of a
  separate username span next to the icon button. Clicking the combined
  control still opens the linked-accounts dialog, and the tooltip is
  unchanged.

## [1.0.0-beta.135] — 2026-08-07

### Fixed

- The chat UI mobile sidebar toggle stays visible above the open drawer. On
  narrow viewports the sidebar becomes a fixed overlay (z-index 150) but the
  toggle button kept its desktop z-index (100) and stayed pinned at
  `left: 12px`, so the open drawer covered the only button that closes it and
  the menu could never be closed again. The toggle now sits above the drawer
  (z-index 200) and moves to `left: 268px` (just outside the drawer's right
  edge) while it is open.

### Docs

- New step-by-step guide `docs/COMFYUI_LOCAL_SETUP.md`: install ComfyUI on a
  local GPU machine, export an LTX-Video API workflow, connect to a VPS-hosted
  PawFlow via reverse SSH tunnel (or Tailscale), configure the
  `comfyUIVideoGeneration` service, and test `generate_video` end to end.

## [1.0.0-beta.134] — 2026-08-07

### Fixed

- The context gauge for API providers now counts the full PawFlow provider
  context: messages + the provider system prompt + tool definitions. The
  gauge, the injected `Context: ~x/y` note and the persisted snapshot all
  derive from the same counter (`count_context_tokens`), so they can no
  longer disagree — a conversation shown at 1% while its context note said
  12% was the symptom of the gauge counting a message subset without the
  system prompt and tool schemas that travel with every request.
- The platform agent prompt now forbids editing source files through bash
  (heredocs, `echo >`, python inline, `sed -i`): the dedicated edit tools
  are the only supported path, keeping edits atomic and auditable on every
  install.

## [1.0.0-beta.133] — 2026-08-07

### Fixed

- Codex interactive cold-start readiness now parses tmux pane state with a
  printable delimiter. Tmux sanitizes literal tabs to underscores, which made
  every otherwise-valid thread and editable-cursor observation fail and forced
  the first prompt through the advisory best-effort path.

## [1.0.0-beta.132] — 2026-08-07

### Fixed

- Codex interactive context usage now comes from the native rollout's latest
  `last_token_usage.input_tokens` and `model_context_window` values after every
  internal Responses exchange. The gauge and auto-compaction cache no longer
  drift from tiny proxy deltas or stay near zero during long tool-driven turns;
  cumulative billing usage is explicitly excluded from context occupancy.

## [1.0.0-beta.131] — 2026-08-07

### Added

- The webchat action dock can now show the selected agent's durable TodoStore in
  a translated, read-only dialog, including status groups, details, dependencies,
  owner and update time. Shared-conversation readers resolve the owner's scoped
  list and all task text is rendered through safe DOM text nodes.

### Fixed

- Codex interactive cold-start readiness no longer depends on release UI copy,
  model names or `pane_current_command` (the real tmux pane runs through
  `sh -c`). PawFlow now combines the current launch's Codex thread-writer lock
  with a stable editable-cursor/input state from tmux before the first paste.

## [1.0.0-beta.130] — 2026-08-07

### Fixed

- The global theme and language controls now sit immediately after the
  PawFlow Agent title, ahead of dynamic status, loading, agent and usage
  badges, so their header position remains stable while work starts and stops.
  Both controls reuse the action dock's translated tooltip portal and hover
  zoom while retaining their existing selectors.

## [1.0.0-beta.129] — 2026-08-07

### Fixed

- Pending webchat UI actions now reconcile against the synchronous status
  registry every 1.5 seconds while work remains. Results are recovered even
  when the per-tab SSE stream expires or reconnects at the wrong moment;
  silent polling calls participate without appearing in the visible activity
  indicator, and only one reconciliation request may run at a time.

## [1.0.0-beta.128] — 2026-08-07

### Fixed

- Codex interactive cold starts no longer reject the first webchat prompt when
  a newer TUI layout does not expose the expected composer markers. Readiness
  detection is advisory again, and a pane reaction after the single tmux paste
  is accepted as transport evidence so PawFlow can submit the prompt without a
  manual Enter.

## [1.0.0-beta.127] — 2026-08-07

### Fixed

- `use_tool` now treats `arguments_json` as the canonical target-tool input.
  A parameter misplaced on the wrapper envelope is merged only when the target
  schema declares it and the inner object omitted it; an envelope duplicate can
  no longer overwrite the canonical value. Undeclared narration remains
  harmless, while genuinely unknown parameters still fail loudly.
- The conversation action dock now stays centered when the Active Agents panel
  or the left Conversation controls panel is hidden. Equal desktop side tracks
  make visibility independent from the dock position, while the narrow-screen
  layout keeps its single-column stacking behavior.

## [1.0.0-beta.126] — 2026-08-07

### Fixed

- `repair_tool_sequence` left un-addressable tool calls (empty id) on an
  assistant message that had text: the strip was gated on a condition that
  could never hold, so the repaired list still carried the assistant
  `tool_calls` block with no results behind it — the exact 400 the module
  exists to prevent.
- `use_tool` no longer fails a call because of narration attached to the
  envelope. `bash` declares `description`, so models send it to every tool;
  merging it into the target arguments made `read` and others answer
  `unknown argument(s) ['description']` for calls that previously ran.
  `description` / `explanation` / `reasoning` / `thought` now reach the target
  only when it declares them. Genuinely unknown keys stay loud.
- Vision fallback: a stale `_pawflow_current_user_message` marker on an older
  user message no longer wins over a newer user message that carries images,
  which would have sent those image parts raw to a non-vision LLM. An unmarked
  user resume row persisted after the real prompt still does not move the
  boundary — only image parts override the marker.

## [1.0.0-beta.125] — 2026-08-07

### Fixed

- Global theme and language now sit on the left of the chat header immediately
  after usage, while linked accounts, the authenticated principal, and power
  remain grouped on the right as originally specified.
- The linked-account and power controls now reuse the conversation dock's
  styled tooltip, translated label/description, and 1.4x hover zoom instead of
  browser-native title tooltips.

## [1.0.0-beta.124] — 2026-08-06

### Fixed

- The UI could stall under continuous traffic: the background action
  scheduler used a process-wide "last enqueue" deadline, so every new
  UI action (tabs, users, refreshes) postponed the whole queue
  indefinitely and actions were never submitted. Each queued action now
  owns its own ready-at deadline, so the queue advances even under
  bursty refreshes; latency is logged (queue wait / handler duration)
  for future diagnosis.
- Codex interactive prompts could be silently lost on cold sessions:
  a paste sent before the composer existed is discarded by Codex with no
  tmux error. Prompt readiness is mandatory again (bounded 45s, same as
  Claude Code) and now also recognizes the structural `> ` composer line
  while excluding the permanent `>_` header — robust to Codex release
  copy changes. Paste verification requires evidence in the composer
  (attachment chip or running turn), not an unrelated pane redraw.
- Global theme and language controls now live in the right-hand chat
  header instead of the conversation action dock. The authenticated
  principal is shown between the linked-account control and the
  power-styled logout button.

## [1.0.0-beta.123] — 2026-08-06

### Fixed

- Concurrent API-provider conversations could execute a tool with another
  conversation's mutable handler scope. A DeepSeek `todolist.create` and
  `schedule_continuation`, for example, could report success while writing into
  an active Codex conversation. Tool dispatch now forks the registry and
  configures execution-local handlers with the current user, conversation,
  agent, client, and model immediately before execution.
- Relay HTTP streaming failures now mark the producer complete and remove the
  spool synchronously before surfacing the error, even while the relay runner is
  still unwinding.
- Chat controls now keep realtime voice, grab, and refresh together in the
  conversation controls row; linked accounts and logout remain visible in the
  header, and the linked-account dialog keeps long identities readable beside a
  bounded unlink action.
- Simplified live view no longer leaves an active turn displayed as completed
  after reload: an in-flight history marker or a fresh SSE row reopens a tail
  block that was closed only by reconstruction, while real terminal events stay
  final.

## [1.0.0-beta.122] — 2026-08-06

### Fixed

- `use_tool` silently dropped any top-level parameter placed next to
  `arguments_json` (e.g. `use_tool(tool_name="bash",
  arguments_json="{...}", local=true)`): the target tool then ran on
  the wrong surface (relay instead of local) with no error. Unknown
  top-level keys are now merged into the inner arguments so the target
  handler receives them — genuinely unknown keys are rejected loudly by
  schema validation instead of being ignored.
- `use_tool` failed with "missing tool_name" when the tool name was
  placed INSIDE the argument payload (`use_tool(arguments_json="{\"tool_name\":\"bash\",\"command\":...}")`
  with no top-level `tool_name`). The tool name is now recovered from
  the parsed payload, and the same recovery applies through nested
  `use_tool` wrappers (payload arguments used directly when the
  envelope keys are absent).

## [1.0.0-beta.121] — 2026-08-06

### Fixed

- The vision fallback (image → description via the delegated vision
  service) could silently stop running on real turns: the dynamic-metadata
  injection rebuilds the last user message as a fresh LLMMessage and used
  to drop the `_pawflow_current_user_message` marker, so the fallback
  guard (`has_current_vision_inputs`) saw no current prompt, skipped the
  transformation, and raw image parts reached the text-only LLM — the
  exact trigger of the provider 400 "insufficient tool messages" when
  two `see`/`read` tools ran in parallel. Rebuilds now carry all
  `_pawflow_*` attributes (`_alc_carry_pawflow_attrs`), and the fallback
  no longer depends on the marker alone: when no message is marked, the
  most recent user message is treated as the active prompt, so a
  non-vision LLM can never receive image parts.

## [1.0.0-beta.119] — 2026-08-06

### Fixed

- Agent turns could die with a 400 "insufficient tool messages following
  tool_calls message" from strict OpenAI-compatible providers: a cancel,
  compact, rewind, or out-of-order in-turn tool results could leave an
  assistant `tool_calls` block without its tool responses. A new shared
  repair (`core/llm_tool_sequence`) rebuilds the context before every LLM
  send so each `tool_calls` block is immediately followed by its results —
  moving results persisted before their assistant, dropping duplicates and
  orphans, and synthesizing `[Result unavailable...]` for unanswered calls
  (preempted turn). The agent setup repair delegates to the same helper.
- CI: restored the `LLMMessage` import used by `_alc_setup` type
  annotations and synced `PROJECT_SUMMARY.md` to the shipped version.

## [1.0.0-beta.120] — 2026-08-06

### Fixed

- Chat UI simplified turn view: detail panels now read like a roller —
  opening a tab anchors it at the newest row (bottom) and the active panel
  follows the stream while the turn runs (unless the reader scrolled up).
  The ephemeral cue column reads top-to-bottom like a terminal: the newest
  cue settles at the bottom at full strength, older ones roll up above it
  and fade (CSS mask/transform updated).
- Agent prompt policy: new Section 8 "Heredoc & Shell Payload Safety" —
  agents must prefer `write`/`edit`/`apply_patch` over hand-escaped shell
  payloads, and verify a payload arrived intact before executing it.
- `'StreamEmitter' object has no attribute 'on_status'` crash when an agent
  ended its turn while background tasks were still pending ("Waiting for
  background tasks..." path). The hook was called but never implemented on
  any emitter; it now exists as a no-op on the base emitter and publishes a
  transient `status` SSE event from `StreamEmitter`, surfaced in the chat
  UI status bar without polluting the timeline.
- `AgentResultWaiter` hygiene bound is now **activity-based** instead of
  creation-based: a live turn (one that keeps emitting progress/tool/token
  events through the bus) is waited on for as long as it runs — the 30 min
  ceiling now applies only to DEAD entries (agent crash, preempt, cancelled
  queued submission) that would otherwise leak for the server's lifetime.
  This restores the "NO implicit timeout" contract for Telegram/Google Chat
  turns that legitimately run longer than 30 minutes.
- `resolve_relay_aware_url(transform_relay=True)` is verified to never
  return a private-IP relay-shaped URL verbatim (non-regression test for
  the SSRF fix).
- **400 "insufficient tool messages" on parallel `see`/`read` image
  results** (second root cause after the sequence repair): the OpenAI
  builder turned image-bearing tool results into a `user` message emitted
  BETWEEN the tool results, so a strict provider saw
  `assistant(tool_calls) -> tool -> user -> tool` and rejected it. Image
  user messages are now deferred and flushed after the whole tool block —
  `assistant(tool_calls) -> tool* -> user(image)*` is valid by construction
  even when a delegated-vision description fails.
- **`see`/`read` on a text-only LLM now return the vision-model
  description at tool-execution time**: when the active `llmConnection` has
  `supports_vision: false` and names a `vision_llm_service`, image tool
  results are described immediately (`core/vision_describe.describe_tool_result_images`)
  and the tool result becomes plain text — the text-only model perceives
  the file and strict providers never see images inside tool results. When
  the LLM is vision-enabled, or no delegated service is configured, the
  native multimodal result is unchanged.

## [1.0.0-beta.118] — 2026-08-06

### Security

- **SSRF bypass closed in relay-aware URL validation** (`core/relay_proxy_url.py`):
  a URL shaped `http://<private-ip>/<host>:<port>/...` was classified as a
  legacy relay URL and returned verbatim, skipping the private-address
  rejection — verified exploitable against cloud metadata (169.254.169.254),
  127.0.0.1, `[::1]` and RFC1918 hosts via ComfyUI media URLs and other
  `allow_private=False` paths. The relay netloc is now checked for
  private/local addresses even when the URL is not transformed; a real relay
  id (an identifier, never an IP) is unaffected. Regression test added.
- **Path traversal via agent names closed** (`claude_code_interactive_pool.py`,
  `codex_interactive_pool.py` via shared base, `claude_code_session.py`,
  `codex_session.py`): `agent_name` is user-controlled (`agent_create`
  accepts any string) and was embedded raw into host-side and in-container
  session workdirs, where credentials/certs are written. A name like
  `../../x` could escape the session root; names are now sanitized
  (`/`, `\`, `:`, `..` → `_`).
- **Telegram bot token no longer persisted inside `telegram.raw`**
  (`telegram_receiver.py`): the live bot token and owner id (injected into
  every update) were dumped verbatim into the `telegram.raw` FlowFile
  attribute, exposed to any attribute-dumping/logging flow. The functional
  `telegram.bot_token` attribute remains; the raw dump strips the secret keys.
- **PFP registry SSRF + DoS hardening** (`core/pfp_registry.py`,
  `core/pfp_package/_pp_mod2.py`): registry URLs pointing at
  private/loopback/link-local addresses are rejected; registry index and
  package downloads are streamed with hard caps (1 MB index / 128 MB
  package) instead of reading the full body first; zip entries are capped
  per-entry (64 MB) and in total (256 MB uncompressed) against zip-bombs.
- **WebSocket frame size caps** (`services/code_server_proxy.py`,
  `services/_relay_ws.py`): a single connection (client or user-run relay)
  declaring a huge 64-bit frame length could OOM the server; frames larger
  than 64 MiB are now rejected.

### Fixed

- `closeAudioTab()` in the chat UI used an invalid CSS selector (missing
  `]`), throwing and leaving the dock button clickable, never switching back
  to Chat and rendering the main area blank; the selector is fixed.
- LLM-provided `start_line`/`end_line` in the Edit tool-call renderer were
  interpolated into `innerHTML` unescaped (XSS gap in the message render
  path); they are now escaped like every sibling field.
- Corrupt `pawflow_msg_history` localStorage no longer kills the whole chat
  UI at load (`state.js`).
- The admin update-wait poll stops when the dialog is closed instead of
  running up to the 600 s timeout and force-reloading minutes later.
- Missing i18n keys `refreshConversationTitle` and `noPackageResults` added
  to en/fr/es catalogs (previously rendered as raw key text).
- `AgentResultWaiter` no longer leaks pending entries forever: queued
  submissions and turns that never emit `done` (agent crash, preempt) are
  swept after a 30-minute TTL, and an unbounded `wait()` is bounded by that
  TTL.

## [1.0.0-beta.117] — 2026-08-05

### Fixed

- The NeXT-style action dock (Terminal / Agent Tmux / VS Code / Desktop /
  Audio row in the composer) now zooms on hover like a macOS dock: each item
  scales to 1.4 with a spring easing (`cubic-bezier(0.34, 1.56, 0.64, 1)`),
  origin at the bottom center so the item grows upward, an accent border and a
  drop shadow, and the hovered item rises above its neighbours. The container
  no longer clips the overflow. The previous attempt animated the task-tab
  dock (right-edge vertical stack), which is a different element that is not
  the one users see.
- The chat UI template is now part of the served-asset signature: editing
  `template.html` (which carries the whole inline `<style>` block) invalidates
  the server's in-memory HTML cache immediately, so CSS-only changes reach the
  browser within the 5s signature check without a server restart. Before this,
  a running server kept serving the old template forever and a browser
  hard-reload showed stale CSS because the server itself never re-read the
  file.

## [1.0.0-beta.116] — 2026-08-05

### Added

- Vision `llmConnection` services expose `vision_thinking_budget` in the
  service editor: reasoning models (gpt-5.x, o-series) narrate their process
  when given a budget, and the budget now flows through to the vision call.
  Set `0` for no requested thinking, a small value (e.g. 512) to cap it, or
  `-1` to disable explicitly.
- The floating task-tab dock (right edge) zooms on hover like a macOS dock —
  `scale(1.35)` with a spring easing (`cubic-bezier(0.34, 1.56, 0.64, 1)`),
  origin anchored to the dock edge, deeper shadow and raised stacking so the
  hovered tab passes over its neighbours.

### Fixed

- Tool argument JSON repair now recovers unescaped double quotes inside string
  values: a shell command embedding its own quoting (`grep -n "pattern" file`)
  previously failed the whole payload with `Expecting ',' delimiter`, wasting
  the call. Every plausible opener/closer pair is re-quoted and the first
  variant that parses is accepted; mixed invalid `\'` escapes plus bare quotes
  are fixed in sequence. Repairs run only after strict parsing fails, so a
  valid payload is never altered.
- Assistant `tool_calls` are sanitized at message-build time: a call with no
  following `role: tool` response (the fingerprint of a turn cancelled
  mid-call) is a hard provider 400 that then poisons every later call in the
  conversation. The OpenAI builder strips unanswered calls, keeping any text,
  and drops a message left with neither text nor calls; compaction applies the
  same cleanup so the persisted context heals at the next compact.
- Delegated vision never falls back to the model's reasoning output as the
  image description. Reasoning models (gpt-5.x, o-series) narrate their
  process ("I need to...", "I'll make sure...") which previously polluted the
  persisted description and the main prompt at every turn; only the final
  content is an acceptable description.
- Thinking streaming is chunked at ~250 characters on word boundaries instead
  of one delta per token, keeping the detail block readable mid-stream. The
  durable `thinking_content` still supersedes the preview at turn end.
- The simplified turn view ignores a `done`/`cancel` that names a different
  turn while the current block is `working`: when the reader sends a message
  mid-turn, the server preempts it and the old turn's cancelled `done` lands
  after the successor's block is already open. Applying it positionally
  stamped the live block "Completed" (frozen clock, no rain) over an agent
  still working; the preempted turn was already closed at its own user
  boundary.
- A system notice (compact finished, git pruned) never opens a turn block.
  `/compact` while the agent is idle is the standing case: the notice arrives
  with no open turn and nothing ever closes a block a notice opened, so an
  orphan block sat in "working" with rain and a ticking clock forever. With a
  turn open the notice is filed into that block; with none it stays top level.

## [1.0.0-beta.115] — 2026-08-05

### Fixed

- Relay HTTP spool cleanup is now atomic across producer and consumer threads.
  Exhausting the response iterator cannot return before the temporary file has
  actually been removed, eliminating the Python 3.11 CI race that occasionally
  left `/tmp/pawflow-relay-http-*` behind.
- Delegated-vision MCP tool unwrapping failures are logged at debug level
  instead of being silently swallowed, keeping the full Bandit security scan
  clean while preserving the original tool name as the safe fallback.

## [1.0.0-beta.114] — 2026-08-05

### Added

- OpenAI and OpenAI Responses services expose `reasoning_effort` in the
  service editor (`minimal`, `low`, `medium`, `high`, `xhigh`, or `max`), with
  an empty value preserving the model default.

### Fixed

- Claude Code and Codex interactive prompts are loaded as one tmux buffer and
  sent with exactly one bracketed `paste-buffer -p`. Visual verification can
  reject an unconfirmed transport but never replays and duplicates the prompt.
- Compact Codex `UserPromptSubmit` receipts without an explicit digest now
  acknowledge the newest tracked PawFlow injection instead of triggering
  repeated submit keys after the turn has started.
- API-provider preemption now repairs strict tool-call ordering before the
  restarted OpenAI request: every assistant `tool_calls` block is immediately
  followed by one result per call id, reusing persisted results in call order
  and synthesizing an explicit cancelled-result placeholder only when needed.
- Delegated vision operates only on the current user prompt and current-turn
  `read`/`see` results. Descriptions replace raw images in the live context and
  persist on attachments, while duplicate message UUIDs cannot reintroduce or
  redescribe the same upload.
- Cold CLI sessions now honor `compact_threshold_pct` exactly. The removed
  hidden 40% threshold no longer compacts a context while the configured gauge
  is still below its visible threshold; `0` consistently disables proactive
  compaction.
- Direct Anthropic, OpenAI chat-completions, and OpenAI Responses streams emit
  text and reasoning callbacks as wire deltas instead of buffering the whole
  block until completion, restoring immediate token/thinking previews.

## [1.0.0-beta.113] — 2026-08-05

### Fixed

- **API soft preempt** — a user message arriving while the worker is between
  iterations (tool call in flight, no active HTTP connection) no longer kills
  the thread, bumps the generation and reloads the whole context from disk.
  It now cancels the in-flight tool calls (they reply `cancelled`), queues
  the message, and the live worker drains it at the start of its next
  iteration — the LLM reads it naturally, with zero disk reload and zero
  context rebuild. A preempt during an actual LLM stream still aborts +
  fast-restarts, since a message cannot be injected into a stream that
  already left.
- **Vision fallback describes only the current turn** — images in older
  context messages are no longer re-submitted to the vision model at every
  turn (prompt bloat) or re-described after a server restart (cold cache,
  network calls for nothing). Only the current user message and the tool
  results produced after it are described; older images are replaced by a
  short placeholder.
- **Vision descriptions are persisted** — once a user image is described,
  its attachment is patched in the store as `described` with the description,
  so the context loads the description as text instead of re-creating the
  `image_ref`: the image is never re-submitted to the vision model on later
  turns, even after a restart. The UI keeps the attachment (image still
  displayed).

## [1.0.0-beta.112] — 2026-08-05

### Fixed

- User messages are no longer duplicated on a start-of-turn (agent idle): the
  message is pre-persisted by the streaming ingress and also sits in the
  executor file queue; the drain now skips any flowfile or queued message
  whose msg_id is already present in the context being built. This also
  covers the busy-agent (PendingQueue) case via the same msg_id check.

## [1.0.0-beta.111] — 2026-08-05

### Fixed

- API preempt no longer surfaces an error turn: aborting the in-flight HTTP
  stream now converts the closed-socket read error into a clean
  `AgentCancelled` interruption (openai, openai-responses, anthropic).
- User messages are no longer duplicated when the agent is busy: a message
  pre-persisted by the streaming ingress and queued while the agent is
  active is not re-appended on the next drain, so the transcript and agent
  context keep a single user row (one uploaded image is described once).

## [1.0.0-beta.110] — 2026-08-05

### Added

- Added a cross-platform code signing plan (`docs/CODE_SIGNING_PLAN.md`):
  Authenticode/Azure Trusted Signing for Windows, Developer ID + notarization
  for macOS, OpenPGP for .deb/.rpm, key separation, costs, and rollout phases.

### Fixed

- API providers (openai/anthropic/responses) now preempt like CLI providers:
  a new user message aborts the in-flight HTTP stream, cancels in-flight tool
  calls, and fast-restarts a fresh turn with the message instead of queueing
  it behind long-running tools.
- Tool calls with slightly-broken argument JSON no longer render as empty
  `Bash()` in the chat: the raw arguments are shown (truncated) and empty
  parens are omitted.
- Vision fallback no longer issues duplicate network calls when parallel
  workers describe the same image: per-image single-flight coalesces them.

## [1.0.0-beta.109] — 2026-08-05

### Added

- Added a configurable `vision_max_tokens` parameter on llmConnection services, so
  verbose vision models (e.g. GPT-5.6 Luna) can raise the per-image description
  budget beyond the 1024-token default.

### Fixed

- Fixed `flash_delegate` failing with "requires an active source agent and
  llm_service" when invoked by API providers through `use_tool`: the calling
  agent identity and LLM service are now derived from the conversation agent
  config instead of relying solely on the `_do_execute` injection, and missing
  context reports an actionable error instead of a bare BUG string.
- Restored `delegate` source identity (self-call detection, result delivery) on
  the same `use_tool` path.
- Kept the final chat message visible after a background result.

### Changed

- Vision fallback rework: the v2 describe prompt forbids process narration and
  invented text (models like Kimi leaked chain-of-thought and hallucinated
  URLs/dates), the `max_tokens` default dropped from 4096 to 1024, and images
  in one pass are now described in parallel (up to 4 workers).
- The vision-description cache key now includes the token budget, so a
  truncated description is never served from cache after the budget is raised.

## [1.0.0-beta.108] — 2026-08-05

### Fixed

- Kept conversation controls, actions, active agents, and pasted-file previews
  inside the main chat layout, so opening the left sidebar no longer makes them
  overlap it.
- Replaced clipped action-dock hover labels with shared CSS-styled tooltips that
  do not create a horizontal scrollbar.

### Changed

- Moved conversation actions into a horizontal dock between conversation controls
  and active agents, and rendered pasted files as stacked thumbnails beside the
  prompt with an expandable count after the first three.

## [1.0.0-beta.107] — 2026-08-05

### Changed

- Added a compact left-side conversation-controls dock mirroring the active-agent
  panel, with permissions, TTS, STT, and View controls beside the prompt.
- Moved account linking and logout from the header into the always-visible right
  action dock, and added flags to the language selector.

### Fixed

- Restored the administration gear menu by rendering its expanded panel outside
  the right dock's clipped scroll container and preserving it during click bubbling.

## [1.0.0-beta.106] — 2026-08-05

### Changed

- Simplified the web chat chrome: theme and language now follow usage in the
  compact header, account linking and logout share one split control, conversation
  actions and administration use an always-visible right dock, and view, live
  speech, and permission controls now sit above the prompt.

## [1.0.0-beta.105] — 2026-08-05

### Fixed

- Aligned scheduled wake-up generation ownership with the agent-qualified active
  turn key, preventing a concurrent user message from starting a second Codex
  interactive consumer and evicting the wake-up's live session.
- Stopped reporting translated Docker-host skill mount sources as missing from a
  containerized server while preserving a warning for genuinely absent local
  sources.

## [1.0.0-beta.104] — 2026-08-05

### Added

- Added a deferred integration plan for an independently distributed Maestro PFP
  connector, with explicit licensing gates, a strict HTTP allowlist, durable job
  ownership, security requirements, phased delivery, and go/no-go criteria.

### Fixed

- Gave scheduled continuation wakeups their own runtime turn identity and active
  marker, preventing resumed work from being filed under a detail block that was
  already labelled `Completed` while the agent was visibly still running.
- Made the skill-learning loop produce observable structured outcomes instead of
  silently returning `null` or swallowing provider failures, stopped broad
  same-domain skills from suppressing recurring operational procedures, and
  exposed pending drafts in the Memories UI with reviewed conversation-scope
  promotion and delete actions.
- Prevented flash and regular delegates using CCI, Antigravity, or another
  three-argument interactive turn callback from failing before inference with
  a callback-arity `TypeError`.
- Made long-running-work guidance require stable workspace log and exit-status
  files because temporary FileStore tool results may be removed by TTL or
  compaction before a scheduled continuation wakes.
- Enforced each PFP UI extension's signed hook declarations at browser runtime,
  including realtime media hooks; removed a second full content pass while
  preserving per-request asset integrity checks; and made multi-file
  `apply_patch` rollback continue after individual restoration failures,
  preserve the original exception, and remove directories it created.
- Made extension-repository creation reject concurrent duplicate keys instead
  of silently overwriting one winner, released failed Google Chat event claims
  for retry, and made protected-path approval inspect nested `batch_edit` paths
  plus every target encoded in an `apply_patch` payload.

## [1.0.0-beta.103] — 2026-08-04

### Fixed

- Removed the installer updater's runtime `apk add bash` bootstrap by running it
  in the already-local PawFlow server image, and made the admin update dialog
  report an exited `pawflow-updater` with its bounded logs immediately instead
  of waiting for the ten-minute restart timeout.

## [1.0.0-beta.102] — 2026-08-04

### Added

- Added the installable `pawflow.avatar-helper` package with a dedicated helper
  agent and skill, a fixed-target `pawflow-ui` semantic tool, and a
  non-destructive browser overlay that can describe, open, scroll to, and
  highlight PawFlow interface surfaces without submitting forms or accepting
  arbitrary selectors.

### Fixed

- Restored terminal paste from the browser clipboard with `Ctrl+V` and the
  right-click action by routing both handlers to the terminal's active WebSocket.

## [1.0.0-beta.101] — 2026-08-04

### Added

- Added the universal durable `todolist` tool, scoped per user, conversation,
  and agent, with live API context injection, cold CLI bootstrap injection, and
  successful `TaskCreate`/`TaskUpdate` mirroring for Claude Code interactive.
  The common agent policy now proactively uses it for multi-step and deferred
  work and hands operations longer than about one minute to a passive
  `schedule_continuation` instead of polling.

### Fixed

- Retried Codex interactive prompt submission with `Enter` until a matching
  hook, provider request, or terminal state proves delivery, and failed
  explicitly instead of leaving pasted text waiting for manual submission.
- Recreated interactive CLI containers whose tmux session died while Docker
  still reported the container healthy.
- Reset a stale Codex interactive context gauge during post-restart page
  hydration instead of displaying the dead provider session at 100% until the
  next user turn.

## [1.0.0-beta.100] — 2026-08-04

### Fixed

- Recovered the webchat automatically when an update briefly returns 502 for a
  deferred JavaScript module, and kept image paste/drop upload handling inside
  the attachment module so an unrelated Files-panel load failure cannot leave
  `handleFiles` undefined.
- Stopped the newest historical conversation detail block from appearing as
  running after reload unless the server reports a live turn or the live stream
  has actually fed that block.
- Added deterministic official PFP builds and a GitHub release gate that verifies
  bundled signatures unconditionally and reconstructs artifacts byte-for-byte
  when `PAWFLOW_PFP_SIGNING_KEY` is configured.
- Excluded Python bytecode caches from signed PFP source collection so local
  imports cannot change package bytes or ship stale executable bytecode.

## [1.0.0-beta.99] — 2026-08-04

### Added

- Added feature-neutral PFP contracts for extension repositories, immutable
  authenticated assets, inert AudioWorklets, realtime media lifecycle events,
  and authorized semantic browser actions.
- Added the installable `pawflow.avatar-runtime` package with lazy
  TalkingHead/HeadAudio/MotionEngine rendering, repository controls, semantic
  actions, voice-alias bindings, explicit teardown, and reproducible vendoring.
- Added an independent deterministic MIT starter avatar pack plus lifecycle,
  cold-boot, package-budget, tamper, disable, uninstall, and recovery coverage.
- Grouped PFP Depot entries by package category for easier catalog navigation.

### Fixed

- Made multi-file `apply_patch` validation atomic so a rejected later hunk never
  leaves earlier files modified.
- Relay runtime staging no longer hashes or copies cached Python bytecode, and
  hot-reload compiles the mounted source directly before refreshing the facade,
  preventing stale functions after split-module updates.
- Normalized Voicebox WAV responses to the canonical audio MIME type.

## [1.0.0-beta.98] — 2026-08-03

### Added

- Added a permanent **PFP Depot** in the left sidebar. It lists the bundled
  catalog and each user's validated uploads, supports local `.pfp` upload,
  inspection and installation, and lets owners delete uploaded artifacts.
- Server-managed relays with server-local execution enabled now expose the
  Local/Remote chooser for Terminal and Desktop, with local PTY, noVNC, audio,
  lifecycle, and authenticated proxy routing handled inside PawFlow.

### Fixed

- Incremental backup encryption now uses self-describing AES-GCM envelopes with
  a fresh embedded scrypt salt, removing the shared `salt.bin` overwrite race
  and keeping concurrent or partially unreadable backups independently
  decryptable.
- Google Chat direct messages now validate that their configured conversation
  belongs to the bot owner, and collective spaces are restricted to
  transport-enforced read-only turns.

### Security

- ComfyUI validates every external media input URL, including redirect targets,
  against the relay-aware SSRF boundary before downloading it.
- Tool calls are normalized and expression-resolved once before authorization,
  then executed from the frozen prepared arguments so approved filesystem
  targets cannot change between approval and execution.

## [1.0.0-beta.97] — 2026-08-03

### Added

- Added a deployable, owner-scoped Google Chat agent flow with Google-signed
  webhook verification, deny-by-default space authorization, per-space
  conversation bindings, threaded live replies, attachment ingestion, and
  transport-enforced read-only turns by default.

### Fixed

- Telegram now renders structured `rich_message` payloads as readable text
  instead of forwarding their raw JSON representation.
- Telegram photo albums now debounce updates sharing a `media_group_id` into a
  single agent turn and materialize every photo as its own FileStore attachment.
  Album downloads run after grouping so network latency cannot split the album.

## [1.0.0-beta.96] — 2026-08-03

### Fixed

- Grab now mirrors `Shift+Enter` in both places: it sends CSI-u
  `Ctrl+Enter` to the interactive tmux and inserts the same visible newline
  into the webchat draft. PawFlow tracks the already mirrored prefix so final
  submission sends only the remaining suffix and never duplicates prior lines.

## [1.0.0-beta.95] — 2026-08-03

### Fixed

- Codex interactive prompt injection now sends the exact
  `Escape, Escape, paste, 200 ms, Enter, 200 ms, Enter` sequence for both
  regular delivery and live interruption. Codex-specific timing is capped at
  200 ms even when stale environment overrides request longer delays.
- In Grab mode, `Shift+Enter` now flushes the current webchat line and sends
  the CSI-u `Ctrl+Enter` sequence to the tmux, inserting a newline in Codex,
  Claude Code, and Antigravity without submitting. Outside Grab it remains a
  local webchat newline.

## [1.0.0-beta.94] — 2026-08-03

### Added

- Added a bundled local PFP catalog with the optional Comfy Cloud MCP connector,
  plus generic `service_template` package objects that can be installed and
  removed independently. The Resources UI can search installed templates and
  use one to prefill the canonical service creation form without creating a
  service implicitly.
- Services can now be copied from their context menu into the normal creation
  form. The copy preserves the service type, description, and configuration,
  leaves the name empty, and lets the user choose the new service scope before
  submitting.

## [1.0.0-beta.93] — 2026-08-03

### Added

- Added built-in ComfyUI image and video services. Administrators configure
  trusted API-format workflow presets with explicit argument bindings and output
  nodes; agents can invoke only those operations. Direct and relay-aware
  endpoints are supported, and media inputs and outputs are transferred with
  bounded, file-backed streaming.
- Administrators can enable server-local filesystem and command execution per
  managed relay from **Server settings → Server Relays**. The capability is off
  by default, enforced by a dedicated admin-only API, applies without restarting
  the relay, and routes `local=true` into the PawFlow server container.

### Fixed

- Managed relays now receive a reconnect grace period before PawFlow replaces a
  still-running container. Connection state is rechecked after Docker inspection
  and immediately before replacement, preventing a transient WebSocket closure
  from becoming a destructive stop and an exit code 137 interruption.
- Relay HTTP proxying for code-server, port forwards, and provider traffic now
  streams bounded chunks through disk-backed responses instead of buffering and
  base64-encoding complete bodies. Command timeout and transport wait timeout are
  also separated so long-running streams are not cancelled prematurely.
- Codex interactive live submission now uses one canonical
  `Escape, Escape, paste, Enter` sequence. Receipt and tmux verification are
  observation-only and never inject blind extra Enter keys, preventing slow or
  multi-chip prompts from being unfolded or duplicated.

## [1.0.0-beta.92] — 2026-08-03

### Added

- Admin CLI and relay image rebuilds now continue through a guarded restart
  workflow: preflight validates a detached Docker helper, relay builds recreate
  managed relays, PawFlow restarts, and the UI streams every phase before
  reloading once a new server process is healthy.

### Fixed

- Codex interactive messages now preempt immediately even while the active turn
  is represented only by a captured tmux marker; delivery selects the Codex pool
  instead of incorrectly falling back to Claude or PendingQueue.
- Codex live preemption now waits for an exact UserPromptSubmit or MITM receipt
  before marking a rescue handled. Failed preempts retain the active owner and
  queued message, and diagnostics distinguish a different stale prompt from no
  acknowledgement.
- Failed image builds or managed-relay recreation no longer proceed to a PawFlow
  restart, and a shared workflow lock prevents concurrent image update chains.

## [1.0.0-beta.91] — 2026-08-03

### Added

- Added an implementation-ready plan for isolating parallel agents with Git
  worktrees, including lifecycle, ownership, conflict, recovery, cleanup, UI,
  observability, security, rollout, and testing requirements.

### Fixed

- Codex interactive usage now separates cached prompt reads from uncached input
  tokens across Responses exchanges. Message footers and cost accounting no
  longer report repeated cached context as ordinary input, while the context
  gauge continues to use the complete prompt size of the latest exchange.

## [1.0.0-beta.90] — 2026-08-03

### Added

- Added implementation-ready plans for remote CLI compute pools, relay enrollment
  and storage, and resource ACL sharing.
- Added WIP PFP package prototypes for a legal assistant, document templates, and
  encrypted incremental backups, including their flows and template assets.

### Fixed

- `/cc_sessions` now exposes a merged Claude, Codex, and Gemini session view and
  routes reads and writes to the owning provider root while preserving user
  isolation.
- LLM failover is sticky for the complete agent turn: after a provider failure,
  later tool-result calls stay on the selected fallback, advance to the next
  fallback if needed, and retry the main connection only on the next user turn.
- PawCode and webchat conversation/agent selectors now list every LLM-capable
  service, including `llmAggregator` and `llmFailover`, instead of only direct
  `llmConnection` services.

## [1.0.0-beta.89] — 2026-08-03

### Security

- The approval gate now decides on the call that actually runs. Arguments
  delivered as a JSON string reached it as empty arguments, so the dangerous-
  and catastrophic-command scans inspected nothing while the registry decoded
  the same string and executed the real command; both the main and sub-agent
  paths now canonicalize name and arguments before any decision.
- `shell`, `exec`, `run`, `terminal`, `run_command` and `execute` execute as
  `bash` and are now classified as `bash` for approval, instead of falling to
  the session-scoped default and escaping the command-content scan. Escalation
  is one-way, so tools like `create_file` keep their existing classification.
- A `pre_tool_call` hook replacing a call, and `$VAR` resolution rewriting its
  values, both ran after approval and executed without a second prompt. The
  call is now re-authorized when — and only when — it changed.

### Added

- Added the `llmFailover` service: every agent turn starts with its configured
  main `llmConnection`, then advances through unique ordered fallbacks only on
  provider failure. The selected fallback stays active for the rest of that
  turn, including later tool-result calls; only the next user turn retries the
  main connection. Agent handoffs flush durable conversation work and cold-start
  the next provider from the current persisted context; cancellation and force
  stop never trigger failover, unresolved tool outcomes are marked for
  inspection, and exhaustion returns one sanitized error. The service is
  available in LLM selectors and documented in the README and website.
- Tool arguments are aligned with their declared schema types before dispatch:
  a JSON-encoded array or object is decoded, `"true"`/`"50"` become a boolean
  or an integer, and a null optional is dropped. Ambiguous shapes are refused
  with a message naming the property rather than guessed — a bare string is
  never split or wrapped into an array, and a boolean never satisfies
  `integer`. Values already matching their type are untouched.
- `run_tests` accepts `maxfail` (default 1, unchanged fail-fast). `maxfail=0`
  runs the whole selection and reports every failure in one call instead of one
  call per failing test.

### Fixed

- The force-stop writer-ordering invariant test now scopes its source-order
  checks to the `_append` implementation that owns the barrier. A legitimate
  conversation-writer flush in another AgentLoop mixin can no longer produce a
  false CI failure while the cancel-before-persist invariant remains enforced.
- Three private tool-argument decoders that the earlier parser unification
  missed now use the canonical decoder: `BaseFsHandler._unwrap_json` (19
  filesystem handlers), `RealtimeToolBridge._parse_args` (voice sessions) and
  the tool activity digest. All three answered a decode failure with empty
  arguments, discarding a diagnostic the canonical parser produces and, in the
  digest, reporting "no arguments" for calls that had run correctly. A test now
  enforces that no module outside `core/tool_json.py` decodes tool arguments.
- `bash` answered an empty command with a result that did not start with
  `Error:`, so every malformed `bash` call was recorded as a success in the
  tool metrics. It now reports the error and echoes the argument names it did
  receive.
- The Claude Code argument aliases (`file_path` → `path`, ...) renamed keys in
  the caller's own dictionary, so the call kept for re-authorization, for the
  `post_tool_call` hook and for the transcript no longer matched the one the
  user approved.

## [1.0.0-beta.88] — 2026-08-03

### Added

- Added architecture plans for resource ACL sharing, remote relay enrollment,
  and remote relay storage/CLI execution.

### Fixed

- Interactive tmux viewers now survive normal chat-state cleanup while their
  terminal session is still open, with route authorization and active-agent
  invariants covered by regression tests.
- Streaming workers and out-of-band CCI captures now carry distinct active-turn
  ownership tokens. Cleanup can only remove state owned by that turn, so a
  terminal capture cannot overwrite or release a live worker, a finished worker
  cannot leave a ghost in Active Agents, and a replacement worker's client is
  preserved.

## [1.0.0-beta.87] — 2026-08-02

### Fixed

- Codex Interactive cold starts no longer embed the previous
  `.pawflow_cci/initial_context.md` inside the next one. Code mode deliberately
  elides its persisted script arguments, which hid the bootstrap path from the
  visible-path deduplicator and let the full tool result recurse one layer
  deeper on every restart. The shared CLI serializer now pairs native results
  carrying the exact bootstrap header with their call and excludes that pair
  from the next context. The call and result remain visible in the transcript
  and counted by the provider-side gauge; quoted mentions of the header remain
  ordinary context.

## [1.0.0-beta.86] — 2026-08-02

### Fixed

- CI is green again. beta.85 gave `_capture_stream_callbacks` a third callback,
  `ensure_final_text`, and updated every caller but one test module:
  `tests/test_cci_capture_streaming.py` still unpacked two values in twelve
  places, so the suite died on the first of them and `-x` hid the eleven
  others. The docstring that still announced the old two-value contract is
  corrected too — it is what the stale test was written against. No production
  behaviour changed.

## [1.0.0-beta.85] — 2026-08-02

### Fixed

- Grab waits for the terminal to ingest every non-empty composer write before
  sending its single Enter, including one-line prompts. This removes the race
  where Codex displayed the text but required a second Enter to submit it.
- Modified Enter keys insert a newline locally in both normal and Grab modes.
  The complete multiline draft is sent later as one bracketed paste, avoiding
  tmux normalisation of CSI-u Ctrl+Enter into a submit.
- Manual terminal capture cannot lose its final answer at a Stop/request
  boundary: a response visible only through transient tokens is persisted
  before activity is released, and the browser reconciles the durable
  `new_message` with its same-id streaming bubble.

## [1.0.0-beta.84] — 2026-08-02

### Added

- Grab mode covers `antigravity-interactive` too. Antigravity owns a tmux like
  the other two CLI providers, so there was never a reason it could not be
  grabbed — it simply attaches through `open_antigravity_interactive_terminal`
  rather than the CC one, which only searches the CC and Codex pools.
  `_GRAB_OPEN_ACTIONS` now maps provider to attach action, and
  `list_cc_interactive_terminals` — the listing that decides whether the button
  appears — includes the Antigravity pool, normalising its self-reported
  `antigravity-observer` (the container) to the LLM provider name callers
  dispatch on.

## [1.0.0-beta.83] — 2026-08-02

### Fixed

- `codex-interactive` no longer refuses a turn when it fails to recognise the
  Codex composer. beta.82 turned the cold-start readiness marker into a hard
  gate, so the first Codex release whose idle composer drew none of the known
  markers broke the provider outright: every cold send was refused with
  `refusing first send ... because the TUI composer is not ready`, five LLM
  retries deep at ~45 s apiece, against a TUI that was sitting there ready to
  be pasted into. Readiness is advisory again — a missed marker pastes anyway —
  and the Codex wait runs on its own 12-second clock instead of Claude Code's
  45. The undrawn-composer case the gate was added for is still caught by the
  before/after paste proof, which does not model the TUI and re-pastes when
  nothing arrived. Every line of the fix is on `CodexInteractivePool`; the
  Claude Code pool is untouched, and a test pins that.
- `Ctrl+Enter` in the chat composer inserts a newline instead of doing nothing.
  Both CLI TUIs use it for that; the webchat only ever honoured `Shift+Enter`.
  Both work now.

### Added

- **Grab mode.** When the selected agent runs on an interactive CLI provider
  and its tmux is live, a grab button appears in the composer row: held, the
  chat composer becomes a direct input to that TUI, and what you type lands in
  the terminal as if you were attached to it. Releasing it — or switching
  agent, switching conversation, or the session dying — returns the composer to
  the normal path.

  It reuses the terminal transport whole (`open_cc_interactive_terminal`, then
  `terminal_input` into the container PTY) and deliberately never uses
  `pool.send_text()`: that path files an anti-mirror ticket meant for
  PawFlow-injected prompts, and a prompt a human typed must be mirrored. Typed
  through the PTY, the `UserPromptSubmit` hook files it as a `channel="tmux"`
  user message and the MITM captures the answer, so a grabbed prompt is an
  ordinary conversation turn.

  A typed newline is `Ctrl+Enter` forwarded to the TUI as the CSI u sequence
  `ESC[13;5u` — Codex, Claude Code and Antigravity all break the line on it —
  so the break is made by the TUI in its own composer rather than by shoving a
  multiline block across, which is what unfolds one prompt into several
  submissions. A block that arrives already multiline was pasted rather than
  typed, and goes as one bracketed paste followed by `Enter`. `Esc` and
  `Ctrl+C` pass through to the TUI, and typing while a turn is running is
  allowed — the TUI queues it, exactly as it would for a human at the same
  tmux. Grab covers the two providers `open_cc_interactive_terminal` attaches
  (`claude-code-interactive`, `codex-interactive`); `antigravity-interactive`
  is not wired to it yet.

## [1.0.0-beta.82] — 2026-08-02

### Fixed

- Cold interactive CLI bootstraps are now pasted as one physical line that
  points at the complete `initial_context.md` file instead of pasting a
  multiline instruction into a terminal composer. Codex can no longer unfold
  those lines into separate submissions that PawFlow persists and displays as
  user messages.
- PawFlow-injected prompt markers now retain consumable fragment state for the
  local `UserPromptSubmit` hook. A fragment remains recognisable after a stop,
  compaction, or event-service replacement, is consumed only once, and cannot
  later hide matching text genuinely typed by the user. Short and expired
  fragments remain normal user input.
- `codex-interactive` now proves prompt submission from the exact
  `UserPromptSubmit` receipt or the first MITM `/responses` request without
  consuming the coordinator's event stream. It sends another `Enter` only when
  neither proof arrived, reports partial submissions explicitly, and fails
  instead of claiming that an unsent composer was accepted.
- A cold Codex session waits for the actual composer before pasting, while a
  successful pane reaction latches readiness for later turns. This prevents
  bootstraps from landing before the TUI exists and stops repeated pastes from
  stacking attachment chips in the composer.
- Force stop records an authoritative timestamp and clears pending relaunch
  state. Manual compaction resumes a turn only when its runtime marker started
  after the most recent force stop, including when the stop occurs during the
  compaction itself.
- Managed server-relay launch diagnostics no longer expose relay or internal
  authentication tokens in process-command logs.

## [1.0.0-beta.81] — 2026-08-02

### Fixed

- `codex-interactive` no longer waits up to 45 seconds for release-dependent
  visual readiness markers on an already usable session. Codex performs one
  immediate probe, proves that the paste reached the TUI from the pane reaction,
  and latches readiness for subsequent turns.
- A Codex prompt that reached the composer can no longer be mistaken for a
  submitted prompt merely because its text is absent from the pane. Codex
  renders large pastes as attachment chips, so its verifier now retries `Enter`
  up to three times whenever submission remains unconfirmed and no turn is
  running. This removes the failure where a prompt waited in tmux until a human
  submitted it manually.
- Codex transport warnings no longer dump the tmux pane into server logs. The
  pane can contain prompt material and is not required to report the bounded
  transport verdict.
- Returning to a Codex tmux tab no longer leaves xterm and its bridge PTY on
  different grids. The Codex viewer alone stays fixed at the same `220x50` size
  as the pinned tmux window, while ordinary and Claude Code terminals retain
  responsive fitting. Browser resizes still never propagate to the shared tmux
  window or send `SIGWINCH` to an in-flight TUI.
- A due `schedule_continuation` wake-up no longer recreates itself every ten
  seconds while the conversation is already active. The active turn satisfies
  that one-shot handoff; the poller acknowledges it while continuing to defer
  unrelated pending work normally.

## [1.0.0-beta.80] — 2026-08-01

### Fixed

- A late `Stop` can no longer close and disarm the turn that started after it.
  The turn boundary was decided in arrival order, but the two kinds of boundary
  event do not share a route: the proxy emits `request_start` over one
  persistent event socket, while every hook run opens its own connection for a
  single frame. A `Stop` delayed on its way in was therefore published after
  the next turn's `request_start` and marked that turn as already over —
  disarming the undelivered backstop for its whole duration, so the answer sat
  in the queue with nobody reading it and no capture was ever spawned. The
  boundary now compares the events' own timestamps and ignores one older than
  the event that set it.

## [1.0.0-beta.79] — 2026-08-01

### Fixed

- The interactive-CLI safety net no longer fights the turn it is watching.
  Three defects, all reported as the same thing — the activity block closing
  while the tmux visibly worked, active-agents flickering, then switching
  itself back on after the answer had landed:
  - `wait_event` stamped the freshness clock before checking the epoch, so an
    already-evicted coordinator refreshed it on its way out. That stamp reads
    as "has polled since claiming", which retired the 120s claim grace and let
    the net take the stream from a live turn three seconds later. The new owner
    then died on its first read while the capture kept writing rows — the
    webchat filled in under a block marked Completed.
  - The undelivered rule forced adoption past a coordinator that had claimed
    and not yet polled, which `claim_consumer` refuses on exactly that ground.
    The refused capture consumed nothing, so the queue stayed stale and the
    next sweep did it again: one capture every 5s, each streaming 0 chars and
    blinking the active-agent marker, for the whole time a slow TUI took to
    accept its prompt. It now respects the claim grace, which remains a ceiling
    and not an amnesty.
  - Nothing drains a session's queue when a turn ENDS — `drain_session` runs
    when the next turn claims — so every finished turn left its post-Stop tail
    waiting, and 25s later the rule adopted a turn that was already over. The
    capture raised the active-agent marker and waited for a Stop that had come
    and gone. A Stop now marks the session as between turns; anything that
    starts one arms the rule again.
  - A capture that yields the stream no longer announces itself: it claims
    before raising the marker instead of discovering the refusal inside its
    coordinator, after the fact.
- `splitContent` now stamps `fragment.identifier`, so a split -> work -> merge
  round trip cannot mix two documents. `mergeContent` correlates on that
  attribute by default and the splitter never set it, so every fragment of
  every document fell into the same `_default` bin — and beta.78's ordering by
  `fragment.index` then interleaved two concurrent documents by position
  instead of separating them (`a0|a1` and `b0|b1` in flight returned `b0|a1`).
  The parent FlowFile's `process_id` names the split, as in NiFi.
- The simplified view no longer leaves finished turns spinning after a load
  more. Reconciliation closed a turn only when a USER row followed it, and a
  history page routinely ends mid-transcript — its last turn followed by the
  mid-turn head of the page below. That turn kept a ticking clock and a rain
  surface over work long finished, one more per page loaded. Only the newest
  turn on screen may still be working; the server's active-turn set is the only
  thing that may hold an older one open.

## [1.0.0-beta.78] — 2026-08-01

### Fixed

- The orphan-turn net can no longer kill the turn it was meant to rescue.
  beta.77's backstop argued it was safe against a live coordinator because
  adoption goes through a `capture` claim, refused while a request consumer
  is actually polling. That reads liveness from one fact — did it poll
  recently — and a coordinator that has claimed and not polled YET is alive
  too: it is inside its send, which blocks on TUI readiness, paste, settle,
  double Enter and submit verification before it reads anything.
  `_REQUEST_CLAIM_GRACE_SECONDS` (120s) already describes that window, while
  the backstop declared the stream unread after 25. On a slow TUI (`prompt
  not detected ready`, submitted best-effort ~50s in) the net took the stream
  and the real coordinator died on its very first read with
  `CCIConsumerEvicted`. The tmux kept working and the capture kept writing
  rows, so the webchat showed the whole turn while active-agents and the
  context gauge stayed dead for the rest of it. A capture claim now also
  refuses while a request claim is outstanding and unpolled; past the grace
  it takes the stream as before, so no turn stays invisible any longer than
  it did.
- Load more no longer feeds older rows to the running turn. `turnViewReconcile`
  walks the DOM from the top to rebuild `USER > BLOCK > last message`, but
  started that walk holding `_turnOpen` — the LIVE turn, which is the newest
  thing on screen and sits at the bottom. A load-more page usually starts
  mid-turn (its first rows are the tail of a turn whose user message was not
  loaded), and those rows have no user row above them, so they were seeded from
  `_turnOpen` and filed into the live turn's block far below where they sit,
  leaving the fragment's own answer at top level with no block above it — the
  structure broken exactly where the page was joined. Rows met before the live
  turn's user row now open their own turn, as they would if nothing were
  running. Only reproducible while a turn was running, because `_turnOpen` is
  what a running turn leaves behind.
- `mergeContent` now restores split order. `splitContent` stamps
  `fragment.index` on every piece it emits and the merge never read it, so a
  split → work → merge round trip returned the document with its pieces in
  arrival order — shuffled, the more reliably the more the branches differed
  in cost. Fragments carrying no usable index keep arrival order: a plain
  executor fan-out tags clones with `fragment.identifier` only, and those
  have no order to restore.
- The local embedding model is downloaded once instead of on the first
  message after every server restart. With no `HF_HOME` the HuggingFace cache
  landed in the container's own `~/.cache`, which a recreate throws away, so
  each restart re-fetched `all-MiniLM-L6-v2` through some forty HEAD/GET
  round trips to huggingface.co — holding that first message, and logging an
  unauthenticated rate-limit warning on the way — for weights that never
  change. The HF caches now sit under `/app/data` like the tiktoken cache
  already did; `core.embeddings` was already trying `local_files_only` first
  and can finally succeed.

## [1.0.0-beta.77] — 2026-08-01

### Fixed

- Whatever crosses the wire now reaches the webchat, as a rule rather than as
  a set of cases. Everything that decided whether a turn was being watched was
  a guess about the reader — has a coordinator claimed recently, did one poll
  recently, was a prompt injected recently — and every way of being wrong had
  the same outcome: the proxy streams a real turn into a queue, nobody takes
  it out, and the webchat shows nothing while the tmux visibly works. The
  claim released by a failed send in beta.76 closed one such way. This closes
  the rest by observing the queue instead of the reader: events waiting longer
  than 25 seconds mean nobody is reading them, whatever the timestamps claim,
  and the turn is adopted — forced past the liveness graces, since those are
  the guesses being backstopped. Still safe against a live coordinator without
  asking about one, because adoption goes through a `capture` claim, which is
  refused while a request consumer is actually polling. A `cci-pending-sweep`
  thread re-asks every five seconds, so a turn that streams in a burst and
  then goes quiet — the case a check on publish alone can never see — is
  adopted too.

## [1.0.0-beta.76] — 2026-08-01

### Fixed

- A paste is now judged by whether the screen moved, not by whether we
  recognise what is on it. The two probes that decided it — a chip in the
  composer, a tail fragment of the prompt on the pane — both model a TUI, and
  a Codex build that draws its composer inside a box and collapses the paste
  into `[Pasted Content N chars]` satisfies neither: the composer line no
  longer starts with `>` so the chip probe cannot locate it, and the pasted
  text is a chip so no fragment of it is rendered. Every paste attempt was
  therefore declared missing and pasted again, and the send failed with
  `prompt never reached the composer` on a prompt that was in the composer
  four chips deep and needed nothing but `Enter` — a composer a human then has
  to clear by hand. `send_text` captures the pane before pasting and compares:
  a screen that changed is proof the paste arrived, whatever the TUI chose to
  draw. An unchanged screen is still a refusal, and still retried — that is
  the case the retry exists for.

- A turn started by hand after a failed send is no longer invisible in the
  webchat. The provider claims the event stream before sending, which mutes
  the orphan-turn safety net for two minutes on the reasoning that the
  coordinator is inside its send and about to start polling. A send that fails
  builds no coordinator and withdrew nothing, so when the user pressed `Enter`
  in the tmux themselves and the TUI ran the prompt it had been holding, the
  proxy streamed a real turn to a stream marked as owned by a coordinator that
  did not exist: tmux working, webchat silent. A failed send now calls
  `release_consumer()` before raising, scoped by epoch so a claim taken since
  then is left alone.

- The local embedding model is loaded from its cache without asking
  huggingface.co whether the cache is still current. Some forty HEAD/GET round
  trips ran on the first message after every server restart — holding that
  message, and logging an unauthenticated rate-limit warning — to confirm that
  a pinned model had not changed. The load is offline first and falls back to
  the online path unchanged when the cache is missing or incomplete.

### Added

- A tmux send failure now logs the pane it happened on. Every check in the
  interactive pool reads the screen, and each of them reported only its own
  verdict — *TUI prompt not detected ready*, *paste did not reach the
  composer* — which records that our reading of the screen failed and never
  what was drawn. That is the whole difference between fixing a TUI change and
  guessing at one: the pane behind the paste bug above had to be inferred from
  a photograph of a terminal. The head of the pane (2000 chars) is appended to
  the warning, on failure only, degrading to a note when the pane cannot be
  read. On a fresh session this puts the beginning of the injected prompt in
  the server log.

## [1.0.0-beta.75] — 2026-08-01

### Fixed

- A pasted prompt is no longer declared missing because the composer wrapped
  it. The proof that a paste landed is a 24-character tail of the prompt found
  on the pane, and `capture-pane` returns a screen, not a buffer: Claude Code
  hard-wraps the prompt at the pane width, so that tail routinely arrives split
  across two screen lines with a border and padding between its halves —
  present character for character, absent from any verbatim search. The wrap
  falls at the same column every time, so all three paste attempts failed the
  same way and a prompt that was sitting in the composer needing only `Enter`
  ended the turn with "prompt never reached the composer". The fragment is now
  matched with borders and whitespace removed from both sides.
  `_verify_submitted` keeps the verbatim test: there an absent fragment means
  "submitted", and erring toward silence leaves a running turn alone.

- A code-mode script no longer loses what only it knows. beta.74 replaced a
  script's whole output as soon as one relay row had been counted, without
  checking that the output was a copy of anything: a script that made a call
  and then printed a conclusion drawn from it — `derived comparison: ...` —
  had that conclusion persisted as a size, and lost it at the next cold start.
  The elision is now per fragment. What a row beside it already carries
  verbatim is replaced by a pointer to that row; everything else is kept
  exactly as printed, and an output that quotes nothing is returned untouched.

- The rows a code-mode script answers for are counted per row, not per
  observation. A Responses call item is observed twice — streamed as it is
  made, then replayed in the next request's input — and the base coordinator
  renders the second observation onto the first row. The counter was bumped
  after that call regardless, so a re-read became a call the *next* script had
  to answer for: three rows really emitted (script, its call, a second script),
  a counter reading two, and the second script — which drove nothing at all —
  lost the output nobody else was holding.

- A pane with no input box is no longer proof that a paste landed. When the
  composer could not be located, `_paste_landed` answered "unknowable", which
  is `True` — and that accepted the one case the proof exists for. A Codex pane
  showing only its permanent `>_ OpenAI Codex (v...)` header locates no
  composer, so a paste into a TUI that has no input box yet — past the
  readiness timeout, or during a redraw — was declared landed, `Enter` went
  nowhere, and the turn waited out its 300-second no-event timeout. A missing
  composer is now not an answer: the probe keeps looking until its window
  closes, then refuses, and the paste is retried. A pool that declares no
  composer prefix is untouched — its whole pane *is* the composer.

- `store: false` now completes an explicit `include` instead of standing aside
  for it. Testing only for the presence of the key left
  `extra_body: {"include": []}` — or an include naming anything else — as a
  Zero Data Retention request asking for no encrypted reasoning: the API keeps
  nothing, our history gets no ciphertext, and the next turn of the tool loop
  is a 400. The caller's include is kept and `reasoning.encrypted_content` is
  added to it, once.

- The animated block no longer hides a native tool the agent really ran. Once a
  turn had produced an MCP call, *every* remaining native row was dropped from
  the cue surface — which is right for the `exec()` wrapper around a code-mode
  script and wrong for a `local_shell_call` or an `apply_patch` in a mixed
  turn. A wrapper is now recognised by the mark the provider leaves on it
  (`<code-mode script, N chars>`); anything else keeps its cue.

- That recognition now also covers the native row the first MCP call arrives
  *behind*. A native row is held briefly to see whether it was only the
  wrapper; the first MCP call ended that wait by discarding whatever was held,
  without asking what it was. A `local_shell(ls -la)` immediately followed by
  `read(a)` — the ordinary shape of a mixed turn — therefore showed one cue
  instead of two, and the shell command the agent ran went past unseen. The
  held row is dropped only if it carries the wrapper mark; anything else is
  cued behind the call that ended its wait.

- An artifact result releases the cue its call pinned. A pinned cue is freed by
  the second offer of its row, and a `show_file` result never makes one: it is
  claimed for the Artifacts tab and the ordinary `tool_result` ingest is
  skipped. A finished `show_file` went on being shown as the thing the agent
  was doing — bounded, and gone at the end of the turn, but contradicting what
  the pin promises. The release is now raised where the artifact is claimed as
  well.

### Changed

- A tool that is still running stays on the animated surface until it answers.
  Cues are pushed off the back of the column by newer ones, so the thinking and
  the messages an agent produces *while it waits* pushed the running call out
  of sight — at the one moment the reader wants to know what is running. A tool
  cue with no result yet is now pinned: newer cues push it back and dim it, but
  cannot push it out. It is released the moment its result lands, bounded so a
  script firing a dozen calls at once does not turn the column into a wall, and
  never faded past the last readable step.

## [1.0.0-beta.74] — 2026-08-01

### Fixed

- A code-mode script's output no longer reaches the next context alongside the
  calls it is made of. beta.72 elided the script *body* — half the duplication.
  The other half is what it printed: the relay hands each call's result to the
  script, which aggregates them, so the same bytes were persisted twice, once
  as each call's own tool result and once more inside the script's. The row
  keeps its place and now reports the output's size and the calls beside it.
  Only when the script actually produced relay rows: one that reached no
  PawFlow tool — read through Codex's own runtime, or a value computed and
  printed — is described by nobody else, and its output is the only record
  there is. The rows counted are the ones this script produced, so a later
  script in the same turn that drove nothing keeps its output.

  This never affected the codex-interactive gauge, which is measured on the
  wire: Codex's own window holds the script and its output once and never sees
  the relay's rows. The duplication was invisible while a session stayed warm
  and was paid in full at every cold start — which is to say at every
  compaction restart, when the context is rebuilt from what PawFlow holds.

## [1.0.0-beta.73] — 2026-08-01

### Fixed

- A prompt PawFlow pasted into a tmux TUI is no longer reported as submitted
  when the paste never reached the composer. Pressing `Enter` again was the
  only retry there was, and it answers the wrong failure: when the paste itself
  does not land, the input box is empty — exactly what a delivered prompt
  leaves behind — so the verifier concluded "submitted", `send_text` returned
  success, and the turn waited on a session that had been asked for nothing.
  No `UserPromptSubmit` hook, no proxy event, no error in the log, and the
  agent shown active until the 300-second no-event timeout. The send now proves
  the paste landed — the composer chip, or a probe fragment on the pane — and
  pastes again if it did not, bounded, failing loudly rather than silently
  succeeding. Where the TUI offers no usable signal the answer stays "cannot
  tell" and the previous best-effort path is unchanged.
- The Codex readiness wait no longer accepts the pane's permanent
  `>_ OpenAI Codex (v...)` header as a drawn input box. It is rendered the
  instant the TUI starts, so the cold-start wait returned immediately and the
  first paste of a fresh session — the whole bootstrap collage, after a
  compaction restart — went into a composer that did not exist yet.
- A real user prompt is no longer swallowed by the hook-marked injection path,
  which is the main one: the digest path only sees submits the flag did not
  carry. Consuming a marked submit spent the ticket and a digest but left the
  recorded text, so with digests at 0 and tickets at 0 the text still claimed
  any twelve-character phrase the user typed that occurred inside it — neither
  persisted nor answered. The text is now accounted for as the digest path does
  it: whole injection dropped, a piece cut out and the rest left matchable, and
  the last-resort record retired with its text.
- Zero Data Retention is configurable as documented. `store` was absent from
  the `llmConnection` schema, so the only route to it was `extra_body` — and
  the ZDR `include` was derived *before* `extra_body` was merged, meaning
  `extra_body: {"store": false}` produced a ZDR request with no encrypted
  reasoning at all, a hard 400 on the next turn of any tool loop. The include
  is now decided on the body that will actually be sent, an `include` set by
  hand is never overwritten, and `store` is a Responses-only field of the
  schema read as a tri-state: a select answers with the string `"false"`, and
  `bool("false")` is `True`.
- The simplified turn block's cue surface shows the MCP calls instead of the
  `exec(...)` wrapper that carries them. Deferring each wrapper only against
  the calls that follow it left every later script in a turn winning its own
  race, the window was too short for the relay round trip, and every row was
  cued twice — the second time carrying the code body's whole output block.
- The desktop relay image builder no longer claims to prune the Docker build
  cache after a build; beta.72 removed that daemon-wide prune.

## [1.0.0-beta.72] — 2026-08-01

### Fixed

- A real user prompt is no longer swallowed as a fragment of a PawFlow
  injection. beta.71 taught the guard to recognise a piece of a split paste by
  matching it against the injected text, but kept that text matchable for the
  full 600-second digest window — so once the injection had already been
  consumed in full, any twelve-character phrase the user typed that happened to
  occur inside it was claimed as ours, and was neither persisted nor answered.
  An injection is now *consumed* as its pieces arrive: each claimed piece is cut
  out of what remains, an injection matched whole by its digest drops its text
  immediately, and an entry with nothing identifiable left is discarded. A far
  shorter burst window, refreshed by each piece, bounds the same thing in time.
- The Codex submit verifier no longer presses Enter into a running turn. The
  composer region was located by scanning the pane bottom-up for a line starting
  with `>`, which also matches the permanent `>_ OpenAI Codex` header. With the
  composer off screen the scan stopped on the header and returned the whole
  transcript as the composer, so a chip left by an already-submitted message
  read as an unsent paste and three Enters were sent mid-turn. A composer line
  is the prefix followed by a space or nothing; the header glues punctuation to
  it.
- The context gauge is no longer inflated by counting on top of a measurement.
  For an observed CLI provider `used` is the prompt size the provider itself
  reported, and it already contains the message being appended. The streaming
  hot path added PawFlow's own count for each append anyway, compounding every
  time: the gauge climbed for a whole turn — observed going from 62% to 92% with
  no compaction of any kind — until the next full recompute put the measurement
  back and it resumed growing correctly. It also armed `compact_threshold_pct`
  on the inflated number, firing auto-compaction early. Both incremental paths
  now refuse a measured cache.
- The gauge denominator no longer changes at the turn boundary. Claude Code's
  own `modelUsage[model].contextWindow` was consulted only while a turn was
  active; between turns the code read `client._real_context_size` /
  `client._context_window`, attributes PawFlow assigns nowhere, so it resolved
  to 0 and the denominator silently fell back to the configured budget. One
  lookup, `_client_real_window`, now serves both — and also the turn budget in
  `_agentctx_p3` and the cap in `context_ops`, neither of which had ever applied
  the provider's real window.
- A tool row whose result has not reached the transcript yet hydrates correctly.
  A request leaves `_inflight` the moment it completes, but its result is
  written slightly later; a `load_history` landing between the two read the page
  before the result existed and the snapshot after the entry was gone, rendering
  a running call as an ordinary finished one — no pending bullet, no BG/Kill, no
  result. Finished requests stay visible to row hydration alone for a short
  grace; every control path (kill, cancel, unbound checks) still sees them gone
  at once.
- The three global `docker image prune --filter dangling=true` calls are gone
  from `install-pawflow.sh`, `install-pawflow.ps1` and the relay desktop app, as
  is a `docker builder prune -f` that wiped the entire daemon build cache after
  building a relay image. All of them are daemon-wide: on a machine where
  PawFlow shares Docker with anything else they deleted other projects untagged
  layers and build cache. Each path already removes, by id, the images it
  untagged itself. The UI update path had received this fix already; the
  installers carried assertions *requiring* the prune, which is why it survived.

### Changed

- The detail block cue surface names the work instead of the wrapper. A
  code-mode turn is one native call — `exec(<code-mode script, N chars>)` — and
  everything it does is the MCP calls the relay reports underneath it. Cueing
  every tool row identically put the wrapper in front and the work behind. A
  native row now yields: it is held briefly and an MCP call arriving in that
  window takes its place. A native call genuinely on its own still reaches the
  surface, so a turn using no MCP tool is not left blank.

### Added

- Codex real context window is derived from its TUI status bar. The Responses
  API reports how full the window is (`input_tokens`) but never how big it is,
  so the gauge and the auto-compact threshold divided by whatever
  `max_context_size` happened to be configured. The TUI prints the missing half
  (`context left 74%`), and the two together determine the window exactly.
  Sampled once per turn; a reading below 15% occupancy is refused as rounding
  noise, and a derivation within 5% of the stored value does not replace it.
- OpenAI Responses reasoning items are carried end to end. On that API the chain
  of thought is an output item the next request must hand back with the turn
  that produced it — PawFlow kept only the reasoning text, so every iteration of
  a tool loop re-entered having forgotten why it called the tool. The item is
  now captured verbatim, stored on the assistant message, and replayed ahead of
  the message and the calls it led to. Setting `store: false` in the service
  config adds `include: ["reasoning.encrypted_content"]`, which Zero Data
  Retention requires: there, a turn whose reasoning item the API cannot resolve
  is a hard 400, not a quality loss.

## [1.0.0-beta.71] — 2026-08-01

### Fixed

- Fragments of a PawFlow-injected prompt are no longer filed as messages the
  user typed. One paste does not always produce one submit: a TUI that collapses
  pasted text into an attachment chip can submit a composer holding several of
  them as several `UserPromptSubmit` hooks, and a piece's SHA-256 matches no
  recorded injection. The pieces after the first fell through to the manual-prompt
  path, were published under the user's name, and woke the agent one by one --
  the visible result being an agent commenting, line by line, on a background
  tool result it had just been handed itself. The injected text is now kept for
  the same 600-second window as its digest and a prompt that is a slice of it is
  recognised as PawFlow's own. A slice under 12 characters stays manual: `ok`
  occurs inside any large paste and is also exactly what a human types. The
  ignore ticket is still spent on the first piece, or it would survive the paste
  and swallow the next thing the user really typed.
- A pasted prompt that never left the Codex input box no longer reports success.
  `_verify_submitted` looked for a tail fragment of the injected text in the pane
  and read its absence as "the input box let it go". The Codex TUI never renders
  pasted text -- it shows `[Pasted Content N chars]` -- so that condition held
  from the first poll onwards, every send reported success, and six pastes
  stacked up in one composer until a human pressed Enter. The chip is the signal
  instead, scoped to the composer so one left in the transcript by an already
  submitted message is not mistaken for an unsent one. Paste settle is now
  per-pool and 1s for Codex: a multi-kilobyte paste takes its TUI longer to
  ingest than the 0.2s that suits Claude Code.
- The bootstrap paste no longer carries the whole current turn. It was written
  to `initial_context.md` under `## Latest User Request` *and* quoted in full in
  the pasted text, so a turn carrying a log dump was tens of kilobytes typed into
  a terminal input box. 2000 characters are quoted inline -- enough to identify
  the question without a file read -- and the rest points at the file.
- A code-mode script body is no longer quoted, on screen or in the record. The
  GPT-5.x "sol" harness runs one freeform `exec` item and drives every tool from
  inside its JavaScript, so each group of MCP calls was fronted by a row reading
  `exec(const r=await tools.mcp__pawflow__use_tool...)` -- the aggregator the eye
  lands on, in front of the rows that name what actually ran. Worse in the
  record: a call's arguments are persisted and replayed into the next context, so
  kilobytes of generated JavaScript came back at every bootstrap describing work
  already described by the rows around it. The row stays -- it is the only
  evidence the turn ran a script, and what the script does with Codex's own
  runtime is reported by nobody else -- but it states the body's size instead of
  quoting it.
- `/compact` shows its result again in the simplified view. A system notice was
  barred from the spot under the block so it could never displace the agent's
  answer, but a compact answers with a notice and nothing else: barred, it was
  filed inside the detail block and the turn reported its own outcome nowhere the
  reader could see it. A notice now holds that spot while nothing else does,
  yields it to the first real message, and is replaced by a newer notice rather
  than queueing behind it. Same path, same fix, for every slash command whose
  answer is a notice.

## [1.0.0-beta.70] — 2026-08-01

### Fixed

- The Codex interactive context gauge reads the real window instead of 0. It
  was rebuilt from the messages PawFlow holds, and only once it had observed
  the provider read `initial_context.md` -- the file the PawFlow context is
  externalized into. The GPT-5.x "sol" harness reads that file from inside its
  own script, so the call never reaches PawFlow: the gate never opened, the
  computation kept counting an empty message list, and the gauge showed 0 for
  the entire life of the session, including after switching conversation,
  where the persisted snapshot said the session was still cold. A gauge stuck
  at 0 also never trips auto-compaction. PawFlow sits on that wire and does
  not have to rebuild anything: every `/responses` exchange reports the
  prompt-token count Codex computed itself, so that number is now the gauge.
  It is the last exchange's, not the turn's sum -- a turn runs several, and
  summing their prompts would report several times the window -- which also
  means a compaction inside Codex moves the gauge back down. Cost accounting
  keeps summing, unchanged. Codex interactive only; the other interactive
  providers keep the reconstructed gauge.
- No tool call a Codex code-mode turn runs is hidden any more. The `exec` body
  that drives them was dropped from the view, on the grounds that its rows
  come from the tool relay that executes them. That holds only for the calls
  the script routes through PawFlow: what it runs with Codex's own runtime is
  executed by no relay and reported by nobody, and reading the bootstrap
  context is the first thing such a script does -- so the calls that load a
  turn's whole context appeared in no view at all. The body keeps its row; the
  relay's rows still name the tools it ran.
- A tool call that is still running still looks like it is running after a
  reload or a conversation switch. That state lived only in the live SSE
  stream, so a view rebuilt from the transcript drew a call whose result had
  not been written yet as an ordinary finished row -- no pending bullet, and
  no BG or Kill button on the one call that could still use them. The relay
  knew all along: its in-flight table carries the conversation, the agent, the
  tool and its call id for every executing request, but the table was
  write-only. `load_history` now reads it (`inflight_snapshot`), ships an
  `active_tool_calls` snapshot, and marks the rows still in flight as live --
  a row that already carries its result is never marked, and a call running in
  a task or delegate sub-conversation hydrates in the parent view where its
  row is drawn. All providers: the defect was in hydration, not in a stream.
- `read_history` fails closed on an unreadable ownership lookup. The windowed
  readers -- `search`, `oldest`, `range`, `around` -- walk the transcript with
  the conversation id alone, so `_owns_conversation` is the only check between
  another user's messages and whoever knows that id; an exception reading the
  conversation metadata used to grant access. A corrupted or unreadable
  `extras.json` was therefore enough to disclose a conversation to any
  authenticated user who knew its id. The denial is logged. `recent` was never
  affected: it pages through `load_page`, which is given the user id.
- A code-mode tool row reaches the session that is actually running, and
  reports a refusal as one. Sessions are never unregistered, so a conversation
  accumulates the state of every container it ever had -- all still flagged as
  being in code mode -- and the event went to the first in insertion order:
  the oldest, usually dead. Publishing reported success while the event sat in
  a queue nobody reads, and the live UI drew no tool row at all. Candidates are
  now ordered connected-first, then newest; `connected` orders rather than
  filters, so a provider whose proxy never marks it cannot lose its rows over
  it. Separately, `is_error` was hardcoded false: a read-only denial, a
  rejected approval, a blocked hook and a failed tool all drew a green row
  under a tool that never ran. It now follows the same rule as the MCP bridge.
- Updating PawFlow no longer prunes other projects' Docker images. The cleanup
  step ended on a daemon-wide `docker image prune --filter dangling=true`,
  which deletes the untagged layers of everything else built on the same
  daemon -- undoing, in one line, the repository filter every other line of
  that script exists to enforce. The untagged layers of PawFlow's own
  repositories are still reclaimed, by image id, inside that filter.

## [1.0.0-beta.69] — 2026-07-31

### Fixed

- Codex interactive renders the tools a code-mode body runs, one row each,
  like every other provider. The GPT-5.x "sol" harness calls nothing directly:
  it runs one freeform `exec` item and drives every tool from inside its
  JavaScript, so the whole turn showed as `exec(<javascript>)` rows under a
  native badge — which tool ran, on what, and whether it was PawFlow or the
  container's own shell were all unreadable. Reading the names back out of the
  script was tried and does not hold: property shorthand (`{tool_name}`), a
  table of calls driven by a loop, `Promise.all(names.map(...))` and
  `.filter()` over the tool list are how code-mode is written, not corner
  cases, and each one fell back to the raw body. PawFlow executes those calls
  itself, so they now come from the tool relay, which knows each one's name,
  arguments and result exactly. They are published into the session's event
  queue as ordinary observed calls, so they become rows through the one path
  all providers share — MCP badge, background and kill buttons included. The
  relay only reports a call no provider row was waiting for, and only while
  the turn is in code mode, so a tool the model called directly still renders
  once. The JS literal parser and the `<call_id>#<index>` row splitting it
  needed are gone.
- A finished tool call no longer ends the turn marked `[Stopped]` in the
  simplified live view. Its ephemeral cue carries a copy of the rendered row,
  and the copy kept the row's `data-tc-id`. The cue surface sits above the tabs,
  so a lookup by call id found the copy first: the result attached to a node
  seconds from fading, the canonical row stayed pending, and the end of the turn
  stamped it `[Stopped]` right beside a cue showing the output it never got —
  which also read as the row being rendered twice. A cue copy is decoration: it
  now carries no `id`, `data-msgid` or `data-tc-id`, on the copy itself and
  everything nested in it, and the turn-end finalizer skips pending bullets
  inside one.
- No tool call is hidden from the chat any more. `get_tool_schema` was
  filtered out of both display paths: the live SSE path skipped the call and
  blanked its result's name — an unnamed result row attached to nothing — and
  the reload path dropped the pair again, so a reloaded turn had fewer rows
  than the transcript held. Both paths keep unwrapping the MCP wrapper, so the
  name shown is still the real tool and never `use_tool`.
- `PROJECT_SUMMARY.md` states the version that actually ships. It had been
  hand-written at `1.0.0b58` while the package and the tags were ten betas
  ahead, and its repository figures still described the tree as it stood in
  April. Both are refreshed, and a test now checks the stated version against
  `pyproject.toml` so the summary cannot silently fall behind again.

## [1.0.0-beta.68] — 2026-07-31

### Fixed

- A Codex interactive turn reaches the chat again. Codex 0.146 stopped POSTing
  a Responses body: it opens `GET /backend-api/codex/responses` with
  `Upgrade: websocket` (`openai-beta: responses_websockets=2026-02-06`) and
  exchanges the same Responses events as `permessage-deflate` WebSocket
  messages. The MITM forwarded the bytes untouched, so the CLI never noticed —
  the tmux worked perfectly — but a 101 carries neither `Content-Length` nor
  chunking, so on the HTTP path the frames that followed were read as the next
  response header and every event of the turn was lost. The coordinator had
  seen the `request_start`, so its no-observed-event guard never fired: the
  turn returned empty, with no text, no tool calls and no error, until the user
  stopped it. The proxy now decodes the frames (RFC 6455 framing, RFC 7692
  `permessage-deflate` with the context takeover ChatGPT negotiates — one
  decompressor per direction per connection). Both directions carry plain JSON:
  server events are republished under the same `sse` envelope SSE used, and the
  client's `response.create` yields its `function_call` and
  `function_call_output` items to the existing extraction. An upgrade
  negotiating an extension the decoder cannot undo now fails the turn where it
  breaks instead of timing out five minutes later with no reason.
- The Codex TUI keeps the same builtin tools as `codex app-server`. Both
  providers run the same binary, in a container whose cwd is the same session
  workdir, and both bootstrap from the same `.pawflow_cci/initial_context.md`;
  only the transport differs, so the tool set must not. The blocklist
  `codex exec` carries was being applied to the TUI as well, which removed the
  only way codex had to read the cold-start context PawFlow hands it — the file
  is local to the session workdir, and the MCP `read` resolves against the
  relay, whose server-fs is rooted at `CLAUDE_SESSIONS_DIR` and cannot see a
  codex session. The same blocklist removed `view_image`, so the attachments
  `_cci_materialize_images` writes into `.pawflow_vision/` could never be
  opened. Steering toward PawFlow tools for user work stays a prompt concern,
  as it already is for app-server.

## [1.0.0-beta.67] — 2026-07-31

### Fixed

- A claim owns the interactive stream from the moment it is granted, not from
  the first poll. A request coordinator claims the stream, then blocks through
  TUI readiness, paste, settle and submit verification before `run()` starts
  polling — up to ~55s on a cold TUI. The orphan-turn net judged the presence
  of a reader on `last_wait_at` and `injected_intent_at`, both set only after
  that send, so in between the stream looked unowned: the capture adopted the
  live turn and its own claim evicted the coordinator that was about to read
  it. The service now timestamps the `request` claim itself and the net honours
  it, capped at 120s. A coordinator that dies inside that window has already
  failed its turn with an error, so there is no invisible response left to
  recover and suppressing the net there costs nothing.
- The codex TUI no longer opens its trust modal on a cold start. `config.toml`
  declares the session slot as a trusted project — `projects."<path>"`,
  `trust_level = "trusted"`, the key read out of the codex 0.146 binary rather
  than assumed. Without it the dialog waits while drawing no readiness marker,
  so the launch burns the full prompt-ready timeout, the injected prompt is
  pasted into the modal, and only a human at the tmux can unblock it. The
  declared path is exactly the directory the tmux `cd`s into, and the table is
  written inside the PawFlow-managed section so a regeneration replaces it
  instead of stacking a second table on the same key — duplicate TOML, which
  makes codex reject the whole file. `codex exec` never asks the question, so
  the headless provider emits nothing.

## [1.0.0-beta.66] — 2026-07-31

### Fixed

- A guess about the end of a turn no longer ends a turn that is running. The
  block said `Completed`, elapsed frozen at 1s, animations gone, over an agent
  four tools in and still working. No `done` had been published — the
  conversation writer logs every publication and there was none for the whole
  turn. A history page carries `turn_final` on the last assistant row of every
  turn its classifier believes is over, flagged `turn_final_derived` because it
  is a reconstruction rather than a statement by the server, and the only thing
  keeping that guess away from a live turn was `active_turn_ids` server side —
  which the client obeyed without asking whether the turn was still speaking.
  The view now holds the invariant on evidence it owns: a turn the runtime
  snapshot names, or one the live channel is feeding, refuses a derived final
  outright, and a derived final that did close a turn is remembered as a guess
  that the next live row of that turn undoes. A `done` never carries the flag,
  so nothing real is blocked and nothing reopens a turn the server declared
  finished.
- Gap recovery adopts the `active_turns` snapshot of the page it renders, like a
  full load does. It is the path that runs precisely while a turn is in flight,
  and it was judging liveness on a picture taken at the last full load.

## [1.0.0-beta.65] — 2026-07-31

### Added

- The answer now appears while it is written, whatever provider is behind it.
  Only one path ever streamed before — the orphan-turn capture that adopts a
  tmux turn nobody is listening to — so an answer arrived progressively when a
  background tool woke the agent and landed sealed in one piece the rest of the
  time. `StreamEmitter.get_token_callback` was receiving the text from every
  provider and dropping it. Granularity stays the provider's and is not a
  setting: the API providers deliver real deltas, the CLI ones whole blocks
  (Claude Code 1.0+ sends complete `assistant` events, with no
  `content_block_delta` to forward), so those are progressive block by block
  rather than word by word.
- `agent.llm_call` and `agent.tool` spans. Tracing was wired end to end and
  instrumented a single boundary, so a trace said a turn took four minutes and
  nothing about where they went. These two are the ones that can be slow, and
  the only ones that tell each other apart afterwards. Tools run in a thread
  pool and OpenTelemetry keeps the active span in a `ContextVar`, which a pool
  worker does not inherit — the trace context is captured in the submitting
  thread and re-attached in the worker, or every tool span would be a root span
  and the trace would show the tools as unrelated top-level rows.

### Fixed

- A captured tmux turn shows its meta line again. The capture persists its text
  as it is written, which is before the coordinator returns, so the model and
  the token counts do not exist yet and the message went out with a source
  carrying neither — and the client renders no meta line from that.
  `message_meta` is an update channel, so the capture now remembers the ids it
  wrote and sends the real numbers once it has them. Nothing measured means
  nothing published: a meta line asserting zeroes nobody counted reads as a turn
  that cost nothing.
- Assistant blocks no longer merge into one bubble while streaming. The durable
  message id rotates at every persisted block, so one turn carries several; the
  client starts a new bubble when it changes, instead of piling the blocks
  together live and splitting them apart again on reload.

## [1.0.0-beta.64] — 2026-07-31

### Fixed

- A cold CLI start no longer nests the previous one inside itself. The agent
  reads its serialized context from `initial_context.md`; that read and its
  result are persisted on purpose, so the transcript shows what the agent did
  rather than hiding a call that cannot be told apart from a lost one. They were
  also being serialized back into the *next* context file — and since the result
  body is the bootstrap file, each cold start embedded a verbatim copy of the
  one before it. A 671 KB context file was 16% copies of itself, two echoes of
  52 KB and 53.5 KB quoting content already present in clear a few hundred lines
  above. Transcript and agent context are two surfaces with two rules: the pair
  stays in the first and is dropped from the second, in every serialization path
  of all CLI providers. The gauge still charges for that body — it is
  literally what fills the provider's window — and the serializer reuses the
  gauge's own predicate so the two cannot drift apart.
- `# nosec B404` markers point at the line Bandit flags again. The beta.63 ruff
  pass split `import subprocess, os, shutil  # nosec B404` into one import per
  line and the comment stayed on the last of them, leaving the `subprocess`
  import bare and the security scan red on two findings that had already been
  reviewed and accepted.
- A pooled SQLite connection waits for the write lock as long as it waits for a
  connection. SQLite serializes writers and the loser gives up after its busy
  timeout; sqlite3's 5s default was never chosen, while the pool already blocks
  up to 30s to hand out a connection. Concurrent flow tasks on one database
  could raise `database is locked` instead of simply taking their turn. The
  journal mode was measured and makes no difference here — the timeout is the
  whole effect — so no WAL pragma was added and user databases keep their
  on-disk format.

## [1.0.0-beta.63] — 2026-07-31

### Fixed

- A chain of `read_history` calls no longer brings the server down. Not a
  deadlock — no lock is involved — but CPU and memory: eight of the nine
  actions called `ConversationStore.load()`, which parses every row of the
  transcript, sorts all of them and composes every display trace, to render at
  most a hundred messages. `range` did it twice per call, once to slice and
  once more inside the renderer to rebuild a msg_id-to-position index. That is
  the exact shape the archived-phase summaries hand an agent — one `range()`
  hint per phase — and on a 257k-message conversation a handful of those calls
  is hundreds of megabytes and seconds of CPU each. The store gains bounded
  readers (`iter_display_windows`, `load_window_by_index`,
  `find_display_index`), `load_range_by_msg_id` collects between its two
  anchors and stops at the closing one, and every action now retains only what
  it returns: totals are counters, `recent` keeps a sliding tail, `around`
  locates its anchor in a streaming pass then reads the window by index, and
  `search` keeps its best hits bounded. An unterminated range is an error
  naming the bad anchor instead of a silent empty answer. The regression test
  measures the largest batch the store actually composes per call and fails if
  it tracks the size of the conversation instead of the size of the page.
- The cold/live rule is guarded in both directions. Case 1 — no process, so we
  launch, so it is a cold start with the full context — was already enforced.
  Its mirror was not: a turn built as a cold start whose provider then found
  the process alive simply sent a delta carved out of a context assembled for
  a launch that never happened. Nothing crashed, so nothing was noticed, but
  every message paid for the whole transcript being loaded and compacted for
  nothing, the gauge was zeroed against a session that never restarted, and
  the persisted session pointers were cleared and rewritten each turn.
  `_cli_require_delta_context` now raises `DeltaContextRequired` at every
  provider's reuse site and the turn is rebuilt as the delta it is, at most
  once per turn, on all five CLI providers.
- Updating from the UI reclaims disk, as updating from the command line always
  did. The installer has always pruned the image tags an install stopped
  using; the UI updater never did, so an instance that only ever updated from
  the UI kept every version it had run — beta.49, .50, .53, .57, .59, .61 — at
  a couple of gigabytes each. Both updater scripts now end with the same
  cleanup, after the restart, tolerating their own failures. What to keep is
  read from the daemon rather than assumed: any image a container references
  is spared, along with the server image being installed and the configured
  relay images, which sit referenced by nothing between agent turns.
- The admin gate matches a role exactly instead of as a substring. `"admin" in
  roles` also accepted `admin-readonly` and `non-admin` — and a group named
  `admin` in the identity provider was a privilege escalation waiting to be
  created.

### Added

- `codex-interactive`: the real Codex TUI in tmux, kept alive across turns and
  read from a local MITM of its `/responses` stream, on the credential pool
  `codex-app-server` already owns. One user-visible turn can span several
  Responses exchanges around MCP calls. It obeys the cold/delta rule like every
  other CLI provider.
- Session correlation in logs, always on and free. A `ContextVar`-bound session
  key is injected by a logging filter, so every line emitted at any depth
  carries `session=` and "what happened to this container" becomes a grep. The
  bugs of the last ten betas were all shaped like that question.
- Optional OpenTelemetry tracing, off unless an operator sets an endpoint.
  Absent package or absent endpoint, every path is a no-op costing one
  attribute lookup; a misconfiguration warns instead of stopping the boot.
  `OTEL_EXPORTER_OTLP_ENDPOINT` is read first. When a span is active its trace
  id becomes the ambient log correlation, so a trace names its log lines and a
  log line names its trace.
- IdP claims, groups and permissions. Groups are extracted from the configured
  claim by dotted path (`realm_access.roles` works) and mapped to PawFlow roles
  by an explicit table. A mapped group can provision an account only if the
  provider also carries a rule saying so. Local role wins over remote by
  default, configurable through `auth.role_precedence`. The session carries its
  groups and they reach flows as `http.auth.groups`. The Keycloak pitfall —
  roles are not in `userinfo` without a mapper — is documented.

### Changed

- Ruff passes clean across the tree: 936 findings resolved, no functional
  change intended, committed separately so a reviewer can skip it and a bisect
  never lands on it.

## [1.0.0-beta.62] — 2026-07-31

### Fixed

- The simplified chat view could tear an answer out of an earlier turn and
  drop it at the bottom of the page. `final_msg_id` is resolved with a lookup
  that reaches any row on screen, and promoting a row *moves* it — so a `done`
  for a turn that produced nothing of its own, naming a message from half an
  hour earlier, relocated that message under a fresh, empty block reading
  "Completed in 0s". The promotion is now refused unless the row is already
  filed inside this turn's block or sits after it at top level. An id names a
  row; it never selects one.
- A Claude Code credential slot is taken by a launch, never by a lookup.
  `_setup_credentials` writes `.credentials.json`, `self._current_pool_index`
  and the stored slot, and it ran BEFORE the live-session lookup. When the
  exact key missed and `get_compatible()` adopted a session on another slot,
  only the key, the local index and the extra were realigned: the client and
  the workdir stayed on the slot this turn had just written. `_recover_tokens`
  then wrote one account's rotated tokens into another account's slot, an auth
  error refreshed or excluded the wrong credential, and the registry sweeper
  attributed the workdir to a third — a multi-account pool could disconnect
  accounts the turn never touched. The lookup now runs first, a reuse adopts
  the live session's slot without writing anything, and only the launch branch
  takes a slot. This is what codex and gemini already did.
- Killing an Antigravity session no longer evicts its replacement.
  `ensure_started` pops the stale entry, releases the lock, then runs
  `before_launch` and the relaunch — seconds during which the cold retry can
  register a new session under the same key. The trailing `pop` was
  unconditional, so the kill of the old session dropped the new one from the
  registry while its container kept running: untracked, never reaped, the very
  orphan the cleanup exists to prevent. The pop now requires the current entry
  to still be that session.
- An OAuth token rotated inside a CLI container is rescued on a clock, not on
  a teardown. Anthropic's `refresh_token` is single-use, and the copy back to
  the pool slot only ran when a session was torn down — which made it depend
  on the server being alive to perform it. A hard kill, or an update whose
  Docker stop grace (10s by default) expires while `docker rm -f` works
  through the containers at up to 15s each, and the token died with the
  container while the pool kept one that could no longer be refreshed. Every
  live pool now also copies back on each sweeper tick, for the sessions it is
  *not* evicting, and `shutdown_all` takes every token before it kills
  anything. `recover_tokens_from_workdir` is idempotent per (workdir, slot)
  through a shared memo, so a tick where nothing rotated writes nothing.
- A container still up at boot is reaped. The shutdown reaper is driven by the
  `org.pawflow.server-id` label precisely so that no hand-maintained name list
  can fall behind, but the boot cleanup still matched names
  (`pf-<server-id>-*`): the batch pools are named `pf-cc-pool-*` and the
  relays and logins `pawflow-*`, so none of them carried this server's prefix
  and none were ever cleaned up at startup. Both ends now call the same
  `docker_utils.reap_spawned_containers`, and boot runs it before task
  registration, flow restore and boot-recovery — before anything can spawn a
  container or claim a credential slot. Nothing is adopted back: the pools
  track sessions in memory only, so a survivor would hold a slot and write
  into a session workdir the new process believes it owns. The legacy name
  pass still refuses any container carrying another server's id.

### Added

- Three implementation plans in `docs/`: `EVAL_HARNESS_PLAN.md` (scored agent
  evaluation — case format, scorers, five suites, scorecard, phasing),
  `MODEL_HARNESS_PROFILES_PLAN.md` (per-model prompt/tool/limit tuning behind
  one resolution point) and `THREAT_MODELS_PLAN.md` (per-surface attacker
  models with a mandatory, never-empty residual-risk section).

## [1.0.0-beta.61] — 2026-07-31

### Fixed

- claude-code (`-p`) could still launch a fresh process holding nothing but
  the last user message. beta.60 moved the context phase onto the live
  registry, but the provider still branched on the persisted
  `claude_session:*` id — which outlives the process it describes — and built
  the delta two blocks BEFORE looking the live session up. After a server
  restart the context phase correctly loaded the whole transcript, the
  provider cut it down to the system prompt plus the last question, found no
  live process, launched one, and handed it that delta. The serialization is
  now decided after the lookup and on `st._is_reuse` alone; the stored id only
  picks the credential slot to resume on.
- The two phases now ask with the same inputs. The context phase cannot know
  which credential slot `_setup_credentials` will pick, so it asks without
  one; the provider asked for one exact slot and answered "cold" where the
  context phase had answered "warm", orphaning the live process and paying a
  cold retry. `LiveSessionRegistry.get_compatible()` (mirroring codex and
  gemini) hands back the key as well, and the provider adopts a session with
  the key it actually lives under.
- An Antigravity launch refusal no longer leaves a container nobody tracks.
  `ensure_started` dropped the unusable session from the registry, then asked
  `before_launch`, then killed — so a `ColdStartRequired` never reached the
  kill and the cold retry started a second container beside the first. The
  kill is in a `finally`. This is an ordinary state, not a corner case:
  `find_session` calls a session warm on the container alone while
  `ensure_started` also wants the proxy journal ready.
- The per-iteration heartbeat is stopped on every exit. `_alc_iteration` left
  by five returns and by any exception, and only two of them stopped the
  thread, so a compact restart, an overflow retry, a plain tool turn or a
  cancellation each leaked one heartbeat publishing to the same conversation.
  The function that starts it now owns its lifetime (`try` body / `finally`
  stop), and the stop clears the handle so it can never run twice.

## [1.0.0-beta.60] — 2026-07-30

### Fixed

- Every CLI now answers the same question the same way: no process running ->
  we launch -> cold start -> full context; a process is running -> delta.
  beta.59 gave codex and gemini the refusal that enforces it and left the
  others out, so claude-code interactive and antigravity could still hand a
  bare delta to a process that knew nothing. The rule is asked at each
  provider's own launch site, because that is where "we are about to start a
  process" is known, and the pools take a `before_launch` callback rather
  than deciding themselves: they manage containers, not context policy, and
  they call it only when they are really going to launch, never on a reuse.

- claude-code (`-p`) had a third path, and it was the last provider that did:
  no live process but a persisted session id, so it launched with `--resume`
  and a delta and let CC replay its own jsonl. Whether that file still meant
  anything was decided by re-deriving CC's project-key algorithm and trusting
  a transcript only CC can validate; when CC declined it and opened a fresh
  session instead, the `SESSION MISMATCH` check merely logged it while the
  agent lost its history, and the new empty session id was then persisted as
  though it were sound, so every later turn resumed a session holding
  nothing. There is no resume-from-disk path any more, and the context phase
  asks its live registry instead of the stored id -- the same alignment codex
  and gemini got in beta.58.

- Refusing a launch no longer strands the live session's turn lock. Both
  stream providers hold it when they ask, and their own try/finally starts
  later, so the lock was acquired and never released. It is an RLock and the
  retry runs on the same thread, which hid it: the second acquisition
  succeeded, one finally released one level, and the next turn on that
  session -- on another thread -- waited forever. The refusal now takes a
  `release` callback, and codex asks before minting its MCP token rather than
  after.

- The "this context is a delta" marker no longer lives on the shared service
  client. That client comes from the service registry and the loop only
  clones it later, so a second conversation preparing a cold context on the
  same service cleared the marker before the first one's clone was made --
  and the refusal became a no-op exactly when two conversations shared a
  service. It travels in the turn's context and is stamped on the turn's own
  clone.

- A rebuilt context is now adopted whole. The restart replaced the client,
  the messages and the ctx, but left the loop's tool registry, tool
  definitions, model and cancel registration pointing at the context it had
  just abandoned -- so a turn could execute tools through a registry the new
  context never configured, and force-stop reached the wrong clone. The
  message list is replaced in place, since ctx, the emitter and every closure
  built at setup hold that exact object.

- The cold restart no longer spends an iteration. It was counted before the
  provider was called and never given back, so a turn with
  `max_iterations=1` ended having never called the model -- and CLI providers
  deliberately synthesize no empty answer.

- The context gauge is reset on the pass that launches. The reset sat inside
  the block gated on `not force_cold`, which is exactly the pass that knows
  the old session is gone, so the dead session's percentage survived the
  restart.

- An ephemeral call (compact, memory extraction) is exempt from the refusal
  everywhere. It builds its own full text, but it clones a client that may
  carry the marker, and bouncing it would restart a compaction as if it were
  the agent's own turn.

## [1.0.0-beta.59] — 2026-07-30

### Fixed

- Looking a live CLI session up now restarts its idle clock. The idle TTL
  exists to reap containers nobody asks for, and `last_used` is the only
  evidence the sweeper has — but the lookups that hand a container to a caller
  never refreshed it. A session sitting at the end of its TTL could therefore
  be found alive by the context phase and swept moments later, before the
  provider claimed it. The CCI and Antigravity pools already refreshed on
  lookup; `CodexLiveRegistry`, `GeminiLiveRegistry` and `LiveSessionRegistry`
  now do too. The TTL itself is unchanged: a session nobody asks for is still
  reaped on schedule.

- Launching a CLI process is a cold start, and a cold start now always gets the
  full context. There are two cases and no third one: no process running, so we
  launch and send everything; or a process is running, so we send the delta.
  The context phase decides which applies, but the provider is what actually
  launches, and only it can find the process gone — crashed, or its container
  stopped — after the context was already built as a delta. It used to launch
  anyway, handing a bare question to a process that knew nothing: no
  transcript, no persona, no skills, no tool configuration. It now refuses
  (`ColdStartRequired`), and the turn is rebuilt through the ordinary cold path
  and run again. Nothing has reached the model at that point, so the restart
  costs no tokens.

- The context phase now asks the live-session question with each provider's own
  inputs. The shared helper's pool fallback was unconditional, but the two
  providers disagree on it: codex takes any compatible session, gemini only
  when the stored slot is missing — a concrete index that misses means the slot
  changed on purpose, and the old-slot container would resurrect the previous
  account's session. The policy is now the caller's. Both providers also read
  the stored pool index only while they still hold a session id, so the context
  phase does the same; reading it unconditionally would have passed a concrete
  index where the provider passes -1, and the two would have parted company
  again on the fallback.

### Removed

- The partial cold-context reconstruction (`_arm_cold_context_rebuild`,
  `_cli_cold_context`) and its clone whitelist entry. It recovered the
  transcript only, while its docstring promised persona, skills and tool
  configuration as well. Rebuilding the context through the path that already
  knows how to build it removes both the half-measure and the promise.

## [1.0.0-beta.58] — 2026-07-30

### Fixed

- The context phase and the CLI providers now answer "does this conversation
  still have a live session?" the same way. The providers ask their live
  registry; the context phase asked whether a session id was still persisted.
  Once anything cleared that id — a stale-thread reset, a compaction
  invalidation, a pool index that no longer matched — the two disagreed
  permanently, because nothing on the codex reuse path wrote the id back: every
  turn announced a cold start, loaded and compacted the whole transcript, then
  watched the provider find the very same process alive, resume it and discard
  all of it. Both branches now go through one helper
  (`core.cli_live_sessions.find_live_cli_session`), and codex re-persists a
  thread id recovered from a live session.

- A CLI turn no longer loses its context when the session it was told to reuse
  disappears first. The context phase empties the message list whenever it
  finds a live codex or gemini session, because a resume only needs the delta —
  but that check reserves nothing, and the idle sweeper, a cleanup or a crashed
  process can take the session away before the provider acquires its turn lock.
  The provider then correctly went cold with a list that no longer described
  the conversation: no transcript, no persona, no skills, no tool config. The
  context phase now leaves a one-shot callback the provider invokes on its cold
  path to reload the real context; gemini additionally stops loading its stored
  session in that case, since replaying it on top of a rebuilt context would
  send the transcript twice and leave the gauge on the dead session's numbers.
  Nothing changes on the happy path, where the session really is still there.
  The callback is carried across `clone_for_call`: the agent loop clones the
  client after the context phase arms it, so without that the recovery was a
  no-op in production while every same-instance test still passed. The rebuilt
  context also carries the provider system prompt a resumed turn deliberately
  omits, and is concatenated with the turn's delta instead of replacing it —
  replacing it dropped the user's actual question.

- The Gemini and Antigravity VNC logins start their container again. The
  beta.56 label work added the `pawflow_container_labels` call to that
  background thread but not its import — the identical imports in the Claude
  and Codex branches are local to *their* nested functions — so both logins
  died on a `NameError` reported as a generic "Login failed", before any
  `docker run`. The test that shipped with the label work only checked that the
  name appeared somewhere in the file, which a grep cannot distinguish from a
  name that is actually bound; the new test runs the thread.
- Stopping one PawFlow server no longer removes another one's containers. The
  shutdown reaper's first pass filters on this server's label and is exact, but
  the legacy name pass that follows matches prefixes carrying no server id
  (`pf-cc-pool-`, `pawflow-relay-srv-`, the logins…), which on a shared Docker
  daemon also name a second instance's containers — and those survived pass 1
  precisely because their label was not ours. The label now decides in both
  directions: a container owned by another server is left alone, and only an
  unlabelled one, which can only predate the label, is reaped by name. The
  decision moved into `core.docker_utils.legacy_reap_ids` so it can be tested
  at all. `pawflow-agy-login-` was also missing from the list, so an old
  Antigravity login could survive instead.
- A FileStore write no longer outlives the conversation it belongs to. Moving
  the bytes outside the global lock (beta.55) left a window: `delete_by`
  snapshots the entries it can see, and a store that reserved its path earlier
  is not among them, so it registered afterwards and resurrected a file for a
  deleted conversation — or crashed with `FileNotFoundError` when the wipe took
  the bucket directory with it. A per-conversation wipe counter, read before
  the reservation and rechecked under the lock, now discards such a write and
  its bytes; `store`/`store_file` return `""` in that case.
  The wipe is announced in the same locked block that takes its snapshot and
  stays announced until it finishes, so a write cannot slip between the two —
  reserving after the snapshot and registering before the counter moved — and
  come back with a valid id for a conversation that no longer exists.

- The interactive proxies no longer grow their capture log without bound. The
  shell that starts each one appends its stderr to a file, and for
  claude-code-interactive that file sits on the container's 512 MB `/tmp`
  tmpfs, shared with every tool call — so a long-lived container did not merely
  waste space, it eventually broke the tool calls that needed to write there. A
  daemon thread now checks the file at startup and every 60 s and truncates it
  past 20 MB, which is safe under the live writer because the redirect opened it
  with `O_APPEND`. The Antigravity observer gets the same guard on its stderr
  capture file; its `observer-<id>.jsonl` is deliberately exempt, because the
  pool reads that one back for the `proxy_start` event to decide a session is
  still alive.

- The three webchat invariants that only a browser covered now run without one.
  When beta.57 made the browser tests skip on a runner where headless Chromium
  renders nothing, the turn controller kept its Node twin but the
  `conversations.js` integrations kept nothing: pagination in backend cursor
  units, per-conversation runtime turns across an A/B/A switch, and eviction
  forgetting every id it removed were unguarded wherever the browser was
  unusable — which includes CI. `tests/js/conversations_spec.js` drives the
  real sources against the DOM stub with the same fixtures and assertions, and
  `tests/test_conversations_js.py` puts it on the gate. Each of the three was
  confirmed to fail against a deliberately broken guard. The browser tests stay
  as the real-browser run, and their skip message no longer claims a hole that
  is now closed.

## [1.0.0-beta.57] — 2026-07-30

### Fixed

- A browser that cannot run says so instead of failing the build. Headless
  Chromium never produces a DOM for a local file on the CI runners, and neither
  extra flags nor a longer timeout changed that — it hung at 20 s, then at
  120 s. The line is now drawn at the DOM: no DOM at all is an environment
  verdict and skips, a DOM carrying an error is a behaviour verdict and still
  fails. The verdict is remembered, so the file costs one 25 s wait instead of
  four. The invariants that test asserted also run under Node in
  `tests/js/turn_view_spec.js`, which needs no browser.

## [1.0.0-beta.56] — 2026-07-30

### Security

- An agent turn submitted through the runtime API keeps the principal that API
  stamped: `source_attributes` can no longer overwrite `http.auth.principal`,
  the channel or the request id. A flow forwarding its own visitor payload as
  provenance could otherwise choose the identity every conversation ACL
  downstream authorizes against.

### Changed

- A finished sub-agent gives its CLI container back. A flash agent, delegate,
  task or plan step kept its warm container until the idle sweeper reaped it
  one service timeout later (30 min), while the pool is 1:1 and capped at 50
  and reuse is keyed on identity, never availability — so a fan-out of
  differently named flash agents consumed slots nobody could borrow. Only an
  explicitly persistent delegate keeps its session.
- Everything PawFlow spawns now carries an `org.pawflow.server-id` label and
  the shutdown reap is driven by it instead of a hand-maintained list of name
  prefixes. Interactive Claude Code and the Antigravity observer containers
  were never in that list and survived `docker stop`. The reap also runs before
  the ConversationWriter drain: `docker stop` SIGKILLs after 10 s by default,
  so a reap queued behind a 20 s drain never ran at all.
- Starting a CLI instance is a cold start for codex and gemini too. Codex
  accepted a rollout jsonl on disk as proof of session and gemini checked no
  liveness at all, so a relaunch after the 1800 s idle sweep resumed the
  provider thread instead of reloading `initial_context.md` — for as many
  tokens as our own, possibly compacted, context, and with a gauge left on the
  dead session's numbers. Both now require a live process, like CCI and
  antigravity already did.
- Simplified-view turn boundaries are positional again. beta.55 made a durable
  `turn_id` route rows to the block it names, so answering a first message while
  the reader had already sent a second inserted that answer *above* the newer
  message. An id names a turn; it never selects one. The rule, why correlation
  loses, and the fact that it has now been reverted once are stated in
  `turn_view.js` and `docs/SIMPLIFIED_LIVE_CHAT_VIEW_PLAN.md`. The runtime
  `active_turns` snapshot stays: it reports liveness, never placement.

### Fixed

- The installer's rollback works again: a mangled line continuation made it
  call `docker rename` with a third operand, so a failed replacement removed
  the new container and left the previous server stopped and renamed instead
  of restoring it. The fake docker in the tests now checks argument arity,
  which is what let this ship green.
- Simplified view draws sub-agent boxes again. A classic-mode
  `group_delegate_messages: false` followed the user into simplified, where
  the delegate box is the only sub-agent renderer and its toggle is hidden.
- A turn that was live when the page loaded stops being treated as live once
  it ends: its runtime entry is retired on both terminal paths, so the block
  closes and the reader gets its last message back at the next reconciliation.
- A conversation handed over is no longer blocked by a FileStore row whose
  bytes are already gone; the intact attachments move and the stale row is
  logged. Storing a file no longer holds the global FileStore lock across the
  disk write, while still following an owner handoff that lands mid-write.
- The CLI context gauge passes through zero on every cold start, for every
  provider. The two hand-built `message_meta` payloads dropped
  `cli_context_state`, and the browser discards a zero that does not carry it,
  so a turn could measure an empty window and be unable to say so.
- The between-rounds gauge refresh covers all four CLI providers instead of
  claude-code-interactive alone; codex, gemini and antigravity no longer freeze
  their gauge until the agent stops.
- The browser-level webchat tests run with a private Chromium profile and no
  first-run/network work, so they no longer hang the CI runner.

## [1.0.0-beta.55] — 2026-07-30

### Security

- Conversation-scoped PFP dev load/unload now applies its runtime default scope
  during authorization too, so omitting `scope` cannot turn a conversation
  operation into an ungated user-scoped one.
- Tool process-control actions now fail closed on unknown ids and authorize the
  conversation resolved from the effective approval request or tool call. Direct
  providers reserve ownership before worker submission, closing the interval
  before background registration, and command-bearing tool names are normalized
  case-insensitively before dangerous-command policy is evaluated.
- Conversation FTS now purges derived plaintext when the source listing or a
  transcript is unreadable. Every transcript rewrite bumps its generation under
  the conversation lock, so deleted or redacted text cannot survive behind stable
  ids/timestamps or a transient read failure.

### Fixed

- Simplified webchat pagination now advances a backend transcript cursor instead
  of deriving one from DOM nodes that tabs may reparent. Active-turn snapshots
  hydrate before replay suppression, conversation switches preserve live
  animation/state, durable `turn_id` routes concurrent turns, and legacy
  unstamped transcripts retain positional boundaries.
- Conversation owner handoff now publishes the new owner only after the
  conversation directory, scoped resources, and FileStore bytes/index have all
  moved. Stores serialize with the transfer, failures roll back, and reverse-index
  rebuilds cannot overwrite an invite or role update that lands during a sweep.
- Server updates now reject out-of-root symlink destinations, propagate requested
  git/Compose pull failures, and restore the previous container image when the new
  server fails startup or health checks.
- Managed relay recovery uses the live relay WebSocket as health, replacing a
  container that is running but disconnected instead of waiting forever for a
  wedged client.
- CCI consumer takeover now orders epoch changes, queue delivery, pushback,
  overflow, and wakeups under one condition, preserving the replacement turn's
  first event and stream order.
- CLI context gauges keep their integer bootstrap baseline across `LLMClient`
  clones and preserve `cli_context_state` through both `message_meta` and `done`,
  including authoritative cold zero after compaction.

## [1.0.0-beta.54] — 2026-07-30

### Security

- **A read collaborator could drive the tools of a conversation they could
  only watch.** The SSE stream hands approval requests to anyone with read
  access, and `tools_exec` gated nothing: the dialog could be answered —
  including with `always_allow`, which persists — and the tools that ran next
  killed or detached. The cluster now carries a role table like the three
  already gated, and driving is a write. A tc_id and an approval request_id
  name their target on their own, so the conversation that actually owns them
  is resolved and the check lands there, rather than on the `conversation_id`
  sent alongside.

- **Any authenticated user who knew a conversation id owned its resources.**
  A conversation-scoped agent, skill, prompt, MCP or task is filed under the
  conversation's owner, and `ResourceStore` resolves that owner from the id
  alone — deliberately, so a collaborator's turn finds them. Nothing asked
  whether the requester was entitled to it: the whole `agent_resource` cluster
  took a `conversation_id` straight from the request body and could list, read,
  overwrite or delete them, or point the conversation at another agent. All
  fifty-odd actions are now classified, with a completeness test that fails on
  the next one added without a row.

- **`monitor` bypassed content-sensitive approval.** It builds a shell script
  and hands it to `BashHandler` in-process, so the gate never saw a `bash`
  call: one approval of a harmless monitor was persisted and every later
  command rode in on it — a destructive one, or `local=true` on the host —
  with no second prompt. The gate now judges `monitor` on its command, as it
  does `bash` and `execute_script`.

### Fixed

- **A CCI handoff could swallow the first event of the new turn.** The
  consumer epoch was checked on the way into `wait_event` and never after the
  blocking get, so a coordinator already parked there when a newer consumer
  claimed the stream was still the one the queue woke — and dropped the event
  on its way out, truncating text or severing a tool call from its arguments.
  The event is handed back and the new owner takes it before touching the
  queue.

- **A complete Azure endpoint URL lost its `api-version`.** The request line
  was rebuilt from the path alone, so an operator who pasted the full target
  from the portal had the mandatory version — which lives in the query —
  dropped from every request, streaming and not, and Azure rejected them all.

- **A conversation handed to a collaborator arrived without its resources or
  its files.** When a departed owner's conversation moves, only the
  conversation directory used to move: its own agents, skills, prompts, MCPs
  and tasks are filed under the owner, and so are its attachments, both
  resolved through whoever owns it now. Both follow it now. The move is also a
  compare-and-swap: two collaborators who had each resolved the departed owner
  before either took the lock moved it twice, the second taking it from the
  first, who carried on writing against a directory the conversation had left.

- **A slow passive recall surfaced turns later, on another subject.** A turn
  arriving while one was in flight was skipped — by design — but the slow
  answer still landed and was served to some later turn asking about something
  else. A recall now publishes only if it is still the newest one asked for.

- **A valid tool result containing the words "unknown tool" re-ran the tool.**
  The MCP bridge read the bare substring as a routing miss, threw the result
  away and called the lowercased name — running the side effects a second
  time. It now matches the routing error itself.

- **A failed `docker compose pull` was reported as a successful update.**
  `pull --ignore-buildable || pull || true` turned an unreachable registry, an
  expired login or a rate limit into a success, and `up` then cleanly
  restarted the image already on the host. The flag is probed instead, so
  where compose can tell "nothing to pull" from "pull failed", a failure stops
  the update; the legacy path still tolerates it but says so.

- **A failed artifact refresh destroyed part of the installation it was
  replacing.** Each artifact was deleted just before its replacement was
  copied in, so one failed `docker cp` left that artifact simply gone. Every
  artifact is now copied into a staging directory first and swapped in only
  once they are all there.

## [1.0.0-beta.53] — 2026-07-30

### Fixed

- Loading an older history page in simplified view no longer completes the
  newest live block or leaves a historical partial turn ticking forever.
- Persisted delegate traces now reload in Tool calls, and simplified view always
  renders delegate boxes even when classic delegate grouping was disabled.

## [1.0.0-beta.52] — 2026-07-29

### Fixed

- **The simplified view collapsed to a flat transcript on a long turn.** Top
  level holds a user row, that turn's block, and the block's last message —
  nothing else. The view enforced that as rows arrived, and only then: a block
  was built from a user row and died with it. On a conversation where the agent
  works for hundreds of rows without being spoken to, the 50-row history window
  holds no user row at all, so no turn ever opened and every thought, tool call
  and message rendered inline with no block anywhere — and each reload
  reproduced it exactly. Activity with nothing above it now opens a turn of its
  own, a turn outlives the row it was anchored on, and `turnViewReconcile`
  enforces the rule against the DOM after every history render: a stray row is
  filed into the turn it falls in, and a replayed turn is given its last
  message under its block instead of buried inside it.

- **A delegate left nothing on screen in the simplified view.** Delegate
  grouping had been filed with the classic-only view options and forced off,
  and it is the only thing that draws a sub-agent at all: the box was never
  built, and a stored trace — whose content is empty by construction — rendered
  as a blank row. A sub-agent could run, work and return with nothing visible
  but its result message. The box is activity, so it is drawn again and the
  turn view files it with the tool calls, inside the block.

- **A delegate result was previewed by its opening, not its conclusion.** What
  lands in the caller's context and in the transcript is a preview of the
  sub-agent's answer, cut from the head — so it showed the agent announcing
  what it was about to do and stopped before anything it found. On a provider
  whose turn is several messages long the conclusion was never in it. The
  preview is now the tail, starting on a line boundary when one sits near the
  cut, and the full text stays in its FileStore copy.

- **The codex app-server glued its assistant messages together.** Each
  completed message was one entry of `final_text_parts`, joined with nothing:
  two messages met as `as requested.I'm scoping`, and a turn read as one
  run-on paragraph with no way to tell where its answer started. They are
  joined with a blank line; the delta fallback, whose entries are fragments of
  a single message, still is not.

## [1.0.0-beta.51] — 2026-07-29

### Changed

- **A new conversation opens in the simplified view.** `chat.view_mode` now
  falls back to `simplified` instead of `classic`, so a conversation nobody
  configured gets the live turn view; an explicit choice at any scope of the
  cascade still wins, and the View menu switches back per conversation.
- **A new conversation starts in `auto` permission mode.** The mode is written
  once, at creation, by the shared creation contract — web chat, Telegram and
  the flow API alike — rather than becoming the fallback every reader applies.
  Conversations that already exist keep the mode they have been running under.
  On a deployment where agents touch production systems or untrusted input, set
  the conversation back to `default` or `read_only` before giving it work.

### Documentation

- The webchat view reference described correlated turn grouping and a classic
  default, both of which stopped being true in beta.47. It now describes the
  positional boundaries, the last-message spot, the replayed-history exemption,
  the header timer and the glyph rain, and names simplified as the default.
- The in-app update is documented as it now behaves: `/health` carries the
  running version and a per-process id, the panel waits for a *different*
  process before reloading, and after ten minutes it says which of the two
  failures happened. The `chown` handover and its incomplete-uid/gid fallback
  are documented with it.
- The security model states the `auto` creation default and when to move off it;
  README covers both views, the creation defaults, and the browser update path;
  the website carries an update how-to, a view how-to, and FAQ answers matching
  them.

## [1.0.0-beta.50] — 2026-07-29

### Changed

- **A cold CLI session starts with room to work.** The proactive compact that
  runs before a CLI session is handed its context was evaluated against
  `compact_threshold_pct` — 95% in practice. That threshold is right for a
  live session, where it guards against overflow, but a cold session receives
  its whole context in one go: the stored messages are serialized into
  `initial_context.md` and read back as a single tool result. At 95% a session
  could therefore open at 94% of its window, having done nothing yet, and be
  compacted again within a few turns. A cold start is now held to
  `CLI_COLD_START_TRIGGER_FRACTION` (40% of `max_context_size`) instead —
  whatever `compact_threshold_pct` says, including 0, where the provider's own
  mechanism cannot help because the file is written before it sees anything.
  A stricter configured value is never loosened, and live sessions and direct
  API providers are untouched.

### Added

- **`tools/gauge_probe.py`** — runs the gauge counter and the compaction
  counter over a stored agent context and reports how much of their difference
  the cold-CLI bootstrap boundary accounts for. `UNEXPLAINED` must be 0.
  It also lists every *structural* bootstrap marker, which a grep for the
  marker string cannot do: that also matches messages which merely quote it,
  such as tool output from reading this repository's own source. Read-only, no
  network, works without `tiktoken`, and accepts a bare `.jsonl` so a version
  recovered from conversation git history can be inspected.

### Fixed

- **The context gauge no longer survives the CLI session it describes.** On a
  CLI provider the gauge measures the provider's window since the last
  `initial_context.md` read -- everything before that boundary is deliberately
  counted as zero, because the provider received a file reference, not those
  messages. A server restart kills that session but left the persisted entry
  untouched, and `compute_context_usage` returns it verbatim while no agent is
  running: the next turn redisplayed the dead session's percentage against a
  window nothing had filled. Measured in the field at 35% of 800k on a cold
  start. `_prepare_agent_context` now zeroes and republishes the gauge at the
  one place that knows the session is gone.

  The compaction that followed the restart was not a second bug. While a CLI
  session is live the threshold is evaluated against the gauge itself, so a
  gauge below the threshold means no compaction. The whole stored context is
  measured only on a cold session, where the provider window is empty by
  definition and the size that matters is what is about to be written into
  `initial_context.md`. The stale reading was the only defect: with the gauge
  at 0 the sequence reads as it should -- cold start, oversized store,
  squeeze, then a gauge that climbs as the provider reads the file.

- **A compact notice no longer escapes the simplified view.** The
  `compact_progress` handler creates its system row through `addMsg` rather
  than a message event, which made it the last row-creating path not routed
  through `turnViewIngest`: it landed at top level, outside every block, and
  stayed there. Same for the git-prune notice. Both are now filed in the
  block -- and explicitly barred from the outside spot, which belongs to the
  last message of the turn, not to a status line.

- **`tiktoken` is imported lazily.** `core/token_counter.py` carries a full
  fallback path for when the tokenizer is unavailable -- approximate counting,
  a five-minute retry window -- but imported the module at file scope, so its
  absence raised `ModuleNotFoundError` instead of degrading. Running any tool
  that touches token counting outside the server image failed outright.

## [1.0.0-beta.49] — 2026-07-29

### Changed

- **The simplified view's activity surface is a rain of glyphs, and every cue
  condenses out of it.** The block draws a themed glyph rain behind its cues --
  one shared 15 fps ticker for every running block on the page, stopped the
  moment the last one ends -- and a cue arrives by resolving character by
  character out of that same alphabet, text and copied tool call alike. Cues no
  longer share one spot: they form a column, newest on top at full strength,
  older ones pushed down and fading out. Nothing leaves on a timer any more --
  a cue holds its place until a newer one arrives, so a minute of silence no
  longer blanks the surface. A tool call is shown as the classic view draws it,
  copied in full, instead of the label "Calling tool..." that named neither the
  tool nor its arguments. The surface is taller (190px) and its text is meant
  to be read rather than glimpsed.
- **The block header counts the turn's seconds**, ticking while it runs and
  frozen at what it took once it ends. Through a long silent stretch it is the
  one thing on screen that keeps moving.

### Fixed

- **The last message of a turn is shown outside its block, always.** Which row
  is the answer was decided from the done payload, so a provider that ends a
  turn without naming a final message -- the CLI ones do -- left its answer
  inside a collapsed tab under a header still reading "working". It is
  positional now: the last message row of a turn takes the spot under the
  block and hands it back when a newer one arrives. Replayed history is
  exempt, since an older page arrives after rows that precede it.
- **A done event closes the block even when it names no final message.** The
  close used to require `is_final` *and* `final_msg_id` together; what the
  server names still decides which row is hoisted out, but whether the turn is
  over no longer depends on it.
- **A new user message closes the block above it.** A turn the server never
  closed stayed on "working", with its clock running, above a message the
  reader had already moved past. A turn that ended on an error or a stop keeps
  that status.

- **The update panel reloaded onto the version it started from.** It polled
  `/health` and reloaded on the first answer, and `/health` said only `ok`. An
  updater that failed before stopping anything therefore looked exactly like a
  finished update: the server never went away, the first poll succeeded, the
  page reloaded at once on the old version, and nothing said the update had not
  happened. `/health` now carries the running version and a per-process id, the
  panel waits for a *different* process, and after ten minutes it says which of
  the two failures happened and prints `docker logs pawflow-updater`.
- **The update left its new install directory owned by root, silently.** The
  updater runs as root, so the per-version directory it extracts is root-owned
  until it hands it back. That `chown` required *both* `PAWFLOW_RUN_UID` and
  `PAWFLOW_RUN_GID` in the server container's environment; a container created
  by an older start script carries only the first, so the step was skipped
  altogether -- with no warning, on an update whose log otherwise reads clean.
  The owner is now read off the install directory being replaced when the pair
  is incomplete, and the `chown` runs whatever the refresh did rather than only
  on the success path.
- **A failed host-artifact refresh could leave the deployment unstartable.**
  When the refresh failed on the first artifact -- the start script itself --
  the new directory existed and was empty, and forcing past the failure still
  `cd`-ed into it: the updater died on a script that was not there and the
  server was never touched. The handover now falls back to the directory being
  replaced, which does carry a working script.
- **`install-pawflow.sh` failed on `unlinkat: permission denied`.** That is what
  the leftovers above look like from the command line, and the message named
  neither the cause nor the way out. The extraction now checks the runtime
  directory's ownership up front, before creating anything, and prints the
  `chown` that takes it back.
- The server-update test suite no longer calls the live GitHub API. One test
  resolved the published image without pinning the release lookup, so a
  rate-limited or offline runner got an empty tag and the build failed on
  beta.48 for a reason unrelated to the change under test. The whole suite now
  passes with outbound HTTP refused.

## [1.0.0-beta.48] — 2026-07-29

### Changed

- **The collapsed activity cues stack instead of taking turns.** One cue at a
  time, each waiting 1.5 s for the one before it, hid most of what the agent was
  doing and read as a slideshow. Cues now share one spot and stack in depth: the
  newest zooms in at the front, sharp, while the ones behind it are pushed back
  a step — smaller, dimmer, motion-blurred — and drop off the back of the stack.
  Each leaves at its own moment. A cue's pose is a function of its depth alone,
  so it is recomputed on arrival, and the surface is a fixed-height clipped
  stage: a burst of activity never shifts the layout.

### Fixed

- **Tool results escaped the turn block.** A result whose `tool_call` row is
  not in the DOM is queued for 750 ms and then rendered standalone. That
  fallback in `sse_state.js` was the one row-creating path never wired to the
  turn view, so every unmatched result — backgrounded MCP calls, results
  arriving after a view switch reload — was dropped at top level between the
  block and the next user message. Read off the live DOM: the block held 54
  `tool_call` rows and zero `tool_result` rows while three sat outside it.
- **A user message from another client was filed inside the previous turn.**
  The live `new_message` handler ingested every role, so a user message that
  this tab did not itself submit became turn content instead of a boundary.
  It now opens a turn, exactly like the local submit path.
- Standalone assistant narration — a delegate reply with no delegate frame, an
  agent response — is handed to the block instead of being left at top level.
- **The simplified turn block rendered as a bare bar and swallowed its own
  header.** The message column is a scrolling flex column, so its items shrink
  once the transcript is taller than the viewport. Every other `.msg` is
  protected by the automatic minimum size — which, per the flexbox spec, does
  not apply to a flex item whose overflow is not visible. The activity block is
  the one `.msg` with `overflow: hidden`, so on any conversation long enough to
  scroll it collapsed to the 22 pixels of its own padding: no readable header,
  no animation, and a two pixel sliver as the only clickable target. Measured
  in headless Chromium against the real stylesheet and the real controller:
  22px before, 100px while working and 46px once completed after. The block now
  declares `flex: none`, and drops the `.msg` padding it never wanted — as
  `.msg.simple-turn-block`, because the `.msg` padding is declared further down
  the sheet and wins on source order at equal specificity.
- **The tab icons filled their own tabs.** `_turnSvg` emits a `viewBox` and no
  dimensions, so each inline SVG scaled to the full width of its grid column —
  four ~180px pictograms with their labels pushed out of the block. They are
  sized at 16px and the tab lays its icon and label out on one row.
- A completed turn no longer reserves the ephemeral animation band. It is laid
  out only while the block carries `turn-working`, so a reloaded transcript —
  which is nothing but finished turns — shows headers instead of empty strips.

## [1.0.0-beta.47] — 2026-07-29

### Fixed

- **The simplified chat view did nothing at all in the web chat.** Grouping was
  built on `turn_id` correlation, which the runtime derives from the flowfile
  attribute `agent.request_msg_id`. Only the programmatic runtime API ever set
  it, so every turn submitted from the web chat — the only client the view
  exists for — ran with an empty one. No stored message and no live event
  carried a `turn_id`, so `turnViewIngest` rejected every row: no activity
  block was built, nothing was reparented, and there were no tabs, no ephemeral
  text and no icons. Selecting Simplified persisted the mode and switched the
  menu, then rendered an ordinary classic transcript, with no error anywhere.

  The view no longer groups by correlation. Boundaries are positional, which is
  what they always were on screen: a user message opens a turn and its block,
  everything rendered after it goes into the block, the terminal answer is
  lifted out and placed below it, and the next user message closes the turn and
  opens the next one. A user message that arrives before the answer therefore
  reads user / block / user / block / answer, with the answer under the last
  block — where the reader is looking — rather than lifted back above content
  that arrived after it. `turn_id` names a turn but no longer routes rows, so it
  can no longer be missing: a turn nobody stamped groups identically, including
  one whose user message has no id at all.

  The submitting path also stamps the turn id it was always meant to carry, from
  the user message id the browser already generates, so stored rows are
  self-describing and the terminal-answer marker lands on reload. That stamp is
  no longer load-bearing for the view itself.

## [1.0.0-beta.46] — 2026-07-29

### Added

- **Simplified live chat view.** The View menu now stores `chat.view_mode` at
  conversation scope. `classic` keeps the existing transcript; `simplified`
  presents each user turn as the user message, one expandable activity block
  with live Messages, Thinking, Tool calls and Artifacts tabs, and the same
  canonical final assistant message below it. The block reparents the nodes the
  existing renderers already produce instead of re-rendering them, so tool
  result attachment, diffs, inline media, message actions and `msg_id` dedupe
  keep working unchanged. Only a successful `show_file` result becomes an
  artifact card; approvals, questions, errors and notifications stay top-level.
  Turns carry durable `turn_id`/`turn_final` metadata so reload, pagination and
  reconnect rebuild the same grouping. Classic mode is untouched.

### Security

- **Context and usage actions were not gated by the sharing ACL at all.** Phase
  3 of the sharing work put a role table in front of the conversation
  dispatcher, but `_ctxops_*` and `usage` were never given one, and neither
  called `require_read`/`require_write`. They took whatever `conversation_id`
  they were handed and acted on it. Measured before the fix, against a
  conversation owned by someone else:

  - a user with **no relationship to the conversation at all** got `200` on
    `get_context`, `view_context`, `usage_conversation`, `get_cost` and
    `list_context_usage` — reading another account's agent contexts and what
    their conversation cost;
  - a **read-only collaborator** got `200` on `delete_agent_context` and
    `add_context_message`, and `202` on `git_prune` — destroying an agent's
    whole context and pruning the conversation's git history from a role whose
    entire meaning is that it cannot write.

  Both dispatchers now gate from a role table, like the conversation one, with
  a completeness test per cluster so an action added without a row fails the
  build instead of shipping ungated. All of the above answer `404` — the same
  answer an unknown id gets, so the gate leaks no existence either.

- **The deployment-wide cost total was readable by any authenticated user.**
  `get_cost` with no `conversation_id` answers `UsageLedger.total_cost()` —
  every turn every user of the install has ever run. With an id it is now gated
  on that conversation; without one there is nothing to gate it on but the
  role, so it requires admin. The other usage actions were already scoped:
  `cost` reads `user_usage(user_id)`, and `_usage_query_filters` pins non-admins
  to their own id.

- **Realtime sessions refused write collaborators.** `_livekit_sessions.py`
  still used the owner-equality test inherited from the legacy voice bridge, so
  a collaborator who could drive the agent by typing was denied the microphone.
  Not a leak — the opposite — but the ACL is the single source of truth for
  that question. A non-owner is now allowed exactly when `require_write`
  allows them.

### Fixed

- **The simplified view hid the answer of every turn it could not prove was
  final.** The view lifts the row carrying `turn_final` out of the activity
  block to render it as the standalone answer, so a turn without that marker
  showed a user message, a collapsed block, and no visible reply. The marker is
  written by a patch that legitimately never lands: rows recorded before the
  feature carry none, terminal paths that leave `final_msg_id` empty produce
  none, and a patch matching no row was discarded in silence. Every
  pre-existing conversation reloaded in simplified mode therefore lost its
  answers. Reconstruction no longer depends on the marker: when no row of a
  turn carries `turn_final`, the display classifier marks the last visible
  assistant row and flags it `turn_final_derived`. Error rows, tool traffic and
  display-only rows are never promoted, and a turn that produced no visible
  answer still gets no final row. Classification runs per page, so a derived
  marker never displaces a final already placed, and an authoritative one
  reclaims the row it supersedes. `patch_message` now warns when it matches no
  row instead of returning quietly.

- **Live-window trimming could destroy a turn that was still running.** The
  trim reaches a turn through its user anchor, which is never marked live even
  while the block below it streams, so the existing live guard was bypassed and
  the whole group was evicted mid-flight. The guard now covers every node of
  the group.

- **Streamed text queued one animation cue per token.** The coalescing window
  the simplified view declared was never wired up, and turn identity was
  re-rendered twice per token on top of it. Text now emits one cue per window
  and identity re-renders only when it changes; discrete tool cues stay
  immediate. The transient excerpt also took the last characters of the stream
  rather than the first, and the load-more banner counted nested `msg_id`s in
  classic mode, inflating its total.

- **CLI context gauges counted a session's context before the session had it.**
  The gauge now follows the context actually present in the provider session: a
  compact or context edit invalidates that session and resets it to zero, the
  next cold turn counts only the short injected bootstrap prompt, and the
  serialized PawFlow messages join the gauge when the CLI reads
  `initial_context.md` — counted once, not twice. This lifecycle now covers
  Claude Code, Claude Code interactive, Codex app-server, Antigravity and
  Gemini CLI, replacing the flat invisible-overhead offset that applied to
  Claude Code alone. Direct API providers keep counting their request messages.

- **`conversation_search` kept serving text that had been edited or deleted.**
  The index skips a conversation whose `updated_at` has not moved, and only
  rebuilds one whose row count shrank. Neither sees a redaction: editing a
  message in place leaves `updated_at` untouched — it is the newest message's
  timestamp — at a constant row count, and deleting the newest message moves it
  *backwards*, which reads as "older than what I already hold". A password
  redacted out of a transcript, or a message deleted from it, stayed searchable
  indefinitely. The store now keeps a `transcript_generation` counter, bumped by
  every rewrite and never by a plain append; the index compares it and rebuilds
  the conversation when it moves. Appends stay incremental — a search pays for
  the refresh, so re-reading every transcript would trade a correctness bug for
  a performance one. An index written before the counter existed is rebuilt
  once, since it may hold exactly the text this fixes.

- **Two invitations sent at the same moment could lose one from the "shared
  with me" list.** The reverse index is a whole-file read-modify-write with no
  lock, so two invites to the same person from two conversations both read the
  old list and the second write dropped the first. The ACL is authoritative and
  was never affected — nothing was granted or denied wrongly — but the
  conversation was missing from that user's sidebar until the index was
  rebuilt. Now serialised per user, and the staging file carries a unique name:
  a fixed `.json.tmp` let one writer publish another's bytes.

- **A bad server image destroyed the running server before it was found to be
  bad.** `scripts/run-pawflow-docker.sh` probes the new image twice — does it
  carry the Docker CLI, can it reach the mounted daemon — and both probes ran
  *after* `docker rm -f` on the server container. An image missing the CLI, or
  a socket the container could not reach, left the operator with no server at
  all and a message about how to rebuild one. The probes read and start
  nothing, so they now run first; the destruction is the last step before
  `docker run`. An update that fails must leave the old server running.

- **The update refreshed the server image but never the host files that came
  with it.** `install-pawflow.sh` copies a set of artifacts out of the image
  onto the host — the start script the update itself runs, `doctor-pawflow.sh`,
  the relay image catalog, the AppArmor profiles, `docker/claude-code`,
  `docker/pawflow_sdk`, `tools/mcp_bridge.py`, `core/tool_json.py` and
  `pawflow_relay`. They live outside the container, so pulling a new image left
  them untouched: every update from the UI kept whatever version had last been
  installed from the command line, indefinitely. A fix to any of those files
  could only ever reach a host through a manual reinstall.

  The updater now extracts them from the image it just pulled, into the new
  version's `~/.pawflow/runtime/<tag>` directory, and starts the server from
  there — `PAWFLOW_SOURCE_DIR` moves the `org.pawflow.host-app-dir` label with
  it, so the next update finds what this one wrote, and the previous version's
  directory stays intact to fall back to. An install started with
  `--runtime-dir` is refreshed in place instead, and a git checkout is left
  alone: `git pull` is what moves its files.

  The refresh happens between the pull and the start script, the window where
  failing is still free. **It aborts by default** — the files it could not
  write include the start script about to run — with an opt-in checkbox to
  continue anyway, which warns that the new server image is being started with
  the host-side files of the version it replaces.

## [1.0.0-beta.45] — 2026-07-29

### Fixed

- **A relay container the Docker daemon refused to kill aborted the whole server
  update.** `scripts/run-pawflow-docker.sh` removes the managed relay containers
  before recreating the server, so they restart with current runtime code. It
  did so in a single `docker rm -f` whose failure, under `set -e`, stopped the
  script — *after* the new image had been pulled and *before* the server
  container was recreated. A beta.42 → beta.44 update hit exactly that (`could
  not kill container: tried to kill container, but did not receive an exit
  event`): the server stayed on beta.42 while its relay was left half-killed,
  and the update looked like it had simply reloaded. Each relay is now removed
  on its own and a failure is a loud warning on stderr instead of an abort.

- **A managed relay container that died stayed dead until the server was
  restarted.** The container of a managed server relay is started once, from
  `RelayService.connect()`, and runs with `--rm`. Nothing re-created it, so a
  crash — or an operator running `docker rm -f pawflow-relay-srv-<id>` to
  unblock the update above — left the transport retrying against a container
  that no longer existed, for as long as the server stayed up. The relay now
  respawns itself: when a request fails with a disconnect error and is about to
  be retried, `ensure_managed_relay_alive()` re-creates the container if, and
  only if, it is really gone. A live container is left strictly alone (the
  ordinary disconnect is the relay client reconnecting, and a respawn would
  turn it into a cold start), unmanaged operator-run relays are never touched,
  and a burst of failing calls asks for one container start rather than one per
  call. This is what made the previous entry's "relays are recreated on demand"
  actually true; it was not, while the server kept running.

## [1.0.0-beta.44] — 2026-07-29

### Fixed

- **The CI security gate failed on a false positive.** `bandit` reads
  `GHCR_TOKEN_URL` as a hardcoded password (B105) because the name contains
  `TOKEN` and the value is a string literal. The constant is the public GHCR
  *endpoint* that hands out an anonymous pull token for a public repository —
  there is no credential in the source. Marked `# nosec B105`, as the OAuth
  `token_url` entries already are. The beta.43 image itself published normally:
  `docker-publish.yml` and `ci.yml` are independent workflows.

## [1.0.0-beta.43] — 2026-07-29

### Added

- **`conversation_search` — an agent can now search what was actually said in
  past conversations, not only what it remembered to store.** `recall` reads
  the memory store, which holds what some agent decided at the time was worth
  keeping; anything nobody extracted was simply gone. The new tool runs a
  SQLite FTS5 index over the raw transcripts (one database per user under
  `data/runtime/conversation_index/`), with an optional `summarize=true` that
  has the summarizer synthesize the hits. Read-only, approval-exempt, and
  allowed in read-only mode. **Encrypted conversations are never indexed** —
  an FTS index is plaintext, so indexing one would put its content back in the
  clear; a conversation encrypted after indexing is purged, and an unreadable
  encryption state counts as encrypted. Closes P4 of
  `docs/LEARNING_LOOP_PLAN.md`.

- **Agents are now asked to reflect, once there is something to reflect on.**
  The diary has always accepted a `reflection` entry type that nothing ever
  requested, so agents accumulated observations and never synthesized them. A
  `Reflection due` block now appears next to the diary digest — but only after
  5 diary entries since the last reflection *and* 6 hours since it, because a
  standing instruction to reflect is one an agent learns to skip and a
  wall-clock trigger nags a diary nothing was added to. The nudge also asks
  whether the synthesis deserves a `kg_add` triple or a skill, which closes the
  loop back into skill creation. Closes P5 of `docs/LEARNING_LOOP_PLAN.md`.

### Fixed

- **A resumed tmux session replayed the whole previous turn into Telegram.**
  After a background result woke the Claude Code session, the user received a
  hundred tool-call messages at once — every tool call of the previous turn,
  again. A live Claude Code session replays its **entire context** on every API
  request, so the MITM proxy observes every prior `tool_use` block each time;
  PawFlow-driven turns dedup against the container's id sets, but
  `_run_manual_capture` built its coordinator without them and fell back to
  fresh per-coordinator sets, re-emitting the lot. Each one was persisted as a
  transcript row and published as a `tool_call` event. The webchat keys tool
  blocks by `tc_id` and absorbed the repeat, which is why it looked like a
  Telegram-only bug — it was not: the duplicate rows were also going into the
  transcript. The capture now shares the pooled container's dedup sets, with a
  per-session fallback so two chained captures still dedup against each other.

- **A message refused by the runtime left a channel client waiting forever.**
  `AgentRuntimeAPI.submit_message` is how every non-HTTP transport submits —
  Telegram today. When the runtime refuses a submission it answers the same
  404 an unknown conversation gets, and that body carries neither `status`
  nor `wait_for_done`, so both defaulted to "accepted" and `True`. The caller
  then waited on the correlated `done` of a turn that was never started, and
  the Telegram bridge waits with no timeout by project rule — the user got
  silence, and the bridge thread stayed parked. Refusals now cancel the
  registered waiter and raise `AgentSubmissionRejected`, which the Telegram
  client already reports to the user. Found while writing the channel-bridge
  test that `docs/CONVERSATION_SHARING_PLAN.md` had been asking for.

## [1.0.0-beta.42] — 2026-07-28

### Fixed

- **The server could not update itself unless it was a Docker Compose stack.**
  Which most servers are not: `install-pawflow.sh` ends on
  `run-pawflow-docker.sh`, a plain `docker run`. Compose stamps its project path
  on every container it creates; a `docker run` stamps nothing, so the update
  refused with *"not started by Docker Compose"* and left the gear menu useless
  for every installer deployment. The updater now recognises that shape and does
  what the installer does: pull the published server image, then re-run
  `run-pawflow-docker.sh`, which already recreates the container in place. The
  environment of the running container is replayed, so the bootstrap key, the
  uid/gid and the relay images survive the update; `PAWFLOW_BOOTSTRAP_RESET` is
  forced empty, since replaying a fresh install's first-run flag would wipe a
  working server's installer state. `run-pawflow-docker.sh` now also stamps
  `org.pawflow.*` labels, and `core/installer_deployment.py` falls back to
  `PAWFLOW_HOST_APP_DIR` and `docker inspect` so an install that is *already*
  running is updatable, not only the next one.

- **The published version of the relay images was always unknown.** Two causes.
  It was never asked of the registry: the shipped catalog's
  `relay_image_version` was reported as "published", which answers what this
  server expects, not what exists. GHCR is now queried for the real tags, with
  the catalog kept as the offline fallback. And that catalog was read from
  `/app/config`, a host bind mount seeded no-clobber by the entrypoint, so an
  install predating the `relay_image_version` key kept a catalog without it
  forever — reporting an empty published version on a perfectly current server.
  Shipped, versioned data is now read from the image's own copy.

## [1.0.0-beta.41] — 2026-07-28

### Added

- **Source-scan tests now fail with a diagnosis instead of a substring error.**
  Structural invariants that cannot be tested by running the code are pinned by
  scanning source text, which couples a test to a marker string in a file that
  does not know it is a marker. `tests/_srcscan.py` replaces bare `str.index()`
  slicing: it refuses a marker that is missing, ambiguous, or matches only as a
  prefix of a longer name, and reports which marker, how often and on which
  lines. Its `region()` searches the end marker after the start marker, so a
  newly added symbol can no longer steal a region boundary and silently empty
  the slice — the failure that prompted this, where `def _append` matched a new
  `def _append_platform_note`. Markers shared by several tests are declared in
  `tests/_anchors.py` with the reason they exist, checked once by
  `tests/test_source_anchors.py`, and carry an `# anchor: <name>` comment at the
  anchored site so a rename is caught where it is written rather than in a
  distant test. See `docs/development.md`.

- **An agent is now told when another agent changes code under it.**
  Several agents in one conversation share the same relay, and therefore the
  same files. Agent B read `service.py`, reasoned about it for a few turns, and
  agent A rewrote it in the meantime — nothing told B. It kept editing against a
  view that no longer existed, and the collision surfaced only as a failed
  `old_string` match, or as a silently clobbered change.
  `core/read_conflict.py` uses the per-agent read hashes the edit guard already
  keeps: when a write lands, every other agent whose last read no longer matches
  what is on disk gets a notice, delivered at its next turn under *Files that
  changed under you*. All mutation tools report it (`write`, `edit`,
  `apply_patch`, `batch_edit`, `find_replace`, `delete`), on both the workdir and
  the relay path.
  It stays quiet: a single-agent conversation has no other readers and pays
  nothing, an identical rewrite notifies nobody, and a re-read clears the notice
  so the agent is told once rather than every turn. The notice is advisory — it
  asks for a re-read, it never refuses an operation.

### Changed

- **A read-conflict notice now reaches the agent during the turn, not after it.**
  The notice was delivered at the next context build, which meant an agent in a
  long tool loop kept working against a stale view for the whole loop — exactly
  the window where the collision does damage. It now rides the last tool result
  of a batch, so the agent sees it between tool calls. It is appended *after*
  the untrusted-content envelope: that envelope tells the agent to treat the
  content as external data, and a PawFlow-generated warning buried inside it
  would teach the agent to distrust our own warnings. The next-turn channel
  stays for turns that ended without tool calls; since taking the block clears
  it, exactly one channel ever delivers and the notice can never arrive twice.

## [1.0.0-beta.40] — 2026-07-28

### Fixed

- **Webchat messages reached nothing while a tmux turn was visibly working.**
  When a PawFlow turn ended with a background tool still running, the tool's
  result landed in the Claude Code container and Claude Code resumed on its
  own. That is a *captured* turn: `_active_turns` is registered so the agent
  shows as busy, but there is no streaming worker, no `_active_contexts` entry
  and no `_active_claude_client`. `agent_streaming` reads that combination as
  "already active but not preemptable" and parks the message in the
  PendingQueue — correct for a real turn, which drains at its end, but a
  captured turn has no owner to drain it. Every message sent from the webchat
  sat in the queue until a force stop discarded it, with the UI showing the
  agent up the whole time. Two gaps closed: the message is now typed straight
  into the live tmux, and the capture hands any queued messages back when it
  releases the turn. Liveness is decided by the MITM proxy's WebSocket — it is
  up exactly while a container lives, so it proves there is a tmux to deliver
  into without consulting the turn bookkeeping that went stale in the first
  place.

- **A captured tmux turn was invisible until it ended.** The same sequence, seen
  from the other direction: the tmux worked for minutes — text, tool calls, tool
  results, every byte observed by the MITM proxy — and the webchat showed
  nothing, because the capture built its turn coordinator with *no callbacks*
  and persisted one lump when the turn finished. A PawFlow-driven turn passes
  four callbacks and streams each block through the agent loop; the capture had
  none, so it was silent by construction, and its tool calls were dropped
  entirely. It now passes the same `callback` and `block_callback`: text deltas
  publish as `token` events while they are written, and each completed block is
  persisted and published as it arrives. This is the rule the Antigravity
  observer already implements — its manual ingest streams out-of-band tmux
  activity by default and suspends only while PawFlow drives a turn — applied
  to Claude Code interactive: everything the proxy intercepts reaches the SSE
  listeners while it happens, whoever started the turn.

- **`apply_patch` could rewrite the wrong lines without saying so.** When `git
  apply` refused to parse a diff — which it does on hand-counted `@@` counts,
  even when the context and the edits are correct — its diagnostic was
  discarded and the patch fell through to a fallback parser that applied
  *positionally*. That parser never compared what it removed, so a hunk whose
  context existed nowhere was applied anyway to whatever sat at the stated
  offset; and it indexed the old-side `@@` number into a buffer the preceding
  hunks had already grown, so every hunk after the first landed off by the net
  line delta before it. It also wrote each file as it went, leaving earlier
  files rewritten when a later hunk was nonsense. The fallback now treats the
  `@@` number as a hint and locates each hunk by its context, refuses a hunk it
  cannot place (quoting git's reason when git supplied one), and writes nothing
  until every hunk in the patch has been placed. A patch with wrong `@@` counts
  or a bare `@@` now applies correctly instead of corrupting the target.

  A hunk with no context at all (a pure insertion, as `diff -U0` emits) has
  nothing to match, so it is checked against the header's own redundancy
  instead: the two counts must restate the hunk body, and the new-side start
  must restate the old-side start shifted by every hunk already applied. All
  three agreeing corroborates the position; any disagreement, or a bare `@@`
  carrying no arithmetic, is refused rather than placed on trust. Every hunk is
  now checked — by context where context exists, by arithmetic where it does not.

- **`apply_patch` reported success on a zero-context diff it had not applied.**
  `git apply` requires one line of context and otherwise skips the hunk *and
  still exits 0*, so a `diff -U0` patch came back applied over an untouched
  file. It is now invoked with `--unidiff-zero`, which relaxes only the hunks
  that carry no context — a patch that has context applies identically either
  way.

- **`Monitor` dropped output past `line_limit` without saying so.** A body that
  stops at the cap is indistinguishable from a command that had nothing more to
  say, so a caller reading a truncated result concluded the output simply ended
  there. The watcher script now reports the true line count and which branch it
  took, and a truncated result states the cut twice: `truncated=N` in the
  header, and a closing line giving how many lines were dropped, how many were
  produced, and which end was kept (`first` for raw output, `last` for the
  never-matched fallback). A hit list capped by `limit` is not reported as
  truncation — the header already carries `limit=`, so that cap was never
  silent. `line_limit` still never costs a match: the hit search greps the whole
  capture file, so a marker on line 400 is found under any `line_limit`.

## [1.0.0-beta.39] — 2026-07-28

### Fixed

- **`Monitor` hung instead of returning early, and worst on the patterns it
  advertises.** It piped the command through `grep --line-buffered | head -n N`,
  which cannot stop anything: a downstream stage exiting never ends a pipeline
  because the shell waits for every member, and `grep` only learns `head` is
  gone when it next writes. For a pattern matching once — `FAILED`,
  `listening on port`, its own examples — that second write never comes, so the
  command ran to completion and `Monitor` came back at its timeout. Measured on
  a 60-second command: a pattern matching once took the full timeout, the same
  pipeline with a pattern matching every line returned in two seconds. The more
  selective the pattern, the longer the hang. The command now runs in its own
  session with its output captured to a file, and a watcher kills the whole
  process group — children included — as soon as the pattern is satisfied or
  the deadline passes.
- **A `Monitor` timeout threw away everything the command had produced.** The
  bash layer's own timeout fired first and returned its error banner in place
  of the output, contradicting the documented "returns what was captured so
  far". The watcher now owns the deadline and the capture file survives it; a
  pattern that never matched returns the tail of the raw output rather than a
  blank.
- **`Monitor` never reported the command's exit code, and guessed its own
  reason from the output text** — a build logging the words "timed out" was
  reported as a Monitor timeout. The shell states `reason=` and `rc=` on their
  own line, and the header carries `exit_code=` when the command ended by
  itself. None of this was caught earlier because every existing test mocked
  the bash layer and asserted the command *string*: they passed against a tool
  that hung on every selective pattern. The suite now executes the script.
- **An RTK rewrite would have truncated a generated script to its last line.**
  The rewrite keeps only the final line of `rtk`'s output as the whole command,
  which is harmless for the one-liners it was built for and destructive for a
  multi-line script. Generated scripts opt out (`_skip_rtk`); `Monitor` uses it.

### Changed

- **Tool guidance: match processes by pidfile, not by command line.** `pgrep -f`
  and `pkill -f` also match the shell that runs them, because the pattern is in
  that shell's own command line by construction. Observed twice in one session:
  a wait loop on `pgrep -f 'pytest tests/'` matched itself and spun until
  killed, losing a completed test run, and a `pkill -f` killed its own shell
  mid-command. Stated in `bash`'s description and in the tool-usage block.

## [1.0.0-beta.38] — 2026-07-28

### Fixed

- **The header kept showing "Working: Command" with nothing running.** A
  context operation (`/compact`, `/clear`, `/rewind`) reports its own progress
  through `context_op` events, so its acknowledgement is marked
  `suppress_command_result` to keep the raw JSON from printing as a system
  message. The background dispatcher took that marker as permission to publish
  nothing at all — but the browser clears a pending UI action only when a
  result arrives, so the call stayed open and the server-side registry held it
  pending until its 600s TTL expired. The suppressed path now publishes a
  `command_result` carrying `{"suppressed": true}`: nothing renders, and the
  call completes with the operation.

## [1.0.0-beta.37] — 2026-07-28

### Fixed

- **Two coordinators could read one Claude Code session, splitting the turn
  between them.** `queue.Queue` hands each event to exactly one getter, so a
  capture coordinator (started by a manual tmux prompt, or by Claude Code
  injecting its own background-task notification) and the next webchat turn
  competed for the same stream. Text deltas arrived halved, producing answers
  that begin mid-sentence; `tool_use` blocks were severed from their
  `input_json_delta` chunks, leaving MCP wrappers un-unwrapped and rendered as
  a bare `use_tool`. Only a compact recovered, because killing the session
  mints a new token. Ownership is now arbitrated by epoch: a request claim
  always wins and happens before the drain, a capture claim refuses while a
  request coordinator polls, and an evicted capture discards its partial text
  instead of publishing a truncated message.
- **The webchat showed the agent idle while the tmux was visibly working.** A
  turn PawFlow did not send — a human typing in the tmux, or Claude Code
  resuming on a background-task notification — runs outside the streaming
  worker, so `_active_turns` stayed empty. PawFlow attaches to such a turn
  rather than restarting it (a second prompt would duplicate work already in
  flight) and now publishes the active-agent marker for its duration.
- **Killing a finished tool call cancelled a different, running one.** A
  targeted kill that finds nothing is ambiguous: the call may be running but
  not yet bound to its `tool_call` id, or it may simply be over. Only the
  first justifies widening to a whole-agent cancel. A stale-looking `edit`
  was killed and took the running `bash` with it; the broad fallback now
  requires an unbound in-flight request, and otherwise reports that there was
  nothing to kill.
- **A wrapper call whose streamed input was lost is recovered from the request
  body.** When the chunks carrying `tool_name` go missing the call is dropped
  entirely by the MCP completeness check, so nothing was persisted and the
  observed replay can supersede it without duplicating.
- **PawFlow's own CamelCase tools were unreachable through `use_tool`.** The
  MCP bridge lowercased any CamelCase tool name, to map Claude Code's native
  spellings onto PawFlow tools (`Read` → `read`). PawFlow registers CamelCase
  tools of its own — `Monitor`, `ScheduleWakeup`, `PushNotification`,
  `EnterPlanMode`/`ExitPlanMode` — and those are precisely the ones whose CC
  built-in equivalents are deliberately disallowed, so the intended fallback
  path was broken: `Monitor` answered `unknown tool 'monitor'` with no way
  through. The name is now sent as written and only lowered if the server
  replies that it does not know it, leaving the registry as the single
  authority. Consequence of the defect: agents fell back to polling a log file
  with `sleep N; tail`, which costs a turn per poll and reports on the sleep
  instead of the command.
- **Tool guidance now names the tools that make polling unnecessary.** It said
  "use `run_in_background` for long-running commands" and stopped there —
  never mentioning `Monitor` (blocks until exit or regex match, 10 min cap),
  `run_tests`, or `security_scan`, all of which exist. The `bash` tool now
  points at `Monitor` in its own description too, where an agent reaching for
  a shell actually reads.

### Added

- **Azure OpenAI and GitHub Copilot providers.** Both speak OpenAI
  chat-completions bodies, so they reuse the entire OpenAI path; only the
  envelope differs, and that is the whole of
  `core/llm_providers/openai_dialects.py`. Azure could not be reached through
  `base_url` alone: its key travels in an `api-key` header, it addresses a
  *deployment* rather than a model, and it requires an `api-version`. Copilot
  is a two-token provider — a device flow (no callback URL, no browser on the
  server) yields a GitHub token, saved as the service's `api_key`, which each
  session exchanges for a short-lived Copilot token, cached and renewed before
  expiry rather than at it. Ollama, LM Studio, OpenRouter, DeepSeek and the
  other OpenAI-compatible endpoints already worked through `openai` +
  `base_url` and are unchanged.

## [1.0.0-beta.36] — 2026-07-28

### Added

- **The server can now update itself** from the gear menu, completing the
  Update feature. A container cannot replace itself — `docker restart` comes
  back on the old image and `rm -f <self>` kills the process issuing the
  command — so the work goes to a short-lived detached container that has the
  Docker socket, survives the server's death, and drives
  `docker compose pull` + `up -d --build` from the project directory.
  That directory is **detected, not configured**: compose stamps it on every
  container it creates (`com.docker.compose.project.working_dir`, a host path),
  and `core/compose_deployment.py` finds this container's own id to read it.
  It is mounted into the updater at its own host path, because compose resolves
  `./data` and `build: .` against it and hands the daemon host paths — mounting
  it anywhere else would silently produce wrong bind mounts. A preflight proves
  the updater image runs, carries a working `docker compose`, and that the
  project directory is really there, before anything irreversible happens.
  Restarting kills every running agent turn: the dialog says so, and says how
  many, but does not refuse on the operator's behalf — it is the same cost as
  running `docker compose up -d` by hand.
- **Passive memory recall** (`core/passive_recall.py`). Memories close to what
  the user just said now surface on their own, without the agent deciding to
  call `recall` — which it can only do when it already suspects something
  relevant exists. The embedding and the search run in a daemon thread and the
  result is injected on the *next* turn, so a turn is never delayed and a slow
  or missing embedding provider degrades to "no passive memories" rather than
  to a stall. Hits below 0.4 similarity are dropped and anything already quoted
  in the static digest is skipped. `passive_recall_limit = 0` disables it.
- **Auto-poke** (`core/auto_poke.py`). The plan orchestrator only advances when
  the agent calls `update_plan`; a turn that ended without it left the step
  `in_progress` with nothing to wake the agent again, so the plan stalled until
  somebody noticed. Such a turn now gets handed back with a message naming the
  two acceptable exits — finish the step, or report the blocker. Bounded to two
  consecutive pokes per step, reset by any progress; never after an error, an
  interruption or a force stop; never when messages are already queued.
  `auto_poke_limit = 0` disables it.

### Fixed

- **The cognitive digests were invalidating the prompt cache on every turn
  that wrote to a store.** Memory, diary, knowledge-graph and project-structure
  digests sat in the system prompt, and they are rebuilt from live stores — so
  any `remember`, `diary_write`, `kg_add` or graph rebuild moved the cached
  prefix. Provider caching is prefix-based: that invalidates the system block,
  the tool definitions and every message behind them, i.e. the entire
  conversation is re-read at full price on the next turn. On API providers the
  digests now ride the same channel as the date/time — merged into the last
  user message, after all cache breakpoints. CLI providers keep them in the
  system prompt: their prompt goes through the cold-start bootstrap file and
  would otherwise be echoed back in the prompt handed to the CLI binary.
- Cache-break diagnostics were comparing conversations against each other. One
  `LLMConnection` service owns a single `LLMClient` shared by every
  conversation using it, and the detector kept one slot of state, so every
  switch between conversations looked like a cache break — noise that hid the
  real ones. State is now keyed per conversation and bounded.

### Added

- **Updates panel in the admin gear menu** (`core/update_manager.py`). It
  reports, per component, the installed version against the published one —
  server against the latest GitHub release, agent CLIs against the npm
  registry, relay images against the shipped catalog. The release tag
  (`1.0.0-beta.35`) and the packaged version (`1.0.0b35`) are compared under
  PEP 440, so they read as equal instead of flagging every install as stale
  forever. A component whose version cannot be resolved reports "unknown"
  rather than failing the dialog.
- The same panel rebuilds the **agent CLI tools image**. Antigravity installs
  from an unversioned script, so it has no version signal at all: forcing
  (`--no-cache`) is the only way to pick it up, and the versions actually
  installed are now stamped into the image at build time
  (`/opt/pawflow/cli_versions.json`) instead of being inferred.
- The panel also rebuilds the **relay images** and moves running relays onto
  them. The move goes through a new `ServerRelayManager.recreate()`, which
  replaces the container and nothing else: workspace directory, volumes,
  relay id, registered service and conversation bindings all survive.
  `destroy()` + `spawn()` would have deleted the volume and the workspace,
  i.e. the user's work. A failed respawn restores the previous metadata
  instead of dropping the relay from the store. The sweep is sequential and
  does not stop on a failure. Building alone changes nothing for running
  relays — a container keeps the image it started from until it is recreated.
  All of these actions require the `admin` role, refuse concurrent runs with
  HTTP 409, stream progress over SSE, and log who triggered them: `docker
  build` against the host socket is effectively root on the machine.
- **Conversation sharing is complete and reachable from the UI** (phases 4-7
  of `docs/CONVERSATION_SHARING_PLAN.md`). The sidebar splits into Mine /
  Invitations / Shared with me; an invite has explicit Accept and Decline
  and grants nothing until accepted; the owner gets a share dialog with
  inline role change and removal; a collaborator can leave from the context
  menu; and a user bubble written by somebody else now carries an author
  label — until sharing there was never more than one human in a
  conversation, so every user bubble looked alike. en/fr/es throughout.
- Channel bridges (Telegram et al.) and deployed flows honor collaborators:
  `authorize_conversation_target` resolves through the same
  `core.conversation_access` primitive the webchat actions use instead of
  keeping a second owner-equality check, and takes the access level the call
  site actually needs. The default is `write`, so a call site nobody
  reviewed denies rather than widens.
- A conversation whose owner's account is deleted is handed to the first
  accepted `write` collaborator who writes to it, moving its directory under
  the conversation lock. Without it, deleting an account silently orphaned
  every conversation shared out of it.

### Security

- **A message could be submitted into any conversation by id.** The
  submit path read `conversation_id` from the request body and never
  checked it against the authenticated principal — and nothing below that
  point is partitioned by requester: the agent context, the agent config and
  the CLI session state all load from the owner's directory using the id
  alone. Any logged-in user who knew or guessed an id could post into
  someone else's conversation and have the agent answer with its full
  context. This is the write-side twin of the SSE gap closed in beta.33 and
  predates sharing. A rejection returns the same 404 an unknown id gets.
  The check runs at the streaming ingress, before the user message is
  persisted and published to subscribers and before the preempt logic that
  cancels whatever agent is running on the conversation — a check placed
  after the background-thread boundary would have run after the write it
  exists to prevent.
- **Git history, archives and conversation files were reachable by id.**
  `conv_rollback`, `conv_delete_branch`, `conv_tag`, `conv_git_log`,
  `conv_export_pawflow` (a complete archive of the conversation) and
  `clear_store` (which deletes every FileStore file of a conversation)
  resolved the conversation by id with no access check. They are now gated
  by a single table, `_ACTION_ROLES`, with a test that fails if an action is
  added to those handlers without a row in it. Rollback and branch deletion
  are owner-only: they discard history for every participant.
- `loop_list` returned every user's scheduled loops when called without a
  conversation_id. It now requires one. `loop_stop` is keyed by loop rather
  than conversation, so it resolves the loop's conversation and requires
  write access on it — a loop spends the owner's budget on every tick.
- Server workspace and execution-relay lifecycle actions
  (`create/destroy_server_workspace`, `create/destroy_server_execution_relay`)
  were reachable by id: any logged-in user could destroy another user's
  server workspace. Now owner-only; the two status actions require read.
- **Conversation-scoped services were reachable by conversation id.** The
  service registry keys its conversation scope on the conversation alone —
  `_service_scope_id` drops the requester for `scope="conv"` — so every
  service_flow handler that reads `scope` from the request body acted on
  whichever conversation the request named. `get_service_detail` returned
  another user's service definition **including its config, which is where
  service credentials live**, and `update_service`, `delete_service`,
  `toggle_service`, `move_service_scope`, `service_install` and
  `service_uninstall` mutated it. A request that asks for conversation scope
  now requires write access on the conversation it names; global scope (the
  default) is untouched.

### Fixed

- Conversation-scoped agents, skills and MCPs were filed under whoever asked
  for them rather than under the conversation. Invisible while every
  requester was the owner; on a shared conversation a collaborator's turn
  resolved none of the conversation's own resources, so the agent it runs on
  would simply not be found.
- The `export` and `conv_export_claude_code` actions passed
  `store.load(conversation_id=...)`, a keyword `load()` does not accept —
  both had been raising `TypeError` instead of exporting.

## [1.0.0-beta.35] — 2026-07-27

### Fixed

- **The `edit` tool reported diffs that did not match what it wrote.** The diff
  was assembled from `old_string`/`new_string` *before* the write, so it
  described the intent rather than the result — and it was wrong three ways at
  once. A match starting or ending mid-line marked the WHOLE line removed and
  printed only the replacement fragment, so the untouched remainder of that
  line appeared deleted when it had never moved. Added rows were appended after
  the entire context window instead of sitting at the replacement point, which
  read as code jumping downwards. Added rows were numbered as if the new text
  had the same line count as the old one, and trailing context kept its
  pre-edit numbering. On top of that, `replace_all` announced N replacements
  while showing only the first. The file on disk was always correct — only the
  report lied — but it cost a verification read after edits that were fine.
  The diff is now derived from the before/after texts (`difflib`, grouped
  opcodes, ±3 lines), covers every changed region, and announces truncation
  instead of silently capping.

### Changed

- **Claude Code's native tool calls are no longer hidden from the transcript.**
  The proxy and the turn coordinator both dropped the provider's own
  bootstrap/discovery calls — `GetSchema`, `ToolSearch`, and the `Read` of
  `.pawflow_cci/initial_context.md` — along with their results. It was meant to
  spare the transcript some noise, but it made a turn that opens by reading its
  own context render an empty technical-details block, and left no way to tell
  a deliberately suppressed call from a lost one — precisely the question a
  transcript exists to answer. The `is_hidden_native_tool` predicate and the
  whole `hidden` plumbing behind it (the observer's hidden-id set, the four
  `not block.get("hidden")` guards) are gone rather than neutralized: what the
  agent did is what the transcript shows.

## [1.0.0-beta.34] — 2026-07-27

### Fixed

- **beta.33 broke the chat UI entirely** — the whole page was dead: history
  never rendered, every action hung "in progress" forever, and the browser
  console showed a 404 on `GET /api/agent/events?conversation_id=__ui__:tab-…`.
  The SSE authorization added in beta.33 assumed every `conversation_id` on
  that endpoint is a conversation. It is not: the chat UI opens a second
  stream per browser tab on `__ui__:<tab id>` and routes the `command_result`
  of *every* `action$()` call through it, deliberately decoupled from the
  conversation stream (which `resumeConv` closes and reopens around history
  rendering). That id has no owner and no row on disk, so `require_read` could
  only ever deny it — the gate 404'd the UI's own command bus. The channel is
  now recognized (`is_ui_bus_channel`) and exempted from the per-conversation
  check; it still requires an authenticated requester, and real conversations
  are gated exactly as before.
- A `token` SSE event published without a `msg_id` is now refused
  (`ValueError`) instead of producing an anonymous streaming bubble. Tokens
  accumulate into a bubble that is reconciled against the transcript by
  `msg_id`; with no id, only the turn-ending event could pair it with the
  persisted line, so losing that event made gap reconciliation render the
  stored message beside the bubble already on screen — the same answer twice.
  This closes the residual risk introduced with reconciliation in beta.33: no
  emitter could reach it (the single one stamps a uuid, and Claude Code
  publishes no tokens at all), but nothing enforced it either. A refused event
  costs the live preview only; the message still arrives persisted.

## [1.0.0-beta.33] — 2026-07-27

### Fixed

- Message pipeline audit — three more defects on the live channel, all of
  them invisible in the transcript (the stored conversation was never
  wrong; only the live view was):
  - **Lost messages.** The `done` handler registered every `msg_id` of the
    turn as "already displayed" to guard against replay duplicates. An id
    whose event never arrived — socket down during the gap — was recorded
    as displayed anyway, so `addMsg` refused its later delivery too and the
    message stayed invisible until a manual reload. Only ids that actually
    reached the DOM are marked now.
  - **Unrecoverable gaps.** Nothing healed what the server published while
    a socket was down: events accepted by a half-open writer are never
    buffered (`send()` returned true, so the bus counted them delivered),
    and the watchdog / health-timer reconnects pass `replay=false`, which
    skips the buffer on purpose. A reconnect that follows a real drop now
    re-reads the transcript tail and renders only what is missing —
    idempotent by `msg_id`, and each recovered message is inserted at its
    own server timestamp rather than appended at the end.
  - **Phantom message at the end of a turn.** The token handler appended to
    the stream buffer before creating its element; when `addMsg` refused
    (that `msg_id` was already on screen), the text stayed in a stream with
    no element, and neither reset site clears it — both are guarded on the
    element existing. `done` falls back to that buffer when nothing of the
    turn rendered, resurrecting the text as a duplicate bubble.
- Buffered SSE events were replayed regardless of age: `_BUFFER_TTL` was
  applied only when appending to a buffer, and expired a conversation's
  buffer on its *newest* entry — so a buffer that kept receiving events
  never shed the old ones. A subscriber could be handed an event minutes
  old and render it as if it had just happened. The TTL is now enforced at
  delivery, and stale rows are pruned as they are found.

- The first message of a turn jumped below the final answer once the turn
  ended, and a page reload put it back where it belonged. Nothing was
  re-sent and no event was replayed: the `done` handler looks up the DOM
  element the turn's final metadata belongs to by scanning `all_msg_ids`,
  but it scanned oldest-first and stopped on the first match. On a CLI
  provider a turn persists several messages (a narration, tool calls, then
  the answer), so it matched the turn's OPENING line — stamped the whole
  turn's tokens/cost/duration onto it, then re-sorted it to the `done`
  timestamp, physically moving it to the bottom. The scan now runs
  newest-first, and the re-sort is restricted to the live streaming
  placeholder, the only element positioned with the browser's clock rather
  than a server timestamp of its own.

- Images sent to a vision model while the agent was already running (the
  preempt path) were never downscaled: `_build_user_content` resizes on
  ingestion, but preempt hands the raw `file_id` to the provider, and the
  three sites that materialize a file on disk (Claude Code interactive,
  antigravity, Codex) wrote the original bytes — so the agent opening the
  file itself hit the provider's "exceeds 2000x2000" rejection. New
  `core.image_resize.write_vision_image()` downscales and names the file
  after the encoding actually written (a re-encoded PNG becomes `.jpg`).
- Mobile chat UI: the top header was pushed off-screen and technical text
  was unreadably small. `height: 100vh` on `body`/`.sidebar` is the
  URL-bar-hidden height, i.e. taller than the real viewport, and with
  `overflow: hidden` the overflow was unrecoverable. Switched to `100dvh`
  (with a `100vh` fallback) and added a mobile type scale (+1–2px per
  level, full-width bubbles, `.tc-output` 11 → 12px). The composer is now
  16px, which also stops iOS auto-zoom on focus.
- A tool call interrupted mid-flight stayed "pending" forever (spinner +
  →BG/✘ buttons, no result): `interrupting` was the only turn-ending SSE
  path that did not finalize in-flight tool calls, and since the turn
  keeps running, no other finalizer fired either. It now finalizes them,
  and synthesized results (`[Interrupted]`, `[Stopped]`, `[result not
  delivered]`) are marked as placeholders so a real result arriving late
  replaces them — never the reverse.

### Security

- The live SSE stream (`GET /api/agent/events`) never checked whether the
  requester may see the conversation it streams: `validate_auth` upstream only
  proves *who* is asking, and the task read `conversation_id` straight from the
  query string. Any logged-in user who knew or guessed a `conversation_id`
  could subscribe to someone else's conversation. `agentSSEStream` now
  resolves read access before subscribing and answers a rejection with the same
  404 an unknown `conversation_id` gets, so it cannot be used to probe which
  conversations exist. A request with no trusted principal is rejected too.
- The conversation actions that resolve a conversation by id alone were open
  to any logged-in user who knew that id: `set_conv_title` renamed it,
  `conv_encrypt_*` / `relay_workspace_*` could enable, lock or disable its
  encryption, and `poll` leaked its message count. Access to the owner's
  directory was never the barrier it looked like — only the actions that pass
  `user_id` down to storage (`load_history`, `search_messages`,
  `delete_conversation`) were partitioned by path. Every action in
  `_conv_core.py` that names a conversation now resolves
  `require_read`/`require_write`/`require_owner` first (phase 3 below) and
  answers a rejection with the unknown-conversation 404.

### Added

- Conversation sharing, phase 3 — sharing is now reachable.
  `tasks/ai/actions/_conv_sharing.py` adds `share_conversation`,
  `respond_to_share_invite`, `list_collaborators`,
  `update_collaborator_role`, `kick_collaborator`, `leave_conversation` and
  `list_shared_conversations`. Inviting is owner-only and two-sided (the owner
  can only create a `pending` row; access starts when the invitee accepts),
  kicking keeps the row for the audit trail, and every rejection is the same
  404 an unknown `conversation_id` gets. The `_conv_core.py` handlers now
  address storage with the resolved *owner's* id, so an accepted collaborator
  reads and writes the one shared conversation rather than a copy — encryption
  management and deletion stay owner-only. No frontend yet (phase 7).

- Conversation sharing, phase 1 (`core/conversation_access.py`): the
  authorization primitive every call site will consult instead of its own
  owner-equality check — `resolve_conversation_access` / `require_read` /
  `require_write` / `require_owner`, the collaborators ACL stored via the
  existing `extra` mechanism (no schema change), and a per-user reverse index
  of conversations shared with them. `ConversationStore` gains the read-only
  `resolve_owner()` and `shared_index_path()`. Its only consumer so far is the
  SSE fix above — sharing itself is not reachable yet, there is no action to
  invite anyone; see `docs/CONVERSATION_SHARING_PLAN.md`.

## [1.0.0-beta.32] — 2026-07-27

### Fixed

- Action-menu (`+`) dropdown could be cut off with no way to scroll on
  short/narrow viewports (`position:absolute`, no height limit). Now
  `position:fixed`, clamped to the viewport, scrollable, and positioned/
  flipped in JS relative to the button instead of a static corner.
- Message/thinking-block ordering could land new content above older,
  correctly-timestamped history whenever the browser and server clocks
  disagreed (`_insertMessageChronologically` was comparing a client
  `Date.now()` fallback against real server timestamps). Only genuine
  server timestamps are now compared against each other; content with no
  real timestamp always appends at the true end. A buffered `error_event`
  replayed after a client reconnects now renders at its true original
  time instead of whenever the replay happened to land.
- OAuth refresh race: `_refresh_oauth_token_coordinated` released its
  per-slot lock before the caller persisted the rotated tokens, letting a
  concurrent session read the pool in that gap, miss the rotation, and
  re-POST an already-consumed refresh token — a real `invalid_grant`
  rejection that dropped a perfectly good credential. Persistence now
  happens inside the lock.
- Telegram (and any other `AgentResultWaiter`-based bridge) never
  surfaced fatal LLM errors (credential expiry, session lost, budget
  exceeded): `on_fatal_error` published `error_event` without
  `turn_id`/`request_msg_id`, so the waiter couldn't correlate it back to
  the pending request and callers silently got the turn's empty response
  instead of the real error text.
- A fatal-error's synthetic assistant message was marked `is_error=True`
  but not `display_only=True`, so it could resurface as fake prior
  assistant content in the LLM context on a later turn. Now excluded from
  context (still visible in the transcript) via the same mechanism used
  for `sub_agent_trace`/delegate nudges.
- Mobile-responsive chat UI: `.header`/`.header .actions` now
  `flex-wrap` under 768px instead of squeezing/overflowing; `.sidebar`
  becomes a `position:fixed` overlay under 768px instead of squeezing
  `.main`; ~20 fixed-pixel-width dialog panels across the chat UI now
  clamp to the viewport instead of overflowing horizontally on a phone.

## [1.0.0-beta.31] — 2026-07-27

### Added

- New PFP installable object type `mcp_server`: installs directly as a
  ready-to-use `mcp` resource with no manual reconnection step after
  install. Structural validation (an `http` server needs `url`, a
  `stdio` server needs `command`) and risk classification (high for
  `stdio`, medium for `http`-only), mirroring the existing
  `tool`/`service_provider` and `service_definition` object types.
- Chat UI: a right-side sliding panel (`task_tabs.js`) showing a single
  task's messages, opened from the Active Agents panel or the inline
  task-block header. `addMsg()`/`_getTaskBlock()` tag rendered elements
  with `dataset.taskId`; the panel clones the matching top-level nodes
  and stays live via a `MutationObserver`, with no re-fetch or parallel
  render path.

## [1.0.0-beta.30] — 2026-07-23

### Added

- `manageCalendar` task (`tasks/io/manage_calendar.py`): list/create/update/
  delete calendar events. Two providers: Google Calendar API v3 with the same
  OAuth2 refresh-token flow as `sendEmail` (client_id/client_secret/
  refresh_token), or generic CalDAV (Nextcloud, Radicale, iCloud, most
  self-hosted servers) over HTTP Basic auth using PUT/DELETE of iCalendar
  (.ics) resources and a calendar-query REPORT for listing.
- New PFP installable object type `web_app` (`webapp.v1`): a standalone
  page (html/js/css) served at its own authenticated route
  `/apps/<package>/<name>/`, separate from the chat page's `ui_extension`
  slot/hook contract — `ui_extension` never ships `.html` and never gets
  its own URL, `web_app` does both. New task `servePfpWebAppAssets`
  (`tasks/io/serve_pfp_webapp_assets.py`), wired into the default
  `pawflow_agent` flow behind the same session auth gate as `/chat`. The
  Packages section of the Resources sidebar shows a ↗ link to any
  installed package's web_app pages. See `docs/PFP_DEVELOPER_GUIDE.md`
  ("Standalone Pages") for the manifest shape and trust model.

## [1.0.0-beta.29] — 2026-07-20

### Added

- Spend budgets (`core/budget_store.py`): cumulative daily/monthly caps
  scoped to a user, conversation, agent, LLM service, or globally, with a
  `warn` (notify only) or `block` (refuse the next turn on that scope)
  policy. Enforcement checks period-to-date REAL spend from the usage
  ledger once per external agent turn (subscription/virtual cost never
  counts); notifications fire at 50/80/100% of the limit into the
  triggering conversation, deduplicated per period. Managed via a new
  "Budgets" section on the dashboard (admin-only create/delete, progress
  bars for everyone budgets apply to) or `budget_list`/`budget_create`/
  `budget_update`/`budget_delete` actions. Distinct from the existing
  per-turn `max_budget_usd` LLM-service safety cap.
- Global "Usage & Costs" dashboard (header action menu): KPI cards
  (today/7d/30d, tokens, cache-hit rate, 30-day projection), a stacked
  daily bar chart (canvas, no external charting dependency) stackable by
  LLM service / agent / model / channel, and top-10 conversations/agents
  by cost. Bars fall back to tokens when the window has no priced usage.
  Admins get an "All users" toggle. Backed by a new bundled
  `usage_dashboard` action on the usage ledger.
- `subscription` flag on `llmConnection`: usage from a flat-rate service
  (Claude Code / Codex / Gemini subscription login) is recorded as
  `virtual_cost_usd` in the usage ledger instead of `cost_usd` — real spend
  stays $0 and budgets never count it, while `cost_per_1m_*` rates still
  drive a "what this would have cost via API" figure surfaced everywhere
  the ledger is read (badge, panel, summary/timeseries/top/export).
- Persistent usage/cost ledger (`core/usage_ledger.py`, SQLite at
  `data/system/usage.db`): every LLM call is recorded as ONE event with
  full dimensions (user, conversation, agent, llm_service, model,
  provider, channel) and the cost FROZEN at the service rates in effect
  at call time. Replaces TokenTracker (JSON aggregates, no conversation
  dimension) and CostTracker (in-memory, lost on restart); the legacy
  `token_usage.json` is imported once as synthetic `migrated` events.
  Newly attributed traffic that was previously invisible: sub-agent runs
  (`delegate`/`flash_delegate`, channel `subagent`, priced from the
  sub-agent's own service), realtime LiveKit sessions (structured token
  metrics from the worker, channel `realtime`), aggregator advisors, and
  internal calls such as title generation (channel `system`).
- Live conversation cost gauge in the webchat header: a `usage.updated`
  SSE event is published after every turn (task sub-conversations roll up
  to the parent), the badge shows the conversation's cumulative cost and
  opens a breakdown panel — totals, by agent / channel / model with bars,
  and the most recent turns. Backed by a new `usage_conversation` action
  and conversation-prefix queries on the ledger.
- Usage query actions on the agent-loop API (`usage_summary`,
  `usage_timeseries` with hour/day/month buckets and one group-by
  dimension, `usage_top`, `usage_export` JSON/CSV) with period and
  dimension filters — non-admins scoped to their own user, admins can
  query any/all users. Foundation for the upcoming usage dashboards.
  See `docs/usage_tracking.md`.

## [1.0.0-beta.28] — 2026-07-19

### Fixed

- Corrected the spelling of the default first-run Private Gateway bootstrap key
  (`roy betty` → `roy batty`) everywhere it appears: code default
  (`DEFAULT_BOOTSTRAP_GATEWAY_KEY`), install scripts, installer flow, docs,
  README, website, and tests.

## [1.0.0-beta.27] — 2026-07-19

### Added

- Native Claude Code plugin support (`claude-code` / `claude-code-interactive`):
  new `claude_plugins` (`plugin@marketplace` ids) and `claude_marketplaces`
  (`name=owner/repo` or `name=<git-url>`) parameters on `llmConnection` —
  merged as `enabledPlugins` / `extraKnownMarketplaces` into the session's
  `.claude/settings.json` (all other keys preserved, CCI hook settings
  included); Claude Code auto-installs them on session start, and removed
  entries are cleared on the next session.
- Native Codex plugin support (`codex-app-server`): a new `codex_plugins`
  parameter on `llmConnection` (comma-separated names, optional
  `name@marketplace`, default marketplace `openai-curated`) emits
  `[plugins."<name>@<marketplace>"]` entries in the session's generated
  `~/.codex/config.toml`, enabling OpenAI's curated plugins (Linear,
  GitHub, Gmail, Calendar, ...) in OAuth-mode Codex sessions. The
  generated config.toml is now a managed section between markers —
  content codex or the user writes outside it (e.g. `codex plugin
  install` state in the persistent session slot) survives regeneration.

## [1.0.0-beta.26] — 2026-07-17

### Added

- Background delegate results are now spoken into live voice sessions:
  when a `flash_delegate`/`delegate` sub-agent finishes while its caller
  has an active realtime LiveKit session, the result is injected into
  the session as an out-of-band context message (voice-friendly wording)
  on top of the normal text-channel delivery.
- `consult_agent` tool (voice-front delegation): one-shot call to the
  conversation agent's own model — resolved system prompt + configured
  `llm_service`, bounded conversation context, answer returned as the
  tool result. Approval-exempt (the delegate gets no tools). With
  `tool_profile=consult_agent` on a realtime service, the realtime model
  becomes a thin spoken interface that routes substantial work to the
  agent's brain; long answers are spoken when they land via the detached
  tool path.

- Managed realtime stack (zero-config LiveKit): leaving `livekit_url`
  empty on a `realtimeVoiceConnection` service now makes PawFlow
  provision and supervise the LiveKit stack itself through the Docker
  socket — `pawflow-livekit` (generated API credentials, never
  devkey/secret) and `pawflow-livekit-worker` (dependency-only image
  built locally once, worker code bind-mounted from the server install).
  The worker deployment secret is generated and persisted encrypted;
  browsers connect same-origin through the new `/livekit` signal
  WebSocket proxy (works on HTTPS pages, no manual docker-compose or env
  variables). External LiveKit servers remain supported by setting
  `livekit_url` + API key/secret.

- Realtime LiveKit Gemini video bench (`spikes/livekit/bench/driver3.py`):
  synthetic camera track (red square) plus a spoken color question against a
  real `gemini` Live session — user/agent transcripts, correct color answer,
  and non-silent agent voice all asserted. Validates the native video-input
  path end to end.

## [1.0.0-beta.25] — 2026-07-17

### Added

- Realtime LiveKit local-pipeline bench (`spikes/livekit/bench/`): minimal
  OpenAI-compatible local STT (faster-whisper) and TTS (piper) servers plus a
  `BENCH_PROVIDER=local_pipeline` mode of the fake control plane — the full
  zero-cloud-audio loop (Silero VAD + turn-detector + local STT/TTS + text
  LLM + tool round-trip) validated end-to-end with real speech.

### Fixed

- Realtime LiveKit worker: `local_pipeline` TTS model default changed from
  `kokoro` to `tts-1` — the OpenAI TTS plugin picks its wire format from the
  model name, and any non-`tts-1` name selects SSE streaming, which
  kokoro-fastapi/speaches-style local servers do not implement (the session
  then produces no agent audio at all).

## [1.0.0-beta.24] — 2026-07-17

### Added

- Realtime LiveKit P0 spike (`docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`):
  new optional dependency group `pawflow[realtime-livekit]` guarded by
  `services/livekit_deps.py` (clear setup error when absent), docker-compose
  `realtime` profile (self-hosted LiveKit dev server + `livekit-worker`
  sidecar built from `docker/livekit-worker/Dockerfile`), and spike scripts
  under `spikes/livekit/`: OpenAI Realtime voice hello-world, Gemini Live
  video hello-world with a synthetic-frame publisher, and the worker-control
  WebSocket prototype (`control_protocol.py`) with a fake tool-call
  round-trip — protocol covered by CI tests
  (`tests/test_livekit_spike_control.py`), no live provider calls. Also:
  local pipeline spike (`spike_local_pipeline.py`, the OpenLive-shaped
  zero-cloud-audio cascade — Silero VAD + turn-detector + local
  OpenAI-compatible STT/TTS + any text LLM) and `SPIKE_VIDEO=1` on the
  OpenAI spike (gpt-realtime image-input frame path).
- Realtime LiveKit P1 (service + session API): `realtimeVoiceConnection`
  gains `engine: livekit` with full config validation and a compatibility
  loader mapping legacy configs (`protocol`→`provider`,
  `vad`→`turn_detection`); scoped JWT tokens (browser room token with
  minimum grants and 15-min-capped TTL, agent room token, PawFlow-signed
  worker-control token); session registry with one active session per
  conversation and force-stop integration; `POST
  /api/realtime/livekit/start`/`stop` endpoints and the
  `/ws/realtime-worker/{session_id}` control WebSocket speaking the
  promoted `services/_realtime_worker_protocol.py` contract; `realtime.*`
  events on the conversation event bus. 40 new unit tests
  (`tests/test_livekit_engine.py`); docs in `services.md` and
  `security_model.md`.
- Realtime LiveKit P2 (worker MVP + tools + transcripts): new sidecar
  worker package `pawflow_livekit_worker/` (automatic LiveKit dispatch,
  PawFlow bootstrap fetch, provider `AgentSession` for
  openai/gemini/local_pipeline, proxied function tools, session cap);
  `POST /api/realtime/livekit/worker/bootstrap` endpoint guarded by the
  `PAWFLOW_REALTIME_WORKER_SECRET` deployment secret, resolving provider
  credentials server-side (never to the browser); worker tool calls now
  run through the existing `RealtimeToolBridge` (silent approval, long
  tools detach to `context` injection); final transcripts persist as
  normal conversation messages. 13 new tests
  (`tests/test_livekit_worker_p2.py`).
- Realtime LiveKit P3 (webchat live panel): the conversation mic button now
  routes `engine: livekit` services through WebRTC — vendored
  `livekit-client` 2.20.1 served at `/api/realtime/livekit/sdk.js`
  (lazy-loaded), new `conversation_livekit.js` reusing the voice overlay
  with camera/screen-share controls gated by `video_input`, live
  captions/state/tool activity from `realtime.*` SSE events, and a
  LiveKit/provider badge in the voice settings panel. Legacy-engine
  services keep the PCM bridge until the P5 retirement window. 8 new tests
  (`tests/test_livekit_ui.py`).
- Realtime LiveKit P4/P6/P7 config: `video_fps_active`/`video_fps_idle`
  frame-sampling service keys (worker applies them via
  `VoiceActivityVideoSampler`), worker provider mapping for `azure_openai`
  (OpenAI plugin Azure mode), `xai` (api.x.ai OpenAI-realtime endpoint) and
  `aws_nova` (guarded `livekit-plugins-aws` import with a clear install
  error), and `local_stt_url`/`local_stt_model`/`local_tts_url`/
  `local_tts_model`/`local_tts_voice` service keys so the zero-cloud-audio
  local pipeline is configured per service instead of per worker env.
- CUA screen mode phase 2 (AX-first addressing, `docs/CUA_MODE_PLAN.md`):
  new `screen` tool actions `windows` (window list), `window_state`
  (accessibility-element tree + grounding screenshot) and `status` (backend
  health), and `click`/`double_click`/`type` now accept `element_index` with
  `pid`/`window_id` to act on an element by AX identity — works on
  backgrounded/minimized windows, no coordinates or screen revision needed
  (a stale element yields a structured driver error instead of a blind
  click). The relay container path (`tools/fs_screen.py`) now routes through
  cua-driver too when `PAWFLOW_SCREEN_MODE=cua` — AT-SPI element trees on
  the Xvfb desktop with zero pointer contention with VNC users; the default
  xdotool backend is unchanged. Documented in `docs/desktop_vnc.md`.
- Desktop-capable relay images now bundle `cua-driver`: install step in the
  `desktop.runtime` feature of `config/relay_image_catalog.json` (relay image
  version `2026.07.16`) and in `docker/relay-dev/Dockerfile`, ready for CUA
  screen mode inside relay desktops (container dispatch lands with phase 2).
- Realtime multimodal plan: added a local pipeline profile to cascade mode
  (`docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`) — LiveKit local VAD/STT/TTS
  plugins + turn detector for full-duplex, zero-cloud-audio voice with any
  text `llmConnection`, composing with the vision fallback for video frames.
- CUA screen mode (phase 1 of `docs/CUA_MODE_PLAN.md`): with
  `PAWFLOW_SCREEN_MODE=cua`, relay-host `screen_*` actions route through
  [cua-driver](https://github.com/trycua/cua) desktop-scope tools (CLI form,
  per-action subprocess) for background computer use — the real cursor never
  moves and focus is not stolen. Per-agent overlay-cursor sessions
  (`PAWFLOW_CUA_SESSION`), pre-click screen guard unchanged, structured
  refusals surfaced verbatim (no silent foreground fallback), and a new
  `screen_status` action exposing the driver health report. Default backend
  is unchanged.

- Skill learning loop (P1–P3 of `docs/LEARNING_LOOP_PLAN.md`):
  - Agents now receive a `## Skill loop` system-prompt block instructing them
    to crystallize novel multi-step procedures into skills via
    `manage_resource` and to update skills that proved wrong during use.
  - Post-compaction extraction also asks the summarizer whether the summary
    contains a reusable procedure not covered by an existing skill; approved
    drafts are stored as conversation-scoped memories tagged `skill-draft`
    (never auto-installed).
  - `load_skill` records per-skill usage statistics
    (`data/runtime/skill_stats.json`), appends a self-improvement footer, and
    suggests promoting a conversation-scoped skill to user scope after
    repeated loads.
  - New `skillCurator` flow task: flags never-loaded and stale skills, runs an
    optional LLM review (keep/archive/merge), and emits a JSON report.
    Report-only — actions are applied by the user after review.

### Fixed

- Secrets added mid-conversation now reach tool executions immediately: the
  tool-relay secrets env/redaction caches stored a config fingerprint but
  never compared it on cache hits, so a newly added secret was neither
  injected into `$VAR` env nor redacted from output until a server restart.
  The fingerprint is now checked once per execution (shared between the env
  and redaction caches — hot path unchanged). Regression tests in
  `tests/test_tool_relay_secret_cache.py`.

## [1.0.0-beta.23] — 2026-07-13

### Fixed

- Webchat history pagination now uses the oldest rendered message as a cursor,
  so **Load more** returns the immediately adjacent older messages even when
  live-render trimming or technical message groups made the numeric offset drift.
- `LLMConnectionService` now reads its service id from the config injected by
  the registry instead of a never-set instance attribute, restoring per-service
  API-key-pool stickiness (`llm_api_key_idx:<id>` no longer collides across
  services) and the vision-fallback self-reference guard.
- The LLM aggregator injects advisor reports into the last user message instead
  of appending a trailing system message, which Anthropic-API connections
  treated as a replacement for the agent's system prompt and CLI session
  serialization dropped entirely. The Anthropic message builder now also
  concatenates multiple system messages instead of keeping only the last one.
- Read-only advisor conversations register their permission mode in an
  in-process `ToolApprovalGate` registry: `set_extra` silently no-ops for
  ephemeral (never-persisted) conversations, so CLI-provider advisors reaching
  tools through the MCP relay were not actually restricted to the fail-closed
  read-only allowlist. The registration is removed when the run finishes.
- CLI tool-result truncation now exempts results carrying inline
  `__image_data__:` payloads (same rule as the ToolRegistry cap), so oversized
  screenshots are no longer cut mid-base64.

## [1.0.0-beta.22] — 2026-07-13

### Added

- Added the `llmAggregator` service. It consults multiple direct
  `llmConnection` advisors in parallel, injects their internal plans into a
  separate final LLM, and exposes the composite anywhere an agent accepts an
  LLM-capable service.
- Added `best_effort` and `fail_fast` advisor failure policies, configurable
  concurrency and iteration limits, per-turn report reuse, coordinated aborts,
  and separate advisor usage/cost accounting that does not inflate the main
  context gauge.
- Added a complete multi-LLM setup guide, README example, website how-to, and
  documentation-hub links.

### Security

- Advisor sub-contexts are silent, isolated, and ephemeral. Read-only
  enforcement is enabled by default with a fail-closed tool allowlist, including
  CLI-backed providers through their scoped MCP context; interactive and
  state-mutating tools remain available only to the final LLM under the normal
  conversation approval policy.

## [1.0.0-beta.21] — 2026-07-13

### Fixed

- Telegram slash commands no longer expose raw command-result JSON or internal
  `client_only` envelopes. Supported local commands execute in the Telegram
  client, unavailable UI operations return actionable guidance, and unknown or
  failed commands return an explicit human-readable error.
- `/new` now opens Telegram's native conversation wizard, and
  `/conversations` is wired as a functional alias of `/conv` in Telegram,
  PawCode, webchat, and the VS Code extension.
- PawCode now treats `/conv list` as a conversation listing instead of trying
  to select a conversation whose ID is `list`.

## [1.0.0-beta.20] — 2026-07-13

### Added

- Added end-to-end delegated-vision documentation, including a GLM 5.2 +
  Gemma 4 Cloud configuration guide, provider requirements, caching behavior,
  cost and security boundaries, and website discovery links.

### Changed

- `screen(screenshot)` and `see(screen)` now return an opaque screen revision.
  `click` and `double_click` require that revision and may accept a target
  bounding box so actions are tied to the exact image used for coordinate
  selection.

### Security

- Desktop clicks now compare a bounded reference crop with a fresh local relay
  capture immediately before any mouse input. A changed target returns
  `STALE_SCREEN` without moving or clicking; an unchanged target proceeds
  without another LLM or vision request. Revisions are scoped to the user,
  conversation, relay, and local or Docker display route.

## [1.0.0-beta.19] — 2026-07-13

### Fixed

- Vision fallback now works end-to-end in the agent loop. The
  `LLMConnectionService._maybe_apply_vision_fallback` method referenced
  `self._service_id`, which does not exist on `BaseService` instances — the
  attribute lives on `ServiceDefinition` (the registry wrapper). The resulting
  `AttributeError` was silently caught at DEBUG level, making the fallback
  appear to run but always return messages unchanged. Both call sites now use
  `getattr(self, "_service_id", "")` so the fallback proceeds when
  `supports_vision=false` and `vision_llm_service` is configured. Image
  attachments and `see`/`read` tool results are now described by the vision
  service before reaching a non-vision LLM.
- Escalated vision fallback exception logging from DEBUG to WARNING so silent
  failures are immediately visible in server logs.
- Added early-return diagnostic logging in `apply_vision_fallback` to identify
  which guard (recursion, no images, self-reference, unresolved service) skips
  the description pass.

## [1.0.0-beta.18] — 2026-07-13

### Fixed

- Corrected cold-session context accounting for Codex app-server, Gemini ACP,
  Claude Code, Claude Code interactive, and Antigravity interactive. The
  serialized PawFlow context is replaced at the first native bootstrap read
  boundary, so only content actually loaded into the provider context is
  counted and chunked reads remain additive.
- Invalidated stale context-usage snapshots created by the previous accounting
  formula, preventing an incorrect near-full gauge from surviving a restart.
- Suppressed Bandit's B105 false positive for the
  `conv_encrypt_passwd` action identifier without changing the public command
  contract.

## [1.0.0-beta.17] — 2026-07-13

### Changed

- Unified domain slash-command parsing and human-readable result rendering on
  the server for webchat, Telegram, PawCode, and the VS Code extension. Client
  implementations now retain only transport-specific UI operations.
- Reserved `/audio` for server-side audio generation across clients and
  renamed the webchat relay-stream control to `/relay-audio`.

### Fixed

- Restored broken or mismatched routing for conversation, agent, task, flow,
  memory, tool, media, debug, loop, hook, and resource commands.
- Preserved complete multi-word text in `/memory add` and `/memory search`,
  and aligned PawCode `/msg` and `/btw` target parsing with the shared
  syntax, accepting both `agent` and `@agent`.
- Added consistent `display` output without suppressing structured client
  state updates such as conversation switching after `/fork`.
- Extended vision-fallback diagnostics to report early-return reasons.

## [1.0.0-beta.16] — 2026-07-13

### Fixed

- Pre-cache tiktoken `cl100k_base` BPE file at Docker build time in a persistent
  path (`/app/data/tiktoken_cache`) so token counting never needs network at
  runtime. The entrypoint seeds the cache into the `/app/data` bind mount on
  first boot, preventing a `/tmp` cache wipe + network failure from permanently
  degrading the context gauge.
- Added diagnostic logging to the vision fallback path in the agent loop.
  Both `_alc_apply_vision_fallback` and `LLMConnectionService._maybe_apply_vision_fallback`
  now log the `supports_vision`, `vision_llm_service`, and image detection
  results so a misconfigured fallback is immediately visible in server logs.

## [1.0.0-beta.15] — 2026-07-13

### Fixed

- Context gauge no longer inflates artificially for CLI providers (Codex,
  Claude Code, Gemini, CCI, Antigravity). Tool results arriving through the
  live `block_callback` and `turn_callback` paths were persisted without the
  `tool_result_max_chars` cap (default 50K chars) that `ToolRegistry.execute`
  applies to PawFlow MCP tools. Native Codex tool outputs (e.g. `cat
  initial_context.md`) were stored at full size, duplicating the serialized
  context and causing the gauge to jump from 19K to 80K+ tokens after a single
  cold-start file read. Both callback paths now truncate to the configured
  limit before persistence.
- `tiktoken` encoding failures are no longer permanent. A transient network or
  cache issue at startup previously set `_encoding_failed = True` forever,
  making every subsequent token count use the approximate fallback
  `(bytes + 3) // 4`, which overestimates by 1.1–2x depending on content and
  inflates the context gauge. The flag is now a monotonic timestamp with a
  5-minute retry window, so tiktoken is re-attempted and the precise
  `cl100k_base` tokenizer is used as soon as it becomes available.

## [1.0.0-beta.14] — 2026-07-12

### Fixed

- Multimodal prompt-token fallbacks now use the shared message counter instead
  of measuring Python's serialized image blocks. Image transport bytes no
  longer inflate context usage when a provider omits usage metadata; only text
  content and message overhead are estimated across API and CLI providers.
- Delegated result-shape resolution now supports minimal tool registries that
  expose `list_tools()` without `get()`, restoring targeted cancellation for
  direct API-provider tool execution.
- The Claude Code interactive no-proxy timeout regression test now patches the
  coordinator's owning module, preventing the test from waiting for the
  production 300-second timeout.

## [1.0.0-beta.13] — 2026-07-12

### Fixed

- Image-producing tools invoked through `use_tool` now preserve oversized
  image payloads across the registry safety cap, agent multimodal conversion,
  and MCP relay serialization. The effective delegated handler is resolved
  through aliases and nested wrappers while retaining the trusted
  `_returns_images` gate, so ordinary text containing an image marker remains
  capped.

## [1.0.0-beta.12] — 2026-07-11

### Added

- Pocket TTS (`pocketTTS`) local text-to-speech service for on-device voice
  generation without external API dependencies.

### Fixed

- Vision fallback now triggers in the agent loop when the active LLM lacks
  native vision support but has a `vision_llm_service` configured. Previously
  the fallback only ran through `LLMConnectionService.complete[_stream]`,
  which the agent loop bypasses by calling `LLMClient` directly — so image
  attachments and `see`/`read` tool results were never described for
  non-vision models. The fix delegates to the resolved service's existing
  `_maybe_apply_vision_fallback` from both the main LLM call and the
  interrupt-handling path.
- Tool-result image materialization (`_materialize_tool_result_images`) now
  logs the exception when FileStore storage fails instead of silently
  returning `[image omitted: failed to store image result]`.

## [1.0.0-beta.11] — 2026-07-10

### Fixed

- Claude Code interactive and Anthropic streaming now buffer indexed Anthropic
  content blocks independently, so interleaved `thinking` and `text` blocks no
  longer flush out of order or split visible words across Telegram messages.
- Telegram conversation forwarding now treats `thinking_delta` as a transient
  preview until the durable `thinking_content` arrives, preventing broken
  fragments such as partial reasoning sentences from being posted before tool
  calls.

## [1.0.0-beta.10] — 2026-07-10

### Added

- Native Tripo3D (`tripo3DGeneration`) and Meshy AI (`meshy3DGeneration`)
  services against the vendor APIs: text-to-3D (Meshy preview + refine
  workflow), image-to-3D, rigging, animation (Meshy action ids, Tripo
  presets incl. quadruped/hexapod/serpentine variants), retexture, and
  Tripo convert/stylize. New agent tools `rig_3d_model`,
  `animate_3d_model` and `retexture_3d_model`; `generate_3d` now surfaces
  the vendor `task_id` for chaining. See `docs/tripo_meshy.md`.
- Vision fallback for non-vision LLMs: an `llmConnection` with
  `supports_vision: false` can name a `vision_llm_service` that describes
  incoming images (OCR, UI elements with coordinates) before the messages
  reach the non-vision model, with memory + disk caching by image hash.
  `supports_vision` is now configurable for all providers, including the
  CLI ones whose `base_url` may point at a non-vision model.
- Ollama cloud free-tier presets: live model listing from
  `https://ollama.com/v1/models` in the service panel and install wizard,
  plus documentation for the free out-of-the-box setup path.

## [1.0.0-beta.9] — 2026-07-06

### Added

- Documented the LiveKit-first realtime migration plan. Future realtime work now
  targets `realtimeVoiceConnection` with a LiveKit engine, sidecar worker,
  worker-control WebSocket, feature-by-feature migration matrix, scoped LiveKit
  tokens, ConversationEventBus/SSE event reuse, and explicit retirement of the
  custom provider protocol bridge after parity.

### Fixed

- OpenAI vision-rejection retry tests now match the current `_http_post`
  signature that receives a per-call `base_url`, keeping relay-aware URL
  handling covered in CI.

## [1.0.0-beta.8] — 2026-07-06

### Fixed

- Relay-aware provider URLs now mint `/relay-proxy/...` links against the
  listener's private address and keep the route `private_only`, so leaked proxy
  URLs cannot be used from the internet. HTTPS listeners are supported by
  skipping certificate hostname verification only for that internal private
  `/relay-proxy/` hop.
- Token counting no longer fails at import time when `tiktoken` cannot download
  its `cl100k_base` BPE file in CI or offline environments; PawFlow falls back
  to a deterministic local estimate.

## [1.0.0-beta.7] — 2026-07-06

### Added

- Relay-aware provider URLs now support native `relay://<relay>/<host>:<port>/path`
  and `relays://<relay>/<host>:<port>/path` forms. Legacy
  `http(s)://<relay>/<host>:<port>/path` URLs remain supported.

### Fixed

- OpenAI-compatible relay streaming now resolves the relay base URL once per
  request, preventing repeated proxy token minting and inconsistent stream
  routing. Broken relay streams fall back to non-streaming completion with
  redacted diagnostic logs.
- MCP HTTP relay URLs now preserve conversation scope when minted at discovery
  time and re-minted at execution time.

## [1.0.0-beta.6] — 2026-07-06

### Fixed

- Relay-routed `llmConnection` base URLs now expose an explicit `relay_local`
  mode. Docker relays can route OpenAI-compatible endpoints such as Ollama at
  `http://<relay>/localhost:11434/v1` through the host helper, while container
  namespace targets remain available with `relay_local=false`.

## [1.0.0-beta.5] — 2026-07-06

### Fixed

- OpenAI-compatible `llmConnection` services can now reliably use relay-routed
  local endpoints such as `http://MyWorkspace/localhost:11434/v1`; per-call
  user/conversation identity is applied to isolated LLM clients before resolving
  the relay proxy URL.
- `/relay-proxy/...` now strips backend hop-by-hop streaming headers such as
  `Content-Length`, `Transfer-Encoding`, and `Connection`, preventing broken
  SSE/chunked responses from local OpenAI-compatible servers.
- `claude-code-interactive` custom `base_url` handling now supports custom
  HTTPS hosts/ports and local clear-HTTP upstreams behind the local TLS MITM.
- LLM client clone isolation is preserved while still allowing relay-aware URL
  resolution during `complete()` and `complete_stream()` calls.

## [1.0.0-beta.4] — 2026-07-06

### Added

- Flow lifecycle controls: `shutdownTrigger`, cron backpressure controls
  (`skip_if_pending`, `max_queue`), `manage_flow logs`, and
  `update_definition` hot-swap support make long-running flows easier to
  debug and stop cleanly.
- Relay-backed port forwarding is now surfaced through the PawFlow HTTP
  listener with absolute `/fwd/...` URLs in chat, plus slash-command support
  for listing, opening, and removing forwards by relay and visible port.

### Fixed

- Flow-deployed relay/script execution now preserves destination casing,
  injects user/conversation context into sandboxed tasks, and resolves relay
  filesystem services consistently.
- `httpListener` and `relay` help sheets now expose static parameter metadata,
  and the intentional `0.0.0.0` listener default is annotated for Bandit.
- chat-ui: inline audio players inserted in tool results never loaded (stuck
  at `0:00 / --:--`) in bearer-only sessions, while the same file played fine
  from the Files panel — the same 401 the inline video black-box bug had. The
  June revert of the authed-blob video fix also removed the audio half and it
  was never restored: the player used a raw `Audio(url)` src that sends
  neither the `pawflow_token` cookie nor the bearer header. Inline audio now
  fetches the bytes with the bearer header and plays from a same-origin blob
  URL, like the file viewer and inline images (video stays lazy-native, blobs
  would break range streaming there).
- `web_search` no longer returns a `ModuleNotFoundError` traceback when the
  connected relay's workspace is not the PawFlow repo: the relay payload
  imports PawFlow's `core` package, which only exists on the dev relay. A
  failed relay run (exception, non-zero exit, or empty output) now falls back
  to the server-side provider chain instead of surfacing the error as the
  search result.

## [1.0.0-beta.3] — 2026-07-04

### Added

- Realtime voice context (P3): voice sessions now start knowing what was
  discussed before — the `realtimeVoiceConnection` service's new
  `context_mode` (default `summary:2000`; `isolated` disables, `last:N` /
  `full` supported) appends conversation context to the session
  instructions, reusing the same context system as sub-agents
  (`resolve_context_messages`, extracted from the spawn handler). Applies
  to webchat sessions and Telegram voice-note turns.
- Gemini Live adapter (P3): `protocol: gemini_live` on
  `realtimeVoiceConnection` runs voice sessions through Google's Live API
  (`BidiGenerateContent`), with credentials from a `gemini` llmConnection.
  The adapter resamples PawFlow's 24 kHz uplink to Gemini's 16 kHz input
  (pure Python, no new dependency), maps `toolCall`/`toolResponse` onto
  the same PawFlow tool bridge, and handles server-side barge-in.
- Realtime session resumption (P3): when a provider drops a session whose
  adapter carries a resumption handle (Gemini Live
  `sessionResumptionUpdate`), the bridge reconnects transparently (max 2
  attempts) — the browser session, captions, and tool state survive the
  disconnect instead of ending with `provider_closed`.
- Voice settings panel (P3): right-click on the webchat mic button now
  opens a settings panel listing every realtime voice service with its
  model, voice, VAD mode, and context setting; one click selects, and the
  choice is remembered per conversation.

### Fixed

- The Gemini ACP warm-container fallback now only fires when the stored pool
  slot is missing (restart/compact), so an intentional slot change (rotation,
  slot removal) can no longer resurrect the previous account's live session.
  Codex and Gemini credential setup also no longer rewrite the pool when every
  refresh failure was transient (matching Claude Code), so a stale local copy
  cannot clobber a concurrent login.
- Gemini ACP live sessions now key warm containers by OAuth credential pool
  slot and recover compatible sessions when the stored slot is missing, so
  token recovery persists refresh-token changes back to the correct provider
  account. Conversation-scoped helpers also recognize `::flash::` sub-convs
  wherever normal delegate/task sub-convs were already supported.
- CLI provider credential setup now preserves freshly refreshed OAuth tokens
  when compacting dead pool entries, and reindexes the selected pool slot
  after purge so Claude Code, Codex, and Gemini teardown recovery write back
  to the correct account.
- Codex and Gemini OAuth refresh now distinguish rejected credentials from
  transient network/server failures, matching Claude Code behavior: temporary
  refresh failures no longer remove saved login slots from the provider pool.
- Delegate and `flash_delegate` sub-agents using CLI-backed providers now keep
  the parent conversation/service scope when resolving LLM clients, so one
  OAuth login shared through an `llmCredentialOAuthProvider` is reused instead
  of falling back to an empty credential pool.
- `flash_delegate` now receives the caller's source agent and `llm_service`
  context through the relay, matching normal `delegate` behavior. The Active
  Agents poll is also conversation-bound and rejects stale responses, so a
  delayed `list_active` response from another conversation cannot repaint the
  current conversation's active-agent panel or context gauge.

## [1.0.0-beta.2] — 2026-07-03

### Added

- Realtime voice conversation (P1): new `realtimeVoiceConnection` LLM-family
  service type — speech-to-speech sessions with a PawFlow agent through
  provider realtime APIs. Multi-provider by protocol adapter
  (`openai_realtime` covers OpenAI, Azure OpenAI, and compatible endpoints;
  credentials/base URL come from an existing `llmConnection`). The webchat
  gains a voice-mode button: continuous mic streaming and agent audio over an
  authenticated `/ws/realtime/{conversation_id}` WebSocket, live captions,
  barge-in, session/duration caps, and conversation-ownership enforcement.
  Final transcripts persist as normal messages so all attached clients see
  the exchange and the text agent resumes seamlessly. Design and phasing in
  `docs/REALTIME_VOICE_PLAN.md`.
- Realtime voice tools (P2a): the service's `tool_profile` exposes PawFlow
  tools to the voice model through a silent approval gate (new
  `ToolApprovalGate.check(allow_prompt=False)` probe — exempt/pre-approved
  tools run, anything needing a dialog is refused with a spoken
  explanation; `permission_mode` auto/read_only honored). Long tools detach
  to the background with an immediate interim result; the real result is
  injected back into the session, or persisted as a system message if the
  session already ended.
- Voice-native agents (P2b): agents can pin a `realtime_voice_service` in
  their conversation config (webchat agent editor). The webchat voice mode
  is now a full-screen overlay — state-reactive orb, live captions, tool
  activity, mute and hang-up — and a linked agent skips the service picker.
- Telegram speech-to-speech voice notes (P2c): a voice note sent to a
  voice-native agent is answered by a one-shot realtime turn (ffmpeg
  OGG/Opus ⇄ PCM16) — the reply is a Telegram voice note in the model's own
  voice, the transcript arrives as text through the live bridge, and the
  same tool bridge applies. Falls back to the STT pipeline on any failure;
  the bridge no longer synthesizes TTS on top of voice-channel transcripts.
- Voice mode UI: push-to-talk "Send" button for `vad=manual` sessions (the
  bridge announces the VAD mode in the `ready` frame), and the voice-service
  picker (right-click on the mic button) is now a clickable list instead of
  a `prompt()` dialog.

### Fixed

- Realtime voice stack hardened across ten review passes (26 findings):
  RFC 6455 fragmentation/reassembly and frame-size caps on both WS legs,
  provider-stream desync under mid-frame timeouts, session-cap starvation
  with a muted mic, force-stop wiring, `response.create` serialization
  against the active response (silent agent after fast tool calls),
  cross-session credential-scope race on shared service instances,
  question→answer transcript ordering in both VAD modes, Telegram
  double-processing of voice notes, ffmpeg timeouts, and socket/registry
  leaks on failed session opens.
- Audio WS proxy (`services/audio_proxy.py`, pre-existing): client frames
  are now reassembled per RFC 6455 fragmentation rules and capped at 16 MiB
  — a fragmented or hostile-length frame no longer corrupts the stream or
  buffers unbounded.

## [1.0.0-beta.1] — 2026-07-02

### Security

- chat-ui: the flow-instance editor error message is now HTML-escaped before
  being injected into the panel (last unescaped `innerHTML` error sink).

### Fixed

- FileStore: "Share public link" (`gateway_key` access) locked the owner out
  of their own authenticated access — the files panel "View" returned 403
  until the file was made private again. The gateway-key check now falls back
  to the owner check when no `?k=` is presented; `check_access` is covered by
  tests across all five access levels.

### Changed

- Project status: alpha → beta (README badge, PyPI classifier, ROADMAP,
  PROJECT_SUMMARY, website fallback version metadata).
- `/rewind`: removed the dead, never-wired `summarize` mode stub that
  answered "Not implemented yet"; summarize-from-checkpoint remains covered
  by `/compact`.
- docs: `media_tools.md` documents all `openaiCompatibleVideoGeneration`
  video modes, the configurable source-media body field names, and the
  config-only AtlasCloud Wan 2.7 recipe.

## [1.0.0-alpha.61] — 2026-06-30

### Added

- attachments: document payloads are converted to bounded Markdown context with
  MarkItDown when available, including PDF/DOCX/XLSX/PPTX-style inputs, and the
  dependency is declared in both `requirements.txt` and `pyproject.toml`.

### Fixed

- Telegram: document attachments and vision descriptions are preserved through
  the agent handoff instead of losing context before the model sees them.
- web_search: Claude-style `q` and `maxResults` arguments are accepted, Google
  search falls back to Chromium when static Google HTML is no longer parseable,
  and the server image installs the Patchright browser runtime needed by that
  fallback.

## [1.0.0-alpha.60] — 2026-06-28

### Fixed

- chat-ui: `show_file` video previews still rendered as a permanent black box
  while the same video from `generate_video` played fine. The `.58` lazy-load
  fix deferred wiring via a captured element id (`getElementById('vid_...')`),
  but inline tool-result media is reparented by technical grouping and can be
  re-rendered/replaced before the deferred pass runs — the captured id then
  pointed at an orphaned node while the *visible* `<video>` was never observed.
  Wiring is now a DOM sweep (`hydrateLazyVideos`) that observes whatever lazy
  `<video[data-lazy-src]:not([src])>` is actually present, re-run after every
  regrouping, so it survives reparenting and re-render.

## [1.0.0-alpha.59] — 2026-06-27

### Added

- openaiCompatibleVideoGeneration: full support for every video mode, not just
  text-to-video. The service now exposes `image_to_video`, `frame_to_video`
  (first + last frame), `reference_to_video`, `video_edit`, `video_extend`, and
  `speech_to_video`, so `generate_video` calls carrying an image/video/reference
  are dispatched to the provider instead of being rejected by the handler's
  capability gate. Source-media body field names are configurable
  (`image_field`, `end_image_field`, `video_field`, `audio_field`,
  `reference_field`) — defaults keep the generic OpenAI convention
  (`image_url`/`end_image_url`); set them to `image`/`last_image`/`video`/`audio`
  for AtlasCloud Wan 2.7.

## [1.0.0-alpha.58] — 2026-06-27

### Fixed

- chat-ui: inline `show_file` video previews intermittently rendered as a black
  box, while the full-screen file viewer always worked. The `<video>` carried
  its `src` up-front, but inline tool-result media is regrouped (technical/task
  grouping runs right after each render) and can sit in a collapsed panel — the
  native loader would skip an element that was hidden or mid-reparent, so it
  never painted a frame. The src is now deferred to an `IntersectionObserver`
  that loads the video once it is visible and its DOM position has settled,
  keeping native streaming (HTTP range requests, no in-memory blob).

## [1.0.0-alpha.57] — 2026-06-26

### Added

- openaiCompatibleVideoGeneration: `minimal_submit_body` option — when enabled,
  the async submit sends only `{model, prompt}` (plus `image_url`/`end_image_url`,
  `extra_body`, `callback_url`), omitting `duration`/`aspect_ratio`/`resolution`/etc.
  for providers (e.g. AtlasCloud) that reject unknown body fields. Combined with
  the configurable `submit_path` / `status_path_template`, AtlasCloud's Wan/Kling
  Predictions API (`POST /model/generateVideo` → poll `GET /model/prediction/{id}`)
  now works as a pure-config integration over an `openai` `llmConnection`.

### Changed

- openai-compatible media services: the image/video URL extractors now also
  resolve a result URL from `outputs`/`output` arrays even when the (often
  signed) URL carries no recognizable file extension — matching the response
  shape of Predictions-style providers like AtlasCloud.

## [1.0.0-alpha.56] — 2026-06-26

### Security

- chat-ui: file URLs and ids are no longer interpolated into inline `onclick`
  JS strings (`openFileViewer('…')`, download/share/delete in the file context
  menu, inline image/video/markdown-file links). The browser HTML-decodes an
  attribute before parsing its JS, so an escaped `'` (`&#39;`) decoded back and
  could break out of the string — a DOM-XSS vector for attacker-influenced file
  names/URLs. Values now reach the handlers via HTML-escaped `data-*`
  attributes read from `dataset`, matching the existing inline-audio pattern.

### Fixed

- openai provider: `base_url` paths whose version segment carries a suffix
  (e.g. `/v1beta`, `/v2alpha` on Gemini-compatible gateways) no longer get a
  spurious `/v1` re-appended; a fully-qualified `…/chat/completions` base is
  still used verbatim.

### Changed

- chat-ui: `escapeHtml` is now a single canonical definition in `state.js`
  (loaded early) instead of duplicated in `conversations.js` and
  `messages_tools.js`, so the escaper can't be silently shadowed by a stale
  copy.

## [1.0.0-alpha.55] — 2026-06-26

### Fixed

- openai provider: a `base_url` whose version segment is not `/v1` (e.g.
  z.ai's `/api/paas/v4`) was rewritten down to `/v1`, breaking every request
  to such gateways. The existing version segment is now preserved.

### Changed

- chat-ui / vscode webview: oversized JavaScript modules were split so every
  file is ≤800 lines, with no behavior change (per-file `node --check` and the
  full test suite stay green):
  - `sse.js` (2034 lines) → `sse_state.js` + `sse_handlers_a.js` +
    `sse_handlers_b.js` + a slim `sse.js` shell. `connectSSE`'s per-connection
    state is hoisted to module globals and reset on each connect; the event
    handlers are registered via `_sseWireA()` / `_sseWireB()` on the shared
    `eventSource`.
  - `messages.js` → core + `_render` + `_tools` + `_markdown`.
  - `conversations.js` → core + `_io` + `_menu`.
  - `terminal.js` → engine + `terminal_commands.js`.
  - vscode webview `chat.js` → `chat.js` + `chat_handlers.js`.
  - `HELP_DATA` extracted into `commands_help.js`.

## [1.0.0-alpha.54] — 2026-06-25

### Fixed

- Telegram: agent reasoning was duplicated — the live streamed preview
  (`thinking_delta` fragments) appeared, then the same reasoning again as
  the durable `thinking_content` block. The Telegram bridge merged the two
  with `\n\n` separators between every delta, so the fragmented preview no
  longer substring-matched the clean block and dedup failed. The bridge now
  keeps the delta preview separate; the durable block supersedes it (a
  leftover preview with no block — e.g. a cancelled turn — is still
  flushed). Webchat was unaffected and keeps streaming thinking live.
- claude-code (`-p`) streaming: the assistant's explanatory text ("here's
  what I'll do") arrived in the transcript *after* the tool calls it
  preceded. tool_use/tool_result were persisted live via `block_callback`,
  but text was only emitted at the end-of-turn flush, so it surfaced last.
  Text (and any pending thinking) is now persisted live in emission order —
  `thinking → text → tool_use` — mirroring the interactive provider, with no
  double-persist at the flush.
- claude-code-interactive / claude-code: native file tools
  (Read/Edit/Write/Glob/Grep/NotebookEdit) are no longer disallowed — the agent
  can read its local PawFlow bootstrap and session files even with no relay
  connected (mirrors the codex provider). Bash/WebFetch/WebSearch and the
  MCP-shadowed tools stay blocked.
- claude-code-interactive: each live container now claims an exclusive OAuth
  credential slot (1 login = 1 concurrent container). Anthropic refresh tokens
  are single-use, so two concurrent containers sharing one slot raced and
  invalidated the loser's session; pool exhaustion now raises instead of
  sharing, and teardown recovers any CLI-rotated token back to its slot.
- claude-code (`-p`): the mid-turn preempt intermittently failed — it targeted
  the subprocess via singleton state (`_claude_proc` / `_result_emitted`)
  clobbered by concurrent background streams. It now resolves the target from
  the live session registry, like claude-code-interactive's `find_session`.

## [1.0.0-alpha.53] — 2026-06-20

### Changed

- Refactored the relay worker for maintainability. `pawflow_relay/worker.py`
  shrank from 2390 to 776 lines (−68 %) by extracting focused modules, all
  ≤ 800 lines: `_relay_desktop` (VNC lifecycle + WS tunnel), `_relay_dispatch`
  (the `execute_command` router), `_relay_codeserver` (code-server process +
  WS tunnel), `_relay_terminal` (`TerminalManager` PTY), `_relay_actions`
  (http_proxy + script sync), `_relay_fs_setup` (combined FUSE mount), and
  `_relay_conn` (WS connect + handshake). Per-call state moved from function
  attributes to a `RelayWorkerState` dataclass. Behavior is preserved; the
  public `_ws_connect` entry point is unchanged. Adds ~890 lines of tests,
  including first execution coverage of the PTY, WS/VNC, and HTTP-proxy paths.

### Removed

- Dead HTTP `FSRelayHandler` path in the relay worker (never invoked —
  `worker_main` only calls `_ws_connect`).
- Dormant HTTP remote-worker stack in the engine.

## [1.0.0-alpha.52] — 2026-06-19

### Fixed

- Installed wheel could not start (`pawflow`/`pawcode` crashed with
  `ModuleNotFoundError: No module named 'cli_commands'`). `cli.py` imports
  `cli_commands` at module load, but only `cli` was listed in
  `[tool.setuptools] py-modules`, so `cli_commands.py` was never packaged.
  Both top-level modules are now declared.
- CI bandit run failed on the deliberate `subprocess` re-export in
  `core/install_bootstrap.py` (kept so tests can patch `ib.subprocess`);
  the import is now annotated `# nosec B404`.
- Pixazo describe/remix now upload the image bytes instead of handing
  Pixazo a URL it must fetch.
- Media error messages distinguish an unsupported operation from a
  failed-to-connect condition.
- claude-code (`-p`) tool calls now stream live again instead of arriving
  bundled with their results at the end of the turn. agent_core wired
  `block_callback` for every CLI provider except `claude-code`, so its
  tool_use/tool_result blocks were held until the end-of-turn flush — the
  UI showed the tool_call and its result together, late, with no BG/Kill
  window (worsened by newer Claude Code CLIs emitting the whole response
  under a single turn). `claude-code` is now in the `block_callback` gate,
  and the claude-code stream loop marks block-persisted tool_use ids so the
  turn flush no longer re-persists them (no double tool_call in the
  transcript).

## [1.0.0-alpha.50] — 2026-06-18

### Fixed

- Telegram inbound delivery latency/loss. Media callbacks download via
  `getFile` synchronously; running them on the single poll loop stalled
  `getUpdates` for every bot and message (text included) for minutes, then
  flushed in a burst, and could drop messages. Inbound updates now dispatch
  on a bounded thread pool so downloads run concurrently and the poll loop
  never blocks — messages (and images) arrive immediately again.
- Expired session now surfaces instead of a silent blank chat. An expired
  session makes `/api/agent/events` answer 401, but `EventSource` only exposes
  an opaque error; the stream is now probed on error and a confirmed 401/403
  shows a "session expired" message and redirects to login.
- Admin `view=all` no longer returns a sparse `list_resources`. The alpha.49
  branch dropped every non-catalog section (deployed flows, relays, remote FS,
  summarizer, tasks, flow templates), blanking the panel — notably "Dépôt
  Flows". The full self-view is now built first, then the repo-backed catalogs
  (incl. cross-user flow templates) are overlaid owner-labelled. Secrets and
  variables are never enumerated cross-user.

### Added

- Loading spinner in the chat while a conversation history loads (was a silent
  blank between clearing the view and the history arriving).
- Startup/post-login + relay-close diagnostics (`[ui-action]`, `[svc-load]`,
  `[sse-events]`, per-connection relay ids) to pin remaining startup latency.

## [1.0.0-alpha.49] — 2026-06-17

### Added

- Admin cross-user UI for the resource sidebar. Admins get a "view all" toggle
  on the Services / Flows / Dépôt listings (sends `view=all`) with an owner
  badge on every row, target-owner pickers in the install-service and
  create-resource dialogs, and a "which user?" prompt when demoting a global
  resource down to a user. Non-admins see none of this and behave as before.
- Admin owner override for the resource scope-move path
  (`copy_resource_scope`): demote a global resource to a specific user, or
  promote another user's resource to global, via
  `target_user_id` / `target_conversation_id`. Default = caller.

### Changed

- `tasks/io/chat_ui/resources.js` (5092 lines) was split into 10 semantic
  modules of <=800 lines each (core, pfp, flow_templates, render, menus,
  flow_dialogs, resource_dialogs, create_dialogs, service_dialogs,
  service_login). Cuts fall only on whole-function boundaries; load order is
  preserved in `_JS_MODULES` (core first). No behaviour change.

## [1.0.0-alpha.48] — 2026-06-17

### Added

- Admin cross-user scopes. An admin can switch the Services, Flow repository,
  and resource depot listings to a view-all mode (`view="all"`) that returns
  every user's and conversation's definitions, each labelled with its owner
  (user id / display name, and conversation when conv-scoped). The same admin
  may create, and promote/demote, on behalf of another owner via
  `target_user_id` / `target_conversation_id` — including "demote a global
  definition down to user X". All of this is strictly additive: a non-admin, or
  any request without the new fields, behaves exactly as before. New
  `core/admin_scope.py` centralises the admin gate, owner resolution (validates
  the target user exists and that a target conversation belongs to it), and
  owner display lookup. Enumeration primitives: `ScopedRepository`
  `list_all_owners`, `ResourceStore.list_all_global`,
  `ServiceRegistry.iter_all_scopes`.

### Fixed

- Telegram messages no longer arrive minutes late in bursts. Since the
  off-thread listener dispatch landed, the Telegram bridge ran on a serial
  per-conversation lane with no backpressure while each send opened a fresh TLS
  connection, so under load it fell behind the (SSE-delivered) webchat. Sends
  now reuse a persistent per-bot keep-alive connection — kept separate from the
  long-poll `getUpdates` connection so a 30s poll never blocks a send —
  reconnect on a broken socket, and honour Telegram `429 retry_after` with
  bounded backoff.
- The package version now lives in exactly one place. `core.__version__` had
  drifted to a hardcoded `1.0.0a10`; it is now derived from `pyproject.toml`
  (source checkouts) or the installed package metadata (wheels/docker), so it
  can never go stale again — only `pyproject.toml` needs bumping per release.

## [1.0.0-alpha.46] — 2026-06-16

### Fixed

- The display/persistence side of tool-call decoding is now unified on
  `core.tool_json.parse_tool_arguments`, matching the execution path. The
  unwrap family (`unwrap_mcp_tool`, the Claude Code `_pub` event relay, the
  interactive `_loads_tolerant`/`cc_interactive_filters` helpers, and the
  nested-unwrap loop in `agent_tool_exec`) each carried its own inline
  `json.loads`/autoclose mini-decoder; they now route through a shared
  `_decode_str_arg` helper, so a truncated or escape-mangled arguments
  envelope recovers identically on every provider and in the UI.
- Mid-string truncations are now recovered everywhere. The canonical
  truncation guard treats an "Unterminated string" decode error as an EOF
  truncation (CPython reports its position at the string's opening quote,
  which can be far from EOF), so autoclose repair fires on the execution path
  too — not just on the display helpers that previously autoclosed
  unconditionally.

## [1.0.0-alpha.45] — 2026-06-16

### Added

- Containerized `executeScript` now has full parity with the in-process path:
  `get_service(id)`, `pawflow`, and `flowfile` work identically, proxied to the
  host over the pfp host-call protocol (the service stays on the host; the
  container holds no secrets). Bytes round-trip losslessly; an explicit
  `docker_timeout` cancels a blocking `pawflow.run_agent`.
- `dbConnectionPool` is now a real connection pool: up to `max_connections`
  live connections, one per concurrent caller, with rollback-on-error and
  eviction of broken connections (SQLite `:memory:` pinned to one connection).

### Fixed

- Tool-call argument decoding is unified on
  `core.tool_json.parse_tool_arguments`. `tools/mcp_bridge.py` and
  `services/tool_relay_service.py` no longer carry divergent inline copies, so
  an arguments envelope decodes identically on every route — fixing intermittent
  "failed to decode arguments" and leaked `arguments_json` errors. The canonical
  module is vendored next to the bridge (`/opt/pawflow/tool_json.py`) in every
  provider container.
- The `telegram/pink_skin` moderation flow could not start: the script sandbox
  blocked `from core.embeddings import ...`. The embedding helper
  (`build_memory_embed_fn`) is now injected into `executeScript` in-process, and
  the blacklist regex is bounded to mitigate owner-supplied ReDoS.

## [1.0.0-alpha.44] — 2026-06-16

### Added

- Generic multi-tenant infrastructure for Telegram moderation bots (core), and
  the `telegram/pink_skin` 1.0.0 moderation bot flow built on top of it.

### Fixed

- Bandit B110 finding on the best-effort SQL rollback in
  `tasks/data/execute_sql.py`. The intentional `try/except/pass` cleanup is now
  annotated with `# nosec B110` and a rationale comment, so the security scan
  passes (exit 0) again.

### Changed

- Relay test suite: retry-sleep capture is now scoped to the test thread to
  avoid cross-test interference.

## [1.0.0-alpha.43] — 2026-06-16

### Fixed

- `pyproject.toml` version now tracks the release tag again. It had drifted to
  `1.0.0a33` while tags advanced; release commits now bump it alongside the
  CHANGELOG.

## [1.0.0-alpha.42] — 2026-06-16

### Fixed

- Claude Code interactive provider: live preempts — and the `POST /api/agent`
  request that triggers them — no longer block ~8.5s on tmux submit
  verification. `send_interrupt` ran the best-effort `_verify_submitted` pane
  poll (up to `PAWFLOW_CCI_SUBMIT_VERIFY_SECONDS`, default 6s, plus ~20
  docker-exec round-trips) inline on the request thread. It now runs in a
  daemon thread, so the ack returns immediately and queued Telegram/webchat
  messages no longer back up behind slow injections.
- Secret/variable right-click menu in the chat file viewer rendered literal
  `\u{1F5D1}` escape text instead of the 👁 / ✏ / 🗑 glyphs (doubled backslash
  in `tasks/io/chat_ui/file_viewer.js`).

### Added

- Website hero and README now note that the **Ask PawFlow** help bot is itself
  powered by a PawFlow agent flow.

## [1.0.0-alpha.41] — 2026-06-16

### Added

- Opt-in per-conversation encryption for the public web and Telegram help bots.
  A new `encrypt_conversations` flow parameter (default `false`) makes each
  visitor conversation encrypted at rest with a key derived from the visitor's
  own secret (the web session cookie / Telegram `user_id`); the lookup key is
  `sha256(session)` so the raw session is never stored, and the instance owner
  reading the conversation files on disk sees only ciphertext. Backed by new
  scope-bounded `enable_conv_encryption` / `unlock_conv_encryption` /
  `lock_conv_encryption` flow API methods that wrap the existing DEK/passphrase
  vault. A regression test proves the owner cannot read a conversation without
  the visitor's secret.
- Floating help-chat window on the website: on wider viewports the help panel
  can be dragged by its header and resized via a bottom-right grip; on phones
  it stays full-screen.

### Fixed

- ToolSearch agents no longer have every tool call denied. The permission gate
  ran against the literal `use_tool` dispatch wrapper instead of the inner tool
  it invokes, so a non-interactive conversation got an un-approvable denial and
  content-aware checks (dangerous `bash`, protected paths, read-only writes)
  inspected the wrapper's empty arguments and missed the real command. The gate
  now unwraps `use_tool` → the real tool (with its real arguments) and decides
  on that; `get_tool_schema` / `use_tool` schema plumbing is treated as
  transparent and always allowed. This also closes a latent hole where a
  dangerous `bash` invoked via `use_tool` bypassed the content checks.

### Security

- Flow-level prompt-injection defense for both help bots: every visitor message
  is wrapped before `run_agent` in a `_guard()` envelope delimited by a
  per-message random nonce, instructing the agent to treat the contents as
  untrusted data and ignore any embedded role/prompt/secret/tool-redirect
  attempts. This treats the visitor themselves as a potential attacker,
  independent of the agent's own system prompt.

## [1.0.0-alpha.40] — 2026-06-16

### Fixed

- Agent `max_depth` no longer throttles the tool-use loop. The per-conversation
  `max_depth` setting is the **sub-agent (delegation) recursion depth** only —
  enforced in the executor via `min(max_depth, MAX_GLOBAL_DEPTH)`. A stray
  override in agent context resolution also assigned it to `max_iterations` (the
  tool-use loop cap, a per-LLM-service setting), so any agent whose `max_depth`
  was lowered to forbid delegation was silently capped to that many tool
  iterations. With `max_depth=1` (e.g. the web/Telegram help bots: "no
  sub-agents") the agent got a single iteration, spent it on the first
  `get_tool_schema` call, then hit forced synthesis with no gathered data and
  hallucinated an answer instead of fetching the docs. The two notions are now
  fully decoupled: `max_iterations` is resolved from the LLM service/config
  (default 1000) and is never derived from `max_depth`.

## [1.0.0-alpha.39] — 2026-06-15

### Fixed

- SSE event delivery no longer stalls when a downstream sink is slow. The
  conversation event bus ran in-process listeners — notably the Telegram
  bridge, which POSTs to the Telegram API with a 60s socket timeout — inline on
  the conversation-writer thread, so a single slow push froze live SSE updates
  for every webchat client: the activity panel went blank and messages arrived
  up to ~40s late in bursts. Listeners now run on a bounded, dynamically sized
  thread pool with per-conversation ordering, so one slow sink can no longer
  delay the SSE stream, the server, or any other conversation. Pool size is
  tunable via `PAWFLOW_EVENT_LISTENER_THREADS`.
- Telegram bridge: long HTML messages (e.g. consolidated thinking blocks) that
  exceed Telegram's 4096-char limit no longer break markup. The message
  splitter is now tag-aware — it never cuts inside a tag and closes/reopens any
  open tags at chunk boundaries — fixing the `400 ... Can't find end tag
  corresponding to start tag "blockquote"` rejections that dropped every long
  mirrored message.

## [1.0.0-alpha.38] — 2026-06-15

### Changed

- Host networking is now the **default** container network mode on Linux
  (`--network-host` no longer needed; `--network bridge` opts back to `-p`
  publishing). macOS/Windows keep `bridge` by default because Docker Desktop's
  host networking binds the Docker VM, not the host, leaving ports unreachable.
- The in-container bind defaults to `0.0.0.0` (env `PAWFLOW_CONTAINER_HOST`)
  instead of `127.0.0.1` under host networking. A loopback-only bind made the
  main listener unreachable from sibling **bridge** containers — the managed
  server relays connect back via the host-gateway IP, which only resolves to a
  `0.0.0.0` bind, so a relay-less server could not start workspaces. Keeping
  those ports off the public internet is the host firewall's job in this mode;
  pass `PAWFLOW_CONTAINER_HOST=127.0.0.1` when a front proxy is the only ingress.

### Fixed

- `web_help_bot`: the `POST /api/help` route is now registered `public`, so
  unauthenticated visitors reach the help agent instead of getting a `401
  Unauthorized` from the session-auth gate. The endpoint's security boundary is
  its Origin allowlist, shared LLM budget, and per-session TTL — not login auth
  (mirrors `telegram_help_bot`). Redeploy the flow to pick up the fix.

## [1.0.0-alpha.37] — 2026-06-15

### Added

- Installer `--network-host` (`--network host|bridge`, env `PAWFLOW_NETWORK_MODE`):
  run the server container with host networking so every port it opens —
  including the dynamically-chosen ports of deployed `httpListener` flows, which
  are not known in advance — is reachable on the host without explicit `-p`
  publishing. The in-container bind defaults to `127.0.0.1` in this mode, so
  those ports stay loopback-only (private) and are meant to be fronted by a
  host-side reverse proxy (e.g. Caddy). The `web_help_bot` flow's `http_host`
  now defaults to `127.0.0.1` to match.

### Security

- Resource panel (`list_resources`) no longer leaks other users' deployed
  flows to an admin. The Flows section gated its owner/conversation check on
  `not _is_admin`, so any admin saw every user- and conversation-scoped
  deployment of every account (e.g. a technical user's user-scope bot).
  Ownership is now strict and owner-only — the admin role grants no cross-user
  visibility in this per-user panel; cross-user management stays on the
  dedicated admin endpoints. Other resource listings (agents, skills, MCP,
  tasks, prompts, hooks, services, variables, secrets, packages, voices) were
  audited and already scope strictly to the viewing user + global + current
  conversation.

### Fixed

- Resource panel stayed entirely invisible for a user with no conversation
  (e.g. a freshly-created/technical user). `_loadResourcesNow` hid the panel
  and returned early when no conversation was selected, so the no-conversation
  rendering path (added previously) was never reached, and the boot path with
  no conversations never called `loadResources()`. The panel now renders the
  scope-independent sections (Flows, Services, Packages, Variables, Secrets,
  Agent/Flows repositories) immediately on login, and refreshes into that view
  after the last conversation is deleted.

## [1.0.0-alpha.36] — 2026-06-15

### Fixed

- Resource panel: the **Variables** and **Secrets** sections disappeared
  entirely when empty — the section header (and its `+` create button) was
  gated on a non-empty list, unlike every other section. A user with no
  variables yet could never see the section or add a first one. Both headers
  now render unconditionally with a "no variables"/"no secrets" placeholder,
  matching Services/Flows/etc.
- Resource panel with no conversation selected (e.g. a freshly-created user
  before any conv exists) now shows only the scope-independent sections the
  user can act on: Flows, Services, Packages, Variables, Secrets, Agent
  Repository, Flows Repository. The conversation-scoped sections (Agents,
  Tasks, Relays, Filesystem, Summarizer, Linked Accounts) and the
  conv-irrelevant repos (Skills/Prompts/Themes/Voices/Tasks/MCP/AgentHooks/
  Tools) are hidden until a conversation is selected, instead of rendering a
  confusing mixed set.
- The `default.telegram_help_bot` flow (public Telegram help bot) was invisible
  in the Flow repository browser: it shipped without a `latest.json`, and the
  repo enumeration globs `**/latest.json`, so a flow lacking that file is never
  listed even though its `versions/1.0.0.json` is seeded to disk on restart.
  Added the missing `latest.json` (`{"version": "1.0.0"}`), matching every other
  default flow.
- Interactive-provider interrupt landing on a compact boundary no longer crashes
  the agent loop. When the provider compact already invalidated (killed) the
  Claude Code / Antigravity interactive session before a queued interrupt ran,
  `interrupt_claude_code_interactive` / `interrupt_antigravity_interactive` now
  treat the missing session as a completed no-op (force stop is never an error)
  instead of raising `No active … session for interrupt`.

### Added

- `pawflow` flow facade: user-scope variable access for deployed flows —
  `get_variable`/`set_variable` and an atomic `increment_variable` (file-locked
  read-modify-write via `ConfigStore.atomic_increment_param`, safe under
  parallel `executeScript` instances). Lets a public bot keep a durable,
  panel-visible/resettable counter (e.g. a shared LLM budget across all its
  conversations), since public-channel visitors have no per-user store.
- `pawflow.run_agent` now returns the completed turn's `cost_usd`, `tokens_in`
  and `tokens_out` (surfaced from the existing `done` event — the same figures
  `/cost` reports) so a flow can charge a budget per turn.
- Deterministic, timing-free regression test for the empty `Bash()` tool-call
  race (`test_turn_coordinator_observed_full_args_supersede_empty_stream_emit`),
  marked `xfail(strict=True)`: it drives an empty STREAM emit that claims the
  `tc_id` followed by a full OBSERVED emit for the same id, asserting the
  complete args must win. Documents the two-emitter race at the code level and
  becomes the executable spec for the single-source fix (remove the xfail when
  the fix lands).
- `http_bots.web_help_bot` flow: a public web help bot exposed as an HTTP
  endpoint (`POST /api/help`), mirroring `telegram_help_bot` with HTTP
  ingress/egress — per-session conversation (cookie-keyed), sliding TTL,
  response timeout, Origin allowlist, and a shared daily LLM budget.

### Changed

- Conversations carrying a non-zero TTL are now treated as **temporary**
  (`ConversationStore.is_temporary`): the throwaway per-session conversations
  bots create are deliberately excluded from durable side effects — never
  git-historized (`git_snapshot` is a no-op) and never fed to auto-memory
  (`auto_extract_memories` returns early). Normal compaction still applies. The
  `.git` is left in place, so toggling a conversation unlimited↔temporary just
  stops/resumes committing.
- Builtin flow repository reorganized into groups: `cryptos/`, `github/`,
  `http_bots/`, and `telegram/` (out of the flat `default/` group, which now
  holds only `pawflow_agent` and `pawflow_installer`). The new groups are
  registered as image-managed roots so runtime-installed packages are never
  clobbered by image defaults.
- Crypto report flows (`daily_crypto_email_oauth2`, `manual_crypto_email_oauth2`)
  downgraded from v2.0.0 to v1.0.0 (old v1.0.0 dropped, v2.0.0 renumbered;
  fqn/subflow references updated).

### Removed

- Builtin `discord_agent`, `slack_agent`, and `whatsapp_agent` flow definitions,
  plus the demo/example flows (`demo_pipeline`, `example_pipeline`,
  `exemple_flux`, `http_hello_world`, `http-hello-world`, `sub_upper`). The
  Discord/Slack/WhatsApp task and service code is unchanged — only the shipped
  flow templates were removed.

## [1.0.0-alpha.35] — 2026-06-15

### Fixed

- Agent response waits no longer carry an illegal implicit timeout: the shared
  agent runtime wait (`AgentRuntimeAPI.wait_for_done` / `AgentResultWaiter`),
  the Telegram agent client, and the `pawflow` flow facade `run_agent` now wait
  unbounded by default (project rule: no timeout unless explicitly configured).
  A long turn that exceeded the old 600s cap could detach its coordinator and
  drop the final `done`, so the answer only surfaced on the next message.

### Added

- Diagnostic logging (`[cci-args-debug]`) at the two CCI tool-call emit points:
  warns, only when an MCP tool is about to be emitted with empty arguments,
  with the raw observed input and emit path (stream vs observed). Temporary
  instrumentation to pin a non-deterministic case where a `bash` tool call
  renders with empty arguments in the chat.

## [1.0.0-alpha.34] — 2026-06-15

### Added

- Generic, scope-bounded **`pawflow` API facade** injected into `executeScript`
  (alongside `content`/`attributes`/`flowfile`/`fs`). It lets a flow script
  drive PawFlow — `create_conversation`, `run_agent`/`submit_agent`,
  `cancel_agent`, `set_tool_filters`, conversation extras/TTL,
  `list`/`find`/`delete_conversation` — with every operation authorized against
  the flow's deployment scope via `core.flow_runtime_access` (the same boundary
  `createConversation`/`publishMessage`/`spawnAgent` use). `run_agent` enforces a
  hard message→response timeout and force-cancels a stuck turn, for unattended
  flows where no human can cancel.
- **Public Telegram help bot** flow (`default.telegram_help_bot`) built entirely
  from generic tasks (`telegramReceiver` + `executeScript` + `telegramSend` +
  `cronTrigger` sweep): one conversation per origin user, optional
  `allowed_chat_ids` source gate (restrict to a specific group, exclude DMs),
  no relay, web-only tool allowlist (`web_search,fetch,read`), sliding
  conversation TTL with proactive purge, and a configurable response timeout.
- PawFlow help-agent system prompt (`docs/prompts/pawflow_help_agent.md`) and
  documentation (`docs/telegram_help_bot.md`, plus the `pawflow` facade in
  `docs/multi_client_conversations.md`).

## [1.0.0-alpha.33] — 2026-06-14

### Fixed

- HTTP MCP servers were effectively unusable: the client spoke a non-standard
  "sessionless JSON-RPC over a single POST" dialect (one `POST`, `Accept:
  application/json` only, no `initialize` handshake, no `Mcp-Session-Id`, no
  SSE), which virtually no real MCP server (FastMCP, the official SDK servers)
  accepts — they answer 400/406 or reply over `text/event-stream`. Only stdio
  MCP servers (proxied through the relay) actually worked. The HTTP client now
  implements the conformant **Streamable HTTP** transport: lazy `initialize` +
  `notifications/initialized` handshake, `Mcp-Session-Id` capture and replay,
  `Accept: application/json, text/event-stream` negotiation, incremental SSE
  response parsing, and one transparent re-initialize-and-retry on an expired
  session (HTTP 404). Both tool discovery (`tools/list`) and invocation
  (`tools/call`) go through the new `core.mcp_http_client` module.

### Changed

- HTTP MCP tools routed through a relay-proxy URL now re-mint the ephemeral
  proxy token at call-time (from the stored URL template + user id) instead of
  reusing the token captured at discovery, which could expire on long-lived
  conversations. The relay HTTP proxy already streams SSE end-to-end and
  forwards the `Mcp-Session-Id` header in both directions, so no relay change
  was required.

## [1.0.0-alpha.32] — 2026-06-14

### Fixed

- Large `edit` tool calls rendered as a bare `Update()` with no arguments in
  the chat UI, while smaller edits rendered correctly as `Edit(<path>)`. The
  Claude Code interactive provider rebuilds a tool call's display arguments
  from the streamed `input_json_delta` chunks; when a large input was
  truncated at EOF the strict `json.loads` failed and the arguments were
  dropped to `{}`, so the client fell back to the bare tool-name summary. The
  provider now closes EOF-truncated tool JSON via `autoclose_truncated_json`
  before giving up — valid and genuinely-unrecoverable inputs behave exactly
  as before — and the chat UI recovers the file path from the edit result
  line as a fallback so the header reads `Update(<path>)`. Display-only: the
  edit itself always executed correctly.

## [1.0.0-alpha.31] — 2026-06-14

### Fixed

- Tool calls rendered as the raw `use_tool` wrapper in the chat UI
  (`Read(tool_name=read, arguments_json={...})`) instead of the real tool and
  its arguments. The client unwrap only peeled the wrapper when the tool *name*
  was still a `use_tool` wrapper; when a call arrived half-wrapped — name
  already unwrapped but the arguments still `{tool_name, arguments_json}`, the
  shape the server emits and persists — it passed the wrapper straight through.
  The client now also unwraps when the *arguments* are a `use_tool` wrapper,
  mirroring the server-side `unwrap_mcp_tool` behaviour.

### Changed

- Vision downscale ceiling is now configurable and defaults to 1568px on the
  longest edge (up from 720p/1280px). 1568px is the largest size the Anthropic
  API actually uses — it internally downscales anything larger for
  tokenisation — so this recovers detail for screenshots and fine text without
  spending tokens on pixels the model discards. Override with the
  PAWFLOW_VISION_MAX_DIM env var (clamped just below the 2000px provider
  reject); the re-encode byte budget is likewise overridable with
  PAWFLOW_VISION_MAX_BYTES.

## [1.0.0-alpha.30] — 2026-06-13

### Fixed

- OAuth credential loss on live-session idle teardown. The idle sweeper,
  shutdown, and evict paths killed a warm CLI container without copying back
  the OAuth token the in-container CLI had rotated into its workdir. Anthropic
  rotates the refresh_token (single-use), so the dropped rotation left a dead
  token in the pool and logged Claude Code users out (the next refresh failed
  with invalid_grant). Teardown now recovers the rotated token to the correct
  pool slot first. codex/gemini wired identically as defense-in-depth (OpenAI/
  Google do not invalidate the old refresh_token, so the same hole was benign
  there).
- Oversized images failed to render in vision instead of being downscaled.
  The read/filestore/workdir image paths emitted raw base64 without the shared
  downscaler, so images above the provider pixel limit errored. All image
  emitters now route through resize_image_for_vision, and the vision ceiling
  is lowered to 720p (1280px longest edge) so every payload stays small.
- MCP tool-argument decoding is now tolerant of near-valid JSON. A last-resort
  repair fixes invalid backslash escapes and raw control characters inside
  string literals — but only after strict parsing has already failed, and it
  never alters an already-valid payload. Decode-error messages no longer
  misreport invalid JSON as a wrapping problem.

## [1.0.0-alpha.29] — 2026-06-13

### Added

- Opt-in encryption at rest for conversations and conv-scoped relay workspaces.
  Strictly opt-in and transparent: conversations without it enabled are
  byte-for-byte unchanged on disk and through the API. Threat model is T1 (disk
  at rest) — with the server stopped, encrypted data is ciphertext on disk and
  no key is in memory.
  - Conversation encryption (`/encrypt on`): a per-conversation DEK encrypts
    content fields (message text, thinking, tool arguments and results) with
    AES-GCM; metadata (ids, timestamps, ordering, roles) stays clear so the
    store, restart-from, and git history keep working without the key. Content
    is migrated to ciphertext on enable and back on disable.
  - Key custody: the DEK is wrapped by a passphrase (scrypt + AES-GCM) in a
    RAM-only, session-bound vault — zeroised on lock, purged on logout,
    idle-locked after 15 minutes, and gone on server restart. Commands:
    `/encrypt status|on|off|unlock|lock|passwd`.
  - Optional recovery (escrow) passphrase: `/encrypt escrow on|off` +
    `/encrypt recover` to unlock when the primary passphrase is lost.
  - Trusted key-relay (optional, no prompts): bind a relay's X25519 public key
    (`/encrypt relay <pubkey>`) so a connected relay auto-unlocks bound
    conversations; the server seals the DEK to the relay pubkey and never holds
    a key that opens that wrap, and DEKs are purged when the relay disconnects
    (relay-gone = re-locked). Relay key provisioning via `pawflow-relay key`
    (init/status/export-pubkey/rotate, passphrase-locked at rest) and the
    Relay Desktop "Relay Encryption Key" panel; `pawflow-relay start --unlock-key`.
  - Workspace encryption for conv-scoped server relays (`/relay encrypt <id>
    on|off`, `/relay unlock <id>`): the workspace is stored as a CryFS
    cipher-store and mounted with a DEK delivered over the relay control channel.
  - Relay images bumped to `2026.06.13` (now include `cryfs`).
  - Docs: Security Model "Encryption at Rest" section, design RFC, `/encrypt`
    slash-command reference, and website (features, FAQ, how-to).

## [1.0.0-alpha.28] — 2026-06-13

### Fixed

- Web chat (SSE): the agent event stream checked its lifetime cap at the top of
  the loop, after `writer.iterate()` had already dequeued an event, and broke
  without yielding that chunk. When a message landed on the same iteration the
  cap expired it was dropped — and since `send()` had returned True the bus
  never buffered it for replay, so the reconnecting client could not recover it
  (the message reached side channels like Telegram via the flow sink but never
  the web chat transcript, intermittently). The stream now yields the dequeued
  chunk before the lifetime check and drains any already-queued events before
  closing; adds `SSEWriter.drain_nowait()`.
- Claude Code interactive (tool badges): CCI never set `tool_origin` on tool
  calls, so they rendered with no native/mcp badge (unlike Codex). Tool calls
  are now tagged — PawFlow MCP-bridge tools (`use_tool`/`get_tool_schema`) get
  the MCP badge, the allowed native Claude Code tools get the Native badge —
  threaded through the MITM observer and both provider emit paths.
- Web chat (tool-call display): the client-side `use_tool` unwrap read only the
  legacy `arguments`/`parameters` object, never the advertised `arguments_json`
  string, so a raw wrapper reaching the client rendered as empty parens. The
  client now decodes `arguments_json` first, mirroring the alpha.27 server fix.

## [1.0.0-alpha.27] — 2026-06-13

### Fixed

- Claude Code interactive (transcript display): after alpha.26 switched the
  `use_tool` payload to a string `arguments_json`, the CCI transcript observer
  still read the inner arguments from the legacy `arguments` object, so tool
  calls rendered with empty parentheses (`Bash()`, `Read()`) in the technical
  details panel. The observer now decodes `arguments_json` first (falling back
  to a legacy `arguments`/`parameters` object), so arguments render again.
  Display-only — tool execution was unaffected. Codex/other providers use a
  separate path and were never impacted.

## [1.0.0-alpha.26] — 2026-06-13

### Fixed

- Claude Code interactive (MCP bridge): `use_tool` advertised its payload as a
  free-form `arguments` object, which Anthropic's constrained tool decoding
  intermittently collapsed to an empty `{}` input (`tool_name` and arguments
  both dropped) — producing random "missing required parameter 'tool_name'"
  failures. The bridge now advertises a string `arguments_json` field (mirroring
  the in-process meta-tool); the reader still accepts `arguments_json`, a legacy
  `arguments` object, or flat keys, so other MCP clients (Codex, Gemini) are
  unaffected.
- Telegram bridge: the pre-answer reasoning of a turn was dropped. Thinking was
  buffered under the agent's `agent_name`, but the closing `new_message` event
  carries only `source.name`, so no-tool-call turns never flushed their
  reasoning to Telegram (webchat showed it). The buffer key is now derived from
  `agent_name` or `source.name`, and turn end (`done`/`error_event`) flushes any
  remaining burst.

### Added

- Tool name aliases `image`, `image_view`, `view_image` route to the `see`
  (vision) tool — for `use_tool`, direct MCP calls (rerouted through use_tool,
  no new tools exposed), and HTTP providers. `view` still maps to `read`.
- Design RFC `docs/design/encryption-at-rest.md`: opt-in, per-conversation
  at-rest encryption and encrypted server relay workspaces (threat model,
  KEK/DEK with passphrase/relay/escrow wraps, RAM-only custody, UX/commands).

## [1.0.0-alpha.25] — 2026-06-13

### Fixed

- Relay/services connection dot: the Services list reported a relay as
  "started" as soon as it was enabled, while the Relays panel reported it via
  the live connection state — so the same relay could show green in one panel
  and red in the other during the connect window. Both panels now compute a
  relay's state from the same `is_connected()` call.

### Changed

- Relays panel connection dot is now tri-state, matching the Services list: 🟢
  connected, 🟡 connecting (enabled but the relay pool has no connection yet —
  managed container dialing back or lazy connect in flight), 🔴 down/disabled.
  The relay info dialog shows the same "starting" state.

## [1.0.0-alpha.24] — 2026-06-13

### Fixed

- Sub-conversation runtime scope (HIGH): the tool relay only rooted `::task::`
  sub-conversations to their parent, so `::task_verify::` and `::delegate::`
  sub-conversations resolved hooks, tool permissions and secret injection
  against their own (empty) conversation id. A `bash`/`execute_script` run from
  a verify or delegate step did not enforce the parent's tool permissions or
  receive its secrets. `_root_conversation_id` now strips all three markers.
- Vision: a pre-uploaded oversized image (e.g. a full-resolution JPEG whose
  mime type is unchanged by the resize) was downscaled in memory but the
  oversized original was kept in storage, so downstream reads still hit the
  provider pixel limit. The attachment is now re-stored whenever the resize
  actually changed the bytes.
- Catch-up context: the Claude Code provider stripped `::delegate::` and
  `::task::` but not `::task_verify::`, so a verify sub-agent received no
  catch-up from the parent conversation. Aligned on the canonical marker
  triple.

## [1.0.0-alpha.23] — 2026-06-13

### Fixed

- Claude Code interactive and Antigravity interactive: a live preempt that
  extended a turn past a Stop hook left the stop/done latch set, so a later
  idle gap (the model churning on a large tool result) ended the turn
  coordinator mid-answer. The coordinator returned the already-delivered
  previous answer while the real final answer was generated with no listener —
  reaching only the tmux session, never the webchat/Telegram channels. A fresh
  `/v1/messages` request after a Stop now clears the stale latch so the turn
  runs to its real end and the final answer is delivered.
- Vision: oversized images are now downscaled to the 2000px ceiling
  proactively at ingestion, provider-agnostically. User attachments,
  tool-result images and `see`/`screen` captures share one resize helper
  (`core/image_resize.py`), so a full-resolution screenshot no longer exceeds
  the provider pixel limit and gets rejected at read time — the stored copy
  every downstream path uses is already within limits.

## [1.0.0-alpha.22] — 2026-06-12

### Fixed

- Full scope-resolution audit (11 passes) across the four scoped chains —
  ServiceRegistry, ResourceStore/repository, the secrets/params expression
  cascade, and relay bindings. ~80 call sites that resolved only user/global
  now walk the canonical conv > user > global chain, so conversation-scoped
  services, agents, skills, prompts, secrets and relays (e.g. installed by
  packages into a conversation) are visible everywhere they are used:
  agent system prompts and Connected Relays, relay listing/connect/disconnect,
  relay-proxy routes (tokens now carry the conversation), LLM service and
  cost lookups, fs-service auto-detection, tool argument expression
  resolution, and more.
- Relay bindings: `/relay status` and the cognitive-ui build fallback now
  read the per-agent bindings format correctly; whitelists, scans and
  fs-manifest notifications cover agent-specific links via the new
  `get_linked_all`.
- Sub-conversations (`::task::`, `::task_verify::`, `::delegate::`) inherit
  the parent conversation's agent roster, and all SSE/event routing and
  task/config lookups recognize every sub-conversation marker instead of
  only `::task::` — delegate events no longer vanish onto an unwatched bus.
- Checkpoint rewind and cleanup actually work again: checkpoint files are
  saved with an owner, but all reads passed no user_id and were silently
  denied, so rewind restored nothing and expired checkpoints were never
  deleted. Sandbox `filestore://` reads and the write handler no longer
  wrongly deny the caller's own private files; filestore deletes now enforce
  the owner check.
- delete_agent routes to the scope the definition actually lives in
  (conversation/user/global with admin gate), matching delete_skill.

## [1.0.0-alpha.10] — 2026-06-10

### Fixed

- Telegram now shows agent thinking as a single consolidated block per
  reasoning burst instead of flooding the chat with every streamed fragment
  ("bouts") followed by a duplicate of the whole thing. The conversation
  bridge accumulates `thinking`/`thinking_delta`/`thinking_content` events and
  flushes one merged message when the burst ends (next tool call, tool result,
  or message), de-duplicating cumulative snapshots. This also removes the
  message-flood that could rate-limit the bot and stall inbound Telegram
  messages. Most visible with the Claude Code interactive provider, whose CLI
  now emits thinking in many small blocks.
- Claude Code interactive terminal viewer ("open in tmux") no longer reports
  "no sessions". The webchat viewer attached/resized the tmux session as a
  hardcoded uid 1000, but alpha.9 moved the in-container CLI (and its tmux
  server) to `PAWFLOW_RUN_UID`; the viewer now derives the same uid from the
  pool, so it looks in the correct `/tmp/tmux-<uid>/` socket dir.

## [1.0.0-alpha.9] — 2026-06-10

### Fixed

- Media reference sharing now actually reaches the provider. The temporary
  public `?k=` (gateway_key) URL minted for image/video/audio reference
  inputs was rejected with `401 Unauthorized` by the HTTP listener's inline
  session-auth gate, which had no notion of public/gateway_key file access
  (the private gateway and the flow auth task already did). `/files/<id>`
  downloads that authenticate via a public access level or a valid `?k=`
  now bypass the session gate; `_handle_filestore_download` still enforces
  `check_access`. This unblocks image-to-video and other media-ref flows.
- Claude Code interactive containers now run the in-container CLI as
  `PAWFLOW_RUN_UID`/`PAWFLOW_RUN_GID` (the host user that launched the
  PawFlow Docker server) instead of a hardcoded uid 1000 — matching the
  batch claude-code pool. The session `projects/` and `memory/` trees are
  created and chowned to that uid, so server-side tools (e.g. the memory
  skill's `write` via the combined-fs) and the CLI share one uid and no
  longer hit `Permission denied` across the uid boundary. Existing
  on-disk sessions created before this fix stay owned by the old uid and
  may need a one-time `chown` of their `projects/` trees.

## [1.0.0-alpha.8] — 2026-06-10

### Added

- Share FileStore files publicly from the chat: the file context menu now
  offers "Share public link" (mints an unguessable gateway-key URL that
  needs no login and bypasses the private gateway) and "Make private" to
  revoke it, backed by a new owner-only `set_file_access` action.
- Media webhook mode now polls the provider status URL in lockstep with
  the callback (Pixazo): a callback that never arrives falls back to
  polling instead of hanging until the timeout.

### Fixed

- Media reference inputs no longer leak the dead `localhost:9090` handler
  default to external providers. The temporary public share resolves the
  reachable base from the media service `public_callback_base_url` (the
  value already used for webhooks), so image-to-video and other reference
  flows work without a separate relay `file_base_url`; a clear warning is
  logged when no public base can be resolved.
- Claude Code interactive: the first message after a cold container/tmux
  start is no longer dropped. The sender now waits for the TUI input
  prompt to be on screen before pasting, fixing the race that required a
  manual Enter.

## [1.0.0-alpha.7] — 2026-06-10

### Added

- Media reference inputs (image/video/audio) are shared as public,
  gateway-key URLs only for the duration of a single generation call and
  revoked afterwards, letting external providers fetch FileStore assets
  without leaving them publicly reachable. Wired into `generate_video`,
  `edit_image`, and every capability handler.
- Website: Telegram surfaced as a first-class agent client — homepage
  showcase section and a Channels how-to recipe with a real chat
  screenshot.

### Fixed

- Media provider webhooks: callback routes now bypass the private gateway
  challenge (`gateway_exempt`) while still accepting public IPs, so a
  provider's internet callback reaches PawFlow instead of the challenge
  page — previously the job was never notified and silently timed out.
- Webhook mode now surfaces a synchronous-ack error (invalid input URL,
  unsupported format, ...) immediately instead of blocking on a callback
  that will never arrive.
- CC interactive: double-Enter submit so a message is not dropped when it
  is sent right after a restart.

## [1.0.0-alpha.6] — 2026-06-10

### Added

- `github.ci_autofix` flow package: auto-fix CI failures via webhooks.
- Per-instance webhook routes minted through the reserved
  `${_instance_id}` parameter.
- Website: hero install command, SEO metadata, release links resolved
  live from the GitHub API, and generated hero/diagram/docs-map/FAQ
  visuals.

### Fixed

- CI tests no longer download models from HuggingFace, and the CI job is
  capped at 30 minutes — a stalled download could otherwise hang the job
  until the 6h Actions limit.
- OpenAI image generation filesystem handling and request timeout.
- The interactive final response is now emitted as the last message
  only; CLI task store writes fixed.
- tmux submit tests record only the test thread's sleeps, removing a CI
  flake.

## [1.0.0-alpha.5] — 2026-06-10

### Added

- Expression language: documented `${...}` escaping via opaque tokens
  that survive recursive resolution passes.
- claude-code image: resolve and pin the latest published npm version of
  each agent CLI so a rebuild reinstalls only on an upstream change.

### Fixed

- Expression resolver no longer mangles unresolved `${...}` expressions
  (pipeline ops in content, e.g. shell parameter expansions, were
  truncated).

## [1.0.0-alpha.4] — 2026-06-09

### Added

- Surface the effective CCI model from `message_start`.
- Documentation: A2A multi-hop async confirmation saga and A2A
  multi-client isolated context patterns.

### Fixed

- Normalize suffixed Telegram bot commands (e.g. `/cmd@botname`).
- Telegram command mirroring and CCI final-response relay.

## [1.0.0-alpha.3] — 2026-06-09

### Added

- Manual tmux messages in Claude Code Interactive (CCI) are now
  published live.

### Fixed

- Avoid side effects when mirroring Telegram commands into conversations.

## [1.0.0-alpha.2] — 2026-06-09

### Added

- Telegram commands are mirrored into active conversations.

### Fixed

- Interactive tmux runtime isolation.
- Preserve tmux mouse scroll in interactive terminals.

## [1.0.0-alpha.1] — 2026-05-19

First public release.

### Added

**AI Agents**
- Multi-agent conversations with tool-use loop (LLM → tool → LLM → ...)
- 5+ LLM backends: Claude Code, Codex CLI, Gemini CLI, Anthropic API, OpenAI API, and OpenAI-compatible endpoints
- Streaming SSE output to web chat and CLI
- Plan system: structured plan creation, approval, assignment, verification
- Context compaction with `{agent_name}.md` re-injection
- Configurable permission modes: auto, approve-edits, read-only
- Cost tracking with per-conversation budget caps (`max_budget_usd`)
- Force stop: Escape 1x = graceful, 2x = immediate kill

**Tools (90+)**
- Filesystem: read, write, edit, glob, grep, list_dir, move, delete
- Execution and desktop: bash, execute_script, run_in_background, screen, browser, desktop/VNC-backed interaction
- Web: web_fetch, web_search, web_screenshot
- Media: generate_image, generate_video, generate_audio, generate_3d, upscale_image, try_on, lipsync, clone_voice, speak, see (vision)
- Git: git_log, git_diff, git_commit, git_branch
- Multi-agent, plans, and resources: delegate, ask_user, create_plan, manage_plan, manage_resource, link_resource
- Security: security_scan, validate_http_auth
- MCP: connect to any MCP server, tools auto-discovered
- All relay-backed tools route through the connected runtime for local or containerized execution

**Cognitive Systems**
- Memory: categorized facts with scopes and temporal validity
- Knowledge Graph: entity-relationship triples with BFS/DFS, community detection
- Agent Diary: per-agent personal journal
- Project Graph: AST-based code structure analysis (17 languages via tree-sitter)
- Memory digests auto-injected into system prompt

**Pipeline Engine**
- 100+ NiFi-style tasks across 5 categories (System, IO, Data, Control, AI)
- Batch, continuous, and CRON execution modes
- Backpressure, checkpointing, crash recovery
- Flow versioning with rollback
- Graphical debugger with breakpoints and step-through
- Data preview and flow diff
- NiFi flow import (XML/JSON) with Groovy-to-Python script conversion
- 15 flow templates (ETL, Monitoring, Communication, Data Processing, Integration)
- Event triggers: file watcher, webhook, event-driven, polling

**Web Chat UI**
- Real-time SSE streaming
- File explorer with relay filesystem access
- Context editor (view/edit agent context)
- Conversation management with auto-titles
- Shared conversation state across web, PawCode CLI, VS Code, APIs/channels, and flows
- @file autocomplete from relay filesystem
- 60+ slash commands
- Drag & drop file attachments
- Multi-agent support with agent switching
- Desktop access via `/desktop`, screen interaction, and VNC-style sessions when configured

**Infrastructure**
- 9 OAuth2 providers (Google, GitHub, Microsoft, X, Facebook, Amazon, Telegram, Generic)
- Expression language: 40+ chainable operations with scope cascade
- Docker relay for sandboxed tool execution
- Plugin system with semver versioning, .pfp export/import
- Cluster mode with leader election
- Audit logging, rate limiting, Prometheus metrics
- HTTP listener service with SSL/TLS
- PawCode CLI (Claude Code-compatible terminal client)
- VS Code extension connected to the same relay/runtime model
- 4105 tests

**Skills**
- Agent Skills system: per-skill `SKILL.md` manifests with bind-mounted
  asset directories and allowed-tools enforcement.
- Skills repository FUSE mount (`skfs.*`): relay containers mount the
  Agent Skills repository read-only at `/skills`, so non-CLI providers
  can reach a skill's asset files referenced from its instructions.

### Fixed

- `SKILL.md` frontmatter no longer accumulates the read-derived
  `declared_allowed_tools` alias on update.
- `/skill update` is routed to the server from the chat UI, and
  `/add-skill` derives a short manifest description instead of copying
  the full instructions body.

### Security
- Secrets encrypted at rest with AEAD v2
- PBKDF2 password hashing (600K iterations)
- `config/secret.key` excluded from version control
- Configurable CORS, rate limiting, request size limits
- Sandboxed script execution with restricted imports
