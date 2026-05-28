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
    build_server_minimal_relay = Path("scripts/build-server-minimal-relay.sh")
    run = Path("scripts/run-pawflow-docker.sh")
    doctor = Path("scripts/doctor-pawflow.sh")
    doctor_ps1 = Path("scripts/doctor-pawflow.ps1")

    for script in (install, build, build_server_minimal_relay, run, doctor):
        assert script.exists()
        assert script.stat().st_mode & 0o111
        assert "set -euo pipefail" in script.read_text(encoding="utf-8")

    assert doctor_ps1.exists()

    run_src = run.read_text(encoding="utf-8")
    assert "printenv PAWFLOW_IMAGE" in run_src
    assert "printenv PAWFLOW_HOME" in run_src
    assert "PAWFLOW_PUBLISH_HOST" in run_src
    assert 'PUBLISH_HOST="127.0.0.1"' in run_src
    assert "PAWFLOW_BOOTSTRAP_GATEWAY_KEY" in run_src
    assert "BOOTSTRAP_GATEWAY_KEY" in run_src
    assert "RoyBetty" in run_src
    assert "--help|-h" in run_src
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_src
    assert "--group-add" in run_src
    assert "command -v docker" in run_src
    assert "does not contain the Docker CLI" in run_src
    assert '-p "$PUBLISH_HOST:$PORT:$PORT"' in run_src
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
    assert "--from-source" in install_src
    assert "--version" in install_src
    assert "Checkout this git tag before building runtime images" in install_src
    assert "--pull-server" in install_src
    assert "--platform" in install_src
    assert "--native" in install_src
    assert "--container" in install_src
    assert "--no-start" in install_src
    assert "PAWFLOW_VERSION" in install_src
    assert "PAWFLOW_DOCKER_PLATFORM" in install_src
    assert "PAWFLOW_START_TARGET" in install_src
    assert "PAWFLOW_DATA_DIR" in install_src
    assert "python -m venv" not in install_src
    assert "-m venv" in install_src
    assert "SERVER_MODE=\"auto\"" in install_src
    assert "START_TARGET=\"container\"" in install_src
    assert 'HOST="0.0.0.0"' in install_src
    assert 'native_host="127.0.0.1"' in install_src
    assert 'IMAGE="$IMAGE_REPO:latest"' in install_src
    assert "ghcr.io/allcolor/pawflow" in install_src
    assert "docker pull \"${pull_args[@]}\" \"$IMAGE\"" in install_src
    assert "Prebuilt PawFlow server image unavailable" in install_src
    assert 'SERVER_MODE="source" prepare_checkout_ref "$REPO_DIR"' in install_src
    assert '[[ "$SERVER_MODE" != "source" && -z "$VERSION" ]]' in install_src
    assert "refs/tags/$VERSION" in install_src
    assert "checkout main" in install_src
    assert "git clone" in install_src
    assert "docker/claude-code/build.sh" in install_src
    assert "scripts/build-server-minimal-relay.sh" in install_src
    assert "docker/relay-dev/build.sh" in install_src
    assert "pawflow-claude-code:latest" in install_src
    assert "pawflow-relay-minimal:latest" in install_src
    assert "pawflow-relay-dev:latest" in install_src
    assert "windows-shell" in install_src
    assert "Native Windows shells are not supported" not in install_src
    assert 'printenv PAWFLOW_BOOTSTRAP_GATEWAY_KEY' in install_src
    assert 'PAWFLOW_BOOTSTRAP_GATEWAY_KEY="$bootstrap_gateway_key"' in install_src

    build_src = build.read_text(encoding="utf-8")
    assert "printenv PAWFLOW_IMAGE" in build_src
    assert "PAWFLOW_DOCKER_PLATFORM" in build_src
    assert "--platform" in build_src

    claude_build_src = Path("docker/claude-code/build.sh").read_text(encoding="utf-8")
    relay_dev_build_src = Path("docker/relay-dev/build.sh").read_text(encoding="utf-8")
    generator_src = Path("scripts/generate-relay-image.py").read_text(encoding="utf-8")
    assert "PAWFLOW_DOCKER_PLATFORM" in claude_build_src
    assert "PAWFLOW_DOCKER_PLATFORM" in relay_dev_build_src
    assert "PAWFLOW_DOCKER_PLATFORM" in generator_src
    assert 'PAWFLOW_DOCKER_PLATFORM:-' in generator_src

    minimal_relay_src = build_server_minimal_relay.read_text(encoding="utf-8")
    assert "PAWFLOW_SERVER_MINIMAL_RELAY_IMAGE" in minimal_relay_src
    assert "PAWFLOW_PYTHON" in minimal_relay_src
    assert "pawflow-relay-minimal:latest" in minimal_relay_src
    assert "--profile server-minimal" in minimal_relay_src
    assert "ghcr.io/allcolor/pawflow:latest" in build_src

    doctor_src = doctor.read_text(encoding="utf-8")
    assert "printenv PAWFLOW_PORT" in doctor_src
    assert "wsl.exe --status" in doctor_src
    assert "Docker Desktop" in doctor_src
    assert "Native Windows install can continue" in doctor_src
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
    assert "native Windows" in doctor_ps1_src
    assert "Linux containers" in doctor_ps1_src
    assert "docker info >/dev/null 2>&1" in doctor_ps1_src
    assert "Docker daemon reachable from WSL" in doctor_ps1_src
    assert "Native Windows install can continue" in doctor_ps1_src
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
    assert "PAWFLOW_PUBLISH_HOST=0.0.0.0" in doc
    assert "PAWFLOW_BOOTSTRAP_RESET=1" in doc
    assert "mounted cert/key files" in doc
    assert "private self-signed" in doc
    assert "summarizer_service" in doc
    assert "secret IDs" in doc
    assert "Complete from-scratch install" in doc
    assert "--version VERSION" in doc
    assert "--from-source" in doc
    assert "--native" in doc
    assert "prebuilt" in doc
    assert "checks out the matching git tag before" in doc
    assert "pawflow-claude-code:latest" in doc
    assert "pawflow-relay-minimal:latest" in doc
    assert "pawflow-relay-dev:latest" in doc

    assert "docker info" in prompt
    assert "doctor-pawflow.sh" in prompt
    assert "doctor-pawflow.ps1" in prompt
    assert "bash scripts/install-pawflow.sh" in prompt
    assert "--version VERSION" in prompt
    assert "--from-source" in prompt
    assert "--native" in prompt
    assert "prebuilt" in prompt
    assert "pawflow-claude-code:latest" in prompt
    assert "pawflow-relay-minimal:latest" in prompt
    assert "pawflow-relay-dev:latest" in prompt
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
    assert "Fresh complete install" in doc
    assert "Versioned install" in doc
    assert "Native server install" in doc
    assert "--from-source --version" in doc
    assert "--native" in doc
    assert "PAWFLOW_DOCKER_PLATFORM" in doc
    assert "pawflow-claude-code:latest" in doc
    assert "pawflow-relay-minimal:latest" in doc
    assert "pawflow-relay-dev:latest" in doc
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


