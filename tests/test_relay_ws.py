"""Tests for WS Reverse relay system — RelayConnectionManager, WS endpoint,
cross-channel services, heartbeat, multi-service, tool approval, i18n."""

import asyncio
import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, AsyncMock


# ═══════════════════════════════════════════════════════════════════
# 1. RelayConnectionManager (15 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRelayConnectionManager(unittest.TestCase):
    """Test RelayConnectionManager singleton and CRUD."""

    def setUp(self):
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()
        self.mgr = RelayConnectionManager.instance()

    def tearDown(self):
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()

    def test_singleton(self):
        from core.relay_manager import RelayConnectionManager
        m1 = RelayConnectionManager.instance()
        m2 = RelayConnectionManager.instance()
        self.assertIs(m1, m2)

    def test_register_and_get(self):
        ws = MagicMock()
        conn = self.mgr.register("user1", "relay1", "executor", ws, {"platform": "linux"})
        self.assertEqual(conn.user_id, "user1")
        self.assertEqual(conn.relay_id, "relay1")
        self.assertEqual(conn.relay_type, "executor")
        got = self.mgr.get("user1", "relay1")
        self.assertIs(got, conn)

    def test_get_by_type(self):
        ws = MagicMock()
        self.mgr.register("user1", "exec1", "executor", ws, {})
        self.mgr.register("user1", "fs1", "filesystem", ws, {})
        got = self.mgr.get("user1", relay_type="filesystem")
        self.assertEqual(got.relay_id, "fs1")

    def test_get_first_available(self):
        ws = MagicMock()
        self.mgr.register("user1", "relay1", "executor", ws, {})
        got = self.mgr.get("user1")
        self.assertEqual(got.relay_id, "relay1")

    def test_get_nonexistent(self):
        self.assertIsNone(self.mgr.get("nobody"))
        self.assertIsNone(self.mgr.get("nobody", "norelay"))

    @patch("core.relay_manager.RelayConnectionManager._auto_install_service")
    @patch("core.relay_manager.RelayConnectionManager._auto_disable_service")
    @patch("core.relay_manager.RelayConnectionManager._notify_disconnect")
    def test_unregister(self, mock_notify, mock_disable, mock_install):
        ws = MagicMock()
        self.mgr.register("user1", "relay1", "executor", ws, {})
        self.assertIsNotNone(self.mgr.get("user1", "relay1"))
        self.mgr.unregister("user1", "relay1")
        self.assertIsNone(self.mgr.get("user1", "relay1"))
        mock_disable.assert_called_once_with("user1", "relay1")
        mock_notify.assert_called_once()

    def test_unregister_nonexistent(self):
        # Should not raise
        self.mgr.unregister("nobody", "norelay")

    def test_list_for_user(self):
        ws = MagicMock()
        self.mgr.register("user1", "r1", "executor", ws, {"platform": "win32"})
        self.mgr.register("user1", "r2", "filesystem", ws, {})
        lst = self.mgr.list_for_user("user1")
        self.assertEqual(len(lst), 2)
        ids = {r["relay_id"] for r in lst}
        self.assertEqual(ids, {"r1", "r2"})

    def test_list_for_user_empty(self):
        self.assertEqual(self.mgr.list_for_user("nobody"), [])

    def test_list_by_type(self):
        ws = MagicMock()
        self.mgr.register("user1", "e1", "executor", ws, {})
        self.mgr.register("user1", "e2", "executor", ws, {})
        self.mgr.register("user1", "f1", "filesystem", ws, {})
        executors = self.mgr.list_by_type("user1", "executor")
        self.assertEqual(len(executors), 2)

    def test_is_connected(self):
        ws = MagicMock()
        self.mgr.register("user1", "relay1", "executor", ws, {})
        self.assertTrue(self.mgr.is_connected("user1", "relay1"))
        self.assertFalse(self.mgr.is_connected("user1", "nope"))
        self.assertTrue(self.mgr.is_connected("user1", relay_type="executor"))
        # Without specifying type, any relay counts as connected
        self.assertTrue(self.mgr.is_connected("user1"))
        self.assertFalse(self.mgr.is_connected("nobody"))

    def test_relay_connection_to_dict(self):
        ws = MagicMock()
        conn = self.mgr.register("user1", "relay1", "executor", ws, {"root": "/tmp"})
        d = conn.to_dict()
        self.assertEqual(d["relay_id"], "relay1")
        self.assertEqual(d["relay_type"], "executor")
        self.assertIn("uptime_seconds", d)
        self.assertEqual(d["info"]["root"], "/tmp")

    def test_resolve_pending_sync(self):
        """Test that resolve_pending works for sync pending requests."""
        ws = MagicMock()
        conn = self.mgr.register("user1", "relay1", "executor", ws, {})

        # Set up a sync pending
        event = threading.Event()
        result_holder = {"data": None, "error": None}
        conn._sync_pending = {"req123": (event, result_holder)}

        self.mgr.resolve_pending("user1", "relay1", "req123", {"exit_code": 0, "stdout": "ok"})

        self.assertTrue(event.is_set())
        self.assertEqual(result_holder["data"]["exit_code"], 0)

    @patch("core.relay_manager.RelayConnectionManager._auto_install_service")
    @patch("core.relay_manager.RelayConnectionManager._auto_disable_service")
    def test_unregister_cancels_pending(self, mock_disable, mock_install):
        ws = MagicMock()
        conn = self.mgr.register("user1", "relay1", "executor", ws, {})

        # Add a pending async future
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        conn._pending["req1"] = future

        self.mgr.unregister("user1", "relay1")
        self.assertTrue(future.cancelled())
        loop.close()

    def test_multi_user_isolation(self):
        ws = MagicMock()
        self.mgr.register("alice", "r1", "executor", ws, {})
        self.mgr.register("bob", "r1", "executor", ws, {})
        self.assertIsNotNone(self.mgr.get("alice", "r1"))
        self.assertIsNotNone(self.mgr.get("bob", "r1"))
        self.mgr.unregister("alice", "r1")
        self.assertIsNone(self.mgr.get("alice", "r1"))
        self.assertIsNotNone(self.mgr.get("bob", "r1"))


