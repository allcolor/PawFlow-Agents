"""EnterPlanMode / ExitPlanMode handlers.

Pawflow replacements for the Claude Code built-ins of the same name.

Plan mode is a conv-scoped flag (ConversationStore.set_extra(plan_mode))
that, when enabled, makes ``agent_context.build_context`` append a PLAN
MODE directive to the system prompt (see tasks/ai/agent_context.py).
While the directive is active, the agent MUST call ``create_plan`` before
any other tools and wait for ``approve_plan`` before executing.

Until now the flag was only reachable via the user-facing ``/plan on|off``
slash command. These handlers let the agent toggle its own plan mode when
it decides the work is complex enough to warrant a plan (the exact
semantics of the CC built-ins).
"""

import logging
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class EnterPlanModeHandler(ToolHandler):
    """Enable plan mode for the current conversation."""

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "EnterPlanMode"

    @property
    def description(self) -> str:
        return (
            "Switch this conversation into PLAN MODE. While plan mode is "
            "active, you must call create_plan(title, steps) to propose a "
            "plan and wait for the user to call approve_plan before "
            "executing any other tools. Use this when the task is complex "
            "enough that proposing a plan up-front is safer than diving "
            "straight in. Call ExitPlanMode when the plan is executed (or "
            "rejected) and you want to return to normal operation."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        # No args — plan mode is a conv-scoped toggle, not a per-call param.
        return {"type": "object", "properties": {}, "required": []}

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._conversation_id:
            return "Error: no conversation context — cannot enter plan mode."
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        store.set_extra(self._conversation_id, "plan_mode", True,
                        user_id=self._user_id or "")
        logger.info("[plan-mode] ENTERED conv=%s user=%s",
                    self._conversation_id[:8], self._user_id or "?")
        return (
            "Plan mode ENABLED. You must now call create_plan(title, steps) "
            "to propose your plan. Do NOT call any other tools until the "
            "user runs approve_plan. Call ExitPlanMode when you want to "
            "leave plan mode."
        )


class ExitPlanModeHandler(ToolHandler):
    """Disable plan mode for the current conversation."""

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "ExitPlanMode"

    @property
    def description(self) -> str:
        return (
            "Leave PLAN MODE and return to normal operation. Call this once "
            "your plan has been executed (or rejected by the user) and you "
            "no longer need the plan-first protocol."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._conversation_id:
            return "Error: no conversation context — cannot exit plan mode."
        from core.conversation_store import ConversationStore
        store = ConversationStore.instance()
        store.set_extra(self._conversation_id, "plan_mode", False,
                        user_id=self._user_id or "")
        logger.info("[plan-mode] EXITED conv=%s user=%s",
                    self._conversation_id[:8], self._user_id or "?")
        return "Plan mode DISABLED. Normal operation resumed."


__all__ = ["EnterPlanModeHandler", "ExitPlanModeHandler"]
