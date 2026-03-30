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
        "web_search": ("Searching the web", None),
        "scrape_url": ("Scraping", "page"),
        "execute_script": ("Running", "script"),
        "create_file": ("Creating", "file"),
        "schedule_continuation": ("Scheduling continuation", None),
        "spawn_agents": ("Spawning", "agent"),
        # Split filesystem tools — tool name IS the action
        "read": ("Reading", "file"),
        "write": ("Writing", "file"),
        "edit": ("Editing", "file"),
        "batch_edit": ("Batch editing", "file"),
        "apply_patch": ("Applying", "patch"),
        "find_replace": ("Find & replace", None),
        "delete": ("Deleting", "file"),
        "mkdir": ("Creating", "directory"),
        "stat": ("Checking", "file"),
        "exists": ("Checking existence", None),
        "list_dir": ("Listing", "directory"),
        "glob": ("Searching", "file"),
        "grep": ("Searching file contents", None),
        "bash": ("Running", "command"),
        "notebook_edit": ("Editing", "notebook"),
        "copy": ("Copying", "file"),
    }
    counts = {}
    for tc in tool_calls:
        counts[tc.name] = counts.get(tc.name, 0) + 1
    parts = []
    for name, count in counts.items():
        if name in _VERBS:
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
    """Call narrator service if configured. No fallback — no narrator = silence."""
    narrator_svc_name = ctx.get("narrator_service", "")
    if not narrator_svc_name:
        return ""  # No narrator configured → tools execute silently
    narration = _call_narrator(narrator_svc_name, tool_calls, ctx)
    if narration:
        bus.publish_event(conversation_id, "narration", {
            "text": narration, "agent_name": agent_name,
            "msg_id": msg_id,
            "source": source,
        })
        # Persist in transcript (display-only — NOT in agent context)
        try:
            from core.conversation_writer import ConversationWriter
            import time as _t
            ConversationWriter.for_conversation(conversation_id).enqueue([{
                "role": "assistant",
                "content": narration,
                "source": {**(source or {}), "narrator": True},
                "msg_id": msg_id,
                "display_only": True,
                "timestamp": _t.time(),
            }])
        except Exception as _pe:
            logging.getLogger(__name__).debug(f"[narrator] persist failed: {_pe}")
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
    """Call a narrator LLM to describe what the agent is doing."""
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        svc = GlobalServiceRegistry.get_instance().get_live_instance(svc_name)
        if not svc:
            logging.getLogger(__name__).info(f"[narrator] service '{svc_name}' NOT FOUND")
            return ""
        logging.getLogger(__name__).info(f"[narrator] calling service '{svc_name}'")

        # Format tool calls with enough detail for meaningful narration
        _KEY_LIMITS = {"command": 500, "code": 500, "prompt": 300,
                       "content": 300, "path": 200, "query": 200}
        def _fmt(args):
            return ", ".join(f"{k}={str(v)[:_KEY_LIMITS.get(k, 80)]}"
                             for k, v in args.items())
        tools_desc = "\n".join(
            f"  - {tc.name}({_fmt(tc.arguments)})"
            for tc in tool_calls[:8])
        if len(tool_calls) > 8:
            tools_desc += f"\n  - ... +{len(tool_calls) - 8} more"

        # Give context: agent name + last user message
        agent_name = ctx.get("active_agent_name", "the agent")
        last_user_msg = ""
        for m in reversed(ctx.get("messages", [])):
            if m.role == "user":
                content = m.content if isinstance(m.content, str) else str(m.content)
                last_user_msg = content[:200]
                break

        prompt = (
            f"Agent '{agent_name}' is executing these tool calls:\n{tools_desc}\n\n"
            + (f"Context — the user asked: \"{last_user_msg}\"\n\n" if last_user_msg else "")
            + "Describe what the agent is doing in 1-2 short sentences. "
            "Be specific about the actual action and its purpose. "
            "Don't say 'the agent' — speak as if narrating: 'Reading the config file to check...'\n\n"
            "IMPORTANT: You MUST output a response. Even a single sentence is fine. "
            "Do NOT output nothing.")

        # Sync call with short timeout — narrator must not block the agent loop
        import concurrent.futures
        _NARRATOR_TIMEOUT = 4  # seconds
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(svc.complete,
                                 [LLMMessage(role="user", content=prompt)],
                                 None, 0.3, 150)
            try:
                resp = future.result(timeout=_NARRATOR_TIMEOUT)
            except concurrent.futures.TimeoutError:
                logging.getLogger(__name__).info("[narrator] timed out (>%ds), skipping", _NARRATOR_TIMEOUT)
                return ""
        _track_narrator(resp, ctx)
        text = (resp.content or "").strip()
        return text + "\n" if text and not text.endswith("\n") else text
    except Exception as e:
        logging.getLogger(__name__).warning("[narrator] service '%s' failed: %s", svc_name, e)
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
        """Streaming mode: returns ACK immediately, runs loop in background thread.

        _prepare_agent_context (which may compact) runs in the background
        thread, NOT here. This method returns in < 1s.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore

        # Parse body for conversation_id and user text (lightweight, no LLM)
        raw = flowfile.get_content().decode("utf-8", errors="replace")
        try:
            _body = json.loads(raw) if raw.strip().startswith("{") else {}
        except (json.JSONDecodeError, TypeError):
            _body = {}
        conversation_id = (
            _body.get("conversation_id")
            or flowfile.get_attribute("agent.conversation_id")
            or ""
        )
        _user_text = _body.get("message", "")
        _target = _body.get("target_agent", "")
        bus = ConversationEventBus.instance()

        # If agent thread already running, preempt or queue
        _already_active = any(
            t.is_alive() and t.name == f"agent-stream-{conversation_id}"
            for t in threading.enumerate())
        if _already_active:
            _active_client = getattr(self, '_active_claude_client', {}).get(conversation_id)
            if _active_client and hasattr(_active_client, 'send_user_message') and _user_text:
                _attachments = _body.get("attachments", [])
                if _active_client.send_user_message(_user_text, attachments=_attachments):
                    logger.info(f"[agent:{conversation_id[:8]}] preempt via claude-code stdin")
                    ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                                      "message_count": ConversationStore.instance().message_count(conversation_id)})
                    flowfile.set_content(ack.encode("utf-8"))
                    flowfile.set_attribute("agent.conversation_id", conversation_id)
                    return [flowfile]

            logger.info(f"[agent:{conversation_id[:8]}] already active — queueing")
            # Preserve the original user message before overwriting with ack
            flowfile.set_attribute("_queued_user_text", _user_text)
            _queued_key = f"_queued_msgs:{conversation_id}"
            if not hasattr(self, '_pending_user_msgs'):
                self._pending_user_msgs = {}
            self._pending_user_msgs.setdefault(_queued_key, []).append(flowfile)
            bus.publish_event(conversation_id, "message_queued", {"conversation_id": conversation_id})
            ack = json.dumps({"status": "queued", "conversation_id": conversation_id,
                              "message_count": ConversationStore.instance().message_count(conversation_id)})
            flowfile.set_content(ack.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
            return [flowfile]

        # Mark active
        with self._active_lock:
            self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1
            self._user_active_conversations.add(conversation_id)
        ConversationStore.instance().set_status(conversation_id, "active")

        if _target:
            bus.publish_event(conversation_id, "thinking", {
                "conversation_id": conversation_id, "agent_name": _target,
            })

        # Clone flowfile for background thread (main thread overwrites with ack)
        from core import FlowFile as _FF
        _bg_ff = _FF(content=flowfile.get_content(),
                      attributes=dict(flowfile.attributes))

        # Background thread: prepare context (may compact), then run agent loop
        def _bg_streaming():
            try:
                ctx = self._prepare_agent_context(_bg_ff)
            except Exception as e:
                logger.error("[agent:%s] prepare_context failed: %s",
                             conversation_id[:8], e, exc_info=True)
                # Resolve agent name for error events
                _err_agent = _target
                if not _err_agent:
                    try:
                        _ares = ConversationStore.instance().get_extra(
                            conversation_id, "active_resources") or {}
                        _err_agent = _ares.get("agent", "")
                    except Exception:
                        pass
                bus.publish_event(conversation_id, "compact_progress", {
                    "stage": "error", "error": str(e),
                })
                bus.publish_event(conversation_id, "error_event", {
                    "message": f"Context preparation failed: {e}",
                    "agent_name": _err_agent,
                })
                bus.publish_event(conversation_id, "done", {
                    "agent_name": _err_agent, "response": "",
                    "finish_reason": "error",
                })
                with self._active_lock:
                    self._active_conversations[conversation_id] = max(0,
                        self._active_conversations.get(conversation_id, 1) - 1)
                ConversationStore.instance().set_status(conversation_id, "idle")
                return

            _gen_key = f"{conversation_id}:{_target}" if _target else conversation_id
            with self._conv_gen_lock:
                gen = self._conv_generation.get(_gen_key, 0)
            ctx["_generation"] = gen
            ctx["_gen_key"] = _gen_key

            self._streaming_agent_loop(ctx, conversation_id, bus)

        thread = threading.Thread(
            target=_bg_streaming, daemon=True,
            name=f"agent-stream-{conversation_id}")
        thread.start()

        # Start poller if configured
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            threading.Thread(
                target=self._poll_conversations, args=(poll_interval,),
                daemon=True, name="agent-poller").start()
            logger.info(f"Agent poller started (interval={poll_interval}s)")

        ack = json.dumps({"status": "accepted", "conversation_id": conversation_id,
                          "message_count": ConversationStore.instance().message_count(conversation_id)})
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
                _crash_agent = ctx.get("active_agent_name", "") or ""
                bus.publish_event(conversation_id, "error_event", {
                    "message": f"Agent loop crashed: {e}",
                    "agent_name": _crash_agent,
                })
            except Exception:
                pass
        finally:
            # Cancel any bg tasks still running for this conversation
            try:
                import core.background_tool as _bg
                for t in _bg.list_tasks(conversation_id):
                    if t["status"] == "running":
                        _bg.cancel(t["tc_id"])
                        logger.info("[agent:%s] cancelled bg task %s on exit",
                                    conversation_id[:8], t["tc_id"])
                # Purge unclaimed results (agent won't pick them up)
                for t in _bg.list_tasks(conversation_id):
                    _bg.pop_completed(conversation_id, t["tc_id"])
            except Exception:
                pass
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

        _had_error = False
        try:
            result = self._run_agent_loop(ctx, emitter)
            _had_error = getattr(result, "finish_reason", "") == "error"

            # Set idle status
            ConversationStore.instance().set_status(conversation_id, "idle")

        except Exception:
            _had_error = True
            try:
                ConversationStore.instance().set_status(conversation_id, "idle")
            except Exception:
                pass
        finally:
            use_conv_store = ctx.get("use_conv_store", False)

            # Check for pending user messages — but NOT if interrupted or fatal error
            _was_interrupted = not self._is_current_generation(gen_key, my_generation)
            if use_conv_store and conversation_id and not ctx.get("is_poll") and not _was_interrupted and not _had_error:
                try:
                    # Flush writer — ensure all messages from this turn are on disk
                    from core.conversation_writer import ConversationWriter
                    try:
                        ConversationWriter.for_conversation(conversation_id).flush(timeout=5)
                    except Exception:
                        pass
                    _cs = ConversationStore.instance()
                    _final_count = _cs.message_count(conversation_id)
                    # Check: are there user messages AFTER the last assistant/tool message?
                    # Load the tail and check if the very last message(s) are from the user
                    # (meaning the agent didn't respond to them)
                    _tail = _cs.load_page(conversation_id, limit=5, offset=max(0, _final_count - 5))
                    _tail_msgs = (_tail["messages"] if _tail else []) if _tail else []
                    _pending = []
                    for m in reversed(_tail_msgs):
                        if not isinstance(m, dict):
                            continue
                        if m.get("role") == "user" and not (
                                isinstance(m.get("content"), str) and m["content"].startswith("[System:")):
                            _pending.append(m)
                        else:
                            break  # hit a non-user message → no more pending
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
            if ctx.get("is_random_thought") and not _was_cancelled and not _had_error:
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
            if not _was_cancelled and not _had_error:
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
