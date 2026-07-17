"""P0 spike: hello-world Gemini Live agent with video-frame input.

Validates the multimodal baseline of docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md:
the agent subscribes to a video track (camera, screen share, or the synthetic
publisher below) and Gemini answers questions about what it sees.

Requires: pip install "pawflow[realtime-livekit]"
Env: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, GOOGLE_API_KEY

Run:   python spikes/livekit/spike_gemini_video.py dev
Video: join with a camera/screen-share client, or publish synthetic frames:
       python spikes/livekit/publish_synthetic_video.py <room-name>
Then ask out loud: "What color is the square?" — the answer must track the
color cycle to prove real frame ingestion.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.livekit_deps import require_livekit  # noqa: E402

require_livekit()

from livekit import agents  # noqa: E402
from livekit.agents import Agent, AgentSession, RoomInputOptions  # noqa: E402
from livekit.plugins import google  # noqa: E402

INSTRUCTIONS = (
    "You are PawFlow's multimodal spike agent. You can see the user's video "
    "track. When asked what you see, describe the current frame precisely, "
    "including colors and any text or numbers."
)


def _realtime_model():
    # plugin namespace moved out of beta during 1.x; support both
    realtime = getattr(google, "realtime", None) or google.beta.realtime
    return realtime.RealtimeModel(voice="Puck")


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()
    session = AgentSession(llm=_realtime_model())
    await session.start(
        room=ctx.room,
        agent=Agent(instructions=INSTRUCTIONS),
        room_input_options=RoomInputOptions(video_enabled=True),
    )
    await session.generate_reply(
        instructions="Greet the user and mention you can see their video.")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
