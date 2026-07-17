# LiveKit Agents sidecar worker image (docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md).
# P0: ships the spike entrypoints under spikes/livekit/. The production worker
# module replaces the default command in P2. Build context is the repo root.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "livekit-agents>=1.6.0" \
    "livekit-plugins-openai>=1.6.0" \
    "livekit-plugins-google>=1.6.0" \
    "livekit-plugins-silero>=1.6.0" \
    "aiohttp>=3.9.0"

COPY spikes/livekit/ /app/spikes/livekit/

# Hello-world OpenAI Realtime voice agent in LiveKit dev mode.
# Override the command to run the Gemini video spike or the
# worker-control spike instead (see spikes/livekit/README.md).
CMD ["python", "spikes/livekit/spike_openai_voice.py", "dev"]
