# Realtime Voice Conversation — Design & Implementation Plan

Status: **P1 + P2 shipped** (released in 1.0.0-beta.2); **P3 shipped** (released in 1.0.0-beta.3) — context injection, gemini_live adapter, session resumption, voice settings UI (gemini_live pending a live-endpoint validation). Superseded for future realtime implementation by `REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`; the custom bridge is a migration source, not the long-term target.
Decided: 2026-07-02 — realtime voice is delivered as a new LLM-family service
type `realtimeVoiceConnection`, multi-provider through protocol adapters.

## Goal

Full-duplex voice conversation with a PawFlow agent: the user talks, the
agent answers with streamed audio, the user can interrupt (barge-in), and
the exchange is a first-class PawFlow conversation — transcripts persisted,
visible live in every attached client, usable by `/compact`, memory
extraction, and the Telegram bridge.

This is NOT the existing STT→text-agent→TTS walkie-talkie (that stays as
is). A realtime voice model (OpenAI `gpt-realtime`, Gemini Live, ...) is a
speech-to-speech LLM living inside a bidirectional session; the session — not
a request/response call — is the unit of work.

## Existing foundations (verified in code)

| Piece | Where | Reused for |
|---|---|---|
| WS upgrade + session auth (`?token=`/cookie) + gateway checks | `services/_http_server.py` (RFC 6455 handshake, `entry.ws_handler(sock, path_params, meta)` with `auth_user_id`) | the browser-facing `/ws/realtime/...` route |
| Route registration with `ws_handler` | `RouteRegistry.register(...)` in `services/_http_base.py`; examples in `tasks/ai/actions/_sf_routes.py` | same |
| Sync WS frame I/O on raw sockets | `services/audio_proxy.py` (`_ws_recv`, `_ws_send_binary`, `_ws_close`) | browser leg of the bridge |
| Hand-rolled WS **client** handshake | `pawflow_relay/_relay_conn.py`, `_relay_codeserver.py` | provider leg (WSS client, no new dependency) |
| Downlink audio player | `tasks/io/chat_ui/audio.js` (WS → decoder → SharedArrayBuffer ring → AudioWorklet) | agent voice playback |
| Mic capture worklet | `tasks/io/chat_ui/conversation_stt.js` (WAV/PCM worklet) | continuous mic streaming |
| Tool dispatch outside the agent loop | `core/tool_registry.py::execute(name, arguments)` + `core/tool_approval.py` | provider function calls |
| Transcript persistence + fan-out | `ConversationStore.append_message` (UUID+timestamp) + `ConversationEventBus.publish_event` | conversation integration |
| Service type registration | `TYPE` attr + `ServiceFactory.register` (`core/__init__.py`), `llm_service` reuse pattern from `services/openai_compatible_media_service.py` | the new service |

The only genuinely new brick is the provider session bridge.

## Architecture

```
browser ⇄ WS /ws/realtime/{conversation_id}?token=…&service=…&agent=…
  ↑ binary frames: mic PCM16 chunks (uplink)
  ↓ binary frames: agent audio PCM16 (downlink)
  ⇅ text frames: JSON control events (transcripts, state, errors)
            │
   RealtimeSessionBridge (1 per session, threads like audio_proxy)
            │        │        │
   ToolRegistry   Conversation  budget/duration caps,
   .execute       Store+EventBus  force-stop
            │
   protocol adapter (RealtimeAdapter interface)
            │
   provider WSS (openai_realtime | gemini_live | …)
```

### 1. Service type: `realtimeVoiceConnection`

`services/realtime_voice_service.py`, `TYPE = "realtimeVoiceConnection"`,
registered with `ServiceFactory`. It is an LLM-family service but NOT a new
provider inside `LLMConnectionService`: `generate()` is request/response
while a realtime session is a stateful object with a lifecycle. Like the
media services, it references an existing `llmConnection` for credentials.

Config schema (P1):

