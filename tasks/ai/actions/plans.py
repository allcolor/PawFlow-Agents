"""AgentLoopTask actions -- plan management.

Plans are stored as individual JSON files via PlanStore.
The orchestrator drives execution: approve → step by step → complete.
Agents only know their current step and call update_plan(done/error).
"""

import json
import logging
import time

from core import FlowFile
from core.plan_store import PlanStore

logger = logging.getLogger(__name__)


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
        pass


def force_stop_agent(conv_id, agent_name):
    """Force stop an agent — standalone, no self needed."""
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        AgentLoopTask.force_stop_agent(conv_id, agent_name)
    except Exception as e:
        logger.warning("force_stop_agent failed: %s", e)


def _force_stop_agent(self, conv_id, agent_name=""):
    """Force stop an agent (legacy — uses self for CC subprocess kill)."""
    try:
        self.cancel_agent(conv_id, agent_name=agent_name)
        from services.tool_relay_service import ToolRelayService
        ToolRelayService.cancel_agent(conv_id, agent_name)
    except Exception:
        pass
    # Kill Claude Code subprocess
    try:
        with self._active_contexts_lock:
            if agent_name:
                _keys = [f"{conv_id}:{agent_name}"]
            else:
                _keys = [k for k in self._active_claude_client
                         if k == conv_id or k.startswith(conv_id + ":")]
            _clients = [(k, self._active_claude_client.get(k)) for k in _keys]
        for _k, client in _clients:
            if client and hasattr(client, 'cancel_claude_code'):
                client.cancel_claude_code(force=True)
    except Exception:
        pass


