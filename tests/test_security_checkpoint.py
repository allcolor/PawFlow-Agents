"""Tests for Security and Checkpoint features."""

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

    def test_no_default_admin_created(self):
        sm = SecurityManager()
        assert sm.get_user("admin") is None

    def test_create_and_authenticate_user(self):
        sm = SecurityManager()
        sm.create_user("bob", "secret123", Role.USER, email="bob@test.com")

        session = sm.authenticate("bob", "secret123")
        assert session is not None
        assert session.username == "bob"
        assert session.role == Role.USER

    def test_wrong_password_fails(self):
        sm = SecurityManager()
        sm.create_user("alice", "correct", Role.USER)
        session = sm.authenticate("alice", "wrong")
        assert session is None

    def test_disabled_user_fails(self):
        sm = SecurityManager()
        sm.create_user("charlie", "pass", Role.USER)
        sm.update_user("charlie", enabled=False)
        session = sm.authenticate("charlie", "pass")
        assert session is None

    def test_role_permissions(self):
        sm = SecurityManager()
        sm.create_user("user", "pass", Role.USER)
        session = sm.authenticate("user", "pass")

        assert sm.check_permission(session, "monitor.view")
        assert not sm.check_permission(session, "flow.edit")
        assert not sm.check_permission(session, "flow.execute")
        assert not sm.check_permission(session, "user.manage")
        assert not sm.check_permission(session, "plugin.install")

    def test_admin_has_all_permissions(self):
        sm = SecurityManager()
        sm.create_user("admin", "admin", Role.ADMIN)
        session = sm.authenticate("admin", "admin")
        assert session is not None

        for perm in ROLE_PERMISSIONS[Role.ADMIN]:
            assert sm.check_permission(session, perm)

    def test_user_not_admin(self):
        sm = SecurityManager()
        sm.create_user("regular", "pass", Role.USER)
        session = sm.authenticate("regular", "pass")

        assert sm.check_permission(session, "monitor.view")
        assert not sm.check_permission(session, "flow.edit")
        assert not sm.check_permission(session, "flow.execute")

    def test_session_logout(self):
        sm = SecurityManager()
        sm.create_user("temp", "pass", Role.USER)
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
        sm.create_user("todelete", "pass", Role.USER)
        session = sm.authenticate("todelete", "pass")
        assert session is not None

        sm.delete_user("todelete")
        assert sm.get_user("todelete") is None
        # Session should be invalidated
        assert sm.get_session(session.session_id) is None

    def test_duplicate_user_raises(self):
        sm = SecurityManager()
        sm.create_user("unique", "pass", Role.USER)
        with pytest.raises(ValueError, match="already exists"):
            sm.create_user("unique", "pass", Role.USER)

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
        sm.create_user("persistent", "pass123", Role.USER)
        sm.generate_api_key("persistent key")

        # Create new instance (simulates restart)
        SecurityManager._instance = None
        sm2 = SecurityManager()
        user = sm2.get_user("persistent")
        assert user is not None
        assert user.role == Role.USER
        assert len(sm2.list_api_keys()) >= 1

    def test_unknown_roles_are_rejected(self):
        with pytest.raises(ValueError):
            User.from_dict({"username": "bad", "role": "invalid_role"})


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
