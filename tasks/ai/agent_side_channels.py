"""AgentLoopTask mixin — AgentStreaming methods

Auto-extracted from tasks/ai/agent_loop.py.
All methods access self (AgentLoopTask instance).
"""
import logging


from core.llm_client import (
    LLMMessage,
)

logger = logging.getLogger(__name__)



class AgentStreamingMixin:
    """Methods extracted from AgentLoopTask."""



class AgentSideChannelsMixin:
    """BTW side-channel queries."""

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
                agent_name = active_res.get("agent", "")
            rs = ResourceStore.instance()
            adef = rs.get_any("agent", agent_name, user_id,
                              conversation_id=conversation_id)
            if not adef:
                bus.publish_event(conversation_id, "btw_done", {
                    "agent_name": agent_name,
                    "error": f"Agent '{agent_name}' not found",
                })
                return
            sys_prompt = adef["prompt"]
            client, _, _ = self._resolve_agent_client(
                agent_name, user_id, conversation_id)

            if not client:
                bus.publish_event(conversation_id, "btw_done", {
                    "agent_name": agent_name,
                    "error": "No LLM service available",
                })
                return

            # For CC providers: use a transient sub-conv (like tasks).
            # The CC session lives only for this btw call, then is destroyed.
            # _ephemeral_stream prevents btw from overwriting _claude_proc.
            # btw runs on its OWN cloned client — fully isolated from the
            # shared singleton. Each Claude Code stream has its own
            # container; the Python orchestration state must be per-
            # stream too. The previous _btw_lock that serialized concurrent
            # /btw calls is no longer needed (each call has its own state).
            _is_cc = hasattr(client, 'cancel_claude_code')
            _btw_conv_id = f"{conversation_id}::btw::{agent_name}"
            _btw_client = client
            if _is_cc and hasattr(client, 'clone_for_call'):
                _btw_client = client.clone_for_call()

            # 2. Build lightweight context: system + last N messages (truncated)
            raw = store.load(conversation_id) or []
            recent = self._deserialize_messages(raw[-6:], conversation_id=conversation_id) if len(raw) > 6 else self._deserialize_messages(raw, conversation_id=conversation_id)
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
                LLMMessage(role="system", content=btw_system,
                            conversation_id=conversation_id),
                LLMMessage(role="user", content=(
                    f"[Brief context of our conversation:\n{context_summary}]\n\n"
                    f"Quick question: {question}"
                ), conversation_id=conversation_id),
            ]

            # 3. Single LLM call, no tools. No per-token SSE (IMMUTABLE
            # RULE: stream block → LLMMessage → writer → SSE post-write).
            # The final `btw_done` event below is published from the
            # writer AFTER the assistant reply hits disk.
            bus.publish_event(conversation_id, "btw_thinking", {
                "agent_name": agent_name,
            })

            _btw_call_kwargs = {
                "call_user_id": user_id,
                "call_conversation_id": _btw_conv_id,
                "call_agent_name": agent_name,
                "call_event_cid": conversation_id,  # publish to parent conv
                "call_ephemeral_stream": True,
            }
            response = _btw_client.complete_stream(
                messages=btw_messages,
                tools=None,
                temperature=0.5,
                max_tokens=0,
                callback=None,
                **_btw_call_kwargs,
            )

            # 3b. Cleanup transient CC session (like task cleanup)
            if _is_cc:
                try:
                    store.invalidate_claude_sessions(_btw_conv_id)
                    store.delete(_btw_conv_id)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)

            # 4. Persist btw Q&A in conversation history
            import time as _btw_time
            _btw_now = _btw_time.time()
            _btw_user_source = {"type": "user", "name": user_id,
                                "btw": True, "target_agent": agent_name}
            _btw_agent_source = {"type": "agent", "name": agent_name, "btw": True}
            from core.conversation_writer import ConversationWriter
            from core.llm_client import stamp_message
            _btw_writer = ConversationWriter.for_conversation(conversation_id)
            _btw_writer.enqueue_message(
                stamp_message({"role": "user", "content": f"[btw] {question}",
                               "source": _btw_user_source}, conversation_id),
                agent_name=agent_name, user_id=user_id)
            # btw_done SSE fires AFTER the assistant reply hits disk
            # (visible ⇒ persisted invariant).
            _btw_done_sse = {
                "type": "btw_done",
                "data": {
                    "agent_name": agent_name,
                    "question": question,
                    "response": response.content,
                    "source": _btw_agent_source,
                },
            }
            _btw_writer.enqueue_message(
                stamp_message({"role": "assistant", "content": response.content,
                               "source": _btw_agent_source}, conversation_id),
                agent_name=agent_name, user_id=user_id,
                sse_events=[_btw_done_sse])
            logger.info(f"[btw:{conversation_id[:8]}] {agent_name} answered "
                        f"({len(response.content)} chars)")

        except Exception as e:
            logger.error(f"[btw:{conversation_id[:8]}] error: {e}", exc_info=True)
            # Cleanup CC state on error too
            if _is_cc:
                try:
                    store.invalidate_claude_sessions(_btw_conv_id)
                    store.delete(_btw_conv_id)
                except Exception:
                    logger.debug("exception suppressed", exc_info=True)
            bus.publish_event(conversation_id, "btw_done", {
                "agent_name": agent_name,
                "error": str(e),
            })
