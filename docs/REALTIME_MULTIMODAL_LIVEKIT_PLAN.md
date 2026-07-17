# Realtime Multimodal LiveKit Integration Plan

Status: P0 in progress — spike infrastructure landed 2026-07-17 (dependency group, compose profile, spike scripts, worker-control prototype + CI tests); live OpenAI/Gemini spike runs pending API keys.
Decided: 2026-07-06. Updated: 2026-07-16 (local pipeline profile added to cascade mode).
Owner intent: replace PawFlow's custom realtime voice bridge with a LiveKit Agents based implementation, then extend it to audio/video multimodal sessions without duplicating media transport or provider-specific realtime stacks.

This plan supersedes the custom-provider direction in `docs/REALTIME_VOICE_PLAN.md` for future realtime work. The existing `realtimeVoiceConnection` implementation is treated as a migration source and temporary compatibility layer only. The target architecture is: PawFlow remains the orchestration, identity, tool, memory, and persistence layer; LiveKit Agents becomes the realtime media and provider integration layer.

## Goals

- Let a PawFlow agent run as a live audio or audio/video assistant.
- Support OpenAI Realtime and Gemini Live first, with Azure OpenAI, xAI, AWS Nova Sonic, and other LiveKit realtime plugins following through the same integration.
- Avoid reimplementing WebRTC, rooms, track handling, video sampling, provider adapters, VAD, interruption handling, and SDK-specific session mechanics inside PawFlow.
- Preserve PawFlow semantics: selected agent, service configuration, secrets, tool ACLs, conversation persistence, streaming UI events, force-stop behavior, memory extraction, and provenance.
- Replace the existing custom realtime voice implementation after LiveKit reaches feature parity; do not maintain two realtime stacks long-term.

## Non-goals

- Do not build a full custom WebRTC SFU or room server in PawFlow.
- Do not duplicate LiveKit provider plugins as PawFlow-native providers unless there is a clear gap.
- Do not expose provider API keys to browsers or mobile clients.
- Do not persist raw audio or video by default.
- Do not implement voice-based approval for privileged tools; spoken transcripts are not a secure confirmation channel.

## Recommended Architecture

```
PawFlow conversation UI / flow runtime
  -> RealtimeAgentSession API
    -> LiveKitRealtimeService
      -> LiveKit room + LiveKit Agents sidecar worker
        -> LiveKit provider plugin
          -> OpenAI Realtime | Gemini Live | Azure OpenAI | xAI | AWS Nova Sonic | ...
```

Media path:

```
Browser/mobile client
  -> LiveKit server over WebRTC
  -> LiveKit Agents sidecar worker
  -> provider plugin
  -> provider realtime API

provider realtime API
  -> provider plugin
  -> LiveKit Agents sidecar worker
  -> LiveKit server over WebRTC
  -> Browser/mobile client
```

Control path:

```
Browser/mobile client
  -> PawFlow HTTP/SSE/WebSocket APIs for session start, status, force-stop, and UI events

UI realtime events should reuse the existing ConversationEventBus and SSE fan-out wherever possible. The LiveKit worker-control channel normalizes provider events into PawFlow events; PawFlow publishes them to attached clients through the same conversation event path used by chat updates. A dedicated browser WS should be added only if the existing SSE path cannot meet latency or ordering requirements.

LiveKit Agents sidecar worker
  -> PawFlow worker-control WebSocket for transcripts, state, usage, tool calls, tool results, and shutdown commands
```

PawFlow is no longer in the hot media path. PawFlow issues scoped room tokens, starts or dispatches the worker, receives normalized control events, executes tools, persists final transcripts, and can force-stop the session. Audio/video packets flow through LiveKit, not through PawFlow's raw PCM websocket bridge.

PawFlow owns:

- Agent selection and instructions.
- Agent-linked realtime service choice.
- Secrets and ephemeral token issuance.
- Tool exposure, ACLs, approval checks, and execution.
- Conversation store writes with UUID and timestamp.
- SSE/WebSocket fan-out to all attached clients.
- Session lifecycle, caps, audit logs, and force-stop.
- Worker-control WebSocket endpoint and normalized event persistence.

LiveKit owns:

- WebRTC media transport.
- Rooms and participant tracks.
- Browser/mobile media SDKs.
- Audio/video track ingestion.
- Video frame sampling for model input.
- Realtime model plugin lifecycle.
- Provider-specific protocol differences.
- Barge-in and turn handling where supported.

## Runtime Topology Decision

Default topology: LiveKit Agents runs as a sidecar container or sidecar process, not inside the PawFlow web/server process.

Reasoning:

- LiveKit Agents is asyncio-native. PawFlow's current realtime bridge and HTTP stack are sync/thread-oriented. Running LiveKit Agents in-process would require a dedicated event loop thread and would create fragile lifecycle coupling.
- A sidecar matches PawFlow's existing deployment style: relay containers, CCI containers, and isolated execution services.
- The sidecar can be scaled or restarted independently from PawFlow without killing the main HTTP worker.
- Provider SDK dependencies stay out of minimal PawFlow installs unless realtime LiveKit is enabled.