def test_python_package_metadata_includes_cli_and_relay_tools():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'py-modules = ["cli"]' in pyproject
    include_block = pyproject[pyproject.index("[tool.setuptools.packages.find]"):]
    include_block = include_block[:include_block.index("exclude =")]
    assert '"tools*"' in include_block
    assert '"api*"' not in include_block
    assert '"gui*"' not in include_block
    assert '"pawflow_sdk*"' not in include_block
    assert Path("tools/__init__.py").exists()


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
    assert "/" in patterns
    assert "/install" in patterns
    assert "/install/api" in patterns
    assert "/install/api/llm-credential/prepare" in patterns
    assert "/install/api/llm-credential/paste" in patterns
    assert "/install/api/llm-credential/server-login" in patterns
    assert "/install/api/llm-credential/server-login/status" in patterns
    assert "/install/api/llm-credential/server-login/cleanup" in patterns
    assert "/install/api/finalize" in patterns

    redirect = flow["tasks"]["redirect_to_install"]["parameters"]
    assert redirect["status_code"] == 302
    assert redirect["headers"]["Location"] == "/install"
    assert {
        "from": "http_in",
        "to": "redirect_to_install",
        "type": "GET:/",
    } in flow["relations"]
    route_relationships = {
        route.get("relationship") or f"{route.get('method', 'GET').upper()}:{route.get('pattern', '/')}"
        for route in routes
    }
    relation_keys = {
        (rel["from"], rel["type"])
        for rel in flow["relations"]
    }
    for relationship in route_relationships:
        assert ("http_in", relationship) in relation_keys

    assert flow["tasks"]["install_api"]["type"] == "installBootstrap"
    route_names = set(flow["tasks"]["route_install"]["parameters"]["routes"])
    assert {
        "api_status",
        "api_finalize",
        "llm_credential_prepare",
        "llm_credential_paste",
        "llm_credential_server_login",
        "llm_credential_server_login_status",
        "llm_credential_server_login_cleanup",
    }.issubset(route_names)

    ui_params = flow["tasks"]["install_ui"]["parameters"]
    assert ui_params["content_file"] == "install.html"
    ui_asset = template.parent / "assets" / "install.html"
    assert ui_asset.exists()
    ui_content = ui_asset.read_text(encoding="utf-8")
    assert "Admin User" in ui_content
    assert "OAuth Configuration" in ui_content
    assert "Private Gateway" in ui_content
    assert "fetch('/install/api'" in ui_content
    assert "fetch('/install/api/llm-credential/prepare'" in ui_content
    assert "fetch('/install/api/llm-credential/paste'" in ui_content
    assert "fetch('/install/api/llm-credential/server-login'" in ui_content
    assert "fetch('/install/api/llm-credential/server-login/status'" in ui_content
    assert "fetch('/install/api/finalize'" in ui_content
    assert "private_gateway_skins" in ui_content
    assert "gateway_skin" in ui_content
    assert "new_gateway_key" in ui_content
    assert "admin_password" in ui_content
    assert "admin_password_confirm" in ui_content
    assert "Confirm admin password" in ui_content
    assert "validAdminPassword" in ui_content
    assert "Admin password confirmation must match" in ui_content
    assert "${" not in ui_content
    assert "`" not in ui_content
    assert "llm_service_id" in ui_content
    assert "credential_service_id" in ui_content
    assert "llm_service_scope" in ui_content
    assert "credential_pool_scope" in ui_content
    assert "summarizer_service_scope" in ui_content
    assert "External providers" in ui_content
    assert "Final TLS mode" in ui_content
    assert "Agy / Antigravity" in ui_content
    assert "Admin Facebook email/id" in ui_content
    assert "Admin Amazon email/id" in ui_content
    assert "Admin Telegram id" in ui_content
    assert "Generic provider name" in ui_content
    assert "oauth_generic_authorize_url" in ui_content
    assert "blocking smoke checks" in ui_content
    assert "antigravity-interactive" in ui_content
    assert "Create login pool" in ui_content
    assert "Login via server" in ui_content
    assert "id=\"vnc_dialog\"" in ui_content
    assert "id=\"vnc_frame\"" in ui_content
    assert "showVncDialog" in ui_content
    assert "window.open" not in ui_content
    assert "codex_appserver_llm_service" in ui_content
    assert "window.location.href='/chat'" in ui_content


