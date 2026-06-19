"""Codex app-server provider — the streaming turn loop.

Extracted from core/llm_providers/codex_app_server.py for the <=800-line
rule (invariant 2: composed back via MRO into LLMCodexAppServerMixin).
"""
import logging
import os
import queue
import shutil
import subprocess  # nosec B404
import threading
import time
import uuid
from typing import Dict, List, Optional

from core.llm_providers.codex_session import (
    recover_tokens_from_workdir)
from core.llm_providers._codex_app_rpc import _CodexAppServerProtocolError  # noqa: F401

logger = logging.getLogger(__name__)


class _CodexAppStreamMixin:
    """The streaming turn loop (MRO mixin)."""

    def _stream_codex_app_server(
        self,
        messages,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 0,
        tools=None,
        callback=None,
        *,
        thinking_budget: int = 0,
        thinking_callback=None,
        turn_callback=None,
        block_callback=None,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
    ):
        """Stream one Codex app-server turn into PawFlow callbacks."""
        from core.llm_client import LLMClientError, LLMResponse
        from tasks.ai.agent_exceptions import AgentCancelled

        if getattr(self, "_abort", None) and self._abort.is_set():
            raise AgentCancelled()

        self._codex_app_turn_completed_for_callback = False

        user_id = call_user_id or getattr(self, "_user_id", "") or ""
        conv_id = call_conversation_id or getattr(self, "_conversation_id", "") or ""
        agent_name = call_agent_name or getattr(self, "_agent_name", "") or "default"
        is_ephemeral = bool(call_ephemeral_stream if call_ephemeral_stream is not None
                            else getattr(self, "_ephemeral_stream", False))
        model = (model or "").strip()
        effort = self._codex_app_effort(
            thinking_budget, self._cfg("effort", ""))
        reasoning_summary = self._codex_app_reasoning_summary(effort)

        # Mutates message content to remove image blocks from text history.
        image_blocks = self._codex_app_extract_images(
            messages, user_id=user_id, conversation_id=conv_id)

        store = None
        thread_id = ""
        if conv_id and not is_ephemeral:
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                thread_id = store.get_extra(
                    conv_id, f"codex_app_server_thread:{agent_name or 'default'}") or ""
            except Exception:
                logger.debug("[codex-app] failed to restore thread id", exc_info=True)

        workdir = self._codex_get_session_workdir(conv_id, agent_name, user_id)
        os.makedirs(workdir, exist_ok=True)
        container_dir = self._codex_app_container_dir(workdir)

        if is_ephemeral:
            prompt_mode = "ephemeral"
            initial_text = self._codex_app_resume_text(messages)
        elif thread_id:
            prompt_mode = "resume"
            initial_text = self._codex_app_resume_text(messages)
        else:
            prompt_mode = "cold"
            initial_text = self._codex_app_full_initial_text(messages, workdir, container_dir)
        prompt_tokens = self._codex_app_estimate_prompt_tokens(initial_text)
        logger.info(
            "[codex-app] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
            prompt_tokens, prompt_mode, len(messages), len(initial_text))

        resume_pool_idx = -1
        if thread_id and conv_id and store is not None:
            try:
                resume_pool_idx = int(store.get_extra(
                    conv_id, f"codex_app_pool_idx:{agent_name or 'default'}") or -1)
            except Exception:
                logger.debug("[codex-app] failed to restore pool index", exc_info=True)

        svc_id = getattr(self, "_agent_service", "") or ""
        live_reg = None
        live_key = None
        live_session = None
        owns_live_lock = False
        is_reuse = False
        reuse_container = ""
        internal_token = ""  # nosec B105
        proc = None
        container = None
        stderr_lines: queue.Queue[str] = queue.Queue(maxsize=200)
        _first_event_timer = None
        _first_event_done = None

        if conv_id and not is_ephemeral:
            try:
                from core.codex_live_registry import CodexLiveRegistry
                live_reg = CodexLiveRegistry.instance()
                _idle_ttl = getattr(self, "timeout", None)
                live_reg.ensure_sweeper(
                    idle_ttl_seconds=int(_idle_ttl) if _idle_ttl else None,
                    recover=recover_tokens_from_workdir)
                live_key = (user_id, conv_id, agent_name or "default", svc_id,
                            int(resume_pool_idx))
                live_session = live_reg.get(live_key)
                if live_session is None:
                    compatible = live_reg.get_compatible(
                        user_id, conv_id, agent_name or "default", svc_id)
                    if compatible is not None:
                        live_key, live_session = compatible
                        try:
                            resume_pool_idx = int(live_key[4])
                        except Exception:
                            resume_pool_idx = -1
                        logger.info(
                            "[codex-app-live] restored live key conv=%s agent=%s service=%s pool_idx=%s thread=%s",
                            conv_id[:8] or "?", agent_name or "default", svc_id or "default",
                            int(resume_pool_idx), (live_session.session_id or thread_id)[:12] or "new")
                if live_session is None:
                    logger.info(
                        "[codex-app-live] cold-start conv=%s agent=%s service=%s pool_idx=%s thread=%s",
                        conv_id[:8] or "?", agent_name or "default", svc_id or "default",
                        int(resume_pool_idx), thread_id[:12] or "new")
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
                        thread_id = live_session.session_id or thread_id
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        logger.info(
                            "[codex-app-live] REUSE conv=%s agent=%s session=%s reuse=%d",
                            conv_id[:8] or "?", agent_name, thread_id[:12],
                            live_session.reuse_count)
                    else:
                        reuse_container = live_session.container_name or ""
                        container = reuse_container
                        thread_id = live_session.session_id or thread_id
                        internal_token = live_session.mcp_internal_token or ""
                        if getattr(live_session, "event_q", None) is not None:
                            stderr_lines = live_session.event_q
                        if resume_pool_idx >= 0:
                            self._current_pool_index = resume_pool_idx
                        logger.warning(
                            "[codex-app-live] process dead but container alive; "
                            "restarting app-server in container=%s conv=%s agent=%s session=%s",
                            reuse_container, conv_id[:8] or "?", agent_name,
                            thread_id[:12] or "new")
            except Exception:
                logger.debug("[codex-app-live] lookup failed", exc_info=True)
                live_reg = None
                live_key = None

        if not is_reuse:
            self._codex_setup_credentials(
                workdir, pool_index=resume_pool_idx,
                user_id=user_id, conversation_id=conv_id)
            if conv_id and store is not None and hasattr(self, "_current_pool_index"):
                try:
                    store.set_extra(conv_id, f"codex_app_pool_idx:{agent_name or 'default'}",
                                    self._current_pool_index)
                except Exception:
                    logger.debug("[codex-app] failed to persist pool index", exc_info=True)
            if live_reg is not None and conv_id and not is_ephemeral:
                try:
                    live_key = (user_id, conv_id, agent_name or "default", svc_id,
                                int(getattr(self, "_current_pool_index", resume_pool_idx)))
                except Exception:
                    live_key = None
            _, internal_token = self._codex_setup_mcp_config(
                workdir, user_id=user_id, conversation_id=conv_id, agent_name=agent_name)

        active_key = (user_id, conv_id, agent_name, time.time())
        text_parts: List[str] = []
        turn_text_parts: List[str] = []
        turn_text_is_final = False
        final_text_parts: List[str] = []
        thinking_parts: List[str] = []
        live_thinking_parts: List[str] = []
        emitted_thinking_parts: List[str] = []
        last_thinking_emit = 0.0
        stream_uniq = f"codexapp-{uuid.uuid4().hex[:8]}"
        stream_tc_names: Dict[str, str] = {}
        stream_tc_started: Dict[str, float] = {}
        completed_tool_ids = set()
        self._had_preempts_this_turn = False
        self._codex_app_preempt_pending = 0
        self._codex_app_sent_preempt_texts = []
        self._codex_app_native_tool_hint_sent = False

        def _flush_text():
            nonlocal turn_text_parts, turn_text_is_final
            if not turn_text_parts:
                return
            if not turn_text_is_final:
                logger.warning(
                    "[codex-app] dropping non-final assistant delta text; waiting for completed item (delta_len=%d)",
                    len("".join(turn_text_parts)),
                )
                turn_text_parts = []
                return
            text = "".join(turn_text_parts).strip()
            turn_text_parts = []
            turn_text_is_final = False
            if text and turn_callback:
                try:
                    if thinking_parts:
                        turn_callback(text, [], "".join(thinking_parts).strip())
                        thinking_parts.clear()
                    else:
                        turn_callback(text, [])
                except TypeError:
                    turn_callback(text, [])

        def _flush_live_thinking(force: bool = False) -> None:
            nonlocal last_thinking_emit
            if not live_thinking_parts:
                return
            now = time.time()
            text = "".join(live_thinking_parts)
            if not text.strip():
                live_thinking_parts.clear()
                return
            if not force:
                if len(text) < 160:
                    return
                if now - last_thinking_emit < 4.0:
                    return
            live_thinking_parts.clear()
            last_thinking_emit = now
            if thinking_callback:
                thinking_callback(text)

        turn_failed = False
        self._codex_app_compact_hard_killed = False

        try:
            thread_key = f"codex_app_server_thread:{agent_name or 'default'}"
            _phase_t0 = time.monotonic()
            if not is_reuse:
                proc, container = self._codex_pool_popen(
                    workdir,
                    ["app-server"],
                    container_name=reuse_container,
                    user_id=user_id,
                    conversation_id=conv_id,
                    agent_name=agent_name,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                self._codex_app_start_stderr_drain(proc, stderr_lines)
                _spawn_ms = (time.monotonic() - _phase_t0) * 1000.0
                logger.info("[codex-app] started app-server conv=%s agent=%s thread=%s",
                            conv_id[:8] or "?", agent_name, thread_id[:12] or "new")

                _phase_t0 = time.monotonic()
                self._codex_app_initialize(proc, stderr_lines=stderr_lines)
                _init_ms = (time.monotonic() - _phase_t0) * 1000.0
                if thread_id:
                    try:
                        _phase_t0 = time.monotonic()
                        thread = self._codex_app_resume_thread(proc, thread_id, model)
                        _thread_ms = (time.monotonic() - _phase_t0) * 1000.0
                    except Exception as exc:
                        if not self._codex_app_missing_rollout_error(exc):
                            raise
                        logger.warning(
                            "[codex-app] stale thread id %s; starting a new thread", thread_id[:12])
                        if conv_id and store is not None and not is_ephemeral:
                            try:
                                store.set_extra(conv_id, thread_key, "")
                            except Exception:
                                logger.debug("[codex-app] failed to clear stale thread id", exc_info=True)
                        thread_id = ""
                        prompt_mode = "cold-after-stale-resume"
                        initial_text = self._codex_app_full_initial_text(
                            messages, workdir, container_dir)
                        prompt_tokens = self._codex_app_estimate_prompt_tokens(initial_text)
                        logger.info(
                            "[codex-app] gauge: prompt_tokens=%d mode=%s (msgs=%d, input=%d chars)",
                            prompt_tokens, prompt_mode, len(messages), len(initial_text))
                        _phase_t0 = time.monotonic()
                        thread = self._codex_app_start_thread(proc, model, container_dir)
                        _thread_ms = (time.monotonic() - _phase_t0) * 1000.0
                else:
                    _phase_t0 = time.monotonic()
                    thread = self._codex_app_start_thread(proc, model, container_dir)
                    _thread_ms = (time.monotonic() - _phase_t0) * 1000.0
                if max(_spawn_ms, _init_ms, _thread_ms) >= 500.0:
                    logger.info(
                        "[codex-app] startup timing conv=%s agent=%s spawn=%.1fms "
                        "initialize=%.1fms thread=%.1fms mode=%s",
                        conv_id[:8] or "?", agent_name, _spawn_ms, _init_ms,
                        _thread_ms, prompt_mode)
                thread_id = (thread or {}).get("id", "") or thread_id
                if thread_id and conv_id and store is not None and not is_ephemeral:
                    try:
                        store.set_extra(conv_id, thread_key, thread_id)
                    except Exception:
                        logger.debug("[codex-app] failed to persist thread id", exc_info=True)
            elif not thread_id:
                raise LLMClientError("codex app-server live session has no thread id")
            if not thread_id:
                raise LLMClientError("codex app-server did not return a thread id")

            input_items = self._codex_app_input_items(
                initial_text, image_blocks, workdir, container_dir)
            _phase_t0 = time.monotonic()
            turn = self._codex_app_start_turn(
                proc, thread_id, input_items, model, container_dir,
                effort, reasoning_summary)
            _turn_start_ms = (time.monotonic() - _phase_t0) * 1000.0
            if _turn_start_ms >= 500.0:
                logger.info(
                    "[codex-app] turn/start timing conv=%s agent=%s mode=%s "
                    "prompt_tokens=%d input_chars=%d ms=%.1f",
                    conv_id[:8] or "?", agent_name, prompt_mode,
                    int(prompt_tokens or 0), len(initial_text or ""),
                    _turn_start_ms)
            turn_id = (turn or {}).get("id", "")

            lock = self._codex_app_ensure_lock()
            with lock:
                active = getattr(self, "_codex_app_active", None)
                if not isinstance(active, dict):
                    active = {}
                    self._codex_app_active = active
                active[active_key] = {
                    "proc": proc,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "workdir": workdir,
                    "container_dir": container_dir,
                    "user_id": user_id,
                    "conversation_id": conv_id,
                    "agent_name": agent_name,
                    "started_at": time.time(),
                }

            # Surface LIVE while the app-server turn is running, not only
            # after the turn completes. Preempt already uses _codex_app_active;
            # the UI badge comes from CodexLiveRegistry.status().
            if live_reg is not None and live_key is not None and not is_ephemeral:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=thread_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=True,
                    )
                    logger.info(
                        "[codex-app-live] active conv=%s agent=%s session=%s turn=%s",
                        conv_id[:8] or "?", agent_name, thread_id[:12],
                        turn_id[:12])
                except Exception:
                    logger.debug("[codex-app-live] active register failed", exc_info=True)

            _turn_wait_started = time.monotonic()
            _first_event_seen = False
            _last_event_at = _turn_wait_started
            _first_event_done = threading.Event()

            def _warn_slow_first_event() -> None:
                if _first_event_done.is_set():
                    return
                logger.warning(
                    "[codex-app] waiting %.1fs for first event after turn/start "
                    "conv=%s agent=%s mode=%s prompt_tokens=%d input_chars=%d "
                    "thread=%s turn=%s",
                    time.monotonic() - _turn_wait_started,
                    conv_id[:8] or "?", agent_name, prompt_mode,
                    int(prompt_tokens or 0), len(initial_text or ""),
                    thread_id[:12], turn_id[:12])
                self._codex_app_log_stderr(stderr_lines)

            _first_event_timer = threading.Timer(10.0, _warn_slow_first_event)
            _first_event_timer.daemon = True
            _first_event_timer.start()
            while True:
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                msg = self._codex_app_read_message(proc)
                if getattr(self, "_abort", None) and self._abort.is_set():
                    raise AgentCancelled()
                if msg is None:
                    break
                _now_evt = time.monotonic()
                if "id" in msg:
                    # Late response to turn/steer or server request resolution.
                    if msg.get("error"):
                        raise _CodexAppServerProtocolError(str(msg.get("error")))
                    continue
                method = msg.get("method", "")
                params = msg.get("params", {}) or {}
                is_useful_stream_event = (
                    method.startswith("item/")
                    or method in ("turn/completed", "turn/failed")
                )
                if is_useful_stream_event:
                    if not _first_event_seen:
                        _first_event_seen = True
                        _first_event_done.set()
                        _first_event_timer.cancel()
                        _first_ms = (_now_evt - _turn_wait_started) * 1000.0
                        if _first_ms >= 1000.0:
                            logger.info(
                                "[codex-app] first useful stream event after turn/start %.1fms "
                                "method=%s conv=%s agent=%s mode=%s prompt_tokens=%d "
                                "input_chars=%d thread=%s turn=%s",
                                _first_ms, method, conv_id[:8] or "?", agent_name,
                                prompt_mode, int(prompt_tokens or 0),
                                len(initial_text or ""), thread_id[:12], turn_id[:12])
                    elif (_now_evt - _last_event_at) >= 10.0:
                        logger.info(
                            "[codex-app] stream event gap %.1fms method=%s conv=%s agent=%s turn=%s",
                            (_now_evt - _last_event_at) * 1000.0, method,
                            conv_id[:8] or "?", agent_name, turn_id[:12])
                    _last_event_at = _now_evt

                if method == "item/agentMessage/delta":
                    delta = params.get("delta") or params.get("text") or ""
                    if delta:
                        text_parts.append(delta)
                        turn_text_parts.append(delta)
                        turn_text_is_final = False
                        if callback:
                            callback(delta)
                    continue

                if method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
                    delta = params.get("delta") or params.get("text") or ""
                    if delta:
                        thinking_parts.append(delta)
                        live_thinking_parts.append(delta)
                        _flush_live_thinking()
                    continue

                if method == "item/completed":
                    item = params.get("item", {}) or {}
                    if (item.get("type") in ("message", "agentMessage")
                            and item.get("role", "assistant") == "assistant"):
                        final_text = self._codex_app_payload_text(item).strip()
                        if final_text:
                            delta_text = "".join(turn_text_parts).strip()
                            if delta_text and delta_text != final_text:
                                logger.warning(
                                    "[codex-app] assistant delta/final mismatch; "
                                    "using completed item as source of truth "
                                    "delta_len=%d final_len=%d",
                                    len(delta_text), len(final_text))
                            turn_text_parts = [final_text]
                            turn_text_is_final = True
                            final_text_parts.append(final_text)
                        continue
                    if item.get("type") == "reasoning":
                        summary = item.get("summary") or []
                        content = item.get("content") or []
                        if isinstance(summary, (str, dict)):
                            summary = [summary]
                        if isinstance(content, (str, dict)):
                            content = [content]
                        for part in list(summary) + list(content):
                            if isinstance(part, dict):
                                text = part.get("text") or part.get("summary") or ""
                            else:
                                text = str(part or "")
                            self._codex_app_append_final_reasoning(text, emitted_thinking_parts=emitted_thinking_parts, thinking_parts=thinking_parts)
                        continue

                if method == "item/started":
                    item = params.get("item", {}) or {}
                    if item.get("type") == "contextCompaction":
                        from core.llm_client import CCCompactDetected
                        logger.warning(
                            "[codex-app] contextCompaction detected — handing compaction to PawFlow")
                        self._codex_app_hard_kill_for_context_compaction("item/started", conv_id=conv_id, store=store, is_ephemeral=is_ephemeral, agent_name=agent_name, active_key=active_key, live_reg=live_reg, live_key=live_key, proc=proc, container=container, internal_token=internal_token)
                        raise CCCompactDetected(
                            "Codex app-server contextCompaction detected")
                    if item.get("type") in ("commandExecution", "fileChange", "dynamicToolCall"):
                        _flush_live_thinking(force=True)
                        if block_callback and turn_text_parts:
                            _flush_text()
                        tc_id = f"{stream_uniq}:{item.get('id') or uuid.uuid4().hex[:8]}"
                        native_name = self._codex_app_native_tool_name(item)
                        self._codex_app_send_native_tool_hint(native_name, proc=proc, thread_id=thread_id, turn_id=turn_id, workdir=workdir, container_dir=container_dir)
                        stream_tc_names[tc_id] = native_name
                        if block_callback:
                            block_callback("tool_use", {
                                "id": tc_id,
                                "name": native_name,
                                "arguments": self._codex_app_native_tool_args(item),
                                "thinking": "".join(thinking_parts).strip(),
                                "tool_origin": "native",
                            })
                            thinking_parts.clear()
                        continue
                    if item.get("type") == "mcpToolCall":
                        _flush_live_thinking(force=True)
                        if block_callback and turn_text_parts:
                            _flush_text()
                        tc_id = f"{stream_uniq}:{item.get('id') or uuid.uuid4().hex[:8]}"
                        raw_name = item.get("tool") or "use_tool"
                        raw_args = item.get("arguments") or {}
                        stream_tc_names[tc_id] = raw_name
                        stream_tc_started[tc_id] = time.perf_counter()
                        logger.info(
                            "[codex-app] timing mcpToolCall started tc_id=%s raw_id=%s tool=%s conv=%s agent=%s",
                            tc_id, item.get("id") or "", raw_name,
                            conv_id[:8] or "?", agent_name or "")
                        try:
                            from core.llm_client import unwrap_mcp_tool
                            tc_name, tc_args = unwrap_mcp_tool(raw_name, raw_args)
                        except Exception:
                            tc_name, tc_args = raw_name, raw_args
                        try:
                            from core.background_tool import enqueue_cc_tc, _args_hash
                            enqueue_cc_tc(conv_id, agent_name, tc_id, tc_name, _args_hash(tc_args))
                        except Exception:
                            logger.debug("[codex-app] enqueue background tc skipped", exc_info=True)
                        if block_callback:
                            block_callback("tool_use", {
                                "id": tc_id,
                                "name": tc_name,
                                "arguments": tc_args,
                                "thinking": "".join(thinking_parts).strip(),
                                "tool_origin": "mcp",
                            })
                            thinking_parts.clear()
                        continue

                if method == "item/completed":
                    item = params.get("item", {}) or {}
                    if item.get("type") == "contextCompaction":
                        from core.llm_client import CCCompactDetected
                        logger.warning(
                            "[codex-app] contextCompaction completed before interception — compacting PawFlow context")
                        self._codex_app_hard_kill_for_context_compaction("item/completed", conv_id=conv_id, store=store, is_ephemeral=is_ephemeral, agent_name=agent_name, active_key=active_key, live_reg=live_reg, live_key=live_key, proc=proc, container=container, internal_token=internal_token)
                        raise CCCompactDetected(
                            "Codex app-server contextCompaction completed")
                    if item.get("type") in ("commandExecution", "fileChange", "dynamicToolCall"):
                        raw_id = item.get("id") or ""
                        tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                        if not tc_id:
                            continue
                        native_name = stream_tc_names.get(tc_id) or self._codex_app_native_tool_name(item)
                        completed_tool_ids.add(tc_id)
                        if block_callback:
                            block_callback("tool_result", {
                                "tc_id": tc_id,
                                "tool": native_name,
                                "result": self._codex_app_native_tool_result(item),
                                "tool_origin": "native",
                            })
                        continue
                    if item.get("type") == "mcpToolCall":
                        raw_id = item.get("id") or ""
                        tc_id = f"{stream_uniq}:{raw_id}" if raw_id else ""
                        if not tc_id:
                            continue
                        raw_name = stream_tc_names.get(tc_id) or item.get("tool") or ""
                        result_str = self._codex_app_result_text(item)
                        completed_tool_ids.add(tc_id)
                        started = stream_tc_started.pop(tc_id, 0.0)
                        provider_ms = ((time.perf_counter() - started) * 1000
                                       if started else 0.0)
                        logger.info(
                            "[codex-app] timing mcpToolCall completed tc_id=%s raw_id=%s tool=%s provider_ms=%.1f result_len=%d",
                            tc_id, raw_id, raw_name, provider_ms,
                            len(result_str))
                        if block_callback:
                            block_callback("tool_result", {
                                "tc_id": tc_id,
                                "tool": raw_name,
                                "result": result_str,
                                "tool_origin": "mcp",
                            })
                        continue

                if method == "turn/completed":
                    turn = params.get("turn", {}) or {}
                    status = turn.get("status") or ""
                    err = turn.get("error")
                    if status in ("failed", "error") or err:
                        raise LLMClientError(
                            f"codex app-server turn failed: {err or status}")
                    if getattr(self, "_codex_app_preempt_pending", 0) > 0:
                        sent = list(getattr(self, "_codex_app_sent_preempt_texts", []) or [])
                        rollout = self._codex_app_rollout_path(workdir, thread_id)
                        pstatus = self._codex_app_check_preempt_in_rollout(rollout, sent)
                        deadline = time.time() + 3.0
                        while pstatus in ("unread", "unknown") and time.time() < deadline:
                            time.sleep(0.1)
                            pstatus = self._codex_app_check_preempt_in_rollout(rollout, sent)
                        if pstatus == "done":
                            self._had_preempts_this_turn = True
                            logger.info(
                                "[codex-app] turn completed; rollout shows %d preempt(s) received by provider",
                                len(sent))
                        else:
                            logger.info(
                                "[codex-app] turn completed; %d preempt(s) not proven received in rollout (status=%s) - pending rescue will retrigger",
                                len(sent), pstatus)
                        self._codex_app_preempt_pending = 0
                        self._codex_app_sent_preempt_texts = []
                    self._codex_app_turn_completed_for_callback = True
                    break

                if method == "error":
                    raise LLMClientError(f"codex app-server error: {params.get('error') or params}")

            _flush_text()
            _first_event_done.set()
            _first_event_timer.cancel()
            _flush_live_thinking(force=True)
            content = "".join(final_text_parts).strip() or "".join(text_parts).strip()
            return LLMResponse(
                content=content,
                model=model,
                tokens_in=max(0, int(prompt_tokens or 0)),
                tokens_out=max(0, len(content) // 4),
                finish_reason="stop",
                raw={"thread_id": thread_id, "turn_id": turn_id,
                     "tool_results": len(completed_tool_ids)},
                thinking="".join(thinking_parts).strip(),
            )
        except _CodexAppServerProtocolError as exc:
            turn_failed = True
            raise LLMClientError(str(exc)) from exc
        except Exception:
            turn_failed = True
            raise
        finally:
            if _first_event_done is not None:
                _first_event_done.set()
            if _first_event_timer is not None:
                try:
                    _first_event_timer.cancel()
                except Exception:
                    logger.debug("[codex-app] first-event timer cancel failed", exc_info=True)
            lock = self._codex_app_ensure_lock()
            with lock:
                active = getattr(self, "_codex_app_active", None)
                if isinstance(active, dict):
                    active.pop(active_key, None)
            try:
                self._codex_recover_tokens(
                    workdir, user_id=user_id, conversation_id=conv_id)
            except Exception:
                logger.debug("[codex-app] token recovery failed", exc_info=True)

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
                and bool(thread_id)
                and bool(container)
                and not is_ephemeral
            )
            if keep_alive:
                try:
                    live_reg.register(
                        live_key, container, workdir,
                        service_id=svc_id,
                        session_id=thread_id,
                        proc=proc,
                        event_q=stderr_lines,
                        mcp_internal_token=internal_token,
                        active_turn=False,
                    )
                    logger.info(
                        "[codex-app-live] keep-alive conv=%s agent=%s session=%s",
                        conv_id[:8] or "?", agent_name, thread_id[:12])
                except Exception:
                    logger.debug("[codex-app-live] register failed", exc_info=True)
                    keep_alive = False

            if not keep_alive and not self._codex_app_compact_hard_killed:
                if live_reg is not None and live_key is not None:
                    try:
                        live_reg.evict(live_key, "app_server_teardown")
                    except Exception:
                        logger.debug("[codex-app-live] evict failed", exc_info=True)
                if proc is not None:
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                            try:
                                proc.wait(timeout=2.0)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait(timeout=2.0)
                    except Exception:
                        logger.debug("[codex-app] terminate failed", exc_info=True)
                if internal_token:
                    try:
                        from core.internal_auth import revoke_token
                        revoke_token(internal_token)
                    except Exception:
                        logger.debug("[codex-app] internal token revoke failed", exc_info=True)
                self._codex_pool_release(container)
            self._codex_app_log_stderr(stderr_lines)
            if owns_live_lock and live_session is not None:
                try:
                    live_session.turn_lock.release()
                except Exception:
                    logger.debug("[codex-app-live] turn lock release failed", exc_info=True)
            if is_ephemeral and workdir:
                try:
                    shutil.rmtree(workdir, ignore_errors=True)
                    if os.path.isdir(workdir):
                        stale = f"{workdir}.stale-{uuid.uuid4().hex[:8]}"
                        try:
                            os.replace(workdir, stale)
                            shutil.rmtree(stale, ignore_errors=True)
                        except OSError:
                            logger.debug(
                                "[codex-app] deferred ephemeral workdir cleanup: %s",
                                workdir)
                    else:
                        logger.debug("[codex-app] deleted ephemeral workdir: %s", workdir)
                except Exception:
                    logger.debug("[codex-app] ephemeral workdir cleanup failed: %s",
                                 workdir, exc_info=True)
