# Changelog

All notable changes to PawFlow will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
