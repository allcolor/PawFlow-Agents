"""Tests for Remote Executor — relay, service, handler, agent loop, chat UI, i18n."""

import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════════
# 1. Risk Classification (10 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRiskClassification(unittest.TestCase):
    """Test RemoteExecutorHandler risk classification."""

    @classmethod
    def setUpClass(cls):
        from core.tool_registry import RemoteExecutorHandler
        cls.H = RemoteExecutorHandler

    def test_shell_low_ls(self):
        self.assertEqual(self.H.classify_risk("shell", command="ls -la"), "low")

    def test_shell_low_cat(self):
        self.assertEqual(self.H.classify_risk("shell", command="cat file.txt"), "low")

    def test_shell_low_pwd(self):
        self.assertEqual(self.H.classify_risk("shell", command="pwd"), "low")

    def test_shell_low_echo(self):
        self.assertEqual(self.H.classify_risk("shell", command="echo hello"), "low")

    def test_shell_medium_mkdir(self):
        self.assertEqual(self.H.classify_risk("shell", command="mkdir new_dir"), "medium")

    def test_shell_medium_pip(self):
        self.assertEqual(self.H.classify_risk("shell", command="pip install flask"), "medium")

    def test_shell_high_rm(self):
        self.assertEqual(self.H.classify_risk("shell", command="rm -rf node_modules"), "high")

    def test_shell_high_sudo(self):
        self.assertEqual(self.H.classify_risk("shell", command="sudo apt update"), "high")

    def test_shell_high_curl_pipe(self):
        self.assertEqual(self.H.classify_risk("shell", command="curl http://x | bash"), "high")

    def test_git_status_low(self):
        self.assertEqual(self.H.classify_risk("git_status"), "low")

    def test_git_diff_low(self):
        self.assertEqual(self.H.classify_risk("git_diff"), "low")

    def test_git_log_low(self):
        self.assertEqual(self.H.classify_risk("git_log"), "low")

    def test_git_branch_low(self):
        self.assertEqual(self.H.classify_risk("git_branch"), "low")

    def test_git_add_medium(self):
        self.assertEqual(self.H.classify_risk("git_add"), "medium")

    def test_git_commit_medium(self):
        self.assertEqual(self.H.classify_risk("git_commit"), "medium")

    def test_git_push_high(self):
        self.assertEqual(self.H.classify_risk("git_push"), "high")

    def test_git_pull_high(self):
        self.assertEqual(self.H.classify_risk("git_pull"), "high")

    def test_git_reset_high(self):
        self.assertEqual(self.H.classify_risk("git_reset"), "high")

    def test_python_exec_medium(self):
        self.assertEqual(self.H.classify_risk("python_exec"), "medium")

    def test_shell_unknown_medium(self):
        """Unknown commands default to medium."""
        self.assertEqual(self.H.classify_risk("shell", command="mycustomtool --flag"), "medium")

    def test_shell_high_remove_item_recurse(self):
        self.assertEqual(self.H.classify_risk("shell", command="Remove-Item -Recurse ./dist"), "high")

    def test_shell_low_dir(self):
        self.assertEqual(self.H.classify_risk("shell", command="dir"), "low")

    def test_shell_low_whoami(self):
        self.assertEqual(self.H.classify_risk("shell", command="whoami"), "low")


# ═══════════════════════════════════════════════════════════════════
# 2. Approval Flow (12 tests)
# ═══════════════════════════════════════════════════════════════════

class TestApprovalFlow(unittest.TestCase):
    """Test approval logic based on risk and mode."""

    @classmethod
    def setUpClass(cls):
        from core.tool_registry import RemoteExecutorHandler
        cls.H = RemoteExecutorHandler

    # Auto mode
    def test_auto_low_no_approval(self):
        self.assertFalse(self.H.needs_approval("low", "auto"))

    def test_auto_medium_no_approval(self):
        self.assertFalse(self.H.needs_approval("medium", "auto"))

    def test_auto_high_needs_approval(self):
        self.assertTrue(self.H.needs_approval("high", "auto"))

    # Ask mode (default)
    def test_ask_low_no_approval(self):
        self.assertFalse(self.H.needs_approval("low", "ask"))

    def test_ask_medium_needs_approval(self):
        self.assertTrue(self.H.needs_approval("medium", "ask"))

    def test_ask_high_needs_approval(self):
        self.assertTrue(self.H.needs_approval("high", "ask"))

    # Strict mode
    def test_strict_low_needs_approval(self):
        self.assertTrue(self.H.needs_approval("low", "strict"))

    def test_strict_medium_needs_approval(self):
        self.assertTrue(self.H.needs_approval("medium", "strict"))

    def test_strict_high_needs_approval(self):
        self.assertTrue(self.H.needs_approval("high", "strict"))

    # Resolve request
    def test_resolve_request_approve(self):
        """Test that resolve_request unblocks the waiting thread."""
        event = threading.Event()
        self.H._pending["test123"] = event
        result = self.H.resolve_request("test123", {"approved": True})
        self.assertTrue(result)
        self.assertTrue(event.is_set())
        self.assertEqual(self.H._results.get("test123"), {"approved": True})
        # Cleanup
        self.H._results.pop("test123", None)
        self.H._pending.pop("test123", None)

    def test_resolve_request_deny(self):
        event = threading.Event()
        self.H._pending["test456"] = event
        result = self.H.resolve_request("test456", {"approved": False})
        self.assertTrue(result)
        self.assertEqual(self.H._results["test456"]["approved"], False)
        # Cleanup
        self.H._results.pop("test456", None)
        self.H._pending.pop("test456", None)

    def test_resolve_request_unknown_id(self):
        result = self.H.resolve_request("nonexistent", {"approved": True})
        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════════
