"""Secret-env, result-normalization and package/flowfile resolution helpers.

Split out of core/pfp_runtime.py for the <=800-line rule; re-exported from
core.pfp_runtime (invariant 1: import-path stability).
"""

from __future__ import annotations
import logging
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Any, Dict

from core.pfp_runtime._base import PackageRuntimeError, RUNTIME_INVOKE_FORMAT, RUNTIME_RESULT_FORMAT

logger = logging.getLogger(__name__)


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

def _is_blocked_builtin_service_operation(operation: str) -> bool:
    operation = str(operation or "").strip()
    if not operation or operation.startswith("_"):
        return True
    blocked = {
        "connect", "disconnect", "is_connected", "status", "validate",
        "get_parameter_schema", "ensure_connected", "reset", "close",
        "open", "start", "stop", "shutdown", "restart",
        "set_runtime_context", "set_user_id", "set_conversation_id",
        "set_agent_name",
    }
    if operation in blocked:
        return True
    return operation.startswith(("ensure_", "set_"))

def _json_safe_service_result(result: Any, service_name: str, operation: str) -> Any:
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PackageRuntimeError(
            f"service operation returned a non-JSON result: {service_name}.{operation}") from exc
    return result

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
    for key in ("agent_name", "output_dir", "max_artifact_bytes", "relay_id"):
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

def _runtime_metadata(target: Any) -> Dict[str, Any]:
    runtime = getattr(target, "_package_runtime", None)
    if isinstance(runtime, dict):
        return runtime
    config = getattr(target, "config", None)
    if isinstance(config, dict) and isinstance(config.get("package_runtime"), dict):
        return config["package_runtime"]
    return {}

def _required_object_ids(target: Dict[str, str]) -> set[str]:
    kind = str(target.get("kind") or "")
    name = str(target.get("name") or "")
    object_ids = {f"{kind}:{name}"}
    if kind == "service":
        object_ids.add(f"service_provider:{name}")
    return object_ids

def _runtime_matches_target(runtime: Dict[str, Any], target: Dict[str, str]) -> bool:
    if str(runtime.get("package") or "") != str(target.get("package") or ""):
        return False
    version_constraint = str(target.get("version") or "")
    if version_constraint:
        from core.pfp_capabilities import _version_satisfies
        if not _version_satisfies(str(runtime.get("version") or ""), version_constraint):
            return False
    return str(runtime.get("object_id") or "") in _required_object_ids(target)

def _require_runtime_target(runtime_target: Any, target: Dict[str, str]) -> None:
    package = str(target.get("package") or "")
    if not package:
        return
    runtime = _runtime_metadata(runtime_target)
    if not runtime:
        raise PackageRuntimeError(
            f"host {target.get('kind', '')} is not a PFP package runtime: {target.get('name', '')}")
    if not _runtime_matches_target(runtime, target):
        raise PackageRuntimeError(
            f"host {target.get('kind', '')} does not match package target: {package}/{target.get('kind', '')}:{target.get('name', '')}")

def _resolve_package_service(service_registry: Any, target: Dict[str, str], *,
                             user_id: str, conversation_id: str) -> Any:
    resolver = getattr(service_registry, "resolve_by_type", None)
    if not callable(resolver):
        return None
    for definition in resolver("packageRuntime", user_id=user_id, conv_id=conversation_id):
        runtime = _runtime_metadata(definition)
        if not runtime:
            runtime = _runtime_metadata(getattr(definition, "config", {}))
        if not _runtime_matches_target(runtime, target):
            continue
        service_id = str(getattr(definition, "service_id", "") or "")
        if not service_id:
            continue
        scoped_getter = getattr(service_registry, "get_live_instance", None)
        if callable(scoped_getter):
            live = scoped_getter(
                str(getattr(definition, "scope", "") or ""),
                str(getattr(definition, "scope_id", "") or ""),
                service_id,
            )
        else:
            live = service_registry.resolve(service_id, user_id=user_id, conv_id=conversation_id)
        if live is not None:
            return live
    return None

def _parent_conversation_ids(conversation_id: str) -> list[str]:
    conversation_id = str(conversation_id or "")
    ids = [conversation_id] if conversation_id else []
    for marker in ("::task::", "::task_verify::", "::delegate::", "::flash::"):
        if conversation_id and marker in conversation_id:
            parent = conversation_id.split(marker, 1)[0]
            if parent and parent not in ids:
                ids.append(parent)
            break
    return ids

def _filter_conversation_id(conversation_id: str) -> str:
    conversation_id = str(conversation_id or "")
    for marker in ("::task::", "::task_verify::", "::delegate::", "::flash::"):
        if marker in conversation_id:
            return conversation_id.split(marker, 1)[0]
    return conversation_id

