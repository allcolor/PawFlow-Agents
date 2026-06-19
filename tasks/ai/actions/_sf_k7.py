"""AgentLoopTask actions  - service flow"""

import json
import logging

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _find_http_listener,
)
from tasks.ai.actions._sf_routes import (
    _ensure_vnc_routes,
    _novnc_backend_http_ready,
)

logger = logging.getLogger(__name__)


def _handle_sf_k7(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k7. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
    if action == "close_code_server":
        relay_id = body.get("relay_id", "")
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if svc:
                svc._request("stop_code_server")
            # Unregister proxy session (routes stay for other relays)
            try:
                from services.code_server_proxy import unregister_code_server
                unregister_code_server(relay_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "open_desktop":
        relay_id = body.get("relay_id", "")
        local_screen = body.get("local_screen", False)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]

            _action_start = "start_local_desktop" if local_screen else "start_desktop"
            _action_status_key = "local_screen_running" if local_screen else "running"
            _session_prefix = "local_desktop" if local_screen else "desktop"

            # Check if already running (idempotent)
            status = svc._request("desktop_status")
            logger.info("[open_desktop] desktop_status for %s: %s (key=%s)", relay_id, status, _action_status_key)
            if isinstance(status, dict) and status.get(_action_status_key):
                _login_sid = flowfile.get_attribute("auth.session_id") or ""
                if local_screen:
                    _novnc_port = status.get("local_screen_novnc_port")
                    if _novnc_port:
                        _relay_addr = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                        if not _novnc_backend_http_ready(_relay_addr, _novnc_port):
                            logger.warning(
                                "[open_desktop] already-running local noVNC is not reachable at %s:%s; restarting %s",
                                _relay_addr, _novnc_port, relay_id)
                            try:
                                svc._request("stop_local_desktop")
                            except Exception:
                                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                            _novnc_port = 0
                    if _novnc_port:
                        _sid = f"{_session_prefix}_{relay_id}"
                        from services.vnc_proxy import register_session
                        _vtok = register_session(
                            _sid, _novnc_port,
                            owner_user_id=user_id,
                            login_session_id=_login_sid)
                        _ensure_vnc_routes(flowfile)
                        # Re-register audio for already-running desktop
                        _audio_token = ""  # nosec B105
                        try:
                            from services.audio_proxy import register_audio_source
                            _audio_port = status.get("local_screen_audio_port")
                            if _audio_port:
                                _audio_token = register_audio_source(_sid, _relay_addr, _audio_port,
                                                                     owner_user_id=user_id,
                                                                     login_session_id=_login_sid)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        flowfile.set_content(json.dumps({
                            "ok": True, "already_running": True, "local_screen": True,
                            "relay_id": relay_id,
                            "url": f"/vnc/{_sid}/{_vtok}/vnc.html?autoconnect=true&resize=scale&path=vnc/{_sid}/{_vtok}/websockify",
                            "audio_session": _sid if _audio_token else "",
                            "audio_token": _audio_token,
                        }).encode())
                        return [flowfile]
                else:
                    _backend_host, _backend_port = _server_relay_proxy_target(relay_id, 6080)
                    logger.info("[open_desktop] already running, backend=%s:%s for %s",
                                _backend_host, _backend_port, relay_id)
                    if not _backend_port and status.get("novnc_port"):
                        _backend_host = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                        _backend_port = status.get("novnc_port")
                    if _backend_port and not _novnc_backend_http_ready(_backend_host, _backend_port):
                        logger.warning(
                            "[open_desktop] already-running noVNC is not reachable at %s:%s; restarting %s",
                            _backend_host, _backend_port, relay_id)
                        try:
                            svc._request("stop_desktop")
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        _backend_port = 0
                    if _backend_port:
                        _sid = f"{_session_prefix}_{relay_id}"
                        from services.vnc_proxy import register_session
                        _vtok = register_session(
                            _sid, _backend_port,
                            owner_user_id=user_id,
                            login_session_id=_login_sid,
                            host=_backend_host)
                        _ensure_vnc_routes(flowfile)
                        # Re-register audio for already-running desktop
                        _audio_token = ""  # nosec B105
                        try:
                            from services.audio_proxy import register_audio_source
                            _audio_host, _audio_port = _server_relay_proxy_target(relay_id, 6180)
                            if not _audio_port and status.get("audio_port"):
                                _audio_host = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                                _audio_port = status.get("audio_port")
                            if _audio_port:
                                _audio_token = register_audio_source(_sid, _audio_host, _audio_port,
                                                                     owner_user_id=user_id,
                                                                     login_session_id=_login_sid)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        flowfile.set_content(json.dumps({
                            "ok": True, "already_running": True,
                            "relay_id": relay_id,
                            "url": f"/vnc/{_sid}/{_vtok}/vnc.html?autoconnect=true&resize=scale&path=vnc/{_sid}/{_vtok}/websockify",
                            "audio_session": _sid if _audio_token else "",
                            "audio_token": _audio_token,
                        }).encode())
                        return [flowfile]

            logger.info("[open_desktop] Starting %s on relay %s", _action_start, relay_id)
            result = svc._request(_action_start)
            logger.debug("[open_desktop] %s result: %s", _action_start, result)
            # _request() unwraps the relay response — result is the inner data dict directly
            novnc_port = result.get("novnc_port") if isinstance(result, dict) else None
            if not novnc_port:
                flowfile.set_content(json.dumps({"error": f"Failed to start {_action_start}", "detail": str(result)}).encode())
                return [flowfile]

            _login_sid = flowfile.get_attribute("auth.session_id") or ""
            if local_screen:
                # Local screen: the relay runs VNC+websockify on its own machine.
                # The novnc_port is directly on the relay's host (not in Docker).
                # Use the relay's address to proxy.
                _relay_addr = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                host_port = novnc_port
                # For local relays connecting from the same machine, use the port directly
                session_id = f"{_session_prefix}_{relay_id}"
                from services.vnc_proxy import register_session
                _vtok = register_session(
                    session_id, host_port,
                    owner_user_id=user_id,
                    login_session_id=_login_sid,
                    host=_relay_addr)
            else:
                # Managed relay containers are reached over Docker networking;
                # remote host relays expose the returned noVNC port on their
                # relay address.
                backend_host, backend_port = _server_relay_proxy_target(relay_id, 6080)
                if not backend_port:
                    backend_host = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                    backend_port = novnc_port
                session_id = f"{_session_prefix}_{relay_id}"
                from services.vnc_proxy import register_session
                _vtok = register_session(
                    session_id, backend_port,
                    owner_user_id=user_id,
                    login_session_id=_login_sid,
                    host=backend_host)

            _ensure_vnc_routes(flowfile)

            # Register audio source if available
            _audio_token = ""  # nosec B105
            try:
                from services.audio_proxy import register_audio_source
                if local_screen:
                    # Local relay: audio_capture runs on relay host
                    _audio_port = result.get("audio_port") if isinstance(result, dict) else None
                    if _audio_port:
                        _audio_token = register_audio_source(session_id, _relay_addr, _audio_port,
                                                             owner_user_id=user_id,
                                                             login_session_id=_login_sid)
                else:
                    _audio_host, _audio_port = _server_relay_proxy_target(relay_id, 6180)
                    if not _audio_port and isinstance(result, dict) and result.get("audio_port"):
                        _audio_host = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                        _audio_port = result.get("audio_port")
                    if _audio_port:
                        _audio_token = register_audio_source(session_id, _audio_host, _audio_port,
                                                             owner_user_id=user_id,
                                                             login_session_id=_login_sid)
            except Exception as _ae:
                logger.debug("[open_desktop] Audio registration skipped: %s", _ae)

            flowfile.set_content(json.dumps({
                "ok": True, "relay_id": relay_id, "local_screen": local_screen,
                "url": f"/vnc/{session_id}/{_vtok}/vnc.html?autoconnect=true&resize=scale&path=vnc/{session_id}/{_vtok}/websockify",
                "audio_session": session_id if _audio_token else "",
                "audio_token": _audio_token,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "close_desktop":
        relay_id = body.get("relay_id", "")
        local_screen = body.get("local_screen", False)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if svc:
                svc._request("stop_local_desktop" if local_screen else "stop_desktop")
            from services.vnc_proxy import unregister_session
            _prefix = "local_desktop" if local_screen else "desktop"
            _session_id = f"{_prefix}_{relay_id}"
            unregister_session(_session_id)
            try:
                from services.audio_proxy import unregister_audio_source
                unregister_audio_source(_session_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Port forwarding ─────────────────────────────────────────────

    if action == "port_forward_add":
        relay_id = body.get("relay_id", "")
        int_port = body.get("port", 0) or body.get("int_port", 0)
        ext_port = body.get("ext_port", 0) or int_port
        if not relay_id or not int_port:
            flowfile.set_content(json.dumps({"error": "Missing relay_id or port"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            int_port = int(int_port)
            ext_port = int(ext_port)
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]

            from services.port_forward_proxy import add_forward, fwd_http_proxy, fwd_root_redirect, _ROUTE_OWNER
            _ttl = int(body.get("ttl_seconds", 28800)) or 28800
            first, _fwd_id, _fwd_token = add_forward(
                relay_id, int_port, svc, ext_port=ext_port,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                ttl_seconds=_ttl,
                description=body.get("description", "") or "")

            # Register generic routes once (shared by all forwards).
            # The root pattern (trailing slash, no `{path+}`) is needed
            # because `{path+}` requires at least one segment, so the
            # exact URL we hand to the user (`/fwd/<fid>/<tok>/`) would
            # otherwise 404. fwd_root_redirect on the no-slash variant
            # nudges browsers that drop the trailing slash.
            if first:
                http_svc = _find_http_listener()
                if http_svc:
                    for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                        http_svc.register_route(method, "/fwd/{forward_id}/{token}/",
                                                _ROUTE_OWNER, callback=fwd_http_proxy)
                        http_svc.register_route(method, "/fwd/{forward_id}/{token}/{path+}",
                                                _ROUTE_OWNER, callback=fwd_http_proxy)
                    http_svc.register_route("GET", "/fwd/{forward_id}/{token}",
                                            _ROUTE_OWNER, callback=fwd_root_redirect)

            _url = f"/fwd/{_fwd_id}/{_fwd_token}/"
            flowfile.set_content(json.dumps({
                "ok": True, "relay_id": relay_id,
                "forward_id": _fwd_id, "token": _fwd_token,
                "int_port": int_port, "ext_port": ext_port, "url": _url,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "port_forward_remove":
        forward_id = body.get("forward_id", "") or ""
        if not forward_id:
            flowfile.set_content(json.dumps({
                "error": "Missing forward_id",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from services.port_forward_proxy import remove_forward, _ROUTE_OWNER
            last = remove_forward(forward_id=forward_id)
            if last:
                http_svc = _find_http_listener()
                if http_svc:
                    http_svc.unregister_routes(_ROUTE_OWNER)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "port_forward_list":
        from services.port_forward_proxy import list_forwards
        flowfile.set_content(json.dumps({"forwards": list_forwards()}).encode())
        return [flowfile]

    # ── Private gateway admin ────────────────────────────────────────


    if action == "private_gateway_list_bans":
        try:
            svc = _private_gateway_for_body()
            if svc is not None:
                bans = svc.list_bans()
            else:
                from services.private_gateway import list_bans
                bans = list_bans()
            flowfile.set_content(json.dumps({"bans": bans}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "private_gateway_unban":
        ip = body.get("ip", "")
        if not ip:
            flowfile.set_content(json.dumps({"error": "Missing ip"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            svc = _private_gateway_for_body()
            if svc is not None:
                was_banned = svc.unban_ip(ip)
            else:
                from services.private_gateway import unban_ip
                was_banned = unban_ip(ip)
            flowfile.set_content(json.dumps({"ok": True, "was_banned": was_banned}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "private_gateway_status":
        try:
            svc = _private_gateway_for_body()
            if svc is not None:
                enabled = svc.is_enabled()
                bans = svc.list_bans()
            else:
                from services.private_gateway import PrivateGateway, list_bans
                enabled = PrivateGateway.is_enabled_static()
                bans = list_bans()
            flowfile.set_content(json.dumps({
                "enabled": enabled,
                "banned_count": len(bans),
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Docker VM management ──────────────────────────────────────

    if action == "list_vms":
        from core.docker_utils import list_containers, get_server_id
        owner = body.get("owner", "")  # empty = all pf-* containers
        containers = list_containers(owner)
        _srv_id = get_server_id()
        # Enrich with ownership info
        for c in containers:
            name = c.get("name", "")
            if _srv_id and f"pf-{_srv_id[:12]}" in name.replace(".", "-").replace("_", "-"):
                c["owner"] = "server"
            else:
                c["owner"] = "client"
        flowfile.set_content(json.dumps({"vms": containers}).encode())
        return [flowfile]

    return _UNHANDLED
