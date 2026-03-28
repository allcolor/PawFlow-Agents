"""compact_result tool — receives summary from Claude Code during compaction."""

import json
import logging
import threading
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

# Global state: when a compact is waiting, these are set
_pending_lock = threading.Lock()
_pending: Dict[str, dict] = {}  # key → {"event": Event, "summary": str}


def wait_for_compact_result(key: str, timeout: float = 300) -> str:
    """Block until compact_result is called for this key. Returns summary or raises."""
    with _pending_lock:
        entry = _pending.get(key)
    if not entry:
        raise RuntimeError(f"No compact pending for key '{key}'. Call set_compact_key first.")
    # Already delivered?
    if entry["summary"]:
        with _pending_lock:
            _pending.pop(key, None)
        return entry["summary"]
    if not entry["event"].wait(timeout=timeout):
        with _pending_lock:
            _pending.pop(key, None)
        raise TimeoutError(f"compact_result not called within {timeout}s")
    with _pending_lock:
        entry = _pending.pop(key, {})
    return entry.get("summary", "")


def set_compact_key(key: str):
    """Register a key to listen for. Called before launching Claude Code."""
    event = threading.Event()
    with _pending_lock:
        _pending[key] = {"event": event, "summary": ""}


class CompactResultHandler(ToolHandler):

    @property
    def name(self):
        return "compact_result"

    @property
    def description(self):
        return (
            "Return the result of a compaction/summarization task. "
            "Call this with your summary when asked to summarize content. "
            "This is the ONLY way to return a summary — do NOT respond with text."
        )

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "The summary text",
                },
                "compact_key": {
                    "type": "string",
                    "description": "The compact key provided in the instructions",
                },
            },
            "required": ["summary", "compact_key"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(arguments, str):
            arguments = {"summary": arguments}
        summary = arguments.get("summary", "")
        if not summary:
            return "Error: summary is required"
        compact_key = arguments.get("compact_key", "")
        if not compact_key:
            return "Error: compact_key is required. Check the instructions for the compact_key value."
        with _pending_lock:
            delivered = False
            if compact_key and compact_key in _pending:
                entry = _pending[compact_key]
                if not entry["summary"]:
                    entry["summary"] = summary
                    entry["event"].set()
                    delivered = True
                    logger.info("[compact_result] delivered %d chars to key '%s'",
                                len(summary), compact_key)
            # Fallback: wrong key but exactly 1 pending → deliver anyway
            # (Claude may hallucinate a key from the conversation content)
            if not delivered and _pending:
                _keys = [k for k, v in _pending.items() if not v["summary"]]
                if len(_keys) == 1:
                    _fallback_key = _keys[0]
                    entry = _pending[_fallback_key]
                    entry["summary"] = summary
                    entry["event"].set()
                    delivered = True
                    logger.warning("[compact_result] key '%s' not found, delivered to "
                                   "sole pending key '%s' (%d chars)",
                                   compact_key, _fallback_key, len(summary))
            if not delivered:
                logger.warning("[compact_result] key '%s' not found in pending: %s",
                               compact_key, list(_pending.keys()))
        if not delivered:
            logger.info("[compact_result] called but no compact pending, ignoring")
            return "No compact in progress. Summary ignored."
        return "Summary received."
