import hashlib
import json

from core.deployment_registry import DeploymentRegistry
from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
from core import install_bootstrap as ib
import core.paths as _paths


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


def _stub_cert_generation(tmp_path, monkeypatch):
    cert = tmp_path / "ssl" / "bootstrap.crt"
    key = tmp_path / "ssl" / "bootstrap.key"

    def fake_run(cmd, check, capture_output, text, timeout):
        cert.parent.mkdir(parents=True, exist_ok=True)
        cert.write_text("CERT", encoding="utf-8")
        key.write_text("KEY", encoding="utf-8")

    monkeypatch.setattr(ib, "BOOTSTRAP_CERT_FILE", cert)
    monkeypatch.setattr(ib, "BOOTSTRAP_KEY_FILE", key)
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
        from core.resource_store import ResourceStore
        from core.security import SecurityManager
        ConversationStore.reset()
        ResourceStore.reset()
        SecurityManager._instance = None

        reg = DeploymentRegistry.get_instance()
        reg.deploy(str(template), instance_id=ib.INSTALLER_INSTANCE_ID, source="bootstrap")
        reg.update_status(ib.INSTALLER_INSTANCE_ID, "running")
        ServiceRegistry.get_instance().install(
            SCOPE_GLOBAL,
            "",
            ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID,
            "privateGateway",
            {"enabled": True, "secret_refs": ib.BOOTSTRAP_GATEWAY_SECRET_REF},
            enabled=True,
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
        assert state["checks"]["summarizer_service"] is True
        assert state["checks"]["main_flow_deployed"] is True
        assert state["checks"]["first_conversation"] is True
        assert state["draft"]["gateway"]["key_sha256"] == hashlib.sha256(
            new_key.encode("utf-8")
        ).hexdigest()
        assert state["draft"]["gateway"]["service_id"] == ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID
        assert state["draft"]["llm_services"]["primary"] == "review_llm"
        assert state["draft"]["summarizer_service"]["service_id"] == ib.SUMMARIZER_SERVICE_ID
        assert state["draft"]["flows"]["main_instance_id"] == ib.MAIN_INSTANCE_ID
        assert state["draft"]["conversation"]["agent"] == ib.FIRST_RUN_AGENT
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
        llm = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", "review_llm")
        assert llm is not None
        assert llm.config["provider"] == "codex-app-server"
        summarizer = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.SUMMARIZER_SERVICE_ID)
        assert summarizer is not None
        assert summarizer.config["llm_service"] == "review_llm"
        secrets_file = _paths.GLOBAL_SECRETS_FILE.read_text(encoding="utf-8")
        assert new_key not in secrets_file
        assert "admin-password-123" not in system_dir.joinpath("users.json").read_text(encoding="utf-8")
    finally:
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore
        from core.security import SecurityManager
        ConversationStore.reset()
        ResourceStore.reset()
        SecurityManager._instance = None
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


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
