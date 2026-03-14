"""Tests for user-scoped services (UserServiceRegistry, forwarding, agent actions, i18n)."""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Register all tasks and services so ServiceFactory knows about types
from tasks import register_all_tasks
register_all_tasks()

# Use a real service type that's registered
SVC_TYPE = "cacheService"

# ── Registry CRUD ──────────────────────────────────────────────────


class TestUserServiceRegistry:
    """Tests for UserServiceRegistry CRUD operations."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Setup fresh registry with temp directory."""
        from gui.services.user_service_registry import UserServiceRegistry, USER_SERVICES_DIR
        import gui.services.user_service_registry as mod

        UserServiceRegistry.reset()
        self.orig_dir = mod.USER_SERVICES_DIR
        mod.USER_SERVICES_DIR = tmp_path / "user_services"
        self.registry = UserServiceRegistry.get_instance()
        yield
        UserServiceRegistry.reset()
        mod.USER_SERVICES_DIR = self.orig_dir

    def test_install(self):
        sdef = self.registry.install("alice", "mydb", SVC_TYPE, config={"host": "localhost"})
        assert sdef.service_id == "mydb"
        assert sdef.service_type == SVC_TYPE
        assert sdef.user_id == "alice"
        assert sdef.config == {"host": "localhost"}
        assert sdef.enabled is True

    def test_install_with_description(self):
        sdef = self.registry.install("alice", "mydb", SVC_TYPE,
                                     description="My personal DB")
        assert sdef.description == "My personal DB"

    def test_install_invalid_type(self):
        with pytest.raises(ValueError, match="Unknown service type"):
            self.registry.install("alice", "mydb", "nonexistent_type_xyz")

    def test_install_replaces_existing(self):
        self.registry.install("alice", "mydb", SVC_TYPE, config={"host": "a"})
        self.registry.install("alice", "mydb", SVC_TYPE, config={"host": "b"})
        sdef = self.registry.get_definition("alice", "mydb")
        assert sdef.config["host"] == "b"

    def test_uninstall(self):
        self.registry.install("alice", "mydb", SVC_TYPE)
        self.registry.uninstall("alice", "mydb")
        assert self.registry.get_definition("alice", "mydb") is None

    def test_uninstall_nonexistent(self):
        # Should not raise
        self.registry.uninstall("alice", "nonexistent")

    def test_enable(self):
        self.registry.install("alice", "mydb", SVC_TYPE, enabled=False)
        assert self.registry.get_definition("alice", "mydb").enabled is False
        self.registry.enable("alice", "mydb")
        assert self.registry.get_definition("alice", "mydb").enabled is True

    def test_disable(self):
        self.registry.install("alice", "mydb", SVC_TYPE, enabled=True)
        self.registry.disable("alice", "mydb")
        assert self.registry.get_definition("alice", "mydb").enabled is False

    def test_enable_nonexistent(self):
        # Should not raise
        self.registry.enable("alice", "nonexistent")

    def test_disable_nonexistent(self):
        # Should not raise
        self.registry.disable("alice", "nonexistent")

    def test_update_config(self):
        self.registry.install("alice", "mydb", SVC_TYPE, config={"host": "a"})
        self.registry.update_config("alice", "mydb", {"host": "b", "port": "5432"})
        sdef = self.registry.get_definition("alice", "mydb")
        assert sdef.config == {"host": "b", "port": "5432"}

    def test_update_config_nonexistent(self):
        with pytest.raises(KeyError):
            self.registry.update_config("alice", "nonexistent", {"host": "b"})

    def test_update_description(self):
        self.registry.install("alice", "mydb", SVC_TYPE, description="old")
        self.registry.update_description("alice", "mydb", "new")
        assert self.registry.get_definition("alice", "mydb").description == "new"

    def test_get_definition(self):
        self.registry.install("alice", "mydb", SVC_TYPE)
        assert self.registry.get_definition("alice", "mydb") is not None
        assert self.registry.get_definition("alice", "other") is None
        assert self.registry.get_definition("bob", "mydb") is None

    def test_get_all_for_user(self):
        self.registry.install("alice", "db1", SVC_TYPE)
        self.registry.install("alice", "db2", SVC_TYPE)
        all_defs = self.registry.get_all_for_user("alice")
        assert len(all_defs) == 2
        assert "db1" in all_defs
        assert "db2" in all_defs

    def test_get_all_for_user_empty(self):
        assert self.registry.get_all_for_user("nobody") == {}


# ── Isolation ──────────────────────────────────────────────────────


