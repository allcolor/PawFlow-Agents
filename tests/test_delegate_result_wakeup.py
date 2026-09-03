"""Delegate-result delivery cannot strand a message during caller teardown."""

from core.handlers._spawn_delivery import _SpawnDeliveryMixin
from core.pending_queue import PendingQueue
from tasks.ai.agent_loop import AgentLoopTask


def test_preempt_enqueue_always_schedules_a_race_safe_wake(monkeypatch):
    """The caller may become idle between the active check and the enqueue."""
    queued = []
    wakes = []

    class Queue:
        def enqueue(self, message, source=""):
            queued.append((message, source))

    def wake(cls, conversation_id, agent_name, **kwargs):
        wakes.append((conversation_id, agent_name, kwargs))

    monkeypatch.setattr(
        PendingQueue, "for_agent", staticmethod(lambda _cid, _agent: Queue()))
    monkeypatch.setattr(AgentLoopTask, "wake_agent", classmethod(wake))

    _SpawnDeliveryMixin._preempt_caller(
        object(), "conv1", "assistant", "delegate finished", "msg1",
        {"type": "agent_delegate"}, user_id="alice")

    assert queued[0][0]["content"] == "delegate finished"
    assert queued[0][0]["msg_id"] == "msg1"
    assert queued[0][1] == "delegate_reply"
    assert wakes == [("conv1", "assistant", {
        "reason": "[delegate_reply] queued result for assistant",
        "user_id": "alice",
        "delay": 0.0,
        "even_if_active": True,
    })]
