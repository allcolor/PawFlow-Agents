"""Auto-extracted from core/tool_registry.py — see core/handlers/__init__.py"""

import json
import logging
import os
import re
import shlex
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
    _conversation_id: str = ""

    @property
    def name(self) -> str:
        return "run_tests"

    @property
    def description(self) -> str:
        return (
            "Run pytest on test files. Returns pass/fail summary with first failure details. "
            "Parameters: test_files (list), test_pattern (string, e.g. 'test_foo'), timeout (int, optional — no timeout by default), max_output (int, optional)."
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
                    "description": "Timeout in seconds. If omitted, no timeout is enforced (tests run to completion).",
                },
                "max_output": {
                    "type": "integer",
                    "description": "Max output characters (default: 3000, max: 150000).",
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

    def set_conversation_id(self, conversation_id: str):
        self._conversation_id = conversation_id

    def _rtk_enabled(self, arguments: Dict[str, Any]) -> bool:
        """Return whether run_tests should use RTK output."""
        from core.handlers._fs_base import _truthy

        secret_env = arguments.get("_secret_env") or {}
        if "PAWFLOW_USE_RTK" in secret_env:
            return _truthy(secret_env.get("PAWFLOW_USE_RTK"))
        if "PAWFLOW_USE_RTK" in os.environ:
            return _truthy(os.environ.get("PAWFLOW_USE_RTK"))
        try:
            from core.expression import resolve_expression
            raw = resolve_expression(
                "$" + "{" + "PAWFLOW_USE_RTK:default(\"\")" + "}",
                owner=self._user_id,
                conversation_id=self._conversation_id,
            )
        except Exception:
            raw = ""
        return _truthy(raw)

    def _maybe_rewrite_with_rtk(self, svc, cmd: str,
                                arguments: Dict[str, Any], timeout) -> str:
        """Best-effort RTK rewrite for pytest commands."""
        if not self._rtk_enabled(arguments):
            return cmd
        try:
            rewrite_cmd = "rtk rewrite " + shlex.quote(cmd)
            if "timeout" in arguments:
                result = svc.exec(".", rewrite_cmd, timeout)
            else:
                result = svc.exec(".", rewrite_cmd)
        except Exception as exc:
            logger.debug("[run_tests] RTK rewrite failed; using raw command: %s", exc)
            return cmd
        if result.get("returncode", 0) != 0:
            logger.debug(
                "[run_tests] RTK rewrite unsupported rc=%s; using raw command",
                result.get("returncode"),
            )
            return cmd
        lines = [
            line.strip()
            for line in str(result.get("stdout", "")).splitlines()
            if line.strip() and not line.strip().startswith("[rtk]")
        ]
        return lines[-1] if lines else cmd


    def execute(self, arguments: Dict[str, Any]) -> str:
        from core.handlers._arg_normalize import normalize_string_list
        test_files = normalize_string_list(arguments.get("test_files"))
        test_pattern = arguments.get("test_pattern", "")
        timeout = arguments.get("timeout")
        if timeout is not None:
            try:
                timeout = max(1, int(timeout))
            except (TypeError, ValueError):
                timeout = None
        service_name = arguments.get("service", "")
        try:
            max_output = int(arguments.get("max_output", 3000) or 3000)
        except (TypeError, ValueError):
            max_output = 3000
        max_output = max(1, min(max_output, 150000))

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

        cmd = self._maybe_rewrite_with_rtk(svc, cmd, arguments, timeout)

        try:
            if "timeout" in arguments:
                result = svc.exec(".", cmd, timeout)
            else:
                result = svc.exec(".", cmd)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")
            rc = result.get("returncode", -1)
            output = stdout
            if stderr:
                output += "\n" + stderr
            if len(output) > max_output:
                output = output[:max_output] + "\n... (truncated)"
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
