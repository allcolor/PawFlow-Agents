"""Tests for delegate_status / delegate_result and their registry state.

Verifies that:
1. register_live_delegate stamps started_at and list_live_delegates returns a
   sanitized, caller-filtered snapshot (no client/task objects)
2. record_finished_delegate keeps a bounded, caller-filtered ring buffer with
   capped retained response text
3. DelegateStatusHandler reports live + finished delegates (flash and plain)
   for the calling agent only
4. DelegateResultHandler returns the retained output by task_id, reports
   still-running delegates, and hides other callers' results
5. A shared request is pending while it waits in the target's queue, read
   once taken, running through the target's turn, completed by its
   turn_final row, and consumed when the target answered elsewhere
6. The status is compact and bounded
7. Wiring: handlers registered in the default registry, results recorded in
   _inject_bg_result, relay source-context injection covers both tools
"""
import json
import time

from unittest.mock import MagicMock, patch

import pytest

from core.conversation_store import ConversationStore
from core.agent_executor import (
    AgentTask,
    get_finished_delegate,
    list_finished_delegates,
    list_live_delegates,
    record_finished_delegate,
    register_live_delegate,
    unregister_live_delegate,
)
from core.handlers.resource_agent import (
    DelegateResultHandler,
    DelegateStatusHandler,
)

# Recent timestamps: a delegate unanswered for over a day is reported as stale.
_NOW = time.time()


def _make_handler(cls, conversation_id, source_agent="agentA"):
    h = cls()
    h.set_conversation_id(conversation_id)
    h.set_user_id("user1")
    h.set_source_agent(source_agent, "svc_a")
    return h


@pytest.fixture
def fresh_queues():
    from core.pending_queue import PendingQueue
    PendingQueue.drop_cache()
    yield PendingQueue
    PendingQueue.drop_cache()


def _shared_request(task_id, ts, target="reviewer"):
    return {
        "role": "user",
        "content": f"request {task_id}",
        "timestamp": ts,
        "source": {
            "type": "agent_delegate",
            "from": "agentA",
            "to": target,
            "target_agent": target,
            "task_id": task_id,
        },
    }


def _shared_reply(task_id, ts, *, turn_final, role="assistant", content="",
                  **extra):
    """A row of the reviewer's turn answering ``task_id``.

    ``turn_final=None`` leaves the marker out, like the caller's user row for
    a reply delivered while it was busy and like rows that predate it.
    """
    row = {
        "role": role,
        "content": content or f"row of {task_id}",
        "timestamp": ts,
        "source": {
            "type": "agent_delegate",
            "from": "reviewer",
            "to": "agentA",
            "kind": "reply",
            "task_id": task_id,
        },
        **extra,
    }
    if turn_final is not None:
        row["turn_id"] = f"turn-{task_id}"
        row["turn_final"] = turn_final
    return row


class TestLiveDelegateSnapshot:
    def test_register_stamps_started_at_and_list_sanitizes(self):
        task = AgentTask(id="tid1", agent_name="agentA::flash::critic",
                         message="m")
        register_live_delegate("conv_live1", "agentA",
                               "agentA::flash::critic", "tid1",
                               MagicMock(), task)
        try:
            entries = list_live_delegates("conv_live1", "agentA")
            assert len(entries) == 1
            e = entries[0]
            assert e["target"] == "agentA::flash::critic"
            assert e["task_id"] == "tid1"
            assert e["started_at"] > 0
            assert e["pending_messages"] == 0
            assert "client" not in e and "task" not in e
        finally:
            unregister_live_delegate("conv_live1", "agentA",
                                     "agentA::flash::critic", "tid1")

    def test_list_filters_by_caller_and_conversation(self):
        task = AgentTask(id="tid1", agent_name="x", message="m")
        register_live_delegate("conv_live2", "agentA", "t1", "tid1",
                               MagicMock(), task)
        register_live_delegate("conv_live2", "agentB", "t2", "tid2",
                               MagicMock(), task)
        register_live_delegate("conv_other", "agentA", "t3", "tid3",
                               MagicMock(), task)
        try:
            targets = {e["target"]
                       for e in list_live_delegates("conv_live2", "agentA")}
            assert targets == {"t1"}
            all_conv = {e["target"] for e in list_live_delegates("conv_live2")}
            assert all_conv == {"t1", "t2"}
        finally:
            unregister_live_delegate("conv_live2", "agentA", "t1", "tid1")
            unregister_live_delegate("conv_live2", "agentB", "t2", "tid2")
            unregister_live_delegate("conv_other", "agentA", "t3", "tid3")


