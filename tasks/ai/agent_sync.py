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

        # Persistence: each message was already routed through
        # ConversationWriter.enqueue_message during the loop (agent_core._append).
        # Block here on the final writer flush to guarantee disk sync before
        # the sync entrypoint returns.
        if use_conv_store and conversation_id:
            from core.conversation_writer import ConversationWriter
            ConversationWriter.for_conversation(conversation_id).flush(timeout=30.0)

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
