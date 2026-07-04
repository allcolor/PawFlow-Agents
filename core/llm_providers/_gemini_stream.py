"""Gemini streaming turn (_stream_gemini) for LLMGeminiMixin.

Split out of gemini.py as a leaf mixin so the file stays <= 800 lines. The
method is moved verbatim; it calls LLMGeminiMixin/ACP helpers via the MRO.
"""
from __future__ import annotations

import logging
import os
import queue
import time
import uuid
from typing import Any, Dict, List, Optional

from core.llm_providers.gemini_session import recover_tokens_from_workdir
from core.llm_providers._gemini_acp import (
    _GeminiAcpCapacityError,
    _GeminiAcpProtocolError,
)

logger = logging.getLogger(__name__)


class _GeminiStreamMixin:
    """Gemini streaming turn for LLMGeminiMixin."""

    def _stream_gemini(
        self,
        messages,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 0,
        tools=None,
        callback=None,
        *,
        thinking_budget: int = 0,
        turn_callback=None,
        block_callback=None,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
    ):
        """Stream one Gemini ACP prompt into PawFlow callbacks."""
        from core.llm_client import LLMClientError, LLMResponse

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conv_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or "default"
        is_ephemeral = bool(call_ephemeral_stream if call_ephemeral_stream is not None
                            else getattr(self, "_ephemeral_stream", False))
        model = (model or "").strip()
        effort = self._gemini_acp_effort(thinking_budget, self._cfg("effort", ""))

        image_blocks = self._gemini_acp_extract_images(
            messages, user_id=user_id, conversation_id=conv_id)

        workdir = self._gemini_get_session_workdir(conv_id, agent_name, user_id)
        os.makedirs(workdir, exist_ok=True)
        container_dir = self._gemini_acp_container_dir(workdir)

        def _estimate_prompt_tokens(text: str) -> int:
            try:
                from core.token_counter import (
                    count_messages_tokens as _count_msgs,
                    resolve_token_multiplier as _resolve_mult,
                )
                mult = _resolve_mult(getattr(self, "_config_ref", None) or {})
                return _count_msgs([{"content": text or ""}], multiplier=mult)
            except Exception:
                fallback = int(len(text or "") / 3.5)
                logger.warning(
                    "[gemini-acp] count_messages_tokens failed, fell back to chars/3.5 -> %d",
                    fallback, exc_info=True)
                return fallback

        def _prompt_text_for_mode(mode: str) -> str:
            if str(mode or "").startswith("resume"):
                return self._gemini_acp_resume_text(messages)
            return self._gemini_acp_full_initial_text(messages, workdir, container_dir)

        store = None
        session_id = ""
        session_key = f"gemini_acp_session:{agent_name or 'default'}"
        session_version_key = f"gemini_acp_session_version:{agent_name or 'default'}"
        pool_key = f"gemini_acp_pool_idx:{agent_name or 'default'}"
        if conv_id and not is_ephemeral:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                session_id = store.get_extra(conv_id, session_key) or ""
                session_version = store.get_extra(conv_id, session_version_key) or ""
                if session_id and session_version != "2":
                    logger.info(
                        "[gemini-acp] clearing legacy stored session %s version=%s",
                        session_id[:12], session_version or "?")
                    store.set_extra(conv_id, session_key, "")
                    store.set_extra(conv_id, session_version_key, "")
                    session_id = ""
            except Exception:
                logger.debug("[gemini-acp] failed to restore session id", exc_info=True)

        prompt_mode = "resume" if session_id else "cold"
        initial_text = _prompt_text_for_mode(prompt_mode)

        resume_pool_idx = -1
        if session_id and conv_id and store is not None:
            try:
                resume_pool_idx = int(store.get_extra(conv_id, pool_key) or -1)
            except Exception:
                logger.debug("[gemini-acp] failed to restore pool index", exc_info=True)

        svc_id = getattr(self, "_agent_service", "") or ""
        live_reg = None
        live_key = None
        live_session = None
        owns_live_lock = False
        is_reuse = False
        mcp_servers: list = []
        internal_token = ""  # nosec B105
        proc = None
        container = None
        reuse_container = ""
        stderr_lines: queue.Queue[str] = queue.Queue(maxsize=200)

        if conv_id and not is_ephemeral:
            try:
                from core.gemini_live_registry import GeminiLiveRegistry
                live_reg = GeminiLiveRegistry.instance()
                _idle_ttl = getattr(self, "timeout", None)
                live_reg.ensure_sweeper(
                    idle_ttl_seconds=int(_idle_ttl) if _idle_ttl else None,
                    recover=recover_tokens_from_workdir)
                live_key = (user_id, conv_id, agent_name or "default", svc_id,
                            int(resume_pool_idx))
                live_session = live_reg.get(live_key)
                # Fallback ONLY when the stored pool slot is missing (extra
                # lost after restart/compact). A concrete resume_pool_idx that
                # misses means the slot changed on purpose (rotation, slot
                # removal) — reusing the old-slot container would resurrect
                # the previous account's session.
                if live_session is None and resume_pool_idx < 0:
                    compatible = live_reg.get_compatible(
                        user_id, conv_id, agent_name or "default", svc_id)
                    if compatible is not None:
                        live_key, live_session = compatible
                        try:
                            resume_pool_idx = int(live_key[4])
                        except Exception:
                            resume_pool_idx = -1
                        logger.info(
                            "[gemini-acp-live] restored live key conv=%s agent=%s service=%s pool_idx=%s session=%s",
                            conv_id[:8] or "?", agent_name or "default", svc_id or "default",
                            int(resume_pool_idx), (live_session.session_id or session_id)[:12] or "new")
                if live_session is not None and not live_session.is_container_alive():
                    live_reg.evict(live_key, "dead_container")
                    live_session = None
                if live_session is not None:
                    live_session.turn_lock.acquire()
                    owns_live_lock = True
                    if live_session.is_process_alive():
                        live_reg.touch(live_key)
                        is_reuse = True
                        proc = live_session.proc
                        container = live_session.container_name
                        internal_token = live_session.mcp_internal_token or ""
                        session_id = live_session.session_id or session_id
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        prompt_mode = "resume-live"
                        initial_text = _prompt_text_for_mode(prompt_mode)
                        logger.info(
                            "[gemini-acp-live] REUSE conv=%s agent=%s session=%s reuse=%d",
                            conv_id[:8] or "?", agent_name, session_id[:12],
                            live_session.reuse_count)
                    else:
                        reuse_container = live_session.container_name
                        container = reuse_container
                        internal_token = live_session.mcp_internal_token or ""
                        session_id = live_session.session_id or session_id
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        logger.warning(
                            "[gemini-acp-live] process dead but container alive; restarting ACP in container=%s",
                            reuse_container)
            except Exception:
                logger.debug("[gemini-acp-live] lookup failed", exc_info=True)
                live_reg = None
                live_key = None

        if not is_reuse:
            if session_id:
                logger.info(
                    "[gemini-acp-live] stored session %s has no live process; loading in fresh ACP process",
                    session_id[:12])
            self._gemini_setup_credentials(
                workdir, pool_index=resume_pool_idx,
                user_id=user_id, conversation_id=conv_id)
            if conv_id and store is not None and hasattr(self, "_current_pool_index"):
                try:
                    store.set_extra(conv_id, pool_key, self._current_pool_index)
                except Exception:
                    logger.debug("[gemini-acp] failed to persist pool index", exc_info=True)
            if live_reg is not None and conv_id and not is_ephemeral:
                try:
                    live_key = (user_id, conv_id, agent_name or "default", svc_id,
                                int(getattr(self, "_current_pool_index", resume_pool_idx)))
                except Exception:
                    live_key = None
            mcp_servers, internal_token = self._gemini_acp_mcp_servers(
                user_id=user_id, conversation_id=conv_id, agent_name=agent_name)
            self._gemini_acp_write_settings(
                workdir, model, effort, thinking_budget, temperature, max_tokens,
                mcp_servers=mcp_servers, mcp_cwd=container_dir)
        active_key = (user_id, conv_id, agent_name, time.time())
        text_parts: List[str] = []
        turn_text_parts: List[str] = []
        thinking_parts: List[str] = []
        stream_uniq = f"geminiacp-{uuid.uuid4().hex[:8]}"
        stream_tc_names: Dict[str, str] = {}
        stream_tc_display_names: Dict[str, str] = {}
        stream_tc_display_args: Dict[str, Any] = {}
        completed_tool_ids = set()
        started_tool_ids = set()
        deferred_tool_ids = set()
        usage_meta: Dict[str, Any] = {}
        loaded_session_replay_barrier = False
        self._had_preempts_this_turn = False
        self._gemini_acp_preempt_pending = 0
        self._gemini_acp_sent_preempt_texts = []

        def _flush_text():
            nonlocal turn_text_parts
            if not turn_text_parts:
                return
            text = "".join(turn_text_parts).strip()
            turn_text_parts = []
            if text and turn_callback:
                try:
                    if thinking_parts:
                        turn_callback(text, [], "".join(thinking_parts).strip())
                        thinking_parts.clear()
                    else:
                        turn_callback(text, [])
                except TypeError:
                    turn_callback(text, [])

        turn_failed = False
        opened_session_this_call = False
        try:
            if not is_reuse:
                proc, container = self._gemini_acp_start_process(
                    workdir, model, container_name=reuse_container,
                    user_id=user_id, conversation_id=conv_id,
                    agent_name=agent_name)
                self._gemini_acp_start_stderr_drain(proc, stderr_lines)
                logger.info("[gemini-acp] started ACP conv=%s agent=%s session=%s",
                            conv_id[:8] or "?", agent_name, session_id[:12] or "new")

                self._gemini_acp_start_stdout_drain(proc)
                init_result = self._gemini_acp_initialize(proc)
                self._gemini_acp_authenticate(proc)
                supports_load = bool(
                    ((init_result.get("agentCapabilities") or {}).get("loadSession")))
                if session_id and supports_load:
                    try:
                        self._gemini_acp_load_session(proc, session_id, container_dir, mcp_servers)
                        loaded_session_replay_barrier = True
                    except Exception as exc:
                        if not self._gemini_acp_stale_session_error(exc):
                            raise
                        logger.warning(
                            "[gemini-acp] stale session id %s; starting new session",
                            session_id[:12])
                        if conv_id and store is not None and not is_ephemeral:
                            try:
                                store.set_extra(conv_id, session_key, "")
                                store.set_extra(conv_id, session_version_key, "")
                            except Exception:
                                logger.debug("[gemini-acp] failed to clear stale session id", exc_info=True)
                        session_id = ""
                        prompt_mode = "cold-after-stale-session"
                        initial_text = _prompt_text_for_mode(prompt_mode)
                elif session_id and not supports_load:
                    session_id = ""
                    prompt_mode = "cold-no-load-session"
                    initial_text = _prompt_text_for_mode(prompt_mode)

                if not session_id:
                    logger.info("[gemini-acp] opening new session cwd=%s", container_dir)
                    result = self._gemini_acp_new_session(proc, container_dir, mcp_servers)
                    session_id = (result or {}).get("sessionId", "")
                    opened_session_this_call = True
                    logger.info("[gemini-acp] new session id=%s", session_id[:12] or "?")
            elif not session_id:
                raise LLMClientError("gemini ACP live session has no session id")
            if not session_id:
                raise LLMClientError("gemini ACP did not return a session id")

            prompt_tokens = _estimate_prompt_tokens(initial_text)
            logger.info(
                "[gemini-acp] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
                prompt_tokens, prompt_mode, len(messages), len(initial_text))
            prompt = self._gemini_acp_prompt_items(initial_text, image_blocks)
            active_state = {
                "proc": proc,
                "session_id": session_id,
                "workdir": workdir,
                "container_dir": container_dir,
                "started_at": time.time(),
            }
            lock = self._gemini_acp_ensure_lock()
            with lock:
                active = getattr(self, "_gemini_acp_active", None)
                if not isinstance(active, dict):
                    active = {}
                    self._gemini_acp_active = active
                active[active_key] = active_state

            if live_reg is not None and live_key is not None and not is_ephemeral:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=session_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=True,
                    )
                    logger.info(
                        "[gemini-acp-live] active conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, session_id[:12])
                except Exception:
                    logger.debug("[gemini-acp-live] active register failed", exc_info=True)

            logger.info(
                "[gemini-acp] sending prompt session=%s items=%d images=%d chars=%d",
                session_id[:12], len(prompt), len(image_blocks), len(initial_text))
            req_id = self._gemini_acp_next_id()
            self._gemini_acp_send(proc, {
                "jsonrpc": "2.0",
                "method": "session/prompt",
                "id": req_id,
                "params": {"sessionId": session_id, "prompt": prompt},
            })

            logger.info("[gemini-acp] prompt sent; waiting for ACP events")
            self._gemini_acp_log_stderr(stderr_lines)

            _prompt_activity_seen = False
            _preempt_prompt_active = False
            _skip_resume_replay = bool(loaded_session_replay_barrier and not is_reuse)
            _resume_replay_skipped = 0
            _last_acp_event_at = time.monotonic()
            _last_acp_event = "prompt_sent"
            while True:
                msg = self._gemini_acp_read_message(
                    proc, timeout_s=None, wait_log_s=15.0,
                    wait_context=lambda: (
                        f"session={session_id[:12]} req={req_id} "
                        f"last={_last_acp_event} "
                        f"idle={time.monotonic() - _last_acp_event_at:.1f}s"
                    ))
                _now_acp_event = time.monotonic()
                _gap_s = _now_acp_event - _last_acp_event_at
                if _gap_s >= 5.0:
                    logger.info(
                        "[gemini-acp][gap] %.1fs since %s before %s",
                        _gap_s, _last_acp_event,
                        self._gemini_acp_message_preview(msg))
                _last_acp_event_at = _now_acp_event
                _last_acp_event = self._gemini_acp_message_preview(msg)
                if msg is None:
                    raise _GeminiAcpProtocolError(
                        "gemini ACP exited before session/prompt completed")

                incoming_id = msg.get("id")
                if (incoming_id is not None
                        and incoming_id == active_state.get("preempt_req_id")
                        and incoming_id != req_id):
                    req_id = int(incoming_id)
                    active_state.pop("preempt_req_id", None)
                    _preempt_prompt_active = True
                    _prompt_activity_seen = True
                    logger.info("[gemini-acp-live] switched reader to preempt prompt id=%s", req_id)

                if incoming_id == req_id:
                    logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))
                    _prompt_activity_seen = True
                    if msg.get("error"):
                        capacity_message = self._gemini_acp_capacity_error(msg.get("error"))
                        if capacity_message:
                            raise _GeminiAcpCapacityError(
                                f"Gemini capacity exhausted: {capacity_message}")
                        raise _GeminiAcpProtocolError(
                            f"session/prompt failed: {msg.get('error')}")
                    result = msg.get("result") or {}
                    usage_meta = result.get("_meta") or result.get("meta") or {}
                    stop_reason = result.get("stopReason") or "end_turn"
                    if stop_reason in ("cancelled", "canceled"):
                        next_req_id = active_state.pop("preempt_req_id", None)
                        if next_req_id and next_req_id != req_id:
                            req_id = int(next_req_id)
                            _preempt_prompt_active = True
                            _prompt_activity_seen = False
                            logger.info(
                                "[gemini-acp-live] cancelled old prompt; waiting for preempt id=%s",
                                req_id)
                            continue
                        break
                    if _preempt_prompt_active:
                        sent = list(getattr(self, "_gemini_acp_sent_preempt_texts", []) or [])
                        pstatus = self._gemini_acp_check_preempt_in_history(workdir, sent)
                        if pstatus in ("done", "pending"):
                            self._had_preempts_this_turn = True
                            logger.info(
                                "[gemini-acp-live] preempt prompt completed (history=%s, count=%d)",
                                pstatus, len(sent))
                        else:
                            logger.info(
                                "[gemini-acp-live] preempt prompt completed but history status=%s; pending rescue may retrigger",
                                pstatus)
                        self._gemini_acp_preempt_pending = 0
                        self._gemini_acp_sent_preempt_texts = []
                    if stop_reason not in ("end_turn", "stop", "max_tokens"):
                        logger.info("[gemini-acp] prompt stopped: %s", stop_reason)
                    break

                if "id" in msg and msg.get("method"):
                    logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))
                    req_method = msg.get("method", "")
                    req_params = msg.get("params", {}) or {}
                    logger.info("[gemini-acp] client request during prompt: %s", req_method)
                    if req_method == "session/request_permission":
                        _prompt_activity_seen = True
                        outcome = self._gemini_acp_permission_result(req_params)
                        self._gemini_acp_send(proc, {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "result": outcome,
                        })
                    else:
                        self._gemini_acp_send(proc, {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32601, "message": "client method not implemented"},
                        })
                    continue

                method = msg.get("method", "")
                params = msg.get("params", {}) or {}
                if method != "session/update":
                    logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))
                    logger.info("[gemini-acp] ignored ACP message during prompt: %s", method or "?")
                    continue
                update = params.get("update", {}) or {}
                kind = update.get("sessionUpdate") or ""
                if _skip_resume_replay:
                    if kind == "available_commands_update":
                        _skip_resume_replay = False
                        if _resume_replay_skipped:
                            logger.info(
                                "[gemini-acp] skipped %d replayed session/load update(s)",
                                _resume_replay_skipped)
                        continue
                    _resume_replay_skipped += 1
                    continue
                logger.info("[gemini-acp][recv] %s", self._gemini_acp_message_preview(msg))

                if kind == "agent_message_chunk":
                    _prompt_activity_seen = True
                    delta = self._gemini_acp_content_text(update.get("content"))
                    if delta:
                        text_parts.append(delta)
                        turn_text_parts.append(delta)
                        if callback:
                            callback(delta)
                    continue

                if kind == "agent_thought_chunk":
                    _prompt_activity_seen = True
                    thought = self._gemini_acp_clean_thinking(
                        self._gemini_acp_content_text(update.get("content")))
                    if thought:
                        thinking_parts.append(thought)
                    continue

                _terminal_tool_statuses = ("completed", "failed", "cancelled", "canceled")

                def _emit_started_tool(
                    tc_id: str,
                    raw_name: str,
                    raw_input: dict,
                    update: dict,
                    result_text: str = "",
                    enqueue_live_mapping: bool = True,
                ) -> None:
                    stream_tc_names[tc_id] = raw_name
                    if enqueue_live_mapping:
                        self._gemini_acp_enqueue_live_tool_tc(
                            conv_id, agent_name, tc_id, raw_name, raw_input, update)
                    display_name, display_args = self._gemini_acp_display_tool_call(
                        raw_name, raw_input, result_text)
                    stream_tc_display_names[tc_id] = display_name
                    stream_tc_display_args[tc_id] = display_args
                    defer_wrapper = raw_name == "use_tool" and not raw_input and not result_text
                    if block_callback and not defer_wrapper:
                        block_callback("tool_use", {
                            "id": tc_id,
                            "name": display_name,
                            "arguments": display_args,
                            "thinking": "".join(thinking_parts).strip(),
                        })
                        thinking_parts.clear()
                        started_tool_ids.add(tc_id)
                    elif defer_wrapper:
                        deferred_tool_ids.add(tc_id)

                def _emit_finished_tool(
                    update: dict,
                    tc_id: str,
                    raw_name: str,
                    raw_input: dict,
                ) -> None:
                    result_text = self._gemini_acp_clean_tool_result_text(
                        self._gemini_acp_tool_result_text(update))
                    display_name, display_args = self._gemini_acp_display_tool_call(
                        stream_tc_names.get(tc_id) or raw_name, raw_input, result_text)
                    if display_name in ("use_tool", "mcp__pawflow__use_tool", "mcp_pawflow_use_tool"):
                        display_name = stream_tc_display_names.get(tc_id) or display_name
                        display_args = stream_tc_display_args.get(tc_id, display_args)
                    if tc_id not in started_tool_ids:
                        _emit_started_tool(
                            tc_id, raw_name, raw_input, update, result_text,
                            enqueue_live_mapping=False)
                        started_tool_ids.add(tc_id)
                        deferred_tool_ids.discard(tc_id)
                    completed_tool_ids.add(tc_id)
                    if block_callback:
                        block_callback("tool_result", {
                            "tc_id": tc_id,
                            "tool": display_name,
                            "result": result_text,
                        })

                if kind == "tool_call":
                    _prompt_activity_seen = True
                    if turn_text_parts:
                        _flush_text()
                    raw_id = update.get("toolCallId") or uuid.uuid4().hex[:8]
                    tc_id = f"{stream_uniq}:{raw_id}"
                    status = update.get("status") or ""
                    raw_name = self._gemini_acp_tool_name(update)
                    raw_input = self._gemini_acp_tool_arguments(update)
                    if not raw_input and raw_name == "use_tool":
                        raw_input = self._gemini_acp_history_tool_arguments(workdir, raw_id)
                    if status in _terminal_tool_statuses:
                        _emit_finished_tool(update, tc_id, raw_name, raw_input)
                    elif tc_id not in started_tool_ids:
                        _emit_started_tool(tc_id, raw_name, raw_input, update)
                    continue

                if kind == "tool_call_update":
                    _prompt_activity_seen = True
                    if turn_text_parts:
                        _flush_text()
                    raw_id = update.get("toolCallId") or ""
                    tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                    status = update.get("status") or ""
                    raw_name = self._gemini_acp_tool_name(update)
                    raw_input = self._gemini_acp_tool_arguments(update)
                    if not raw_input and raw_name == "use_tool" and raw_id:
                        raw_input = self._gemini_acp_history_tool_arguments(workdir, raw_id)
                    if tc_id and status in _terminal_tool_statuses:
                        _emit_finished_tool(update, tc_id, raw_name, raw_input)
                    elif tc_id and tc_id not in started_tool_ids:
                        _emit_started_tool(tc_id, raw_name, raw_input, update)
                    continue


            _flush_text()
            content = "".join(text_parts).strip()
            tokens_out = self._gemini_acp_output_tokens(usage_meta, content)
            if session_id and conv_id and store is not None and not is_ephemeral:
                try:
                    store.set_extra(conv_id, session_key, session_id)
                    store.set_extra(conv_id, session_version_key, "2")
                except Exception:
                    logger.debug("[gemini-acp] failed to persist session id", exc_info=True)
            return LLMResponse(
                content=content,
                model=model,
                tokens_in=max(0, int(prompt_tokens or 0)),
                tokens_out=max(0, int(tokens_out or 0)),
                finish_reason="stop",
                raw={"session_id": session_id, "tool_results": len(completed_tool_ids)},
                thinking="".join(thinking_parts).strip(),
            )
        except _GeminiAcpCapacityError as exc:
            turn_failed = True
            raise LLMClientError(str(exc)) from exc
        except _GeminiAcpProtocolError as exc:
            turn_failed = True
            raise LLMClientError(str(exc)) from exc
        except Exception:
            turn_failed = True
            raise
        finally:
            lock = self._gemini_acp_ensure_lock()
            with lock:
                active = getattr(self, "_gemini_acp_active", None)
                if isinstance(active, dict):
                    active.pop(active_key, None)
            try:
                self._gemini_recover_tokens(
                    workdir, user_id=user_id, conversation_id=conv_id)
            except Exception:
                logger.debug("[gemini-acp] token recovery failed", exc_info=True)

            proc_alive = False
            if proc is not None:
                try:
                    proc_alive = proc.poll() is None
                except Exception:
                    proc_alive = False
            keep_alive = (
                not turn_failed
                and proc_alive
                and live_reg is not None
                and live_key is not None
                and bool(session_id)
                and bool(container)
                and not is_ephemeral
            )
            if keep_alive:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=session_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=False,
                    )
                    logger.info(
                        "[gemini-acp-live] keep-alive conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, session_id[:12])
                except Exception:
                    logger.debug("[gemini-acp-live] register failed", exc_info=True)
                    keep_alive = False

            if not keep_alive:
                if turn_failed and opened_session_this_call and conv_id and store is not None and not is_ephemeral:
                    try:
                        if (store.get_extra(conv_id, session_key) or "") == session_id:
                            store.set_extra(conv_id, session_key, "")
                            store.set_extra(conv_id, session_version_key, "")
                    except Exception:
                        logger.debug("[gemini-acp] failed to clear failed fresh session", exc_info=True)
                if live_reg is not None and live_key is not None:
                    try:
                        live_reg.evict(live_key, "acp_teardown")
                    except Exception:
                        logger.debug("[gemini-acp-live] evict failed", exc_info=True)
                if proc is not None:
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                    except Exception:
                        logger.debug("[gemini-acp] terminate failed", exc_info=True)
                if internal_token:
                    try:
                        from core.internal_auth import revoke_token
                        revoke_token(internal_token)
                    except Exception:
                        logger.debug("[gemini-acp] internal token revoke failed", exc_info=True)
                self._gemini_pool_release(container)
            self._gemini_acp_log_stderr(stderr_lines)
            if owns_live_lock and live_session is not None:
                try:
                    live_session.turn_lock.release()
                except Exception:
                    logger.debug("[gemini-acp-live] turn lock release failed", exc_info=True)

