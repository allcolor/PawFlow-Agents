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
            # If plan was created by an agent, notify them to continue
            created_by = plan.get("created_by", "")
            if created_by and created_by != "user" and created_by != user_id:
                bus.publish_event(conv_id, "notification", {
                    "message": f"Plan '{plan['title']}' ({plan_id}) has been approved. You may proceed with execution.",
                    "target_agent": created_by,
                })
        except Exception:
            pass
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
        # Assign implies approval
        if plan["status"] == "pending_approval":
            plan["status"] = "approved"
        if plan["status"] == "cancelled":
            flowfile.set_content(json.dumps({"error": "Cannot assign a cancelled plan"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]

        plan["status"] = "in_progress"
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
        # Wake up the assigned agent to start working
        try:
            from core.poll_scheduler import PollScheduler
            PollScheduler.instance().schedule_delay(
                conv_id, 0,
                key=f"{conv_id}::plan::{plan_id}",
                reason=f"[plan:{plan_id}] assigned to {agent}",
                user_id=user_id,
            )
        except Exception:
            pass
        flowfile.set_content(json.dumps({"plan": plan}, ensure_ascii=False).encode())
        return [flowfile]

    return None
