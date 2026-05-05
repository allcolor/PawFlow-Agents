import hashlib
import json

from core.deployment_registry import DeploymentRegistry
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
    dep_dir = tmp_path / "deployments"
    state_file = tmp_path / "install_state.json"
    template = tmp_path / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    _write_installer_template(template)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
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
        assert inst.parameters["bootstrap_gateway_key"] == "RoyBetty"
        assert inst.parameters["ssl_certfile"] == str(cert)
        assert inst.parameters["ssl_keyfile"] == str(key)
        assert inst.parameters["ssl_mode"] == "self_signed"

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["install_complete"] is False
        assert state["installer_instance_id"] == ib.INSTALLER_INSTANCE_ID
        assert state["checks"]["bootstrap_self_signed_cert"] is True
        assert state["draft"]["server"]["ssl_mode"] == "self_signed"
    finally:
        DeploymentRegistry.reset()


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
            "secrets": {"api_key": "secret"},
        },
    }), encoding="utf-8")

    status = ib.get_install_status()

    assert status["draft"] == {
        "server": {"ssl_mode": "self_signed"},
        "gateway": {"key_sha256": "abc"},
    }


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


def test_finalize_install_persists_complete_state_without_cleartext_key(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    dep_dir = tmp_path / "deployments"
    state_file = tmp_path / "install_state.json"
    template = tmp_path / "installer.json"
    _write_installer_template(template)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setenv("PAWFLOW_BOOTSTRAP_GATEWAY_KEY", "RoyBetty")

    try:
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

        new_key = "new-gateway-key-123"
        status = ib.finalize_install({
            "bootstrap_gateway_key": "RoyBetty",
            "new_gateway_key": new_key,
        })

        assert status["install_complete"] is True
        assert status["current_step"] == "complete"
        assert "finalize" in status["completed_steps"]
        stored = state_file.read_text(encoding="utf-8")
        assert new_key not in stored
        state = json.loads(stored)
        assert state["checks"]["gateway_replaced"] is True
        assert state["draft"]["gateway"]["key_sha256"] == hashlib.sha256(
            new_key.encode("utf-8")
        ).hexdigest()
        assert reg.get(ib.INSTALLER_INSTANCE_ID).status == "stopped"
    finally:
        DeploymentRegistry.reset()


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
