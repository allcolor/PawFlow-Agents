"""RelayThread must not treat a service_install error payload as success.

The server answers install failures with HTTP 200 + {"error": ...}. When the
relay ignored it, /ws/relay/<id> stayed unregistered and every WebSocket
handshake answered 400 with nothing explaining why.
"""

import pytest

from pawflow_relay.thread import RelayThread


def _relay(tmp_path, reply):
    relay = RelayThread("https://pawflow.invalid", "session", "alice", str(tmp_path),
                        relay_id="Ultima7D")
    relay.ws_token = "test-token"
    relay._api = lambda method, path, body: reply
    return relay


def test_install_error_payload_raises(tmp_path):
    relay = _relay(tmp_path, {"error": "Service installation already running: Ultima7D"})
    with pytest.raises(RuntimeError, match="already running"):
        relay._install_service()


def test_reregister_surfaces_install_error(tmp_path):
    relay = _relay(tmp_path, {"error": "boom"})
    with pytest.raises(RuntimeError, match="Ultima7D failed: boom"):
        relay._reregister_service()


def test_install_success_payload_passes(tmp_path):
    relay = _relay(tmp_path, {"installed": True, "id": "Ultima7D"})
    relay._install_service()
