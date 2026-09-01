"""Service Tunnel deployment artifact contracts."""

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_compose_exposes_pinned_optional_frps_profile():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    pawflow = compose["services"]["pawflow"]
    frps = compose["services"]["frps"]

    assert "PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY=${PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY:-}" in pawflow["environment"]
    assert frps["image"] == "fatedier/frps:v0.70.1"
    assert frps["profiles"] == ["service-tunnels"]
    assert frps["depends_on"]["pawflow"]["condition"] == "service_healthy"
    assert len(frps["ports"]) == 2
    assert any(port.endswith("/tcp") for port in frps["ports"])
    assert any(port.endswith("/udp") for port in frps["ports"])


def test_frps_config_forces_tls_and_authorizes_login_and_proxy():
    config = (ROOT / "docker" / "frps.toml").read_text(encoding="utf-8")

    assert "transport.tls.force = true" in config
    assert 'auth.token = "{{ .Envs.PAWFLOW_FRPS_TOKEN }}"' in config
    assert config.count('path = "/internal/service-tunnels/frp"') == 2
    assert 'ops = ["Login"]' in config
    assert 'ops = ["NewProxy"]' in config
    assert "webServer.port" not in config
    assert "vhostHTTPPort" not in config
    assert "vhostHTTPSPort" not in config


def test_server_startup_registers_private_authorizer():
    source = (ROOT / "cli.py").read_text(encoding="utf-8")
    restore = source.index("er.restore_from_disk(")
    register = source.index("ensure_service_tunnel_route()", restore)
    ready = source.index("PawFlow server ready", register)
    assert restore < register < ready
