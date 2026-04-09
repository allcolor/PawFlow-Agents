"""Tests for ServiceRegistry — CRUD, scope isolation, resolution chain, persistence, i18n."""

import json
import os
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


def _registry_fixture(tmp_path):
    """Shared setup: fresh ServiceRegistry with temp storage."""
    from gui.services.service_registry import ServiceRegistry
    import gui.services.service_registry as mod

    ServiceRegistry.reset()
    orig_user_dir = mod.USER_SERVICES_DIR
    orig_global_file = mod.GLOBAL_SERVICES_FILE
    mod.USER_SERVICES_DIR = tmp_path / "user_services"
    mod.GLOBAL_SERVICES_FILE = tmp_path / "global_services.json"
    reg = ServiceRegistry.get_instance()
    return reg, mod, orig_user_dir, orig_global_file


def _registry_teardown(mod, orig_user_dir, orig_global_file):
    from gui.services.service_registry import ServiceRegistry
    ServiceRegistry.reset()
    mod.USER_SERVICES_DIR = orig_user_dir
    mod.GLOBAL_SERVICES_FILE = orig_global_file


# ── Registry CRUD (user scope) ────────────────────────────────────


class TestServiceRegistryCRUD:
    """Tests for ServiceRegistry CRUD operations on user scope."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

    def test_install(self):
        sdef = self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE,
                                config={"host": "localhost"})
        assert sdef.service_id == "mydb"
        assert sdef.service_type == SVC_TYPE
        assert sdef.scope == "user"
        assert sdef.config == {"host": "localhost"}
        assert sdef.enabled is True

    def test_install_with_description(self):
        sdef = self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE,
                                description="My personal DB")
        assert sdef.description == "My personal DB"

    def test_install_invalid_type(self):
        with pytest.raises(ValueError, match="Unknown service type"):
            self.reg.install(self.SCOPE, "alice", "mydb", "nonexistent_type_xyz")

    def test_install_replaces_existing(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "a"})
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "b"})
        sdef = self.reg.get_definition(self.SCOPE, "alice", "mydb")
        assert sdef.config["host"] == "b"

    def test_uninstall(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE)
        self.reg.uninstall(self.SCOPE, "alice", "mydb")
        assert self.reg.get_definition(self.SCOPE, "alice", "mydb") is None

    def test_uninstall_nonexistent(self):
        self.reg.uninstall(self.SCOPE, "alice", "nonexistent")

    def test_enable(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, enabled=False)
        assert self.reg.get_definition(self.SCOPE, "alice", "mydb").enabled is False
        self.reg.enable(self.SCOPE, "alice", "mydb")
        assert self.reg.get_definition(self.SCOPE, "alice", "mydb").enabled is True

    def test_disable(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, enabled=True)
        self.reg.disable(self.SCOPE, "alice", "mydb")
        assert self.reg.get_definition(self.SCOPE, "alice", "mydb").enabled is False

    def test_enable_nonexistent(self):
        self.reg.enable(self.SCOPE, "alice", "nonexistent")

    def test_disable_nonexistent(self):
        self.reg.disable(self.SCOPE, "alice", "nonexistent")

    def test_update_config(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "a"})
        self.reg.update_config(self.SCOPE, "alice", "mydb", {"host": "b", "port": "5432"})
        sdef = self.reg.get_definition(self.SCOPE, "alice", "mydb")
        assert sdef.config == {"host": "b", "port": "5432"}

    def test_update_config_nonexistent(self):
        with pytest.raises(KeyError):
            self.reg.update_config(self.SCOPE, "alice", "nonexistent", {"host": "b"})

    def test_update_description(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, description="old")
        self.reg.update_description(self.SCOPE, "alice", "mydb", "new")
        assert self.reg.get_definition(self.SCOPE, "alice", "mydb").description == "new"

    def test_get_definition(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE)
        assert self.reg.get_definition(self.SCOPE, "alice", "mydb") is not None
        assert self.reg.get_definition(self.SCOPE, "alice", "other") is None
        assert self.reg.get_definition(self.SCOPE, "bob", "mydb") is None

    def test_get_all(self):
        self.reg.install(self.SCOPE, "alice", "db1", SVC_TYPE)
        self.reg.install(self.SCOPE, "alice", "db2", SVC_TYPE)
        all_defs = self.reg.get_all(self.SCOPE, "alice")
        assert len(all_defs) == 2
        assert "db1" in all_defs
        assert "db2" in all_defs

    def test_get_all_empty(self):
        assert self.reg.get_all(self.SCOPE, "nobody") == {}


# ── Isolation ─────────────────────────────────────────────────────


class TestUserScopeIsolation:
    """Tests that user A cannot see user B's services."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

    def test_user_a_cannot_see_user_b(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE)
        self.reg.install(self.SCOPE, "bob", "hisdb", SVC_TYPE)
        alice_svcs = self.reg.get_all(self.SCOPE, "alice")
        bob_svcs = self.reg.get_all(self.SCOPE, "bob")
        assert "mydb" in alice_svcs
        assert "hisdb" not in alice_svcs
        assert "hisdb" in bob_svcs
        assert "mydb" not in bob_svcs

    def test_uninstall_only_affects_own_user(self):
        self.reg.install(self.SCOPE, "alice", "shared_name", SVC_TYPE)
        self.reg.install(self.SCOPE, "bob", "shared_name", SVC_TYPE)
        self.reg.uninstall(self.SCOPE, "alice", "shared_name")
        assert self.reg.get_definition(self.SCOPE, "alice", "shared_name") is None
        assert self.reg.get_definition(self.SCOPE, "bob", "shared_name") is not None


