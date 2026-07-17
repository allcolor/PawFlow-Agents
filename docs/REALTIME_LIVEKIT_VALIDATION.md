# Realtime LiveKit — End-to-End Validation Runbook

Everything below is implemented and unit-tested (P0–P3 of
`REALTIME_MULTIMODAL_LIVEKIT_PLAN.md`); this runbook is the live-validation
pass the owner runs once, with real provider keys. Each step maps to a plan
acceptance criterion. Record failures as issues against the plan phase.

## 1. Stack up

```bash
export PAWFLOW_REALTIME_WORKER_SECRET=$(openssl rand -hex 24)
export OPENAI_API_KEY=...        # voice baseline
export GOOGLE_API_KEY=...        # multimodal baseline
docker compose --profile realtime up -d livekit livekit-worker
docker compose up -d pawflow
```

Checks:
- `livekit-worker` logs show it registered with the LiveKit server.
- Without `PAWFLOW_REALTIME_WORKER_SECRET` on the pawflow service, the
  worker logs `bootstrap refused (503)` — expected fail-closed behavior.

## 2. Spikes in isolation (P0 acceptance)

Run the four spikes per `spikes/livekit/README.md`:
OpenAI voice, OpenAI voice + `SPIKE_VIDEO=1`, Gemini video (+ synthetic
publisher, ask "what color is the square?" through a color change), local
pipeline (verify zero audio egress). Record plugin capability gaps
(Gemini session resumption, frame sampling controls, tool behavior) in the
README findings log and the plan's migration matrix.

## 3. Service + session (P1/P2 acceptance)

1. Create a `realtimeVoiceConnection` service with:
   `engine=livekit`, `livekit_url=ws://localhost:7880`,
   `livekit_api_key=devkey`, `livekit_api_secret=secret`,
   `provider=openai`, `model=gpt-realtime`, `llm_service=<openai conn>`,
   `tool_profile=recall,web_search`.
2. Broken-config check: omit `livekit_url` → service install fails with the
   actionable message (no silent fallback).
3. In a conversation, click the mic button and pick the LiveKit service
   (LiveKit badge in the right-click settings panel).
4. Expected: overlay opens, agent greets by voice, barge-in works,
   captions track both sides, final transcripts land as normal chat
   messages (visible from a second attached client), audio is NOT
   persisted anywhere.
5. Tools: ask the agent to use `recall` (pre-approved → runs; result
   spoken). Ask for a tool needing interactive approval → spoken refusal.
6. Stop: hang up → worker logs shutdown, `POST stop` returns
   `stopped: true`; force-stop from the UI kills the session and the NEXT
   session starts cleanly.

## 4. Video (P3/P4 acceptance)

Set `video_input=true`, `video_source=both` on the service (Gemini
provider for the native path). Expected: camera + screen-share buttons in
the overlay, agent describes what it sees, frame answers change when the
scene changes. Repeat with the OpenAI provider (sampled-frames path) and
note the image-token cost delta.

## 5. Local pipeline (P7 profile)

Service with `provider=local_pipeline`, `llm_service=<any text conn, e.g.
Ollama>`; worker env pointing at local STT/TTS servers (see
`spikes/livekit/README.md` §3). Expected: full-duplex voice with barge-in,
no audio egress (tcpdump), only text completions to the LLM endpoint.

## 6. Sign-off gates

- All of §2–§5 pass → mark P0/P2/P3/P4 acceptance done in the plan.
- THEN start P5 (retire the custom bridge): flip the webchat default to
  LiveKit, gate the legacy bridge behind
  `PAWFLOW_ENABLE_LEGACY_REALTIME_BRIDGE=1`, delete the protocol adapters
  per the plan's migration matrix (every row covered or explicitly
  dropped), and only after the Gemini resumption row is resolved.
- P6 (Azure/xAI/Nova) and P8 (recording) start after wave-1 sign-off.
