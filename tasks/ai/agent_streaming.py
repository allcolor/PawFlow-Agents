"""AgentLoopTask mixin — streaming agent execution.

Thread spawning, ACK return, narration functions.
The actual loop logic is in agent_core.py (_run_agent_loop).
"""
import json
import logging
import threading
import time
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import (
    LLMClient, LLMMessage, LLMResponse, LLMToolDefinition,
    LLMToolCall, LLMToolResult, LLMClientError,
)
from core.tool_registry import ToolRegistry, create_default_registry, load_agent_tools
from tasks.ai.agent_exceptions import AgentCancelled, _InterruptComplete


def _synthesize_narration(tool_calls: List[LLMToolCall]) -> str:
    """Build a short narration string from tool_calls when the LLM didn't provide text."""
    if not tool_calls:
        return ""
    _VERBS = {
        "generate_image": ("Generating", "image"),
        "filesystem": None,
        "web_search": ("Searching the web", None),
        "scrape_url": ("Scraping", "page"),
        "execute_script": ("Running", "script"),
        "create_file": ("Creating", "file"),
        "read_file": ("Reading", "file"),
        "schedule_continuation": ("Scheduling continuation", None),
        "spawn_agents": ("Spawning", "agent"),
    }
    counts = {}
    for tc in tool_calls:
        counts[tc.name] = counts.get(tc.name, 0) + 1
    parts = []
    for name, count in counts.items():
        if name == "filesystem":
            actions = {}
            for tc in tool_calls:
                if tc.name == "filesystem":
                    a = tc.arguments.get("action", "filesystem op")
                    actions[a] = actions.get(a, 0) + 1
            for a, c in actions.items():
                label = a.replace("_", " ")
                parts.append(f"{label} ({c})" if c > 1 else label)
        elif name in _VERBS:
            v = _VERBS[name]
            if v is None:
                continue
            verb, noun = v
            if noun and count > 1:
                parts.append(f"{verb} {count} {noun}s")
            else:
                parts.append(verb)
        else:
            parts.append(f"{name} ({count})" if count > 1 else name)
    if not parts:
        return ""
    return ", ".join(parts) + ".\n"


def _narrate_tool_calls(tool_calls, ctx, bus, conversation_id, agent_name, source,
                        msg_id=""):
    """Narration cascade: narrator LLM → current LLM → static synthesis."""
    narration = ""
    narrator_svc_name = ctx.get("narrator_service", "")
    if narrator_svc_name:
        narration = _call_narrator(narrator_svc_name, tool_calls, ctx)
    if not narration:
        narration = _call_narrator_with_client(ctx.get("client"), tool_calls, ctx)
    if not narration:
        narration = _synthesize_narration(tool_calls)
    if narration:
        bus.publish_event(conversation_id, "narration", {
            "text": narration, "agent_name": agent_name,
            "msg_id": msg_id,
            "source": source,
        })
    return narration


def _track_narrator(resp, ctx):
    """Track narrator token usage (best-effort)."""
    if not resp or resp.tokens_in <= 0:
        return
    try:
        from core.token_tracker import TokenTracker
        TokenTracker.instance().track(
            ctx.get("user_id", "system"), resp.tokens_in, resp.tokens_out,
            model=resp.model or "", agent_name=ctx.get("active_agent_name", "narrator"),
            llm_service=ctx.get("narrator_service", "narrator"))
        TokenTracker.instance().flush()
    except Exception:
        pass


