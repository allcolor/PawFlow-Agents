"""P1 tests: LiveKit engine config loader, scoped tokens, session registry.

No LiveKit install, no network: tokens are plain JWTs (pyjwt), the session
registry is exercised with stubbed ServiceRegistry/ownership, and the
worker-control WS handler runs against a fake socket with scripted frames.
"""

import json
import time

import jwt
import pytest

from core import ServiceError
from services import _livekit_engine as engine
from services import _livekit_sessions as sessions
from services import _realtime_worker_protocol as proto


BASE_CFG = {
    "engine": "livekit",
    "livekit_url": "ws://localhost:7880",
    "livekit_api_key": "devkey",
    "livekit_api_secret": "secret",
    "provider": "openai",
    "model": "gpt-realtime",
}


def _cfg(**overrides):
    cfg = dict(BASE_CFG)
    cfg.update(overrides)
    for key, value in list(cfg.items()):
        if value is None:
            del cfg[key]
    return cfg


class TestResolveLivekitConfig:
    def test_minimal_valid(self):
        out = engine.resolve_livekit_config(_cfg())
        assert out["engine"] == "livekit"
        assert out["provider"] == "openai"
        assert out["modalities"] == ["audio", "text"]
        assert out["video_input"] is False
        assert out["turn_detection"] == "provider_default"
        assert out["context_mode"] == "summary:2000"
        assert out["recording_policy"] == "transcript"
        assert out["max_session_seconds"] == 600

    @pytest.mark.parametrize("missing,fragment", [
        ("livekit_api_key", "livekit_api_key and livekit_api_secret"),
        ("livekit_api_secret", "livekit_api_key and livekit_api_secret"),
        ("model", "model is required"),
    ])
    def test_missing_required_fails_clearly(self, missing, fragment):
        with pytest.raises(ServiceError, match=fragment):
            engine.resolve_livekit_config(_cfg(**{missing: None}))

    def test_empty_url_selects_managed_stack(self):
        """No livekit_url = managed mode with generated credentials."""
        out = engine.resolve_livekit_config(
            _cfg(livekit_url=None, livekit_api_key=None,
                 livekit_api_secret=None))
        assert out["livekit_managed"] is True
        assert out["livekit_url"].startswith("ws://127.0.0.1:")
        assert out["livekit_api_key"].startswith("pflk")
        assert len(out["livekit_api_secret"]) >= 32
        # Stable across calls (persisted, not re-generated).
        again = engine.resolve_livekit_config(_cfg(livekit_url=None))
        assert again["livekit_api_key"] == out["livekit_api_key"]
        assert again["livekit_api_secret"] == out["livekit_api_secret"]

    def test_external_url_is_not_managed(self):
        out = engine.resolve_livekit_config(_cfg())
        assert out["livekit_managed"] is False
        assert out["livekit_url"] == "ws://localhost:7880"

    def test_bad_url_scheme_rejected(self):
        with pytest.raises(ServiceError, match="ws\\(s\\)://"):
            engine.resolve_livekit_config(_cfg(livekit_url="localhost:7880"))

    def test_unknown_provider_rejected(self):
        with pytest.raises(ServiceError, match="Unknown livekit provider"):
            engine.resolve_livekit_config(_cfg(provider="acme"))

    def test_provider_or_protocol_required(self):
        with pytest.raises(ServiceError, match="provider is required"):
            engine.resolve_livekit_config(_cfg(provider=None))

    def test_legacy_config_maps_deterministically(self):
        """protocol/vad/llm_service legacy keys map onto the LiveKit shape."""
        legacy = {
            "livekit_url": "ws://localhost:7880",
            "livekit_api_key": "devkey",
            "livekit_api_secret": "secret",
            "protocol": "gemini_live",
            "llm_service": "gem",
            "model": "gemini-2.5-flash-native-audio-preview-09-2025",
            "vad": "manual",
            "instructions_mode": "custom",
            "instructions": "Be brief.",
            "tool_profile": "recall,web_search",
            "context_mode": "last:10",
            "max_session_seconds": 120,
        }
        out = engine.resolve_livekit_config(legacy)
        assert out["provider"] == "gemini"
        assert out["turn_detection"] == "manual"
        assert out["llm_service"] == "gem"
        assert out["instructions_mode"] == "custom"
        assert out["instructions"] == "Be brief."
        assert out["tool_profile"] == "recall,web_search"
        assert out["context_mode"] == "last:10"
        assert out["max_session_seconds"] == 120

    def test_legacy_server_vad_maps(self):
        out = engine.resolve_livekit_config(_cfg(vad="server"))
        assert out["turn_detection"] == "server_vad"

    def test_invalid_modalities_rejected(self):
        with pytest.raises(ServiceError, match="Invalid modalities"):
            engine.resolve_livekit_config(_cfg(modalities="audio,smell"))

    def test_video_modality_implies_video_input(self):
        out = engine.resolve_livekit_config(
            _cfg(modalities="audio,text,video"))
        assert out["video_input"] is True

    def test_local_pipeline_needs_no_model(self):
        out = engine.resolve_livekit_config(
            _cfg(provider="local_pipeline", model=None))
        assert out["provider"] == "local_pipeline"

    def test_video_fps_defaults_and_validation(self):
        out = engine.resolve_livekit_config(_cfg())
        assert out["video_fps_active"] == 1.0
        assert out["video_fps_idle"] == 0.33
        out = engine.resolve_livekit_config(
            _cfg(video_fps_active=2, video_fps_idle="0.5"))
        assert out["video_fps_active"] == 2.0
        assert out["video_fps_idle"] == 0.5
        with pytest.raises(ServiceError, match="video_fps_active"):
            engine.resolve_livekit_config(_cfg(video_fps_active="fast"))
        with pytest.raises(ServiceError, match="positive"):
            engine.resolve_livekit_config(_cfg(video_fps_idle=-1))

    def test_local_pipeline_plugin_keys_pass_through(self):
        out = engine.resolve_livekit_config(_cfg(
            provider="local_pipeline", model=None,
            local_stt_url="http://stt:8001/v1", local_tts_voice="af_bella"))
        assert out["local_stt_url"] == "http://stt:8001/v1"
        assert out["local_tts_voice"] == "af_bella"
        assert out["local_tts_url"] == ""

    def test_bad_max_session_seconds(self):
        with pytest.raises(ServiceError, match="max_session_seconds"):
            engine.resolve_livekit_config(_cfg(max_session_seconds="soon"))
        with pytest.raises(ServiceError, match="positive"):
            engine.resolve_livekit_config(_cfg(max_session_seconds=-5))


