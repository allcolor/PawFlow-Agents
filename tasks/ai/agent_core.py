"""AgentLoopTask mixin — unified agent execution loop."""
import copy
import json
import logging
import time
from typing import Dict, List

from core.llm_client import (
    LLMClient, LLMMessage, LLMToolDefinition,
)
from tasks.ai.agent_emitter import AgentEmitter, AgentResult
from tasks.ai.agent_exceptions import AgentCancelled, _InterruptComplete

logger = logging.getLogger(__name__)


def _apply_bg_results(messages, conversation_id):
    """Apply completed background tool results to in-memory messages."""
    import core.background_tool as _bg
    for m in messages:
        if (m.role == "tool" and isinstance(m.content, str)
                and "Running in background" in m.content
                and getattr(m, 'tool_call_id', None)):
            result = _bg.pop_completed(conversation_id, m.tool_call_id)
            if result is not None:
                m.content = result
                logger.info("[bg-tool] applied result for %s in-memory",
                            m.tool_call_id)


def _check_budget(ctx, total_in, total_out):
    """Raise RuntimeError if conversation cost exceeds max_budget_usd."""
    budget = ctx.get("max_budget_usd", 0)
    if not budget:
        return  # no cap
    svc_cfg = getattr(ctx.get("resolved_svc"), 'config', {}) or {}
    cost_in = float(svc_cfg.get("cost_per_1m_input", 3.0))
    cost_out = float(svc_cfg.get("cost_per_1m_output", 15.0))
    spent = (total_in / 1_000_000 * cost_in) + (total_out / 1_000_000 * cost_out)
    if spent >= budget:
        raise RuntimeError(f"Budget exceeded: ${spent:.4f} >= ${budget:.2f} limit")