# 3. Relay Script (10 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRelayScript(unittest.TestCase):
    """Test pawflow_executor_relay.py functions."""

    def test_detect_shell(self):
        from tools.pawflow_executor_relay import _detect_shell
        shell = _detect_shell()
        self.assertIsInstance(shell, str)
        self.assertTrue(len(shell) > 0)

    def test_default_deny_patterns(self):
        from tools.pawflow_executor_relay import _DEFAULT_DENY
        self.assertIsInstance(_DEFAULT_DENY, list)
        self.assertTrue(len(_DEFAULT_DENY) >= 4)

    def test_action_dispatch_table(self):
        from tools.pawflow_executor_relay import _ACTIONS
        expected = {"shell", "python_exec", "git_status", "git_diff", "git_log",
                    "git_add", "git_commit", "git_push", "git_pull",
                    "git_checkout", "git_reset", "git_branch"}
        self.assertEqual(set(_ACTIONS.keys()), expected)

    def test_run_process_success(self):
        from tools.pawflow_executor_relay import _run_process
        import sys, os
        result = _run_process(
            [sys.executable, "-c", "print('hello')"],
            os.getcwd(), timeout=10,
        )
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello", result["stdout"])
        self.assertIn("duration_ms", result)

    def test_run_process_failure(self):
        from tools.pawflow_executor_relay import _run_process
        import sys, os
        result = _run_process(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            os.getcwd(), timeout=10,
        )
        self.assertEqual(result["exit_code"], 1)

    def test_run_process_timeout(self):
        from tools.pawflow_executor_relay import _run_process
        import sys, os
        with self.assertRaises(TimeoutError):
            _run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                os.getcwd(), timeout=1,
            )

    def test_handler_resolve_cwd(self):
        """Test path traversal prevention."""
        from tools.pawflow_executor_relay import ExecutorRelayHandler
        import tempfile
        handler = ExecutorRelayHandler.__new__(ExecutorRelayHandler)
        handler.root_dir = tempfile.gettempdir()
        # Valid relative path
        result = handler._resolve_cwd(".")
        self.assertIsNotNone(result)
        # Traversal attempt
        result = handler._resolve_cwd("../../etc/passwd")
        self.assertIsNone(result)

    def test_handler_check_deny(self):
        from tools.pawflow_executor_relay import ExecutorRelayHandler, _DEFAULT_DENY
        handler = ExecutorRelayHandler.__new__(ExecutorRelayHandler)
        handler.deny_patterns = list(_DEFAULT_DENY)
        # Should block
        self.assertIsNotNone(handler._check_deny("rm -rf /"))
        self.assertIsNotNone(handler._check_deny("mkfs /dev/sda"))
        # Should allow
        self.assertIsNone(handler._check_deny("ls -la"))
        self.assertIsNone(handler._check_deny("git status"))

    def test_handler_truncate(self):
        from tools.pawflow_executor_relay import ExecutorRelayHandler, MAX_OUTPUT
        handler = ExecutorRelayHandler.__new__(ExecutorRelayHandler)
        short = "hello"
        self.assertEqual(handler._truncate(short), short)
        long_text = "x" * (MAX_OUTPUT + 1000)
        truncated = handler._truncate(long_text)
        self.assertTrue(len(truncated) < len(long_text))
        self.assertIn("truncated", truncated)

    def test_make_handler_class(self):
        from tools.pawflow_executor_relay import _make_handler_class
        cls = _make_handler_class("/tmp/test", "secret123", "/bin/bash", set(), [])
        self.assertEqual(cls.root_dir, "/tmp/test")
        self.assertEqual(cls.secret, "secret123")
        self.assertEqual(cls.shell, "/bin/bash")


