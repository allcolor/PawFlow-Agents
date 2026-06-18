import hashlib
import json
import time
from pathlib import Path

import pytest

from core.deployment_registry import DeploymentRegistry
from core.service_registry import ServiceRegistry, SCOPE_GLOBAL
from core import FlowFile, install_bootstrap as ib
from core import _install_base as ib_base
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

    monkeypatch.setattr(ib_base, "BOOTSTRAP_CERT_FILE", cert)
    monkeypatch.setattr(ib_base, "BOOTSTRAP_KEY_FILE", key)
    monkeypatch.setattr(ib_base, "FINAL_CERT_FILE", final_cert)
    monkeypatch.setattr(ib_base, "FINAL_KEY_FILE", final_key)
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", template)
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


def test_bootstrap_reset_redeploys_installer_with_current_port(tmp_path, monkeypatch):
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", template)
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)

    try:
        assert ib.ensure_install_bootstrap(port=9090) is True
        first = DeploymentRegistry.get_instance().get(ib.INSTALLER_INSTANCE_ID)
        assert first is not None
        first_created = first.created_at
        assert first.parameters["port"] == 9090

        monkeypatch.setenv("PAWFLOW_BOOTSTRAP_RESET", "1")
        assert ib.ensure_install_bootstrap(port=19990) is True
        second = DeploymentRegistry.get_instance().get(ib.INSTALLER_INSTANCE_ID)
        assert second is not None
        assert second.parameters["port"] == 19990
        assert second.created_at >= first_created
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", tmp_path / "install_state.json")
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", tmp_path / "missing.json")
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


def test_completed_install_syncs_main_flow_listener_port(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    state_file = tmp_path / "install_state.json"
    main_template = tmp_path / "main.json"
    _write_main_template(main_template)
    state_file.write_text(json.dumps({"install_complete": True}), encoding="utf-8")

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", tmp_path / "missing.json")
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)

    try:
        from tasks import _register_all_services
        _register_all_services()
        reg = DeploymentRegistry.get_instance()
        reg.deploy(
            str(main_template),
            instance_id=ib.MAIN_INSTANCE_ID,
            source="bootstrap",
            service_configs={"http_listener": {"port": 9090}},
        )
        reg.deploy(str(main_template), instance_id=ib.INSTALLER_INSTANCE_ID, source="bootstrap")
        ServiceRegistry.get_instance().install(
            SCOPE_GLOBAL,
            "",
            ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID,
            "privateGateway",
            {"enabled": True, "secret_refs": ib.BOOTSTRAP_GATEWAY_SECRET_REF},
            enabled=True,
        )

        assert ib.ensure_install_bootstrap(port=19990) is False
        inst = reg.get(ib.MAIN_INSTANCE_ID)
        assert inst is not None
        assert inst.service_configs["http_listener"]["port"] == 19990
        assert reg.get(ib.INSTALLER_INSTANCE_ID) is None
        assert ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID) is None
    finally:
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", template)
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


