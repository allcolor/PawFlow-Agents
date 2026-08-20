"""Flow Runtime Console actions: task control, queue inspection, FlowFiles.

The NiFi-style operations API behind the Flow Runtime Viewer. Everything
here targets a RUNNING deployed instance (ExecutorRegistry); templates and
`flow_ref` drill-downs stay read-only in `flow_runtime_graph`.

Design rules (see docs/flow_runtime_console.md):
- queues are addressed by their stable ``connection_id`` (source +
  relationship + target), never by (source, target) alone;
- FlowFiles are addressed by ``process_id``, never by index;
- a FlowFile that was consumed between listing and clicking answers
  ``no_longer_queued`` — a normal condition, not an error;
- ``clear`` drops the queue WITHOUT resetting the downstream task (the
  engine's ``clear_task_queue`` reset behavior is deliberately not used);
- every mutation checkpoints immediately (a crash must not resurrect
  dropped FlowFiles from a stale checkpoint) and records provenance;
- secrets are never resolved for display: task details expose the RAW
  configuration (``${...}`` references intact), not resolved values.
"""

import json
import logging

logger = logging.getLogger(__name__)

_ACTIONS = {
    "flow_runtime_task_control", "flow_runtime_task_details",
    "flow_runtime_queue_list", "flow_runtime_queue_control",
    "flow_runtime_queue_item", "flow_runtime_flowfile_drop",
}

_TASK_OPS = {"start", "stop", "restart", "disable"}
_QUEUE_OPS = {"pause", "resume", "clear"}
_PREVIEW_MAX = 32 * 1024


def _authorize(instance_id: str, user_id: str, flowfile):
    """Same gate as start/stop/undeploy_flow: owner, or admin for global."""
    from core.deployment_registry import DeploymentRegistry
    inst = DeploymentRegistry.get_instance().get(instance_id)
    if inst and user_id and inst.owner and inst.owner != user_id:
        return "Permission denied"
    if inst and not inst.owner:
        roles = flowfile.get_attribute("http.auth.roles") or ""
        if "admin" not in roles:
            return "Requires admin role"
    return ""


def _executor(instance_id: str):
    from core.executor_registry import ExecutorRegistry
    executor = ExecutorRegistry.get_instance().get(instance_id)
    if executor is None or not executor.is_running:
        return None
    return executor


def _record_manual_op(executor, *, op: str, task_id: str = "",
                      connection_id: str = "", user_id: str = "",
                      flowfiles: int = 0, size_bytes: int = 0,
                      flowfile_id: str = "") -> None:
    """Provenance for manual console operations (audit trail)."""
    provenance = getattr(executor, "_provenance", None)
    if not provenance:
        return
    try:
        from engine.provenance import ProvenanceEvent, ProvenanceEventType
        provenance.record(ProvenanceEvent(
            event_type=ProvenanceEventType.DROP if "drop" in op or op == "queue_clear"
            else ProvenanceEventType.ROUTE,
            flowfile_id=flowfile_id,
            task_id=task_id or connection_id,
            task_type="manual_console_op",
            flow_id=getattr(getattr(executor, "_flow", None), "id", ""),
            content_size=size_bytes,
            attributes={"actor": user_id, "connection_id": connection_id,
                        "flowfiles": str(flowfiles)},
            details=op,
        ))
    except Exception:
        logger.debug("manual op provenance failed", exc_info=True)


def _checkpoint_now(executor) -> None:
    """Persist the mutated queues immediately: a crash right after a manual
    clear/drop must NOT restore the dropped FlowFiles from an older
    checkpoint."""
    try:
        executor._save_checkpoint()
    except Exception:
        logger.debug("post-mutation checkpoint failed", exc_info=True)


def _flowfile_row(connection, flowfile):
    created = getattr(flowfile, "created_at", 0)
    if hasattr(created, "timestamp"):
        created = created.timestamp()
    return {
        "process_id": flowfile.process_id,
        "size": flowfile.size(),
        "created_at": float(created or 0),
        "attr_count": len(flowfile.get_attributes()),
        "filename": flowfile.get_attribute("filename", ""),
        "mime_type": flowfile.get_attribute("mime.type", "")
        or flowfile.get_attribute("mime_type", ""),
    }


