"""PawFlow LiveKit Agents sidecar worker — P2 MVP.

Joins LiveKit rooms (automatic agent dispatch), fetches the session
bootstrap from PawFlow, runs the provider AgentSession, and mirrors
everything through the worker-control WebSocket:

  room joined -> POST /api/realtime/livekit/worker/bootstrap (deployment
  secret) -> AgentSession(provider from bootstrap) + control WS attach ->
  transcripts/state -> `event` messages; provider tool calls -> `tool_call`
  round-trips (PawFlow RealtimeToolBridge decides); `context` -> spoken
  update; `shutdown` / max_session_seconds -> session teardown.

Env:
  PAWFLOW_URL                      e.g. http://localhost:8080
  PAWFLOW_REALTIME_WORKER_SECRET   deployment secret (bootstrap auth)
  LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
  LOCAL_STT_URL / LOCAL_TTS_URL / LOCAL_*_MODEL   (local_pipeline only)

Run:  python -m pawflow_livekit_worker dev
"""

import asyncio
import logging
import os

import aiohttp

from .control_client import WorkerControlClient

logger = logging.getLogger("pawflow-livekit-worker")


def _pawflow_url() -> str:
    url = (os.environ.get("PAWFLOW_URL", "") or "").rstrip("/")
    if not url:
        raise RuntimeError("PAWFLOW_URL is required (PawFlow server base URL)")
    return url


def _control_ws_url(bootstrap: dict) -> str:
    base = _pawflow_url()
    ws_base = "ws" + base[4:] if base.startswith("http") else base
    return (f"{ws_base}/ws/realtime-worker/{bootstrap['session_id']}"
            f"?token={bootstrap['control_token']}")


def _tls_insecure() -> bool:
    """PAWFLOW_TLS_INSECURE=1 — accept the server's self-signed cert.

    Set by the managed-stack provisioner when the PawFlow listener runs
    TLS with the default install certificate (the fetch targets loopback).
    """
    return os.environ.get("PAWFLOW_TLS_INSECURE", "") == "1"


async def fetch_bootstrap(room_name: str) -> dict:
    """Ask PawFlow for the session bootstrap of a room; None-safe: raises."""
    secret = os.environ.get("PAWFLOW_REALTIME_WORKER_SECRET", "")
    if not secret:
        raise RuntimeError("PAWFLOW_REALTIME_WORKER_SECRET is required")
    async with aiohttp.ClientSession() as http:
        async with http.post(
                f"{_pawflow_url()}/api/realtime/livekit/worker/bootstrap",
                json={"room": room_name},
                ssl=(False if _tls_insecure() else None),
                headers={"X-PawFlow-Worker-Secret": secret}) as resp:
            payload = await resp.json()
            if resp.status != 200:
                raise RuntimeError(
                    f"bootstrap refused ({resp.status}): "
                    f"{payload.get('error', '')}")
            return payload


