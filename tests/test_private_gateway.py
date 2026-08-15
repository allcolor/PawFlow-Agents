"""Private gateway client-IP resolution behind a trusted reverse proxy."""

from services.private_gateway import _effective_client_ip


def test_no_trusted_proxies_uses_direct_peer():
    headers = {"X-Forwarded-For": "203.0.113.9"}
    assert _effective_client_ip("198.51.100.4", headers, {}) == "198.51.100.4"


def test_xff_honoured_only_from_trusted_proxy():
    cfg = {"trusted_proxies": "127.0.0.1"}
    headers = {"X-Forwarded-For": "203.0.113.9"}
    # Direct peer is the proxy -> take client from XFF
    assert _effective_client_ip("127.0.0.1", headers, cfg) == "203.0.113.9"
    # Direct peer is NOT the proxy -> XFF is spoofable, ignore it
    assert _effective_client_ip("198.51.100.4", headers, cfg) == "198.51.100.4"


def test_xff_rightmost_untrusted_hop_wins():
    # Client can prepend junk to XFF; the right-most hop not in the
    # trusted set is the one the trusted proxy actually saw.
    cfg = {"trusted_proxies": "127.0.0.1, 10.0.0.0/8"}
    headers = {"X-Forwarded-For": "6.6.6.6, 203.0.113.9, 10.0.0.5"}
    assert _effective_client_ip("127.0.0.1", headers, cfg) == "203.0.113.9"


def test_xff_missing_or_all_trusted_falls_back_to_peer():
    cfg = {"trusted_proxies": "127.0.0.1"}
    assert _effective_client_ip("127.0.0.1", {}, cfg) == "127.0.0.1"
    headers = {"X-Forwarded-For": "127.0.0.1"}
    assert _effective_client_ip("127.0.0.1", headers, cfg) == "127.0.0.1"


def test_invalid_trusted_proxies_entries_are_ignored():
    cfg = {"trusted_proxies": "not-an-ip, 127.0.0.1"}
    headers = {"X-Forwarded-For": "203.0.113.9"}
    assert _effective_client_ip("127.0.0.1", headers, cfg) == "203.0.113.9"


def test_cidr_trusted_proxies():
    cfg = {"trusted_proxies": "172.18.0.0/16"}
    headers = {"x-forwarded-for": "203.0.113.9"}
    assert _effective_client_ip("172.18.0.2", headers, cfg) == "203.0.113.9"


def test_explicit_gateway_submit_wins_over_a_valid_cookie(monkeypatch):
    """Regression: POST /_gateway must be handled even when already authed.

    The Android app (and any client that keeps its cookies) posts /_gateway
    with the secret to (re)open a session. The already-authenticated cookie
    bypass used to run first, so the POST fell through to normal routing,
    where no route matches /_gateway: the WebView displayed a raw 404 JSON
    instead of the chat.
    """
    import io

    import services.private_gateway as pg

    calls = []
    monkeypatch.setattr(
        pg, "_handle_submit",
        lambda handler, ip, body, *a, **k: calls.append(ip) or True)

    class Handler:
        command = "POST"
        path = "/_gateway"
        client_address = ("203.0.113.5", 1234)
        server = None
        headers = {
            "Cookie": pg._COOKIE_NAME + "=" + pg._make_cookie_value("203.0.113.5"),
            "Content-Length": "0",
        }
        rfile = io.BytesIO(b"")

    assert pg._check_request_inner(Handler(), {"enabled": True}) is True
    assert calls == ["203.0.113.5"]