class TestRoomTokens:
    def test_browser_token_scope_and_grants(self):
        cfg = engine.resolve_livekit_config(_cfg())
        token = engine.create_browser_room_token(
            cfg, room_name="room1", session_id="sess-12345678",
            conversation_id="conv1", user_id="quentin")
        claims = jwt.decode(token, "secret", algorithms=["HS256"],
                            options={"verify_aud": False})
        assert claims["iss"] == "devkey"
        assert "quentin" in claims["sub"] and "sess-123" in claims["sub"]
        grants = claims["video"]
        assert grants["room"] == "room1"
        assert grants["roomJoin"] is True
        assert grants["roomAdmin"] is False
        assert grants["roomCreate"] is False
        assert grants["canPublishSources"] == ["microphone"]
        meta = json.loads(claims["metadata"])
        assert meta == {"session_id": "sess-12345678",
                        "conversation_id": "conv1"}

    def test_browser_token_video_sources(self):
        cfg = engine.resolve_livekit_config(
            _cfg(video_input=True, video_source="both"))
        token = engine.create_browser_room_token(
            cfg, room_name="r", session_id="s" * 10, conversation_id="c",
            user_id="u")
        sources = jwt.decode(token, "secret", algorithms=["HS256"])[
            "video"]["canPublishSources"]
        assert "camera" in sources and "screen_share" in sources

    def test_token_ttl_capped_at_15_minutes(self):
        assert engine.room_token_ttl_seconds(600) == 660
        assert engine.room_token_ttl_seconds(7200) == 900
        cfg = engine.resolve_livekit_config(_cfg(max_session_seconds=7200))
        token = engine.create_browser_room_token(
            cfg, room_name="r", session_id="s" * 10, conversation_id="c",
            user_id="u")
        claims = jwt.decode(token, "secret", algorithms=["HS256"])
        assert claims["exp"] - time.time() <= 901

    def test_agent_token_has_agent_grant(self):
        cfg = engine.resolve_livekit_config(_cfg())
        token = engine.create_agent_room_token(
            cfg, room_name="r", session_id="s" * 10, agent_name="claude")
        claims = jwt.decode(token, "secret", algorithms=["HS256"])
        assert claims["video"]["agent"] is True
        assert claims["video"]["roomAdmin"] is False


