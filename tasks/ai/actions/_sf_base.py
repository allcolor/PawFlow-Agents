"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
from typing import Dict, Any, Optional

from core import FlowFile

logger = logging.getLogger(__name__)

# Sentinel: a cluster handler returns this when `action` is not one it owns.
_UNHANDLED = object()



# Pending OAuth flows (in-memory, keyed by service_id)
_oauth_pending: Dict[str, Dict[str, str]] = {}


def _is_admin(flowfile: FlowFile) -> bool:
    return "admin" in (flowfile.get_attribute("http.auth.roles") or "")


def _service_scope_id(scope: str, user_id: str, conversation_id: str = "") -> str:
    if scope == "global":
        return ""
    if scope == "conv":
        return conversation_id
    return user_id


def _normalize_service_scope(scope: str) -> str:
    if scope in ("conversation", "conv"):
        return "conv"
    if scope == "global":
        return "global"
    return "user"


_SERVICE_CATEGORY_ORDER = {
    "auth": 0,
    "ai": 1,
    "image": 2,
    "video": 3,
    "audio": 4,
    "voice": 5,
    "3d": 6,
    "upscale": 7,
    "try-on": 8,
    "filesystem": 9,
    "automation": 10,
    "messaging": 11,
    "network": 12,
    "data": 13,
    "cache": 14,
    "security": 15,
    "system": 16,
    "other": 99,
}

_SERVICE_CATEGORY_BY_TYPE = {
    "authGateway": "auth",
    "oauthProvider": "auth",
    "llmConnection": "ai",
    "llmCredentialOAuthProvider": "ai",
    "summarizer": "ai",
    "ccInteractiveEvents": "ai",
    "codexImageGeneration": "image",
    "openaiImageGeneration": "image",
    "openaiCompatibleImageGeneration": "image",
    "grokImageGeneration": "image",
    "wavespeedImageGeneration": "image",
    "grokVideoGeneration": "video",
    "klingVideoGeneration": "video",
    "openaiCompatibleVideoGeneration": "video",
    "wavespeedVideoGeneration": "video",
    "sunoAudioGeneration": "audio",
    "supertonicTTS": "audio",
    "voxcpmTTS": "audio",
    "openaiCompatibleSTT": "audio",
    "xaiTTS": "audio",
    "xaiSTT": "audio",
    "wavespeedAudioGeneration": "audio",
    "elevenLabsVoiceClone": "voice",
    "fishAudioVoiceClone": "voice",
    "wavespeedVoiceClone": "voice",
    "wavespeed3DGeneration": "3d",
    "wavespeedUpscale": "upscale",
    "wavespeedTryOn": "try-on",
    "wavespeedLipsync": "video",
    "wavespeedTrainer": "image",
    "rcloneFilesystem": "filesystem",
    "rcloneOAuthCredentials": "filesystem",
    "googleDrive": "filesystem",
    "oneDrive": "filesystem",
    "fileTracking": "filesystem",
    "browser": "automation",
    "telegramBot": "messaging",
    "discordBot": "messaging",
    "slackBot": "messaging",
    "whatsappCloud": "messaging",
    "relay": "network",
    "httpClientService": "network",
    "httpListener": "network",
    "toolRelay": "network",
    "dbConnectionPool": "data",
    "cacheService": "cache",
    "distributedMapCache": "cache",
    "httpAuthValidator": "security",
    "sslContext": "security",
    "privateGateway": "security",
    "packageRuntime": "system",
}

_DISABLED_DIRECT_SERVICE_INSTALL_TYPES = {"filesystem"}

_DISABLED_DIRECT_SERVICE_INSTALL_MESSAGES = {
    "filesystem": "Server filesystem services are disabled. Create a server relay instead.",
}


