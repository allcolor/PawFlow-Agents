"""Spend budgets: CRUD, period math, matching, enforcement, and
threshold notifications (core/budget_store.py).
"""

import json
import time

import pytest

from core import FlowFile
from core.budget_store import (
    BudgetStore, BudgetExceededError, current_spend, period_bounds,
    enforce_pre_turn, check_and_notify,
)
from core.usage_ledger import UsageLedger


class _FakeLedger:
    """Returns a fixed cost_usd regardless of filters — enough to drive
    the matching/enforcement/notification logic under test."""

    def __init__(self, cost_usd=0.0):
        self.cost_usd = cost_usd
        self.calls = []

    def summary(self, **kw):
        self.calls.append(kw)
        return {"cost_usd": self.cost_usd}


@pytest.fixture()
def store(tmp_path):
    BudgetStore.reset()
    inst = BudgetStore(path=str(tmp_path / "budgets.json"))
    BudgetStore._instance = inst
    yield inst
    BudgetStore.reset()


@pytest.fixture()
def isolated_ledger(tmp_path):
    """TestBudgetActions goes through the real UsageLedger singleton (via
    _handle_budget -> _budget_to_dict) — isolate it so leftover events from
    other test modules sharing the session-scoped data dir can't skew
    period-to-date spend assertions here."""
    UsageLedger.reset()
    inst = UsageLedger(path=str(tmp_path / "usage.db"))
    UsageLedger._instance = inst
    yield inst
    UsageLedger.reset()


class TestPeriodBounds:
    def test_daily_key_format(self):
        now = time.time()
        start, key = period_bounds("daily", now)
        assert key == time.strftime("%Y-%m-%d", time.localtime(now))
        assert start <= now

    def test_monthly_key_format(self):
        now = time.time()
        start, key = period_bounds("monthly", now)
        assert key == time.strftime("%Y-%m", time.localtime(now))
        assert start <= now


class TestCrud:
    def test_create_requires_scope_value_unless_global(self, store):
        with pytest.raises(ValueError, match="scope_value"):
            store.create(scope_type="user", scope_value="", period="daily",
                        limit_usd=10, policy="warn", created_by="admin")
        b = store.create(scope_type="global", scope_value="ignored",
                         period="daily", limit_usd=10, policy="warn",
                         created_by="admin")
        assert b.scope_value == ""  # cleared for global

    def test_create_rejects_bad_enums_and_limit(self, store):
        with pytest.raises(ValueError, match="scope_type"):
            store.create(scope_type="bogus", scope_value="x", period="daily",
                        limit_usd=10, policy="warn", created_by="a")
        with pytest.raises(ValueError, match="period"):
            store.create(scope_type="user", scope_value="x", period="weekly",
                        limit_usd=10, policy="warn", created_by="a")
        with pytest.raises(ValueError, match="policy"):
            store.create(scope_type="user", scope_value="x", period="daily",
                        limit_usd=10, policy="deny", created_by="a")
        with pytest.raises(ValueError, match="limit_usd"):
            store.create(scope_type="user", scope_value="x", period="daily",
                        limit_usd=0, policy="warn", created_by="a")

    def test_persists_across_reopen(self, tmp_path):
        path = str(tmp_path / "b.json")
        BudgetStore.reset()
        s1 = BudgetStore(path=path)
        b = s1.create(scope_type="user", scope_value="alice", period="daily",
                     limit_usd=5, policy="warn", created_by="admin")
        BudgetStore.reset()
        s2 = BudgetStore(path=path)
        got = s2.get(b.id)
        assert got is not None and got.scope_value == "alice"

    def test_update_resets_notify_dedup_state(self, store):
        b = store.create(scope_type="user", scope_value="alice",
                         period="daily", limit_usd=5, policy="warn",
                         created_by="admin")
        b.last_notified_pct = 80
        b.last_notified_period_key = "2026-07-20"
        store._save_one(b)
        updated = store.update(b.id, limit_usd=10)
        assert updated.last_notified_pct == 0
        assert updated.last_notified_period_key == ""

    def test_update_unknown_id_raises(self, store):
        with pytest.raises(KeyError):
            store.update("nosuch", limit_usd=5)

    def test_delete_returns_false_when_missing(self, store):
        assert store.delete("nosuch") is False

    def test_list_filters_by_scope(self, store):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=5, policy="warn", created_by="admin")
        store.create(scope_type="agent", scope_value="scout", period="daily",
                    limit_usd=5, policy="warn", created_by="admin")
        assert len(store.list(scope_type="user")) == 1
        assert len(store.list(scope_type="agent", scope_value="scout")) == 1
        assert len(store.list()) == 2


