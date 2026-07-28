"""Tests for core.handlers.monitor.MonitorHandler.

Pawflow replacement for the Claude Code built-in `Monitor`. Wraps a
relay bash invocation with a regex/line-cap/timeout-bounded shell
pipeline so the agent gets a single bounded result instead of streaming.
"""

import os
import subprocess  # nosec B404
import time
import unittest
from unittest.mock import patch, MagicMock

from core.handlers.monitor import MonitorHandler, _BASH_TIMEOUT_MARGIN_S


class TestMonitorHandler(unittest.TestCase):

    def setUp(self):
        self.h = MonitorHandler()
        self.h.set_conversation_id("conv-abc")
        self.h.set_user_id("alice")

    def test_name_matches_cc_builtin(self):
        assert self.h.name == "Monitor"

    def test_schema_requires_command(self):
        sch = self.h.parameters_schema
        assert sch["required"] == ["command"]
        assert "pattern" in sch["properties"]
        assert "limit" in sch["properties"]
        assert "line_limit" in sch["properties"]
        assert "timeout_ms" in sch["properties"]

    def test_execute_missing_command_errors(self):
        res = self.h.execute({})
        assert res.startswith("Error:")
        assert "command" in res.lower()

    def test_execute_blank_command_errors(self):
        res = self.h.execute({"command": "   "})
        assert res.startswith("Error:")

    def _captured_bash(self, output: str = ""):
        bash = MagicMock()
        bash.execute.return_value = output
        return bash

    def _run_for_real(self, arguments):
        """Build the script through the handler, then actually run it.

        The old tests mocked bash and asserted the command *string* — they
        passed against a tool that hung on every selective pattern. Nothing
        short of executing the script can tell you it terminates.
        """
        bash = MagicMock()

        def _real(args):
            proc = subprocess.run(  # nosec B603
                ["sh", "-c", args["command"]],
                capture_output=True, text=True,
                timeout=args.get("timeout", 60),
            )
            return proc.stdout

        bash.execute.side_effect = _real
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            return self.h.execute(arguments)

    def test_execute_caps_timeout_to_max(self):
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "sleep 9999",
                            "timeout_ms": 9_999_999_999})
        bash_args = bash.execute.call_args[0][0]
        # _MAX_TIMEOUT_MS is 10 minutes -> 600s, plus the margin that lets the
        # watcher's own deadline fire first and report a real reason.
        assert bash_args["timeout"] == 600 + _BASH_TIMEOUT_MARGIN_S

    def test_execute_opts_out_of_rtk_rewriting(self):
        # RTK keeps only the last line of its output as the command, so a
        # rewrite would reduce the whole watcher script to its final `rm -f`.
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "true"})
        bash_args = bash.execute.call_args[0][0]
        assert bash_args["_skip_rtk"] is True

    def test_execute_floors_timeout_to_min(self):
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "true", "timeout_ms": 0})
        bash_args = bash.execute.call_args[0][0]
        # Floored to 1000ms -> 1s minimum.
        assert bash_args["timeout"] >= 1

    def test_execute_forwards_relay(self):
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "true", "relay": "fs_main"})
        bash_args = bash.execute.call_args[0][0]
        assert bash_args["relay"] == "fs_main"

    def test_execute_omits_relay_when_blank(self):
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "true"})
        bash_args = bash.execute.call_args[0][0]
        assert "relay" not in bash_args

    def test_execute_propagates_conv_user_to_bash(self):
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "true"})
        bash.set_conversation_id.assert_called_once_with("conv-abc")
        bash.set_user_id.assert_called_once_with("alice")

    def test_execute_bash_exception_returns_error(self):
        bash = MagicMock()
        bash.execute.side_effect = RuntimeError("relay down")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            res = self.h.execute({"command": "true"})
        assert res.startswith("Error:")
        assert "relay down" in res

    def test_a_command_printing_timed_out_is_not_reported_as_a_timeout(self):
        # The reason used to be guessed from the output text, so a build
        # logging the words "timed out" was reported as a Monitor timeout.
        # The shell states the reason now; the text is just text.
        res = self._run_for_real({"command": "echo 'upstream request timed out'"})
        assert "reason=exit" in res
        assert "timed out" in res


class TestMonitorReallyTerminates(unittest.TestCase):
    """Behavioural tests: the script is executed, not just built.

    Every one of these fails against the old `cmd | grep | head` pipeline.
    """

    def setUp(self):
        self.h = MonitorHandler()

    _run_for_real = TestMonitorHandler._run_for_real

    # A 20s producer. Anything that waits for it blows the assertions.
    _PRODUCER = 'for i in $(seq 1 20); do echo "line $i"; sleep 1; done'

    def test_a_pattern_matching_once_returns_early(self):
        # THE regression. A pattern matching a single time — 'FAILED',
        # 'listening on port', the tool's own examples — left grep with no
        # second write, so it never took SIGPIPE from head and the producer
        # ran to completion. The more selective the pattern, the longer the
        # hang: measured 45s on a 3s match before this.
        started = time.monotonic()
        res = self._run_for_real({
            "command": self._PRODUCER,
            "pattern": "line 2$",
            "limit": 1,
            "timeout_ms": 30000,
        })
        elapsed = time.monotonic() - started

        assert "reason=match" in res, res
        assert "line 2" in res, res
        assert elapsed < 12, f"returned in {elapsed:.1f}s; producer runs 20s"

    def test_the_producer_is_killed_not_merely_abandoned(self):
        # Killing the direct child leaves the sleeps orphaned and the shell
        # still waiting on them, so the whole group has to go.
        marker = "/tmp/pawflow_monitor_liveness"  # nosec B108
        started = time.monotonic()
        self._run_for_real({
            "command": (
                f"rm -f {marker}; "
                f'for i in $(seq 1 20); do echo "line $i"; sleep 1; done; '
                f"touch {marker}"
            ),
            "pattern": "line 2$",
            "limit": 1,
            "timeout_ms": 30000,
        })
        elapsed = time.monotonic() - started
        time.sleep(3)

        assert elapsed < 12
        assert not os.path.exists(marker), (
            "the command kept running after Monitor returned"
        )

    def test_a_timeout_still_returns_what_was_captured(self):
        # The bash layer's timeout used to swallow the output and hand back
        # its own error banner, losing every line the command had produced.
        res = self._run_for_real({
            "command": self._PRODUCER,
            "pattern": "NEVER_MATCHES",
            "limit": 1,
            "timeout_ms": 4000,
        })

        assert "reason=timeout" in res, res
        assert "line 1" in res, res

    def test_the_commands_exit_code_is_reported(self):
        # Without it an agent watching a build cannot tell success from
        # failure without parsing the output.
        res = self._run_for_real({"command": "echo done; exit 7"})

        assert "reason=exit" in res, res
        assert "exit_code=7" in res, res

    def test_a_command_containing_quotes_survives_the_trip(self):
        # It travels inside a quoted heredoc, so nothing is escaped by hand.
        res = self._run_for_real({
            "command": """echo "it's fine"; echo '$HOME stays literal'""",
        })

        assert "reason=exit" in res, res
        assert "it's fine" in res, res
        # Single quotes survived the trip, so the shell did not expand this.
        assert "$HOME stays literal" in res, res

    def test_the_heredoc_delimiter_is_refused_rather_than_breaking_out(self):
        res = self.h.execute({"command": "echo __PAWFLOW_MON_CMD__"})

        assert res.startswith("Error:")


if __name__ == "__main__":
    unittest.main()