class TestFinishedDelegateRing:
    def test_record_and_list_filtered(self):
        record_finished_delegate("conv_fin1", "agentA",
                                 "agentA::flash::auditor", "tidA",
                                 "completed", "", 1234.5)
        record_finished_delegate("conv_fin1", "agentB",
                                 "agentB::flash::other", "tidB",
                                 "error", "boom", 10.0)
        mine = list_finished_delegates("conv_fin1", "agentA")
        assert [e["task_id"] for e in mine] == ["tidA"]
        assert mine[0]["status"] == "completed"
        assert mine[0]["duration_ms"] == 1234.5
        assert mine[0]["finished_at"] > 0
        assert "parent_conv" not in mine[0]

    def test_error_truncated_and_ring_bounded(self):
        from core._agent_executor_base import _FINISHED_DELEGATES_MAX
        for i in range(_FINISHED_DELEGATES_MAX + 5):
            record_finished_delegate("conv_fin2", "agentA", "t", f"tid{i}",
                                     "error", "x" * 2000)
        entries = list_finished_delegates("conv_fin2", "agentA")
        assert len(entries) <= _FINISHED_DELEGATES_MAX
        assert entries[-1]["task_id"] == f"tid{_FINISHED_DELEGATES_MAX + 4}"
        assert len(entries[-1]["error"]) == 500

    def test_response_retained_capped_and_summarized(self):
        from core._agent_executor_base import _FINISHED_RESPONSE_MAX_CHARS
        record_finished_delegate("conv_fin3", "agentA", "agentA::flash::big",
                                 "tidR", "completed",
                                 response="y" * (_FINISHED_RESPONSE_MAX_CHARS + 10))
        summary = list_finished_delegates("conv_fin3", "agentA")[-1]
        assert "response" not in summary
        assert summary["response_chars"] == _FINISHED_RESPONSE_MAX_CHARS
        full = get_finished_delegate("conv_fin3", "agentA", "tidR")
        assert len(full["response"]) == _FINISHED_RESPONSE_MAX_CHARS
        assert get_finished_delegate("conv_fin3", "agentA", "nope") is None


