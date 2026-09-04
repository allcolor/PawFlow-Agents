"""service_flow branch: Antigravity ACP server login through noVNC.

The ``antigravity-acp`` provider logs in through Google's ACP server itself
(``authenticate`` opens a browser and completes the OAuth redirect on the
server's loopback listener). This module runs that flow in a
``pawflow-claude-code`` login container with the shared display/noVNC setup
and copies the resulting token files into the service ``GEMINI_HOME`` kept by
``core.antigravity_acp_pool``.

The UI reuses the Agy login dialog: ``agy_server_login`` and
``agy_server_login_status`` in ``_sf_k8`` branch here when the service
provider is ``antigravity-acp``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 - login containers are docker processes.
import threading
import time
from pathlib import Path

from tasks.ai.actions._sf_routes import (
    _docker_container_ip,
    _docker_published_host,
    _ensure_vnc_routes,
    _publish_command_result,
    _wait_for_vnc_login_backend,
)

logger = logging.getLogger(__name__)

PROVIDER = "antigravity-acp"
LOGIN_SCRIPT = "agy_acp_auth_login.sh"
LOGIN_DRIVER = "agy_acp_login.py"
LOGIN_IMAGE = "pawflow-claude-code:latest"
CONTAINER_PREFIX = "pawflow-agyacp-login-"
CONTAINER_RESULT = "/tmp/agy-acp-login.result.json"  # nosec B108 - path inside the login container
CONTAINER_TOKEN_DIR = "/workspace/.gemini/antigravity-acp"
TOKEN_FILES = ("acp_token.json", "acp_business_token.json")
LOGIN_TIMEOUT_SECONDS = 300
#: Methods that need the browser; the API-key methods are configured, not logged in.
BROWSER_METHODS = ("oauth-personal", "oauth-business")


def _is_antigravity_acp_service(sdef) -> bool:
    config = getattr(sdef, "config", {}) or {}
    return str(config.get("provider") or "") == PROVIDER


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def _requested_method(body: dict, sdef) -> str:
    from core.llm_providers.antigravity_acp import DEFAULT_AUTH_METHOD

    form = body.get("config") if isinstance(body.get("config"), dict) else {}
    stored = getattr(sdef, "config", {}) or {}
    return str(
        form.get("antigravity_acp_auth_method")
        or stored.get("antigravity_acp_auth_method")
        or DEFAULT_AUTH_METHOD
    ).strip()


def start_antigravity_acp_login(body, user_id, flowfile, *, sdef, service_id,
                                conversation_id):
    """Start the login container; returns the flowfile list for the action."""
    method = _requested_method(body, sdef)
    if method not in BROWSER_METHODS:
        flowfile.set_content(json.dumps({
            "error": (
                f"antigravity_acp_auth_method={method} does not use a browser "
                "login; set api_key on the service instead"),
        }).encode())
        return [flowfile]

    try:
        import uuid as _uuid
        from pawflow_relay.utils import find_free_port as _find_free_port
        from services.vnc_proxy import register_session

        session_id = _uuid.uuid4().hex[:12]
        free_port = _find_free_port()
        backend_host = _docker_published_host()
        container_name = f"{CONTAINER_PREFIX}{session_id}"
        volume_name = (f"pawflow_ws_{conversation_id}" if conversation_id
                       else f"pawflow_login_{session_id}")
        logger.info("[agy-acp-login] Creating session %s (port %d)", session_id, free_port)
        vnc_token = register_session(
            session_id, free_port,
            owner_user_id=user_id,
            conversation_id=conversation_id,
            login_session_id=flowfile.get_attribute("auth.session_id") or "",
            container=container_name, service_id=service_id,
            user_id=user_id, volume=volume_name,
            launch_time=time.time(), ready=False,
            host=backend_host)
    except Exception as exc:
        logger.error("[agy-acp-login] Setup failed: %s", exc, exc_info=True)
        flowfile.set_content(json.dumps({"error": f"Login setup failed: {exc}"}).encode())
        return [flowfile]

    def _bg_setup():
        from core.docker_utils import (
            docker_cmd,
            pawflow_container_labels,
            to_host_path,
            translate_path,
        )
        try:
            root = _project_root()
            mounts = []
            for name in (LOGIN_SCRIPT, LOGIN_DRIVER):
                src = os.path.join(root, "docker", "claude-code", name)
                if os.path.exists(src):
                    mounts += ["-v", f"{translate_path(to_host_path(src))}:/opt/pawflow/{name}:ro"]
            argv = docker_cmd() + [
                "run", "--rm", "--detach",
                "--name", container_name,
                *pawflow_container_labels("login"),
                "-p", f"{free_port}:6080",
                "--tmpfs", "/workspace:rw,size=64m",
                "--shm-size", "512m",
                "-e", "HOME=/home/pawflow",
                "-e", f"AGY_ACP_AUTH_METHOD={method}",
                *mounts,
                "--entrypoint", "bash",
                LOGIN_IMAGE,
                f"/opt/pawflow/{LOGIN_SCRIPT}",
            ]
            logger.info("[agy-acp-login] Starting container %s on port %d", container_name, free_port)
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)  # nosec B603
            if result.returncode != 0:
                from services.vnc_proxy import update_session_error
                logger.error("[agy-acp-login] Docker failed: %s", result.stderr[:300])
                update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                _publish_command_result(conversation_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                return
        except Exception as exc:
            from services.vnc_proxy import update_session_error
            logger.error("[agy-acp-login] Docker error: %s", exc)
            update_session_error(session_id, str(exc))
            _publish_command_result(conversation_id, {"error": f"Login failed: {exc}"})
            return

        container_host = _docker_container_ip(container_name)
        if not _wait_for_vnc_login_backend(session_id, backend_host, free_port,
                                           "[agy-acp-login]", container_host):
            return
        _ensure_vnc_routes(flowfile)
        from services.vnc_proxy import update_session_ready
        update_session_ready(session_id)
        from core.conversation_event_bus import ConversationEventBus
        ConversationEventBus.instance().publish_event(
            conversation_id, "vnc_login_ready", {
                "session_id": session_id, "service_id": service_id,
                "token": vnc_token,
                # The Agy dialog namespace: its status/cleanup actions branch
                # back here on the service provider.
                "cli": "agy",
            })

    threading.Thread(target=_bg_setup, daemon=True,
                     name=f"agy-acp-login-{session_id}").start()
    flowfile.set_content(json.dumps({
        "ok": True, "message": f"Starting Antigravity ACP login ({method})...",
        "session_id": session_id,
        "token": vnc_token,
        "cli": "agy",
        "vnc_url": f"/vnc/{session_id}/{vnc_token}/vnc.html",
    }).encode())
    return [flowfile]


def _remove_container(container: str) -> None:
    from core.docker_utils import docker_cmd
    try:
        subprocess.run(docker_cmd() + ["rm", "-f", container],  # nosec B603
                       capture_output=True, timeout=10)
    except Exception:
        logger.debug("[agy-acp-login] container removal failed", exc_info=True)


def _exec_cat(container: str, path: str, timeout: int = 5):
    from core.docker_utils import docker_cmd
    return subprocess.run(  # nosec B603
        docker_cmd() + ["exec", container, "cat", path],
        capture_output=True, text=True, timeout=timeout)


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def antigravity_acp_login_status(user_id, flowfile, *, session_id, session,
                                 service_id, conv_id):
    """Poll the login container; on success store the token files."""
    del conv_id
    from services.vnc_proxy import unregister_session

    if session.get("error"):
        unregister_session(session_id)
        flowfile.set_content(json.dumps({"error": session["error"]}).encode())
        return [flowfile]
    if not session.get("ready"):
        flowfile.set_content(json.dumps({"status": "starting"}).encode())
        return [flowfile]

    container = session["container"]
    launch_time = session.get("launch_time", 0)
    if time.time() - launch_time > LOGIN_TIMEOUT_SECONDS:
        _remove_container(container)
        unregister_session(session_id)
        flowfile.set_content(json.dumps({
            "error": f"Antigravity ACP login timed out ({LOGIN_TIMEOUT_SECONDS // 60} min)"}).encode())
        return [flowfile]

    try:
        result = _exec_cat(container, CONTAINER_RESULT)
        if result.returncode != 0 or not result.stdout.strip():
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]
        outcome = json.loads(result.stdout)
    except Exception:
        flowfile.set_content(json.dumps({"status": "pending"}).encode())
        return [flowfile]

    if not isinstance(outcome, dict) or not outcome.get("ok"):
        _remove_container(container)
        unregister_session(session_id)
        error = str((outcome or {}).get("error") if isinstance(outcome, dict) else "") or "login failed"
        flowfile.set_content(json.dumps({"error": f"Antigravity ACP login failed: {error}"}).encode())
        return [flowfile]

    from core.antigravity_acp_pool import AntigravityAcpPool

    saved = []
    try:
        target_dir = AntigravityAcpPool.home_dir(user_id, service_id) / ".gemini" / "antigravity-acp"
        for name in TOKEN_FILES:
            read = _exec_cat(container, f"{CONTAINER_TOKEN_DIR}/{name}", timeout=10)
            if read.returncode != 0 or not read.stdout.strip():
                continue
            _write_private(target_dir / name, read.stdout)
            saved.append(name)
    except Exception as exc:
        _remove_container(container)
        unregister_session(session_id)
        flowfile.set_content(json.dumps({"error": f"Failed to save the Antigravity ACP token: {exc}"}).encode())
        return [flowfile]

    _remove_container(container)
    unregister_session(session_id)
    if not saved:
        flowfile.set_content(json.dumps({
            "error": "Antigravity ACP login reported success but wrote no token file"}).encode())
        return [flowfile]
    flowfile.set_content(json.dumps({
        "ok": True,
        "message": f"Antigravity ACP login saved ({outcome.get('method', '')})",
        "files": saved,
    }).encode())
    return [flowfile]
