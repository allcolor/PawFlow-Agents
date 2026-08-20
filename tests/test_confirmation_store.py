"""Durable confirmations + durable flow wait/notify."""

import json
import time

import pytest

from core import FlowFile
from core.confirmation_store import (
    ConfirmationStore, find_own_flow_ids, normalize_options,
    parse_timeout_seconds)


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ConfirmationStore(database_path=tmp_path / "conf.db")
    # No background thread, no SSE, no scheduler in unit tests.
    monkeypatch.setattr(s, "ensure_sweeper", lambda: None)
    monkeypatch.setattr(ConfirmationStore, "_publish",
                        staticmethod(lambda cid, et, rec: None))
    return s


# ── Timeout parsing ──────────────────────────────────────────────

def test_parse_timeout_accepts_days_months_years():
    assert parse_timeout_seconds(None) == 0
    assert parse_timeout_seconds("") == 0
    assert parse_timeout_seconds(3600) == 3600
    assert parse_timeout_seconds("90s") == 90
    assert parse_timeout_seconds("12h") == 12 * 3600
    assert parse_timeout_seconds("30d") == 30 * 86400
    assert parse_timeout_seconds("6mo") == 6 * 30 * 86400
    assert parse_timeout_seconds("2y") == 2 * 365 * 86400
    with pytest.raises(ValueError):
        parse_timeout_seconds("soon")
    with pytest.raises(ValueError):
        parse_timeout_seconds(-5)


def test_normalize_options_mixes_shapes():
    assert normalize_options(["a", {"value": "b", "label": "B!"}]) == [
        {"value": "a", "label": "a"}, {"value": "b", "label": "B!"}]


# ── Confirmations ────────────────────────────────────────────────

def _mk(store, **kw):
    args = dict(conversation_id="c1", user_id="u1", requester_kind="agent",
                requester="assistant", message="Deploy to prod?")
    args.update(kw)
    return store.create_confirmation(**args)


def test_confirm_defaults_to_yes_no_and_answers(store, monkeypatch):
    woken = {}
    monkeypatch.setattr(ConfirmationStore, "_resume_requester",
                        lambda self, rec: woken.update(rec))
    rec = _mk(store)
    assert rec["status"] == "pending"
    assert [o["value"] for o in rec["options"]] == ["yes", "no"]
    assert store.list_confirmations(user_id="u1")[0]["request_id"] == rec["request_id"]
    out = store.respond(rec["request_id"], "yes", answered_by="u1")
    assert out["status"] == "answered" and out["answer"] == "yes"
    assert woken["request_id"] == rec["request_id"]
    # Second answer refused, gone from the pending list.
    with pytest.raises(ValueError):
        store.respond(rec["request_id"], "no")
    assert store.list_confirmations(user_id="u1", status="pending") == []


def test_choice_and_multi_validate_against_options(store, monkeypatch):
    monkeypatch.setattr(ConfirmationStore, "_resume_requester",
                        lambda self, rec: None)
    with pytest.raises(ValueError):
        _mk(store, mode="choice", options=["only-one"])
    rec = _mk(store, mode="multi", options=["red", "green", "blue"])
    with pytest.raises(ValueError):
        store.respond(rec["request_id"], ["red", "purple"])
    out = store.respond(rec["request_id"], ["red", "blue"])
    assert out["answer"] == ["red", "blue"]


def test_answer_resolves_the_confirmation_signal(store, monkeypatch):
    # A flow durably waiting on confirmation:<id> resumes on the answer.
    monkeypatch.setattr(
        ConfirmationStore, "deliver_resolved", lambda self: 0)
    rec = _mk(store, requester_kind="flow", requester="flowX")
    ff = FlowFile(content=b"payload")
    wait_id = store.park_wait(
        signal_id=f"confirmation:{rec['request_id']}",
        instance_id="inst1", task_id="t1", flowfile=ff)
    assert wait_id
    store.respond(rec["request_id"], "no")
    waits = store.list_waits(status="resolved")
    assert len(waits) == 1 and waits[0]["wait_id"] == wait_id


def test_agent_answer_schedules_a_wake(store, monkeypatch):
    calls = {}

    class _Sched:
        def schedule_delay(self, cid, delay, key="", reason="", user_id=""):
            calls.update(cid=cid, key=key, reason=reason, user_id=user_id)
    import core.poll_scheduler as ps
    monkeypatch.setattr(ps.PollScheduler, "instance",
                        classmethod(lambda cls: _Sched()))
    import tasks.ai.agent_loop as al
    monkeypatch.setattr(al.AgentLoopTask, "wake_poller",
                        classmethod(lambda cls: None))
    rec = _mk(store)
    store.respond(rec["request_id"], "yes", answered_by="u1")
    assert calls["cid"] == "c1"
    assert rec["request_id"] in calls["key"]
    assert "[scheduled:assistant]" in calls["reason"]
    assert '"yes"' in calls["reason"]


