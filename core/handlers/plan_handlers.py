"""Plan tool handlers — create, update, approve, assign, cancel, delete, verify."""

import json
import logging
import threading
import time
import uuid
from typing import Dict, Any

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

class _PlanHandlerBase(ToolHandler):
    """Base for plan handlers — provides conversation_id, agent_name, user_id."""

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""
        self._user_id = ""

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def set_user_id(self, uid: str):
        self._user_id = uid


class CreatePlanHandler(_PlanHandlerBase):
    """Create a structured plan for a multi-step task."""

    @property
    def name(self) -> str:
        return "create_plan"

    @property
    def description(self) -> str:
        return (
            "Create a structured plan for a multi-step task. Each step has a "
            "description and status. The plan requires user approval before execution. "
            "Use assign_plan to assign it to agents after approval.\n"
            "WORKFLOW: 1) create_plan → 2) user approves → 3) work on steps, calling update_plan "
            "to mark each step in_progress then done as you go → 4) post a final recap message "
            "summarizing what was accomplished when all steps are done."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Plan title (short summary of the goal)",
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                        },
                        "required": ["description"],
                    },
                    "description": "List of plan steps",
                },
            },
            "required": ["title", "steps"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time
        title = arguments.get("title", "")
        steps = arguments.get("steps", [])
        if not title or not steps:
            return "Error: title and steps are required"

        plan_id = f"p_{uuid.uuid4().hex[:8]}"
        plan = {
            "id": plan_id,
            "title": title,
            "status": "pending_approval",
            "created_by": self._agent_name,
            "created_at": time.time(),
            "assigned_to": [],
            "verifier": "",
            "steps": [
                {
                    "index": i + 1,
                    "description": s.get("description", ""),
                    "status": "pending",
                    "paused": False,
                    "note": "",
                    "task_id": "",
                    "assigned_to": "",
                    "verifier": "",
                }
                for i, s in enumerate(steps)
            ],
        }

        if self._conversation_id:
            plan["conversation_id"] = self._conversation_id
            try:
                from core.plan_store import PlanStore
                PlanStore.instance().save(self._user_id, self._conversation_id, plan)
                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        self._conversation_id, "plan_created", {"plan": plan})
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Failed to persist plan: {e}")

        lines = [f"Plan '{plan_id}' created: **{title}** ({len(steps)} steps)"]
        lines.append("Status: pending_approval \u2014 waiting for user to approve.")
        for s in plan["steps"]:
            lines.append(f"  \u25cb {s['index']}. {s['description']}")
        return "\n".join(lines)

