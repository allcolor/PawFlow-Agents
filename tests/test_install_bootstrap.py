import hashlib
import json
import time
from pathlib import Path

from core.deployment_registry import DeploymentRegistry
from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
from core import FlowFile, install_bootstrap as ib
import core.paths as _paths
from tasks.system import install_bootstrap as ib_task


def _write_installer_template(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": "pawflow-installer",
        "name": "pawflow_installer",
        "version": "1.0.0",
        "tasks": {"gen": {"type": "generateFlowFile", "parameters": {}}},
        "relations": [],
    }), encoding="utf-8")


def _write_main_template(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": "pawflow-agent",
        "name": "pawflow_agent",
        "version": "1.0.0",
        "parameters": {"private_gateway_service_id": ""},
        "tasks": {"gen": {"type": "generateFlowFile", "parameters": {}}},
        "relations": [],
    }), encoding="utf-8")


def _write_gateway_skin(repository_dir, name="matrix"):
    skin_dir = repository_dir / "private_gateway_skin" / "global" / name
    skin_dir.mkdir(parents=True, exist_ok=True)
    skin_dir.joinpath("skin.json").write_text(json.dumps({
        "name": name,
        "title": name.title(),
    }), encoding="utf-8")
    skin_dir.joinpath("template.html").write_text(
        "<html>{{ next_url }} {{ error }} {{ cooldown }}</html>",
        encoding="utf-8",
    )


