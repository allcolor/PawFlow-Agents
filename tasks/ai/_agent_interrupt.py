"""Agent cancel/interrupt cluster for AgentLoopTask.

_AgentInterruptMixin holds generation-tracking, cancel_agent, interrupt_agent
and the interrupt-flag check. Split out of agent_loop.py for the <=800-line
rule; mixed into AgentLoopTask (one MRO, shared self state).
"""

import threading
import logging

from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND

logger = logging.getLogger(__name__)


class _AgentInterruptMixin:
    """Cancel/interrupt/generation methods for AgentLoopTask."""

    def _is_current_generation(self, conversation_id: str, generation: int) -> bool:
        """Check if this thread's generation is still current.

        Returns False if a newer user request has started for this conversation,
        meaning this thread should NOT overwrite the conversation store.
        """
        with self._conv_gen_lock:
            return self._conv_generation.get(conversation_id, 0) == generation


    def cancel_agent(self, conversation_id: str, agent_name: str = "",
                     silent: bool = False, reason: str = "user_request"):
        """Cancel a running agent for this conversation.

        If agent_name is specified, only cancel that specific agent's thread.
        Otherwise cancel ALL agents for this conversation.

        Increments the generation counter so the running thread detects
        staleness at the next check point and stops gracefully.

        If silent=True, no SSE event is published (used by context ops
        that cancel as a precaution, not as user-visible action).
        """
        # Empty agent_name = cancel all agents
        # whose gen_key is just conversation_id, not conversation_id:assistant
        _is_named = agent_name and agent_name != ""
        with self._conv_gen_lock:
            # Bump ONLY the keys that match a CURRENTLY RUNNING loop.
            # Do NOT bump keys that don't exist yet — a future loop
            # will capture its own gen at startup and won't be affected.
            _keys_to_bump = set()
            _prefix = f"{conversation_id}:"
            for k in list(self._conv_generation):
                if k == conversation_id or k.startswith(_prefix):
                    if _is_named and agent_name.lower() not in k.lower() and k != conversation_id:
                        continue  # different agent — don't touch
                    _keys_to_bump.add(k)
            # Always include the standard keys (they may be used by the current loop)
            _keys_to_bump.add(conversation_id)
            if _is_named:
                _keys_to_bump.add(f"{conversation_id}:{agent_name}")
            for k in _keys_to_bump:
                self._conv_generation[k] = self._conv_generation.get(k, 0) + 1
        if not silent:
            # Publish cancellation event for SSE listeners
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "cancelled", {
                    "reason": reason,
                    "agent_name": agent_name if _is_named else "all",
                }
            )
        # Cancel tool relay for this (conv, agent) — pending tool calls return error
        try:
            from services.tool_relay_service import ToolRelayService
            _cancel_agent = agent_name if _is_named else ""
            ToolRelayService.cancel_agent(conversation_id, _cancel_agent)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        # Kill any running Claude Code subprocess for this conversation
        _force = False  # force stop handled separately by FORCE_STOP action
        # Kill Claude Code subprocess (check both conv:agent and conv-only keys)
        with self._active_contexts_lock:
            _cc_keys = [f"{conversation_id}:{agent_name}"] if _is_named else \
                [k for k in self._active_claude_client if (k == conversation_id or k.startswith(conversation_id + ":")) and "::task::" not in k and "::task_verify::" not in k]
            _cc_clients = [(k, self._active_claude_client.get(k)) for k in _cc_keys]
        for _cc_key, client in _cc_clients:
            if client and hasattr(client, 'cancel_claude_code'):
                client.cancel_claude_code(force=_force)
            if client and hasattr(client, 'abort'):
                client.abort()
        # Also cancel thought threads and schedules for this agent
        from core.poll_scheduler import PollScheduler
        scheduler = PollScheduler.instance()
        if _is_named:
            # Cancel specific agent's thought
            _thought_key = f"{conversation_id}::thought::{agent_name.lower()}"
            with self._conv_gen_lock:
                self._conv_generation[_thought_key] = \
                    self._conv_generation.get(_thought_key, 0) + 1
            with self._interrupt_lock:
                self._conv_interrupt[_thought_key] = True
            scheduler.cancel(_thought_key)
        else:
            # Cancel ALL thought threads for this conversation
            with self._conv_gen_lock:
                for k in list(self._conv_generation):
                    if "::thought::" in k and k.startswith(conversation_id):
                        self._conv_generation[k] += 1
            for k in list(scheduler._schedules):
                if k.startswith(conversation_id) and "::task::" not in k and "::task_verify::" not in k:
                    scheduler.cancel(k)
        # Clear poll cooldown so poller doesn't re-trigger immediately
        with self._active_lock:
            self._active_conversations.pop(conversation_id, None)
            self._user_active_conversations.discard(conversation_id)

        # Reset status
        # _active_contexts cleanup happens in _run_agent_loop finally
        import traceback as _tb
        _caller = ""
        try:
            _stack = _tb.extract_stack(limit=6)[:-1]
            _caller = " <- " + " <- ".join(
                f"{__import__('os').path.basename(f.filename)}:{f.lineno}"
                for f in reversed(_stack[-4:]))
        except Exception:
            logger.debug("exception suppressed", exc_info=True)
        logger.info(f"[agent:{conversation_id[:8]}] cancelled ({reason})"
                    f"{f' (agent: {agent_name})' if _is_named else ' (all)'}"
                    f"{_caller}")


    def interrupt_agent(self, conversation_id: str, agent_name: str = ""):
        """Interrupt: cancel the current LLM call and spawn a parallel synthesis.

        Cooldown: ignores repeated interrupts within 10 seconds.
        No-op if no agent is actively running.
        """
        # Check if anything is actually running for this conversation. A
        # thread can be alive while _active_contexts is temporarily empty
        # during context preparation, provider compact/restart, or cleanup.
        with self._active_contexts_lock:
            _any_active = any(
                k == conversation_id or k.startswith(conversation_id + ":")
                for k in self._active_contexts)
        if not _any_active:
            _any_active = any(
                t.is_alive() and (
                    t.name == f"agent-stream-{conversation_id}"
                    or t.name.startswith(f"agent-stream-{conversation_id}:")
                )
                for t in threading.enumerate())
        if not _any_active:
            logger.info(f"[agent:{conversation_id[:8]}] interrupt ignored — no active agent")
            return

        import time as _t
        _synth_key = f"{conversation_id}:{agent_name or 'all'}"
        _now = _t.time()
        with self._interrupt_lock:
            _last = self._interrupt_cooldowns.get(_synth_key, 0)
            _is_repeat = _now - _last < 10
            if _is_repeat:
                self._interrupt_cooldowns.pop(_synth_key, None)
            else:
                self._interrupt_cooldowns[_synth_key] = _now
        if _is_repeat:
            # Second interrupt within cooldown = escalate to force stop
            logger.info(f"[agent:{conversation_id[:8]}] repeat interrupt → escalating to force stop")
            self.cancel_agent(conversation_id, agent_name=agent_name, silent=False)
            try:
                from tasks.ai.actions.cancel_interrupt import _clear_force_stop_relaunch_state
                _clear_force_stop_relaunch_state(conversation_id, agent_name)
            except Exception:
                logger.debug("force-stop relaunch cleanup failed", exc_info=True)
            # Force kill Claude Code subprocess if applicable
            _esc_key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
            with self._active_contexts_lock:
                _cc_client = self._active_claude_client.get(_esc_key)
            if _cc_client and hasattr(_cc_client, 'cancel_claude_code'):
                _cc_client.cancel_claude_code(force=True)
            if _cc_client and hasattr(_cc_client, 'abort'):
                _cc_client.abort()
            # Force cleanup
            from core.conversation_event_bus import ConversationEventBus as _CEB_int
            _CEB_int.instance().publish_event(
                conversation_id, "done", {
                    "response": "[Force stopped by user]",
                    "agent_name": agent_name or "",
                    "force_stopped": True,
                })
            with self._active_lock:
                self._active_conversations.pop(conversation_id, None)
                self._user_active_conversations.discard(conversation_id)
            with self._active_contexts_lock:
                # Remove all agents for this conversation
                for k in list(self._active_contexts):
                    if k == conversation_id or k.startswith(conversation_id + ":"):
                        del self._active_contexts[k]
            return

        logger.info(f"[agent:{conversation_id[:8]}] interrupt for '{agent_name or 'agent'}'")

        # Interrupt = inject a STOP user message into the live agent when the
        # provider supports bidirectional steering (CC stdin, Codex turn/steer,
        # Gemini ACP session/prompt). Do not spawn a separate synthesizer.
        _int_key = f"{conversation_id}:{agent_name}" if agent_name else conversation_id
        with self._active_contexts_lock:
            _active_client = self._active_claude_client.get(_int_key)
            _active_ctx = self._active_contexts.get(_int_key) or {}
        try:
            from services.tool_relay_service import ToolRelayService
            ToolRelayService.cancel_agent(conversation_id, agent_name)
        except Exception:
            logger.debug("exception suppressed", exc_info=True)

        if (_active_client and hasattr(_active_client, 'send_user_message')
                and _active_client.send_user_message(
                    SOFT_INTERRUPT_USER_COMMAND,
                    user_id=str(_active_ctx.get("user_id") or ""),
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                )):
            logger.info(
                f"[agent:{conversation_id[:8]}] interrupt delivered as live user STOP "
                f"to '{agent_name or 'agent'}'")
            return

        # LLM API streams are not transport-bidirectional once the HTTP request
        # is in flight. Mark the active loop for a graceful interrupt turn:
        # the loop discards the current API turn at the next safe boundary,
        # sends the STOP user message once, persists that assistant reply, then
        # exits. Do NOT bump generation here; that is force-stop semantics.
        _interrupt_keys = set()
        _prefix = f"{conversation_id}:"
        _agent_l = (agent_name or "").lower()
        with self._active_contexts_lock:
            for _ctx_key, _ctx in self._active_contexts.items():
                if not (_ctx_key == conversation_id or _ctx_key.startswith(_prefix)):
                    continue
                if _agent_l and _agent_l not in _ctx_key.lower():
                    continue
                if isinstance(_ctx, dict):
                    _interrupt_keys.add(_ctx.get("_gen_key") or _ctx_key)
                else:
                    _interrupt_keys.add(_ctx_key)
        if not _interrupt_keys:
            _interrupt_keys.add(f"{conversation_id}:{agent_name}" if agent_name else conversation_id)
        with self._interrupt_lock:
            for _key in _interrupt_keys:
                self._conv_interrupt[_key] = True
        logger.info(
            f"[agent:{conversation_id[:8]}] interrupt scheduled graceful STOP "
            f"for non-steerable provider '{agent_name or 'agent'}' "
            f"keys={sorted(_interrupt_keys)}")
        return



    def _check_interrupt(self, gen_key: str) -> bool:
        """Check and consume the interrupt flag for a gen_key."""
        with self._interrupt_lock:
            return self._conv_interrupt.pop(gen_key, False)