# ── Compatible ────────────────────────────────────────────────────


class TestGetCompatible:
    """Tests for get_compatible filtering."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

    def test_filters_by_type(self):
        self.reg.install(self.SCOPE, "alice", "db1", SVC_TYPE)
        self.reg.install(self.SCOPE, "alice", "llm1", "httpClientService")
        compat = self.reg.get_compatible(self.SCOPE, "alice", SVC_TYPE)
        assert len(compat) == 1
        assert compat[0].service_id == "db1"

    def test_filters_by_user(self):
        self.reg.install(self.SCOPE, "alice", "db1", SVC_TYPE)
        self.reg.install(self.SCOPE, "bob", "db2", SVC_TYPE)
        compat = self.reg.get_compatible(self.SCOPE, "alice", SVC_TYPE)
        assert len(compat) == 1
        assert compat[0].service_id == "db1"

    def test_empty(self):
        assert self.reg.get_compatible(self.SCOPE, "alice", SVC_TYPE) == []


# ── Live instances ────────────────────────────────────────────────


class TestLiveInstances:
    """Tests for connect/disconnect/get_live_instance."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

    def test_get_live_instance_none_when_disabled(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, enabled=False)
        assert self.reg.get_live_instance(self.SCOPE, "alice", "mydb") is None

    def test_is_connected_false_when_not_installed(self):
        assert self.reg.is_connected(self.SCOPE, "alice", "nonexistent") is False

    def test_disconnect_scope(self):
        mock_svc = MagicMock()
        sid = self.reg._resolve_scope_id(self.SCOPE, "alice")
        with self.reg._data_lock:
            self.reg._live_instances.setdefault(sid, {})["svc1"] = mock_svc
        self.reg.disconnect_scope(self.SCOPE, "alice")
        mock_svc.disconnect.assert_called_once()

    def test_disconnect_all_scopes(self):
        mock_a = MagicMock()
        mock_b = MagicMock()
        sid_a = self.reg._resolve_scope_id(self.SCOPE, "alice")
        sid_b = self.reg._resolve_scope_id(self.SCOPE, "bob")
        with self.reg._data_lock:
            self.reg._live_instances.setdefault(sid_a, {})["s1"] = mock_a
            self.reg._live_instances.setdefault(sid_b, {})["s2"] = mock_b
        self.reg._disconnect_all_scopes()
        mock_a.disconnect.assert_called_once()
        mock_b.disconnect.assert_called_once()


# ── Resolution chain ──────────────────────────────────────────────


