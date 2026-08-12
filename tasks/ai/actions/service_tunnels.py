"""Authenticated webchat actions for user-scoped service tunnels."""

import json

from core import service_tunnel_control
from core import service_tunnels


_ACTIONS = frozenset({
    "service_tunnels_list",
    "service_tunnel_catalog",
    "service_tunnel_catalog_save",
    "service_tunnel_catalog_delete",
    "service_tunnel_create",
    "service_tunnel_start",
    "service_tunnel_stop",
    "service_tunnel_status",
    "service_tunnel_delete",
})
_CREATE_FIELDS = (
    "name", "access_relay", "service_relay", "service_id",
    "bind_host", "bind_port", "persistent",
)
_CATALOG_FIELDS = (
    "service_id", "name", "protocol", "target_host", "target_port",
)


def _reply(flowfile, payload, status=""):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if status:
        flowfile.set_attribute("http.response.status", status)
    return [flowfile]


def _required(body, field):
    value = str(body.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _conversation(body):
    return _required(body, "conversation_id")


def _handle_service_tunnels(self, action, body, store, user_id, flowfile):
    """Handle service-tunnel UI actions using the authenticated owner identity."""
    if action not in _ACTIONS:
        return None

    try:
        if action == "service_tunnels_list":
            return _reply(flowfile, {
                "tunnels": service_tunnels.list_tunnels(user_id)})

        conversation_id = _conversation(body)

        if action == "service_tunnel_catalog":
            services = service_tunnel_control.list_catalog(
                user_id, conversation_id, _required(body, "relay_id"))
            return _reply(flowfile, {"services": services})

        if action == "service_tunnel_catalog_save":
            raw = body.get("service")
            if not isinstance(raw, dict):
                raise ValueError("service is required")
            payload = {key: raw[key] for key in _CATALOG_FIELDS if key in raw}
            saved = service_tunnel_control.save_catalog_service(
                user_id, conversation_id, _required(body, "relay_id"), payload)
            return _reply(flowfile, {"service": saved})

        if action == "service_tunnel_catalog_delete":
            deleted = service_tunnel_control.delete_catalog_service(
                user_id, conversation_id, _required(body, "relay_id"),
                _required(body, "service_id"))
            return _reply(flowfile, {"deleted": deleted})

        if action == "service_tunnel_create":
            payload = {key: body[key] for key in _CREATE_FIELDS if key in body}
            tunnel = service_tunnel_control.create_tunnel(
                user_id, conversation_id, payload)
            return _reply(flowfile, {"tunnel": tunnel})

        tunnel_id = _required(body, "tunnel_id")
        if action == "service_tunnel_start":
            tunnel = service_tunnel_control.start_tunnel(
                user_id, conversation_id, tunnel_id)
            return _reply(flowfile, {"tunnel": tunnel})
        if action == "service_tunnel_stop":
            tunnel = service_tunnel_control.stop_tunnel(
                user_id, conversation_id, tunnel_id)
            return _reply(flowfile, {"tunnel": tunnel})
        if action == "service_tunnel_status":
            tunnel = service_tunnel_control.tunnel_status(
                user_id, conversation_id, tunnel_id)
            return _reply(flowfile, {"tunnel": tunnel})

        deleted = service_tunnel_control.delete_tunnel(
            user_id, conversation_id, tunnel_id)
        return _reply(flowfile, {"deleted": deleted})
    except PermissionError as exc:
        return _reply(flowfile, {"error": str(exc)}, "403")
    except ConnectionError as exc:
        return _reply(flowfile, {"error": str(exc)}, "503")
    except (KeyError, TypeError, ValueError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        return _reply(flowfile, {"error": str(message)}, "400")
    except Exception as exc:
        return _reply(flowfile, {"error": str(exc)}, "500")
