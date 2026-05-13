"""Runtime safety checks for installed PawFlow Package objects."""

from __future__ import annotations

import json
import hashlib
import os
import base64
import shlex
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict


RUNTIME_INVOKE_FORMAT = "pawflow.package.runtime.invoke.v1"
RUNTIME_RESULT_FORMAT = "pawflow.package.runtime.result.v1"
HOST_CALL_FORMAT = "pawflow.package.runtime.host_call.v1"


class PackageRuntimeError(RuntimeError):
    """Raised when a PFP runtime object cannot be safely prepared."""


class RelayPackageRuntimeBridge:
    """Run package entrypoints inside the conversation default relay."""

    def invoke(self, request: Dict[str, Any]) -> Any:
        if not isinstance(request, dict) or request.get("format") != RUNTIME_INVOKE_FORMAT:
            raise PackageRuntimeError("invalid PFP runtime invocation envelope")
        package = request.get("package") or {}
        runtime = str(package.get("runtime") or "python")
        if runtime != "python":
            raise PackageRuntimeError(f"unsupported PFP runtime: {runtime}")
        runner = str(package.get("runner") or "")
        if runner != "python":
            raise PackageRuntimeError(f"unsupported PFP runtime runner: {runner}")

        relay = self._resolve_relay(request)
        relay_root = self._relay_package_root(package)
        self._deploy_package(relay, package, relay_root)
        relay_request = self._relay_request(request, relay_root)
        output_dir = str((relay_request.get("context") or {}).get("output_dir") or "")
        if output_dir:
            relay.mkdir(output_dir)
        request_file = f".pawflow/request-{uuid.uuid4().hex}.json"
        relay.write_file(
            f"{relay_root}/{request_file}",
            (json.dumps(relay_request, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        controller = ".pawflow/pfp_relay_runner.py"
        entrypoint = package["entrypoint"]
        command = " ".join([
            "python3",
            shlex.quote(controller),
            shlex.quote(request_file),
            shlex.quote(entrypoint),
        ])
        result = relay.exec(relay_root, command, env=self._controller_env(request))
        stdout = str((result or {}).get("stdout") or "")
        stderr = str((result or {}).get("stderr") or "")
        code = int((result or {}).get("returncode") or 0)
        if code != 0:
            detail = f": {stderr.strip()}" if stderr.strip() else ""
            raise PackageRuntimeError(f"PFP relay runner exited with code {code}{detail}")
        lines = [line for line in stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise PackageRuntimeError("PFP relay runner must emit exactly one JSON result line")
        try:
            result = json.loads(lines[0])
        except Exception as exc:
            raise PackageRuntimeError("PFP relay runner did not return JSON") from exc
        if not isinstance(result, dict) or result.get("format") != RUNTIME_RESULT_FORMAT:
            raise PackageRuntimeError("PFP relay runner returned an invalid result envelope")
        self._copy_result_artifacts(relay, relay_request, result)
        return result

    def _resolve_relay(self, request: Dict[str, Any]) -> Any:
        context = request.get("context") or {}
        user_id = str(context.get("user_id") or "")
        conversation_id = str(context.get("conversation_id") or "")
        agent_name = str(context.get("agent_name") or "")
        if not user_id or not conversation_id:
            raise PackageRuntimeError("PFP runtime requires user_id and conversation_id to resolve the default relay")
        from core.relay_bindings import get_default
        relay_id = get_default(conversation_id, agent=agent_name) or ""
        if not relay_id:
            raise PackageRuntimeError("PFP runtime requires a default relay for this conversation")
        from core.service_registry import ServiceRegistry
        relay = ServiceRegistry.get_instance().resolve(relay_id, user_id=user_id, conv_id=conversation_id)
        if relay is None:
            raise PackageRuntimeError(f"PFP default relay is not available: {relay_id}")
        if not hasattr(relay, "exec") or not hasattr(relay, "write_file"):
            raise PackageRuntimeError(f"PFP default relay does not support runtime execution: {relay_id}")
        return relay

    def _relay_package_root(self, package: Dict[str, Any]) -> str:
        package_id = _safe_cache_name(str(package.get("package") or "package"))
        version = _safe_cache_name(str(package.get("version") or "0"))
        digest = str(package.get("hash") or "").replace("sha256:", "")[:16] or "dev"
        return f".pawflow/pfp/packages/{package_id}@{version}-{digest}"

    def _deploy_package(self, relay: Any, package: Dict[str, Any], relay_root: str) -> None:
        content_dir = Path(str(package.get("content_dir") or "")).resolve()
        if not content_dir.is_dir():
            raise PackageRuntimeError("PFP package content directory is missing")
        relay.mkdir(f"{relay_root}/.pawflow")
        relay.write_file(f"{relay_root}/.pawflow/pfp_relay_runner.py", _RELAY_RUNNER.encode("utf-8"))
        sdk_source = Path(__file__).resolve().parents[1] / "docker" / "pawflow_sdk" / "pawflow.py"
        relay.mkdir(f"{relay_root}/.pawflow/sdk")
        relay.write_file(f"{relay_root}/.pawflow/sdk/pawflow.py", sdk_source.read_bytes())
        for path in sorted(content_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(content_dir).as_posix()
            relay.write_file(f"{relay_root}/{rel}", path.read_bytes())

    def _relay_request(self, request: Dict[str, Any], relay_root: str) -> Dict[str, Any]:
        copied = json.loads(json.dumps(request, ensure_ascii=False))
        context = copied.setdefault("context", {})
        if context.get("output_dir"):
            context["server_output_dir"] = context["output_dir"]
            context["output_dir"] = f"{relay_root}/.pawflow/artifacts/{uuid.uuid4().hex}"
        return copied

    def _controller_env(self, request: Dict[str, Any]) -> Dict[str, str]:
        env = _subprocess_env(request)
        from core.handlers._fs_base import get_tool_relay_env
        env.update(get_tool_relay_env())
        env["PAWFLOW_PFP_RELAY_RUNNER"] = "1"
        env["PYTHONPATH"] = ".pawflow/sdk"
        env["PAWFLOW_PFP_SDK_PATH"] = ".pawflow/sdk"
        context = request.get("context") or {}
        env["PAWFLOW_USER_ID"] = str(context.get("user_id") or "")
        env["PAWFLOW_CONVERSATION_ID"] = str(context.get("conversation_id") or "")
        env["PAWFLOW_AGENT_NAME"] = str(context.get("agent_name") or "")
        return env

    def _copy_result_artifacts(self, relay: Any, request: Dict[str, Any],
                               result: Dict[str, Any]) -> None:
        context = request.get("context") or {}
        relay_output_dir = str(context.get("output_dir") or "")
        server_output_dir = str(context.get("server_output_dir") or "")
        if not relay_output_dir or not server_output_dir:
            return
        artifact = _artifact_from_result(result)
        if not artifact:
            return
        rel = _safe_artifact_relpath(str(artifact.get("path") or ""))
        data = relay.read_file(f"{relay_output_dir}/{rel}")
        target = (Path(server_output_dir).resolve() / rel).resolve()
        try:
            target.relative_to(Path(server_output_dir).resolve())
        except ValueError as exc:
            raise PackageRuntimeError("PFP media artifact escapes server output_dir") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


_RELAY_RUNNER = r'''
import json
import os
import subprocess
import sys
import threading

RUNTIME_INVOKE_FORMAT = "pawflow.package.runtime.invoke.v1"
RUNTIME_RESULT_FORMAT = "pawflow.package.runtime.result.v1"
HOST_CALL_FORMAT = "pawflow.package.runtime.host_call.v1"


def _load_request(path):
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    if not isinstance(request, dict) or request.get("format") != RUNTIME_INVOKE_FORMAT:
        raise RuntimeError("invalid PFP invocation envelope")
    return request


def _child_env():
    blocked = {
        "PAWFLOW_TOOL_RELAY_URL",
        "PAWFLOW_TOOL_RELAY_TOKEN",
        "PAWFLOW_PFP_RELAY_RUNNER",
    }
    env = {k: v for k, v in os.environ.items() if k not in blocked}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.environ.get("PAWFLOW_PFP_SDK_PATH", ".pawflow/sdk")
    return env


def _host_response(invocation, host_call):
    try:
        import pawflow
        response = pawflow._request(
            "execute_pfp_host_call",
            invocation=invocation,
            host_call=host_call,
        )
        if isinstance(response, dict) and response.get("format") == RUNTIME_RESULT_FORMAT:
            return response
        return {"format": RUNTIME_RESULT_FORMAT, "ok": True, "result": response}
    except Exception as exc:
        return {"format": RUNTIME_RESULT_FORMAT, "ok": False, "error": str(exc)}


def _emit_result(envelope):
    print(json.dumps(envelope, ensure_ascii=False), flush=True)


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("usage: pfp_relay_runner.py <request.json> <entrypoint.py>")
    request = _load_request(sys.argv[1])
    entrypoint = sys.argv[2]
    proc = subprocess.Popen(
        [sys.executable, entrypoint],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=_child_env(),
    )
    stderr_chunks = []

    def _read_stderr():
        if proc.stderr is None:
            return
        for chunk in proc.stderr:
            stderr_chunks.append(chunk)

    threading.Thread(target=_read_stderr, daemon=True).start()
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    results = []
    invalid_output = []
    for line in proc.stdout:
        text = line.strip()
        if not text:
            continue
        try:
            envelope = json.loads(text)
        except Exception:
            invalid_output.append(text)
            continue
        if not isinstance(envelope, dict):
            invalid_output.append(text)
            continue
        fmt = envelope.get("format")
        if fmt == HOST_CALL_FORMAT:
            proc.stdin.write(json.dumps(_host_response(request, envelope), ensure_ascii=False) + "\n")
            proc.stdin.flush()
            continue
        if fmt == RUNTIME_RESULT_FORMAT:
            results.append(envelope)
            continue
        invalid_output.append(text)
    code = proc.wait()
    if code != 0 and not results:
        detail = "".join(stderr_chunks).strip()
        _emit_result({
            "format": RUNTIME_RESULT_FORMAT,
            "ok": False,
            "error": f"PFP entrypoint exited with code {code}" + (f": {detail}" if detail else ""),
        })
        return
    if invalid_output:
        _emit_result({
            "format": RUNTIME_RESULT_FORMAT,
            "ok": False,
            "error": "PFP entrypoint emitted non-runtime stdout",
        })
        return
    if len(results) != 1:
        _emit_result({
            "format": RUNTIME_RESULT_FORMAT,
            "ok": False,
            "error": "PFP entrypoint must emit exactly one result envelope",
        })
        return
    _emit_result(results[0])


if __name__ == "__main__":
    main()
'''


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
        return handler.execute(call["arguments"])

    def execute_service_call(self, service_ref: str, operation: str,
                             arguments: Dict[str, Any] | None = None) -> Any:
        call = self.build_service_call(service_ref, operation, arguments)
        service = self._resolve_service(call["target"])
        if not hasattr(service, "invoke"):
            raise PackageRuntimeError(
                f"service does not support PFP host invocation: {call['target']['name']}")
        return service.invoke(call["operation"], call["arguments"])

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
        handler = self.tool_registry.get(target["name"])
        if handler is None:
            raise PackageRuntimeError(f"host tool is not available: {target['name']}")
        return handler

    def _resolve_service(self, target: Dict[str, str]) -> Any:
        if self.service_registry is None:
            raise PackageRuntimeError("service_registry is required for PFP host service calls")
        resolver = getattr(self.service_registry, "resolve", None)
        if not resolver:
            raise PackageRuntimeError("service_registry.resolve is required for PFP host service calls")
        service = resolver(target["name"], user_id=self.user_id, conv_id=self.conversation_id)
        if service is None:
            raise PackageRuntimeError(f"host service is not available: {target['name']}")
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


def _invoke_bridge(request: Dict[str, Any]) -> Any:
    return RelayPackageRuntimeBridge().invoke(request)


def _subprocess_env(request: Dict[str, Any]) -> Dict[str, str]:
    sdk_dir = Path(__file__).resolve().parents[1] / "docker" / "pawflow_sdk"
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(sdk_dir),
        "PATH": os.environ.get("PATH", ""),
    }
    env.update(_secret_env_vars(request))
    return env


def _secret_env_vars(request: Dict[str, Any]) -> Dict[str, str]:
    package = request.get("package") if isinstance(request, dict) else {}
    context = request.get("context") if isinstance(request, dict) else {}
    if not isinstance(package, dict):
        return {}
    bindings = package.get("secret_bindings") or {}
    if not isinstance(bindings, dict):
        bindings = {}
    result = {}
    for requirement in package.get("secrets") or []:
        if not isinstance(requirement, dict):
            continue
        name = str(requirement.get("name") or "")
        if not name:
            continue
        bound_key = str(bindings.get(name) or "")
        if not bound_key:
            if requirement.get("required", True):
                raise PackageRuntimeError(f"PFP secret binding is missing: {name}")
            continue
        value = _resolve_secret_value(
            bound_key,
            user_id=str((context or {}).get("user_id") or ""),
            conversation_id=str((context or {}).get("conversation_id") or ""),
        )
        if value is None:
            raise PackageRuntimeError(f"PFP bound secret is not available: {name}")
        env_name = str(requirement.get("env") or _secret_env_name(name))
        result[env_name] = value
    return result


def _resolve_secret_value(secret_key: str, *, user_id: str,
                          conversation_id: str) -> str | None:
    if conversation_id:
        try:
            from core.conversation_store import ConversationStore
            from core.secrets import SecretsManager
            raw = ConversationStore.instance().get_extra(
                conversation_id, "conv_secrets") or {}
            if secret_key in raw:
                value = raw[secret_key]
                sm = SecretsManager.get_instance()
                return sm.decrypt(value) if str(value).startswith("enc:") else str(value)
        except Exception as exc:
            raise PackageRuntimeError("PFP conversation secret could not be loaded") from exc
    if user_id:
        from core.expression import _load_user_secrets
        secrets = _load_user_secrets(user_id)
        if secret_key in secrets:
            return str(secrets[secret_key])
    from core.expression import _load_global_secrets
    secrets = _load_global_secrets()
    if secret_key in secrets:
        return str(secrets[secret_key])
    return None


def _secret_env_name(name: str) -> str:
    clean = "".join(ch if ch.isalnum() else "_" for ch in str(name or "")).upper().strip("_")
    return f"PFP_SECRET_{clean or 'VALUE'}"


def _normalize_tool_result(result: Any) -> str:
    if isinstance(result, dict) and result.get("format") == RUNTIME_RESULT_FORMAT:
        _raise_result_error(result)
        return str(result.get("result", ""))
    return str(result if result is not None else "")


def _normalize_service_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict) and result.get("format") == RUNTIME_RESULT_FORMAT:
        _raise_result_error(result)
        payload = result.get("result", {})
        if not isinstance(payload, dict):
            raise PackageRuntimeError("PFP service runtime result must be an object")
        return payload
    if not isinstance(result, dict):
        raise PackageRuntimeError("PFP service runtime bridge returned a non-object result")
    return result


def _normalize_task_result(result: Any) -> Any:
    if isinstance(result, dict) and result.get("format") == RUNTIME_RESULT_FORMAT:
        _raise_result_error(result)
        flowfiles = result.get("flowfiles", [])
        if not isinstance(flowfiles, list):
            raise PackageRuntimeError("PFP task runtime flowfiles must be a list")
        return [_flowfile_from_payload(item) for item in flowfiles]
    return result


def _raise_result_error(result: Dict[str, Any]) -> None:
    if result.get("ok", True):
        return
    raise PackageRuntimeError(str(result.get("error") or "PFP runtime bridge failed"))


def _invocation_envelope(kind: str, prepared: Dict[str, Any],
                         payload: Dict[str, Any],
                         context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "format": RUNTIME_INVOKE_FORMAT,
        "kind": kind,
        "package": prepared,
        "context": _runtime_context(context or {}),
        "payload": payload,
    }


def _runtime_context(context: Dict[str, Any]) -> Dict[str, str]:
    scope = str(context.get("scope") or "").strip()
    if scope in {"conv", "conversation"}:
        scope = "conversation"
    elif scope != "user":
        scope = "user" if context.get("user_id") else ""
    result = {
        "user_id": str(context.get("user_id") or ""),
        "conversation_id": str(context.get("conversation_id") or ""),
        "scope": scope,
    }
    for key in ("output_dir", "max_artifact_bytes"):
        if context.get(key) is not None:
            result[key] = str(context.get(key) or "")
    return result


def _list_value(value: Any) -> list:
    return value if isinstance(value, list) else []


def _caller_identity(runtime: Dict[str, Any]) -> Dict[str, str]:
    return {
        "package": str(runtime.get("package") or ""),
        "version": str(runtime.get("version") or ""),
        "object_id": str(runtime.get("object_id") or ""),
    }


def _target_ref(target: Any) -> str:
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        kind = str(target.get("kind") or "")
        name = str(target.get("name") or "")
        package = str(target.get("package") or "")
        version = str(target.get("version") or "")
        if not kind or not name:
            raise PackageRuntimeError("PFP host-call target requires kind and name")
        object_ref = f"{kind}:{name}"
        if package:
            package_ref = f"{package}@{version}" if version else package
            return f"{package_ref}/{object_ref}"
        return name
    return ""


def _artifact_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = result.get("result") if isinstance(result, dict) else None
    if isinstance(payload, dict) and isinstance(payload.get("artifact"), dict):
        return payload["artifact"]
    return {}


def _safe_artifact_relpath(value: str) -> str:
    rel = str(value or "").replace("\\", "/").strip("/")
    if not rel:
        raise PackageRuntimeError("PFP media artifact.path is required")
    parsed = PurePosixPath(rel)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise PackageRuntimeError("PFP media artifact.path must be relative to output_dir")
    return rel


def _flowfile_descriptor(flowfile: Any) -> Dict[str, Any]:
    if flowfile is None:
        return {}
    attributes = getattr(flowfile, "attributes", {}) or {}
    content = b""
    try:
        content = flowfile.get_content() if hasattr(flowfile, "get_content") else getattr(flowfile, "content", b"")
    except Exception:
        content = b""
    content = content or b""
    return {
        "attributes": dict(attributes),
        "content_size": len(content),
        "content_b64": base64.b64encode(content).decode("ascii"),
    }


def _flowfile_from_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise PackageRuntimeError("PFP task runtime flowfile must be an object")
    content_b64 = str(payload.get("content_b64") or "")
    try:
        content = base64.b64decode(content_b64.encode("ascii"), validate=True)
    except Exception as exc:
        raise PackageRuntimeError("PFP task runtime flowfile content_b64 is invalid") from exc
    attributes = payload.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise PackageRuntimeError("PFP task runtime flowfile attributes must be an object")
    from core import FlowFile
    return FlowFile(content=content, attributes={str(k): str(v) for k, v in attributes.items()})


def _safe_entrypoint(value: str) -> str:
    rel = value.replace("\\", "/").strip("/")
    if not rel:
        raise PackageRuntimeError("PFP runtime entrypoint is required")
    parsed = PurePosixPath(rel)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise PackageRuntimeError(f"Unsafe PFP runtime entrypoint: {value}")
    return rel


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()
