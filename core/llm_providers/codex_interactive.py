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
            consumer_epoch=consumer_epoch)
        response = coord.run(getattr(self, "_abort", None))
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
            consumer_epoch=consumer_epoch)
        response = coord.run(getattr(self, "_abort", None))
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