def _service_category(service_type: str, service_cls: type) -> str:
    category = str(getattr(service_cls, "CATEGORY", "") or "").strip().lower()
    category = {
        "try_on": "try-on",
        "lipsync": "video",
        "trainer": "image",
    }.get(category, category)
    if category in _SERVICE_CATEGORY_ORDER:
        return category
    try:
        from services.base_capabilities import (
            BaseImage3DService, BaseImageUpscaleService, BaseTryOnService,
            BaseLipsyncService, BaseImageTrainerService,
        )
        for base_cls, base_category in (
            (BaseImage3DService, "3d"),
            (BaseImageUpscaleService, "upscale"),
            (BaseTryOnService, "try-on"),
            (BaseLipsyncService, "video"),
            (BaseImageTrainerService, "image"),
        ):
            if issubclass(service_cls, base_cls):
                return base_category
    except Exception:
        logger.debug("Service capability category detection skipped", exc_info=True)
    return _SERVICE_CATEGORY_BY_TYPE.get(service_type, "other")


def _service_type_sort_key(service: Dict[str, Any]):
    category = service.get("category", "other")
    return (_SERVICE_CATEGORY_ORDER.get(category, 99), service.get("name", "").lower(), service.get("type", ""))


def _service_requires_connected_state(service_type: str) -> bool:
    if service_type == "filesystem":
        return True
    return False


def _wait_for_service_connected(reg, scope: str, scope_id: str, service_id: str,
                                timeout_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if reg.is_connected(scope, scope_id, service_id):
            return True
        time.sleep(0.5)
    return reg.is_connected(scope, scope_id, service_id)


def _validate_required_service_config(svc_cls, config: Dict[str, Any]) -> None:
    try:
        instance = object.__new__(svc_cls)
        instance.config = {}
        schema = instance.get_parameter_schema()
    except Exception as exc:
        logger.debug("Service schema validation skipped: %s", exc)
        return
    missing = []
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict) or not spec.get("required"):
            continue
        value = (config or {}).get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    if missing:
        raise ValueError("Missing required service config: " + ", ".join(missing))


def _service_started_for_listing(reg, scope: str, scope_id: str, sid: str,
                                 sdef) -> bool:
    if not getattr(sdef, "enabled", True):
        return False
    # Relays and filesystems have a *live* link whose state is the truth the
    # UI dot must show: the relay panel computes its dot with
    # reg.is_connected() (relay pool has a connection), so the services list
    # has to use the same call or the two dots disagree for the same relay
    # (green "started" in Services, red "not connected" in Relays during the
    # connect window). Stateless services (LLM connections, media, etc.) have
    # no persistent link — for them enabled == started.
    svc_type = getattr(sdef, "service_type", "")
    if svc_type == "relay" or _service_requires_connected_state(svc_type):
        return reg.is_connected(scope, scope_id, sid)
    return True


def _service_install_state_for_listing(scope: str, scope_id: str,
                                       service_id: str) -> Dict[str, Any]:
    try:
        from core.service_install import read_install_state
        state = read_install_state(scope, scope_id, service_id)
        return {k: v for k, v in state.items() if k not in {"scope_id"}}
    except Exception:
        logger.debug("Service install state lookup failed", exc_info=True)
        return {"status": "unknown"}


def _credential_provider_for_service(service_id: str, user_id: str = "") -> str:
    from services.llm_credential_oauth import normalize_provider
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    scopes = [("global", "")]
    if user_id:
        scopes.insert(0, ("user", user_id))
    for scope, sid in scopes:
        sdef = reg.get_definition(scope, sid, service_id)
        if not sdef:
            continue
        cfg = getattr(sdef, "config", {}) or {}
        if sdef.service_type == "llmCredentialOAuthProvider":
            return normalize_provider(cfg.get("provider", ""))
        if sdef.service_type == "llmConnection":
            return normalize_provider(cfg.get("provider", ""))
    return ""


def _credential_module(provider: str):
    from services.llm_credential_oauth import normalize_provider
    provider = normalize_provider(provider)
    if provider == "claude-code":
        from core.llm_providers import claude_code_session as mod
        return mod
    if provider == "codex-app-server":
        from core.llm_providers import codex_session as mod
        return mod
    if provider == "gemini":
        from core.llm_providers import gemini_session as mod
        return mod
    raise ValueError(f"Unsupported credential provider: {provider}")