Initial deployment modes:

1. Local development: Docker Compose starts PawFlow, LiveKit server, and `pawflow-livekit-worker`.
2. Production self-hosted: LiveKit server and worker are explicit services in the deployment.
3. LiveKit Cloud: PawFlow still runs the worker sidecar, but rooms are hosted by LiveKit Cloud.

In-process LiveKit Agents is not the default target. It can be considered only for an explicit minimal single-binary mode after the sidecar path works.

## Worker Control Channel

Use a dedicated worker-control WebSocket from the LiveKit worker to PawFlow. This is separate from the browser LiveKit WebRTC connection.

Endpoint shape:

```
/ws/realtime-worker/{session_id}?token=...
```

Properties:

- The token is server-issued, short-lived, scoped to one session, one worker identity, one conversation, and one agent.
- The worker opens the connection after it joins the LiveKit room.
- Worker -> PawFlow events carry transcripts, state, usage, errors, tool calls, media metadata, and provider lifecycle events.
- PawFlow -> worker messages carry tool results, force-stop, context injections, and shutdown commands.
- If the control channel drops, PawFlow marks the session degraded and may force-stop after a short grace period.

HTTP callbacks are simpler but insufficient for bidirectional tool calls and force-stop. Redis/pubsub is unnecessary for the first implementation and would add another required dependency.

## Service Model

Target service model:

```
type: realtimeVoiceConnection
engine: livekit
```

During migration, a temporary internal/new service name such as `livekitRealtimeConnection` is acceptable behind a feature flag, but the final user-facing model should reuse the existing realtime service concept instead of creating a permanent parallel service family.

Suggested config keys:

| Key | Required | Notes |
|---|---:|---|
| `engine` | yes | `livekit` for the replacement implementation. Legacy configs without `engine` are interpreted by the compatibility loader. |
| `livekit_url` | yes | LiveKit server URL. Can point to self-hosted LiveKit or LiveKit Cloud. |
| `livekit_api_key` | yes | Stored as a PawFlow secret reference. |
| `livekit_api_secret` | yes | Stored as a PawFlow secret reference. |
| `llm_service` | yes for migrated configs | Existing `llmConnection` id used as the provider credential/source-of-truth for compatibility. Preferred over introducing provider credentials directly into the realtime service. |
| `provider` | yes | `openai`, `gemini`, `azure_openai`, `xai`, `aws_nova`, or later plugin name. Can be inferred from `protocol`/`llm_service.provider` for legacy configs. |
| `provider_secret` | optional | Secret name for provider credentials when no `llm_service` exists. Server-side only. |
| `model` | yes | Provider model or deployment ID. |
| `voice` | no | Provider voice ID. |
| `modalities` | no | Default `audio,text`; optional `video`. |
| `instructions_mode` | no | Preserve current semantics: `agent` uses selected agent instructions, `custom` uses `instructions`. Default `agent`. |
| `instructions` | no | Custom system instructions when `instructions_mode=custom`. |
| `input_audio_format` / `output_audio_format` | no | Accepted for compatibility. LiveKit/provider plugin owns actual transport encoding where possible. |
| `transcription_model` | no | Optional provider-specific transcription setting when plugin supports it. Otherwise ignored with a warning. |
| `video_input` | no | Enables camera/screen-share frame ingestion. Default false. |
| `video_source` | no | `camera`, `screen`, or `both` for UI defaults. |
| `video_fps_active` | no | Default 1 FPS while the user is speaking. |
| `video_fps_idle` | no | Default 0.33 FPS while idle. |
| `turn_detection` | no | Replacement for legacy `vad`: `provider_default`, `semantic_vad`, `server_vad`, or `manual`, depending on plugin support. |
| `tool_profile` | no | Existing PawFlow tool profile to expose to the live agent. |
| `context_mode` | no | Reuse realtime voice context modes, default `summary:2000`. |
| `max_session_seconds` | no | Hard cap, default 600. |
| `recording_policy` | no | `none`, `transcript`, `audio`, `audio_video`; default `transcript`. |

This service should be selectable from an agent configuration as the agent's realtime service. It should not replace the normal agent `llm_service`; an agent can have both:

- `llm_service`: request/response text and tool loop.
- `realtime_service`: live audio/video execution mode.

Provider credentials compatibility:

- Existing `llm_service` remains the primary migration path. The LiveKit worker receives resolved provider configuration from PawFlow; it does not read arbitrary user secrets directly.
- `provider_secret` is only for new provider configs that do not have an existing `llmConnection` representation.
- No provider API key is sent to the browser. PawFlow sends provider credentials only to the trusted sidecar worker through its environment or worker-control bootstrap payload.

## Existing Feature Migration Matrix

