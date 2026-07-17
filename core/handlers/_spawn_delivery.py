"""Shared-delegate delivery + dedup helpers for SpawnAgentsHandler.

Extracted from resource_agent.py to keep files <=800 lines. These methods are
mixed into SpawnAgentsHandler (and inherited by FlashAgentHandler); they call
each other and the host handler's members via self, so they rely on the host's
MRO and are never instantiated on their own.
"""

import json
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)


class _SpawnDeliveryMixin:
    """Delivery/dedup methods composed onto SpawnAgentsHandler via MRO."""

    # Per-pair short-window dedup: LLMs sometimes call delegate twice
    # in rapid succession with identical content (hallucinated retry,
    # or mid-turn "just checking"). Skipping the duplicate prevents
    # double blocks in the UI and a double wake/preempt of the target.
    _SHARED_DEDUP_TTL_SEC = 30
    _shared_dedup: Dict[str, float] = {}
    _shared_dedup_lock = threading.Lock()

    def _is_duplicate_shared_delegate(self, conv_id: str, from_agent: str,
                                       to_agent: str, message: str) -> bool:
        import hashlib as _h
        import time as _t
        _key = "|".join([
            conv_id, from_agent, to_agent,
            _h.sha1(
                message.encode("utf-8", errors="replace"),
                usedforsecurity=False,
            ).hexdigest(),
        ])
        now = _t.time()
        with self._shared_dedup_lock:
            # Garbage-collect old entries so the dict doesn't grow
            # unboundedly over a long conversation.
            _cutoff = now - self._SHARED_DEDUP_TTL_SEC
            for _k in [k for k, ts in self._shared_dedup.items() if ts < _cutoff]:
                self._shared_dedup.pop(_k, None)
            last = self._shared_dedup.get(_key, 0.0)
            if last and (now - last) < self._SHARED_DEDUP_TTL_SEC:
                return True
            self._shared_dedup[_key] = now
        return False

    def _deliver_shared_delegate(self, from_agent: str, to_agent: str,
                                 message: str, user_id: str,
                                 conv_id: str = "") -> Dict[str, str]:
        """Persist a private delegate message and trigger the target.

        Routing (via ConversationStore.append_message):
          - transcript
          - from_agent's context (prefixed [delegate from→to])
          - to_agent's context (raw, role=user)
          - NOT shared, NOT other agents

        Target trigger:
          - running → preempt queue (stdin injection via send_user_message,
            OR turn-boundary preempt if turn_mode mismatch)
          - idle    → wake by spawning a new agent loop
        """
        import uuid as _uuid
        conv_id = conv_id or self._conversation_id or ""
        # Membership guard: the target agent MUST be a member of this
        # conversation before we persist the delegate msg and wake its
        # loop. Without this, a caller (or a hallucinated tool call)
        # asking to delegate to an unknown agent silently enqueues a
        # message for a phantom, spawns a wake loop, and the phantom's
        # _resolve_agent_client hard-fails late — leaving a dangling
        # message, an orphaned relay, and a confusing error.
        # require_agent_member auto-registers from a global/user agent
        # definition when possible, so valid cross-conv agents "just
        # work"; returns an actionable error otherwise.
        from core.conv_agent_config import require_agent_member
        _member_err = require_agent_member(
            conv_id, to_agent, user_id=user_id)
        if _member_err:
            logger.warning(
                "[delegate-shared] membership check failed: %s",
                _member_err)
            return {"state": f"error: {_member_err}"}
        # Dedup: skip if the same (from, to, message) was just sent.
        if conv_id and self._is_duplicate_shared_delegate(
                conv_id, from_agent, to_agent, message):
            logger.info(
                "[delegate-shared] duplicate within %ds — skipped "
                "(%s -> %s)",
                self._SHARED_DEDUP_TTL_SEC, from_agent, to_agent)
            return {"state": "duplicate (ignored)"}
        _msg_id = _uuid.uuid4().hex[:12]
        _src = {
            "type": "agent_delegate",
            "from": from_agent,
            "to": to_agent,
        }
        # Persist via ConversationWriter.append_message — the unified
        # router reads source.type == "agent_delegate" and routes the
        # message privately to (transcript + from ctx + to ctx) with
        # proper prefixes, skipping shared broadcast and other agents.
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        _delegate_msg = stamp_message({
            "role": "user",
            "content": message,
            "msg_id": _msg_id,
            "source": _src,
        }, conv_id)
        # Publish a live SSE event AFTER the message lands on disk so
        # the webchat renders the delegate block in real time without
        # ever racing ahead of persisted state (visible => persisted).
        _sse_new_msg = {
            "type": "new_message",
            "data": {
                "role": _delegate_msg["role"],
                "content": message,
                "msg_id": _msg_id,
                "source": _src,
                "ts": _delegate_msg.get("ts"),
            },
        }
        try:
            ConversationWriter.for_conversation(conv_id).enqueue_message(
                _delegate_msg, agent_name=from_agent, user_id=user_id,
                sse_events=[_sse_new_msg])
        except Exception as e:
            logger.warning("[delegate-shared] persist failed: %s", e)

        # Trigger the target. Same preempt/wake helpers used by the
        # sub-agent result delivery path — they already know how to
        # route to a specific agent within a conv.
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            inst = AgentLoopTask._live_instance
            if inst:
                key = f"{conv_id}:{to_agent}" if to_agent else conv_id
                # _route_conv_id is the conv the target agent actually
                # runs in — usually conv_id, but if the target is inside
                # a task sub-conv (parent::task::tid:agent) we must use
                # that sub-conv for preempt/wake routing.
                _route_conv_id = conv_id
                with inst._active_contexts_lock:
                    running = key in inst._active_contexts
                    if not running and to_agent:
                        # Scan for the agent in a task sub-conv
                        _prefix = f"{conv_id}::task::"
                        _suffix = f":{to_agent}"
                        for k in inst._active_contexts:
                            if k.startswith(_prefix) and k.endswith(_suffix):
                                key = k
                                # Extract sub-conv ID (everything before :agent)
                                _route_conv_id = k[: -len(_suffix)]
                                running = True
                                break
                if running:
                    logger.info(
                        "[delegate-shared] target '%s' running (key=%s) — preempt",
                        to_agent, key)
                    self._preempt_caller(inst, _route_conv_id, to_agent,
                                         message, _msg_id, _src)
                    return {"state": "running (preempted)"}
                else:
                    logger.info(
                        "[delegate-shared] target '%s' idle — wake", to_agent)
                    self._wake_caller(inst, conv_id, to_agent, user_id,
                                      message, _msg_id, source=_src)
                    return {"state": "idle (waking)"}
        except Exception as e:
            logger.error("[delegate-shared] trigger failed: %s", e)
        return {"state": "unknown (no AgentLoopTask instance)"}

    def _inject_bg_result(self, result, task, conv_id, user_id, source_agent):
        """Deliver a sub-agent's result back to the caller agent.

        Private A↔B channel: only the caller (source_agent) sees this
        message — NOT other agents linked to the conversation. The user
        sees it in the transcript (user sees everything).

        Delivery:
          1. Full response persisted to FileStore (category="delegate_result")
             so the caller can read it in full if needed.
          2. A short "[Delegate result for task_id=X] — read file Y, react"
             prompt-style user message is injected into the caller's
             context only.
          3. If the caller is currently running → preempt (append to
             _pending_user_msgs so the current loop picks it up).
             If the caller is idle → wake a new loop via agent_loop.
        """
        import uuid as _uuid
        try:
            # 1. Persist the full result to the FileStore — the caller
            #    can `read` it if the short summary isn't enough.
            _full_text_parts = [
                "# Delegate result\n",
                f"task_id: {result.task_id}\n",
                f"agent: {result.agent_name}\n",
                f"status: {result.status}\n",
                f"duration: {result.duration_ms/1000:.1f}s\n",
                f"tokens_in: {result.tokens_in}, tokens_out: {result.tokens_out}\n",
            ]
            if result.model:
                _full_text_parts.append(f"model: {result.model}\n")
            if result.tools_called:
                _full_text_parts.append(
                    f"tools_called: {', '.join(result.tools_called)}\n")
            _full_text_parts.append("\n---\n\n")
            if result.response:
                _full_text_parts.append(f"## Response\n\n{result.response}\n")
            if result.error:
                _full_text_parts.append(f"\n## Error\n\n{result.error}\n")
            if result.question:
                _full_text_parts.append(
                    f"\n## Agent needs input\n\n{result.question}\n"
                    f"\nReply by calling delegate("
                    f"agent='{result.agent_name}', message='<your answer>').\n")
            _full_text = "".join(_full_text_parts)

            _file_id = ""
            try:
                from core.file_store import FileStore
                _file_id = FileStore.instance().store(
                    f"delegate_{result.task_id}.md",
                    _full_text.encode("utf-8"),
                    "text/markdown",
                    user_id=user_id, conversation_id=conv_id,
                    category="delegate_result")
            except Exception as _fe:
                logger.warning("[bg-delegate] FileStore persist failed: %s", _fe)

            # 2. Build the short nudge shown in the caller's context.
            #    Deliberately phrased as an imperative user message so the
            #    LLM reacts (same pattern as plan/task injections).
            if result.status == "needs_input" and result.question:
                _summary = (
                    f"[Delegate result for task_id={result.task_id}] "
                    f"Sub-agent '{result.agent_name}' needs your input. "
                    f"Question:\n\n{result.question}\n\n"
                    f"You MUST read the full context in file "
                    f"{_file_id or '<unavailable>'} and reply by calling "
                    f"delegate(agent='{result.agent_name}', "
                    f"message='<your answer>')."
                )
            elif result.error:
                _summary = (
                    f"[Delegate result for task_id={result.task_id}] "
                    f"Sub-agent '{result.agent_name}' FAILED: {result.error[:300]}.\n"
                    f"Full trace in file {_file_id or '<unavailable>'}. "
                    f"Read it and decide how to react (retry, fallback, tell the user)."
                )
            else:
                # Cap inline preview so the context isn't flooded.
                _preview = (result.response or "")[:800]
                _more = (len(result.response or "") > 800)
                _summary = (
                    f"[Delegate result for task_id={result.task_id}] "
                    f"Sub-agent '{result.agent_name}' finished.\n\n"
                    f"{_preview}{'…' if _more else ''}\n\n"
                    f"{'Full response in file ' + _file_id + ' — read it with `read` if you need more.' if _file_id and _more else ''}\n"
                    f"READ this result and REACT: integrate it into your work, "
                    f"or reply to the user with what you learned. Do not ignore it."
                ).rstrip()

            _msg_id = _uuid.uuid4().hex[:12]

            # 3. Deliver to the caller: preempt if running, wake if idle.
            self._deliver_to_caller(
                conv_id=conv_id, caller_agent=source_agent,
                user_id=user_id, text=_summary, msg_id=_msg_id,
                task_id=result.task_id, delegate_agent=result.agent_name,
                file_id=_file_id,
            )

            # 4. If the caller is on a live voice session right now, also
            #    speak the result there (best-effort, on top of the normal
            #    text-channel delivery above).
            self._announce_to_voice_session(conv_id, source_agent, result)
        except Exception as e:
            logger.exception("[bg-delegate] Failed to deliver result for task %s: %s",
                             result.task_id, e)

    def _announce_to_voice_session(self, conv_id, source_agent, result):
        """Speak a delegate result into the conversation's live session.

        Voice-friendly wording (no file ids, bounded preview). Silent
        no-op when there is no live LiveKit session for this agent.
        """
        try:
            from core.service_registry import _parent_conversation_id
            from services._livekit_sessions import (
                announce_to_conversation_session)
            _cid = _parent_conversation_id(conv_id) or conv_id
            if result.error:
                _text = (f"Background delegate '{result.agent_name}' "
                         f"FAILED: {result.error[:300]}. Tell the user and "
                         "decide how to react.")
            elif result.status == "needs_input" and result.question:
                _text = (f"Background delegate '{result.agent_name}' needs "
                         f"input: {result.question[:500]} — relay the "
                         "question to the user.")
            else:
                _text = (f"Background delegate '{result.agent_name}' "
                         f"finished. Result: {(result.response or '')[:800]}"
                         "\nReport the relevant outcome to the user in a "
                         "concise spoken summary.")
            if announce_to_conversation_session(_cid, source_agent, _text):
                logger.info("[bg-delegate] result for task %s spoken into "
                            "live session cid=%s", result.task_id, _cid[:8])
        except Exception:
            logger.debug("[bg-delegate] voice announce skipped",
                         exc_info=True)

    def _deliver_to_caller(self, conv_id, caller_agent, user_id, text, msg_id,
                           task_id, delegate_agent, file_id):
        """Route the delegate result to the caller — preempt-or-wake.

        A delegate is a private A↔B channel: this nudge goes ONLY into
        caller_agent's context (not shared, not other agents). The user
        sees it via the transcript (display_only publish).
        """
        _source = {
            "type": "user",
            "name": "system",
            "target_agent": caller_agent,
            "delegate": {
                "task_id": task_id,
                "agent": delegate_agent,
                "file_id": file_id,
            },
        }

        # Persist + publish display_only nudge so the user sees it in
        # chat AFTER it's on disk (visible ⇒ persisted). Router handles
        # display_only=True → transcript-only. When the caller is a task
        # agent, conv_id is the sub-conv (parent::task::tid) but SSE must
        # go to the parent conv.
        from core.service_registry import _parent_conversation_id
        _sse_cid = _parent_conversation_id(conv_id) or conv_id
        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        _nudge_msg = stamp_message({
            "role": "user",
            "content": text,
            "msg_id": msg_id,
            "display_only": True,
            "source": _source,
        }, conv_id)
        _sse_evt = {
            "type": "new_message",
            "cid": _sse_cid,
            "data": {
                "role": "user",
                "content": text,
                "msg_id": msg_id,
                "display_only": True,
                "source": _source,
            },
        }
        try:
            ConversationWriter.for_conversation(conv_id).enqueue_message(
                _nudge_msg, agent_name=caller_agent, user_id=user_id,
                sse_events=[_sse_evt])
        except Exception as e:
            logger.error("[bg-delegate] persist nudge failed: %s", e, exc_info=True)

        # Check caller state via AgentLoopTask._active_contexts.
        from tasks.ai.agent_loop import AgentLoopTask
        inst = AgentLoopTask._live_instance
        if not inst:
            logger.warning(
                "[bg-delegate] no AgentLoopTask instance — cannot deliver "
                "result for task %s to caller %s", task_id, caller_agent)
            return

        _key = f"{conv_id}:{caller_agent}" if caller_agent else conv_id
        with inst._active_contexts_lock:
            _is_running = _key in inst._active_contexts

        if _is_running:
            # Preempt path: append to the caller's pending queue so the
            # active loop injects it on its next turn boundary.
            logger.info(
                "[bg-delegate] caller '%s' is running — preempting with "
                "result for task %s", caller_agent, task_id)
            self._preempt_caller(inst, conv_id, caller_agent, text, msg_id, _source)
        else:
            # Wake path: no active loop → spawn a fresh stream so the
            # caller reads + reacts to the result.
            logger.info(
                "[bg-delegate] caller '%s' is idle — waking with result "
                "for task %s", caller_agent, task_id)
            self._wake_caller(inst, conv_id, caller_agent, user_id, text, msg_id)

    @staticmethod
    def _preempt_caller(inst, conv_id, caller_agent, text, msg_id, source):
        """Append the delegate result to the caller's PendingQueue — the
        running agent loop will drain it at its next turn boundary."""
        try:
            from core.pending_queue import PendingQueue
            from core.llm_client import stamp_message
            msg = stamp_message({
                "role": "user",
                "content": text,
                "source": source or {"type": "agent_delegate"},
                "msg_id": msg_id or None,
            }, conv_id)
            PendingQueue.for_agent(conv_id, caller_agent or "").enqueue(
                msg, source="delegate_reply")
        except Exception as e:
            logger.error("[bg-delegate] preempt failed: %s", e)

    @staticmethod
    def _wake_caller(inst, conv_id, caller_agent, user_id, text, msg_id,
                     source=None):
        """Wake an idle caller by running a fresh agent loop with the
        result as the user input. `source` (if given) identifies the
        trigger so the agent loop can set ctx._turn_mode accordingly
        (e.g. agent_delegate → delegate_reply mode auto-tags the flush)."""
        try:
            from core import FlowFile
            body = json.dumps({
                "message": text,
                "conversation_id": conv_id,
                "msg_id": msg_id,
                "target_agent": caller_agent,
            })
            ff = FlowFile(body.encode("utf-8"))
            ff.set_attribute("http.auth.principal", user_id)
            ff.set_attribute("target_agent", caller_agent)
            # The caller already pre-persisted the nudge via writer
            # (see _deliver_to_caller / _deliver_shared_delegate) — tell
            # agent_streaming.py to skip its own pre-persist so we don't
            # write the same msg_id twice.
            ff.set_attribute("skip_pre_persist", "1")
            if source:
                ff.set_attribute("message_source", json.dumps(source))
            # Run in a thread so we don't block the completion callback
            # (which is running on the SubAgentExecutor's pool).
            import threading as _th
            _th.Thread(
                target=inst._execute_streaming,
                args=(ff,),
                daemon=True,
                name=f"wake-{caller_agent}",
            ).start()
        except Exception as e:
            logger.error("[bg-delegate] wake failed: %s", e)


