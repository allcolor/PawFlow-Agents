"""Phase 10 — route security regression matrix.

Direct unit-level coverage of the per-route capability checks. We do
not spin up the HTTP listener here; instead we drive the *_proxy
handlers with synthetic PendingRequest / WS-meta dicts and assert the
verdict for each (auth, ownership, resource_type) combination listed
in the security plan. End-to-end HTTP coverage already lives in
`test_user_services.py` for the integration paths.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import pytest

from core import capability_auth as ca
from core import capability_routes as cr


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


@dataclass
class _FakePendingReq:
    """Minimal stand-in for `services.http_listener_service.PendingRequest`.
    Only carries the attributes capability_routes inspects."""
    auth_user_id: str = ""
    auth_role: str = ""
    auth_session_id: str = ""
    remote_addr: str = ""
    path_params: Dict[str, str] = field(default_factory=dict)
    completed: Optional[Tuple[int, Dict[str, str], bytes]] = None

    def complete(self, status: int, headers: Dict[str, str], body: bytes):
        self.completed = (status, dict(headers), bytes(body))


@pytest.fixture()
def cap_db(tmp_path):
    ca._reset_for_tests()
    ca.init_db(tmp_path / "caps.json")
    yield
    ca._reset_for_tests()


# ---------------------------------------------------------------------------
# Generic verify_route_request behaviour
# ---------------------------------------------------------------------------


def test_unauthenticated_request_yields_401(cap_db):
    req = _FakePendingReq()  # auth_user_id is empty
    claims, err = cr.verify_route_request(req, "vnc", "sess1", "any-tok")
    assert claims is None
    assert err and err["status"] == 401


def test_bearer_only_request_accepts_bound_capability(cap_db):
    tok = cr.mint_route_token("vnc", "sess1", "install-bootstrap")
    req = _FakePendingReq(remote_addr="10.0.0.1")
    claims, err = cr.verify_route_request(
        req, "vnc", "sess1", tok, allow_bearer_only=True)
    assert err is None
    assert claims and claims.user_id == "install-bootstrap"


def test_owner_request_succeeds(cap_db):
    tok = cr.mint_route_token("vnc", "sess1", "alice")
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.1")
    claims, err = cr.verify_route_request(req, "vnc", "sess1", tok)
    assert err is None
    assert claims and claims.user_id == "alice"


def test_other_user_token_yields_403(cap_db):
    tok_for_alice = cr.mint_route_token("vnc", "sess1", "alice")
    req = _FakePendingReq(auth_user_id="bob", remote_addr="10.0.0.2")
    claims, err = cr.verify_route_request(req, "vnc", "sess1", tok_for_alice)
    assert claims is None
    assert err and err["status"] == 403


def test_invalid_token_yields_403(cap_db):
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.3")
    claims, err = cr.verify_route_request(req, "vnc", "sess1", "bogus-token")
    assert claims is None
    assert err and err["status"] == 403


def test_missing_token_yields_401(cap_db):
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.4")
    claims, err = cr.verify_route_request(req, "vnc", "sess1", "")
    assert claims is None
    assert err and err["status"] == 401


def test_expired_token_yields_403(cap_db):
    tok = cr.mint_route_token(
        "vnc", "sess-old", "alice", ttl_seconds=1)
    time.sleep(1.1)
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.5")
    claims, err = cr.verify_route_request(req, "vnc", "sess-old", tok)
    assert claims is None
    assert err and err["status"] == 403


def test_revoked_token_yields_403(cap_db):
    tok = cr.mint_route_token("vnc", "sess-r", "alice")
    cr.revoke_route_tokens("sess-r")
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.6")
    claims, err = cr.verify_route_request(req, "vnc", "sess-r", tok)
    assert claims is None
    assert err and err["status"] == 403


# ---------------------------------------------------------------------------
# Cross-resource forge protection — the test the plan explicitly calls out
# ---------------------------------------------------------------------------


def test_vnc_token_rejected_on_terminal_route(cap_db):
    """Token minted for `vnc` MUST NOT verify against a `terminal` route
    even when user_id and resource_id are identical — the token's
    resource_type is part of the binding."""
    tok = cr.mint_route_token("vnc", "sess1", "alice")
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.7")
    claims, err = cr.verify_route_request(req, "terminal", "sess1", tok)
    assert claims is None
    assert err and err["status"] == 403


def test_token_rejected_on_other_resource_id(cap_db):
    tok = cr.mint_route_token("port_forward", "fwdA", "alice")
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.8")
    claims, err = cr.verify_route_request(
        req, "port_forward", "fwdB", tok)
    assert claims is None
    assert err and err["status"] == 403


# ---------------------------------------------------------------------------
# Rate-limiting on verify failures
# ---------------------------------------------------------------------------


def test_verify_rate_limit_returns_429(cap_db):
    ip = "203.0.113.99"
    req = _FakePendingReq(auth_user_id="alice", remote_addr=ip)
    for _ in range(ca._FAIL_RATE_LIMIT_THRESHOLD):
        cr.verify_route_request(req, "vnc", "sess1", "bad-tok")
    valid = cr.mint_route_token("vnc", "sess1", "alice")
    claims, err = cr.verify_route_request(req, "vnc", "sess1", valid)
    # 429 returned BEFORE we even get to look at the (now valid) token,
    # so claims is None and the status is 429.
    assert claims is None
    assert err and err["status"] == 429


# ---------------------------------------------------------------------------
# WS path returns a CLOSE frame (the 101 upgrade is already complete by
# the time the handler runs — HTTP status lines on the socket would be
# protocol-violation bytes).
# ---------------------------------------------------------------------------


def _close_code(frame: bytes) -> int:
    assert frame[0] == 0x88, f"not a WS close frame: {frame!r}"
    return (frame[2] << 8) | frame[3]


def test_ws_unauthenticated_returns_close_frame(cap_db):
    claims, err = cr.verify_route_ws({}, "vnc", "sess1", "x")
    assert claims is None
    assert _close_code(err) == 1008  # Policy Violation


def test_ws_other_user_returns_close_frame(cap_db):
    tok = cr.mint_route_token("vnc", "sess1", "alice")
    meta = {"auth_user_id": "bob", "remote_addr": "10.0.0.10"}
    claims, err = cr.verify_route_ws(meta, "vnc", "sess1", tok)
    assert claims is None
    assert _close_code(err) == 1008


def test_ws_owner_succeeds(cap_db):
    tok = cr.mint_route_token("terminal", "t1", "alice")
    meta = {"auth_user_id": "alice", "remote_addr": "10.0.0.11"}
    claims, err = cr.verify_route_ws(meta, "terminal", "t1", tok)
    assert err is None
    assert claims and claims.resource_type == "terminal"


def test_ws_bearer_capability_succeeds_without_session_auth(cap_db):
    tok = cr.mint_route_token("terminal", "t1", "alice")
    claims, err = cr.verify_route_ws(
        {"remote_addr": "203.0.113.10"}, "terminal", "t1", tok,
        allow_bearer_only=True)
    assert err is None
    assert claims and claims.user_id == "alice"


# ---------------------------------------------------------------------------
# Route handlers refuse to mint without owner_user_id
# ---------------------------------------------------------------------------


def test_register_session_requires_owner(cap_db):
    from services.vnc_proxy import register_session
    with pytest.raises(ValueError):
        register_session("sX", 5901)


def test_register_terminal_requires_owner(cap_db):
    from services.terminal_proxy import register_terminal
    with pytest.raises(ValueError):
        register_terminal("sX", "relay-A")


def test_register_code_server_requires_owner(cap_db):
    from services.code_server_proxy import register_code_server
    with pytest.raises(ValueError):
        register_code_server("relay-A", 8080, None)


def test_add_forward_requires_owner(cap_db):
    from services.port_forward_proxy import add_forward
    with pytest.raises(ValueError):
        add_forward("relay-A", 8080, None, ext_port=8080)


def test_register_audio_source_requires_owner(cap_db):
    from services.audio_proxy import register_audio_source
    with pytest.raises(ValueError):
        register_audio_source("sX", "127.0.0.1", 6180)


# ---------------------------------------------------------------------------
# End-to-end style smoke tests — register → verify (success/failure)
# ---------------------------------------------------------------------------


def test_vnc_register_then_verify_owner(cap_db):
    from services.vnc_proxy import register_session, get_session_token
    tok = register_session(
        "vnc-S", 5901,
        owner_user_id="alice",
        login_session_id="login-1")
    assert get_session_token("vnc-S") == tok
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.20")
    claims, err = cr.verify_route_request(req, "vnc", "vnc-S", tok)
    assert err is None
    assert claims and claims.user_id == "alice"


def test_terminal_register_then_unregister_revokes(cap_db):
    from services.terminal_proxy import register_terminal, unregister_terminal
    tok = register_terminal(
        "term-S", "relay-A", relay_service=None,
        owner_user_id="alice")
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.21")
    assert cr.verify_route_request(
        req, "terminal", "term-S", tok)[1] is None
    unregister_terminal("term-S")
    claims, err = cr.verify_route_request(req, "terminal", "term-S", tok)
    assert claims is None
    assert err and err["status"] == 403


def test_terminal_registers_server_pipe_command(cap_db):
    from services.terminal_proxy import get_terminal, register_terminal, unregister_terminal

    token = register_terminal(
        "term-server", "__server__", relay_service=None,
        owner_user_id="alice",
        server_pipe_command=["docker", "exec", "python3"])

    sess = get_terminal("term-server")
    assert token
    assert sess["relay_service_id"] == "__server__"
    assert sess["relay_service"] is None
    assert sess["server_pipe_command"] == ["docker", "exec", "python3"]

    unregister_terminal("term-server")


def test_port_forward_remove_requires_forward_id(cap_db):
    from services.port_forward_proxy import (
        add_forward, remove_forward, list_forwards,
    )
    first, fid, _tok = add_forward(
        "relay-A", 9000, None, ext_port=9000,
        owner_user_id="alice")
    assert first is True
    assert any(e["forward_id"] == fid for e in list_forwards())
    assert remove_forward("") is False
    last = remove_forward(fid)
    assert last is True
    assert list_forwards() == []


def test_logout_revokes_capabilities_for_session(cap_db):
    """Tokens minted with `login_session_id=L` are dropped when
    `revoke_session_capabilities(L)` is called — same path the real
    SecurityManager.logout uses."""
    tok = cr.mint_route_token(
        "vnc", "vnc-X", "alice", session_id="login-A")
    req = _FakePendingReq(auth_user_id="alice", remote_addr="10.0.0.30")
    assert cr.verify_route_request(req, "vnc", "vnc-X", tok)[1] is None
    ca.revoke_session_capabilities("login-A")
    claims, err = cr.verify_route_request(req, "vnc", "vnc-X", tok)
    assert claims is None
    assert err and err["status"] == 403
