"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _credential_provider_for_service,
    _store_codex_tokens,
    _store_gemini_tokens,
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


def _handle_sf_k8(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k8. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
    if action == "kill_vm":
        container_id = body.get("container_id", "")
        if not container_id:
            flowfile.set_content(json.dumps({"error": "Missing container_id"}).encode())
            return [flowfile]
        from core.docker_utils import docker_rm
        try:
            docker_rm(container_id, force=True)
            flowfile.set_content(json.dumps({"ok": True, "killed": container_id}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Codex login via server (noVNC) ──────────────────────────────
    # Mirror of claude_code_server_login but drives `codex login` (OAuth
    # PKCE against auth.openai.com) inside the same shared image. Each
    # CLI keeps its own action namespace (codex_server_login_*) so the
    # three login flows can evolve separately.

    if action == "codex_server_login":
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
            if _credential_provider_for_service(service_id, user_id) != "codex-app-server":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a codex credential provider"}).encode())
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
            container_name = f"pawflow-codex-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"
            logger.info("[codex-login] Creating session %s (port %d)", session_id, free_port)
            from services.vnc_proxy import register_session
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                container=container_name, service_id=service_id,
                user_id=user_id, volume=volume_name,
                launch_time=time.time(), ready=False,
                host=backend_host)

        except Exception as e:
            logger.error("[codex-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup():
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--tmpfs", "/workspace:rw,size=64m",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    "--entrypoint", "bash",
                    image,
                    "/opt/pawflow/codex_auth_login.sh",
                ]
                logger.info("[codex-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[codex-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[codex-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            container_host = _docker_container_ip(container_name)
            if not _wait_for_vnc_login_backend(session_id, backend_host, free_port, "[codex-login]", container_host):
                return

            _ensure_vnc_routes(flowfile)



            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id, "service_id": service_id,
                    "token": _vnc_token,
                    "cli": "codex",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup, daemon=True, name=f"codex-login-{session_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting codex login container...",
            "session_id": session_id,
            "token": _vnc_token,
            "cli": "codex",
            "vnc_url": f"/vnc/{session_id}/{_vnc_token}/vnc.html",
        }).encode())
        return [flowfile]

    if action == "codex_server_login_cleanup":
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

    if action == "codex_server_login_status":
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
        conv_id = body.get("conversation_id", "") or session.get("conversation_id", "") or ""
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
        launch_time = session.get("launch_time", 0)
        if time.time() - launch_time > 180:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": "Codex login timed out (3 min)"}).encode())
            return [flowfile]

        try:
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "stat -c %Y /home/pawflow/.codex/auth.json 2>/dev/null || stat -c %Y /workspace/auth.json 2>/dev/null"],
                capture_output=True, text=True, timeout=5)
            if stat_result.returncode != 0:
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            file_mtime = int(stat_result.stdout.strip())
            if file_mtime < int(launch_time):
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
        except Exception:
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]

        try:
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.codex/auth.json 2>/dev/null || cat /workspace/auth.json"],
                capture_output=True, text=True, timeout=10)
            from core.llm_providers.codex_session import parse_auth_json
            parsed = parse_auth_json(read_result.stdout)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read codex credentials: {e}"}).encode())
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            return [flowfile]

        access_token = parsed.get("access_token", "")
        refresh_token = parsed.get("refresh_token", "")
        expires_at = parsed.get("expires_at", 0)
        account = parsed.get("account", "")
        id_token = parsed.get("id_token", "")
        if access_token and refresh_token:
            try:
                _store_codex_tokens(
                    service_id, access_token, refresh_token, expires_at,
                    account=account, id_token=id_token, user_id=user_id,
                    conv_id=conv_id)
            except Exception as e:
                logger.warning("Failed to save codex credentials: %s", e)
                _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
                unregister_session(session_id)
                flowfile.set_content(json.dumps({"error": f"Failed to save codex credentials: {e}"}).encode())
                return [flowfile]
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        if not access_token:
            flowfile.set_content(json.dumps({"error": "No access_token in codex auth.json"}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Codex credentials saved!",
        }).encode())
        return [flowfile]

    # ── Gemini-compatible login via server (noVNC) ───────────────────
    # Gemini CLI and Antigravity both produce the Gemini OAuth credential files.
    # They share the same status/cleanup path and encrypted gemini pool.

    if action in {"gemini_server_login", "agy_server_login"}:
        login_cli = "agy" if action == "agy_server_login" else "gemini"
        login_label = "Antigravity" if login_cli == "agy" else "Gemini"
        script_name = "agy_auth_login.sh" if login_cli == "agy" else "gemini_auth_login.sh"
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            sdef = ServiceRegistry.get_instance().resolve_definition(
                service_id, user_id=user_id, conv_id=conversation_id)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            if _credential_provider_for_service(service_id, user_id) != "gemini":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a gemini credential provider"}).encode())
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
            container_name = f"pawflow-{login_cli}-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"
            logger.info("[%s-login] Creating session %s (port %d)", login_cli, session_id, free_port)
            from services.vnc_proxy import register_session
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                container=container_name, service_id=service_id,
                user_id=user_id, volume=volume_name,
                launch_time=time.time(), ready=False,
                host=backend_host)
        except Exception as e:
            logger.error("[%s-login] Setup failed: %s", login_cli, e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup_gemini():
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
                    _project_root, "docker", "claude-code", script_name)
                _script_mount = []
                if _os.path.exists(_script_src):
                    _script_mount = [
                        "-v",
                        f"{_translate_path(_to_host_path(_script_src))}:/opt/pawflow/{script_name}:ro",
                    ]
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--tmpfs", "/workspace:rw,size=64m",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    *_script_mount,
                    "--entrypoint", "bash",
                    image,
                    f"/opt/pawflow/{script_name}",
                ]
                logger.info("[%s-login] Starting container %s on port %d", login_cli, container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[%s-login] Docker failed: %s", login_cli, result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[%s-login] Docker error: %s", login_cli, e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            container_host = _docker_container_ip(container_name)
            if not _wait_for_vnc_login_backend(session_id, backend_host, free_port, f"[{login_cli}-login]", container_host):
                return

            _ensure_vnc_routes(flowfile)


            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id, "service_id": service_id,
                    "token": _vnc_token,
                    "cli": login_cli,
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup_gemini, daemon=True, name=f"{login_cli}-login-{session_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": f"Starting {login_label} login container...",
            "session_id": session_id,
            "token": _vnc_token,
            "cli": login_cli,
            "vnc_url": f"/vnc/{session_id}/{_vnc_token}/vnc.html",
        }).encode())
        return [flowfile]

    if action in {"gemini_server_login_cleanup", "agy_server_login_cleanup"}:
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

    if action in {"gemini_server_login_status", "agy_server_login_status"}:
        login_label = "Antigravity" if action == "agy_server_login_status" else "Gemini"
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
        conv_id = body.get("conversation_id", "") or session.get("conversation_id", "") or ""
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
        launch_time = session.get("launch_time", 0)
        if time.time() - launch_time > 180:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": f"{login_label} login timed out (3 min)"}).encode())
            return [flowfile]

        try:
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "stat -c %Y /home/pawflow/.gemini/oauth_creds.json 2>/dev/null || stat -c %Y /workspace/oauth_creds.json 2>/dev/null"],
                capture_output=True, text=True, timeout=5)
            if stat_result.returncode != 0:
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            file_mtime = int(stat_result.stdout.strip())
            if file_mtime < int(launch_time):
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
        except Exception:
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]

        try:
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.gemini/oauth_creds.json 2>/dev/null || cat /workspace/oauth_creds.json"],
                capture_output=True, text=True, timeout=10)
            from core.llm_providers.gemini_session import parse_oauth_creds_json
            parsed = parse_oauth_creds_json(read_result.stdout)
            # Also try to read google_accounts.json for the account label.
            account = ""
            try:
                acc_result = _sp.run(  # nosec B603
                    _docker_cmd() + ["exec", container, "bash", "-c",
                                      "cat /home/pawflow/.gemini/google_accounts.json 2>/dev/null || cat /workspace/google_accounts.json"],
                    capture_output=True, text=True, timeout=5)
                if acc_result.returncode == 0 and acc_result.stdout.strip():
                    _accs = json.loads(acc_result.stdout)
                    if isinstance(_accs, dict) and _accs:
                        account = next(iter(_accs.keys()), "") or ""
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read gemini credentials: {e}"}).encode())
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            return [flowfile]

        access_token = parsed.get("access_token", "")
        refresh_token = parsed.get("refresh_token", "")
        expires_at = parsed.get("expires_at", 0)
        if access_token and refresh_token:
            try:
                _store_gemini_tokens(
                    service_id, access_token, refresh_token, expires_at,
                    account=account, user_id=user_id, conv_id=conv_id)
            except Exception as e:
                logger.warning("Failed to save gemini credentials: %s", e)
                _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
                unregister_session(session_id)
                flowfile.set_content(json.dumps({"error": f"Failed to save gemini credentials: {e}"}).encode())
                return [flowfile]
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        if not access_token:
            flowfile.set_content(json.dumps({"error": "No access_token in gemini oauth_creds.json"}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps({
            "ok": True, "message": f"{login_label} credentials saved!",
        }).encode())
        return [flowfile]

    # -- Rclone OAuth login via server (noVNC) ------------------------

    return _UNHANDLED
