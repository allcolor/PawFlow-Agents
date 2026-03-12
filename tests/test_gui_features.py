"""Tests for GUI-related features: auth helpers, secrets, parameter contexts."""

import json
import os
import tempfile
import pytest
from pathlib import Path

from core.security import SecurityManager, Role, User, Session
from core.secrets import SecretsManager, get_secrets_manager
from core.parameter_context import ParameterContext


# --- Auth helpers ---

class TestSecurityManagerAuth:

    def setup_method(self):
        """Create a fresh SecurityManager with a temp config dir."""
        self.tmpdir = tempfile.mkdtemp()
        # Override config paths
        import core.security as sec_mod
        self._orig_config = sec_mod.SECURITY_CONFIG_PATH
        self._orig_users = sec_mod.USERS_PATH
        sec_mod.SECURITY_CONFIG_PATH = os.path.join(self.tmpdir, "security.json")
        sec_mod.USERS_PATH = os.path.join(self.tmpdir, "users.json")
        # Reset singleton
        SecurityManager._instance = None
        self.sm = SecurityManager.get_instance()

    def teardown_method(self):
        import core.security as sec_mod
        sec_mod.SECURITY_CONFIG_PATH = self._orig_config
        sec_mod.USERS_PATH = self._orig_users
        SecurityManager._instance = None
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_auth_disabled_by_default(self):
        assert not self.sm.auth_enabled

    def test_enable_disable_auth(self):
        self.sm.enable_auth(True)
        assert self.sm.auth_enabled
        self.sm.enable_auth(False)
        assert not self.sm.auth_enabled

    def test_create_user_and_authenticate(self):
        self.sm.enable_auth(True)
        self.sm.create_user("testuser", "pass123", Role.EDITOR)
        session = self.sm.authenticate("testuser", "pass123")
        assert session is not None
        assert session.username == "testuser"
        assert session.role == Role.EDITOR

    def test_authenticate_wrong_password(self):
        self.sm.enable_auth(True)
        self.sm.create_user("bob", "correct", Role.VIEWER)
        assert self.sm.authenticate("bob", "wrong") is None

    def test_check_permission(self):
        self.sm.enable_auth(True)
        # Default admin user already exists; update its password for the test
        self.sm.update_user("admin", password="admin")
        self.sm.create_user("viewer", "viewer", Role.VIEWER)

        admin_session = self.sm.authenticate("admin", "admin")
        viewer_session = self.sm.authenticate("viewer", "viewer")

        assert self.sm.check_permission(admin_session, "flow.edit")
        assert self.sm.check_permission(admin_session, "user.manage")
        assert not self.sm.check_permission(viewer_session, "flow.edit")
        assert self.sm.check_permission(viewer_session, "monitor.view")

    def test_auth_disabled_allows_all(self):
        session = Session(session_id="test", username="any", role=Role.VIEWER)
        # Auth disabled: everything is allowed
        assert self.sm.check_permission(session, "flow.edit")
        assert self.sm.check_permission(session, "user.manage")

    def test_list_users(self):
        self.sm.create_user("alice", "pass", Role.EDITOR, email="alice@test.com")
        users = self.sm.list_users()
        # Default admin + alice
        alice = [u for u in users if u["username"] == "alice"]
        assert len(alice) == 1
        assert alice[0]["email"] == "alice@test.com"
        assert "password_hash" not in alice[0]

    def test_delete_user(self):
        self.sm.create_user("temp", "pass", Role.VIEWER)
        self.sm.delete_user("temp")
        assert self.sm.get_user("temp") is None

    def test_api_key_lifecycle(self):
        key = self.sm.generate_api_key("test key")
        assert self.sm.validate_api_key(key)
        self.sm.revoke_api_key(key)
        assert not self.sm.validate_api_key(key)

    def test_session_logout(self):
        self.sm.enable_auth(True)
        self.sm.create_user("user1", "pass", Role.EDITOR)
        session = self.sm.authenticate("user1", "pass")
        assert self.sm.get_session(session.session_id) is not None
        self.sm.logout(session.session_id)
        assert self.sm.get_session(session.session_id) is None


# --- Secrets ---

class TestSecretsManager:

    def test_encrypt_decrypt(self):
        sm = SecretsManager(key="testkey")
        encrypted = sm.encrypt("hello world")
        assert encrypted.startswith("enc:")
        assert sm.decrypt(encrypted) == "hello world"

    def test_is_encrypted(self):
        sm = SecretsManager(key="testkey")
        assert not sm.is_encrypted("plain text")
        assert sm.is_encrypted("enc:abc123")

    def test_decrypt_plaintext_passthrough(self):
        sm = SecretsManager(key="testkey")
        assert sm.decrypt("plain") == "plain"
        assert sm.decrypt("") == ""

    def test_encrypt_already_encrypted(self):
        sm = SecretsManager(key="testkey")
        encrypted = sm.encrypt("data")
        # Re-encrypting already encrypted data returns it as-is
        assert sm.encrypt(encrypted) == encrypted

    def test_wrong_key_fails(self):
        sm1 = SecretsManager(key="key1")
        encrypted = sm1.encrypt("secret")
        sm2 = SecretsManager(key="key2")
        with pytest.raises(ValueError):
            sm2.decrypt(encrypted)


# --- Parameter Context ---

class TestParameterContext:

    def test_basic_get(self):
        ctx = ParameterContext({"env": "prod", "batch": "100"})
        assert ctx.get("env") == "prod"
        assert ctx.get("batch") == "100"
        assert ctx.get("missing") is None
        assert ctx.get("missing", "default") == "default"

    def test_has(self):
        ctx = ParameterContext({"key": "val"})
        assert ctx.has("key")
        assert not ctx.has("other")

    def test_with_overrides(self):
        ctx = ParameterContext({"env": "dev", "db": "local"})
        new_ctx = ctx.with_overrides({"env": "prod"})
        assert new_ctx.get("env") == "prod"
        assert new_ctx.get("db") == "local"
        # Original unchanged
        assert ctx.get("env") == "dev"

    def test_parameters_returns_copy(self):
        ctx = ParameterContext({"a": "1"})
        params = ctx.parameters
        params["b"] = "2"
        assert not ctx.has("b")

    def test_with_mapping(self):
        parent = ParameterContext({"env": "prod", "key": "abc"})
        child = parent.with_mapping({
            "sub_env": "${flow.parameters.env}",
            "mode": "fast",
        })
        assert child.get("sub_env") == "prod"
        assert child.get("mode") == "fast"

    def test_resolve_expression(self):
        ctx = ParameterContext({"name": "world"})
        result = ctx.resolve("Hello ${flow.parameters.name}!")
        assert result == "Hello world!"
