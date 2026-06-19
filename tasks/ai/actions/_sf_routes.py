"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
from typing import List

from core import FlowFile

logger = logging.getLogger(__name__)


def _ws_upgrade_required(req) -> None:
    """Reject plain HTTP requests sent to WebSocket-only routes."""
    req.complete(
        426,
        {
            "Content-Type": "application/json",
            "Connection": "close",
        },
        b'{"error": "WebSocket upgrade required"}',
    )


def _docker_published_host() -> str:
    """Host address this container can use to reach Docker-published ports."""
    import os as _os
    import socket as _socket
    override = _os.environ.get("PAWFLOW_DOCKER_PUBLISHED_HOST", "").strip()
    if override:
        return override
    if _os.path.exists("/.dockerenv"):
        try:
            with open("/proc/net/route", "r", encoding="utf-8") as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "00000000":
                        gateway = int(parts[2], 16).to_bytes(4, "little")
                        return _socket.inet_ntoa(gateway)
        except Exception:
            logger.debug("Docker gateway lookup failed", exc_info=True)
        try:
            return _socket.gethostbyname("host.docker.internal")
        except Exception:
            logger.debug("host.docker.internal lookup failed", exc_info=True)
    return "127.0.0.1"


def _ensure_vnc_routes(flowfile: FlowFile) -> None:
    """Ensure /vnc/ and /audio/ routes exist on the request's HTTP listener."""
    _req_port = flowfile.get_attribute("http.listener.port") or ""
    if not _req_port:
        logger.warning("[vnc] No http.listener.port on flowfile — cannot target listener")
        return
    try:
        from services.vnc_proxy import vnc_ws_proxy, vnc_http_proxy
        from services.audio_proxy import audio_ws_proxy
        from services.http_listener_service import _instances
        _http_svc = _instances.get(int(_req_port))
        if not _http_svc:
            logger.warning("[vnc] No live listener on port %s (instances: %s)",
                           _req_port, list(_instances.keys()))
            return
        _vnc_owner = "_vnc_proxy"
        _http_svc.register_route("GET", "/vnc/{session_id}/{token}/websockify",
                                 _vnc_owner, callback=_ws_upgrade_required,
                                 ws_handler=vnc_ws_proxy, public=True)
        _http_svc.register_route("GET", "/vnc/{session_id}/{token}/{path+}",
                                 _vnc_owner, callback=vnc_http_proxy, public=True)
        logger.info("[vnc] Registered VNC routes on port %s", _req_port)
        _audio_exists = [r for r in _http_svc.get_routes()
                         if r.get("pattern", "").startswith("/audio/")]
        if not _audio_exists:
            _http_svc.register_route("GET", "/audio/{session_id}/{token}/stream",
                                     _vnc_owner, callback=_ws_upgrade_required,
                                     ws_handler=audio_ws_proxy, public=True)
    except Exception as e:
        logger.warning("[vnc] Route registration failed: %s", e)


