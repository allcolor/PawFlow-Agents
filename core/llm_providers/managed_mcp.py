"""LLM provider facade for the managed MCP CLI providers.

``cc_mcp`` and ``codex_mcp`` drive the official Claude Code / Codex TUIs
through the same managed pools, tmux input path, prompt builders and PawFlow
MCP bridge as ``claude-code-interactive`` / ``codex-interactive``. The only
substitution is observation: the pools launch the CLI in ``managed_mcp`` mode
(no proxy, no CA, no vendor host redirection) and the turn completes on the
CLI's native ``Stop`` hook through :class:`_ManagedMcpTurnCoordinator`.

``agy_mcp`` is probe-gated and refuses every turn with a typed error until the
official ``agy`` build proves a final-answer source (see the spec table).

Thin entrypoints only: this module holds no process, transport or vendor
logic of its own.
"""

from __future__ import annotations

import logging
import time
import uuid

from core.llm_providers._managed_mcp_turn import _ManagedMcpTurnCoordinator
from core.llm_providers.claude_code_interactive import LLMClaudeCodeInteractiveMixin
from core.managed_mcp_spec import (
    MANAGED_MCP_PROVIDERS,
    ManagedMcpProviderSpec,
    managed_mcp_capability_matrix,
    managed_mcp_spec,
)

logger = logging.getLogger(__name__)


