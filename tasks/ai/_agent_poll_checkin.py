"""Poll-context assembly and check-in payload builders for AgentPollerMixin
(periodic check-ins, plan-step / plan-verify check-ins).

Split out of agent_poller.py as a leaf mixin so the file stays <= 800 lines.
Methods rely on AgentPollerMixin host state/methods via the MRO.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage

logger = logging.getLogger(__name__)


class _AgentPollCheckinMixin:
    """Poll-context + check-in payload builders for AgentPollerMixin."""

    def _build_poll_context(self, conversation_id: str,
                            messages_data: List[Dict],
                            scheduled_reasons: Optional[List[str]] = None,
                            skip_agent_context: bool = False,
                            preloaded_conversation_id: str = "",
                            independent_context: bool = False,
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
                logger.debug("exception suppressed", exc_info=True)

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
                preloaded_conversation_id=preloaded_conversation_id,
                independent_context=independent_context,
            )
        except Exception as e:
            logger.error(f"[poll] _prepare_agent_context failed for {conversation_id[:8]}: {e}")
            return None

        # Override use_conv_store (always True for polls)
        ctx["use_conv_store"] = True

        # Poll-specific flags
        _is_task = any("[agent_task:" in r for r in (scheduled_reasons or []))
        _is_task_verify = any("[task_verify:" in r for r in (scheduled_reasons or []))
        _is_plan_step = any("[plan_step:" in r for r in (scheduled_reasons or []))
        _is_plan_verify = any("[plan_verify:" in r for r in (scheduled_reasons or []))
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
            _is_plan_step, _is_plan_verify,
            user_id=_poll_uid,
        )
        if checkin_content:
            ctx["messages"].append(LLMMessage(role="user", content=checkin_content,
                                               conversation_id=conversation_id))
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
            compact_match = re.match(r'\[compact_resume:([\w.-]+)\]', sr)
            if compact_match:
                return compact_match.group(1)
            tv_match = re.search(r'\[task_verify:[^\]]+\].*by ([\w.-]+)', sr)
            if tv_match:
                return tv_match.group(1)
            plan_match = re.search(r'\[plan_step:\w+:\d+\]\s*\(([\w.-]+)\)', sr)
            if plan_match:
                return plan_match.group(1)
            pv_match = re.search(r'\[plan_verify:\w+:\d+:[\w.-]+\]\s*\(([\w.-]+)\)', sr)
            if pv_match:
                return pv_match.group(1)
            sched_match = re.match(r'\[scheduled:([\w.-]+)\]', sr)
            if sched_match:
                return sched_match.group(1)
        return None


    def _build_poll_checkin(self, conversation_id: str,
                            scheduled_reasons: List[str],
                            agent_name: str,
                            is_task: bool, is_task_verify: bool,
                            is_random_thought: bool,
                            is_plan_step: bool = False,
                            is_plan_verify: bool = False,
                            user_id: str = "") -> str:
        """Build the check-in prompt for a poll-triggered agent run."""
        from core.conversation_store import ConversationStore as _CS3

        if is_plan_verify:
            return self._build_plan_verify_checkin(
                conversation_id, scheduled_reasons, agent_name, user_id=user_id)

        if is_plan_step:
            return self._build_plan_step_checkin(
                conversation_id, scheduled_reasons, agent_name, user_id=user_id)

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
                _iter = _td.get("reschedule_count", 0)
                _max = _td.get("max_iterations", 0)
                _rejection = _td.get("last_rejection")
                _rej_text = ""
                if _rejection:
                    _rej_text = (
                        f"\n\n[REJECTION] Rejected by {_rejection.get('by', '?')}: "
                        f"\"{_rejection.get('reason', '')}\". Address this."
                    )
                if _max > 0 and _iter >= _max:
                    # Remove instance — only task_def + log remain
                    del _all_tasks[_tid]
                    _CS3.instance().set_extra(conversation_id, "agent_tasks", _all_tasks)
                    return (
                        f"[System: Task {_tid} failed — max iterations ({_max}) reached]\n"
                        f"Inform the user."
                    )
                from datetime import datetime as _DTtask
                _created_str = _DTtask.fromtimestamp(
                    _td.get("created_at", 0)).strftime("%Y-%m-%d %H:%M") if _td.get("created_at") else "?"
                _iter_label = f"{_iter}/{_max}" if _max > 0 else str(_iter)
                return (
                    f"[System: Task {_tid} — iteration {_iter_label}]\n\n"
                    f"**Task ID:** {_tid} (assigned {_created_str})\n"
                    f"**Task:** {_td.get('task', '?')}\n"
                    + (f"**Criteria:** {_td.get('completion_criteria', '')}\n" if _td.get("completion_criteria") else "")
                    + (f"**Progress so far (this instance only):** {_td.get('last_result', 'None yet')}\n"
                       if _iter > 0 else "**Progress:** None yet — this is iteration 1. "
                       "Start working on the task.\n")
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
                _iter = _td.get("reschedule_count", 0)
                _max = _td.get("max_iterations", 0)
                _il = f"{_iter}/{_max}" if _max > 0 else str(_iter)
                lines.append(
                    f"- **{_tid}** (iter {_il}): {_td.get('task', '?')[:100]}"
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
                "You can also engage other agents via delegate if you want their perspective.\n"
                "Do NOT respond with [NO_PENDING_WORK] — always contribute something."
            )

        if any(r.startswith("[compact_resume:") for r in scheduled_reasons):
            return (
                "[System: Context compaction completed. Continue the interrupted "
                "work immediately from the compacted context. Do not wait for a "
                "new user message, and do not respond with [NO_PENDING_WORK].]"
            )

        if scheduled_reasons:
            from datetime import datetime, timezone as _tz_checkin
            _now_str = datetime.now(_tz_checkin.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            reasons_text = "\n".join(f"- {r}" for r in scheduled_reasons)
            return (
                f"[System: Scheduled wake-up — {_now_str}]\n"
                f"You are being woken up because of scheduled reminder(s):\n"
                f"{reasons_text}\n\n"
                "IMPORTANT: This is a NEW wake-up. Any similar work you see in the "
                "conversation history above was done in a PREVIOUS session. You must "
                "execute the scheduled task(s) NOW, fresh — do not skip them because "
                "they appear to have been done before.\n\n"
                "Act on these scheduled reasons using your tools.\n"
                "Do NOT respond with [NO_PENDING_WORK] unless you have fully "
                "addressed all scheduled reasons above IN THIS SESSION."
            )

        return (
            "[System: Autonomous check-in]\n"
            "Review the conversation above. Is there pending research or work "
            "that you started but didn't finish? If yes, continue working on it "
            "using your available tools.\n"
            "If everything is complete, respond with [NO_PENDING_WORK].\n"
            "You can also use the ScheduleWakeup tool to schedule a future check-in "
            "at a specific time or after a delay."
        )


    def _build_plan_step_checkin(self, conversation_id: str,
                                 scheduled_reasons: List[str],
                                 agent_name: str,
                                 user_id: str = "") -> str:
        """Plan step check-in — returns empty string.

        The step instruction is already in the conversation as a real user
        message (written by _orchestrate_next_step). No duplicate needed.
        The poller just wakes the agent — the message is in the context.
        """
        return ""

    def _build_plan_verify_checkin(self, conversation_id: str,
                                    scheduled_reasons: List[str],
                                    agent_name: str,
                                    user_id: str = "") -> str:
        """Build check-in prompt for a plan step verification."""
        import re

        # Extract plan_id, step number, and executor from reason:
        # [plan_verify:p_xxx:N:executor_agent] (verifier)
        plan_id = ""
        step_num = 0
        executor = ""
        for sr in scheduled_reasons:
            m = re.search(r'\[plan_verify:(p_\w+):(\d+):([\w.-]+)\]', sr)
            if m:
                plan_id = m.group(1)
                step_num = int(m.group(2))
                executor = m.group(3)
                break

        if not plan_id:
            return "[System: Plan verification scheduled but no plan_id found.]"

        from core.plan_store import PlanStore
        plan = PlanStore.instance().get(user_id, conversation_id, plan_id)
        if not plan:
            return f"[System: Plan {plan_id} not found.]"

        step = None
        for s in plan["steps"]:
            if s["index"] == step_num:
                step = s
                break
        if not step:
            return f"[System: Step {step_num} not found in plan {plan_id}.]"

        # Build context: show full plan with current step highlighted
        total = len(plan["steps"])
        steps_text = ""
        for s in plan["steps"]:
            marker = ">>" if s["index"] == step_num else "  "
            icon = {"done": "\u2713", "skipped": "\u2014", "in_progress": "\u25d4",
                    "error": "\u2717", "pending": "\u25cb",
                    "pending_verification": "\u2690"}.get(s["status"], "?")
            steps_text += f"{marker} {icon} {s['index']}. {s['description']}"
            if s.get("note"):
                steps_text += f" [{s['note']}]"
            steps_text += "\n"

        executor_note = step.get("note", "No note provided.")

        return (
            f"Verify step {step_num}/{total} of plan \"{plan['title']}\": "
            f"{step['description']}\n\n"
            f"Plan: {plan_id}\n"
            f"Executed by: {executor}\n"
            f"Executor's note: {executor_note}\n\n"
            f"All steps:\n{steps_text}\n"
            f"Review step {step_num}. Verify the work was done correctly.\n"
            f"When done, call:\n"
            f"  verify_plan_step(plan_id=\"{plan_id}\", step={step_num}, "
            f"approved=true, reason=\"looks good\")\n\n"
            f"If the step needs rework:\n"
            f"  verify_plan_step(plan_id=\"{plan_id}\", step={step_num}, "
            f"approved=false, reason=\"what needs to be fixed\")\n\n"
            f"Do NOT respond with text only — verify and call the tool."
        )

