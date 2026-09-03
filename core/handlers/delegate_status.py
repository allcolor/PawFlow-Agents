"""delegate_status / delegate_result — caller-facing delegate observability.

The calling agent is otherwise blind between a delegate/flash_delegate spawn
and the asynchronous result delivery. These tools rebuild acknowledged and
finished work from the durable transcript, merge current runtime details, and
let a caller recover status or output after its provider context was compacted.
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


def _durable_state(parent_conv_id: str, caller: str,
                   user_id: str) -> Dict[str, list]:
    """Load transcript-backed delegate state without hiding runtime entries."""
    try:
        from core.conversation_store import ConversationStore
        return ConversationStore.instance().load_delegate_state(
            parent_conv_id, caller, user_id=user_id)
    except Exception:
        logger.warning(
            "delegate durable-state scan failed for %s/%s",
            parent_conv_id, caller, exc_info=True)
        return {"live": [], "finished": []}


def _merged_state(parent_conv_id: str, caller: str,
                  user_id: str) -> tuple[list, list]:
    """Merge durable task identities with process-local execution details."""
    from core.agent_executor import (
        list_finished_delegates, list_live_delegates,
    )

    durable = _durable_state(parent_conv_id, caller, user_id)
    live_by_id = {
        str(entry.get("task_id") or ""): dict(entry)
        for entry in durable.get("live") or []
        if entry.get("task_id")
    }
    for entry in list_live_delegates(parent_conv_id, caller):
        item = dict(entry)
        target = str(item.get("target") or "")
        item.setdefault(
            "mode", "flash" if _FLASH_MARKER in target else "isolated")
        item["status"] = "running"
        item["runtime_attached"] = True
        live_by_id[str(item.get("task_id") or "")] = item

    finished_by_id = {
        str(entry.get("task_id") or ""): dict(entry)
        for entry in durable.get("finished") or []
        if entry.get("task_id")
    }
    for entry in list_finished_delegates(parent_conv_id, caller):
        item = dict(entry)
        target = str(item.get("target") or "")
        item.setdefault(
            "mode", "flash" if _FLASH_MARKER in target else "isolated")
        finished_by_id[str(item.get("task_id") or "")] = item

    for task_id in finished_by_id:
        live_by_id.pop(task_id, None)
    live = sorted(
        live_by_id.values(),
        key=lambda item: item.get("started_at") or 0.0,
    )
    finished = sorted(
        finished_by_id.values(),
        key=lambda item: item.get("finished_at") or 0.0,
    )[-100:]
    return live, finished


def _render_entry(entry: Dict[str, Any], *, include_response: bool) -> Dict:
    item = dict(entry)
    target = str(item.pop("target", "") or "")
    item.pop("caller", None)
    response = str(item.get("response") or "")
    if not include_response:
        item.pop("response", None)
        item.setdefault("response_chars", len(response))
    item.update(_display_name(target))
    item["agent"] = target
    return item


class DelegateStatusHandler(SpawnAgentsHandler):
    """Report the caller's delegates: live ones and recent finished ones."""

    @property
    def name(self) -> str:
        return "delegate_status"

    @property
    def description(self) -> str:
        return (
            "Check durable status for your shared, isolated, and flash "
            "delegates, including after context compaction. Returns pending/"
            "running work with task IDs and runtime details, plus the latest "
            "100 finished results. Use delegate_result to fetch one output."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def execute(self, arguments: Dict[str, Any]) -> str:
        import time

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
        raw_live, raw_finished = _merged_state(
            parent_conv_id, src_agent, self._user_id)
        live = []
        for entry in raw_live:
            item = _render_entry(entry, include_response=False)
            started = item.pop("started_at", 0.0)
            item["age_seconds"] = round(now - started, 1) if started else None
            live.append(item)

        finished = [
            _render_entry(entry, include_response=False)
            for entry in raw_finished
        ]

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
            "flash_delegate reply), including after context compaction. "
            "Returns the durable response text plus status, error, and "
            "duration; pending/running delegates remain discoverable."
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
        from core.agent_executor import get_finished_delegate
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
            return json.dumps(
                _render_entry(entry, include_response=True),
                ensure_ascii=False, indent=2)

        live_entries, finished_entries = _merged_state(
            parent_conv_id, src_agent, self._user_id)
        entry = next((
            dict(item) for item in reversed(finished_entries)
            if item.get("task_id") == task_id
        ), None)
        if entry is not None:
            return json.dumps(
                _render_entry(entry, include_response=True),
                ensure_ascii=False, indent=2)

        for live in live_entries:
            if live.get("task_id") == task_id:
                status = str(live.get("status") or "running")
                return json.dumps({
                    "task_id": task_id,
                    "status": status,
                    "message": "Delegate has no terminal result yet. "
                               "Check again with delegate_status.",
                }, ensure_ascii=False)

        return (
            f"Error: no finished or live delegate with task_id '{task_id}' "
            f"for agent '{src_agent}' (finished results are retained for "
            f"the last 100 completions)."
        )
