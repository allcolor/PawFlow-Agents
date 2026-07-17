"""PawFlow LiveKit sidecar worker (docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md).

Runs OUTSIDE the PawFlow server process (sidecar container/process, its own
asyncio loop). `control_client` is LiveKit-free and CI-tested; `worker`
needs the `pawflow[realtime-livekit]` dependency group.
"""