# ═══════════════════════════════════════════════════════════════════
# 2. RemoteExecutorService WS mode (8 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRemoteExecutorServiceWS(unittest.TestCase):
    """Test RemoteExecutorService with WS mode."""

    def test_ws_mode_detection(self):
        from services.remote_executor_service import RemoteExecutorService
        svc = RemoteExecutorService({"relay_id": "exec1", "secret": "abc"})
        self.assertTrue(svc._is_ws_mode())

    def test_http_mode_detection(self):
        from services.remote_executor_service import RemoteExecutorService
        svc = RemoteExecutorService({"host": "localhost", "port": 9877, "secret": "abc"})
        self.assertFalse(svc._is_ws_mode())

    def test_ping_ws_connected(self):
        from services.remote_executor_service import RemoteExecutorService
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()
        mgr = RelayConnectionManager.instance()

        ws = MagicMock()
        mgr.register("user1", "exec1", "executor", ws, {})

        svc = RemoteExecutorService({"relay_id": "exec1", "secret": "abc"})
        svc.set_user_id("user1")
        self.assertTrue(svc.ping())

        RelayConnectionManager.reset()

    def test_ping_ws_disconnected(self):
        from services.remote_executor_service import RemoteExecutorService
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()

        svc = RemoteExecutorService({"relay_id": "exec1", "secret": "abc"})
        svc.set_user_id("user1")
        self.assertFalse(svc.ping())

        RelayConnectionManager.reset()

    def test_get_relay_info_ws(self):
        from services.remote_executor_service import RemoteExecutorService
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()
        mgr = RelayConnectionManager.instance()

        ws = MagicMock()
        mgr.register("user1", "exec1", "executor", ws, {"platform": "linux", "shell": "/bin/bash"})

        svc = RemoteExecutorService({"relay_id": "exec1", "secret": "abc"})
        svc.set_user_id("user1")
        info = svc.get_relay_info()
        self.assertEqual(info["platform"], "linux")

        RelayConnectionManager.reset()

    def test_send_command_no_relay(self):
        from services.remote_executor_service import RemoteExecutorService
        from core import ServiceError
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()

        svc = RemoteExecutorService({"relay_id": "exec1", "secret": "abc"})
        svc.set_user_id("user1")

        with self.assertRaises(ServiceError):
            svc.send_command("shell", command="ls")

        RelayConnectionManager.reset()

    def test_allowed_actions_filter(self):
        from services.remote_executor_service import RemoteExecutorService
        from core import ServiceError
        svc = RemoteExecutorService({
            "relay_id": "exec1", "secret": "abc",
            "allowed_actions": "shell,git_status",
        })
        svc.set_user_id("user1")

        with self.assertRaises(ServiceError) as ctx:
            svc.send_command("python_exec", code="print(1)")
        self.assertIn("not allowed", str(ctx.exception))

    def test_approval_mode_property(self):
        from services.remote_executor_service import RemoteExecutorService
        svc = RemoteExecutorService({"secret": "abc", "approval_mode": "strict"})
        self.assertEqual(svc.approval_mode, "strict")


