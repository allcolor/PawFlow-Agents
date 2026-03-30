"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class CreateFileHandler(ToolHandler):
    """Create a downloadable file and return a URL.

    Stores the file in the FileStore singleton with a configurable TTL.
    The base_url must be set via set_base_url() before use so the tool
    can generate valid download links.
    """

    _base_url: str = "http://localhost:9090"
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "share_file"

    @property
    def description(self) -> str:
        return (
            "Upload a file to the server and return a download URL for the user. "
            "Use this ONLY to share files with the user via chat (images, PDFs, exports). "
            "Do NOT use this to create or modify code/workspace files — use 'write' instead."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename with extension (e.g. 'report.csv', 'code.py')",
                },
                "content": {
                    "type": "string",
                    "description": "File content as text",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type (default: auto-detected from extension)",
                },
                "destination": {
                    "type": "string",
                    "description": "Where to write: 'filestore' (default, server), or relay service name",
                },
            },
            "required": ["filename", "content"],
        }

    def set_base_url(self, base_url: str):
        self._base_url = base_url.rstrip("/")

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        filename = arguments.get("filename", "file.txt")
        content = arguments.get("content", "")
        content_type = arguments.get("content_type", "")
        destination = arguments.get("destination", "filestore")

        if not content_type:
            content_type = self._guess_content_type(filename)

        from core.storage_resolver import StorageResolver
        resolver = StorageResolver(user_id=self._user_id)
        result = resolver.write(destination, filename,
                                content.encode("utf-8"), content_type)

        if result.get("file_id"):
            url = f"{self._base_url}/files/{result['file_id']}/{filename}"
            return f"File created: {url}\nfile_id: {result['file_id']}"
        else:
            return f"File written to {result.get('destination', destination)}: {result.get('path', filename)}"

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mapping = {
            "txt": "text/plain",
            "html": "text/html",
            "htm": "text/html",
            "css": "text/css",
            "js": "application/javascript",
            "json": "application/json",
            "csv": "text/csv",
            "xml": "application/xml",
            "py": "text/x-python",
            "md": "text/markdown",
            "pdf": "application/pdf",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "svg": "image/svg+xml",
            "zip": "application/zip",
        }
        return mapping.get(ext, "application/octet-stream")


