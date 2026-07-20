"""UsageLedger: event recording, frozen cost, queries, legacy migration,
usage query actions, and the realtime usage hook.

Replaces the former TokenTracker/CostTracker tests — the ledger is the
single persistent source of truth for tokens and cost.
"""

import json
import time

import pytest

from core import FlowFile
from core.usage_ledger import UsageLedger, compute_cost


@pytest.fixture()
def ledger(tmp_path):
    UsageLedger.reset()
    inst = UsageLedger(path=str(tmp_path / "usage.db"))
    UsageLedger._instance = inst
    yield inst
    UsageLedger.reset()


class TestRecord:
    def test_requires_user_and_channel(self, ledger):
        with pytest.raises(ValueError, match="user_id"):
            ledger.record(user_id="", channel="chat", tokens_in=1)
        with pytest.raises(ValueError, match="channel"):
            ledger.record(user_id="alice", channel="", tokens_in=1)

    def test_cost_frozen_at_write(self, ledger):
        cost = ledger.record(
            user_id="alice", channel="chat", conversation_id="c1",
            model="gpt", tokens_in=1_000_000, tokens_out=500_000,
            cost_per_1m_input=3.0, cost_per_1m_output=15.0)
        assert cost == pytest.approx(3.0 + 7.5)
        # Later "price change" does not rewrite history: stored cost stands.
        assert ledger.conversation_cost("c1")["total"] == pytest.approx(10.5)

    def test_no_pricing_means_zero_cost_but_tokens_kept(self, ledger):
        cost = ledger.record(user_id="alice", channel="chat",
                             conversation_id="c1", model="m",
                             tokens_in=100, tokens_out=50)
        assert cost == 0.0
        s = ledger.summary(user_id="alice")
        assert s["tokens_in"] == 100 and s["tokens_out"] == 50

    def test_cache_rate_defaults(self):
        # read = 10% of input, write = 125% of input (Anthropic ratios)
        cost = compute_cost(0, 0, 1_000_000, 1_000_000, 10.0, 0.0)
        assert cost == pytest.approx(1.0 + 12.5)
        # explicit rates win
        cost = compute_cost(0, 0, 1_000_000, 0, 10.0, 0.0,
                            cost_per_1m_cache_read=2.0)
        assert cost == pytest.approx(2.0)

    def test_persistent_across_reopen(self, tmp_path):
        path = str(tmp_path / "u.db")
        UsageLedger.reset()
        first = UsageLedger(path=path)
        first.record(user_id="alice", channel="chat", conversation_id="c1",
                     model="m", tokens_in=10, tokens_out=5,
                     cost_per_1m_input=1.0, cost_per_1m_output=1.0)
        UsageLedger.reset()
        second = UsageLedger(path=path)
        assert second.summary(user_id="alice")["tokens_in"] == 10
        assert second.conversation_cost("c1")["total"] > 0
        UsageLedger.reset()