def _build_session(bootstrap: dict):
    """Provider plugin selection — the only provider-specific code here."""
    from livekit.agents import AgentSession
    from livekit.plugins import openai

    provider = bootstrap["provider"]
    creds = bootstrap.get("credentials", {})
    api_key = creds.get("api_key", "") or os.environ.get(
        creds.get("env_var", "") or "", "")

    if provider == "openai":
        kwargs = {"model": bootstrap["model"], "api_key": api_key}
        if bootstrap.get("voice"):
            kwargs["voice"] = bootstrap["voice"]
        if creds.get("base_url"):
            kwargs["base_url"] = creds["base_url"]
        return AgentSession(llm=openai.realtime.RealtimeModel(**kwargs))

    if provider == "gemini":
        from livekit.plugins import google
        realtime = getattr(google, "realtime", None) or google.beta.realtime
        kwargs = {"model": bootstrap["model"], "api_key": api_key}
        if bootstrap.get("voice"):
            kwargs["voice"] = bootstrap["voice"]
        return AgentSession(llm=realtime.RealtimeModel(**kwargs))

    if provider == "azure_openai":
        # OpenAI plugin in Azure mode; the llmConnection's base_url is the
        # Azure endpoint, the service `model` is the deployment name.
        return AgentSession(llm=openai.realtime.RealtimeModel.with_azure(
            azure_deployment=bootstrap["model"],
            azure_endpoint=creds.get("base_url", ""),
            api_key=api_key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION",
                                       "2025-04-01-preview"),
            voice=bootstrap.get("voice") or "alloy"))

    if provider == "xai":
        # Grok voice speaks the OpenAI realtime protocol on api.x.ai.
        kwargs = {"model": bootstrap["model"], "api_key": api_key,
                  "base_url": creds.get("base_url", "")
                  or "https://api.x.ai/v1"}
        if bootstrap.get("voice"):
            kwargs["voice"] = bootstrap["voice"]
        return AgentSession(llm=openai.realtime.RealtimeModel(**kwargs))

    if provider == "aws_nova":
        # livekit-plugins-aws is NOT in the default dependency group (heavy
        # AWS deps) — install it in the worker image to use Nova Sonic.
        try:
            from livekit.plugins import aws
        except ImportError:
            raise RuntimeError(
                "provider 'aws_nova' needs livekit-plugins-aws: "
                "pip install 'livekit-plugins-aws[realtime]'")
        return AgentSession(llm=aws.realtime.RealtimeModel(
            voice=bootstrap.get("voice") or "tiffany"))

    if provider == "local_pipeline":
        from livekit.plugins import silero
        from livekit.plugins.turn_detector.multilingual import (
            MultilingualModel,
        )
        local = bootstrap.get("local_pipeline", {}) or {}

        def _local(key, env, default):
            return local.get(key, "") or os.environ.get(env, "") or default

        return AgentSession(
            vad=silero.VAD.load(),
            turn_detection=MultilingualModel(),
            stt=openai.STT(
                base_url=_local("local_stt_url", "LOCAL_STT_URL",
                                "http://localhost:8001/v1"),
                model=_local("local_stt_model", "LOCAL_STT_MODEL",
                             "Systran/faster-whisper-small"),
                api_key=os.environ.get("LOCAL_STT_KEY", "local")),
            llm=openai.LLM(
                base_url=creds.get("base_url", "") or None,
                model=bootstrap.get("model")
                or creds.get("default_model", ""),
                api_key=api_key or "local"),
            tts=openai.TTS(
                base_url=_local("local_tts_url", "LOCAL_TTS_URL",
                                "http://localhost:8002/v1"),
                # MUST stay tts-1/tts-1-hd unless the local server speaks the
                # OpenAI SSE stream format: the plugin picks the wire format
                # from the MODEL NAME, and any other name selects SSE, which
                # kokoro-fastapi/speaches do not implement (bench finding
                # 2026-07-17). Local servers ignore the model name anyway.
                model=_local("local_tts_model", "LOCAL_TTS_MODEL", "tts-1"),
                voice=_local("local_tts_voice", "LOCAL_TTS_VOICE",
                             "af_heart"),
                api_key=os.environ.get("LOCAL_TTS_KEY", "local")),
        )

    raise RuntimeError(f"Unknown realtime provider '{provider}'")


def _proxy_tools(bootstrap: dict, control: WorkerControlClient) -> list:
    """LiveKit function tools that forward to PawFlow's tool bridge."""
    from livekit.agents import llm as lk_llm

    tools = []
    for definition in bootstrap.get("tools", []):
        raw = {"name": definition["name"],
               "description": definition.get("description", ""),
               "parameters": definition.get("parameters",
                                            {"type": "object",
                                             "properties": {}})}

        def _make(name):
            async def _handler(raw_arguments: dict):
                outcome = await control.call_tool(name, raw_arguments)
                result = outcome.get("result") or {}
                if not outcome.get("ok"):
                    return f"Error: {result.get('error', 'tool failed')}"
                return str(result.get("text", ""))
            return _handler

        tools.append(lk_llm.function_tool(_make(raw["name"]),
                                          raw_schema=raw))
    return tools


def _room_input_options(bootstrap: dict):
    """Video frame sampling per the plan's video_fps_active/idle settings.

    Falls back to the plugin's default sampler when this livekit-agents
    build does not expose VoiceActivityVideoSampler (documented default:
    ~1 FPS speaking, ~0.3 FPS idle — same shape as our config defaults).
    """
    from livekit.agents import RoomInputOptions
    video = bool(bootstrap.get("video_input"))
    if not video:
        return RoomInputOptions(video_enabled=False)
    try:
        from livekit.agents.voice.room_io import VoiceActivityVideoSampler
        sampler = VoiceActivityVideoSampler(
            speaking_fps=float(bootstrap.get("video_fps_active", 1.0)),
            silent_fps=float(bootstrap.get("video_fps_idle", 0.33)))
        return RoomInputOptions(video_enabled=True, video_sampler=sampler)
    except (ImportError, TypeError):
        logger.info("VoiceActivityVideoSampler unavailable — using the "
                    "plugin's default video sampling")
        return RoomInputOptions(video_enabled=True)


