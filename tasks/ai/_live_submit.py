"""Deliver non-user messages to an agent: inbox item plus imperative notice.

A delegate, a background result or a due wake-up is not the user: it never
interrupts the agent and its content is never pasted into a prompt. While
the agent runs, the message is stored as an inbox item (core/agent_inbox.py)
and the agent only gets the short notice telling it to call ``read_inbox``:

- a running tmux CLI (Claude Code, Codex) gets the notice pasted now, on its
  own (paste + Enter, no Escape), and reads it at its next step;
- an API agent gets it from ``drain_pending`` at its next iteration.

A burst of messages pastes one notice while an unread notice is outstanding.
Items the turn did
not read keep the agent going at its end (agent_core final drain).

An idle agent is woken for actionable work. Passive catch-up stays unread
until its next real turn, including for workflow recipients.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_LIVE_SUBMIT_PROVIDERS = frozenset({"claude-code-interactive", "codex-interactive"})

# A notice the CLI did not accept is tried again shortly (the composer may be
# redrawing, a paste may have been swallowed). If it still fails, the next
# drain or the end-of-turn check delivers the notice.
_RETRY_DELAYS_SECONDS = (2.0, 5.0)

# One lock per (conversation, agent): notices reach the tmux one paste +
# Enter at a time.
_submit_locks: dict = {}
_submit_locks_guard = threading.Lock()


def _submit_lock(conversation_id: str, agent_name: str) -> threading.Lock:
    key = (conversation_id, agent_name.lower())
    with _submit_locks_guard:
        lock = _submit_locks.get(key)
        if lock is None:
            lock = _submit_locks[key] = threading.Lock()
        return lock


def _active_cli_client(conversation_id: str, agent_name: str):
    """The live CLI client of the agent's running turn, or None."""
    from tasks.ai.agent_loop import AgentLoopTask
    inst = AgentLoopTask._live_instance
    if inst is None or not conversation_id or not agent_name:
        return None
    key = f"{conversation_id}:{agent_name}".lower()
    with inst._active_contexts_lock:
        client = next((c for k, c in inst._active_claude_client.items()
                       if k.lower() == key), None)
    if getattr(client, "provider", "") not in _LIVE_SUBMIT_PROVIDERS:
        return None
    return client


def submit_or_queue(conversation_id: str, agent_name: str, message: dict,
                    source: str, *, kind: str, sender: str,
                    user_id: str = "", wake: bool = True,
                    wake_reason: str = "", even_if_active: bool = True) -> bool:
    """Deliver a stamped non-user message to an agent.

    Running agent: inbox item of ``kind`` from ``sender`` plus notice.
    Idle agent: the same inbox item plus wake. False when the
    force-stop fence rejected the message.
    """
    from tasks.ai.agent_loop import AgentLoopTask
    from core.conv_agent_config import get_agent_config
    if (kind != "catch_up"
            and get_agent_config(conversation_id, agent_name).get("runtime_kind") == "workflow"):
        return _submit_workflow_delivery(conversation_id, agent_name, message,
                                         source, user_id)
    from core import agent_inbox
    if not agent_inbox.enqueue_item(
            conversation_id, agent_name, message, kind, sender):
        return False
    logger.info("[inbox] %s stored for %s/%s (kind=%s from %s)",
                message.get("msg_id"), conversation_id[:8], agent_name,
                kind, sender)
    client = _active_cli_client(conversation_id, agent_name)
    if client is not None:
        threading.Thread(
            target=_paste_notice,
            args=(client, conversation_id, agent_name, user_id),
            name=f"inbox-notice-{agent_name}", daemon=True,
        ).start()
    if wake and kind != "catch_up":
        AgentLoopTask.wake_agent(
            conversation_id, agent_name,
            reason=wake_reason or f"[{source}] queued for {agent_name}",
            user_id=user_id, delay=0.0, even_if_active=even_if_active)
    return True


def _submit_workflow_delivery(conversation_id, agent_name, message, source, user_id):
    """Keep workflow recipients on their existing durable lease protocol."""
    from core.agent_inbox_store import AgentInboxStore
    from core.agent_runtime_router import AgentRuntimeRouter
    from core.conversation_writer import ConversationWriter
    from core.workflow_agent_runtime import prepare_workflow_turn
    runtime = AgentRuntimeRouter.instance().resolve(conversation_id, agent_name)
    inbox = AgentInboxStore.instance()
    inbox.prepare_receipt(conversation_id, agent_name, message, source)
    ConversationWriter.for_conversation(conversation_id).enqueue_message_if_absent(
        dict(message), agent_name=agent_name, user_id=user_id)
    item = inbox.mark_transcript_persisted(
        conversation_id, agent_name, message["msg_id"])
    if item.state == "discarded":
        return False
    prepared = prepare_workflow_turn(
        conversation_id=conversation_id, agent_name=runtime.canonical_agent_name,
        user_id=user_id, message=message.get("content") or "",
        attachments=message.get("attachments") or [], message_id=message["msg_id"],
        channel=message.get("channel") or "web", permission_mode="default",
        source=dict(message.get("source") or {}))
    runtime.adapter.submit(prepared)
    return True


def _paste_notice(client, conversation_id, agent_name, user_id) -> None:
    from core import agent_inbox
    with _submit_lock(conversation_id, agent_name):
        notice, newest = agent_inbox.peek_notice(
            conversation_id, agent_name, only_new=True)
        if not notice:
            return
        for attempt, delay in enumerate((0.0, *_RETRY_DELAYS_SECONDS)):
            if delay:
                time.sleep(delay)
                if _active_cli_client(conversation_id, agent_name) is not client:
                    return  # the turn ended: its final drain takes over
            try:
                ok = client.send_queued_message(
                    notice, user_id=user_id,
                    conversation_id=conversation_id, agent_name=agent_name,
                    msg_id="")
            except Exception:
                logger.warning("[inbox] notice paste to %s/%s failed",
                               conversation_id[:8], agent_name, exc_info=True)
                ok = False
            if ok:
                agent_inbox.record_notice(conversation_id, agent_name, newest)
                logger.info("[inbox] notice pasted to running %s/%s",
                            conversation_id[:8], agent_name)
                return
            logger.warning("[inbox] notice not accepted by %s/%s (attempt %d)",
                           conversation_id[:8], agent_name, attempt + 1)
