"""Tests for the flash_status tool and its registry state.

Verifies that:
1. register_live_delegate stamps started_at and list_live_delegates returns a
   sanitized, caller-filtered snapshot (no client/task objects)
2. record_finished_delegate keeps a bounded, caller-filtered ring buffer
3. FlashStatusHandler.execute reports live + finished flash agents for the
   calling agent only, excluding non-flash delegates
4. flash_status is registered in the default tool registry and
   FlashAgentHandler records finished results for it
"""
import json

from unittest.mock import MagicMock

from core.agent_executor import (
    AgentTask,
    list_finished_delegates,
    list_live_delegates,
    record_finished_delegate,
    register_live_delegate,
    unregister_live_delegate,
)
from core.handlers.resource_agent import FlashStatusHandler


def _make_status_handler(conversation_id="conv_fs", user_id="user1",
                         source_agent="agentA", llm_service="svc_a"):
    h = FlashStatusHandler()
    h.set_conversation_id(conversation_id)
    h.set_user_id(user_id)
    h.set_source_agent(source_agent, llm_service)
    return h


class TestLiveDelegateSnapshot:
    def test_register_stamps_started_at_and_list_sanitizes(self):
        task = AgentTask(id="tid1", agent_name="agentA::flash::critic",
                         message="m")
        client = MagicMock()
        register_live_delegate("conv_live1", "agentA",
                               "agentA::flash::critic", "tid1", client, task)
        try:
            entries = list_live_delegates("conv_live1", "agentA")
            assert len(entries) == 1
            e = entries[0]
            assert e["target"] == "agentA::flash::critic"
            assert e["task_id"] == "tid1"
            assert e["started_at"] > 0
            assert e["pending_messages"] == 0
            # No live objects leak into the snapshot
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
        # Oldest entries were evicted; the newest survive
        assert len(entries) <= _FINISHED_DELEGATES_MAX
        assert entries[-1]["task_id"] == f"tid{_FINISHED_DELEGATES_MAX + 4}"
        assert len(entries[-1]["error"]) == 500


class TestFlashStatusHandler:
    def test_reports_live_and_finished_flash_agents_only(self):
        conv = "conv_fsh1"
        task = AgentTask(id="tid1", agent_name="agentA::flash::critic",
                         message="m")
        register_live_delegate(conv, "agentA", "agentA::flash::critic",
                               "tid1", MagicMock(), task)
        # Plain (non-flash) delegate must not appear
        register_live_delegate(conv, "agentA", "reviewer", "tid2",
                               MagicMock(), task)
        record_finished_delegate(conv, "agentA", "agentA::flash::done1",
                                 "tid3", "completed", "", 42.0)
        record_finished_delegate(conv, "agentA", "reviewer", "tid4",
                                 "completed")
        try:
            h = _make_status_handler(conversation_id=conv)
            out = json.loads(h.execute({}))
            assert out["counts"] == {"live": 1, "finished": 1}
            live = out["live"][0]
            assert live["name"] == "critic"
            assert live["agent"] == "agentA::flash::critic"
            assert live["task_id"] == "tid1"
            assert live["age_seconds"] >= 0
            fin = out["finished"][0]
            assert fin["name"] == "done1"
            assert fin["status"] == "completed"
            assert fin["duration_ms"] == 42.0
        finally:
            unregister_live_delegate(conv, "agentA",
                                     "agentA::flash::critic", "tid1")
            unregister_live_delegate(conv, "agentA", "reviewer", "tid2")

    def test_other_callers_flash_agents_are_hidden(self):
        conv = "conv_fsh2"
        task = AgentTask(id="tid1", agent_name="agentB::flash::spy",
                         message="m")
        register_live_delegate(conv, "agentB", "agentB::flash::spy",
                               "tid1", MagicMock(), task)
        try:
            h = _make_status_handler(conversation_id=conv,
                                     source_agent="agentA")
            out = json.loads(h.execute({}))
            assert out["counts"] == {"live": 0, "finished": 0}
        finally:
            unregister_live_delegate(conv, "agentB",
                                     "agentB::flash::spy", "tid1")

    def test_error_without_source_agent(self):
        h = FlashStatusHandler()
        h.set_conversation_id("conv_fsh3")
        out = h.execute({})
        assert out.startswith("Error: flash_status")


class TestWiringInvariants:
    def test_flash_status_registered_in_default_registry(self):
        import inspect
        import core.tool_registry as tr
        src = inspect.getsource(tr)
        assert "registry.register(FlashStatusHandler())" in src

    def test_flash_delegate_records_finished_results(self):
        import inspect
        from core.handlers.flash_agent import FlashAgentHandler
        src = inspect.getsource(FlashAgentHandler.execute)
        assert "record_finished_delegate(" in src

    def test_relay_sets_source_context_for_flash_status(self):
        import inspect
        import services._tool_relay_execute as tre
        src = inspect.getsource(tre)
        assert '"flash_status"' in src