def _stub_cert_generation(tmp_path, monkeypatch):
    cert = tmp_path / "ssl" / "bootstrap.crt"
    key = tmp_path / "ssl" / "bootstrap.key"
    final_cert = tmp_path / "ssl" / "server.crt"
    final_key = tmp_path / "ssl" / "server.key"

    def fake_run(cmd, check, capture_output, text, timeout):
        out = Path(cmd[cmd.index("-out") + 1])
        keyout = Path(cmd[cmd.index("-keyout") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        keyout.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("CERT", encoding="utf-8")
        keyout.write_text("KEY", encoding="utf-8")

    monkeypatch.setattr(ib, "BOOTSTRAP_CERT_FILE", cert)
    monkeypatch.setattr(ib, "BOOTSTRAP_KEY_FILE", key)
    monkeypatch.setattr(ib, "FINAL_CERT_FILE", final_cert)
    monkeypatch.setattr(ib, "FINAL_KEY_FILE", final_key)
    monkeypatch.setattr(ib.subprocess, "run", fake_run)
    return cert, key


def test_fresh_install_deploys_installer_flow(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    state_file = tmp_path / "install_state.json"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    template = tmp_path / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    _write_installer_template(template)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "INSTALLER_TEMPLATE", template)
    cert, key = _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")

    try:
        assert ib.ensure_install_bootstrap(port=9443) is True
        reg = DeploymentRegistry.get_instance()
        inst = reg.get(ib.INSTALLER_INSTANCE_ID)
        assert inst is not None
        assert inst.status == "running"
        assert inst.source == "bootstrap"
        assert inst.parameters["port"] == 9443
        assert "bootstrap_gateway_key" not in inst.parameters
        assert inst.parameters["bootstrap_gateway_secret_ref"] == ib.BOOTSTRAP_GATEWAY_SECRET_REF
        assert inst.parameters["private_gateway_service_id"] == ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID
        assert inst.parameters["ssl_certfile"] == str(cert)
        assert inst.parameters["ssl_keyfile"] == str(key)
        assert inst.parameters["ssl_mode"] == "self_signed"

        sdef = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID)
        assert sdef is not None
        assert sdef.service_type == "privateGateway"
        assert sdef.config["secret_refs"] == ib.BOOTSTRAP_GATEWAY_SECRET_REF
        secret_file = _paths.GLOBAL_SECRETS_FILE.read_text(encoding="utf-8")
        assert "RoyBetty" not in secret_file

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["install_complete"] is False
        assert state["installer_instance_id"] == ib.INSTALLER_INSTANCE_ID
        assert state["checks"]["bootstrap_self_signed_cert"] is True
        assert state["checks"]["bootstrap_private_gateway"] is True
        assert state["draft"]["server"]["ssl_mode"] == "self_signed"
        assert state["draft"]["gateway"]["service_id"] == ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID
    finally:
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_existing_deployments_skip_bootstrap_without_state(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    dep_dir = tmp_path / "deployments"
    template = tmp_path / "template.json"
    template.write_text(json.dumps({
        "id": "existing-flow",
        "name": "existing_flow",
        "version": "1.0.0",
        "tasks": {},
        "relations": [],
    }), encoding="utf-8")

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", tmp_path / "install_state.json")
    monkeypatch.setattr(ib, "INSTALLER_TEMPLATE", tmp_path / "missing.json")
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)

    try:
        from tasks import _register_all_services
        _register_all_services()
        reg = DeploymentRegistry.get_instance()
        reg.deploy(str(template), instance_id="existing-flow", source="test")
        assert ib.ensure_install_bootstrap(port=9090) is False
        assert reg.get(ib.INSTALLER_INSTANCE_ID) is None
    finally:
        DeploymentRegistry.reset()


def test_bootstrap_reset_removes_state_and_redeploys_installer(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    state_file = tmp_path / "install_state.json"
    template = tmp_path / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    _write_installer_template(template)
    state_file.write_text(json.dumps({"install_complete": False, "current_step": "llm_services"}), encoding="utf-8")

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "INSTALLER_TEMPLATE", template)
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_RESET", "1")

    try:
        assert ib.ensure_install_bootstrap(port=9090) is True
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["install_complete"] is False
        assert state["current_step"] == "server"
        assert state["installer_instance_id"] == ib.INSTALLER_INSTANCE_ID
    finally:
        monkeypatch.delenv("PAWFLOW_BOOTSTRAP_RESET", raising=False)
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_install_status_only_exposes_public_draft_sections(tmp_path, monkeypatch):
    state_file = tmp_path / "install_state.json"
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    state_file.write_text(json.dumps({
        "draft": {
            "server": {"ssl_mode": "self_signed"},
            "gateway": {"key_sha256": "abc"},
            "auth": {"service_id": "_auth_gateway"},
            "llm_services": {"primary": "codex_appserver_llm_service"},
            "secrets": {"api_key": "secret"},
        },
    }), encoding="utf-8")

    status = ib.get_install_status()

    assert status["draft"] == {
        "server": {"ssl_mode": "self_signed"},
        "gateway": {"key_sha256": "abc"},
        "auth": {"service_id": "_auth_gateway"},
        "llm_services": {"primary": "codex_appserver_llm_service"},
    }


def test_bootstrap_secret_write_preserves_other_raw_secrets(tmp_path, monkeypatch):
    system_dir = tmp_path / "system"
    secrets_file = system_dir / "global_secrets.json"
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", secrets_file)
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    secrets_file.parent.mkdir(parents=True, exist_ok=True)
    secrets_file.write_text(
        json.dumps({"other.secret": "enc:v2:not-valid"}),
        encoding="utf-8",
    )

    ib._store_bootstrap_gateway_secret("RoyBetty")

    raw = json.loads(secrets_file.read_text(encoding="utf-8"))
    assert raw["other.secret"] == "enc:v2:not-valid"
    assert raw[ib.BOOTSTRAP_GATEWAY_SECRET_REF].startswith("enc:v2:")
    assert "RoyBetty" not in secrets_file.read_text(encoding="utf-8")


def test_finalize_install_requires_replaced_gateway_key(tmp_path, monkeypatch):
    state_file = tmp_path / "install_state.json"
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")
    state_file.write_text(json.dumps({
        "install_complete": False,
        "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
        "checks": {},
        "draft": {},
    }), encoding="utf-8")

    try:
        ib.finalize_install({
            "bootstrap_gateway_key": "wrong",
            "new_gateway_key": "new-gateway-key-123",
        })
        assert False, "invalid bootstrap key should fail"
    except PermissionError:
        pass

    try:
        ib.finalize_install({
            "bootstrap_gateway_key": "RoyBetty",
            "new_gateway_key": "RoyBetty",
        })
        assert False, "unchanged bootstrap key should fail"
    except ValueError:
        pass


def test_finalize_install_validates_main_template_before_persistent_writes(tmp_path, monkeypatch):
    ServiceRegistry.reset()
    state_file = tmp_path / "install_state.json"
    system_dir = tmp_path / "system"
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "MAIN_TEMPLATE", tmp_path / "missing-main.json")
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")
    state_file.write_text(json.dumps({
        "install_complete": False,
        "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
        "checks": {},
        "draft": {},
    }), encoding="utf-8")

    try:
        try:
            ib.finalize_install({
                "bootstrap_gateway_key": "RoyBetty",
                "new_gateway_key": "new-gateway-key-123",
                "admin_password": "admin-password-123",
                "llm_service_id": "review_llm",
                "llm_provider": "codex-app-server",
                "llm_model": "gpt-5.5",
            })
            assert False, "missing main flow template should fail before persistence"
        except ValueError as exc:
            assert "main PawFlow flow template is missing" in str(exc)

        assert not _paths.GLOBAL_SECRETS_FILE.exists()
        assert ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID) is None
    finally:
        ServiceRegistry.reset()


