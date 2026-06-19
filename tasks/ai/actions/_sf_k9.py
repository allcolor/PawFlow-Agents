"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _is_admin,
    _service_scope_id,
    _normalize_service_scope,
    _resolve_service_definition_for_action,
)
from tasks.ai.actions._sf_routes import (
    _docker_published_host,
    _ensure_vnc_routes,
    _wait_for_vnc_login_backend,
    _docker_container_ip,
    _publish_command_result,
)

logger = logging.getLogger(__name__)


def _handle_sf_k9(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k9. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
    if action == "rclone_server_login":
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        scope_arg = body.get("scope", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            sdef = _resolve_service_definition_for_action(
                service_id, user_id, conversation_id, scope_arg)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            if sdef.service_type != "rcloneOAuthCredentials":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not an rclone OAuth credential service"}).encode())
                return [flowfile]
            if sdef.scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global rclone credential login"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            cfg = sdef.config or {}
            rclone_type = str(cfg.get("provider") or "").strip()
            if rclone_type not in {"drive", "onedrive"}:
                flowfile.set_content(json.dumps({
                    "error": "Server login is only available for drive and onedrive rclone OAuth credentials",
                }).encode())
                return [flowfile]
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Cannot verify service: {e}"}).encode())
            return [flowfile]

        try:
            import uuid as _uuid
            from pawflow_relay.utils import find_free_port as _find_free_port
            session_id = _uuid.uuid4().hex[:12]
            free_port = _find_free_port()
            backend_host = _docker_published_host()
            container_name = f"pawflow-rclone-login-{session_id}"
            image = "pawflow-claude-code:latest"
            logger.info("[rclone-login] Creating session %s (port %d)", session_id, free_port)
            from services.vnc_proxy import register_session
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                container=container_name, service_id=service_id,
                service_scope=sdef.scope, service_scope_id=sdef.scope_id,
                rclone_type=rclone_type, user_id=user_id,
                launch_time=time.time(), ready=False,
                host=backend_host)
        except Exception as e:
            logger.error("[rclone-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup_rclone():
            import os as _os
            import subprocess as _sp  # nosec B404
            from core.docker_utils import (
                docker_cmd as _docker_cmd,
                to_host_path as _to_host_path,
                translate_path as _translate_path,
            )
            try:
                _project_root = _os.path.dirname(_os.path.dirname(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
                _script_src = _os.path.join(
                    _project_root, "docker", "claude-code", "rclone_auth_login.sh")
                _script_mount = []
                if _os.path.exists(_script_src):
                    _script_mount = [
                        "-v",
                        f"{_translate_path(_to_host_path(_script_src))}:/opt/pawflow/rclone_auth_login.sh:ro",
                    ]
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    "-e", f"PAWFLOW_RCLONE_TYPE={rclone_type}",
                    *_script_mount,
                    "--entrypoint", "bash",
                    image,
                    "/opt/pawflow/rclone_auth_login.sh",
                ]
                logger.info("[rclone-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[rclone-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[rclone-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            container_host = _docker_container_ip(container_name)
            if not _wait_for_vnc_login_backend(session_id, backend_host, free_port, "[rclone-login]", container_host):
                return

            _ensure_vnc_routes(flowfile)


            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id,
                    "service_id": service_id,
                    "scope": sdef.scope,
                    "token": _vnc_token,
                    "cli": "rclone",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup_rclone, daemon=True, name=f"rclone-login-{session_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting rclone login container...",
        }).encode())
        return [flowfile]

    if action == "rclone_server_login_cleanup":
        session_id = body.get("session_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if session:
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", session.get("container", "")],  # nosec B603
                        capture_output=True, timeout=10)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            unregister_session(session_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "rclone_server_login_status":
        session_id = body.get("session_id", "")
        service_id = body.get("service_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if not session:
            flowfile.set_content(json.dumps({"error": "Unknown session"}).encode())
            return [flowfile]
        service_id = service_id or session.get("service_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        if session.get("error"):
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": session["error"]}).encode())
            return [flowfile]
        if not session.get("ready"):
            flowfile.set_content(json.dumps({"status": "starting"}).encode())
            return [flowfile]

        import subprocess as _sp  # nosec B404
        from core.docker_utils import docker_cmd as _docker_cmd
        container = session["container"]
        try:
            result_dir = "/tmp/pawflow-rclone-login"  # nosec B108 - relay-container rclone login scratch dir.
            error_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c", f"cat {result_dir}/rclone_error.txt 2>/dev/null"],
                capture_output=True, text=True)
            if error_result.returncode == 0 and error_result.stdout.strip():
                flowfile.set_content(json.dumps({"error": error_result.stdout.strip()[:500]}).encode())
                return [flowfile]
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c", f"test -s {result_dir}/rclone_config_body.txt"],
                capture_output=True, text=True)
            if stat_result.returncode != 0:
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c", f"cat {result_dir}/rclone_config_body.txt"],
                capture_output=True, text=True)
            rclone_config = read_result.stdout.strip()
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read rclone config: {e}"}).encode())
            return [flowfile]

        if not rclone_config or "type =" not in rclone_config:
            flowfile.set_content(json.dumps({"error": "Generated rclone config is empty or invalid"}).encode())
            return [flowfile]

        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            service_scope = session.get("service_scope", "") or _normalize_service_scope(body.get("scope", ""))
            service_scope_id = session.get("service_scope_id", "") or _service_scope_id(
                service_scope, user_id, body.get("conversation_id", ""))
            reg.update_config(service_scope, service_scope_id, service_id, {
                "rclone_config": rclone_config,
            })
            try:
                from core.remote_fs_bindings import notify_linked_relays
                notify_linked_relays(session.get("conversation_id", ""), user_id)
            except Exception:
                logger.debug("Remote FS relay notification after rclone login failed", exc_info=True)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to save rclone config: {e}"}).encode())
            return [flowfile]

        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": "Rclone config saved in service.",
        }).encode())
        return [flowfile]

    return _UNHANDLED
