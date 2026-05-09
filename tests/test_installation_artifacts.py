from pathlib import Path
import json
import os
import subprocess


def test_server_dockerfile_supports_bootstrap_docker_builds():
    src = Path("Dockerfile").read_text(encoding="utf-8")

    assert "docker.io" in src
    assert "gosu" in src
    assert "openssl" in src
    assert "ca-certificates" in src
    assert "curl" in src
    assert "ffmpeg" in src
    assert "PLAYWRIGHT_BROWSERS_PATH" in src
    assert "python -m playwright install --with-deps chromium" in src
    assert "useradd -u 1000 -g 1000" in src
    assert "/app/data /app/certs" in src
    assert "/app/default-data" in src
    assert "/app/default-config" in src
    assert "server-entrypoint.sh" in src

    entrypoint = Path("docker/server-entrypoint.sh").read_text(encoding="utf-8")
    assert "seed_missing_tree /app/default-data/repository /app/data/repository" in entrypoint
    assert "seed_missing_tree /app/default-config /app/config" in entrypoint
    assert "chown -R pawflow:pawflow" in entrypoint
    assert "exec gosu pawflow" in entrypoint

    relay_dev = Path("docker/relay-dev/Dockerfile").read_text(encoding="utf-8")
    assert "COPY tools/ /opt/pawflow/" in relay_dev
    assert "COPY pawflow_relay/ /opt/pawflow/pawflow_relay/" in relay_dev

    relay_build = Path("docker/relay-dev/build.sh").read_text(encoding="utf-8")
    assert "-f \"$SCRIPT_DIR/Dockerfile\"" in relay_build
    assert '"$REPO_DIR"' in relay_build


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
    assert "printenv PAWFLOW_IMAGE" in run_src
    assert "printenv PAWFLOW_HOME" in run_src
    assert "PAWFLOW_BOOTSTRAP_GATEWAY_KEY" in run_src
    assert "BOOTSTRAP_GATEWAY_KEY" in run_src
    assert "RoyBetty" in run_src
    assert "--help|-h" in run_src
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_src
    assert "--group-add" in run_src
    assert "$PAWFLOW_HOME/data:/app/data" in run_src
    assert "$PAWFLOW_HOME/certs:/app/certs" in run_src

    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    assert "data/runtime" in dockerignore
    assert "data/system" in dockerignore
    assert "pawflow-relay-desktop/node_modules" in dockerignore

    install_src = install.read_text(encoding="utf-8")
    assert "printenv PAWFLOW_IMAGE" in install_src
    assert "ghcr.io/allcolor/pawflow:latest" in install_src
    assert "doctor-pawflow.sh" in install_src
    assert "--skip-doctor" in install_src
    assert "--source" in install_src
    assert "git clone" in install_src
    assert "docker pull" in install_src

    build_src = build.read_text(encoding="utf-8")
    assert "printenv PAWFLOW_IMAGE" in build_src
    assert "ghcr.io/allcolor/pawflow:latest" in build_src

    doctor_src = doctor.read_text(encoding="utf-8")
    assert "printenv PAWFLOW_PORT" in doctor_src
    assert "wsl.exe --status" in doctor_src
    assert "Docker Desktop" in doctor_src
    assert "docker info" in doctor_src
    assert "/var/run/docker.sock" in doctor_src
    assert "--require-socket" in doctor_src
    assert "--source" in doctor_src
    assert "Port $PORT" in doctor_src

    doctor_ps1_src = doctor_ps1.read_text(encoding="utf-8")
    assert "wsl.exe --status" in doctor_ps1_src
    assert "wsl.exe --list --verbose" in doctor_ps1_src
    assert "wsl --install" in doctor_ps1_src
    assert "Docker Desktop" in doctor_ps1_src
    assert "Linux containers" in doctor_ps1_src
    assert "docker info >/dev/null 2>&1" in doctor_ps1_src
    assert "Docker daemon reachable from WSL" in doctor_ps1_src
    assert "Windows docker CLI exists but daemon is not reachable" not in doctor_ps1_src
    assert "test -S /var/run/docker.sock" in doctor_ps1_src
    assert "Get-NetTCPConnection" in doctor_ps1_src
    assert "PortInUseFromWindows" in doctor_ps1_src
    assert "PortInUseFromWsl" in doctor_ps1_src
    assert "already in use on Windows" in doctor_ps1_src
    assert "already in use inside WSL" in doctor_ps1_src
    assert "Port $Port" in doctor_ps1_src


def test_install_script_help_works_without_pawflow_env(tmp_path):
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
    }
    for script in (
        "scripts/install-pawflow.sh",
        "scripts/run-pawflow-docker.sh",
        "scripts/doctor-pawflow.sh",
    ):
        result = subprocess.run(
            ["bash", script, "--help"],
            cwd=Path.cwd(),
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr


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


def test_compose_healthcheck_accepts_bootstrap_tls():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "https://localhost:9090/health" in compose
    assert "ssl._create_unverified_context" in compose
    assert "http://localhost:9090/health" in compose
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose


def test_docker_docs_explain_wsl_vhdx_compaction():
    doc = Path("docs/docker.md").read_text(encoding="utf-8")

    assert "Complete install scenarios" in doc
    assert "Fresh published-image install" in doc
    assert "Restart before finalization" in doc
    assert "Restart after finalization" in doc
    assert "Docker socket unavailable" in doc
    assert "Server-side relay after install" in doc
    assert "Windows host prerequisites" in doc
    assert "run the normal Linux" in doc
    assert "install script inside WSL" in doc
    assert "WSL2: Reclaim Docker Build Cache Space" in doc
    assert "docker builder prune -a" in doc
    assert "ext4.vhdx" in doc
    assert "compact vdisk" in doc
    assert "Do not use Windows Settings **Reset**" in doc


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
    assert flow["parameters"]["bootstrap_gateway_secret_ref"] == "privategateway.bootstrap"
    assert flow["parameters"]["private_gateway_service_id"] == "_bootstrap_private_gateway"
    assert flow["parameters"]["ssl_certfile"] == "data/system/ssl/bootstrap.crt"
    assert flow["parameters"]["ssl_keyfile"] == "data/system/ssl/bootstrap.key"
    listener_params = flow["services"]["http_listener"]["parameters"]
    assert listener_params["ssl_certfile"] == "${ssl_certfile}"
    assert listener_params["ssl_keyfile"] == "${ssl_keyfile}"
    assert listener_params["private_gateway_service_id"] == "${private_gateway_service_id}"

    routes = flow["tasks"]["http_in"]["parameters"]["routes"]
    patterns = {route["pattern"] for route in routes}
    assert "/install" in patterns
    assert "/install/api" in patterns
    assert "/install/api/finalize" in patterns

    assert flow["tasks"]["install_api"]["type"] == "installBootstrap"
    route_names = set(flow["tasks"]["route_install"]["parameters"]["routes"])
    assert {"api_status", "api_finalize"}.issubset(route_names)

    ui_content = flow["tasks"]["install_ui"]["parameters"]["content"]
    assert "fetch('/install/api'" in ui_content
    assert "fetch('/install/api/finalize'" in ui_content
    assert "new_gateway_key" in ui_content
    assert "admin_password" in ui_content
    assert "llm_service_id" in ui_content
    assert "codex_appserver_llm_service" in ui_content


def test_server_relay_uses_embedded_code_by_default():
    src = Path("core/server_relay_manager.py").read_text(encoding="utf-8")

    assert '"server_relay_mount_code": "0"' in src
    assert "code_mount_args = []" in src
    assert "if relay_mount_code:" in src
    assert "pawflow_relay_launcher.py" in src
