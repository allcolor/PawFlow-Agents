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
        """new_message SSE event goes to parent conv, not task sub-conv.

        The nudge is persisted to the sub-conv transcript, but the SSE
        event carries cid=parent so the UI renders it in the parent feed.
        """
        h = _make_handler()
        mock_inst = MagicMock()
        mock_inst._active_contexts = {"conv1::task::t_abc:agentA": {}}
        mock_inst._active_contexts_lock = threading.Lock()

        with patch("core.conversation_writer.ConversationWriter.for_conversation") as mock_writer:
            mock_enqueue = mock_writer.return_value.enqueue_message
            h._deliver_to_caller(
                conv_id="conv1::task::t_abc",
                caller_agent="agentA", user_id="user1",
                text="result", msg_id="m1",
                task_id="t1", delegate_agent="B", file_id="f1")
            # Persist call: writer is created for the sub-conv transcript
            assert mock_writer.call_args_list[0].args[0] == "conv1::task::t_abc"
            # SSE event (attached to enqueue) redirects to the parent conv
            sse_events = mock_enqueue.call_args.kwargs["sse_events"]
            assert sse_events[0]["cid"] == "conv1"
            assert sse_events[0]["type"] == "new_message"

    def test_sse_on_same_conv_for_normal_caller(self):
        """For a normal caller, SSE cid matches the writer conv."""
        h = _make_handler()
        with patch("core.conversation_writer.ConversationWriter.for_conversation") as mock_writer:
            mock_enqueue = mock_writer.return_value.enqueue_message
            h._deliver_to_caller(
                conv_id="conv1",
                caller_agent="agentA", user_id="user1",
                text="result", msg_id="m1",
                task_id="t1", delegate_agent="B", file_id="f1")
            assert mock_writer.call_args_list[0].args[0] == "conv1"
            sse_events = mock_enqueue.call_args.kwargs["sse_events"]
            assert sse_events[0]["cid"] == "conv1"
            assert sse_events[0]["type"] == "new_message"


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
             patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"B": {"llm_service": "svc"}}), \
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
             patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"B": {"llm_service": "svc"}}), \
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
             patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"B": {"llm_service": "svc"}}), \
             patch.object(h, "_preempt_caller") as mock_preempt:
            result = h._deliver_shared_delegate(
                from_agent="agentA", to_agent="B",
                message="hello", user_id="user1",
                conv_id="conv1")
            assert result["state"] == "running (preempted)"
            # Direct key → uses main conv, not task sub-conv
            assert mock_preempt.call_args.args[1] == "conv1"


class TestSharedDelegateMembershipGuard:
    """A delegate to an agent that's not in conv_agents must not silently
    enqueue a phantom turn — that used to leave a dangling message in
    the target's ctx and fail downstream in _resolve_agent_client with a
    useless 'no llm_service' error. The guard either auto-registers the
    target from a global definition (if one exists with a service) or
    refuses the delegate with an actionable message."""

    def test_refuses_when_target_not_in_conv_and_no_global_def(self):
        h = _make_handler(conversation_id="conv1")
        with patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"claude": {"llm_service": "svc"}}), \
             patch("core.resource_store.ResourceStore") as mock_rs, \
             patch.object(h, "_is_duplicate_shared_delegate", return_value=False):
            mock_rs.instance.return_value.get_any.return_value = None
            result = h._deliver_shared_delegate(
                from_agent="claude", to_agent="ghost",
                message="hello", user_id="user1",
                conv_id="conv1")
        assert result["state"].startswith("error:")
        assert "ghost" in result["state"]
        assert "claude" in result["state"]  # lists known agents

    def test_auto_registers_from_global_definition_with_service(self):
        h = _make_handler(conversation_id="conv1")
        with patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"claude": {"llm_service": "svc"}}), \
             patch("core.conv_agent_config.add_agent_to_conv") as mock_add, \
             patch("core.resource_store.ResourceStore") as mock_rs, \
             patch.object(h, "_is_duplicate_shared_delegate", return_value=False), \
             patch.object(h, "_wake_caller"), \
             patch("tasks.ai.agent_loop.AgentLoopTask._live_instance",
                   new_callable=PropertyMock,
                   return_value=MagicMock(
                       _active_contexts={},
                       _active_contexts_lock=threading.Lock())), \
             patch("core.conversation_writer.ConversationWriter.for_conversation"):
            mock_rs.instance.return_value.get_any.return_value = {
                "prompt": "...",
                "llm_service": "qwen_llm_service",
            }
            result = h._deliver_shared_delegate(
                from_agent="claude", to_agent="qwen",
                message="hello", user_id="user1",
                conv_id="conv1")
        mock_add.assert_called_once()
        _kwargs = mock_add.call_args.kwargs
        assert _kwargs["llm_service"] == "qwen_llm_service"
        assert result["state"] == "idle (waking)"

    def test_case_insensitive_membership_match(self):
        """conv_agents stored as 'Qwen' but delegate called with 'qwen'
        must match without triggering the guard."""
        h = _make_handler(conversation_id="conv1")
        with patch("core.conv_agent_config.get_all_agent_configs",
                   return_value={"Qwen": {"llm_service": "svc"}}), \
             patch.object(h, "_is_duplicate_shared_delegate", return_value=False), \
             patch.object(h, "_wake_caller"), \
             patch("tasks.ai.agent_loop.AgentLoopTask._live_instance",
                   new_callable=PropertyMock,
                   return_value=MagicMock(
                       _active_contexts={},
                       _active_contexts_lock=threading.Lock())), \
             patch("core.conversation_writer.ConversationWriter.for_conversation"):
            result = h._deliver_shared_delegate(
                from_agent="claude", to_agent="qwen",
                message="hello", user_id="user1",
                conv_id="conv1")
        # Should NOT be an error — case-insensitive match found it
        assert not result["state"].startswith("error:")
