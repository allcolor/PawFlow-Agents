# Changelog

All notable changes to PawFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