class TestDelegateStatusHandler:
    def test_shared_delegate_is_recovered_from_durable_transcript(self, tmp_path):
        conv = "conv_dsh_shared_live"
        transcript = [{
            "role": "user",
            "content": "audit the implementation",
            "timestamp": _NOW + 0,
            "source": {
                "type": "agent_delegate",
                "from": "agentA",
                "to": "reviewer",
                "target_agent": "reviewer",
                "task_id": "tid-shared-live",
            },
        }, {
            "role": "user",
            "content": "private task from another caller",
            "timestamp": _NOW + 1,
            "source": {
                "type": "agent_delegate",
                "from": "agentB",
                "to": "reviewer",
                "target_agent": "reviewer",
                "task_id": "tid-other-caller",
            },
        }]
        store = ConversationStore(
            store_dir=str(tmp_path / "shared-live-conversations"))
        store.save(conv, transcript, user_id="user1")

        with patch.object(
                store, "load",
                side_effect=AssertionError(
                    "delegate state must stream instead of loading transcript")), \
                patch("core.conversation_store.ConversationStore.instance",
                      return_value=store):
            out = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))

        assert out["counts"] == {
            "live": 1, "finished": 0, "stale": 0, "consumed": 0}
        assert out["live"][0]["task_id"] == "tid-shared-live"
        assert out["live"][0]["agent"] == "reviewer"
        assert out["live"][0]["mode"] == "shared"
        # Not in the reviewer's queue, and nothing from it since.
        assert out["live"][0]["status"] == "read"

    def test_durable_and_runtime_live_delegate_are_merged_once(self, tmp_path):
        conv = "conv_dsh_shared_runtime"
        task_id = "tid-shared-runtime"
        store = ConversationStore(
            store_dir=str(tmp_path / "shared-runtime-conversations"))
        store.save(conv, [{
            "role": "user",
            "content": "audit",
            "timestamp": _NOW + 0,
            "source": {
                "type": "agent_delegate",
                "from": "agentA",
                "to": "reviewer",
                "target_agent": "reviewer",
                "task_id": task_id,
            },
        }], user_id="user1")
        task = AgentTask(id=task_id, agent_name="reviewer", message="audit")
        register_live_delegate(
            conv, "agentA", "reviewer", task_id, MagicMock(), task)
        try:
            with patch("core.conversation_store.ConversationStore.instance",
                       return_value=store):
                out = json.loads(
                    _make_handler(DelegateStatusHandler, conv).execute({}))
        finally:
            unregister_live_delegate(conv, "agentA", "reviewer", task_id)

        assert out["counts"] == {
            "live": 1, "finished": 0, "stale": 0, "consumed": 0}
        assert out["live"][0]["task_id"] == task_id
        assert out["live"][0]["status"] == "running"
        assert out["live"][0]["runtime_attached"] is True

    def test_shared_delegate_reply_is_recovered_after_registry_restart(
            self, tmp_path):
        conv = "conv_dsh_shared_finished"
        transcript = [
            {
                "role": "user",
                "content": "audit the implementation",
                "timestamp": 100.0,
                "source": {
                    "type": "agent_delegate",
                    "from": "agentA",
                    "to": "reviewer",
                    "target_agent": "reviewer",
                    "task_id": "tid-shared-finished",
                },
            },
            {
                "role": "assistant",
                "content": "The audit passes.",
                "timestamp": 104.0,
                "source": {
                    "type": "agent_delegate",
                    "from": "reviewer",
                    "to": "agentA",
                    "kind": "reply",
                    "task_id": "tid-shared-finished",
                },
            },
        ]
        store = ConversationStore(
            store_dir=str(tmp_path / "shared-finished-conversations"))
        store.save(conv, transcript, user_id="user1")

        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            status = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))
            result = json.loads(_make_handler(
                DelegateResultHandler, conv).execute({
                    "task_id": "tid-shared-finished",
                }))

        assert status["counts"] == {
            "live": 0, "finished": 1, "stale": 0, "consumed": 0}
        assert status["finished"][0]["task_id"] == "tid-shared-finished"
        assert status["finished"][0]["mode"] == "shared"
        assert result["status"] == "completed"
        assert result["response"] == "The audit passes."
        assert result["duration_ms"] == 4000.0

    def test_durable_finished_results_remain_bounded(self, tmp_path):
        conv = "conv_dsh_shared_bounded"
        transcript = []
        for index in range(105):
            task_id = f"tid-bounded-{index}"
            transcript.extend([
                {
                    "role": "user",
                    "content": "request",
                    "timestamp": float(index * 2 + 1),
                    "source": {
                        "type": "agent_delegate",
                        "from": "agentA",
                        "to": "reviewer",
                        "target_agent": "reviewer",
                        "task_id": task_id,
                    },
                },
                {
                    "role": "assistant",
                    "content": f"result-{index}",
                    "timestamp": float(index * 2 + 2),
                    "source": {
                        "type": "agent_delegate",
                        "from": "reviewer",
                        "to": "agentA",
                        "kind": "reply",
                        "task_id": task_id,
                    },
                },
            ])
        store = ConversationStore(
            store_dir=str(tmp_path / "shared-bounded-conversations"))
        store.save(conv, transcript, user_id="user1")

        state = store.load_delegate_state(conv, "agentA", user_id="user1")

        assert len(state["finished"]) == 100
        assert state["finished"][0]["task_id"] == "tid-bounded-5"
        assert state["finished"][-1]["task_id"] == "tid-bounded-104"

    def test_reports_flash_and_plain_delegates(self):
        conv = "conv_dsh1"
        task = AgentTask(id="tid1", agent_name="agentA::flash::critic",
                         message="m")
        register_live_delegate(conv, "agentA", "agentA::flash::critic",
                               "tid1", MagicMock(), task)
        register_live_delegate(conv, "agentA", "reviewer", "tid2",
                               MagicMock(), task)
        record_finished_delegate(conv, "agentA", "agentA::flash::done1",
                                 "tid3", "completed", "", 42.0,
                                 response="r1")
        record_finished_delegate(conv, "agentA", "reviewer", "tid4",
                                 "completed")
        try:
            h = _make_handler(DelegateStatusHandler, conv)
            out = json.loads(h.execute({}))
            assert out["counts"] == {
                "live": 2, "finished": 2, "stale": 0, "consumed": 0}
            by_tid = {e["task_id"]: e for e in out["live"]}
            assert by_tid["tid1"]["name"] == "critic"
            assert by_tid["tid1"]["mode"] == "flash"
            assert by_tid["tid1"]["age_seconds"] >= 0
            # A plain agent's name is its agent; mode already says flash.
            assert by_tid["tid2"]["agent"] == "reviewer"
            assert "name" not in by_tid["tid2"]
            assert "kind" not in by_tid["tid1"]
            fin = {e["task_id"]: e for e in out["finished"]}
            assert fin["tid3"]["mode"] == "flash"
            assert fin["tid3"]["response_chars"] == 2
            assert fin["tid4"]["agent"] == "reviewer"
            assert "response_chars" not in fin["tid4"]
        finally:
            unregister_live_delegate(conv, "agentA",
                                     "agentA::flash::critic", "tid1")
            unregister_live_delegate(conv, "agentA", "reviewer", "tid2")

    def test_other_callers_delegates_are_hidden(self):
        conv = "conv_dsh2"
        task = AgentTask(id="tid1", agent_name="agentB::flash::spy",
                         message="m")
        register_live_delegate(conv, "agentB", "agentB::flash::spy",
                               "tid1", MagicMock(), task)
        try:
            h = _make_handler(DelegateStatusHandler, conv)
            out = json.loads(h.execute({}))
            assert out["counts"] == {
                "live": 0, "finished": 0, "stale": 0, "consumed": 0}
        finally:
            unregister_live_delegate(conv, "agentB",
                                     "agentB::flash::spy", "tid1")

    def test_error_without_source_agent(self):
        h = DelegateStatusHandler()
        h.set_conversation_id("conv_dsh3")
        out = h.execute({})
        assert out.startswith("Error: delegate_status")


