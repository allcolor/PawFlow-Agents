"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import logging
import time


logger = logging.getLogger(__name__)


def wake_agent_poller() -> None:
    """Wake the live agent poller if it is running."""
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        _exec = AgentLoopTask._live_instance
        if _exec is not None and hasattr(_exec, "_poller_wake"):
            _exec._poller_wake.set()
    except Exception:
        logger.debug("agent poller wake failed", exc_info=True)


def schedule_agent_task_wake(conversation_id: str, task_id: str,
                             reason: str, user_id: str = "",
                             delay_seconds: int = 0) -> float:
    """Schedule a task wake and nudge the poller immediately."""
    from core.poll_scheduler import PollScheduler
    recheck_at = PollScheduler.instance().schedule_delay(
        conversation_id, delay_seconds,
        key=f"{conversation_id}::task::{task_id}",
        reason=reason,
        user_id=user_id,
    )
    wake_agent_poller()
    return recheck_at


def _activate_dependents(conversation_id: str, completed_task_id: str,
                        result: str = "", user_id: str = ""):
    """Check if any waiting tasks can be activated after a task completes."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    all_tasks = store.get_extra(conversation_id, "agent_tasks") or {}
    activated = []
    for tid, t in list(all_tasks.items()):
        if not isinstance(t, dict) or t.get("status") != "waiting":
            continue
        deps = t.get("depends_on") or []
        if completed_task_id not in deps:
            continue
        # Check if ALL deps are met (not in all_tasks = completed and removed)
        all_met = all(d not in all_tasks or all_tasks[d].get("status") == "completed"
                      for d in deps)
        if not all_met:
            continue
        # Activate this task
        t["status"] = "active"
        all_tasks[tid] = t
        schedule_agent_task_wake(
            conversation_id, tid,
            reason=f"[agent_task:{tid}] deps met, activated ({t.get('agent', '?')})",
            user_id=user_id or t.get("assigned_by", ""),
            delay_seconds=0,
        )
        # Inject parent results into the sub-conversation
        parent_results = {}
        for d in deps:
            _log = store.get_extra(conversation_id, f"task_log:{d}") or []
            # Find last completed entry
            for entry in reversed(_log):
                if entry.get("type") == "completed":
                    parent_results[d] = entry.get("detail", "")
                    break
        if parent_results:
            sub_cid = f"{conversation_id}::task::{tid}"
            _msg = "## Results from dependency tasks\n"
            for dep_id, dep_result in parent_results.items():
                _msg += f"\n### Task {dep_id}\n{dep_result}\n"
            import uuid as _task_uuid
            from core.conversation_writer import ConversationWriter
            from core.llm_client import stamp_message
            ConversationWriter.for_conversation(sub_cid).enqueue_message(
                stamp_message({"role": "user", "content": _msg,
                               "msg_id": _task_uuid.uuid4().hex[:12]},
                              sub_cid))
        activated.append(tid)
        try:
            _append_task_log(conversation_id, tid, {
                "type": "activated",
                "agent": t.get("agent", ""),
                "detail": f"Dependencies met: {', '.join(deps)}",
            })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "task_progress", {
                    "task_id": tid, "agent": t.get("agent", ""),
                    "stage": "activated", "deps_met": deps,
                },
            )
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    if activated:
        store.set_extra(conversation_id, "agent_tasks", all_tasks)
        logger.info("Activated %d dependent tasks after %s completed: %s",
                    len(activated), completed_task_id, activated)
    return activated


def _append_task_log(conversation_id: str, task_id: str, entry: dict):
    """Append an entry to the persistent task timeline log (standalone helper)."""
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    key = f"task_log:{task_id}"
    log = store.get_extra(conversation_id, key) or []
    entry["ts"] = time.time()
    log.append(entry)
    if len(log) > 500:
        log = log[-500:]
    store.set_extra(conversation_id, key, log)