| Existing shipped feature | Target in LiveKit migration | Verdict |
|---|---|---|
| `realtimeVoiceConnection` service type | Keep service concept, add `engine: livekit`, migrate configs through compatibility loader | keep shape / replace engine |
| OpenAI realtime adapter | Use LiveKit OpenAI Realtime plugin | replace |
| Gemini Live adapter | Use LiveKit Gemini Live plugin; verify session resumption support during spike | replace, gap check |
| Browser raw PCM websocket bridge | Browser connects to LiveKit over WebRTC | replace |
| Mic capture and audio playback UI | Replace transport with LiveKit client SDK; keep PawFlow live panel state/captions | replace transport / keep UX |
| Context injection `context_mode` | Keep policy and send bounded context to worker/provider session | keep |
| `instructions_mode=agent/custom` and `instructions` | Keep exact semantics in service config | keep |
| Tool bridge silent approval | Reuse existing `RealtimeToolBridge` through worker-control WebSocket | keep |
| Force-stop semantics | PawFlow sends shutdown over worker-control channel and revokes/disconnects LiveKit participants | keep |
| Gemini `sessionResumptionUpdate` | Prefer LiveKit/plugin-native reconnect. If plugin does not expose equivalent, mark as provider gap before deleting old adapter. | gap check |
| Gemini 24k -> 16k resampling | LiveKit/provider plugin should own audio format conversion. Keep no PawFlow resampler unless spike proves a missing capability. | replace |
| Telegram one-shot voice turn | Migrate to LiveKit if practical; otherwise keep isolated one-shot path temporarily with an explicit deletion ticket. | gap check |
| Voice settings UI | Keep UI concept, populate from LiveKit-backed service metadata | keep |
| Existing tests | Convert service/tool/context/security tests to LiveKit fakes; delete protocol-adapter tests after old adapters are removed | migrate/delete |

No old custom adapter can be removed until the matrix row is either covered by LiveKit or explicitly accepted as a dropped feature.

## Backend Components

### 1. `LiveKitRealtimeService`

Location: `services/livekit_realtime_service.py`.

Responsibilities:

- Validate service config and required secret references.
- Create short-lived LiveKit room tokens for the browser.
- Start or dispatch a LiveKit Agents worker session for the selected PawFlow agent.
- Resolve provider plugin configuration from PawFlow service config.
- Enforce user/conversation authorization before creating a session.
- Track active sessions by `conversation_id` and `session_id`.
- Provide `stop_session(force=True)` with PawFlow force-stop semantics.

### 2. LiveKit Agent Worker

Preferred implementation: a Python worker module owned by PawFlow, launched as part of the PawFlow deployment or as a sidecar container.

Responsibilities:

- Join the LiveKit room as the agent participant.
- Instantiate a LiveKit `AgentSession`.
- Instantiate provider plugin from config.
- Map PawFlow agent instructions and context into the LiveKit agent.
- Enable `video_input=True` when the session config requires it.
- Forward transcripts, state changes, usage, tool activity, and errors back to PawFlow through the worker-control WebSocket.
- Call PawFlow tool bridge for allowed tool calls through the worker-control WebSocket.
- Run its own asyncio event loop inside the sidecar; PawFlow must not host the LiveKit Agents loop in the main HTTP worker.

### 3. `RealtimeToolBridge` Reuse

Reuse the existing realtime voice tool restrictions:

- Silent approval checks only.
- Pre-approved/exempt tools can run.
- Tools requiring interactive approval are rejected with a safe spoken/text response.
- Long-running tools return an immediate interim result and continue asynchronously when appropriate.
- Tool calls include session ID, user ID, conversation ID, and agent name for audit.

### 4. Session Event API

Expose a normalized PawFlow event stream independent of LiveKit internals. Event names use the `realtime.*` namespace and must be documented with the ConversationEventBus/SSE event schema when implemented:

```
realtime.session.created
realtime.session.ready
realtime.media.connected
realtime.user.transcript.delta
realtime.user.transcript.final
realtime.agent.transcript.delta
realtime.agent.transcript.final
realtime.agent.audio.started
realtime.agent.audio.stopped
realtime.tool.started
realtime.tool.completed
realtime.tool.rejected
realtime.usage
realtime.error
realtime.session.closed
```

Persist only final user/assistant transcript messages as normal conversation messages by default. Intermediate deltas are UI events only.

## UI Plan

### Agent Configuration UI

Add a `Realtime` section to the agent editor:

- Toggle: realtime enabled.
- Select: realtime service.
- Read-only summary of provider, model, voice, modalities, VAD, max duration.
- Warning if video is enabled but the selected provider/plugin does not support visual input.
- Save selected realtime service on the agent/conversation config.

### Conversation UI

When the selected agent has a realtime service:

- Show a call button next to the text composer.
- On click, open a live session panel/overlay.
- Controls: mute/unmute mic, camera on/off, screen share, stop.
- Show state: connecting, listening, thinking, speaking, tool running, reconnecting, ended.
- Show live captions for user and assistant.
- Show tool activity without exposing raw tool arguments unless already allowed in the normal UI.
- Persist final transcripts into the normal chat timeline.