class TestSharedDelegateLifecycle:
    def test_queued_request_is_pending_and_taken_one_is_read(
            self, tmp_path, fresh_queues):
        conv = "conv_dsl_queue"
        queued = _shared_request("tid-queued", _NOW - 30)
        store = ConversationStore(
            store_dir=str(tmp_path / "queue-conversations"))
        store.save(conv, [_shared_request("tid-taken", _NOW - 60), queued],
                   user_id="user1")

        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            fresh_queues.for_agent(conv, "reviewer").enqueue(
                dict(queued, msg_id="m-queued", ts=_NOW - 30),
                source="delegate_reply")
            out = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))

        assert {e["task_id"]: e["status"] for e in out["live"]} == {
            "tid-taken": "read", "tid-queued": "pending"}
        assert out["counts"]["consumed"] == 0

    def test_request_answered_under_another_task_is_consumed(
            self, tmp_path, fresh_queues):
        conv = "conv_dsl_consumed"
        store = ConversationStore(
            store_dir=str(tmp_path / "consumed-conversations"))
        store.save(conv, [
            # The reviewer answers both in one turn, under the newest...
            _shared_request("tid-batched", _NOW - 100),
            _shared_request("tid-newest", _NOW - 90),
            _shared_reply("tid-newest", _NOW - 80, turn_final=True,
                          content="Both are done."),
            # ...and this one with a new delegate to the caller.
            _shared_request("tid-asked-back", _NOW - 70),
            {
                "role": "user",
                "content": "Which branch?",
                "timestamp": _NOW - 60,
                "source": {
                    "type": "agent_delegate",
                    "from": "reviewer",
                    "to": "agentA",
                    "target_agent": "agentA",
                    "task_id": "tid-question",
                },
            },
        ], user_id="user1")

        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            out = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))
            result = json.loads(_make_handler(
                DelegateResultHandler, conv).execute({
                    "task_id": "tid-batched"}))

        assert out["live"] == []
        assert out["counts"] == {
            "live": 0, "finished": 1, "stale": 0, "consumed": 2}
        assert [e["task_id"] for e in out["finished"]] == ["tid-newest"]
        assert out["finished"][0]["duration_ms"] == 10000
        assert result["status"] == "consumed"
        assert "another task_id" in result["message"]

    def test_target_turn_keeps_request_running_until_turn_final(
            self, tmp_path, fresh_queues):
        conv = "conv_dsl_running"
        base = _NOW - 600
        request = _shared_request("tid-work", base)
        turn = [
            _shared_reply("tid-work", base + 1, turn_final=False,
                          tool_calls=[{"id": "tc1", "name": "bash",
                                       "arguments": {"command": "make test"}}]),
            _shared_reply("tid-work", base + 2, turn_final=False, role="tool",
                          content="12 passed", tool_call_id="tc1"),
            _shared_reply("tid-work", base + 3, turn_final=False,
                          content="Tests pass, writing the summary."),
        ]
        store = ConversationStore(
            store_dir=str(tmp_path / "running-conversations"))
        store.save(conv, [request] + turn, user_id="user1")
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            running = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))

        # The turn's last row completes it; the copy delivered to the busy
        # caller half a minute later changes neither duration nor text.
        final = _shared_reply("tid-work", base + 10, turn_final=True,
                              content="All 12 tests pass.")
        delivered = _shared_reply("tid-work", base + 40, turn_final=None,
                                  role="user", content="Delivered copy.")
        store.save(conv, [request] + turn + [final, delivered],
                   user_id="user1")
        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            done = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))
            result = json.loads(_make_handler(
                DelegateResultHandler, conv).execute({"task_id": "tid-work"}))

        assert [(e["task_id"], e["status"]) for e in running["live"]] == [
            ("tid-work", "running")]
        assert running["counts"]["finished"] == 0
        assert done["counts"] == {
            "live": 0, "finished": 1, "stale": 0, "consumed": 0}
        assert result["status"] == "completed"
        assert result["response"] == "All 12 tests pass."
        assert result["duration_ms"] == pytest.approx(10000.0)

    def test_turn_left_without_marker_completes_on_next_task(
            self, tmp_path, fresh_queues):
        conv = "conv_dsl_unmarked"
        base = _NOW - 600
        store = ConversationStore(
            store_dir=str(tmp_path / "unmarked-conversations"))
        store.save(conv, [
            _shared_request("tid-first", base),
            _shared_reply("tid-first", base + 5, turn_final=False,
                          content="First answer."),
            _shared_request("tid-second", base + 20),
            _shared_reply("tid-second", base + 30, turn_final=False,
                          content="Working on it."),
        ], user_id="user1")

        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            out = json.loads(
                _make_handler(DelegateStatusHandler, conv).execute({}))

        assert [e["task_id"] for e in out["finished"]] == ["tid-first"]
        assert out["finished"][0]["duration_ms"] == 5000
        assert [(e["task_id"], e["status"]) for e in out["live"]] == [
            ("tid-second", "running")]

    def test_status_is_compact_and_bounded(self):
        from core.handlers import delegate_status as module
        now = time.time()
        live = [{"task_id": f"live{i}", "target": "GameDev3",
                 "mode": "shared", "status": "pending",
                 "pending_messages": 0, "started_at": now - 600 + i}
                for i in range(120)]
        live.append({"task_id": "flash1", "target": "agentA::flash::critic",
                     "mode": "flash", "status": "running",
                     "runtime_attached": True, "pending_messages": 0,
                     "started_at": now - 1})
        finished = [{"task_id": f"done{i}", "target": "GameDev4",
                     "mode": "shared", "status": "completed", "error": "",
                     "duration_ms": 1234.5678,
                     "finished_at": now - i - 0.123456,
                     "response": "x" * 4000} for i in range(100)]
        handler = module.DelegateStatusHandler.__new__(
            module.DelegateStatusHandler)
        handler._conversation_id = "conv"
        handler._user_id = "owner"
        with patch.object(module.DelegateStatusHandler,
                          "_resolve_source_context",
                          return_value=("agentA", "")), \
                patch.object(module, "_merged_state",
                             return_value=(live, finished)), \
                patch("core.service_registry._parent_conversation_id",
                      return_value=""):
            raw = handler.execute({})
        out = json.loads(raw)

        assert "\n" not in raw
        assert len(raw) < 25_000
        assert out["counts"] == {
            "live": 121, "finished": 100, "stale": 0, "consumed": 0}
        assert len(out["live"]) == module._LIVE_SHOWN
        assert set(out["live"][0]) == {
            "task_id", "mode", "status", "agent", "age_seconds"}
        assert out["live"][-1]["name"] == "critic"
        assert "kind" not in out["live"][-1]
        done = out["finished"][0]
        assert set(done) == {"task_id", "mode", "status", "agent",
                             "duration_ms", "finished_at", "response_chars"}
        assert done["duration_ms"] == 1235
        assert done["finished_at"] == round(done["finished_at"], 1)


