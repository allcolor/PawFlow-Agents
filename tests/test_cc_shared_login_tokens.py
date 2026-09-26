"""Containers sharing one Claude login must never keep a dead refresh_token.

Every interactive container launched on a pool slot gets its own copy of the
slot's token in ``<workdir>/.credentials.json``. Anthropic's refresh_token is
single-use: when one CLI renews, every other container on the login holds a
dead one and fails with authentication_failed at its next renewal
(2026-09-26, two claude agents in two conversations on one login).

The fix goes both ways. A workdir token only reaches the pool when it is
NEWER than the pool's (otherwise a stale file put a dead token back), and the
pool's newest token is written into every container's file before a submit.
Claude Code re-reads the file when its mtime changes, before renewing.
"""

import copy
import json
import os
import stat
import time

import pytest

from core.llm_providers import _cc_credentials as cc
from core.llm_providers import claude_code_session as ccs
from core.llm_providers import cli_shared


def _ms(seconds_from_now):
    return int((time.time() + seconds_from_now) * 1000)


@pytest.fixture
def pool_store(monkeypatch):
    """An in-memory credential pool behind the facade's load/save."""
    store = {"pool": [], "saves": 0}

    def load(service_id="", user_id="", conv_id=""):
        return copy.deepcopy(store["pool"])

    def save(pool, service_id="", user_id="", conv_id=""):
        store["pool"] = copy.deepcopy(pool)
        store["saves"] += 1

    monkeypatch.setattr(ccs, "_find_cc_service_id", lambda *a, **k: "svc")
    monkeypatch.setattr(ccs, "_load_credentials_pool", load)
    monkeypatch.setattr(ccs, "_save_credentials_pool", save)
    monkeypatch.setattr(cli_shared, "_RECOVERED_SIGNATURES", {})
    return store


def _slot(access, refresh, expires):
    return {"access_token": access, "refresh_token": refresh,
            "expires_at": expires, "account": "one"}