Session start flow:

```
User clicks Live
  -> browser POST /api/realtime/livekit/start
  -> PawFlow authorizes user/conversation/agent
  -> PawFlow creates LiveKit room/session token
  -> browser joins LiveKit room
  -> PawFlow starts/dispatches LiveKit agent worker
  -> user media tracks stream via LiveKit
  -> transcript/audio/tool events return to PawFlow UI
```

### Flow Builder UI

Add a `RealtimeAgentTask` later, after chat UI works. Inputs:

- `agent_id` or `agent_name`.
- `realtime_service_id`.
- `modalities`.
- `session_policy`.
- Optional room name/session ID.

Outputs:

- Transcript.
- Session event log.
- Tool results.
- Optional recording FileStore references when explicitly enabled.

## Provider Strategy

The provider phases below are capability waves, not implementation phase numbers. The concrete execution order is defined later in `Implementation Phases` (`P0` through `P8`).

### Provider Wave 1

- OpenAI Realtime through LiveKit plugin.
- Gemini Live through LiveKit plugin.

OpenAI is the baseline for voice agents. Gemini Live is the baseline for true audio plus visual input. The P0 spike must validate both basic OpenAI voice and Gemini video-frame input before the old adapters are scheduled for removal.

### Provider Wave 2

- Azure OpenAI Realtime through LiveKit plugin or OpenAI-compatible config.
- xAI Grok Voice Agent through LiveKit plugin.
- AWS Nova Sonic through LiveKit plugin.

### Provider Wave 3: Cascade Providers

For providers without native realtime audio/video sessions, support a cascade mode later:

```
LiveKit audio/video input
  -> STT / vision frame extraction
  -> normal PawFlow LLM agent loop
  -> streaming TTS
  -> LiveKit audio output
```

Use this for Anthropic and other text-first LLMs. This is an explicit fallback mode, not the default for providers with native realtime support. Expected added latency is roughly 1-3 seconds versus sub-second native realtime paths, depending on STT finalization, LLM latency, and TTS time-to-first-audio.

Cascade mode should reuse existing PawFlow services: configured STT services for speech recognition, normal `llmConnection` services for the text agent loop, and configured TTS services for speech output. It should not introduce a separate STT/TTS registry.

### Local Pipeline Profile (zero-cloud-audio cascade)

Added 2026-07-16. A special case of cascade mode: run the entire voice loop with local components so no audio ever leaves the deployment — only the text turn goes to the configured `llmConnection`.

- Building blocks are standard LiveKit Agents plugins: Silero VAD, a local Whisper/faster-whisper STT plugin, a local TTS plugin (e.g. Kokoro), and the LiveKit turn-detector model for end-of-turn detection. Barge-in/interruption handling comes from the LiveKit `AgentSession`, exactly as for native realtime providers — PawFlow does not rebuild any of it.
- Works with any text-first `llmConnection`, including non-vision models: video frames extracted by LiveKit go through the existing vision fallback (`vision_llm_service`), so a text-only model can still "see" camera/screen input.
- PawFlow already ships local engines for the turn-by-turn walkie-talkie path (Voicebox/Whisper STT, Supertonic TTS). The gap this profile closes is the full-duplex streaming pipeline: continuous VAD, smart end-of-turn detection, sentence-by-sentence TTS while the LLM streams, and barge-in mid-word.
- The local plugins run inside the sidecar worker, not the PawFlow server process; the worker image must document model download/caching (VAD/STT/TTS weights) so first-call latency is predictable.
- Market validation: OpenLive (github.com/katipally/openlive, see `marketing/concurrent-openlive.md`) proves demand for exactly this shape — on-device voice loop, bring-your-own text model, delegated vision. Positioning: no audio upload, no per-minute metering, any model, pairs with the free-tier OOTB story (e.g. Ollama cloud GLM + local voice).

Configuration reuses the same realtime service shape with `provider: local_pipeline`; STT/TTS/VAD plugin choices are service config keys resolved by the worker.

## Relay and Credential Boundaries

The LiveKit worker does not need the PawFlow relay to access user files by default. It should call back into PawFlow for tools and context instead of mounting relay workspaces directly. This keeps the same authorization path as normal PawFlow tool execution.

Credential flow:

- PawFlow resolves `llm_service` or `provider_secret` server-side.
- PawFlow starts or configures the trusted worker with provider credentials through deployment secrets or a scoped worker bootstrap message.
- The worker never receives broad user secret-store access. It receives only the concrete provider credential required for the active session.
- Browser clients receive only LiveKit room tokens.
- Relay-local filesystem or shell access remains behind PawFlow tools and existing ACLs; realtime sessions do not get a direct relay bypass.

## Security Requirements

