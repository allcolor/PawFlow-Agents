"""Relay-aware provider URL helper tests."""

import pytest

from core import ServiceError
from core.relay_proxy_url import (
    maybe_transform_relay_proxy_url,
    parse_relay_proxy_url,
    resolve_relay_aware_url,
)
from services import http_listener_service as _hl_mod


class _Listener:
    is_ssl = False
    public_hostname = ""


def test_parse_standard_relay_proxy_url():
    parsed = parse_relay_proxy_url("https://relay_a/localhost:9443/v1?q=1")

    assert parsed.relay_id == "relay_a"
    assert parsed.target_scheme == "https"
    assert parsed.target_host == "localhost"
    assert parsed.target_port == 9443
    assert parsed.target_path == "/v1"
    assert parsed.query == "q=1"


def test_transform_standard_relay_proxy_url(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")

    url = maybe_transform_relay_proxy_url(
        "https://relay_a/localhost:9443/v1?q=1", user_id="alice")

    assert url == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/s/localhost:9443/v1?q=1"


def test_relay_shaped_url_validates_without_runtime_context():
    url = resolve_relay_aware_url(
        "http://${conv.relay}/localhost:7788",
        service_name="Test service",
        transform_relay=False,
    )

    assert url == "http://${conv.relay}/localhost:7788"


def test_direct_private_url_requires_opt_in():
    with pytest.raises(ServiceError, match="private/local network"):
        resolve_relay_aware_url(
            "http://127.0.0.1:7788",
            service_name="Test service",
        )

    assert resolve_relay_aware_url(
        "http://127.0.0.1:7788",
        service_name="Test service",
        allow_private=True,
    ) == "http://127.0.0.1:7788"