class TestMatching:
    def _mk(self, store, **over):
        cfg = dict(scope_type="global", scope_value="", period="daily",
                  limit_usd=5, policy="block", created_by="admin")
        cfg.update(over)
        return store.create(**cfg)

    def test_global_always_matches(self, store):
        self._mk(store, scope_type="global")
        ledger = _FakeLedger(cost_usd=10)
        with pytest.raises(BudgetExceededError):
            enforce_pre_turn(ledger, user_id="anyone")

    def test_user_scope_matches_exact_user_only(self, store):
        self._mk(store, scope_type="user", scope_value="alice")
        ledger = _FakeLedger(cost_usd=10)
        with pytest.raises(BudgetExceededError):
            enforce_pre_turn(ledger, user_id="alice")
        enforce_pre_turn(ledger, user_id="bob")  # no match, no raise

    def test_conversation_scope_matches_task_subconvs(self, store):
        self._mk(store, scope_type="conversation", scope_value="c1")
        ledger = _FakeLedger(cost_usd=10)
        with pytest.raises(BudgetExceededError):
            enforce_pre_turn(ledger, conversation_id="c1::task::t1")
        enforce_pre_turn(ledger, conversation_id="c1b")  # not a match

    def test_agent_and_service_scope(self, store):
        self._mk(store, scope_type="agent", scope_value="scout")
        ledger = _FakeLedger(cost_usd=10)
        with pytest.raises(BudgetExceededError):
            enforce_pre_turn(ledger, agent_name="scout")
        enforce_pre_turn(ledger, agent_name="other")


class TestEnforcePreTurn:
    def test_warn_policy_never_raises(self, store):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=1, policy="warn", created_by="admin")
        enforce_pre_turn(_FakeLedger(cost_usd=100), user_id="alice")

    def test_under_limit_does_not_raise(self, store):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=10, policy="block", created_by="admin")
        enforce_pre_turn(_FakeLedger(cost_usd=5), user_id="alice")

    def test_message_matches_fatal_error_string_convention(self, store):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=1, policy="block", created_by="admin")
        with pytest.raises(BudgetExceededError) as exc:
            enforce_pre_turn(_FakeLedger(cost_usd=2), user_id="alice")
        assert str(exc.value).startswith("Budget exceeded:")
        assert isinstance(exc.value, RuntimeError)


class TestCheckAndNotify:
    def test_dedup_within_same_period(self, store, monkeypatch):
        b = store.create(scope_type="user", scope_value="alice",
                         period="daily", limit_usd=10, policy="warn",
                         created_by="admin")
        sent = []
        monkeypatch.setattr("core.budget_store._notify",
                            lambda *a, **k: sent.append(a))
        check_and_notify(_FakeLedger(cost_usd=6), user_id="alice",
                         conversation_id="c1")  # 60% -> fires 50
        assert len(sent) == 1
        check_and_notify(_FakeLedger(cost_usd=6), user_id="alice",
                         conversation_id="c1")  # still 60% -> no re-fire
        assert len(sent) == 1
        got = store.get(b.id)
        assert got.last_notified_pct == 50

    def test_escalating_thresholds_each_fire_once(self, store, monkeypatch):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=10, policy="warn", created_by="admin")
        fired = []
        monkeypatch.setattr(
            "core.budget_store._notify",
            lambda b, spend, pct, cid, agent: fired.append(pct))
        check_and_notify(_FakeLedger(cost_usd=6), user_id="alice")
        check_and_notify(_FakeLedger(cost_usd=9), user_id="alice")
        check_and_notify(_FakeLedger(cost_usd=12), user_id="alice")
        assert fired == [50, 80, 100]

    def test_never_raises_on_internal_error(self, store, monkeypatch):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=10, policy="warn", created_by="admin")

        class _Boom:
            def summary(self, **kw):
                raise RuntimeError("boom")
        check_and_notify(_Boom(), user_id="alice")  # must not raise

    def test_no_conversation_id_logs_only(self, store, monkeypatch):
        store.create(scope_type="user", scope_value="alice", period="daily",
                    limit_usd=10, policy="warn", created_by="admin")
        writer_calls = []
        import core.budget_store as bs

        class _FakeWriter:
            def enqueue_message(self, *a, **k):
                writer_calls.append((a, k))

        class _FakeCW:
            @staticmethod
            def for_conversation(cid):
                return _FakeWriter()
        monkeypatch.setitem(
            __import__("sys").modules, "core.conversation_writer",
            type("m", (), {"ConversationWriter": _FakeCW}))
        check_and_notify(_FakeLedger(cost_usd=6), user_id="alice",
                         conversation_id="")
        assert writer_calls == []


