"""The stdio MCP bridge survives a PawFlow server restart."""

from pawflow_relay import mcp_stdio
from pawflow_relay.mcp_stdio import HTTPBridge, RelayController

_INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"clientInfo": {"name": "agy"}}}
_CALL = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "read_inbox", "arguments": {}}}


class _Server:
    """Scripted HTTP answers; records what the bridge sent."""

    def __init__(self, bridge, answers):
        self.bridge = bridge
        self.answers = list(answers)
        self.sent = []

    def __call__(self, method, url, payload=None, include_session=False):
        self.sent.append((payload.get("method"), payload.get("id"),
                          self.bridge.session_id if include_session else ""))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body, session = answer
        if session:
            self.bridge.session_id = session
        return status, {}, body


def _bridge(monkeypatch, answers):
    bridge = HTTPBridge("https://pawflow.example/mcp/srv_x", "key")
    server = _Server(bridge, answers)
    monkeypatch.setattr(bridge, "_request", server)
    monkeypatch.setattr(mcp_stdio.time, "sleep", lambda _s: None)
    return bridge, server


def test_a_lost_session_is_reinitialized_and_the_call_sent_once_more(monkeypatch):
    ok = {"jsonrpc": "2.0", "id": 7, "result": {}}
    bridge, server = _bridge(monkeypatch, [
        (200, {"result": {}}, "s1"),
        (404, {"error": "MCP session not found"}, ""),
        (200, {"result": {}}, "s2"),
        (202, None, ""),
        (200, ok, ""),
    ])
    bridge.rpc(_INIT)

    assert bridge.rpc(_CALL) == (200, ok)
    methods = [sent[0] for sent in server.sent]
    assert methods == ["initialize", "tools/call", "initialize",
                       "notifications/initialized", "tools/call"]
    assert server.sent[1][2] == "s1" and server.sent[4][2] == "s2"
    assert server.sent[2][1] != 0  # the replayed initialize has its own id


def test_other_404s_are_not_retried(monkeypatch):
    bridge, server = _bridge(monkeypatch, [
        (200, {"result": {}}, "s1"),
        (404, {"error": "Unknown server"}, ""),
    ])
    bridge.rpc(_INIT)
    assert bridge.rpc(_CALL)[0] == 404
    assert len(server.sent) == 2


def test_without_an_initialize_to_replay_the_404_is_returned(monkeypatch):
    bridge, server = _bridge(monkeypatch, [
        (404, {"error": "MCP session not found"}, ""),
    ])
    assert bridge.rpc(_CALL)[0] == 404
    assert len(server.sent) == 1


def test_a_restarting_server_is_waited_for(monkeypatch):
    ok = {"jsonrpc": "2.0", "id": 7, "result": {}}
    bridge, server = _bridge(monkeypatch, [
        ConnectionRefusedError(),
        (502, {"error": "Bad Gateway"}, ""),
        (200, ok, ""),
    ])
    assert bridge.rpc(_CALL) == (200, ok)
    assert len(server.sent) == 3


def test_a_server_down_for_good_still_fails(monkeypatch):
    monkeypatch.setattr(mcp_stdio, "_UNAVAILABLE_RETRY_SECONDS", 0.0)
    bridge, _server = _bridge(monkeypatch, [ConnectionRefusedError()])
    try:
        bridge.rpc(_CALL)
    except ConnectionRefusedError:
        return
    raise AssertionError("a refused connection must surface once retries end")


def test_a_failed_notification_gets_no_response():
    # A response with "id": null made Agy close the MCP connection.
    assert mcp_stdio._http_error_response(
        None, "notifications/cancelled", 404,
        {"error": "MCP session not found"}) is None


def test_a_failed_request_gets_an_error_with_its_id():
    response = mcp_stdio._http_error_response(
        7, "tools/call", 404, {"error": "MCP session not found"})
    assert response == {"jsonrpc": "2.0", "id": 7, "error": {
        "code": -32000, "message": "MCP session not found"}}
    assert mcp_stdio._http_error_response(
        8, "tools/call", 502, None)["error"]["message"] == "PawFlow returned HTTP 502"


class _Proc:
    def __init__(self, alive):
        self.alive = alive

    def poll(self):
        return None if self.alive else 1


def _controller(tmp_path, monkeypatch, alive):
    controller = RelayController(object(), tmp_path, "agy")
    controller._process = _Proc(alive)
    calls = []
    monkeypatch.setattr(controller, "reconnect", lambda: calls.append(1))
    return controller, calls


def test_a_dead_relay_process_is_reconnected(tmp_path, monkeypatch):
    controller, calls = _controller(tmp_path, monkeypatch, alive=False)
    controller._recover({"client_id": controller.client_id})
    assert calls == [1]


def test_a_released_lease_is_reclaimed(tmp_path, monkeypatch):
    controller, calls = _controller(tmp_path, monkeypatch, alive=True)
    controller._recover({"client_id": ""})
    assert calls == [1]


def test_a_lease_held_by_another_client_is_left_alone(tmp_path, monkeypatch):
    controller, calls = _controller(tmp_path, monkeypatch, alive=True)
    controller._recover({"client_id": "cli_other"})
    controller._recover({"client_id": controller.client_id})
    assert calls == []