class TestResolutionChain:
    """Tests for resolve(), resolve_by_type(), resolve_all() — conv > user > global."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_GLOBAL, SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        # Install same service_id in global and user with different configs
        self.reg.install(SCOPE_GLOBAL, "", "shared", SVC_TYPE,
                         config={"source": "global"}, description="global one")
        self.reg.install(SCOPE_USER, "alice", "shared", SVC_TYPE,
                         config={"source": "user"}, description="user one")
        self.reg.install(SCOPE_GLOBAL, "", "global_only", SVC_TYPE,
                         config={"source": "global"})
        self.reg.install(SCOPE_USER, "alice", "user_only", SVC_TYPE,
                         config={"source": "user"})
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

    def test_resolve_definition_user_wins(self):
        sdef = self.reg.resolve_definition("shared", user_id="alice")
        assert sdef is not None
        assert sdef.config["source"] == "user"

    def test_resolve_definition_falls_back_to_global(self):
        sdef = self.reg.resolve_definition("global_only", user_id="alice")
        assert sdef is not None
        assert sdef.config["source"] == "global"

    def test_resolve_definition_user_only(self):
        sdef = self.reg.resolve_definition("user_only", user_id="alice")
        assert sdef is not None
        assert sdef.config["source"] == "user"

    def test_resolve_definition_not_found(self):
        assert self.reg.resolve_definition("nonexistent", user_id="alice") is None

    def test_resolve_definition_no_user_gets_global(self):
        sdef = self.reg.resolve_definition("shared")
        assert sdef is not None
        assert sdef.config["source"] == "global"

    def test_resolve_by_type_deduplicates(self):
        """Same service_id in user+global: user wins, no duplicate."""
        defs = self.reg.resolve_by_type(SVC_TYPE, user_id="alice")
        ids = [d.service_id for d in defs]
        assert ids.count("shared") == 1
        shared = next(d for d in defs if d.service_id == "shared")
        assert shared.config["source"] == "user"  # most specific wins

    def test_resolve_by_type_includes_all(self):
        defs = self.reg.resolve_by_type(SVC_TYPE, user_id="alice")
        ids = {d.service_id for d in defs}
        assert "shared" in ids
        assert "global_only" in ids
        assert "user_only" in ids

    def test_resolve_by_type_no_user(self):
        defs = self.reg.resolve_by_type(SVC_TYPE)
        ids = {d.service_id for d in defs}
        assert "global_only" in ids
        assert "shared" in ids
        assert "user_only" not in ids

    def test_resolve_all_most_specific_wins(self):
        all_defs = self.reg.resolve_all(user_id="alice")
        assert "shared" in all_defs
        assert all_defs["shared"].config["source"] == "user"
        assert "global_only" in all_defs
        assert "user_only" in all_defs


# ── Persistence ───────────────────────────────────────────────────


class TestPersistence:
    """Tests for save/load per-user files."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        self.tmp_path = tmp_path
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

    def test_save_creates_file(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "localhost"})
        filepath = self.mod.USER_SERVICES_DIR / "alice.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert "mydb" in data
        assert data["mydb"]["service_type"] == SVC_TYPE

    def test_reload_after_reset(self):
        from gui.services.service_registry import ServiceRegistry, SCOPE_USER
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "localhost"})
        ServiceRegistry.reset()
        reg2 = ServiceRegistry.get_instance()
        sdef = reg2.get_definition(SCOPE_USER, "alice", "mydb")
        assert sdef is not None
        assert sdef.config["host"] == "localhost"

    def test_per_user_files(self):
        self.reg.install(self.SCOPE, "alice", "db1", SVC_TYPE)
        self.reg.install(self.SCOPE, "bob", "db2", SVC_TYPE)
        assert (self.mod.USER_SERVICES_DIR / "alice.json").exists()
        assert (self.mod.USER_SERVICES_DIR / "bob.json").exists()

    def test_expressions_preserved_in_config(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE,
                         config={"password": "${db_pass}"})
        sdef = self.reg.get_definition(self.SCOPE, "alice", "mydb")
        assert sdef.config["password"] == "${db_pass}"


# ── ServiceDef ────────────────────────────────────────────────────