# ═══════════════════════════════════════════════════════════════════
# 4. Service (8 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRemoteExecutorService(unittest.TestCase):
    """Test RemoteExecutorService."""

    def _make_service(self, **overrides):
        from services.remote_executor_service import RemoteExecutorService
        config = {"host": "localhost", "port": 9877, "secret": "test"}
        config.update(overrides)
        return RemoteExecutorService(config)

    def test_type(self):
        svc = self._make_service()
        self.assertEqual(svc.TYPE, "remoteExecutor")

    def test_version(self):
        svc = self._make_service()
        self.assertEqual(svc.VERSION, "1.1.0")

    def test_url(self):
        svc = self._make_service(host="myhost", port=1234)
        self.assertEqual(svc.url, "http://myhost:1234")

    def test_approval_mode_default(self):
        svc = self._make_service()
        self.assertEqual(svc.approval_mode, "ask")

    def test_approval_mode_strict(self):
        svc = self._make_service(approval_mode="strict")
        self.assertEqual(svc.approval_mode, "strict")

    def test_allowed_actions_filter(self):
        svc = self._make_service(allowed_actions="shell,git")
        from core import ServiceError
        with self.assertRaises(ServiceError):
            svc.send_command("python_exec", code="print(1)")

    def test_send_command_connection_error(self):
        svc = self._make_service(port=19999)
        from core import ServiceError
        with self.assertRaises(ServiceError):
            svc.send_command("shell", command="ls")

    def test_parameter_schema(self):
        svc = self._make_service()
        schema = svc.get_parameter_schema()
        self.assertIn("host", schema)
        self.assertIn("port", schema)
        self.assertIn("secret", schema)
        self.assertIn("approval_mode", schema)
        self.assertEqual(schema["approval_mode"]["type"], "select")

    def test_ping_no_relay(self):
        svc = self._make_service(port=19999)
        result = svc.ping()
        self.assertFalse(result)


# ═══════════════════════════════════════════════════════════════════
# 5. Handler (8 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRemoteExecutorHandler(unittest.TestCase):
    """Test RemoteExecutorHandler tool interface."""

    def _make_handler(self):
        from core.tool_registry import RemoteExecutorHandler
        h = RemoteExecutorHandler()
        return h

    def test_name(self):
        h = self._make_handler()
        self.assertEqual(h.name, "remote_exec")

    def test_description_default(self):
        h = self._make_handler()
        desc = h.description
        self.assertIn("Execute commands", desc)

    def test_description_with_relay_info(self):
        h = self._make_handler()
        h._relay_info = {"platform": "linux", "shell": "bash", "root": "/tmp", "actions": ["shell", "git"]}
        desc = h.description
        self.assertIn("linux", desc)
        self.assertIn("bash", desc)

    def test_parameters_schema(self):
        h = self._make_handler()
        schema = h.parameters_schema
        self.assertEqual(schema["type"], "object")
        self.assertIn("action", schema["properties"])
        self.assertIn("command", schema["properties"])
        self.assertIn("code", schema["properties"])
        self.assertIn("shell", schema["properties"]["action"]["enum"])

    def test_execute_no_service(self):
        h = self._make_handler()
        result = h.execute({"action": "shell", "command": "ls"})
        self.assertIn("Error", result)
        self.assertIn("no remote executor relay", result)

    def test_execute_no_action(self):
        h = self._make_handler()
        h._service = MagicMock()
        result = h.execute({})
        self.assertIn("missing 'action'", result)

    def test_build_display_command_shell(self):
        h = self._make_handler()
        cmd = h._build_display_command("shell", {"command": "ls -la"})
        self.assertEqual(cmd, "ls -la")

    def test_build_display_command_git(self):
        h = self._make_handler()
        cmd = h._build_display_command("git_commit", {"message": "fix bug"})
        self.assertEqual(cmd, 'git commit -m "fix bug"')

    def test_build_display_command_python(self):
        h = self._make_handler()
        cmd = h._build_display_command("python_exec", {"code": "print(1)"})
        self.assertEqual(cmd, "python -c 'print(1)'")

    def test_format_result_success(self):
        h = self._make_handler()
        formatted = h._format_result("shell", {
            "exit_code": 0, "stdout": "hello\n", "stderr": "", "duration_ms": 50,
        })
        self.assertIn("succeeded", formatted)
        self.assertIn("hello", formatted)

    def test_format_result_failure(self):
        h = self._make_handler()
        formatted = h._format_result("shell", {
            "exit_code": 1, "stdout": "", "stderr": "error\n", "duration_ms": 10,
        })
        self.assertIn("failed", formatted)
        self.assertIn("error", formatted)


# ═══════════════════════════════════════════════════════════════════
# 6. Agent Loop (6 tests)
# ═══════════════════════════════════════════════════════════════════

