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

logger = logging.getLogger(__name__)



class AgentStreamingMixin:
    """Methods extracted from AgentLoopTask."""


    def _execute_sync(self, flowfile: FlowFile) -> List[FlowFile]:
        start_time = time.time()
        total_tokens_in = 0
        total_tokens_out = 0
        tools_called: List[str] = []

        ctx = self._prepare_agent_context(flowfile)
        client = ctx["client"]
        registry = ctx["registry"]
        tool_defs = ctx["tool_defs"]
        messages = ctx["messages"]
        model = ctx["model"]
        conversation_id = ctx["conversation_id"]
        use_conv_store = ctx["use_conv_store"]
        conv_ttl = ctx["conv_ttl"]
        conv_attr = ctx["conv_attr"]
        base_count = ctx.get("_base_message_count", 0)

        # Apply per-agent model override
        if use_conv_store and conversation_id:
            from core.conversation_store import ConversationStore
            _agent_n = ctx.get("active_agent_name") or ""
            _mo = ConversationStore.instance().get_extra(conversation_id, f"model_override:{_agent_n}")
            if _mo:
                model = _mo

        iteration = 0
        final_model = ""
        finish_reason = ""
        response_content = ""
        _need_more_retried_ns = False  # guards heuristic tool-mention retry
        _consecutive_tool: Dict[str, int] = {}  # tool_name → consecutive call count
        _max_consec = ctx.get("max_consecutive_tool_calls", 25)

        _client_provider_ns = getattr(client, "provider", "") or ""
        if not isinstance(_client_provider_ns, str):
            _client_provider_ns = ""

        while iteration < ctx["max_iterations"]:
            iteration += 1

            # Compact before every LLM call — the limit is the limit
            _pre_len_ns = len(messages)
            messages = self._compact_if_needed(
                messages, ctx.get("default_client") or client,
                ctx.get("max_context_size", 64000),
                ctx.get("context_compact_threshold", 0.8),
                ctx.get("context_keep_recent", 6),
                conversation_id=ctx.get("conversation_id", ""),
                agent_name=ctx.get("active_agent_name") or "",
                tool_defs=tool_defs,
                chars_per_token=ctx.get("chars_per_token", 0),
            )

            _id_nicks_ns = ctx.get("_nicknames") or {}
            _llm_msgs = self._inject_identity(messages, _id_nicks_ns)
            _llm_msgs = self._apply_identity_suffix(_llm_msgs, ctx.get("_identity_suffix", ""))

            response = client.complete(
                messages=_llm_msgs,
                model=model or None,
                temperature=ctx["temperature"],
                max_tokens=ctx["max_tokens"],
                tools=tool_defs if tool_defs else None,
                thinking_budget=ctx.get("thinking_budget", 0),
            )

            total_tokens_in += response.tokens_in
            total_tokens_out += response.tokens_out
            final_model = response.model
            finish_reason = response.finish_reason

            # Deflate images: LLM has seen them, replace base64 with references
            self._deflate_image_messages(messages)

            # Calibrate chars_per_token from actual usage (sync path)
            if response.tokens_in > 0:
                _cal_chars = sum(
                    len(m.content) if isinstance(m.content, str) else 0
                    for m in _llm_msgs
                )
                _svc_id = ctx.get("active_llm_service") or ""
                self._calibrate_cpt(_svc_id, _cal_chars, response.tokens_in)
                ctx["chars_per_token"] = self._get_cpt(
                    _svc_id, ctx.get("chars_per_token", 0))

            if not response.tool_calls:
                _source_ns = {"type": "agent", "name": ctx.get("active_agent_name") or ""}
                action, msgs, final, _need_more_retried_ns = self._handle_response_no_tools(
                    response.content or "", _client_provider_ns, tool_defs,
                    _need_more_retried_ns, source=_source_ns,
                )
                messages.extend(msgs)
                if action == "break":
                    response_content = final
                    break
                continue

            _need_more_retried_ns = False  # reset on successful tool_call
            _source_tc_ns = {"type": "agent", "name": ctx.get("active_agent_name") or ""}
            messages.append(LLMMessage(
                role="assistant", content=response.content,
                tool_calls=response.tool_calls,
                source=_source_tc_ns,
            ))

            results = self._execute_tool_calls(
                response.tool_calls, registry, _consecutive_tool, _max_consec,
                parallel=False,
                agent_name=ctx.get("active_agent_name") or "",
                agent_svc=ctx.get("active_llm_service", ""),
                conversation_id=ctx.get("conversation_id", ""),
                user_id=ctx.get("user_id", ""),
            )
            for tc, result_text in results:
                tools_called.append(tc.name)
                messages.append(LLMMessage(
                    role="tool", content=result_text, tool_call_id=tc.id,
                ))
        else:
            logger.warning("Agent reached max iterations (%d), forcing synthesis",
                           ctx["max_iterations"])
            content, ti, to, fm = self._force_synthesis(
                messages, client, ctx,
                prompt=(
                    "[System: You have reached the maximum number of tool calls. "
                    "You MUST now provide your final response to the user. "
                    "Synthesize all the information you gathered from your tool calls "
                    "and present a clear, comprehensive answer. Do NOT call any more tools.]"
                ),
                tools_called=tools_called, compact_threshold=1.0,
            )
            response_content = content
            total_tokens_in += ti
            total_tokens_out += to
            if fm:
                final_model = fm

        # If the agent produced no final text, force a synthesis
        if not response_content:
            logger.warning("[agent] empty response — forcing synthesis")
            content, ti, to, fm = self._force_synthesis(
                messages, client, ctx,
                prompt=(
                    "[System: You did not provide a response to the user. "
                    "You MUST respond now. Synthesize any information you have and present "
                    "a clear answer. Do NOT call any tools.]"
                ),
                tools_called=tools_called,
            )
            response_content = content
            total_tokens_in += ti
            total_tokens_out += to
            if fm:
                final_model = fm

        duration_ms = (time.time() - start_time) * 1000
        flowfile.set_attribute("agent.iterations", str(iteration))
        flowfile.set_attribute("agent.tools_called", ",".join(tools_called))
        flowfile.set_attribute("agent.model", final_model)
        flowfile.set_attribute("agent.tokens_in", str(total_tokens_in))
        flowfile.set_attribute("agent.tokens_out", str(total_tokens_out))
        flowfile.set_attribute("agent.duration_ms", f"{duration_ms:.1f}")
        flowfile.set_attribute("agent.finish_reason", finish_reason)

        # Track token usage
        _client_model = getattr(client, "default_model", "") or ""
        self._track_tokens(
            ctx.get("user_id", "anonymous"),
            total_tokens_in, total_tokens_out,
            model=final_model or _client_model,
            agent_name=ctx.get("active_agent_name", "") or "",
            llm_service=ctx.get("active_llm_service", ""),
        )

        if use_conv_store and conversation_id:
            from core.conversation_store import ConversationStore
            new_msgs = messages[base_count:]
            if new_msgs:
                ConversationStore.instance().append_messages(
                    conversation_id,
                    self._serialize_messages(new_msgs, channel=ctx.get("channel", "")),
                    ttl=conv_ttl, user_id=ctx.get("user_id", ""),
                )

        if conv_attr:
            flowfile.set_attribute(conv_attr, json.dumps(
                self._serialize_messages(messages, channel=ctx.get("channel", "")),
                ensure_ascii=False,
            ))

        if use_conv_store:
            _agent_name = ctx.get("active_agent_name", "")
            _llm_svc = ctx.get("active_llm_service", "")
            _client_prov = getattr(client, "provider", "") if client else ""
            if not isinstance(_client_prov, str):
                _client_prov = ""
            _client_burl = getattr(client, "base_url", "") if client else ""
            if not isinstance(_client_burl, str):
                _client_burl = ""
            _source = {"type": "agent", "name": _agent_name or ""}
            if _llm_svc:
                _source["llm_service"] = _llm_svc
            if _client_prov:
                _source["provider"] = _client_prov
            if _client_burl and isinstance(_client_burl, str):
                import re as _re2
                _source["base_url"] = _re2.sub(r'(key|token|secret)=[^&]+', r'\1=***', _client_burl)
            output = json.dumps({
                "response": response_content,
                "conversation_id": conversation_id,
                "model": final_model or _client_model,
                "provider": _client_prov,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "source": _source,
            }, ensure_ascii=False)
            flowfile.set_content(output.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
        else:
            flowfile.set_content(response_content.encode("utf-8"))

        return [flowfile]


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
            # Messages are already persisted in ConversationStore (done in execute())
            # Publish event so the running thread knows to check for new messages
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
            # Deflate images before any persistence
            self._deflate_image_messages(new_messages)

            # Full serialization for agent context (all messages)
            all_serialized = self._serialize_messages(new_messages, channel=channel)

            # Transcript: only conversation messages (user + assistant text, no tool plumbing)
            transcript_msgs = [
                m for m in new_messages
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
        _max_consec_s = ctx.get("max_consecutive_tool_calls", 25)

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

                    # Checkpoint: pick up new user messages appended while we were working
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
                                        f"{len(_new_user)} queued user message(s)")
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

                    logger.info(f"[agent:{conversation_id[:8]}] LLM responded: "
                                f"tokens_in={response.tokens_in}, tokens_out={response.tokens_out}, "
                                f"tool_calls={len(response.tool_calls) if response.tool_calls else 0}, "
                                f"finish={finish_reason}, content_len={len(response.content or '')}")

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
            from core.conversation_store import ConversationStore as _CSi
            _CSi.instance().set_status(conversation_id, "active")
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


    def _btw_query(self, conversation_id: str, agent_name: str,
                   question: str, user_id: str) -> None:
        """Side-channel query — separate LLM call, no tools.

        Loads a lightweight context (system prompt + last few messages),
        makes a single LLM call without tools, and publishes the response
        via SSE. Persists btw Q&A to conversation history with btw flag.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore

        bus = ConversationEventBus.instance()
        store = ConversationStore.instance()

        try:
            # 1. Resolve agent's system prompt + LLM client
            if not agent_name:
                # Fall back to active agent for this conversation
                active_res = store.get_extra(conversation_id, "active_resources") or {}
                agent_name = active_res.get("agent", "") or "assistant"
            rs = ResourceStore.instance()
            adef = rs.get_any("agent", agent_name, user_id)
            if not adef:
                bus.publish_event(conversation_id, "btw_done", {
                    "agent_name": agent_name,
                    "error": f"Agent '{agent_name}' not found",
                })
                return
            sys_prompt = adef["prompt"]
            llm_svc = adef.get("llm_service", "")
            if llm_svc and "${" in llm_svc:
                from core.expression import resolve_expression
                llm_svc = resolve_expression(llm_svc, owner=user_id)
                if "${" in llm_svc:
                    llm_svc = ""
            client = None
            if llm_svc:
                client, _ = self._resolve_llm_service(llm_svc, user_id)
            if not client:
                task_svc = self.config.get("llm_service", "default")
                if "${" in task_svc:
                    task_svc = "default"
                client, _ = self._resolve_llm_service(task_svc, user_id)

            if not client:
                bus.publish_event(conversation_id, "btw_done", {
                    "agent_name": agent_name,
                    "error": "No LLM service available",
                })
                return

            # 2. Build lightweight context: system + last N messages (truncated)
            raw = store.load(conversation_id) or []
            recent = self._deserialize_messages(raw[-6:]) if len(raw) > 6 else self._deserialize_messages(raw)
            # Truncate each message content to keep context small
            summary_parts = []
            for m in recent:
                content = m.content if isinstance(m.content, str) else str(m.content)
                role_label = m.role.upper()
                truncated = content[:200] + ("..." if len(content) > 200 else "")
                summary_parts.append(f"[{role_label}]: {truncated}")
            context_summary = "\n".join(summary_parts)

            # Inject identity into btw system prompt
            _btw_nicknames = store.get_extra(conversation_id, "agent_nicknames") or {}
            _btw_nick_key = agent_name.lower()
            _btw_nick = next((v for k, v in _btw_nicknames.items() if k.lower() == _btw_nick_key), None)
            if _btw_nick:
                _id_block = (
                    f"[IDENTITY] Your real agent id is \"{agent_name}\". "
                    f"The user has given you the nickname \"{_btw_nick}\". "
                    f"When other agents or tools refer to \"{agent_name}\" or "
                    f"\"{_btw_nick}\" (case-insensitive), they mean YOU.\n\n"
                )
            else:
                _id_block = f"[IDENTITY] Your agent id is \"{agent_name}\".\n\n"
            btw_system = (
                _id_block + sys_prompt + "\n\n"
                "[SIDE QUESTION: The user is asking a quick question while you are working. "
                "Answer briefly and concisely. Do NOT use any tools. "
                "This does not affect your current task.]"
            )
            btw_messages = [
                LLMMessage(role="system", content=btw_system),
                LLMMessage(role="user", content=(
                    f"[Brief context of our conversation:\n{context_summary}]\n\n"
                    f"Quick question: {question}"
                )),
            ]

            # 3. Single LLM call, no tools, stream tokens via SSE
            bus.publish_event(conversation_id, "btw_thinking", {
                "agent_name": agent_name,
            })

            def on_btw_token(text):
                bus.publish_event(conversation_id, "btw_token", {
                    "agent_name": agent_name, "text": text,
                })

            response = client.complete_stream(
                messages=btw_messages,
                tools=None,
                temperature=0.5,
                max_tokens=1024,
                callback=on_btw_token,
            )

            # 4. Persist btw Q&A in conversation history
            import time as _btw_time
            _btw_now = _btw_time.time()
            _btw_user_source = {"type": "user", "name": user_id or "anonymous",
                                "btw": True, "target_agent": agent_name}
            _btw_agent_source = {"type": "agent", "name": agent_name, "btw": True}
            store.append_messages(conversation_id, [
                {"role": "user", "content": f"[btw] {question}",
                 "source": _btw_user_source, "timestamp": _btw_now},
                {"role": "assistant", "content": response.content,
                 "source": _btw_agent_source, "timestamp": _btw_now},
            ])

            # 5. Publish done event
            bus.publish_event(conversation_id, "btw_done", {
                "agent_name": agent_name,
                "question": question,
                "response": response.content,
                "source": _btw_agent_source,
            })
            logger.info(f"[btw:{conversation_id[:8]}] {agent_name} answered "
                        f"({len(response.content)} chars)")

        except Exception as e:
            logger.error(f"[btw:{conversation_id[:8]}] error: {e}", exc_info=True)
            bus.publish_event(conversation_id, "btw_done", {
                "agent_name": agent_name,
                "error": str(e),
            })


    def _broadcast_agents(self, conversation_id: str, message: str,
                          user_id: str) -> None:
        """Send a message to ALL defined agents in parallel.

        Each response is published as an SSE 'agent_response' event,
        and a final 'broadcast_done' is sent when all are complete.
        """
        from core.conversation_event_bus import ConversationEventBus
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore
        from core.agent_executor import SubAgentExecutor, resolve_agent_task

        bus = ConversationEventBus.instance()

        try:
            rs = ResourceStore.instance()
            all_agents = rs.list_all("agent", user_id)
            if not all_agents:
                bus.publish_event(conversation_id, "error_event", {
                    "message": "No agents defined. Use /agent create first.",
                })
                return

            agent_names = [a["name"] for a in all_agents]
            all_targets = agent_names
            bus.publish_event(conversation_id, "thinking", {
                "detail": f"Broadcasting to {len(all_targets)} targets: {', '.join(all_targets)}",
            })

            # Resolve default LLM client
            task_llm_service = self.config.get("llm_service", "")
            if not task_llm_service or "${" in task_llm_service:
                task_llm_service = "default"
            client, _ = self._resolve_client(
                task_llm_service, user_id, resolve_expressions=False,
            )
            if not client:
                bus.publish_event(conversation_id, "error_event", {
                    "message": "No LLM service available for broadcast.",
                })
                return

            # Build tasks
            registry = self.get_tool_registry()
            self._configure_tool_handlers(registry)

            def _client_resolver(svc_id, uid):
                return self._resolve_llm_service(svc_id, uid)

            def _bc_on_event(event_type, data):
                try:
                    bus.publish_event(conversation_id, event_type, data)
                except Exception:
                    pass

            sub_executor = SubAgentExecutor(
                client, registry, max_workers=len(agent_names) + 1,
                client_resolver=_client_resolver,
                on_event=_bc_on_event,
            )

            tasks = []
            for name in all_targets:
                try:
                    task = resolve_agent_task(name, message, user_id)
                    tasks.append(task)
                except KeyError:
                    logger.warning("Broadcast: agent '%s' not found, skipping", name)

            if not tasks:
                bus.publish_event(conversation_id, "error_event", {
                    "message": "No valid agents to broadcast to.",
                })
                return

            # Spawn all agents in parallel
            results = sub_executor.spawn(tasks, wait=True)

            # Publish each result and persist in conversation
            cstore = ConversationStore.instance()
            for result in results:
                source = {
                    "type": "agent",
                    "name": result.agent_name,
                }
                content = result.response if result.status == "completed" else (
                    f"[Error: {result.error}]"
                )
                # Persist in conversation
                msg = LLMMessage(
                    role="assistant",
                    content=content,
                    source=source,
                )
                cstore.append_messages(
                    conversation_id,
                    self._serialize_messages([msg]),
                    user_id=user_id,
                )
                # Publish SSE event
                bus.publish_event(conversation_id, "agent_response", {
                    "agent_name": result.agent_name,
                    "response": content,
                    "source": source,
                    "status": result.status,
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "duration_ms": round(result.duration_ms, 1),
                })

            # Broadcast complete
            bus.publish_event(conversation_id, "broadcast_done", {
                "agent_count": len(results),
                "message_count": cstore.message_count(conversation_id),
            })

            sub_executor.shutdown()

        except Exception as e:
            logger.error("Broadcast error: %s", e, exc_info=True)
            bus.publish_event(conversation_id, "error_event", {
                "message": f"Broadcast failed: {e}",
            })