def test_bootstrap_reset_removes_previous_main_deployment(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    state_file = tmp_path / "install_state.json"
    installer_template = tmp_path / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    main_template = tmp_path / "repository" / "flows" / "global" / "default" / "pawflow_agent" / "versions" / "1.0.0.json"
    _write_installer_template(installer_template)
    _write_main_template(main_template)
    state_file.write_text(json.dumps({"install_complete": True, "current_step": "complete"}), encoding="utf-8")

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", installer_template)
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_RESET", "1")

    try:
        reg = DeploymentRegistry.get_instance()
        reg.deploy(str(main_template), instance_id=ib.MAIN_INSTANCE_ID, source="bootstrap")

        assert ib.ensure_install_bootstrap(port=19990) is True
        assert reg.get(ib.MAIN_INSTANCE_ID) is None
        installer = reg.get(ib.INSTALLER_INSTANCE_ID)
        assert installer is not None
        assert installer.parameters["port"] == 19990
    finally:
        monkeypatch.delenv("PAWFLOW_BOOTSTRAP_RESET", raising=False)
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_bootstrap_refreshes_installer_template_from_default_data(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    state_file = tmp_path / "install_state.json"
    persistent_template = tmp_path / "data" / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    default_flow_dir = tmp_path / "default-data" / "repository" / "flows" / "global" / "default" / "pawflow_installer"
    default_template = default_flow_dir / "versions" / "1.0.0.json"
    _write_installer_template(persistent_template)
    default_template.parent.mkdir(parents=True, exist_ok=True)
    default_template.write_text(json.dumps({
        "id": "pawflow-installer",
        "name": "pawflow_installer",
        "version": "1.0.0",
        "tasks": {
            "redirect_to_install": {
                "type": "handleHTTPResponse",
                "parameters": {"status_code": 302},
            }
        },
        "relations": [{"from": "http_in", "to": "redirect_to_install", "type": "GET:/"}],
    }), encoding="utf-8")
    (default_template.parent / "assets").mkdir()
    (default_template.parent / "assets" / "install.html").write_text("new", encoding="utf-8")

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", persistent_template)
    monkeypatch.setattr(ib_base, "DEFAULT_INSTALLER_FLOW_DIR", default_flow_dir)
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_RESET", "1")

    try:
        assert ib.ensure_install_bootstrap(port=9090) is True
        refreshed = json.loads(persistent_template.read_text(encoding="utf-8"))
        assert "redirect_to_install" in refreshed["tasks"]
        assert (persistent_template.parent / "assets" / "install.html").read_text(encoding="utf-8") == "new"
    finally:
        monkeypatch.delenv("PAWFLOW_BOOTSTRAP_RESET", raising=False)
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_refreshed_installer_template_redeploys_existing_installer(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    ServiceRegistry.reset()
    dep_dir = tmp_path / "deployments"
    runtime_dir = tmp_path / "runtime"
    system_dir = tmp_path / "system"
    state_file = tmp_path / "install_state.json"
    persistent_template = tmp_path / "data" / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    default_flow_dir = tmp_path / "default-data" / "repository" / "flows" / "global" / "default" / "pawflow_installer"
    default_template = default_flow_dir / "versions" / "1.0.0.json"
    _write_installer_template(persistent_template)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(_paths, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(_paths, "SYSTEM_DIR", system_dir)
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "INSTALLER_TEMPLATE", persistent_template)
    monkeypatch.setattr(ib_base, "DEFAULT_INSTALLER_FLOW_DIR", default_flow_dir)
    _stub_cert_generation(tmp_path, monkeypatch)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_RESET", raising=False)

    try:
        assert ib.ensure_install_bootstrap(port=9090) is True
        first = DeploymentRegistry.get_instance().get(ib.INSTALLER_INSTANCE_ID)
        assert first is not None
        first_created = first.created_at

        default_template.parent.mkdir(parents=True, exist_ok=True)
        default_template.write_text(json.dumps({
            "id": "pawflow-installer",
            "name": "pawflow_installer",
            "version": "1.0.0",
            "tasks": {"fresh": {"type": "generateFlowFile", "parameters": {}}},
            "relations": [],
        }), encoding="utf-8")
        time.sleep(0.01)

        assert ib.ensure_install_bootstrap(port=19990) is True
        second = DeploymentRegistry.get_instance().get(ib.INSTALLER_INSTANCE_ID)
        assert second is not None
        assert second.created_at > first_created
        assert second.parameters["port"] == 19990
        refreshed = json.loads(persistent_template.read_text(encoding="utf-8"))
        assert "fresh" in refreshed["tasks"]
    finally:
        DeploymentRegistry.reset()
        ServiceRegistry.reset()


def test_install_status_only_exposes_public_draft_sections(tmp_path, monkeypatch):
    state_file = tmp_path / "install_state.json"
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")
    state_file.write_text(json.dumps({
        "install_complete": False,
        "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
        "checks": {},
        "draft": {},
    }), encoding="utf-8")

    try:
        ib.finalize_install({
            "new_gateway_key": "RoyBetty",
        })
        assert False, "unchanged bootstrap key should fail"
    except ValueError:
        pass


def test_finalize_install_rejects_mismatched_admin_password_confirmation(tmp_path, monkeypatch):
    state_file = tmp_path / "install_state.json"
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")
    state_file.write_text(json.dumps({
        "install_complete": False,
        "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
        "checks": {},
        "draft": {},
    }), encoding="utf-8")

    try:
        ib.finalize_install({
            "bootstrap_gateway_key": "RoyBetty",
            "new_gateway_key": "new-gateway-key-123",
            "admin_password": "Admin-password-123",
            "admin_password_confirm": "Admin-password-456",
        })
        assert False, "mismatched admin password confirmation should fail"
    except ValueError as exc:
        assert "admin_password_confirm must match" in str(exc)


def test_finalize_install_rejects_weak_admin_password(tmp_path, monkeypatch):
    state_file = tmp_path / "install_state.json"
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")
    state_file.write_text(json.dumps({
        "install_complete": False,
        "installer_instance_id": ib.INSTALLER_INSTANCE_ID,
        "checks": {},
        "draft": {},
    }), encoding="utf-8")

    try:
        ib.finalize_install({
            "bootstrap_gateway_key": "RoyBetty",
            "new_gateway_key": "new-gateway-key-123",
            "admin_password": "admin-password-123",
            "admin_password_confirm": "admin-password-123",
        })
        assert False, "weak admin password should fail"
    except ValueError as exc:
        assert "admin_password must include an uppercase letter" in str(exc)


def test_finalize_install_validates_main_template_before_persistent_writes(tmp_path, monkeypatch):
    ServiceRegistry.reset()
    state_file = tmp_path / "install_state.json"
    system_dir = tmp_path / "system"
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "MAIN_TEMPLATE", tmp_path / "missing-main.json")
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
                "admin_password": "Admin-password-123",
                "admin_password_confirm": "Admin-password-123",
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "MAIN_TEMPLATE", main_template)
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
                "admin_password": "Admin-password-123",
                "admin_password_confirm": "Admin-password-123",
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "MAIN_TEMPLATE", main_template)
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
        reg.deploy(
            str(template),
            instance_id=ib.INSTALLER_INSTANCE_ID,
            source="bootstrap",
            parameters={"port": 19990},
        )
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
            "admin_password": "Admin-password-123",
            "admin_password_confirm": "Admin-password-123",
            "llm_service_id": "review_llm",
            "llm_provider": "codex-app-server",
            "llm_model": "gpt-5.5",
            "credential_service_id": "codex_oauth_credentials",
            "first_conversation": json.dumps({
                "title": "Ops bootstrap",
                "agents": [
                    {
                        "instance_name": "assistant",
                        "definition": "assistant",
                        "llm_service": "review_llm",
                        "params": {"name": "Assistant"},
                    },
                    {
                        "instance_name": "reviewer",
                        "definition": "assistant",
                        "llm_service": "review_llm",
                        "model": "gpt-5.5-review",
                        "tools": ["read", "grep"],
                        "max_depth": 50,
                        "params": {"name": "Reviewer", "role": "review"},
                    },
                ],
            }),
        })

        assert status["install_complete"] is True
        assert status["current_step"] == "complete"
        assert "finalize" in status["completed_steps"]
        stored = state_file.read_text(encoding="utf-8")
        assert new_key not in stored
        state = json.loads(stored)
        assert state["checks"]["gateway_replaced"] is True
        assert state["checks"]["admin_user"] is True
        assert state["checks"]["final_private_gateway_key"] is True
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
        assert state["draft"]["server"]["port"] == 19990
        assert state["draft"]["server"]["ssl_mode"] == "self_signed"
        assert state["draft"]["llm_services"]["primary"] == "review_llm"
        assert state["draft"]["llm_services"]["credential_service_id"] == "codex_oauth_credentials"
        assert state["draft"]["summarizer_service"]["service_id"] == ib.SUMMARIZER_SERVICE_ID
        assert state["draft"]["flows"]["main_instance_id"] == ib.MAIN_INSTANCE_ID
        assert state["draft"]["conversation"]["title"] == "Ops bootstrap"
        assert [a["instance_name"] for a in state["draft"]["conversation"]["agents"]] == ["assistant", "reviewer"]
        conv_id = state["draft"]["conversation"]["conversation_id"]
        conv_store = ConversationStore.instance()
        assert conv_store.get_extra(conv_id, "title") == "Ops bootstrap"
        assert conv_store.get_extra(conv_id, "active_resources") == {
            "agents": ["assistant", "reviewer"],
            "agent": "assistant",
        }
        conv_agents = conv_store.get_extra(conv_id, "conv_agents")
        assert conv_agents["assistant"]["llm_service"] == "review_llm"
        assert conv_agents["reviewer"]["definition"] == "assistant"
        assert conv_agents["reviewer"]["model"] == "gpt-5.5-review"
        assert conv_agents["reviewer"]["tools"] == ["read", "grep"]
        assert conv_agents["reviewer"]["max_depth"] == 50
        assert conv_agents["reviewer"]["params"] == {"name": "Reviewer", "role": "review"}
        assert state["draft"]["smoke_tests"]["llm_credential_pool"]["valid_count"] == 1
        assert state["draft"]["smoke_tests"]["main_flow_executor"]["ok"] is True
        assert state["draft"]["smoke_tests"]["final_private_gateway_key"]["ok"] is True
        assert restored["instance_id"] == ib.MAIN_INSTANCE_ID
        assert restored["parameters"] == {
            "private_gateway_service_id": ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID,
            "port": 19990,
        }
        assert restored["service_configs"]["http_listener"]["port"] == 19990
        assert restored["service_configs"]["http_listener"]["private_gateway_service_id"] == ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID
        assert restored["service_configs"]["http_listener"]["ssl_certfile"].endswith("server.crt")
        assert restored["service_configs"]["auth"]["providers"] == {"builtin": {"enabled": True}}
        from services.private_gateway import verify_secret
        assert verify_secret(new_key, ib.FINAL_GATEWAY_SECRET_REF) is True
        assert verify_secret("RoyBetty", ib.FINAL_GATEWAY_SECRET_REF) is False
        assert reg.get(ib.MAIN_INSTANCE_ID).status == "running"
        assert reg.get(ib.INSTALLER_INSTANCE_ID) is None
        sdef = ServiceRegistry.get_instance().get_definition(
            SCOPE_GLOBAL, "", ib.BOOTSTRAP_PRIVATE_GATEWAY_SERVICE_ID)
        assert sdef is None
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
        assert "Admin-password-123" not in system_dir.joinpath("users.json").read_text(encoding="utf-8")
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "MAIN_TEMPLATE", main_template)
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
            "admin_password": "Admin-password-123",
            "admin_password_confirm": "Admin-password-123",
            "llm_service_id": "review_llm",
            "llm_provider": "codex-app-server",
            "llm_model": "gpt-5.5",
            "llm_api_key": "api-key-123",
            "listener_port": 19990,
        })

        assert status["install_complete"] is True
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["checks"]["llm_credential_pool"] is True
        assert state["draft"]["llm_services"]["credential_service_id"] == ""
        assert state["draft"]["smoke_tests"]["llm_credential_pool"] == {"ok": True, "skipped": True}
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


