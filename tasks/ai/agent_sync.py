"""AgentLoopTask mixin — synchronous agent execution.

Delegates to _run_agent_loop with a SyncEmitter (no SSE, no threading).
"""
import json
import logging
from typing import List

from core import FlowFile
from tasks.ai.agent_emitter import SyncEmitter

logger = logging.getLogger(__name__)


class AgentStreamingMixin:
    """Placeholder — streaming methods are in agent_streaming.py."""


class AgentSyncMixin:
    """Synchronous agent execution — blocking, returns FlowFile with response."""

    def _execute_sync(self, flowfile: FlowFile) -> List[FlowFile]:
        ctx = self._prepare_agent_context(flowfile)
        emitter = SyncEmitter()
        result = self._run_agent_loop(ctx, emitter)

        # Set FlowFile attributes from result
        flowfile.set_attribute("agent.iterations", str(result.iterations))
        flowfile.set_attribute("agent.tools_called", ",".join(result.tools_called))
        flowfile.set_attribute("agent.model", result.model)
        flowfile.set_attribute("agent.tokens_in", str(result.tokens_in))
        flowfile.set_attribute("agent.tokens_out", str(result.tokens_out))
        flowfile.set_attribute("agent.duration_ms", f"{result.duration_ms:.1f}")
        flowfile.set_attribute("agent.finish_reason", result.finish_reason)

        conversation_id = ctx.get("conversation_id", "")
        use_conv_store = ctx.get("use_conv_store", False)
        conv_ttl = ctx.get("conv_ttl", 0)
        conv_attr = ctx.get("conv_attr", "")
        base_count = ctx.get("_base_message_count", 0)

        # Persist new messages to ConversationStore (one at a time via
        # the unified append_message router). FIFO writer queue means
        # wait=True on the last message blocks until all prior writes
        # completed.
        if use_conv_store and conversation_id and result.new_messages:
            from core.conversation_writer import ConversationWriter
            serialized = self._serialize_messages(
                result.new_messages, channel=ctx.get("channel", ""))
            writer = ConversationWriter.for_conversation(conversation_id)
            _agent_n = ctx.get("active_agent_name", "") or ""
            for i, m in enumerate(serialized):
                writer.enqueue_message(
                    m, agent_name=_agent_n,
                    user_id=ctx.get("user_id", ""),
                    wait=(i == len(serialized) - 1))

        # Serialize full conversation to FlowFile attribute (pipeline mode)
        if conv_attr:
            flowfile.set_attribute(conv_attr, json.dumps(
                self._serialize_messages(
                    result.messages, channel=ctx.get("channel", "")),
                ensure_ascii=False))

        # Build output JSON
        if use_conv_store:
            import re as _re
            output = json.dumps({
                "response": result.response_content,
                "conversation_id": conversation_id,
                "model": result.model,
                "provider": result.provider,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "source": result.source,
            }, ensure_ascii=False)
            flowfile.set_content(output.encode("utf-8"))
            flowfile.set_attribute("agent.conversation_id", conversation_id)
        else:
            flowfile.set_content((result.response_content or "").encode("utf-8"))

        return [flowfile]
