"""Phase 1 unit tests for `core.capability_auth`.

Covers issue/verify/revoke roundtrips, persistence across re-init
(simulates a server restart), strict resource/owner/conv binding,
and the per-IP failure rate-limit.
"""

import time

import pytest

from core import capability_auth as ca


@pytest.fixture()
def db(tmp_path):
    """Fresh capability DB in tmp_path; cleaned up between tests."""
    ca._reset_for_tests()
    db_path = tmp_path / "capabilities.db"
    ca.init_db(db_path)
    yield db_path
    ca._reset_for_tests()


def test_init_db_materializes_json_store(tmp_path):
    ca._reset_for_tests()
    db_path = tmp_path / "capabilities.db"
    ca.init_db(db_path)

    assert not db_path.exists()
    json_path = tmp_path / "capabilities.json"
    assert json_path.exists()
    assert json_path.read_text(encoding="utf-8") == "[]"
    ca._reset_for_tests()


def test_issue_returns_token_and_verify_roundtrip(db):
    token = ca.issue_capability(
        "vnc", "sess1", "alice",
        conversation_id="convA", session_id="login1")
    assert isinstance(token, str) and len(token) > 20

    claims = ca.verify_capability(
        token, "vnc", "sess1",
        user_id="alice", conversation_id="convA")
    assert claims.resource_type == "vnc"
    assert claims.resource_id == "sess1"
    assert claims.user_id == "alice"
    assert claims.conversation_id == "convA"
    assert claims.session_id == "login1"


def test_token_unknown_raises_not_found(db):
    with pytest.raises(ca.CapabilityNotFound):
        ca.verify_capability("never-issued", "vnc", "sess1",
                             user_id="alice")


def test_empty_token_raises_not_found(db):
    with pytest.raises(ca.CapabilityNotFound):
        ca.verify_capability("", "vnc", "sess1", user_id="alice")


