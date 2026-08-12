import os

import pytest

from pawflow_relay import service_tunnels as st


@pytest.fixture(autouse=True)
def isolated_relay_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWFLOW_RELAY_HOME", str(tmp_path / "relay"))
    st._PROCESSES.clear()
    yield
    st._PROCESSES.clear()


def _message(role="service", **overrides):
    data = {
        "role": role,
        "tunnel_id": "tunnel1",
        "relay_id": "home",
        "server_name": "pft_tunnel1",
        "frps_server": "pawflow.example",
        "frps_port": 7000,
        "frps_token": "shared-auth",
        "grant": "signed.grant",
        "secret_key": "tunnel-secret",
        "transport": "quic",
        "service_id": "ssh",
        "bind_host": "127.0.0.1",
        "bind_port": 22022,
    }
    data.update(overrides)
    return data


def test_catalog_is_scoped_by_relay_and_persistent():
    saved = st.save_service("home", {
        "service_id": "ssh", "name": "Home SSH", "protocol": "tcp",
        "target_host": "127.0.0.1", "target_port": 22})
    assert saved["target_port"] == 22
    assert st.list_services("laptop") == []
    assert st.list_services("home") == [saved]
    assert st.delete_service("home", "ssh") is True
    assert st.list_services("home") == []


def test_catalog_actions_create_list_and_delete():
    saved = st.handle_action("service_tunnel_catalog_save", {
        "relay_id": "home",
        "service": {"service_id": "ssh", "name": "SSH", "protocol": "tcp",
                    "target_host": "127.0.0.1", "target_port": 22},
    })
    assert saved["service"]["service_id"] == "ssh"
    assert st.handle_action("service_tunnel_catalog", {
        "relay_id": "home"})["services"] == [saved["service"]]
    assert st.handle_action("service_tunnel_catalog_delete", {
        "relay_id": "home", "service_id": "ssh"}) == {"deleted": True}


def test_service_config_uses_only_approved_catalog_target():
    st.save_service("home", {
        "service_id": "ssh", "name": "Home SSH", "protocol": "tcp",
        "target_host": "127.0.0.1", "target_port": 22})
    config = st.build_frpc_config(_message(target_host="10.0.0.8", target_port=5432))
    assert 'type = "stcp"' in config
    assert 'localIP = "127.0.0.1"' in config
    assert "localPort = 22" in config
    assert "5432" not in config
    assert 'metadatas.pawflow_grant = "signed.grant"' in config


def test_access_config_is_loopback_only():
    config = st.build_frpc_config(_message(role="access", relay_id="laptop"))
    assert "[[visitors]]" in config
    assert 'bindAddr = "127.0.0.1"' in config
    assert "bindPort = 22022" in config
    with pytest.raises(ValueError, match="loopback-only"):
        st.build_frpc_config(_message(
            role="access", relay_id="laptop", bind_host="0.0.0.0"))


def test_unapproved_service_is_rejected():
    with pytest.raises(KeyError, match="not approved"):
        st.build_frpc_config(_message())


def test_apply_is_idempotent_and_stop_terminates(monkeypatch):
    st.save_service("home", {
        "service_id": "ssh", "name": "Home SSH", "protocol": "tcp",
        "target_host": "127.0.0.1", "target_port": 22})

    class Process:
        def __init__(self):
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    created = []

    def fake_popen(*args, **kwargs):
        process = Process()
        created.append((args, kwargs, process))
        return process

    monkeypatch.setattr(st, "_frpc_binary", lambda: "/opt/frp/frpc")
    monkeypatch.setattr(st.subprocess, "Popen", fake_popen)
    first = st.apply_tunnel(_message())
    second = st.apply_tunnel(_message())
    assert first["already_running"] is False
    assert second["already_running"] is True
    assert len(created) == 1
    state = next(iter(st._PROCESSES.values()))
    assert os.stat(state["config_path"]).st_mode & 0o077 == 0
    stopped = st.stop_tunnel("tunnel1", "service")
    assert stopped == {"running": False, "stopped": True, "role": "service"}
    assert created[0][2].terminated is True
