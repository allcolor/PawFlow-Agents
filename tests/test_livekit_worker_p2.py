"""P2 tests: worker bootstrap endpoint, tool bridge wiring, transcripts,
and the sidecar control client (aiohttp round-trip, contract-pinned to
services/_realtime_worker_protocol.py). No LiveKit, no providers."""

import asyncio
import json

import pytest

from services import _livekit_sessions as sessions
from services import _realtime_worker_protocol as proto
from pawflow_livekit_worker import control_client


BASE_CFG = {
    "engine": "livekit",
    "livekit_url": "ws://localhost:7880",
    "livekit_api_key": "devkey",
    "livekit_api_secret": "secret",
    "provider": "openai",
    "model": "gpt-realtime",
    "llm_service": "oai",
}


class _FakeServiceDef:
    def __init__(self, config, service_type="realtimeVoiceConnection"):
        self.config = config
        self.service_type = service_type


class _FakeLLM:
    provider = "openai"
    api_key = "sk-test"
    base_url = "https://api.openai.example"
    default_model = "gpt-realtime"


class _FakeRegistry:
    def __init__(self, defs, llms):
        self.defs = defs
        self.llms = llms

    def resolve_definition(self, sid, **_kw):
        return self.defs.get(sid)

    def resolve(self, sid, **_kw):
        return self.llms.get(sid)


@pytest.fixture()
def stubbed_env(monkeypatch):
    with sessions._lock:
        sessions._sessions.clear()
        sessions._by_conversation.clear()
    from core import service_registry as sr
    from core import flow_runtime_access as fra
    registry = _FakeRegistry({"lk": _FakeServiceDef(dict(BASE_CFG))},
                             {"oai": _FakeLLM()})
    monkeypatch.setattr(sr.ServiceRegistry, "get_instance",
                        classmethod(lambda cls: registry))
    monkeypatch.setattr(fra, "conversation_owner", lambda cid: "quentin")
    import services._realtime_bridge as bridge
    monkeypatch.setattr(bridge, "resolve_session_instructions",
                        lambda shim, cid, agent, uid: f"You are {agent}.")
    yield registry
    with sessions._lock:
        sessions._sessions.clear()
        sessions._by_conversation.clear()


def _start(**kw):
    return sessions.start_livekit_session(
        service_id="lk", conversation_id="conv1", agent_name="claude",
        user_id="quentin", **kw)


def _bootstrap_request(body, secret="wsecret"):
    from services._http_base import PendingRequest
    headers = {}
    if secret is not None:
        headers["X-PawFlow-Worker-Secret"] = secret
    return PendingRequest(
        request_id="r1", method="POST",
        path="/api/realtime/livekit/worker/bootstrap", headers=headers,
        body=json.dumps(body).encode())


