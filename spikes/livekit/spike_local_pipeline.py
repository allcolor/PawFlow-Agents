"""P0 spike: local pipeline profile — zero-cloud-audio voice cascade.

Validates the `provider: local_pipeline` profile of
docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md (the OpenLive-shaped path,
github.com/katipally/openlive): full-duplex voice where NO audio leaves the
deployment — Silero VAD + LiveKit end-of-turn model run in-process, STT and
TTS hit local OpenAI-compatible servers, and only the text turn goes to the
configured LLM (which can itself be local, e.g. Ollama).

Requires: pip install "pawflow[realtime-livekit]"
Env (all OpenAI-compatible base URLs):
  LOCAL_STT_URL   default http://localhost:8001/v1  (e.g. speaches /
                  faster-whisper-server)   LOCAL_STT_MODEL default
                  Systran/faster-whisper-small
  LOCAL_TTS_URL   default http://localhost:8002/v1  (e.g. kokoro-fastapi)
                  LOCAL_TTS_MODEL default kokoro, LOCAL_TTS_VOICE af_heart
  SPIKE_LLM_URL   default http://localhost:11434/v1 (Ollama)
  SPIKE_LLM_MODEL default qwen3:8b
  SPIKE_LLM_KEY   default "local"
  plus LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET

Run:  python spikes/livekit/spike_local_pipeline.py dev
Expected: barge-in works mid-sentence, sentence-by-sentence TTS starts
while the LLM is still streaming, and tcpdump/provider dashboards show no
audio egress — only text completions to SPIKE_LLM_URL.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.livekit_deps import require_livekit  # noqa: E402

require_livekit()

from livekit import agents  # noqa: E402
from livekit.agents import Agent, AgentSession  # noqa: E402
from livekit.plugins import openai, silero  # noqa: E402
from livekit.plugins.turn_detector.multilingual import (  # noqa: E402
    MultilingualModel,
)

INSTRUCTIONS = (
    "You are PawFlow's local-pipeline spike agent. Keep spoken answers "
    "short. If asked, explain that your voice loop runs fully locally and "
    "only text reaches the language model."
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "") or default


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect()
    session = AgentSession(
        vad=silero.VAD.load(),
        turn_detection=MultilingualModel(),
        stt=openai.STT(
            base_url=_env("LOCAL_STT_URL", "http://localhost:8001/v1"),
            model=_env("LOCAL_STT_MODEL", "Systran/faster-whisper-small"),
            api_key=_env("LOCAL_STT_KEY", "local"),
        ),
        llm=openai.LLM(
            base_url=_env("SPIKE_LLM_URL", "http://localhost:11434/v1"),
            model=_env("SPIKE_LLM_MODEL", "qwen3:8b"),
            api_key=_env("SPIKE_LLM_KEY", "local"),
        ),
        tts=openai.TTS(
            base_url=_env("LOCAL_TTS_URL", "http://localhost:8002/v1"),
            model=_env("LOCAL_TTS_MODEL", "kokoro"),
            voice=_env("LOCAL_TTS_VOICE", "af_heart"),
            api_key=_env("LOCAL_TTS_KEY", "local"),
        ),
    )
    await session.start(room=ctx.room, agent=Agent(instructions=INSTRUCTIONS))
    await session.generate_reply(
        instructions="Greet the user and mention the voice loop is local.")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