| Key | Default | Notes |
|---|---|---|
| `llm_service` | required | id of the `llmConnection` supplying `api_key` + `base_url` |
| `protocol` | `openai_realtime` | adapter selector: `openai_realtime` or `gemini_live` |
| `model` | required | e.g. `gpt-realtime`, `gpt-4o-realtime-preview` |
| `voice` | `alloy` | provider voice id |
| `instructions_mode` | `agent` | `agent` = use the conversation agent's system prompt; `custom` = `instructions` field |
| `instructions` | `""` | used when `instructions_mode=custom` |
| `input_audio_format` / `output_audio_format` | `pcm16` | provider-side formats |
| `vad` | `server` | `server` (provider VAD) or `manual` (client push-to-talk commits) |
| `max_session_seconds` | `600` | hard cap, bridge closes the session |
| `tool_profile` | `""` (none) | P2: comma list / profile of PawFlow tools exposed as functions |
| `context_mode` | `summary:2000` | P3: conversation context injected into the session instructions — the shared sub-agent vocabulary (`isolated`/`last:N`/`summary:N`/`full`, resolved by `core.handlers.spawn_agents.resolve_context_messages`) |

Service methods: `open_session(session_config) -> RealtimeAdapter` plus
`describe()`/health. Everything session-scoped lives on the adapter.

### 2. Adapter interface (multi-provider seam)

`services/_realtime_adapters.py`:

```python
class RealtimeAdapter:
    def connect(self, *, model, voice, instructions, tools, vad,
                input_format, output_format) -> None: ...
    def send_audio(self, pcm_chunk: bytes) -> None      # uplink mic audio
    def commit_input(self) -> None                      # manual-VAD end of turn
    def send_tool_result(self, call_id: str, result: str) -> None
    def interrupt(self) -> None                         # barge-in: cancel current response
    def recv_event(self, timeout: float) -> dict | None # normalized event, see below
    def close(self) -> None
```

Normalized events (the bridge consumes ONLY these):

| type | payload | meaning |
|---|---|---|
| `audio` | `data: bytes` (PCM16 out) | agent speech chunk |
| `transcript_user` | `text, final: bool` | user speech transcript |
| `transcript_agent` | `text, final: bool` | agent speech transcript |
| `speech_started` | — | provider VAD detected user speech (drives barge-in) |
| `response_done` | `usage: dict` | turn finished (tokens for cost tracking) |
| `tool_call` | `call_id, name, arguments` | provider function call |
| `error` | `message, fatal: bool` | provider error |

`OpenAIRealtimeAdapter` (P1): hand-rolled WSS client (house style — TLS via
`ssl.create_default_context`, RFC 6455 client handshake with masked frames,
mirroring `pawflow_relay/_relay_conn.py`). Protocol mapping:
`session.update`, `input_audio_buffer.append` (base64 PCM16),
`input_audio_buffer.commit`, `response.cancel`,
`conversation.item.create` (function_call_output) + `response.create`;
reads `response.output_audio.delta`, `response.output_audio_transcript.*`,
`conversation.item.input_audio_transcription.*`,
`input_audio_buffer.speech_started`, `response.done`,
`response.function_call_arguments.done`, `error`. This one adapter covers
OpenAI, Azure OpenAI, and every OpenAI-realtime-compatible endpoint via the
`llmConnection.base_url` (`https://…` → `wss://…/realtime?model=…`).

`GeminiLiveAdapter` (`services/_realtime_gemini.py`, shipped): the
`BidiGenerateContent` WS protocol. PCM16 16 kHz in (the adapter resamples
PawFlow's 24 kHz uplink — pure-Python linear interpolation, no new
dependency) / 24 kHz out (matches the downlink verbatim).
`toolCall`/`toolResponse` map onto the same normalized `tool_call` events;
transcription deltas accumulate and flush as finals on
`turnComplete`/`interrupted` (user before agent — the bridge ordering
contract); `interrupted` maps to `speech_started` and `interrupt()` is a
no-op (barge-in is server-side). `sessionResumptionUpdate` handles are
captured and exposed through `resumption_state()`. Credentials come from
a `gemini` llmConnection (`api_key` required).

