"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _credential_provider_for_service,
    _store_claude_tokens,
    _store_codex_tokens,
    _store_gemini_tokens,
)
from tasks.ai.actions._sf_routes import (
    _docker_published_host,
    _ensure_vnc_routes,
    _wait_for_vnc_login_backend,
    _docker_container_ip,
    _publish_command_result,
)

logger = logging.getLogger(__name__)


def _handle_sf_k3(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k3. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
    if action == "codex_relay_login":
        """Launch `codex login` on a relay — async, result via SSE."""
        service_id = body.get("service_id", "")
        relay_id = body.get("relay_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id or not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id or relay_id"}).encode())
            return [flowfile]
        relay_svc = None
        if hasattr(self, '_services'):
            relay_svc = self._services.get(relay_id)
        if not relay_svc and user_id:
            try:
                from core.service_registry import ServiceRegistry
                relay_svc = ServiceRegistry.get_instance().resolve(
                    relay_id, user_id=user_id, conv_id=conversation_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_svc:
            flowfile.set_content(json.dumps({"error": f"Relay service '{relay_id}' not found"}).encode())
            return [flowfile]

        def _bg_codex_relay_login():
            try:
                logger.info("[codex-relay-login] Starting auth via relay %s", relay_id)
                result = relay_svc._request_with_progress(
                    "codex_auth_login", timeout=300)
            except Exception as e:
                logger.error("[codex-relay-login] Failed: %s", e)
                _publish_command_result(conversation_id, {"error": str(e)})
                return
            if not result or (isinstance(result, dict) and "error" in result):
                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                _publish_command_result(conversation_id, {"error": error})
                return
            credentials = result.get("credentials", {}) if isinstance(result, dict) else {}
            if not credentials:
                _publish_command_result(conversation_id, {"error": "No credentials returned"})
                return
            from core.llm_providers.codex_session import parse_auth_json
            parsed = parse_auth_json(json.dumps(credentials))
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            account = parsed.get("account", "")
            id_token = parsed.get("id_token", "")
            if not access_token:
                _publish_command_result(conversation_id, {"error": "No access_token in codex auth.json"})
                return
            _store_codex_tokens(
                service_id, access_token, refresh_token, expires_at,
                account=account, id_token=id_token, user_id=user_id,
                conv_id=conversation_id)
            logger.info("[codex-relay-login] Credentials saved for %s", service_id)
            _publish_command_result(conversation_id, {
                "ok": True, "message": "Codex credentials saved!"})

        import threading as _threading  # noqa: F811
        _threading.Thread(target=_bg_codex_relay_login, daemon=True,
                           name=f"codex-relay-login-{relay_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Codex login started — authorize in the browser that opens on the relay."
        }).encode())
        return [flowfile]

    # ── Gemini login via relay ─────────────────────────────────
    if action == "gemini_relay_login":
        """Launch interactive `gemini` (OAuth dance) on a relay — async, SSE result."""
        service_id = body.get("service_id", "")
        relay_id = body.get("relay_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id or not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id or relay_id"}).encode())
            return [flowfile]
        relay_svc = None
        if hasattr(self, '_services'):
            relay_svc = self._services.get(relay_id)
        if not relay_svc and user_id:
            try:
                from core.service_registry import ServiceRegistry
                relay_svc = ServiceRegistry.get_instance().resolve(
                    relay_id, user_id=user_id, conv_id=conversation_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_svc:
            flowfile.set_content(json.dumps({"error": f"Relay service '{relay_id}' not found"}).encode())
            return [flowfile]

        def _bg_gemini_relay_login():
            try:
                logger.info("[gemini-relay-login] Starting auth via relay %s", relay_id)
                result = relay_svc._request_with_progress(
                    "gemini_auth_login", timeout=300)
            except Exception as e:
                logger.error("[gemini-relay-login] Failed: %s", e)
                _publish_command_result(conversation_id, {"error": str(e)})
                return
            if not result or (isinstance(result, dict) and "error" in result):
                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                _publish_command_result(conversation_id, {"error": error})
                return
            credentials = result.get("credentials", {}) if isinstance(result, dict) else {}
            accounts = result.get("accounts", {}) if isinstance(result, dict) else {}
            if not credentials:
                _publish_command_result(conversation_id, {"error": "No credentials returned"})
                return
            from core.llm_providers.gemini_session import parse_oauth_creds_json
            parsed = parse_oauth_creds_json(json.dumps(credentials))
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            account = next(iter(accounts.keys()), "") if isinstance(accounts, dict) and accounts else ""
            if not access_token:
                _publish_command_result(conversation_id, {"error": "No access_token in gemini oauth_creds.json"})
                return
            _store_gemini_tokens(
                service_id, access_token, refresh_token, expires_at,
                account=account, user_id=user_id, conv_id=conversation_id)
            logger.info("[gemini-relay-login] Credentials saved for %s", service_id)
            _publish_command_result(conversation_id, {
                "ok": True, "message": "Gemini credentials saved!"})

        import threading as _threading  # noqa: F811
        _threading.Thread(target=_bg_gemini_relay_login, daemon=True,
                           name=f"gemini-relay-login-{relay_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Gemini login started — authorize in the browser that opens on the relay."
        }).encode())
        return [flowfile]

    # ── Claude Code login via server (noVNC) ───────────────────────

    if action == "claude_code_server_login":
        """Spawn a Docker container with Chromium + noVNC for Claude auth.

        Returns {session_id} immediately — Docker setup runs in background.
        Frontend polls claude_code_server_login_status for readiness.
        """
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            if _credential_provider_for_service(service_id, user_id) != "claude-code":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a claude-code credential provider"}).encode())
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
            container_name = f"pawflow-claude-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"

            logger.info("[vnc-login] Creating session %s (port %d)", session_id, free_port)

            # Pre-register session so status endpoint works immediately
            from services.vnc_proxy import register_session
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=getattr(flowfile, "auth_session_id", "") or "",
                container=container_name, service_id=service_id,
                user_id=user_id, volume=volume_name,
                launch_time=time.time(), ready=False,
                host=backend_host)
        except Exception as e:
            logger.error("[vnc-login] Setup failed: %s", e, exc_info=True)
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
                    "/opt/pawflow/auth_login.sh",
                ]
                logger.info("[vnc-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[vnc-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[vnc-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            container_host = _docker_container_ip(container_name)
            # Wait for noVNC to be ready. Different deployments expose Docker
            # login containers either directly on the Docker bridge or through
            # a published host port.
            if not _wait_for_vnc_login_backend(session_id, backend_host, free_port, "[vnc-login]", container_host):
                return

            _ensure_vnc_routes(flowfile)


            # Mark session as ready and notify frontend to open dialog
            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            logger.info("[vnc-login] Session %s ready — notifying frontend", session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id,
                    "service_id": service_id,
                    "token": _vnc_token,
                    "cli": "claude",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup, daemon=True, name=f"vnc-login-{session_id}").start()

        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting login container...",
            "session_id": session_id,
            "token": _vnc_token,
            "cli": "claude",
            "vnc_url": f"/vnc/{session_id}/{_vnc_token}/vnc.html",
        }).encode())
        return [flowfile]

    if action == "claude_code_server_login_cleanup":
        """Cleanup a login container (user closed dialog or timeout)."""
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

    if action == "claude_code_server_login_status":
        """Poll for login completion. Check if credentials file was updated."""
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

        # Background setup error
        if session.get("error"):
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": session["error"]}).encode())
            return [flowfile]

        # Container still starting
        if not session.get("ready"):
            flowfile.set_content(json.dumps({"status": "starting"}).encode())
            return [flowfile]

        import subprocess as _sp  # nosec B404
        from core.docker_utils import docker_cmd as _docker_cmd
        container = session["container"]
        launch_time = session.get("launch_time", 0)

        # Check timeout (2 min max)
        if time.time() - launch_time > 120:
            _sp.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                    capture_output=True, timeout=10)
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": "Login timed out (2 min)"}).encode())
            return [flowfile]

        # Check if .credentials.json was updated since launch
        try:
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "stat -c %Y /home/pawflow/.credentials.json 2>/dev/null || stat -c %Y /workspace/.credentials.json 2>/dev/null"],
                capture_output=True, text=True, timeout=5)
            if stat_result.returncode != 0:
                # File doesn't exist yet
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            file_mtime = int(stat_result.stdout.strip())
            if file_mtime < int(launch_time):
                # File exists but not updated since launch
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
        except Exception:
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]

        # Credentials updated — read them
        try:
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.credentials.json 2>/dev/null || cat /workspace/.credentials.json"],
                capture_output=True, text=True, timeout=10)
            credentials = json.loads(read_result.stdout)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read credentials: {e}"}).encode())
            # Cleanup
            _sp.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                    capture_output=True, timeout=10)
            unregister_session(session_id)
            return [flowfile]

        # Save tokens to service config
        oauth = credentials.get("claudeAiOauth", {})
        access_token = oauth.get("accessToken", "")
        refresh_token = oauth.get("refreshToken", "")
        expires_at = oauth.get("expiresAt", 0)

        import time as _t
        _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
        _remaining = _exp_s - _t.time()
        logger.info("[vnc-login] Credentials from container: token=%s...  expires=%s (%.1fh %s)",
                    access_token[:20] if access_token else "EMPTY",
                    expires_at, _remaining / 3600,
                    "EXPIRED" if _remaining < 0 else "valid")

        if access_token and _remaining > 0:
            try:
                _store_claude_tokens(
                    service_id, access_token, refresh_token, expires_at,
                    user_id=user_id, conv_id=conv_id)
            except Exception as e:
                logger.warning("Failed to save credentials: %s", e)
                _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
                unregister_session(session_id)
                flowfile.set_content(json.dumps({"error": f"Failed to save credentials: {e}"}).encode())
                return [flowfile]
        elif access_token and _remaining <= 0:
            logger.error("[vnc-login] REFUSING to save EXPIRED token (expires_at=%s, %.1fh ago)",
                         expires_at, abs(_remaining) / 3600)
            flowfile.set_content(json.dumps({
                "error": f"Login returned expired token ({abs(_remaining)/3600:.0f}h ago). Try again."
            }).encode())
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            return [flowfile]

        # Cleanup container (volume stays)
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                    capture_output=True, timeout=10)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        # Cleanup VNC proxy routes
        # Routes are shared (/vnc/{session_id}/...) — don't unregister

        if not access_token:
            flowfile.set_content(json.dumps({"error": "No accessToken in credentials"}).encode())
            return [flowfile]

        flowfile.set_content(json.dumps({
            "ok": True,
            "message": "Claude Code credentials saved!",
        }).encode())
        return [flowfile]

    # ── Claude Code set credentials (paste) ──────────────────────────

    if action == "claude_code_login_url":
        """Return instructions for Claude Code login (paste credentials)."""
        flowfile.set_content(json.dumps({
            "flow": "paste_credentials",
            "message": (
                "Run this on your machine:\n\n"
                "  claude auth login\n\n"
                "Then paste the content of:\n\n"
                "  ~/.claude/.credentials.json\n\n"
                "(macOS/Linux) or %USERPROFILE%\\.claude\\.credentials.json (Windows)"
            ),
        }).encode())
        return [flowfile]

    # ── Codex set credentials (paste) ─────────────────────────────

    if action == "codex_login_url":
        flowfile.set_content(json.dumps({
            "flow": "paste_credentials",
            "message": (
                "Run on your machine:\n\n"
                "  codex login\n\n"
                "Then paste the content of:\n\n"
                "  ~/.codex/auth.json\n\n"
                "(macOS/Linux) or %USERPROFILE%\\.codex\\auth.json (Windows)"
            ),
        }).encode())
        return [flowfile]

    if action in ("codex_login_code", "codex_auth"):
        service_id = body.get("service_id", "")
        credentials_json = body.get("credentials", "").strip()
        if not service_id or not credentials_json:
            flowfile.set_content(json.dumps({"error": "Missing service_id or credentials"}).encode())
            return [flowfile]
        try:
            from core.llm_providers.codex_session import parse_auth_json
            parsed = parse_auth_json(credentials_json)
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            account = parsed.get("account", "")
            id_token = parsed.get("id_token", "")
            if not access_token:
                flowfile.set_content(json.dumps({
                    "error": (
                        "Invalid credentials: no access_token found. "
                        "Expected format: {\"tokens\": {\"access_token\": \"...\", \"refresh_token\": \"...\"}}"
                    ),
                }).encode())
                return [flowfile]
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            sdef = greg.get_definition("global", "", service_id)
            _stored = False
            if sdef:
                _roles = flowfile.get_attribute("http.auth.roles") or ""
                if action == "codex_auth" and "admin" not in _roles:
                    flowfile.set_content(json.dumps({
                        "error": f"Admin permission required for global service '{service_id}'"
                    }).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                if _credential_provider_for_service(service_id, user_id) != "codex-app-server":
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{service_id}' is not a codex credential provider"
                    }).encode())
                    return [flowfile]
                _store_codex_tokens(
                    service_id, access_token, refresh_token, expires_at,
                    account=account, id_token=id_token, user_id=user_id,
                    conv_id=body.get("conversation_id", ""))
                _stored = True
            if not _stored:
                try:
                    usdef = greg.get_definition("user", user_id, service_id)
                    if usdef:
                        if _credential_provider_for_service(service_id, user_id) != "codex-app-server":
                            flowfile.set_content(json.dumps({
                                "error": f"Service '{service_id}' is not a codex credential provider"
                            }).encode())
                            return [flowfile]
                        _store_codex_tokens(
                            service_id, access_token, refresh_token, expires_at,
                            account=account, id_token=id_token, user_id=user_id,
                            conv_id=body.get("conversation_id", ""))
                        _stored = True
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not _stored:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Codex credentials saved for '{service_id}'",
            }).encode())
        except json.JSONDecodeError:
            flowfile.set_content(json.dumps({"error": "Invalid JSON. Paste the raw content of ~/.codex/auth.json"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Gemini set credentials (paste) ────────────────────────────

    return _UNHANDLED