# ═══════════════════════════════════════════════════════════════════
# 3. Cross-channel services — Plan B (8 tests)
# ═══════════════════════════════════════════════════════════════════

class TestCrossChannelServices(unittest.TestCase):
    """Test cross-channel service discovery via UserServiceRegistry fallback."""

    def setUp(self):
        from gui.services.user_service_registry import UserServiceRegistry
        UserServiceRegistry.reset()
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()
        # Patch GlobalServiceRegistry to avoid interference from running services
        self._greg_patcher = patch(
            "gui.services.global_service_registry.GlobalServiceRegistry.get_instance"
        )
        mock_greg = self._greg_patcher.start()
        mock_greg.return_value.get_all_definitions.return_value = {}

    def tearDown(self):
        self._greg_patcher.stop()
        from gui.services.user_service_registry import UserServiceRegistry
        UserServiceRegistry.reset()
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()

    def test_find_executor_flow_first(self):
        """Flow-level services take priority over user services."""
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        mock_svc = MagicMock()
        mock_svc.TYPE = "remoteExecutor"
        task._services = {"exec": mock_svc}
        result = task._find_executor_service("user1")
        self.assertIs(result, mock_svc)

    def test_find_executor_fallback_registry(self):
        """Falls back to UserServiceRegistry when no flow service."""
        from tasks.ai.agent_loop import AgentLoopTask
        from gui.services.user_service_registry import UserServiceRegistry

        task = AgentLoopTask.__new__(AgentLoopTask)
        task._services = {}

        registry = UserServiceRegistry.get_instance()
        mock_svc = MagicMock()
        mock_svc.TYPE = "remoteExecutor"

        with patch.object(registry, 'get_compatible') as mock_compat, \
             patch.object(registry, 'get_live_instance') as mock_live:
            from gui.services.user_service_registry import UserServiceDef
            sdef = UserServiceDef(
                service_id="exec1", service_type="remoteExecutor",
                user_id="user1", enabled=True,
            )
            mock_compat.return_value = [sdef]
            mock_live.return_value = mock_svc

            result = task._find_executor_service("user1")
            self.assertIs(result, mock_svc)

    def test_find_executor_no_user_id(self):
        """Without user_id, no fallback to registry."""
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        task._services = {}
        result = task._find_executor_service("")
        self.assertIsNone(result)

    def test_find_filesystem_flow_first(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        mock_svc = MagicMock()
        mock_svc.TYPE = "filesystem"
        task._services = {"fs": mock_svc}
        result = task._find_filesystem_service("user1")
        self.assertIs(result, mock_svc)

    def test_find_filesystem_fallback_registry(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from gui.services.user_service_registry import UserServiceRegistry

        task = AgentLoopTask.__new__(AgentLoopTask)
        task._services = {}

        registry = UserServiceRegistry.get_instance()
        mock_svc = MagicMock()

        with patch.object(registry, 'get_compatible') as mock_compat, \
             patch.object(registry, 'get_live_instance') as mock_live:
            from gui.services.user_service_registry import UserServiceDef
            sdef = UserServiceDef(
                service_id="fs1", service_type="filesystem",
                user_id="user1", enabled=True,
            )
            mock_compat.return_value = [sdef]
            mock_live.return_value = mock_svc

            result = task._find_filesystem_service("user1")
            self.assertIs(result, mock_svc)

    def test_find_filesystem_disabled_skipped(self):
        """Disabled services are not returned."""
        from tasks.ai.agent_loop import AgentLoopTask
        from gui.services.user_service_registry import UserServiceRegistry

        task = AgentLoopTask.__new__(AgentLoopTask)
        task._services = {}

        registry = UserServiceRegistry.get_instance()
        with patch.object(registry, 'get_compatible') as mock_compat:
            from gui.services.user_service_registry import UserServiceDef
            sdef = UserServiceDef(
                service_id="fs1", service_type="filesystem",
                user_id="user1", enabled=False,
            )
            mock_compat.return_value = [sdef]
            result = task._find_filesystem_service("user1")
            self.assertIsNone(result)

    def test_list_available_services(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask.__new__(AgentLoopTask)
        mock_svc = MagicMock()
        mock_svc.TYPE = "remoteExecutor"
        mock_svc.get_relay_info.return_value = {"root": "/tmp"}
        task._services = {"exec": mock_svc}
        result = task._list_available_services("", "remoteExecutor")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "exec")

    def test_list_available_includes_user_services(self):
        from tasks.ai.agent_loop import AgentLoopTask
        from gui.services.user_service_registry import UserServiceRegistry

        task = AgentLoopTask.__new__(AgentLoopTask)
        task._services = {}

        registry = UserServiceRegistry.get_instance()
        with patch.object(registry, 'get_all_for_user') as mock_all:
            from gui.services.user_service_registry import UserServiceDef
            mock_all.return_value = {
                "exec1": UserServiceDef(
                    service_id="exec1", service_type="remoteExecutor",
                    user_id="user1", enabled=True, description="WS relay: linux",
                ),
            }
            result = task._list_available_services("user1", "remoteExecutor")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["id"], "exec1")


# ═══════════════════════════════════════════════════════════════════
# 4. Heartbeat — Plan C (7 tests)
# ═══════════════════════════════════════════════════════════════════

class TestServiceHeartbeat(unittest.TestCase):
    """Test UserServiceRegistry heartbeat."""

    def setUp(self):
        from gui.services.user_service_registry import UserServiceRegistry
        UserServiceRegistry.reset()

    def tearDown(self):
        from gui.services.user_service_registry import UserServiceRegistry
        inst = UserServiceRegistry.get_instance()
        inst.stop_heartbeat()
        UserServiceRegistry.reset()

    def test_heartbeat_start_stop(self):
        from gui.services.user_service_registry import UserServiceRegistry
        reg = UserServiceRegistry.get_instance()
        reg.start_heartbeat(interval=1)
        self.assertTrue(reg._heartbeat_running)
        reg.stop_heartbeat()
        self.assertFalse(reg._heartbeat_running)

    def test_heartbeat_check_healthy(self):
        from gui.services.user_service_registry import UserServiceRegistry, UserServiceDef
        reg = UserServiceRegistry.get_instance()

        mock_svc = MagicMock()
        mock_svc.ping.return_value = True
        sdef = UserServiceDef(
            service_id="exec1", service_type="remoteExecutor",
            user_id="user1", enabled=True,
        )
        reg._definitions["user1"] = {"exec1": sdef}
        reg._live_instances["user1"] = {"exec1": mock_svc}

        reg._heartbeat_check()
        self.assertEqual(reg._failure_counts.get(("user1", "exec1"), 0), 0)
        mock_svc.ping.assert_called_once()

    def test_heartbeat_check_failure_increment(self):
        from gui.services.user_service_registry import UserServiceRegistry, UserServiceDef
        reg = UserServiceRegistry.get_instance()

        mock_svc = MagicMock()
        mock_svc.ping.return_value = False
        sdef = UserServiceDef(
            service_id="exec1", service_type="remoteExecutor",
            user_id="user1", enabled=True,
        )
        reg._definitions["user1"] = {"exec1": sdef}
        reg._live_instances["user1"] = {"exec1": mock_svc}

        reg._heartbeat_check()
        self.assertEqual(reg._failure_counts[("user1", "exec1")], 1)

    def test_heartbeat_auto_disable_after_3_failures(self):
        from gui.services.user_service_registry import UserServiceRegistry, UserServiceDef
        reg = UserServiceRegistry.get_instance()

        mock_svc = MagicMock()
        mock_svc.ping.return_value = False
        sdef = UserServiceDef(
            service_id="exec1", service_type="remoteExecutor",
            user_id="user1", enabled=True,
        )
        reg._definitions["user1"] = {"exec1": sdef}
        reg._live_instances["user1"] = {"exec1": mock_svc}
        reg._loaded_users.add("user1")

        # Fail 3 times
        with patch.object(reg, '_notify_service_down'), \
             patch.object(reg, '_save_user_to_disk'):
            for _ in range(3):
                reg._heartbeat_check()

        self.assertFalse(sdef.enabled)

    def test_heartbeat_skips_non_relay_types(self):
        from gui.services.user_service_registry import UserServiceRegistry, UserServiceDef
        reg = UserServiceRegistry.get_instance()

        mock_svc = MagicMock()
        mock_svc.ping.return_value = False
        sdef = UserServiceDef(
            service_id="db1", service_type="postgresql",
            user_id="user1", enabled=True,
        )
        reg._definitions["user1"] = {"db1": sdef}
        reg._live_instances["user1"] = {"db1": mock_svc}

        reg._heartbeat_check()
        # postgresql is not in HEARTBEAT_TYPES, so ping should not be called
        mock_svc.ping.assert_not_called()

    def test_heartbeat_skips_disabled(self):
        from gui.services.user_service_registry import UserServiceRegistry, UserServiceDef
        reg = UserServiceRegistry.get_instance()

        mock_svc = MagicMock()
        sdef = UserServiceDef(
            service_id="exec1", service_type="remoteExecutor",
            user_id="user1", enabled=False,
        )
        reg._definitions["user1"] = {"exec1": sdef}
        reg._live_instances["user1"] = {"exec1": mock_svc}

        reg._heartbeat_check()
        mock_svc.ping.assert_not_called()

    def test_heartbeat_resets_on_recovery(self):
        from gui.services.user_service_registry import UserServiceRegistry, UserServiceDef
        reg = UserServiceRegistry.get_instance()

        mock_svc = MagicMock()
        sdef = UserServiceDef(
            service_id="exec1", service_type="remoteExecutor",
            user_id="user1", enabled=True,
        )
        reg._definitions["user1"] = {"exec1": sdef}
        reg._live_instances["user1"] = {"exec1": mock_svc}

        # 2 failures
        mock_svc.ping.return_value = False
        reg._heartbeat_check()
        reg._heartbeat_check()
        self.assertEqual(reg._failure_counts[("user1", "exec1")], 2)

        # Recovery
        mock_svc.ping.return_value = True
        reg._heartbeat_check()
        self.assertNotIn(("user1", "exec1"), reg._failure_counts)


# ═══════════════════════════════════════════════════════════════════
# 5. Multi-service selection — Plan D (6 tests)
# ═══════════════════════════════════════════════════════════════════

class TestMultiServiceSelection(unittest.TestCase):
    """Test multi-service selection for handlers."""

    def test_executor_handler_service_param_in_schema(self):
        from core.tool_registry import RemoteExecutorHandler
        h = RemoteExecutorHandler()
        schema = h.parameters_schema
        self.assertIn("service", schema["properties"])

    def test_filesystem_handler_service_param_in_schema(self):
        from core.tool_registry import FilesystemToolHandler
        h = FilesystemToolHandler()
        schema = h.parameters_schema
        self.assertIn("service", schema["properties"])

    def test_executor_handler_description_multi_service(self):
        from core.tool_registry import RemoteExecutorHandler
        h = RemoteExecutorHandler()
        h._available_services = [
            {"id": "exec1", "root": "/project"},
            {"id": "exec2", "root": "/data"},
        ]
        desc = h.description
        self.assertIn("exec1", desc)
        self.assertIn("exec2", desc)
        self.assertIn("service", desc)

    def test_executor_handler_description_single_service(self):
        from core.tool_registry import RemoteExecutorHandler
        h = RemoteExecutorHandler()
        h._available_services = [{"id": "exec1", "root": "/project"}]
        desc = h.description
        self.assertNotIn("Available services:", desc)

    def test_filesystem_handler_description_multi_service(self):
        from core.tool_registry import FilesystemToolHandler
        h = FilesystemToolHandler()
        h._available_services = [
            {"id": "fs1", "type": "localFilesystem", "root": "/data"},
            {"id": "fs2", "type": "googleDrive", "root": "My Drive"},
        ]
        desc = h.description
        self.assertIn("fs1", desc)
        self.assertIn("fs2", desc)

    def test_executor_resolve_service_default(self):
        from core.tool_registry import RemoteExecutorHandler
        h = RemoteExecutorHandler()
        mock_svc = MagicMock()
        h._service = mock_svc
        resolved = h._resolve_service("")
        self.assertIs(resolved, mock_svc)


# ═══════════════════════════════════════════════════════════════════
# 6. Tool Approval — Plan A (12 tests)
# ═══════════════════════════════════════════════════════════════════

class TestToolApprovalGate(unittest.TestCase):
    """Test ToolApprovalGate universal permission system."""

    def test_exempt_tools_auto_approved(self):
        from core.tool_approval import ToolApprovalGate
        result = ToolApprovalGate.check("recall", "Recall memory", "conv1", "user1")
        self.assertEqual(result, "approved")

    def test_exempt_semantic_recall(self):
        from core.tool_approval import ToolApprovalGate
        result = ToolApprovalGate.check("semantic_recall", "Search", "conv1", "user1")
        self.assertEqual(result, "approved")

    def test_exempt_show_file(self):
        from core.tool_approval import ToolApprovalGate
        result = ToolApprovalGate.check("show_file", "Show file", "conv1", "user1")
        self.assertEqual(result, "approved")

    def test_always_ask_denied_without_conversation(self):
        from core.tool_approval import ToolApprovalGate
        result = ToolApprovalGate.check("remote_exec", "ls -la", "", "user1")
        self.assertEqual(result, "denied")

    def test_session_allow_persisted(self):
        from core.tool_approval import ToolApprovalGate
        with patch.object(ToolApprovalGate, '_get_permissions') as mock_perms:
            mock_perms.return_value = {"filesystem": "session_allow"}
            result = ToolApprovalGate.check("filesystem", "list files", "conv1", "user1")
            self.assertEqual(result, "approved")

    def test_always_allow_persisted(self):
        from core.tool_approval import ToolApprovalGate
        with patch.object(ToolApprovalGate, '_get_permissions') as mock_perms:
            mock_perms.return_value = {"execute_script": "always_allow"}
            result = ToolApprovalGate.check("execute_script", "run code", "conv1", "user1")
            self.assertEqual(result, "approved")

    def test_resolve_request(self):
        from core.tool_approval import ToolApprovalGate
        event = threading.Event()
        with ToolApprovalGate._lock:
            ToolApprovalGate._pending["test123"] = event

        ok = ToolApprovalGate.resolve_request("test123", {"choice": "allow_once"})
        self.assertTrue(ok)
        self.assertTrue(event.is_set())

        with ToolApprovalGate._lock:
            result = ToolApprovalGate._results.pop("test123", None)
        self.assertEqual(result["choice"], "allow_once")

    def test_resolve_unknown_request(self):
        from core.tool_approval import ToolApprovalGate
        ok = ToolApprovalGate.resolve_request("nonexistent", {"choice": "deny"})
        self.assertFalse(ok)

    def test_set_and_get_permission(self):
        from core.tool_approval import ToolApprovalGate
        with patch("core.conversation_store.ConversationStore") as MockStore:
            mock_inst = MagicMock()
            MockStore.instance.return_value = mock_inst
            mock_inst.get_extra.return_value = {}

            ToolApprovalGate._set_permission("conv1", "filesystem", "session_allow")
            mock_inst.set_extra.assert_called_once()
            args = mock_inst.set_extra.call_args
            self.assertEqual(args[0][1], "tool_permissions")
            self.assertEqual(args[0][2]["filesystem"], "session_allow")

    def test_non_always_ask_approved_without_dialog(self):
        """Non-ALWAYS_ASK tools are approved when no dialog can be shown."""
        from core.tool_approval import ToolApprovalGate
        with patch.object(ToolApprovalGate, '_get_permissions', return_value={}):
            with patch("core.conversation_event_bus.ConversationEventBus") as MockBus:
                MockBus.instance.side_effect = Exception("no bus")
                result = ToolApprovalGate.check("remember", "Save note", "conv1", "user1")
                self.assertEqual(result, "approved")

    def test_always_ask_in_set(self):
        from core.tool_approval import ToolApprovalGate
        self.assertIn("remote_exec", ToolApprovalGate.ALWAYS_ASK)
        self.assertIn("execute_script", ToolApprovalGate.ALWAYS_ASK)
        self.assertIn("browser_action", ToolApprovalGate.ALWAYS_ASK)
        # filesystem/local_files handled by action-aware _FS_ALWAYS_ASK instead

    def test_exempt_in_set(self):
        from core.tool_approval import ToolApprovalGate
        self.assertIn("recall", ToolApprovalGate.EXEMPT_TOOLS)
        self.assertIn("semantic_recall", ToolApprovalGate.EXEMPT_TOOLS)
        self.assertIn("show_file", ToolApprovalGate.EXEMPT_TOOLS)


# ═══════════════════════════════════════════════════════════════════
# 7. Auto-install / auto-disable (5 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRelayAutoInstall(unittest.TestCase):
    """Test auto-install/disable of services when relay connects/disconnects."""

    def setUp(self):
        # Ensure service types are registered
        import services.remote_executor_service  # noqa: F401
        import services.filesystem_service  # noqa: F401
        from gui.services.user_service_registry import UserServiceRegistry
        UserServiceRegistry.reset()
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()

    def tearDown(self):
        from gui.services.user_service_registry import UserServiceRegistry
        UserServiceRegistry.reset()
        from core.relay_manager import RelayConnectionManager
        RelayConnectionManager.reset()

    def test_auto_install_on_register(self):
        from core.relay_manager import RelayConnectionManager
        from gui.services.user_service_registry import UserServiceRegistry

        mgr = RelayConnectionManager.instance()
        ws = MagicMock()
        mgr.register("user1", "exec1", "executor", ws, {"platform": "linux", "root": "/tmp"})

        reg = UserServiceRegistry.get_instance()
        sdef = reg.get_definition("user1", "exec1")
        self.assertIsNotNone(sdef)
        self.assertEqual(sdef.service_type, "remoteExecutor")
        self.assertTrue(sdef.enabled)

    def test_auto_install_filesystem(self):
        from core.relay_manager import RelayConnectionManager
        from gui.services.user_service_registry import UserServiceRegistry

        mgr = RelayConnectionManager.instance()
        ws = MagicMock()
        mgr.register("user1", "fs1", "filesystem", ws, {"root": "/data"})

        reg = UserServiceRegistry.get_instance()
        sdef = reg.get_definition("user1", "fs1")
        self.assertIsNotNone(sdef)
        self.assertEqual(sdef.service_type, "localFilesystem")

    def test_auto_disable_on_unregister(self):
        from core.relay_manager import RelayConnectionManager
        from gui.services.user_service_registry import UserServiceRegistry

        mgr = RelayConnectionManager.instance()
        ws = MagicMock()
        mgr.register("user1", "exec1", "executor", ws, {})

        reg = UserServiceRegistry.get_instance()
        sdef = reg.get_definition("user1", "exec1")
        self.assertTrue(sdef.enabled)

        mgr.unregister("user1", "exec1")
        sdef = reg.get_definition("user1", "exec1")
        self.assertFalse(sdef.enabled)

    def test_re_enable_on_reconnect(self):
        from core.relay_manager import RelayConnectionManager
        from gui.services.user_service_registry import UserServiceRegistry

        mgr = RelayConnectionManager.instance()
        ws = MagicMock()

        mgr.register("user1", "exec1", "executor", ws, {})
        mgr.unregister("user1", "exec1")

        reg = UserServiceRegistry.get_instance()
        sdef = reg.get_definition("user1", "exec1")
        self.assertFalse(sdef.enabled)

        # Reconnect
        mgr.register("user1", "exec1", "executor", ws, {})
        sdef = reg.get_definition("user1", "exec1")
        self.assertTrue(sdef.enabled)

    def test_description_set_from_info(self):
        from core.relay_manager import RelayConnectionManager
        from gui.services.user_service_registry import UserServiceRegistry

        mgr = RelayConnectionManager.instance()
        ws = MagicMock()
        # Use a unique relay_id to avoid conflict with previous test data on disk
        mgr.register("user1", "desc-test-relay", "executor", ws,
                      {"platform": "win32", "root": "C:\\proj"})

        reg = UserServiceRegistry.get_instance()
        sdef = reg.get_definition("user1", "desc-test-relay")
        self.assertIsNotNone(sdef)
        self.assertIn("win32", sdef.description)


# ═══════════════════════════════════════════════════════════════════
# 8. i18n keys (6 tests)
# ═══════════════════════════════════════════════════════════════════

class TestI18nKeys(unittest.TestCase):
    """Verify i18n keys exist in all 3 locales."""

    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path
        i18n_dir = Path("gui/i18n")
        cls.en = json.loads((i18n_dir / "en.json").read_text(encoding="utf-8"))
        cls.fr = json.loads((i18n_dir / "fr.json").read_text(encoding="utf-8"))
        cls.es = json.loads((i18n_dir / "es.json").read_text(encoding="utf-8"))

    def _check_key(self, key):
        self.assertIn(key, self.en, f"Missing EN key: {key}")
        self.assertIn(key, self.fr, f"Missing FR key: {key}")
        self.assertIn(key, self.es, f"Missing ES key: {key}")

    def test_relay_keys(self):
        for k in ("relay.connected", "relay.disconnected", "relay.no_relay",
                   "relay.ws_mode", "relay.http_mode"):
            self._check_key(k)

    def test_tool_approval_keys(self):
        for k in ("tool_approval.title", "tool_approval.deny",
                   "tool_approval.allow_once", "tool_approval.allow_session",
                   "tool_approval.always_allow"):
            self._check_key(k)

    def test_service_keys(self):
        for k in ("service.heartbeat_down", "service.auto_installed",
                   "service.auto_disabled", "service.multi_available"):
            self._check_key(k)

    def test_existing_exec_keys_preserved(self):
        for k in ("exec.approval_title", "exec.approve", "exec.deny",
                   "exec.risk_low", "exec.risk_medium", "exec.risk_high"):
            self._check_key(k)

    def test_existing_filesystem_keys_preserved(self):
        for k in ("filesystem.local_name", "filesystem.error_no_service",
                   "filesystem.relay_usage"):
            self._check_key(k)

    def test_all_locales_same_key_count(self):
        """All locales should have the same relay/approval/service keys."""
        relay_keys = [k for k in self.en if k.startswith("relay.")]
        for k in relay_keys:
            self.assertIn(k, self.fr, f"FR missing: {k}")
            self.assertIn(k, self.es, f"ES missing: {k}")


# ═══════════════════════════════════════════════════════════════════
# 9. WS endpoint structure (4 tests)
# ═══════════════════════════════════════════════════════════════════

class TestWSEndpoint(unittest.TestCase):
    """Test that the WS relay endpoint is properly defined."""

    def test_ws_relay_route_removed(self):
        """Relay endpoint moved from API router to FilesystemWSListener."""
        from api.routers.ws_router import router
        routes = [r.path for r in router.routes]
        # Old /relay route no longer in API router (handled by FilesystemWSListener)
        self.assertNotIn("/relay", routes)

    def test_relay_manager_import(self):
        """Verify relay_manager module is importable."""
        from core.relay_manager import RelayConnectionManager, RelayConnection
        self.assertIsNotNone(RelayConnectionManager)
        self.assertIsNotNone(RelayConnection)

    def test_tool_approval_import(self):
        """Verify tool_approval module is importable."""
        from core.tool_approval import ToolApprovalGate
        self.assertIsNotNone(ToolApprovalGate)

    def test_remote_executor_service_version(self):
        from services.remote_executor_service import RemoteExecutorService
        self.assertEqual(RemoteExecutorService.VERSION, "1.1.0")


# ═══════════════════════════════════════════════════════════════════
# 10. Relay script CLI args (5 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRelayScriptArgs(unittest.TestCase):
    """Test that relay scripts accept --connect args."""

    def test_executor_relay_has_connect_arg(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "relay", "tools/pawflow_executor_relay.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Don't execute, just verify source contains --connect
        source = open("tools/pawflow_executor_relay.py").read()
        self.assertIn("--connect", source)
        self.assertIn("--token", source)
        self.assertIn("--relay-id", source)

    def test_fs_relay_has_connect_arg(self):
        source = open("tools/pawflow_relay.py").read()
        self.assertIn("--server", source)
        self.assertIn("--token", source)
        self.assertIn("--relay-id", source)

    def test_executor_relay_has_ws_connect(self):
        source = open("tools/pawflow_executor_relay.py").read()
        self.assertIn("_ws_connect", source)
        self.assertIn("ws_key", source)

    def test_fs_relay_has_ws_connect(self):
        source = open("tools/pawflow_relay.py").read()
        self.assertIn("_ws_connect", source)

    def test_executor_relay_ws_doc_updated(self):
        source = open("tools/pawflow_executor_relay.py").read()
        self.assertIn("WS Reverse", source)
        self.assertIn("ws://pawflow.example.com", source)


if __name__ == "__main__":
    unittest.main()
