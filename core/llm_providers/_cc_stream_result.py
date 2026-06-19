"""Claude Code streaming: result-event handler + turn finalize."""


import logging
import os
import time

from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND  # noqa: F401
from core.llm_providers._cc_base import (
    _CC_READER_EOF, _CC401Retry, _CCStreamState)  # noqa: F401

logger = logging.getLogger(__name__)


class _CCStreamResultMixin:
    """Claude Code streaming: result-event handler + turn finalize."""
    def _ccs_on_result(self, st, event):
        from core.llm_client import LLMClientError
        from core.conversation_store import ConversationStore
        self._ccs_flush_turn(st)
        # Check for API errors (auth failure, rate limit, etc.)
        if event.get("is_error") or event.get("subtype") == "error_during_execution":
            _err_text = event.get("result", "")
            _errors = event.get("errors", [])
            if _errors:
                _err_text = _err_text or "; ".join(
                    e.get("message", str(e)) if isinstance(e, dict) else str(e)
                    for e in _errors)
                logger.error("[claude-code] errors: %s", _errors)
            # Dump the full event body + any stderr lines we've
            # accumulated — useful when the "error" is an opaque
            # "empty or malformed response" (CC's HTTP client
            # sometimes swallows the upstream body and we need
            # stderr or the raw event to see what really failed).
            try:
                _stderr_snapshot = "".join(
                    getattr(self, "_stderr_buffer", []) or [])[-4000:]
                # api_error_status = HTTP status CC's http
                # client actually received (None = never got
                # a response). duration_api_ms = how long CC
                # waited for the response — a few ms means
                # the connection failed fast (DNS/refused),
                # hundreds of ms means the server answered
                # (so the bug is upstream of CC's parser).
                _api_status = event.get("api_error_status")
                _api_ms = event.get("duration_api_ms")
                _term = event.get("terminal_reason", "")
                _stop = event.get("stop_reason", "")
                _usage = event.get("usage", {}) or {}
                logger.error(
                    "[claude-code] is_error result: "
                    "api_error_status=%r duration_api_ms=%r "
                    "terminal_reason=%r stop_reason=%r "
                    "subtype=%r _err_text=%r stderr_tail=%r",
                    _api_status, _api_ms, _term, _stop,
                    event.get("subtype"),
                    _err_text[:500], _stderr_snapshot)
                # Full event dump at DEBUG — re-enable at
                # INFO only when investigating an is_error
                # failure (e.g. zero-iteration / proxy
                # intercept). Happy path never reaches here.
                if logger.isEnabledFor(logging.DEBUG):
                    import json as _json
                    logger.debug(
                        "[claude-code] is_error full event: %s",
                        _json.dumps(event, default=str,
                                     ensure_ascii=False)[:4000])
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            _lower = _err_text.lower()
            _is_auth = (
                "authentication" in _lower
                or "401" in _err_text
                or "not logged in" in _lower
                or "please run /login" in _lower
                or "unauthorized" in _lower
            )
            if _is_auth:
                if not st._auth_retried:
                    st._auth_retried = True
                    # Step 1: force-refresh the current pool
                    # credential. 'Not logged in' often just
                    # means the access_token expired; the
                    # refresh_token is usually still valid.
                    # Only if refresh ALSO fails do we mark
                    # the slot dead and rotate.
                    _bad_idx = getattr(
                        self, '_current_pool_index', st._resume_pool_idx)
                    _refreshed = False
                    try:
                        if _bad_idx >= 0:
                            _refreshed = self._force_refresh_pool_entry(
                                _bad_idx, user_id=st.user_id,
                                conversation_id=st.conv_id)
                    except Exception as _rf_err:
                        logger.warning(
                            "[claude-code] force-refresh pool[%s] "
                            "failed: %s", _bad_idx, _rf_err)
                    # Always invalidate the CC session — the
                    # old jsonl was bound to the dead token
                    # and CC won't accept a new token on the
                    # same --resume session.
                    if st.conv_id:
                        try:
                            ConversationStore.instance().set_extra(
                                st.conv_id,
                                f"claude_session:{st.agent_name or 'default'}",
                                "")
                        except Exception:
                            logger.debug("exception suppressed", exc_info=True)
                    try:
                        if _refreshed:
                            logger.warning(
                                "[claude-code] auth failure "
                                "('%s') — refreshed pool[%s], "
                                "retrying same slot",
                                _err_text[:100], _bad_idx)
                            self._setup_credentials(
                                st.workdir, pool_index=_bad_idx,
                                user_id=st.user_id,
                                conversation_id=st.conv_id)
                        else:
                            _tried = getattr(self, '_tried_pool_idx', set())
                            _tried = set(_tried) | {_bad_idx}
                            self._tried_pool_idx = _tried
                            logger.warning(
                                "[claude-code] auth failure "
                                "('%s') — refresh failed, "
                                "rotating OAuth pool (tried=%s)",
                                _err_text[:100], sorted(_tried))
                            self._setup_credentials(
                                st.workdir, pool_index=-1,
                                exclude_indices=_tried,
                                user_id=st.user_id,
                                conversation_id=st.conv_id)
                    except Exception as _ref_err:
                        raise LLMClientError(
                            f"Claude Code auth failed and "
                            f"recovery failed: {_ref_err}") from None
                    raise _CC401Retry()
                raise LLMClientError(
                    f"Claude Code auth failed (all pool "
                    f"credentials exhausted): {_err_text[:300]}")
            if event.get("subtype") == "error_during_execution":
                # Include the error code/text so LLMClient retry loop can match it
                raise LLMClientError(f"Claude Code error: {_err_text[:300]}")
            # is_error without error_during_execution: API error (500, 429, etc.)
            # Raise so it reaches the retry loop in LLMClient
            if _err_text:
                raise LLMClientError(f"Claude Code API error: {_err_text[:300]}")
            logger.warning("[claude-code] result has is_error=True but no details")
        result_text = event.get("result", "")
        if not st.turn_callback and result_text and not st.content_parts:
            st.content_parts.append(result_text)
            if st.callback:
                st.callback(result_text)
        st.last_data = event
        # Publish token stats for the webchat
        _usage = event.get("usage", {})
        # Total input = direct + cache_read + cache_creation
        _total_in = (_usage.get("input_tokens", 0)
                     + _usage.get("cache_read_input_tokens", 0)
                     + _usage.get("cache_creation_input_tokens", 0))
        _total_out = _usage.get("output_tokens", 0)
        # model is in modelUsage keys, not at top level
        _model_usage = event.get("modelUsage", {})
        # Fallback: if usage is empty, sum from modelUsage
        if not _total_in and not _total_out and _model_usage:
            for _mu in _model_usage.values():
                _total_in += (_mu.get("inputTokens", 0)
                              + _mu.get("input_tokens", 0)
                              + _mu.get("cacheReadInputTokens", 0)
                              + _mu.get("cache_read_input_tokens", 0)
                              + _mu.get("cacheCreationInputTokens", 0)
                              + _mu.get("cache_creation_input_tokens", 0))
                _total_out += (_mu.get("outputTokens", 0)
                               + _mu.get("output_tokens", 0))
        logger.info("[claude-code] result: usage=%s, modelUsage=%s, tokens=%d/%d",
                    _usage, _model_usage, _total_in, _total_out)
        # Prefer event.get("model") (CC's authoritative
        # answer for this turn). When absent, prefer the
        # REQUESTED model if present in modelUsage — picking
        # the first dict key is non-deterministic and can
        # land on a side-task model (e.g. haiku used for
        # summarization while opus runs the turn), giving
        # the wrong contextWindow.
        _event_model = event.get("model") or ""
        if _event_model:
            _result_model = _event_model
        elif _model_usage and st.model in _model_usage:
            _result_model = st.model
        elif _model_usage:
            _result_model = list(_model_usage.keys())[0]
        else:
            _result_model = st.model
        if _total_in or _total_out:
            # Get the msg_id of the last assistant message (from turn_callback)
            _last_msg_id = ""
            try:
                _last_msg_id = getattr(self, '_last_turn_msg_id', "") or ""
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            # Context-fill: exact value from CC stream's last
            # assistant.message.usage (prompt size at that point).
            # Budget comes from CC itself: result.modelUsage[model]
            # .contextWindow is CC's authoritative value
            # (src/utils/context.ts::getContextWindowForModel) —
            # accounts for 200k default, [1m] suffix beta,
            # CLAUDE_CODE_MAX_CONTEXT_TOKENS overrides, etc.
            # Cache CC's reported contextWindow for the central
            # PawFlow gauge denominator. Do not use provider prompt
            # usage as `context_used`: active PawFlow context size
            # is computed in tasks.ai.context_usage.
            _cc_mu = _model_usage.get(_result_model) if _model_usage else None
            _cc_ctx_window = 0
            if isinstance(_cc_mu, dict):
                _cc_ctx_window = int(_cc_mu.get("contextWindow")
                                      or _cc_mu.get("context_window")
                                      or 0)
            # No iterate-and-pick-first fallback: that path
            # silently returned haiku's 200k when modelUsage
            # held both haiku (side-task) and opus (turn),
            # giving the central gauge a wrong denominator. If
            # _result_model has no contextWindow, leave the
            # previous per-stream value unchanged.
            # Per-stream cache keyed by (conv, agent) — the
            # singleton self._cc_context_window was clobbered
            # across concurrent streams (memory_extract /
            # _compact / multi-agent), so an opus turn read
            # back haiku's 200k after an unrelated stream had
            # written it.
            _cw_key = (st.conv_id or "", st.agent_name or "")
            _cw_map = getattr(self, '_cc_context_window_by_stream', None)
            if _cw_map is None:
                _cw_map = {}
                self._cc_context_window_by_stream = _cw_map
            if _cc_ctx_window > 0:
                _cw_map[_cw_key] = _cc_ctx_window
            self._ccs_pub(st, "message_meta", {
                "msg_id": _last_msg_id,
                "agent_name": st.agent_name,
                "source": {
                    "type": "agent", "name": st.agent_name,
                    "llm_service": getattr(self, '_agent_service', ""),
                    "provider": "claude-code",
                    "model": _result_model,
                    "tokens_in": _total_in,
                    "tokens_out": _total_out,
                },
                "model": _result_model,
                "provider": "claude-code",
                "tokens_in": _total_in,
                "tokens_out": _total_out,
                "num_turns": event.get("num_turns", st._turn_count),
                "duration_ms": event.get("duration_ms", 0),
            })
        # If one or more preempts were injected via stdin
        # BEFORE this result event, CC may already be processing
        # them in a new turn (observed live: CC reads stdin right
        # after emitting `result` and starts a fresh assistant
        # turn). Breaking here would kill the subprocess while
        # CC is generating the preempt's response, losing it.
        # Decision flow (deterministic via CC's session jsonl):
        #   - 'done': every queued preempt has an assistant
        #      message AFTER it in jsonl → break safely.
        #   - 'pending': preempt visible in jsonl AFTER last
        #      assistant → CC has read stdin and WILL respond;
        #      keep the stream open with NO timeout (response
        #      may take up to ~250s for complex queries).
        #   - 'unread'/'unknown' after 3s poll: stdin not seen
        #      by CC → likely lost; break and let PendingQueue
        #      re-trigger on the next turn.
        if getattr(self, '_preempt_pending', 0) > 0:
            _sent = list(getattr(self, '_sent_preempt_texts', []))
            # Source priority:
            #   1. _live_session.session_id (REUSE only) —
            #      pinned at register time, the actual
            #      session_id CC is writing under. Immune
            #      to extras/self clobbers.
            #   2. local `session_id` — read from extras
            #      at stream entry (line 1017). Persistent
            #      source for NEW spawns where the live
            #      session doesn't exist yet.
            #   3. last_data['session_id'] — from the
            #      result event we JUST received. CC's
            #      authoritative reply.
            #   4. self._current_session_id — last fallback,
            #      volatile.
            # If all four are empty, an invariant was
            # broken upstream (CC never emitted init OR
            # extras was wiped between entry and now);
            # raise so the bug surfaces instead of
            # silently mis-declaring the preempt lost.
            _sid = ((st._live_session.session_id
                     if st._is_reuse and st._live_session else "")
                    or st.session_id
                    or st.last_data.get('session_id', '')
                    or getattr(self, '_current_session_id', '')
                    or '')
            if not _sid:
                raise RuntimeError(
                    "[claude-code] preempt-check cannot "
                    "resolve session_id from any source "
                    f"(reuse={st._is_reuse}, "
                    f"live={getattr(st._live_session, 'session_id', None) if st._live_session else None!r}, "
                    f"local={st.session_id!r}, "
                    f"last_data={st.last_data.get('session_id', '')!r}, "
                    f"self={getattr(self, '_current_session_id', '')!r}) "
                    "— a previous invariant was violated "
                    "(CC didn't emit init? extras wiped? "
                    "_cleanup_proc cleared self mid-stream?). "
                    "Refusing to silently mis-declare "
                    "preempt lost.")
            _jsonl = os.path.join(
                st.workdir, 'projects',
                self._cc_project_key(st.workdir),
                f"{_sid}.jsonl")
            _pstatus = self._check_preempt_in_jsonl(_jsonl, _sent)
            # CC writes a stdin preempt to its session jsonl
            # the moment it reads from stdin, which can happen
            # ~tens of ms AFTER it emits result. If we don't
            # see the preempt yet, poll briefly for it to land
            # before deciding the preempt was lost.
            if _pstatus in ('unread', 'unknown'):
                _poll_until = time.monotonic() + 3.0
                while time.monotonic() < _poll_until:
                    time.sleep(0.2)
                    if st.proc.poll() is not None:
                        break
                    _pstatus = self._check_preempt_in_jsonl(
                        _jsonl, _sent)
                    if _pstatus not in ('unread', 'unknown'):
                        break
            if _pstatus == 'done':
                # CC integrated the preempt mid-turn; the just-
                # emitted assistant message IS the response.
                logger.info(
                    "[claude-code] result emitted; jsonl shows "
                    "all %d preempt(s) answered inline — break",
                    len(_sent))
                self._had_preempts_this_turn = True
                self._preempt_pending = 0
                self._sent_preempt_texts = []
                self._result_emitted = True
                return "break"
            if _pstatus == 'pending':
                # CC has read stdin (preempt is in jsonl) but
                # has not yet produced the response. CC WILL
                # respond — there is no useful upper bound on
                # how long that takes (could be 250s for a
                # complex query). Keep the stream open with NO
                # timeout: the for-loop blocks on stdout for
                # the next assistant event, and EOF on proc
                # death exits cleanly via the finally block.
                logger.info(
                    "[claude-code] result emitted; CC has read "
                    "%d preempt(s) (jsonl=pending) — keeping "
                    "stream open with NO timeout, waiting for "
                    "CC's response", self._preempt_pending)
                self._had_preempts_this_turn = True
                self._preempt_pending = 0
                return "continue"
            # 'unread' / 'unknown' after polling: CC has not
            # acknowledged stdin. Most likely it exited or is
            # silently stuck. Don't wait further; let pawflow
            # re-deliver via PendingQueue on the next turn.
            # _had_preempts_this_turn stays False so the
            # caller knows to re-trigger if drained user msgs
            # exist.
            logger.warning(
                "[claude-code] result emitted; %d preempt(s) "
                "NOT visible in jsonl after 3s poll "
                "(status=%s) — preempt likely lost, breaking. "
                "PendingQueue will re-trigger.",
                self._preempt_pending, _pstatus)
            self._preempt_pending = 0
            self._sent_preempt_texts = []
            self._result_emitted = True
            return "break"
        # CC emitted its final result. Mark this so future
        # preempts are refused (caller routes via PendingQueue).
        self._result_emitted = True
        return "break"

    def _ccs_finalize(self, st):
        from core.llm_client import LLMClientError, LLMResponse
        from core.conversation_store import ConversationStore

        # Don't error on non-zero exit if we got a successful result
        # (process was killed after break on result event — that's expected).
        # `_compact_result_done` counts: when the sentinel compact session
        # delivers its payload via the compact_result tool, we
        # intentionally SIGKILL CC before the final result event can
        # fire (otherwise CC stalls waiting for another input). The
        # payload IS the successful outcome; treat the 137 exit the
        # same as a clean result-event break.
        _got_result = (
            bool(st.last_data.get("session_id") or st.last_data.get("result"))
            or st._compact_result_done)
        _was_compact_stall = (st.proc.returncode == -9 and st._stall_start_time > 0 and not st._got_assistant)
        # Tool-result / no-assistant stalls are PawFlow-watchdog kills. CC
        # produced work up to that point; the kill is our own recovery
        # action, not a user-facing failure. Tag the exception so the
        # retry loop in LLMClient.complete_stream treats it as retryable
        # (same path as compact_stall) instead of surfacing an error to
        # the user on the first attempt.
        _was_tool_stall = bool(self._stall_killed) and not _was_compact_stall
        if st.proc.returncode and st.proc.returncode != 0 and not _got_result:
            if st._stderr:
                logger.error("Claude CLI stderr: %.500s", st._stderr)
            if _was_compact_stall:
                _reason = "compact_stall"
            elif _was_tool_stall:
                _reason = "tool_stall"
            else:
                _reason = ""
            raise LLMClientError(
                f"Claude CLI stream exited with code {st.proc.returncode}"
                + (f" ({_reason})" if _reason else "")
                + (f": {st._stderr[:200]}" if st._stderr else ""))

        # If turn_callback handled all turns, don't return content
        # (prevents agent loop from persisting the same text again)
        full_content = "" if st.turn_callback else "".join(st.content_parts)

        new_session = st.last_data.get("session_id", "")
        if new_session:
            # Persist session_id in conversation store (NOT on self — client is shared)
            if st.conv_id:
                try:
                    from core.conversation_store import ConversationStore
                    ConversationStore.instance().set_extra(
                        st.conv_id, f"claude_session:{st.agent_name or 'default'}",
                        new_session)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)

        # Context-fill semantics: report the LAST assistant event's per-call
        # usage (real prompt size at end of turn, ≤ context_max), NOT the
        # `result.usage` summed across sub-calls (which balloons cache_read to
        # N×prefix and makes the UI clamp at 100%). `_latest_usage` is captured
        # at every assistant event in the stream loop.
        _u_final = st._latest_usage or st.last_data.get("usage", {})
        _ti_in = _u_final.get("input_tokens", 0)
        _ti_creation = _u_final.get("cache_creation_input_tokens", 0)
        _ti_read = _u_final.get("cache_read_input_tokens", 0)
        _to = _u_final.get("output_tokens", 0)
        if not (_ti_in or _ti_creation or _ti_read or _to):
            for _mu in st.last_data.get("modelUsage", {}).values():
                _ti_in += _mu.get("inputTokens", 0) + _mu.get("input_tokens", 0)
                _ti_read += _mu.get("cacheReadInputTokens", 0) + _mu.get("cache_read_input_tokens", 0)
                _ti_creation += _mu.get("cacheCreationInputTokens", 0) + _mu.get("cache_creation_input_tokens", 0)
                _to += _mu.get("outputTokens", 0) + _mu.get("output_tokens", 0)
        _ti = _ti_in + _ti_creation + _ti_read
        return LLMResponse(
            content=full_content,
            model=st.last_data.get("model", st.model),
            tokens_in=_ti_in,
            tokens_out=_to,
            total_tokens=_ti + _to,
            cache_creation_tokens=_ti_creation,
            cache_read_tokens=_ti_read,
            finish_reason="stop",
            raw=st.last_data,
        )