def test_finalize_install_rolls_back_runtime_artifacts_when_smoke_checks_fail(tmp_path, monkeypatch):
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
    monkeypatch.setattr(ib_base, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib_base, "MAIN_TEMPLATE", main_template)
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

        class _Executor:
            def stop(self):
                pass

        def fake_restore(self, instance_id, flow_path, *args, **kwargs):
            self._executors[instance_id] = _Executor()
            return True

        def fail_smoke_checks(**kwargs):
            raise RuntimeError("forced smoke failure")

        monkeypatch.setattr(ExecutorRegistry, "_restore_instance", fake_restore)
        monkeypatch.setattr(ib, "_run_install_smoke_checks", fail_smoke_checks)

        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()
        sm.create_user("bootstrap_admin", "Old-admin-password-123", Role.ADMIN)
        users_before = _paths.USERS_FILE.read_bytes()
        _paths.GLOBAL_SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _paths.GLOBAL_SECRETS_FILE.write_text(
            json.dumps({"existing.secret": "kept"}), encoding="utf-8")
        secrets_before = _paths.GLOBAL_SECRETS_FILE.read_bytes()

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

        try:
            ib.finalize_install({
                "bootstrap_gateway_key": "RoyBetty",
                "new_gateway_key": "new-gateway-key-123",
                "admin_username": "rollback_admin",
                "admin_password": "Admin-password-123",
                "admin_password_confirm": "Admin-password-123",
                "llm_service_id": "review_llm",
                "llm_provider": "codex-app-server",
                "llm_model": "gpt-5.5",
                "llm_api_key": "api-key-123",
                "listener_port": 19990,
                "oauth_google_client_id": "google-id",
                "oauth_google_client_secret": "google-secret",
            })
            assert False, "smoke check failure should abort finalization"
        except RuntimeError as exc:
            assert "forced smoke failure" in str(exc)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["install_complete"] is False
        assert reg.get(ib.MAIN_INSTANCE_ID) is None
        assert ExecutorRegistry.get_instance().get(ib.MAIN_INSTANCE_ID) is None
        svc = ServiceRegistry.get_instance()
        assert svc.get_definition(SCOPE_GLOBAL, "", ib.FINAL_PRIVATE_GATEWAY_SERVICE_ID) is None
        assert svc.get_definition(SCOPE_GLOBAL, "", ib.AUTH_GATEWAY_SERVICE_ID) is None
        assert svc.get_definition(SCOPE_GLOBAL, "", "review_llm") is None
        assert svc.get_definition(SCOPE_GLOBAL, "", ib.SUMMARIZER_SERVICE_ID) is None
        secrets_raw = json.loads(_paths.GLOBAL_SECRETS_FILE.read_text(encoding="utf-8"))
        assert ib.FINAL_GATEWAY_SECRET_REF not in secrets_raw
        assert _paths.GLOBAL_SECRETS_FILE.read_bytes() == secrets_before
        assert _paths.USERS_FILE.read_bytes() == users_before
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


