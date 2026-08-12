"""Authorize FRP server-plugin operations for PawFlow service tunnels.

FRPS calls this boundary before accepting a tunnel client or registering its
STCP proxy. Every decision is rebuilt from a short-lived signed grant and the
current owner-scoped tunnel record; request metadata alone is never trusted.
"""

from __future__ import annotations

import hmac
from typing import Any, Dict

from core import service_tunnels


_ALLOW = {"reject": False, "unchange": True}
_DENY = {
    "reject": True,
    "reject_reason": "Invalid service tunnel request",
    "unchange": True,
}


def _mapping(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Invalid service tunnel request")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Invalid service tunnel request")
    return value


def _claims(token: Any, signing_key: str, now: int | None) -> Dict[str, Any]:
    return service_tunnels.verify_grant(signing_key, _text(token), now=now)


def _tunnel_for_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    tunnel = service_tunnels.get_tunnel(
        claims["user_id"], claims["tunnel_id"], include_secrets=True)
    if not tunnel.get("enabled", False):
        raise ValueError("Invalid service tunnel request")
    if not hmac.compare_digest(
            _text(claims.get("server_name")), _text(tunnel.get("server_name"))):
        raise ValueError("Invalid service tunnel request")
    return tunnel


def _expected_relay(tunnel: Dict[str, Any], role: str) -> str:
    field = "service_relay" if role == "service" else "access_relay"
    return _text(tunnel.get(field))


def _authorize_login(
        content: Dict[str, Any], signing_key: str, now: int | None) -> None:
    metas = _mapping(content.get("metas"))
    claims = _claims(metas.get("pawflow_grant"), signing_key, now)
    tunnel = _tunnel_for_claims(claims)
    role = _text(claims.get("role"))
    relay_id = _text(claims.get("relay_id"))

    if relay_id != _text(metas.get("pawflow_relay_id")):
        raise ValueError("Invalid service tunnel request")
    if relay_id != _expected_relay(tunnel, role):
        raise ValueError("Invalid service tunnel request")
    expected_client = f"pft_{claims['tunnel_id']}_{role}"
    if not hmac.compare_digest(_text(content.get("client_id")), expected_client):
        raise ValueError("Invalid service tunnel request")


def _authorize_new_proxy(
        content: Dict[str, Any], signing_key: str, now: int | None) -> None:
    user_metas = _mapping(_mapping(content.get("user")).get("metas"))
    proxy_metas = _mapping(content.get("metas"))
    user_grant = _text(user_metas.get("pawflow_grant"))
    if not hmac.compare_digest(
            user_grant, _text(proxy_metas.get("pawflow_grant"))):
        raise ValueError("Invalid service tunnel request")

    claims = _claims(user_grant, signing_key, now)
    tunnel = _tunnel_for_claims(claims)
    if claims.get("role") != "service":
        raise ValueError("Invalid service tunnel request")
    relay_id = _text(claims.get("relay_id"))
    if relay_id != _text(user_metas.get("pawflow_relay_id")):
        raise ValueError("Invalid service tunnel request")
    if relay_id != _expected_relay(tunnel, "service"):
        raise ValueError("Invalid service tunnel request")
    if not hmac.compare_digest(
            _text(content.get("proxy_name")), _text(tunnel.get("server_name"))):
        raise ValueError("Invalid service tunnel request")
    if content.get("proxy_type") != "stcp":
        raise ValueError("Invalid service tunnel request")
    if not hmac.compare_digest(
            _text(content.get("sk")), _text(tunnel.get("secret_key"))):
        raise ValueError("Invalid service tunnel request")


def authorize(
        request: Any, signing_key: str, *, now: int | None = None) -> Dict[str, Any]:
    """Return an FRP server-plugin decision for one operation.

    Only Login and NewProxy are accepted. Malformed, stale, unknown,
    cross-owner, or otherwise mismatched requests receive the same generic
    denial so the plugin cannot be used to enumerate tunnel state.
    """
    try:
        payload = _mapping(request)
        if payload.get("version") != "0.1.0":
            raise ValueError("Invalid service tunnel request")
        content = _mapping(payload.get("content"))
        operation = payload.get("op")
        if operation == "Login":
            _authorize_login(content, signing_key, now)
        elif operation == "NewProxy":
            _authorize_new_proxy(content, signing_key, now)
        else:
            raise ValueError("Invalid service tunnel request")
    except (KeyError, TypeError, ValueError):
        return dict(_DENY)
    return dict(_ALLOW)