class UpdatePlanHandler(_PlanHandlerBase):
    """Update the status of steps in a plan."""

    @property
    def name(self) -> str:
        return "update_plan"

    @property
    def description(self) -> str:
        return (
            "Update the status of one or more steps in a plan. "
            "Call this as you complete steps to show progress to the user.\n"
            "IMPORTANT: Update steps in real-time as you work — mark in_progress when starting "
            "a step, then done when finished. Do NOT batch all updates at the end. "
            "The user sees progress live."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {
                    "type": "string",
                    "description": "Plan ID (e.g. p_abc12345)",
                },
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "integer", "description": "Step number (1-based)"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "skipped", "error"],
                            },
                            "note": {"type": "string", "description": "Optional note"},
                        },
                        "required": ["step", "status"],
                    },
                },
            },
            "required": ["plan_id", "updates"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        updates = arguments.get("updates", [])
        if not plan_id or not updates:
            return "Error: plan_id and updates are required"

        if not self._conversation_id:
            return "Error: no conversation context"

        from core.plan_store import PlanStore
        plan = PlanStore.instance().get(self._user_id, self._conversation_id, plan_id)
        if not plan:
            return f"Error: plan '{plan_id}' not found."

        _changed_steps = set()  # steps that ACTUALLY changed status
        for u in updates:
            step_num = int(u.get("step") or u.get("index") or 0)
            if step_num == 0:
                continue
            status = u.get("status", "")
            note = u.get("note", "")
            # Agent can only set done or error
            if status not in ("done", "error"):
                continue
            for s in plan["steps"]:
                if s["index"] == step_num:
                    # Only the CURRENT step (in_progress) can be updated
                    if s["status"] != "in_progress":
                        break
                    if status == "done":
                        verifier = (s.get("verifier") or
                                    plan.get("verifier", ""))
                        if verifier:
                            s["status"] = "pending_verification"
                            if note:
                                s["note"] = note
                            _changed_steps.add(step_num)
                            break
                    s["status"] = status
                    if note:
                        s["note"] = note
                    _changed_steps.add(step_num)
                    break

        # Auto-update plan status
        statuses = [s["status"] for s in plan["steps"]]
        if all(s in ("done", "skipped") for s in statuses):
            plan["status"] = "completed"

        # Persist to file (no JSONL duplication)
        PlanStore.instance().save(self._user_id, self._conversation_id, plan)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "plan_updated", {"plan": plan})
        except Exception:
            pass

        # Format response for the agent
        done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
        total = len(plan["steps"])
        lines = [f"**{plan['title']}** — {done_count}/{total} done [{plan['status']}]"]
        for s in plan["steps"]:
            icon = {"pending": "\u25cb", "in_progress": "\u25d4", "done": "\u2713",
                    "skipped": "\u2013", "error": "\u2717",
                    "pending_verification": "\u2690"}.get(s["status"], "\u25cb")
            note_str = f' \u2014 {s["note"]}' if s.get("note") else ""
            lines.append(f"  {icon} {s['index']}. {s['description']}{note_str}")

        # Tell the agent to stop — the orchestrator handles next steps
        lines.append("\nSTOP here. The orchestrator will handle the next step.")
        if plan["status"] == "completed":
            lines.append("Plan completed.")

        # Only act if something ACTUALLY changed (prevents cascade from re-sent updates)
        if not _changed_steps:
            return "\n".join(lines)

        # Check what changed for orchestration decisions
        _needs_orchestrate = False
        _needs_verify = []
        for sn in _changed_steps:
            _step_obj = next((s for s in plan["steps"] if s["index"] == sn), None)
            if not _step_obj:
                continue
            if _step_obj["status"] == "done":
                _needs_orchestrate = True
            elif _step_obj["status"] == "pending_verification":
                _v = _step_obj.get("verifier") or plan.get("verifier", "")
                if _v:
                    _needs_verify.append((sn, _v, self._agent_name))

        # Force stop the agent — step is done/error, agent must not continue
        _has_terminal = any(
            next((s for s in plan["steps"] if s["index"] == sn), {}).get("status", "")
            in ("done", "error", "pending_verification")
            for sn in _changed_steps
        )
        if _has_terminal and self._agent_name and self._conversation_id:
            from tasks.ai.actions.plans import force_stop_agent
            force_stop_agent(self._conversation_id, self._agent_name)

        # Orchestrate next step / schedule verifiers
        _uid = self._user_id
        if plan["status"] != "completed":
            if _needs_orchestrate:
                from tasks.ai.actions.plans import orchestrate_next_step
                orchestrate_next_step(self._conversation_id, plan_id, _uid)
            for _step_n, _verifier, _executor in _needs_verify:
                try:
                    from core.poll_scheduler import PollScheduler
                    PollScheduler.instance().schedule_delay(
                        self._conversation_id, 0,
                        key=f"{self._conversation_id}::plan::{plan_id}::verify{_step_n}::{_verifier}",
                        reason=f"[plan_verify:{plan_id}:{_step_n}:{_executor}] ({_verifier})",
                        user_id=_uid,
                    )
                    from tasks.ai.agent_loop import AgentLoopTask
                    AgentLoopTask.wake_poller()
                except Exception as e:
                    logger.warning("Plan post-verify schedule failed: %s", e)
            for _step_n, _verifier, _executor in _needs_verify:
                def _post_verify(sn=_step_n, vf=_verifier, ex=_executor):
                    try:
                        from core.poll_scheduler import PollScheduler
                        PollScheduler.instance().schedule_delay(
                            self._conversation_id, 0,
                            key=f"{self._conversation_id}::plan::{plan_id}::verify{sn}::{vf}",
                            reason=f"[plan_verify:{plan_id}:{sn}:{ex}] ({vf})",
                            user_id="",
                        )
                        from tasks.ai.agent_loop import AgentLoopTask
                        AgentLoopTask.wake_poller()
                    except Exception as e:
                        logger.warning("Plan post-verify schedule failed: %s", e)
                threading.Thread(target=_post_verify, daemon=True).start()

        return "\n".join(lines)


