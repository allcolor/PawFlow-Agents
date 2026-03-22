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



class AgentSyncMixin:
    """Synchronous agent execution."""

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