class TestCurrentSpend:
    def test_uses_period_start_and_scope_filter(self, store):
        b = store.create(scope_type="llm_service", scope_value="svc-a",
                         period="monthly", limit_usd=5, policy="warn",
                         created_by="admin")
        ledger = _FakeLedger(cost_usd=3.5)
        assert current_spend(ledger, b) == 3.5
        call = ledger.calls[-1]
        assert call["llm_service"] == "svc-a"
        assert "since" in call


def _ff(roles=""):
    ff = FlowFile()
    if roles:
        ff.set_attribute("http.auth.roles", roles)
    return ff


class TestBudgetActions:
    def _run(self, action, body, user_id="alice", roles=""):
        from tasks.ai.actions.usage import _handle_usage
        ff = _ff(roles)
        out = _handle_usage(None, action, body, None, user_id, ff)
        assert out == [ff]
        return ff

    def test_non_admin_cannot_create(self, store, isolated_ledger):
        ff = self._run("budget_create", {
            "scope_type": "global", "period": "daily", "limit_usd": 5,
            "policy": "warn"})
        assert ff.get_attribute("http.response.status") == "403"

    def test_admin_create_update_delete_roundtrip(self, store, isolated_ledger):
        ff = self._run("budget_create", {
            "scope_type": "user", "scope_value": "alice", "period": "daily",
            "limit_usd": 5, "policy": "block"}, roles="admin")
        data = json.loads(ff.get_content())
        assert data["scope_value"] == "alice" and data["pct"] == 0.0
        bid = data["id"]

        ff = self._run("budget_update", {"budget_id": bid, "limit_usd": 20},
                       roles="admin")
        assert json.loads(ff.get_content())["limit_usd"] == 20

        ff = self._run("budget_delete", {"budget_id": bid}, roles="admin")
        assert json.loads(ff.get_content())["deleted"] is True

    def test_create_bad_input_is_400(self, store, isolated_ledger):
        ff = self._run("budget_create", {
            "scope_type": "bogus", "period": "daily", "limit_usd": 5,
            "policy": "warn"}, roles="admin")
        assert ff.get_attribute("http.response.status") == "400"

    def test_list_scopes_non_admin_to_own_and_global(self, store, isolated_ledger):
        self._run("budget_create", {
            "scope_type": "global", "period": "daily", "limit_usd": 5,
            "policy": "warn"}, roles="admin")
        self._run("budget_create", {
            "scope_type": "user", "scope_value": "alice", "period": "daily",
            "limit_usd": 5, "policy": "warn"}, roles="admin")
        self._run("budget_create", {
            "scope_type": "user", "scope_value": "bob", "period": "daily",
            "limit_usd": 5, "policy": "warn"}, roles="admin")
        self._run("budget_create", {
            "scope_type": "agent", "scope_value": "scout", "period": "daily",
            "limit_usd": 5, "policy": "warn"}, roles="admin")

        ff = self._run("budget_list", {}, user_id="alice")
        scopes = {(b["scope_type"], b["scope_value"])
                 for b in json.loads(ff.get_content())["budgets"]}
        assert scopes == {("global", ""), ("user", "alice")}

        ff = self._run("budget_list", {}, user_id="admin", roles="admin")
        assert len(json.loads(ff.get_content())["budgets"]) == 4