def test_expiry_sweep_expires_and_signals(store, monkeypatch):
    monkeypatch.setattr(ConfirmationStore, "_resume_requester",
                        lambda self, rec: None)
    rec = _mk(store, expires_in_seconds=0.01)
    time.sleep(0.05)
    store.sweep_once()
    assert store.get_confirmation(rec["request_id"])["status"] == "expired"
    # The stored signal value carries the expiry for late waiters.
    value = store.consume_signal_value(f"confirmation:{rec['request_id']}")
    assert value == {"status": "expired", "answer": None}
    with pytest.raises(ValueError):
        store.respond(rec["request_id"], "yes")


def test_cancel_releases_waiters(store):
    rec = _mk(store)
    assert store.cancel(rec["request_id"]) is True
    assert store.cancel(rec["request_id"]) is False
    value = store.consume_signal_value(f"confirmation:{rec['request_id']}")
    assert value["status"] == "cancelled"


# ── Durable wait/notify ──────────────────────────────────────────

class _FakeExecutor:
    def __init__(self):
        self.injected = []
        self.is_running = True

    def inject(self, flowfile, entry_task_id=None):
        self.injected.append((flowfile, entry_task_id))
        return True


@pytest.fixture
def fake_registry(monkeypatch):
    from core.executor_registry import ExecutorRegistry
    executor = _FakeExecutor()

    class _Reg:
        _executors = {"inst1": executor}
        def get(self, instance_id):
            return self._executors.get(instance_id)
    monkeypatch.setattr(ExecutorRegistry, "get_instance",
                        classmethod(lambda cls: _Reg()))
    return executor


def test_notify_resumes_a_parked_flowfile(store, fake_registry):
    ff = FlowFile(content=b"hello")
    ff.set_attribute("custom", "kept")
    wait_id = store.park_wait(signal_id="sig1", instance_id="inst1",
                              task_id="waitTask", flowfile=ff)
    assert wait_id
    resolved = store.notify_signal("sig1", {"answer": 42})
    assert resolved == 1
    assert len(fake_registry.injected) == 1
    restored, entry = fake_registry.injected[0]
    assert entry == "waitTask"
    assert restored.get_content() == b"hello"
    assert restored.get_attribute("custom") == "kept"
    assert restored.get_attribute("durable.wait.status") == "signaled"
    assert json.loads(restored.get_attribute("durable.wait.value")) == {"answer": 42}
    assert store.list_waits(status="delivered")[0]["wait_id"] == wait_id


def test_notify_before_wait_passes_through_immediately(store):
    assert store.notify_signal("early", "v1") == 0
    ff = FlowFile()
    wait_id = store.park_wait(signal_id="early", instance_id="i",
                              task_id="t", flowfile=ff)
    assert wait_id is None   # value consumed, caller passes through
    assert ff.get_attribute("durable.wait.status") == "signaled"
    assert json.loads(ff.get_attribute("durable.wait.value")) == "v1"
    # Consumed: a second wait parks normally.
    assert store.park_wait(signal_id="early", instance_id="i",
                           task_id="t", flowfile=FlowFile()) is not None


def test_delivery_waits_for_the_flow_to_run_again(store, monkeypatch):
    from core.executor_registry import ExecutorRegistry

    class _EmptyReg:
        _executors = {}
        def get(self, instance_id):
            return None
    monkeypatch.setattr(ExecutorRegistry, "get_instance",
                        classmethod(lambda cls: _EmptyReg()))
    store.park_wait(signal_id="sig", instance_id="inst1", task_id="t",
                    flowfile=FlowFile())
    store.notify_signal("sig", "v")
    # Not running: stays resolved (sweeper retries later).
    assert store.list_waits(status="resolved")
    executor = _FakeExecutor()

    class _Reg:
        _executors = {"inst1": executor}
        def get(self, instance_id):
            return self._executors.get(instance_id)
    monkeypatch.setattr(ExecutorRegistry, "get_instance",
                        classmethod(lambda cls: _Reg()))
    assert store.deliver_resolved() == 1
    assert executor.injected


def test_wait_timeout_delivers_with_timeout_status(store, fake_registry):
    store.park_wait(signal_id="never", instance_id="inst1", task_id="t",
                    flowfile=FlowFile(), timeout_seconds=0.01)
    time.sleep(0.05)
    store.sweep_once()
    restored, _ = fake_registry.injected[0]
    assert restored.get_attribute("durable.wait.status") == "timeout"


# ── Flow tasks ───────────────────────────────────────────────────

def test_durable_wait_task_parks_and_resumes(store, monkeypatch, fake_registry):
    import core.confirmation_store as mod
    monkeypatch.setattr(mod.ConfirmationStore, "instance",
                        classmethod(lambda cls: store))
    from tasks.control.durable_confirm import DurableWaitTask
    task = DurableWaitTask({"signal_id": "sigT", "timeout": "30d"})
    monkeypatch.setattr(mod, "find_own_flow_ids",
                        lambda t: {"instance_id": "inst1", "task_id": "wt"})
    ff = FlowFile(content=b"x")
    assert task.execute(ff) == []          # parked
    store.notify_signal("sigT", "done")    # resumes via fake executor
    restored, entry = fake_registry.injected[0]
    assert entry == "wt"
    # Re-injected FlowFile passes straight through the task.
    assert task.execute(restored) == [restored]


