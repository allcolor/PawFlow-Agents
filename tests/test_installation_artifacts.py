from pathlib import Path
import json


def test_server_dockerfile_supports_bootstrap_docker_builds():
    src = Path("Dockerfile").read_text(encoding="utf-8")

    assert "docker.io" in src
    assert "openssl" in src
    assert "ca-certificates" in src
    assert "curl" in src
    assert "ffmpeg" in src
    assert "PLAYWRIGHT_BROWSERS_PATH" in src
    assert "python -m playwright install --with-deps chromium" in src
    assert "/app/data /app/certs" in src
    assert "USER pawflow" in src


def test_install_scripts_mount_persistent_dirs_and_docker_socket():
    install = Path("scripts/install-pawflow.sh")
    build = Path("scripts/build-pawflow-docker.sh")
    run = Path("scripts/run-pawflow-docker.sh")
    doctor = Path("scripts/doctor-pawflow.sh")
    doctor_ps1 = Path("scripts/doctor-pawflow.ps1")

    for script in (install, build, run, doctor):
        assert script.exists()
        assert script.stat().st_mode & 0o111
        assert "set -euo pipefail" in script.read_text(encoding="utf-8")

    assert doctor_ps1.exists()

    run_src = run.read_text(encoding="utf-8")
    assert "${PAWFLOW_IMAGE}" in run_src
    assert "${PAWFLOW_HOME}" in run_src
    assert "PAWFLOW_BOOTSTRAP_GATEWAY_KEY" in run_src
    assert "RoyBetty" in run_src
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_src
    assert "--group-add" in run_src
    assert "$PAWFLOW_HOME/data:/app/data" in run_src
    assert "$PAWFLOW_HOME/certs:/app/certs" in run_src

    install_src = install.read_text(encoding="utf-8")
    assert "${PAWFLOW_IMAGE}" in install_src
    assert "doctor-pawflow.sh" in install_src
    assert "--skip-doctor" in install_src
    assert "--source" in install_src
    assert "git clone" in install_src
    assert "docker pull" in install_src

    build_src = build.read_text(encoding="utf-8")
    assert "${PAWFLOW_IMAGE}" in build_src

    doctor_src = doctor.read_text(encoding="utf-8")
    assert "wsl.exe --status" in doctor_src
    assert "Docker Desktop" in doctor_src
    assert "docker info" in doctor_src
    assert "/var/run/docker.sock" in doctor_src
    assert "--require-socket" in doctor_src
    assert "--source" in doctor_src
    assert "Port $PORT" in doctor_src

    doctor_ps1_src = doctor_ps1.read_text(encoding="utf-8")
    assert "wsl.exe --status" in doctor_ps1_src
    assert "wsl --install" in doctor_ps1_src
    assert "Docker Desktop" in doctor_ps1_src
    assert "Linux containers" in doctor_ps1_src
    assert "Port $Port" in doctor_ps1_src


def test_install_docs_and_agent_prompt_capture_bootstrap_contract():
    doc = Path("docs/installation_bootstrap.md").read_text(encoding="utf-8")
    prompt = Path("docs/prompts/install_with_agent.md").read_text(encoding="utf-8")

    assert "PawFlow Installer" in doc
    assert "doctor-pawflow.sh" in doc
    assert "doctor-pawflow.ps1" in doc
    assert "RoyBetty" in doc
    assert "Never create a default user relay" in doc
    assert "Install relay client" in doc
    assert "/var/run/docker.sock" in doc
    assert "Summarizer service" in doc
    assert "Variables and secrets" in doc
    assert "bootstrap self-signed TLS certificate" in doc
    assert "Let's Encrypt" in doc
    assert "ZeroSSL" in doc
    assert "summarizer_service" in doc
    assert "secret IDs" in doc

    assert "docker info" in prompt
    assert "doctor-pawflow.sh" in prompt
    assert "doctor-pawflow.ps1" in prompt
    assert "docker pull ghcr.io/allcolor/pawflow:latest" in prompt
    assert "https://localhost:9090" in prompt
    assert "self-signed bootstrap certificate" in prompt
    assert "Do not configure relays" in prompt
    assert "summarizer service" in prompt
    assert "variables, secrets" in prompt
    assert "RoyBetty" in prompt


def test_cli_bootstrap_failure_is_not_silently_ignored():
    src = Path("cli.py").read_text(encoding="utf-8")
    block = src[src.index("from core.install_bootstrap import ensure_install_bootstrap"):
                src.index("logger.info(\"Restoring deployed flows")]
    assert "logger.error" in block
    assert "raise" in block


def test_pawflow_installer_flow_template_exists():
    latest = Path("data/repository/flows/global/default/pawflow_installer/latest.json")
    template = Path("data/repository/flows/global/default/pawflow_installer/versions/1.0.0.json")

    assert latest.exists()
    assert template.exists()

    flow = json.loads(template.read_text(encoding="utf-8"))
    assert flow["id"] == "pawflow-installer"
    assert flow["fqn"] == "default.pawflow_installer:1.0.0"
    assert flow["parameters"]["bootstrap_gateway_key"] == "RoyBetty"
    assert flow["parameters"]["ssl_certfile"] == "data/system/ssl/bootstrap.crt"
    assert flow["parameters"]["ssl_keyfile"] == "data/system/ssl/bootstrap.key"
    listener_params = flow["services"]["http_listener"]["parameters"]
    assert listener_params["ssl_certfile"] == "${ssl_certfile}"
    assert listener_params["ssl_keyfile"] == "${ssl_keyfile}"

    routes = flow["tasks"]["http_in"]["parameters"]["routes"]
    patterns = {route["pattern"] for route in routes}
    assert "/install" in patterns
    assert "/install/api" in patterns

    api_content = flow["tasks"]["install_api"]["parameters"]["content"]
    assert "self_signed_bootstrap" in api_content
    assert "letsencrypt_acme" in api_content
    assert "summarizer_service" in api_content
    assert "variables" in api_content
    assert "secrets" in api_content
