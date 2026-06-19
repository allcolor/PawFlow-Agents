"""Claude Code streaming turn orchestrator (<=800-line split)."""


import json
import logging
import os
import queue
import threading
import time
from typing import Dict, List, Optional

from core.agent_prompt_policy import append_cli_mcp_system_prompt
from core.cc_live_registry import CCLiveSession, LiveSessionRegistry
from core.interrupt_policy import SOFT_INTERRUPT_USER_COMMAND  # noqa: F401
from core.llm_providers.claude_code_session import (
    _get_sessions_base, recover_tokens_from_workdir)
from core.llm_providers._cc_base import (
    _CC_READER_EOF, _CC401Retry, _CCStreamState)  # noqa: F401

logger = logging.getLogger(__name__)


class _CCStreamMixin:
    """The _stream_claude_code orchestrator (MRO mixin)."""
    def _stream_claude_code(
        self, messages, model, temperature, max_tokens, tools, callback=None,
        turn_callback=None, block_callback=None, _is_auth_retry=False,
        *,
        call_user_id: Optional[str] = None,
        call_conversation_id: Optional[str] = None,
        call_agent_name: Optional[str] = None,
        call_event_cid: Optional[str] = None,
        call_ephemeral_stream: Optional[bool] = None,
    ):
        """Stream from claude CLI using bidirectional stream-json.

        Input: JSON lines on stdin (user messages, can preempt anytime)
        Output: JSON lines on stdout (events: assistant, user, result, etc.)

        turn_callback(text, tool_calls): called at each turn boundary so
        the agent loop can persist intermediate messages. Each Claude Code
        assistant turn = one message in the conversation.

        Claude Code uses MCP for tool calls — tools param is ignored.

        Per-call identity (user_id / conversation_id / agent_name /
        event_cid / ephemeral_stream) MUST be passed via the call_*
        kwargs by every caller. The shared client instance no longer
        carries these as state — the previous self._user_id /
        self._conversation_id / etc. pattern was a footgun: concurrent
        compact / memory-extract / sub-agent streams would clobber
        each other's identity via try/finally save-restore on the
        same instance, leaving the values empty for whichever stream
        won the race. Each call now passes its own scope explicitly.
        """
        st = _CCStreamState()
        st.model = model
        st.callback = callback
        st.turn_callback = turn_callback
        st.block_callback = block_callback
        from core.llm_client import LLMClientError

        # Resolve per-call identity. Fall back to self.* only as a
        # transitional safety net so a caller that hasn't yet been
        # updated to pass kwargs doesn't crash; the goal is for every
        # call site to pass these explicitly, at which point the
        # fallback can be tightened to raise.
        st.user_id = (call_user_id if call_user_id is not None
                    else getattr(self, '_user_id', ""))
        st.conv_id = (call_conversation_id if call_conversation_id is not None
                    else getattr(self, '_conversation_id', ""))
        st.agent_name = (call_agent_name if call_agent_name is not None
                       else getattr(self, '_agent_name', ""))
        st._is_ephemeral = (bool(call_ephemeral_stream)
                          if call_ephemeral_stream is not None
                          else bool(getattr(self, '_ephemeral_stream', False)))
        st._raw_event_cid = (call_event_cid if call_event_cid is not None
                           else getattr(self, '_event_cid', ''))

        # Extract images BEFORE serialization (they'll be sent as content blocks).
        # user_id + conv_id are REQUIRED — FileStore enforces owner×conv
        # access control, and a missing identifier silently drops the
        # user's image. _extract_images raises if either is empty.
        image_blocks = self._extract_images(
            messages, user_id=st.user_id, conversation_id=st.conv_id)

        # Always load session_id from the store for THIS conversation
        # (never from self — the client is shared across conversations)
        st.session_id = ""
        if st.conv_id:
            try:
                from core.conversation_store import ConversationStore
                st.session_id = ConversationStore.instance().get_extra(
                    st.conv_id, f"claude_session:{st.agent_name or 'default'}") or ""
                if st.session_id:
                    logger.info("[%s/%s/%s] Restored claude session: %s",
                                st.user_id[:6] or '?', st.conv_id[:8] or '?',
                                st.agent_name or 'default', st.session_id)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

        st.workdir = self._get_session_workdir(st.conv_id, st.agent_name, st.user_id)
        _rel = os.path.relpath(st.workdir, _get_sessions_base()).replace("\\", "/")
        _session_dir = f"/cc_sessions/{_rel}"
        st._provider_workdir = self._cc_namespace_workdir(st.workdir)

        # Session-aware serialization:
        # - New session (no session_id): feed the full PawFlow ctx ONCE.
        # - Resume (session_id set): CC already has the history; send only
        #   the new user message. The catch-up mechanism below injects
        #   anything that arrived from other agents since last turn.
        if st.session_id:
            system_prompt = ""
            for _m in messages:
                if _m.role == "system":
                    _c = _m.content
                    system_prompt = _m.text_content if isinstance(_c, list) else (_c or "")
                    break
            system_prompt = append_cli_mcp_system_prompt(system_prompt)
            last_user = ""
            for _m in reversed(messages):
                if _m.role == "user":
                    _c = _m.content
                    if isinstance(_c, list):
                        last_user = _m.text_content
                    else:
                        last_user = _c or ""
                    break
            user_text = last_user
            initial_text = self._build_stdin_with_system(system_prompt, user_text)
        else:
            system_prompt, user_text = self._serialize_messages_for_cli(messages, None)
            system_prompt = append_cli_mcp_system_prompt(system_prompt)
            initial_text = self._build_cli_initial_context_prompt(
                messages,
                system_prompt=system_prompt,
                user_text=user_text,
                workdir=st.workdir,
                provider_workdir=st._provider_workdir,
            )
        logger.debug("[claude-code] prompt: system=%d user=%d images=%d msgs=%d session=%s",
                     len(system_prompt), len(user_text), len(image_blocks), len(messages),
                     "resume" if st.session_id else "new")

        logger.info("claude-code stream: conv_id='%s' user='%s' agent='%s' session='%s'",
                     st.conv_id, st.user_id, st.agent_name, st.session_id[:12] if st.session_id else "new")
        # Resume with same credential that created the session (approach 3)
        st._resume_pool_idx = -1
        if st.session_id and st.conv_id:
            try:
                st._resume_pool_idx = int(ConversationStore.instance().get_extra(
                    st.conv_id, f"claude_pool_idx:{st.agent_name or 'default'}") or -1)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        self._setup_credentials(
            st.workdir, pool_index=st._resume_pool_idx,
            user_id=st.user_id, conversation_id=st.conv_id)
        # Store pool index for this session
        if st.conv_id and hasattr(self, '_current_pool_index'):
            try:
                ConversationStore.instance().set_extra(
                    st.conv_id, f"claude_pool_idx:{st.agent_name or 'default'}",
                    self._current_pool_index)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
        st._auth_retried = _is_auth_retry

        # Live-session reuse: look up a warm CC process pinned by
        # (user, conv, agent, service, pool_idx). A hit skips the spawn
        # (mcp setup, container exec, CC startup, --resume load) and
        # pushes the new user message onto the existing stdin. A miss
        # (or a dead proc — auto-evicted by get()) falls through to
        # _spawn_cc_stream. Ephemeral streams (compact / memory_extract
        # / btw) never reuse: they're short-lived by design and must
        # not inherit nor leak another stream's proc.
        st._svc_id = getattr(self, '_agent_service', '') or 'default'
        # Intentionally NOT `getattr(...) or -1`: `or` coerces 0 to -1,
        # silently mapping OAuth pool slot 0 onto the api-key sentinel.
        # The getattr default handles the "attr never set" case (api-key
        # mode: _setup_credentials early-returns before assigning the
        # attr).
        st._svc_pool_idx = int(getattr(self, '_current_pool_index', -1))
        # _is_ephemeral resolved earlier at function entry from
        # call_ephemeral_stream (with self._ephemeral_stream fallback).
        st._live_reg = LiveSessionRegistry.instance()
        st._live_key = None
        st._live_session: Optional[CCLiveSession] = None
        if st.conv_id and not st._is_ephemeral:
            st._live_key = (st.user_id, st.conv_id, st.agent_name or 'default',
                         st._svc_id, st._svc_pool_idx)
            st._live_session = st._live_reg.get(st._live_key)
            if st._live_session is not None:
                # Bump reuse_count for the new stream call. The idle
                # invariant is handled by the stdout reader daemon
                # (per-line touch) so we don't need to bump last_used
                # for that here — but touch() does it anyway, which
                # closes the tiny window between get() and the first
                # incoming line as a defence-in-depth.
                st._live_reg.touch(st._live_key)
        st._is_reuse = st._live_session is not None

        if st._is_reuse:
            # Serialise concurrent _stream_claude_code calls targeting the
            # same live session. Without this, bg_bucket_builder's
            # auto_extract_memories (or any other background caller that
            # reuses the same client) can enter _stream while the main
            # stream is still mid-turn, clobber proc.stdin with a rogue
            # message, and end the main turn with an empty stop. The lock
            # is RLock so one thread can re-enter (nested flush/retries
            # during teardown) without deadlocking itself.
            _turn_lock_acquired = st._live_session.turn_lock.acquire()
            st._owns_turn_lock = _turn_lock_acquired
            try:
                st.proc = st._live_session.proc
                self._pool_container_name = st._live_session.pool_container
                st._mcp_internal_token = st._live_session.mcp_internal_token
                # Non-ephemeral by construction (see guard above); mirror the
                # spawn path's self._claude_proc assignment so preempt /
                # cancel_claude_code targets the reused process.
                self._claude_proc = st.proc
                # The live session pins CC's actual session_id; that's
                # the source of truth for the jsonl filename CC writes
                # to. Local `session_id` (read from extras at line
                # 1017) and `self._current_session_id` (volatile) MAY
                # diverge from it under concurrent code paths that
                # touch extras or self — _live_session.session_id was
                # captured at register time and is immune to those.
                if not st._live_session.session_id:
                    # Invariant from the register site: keep-alive
                    # never registers without a session_id. If we ever
                    # observe an empty one here, registration violated
                    # the contract — fail loudly.
                    raise RuntimeError(
                        f"[cc-live] REUSE entry for "
                        f"{st.user_id[:6]}/{st.conv_id[:8]}/{st.agent_name} has "
                        f"empty session_id on the live session — the "
                        f"register site should have refused to create "
                        f"this. Pawflow data corruption?")
                # Override the local session_id from extras if it
                # disagrees — the live session's value wins (extras
                # could have been cleared by a sibling code path).
                if st.session_id and st.session_id != st._live_session.session_id:
                    logger.warning(
                        "[cc-live] REUSE: extras session_id=%s "
                        "DIVERGES from live session_id=%s — using "
                        "live (CC's reality)",
                        st.session_id[:12], st._live_session.session_id[:12])
                st.session_id = st._live_session.session_id
                # Sync self too so any code path that still reads from
                # the singleton sees the right value. Defence-in-depth.
                self._current_session_id = st.session_id
                try:
                    self._cc_container_pid = int(getattr(
                        st.proc, '_pf_pid', 0) or 0)
                except (TypeError, ValueError):
                    pass
                logger.info(
                    "[cc-live] REUSE %s/%s/%s@%s#%d (reuse_count=%d, "
                    "lived=%.1fs, session=%s)",
                    st.user_id[:6], st.conv_id[:8], st.agent_name or 'default',
                    st._svc_id, st._svc_pool_idx, st._live_session.reuse_count,
                    time.monotonic() - st._live_session.spawn_at,
                    st.session_id[:12])
            except BaseException:
                if st._owns_turn_lock:
                    try:
                        st._live_session.turn_lock.release()
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                raise
        else:
            st._owns_turn_lock = False
            st.proc, self._pool_container_name, st._mcp_internal_token = (
                self._spawn_cc_stream(st.workdir, st.user_id, st.conv_id, st.agent_name,
                                      st.session_id, st.model,
                                      ephemeral_stream=st._is_ephemeral))

        # Multi-agent catch-up: when resuming a session, inject messages
        # from other agents that CC hasn't seen (arrived after CC's last turn)
        catchup_text = ""
        if st.session_id and st.conv_id and st.agent_name:
            catchup_text = self._build_catchup_context(st.conv_id, st.agent_name)

        # Send initial message as stream-json (keep stdin open for preempt/interrupt)
        try:
            # Prepend catch-up context to the initial message
            if catchup_text:
                initial_text = catchup_text + "\n\n" + initial_text

            if image_blocks:
                # Multipart: text + images as content array (enables vision)
                content = [{"type": "text", "text": initial_text}] + image_blocks
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": content},
                })
            else:
                msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": initial_text},
                })
            st.proc.stdin.write(msg + "\n")
            st.proc.stdin.flush()
        except BrokenPipeError:
            stderr = ""
            try:
                stderr = "".join(
                    getattr(self, "_stderr_buffer", []) or []
                ).strip()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            st.proc.wait()
            raise LLMClientError(
                f"Claude CLI pipe broken (exit {st.proc.returncode}): {stderr[:500]}")

        # SSE publisher for webchat visibility.
        # _event_cid sentinel values:
        #   None               → publishing explicitly suppressed (sub-agent path)
        #   "" or missing attr → fall back to conv_id (main agent default)
        #   any string         → publish to that conv
        # _subagent_event_cb: if set, called INSTEAD of the bus — used by
        #   SubAgentExecutor to re-emit CC's tool_call/tool_result as
        #   sub_agent_tool/sub_agent_tool_result so they land in the
        #   delegate sub-block instead of the main chat.
        # _raw_event_cid resolved earlier at function entry from
        # call_event_cid (with self._event_cid fallback).
        if st._raw_event_cid is None:
            st._event_cid = ""
        else:
            st._event_cid = st._raw_event_cid or st.conv_id
        st._subagent_event_cb = getattr(self, '_subagent_event_cb', None)
        # Extract task_id from sub-conv ID so frontend can group task events
        st._task_id = ''
        if '::task::' in st.conv_id:
            st._task_id = st.conv_id.split('::task::')[-1].split('::')[0]
        st._agent_ctx = getattr(self, '_agent_ctx', {}) or {}


        # Read streaming output — accumulate per turn
        st.content_parts: List[str] = []  # final result text
        st.last_data: dict = {}
        st._turn_count = 0

        # Per-turn accumulator
        st._turn_text_parts: List[str] = []
        st._turn_tool_calls: list = []
        st._turn_thinking: str = ""
        # Redacted thinking tracking. CC/Anthropic return extended-thinking
        # blocks with thinking="" + signature="..." — the content is
        # encrypted at the API level. We can't show the reasoning but we
        # CAN surface "Thought for Xs" so the user sees the agent did
        # reason, and the chat bubble stays visually aligned with the
        # pre-redaction UX.
        st._turn_thinking_redacted: bool = False
        st._turn_thinking_start: float = 0.0
        st._turn_thinking_end: float = 0.0
        st._tool_results: dict = {}  # tool_use_id → result text
        # Persistent tool_call_id → unwrapped tool name map. _turn_tool_calls
        # is cleared on every _flush_turn, so by the time a tool_result for
        # tool T arrives (potentially several turns after the tool_use that
        # issued it), the per-turn list can't resolve the name and we'd
        # fall back to the raw tc_id. Keep a stream-scoped map so the
        # tool_result handler can always recover the name — critical for
        # the compact_result short-circuit kill.
        st._stream_tc_names: Dict[str, str] = {}
        st._current_msg_id: str = ""  # track message ID to detect incremental updates
        # Latest usage observed on an assistant event — used to publish
        # a fresh context-fill % to the webchat. The `result` event's
        # usage may sum differently; the last assistant.message.usage
        # reflects the actual prompt size of the final turn.
        st._latest_usage: dict = {}
        self._preempt_pending = 0  # reset at start of each stream
        self._had_preempts_this_turn = False
        self._result_emitted = False  # set True when CC emits final result
        self._compacting = False  # set True when CC compact_boundary fires
        # CC's authoritative context window for the model in use is lifted
        # from result.modelUsage[model].contextWindow on each result event
        # and cached PER-STREAM in self._cc_context_window_by_stream
        # (keyed by (conv_id, agent_name)). The old singleton scalar got
        # clobbered across concurrent streams (memory_extract / _compact /
        # multi-agent) on the shared provider — an opus turn would read
        # back haiku's 200k after an unrelated stream had written it.
        if not hasattr(self, '_cc_context_window_by_stream'):
            self._cc_context_window_by_stream = {}
        # Track text of every preempt sent via stdin during this stream so
        # we can locate it in CC's session jsonl by content match. Used by
        # _check_preempt_in_jsonl to determine whether CC has already
        # responded to the preempt (last assistant after preempt) or not.
        self._sent_preempt_texts: list = []

        # Compact-drain state: when CC emits compact_boundary we don't
        # kill+raise immediately anymore. We close CC's stdin (EOF signal),
        # let the parse loop drain remaining events (per-msg_id flushes
        # persist each turn through turn_callback), then raise
        # CCCompactDetected once CC has finished streaming. Killing too
        # early lost already-emitted tool_use/tool_result blocks that were
        # still in the pipe buffer — resulting in gaps in shared.jsonl
        # between the last persisted turn and the compact trigger.
        st._compact_pending = [False]
        st._compact_drain_timer = [None]



        # Stall watchdog: if CC emits a system event (init or compact_boundary)
        # but produces no assistant response within _STALL_TIMEOUT seconds,
        # kill the process. The retry loop in stream_chat will relaunch
        # a fresh CC process with the same session.
        # Read from the LLM service config (timeout property) so users can
        # tune it without touching code. 0/None means no stall timeout;
        # the user can still stop explicitly.
        st._STALL_TIMEOUT = int(getattr(self, "timeout", None) or 0)
        st._stall_start_time = 0.0  # time.monotonic() when stall watch begins
        st._got_assistant = False   # set True on first assistant event
        st._last_tool_result_time = 0.0  # monotonic time of last tool_result with no pending tools
        st._pending_tool_ids = set()     # tool_use ids awaiting results
        st._emitted_sse_tcs = set()      # tool_use ids for which we sent a SSE tool_call
        st._compact_result_done = False  # flip when compact_result tool delivers

        # Phantom tool call detector: if CC emits too many empty/phantom
        # tool calls in a short window, it likely lost context after a bad
        # internal compact. Trigger a PawFlow compact to recover.
        st._PHANTOM_WINDOW = 300   # 5 minutes
        st._PHANTOM_THRESHOLD = 10
        st._phantom_timestamps: list = []  # monotonic timestamps of phantom detections

        st._watchdog_stop = threading.Event()


        self._stall_killed = False  # set by watchdog — retry must be unconditional

        # Heartbeat state for observability — updated by the main event
        # loop so the watchdog (and anyone reading logs) can see WHERE
        # we are when nothing moves for long stretches.
        #
        # On reuse we share the session's hb_state dict by reference so
        # the original reader daemon (captured at spawn time) keeps
        # writing into the SAME object this turn's watchdog reads. We
        # reset the per-turn counters in place rather than allocating a
        # fresh dict, which would orphan the reader's closure.
        if st._is_reuse and st._live_session.hb_state is not None:
            st._hb_state = st._live_session.hb_state
            st._hb_state.update({
                "last_event_ts": 0.0,
                "last_event_kind": "",
                "last_dispatched_tc": "",
                "last_tool_result_id": "",
                "stream_line_count": 0,
                "last_turn_flush_ts": 0.0,
                "stdin_closed": False,
            })
        else:
            st._hb_state = {
                "last_event_ts": 0.0,       # time.monotonic() of last stdout line read
                "last_event_kind": "",      # 'assistant', 'user', 'system', 'result', ...
                "last_dispatched_tc": "",   # last tool_use dispatched (id + name)
                "last_tool_result_id": "",  # last tool_result received
                "stream_line_count": 0,     # total lines read from CC stdout
                "last_turn_flush_ts": 0.0,  # monotonic of last _flush_turn
                "stdin_closed": False,      # True once we sent EOF on stdin
            }
        # Sentinel-session EOF nudge: after _SENTINEL_EOF_INTERVAL
        # seconds of silence on a _compact/_memory_extract session,
        # close proc.stdin to signal EOF to CC. CC interprets this as
        # "no more user input" and finalises its current turn (LLM
        # reply included), which in practice flushes the buffered
        # JSON events to stdout so our reader sees them. Does NOT
        # kill CC (the process keeps running until it decides to
        # exit on its own). The 300s stall watchdog remains as a
        # hard fallback if EOF doesn't suffice.
        st._is_sentinel_conv = bool(st.conv_id) and st.conv_id.startswith("_")
        st._SENTINEL_EOF_INTERVAL = 10.0


        st._watchdog_dbg_count = 0
        st._watchdog_thread = threading.Thread(
            target=self._ccs_stall_watchdog, args=(st,), daemon=True)
        st._watchdog_thread.start()

        # Reader daemon: pure stdout → event queue pump. Decouples IO
        # from dispatch so the dispatch loop can block on a single
        # queue.get() and react promptly to proc death / sentinel EOF
        # without polling stdout directly.
        #
        # On reuse: the original reader is still draining the same
        # proc.stdout. We adopt its queue + thread + stop_event; any
        # stale events sitting in the queue from between-turn idle are
        # unexpected (CC stays quiet after `result`) but the dispatch
        # loop below still short-circuits on `result` so they'd be
        # harmless at worst.
        if st._is_reuse:
            st._event_q = st._live_session.event_q
            st._reader_thread = st._live_session.reader_thread
            st._reader_stop = st._live_session.stop_event
        else:
            st._event_q = queue.Queue()
            st._reader_stop = threading.Event()


            st._reader_thread = threading.Thread(
                target=self._ccs_reader_daemon, args=(st,), daemon=True, name="cc-reader")
            st._reader_thread.start()

        # Live-session reuse decision: set to True ONLY after a clean
        # result-event break AND no compact/stall/auth failure. Any
        # other exit path (EOF, exception, compact, stall) leaves this
        # False so the `finally` block tears down the proc as usual.
        st._keep_alive = False
        # Defensive init: post-finally code reads _stderr inside an
        # `if proc.returncode ...` branch that stays skipped on the
        # keep-alive path (proc still running → returncode=None). Setting
        # to "" here keeps the name bound even if finally takes the
        # keep-alive branch that skips _cleanup_proc.
        st._stderr = ""

        try:
            self._ccs_dispatch_loop(st)

            # Loop exited naturally (result break or stdout EOF). If a
            # compact_boundary fired during this stream, raise now — all
            # pre-compact events have been drained through turn_callback
            # via the per-msg_id rollover in the main loop.
            if st._compact_pending[0]:
                from core.llm_client import CCCompactDetected
                raise CCCompactDetected("CC auto-compact detected")

            # Clean result-event exit: no compact, no watchdog stall kill.
            # Promote to keep-alive so `finally` retains proc + reader +
            # pool container for the next turn's reuse. proc.poll() must
            # still be None — a racy EOF break between here and finally
            # would leave us registering a dead session. Ephemeral streams
            # (_live_key is None) never keep alive.
            _stall_killed_flag = bool(getattr(self, '_stall_killed', False))
            st._keep_alive = (
                st._live_key is not None
                and bool(getattr(self, '_result_emitted', False))
                and not _stall_killed_flag
                and st.proc.poll() is None
            )

        except _CC401Retry:
            # 401 mid-stream: credentials already refreshed, retry once.
            # Evict BEFORE recursing so the retry doesn't re-adopt the
            # about-to-be-killed proc from the registry.
            if st._live_key is not None:
                st._live_reg.evict(st._live_key, "auth_401")
            logger.info("[claude-code] retrying after 401 token refresh")
            return self._stream_claude_code(
                messages, st.model, temperature, max_tokens, tools, st.callback,
                turn_callback=st.turn_callback, block_callback=st.block_callback,
                _is_auth_retry=True)
        except BaseException as _dispatch_exc:
            # ANY other exception in the dispatch loop (CCCompactDetected,
            # KeyboardInterrupt, AgentCancelled, programming bugs, etc.):
            # 1. Evict the live-session entry IMMEDIATELY so a concurrent
            #    reuse lookup cannot adopt this about-to-die proc. The
            #    finally block evicts too, but that runs AFTER `raise`
            #    propagates through intermediate frames — a concurrent
            #    turn that calls `_live_reg.get(key)` in that window
            #    would get a session pointing at a dying subprocess.
            # 2. Force `_keep_alive = False` so the finally path takes
            #    the teardown branch unconditionally (the normal keep-
            #    alive computation happens AFTER the while loop; if an
            #    exception escapes the loop, that computation never
            #    ran — but defense-in-depth in case someone later adds
            #    a keep-alive assignment earlier in the flow).
            # 3. Kill hard NOW, not just in finally. `_cleanup_proc` in
            #    finally does `proc.kill()` + pool release (which is
            #    `docker rm -f` in the 1:1 model, so the container IS
            #    nuked). Calling `_kill_cc_hard` here is belt-and-
            #    suspenders: it adds a container-side pgid kill that
            #    reaps any Node workers CC forked BEFORE the docker rm
            #    tears down the namespace. Redundant but cheap; keeps
            #    the exception teardown path symmetric with the explicit
            #    kill paths (compact_boundary, compact_result, phantom).
            # BaseException (not just Exception) catches AgentCancelled /
            # SystemExit / KeyboardInterrupt too — those also leave a
            # live proc behind if we don't tear down here.
            if st._live_key is not None:
                try:
                    st._live_reg.evict(st._live_key, "dispatch_exception")
                except Exception:
                    logger.debug("early-evict failed", exc_info=True)
            st._keep_alive = False
            try:
                self._kill_cc_hard(st.proc)
            except Exception:
                logger.debug("kill_cc_hard in except failed", exc_info=True)
            logger.info(
                "[claude-code] dispatch loop aborted by %s: %.200s",
                type(_dispatch_exc).__name__, str(_dispatch_exc))
            raise
        finally:
            # Stop compact stall watchdog
            st._watchdog_stop.set()
            # Cancel compact-drain timeout timer if still pending
            # (loop exited cleanly before deadline, or via an exception
            # that wasn't the compact path at all).
            try:
                _t = st._compact_drain_timer[0]
                if _t is not None:
                    _t.cancel()
            except Exception:
                logger.debug("exception suppressed", exc_info=True)
            # Flush any pending turn (ensures last text is persisted even if interrupted)
            try:
                self._ccs_flush_turn(st)
            except Exception:
                logger.debug("exception suppressed", exc_info=True)

            if st._keep_alive:
                # Retain proc + reader + pool container for reuse. Skip
                # _cleanup_proc / pool release / token revoke — those are
                # lifecycle-scoped to the live session, not the turn.
                # The reader daemon's per-line touch already keeps
                # last_used fresh — no end-of-stream touch needed.
                try:
                    if st._is_reuse:
                        pass  # nothing to do; reader keeps last_used fresh
                    else:
                        # CC's session_id captured during this stream's
                        # init event (line 1908) AND/OR returned in the
                        # final result event. Either source is the
                        # authoritative jsonl filename CC is writing
                        # to — pin it on the live session so post-
                        # result preempt checks (and any future
                        # introspection) can locate the file without
                        # going through volatile state (extras, self).
                        _live_session_id = (
                            getattr(self, '_current_session_id', '')
                            or st.last_data.get('session_id', '')
                            or '')
                        if not _live_session_id:
                            # Hard invariant: a live session that gets
                            # registered must know its CC session_id —
                            # that's the whole point of keep-alive.
                            # Without it, future REUSEs cannot inspect
                            # CC's jsonl, preempt-loss check goes blind,
                            # and the bug we're fixing returns.
                            raise RuntimeError(
                                "[cc-live] keep-alive register called "
                                "without a session_id (init event not "
                                "seen and result event lacks session_id) "
                                "— refusing to register a blind live "
                                "session. Falling through to teardown.")
                        _session = CCLiveSession(
                            proc=st.proc,
                            event_q=st._event_q,
                            reader_thread=st._reader_thread,
                            stop_event=st._reader_stop,
                            pool_container=self._pool_container_name,
                            workdir=st.workdir,
                            service_id=st._svc_id,
                            svc_pool_idx=st._svc_pool_idx,
                            user_id=st.user_id,
                            conv_id=st.conv_id,
                            session_id=_live_session_id,
                            mcp_internal_token=st._mcp_internal_token,
                            hb_state=st._hb_state,
                        )
                        st._live_reg.register(st._live_key, _session)
                        # Start the idle sweeper on first register — no
                        # work until there's a session to sweep.
                        st._live_reg.ensure_sweeper(
                            killer=self._kill_cc_hard,
                            recover=recover_tokens_from_workdir)
                except Exception:
                    logger.warning(
                        "[cc-live] register/touch failed; falling back "
                        "to full teardown", exc_info=True)
                    st._keep_alive = False  # fall through to teardown below
                else:
                    # Still recover refreshed OAuth tokens from workdir
                    # — CC may have refreshed mid-turn and we want them
                    # persisted for resume-without-live-session paths.
                    try:
                        self._recover_tokens(
                            st.workdir, user_id=st.user_id,
                            conversation_id=st.conv_id)
                    except Exception:
                        logger.debug(
                            "_recover_tokens failed", exc_info=True)

            if not st._keep_alive:
                # Full teardown: evict any live-session entry first so
                # the next turn doesn't re-adopt the dead proc, then
                # kill + recover + revoke as before.
                if st._is_reuse and st._live_key is not None:
                    st._live_reg.evict(st._live_key, "turn_failed")
                # Cleanup process — _cleanup_proc captures stderr internally
                st._stderr = self._cleanup_proc(st.proc)
                # Recover refreshed tokens from workdir (Claude Code may have refreshed them)
                self._recover_tokens(
                    st.workdir, user_id=st.user_id, conversation_id=st.conv_id)
                # Revoke the internal-auth token minted for this CC invocation —
                # scoped to the lifetime of this stream, not retained across calls.
                # Without this, tokens accumulate in core.internal_auth._tokens
                # until server restart (memory-only, but a lingering valid token
                # leaked from .mcp.json or process env stays replayable).
                if st._mcp_internal_token:
                    try:
                        from core.internal_auth import revoke_token
                        revoke_token(st._mcp_internal_token)
                    except Exception:
                        logger.debug("internal-auth revoke failed", exc_info=True)

            # Release the live-session turn lock acquired at REUSE entry
            # (line 1111). Held across the entire stream call so concurrent
            # callers — bg_bucket_builder threads, next user turn's
            # bg_streaming thread, etc. — don't push rogue input onto the
            # in-flight session's stdin. WITHOUT this release, the first
            # successful REUSE held the lock forever (acquire had no
            # corresponding release on success path) and every subsequent
            # turn that hit REUSE blocked indefinitely on
            # turn_lock.acquire(). Symptom in the wild: user sends a
            # second message, agent stream starts, log stops on
            # `claude-code stream: conv_id=...`, no spawn / no REUSE log,
            # 4+ minutes of silent freeze before the user gives up.
            # RLock release is balanced with the single acquire at 1111;
            # do it inside the finally so we cover normal return AND any
            # exception that propagates through the streaming body.
            if st._owns_turn_lock and st._live_session is not None:
                try:
                    st._live_session.turn_lock.release()
                except Exception:
                    logger.debug(
                        "turn_lock release failed (likely already released "
                        "via the early-error path at line 1129)",
                        exc_info=True)
                st._owns_turn_lock = False
        return self._ccs_finalize(st)
