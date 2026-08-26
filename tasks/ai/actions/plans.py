"""AgentLoopTask actions -- plan management.

Plans are stored as individual JSON files via PlanStore.
The orchestrator drives execution: approve → step by step → complete.
Agents only know their current step and call update_plan(done/error).
"""

import json
import logging
import time

from core.plan_store import PlanStore

logger = logging.getLogger(__name__)


LEGACY_PLAN_ACTIONS = {
    "get_plans", "get_plan", "create_plan_user", "approve_plan",
    "reject_plan", "cancel_plan", "delete_plan", "update_plan_step",
    "assign_plan", "cancel_step", "resume_step", "set_plan_verifier",
    "reset_plan", "verify_plan_step", "pause_plan_step",
}


def _get_plan(conv_id, plan_id, user_id):
    """Load a plan from PlanStore."""
    return PlanStore.instance().get(user_id, conv_id, plan_id)


def _save_plan(conv_id, plan, user_id):
    """Save a plan to PlanStore."""
    PlanStore.instance().save(user_id, conv_id, plan)


def _publish(conv_id, event_type, data):
    """Publish SSE event."""
    try:
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(conv_id, event_type, data)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def force_stop_agent(conv_id, agent_name):
    """Force stop an agent — standalone, no self needed."""
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        AgentLoopTask.force_stop_agent(conv_id, agent_name)
    except Exception as e:
        logger.warning("force_stop_agent failed: %s", e)


def _force_stop_agent(self, conv_id, agent_name=""):
    """Force stop an agent (legacy — uses self for CC subprocess kill)."""
    from tasks.ai.agent_loop import AgentLoopTask
    _exec = AgentLoopTask._live_instance or self
    try:
        _exec.cancel_agent(conv_id, agent_name=agent_name, reason="plan_step_done")
        from services.tool_relay_service import ToolRelayService
        ToolRelayService.cancel_agent(conv_id, agent_name)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    # Kill Claude Code subprocess
    try:
        with _exec._active_contexts_lock:
            if agent_name:
                _keys = [f"{conv_id}:{agent_name}"]
            else:
                _keys = [k for k in _exec._active_claude_client
                         if k == conv_id or k.startswith(conv_id + ":")]
            _clients = [(k, _exec._active_claude_client.get(k)) for k in _keys]
        for _k, client in _clients:
            if client and hasattr(client, 'cancel_claude_code'):
                client.cancel_claude_code(force=True)
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)


def _handle_plans(self, action, body, store, user_id, flowfile):
    """Handle plan management actions. Returns [flowfile] or None."""

    if action not in LEGACY_PLAN_ACTIONS:
        return None
    flowfile.set_content(json.dumps({
        "error": "Legacy plans are disabled",
    }).encode())
    flowfile.set_attribute("http.response.status", "404")
    return [flowfile]



def deliver_agent_message(conv_id: str, user_id: str, agent: str, text: str,
                          *, source_extra: dict = None, label: str = "message"):
    """Hand text to an agent exactly as if the user had typed it.

    Persist first, publish second (visible => persisted), then run the same
    streaming entry point a real user message runs. Used by the plan
    orchestrator and by the auto-poke; both need identical semantics, and a
    second implementation would drift.
    """
    import json as _j
    import uuid as _uuid
    from core import FlowFile as _FF
    from core.conversation_writer import ConversationWriter
    from core.llm_client import stamp_message

    _msg_id = _uuid.uuid4().hex[:12]
    _src = {"type": "user", "name": user_id, "target_agent": agent}
    _src.update(source_extra or {})
    _stamped = stamp_message({
        "role": "user", "content": text, "msg_id": _msg_id, "source": _src,
    }, conv_id)
    _sse_evt = {"type": "new_message", "data": {
        "role": "user", "content": text, "msg_id": _msg_id,
        "source": _src, "ts": _stamped.get("ts"),
    }}
    try:
        ConversationWriter.for_conversation(conv_id).enqueue_message(
            dict(_stamped), agent_name=agent or "",
            user_id=user_id, sse_events=[_sse_evt])
    except Exception as e:
        logger.error("%s: persist failed: %s", label, e)
    body = _j.dumps({
        "message": text,
        "conversation_id": conv_id,
        "target_agent": agent,
        "msg_id": _msg_id,
    })
    ff = _FF(body.encode("utf-8"))
    ff.set_attribute("http.auth.principal", user_id)
    # agent_streaming.py must not double-persist this msg_id.
    ff.set_attribute("skip_pre_persist", "1")
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        inst = AgentLoopTask._live_instance
        if inst:
            inst._execute_streaming(ff)
            logger.info("%s sent to agent '%s' (same as user msg)", label, agent)
        else:
            logger.error("%s: no AgentLoopTask instance", label)
    except Exception as e:
        logger.error("%s send failed: %s", label, e)