class TestDelegateResultHandler:
    def test_pending_durable_delegate_scans_transcript_once(self, tmp_path):
        conv = "conv_dr_pending_once"
        store = ConversationStore(
            store_dir=str(tmp_path / "pending-once-conversations"))
        store.save(conv, [{
            "role": "user",
            "content": "audit",
            "timestamp": _NOW + 0,
            "source": {
                "type": "agent_delegate",
                "from": "agentA",
                "to": "reviewer",
                "task_id": "tid-pending-once",
            },
        }], user_id="user1")

        with patch.object(
                store, "load_delegate_state",
                wraps=store.load_delegate_state) as scan, \
                patch("core.conversation_store.ConversationStore.instance",
                      return_value=store):
            result = json.loads(_make_handler(
                DelegateResultHandler, conv).execute({
                    "task_id": "tid-pending-once",
                }))

        # Not in the reviewer's queue: taken, and no answer yet.
        assert result["status"] == "read"
        scan.assert_called_once()

    def test_finished_isolated_delegate_is_recovered_from_display_trace(
            self, tmp_path):
        conv = "conv_dr_trace"
        store = ConversationStore(
            store_dir=str(tmp_path / "trace-conversations"))
        store.save(conv, [], user_id="user1")
        store.create_display_trace(
            conv, "tid-trace", {
                "name": "auditor",
                "parent_agent": "agentA",
                "task_id": "tid-trace",
            },
            user_id="user1",
        )
        store.append_display_trace(
            conv, "tid-trace", {
                "type": "done",
                "status": "completed",
                "error": "",
            },
            content_update="durable result",
        )

        with patch("core.conversation_store.ConversationStore.instance",
                   return_value=store):
            result = json.loads(_make_handler(
                DelegateResultHandler, conv).execute({"task_id": "tid-trace"}))

        assert result["status"] == "completed"
        assert result["response"] == "durable result"
        assert result["duration_ms"] >= 0.0
        assert result["mode"] == "isolated"

    def test_returns_full_response_of_finished_delegate(self):
        conv = "conv_dr1"
        record_finished_delegate(conv, "agentA", "agentA::flash::auditor",
                                 "tidF", "completed", "", 99.0,
                                 response="the full report text")
        h = _make_handler(DelegateResultHandler, conv)
        out = json.loads(h.execute({"task_id": "tidF"}))
        assert out["response"] == "the full report text"
        assert out["status"] == "completed"
        assert out["name"] == "auditor"
        assert out["kind"] == "flash"

    def test_running_delegate_reports_running(self):
        conv = "conv_dr2"
        task = AgentTask(id="tidL", agent_name="slowagent", message="m")
        register_live_delegate(conv, "agentA", "slowagent", "tidL",
                               MagicMock(), task)
        try:
            h = _make_handler(DelegateResultHandler, conv)
            out = json.loads(h.execute({"task_id": "tidL"}))
            assert out["status"] == "running"
        finally:
            unregister_live_delegate(conv, "agentA", "slowagent", "tidL")

    def test_unknown_task_id_and_missing_param(self):
        h = _make_handler(DelegateResultHandler, "conv_dr3")
        assert h.execute({"task_id": "ghost"}).startswith("Error: no finished")
        assert h.execute({}).startswith("Error: delegate_result requires")

    def test_other_callers_results_are_hidden(self):
        conv = "conv_dr4"
        record_finished_delegate(conv, "agentB", "agentB::flash::sec",
                                 "tidS", "completed", response="private")
        h = _make_handler(DelegateResultHandler, conv)
        assert h.execute({"task_id": "tidS"}).startswith("Error: no finished")


class TestWiringInvariants:
    def test_handlers_registered_in_default_registry(self):
        import inspect
        import core.tool_registry as tr
        src = inspect.getsource(tr)
        assert "registry.register(DelegateStatusHandler())" in src
        assert "registry.register(DelegateResultHandler())" in src

    def test_inject_bg_result_records_finished_delegates(self):
        import inspect
        from core.handlers._spawn_delivery import _SpawnDeliveryMixin
        src = inspect.getsource(_SpawnDeliveryMixin._inject_bg_result)
        assert "record_finished_delegate(" in src

    def test_relay_sets_source_context_for_status_tools(self):
        import inspect
        import services._tool_relay_execute as tre
        src = inspect.getsource(tre)
        assert '"delegate_status"' in src
        assert '"delegate_result"' in src