### 3. Browser ⇄ PawFlow WS contract

Route: `GET /ws/realtime/{conversation_id}` — registered with a `ws_handler`,
NOT public → `_http_server` enforces session auth (cookie or `?token=`) and
the private gateway. Query params: `service` (realtimeVoiceConnection id),
`agent` (conversation agent name).

Frames:
- **binary uplink**: raw PCM16 mono mic chunks (24 kHz little-endian — the OpenAI pcm16 native rate, relayed verbatim;
  the bridge forwards to the adapter which resamples/encodes if needed).
- **binary downlink**: raw PCM16 mono 24 kHz agent audio chunks.
- **text frames**: JSON control events, `{"type": …}`:
  - client→server: `start` (optional overrides), `commit` (manual VAD —
    the overlay shows a "Send" button when the session is manual),
    `interrupt`, `stop`.
  - server→client: `ready` (`{state, vad}` — `vad: "manual"` makes the
    client show the push-to-talk Send control), `transcript_user`,
    `transcript_agent` (`{text, final}` — drives live captions),
    `speech_started` (flush local playback for barge-in), `state`
    (`listening|thinking|speaking`), `usage`, `error`, `closed` (`{reason}`).

### 4. RealtimeSessionBridge

`services/_realtime_bridge.py`. One instance per accepted WS connection,
blocking-thread model exactly like `audio_ws_proxy`:

- **pump A** (handler thread): browser WS → `adapter.send_audio` / control.
- **pump B** (worker thread): `adapter.recv_event()` → browser WS + side
  effects:
  - `audio` → binary downlink;
  - `speech_started` → `adapter.interrupt()` + `speech_started` to client
    (client flushes its ring buffer) — barge-in;
  - final transcripts → `ConversationStore.append_message` (role `user` /
    `assistant`, UUID + timestamp, `meta.voice=true`) and
    `ConversationEventBus.publish_event` so webchat/Telegram/PawCode see the
    exchange live;
  - `tool_call` (P2) → `ToolRegistry.execute` honoring `tool_approval`,
    result via `send_tool_result`; long-running tools are NOT awaited —
    profile restricts to fast tools, long ones get a delegated background
    task and an immediate interim result;
  - `response_done.usage` → token/cost tracking; enforce budget cap;
  - `max_session_seconds` / `stop` / force-stop → `adapter.close()`,
    `closed` to client. Force stop kills the session and NEVER poisons the
    next one (project convention).
- Session registry keyed by conversation_id for force-stop and single
  active voice session per conversation.

Context at session start: system prompt of the selected agent
(`instructions_mode=agent`) + a compact recent-conversation summary; realtime
sessions have small contexts, so we inject a bounded digest, not the whole
transcript.

### 5. Web chat UI (P1)

`tasks/io/chat_ui/conversation_voice.js` + a mic-wave button next to the
STT button:
- continuous capture: reuse the STT worklet path, downsample to PCM16
  24 kHz (OpenAI pcm16 native rate), ship ~40 ms binary chunks on the WS;
- playback: dedicated `AudioContext` + worklet ring buffer (same pattern as
  `audio.js`), fed by binary downlink; `speech_started` flushes the ring;
- live captions from `transcript_*` events; `state` drives the button UI;
- stop button + auto-stop on `closed`/error; transcripts appear as normal
  messages via the existing SSE path (no special rendering needed).

### 6. Security

- `/ws/realtime/...` is session-authenticated + gateway-checked by the
  existing `_http_server` WS path; the route is added to
  `test_route_security_matrix`.
- The provider WSS URL derives from the `llmConnection.base_url` — the same
  SSRF posture as the media services (private base URLs refused unless
  explicitly allowed there; realtime providers are public endpoints).
- No raw audio persisted by default (transcripts only). Audio recording to
  FileStore is a possible later opt-in.
- Per-session caps: `max_session_seconds` + provider usage tracked per turn.

