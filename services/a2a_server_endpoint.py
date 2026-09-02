"""Inbound A2A 1.0 HTTP+JSON endpoint for published PawFlow agents."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

from core.a2a_store import A2AStore
from core.a2a_runtime import public_task, send_message
from services.mcp_server_endpoint import (
    _header, _json_response,
)


logger = logging.getLogger(__name__)
_ROUTE_OWNER = "_published_a2a_server"


def _request_json(req) -> Dict[str, Any]:
    try:
        value = json.loads((req.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _publication(req, *, authenticate: bool = True) -> Tuple[
        Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    from services.published_agent_auth import resolve_published_agent

    access = resolve_published_agent(req, authenticate=authenticate)
    if access.error == "not_found":
        _json_response(req, 404, {"error": "A2A publication not found"})
        return None, None
    if access.error == "origin_forbidden":
        _json_response(req, 403, {"error": "Origin is not allowed"})
        return None, None
    if access.error == "unauthorized":
        _json_response(req, 401, {"error": "Unauthorized"},
                       {"WWW-Authenticate": "Bearer"})
        return None, None
    if access.error:
        _json_response(req, 403, {"error": "Published agent is unavailable"})
        return None, None
    return access.publication, access.key


def _base_url(req, publication_id: str) -> str:
    proto = (_header(req, "X-Forwarded-Proto") or "http").split(",", 1)[0].strip()
    host = (_header(req, "X-Forwarded-Host") or _header(req, "Host")
            or "localhost").split(",", 1)[0].strip()
    return f"{proto}://{host}/a2a/{publication_id}"


def _agent_card(req, publication: Dict[str, Any]) -> Dict[str, Any]:
    publication_id = publication["publication_id"]
    description = publication.get("description") or (
        f"PawFlow agent {publication['agent_name']}")
    return {
        "name": publication.get("label") or publication["agent_name"],
        "description": description,
        "supportedInterfaces": [{
            "url": _base_url(req, publication_id),
            "protocolBinding": "HTTP+JSON",
            "protocolVersion": "1.0",
        }],
        "provider": {"organization": "PawFlow", "url": "https://pawflow.allcolor.org/"},
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": True,
        },
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer"},
        },
        "security": [{"bearer": []}],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": publication["agent_name"],
            "name": publication["agent_name"],
            "description": description,
            "tags": ["pawflow", "agent"],
        }],
    }


def handle_agent_card(req) -> None:
    publication, _ = _publication(req, authenticate=False)
    if publication:
        _json_response(req, 200, _agent_card(req, publication), {
            "Cache-Control": "public, max-age=60",
        })


def handle_extended_agent_card(req) -> None:
    publication, _ = _publication(req)
    if publication:
        _json_response(req, 200, _agent_card(req, publication))


def handle_send_message(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    body = _request_json(req)
    if not body:
        _json_response(req, 400, {"error": "A JSON SendMessageRequest is required"})
        return
    try:
        result = send_message(publication, key, body)
    except PermissionError as exc:
        _json_response(req, 403, {"error": str(exc)})
    except ValueError as exc:
        _json_response(req, 400, {"error": str(exc)})
    except Exception as exc:
        logger.exception("A2A message submission failed")
        _json_response(req, 500, {"error": "A2A submission failed", "message": str(exc)})
    else:
        _json_response(req, 200, result)


def handle_get_task(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    task_id = str((req.path_params or {}).get("task_id") or "")
    task = A2AStore.instance().get_task(
        publication["publication_id"], task_id, key["key_id"])
    if not task:
        _json_response(req, 404, {"error": "A2A task not found"})
        return
    _json_response(req, 200, public_task(task))


def handle_list_tasks(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    query = parse_qs(str(getattr(req, "query_string", "") or ""))
    context_id = str((query.get("contextId") or [""])[0])
    tasks = A2AStore.instance().list_tasks(
        publication["publication_id"], key["key_id"], context_id)
    _json_response(req, 200, {"tasks": [public_task(task) for task in tasks]})


def handle_cancel_task(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    task_id = str((req.path_params or {}).get("task_id") or "")
    store = A2AStore.instance()
    task = store.get_task(publication["publication_id"], task_id, key["key_id"])
    if not task:
        _json_response(req, 404, {"error": "A2A task not found"})
        return
    if task["state"] in {"completed", "failed", "canceled", "rejected"}:
        _json_response(req, 409, {"error": "A2A task is already terminal"})
        return
    # Killing a shared parent agent could interrupt an unrelated user turn.
    # Isolated A2A contexts can be stopped safely; shared mode is cooperative.
    if publication["context_policy"] == "isolated":
        try:
            from tasks.ai.agent_loop import AgentLoopTask
            AgentLoopTask.force_stop_agent(
                task["internal_conversation_id"], publication["agent_name"])
        except Exception:
            logger.warning("Could not force-stop isolated A2A task", exc_info=True)
    store.update_task(task_id, "canceled")
    task = store.get_task(publication["publication_id"], task_id, key["key_id"])
    _json_response(req, 200, public_task(task))


def register_a2a_routes(listener) -> None:
    """Register idempotent public routes; callbacks enforce scoped Bearer auth."""
    existing = {(row.get("method", ""), row.get("pattern", ""))
                for row in listener.get_routes()}
    routes = [
        ("GET", "/a2a/{publication_id}/agent-card.json", handle_agent_card),
        ("GET", "/a2a/{publication_id}/extendedAgentCard", handle_extended_agent_card),
        ("POST", "/a2a/{publication_id}/message:send", handle_send_message),
        ("GET", "/a2a/{publication_id}/tasks", handle_list_tasks),
        ("GET", "/a2a/{publication_id}/tasks/{task_id}", handle_get_task),
        ("POST", "/a2a/{publication_id}/tasks/{task_id}:cancel", handle_cancel_task),
    ]
    for method, pattern, callback in routes:
        if (method, pattern) not in existing:
            listener.register_route(method, pattern, _ROUTE_OWNER,
                                    callback=callback, public=True)


def ensure_a2a_routes() -> None:
    from services.http_listener_service import HTTPListenerService
    listener = next(iter(HTTPListenerService.all_instances().values()), None)
    if listener is None:
        raise RuntimeError("No HTTP listener is available for the A2A endpoint")
    register_a2a_routes(listener)


__all__ = ["register_a2a_routes", "ensure_a2a_routes"]
