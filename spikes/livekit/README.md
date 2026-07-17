# LiveKit Realtime Spike (P0)

Technical spike for `docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`, outside the
production path. Goal: de-risk the stack before P1 — OpenAI voice through
LiveKit, Gemini Live video-frame input, and the worker-control WebSocket
tool round-trip.

## Setup

```bash
pip install "pawflow[realtime-livekit]"   # or: pip install -e ".[realtime-livekit]"
```

Missing dependencies produce a clear error from `services/livekit_deps.py`
telling you exactly that command.

Local LiveKit server + worker container (Linux, host networking):

```bash
docker compose --profile realtime up livekit          # dev server on ws://localhost:7880
docker compose --profile realtime up livekit-worker   # runs the OpenAI voice spike
```

Dev credentials are LiveKit's well-known `devkey` / `secret` pair. Export
them to run spikes on the host directly:

```bash
export LIVEKIT_URL=ws://localhost:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret
```

## 1. OpenAI Realtime voice

```bash
export OPENAI_API_KEY=...
python spikes/livekit/spike_openai_voice.py dev
```

Join the room with the [LiveKit Agents Playground](https://agents-playground.livekit.io/)
pointed at your server (or any LiveKit client). Expected: the agent greets
you, answers by voice, and you can interrupt it mid-sentence (barge-in is
handled by the `AgentSession`, no PawFlow code involved).

`SPIKE_VIDEO=1` also enables video-frame input for OpenAI: gpt-realtime
accepts image input and LiveKit forwards sampled frames (~1 fps) from the
video track. Same validation as the Gemini spike (color-cycling square via
`publish_synthetic_video.py`). Cost caveat: every sampled frame is billed
as image-input tokens — Gemini Live remains the cheaper native video path.

## 2. Gemini Live with video

```bash
export GOOGLE_API_KEY=...
python spikes/livekit/spike_gemini_video.py dev
```

Join with a camera client, or publish synthetic frames from another shell:

```bash
python spikes/livekit/publish_synthetic_video.py <room-name>
```

Ask out loud: “what color is the square?” — the square cycles
red/green/blue every 3 seconds, so a correct, changing answer proves real
frame ingestion (not a cached description).

## 3. Local pipeline (zero-cloud-audio, OpenLive-shaped)

Validates the plan's `provider: local_pipeline` profile — the
[OpenLive](https://github.com/katipally/openlive)-shaped path: no audio
leaves the deployment, only the text turn reaches the LLM.

```bash
# local OpenAI-compatible servers, e.g.:
#   STT: speaches / faster-whisper-server on :8001
#   TTS: kokoro-fastapi on :8002
#   LLM: Ollama on :11434 (or any llmConnection-style endpoint)
python spikes/livekit/spike_local_pipeline.py dev
```

Expected: Silero VAD + LiveKit turn-detector give real full-duplex
(barge-in mid-word, TTS starts sentence-by-sentence while the LLM streams);
network inspection shows zero audio egress. Findings to record: first-call
latency after weight download, end-of-turn quality in French, added latency
vs the OpenAI Realtime spike.

## 4. Worker-control WebSocket + fake tool round-trip

No LiveKit or provider needed — pure protocol prototype
(`control_protocol.py`, the contract the P1 endpoint will implement):

```bash
python spikes/livekit/spike_control_server.py            # fake PawFlow side, port 8899
python spikes/livekit/spike_worker_control.py            # sidecar side
```

Expected output: `handshake OK`, `tool round-trip OK in X ms`, then
`worker-control spike PASSED`. The protocol logic is unit-tested in CI
(`tests/test_livekit_spike_control.py`).

## Control-plane bench (no provider key)

`bench/` runs the REAL worker against a local LiveKit server and a fake
PawFlow control plane — validates dispatch → bootstrap fetch → control WS
handshake → media/session events without any provider credential:

```bash
cd /tmp && curl -sL https://github.com/livekit/livekit/releases/download/v1.9.1/livekit_1.9.1_linux_amd64.tar.gz | tar xz
./livekit-server --dev --bind 127.0.0.1 &
python spikes/livekit/bench/fake_pawflow.py &                 # port 8898
PAWFLOW_URL=http://127.0.0.1:8898 PAWFLOW_REALTIME_WORKER_SECRET=benchsecret \
  LIVEKIT_URL=ws://127.0.0.1:7880 LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=secret \
  python -m pawflow_livekit_worker start &
python spikes/livekit/bench/driver.py                          # prints TIER1 PASSED/FAILED
```

### Local pipeline bench (zero-cloud-audio)

`bench/local_stt_server.py` (faster-whisper) and `bench/local_tts_server.py`
(piper + ffmpeg) are minimal OpenAI-compatible stand-ins for
speaches/kokoro-fastapi. With them running on :8001/:8002, start the fake
control plane and worker with `BENCH_PROVIDER=local_pipeline` and run
`driver2.py` on a fresh `BENCH_ROOM`: Silero VAD + turn-detector + local
Whisper STT + text LLM + local TTS, with only the text turn leaving the
machine. Extra deps: `pip install livekit-plugins-turn-detector
faster-whisper piper-tts` + a piper voice, then
`python -m livekit.agents download-files`.

### Provider-leg bench

With `OPENAI_API_KEY` exported, `bench/driver2.py` exercises the real
provider leg (tier 2): it synthesizes a spoken instruction with the OpenAI
TTS API, publishes it as the user's mic track, and asserts the user
transcript events, the `echo` tool round-trip through the control plane,
and a non-silent agent audio reply. Use a fresh `BENCH_ROOM` per run —
reusing a room name against a live worker skips the track subscription.

## Findings log

Record plugin capability gaps here as the spike runs (Gemini session
resumption, video sampling controls, tool behavior differences) — they feed
the migration matrix in the plan.

- 2026-07-17 (tier-1 bench, livekit-agents 1.6.5, livekit-server 1.9.1): full control-plane chain PASSED headless — worker dispatch, bootstrap fetch (secret-authenticated), worker-control hello/hello_ack, `realtime.media.connected` + `realtime.agent.state` events, agent participant joined the room. Deprecation warnings to track for a future livekit-agents bump: `metrics_collected` → `session_usage_updated`, `RoomInputOptions`/`RoomOutputOptions` → `RoomOptions` (both still functional in 1.6.5, worker unchanged for now).
- 2026-07-17 (tier-2 bench, gpt-realtime via `OPENAI_API_KEY`, server restarted on 1.0.0-beta.24): PASSED — spoken instruction (OpenAI TTS 24 kHz PCM published as mic track) produced `realtime.user.transcript.delta/final` events, a provider `tool_call` for `echo` with a successful `tool_result` round-trip through the worker-control WS, `realtime.agent.transcript.final`, and a non-silent agent audio reply in the room (1000+ frames). The full P1/P2 provider leg works end-to-end with real audio. Also confirmed: the beta.24 secrets-cache fix delivers newly added secrets to the relay env without a further restart.
- 2026-07-17 (local pipeline bench): PASSED — full zero-cloud-audio loop with Silero VAD + LiveKit turn-detector + local faster-whisper STT (perfect transcription of the spoken instruction) + `gpt-4o-mini` text turn + local piper TTS, including the `echo` tool round-trip and a real agent voice reply in the room. Two findings fixed in the worker: (1) the OpenAI TTS plugin selects its wire format from the MODEL NAME — any name other than `tts-1`/`tts-1-hd` switches to SSE streaming, which kokoro-fastapi/speaches do not implement (symptom: `no audio frames were pushed`); worker default changed from `kokoro` to `tts-1`. (2) The local TTS server should output 24 kHz to match the plugin's expected sample rate. Also noted: `livekit.plugins.turn_detector` is deprecated in favor of `livekit.agents.inference.TurnDetector` (still functional in 1.6.5).
- 2026-07-17 (tier-3 bench, Gemini Live video — `bench/driver3.py`, `BENCH_PROVIDER=gemini`): TIER3 PASSED after the AI Studio project was recharged (initial runs failed with `1011 Your prepayment credits are depleted` — billing, not code). The driver publishes a synthetic red-square camera track plus a spoken color question; the agent transcribed the question, answered "Red." (color_correct), and returned real voice audio (energy ~783k vs ~1.3k noise floor). All four checks green: user_transcript, agent_transcript, color_correct, agent_audio. This validates the native video path end to end (worker dispatch, authenticated bootstrap, Gemini Live session, video frames, transcripts, audio out).
