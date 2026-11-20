"""Gateway origin derived from the relay WebSocket URL (manual mode)."""

import pytest

from pawflow_relay.cli import gateway_url_from_ws


@pytest.mark.parametrize("ws_url, expected", [
    # No explicit port: no port in the origin, so HTTPS uses 443.
    ("wss://webchat.example.org/ws/relay/mcp_cli_x", "https://webchat.example.org"),
    ("ws://webchat.example.org/ws/relay/r1", "http://webchat.example.org"),
    # An explicit port is preserved.
    ("wss://host.example:8443/ws/relay/r1", "https://host.example:8443"),
    ("ws://localhost:9090/ws/relay/r1", "http://localhost:9090"),
])
def test_gateway_url_from_ws(ws_url, expected):
    assert gateway_url_from_ws(ws_url) == expected


def test_wss_without_port_reaches_https_default_port():
    """Regression: wss://host used to become https://host:80 (TLS on HTTP)."""
    from urllib.parse import urlparse
    origin = urlparse(gateway_url_from_ws("wss://webchat.example.org/ws/relay/r"))
    assert origin.scheme == "https"
    assert origin.port is None  # acquire_gateway_cookie then defaults to 443