class TestWorkerControlToken:
    def test_round_trip(self):
        token = engine.create_worker_control_token(
            session_id="sid1", conversation_id="cid1", user_id="u1",
            agent_name="claude", ttl_s=60)
        claims = engine.verify_worker_control_token(token, "sid1")
        assert claims["cid"] == "cid1"
        assert claims["uid"] == "u1"
        assert claims["agent"] == "claude"

    def test_wrong_session_rejected(self):
        token = engine.create_worker_control_token(
            session_id="sid1", conversation_id="c", user_id="u",
            agent_name="a", ttl_s=60)
        with pytest.raises(ValueError, match="another session"):
            engine.verify_worker_control_token(token, "sid2")

    def test_expired_rejected(self):
        token = engine.create_worker_control_token(
            session_id="sid1", conversation_id="c", user_id="u",
            agent_name="a", ttl_s=-120)
        with pytest.raises(ValueError, match="Invalid worker-control token"):
            engine.verify_worker_control_token(token, "sid1")

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            engine.verify_worker_control_token("not-a-jwt", "sid1")

    def test_livekit_room_token_is_not_a_control_token(self):
        """A leaked room token must not open the worker-control WS."""
        cfg = engine.resolve_livekit_config(_cfg())
        token = engine.create_browser_room_token(
            cfg, room_name="r", session_id="sid1", conversation_id="c",
            user_id="u")
        with pytest.raises(ValueError):
            engine.verify_worker_control_token(token, "sid1")


# -- session registry ----------------------------------------------------

class _FakeServiceDef:
    def __init__(self, config, service_type="realtimeVoiceConnection"):
        self.config = config
        self.service_type = service_type


class _FakeRegistry:
    def __init__(self, defs):
        self.defs = defs

    def resolve_definition(self, sid, **_kw):
        return self.defs.get(sid)


@pytest.fixture()
def clean_sessions():
    with sessions._lock:
        sessions._sessions.clear()
        sessions._by_conversation.clear()
    yield
    with sessions._lock:
        sessions._sessions.clear()
        sessions._by_conversation.clear()


@pytest.fixture()
def stubbed_env(monkeypatch, clean_sessions):
    from core import service_registry as sr
    from core import flow_runtime_access as fra
    registry = _FakeRegistry({"lk": _FakeServiceDef(_cfg()),
                              "notrt": _FakeServiceDef(_cfg(),
                                                       "llmConnection")})
    monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                        classmethod(lambda cls: registry))
    monkeypatch.setattr(fra, "conversation_owner", lambda cid: "quentin")
    return registry


