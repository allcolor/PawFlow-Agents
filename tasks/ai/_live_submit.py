"""Submit non-user messages to a busy CLI agent one at a time, on arrival.

A delegate, a background result or a due wake-up is not the user: it never
interrupts the agent. It is still submitted the moment it arrives, on its
own: paste + Enter into the running tmux session, and the CLI keeps it until
its current step (a 15-minute tool included) lets it read it. Twenty
delegates are twenty submissions, never one paste.

They used to wait in the PendingQueue until the CLI turn ended -- a CLI turn
is a single PawFlow iteration, so nothing drained the queue before -- and the
next prompt then carried the whole backlog at once (2026-09-25: delegates
held 15 minutes, and a 2634-message paste that Codex never submitted).

API agents keep the queue: their loop drains it at every iteration.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_LIVE_SUBMIT_PROVIDERS = frozenset({"claude-code-interactive", "codex-interactive"})

# One lock per (conversation, agent): submissions reach the tmux in arrival
# order, one paste + Enter at a time.
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
                    source: str, *, user_id: str = "", wake: bool = True,
                    wake_reason: str = "", even_if_active: bool = True) -> bool:
    """Deliver a stamped non-user message to an agent.

    A running CLI agent gets it submitted at once in its live session; the
    message is then queued as ``preempt_rescue`` so the final drain persists
    it without starting another turn. Anything else -- idle agent, API
    provider, failed paste -- takes the PendingQueue + wake path.
    """
    content = message.get("content")
    client = _active_cli_client(conversation_id, agent_name)
    if client is None or not isinstance(content, str) or not content.strip():
        return _queue(conversation_id, agent_name, message, source,
                      user_id, wake, wake_reason, even_if_active)
    threading.Thread(
        target=_submit_live,
        args=(client, conversation_id, agent_name, message, source,
              user_id, wake, wake_reason, even_if_active),
        name=f"live-submit-{agent_name}", daemon=True,
    ).start()
    return True


def _submit_live(client, conversation_id, agent_name, message, source,
                 user_id, wake, wake_reason, even_if_active) -> None:
    msg_id = message.get("msg_id") or ""
    with _submit_lock(conversation_id, agent_name):
        try:
            ok = client.send_queued_message(
                message["content"], user_id=user_id,
                conversation_id=conversation_id, agent_name=agent_name,
                msg_id=msg_id)
        except Exception:
            logger.warning("[live-submit] submission to %s/%s failed",
                           conversation_id[:8], agent_name, exc_info=True)
            ok = False
    if ok:
        logger.info("[live-submit] %s submitted to the running %s/%s (source=%s)",
                    msg_id, conversation_id[:8], agent_name, source)
        _queue(conversation_id, agent_name, message, "preempt_rescue",
               user_id, False, "", False)
        return
    logger.warning("[live-submit] %s not submitted to %s/%s — queued (source=%s)",
                   msg_id, conversation_id[:8], agent_name, source)
    _queue(conversation_id, agent_name, message, source,
           user_id, wake, wake_reason, even_if_active)


def _queue(conversation_id, agent_name, message, source, user_id, wake,
           wake_reason, even_if_active) -> bool:
    from core.pending_queue import PendingQueue
    queued = PendingQueue.for_agent(conversation_id, agent_name).enqueue(
        message, source=source)
    if wake:
        from tasks.ai.agent_loop import AgentLoopTask
        AgentLoopTask.wake_agent(
            conversation_id, agent_name,
            reason=wake_reason or f"[{source}] queued for {agent_name}",
            user_id=user_id, delay=0.0, even_if_active=even_if_active)
    return bool(queued)
