"""Registration must bind the wire identity and lifecycle to the target relay."""

import asyncio
import json
import sys
import threading
from types import SimpleNamespace

import pytest

from services import _relay_conn
from services.filesystem_service import RelayService


@pytest.fixture
def session(monkeypatch):
    service = RelayService({
        "_service_id": "alpha", "_scope": "user", "_scope_id": "alice",
        "token": "synthetic-alpha-token",
    })
    sent = []
    effects = []
    closed = []
    writer = SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, "services.tool_relay_service", SimpleNamespace(
        ToolRelayService=SimpleNamespace(
            resync_fence_highwaters=lambda: None,
            _fence_highwater_lock=threading.Lock(), _fence_highwater={},
        ),
    ))
    monkeypatch.setitem(sys.modules, "core.relay_key_integration", SimpleNamespace(
        on_relay_connected=lambda _, identity: effects.append(("connect", identity)),
        on_relay_disconnected=lambda identity: effects.append(("disconnect", identity)),
    ))
    monkeypatch.setitem(sys.modules, "core.service_tunnel_lifecycle", SimpleNamespace(
        on_relay_connected=lambda identity: effects.append(("tunnel", identity)),
    ))
    monkeypatch.setattr(service, "_set_relay",
                        lambda *args: effects.append(("register", "alpha")) or 42.0)
    monkeypatch.setattr(service, "_spawn_ctx_sync",
                        lambda info, identity: effects.append(("sync", identity)))
    monkeypatch.setattr(service, "_clear_relay",
                        lambda **kwargs: effects.append(("clear", "alpha")))
    monkeypatch.setattr(service, "_record_managed_relay_disconnect",
                        lambda when: effects.append(("recovery", when)))

    async def send(writer, data):
        sent.append(json.loads(data))

    async def main_loop(*args):
        return None

    monkeypatch.setattr(_relay_conn, "_ws_send_frame", send)
    monkeypatch.setattr(service, "_relay_main_loop", main_loop)

    def run(registration, acknowledgement=None):
        frames = iter([registration, acknowledgement or {"type": "fence_ack"}])

        async def receive(reader):
            return 1, json.dumps(next(frames)).encode()

        monkeypatch.setattr(_relay_conn, "_ws_recv_frame", receive)

        async def serve():
            await service._serve_relay_session(
                object(), writer, asyncio.get_running_loop(), "synthetic-peer")

        asyncio.run(serve())
        return SimpleNamespace(
            sent=sent, effects=effects, closed=closed, service=service)

    return run


def registration(**changes):
    return {
        "type": "register", "relay_id": "alpha",
        "token": "synthetic-alpha-token", **changes,
    }


@pytest.mark.parametrize("identity", ["beta", "", None, 7, ["alpha"]])
def test_rejects_identity_other_than_target_before_lifecycle(session, identity):
    result = session(registration(relay_id=identity))
    assert result.sent == [{"type": "error", "message": "Relay identity mismatch"}]
    assert result.effects == []
    assert result.service._relay_info == {}
    assert result.closed == [True]


@pytest.mark.parametrize("token", ["wrong-token", "", None])
def test_rejected_token_has_no_disconnect_side_effect(session, token):
    result = session(registration(token=token))
    assert result.sent == [{"type": "error", "message": "Token mismatch"}]
    assert result.effects == []
    assert result.closed == [True]


def test_missing_identity_is_rejected(session):
    message = registration()
    del message["relay_id"]
    result = session(message)
    assert result.sent == [{"type": "error", "message": "Relay identity mismatch"}]
    assert result.effects == []


def test_missing_fence_ack_has_no_disconnect_side_effect(session):
    result = session(registration(), {"type": "wrong-ack"})
    assert result.sent[-1] == {
        "type": "error", "message": "fence snapshot not acknowledged",
    }
    assert result.effects == []


def test_valid_registration_keeps_authenticated_lifecycle(session):
    result = session(registration())
    assert result.sent == [
        {"type": "fence_snapshot", "highwaters": {}},
        {"type": "registered", "relay_id": "alpha"},
    ]
    assert result.effects == [
        ("register", "alpha"), ("sync", "alpha"), ("connect", "alpha"),
        ("tunnel", "alpha"), ("clear", "alpha"), ("recovery", 42.0),
        ("disconnect", "alpha"),
    ]
    assert result.closed == [True]
