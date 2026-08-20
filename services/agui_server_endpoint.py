"""Inbound AG-UI HTTP endpoint for published PawFlow agents.

AG-UI (https://github.com/ag-ui-protocol/ag-ui) clients POST one
RunAgentInput to the agent URL and read back an SSE stream of AG-UI
events. PawFlow serves it on the SAME publications and Bearer keys as the
A2A endpoint — one "publish agent" action exposes both protocols:

    GET  /agui/{publication_id}        — small JSON descriptor (auth'd)
    POST /agui/{publication_id}        — run the agent (SSE response)

The AG-UI ``threadId`` maps to a per-key A2A context (prefixed "agui_"),
so an isolated publication gives every AG-UI thread its own internal
conversation with durable server-side history.
"""

from __future__ import annotations

import logging

from services.a2a_server_endpoint import _publication, _request_json
from services.mcp_server_endpoint import _json_response


logger = logging.getLogger(__name__)
_ROUTE_OWNER = "_published_agui_server"

_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def handle_describe(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    isolated = publication.get("context_policy", "isolated") == "isolated"
    _json_response(req, 200, {
        "protocol": "ag-ui",
        "name": publication.get("label") or publication["agent_name"],
        "description": publication.get("description")
        or f"PawFlow agent {publication['agent_name']}",
        "agent": publication["agent_name"],
        "transport": "http-sse",
        "contextPolicy": publication.get("context_policy", "isolated"),
        # Interactive protocol features require an isolated context: on a
        # shared publication the conversation belongs to the owner and must
        # not grow client-declared tools or state.
        "capabilities": {
            "frontendTools": isolated,
            "sharedState": isolated,
            "interrupts": isolated,
            "multimodal": ["text", "inline-data", "url-reference"],
        },
    })


def handle_run(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    body = _request_json(req)
    if not body:
        _json_response(req, 400, {"error": "A JSON RunAgentInput is required"})
        return
    from core.agui_runtime import run_agent_stream
    try:
        stream = run_agent_stream(publication, key, body)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("AG-UI run setup failed")
        _json_response(req, 500, {"error": "AG-UI run failed",
                                  "message": str(exc)})
        return
    req.complete_stream(200, dict(_SSE_HEADERS), stream)


def register_agui_routes(listener) -> None:
    """Register idempotent public routes; callbacks enforce scoped Bearer auth."""
    existing = {(row.get("method", ""), row.get("pattern", ""))
                for row in listener.get_routes()}
    routes = [
        ("GET", "/agui/{publication_id}", handle_describe),
        ("POST", "/agui/{publication_id}", handle_run),
    ]
    for method, pattern, callback in routes:
        if (method, pattern) not in existing:
            listener.register_route(method, pattern, _ROUTE_OWNER,
                                    callback=callback, public=True)


def ensure_agui_routes() -> None:
    from services.http_listener_service import HTTPListenerService
    listener = next(iter(HTTPListenerService.all_instances().values()), None)
    if listener is None:
        raise RuntimeError("No HTTP listener is available for the AG-UI endpoint")
    register_agui_routes(listener)


__all__ = ["register_agui_routes", "ensure_agui_routes"]
