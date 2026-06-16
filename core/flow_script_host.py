"""Host-side dispatch for containerized executeScript host-calls (Option A).

A containerized ``executeScript`` script reaches ``get_service()``, ``pawflow``
and ``flowfile`` through the very same stdin/stdout host-call protocol the PFP
runtime SDK already speaks (``pawflow.package.runtime.host_call.v1`` /
``...result.v1``). This module resolves ONE decoded host-call envelope against:

- THIS flow's declared services only (``self._services`` from the task) — never
  a global registry, mirroring the local ``_sandbox_get_service`` boundary;
- the flow's scope-bounded :class:`FlowPawflowApi` facade (already authorized
  against the deployment scope), and
- the live :class:`FlowFile`.

The goal is to be a 100% drop-in replacement for the in-process path: the names
a script sees AND the way it calls them are identical in ``_execute_local`` and
``_execute_docker``. Bytes cross the JSON boundary base64-encoded so binary
FlowFile content round-trips losslessly.

Security / lifecycle:
- Service resolution is bounded to the flow's declared services; an unknown id
  raises a clear error instead of leaking the registry.
- Only dunder (``__x__``) operations are refused — they are never legitimate
  service/pawflow operations and would otherwise let Python internals (copy,
  pickle, repr) trigger host-calls. Everything the in-process script could call
  on the raw object stays callable, for true drop-in parity.
- Underlying exceptions are sanitized before crossing back to the container
  (which may forward them to end users); the full detail is logged host-side.
- ``register_inflight_agent``/``abort`` let the task's EXPLICIT docker timeout
  cancel a blocking ``pawflow.run_agent`` the script launched — no implicit
  per-call timeout is added.
"""

import base64
import json
import logging
import threading

logger = logging.getLogger(__name__)

HOST_CALL_FORMAT = "pawflow.package.runtime.host_call.v1"
RESULT_FORMAT = "pawflow.package.runtime.result.v1"

# Marker for bytes transported over the JSON boundary (lossless binary).
_BYTES_KEY = "__bytes_b64__"


class FlowScriptHostError(Exception):
    """Raised for an invalid or unauthorized containerized host-call.

    Its message is considered SAFE to forward to the container (it never carries
    underlying secrets/paths — only our own boundary diagnostics).
    """


def _is_dunder(operation: str) -> bool:
    return operation.startswith("__")


def _json_safe(result, context: str):
    """Validate a result is JSON-serializable; raise a clear error if not.

    Copied here (rather than imported from core.pfp_runtime) to keep this
    module decoupled from the PFP package runtime internals.
    """
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FlowScriptHostError(
            f"operation returned a non-JSON result: {context}") from exc
    return result