def test_expired_token_rejected_and_purged(db):
    token = ca.issue_capability(
        "vnc", "sess1", "alice", ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(ca.CapabilityExpired):
        ca.verify_capability(token, "vnc", "sess1", user_id="alice")
    # Opportunistic purge: second verify -> NotFound, not Expired.
    with pytest.raises(ca.CapabilityNotFound):
        ca.verify_capability(token, "vnc", "sess1", user_id="alice")


def test_wrong_resource_type_rejected(db):
    token = ca.issue_capability("vnc", "sess1", "alice")
    with pytest.raises(ca.CapabilityWrongResource):
        ca.verify_capability(token, "terminal", "sess1", user_id="alice")


def test_wrong_resource_id_rejected(db):
    token = ca.issue_capability("vnc", "sess1", "alice")
    with pytest.raises(ca.CapabilityWrongResource):
        ca.verify_capability(token, "vnc", "sess999", user_id="alice")


def test_wrong_user_rejected(db):
    token = ca.issue_capability("vnc", "sess1", "alice")
    with pytest.raises(ca.CapabilityWrongOwner):
        ca.verify_capability(token, "vnc", "sess1", user_id="bob")


def test_wrong_conversation_rejected(db):
    token = ca.issue_capability(
        "vnc", "sess1", "alice", conversation_id="convA")
    with pytest.raises(ca.CapabilityWrongOwner):
        ca.verify_capability(
            token, "vnc", "sess1",
            user_id="alice", conversation_id="convB")


def test_token_with_empty_conv_accepts_any_conv(db):
    """Tokens minted without conv scope ("") accept any requester conv —
    used for global resources like the system terminal."""
    token = ca.issue_capability("terminal", "sysT", "alice")
    claims = ca.verify_capability(
        token, "terminal", "sysT",
        user_id="alice", conversation_id="any-conv")
    assert claims.resource_id == "sysT"


def test_revoke_by_token(db):
    token = ca.issue_capability("vnc", "sess1", "alice")
    assert ca.revoke_capability(token=token) == 1
    with pytest.raises(ca.CapabilityNotFound):
        ca.verify_capability(token, "vnc", "sess1", user_id="alice")


def test_revoke_by_resource_id_drops_all(db):
    t1 = ca.issue_capability("vnc", "sess1", "alice")
    t2 = ca.issue_capability("vnc", "sess1", "alice")  # second token same res
    assert ca.revoke_capability(resource_id="sess1") == 2
    for t in (t1, t2):
        with pytest.raises(ca.CapabilityNotFound):
            ca.verify_capability(t, "vnc", "sess1", user_id="alice")


def test_revoke_by_session_drops_all(db):
    t1 = ca.issue_capability(
        "vnc", "sess1", "alice", session_id="login1")
    t2 = ca.issue_capability(
        "terminal", "termA", "alice", session_id="login1")
    t3 = ca.issue_capability(
        "vnc", "sess2", "alice", session_id="login2")
    assert ca.revoke_session_capabilities("login1") == 2
    # login1's tokens are gone
    for t, rt, rid in [(t1, "vnc", "sess1"), (t2, "terminal", "termA")]:
        with pytest.raises(ca.CapabilityNotFound):
            ca.verify_capability(t, rt, rid, user_id="alice")
    # login2's token still works
    claims = ca.verify_capability(t3, "vnc", "sess2", user_id="alice")
    assert claims.token == t3


def test_revoke_session_with_empty_id_is_noop(db):
    ca.issue_capability("vnc", "sess1", "alice", session_id="")
    assert ca.revoke_session_capabilities("") == 0


def test_revoke_requires_exactly_one_argument(db):
    with pytest.raises(ValueError):
        ca.revoke_capability()
    with pytest.raises(ValueError):
        ca.revoke_capability(token="x", resource_id="y")


def test_persistence_survives_reinit(tmp_path):
    """Server-restart simulation: issue, _reset_for_tests (drops in-mem),
    init_db on the same file, verify still works."""
    ca._reset_for_tests()
    db_path = tmp_path / "capabilities.db"
    ca.init_db(db_path)
    token = ca.issue_capability(
        "vnc", "sess1", "alice",
        conversation_id="convA", session_id="login1")

    # Simulate server restart
    ca._reset_for_tests()
    ca.init_db(db_path)

    claims = ca.verify_capability(
        token, "vnc", "sess1",
        user_id="alice", conversation_id="convA")
    assert claims.session_id == "login1"
    ca._reset_for_tests()


def test_init_purges_expired_at_boot(tmp_path):
    ca._reset_for_tests()
    db_path = tmp_path / "capabilities.db"
    ca.init_db(db_path)
    expired = ca.issue_capability("vnc", "sess-old", "alice", ttl_seconds=1)
    fresh = ca.issue_capability("vnc", "sess-new", "alice", ttl_seconds=3600)
    time.sleep(1.1)

    ca._reset_for_tests()
    ca.init_db(db_path)  # should purge `expired`

    with pytest.raises(ca.CapabilityNotFound):
        ca.verify_capability(expired, "vnc", "sess-old", user_id="alice")
    claims = ca.verify_capability(fresh, "vnc", "sess-new", user_id="alice")
    assert claims.resource_id == "sess-new"
    ca._reset_for_tests()


def test_rate_limit_kicks_in_after_threshold(db):
    ip = "10.13.13.99"
    # Trigger threshold failures with bad tokens.
    for _ in range(ca._FAIL_RATE_LIMIT_THRESHOLD):
        with pytest.raises(ca.CapabilityNotFound):
            ca.verify_capability("bad-token", "vnc", "sess1",
                                 user_id="alice", remote_ip=ip)
    # Next attempt — even with a valid token — must be rate-limited.
    valid = ca.issue_capability("vnc", "sess1", "alice")
    with pytest.raises(ca.CapabilityRateLimited):
        ca.verify_capability(valid, "vnc", "sess1",
                             user_id="alice", remote_ip=ip)
    # Different IP not affected.
    claims = ca.verify_capability(
        valid, "vnc", "sess1",
        user_id="alice", remote_ip="10.13.13.42")
    assert claims.token == valid


def test_rate_limit_disabled_when_no_remote_ip(db):
    """Calls without remote_ip (e.g. internal use) bypass rate limiting."""
    for _ in range(ca._FAIL_RATE_LIMIT_THRESHOLD * 2):
        with pytest.raises(ca.CapabilityNotFound):
            ca.verify_capability("bad-token", "vnc", "sess1",
                                 user_id="alice", remote_ip="")


def test_issue_validates_required_fields(db):
    with pytest.raises(ValueError):
        ca.issue_capability("", "sess1", "alice")
    with pytest.raises(ValueError):
        ca.issue_capability("vnc", "", "alice")
    with pytest.raises(ValueError):
        ca.issue_capability("vnc", "sess1", "")
    with pytest.raises(ValueError):
        ca.issue_capability("vnc", "sess1", "alice", ttl_seconds=0)


def test_is_owner_or_admin():
    assert ca.is_owner_or_admin("alice", "alice") is True
    assert ca.is_owner_or_admin("bob", "alice") is False
    assert ca.is_owner_or_admin("bob", "alice", auth_role="admin") is True
    assert ca.is_owner_or_admin("", "alice") is False
    assert ca.is_owner_or_admin("", "alice", auth_role="admin") is False


def test_uninitialised_use_raises(tmp_path):
    """Calling issue/verify before init_db must fail loudly."""
    ca._reset_for_tests()
    with pytest.raises(RuntimeError):
        ca.issue_capability("vnc", "sess1", "alice")
