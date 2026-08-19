"""Codex TUI provider with a transparent MITM-observed Responses stream."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import uuid

from core.codex_interactive_pool import CodexInteractivePool
from core.llm_providers._codex_interactive_turn import (
    _CodexInteractiveTurnCoordinator)
from core.llm_providers.claude_code_interactive import (
    LLMClaudeCodeInteractiveMixin)
from core.llm_providers.cli_shared import LLMCliSharedMixin

logger = logging.getLogger(__name__)


def codex_rollout_context_usage(workdir: str, *, not_before: float = 0.0,
                                thread_id: str = ""):
    """Return the latest native Codex prompt size and context window.

    Recent Codex versions no longer expose ``context left`` in the TUI footer,
    and their proxied Responses usage may describe only the current exchange.
    The session rollout's ``token_count`` event is the source Codex itself uses
    for its context display.  Read backwards so even a large rollout costs only
    the distance to its latest valid measurement.

    ``thread_id`` restricts the search to that thread's rollout.  A TUI workdir
    holds one live session, but app-server can resume several threads under the
    same workdir, and there the most-recently-touched file is not necessarily
    the thread that just answered.
    """
    sessions = Path(workdir or "") / ".codex" / "sessions"
    try:
        candidates = sorted(
            sessions.glob("**/rollout-*.jsonl"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True)
    except (OSError, ValueError):
        return 0, 0
    if thread_id:
        candidates = [path for path in candidates if thread_id in path.name]

    for path in candidates:
        try:
            if not_before and path.stat().st_mtime < float(not_before) - 2.0:
                continue
            with path.open("rb") as stream:
                stream.seek(0, 2)
                position = stream.tell()
                partial = b""
                while position > 0:
                    size = min(64 * 1024, position)
                    position -= size
                    stream.seek(position)
                    chunks = (stream.read(size) + partial).split(b"\n")
                    partial = chunks.pop(0) if position > 0 else b""
                    for raw in reversed(chunks):
                        if b'"token_count"' not in raw:
                            continue
                        try:
                            event = json.loads(raw)
                            payload = event.get("payload") or {}
                            if payload.get("type") != "token_count":
                                continue
                            info = payload.get("info") or {}
                            last = info.get("last_token_usage") or {}
                            used = max(0, int(last.get("input_tokens") or 0))
                            window = max(
                                0, int(info.get("model_context_window") or 0))
                        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                            continue
                        if used > 0:
                            return used, window
        except OSError:
            continue
    return 0, 0


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

    # Recording the measured prompt size is provider-neutral -- every observed
    # CLI feeds the same gauge from the same dict. One implementation, borrowed
    # like the prompt helpers above so a bare mixin still exposes it.
    record_observed_cli_context = LLMCliSharedMixin.record_observed_cli_context

    def _publish_codex_context_gauge(self, conversation_id: str,
                                     agent_name: str, user_id: str = "",
                                     event_cid: str = "") -> None:
        """Publish a native mid-turn measurement to UI and compact checks."""
        try:
            from core.conversation_event_bus import ConversationEventBus
            from tasks.ai.context_usage import (
                compute_context_usage, persist_context_usage,
                usage_event_payload)

            usage = compute_context_usage(
                conversation_id, agent_name, user_id=user_id,
                source="codex_interactive_token_count")
            if int(usage.get("max", 0) or 0) <= 0:
                return
            persist_context_usage(conversation_id, agent_name, usage)
            payload = usage_event_payload(usage)
            payload["live"] = True
            ConversationEventBus.instance().publish_event(
                event_cid or conversation_id, "message_meta", payload)
        except Exception:
            logger.debug(
                "[codex-interactive] live context gauge publish failed",
                exc_info=True)

    def record_codex_live_context(self, state, conversation_id: str,
                                  agent_name: str, fallback_tokens: int,
                                  *, user_id: str = "",
                                  event_cid: str = "") -> None:
        """Record and publish Codex's authoritative native context counter."""
        native_used, native_window = codex_rollout_context_usage(
            getattr(state, "workdir", ""),
            not_before=float(getattr(state, "created_at", 0.0) or 0.0))
        used = native_used or fallback_tokens
        self.record_observed_cli_context(conversation_id, agent_name, used)
        if native_window > 0 and conversation_id and agent_name:
            windows = getattr(
                self, "_cli_observed_context_window_by_stream", None)
            if not isinstance(windows, dict):
                windows = {}
                self._cli_observed_context_window_by_stream = windows
            windows[(conversation_id, agent_name)] = native_window
        self._publish_codex_context_gauge(
            conversation_id, agent_name, user_id=user_id,
            event_cid=event_cid)

    def record_codex_context_window(self, pool, state, conversation_id: str,
                                    agent_name: str, used_tokens: int) -> None:
        """Fallback: derive and store the context window from an older TUI.

        Current Codex rollouts report ``model_context_window`` directly. Older
        versions exposed only ``context left 74%`` in the status bar, so keep
        the derivation for sessions without a native rollout measurement.

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
        if previous > 0:
            return
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
        pool_conversation_id = conversation_id
        if ephemeral:
            pool_conversation_id = (
                f"{conversation_id}__ephemeral_{uuid.uuid4().hex}")
        state = pool.ensure_started(
            self, model or "", user_id, pool_conversation_id, agent_name,
            before_launch=None if ephemeral else (
                lambda: self._cli_require_cold_context("codex-interactive")))
        try:
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
                agent_name=agent_name, state=state)
            _, _, event_service = get_or_create_cc_interactive_event_service()
            consumer_epoch = event_service.claim_consumer(state.session_token)
            event_service.drain_session(state.session_token)
            if not pool.send_text(state, prompt):
                event_service.release_consumer(
                    state.session_token, consumer_epoch)
                detail = (
                    getattr(state, "last_error", "") or "unknown tmux error")
                raise LLMClientError(
                    "Failed to paste prompt into Codex interactive tmux session: "
                    f"{detail}")
            state.initial_context_loaded = True
            # Same dedup contract as the CCI provider: everything in
            # `messages` has been conveyed, never re-paste it.
            _submitted = getattr(state, "submitted_msg_ids", None)
            if _submitted is None:
                _submitted = set()
                state.submitted_msg_ids = _submitted
            _submitted.update(
                mid for mid in (
                    getattr(m, "msg_id", "")
                    for m in (messages or [])
                    if getattr(m, "role", "") == "user")
                if mid)

            try:
                coord = _CodexInteractiveTurnCoordinator(
                    event_service, state.session_token, callback=callback,
                    thinking_callback=thinking_callback,
                    block_callback=block_callback, turn_callback=turn_callback,
                    touch_callback=lambda: pool.touch(state),
                    emitted_tool_use_ids=state.emitted_tool_use_ids,
                    emitted_tool_result_ids=state.emitted_tool_result_ids,
                    consumer_epoch=consumer_epoch,
                    liveness_callback=lambda: pool.session_is_live(state.name),
                    context_tokens_callback=lambda tokens: (
                        self.record_codex_live_context(
                            state, conversation_id, agent_name, tokens,
                            user_id=user_id,
                            event_cid=call_event_cid or conversation_id)))
                response = coord.run(getattr(self, "_abort", None))
            finally:
                event_service.release_consumer(
                    state.session_token, consumer_epoch)
            self.record_codex_context_window(
                pool, state, conversation_id, agent_name,
                coord.observed_context_tokens)
            response.model = response.model or model or self.default_model
            return response
        finally:
            if ephemeral:
                pool.destroy_ephemeral(state)

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
        try:
            coord = _CodexInteractiveTurnCoordinator(
                event_service, state.session_token, callback=callback,
                thinking_callback=thinking_callback,
                block_callback=block_callback, turn_callback=turn_callback,
                touch_callback=lambda: pool.touch(state),
                emitted_tool_use_ids=state.emitted_tool_use_ids,
                emitted_tool_result_ids=state.emitted_tool_result_ids,
                consumer_epoch=consumer_epoch,
                liveness_callback=lambda: pool.session_is_live(state.name),
                context_tokens_callback=lambda tokens: (
                    self.record_codex_live_context(
                        state, conversation_id, agent_name, tokens,
                        user_id=user_id, event_cid=conversation_id)))
            response = coord.run(getattr(self, "_abort", None))
        finally:
            event_service.release_consumer(state.session_token, consumer_epoch)
        self.record_codex_context_window(
            pool, state, conversation_id, agent_name,
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
