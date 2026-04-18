"""Tests for delegate routing when called from within a task sub-conv.

Verifies that:
1. SpawnAgentsHandler extracts parent conv_id from task sub-conv IDs
2. resolve_agent_task receives parent conv_id (not sub-conv)
3. _deliver_shared_delegate uses parent conv_id for persist + wake/preempt
4. _deliver_to_caller publishes SSE on parent conv
5. Reverse delegate (parent→task agent) finds the agent in task sub-conv
6. source_task_id propagates through AgentTask and SSE events
"""
import json
import threading
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from core.handlers.resource_agent import SpawnAgentsHandler
from core.agent_executor import AgentTask


def _make_handler(conversation_id="conv1", user_id="user1",
                  source_agent="agentA", llm_service="svc_a"):
    h = SpawnAgentsHandler()
    h.set_conversation_id(conversation_id)
    h.set_user_id(user_id)
    h.set_source_agent(source_agent, llm_service)
    h._client_resolver = lambda sid, uid: (MagicMock(), MagicMock())
    h._default_client = MagicMock()
    h._registry = MagicMock()
    h._on_event = MagicMock()
    return h


class TestTaskConvIdExtraction:
    """Step 1: parent conv_id extraction from ::task:: sub-conv."""

    def test_no_task_prefix(self):
        """Normal conv_id passes through unchanged."""
        h = _make_handler(conversation_id="conv1")
        # Trigger execute with a mock that lets us inspect what conv_id is used
        with patch("core.agent_executor.resolve_agent_task") as mock_rat, \
             patch.object(h, "_deliver_shared_delegate", return_value={"state": "ok"}):
            h.execute({"tasks": [{"agent": "B", "message": "hi"}]})
            # shared path calls _deliver_shared_delegate with conv_id=conv1
            call_kw = mock_rat.call_args  # not called for shared
            h._deliver_shared_delegate.assert_called_once()
            args = h._deliver_shared_delegate.call_args
            assert args.kwargs.get("conv_id") == "conv1"

    def test_task_subconv_extracts_parent(self):
        """Task sub-conv ID extracts parent for agent lookups."""
        h = _make_handler(conversation_id="conv1::task::t_abc123")
        with patch.object(h, "_deliver_shared_delegate", return_value={"state": "ok"}):
            h.execute({"tasks": [{"agent": "B", "message": "hi"}]})
            args = h._deliver_shared_delegate.call_args
            # conv_id should be parent, not sub-conv
            assert args.kwargs.get("conv_id") == "conv1"

    def test_isolated_delegate_uses_parent_for_resolve(self):
        """Isolated delegate resolves agent in parent conv, not sub-conv."""
        h = _make_handler(conversation_id="conv1::task::t_abc123")
        with patch("core.agent_executor.resolve_agent_task") as mock_rat, \
             patch("core.agent_executor.get_live_delegate", return_value=None), \
             patch.object(h, "_is_caller_a_delegate", return_value=False):
            mock_task = AgentTask(id="x", agent_name="B", message="hi",
                                 llm_service="svc_b")
            mock_rat.return_value = mock_task
            # mock spawn to avoid actual execution
            with patch("core.agent_executor.SubAgentExecutor") as mock_exec:
                mock_exec.return_value.spawn.return_value = []
                h.execute({"tasks": [{"agent": "B", "message": "hi",
                           "context": "isolated"}]})
            # resolve_agent_task called with parent conv_id
            assert mock_rat.call_args.kwargs["conversation_id"] == "conv1"


class TestSourceTaskIdPropagation:
    """Step 6: source_task_id flows through AgentTask and events."""

    def test_source_task_id_set_on_task(self):
        """AgentTask.source_task_id is set from the sub-conv ID."""
        h = _make_handler(conversation_id="conv1::task::t_abc123")
        with patch("core.agent_executor.resolve_agent_task") as mock_rat, \
             patch("core.agent_executor.get_live_delegate", return_value=None), \
             patch.object(h, "_is_caller_a_delegate", return_value=False):
            mock_task = AgentTask(id="x", agent_name="B", message="hi",
                                 llm_service="svc_b")
            mock_rat.return_value = mock_task
            with patch("core.agent_executor.SubAgentExecutor") as mock_exec:
                mock_exec.return_value.spawn.return_value = []
                h.execute({"tasks": [{"agent": "B", "message": "hi",
                           "context": "isolated"}]})
            assert mock_task.source_task_id == "t_abc123"

    def test_delegate_group_start_includes_source_task_id(self):
        """delegate_group_start SSE event includes source_task_id."""
        h = _make_handler(conversation_id="conv1::task::t_abc123")
        with patch("core.agent_executor.resolve_agent_task") as mock_rat, \
             patch("core.agent_executor.get_live_delegate", return_value=None), \
             patch.object(h, "_is_caller_a_delegate", return_value=False):
            mock_task = AgentTask(id="x", agent_name="B", message="hi",
                                 llm_service="svc_b")
            mock_rat.return_value = mock_task
            h.set_delegate_tc_id("tc_001")
            with patch("core.agent_executor.SubAgentExecutor") as mock_exec:
                mock_exec.return_value.spawn.return_value = []
                h.execute({"tasks": [{"agent": "B", "message": "hi",
                           "context": "isolated"}]})
            # Check delegate_group_start event
            event_calls = [c for c in h._on_event.call_args_list
                           if c.args[0] == "delegate_group_start"]
            assert len(event_calls) == 1
            assert event_calls[0].args[1]["source_task_id"] == "t_abc123"

    def test_no_source_task_id_for_normal_conv(self):
        """source_task_id is empty when not in a task."""
        h = _make_handler(conversation_id="conv1")
        with patch("core.agent_executor.resolve_agent_task") as mock_rat, \
             patch("core.agent_executor.get_live_delegate", return_value=None), \
             patch.object(h, "_is_caller_a_delegate", return_value=False):
            mock_task = AgentTask(id="x", agent_name="B", message="hi",
                                 llm_service="svc_b")
            mock_rat.return_value = mock_task
            h.set_delegate_tc_id("tc_001")
            with patch("core.agent_executor.SubAgentExecutor") as mock_exec:
                mock_exec.return_value.spawn.return_value = []
                h.execute({"tasks": [{"agent": "B", "message": "hi",
                           "context": "isolated"}]})
            assert mock_task.source_task_id == ""


