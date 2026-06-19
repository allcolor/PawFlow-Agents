"""Claude Code streaming turn helpers: publish, flush, catch-up, phantom."""


import json
import logging
import time

from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND  # noqa: F401
from core.llm_providers._cc_base import (
    _CC_READER_EOF, _CC401Retry, _CCStreamState)  # noqa: F401

logger = logging.getLogger(__name__)


class _CCStreamTurnMixin:
    """Claude Code streaming turn helpers: publish, flush, catch-up, phantom."""
    def _ccs_pub(self, st, event_type, data):
        # Safety net: any tool_call/tool_result that escaped unwrap
        # (raw `mcp__pawflow__use_tool` / `use_tool` name with wrapped
        # args) gets unwrapped here before it reaches the UI / subagent
        # relay. Prevents `use_tool(tool_name=read, arguments=[object
        # Object])` from ever being displayed.
        if event_type in ("tool_call", "tool_result") and isinstance(data, dict):
            _t = data.get("tool", "")
            if _t in ("mcp__pawflow__use_tool", "use_tool"):
                try:
                    from core.llm_client import unwrap_mcp_tool, _decode_str_arg
                    _raw_args = data.get("arguments", {}) or {}
                    # Canonical decode (autoclose + repair), identical to the
                    # execution path; keeps the original on genuine failure.
                    _raw_args = _decode_str_arg(_raw_args)
                    _u_name, _u_args = unwrap_mcp_tool(_t, _raw_args)
                    # If unwrap didn't resolve (still the wrapper name),
                    # fall back to reading tool_name from the raw args
                    # so the UI never shows `use_tool(...)`.
                    if _u_name in ("mcp__pawflow__use_tool", "use_tool") and isinstance(_raw_args, dict):
                        _u_name = _raw_args.get("tool_name", _t) or _t
                        _inner = _decode_str_arg(_raw_args.get("arguments", _raw_args))
                        _u_args = _inner if isinstance(_inner, dict) else _raw_args
                    data["tool"] = _u_name
                    if event_type == "tool_call":
                        data["arguments"] = _u_args
                    # Only log when the unwrap actually produced a
                    # different name — "X → X" is noise from the
                    # no-op branch where raw_args had no usable
                    # tool_name to peel.
                    if _u_name != _t:
                        logger.warning(
                            "[claude-code] _pub safety-net unwrapped %s → %s",
                            _t, _u_name)
                except Exception as _unwrap_err:
                    logger.warning(
                        "[claude-code] _pub safety-net unwrap failed "
                        "for tool=%s event=%s: %s",
                        _t, event_type, _unwrap_err, exc_info=True)
        if st._subagent_event_cb:
            try:
                st._subagent_event_cb(event_type, data)
            except Exception as _sub_err:
                # Never-swallow: log loudly. Do NOT raise — raising
                # here would kill the CC stream parse loop for the
                # rest of the turn. Log is the pragmatic floor
                # (user rule: at minimum log).
                logger.error(
                    "[claude-code] subagent_event_cb failed for "
                    "event=%s: %s", event_type, _sub_err, exc_info=True)
            # Subagent events relay to parent via the callback ONLY;
            # they must NOT also hit the parent conv's event bus,
            # otherwise the UI gets duplicates.
            return
        if not st._event_cid:
            return
        if st._task_id:
            data['task_id'] = st._task_id
            data['task_iteration'] = st._agent_ctx.get("_task_iteration", 0)
        # If this turn is a delegate reply, tag the event with
        # agent_delegate source so the UI groups it under the private
        # delegate block instead of the main chat.
        _tm = st._agent_ctx.get("_turn_mode") or {}
        if (_tm.get("type") == "delegate_reply"
                and _tm.get("source_agent")
                and "source" not in data):
            data["source"] = {
                "type": "agent_delegate",
                "from": st.agent_name or "",
                "to": _tm["source_agent"],
            }
        # Invariant: user-visible state MUST be on disk before we
        # publish the SSE that makes it visible. For message_meta with
        # context_usage, persist the extras synchronously first.
        # (Earlier commit 056b99e moved this AFTER publish on a daemon
        # thread to dodge a lock contention issue; that violated the
        # "visible = persisted" invariant — if the extras lock blocks,
        # we log loudly and still publish so the gauge doesn't freeze,
        # but we never skip logging the failure.)
        # Persist BEFORE publish (strict visible=persisted invariant):
        # if the gauge value fails to hit disk, don't show a live SSE
        # value that will disappear on reload — the UI and the
        # persisted state would disagree. Log loudly and skip the
        # publish so the inconsistency is visible in logs rather than
        # silently drifting.
        #
        # With `get_extra*` readers now holding the same per-conv
        # lock as `set_extra`, there's no concurrent file handle on
        # `extras.json` during the atomic rename — `os.replace`
        # cannot be blocked by our own reads anymore. A
        # PermissionError here now signals a real OS-level problem
        # (disk full, genuine permission issue) and is a bug worth
        # investigating, not masking.
        _persist_ok = True
        if (event_type == "message_meta"
                and isinstance(data, dict)
                and (data.get("context_used") or 0) > 0
                and (data.get("context_max") or 0) > 0
                and st.agent_name):
            try:
                from core.conversation_store import ConversationStore as _CS_pub
                _store_pub = _CS_pub.instance()
                _cu_map = _store_pub.get_extra(
                    st._event_cid, "context_usage") or {}
                _cu_map[st.agent_name] = {
                    "used": int(data["context_used"]),
                    "max": int(data["context_max"]),
                    "pct": float(data.get("context_pct") or 0),
                    "updated_at": int(time.time()),
                }
                _store_pub.set_extra(
                    st._event_cid, "context_usage", _cu_map)
            except Exception as _ctx_err:
                _persist_ok = False
                logger.error(
                    "[claude-code] context_usage persist FAILED "
                    "for cid=%s agent=%s: %s — SKIPPING SSE publish "
                    "to keep visible=persisted invariant. This is a "
                    "real bug to investigate (not a transient retry "
                    "case).",
                    st._event_cid, st.agent_name, _ctx_err, exc_info=True)
        if not _persist_ok:
            return
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                st._event_cid, event_type, data)
        except Exception as _pub_err:
            logger.error(
                "[claude-code] publish_event failed for event=%s "
                "cid=%s: %s", event_type, st._event_cid, _pub_err,
                exc_info=True)

    def _ccs_inject_catchup(self, st):
        """Check for new messages from other agents and inject via stdin."""
        if not st.conv_id or not st.agent_name:
            return
        catchup = self._build_catchup_context(st.conv_id, st.agent_name)
        if not catchup:
            return
        _p = getattr(self, '_claude_proc', None)
        if _p and _p.poll() is None:
            msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": catchup},
            })
            _p.stdin.write(msg + "\n")
            _p.stdin.flush()
            self._preempt_pending = getattr(self, '_preempt_pending', 0) + 1

    def _ccs_flush_turn(self, st):
        """Emit the accumulated turn via turn_callback."""
        text = "".join(st._turn_text_parts).strip()
        # Drop phantom tool calls: empty inner args + no result (never executed)
        # For MCP wrapped calls, check the inner arguments, not the wrapper
        def _has_real_args(t):
            # Phantom detection is input-only: empty args, empty bash
            # command, or all-whitespace values. Output-based matching
            # ("no command provided" in result) was removed — a tool's
            # OUTPUT can legitimately contain any phrase (git log of a
            # commit whose message is about the filter itself, grep
            # over this source file, etc.) and we were silently
            # dropping real calls + results from the transcript.
            args = t.get("arguments", {})
            if not args or args == {}:
                return False
            # MCP wrapper: check inner arguments
            if t.get("name") == "mcp__pawflow__use_tool" and isinstance(args, dict):
                inner = args.get("arguments", {})
                # Tolerate flat args: LLM sometimes forgets the "arguments"
                # wrapper and places tool args at the top level next to
                # tool_name. Harvest them so the call isn't dropped as
                # phantom. Symmetric with mcp_bridge.py's flat-args harvest.
                if not inner or inner == {}:
                    _flat = {k: v for k, v in args.items() if k != "tool_name"}
                    if not _flat:
                        return False
                    inner = _flat
                # bash with empty/whitespace command (resolve aliases)
                from core.llm_client import _TOOL_ALIASES
                inner_tool = args.get("tool_name", "")
                inner_tool = _TOOL_ALIASES.get(inner_tool, inner_tool)
                if inner_tool == "bash" and isinstance(inner, dict) and not str(inner.get("command", "")).strip():
                    return False
                # Any tool where all string values are empty
                if isinstance(inner, dict) and inner and all(
                        not str(v).strip() for v in inner.values()):
                    return False
            # Non-MCP bash with empty command
            if t.get("name") == "bash" and isinstance(args, dict) and not str(args.get("command", "")).strip():
                return False
            return True
        _real = [t for t in st._turn_tool_calls if _has_real_args(t)]
        _dropped = len(st._turn_tool_calls) - len(_real)
        if _dropped:
            _dropped_tcs = [t for t in st._turn_tool_calls if not _has_real_args(t)]
            logger.warning("[CC-DROPPED] %d phantom tool call(s): %s", _dropped,
                         json.dumps(_dropped_tcs, default=str, ensure_ascii=False)[:3000])
        # Skip tool calls already persisted live via block_callback so the
        # turn flush does not persist them a second time (mirrors the
        # _tool_results.pop dedup for results). Empty set when block_callback
        # is disabled, so this is a no-op for that path.
        tc = [t for t in _real if t.get("id") not in st._block_persisted_tc_ids]
        turn_thinking = st._turn_thinking
        # Redacted thinking synthesis: if CC sent thinking blocks with
        # signature but no content (Anthropic API policy — reasoning
        # is encrypted at the API level), synthesize a user-visible
        # placeholder so the UI still renders a "Thought for Xs"
        # bubble instead of silently dropping the signal.
        if (not turn_thinking) and st._turn_thinking_redacted:
            _dur_s = max(0.0, st._turn_thinking_end - st._turn_thinking_start)
            turn_thinking = (
                f"[Thought for {_dur_s:.1f}s — reasoning content "
                f"redacted by the Anthropic API; the signature is "
                f"preserved in the session so the chain of thought "
                f"is carried forward on resume.]")
        # Attach results to tool calls
        for t in tc:
            t["result"] = st._tool_results.pop(t.get("id", ""), None)
        # Attach thinking to first tool_call (legacy tc_msg carrier)
        # AND pass it through as a 3rd positional to turn_callback so
        # text-only turns (no tool_calls) can still persist it on the
        # assistant text message. Without the 3rd positional, thinking
        # is lost whenever the LLM's reply is pure text.
        _tc_thinking = turn_thinking
        for t in tc:
            t["thinking"] = _tc_thinking
            _tc_thinking = ""  # only first tc gets thinking
        st._turn_text_parts = []
        st._turn_tool_calls = []
        st._turn_thinking = ""
        st._turn_thinking_redacted = False
        st._turn_thinking_start = 0.0
        st._turn_thinking_end = 0.0
        # Mark the most recent turn flush — the sentinel-session
        # EOF nudger in _stall_watchdog uses this as its silence
        # threshold anchor.
        st._hb_state["last_turn_flush_ts"] = time.monotonic()
        # cc-live idle is reset by the stdout reader daemon on every
        # line received — see the touch in _reader_daemon. No need
        # for a per-turn touch here.
        # Phantom-only turn: CC emitted a tool_call we dropped at
        # phantom detection (typo in param name, empty bash command,
        # whitespace-only args) AND nothing else. Without `text` or
        # surviving `tc`, the only thing left is `turn_thinking` —
        # but that thinking was the model "explaining" the phantom
        # call. Keeping it would persist an orphan assistant row
        # (content_len=0, tool_calls=0, thinking_len>0) that the
        # UI renders as a stray "Thought for Xs" bubble polluting
        # the chat. Drop the turn entirely.
        _phantom_only = (_dropped > 0 and not tc and not text)
        if _phantom_only:
            logger.info(
                "[claude-code] flush turn %d SKIPPED: phantom-only "
                "(dropped %d tc, no text, thinking=%d) — not persisted",
                st._turn_count, _dropped, len(turn_thinking))
        if (text or tc or turn_thinking) and st.turn_callback and not _phantom_only:
            logger.info("[claude-code] flush turn %d: text=%d chars, tc=%d, thinking=%d, callback=%s",
                        st._turn_count, len(text), len(tc), len(turn_thinking), bool(st.turn_callback))
            try:
                # Back-compat: old callbacks accept (text, tc). New
                # callbacks accept (text, tc, thinking). Introspect
                # once so we don't break the surface for anyone.
                import inspect as _insp
                try:
                    _nparams = len(_insp.signature(st.turn_callback).parameters)
                except (TypeError, ValueError):
                    _nparams = 2
                if _nparams >= 3:
                    st.turn_callback(text, tc, turn_thinking)
                else:
                    st.turn_callback(text, tc)
            except Exception as e:
                logger.error("[claude-code] turn_callback error: %s", e,
                             exc_info=True)
        elif text or tc:
            # Internal sentinel sessions (e.g. "_compact" summarizer,
            # "_memory_extract") run without a turn_callback by design —
            # they aggregate the result in content_parts. Log at INFO
            # so that summarizer / memory-extract behavior is visible
            # when these sessions misbehave (CC saturating, looping on
            # phantom tool calls, etc.). Includes a tool-name digest
            # so debugging doesn't require enabling DEBUG everywhere.
            _is_sentinel = st.conv_id.startswith("_") if st.conv_id else False
            # mcp__pawflow__use_tool is the meta-dispatch tool — the
            # ACTUAL useful info is in its `tool_name` argument
            # ("read", "compact_result", …). Without unwrapping it,
            # every log line just says "use_tool" and you can't tell
            # the summarizer apart from a phantom call.
            def _tc_label(t):
                name = t.get("name", "?")
                args = t.get("arguments") or {}
                if name == "mcp__pawflow__use_tool" and isinstance(args, dict):
                    inner = args.get("tool_name") or "?"
                    inner_args = args.get("arguments") or {}
                    # Add a single distinguishing arg per inner tool
                    if inner == "read":
                        _p = (inner_args.get("path") or "")[:24]
                        _o = inner_args.get("offset")
                        _l = inner_args.get("limit")
                        return (f"use_tool/read({_p}"
                                + (f",off={_o}" if _o else "")
                                + (f",lim={_l}" if _l else "") + ")")
                    if inner == "compact_result":
                        _slen = len(str(inner_args.get("summary", "")))
                        return f"use_tool/compact_result(summary={_slen}c)"
                    return f"use_tool/{inner}"
                return name
            _tc_names = ",".join(_tc_label(t) for t in tc)[:200]
            if _is_sentinel:
                logger.info("[claude-code] flush turn %d (sentinel '%s'): "
                            "text=%d, tc=%d [%s]",
                            st._turn_count, st.conv_id, len(text), len(tc),
                            _tc_names)
            else:
                logger.warning("[claude-code] flush turn %d but NO turn_callback: "
                               "text=%d, tc=%d [%s]",
                               st._turn_count, len(text), len(tc), _tc_names)
            # Tell webchat to finalize current streaming element
            self._ccs_pub(st, "turn_complete", {
                "agent_name": st.agent_name,
                "turn": st._turn_count,
            })
            # Clear content_parts — intermediate turns are persisted
            # by turn_callback. Only the LAST turn stays in content_parts
            st.content_parts.clear()

    def _ccs_record_phantom(self, st, tool_name, block_id):
        """Record a phantom tool call. If threshold exceeded, trigger compact."""
        now = time.monotonic()
        st._phantom_timestamps.append(now)
        # Prune entries outside window
        cutoff = now - st._PHANTOM_WINDOW
        while st._phantom_timestamps and st._phantom_timestamps[0] < cutoff:
            st._phantom_timestamps.pop(0)
        count = len(st._phantom_timestamps)
        if count >= st._PHANTOM_THRESHOLD:
            logger.warning(
                "[claude-code] %d phantom tool calls in %ds window "
                "(latest: %s id=%s) -- killing CC, PawFlow will compact",
                count, st._PHANTOM_WINDOW, tool_name, block_id)
            if st._compact_pending[0]:
                return
            self._compacting = True
            st._compact_pending[0] = True
            # Same rationale as the compact_boundary branch: flush any
            # real pre-phantom turn still sitting in the per-turn
            # accumulator, then kill host + container-side claude CLI
            # immediately. No drain window — phantom tool calls are
            # the symptom of a blown context and keeping the stream
            # open only lets CC emit more garbage that we'd pollute
            # the transcript with.
            try:
                self._ccs_flush_turn(st)
            except Exception as _fe:
                logger.error(
                    "[claude-code] pre-phantom-compact flush failed: %s",
                    _fe, exc_info=True)
            self._kill_cc_hard(st.proc)

