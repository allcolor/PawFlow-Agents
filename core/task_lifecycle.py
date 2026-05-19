"""Agent task lifecycle cleanup helpers."""

from __future__ import annotations

import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


def split_task_conversation(conversation_id: str,
                            task_id: str = "") -> Tuple[str, str]:
    """Return ``(parent_cid, task_id)`` for task or verify sub-convs."""
    parent_cid = conversation_id or ""
    resolved_task_id = task_id or ""
    for marker in ("::task::", "::task_verify::"):
        if marker in parent_cid:
            parent_cid, suffix = parent_cid.split(marker, 1)
            if not resolved_task_id:
                resolved_task_id = suffix.split("::", 1)[0]
            break
    return parent_cid, resolved_task_id


def task_context_ids(parent_cid: str, task_id: str) -> Tuple[str, str]:
    """Return the task work and verification sub-conversation ids."""
    return (f"{parent_cid}::task::{task_id}",
            f"{parent_cid}::task_verify::{task_id}")


def _clear_live_task_runtime(context_ids: Tuple[str, str], agent_name: str) -> int:
    """Drop best-effort in-memory state for a finished/cancelled task."""
    try:
        from tasks.ai.agent_loop import AgentLoopTask
        exec_inst = AgentLoopTask._live_instance
    except Exception:
        exec_inst = None
    if exec_inst is None:
        return 0

    removed = 0
    prefixes = tuple(context_ids)

    def _matches(value: str) -> bool:
        return any(value == cid or value.startswith(cid + ":")
                   for cid in prefixes)

    clients = []
    lock = getattr(exec_inst, "_active_contexts_lock", None)
    if lock is not None:
        with lock:
            active_contexts = getattr(exec_inst, "_active_contexts", {})
            for key in list(active_contexts):
                if _matches(str(key)):
                    active_contexts.pop(key, None)
                    removed += 1
            active_clients = getattr(exec_inst, "_active_claude_client", {})
            for key in list(active_clients):
                if _matches(str(key)):
                    client = active_clients.pop(key, None)
                    if client is not None:
                        clients.append(client)

    for client in clients:
        try:
            if hasattr(client, "cancel_claude_code"):
                client.cancel_claude_code(force=True)
            if hasattr(client, "abort"):
                client.abort()
        except Exception:
            logger.debug("task runtime client cleanup failed", exc_info=True)

    active_lock = getattr(exec_inst, "_active_lock", None)
    if active_lock is not None:
        with active_lock:
            active_thoughts = getattr(exec_inst, "_active_thoughts", set())
            for cid in context_ids:
                active_thoughts.discard(cid)

    gen_lock = getattr(exec_inst, "_conv_gen_lock", None)
    if gen_lock is not None:
        with gen_lock:
            generations = getattr(exec_inst, "_conv_generation", {})
            for key in list(generations):
                if _matches(str(key)):
                    generations.pop(key, None)

    interrupt_lock = getattr(exec_inst, "_interrupt_lock", None)
    if interrupt_lock is not None:
        with interrupt_lock:
            interrupts = getattr(exec_inst, "_conv_interrupt", {})
            for cid in context_ids:
                interrupts.pop(cid, None)

    return removed


def cleanup_agent_task_context(conversation_id: str, task_id: str,
                               agent_name: str = "", store=None,
                               *, cancel_schedules: bool = True,
                               clear_runtime: bool = False,
                               reason: str = "task_finished") -> Dict[str, object]:
    """Delete a terminal task's isolated PawFlow context and CLI sessions."""
    parent_cid, resolved_task_id = split_task_conversation(
        conversation_id, task_id)
    if not parent_cid or not resolved_task_id:
        return {"parent_cid": parent_cid, "task_id": resolved_task_id,
                "deleted": 0, "runtime_removed": 0}

    if store is None:
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()

    context_ids = task_context_ids(parent_cid, resolved_task_id)
    if cancel_schedules:
        try:
            from core.poll_scheduler import PollScheduler
            scheduler = PollScheduler.instance()
            for cid in context_ids:
                scheduler.cancel(cid)
        except Exception:
            logger.debug("task schedule cleanup failed", exc_info=True)

    try:
        from services.tool_relay_service import ToolRelayService
        for cid in context_ids:
            ToolRelayService.cancel_agent(cid, agent_name)
    except Exception:
        logger.debug("task relay cancellation cleanup failed", exc_info=True)

    runtime_removed = (_clear_live_task_runtime(context_ids, agent_name)
                       if clear_runtime else 0)

    deleted = 0
    for cid in context_ids:
        try:
            store.invalidate_claude_sessions(cid)
            if store.delete(cid):
                deleted += 1
        except Exception:
            logger.debug("task context delete failed for %s", cid,
                         exc_info=True)
    logger.info("Cleaned task context %s/%s (%s): deleted=%d runtime=%d",
                parent_cid[:8], resolved_task_id, reason, deleted,
                runtime_removed)
    return {"parent_cid": parent_cid, "task_id": resolved_task_id,
            "contexts": context_ids, "deleted": deleted,
            "runtime_removed": runtime_removed}
