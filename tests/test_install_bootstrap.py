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


def test_fresh_install_deploys_installer_flow(tmp_path, monkeypatch):
    DeploymentRegistry.reset()
    dep_dir = tmp_path / "deployments"
    state_file = tmp_path / "install_state.json"
    template = tmp_path / "repository" / "flows" / "global" / "default" / "pawflow_installer" / "versions" / "1.0.0.json"
    _write_installer_template(template)

    monkeypatch.setattr(_paths, "DEPLOYMENTS_DIR", dep_dir)
    monkeypatch.setattr(ib, "INSTALL_STATE_FILE", state_file)
    monkeypatch.setattr(ib, "INSTALLER_TEMPLATE", template)
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

        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["install_complete"] is False
        assert state["installer_instance_id"] == ib.INSTALLER_INSTANCE_ID
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
    monkeypatch.delenv("PAWFLOW_BOOTSTRAP_DISABLED", raising=False)

    try:
        reg = DeploymentRegistry.get_instance()
        reg.deploy(str(template), instance_id="existing-flow", source="test")
        assert ib.ensure_install_bootstrap(port=9090) is False
        assert reg.get(ib.INSTALLER_INSTANCE_ID) is None
    finally:
        DeploymentRegistry.reset()