class TestWorkerBootstrapEndpoint:
    def test_disabled_without_env(self, stubbed_env, monkeypatch):
        monkeypatch.delenv(sessions._WORKER_SECRET_ENV, raising=False)
        req = _bootstrap_request({"room": "x"})
        sessions._worker_bootstrap_endpoint(req)
        assert req.response_status == 503
        assert "PAWFLOW_REALTIME_WORKER_SECRET" in \
            json.loads(req.response_body)["error"]

    def test_bad_secret_rejected(self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        req = _bootstrap_request({"room": "x"}, secret="wrong")
        sessions._worker_bootstrap_endpoint(req)
        assert req.response_status == 403

    def test_unknown_room_404(self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        req = _bootstrap_request({"room": "nope"})
        sessions._worker_bootstrap_endpoint(req)
        assert req.response_status == 404

    def test_success_payload(self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        started = _start()
        req = _bootstrap_request({"room": started["room"]})
        sessions._worker_bootstrap_endpoint(req)
        assert req.response_status == 200
        boot = json.loads(req.response_body)
        assert boot["session_id"] == started["session_id"]
        assert boot["control_token"] and boot["agent_room_token"]
        assert boot["provider"] == "openai"
        assert boot["instructions"] == "You are claude."
        assert boot["credentials"]["api_key"] == "sk-test"
        assert boot["tools"] == []  # no tool_profile configured
        # browser payload must NOT contain provider credentials
        assert "credentials" not in started
        assert "sk-test" not in json.dumps(started)

    def test_bootstrap_carries_video_and_local_pipeline_settings(
            self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        stubbed_env.defs["lk"].config.update({
            "video_input": True, "video_fps_active": 2,
            "video_fps_idle": 0.5, "local_stt_url": "http://stt:8001/v1"})
        started = _start()
        req = _bootstrap_request({"room": started["room"]})
        sessions._worker_bootstrap_endpoint(req)
        boot = json.loads(req.response_body)
        assert boot["video_input"] is True
        assert boot["video_fps_active"] == 2.0
        assert boot["video_fps_idle"] == 0.5
        assert boot["local_pipeline"] == {
            "local_stt_url": "http://stt:8001/v1"}

    def test_provider_llm_mismatch_400(self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        stubbed_env.defs["lk"].config["provider"] = "gemini"
        started = _start()
        req = _bootstrap_request({"room": started["room"]})
        sessions._worker_bootstrap_endpoint(req)
        assert req.response_status == 400
        assert "requires a 'gemini' llmConnection" in \
            json.loads(req.response_body)["error"]

    def test_missing_credentials_400(self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        stubbed_env.defs["lk"].config["llm_service"] = ""
        started = _start()
        req = _bootstrap_request({"room": started["room"]})
        sessions._worker_bootstrap_endpoint(req)
        assert req.response_status == 400
        assert "llm_service" in json.loads(req.response_body)["error"]

    def test_provider_secret_env_passthrough(self, stubbed_env, monkeypatch):
        monkeypatch.setenv(sessions._WORKER_SECRET_ENV, "wsecret")
        stubbed_env.defs["lk"].config["llm_service"] = ""
        stubbed_env.defs["lk"].config["provider_secret"] = "XAI_API_KEY"
        started = _start()
        req = _bootstrap_request({"room": started["room"]})
        sessions._worker_bootstrap_endpoint(req)
        boot = json.loads(req.response_body)
        assert boot["credentials"] == {"source": "env",
                                       "env_var": "XAI_API_KEY"}


class _FakeSock:
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += bytes(data)

    def close(self):
        pass

    def sent_text(self):
        return self.sent.decode("utf-8", errors="ignore")


def _run_handler_with_frames(monkeypatch, session_id, token, frames):
    it = iter(frames)
    monkeypatch.setattr(sessions, "_ws_recv",
                        lambda _s: next(it, (None, b"")))
    published = []
    monkeypatch.setattr(sessions, "_publish",
                        lambda cid, name, data: published.append(
                            (name, data)))
    sock = _FakeSock()
    sessions.worker_control_ws_handler(
        sock, {"session_id": session_id}, {"query": f"token={token}"})
    return sock, published


class TestToolBridgeWiring:
    def test_tool_call_runs_through_bridge(self, stubbed_env, monkeypatch):
        started = _start()
        sid = started["session_id"]
        session = sessions.get_session(sid)
        token = session["worker_bootstrap"]["control_token"]

        class _FakeBridge:
            calls = []

            def handle_call(self, call_id, name, arguments, *, send_result,
                            announce=None, **_kw):
                self.calls.append((call_id, name, arguments))
                send_result(call_id, f"ran {name}")
                return "done"

        session["tool_bridge"] = _FakeBridge()
        frames = [
            (0x1, proto.dumps(proto.make_message(
                "tool_call", call_id="c1", name="recall",
                arguments={"query": "x"})).encode()),
            (0x1, proto.dumps(proto.make_message(
                "bye", reason="done")).encode()),
        ]
        sock, published = _run_handler_with_frames(
            monkeypatch, sid, token, frames)
        assert _FakeBridge.calls == [("c1", "recall", {"query": "x"})]
        text = sock.sent_text()
        assert '"tool_result"' in text and "ran recall" in text
        names = [n for n, _ in published]
        assert "realtime.tool.started" in names
        assert "realtime.tool.completed" in names

    def test_denied_tool_publishes_rejected(self, stubbed_env, monkeypatch):
        started = _start()
        sid = started["session_id"]
        session = sessions.get_session(sid)
        token = session["worker_bootstrap"]["control_token"]

        class _DenyBridge:
            def handle_call(self, call_id, name, arguments, *, send_result,
                            announce=None, **_kw):
                send_result(call_id, "refused")
                return "denied"

        session["tool_bridge"] = _DenyBridge()
        frames = [(0x1, proto.dumps(proto.make_message(
            "tool_call", call_id="c1", name="bash",
            arguments={})).encode())]
        _sock, published = _run_handler_with_frames(
            monkeypatch, sid, token, frames)
        assert ("realtime.tool.rejected",
                {"session_id": sid, "tool": "bash", "status": "denied"}) in \
            published


class TestTranscriptPersistence:
    def test_final_transcripts_persist_as_messages(self, stubbed_env,
                                                   monkeypatch):
        started = _start()
        sid = started["session_id"]
        token = sessions.get_session(sid)["worker_bootstrap"]["control_token"]
        persisted = []
        import services._realtime_bridge as bridge
        monkeypatch.setattr(
            bridge, "persist_voice_transcript",
            lambda cid, agent, uid, role, text, channel="voice":
                persisted.append((cid, agent, uid, role, text)))
        frames = [
            (0x1, proto.dumps(proto.make_message(
                "event", name="realtime.user.transcript.final",
                data={"text": "quelle heure ?"})).encode()),
            (0x1, proto.dumps(proto.make_message(
                "event", name="realtime.user.transcript.delta",
                data={"text": "qu"})).encode()),
            (0x1, proto.dumps(proto.make_message(
                "event", name="realtime.agent.transcript.final",
                data={"text": "Il est midi."})).encode()),
        ]
        _run_handler_with_frames(monkeypatch, sid, token, frames)
        assert persisted == [
            ("conv1", "claude", "quentin", "user", "quelle heure ?"),
            ("conv1", "claude", "quentin", "assistant", "Il est midi."),
        ]


class TestWorkerProviderMapping:
    """Static introspection (house pattern): the worker maps every engine
    provider — live behavior is validated in the owner's E2E pass."""

    def test_all_providers_wired(self):
        from pathlib import Path
        src = Path("pawflow_livekit_worker/worker.py").read_text(
            encoding="utf-8")
        assert 'provider == "openai"' in src
        assert 'provider == "gemini"' in src
        assert "with_azure" in src                      # azure_openai
        assert "api.x.ai" in src                        # xai
        assert "livekit-plugins-aws" in src             # aws_nova guard
        assert 'provider == "local_pipeline"' in src
        # config-driven local pipeline endpoints override worker env
        assert 'local.get(key, "") or os.environ.get(env' in src
        # P4 frame sampling settings
        assert "VoiceActivityVideoSampler" in src
        assert "video_fps_active" in src and "video_fps_idle" in src


# -- sidecar control client (aiohttp) --------------------------------------

class TestControlClientContract:
    def test_client_messages_match_server_protocol(self):
        """The worker's duplicated message builder stays contract-compatible
        with the server-side protocol module."""
        for msg_type, payload in [
            ("hello", {"session_id": "s", "worker_id": "w", "sdk": "lk"}),
            ("event", {"name": "realtime.usage", "data": {}}),
            ("tool_call", {"call_id": "c", "name": "read",
                           "arguments": {}}),
            ("bye", {"reason": "done"}),
        ]:
            wire = json.dumps(control_client._make_message(
                msg_type, **payload))
            parsed = proto.parse_message(wire)
            assert parsed["type"] == msg_type


@pytest.mark.asyncio
async def test_control_client_round_trip():
    """Client against a fake PawFlow endpoint speaking the real protocol."""
    from aiohttp import web, WSMsgType
    from aiohttp.test_utils import TestClient, TestServer

    contexts = []
    shutdowns = []

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            message = proto.parse_message(msg.data)
            if message["type"] == "hello":
                await ws.send_str(proto.dumps(proto.make_message(
                    "hello_ack", session_id=message["session_id"])))
                await ws.send_str(proto.dumps(proto.make_message(
                    "context", text="late tool result")))
            elif message["type"] == "tool_call":
                await ws.send_str(proto.dumps(proto.make_message(
                    "tool_result", call_id=message["call_id"], ok=True,
                    result={"text": "pong"})))
            elif message["type"] == "bye":
                break
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws/realtime-worker/{session_id}", handler)
    server_client = TestClient(TestServer(app))
    await server_client.start_server()
    try:
        port = server_client.server.port
        url = (f"http://127.0.0.1:{port}/ws/realtime-worker/s1?token=t")

        async def on_context(text):
            contexts.append(text)

        async def on_shutdown(reason):
            shutdowns.append(reason)

        client = control_client.WorkerControlClient(
            url, "s1", "w1", on_context=on_context,
            on_shutdown=on_shutdown)
        await client.connect()
        await client.send_event("realtime.session.ready", {})
        outcome = await client.call_tool("echo", {"q": "ping"}, timeout=5)
        assert outcome == {"ok": True, "result": {"text": "pong"}}
        await asyncio.sleep(0.05)   # let the context message dispatch
        assert contexts == ["late tool result"]
        await client.close("test done")
        assert client.closed.is_set()
    finally:
        await server_client.close()


@pytest.mark.asyncio
async def test_control_client_rejected_handshake():
    """A shutdown reply during handshake surfaces as ConnectionError."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()   # hello
        await ws.send_str(proto.dumps(proto.make_message(
            "shutdown", reason="bad token")))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws/realtime-worker/{session_id}", handler)
    server_client = TestClient(TestServer(app))
    await server_client.start_server()
    try:
        port = server_client.server.port
        client = control_client.WorkerControlClient(
            f"http://127.0.0.1:{port}/ws/realtime-worker/s1?token=bad",
            "s1", "w1")
        with pytest.raises(ConnectionError, match="bad token"):
            await client.connect()
    finally:
        if client._http is not None:
            await client._http.close()
        await server_client.close()
