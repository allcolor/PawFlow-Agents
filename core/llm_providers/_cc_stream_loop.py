"""Claude Code streaming dispatch loop + reader/watchdog daemons."""


import json
import logging
import time

from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND  # noqa: F401
from core.llm_providers._cc_base import (
    _CC_READER_EOF, _CC401Retry, _CCStreamState)  # noqa: F401

logger = logging.getLogger(__name__)


class _CCStreamLoopMixin:
    """Claude Code streaming dispatch loop + reader/watchdog daemons."""
    def _ccs_dispatch_loop(self, st):
        while True:
            event = st._event_q.get()
            if event is _CC_READER_EOF:
                break

            etype = event.get("type", "")
            st._hb_state["last_event_kind"] = etype
            _parent_tc_id = event.get("parent_tool_use_id") or ""
            # Raw event dump at DEBUG. Confirmed CC 1.0+ sends
            # complete `assistant` events (no content_block_delta)
            # with thinking blocks redacted (thinking="" + signature).
            logger.debug("[cc-raw] %s %.500s", etype, json.dumps(event))

            if etype == "system":
                # Capture AND persist session_id from init event immediately.
                # Must be in ConversationStore before any preempt triggers
                # _prepare_agent_context (which checks for session to skip compact).
                sid = event.get("session_id", "")
                if sid:
                    self._current_session_id = sid
                subtype = event.get("subtype", "")
                # Only the init event announces a (re)used session. Other
                # system events (compact_boundary, status, etc.) also
                # carry session_id — without this guard each one logged
                # "NEW session" with the same sid, flooding the logs.
                if sid and st.conv_id and subtype == "init":
                    _tag = (f"{st.user_id[:6] or '?'}/{st.conv_id[:8] or '?'}/"
                            f"{st.agent_name or 'default'}")
                    if st.session_id and sid != st.session_id:
                        logger.warning(
                            "[claude-code][%s] SESSION MISMATCH: sent --resume %s but CC returned %s "
                            "(resume FAILED — CC created new session)",
                            _tag, st.session_id[:12], sid[:12])
                    elif st.session_id and sid == st.session_id:
                        logger.info("[claude-code][%s] RESUME OK: session %s reused",
                                    _tag, sid[:12])
                    else:
                        logger.info("[claude-code][%s] NEW session: %s",
                                    _tag, sid[:12])
                    try:
                        from core.conversation_store import ConversationStore
                        ConversationStore.instance().set_extra(
                            st.conv_id,
                            f"claude_session:{st.agent_name or 'default'}",
                            sid)
                    except Exception:
                        logger.debug("exception suppressed", exc_info=True)
                # compact_boundary → drain CC stream + PawFlow compact; init → arm stall watchdog
                if subtype == "compact_boundary" or (
                        subtype == "status" and event.get("status") == "compacting"):
                    # Sentinel sessions (_compact, _memory_extract, …)
                    # are themselves PawFlow compactions. If CC saturates
                    # mid-summarization, let it run its own internal
                    # compact — interrupting would either loop forever
                    # (compact-of-compact) or destroy the in-flight
                    # summarization. Preempt-on-compact only applies
                    # to normal user sessions where PawFlow's bucket
                    # cache produces a better result than CC's auto.
                    _is_sentinel = st.conv_id.startswith("_") if st.conv_id else False
                    if _is_sentinel:
                        logger.info("[claude-code] CC self-compacting in "
                                     "sentinel '%s' — letting it continue",
                                     st.conv_id)
                        continue
                    if st._compact_pending[0]:
                        continue
                    logger.warning(
                        "[claude-code] CC compacting detected (subtype=%s) "
                        "— flushing pre-compact turn, killing CC, "
                        "PawFlow will compact", subtype)
                    # Set BEFORE killing so any racing send_user_message
                    # from another thread sees the flag and refuses,
                    # routing the user message via PendingQueue.
                    self._compacting = True
                    st._compact_pending[0] = True
                    # compact_boundary is the LAST useful event from CC
                    # for this turn — everything that follows is CC's own
                    # summary + post-compact work we do NOT want
                    # ingested. Do not drain. But the turn that fired
                    # compact may still hold unflushed events in the
                    # per-turn accumulator: if CC streamed
                    # tool_use + tool_result + assistant text inside the
                    # same msg_id and compact_boundary fired before the
                    # next msg_id rollover, those items were only in
                    # CC's .jsonl and never made it to the PawFlow
                    # transcript / webchat. Force-flush now so nothing
                    # emitted pre-compact is lost.
                    try:
                        self._ccs_flush_turn(st)
                    except Exception as _fe:
                        logger.error(
                            "[claude-code] pre-compact flush failed: %s",
                            _fe, exc_info=True)
                    # Kill host AND container-side claude. Without the
                    # container-side kill the claude CLI survives as an
                    # orphan inside the pool container and keeps running
                    # in parallel with the replacement session PawFlow
                    # is about to spawn.
                    self._kill_cc_hard(st.proc)
                    break
                if subtype == "init":
                    st._stall_start_time = time.monotonic()
                    st._got_assistant = False
                    if st._STALL_TIMEOUT > 0:
                        logger.info("[claude-code][%s/%s/%s] init — stall watchdog armed (%.0fs timeout)",
                                    st.user_id[:6] or '?', st.conv_id[:8] or '?',
                                    st.agent_name or 'default', st._STALL_TIMEOUT)
                    else:
                        logger.info("[claude-code][%s/%s/%s] init — stall watchdog disabled",
                                    st.user_id[:6] or '?', st.conv_id[:8] or '?',
                                    st.agent_name or 'default')
                continue

            if etype == "assistant":
                # Got a response — stall watchdog disarmed
                st._got_assistant = True
                st._last_tool_result_time = 0.0

                msg = event.get("message", {})
                msg_id = msg.get("id", "")
                # Capture freshest provider usage for cost/result metadata.
                # Do not publish it as the context gauge: the UI gauge is
                # PawFlow active-context usage, produced by
                # tasks.ai.context_usage, while provider prompt usage has a
                # different scope and can legitimately diverge mid-turn.
                _u = msg.get("usage")
                if isinstance(_u, dict) and _u != st._latest_usage:
                    st._latest_usage = _u

                # Claude Code sends INCREMENTAL updates for the same message:
                # event 1: [thinking], event 2: [text], event 3: [tool_use]
                # Each event has ONLY the new block, not all blocks.
                # Same msg_id = same turn → just append (don't clear).
                if msg_id and msg_id != st._current_msg_id:
                    # New message — flush previous turn
                    if st._turn_count > 0:
                        self._ccs_flush_turn(st)
                        self._ccs_inject_catchup(st)
                    st._turn_count += 1
                    st._current_msg_id = msg_id
                for block in msg.get("content", []):
                    btype = block.get("type", "")
                    if btype == "text":
                        text = block.get("text", "")
                        if text:
                            st._turn_text_parts.append(text)
                            st.content_parts.append(text)
                            if st.callback:
                                st.callback(text)
                    elif btype == "tool_use":
                        logger.debug("[CC-RAW-TOOL] block=%s", json.dumps(block, default=str, ensure_ascii=False))
                        _block_id = block.get("id", "")
                        _block_entry = {
                            "name": block.get("name", ""),
                            "arguments": block.get("input", {}),
                            "id": _block_id,
                        }
                        # Dedup: Claude Code may send the same tool_use block
                        # multiple times for the same msg_id (incremental updates).
                        # First time: input={} (empty), later: input={real args}.
                        # Replace by id instead of blindly appending.
                        _existing_idx = None
                        for _i, _tc in enumerate(st._turn_tool_calls):
                            if _tc.get("id") == _block_id:
                                _existing_idx = _i
                                break
                        if _existing_idx is not None:
                            st._turn_tool_calls[_existing_idx] = _block_entry
                        else:
                            st._turn_tool_calls.append(_block_entry)
                        st._pending_tool_ids.add(_block_id)
                        # Remember the unwrapped tool name for this
                        # id across the whole stream, not just the
                        # current turn. Used when tool_result comes
                        # back after the tool_use's turn has been
                        # flushed (common when CC emits many tool
                        # calls in quick succession).
                        try:
                            from core.llm_client import unwrap_mcp_tool
                            _persist_name, _ = unwrap_mcp_tool(
                                block.get("name", ""),
                                block.get("input", {}) or {})
                            if _block_id and _persist_name:
                                st._stream_tc_names[_block_id] = _persist_name
                                st._hb_state["last_dispatched_tc"] = (
                                    f"{_persist_name}({_block_id[:8]})")
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        # Unwrap MCP wrapper for display:
                        # mcp__pawflow__use_tool(tool_name=X, arguments={...})
                        # → X({...})  (with alias resolution: shell→bash etc.)
                        _tc_name = block.get("name", "")
                        _tc_args = block.get("input", {})
                        from core.llm_client import unwrap_mcp_tool
                        _tc_name, _tc_args = unwrap_mcp_tool(_tc_name, _tc_args)
                        # Don't emit SSE for empty-arg tool calls — likely
                        # an incremental update that will be followed by the
                        # real one with actual arguments.
                        if not _tc_args or _tc_args == {} or _tc_args == "{}":
                            logger.warning("[claude-code] skipping SSE for empty tool_use %s (id=%s) — awaiting args",
                                         _tc_name, _block_id)
                            continue
                        # Skip bash with empty/missing/whitespace command
                        if _tc_name == "bash" and isinstance(_tc_args, dict) and not str(_tc_args.get("command", "")).strip():
                            logger.warning("[claude-code] skipping SSE for bash with empty command (id=%s)", _block_id)
                            self._ccs_record_phantom(st, _tc_name, _block_id)
                            continue
                        # Skip any tool where ALL string values are empty
                        if isinstance(_tc_args, dict) and _tc_args and all(
                                not str(v).strip() for v in _tc_args.values()):
                            logger.warning("[claude-code] skipping SSE for %s with all-empty args (id=%s)", _tc_name, _block_id)
                            self._ccs_record_phantom(st, _tc_name, _block_id)
                            continue
                        # Skip meta tools from SSE
                        if _tc_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                            continue
                        _tc_event = {
                            "tool": _tc_name,
                            "arguments": _tc_args,
                            "tc_id": _block_id,
                            "agent_name": st.agent_name,
                            "llm_service": getattr(self, '_agent_service', ""),
                            "via": "claude-code",
                            "ts": time.time(),
                        }
                        if _parent_tc_id:
                            _tc_event["parent_tc_id"] = _parent_tc_id
                        # IMMUTABLE RULE: stdout → LLMMessage → writer
                        # → transcript/shared/ctx → SSE (post-write).
                        # Flush THIS tool_use block now via
                        # block_callback so the UI sees tool_call
                        # live (lets the user click BG/Kill while
                        # the tool is still running). Waiting for
                        # the turn boundary would hide the tool_call
                        # until AFTER tool_result landed — same
                        # block of SSE, no way to interject.
                        st._emitted_sse_tcs.add(_block_id)
                        # Register the CC tool_use id BEFORE block_callback
                        # — block_callback writes to the conversation store
                        # (multi-second I/O on Windows when the writer queue
                        # is busy), and during that wait the MCP bridge in
                        # the CC container forwards the same call to the
                        # relay. If we enqueue after block_callback, the
                        # relay's pop_cc_tc misses (its 500ms retry is too
                        # short to outwait a slow writer). The enqueue
                        # itself is a tiny in-memory dict update, so doing
                        # it first costs nothing.
                        try:
                            from core.background_tool import (
                                enqueue_cc_tc, _args_hash,
                            )
                            from core.llm_client import unwrap_mcp_tool
                            _match_name, _match_args = unwrap_mcp_tool(
                                _tc_name, _tc_args or {})
                            enqueue_cc_tc(
                                st.conv_id, st.agent_name, _block_id,
                                _match_name, _args_hash(_match_args))
                        except Exception as _ee:
                            logger.debug(
                                "[claude-code] enqueue_cc_tc skipped: %s",
                                _ee)
                        if st.block_callback:
                            try:
                                _bc_payload = {
                                    "id": _block_id,
                                    "name": block.get("name", "") or _tc_name,
                                    "arguments": block.get("input", _tc_args),
                                    "thinking": st._turn_thinking,
                                }
                                if _parent_tc_id:
                                    _bc_payload["parent_tc_id"] = _parent_tc_id
                                st.block_callback("tool_use", _bc_payload)
                                st._turn_thinking = ""
                            except Exception as _bc_err:
                                logger.error(
                                    "[claude-code] block_callback tool_use failed: %s",
                                    _bc_err, exc_info=True)
                    elif btype == "thinking":
                        thinking = block.get("thinking", "")
                        _has_sig = bool(block.get("signature"))
                        logger.info(
                            "[cc-stream] thinking block: len=%d sig=%s preview=%r",
                            len(thinking), _has_sig, thinking[:120])
                        if thinking:
                            # Raw reasoning text exposed (rare now;
                            # Anthropic redacts by default). Persist
                            # verbatim.
                            st._turn_thinking = thinking
                        elif _has_sig:
                            # Redacted thinking: signature without
                            # content. Mark the turn so _flush_turn
                            # can synthesize a "Thought for Xs"
                            # placeholder — user gets a visual cue
                            # that reasoning happened even though
                            # the API strips the text.
                            st._turn_thinking_redacted = True
                            if st._turn_thinking_start == 0.0:
                                st._turn_thinking_start = time.time()
                            st._turn_thinking_end = time.time()
                # Update turn count on status
                self._ccs_pub(st, "heartbeat", {
                    "agent_name": st.agent_name,
                    "status": f"turn {st._turn_count}",
                    "iteration": st._turn_count,
                })
                st.last_data = msg

            elif etype == "user":
                # Tool results — capture for persistence + forward to webchat
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_result":
                        logger.debug("[CC-RAW-RESULT] block=%s", json.dumps(block, default=str, ensure_ascii=False)[:2000])
                        tc_id = block.get("tool_use_id", "")
                        result_text = block.get("content", "")
                        if isinstance(result_text, list):
                            # Content blocks format
                            result_text = " ".join(
                                b.get("text", "") for b in result_text
                                if isinstance(b, dict))
                        result_str = str(result_text) if result_text else "(no output)"
                        # Store for turn_callback persistence
                        if tc_id:
                            st._tool_results[tc_id] = result_str
                            st._pending_tool_ids.discard(tc_id)
                            st._hb_state["last_tool_result_id"] = (
                                f"{tc_id[:8]}={len(result_str)}c")
                            if not st._pending_tool_ids:
                                st._last_tool_result_time = time.monotonic()
                        # Resolve tool name. Try the stream-scoped
                        # map first (survives turn flushes), fall
                        # back to _turn_tool_calls for robustness.
                        # Without this, the compact_result short-
                        # circuit would miss whenever the tool_use
                        # was in a flushed earlier turn.
                        _tr_name = st._stream_tc_names.get(tc_id, "")
                        if not _tr_name:
                            _tr_name = tc_id
                            for _tc in st._turn_tool_calls:
                                if _tc.get("id") == tc_id:
                                    from core.llm_client import unwrap_mcp_tool
                                    _tr_name, _ = unwrap_mcp_tool(
                                        _tc.get("name", tc_id), _tc.get("arguments", {}))
                                    break
                        # Skip meta tool results from SSE
                        if _tr_name in ("get_tool_schema", "mcp__pawflow__get_tool_schema"):
                            continue
                        # Suppress the tool_result SSE iff we suppressed the
                        # matching tool_call (phantom: empty args / empty
                        # bash command / all-empty args — see filters above).
                        # Output-based detection ("no command provided" in
                        # result_str) was wrong in two ways — it swallowed
                        # legitimate Read results containing the phrase in
                        # source, AND legitimate bash output containing it
                        # (git log picks up commit e59a188's own message:
                        # 'Fix: scope "no command provided" phantom filter
                        # to bash only' on any command touching this file).
                        # Phantom detection is input-only.
                        if tc_id and tc_id not in st._emitted_sse_tcs:
                            continue
                        _tr_event = {
                            "tool": _tr_name,
                            "result": result_str[:300],
                            "tc_id": tc_id,
                            "agent_name": st.agent_name,
                            "llm_service": getattr(self, '_agent_service', ""),
                            "via": "claude-code",
                        }
                        if _parent_tc_id:
                            _tr_event["parent_tc_id"] = _parent_tc_id
                        # IMMUTABLE RULE: stdout → LLMMessage → writer
                        # → transcript/shared/ctx → SSE (post-write).
                        # Flush THIS tool_result block now via
                        # block_callback. Paired with the live
                        # tool_use flush above: the tc_msg landed
                        # when CC emitted tool_use (UI saw it
                        # live); the tr_msg lands here when the
                        # result comes back. Together they replace
                        # the previous end-of-turn bundle where
                        # both landed together and the UI saw the
                        # tool_call only AFTER the result was in.
                        if st.block_callback:
                            try:
                                _br_payload = {
                                    "tc_id": tc_id,
                                    "tool": _tr_name,
                                    "result": result_str,
                                }
                                if _parent_tc_id:
                                    _br_payload["parent_tc_id"] = _parent_tc_id
                                st.block_callback("tool_result", _br_payload)
                                # Consumed by block_callback: drop
                                # from _tool_results so end-of-turn
                                # flush doesn't double-persist.
                                st._tool_results.pop(tc_id, None)
                            except Exception as _br_err:
                                logger.error(
                                    "[claude-code] block_callback tool_result failed: %s",
                                    _br_err, exc_info=True)
                        # compact_result is terminal: once CC has
                        # delivered the summary, everything it emits
                        # afterwards is post-summary fluff we don't
                        # ingest (and CC often just stalls waiting
                        # for more input until the 180s watchdog
                        # fires). Treat it like compact_boundary —
                        # flush the current turn and kill CC now so
                        # the caller (_summarize_via_cc) wakes up
                        # immediately instead of after a 3-minute
                        # stall timeout.
                        if _tr_name == "compact_result":
                            logger.info(
                                "[claude-code] compact_result "
                                "delivered — flushing + killing CC "
                                "to end stream")
                            try:
                                self._ccs_flush_turn(st)
                            except Exception as _fe:
                                logger.error(
                                    "[claude-code] pre-compact_result "
                                    "flush failed: %s",
                                    _fe, exc_info=True)
                            self._kill_cc_hard(st.proc)
                            st._compact_result_done = True
                            break
                if st._compact_result_done:
                    break

            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                _delta_type = delta.get("type", "")
                # Extended thinking is streamed as a sequence of
                # content_block_delta events carrying `thinking_delta`
                # (with `delta.thinking` = chunk) and a trailing
                # `signature_delta` (with `delta.signature`). CC's
                # session.jsonl typically persists the final
                # `thinking` block with thinking="" + signature only
                # (redacted at rest), so the ONLY place the raw
                # reasoning text is visible is on the wire here.
                # Without this branch the pipeline sees
                # `turn_thinking=0` even when the model produced
                # real reasoning.
                text = delta.get("text", "")
                thinking_delta = delta.get("thinking", "")
                if thinking_delta or _delta_type == "thinking_delta":
                    # Some server versions put the chunk in
                    # `delta.thinking`, others use a nested shape;
                    # cover both.
                    _chunk = thinking_delta or delta.get("text", "")
                    if _chunk:
                        st._turn_thinking += _chunk
                        logger.debug(
                            "[cc-stream] thinking_delta: +%d (total=%d)",
                            len(_chunk), len(st._turn_thinking))
                elif text:
                    st._turn_text_parts.append(text)
                    st.content_parts.append(text)
                    if st.callback:
                        st.callback(text)

            elif etype == "result":
                _act = self._ccs_on_result(st, event)
                if _act == "break":
                    break
                if _act == "continue":
                    continue

    def _ccs_reader_daemon(self, st):
        try:
            for _line in st.proc.stdout:
                if st._reader_stop.is_set():
                    break
                st._hb_state["stream_line_count"] += 1
                st._hb_state["last_event_ts"] = time.monotonic()
                # Reset cc-live idle on EVERY line received from
                # CC's stdout. This is the simplest correct
                # invariant: any byte coming back from CC means
                # the session is actively streaming — the idle
                # sweeper must not race with any in-flight
                # turn, init handshake, slow tool reply, long
                # thinking block, etc. bump_reuse=False because
                # one stream call is one logical reuse — the
                # counter is bumped at REUSE entry, not per
                # line.
                if st._live_key is not None:
                    try:
                        st._live_reg.touch(st._live_key, bump_reuse=False)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _ev = json.loads(_line)
                except json.JSONDecodeError:
                    continue
                # Defensive: event must be a dict. When the stream
                # is wrapped in a PTY (script -qfc), extra terminal
                # output like "Script started" banners or control
                # sequences can produce parseable-but-non-dict
                # JSON (e.g. a bare string literal). Log once and
                # skip instead of exploding on .get().
                if not isinstance(_ev, dict):
                    logger.warning(
                        "[claude-code] non-dict JSON ignored (%s): %r",
                        type(_ev).__name__, str(_ev)[:200])
                    continue
                st._event_q.put(_ev)
        except Exception as _re_err:
            logger.debug("[cc-reader] stdout read failed: %s", _re_err)
        finally:
            st._event_q.put(_CC_READER_EOF)

    def _ccs_stall_watchdog(self, st):
        pass  # _stall_killed is on self
        while not st._watchdog_stop.is_set():
            if st._STALL_TIMEOUT > 0 and st._stall_start_time and not st._got_assistant:
                elapsed = time.monotonic() - st._stall_start_time
                if elapsed >= st._STALL_TIMEOUT:
                    logger.warning(
                        "[claude-code] Stall detected (%.0fs with no assistant "
                        "response, budget=%.0fs) — killing process for retry. "
                        "hb: lines_read=%d last_event=%s@%.0fs last_tc=%s "
                        "last_tr=%s pending=%s",
                        elapsed, st._STALL_TIMEOUT,
                        st._hb_state["stream_line_count"],
                        st._hb_state["last_event_kind"] or "(none)",
                        time.monotonic() - st._hb_state["last_event_ts"]
                          if st._hb_state["last_event_ts"] else -1,
                        st._hb_state["last_dispatched_tc"] or "(none)",
                        st._hb_state["last_tool_result_id"] or "(none)",
                        sorted(st._pending_tool_ids)[:5])
                    self._stall_killed = True
                    try:
                        st.proc.kill()
                    except OSError:
                        pass
                    return
            # Tool result stall: all tools resolved but no assistant response
            if st._STALL_TIMEOUT > 0 and st._last_tool_result_time and not st._pending_tool_ids:
                elapsed = time.monotonic() - st._last_tool_result_time
                if elapsed >= st._STALL_TIMEOUT:
                    logger.warning(
                        "[claude-code] Tool-result stall (%.0fs since last "
                        "tool_result, no pending tools, no assistant) "
                        "— killing for retry. hb: lines_read=%d "
                        "last_event=%s@%.0fs last_tc=%s last_tr=%s",
                        elapsed,
                        st._hb_state["stream_line_count"],
                        st._hb_state["last_event_kind"] or "(none)",
                        time.monotonic() - st._hb_state["last_event_ts"]
                          if st._hb_state["last_event_ts"] else -1,
                        st._hb_state["last_dispatched_tc"] or "(none)",
                        st._hb_state["last_tool_result_id"] or "(none)")
                    self._stall_killed = True
                    try:
                        st.proc.kill()
                    except OSError:
                        pass
                    return
            # Sentinel-session EOF nudge: when a _compact /
            # _memory_extract session goes silent for
            # _SENTINEL_EOF_INTERVAL AND we're not waiting on a
            # pending tool, close proc.stdin. CC sees EOF on its
            # stdin (stream-json input is done) and finalises its
            # current turn — LLM reply, any pending tool_use,
            # compact_result — then exits cleanly. This replicates
            # what the stall watchdog's proc.kill() incidentally
            # achieves (pipe close on our side → Python unblocks
            # from readline), but WITHOUT killing CC. One-shot per
            # stream: once stdin is closed we can't re-open it, so
            # stdin_closed flag guards re-entry.
            if (st._is_sentinel_conv
                    and not st._hb_state["stdin_closed"]
                    and st._hb_state["last_turn_flush_ts"]
                    and not st._pending_tool_ids):
                _since_turn = (time.monotonic()
                                - st._hb_state["last_turn_flush_ts"])
                if _since_turn >= st._SENTINEL_EOF_INTERVAL:
                    try:
                        if st.proc.stdin and not st.proc.stdin.closed:
                            st.proc.stdin.close()
                            st._hb_state["stdin_closed"] = True
                            logger.info(
                                "[claude-code] sentinel '%s' idle "
                                "%.0fs since last turn — closed "
                                "stdin (EOF nudge, NOT a kill)",
                                st.conv_id, _since_turn)
                    except (OSError, BrokenPipeError) as _eof_err:
                        logger.debug(
                            "[claude-code] EOF nudge failed: %s",
                            _eof_err)

            # DEBUG heartbeat every 30s. Kept at debug so default
            # deployments don't log every half-minute on every
            # healthy stream; enable when chasing a specific hang
            # via the usual logger config. The stall watchdog's
            # kill log still fires at WARNING with the same state
            # snapshot for the worst case.
            st._watchdog_dbg_count += 1
            if st._watchdog_dbg_count % 6 == 0:  # every 30s
                _now = time.monotonic()
                _since_evt = (_now - st._hb_state["last_event_ts"]
                               if st._hb_state["last_event_ts"] else -1)
                _since_tr = (_now - st._last_tool_result_time
                              if st._last_tool_result_time else -1)
                logger.debug(
                    "[claude-code] hb: lines_read=%d last_event=%s (%.0fs ago) "
                    "last_tc=%s last_tr=%s pending=%s got_asst=%s since_tr=%.0fs",
                    st._hb_state["stream_line_count"],
                    st._hb_state["last_event_kind"] or "(none)",
                    _since_evt,
                    st._hb_state["last_dispatched_tc"] or "(none)",
                    st._hb_state["last_tool_result_id"] or "(none)",
                    sorted(st._pending_tool_ids)[:5],
                    st._got_assistant, _since_tr)
            st._watchdog_stop.wait(5)

