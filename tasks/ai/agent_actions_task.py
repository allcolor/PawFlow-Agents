"""AgentActionsTask — pure action dispatcher.

UI commands (open_desktop, list_resources, load_history, set_param,
…) are not agent messages. They have their own endpoint (/api/ui),
their own task slot, their own max_instances. They never traverse
the agent execution pipeline.

Subclasses AgentLoopTask to reuse the action handler dispatch
(AgentActionsMixin lives there).
"""

import json
import logging
from typing import List

from core import FlowFile
from tasks.ai.agent_loop import AgentLoopTask

logger = logging.getLogger(__name__)


class AgentActionsTask(AgentLoopTask):
    """Pure action dispatcher — never runs an agent turn."""

    TYPE = "agentActions"
    NAME = "Agent Actions"
    DESCRIPTION = (
        "Dispatch UI / command actions (list_resources, open_desktop, "
        "load_history, …) without going through the agent execution "
        "pipeline. Use a dedicated endpoint and task slot so heavy "
        "agent work (compact, long messages) never blocks the UI."
    )
    ICON = "settings"

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        _rid = flowfile.get_attribute("http.request.id") or ""
        _act_log = "?"
        try:
            raw = flowfile.get_content().decode("utf-8", errors="replace")
            if raw.lstrip().startswith("{"):
                _b = json.loads(raw)
                if isinstance(_b, dict):
                    _act_log = _b.get("action") or "?"
        except Exception:
            pass
        logger.info("[agent_actions] enter req_id=%s action=%s",
                    _rid[:8] if _rid else "?", _act_log)
        import time as _t_aa
        _t_aa_start = _t_aa.monotonic()
        try:
            result = self._handle_action(flowfile)
            if result is None:
                flowfile.set_content(json.dumps({
                    "error": "Not an action — body must contain {\"action\": ...}",
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                result = [flowfile]
            return result
        finally:
            _dur = (_t_aa.monotonic() - _t_aa_start) * 1000
            logger.info("[agent_actions] exit  req_id=%s action=%s took=%.0fms",
                        _rid[:8] if _rid else "?", _act_log, _dur)


from core import TaskFactory
TaskFactory.register(AgentActionsTask)
