"""delegate_status / delegate_result — caller-facing delegate observability.

The calling agent is otherwise blind between a delegate/flash_delegate spawn
and the asynchronous result delivery: these tools expose the live-delegate
registry and the bounded finished-results ring (recorded in
_SpawnDeliveryMixin._inject_bg_result) so the caller can verify liveness and
pull a result whose push delivery it missed.
"""

import json
import logging
from typing import Any, Dict

from core.handlers.spawn_agents import SpawnAgentsHandler

logger = logging.getLogger(__name__)

_FLASH_MARKER = "::flash::"


def _display_name(target: str) -> Dict[str, str]:
    """Split a runtime target into a display name and kind."""
    if _FLASH_MARKER in target:
        return {"name": target.split(_FLASH_MARKER, 1)[-1], "kind": "flash"}
    return {"name": target, "kind": "agent"}


class DelegateStatusHandler(SpawnAgentsHandler):
    """Report the caller's delegates: live ones and recent finished ones."""

    @property
    def name(self) -> str:
        return "delegate_status"

    @property
    def description(self) -> str:
        return (
            "Check the status of your delegates (both flash_delegate agents "
            "and background delegate sub-agents). Returns the live ones "
            "(name, kind, task_id, age, queued follow-ups) and the recently "
            "finished ones (status, error, duration, response size). Use it "
            "to verify delegated work is actually running instead of "
            "inferring liveness from silence; fetch a finished output with "
            "delegate_result."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time

        from core.agent_executor import (
            list_finished_delegates, list_live_delegates,
        )
        from core.service_registry import _parent_conversation_id

        raw_conv_id = self._conversation_id
        parent_conv_id = _parent_conversation_id(raw_conv_id) or raw_conv_id
        src_agent, _src_svc = self._resolve_source_context()
        if not src_agent:
            return (
                "Error: delegate_status could not determine the calling"
                " agent: no thread-local source context and no agent"
                " instance name is configured for this conversation."
            )

        now = time.time()
        live = []
        for entry in list_live_delegates(parent_conv_id, src_agent):
            target = entry.pop("target")
            started = entry.pop("started_at", 0.0)
            entry.pop("caller", None)
            entry.update(_display_name(target))
            entry["agent"] = target
            entry["age_seconds"] = round(now - started, 1) if started else None
            live.append(entry)

        finished = []
        for entry in list_finished_delegates(parent_conv_id, src_agent):
            target = entry.pop("target")
            entry.pop("caller", None)
            entry.update(_display_name(target))
            entry["agent"] = target
            finished.append(entry)

        return json.dumps({
            "live": live,
            "finished": finished,
            "counts": {"live": len(live), "finished": len(finished)},
        }, ensure_ascii=False, indent=2)


class DelegateResultHandler(DelegateStatusHandler):
    """Fetch the retained output of a finished delegate by task_id."""

    @property
    def name(self) -> str:
        return "delegate_result"

    @property
    def description(self) -> str:
        return (
            "Fetch the output of one of your finished delegates by task_id "
            "(as listed by delegate_status, or from the delegate/"
            "flash_delegate reply). Returns the full retained response text "
            "plus status, error, and duration. Use it to pull a result whose "
            "asynchronous delivery you missed; if the delegate is still "
            "running it says so instead."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task ID of the delegate (from the spawn "
                                   "reply or delegate_status)",
                },
            },
            "required": ["task_id"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.agent_executor import (
            get_finished_delegate, list_live_delegates,
        )
        from core.service_registry import _parent_conversation_id

        task_id = str(arguments.get("task_id", "")).strip()
        if not task_id:
            return "Error: delegate_result requires a non-empty task_id."

        raw_conv_id = self._conversation_id
        parent_conv_id = _parent_conversation_id(raw_conv_id) or raw_conv_id
        src_agent, _src_svc = self._resolve_source_context()
        if not src_agent:
            return (
                "Error: delegate_result could not determine the calling"
                " agent: no thread-local source context and no agent"
                " instance name is configured for this conversation."
            )

        entry = get_finished_delegate(parent_conv_id, src_agent, task_id)
        if entry is not None:
            target = entry.pop("target")
            entry.pop("caller", None)
            entry.update(_display_name(target))
            entry["agent"] = target
            return json.dumps(entry, ensure_ascii=False, indent=2)

        for live in list_live_delegates(parent_conv_id, src_agent):
            if live.get("task_id") == task_id:
                return json.dumps({
                    "task_id": task_id,
                    "status": "running",
                    "message": "Delegate is still running — no result yet. "
                               "Check again with delegate_status.",
                }, ensure_ascii=False)

        return (
            f"Error: no finished or live delegate with task_id '{task_id}' "
            f"for agent '{src_agent}' (finished results are retained for "
            f"the last 100 completions)."
        )
