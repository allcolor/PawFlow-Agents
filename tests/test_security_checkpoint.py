"""Tests for Security, Checkpoint, and Worker Health features."""

import json
import time
import pytest
import tempfile
import shutil
from pathlib import Path

from tasks import register_all_tasks
register_all_tasks()

from core import FlowFile
from core.security import SecurityManager, User, Role, ROLE_PERMISSIONS, _hash_password
from engine.checkpoint import CheckpointManager
from core.connection import Connection


# ============================================================================
# Security Tests
# ============================================================================

class TestSecurity:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Use temp files for security config."""
        import core.paths as _paths
        self._orig_config = _paths.SECURITY_FILE
        self._orig_users = _paths.USERS_FILE
        _paths.SECURITY_FILE = tmp_path / "security.json"
        _paths.USERS_FILE = tmp_path / "users.json"
        # Reset singleton
        SecurityManager._instance = None
        yield
        _paths.SECURITY_FILE = self._orig_config
        _paths.USERS_FILE = self._orig_users
        SecurityManager._instance = None

    def test_default_admin_created(self):
        sm = SecurityManager()
        user = sm.get_user("admin")
        assert user is not None
        assert user.role == Role.ADMIN

    def test_create_and_authenticate_user(self):
        sm = SecurityManager()
        sm.create_user("bob", "secret123", Role.EDITOR, email="bob@test.com")

        session = sm.authenticate("bob", "secret123")
        assert session is not None
        assert session.username == "bob"
        assert session.role == Role.EDITOR

    def test_wrong_password_fails(self):
        sm = SecurityManager()
        sm.create_user("alice", "correct", Role.VIEWER)
        session = sm.authenticate("alice", "wrong")
        assert session is None

    def test_disabled_user_fails(self):
        sm = SecurityManager()
        sm.create_user("charlie", "pass", Role.VIEWER)
        sm.update_user("charlie", enabled=False)
        session = sm.authenticate("charlie", "pass")
        assert session is None

    def test_role_permissions(self):
        sm = SecurityManager()
        sm.create_user("editor", "pass", Role.EDITOR)
        session = sm.authenticate("editor", "pass")

        assert sm.check_permission(session, "flow.edit")
        assert sm.check_permission(session, "flow.execute")
        assert not sm.check_permission(session, "user.manage")
        assert not sm.check_permission(session, "plugin.install")

    def test_admin_has_all_permissions(self):
        sm = SecurityManager()
        session = sm.authenticate("admin", "admin")
        assert session is not None

        for perm in ROLE_PERMISSIONS[Role.ADMIN]:
            assert sm.check_permission(session, perm)

    def test_viewer_readonly(self):
        sm = SecurityManager()
        sm.create_user("viewer", "pass", Role.VIEWER)
        session = sm.authenticate("viewer", "pass")

        assert sm.check_permission(session, "monitor.view")
        assert not sm.check_permission(session, "flow.edit")
        assert not sm.check_permission(session, "flow.execute")

    def test_session_logout(self):
        sm = SecurityManager()
        sm.create_user("temp", "pass", Role.VIEWER)
        session = sm.authenticate("temp", "pass")
        assert sm.get_session(session.session_id) is not None

        sm.logout(session.session_id)
        assert sm.get_session(session.session_id) is None

    def test_api_key_lifecycle(self):
        sm = SecurityManager()
        key = sm.generate_api_key("test key")
        assert sm.validate_api_key(key)
        assert len(sm.list_api_keys()) == 1

        sm.revoke_api_key(key)
        assert not sm.validate_api_key(key)

    def test_delete_user(self):
        sm = SecurityManager()
        sm.create_user("todelete", "pass", Role.VIEWER)
        session = sm.authenticate("todelete", "pass")
        assert session is not None

        sm.delete_user("todelete")
        assert sm.get_user("todelete") is None
        # Session should be invalidated
        assert sm.get_session(session.session_id) is None

    def test_duplicate_user_raises(self):
        sm = SecurityManager()
        sm.create_user("unique", "pass", Role.VIEWER)
        with pytest.raises(ValueError, match="already exists"):
            sm.create_user("unique", "pass", Role.VIEWER)

    def test_oauth_config(self):
        sm = SecurityManager()
        sm.set_oauth_config("google", {
            "client_id": "abc",
            "client_secret": "xyz",
            "authorize_url": "https://accounts.google.com/o/oauth2/auth",
        })
        assert "google" in sm.list_oauth_providers()
        config = sm.get_oauth_config("google")
        assert config["client_id"] == "abc"

    def test_persistence(self, tmp_path):
        sm = SecurityManager()
        sm.create_user("persistent", "pass123", Role.OPERATOR)
        sm.generate_api_key("persistent key")

        # Create new instance (simulates restart)
        SecurityManager._instance = None
        sm2 = SecurityManager()
        user = sm2.get_user("persistent")
        assert user is not None
        assert user.role == Role.OPERATOR
        assert len(sm2.list_api_keys()) >= 1


# ============================================================================
# Checkpoint Tests
# ============================================================================

class TestCheckpoint:

    @pytest.fixture
    def checkpoint_dir(self, tmp_path):
        return str(tmp_path / "checkpoints")

    def test_save_and_load_checkpoint(self, checkpoint_dir):
        mgr = CheckpointManager("test_flow", checkpoint_dir=checkpoint_dir)

        # Create a connection with FlowFiles
        conn = Connection("a", "b")
        ff1 = FlowFile(content=b"hello world", attributes={"key": "val1"})
        ff2 = FlowFile(content=b"second item", attributes={"key": "val2"})
        conn.enqueue(ff1)
        conn.enqueue(ff2)

        # Save checkpoint
        path = mgr.save_checkpoint([conn], {"a": {"state": "running"}}, 1)
        assert Path(path).exists()

        # FlowFiles should still be in queue after checkpoint
        assert conn.queue_size() == 2

    def test_restore_flowfiles(self, checkpoint_dir):
        mgr = CheckpointManager("test_flow", checkpoint_dir=checkpoint_dir)

        conn = Connection("src", "dest")
        ff = FlowFile(content=b"important data", attributes={"x": "1"})
        conn.enqueue(ff)

        mgr.save_checkpoint([conn], {}, 1)

        # Load and restore
        data = mgr.load_latest_checkpoint()
        assert data is not None
        restored = mgr.restore_flowfiles(data)

        key = ("src", "dest")
        assert key in restored
        assert len(restored[key]) == 1
        assert restored[key][0].get_content() == b"important data"
        assert restored[key][0].get_attribute("x") == "1"

    def test_multiple_checkpoints_trimmed(self, checkpoint_dir):
        mgr = CheckpointManager("test_flow", checkpoint_dir=checkpoint_dir,
                                max_checkpoints=3)

        for i in range(5):
            conn = Connection("a", "b")
            ff = FlowFile(content=f"data-{i}".encode())
            conn.enqueue(ff)
            mgr.save_checkpoint([conn], {}, i + 1)

        checkpoints = mgr.list_checkpoints()
        assert len(checkpoints) == 3
        # Latest should be version 5
        assert checkpoints[-1]["flow_version"] == 5

    def test_empty_queue_checkpoint(self, checkpoint_dir):
        mgr = CheckpointManager("test_flow", checkpoint_dir=checkpoint_dir)
        conn = Connection("a", "b")  # empty

        mgr.save_checkpoint([conn], {}, 1)
        data = mgr.load_latest_checkpoint()
        assert data is not None
        assert len(data["queues"]) == 0

    def test_large_content_uses_file(self, checkpoint_dir):
        mgr = CheckpointManager("test_flow", checkpoint_dir=checkpoint_dir)

        # Content larger than INLINE_MAX_BYTES
        large_content = b"x" * (300 * 1024)  # 300KB
        conn = Connection("a", "b")
        conn.enqueue(FlowFile(content=large_content))

        mgr.save_checkpoint([conn], {}, 1)

        data = mgr.load_latest_checkpoint()
        restored = mgr.restore_flowfiles(data)
        assert restored[("a", "b")][0].get_content() == large_content

    def test_clear(self, checkpoint_dir):
        mgr = CheckpointManager("test_flow", checkpoint_dir=checkpoint_dir)
        conn = Connection("a", "b")
        conn.enqueue(FlowFile(content=b"data"))
        mgr.save_checkpoint([conn], {}, 1)

        mgr.clear()
        assert mgr.load_latest_checkpoint() is None


# ============================================================================
# Worker Health Tests
# ============================================================================

class TestWorkerHealth:

    def test_circuit_breaker_trips(self):
        from engine.remote_worker import WorkerCoordinator, WorkerStatus

        coord = WorkerCoordinator(max_consecutive_failures=3)
        worker = coord.register_worker("test-worker", "host", 9999)

        # Simulate consecutive failures
        for i in range(3):
            assignment = coord.submit_task(
                f"t{i}", "log", {"message": "x", "level": "INFO"},
                b"data", {}, worker_id=worker.worker_id,
            )
            coord.fail_task(assignment.assignment_id, "error")
            coord._record_failure(worker.worker_id)

        # Worker should be OFFLINE
        assert coord._workers[worker.worker_id].status == WorkerStatus.OFFLINE

    def test_success_resets_failures(self):
        from engine.remote_worker import WorkerCoordinator

        coord = WorkerCoordinator(max_consecutive_failures=5)
        worker = coord.register_worker("test-worker", "host", 9999)

        # 3 failures
        for i in range(3):
            coord._record_failure(worker.worker_id)
        assert coord._consecutive_failures[worker.worker_id] == 3

        # Success resets
        coord._record_success(worker.worker_id)
        assert worker.worker_id not in coord._consecutive_failures

    def test_manual_reset_worker(self):
        from engine.remote_worker import WorkerCoordinator, WorkerStatus

        coord = WorkerCoordinator(max_consecutive_failures=1)
        worker = coord.register_worker("test-worker", "host", 9999)

        coord._record_failure(worker.worker_id)
        assert coord._workers[worker.worker_id].status == WorkerStatus.OFFLINE

        coord.reset_worker(worker.worker_id)
        assert coord._workers[worker.worker_id].status == WorkerStatus.IDLE

    def test_heartbeat_timeout(self):
        from engine.remote_worker import WorkerCoordinator, WorkerStatus
        from datetime import datetime, timedelta

        coord = WorkerCoordinator(heartbeat_timeout_seconds=1)
        worker = coord.register_worker("test-worker", "host", 9999)

        # Backdate heartbeat
        coord._workers[worker.worker_id].last_heartbeat = (
            datetime.now() - timedelta(seconds=5)
        )

        coord._check_worker_health()
        assert coord._workers[worker.worker_id].status == WorkerStatus.OFFLINE

    def test_health_summary(self):
        from engine.remote_worker import WorkerCoordinator

        coord = WorkerCoordinator()
        coord.register_worker("w1", "h1", 9001)
        coord.register_worker("w2", "h2", 9002)

        summary = coord.get_health_summary()
        assert summary["total_workers"] == 3  # 2 + local
        assert summary["online"] == 3


# ============================================================================
# Controller Service Injection Tests
# ============================================================================

class TestServiceInjection:

    def test_task_gets_services(self):
        from core.base_task import BaseTask

        class DummyTask(BaseTask):
            TYPE = "dummy"
            VERSION = "1.0.0"
            NAME = "Dummy"
            DESCRIPTION = "Test"
            ICON = ""

            def execute(self, flowfile):
                svc = self.get_service("my_cache")
                if svc:
                    flowfile.set_attribute("has_service", "true")
                return [flowfile]

            def get_parameter_schema(self):
                return {}

        task = DummyTask({})
        task.set_services({"my_cache": "mock_service"})

        assert task.get_service("my_cache") == "mock_service"
        assert task.get_service("nonexistent") is None

    def test_service_via_config_id(self):
        from core.base_task import BaseTask

        class SvcTask(BaseTask):
            TYPE = "svc_test"
            VERSION = "1.0.0"
            NAME = "Svc"
            DESCRIPTION = "Test"
            ICON = ""

            def execute(self, flowfile):
                return [flowfile]

            def get_parameter_schema(self):
                return {}

        task = SvcTask({"service_id": "my_db"})
        task.set_services({"my_db": "db_connection"})

        # get_service with the config's service_id
        assert task.get_service("my_db") == "db_connection"


# ============================================================================
# Worker Auth Tests
# ============================================================================

class TestWorkerAuth:

    def test_server_with_api_key(self):
        from engine.worker_server import WorkerServer
        from engine.worker_client import WorkerClient

        server = WorkerServer(port=0, api_key="test-secret-key")
        server.start()
        try:
            port = server.port

            # Request without auth should fail
            import http.client
            conn = http.client.HTTPConnection("localhost", port, timeout=5)
            conn.request("GET", "/status")
            resp = conn.getresponse()
            assert resp.status == 401

            # Request with correct auth should succeed
            conn2 = http.client.HTTPConnection("localhost", port, timeout=5)
            conn2.request("GET", "/status", headers={
                "Authorization": "Bearer test-secret-key"
            })
            resp2 = conn2.getresponse()
            assert resp2.status == 200

            # Request with wrong auth should fail
            conn3 = http.client.HTTPConnection("localhost", port, timeout=5)
            conn3.request("GET", "/status", headers={
                "Authorization": "Bearer wrong-key"
            })
            resp3 = conn3.getresponse()
            assert resp3.status == 401
        finally:
            server.stop()

    def test_server_without_api_key(self):
        from engine.worker_server import WorkerServer

        server = WorkerServer(port=0)  # No API key
        server.start()
        try:
            import http.client
            conn = http.client.HTTPConnection("localhost", server.port, timeout=5)
            conn.request("GET", "/status")
            resp = conn.getresponse()
            assert resp.status == 200  # No auth required
        finally:
            server.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