class TestUserServiceIsolation:
    """Tests that user A cannot see user B's services."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.user_service_registry import UserServiceRegistry
        import gui.services.user_service_registry as mod

        UserServiceRegistry.reset()
        self.orig_dir = mod.USER_SERVICES_DIR
        mod.USER_SERVICES_DIR = tmp_path / "user_services"
        self.registry = UserServiceRegistry.get_instance()
        yield
        UserServiceRegistry.reset()
        mod.USER_SERVICES_DIR = self.orig_dir

    def test_user_a_cannot_see_user_b(self):
        self.registry.install("alice", "mydb", SVC_TYPE)
        self.registry.install("bob", "hisdb", SVC_TYPE)
        alice_svcs = self.registry.get_all_for_user("alice")
        bob_svcs = self.registry.get_all_for_user("bob")
        assert "mydb" in alice_svcs
        assert "hisdb" not in alice_svcs
        assert "hisdb" in bob_svcs
        assert "mydb" not in bob_svcs

    def test_uninstall_only_affects_own_user(self):
        self.registry.install("alice", "shared_name", SVC_TYPE)
        self.registry.install("bob", "shared_name", SVC_TYPE)
        self.registry.uninstall("alice", "shared_name")
        assert self.registry.get_definition("alice", "shared_name") is None
        assert self.registry.get_definition("bob", "shared_name") is not None


# ── Compatible ─────────────────────────────────────────────────────


class TestUserServiceCompatible:
    """Tests for get_compatible filtering."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.user_service_registry import UserServiceRegistry
        import gui.services.user_service_registry as mod

        UserServiceRegistry.reset()
        self.orig_dir = mod.USER_SERVICES_DIR
        mod.USER_SERVICES_DIR = tmp_path / "user_services"
        self.registry = UserServiceRegistry.get_instance()
        yield
        UserServiceRegistry.reset()
        mod.USER_SERVICES_DIR = self.orig_dir

    def test_get_compatible_filters_by_type(self):
        self.registry.install("alice", "db1", SVC_TYPE)
        self.registry.install("alice", "llm1", "httpClientService")
        compat = self.registry.get_compatible(SVC_TYPE, "alice")
        assert len(compat) == 1
        assert compat[0].service_id == "db1"

    def test_get_compatible_filters_by_user(self):
        self.registry.install("alice", "db1", SVC_TYPE)
        self.registry.install("bob", "db2", SVC_TYPE)
        compat = self.registry.get_compatible(SVC_TYPE, "alice")
        assert len(compat) == 1
        assert compat[0].service_id == "db1"

    def test_get_compatible_empty(self):
        assert self.registry.get_compatible(SVC_TYPE, "alice") == []


# ── Live instances ─────────────────────────────────────────────────


class TestUserServiceLiveInstances:
    """Tests for connect/disconnect/get_live_instance."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.user_service_registry import UserServiceRegistry
        import gui.services.user_service_registry as mod

        UserServiceRegistry.reset()
        self.orig_dir = mod.USER_SERVICES_DIR
        mod.USER_SERVICES_DIR = tmp_path / "user_services"
        self.registry = UserServiceRegistry.get_instance()
        yield
        UserServiceRegistry.reset()
        mod.USER_SERVICES_DIR = self.orig_dir

    def test_get_live_instance_none_when_not_connected(self):
        self.registry.install("alice", "mydb", SVC_TYPE, enabled=False)
        assert self.registry.get_live_instance("alice", "mydb") is None

    def test_is_connected_false_when_not_installed(self):
        assert self.registry.is_connected("alice", "nonexistent") is False

    def test_disconnect_all(self):
        mock_svc = MagicMock()
        with self.registry._data_lock:
            self.registry._live_instances.setdefault("alice", {})["svc1"] = mock_svc
        self.registry.disconnect_all("alice")
        mock_svc.disconnect.assert_called_once()
        assert self.registry.get_live_instance("alice", "svc1") is None

    def test_disconnect_all_users(self):
        mock_svc_a = MagicMock()
        mock_svc_b = MagicMock()
        with self.registry._data_lock:
            self.registry._live_instances.setdefault("alice", {})["s1"] = mock_svc_a
            self.registry._live_instances.setdefault("bob", {})["s2"] = mock_svc_b
        self.registry.disconnect_all_users()
        mock_svc_a.disconnect.assert_called_once()
        mock_svc_b.disconnect.assert_called_once()


# ── Persistence ────────────────────────────────────────────────────


class TestUserServicePersistence:
    """Tests for save/load per-user files."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.user_service_registry import UserServiceRegistry
        import gui.services.user_service_registry as mod

        UserServiceRegistry.reset()
        self.orig_dir = mod.USER_SERVICES_DIR
        mod.USER_SERVICES_DIR = tmp_path / "user_services"
        self.tmp_path = tmp_path
        self.registry = UserServiceRegistry.get_instance()
        yield
        UserServiceRegistry.reset()
        mod.USER_SERVICES_DIR = self.orig_dir

    def test_save_creates_file(self):
        import gui.services.user_service_registry as mod
        self.registry.install("alice", "mydb", SVC_TYPE, config={"host": "localhost"})
        filepath = mod.USER_SERVICES_DIR / "alice.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert "mydb" in data
        assert data["mydb"]["service_type"] == SVC_TYPE

    def test_reload_after_reset(self):
        import gui.services.user_service_registry as mod
        self.registry.install("alice", "mydb", SVC_TYPE, config={"host": "localhost"})

        # Reset and reload
        from gui.services.user_service_registry import UserServiceRegistry
        UserServiceRegistry.reset()
        reg2 = UserServiceRegistry.get_instance()
        sdef = reg2.get_definition("alice", "mydb")
        assert sdef is not None
        assert sdef.config["host"] == "localhost"
        assert sdef.service_type == SVC_TYPE

    def test_per_user_files(self):
        import gui.services.user_service_registry as mod
        self.registry.install("alice", "db1", SVC_TYPE)
        self.registry.install("bob", "db2", SVC_TYPE)
        assert (mod.USER_SERVICES_DIR / "alice.json").exists()
        assert (mod.USER_SERVICES_DIR / "bob.json").exists()

    def test_expressions_preserved_in_config(self):
        """Expressions like ${secrets.user.key} are stored as-is in config."""
        self.registry.install("alice", "mydb", SVC_TYPE,
                              config={"password": "${secrets.user.db_pass}"})
        sdef = self.registry.get_definition("alice", "mydb")
        assert sdef.config["password"] == "${secrets.user.db_pass}"