class ScheduleContinuationHandler(ToolHandler):
    """Signal that the agent wants to continue working after a pause.

    When the agent calls this tool, the agent loop will:
    1. Let the LLM finish its current response (status update to the user)
    2. Wait the specified delay
    3. Inject the plan as a system message and start a new round
    """

    @property
    def name(self) -> str:
        return "schedule_continuation"

    @property
    def description(self) -> str:
        return (
            "Schedule a continuation of your work. Call this when you have more "
            "research or tasks to do but want to deliver intermediate findings first. "
            "After your current response, the system will automatically resume your work. "
            "Include a clear plan of what you'll do next."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "string",
                    "description": "What you plan to do in the next round (be specific)",
                },
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait before resuming (default 3)",
                },
            },
            "required": ["plan"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        plan = arguments.get("plan", "")
        delay = int(arguments.get("delay_seconds", 3))
        return (
            f"Continuation scheduled. Plan: {plan}. "
            f"Resuming in {delay}s. Now give the user a status update "
            f"about what you've found so far and what you'll do next."
        )


class ScheduleRecheckHandler(ToolHandler):
    """Schedule a persistent recheck for the current conversation.

    The agent calls this to say "wake me up at time X" or "wake me up in N seconds".
    The recheck survives server restarts — it's persisted to disk.
    """

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "schedule_recheck"

    @property
    def description(self) -> str:
        return (
            "Schedule a future autonomous check-in for this conversation. "
            "Use this when the user asks you to do something at a specific time or date, "
            "or when you need to periodically monitor something. "
            "The recheck survives server restarts. "
            "You can specify either a delay in seconds or an exact ISO datetime."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds from now to schedule the recheck (e.g. 3600 for 1 hour)",
                },
                "at": {
                    "type": "string",
                    "description": "ISO 8601 datetime for the recheck (e.g. '2026-03-12T14:00:00'). "
                                   "If no timezone, assumes UTC.",
                },
                "reason": {
                    "type": "string",
                    "description": "What to do when the recheck fires (e.g. 'check stock price of AAPL')",
                },
                "agent": {
                    "type": "string",
                    "description": "Agent to wake up (e.g. 'grok', 'qwen'). Default: whichever agent is active.",
                },
            },
            "required": ["reason"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.poll_scheduler import PollScheduler

        reason = arguments.get("reason", "scheduled recheck")
        at_str = arguments.get("at", "")
        delay = arguments.get("delay_seconds", 0)
        agent = arguments.get("agent", "")

        if not self._conversation_id:
            return "Error: no conversation context — cannot schedule recheck"

        scheduler = PollScheduler.instance()

        if at_str:
            from datetime import datetime, timezone as tz
            try:
                dt = datetime.fromisoformat(at_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz.utc)
                recheck_at = dt.timestamp()
            except ValueError:
                return f"Error: invalid datetime format '{at_str}'. Use ISO 8601 (e.g. '2026-03-12T14:00:00')"
        elif delay and int(delay) > 0:
            import time
            recheck_at = time.time() + int(delay)
        else:
            return "Error: provide either 'delay_seconds' or 'at'"

        # If agent specified, encode it in reason so poller wakes the right agent
        sched_reason = reason
        if agent:
            sched_reason = f"[scheduled:{agent}] {reason}"

        scheduler.schedule(
            conversation_id=self._conversation_id,
            recheck_at=recheck_at,
            user_id=self._user_id,
            reason=sched_reason,
        )

        from datetime import datetime, timezone as tz
        dt_str = datetime.fromtimestamp(recheck_at, tz=tz.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        agent_info = f" Agent: {agent}" if agent else ""
        return f"Recheck scheduled for {dt_str}.{agent_info} Reason: {reason}"


class LocalFilesHandler(ToolHandler):
    """Access the user's local filesystem through the browser.

    Uses the File System Access API (Chromium only).  When the agent calls
    this tool, a ``file_request`` SSE event is sent to the browser which
    executes the operation locally and POSTs the result back.  The handler
    blocks until the browser responds (or times out).
    """

    _conversation_id: str = ""

    # Class-level shared state (across threads / instances)
    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "local_files"

    @property
    def description(self) -> str:
        return (
            "Access files on the user's local machine through the browser. "
            "The user must first open a local folder by clicking the folder button in the chat UI. "
            "Actions: list_dir (list files/subdirs), read_file (read text content), "
            "write_file (create or overwrite a file). Paths are relative to the opened folder."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_dir", "read_file", "write_file"],
                    "description": "The operation to perform",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within the opened folder (e.g. 'src/main.py' or '.')",
                },
                "content": {
                    "type": "string",
                    "description": "File content for write_file action",
                },
            },
            "required": ["action", "path"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        import uuid
        from core.conversation_event_bus import ConversationEventBus

        if not self._conversation_id:
            return "Error: no conversation context"

        action = arguments.get("action", "")
        path = arguments.get("path", ".")
        content = arguments.get("content", "")

        request_id = uuid.uuid4().hex[:12]
        event = threading.Event()

        with self._lock:
            self._pending[request_id] = event

        # Ask the browser to execute the file operation
        ConversationEventBus.instance().publish_event(
            self._conversation_id, "file_request", {
                "request_id": request_id,
                "action": action,
                "path": path,
                "content": content,
            },
        )

        # Block until browser responds or timeout
        if not event.wait(timeout=60):
            with self._lock:
                self._pending.pop(request_id, None)
                self._results.pop(request_id, None)
            return (
                "Error: browser did not respond within 60s. "
                "Make sure the user has opened a local folder by clicking the folder button (📁)."
            )

        with self._lock:
            result = self._results.pop(request_id, None)
            self._pending.pop(request_id, None)

        if result is None:
            return "Error: no result received"

        if isinstance(result, dict) and "error" in result:
            return f"Error: {result['error']}"

        return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)

    @classmethod
    def resolve_request(cls, request_id: str, result: Any) -> bool:
        """Called when the browser POSTs a file operation result back."""
        with cls._lock:
            event = cls._pending.get(request_id)
            if event is None:
                logger.warning(f"[local_files] resolve_request for unknown/expired id: {request_id}")
                return False
            cls._results[request_id] = result
            event.set()
        return True
