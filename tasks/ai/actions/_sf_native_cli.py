"""Native CLI login, stored-auth status and managed-image version actions."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess  # nosec B404 - bounded Docker CLI calls.
import threading
import time
import uuid

from core.native_cli_auth import (
    PROVIDERS, merge_native_auth, native_cli_auth_status, native_cli_binary,
    native_cli_home, native_cli_image, native_cli_user_spec,
)
from tasks.ai.actions._sf_base import (
    _UNHANDLED, _is_admin, _resolve_service_definition_for_action,
)
from tasks.ai.actions._sf_routes import (
    _docker_container_ip, _docker_published_host, _ensure_vnc_routes,
    _publish_command_result, _wait_for_vnc_login_backend,
)

logger = logging.getLogger(__name__)
ACTIONS = {
    "native_cli_server_login", "native_cli_server_login_status",
    "native_cli_server_login_cleanup", "native_cli_status",
    "native_cli_versions", "native_cli_update",
}
RESULT_FILE = "/tmp/native-cli-login.result.json"  # nosec B108 - inside the login container.


def _reply(flowfile, data):
    flowfile.set_content(json.dumps(data).encode())
    return [flowfile]


def _docker(args, timeout=10):
    from core.docker_utils import docker_cmd
    return subprocess.run(docker_cmd() + args, capture_output=True, text=True,
                          timeout=timeout)  # nosec B603


def _remove_container(container):
    try:
        _docker(["rm", "-f", container])
    except Exception:
        logger.debug("Native login cleanup failed", exc_info=True)


def _session_for_request(body, user_id):
    from services.vnc_proxy import _sessions
    session = _sessions.get(body.get("session_id", ""))
    if (not session or session.get("native_provider") not in PROVIDERS
            or session.get("owner_user_id") != user_id):
        raise ValueError("Unknown login session")
    if body.get("service_id") and body["service_id"] != session["service_id"]:
        raise ValueError("Login session belongs to a different service")
    return session


def _managed(config):
    # External server credentials and upgrades belong to that server.
    return config.get("opencode_mode", "managed") == "managed"


def _login_argv(provider, user_id, service_id, container, port):
    from core.docker_utils import pawflow_container_labels, to_host_path, translate_path
    home = native_cli_home(provider, user_id, service_id)
    script = Path(__file__).resolve().parents[3] / "docker/claude-code/native_cli_login.py"
    mounts = ["-v", f"{translate_path(to_host_path(str(script)))}:/opt/pawflow/native_cli_login.py:ro"]
    login_home = "/workspace"
    if provider == "cursor-acp":
        # Cursor owns its undocumented credential layout; keep its entire
        # private auth home, never a guessed subset of files.
        mounts += ["-v", f"{translate_path(to_host_path(str(home)))}:/native-home"]
        login_home = "/native-home"
    return [
        "run", "--rm", "--detach", "--pull", "never", "--name", container,
        *pawflow_container_labels("login"), "-p", f"{port}:6080",
        "--tmpfs", "/workspace:rw,size=64m", "--shm-size", "512m",
        "--user", "root",
        "-e", f"PAWFLOW_NATIVE_PROVIDER={provider}",
        "-e", f"PAWFLOW_NATIVE_HOME={login_home}",
        "-e", f"PAWFLOW_NATIVE_BIN={native_cli_binary(provider)}",
        "-e", f"PAWFLOW_NATIVE_USER={native_cli_user_spec()}",
        *mounts, "--entrypoint", "python3", native_cli_image(provider),
        "/opt/pawflow/native_cli_login.py",
    ]


def _complete_login(provider, user_id, service_id, container):
    if provider == "cursor-acp":
        return
    relative = ".grok/auth.json" if provider == "grok-build-acp" else ".local/share/opencode/auth.json"
    result = _docker(["exec", container, "cat", "/workspace/" + relative])
    if result.returncode:
        raise ValueError("Login reported success but wrote no credential file")
    merge_native_auth(native_cli_home(provider, user_id, service_id),
                      relative, json.loads(result.stdout))


def _start_login(provider, service_id, user_id, conversation_id, flowfile):
    from pawflow_relay.utils import find_free_port
    from services.vnc_proxy import register_session, _sessions, update_session_ready

    session_id = uuid.uuid4().hex
    container = "pawflow-native-login-" + session_id[:12]
    port = find_free_port()
    backend_host = _docker_published_host()
    token = register_session(
        session_id, port, owner_user_id=user_id, user_id=user_id,
        ttl_seconds=360,
        conversation_id=conversation_id,
        login_session_id=flowfile.get_attribute("auth.session_id") or "",
        container=container, service_id=service_id, native_provider=provider,
        launch_time=time.time(), ready=False, host=backend_host)
    session = _sessions[session_id]
    session["native_status"] = {"status": "starting"}

    def work():
        try:
            result = _docker(_login_argv(provider, user_id, service_id, container, port), timeout=30)
            if result.returncode:
                raise ValueError("Could not start login container; install the CLI tools image from Updates")
            if session.get("native_cancelled"):
                return
            container_host = _docker_container_ip(container)
            if not _wait_for_vnc_login_backend(session_id, backend_host, port,
                                               "[native-cli-login]", container_host):
                raise ValueError("Login display did not become ready")
            _ensure_vnc_routes(flowfile)
            update_session_ready(session_id)
            session["native_status"] = {"status": "pending"}
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready",
                {"session_id": session_id, "service_id": service_id,
                 "token": token, "cli": "native"})
            deadline = session["launch_time"] + 300
            while time.time() < deadline and not session.get("native_cancelled"):
                result = _docker(["exec", container, "cat", RESULT_FILE])
                if result.returncode == 0:
                    outcome = json.loads(result.stdout)
                    if not isinstance(outcome, dict) or outcome.get("ok") is not True:
                        raise ValueError("Native CLI login failed")
                    _complete_login(provider, user_id, service_id, container)
                    session["native_status"] = {
                        "ok": True, "message": f"{PROVIDERS[provider][1]} login saved"}
                    return
                time.sleep(2)
            if not session.get("native_cancelled"):
                raise ValueError("Native CLI login timed out (5 min)")
        except Exception:
            logger.debug("Native CLI login failed", exc_info=True)
            session["native_status"] = {"error": "Native CLI login failed; check the CLI installation and retry"}
            _publish_command_result(conversation_id, session["native_status"])
        finally:
            _remove_container(container)

    threading.Thread(target=work, daemon=True, name=f"native-login-{session_id[:12]}").start()
    return _reply(flowfile, {"ok": True, "session_id": session_id, "token": token,
                            "cli": "native", "message": "Starting native CLI login..."})


def _versions(provider, config):
    from core import update_manager
    if not _managed(config):
        return {"error": "This OpenCode server is externally managed; check its health and upgrade it on its host"}
    key, label, _, _ = PROVIDERS[provider]
    image = native_cli_image(provider)
    current = update_manager.installed_cli_versions(image).get(key, "")
    latest = (update_manager.latest_npm_version("opencode-ai") if key == "opencode"
              else update_manager.latest_native_version(key))
    component = update_manager._component(key, label, current, latest, image=image)
    component["message"] = f"{label}: installed {current or 'unknown'}, latest {latest or 'unknown'} ({image})"
    return component


def _handle_sf_native_cli(self, action, body, store, user_id, flowfile, _helpers):
    if action not in ACTIONS:
        return _UNHANDLED
    try:
        if not user_id:
            raise ValueError("Authentication required")
        if action in {"native_cli_server_login_status", "native_cli_server_login_cleanup"}:
            session = _session_for_request(body, user_id)
            if action.endswith("_status"):
                return _reply(flowfile, session["native_status"])
            from services.vnc_proxy import unregister_session
            session["native_cancelled"] = True
            threading.Thread(target=_remove_container, args=(session["container"],),
                             daemon=True, name="native-login-cleanup").start()
            unregister_session(body["session_id"])
            return _reply(flowfile, {"ok": True})

        service_id = str(body.get("service_id") or "")
        conversation_id = (body.get("conversation_id")
                           or flowfile.get_attribute("http.conversation_id") or "")
        if not service_id:
            raise ValueError("Missing service_id")
        sdef = _resolve_service_definition_for_action(
            service_id, user_id, conversation_id, body.get("scope", ""))
        config = getattr(sdef, "config", {}) or {}
        provider = config.get("provider")
        if provider not in PROVIDERS:
            raise ValueError("Service is not a supported native CLI connection")
        if not _managed(config):
            raise ValueError("External OpenCode auth and upgrades must be managed on its server")
        if action == "native_cli_server_login":
            return _start_login(provider, service_id, user_id, conversation_id, flowfile)
        if action == "native_cli_status":
            state = native_cli_auth_status(provider, user_id, service_id)
            field = "opencode_env" if provider == "opencode" else "acp_env"
            raw_env = config.get(field) or {}
            try:
                env = json.loads(raw_env) if isinstance(raw_env, str) else raw_env
            except ValueError:
                raise ValueError("Provider environment must be a JSON object of strings") from None
            if not isinstance(env, dict) or any(not isinstance(value, str) for value in env.values()):
                raise ValueError("Provider environment must be a JSON object of strings")
            configured = any(value.strip() for value in env.values())
            return _reply(flowfile, {**state, "provider_environment_configured": configured,
                "message": ("Native login credentials are stored." if state["stored"]
                            else "No native login credentials are stored.")
                           + (" Provider environment variables are configured." if configured else "")
                           + " Token validity is checked when the CLI connects."})
        if action == "native_cli_update":
            from core.update_manager import cli_image_name
            if not _is_admin(flowfile):
                raise ValueError("An administrator must update the CLI tools image")
            if native_cli_image(provider) != cli_image_name():
                raise ValueError("This service uses a custom image; rebuild that image on its host")
            return _reply(flowfile, {"ok": True, "open_updates": True})
        def work():
            try:
                result = _versions(provider, config)
            except Exception:
                result = {"error": "Could not check native CLI versions"}
            _publish_command_result(conversation_id, result)
        threading.Thread(target=work, daemon=True, name="native-cli-versions").start()
        return _reply(flowfile, {"ok": True, "message": "Checking installed and latest CLI versions..."})
    except ValueError as exc:
        return _reply(flowfile, {"error": str(exc)})
