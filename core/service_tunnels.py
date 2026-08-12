"""User-scoped service tunnel definitions and signed relay grants.

A service tunnel exposes one locally approved TCP service from a service relay
through a loopback-only listener on an access relay.  The module owns durable
definitions and deliberately keeps orchestration and FRP process management in
separate layers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from typing import Any, Dict, List

from core.repository import SCOPE_USER, ScopedRepository


_RTYPE = "service_tunnels"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ID_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,96}$")
_SECRET_FIELDS = frozenset({"secret_key", "conversation_id"})


def _required(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _identifier(value: Any, field: str) -> str:
    text = _required(value, field)
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


def _port(value: Any, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a TCP port") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{field} must be between 1 and 65535")
    return port


def _public(entry: Dict[str, Any]) -> Dict[str, Any]:
    result = {key: value for key, value in entry.items() if key not in _SECRET_FIELDS}
    if result.get("display_name"):
        result["name"] = result["display_name"]
    return result


def create_tunnel(user_id: str, payload: Dict[str, Any],
                  authorized_service: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a tunnel after its target service was approved by the relay.

    ``authorized_service`` must come from the service relay's local catalogue;
    request payloads are never allowed to choose an arbitrary target host/port.
    """
    user_id = _required(user_id, "user_id")
    if not isinstance(payload, dict):
        raise ValueError("payload is required")
    if not isinstance(authorized_service, dict):
        raise ValueError("authorized_service is required")

    access_relay = _identifier(payload.get("access_relay"), "access_relay")
    service_relay = _identifier(payload.get("service_relay"), "service_relay")
    if access_relay == service_relay:
        raise ValueError("access_relay and service_relay must be different")

    bind_host = str(payload.get("bind_host") or "127.0.0.1").strip().lower()
    if bind_host not in _LOOPBACK_HOSTS:
        raise ValueError("bind_host must be loopback-only")

    service_id = _identifier(authorized_service.get("service_id"), "service_id")
    protocol = str(authorized_service.get("protocol") or "").strip().lower()
    if protocol != "tcp":
        raise ValueError("Only TCP service tunnels are supported")
    target_host = _required(authorized_service.get("target_host"), "target_host")
    target_port = _port(authorized_service.get("target_port"), "target_port")
    tunnel_id = uuid.uuid4().hex
    now = time.time()
    entry = {
        "tunnel_id": tunnel_id,
        "display_name": _required(payload.get("name"), "name"),
        "conversation_id": _required(
            payload.get("conversation_id"), "conversation_id"),
        "access_relay": access_relay,
        "service_relay": service_relay,
        "bind_host": bind_host,
        "bind_port": _port(payload.get("bind_port"), "bind_port"),
        "service_id": service_id,
        "service_name": str(authorized_service.get("name") or service_id),
        "protocol": protocol,
        "target_host": target_host,
        "target_port": target_port,
        "server_name": f"pft_{tunnel_id}",
        "secret_key": secrets.token_urlsafe(32),
        "persistent": bool(payload.get("persistent", True)),
        "enabled": True,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    saved = ScopedRepository.instance().create(
        _RTYPE, tunnel_id, SCOPE_USER, entry, user_id=user_id)
    return _public(saved)


def get_tunnel(user_id: str, tunnel_id: str, *, include_secrets: bool = False) -> Dict[str, Any]:
    user_id = _required(user_id, "user_id")
    tunnel_id = _identifier(tunnel_id, "tunnel_id")
    entry = ScopedRepository.instance().get(
        _RTYPE, tunnel_id, SCOPE_USER, user_id=user_id)
    if entry is None:
        raise KeyError(f"Service tunnel '{tunnel_id}' not found")
    return dict(entry) if include_secrets else _public(entry)


def list_tunnels(user_id: str) -> List[Dict[str, Any]]:
    user_id = _required(user_id, "user_id")
    entries = ScopedRepository.instance().list(
        _RTYPE, SCOPE_USER, user_id=user_id)
    return sorted((_public(entry) for entry in entries),
                  key=lambda item: float(item.get("created_at") or 0))


def list_all_tunnels(*, include_secrets: bool = False) -> List[Dict[str, Any]]:
    """List every owner-scoped tunnel for internal lifecycle reconciliation."""
    entries = ScopedRepository.instance().list_all_owners(_RTYPE)
    if include_secrets:
        return [dict(entry) for entry in entries]
    return [_public(entry) for entry in entries]


def update_tunnel(user_id: str, tunnel_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"enabled", "status", "error", "connection_mode", "last_connected_at"}
    clean = {key: value for key, value in dict(changes or {}).items() if key in allowed}
    clean["updated_at"] = time.time()
    updated = ScopedRepository.instance().update(
        _RTYPE, _identifier(tunnel_id, "tunnel_id"), SCOPE_USER, clean,
        user_id=_required(user_id, "user_id"))
    return _public(updated)


def delete_tunnel(user_id: str, tunnel_id: str) -> bool:
    return ScopedRepository.instance().delete(
        _RTYPE, _identifier(tunnel_id, "tunnel_id"), SCOPE_USER,
        user_id=_required(user_id, "user_id"))


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_grant(signing_key: str, *, tunnel_id: str, relay_id: str,
                user_id: str, server_name: str, role: str,
                ttl_seconds: int = 300, now: int | None = None) -> str:
    """Issue a short-lived HMAC grant consumed by relays and the FRP plugin."""
    key = _required(signing_key, "signing_key").encode("utf-8")
    if role not in {"service", "access"}:
        raise ValueError("role must be 'service' or 'access'")
    if ttl_seconds < 1 or ttl_seconds > 3600:
        raise ValueError("ttl_seconds must be between 1 and 3600")
    issued_at = int(time.time() if now is None else now)
    claims = {
        "tunnel_id": _identifier(tunnel_id, "tunnel_id"),
        "relay_id": _identifier(relay_id, "relay_id"),
        "user_id": _required(user_id, "user_id"),
        "server_name": _identifier(server_name, "server_name"),
        "role": role,
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
        "nonce": secrets.token_urlsafe(12),
    }
    payload = _b64encode(json.dumps(
        claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(key, payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_grant(signing_key: str, token: str, *, now: int | None = None) -> Dict[str, Any]:
    key = _required(signing_key, "signing_key").encode("utf-8")
    try:
        payload, signature = _required(token, "token").split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid service tunnel grant") from exc
    expected = _b64encode(hmac.new(
        key, payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid service tunnel grant")
    try:
        claims = json.loads(_b64decode(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid service tunnel grant") from exc
    current = int(time.time() if now is None else now)
    if int(claims.get("exp") or 0) <= current:
        raise ValueError("Service tunnel grant expired")
    if claims.get("role") not in {"service", "access"}:
        raise ValueError("Invalid service tunnel grant role")
    _identifier(claims.get("tunnel_id"), "tunnel_id")
    _identifier(claims.get("relay_id"), "relay_id")
    _required(claims.get("user_id"), "user_id")
    _identifier(claims.get("server_name"), "server_name")
    return claims