class AgentCoreMixin:
    def _run_agent_loop(self, ctx: Dict, emitter: AgentEmitter) -> AgentResult:
        """The ONE agent execution loop — used by both sync and streaming."""
        conversation_id = ctx.get("conversation_id", "")
        # Push context into active stack — pop in finally (guarantees no ghost)
        _agent_name = ctx.get("active_agent_name", "")
        _ctx_key = f"{conversation_id}:{_agent_name}" if _agent_name else conversation_id
        with self._active_contexts_lock:
            self._active_contexts[_ctx_key] = ctx
        try:
            return self._run_agent_loop_inner(ctx, emitter)
        finally:
            with self._active_contexts_lock:
                self._active_contexts.pop(_ctx_key, None)

    def _run_agent_loop_inner(self, ctx, emitter):
        conversation_id = ctx.get("conversation_id", "")
        start_time = time.time()
        ctx["_started_at"] = start_time
        total_tokens_in = 0
        total_tokens_out = 0
        total_cache_read = 0
        total_cache_write = 0
        tools_called: List[str] = []
        iteration = 0
        final_model = ""
        finish_reason = ""
        response_content = ""
        _need_more_retried = False
        _fatal_error = False
        _fatal_error_msg = ""

        client: LLMClient = ctx["client"]
        registry = ctx["registry"]
        tool_defs = ctx["tool_defs"]
        messages: List[LLMMessage] = ctx["messages"]
        model = ctx["model"]
        conversation_id = ctx.get("conversation_id", "")
        use_conv_store = ctx.get("use_conv_store", False)
        user_id = ctx.get("user_id", "")

        # Set context on client for providers that need it (claude-code)
        client._conversation_id = conversation_id
        client._user_id = user_id
        client._agent_name = ctx.get("active_agent_name", "")
        client._agent_service = ctx.get("active_llm_service", "")
        client._event_cid = ctx.get("_event_cid", conversation_id)

        # Register active claude-code client for preempt (stdin injection)
        _agent_name_key = f"{conversation_id}:{ctx.get('active_agent_name', '')}" if ctx.get('active_agent_name') else conversation_id
        if hasattr(client, 'send_user_message') and conversation_id:
            with self._active_contexts_lock:
                self._active_claude_client[_agent_name_key] = client
            # Clear cancelled state from previous run
            try:
                from services.tool_relay_service import ToolRelayService
                ToolRelayService.uncancel_agent(
                    conversation_id, ctx.get("active_agent_name", ""))
            except Exception:
                pass
        max_rounds = int(ctx.get("max_rounds", 1)) if emitter.is_streaming else 1
        _consecutive_tool: Dict[str, int] = {}
        _max_consec = ctx.get("max_consecutive_tool_calls", 100)
        # Apply per-agent model override
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                from tasks.ai.agent_utils import _resolve_extra
                _cs = ConversationStore.instance()
                _agent_n = ctx.get("active_agent_name") or ""
                # Fast mode: override model with fast variant
                _fast = _resolve_extra(_cs, conversation_id, "fast_mode", user_id)
                if _fast:
                    model = _fast
                # Per-agent model override (takes priority over fast)
                _mo = _resolve_extra(
                    _cs, conversation_id,
                    f"model_override:{_agent_n}", user_id)
                if _mo:
                    model = _mo
            except Exception:
                pass
        # Client metadata
        _client_provider = getattr(client, "provider", "") or ""
        if not isinstance(_client_provider, str):
            _client_provider = ""
        _client_model = getattr(client, "default_model", "") or ""
        _client_base_url = getattr(client, "base_url", "") or ""
        if not isinstance(_client_base_url, str):
            _client_base_url = ""
        def _agent_source(tok_in=0, tok_out=0, model_override=""):
            import re as _re
            src = {
                "type": "agent", "name": ctx.get("active_agent_name", ""),
                "llm_service": ctx.get("active_llm_service", ""),
                "provider": _client_provider,
                "model": model_override or _client_model,
                "base_url": _re.sub(r'(key|token|secret)=[^&]+', r'\1=***',
                                    _client_base_url) if _client_base_url else "",
                "containerized": bool(getattr(client, 'containerize', False)),
            }
            if tok_in or tok_out:
                src["tokens_in"] = tok_in
                src["tokens_out"] = tok_out
            return src
        # SpawnAgentsHandler source tracking
        from core.tool_registry import SpawnAgentsHandler as _SAH
        for _h in registry.list_tools():
            if isinstance(_h, _SAH):
                _h.set_source_agent(
                    ctx.get("active_agent_name", ""),
                    ctx.get("active_llm_service", ""))
                break
        # New messages tracking
        new_messages: List[LLMMessage] = []
        all_assistant_msg_ids: List[str] = []  # survives flush, for done event
        base_count = ctx.get("_base_message_count", 0)
        if len(messages) > base_count:
            new_messages.extend(messages[base_count:])
        def _append(msg: LLMMessage):
            # Sync msg_id: assistant messages use emitter's pre-generated ID
            # so SSE streaming tokens, done event, and persisted message all
            # share the SAME msg_id — enabling client-side dedup.
            if msg.role == "assistant" and emitter._current_msg_id:
                msg.msg_id = emitter._current_msg_id
                all_assistant_msg_ids.append(msg.msg_id)
                # After this message, generate a NEW msg_id for the next one
                import uuid as _uuid_append
                emitter._current_msg_id = _uuid_append.uuid4().hex[:12]
            messages.append(msg)
            new_messages.append(msg)
            # Persist via conversation writer + publish SSE (single source of truth)
            if use_conv_store and conversation_id and msg.role in ("assistant", "tool"):
                try:
                    from core.conversation_writer import ConversationWriter
                    _store_msg = {
                        "role": msg.role, "content": msg.content,
                        "source": msg.source,
                        "msg_id": getattr(msg, "msg_id", None),
                        "tool_call_id": getattr(msg, "tool_call_id", None),
                    }
                    if msg.thinking:
                        _store_msg["thinking"] = msg.thinking
                    if msg.tool_calls:
                        _store_msg["tool_calls"] = [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in msg.tool_calls
                        ]
                    # Build SSE events to publish AFTER write
                    # Skip for Claude Code — claude_code.py publishes SSE in real-time
                    _sse = []
                    if not ctx.get("_is_claude_code"):
                        _agent = (msg.source or {}).get("name", "") if msg.source else ""
                        if not _agent:
                            _agent = ctx.get("active_agent_name", "")
                        _svc = (msg.source or {}).get("llm_service", "") if msg.source else ""
                        if msg.role == "assistant" and msg.tool_calls:
                            from core.llm_client import unwrap_mcp_tool
                            for tc in msg.tool_calls:
                                _tc_name, _tc_args = unwrap_mcp_tool(tc.name, tc.arguments)
                                _sse.append({"type": "tool_call", "data": {
                                    "tool": _tc_name, "arguments": _tc_args,
                                    "tc_id": tc.id,
                                    "agent_name": _agent, "llm_service": _svc,
                                }})
                        if msg.role == "tool":
                            _preview = (msg.content[:2000] if isinstance(msg.content, str)
                                        else str(msg.content)[:2000])
                            if isinstance(_preview, str) and _preview.startswith("[TOOL OUTPUT"):
                                _nl = _preview.find("\n")
                                if _nl >= 0:
                                    _preview = _preview[_nl + 1:]
                                if _preview.endswith("[/TOOL OUTPUT]"):
                                    _preview = _preview[:-len("[/TOOL OUTPUT]")].rstrip("\n")
                            _sse.append({"type": "tool_result", "data": {
                                "tool": getattr(msg, '_tool_name', ''),
                                "result": _preview,
                                "tc_id": getattr(msg, 'tool_call_id', ''),
                                "agent_name": _agent, "llm_service": _svc,
                            }})
                    ConversationWriter.for_conversation(conversation_id).enqueue(
                        [_store_msg], user_id=user_id, sse_events=_sse if _sse else None)
                except Exception:
                    pass
            # Publish per-message metadata (model, tokens, service) so client
            # can attach badge + info to the correct element by msg_id
            if msg.role == "assistant" and msg.source and emitter.is_streaming:
                _src = msg.source
                if _src.get("tokens_in") or _src.get("tokens_out") or _src.get("llm_service"):
                    from core.conversation_event_bus import ConversationEventBus
                    try:
                        ConversationEventBus.instance().publish_event(
                            ctx.get("_event_cid", conversation_id), "message_meta", {
                                "msg_id": msg.msg_id,
                                "source": _src,
                                "model": _src.get("model", ""),
                                "provider": _src.get("provider", ""),
                                "tokens_in": _src.get("tokens_in", 0),
                                "tokens_out": _src.get("tokens_out", 0),
                            })
                    except Exception:
                        pass

        def _flush():
            nonlocal new_messages
            emitter.flush(new_messages)
            new_messages = []
        # Track known message count
        if use_conv_store and conversation_id:
            try:
                from core.conversation_store import ConversationStore
                ctx["_last_known_msg_count"] = ConversationStore.instance().message_count(
                    conversation_id)
            except Exception:
                ctx["_last_known_msg_count"] = 0

        # Repair orphan tool_calls — assistant messages with tool_calls
        # whose tool results are missing (broken by compact/clear)
        _repaired = False
        for i, m in enumerate(messages):
            if m.role == "assistant" and m.tool_calls:
                tc_ids = {tc.id for tc in m.tool_calls}
                # Check if all tool_call_ids have responses after this message
                found_ids = set()
                for j in range(i + 1, min(i + len(tc_ids) + 2, len(messages))):
                    if messages[j].role == "tool" and messages[j].tool_call_id in tc_ids:
                        found_ids.add(messages[j].tool_call_id)
                missing = tc_ids - found_ids
                if missing:
                    # Insert placeholder tool results for missing IDs
                    for idx, tc_id in enumerate(missing):
                        messages.insert(i + 1 + idx, LLMMessage(
                            role="tool", content="[Result unavailable — cleared by context compaction]",
                            tool_call_id=tc_id))
                    _repaired = True
        if _repaired:
            logger.warning(f"[agent:{conversation_id[:8]}] repaired orphan tool_calls in context")

        # Start file checkpoint for /rewind support
        _cp_id = ""
        if use_conv_store and conversation_id and not ctx.get("is_poll"):
            try:
                from core.checkpoint import CheckpointManager
                _cp_id = CheckpointManager.start_checkpoint(conversation_id)
                # Set checkpoint_id on all BaseFsHandler instances
                from core.handlers._fs_base import BaseFsHandler as _BFH
                for _h in registry.list_tools():
                    if isinstance(_h, _BFH):
                        _h.set_checkpoint_id(_cp_id)
            except Exception as _cp_err:
                logger.debug(f"[checkpoint] init failed: {_cp_err}")

        emitter.on_loop_start(ctx)
        _flush()
        _summ = ctx.get("summarizer", (None, 0, ""))
        compact_client = _summ[0] if _summ[0] else client
        _compact_svc_id = _summ[2] if len(_summ) > 2 else ctx.get("active_llm_service", "")

        # Note: post-response compact is done by auto-compact on load
        # in _prepare_agent_context. No lazy flag needed.

        try:
            for current_round in range(1, max_rounds + 1):
                continuation_plan = None
                continuation_delay = 3
                for _ in iter(lambda: iteration < ctx["max_iterations"], False):
                    emitter.check_cancelled()
                    emitter.drain_pending(messages, _append, iteration)
                    iteration += 1
                    ctx["_iteration"] = iteration
                    ctx["_round"] = current_round

                    poll_silent = ctx.get("is_poll", False) and iteration == 1
                    # Heartbeat covers the ENTIRE iteration (LLM + tools)
                    _iter_hb = emitter.start_heartbeat(poll_silent)
                    emitter.on_iteration_start(
                        iteration, current_round, ctx["max_iterations"],
                        max_rounds, tools_called, poll_silent)
                    # Claude-code: skip per-iteration compaction.
                    # If no active session, offload old messages to FileStore
                    # (avoids "Prompt too long" — Claude reads history via MCP tool).
                    # If active session, we'll send only the last user message anyway.
                    if ctx.get("_is_claude_code"):
                        if ctx.get("_claude_has_session"):
                            llm_context = list(messages)
                        else:
                            llm_context = self._prepare_cc_file_context(list(messages))
                    else:
                        _max_ctx = ctx.get("max_context_size", 64000)
                        _cpt = ctx.get("chars_per_token", 0)
                        _est = self._estimate_tokens(messages, tool_defs=tool_defs,
                                                     chars_per_token=_cpt)
                        _threshold = ctx.get("context_compact_threshold", 0.75)
                        _precompact = ctx.get("_precompact_snapshot")

                        # Microcompaction: clear old tool results after idle gap
                        if iteration == 1:
                            self._microcompact_time_based(messages)

                        # Precompact at 40%: start background summarization
                        if (_est > _max_ctx * 0.4
                                and not _precompact
                                and not ctx.get("_precompact_running")):
                            _snap_msgs = copy.deepcopy(messages)
                            _snap_last_id = messages[-1].msg_id if messages else ""
                            ctx["_precompact_running"] = True

                            def _bg_precompact():
                                try:
                                    logger.info("[precompact:%s] starting at %.0f%% (%d tokens)",
                                                conversation_id[:8], (_est / _max_ctx) * 100, _est)
                                    result = self._compact(
                                        _snap_msgs, compact_client, _max_ctx,
                                        threshold=0.0, force=True,
                                        conversation_id=conversation_id,
                                        agent_name=ctx.get("active_agent_name") or "",
                                        tool_defs=ctx.get("tool_defs"),
                                        chars_per_token=_cpt,
                                    )
                                    ctx["_precompact_snapshot"] = {
                                        "messages": result,
                                        "last_msg_id": _snap_last_id,
                                        "original_count": len(_snap_msgs),
                                    }
                                    logger.info("[precompact:%s] ready: %d → %d messages",
                                                conversation_id[:8], len(_snap_msgs), len(result))
                                except Exception as e:
                                    logger.warning("[precompact:%s] failed: %s", conversation_id[:8], e)
                                finally:
                                    ctx["_precompact_running"] = False

                            threading.Thread(target=_bg_precompact, daemon=True,
                                             name=f"precompact-{conversation_id[:8]}").start()

                        # At threshold: use precompact snapshot if available
                        if _est > _max_ctx * _threshold and _precompact:
                            _snap = _precompact
                            _snap_last_id = _snap["last_msg_id"]
                            # Find where the snapshot ends in current messages
                            _split = len(messages)
                            for _si in range(len(messages)):
                                if messages[_si].msg_id == _snap_last_id:
                                    _split = _si + 1
                                    break
                            # Merge: precompact summary + messages after snapshot
                            _after = messages[_split:]
                            llm_context = list(_snap["messages"]) + _after
                            # Check if this fits — if not, compact the remainder
                            _merged_est = self._estimate_tokens(
                                llm_context, tool_defs=tool_defs, chars_per_token=_cpt)
                            if _merged_est > _max_ctx * _threshold:
                                llm_context = self._compact(
                                    copy.deepcopy(llm_context), compact_client, _max_ctx,
                                    threshold=_threshold,
                                    conversation_id=conversation_id,
                                    agent_name=ctx.get("active_agent_name") or "",
                                    tool_defs=ctx.get("tool_defs"),
                                    chars_per_token=_cpt,
                                )
                            logger.info("[precompact:%s] applied: snapshot(%d) + after(%d) = %d msgs",
                                        conversation_id[:8], len(_snap["messages"]),
                                        len(_after), len(llm_context))
                            ctx["_precompact_snapshot"] = None  # consumed
                            ctx["_context_diverged"] = True
                        else:
                            # Normal path: compact if over threshold
                            llm_context = self._compact(
                                copy.deepcopy(messages), compact_client, _max_ctx,
                                threshold=_threshold,
                                conversation_id=conversation_id,
                                agent_name=ctx.get("active_agent_name") or "",
                                tool_defs=ctx.get("tool_defs"),
                                chars_per_token=_cpt,
                            )
                            if len(llm_context) < len(messages):
                                ctx["_context_diverged"] = True

                    # Pre-injection char count for CPT calibration
                    _pre_inject_chars = self._estimate_tokens(
                        llm_context, tool_defs=tool_defs, chars_per_token=1.0)

                    # Identity injection
                    _id_nicks = ctx.get("_nicknames") or {}
                    llm_context = self._inject_identity(llm_context, _id_nicks)
                    llm_context = self._apply_identity_suffix(
                        llm_context, ctx.get("_identity_suffix", ""))

                    # Dynamic metadata — merged into the last user message
                    # (AFTER cache breakpoints, so prefix is stable)
                    _max_ctx = ctx.get("max_context_size", 200000)
                    _est_used = self._estimate_tokens(
                        llm_context, tool_defs=tool_defs,
                        chars_per_token=ctx.get("chars_per_token", 0))
                    _remaining = max(0, _max_ctx - _est_used)
                    _dt = ctx.get("_datetime_str", "")
                    _meta_parts = []
                    if _dt:
                        _meta_parts.append(f"Current date/time: {_dt}")
                    _meta_parts.append(f"Context: ~{_est_used}/{_max_ctx} tokens (~{_remaining} remaining)")
                    _meta_note = "\n\n[System: " + ". ".join(_meta_parts) + "]"
                    # Find last user message and append metadata to it
                    for _mi in range(len(llm_context) - 1, -1, -1):
                        if llm_context[_mi].role == "user":
                            _um = llm_context[_mi]
                            _uc = _um.content if isinstance(_um.content, str) else str(_um.content or "")
                            llm_context[_mi] = LLMMessage(
                                role="user", content=_uc + _meta_note,
                                tool_calls=_um.tool_calls, tool_call_id=_um.tool_call_id,
                                source=_um.source, msg_id=_um.msg_id,
                            )
                            break

                    emitter.check_cancelled()

                    # Interrupt check
                    if emitter.check_interrupt():
                        logger.info(f"[agent:{conversation_id[:8]}] interrupted — forcing synthesis")
                        _append(LLMMessage(
                            role="user",
                            content=(
                                "[System: The user has requested an immediate response. "
                                "Stop all tool usage. Summarize your progress so far and "
                                "provide your best answer with the information you have "
                                "gathered. Mention what you were still working on so the "
                                "user can ask you to continue if needed.]"),
                        ))
                        _irpt_resp = client.complete_stream(
                            messages=self._compact(
                                copy.deepcopy(messages), compact_client,
                                ctx.get("max_context_size", 64000), threshold=0.6),
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=None,
                            callback=emitter.get_token_callback(False),
                        ) if emitter.is_streaming else client.complete(
                            messages=self._compact(
                                copy.deepcopy(messages), compact_client,
                                ctx.get("max_context_size", 64000), threshold=0.6),
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=None,
                        )
                        _append(LLMMessage(
                            role="assistant", content=_irpt_resp.content,
                            source=_agent_source()))
                        response_content = _irpt_resp.content
                        total_tokens_in += _irpt_resp.tokens_in
                        total_tokens_out += _irpt_resp.tokens_out
                        total_cache_read += getattr(_irpt_resp, 'cache_read_tokens', 0)
                        total_cache_write += getattr(_irpt_resp, 'cache_creation_tokens', 0)
                        final_model = _irpt_resp.model
                        _flush()
                        raise _InterruptComplete()

                    # Force-fit guard (skip for claude-code — it manages its own context)
                    if not ctx.get("_is_claude_code"):
                        _pre_send_est = self._estimate_tokens(
                            llm_context, tool_defs=ctx.get("tool_defs"),
                            chars_per_token=ctx.get("chars_per_token", 0))
                        logger.debug(
                            f"[compact] pre-send: {_pre_send_est} est. tokens, "
                            f"{len(llm_context)} msgs, max={_max_ctx}")
                        if _pre_send_est > _max_ctx:
                            logger.warning(
                                f"[compact] STILL OVER ({_pre_send_est} > {_max_ctx}), force-fitting...")
                            llm_context = self._force_fit_context(
                                llm_context, _max_ctx,
                                chars_per_token=ctx.get("chars_per_token", 0),
                                tool_defs=ctx.get("tool_defs"))

                    # Dynamic lazy tools fallback — for tools_mode=full that needs switching
                    if (tool_defs and not ctx.get("_lazy_tools_active")
                            and len(tool_defs) > 4 and iteration > 5):
                        _tools_chars = sum(
                            len(td.name) + len(td.description or "")
                            + len(json.dumps(td.parameters or {}))
                            for td in tool_defs)
                        _tools_pct = (_tools_chars / max(_pre_send_est * 3.5, 1)) * 100
                        if _tools_pct > 15:
                            logger.info(f"[compact] Dynamic lazy switch: tools={_tools_pct:.0f}%% of context")
                            from core.tool_registry import GetToolSchemaHandler, UseToolHandler
                            _gts = GetToolSchemaHandler(registry)
                            _ut = UseToolHandler(registry)
                            registry.register(_gts)
                            registry.register(_ut)
                            tool_defs = [
                                LLMToolDefinition(name=_gts.name, description=_gts.description,
                                                  parameters=_gts.parameters_schema),
                                LLMToolDefinition(name=_ut.name, description=_ut.description,
                                                  parameters=_ut.parameters_schema),
                            ]
                            ctx["tool_defs"] = tool_defs
                            ctx["_lazy_tools_active"] = True

                    # LLM call
                    _tb = ctx.get("thinking_budget", 0)
                    _is_claude_code = _client_provider == "claude-code"

                    _cc_turn_count = [0]

                    def _claude_code_turn_callback(text, tool_calls):
                        """Called by claude-code at each internal turn boundary.

                        Persists everything the user sees in the transcript:
                        - Assistant text → real message (in context)
                        - Tool calls → display_only (visible but not in LLM context)
                        - Tool results → display_only, truncated to 300 chars
                        - Thinking → display_only

                        SSE events (tool_call, tool_result, thinking_content) are
                        published in real-time by claude_code.py as they arrive from
                        the stream. This callback only persists + publishes
                        turn_complete to finalize the streaming element.
                        """
                        nonlocal tools_called
                        from core.llm_client import LLMToolCall

                        _cc_turn_count[0] += 1
                        ctx["_iteration"] = _cc_turn_count[0]

                        _bus = emitter.bus
                        _cid = ctx.get("_event_cid", conversation_id)
                        turn_msgs = []
                        _src = _agent_source()
                        _agent = _src.get("name", "")

                        # display_only messages are NOT persisted in the transcript.
                        # The transcript contains LLM context messages (assistant, tool)
                        # and _classify_messages_for_display reconstructs the visual
                        # representation (tool_call, tool_result, thinking) from them.
                        # Persisting display_only would create duplicates at reload.

                        if text:
                            msg = LLMMessage(
                                role="assistant", content=text, source=_src)
                            _append(msg)  # persists immediately
                            turn_msgs.append(msg)
                            client._last_turn_msg_id = getattr(msg, "msg_id", "")

                        # Finalize streaming element — next turn creates a new one
                        if text or tool_calls:
                            # Estimate tokens from text length (real values come in done)
                            _cpt = ctx.get("chars_per_token", 0) or 3.5
                            _est_out = int(len(text) / _cpt) if text else 0
                            _bus.publish_event(_cid, "turn_complete", {
                                "agent_name": _agent,
                                "msg_id": client._last_turn_msg_id if text else "",
                                "source": _src,
                                "model": _src.get("model", ""),
                                "provider": _src.get("provider", ""),
                                "tokens_out": _est_out,
                            })

                        if tool_calls:
                            # Extract thinking from first tool_call (claude-code bundles it there)
                            _thinking_text = tool_calls[0].get("thinking", "") if tool_calls else ""

                            tc_objects = [
                                LLMToolCall(
                                    id=tc.get("id", ""),
                                    name=tc.get("name", ""),
                                    arguments=tc.get("arguments", {}),
                                ) for tc in tool_calls
                            ]
                            for tc_obj in tc_objects:
                                # Unwrap MCP tool names for display
                                _display_name = tc_obj.name
                                if _display_name.startswith("mcp__"):
                                    # mcp__server__tool → tool
                                    _parts = _display_name.split("__", 2)
                                    _display_name = _parts[-1] if len(_parts) >= 3 else _display_name
                                    # For use_tool, show the inner tool_name
                                    if _display_name == "use_tool" and isinstance(tc_obj.arguments, dict):
                                        _display_name = tc_obj.arguments.get("tool_name", _display_name)
                                tools_called.append(_display_name)
                                ctx["_last_tool"] = _display_name

                            # Tool call message (in LLM context, includes thinking)
                            tc_msg = LLMMessage(
                                role="assistant", content="",
                                tool_calls=tc_objects, thinking=_thinking_text,
                                source=_src)
                            _append(tc_msg)
                            turn_msgs.append(tc_msg)

                            for i, tc_obj in enumerate(tc_objects):
                                tc_raw = tool_calls[i] if i < len(tool_calls) else {}
                                _result = tc_raw.get("result") or ""

                                from core.llm_client import unwrap_mcp_tool
                                _display_name, _display_args = unwrap_mcp_tool(
                                    tc_obj.name, tc_obj.arguments)

                                # Tool result (in LLM context)
                                tr_content = _result or "(no output)"
                                tr_msg = LLMMessage(
                                    role="tool", content=tr_content,
                                    tool_call_id=tc_obj.id)
                                tr_msg._tool_name = _display_name
                                _append(tr_msg)
                                turn_msgs.append(tr_msg)

                                # display_only NOT persisted — _classify_messages_for_display
                                # reconstructs tool_call/tool_result from LLM context messages

                    def _llm_call(msgs, ps=poll_silent):
                        if emitter.is_streaming:
                            return client.complete_stream(
                                messages=msgs, model=model or None,
                                temperature=ctx["temperature"], max_tokens=ctx["max_tokens"],
                                tools=tool_defs if tool_defs else None,
                                callback=emitter.get_token_callback(ps),
                                thinking_budget=_tb,
                                thinking_callback=emitter.get_thinking_callback(ps) if _tb > 0 else None,
                                turn_callback=_claude_code_turn_callback if _is_claude_code else None)
                        return client.complete(
                            messages=msgs, model=model or None,
                            temperature=ctx["temperature"], max_tokens=ctx["max_tokens"],
                            tools=tool_defs if tool_defs else None, thinking_budget=_tb)

                    # Claude-code with existing session: send only the latest
                    # user message (session has full context via --resume)
                    _call_context = llm_context
                    if _is_claude_code and ctx.get("_claude_has_session"):
                        def _is_real_user_msg(m):
                            if m.role != "user":
                                return False
                            c = m.content
                            if isinstance(c, list):
                                return True  # multipart (text+image) = real user msg
                            t = c or ""
                            return not t.startswith("[System:") and not t.startswith("[Conversation summary")
                        _new_msgs = [m for m in llm_context if _is_real_user_msg(m)]
                        if _new_msgs:
                            # Only the latest user message — no system prompt
                            # (session already has it from initial context)
                            _call_context = [_new_msgs[-1]]

                    _resume_retried = False
                    try:
                        _check_budget(ctx, total_tokens_in, total_tokens_out)
                        response = _llm_call(_call_context)
                    except AgentCancelled:
                        raise
                    except Exception as llm_err:
                        err_str = str(llm_err)
                        # AgentCancelled may be wrapped in LLMClientError
                        if "AgentCancelled" in err_str:
                            raise AgentCancelled()
                        # Budget exceeded — fatal, no retry
                        if "Budget exceeded" in err_str:
                            logger.warning("[agent:%s] %s", conversation_id[:8], err_str)
                            emitter.on_fatal_error(err_str)
                            _fatal_error = True; _fatal_error_msg = _fatal_error_msg or err_str
                            break
                        # Claude-code resume failed → invalidate session, retry
                        # with full context (first-message flow).
                        # But NOT if the agent was cancelled (interrupt) — that's
                        # intentional, not a connection failure.
                        _was_cancelled = False
                        try:
                            emitter.check_cancelled()
                        except AgentCancelled:
                            _was_cancelled = True
                        if _was_cancelled:
                            raise AgentCancelled()
                        _is_auth_error = "auth" in err_str.lower() or "401" in err_str
                        if _is_claude_code and ctx.get("_claude_has_session") and not _is_auth_error:
                            logger.warning("[claude-code] resume failed (%s), "
                                           "retrying with full context", err_str[:100])
                            try:
                                from core.conversation_store import ConversationStore
                                ConversationStore.instance().invalidate_claude_sessions(
                                    conversation_id)
                            except Exception:
                                pass
                            # Invalidate session in store
                            try:
                                from core.conversation_store import ConversationStore
                                _an = ctx.get("active_agent_name", "") or "default"
                                ConversationStore.instance().set_extra(
                                    conversation_id, f"claude_session:{_an}", "")
                            except Exception:
                                pass
                            ctx["_claude_has_session"] = False
                            try:
                                _check_budget(ctx, total_tokens_in, total_tokens_out)
                                llm_context = self._prepare_cc_file_context(list(messages))
                                response = _llm_call(llm_context)
                                _resume_retried = True
                            except Exception as retry_err:
                                logger.error("Claude-code full-context retry failed: %s", retry_err)
                                emitter.on_fatal_error(f"LLM call failed: {retry_err}")
                                _fatal_error = True; _fatal_error_msg = _fatal_error_msg or f"LLM call failed: {retry_err}"
                                break
                        if _resume_retried:
                            pass  # resume fallback succeeded
                        elif ("exceed_context_size" in err_str
                              or "n_prompt_tokens" in err_str
                              or "Prompt is too long" in err_str
                              or "prompt_too_long" in err_str):
                            logger.warning(f"[agent:{conversation_id[:8]}] Context overflow, retrying...")
                            emitter.on_overflow_retry(iteration)
                            if _is_claude_code:
                                llm_context = self._prepare_cc_file_context(
                                    list(messages), max_recent=20)
                            else:
                                llm_context = self._compact(
                                    llm_context, compact_client,
                                    ctx.get("max_context_size", 64000), threshold=0.5,
                                    conversation_id=conversation_id,
                                    tool_defs=ctx.get("tool_defs"),
                                    chars_per_token=ctx.get("chars_per_token", 0))
                            try:
                                _check_budget(ctx, total_tokens_in, total_tokens_out)
                                response = _llm_call(llm_context)
                            except Exception as retry_err:
                                logger.error(f"LLM retry failed: {retry_err}")
                                emitter.on_fatal_error(f"LLM call failed: {retry_err}")
                                _fatal_error = True; _fatal_error_msg = _fatal_error_msg or f"LLM call failed: {retry_err}"
                                break
                        else:
                            # Transient errors (500, 503, 529, timeout) — the LLMClient
                            # already retried max_retries times. At the agent level, we
                            # retry once more with a fresh call (new process for claude-code).
                            _transient = any(p in err_str for p in (
                                "500", "503", "502", "529", "overloaded", "timeout",
                                "Internal server error", "api_error", "server_error",
                                "rate_limit", "429"))
                            if _transient and not ctx.get("_agent_transient_retried"):
                                ctx["_agent_transient_retried"] = True
                                logger.warning("[agent:%s] transient LLM error, retrying: %s",
                                               conversation_id[:8], err_str[:150])
                                # For claude-code: invalidate session so retry starts fresh
                                if _is_claude_code and ctx.get("_claude_has_session"):
                                    try:
                                        from core.conversation_store import ConversationStore
                                        _an = ctx.get("active_agent_name", "") or "default"
                                        ConversationStore.instance().set_extra(
                                            conversation_id, f"claude_session:{_an}", "")
                                    except Exception:
                                        pass
                                    ctx["_claude_has_session"] = False
                                    llm_context = self._prepare_cc_file_context(list(messages))
                                time.sleep(5)
                                try:
                                    _check_budget(ctx, total_tokens_in, total_tokens_out)
                                    response = _llm_call(llm_context)
                                except AgentCancelled:
                                    raise
                                except Exception as retry_err:
                                    logger.error("[agent:%s] transient retry also failed: %s",
                                                 conversation_id[:8], retry_err)
                                    emitter.on_fatal_error(f"LLM call failed after retry: {retry_err}")
                                    _fatal_error = True; _fatal_error_msg = _fatal_error_msg or f"LLM call failed: {retry_err}"
                                    break
                            else:
                                logger.error(f"LLM call failed (iter {iteration}): {llm_err}")
                                emitter.on_fatal_error(f"LLM call failed: {llm_err}")
                                _fatal_error = True; _fatal_error_msg = _fatal_error_msg or f"LLM call failed: {llm_err}"
                                break
                    finally:
                        pass  # heartbeat stopped at iteration end

                    emitter.check_cancelled()

                    # Post-response — mark session as active for next iteration
                    if _is_claude_code and not ctx.get("_claude_has_session"):
                        try:
                            from core.conversation_store import ConversationStore
                            _an = ctx.get("active_agent_name", "") or "default"
                            if ConversationStore.instance().get_extra(
                                    conversation_id, f"claude_session:{_an}"):
                                ctx["_claude_has_session"] = True
                        except Exception:
                            pass
                        # Check: if context was offloaded to file, did CC read it?
                        _cc_fid = getattr(self, '_cc_context_file_id', '')
                        if _cc_fid and iteration == 1 and response.tool_calls:
                            _read_calls = [tc for tc in response.tool_calls
                                           if tc.name in ("read", "mcp__pawflow__use_tool")]
                            if not _read_calls:
                                logger.warning(
                                    "[cc-context] Claude Code did NOT read the history "
                                    "file %s on first turn — context may be lost", _cc_fid)
                            self._cc_context_file_id = ''  # check once
                    total_tokens_in += response.tokens_in
                    total_tokens_out += response.tokens_out
                    total_cache_read += getattr(response, 'cache_read_tokens', 0)
                    total_cache_write += getattr(response, 'cache_creation_tokens', 0)
                    final_model = response.model
                    finish_reason = response.finish_reason

                    # Budget warning at 80%
                    _bud = ctx.get("max_budget_usd", 0)
                    if _bud and not ctx.get("_budget_warning_sent"):
                        _svc_c = getattr(ctx.get("resolved_svc"), 'config', {}) or {}
                        _ci = float(_svc_c.get("cost_per_1m_input", 3.0))
                        _co = float(_svc_c.get("cost_per_1m_output", 15.0))
                        _spent = (total_tokens_in / 1_000_000 * _ci) + (total_tokens_out / 1_000_000 * _co)
                        if _spent >= _bud * 0.8:
                            ctx["_budget_warning_sent"] = True
                            emitter.bus.publish_event(ctx.get("_event_cid", conversation_id), "budget_warning", {
                                "spent_usd": round(_spent, 4),
                                "budget_usd": _bud,
                                "percent": round(_spent / _bud * 100, 1),
                                "agent_name": ctx.get("active_agent_name", ""),
                            })

                    self._deflate_image_messages(messages)
                    # Clear old tool results — keep last 3 (2 was too aggressive, caused repeats)
                    _keep = 3
                    self._clear_seen_tool_results(
                        messages, keep_recent=_keep,
                        conversation_id=conversation_id, user_id=user_id,
                        agent_name=ctx.get("active_agent_name", ""))

                    # Apply pending background tool results to in-memory messages
                    import core.background_tool as _bg_mod
                    _apply_bg_results(messages, conversation_id)

                    if response.tokens_in > 0:
                        _svc_id = ctx.get("active_llm_service") or ""
                        self._calibrate_cpt(_svc_id, _pre_inject_chars, response.tokens_in)
                        ctx["chars_per_token"] = self._get_cpt(
                            _svc_id, ctx.get("chars_per_token", 0))

                    # No tools → final response (but wait for bg tasks first)
                    if not response.tool_calls:
                        if _bg_mod.has_pending(conversation_id):
                            logger.info("[agent:%s] waiting for background tasks before exit",
                                        conversation_id[:8])
                            emitter.on_status("Waiting for background tasks...")
                            _bg_mod.wait_pending(conversation_id, timeout=120,
                                                 cancel_check=emitter.check_cancelled)
                            _apply_bg_results(messages, conversation_id)
                            continue

                        _resp_text = response.content or ""
                        # Claude-code: turn_callback persisted all content.
                        # response.content is "" — don't persist an empty msg.
                        # response_content stays "" — done event uses last turn text.
                        if _is_claude_code:
                            response_content = _resp_text
                            emitter.stop_heartbeat(_iter_hb)
                            _flush()
                            break
                        _has_thinking = bool(getattr(response, 'thinking', ''))
                        # Empty response with thinking = LLM is stuck in reasoning
                        # Give it one more chance with explicit instruction
                        if not _resp_text and _has_thinking and not _need_more_retried:
                            logger.warning(f"[agent:{conversation_id[:8]}] thinking-only response (no text/tools), nudging")
                            _append(LLMMessage(role="assistant", content="",
                                               source=_agent_source(response.tokens_in, response.tokens_out)))
                            _append(LLMMessage(role="user", content=(
                                "[System: You produced reasoning but no visible response or tool calls. "
                                "You MUST either call a tool or provide a text response to the user. "
                                "Do not just think — act or respond.]")))
                            _need_more_retried = True
                            continue
                        _src_no_tools = _agent_source(response.tokens_in, response.tokens_out, response.model)
                        action, msgs, final, _need_more_retried = self._handle_response_no_tools(
                            _resp_text, _client_provider, tool_defs,
                            _need_more_retried, source=_src_no_tools)
                        # Attach thinking to the first assistant message
                        _thinking_txt = response.thinking or ""
                        for _m in msgs:
                            if _m.role == "assistant" and _thinking_txt:
                                _m.thinking = _thinking_txt
                                _thinking_txt = ""  # only on the first one
                            _append(_m)
                        if action == "break":
                            response_content = final
                            emitter.stop_heartbeat(_iter_hb)
                            _flush()
                            break
                        continue

                    # Tool calls
                    _need_more_retried = False
                    _append(LLMMessage(
                        role="assistant", content=response.content,
                        tool_calls=response.tool_calls,
                        thinking=response.thinking or "",
                        source=_agent_source(response.tokens_in, response.tokens_out, response.model)))

                    if poll_silent and response.tool_calls:
                        poll_silent = False

                    emitter.on_tool_calls(
                        response.tool_calls, response.content or "",
                        response.thinking or "", poll_silent)
                    # Update running agent with tool info
                    _tool_names = [tc.name for tc in response.tool_calls]
                    results = self._execute_tool_calls(
                        response.tool_calls, registry, _consecutive_tool,
                        _max_consec, parallel=emitter.is_streaming,
                        agent_name=ctx.get("active_agent_name") or "",
                        agent_svc=ctx.get("active_llm_service", ""),
                        conversation_id=conversation_id, user_id=user_id,
                        is_claude_code=_is_claude_code,
                        cancel_check=emitter.check_cancelled,
                        event_cid=ctx.get("_event_cid", ""))

                    for tc, result_text in results:
                        tools_called.append(tc.name)
                        ctx["_last_tool"] = tc.name
                        emitter.check_cancelled()  # check after each tool
                        if tc.name == "schedule_continuation":
                            continuation_plan = tc.arguments.get("plan", "Continue")
                            continuation_delay = int(tc.arguments.get("delay_seconds", 3))
                        _tr_msg = LLMMessage(role="tool", content=result_text, tool_call_id=tc.id)
                        _tr_msg._tool_name = tc.name
                        _append(_tr_msg)
                        # Preview for SSE
                        _prev = result_text[:2000] if isinstance(result_text, str) else str(result_text)[:2000]
                        if isinstance(_prev, str) and _prev.startswith("[TOOL OUTPUT"):
                            _nl = _prev.find("\n")
                            if _nl >= 0:
                                _prev = _prev[_nl + 1:]
                            if _prev.endswith("[/TOOL OUTPUT]"):
                                _prev = _prev[:-len("[/TOOL OUTPUT]")].rstrip("\n")
                        emitter.on_tool_result(tc, result_text, _prev)

                    # Per-turn aggregate cap: if total tool results > 200K chars,
                    # persist the largest to FileStore to avoid context bloat
                    _AGG_CAP = 200_000
                    _turn_tool_msgs = [m for m in messages if m.role == "tool"
                                       and m in new_messages]
                    _total_chars = sum(len(m.content) for m in _turn_tool_msgs
                                       if isinstance(m.content, str))
                    if _total_chars > _AGG_CAP:
                        for m in sorted(_turn_tool_msgs,
                                        key=lambda x: len(x.content or ''), reverse=True):
                            if _total_chars <= _AGG_CAP:
                                break
                            if isinstance(m.content, str) and len(m.content) > 5000:
                                from core.file_store import FileStore
                                fid = FileStore.instance().store(
                                    "tool_result.txt", m.content.encode(), "text/plain")
                                _saved = len(m.content)
                                m.content = (
                                    f"[Result too large ({_saved:,} chars) — saved to "
                                    f"FileStore: /files/{fid}/tool_result.txt. Use "
                                    f"read(path='tool_result.txt', source='filestore', "
                                    f"file_id='{fid}') to access.]")
                                _total_chars -= _saved - len(m.content)
                        logger.info("[agent:%s] aggregate cap: persisted large tool results to FileStore",
                                    conversation_id[:8])

                    emitter.stop_heartbeat(_iter_hb)  # stop iteration heartbeat
                    emitter.on_iteration_end(
                        iteration, current_round, ctx["max_iterations"],
                        max_rounds, tools_called)
                    emitter.drain_pending(messages, _append, iteration)
                    emitter.check_cancelled()

                    # Mid-turn compaction: every 5 iterations, progressively clear
                    # old tool results on the canonical messages to stop context growth
                    # (skip for claude-code — manages its own context)
                    if not ctx.get("_is_claude_code") and iteration % 5 == 0 and len(messages) > 20:
                        _cpt = ctx.get("chars_per_token", 0) or 3.5
                        _mid_est = self._estimate_tokens(messages, chars_per_token=_cpt)
                        _mid_target = int(ctx.get("max_context_size", 200000) * 0.5)
                        if _mid_est > _mid_target:
                            logger.info(f"[agent:{conversation_id[:8]}] mid-turn compact: "
                                        f"{_mid_est} tokens > {_mid_target} target")
                            self._progressive_clear_tool_results(
                                messages, _mid_target, _mid_est,
                                keep_recent=4, chars_per_token=_cpt)

                    _flush()
                else:
                    # Max iterations reached
                    logger.warning("Agent reached max iterations (%d), forcing synthesis",
                                   ctx["max_iterations"])
                    _pre = len(messages)
                    content, ti, to, fm = self._force_synthesis(
                        messages, client, ctx,
                        prompt=(
                            "[System: You have reached the maximum number of tool calls. "
                            "You MUST now provide your final response to the user. "
                            "Synthesize all the information you gathered from your tool calls "
                            "and present a clear, comprehensive answer. Do NOT call any more tools.]"),
                        compact_client=compact_client,
                        use_streaming=emitter.is_streaming,
                        token_callback=emitter.get_token_callback(False) if emitter.is_streaming else None,
                        tools_called=tools_called, compact_threshold=1.0,
                        conversation_id=conversation_id)
                    new_messages.extend(messages[_pre:])
                    response_content = content
                    total_tokens_in += ti
                    total_tokens_out += to
                    if fm:
                        final_model = fm

                # Mark last assistant message as error before persisting
                if _fatal_error:
                    # Find last assistant msg — may be in new_messages (not yet flushed)
                    # or in messages (already flushed by turn_callback for claude-code)
                    _err_mid = ""
                    for m in reversed(new_messages):
                        if m.role == "assistant":
                            m.is_error = True
                            _err_mid = m.msg_id
                            break
                    if not _err_mid:
                        # Already flushed (claude-code path) — find in full messages
                        for m in reversed(messages):
                            if m.role == "assistant":
                                m.is_error = True
                                _err_mid = m.msg_id
                                break
                    if not _err_mid and _fatal_error_msg:
                        # No assistant message at all — create one
                        _err_msg = LLMMessage(
                            role="assistant", content=_fatal_error_msg,
                            is_error=True, source=_agent_source())
                        new_messages.append(_err_msg)
                        messages.append(_err_msg)
                        _err_mid = _err_msg.msg_id

                _flush()

                if _fatal_error:
                    finish_reason = "error"
                    # Patch the message in store (may have been flushed earlier)
                    if _err_mid and use_conv_store and conversation_id:
                        try:
                            from core.conversation_store import ConversationStore
                            ConversationStore.instance().patch_message(
                                conversation_id, _err_mid, is_error=True)
                        except Exception:
                            pass
                    break

                # Continuation
                if continuation_plan and current_round < max_rounds:
                    _append(LLMMessage(
                        role="user",
                        content=(
                            f"[System: Automatic continuation — round {current_round + 1}]\n"
                            f"Continue with your plan: {continuation_plan}\n"
                            f"Build on your previous findings. When done, provide a final synthesis. "
                            f"If you still have more work, call schedule_continuation again.")))
                    response_content = ""
                    time.sleep(continuation_delay)
                    continue
                else:
                    break

            # Empty response synthesis (skip for claude-code — turn_callback
            # already persisted all content, response_content is intentionally "")
            if not response_content and not _fatal_error and not ctx.get("_is_claude_code"):
                logger.warning(f"[agent:{conversation_id[:8]}] empty response — forcing synthesis")
                _pre = len(messages)
                content, ti, to, fm = self._force_synthesis(
                    messages, client, ctx,
                    prompt=(
                        "[System: You did not provide a response to the user. "
                        "You MUST respond now. Synthesize any information you have and present "
                        "a clear answer. Do NOT call any tools.]"),
                    compact_client=compact_client,
                    use_streaming=emitter.is_streaming,
                    token_callback=emitter.get_token_callback(False) if emitter.is_streaming else None,
                    tools_called=tools_called, conversation_id=conversation_id)
                new_messages.extend(messages[_pre:])
                response_content = content
                total_tokens_in += ti
                total_tokens_out += to
                if fm:
                    final_model = fm
                _flush()

            def _make_result(reason=""):
                # Compute turn cost
                try:
                    from core.cost_tracker import CostTracker
                    _conv_cost = CostTracker.instance().get_conversation_cost(conversation_id)
                    _turn_cost = _conv_cost.get("total", 0.0)
                except Exception:
                    _turn_cost = 0.0
                return AgentResult(
                    response_content=response_content,
                    conversation_id=conversation_id,
                    model=final_model or _client_model,
                    provider=_client_provider, base_url=_client_base_url,
                    tokens_in=total_tokens_in, tokens_out=total_tokens_out,
                    tools_called=tools_called, iterations=iteration,
                    duration_ms=(time.time() - start_time) * 1000,
                    finish_reason=reason or finish_reason, source=_agent_source(),
                    messages=messages, new_messages=new_messages,
                    all_msg_ids=all_assistant_msg_ids,
                    cost_usd=_turn_cost)

            # Final drain: pick up any messages that arrived during the last turn
            _pre_drain = len(messages)
            emitter.drain_pending(messages, _append, iteration)
            _new_user_msgs = [m for m in messages[_pre_drain:] if m.role == "user"]
            if _new_user_msgs:
                logger.info("[agent:%s] %d message(s) arrived during last turn — persisting + re-triggering",
                            conversation_id[:8], len(_new_user_msgs))
                _flush()
                # Signal that a new turn should start after done
                ctx["_retrigger_after_done"] = True

            # Unregister claude-code client BEFORE done (prevents stale preempt)
            _unreg_key = f"{conversation_id}:{ctx.get('active_agent_name', '')}" if ctx.get('active_agent_name') else conversation_id
            with self._active_contexts_lock:
                self._active_claude_client.pop(_unreg_key, None)

            # Post-loop: ALWAYS publish done, even if cleanup fails
            try:
                # NO_PENDING_WORK handling (streaming/poller only via emitter)
                _processed = emitter.on_no_pending_work(response_content or "", ctx)
                if _processed is None:
                    new_messages.clear()
                    result = _make_result("discarded")
                    emitter.on_done(result)
                    return result
                response_content = _processed

                self._track_tokens(
                    user_id or "anonymous", total_tokens_in, total_tokens_out,
                    model=final_model or _client_model,
                    agent_name=ctx.get("active_agent_name", "") or "",
                    llm_service=ctx.get("active_llm_service", ""))

                # Track cost per conversation/model
                try:
                    from core.cost_tracker import CostTracker
                    CostTracker.instance().track(
                        conversation_id, final_model or _client_model,
                        tokens_in=total_tokens_in, tokens_out=total_tokens_out,
                        cache_read=total_cache_read, cache_write=total_cache_write)
                except Exception as _cost_err:
                    logger.debug("[agent:%s] cost tracking error: %s",
                                 conversation_id[:8], _cost_err)

                self._cleanup_tool_result_files(
                    conversation_id=conversation_id,
                    agent_name=ctx.get("active_agent_name", ""))
            except Exception as _post_err:
                logger.error("[agent:%s] post-loop error: %s", conversation_id[:8],
                             _post_err, exc_info=True)
            finally:
                try:
                    result = _make_result()
                    logger.info("[agent:%s] publishing done (agent=%s)",
                                conversation_id[:8], ctx.get("active_agent_name", ""))
                    emitter.on_done(result)
                except Exception as _done_err:
                    logger.error("[agent:%s] CRITICAL: on_done failed: %s",
                                 conversation_id[:8], _done_err, exc_info=True)
                    # Last resort: publish done directly
                    try:
                        from core.conversation_event_bus import ConversationEventBus
                        ConversationEventBus.instance().publish_event(
                            ctx.get("_event_cid", conversation_id), "done", {
                                "response": response_content or "",
                                "agent_name": ctx.get("active_agent_name", ""),
                            })
                    except Exception:
                        pass
            return result

        except _InterruptComplete:
            def _make_result(reason=""):
                return AgentResult(
                    response_content=response_content, conversation_id=conversation_id,
                    model=final_model or _client_model, provider=_client_provider,
                    base_url=_client_base_url, tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out, tools_called=tools_called,
                    iterations=iteration, duration_ms=(time.time() - start_time) * 1000,
                    finish_reason=reason, source=_agent_source(),
                    messages=messages, new_messages=new_messages)
            emitter.on_interrupted(_make_result("interrupted"))
            return _make_result("interrupted")

        except AgentCancelled:
            logger.info(f"[agent:{conversation_id[:8]}] cancelled — NOT flushing (cancelled tour)")
            # Don't flush — the cancelled tour's messages should not be in the transcript
            # The interrupt synthesis will persist its own response separately
            def _make_result(reason=""):
                return AgentResult(
                    response_content=response_content, conversation_id=conversation_id,
                    model=final_model or _client_model, tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out, tools_called=tools_called,
                    iterations=iteration, duration_ms=(time.time() - start_time) * 1000,
                    finish_reason=reason, source=_agent_source(),
                    messages=messages, new_messages=new_messages)
            emitter.on_cancelled(_make_result("cancelled"), ctx)
            return _make_result("cancelled")

        except Exception as e:
            logger.error(f"Agent loop error: {e}", exc_info=True)
            _flush()
            emitter.on_error(e)
            raise