def test_durable_wait_task_requires_deployed_flow(store, monkeypatch):
    import core.confirmation_store as mod
    monkeypatch.setattr(mod.ConfirmationStore, "instance",
                        classmethod(lambda cls: store))
    monkeypatch.setattr(mod, "find_own_flow_ids", lambda t: None)
    from core import TaskError
    from tasks.control.durable_confirm import DurableWaitTask
    with pytest.raises(TaskError, match="DEPLOYED continuous flow"):
        DurableWaitTask({"signal_id": "s"}).execute(FlowFile())


def test_request_confirmation_task_stamps_signal(store, monkeypatch):
    import core.confirmation_store as mod
    monkeypatch.setattr(mod.ConfirmationStore, "instance",
                        classmethod(lambda cls: store))
    from tasks.control.durable_confirm import RequestConfirmationTask
    task = RequestConfirmationTask({"message": "Go on?",
                                    "mode": "choice",
                                    "options": "go, stop, retry"})
    task.set_runtime_context(user_id="u1", conversation_id="c9")
    ff = FlowFile()
    out = task.execute(ff)[0]
    request_id = out.get_attribute("confirmation.request_id")
    assert request_id.startswith("req_")
    assert out.get_attribute("confirmation.signal_id") == f"confirmation:{request_id}"
    rec = store.get_confirmation(request_id)
    assert rec["requester_kind"] == "flow"
    assert [o["value"] for o in rec["options"]] == ["go", "stop", "retry"]


def test_durable_notify_task_fires_signal(store, monkeypatch):
    import core.confirmation_store as mod
    monkeypatch.setattr(mod.ConfirmationStore, "instance",
                        classmethod(lambda cls: store))
    from tasks.control.durable_confirm import DurableNotifyTask
    ff = FlowFile()
    ff.set_attribute("result", "ok")
    out = DurableNotifyTask({"signal_id": "sigN",
                             "value_attribute": "result"}).execute(ff)[0]
    assert out.get_attribute("durable.notify.resolved") == "0"
    assert store.consume_signal_value("sigN") == "ok"


# ── Agent tool + actions ─────────────────────────────────────────

def test_request_confirmation_tool_creates_pending(store, monkeypatch):
    import core.confirmation_store as mod
    monkeypatch.setattr(mod.ConfirmationStore, "instance",
                        classmethod(lambda cls: store))
    from core.handlers.user_interaction import RequestConfirmationHandler
    handler = RequestConfirmationHandler()
    handler.set_conversation_id("c1")
    handler.set_user_id("u1")
    handler.set_agent_name("assistant")
    out = handler.execute({"message": "Deploy?", "mode": "choice",
                           "options": ["now", "later"]})
    assert "pending" in out and "req_" in out
    rec = store.list_confirmations(user_id="u1")[0]
    assert rec["requester"] == "assistant"
    assert handler.execute({}) .startswith("Error")


def test_actions_list_respond_and_authz(store, monkeypatch):
    import core.confirmation_store as mod
    monkeypatch.setattr(mod.ConfirmationStore, "instance",
                        classmethod(lambda cls: store))
    monkeypatch.setattr(ConfirmationStore, "_resume_requester",
                        lambda self, rec: None)
    from tasks.ai.actions.confirmations import _handle_confirmations
    rec = _mk(store)

    def call(action, body, user_id):
        ff = FlowFile()
        result = _handle_confirmations(None, action, body, None, user_id, ff)
        assert result is not None
        return json.loads(ff.get_content().decode()), ff

    data, _ = call("list_confirmations", {}, "u1")
    assert len(data["confirmations"]) == 1
    # A stranger neither sees nor answers it (no existence oracle).
    data, ff = call("respond_confirmation",
                    {"request_id": rec["request_id"], "answer": "yes"},
                    "intruder")
    assert data["error"] and ff.get_attribute("http.response.status") == "404"
    data, _ = call("respond_confirmation",
                   {"request_id": rec["request_id"], "answer": "yes"}, "u1")
    assert data["ok"] and data["confirmation"]["answer"] == "yes"
    assert _handle_confirmations(None, "unrelated", {}, None, "u1",
                                 FlowFile()) is None


def test_find_own_flow_ids_matches_identity(monkeypatch):
    from core.executor_registry import ExecutorRegistry
    sentinel = object()

    class _Exec:
        _tasks = {"t42": sentinel}

    class _Reg:
        _executors = {"instX": _Exec()}
    monkeypatch.setattr(ExecutorRegistry, "get_instance",
                        classmethod(lambda cls: _Reg()))
    assert find_own_flow_ids(sentinel) == {"instance_id": "instX",
                                           "task_id": "t42"}
    assert find_own_flow_ids(object()) is None
