"""P0 tests: LiveKit dependency guard + worker-control spike protocol.

No LiveKit install and no provider calls: covers services/livekit_deps.py
and the protocol prototype in spikes/livekit/control_protocol.py, including
a real WebSocket tool round-trip against the spike server handler on a
local aiohttp test server.
"""

import sys
from pathlib import Path

import pytest

from services import livekit_deps

SPIKE_DIR = Path(__file__).resolve().parents[1] / "spikes" / "livekit"
if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

import control_protocol as cp  # noqa: E402


class TestLivekitDepsGuard:
    def test_missing_deps_raise_clear_setup_error(self, monkeypatch):
        monkeypatch.setattr(livekit_deps, "REQUIRED_MODULES", {
            "definitely_not_a_module_xyz": "livekit-agents",
            "also_not_a_module.sub": "livekit-plugins-openai",
            "json": "json",
        })
        with pytest.raises(RuntimeError) as exc:
            livekit_deps.require_livekit()
        text = str(exc.value)
        assert "livekit-agents" in text
        assert "livekit-plugins-openai" in text
        assert "json" not in text.split("Install")[0].replace(
            "packages", "")  # present module not reported as missing
        assert 'pip install "pawflow[realtime-livekit]"' in text

    def test_all_present_passes(self, monkeypatch):
        monkeypatch.setattr(livekit_deps, "REQUIRED_MODULES",
                            {"json": "json", "os.path": "os"})
        livekit_deps.require_livekit()  # must not raise


class TestControlProtocol:
    def test_make_parse_round_trip(self):
        msg = cp.make_message("tool_call", call_id="c1", name="echo",
                              arguments={"a": 1})
        parsed = cp.parse_message(cp.dumps(msg))
        assert parsed == msg
        assert parsed["id"] and parsed["ts"]  # UUID + timestamp convention

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError, match="Unknown"):
            cp.make_message("nope")
        with pytest.raises(ValueError, match="Unknown"):
            cp.parse_message('{"type": "nope", "id": "x", "ts": 1}')

    def test_missing_fields_rejected(self):
        with pytest.raises(ValueError, match="missing required fields"):
            cp.make_message("hello", session_id="s")
        msg = cp.make_message("hello", session_id="s", worker_id="w",
                              sdk="test")
        del msg["worker_id"]
        with pytest.raises(ValueError, match="worker_id"):
            cp.parse_message(cp.dumps(msg))

    def test_not_json_rejected(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            cp.parse_message("pcm16 garbage")
        with pytest.raises(ValueError, match="JSON object"):
            cp.parse_message("[1, 2]")

    def test_stub_handshake_events_and_unknown_tool(self):
        stub = cp.PawFlowControlStub()
        replies = stub.reply(cp.make_message(
            "hello", session_id="s1", worker_id="w", sdk="test"))
        assert [r["type"] for r in replies] == ["hello_ack"]
        assert replies[0]["session_id"] == "s1"

        assert stub.reply(cp.make_message(
            "event", name="realtime.session.ready", data={})) == []
        assert stub.events == [("realtime.session.ready", {})]

        replies = stub.reply(cp.make_message(
            "tool_call", call_id="c9", name="no_such_tool", arguments={}))
        assert replies[0]["type"] == "tool_result"
        assert replies[0]["ok"] is False
        assert "no_such_tool" in replies[0]["result"]["error"]

        assert stub.reply(cp.make_message("bye", reason="done")) == []
        assert stub.closed_reason == "done"

    def test_stub_rejects_server_side_types(self):
        stub = cp.PawFlowControlStub()
        with pytest.raises(ValueError, match="Unexpected"):
            stub.reply(cp.make_message("shutdown", reason="x"))


@pytest.mark.asyncio
async def test_worker_control_websocket_tool_round_trip():
    """Full round-trip against the spike server handler over a real WS."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    import spike_control_server as srv

    app = web.Application()
    app.router.add_get("/ws/realtime-worker/{session_id}",
                       srv.worker_control)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        ws = await client.ws_connect("/ws/realtime-worker/t1")
        await ws.send_str(cp.dumps(cp.make_message(
            "hello", session_id="t1", worker_id="w1", sdk="test")))
        ack = cp.parse_message((await ws.receive()).data)
        assert ack["type"] == "hello_ack" and ack["session_id"] == "t1"

        await ws.send_str(cp.dumps(cp.make_message(
            "tool_call", call_id="c1", name="echo",
            arguments={"question": "ping"})))
        result = cp.parse_message((await ws.receive()).data)
        assert result["type"] == "tool_result"
        assert result["call_id"] == "c1"
        assert result["ok"] is True
        assert result["result"] == {"question": "ping"}

        await ws.send_str(cp.dumps(cp.make_message("bye", reason="done")))
        closed = await ws.receive()
        assert closed.type.name in ("CLOSE", "CLOSED", "CLOSING")
    finally:
        await client.close()
