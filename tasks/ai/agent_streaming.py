"""AgentLoopTask mixin — AgentStreaming methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
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
    """Build a short narration string from tool_calls when the LLM didn't provide text.

    Groups calls by tool name and produces a one-liner like:
      "Generating 5 images and creating 2 files."
    """
    if not tool_calls:
        return ""

    _VERBS = {
        "generate_image": ("Generating", "image"),
        "filesystem": None,  # special handling
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
            # Group by action
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


def _narrate_tool_calls(tool_calls, ctx, bus, conversation_id,
                         agent_name, source):
    """Narration cascade for tool_calls without LLM text or thinking.

    1. Try narrator LLM service (small fast model) for contextual narration
    2. Fallback to static synthesis
    Publishes the narration as a SSE token event.
    """
    narration = ""

    # Try narrator LLM: dedicated service → current LLM → static synthesis
    narrator_svc_name = ctx.get("narrator_service", "")
    if narrator_svc_name:
        narration = _call_narrator(narrator_svc_name, tool_calls, ctx)
    if not narration:
        # Fallback: use current LLM client (same as summarizer pattern)
        narration = _call_narrator_with_client(ctx.get("client"), tool_calls)
    if not narration:
        narration = _synthesize_narration(tool_calls)

    if narration:
        bus.publish_event(conversation_id, "token", {
            "text": narration,
            "agent_name": agent_name,
            "source": source,
            "synthetic": True,
        })
    return narration


def _call_narrator(svc_name: str, tool_calls: List[LLMToolCall],
                    ctx: dict) -> str:
    """Call a small LLM to narrate tool_calls in one sentence."""
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        svc = GlobalServiceRegistry.get_instance().get_live_instance(svc_name)
        if not svc:
            return ""

        # Build prompt with enough context
        _KEY_LIMITS = {"command": 300, "code": 300, "prompt": 150}
        def _fmt(args):
            return ", ".join(f"{k}={str(v)[:_KEY_LIMITS.get(k, 50)]}" for k, v in args.items())
        tools_desc = "; ".join(
            f"{tc.name}({_fmt(tc.arguments)})"
            for tc in tool_calls[:5]
        )
        if len(tool_calls) > 5:
            tools_desc += f"; ... +{len(tool_calls) - 5} more"

        prompt = (
            f"The AI agent is about to call these tools: {tools_desc}\n"
            f"Write ONE short sentence (max 15 words) describing what it's doing. "
            f"Be specific about the actual action, not generic. "
            f"Write only the sentence, nothing else."
        )

        from core.llm_client import LLMMessage
        messages = [LLMMessage(role="user", content=prompt)]
        resp = svc.complete(messages, max_tokens=50, temperature=0.3)
        text = (resp.content or "").strip()
        if text and not text.endswith("\n"):
            text += "\n"
        return text
    except Exception as e:
        logger.debug("Narrator service '%s' failed: %s", svc_name, e)
        return ""


def _call_narrator_with_client(client, tool_calls: List[LLMToolCall]) -> str:
    """Use the current LLM client to narrate tool_calls in one sentence."""
    if not client:
        return ""
    try:
        # Build description with enough context for meaningful narration
        _KEY_LIMITS = {"command": 300, "code": 300, "prompt": 150}
        def _fmt_args(args):
            parts = []
            for k, v in args.items():
                limit = _KEY_LIMITS.get(k, 50)
                parts.append(f"{k}={str(v)[:limit]}")
            return ", ".join(parts)

        tools_desc = "; ".join(
            f"{tc.name}({_fmt_args(tc.arguments)})"
            for tc in tool_calls[:5]
        )
        if len(tool_calls) > 5:
            tools_desc += f"; ... +{len(tool_calls) - 5} more"

        prompt = (
            f"The AI agent is about to call these tools: {tools_desc}\n"
            f"Write ONE short sentence (max 15 words) describing what it's doing. "
            f"Be specific about the actual action, not generic. "
            f"Write only the sentence, nothing else."
        )

        from core.llm_client import LLMMessage
        messages = [LLMMessage(role="user", content=prompt)]
        resp = client.complete(messages, max_tokens=50, temperature=0.3)
        text = (resp.content or "").strip()
        if text and not text.endswith("\n"):
            text += "\n"
        return text
    except Exception as e:
        logger.debug("Narrator via current LLM failed: %s", e)
        return ""


logger = logging.getLogger(__name__)



from tasks.ai.agent_sync import AgentSyncMixin
from tasks.ai.agent_side_channels import AgentSideChannelsMixin


class AgentStreamingMixin(AgentSyncMixin, AgentSideChannelsMixin):
    """Streaming agent execution + sync + side channels."""


    def _execute_streaming(self, flowfile: FlowFile) -> List[FlowFile]:
        """Streaming mode: publish SSE events to ConversationEventBus.

        Returns immediately with a JSON ack.  The agent loop runs in a
        background thread, publishing events as it goes.
        """
        from core.conversation_event_bus import ConversationEventBus

        try:
            ctx = self._prepare_agent_context(flowfile)
        except ValueError as e:
            # Agent not found or other validation error — return error to client
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.status.code", "400")
            return [flowfile]
        conversation_id = ctx["conversation_id"]
        bus = ConversationEventBus.instance()

        # Wait for any context operation to complete before proceeding
        if not self._is_context_op_free(conversation_id):
            evt = self._get_context_op_event(conversation_id)
            if not evt.wait(timeout=60.0):
                flowfile.set_content(json.dumps({
                    "error": "Context operation in progress, try again",
                }).encode())
                flowfile.set_attribute("http.response.status", "409")
                return [flowfile]

        _target = ctx.get("_target_agent", "")

        # Publish "thinking" immediately
        bus.publish_event(conversation_id, "thinking", {
            "conversation_id": conversation_id,
            "agent_name": _target or "",
        })
        # Generation key — used for cancel/interrupt detection (NOT bumped here).
        # Only cancel_agent() and interrupt_agent() bump the generation counter.
        _gen_key = f"{conversation_id}:{_target}" if _target else conversation_id
        with self._conv_gen_lock:
            gen = self._conv_generation.get(_gen_key, 0)
        ctx["_generation"] = gen
        ctx["_gen_key"] = _gen_key

        # If an agent thread is physically running for this conversation,
        # don't spawn another one — just append the message and return ACK.
        # Check by scanning live threads — no stale flags, no ghosts.
        _already_active = any(
            t.is_alive() and t.name == f"agent-stream-{conversation_id}"
            for t in threading.enumerate()
        )
        if _already_active:
            logger.info(f"[agent:{conversation_id[:8]}] agent already active — "
                        f"queueing message instead of spawning new thread")
            # Store the message text in a shared queue so the active agent
            # thread can pick it up via _do_drain (not just store count)
            _queued_key = f"_queued_msgs:{conversation_id}"
            if not hasattr(self, '_pending_user_msgs'):
                self._pending_user_msgs = {}
            self._pending_user_msgs.setdefault(_queued_key, []).append(ctx)
            bus.publish_event(conversation_id, "message_queued", {
                "conversation_id": conversation_id,
            })
            from core.conversation_store import ConversationStore as _CSq
            msg_count = _CSq.instance().message_count(conversation_id)
            ack = json.dumps({
                "status": "queued",
                "conversation_id": conversation_id,
                "message_count": msg_count,
            }, ensure_ascii=False)
            flowfile.set_content(ack.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
            return [flowfile]

        # Mark conversation as active (prevents poller from picking it up)
        with self._active_lock:
            self._active_conversations[conversation_id] = self._active_conversations.get(conversation_id, 0) + 1
            self._user_active_conversations.add(conversation_id)

        # Set conversation status to active
        from core.conversation_store import ConversationStore
        ConversationStore.instance().set_status(conversation_id, "active")

        # Register active interaction for UI tracking
        _user_msgs = [m for m in ctx["messages"] if m.role == "user"]
        _msg_preview = ""
        if _user_msgs:
            _last = _user_msgs[-1].text_content if isinstance(_user_msgs[-1].content, list) else (_user_msgs[-1].content or "")
            _msg_preview = _last[:80]
        with self._interactions_lock:
            self._active_interactions[_gen_key] = {
                "agent_name": _target or ctx.get("active_agent_name", ""),
                "message_preview": _msg_preview,
                "started_at": time.time(),
                "iteration": 0,
                "last_tool": "",
                "status": "thinking",
                "conversation_id": conversation_id,
            }

        # Start agent loop in background thread
        thread = threading.Thread(
            target=self._streaming_agent_loop,
            args=(ctx, conversation_id, bus),
            daemon=True,
            name=f"agent-stream-{conversation_id}",
        )
        thread.start()

        # Start poller if configured and not already running
        poll_interval = int(self.config.get("poll_interval", 0))
        if poll_interval > 0 and not self._poller_started:
            self._poller_started = True
            poller = threading.Thread(
                target=self._poll_conversations,
                args=(poll_interval,),
                daemon=True,
                name="agent-poller",
            )
            poller.start()
            logger.info(f"Agent poller started (interval={poll_interval}s)")

        # Return immediately with ack (include message_count so client can sync)
        from core.conversation_store import ConversationStore as _CS
        msg_count = _CS.instance().message_count(conversation_id)
        ack = json.dumps({
            "status": "accepted",
            "conversation_id": conversation_id,
            "message_count": msg_count,
        }, ensure_ascii=False)
        flowfile.set_content(ack.encode("utf-8"))
        flowfile.set_attribute("agent.conversation_id", conversation_id)
        flowfile.set_attribute("agent.streaming", "true")

        return [flowfile]


    def _streaming_agent_loop(self, ctx: Dict, conversation_id: str,
                              bus) -> None:
        """Background thread: run agent loop, publish events to EventBus.

        Supports autonomous continuation: if the agent calls the
        ``schedule_continuation`` tool during a round, the loop will
        publish a ``done`` event with the intermediate response, wait
        the requested delay, then start a new round with the
        continuation plan injected as a system message.
        """
        try:
            self._streaming_agent_loop_inner(ctx, conversation_id, bus)
        except Exception as e:
            logger.error(f"[agent:{conversation_id[:8]}] streaming loop crashed: {e}",
                         exc_info=True)
            try:
                from core.conversation_event_bus import ConversationEventBus
                ConversationEventBus.instance().publish_event(
                    conversation_id, "error_event",
                    {"message": f"Agent loop crashed: {e}"},
                )
            except Exception:
                pass
        finally:
            # ALWAYS clean up — even if setup crashed before the inner try/finally
            self._decrement_active(conversation_id, ctx)


    def _streaming_agent_loop_inner(self, ctx: Dict, conversation_id: str,
                                     bus) -> None:
        """Inner streaming loop — wrapped by _streaming_agent_loop for guaranteed cleanup."""
        from core.conversation_event_bus import ConversationEventBus

        # For sub-conversations (task contexts), publish SSE events on the parent
        # so the web UI and PawCode (which listen on the parent) see them
        _sse_conv_id = conversation_id
        if "::task::" in conversation_id:
            _sse_conv_id = conversation_id.split("::task::")[0]
        # Wrap the bus to auto-redirect SSE events to parent
        _real_bus = bus

        class _RedirectBus:
            """Wrapper that redirects SSE events to the parent conversation."""
            def publish_event(self, cid, event_type, data):
                _real_bus.publish_event(_sse_conv_id, event_type, data)
            def subscriber_count(self, cid):
                return _real_bus.subscriber_count(_sse_conv_id)
            def __getattr__(self, name):
                return getattr(_real_bus, name)
        if _sse_conv_id != conversation_id:
            bus = _RedirectBus()

        my_generation = ctx.get("_generation", 0)
        gen_key = ctx.get("_gen_key", conversation_id)
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0
        def _update_interaction(**kwargs):
            """Update the active interaction tracker."""
            with self._interactions_lock:
                info = self._active_interactions.get(gen_key)
                if info:
                    info.update(kwargs)

        # Publish flowfile_in so the chat shows incoming activity
        _agent_name = ctx.get("active_agent_name", "")
        _is_poll = ctx.get("is_poll", False)
        _is_thought = ctx.get("is_random_thought", False)
        _scheduled_reasons = ctx.get("scheduled_reasons") or []
        _ff_reason = ""
        if _scheduled_reasons:
            _ff_reason = _scheduled_reasons[0] if len(_scheduled_reasons) == 1 else f"{len(_scheduled_reasons)} triggers"
        _ff_info = {"agent": _agent_name}
        if _ff_reason:
            _ff_info["reason"] = _ff_reason
        if _is_poll:
            _ff_info["type"] = "poll"
        if _is_thought:
            _ff_info["type"] = "thought"
        if not _is_poll or _ff_reason:
            # Don't publish for routine empty polls (no reason = nothing interesting)
            bus.publish_event(conversation_id, "flowfile_in", _ff_info)

        tools_called: List[str] = []

        client = ctx["client"]
        registry = ctx["registry"]
        tool_defs = ctx["tool_defs"]
        messages = ctx["messages"]  # LLM working context (may be compacted)
        model = ctx["model"]
        use_conv_store = ctx["use_conv_store"]
        conv_ttl = ctx["conv_ttl"]
        channel = ctx.get("channel", "")

        # Track message count for new-message checkpoint
        if use_conv_store and conversation_id:
            try:
                ctx["_last_known_msg_count"] = ConversationStore.instance().message_count(
                    conversation_id)
            except Exception:
                ctx["_last_known_msg_count"] = 0

        # Apply per-agent model override
        if use_conv_store and conversation_id:
            from core.conversation_store import ConversationStore
            _agent_n = ctx.get("active_agent_name") or ""
            _mo = ConversationStore.instance().get_extra(conversation_id, f"model_override:{_agent_n}")
            if _mo:
                model = _mo

        # Track new messages added during this run for append-only persistence.
        # The canonical conversation history lives in the ConversationStore and
        # is only extended — never overwritten — by this thread.
        new_messages: List[LLMMessage] = []
        # The user message was already appended to `messages` by _prepare_agent_context.
        # Record it as a new message so it gets persisted.
        base_count = ctx.get("_base_message_count", 0)
        if len(messages) > base_count:
            new_messages.extend(messages[base_count:])

        max_rounds = int(ctx.get("max_rounds", 1))
        iteration = 0
        final_model = ""
        finish_reason = ""
        response_content = ""
        _need_more_retried = False  # guards heuristic tool-mention retry (once per response)

        user_id = ctx.get("user_id", "")

        # Source metadata for identity tracking
        _agent_name = ctx.get("active_agent_name", "")
        _agent_svc = ctx.get("active_llm_service", "")

        # Set thread-local source agent on SpawnAgentsHandler
        from core.tool_registry import SpawnAgentsHandler as _SAH_stream
        for _h in registry.list_tools():
            if isinstance(_h, _SAH_stream):
                _h.set_source_agent(_agent_name or "", _agent_svc)
                break
        # LLM client metadata for traceability
        _client_provider = getattr(client, "provider", "")
        _client_base_url = getattr(client, "base_url", "")

        # Resolve model from client (always available, unlike final_model which needs a response)
        _client_model = getattr(client, "default_model", "") or ""

        def _agent_source():
            import re as _re
            return {
                "type": "agent",
                "name": _agent_name or "",
                "llm_service": _agent_svc or "",
                "provider": _client_provider or "",
                "model": _client_model,
                "base_url": _re.sub(r'(key|token|secret)=[^&]+', r'\1=***', _client_base_url) if _client_base_url else "",
            }

        _strip_echo_prefix = self._strip_echo_prefix

        def _append(msg: LLMMessage):
            """Append a message to both the LLM context and the new-messages list."""
            messages.append(msg)
            new_messages.append(msg)

        def _flush_new():
            """Persist new messages: transcript (clean) + agent context (full).

            Conversation transcript: only user messages + assistant text responses.
            Agent context: everything including tool calls and tool results.
            Binary/base64 data is never persisted — images are deflated first.
            """
            nonlocal new_messages
            if not (use_conv_store and conversation_id and new_messages):
                return
            from core.conversation_store import ConversationStore
            # Deflate images on a COPY for persistence — originals stay
            # intact so the LLM can still see fresh images in this iteration
            import copy as _cp
            _persist_msgs = _cp.deepcopy(new_messages)
            self._deflate_image_messages(_persist_msgs)

            # Full serialization for agent context (all messages)
            all_serialized = self._serialize_messages(_persist_msgs, channel=channel)

            # Transcript: only conversation messages (user + assistant text, no tool plumbing)
            transcript_msgs = [
                m for m in _persist_msgs
                if m.role in ("user", "assistant") and not getattr(m, "tool_calls", None)
                and m.role != "tool"
            ]
            transcript_serialized = self._serialize_messages(transcript_msgs, channel=channel) if transcript_msgs else []

            store = ConversationStore.instance()

            # 1. Conversation transcript — clean, lightweight
            if transcript_serialized:
                store.append_messages(
                    conversation_id, transcript_serialized,
                    ttl=conv_ttl, user_id=user_id,
                )

            # 2. Agent context — full working context (always, not just when diverged)
            _flush_agent = ctx.get("active_agent_name") or ""
            store.append_to_agent_context(conversation_id, _flush_agent, all_serialized)
            # Mark context as diverged since transcript and context now differ
            ctx["_context_diverged"] = True

            # For sub-conversations (tasks), append transcript to parent
            if "::task::" in conversation_id and transcript_serialized:
                _parent_cid = conversation_id.split("::task::")[0]
                store.append_messages(_parent_cid, transcript_serialized,
                                       ttl=conv_ttl, user_id=user_id)
            # Update known count so the finally-block pending check
            # doesn't mistake our own flushed messages for new user input
            ctx["_last_known_msg_count"] = store.message_count(conversation_id)
            new_messages = []

        # Persist the user message immediately so it's never lost
        _flush_new()

        # Consecutive tool call limiter
        _consecutive_tool_s: Dict[str, int] = {}
        _max_consec_s = ctx.get("max_consecutive_tool_calls", 100)

        def _do_drain():
            """Drain pending user messages into context from two sources:
            1. Executor input queue (FlowFiles from different conversations)
            2. Internal queue (messages from "agent already active" path)
            """
            # Source 1: executor queue
            if hasattr(self, '_drain_pending') and self._drain_pending:
                try:
                    for _pff in self._drain_pending():
                        _pbody = _pff.get_content()
                        if isinstance(_pbody, bytes):
                            _pbody = _pbody.decode("utf-8", errors="replace")
                        _ptext = None
                        _is_action = False
                        try:
                            _pjson = json.loads(_pbody)
                            if isinstance(_pjson, dict):
                                _is_action = bool(_pjson.get("action"))
                                if not _is_action:
                                    _pconv = _pjson.get("conversation_id")
                                    if _pconv and _pconv != conversation_id:
                                        continue
                                    _ptext = _pjson.get("message", "")
                        except (json.JSONDecodeError, ValueError):
                            pass
                        if _is_action:
                            # Execute action immediately (UI requests: list_resources, etc.)
                            try:
                                result = self._handle_action(_pff)
                                if result:
                                    # Send response back to HTTP client
                                    for _rff in result:
                                        _respond_http(_rff)
                            except Exception as _ae:
                                logger.debug(f"Inline action failed: {_ae}")
                        elif _ptext and _ptext.strip():
                            _inject_user_msg(_ptext, _pff.get_attribute("http.auth.principal"))
                            _respond_http(_pff)
                except Exception as _e:
                    logger.debug(f"Queue drain failed: {_e}")

            # Source 2: internal "already active" queue
            _queued_key = f"_queued_msgs:{conversation_id}"
            if hasattr(self, '_pending_user_msgs') and _queued_key in self._pending_user_msgs:
                _queued = self._pending_user_msgs.pop(_queued_key, [])
                for _qctx in _queued:
                    _qmsgs = _qctx.get("messages", [])
                    # Last message should be the user message
                    for _qm in reversed(_qmsgs):
                        if _qm.role == "user":
                            _text = _qm.content if isinstance(_qm.content, str) else str(_qm.content)
                            _inject_user_msg(_text, _qctx.get("user_id", ""))
                            break

        def _inject_user_msg(text, uid_attr):
            """Inject a user message into the LLM context."""
            _uid = uid_attr or user_id
            _append(LLMMessage(
                role="user", content=text,
                source={"type": "user", "name": _uid},
            ))
            logger.info(f"[agent:{conversation_id[:8]}] injected pending "
                        f"user message: {text[:80]!r}")

        def _respond_http(ff, body=None, status=200):
            """Send HTTP response for a drained FlowFile."""
            _req_id = ff.get_attribute("http.request.id")
            if not _req_id:
                return
            if body is None:
                body = ff.get_content() if ff.get_attribute("http.response.status") else \
                    json.dumps({"status": "accepted",
                                "conversation_id": conversation_id}).encode()
            if isinstance(body, str):
                body = body.encode("utf-8")
            _status = int(ff.get_attribute("http.response.status") or status)
            _ct = ff.get_attribute("http.response.header.Content-Type") or "application/json"
            try:
                from services.http_listener_service import _instances
                for _port, _svc in _instances.items():
                    if _svc._server and _req_id in _svc._server._pending_requests:
                        _svc.complete_response(_req_id, _status, {"Content-Type": _ct}, body)
                        break
            except Exception:
                pass

        _fatal_error = False
        try:
            for current_round in range(1, max_rounds + 1):
                # Track continuation requests for this round
                continuation_plan = None
                continuation_delay = 3

                while iteration < ctx["max_iterations"]:
                    # Check cancellation at the very start of each iteration
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    _do_drain()

                    # Also check conversation store for messages from other channels
                    if use_conv_store and conversation_id and iteration > 1:
                        try:
                            _cs_check = ConversationStore.instance()
                            _current_count = _cs_check.message_count(conversation_id)
                            _known_count = ctx.get("_last_known_msg_count", 0)
                            if _current_count > _known_count:
                                _page = _cs_check.load_page(
                                    conversation_id,
                                    limit=_current_count - _known_count,
                                    offset=_known_count,
                                )
                                _tail = _page["messages"] if _page else []
                                _new_user = [
                                    m for m in (_tail or [])
                                    if isinstance(m, dict) and m.get("role") == "user"
                                    and not (isinstance(m.get("content"), str)
                                             and m["content"].startswith("[System:"))
                                ]
                                if _new_user:
                                    for _nu in _new_user:
                                        messages.append(LLMMessage(
                                            role="user",
                                            content=_nu.get("content", ""),
                                            source=_nu.get("source"),
                                        ))
                                    logger.info(
                                        f"[agent:{conversation_id[:8]}] injected "
                                        f"{len(_new_user)} store user message(s)")
                                ctx["_last_known_msg_count"] = _current_count
                        except Exception as _chk_err:
                            logger.debug(f"Message checkpoint failed: {_chk_err}")

                    iteration += 1

                    # During poll first iteration, suppress streaming to avoid
                    # showing [NO_PENDING_WORK] in the UI. If tool calls happen,
                    # poll_silent flips off and subsequent iterations stream normally.
                    poll_silent = ctx.get("is_poll", False) and iteration == 1

                    # Notify client that LLM is being called
                    logger.info(f"[agent:{conversation_id[:8]}] round {current_round}/{max_rounds}, "
                                f"iteration {iteration}/{ctx['max_iterations']}, "
                                f"messages={len(messages)}, tools_called={len(tools_called)}")
                    # Always publish iteration_status (even during poll_silent)
                    bus.publish_event(conversation_id, "iteration_status", {
                        "agent_name": _agent_name or "",
                        "iteration": iteration,
                        "max_iterations": ctx["max_iterations"],
                        "round": current_round,
                        "max_rounds": max_rounds,
                        "tools_called": tools_called[-3:],
                        "total_tools": len(tools_called),
                    })
                    if not poll_silent:
                        bus.publish_event(conversation_id, "thinking", {
                            "iteration": iteration,
                            "round": current_round,
                            "agent_name": _agent_name or "",
                        })

                    # Use streaming LLM call with token callback
                    token_parts: List[str] = []
                    last_token_time = time.time()

                    def on_token(text: str):
                        nonlocal last_token_time
                        if not self._is_current_generation(gen_key, my_generation):
                            raise AgentCancelled()
                        last_token_time = time.time()
                        token_parts.append(text)
                        if not poll_silent:
                            bus.publish_event(conversation_id, "token", {
                                "text": text,
                                "agent_name": _agent_name or "",
                                "source": _agent_source(),
                            })

                    def on_thinking(text: str):
                        if not self._is_current_generation(gen_key, my_generation):
                            raise AgentCancelled()
                        if not poll_silent:
                            bus.publish_event(conversation_id, "thinking_content", {
                                "text": text,
                                "agent_name": _agent_name or "",
                            })

                    # Heartbeat thread (suppressed during silent poll)
                    heartbeat_stop = threading.Event()

                    def heartbeat():
                        while not heartbeat_stop.wait(5.0):
                            if poll_silent:
                                continue
                            elapsed = int(time.time() - last_token_time)
                            bus.publish_event(conversation_id, "thinking", {
                                "iteration": iteration,
                                "round": current_round,
                                "waiting_seconds": elapsed,
                                "agent_name": _agent_name or "",
                            })

                    hb_thread = threading.Thread(target=heartbeat, daemon=True)
                    hb_thread.start()

                    # Compact context if approaching token limit.
                    # Always compact — even during tool chains.  The limit is
                    # the limit; violating it means a 400 from the API.
                    _summ = ctx.get("summarizer", (None, 0))
                    if _summ[0]:
                        compact_client = _summ[0]
                    else:
                        compact_client = ctx.get("default_client") or client
                    _pre_compact_len = len(messages)
                    llm_context = self._compact_if_needed(
                        list(messages), compact_client,
                        ctx.get("max_context_size", 64000),
                        ctx.get("context_compact_threshold", 0.8),
                        ctx.get("context_keep_recent", 6),
                        conversation_id=conversation_id,
                        agent_name=_agent_name or "",
                        tool_defs=ctx.get("tool_defs"),
                        chars_per_token=ctx.get("chars_per_token", 0),
                    )
                    # If compaction happened, mark context as diverged so
                    # _flush_new() appends subsequent messages to the agent
                    # context (not just to the canonical messages).
                    if len(llm_context) < _pre_compact_len:
                        ctx["_context_diverged"] = True

                    # Inject identity prefixes so LLM knows who said what
                    _id_nicks = ctx.get("_nicknames") or {}
                    llm_context = self._inject_identity(llm_context, _id_nicks)
                    llm_context = self._apply_identity_suffix(llm_context, ctx.get("_identity_suffix", ""))

                    # Inject token budget awareness (like Claude Code)
                    _max_ctx = ctx.get("max_context_size", 200000)
                    _est_used = self._estimate_tokens(
                        llm_context, tool_defs=tool_defs,
                        chars_per_token=ctx.get("chars_per_token", 0))
                    _remaining = max(0, _max_ctx - _est_used)
                    if llm_context and llm_context[0].role == "system":
                        _budget_note = f"\n\n[Context: ~{_est_used} of {_max_ctx} tokens used, ~{_remaining} remaining]"
                        llm_context[0] = LLMMessage(
                            role="system",
                            content=(llm_context[0].content or "") + _budget_note,
                        )

                    # Check cancellation before LLM call
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    # Check interrupt — force synthesis instead of continuing
                    if self._check_interrupt(gen_key):
                        logger.info(f"[agent:{conversation_id[:8]}] interrupted — forcing synthesis")
                        _append(LLMMessage(
                            role="user",
                            content=(
                                "[System: The user has requested an immediate response. "
                                "Stop all tool usage. Summarize your progress so far and "
                                "provide your best answer with the information you have "
                                "gathered. Mention what you were still working on so the "
                                "user can ask you to continue if needed.]"
                            ),
                        ))
                        bus.publish_event(conversation_id, "thinking", {
                            "iteration": iteration, "round": "interrupt",
                            "agent_name": _agent_name or "",
                        })
                        interrupt_resp = client.complete_stream(
                            messages=self._compact_if_needed(
                                list(messages), compact_client,
                                ctx.get("max_context_size", 64000), 0.6,
                                ctx.get("context_keep_recent", 6),
                            ),
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=None,  # No tools — just answer
                            callback=on_token,
                        )
                        _append(LLMMessage(
                            role="assistant", content=interrupt_resp.content,
                            source=_agent_source(),
                        ))
                        response_content = interrupt_resp.content
                        total_tokens_in += interrupt_resp.tokens_in
                        total_tokens_out += interrupt_resp.tokens_out
                        final_model = interrupt_resp.model
                        _flush_new()
                        # Break out of both while and for loops
                        raise _InterruptComplete()

                    # Hard guard: verify context fits before sending to LLM
                    _max_ctx = ctx.get("max_context_size", 64000)
                    _pre_send_est = self._estimate_tokens(
                        llm_context, tool_defs=ctx.get("tool_defs"),
                        chars_per_token=ctx.get("chars_per_token", 0))
                    print(
                        f"[COMPACT-GUARD] pre-send: "
                        f"{_pre_send_est} est. tokens, {len(llm_context)} msgs, "
                        f"max={_max_ctx}, cpt={ctx.get('chars_per_token', 0):.2f}",
                        flush=True)
                    if _pre_send_est > _max_ctx:
                        print(
                            f"[COMPACT-GUARD] STILL OVER LIMIT "
                            f"({_pre_send_est} > {_max_ctx}), force-fitting...",
                            flush=True)
                        llm_context = self._force_fit_context(
                            llm_context, _max_ctx,
                            chars_per_token=ctx.get("chars_per_token", 0),
                            tool_defs=ctx.get("tool_defs"),
                        )
                        _post_fit = self._estimate_tokens(
                            llm_context, tool_defs=ctx.get("tool_defs"),
                            chars_per_token=ctx.get("chars_per_token", 0))
                        print(f"[COMPACT-GUARD] after force-fit: "
                              f"{_post_fit} est. tokens, {len(llm_context)} msgs",
                              flush=True)

                    _thinking_budget = ctx.get("thinking_budget", 0)
                    try:
                        response = client.complete_stream(
                            messages=llm_context,
                            model=model or None,
                            temperature=ctx["temperature"],
                            max_tokens=ctx["max_tokens"],
                            tools=tool_defs if tool_defs else None,
                            callback=on_token,
                            thinking_budget=_thinking_budget,
                            thinking_callback=on_thinking if _thinking_budget > 0 else None,
                        )
                    except AgentCancelled:
                        raise
                    except Exception as llm_err:
                        err_str = str(llm_err)
                        # Detect context overflow — force aggressive compaction and retry once
                        if "exceed_context_size" in err_str or "n_prompt_tokens" in err_str:
                            logger.warning(f"[agent:{conversation_id[:8]}] Context overflow detected, "
                                           f"forcing aggressive compaction and retrying...")
                            bus.publish_event(conversation_id, "thinking", {
                                "iteration": iteration, "detail": "compacting context...",
                                "agent_name": _agent_name or "",
                            })
                            llm_context = self._compact_if_needed(
                                llm_context, compact_client,
                                ctx.get("max_context_size", 64000),
                                0.5,  # aggressive threshold
                                ctx.get("context_keep_recent", 6),
                                conversation_id=conversation_id,
                                tool_defs=ctx.get("tool_defs"),
                                chars_per_token=ctx.get("chars_per_token", 0),
                            )
                            try:
                                heartbeat_stop.clear()
                                hb_thread = threading.Thread(target=heartbeat, daemon=True)
                                hb_thread.start()
                                response = client.complete_stream(
                                    messages=llm_context,
                                    model=model or None,
                                    temperature=ctx["temperature"],
                                    max_tokens=ctx["max_tokens"],
                                    tools=tool_defs if tool_defs else None,
                                    callback=on_token,
                                    thinking_budget=_thinking_budget,
                                    thinking_callback=on_thinking if _thinking_budget > 0 else None,
                                )
                            except Exception as retry_err:
                                logger.error(f"LLM retry also failed (iter {iteration}): {retry_err}")
                                bus.publish_event(conversation_id, "error_event", {
                                    "message": f"LLM call failed after compaction: {retry_err}",
                                })
                                response_content = f"Error: {retry_err}"
                                _fatal_error = True
                                break
                            finally:
                                heartbeat_stop.set()
                                hb_thread.join(timeout=1)
                        else:
                            logger.error(f"LLM call failed (iter {iteration}): {llm_err}")
                            bus.publish_event(conversation_id, "error_event", {
                                "message": f"LLM call failed: {llm_err}",
                            })
                            response_content = f"Error: {llm_err}"
                            _fatal_error = True
                            break
                    finally:
                        heartbeat_stop.set()
                        hb_thread.join(timeout=1)

                    # Check cancellation immediately after LLM call returns
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    total_tokens_in += response.tokens_in
                    total_tokens_out += response.tokens_out
                    final_model = response.model
                    finish_reason = response.finish_reason

                    # Deflate images: LLM has seen them, replace base64 with refs
                    self._deflate_image_messages(messages)

                    # Calibrate chars_per_token from actual usage
                    # Use _estimate_tokens(cpt=1) to get raw char count (same formula)
                    if response.tokens_in > 0:
                        _cal_chars = self._estimate_tokens(
                            llm_context, tool_defs=tool_defs, chars_per_token=1.0)
                        _svc_id = ctx.get("active_llm_service") or ""
                        self._calibrate_cpt(_svc_id, _cal_chars, response.tokens_in)
                        ctx["chars_per_token"] = self._get_cpt(
                            _svc_id, ctx.get("chars_per_token", 0))

                    _rc = response.content or ""
                    _rtc = len(response.tool_calls) if response.tool_calls else 0
                    logger.info(f"[agent:{conversation_id[:8]}] LLM responded: "
                                f"tokens_in={response.tokens_in}, tokens_out={response.tokens_out}, "
                                f"tool_calls={_rtc}, "
                                f"finish={finish_reason}, content_len={len(_rc)}")
                    if _rtc > 0 and _rc:
                        logger.info(f"[agent:{conversation_id[:8]}] narration with tools: {_rc[:200]!r}")
                    elif _rtc > 0 and not _rc:
                        logger.info(f"[agent:{conversation_id[:8]}] NO narration — tools called without text")

                    if not response.tool_calls:
                        action, msgs, final, _need_more_retried = self._handle_response_no_tools(
                            response.content or "", _client_provider, tool_defs,
                            _need_more_retried, source=_agent_source(),
                        )
                        for _m in msgs:
                            _append(_m)
                        if action == "break":
                            response_content = final
                            _flush_new()
                            break
                        continue

                    # Tool calls
                    _need_more_retried = False  # reset on successful tool_call
                    _append(LLMMessage(
                        role="assistant", content=response.content,
                        tool_calls=response.tool_calls,
                        source=_agent_source(),
                    ))

                    # If poll was silent but LLM made tool calls → real work detected
                    # Emit thinking event to wake up the UI
                    if poll_silent and response.tool_calls:
                        poll_silent = False
                        bus.publish_event(conversation_id, "thinking", {
                            "iteration": iteration, "round": current_round,
                            "agent_name": _agent_name or "",
                        })

                    # Narration cascade: ensure the user sees what the agent
                    # is about to do before tool_calls execute.
                    # 1. LLM provided text → already streamed via on_token
                    # 2. LLM provided thinking → already streamed via on_thinking
                    # 3. Neither → try narrator LLM service for contextual one-liner
                    # 4. No narrator → static synthesis from tool_call names
                    if not _rc and not (response.thinking or "") and response.tool_calls:
                        _narr = _narrate_tool_calls(
                            response.tool_calls, ctx, bus, conversation_id,
                            _agent_name or "", _agent_source(),
                        )

                    # Publish all tool_call events upfront
                    _sub_count = bus.subscriber_count(conversation_id)
                    for tc in response.tool_calls:
                        tools_called.append(tc.name)
                        logger.info(f"[agent:{conversation_id[:8]}] publishing tool_call SSE: "
                                    f"tool={tc.name}, subscribers={_sub_count}")
                        bus.publish_event(conversation_id, "tool_call", {
                            "tool": tc.name, "arguments": tc.arguments,
                            "agent_name": _agent_name or "",
                            "llm_service": _agent_svc or "",
                        })
                    _update_interaction(
                        iteration=iteration, last_tool=response.tool_calls[-1].name,
                        status="tool_call",
                    )

                    # Execute tools with consecutive-call limiting
                    results_ordered = self._execute_tool_calls(
                        response.tool_calls, registry, _consecutive_tool_s,
                        _max_consec_s, parallel=True,
                        agent_name=_agent_name or "",
                        agent_svc=_agent_svc or "",
                        conversation_id=conversation_id,
                        user_id=ctx.get("user_id", ""),
                    )

                    # Process results in original order
                    for tc, result_text in results_ordered:
                        if tc.name == "schedule_continuation":
                            continuation_plan = tc.arguments.get("plan", "Continue working")
                            continuation_delay = int(tc.arguments.get("delay_seconds", 3))
                        _append(LLMMessage(role="tool", content=result_text, tool_call_id=tc.id))
                        _result_preview = result_text if tc.name == "spawn_agents" else (result_text if isinstance(result_text, str) else str(result_text[0].get("text", "") if result_text else ""))
                        # Keep more text for diffs (filesystem edit results)
                        _preview_limit = 5000 if (tc.name == "filesystem" and isinstance(_result_preview, str) and any(p in _result_preview for p in ("replacement(s):", "Edited ", "Written "))) else 2000
                        _result_preview = _result_preview[:_preview_limit]
                        # Strip TOOL OUTPUT wrapper for display
                        if isinstance(_result_preview, str) and _result_preview.startswith("[TOOL OUTPUT"):
                            _fnl = _result_preview.find("\n")
                            if _fnl >= 0:
                                _result_preview = _result_preview[_fnl + 1:]
                            if _result_preview.endswith("[/TOOL OUTPUT]"):
                                _result_preview = _result_preview[:-len("[/TOOL OUTPUT]")].rstrip("\n")
                        bus.publish_event(conversation_id, "tool_result", {
                            "tool": tc.name, "result": _result_preview,
                            "agent_name": _agent_name or "",
                            "llm_service": _agent_svc or "",
                        })

                    bus.publish_event(conversation_id, "iteration_status", {
                        "agent_name": _agent_name or "",
                        "iteration": iteration,
                        "max_iterations": ctx["max_iterations"],
                        "round": current_round,
                            "max_rounds": max_rounds,
                            "tools_called": tools_called[-3:],
                            "total_tools": len(tools_called),
                        })

                    # Drain pending user messages after tool batch
                    _do_drain()

                    # Compact old tool chains to reduce context size
                    # (keeps last 6 messages intact, compacts older chains)
                    if len(messages) > 20:
                        messages[:] = self._compact_tool_chains(messages, keep_recent=6)

                    # Check cancellation after tool execution
                    if not self._is_current_generation(gen_key, my_generation):
                        raise AgentCancelled()

                    # Flush tool calls + results to disk after each iteration
                    _flush_new()
                else:
                    # Max iterations reached — force synthesis
                    logger.warning("Agent reached max iterations (%d), forcing synthesis",
                                   ctx["max_iterations"])
                    bus.publish_event(conversation_id, "thinking", {
                        "iteration": iteration + 1, "round": current_round,
                        "agent_name": _agent_name or "",
                    })
                    _pre = len(messages)
                    content, ti, to, fm = self._force_synthesis(
                        messages, client, ctx,
                        prompt=(
                            "[System: You have reached the maximum number of tool calls. "
                            "You MUST now provide your final response to the user. "
                            "Synthesize all the information you gathered from your tool calls "
                            "and present a clear, comprehensive answer. Do NOT call any more tools.]"
                        ),
                        compact_client=compact_client,
                        use_streaming=True,
                        token_callback=lambda text: bus.publish_event(
                            conversation_id, "token", {
                                "text": text,
                                "agent_name": _agent_name or "",
                                "source": _agent_source(),
                            }),
                        tools_called=tools_called, compact_threshold=1.0,
                        conversation_id=conversation_id,
                    )
                    new_messages.extend(messages[_pre:])
                    response_content = content
                    total_tokens_in += ti
                    total_tokens_out += to
                    if fm:
                        final_model = fm

                # Flush any remaining new messages to the canonical history
                _flush_new()

                # Fatal LLM error — exit all rounds
                if _fatal_error:
                    break

                # Check if continuation was requested
                if continuation_plan and current_round < max_rounds:
                    # Publish intermediate done so the UI shows the current response
                    bus.publish_event(conversation_id, "done", self._build_done_event(
                        conversation_id, response_content, _agent_name,
                        final_model or _client_model, _client_provider,
                        total_tokens_in, total_tokens_out, tools_called,
                        iteration, start_time, source=_agent_source(),
                        continuing=True,
                    ))

                    logger.info(f"[agent:{conversation_id[:8]}] continuation scheduled: "
                                f"plan='{continuation_plan}', delay={continuation_delay}s, "
                                f"next_round={current_round + 1}/{max_rounds}")

                    # Wait before continuing
                    time.sleep(continuation_delay)

                    # Inject continuation as a system message
                    _append(LLMMessage(
                        role="user",
                        content=(
                            f"[System: Automatic continuation — round {current_round + 1}]\n"
                            f"Continue with your plan: {continuation_plan}\n"
                            f"Build on your previous findings. When done, provide a final synthesis. "
                            f"If you still have more work, call schedule_continuation again."
                        ),
                    ))

                    # Reset response_content for next round
                    response_content = ""
                    continue
                else:
                    # No continuation — we're done
                    break

            # If the agent produced no final text, force a synthesis
            if not response_content:
                logger.warning(f"[agent:{conversation_id[:8]}] empty response — forcing synthesis")
                bus.publish_event(conversation_id, "thinking", {
                    "iteration": iteration + 1, "round": "synthesis",
                    "agent_name": _agent_name or "",
                })
                _pre = len(messages)
                content, ti, to, fm = self._force_synthesis(
                    messages, client, ctx,
                    prompt=(
                        "[System: You did not provide a response to the user. "
                        "You MUST respond now. Synthesize any information you have and present "
                        "a clear answer. Do NOT call any tools.]"
                    ),
                    compact_client=compact_client,
                    use_streaming=True,
                    token_callback=lambda text: bus.publish_event(
                        conversation_id, "token", {
                            "text": text,
                            "agent_name": _agent_name or "",
                            "source": _agent_source(),
                        }),
                    tools_called=tools_called,
                    conversation_id=conversation_id,
                )
                new_messages.extend(messages[_pre:])
                response_content = content
                total_tokens_in += ti
                total_tokens_out += to
                if fm:
                    final_model = fm
                _flush_new()

            # Handle [NO_PENDING_WORK] / [RECHECK_IN:...] tags
            if "[NO_PENDING_WORK]" in (response_content or ""):
                import re as _re

                # Random thoughts must ALWAYS produce a response — reject NO_PENDING_WORK
                if _is_thought:
                    stripped_thought = _re.sub(r'\s*\[NO_PENDING_WORK\]', '', response_content or "")
                    stripped_thought = _re.sub(r'\s*\[RECHECK_IN:\d+\]', '', stripped_thought).strip()
                    if stripped_thought:
                        # Has real content — use it
                        response_content = stripped_thought
                    else:
                        # Empty — discard silently, next random thought will fire
                        logger.info(f"[agent:{conversation_id[:8]}] random thought returned "
                                    f"NO_PENDING_WORK — discarding (next thought will fire)")
                        bus.publish_event(conversation_id, "discard", {
                            "agent_name": _agent_name or "",
                        })
                        new_messages.clear()
                        return
                    # Skip the cooldown/recheck logic for thoughts
                else:

                    recheck_match = _re.search(r'\[RECHECK_IN:(\d+)\]', response_content or "")
                    default_recheck = int(self.config.get("poll_recheck_delay", 7200))
                    recheck_delay = int(recheck_match.group(1)) if recheck_match else default_recheck

                    # Strip tags to see if there's real content underneath
                    stripped = _re.sub(r'\s*\[NO_PENDING_WORK\]', '', response_content)
                    stripped = _re.sub(r'\s*\[RECHECK_IN:\d+\]', '', stripped)
                    stripped = _re.sub(r'\[System:[^\]]*\]', '', stripped)
                    stripped = stripped.strip()

                    # Set cooldown (in-memory) AND persistent schedule
                    from core.poll_scheduler import PollScheduler
                    _recheck_agent = ctx.get("active_agent_name") or ""
                    user_id = ctx.get("user_id", "")
                    PollScheduler.instance().schedule_delay(
                        conversation_id, recheck_delay, user_id=user_id,
                        key=f"{conversation_id}::recheck::{_recheck_agent}",
                        reason=f"[scheduled:{_recheck_agent}] RECHECK_IN",
                    )

                    if not stripped:
                        # Pure poll check-in with nothing to say — discard entirely
                        logger.info(f"[agent:{conversation_id[:8]}] poll check-in: no pending work, "
                                    f"recheck in {recheck_delay}s (discarded)")
                        bus.publish_event(conversation_id, "discard", {
                            "agent_name": _agent_name or "",
                        })
                        new_messages.clear()
                        # Mark conversation idle — agent has no pending work
                        ConversationStore.instance().set_status(conversation_id, "idle")
                        return
                    else:
                        # Real content + tags — keep the content, strip the tags
                        logger.info(f"[agent:{conversation_id[:8]}] response with NO_PENDING_WORK tag, "
                                    f"keeping {len(stripped)} chars, recheck in {recheck_delay}s")
                        response_content = stripped
                        # Also strip from the persisted assistant message
                        if new_messages:
                            last_assistant = None
                            for msg in reversed(new_messages):
                                if msg.role == "assistant":
                                    last_assistant = msg
                                    break
                            if last_assistant and "[NO_PENDING_WORK]" in (last_assistant.content or ""):
                                last_assistant.content = stripped
                        _flush_new()

            # Publish final done event
            logger.info(f"[agent:{conversation_id[:8]}] done: response_len={len(response_content or '')}, "
                        f"tools={tools_called}")
            bus.publish_event(conversation_id, "done", self._build_done_event(
                conversation_id, response_content, _agent_name,
                final_model or _client_model, _client_provider,
                total_tokens_in, total_tokens_out, tools_called,
                iteration, start_time, source=_agent_source(),
            ))

            # Track token usage
            self._track_tokens(
                ctx.get("user_id", "anonymous"),
                total_tokens_in, total_tokens_out,
                model=final_model or _client_model,
                agent_name=_agent_name or "",
                llm_service=_agent_svc or "",
            )

            # Always set idle — follow-ups are handled by PollScheduler
            from core.conversation_store import ConversationStore as _CS
            _agent_name = ctx.get("active_agent_name") or ""
            _CS.instance().set_status(conversation_id, "idle")

        except _InterruptComplete:
            logger.info(f"[agent:{conversation_id[:8]}] interrupt synthesis done")
            bus.publish_event(conversation_id, "done", self._build_done_event(
                conversation_id, response_content, _agent_name,
                final_model or _client_model, _client_provider,
                total_tokens_in, total_tokens_out, tools_called,
                iteration, start_time, source=_agent_source(),
                interrupted=True,
            ))
            # Set idle — the user stopped this agent intentionally.
            # Don't set "active" which would trigger the poller to relance.
            from core.conversation_store import ConversationStore as _CSi
            _CSi.instance().set_status(conversation_id, "idle")
        except AgentCancelled:
            logger.info(f"[agent:{conversation_id[:8]}] cancelled — stopping gracefully")
            # Flush any partial messages accumulated so far
            _flush_new()
            # cancel_agent() already published the "cancelled" event and set status
        except Exception as e:
            logger.error(f"Streaming agent loop error: {e}", exc_info=True)
            # Flush any partial messages before reporting error
            _flush_new()
            bus.publish_event(conversation_id, "error_event", {
                "message": str(e),
                "conversation_id": conversation_id,
            })
        finally:
            # Note: _decrement_active is called by the outer wrapper
            # (_streaming_agent_loop) to guarantee cleanup even on setup crash.

            # Check if new USER messages arrived while we were finishing up.
            # Only schedule wake-up if there are actual user messages to process.
            if use_conv_store and conversation_id and not ctx.get("is_poll"):
                try:
                    _cs_final = ConversationStore.instance()
                    _final_count = _cs_final.message_count(conversation_id)
                    _known_final = ctx.get("_last_known_msg_count", 0)
                    if _final_count > _known_final:
                        # Check if any of the new messages are actually from users
                        _page = _cs_final.load_page(
                            conversation_id,
                            limit=_final_count - _known_final,
                            offset=_known_final,
                        )
                        _pending_user = [
                            m for m in (_page["messages"] if _page else [])
                            if isinstance(m, dict) and m.get("role") == "user"
                            and not (isinstance(m.get("content"), str)
                                     and m["content"].startswith("[System:"))
                        ]
                        if _pending_user:
                            from core.poll_scheduler import PollScheduler
                            _agent_n = ctx.get("active_agent_name") or ""
                            PollScheduler.instance().schedule_delay(
                                conversation_id, 3,
                                key=f"{conversation_id}::pending_msg",
                                reason=f"[pending_message] {len(_pending_user)} user message(s) ({_agent_n})",
                                user_id=ctx.get("user_id", ""),
                            )
                            logger.info(f"[agent:{conversation_id[:8]}] {len(_pending_user)} "
                                        f"pending user message(s) — scheduled wake-up")
                except Exception:
                    pass

            # Auto-reschedule random thought if still enabled
            # BUT NOT if the agent was cancelled (generation is stale)
            _was_cancelled = not self._is_current_generation(gen_key, my_generation)
            if ctx.get("is_random_thought") and not _was_cancelled:
                try:
                    from core.conversation_store import ConversationStore as _CSrt
                    from core.poll_scheduler import PollScheduler as _PSrt
                    import random as _rng_rt
                    # Extract ALL agent names from scheduled reasons (not just first)
                    _rt_reasons = ctx.get("_scheduled_reasons", [])
                    _rt_agents = set()
                    for _rr in _rt_reasons:
                        if "[random_thought]" in _rr and "(" in _rr:
                            _rt_agents.add(_rr.rsplit("(", 1)[-1].rstrip(")"))
                    if not _rt_agents:
                        _rt_agents = {ctx.get("active_agent_name") or "assistant"}
                    from core.conversation_event_bus import ConversationEventBus as _EBrt
                    _rt_bus = _EBrt.instance()
                    _rt_store = _CSrt.instance()
                    for _rt_agent in _rt_agents:
                        _rt_agent_key = _rt_agent.lower()
                        _rt_extra_key = f"random_thought::{_rt_agent_key}"
                        _rt_config = _rt_store.get_extra(conversation_id, _rt_extra_key)
                        if _rt_config and _rt_config.get("enabled"):
                            _rt_delay = _rng_rt.randint(
                                _rt_config["min_interval"], _rt_config["max_interval"],
                            )
                            _PSrt.instance().schedule_delay(
                                conversation_id, _rt_delay,
                                key=f"{conversation_id}::thought::{_rt_agent_key}",
                                reason=f"[random_thought] spontaneous thought ({_rt_agent})",
                                user_id=ctx.get("user_id", ""),
                            )
                            _rt_bus.publish_event(conversation_id, "thought_scheduled", {
                                "agent": _rt_agent,
                                "delay": _rt_delay,
                                "frequency": _rt_config.get("frequency", ""),
                            })
                    # Set idle after thought
                    _rt_store.set_status(conversation_id, "idle")
                except Exception as _rt_err:
                    logger.warning(f"[agent] Failed to reschedule thought: {_rt_err}")

            # Auto-reschedule active tasks if agent didn't call complete_task
            if not _was_cancelled:
                try:
                    from core.conversation_store import ConversationStore as _CSat
                    from core.poll_scheduler import PollScheduler as _PSat
                    _at_store = _CSat.instance()
                    _at_sched = _PSat.instance()
                    _at_all = _at_store.get_extra(conversation_id, "agent_tasks") or {}
                    _at_agent = ctx.get("active_agent_name") or ""
                    for _at_tid, _at_task in _at_all.items():
                        if not isinstance(_at_task, dict):
                            continue
                        if _at_task.get("agent") != _at_agent:
                            continue
                        if _at_task.get("status") != "active":
                            continue
                        # Auto-fail if max iterations reached
                        _at_iters = _at_task.get("iterations_done", 0)
                        _at_max = _at_task.get("max_iterations", 50)
                        if _at_iters >= _at_max:
                            _at_task["status"] = "failed"
                            _at_task["last_result"] = f"Auto-failed: {_at_iters}/{_at_max} iterations"
                            _at_all[_at_tid] = _at_task
                            _at_store.set_extra(conversation_id, "agent_tasks", _at_all)
                            logger.info(f"[task] Auto-failed {_at_tid}: max iterations reached")
                            continue
                        _at_key = f"{conversation_id}::task::{_at_tid}"
                        if _at_sched.get(_at_key):
                            continue  # already scheduled
                        from core.tool_registry import AssignTaskHandler as _ATH
                        _at_delay = _ATH._get_task_delay(_at_task)
                        _at_real_agent = _at_task.get("agent", _at_agent)
                        _at_sched.schedule_delay(
                            conversation_id, _at_delay,
                            key=_at_key,
                            reason=f"[agent_task:{_at_tid}] auto-reschedule ({_at_real_agent})",
                            user_id=ctx.get("user_id", ""),
                        )
                        logger.info(f"[task] Auto-rescheduled {_at_tid} for {_at_agent} "
                                    f"(agent didn't call complete_task)")
                except Exception as _at_err:
                    logger.warning(f"[agent] Failed to auto-reschedule tasks: {_at_err}")