def _content_preview(flowfile):
    """Text preview for small printable content; metadata otherwise."""
    size = flowfile.size()
    mime = (flowfile.get_attribute("mime.type", "")
            or flowfile.get_attribute("mime_type", "") or "")
    if size > _PREVIEW_MAX:
        return {"available": False, "reason": "too_large", "size": size,
                "mime_type": mime}
    raw = flowfile.get_content() or b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"available": False, "reason": "binary", "size": size,
                "mime_type": mime}
    if "\x00" in text:
        return {"available": False, "reason": "binary", "size": size,
                "mime_type": mime}
    return {"available": True, "text": text, "size": size,
            "mime_type": mime}


def _handle_flow_runtime(self, action, body, store, user_id, flowfile):
    """Handle flow runtime console actions. Returns [flowfile] or None."""
    if action not in _ACTIONS:
        return None

    def _reply(payload, status=""):
        flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
        if status:
            flowfile.set_attribute("http.response.status", status)
        return [flowfile]

    instance_id = str(body.get("instance_id", "") or "")
    if not instance_id:
        return _reply({"error": "instance_id is required"}, "400")
    denied = _authorize(instance_id, user_id, flowfile)
    if denied:
        return _reply({"error": denied}, "403")
    executor = _executor(instance_id)
    if executor is None:
        return _reply({"error": "Instance is not running"}, "409")

    # ── Tasks ────────────────────────────────────────────────────
    if action == "flow_runtime_task_control":
        task_id = str(body.get("task_id", "") or "")
        operation = str(body.get("operation", "") or "")
        if not task_id or operation not in _TASK_OPS:
            return _reply({"error": "task_id and operation "
                                    "(start|stop|restart|disable) required"},
                          "400")
        method = getattr(executor, f"{operation}_task", None)
        if not callable(method):
            return _reply({"error": f"Unsupported operation {operation}"}, "400")
        ok = bool(method(task_id))
        _record_manual_op(executor, op=f"task_{operation}", task_id=task_id,
                          user_id=user_id)
        state = (executor.get_all_task_states() or {}).get(task_id, {})
        return _reply({"ok": ok, "task_id": task_id, "state": state})

    if action == "flow_runtime_task_details":
        task_id = str(body.get("task_id", "") or "")
        task = executor.get_task(task_id)
        if task is None:
            return _reply({"error": "Unknown task"}, "404")
        state = (executor.get_all_task_states() or {}).get(task_id, {})
        # RAW configuration only: expressions and ${secret} references are
        # shown as written, never resolved — resolving would leak secrets
        # into the browser. Internal runtime keys (_user_id...) are elided.
        raw_config = dict(getattr(task, "_original_config", None)
                          or task.config or {})
        config = {k: v for k, v in raw_config.items()
                  if not str(k).startswith("_")}
        return _reply({
            "task_id": task_id,
            "type": getattr(task, "TYPE", ""),
            "name": getattr(task, "NAME", ""),
            "state": state,
            "config": config,
            "schema": task.get_parameter_schema() or {},
        })

    # ── Queues ───────────────────────────────────────────────────
    connection_id = str(body.get("connection_id", "") or "")
    connection = executor.connections.get_by_id(connection_id) \
        if connection_id else None
    if connection is None:
        return _reply({"error": "Unknown connection"}, "404")

    if action == "flow_runtime_queue_list":
        offset = max(0, int(body.get("offset", 0) or 0))
        limit = max(1, min(int(body.get("limit", 50) or 50), 100))
        # Server-side pagination: never ship a whole multi-thousand queue
        # to the browser.
        window = connection.peek_all(limit=offset + limit)[offset:]
        return _reply({
            "stats": connection.get_stats(),
            "offset": offset, "limit": limit,
            "flowfiles": [_flowfile_row(connection, ff) for ff in window],
        })

    if action == "flow_runtime_queue_control":
        operation = str(body.get("operation", "") or "")
        if operation not in _QUEUE_OPS:
            return _reply({"error": "operation must be pause|resume|clear"},
                          "400")
        if operation == "pause":
            connection.pause()
        elif operation == "resume":
            connection.resume()
        else:
            dropped = connection.queue_size()
            dropped_bytes = connection.queue_bytes()
            # Drop ONLY the FlowFiles — no implicit task reset (that is what
            # the user asked for, nothing else).
            connection.clear()
            _checkpoint_now(executor)
            _record_manual_op(executor, op="queue_clear",
                              connection_id=connection_id, user_id=user_id,
                              flowfiles=dropped, size_bytes=dropped_bytes)
        if operation in ("pause", "resume"):
            _record_manual_op(executor, op=f"queue_{operation}",
                              connection_id=connection_id, user_id=user_id)
        return _reply({"ok": True, "stats": connection.get_stats()})

    process_id = str(body.get("process_id", "") or "")
    if not process_id:
        return _reply({"error": "process_id is required"}, "400")
    queued = connection.get_flowfile(process_id)

    if action == "flow_runtime_queue_item":
        if queued is None:
            # Queues are dynamic; the item was consumed between the listing
            # and the click. Normal condition, not a system error.
            return _reply({"no_longer_queued": True})
        return _reply({
            "flowfile": _flowfile_row(connection, queued),
            "attributes": queued.get_attributes(),
            "content": _content_preview(queued),
        })

    if action == "flow_runtime_flowfile_drop":
        if queued is None:
            return _reply({"no_longer_queued": True})
        size = queued.size()
        ok = connection.remove_by_process_id(process_id)
        if ok:
            _checkpoint_now(executor)
            _record_manual_op(executor, op="flowfile_drop",
                              connection_id=connection_id, user_id=user_id,
                              flowfiles=1, size_bytes=size,
                              flowfile_id=process_id)
        return _reply({"ok": ok, "stats": connection.get_stats()})

    return None