### 7. Testing (no live API in CI)

- **Adapter**: unit tests against a local fake WS server (thread +
  `socketserver` using the same frame helpers) speaking recorded
  openai-realtime event sequences: handshake, session.update, audio append,
  transcript deltas, function call round-trip, error, close.
- **Bridge**: fake adapter (list of scripted events) + fake browser socket →
  assert transcripts persisted with UUID/timestamp, events published,
  barge-in interrupts, caps enforced, force-stop clean.
- **Route**: registration + auth matrix entry.
- **UI**: static introspection tests (house pattern) on
  `conversation_voice.js` wiring.

## Phasing

- **P1 — SHIPPED**: `realtimeVoiceConnection` service + OpenAI realtime
  adapter + bridge + `/ws/realtime` route + webchat voice mode + transcripts
  + caps. No tools.
- **P2 — SHIPPED**:
  - **P2a tools**: `tool_profile` → `RealtimeToolBridge`
    (`services/_realtime_tools.py`). Approval is SILENT
    (`ToolApprovalGate.check(allow_prompt=False)`): exempt/pre-approved
    tools run, anything needing a dialog is refused with a spoken-friendly
    message; `permission_mode` auto/read_only honored. Long tools detach
    past a soft timeout — interim result immediately, real result injected
    back via `adapter.inject_context()` (or persisted as a system message
    if the session ended). Voice approval ("yes go ahead") is deliberately
    NOT implemented: the user transcript is an injection vector; revisit
    with a confirmation UX.
  - **P2b voice-native agents**: `realtime_voice_service` in the agent conv
    config (editable in the webchat agent editor); `list_realtime_services`
    returns `{services, linked}` — a linked agent pins its service. Webchat
    voice mode is a full-screen overlay: state-reactive orb
    (connecting/listening/thinking/speaking/tool), live captions, tool
    activity, mute, hang-up.
  - **P2c Telegram**: voice notes from a linked agent run a one-shot
    speech-to-speech turn (`services/_realtime_turn.py`, manual VAD,
    ffmpeg OGG/Opus ⇄ PCM16 24k). The reply is a `sendVoice` note; the
    transcript reaches Telegram as text via the live bridge (user side
    tagged `channel=telegram` to prevent echo; bridge TTS skipped for
    voice-channel messages). Any failure falls back to the STT pipeline.
    True duplex over Telegram is NOT possible via the Bot API — would need
    MTProto group calls (tgcalls); parked as exploratory.
- **P3 — SHIPPED**:
  - **Context injection**: the service's `context_mode` (default
    `summary:2000`; `isolated` disables) appends conversation context to
    the session instructions in both instruction modes, reusing the
    shared sub-agent context system (`resolve_context_messages`). The
    block carries an explicit treat-as-data guard (persisted content is
    untrusted).
  - **`gemini_live` adapter**: see §2 — second protocol through the same
    seam, `gemini` llmConnection credentials, adapter-side 24k→16k uplink
    resampling. Not yet validated against the live Google endpoint.
  - **Session resumption**: the bridge's provider pump reconnects
    transparently when the dropped adapter carries a resumption handle
    (`resumption_state()`, Gemini Live); mic chunks sent during the gap
    are dropped, the client sees `state: connecting` then `listening`.
    The 2-attempt budget re-arms once the resumed connection delivers
    events (Gemini's periodic goAway rotations are normal — only
    back-to-back failed resumes end the session). Protocols without
    handles keep the original `provider_closed` teardown. Known debt: a
    drop mid-turn loses that turn's unflushed partial transcripts (they
    live in the dead adapter's accumulators).
  - **Voice settings UI**: right-click on the webchat mic button opens a
    settings panel — every realtime service with what it will do (model,
    voice, VAD mode, context_mode from `list_realtime_services`), one
    click selects, the pick persists per conversation (localStorage).
- **Later**: Nova Sonic (HTTP/2 bidi), WebRTC transport option,
  SIP/telephony, voice approval UX, Telegram group calls (tgcalls).
