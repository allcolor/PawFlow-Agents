"""Minimal A2A 1.0 HTTP+JSON client with PawFlow secret and relay support."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Tuple
import requests

from core.relay_proxy_url import resolve_relay_aware_url


_TIMEOUT_SECONDS = 30


def _json_response(response: requests.Response) -> Dict[str, Any]:
    if not 200 <= int(response.status_code) < 300:
        raise ValueError(f"A2A endpoint returned HTTP {response.status_code}")
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("A2A endpoint returned a non-object JSON response")
    return value


def _resolve_secret(name: str, *, user_id: str, conversation_id: str) -> str:
    if not name:
        return ""
    from core.pfp_runtime._helpers import _resolve_secret_value
    value = _resolve_secret_value(
        name, user_id=user_id, conversation_id=conversation_id)
    if value is None:
        raise ValueError(f"A2A authentication secret is unavailable: {name}")
    return value


def discover(card_url: str, *, user_id: str, conversation_id: str,
             agent_name: str = "", allow_private: bool = False) -> Tuple[
                 Dict[str, Any], str]:
    safe_card_url = resolve_relay_aware_url(
        card_url, user_id=user_id, conversation_id=conversation_id,
        agent_name=agent_name, allow_private=allow_private,
        service_name="A2A Agent Card")
    response = requests.get(
        safe_card_url, headers={"Accept": "application/json",
                                "User-Agent": "PawFlow-A2A/1.0"},
        timeout=_TIMEOUT_SECONDS, allow_redirects=False)
    card = _json_response(response)
    interfaces = card.get("supportedInterfaces")
    if not isinstance(interfaces, list):
        raise ValueError("A2A Agent Card has no supportedInterfaces")
    interface = next((item for item in interfaces
                      if isinstance(item, dict)
                      and str(item.get("protocolBinding") or "").upper()
                      == "HTTP+JSON"), None)
    if not interface or not interface.get("url"):
        raise ValueError("A2A Agent Card does not advertise HTTP+JSON")
    endpoint = resolve_relay_aware_url(
        str(interface["url"]), user_id=user_id,
        conversation_id=conversation_id, agent_name=agent_name,
        allow_private=allow_private, service_name="A2A interface")
    return card, endpoint


def call_target(target: Dict[str, Any], action: str, *, message: str = "",
                task_id: str = "", context_id: str = "", user_id: str,
                conversation_id: str, agent_name: str = "") -> Dict[str, Any]:
    if target.get("kind") != "remote":
        raise ValueError("The a2a tool requires a remote named target")
    card, endpoint = discover(
        target["agent_card_url"], user_id=user_id,
        conversation_id=conversation_id, agent_name=agent_name,
        allow_private=bool(target.get("allow_private")))
    secret = _resolve_secret(
        str(target.get("auth_secret") or ""), user_id=user_id,
        conversation_id=conversation_id)
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": "PawFlow-A2A/1.0"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    tenant = next((str(item.get("tenant") or "") for item in
                   card.get("supportedInterfaces", [])
                   if isinstance(item, dict)
                   and str(item.get("protocolBinding") or "").upper()
                   == "HTTP+JSON"), "")
    if tenant:
        headers["A2A-Tenant"] = tenant
    if action == "send":
        if not message.strip():
            raise ValueError("message is required for A2A send")
        payload: Dict[str, Any] = {
            "message": {
                "messageId": "msg_" + uuid.uuid4().hex,
                "role": "user",
                "parts": [{"text": message}],
            },
            "configuration": {"returnImmediately": True},
        }
        if context_id:
            payload["message"]["contextId"] = context_id
        response = requests.post(
            endpoint.rstrip("/") + "/message:send", headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=_TIMEOUT_SECONDS, allow_redirects=False)
    elif action == "get":
        if not task_id:
            raise ValueError("task_id is required for A2A get")
        response = requests.get(
            endpoint.rstrip("/") + "/tasks/" + task_id,
            headers=headers, timeout=_TIMEOUT_SECONDS,
            allow_redirects=False)
    elif action == "cancel":
        if not task_id:
            raise ValueError("task_id is required for A2A cancel")
        response = requests.post(
            endpoint.rstrip("/") + "/tasks/" + task_id + ":cancel",
            headers=headers, data=b"{}", timeout=_TIMEOUT_SECONDS,
            allow_redirects=False)
    else:
        raise ValueError("action must be 'send', 'get', or 'cancel'")
    return _json_response(response)