- Browser receives only a short-lived LiveKit room token, never provider secrets.
- LiveKit room token TTL defaults to `min(max_session_seconds + 60, 15 minutes)` and is never longer than the configured hard cap plus a small connection grace period.
- Provider credentials stay server-side in PawFlow/worker environment.
- Room tokens are scoped to one conversation, one user, and one session.
- Browser participant tokens grant only the minimum required room permissions: join room, publish local microphone/camera/screen tracks requested by the UI, and subscribe to the agent participant. They must not grant room admin permissions.
- Agent participant tokens are issued only to the trusted worker and scoped to the target room/session.
- Session start requires existing conversation authorization and selected agent validation.
- Camera and screen share are opt-in per session in the browser.
- Raw audio/video recording is off by default.
- Recording, if enabled, must write to FileStore with explicit metadata and retention policy.
- Tool calls must pass existing PawFlow permission checks.
- Force-stop must immediately disconnect the agent participant, ask LiveKit to remove participants from the room where supported, close the worker-control WebSocket, and end provider session state. A leaked browser token may remain cryptographically valid until TTL expiry, so TTL must stay short and stopped rooms must reject further work server-side.
- Usage and errors must be logged without secret values or raw provider auth headers.

## Testing Plan

No live provider calls in CI.

Unit tests:

- Service config validation rejects missing LiveKit URL, API key, API secret, provider secret, model, and invalid modalities.
- Agent without selected realtime service does not expose live controls.
- Token creation scopes room identity to user/conversation/session.
- Session registry enforces configured single-session policy per conversation.
- Tool bridge allows pre-approved tools and rejects approval-required tools.
- Transcript final events persist messages with UUID and timestamp.
- Force-stop closes session without poisoning the next session.
- Legacy config loader maps `protocol`, `llm_service`, `vad`, `instructions_mode`, `instructions`, audio format fields, and `context_mode` to the LiveKit engine config.

Integration tests with fakes:

- Fake LiveKit worker sends transcript/audio/tool/error events to PawFlow over the worker-control WebSocket.
- Browser start endpoint returns a token and room metadata.
- UI event stream receives normalized realtime events.
- Video-enabled config sends `video_input=True` to the worker config.
- Unsupported provider/modalities combinations fail early with actionable errors.
- Existing realtime tests are migrated as follows: keep service/config/tool/context/security tests against LiveKit fakes; remove old OpenAI/Gemini protocol-adapter tests only when those adapters are deleted.

Manual validation:

- OpenAI voice-only browser session.
- Gemini audio plus camera session.
- Gemini audio plus screen share session.
- Tool call from realtime session.
- Barge-in/interruption.
- Session stop and reconnect.

## Implementation Phases

### P0: Spike, Dependency, and Deployment Decision

P0 is intentionally a real technical spike, not a short paperwork phase. It should de-risk the whole stack before production code replaces the shipped custom bridge.

- Default deployment decision: sidecar worker plus LiveKit server or LiveKit Cloud.
- Decide local development default: Docker Compose should start a self-hosted LiveKit server and `pawflow-livekit-worker`.
- Add optional dependency group for LiveKit Agents to avoid forcing it into minimal installs.
- Build a technical spike outside the production path:
  - hello-world LiveKit Agent with OpenAI Realtime voice;
  - hello-world Gemini Live audio plus camera or synthetic video frame input;
  - worker-control WebSocket prototype from sidecar to PawFlow;
  - one fake PawFlow tool call round-trip.
- Record any LiveKit plugin capability gaps, especially Gemini session resumption, video sampling controls, and provider-specific tool behavior.

Acceptance:

- A documented local dev path exists.
- Missing LiveKit dependencies produce a clear setup error.
- The spike proves OpenAI voice, Gemini visual input, and worker-control tool round-trip before P1 starts.
- Any gap versus shipped custom realtime voice is written into the migration matrix.

P0 progress (2026-07-17):

- Landed: `pawflow[realtime-livekit]` optional dependency group; import guard `services/livekit_deps.py` with actionable setup error; docker-compose `realtime` profile (LiveKit dev server + `livekit-worker` sidecar, `docker/livekit-worker/Dockerfile`); spike scripts and runbook under `spikes/livekit/` (OpenAI voice, Gemini video + synthetic-frame publisher, worker-control WebSocket prototype with fake tool round-trip); protocol prototype `control_protocol.py` unit-tested in CI (`tests/test_livekit_spike_control.py`), local end-to-end round-trip verified. Also landed ahead of P7: local pipeline spike (`spike_local_pipeline.py` — Silero VAD + turn-detector + local OpenAI-compatible STT/TTS + any text LLM, the OpenLive-shaped zero-cloud-audio path) and `SPIKE_VIDEO=1` on the OpenAI spike (gpt-realtime image-input frame path).
- Remaining before P1 sign-off: run the OpenAI voice and Gemini video spikes against live endpoints (needs `OPENAI_API_KEY` / `GOOGLE_API_KEY`), and record plugin capability gaps (Gemini session resumption, video sampling controls, tool behavior) in the migration matrix and the spike README findings log. (Owner decision 2026-07-17: implementation continues through the phases; all live validation happens at the end.)