def _handle_plans(self, action, body, store, user_id, flowfile):
    """Handle plan management actions. Returns [flowfile] or None."""

    conv_id = body.get("conversation_id", "")
    ps = PlanStore.instance()

    # ── List plans ──
    if action == "get_plans":
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        # Migrate from extras on first access
        PlanStore.migrate_from_extras(conv_id, user_id, store)
        plans = ps.list_plans(user_id, conv_id)
        flowfile.set_content(json.dumps({"plans": plans}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Get single plan ──
    if action == "get_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Create plan (from user UI) ──
    if action == "create_plan_user":
        title = body.get("title", "")
        steps = body.get("steps", [])
        assigned_to = body.get("assigned_to", "")  # plan-level agent
        if not conv_id or not title or not steps:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id, title, or steps"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        import uuid
        plan_id = "p_" + uuid.uuid4().hex[:8]
        built_steps = []
        for i, s in enumerate(steps):
            if isinstance(s, str):
                built_steps.append({
                    "index": i + 1, "description": s,
                    "status": "pending", "note": "", "assigned_to": "",
                })
            else:
                built_steps.append({
                    "index": i + 1,
                    "description": s.get("description", ""),
                    "status": "pending", "note": "",
                    "assigned_to": s.get("assigned_to", ""),
                })
        # Auto-approve only if at least one agent is assigned
        has_agent = bool(assigned_to) or any(s["assigned_to"] for s in built_steps)
        plan = {
            "id": plan_id,
            "conversation_id": conv_id,
            "title": title,
            "status": "approved" if has_agent else "pending_approval",
            "created_by": assigned_to or user_id,
            "created_at": time.time(),
            "assigned_to": [assigned_to] if assigned_to else [],
            "steps": built_steps,
        }
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_created", {"plan": plan})
        # Auto-orchestrate if approved
        if plan["status"] == "approved":
            _orchestrate_next_step(self, conv_id, plan_id, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Approve plan → launch orchestrator ──
    if action == "approve_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if plan["status"] not in ("pending_approval",):
            flowfile.set_content(json.dumps({"error": f"Plan is already {plan['status']}"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan["status"] = "approved"
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        # Launch orchestrator — does NOT call LLM, just schedules first step
        _orchestrate_next_step(self, conv_id, plan_id, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Reject plan ──
    if action == "reject_plan":
        plan_id = body.get("plan_id", "")
        reason = body.get("reason", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        plan["status"] = "cancelled"
        if reason:
            plan["rejection_reason"] = reason
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Cancel plan → force stop + stop orchestration ──
    if action == "cancel_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Force stop any agent working on a step
        _stop_plan_agents(self, conv_id, plan)
        plan["status"] = "cancelled"
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Delete plan → cancel + delete file ──
    if action == "delete_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if plan:
            _stop_plan_agents(self, conv_id, plan)
        ps.delete(user_id, conv_id, plan_id)
        _publish(conv_id, "plan_deleted", {"plan_id": plan_id})
        flowfile.set_content(json.dumps({"deleted": True}).encode())
        return [flowfile]

    # ── Update plan step (called by agent: done/error only) ──
    if action == "update_plan_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        status = body.get("status", "")
        note = body.get("note", "")
        if not conv_id or not plan_id or not step or not status:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if status not in ("done", "error", "skipped"):
            flowfile.set_content(json.dumps({"error": "Status must be done, error, or skipped"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Update the step
        step_agent = ""
        for s in plan["steps"]:
            if s["index"] == step:
                s["status"] = status
                if note:
                    s["note"] = note
                step_agent = s.get("assigned_to", "")
                break
        # Check if all done
        statuses = [s["status"] for s in plan["steps"]]
        if all(s in ("done", "skipped") for s in statuses):
            plan["status"] = "completed"
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        # Force stop the agent that was working on this step
        if step_agent and status in ("done", "error", "skipped"):
            _force_stop_agent(self, conv_id, step_agent)
        # Orchestrate next step (if not completed and status progresses the plan)
        if status in ("done", "skipped") and plan["status"] != "completed":
            _orchestrate_next_step(self, conv_id, plan_id, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Assign agent to steps ──
    if action == "assign_plan":
        plan_id = body.get("plan_id", "")
        agent = body.get("agent", "")
        step_range = body.get("step_range", "")
        if not conv_id or not plan_id or not agent:
            flowfile.set_content(json.dumps({"error": "Missing plan_id or agent"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if plan["status"] == "cancelled":
            flowfile.set_content(json.dumps({"error": "Cannot assign a cancelled plan"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if agent not in plan.get("assigned_to", []):
            plan.setdefault("assigned_to", []).append(agent)
        reassignable = ("pending", "error")
        if step_range == "remaining":
            for s in plan["steps"]:
                if s["status"] in reassignable:
                    s["assigned_to"] = agent
        elif step_range:
            target_steps = []
            for part in step_range.split(","):
                part = part.strip()
                if "-" in part:
                    a, b = part.split("-", 1)
                    target_steps.extend(range(int(a), int(b) + 1))
                else:
                    target_steps.append(int(part))
            for s in plan["steps"]:
                if s["index"] in target_steps and s["status"] in reassignable:
                    s["assigned_to"] = agent
        else:
            for s in plan["steps"]:
                if not s.get("assigned_to") and s["status"] in reassignable:
                    s["assigned_to"] = agent
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Cancel step → force stop + reset + stop orchestration ──
    if action == "cancel_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        if not conv_id or not plan_id or not step:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        for s in plan["steps"]:
            if s["index"] == step:
                agent = s.get("assigned_to", "")
                if agent and s["status"] == "in_progress":
                    _force_stop_agent(self, conv_id, agent)
                s["status"] = "pending"
                break
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Resume step → relaunch orchestrator from this step ──
    if action == "resume_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        if not conv_id or not plan_id or not step:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Ensure the step is pending
        target = None
        for s in plan["steps"]:
            if s["index"] == step:
                target = s
                break
        if not target or target["status"] != "pending":
            flowfile.set_content(json.dumps({"error": "Step must be pending to resume"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if plan["status"] == "cancelled":
            plan["status"] = "approved"
            _save_plan(conv_id, plan, user_id)
        _orchestrate_next_step(self, conv_id, plan_id, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Set verifier ──
    if action == "set_plan_verifier":
        plan_id = body.get("plan_id", "")
        verifier = body.get("verifier", "")
        step = int(body.get("step", 0))
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if step > 0:
            for s in plan["steps"]:
                if s["index"] == step:
                    s["verifier"] = verifier
                    break
        else:
            plan["verifier"] = verifier
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Reset plan ──
    if action == "reset_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Force stop any running agents first
        _stop_plan_agents(self, conv_id, plan)
        plan["status"] = "pending_approval"
        for s in plan["steps"]:
            s["status"] = "pending"
            s["note"] = ""
            s.pop("verified_by", None)
            s.pop("rejected_by", None)
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Verify step ──
    if action == "verify_plan_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        approved = body.get("approved", False)
        reason = body.get("reason", "")
        if not conv_id or not plan_id or not step:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        target_step = None
        for s in plan["steps"]:
            if s["index"] == step:
                target_step = s
                break
        if not target_step:
            flowfile.set_content(json.dumps({"error": f"Step {step} not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if target_step["status"] != "pending_verification":
            flowfile.set_content(json.dumps({"error": f"Step is '{target_step['status']}'"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if approved:
            target_step["status"] = "done"
            target_step["verified_by"] = user_id or "user"
            if reason:
                target_step["note"] = (target_step.get("note", "") + f" [verified: {reason}]").strip()
            statuses = [s["status"] for s in plan["steps"]]
            if all(s in ("done", "skipped") for s in statuses):
                plan["status"] = "completed"
        else:
            target_step["status"] = "pending"
            target_step["rejected_by"] = user_id or "user"
            if reason:
                target_step["note"] = (target_step.get("note", "") + f" [rejected: {reason}]").strip()
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        # Continue orchestration if approved and not completed
        if approved and plan["status"] != "completed":
            _orchestrate_next_step(self, conv_id, plan_id, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    # ── Pause step ──
    if action == "pause_plan_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        paused = body.get("paused", True)
        if not conv_id or not plan_id or not step:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan = _get_plan(conv_id, plan_id, user_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        for s in plan["steps"]:
            if s["index"] == step:
                if paused and s["status"] == "in_progress":
                    # Pausing a running step: force stop + revert to pending
                    agent = s.get("assigned_to", "")
                    if agent:
                        _force_stop_agent(self, conv_id, agent)
                    s["status"] = "pending"
                s["paused"] = bool(paused)
                break
        _save_plan(conv_id, plan, user_id)
        _publish(conv_id, "plan_updated", {"plan": plan})
        # If unpausing, resume orchestration
        if not paused and plan["status"] in ("in_progress", "approved"):
            _orchestrate_next_step(self, conv_id, plan_id, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    return None


# ── Orchestrator ──────────────────────────────────────────────────────

def orchestrate_next_step(conv_id, plan_id, user_id):
    """Find and schedule the next pending step. Standalone — callable from handlers."""
    return _orchestrate_next_step(None, conv_id, plan_id, user_id)


def _orchestrate_next_step(self, conv_id, plan_id, user_id):
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
        if not ResourceStore.instance().get_any("agent", agent, user_id):
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

    # Send step instruction as a REAL user message — this IS what triggers the agent.
    # The message goes into the conv like any user message: transcript + shared + agent contexts.
    # The poller just wakes the agent — the instruction is already in the context.
    total = len(plan["steps"])
    step_num = next_step["index"]
    _user_msg = (
        f"Execute step {step_num}/{total}: {next_step['description']}\n\n"
        f"Plan: {plan_id}\n"
        f"When done, call:\n"
        f"  update_plan(plan_id=\"{plan_id}\", updates=[{{\"step\": {step_num}, "
        f"\"status\": \"done\", \"note\": \"what you did\"}}])\n"
        f"If the step fails, set status to \"error\" with a note explaining why.\n"
        f"Do NOT skip ahead to other steps."
    )
    try:
        from core.conversation_writer import ConversationWriter
        import uuid as _uuid_plan
        _msg_id = _uuid_plan.uuid4().hex[:12]
        ConversationWriter.for_conversation(conv_id).enqueue(
            [{"role": "user", "content": _user_msg,
              "msg_id": _msg_id, "ts": time.time(),
              "source": {"type": "user", "name": user_id,
                         "target_agent": agent, "plan_id": plan_id}}],
            user_id=user_id)
    except Exception as e:
        logger.warning("Failed to write plan step user message: %s", e)

    # Wake the agent — the instruction is already in the context from the message above
    try:
        from core.poll_scheduler import PollScheduler
        PollScheduler.instance().schedule_delay(
            conv_id, 0,
            key=f"{conv_id}::plan::{plan_id}::step{step_num}::{agent}",
            reason=f"[plan_step:{plan_id}:{step_num}] ({agent})",
            user_id=user_id,
        )
        logger.info("Plan %s step %d scheduled for agent '%s'",
                     plan_id, step_num, agent)
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.wake_poller()
        except Exception:
            pass
    except Exception as e:
        logger.warning("Failed to schedule plan step: %s", e)


def _stop_plan_agents(self, conv_id, plan):
    """Force stop all agents working on in_progress steps of this plan."""
    for s in plan["steps"]:
        if s["status"] == "in_progress":
            agent = s.get("assigned_to", "")
            if agent:
                _force_stop_agent(self, conv_id, agent)
            s["status"] = "pending"
