"""Runtime entrypoint preparation, invocation builders and PackageRuntimeHost.

Split out of core/pfp_runtime.py for the <=800-line rule; re-exported from
core.pfp_runtime (invariant 1: import-path stability).
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, Dict

from core.pfp_runtime._base import HOST_CALL_FORMAT, PackageRuntimeError, RUNTIME_INVOKE_FORMAT
from core.pfp_runtime._bridge import RelayPackageRuntimeBridge
from core.pfp_runtime._helpers import _caller_identity, _flowfile_descriptor, _invocation_envelope, _is_blocked_builtin_service_operation, _json_safe_service_result, _list_value, _normalize_service_result, _normalize_task_result, _normalize_tool_result, _require_runtime_target, _resolve_package_service, _resolve_package_tool, _safe_entrypoint, _sha256_file, _target_ref

logger = logging.getLogger(__name__)


def prepare_runtime_entrypoint(runtime: Dict[str, Any],
                               installed_from: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Validate and resolve an installed package runtime entrypoint.

    This is intentionally execution-free. It verifies the content PawFlow would
    execute later is still the signed installed file and is still confined to
    the package content directory.
    """
    runtime = runtime or {}
    installed_from = installed_from or {}
    package = str(runtime.get("package") or "")
    object_id = str(runtime.get("object_id") or "")
    if not package or not object_id:
        raise PackageRuntimeError("package_runtime.package and package_runtime.object_id are required")

    content_dir = Path(str(runtime.get("content_dir") or "")).expanduser().resolve()
    if not content_dir.is_dir():
        raise PackageRuntimeError(f"PFP package content directory is missing: {content_dir}")

    entrypoint = _safe_entrypoint(str(runtime.get("entrypoint") or ""))
    entrypoint_path = (content_dir / entrypoint).resolve()
    try:
        entrypoint_path.relative_to(content_dir)
    except ValueError as exc:
        raise PackageRuntimeError("PFP runtime entrypoint escapes package content directory") from exc
    if not entrypoint_path.is_file():
        raise PackageRuntimeError(f"PFP runtime entrypoint is missing: {entrypoint}")

    expected_hash = str(installed_from.get("hash") or runtime.get("hash") or "")
    actual_hash = _sha256_file(entrypoint_path)
    if expected_hash and actual_hash != expected_hash and not bool(installed_from.get("dev") or runtime.get("dev")):
        raise PackageRuntimeError(
            f"PFP runtime entrypoint hash mismatch for {package}:{object_id}")
    runner = str(runtime.get("runner") or "").strip()
    if runner != "python":
        raise PackageRuntimeError(f"unsupported PFP runtime runner: {runner}")

    return {
        "package": package,
        "version": str(runtime.get("version") or ""),
        "object_id": object_id,
        "runtime": str(runtime.get("runtime") or "python"),
        "content_dir": str(content_dir),
        "entrypoint": entrypoint,
        "entrypoint_path": str(entrypoint_path),
        "hash": actual_hash,
        "runner": runner,
        "dependencies": _list_value(runtime.get("dependencies")),
        "allowed_tools": _list_value(runtime.get("allowed_tools")),
        "allowed_services": _list_value(runtime.get("allowed_services")),
        "provides": _list_value(runtime.get("provides")),
        "secrets": _list_value(runtime.get("secrets")),
        "secret_bindings": dict(runtime.get("secret_bindings") or {}),
        "dev": bool(runtime.get("dev") or installed_from.get("dev")),
    }

