"""Relay-aware provider URL helper tests."""

import pytest

from core import ServiceError
from core.relay_proxy_url import (
    maybe_transform_relay_proxy_url,
    parse_relay_proxy_url,
    relay_proxy_ssl_context,
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


def test_parse_native_relay_proxy_url_schemes():
    plain = parse_relay_proxy_url("relay://relay_a/localhost:11434/v1")
    secure = parse_relay_proxy_url("relays://relay_a/api.example.test:443/v1?q=1")

    assert plain.relay_id == "relay_a"
    assert plain.target_scheme == "http"
    assert plain.target_host == "localhost"
    assert plain.target_port == 11434
    assert plain.target_path == "/v1"
    assert secure.relay_id == "relay_a"
    assert secure.target_scheme == "https"
    assert secure.target_host == "api.example.test"
    assert secure.target_port == 443
    assert secure.target_path == "/v1"
    assert secure.query == "q=1"


def test_transform_standard_relay_proxy_url(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")

    url = maybe_transform_relay_proxy_url(
        "https://relay_a/localhost:9443/v1?q=1", user_id="alice")

    assert url == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/s/localhost:9443/v1?q=1"


def test_transform_native_relay_proxy_url(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")

    plain = maybe_transform_relay_proxy_url(
        "relay://relay_a/localhost:11434/v1", user_id="alice", relay_local=True)
    secure = maybe_transform_relay_proxy_url(
        "relays://relay_a/api.example.test:443/v1?q=1", user_id="alice")

    assert plain == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/l/localhost:11434/v1"
    assert secure == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/s/api.example.test:443/v1?q=1"


def test_transform_relay_proxy_url_refuses_public_listener_address(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "8.8.8.8")

    assert maybe_transform_relay_proxy_url(
        "relay://relay_a/localhost:11434/v1", user_id="alice") is None


def test_relay_proxy_ssl_context_only_skips_verification_for_private_proxy(monkeypatch):
    monkeypatch.setattr(
        "core.relay_proxy_url.ssl._create_unverified_context",
        lambda: "unverified",
    )
    monkeypatch.setattr(
        "core.relay_proxy_url.ssl.create_default_context",
        lambda: "default",
    )

    assert relay_proxy_ssl_context(
        "https://10.0.0.2:9090/relay-proxy/relay_a/tok/localhost:11434/v1"
    ) == "unverified"
    assert relay_proxy_ssl_context(
        "https://example.com/relay-proxy/relay_a/tok/localhost:11434/v1"
    ) == "default"
    assert relay_proxy_ssl_context("https://10.0.0.2:9090/other") == "default"


def test_resolve_native_relay_proxy_url(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")

    url = resolve_relay_aware_url(
        "relay://relay_a/localhost:11434/v1",
        user_id="alice",
        conversation_id="conv1",
        service_name="Test service",
    )

    assert url == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/localhost:11434/v1"


def test_transform_relay_proxy_url_with_local_mode(monkeypatch):
    monkeypatch.setattr(_hl_mod, "_instances", {9090: _Listener()})
    monkeypatch.setattr("core.relay_proxy_auth.issue_token", lambda user_id, relay_id, conv_id="": "tok")
    monkeypatch.setattr("core.relay_proxy_url.get_host_ip", lambda: "10.0.0.2")

    local_url = maybe_transform_relay_proxy_url(
        "http://relay_a/localhost:11434/v1", user_id="alice",
        relay_local=True)
    container_url = maybe_transform_relay_proxy_url(
        "http://relay_a/localhost:11434/v1", user_id="alice",
        relay_local=False)

    assert local_url == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/l/localhost:11434/v1"
    assert container_url == "http://10.0.0.2:9090/relay-proxy/relay_a/tok/c/localhost:11434/v1"


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