class TestQueries:
    def _seed(self, ledger):
        now = time.time()
        ledger.record(user_id="alice", channel="chat", conversation_id="c1",
                      agent_name="claude", llm_service="svc-a", model="m1",
                      tokens_in=100, tokens_out=10, cost_per_1m_input=10.0,
                      cost_per_1m_output=10.0, ts=now)
        ledger.record(user_id="alice", channel="subagent",
                      conversation_id="c1", agent_name="scout",
                      llm_service="svc-b", model="m2",
                      tokens_in=200, tokens_out=20, ts=now)
        ledger.record(user_id="bob", channel="chat", conversation_id="c2",
                      agent_name="claude", llm_service="svc-a", model="m1",
                      tokens_in=400, tokens_out=40, ts=now - 40 * 86400)
        return now

    def test_summary_filters(self, ledger):
        self._seed(ledger)
        assert ledger.summary(user_id="alice")["tokens_in"] == 300
        assert ledger.summary(user_id="alice",
                              channel="subagent")["tokens_in"] == 200
        assert ledger.summary(llm_service="svc-a")["tokens_in"] == 500
        now = time.time()
        assert ledger.summary(since=now - 86400)["tokens_in"] == 300

    def test_timeseries_grouped(self, ledger):
        self._seed(ledger)
        rows = ledger.timeseries(bucket="day", group_by="llm_service",
                                 user_id="alice")
        assert {r["grp"] for r in rows} == {"svc-a", "svc-b"}
        assert all("bucket" in r for r in rows)

    def test_timeseries_rejects_bad_dimensions(self, ledger):
        with pytest.raises(ValueError):
            ledger.timeseries(bucket="minute")
        with pytest.raises(ValueError):
            ledger.timeseries(group_by="1; DROP TABLE usage_events")
        with pytest.raises(ValueError):
            ledger.top(dimension="id")
        with pytest.raises(ValueError):
            ledger.top(order_by="cost_usd; --")

    def test_top_by_tokens(self, ledger):
        self._seed(ledger)
        top = ledger.top(dimension="conversation_id",
                         order_by="tokens_in", limit=1)
        assert top[0]["value"] == "c2"

    def test_conversation_cost_shape(self, ledger):
        self._seed(ledger)
        data = ledger.conversation_cost("c1")
        assert set(data) == {"total", "by_model"}
        assert data["by_model"]["m1"]["in"] == 100
        assert data["total"] == pytest.approx(110 * 10.0 / 1_000_000)
        assert ledger.total_cost() == pytest.approx(data["total"])

    def test_user_usage_rollup_shape(self, ledger):
        self._seed(ledger)
        u = ledger.user_usage("alice")
        assert u["total_in"] == 300 and u["total_out"] == 30
        assert u["models"]["m1"]["in"] == 100
        agent = u["agents"]["claude::svc-a"]
        assert agent["calls"] == 1 and agent["cost"] > 0
        assert len(u["daily"]) >= 1

    def test_all_usage(self, ledger):
        self._seed(ledger)
        allu = ledger.all_usage()
        assert set(allu) == {"alice", "bob"}

    def test_export_rows(self, ledger):
        self._seed(ledger)
        rows = ledger.export_rows(user_id="alice")
        assert len(rows) == 2
        assert {"id", "ts", "channel", "cost_usd"} <= set(rows[0])


class TestLegacyMigration:
    def test_token_usage_json_imported_once(self, tmp_path, monkeypatch):
        import core.paths as paths
        legacy = tmp_path / "token_usage.json"
        legacy.write_text(json.dumps({
            "alice": {"total_in": 60, "total_out": 30, "daily": {},
                      "models": {"m": {"in": 60, "out": 30}},
                      "agents": {"claude::svc": {
                          "agent": "claude", "llm_service": "svc",
                          "in": 60, "out": 30, "cache_read": 5,
                          "cache_write": 2, "calls": 3}}},
        }), encoding="utf-8")
        monkeypatch.setattr(paths, "TOKEN_USAGE_FILE", legacy)
        UsageLedger.reset()
        ledger = UsageLedger(path=str(tmp_path / "usage.db"))
        u = ledger.user_usage("alice")
        assert u["total_in"] == 60 and u["total_out"] == 30
        assert u["agents"]["claude::svc"]["in"] == 60
        assert ledger.summary(user_id="alice",
                              channel="migrated")["tokens_in"] == 60
        # File renamed — a second init must not double-import.
        assert not legacy.exists()
        assert legacy.with_suffix(".json.migrated").exists()
        UsageLedger.reset()
        ledger2 = UsageLedger(path=str(tmp_path / "usage.db"))
        assert ledger2.summary(user_id="alice")["tokens_in"] == 60
        UsageLedger.reset()


def _ff(roles=""):
    ff = FlowFile()
    if roles:
        ff.set_attribute("http.auth.roles", roles)
    return ff


