"""AgentLoopTask mixin — AgentPoller methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import logging
import os
import threading
import time
from typing import Dict, List


from tasks.ai._agent_poll_checkin import _AgentPollCheckinMixin

logger = logging.getLogger(__name__)

_WATCHDOG_INTERVAL_SECONDS = int(
    os.getenv("PAWFLOW_AGENT_WATCHDOG_INTERVAL_SECONDS", "300") or "300")


def _check_task_limits(task: dict, task_id: str) -> str:
    """Check pre-launch limits. Returns cancel reason or empty string."""
    import time as _t
    # max_reschedules
    _max_rs = task.get("max_reschedules", 0)
    if _max_rs and task.get("reschedule_count", 0) >= _max_rs:
        return f"max_reschedules reached ({_max_rs})"
    # max_total_time
    _max_tt = task.get("max_total_time", 0)
    if _max_tt and task.get("created_at"):
        _elapsed = _t.time() - task["created_at"]
        if _elapsed >= _max_tt:
            return f"max_total_time exceeded ({int(_elapsed)}s >= {_max_tt}s)"
    # max_budget
    _max_b = task.get("max_budget", 0)
    if _max_b and task.get("total_cost", 0) >= _max_b:
        return f"max_budget exceeded (${task['total_cost']} >= ${_max_b})"
    return ""


class AgentPollerMixin(_AgentPollCheckinMixin):
    """Methods extracted from AgentLoopTask."""


    def _poll_conversations(self, interval: int) -> None:
        """Background poller: periodically check active conversations for pending work.

        For each eligible conversation (has an SSE subscriber, not currently being
        processed, last message was from assistant with tool usage), re-run the
        agent loop with a check-in prompt.
        """

        logger.info(f"Agent poller running (interval={interval}s)")

        # On startup: reschedule any active tasks that have no pending schedule
        try:
            self._reschedule_active_tasks()
        except Exception as e:
            logger.warning(f"Failed to reschedule active tasks on startup: {e}")

        while not self._poller_stop.is_set():
            # Wait for interval OR immediate wake signal
            self._poller_wake.wait(timeout=interval)
            self._poller_wake.clear()
            if self._poller_stop.is_set():
                break
            try:
                _t0 = time.time()
                self._poll_once()
                _dt = time.time() - _t0
                if _dt > 0.05:
                    logger.warning(f"[poller] _poll_once took {_dt*1000:.0f}ms")
            except Exception as e:
                logger.error(f"Agent poller error: {e}", exc_info=True)


    def _maybe_cleanup_checkpoints_async(self) -> None:
        now = time.time()
        last_cleanup = getattr(self, '_last_checkpoint_cleanup', 0)
        if now - last_cleanup <= 86400:  # 24h
            return
        if getattr(self, '_checkpoint_cleanup_running', False):
            return
        self._last_checkpoint_cleanup = now
        self._checkpoint_cleanup_running = True

        def _worker() -> None:
            try:
                from core.checkpoint import CheckpointManager
                started = time.monotonic()
                cleaned = CheckpointManager.cleanup_old(30)
                elapsed_ms = (time.monotonic() - started) * 1000.0
                if cleaned:
                    logger.info(
                        "[checkpoint] cleaned %d old checkpoint(s) in %.1fms",
                        cleaned, elapsed_ms)
                elif elapsed_ms >= 50.0:
                    logger.info(
                        "[checkpoint] cleanup checked old checkpoints in %.1fms",
                        elapsed_ms)
            except Exception as exc:
                logger.debug(f"[checkpoint] cleanup failed: {exc}")
            finally:
                self._checkpoint_cleanup_running = False

        threading.Thread(
            target=_worker,
            daemon=True,
            name="agent-poller-checkpoint-cleanup",
        ).start()

    def _poll_once(self) -> None:
        """Single poll iteration: check scheduled rechecks and active conversations."""
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        bus = ConversationEventBus.instance()
        store = ConversationStore.instance()
        scheduler = PollScheduler.instance()
        _pt0 = time.time()

        # Checkpoint cleanup scans FileStore metadata and can be slow on a warm
        # workspace. Schedule it from the poller, but never run it inline with
        # user wake-up decisions.
        try:
            self._maybe_cleanup_checkpoints_async()
        except Exception as _cp_err:
            logger.debug(f"[checkpoint] cleanup schedule failed: {_cp_err}")

        _dt_ckpt = time.time() - _pt0
        if _dt_ckpt > 0.05:
            logger.warning(f"[poller-timing] checkpoint: {_dt_ckpt*1000:.0f}ms")
        _pt1 = time.time()
        _pt1 = time.time()
        # Watchdog scans every conversation, so it must not run on every
        # poll tick while the server is idle. Startup reschedule already
        # covers restart recovery; this periodic pass repairs rare races.
        _now_watchdog = time.time()
        _last_task_watchdog = getattr(self, "_last_task_watchdog", 0)
        if _now_watchdog - _last_task_watchdog >= _WATCHDOG_INTERVAL_SECONDS:
            self._last_task_watchdog = _now_watchdog
            try:
                self._ensure_tasks_scheduled()
            except Exception as _wt_err:
                logger.warning(f"Task watchdog failed: {_wt_err}")

        _dt_tasks = time.time() - _pt1
        if _dt_tasks > 0.05:
            logger.warning(f"[poller-timing] ensure_tasks: {_dt_tasks*1000:.0f}ms")
        _pt2 = time.time()
        _last_thought_watchdog = getattr(self, "_last_thought_watchdog", 0)
        if _now_watchdog - _last_thought_watchdog >= _WATCHDOG_INTERVAL_SECONDS:
            self._last_thought_watchdog = _now_watchdog
            try:
                self._ensure_thoughts_scheduled()
            except Exception as _wt_err:
                logger.warning(f"Thought watchdog failed: {_wt_err}")

        _dt_thoughts = time.time() - _pt2
        if _dt_thoughts > 0.05:
            logger.warning(f"[poller-timing] ensure_thoughts: {_dt_thoughts*1000:.0f}ms")
        # Collect conversations to poll from two sources:
        # 1. Scheduled rechecks that are due (persistent, works without SSE)
        # 2. Active SSE conversations with cooldown expired (legacy behavior)
        to_poll: set[str] = set()
        # Scheduled rechecks bypass eligibility checks (they were explicitly requested)
        scheduled_ids: set[str] = set()

        # Source 1: PollScheduler — persistent scheduled rechecks
        # Map cid -> list of reasons for scheduled wakeups (non-thought)
        scheduled_reasons: Dict[str, List[str]] = {}
        # Thought entries are processed individually (each agent gets its own loop)
        thought_entries: List[Dict] = []
        due_entries = scheduler.get_due()
        for entry in due_entries:
            cid = entry["conversation_id"]
            entry_key = entry.get("key", cid)
            reason = entry.get("reason", "scheduled recheck")

            if "::thought::" in entry_key:
                # Thoughts are never blocked — they can arrive anytime
                thought_entries.append(entry)
                continue

            if "::task::" in entry_key or "::task_verify::" in entry_key:
                thought_entries.append(entry)
                continue

            if "::recheck::" in entry_key:
                thought_entries.append(entry)
                continue

            if "::plan::" in entry_key or "::plan_verify::" in entry_key:
                thought_entries.append(entry)
                continue

            # Generic scheduled recheck (user-requested via /schedule)
            logger.info(f"[poller] Scheduled recheck due for {cid[:8]}: {reason}")
            to_poll.add(cid)
            scheduled_ids.add(cid)
            scheduled_reasons.setdefault(cid, []).append(reason)

        # Source 2 removed: all autonomous wake-ups go through PollScheduler
        # with agent-qualified keys (::thought::, ::task::, ::recheck::).
        # No more SSE cooldown guessing.

        if not to_poll and not thought_entries:
            return

        # Process non-thought polls (grouped by conversation, one at a time)
        for conversation_id in to_poll:
            # Skip if already being processed — but reschedule so we don't lose it.
            # Pending/preempt rescue wakes can become due while the current turn is
            # still cleaning up. Dropping generic reasons here loses the user
            # message until another event happens.
            with self._active_lock:
                if conversation_id in self._active_conversations:
                    reasons = scheduled_reasons.get(conversation_id, []) or ["[pending] active retry"]
                    for r in reasons:
                        import re as _re_resched
                        _tid_m = _re_resched.search(r'\[agent_task:(t_\w+)\]', r)
                        if _tid_m:
                            key = f"{conversation_id}::task::{_tid_m.group(1)}"
                        else:
                            import hashlib as _hashlib_resched
                            digest = _hashlib_resched.sha1(
                                r.encode('utf-8', 'ignore'),
                                usedforsecurity=False,
                            ).hexdigest()[:8]
                            key = f"{conversation_id}::pending::{digest}"
                        scheduler.schedule_delay(
                            conversation_id, 10, key=key, reason=r)
                    continue

            # Load conversation history
            messages_data = store.load(conversation_id)
            if not messages_data:
                continue

            # Scheduled rechecks bypass eligibility (explicitly requested by agent)
            if conversation_id not in scheduled_ids:
                if not self._is_eligible_for_poll(conversation_id, messages_data):
                    continue

            logger.info(f"[poller] Waking up conversation {conversation_id[:8]}")

            # Bump generation for the poll run
            with self._conv_gen_lock:
                gen = self._conv_generation.get(conversation_id, 0) + 1
                self._conv_generation[conversation_id] = gen

            # Mark as active
            with self._active_lock:
                self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1

            # Build context and run agent loop
            try:
                reasons = scheduled_reasons.get(conversation_id, [])
                ctx = self._build_poll_context(conversation_id, messages_data,
                                               scheduled_reasons=reasons)
                if ctx is None:
                    with self._active_lock:
                        rc = self._active_conversations.get(conversation_id, 1) - 1
                        if rc <= 0:
                            self._active_conversations.pop(conversation_id, None)
                        else:
                            self._active_conversations[conversation_id] = rc
                    continue
                ctx["_generation"] = gen
                ctx["_gen_key"] = conversation_id

                # _active_contexts is managed by _run_agent_loop (push/pop in finally)
                bus.publish_event(conversation_id, "thinking", {
                    "iteration": 0,
                    "poll": True,
                })

                thread = threading.Thread(
                    target=self._streaming_agent_loop,
                    args=(ctx, conversation_id, bus),
                    daemon=True,
                    name=f"agent-poll-{conversation_id[:8]}",
                )
                thread.start()
            except Exception as e:
                logger.error(f"[poller] Failed to wake {conversation_id[:8]}: {e}")
                with self._active_lock:
                    rc = self._active_conversations.get(conversation_id, 1) - 1
                    if rc <= 0:
                        self._active_conversations.pop(conversation_id, None)
                    else:
                        self._active_conversations[conversation_id] = rc

        # Process thought entries individually (each agent gets its own loop)
        for entry in thought_entries:
            cid = entry["conversation_id"]
            entry_key = entry.get("key", cid)
            reason = entry.get("reason", "scheduled recheck")
            _is_task = "::task::" in entry_key
            _is_task_verify = "::task_verify::" in entry_key
            _is_task_context = _is_task or _is_task_verify
            _task_id_tmp = ""
            _task_data_tmp = {}
            _task_context_agent = ""

            # For task sub-conversations, load from the sub-conv
            if _is_task_context:
                _task_id_tmp = entry_key.rsplit("::", 1)[-1]
                _all_tasks_tmp = store.get_extra(cid, "agent_tasks") or {}
                _task_data_tmp = _all_tasks_tmp.get(_task_id_tmp, {})
                if _is_task_verify:
                    _task_context_agent = (
                        _task_data_tmp.get("verifier", "")
                        or self._extract_agent_from_reasons([reason])
                        or _task_data_tmp.get("agent", "")
                    )
                else:
                    _task_context_agent = _task_data_tmp.get("agent", "")
                try:
                    _task_ctx_data = (store.load_agent_context(entry_key, _task_context_agent)
                                      if _task_context_agent else None)
                except ValueError:
                    _task_ctx_data = None
                try:
                    messages_data = _task_ctx_data if _task_ctx_data is not None else store.load(entry_key)
                except ValueError:
                    messages_data = []
                if messages_data:
                    # Subsequent iteration. Interactive tasks must not receive
                    # a bare "continue" because it can be mistaken for user data.
                    import uuid as _poll_uuid
                    from core.conversation_writer import ConversationWriter
                    from core.llm_client import stamp_message
                    _continue_content = "continue"
                    if _task_data_tmp.get("interactive"):
                        _continue_content = (
                            "[System: Scheduled task wake-up. No new user message "
                            "was provided. Continue only if the task can progress "
                            "without user input. If you are waiting for the user, "
                            "report that you are still waiting and do not invent "
                            "the missing answer.]"
                        )
                    _continue_msg = stamp_message({
                        "role": "user", "content": _continue_content,
                        "source": ({"type": "user", "target_agent": _task_context_agent}
                                   if _task_context_agent else {"type": "context"}),
                        "msg_id": _poll_uuid.uuid4().hex[:12]}, entry_key)
                    ConversationWriter.for_conversation(entry_key).enqueue_message(
                        _continue_msg,
                        wait=True)
                    if _task_ctx_data is not None and _task_context_agent:
                        messages_data = list(_task_ctx_data) + [_continue_msg]
                        store.save_agent_context(
                            entry_key, _task_context_agent, messages_data)
                    else:
                        messages_data = store.load(entry_key)
                if not messages_data:
                    # First iteration — sub-conv doesn't exist yet.
                    if _is_task_verify:
                        _task_prompt = "[System: Task verification context initialized.]"
                    else:
                        _task_prompt = _task_data_tmp.get("task", "") or _task_data_tmp.get("prompt", "") or reason
                    # Inject task prompt as a normal user message
                    # (as if the human owner sent it directly)
                    _meta_tmp = store.get_metadata(cid)
                    _uid_tmp = _meta_tmp["user_id"] if _meta_tmp else ""
                    import uuid as _poll_uuid
                    from core.llm_client import stamp_message
                    messages_data = [stamp_message({
                        "role": "user", "content": _task_prompt,
                        "source": ({"type": "user", "target_agent": _task_context_agent}
                                   if _task_context_agent else {"type": "context"}),
                        "msg_id": _poll_uuid.uuid4().hex[:12]}, entry_key)]
                    store.save(entry_key, messages_data, user_id=_uid_tmp)
                    # Set permission_mode on sub-conv if auto_allow
                    if _task_data_tmp.get("auto_allow"):
                        store.set_extra(entry_key, "permission_mode", "auto")
            else:
                messages_data = store.load(cid)
            if not messages_data:
                continue

            # Extract agent name from key
            if _is_task_context:
                _task_id = entry_key.rsplit("::", 1)[-1]
                _all_tasks = store.get_extra(cid, "agent_tasks") or {}
                _task_entry = _all_tasks.get(_task_id, {})
                if _is_task_verify:
                    _thought_agent = _task_context_agent or _task_entry.get("verifier", "") or _task_entry.get("agent", "")
                else:
                    _thought_agent = _task_entry.get("agent", "")
                # Skip cancelled/completed/failed tasks
                _task_status = _task_entry.get("status", "")
                if _task_status in ("cancelled", "completed", "failed"):
                    logger.info("[poller] Skipping task %s — status=%s", _task_id, _task_status)
                    with self._active_lock:
                        self._active_thoughts.discard(entry_key)
                    continue
                if _is_task:
                    # ── Pre-launch limit checks ──
                    _cancel_reason = _check_task_limits(_task_entry, _task_id)
                    if _cancel_reason:
                        logger.info("[poller] Cancelling task %s — %s", _task_id, _cancel_reason)
                        _task_entry["status"] = "cancelled"
                        _task_entry["cancel_reason"] = _cancel_reason
                        _all_tasks[_task_id] = _task_entry
                        store.set_extra(cid, "agent_tasks", _all_tasks)
                        bus.publish_event(cid, "task_stopped", {
                            "task_id": _task_id, "agent_name": _thought_agent,
                            "reason": _cancel_reason, "force": True})
                        try:
                            from core.task_lifecycle import cleanup_agent_task_context
                            cleanup_agent_task_context(
                                cid, _task_id, _thought_agent, store,
                                clear_runtime=True, reason="task_limit_cancel")
                        except Exception:
                            logger.debug("task limit cleanup failed", exc_info=True)
                        with self._active_lock:
                            self._active_thoughts.discard(entry_key)
                        continue
                    # ── Increment reschedule_count (only real task runs, not verification) ──
                    _task_entry["reschedule_count"] = _task_entry.get("reschedule_count", 0) + 1
                    _all_tasks[_task_id] = _task_entry
                    store.set_extra(cid, "agent_tasks", _all_tasks)
            elif "::" in entry_key:
                # Thought key: conv::thought::agent_name
                _thought_agent = entry_key.rsplit("::", 1)[-1]
            else:
                # Resolve from active_resources
                _ar = store.get_extra(cid, "active_resources") or {}
                _thought_agent = _ar.get("agent", "")

            if not _thought_agent:
                logger.error(f"[BUG] Poller entry '{entry_key}' has no agent name! "
                             f"reason={reason}, conv={cid[:8]}. "
                             f"This should never happen — a schedule was created without agent.")
                with self._active_lock:
                    self._active_thoughts.discard(entry_key)
                continue

            # Validate agent exists — never spawn a phantom agent
            if "::plan::" in entry_key or "::plan_verify::" in entry_key:
                try:
                    from core.resource_store import ResourceStore as _RS_chk
                    _meta_chk = store.get_metadata(cid)
                    _uid_chk = _meta_chk["user_id"] if _meta_chk else ""
                    _adef_chk = _RS_chk.instance().get_any(
                        "agent", _thought_agent, _uid_chk,
                        conversation_id=cid)
                    if not _adef_chk:
                        logger.error(
                            f"[plan] Agent '{_thought_agent}' not found in "
                            f"ResourceStore for entry '{entry_key}'. "
                            f"Refusing to spawn — would fallback on default LLM.")
                        with self._active_lock:
                            self._active_thoughts.discard(entry_key)
                        continue
                except Exception as _e_chk:
                    logger.error(f"[plan] Failed to validate agent '{_thought_agent}': {_e_chk}")
                    with self._active_lock:
                        self._active_thoughts.discard(entry_key)
                    continue

            # Skip if this agent already has a thought running
            with self._active_lock:
                if entry_key in self._active_thoughts:
                    logger.info(f"[poller] Skipping thought {entry_key} — already running")
                    continue
                self._active_thoughts.add(entry_key)

            logger.info(f"[poller] Waking thought {entry_key} (agent={_thought_agent})")
            bus.publish_event(cid, "thought_firing", {"agent": _thought_agent})

            # Each thought agent gets its own gen_key so multiple thoughts
            # on the same conversation don't invalidate each other.
            _thought_gen_key = entry_key  # e.g. "conv_id::thought::grok"
            with self._conv_gen_lock:
                gen = self._conv_generation.get(_thought_gen_key, 0) + 1
                self._conv_generation[_thought_gen_key] = gen

            # Mark as active (but NOT user-active — won't block other thoughts)
            with self._active_lock:
                self._active_conversations[cid] = self._active_conversations.get(cid, 0) + 1

            try:
                # Build context using parent cid for metadata/user_id, but with
                # the isolated task or task-verification messages.
                ctx = self._build_poll_context(cid, messages_data,
                                               scheduled_reasons=[reason],
                                               skip_agent_context=_is_task_context,
                                               preloaded_conversation_id=entry_key if _is_task_context else "",
                                               independent_context=_is_task_context)
                # For task sub-conversations, override the conversation_id so
                # messages are persisted in the sub-conv, not the parent.
                if _is_task_context and ctx:
                    ctx["conversation_id"] = entry_key
                    # Don't resume parent's Claude Code session
                    ctx["_claude_has_session"] = False
                    # Track iteration number for transcript grouping (reschedule_count = iteration number)
                    if _is_task:
                        ctx["_task_iteration"] = _task_entry.get("reschedule_count", 0) if _task_entry else 0
                    ctx["_independent_context"] = True
                if ctx is None:
                    with self._active_lock:
                        rc = self._active_conversations.get(cid, 1) - 1
                        if rc <= 0:
                            self._active_conversations.pop(cid, None)
                        else:
                            self._active_conversations[cid] = rc
                        self._active_thoughts.discard(entry_key)
                    continue
                ctx["_generation"] = gen
                ctx["_gen_key"] = _thought_gen_key
                ctx["_thought_key"] = entry_key

                # _active_contexts is managed by _run_agent_loop (push/pop in finally)
                _thinking_evt = {
                    "iteration": 0,
                    "poll": True,
                    "agent_name": _thought_agent,
                }
                # Include task_id + iteration so the UI creates the right task block
                if _is_task:
                    _thinking_evt["task_id"] = _task_id
                    _thinking_evt["task_iteration"] = _task_entry.get("reschedule_count", 0) if _task_entry else 0
                bus.publish_event(cid, "thinking", _thinking_evt)

                # For task entries, use the sub-conversation ID so messages are
                # persisted in the isolated task or verification context.
                _loop_cid = entry_key if _is_task_context else cid
                # But publish events on the parent conv so webchat sees them
                if _is_task_context:
                    ctx["_event_cid"] = cid
                thread = threading.Thread(
                    target=self._streaming_agent_loop,
                    args=(ctx, _loop_cid, bus),
                    daemon=True,
                    name=f"agent-thought-{entry_key[-16:]}",
                )
                thread.start()

                # Task timeout watchdog
                if _is_task:
                    _task_timeout = (_task_entry or {}).get("timeout", 0)
                    if _task_timeout and _task_timeout > 0:
                        _wdog_tid = _task_id
                        _wdog_agent = _thought_agent
                        _wdog_cid = cid
                        _wdog_thread = thread
                        def _timeout_watchdog():
                            _wdog_thread.join(timeout=_task_timeout)
                            if _wdog_thread.is_alive():
                                logger.warning("[task:%s] timeout after %ds, interrupting",
                                              _wdog_tid, _task_timeout)
                                from tasks.ai.actions.scheduling import _kill_running_task_agent
                                _kill_running_task_agent(
                                    self, _wdog_cid, _wdog_tid, _wdog_agent, force=False)
                        threading.Thread(
                            target=_timeout_watchdog, daemon=True,
                            name=f"task-timeout-{_wdog_tid}",
                        ).start()
            except Exception as e:
                logger.error(f"[poller] Failed thought {entry_key}: {e}")
                with self._active_lock:
                    rc = self._active_conversations.get(cid, 1) - 1
                    if rc <= 0:
                        self._active_conversations.pop(cid, None)
                    else:
                        self._active_conversations[cid] = rc
                    self._active_thoughts.discard(entry_key)


    def _reschedule_active_tasks(self):
        """On poller startup, reschedule any active tasks that survived a restart."""
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler
        store = ConversationStore.instance()
        scheduler = PollScheduler.instance()
        count = 0
        for conv in store.list_conversations():
            cid = conv["conversation_id"]
            _cache = store._load_cache(cid)
            if "agent_tasks" not in _cache.get("extra_keys", set()):
                continue
            all_tasks = store.get_extra(cid, "agent_tasks") or {}
            if not isinstance(all_tasks, dict):
                continue
            for task_id, task in all_tasks.items():
                if not isinstance(task, dict):
                    continue
                if task.get("status") not in ("active", "verifying"):
                    continue
                agent = task.get("agent", "")
                sched_key = f"{cid}::task::{task_id}"
                existing = scheduler.get(sched_key)
                if existing:
                    continue
                from core.tool_registry import AssignTaskHandler as _ATH_rs
                interval_s = _ATH_rs._get_task_delay(task)
                scheduler.schedule_delay(
                    cid, interval_s,
                    key=sched_key,
                    reason=f"[agent_task:{task_id}] resumed after restart ({agent})",
                    user_id=task.get("assigned_by", ""),
                )
                count += 1
                logger.info(f"[task] Rescheduled {task_id} for {agent} "
                            f"in conv {cid[:8]} (interval={interval_s}s)")
        if count:
            logger.info(f"[task] Rescheduled {count} active task(s) on startup")


    def _ensure_tasks_scheduled(self):
        """Watchdog: ensure every active task has a pending schedule.

        Called at each poll cycle. If a task is active but has no schedule
        (lost due to race condition, restart, etc.), recreate it.
        """
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler
        sched = PollScheduler.instance()
        store = ConversationStore.instance()
        for conv in store.list_conversations():
            cid = conv["conversation_id"]
            _cache = store._load_cache(cid)
            if "agent_tasks" not in _cache.get("extra_keys", set()):
                continue
            all_tasks = _cache.get("extras", {}).get("agent_tasks") or {}
            if not isinstance(all_tasks, dict):
                continue
            for tid, task in all_tasks.items():
                if not isinstance(task, dict):
                    continue
                if task.get("status") not in ("active",):
                    continue
                sched_key = f"{cid}::task::{tid}"
                if sched.get(sched_key):
                    continue  # already scheduled
                # Don't reschedule if task is currently running
                with self._active_lock:
                    if sched_key in self._active_thoughts:
                        continue
                # Check limits before rescheduling
                _cancel = _check_task_limits(task, tid)
                if _cancel:
                    task["status"] = "cancelled"
                    task["cancel_reason"] = _cancel
                    all_tasks[tid] = task
                    store.set_extra(cid, "agent_tasks", all_tasks)
                    logger.info(f"[task-watchdog] Cancelled task {tid}: {_cancel}")
                    continue
                from core.tool_registry import AssignTaskHandler
                delay = AssignTaskHandler._get_task_delay(task)
                sched.schedule_delay(
                    cid, delay, key=sched_key,
                    reason=f"[agent_task:{tid}] watchdog reschedule ({task.get('agent', '?')})",
                    user_id=task.get("assigned_by", ""),
                )
                logger.info(f"[task-watchdog] Rescheduled lost task {tid} for "
                            f"{task.get('agent', '?')} in {cid[:8]}")


    def _ensure_thoughts_scheduled(self):
        """Watchdog: ensure every enabled autoconv thought has a pending schedule.

        Scans all conversations for random_thought::* extras with enabled=True.
        If no matching schedule exists in PollScheduler, creates one.
        This handles restarts where the PollScheduler file lost the schedule.
        """
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler
        import random as _rng
        sched = PollScheduler.instance()
        store = ConversationStore.instance()
        for conv in store.list_conversations():
            cid = conv["conversation_id"]
            _cache = store._load_cache(cid)
            if not any(k.startswith("random_thought::") for k in _cache.get("extra_keys", set())):
                continue
            extra = _cache.get("extras", {})
            for key, val in extra.items():
                if not key.startswith("random_thought::") or not isinstance(val, dict):
                    continue
                if not val.get("enabled"):
                    continue
                agent = val.get("agent", key.split("::")[-1])
                agent_key = agent.lower()
                thought_key = f"{cid}::thought::{agent_key}"
                if sched.get(thought_key):
                    continue  # already scheduled
                # Not scheduled — recreate
                min_iv = val.get("min_interval", 10)
                max_iv = val.get("max_interval", 10)
                delay = _rng.randint(min_iv, max_iv)  # nosec B311
                sched.schedule_delay(
                    cid, delay, key=thought_key,
                    reason=f"[random_thought] watchdog reschedule ({agent})",
                    user_id=conv.get("user_id", ""),
                )
                logger.info(f"[thought-watchdog] Rescheduled autoconv for {agent} "
                            f"in {cid[:8]} (delay={delay}s)")


    def _is_eligible_for_poll(self, conversation_id: str,
                              messages_data: List[Dict]) -> bool:
        """Check if a conversation is eligible for autonomous polling.

        Eligible if conversation status is ``active`` (set by the agent when
        it used tools and may have follow-up work).  Falls back to message
        heuristics if status is not set.
        """
        if not messages_data or len(messages_data) < 3:
            return False

        # Primary check: use conversation status
        from core.conversation_store import ConversationStore
        meta = ConversationStore.instance().get_metadata(conversation_id)
        if meta:
            status = meta.get("status", "idle")
            # Only poll active conversations
            if status != "active":
                return False

        # Find the last non-system message
        last_msg = None
        for msg in reversed(messages_data):
            role = msg.get("role", "")
            if role in ("assistant", "user", "tool"):
                last_msg = msg
                break

        if not last_msg:
            return False

        # Must end with assistant message (not waiting for user)
        if last_msg.get("role") != "assistant":
            return False

        # Don't re-poll if last message is already a poll check-in response
        content = last_msg.get("content", "")
        if "[NO_PENDING_WORK]" in content:
            return False

        # Must have had tool calls in history (active work, not just chat)
        has_tools = any(
            msg.get("role") == "tool" or msg.get("tool_calls")
            for msg in messages_data
        )
        if not has_tools:
            return False

        return True