class TestSessionRegistry:
    def test_start_returns_browser_payload_and_registers(self, stubbed_env):
        out = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        assert out["livekit_url"] == "ws://localhost:7880"
        assert out["provider"] == "openai"
        assert out["token"]
        session = sessions.get_session(out["session_id"])
        assert session["state"] == "created"
        assert session["room_name"] == out["room"]
        boot = session["worker_bootstrap"]
        assert boot["agent_room_token"] and boot["control_token"]
        # control token is scoped to this very session
        claims = engine.verify_worker_control_token(
            boot["control_token"], out["session_id"])
        assert claims["cid"] == "conv1"

    def test_managed_start_waits_for_provisioning(self, stubbed_env,
                                                  monkeypatch):
        from core.realtime_stack_manager import RealtimeStackManager
        stubbed_env.defs["lkm"] = _FakeServiceDef(
            _cfg(livekit_url=None, livekit_api_key=None,
                 livekit_api_secret=None))
        monkeypatch.setattr(
            RealtimeStackManager, "ensure_stack",
            lambda self: {"state": "provisioning", "detail": "pulling"})
        with pytest.raises(ServiceError, match="provisioning"):
            sessions.start_livekit_session(
                service_id="lkm", conversation_id="conv1",
                agent_name="claude", user_id="quentin")

    def test_managed_start_uses_signal_proxy_path(self, stubbed_env,
                                                  monkeypatch):
        from core.realtime_stack_manager import RealtimeStackManager
        stubbed_env.defs["lkm"] = _FakeServiceDef(
            _cfg(livekit_url=None, livekit_api_key=None,
                 livekit_api_secret=None))
        monkeypatch.setattr(RealtimeStackManager, "ensure_stack",
                            lambda self: {"state": "ready", "detail": ""})
        out = sessions.start_livekit_session(
            service_id="lkm", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        # Browser goes same-origin through the proxy; the worker bootstrap
        # keeps the direct local URL.
        assert out["livekit_url"] == ""
        assert out["livekit_path"] == "/livekit"
        session = sessions.get_session(out["session_id"])
        assert session["worker_bootstrap"]["livekit_url"].startswith(
            "ws://127.0.0.1:")

    def test_api_key_caller_rejected(self, stubbed_env):
        with pytest.raises(PermissionError, match="user session"):
            sessions.start_livekit_session(
                service_id="lk", conversation_id="conv1",
                agent_name="claude", user_id="")

    def test_foreign_conversation_rejected(self, stubbed_env):
        with pytest.raises(PermissionError, match="not your conversation"):
            sessions.start_livekit_session(
                service_id="lk", conversation_id="conv1",
                agent_name="claude", user_id="intruder")

    def test_admin_may_start_on_any_conversation(self, stubbed_env):
        out = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="root", role="admin")
        assert out["session_id"]

    def test_wrong_service_type_rejected(self, stubbed_env):
        with pytest.raises(ServiceError, match="not a realtimeVoiceConnection"):
            sessions.start_livekit_session(
                service_id="notrt", conversation_id="conv1",
                agent_name="claude", user_id="quentin")

    def test_second_start_supersedes_first(self, stubbed_env):
        first = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        second = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        assert sessions.get_session(first["session_id"]) is None
        active = sessions.active_session_for_conversation("conv1")
        assert active["session_id"] == second["session_id"]

    def test_stop_is_idempotent_and_never_poisons_next(self, stubbed_env):
        out = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        assert sessions.stop_livekit_session(
            session_id=out["session_id"], reason="force_stop") is True
        assert sessions.stop_livekit_session(
            session_id=out["session_id"]) is False
        assert sessions.active_session_for_conversation("conv1") is None
        # next session starts cleanly after a force-stop
        again = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        assert sessions.get_session(again["session_id"])["state"] == "created"

    def test_stop_by_conversation(self, stubbed_env):
        sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        assert sessions.stop_livekit_session(conversation_id="conv1") is True


# -- worker-control WS handler --------------------------------------------

class _FakeSock:
    """Captures server->worker frames; feeds nothing (reject paths)."""

    def __init__(self):
        self.sent = b""
        self.closed = False

    def sendall(self, data):
        self.sent += bytes(data)

    def close(self):
        self.closed = True

    def sent_text(self):
        return self.sent.decode("utf-8", errors="ignore")