def _store_claude_tokens(service_id, access_token, refresh_token, expires_at,
                         user_id="", conv_id=""):
    from core.llm_providers.claude_code_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        service_id=service_id, user_id=user_id, conv_id=conv_id)
    logger.info("Claude Code credential added to pool for '%s'", service_id)


def _store_codex_tokens(service_id, access_token, refresh_token, expires_at,  # nosec B107
                        account="", id_token="", user_id="", conv_id=""):
    from core.llm_providers.codex_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        account=account, service_id=service_id, id_token=id_token,
        user_id=user_id, conv_id=conv_id)
    logger.info("Codex credential added to pool for '%s'", service_id)


def _store_gemini_tokens(service_id, access_token, refresh_token, expires_at,
                          account="", user_id="", conv_id=""):
    from core.llm_providers.gemini_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        account=account, service_id=service_id, user_id=user_id,
        conv_id=conv_id)
    logger.info("Gemini credential added to pool for '%s'", service_id)


def _resolve_service_definition_for_action(service_id: str, user_id: str,
                                           conversation_id: str = "",
                                           scope_arg: str = ""):
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    scope_arg = (scope_arg or "").strip()
    if scope_arg:
        scope = _normalize_service_scope(scope_arg)
        return reg.get_definition(
            scope, _service_scope_id(scope, user_id, conversation_id), service_id)
    return reg.resolve_definition(
        service_id, user_id=user_id, conv_id=conversation_id)


_PARAM_SCHEMA_KEYS = {
    "type", "default", "description", "options", "sensitive", "service_type",
    "provider", "provider_field", "required",
}


def _schema_entry_from_value(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict) and _PARAM_SCHEMA_KEYS.intersection(value):
        entry = dict(value)
        entry.setdefault("type", "string")
        return entry
    if isinstance(value, bool):
        return {"type": "boolean", "default": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "integer", "default": value}
    if isinstance(value, float):
        return {"type": "float", "default": value}
    if isinstance(value, (dict, list)):
        return {"type": "object", "default": value}
    return {"type": "string", "default": "" if value is None else value}


def _normalize_flow_parameters(raw_params: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw_params, dict):
        return {}
    if isinstance(raw_params.get("properties"), dict):
        required = set(raw_params.get("required") or [])
        out = {}
        for name, spec in raw_params["properties"].items():
            entry = dict(spec) if isinstance(spec, dict) else _schema_entry_from_value(spec)
            if name in required:
                entry["required"] = True
            out[name] = entry
        return out
    return {name: _schema_entry_from_value(value)
            for name, value in raw_params.items()
            if not str(name).startswith("_")}


def _template_roots(user_id: str, conversation_id: str = ""):
    from core.paths import REPOSITORY_DIR
    roots = []
    if user_id:
        if conversation_id:
            roots.append(REPOSITORY_DIR / "flows" / "users" / user_id / conversation_id)
        roots.append(REPOSITORY_DIR / "flows" / "users" / user_id)
    roots.append(REPOSITORY_DIR / "flows" / "global")
    return roots


def _resolve_flow_template_path(template_id: str, user_id: str,
                                conversation_id: str = ""):
    if not template_id:
        return None
    for root in _template_roots(user_id, conversation_id):
        if not root.is_dir():
            continue
        for latest in root.rglob("latest.json"):
            flow_dir = latest.parent
            try:
                ptr = json.loads(latest.read_text(encoding="utf-8"))
                version = (ptr.get("version") or "").strip()
                if not version:
                    continue
                vfile = flow_dir / "versions" / f"{version}.json"
                if not vfile.is_file():
                    continue
                raw = json.loads(vfile.read_text(encoding="utf-8"))
                if template_id in {
                    raw.get("id") or flow_dir.name,
                    raw.get("name") or "",
                    raw.get("fqn") or "",
                }:
                    return vfile
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                continue
    return None