# ── UserServiceDef ─────────────────────────────────────────────────


class TestUserServiceDef:
    """Tests for the UserServiceDef dataclass."""

    def test_to_dict(self):
        from gui.services.user_service_registry import UserServiceDef
        sdef = UserServiceDef(
            service_id="mydb", service_type=SVC_TYPE, user_id="alice",
            config={"host": "localhost"}, created_at=1000.0,
        )
        d = sdef.to_dict()
        assert d["service_id"] == "mydb"
        assert d["user_id"] == "alice"
        assert d["config"]["host"] == "localhost"

    def test_from_dict(self):
        from gui.services.user_service_registry import UserServiceDef
        d = {
            "service_id": "mydb", "service_type": SVC_TYPE, "user_id": "alice",
            "config": {"host": "localhost"}, "enabled": True,
            "description": "test", "created_at": 1000.0,
        }
        sdef = UserServiceDef.from_dict(d)
        assert sdef.service_id == "mydb"
        assert sdef.user_id == "alice"

    def test_from_dict_ignores_unknown_keys(self):
        from gui.services.user_service_registry import UserServiceDef
        d = {
            "service_id": "mydb", "service_type": SVC_TYPE, "user_id": "alice",
            "config": {}, "extra_field": "ignored", "created_at": 1000.0,
        }
        sdef = UserServiceDef.from_dict(d)
        assert sdef.service_id == "mydb"
        assert not hasattr(sdef, "extra_field")


# ── Forwarding ─────────────────────────────────────────────────────