def _resolve_package_tool_from_store(target: Dict[str, str], *,
                                     user_id: str,
                                     conversation_id: str,
                                     agent_name: str = "") -> Any:
    if not user_id:
        return None
    name = str(target.get("name") or "")
    try:
        from core.repository import ScopedRepository
        from core.handlers.dynamic_tool import PfpToolProxyHandler
    except Exception:
        return None
    repo = ScopedRepository.instance()
    scoped_entries = []
    for cid in _parent_conversation_ids(conversation_id):
        try:
            for entry in repo.list("tools", "conv", user_id=user_id, conv_id=cid) or []:
                scoped_entries.append((entry, "conversation"))
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    try:
        for entry in repo.list("tools", "user", user_id=user_id) or []:
            scoped_entries.append((entry, "user"))
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    try:
        for entry in repo.list("tools", "global") or []:
            scoped_entries.append((entry, "global"))
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    filter_cid = conversation_id
    for entry, origin_scope in scoped_entries:
        if str(entry.get("name") or "") != name:
            continue
        if filter_cid:
            try:
                from core.tool_mcp_filters import is_tool_enabled
                if not is_tool_enabled(
                        filter_cid, name, agent_name, "dynamic", origin_scope):
                    continue
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        runtime = entry.get("package_runtime") or {}
        if _runtime_matches_target(runtime, target):
            return PfpToolProxyHandler(
                tool_name=name,
                tool_description=entry.get("description", ""),
                tool_parameters=entry.get("parameters", {}) or {},
                package_runtime=runtime,
                installed_from=entry.get("installed_from", {}) or {},
            )
    return None

def _resolve_package_tool(tool_registry: Any, target: Dict[str, str], *,
                          user_id: str = "",
                          conversation_id: str = "",
                          agent_name: str = "") -> Any:
    candidates = []
    direct_getter = getattr(tool_registry, "get", None)
    if callable(direct_getter):
        direct = direct_getter(str(target.get("name") or ""))
        if direct is not None:
            candidates.append(direct)
    lister = getattr(tool_registry, "list_tools", None)
    if callable(lister):
        candidates.extend(lister() or [])
    seen = set()
    for handler in candidates:
        marker = id(handler)
        if marker in seen:
            continue
        seen.add(marker)
        runtime = _runtime_metadata(handler)
        if _runtime_matches_target(runtime, target):
            return handler
    return _resolve_package_tool_from_store(
        target, user_id=user_id, conversation_id=conversation_id,
        agent_name=agent_name)

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
    content_ref = getattr(flowfile, "_content_ref", None)
    if content_ref is not None and bool(getattr(content_ref, "is_on_disk", False)):
        content_path = getattr(content_ref, "_file_path", None)
        if content_path and Path(content_path).is_file():
            return {
                "attributes": dict(attributes),
                "content_size": int(getattr(content_ref, "size", 0) or 0),
                "_content_path": str(content_path),
            }
    content = b""
    try:
        content = flowfile.get_content() if hasattr(flowfile, "get_content") else getattr(flowfile, "content", b"")
    except Exception:
        content = b""
    content = content or b""
    return {
        "attributes": dict(attributes),
        "content_size": len(content),
        "_content_bytes": bytes(content),
    }

def _flowfile_from_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        raise PackageRuntimeError("PFP task runtime flowfile must be an object")
    attributes = payload.get("attributes") or {}
    if not isinstance(attributes, dict):
        raise PackageRuntimeError("PFP task runtime flowfile attributes must be an object")
    if isinstance(payload.get("_content_bytes"), (bytes, bytearray)):
        content = bytes(payload["_content_bytes"])
    elif payload.get("content_path"):
        if payload.get("_delete_content_path"):
            path = _flowfile_content_path(payload)
            from core import FlowFile
            from core.stream import ContentReference
            return FlowFile(
                attributes={str(k): str(v) for k, v in attributes.items()},
                _content_ref=ContentReference(file_path=path, size=path.stat().st_size),
            )
        content = _flowfile_content_from_path(payload)
    else:
        raise PackageRuntimeError("PFP task runtime flowfile requires content_path")
    from core import FlowFile
    return FlowFile(content=content, attributes={str(k): str(v) for k, v in attributes.items()})

def _flowfile_content_path(payload: Dict[str, Any]) -> Path:
    path = Path(str(payload.get("content_path") or "")).expanduser().resolve()
    root_value = str(payload.get("content_root") or "").strip()
    if root_value:
        root = Path(root_value).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise PackageRuntimeError("PFP task runtime flowfile content_path escapes content_root") from exc
    if not path.is_file():
        raise PackageRuntimeError("PFP task runtime flowfile content_path is missing")
    return path

def _flowfile_content_from_path(payload: Dict[str, Any]) -> bytes:
    path = _flowfile_content_path(payload)
    content = path.read_bytes()
    if payload.get("_delete_content_path"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return content

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