# ── Streaming content download ───────────────────────────────────
# GET /api/flow-runtime/{instance_id}/queues/{connection_id}/flowfiles/
#     {process_id}/content — session-authenticated route, streamed straight
# from FlowFile.get_content_stream(): a 500 MB FlowFile never transits
# through JSON/base64 or server RAM.

_ROUTE_OWNER = "_flow_runtime_console"
_ROUTE_PATTERN = ("/api/flow-runtime/{instance_id}/queues/{connection_id}"
                  "/flowfiles/{process_id}/content")


def handle_content_download(req) -> None:
    params = req.path_params or {}
    instance_id = params.get("instance_id", "")
    connection_id = params.get("connection_id", "")
    process_id = params.get("process_id", "")
    user_id = req.auth_user_id or ""

    def _json(status, payload):
        req.complete(status, {"Content-Type": "application/json"},
                     json.dumps(payload).encode())

    from core.deployment_registry import DeploymentRegistry
    inst = DeploymentRegistry.get_instance().get(instance_id)
    if inst and inst.owner and inst.owner != user_id:
        return _json(403, {"error": "Permission denied"})
    if inst and not inst.owner and req.auth_role != "admin":
        return _json(403, {"error": "Requires admin role"})
    executor = _executor(instance_id)
    if executor is None:
        return _json(409, {"error": "Instance is not running"})
    connection = executor.connections.get_by_id(connection_id)
    if connection is None:
        return _json(404, {"error": "Unknown connection"})
    flowfile = connection.get_flowfile(process_id)
    if flowfile is None:
        return _json(404, {"error": "FlowFile is no longer queued"})

    mime = (flowfile.get_attribute("mime.type", "")
            or flowfile.get_attribute("mime_type", "")
            or "application/octet-stream")
    filename = flowfile.get_attribute("filename", "") or process_id
    stream = flowfile.get_content_stream()

    def _chunks():
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                stream.close()
            except Exception:
                logger.debug("Could not close FlowFile content stream",
                             exc_info=True)

    req.complete_stream(200, {
        "Content-Type": mime,
        "Content-Length": str(flowfile.size()),
        "Content-Disposition":
            f'attachment; filename="{filename.replace(chr(34), "")}"',
    }, _chunks())


def register_flow_runtime_routes(listener) -> None:
    """Idempotent; the route is session-authenticated (public=False)."""
    existing = {(row.get("method", ""), row.get("pattern", ""))
                for row in listener.get_routes()}
    if ("GET", _ROUTE_PATTERN) not in existing:
        listener.register_route("GET", _ROUTE_PATTERN, _ROUTE_OWNER,
                                callback=handle_content_download)


def ensure_flow_runtime_routes() -> None:
    """Register the download route on every live listener (deploy-time)."""
    try:
        from services.http_listener_service import HTTPListenerService
        for listener in HTTPListenerService.all_instances().values():
            register_flow_runtime_routes(listener)
    except Exception:
        logger.debug("ensure_flow_runtime_routes failed", exc_info=True)


__all__ = ["_handle_flow_runtime", "register_flow_runtime_routes",
           "ensure_flow_runtime_routes", "handle_content_download"]