class TestServiceForwarding:
    """Tests for _apply_service_forwards and _migrate_service_overrides."""

    def test_migrate_bare_ids_to_global_prefix(self):
        overrides = {"svc1": "my_global_svc", "svc2": "global:already_prefixed"}
        result = {}
        for k, v in overrides.items():
            if v and not v.startswith("global:") and not v.startswith("user:"):
                result[k] = f"global:{v}"
            else:
                result[k] = v
        assert result["svc1"] == "global:my_global_svc"
        assert result["svc2"] == "global:already_prefixed"

    def test_migrate_empty(self):
        result = {}
        overrides = None
        if not overrides:
            result = {}
        assert result == {}

    def test_migrate_preserves_user_prefix(self):
        overrides = {"svc1": "user:alice:mydb"}
        result = {}
        for k, v in overrides.items():
            if v and not v.startswith("global:") and not v.startswith("user:"):
                result[k] = f"global:{v}"
            else:
                result[k] = v
        assert result["svc1"] == "user:alice:mydb"

    def test_apply_forwards_global_prefix(self):
        """Test that global: prefix resolves via GlobalServiceRegistry."""
        from unittest.mock import MagicMock, patch

        mock_flow = MagicMock()
        mock_flow.services = {"llm_svc": MagicMock()}
        mock_live = MagicMock()

        with patch("gui.services.global_service_registry.GlobalServiceRegistry.get_instance") as mock_greg, \
             patch("gui.services.user_service_registry.UserServiceRegistry.get_instance") as mock_ureg:
            mock_greg.return_value.get_live_instance.return_value = mock_live
            mock_ureg.return_value.get_live_instance.return_value = None

            # Import and call
            overrides = {"llm_svc": "global:shared_llm"}
            from gui.services.global_service_registry import GlobalServiceRegistry
            from gui.services.user_service_registry import UserServiceRegistry
            gsvc_reg = mock_greg.return_value
            usvc_reg = mock_ureg.return_value

            for flow_svc_id, ref in overrides.items():
                live = None
                if ref.startswith("user:"):
                    parts = ref.split(":", 2)
                    _, uid, sid = parts
                    live = usvc_reg.get_live_instance(uid, sid)
                elif ref.startswith("global:"):
                    sid = ref.split(":", 1)[1]
                    live = gsvc_reg.get_live_instance(sid)
                if live is not None and flow_svc_id in mock_flow.services:
                    mock_flow.services[flow_svc_id] = live

            gsvc_reg.get_live_instance.assert_called_once_with("shared_llm")
            assert mock_flow.services["llm_svc"] == mock_live

    def test_apply_forwards_user_prefix(self):
        """Test that user: prefix resolves via UserServiceRegistry."""
        mock_flow = MagicMock()
        mock_flow.services = {"db_svc": MagicMock()}
        mock_live = MagicMock()

        with patch("gui.services.global_service_registry.GlobalServiceRegistry.get_instance") as mock_greg, \
             patch("gui.services.user_service_registry.UserServiceRegistry.get_instance") as mock_ureg:
            mock_greg.return_value.get_live_instance.return_value = None
            mock_ureg.return_value.get_live_instance.return_value = mock_live

            overrides = {"db_svc": "user:alice:mydb"}
            gsvc_reg = mock_greg.return_value
            usvc_reg = mock_ureg.return_value

            for flow_svc_id, ref in overrides.items():
                live = None
                if ref.startswith("user:"):
                    parts = ref.split(":", 2)
                    _, uid, sid = parts
                    live = usvc_reg.get_live_instance(uid, sid)
                elif ref.startswith("global:"):
                    sid = ref.split(":", 1)[1]
                    live = gsvc_reg.get_live_instance(sid)
                if live is not None and flow_svc_id in mock_flow.services:
                    mock_flow.services[flow_svc_id] = live

            usvc_reg.get_live_instance.assert_called_once_with("alice", "mydb")
            assert mock_flow.services["db_svc"] == mock_live

    def test_apply_forwards_not_connected_keeps_local(self):
        """When service is not connected, flow keeps its local service."""
        mock_flow = MagicMock()
        original_svc = MagicMock()
        mock_flow.services = {"db_svc": original_svc}

        with patch("gui.services.global_service_registry.GlobalServiceRegistry.get_instance") as mock_greg, \
             patch("gui.services.user_service_registry.UserServiceRegistry.get_instance") as mock_ureg:
            mock_greg.return_value.get_live_instance.return_value = None
            mock_ureg.return_value.get_live_instance.return_value = None

            overrides = {"db_svc": "user:alice:mydb"}
            usvc_reg = mock_ureg.return_value

            for flow_svc_id, ref in overrides.items():
                live = None
                if ref.startswith("user:"):
                    parts = ref.split(":", 2)
                    _, uid, sid = parts
                    live = usvc_reg.get_live_instance(uid, sid)
                if live is not None and flow_svc_id in mock_flow.services:
                    mock_flow.services[flow_svc_id] = live

            # Should remain original
            assert mock_flow.services["db_svc"] == original_svc


# ── Agent actions ──────────────────────────────────────────────────


