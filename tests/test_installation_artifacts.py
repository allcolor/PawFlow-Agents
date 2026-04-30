from pathlib import Path
import json


def test_server_dockerfile_supports_bootstrap_docker_builds():
    src = Path("Dockerfile").read_text(encoding="utf-8")

    assert "docker.io" in src
    assert "/app/data /app/certs" in src
    assert "USER pawflow" in src


def test_install_scripts_mount_persistent_dirs_and_docker_socket():
    install = Path("scripts/install-pawflow.sh")
    build = Path("scripts/build-pawflow-docker.sh")
    run = Path("scripts/run-pawflow-docker.sh")

    for script in (install, build, run):
        assert script.exists()
        assert script.stat().st_mode & 0o111
        assert "set -euo pipefail" in script.read_text(encoding="utf-8")

    run_src = run.read_text(encoding="utf-8")
    assert "ghcr.io/allcolor/pawflow:latest" in run_src
    assert "PAWFLOW_BOOTSTRAP_GATEWAY_KEY" in run_src
    assert "RoyBetty" in run_src
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_src
    assert "--group-add" in run_src
    assert "$PAWFLOW_HOME/data:/app/data" in run_src
    assert "$PAWFLOW_HOME/certs:/app/certs" in run_src

    install_src = install.read_text(encoding="utf-8")
    assert "--source" in install_src
    assert "git clone" in install_src
    assert "docker pull" in install_src


def test_install_docs_and_agent_prompt_capture_bootstrap_contract():
    doc = Path("docs/installation_bootstrap.md").read_text(encoding="utf-8")
    prompt = Path("docs/prompts/install_with_agent.md").read_text(encoding="utf-8")

    assert "PawFlow Installer" in doc
    assert "RoyBetty" in doc
    assert "Never create a default user relay" in doc
    assert "Install relay client" in doc
    assert "/var/run/docker.sock" in doc
    assert "Summarizer service" in doc
    assert "Variables and secrets" in doc
    assert "summarizer_service" in doc
    assert "secret IDs" in doc

    assert "docker info" in prompt
    assert "docker pull ghcr.io/allcolor/pawflow:latest" in prompt
    assert "Do not configure relays" in prompt
    assert "summarizer service" in prompt
    assert "variables, secrets" in prompt
    assert "RoyBetty" in prompt


def test_pawflow_installer_flow_template_exists():
    latest = Path("data/repository/flows/global/default/pawflow_installer/latest.json")
    template = Path("data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json")

    assert latest.exists()
    assert template.exists()

    flow = json.loads(template.read_text(encoding="utf-8"))
    assert flow["id"] == "pawflow-installer"
    assert flow["fqn"] == "default.pawflow_installer:1.0.0"
    assert flow["parameters"]["bootstrap_gateway_key"] == "RoyBetty"

    routes = flow["tasks"]["http_in"]["parameters"]["routes"]
    patterns = {route["pattern"] for route in routes}
    assert "/install" in patterns
    assert "/install/api" in patterns

    api_content = flow["tasks"]["install_api"]["parameters"]["content"]
    assert "summarizer_service" in api_content
    assert "variables" in api_content
    assert "secrets" in api_content
