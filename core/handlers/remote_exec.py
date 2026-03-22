"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)



class RemoteExecutorHandler(ToolHandler):
    """Execute commands on the user's machine through a relay.

    Uses a RemoteExecutorService to communicate with pawflow_executor_relay.py.
    Commands are classified by risk level and may require user approval via
    an SSE dialog in the chat UI (same pattern as LocalFilesHandler).
    """

    _conversation_id: str = ""
    _user_id: str = ""
    _service = None  # RemoteExecutorService instance
    _relay_info: Dict[str, Any] = {}
    _available_services: List[Dict[str, Any]] = []  # Plan D: list of compatible services

    # Class-level shared state (across threads / instances)
    _lock = threading.Lock()
    _pending: Dict[str, threading.Event] = {}
    _results: Dict[str, Any] = {}

    # ── Risk classification ──────────────────────────────────────

    _GIT_RISK = {
        "git_status": "low", "git_diff": "low", "git_log": "low",
        "git_branch": "low",
        "git_add": "medium", "git_commit": "medium", "git_checkout": "medium",
        "git_push": "high", "git_pull": "high", "git_reset": "high",
    }

    _SHELL_LOW = {
        "ls", "dir", "cat", "type", "head", "tail", "wc", "echo", "pwd", "cd",
        "whoami", "date", "file", "which", "where", "env", "printenv", "set",
        "get-childitem", "get-content", "get-location", "hostname", "uname",
        "tree", "less", "more", "sort", "uniq", "diff", "wc",
    }
    _SHELL_HIGH = {
        "rm", "del", "rmdir", "sudo", "chmod", "chown", "chgrp",
        "format", "diskpart", "invoke-expression", "start-process", "iex",
        "remove-item", "kill", "taskkill", "shutdown", "reboot",
        "net", "netsh", "iptables", "mkfs", "dd",
    }

    @classmethod
    def _classify_shell(cls, command: str) -> str:
        """Classify a shell command's risk level."""
        cmd_lower = command.lower().strip()
        # Check first word
        first = cmd_lower.split()[0] if cmd_lower else ""
        # Strip path prefix (e.g. /usr/bin/rm -> rm)
        first_base = first.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        if first_base in cls._SHELL_HIGH:
            return "high"

        # Pattern-based high risk
        high_patterns = [
            r"\brm\s+.*-r", r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard\b",
            r">\s*/dev/", r"curl.*\|\s*(ba)?sh", r"wget.*\|\s*(ba)?sh",
            r"\bsudo\b", r"\b(rm|del)\b.*\s+/\s*$",
            r"remove-item.*-recurse", r"invoke-expression",
        ]
        for pattern in high_patterns:
            if re.search(pattern, cmd_lower):
                return "high"

        if first_base in cls._SHELL_LOW:
            return "low"

        # Default to medium for unknown commands
        return "medium"

    @classmethod
    def classify_risk(cls, action: str, **kwargs) -> str:
        """Classify the risk level of an action."""
        if action in cls._GIT_RISK:
            return cls._GIT_RISK[action]
        if action == "python_exec":
            return "medium"
        if action == "shell":
            return cls._classify_shell(kwargs.get("command", ""))
        return "medium"

    @classmethod
    def needs_approval(cls, risk: str, approval_mode: str) -> bool:
        """Determine if approval is needed based on risk and mode."""
        if approval_mode == "strict":
            return True
        if approval_mode == "auto":
            return risk == "high"
        # "ask" (default): medium and high
        return risk in ("medium", "high")

    # ── ToolHandler interface ────────────────────────────────────

    @property
    def name(self) -> str:
        return "remote_exec"

    @property
    def description(self) -> str:
        info = self._relay_info
        plat = info.get("platform", "unknown")
        shell = info.get("shell", "unknown")
        root = info.get("root", "unknown")
        actions = info.get("actions", ["shell", "python_exec", "git"])
        desc = (
            f"Execute commands on the user's machine via a relay. "
            f"Platform: {plat}, Shell: {shell}, Root: {root}. "
            f"Available actions: {', '.join(actions)}. "
            f"For shell commands, use the correct syntax for {shell} on {plat}. "
            f"Git sub-actions: git_status, git_diff, git_log, git_add, git_commit, "
            f"git_push, git_pull, git_checkout, git_reset, git_branch."
        )
        # Plan D: multi-service selection
        if len(self._available_services) > 1:
            svc_desc = ", ".join(
                f"'{s['id']}' (root={s.get('root', '?')})"
                for s in self._available_services
            )
            desc += f" Available services: {svc_desc}. Use 'service' parameter to choose."
        return desc

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "shell", "python_exec",
                        "git_status", "git_diff", "git_log", "git_add",
                        "git_commit", "git_push", "git_pull", "git_checkout",
                        "git_reset", "git_branch",
                    ],
                    "description": "The action to execute",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (for 'shell' action)",
                },
                "code": {
                    "type": "string",
                    "description": "Python code to execute (for 'python_exec' action)",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref for diff/checkout/reset (optional)",
                },
                "message": {
                    "type": "string",
                    "description": "Commit message (for 'git_commit' action)",
                },
                "files": {
                    "type": "string",
                    "description": "Space-separated file paths (for 'git_add' action)",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to relay root (default: '.')",
                },
                "service": {
                    "type": "string",
                    "description": "Service ID to use (optional, default: first available)",
                },
            },
            "required": ["action"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def set_service(self, service) -> None:
        self._service = service
        if service:
            self._relay_info = service.get_relay_info()
            if hasattr(service, 'set_user_id') and self._user_id:
                service.set_user_id(self._user_id)

    def set_available_services(self, services: List[Dict[str, Any]]) -> None:
        """Plan D: set list of available executor services for multi-service selection."""
        self._available_services = services

    def _resolve_service(self, service_id: str = ""):
        """Resolve which service to use (Plan D: multi-service)."""
        if service_id and self._user_id:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                svc = registry.get_live_instance(self._user_id, service_id)
                if svc:
                    if hasattr(svc, 'set_user_id'):
                        svc.set_user_id(self._user_id)
                    return svc
            except Exception:
                pass
        return self._service

    def execute(self, arguments: Dict[str, Any]) -> str:
        import uuid
        from core.conversation_event_bus import ConversationEventBus

        # Plan D: multi-service selection
        service_id = arguments.get("service", "")
        service = self._resolve_service(service_id) if service_id else self._service

        if not service:
            return (
                "Error: no remote executor relay connected.\n"
                "Run: python pawflow_executor_relay.py --connect ws://<server>/ws/relay "
                "--token <api_key> --secret <secret> --dir <path>"
            )

        action = arguments.get("action", "")
        if not action:
            return "Error: missing 'action' parameter"

        # Build display command for approval dialog
        display_cmd = self._build_display_command(action, arguments)
        risk = self.classify_risk(action, **arguments)
        approval_mode = getattr(service, 'approval_mode', 'ask')

        # Check if approval is needed
        if self.needs_approval(risk, approval_mode) and self._conversation_id:
            request_id = uuid.uuid4().hex[:12]
            event = threading.Event()

            with self._lock:
                self._pending[request_id] = event

            # Send approval request via SSE
            ConversationEventBus.instance().publish_event(
                self._conversation_id, "exec_approval_request", {
                    "request_id": request_id,
                    "action": action,
                    "command": display_cmd,
                    "risk_level": risk,
                    "cwd": arguments.get("cwd", "."),
                    "editable": action == "shell",
                },
            )

            # Block until user responds
            if not event.wait(timeout=120):
                with self._lock:
                    self._pending.pop(request_id, None)
                    self._results.pop(request_id, None)
                return "User did not respond within 120 seconds. Command not executed."

            with self._lock:
                result = self._results.pop(request_id, None)
                self._pending.pop(request_id, None)

            if result is None:
                return "Error: no approval result received"

            if not result.get("approved"):
                return f"User denied execution of: {display_cmd}"

            # User may have edited the command
            edited = result.get("edited_command", "")
            if edited and action == "shell":
                arguments = dict(arguments)
                arguments["command"] = edited
                display_cmd = edited

        # Execute the command via the service
        try:
            kwargs = {}
            if action == "shell":
                kwargs["command"] = arguments.get("command", "")
            elif action == "python_exec":
                kwargs["code"] = arguments.get("code", "")
            elif action == "git_commit":
                kwargs["message"] = arguments.get("message", "")
            elif action in ("git_diff", "git_checkout", "git_reset"):
                ref = arguments.get("ref", "")
                if ref:
                    kwargs["ref"] = ref
                if action == "git_reset":
                    kwargs["mode"] = arguments.get("mode", "--mixed")
            elif action == "git_add":
                files = arguments.get("files", "")
                if files:
                    kwargs["files"] = files

            cwd = arguments.get("cwd", ".")
            if cwd != ".":
                kwargs["cwd"] = cwd

            data = service.send_command(action, **kwargs)

            # Publish output event for chat UI terminal display
            if self._conversation_id:
                ConversationEventBus.instance().publish_event(
                    self._conversation_id, "exec_output", {
                        "action": action,
                        "command": display_cmd,
                        "exit_code": data.get("exit_code", -1),
                        "stdout": data.get("stdout", ""),
                        "stderr": data.get("stderr", ""),
                        "duration_ms": data.get("duration_ms", 0),
                    },
                )

            return self._format_result(action, data)

        except Exception as e:
            return f"Error executing {action}: {e}"

    def _build_display_command(self, action: str, arguments: Dict[str, Any]) -> str:
        """Build a human-readable command string for display."""
        if action == "shell":
            return arguments.get("command", "")
        if action == "python_exec":
            code = arguments.get("code", "")
            if len(code) > 100:
                return f"python -c '{code[:100]}...'"
            return f"python -c '{code}'"
        if action.startswith("git_"):
            sub = action[4:]  # git_status -> status
            extras = []
            if action == "git_commit":
                extras.append(f"-m \"{arguments.get('message', '')}\"")
            elif action in ("git_diff", "git_checkout", "git_reset"):
                ref = arguments.get("ref", "")
                if ref:
                    extras.append(ref)
            elif action == "git_add":
                files = arguments.get("files", "")
                extras.append(files if files else "-A")
            return f"git {sub} {' '.join(extras)}".strip()
        return action

    def _format_result(self, action: str, data: Dict[str, Any]) -> str:
        """Format relay result for the LLM."""
        exit_code = data.get("exit_code", -1)
        stdout = data.get("stdout", "")
        stderr = data.get("stderr", "")
        duration = data.get("duration_ms", 0)

        parts = []
        if exit_code == 0:
            parts.append(f"Command succeeded (exit code 0, {duration}ms)")
        else:
            parts.append(f"Command failed (exit code {exit_code}, {duration}ms)")

        if stdout:
            parts.append(f"stdout:\n{stdout}")
        if stderr:
            parts.append(f"stderr:\n{stderr}")

        return "\n".join(parts)

    @classmethod
    def resolve_request(cls, request_id: str, result: Any) -> bool:
        """Called when the user approves/denies a command in the chat UI."""
        with cls._lock:
            event = cls._pending.get(request_id)
            if event is None:
                logger.warning(f"[remote_exec] resolve_request for unknown/expired id: {request_id}")
                return False
            cls._results[request_id] = result
            event.set()
        return True
