"""LiveKit realtime engine — config loader and scoped token generation.

P1 of docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md. The user-facing service
stays `realtimeVoiceConnection`; `engine: livekit` selects this engine and
the compatibility loader below maps legacy custom-bridge configs onto the
LiveKit shape deterministically (missing required LiveKit settings fail
clearly — no anonymous/default fallback).

Tokens (all JWT HS256 via pyjwt, no LiveKit SDK needed server-side):
- browser room token: signed with the LiveKit API secret, minimum room
  grants only (join + publish own mic/camera/screen + subscribe), never
  roomAdmin. TTL = min(max_session_seconds + 60s grace, 15 min).
- agent room token: for the trusted sidecar worker, scoped to the room.
- worker-control token: PawFlow-signed (SecretsManager subkey), scopes one
  worker to one session/conversation/agent on /ws/realtime-worker/.
"""

import logging
import time

from core import ServiceError

logger = logging.getLogger(__name__)

LIVEKIT_PROVIDERS = ("openai", "gemini", "azure_openai", "xai", "aws_nova",
                     "local_pipeline")

# legacy custom-bridge protocol -> LiveKit provider plugin
_PROTOCOL_TO_PROVIDER = {"openai_realtime": "openai", "gemini_live": "gemini"}

# legacy `vad` -> `turn_detection`
_VAD_TO_TURN_DETECTION = {"server": "server_vad", "manual": "manual"}

_MODALITIES = ("audio", "text", "video")
_RECORDING_POLICIES = ("none", "transcript", "audio", "audio_video")

ROOM_TOKEN_MAX_TTL_S = 15 * 60
# JWT audience label, not a credential.
WORKER_TOKEN_AUDIENCE = "pawflow-realtime-worker"  # nosec B105
_SIGNING_DOMAIN = b"realtime-worker-control"