async def entrypoint(ctx) -> None:
    """One LiveKit job = one PawFlow realtime session."""
    from livekit.agents import Agent

    room_name = ctx.room.name if ctx.room else ""
    try:
        bootstrap = await fetch_bootstrap(room_name)
    except Exception as exc:
        # Room without an active PawFlow session (or misconfig): decline.
        logger.warning("no bootstrap for room '%s': %s", room_name, exc)
        return

    await ctx.connect()

    session_holder = {}

    async def _on_context(text: str) -> None:
        session = session_holder.get("session")
        if session is not None:
            session.generate_reply(instructions=text)

    async def _on_shutdown(reason: str) -> None:
        logger.info("shutdown from PawFlow: %s", reason)
        ctx.shutdown(reason=reason or "pawflow_shutdown")

    control = WorkerControlClient(
        _control_ws_url(bootstrap), bootstrap["session_id"],
        worker_id=f"worker-{os.getpid()}", on_context=_on_context,
        on_shutdown=_on_shutdown)
    await control.connect()

    session = _build_session(bootstrap)
    session_holder["session"] = session

    def _emit(name: str, data: dict) -> None:
        asyncio.create_task(control.send_event(name, data))

    @session.on("user_input_transcribed")
    def _on_user_transcript(ev):
        _emit("realtime.user.transcript.final" if ev.is_final
              else "realtime.user.transcript.delta",
              {"text": ev.transcript})

    @session.on("conversation_item_added")
    def _on_item(ev):
        item = getattr(ev, "item", None)
        if getattr(item, "role", "") == "assistant" and \
                getattr(item, "text_content", ""):
            _emit("realtime.agent.transcript.final",
                  {"text": item.text_content})

    @session.on("agent_state_changed")
    def _on_state(ev):
        _emit("realtime.agent.state", {"state": str(
            getattr(ev, "new_state", "") or "")})

    @session.on("metrics_collected")
    def _on_metrics(ev):
        # Structured token counts so PawFlow can record the session in the
        # usage ledger. Field names differ per metric type (LLMMetrics uses
        # prompt/completion_tokens, RealtimeModelMetrics input/output_tokens)
        # — probe both, keep a bounded raw string for diagnostics.
        metrics = getattr(ev, "metrics", None)
        data = {"kind": type(metrics).__name__ if metrics is not None else ""}
        for src, dst in (("prompt_tokens", "input_tokens"),
                         ("input_tokens", "input_tokens"),
                         ("completion_tokens", "output_tokens"),
                         ("output_tokens", "output_tokens"),
                         ("prompt_cached_tokens", "cached_tokens")):
            val = getattr(metrics, src, None)
            if isinstance(val, (int, float)) and val > 0:
                data[dst] = int(val)
        data["raw"] = str(metrics)[:400]
        _emit("realtime.usage", data)

    await session.start(
        room=ctx.room,
        agent=Agent(instructions=bootstrap.get("instructions", ""),
                    tools=_proxy_tools(bootstrap, control)),
        room_input_options=_room_input_options(bootstrap),
    )
    await control.send_event("realtime.media.connected", {})

    # Hard session cap — PawFlow enforces the same policy server-side, the
    # worker is the enforcement point for the media path.
    max_seconds = int(bootstrap.get("max_session_seconds", 600) or 600)

    async def _cap():
        await asyncio.sleep(max_seconds)
        logger.info("max_session_seconds (%s) reached", max_seconds)
        await control.close("max_session_seconds")
        ctx.shutdown(reason="max_session_seconds")

    cap_task = asyncio.create_task(_cap())
    ctx.add_shutdown_callback(lambda: _cleanup(cap_task, control))


async def _cleanup(cap_task, control) -> None:
    cap_task.cancel()
    if not control.closed.is_set():
        await control.close("job ended")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    main()