def _flow_template_storage_info(tpath, user_id: str, conversation_id: str = ""):
    from core.paths import REPOSITORY_DIR

    tpath = tpath.resolve()
    roots = []
    if user_id and conversation_id:
        roots.append(("conversation", REPOSITORY_DIR / "flows" / "users" / user_id / conversation_id))
    if user_id:
        roots.append(("user", REPOSITORY_DIR / "flows" / "users" / user_id))
    roots.append(("global", REPOSITORY_DIR / "flows" / "global"))
    flow_dir = tpath.parent.parent
    for scope, root in roots:
        try:
            rel_parts = flow_dir.resolve().relative_to(root.resolve()).parts
        except ValueError:
            continue
        if not rel_parts:
            break
        package = ".".join(rel_parts[:-1]) if len(rel_parts) > 1 else "default"
        raw = json.loads(tpath.read_text(encoding="utf-8"))
        return {
            "scope": scope,
            "repo_scope": "conv" if scope == "conversation" else scope,
            "root": root,
            "flow_dir": flow_dir,
            "package": raw.get("package") or package,
            "storage_package": package,
            "flow_name": flow_dir.name,
            "version": raw.get("version") or tpath.stem,
            "raw": raw,
        }
    raise ValueError("Template path is outside the flow repository")


def _validate_flow_package_name(package: str) -> str:
    import re

    package = str(package or "").strip().strip(".")
    if not package:
        raise ValueError("Missing package")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", package):
        raise ValueError("Package may only contain letters, numbers, dots, underscores, and dashes")
    if ".." in package:
        raise ValueError("Package cannot contain empty segments")
    return package


def _ensure_template_scope_edit_allowed(flowfile: FlowFile, scope: str) -> Optional[Dict[str, str]]:
    if scope == "global" and not _is_admin(flowfile):
        flowfile.set_attribute("http.response.status", "403")
        return {"error": "Requires admin role for global scope"}
    return None


def _rewrite_flow_template_package(flow_dir, package: str) -> None:
    for version_file in sorted((flow_dir / "versions").glob("*.json")):
        raw = json.loads(version_file.read_text(encoding="utf-8"))
        version = raw.get("version") or version_file.stem
        raw["package"] = package
        raw["fqn"] = f"{package}.{flow_dir.name}:{version}"
        version_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _service_parameter_schema(service_type: str) -> Dict[str, Any]:
    if not service_type:
        return {}
    try:
        from tasks import register_all_tasks
        register_all_tasks()
        from core import ServiceFactory
        cls = ServiceFactory.get(service_type)
        instance = object.__new__(cls)
        instance.config = {}
        schema = instance.get_parameter_schema()
        return schema if isinstance(schema, dict) else {}
    except Exception:
        return {}


def _flow_services_schema(raw: Dict[str, Any], service_overrides=None,
                          service_configs=None) -> Dict[str, Dict[str, Any]]:
    service_overrides = service_overrides or {}
    service_configs = service_configs or {}
    out = {}
    for service_id, service_def in sorted((raw.get("services") or {}).items()):
        if not isinstance(service_def, dict):
            continue
        service_type = service_def.get("type", "") or ""
        default_params = dict(service_def.get("parameters") or {})
        values = dict(default_params)
        values.update(service_configs.get(service_id) or {})
        schema = _service_parameter_schema(service_type)
        if (service_type == "llmConnection" and "model" in values and
                "default_model" in schema and "default_model" not in values):
            values["default_model"] = values["model"]
        schema = dict(schema)
        for key, value in values.items():
            if key in schema:
                continue
            if service_type == "llmConnection" and key == "model" and "default_model" in schema:
                continue
            schema[key] = _schema_entry_from_value(value)
        out[service_id] = {
            "service_id": service_id,
            "service_type": service_type,
            "parameters_schema": schema,
            "parameter_values": values,
            "default_parameters": default_params,
            "override": service_overrides.get(service_id, ""),
        }
    return out