P1 progress (2026-07-17) — implemented:

- `engine: livekit` on `realtimeVoiceConnection` (`services/realtime_voice_service.py`), full config validation + UI schema keys; compatibility loader `services/_livekit_engine.py::resolve_livekit_config` maps legacy configs deterministically (`protocol`→`provider`, `vad`→`turn_detection`) and fails clearly on missing LiveKit settings.
- Scoped tokens (`services/_livekit_engine.py`, pyjwt — no LiveKit SDK server-side): browser room token (minimum grants, no admin, TTL `min(max+60s, 15min)`), agent room token, PawFlow-signed worker-control token (SecretsManager subkey, audience + session scoped).
- Session registry + API (`services/_livekit_sessions.py`): `POST /api/realtime/livekit/start`/`stop` (owner/admin only), one active session per conversation (newcomer supersedes), force-stop wired into `cancel_interrupt.py`, `realtime.*` events published on the ConversationEventBus.
- Worker-control WS endpoint `/ws/realtime-worker/{session_id}` (public route, fails closed on token mismatch), protocol promoted to `services/_realtime_worker_protocol.py` (spike re-exports it). Worker `tool_call` gets an explicit not-wired-yet refusal until P2.
- Tests: `tests/test_livekit_engine.py` (40) + spike protocol tests — 153 realtime/livekit tests green. Docs: `services.md`, `security_model.md`.

P2 progress (2026-07-17) — implemented (live provider runs pending, per owner decision):

- Worker bootstrap: `POST /api/realtime/livekit/worker/bootstrap` (deployment-secret header `PAWFLOW_REALTIME_WORKER_SECRET`, 503 when unset, room→session lookup) returns control token, agent room token, resolved instructions (same `instructions_mode`/`context_mode` resolver as the legacy bridge), tool definitions, and server-side-resolved provider credentials (`llm_service` first, `provider_secret` env passthrough otherwise) — never sent to the browser.
- Tool bridge wired: worker `tool_call` messages run through the existing `RealtimeToolBridge` (silent approval, long tools detach → `context` message to the live session or system-message persistence), `realtime.tool.started/completed/rejected` published.
- Transcript persistence: `realtime.user/agent.transcript.final` events persist as normal conversation messages via `persist_voice_transcript` (UUID+ts, SSE fan-out); deltas stay UI-only.
- Sidecar worker `pawflow_livekit_worker/`: `control_client.py` (LiveKit-free, CI-tested, contract-pinned to the server protocol) + `worker.py` (automatic LiveKit dispatch → bootstrap fetch → provider `AgentSession` for openai/gemini/local_pipeline → proxy function tools → event mirroring → `max_session_seconds` cap + shutdown handling). Worker image now runs `python -m pawflow_livekit_worker`.
- Tests: `tests/test_livekit_worker_p2.py` (13) — 166 realtime/livekit tests green.

P3 progress (2026-07-17) — implemented (live browser validation pending, per owner decision):

- Vendored `livekit-client` 2.20.1 UMD (`tasks/io/chat_ui/vendor/`, Apache-2.0, THIRD_PARTY_NOTICES.md) served session-authenticated at `GET /api/realtime/livekit/sdk.js`, lazy-loaded on first live call.
- `tasks/io/chat_ui/conversation_livekit.js`: WebRTC live panel over the shared voice overlay (orb/state/captions/tool line), mic publish + agent audio subscribe via the LiveKit room, mute, camera and screen-share buttons gated by the service's `video_input`/`video_source`, captions/state/tool activity from `realtime.*` SSE events (session-id filtered), stop → `POST /api/realtime/livekit/stop`.
- Engine routing: `list_realtime_services` now exposes `engine`/`provider`/`video_input`/`video_source`; the existing mic button routes `engine: livekit` services through the LiveKit path and legacy services through the unchanged PCM bridge. Deviation from the plan text, on purpose: the global default flip + `PAWFLOW_ENABLE_LEGACY_REALTIME_BRIDGE` gate moves to P5, because P2/P3 acceptance (live browser sessions) is deferred to final validation — flipping the default before parity is proven live would break shipped voice.
- Voice settings panel shows a LiveKit/provider badge (+🎥 when video). i18n keys added (en/fr/es).
- Tests: `tests/test_livekit_ui.py` (8, house static-introspection pattern + SDK endpoint) — 241 realtime/livekit/chat-ui tests green.

### P1: Service and Session API

- Add LiveKit engine support to the existing realtime service model.
- Keep any temporary `livekitRealtimeConnection` name internal and migration-only.
- Implement compatibility loader for legacy realtime configs: `protocol`, `llm_service`, `vad`, `instructions_mode`, `instructions`, audio format fields, `context_mode`, `tool_profile`, and `max_session_seconds`.
- Add session start/stop backend API.
- Add scoped LiveKit room token generation.
- Add scoped worker-control token generation.
- Add active session registry.
- Add unit tests and documentation.