def resolve_livekit_config(raw: dict) -> dict:
    """Normalize a realtimeVoiceConnection config for the LiveKit engine.

    Accepts both new-style configs (`engine: livekit` + livekit_* keys) and
    legacy custom-bridge configs (`protocol`/`vad`/`llm_service`), which map
    deterministically when the provider is covered by LiveKit. Raises
    ServiceError on anything missing or invalid.
    """
    cfg = dict(raw or {})

    def _s(key, default=""):
        return str(cfg.get(key, default) or default).strip()

    livekit_url = _s("livekit_url")
    livekit_managed = not livekit_url
    if livekit_managed:
        # Managed mode: no external LiveKit configured — PawFlow
        # provisions livekit-server + worker itself (generated
        # credentials, containers supervised by RealtimeStackManager).
        from core.realtime_stack_manager import RealtimeStackManager
        managed = RealtimeStackManager.get_instance().engine_credentials()
        livekit_url = managed["livekit_url"]
        api_key = managed["livekit_api_key"]
        api_secret = managed["livekit_api_secret"]
    else:
        if not livekit_url.startswith(
                ("ws://", "wss://", "http://", "https://")):
            raise ServiceError(
                f"livekit_url '{livekit_url}' must be a ws(s):// "
                "or http(s):// URL")
        api_key = _s("livekit_api_key")
        api_secret = _s("livekit_api_secret")
        if not api_key or not api_secret:
            raise ServiceError(
                "livekit_api_key and livekit_api_secret are required when "
                "livekit_url points at an external LiveKit server (leave "
                "livekit_url empty for the managed stack)")

    # provider: explicit, else derived from the legacy protocol
    provider = _s("provider").lower()
    protocol = _s("protocol").lower()
    if not provider:
        provider = _PROTOCOL_TO_PROVIDER.get(protocol, "")
    if not provider:
        raise ServiceError(
            "provider is required (one of: "
            + ", ".join(LIVEKIT_PROVIDERS)
            + "), or a legacy protocol "
            "(openai_realtime/gemini_live) it can be derived from")
    if provider not in LIVEKIT_PROVIDERS:
        raise ServiceError(
            f"Unknown livekit provider '{provider}'. "
            f"Supported: {', '.join(LIVEKIT_PROVIDERS)}")

    model = _s("model")
    if not model and provider != "local_pipeline":
        raise ServiceError("model is required for the livekit realtime engine")

    modalities = [m.strip().lower()
                  for m in _s("modalities", "audio,text").split(",")
                  if m.strip()]
    bad = [m for m in modalities if m not in _MODALITIES]
    if bad:
        raise ServiceError(
            f"Invalid modalities {bad}; allowed: {', '.join(_MODALITIES)}")

    video_input = bool(cfg.get("video_input", False)) or "video" in modalities
    video_source = _s("video_source", "camera").lower()
    if video_source not in ("camera", "screen", "both"):
        raise ServiceError(
            f"video_source '{video_source}' must be camera, screen or both")

    def _fps(key, default):
        try:
            value = float(cfg.get(key, default) or default)
        except (TypeError, ValueError):
            raise ServiceError(f"{key} must be a number (frames per second)")
        if value <= 0:
            raise ServiceError(f"{key} must be positive")
        return value

    # Plan defaults: ~1 FPS while the user speaks, ~1 frame / 3 s idle.
    video_fps_active = _fps("video_fps_active", 1.0)
    video_fps_idle = _fps("video_fps_idle", 0.33)

    turn_detection = _s("turn_detection").lower()
    if not turn_detection:
        vad = _s("vad").lower()
        turn_detection = _VAD_TO_TURN_DETECTION.get(vad, "provider_default")
    if turn_detection not in ("provider_default", "semantic_vad",
                              "server_vad", "manual"):
        raise ServiceError(
            f"turn_detection '{turn_detection}' must be provider_default, "
            "semantic_vad, server_vad or manual")

    instructions_mode = _s("instructions_mode", "agent").lower()
    if instructions_mode not in ("agent", "custom"):
        raise ServiceError(
            f"instructions_mode '{instructions_mode}' must be agent or custom")

    recording_policy = _s("recording_policy", "transcript").lower()
    if recording_policy not in _RECORDING_POLICIES:
        raise ServiceError(
            f"recording_policy '{recording_policy}' must be one of: "
            + ", ".join(_RECORDING_POLICIES))

    try:
        max_session_seconds = int(cfg.get("max_session_seconds", 600) or 600)
    except (TypeError, ValueError):
        raise ServiceError("max_session_seconds must be an integer")
    if max_session_seconds <= 0:
        raise ServiceError("max_session_seconds must be positive")

    return {
        "engine": "livekit",
        "livekit_managed": livekit_managed,
        "livekit_url": livekit_url,
        "livekit_api_key": api_key,
        "livekit_api_secret": api_secret,
        "provider": provider,
        "provider_secret": _s("provider_secret"),
        "llm_service": _s("llm_service"),
        "model": model,
        "voice": _s("voice"),
        "modalities": modalities,
        "video_input": video_input,
        "video_source": video_source,
        "video_fps_active": video_fps_active,
        "video_fps_idle": video_fps_idle,
        # local_pipeline plugin endpoints (worker-side OpenAI-compatible
        # servers); empty = the worker's env/defaults apply
        "local_stt_url": _s("local_stt_url"),
        "local_stt_model": _s("local_stt_model"),
        "local_tts_url": _s("local_tts_url"),
        "local_tts_model": _s("local_tts_model"),
        "local_tts_voice": _s("local_tts_voice"),
        "turn_detection": turn_detection,
        "instructions_mode": instructions_mode,
        "instructions": _s("instructions"),
        "tool_profile": _s("tool_profile"),
        "context_mode": _s("context_mode", "summary:2000").lower(),
        "max_session_seconds": max_session_seconds,
        "recording_policy": recording_policy,
    }