def test_install_multiple_llm_services_and_linked_summarizer(tmp_path, monkeypatch):
    ServiceRegistry.reset()
    system_dir = tmp_path / "system"
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")

    try:
        from tasks import _register_all_services
        _register_all_services()

        llm_service_id, summarizer_service_id, credential_service_id = ib._install_llm_and_summarizer({
            "admin_username": "alice",
            "llm_services": [
                {
                    "service_id": "openai_main",
                    "scope": "global",
                    "config": {
                        "provider": "openai",
                        "default_model": "gpt-5.1",
                        "api_key": "secret-key",
                        "base_url": "https://api.openai.com/v1",
                    },
                },
                {
                    "service_id": "gemini_cli",
                    "scope": "user",
                    "credential_scope": "user",
                    "config": {
                        "provider": "gemini",
                        "default_model": "gemini-2.5-pro",
                        "credential_service_id": "alice_gemini_creds",
                    },
                },
            ],
            "summarizer_service": {
                "service_id": "sum_alice",
                "scope": "user",
                "config": {"llm_service": "gemini_cli"},
            },
        })

        reg = ServiceRegistry.get_instance()
        assert llm_service_id == "openai_main"
        assert summarizer_service_id == "sum_alice"
        assert credential_service_id == "alice_gemini_creds"
        openai = reg.get_definition(SCOPE_GLOBAL, "", "openai_main")
        gemini = reg.get_definition("user", "alice", "gemini_cli")
        creds = reg.get_definition("user", "alice", "alice_gemini_creds")
        summarizer = reg.get_definition("user", "alice", "sum_alice")
        assert openai is not None
        assert openai.config["api_key"] == "${llm.openai_main.api_key}"
        assert gemini is not None
        assert gemini.config["credential_service_id"] == "alice_gemini_creds"
        assert creds is not None
        assert creds.config["provider"] == "gemini"
        assert summarizer is not None
        assert summarizer.config["llm_service"] == "gemini_cli"
    finally:
        ServiceRegistry.reset()