def build_tool_invocation(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                          arguments: Dict[str, Any],
                          context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    prepared = prepare_runtime_entrypoint(runtime, installed_from)
    return _invocation_envelope(
        "tool", prepared, {"arguments": arguments or {}}, context)

def build_service_invocation(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                             operation: str,
                             arguments: Dict[str, Any] | None = None,
                             context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not operation:
        raise PackageRuntimeError("PFP service operation is required")
    prepared = prepare_runtime_entrypoint(runtime, installed_from)
    return _invocation_envelope(
        "service", prepared,
        {"operation": operation, "arguments": arguments or {}}, context,
    )

def build_task_invocation(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                          task_config: Dict[str, Any], flowfile: Any,
                          context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    prepared = prepare_runtime_entrypoint(runtime, installed_from)
    return _invocation_envelope(
        "flow_task", prepared,
        {"task_config": task_config or {}, "flowfile": _flowfile_descriptor(flowfile)},
        context,
    )

def build_ui_handler_invocation(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                                action: str, arguments: Dict[str, Any] | None = None,
                                context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Envelope for a UI extension handler triggered by `pfp.call(action, body)`.

    Structurally identical to a tool invocation (relay subprocess, broker-authorized
    host calls back into PawFlow) but tagged so audit / logging can distinguish
    chat-tool calls from UI-driven calls and so the handler can read which action
    name the browser used.
    """
    if not action:
        raise PackageRuntimeError("PFP ui handler action is required")
    prepared = prepare_runtime_entrypoint(runtime, installed_from)
    return _invocation_envelope(
        "ui_handler", prepared,
        {"action": action, "arguments": arguments or {}}, context)

def build_agent_hook_invocation(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                                event: Dict[str, Any],
                                context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Envelope for an installed PFP agent_hook resource."""
    if not isinstance(event, dict):
        raise PackageRuntimeError("PFP agent_hook event must be an object")
    prepared = prepare_runtime_entrypoint(runtime, installed_from)
    return _invocation_envelope(
        "agent_hook", prepared, {"event": event}, context)

class PackageRuntimeHost:
    """Authorized host-call surface exposed to future out-of-process runtimes."""

    def __init__(self, *, user_id: str, conversation_id: str = "",
                 scope: str = "user", caller_runtime: Dict[str, Any],
                 tool_registry: Any = None, service_registry: Any = None):
        if not user_id:
            raise PackageRuntimeError("user_id is required for PFP runtime host calls")
        if not caller_runtime:
            raise PackageRuntimeError("caller_runtime is required for PFP runtime host calls")
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.scope = "conversation" if scope in {"conv", "conversation"} else "user"
        self.caller_runtime = caller_runtime
        self.tool_registry = tool_registry
        self.service_registry = service_registry

    def authorize_tool_call(self, tool_ref: str) -> Dict[str, Any]:
        from core.pfp_capabilities import PackageCapabilityBroker
        broker = PackageCapabilityBroker(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            scope=self.scope,
        )
        return broker.authorize_tool_call(self.caller_runtime, tool_ref)

    def authorize_service_call(self, service_ref: str) -> Dict[str, Any]:
        from core.pfp_capabilities import PackageCapabilityBroker
        broker = PackageCapabilityBroker(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            scope=self.scope,
        )
        return broker.authorize_service_call(self.caller_runtime, service_ref)

    def build_tool_call(self, tool_ref: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        authorization = self.authorize_tool_call(tool_ref)
        return {
            "format": HOST_CALL_FORMAT,
            "kind": "tool",
            "caller": _caller_identity(self.caller_runtime),
            "target": authorization["target"],
            "grant": authorization["grant"],
            "arguments": arguments or {},
        }

    def build_service_call(self, service_ref: str, operation: str,
                           arguments: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not operation:
            raise PackageRuntimeError("PFP host service operation is required")
        authorization = self.authorize_service_call(service_ref)
        return {
            "format": HOST_CALL_FORMAT,
            "kind": "service",
            "caller": _caller_identity(self.caller_runtime),
            "target": authorization["target"],
            "grant": authorization["grant"],
            "operation": operation,
            "arguments": arguments or {},
        }

    def execute_tool_call(self, tool_ref: str, arguments: Dict[str, Any]) -> Any:
        call = self.build_tool_call(tool_ref, arguments)
        handler = self._resolve_tool(call["target"])
        self._set_runtime_context(handler)
        return handler.execute(call["arguments"])

    def execute_service_call(self, service_ref: str, operation: str,
                             arguments: Dict[str, Any] | None = None) -> Any:
        call = self.build_service_call(service_ref, operation, arguments)
        service = self._resolve_service(call["target"])
        self._set_runtime_context(service)
        if hasattr(service, "invoke"):
            return service.invoke(call["operation"], call["arguments"])
        return self._dispatch_service_operation(
            service, call["operation"], call["arguments"], call["target"])

    def _dispatch_service_operation(self, service: Any, operation: str,
                                    arguments: Dict[str, Any],
                                    target: Dict[str, str]) -> Any:
        operation = str(operation or "").strip()
        if _is_blocked_builtin_service_operation(operation):
            raise PackageRuntimeError(
                f"service operation is not available for PFP host invocation: {operation}")
        method = getattr(service, operation, None)
        if not callable(method):
            raise PackageRuntimeError(
                f"service does not support PFP host operation: {target['name']}.{operation}")
        if not isinstance(arguments, dict):
            raise PackageRuntimeError("PFP host service arguments must be an object")
        try:
            result = method(**arguments)
        except TypeError as exc:
            raise PackageRuntimeError(
                f"service operation arguments are invalid for {target['name']}.{operation}: {exc}") from exc
        return _json_safe_service_result(result, target["name"], operation)

    def _set_runtime_context(self, target: Any) -> None:
        if hasattr(target, "set_runtime_context"):
            target.set_runtime_context(
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                scope=self.scope,
                agent_name=str((self.caller_runtime or {}).get("agent_name") or ""),
            )
            return
        if hasattr(target, "set_user_id"):
            target.set_user_id(self.user_id)
        if hasattr(target, "set_conversation_id"):
            target.set_conversation_id(self.conversation_id)
        agent_name = str((self.caller_runtime or {}).get("agent_name") or "")
        if agent_name and hasattr(target, "set_agent_name"):
            target.set_agent_name(agent_name)

    def handle_host_call(self, request: Dict[str, Any]) -> Any:
        if not isinstance(request, dict) or request.get("format") != HOST_CALL_FORMAT:
            raise PackageRuntimeError("invalid PFP host-call envelope")
        kind = str(request.get("kind") or "")
        target_ref = _target_ref(request.get("target") or request.get("target_ref"))
        arguments = request.get("arguments") or {}
        if kind == "tool":
            return self.execute_tool_call(target_ref, arguments)
        if kind == "service":
            operation = str(request.get("operation") or "")
            return self.execute_service_call(target_ref, operation, arguments)
        raise PackageRuntimeError(f"unsupported PFP host-call kind: {kind}")

    def _resolve_tool(self, target: Dict[str, str]) -> Any:
        if self.tool_registry is None:
            raise PackageRuntimeError("tool_registry is required for PFP host tool calls")
        if target.get("package"):
            handler = _resolve_package_tool(
                self.tool_registry, target, user_id=self.user_id,
                conversation_id=self.conversation_id,
                agent_name=str((self.caller_runtime or {}).get("agent_name") or ""))
            if handler is None:
                raise PackageRuntimeError(
                    f"host tool is not available: {target.get('package', '')}/{target.get('kind', '')}:{target.get('name', '')}")
            _require_runtime_target(handler, target)
            return handler
        handler = self.tool_registry.get(target["name"])
        if handler is None:
            raise PackageRuntimeError(f"host tool is not available: {target['name']}")
        _require_runtime_target(handler, target)
        return handler

    def _resolve_service(self, target: Dict[str, str]) -> Any:
        if self.service_registry is None:
            raise PackageRuntimeError("service_registry is required for PFP host service calls")
        if target.get("package"):
            service = _resolve_package_service(
                self.service_registry, target,
                user_id=self.user_id, conversation_id=self.conversation_id)
            if service is None:
                raise PackageRuntimeError(
                    f"host service is not available: {target.get('package', '')}/{target.get('kind', '')}:{target.get('name', '')}")
            _require_runtime_target(service, target)
            return service
        resolver = getattr(self.service_registry, "resolve", None)
        if not resolver:
            raise PackageRuntimeError("service_registry.resolve is required for PFP host service calls")
        service = resolver(target["name"], user_id=self.user_id, conv_id=self.conversation_id)
        if service is None:
            raise PackageRuntimeError(f"host service is not available: {target['name']}")
        _require_runtime_target(service, target)
        return service

def runtime_host_from_invocation(request: Dict[str, Any], *,
                                 tool_registry: Any = None,
                                 service_registry: Any = None) -> PackageRuntimeHost:
    """Build a host-call surface from a validated runtime invocation envelope."""
    if not isinstance(request, dict) or request.get("format") != RUNTIME_INVOKE_FORMAT:
        raise PackageRuntimeError("invalid PFP runtime invocation envelope")
    package_runtime = request.get("package") or {}
    context = request.get("context") or {}
    caller_runtime = {
        "package": package_runtime.get("package", ""),
        "version": package_runtime.get("version", ""),
        "object_id": package_runtime.get("object_id", ""),
        "allowed_tools": package_runtime.get("allowed_tools", []),
        "allowed_services": package_runtime.get("allowed_services", []),
        "agent_name": str(context.get("agent_name") or ""),
    }
    return PackageRuntimeHost(
        user_id=str(context.get("user_id") or ""),
        conversation_id=str(context.get("conversation_id") or ""),
        scope=str(context.get("scope") or "user"),
        caller_runtime=caller_runtime,
        tool_registry=tool_registry,
        service_registry=service_registry,
    )

def invoke_tool(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                arguments: Dict[str, Any],
                context: Dict[str, Any] | None = None) -> str:
    request = build_tool_invocation(runtime, installed_from, arguments, context)
    result = _invoke_bridge(request)
    return _normalize_tool_result(result)

def invoke_service(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                   operation: str, arguments: Dict[str, Any] | None = None,
                   context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    request = build_service_invocation(runtime, installed_from, operation, arguments, context)
    result = _invoke_bridge(request)
    return _normalize_service_result(result)

def invoke_task(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                task_config: Dict[str, Any], flowfile: Any,
                context: Dict[str, Any] | None = None) -> Any:
    request = build_task_invocation(runtime, installed_from, task_config, flowfile, context)
    return _normalize_task_result(_invoke_bridge(request))

def invoke_ui_handler(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                      action: str, arguments: Dict[str, Any] | None = None,
                      context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run a UI extension handler via the relay subprocess sandbox.

    Same isolation guarantees as `invoke_tool`/`invoke_service`: the entrypoint
    is hash-verified against the signed install record, the relay child runs
    with a scrubbed env, and any host-side `pfp.call_tool`/`pfp.call_service`
    requests are re-authorized through `PackageCapabilityBroker` before running.
    Returns a JSON-serializable dict shaped by the handler.
    """
    request = build_ui_handler_invocation(
        runtime, installed_from, action, arguments, context)
    result = _invoke_bridge(request)
    return _normalize_service_result(result)

def invoke_agent_hook(runtime: Dict[str, Any], installed_from: Dict[str, Any],
                      event: Dict[str, Any],
                      context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Run an agent_hook PFP entrypoint and return its JSON decision."""
    request = build_agent_hook_invocation(runtime, installed_from, event, context)
    result = _invoke_bridge(request)
    return _normalize_service_result(result)

def resolve_flow_task_runtime(task_type: str, *, user_id: str,
                              conversation_id: str,
                              scope: str = "conversation") -> Dict[str, Any]:
    from core import pfp_package
    try:
        return pfp_package.resolve_installed_flow_task_runtime(
            task_type, user_id=user_id,
            conversation_id=conversation_id, scope=scope)
    except Exception as exc:
        raise PackageRuntimeError(str(exc)) from exc

def _invoke_bridge(request: Dict[str, Any]) -> Any:
    return RelayPackageRuntimeBridge().invoke(request)