def room_token_ttl_seconds(max_session_seconds: int) -> int:
    return min(int(max_session_seconds) + 60, ROOM_TOKEN_MAX_TTL_S)


def _livekit_jwt(api_key: str, api_secret: str, identity: str, name: str,
                 grants: dict, ttl_s: int, metadata: str = "") -> str:
    import jwt
    now = int(time.time())
    claims = {
        "iss": api_key,
        "sub": identity,
        "nbf": now - 5,
        "exp": now + int(ttl_s),
        "name": name,
        "video": grants,
    }
    if metadata:
        claims["metadata"] = metadata
    return jwt.encode(claims, api_secret, algorithm="HS256")


def create_browser_room_token(engine_cfg: dict, *, room_name: str,
                              session_id: str, conversation_id: str,
                              user_id: str) -> str:
    """Room token for the browser participant — minimum grants, no admin."""
    import json
    publish_sources = ["microphone"]
    if engine_cfg["video_input"]:
        if engine_cfg["video_source"] in ("camera", "both"):
            publish_sources.append("camera")
        if engine_cfg["video_source"] in ("screen", "both"):
            publish_sources.extend(["screen_share", "screen_share_audio"])
    grants = {
        "room": room_name,
        "roomJoin": True,
        "roomAdmin": False,
        "roomCreate": False,
        "canPublish": True,
        "canPublishSources": publish_sources,
        "canSubscribe": True,
        "canPublishData": True,
        "canUpdateOwnMetadata": False,
    }
    return _livekit_jwt(
        engine_cfg["livekit_api_key"], engine_cfg["livekit_api_secret"],
        identity=f"user-{user_id or 'admin'}-{session_id[:8]}",
        name=user_id or "user",
        grants=grants,
        ttl_s=room_token_ttl_seconds(engine_cfg["max_session_seconds"]),
        metadata=json.dumps({"session_id": session_id,
                             "conversation_id": conversation_id}),
    )


def create_agent_room_token(engine_cfg: dict, *, room_name: str,
                            session_id: str, agent_name: str) -> str:
    """Room token for the trusted sidecar worker's agent participant."""
    grants = {
        "room": room_name,
        "roomJoin": True,
        "roomAdmin": False,
        "roomCreate": False,
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": True,
        "agent": True,
    }
    return _livekit_jwt(
        engine_cfg["livekit_api_key"], engine_cfg["livekit_api_secret"],
        identity=f"agent-{session_id[:8]}",
        name=agent_name or "agent",
        grants=grants,
        ttl_s=room_token_ttl_seconds(engine_cfg["max_session_seconds"]),
    )


# -- worker-control tokens (PawFlow-signed) -----------------------------

def _worker_signing_key() -> bytes:
    from core.secrets import get_secrets_manager
    return get_secrets_manager().derive_subkey(_SIGNING_DOMAIN)


def create_worker_control_token(*, session_id: str, conversation_id: str,
                                user_id: str, agent_name: str,
                                ttl_s: int) -> str:
    """Short-lived token scoping ONE worker to ONE session's control WS."""
    import jwt
    now = int(time.time())
    return jwt.encode({
        "aud": WORKER_TOKEN_AUDIENCE,
        "sid": session_id,
        "cid": conversation_id,
        "uid": user_id,
        "agent": agent_name,
        "iat": now,
        "nbf": now - 5,
        "exp": now + int(ttl_s),
    }, _worker_signing_key(), algorithm="HS256")


def verify_worker_control_token(token: str, session_id: str) -> dict:
    """Validate a worker-control token for a session. Raises ValueError."""
    import jwt
    try:
        claims = jwt.decode(token, _worker_signing_key(),
                            algorithms=["HS256"],
                            audience=WORKER_TOKEN_AUDIENCE)
    except jwt.PyJWTError as e:
        raise ValueError(f"Invalid worker-control token: {e}")
    if claims.get("sid") != session_id:
        raise ValueError("worker-control token is for another session")
    return claims
