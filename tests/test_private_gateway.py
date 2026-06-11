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
