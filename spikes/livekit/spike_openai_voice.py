"""P0 spike: hello-world LiveKit Agent with OpenAI Realtime voice.

Validates the primary voice baseline of docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md
before any production code: browser mic -> LiveKit -> OpenAI Realtime ->
LiveKit -> browser audio, with barge-in handled by the AgentSession.

Requires: pip install "pawflow[realtime-livekit]"
Env: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY
     (docker compose --profile realtime sets the LiveKit dev values).

Run:  python spikes/livekit/spike_openai_voice.py dev
Join the room from LiveKit's Agents Playground or any LiveKit client.

Video: SPIKE_VIDEO=1 enables video-frame input for OpenAI too — gpt-realtime
accepts image input, and LiveKit forwards sampled frames (~1 fps) from the
user's video track as images. Validates the OpenAI frame/image path of the
plan's capability matrix (cost caveat: every frame is image-input tokens).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.livekit_deps import require_livekit  # noqa: E402

require_livekit()

from livekit import agents  # noqa: E402
from livekit.agents import Agent, AgentSession, RoomInputOptions  # noqa: E402
from livekit.plugins import openai  # noqa: E402

VIDEO = os.environ.get("SPIKE_VIDEO", "") == "1"

INSTRUCTIONS = (
    "You are PawFlow's realtime spike agent. Answer briefly and confirm "
    "you can hear the user. If asked, say you are running through LiveKit "
    "with the OpenAI Realtime API."
    + (" You receive sampled frames from the user's video track; when asked "
       "what you see, describe the latest frame precisely." if VIDEO else "")
)


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="alloy"),
    )
    await session.start(
        room=ctx.room,
        agent=Agent(instructions=INSTRUCTIONS),
        room_input_options=RoomInputOptions(video_enabled=VIDEO),
    )
    await session.generate_reply(
        instructions="Greet the user and ask them to say something.")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
