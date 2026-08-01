"""Codex TUI provider with a transparent MITM-observed Responses stream."""

from __future__ import annotations

import logging

from core.codex_interactive_pool import CodexInteractivePool
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)
from core.llm_providers.claude_code_interactive import (
    LLMClaudeCodeInteractiveMixin)

logger = logging.getLogger(__name__)


class LLMCodexInteractiveMixin:
    """Long-lived Codex TUI sessions using the official OAuth endpoint."""

    # Prompt/file materialization is provider-neutral. Reuse the proven CCI
    # implementation while keeping transport/session methods Codex-specific.
    _cci_prompt = LLMClaudeCodeInteractiveMixin._cci_prompt
    _cci_catchup_context = LLMClaudeCodeInteractiveMixin._cci_catchup_context
    _cci_live_text = LLMClaudeCodeInteractiveMixin._cci_live_text
    _cci_materialize_images = (
        LLMClaudeCodeInteractiveMixin._cci_materialize_images)
    _cci_attachment_block = staticmethod(
        LLMClaudeCodeInteractiveMixin._cci_attachment_block)
    _cci_preempt_prompt = LLMClaudeCodeInteractiveMixin._cci_preempt_prompt

    def record_observed_cli_context(self, conversation_id: str,
                                    agent_name: str, tokens: int) -> None:
        """Store the prompt size Codex reported for this stream.

        Read back by the context gauge, which otherwise has nothing to
        measure: the window belongs to the Codex session, not to PawFlow.
        The dict is created in ``LLMClient.__init__`` and shared by reference
        with call clones, so the clone that runs the turn and the resolver
        client the gauge reads expose one authoritative value.
        """
        if not conversation_id or not agent_name:
            return
        try:
            measured = int(tokens or 0)
        except (TypeError, ValueError):
            return
        if measured <= 0:
            return
        counts = getattr(self, "_cli_observed_context_tokens_by_stream", None)
        if not isinstance(counts, dict):
            counts = {}
            self._cli_observed_context_tokens_by_stream = counts
        counts[(conversation_id, agent_name)] = measured

    def record_codex_context_window(self, pool, state, conversation_id: str,
                                    agent_name: str, used_tokens: int) -> None:
        """Derive and store the session's real context window from the TUI.

        The Responses API reports the size of each prompt but never the window
        it is measured against, so PawFlow drew the gauge -- and armed its
        auto-compact threshold -- against whatever ``max_context_size`` was
        configured, with no guarantee it matched the model. The Codex TUI shows
        the missing half in its status bar ("context left 74%"), and the two
        together determine the window exactly.

        Sampled once per turn: capturing the pane costs a `docker exec`, and
        the window does not change within a turn.
        """
        if not conversation_id or not agent_name or not state:
            return
        try:
            used = int(used_tokens or 0)
        except (TypeError, ValueError):
            return
        if used <= 0:
            return
        windows = getattr(self, "_cli_observed_context_window_by_stream", None)
        if not isinstance(windows, dict):
            windows = {}
            self._cli_observed_context_window_by_stream = windows
        key = (conversation_id, agent_name)
        previous = int(windows.get(key, 0) or 0)
        try:
            from core.codex_interactive_pool import (
                context_left_fraction, derive_context_window)
            pane = pool._pane_text(state.name)
            left = context_left_fraction(pane)
            window = derive_context_window(used, left, previous=previous)
        except Exception:
            logger.debug("[codex-interactive] context window probe failed",
                         exc_info=True)
            return
        if window <= 0 or window == previous:
            return
        windows[key] = window
        logger.info(
            "[codex-interactive] context window derived: used=%d left=%s "
            "-> window=%d (was %d)", used,
            f"{left:.2f}" if left is not None else "?", window, previous)

    def _stream_codex_interactive(
        self, messages, model, temperature=0.7, max_tokens=0, tools=None,
        callback=None, thinking_budget=0, thinking_callback=None,
        turn_callback=None, block_callback=None, *, call_user_id=None,
        call_conversation_id=None, call_agent_name=None, call_event_cid=None,
        call_ephemeral_stream=None,
    ):
        from core.llm_client import LLMClientError
        from services.cc_interactive_event_service import (
            get_or_create_cc_interactive_event_service)

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conversation_id = (call_conversation_id
                           or getattr(self, "_conversation_id", "") or "")
        agent_name = (call_agent_name
                      or getattr(self, "_agent_name", "") or "")
        if not user_id or not conversation_id or not agent_name:
            raise LLMClientError(
                "codex-interactive requires user_id, conversation_id and "
                "agent_name")

        pool = CodexInteractivePool.instance()
        ephemeral = bool(
            call_ephemeral_stream if call_ephemeral_stream is not None
            else getattr(self, "_ephemeral_stream", False))
        state = pool.ensure_started(
            self, model or "", user_id, conversation_id, agent_name,
            before_launch=None if ephemeral else (
                lambda: self._cli_require_cold_context("codex-interactive")))
        pool.touch(state)
        # Case 2, told by the pool -- see the CCI provider for the reasoning.
        if not ephemeral and getattr(state, "initial_context_loaded", False):
            self._cli_require_delta_context("codex-interactive")
        self._codex_interactive_active_user_id = user_id
        self._codex_interactive_active_conversation_id = conversation_id
        self._codex_interactive_active_agent_name = agent_name
        self._codex_interactive_active_service_id = (
            getattr(self, "_agent_service", "") or "")
        self._had_preempts_this_turn = False

        prompt = self._cci_prompt(
            messages, tools, state.workdir, state.container_workdir,
            user_id, conversation_id,
            initial_context=not state.initial_context_loaded,
            agent_name=agent_name)
        _, _, event_service = get_or_create_cc_interactive_event_service()
        consumer_epoch = event_service.claim_consumer(state.session_token)
        event_service.drain_session(state.session_token)
        if not pool.send_text(state, prompt):
            # No coordinator will ever poll this claim. Hand the stream back
            # so the orphan-turn net can adopt the turn the user is about to
            # start by pressing Enter in the tmux themselves -- the prompt is
            # usually sitting in the composer, and its answer has to reach the
            # webchat rather than run into a stream nobody owns.
            event_service.release_consumer(state.session_token, consumer_epoch)
            detail = getattr(state, "last_error", "") or "unknown tmux error"
            raise LLMClientError(
                "Failed to paste prompt into Codex interactive tmux session: "
                f"{detail}")
        state.initial_context_loaded = True

        coord = _CodexInteractiveTurnCoordinator(
            event_service, state.session_token, callback=callback,
            thinking_callback=thinking_callback,
            block_callback=block_callback, turn_callback=turn_callback,
            touch_callback=lambda: pool.touch(state),
            emitted_tool_use_ids=state.emitted_tool_use_ids,
            emitted_tool_result_ids=state.emitted_tool_result_ids,
            consumer_epoch=consumer_epoch,
            context_tokens_callback=lambda tokens: (
                self.record_observed_cli_context(
                    conversation_id, agent_name, tokens)))
        response = coord.run(getattr(self, "_abort", None))
        self.record_codex_context_window(
            pool, state, conversation_id, agent_name,
            coord.observed_context_tokens)
        response.model = response.model or model or self.default_model
        return response

    def interrupt_codex_interactive(
        self, text: str, *, callback=None, thinking_callback=None,
        turn_callback=None, block_callback=None, user_id: str = "",
        conversation_id: str = "", agent_name: str = "", model: str = "",
    ):
        from core.llm_client import LLMClientError, LLMResponse
        from services.cc_interactive_event_service import (
            get_or_create_cc_interactive_event_service)

        state = self._codex_interactive_session_state(
            user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)
        if not state:
            return LLMResponse(content="", model=model or self.default_model)
        pool = CodexInteractivePool.instance()
        pool.touch(state)
        _, _, event_service = get_or_create_cc_interactive_event_service()
        consumer_epoch = event_service.claim_consumer(state.session_token)
        event_service.drain_session(state.session_token)
        if not pool.send_interrupt(state, text):
            # Same as the send path: no coordinator will poll this claim.
            event_service.release_consumer(state.session_token, consumer_epoch)
            detail = getattr(state, "last_error", "") or "unknown tmux error"
            raise LLMClientError(
                "Failed to interrupt Codex interactive tmux session: "
                f"{detail}")
        coord = _CodexInteractiveTurnCoordinator(
            event_service, state.session_token, callback=callback,
            thinking_callback=thinking_callback,
            block_callback=block_callback, turn_callback=turn_callback,
            touch_callback=lambda: pool.touch(state),
            emitted_tool_use_ids=state.emitted_tool_use_ids,
            emitted_tool_result_ids=state.emitted_tool_result_ids,
            consumer_epoch=consumer_epoch,
            context_tokens_callback=lambda tokens: (
                self.record_observed_cli_context(
                    state.conversation_id, state.agent_name, tokens)))
        response = coord.run(getattr(self, "_abort", None))
        self.record_codex_context_window(
            pool, state, state.conversation_id, state.agent_name,
            coord.observed_context_tokens)
        response.model = response.model or model or self.default_model
        return response

    def _codex_interactive_session_state(
            self, *, user_id: str = "", conversation_id: str = "",
            agent_name: str = ""):
        uid = (user_id
               or getattr(self, "_codex_interactive_active_user_id", "")
               or getattr(self, "_user_id", "") or "")
        cid = (conversation_id
               or getattr(self, "_codex_interactive_active_conversation_id", "")
               or getattr(self, "_conversation_id", "") or "")
        agent = (agent_name
                 or getattr(self, "_codex_interactive_active_agent_name", "")
                 or getattr(self, "_agent_name", "") or "")
        service_id = (
            getattr(self, "_codex_interactive_active_service_id", "")
            or getattr(self, "_agent_service", "") or "")
        if not uid or not cid or not agent:
            return None
        return CodexInteractivePool.instance().find_session(
            uid, cid, agent, service_id)

    def _codex_interactive_send_user_message(
            self, text: str, attachments: list = None, **kwargs):
        state = self._codex_interactive_session_state(
            user_id=kwargs.get("user_id") or "",
            conversation_id=kwargs.get("conversation_id") or "",
            agent_name=kwargs.get("agent_name") or "")
        if not state:
            return False
        prompt = self._cci_preempt_prompt(
            text, attachments or [], state,
            kwargs.get("user_id") or "",
            kwargs.get("conversation_id") or "",
            kwargs.get("agent_name") or "")
        ok = CodexInteractivePool.instance().send_interrupt(state, prompt)
        if ok:
            self._had_preempts_this_turn = True
        return ok

    def cancel_codex_interactive(self, force: bool = False):
        if not force:
            return False
        state = self._codex_interactive_session_state()
        if not state:
            return False
        return CodexInteractivePool.instance().force_stop(state)
