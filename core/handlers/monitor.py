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

# Markers the shell emits so reason and exit code are *reported* rather than
# guessed from the output text.
_STATUS_PREFIX = "__PAWFLOW_MON__"
_NOMATCH_MARKER = "[monitor] pattern never matched - last lines of raw output:"
_CMD_HEREDOC = "__PAWFLOW_MON_CMD__"
# Margin so our own deadline always fires before the bash layer's, which would
# kill the watcher and lose everything captured so far.
_BASH_TIMEOUT_MARGIN_S = 15

# Why a watcher loop and not `cmd | grep | head`:
#
#   - A downstream stage exiting never stops the producer: the shell waits for
#     every member of a pipeline. Measured - awk matched and exited instantly,
#     the stage still took the producer's full runtime.
#   - grep only learns its reader is gone when it writes again. For a pattern
#     that matches once, that second write never comes, so nothing ever gets
#     SIGPIPE. Control experiment on a 60s producer: a pattern matching once
#     ran to the timeout; the same pipeline with a pattern matching every line
#     returned in 2s.
#
# So the more selective the pattern, the longer it hung - the tool failed
# precisely in the case it advertises ('FAILED', 'listening on port').
#
# The command therefore runs in its own session (setsid) with its pid
# recorded, output goes to a file, and a watcher polls that file and kills the
# whole process group - the sleeps included - as soon as the pattern is
# satisfied or the deadline passes. The file survives either way, so a timeout
# still returns what was captured.
_MONITOR_SCRIPT = r"""
__mon_out=$(mktemp) || exit 1
__mon_pf=$(mktemp) || exit 1
__mon_cmd=$(mktemp) || exit 1
__mon_wrap=$(mktemp) || exit 1

cat > "$__mon_cmd" <<'@HEREDOC@'
@COMMAND@
@HEREDOC@

cat > "$__mon_wrap" <<__PAWFLOW_MON_WRAP__
echo \$\$ > "$__mon_pf"
exec sh "$__mon_cmd"
__PAWFLOW_MON_WRAP__

setsid sh "$__mon_wrap" > "$__mon_out" 2>&1 &
__mon_wait=$!
__mon_start=$(date +%s)
__mon_reason=exit
__mon_interval=0.2

while true; do
  if [ -n @PATTERN@ ]; then
    __mon_n=$(grep -c -m @LIMIT@ -E -- @PATTERN@ "$__mon_out" 2>/dev/null)
    [ -z "$__mon_n" ] && __mon_n=0
    if [ "$__mon_n" -ge @LIMIT@ ]; then __mon_reason=match; break; fi
  fi
  kill -0 "$__mon_wait" 2>/dev/null || { __mon_reason=exit; break; }
  __mon_elapsed=$(( $(date +%s) - $__mon_start ))
  if [ "$__mon_elapsed" -ge @TIMEOUT_S@ ]; then __mon_reason=timeout; break; fi
  # Cheap early, calmer later: a 10-minute watch must not rescan 3000 times.
  [ "$__mon_elapsed" -ge 10 ] && __mon_interval=1
  sleep "$__mon_interval"
done

if [ "$__mon_reason" != exit ]; then
  __mon_pgid=$(cat "$__mon_pf" 2>/dev/null)
  [ -n "$__mon_pgid" ] && kill -TERM "-$__mon_pgid" 2>/dev/null
fi

wait "$__mon_wait" 2>/dev/null
__mon_rc=$?

echo "@STATUS@ reason=$__mon_reason rc=$__mon_rc"
if [ -n @PATTERN@ ]; then
  __mon_hits=$(grep -E -- @PATTERN@ "$__mon_out" 2>/dev/null | head -n @LIMIT@)
  if [ -n "$__mon_hits" ]; then
    printf '%s\n' "$__mon_hits"
  else
    echo "@NOMATCH@"
    tail -n @LINE_LIMIT@ "$__mon_out"
  fi
else
  head -n @LINE_LIMIT@ "$__mon_out"
fi

rm -f "$__mon_out" "$__mon_pf" "$__mon_cmd" "$__mon_wrap"
"""


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
            "'FAILED', 'listening on port', etc.). Returning early kills the "
            "command's whole process group, and the result header states "
            "reason=match|exit|timeout plus exit_code= when the command ended "
            "on its own. A timeout still returns what was captured. For truly "
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

        # The command travels to the container inside a quoted heredoc, so it
        # arrives verbatim - no escaping, quotes and newlines intact. The one
        # thing that would break out is the delimiter itself.
        if _CMD_HEREDOC in command:
            return (
                f"Error: 'command' must not contain {_CMD_HEREDOC!r} - it is "
                "the internal heredoc delimiter."
            )

        timeout_s = max(1, timeout_ms // 1000)
        shell_cmd = (
            _MONITOR_SCRIPT
            .replace("@HEREDOC@", _CMD_HEREDOC)
            .replace("@COMMAND@", command)
            .replace("@PATTERN@", shlex.quote(pattern))
            .replace("@LIMIT@", str(max(limit, 1)))
            .replace("@LINE_LIMIT@", str(max(line_limit, 1)))
            .replace("@TIMEOUT_S@", str(timeout_s))
            .replace("@STATUS@", _STATUS_PREFIX)
            .replace("@NOMATCH@", _NOMATCH_MARKER)
        )

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
            "timeout": timeout_s + _BASH_TIMEOUT_MARGIN_S,
            # This is a generated multi-line script; an RTK rewrite would keep
            # only its last line.
            "_skip_rtk": True,
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

        # The shell reports reason and exit code on its own line. Inferring
        # them from the output text was guesswork that read a build printing
        # the words "timed out" as a Monitor timeout.
        reason = ""
        exit_code = None
        body: list = []
        for line in result.splitlines():
            if line.startswith(_STATUS_PREFIX):
                for field in line[len(_STATUS_PREFIX):].split():
                    key, _, value = field.partition("=")
                    if key == "reason":
                        reason = value
                    elif key == "rc" and value.lstrip("-").isdigit():
                        exit_code = int(value)
            else:
                body.append(line)
        result = "\n".join(body).strip("\n")
        captured_lines = [line for line in body if line.strip()]

        if not reason:
            # No status line: the bash layer killed the watcher, or the
            # command never started. Say so instead of claiming an exit.
            logger.warning("[monitor] no status line after %dms", elapsed_ms)
            reason = "unknown"

        header = (
            f"[monitor] reason={reason} elapsed_ms={elapsed_ms} "
            f"lines={len(captured_lines)}"
        )
        if exit_code is not None and reason == "exit":
            header += f" exit_code={exit_code}"
        if pattern:
            header += f" pattern={pattern!r} limit={limit}"
        return header + "\n" + result


__all__ = ["MonitorHandler"]
