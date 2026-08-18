"""PawFlow control-plane orchestration for relay service tunnels."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from core import service_tunnels


logger = logging.getLogger(__name__)

_ENV_KEYS = (
    "PAWFLOW_FRPS_SERVER",
    "PAWFLOW_FRPS_PORT",
    "PAWFLOW_FRPS_TOKEN",
    "PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY",
)


def settings() -> Dict[str, Any]:
    values = {key: str(os.environ.get(key) or "").strip() for key in _ENV_KEYS}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError("Service tunnels are not configured: missing " + ", ".join(missing))
    try:
        port = int(values["PAWFLOW_FRPS_PORT"])
    except ValueError as exc:
        raise ValueError("PAWFLOW_FRPS_PORT must be a TCP/UDP port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("PAWFLOW_FRPS_PORT must be between 1 and 65535")
    return {
        "server": values["PAWFLOW_FRPS_SERVER"],
        "port": port,
        "token": values["PAWFLOW_FRPS_TOKEN"],
        "signing_key": values["PAWFLOW_SERVICE_TUNNEL_SIGNING_KEY"],
    }


def _resolve_relay(user_id: str, conversation_id: str, relay_id: str):
    from core.service_registry import ServiceRegistry
    relay = ServiceRegistry.get_instance().resolve(
        relay_id, user_id=user_id, conv_id=conversation_id)
    if relay is None or getattr(relay, "TYPE", "") != "relay":
        raise ValueError(f"Relay '{relay_id}' not found")
    if not relay.is_connected():
        raise ConnectionError(f"Relay '{relay_id}' is not connected")
    info = getattr(relay, "_relay_info", {}) or {}
    if not info.get("allow_service_tunnels", False):
        raise PermissionError(
            f"Service tunnels are disabled on relay '{relay_id}'")
    return relay


def _relay_request(relay, action: str, target_relay_id: str, **payload):
    info = getattr(relay, "_relay_info", {}) or {}
    message = {"relay_id": target_relay_id}
    message.update(payload)
    return relay._request(
        action, ".",
        local=bool(info.get("service_tunnels_local", False)),
        _request_timeout=20, _retry_on_disconnect=False, **message)


def list_catalog(user_id: str, conversation_id: str, relay_id: str):
    relay = _resolve_relay(user_id, conversation_id, relay_id)
    result = _relay_request(relay, "service_tunnel_catalog", relay_id)
    return list((result or {}).get("services") or [])


def save_catalog_service(user_id: str, conversation_id: str, relay_id: str,
                         payload: Dict[str, Any]):
    relay = _resolve_relay(user_id, conversation_id, relay_id)
    result = _relay_request(
        relay, "service_tunnel_catalog_save", relay_id, service=payload)
    return (result or {}).get("service") or {}


def delete_catalog_service(user_id: str, conversation_id: str, relay_id: str,
                           service_id: str) -> bool:
    relay = _resolve_relay(user_id, conversation_id, relay_id)
    result = _relay_request(
        relay, "service_tunnel_catalog_delete", relay_id,
        service_id=service_id)
    return bool((result or {}).get("deleted"))


def _roles(user_id: str, conversation_id: str, tunnel: Dict[str, Any]):
    return (
        (_resolve_relay(user_id, conversation_id, tunnel["service_relay"]),
         "service", tunnel["service_relay"]),
        (_resolve_relay(user_id, conversation_id, tunnel["access_relay"]),
         "access", tunnel["access_relay"]),
    )


def _message(config: Dict[str, Any], tunnel: Dict[str, Any], role: str,
             relay_id: str, user_id: str) -> Dict[str, Any]:
    grant = service_tunnels.issue_grant(
        config["signing_key"], tunnel_id=tunnel["tunnel_id"],
        relay_id=relay_id, user_id=user_id,
        server_name=tunnel["server_name"], role=role,
        ttl_seconds=3600)
    return {
        "tunnel_id": tunnel["tunnel_id"],
        "role": role,
        "relay_id": relay_id,
        "server_name": tunnel["server_name"],
        "frps_server": config["server"],
        "frps_port": config["port"],
        "frps_token": config["token"],
        "grant": grant,
        "secret_key": tunnel["secret_key"],
        "transport": "quic",
        "service_id": tunnel["service_id"],
        "bind_host": tunnel["bind_host"],
        "bind_port": tunnel["bind_port"],
    }


def start_tunnel(user_id: str, conversation_id: str,
                 tunnel_id: str) -> Dict[str, Any]:
    tunnel = service_tunnels.get_tunnel(
        user_id, tunnel_id, include_secrets=True)
    service_tunnels.update_tunnel(user_id, tunnel_id, {
        "enabled": True, "status": "pending", "error": ""})
    service_started = False
    service_role = None
    try:
        config = settings()
        service_role, access_role = _roles(
            user_id, conversation_id, tunnel)
        _relay_request(
            service_role[0], "service_tunnel_apply", service_role[2],
            **_message(config, tunnel, service_role[1], service_role[2], user_id))
        service_started = True
        _relay_request(
            access_role[0], "service_tunnel_apply", access_role[2],
            **_message(config, tunnel, access_role[1], access_role[2], user_id))
    except Exception as exc:
        if service_started and service_role is not None:
            try:
                _relay_request(
                    service_role[0], "service_tunnel_stop", service_role[2],
                    tunnel_id=tunnel_id, role="service")
            except Exception:
                logger.debug(
                    "Service Tunnel rollback failed for %s",
                    tunnel_id, exc_info=True)
        service_tunnels.update_tunnel(user_id, tunnel_id, {
            "status": "error", "error": str(exc), "connection_mode": "relay"})
        raise
    return service_tunnels.update_tunnel(user_id, tunnel_id, {
        "enabled": True, "status": "connected",
        "error": "", "connection_mode": "relay"})


def create_tunnel(user_id: str, conversation_id: str,
                  payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload or {})
    payload["conversation_id"] = conversation_id
    access_relay = str(payload.get("access_relay") or "").strip()
    service_relay = str(payload.get("service_relay") or "").strip()
    _resolve_relay(user_id, conversation_id, access_relay)
    services = list_catalog(user_id, conversation_id, service_relay)
    service_id = str(payload.get("service_id") or "").strip()
    from core.identifier import identifiers_equal
    authorized = next(
        (entry for entry in services
         if identifiers_equal(entry.get("service_id"), service_id)), None)
    if authorized is None:
        raise KeyError(
            f"Service '{service_id}' is not approved on relay '{service_relay}'")
    service_id = str(authorized.get("service_id") or service_id)
    payload["service_id"] = service_id
    bind_port = int(payload.get("bind_port") or 0)
    for existing in service_tunnels.list_tunnels(user_id):
        if (existing.get("access_relay") == access_relay
                and int(existing.get("bind_port") or 0) == bind_port):
            raise ValueError(
                f"Listener {access_relay}:127.0.0.1:{bind_port} is already used")
    created = service_tunnels.create_tunnel(user_id, payload, authorized)
    return start_tunnel(user_id, conversation_id, created["tunnel_id"])


def stop_tunnel(user_id: str, conversation_id: str,
                tunnel_id: str) -> Dict[str, Any]:
    tunnel = service_tunnels.get_tunnel(
        user_id, tunnel_id, include_secrets=True)
    service_tunnels.update_tunnel(user_id, tunnel_id, {
        "enabled": False, "status": "stopping", "error": ""})
    errors = []
    for role, relay_id in (
            ("service", tunnel["service_relay"]),
            ("access", tunnel["access_relay"])):
        try:
            relay = _resolve_relay(user_id, conversation_id, relay_id)
            _relay_request(relay, "service_tunnel_stop", relay_id,
                           tunnel_id=tunnel_id, role=role)
        except Exception as exc:
            errors.append(str(exc))
    return service_tunnels.update_tunnel(user_id, tunnel_id, {
        "enabled": False,
        "status": "error" if errors else "stopped",
        "error": "; ".join(errors), "connection_mode": "relay"})


def delete_tunnel(user_id: str, conversation_id: str, tunnel_id: str) -> bool:
    try:
        stop_tunnel(user_id, conversation_id, tunnel_id)
    except Exception:
        logger.debug(
            "Service Tunnel stop failed during deletion for %s",
            tunnel_id, exc_info=True)
    return service_tunnels.delete_tunnel(user_id, tunnel_id)


def tunnel_status(user_id: str, conversation_id: str,
                  tunnel_id: str) -> Dict[str, Any]:
    tunnel = service_tunnels.get_tunnel(
        user_id, tunnel_id, include_secrets=True)
    role_status = {}
    errors = []
    for relay, role, relay_id in _roles(user_id, conversation_id, tunnel):
        try:
            role_status[role] = _relay_request(
                relay, "service_tunnel_status", relay_id,
                tunnel_id=tunnel_id, role=role)
        except Exception as exc:
            role_status[role] = {"running": False, "error": str(exc)}
            errors.append(str(exc))
    running = all(bool(role_status.get(role, {}).get("running"))
                  for role in ("service", "access"))
    public = service_tunnels.update_tunnel(user_id, tunnel_id, {
        "status": "connected" if running else "disconnected",
        "error": "; ".join(errors), "connection_mode": "relay"})
    public["roles"] = role_status
    return public


def reconcile_for_relay(relay_id: str | None = None) -> None:
    """Refresh every eligible tunnel, optionally limited to one relay."""
    for tunnel in service_tunnels.list_all_tunnels(include_secrets=True):
        if not tunnel.get("persistent") or not tunnel.get("enabled"):
            continue
        if (relay_id is not None
                and relay_id not in {
                    tunnel.get("access_relay"), tunnel.get("service_relay")}):
            continue
        user_id = str(tunnel.get("_owner_id") or "")
        conversation_id = str(tunnel.get("conversation_id") or "")
        if not user_id or not conversation_id:
            continue
        try:
            start_tunnel(user_id, conversation_id, tunnel["tunnel_id"])
        except Exception:
            logger.debug(
                "Service Tunnel reconciliation failed for %s",
                tunnel.get("tunnel_id"), exc_info=True)