class TestWorkerControlHandler:
    def test_missing_token_rejected(self, stubbed_env):
        sock = _FakeSock()
        sessions.worker_control_ws_handler(
            sock, {"session_id": "sid1"}, {"query": ""})
        assert "token are required" in sock.sent_text()

    def test_bad_token_rejected(self, stubbed_env):
        sock = _FakeSock()
        sessions.worker_control_ws_handler(
            sock, {"session_id": "sid1"}, {"query": "token=garbage"})
        assert "Invalid worker-control token" in sock.sent_text()

    def test_token_for_other_session_rejected(self, stubbed_env):
        token = engine.create_worker_control_token(
            session_id="other", conversation_id="c", user_id="u",
            agent_name="a", ttl_s=60)
        sock = _FakeSock()
        sessions.worker_control_ws_handler(
            sock, {"session_id": "sid1"}, {"query": f"token={token}"})
        assert "another session" in sock.sent_text()

    def test_valid_token_but_no_active_session_rejected(self, stubbed_env):
        token = engine.create_worker_control_token(
            session_id="sid1", conversation_id="c", user_id="u",
            agent_name="a", ttl_s=60)
        sock = _FakeSock()
        sessions.worker_control_ws_handler(
            sock, {"session_id": "sid1"}, {"query": f"token={token}"})
        assert "not active" in sock.sent_text()

    def test_happy_path_hello_event_toolcall_bye(self, stubbed_env,
                                                 monkeypatch):
        out = sessions.start_livekit_session(
            service_id="lk", conversation_id="conv1", agent_name="claude",
            user_id="quentin")
        sid = out["session_id"]
        token = sessions.get_session(sid)["worker_bootstrap"]["control_token"]

        frames = [
            (0x1, proto.dumps(proto.make_message(
                "hello", session_id=sid, worker_id="w1",
                sdk="livekit-agents")).encode()),
            (0x1, proto.dumps(proto.make_message(
                "event", name="realtime.agent.transcript.final",
                data={"text": "bonjour"})).encode()),
            (0x1, proto.dumps(proto.make_message(
                "tool_call", call_id="c1", name="read",
                arguments={})).encode()),
            (0x1, proto.dumps(proto.make_message(
                "bye", reason="done")).encode()),
        ]
        it = iter(frames)
        monkeypatch.setattr(sessions, "_ws_recv",
                            lambda _sock: next(it, (None, b"")))
        published = []
        monkeypatch.setattr(sessions, "_publish",
                            lambda cid, name, data: published.append(
                                (cid, name, data)))
        sock = _FakeSock()
        sessions.worker_control_ws_handler(
            sock, {"session_id": sid}, {"query": f"token={token}"})

        text = sock.sent_text()
        assert '"hello_ack"' in text
        # No tool_profile on this service: the real bridge (P2) answers
        # with a spoken-friendly unavailability message.
        assert '"tool_result"' in text and "not available" in text
        names = [name for _cid, name, _d in published]
        assert "realtime.session.ready" in names
        assert "realtime.agent.transcript.final" in names
        assert "realtime.session.closed" in names
        # bye ends the session and clears the registry
        assert sessions.get_session(sid) is None


# -- HTTP endpoints --------------------------------------------------------

def _request(body: dict, user_id="quentin", role="user"):
    from services._http_base import PendingRequest
    return PendingRequest(
        request_id="r1", method="POST", path="/api/realtime/livekit/start",
        headers={}, body=json.dumps(body).encode(),
        auth_user_id=user_id, auth_role=role)


class TestHttpEndpoints:
    def test_start_endpoint_success(self, stubbed_env):
        req = _request({"service": "lk", "conversation_id": "conv1",
                        "agent_name": "claude"})
        sessions._start_endpoint(req)
        assert req.response_status == 200
        payload = json.loads(req.response_body)
        assert payload["room"] and payload["token"]

    def test_start_endpoint_forbidden(self, stubbed_env):
        req = _request({"service": "lk", "conversation_id": "conv1",
                        "agent_name": "claude"}, user_id="intruder")
        sessions._start_endpoint(req)
        assert req.response_status == 403

    def test_start_endpoint_bad_config_is_400(self, stubbed_env):
        stubbed_env.defs["broken"] = _FakeServiceDef(
            {"engine": "livekit",
             "livekit_url": "localhost:7880"})  # bad scheme, external mode
        req = _request({"service": "broken", "conversation_id": "conv1",
                        "agent_name": "claude"})
        sessions._start_endpoint(req)
        assert req.response_status == 400
        assert "livekit_url" in json.loads(req.response_body)["error"]

    def test_stop_endpoint_owner_only(self, stubbed_env):
        start = _request({"service": "lk", "conversation_id": "conv1",
                          "agent_name": "claude"})
        sessions._start_endpoint(start)
        sid = json.loads(start.response_body)["session_id"]

        foreign = _request({"session_id": sid}, user_id="intruder")
        sessions._stop_endpoint(foreign)
        assert foreign.response_status == 403

        mine = _request({"session_id": sid})
        sessions._stop_endpoint(mine)
        assert mine.response_status == 200
        assert json.loads(mine.response_body)["stopped"] is True
