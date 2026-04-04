"""AgentLoopTask actions -- plan management."""

import json
import logging
import time

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_plans(self, action, body, store, user_id, flowfile):
    """Handle plan management actions. Returns [flowfile] or None."""

    conv_id = body.get("conversation_id", "")

    if action == "get_plans":
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan_list = sorted(plans.values(), key=lambda p: p.get("created_at", 0), reverse=True)
        flowfile.set_content(json.dumps({"plans": plan_list}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "get_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "create_plan_user":
        title = body.get("title", "")
        steps = body.get("steps", [])
        if not conv_id or not title or not steps:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id, title, or steps"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        import uuid
        plan_id = "p_" + uuid.uuid4().hex[:8]
        plan = {
            "id": plan_id,
            "title": title,
            "status": "approved",
            "created_by": user_id or "user",
            "created_at": time.time(),
            "assigned_to": [],
            "steps": [
                {
                    "index": i + 1,
                    "description": s if isinstance(s, str) else s.get("description", ""),
                    "status": "pending",
                    "note": "",
                    "task_id": "",
                    "assigned_to": "",
                }
                for i, s in enumerate(steps)
            ],
        }
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_created", {"plan": plan})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "approve_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        if plan["status"] not in ("pending_approval",):
            flowfile.set_content(json.dumps({"error": f"Plan is already {plan['status']}"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan["status"] = "approved"
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            bus = ConversationEventBus.instance()
            bus.publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        # Auto-start: schedule the first pending step's agent
        _trigger_next_plan_step(conv_id, plan_id, plan, store, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "reject_plan":
        plan_id = body.get("plan_id", "")
        reason = body.get("reason", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        plan["status"] = "cancelled"
        if reason:
            plan["rejection_reason"] = reason
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "cancel_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        plan["status"] = "cancelled"
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "delete_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        if plan_id not in plans:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        del plans[plan_id]
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_deleted", {"plan_id": plan_id})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"deleted": True}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "update_plan_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        status = body.get("status", "")
        note = body.get("note", "")
        if not conv_id or not plan_id or not step or not status:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        for s in plan["steps"]:
            if s["index"] == step:
                s["status"] = status
                if note:
                    s["note"] = note
                break
        statuses = [s["status"] for s in plan["steps"]]
        if all(s in ("done", "skipped") for s in statuses):
            plan["status"] = "completed"
        elif any(s == "in_progress" for s in statuses):
            plan["status"] = "in_progress"
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        # Step chaining: if step is done/skipped, trigger next step
        if status in ("done", "skipped") and plan["status"] != "completed":
            _trigger_next_plan_step(conv_id, plan_id, plan, store, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "assign_plan":
        plan_id = body.get("plan_id", "")
        agent = body.get("agent", "")
        step_range = body.get("step_range", "")
        if not conv_id or not plan_id or not agent:
            flowfile.set_content(json.dumps({"error": "Missing plan_id or agent"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
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

        # Parse step range
        reassignable = ("pending", "in_progress", "error")
        if step_range == "remaining":
            # All steps that aren't done or skipped
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
            # Default: assign all unassigned steps (or reassignable ones with no assignee)
            for s in plan["steps"]:
                if not s.get("assigned_to") and s["status"] in reassignable:
                    s["assigned_to"] = agent

        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "verify_plan_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        approved = body.get("approved", False)
        reason = body.get("reason", "")
        if not conv_id or not plan_id or not step:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
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
            flowfile.set_content(json.dumps({"error": f"Step is '{target_step['status']}', not pending_verification"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        executor = target_step.get("assigned_to") or plan.get("created_by", "")
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
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        if approved and plan["status"] != "completed":
            _trigger_next_plan_step(conv_id, plan_id, plan, store, user_id)
        elif not approved and executor and executor != "user":
            try:
                from core.poll_scheduler import PollScheduler
                PollScheduler.instance().schedule_delay(
                    conv_id, 0,
                    key=f"{conv_id}::plan::{plan_id}::step{step}::{executor}",
                    reason=f"[plan_step:{plan_id}:{step}] ({executor})",
                    user_id=user_id,
                )
                try:
                    from tasks.ai.agent_loop import AgentLoopTask
                    AgentLoopTask.wake_poller()
                except Exception:
                    pass
            except Exception:
                pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "pause_plan_step":
        plan_id = body.get("plan_id", "")
        step = int(body.get("step", 0))
        paused = body.get("paused", True)  # True to pause, False to unpause
        if not conv_id or not plan_id or not step:
            flowfile.set_content(json.dumps({"error": "Missing required fields"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        for s in plan["steps"]:
            if s["index"] == step:
                s["paused"] = bool(paused)
                break
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        # If unpausing and plan is in_progress, check if this step should now execute
        if not paused and plan["status"] in ("in_progress", "approved"):
            # Check if previous steps are all done/skipped
            all_prev_done = all(
                s["status"] in ("done", "skipped")
                for s in plan["steps"] if s["index"] < step
            )
            if all_prev_done:
                _trigger_next_plan_step(conv_id, plan_id, plan, store, user_id)
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "set_plan_verifier":
        plan_id = body.get("plan_id", "")
        verifier = body.get("verifier", "")  # empty string = remove verifier
        step = int(body.get("step", 0))  # 0 = plan-level, >0 = specific step
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
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
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    if action == "reset_plan":
        plan_id = body.get("plan_id", "")
        if not conv_id or not plan_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id or plan_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plan = plans.get(plan_id)
        if not plan:
            flowfile.set_content(json.dumps({"error": f"Plan '{plan_id}' not found"}).encode())
            flowfile.set_attribute("http.response.status", "404")
            return [flowfile]
        # Only allow reset if no step is in_progress
        if any(s["status"] == "in_progress" for s in plan["steps"]):
            flowfile.set_content(json.dumps({"error": "Cannot reset: a step is in progress"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        plan["status"] = "pending_approval"
        for s in plan["steps"]:
            s["status"] = "pending"
            s["note"] = ""
            s.pop("verified_by", None)
            s.pop("rejected_by", None)
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(conv_id, "plan_updated", {"plan": plan})
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    return None


def _trigger_next_plan_step(conv_id, plan_id, plan, store, user_id,
                            current_agent=""):
    """Find the next pending step and schedule its assigned agent.

    If the next step is paused, stop and wait for user to unpause.
    If the next step is for the same agent as current_agent, return
    'continue' so the agent can proceed without rescheduling.
    """
    next_step = None
    for s in plan["steps"]:
        if s["status"] == "pending":
            next_step = s
            break
    if not next_step:
        return None  # No more pending steps

    # Check if step is paused
    if next_step.get("paused"):
        logger.info("Plan %s step %d is paused — waiting for user to unpause",
                     plan_id, next_step["index"])
        return None

    # Resolve agent: step.assigned_to > plan.created_by
    agent = next_step.get("assigned_to") or plan.get("created_by", "")
    if not agent or agent == "user":
        return None  # Can't schedule for user

    # Validate agent exists in ResourceStore — never schedule a phantom agent
    try:
        from core.resource_store import ResourceStore
        _adef = ResourceStore.instance().get_any("agent", agent, user_id)
        if not _adef:
            logger.error("Plan %s step %d: agent '%s' not found in ResourceStore. "
                         "Cannot schedule — refusing to fallback on default LLM.",
                         plan_id, next_step["index"], agent)
            return None
    except Exception as e:
        logger.error("Plan %s step %d: failed to validate agent '%s': %s",
                     plan_id, next_step["index"], agent, e)
        return None

    # Mark plan as in_progress
    if plan["status"] in ("approved",):
        plan["status"] = "in_progress"
        plans = store.get_extra(conv_id, "plans", default={}, user_id=user_id) or {}
        plans[plan_id] = plan
        store.set_extra(conv_id, "plans", plans, user_id=user_id)

    # Always schedule the next step — even for the same agent.
    # The agent must receive a user message to start the next step.
    total = len(plan["steps"])
    _user_msg = (
        f"Execute step {next_step['index']}/{total}: "
        f"{next_step['description']}"
    )
    try:
        from core.conversation_writer import ConversationWriter
        import uuid as _uuid_plan
        _msg_id = _uuid_plan.uuid4().hex[:12]
        ConversationWriter.for_conversation(conv_id).enqueue(
            [{"type": "msg", "role": "user", "content": _user_msg,
              "msg_id": _msg_id, "source": {"type": "plan", "plan_id": plan_id}}],
            user_id=user_id, context_agent=agent)
    except Exception as e:
        logger.warning("Failed to write plan step user message: %s", e)

    try:
        from core.poll_scheduler import PollScheduler
        PollScheduler.instance().schedule_delay(
            conv_id, 0,
            key=f"{conv_id}::plan::{plan_id}::step{next_step['index']}::{agent}",
            reason=f"[plan_step:{plan_id}:{next_step['index']}] ({agent})",
            user_id=user_id,
        )
        logger.info("Plan %s step %d scheduled for agent '%s'",
                     plan_id, next_step["index"], agent)
        # Wake poller immediately
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.wake_poller()
        except Exception:
            pass
    except Exception as e:
        logger.warning("Failed to schedule plan step: %s", e)
    return None
