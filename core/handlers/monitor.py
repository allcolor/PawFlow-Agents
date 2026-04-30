"""Monitor handler — pawflow replacement for the Claude Code built-in
`Monitor`.

Claude Code's built-in streams a command's output live into the agent's
turn. Pawflow's MCP protocol returns tool output synchronously as a
single response, so we give the same semantic via blocking-with-timeout:

  Monitor(command, pattern, timeout_ms) runs ``command`` via the relay
  bash handler, piping stdout|stderr through an optional regex filter,
  and blocks until ONE of:
    - the command exits
    - `pattern` matched ``limit`` lines (or first match if limit=1)
    - `timeout_ms` elapsed

When the agent is monitoring a long-running build, this replaces the
ScheduleWakeup poll anti-pattern: Monitor returns as soon as the pattern
matches, with the matched lines + exit code, instead of the agent
scheduling itself every N seconds to re-check.

For truly long-running watches (hours), use ``bash(run_in_background=True)``
and poll the output file yourself — Monitor is intentionally bounded by
timeout_ms so it never holds a turn open indefinitely.
"""

import logging
import shlex
import time
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)

# Upper bound on timeout_ms. Anything longer than this should go through
# bash(run_in_background=True) so the agent's turn doesn't hang.
_MAX_TIMEOUT_MS = 10 * 60 * 1000  # 10 minutes
_DEFAULT_TIMEOUT_MS = 30 * 1000    # 30 seconds
_DEFAULT_LINE_LIMIT = 200


class MonitorHandler(ToolHandler):
    """Run a command and watch its output with optional pattern matching."""

    _conversation_id: str = ""
    _user_id: str = ""

    @property
    def name(self) -> str:
        return "Monitor"

    @property
    def description(self) -> str:
        return (
            "Run a command via the relay and capture its output, with "
            "optional regex pattern matching to return early. Blocks "
            "until the command exits, the pattern matches `limit` lines, "
            "or `timeout_ms` elapses — whichever comes first. Use this "
            "when you need to watch a long-running process (build, test "
            "suite, deploy) and react as soon as a marker appears ("
            "'FAILED', 'listening on port', etc.). For truly "
            "long-running watches (hours), use bash(run_in_background=true) "
            "+ poll the output file instead — Monitor is capped at 10 min."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run (stdout + stderr both captured).",
                },
                "pattern": {
                    "type": "string",
                    "description": (
                        "Optional POSIX extended regex. When a line matches, "
                        "it's collected; Monitor returns early after `limit` "
                        "matches. Omit to capture raw output up to line_limit."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Max matched lines to collect before returning (default 1). "
                        "Ignored when pattern is empty."
                    ),
                },
                "line_limit": {
                    "type": "integer",
                    "description": (
                        f"Max raw output lines to keep (default {_DEFAULT_LINE_LIMIT}). "
                        f"Prevents runaway buffering."
                    ),
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": (
                        f"Timeout in milliseconds (default {_DEFAULT_TIMEOUT_MS}, "
                        f"max {_MAX_TIMEOUT_MS}). On timeout, returns what was "
                        f"captured so far with reason='timeout'."
                    ),
                },
                "relay": {
                    "type": "string",
                    "description": "Relay service name (same semantics as bash tool).",
                },
                "local": {
                    "type": "boolean",
                    "description": (
                        "If true, execute through the relay host helper on the user's host. "
                        "If false or omitted, execute inside the relay Docker container."
                    ),
                },
            },
            "required": ["command"],
        }

    def set_conversation_id(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id

    def set_user_id(self, user_id: str) -> None:
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        command = (arguments.get("command") or "").strip()
        if not command:
            return "Error: 'command' is required."

        pattern = arguments.get("pattern") or ""
        limit = int(arguments.get("limit", 1) or 1)
        line_limit = int(arguments.get("line_limit", _DEFAULT_LINE_LIMIT)
                         or _DEFAULT_LINE_LIMIT)
        timeout_ms = int(arguments.get("timeout_ms", _DEFAULT_TIMEOUT_MS)
                         or _DEFAULT_TIMEOUT_MS)
        timeout_ms = max(1000, min(timeout_ms, _MAX_TIMEOUT_MS))
        relay = arguments.get("relay") or ""

        # Build a shell pipeline that does the line-capping and pattern
        # filtering INSIDE the relay container. This avoids streaming
        # megabytes back to the server just to filter/trim there.
        #   (command) 2>&1 | [grep -E --line-buffered pattern] | head -n N
        parts = [f"( {command} ) 2>&1"]
        if pattern:
            parts.append(f"grep -E --line-buffered {shlex.quote(pattern)}")
            max_lines = max(limit, 1)
        else:
            max_lines = line_limit
        parts.append(f"head -n {int(max_lines)}")
        shell_cmd = " | ".join(parts)

        # Delegate to BashHandler for the actual relay dispatch. Same
        # security checks (dangerous-command patterns, relay routing).
        from core.handlers.bash import BashHandler
        bash = BashHandler()
        # Copy context (conv/user) into the bash handler so it can
        # route correctly (audit logging, relay auth).
        if hasattr(bash, "set_conversation_id") and self._conversation_id:
            bash.set_conversation_id(self._conversation_id)
        if hasattr(bash, "set_user_id") and self._user_id:
            bash.set_user_id(self._user_id)

        bash_args: Dict[str, Any] = {
            "command": shell_cmd,
            "timeout": max(1, timeout_ms // 1000),
        }
        if relay:
            bash_args["relay"] = relay
        if arguments.get("local", False):
            bash_args["local"] = True

        started_at = time.monotonic()
        try:
            raw = bash.execute(bash_args)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.warning("[monitor] bash raised after %dms: %s", elapsed_ms, exc)
            return (
                f"Error: Monitor bash call failed after {elapsed_ms}ms: "
                f"{type(exc).__name__}: {exc}"
            )

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        result = raw if isinstance(raw, str) else str(raw)
        captured_lines = [
            line for line in result.splitlines()
            if line.strip()
        ]

        # Infer reason. bash's execute returns the command output; a
        # STDERR note from the bash layer or a timeout banner is the
        # only signal we have. Keep this simple and honest.
        reason = "exit"
        if "timed out" in result.lower() or elapsed_ms >= timeout_ms - 100:
            reason = "timeout"
        elif pattern and len(captured_lines) >= max(limit, 1):
            reason = "pattern_match"

        header = (
            f"[monitor] reason={reason} elapsed_ms={elapsed_ms} "
            f"lines={len(captured_lines)}"
        )
        if pattern:
            header += f" pattern={pattern!r} limit={limit}"
        return header + "\n" + result


__all__ = ["MonitorHandler"]