def orchestrate_next_step(conv_id, plan_id, user_id, delay: int = 10):
    """Find and schedule the next pending step. Standalone — callable from handlers."""
    return _orchestrate_next_step(None, conv_id, plan_id, user_id, delay=delay)


def _orchestrate_next_step(self, conv_id, plan_id, user_id, delay: int = 10):
    """Find and schedule the next pending step.

    The orchestrator:
    1. Finds the first pending (non-skipped, non-paused) step
    2. Validates the assigned agent exists
    3. Marks the step as in_progress
    4. Sends a user message to the agent: "Execute step N: description"
    5. Schedules the agent via PollScheduler

    The agent runs, calls update_plan(done/error), and gets force-stopped.
    Then this function is called again for the next step.
    """
    plan = _get_plan(conv_id, plan_id, user_id)
    if not plan or plan["status"] in ("cancelled", "completed"):
        return

    next_step = None
    for s in plan["steps"]:
        if s["status"] == "pending" and not s.get("paused"):
            next_step = s
            break

    if not next_step:
        # Check if all done
        statuses = [s["status"] for s in plan["steps"]]
        if all(s in ("done", "skipped") for s in statuses):
            plan["status"] = "completed"
            _save_plan(conv_id, plan, user_id)
            _publish(conv_id, "plan_updated", {"plan": plan})
        return

    # Resolve agent
    agent = next_step.get("assigned_to") or plan.get("created_by", "")
    if not agent or agent == "user":
        logger.warning("Plan %s step %d: no agent assigned", plan_id, next_step["index"])
        return

    # Validate agent exists
    try:
        from core.resource_store import ResourceStore
        if not ResourceStore.instance().get_any(
                "agent", agent, user_id, conversation_id=conv_id):
            logger.error("Plan %s step %d: agent '%s' not found — refusing to schedule",
                         plan_id, next_step["index"], agent)
            return
    except Exception as e:
        logger.error("Plan %s step %d: agent validation failed: %s",
                     plan_id, next_step["index"], e)
        return

    # Mark step in_progress and plan in_progress
    next_step["status"] = "in_progress"
    if plan["status"] in ("approved",):
        plan["status"] = "in_progress"
    _save_plan(conv_id, plan, user_id)
    _publish(conv_id, "plan_updated", {"plan": plan})

    # Send step instruction exactly like a user message — same pipeline,
    # same code path. No store write, no poller, no special handling.
    total = len(plan["steps"])
    step_num = next_step["index"]
    # Inform about previous step completion (so CC doesn't re-execute interrupted update_plan)
    _prev_info = ""
    if step_num > 1:
        _prev = next((s for s in plan["steps"] if s["index"] == step_num - 1), None)
        if _prev and _prev["status"] in ("done", "skipped"):
            _prev_info = (
                f"Step {step_num - 1}/{total} completed successfully. "
                f"Your update_plan call was received and processed. "
                f"Do NOT re-call update_plan for step {step_num - 1}.\n\n"
            )
    _user_msg = (
        f"{_prev_info}"
        f"Execute step {step_num}/{total}: {next_step['description']}\n\n"
        f"Plan: {plan_id}\n"
        f"When done, call:\n"
        f"  update_plan(plan_id=\"{plan_id}\", updates=[{{\"step\": {step_num}, "
        f"\"status\": \"done\", \"note\": \"what you did\"}}])\n"
        f"If the step fails, set status to \"error\" with a note explaining why.\n"
        f"Do NOT skip ahead to other steps."
    )

    def _send_step():
        deliver_agent_message(conv_id, user_id, agent, _user_msg,
                              source_extra={"plan_id": plan_id},
                              label=f"Plan {plan_id} step {step_num}")

    if delay > 0:
        import threading
        def _delayed_send():
            time.sleep(delay)
            _send_step()
        threading.Thread(target=_delayed_send, daemon=True,
                         name=f"plan-step-{plan_id}-{step_num}").start()
    else:
        _send_step()


def _stop_plan_agents(self, conv_id, plan):
    """Force stop all agents working on in_progress steps of this plan."""
    for s in plan["steps"]:
        if s["status"] == "in_progress":
            agent = s.get("assigned_to", "")
            if agent:
                _force_stop_agent(self, conv_id, agent)
            s["status"] = "pending"