def _workdir(tmp_path, name, access, refresh, expires, **extra):
    d = tmp_path / name
    d.mkdir()
    oauth = {"accessToken": access, "refreshToken": refresh,
             "expiresAt": expires, "scopes": ["user:inference"]}
    oauth.update(extra)
    (d / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")
    return str(d)


def _file(workdir):
    with open(os.path.join(workdir, ".credentials.json"), encoding="utf-8") as f:
        return json.load(f)["claudeAiOauth"]


# ── recover: a stale workdir never overwrites the pool ──────────────────────

def test_a_stale_workdir_does_not_put_a_dead_token_back(pool_store, tmp_path):
    """B was launched with r0; A has since rotated the slot to r1.

    Before the fix, B's first recovery wrote r0 over r1: the pool then held a
    refresh_token Anthropic had already invalidated.
    """
    pool_store["pool"] = [_slot("a1", "r1", _ms(8 * 3600))]
    b = _workdir(tmp_path, "b", "a0", "r0", _ms(3600))

    assert cc.recover_tokens_from_workdir(b, "svc", 0) is False
    assert pool_store["pool"][0]["refresh_token"] == "r1"
    assert pool_store["saves"] == 0


def test_a_newer_workdir_token_still_reaches_the_pool(pool_store, tmp_path):
    pool_store["pool"] = [_slot("a0", "r0", _ms(3600))]
    a = _workdir(tmp_path, "a", "a1", "r1", _ms(8 * 3600))

    assert cc.recover_tokens_from_workdir(a, "svc", 0) is True
    assert pool_store["pool"][0]["refresh_token"] == "r1"


# ── push: the pool's newest token reaches every container's file ────────────

def test_push_writes_the_pool_token_and_keeps_the_rest_of_the_file(
        pool_store, tmp_path):
    new_exp = _ms(8 * 3600)
    pool_store["pool"] = [_slot("a1", "r1", new_exp)]
    b = _workdir(tmp_path, "b", "a0", "r0", _ms(3600),
                 subscriptionType="max")
    path = os.path.join(b, ".credentials.json")
    os.chmod(path, 0o600)
    before = os.stat(path).st_mtime_ns

    assert cc.push_pool_tokens_to_workdir(b, "svc", 0) is True

    oauth = _file(b)
    assert (oauth["accessToken"], oauth["refreshToken"], oauth["expiresAt"]) \
        == ("a1", "r1", new_exp)
    assert oauth["subscriptionType"] == "max"
    assert oauth["scopes"] == ["user:inference"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # The CLI notices a change by the file's mtime.
    assert os.stat(path).st_mtime_ns != before
    assert [n for n in os.listdir(b) if n.endswith(".tmp")] == []


def test_a_pushed_token_is_not_copied_back_as_a_rotation(pool_store, tmp_path):
    pool_store["pool"] = [_slot("a1", "r1", _ms(8 * 3600))]
    b = _workdir(tmp_path, "b", "a0", "r0", _ms(3600))
    assert cc.push_pool_tokens_to_workdir(b, "svc", 0) is True
    saves = pool_store["saves"]

    assert cc.recover_tokens_from_workdir(b, "svc", 0) is False
    assert pool_store["saves"] == saves


@pytest.mark.parametrize("case", ["file_newer", "same", "pool_expired",
                                  "no_file", "no_slot"])
def test_push_leaves_the_file_alone_when_it_has_nothing_better(
        pool_store, tmp_path, case):
    exp = _ms(3600)
    pool_store["pool"] = [_slot("a1", "r1", exp)]
    index = 0
    if case == "file_newer":
        b = _workdir(tmp_path, "b", "a2", "r2", _ms(8 * 3600))
    elif case == "same":
        b = _workdir(tmp_path, "b", "a1", "r1", exp)
    elif case == "pool_expired":
        pool_store["pool"] = [_slot("a1", "r1", _ms(-60))]
        b = _workdir(tmp_path, "b", "a0", "r0", _ms(-3600))
    elif case == "no_file":
        b = str(tmp_path)
    else:
        b = _workdir(tmp_path, "b", "a0", "r0", _ms(60))
        index = 3
    before = _file(b) if case != "no_file" else None

    assert cc.push_pool_tokens_to_workdir(b, "svc", index) is False
    if before is not None:
        assert _file(b) == before


# ── central renewal ─────────────────────────────────────────────────────────

@pytest.fixture
def refresh_allowed(monkeypatch):
    import services.llm_credential_oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "credential_pool_allows_refresh",
                        lambda *a, **k: True)


def test_a_slot_close_to_expiry_is_renewed_centrally(
        pool_store, refresh_allowed, monkeypatch):
    pool_store["pool"] = [_slot("a0", "r0", _ms(600))]
    posted = []
    new_exp = _ms(8 * 3600)

    def refresh(token):
        posted.append(token)
        return {"access_token": "a1", "refresh_token": "r1",
                "expires_at": new_exp}

    monkeypatch.setattr(ccs.ClaudeCodeSessionMixin, "_refresh_oauth_token",
                        staticmethod(refresh))

    assert cc.refresh_pool_slot_if_expiring("svc", 0) is True
    assert posted == ["r0"]
    assert pool_store["pool"][0]["refresh_token"] == "r1"


def test_a_slot_with_time_left_is_not_renewed(
        pool_store, refresh_allowed, monkeypatch):
    pool_store["pool"] = [_slot("a0", "r0", _ms(3 * 3600))]
    monkeypatch.setattr(ccs.ClaudeCodeSessionMixin, "_refresh_oauth_token",
                        staticmethod(lambda t: pytest.fail("renewed early")))

    assert cc.refresh_pool_slot_if_expiring("svc", 0) is False


def test_no_central_renewal_when_the_pool_forbids_it(pool_store, monkeypatch):
    import services.llm_credential_oauth as oauth_mod
    monkeypatch.setattr(oauth_mod, "credential_pool_allows_refresh",
                        lambda *a, **k: False)
    pool_store["pool"] = [_slot("a0", "r0", _ms(60))]
    monkeypatch.setattr(ccs.ClaudeCodeSessionMixin, "_refresh_oauth_token",
                        staticmethod(lambda t: pytest.fail("renewed")))

    assert cc.refresh_pool_slot_if_expiring("svc", 0) is False


def test_a_rejected_central_renewal_keeps_the_slot(
        pool_store, refresh_allowed, monkeypatch):
    pool_store["pool"] = [_slot("a0", "r0", _ms(600))]

    def reject(token):
        raise ccs.OAuthRejectedError("invalid_grant")

    monkeypatch.setattr(ccs.ClaudeCodeSessionMixin, "_refresh_oauth_token",
                        staticmethod(reject))

    assert cc.refresh_pool_slot_if_expiring("svc", 0) is False
    assert [c["refresh_token"] for c in pool_store["pool"]] == ["r0"]


# ── the pool: sync before submit ────────────────────────────────────────────

def _state(name, workdir, idx=0, service="svc"):
    from core.claude_code_interactive_pool import InteractiveContainer
    return InteractiveContainer(
        key=("u", f"c-{name}", name, service), name=name, workdir=workdir,
        container_workdir="/c", session_token=f"s-{name}",
        event_service_id="e", internal_token="i", service_id=service,
        svc_pool_idx=idx, user_id="u", conv_id=f"c-{name}")


def _pool(*states):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool
    pool = InteractiveClaudeCodePool()
    for s in states:
        pool._sessions[s.key] = s
    return pool


def test_b_gets_the_token_a_rotated_before_its_submit(
        pool_store, refresh_allowed, tmp_path):
    """The reported bug, end to end on the files.

    A and B share slot 0. A's CLI renews on its own (r0 -> r1). Before B's
    next submit, the pool learns r1 from A's file and B's file is rewritten
    with it -- B's CLI would otherwise renew with the dead r0.
    """
    exp0, exp1 = _ms(3 * 3600), _ms(8 * 3600)
    pool_store["pool"] = [_slot("a0", "r0", exp0)]
    a = _state("a", _workdir(tmp_path, "a", "a1", "r1", exp1))
    b = _state("b", _workdir(tmp_path, "b", "a0", "r0", exp0))
    pool = _pool(a, b)

    pool._sync_slot_credentials(b)

    assert pool_store["pool"][0]["refresh_token"] == "r1"
    assert _file(b.workdir)["refreshToken"] == "r1"
    assert _file(a.workdir)["refreshToken"] == "r1"


def test_the_sync_is_confined_to_the_same_login(
        pool_store, refresh_allowed, tmp_path):
    pool_store["pool"] = [_slot("a1", "r1", _ms(8 * 3600)),
                          _slot("x0", "x0", _ms(3 * 3600))]
    b = _state("b", _workdir(tmp_path, "b", "a0", "r0", _ms(3 * 3600)))
    other = _state("o", _workdir(tmp_path, "o", "x0", "x0", _ms(3600)), idx=1)
    pool = _pool(b, other)

    pool._sync_slot_credentials(b)

    assert _file(b.workdir)["refreshToken"] == "r1"
    assert _file(other.workdir)["refreshToken"] == "x0"


def test_a_central_renewal_reaches_every_container_on_the_login(
        pool_store, refresh_allowed, tmp_path, monkeypatch):
    exp0, exp1 = _ms(600), _ms(8 * 3600)
    pool_store["pool"] = [_slot("a0", "r0", exp0)]
    monkeypatch.setattr(
        ccs.ClaudeCodeSessionMixin, "_refresh_oauth_token",
        staticmethod(lambda t: {"access_token": "a1", "refresh_token": "r1",
                                "expires_at": exp1}))
    a = _state("a", _workdir(tmp_path, "a", "a0", "r0", exp0))
    b = _state("b", _workdir(tmp_path, "b", "a0", "r0", exp0))
    pool = _pool(a, b)

    pool._sync_slot_credentials(a)

    assert _file(a.workdir)["refreshToken"] == "r1"
    assert _file(b.workdir)["refreshToken"] == "r1"


class _Stop(Exception):
    pass


@pytest.mark.parametrize("send", ["send_text", "send_queued",
                                  "send_interrupt"])
def test_every_submit_path_syncs_before_pasting(monkeypatch, send):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool
    state = _state("b", "/w")
    state.prompt_ready = True
    pool = _pool(state)
    monkeypatch.setattr(InteractiveClaudeCodePool, "_is_alive",
                        lambda self, name: True)
    monkeypatch.setattr(pool, "_refuse_dead_stream", lambda s: False)
    pasted = []
    monkeypatch.setattr(pool, "_paste_text",
                        lambda s, t: pasted.append(t) or False)

    def sync(s):
        assert s is state
        raise _Stop()

    monkeypatch.setattr(pool, "_sync_slot_credentials", sync)

    with pytest.raises(_Stop):
        getattr(pool, send)(state, "hello")
    assert pasted == [], "the paste ran before the credentials sync"


def test_the_sweeper_pushes_to_the_sessions_it_keeps(monkeypatch):
    from core.claude_code_interactive_pool import InteractiveClaudeCodePool
    state = _state("alive", "/w", idx=2)
    state.last_used = time.time()
    pool = _pool(state)
    monkeypatch.setattr(InteractiveClaudeCodePool, "_is_alive",
                        lambda self, name: True)
    monkeypatch.setattr(pool, "_recover_container_tokens", lambda s: None)
    pushed = []
    monkeypatch.setattr(cc, "push_pool_tokens_to_workdir",
                        lambda *a, **k: pushed.append(a) or False)

    assert pool.sweep_idle() == 0
    assert [a[2] for a in pushed] == [2]


def test_codex_containers_are_left_alone(monkeypatch):
    """OpenAI keeps the old refresh_token valid; there is nothing to sync."""
    from core.codex_interactive_pool import CodexInteractivePool
    pool = CodexInteractivePool()
    state = _state("c", "/w")
    monkeypatch.setattr(cc, "push_pool_tokens_to_workdir",
                        lambda *a, **k: pytest.fail("pushed a codex token"))
    monkeypatch.setattr(cc, "refresh_pool_slot_if_expiring",
                        lambda *a, **k: pytest.fail("renewed a codex slot"))

    pool._sync_slot_credentials(state)
    pool._push_slot_tokens(state)


def test_the_shared_login_sync_is_documented():
    from pathlib import Path
    doc = Path("docs/CLAUDE_CODE_INTERACTIVE.md").read_text(encoding="utf-8")
    assert "push_pool_tokens_to_workdir" in doc
    assert "refresh_pool_slot_if_expiring" in doc