def _novnc_backend_http_ready(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True when the server can fetch noVNC HTML from a backend."""
    import socket
    try:
        port = int(port or 0)
    except Exception:
        return False
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(
                b"GET /vnc.html HTTP/1.1\r\n"
                b"Host: novnc\r\n"
                b"Connection: close\r\n\r\n")
            resp = sock.recv(128)
        status = resp.split(b"\r\n", 1)[0]
        return b" 200 " in status or b" 301 " in status or b" 302 " in status
    except Exception:
        return False


def _vnc_login_host_candidates(preferred_host: str) -> List[str]:
    """Return backend hosts to try for Docker-published login noVNC ports."""
    import socket as _socket
    candidates = [preferred_host, "127.0.0.1", "localhost"]
    try:
        candidates.append(_socket.gethostbyname("host.docker.internal"))
    except Exception:
        logger.debug("host.docker.internal lookup failed", exc_info=True)
    seen = set()
    out = []
    for host in candidates:
        host = str(host or "").strip()
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


def _wait_for_vnc_login_backend(session_id: str, preferred_host: str, port: int, log_prefix: str,
                                container_host: str = "", container_port: int = 6080) -> str:
    """Find the reachable noVNC backend for a login container and store it."""
    last_ready_error = "not checked"
    targets = []
    if container_host:
        targets.append((container_host, int(container_port or 6080)))
    targets.extend((host, int(port or 0)) for host in _vnc_login_host_candidates(preferred_host))
    seen_targets = set()
    targets = [target for target in targets if target not in seen_targets and not seen_targets.add(target)]
    for _attempt in range(15):
        for host, target_port in targets:
            try:
                if _novnc_backend_http_ready(host, target_port, timeout=0.75):
                    from services.vnc_proxy import update_session_target
                    update_session_target(session_id, host, target_port)
                    logger.info("%s noVNC ready on %s:%d", log_prefix, host, target_port)
                    return host
                last_ready_error = f"no HTTP response from {host}:{target_port}"
            except Exception as e:
                last_ready_error = f"{host}:{target_port}: {e}"
        time.sleep(1)
    from services.vnc_proxy import update_session_error
    update_session_error(session_id, f"noVNC not reachable on any backend target: {last_ready_error}")
    return ""


def _docker_container_ip(container_name: str) -> str:
    import subprocess as _sp  # nosec B404
    try:
        from core.docker_utils import docker_cmd as _docker_cmd
        result = _sp.run(
            _docker_cmd() + ["inspect", "-f", "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container_name],
            capture_output=True, text=True, timeout=5)  # nosec B603
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0].strip()
    except Exception:
        logger.debug("Docker container IP lookup failed for %s", container_name, exc_info=True)
    return ""


def _ensure_terminal_routes(flowfile: FlowFile) -> None:
    """Ensure /terminal/ routes exist on the request's HTTP listener."""
    _req_port = flowfile.get_attribute("http.listener.port") or ""
    if not _req_port:
        logger.warning("[terminal] No http.listener.port on flowfile — cannot target listener")
        return
    try:
        from services.terminal_proxy import terminal_ws_handler
        from services.http_listener_service import _instances
        _http_svc = _instances.get(int(_req_port))
        if not _http_svc:
            logger.warning("[terminal] No live listener on port %s (instances: %s)",
                           _req_port, list(_instances.keys()))
            return
        _owner = "_terminal_proxy"
        _exists = [r for r in _http_svc.get_routes() if r.get("owner") == _owner]
        if not _exists:
            _http_svc.register_route(
                "GET", "/terminal/{session_id}/{token}",
                _owner,
                callback=_ws_upgrade_required,
                ws_handler=terminal_ws_handler,
                public=True,
            )
            logger.info("[terminal] Registered terminal routes on port %s", _req_port)
    except Exception as e:
        logger.warning("[terminal] Route registration failed: %s", e)


def _ensure_code_server_routes(flowfile: FlowFile) -> None:
    """Ensure /code/ routes exist on the request's HTTP listener."""
    _req_port = flowfile.get_attribute("http.listener.port") or ""
    try:
        from services.code_server_proxy import code_http_proxy, code_ws_proxy
        from services.http_listener_service import _instances
        targets = []
        if _req_port:
            try:
                _http_svc = _instances.get(int(_req_port))
            except (TypeError, ValueError):
                _http_svc = None
            if _http_svc:
                targets.append((int(_req_port), _http_svc))
            else:
                logger.warning("[code-server] No live listener on port %s (instances: %s); registering on all listeners",
                               _req_port, list(_instances.keys()))
        if not targets:
            targets = list(_instances.items())
        if not targets:
            logger.warning("[code-server] No live HTTP listeners; cannot register /code routes")
            return
        for _port, _http_svc in targets:
            _owner = "_code_server_proxy"
            existing = {
                (r.get("method"), r.get("pattern"))
                for r in _http_svc.get_routes()
                if r.get("owner") == _owner
            }

            def _register_missing(method, pattern, callback, ws_handler=None):
                if (method, pattern) in existing:
                    return
                _http_svc.register_route(
                    method, pattern, _owner,
                    callback=callback,
                    ws_handler=ws_handler,
                    public=True,
                )
                existing.add((method, pattern))

            _register_missing(
                "GET", "/code/{session_id}/{token}/",
                code_http_proxy, ws_handler=code_ws_proxy)
            _register_missing(
                "GET", "/code/{session_id}/{token}/{path+}",
                code_http_proxy, ws_handler=code_ws_proxy)
            for _method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
                _register_missing(
                    _method, "/code/{session_id}/{token}/", code_http_proxy)
                _register_missing(
                    _method, "/code/{session_id}/{token}/{path+}", code_http_proxy)
            logger.info("[code-server] Registered code-server routes on port %s", _port)
    except Exception as e:
        logger.warning("[code-server] Route registration failed: %s", e)


def _publish_command_result(conversation_id: str, result: dict):
    """Publish a command result via SSE (background thread → frontend)."""
    from core.conversation_event_bus import ConversationEventBus
    bus = ConversationEventBus.instance()
    if "error" in result:
        bus.publish_event(conversation_id, "command_result", {"error": result["error"]})
    else:
        bus.publish_event(conversation_id, "command_result",
                          {"result": json.dumps(result, ensure_ascii=False)})


def _notify_remote_mounts_after_service_change(sdef, conversation_id: str, user_id: str) -> None:
    if not conversation_id or not user_id:
        return
    if getattr(sdef, "service_type", "") not in {"rcloneFilesystem", "rcloneOAuthCredentials"}:
        return
    try:
        from core.remote_fs_bindings import notify_linked_relays
        notify_linked_relays(conversation_id, user_id)
    except Exception:
        logger.debug("Remote FS relay notification after service update failed", exc_info=True)