def _flow_deploy_schema_payload(raw: Dict[str, Any], *, parameters=None,
                                service_overrides=None,
                                service_configs=None) -> Dict[str, Any]:
    parameters = parameters or {}
    schema = _normalize_flow_parameters(raw.get("parameters", {}))
    for name, value in parameters.items():
        if str(name).startswith("_") or name in schema:
            continue
        schema[name] = _schema_entry_from_value(value)
    values = {}
    for name, spec in schema.items():
        if isinstance(spec, dict) and "default" in spec:
            values[name] = spec.get("default")
    values.update({k: v for k, v in parameters.items()
                   if not str(k).startswith("_")})
    return {
        "template_id": raw.get("id", ""),
        "name": raw.get("name", raw.get("id", "")),
        "version": raw.get("version", ""),
        "scope": raw.get("scope", "independent"),
        "parameters_schema": schema,
        "parameter_values": values,
        "services": _flow_services_schema(
            raw, service_overrides=service_overrides,
            service_configs=service_configs),
    }


def _flow_one_shot_trigger_payload(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return root one-shot triggers that may be selected at manual start."""
    tasks = raw.get("tasks") or {}
    relations = raw.get("relations") or []
    targets = {rel.get("to") for rel in relations if isinstance(rel, dict)}
    root_ids = [tid for tid in tasks if tid not in targets]
    triggers = []
    has_persistent_sources = False
    try:
        from tasks import register_all_tasks
        register_all_tasks()
        from core import TaskFactory
        for task_id, task_def in tasks.items():
            if not isinstance(task_def, dict):
                continue
            task_type = task_def.get("type", "") or ""
            try:
                task_cls = TaskFactory.get(task_type)
                task = task_cls(task_def.get("parameters") or {})
            except Exception:
                logger.debug("Failed to inspect task %s", task_id, exc_info=True)
                task = None
            if task is not None and getattr(task, "is_persistent_source", False):
                has_persistent_sources = True

        for task_id in root_ids:
            task_def = tasks.get(task_id) or {}
            if not isinstance(task_def, dict):
                continue
            task_type = task_def.get("type", "") or ""
            if task_type in {"inputPort", "outputPort"}:
                continue
            try:
                task_cls = TaskFactory.get(task_type)
                task = task_cls(task_def.get("parameters") or {})
            except Exception:
                logger.debug("Failed to inspect root task %s", task_id, exc_info=True)
                task = None
            if task is None:
                continue
            if getattr(task, "is_persistent_source", False):
                continue
            if not hasattr(task, "has_pending_input"):
                continue
            try:
                has_pending = bool(task.has_pending_input())
            except Exception:
                logger.debug("Failed to inspect pending input for %s", task_id,
                             exc_info=True)
                has_pending = False
            if not has_pending:
                continue
            label = getattr(task, "NAME", "") or task_def.get("name") or task_id
            triggers.append({
                "task_id": task_id,
                "task_type": task_type,
                "label": label,
            })
    except Exception:
        logger.debug("Failed to inspect one-shot triggers", exc_info=True)
    return {
        "one_shot_triggers": triggers,
        "has_persistent_sources": has_persistent_sources,
        "is_one_shot_flow": bool(triggers) and not has_persistent_sources,
    }


def _load_flow_instance_template_raw(inst, user_id: str) -> Dict[str, Any]:
    """Load the template JSON for a deployed instance.

    Older bootstrap deployments can keep a stale legacy flow_path while the
    versioned repository has the real template. Prefer the pinned file when it
    exists, then fall back to repository lookup by flow id/name.
    """
    from pathlib import Path as _Path
    candidates = []
    flow_path = getattr(inst, "flow_path", "") or ""
    if flow_path:
        candidates.append(_Path(flow_path))
    for template_id in (getattr(inst, "flow_fqn", "") or "",
                        getattr(inst, "flow_id", "") or "",
                        getattr(inst, "flow_name", "") or ""):
        tpath = _resolve_flow_template_path(
            template_id, user_id, getattr(inst, "conversation_id", "") or "")
        if tpath:
            candidates.append(tpath)
    for path in candidates:
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            continue
    return {}


def _set_instance_config(inst, parameters=None, service_overrides=None,
                         service_configs=None) -> None:
    if parameters is not None:
        preserved = {k: v for k, v in (inst.parameters or {}).items()
                     if str(k).startswith("_")}
        inst.parameters = {**preserved, **(parameters or {})}
    if service_overrides is not None:
        inst.service_overrides = {k: v for k, v in (service_overrides or {}).items()
                                  if v and v != "local"}
    if service_configs is not None:
        inst.service_configs = {k: v for k, v in (service_configs or {}).items()
                                if isinstance(v, dict)}


def _restart_running_flow_instance(instance_id: str, inst) -> bool:
    """Restart a currently running executor so saved deployment config applies."""
    from core.executor_registry import ExecutorRegistry
    reg = ExecutorRegistry.get_instance()
    ex = reg.get(instance_id)
    was_running = bool(ex and getattr(ex, "is_running", False))
    if not was_running:
        return False
    try:
        ex.stop()
    finally:
        reg.unregister(instance_id)
    reg._restore_instance(
        instance_id, inst.flow_path,
        inst.max_workers, inst.max_retries,
        flow_fqn=getattr(inst, "flow_fqn", "") or "",
        flow_scope=getattr(inst, "flow_scope", "") or "",
        parameters=inst.parameters,
        service_overrides=inst.service_overrides,
        service_configs=inst.service_configs,
        owner=inst.owner or "",
        conversation_id=inst.conversation_id or "",
        agent_name=getattr(inst, "agent_name", "") or "",
    )
    return True


def _service_override_matches(ref: str, scope: str, scope_id: str,
                              service_id: str) -> bool:
    """Return whether a deployment service override targets a service."""
    ref = str(ref or "")
    if scope == "global" and ref == service_id:
        return True
    if ref.startswith("global:"):
        return scope == "global" and ref.split(":", 1)[1] == service_id
    if ref.startswith("user:"):
        parts = ref.split(":", 2)
        return (len(parts) == 3 and scope == "user"
                and parts[1] == scope_id and parts[2] == service_id)
    return False


def _refresh_running_flow_service_bindings(scope: str, scope_id: str,
                                           service_id: str) -> list:
    """Refresh running executors that forward a flow service to this service.

    ServiceRegistry reconnects edited services by replacing the live service
    object. Running flows that had a forwarded service keep the old object
    reference unless we rebind them here.
    """
    from core.deployment_registry import DeploymentRegistry
    from core.executor_registry import ExecutorRegistry
    from core.service_registry import ServiceRegistry

    live = ServiceRegistry.get_instance().get_live_instance(scope, scope_id, service_id)
    if live is None:
        return []
    refreshed = []
    exec_reg = ExecutorRegistry.get_instance()
    deployments = DeploymentRegistry.get_instance().get_all()
    for iid, inst in deployments.items():
        overrides = getattr(inst, "service_overrides", None) or {}
        targets = [
            flow_service_id
            for flow_service_id, ref in overrides.items()
            if _service_override_matches(ref, scope, scope_id, service_id)
        ]
        if not targets:
            continue
        executor = exec_reg.get(iid)
        flow = getattr(executor, "_flow", None) if executor else None
        services = getattr(flow, "services", None)
        if not isinstance(services, dict):
            continue
        for flow_service_id in targets:
            if flow_service_id in services:
                services[flow_service_id] = live
        refreshed.append(iid)
    return refreshed


def _find_http_listener():
    """Find the live HTTPListenerService instance."""
    from core.service_registry import ServiceRegistry
    greg = ServiceRegistry.get_instance()
    for _sid, _sdef in greg.get_all("global", "").items():
        if getattr(_sdef, "service_type", "") == "httpListener":
            svc = greg.get_live_instance("global", "", _sid)
            if svc:
                return svc
    return None