class FlowScriptHostDispatcher:
    """Resolve containerized executeScript host-calls against one flow instance.

    Parameters
    ----------
    services:
        The task's declared services (``self._services``). The ONLY resolution
        surface for ``kind="service"`` calls.
    pawflow_api:
        The scope-bounded ``FlowPawflowApi`` instance (or ``None``), used for
        ``kind="pawflow_api"`` calls.
    flowfile:
        The live ``FlowFile`` the task is processing, mutated in place for
        ``kind="flowfile"`` calls so changes are visible downstream.
    """

    def __init__(self, *, services, pawflow_api, flowfile):
        self._services = services or {}
        self._pawflow = pawflow_api
        self._flowfile = flowfile
        self._lock = threading.Lock()
        self._inflight_agent = None  # (conversation_id, agent, runtime_port)

    # ── lifecycle: explicit-timeout-driven cancellation ───────────────
    def abort(self):
        """Cancel a blocking ``pawflow.run_agent`` launched by the script.

        Called by the task's docker-timeout watchdog (an EXPLICIT timeout). It
        unblocks the host loop without adding any implicit per-call timeout.
        """
        with self._lock:
            inflight = self._inflight_agent
        if not inflight or self._pawflow is None:
            return
        cid, agent, runtime_port = inflight
        try:
            self._pawflow.cancel_agent(
                cid, agent=agent, runtime_port=runtime_port,
                reason="container_timeout")
        except Exception:
            logger.debug("abort cancel_agent failed", exc_info=True)

    def handle(self, envelope):
        """Return a result-envelope dict for a decoded host-call envelope.

        Never raises: any error becomes ``{ok: False, error: ...}``. Our own
        boundary errors pass through; underlying exceptions are sanitized
        (logged host-side, generic message returned).
        """
        kind = str((envelope or {}).get("kind") or "")
        operation = str((envelope or {}).get("operation") or "")
        target = str((envelope or {}).get("target") or "")
        try:
            if (not isinstance(envelope, dict)
                    or envelope.get("format") != HOST_CALL_FORMAT):
                raise FlowScriptHostError("invalid host-call envelope")
            args = envelope.get("args") or []
            arguments = envelope.get("arguments") or {}
            if not isinstance(args, list):
                raise FlowScriptHostError("host-call args must be a list")
            if not isinstance(arguments, dict):
                raise FlowScriptHostError("host-call arguments must be an object")
            if kind == "service":
                result = self._call_service(target, operation, args, arguments)
            elif kind == "pawflow_api":
                result = self._call_pawflow(operation, args, arguments)
            elif kind == "flowfile":
                result = self._call_flowfile(operation, args, arguments)
            else:
                raise FlowScriptHostError("unsupported host-call kind: %s" % kind)
            return {"format": RESULT_FORMAT, "ok": True, "result": result}
        except FlowScriptHostError as exc:
            # Our own boundary diagnostics — safe to forward verbatim.
            return {"format": RESULT_FORMAT, "ok": False, "error": str(exc)}
        except Exception:
            # Underlying failure (DB driver, network, ...): the raw message may
            # carry secrets/paths and can reach end users via the bot. Log the
            # detail host-side, return a sanitized message.
            where = ("%s.%s" % (target, operation)) if target else (
                operation or kind)
            logger.warning(
                "containerized host-call failed: %s", where, exc_info=True)
            return {
                "format": RESULT_FORMAT, "ok": False,
                "error": "host operation failed: %s" % where,
            }

    # ── service (bounded to self._services; drop-in parity) ───────────
    def _call_service(self, service_id, operation, args, arguments):
        svc = self._services.get(service_id)
        if svc is None:
            raise FlowScriptHostError(
                "Service '%s' is not declared in this flow's services"
                % service_id)
        if not operation or _is_dunder(operation):
            raise FlowScriptHostError(
                "service operation is not available: %s.%s"
                % (service_id, operation))
        method = getattr(svc, operation, None)
        if not callable(method):
            raise FlowScriptHostError(
                "service does not support operation: %s.%s"
                % (service_id, operation))
        result = method(*args, **arguments)
        return _json_safe(result, "%s.%s" % (service_id, operation))

    # ── pawflow facade (already scope-authorized) ─────────────────────
    def _call_pawflow(self, operation, args, arguments):
        if self._pawflow is None:
            raise FlowScriptHostError("pawflow API is not available in this flow")
        if not operation or _is_dunder(operation):
            raise FlowScriptHostError(
                "pawflow operation is not available: %s" % operation)
        method = getattr(self._pawflow, operation, None)
        if not callable(method):
            raise FlowScriptHostError("pawflow API has no operation: %s" % operation)
        if operation in ("run_agent", "submit_agent"):
            return self._call_pawflow_agent(method, operation, args, arguments)
        result = method(*args, **arguments)
        return _json_safe(result, "pawflow.%s" % operation)

    def _call_pawflow_agent(self, method, operation, args, arguments):
        # Register the target so an EXPLICIT docker timeout can cancel this
        # otherwise-unbounded wait (run_agent with timeout=None).
        cid = arguments.get("conversation_id")
        if cid is None and len(args) > 0:
            cid = args[0]
        agent = arguments.get("agent")
        if agent is None and len(args) > 1:
            agent = args[1]
        runtime_port = arguments.get("runtime_port") or ""
        with self._lock:
            self._inflight_agent = (
                str(cid or ""), str(agent or ""), str(runtime_port or ""))
        try:
            result = method(*args, **arguments)
        finally:
            with self._lock:
                self._inflight_agent = None
        return _json_safe(result, "pawflow.%s" % operation)

    # ── flowfile (mutated in place; binary-safe via base64) ───────────
    def _call_flowfile(self, operation, args, arguments):
        ff = self._flowfile
        if ff is None:
            raise FlowScriptHostError("flowfile is not available")
        if operation == "get_content":
            data = ff.get_content()
            if data is None:
                data = b""
            if isinstance(data, str):
                data = data.encode("utf-8")
            return {_BYTES_KEY: base64.b64encode(bytes(data)).decode("ascii")}
        if operation == "set_content":
            value = arguments.get("content") if "content" in arguments else (
                args[0] if args else "")
            if isinstance(value, dict) and _BYTES_KEY in value:
                # bytes from the script: decode losslessly, store bytes.
                ff.set_content(base64.b64decode(value[_BYTES_KEY]))
            else:
                # str (or other) from the script: store as the script passed it,
                # matching FlowFile.set_content's permissive in-process behavior.
                ff.set_content(value)
            return True
        if operation == "get_attribute":
            key = arguments.get("key") if "key" in arguments else (
                args[0] if args else "")
            default = arguments.get("default")
            if default is None and len(args) > 1:
                default = args[1]
            return ff.get_attribute(str(key), default)
        if operation == "set_attribute":
            if "key" in arguments:
                key = arguments.get("key")
                value = arguments.get("value")
            else:
                key = args[0] if args else ""
                value = args[1] if len(args) > 1 else ""
            ff.set_attribute(str(key), value)
            return True
        if operation == "get_attributes":
            return dict(ff.get_attributes())
        raise FlowScriptHostError(
            "flowfile operation is not available: %s" % operation)