def test_install_bootstrap_reset_is_implemented():
    src = Path("core/install_bootstrap.py").read_text(encoding="utf-8")

    assert "PAWFLOW_BOOTSTRAP_RESET" in src
    assert "INSTALL_STATE_FILE.unlink" in src


def test_vnc_login_routes_skip_session_auth():
    src = Path("tasks/ai/actions/service_flow.py").read_text(encoding="utf-8")

    assert "ws_handler=vnc_ws_proxy, public=True" in src
    assert "callback=vnc_http_proxy, public=True" in src


def test_pawflow_agent_auth_routes_cover_login_forms():
    template = Path("data/repository/flows/global/default/pawflow_agent/versions/1.0.0.json")
    flow = json.loads(template.read_text(encoding="utf-8"))

    routes = flow["tasks"]["http_in"]["parameters"]["routes"]
    route_keys = {(route["method"], route["pattern"]) for route in routes}
    assert ("GET", "/auth/login") in route_keys
    assert ("POST", "/auth/login/builtin") in route_keys
    assert ("GET", "/auth/login/{provider}") in route_keys

    relation_keys = {
        (rel["from"], rel["type"])
        for rel in flow["relations"]
    }
    for route in routes:
        relationship = route.get("relationship") or f"{route.get('method', 'GET').upper()}:{route.get('pattern', '/')}"
        assert ("http_in", relationship) in relation_keys


def test_server_relay_uses_embedded_code_by_default():
    src = Path("core/server_relay_manager.py").read_text(encoding="utf-8")

    assert '"server_relay_mount_code": "0"' in src
    assert "code_mount_args = []" in src
    assert "if relay_mount_code:" in src
    assert "pawflow_relay_launcher.py" in src