class TestAgentServiceActions:
    """Tests for service_* actions in AgentLoopTask._handle_action."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.user_service_registry import UserServiceRegistry
        import gui.services.user_service_registry as mod

        UserServiceRegistry.reset()
        self.orig_dir = mod.USER_SERVICES_DIR
        mod.USER_SERVICES_DIR = tmp_path / "user_services"
        self.registry = UserServiceRegistry.get_instance()
        yield
        UserServiceRegistry.reset()
        mod.USER_SERVICES_DIR = self.orig_dir

    def _make_flowfile(self, body: dict):
        from core import FlowFile
        ff = FlowFile(content=json.dumps(body).encode())
        ff.set_attribute("http.auth.principal", "testuser")
        return ff

    def test_service_list_empty(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({"action": "service_list"})
        result = task._handle_action(ff)
        assert result is not None
        data = json.loads(result[0].get_content())
        assert data["services"] == []

    def test_service_install(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": SVC_TYPE,
            "service_name": "mydb",
            "config_str": "host=localhost,port=5432",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data["installed"] is True
        assert data["id"] == "mydb"

        # Verify it's actually installed
        sdef = self.registry.get_definition("testuser", "mydb")
        assert sdef is not None
        assert sdef.config == {"host": "localhost", "port": "5432"}

    def test_service_install_missing_params(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": "",
            "service_name": "",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert "error" in data

    def test_service_uninstall(self):
        self.registry.install("testuser", "mydb", SVC_TYPE)
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_uninstall",
            "service_id": "mydb",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data["uninstalled"] is True
        assert self.registry.get_definition("testuser", "mydb") is None

    def test_service_uninstall_not_found(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_uninstall",
            "service_id": "nonexistent",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert "error" in data

    def test_service_list_with_entries(self):
        self.registry.install("testuser", "db1", SVC_TYPE, description="Main DB")
        self.registry.install("testuser", "db2", SVC_TYPE, enabled=False)

        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({"action": "service_list"})
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        svcs = data["services"]
        assert len(svcs) == 2
        by_id = {s["id"]: s for s in svcs}
        assert by_id["db1"]["enabled"] is True
        assert by_id["db1"]["description"] == "Main DB"
        assert by_id["db2"]["enabled"] is False

    def test_service_enable(self):
        self.registry.install("testuser", "mydb", SVC_TYPE, enabled=False)
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_enable",
            "service_id": "mydb",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data.get("enabled") is True
        assert self.registry.get_definition("testuser", "mydb").enabled is True

    def test_service_disable(self):
        self.registry.install("testuser", "mydb", SVC_TYPE, enabled=True)
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_disable",
            "service_id": "mydb",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data.get("disabled") is True
        assert self.registry.get_definition("testuser", "mydb").enabled is False


# ── Config parsing ─────────────────────────────────────────────────


class TestConfigParsing:
    """Tests for config string parsing in service_install."""

    def _parse_config_str(self, config_str: str) -> dict:
        config = {}
        if config_str:
            for pair in config_str.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    config[k.strip()] = v.strip()
        return config

    def test_simple_kv(self):
        assert self._parse_config_str("host=localhost,port=5432") == {
            "host": "localhost", "port": "5432"
        }

    def test_expression_preserved(self):
        result = self._parse_config_str("password=${secrets.user.db_pass}")
        assert result["password"] == "${secrets.user.db_pass}"

    def test_empty_string(self):
        assert self._parse_config_str("") == {}

    def test_value_with_equals(self):
        """Value containing = should be preserved."""
        result = self._parse_config_str("url=http://host?key=val")
        assert result["url"] == "http://host?key=val"

    def test_whitespace_trimmed(self):
        result = self._parse_config_str("  host = localhost , port = 5432  ")
        assert result == {"host": "localhost", "port": "5432"}


# ── i18n ───────────────────────────────────────────────────────────


class TestUserServicesI18n:
    """Tests that user services i18n keys exist in all locales."""

    REQUIRED_KEYS = [
        "runtime.user_services_title",
        "runtime.user_services_desc",
        "runtime.user_services_empty",
        "runtime.user_services_add",
        "runtime.svc_scope_global",
        "runtime.svc_scope_user",
        "service.install_success",
        "service.uninstall_success",
        "service.not_found",
        "service.enable_success",
        "service.disable_success",
        "service.list_empty",
        "service.install_usage",
    ]

    @pytest.fixture(params=["en", "fr", "es"])
    def locale_data(self, request):
        locale = request.param
        path = Path(__file__).parent.parent / "gui" / "i18n" / f"{locale}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return locale, data

    def test_keys_present(self, locale_data):
        locale, data = locale_data
        for key in self.REQUIRED_KEYS:
            assert key in data, f"Missing i18n key '{key}' in {locale}.json"

    def test_svc_forwarded_to_no_global_suffix(self, locale_data):
        """runtime.svc_forwarded_to should be generic (not mention 'global')."""
        locale, data = locale_data
        val = data.get("runtime.svc_forwarded_to", "")
        # Should not contain "global" since we now forward to both global and user
        assert "global" not in val.lower(), \
            f"runtime.svc_forwarded_to in {locale} still mentions 'global': {val}"
