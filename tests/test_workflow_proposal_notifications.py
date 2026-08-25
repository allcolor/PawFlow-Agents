from core.workflow_proposal_notifications import (
    queue_planner_event,
    resolve_planner_target,
)


def test_planner_target_must_be_an_existing_conversation_member(monkeypatch):
    monkeypatch.setattr(
        "core.conv_agent_config.resolve_agent_config_entry",
        lambda _cid, _name: ("conv", "Planner", {}))
    assert resolve_planner_target("conv", "planner", "alice") == "Planner"

    monkeypatch.setattr(
        "core.conv_agent_config.resolve_agent_config_entry",
        lambda _cid, _name: ("conv", "", {}))
    try:
        resolve_planner_target("conv", "missing", "alice")
    except ValueError as exc:
        assert "not a member" in str(exc)
    else:
        raise AssertionError("missing planner member was accepted")


def test_planner_event_is_stamped_persisted_queued_and_wakes_exact_agent(
    monkeypatch,
):
    written = []
    queued = []
    woken = []

    class Writer:
        def enqueue_message(self, message, **kwargs):
            written.append((message, kwargs))

    class Queue:
        def enqueue(self, message, source=""):
            queued.append((message, source))
            return True

    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: Writer())
    monkeypatch.setattr(
        "core.pending_queue.PendingQueue.for_agent",
        lambda _cid, _agent: Queue())
    monkeypatch.setattr(
        "core.llm_client.stamp_message",
        lambda message, _cid: {**message, "msg_id": "m1", "ts": 1.0})
    monkeypatch.setattr(
        "tasks.ai.agent_loop.AgentLoopTask.wake_agent",
        lambda cid, agent, **kwargs: woken.append((cid, agent, kwargs)))

    message = queue_planner_event({
        "proposal_id": "wp_1", "conversation_id": "conv",
        "created_by": "Planner", "draft_id": "d_1", "draft_revision": 3,
        "definition_digest": "sha256", "state_revision": 4,
        "review_round": 2, "status": "planner_review",
    }, user_id="alice", action="submitted_to_planner",
       comment="Please review", planner_target="Planner")

    assert message["msg_id"] == "m1" and message["ts"] == 1.0
    assert message["source"]["target_agent"] == "Planner"
    assert message["workflow_proposal"]["draft_revision"] == 3
    assert written[0][1] == {"agent_name": "Planner", "user_id": "alice"}
    assert queued[0][1] == "workflow_proposal"
    assert woken[0][0:2] == ("conv", "Planner")
