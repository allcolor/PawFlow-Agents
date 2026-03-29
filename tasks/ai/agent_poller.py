"""AgentLoopTask mixin — AgentPoller methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional


from core import FlowFile
from core.llm_client import LLMMessage

logger = logging.getLogger(__name__)



class AgentPollerMixin:
    """Methods extracted from AgentLoopTask."""


    def _poll_conversations(self, interval: int) -> None:
        """Background poller: periodically check active conversations for pending work.

        For each eligible conversation (has an SSE subscriber, not currently being
        processed, last message was from assistant with tool usage), re-run the
        agent loop with a check-in prompt.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore

        logger.info(f"Agent poller running (interval={interval}s)")

        # On startup: reschedule any active tasks that have no pending schedule
        try:
            self._reschedule_active_tasks()
        except Exception as e:
            logger.warning(f"Failed to reschedule active tasks on startup: {e}")

        while not self._poller_stop.wait(interval):
            try:
                self._poll_once()
            except Exception as e:
                logger.error(f"Agent poller error: {e}", exc_info=True)


    def _poll_once(self) -> None:
        """Single poll iteration: check scheduled rechecks and active conversations."""
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler

        bus = ConversationEventBus.instance()
        store = ConversationStore.instance()
        scheduler = PollScheduler.instance()

        # Checkpoint cleanup (once per day, tracked by class var)
        try:
            _now = time.time()
            _last_cleanup = getattr(self, '_last_checkpoint_cleanup', 0)
            if _now - _last_cleanup > 86400:  # 24h
                from core.checkpoint import CheckpointManager
                _cleaned = CheckpointManager.cleanup_old(30)
                if _cleaned:
                    logger.info(f"[checkpoint] cleaned {_cleaned} old checkpoint(s)")
                self._last_checkpoint_cleanup = _now
        except Exception as _cp_err:
            logger.debug(f"[checkpoint] cleanup failed: {_cp_err}")

        # Watchdog: ensure active tasks always have a pending schedule
        try:
            self._ensure_tasks_scheduled()
        except Exception as _wt_err:
            logger.warning(f"Task watchdog failed: {_wt_err}")

        # Watchdog: ensure enabled autoconv thoughts have a pending schedule
        try:
            self._ensure_thoughts_scheduled()
        except Exception as _wt_err:
            logger.warning(f"Thought watchdog failed: {_wt_err}")

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
            # Skip if already being processed — but reschedule so we don't lose it
            with self._active_lock:
                if conversation_id in self._active_conversations:
                    # Re-schedule the reasons so they're not lost
                    reasons = scheduled_reasons.get(conversation_id, [])
                    for r in reasons:
                        import re as _re_resched
                        # Extract task_id from reason pattern [agent_task:t_xxx]
                        _tid_m = _re_resched.search(r'\[agent_task:(t_\w+)\]', r)
                        if _tid_m:
                            scheduler.schedule_delay(
                                conversation_id, 10,  # retry in 10s
                                key=f"{conversation_id}::task::{_tid_m.group(1)}",
                                reason=r,
                            )
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

                # Register in active interactions
                _poll_agent = ctx.get("active_agent_name", "") or ""
                with self._running_agents_lock:
                    self._running_agents[conversation_id] = {
                        "agent_name": _poll_agent,
                        "message_preview": ", ".join(reasons)[:80] if reasons else "poll",
                        "started_at": time.time(),
                        "iteration": 0,
                        "last_tool": "",
                        "status": "thinking",
                        "conversation_id": conversation_id,
                    }

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

            # For task sub-conversations, load from the sub-conv
            if "::task::" in entry_key:
                messages_data = store.load(entry_key)
                if not messages_data:
                    # First iteration — sub-conv doesn't exist yet.
                    # Load only the task prompt from parent extras, NOT the full parent conv.
                    _task_id_tmp = entry_key.rsplit("::", 1)[-1]
                    _all_tasks_tmp = store.get_extra(cid, "agent_tasks") or {}
                    _task_data_tmp = _all_tasks_tmp.get(_task_id_tmp, {})
                    _task_prompt = _task_data_tmp.get("prompt", "") or reason
                    # Create a minimal initial context for the task
                    messages_data = [
                        {"role": "user", "content": _task_prompt},
                    ]
                    # Save as the initial sub-conversation
                    _meta_tmp = store.get_metadata(cid)
                    _uid_tmp = _meta_tmp["user_id"] if _meta_tmp else ""
                    store.save(entry_key, messages_data, user_id=_uid_tmp)
            else:
                messages_data = store.load(cid)
            if not messages_data:
                continue

            # Extract agent name from key
            if "::task::" in entry_key or "::task_verify::" in entry_key:
                _task_id = entry_key.rsplit("::", 1)[-1]
                _all_tasks = store.get_extra(cid, "agent_tasks") or {}
                _task_entry = _all_tasks.get(_task_id, {})
                _thought_agent = _task_entry.get("agent", "")
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

            # Skip if this agent already has a thought running
            with self._active_lock:
                if entry_key in self._active_thoughts:
                    logger.info(f"[poller] Skipping thought {entry_key} — already running")
                    continue
                self._active_thoughts.add(entry_key)

            logger.info(f"[poller] Waking thought {entry_key} (agent={_thought_agent})")
            store.set_status(cid, "active")
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
                # Build context using parent cid (for metadata, user_id, services)
                # but with the task's messages_data (isolated or sub-conv)
                _is_task = "::task::" in entry_key
                ctx = self._build_poll_context(cid, messages_data,
                                               scheduled_reasons=[reason],
                                               skip_agent_context=_is_task)
                # For task sub-conversations, override the conversation_id
                # so messages are persisted in the sub-conv, not the parent
                if _is_task and ctx:
                    ctx["conversation_id"] = entry_key
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

                # Register in active interactions so list_active reports it
                with self._running_agents_lock:
                    self._running_agents[_thought_gen_key] = {
                        "agent_name": _thought_agent,
                        "message_preview": reason[:80],
                        "started_at": time.time(),
                        "iteration": 0,
                        "last_tool": "",
                        "status": "thinking",
                        "conversation_id": cid,
                    }

                bus.publish_event(cid, "thinking", {
                    "iteration": 0,
                    "poll": True,
                    "agent_name": _thought_agent,
                })

                # For task entries, use the sub-conversation ID so messages
                # are persisted in the isolated task context
                _loop_cid = entry_key if "::task::" in entry_key else cid
                thread = threading.Thread(
                    target=self._streaming_agent_loop,
                    args=(ctx, _loop_cid, bus),
                    daemon=True,
                    name=f"agent-thought-{entry_key[-16:]}",
                )
                thread.start()
            except Exception as e:
                logger.error(f"[poller] Failed thought {entry_key}: {e}")
                with self._active_lock:
                    rc = self._active_conversations.get(cid, 1) - 1
                    if rc <= 0:
                        self._active_conversations.pop(cid, None)
                    else:
                        self._active_conversations[cid] = rc
                    self._active_thoughts.discard(entry_key)


    def _build_poll_context(self, conversation_id: str,
                            messages_data: List[Dict],
                            scheduled_reasons: Optional[List[str]] = None,
                            skip_agent_context: bool = False,
                            ) -> Optional[Dict]:
        """Build an agent context for a poll-triggered run.

        Delegates to _prepare_agent_context via a synthetic FlowFile,
        then injects poll-specific fields (check-in prompt, flags).
        """
        from core.conversation_store import ConversationStore as _CS2
        _meta = _CS2.instance().get_metadata(conversation_id)
        _poll_uid = _meta["user_id"] if _meta else ""

        # Resolve agent from scheduled reasons (poll-specific)
        _active_agent = self._extract_agent_from_reasons(scheduled_reasons)
        if not _active_agent:
            try:
                _ar = _CS2.instance().get_extra(conversation_id, "active_resources") or {}
                _active_agent = _ar.get("agent", "")
            except Exception:
                pass

        # Build synthetic FlowFile for _prepare_agent_context
        body = json.dumps({
            "message": "",  # no user message — check-in prompt injected below
            "conversation_id": conversation_id,
            "target_agent": _active_agent or "",
        })
        ff = FlowFile(body.encode("utf-8"))
        ff.set_attribute("http.auth.principal", _poll_uid)

        try:
            ctx = self._prepare_agent_context(
                ff,
                preloaded_messages=messages_data if skip_agent_context else None,
            )
        except Exception as e:
            logger.error(f"[poll] _prepare_agent_context failed for {conversation_id[:8]}: {e}")
            return None

        # Override use_conv_store (always True for polls)
        ctx["use_conv_store"] = True

        # Poll-specific flags
        _is_task = any("[agent_task:" in r for r in (scheduled_reasons or []))
        _is_task_verify = any("[task_verify:" in r for r in (scheduled_reasons or []))
        is_random_thought = any(
            r.startswith("[random_thought]") for r in (scheduled_reasons or [])
        )
        ctx["is_poll"] = True
        ctx["is_random_thought"] = is_random_thought
        ctx["_scheduled_reasons"] = scheduled_reasons or []

        # Build and append check-in prompt
        checkin_content = self._build_poll_checkin(
            conversation_id, scheduled_reasons or [],
            _active_agent or ctx.get("active_agent_name", ""),
            _is_task, _is_task_verify, is_random_thought,
        )
        ctx["messages"].append(LLMMessage(role="user", content=checkin_content))
        ctx["_base_message_count"] = len(ctx["messages"])

        return ctx


    # ── Poll helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_agent_from_reasons(scheduled_reasons: Optional[List[str]]) -> Optional[str]:
        """Extract agent name from scheduled reason patterns."""
        if not scheduled_reasons:
            return None
        import re
        for sr in scheduled_reasons:
            if "[random_thought]" in sr and "(" in sr:
                return sr.rsplit("(", 1)[-1].rstrip(")")
            if "[agent_task:" in sr and "(" in sr:
                return sr.rsplit("(", 1)[-1].rstrip(")")
            tv_match = re.search(r'\[task_verify:(\w+)\].*by (\w+)', sr)
            if tv_match:
                return tv_match.group(2)
            sched_match = re.match(r'\[scheduled:(\w+)\]', sr)
            if sched_match:
                return sched_match.group(1)
        return None


    def _build_poll_checkin(self, conversation_id: str,
                            scheduled_reasons: List[str],
                            agent_name: str,
                            is_task: bool, is_task_verify: bool,
                            is_random_thought: bool) -> str:
        """Build the check-in prompt for a poll-triggered agent run."""
        from core.conversation_store import ConversationStore as _CS3

        if is_task:
            _all_tasks = _CS3.instance().get_extra(conversation_id, "agent_tasks") or {}
            _my_tasks = [t for t in _all_tasks.values()
                         if isinstance(t, dict) and t.get("agent") == agent_name
                         and t.get("status") in ("active",)]
            if not _my_tasks:
                return "[System: No active tasks found.]"
            if len(_my_tasks) == 1:
                _td = _my_tasks[0]
                _tid = _td["task_id"]
                _iter = _td.get("iterations_done", 0)
                _max = _td.get("max_iterations", 50)
                _rejection = _td.get("last_rejection")
                _rej_text = ""
                if _rejection:
                    _rej_text = (
                        f"\n\n[REJECTION] Rejected by {_rejection.get('by', '?')}: "
                        f"\"{_rejection.get('reason', '')}\". Address this."
                    )
                if _iter >= _max:
                    _td["status"] = "failed"
                    _all_tasks[_tid] = _td
                    _CS3.instance().set_extra(conversation_id, "agent_tasks", _all_tasks)
                    return (
                        f"[System: Task {_tid} failed — max iterations ({_max}) reached]\n"
                        f"Inform the user."
                    )
                from datetime import datetime as _DTtask
                _created_str = _DTtask.fromtimestamp(
                    _td.get("created_at", 0)).strftime("%Y-%m-%d %H:%M") if _td.get("created_at") else "?"
                return (
                    f"[System: Task {_tid} — iteration {_iter + 1}/{_max}]\n\n"
                    f"**Task ID:** {_tid} (assigned {_created_str})\n"
                    f"**Task:** {_td.get('task', '?')}\n"
                    + (f"**Criteria:** {_td.get('completion_criteria', '')}\n" if _td.get("completion_criteria") else "")
                    + (f"**Progress so far (this instance only):** {_td.get('last_result', 'None yet')}\n"
                       if _iter > 0 else f"**Progress:** None yet — this is iteration 1. "
                       f"Start working on the task.\n")
                    + _rej_text + "\n\n"
                    "WORK on the task first. After making real progress, report it:\n"
                    f"  complete_task(task_id=\"{_tid}\", done=false, progress=\"what you did\")\n"
                    f"When the criteria are fully met BY YOUR OWN WORK in this instance:\n"
                    f"  complete_task(task_id=\"{_tid}\", done=true, progress=\"summary\")\n\n"
                    "Do NOT call done=true unless YOU actually did the work in THIS session.\n"
                    "Do NOT count work from previous conversations or task instances.\n"
                    "Do NOT respond with [NO_PENDING_WORK]."
                )
            # Multiple tasks
            lines = []
            for _td in _my_tasks:
                _tid = _td["task_id"]
                _iter = _td.get("iterations_done", 0)
                _max = _td.get("max_iterations", 50)
                lines.append(
                    f"- **{_tid}** (iter {_iter + 1}/{_max}): {_td.get('task', '?')[:100]}"
                    + (f" | Progress: {_td.get('last_result', '')[:60]}" if _td.get("last_result") else "")
                )
            return (
                f"[System: {len(_my_tasks)} active tasks]\n\n"
                + "\n".join(lines) + "\n\n"
                "Work on your tasks. Call complete_task(task_id=\"...\", done=true/false, progress=\"...\") for each.\n"
                "Do NOT repeat information from previous iterations. Focus on NEW progress only.\n"
                "Do NOT respond with [NO_PENDING_WORK]."
            )

        if is_task_verify:
            import re as _re_tv
            _verify_reason = next(
                (r for r in scheduled_reasons if "[task_verify:" in r), ""
            )
            _tv_match = _re_tv.search(r'\[task_verify:(t_\w+)\]', _verify_reason)
            _verify_tid = _tv_match.group(1) if _tv_match else ""
            _all_tasks = _CS3.instance().get_extra(conversation_id, "agent_tasks") or {}
            _task_data = _all_tasks.get(_verify_tid, {})
            _verified_agent = _task_data.get("agent", "?")
            return (
                f"[System: Task verification request]\n\n"
                f"Agent '{_verified_agent}' claims to have completed task {_verify_tid}.\n\n"
                f"**Task:** {_task_data.get('task', '?')}\n"
                f"**Completion criteria:** {_task_data.get('completion_criteria', 'none specified')}\n"
                f"**Agent's result:** {_task_data.get('last_result', 'no result provided')}\n\n"
                f"Review the result against the criteria. Call "
                f"verify_task(agent='{_verified_agent}', approved=true/false, reason='...')."
            )

        if is_random_thought:
            return (
                "[System: You are continuing the conversation naturally.]\n"
                "Think about what has been discussed so far. If something comes to mind — "
                "a follow-up, a question, a new angle, something you forgot to mention, "
                "a connection you just made — share it directly.\n"
                "Respond as if you're still in the conversation, not arriving from somewhere else. "
                "No preamble like 'a thought occurred to me' or 'while thinking about it'. "
                "Just say what you have to say, naturally.\n"
                "You can also engage other agents via spawn_agents if you want their perspective.\n"
                "Do NOT respond with [NO_PENDING_WORK] — always contribute something."
            )

        if scheduled_reasons:
            reasons_text = "\n".join(f"- {r}" for r in scheduled_reasons)
            return (
                "[System: Scheduled wake-up]\n"
                f"You are being woken up because of scheduled reminder(s):\n"
                f"{reasons_text}\n\n"
                "Act on these scheduled reasons. Respond to the user accordingly.\n"
                "If the reason is a reminder, remind the user.\n"
                "If the reason is to continue work, continue using your tools.\n"
                "Do NOT respond with [NO_PENDING_WORK] unless you have fully "
                "addressed all scheduled reasons above."
            )

        return (
            "[System: Autonomous check-in]\n"
            "Review the conversation above. Is there pending research or work "
            "that you started but didn't finish? If yes, continue working on it "
            "using your available tools.\n"
            "If everything is complete, respond with [NO_PENDING_WORK].\n"
            "You can also use the schedule_recheck tool to schedule a future check-in "
            "at a specific time or after a delay."
        )


    def _reschedule_active_tasks(self):
        """On poller startup, reschedule any active tasks that survived a restart."""
        from core.conversation_store import ConversationStore
        from core.poll_scheduler import PollScheduler
        store = ConversationStore.instance()
        scheduler = PollScheduler.instance()
        count = 0
        for conv in store.list_conversations():
            cid = conv["conversation_id"]
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
            all_tasks = store.get_extra(cid, "agent_tasks") or {}
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
            extra = store.get_extras(cid) or {}
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
                delay = _rng.randint(min_iv, max_iv)
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