class ApprovePlanHandler(_PlanHandlerBase):
    """Approve a plan (agent can approve plans created by other agents)."""

    @property
    def name(self) -> str:
        return "approve_plan"

    @property
    def description(self) -> str:
        return (
            "Approve a plan that is currently in pending_approval status, advancing it\n"
            "to the approved state so that the orchestrator can begin execution.\n\n"
            "This is the gate between planning and doing: a plan created by one agent\n"
            "must be approved before any steps run. Only approve plans you did NOT\n"
            "create yourself -- this enforces a review boundary.\n\n"
            "Parameters:\n"
            "  plan_id  -- the plan ID returned by create_plan (e.g. p_abc12345).\n\n"
            "After approval the orchestrator picks the first pending step, assigns it\n"
            "to the designated agent, and starts execution automatically. You do not\n"
            "need to call assign_plan separately unless you want to reassign steps.\n\n"
            "Use this when: the user explicitly approves, or when a reviewer agent\n"
            "validates the plan structure and decides it is ready."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to approve"},
            },
            "required": ["plan_id"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        if not plan_id or not self._conversation_id:
            return "Error: plan_id required"
        try:
            from core.plan_store import PlanStore
            plan = PlanStore.instance().get(self._user_id, self._conversation_id, plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"
            if plan["status"] != "pending_approval":
                return f"Plan is already {plan['status']}"
            plan["status"] = "approved"
            PlanStore.instance().save(self._user_id, self._conversation_id, plan)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_updated", {"plan": plan})
            except Exception:
                pass
            return f"Plan '{plan_id}' approved. Orchestrator will handle execution."
        except Exception as e:
            return f"Error: {e}"


class AssignPlanHandler(_PlanHandlerBase):
    """Assign a plan to an agent, creating tasks for execution."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "assign_plan"

    @property
    def description(self) -> str:
        return (
            "Assign a plan to an agent for execution. Implies approval if pending. "
            "By default assigns unassigned steps. Use step_range for specific steps "
            "(e.g. '1-3', '2,4,5') or 'remaining' for all non-completed steps. "
            "Can reassign steps that are pending, in_progress, or error."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to assign"},
                "agent": {"type": "string", "description": "Agent name or ALL"},
                "step_range": {
                    "type": "string",
                    "description": "Optional step range (e.g. '1-3', '2,4,5'). If omitted, assigns full plan as one task.",
                },
            },
            "required": ["plan_id", "agent"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        agent = arguments.get("agent", "")
        step_range = arguments.get("step_range", "")
        if not plan_id or not agent or not self._conversation_id:
            return "Error: plan_id and agent required"

        try:
            from core.plan_store import PlanStore
            plan = PlanStore.instance().get(self._user_id, self._conversation_id, plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"
            if plan["status"] == "cancelled":
                return "Error: cannot assign a cancelled plan"
            if plan["status"] == "pending_approval":
                plan["status"] = "approved"
            plan["status"] = "in_progress"
            if agent not in plan.get("assigned_to", []):
                plan.setdefault("assigned_to", []).append(agent)

            reassignable = ("pending", "in_progress", "error")
            assigned_count = 0
            if step_range == "remaining":
                for s in plan["steps"]:
                    if s["status"] in reassignable:
                        s["assigned_to"] = agent
                        assigned_count += 1
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
                        assigned_count += 1
            else:
                for s in plan["steps"]:
                    if not s.get("assigned_to") and s["status"] in reassignable:
                        s["assigned_to"] = agent
                        assigned_count += 1

            PlanStore.instance().save(self._user_id, self._conversation_id, plan)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_updated", {"plan": plan})
            except Exception:
                pass
            mode = f"steps {step_range}" if step_range else "full plan"
            return f"Plan '{plan_id}' assigned to {agent} ({mode}, {assigned_count} steps)."
        except Exception as e:
            return f"Error: {e}"


class CancelPlanHandler(_PlanHandlerBase):
    """Cancel a plan."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "cancel_plan"

    @property
    def description(self) -> str:
        return (
            "Cancel a plan, halting any further step orchestration.\n\n"
            "Sets the plan status to 'cancelled'. Steps that are already in_progress\n"
            "on a running agent may still finish their current work, but the\n"
            "orchestrator will NOT start any new steps after cancellation.\n\n"
            "Parameters:\n"
            "  plan_id  -- the plan ID to cancel (e.g. p_abc12345).\n\n"
            "Use this when: the user asks to abort, the plan is no longer relevant,\n"
            "or a critical error makes remaining steps pointless. Cancellation is\n"
            "permanent -- to resume work, create a new plan.\n\n"
            "A cancelled plan is still visible in the conversation for reference.\n"
            "To remove it entirely, use delete_plan instead."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to cancel"},
            },
            "required": ["plan_id"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        if not plan_id or not self._conversation_id:
            return "Error: plan_id required"
        try:
            from core.plan_store import PlanStore
            plan = PlanStore.instance().get(self._user_id, self._conversation_id, plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"
            plan["status"] = "cancelled"
            PlanStore.instance().save(self._user_id, self._conversation_id, plan)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_updated", {"plan": plan})
            except Exception:
                pass
            return f"Plan '{plan_id}' cancelled."
        except Exception as e:
            return f"Error: {e}"


class DeletePlanHandler(_PlanHandlerBase):
    """Delete a plan permanently."""

    def __init__(self):
        self._conversation_id = ""

    @property
    def name(self) -> str:
        return "delete_plan"

    @property
    def description(self) -> str:
        return (
            "Permanently delete a plan and all its step data from the conversation.\n\n"
            "Unlike cancel_plan (which keeps the plan visible for reference), this\n"
            "removes the plan file from disk entirely. The UI will no longer show it.\n\n"
            "Parameters:\n"
            "  plan_id  -- the plan ID to delete (e.g. p_abc12345).\n\n"
            "Use this when: the plan was created by mistake, is a duplicate, or the\n"
            "user explicitly asks to clean up old plans. Prefer cancel_plan when you\n"
            "want to stop execution but keep the history.\n\n"
            "WARNING: This is irreversible. Any in-progress agents working on steps\n"
            "of this plan will lose their plan context on next update_plan call."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID to delete"},
            },
            "required": ["plan_id"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        if not plan_id or not self._conversation_id:
            return "Error: plan_id required"
        try:
            from core.plan_store import PlanStore
            PlanStore.instance().delete(self._user_id, self._conversation_id, plan_id)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "plan_deleted", {"plan_id": plan_id})
            except Exception:
                pass
            return f"Plan '{plan_id}' deleted."
        except Exception as e:
            return f"Error: {e}"


class VerifyPlanStepHandler(_PlanHandlerBase):
    """Verify (approve or reject) a plan step that is pending verification."""

    def __init__(self):
        self._conversation_id = ""
        self._agent_name = ""

    @property
    def name(self) -> str:
        return "verify_plan_step"

    @property
    def description(self) -> str:
        return (
            "Approve or reject a plan step that is pending verification. "
            "If approved, the step is marked done and the next step is triggered. "
            "If rejected, the step is sent back to the executor for rework."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID"},
                "step": {"type": "integer", "description": "Step number (1-based)"},
                "approved": {"type": "boolean", "description": "True to approve, false to reject"},
                "reason": {"type": "string", "description": "Reason for approval/rejection"},
            },
            "required": ["plan_id", "step", "approved"],
        }

    def set_conversation_id(self, cid: str):
        self._conversation_id = cid

    def set_agent_name(self, name: str):
        self._agent_name = name

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan_id = arguments.get("plan_id", "")
        step_num = int(arguments.get("step", 0))
        approved = arguments.get("approved", False)
        reason = arguments.get("reason", "")

        if not plan_id or not step_num or not self._conversation_id:
            return "Error: plan_id, step, and approved are required"

        try:
            from core.plan_store import PlanStore
            plan = PlanStore.instance().get(self._user_id, self._conversation_id, plan_id)
            if not plan:
                return f"Error: plan '{plan_id}' not found"

            step = None
            for s in plan["steps"]:
                if s["index"] == step_num:
                    step = s
                    break
            if not step:
                return f"Error: step {step_num} not found in plan '{plan_id}'"

            if step["status"] != "pending_verification":
                return f"Error: step {step_num} is '{step['status']}', not pending_verification"

            if approved:
                step["status"] = "done"
                step["verified_by"] = self._agent_name
                if reason:
                    step["note"] = (step.get("note", "") + f" [verified: {reason}]").strip()

                statuses = [s["status"] for s in plan["steps"]]
                if all(s in ("done", "skipped") for s in statuses):
                    plan["status"] = "completed"

                PlanStore.instance().save(self._user_id, self._conversation_id, plan)

                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        self._conversation_id, "plan_updated", {"plan": plan})
                except Exception:
                    pass

                # Orchestrate next step if plan not completed
                if plan["status"] != "completed":
                    import threading
                    def _post_orchestrate_verify():
                        try:
                            from tasks.ai.actions.plans import orchestrate_next_step
                            orchestrate_next_step(
                                self._conversation_id, plan_id,
                                self._agent_name)
                        except Exception as e:
                            logger.warning("Plan verify post-orchestrate failed: %s", e)
                    threading.Thread(target=_post_orchestrate_verify, daemon=True).start()

                return (
                    f"Step {step_num} approved."
                    + (f" Reason: {reason}" if reason else "")
                    + (f" Plan completed!" if plan["status"] == "completed" else "")
                )
            else:
                step["status"] = "pending"
                step["rejected_by"] = self._agent_name
                if reason:
                    step["note"] = (step.get("note", "") + f" [rejected: {reason}]").strip()

                PlanStore.instance().save(self._user_id, self._conversation_id, plan)

                try:
                    from core.conversation_event_bus import ConversationEventBus
                    ConversationEventBus.instance().publish_event(
                        self._conversation_id, "plan_updated", {"plan": plan})
                except Exception:
                    pass

                # Re-trigger the assigned agent for rework
                assigned = step.get("assigned_to") or plan.get("created_by", "")
                if assigned and assigned != "user":
                    import threading
                    def _post_rework():
                        try:
                            from tasks.ai.actions.plans import orchestrate_next_step
                            orchestrate_next_step(
                                self._conversation_id, plan_id, assigned)
                        except Exception as e:
                            logger.warning("Plan reject re-orchestrate failed: %s", e)
                    threading.Thread(target=_post_rework, daemon=True).start()

                return (
                    f"Step {step_num} rejected and sent back to {assigned}."
                    + (f" Reason: {reason}" if reason else "")
                )
        except Exception as e:
            return f"Error: {e}"

