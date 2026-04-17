"""AgentActionsTask — pure action dispatcher.

UI commands (open_desktop, list_resources, load_history, set_param,
...) are not agent messages. They have their own endpoint (/api/ui),
their own task slot, their own max_instances. They never traverse
the agent execution pipeline.

Subclasses AgentLoopTask to reuse the action handler dispatch
(AgentActionsMixin lives there).
"""

import json
from typing import List

from core import FlowFile
from tasks.ai.agent_loop import AgentLoopTask


class AgentActionsTask(AgentLoopTask):
    """Pure action dispatcher — never runs an agent turn."""

    TYPE = "agentActions"
    NAME = "Agent Actions"
    DESCRIPTION = (
        "Dispatch UI / command actions (list_resources, open_desktop, "
        "load_history, ...) without going through the agent execution "
        "pipeline. Use a dedicated endpoint and task slot so heavy "
        "agent work (compact, long messages) never blocks the UI."
    )
    ICON = "settings"

    def __init__(self, config):
        _saved = AgentLoopTask._live_instance
        super().__init__(config)
        # Restore: _live_instance must point to the execution task,
        # not this actions-only dispatcher.
        if _saved is not None:
            AgentLoopTask._live_instance = _saved

    def select_processable(self, connections):
        """UI actions bypass all gating: no LLM capacity check, no
        context-op lock. A /desktop click must not wait for compact
        to finish. Just return the first available FF.
        """
        for conn in connections:
            for ff in conn.peek_all():
                return ff, conn
        return None

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        result = self._handle_action(flowfile)
        if result is None:
            flowfile.set_content(json.dumps({
                "error": "Not an action — body must contain {\"action\": ...}",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            result = [flowfile]
        return result


from core import TaskFactory
TaskFactory.register(AgentActionsTask)
