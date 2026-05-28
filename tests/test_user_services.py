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
    """Shared setup: fresh ServiceRegistry with per-test temp storage.

    service_registry resolves its storage root lazily via
    _global_services_dir() / _user_services_dir(), which derive from
    core.paths.RUNTIME_DIR. We monkeypatch those lazy getters so each
    test gets a clean slate without touching module globals (none of
    those exist any more) or the session-wide conftest tmpdir.
    """
    from core.service_registry import ServiceRegistry
    import core.service_registry as mod

    ServiceRegistry.reset()
    orig_global = mod._global_services_dir
    orig_user = mod._user_services_dir
    mod._global_services_dir = lambda: tmp_path / "global_services"
    mod._user_services_dir = lambda: tmp_path / "user_services"
    reg = ServiceRegistry.get_instance()
    return reg, mod, orig_user, orig_global


def _registry_teardown(mod, orig_user, orig_global):
    from core.service_registry import ServiceRegistry
    ServiceRegistry.reset()
    mod._user_services_dir = orig_user
    mod._global_services_dir = orig_global


# ── Registry CRUD (user scope) ────────────────────────────────────


class TestServiceRegistryCRUD:
    """Tests for ServiceRegistry CRUD operations on user scope."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from core.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

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
        from core.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

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
        from core.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

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
        from core.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

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
        from core.service_registry import SCOPE_GLOBAL, SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
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
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

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
        from core.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        self.tmp_path = tmp_path
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

    def test_save_creates_file(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "localhost"})
        filepath = self.mod._user_services_dir() / "alice" / "mydb.json"
        assert filepath.exists()
        data = json.loads(filepath.read_text(encoding="utf-8"))
        assert data["service_type"] == SVC_TYPE

    def test_reload_after_reset(self):
        from core.service_registry import ServiceRegistry, SCOPE_USER
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE, config={"host": "localhost"})
        ServiceRegistry.reset()
        reg2 = ServiceRegistry.get_instance()
        sdef = reg2.get_definition(SCOPE_USER, "alice", "mydb")
        assert sdef is not None
        assert sdef.config["host"] == "localhost"

    def test_per_user_files(self):
        self.reg.install(self.SCOPE, "alice", "db1", SVC_TYPE)
        self.reg.install(self.SCOPE, "bob", "db2", SVC_TYPE)
        assert (self.mod._user_services_dir() / "alice" / "db1.json").exists()
        assert (self.mod._user_services_dir() / "bob" / "db2.json").exists()

    def test_expressions_preserved_in_config(self):
        self.reg.install(self.SCOPE, "alice", "mydb", SVC_TYPE,
                         config={"password": "${db_pass}"})
        sdef = self.reg.get_definition(self.SCOPE, "alice", "mydb")
        assert sdef.config["password"] == "${db_pass}"

    def test_sensitive_expression_reference_is_encrypted_on_disk(self):
        from core.service_registry import ServiceRegistry, SCOPE_USER

        self.reg.install(
            self.SCOPE,
            "alice",
            "drive1",
            "rcloneFilesystem",
            config={"rclone_type": "sftp", "pass": "${rclone_pass}"},
            enabled=False,
        )
        filepath = self.mod._user_services_dir() / "alice" / "drive1.json"
        data = json.loads(filepath.read_text(encoding="utf-8"))

        assert data["config"]["pass"].startswith("enc:")
        assert data["config"]["pass"] != "${rclone_pass}"

        ServiceRegistry.reset()
        reg2 = ServiceRegistry.get_instance()
        sdef = reg2.get_definition(SCOPE_USER, "alice", "drive1")
        assert sdef.config["pass"] == "${rclone_pass}"

    def test_live_service_resolves_sensitive_expression_after_decrypt(self, monkeypatch):
        marker = "$" + "{rclone_pass}"
        monkeypatch.setattr(
            "core.expression._load_user_secrets",
            lambda username: {"rclone_pass": "resolved-pass"},
        )

        self.reg.install(
            self.SCOPE,
            "alice",
            "drive1",
            "rcloneFilesystem",
            config={"rclone_type": "sftp", "pass": marker},
            enabled=True,
        )
        svc = self.reg.get_live_instance(self.SCOPE, "alice", "drive1")

        assert svc.config.get("pass") == "resolved-pass"

    def test_conversation_service_sensitive_fields_are_encrypted_in_extras(self, monkeypatch):
        from core.service_registry import ServiceRegistry, SCOPE_CONV, CONV_EXTRAS_KEY

        class _Store:
            def __init__(self):
                self.extras = {}

            def get_extra(self, cid, key, default=None):
                return self.extras.get((cid, key), default)

            def set_extra(self, cid, key, value):
                self.extras[(cid, key)] = value

        store = _Store()
        monkeypatch.setattr("core.conversation_store.ConversationStore.instance", staticmethod(lambda: store))

        self.reg.install(
            SCOPE_CONV,
            "conv1",
            "drive1",
            "rcloneFilesystem",
            config={"rclone_type": "sftp", "pass": "${rclone_pass}"},
            enabled=False,
        )
        stored = store.extras[("conv1", CONV_EXTRAS_KEY)]["drive1"]
        assert stored["config"]["pass"].startswith("enc:")

        ServiceRegistry.reset()
        reg2 = ServiceRegistry.get_instance()
        sdef = reg2.get_definition(SCOPE_CONV, "conv1", "drive1")
        assert sdef.config["pass"] == "${rclone_pass}"


# ── ServiceDef ────────────────────────────────────────────────────


class TestServiceDef:
    """Tests for the ServiceDef dataclass."""

    def test_to_dict(self):
        from core.service_registry import ServiceDef
        sdef = ServiceDef(
            service_id="mydb", service_type=SVC_TYPE, scope="user",
            scope_id="alice", config={"host": "localhost"}, created_at=1000.0,
        )
        d = sdef.to_dict()
        assert d["service_id"] == "mydb"
        assert d["user_id"] == "alice"  # backwards compat output
        assert d["config"]["host"] == "localhost"

    def test_from_dict(self):
        from core.service_registry import ServiceDef
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
        from core.service_registry import ServiceDef
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
        with patch("core.service_registry.ServiceRegistry") as mock_cls:
            mock_reg = mock_cls.get_instance.return_value
            mock_svc = MagicMock()
            mock_reg.resolve.return_value = mock_svc

            from core.service_registry import ServiceRegistry
            svc = mock_reg.resolve("shared_llm")
            assert svc == mock_svc

    def test_resolve_returns_none_when_not_found(self):
        with patch("core.service_registry.ServiceRegistry") as mock_cls:
            mock_reg = mock_cls.get_instance.return_value
            mock_reg.resolve.return_value = None

            svc = mock_reg.resolve("nonexistent", user_id="alice")
            assert svc is None


# ── Agent actions ─────────────────────────────────────────────────


class TestAgentServiceActions:
    """Tests for service_* actions in AgentLoopTask._handle_action."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from core.service_registry import SCOPE_USER
        self.SCOPE = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

    def _make_flowfile(self, body: dict):
        from core import FlowFile
        ff = FlowFile(content=json.dumps(body).encode())
        ff.set_attribute("http.auth.principal", "testuser")
        return ff

    def test_service_list_empty(self):
        from tasks.ai.agent_loop import AgentLoopTask
        task = AgentLoopTask({"conversation_store": True, "api_key": "test-key"})
        ff = self._make_flowfile({"action": "list_services"})
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

    def test_service_install_runs_prepare_install_before_connect(self):
        from core import ServiceFactory
        from core.base_service import BaseService
        from tasks.ai.actions.service_flow import _handle_service_flow

        calls = []

        class PrepareInstallTestService(BaseService):
            TYPE = "prepareInstallTestService"

            def get_parameter_schema(self):
                return {}

            def prepare_install(self, reporter=None):
                calls.append(("prepare", dict(self.config)))
                if reporter:
                    reporter.step("validating", "prepared")
                return {"ok": True}

            def _create_connection(self):
                calls.append(("connect", dict(self.config)))
                return {"ok": True}

            def _close_connection(self):
                pass

        ServiceFactory.register(PrepareInstallTestService)
        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": "prepareInstallTestService",
            "service_name": "prepared",
            "config": {"x": "1"},
        })

        result = _handle_service_flow(None, "service_install", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        assert data["installed"] is True
        assert data["install_prepared"] == {"ok": True}
        assert data["install_state"]["status"] == "ready"
        assert data["install_state"]["phase"] == "ready"
        assert [name for name, _cfg in calls] == ["prepare", "connect"]

    def test_service_install_rejects_concurrent_prepare_install(self):
        from core import ServiceFactory
        from core.base_service import BaseService
        from core.service_install import update_install_state
        from tasks.ai.actions.service_flow import _handle_service_flow

        calls = []

        class BusyPrepareInstallTestService(BaseService):
            TYPE = "busyPrepareInstallTestService"

            def get_parameter_schema(self):
                return {}

            def prepare_install(self, reporter=None):
                calls.append("prepare")
                return {"ok": True}

            def _create_connection(self):
                return {"ok": True}

            def _close_connection(self):
                pass

        ServiceFactory.register(BusyPrepareInstallTestService)
        update_install_state(
            "user", "testuser", "busy",
            status="installing",
            service_type="busyPrepareInstallTestService",
            phase="downloading_models",
            message="Download in progress",
        )
        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": "busyPrepareInstallTestService",
            "service_name": "busy",
        })

        result = _handle_service_flow(None, "service_install", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        assert "already running" in data["error"]
        assert data["install_state"]["status"] == "installing"
        assert data["install_state"]["phase"] == "downloading_models"
        assert calls == []

    def test_service_install_status_log_and_cancel_actions(self):
        from core.file_store import FileStore
        from core.service_install import append_install_log, update_install_state
        from tasks.ai.actions.service_flow import _handle_service_flow

        update_install_state(
            "user", "testuser", "svc1",
            status="installing",
            service_type="voicebox",
            phase="creating_venv",
            message="Creating venv",
        )
        append_install_log("user", "testuser", "svc1", {
            "status": "running",
            "phase": "creating_venv",
            "message": "Creating venv",
        })

        status_ff = self._make_flowfile({
            "action": "service_install_status",
            "service_name": "svc1",
        })
        status_result = _handle_service_flow(None, "service_install_status", json.loads(status_ff.get_content()), None, "testuser", status_ff)
        status_data = json.loads(status_result[0].get_content())
        assert status_data["install_state"]["status"] == "installing"

        log_ff = self._make_flowfile({
            "action": "service_install_log",
            "service_name": "svc1",
            "scope": "user",
            "conversation_id": "conv1",
            "download": True,
        })
        log_result = _handle_service_flow(None, "service_install_log", json.loads(log_ff.get_content()), None, "testuser", log_ff)
        log_data = json.loads(log_result[0].get_content())
        assert log_data["log"][-1]["phase"] == "creating_venv"
        assert log_data["download_url"].startswith("fs://filestore/")
        fid = log_data["download_url"].split("/", 4)[3]
        assert FileStore.instance().get(fid, user_id="testuser") is not None

        cancel_ff = self._make_flowfile({
            "action": "service_install_cancel",
            "service_name": "svc1",
        })
        cancel_result = _handle_service_flow(None, "service_install_cancel", json.loads(cancel_ff.get_content()), None, "testuser", cancel_ff)
        cancel_data = json.loads(cancel_result[0].get_content())
        assert cancel_data["install_state"]["status"] == "cancelled"
        assert cancel_data["install_state"]["phase"] == "cancelled"

    def test_service_install_status_global_requires_admin(self):
        from tasks.ai.actions.service_flow import _handle_service_flow

        ff = self._make_flowfile({
            "action": "service_install_status",
            "service_name": "global-svc",
            "scope": "global",
        })

        result = _handle_service_flow(None, "service_install_status", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        assert data["error"] == "Requires admin role for global scope"
        assert result[0].get_attribute("http.response.status") == "403"

    def test_service_install_respects_explicit_global_scope_with_conversation_id(self):
        from tasks.ai.actions.service_flow import _handle_service_flow

        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": SVC_TYPE,
            "service_name": "globaldb",
            "scope": "global",
            "conversation_id": "conv1",
            "config": {"host": "global"},
        })
        ff.set_attribute("http.auth.roles", "admin")

        result = _handle_service_flow(None, "service_install", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        assert data["installed"] is True
        assert self.reg.get_definition("global", "", "globaldb") is not None
        assert self.reg.get_definition("conv", "conv1", "globaldb") is None

    def test_agent_service_install_forces_conversation_scope(self):
        from tasks.ai.actions.service_flow import _handle_service_flow

        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": SVC_TYPE,
            "service_name": "agentdb",
            "scope": "global",
            "conversation_id": "conv1",
            "_agent_name": "assistant",
            "config": {"host": "conv"},
        })
        ff.set_attribute("http.auth.roles", "admin")

        result = _handle_service_flow(None, "service_install", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        assert data["installed"] is True
        assert self.reg.get_definition("conv", "conv1", "agentdb") is not None
        assert self.reg.get_definition("global", "", "agentdb") is None

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

    def test_server_filesystem_schema_requires_root(self):
        from core import ServiceFactory

        svc_cls = ServiceFactory.get("filesystem")
        schema = svc_cls({"root": str(Path.cwd())}).get_parameter_schema()

        assert schema["root"]["required"] is True

    def test_service_install_rejects_filesystem_without_root(self):
        from tasks.ai.actions.service_flow import _handle_service_flow

        ff = self._make_flowfile({
            "action": "service_install",
            "service_type": "filesystem",
            "service_name": "workspace",
            "config": {},
        })

        result = _handle_service_flow(None, "service_install", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        assert data["error"] == "Missing required service config: root"
        assert self.reg.get_definition("user", "testuser", "workspace") is None

    def test_filesystem_services_are_not_relays(self):
        from core.relay_bindings import link_relay, list_available_relays

        self.reg.install(
            "user", "testuser", "workspace", "filesystem",
            config={"root": str(Path.cwd())}, enabled=False)
        self.reg.install(
            "user", "testuser", "relay1", "relay",
            config={"token": "token"}, enabled=False)

        assert [r["relay_id"] for r in list_available_relays(user_id="testuser")] == ["relay1"]
        with pytest.raises(ValueError, match="Relay service 'workspace' not found"):
            link_relay("conv1", "workspace", user_id="testuser")

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
        ff = self._make_flowfile({"action": "list_services"})
        result = task._handle_action(ff)
        data = json.loads(result[0].get_content())
        svcs = data["services"]
        by_id = {s["service_id"]: s for s in svcs}
        assert "db1" in by_id
        assert "db2" in by_id
        assert by_id["db1"]["enabled"] is True
        assert by_id["db1"]["description"] == "Main DB"
        assert by_id["db2"]["enabled"] is False

    def test_service_list_includes_conversation_scope_when_requested(self):
        self.reg.install("conv", "conv1", "convdb", SVC_TYPE, config={"host": "conv"})

        from tasks.ai.actions.service_flow import _handle_service_flow
        ff = self._make_flowfile({"action": "list_services", "conversation_id": "conv1"})
        result = _handle_service_flow(None, "list_services", json.loads(ff.get_content()), None, "testuser", ff)
        data = json.loads(result[0].get_content())

        by_id = {s["service_id"]: s for s in data["services"]}
        assert by_id["convdb"]["scope"] == "conv"
        assert by_id["convdb"]["service_type"] == SVC_TYPE

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


# ── Resource uniqueness ──────────────────────────────────────────


class TestResourceConflict:
    """Tests for cross-scope resource uniqueness enforcement."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        from core.service_registry import SCOPE_GLOBAL, SCOPE_USER
        self.SCOPE_GLOBAL = SCOPE_GLOBAL
        self.SCOPE_USER = SCOPE_USER
        self.reg, self.mod, self._orig_user, self._orig_global_dir = _registry_fixture(tmp_path)
        yield
        _registry_teardown(self.mod, self._orig_user, self._orig_global_dir)

    def test_same_port_same_scope_different_id_blocked(self):
        """Two httpListeners on port 9090 in global scope = conflict."""
        from core.service_registry import ResourceConflictError
        self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                         config={"port": "9090"})
        with pytest.raises(ResourceConflictError, match="port"):
            self.reg.install(self.SCOPE_GLOBAL, "", "http2", "httpListener",
                             config={"port": "9090"})

    def test_same_port_cross_scope_blocked(self):
        """httpListener on port 8080 in global, same port in user = conflict."""
        from core.service_registry import ResourceConflictError
        self.reg.install(self.SCOPE_GLOBAL, "", "http_global", "httpListener",
                         config={"port": "8080"})
        with pytest.raises(ResourceConflictError):
            self.reg.install(self.SCOPE_USER, "alice", "http_user", "httpListener",
                             config={"port": "8080"})

    def test_different_ports_allowed(self):
        """httpListeners on different ports = fine."""
        self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                         config={"port": "8080"})
        sdef = self.reg.install(self.SCOPE_GLOBAL, "", "http2", "httpListener",
                                config={"port": "8081"})
        assert sdef.service_id == "http2"

    def test_reinstall_same_id_allowed(self):
        """Re-installing the same service_id in the same scope = update, not conflict."""
        self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                         config={"port": "9090"})
        sdef = self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                                config={"port": "9090"})
        assert sdef.service_id == "http1"

    def test_bot_token_conflict(self):
        """Same Discord bot_token in two scopes = conflict."""
        from core.service_registry import ResourceConflictError
        self.reg.install(self.SCOPE_GLOBAL, "", "bot1", "discordBot",
                         config={"bot_token": "tok123"})
        with pytest.raises(ResourceConflictError, match="bot_token"):
            self.reg.install(self.SCOPE_USER, "alice", "bot2", "discordBot",
                             config={"bot_token": "tok123"})

    def test_different_bot_tokens_allowed(self):
        """Different bot_tokens = fine."""
        self.reg.install(self.SCOPE_GLOBAL, "", "bot1", "discordBot",
                         config={"bot_token": "tok_a"})
        sdef = self.reg.install(self.SCOPE_USER, "alice", "bot2", "discordBot",
                                config={"bot_token": "tok_b"})
        assert sdef.service_id == "bot2"

    # Relay / toolRelay no longer have port+path uniqueness: each service_id
    # gets its own route on the main HTTP listener.

    def test_tool_relay_connect_marks_started_and_ignores_legacy_port_path(self):
        """toolRelay registers on the main listener and reports connected."""
        from services.tool_relay_service import ToolRelayService

        listener = MagicMock()
        with patch(
            "services.http_listener_service.HTTPListenerService.all_instances",
            return_value={9090: listener},
        ):
            svc = ToolRelayService({
                "_service_id": "_tool_relay",
                "token": "tok",
                "port": 12345,
                "path": "/legacy-dead-path",
            })
            svc.connect()

        assert svc.is_connected()
        listener.register_route.assert_called_once()
        assert listener.register_route.call_args.args[1] == "/ws/tools/_tool_relay"

        svc.disconnect()
        listener.unregister_routes.assert_called_once_with("_tool_relay")
        assert not svc.is_connected()

    def test_tool_relay_ws_session_serializes_response_writes(self, monkeypatch):
        """Concurrent tool results must not write interleaved WS frames."""
        import asyncio
        import threading

        from services.tool_relay_service import ToolRelayService
        import services.filesystem_service as fs_mod

        svc = ToolRelayService({"_service_id": "_tool_relay", "token": "tok"})
        frames = [
            (0x01, json.dumps({
                "type": "register",
                "token": "tok",
                "relay_id": "relay1",
                "user_id": "user1",
                "conversation_id": "conv1",
                "agent_name": "agent1",
            }).encode("utf-8")),
            (0x01, json.dumps({
                "type": "request",
                "request_id": "rid1",
                "method": "read",
            }).encode("utf-8")),
            (0x01, json.dumps({
                "type": "request",
                "request_id": "rid2",
                "method": "read",
            }).encode("utf-8")),
        ]
        barrier = threading.Barrier(2)
        sent = []
        active_sends = 0
        max_active_sends = 0

        class Writer:
            def close(self):
                pass

        def handle_tool_request(msg, user_id, conversation_id, agent_name):
            barrier.wait(timeout=2)
            return {"type": "result", "request_id": msg["request_id"], "data": {}}

        async def run_session():
            done = asyncio.Event()

            async def fake_recv(reader):
                if frames:
                    return frames.pop(0)
                await done.wait()
                return 0x08, b""

            async def fake_send(writer, payload, opcode=0x01):
                nonlocal active_sends, max_active_sends
                active_sends += 1
                max_active_sends = max(max_active_sends, active_sends)
                try:
                    await asyncio.sleep(0.02)
                    msg = json.loads(payload.decode("utf-8"))
                    sent.append(msg)
                    result_ids = {
                        m.get("request_id")
                        for m in sent
                        if m.get("type") == "result"
                    }
                    if result_ids == {"rid1", "rid2"}:
                        done.set()
                finally:
                    active_sends -= 1

            monkeypatch.setattr(fs_mod, "_ws_recv_frame", fake_recv)
            monkeypatch.setattr(fs_mod, "_ws_send_frame", fake_send)
            monkeypatch.setattr(svc, "handle_tool_request", handle_tool_request)
            await svc._serve_tool_session(
                object(), Writer(), asyncio.get_running_loop(), "test")

        asyncio.run(run_session())

        assert max_active_sends == 1
        assert {
            m.get("request_id")
            for m in sent
            if m.get("type") == "result"
        } == {"rid1", "rid2"}

    def test_update_config_conflict(self):
        """Changing port to conflict with existing = blocked."""
        from core.service_registry import ResourceConflictError
        self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                         config={"port": "8080"})
        self.reg.install(self.SCOPE_GLOBAL, "", "http2", "httpListener",
                         config={"port": "8081"})
        with pytest.raises(ResourceConflictError):
            self.reg.update_config(self.SCOPE_GLOBAL, "", "http2", {"port": "8080"})

    def test_update_config_same_values_ok(self):
        """Updating a service with its own existing values = no conflict."""
        self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                         config={"port": "8080"})
        self.reg.update_config(self.SCOPE_GLOBAL, "", "http1", {"port": "8080"})

    def test_unconstrained_type_allows_duplicates(self):
        """cacheService has no uniqueness constraint — duplicates fine."""
        self.reg.install(self.SCOPE_GLOBAL, "", "cache1", SVC_TYPE)
        self.reg.install(self.SCOPE_USER, "alice", "cache2", SVC_TYPE)
        self.reg.install(self.SCOPE_GLOBAL, "", "cache3", SVC_TYPE)
        assert len(self.reg.get_all(self.SCOPE_GLOBAL, "")) >= 2

    def test_uninstall_frees_resource(self):
        """After uninstall, the resource key is available again."""
        self.reg.install(self.SCOPE_GLOBAL, "", "http1", "httpListener",
                         config={"port": "9090"})
        self.reg.uninstall(self.SCOPE_GLOBAL, "", "http1")
        sdef = self.reg.install(self.SCOPE_GLOBAL, "", "http2", "httpListener",
                                config={"port": "9090"})
        assert sdef.service_id == "http2"

    def test_http_listener_port_is_required(self):
        """httpListener has no hidden default port."""
        from services.http_listener_service import HTTPListenerService

        with pytest.raises(ValueError, match="requires port"):
            HTTPListenerService({})

    def test_file_tracking_conflict(self):
        """Same storage_path for fileTracking = conflict."""
        from core.service_registry import ResourceConflictError
        self.reg.install(self.SCOPE_GLOBAL, "", "ft1", "fileTracking",
                         config={"storage_path": "/data/tracking.json"})
        with pytest.raises(ResourceConflictError):
            self.reg.install(self.SCOPE_USER, "alice", "ft2", "fileTracking",
                             config={"storage_path": "/data/tracking.json"})


# ── i18n ──────────────────────────────────────────────────────────

