"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import re
import threading
from typing import Dict, Any, List, Optional

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


def _detect_related_tests(modified_file: str) -> list:
    """Given a modified file path, return likely related test file paths."""
    from pathlib import Path as _Path
    p = _Path(modified_file)
    if p.name.startswith("test_"):
        return []  # Already a test file
    stem = p.stem
    candidates = [
        f"test_{stem}.py",
        f"tests/test_{stem}.py",
        f"test/{stem}_test.py",
        f"{p.parent}/test_{stem}.py",
    ]
    return candidates





class RunTestsHandler(ToolHandler):
    """Run pytest on specified test files via filesystem service exec."""

    _user_id: str = ""

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Run pytest on test files. Returns pass/fail summary with first failure details. "
            "Parameters: test_files (list), test_pattern (string, e.g. 'test_foo'), timeout (int, default 60)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "test_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of test file paths to run",
                },
                "test_pattern": {
                    "type": "string",
                    "description": "Pattern to match test functions (e.g. 'test_foo')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60)",
                },
                "service": {
                    "type": "string",
                    "description": "Filesystem service name (optional)",
                },
            },
            "required": ["test_files"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        test_files = arguments.get("test_files", [])
        test_pattern = arguments.get("test_pattern", "")
        timeout = arguments.get("timeout", 60)
        service_name = arguments.get("service", "")

        if not test_files:
            return "Error: no test files specified"

        from core.handlers._fs_base import find_fs_service
        svc = find_fs_service(self._user_id, service_name)
        if not svc:
            return "Error: no filesystem service available to run tests"

        # Build pytest command
        files_str = " ".join(f'"{f}"' for f in test_files)
        cmd = f"python -m pytest {files_str} -x -q --tb=short --no-header"
        if test_pattern:
            cmd += f" -k \"{test_pattern}\""

        try:
            result = svc.exec(".", cmd, timeout)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            rc = result.get("returncode", -1)
            output = stdout
            if stderr:
                output += "\n" + stderr
            # Truncate to 3000 chars
            if len(output) > 3000:
                output = output[:3000] + "\n... (truncated)"
            status = "PASSED" if rc == 0 else "FAILED"
            return f"Tests {status} (exit code {rc}):\n{output}"
        except Exception as e:
            return f"Error running tests: {e}"


class ReadParentContextHandler(ToolHandler):
    """Read messages from the parent conversation (for sub-agents)."""

    _parent_conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "read_parent_context"

    @property
    def description(self) -> str:
        return (
            "Read recent messages from the parent conversation that spawned "
            "this agent. Use when you need more context about the overall "
            "discussion."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "last_n": {
                    "type": "integer",
                    "description": "Number of recent messages to read (default 20)",
                },
            },
        }

    def set_parent_conversation_id(self, cid: str):
        self._parent_conversation_id = cid

    def set_conversation_id(self, cid: str):
        # Auto-detect parent from sub-conv ID (parent::task::tid)
        if "::task" in cid:
            self._parent_conversation_id = cid.split("::task")[0]

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._parent_conversation_id:
            return ("No parent conversation available (this agent was not "
                    "spawned from a conversation).")

        last_n = arguments.get("last_n", 20)
        try:
            from core.conversation_store import ConversationStore
            store = ConversationStore.instance()
            raw = store.load(self._parent_conversation_id,
                             user_id=self._user_id) or []
            non_system = [m for m in raw if m.get("role") != "system"]
            recent = non_system[-last_n:]
            lines = []
            for m in recent:
                role = m.get("role", "?")
                content = m.get("content", "")[:300]
                lines.append(f"[{role}] {content}")
            return "\n\n".join(lines) if lines else (
                "(no messages in parent conversation)")
        except Exception as e:
            return f"Error reading parent context: {e}"


class SecurityScanHandler(ToolHandler):
    """Run security scans on code via bandit or semgrep."""

    _user_id: str = ""

    @property
    def name(self) -> str:
        return "security_scan"

    @property
    def description(self) -> str:
        return (
            "Run a security scan on Python code files. "
            "Uses bandit (Python-specific) or semgrep (multi-language) via the filesystem exec. "
            "Returns findings with severity, file, line, and description."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File or directory to scan"},
                "tool": {"type": "string", "description": "'bandit' (default) or 'semgrep'"},
                "service": {"type": "string", "description": "Filesystem service"},
            },
            "required": ["path"],
        }

    def set_user_id(self, uid: str):
        self._user_id = uid

    def execute(self, arguments: Dict[str, Any]) -> str:
        path = arguments.get("path", ".")
        tool = arguments.get("tool", "bandit")
        service_name = arguments.get("service", "")

        from core.handlers._fs_base import find_fs_service
        svc = find_fs_service(self._user_id, service_name)
        if not svc:
            return "Error: no filesystem service available"

        try:
            if tool == "semgrep":
                result = svc.exec(".", f"semgrep scan --json {path}", 120)
            else:
                result = svc.exec(".", f"python -m bandit -r -f json {path}", 60)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            rc = result.get("returncode", -1)
            if not stdout and stderr:
                return f"Scan error: {stderr[:500]}"
            # Parse JSON output for summary
            try:
                import json
                data = json.loads(stdout)
                if tool == "bandit":
                    results = data.get("results", [])
                    if not results:
                        return "No security issues found."
                    lines = [f"Found {len(results)} issue(s):"]
                    for r in results[:20]:
                        sev = r.get("issue_severity", "?")
                        fname = r.get("filename", "?")
                        line = r.get("line_number", "?")
                        text = r.get("issue_text", "?")
                        lines.append(f"  [{sev}] {fname}:{line} — {text}")
                    return "\n".join(lines)
                return stdout[:2000]
            except Exception:
                return stdout[:2000] if stdout else f"Exit {rc}: {stderr[:500]}"
        except Exception as e:
            return f"Error running {tool}: {e}"