def test_finalize_install_requires_valid_cli_oauth_before_persistent_writes(tmp_path, monkeypatch):
    ServiceRegistry.reset()
    state_file = tmp_path / "install_state.json"
    system_dir = tmp_path / "system"
    main_template = tmp_path / "main.json"
    _write_main_template(main_template)
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "MAIN_TEMPLATE", main_template)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")
    state_file.write_text(json.dumps({
        "install_complete": False,
        "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
        "checks": {},
        "draft": {},
    }), encoding="utf-8")

    try:
        try:
            ib.finalize_install({
                "bootstrap_gateway_key": "RoyBetty",
                "new_gateway_key": "new-gateway-key-123",
                "admin_password": "admin-password-123",
                "llm_service_id": "review_llm",
                "llm_provider": "codex-app-server",
                "llm_model": "gpt-5.5",
            })
            assert False, "missing OAuth credentials should fail before persistence"
        except ValueError as exc:
            assert "has no valid OAuth credential" in str(exc)

        assert not _paths.GLOBAL_SECRETS_FILE.exists()
        assert ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID) is None
    finally:
        ServiceRegistry.reset()


def test_finalize_install_persists_complete_state_without_cleartext_key(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    repository_dir = tmp_path / "repository"
    state_file = tmp_path / "install_state.json"
    template = tmp_path / "installer.json"
    main_template = tmp_path / "main.json"
    _write_installer_template(template)
    _write_main_template(main_template)
    _write_gateway_skin(repository_dir)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "CONVERSATIONS_DIR", runtime_dir / "conversations")
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "REPOSITORY_DIR", repository_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(_paths, "USERS_FILE", system_dir / "users.json")
    monkeypatch.setattr(_paths, "SESSIONS_FILE", system_dir / "sessions.json")
    monkeypatch.setattr(_paths, "SECURITY_FILE", system_dir / "security.json")
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "MAIN_TEMPLATE", main_template)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")

    try:
        from core.conversation_store import ConversationStore
        from core.executor_registry import ExecutorRegistry
        from core.resource_store import ResourceStore
        from core.security import SecurityManager
        ConversationStore.reset()
        ExecutorRegistry._instance = None
        ResourceStore.reset()
        SecurityManager._instance = None

        restored = {}

        def fake_restore(self, instance_id, flow_path, *args, **kwargs):
            restored["instance_id"] = instance_id
            restored["flow_path"] = flow_path
            restored["parameters"] = kwargs.get("parameters")
            restored["service_configs"] = kwargs.get("service_configs")
            self._executors[instance_id] = object()
            return True

        monkeypatch.setattr(ExecutorRegistry, "_restore_instance", fake_restore)

        reg = DeploymentRegistry.get_instance()
        reg.deploy(str(template), instance_id=ib.INSTALLER_INSTANCE_ID, source="bootstrap")
        reg.update_status(ib.INSTALLER_INSTANCE_ID, "running")
        from tasks import _register_all_services
        _register_all_services()
        ServiceRegistry.get_instance().install(
            SCOPE_GLOBAL,
            "",
            ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID,
            "privateGateway",
            {"enabled": True, "secret_refs": ib.BOOTSTRAP_GATEWAY_SECRET_REF},
            enabled=True,
        )
        ib._install_llm_credential_pool_for_scope("codex-app-server", "global", "admin")
        from core.llm_providers.codex_session import add_credential_to_pool
        add_credential_to_pool(
            "access-token",
            "refresh-token",
            int((time.time() + 3600) * 1000),
            account="admin@example.com",
            service_id="codex_oauth_credentials",
            id_token="id-token",
        )
        state_file.write_text(json.dumps({
            "install_complete": False,
            "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
            "completed_steps": ["server"],
            "checks": {},
            "draft": {},
        }), encoding="utf-8")

        new_key = "new-gateway-key-123"
        status = ib.finalize_install({
            "bootstrap_gateway_key": "RoyBetty",
            "new_gateway_key": new_key,
            "admin_password": "admin-password-123",
            "llm_service_id": "review_llm",
            "llm_provider": "codex-app-server",
            "llm_model": "gpt-5.5",
            "credential_service_id": "codex_oauth_credentials",
        })

        assert status["install_complete"] is True
        assert status["current_step"] == "complete"
        assert "finalize" in status["completed_steps"]
        stored = state_file.read_text(encoding="utf-8")
        assert new_key not in stored
        state = json.loads(stored)
        assert state["checks"]["gateway_replaced"] is True
        assert state["checks"]["admin_user"] is True
        assert state["checks"]["llm_service"] is True
        assert state["checks"]["llm_credential_pool"] is True
        assert state["checks"]["summarizer_service"] is True
        assert state["checks"]["summarizer_llm_resolution"] is True
        assert state["checks"]["main_flow_deployed"] is True
        assert state["checks"]["main_flow_executor"] is True
        assert state["checks"]["first_conversation"] is True
        assert state["checks"]["smoke_tests"] is True
        assert state["draft"]["gateway"]["key_sha256"] == hashlib.sha256(
            new_key.encode("utf-8")
        ).hexdigest()
        assert state["draft"]["gateway"]["service_id"] == ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID
        assert state["draft"]["gateway"]["skin"] == "matrix"
        assert state["draft"]["server"]["ssl_mode"] == "self_signed"
        assert state["draft"]["llm_services"]["primary"] == "review_llm"
        assert state["draft"]["llm_services"]["credential_service_id"] == "codex_oauth_credentials"
        assert state["draft"]["summarizer_service"]["service_id"] == ib.SUMMARIZER_SERVICE_ID
        assert state["draft"]["flows"]["main_instance_id"] == ib.MAIN_INSTANCE_ID
        assert state["draft"]["conversation"]["agent"] == ib.FIRST_RUN_AGENT
        assert state["draft"]["smoke_tests"]["llm_credential_pool"]["valid_count"] == 1
        assert state["draft"]["smoke_tests"]["main_flow_executor"]["ok"] is True
        assert restored["instance_id"] == ib.MAIN_INSTANCE_ID
        assert restored["parameters"] == {"private_gateway_service_id": ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID}
        assert restored["service_configs"]["http_listener"]["private_gateway_service_id"] == ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID
        assert restored["service_configs"]["http_listener"]["ssl_certfile"].endswith("server.crt")
        assert restored["service_configs"]["auth"]["providers"] == {"builtin": {"enabled": True}}
        assert reg.get(ib.MAIN_INSTANCE_ID).status == "running"
        assert reg.get(ib.INSTALLER_INSTANCE_ID).status == "stopped"
        sdef = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID)
        assert sdef is not None
        assert sdef.enabled is False
        final_gateway = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID)
        assert final_gateway is not None
        assert final_gateway.config["secret_refs"] == ib.FINAL_GATEWAY_SECRET_REF
        assert final_gateway.config["skin"] == "matrix"
        auth = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.AUTH_GATEWAY_SERVICE_ID)
        assert auth is not None
        assert auth.config["providers"] == {"builtin": {"enabled": True}}
        credential_pool = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", "codex_oauth_credentials")
        assert credential_pool is not None
        assert credential_pool.service_type == "llmCredentialOAuthProvider"
        assert credential_pool.config["provider"] == "codex-app-server"
        llm = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", "review_llm")
        assert llm is not None
        assert llm.config["provider"] == "codex-app-server"
        assert llm.config["credential_service_id"] == "codex_oauth_credentials"
        summarizer = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.SUMMARIZER_SERVICE_ID)
        assert summarizer is not None
        assert summarizer.config["llm_service"] == "review_llm"
        secrets_file = _paths.GLOBAL_SECRETS_FILE.read_text(encoding="utf-8")
        assert new_key not in secrets_file
        assert "admin-password-123" not in system_dir.joinpath("users.json").read_text(encoding="utf-8")
    finally:
        from core.conversation_store import ConversationStore
        from core.executor_registry import ExecutorRegistry
        from core.resource_store import ResourceStore
        from core.security import SecurityManager
        ConversationStore.reset()
        ExecutorRegistry._instance = None
        ResourceStore.reset()
        SecurityManager._instance = None
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_finalize_install_with_api_key_does_not_create_llm_credential_pool(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    repository_dir = tmp_path / "repository"
    state_file = tmp_path / "install_state.json"
    template = tmp_path / "installer.json"
    main_template = tmp_path / "main.json"
    _write_installer_template(template)
    _write_main_template(main_template)
    _write_gateway_skin(repository_dir)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "CONVERSATIONS_DIR", runtime_dir / "conversations")
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "REPOSITORY_DIR", repository_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(_paths, "USERS_FILE", system_dir / "users.json")
    monkeypatch.setattr(_paths, "SESSIONS_FILE", system_dir / "sessions.json")
    monkeypatch.setattr(_paths, "SECURITY_FILE", system_dir / "security.json")
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "MAIN_TEMPLATE", main_template)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")

    try:
        from core.conversation_store import ConversationStore
        from core.executor_registry import ExecutorRegistry
        from core.resource_store import ResourceStore
        from core.security import SecurityManager
        ConversationStore.reset()
        ExecutorRegistry._instance = None
        ResourceStore.reset()
        SecurityManager._instance = None

        def fake_restore(self, instance_id, flow_path, *args, **kwargs):
            self._executors[instance_id] = object()
            return True

        monkeypatch.setattr(ExecutorRegistry, "_restore_instance", fake_restore)

        reg = DeploymentRegistry.get_instance()
        reg.deploy(str(template), instance_id=ib.INSTALLER_INSTANCE_ID, source="bootstrap")
        reg.update_status(ib.INSTALLER_INSTANCE_ID, "running")
        state_file.write_text(json.dumps({
            "install_complete": False,
            "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
            "completed_steps": ["server"],
            "checks": {},
            "draft": {},
        }), encoding="utf-8")

        status = ib.finalize_install({
            "bootstrap_gateway_key": "RoyBetty",
            "new_gateway_key": "new-gateway-key-123",
            "admin_password": "admin-password-123",
            "llm_service_id": "review_llm",
            "llm_provider": "codex-app-server",
            "llm_model": "gpt-5.5",
            "llm_api_key": "api-key-123",
        })

        assert status["install_complete"] is True
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["checks"]["llm_credential_pool"] is False
        assert state["draft"]["llm_services"]["credential_service_id"] == ""
        assert ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", "codex_oauth_credentials") is None
        llm = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", "review_llm")
        assert "credential_service_id" not in llm.config
    finally:
        from core.conversation_store import ConversationStore
        from core.executor_registry import ExecutorRegistry
        from core.resource_store import ResourceStore
        from core.security import SecurityManager
        ConversationStore.reset()
        ExecutorRegistry._instance = None
        ResourceStore.reset()
        SecurityManager._instance = None
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_install_llm_pool_and_summarizer_support_user_scope(tmp_path, monkeypatch):
    ServiceRegistry.reset()
    system_dir = tmp_path / "system"
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")

    try:
        from tasks import _register_all_services
        _register_all_services()

        llm_service_id, summarizer_service_id, credential_service_id = ib._install_llm_and_summarizer({
            "admin_username": "alice",
            "llm_service_id": "alice_llm",
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-pro",
            "llm_service_scope": "user",
            "credential_pool_scope": "user",
            "summarizer_service_scope": "user",
        })

        reg = ServiceRegistry.get_instance()
        assert llm_service_id == "alice_llm"
        assert summarizer_service_id == ib.SUMMARIZER_SERVICE_ID
        assert credential_service_id == "gemini_oauth_credentials"
        assert reg.get_definition(SCOPE_GLOBAL, "", "alice_llm") is None
        assert reg.get_definition(SCOPE_GLOBAL, "", ib.SUMMARIZER_SERVICE_ID) is None
        llm = reg.get_definition("user", "alice", "alice_llm")
        pool = reg.get_definition("user", "alice", "gemini_oauth_credentials")
        summarizer = reg.get_definition("user", "alice", ib.SUMMARIZER_SERVICE_ID)
        assert llm is not None
        assert llm.config["credential_service_id"] == "gemini_oauth_credentials"
        assert pool is not None
        assert pool.config["provider"] == "gemini"
        assert summarizer is not None
        assert summarizer.config["llm_service"] == "alice_llm"
    finally:
        ServiceRegistry.reset()