def _call_narrator(svc_name: str, tool_calls, ctx) -> str:
    """Call a small LLM to narrate tool_calls in one sentence."""
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        svc = GlobalServiceRegistry.get_instance().get_live_instance(svc_name)
        if not svc:
            return ""
        logging.getLogger(__name__).debug(f"[narrator] using service '{svc_name}'")
        _KEY_LIMITS = {"command": 300, "code": 300, "prompt": 150}
        def _fmt(args):
            return ", ".join(f"{k}={str(v)[:_KEY_LIMITS.get(k, 50)]}" for k, v in args.items())
        tools_desc = "; ".join(f"{tc.name}({_fmt(tc.arguments)})" for tc in tool_calls[:5])
        if len(tool_calls) > 5:
            tools_desc += f"; ... +{len(tool_calls) - 5} more"
        prompt = (
            f"The AI agent is about to call these tools: {tools_desc}\n"
            f"Write ONE short sentence (max 15 words) describing what it's doing. "
            f"Be specific about the actual action, not generic. Write only the sentence.")
        resp = svc.complete([LLMMessage(role="user", content=prompt)], max_tokens=50, temperature=0.3)
        _track_narrator(resp, ctx)
        text = (resp.content or "").strip()
        return text + "\n" if text and not text.endswith("\n") else text
    except Exception as e:
        logging.getLogger(__name__).debug("Narrator service '%s' failed: %s", svc_name, e)
        return ""


def _call_narrator_with_client(client, tool_calls, ctx=None) -> str:
    """Use the current LLM client to narrate tool_calls in one sentence."""
    if not client:
        return ""
    try:
        _KEY_LIMITS = {"command": 300, "code": 300, "prompt": 150}
        def _fmt(args):
            return ", ".join(f"{k}={str(v)[:_KEY_LIMITS.get(k, 50)]}" for k, v in args.items())
        tools_desc = "; ".join(f"{tc.name}({_fmt(tc.arguments)})" for tc in tool_calls[:5])
        if len(tool_calls) > 5:
            tools_desc += f"; ... +{len(tool_calls) - 5} more"
        prompt = (
            f"The AI agent is about to call these tools: {tools_desc}\n"
            f"Write ONE short sentence (max 15 words) describing what it's doing. "
            f"Be specific about the actual action, not generic. Write only the sentence.")
        resp = client.complete([LLMMessage(role="user", content=prompt)], max_tokens=50, temperature=0.3)
        if ctx:
            _track_narrator(resp, ctx)
        text = (resp.content or "").strip()
        return text + "\n" if text and not text.endswith("\n") else text
    except Exception as e:
        logging.getLogger(__name__).debug("Narrator via current LLM failed: %s", e)
        return ""


logger = logging.getLogger(__name__)

from tasks.ai.agent_sync import AgentSyncMixin
from tasks.ai.agent_side_channels import AgentSideChannelsMixin