class TestUsageQueryActions:
    def _seed(self, ledger):
        ledger.record(user_id="alice", channel="chat", conversation_id="c1",
                      agent_name="claude", llm_service="svc-a", model="m1",
                      tokens_in=100, tokens_out=10, cost_per_1m_input=10.0,
                      cost_per_1m_output=10.0)
        ledger.record(user_id="bob", channel="chat", conversation_id="c2",
                      agent_name="claude", llm_service="svc-a", model="m1",
                      tokens_in=400, tokens_out=40)

    def _run(self, action, body, user_id="alice", roles=""):
        from tasks.ai.actions.usage import _handle_usage
        ff = _ff(roles)
        out = _handle_usage(None, action, body, None, user_id, ff)
        assert out == [ff]
        return ff

    def test_summary_scoped_to_caller(self, ledger):
        self._seed(ledger)
        ff = self._run("usage_summary", {"user": "ALL"})  # non-admin: ignored
        data = json.loads(ff.get_content())
        assert data["summary"]["tokens_in"] == 100

    def test_summary_admin_all_users(self, ledger):
        self._seed(ledger)
        ff = self._run("usage_summary", {"user": "ALL"}, roles="admin")
        data = json.loads(ff.get_content())
        assert data["summary"]["tokens_in"] == 500

    def test_timeseries_and_top(self, ledger):
        self._seed(ledger)
        ff = self._run("usage_timeseries",
                       {"bucket": "day", "group_by": "llm_service"})
        assert json.loads(ff.get_content())["timeseries"]
        ff = self._run("usage_top", {"dimension": "conversation_id",
                                     "limit": 5}, roles="admin")
        top = json.loads(ff.get_content())["top"]
        assert top and top[0]["value"] in ("c1", "c2")

    def test_bad_dimension_is_400(self, ledger):
        self._seed(ledger)
        ff = self._run("usage_timeseries", {"bucket": "minute"})
        assert ff.get_attribute("http.response.status") == "400"
        assert "error" in json.loads(ff.get_content())

    def test_export_csv(self, ledger):
        self._seed(ledger)
        ff = self._run("usage_export", {"format": "csv"})
        text = ff.get_content().decode("utf-8")
        assert text.splitlines()[0].startswith("id,")
        assert ff.get_attribute("mime.type") == "text/csv"

    def test_cost_action_uses_frozen_ledger_cost(self, ledger, monkeypatch):
        self._seed(ledger)
        from core.service_registry import ServiceRegistry

        class _Reg:
            def resolve_by_type(self, *_a, **_k):
                return []
        monkeypatch.setattr(ServiceRegistry, "get_instance",
                            classmethod(lambda cls: _Reg()))
        ff = self._run("cost", {})
        data = json.loads(ff.get_content())
        svc = data["services"][0]
        # Frozen cost from the ledger even though the service (and its
        # current rates) is gone from the registry.
        assert svc["cost"] == pytest.approx(110 * 10.0 / 1_000_000)
        assert data["total_in"] == 100

    def test_get_cost_action_reads_ledger(self, ledger):
        self._seed(ledger)
        ff = self._run("get_cost", {"conversation_id": "c1"})
        data = json.loads(ff.get_content())
        assert data["total_usd"] == pytest.approx(110 * 10.0 / 1_000_000)
        assert data["by_model"]["m1"]["in"] == 100


class TestRealtimeUsageHook:
    def test_worker_usage_event_recorded(self, ledger):
        from services._livekit_sessions import _record_realtime_usage
        session = {"user_id": "alice", "conversation_id": "conv9",
                   "agent_name": "claude", "service_id": "lk",
                   "engine_cfg": {"model": "gpt-realtime",
                                  "provider": "openai"}}
        _record_realtime_usage(session, {"input_tokens": 120,
                                         "output_tokens": 40,
                                         "cached_tokens": 10,
                                         "kind": "RealtimeModelMetrics"})
        s = ledger.summary(user_id="alice", channel="realtime")
        assert s["tokens_in"] == 120 and s["tokens_out"] == 40
        assert s["cache_read"] == 10
        rows = ledger.export_rows(conversation_id="conv9")
        assert rows[0]["model"] == "gpt-realtime"
        assert rows[0]["provider"] == "openai"

    def test_no_tokens_records_nothing(self, ledger):
        from services._livekit_sessions import _record_realtime_usage
        _record_realtime_usage({"user_id": "alice"}, {"kind": "TTSMetrics"})
        assert ledger.summary(user_id="alice")["calls"] == 0
