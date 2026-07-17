"""ConsultAgentHandler — one-shot delegation to the conversation agent's brain.

Designed for realtime voice sessions (docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md):
the realtime model is only the spoken interface, and `consult_agent` routes
any substantial task to the conversation agent's OWN model — its resolved
system prompt (definition, params, skills, identity) and its configured
`llm_service` — returning the answer as the tool result. The realtime tool
bridge's detached-execution path then speaks the answer when it lands,
even past the soft timeout.

The delegated call is a pure text completion: the strong model gets NO
tools here (a one-shot answer, not an agent loop), so the handler has no
side effects and is approval-exempt. It works identically from text
sessions (a cheap front model consulting the configured brain).
"""

import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

_ANSWER_MAX_CHARS = 8000   # generous; the realtime bridge trims to 4000
_CONTEXT_MODE = "summary:2000"


class ConsultAgentHandler(ToolHandler):
    """Ask the conversation's agent (its full model) to handle a task."""

    def __init__(self):
        self._user_id = ""
        self._conversation_id = ""
        self._agent_name = ""

    # -- runtime context (set by the executing bridge/loop) -------------

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id or ""

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id or ""

    def set_agent_name(self, agent_name: str) -> None:
        self._agent_name = agent_name or ""

    # -- ToolHandler ------------------------------------------------------

    @property
    def name(self) -> str:
        return "consult_agent"

    @property
    def display_name(self) -> str:
        return "Consult Agent"

    @property
    def description(self) -> str:
        return (
            "Delegate a task or question to this conversation's agent brain "
            "— its full configured model, system prompt and knowledge — and "
            "return its answer. Use this for anything substantial: "
            "reasoning, analysis, drafting, decisions, domain questions. "
            "You stay the interface: relay the returned answer (summarize "
            "it for speech if you are a voice session). The delegate "
            "receives the recent conversation context automatically; put "
            "everything else it needs in the task text. It has no tools — "
            "it answers in one shot."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "The full task or question for the agent brain. "
                        "Self-contained: include any details from the "
                        "current exchange that the recent conversation "
                        "context may not cover."),
                },
            },
            "required": ["task"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        task = str(arguments.get("task", "") or "").strip()
        if not task:
            return "Error: task is required"
        if not self._conversation_id or not self._agent_name:
            return ("Error: consult_agent needs a conversation and an "
                    "agent context")

        try:
            from core.agent_executor import resolve_agent_task
            agent_task = resolve_agent_task(
                self._agent_name, task, self._user_id,
                conversation_id=self._conversation_id)
        except KeyError as e:
            return f"Error: {e}"

        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(
            agent_task.llm_service, user_id=self._user_id,
            conv_id=self._conversation_id)
        if svc is None or not hasattr(svc, "complete"):
            return (f"Error: LLM service '{agent_task.llm_service}' of "
                    f"agent '{self._agent_name}' could not connect")

        from core.handlers.spawn_agents import resolve_context_messages
        from core.llm_client import LLMMessage
        messages = [LLMMessage(role="system",
                               content=agent_task.system_prompt,
                               conversation_id=self._conversation_id)]
        for m in resolve_context_messages(_CONTEXT_MODE,
                                          self._conversation_id,
                                          self._user_id):
            messages.append(LLMMessage(role=m.get("role", "user"),
                                       content=m.get("content", ""),
                                       conversation_id=self._conversation_id))
        messages.append(LLMMessage(role="user", content=task,
                                   conversation_id=self._conversation_id))

        try:
            resp = svc.complete(
                messages=messages,
                call_user_id=self._user_id,
                call_conversation_id=self._conversation_id,
                call_agent_name=self._agent_name,
                call_event_cid="",
                call_ephemeral_stream=True,
            )
        except Exception as e:
            logger.warning("[consult_agent] delegate call failed: %s", e,
                           exc_info=True)
            return f"Error: delegate call failed: {e}"

        content = (getattr(resp, "content", "") or "").strip()
        if not content:
            return "Error: the delegate returned an empty answer"
        if len(content) > _ANSWER_MAX_CHARS:
            content = content[:_ANSWER_MAX_CHARS] + "\n[answer truncated]"
        return content