class AgentStreamingMixin(AgentSyncMixin, AgentSideChannelsMixin):
    """Streaming agent execution + sync + side channels."""

    def _execute_streaming(self, flowfile: FlowFile) -> List[FlowFile]:
        """Streaming mode: returns ACK immediately, runs loop in background thread."""
        from core.conversation_event_bus import ConversationEventBus
        try:
            ctx = self._prepare_agent_context(flowfile)
        except ValueError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.status.code", "400")
            return [flowfile]
        conversation_id = ctx["conversation_id"]
        bus = ConversationEventBus.instance()

        if not self._is_context_op_free(conversation_id):
            evt = self._get_context_op_event(conversation_id)
            if not evt.wait(timeout=60.0):
                flowfile.set_content(json.dumps({"error": "Context operation in progress"}).encode())
                flowfile.set_attribute("http.response.status", "409")
                return [flowfile]

        _target = ctx.get("_target_agent", "")
        bus.publish_event(conversation_id, "thinking", {
            "conversation_id": conversation_id, "agent_name": _target or "",
        })
        _gen_key = f"{conversation_id}:{_target}" if _target else conversation_id
        with self._conv_gen_lock:
            gen = self._conv_generation.get(_gen_key, 0)
        ctx["_generation"] = gen
        ctx["_gen_key"] = _gen_key

        # If agent thread already running, queue the message
        _already_active = any(
            t.is_alive() and t.name == f"agent-stream-{conversation_id}"
            for t in threading.enumerate())
        if _already_active:
            logger.info(f"[agent:{conversation_id[:8]}] already active — queueing")
            _queued_key = f"_queued_msgs:{conversation_id}"
            if not hasattr(self, '_pending_user_msgs'):
                self._pending_user_msgs = {}
            self._pending_user_msgs.setdefault(_queued_key, []).append(ctx)
            bus.publish_event(conversation_id, "message_queued", {"conversation_id": conversation_id})
            from core.conversation_store import ConversationStore as _CSq
            ack = json.dumps({"status": "queued", "conversation_id": conversation_id,
                              "message_count": _CSq.instance().message_count(conversation_id)})
            flowfile.set_content(ack.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
            return [flowfile]

        # Mark active
        with self._active_lock:
            self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1
            self._user_active_conversations.add(conversation_id)
        from core.conversation_store import ConversationStore
        ConversationStore.instance().set_status(conversation_id, "active")

        # Register interaction
        _user_msgs = [m for m in ctx["messages"] if m.role == "user"]
        _msg_preview = ""
        if _user_msgs:
            _last = _user_msgs[-1].content if isinstance(_user_msgs[-1].content, str) else ""
            _msg_preview = _last[:80]
        with self._interactions_lock:
            self._active_interactions[_gen_key] = {
                "agent_name": _target or ctx.get("active_agent_name", ""),
                "message_preview": _msg_preview, "started_at": time.time(),
                "iteration": 0, "last_tool": "", "status": "thinking",
                "conversation_id": conversation_id,
            }

        thread = threading.Thread(
            target=self._streaming_agent_loop,
            args=(ctx, conversation_id, bus),
            daemon=True, name=f"agent-stream-{conversation_id}")
        thread.start()

        # Start poller if configured
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            threading.Thread(
                target=self._poll_conversations, args=(poll_interval,),
                daemon=True, name="agent-poller").start()
            logger.info(f"Agent poller started (interval={poll_interval}s)")

        from core.conversation_store import ConversationStore as _CS
        ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                          "message_count": _CS.instance().message_count(conversation_id)})
        flowfile.set_content(ack.encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", conversation_id)
        flowfile.set_attribute("agent.streaming", "true")
        return [flowfile]

    def _streaming_agent_loop(self, ctx: Dict, conversation_id: str, bus) -> None:
        """Background thread wrapper — guaranteed cleanup via finally."""
        try:
            self._streaming_agent_loop_inner(ctx, conversation_id, bus)
        except Exception as e:
            logger.error(f"[agent:{conversation_id[:8]}] streaming loop crashed: {e}", exc_info=True)
            try:
                bus.publish_event(conversation_id, "error_event", {"message": f"Agent loop crashed: {e}"})
            except Exception:
                pass
        finally:
            self._decrement_active(conversation_id, ctx)

    def _streaming_agent_loop_inner(self, ctx: Dict, conversation_id: str, bus) -> None:
        """Create StreamEmitter, delegate to _run_agent_loop, handle finally cleanup."""
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from tasks.ai.agent_emitter import StreamEmitter

        # Redirect SSE for sub-conversations
        _sse_conv_id = ctx.get("_sse_conversation_id") or conversation_id
        _real_bus = bus
        if _sse_conv_id != conversation_id:
            class _RedirectBus:
                def publish_event(self, _cid, event_type, data=None):
                    _real_bus.publish_event(_sse_conv_id, event_type, data)
                def subscriber_count(self, _cid):
                    return _real_bus.subscriber_count(_sse_conv_id)
                def __getattr__(self, name):
                    return getattr(_real_bus, name)
            bus = _RedirectBus()

        gen_key = ctx.get("_gen_key", conversation_id)
        my_generation = ctx.get("_generation", 0)

        emitter = StreamEmitter(conversation_id, bus, ctx, self, gen_key, my_generation)

        try:
            result = self._run_agent_loop(ctx, emitter)

            # Set idle status
            ConversationStore.instance().set_status(conversation_id, "idle")

        except Exception:
            # Errors already handled by _run_agent_loop + emitter
            try:
                ConversationStore.instance().set_status(conversation_id, "idle")
            except Exception:
                pass
            raise
        finally:
            use_conv_store = ctx.get("use_conv_store", False)

            # Check for pending user messages — but NOT if we were interrupted
            # (the interrupt synthesis already handled the response)
            _was_interrupted = not self._is_current_generation(gen_key, my_generation)
            if use_conv_store and conversation_id and not ctx.get("is_poll") and not _was_interrupted:
                try:
                    _cs = ConversationStore.instance()
                    _final_count = _cs.message_count(conversation_id)
                    _known = ctx.get("_last_known_msg_count", 0)
                    if _final_count > _known:
                        _page = _cs.load_page(conversation_id,
                                              limit=_final_count - _known, offset=_known)
                        _pending = [
                            m for m in (_page["messages"] if _page else [])
                            if isinstance(m, dict) and m.get("role") == "user"
                            and not (isinstance(m.get("content"), str)
                                     and m["content"].startswith("[System:"))]
                        if _pending:
                            from core.poll_scheduler import PollScheduler
                            PollScheduler.instance().schedule_delay(
                                conversation_id, 3,
                                key=f"{conversation_id}::pending_msg",
                                reason=f"[pending_message] {len(_pending)} user message(s)",
                                user_id=ctx.get("user_id", ""))
                except Exception:
                    pass

            # Auto-reschedule random thoughts
            _was_cancelled = not self._is_current_generation(gen_key, my_generation)
            if ctx.get("is_random_thought") and not _was_cancelled:
                try:
                    from core.poll_scheduler import PollScheduler as _PS
                    import random as _rng
                    _reasons = ctx.get("_scheduled_reasons", [])
                    _agents = set()
                    for _r in _reasons:
                        if "[random_thought]" in _r and "(" in _r:
                            _agents.add(_r.rsplit("(", 1)[-1].rstrip(")"))
                    if not _agents:
                        _agents = {ctx.get("active_agent_name") or "assistant"}
                    _store = ConversationStore.instance()
                    for _ag in _agents:
                        _cfg = _store.get_extra(conversation_id, f"random_thought::{_ag.lower()}")
                        if _cfg and _cfg.get("enabled"):
                            _delay = _rng.randint(_cfg["min_interval"], _cfg["max_interval"])
                            _PS.instance().schedule_delay(
                                conversation_id, _delay,
                                key=f"{conversation_id}::thought::{_ag.lower()}",
                                reason=f"[random_thought] spontaneous thought ({_ag})",
                                user_id=ctx.get("user_id", ""))
                            bus.publish_event(conversation_id, "thought_scheduled", {
                                "agent": _ag, "delay": _delay,
                                "frequency": _cfg.get("frequency", "")})
                    _store.set_status(conversation_id, "idle")
                except Exception as e:
                    logger.warning(f"[agent] Failed to reschedule thought: {e}")

            # Auto-reschedule active tasks
            if not _was_cancelled:
                try:
                    _store = ConversationStore.instance()
                    from core.poll_scheduler import PollScheduler as _PS2
                    _all_tasks = _store.get_extra(conversation_id, "agent_tasks") or {}
                    _ag_name = ctx.get("active_agent_name") or ""
                    for _tid, _task in _all_tasks.items():
                        if not isinstance(_task, dict) or _task.get("agent") != _ag_name:
                            continue
                        if _task.get("status") != "active":
                            continue
                        _iters = _task.get("iterations_done", 0)
                        _max = _task.get("max_iterations", 50)
                        if _iters >= _max:
                            _task["status"] = "failed"
                            _task["last_result"] = f"Auto-failed: {_iters}/{_max} iterations"
                            _all_tasks[_tid] = _task
                            _store.set_extra(conversation_id, "agent_tasks", _all_tasks)
                            continue
                        _key = f"{conversation_id}::task::{_tid}"
                        if _PS2.instance().get(_key):
                            continue
                        from core.tool_registry import AssignTaskHandler as _ATH
                        _PS2.instance().schedule_delay(
                            conversation_id, _ATH._get_task_delay(_task),
                            key=_key,
                            reason=f"[agent_task:{_tid}] auto-reschedule ({_task.get('agent', _ag_name)})",
                            user_id=ctx.get("user_id", ""))
                except Exception as e:
                    logger.warning(f"[agent] Failed to auto-reschedule tasks: {e}")