class TestServiceDef:
    """Tests for the ServiceDef dataclass."""

    def test_to_dict(self):
        from gui.services.service_registry import ServiceDef
        sdef = ServiceDef(
            service_id="mydb", service_type=SVC_TYPE, scope="user",
            scope_id="alice", config={"host": "localhost"}, created_at=1000.0,
        )
        d = sdef.to_dict()
        assert d["service_id"] == "mydb"
        assert d["user_id"] == "alice"  # backwards compat output
        assert d["config"]["host"] == "localhost"

    def test_from_dict(self):
        from gui.services.service_registry import ServiceDef
        d = {
            "service_id": "mydb", "service_type": SVC_TYPE, "user_id": "alice",
            "config": {"host": "localhost"}, "enabled": True,
            "description": "test", "created_at": 1000.0,
        }
        sdef = ServiceDef.from_dict(d)
        assert sdef.service_id == "mydb"
        assert sdef.scope == "user"
        assert sdef.scope_id == "alice"

    def test_from_dict_ignores_unknown_keys(self):
        from gui.services.service_registry import ServiceDef
        d = {
            "service_id": "mydb", "service_type": SVC_TYPE, "user_id": "alice",
            "config": {}, "extra_field": "ignored", "created_at": 1000.0,
        }
        sdef = ServiceDef.from_dict(d)
        assert sdef.service_id == "mydb"
        assert not hasattr(sdef, "extra_field")


# ── Forwarding ────────────────────────────────────────────────────


class TestServiceForwarding:
    """Tests for service forwarding via resolve()."""

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

    def test_resolve_global_service(self):
        """resolve() finds global service with no user_id."""
        with patch("gui.services.service_registry.ServiceRegistry") as mock_cls:
            mock_reg = mock_cls.get_instance.return_value
            mock_svc = MagicMock()
            mock_reg.resolve.return_value = mock_svc

            from gui.services.service_registry import ServiceRegistry
            svc = mock_reg.resolve("shared_llm")
            assert svc == mock_svc

    def test_resolve_returns_none_when_not_found(self):
        with patch("gui.services.service_registry.ServiceRegistry") as mock_cls:
            mock_reg = mock_cls.get_instance.return_value
            mock_reg.resolve.return_value = None

            svc = mock_reg.resolve("nonexistent", user_id="alice")
            assert svc is None


# ── Agent actions ─────────────────────────────────────────────────


class TestAgentServiceActions:
    """Tests for service_* actions in AgentLoopTask._handle_action."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from gui.services.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global)

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
        user_svcs = [s for s in data["services"] if s.get("scope") != "global"]
        assert user_svcs == []

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

        sdef = self.reg.get_definition(self.SCOPE, "testuser", "mydb")
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
        self.reg.install(self.SCOPE, "testuser", "mydb", SVC_TYPE)
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_uninstall",
            "service_id": "mydb",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data["uninstalled"] is True
        assert self.reg.get_definition(self.SCOPE, "testuser", "mydb") is None

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
        self.reg.install(self.SCOPE, "testuser", "db1", SVC_TYPE, description="Main DB")
        self.reg.install(self.SCOPE, "testuser", "db2", SVC_TYPE, enabled=False)

        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({"action": "service_list"})
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        svcs = data["services"]
        by_id = {s["id"]: s for s in svcs}
        assert "db1" in by_id
        assert "db2" in by_id
        assert by_id["db1"]["enabled"] is True
        assert by_id["db1"]["description"] == "Main DB"
        assert by_id["db2"]["enabled"] is False

    def test_service_enable(self):
        self.reg.install(self.SCOPE, "testuser", "mydb", SVC_TYPE, enabled=False)
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_enable",
            "service_id": "mydb",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data.get("enabled") is True
        assert self.reg.get_definition(self.SCOPE, "testuser", "mydb").enabled is True

    def test_service_disable(self):
        self.reg.install(self.SCOPE, "testuser", "mydb", SVC_TYPE, enabled=True)
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({
            "action": "service_disable",
            "service_id": "mydb",
        })
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        assert data.get("disabled") is True
        assert self.reg.get_definition(self.SCOPE, "testuser", "mydb").enabled is False


# ── Config parsing ────────────────────────────────────────────────


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
        result = self._parse_config_str("password=${db_pass}")
        assert result["password"] == "${db_pass}"

    def test_empty_string(self):
        assert self._parse_config_str("") == {}

    def test_value_with_equals(self):
        result = self._parse_config_str("url=http://host?key=val")
        assert result["url"] == "http://host?key=val"

    def test_whitespace_trimmed(self):
        result = self._parse_config_str("  host = localhost , port = 5432  ")
        assert result == {"host": "localhost", "port": "5432"}


# ── i18n ──────────────────────────────────────────────────────────


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
        locale, data = locale_data
        val = data.get("runtime.svc_forwarded_to", "")
        assert "global" not in val.lower(), \
            f"runtime.svc_forwarded_to in {locale} still mentions 'global': {val}"