def test_final_tls_config_accepts_provided_cert_files(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"

    params = ib._final_tls_config({
        "ssl_mode": "provided",
        "ssl_certfile": str(cert),
        "ssl_keyfile": str(key),
    })

    assert params == {
        "ssl_mode": "provided",
        "ssl_certfile": str(cert),
        "ssl_keyfile": str(key),
    }


def test_auth_gateway_config_supports_multiple_providers_and_admin_links(tmp_path, monkeypatch):
    system_dir = tmp_path / "system"
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")

    config = ib._build_auth_gateway_config({
        "auth_providers": "google,github",
        "oauth_google_client_id": "google-id",
        "oauth_google_client_secret": "google-secret",
        "admin_google_email": "admin@example.com",
        "oauth_github_client_id": "github-id",
        "oauth_github_client_secret": "github-secret",
        "admin_github_id": "12345",
    }, "admin")

    assert set(config["providers"]) == {"builtin", "google", "github"}
    assert config["providers"]["google"]["client_id"] == "google-id"
    assert config["providers"]["google"]["client_secret"] == "${auth.google.client_secret}"
    assert config["providers"]["github"]["client_secret"] == "${auth.github.client_secret}"
    assert config["admin_links"]["google"] == {
        "username": "admin",
        "claim": "email",
        "value": "admin@example.com",
    }
    assert config["admin_links"]["github"] == {
        "username": "admin",
        "claim": "user_id",
        "value": "12345",
    }
    secret_file = _paths.GLOBAL_SECRETS_FILE.read_text(encoding="utf-8")
    assert "google-secret" not in secret_file
    assert "github-secret" not in secret_file


def test_auth_gateway_config_links_custom_generic_provider_from_generic_admin_field(tmp_path, monkeypatch):
    system_dir = tmp_path / "system"
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")

    config = ib._build_auth_gateway_config({
        "oauth_generic_name": "keycloak",
        "oauth_generic_client_id": "kc-id",
        "oauth_generic_client_secret": "kc-secret",
        "oauth_generic_authorize_url": "https://idp.example/auth",
        "oauth_generic_token_url": "https://idp.example/token",
        "oauth_generic_userinfo_url": "https://idp.example/userinfo",
        "admin_generic_email": "admin@example.com",
    }, "admin")

    assert "keycloak" in config["providers"]
    assert config["admin_links"]["keycloak"] == {
        "username": "admin",
        "claim": "email",
        "value": "admin@example.com",
    }


def test_stop_installer_executor_soon_unregisters_running_executor(monkeypatch):
    calls = []

    class _Executor:
        def stop(self):
            calls.append("stop")

    class _Registry:
        @staticmethod
        def get_instance():
            return _Registry()

        def get(self, instance_id):
            assert instance_id == ib.INSTALLER_INSTANCE_ID
            return _Executor()

        def unregister(self, instance_id):
            assert instance_id == ib.INSTALLER_INSTANCE_ID
            calls.append("unregister")

    class _Timer:
        def __init__(self, delay, fn):
            self.delay = delay
            self.fn = fn
            self.daemon = False

        def start(self):
            calls.append(("delay", self.delay, self.daemon))
            self.fn()

    monkeypatch.setattr(ib.threading, "Timer", _Timer)
    monkeypatch.setattr("core.executor_registry.ExecutorRegistry", _Registry)

    ib._stop_installer_executor_soon(delay=0.25)

    assert calls == [("delay", 0.25, True), "stop", "unregister"]


def test_install_bootstrap_task_rejects_mutating_endpoints_after_completion(monkeypatch):
    monkeypatch.setattr(ib_task, "is_install_complete", lambda: True)

    ff = FlowFile(content=json.dumps({"bootstrap_gateway_key": "RoyBetty"}).encode("utf-8"))
    ff.set_attribute("http.method", "POST")
    ff.set_attribute("http.path", "/install/api/llm-credential/prepare")

    out = ib_task.InstallBootstrapTask({}).execute(ff)[0]
    payload = json.loads(out.get_content().decode("utf-8"))

    assert out.get_attribute("http.response.status") == "410"
    assert payload == {"error": "installer is already finalized"}


def test_bootstrap_cert_generation_reuses_existing_files(tmp_path, monkeypatch):
    cert = tmp_path / "bootstrap.crt"
    key = tmp_path / "bootstrap.key"
    cert.write_text("CERT", encoding="utf-8")
    key.write_text("KEY", encoding="utf-8")

    monkeypatch.setattr(ib, "BOOTSTRAP_CERT_FILE", cert)
    monkeypatch.setattr(ib, "BOOTSTRAP_KEY_FILE", key)

    called = {"run": False}

    def fake_run(*args, **kwargs):
        called["run"] = True

    monkeypatch.setattr(ib.subprocess, "run", fake_run)

    params = ib.ensure_bootstrap_self_signed_cert()

    assert called["run"] is False
    assert params["ssl_certfile"] == str(cert)
    assert params["ssl_keyfile"] == str(key)
    assert params["ssl_mode"] == "self_signed"