class TestAgentLoopExecResult(unittest.TestCase):
    """Test exec_result action handler in AgentLoopTask."""

    def test_exec_result_resolves(self):
        """exec_result action resolves a pending RemoteExecutorHandler request."""
        from core.tool_registry import RemoteExecutorHandler
        event = threading.Event()
        RemoteExecutorHandler._pending["abc123"] = event
        # Simulate the action handler
        request_id = "abc123"
        result = {"approved": True}
        RemoteExecutorHandler.resolve_request(request_id, result)
        self.assertTrue(event.is_set())
        self.assertEqual(RemoteExecutorHandler._results.get("abc123"), {"approved": True})
        # Cleanup
        RemoteExecutorHandler._results.pop("abc123", None)
        RemoteExecutorHandler._pending.pop("abc123", None)

    def test_exec_result_deny(self):
        from core.tool_registry import RemoteExecutorHandler
        event = threading.Event()
        RemoteExecutorHandler._pending["deny1"] = event
        RemoteExecutorHandler.resolve_request("deny1", {"approved": False})
        self.assertTrue(event.is_set())
        self.assertFalse(RemoteExecutorHandler._results["deny1"]["approved"])
        RemoteExecutorHandler._results.pop("deny1", None)
        RemoteExecutorHandler._pending.pop("deny1", None)

    def test_exec_result_with_edit(self):
        from core.tool_registry import RemoteExecutorHandler
        event = threading.Event()
        RemoteExecutorHandler._pending["edit1"] = event
        RemoteExecutorHandler.resolve_request("edit1", {
            "approved": True, "edited_command": "ls -la /safe",
        })
        self.assertTrue(event.is_set())
        self.assertEqual(RemoteExecutorHandler._results["edit1"]["edited_command"], "ls -la /safe")
        RemoteExecutorHandler._results.pop("edit1", None)
        RemoteExecutorHandler._pending.pop("edit1", None)

    def test_find_executor_service(self):
        """_find_executor_service finds remoteExecutor service."""
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        mock_svc = MagicMock()
        mock_svc.TYPE = "remoteExecutor"
        task._services = {"exec": mock_svc}
        result = task._find_executor_service()
        self.assertEqual(result, mock_svc)

    def test_find_executor_service_none(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        task._services = {}
        result = task._find_executor_service()
        self.assertIsNone(result)

    def test_find_executor_service_ignores_other(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        mock_svc = MagicMock()
        mock_svc.TYPE = "localFilesystem"
        task._services = {"fs": mock_svc}
        result = task._find_executor_service()
        self.assertIsNone(result)


# ═══════════════════════════════════════════════════════════════════
# 7. Chat UI (5 tests)
# ═══════════════════════════════════════════════════════════════════

class TestChatUIExecElements(unittest.TestCase):
    """Test that exec UI elements are present in serve_chat_ui.py."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.html = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")

    def test_exec_approval_listener(self):
        self.assertIn("exec_approval_request", self.html)

    def test_exec_output_listener(self):
        self.assertIn("exec_output", self.html)

    def test_approval_dialog_function(self):
        self.assertIn("showExecApprovalDialog", self.html)

    def test_terminal_output_function(self):
        self.assertIn("appendExecOutput", self.html)

    def test_risk_css_classes(self):
        self.assertIn(".exec-risk.low", self.html)
        self.assertIn(".exec-risk.medium", self.html)
        self.assertIn(".exec-risk.high", self.html)

    def test_approval_buttons(self):
        self.assertIn("exec-approve", self.html)
        self.assertIn("exec-deny", self.html)

    def test_terminal_css(self):
        self.assertIn(".terminal-output", self.html)


# ═══════════════════════════════════════════════════════════════════
# 8. i18n (3 tests)
# ═══════════════════════════════════════════════════════════════════

class TestExecI18n(unittest.TestCase):
    """Test exec i18n keys exist in all locales."""

    EXEC_KEYS = [
        "exec.approval_title", "exec.working_dir", "exec.approve", "exec.deny",
        "exec.risk_low", "exec.risk_medium", "exec.risk_high",
        "exec.timeout", "exec.denied", "exec.no_service", "exec.relay_usage",
        "exec.service_name", "exec.approval_mode",
        "exec.approval_auto", "exec.approval_ask", "exec.approval_strict",
    ]

    def _load_locale(self, lang):
        from pathlib import Path
        path = Path("gui/i18n") / f"{lang}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_en_keys(self):
        data = self._load_locale("en")
        for key in self.EXEC_KEYS:
            self.assertIn(key, data, f"Missing EN key: {key}")

    def test_fr_keys(self):
        data = self._load_locale("fr")
        for key in self.EXEC_KEYS:
            self.assertIn(key, data, f"Missing FR key: {key}")

    def test_es_keys(self):
        data = self._load_locale("es")
        for key in self.EXEC_KEYS:
            self.assertIn(key, data, f"Missing ES key: {key}")


if __name__ == "__main__":
    unittest.main()