class LLMManagedMcpMixin:
    """``cc_mcp`` / ``codex_mcp`` / ``agy_mcp`` provider methods."""

    # Prompt, attachment and catch-up rendering are provider-neutral and
    # already shared by the Codex mixin the same way.
    _cci_prompt = LLMClaudeCodeInteractiveMixin._cci_prompt
    _cci_catchup_context = LLMClaudeCodeInteractiveMixin._cci_catchup_context
    _cci_live_text = LLMClaudeCodeInteractiveMixin._cci_live_text
    _cci_materialize_images = (
        LLMClaudeCodeInteractiveMixin._cci_materialize_images)
    _cci_attachment_block = staticmethod(
        LLMClaudeCodeInteractiveMixin._cci_attachment_block)
    _cci_preempt_prompt = LLMClaudeCodeInteractiveMixin._cci_preempt_prompt

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def managed_mcp_capabilities() -> dict:
        """Honest capability matrix for UI/API consumers."""
        return managed_mcp_capability_matrix()

    @staticmethod
    def _managed_mcp_require(provider: str) -> ManagedMcpProviderSpec:
        from core.llm_client import LLMClientError
        spec = managed_mcp_spec(provider)
        if spec is None:
            raise LLMClientError(f"{provider!r} is not a managed MCP provider")
        if not spec.available:
            # Explicit refusal, never a fallback to the MITM twin.
            raise LLMClientError(spec.unavailable_reason
                                 or f"{provider} is not available")
        return spec

    @staticmethod
    def _managed_mcp_pool(spec: ManagedMcpProviderSpec):
        if spec.pool_family == "codex-interactive":
            from core.codex_interactive_pool import CodexInteractivePool
            return CodexInteractivePool.instance()
        if spec.pool_family == "claude-code-interactive":
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            return InteractiveClaudeCodePool.instance()
        from core.llm_client import LLMClientError
        raise LLMClientError(
            f"{spec.provider}: no managed pool for {spec.pool_family}")

    def _managed_mcp_identity(self, call_user_id, call_conversation_id,
                              call_agent_name, provider: str):
        from core.llm_client import LLMClientError
        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conversation_id = (call_conversation_id
                           or getattr(self, "_conversation_id", "") or "")
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or ""
        if not user_id or not conversation_id or not agent_name:
            raise LLMClientError(
                f"{provider} requires user_id, conversation_id and agent_name")
        return user_id, conversation_id, agent_name

    def _managed_mcp_remember_active(self, spec: ManagedMcpProviderSpec,
                                     user_id: str, conversation_id: str,
                                     agent_name: str) -> None:
        """Keep the family's session-lookup attributes populated.

        ``_cci_session_state`` / ``_codex_interactive_session_state`` read
        these; sharing them keeps force stop and terminal lookups on one
        code path per pool family.
        """
        prefix = ("_codex_interactive_active_"
                  if spec.pool_family == "codex-interactive"
                  else "_cci_active_")
        setattr(self, prefix + "user_id", user_id)
        setattr(self, prefix + "conversation_id", conversation_id)
        setattr(self, prefix + "agent_name", agent_name)
        setattr(self, prefix + "service_id",
                getattr(self, "_agent_service", "") or "")

    def _managed_mcp_session_state(self, spec: ManagedMcpProviderSpec, *,
                                   user_id: str = "", conversation_id: str = "",
                                   agent_name: str = ""):
        prefix = ("_codex_interactive_active_"
                  if spec.pool_family == "codex-interactive"
                  else "_cci_active_")
        uid = (user_id or getattr(self, prefix + "user_id", "")
               or getattr(self, "_user_id", "") or "")
        cid = (conversation_id or getattr(self, prefix + "conversation_id", "")
               or getattr(self, "_conversation_id", "") or "")
        agent = (agent_name or getattr(self, prefix + "agent_name", "")
                 or getattr(self, "_agent_name", "") or "")
        service_id = (getattr(self, prefix + "service_id", "")
                      or getattr(self, "_agent_service", "") or "")
        if not uid or not cid or not agent:
            return None
        state = self._managed_mcp_pool(spec).find_session(
            uid, cid, agent, service_id)
        if state is None:
            return None
        # A session of the MITM twin is never a managed session, whatever
        # the key says.
        if getattr(state, "provider", "") != spec.provider:
            return None
        return state

    def _managed_mcp_record_native_context(self, spec, state, conversation_id,
                                           agent_name, *, user_id="",
                                           event_cid="") -> None:
        """Codex only: publish the native rollout counter, never an estimate."""
        if spec.pool_family != "codex-interactive":
            return
        try:
            from core.llm_providers.codex_interactive import codex_rollout_context_usage
            used, _window = codex_rollout_context_usage(
                getattr(state, "workdir", ""),
                not_before=float(getattr(state, "created_at", 0.0) or 0.0))
            if used <= 0:
                return
            record = getattr(self, "record_codex_live_context", None)
            if record is not None:
                record(state, conversation_id, agent_name, used,
                       user_id=user_id, event_cid=event_cid)
        except Exception:
            logger.debug("[%s] native context record failed", spec.provider,
                         exc_info=True)

    def _managed_mcp_coordinator(self, spec, event_service, state, pool, *,
                                 callback, block_callback, turn_callback,
                                 consumer_epoch, started_at):
        return _ManagedMcpTurnCoordinator(
            event_service, state.session_token, provider=spec.provider,
            callback=callback, block_callback=block_callback,
            turn_callback=turn_callback,
            touch_callback=lambda: pool.touch(state),
            emitted_tool_use_ids=state.emitted_tool_use_ids,
            emitted_tool_result_ids=state.emitted_tool_result_ids,
            consumer_epoch=consumer_epoch,
            liveness_callback=lambda: pool.session_is_live(state.name),
            started_at=started_at)

    # -- turn --------------------------------------------------------------

    def _managed_mcp_stream(
        self, provider: str, messages, model, tools=None, callback=None,
        turn_callback=None, block_callback=None, *, call_user_id=None,
        call_conversation_id=None, call_agent_name=None, call_event_cid=None,
        call_ephemeral_stream=None,
    ):
        from core.llm_client import CCCompactDetected, LLMClientError
        from services.cc_interactive_event_service import (
            get_or_create_cc_interactive_event_service,
        )

        spec = self._managed_mcp_require(provider)
        user_id, conversation_id, agent_name = self._managed_mcp_identity(
            call_user_id, call_conversation_id, call_agent_name, provider)
        pool = self._managed_mcp_pool(spec)
        ephemeral = bool(
            call_ephemeral_stream if call_ephemeral_stream is not None
            else getattr(self, "_ephemeral_stream", False))
        pool_conversation_id = conversation_id
        if ephemeral:
            pool_conversation_id = (
                f"{conversation_id}__ephemeral_{uuid.uuid4().hex}")
        # One rule for every CLI: a launch is a cold start and gets the full
        # context; the pool calls back only when it is actually launching.
        state = pool.ensure_started(
            self, model or "", user_id, pool_conversation_id, agent_name,
            before_launch=None if ephemeral else (
                lambda: self._cli_require_cold_context(provider)))
        pool.begin_turn(state)
        try:
            if not ephemeral and getattr(state, "initial_context_loaded", False):
                self._cli_require_delta_context(provider)
            self._managed_mcp_remember_active(
                spec, user_id, conversation_id, agent_name)
            self._had_preempts_this_turn = False
            prompt = self._cci_prompt(
                messages, tools, state.workdir, state.container_workdir,
                user_id, conversation_id,
                initial_context=not state.initial_context_loaded,
                agent_name=agent_name, state=state)
            _, _, event_service = get_or_create_cc_interactive_event_service()
            consumer_epoch = event_service.claim_consumer(state.session_token)
            event_service.drain_session(state.session_token)
            started_at = time.time()
            if not pool.send_text(state, prompt):
                event_service.release_consumer(
                    state.session_token, consumer_epoch)
                detail = getattr(state, "last_error", "") or "unknown tmux error"
                raise LLMClientError(
                    f"Failed to paste prompt into {spec.label} tmux session: "
                    f"{detail}")
            state.initial_context_loaded = True
            submitted = getattr(state, "submitted_msg_ids", None)
            if submitted is None:
                submitted = set()
                state.submitted_msg_ids = submitted
            submitted.update(
                mid for mid in (
                    getattr(m, "msg_id", "")
                    for m in (messages or [])
                    if getattr(m, "role", "") == "user")
                if mid)
            try:
                coord = self._managed_mcp_coordinator(
                    spec, event_service, state, pool, callback=callback,
                    block_callback=block_callback, turn_callback=turn_callback,
                    consumer_epoch=consumer_epoch, started_at=started_at)
                response = coord.run(getattr(self, "_abort", None))
            except CCCompactDetected:
                pool.kill_session(
                    user_id, pool_conversation_id, agent_name,
                    getattr(state, "service_id", "") or "")
                raise
            finally:
                event_service.release_consumer(
                    state.session_token, consumer_epoch)
            self._managed_mcp_record_native_context(
                spec, state, conversation_id, agent_name, user_id=user_id,
                event_cid=call_event_cid or conversation_id)
            response.model = response.model or model or self.default_model
            return response
        finally:
            pool.end_turn(state)
            if ephemeral:
                pool.destroy_ephemeral(state)

    def _managed_mcp_interrupt(
        self, provider: str, text: str, *, callback=None, turn_callback=None,
        block_callback=None, user_id: str = "", conversation_id: str = "",
        agent_name: str = "", model: str = "",
    ):
        from core.llm_client import CCCompactDetected, LLMClientError, LLMResponse
        from services.cc_interactive_event_service import (
            get_or_create_cc_interactive_event_service,
        )

        spec = self._managed_mcp_require(provider)
        state = self._managed_mcp_session_state(
            spec, user_id=user_id, conversation_id=conversation_id,
            agent_name=agent_name)
        if not state:
            # Already gone (compact boundary): the interrupt's goal is met.
            # Force stop is never an error.
            logger.info(
                "[%s-interrupt] no active session for %s/%s \u2014 already "
                "stopped, treating interrupt as no-op",
                provider, conversation_id[:8], agent_name)
            return LLMResponse(content="", model=model or self.default_model)
        pool = self._managed_mcp_pool(spec)
        pool.begin_turn(state)
        try:
            _, _, event_service = get_or_create_cc_interactive_event_service()
            consumer_epoch = event_service.claim_consumer(state.session_token)
            event_service.drain_session(state.session_token)
            started_at = time.time()
            if not pool.send_interrupt(state, text):
                event_service.release_consumer(
                    state.session_token, consumer_epoch)
                detail = getattr(state, "last_error", "") or "unknown tmux error"
                raise LLMClientError(
                    f"Failed to send interrupt to {spec.label} tmux session: "
                    f"{detail}")
            try:
                coord = self._managed_mcp_coordinator(
                    spec, event_service, state, pool, callback=callback,
                    block_callback=block_callback, turn_callback=turn_callback,
                    consumer_epoch=consumer_epoch, started_at=started_at)
                response = coord.run(getattr(self, "_abort", None))
            except CCCompactDetected:
                pool.kill_session(
                    user_id, conversation_id, agent_name,
                    getattr(state, "service_id", "") or "")
                raise
            finally:
                event_service.release_consumer(
                    state.session_token, consumer_epoch)
            response.model = response.model or model or self.default_model
            return response
        finally:
            pool.end_turn(state)

    def _managed_mcp_cancel(self, provider: str, force: bool) -> bool:
        if not force:
            return False
        spec = managed_mcp_spec(provider)
        if spec is None or not spec.available:
            return False
        state = self._managed_mcp_session_state(spec)
        if not state:
            return False
        return self._managed_mcp_pool(spec).force_stop(state)

    def _managed_mcp_send_user_message(self, provider: str, text: str,
                                       attachments: list | None = None,
                                       **kwargs):
        """Live preemption is not advertised for managed providers yet.

        The server-owned request state cannot prove which final answers
        which prompt once two are in flight on one CLI (plan section 12), so
        the caller keeps the message queued for the next loop instead.
        """
        spec = managed_mcp_spec(provider)
        if spec is None or not spec.live_preempt:
            return False
        return False

    # -- concrete entrypoints (dispatch targets) ------------------------------

    def _stream_cc_mcp(
        self, messages, model, temperature=0.7, max_tokens=0, tools=None,
        callback=None, thinking_budget=0, thinking_callback=None,
        turn_callback=None, block_callback=None, *, call_user_id=None,
        call_conversation_id=None, call_agent_name=None, call_event_cid=None,
        call_ephemeral_stream=None,
    ):
        return self._managed_mcp_stream(
            "cc_mcp", messages, model, tools=tools, callback=callback,
            turn_callback=turn_callback, block_callback=block_callback,
            call_user_id=call_user_id, call_conversation_id=call_conversation_id,
            call_agent_name=call_agent_name, call_event_cid=call_event_cid,
            call_ephemeral_stream=call_ephemeral_stream)

    def _stream_codex_mcp(
        self, messages, model, temperature=0.7, max_tokens=0, tools=None,
        callback=None, thinking_budget=0, thinking_callback=None,
        turn_callback=None, block_callback=None, *, call_user_id=None,
        call_conversation_id=None, call_agent_name=None, call_event_cid=None,
        call_ephemeral_stream=None,
    ):
        return self._managed_mcp_stream(
            "codex_mcp", messages, model, tools=tools, callback=callback,
            turn_callback=turn_callback, block_callback=block_callback,
            call_user_id=call_user_id, call_conversation_id=call_conversation_id,
            call_agent_name=call_agent_name, call_event_cid=call_event_cid,
            call_ephemeral_stream=call_ephemeral_stream)

    def _stream_agy_mcp(
        self, messages, model, temperature=0.7, max_tokens=0, tools=None,
        callback=None, thinking_budget=0, thinking_callback=None,
        turn_callback=None, block_callback=None, *, call_user_id=None,
        call_conversation_id=None, call_agent_name=None, call_event_cid=None,
        call_ephemeral_stream=None,
    ):
        # Probe-gated: raises the typed unavailable error, never falls back.
        return self._managed_mcp_stream(
            "agy_mcp", messages, model, tools=tools, callback=callback,
            turn_callback=turn_callback, block_callback=block_callback,
            call_user_id=call_user_id, call_conversation_id=call_conversation_id,
            call_agent_name=call_agent_name, call_event_cid=call_event_cid,
            call_ephemeral_stream=call_ephemeral_stream)

    def interrupt_cc_mcp(self, text: str, **kwargs):
        return self._managed_mcp_interrupt("cc_mcp", text, **kwargs)

    def interrupt_codex_mcp(self, text: str, **kwargs):
        return self._managed_mcp_interrupt("codex_mcp", text, **kwargs)

    def interrupt_agy_mcp(self, text: str, **kwargs):
        return self._managed_mcp_interrupt("agy_mcp", text, **kwargs)

    def cancel_cc_mcp(self, force: bool = False):
        return self._managed_mcp_cancel("cc_mcp", force)

    def cancel_codex_mcp(self, force: bool = False):
        return self._managed_mcp_cancel("codex_mcp", force)

    def cancel_agy_mcp(self, force: bool = False):
        return self._managed_mcp_cancel("agy_mcp", force)

    def _cc_mcp_send_user_message(self, text: str,
                                  attachments: list | None = None, **kwargs):
        return self._managed_mcp_send_user_message(
            "cc_mcp", text, attachments, **kwargs)

    def _codex_mcp_send_user_message(self, text: str,
                                     attachments: list | None = None, **kwargs):
        return self._managed_mcp_send_user_message(
            "codex_mcp", text, attachments, **kwargs)

    def _agy_mcp_send_user_message(self, text: str,
                                   attachments: list | None = None, **kwargs):
        return self._managed_mcp_send_user_message(
            "agy_mcp", text, attachments, **kwargs)


__all__ = [
    "MANAGED_MCP_PROVIDERS",
    "LLMManagedMcpMixin",
    "managed_mcp_spec",
]