Acceptance:

- UI/backend can create a LiveKit room token for an authorized conversation.
- Worker can open a scoped control WebSocket for the same session.
- Unauthorized access is rejected.
- Legacy service configs map deterministically or fail clearly.
- Stop closes the PawFlow session registry entry.

### P2: Worker MVP with OpenAI Realtime and Tools

- Add LiveKit sidecar worker module/container.
- Start one OpenAI Realtime voice session through LiveKit.
- Map agent instructions, custom instructions, and bounded context.
- Wire LiveKit tool calls to existing PawFlow `RealtimeToolBridge` through the worker-control WebSocket.
- Forward final transcripts to ConversationStore.
- Forward normalized realtime state events to the UI.
- Surface tool state in live panel events.
- Persist tool activity in provenance/audit logs.

Acceptance:

- Browser voice session works with OpenAI through LiveKit.
- Final transcripts appear as normal messages.
- Pre-approved tools execute from live sessions.
- Approval-required tools are refused safely.
- Tool results return to the provider session.
- Audio is not persisted.

### P3: UI Live Panel and Default Route Switch

- Replace the conversation live-call transport with the LiveKit client SDK.
- Keep the existing selected-agent realtime service link.
- Add/adjust realtime service selection in agent editor.
- Add call button for realtime-enabled selected agents.
- Add live panel controls: mic, stop, captions.
- Add camera and screen-share controls gated by `video_input`.
- Route the existing webchat live button to LiveKit by default after P2 acceptance.
- Keep old bridge only behind `PAWFLOW_ENABLE_LEGACY_REALTIME_BRIDGE=1`.

Acceptance:

- Users can start and stop a live agent session from the conversation UI.
- Captions and final chat messages stay consistent.
- Camera/screen controls are hidden unless service config enables video.
- Legacy bridge is no longer the default path.

### P4: Gemini Live Video and Resumption Gap Closure

- Add Gemini Live provider config through LiveKit plugin.
- Enable `video_input=True`.
- Add active/idle frame sampling settings when LiveKit exposes them; otherwise document plugin defaults and provider limits.
- Validate camera and screen-share sessions manually.
- Resolve the Gemini session resumption row in the migration matrix: either LiveKit covers it, or PawFlow accepts the behavior change before deleting the custom adapter.

Acceptance:

- Gemini session receives audio plus camera/screen frames.
- User can interrupt and continue naturally.
- Frame sampling is configurable or the LiveKit/plugin default is documented.
- Resumption behavior is explicitly covered or accepted as a dropped feature.

### P5: Retire Custom Realtime Bridge

- Delete or disable PawFlow-native OpenAI realtime protocol adapter.
- Delete or disable PawFlow-native Gemini Live protocol adapter after P4 gap closure.
- Delete or disable custom browser raw PCM websocket bridge for live calls.
- Convert remaining tests to LiveKit fakes or remove tests that only covered deleted protocol adapters.
- Update docs to mark the old direct bridge removed.

Acceptance:

- No default code path uses the custom provider protocol adapters.
- CI covers LiveKit service config, worker-control events, tool bridge, context injection, transcripts, auth, and force-stop.
- Deleted adapters have no orphan imports or docs advertising them as active.

### P6: Additional Providers

- Add Azure OpenAI realtime config.
- Add xAI voice config.
- Add AWS Nova Sonic config.
- Keep provider-specific code in LiveKit plugin config mapping only.

Acceptance:

- New providers do not require new PawFlow media transport code.
- Provider capability matrix is documented.

### P7: Cascade Fallback Mode

- Add explicit cascade mode for text-first/non-realtime providers.
- Reuse existing PawFlow STT, `llmConnection`, and TTS services.
- Surface cascade mode as higher-latency fallback in UI/config.
- Preserve interruption semantics as best-effort only.
- Add the local pipeline profile (`provider: local_pipeline`): Silero VAD + local Whisper STT + local TTS (e.g. Kokoro) + LiveKit turn-detector model in the sidecar worker; document model download/caching.

Acceptance:

- Anthropic or another text-first LLM can run through STT -> LLM -> TTS.
- UI labels cascade sessions as fallback/high-latency.
- Native realtime providers still use LiveKit provider plugins instead of cascade.
- A text-only `llmConnection` can hold a full-duplex voice session with barge-in where no audio leaves the deployment, and non-vision models see video frames through the vision fallback.

### P8: Recording and Provenance

- Add explicit recording policy support.
- Store recordings only when opted in.
- Link recordings to session metadata and transcript messages.
- Add retention and deletion docs.

Acceptance:

- Default remains transcript-only.
- Audio/video recording requires explicit configuration.


## Capability Matrix

