"""SpawnAgent Task — Spawn an agent in a linked conversation from a flow.

Supports sync (wait for response) and async (fire and forget) modes.
The agent runs in the conversation context with configurable history access.

Flow pattern:
    someTask → spawnAgent → handleResponse (sync)
    someTask → spawnAgent                  (async)

Config:
    conversation_id: "${_conversation_id}"
    user_id: "${_user_id}"
    agent_name: Name of the agent to spawn
    mode: "sync" (wait for response) or "async" (fire and forget)
    context_mode: "isolated" | "last:N" | "summary:N" | "full"
"""

import json
import logging
import time
from typing import Dict, Any, List

from core import FlowFile, TaskFactory
from core.base_task import BaseTask

logger = logging.getLogger(__name__)


class SpawnAgentTask(BaseTask):
    """Spawn an agent in a conversation from a flow."""

    TYPE = "spawnAgent"
    VERSION = "1.0.0"
    NAME = "Spawn Agent"
    DESCRIPTION = "Spawn an agent in a linked conversation (sync or async)"
    ICON = "ai"

    def set_runtime_context(self, *, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = ""):
        from core.flow_runtime_access import set_runtime_context
        set_runtime_context(
            self, user_id=user_id, conversation_id=conversation_id,
            scope=scope, agent_name=agent_name)

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            "conversation_id": {
                "type": "string", "required": True,
                "default": "${_conversation_id}",
                "description": "Target conversation ID",
            },
            "user_id": {
                "type": "string", "required": True,
                "default": "${_user_id}",
                "description": "User ID for agent resolution",
            },
            "agent_name": {
                "type": "string", "required": True,
                "description": "Name of the agent to spawn",
            },
            "mode": {
                "type": "select", "required": False, "default": "async",
                "options": ["sync", "async"],
                "description": "sync = wait for response, async = fire and forget",
            },
            "context_mode": {
                "type": "select", "required": False, "default": "isolated",
                "options": ["isolated", "last:5", "last:10", "last:20", "full"],
                "description": "How much conversation context the agent receives",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        conv_id = flowfile.get_attribute("conversation_id") or self.config.get("conversation_id", "")
        user_id = self.config.get("user_id", "")
        agent_name = self.config.get("agent_name", "")
        mode = self.config.get("mode", "async")
        context_mode = self.config.get("context_mode", "isolated")

        if not conv_id or "${" in conv_id:
            flowfile.set_content(json.dumps({
                "error": "No conversation_id - set via FlowFile attribute or flow parameter",
            }).encode())
            return [flowfile]

        if not agent_name:
            flowfile.set_content(json.dumps({
                "error": "Missing agent_name",
            }).encode())
            return [flowfile]

        try:
            from core.flow_runtime_access import (
                authorize_conversation_target, authorize_user_target,
                conversation_owner, runtime_context_from_task,
                trusted_requester_user_id,
            )
            ctx = runtime_context_from_task(self)
            requester = trusted_requester_user_id(flowfile)
            conv_id = authorize_conversation_target(
                ctx, conv_id, requester_user_id=requester,
                allow_global_admin=self.config.get("allow_global_admin"))
            user_id = authorize_user_target(
                ctx, user_id or conversation_owner(conv_id),
                requester_user_id=requester,
                allow_global_admin=self.config.get("allow_global_admin"))
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            return [flowfile]

        message = flowfile.get_content().decode("utf-8", errors="replace")
        if not message.strip():
            flowfile.set_content(json.dumps({
                "error": "Empty message — nothing to send to agent",
            }).encode())
            return [flowfile]

        if mode == "sync":
            return self._execute_sync(flowfile, conv_id, user_id,
                                       agent_name, message, context_mode)
        else:
            return self._execute_async(flowfile, conv_id, user_id,
                                        agent_name, message)

    def _execute_sync(self, flowfile, conv_id, user_id, agent_name,
                       message, context_mode):
        """Sync mode: resolve agent, run loop, wait for response."""
        from core.agent_executor import resolve_agent_task, SubAgentExecutor
        from core.llm_client import LLMClient

        try:
            task = resolve_agent_task(agent_name, message, user_id, conv_id)
            task.context_mode = context_mode
            task.parent_conversation_id = conv_id

            # Resolve LLM client for this agent
            llm_svc = task.llm_service
            client = None
            if llm_svc:
                try:
                    from core.service_registry import ServiceRegistry
                    svc = ServiceRegistry.get_instance().resolve(
                        llm_svc, user_id=user_id)
                    if svc:
                        client = svc.get_client()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not client:
                # Fallback to default service
                try:
                    from core.service_registry import ServiceRegistry
                    svc = ServiceRegistry.get_instance().resolve("default")
                    if svc:
                        client = svc.get_client()
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not client:
                flowfile.set_content(json.dumps({
                    "error": f"No LLM client available for agent '{agent_name}'",
                }).encode())
                return [flowfile]

            from core.tool_registry import create_default_registry
            registry = create_default_registry()
            executor = SubAgentExecutor(client, registry, max_workers=1)
            result = executor.execute_agent(task)
            executor.shutdown()

            # Publish the response in the conversation
            from core.conversation_store import ConversationStore
            from core.conversation_event_bus import ConversationEventBus
            source = {"type": "agent", "name": agent_name}

            if result.status == "completed" and result.response:
                import uuid as _uuid_sa
                _sa_msg_id = _uuid_sa.uuid4().hex[:12]
                from core.conversation_writer import ConversationWriter
                from core.llm_client import stamp_message
                # `done` SSE fires AFTER the message hits disk
                # (visible ⇒ persisted invariant).
                _done_sse = {
                    "type": "done",
                    "data": {
                        "response": result.response,
                        "msg_id": _sa_msg_id,
                        "all_msg_ids": [_sa_msg_id],
                        "conversation_id": conv_id,
                        "agent_name": agent_name,
                        "source": source,
                        "model": result.model,
                        "provider": result.provider,
                        "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out,
                        "tools_called": result.tools_called,
                        "iterations": result.iterations,
                        "duration_ms": result.duration_ms,
                    },
                }
                ConversationWriter.for_conversation(conv_id).enqueue_message(
                    stamp_message({
                        "role": "assistant",
                        "content": result.response,
                        "source": source,
                        "msg_id": _sa_msg_id,
                    }, conv_id),
                    agent_name=agent_name,
                    sse_events=[_done_sse])

            flowfile.set_content(json.dumps({
                "status": result.status,
                "response": result.response,
                "agent": agent_name,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "duration_ms": result.duration_ms,
            }, ensure_ascii=False).encode())

        except KeyError:
            flowfile.set_content(json.dumps({
                "error": f"Agent '{agent_name}' not found",
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({
                "error": f"SpawnAgent sync failed: {e}",
            }).encode())

        return [flowfile]

    def _execute_async(self, flowfile, conv_id, user_id, agent_name, message):
        """Async mode: inject message into conversation, agent picks it up."""
        from core.conversation_store import ConversationStore
        from core.conversation_event_bus import ConversationEventBus

        store = ConversationStore.instance()
        bus = ConversationEventBus.instance()

        from core.conversation_writer import ConversationWriter
        from core.llm_client import stamp_message
        ConversationWriter.for_conversation(conv_id).enqueue_message(
            stamp_message({
                "role": "user",
                "content": message,
                "source": {
                    "type": "flow",
                    "name": self.config.get("_service_id", "flow"),
                    "target_agent": agent_name,
                },
            }, conv_id),
            agent_name=agent_name)

        # Notify so the agentLoop picks it up at next checkpoint
        bus.publish_event(conv_id, "message_queued", {
            "conversation_id": conv_id,
            "target_agent": agent_name,
        })

        logger.info(f"[spawnAgent] Async: sent to {agent_name} in {conv_id[:8]}")
        flowfile.set_content(json.dumps({
            "status": "queued",
            "agent": agent_name,
            "conversation_id": conv_id,
        }).encode())
        return [flowfile]


TaskFactory.register(SpawnAgentTask)
