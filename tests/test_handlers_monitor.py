"""Tests for core.handlers.monitor.MonitorHandler.

Pawflow replacement for the Claude Code built-in `Monitor`. Wraps a
relay bash invocation with a regex/line-cap/timeout-bounded shell
pipeline so the agent gets a single bounded result instead of streaming.
"""

import unittest
from unittest.mock import patch, MagicMock

from core.handlers.monitor import MonitorHandler


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

    def test_execute_no_pattern_uses_line_limit_head(self):
        bash = self._captured_bash("line1\nline2\nline3\n")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            res = self.h.execute({"command": "echo hi", "line_limit": 50})
        assert bash.execute.called
        bash_args = bash.execute.call_args[0][0]
        assert "head -n 50" in bash_args["command"]
        assert "grep -E" not in bash_args["command"]
        assert "reason=" in res
        assert "elapsed_ms=" in res
        assert "lines=3" in res

    def test_execute_with_pattern_pipes_grep(self):
        bash = self._captured_bash("FAILED: case1\n")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            res = self.h.execute({
                "command": "./run.sh",
                "pattern": "^FAILED",
                "limit": 1,
            })
        bash_args = bash.execute.call_args[0][0]
        assert "grep -E --line-buffered" in bash_args["command"]
        assert "head -n 1" in bash_args["command"]
        assert "reason=" in res

    def test_execute_caps_timeout_to_max(self):
        bash = self._captured_bash("")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            self.h.execute({"command": "sleep 9999",
                            "timeout_ms": 9_999_999_999})
        bash_args = bash.execute.call_args[0][0]
        # _MAX_TIMEOUT_MS is 10 minutes -> 600 seconds.
        assert bash_args["timeout"] <= 600

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

    def test_execute_reason_timeout_when_output_says_timed_out(self):
        bash = self._captured_bash("command timed out after 30s\n")
        with patch("core.handlers.bash.BashHandler", return_value=bash):
            res = self.h.execute({"command": "sleep 99", "timeout_ms": 5000})
        assert "reason=timeout" in res


if __name__ == "__main__":
    unittest.main()