| Provider path | Audio in/out | Video/frames | Tools | Recommended role |
|---|---:|---:|---:|---|
| OpenAI Realtime via LiveKit | yes | yes — gpt-realtime accepts image input; LiveKit samples the video track (~1 fps) and forwards frames as images. No provider-native continuous video; each frame bills as image-input tokens | yes | primary voice baseline, video capable |
| Gemini Live via LiveKit | yes | yes — provider-native continuous video ingestion | yes | primary multimodal baseline (cheapest/most proven video path) |
| Azure OpenAI Realtime via LiveKit | yes | likely same as OpenAI path where supported | yes | enterprise OpenAI deployments |
| xAI Voice Agent via LiveKit | yes | no initial target | yes | alternate voice provider |
| AWS Nova Sonic via LiveKit | yes | no initial target | limited/provider-dependent | AWS voice workloads |
| ElevenLabs | TTS/agent voice | no initial target | provider-dependent | separate TTS or voice layer |
| Anthropic cascade | via STT/TTS | via extracted frames | PawFlow tools | text-first fallback |
| Local pipeline (LiveKit local VAD/STT/TTS plugins + any `llmConnection`) | yes, fully local | via extracted frames + vision fallback | PawFlow tools | private zero-cloud-audio voice for any text model |

## Migration Plan from Existing Realtime Voice

The goal is replacement, not coexistence. `realtimeVoiceConnection` remains only until the LiveKit engine reaches parity for the shipped voice features. After parity, new sessions should use LiveKit by default, old custom adapters should be deprecated, and then removed in the same cleanup window.

Required migration steps:

1. Inventory the current realtime voice surface: OpenAI adapter, Gemini adapter, bridge route, browser mic/playback UI, tool bridge, context injection, session resumption, Telegram one-shot voice turn, tests, and docs.
2. Implement LiveKit behind the existing realtime service shape with `engine: livekit`, preserving the same agent-level `realtime_voice_service` link where possible.
3. Add a compatibility loader that maps old service configs to the LiveKit engine when the provider is covered by LiveKit. Missing required LiveKit settings must fail clearly; no anonymous/default fallback.
4. Route the existing webchat live button to the LiveKit session API once parity tests pass.
5. Keep the old bridge callable only behind a temporary feature flag such as `PAWFLOW_ENABLE_LEGACY_REALTIME_BRIDGE=1`. Default must be LiveKit.
6. Remove custom provider adapters and browser WS audio bridge after one release window or earlier if zero backward compatibility is accepted for this migration.
7. Update `docs/REALTIME_VOICE_PLAN.md` to mark the custom bridge as superseded by this plan.

Code targeted for retirement after parity:

- PawFlow-native OpenAI realtime protocol adapter.
- PawFlow-native Gemini Live protocol adapter, unless LiveKit lacks a needed capability.
- Custom browser-to-PawFlow raw PCM websocket bridge for live calls.
- Any duplicated VAD/interruption/media sampling logic that LiveKit already owns.

Code to preserve and reuse:

- Agent/service selection and config storage.
- Conversation persistence and event fan-out.
- Realtime tool bridge and approval policy.
- Context injection policy.
- Session caps, force-stop registry semantics, and audit logging.
- Telegram one-shot voice behavior only if LiveKit cannot cover it directly; otherwise migrate it too.

## Documentation Updates Required During Implementation

- `docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md` as the plan of record.
- `docs/services.md` for the LiveKit-backed realtime service engine.
- `docs/tasks.md` when `RealtimeAgentTask` is added.
- `docs/02_REFERENCE_TASKS_SERVICES.md` for service/task reference.
- `docs/security_model.md` for realtime media permissions and token scoping.
- `docs/REALTIME_VOICE_PLAN.md` marked as superseded for future implementation, with a pointer to this replacement plan.

## Decisions and Remaining Open Questions

Decided before implementation:

- Local development default: Docker Compose should include a self-hosted LiveKit server and a `pawflow-livekit-worker` sidecar. LiveKit Cloud remains a production option, not the only documented path.
- Worker topology: sidecar worker is the default. In-process LiveKit Agents is not the target because of asyncio/runtime coupling with PawFlow's sync/threaded server.
- Worker communication: dedicated worker-control WebSocket to PawFlow, not HTTP-only callbacks and not Redis for v1.

Remaining open questions:

- Should one conversation allow multiple simultaneous live sessions, or enforce one active live session per conversation? Initial recommendation: one active live session per conversation, matching current force-stop/session registry semantics.
- What is the minimum UI needed before enabling video: camera only, screen share only, or both? Initial recommendation: ship camera first, then screen share in the same phase if LiveKit client wiring is straightforward.
- Which providers should be exposed in the first UI dropdown: OpenAI and Gemini only, or all LiveKit plugins discovered at runtime? Initial recommendation: OpenAI and Gemini only until provider capability tests exist.
- Does the LiveKit Gemini plugin expose enough session resumption behavior to match the shipped custom Gemini adapter? Resolve during P0/P4 before deleting the custom adapter.
- Can Telegram one-shot voice be migrated cleanly through LiveKit, or should it remain isolated until a later cleanup? Resolve during P5.