class TestResultDeliverySSE:
    """Step 4: _deliver_to_caller publishes SSE on parent conv."""

    def test_sse_on_parent_conv_for_task_caller(self):
        """display_only SSE event goes to parent conv, not task sub-conv."""
        h = _make_handler()
        mock_inst = MagicMock()
        mock_inst._active_contexts = {"conv1::task::t_abc:agentA": {}}
        mock_inst._active_contexts_lock = threading.Lock()

        with patch("core.conversation_event_bus.ConversationEventBus.instance") as mock_bus:
            mock_pub = mock_bus.return_value.publish_event
            h._deliver_to_caller(
                conv_id="conv1::task::t_abc",
                caller_agent="agentA", user_id="user1",
                text="result", msg_id="m1",
                task_id="t1", delegate_agent="B", file_id="f1")
            # SSE should go to parent conv "conv1", not sub-conv
            sse_call = mock_pub.call_args_list[0]
            assert sse_call.args[0] == "conv1"

    def test_sse_on_same_conv_for_normal_caller(self):
        """display_only SSE event stays on conv_id for normal callers."""
        h = _make_handler()
        with patch("core.conversation_event_bus.ConversationEventBus.instance") as mock_bus:
            mock_pub = mock_bus.return_value.publish_event
            h._deliver_to_caller(
                conv_id="conv1",
                caller_agent="agentA", user_id="user1",
                text="result", msg_id="m1",
                task_id="t1", delegate_agent="B", file_id="f1")
            sse_call = mock_pub.call_args_list[0]
            assert sse_call.args[0] == "conv1"


class TestReverseDelegateScan:
    """Step 5: parent→task agent delegate finds agent in task sub-conv."""

    def test_finds_agent_in_task_subconv(self):
        """_deliver_shared_delegate scans _active_contexts for task keys."""
        h = _make_handler(conversation_id="conv1")
        mock_inst = MagicMock()
        # Agent B is running inside a task, not in the main conv
        mock_inst._active_contexts = {
            "conv1::task::t_abc:B": {"active_agent_name": "B"},
        }
        mock_inst._active_contexts_lock = threading.Lock()

        with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance",
                   new_callable=PropertyMock, return_value=mock_inst), \
             patch.object(h, "_is_duplicate_shared_delegate", return_value=False), \
             patch("core.conversation_writer.ConversationWriter.for_conversation"), \
             patch("core.conversation_event_bus.ConversationEventBus.instance"), \
             patch.object(h, "_preempt_caller") as mock_preempt:
            result = h._deliver_shared_delegate(
                from_agent="agentA", to_agent="B",
                message="hello", user_id="user1",
                conv_id="conv1")
            assert result["state"] == "running (preempted)"
            # Preempt should use the task sub-conv, not parent
            assert mock_preempt.call_args.args[1] == "conv1::task::t_abc"

    def test_falls_back_to_wake_if_not_in_task(self):
        """Agent not in main conv nor task → wakes in main conv."""
        h = _make_handler(conversation_id="conv1")
        mock_inst = MagicMock()
        mock_inst._active_contexts = {}  # empty — agent idle
        mock_inst._active_contexts_lock = threading.Lock()

        with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance",
                   new_callable=PropertyMock, return_value=mock_inst), \
             patch.object(h, "_is_duplicate_shared_delegate", return_value=False), \
             patch("core.conversation_writer.ConversationWriter.for_conversation"), \
             patch("core.conversation_event_bus.ConversationEventBus.instance"), \
             patch.object(h, "_wake_caller") as mock_wake:
            result = h._deliver_shared_delegate(
                from_agent="agentA", to_agent="B",
                message="hello", user_id="user1",
                conv_id="conv1")
            assert result["state"] == "idle (waking)"
            # Wake should use main conv
            assert mock_wake.call_args.args[1] == "conv1"

    def test_direct_key_takes_priority_over_task_scan(self):
        """Agent in main conv is found directly, no scan needed."""
        h = _make_handler(conversation_id="conv1")
        mock_inst = MagicMock()
        mock_inst._active_contexts = {
            "conv1:B": {"active_agent_name": "B"},
            "conv1::task::t_abc:B": {"active_agent_name": "B"},
        }
        mock_inst._active_contexts_lock = threading.Lock()

        with patch("tasks.ai.agent_loop.AgentLoopTask._live_instance",
                   new_callable=PropertyMock, return_value=mock_inst), \
             patch.object(h, "_is_duplicate_shared_delegate", return_value=False), \
             patch("core.conversation_writer.ConversationWriter.for_conversation"), \
             patch("core.conversation_event_bus.ConversationEventBus.instance"), \
             patch.object(h, "_preempt_caller") as mock_preempt:
            result = h._deliver_shared_delegate(
                from_agent="agentA", to_agent="B",
                message="hello", user_id="user1",
                conv_id="conv1")
            assert result["state"] == "running (preempted)"
            # Direct key → uses main conv, not task sub-conv
            assert mock_preempt.call_args.args[1] == "conv1"
