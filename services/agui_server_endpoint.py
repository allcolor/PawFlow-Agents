"""Inbound AG-UI HTTP endpoint for published PawFlow agents.

AG-UI (https://github.com/ag-ui-protocol/ag-ui) clients POST one
RunAgentInput to the agent URL and read back an SSE stream of AG-UI
events. PawFlow serves it on the SAME publications and Bearer keys as the
A2A endpoint — one "publish agent" action exposes both protocols:

    GET  /agui/{publication_id}        — small JSON descriptor (auth'd)
    POST /agui/{publication_id}        — run the agent (SSE response)

For classic publications, AG-UI ``threadId`` maps to an ``agui_``-prefixed
named A2A context. Managed publications pass the raw client ``threadId`` to
the generation-aware AG-UI thread machine; only its derived internal
``context_id`` is prefixed. Either mode gives every isolated thread its own
internal conversation with durable server-side history.
"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

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
_SUBSCRIBER_EPOCH_HEADER = "X-PawFlow-Subscriber-Epoch"


def handle_describe(req) -> None:
    publication, key = _publication(req)
    if not publication or not key:
        return
    isolated = publication.get("context_policy", "isolated") == "isolated"
    # Thread bootstrap (B1-T): GET ?thread_id= creates the thread at
    # generation 0 when unseen and returns the generation every
    # subsequent run must present as `threadGeneration`.
    thread = None
    thread_id = _query_value(req, "thread_id")
    if thread_id:
        from core.a2a_store import A2AStore
        handle = A2AStore.instance().ensure_agui_thread(
            publication, key["key_id"], thread_id)
        thread = {"threadId": thread_id,
                  "generation": handle["generation"]}
    # Managed frontend execution (receipts/deposits) is a publication-
    # fixed setting, announced here — a request can NEVER select it. It
    # requires an isolated context (the batch state is per-thread).
    managed = isolated and bool(publication.get("managed_mode"))
    _json_response(req, 200, {
        "protocol": "ag-ui",
        "name": publication.get("label") or publication["agent_name"],
        "description": publication.get("description")
        or f"PawFlow agent {publication['agent_name']}",
        "agent": publication["agent_name"],
        "transport": "http-sse",
        "contextPolicy": publication.get("context_policy", "isolated"),
        "executionMode": "managed" if managed else "classic",
        # Interactive protocol features require an isolated context: on a
        # shared publication the conversation belongs to the owner and must
        # not grow client-declared tools or state.
        "capabilities": {
            "frontendTools": isolated,
            "sharedState": isolated,
            "interrupts": isolated,
            "managedBatch": managed,
            "multimodal": ["text", "inline-data", "url-reference"],
        },
        # Managed clients drive the batch through these POST ?action=
        # endpoints; the credential rides the X-PawFlow-Exec-Token header.
        "actions": (["attach", "claim_batch", "begin", "deposit", "renew"]
                    if managed else []),
        # Explicit cancellation (B1-J): DELETE on the same URL, the
        # cancel_token (issued in RUN_STARTED) rides this header.
        "cancel": ({"method": "DELETE",
                    "header": "X-PawFlow-Cancel-Token"}
                   if managed else None),
        "threadTtlSeconds": publication.get("thread_ttl_seconds"),
        # Present iff ?thread_id= was given: the generation the client
        # must send back as `threadGeneration` on every run (B1-T).
        "thread": thread,
    })


def _query_value(req, name: str) -> str:
    query = parse_qs(str(getattr(req, "query_string", "") or ""))
    return str((query.get(name) or [""])[0]).strip()


def _query_action(req) -> str:
    return _query_value(req, "action")


def handle_run(req) -> None:
    # Managed frontend-execution actions (claim_batch/begin/deposit/renew)
    # are POSTs to the same URL discriminated by ?action=. A plain run is
    # action-less. The dispatcher authenticates and answers itself.
    action = _query_action(req)
    if action:
        # Any ?action= request is authenticated and answered by the
        # managed dispatcher (unknown actions included, AFTER auth).
        from services._agui_actions import handle_managed_action
        handle_managed_action(req, action)
        return
    publication, key = _publication(req)
    if not publication or not key:
        return
    body = _request_json(req)
    if not body:
        _json_response(req, 400, {"error": "A JSON RunAgentInput is required"})
        return
    # Managed publications run the closed protocol: the admission happens
    # SYNCHRONOUSLY here (before the stream opens) so a busy thread /
    # parent mismatch / incomplete prior batch is a real 409, not an SSE
    # RUN_ERROR. Classic publications keep the plain POST→SSE bridge.
    managed = (publication.get("context_policy") == "isolated"
               and bool(publication.get("managed_mode")))
    if managed:
        from core._agui_managed_runtime import (
            acquire_managed_turn, run_managed_agent_stream,
            ManagedAcquireError)
        try:
            prepared = acquire_managed_turn(publication, key, body)
        except ManagedAcquireError as exc:
            _json_response(req, exc.status,
                           {"error": exc.code, "message": exc.message})
            return
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("managed AG-UI acquire failed")
            _json_response(req, 500, {"error": "internal_error"})
            return
        # Take over the subscriber slot before opening the response. The
        # stream factory is a normal function and starts the durable pilot
        # here too, so neither action is deferred until the first SSE read.
        from core.a2a_store import A2AStore
        try:
            subscriber = A2AStore.instance().acquire_agui_subscriber(
                prepared["context"]["context_id"],
                prepared["spec"]["run_id"], after_seq=0)
            epoch = int(subscriber["subscriber_epoch"])
            stream = run_managed_agent_stream(
                prepared, subscriber_epoch=epoch)
        except Exception:  # pragma: no cover - defensive
            logger.exception("managed AG-UI subscriber setup failed")
            _json_response(req, 500, {"error": "internal_error"})
            return
        headers = dict(_SSE_HEADERS)
        headers[_SUBSCRIBER_EPOCH_HEADER] = str(epoch)
        req.complete_stream(200, headers, stream)
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


def handle_cancel(req) -> None:
    # DELETE /agui/{publication_id} + X-PawFlow-Cancel-Token (B1-J):
    # idempotent, journaled; the dispatcher authenticates first.
    from services._agui_actions import handle_cancel as _cancel
    _cancel(req)


def register_agui_routes(listener) -> None:
    """Register idempotent public routes; callbacks enforce scoped Bearer auth."""
    existing = {(row.get("method", ""), row.get("pattern", ""))
                for row in listener.get_routes()}
    routes = [
        ("GET", "/agui/{publication_id}", handle_describe),
        ("POST", "/agui/{publication_id}", handle_run),
        ("DELETE", "/agui/{publication_id}", handle_cancel),
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