def test_install_relay_server_generates_token_server_side(monkeypatch):
    ServiceRegistry.reset()
    calls = []

    class FakeServerRelayManager:
        @classmethod
        def get_instance(cls):
            return cls()

        def service_relay_config(self, relay_id, *, scope, scope_id, user_id, kind="workspace"):
            return {
                "server_container_name": f"pawflow-relay-srv-{relay_id}",
                "server_workspace_dir": f"data/runtime/relay/{user_id}",
                "server_scope": scope,
                "server_scope_id": scope_id,
                "server_user_id": user_id,
            }

        def spawn_service_relay(self, relay_id, token, *, scope, scope_id, user_id, kind="workspace", internal_token=""):
            calls.append({
                "relay_id": relay_id,
                "token": token,
                "scope": scope,
                "scope_id": scope_id,
                "user_id": user_id,
                "kind": kind,
            })
            return {"relay_id": relay_id}

    monkeypatch.setattr("core.server_relay_manager.ServerRelayManager", FakeServerRelayManager)
    monkeypatch.setattr("tasks.ai.actions.service_flow._wait_for_service_connected", lambda *args, **kwargs: True)

    class FakeListener:
        _port = 19990
        is_ssl = True

        def register_route(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(
        "services.http_listener_service.HTTPListenerService.all_instances",
        staticmethod(lambda: {19990: FakeListener()}),
    )

    try:
        service_id = ib._install_relay_server({
            "relay_server": {
                "enabled": True,
                "service_id": "workspace_relay",
                "scope": "user",
            }
        }, "alice")

        reg = ServiceRegistry.get_instance()
        sdef = reg.get_definition("user", "alice", "workspace_relay")

        assert service_id == "workspace_relay"
        assert sdef is not None
        assert sdef.service_type == "relay"
        assert sdef.config["server_managed"] is True
        assert sdef.config["token"]
        assert calls == [{
            "relay_id": "workspace_relay",
            "token": sdef.config["token"],
            "scope": "user",
            "scope_id": "alice",
            "user_id": "alice",
            "kind": "workspace",
        }]
    finally:
        ServiceRegistry.reset()


def test_relay_server_spec_ignores_client_token():
    spec = ib._relay_server_spec({
        "relay_server": json.dumps({
            "enabled": True,
            "service_id": "workspace_relay",
            "scope": "global",
            "token": "client-token-should-not-be-used",
        })
    })

    assert spec["service_id"] == "workspace_relay"
    assert spec["scope"] == "global"
    assert "token" not in spec["config"]
    assert spec["config"]["mode"] == "readwrite"


def test_install_voice_services_creates_supertonic_and_voicebox():
    ServiceRegistry.reset()
    try:
        from tasks import _register_all_services

        _register_all_services()
        installed = ib._install_voice_services({
            "voice_services": json.dumps({
                "tts": {
                    "enabled": True,
                    "service_id": "bootstrap_tts",
                    "scope": "global",
                    "config": {"voice": "F1", "lang": "fr"},
                },
                "stt": {
                    "enabled": True,
                    "service_id": "bootstrap_stt",
                    "scope": "user",
                    "config": {"client_id": "installer", "stt_model": "small"},
                },
            })
        }, "alice")

        assert installed == [
            {"kind": "tts", "scope": "global", "service_id": "bootstrap_tts", "service_type": "supertonicTTS"},
            {"kind": "stt", "scope": "user", "service_id": "bootstrap_stt", "service_type": "voicebox"},
        ]
        reg = ServiceRegistry.get_instance()
        tts = reg.get_definition(SCOPE_GLOBAL, "", "bootstrap_tts")
        stt = reg.get_definition("user", "alice", "bootstrap_stt")
        assert tts is not None
        assert tts.service_type == "supertonicTTS"
        assert tts.config["auto_install"] is True
        assert tts.config["voice"] == "F1"
        assert stt is not None
        assert stt.service_type == "voicebox"
        assert stt.config["preload_stt_model"] is True
        assert stt.config["stt_model"] == "small"
    finally:
        ServiceRegistry.reset()


def test_create_first_conversation_links_selected_relay(tmp_path, monkeypatch):
    ServiceRegistry.reset()
    runtime_dir = tmp_path / "runtime"
    repository_dir = tmp_path / "repository"
    monkeypatch.setattr(_paths, "CONVERSATIONS_DIR", runtime_dir / "conversations")
    monkeypatch.setattr(_paths, "REPOSITORY_DIR", repository_dir)

    try:
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore
        from core.relay_bindings import get_default, get_linked
        from tasks import _register_all_services

        ConversationStore.reset()
        ResourceStore.reset()
        _register_all_services()
        ServiceRegistry.get_instance().install(
            SCOPE_GLOBAL,
            "",
            "workspace_relay",
            "relay",
            {"token": "server-generated-token", "mode": "readwrite"},
            enabled=True,
        )

        conv_id = ib._create_first_conversation(
            "alice",
            {
                "first_conversation": json.dumps({
                    "title": "Relay bootstrap",
                    "relay_id": "workspace_relay",
                    "agents": [{
                        "instance_name": "assistant",
                        "definition": "assistant",
                        "llm_service": "llm_main",
                        "params": {"name": "Assistant"},
                    }],
                })
            },
            "llm_main",
            ["llm_main"],
        )

        assert get_default(conv_id) == "workspace_relay"
        assert get_linked(conv_id) == ["workspace_relay"]
    finally:
        from core.conversation_store import ConversationStore
        from core.resource_store import ResourceStore
        ConversationStore.reset()
        ResourceStore.reset()
        ServiceRegistry.reset()


def test_final_tls_config_accepts_provided_cert_files(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    cert.write_text("cert", encoding="utf-8")
    key.write_text("key", encoding="utf-8")

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


def test_final_tls_config_rejects_missing_provided_cert_files(tmp_path):
    cert = tmp_path / "missing.crt"
    key = tmp_path / "missing.key"

    with pytest.raises(ValueError, match="provided TLS certificate files must exist"):
        ib._final_tls_config({
            "ssl_certfile": str(cert),
            "ssl_keyfile": str(key),
        })


def test_final_tls_config_infers_self_signed_when_paths_are_empty(tmp_path, monkeypatch):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    monkeypatch.setattr(ib_base, "FINAL_CERT_FILE", cert)
    monkeypatch.setattr(ib_base, "FINAL_KEY_FILE", key)

    def fake_generate(cert_file, key_file, **_kwargs):
        cert_file.write_text("CERT", encoding="utf-8")
        key_file.write_text("KEY", encoding="utf-8")

    monkeypatch.setattr(ib_base, "_generate_self_signed_cert", fake_generate)

    params = ib._final_tls_config({"ssl_mode": "provided"})

    assert params == {
        "ssl_mode": "self_signed",
        "ssl_certfile": str(cert),
        "ssl_keyfile": str(key),
    }


def test_final_tls_config_requires_cert_and_key_together(tmp_path):
    cert = tmp_path / "server.crt"
    cert.write_text("CERT", encoding="utf-8")

    with pytest.raises(ValueError, match="must be provided together"):
        ib._final_tls_config({"ssl_certfile": str(cert)})


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


def test_auth_gateway_config_rejects_selected_generic_without_required_fields(tmp_path, monkeypatch):
    system_dir = tmp_path / "system"
    monkeypatch.setattr(_paths, "GLOBAL_SECRETS_FILE", system_dir / "global_secrets.json")
    monkeypatch.setattr(_paths, "SECRET_KEY_FILE", system_dir / "secret.key")

    try:
        ib._build_auth_gateway_config({"auth_providers": "generic"}, "admin")
        assert False, "selected generic provider should require full OAuth config"
    except ValueError as exc:
        assert "generic OAuth requires" in str(exc)


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


def test_install_bootstrap_task_returns_json_for_unexpected_finalize_errors(monkeypatch):
    monkeypatch.setattr(ib_task, "is_install_complete", lambda: False)

    def fail_finalize(_payload):
        raise RuntimeError("forced finalize failure")

    monkeypatch.setattr(ib_task, "finalize_install", fail_finalize)
    ff = FlowFile(content=json.dumps({"bootstrap_gateway_key": "RoyBetty"}).encode("utf-8"))
    ff.set_attribute("http.method", "POST")
    ff.set_attribute("http.path", "/install/api/finalize")

    out = ib_task.InstallBootstrapTask({}).execute(ff)[0]
    payload = json.loads(out.get_content().decode("utf-8"))

    assert out.get_attribute("http.response.status") == "500"
    assert payload == {"error": "forced finalize failure"}


def test_bootstrap_cert_generation_reuses_existing_files(tmp_path, monkeypatch):
    cert = tmp_path / "bootstrap.crt"
    key = tmp_path / "bootstrap.key"
    cert.write_text("CERT", encoding="utf-8")
    key.write_text("KEY", encoding="utf-8")

    monkeypatch.setattr(ib_base, "BOOTSTRAP_CERT_FILE", cert)
    monkeypatch.setattr(ib_base, "BOOTSTRAP_KEY_FILE", key)

    called = {"run": False}

    def fake_run(*args, **kwargs):
        called["run"] = True

    monkeypatch.setattr(ib.subprocess, "run", fake_run)

    params = ib.ensure_bootstrap_self_signed_cert()

    assert called["run"] is False
    assert params["ssl_certfile"] == str(cert)
    assert params["ssl_keyfile"] == str(key)
    assert params["ssl_mode"] == "self_signed"
