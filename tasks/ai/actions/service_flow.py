"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _publish_command_result(conversation_id: str, result: dict):
    """Publish a command result via SSE (background thread → frontend)."""
    from core.conversation_event_bus import ConversationEventBus
    bus = ConversationEventBus.instance()
    if "error" in result:
        bus.publish_event(conversation_id, "command_result", {"error": result["error"]})
    else:
        bus.publish_event(conversation_id, "command_result",
                          {"result": json.dumps(result, ensure_ascii=False)})


def _notify_remote_mounts_after_service_change(sdef, conversation_id: str, user_id: str) -> None:
    if not conversation_id or not user_id:
        return
    if getattr(sdef, "service_type", "") not in {"rcloneFilesystem", "rcloneOAuthCredentials"}:
        return
    try:
        from core.remote_fs_bindings import notify_linked_relays
        notify_linked_relays(conversation_id, user_id)
    except Exception:
        logger.debug("Remote FS relay notification after service update failed", exc_info=True)

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
    "codexImageGeneration": "image",
    "openaiImageGeneration": "image",
    "grokImageGeneration": "image",
    "wavespeedImageGeneration": "image",
    "grokVideoGeneration": "video",
    "klingVideoGeneration": "video",
    "soraVideoGeneration": "video",
    "wavespeedVideoGeneration": "video",
    "sunoAudioGeneration": "audio",
    "supertonicTTS": "audio",
    "wavespeedAudioGeneration": "audio",
    "elevenLabsVoiceClone": "voice",
    "fishAudioVoiceClone": "voice",
    "wavespeedVoiceClone": "voice",
    "wavespeed3DGeneration": "3d",
    "wavespeedUpscale": "upscale",
    "wavespeedTryOn": "try-on",
    "wavespeedLipsync": "video",
    "wavespeedTrainer": "image",
    "filesystem": "filesystem",
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
    "httpClientService": "network",
    "httpListener": "network",
    "toolRelay": "network",
    "dbConnectionPool": "data",
    "cacheService": "cache",
    "distributedMapCache": "cache",
    "httpAuthValidator": "security",
    "sslContext": "security",
    "privateGateway": "security",
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
    return _SERVICE_CATEGORY_BY_TYPE.get(service_type, "other")


def _service_type_sort_key(service: Dict[str, Any]):
    category = service.get("category", "other")
    return (_SERVICE_CATEGORY_ORDER.get(category, 99), service.get("name", "").lower(), service.get("type", ""))


def _service_requires_connected_state(service_type: str) -> bool:
    try:
        from core import ServiceFactory
        from services.base_tts import BaseTTSService
        cls = ServiceFactory.get(service_type)
        if issubclass(cls, BaseTTSService):
            return True
    except Exception:
        pass
    return False


def _service_started_for_listing(reg, scope: str, scope_id: str, sid: str,
                                 sdef) -> bool:
    if not getattr(sdef, "enabled", True):
        return False
    if not _service_requires_connected_state(getattr(sdef, "service_type", "")):
        return True
    return reg.is_connected(scope, scope_id, sid)


def _credential_provider_for_service(service_id: str, user_id: str = "") -> str:
    from services.llm_credential_oauth import normalize_provider
    from core.service_registry import ServiceRegistry
    reg = ServiceRegistry.get_instance()
    for scope, sid in (("global", ""), ("user", user_id)):
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


def _store_claude_tokens(service_id, access_token, refresh_token, expires_at):
    from core.llm_providers.claude_code_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        service_id=service_id)
    logger.info("Claude Code credential added to pool for '%s'", service_id)


def _store_codex_tokens(service_id, access_token, refresh_token, expires_at,  # nosec B107
                        account="", id_token=""):
    from core.llm_providers.codex_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        account=account, service_id=service_id, id_token=id_token)
    logger.info("Codex credential added to pool for '%s'", service_id)


def _store_gemini_tokens(service_id, access_token, refresh_token, expires_at,
                          account=""):
    from core.llm_providers.gemini_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        account=account, service_id=service_id)
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



def _handle_service_flow(self, action, body, store, user_id, flowfile):
    """Handle service flow actions. Returns [flowfile] or None."""


    if action == "list_summarizers":
        conv_id = body.get("conversation_id", "") or ""
        try:
            from core.summarizer_bindings import summary as _summarizer_summary
            flowfile.set_content(json.dumps(
                _summarizer_summary(user_id, conv_id),
                ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "link_summarizer":
        conv_id = body.get("conversation_id", "") or ""
        scope = body.get("scope", "") or ""
        service_id = body.get("service_id", "") or ""
        if not conv_id or not scope or not service_id:
            flowfile.set_content(json.dumps({
                "error": "conversation_id, scope and service_id are required",
            }).encode())
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            scope_id = conv_id if scope == "conv" else user_id if scope == "user" else ""
            sdef = reg.get_definition(scope, scope_id, service_id)
            if not sdef or sdef.service_type != "summarizer" or not sdef.enabled:
                flowfile.set_content(json.dumps({
                    "error": "Summarizer service not found or disabled",
                }).encode())
                return [flowfile]
            from core.summarizer_bindings import set_binding, summary as _summarizer_summary
            set_binding(conv_id, scope, service_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "summarizer": _summarizer_summary(user_id, conv_id),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "unlink_summarizer":
        conv_id = body.get("conversation_id", "") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.summarizer_bindings import clear_binding, summary as _summarizer_summary
            clear_binding(conv_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "summarizer": _summarizer_summary(user_id, conv_id),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_services":
        # Canonical service listing. Optional `service_type` filter returns
        # only services of that type (e.g. 'llmConnection', 'tool_relay_service').
        # Consumers needing a subset (LLM dropdowns, relay pickers, etc.) call
        # this action with the appropriate filter — never embedded inside
        # unrelated actions.
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            filter_type = body.get("service_type", "") or ""
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            services = []
            for sid, sdef in sorted(reg.get_all("global", "").items()):
                if filter_type and sdef.service_type != filter_type:
                    continue
                _enabled = getattr(sdef, "enabled", True)
                try:
                    _started = _service_started_for_listing(reg, "global", "", sid, sdef)
                except Exception:
                    _started = False
                services.append({
                    "service_id": sid,
                    "ref": f"global:{sid}",
                    "service_type": sdef.service_type,
                    "enabled": _enabled,
                    "started": _started,
                    "description": sdef.description,
                    "scope": "global",
                    "provider": (sdef.config or {}).get("provider", ""),
                })
            for sid, sdef in sorted(reg.get_all("user", user_id).items()):
                if filter_type and sdef.service_type != filter_type:
                    continue
                try:
                    _started = _service_started_for_listing(reg, "user", user_id, sid, sdef)
                except Exception:
                    _started = False
                entry = {
                    "service_id": sid,
                    "ref": f"user:{user_id}:{sid}",
                    "service_type": sdef.service_type,
                    "enabled": sdef.enabled,
                    "started": _started,
                    "description": sdef.description,
                    "scope": "user",
                    "provider": (sdef.config or {}).get("provider", ""),
                }
                svc = reg.get_live_instance_cached("user", user_id, sid) if sdef.enabled else None
                if svc and hasattr(svc, '_relay_info') and svc._relay_info:
                    entry["relay_info"] = svc._relay_info
                elif sdef.config and sdef.config.get("docker_image"):
                    entry["relay_info"] = {
                        "containerized": True,
                        "docker_image": sdef.config["docker_image"],
                    }
                services.append(entry)
            if conv_id:
                for sid, sdef in sorted(reg.get_all("conv", conv_id).items()):
                    if filter_type and sdef.service_type != filter_type:
                        continue
                    try:
                        _started = _service_started_for_listing(reg, "conv", conv_id, sid, sdef)
                    except Exception:
                        _started = False
                    services.append({
                        "service_id": sid,
                        "ref": f"conv:{conv_id}:{sid}",
                        "service_type": sdef.service_type,
                        "enabled": sdef.enabled,
                        "started": _started,
                        "description": sdef.description,
                        "scope": "conv",
                        "provider": (sdef.config or {}).get("provider", ""),
                    })
            flowfile.set_content(json.dumps({
                "services": services,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_service_types":
        from core import ServiceFactory
        types = []
        for stype in ServiceFactory.list_types():
            try:
                cls = ServiceFactory.get(stype)
                types.append({
                    "type": stype,
                    "name": getattr(cls, "NAME", stype),
                    "description": getattr(cls, "DESCRIPTION", ""),
                    "category": _service_category(stype, cls),
                })
            except Exception:
                types.append({"type": stype, "name": stype, "description": "", "category": "other"})
        types.sort(key=_service_type_sort_key)
        flowfile.set_content(json.dumps({"service_types": types}).encode())
        return [flowfile]

    if action == "get_service_schema":
        svc_type = body.get("service_type", "")
        if not svc_type:
            flowfile.set_content(json.dumps({"error": "Missing service_type"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core import ServiceFactory
            cls = ServiceFactory.get(svc_type)
            instance = object.__new__(cls)
            instance.config = {}
            schema = instance.get_parameter_schema()
            rules = instance.get_parameter_rules() if hasattr(instance, 'get_parameter_rules') else []
            actions = instance.get_service_actions() if hasattr(instance, 'get_service_actions') else []
            flowfile.set_content(json.dumps({
                "type": svc_type,
                "name": getattr(cls, "NAME", svc_type),
                "description": getattr(cls, "DESCRIPTION", ""),
                "parameters": schema,
                "rules": rules,
                "actions": actions,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "404")
        return [flowfile]

    if action == "service_install":
        try:
            svc_type = body.get("service_type", "")
            svc_name = body.get("service_name", "")
            config_str = body.get("config_str", "")
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            requested_scope = body.get("scope", "") or ""
            agent_name = (
                body.get("_agent_name", "")
                or body.get("call_agent_name", "")
                or flowfile.get_attribute("call_agent_name")
                or flowfile.get_attribute("agent_name")
                or ""
            )
            scope = requested_scope or ("conversation" if conv_id and agent_name else "user")
            if conv_id and agent_name:
                scope = "conversation"
            profile_name = body.get("profile", "")
            # Profile shortcut: resolve provider/base_url/model from profile
            if profile_name:
                from core.llm_profiles import apply_profile
                try:
                    profile_config = apply_profile(profile_name)
                    svc_type = svc_type or "llmConnection"
                    svc_name = svc_name or profile_name
                except ValueError as pe:
                    flowfile.set_content(json.dumps({"error": str(pe)}).encode())
                    return [flowfile]
            else:
                profile_config = {}
            if not svc_type or not svc_name:
                flowfile.set_content(json.dumps({
                    "error": "Usage: /service install <type> <name> [key=val,...]",
                }).encode())
                return [flowfile]
            if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            # Accept config as dict or as "key=val,key2=val2" string
            config = body.get("config", {})
            if not config and config_str:
                for pair in config_str.split(","):
                    pair = pair.strip()
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        config[k.strip()] = v.strip()
            # Merge: profile_config is base, explicit config wins
            if profile_config:
                merged = dict(profile_config)
                merged.update(config)
                config = merged
            description = body.get("description", "")
            from core.service_registry import ServiceRegistry, SCOPE_GLOBAL, SCOPE_USER, SCOPE_CONV
            reg = ServiceRegistry.get_instance()
            if scope == "global":
                scope_id = ""
            elif scope == "conversation" or scope == "conv":
                scope_id = conv_id or ""
                scope = "conv"
            else:
                scope_id = user_id
                scope = "user"
            reg.install(scope, scope_id, service_id=svc_name,
                        service_type=svc_type, config=config,
                        description=description)
            if _service_requires_connected_state(svc_type) and not reg.is_connected(scope, scope_id, svc_name):
                reg.uninstall(scope, scope_id, svc_name)
                flowfile.set_content(json.dumps({
                    "error": f"Service '{svc_name}' did not start. Check server logs for the provider error.",
                }).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "installed": True, "id": svc_name, "type": svc_type,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action in ("llm_credential_pool_list", "claude_pool_list"):
        svc_id = body.get("service_id", "")
        provider = _credential_provider_for_service(svc_id, user_id) or "claude-code"
        mod = _credential_module(provider)
        pool = mod._load_credentials_pool(svc_id)
        import time as _time
        entries = []
        now = _time.time()
        for i, cred in enumerate(pool):
            exp = int(cred.get("expires_at", 0) or 0)
            exp_s = exp / 1000 if exp > 1e12 else exp
            remaining = exp_s - now if exp_s else 0
            entries.append({
                "index": i,
                "account": cred.get("account", ""),
                "valid": remaining > 0 and bool(cred.get("refresh_token")),
                "expires_in": f"{remaining/3600:.1f}h" if remaining > 0 else "expired",
                "added_at": cred.get("added_at", 0),
            })
        flowfile.set_content(json.dumps({
            "provider": provider,
            "pool": entries,
            "count": len(entries),
            "message": f"{len(entries)} credential(s) in pool for {svc_id or provider}",
        }).encode())
        return [flowfile]

    if action in ("llm_credential_pool_reset", "claude_pool_reset"):
        svc_id = body.get("service_id", "")
        provider = _credential_provider_for_service(svc_id, user_id) or "claude-code"
        mod = _credential_module(provider)
        mod.reset_credentials_pool(svc_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Credentials pool cleared for {svc_id or provider}.",
        }).encode())
        return [flowfile]

    if action in ("llm_credential_pool_remove", "claude_pool_remove"):
        svc_id = body.get("service_id", "")
        idx = int(body.get("index", -1))
        provider = _credential_provider_for_service(svc_id, user_id) or "claude-code"
        mod = _credential_module(provider)
        if mod.remove_credential_from_pool(idx, svc_id):
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Credential {idx} removed from pool.",
            }).encode())
        else:
            flowfile.set_content(json.dumps({
                "error": f"Invalid index {idx}.",
            }).encode())
        return [flowfile]

    if action == "llm_credential_pool_refresh":
        svc_id = body.get("service_id", "")
        idx = int(body.get("index", -1))
        provider = _credential_provider_for_service(svc_id, user_id)
        if not svc_id or idx < 0 or not provider:
            flowfile.set_content(json.dumps({"error": "Missing service_id/provider or invalid index"}).encode())
            return [flowfile]
        mod = _credential_module(provider)
        pool = mod._load_credentials_pool(svc_id)
        if idx >= len(pool):
            flowfile.set_content(json.dumps({"error": f"Invalid index {idx}"}).encode())
            return [flowfile]
        refresh_token = pool[idx].get("refresh_token", "")
        if not refresh_token:
            flowfile.set_content(json.dumps({"error": f"Credential {idx} has no refresh token"}).encode())
            return [flowfile]
        try:
            if provider == "claude-code":
                tokens = mod.ClaudeCodeSessionMixin._refresh_oauth_token(refresh_token)
                mod._persist_tokens_to_service(
                    tokens.get("access_token", ""),
                    tokens.get("refresh_token", refresh_token),
                    tokens.get("expires_at", 0),
                    service_id=svc_id,
                    pool_index=idx)
            elif provider == "codex-app-server":
                tokens = mod.refresh_oauth_token(refresh_token)
                mod._persist_tokens_to_service(
                    tokens.get("access_token", ""),
                    tokens.get("refresh_token", refresh_token),
                    tokens.get("expires_at", 0),
                    service_id=svc_id,
                    pool_index=idx,
                    account=pool[idx].get("account", ""),
                    id_token=pool[idx].get("id_token", ""))
            else:
                tokens = mod.refresh_oauth_token(refresh_token)
                mod._persist_tokens_to_service(
                    tokens.get("access_token", ""),
                    tokens.get("refresh_token", refresh_token),
                    tokens.get("expires_at", 0),
                    service_id=svc_id,
                    pool_index=idx,
                    account=pool[idx].get("account", ""))
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Credential {idx} refreshed.",
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "llm_rotate":
        svc_id = body.get("service_id", "")
        conv_id = body.get("conversation_id", "")
        if not svc_id:
            flowfile.set_content(json.dumps({"error": "Usage: /llm rotate <service>"}).encode())
            return [flowfile]
        # Find the service
        svc = None
        try:
            from core.service_registry import ServiceRegistry
            svc = ServiceRegistry.get_instance().resolve(svc_id, user_id=user_id)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not svc:
            flowfile.set_content(json.dumps({"error": f"Service '{svc_id}' not found"}).encode())
            return [flowfile]
        # Rotate API key pool
        if hasattr(svc, 'rotate_key'):
            new_idx = svc.rotate_key(conv_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Rotated to key index {new_idx} for {svc_id}.",
                "index": new_idx,
            }).encode())
        # Also rotate CC credentials pool
        elif hasattr(svc, 'provider') and svc.provider == 'claude-code':
            from core.llm_providers.claude_code_session import _load_credentials_pool, ClaudeCodeSessionMixin
            pool = _load_credentials_pool(svc_id)
            if pool:
                with ClaudeCodeSessionMixin._pool_lock:
                    new_idx = ClaudeCodeSessionMixin._pool_counter % len(pool)
                    ClaudeCodeSessionMixin._pool_counter += 1
                if conv_id:
                    try:
                        from core.conversation_store import ConversationStore
                        store = ConversationStore.instance()
                        store.set_extra(conv_id, f"claude_pool_idx:{svc_id}", new_idx)
                        # Invalidate CC session (new credential = new session)
                        store.invalidate_claude_sessions(conv_id)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                flowfile.set_content(json.dumps({
                    "ok": True,
                    "message": f"Rotated to credential {new_idx} for {svc_id}. Session invalidated.",
                    "index": new_idx,
                }).encode())
            else:
                flowfile.set_content(json.dumps({"error": "No credentials pool configured"}).encode())
        else:
            flowfile.set_content(json.dumps({"error": "Service has no key pool"}).encode())
        return [flowfile]

    if action == "service_uninstall":
        try:
            svc_id = body.get("service_id", "")
            scope = _normalize_service_scope(body.get("scope", "user"))
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            if not svc_id:
                flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
                return [flowfile]
            if scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            from core.service_registry import ServiceRegistry
            registry = ServiceRegistry.get_instance()
            scope_id = _service_scope_id(scope, user_id, conv_id)
            if not registry.get_definition(scope, scope_id, svc_id):
                flowfile.set_content(json.dumps({"error": f"Service '{svc_id}' not found."}).encode())
                return [flowfile]
            registry.uninstall(scope, scope_id, svc_id)
            flowfile.set_content(json.dumps({
                "uninstalled": True, "id": svc_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "service_enable":
        try:
            from core.service_registry import ServiceRegistry
            registry = ServiceRegistry.get_instance()
            svc_id = body.get("service_id", "")
            scope = _normalize_service_scope(body.get("scope", "user"))
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            if scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            scope_id = _service_scope_id(scope, user_id, conv_id)
            if not registry.get_definition(scope, scope_id, svc_id):
                flowfile.set_content(json.dumps({
                    "error": f"Service '{svc_id}' not found.",
                }).encode())
                return [flowfile]
            registry.enable(scope, scope_id, svc_id)
            flowfile.set_content(json.dumps({
                "enabled": True, "id": svc_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "service_disable":
        try:
            from core.service_registry import ServiceRegistry
            registry = ServiceRegistry.get_instance()
            svc_id = body.get("service_id", "")
            scope = _normalize_service_scope(body.get("scope", "user"))
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            if scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            scope_id = _service_scope_id(scope, user_id, conv_id)
            if not registry.get_definition(scope, scope_id, svc_id):
                flowfile.set_content(json.dumps({
                    "error": f"Service '{svc_id}' not found.",
                }).encode())
                return [flowfile]
            registry.disable(scope, scope_id, svc_id)
            flowfile.set_content(json.dumps({
                "disabled": True, "id": svc_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "get_service_detail":
        sid = body.get("service_id", "")
        scope = _normalize_service_scope(body.get("scope", "global"))
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if not sid:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            sdef = reg.get_definition(scope, _service_scope_id(scope, user_id, conv_id), sid)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{sid}' not found"}).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "service_id": sid,
                "service_type": getattr(sdef, "service_type", ""),
                "config": getattr(sdef, "config", {}),
                "enabled": getattr(sdef, "enabled", True),
                "description": getattr(sdef, "description", ""),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "update_service":
        sid = body.get("service_id", "")
        scope = _normalize_service_scope(body.get("scope", "global"))
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        config = body.get("config", {})
        if not sid:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        if scope == "global" and not _is_admin(flowfile):
            flowfile.set_content(json.dumps({"error": "Only admin can modify global services"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            registry = ServiceRegistry.get_instance()
            scope_id = _service_scope_id(scope, user_id, conv_id)
            sdef = registry.get_definition(scope, scope_id, sid)
            registry.update_config(scope, scope_id, sid, config)
            if sdef:
                _notify_remote_mounts_after_service_change(sdef, conv_id, user_id)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "toggle_service":
        sid = body.get("service_id", "")
        scope = _normalize_service_scope(body.get("scope", "user"))
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        enabled = body.get("enabled", True)
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            if scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            scope_id = _service_scope_id(scope, user_id, conv_id)
            if enabled:
                reg.enable(scope, scope_id, sid)
            else:
                reg.disable(scope, scope_id, sid)
            flowfile.set_content(json.dumps({"ok": True, "enabled": enabled}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_service":
        sid = body.get("service_id", "")
        scope = _normalize_service_scope(body.get("scope", "user"))
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if scope == "global" and not _is_admin(flowfile):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            ServiceRegistry.get_instance().uninstall(
                scope, _service_scope_id(scope, user_id, conv_id), sid)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Claude Code login via relay ──────────────────────────────────

    if action == "claude_code_list_relays":
        """List connected relay services for Claude Code login."""
        relay_list = []
        # Flow services
        if hasattr(self, '_services'):
            for sid, svc in self._services.items():
                if getattr(svc, 'TYPE', '') == 'relay' and getattr(svc, 'is_connected', lambda: False)():
                    info = getattr(svc, '_relay_info', {}) or {}
                    relay_list.append({
                        "relay_id": sid,
                        "platform": info.get("platform", "unknown"),
                        "root": info.get("root", ""),
                    })
        # User services
        if user_id:
            try:
                from core.service_registry import ServiceRegistry
                registry = ServiceRegistry.get_instance()
                for sid, sdef in registry.get_all("user", user_id).items():
                    if not sdef.enabled or sdef.service_type != "relay":
                        continue
                    if any(r["relay_id"] == sid for r in relay_list):
                        continue
                    svc = registry.get_live_instance("user", user_id, sid)
                    if svc and getattr(svc, 'is_connected', lambda: False)():
                        info = getattr(svc, '_relay_info', {}) or {}
                        relay_list.append({
                            "relay_id": sid,
                            "platform": info.get("platform", "unknown"),
                            "root": info.get("root", sdef.description or ""),
                        })
            except Exception as e:
                logger.debug("Failed to list user relays: %s", e)
        flowfile.set_content(json.dumps({"relays": relay_list}).encode())
        return [flowfile]

    if action == "claude_code_relay_login":
        """Launch claude auth login on a relay — async, result via SSE."""
        service_id = body.get("service_id", "")
        relay_id = body.get("relay_id", "")
        conversation_id = body.get("conversation_id", "")

        if not service_id or not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id or relay_id"}).encode())
            return [flowfile]

        # Find the relay service
        relay_svc = None
        if hasattr(self, '_services'):
            relay_svc = self._services.get(relay_id)
        if not relay_svc and user_id:
            try:
                from core.service_registry import ServiceRegistry
                relay_svc = ServiceRegistry.get_instance().resolve(relay_id, user_id=user_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_svc:
            flowfile.set_content(json.dumps({"error": f"Relay service '{relay_id}' not found"}).encode())
            return [flowfile]

        def _bg_relay_login():
            try:
                logger.info("[relay-login] Starting auth via relay %s", relay_id)
                result = relay_svc._request_with_progress(
                    "claude_auth_login", timeout=300)
            except Exception as e:
                logger.error("[relay-login] Failed: %s", e)
                _publish_command_result(conversation_id, {"error": str(e)})
                return

            if not result or (isinstance(result, dict) and "error" in result):
                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                _publish_command_result(conversation_id, {"error": error})
                return

            credentials = result.get("credentials", {}) if isinstance(result, dict) else {}
            if not credentials:
                _publish_command_result(conversation_id, {"error": "No credentials returned"})
                return

            oauth = credentials.get("claudeAiOauth", {})
            access_token = oauth.get("accessToken", "")
            refresh_token = oauth.get("refreshToken", "")
            expires_at = oauth.get("expiresAt", 0)

            if not access_token:
                _publish_command_result(conversation_id, {"error": "No accessToken in credentials"})
                return

            _store_claude_tokens(service_id, access_token, refresh_token, expires_at)
            logger.info("[relay-login] Credentials saved for %s", service_id)
            _publish_command_result(conversation_id, {
                "ok": True, "message": "Claude Code credentials saved!"})

        import threading as _threading  # noqa: F811
        _threading.Thread(target=_bg_relay_login, daemon=True, name=f"relay-login-{relay_id}").start()

        flowfile.set_content(json.dumps({
            "ok": True, "message": "Login started — authorize in the browser that opens on the relay."
        }).encode())
        return [flowfile]

    # ── Codex login via relay ─────────────────────────────────
    if action == "codex_relay_login":
        """Launch `codex login` on a relay — async, result via SSE."""
        service_id = body.get("service_id", "")
        relay_id = body.get("relay_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id or not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id or relay_id"}).encode())
            return [flowfile]
        relay_svc = None
        if hasattr(self, '_services'):
            relay_svc = self._services.get(relay_id)
        if not relay_svc and user_id:
            try:
                from core.service_registry import ServiceRegistry
                relay_svc = ServiceRegistry.get_instance().resolve(relay_id, user_id=user_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_svc:
            flowfile.set_content(json.dumps({"error": f"Relay service '{relay_id}' not found"}).encode())
            return [flowfile]

        def _bg_codex_relay_login():
            try:
                logger.info("[codex-relay-login] Starting auth via relay %s", relay_id)
                result = relay_svc._request_with_progress(
                    "codex_auth_login", timeout=300)
            except Exception as e:
                logger.error("[codex-relay-login] Failed: %s", e)
                _publish_command_result(conversation_id, {"error": str(e)})
                return
            if not result or (isinstance(result, dict) and "error" in result):
                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                _publish_command_result(conversation_id, {"error": error})
                return
            credentials = result.get("credentials", {}) if isinstance(result, dict) else {}
            if not credentials:
                _publish_command_result(conversation_id, {"error": "No credentials returned"})
                return
            from core.llm_providers.codex_session import parse_auth_json
            parsed = parse_auth_json(json.dumps(credentials))
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            account = parsed.get("account", "")
            id_token = parsed.get("id_token", "")
            if not access_token:
                _publish_command_result(conversation_id, {"error": "No access_token in codex auth.json"})
                return
            _store_codex_tokens(service_id, access_token, refresh_token, expires_at, account=account, id_token=id_token)
            logger.info("[codex-relay-login] Credentials saved for %s", service_id)
            _publish_command_result(conversation_id, {
                "ok": True, "message": "Codex credentials saved!"})

        import threading as _threading  # noqa: F811
        _threading.Thread(target=_bg_codex_relay_login, daemon=True,
                           name=f"codex-relay-login-{relay_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Codex login started — authorize in the browser that opens on the relay."
        }).encode())
        return [flowfile]

    # ── Gemini login via relay ─────────────────────────────────
    if action == "gemini_relay_login":
        """Launch interactive `gemini` (OAuth dance) on a relay — async, SSE result."""
        service_id = body.get("service_id", "")
        relay_id = body.get("relay_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id or not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id or relay_id"}).encode())
            return [flowfile]
        relay_svc = None
        if hasattr(self, '_services'):
            relay_svc = self._services.get(relay_id)
        if not relay_svc and user_id:
            try:
                from core.service_registry import ServiceRegistry
                relay_svc = ServiceRegistry.get_instance().resolve(relay_id, user_id=user_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_svc:
            flowfile.set_content(json.dumps({"error": f"Relay service '{relay_id}' not found"}).encode())
            return [flowfile]

        def _bg_gemini_relay_login():
            try:
                logger.info("[gemini-relay-login] Starting auth via relay %s", relay_id)
                result = relay_svc._request_with_progress(
                    "gemini_auth_login", timeout=300)
            except Exception as e:
                logger.error("[gemini-relay-login] Failed: %s", e)
                _publish_command_result(conversation_id, {"error": str(e)})
                return
            if not result or (isinstance(result, dict) and "error" in result):
                error = result.get("error", "Unknown error") if isinstance(result, dict) else str(result)
                _publish_command_result(conversation_id, {"error": error})
                return
            credentials = result.get("credentials", {}) if isinstance(result, dict) else {}
            accounts = result.get("accounts", {}) if isinstance(result, dict) else {}
            if not credentials:
                _publish_command_result(conversation_id, {"error": "No credentials returned"})
                return
            from core.llm_providers.gemini_session import parse_oauth_creds_json
            parsed = parse_oauth_creds_json(json.dumps(credentials))
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            account = next(iter(accounts.keys()), "") if isinstance(accounts, dict) and accounts else ""
            if not access_token:
                _publish_command_result(conversation_id, {"error": "No access_token in gemini oauth_creds.json"})
                return
            _store_gemini_tokens(service_id, access_token, refresh_token, expires_at, account=account)
            logger.info("[gemini-relay-login] Credentials saved for %s", service_id)
            _publish_command_result(conversation_id, {
                "ok": True, "message": "Gemini credentials saved!"})

        import threading as _threading  # noqa: F811
        _threading.Thread(target=_bg_gemini_relay_login, daemon=True,
                           name=f"gemini-relay-login-{relay_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Gemini login started — authorize in the browser that opens on the relay."
        }).encode())
        return [flowfile]

    # ── Claude Code login via server (noVNC) ───────────────────────

    if action == "claude_code_server_login":
        """Spawn a Docker container with Chromium + noVNC for Claude auth.

        Returns {session_id} immediately — Docker setup runs in background.
        Frontend polls claude_code_server_login_status for readiness.
        """
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            if _credential_provider_for_service(service_id, user_id) != "claude-code":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a claude-code credential provider"}).encode())
                return [flowfile]
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Cannot verify service: {e}"}).encode())
            return [flowfile]

        try:
            import uuid as _uuid
            from pawflow_relay.utils import find_free_port as _find_free_port

            session_id = _uuid.uuid4().hex[:12]
            free_port = _find_free_port()
            container_name = f"pawflow-claude-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"

            logger.info("[vnc-login] Creating session %s (port %d)", session_id, free_port)

            # Pre-register session so status endpoint works immediately
            from services.vnc_proxy import register_session, vnc_ws_proxy, vnc_http_proxy
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                login_session_id=getattr(flowfile, "auth_session_id", "") or "",
                container=container_name, service_id=service_id,
                user_id=user_id, volume=volume_name,
                launch_time=time.time(), ready=False)
        except Exception as e:
            logger.error("[vnc-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup():
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--tmpfs", "/workspace:rw,size=64m",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    "--entrypoint", "bash",
                    image,
                    "/opt/pawflow/auth_login.sh",
                ]
                logger.info("[vnc-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[vnc-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[vnc-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            # Wait for noVNC to be ready
            import urllib.request
            for _attempt in range(15):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{free_port}/", timeout=2)  # nosec B310 - local noVNC readiness probe.
                    logger.info("[vnc-login] noVNC ready on port %d", free_port)
                    break
                except Exception:
                    time.sleep(1)

            # Register VNC proxy routes (once, shared by all sessions)
            try:
                svc = None
                try:
                    from core.service_registry import ServiceRegistry
                    greg = ServiceRegistry.get_instance()
                    for _sid, _sdef in greg.get_all("global", "").items():
                        if getattr(_sdef, "service_type", "") == "httpListener":
                            svc = greg.get_live_instance("global", "", _sid)
                            if svc:
                                break
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if svc:
                    _vnc_owner = "_vnc_proxy"
                    existing = [r for r in svc.get_routes() if r.get("owner") == _vnc_owner]
                    if not existing:
                        svc.register_route("GET", "/vnc/{session_id}/{token}/websockify",
                                           _vnc_owner, callback=lambda req: None,
                                           ws_handler=vnc_ws_proxy)
                        svc.register_route("GET", "/vnc/{session_id}/{token}/{path+}",
                                           _vnc_owner, callback=vnc_http_proxy)
                else:
                    logger.warning("[vnc-login] HTTPListenerService NOT FOUND")
            except Exception as e:
                logger.warning("[vnc-login] Route registration failed: %s", e)

            # Mark session as ready and notify frontend to open dialog
            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            logger.info("[vnc-login] Session %s ready — notifying frontend", session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id,
                    "service_id": service_id,
                    "token": _vnc_token,
                    "cli": "claude",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup, daemon=True, name=f"vnc-login-{session_id}").start()

        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting login container...",
        }).encode())
        return [flowfile]

    if action == "claude_code_server_login_cleanup":
        """Cleanup a login container (user closed dialog or timeout)."""
        session_id = body.get("session_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if session:
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", session.get("container", "")],  # nosec B603
                        capture_output=True, timeout=10)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            unregister_session(session_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "claude_code_server_login_status":
        """Poll for login completion. Check if credentials file was updated."""
        session_id = body.get("session_id", "")
        service_id = body.get("service_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if not session:
            flowfile.set_content(json.dumps({"error": "Unknown session"}).encode())
            return [flowfile]

        # Background setup error
        if session.get("error"):
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": session["error"]}).encode())
            return [flowfile]

        # Container still starting
        if not session.get("ready"):
            flowfile.set_content(json.dumps({"status": "starting"}).encode())
            return [flowfile]

        import subprocess as _sp  # nosec B404
        from core.docker_utils import docker_cmd as _docker_cmd
        container = session["container"]
        launch_time = session.get("launch_time", 0)

        # Check timeout (2 min max)
        if time.time() - launch_time > 120:
            _sp.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                    capture_output=True, timeout=10)
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": "Login timed out (2 min)"}).encode())
            return [flowfile]

        # Check if .credentials.json was updated since launch
        try:
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "stat -c %Y /home/pawflow/.credentials.json 2>/dev/null || stat -c %Y /workspace/.credentials.json 2>/dev/null"],
                capture_output=True, text=True, timeout=5)
            if stat_result.returncode != 0:
                # File doesn't exist yet
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            file_mtime = int(stat_result.stdout.strip())
            if file_mtime < int(launch_time):
                # File exists but not updated since launch
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
        except Exception:
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]

        # Credentials updated — read them
        try:
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.credentials.json 2>/dev/null || cat /workspace/.credentials.json"],
                capture_output=True, text=True, timeout=10)
            credentials = json.loads(read_result.stdout)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read credentials: {e}"}).encode())
            # Cleanup
            _sp.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                    capture_output=True, timeout=10)
            unregister_session(session_id)
            return [flowfile]

        # Save tokens to service config
        oauth = credentials.get("claudeAiOauth", {})
        access_token = oauth.get("accessToken", "")
        refresh_token = oauth.get("refreshToken", "")
        expires_at = oauth.get("expiresAt", 0)

        import time as _t
        _exp_s = int(expires_at) / 1000 if int(expires_at) > 1e12 else int(expires_at)
        _remaining = _exp_s - _t.time()
        logger.info("[vnc-login] Credentials from container: token=%s...  expires=%s (%.1fh %s)",
                    access_token[:20] if access_token else "EMPTY",
                    expires_at, _remaining / 3600,
                    "EXPIRED" if _remaining < 0 else "valid")

        if access_token and _remaining > 0:
            try:
                _store_claude_tokens(service_id, access_token, refresh_token, expires_at)
            except Exception as e:
                logger.warning("Failed to save credentials: %s", e)
        elif access_token and _remaining <= 0:
            logger.error("[vnc-login] REFUSING to save EXPIRED token (expires_at=%s, %.1fh ago)",
                         expires_at, abs(_remaining) / 3600)
            flowfile.set_content(json.dumps({
                "error": f"Login returned expired token ({abs(_remaining)/3600:.0f}h ago). Try again."
            }).encode())
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            return [flowfile]

        # Cleanup container (volume stays)
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container],  # nosec B603
                    capture_output=True, timeout=10)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        # Cleanup VNC proxy routes
        # Routes are shared (/vnc/{session_id}/...) — don't unregister

        if not access_token:
            flowfile.set_content(json.dumps({"error": "No accessToken in credentials"}).encode())
            return [flowfile]

        flowfile.set_content(json.dumps({
            "ok": True,
            "message": "Claude Code credentials saved!",
        }).encode())
        return [flowfile]

    # ── Claude Code set credentials (paste) ──────────────────────────

    if action == "claude_code_login_url":
        """Return instructions for Claude Code login (paste credentials)."""
        flowfile.set_content(json.dumps({
            "flow": "paste_credentials",
            "message": (
                "Run this on your machine:\n\n"
                "  claude auth login\n\n"
                "Then paste the content of:\n\n"
                "  ~/.claude/.credentials.json\n\n"
                "(macOS/Linux) or %USERPROFILE%\\.claude\\.credentials.json (Windows)"
            ),
        }).encode())
        return [flowfile]

    # ── Codex set credentials (paste) ─────────────────────────────

    if action == "codex_login_url":
        flowfile.set_content(json.dumps({
            "flow": "paste_credentials",
            "message": (
                "Run on your machine:\n\n"
                "  codex login\n\n"
                "Then paste the content of:\n\n"
                "  ~/.codex/auth.json\n\n"
                "(macOS/Linux) or %USERPROFILE%\\.codex\\auth.json (Windows)"
            ),
        }).encode())
        return [flowfile]

    if action in ("codex_login_code", "codex_auth"):
        service_id = body.get("service_id", "")
        credentials_json = body.get("credentials", "").strip()
        if not service_id or not credentials_json:
            flowfile.set_content(json.dumps({"error": "Missing service_id or credentials"}).encode())
            return [flowfile]
        try:
            from core.llm_providers.codex_session import parse_auth_json
            parsed = parse_auth_json(credentials_json)
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            account = parsed.get("account", "")
            id_token = parsed.get("id_token", "")
            if not access_token:
                flowfile.set_content(json.dumps({
                    "error": (
                        "Invalid credentials: no access_token found. "
                        "Expected format: {\"tokens\": {\"access_token\": \"...\", \"refresh_token\": \"...\"}}"
                    ),
                }).encode())
                return [flowfile]
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            sdef = greg.get_definition("global", "", service_id)
            _stored = False
            if sdef:
                _roles = flowfile.get_attribute("http.auth.roles") or ""
                if action == "codex_auth" and "admin" not in _roles:
                    flowfile.set_content(json.dumps({
                        "error": f"Admin permission required for global service '{service_id}'"
                    }).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                if _credential_provider_for_service(service_id, user_id) != "codex-app-server":
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{service_id}' is not a codex credential provider"
                    }).encode())
                    return [flowfile]
                _store_codex_tokens(service_id, access_token, refresh_token, expires_at, account=account, id_token=id_token)
                _stored = True
            if not _stored:
                try:
                    usdef = greg.get_definition("user", user_id, service_id)
                    if usdef:
                        if _credential_provider_for_service(service_id, user_id) != "codex-app-server":
                            flowfile.set_content(json.dumps({
                                "error": f"Service '{service_id}' is not a codex credential provider"
                            }).encode())
                            return [flowfile]
                        _store_codex_tokens(service_id, access_token, refresh_token, expires_at, account=account, id_token=id_token)
                        _stored = True
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not _stored:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Codex credentials saved for '{service_id}'",
            }).encode())
        except json.JSONDecodeError:
            flowfile.set_content(json.dumps({"error": "Invalid JSON. Paste the raw content of ~/.codex/auth.json"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Gemini set credentials (paste) ────────────────────────────

    if action == "gemini_login_url":
        flowfile.set_content(json.dumps({
            "flow": "paste_credentials",
            "message": (
                "Run on your machine:\n\n"
                "  gemini       (first launch triggers OAuth)\n\n"
                "Then paste the content of:\n\n"
                "  ~/.gemini/oauth_creds.json\n\n"
                "(macOS/Linux) or %USERPROFILE%\\.gemini\\oauth_creds.json (Windows)"
            ),
        }).encode())
        return [flowfile]

    if action in ("gemini_login_code", "gemini_auth"):
        service_id = body.get("service_id", "")
        credentials_json = body.get("credentials", "").strip()
        if not service_id or not credentials_json:
            flowfile.set_content(json.dumps({"error": "Missing service_id or credentials"}).encode())
            return [flowfile]
        try:
            from core.llm_providers.gemini_session import parse_oauth_creds_json
            parsed = parse_oauth_creds_json(credentials_json)
            access_token = parsed.get("access_token", "")
            refresh_token = parsed.get("refresh_token", "")
            expires_at = parsed.get("expires_at", 0)
            if not access_token:
                flowfile.set_content(json.dumps({
                    "error": (
                        "Invalid credentials: no access_token found. "
                        "Expected format: {\"access_token\": \"...\", \"refresh_token\": \"...\", \"expiry_date\": ...}"
                    ),
                }).encode())
                return [flowfile]
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            sdef = greg.get_definition("global", "", service_id)
            _stored = False
            if sdef:
                _roles = flowfile.get_attribute("http.auth.roles") or ""
                if action == "gemini_auth" and "admin" not in _roles:
                    flowfile.set_content(json.dumps({
                        "error": f"Admin permission required for global service '{service_id}'"
                    }).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                if _credential_provider_for_service(service_id, user_id) != "gemini":
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{service_id}' is not a gemini credential provider"
                    }).encode())
                    return [flowfile]
                _store_gemini_tokens(service_id, access_token, refresh_token, expires_at)
                _stored = True
            if not _stored:
                try:
                    usdef = greg.get_definition("user", user_id, service_id)
                    if usdef:
                        if _credential_provider_for_service(service_id, user_id) != "gemini":
                            flowfile.set_content(json.dumps({
                                "error": f"Service '{service_id}' is not a gemini credential provider"
                            }).encode())
                            return [flowfile]
                        _store_gemini_tokens(service_id, access_token, refresh_token, expires_at)
                        _stored = True
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            if not _stored:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Gemini credentials saved for '{service_id}'",
            }).encode())
        except json.JSONDecodeError:
            flowfile.set_content(json.dumps({"error": "Invalid JSON. Paste the raw content of ~/.gemini/oauth_creds.json"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action in ("claude_code_login_code", "claude_code_auth"):
        """Receive pasted credentials JSON and store tokens in service config.

        Supports all service scopes: global, user, conversation.
        Permission check: admin required for global services,
        user can auth their own services.
        """
        service_id = body.get("service_id", "")
        credentials_json = body.get("credentials", "").strip()

        if not service_id or not credentials_json:
            flowfile.set_content(json.dumps({"error": "Missing service_id or credentials"}).encode())
            return [flowfile]

        try:
            creds = json.loads(credentials_json)
            oauth = creds.get("claudeAiOauth", {})
            access_token = oauth.get("accessToken", "")
            refresh_token = oauth.get("refreshToken", "")
            expires_at = oauth.get("expiresAt", 0)

            if not access_token:
                flowfile.set_content(json.dumps({
                    "error": "Invalid credentials: no accessToken found. "
                             "Expected format: {\"claudeAiOauth\": {\"accessToken\": \"...\", ...}}"
                }).encode())
                return [flowfile]

            # Find the service in global or user registry and verify provider
            _stored = False
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            sdef = greg.get_definition("global", "", service_id)
            if sdef:
                # Global service — check admin permission
                _roles = flowfile.get_attribute("http.auth.roles") or ""
                if action == "claude_code_auth" and "admin" not in _roles:
                    flowfile.set_content(json.dumps({
                        "error": f"Admin permission required for global service '{service_id}'"
                    }).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                if _credential_provider_for_service(service_id, user_id) != "claude-code":
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{service_id}' is not a claude-code credential provider"
                    }).encode())
                    return [flowfile]
                _store_claude_tokens(service_id, access_token, refresh_token, expires_at)
                _stored = True

            if not _stored:
                # Try user services
                try:
                    from core.service_registry import ServiceRegistry
                    ureg = ServiceRegistry.get_instance()
                    usdef = ureg.get_definition("user", user_id, service_id)
                    if usdef:
                        if _credential_provider_for_service(service_id, user_id) != "claude-code":
                            flowfile.set_content(json.dumps({
                                "error": f"Service '{service_id}' is not a claude-code credential provider"
                            }).encode())
                            return [flowfile]
                        _store_claude_tokens(service_id, access_token, refresh_token, expires_at)
                        _stored = True
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

            if not _stored:
                flowfile.set_content(json.dumps({
                    "error": f"Service '{service_id}' not found"
                }).encode())
                return [flowfile]

            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Credentials saved for '{service_id}'",
            }).encode())
        except json.JSONDecodeError:
            flowfile.set_content(json.dumps({"error": "Invalid JSON. Paste the raw content of .credentials.json"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "relay_connect":
        # Spawn a child relay on an existing relay for a new directory
        relay_source = body.get("relay_source", "")
        path = body.get("path", "")
        if not path:
            flowfile.set_content(json.dumps({"error": "Missing path"}).encode())
            return [flowfile]

        # Find the source relay service
        from core.service_registry import ServiceRegistry
        ureg = ServiceRegistry.get_instance()
        greg = ServiceRegistry.get_instance()

        source_svc = None
        if relay_source:
            # Explicit source
            source_svc = ureg.get_live_instance("user", user_id, relay_source)
            if not source_svc:
                source_svc = greg.get_live_instance("global", "", relay_source)
        else:
            # Find user's first connected filesystem service
            for sid, sdef in ureg.get_all("user", user_id).items():
                if getattr(sdef, "service_type", "") in ("relay", "filesystem"):
                    svc = ureg.get_live_instance("user", user_id, sid)
                    if svc and hasattr(svc, '_relay_pool') and svc._relay_pool:
                        source_svc = svc
                        relay_source = sid
                        break

        if not source_svc:
            flowfile.set_content(json.dumps({
                "error": f"No connected relay found{' for ' + relay_source if relay_source else ''}. "
                         "Connect a server relay or standalone pawflow-relay client first."
            }).encode())
            return [flowfile]

        # Generate IDs for the child relay
        import hashlib
        _dir_hash = hashlib.md5(path.encode(), usedforsecurity=False).hexdigest()[:8]
        child_relay_id = f"fs_{user_id}_{_dir_hash}"

        # Send spawn_relay command to the source relay
        try:
            import uuid as _uuid_relay
            _req_id = _uuid_relay.uuid4().hex[:12]
            # Use the source service's _request mechanism to send spawn_relay
            # We need to send a raw message to the relay — use the pool's writer
            import asyncio
            with source_svc._relay_pool_lock:
                if not source_svc._relay_pool:
                    raise Exception("Relay not connected")
                _conn = source_svc._relay_pool[0]
                _writer = _conn["writer"]
                _loop = _conn["loop"]

            _spawn_msg = json.dumps({
                "type": "spawn_relay",
                "request_id": _req_id,
                "root": path,
                "relay_id": child_relay_id,
                "token": source_svc.config.get("token", ""),
                "secret": source_svc.config.get("secret", ""),
            }).encode("utf-8")

            async def _send_spawn():
                from services.filesystem_service import _ws_send_frame
                await _ws_send_frame(_writer, _spawn_msg)

            asyncio.run_coroutine_threadsafe(_send_spawn(), _loop).result(timeout=5)

            conv_id = body.get("conversation_id", "")
            _crid = child_relay_id

            def _bg_wait_relay():
                time.sleep(3)
                logger.info("[relay-connect] Relay spawned: %s → %s", _crid, path)
                if conv_id:
                    _publish_command_result(conv_id, {
                        "ok": True,
                        "message": f"Relay spawned: {_crid} → {path}",
                        "service_id": _crid,
                    })

            threading.Thread(target=_bg_wait_relay, daemon=True).start()
            flowfile.set_content(json.dumps({
                "ok": True, "message": f"Spawning relay {child_relay_id}..."
            }).encode())
        except Exception as e:
            logger.error("relay_connect failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Failed to spawn relay: {e}"}).encode())
        return [flowfile]

    if action == "relay_disconnect":
        service_id = body.get("service_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]

        # Find which relay this service is connected through
        from core.service_registry import ServiceRegistry
        ureg = ServiceRegistry.get_instance()
        svc = ureg.get_live_instance("user", user_id, service_id)
        if svc:
            try:
                svc.disconnect()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            try:
                ureg.uninstall("user", user_id, service_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Also try to send stop_relay to all connected relays
        try:
            for sid, sdef in ureg.get_all("user", user_id).items():
                _svc = ureg.get_live_instance("user", user_id, sid)
                if _svc and hasattr(_svc, '_relay_pool') and _svc._relay_pool:
                    try:
                        import asyncio
                        with _svc._relay_pool_lock:
                            if not _svc._relay_pool:
                                continue
                            _conn = _svc._relay_pool[0]
                            _writer = _conn["writer"]
                            _loop = _conn["loop"]
                        _stop_msg = json.dumps({
                            "type": "stop_relay",
                            "relay_id": service_id,
                        }).encode("utf-8")

                        async def _send_stop():
                            from services.filesystem_service import _ws_send_frame
                            await _ws_send_frame(_writer, _stop_msg)

                        asyncio.run_coroutine_threadsafe(_send_stop(), _loop).result(timeout=5)
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Service '{service_id}' disconnected",
        }).encode())
        return [flowfile]

    if action in ("start_flow", "stop_flow", "undeploy_flow"):
        iid = body.get("instance_id", "")
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        try:
            from core.executor_registry import ExecutorRegistry
            from core.deployment_registry import DeploymentRegistry
            reg = ExecutorRegistry.get_instance()
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if inst and user_id and inst.owner and inst.owner != user_id:
                flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                return [flowfile]

            if action == "stop_flow":
                ex = reg.get(iid)
                if ex and ex.is_running:
                    ex.stop()
                reg.unregister(iid)
                flowfile.set_content(json.dumps({"ok": True, "status": "stopped"}).encode())
            elif action == "start_flow":
                inst = dr.get_all().get(iid)
                if not inst:
                    flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                    return [flowfile]
                reg._restore_instance(iid, inst.flow_path,
                                       inst.max_workers, inst.max_retries,
                                       flow_fqn=getattr(inst, "flow_fqn", "") or "",
                                       flow_scope=getattr(inst, "flow_scope", "") or "",
                                       parameters=inst.parameters,
                                       service_overrides=inst.service_overrides,
                                       service_configs=inst.service_configs,
                                       owner=inst.owner or "",
                                       conversation_id=inst.conversation_id or "",
                                       agent_name=getattr(inst, "agent_name", "") or "")
                flowfile.set_content(json.dumps({"ok": True, "status": "running"}).encode())
            elif action == "undeploy_flow":
                ex = reg.get(iid)
                if ex and ex.is_running:
                    ex.stop()
                reg.unregister(iid)
                dr.undeploy(iid)
                flowfile.set_content(json.dumps({"ok": True, "status": "undeployed"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_available_flows":
        # Flow templates are stored under
        #   data/repository/flows/global/<package>/<flow_name>/latest.json
        #   data/repository/flows/users/<uid>/<package>/<flow_name>/latest.json
        #   data/repository/flows/users/<uid>/<conversation_id>/<package>/<flow_name>/latest.json
        # Each <flow_name>/ contains latest.json (a {"version": "X.Y.Z"}
        # pointer) plus versions/<version>.json (the real flow definition).
        # We walk conversation, user, then global scopes.
        try:
            from core.paths import REPOSITORY_DIR
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            templates = []
            roots = []
            if user_id:
                if conv_id:
                    roots.append(("conversation",
                                  REPOSITORY_DIR / "flows" / "users" / user_id / conv_id))
                roots.append(("user",
                              REPOSITORY_DIR / "flows" / "users" / user_id))
            roots.append(("global", REPOSITORY_DIR / "flows" / "global"))
            for scope_label, root in roots:
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
                        templates.append({
                            "id": raw.get("id") or flow_dir.name,
                            "name": raw.get("name") or flow_dir.name,
                            "version": version,
                            "description": raw.get("description") or "",
                            "scope": raw.get("scope") or scope_label,
                            "tasks_count": len(raw.get("tasks", {}) or {}),
                            "services_count": len(raw.get("services", {}) or {}),
                            "file_path": str(vfile),
                        })
                    except Exception as e:
                        logger.debug("list_available_flows: skip %s: %s",
                                     latest, e)
            templates.sort(key=lambda t: (t["scope"], t["name"]))
            flowfile.set_content(
                json.dumps({"templates": templates},
                           ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]


    if action == "get_flow_deploy_schema":
        template_id = body.get("template_id", "")
        if not template_id:
            flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
            return [flowfile]
        try:
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            tpath = _resolve_flow_template_path(template_id, user_id, conv_id)
            if not tpath:
                flowfile.set_content(json.dumps(
                    {"error": f"Template '{template_id}' not found in "
                              "data/repository/flows/"}).encode())
                return [flowfile]
            raw = json.loads(tpath.read_text(encoding="utf-8"))
            payload = _flow_deploy_schema_payload(raw)
            payload["file_path"] = str(tpath)
            flowfile.set_content(json.dumps(
                payload, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]


    if action == "deploy_flow":
        template_id = body.get("template_id", "")
        conv_id = body.get("conversation_id", "")
        deploy_scope = "conversation" if conv_id else body.get("scope", "user")
        params = body.get("parameters", {})
        service_overrides = body.get("service_overrides", {})
        service_configs = body.get("service_configs", {})
        if deploy_scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps(
                {"error": "Requires admin role for global scope"}).encode())
            return [flowfile]
        if not template_id:
            flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
            return [flowfile]
        try:
            from core.deployment_registry import DeploymentRegistry
            tpath = _resolve_flow_template_path(template_id, user_id, conv_id)
            if not tpath:
                flowfile.set_content(json.dumps(
                    {"error": f"Template '{template_id}' not found in "
                              "data/repository/flows/"}).encode())
                return [flowfile]


            # Read flow scope from template (runtime dependency declaration)
            flow_config = json.loads(tpath.read_text(encoding="utf-8"))
            flow_scope = flow_config.get("scope", "independent")

            # Validate runtime dependencies
            uid = user_id
            if flow_scope in ("user", "conversation") and not uid:
                flowfile.set_content(json.dumps(
                    {"error": f"Flow requires user context (scope={flow_scope})"}).encode())
                return [flowfile]
            if flow_scope == "conversation" and not conv_id:
                flowfile.set_content(json.dumps(
                    {"error": "Flow requires conversation context (scope=conversation)"}).encode())
                return [flowfile]

            # Inject runtime parameters based on flow scope
            if flow_scope in ("user", "conversation"):
                params["_user_id"] = uid
            if flow_scope == "conversation":
                params["_conversation_id"] = conv_id
            params["_flow_scope"] = flow_scope

            dr = DeploymentRegistry.get_instance()
            agent_name = (
                body.get("_agent_name", "")
                or body.get("call_agent_name", "")
                or flowfile.get_attribute("call_agent_name")
                or getattr(self, "_agent_name", "")
                or ""
            )
            iid = dr.deploy(
                template_path=str(tpath),
                owner=uid,
                parameters=params,
                source="agent",
                conversation_id=conv_id if deploy_scope == "conversation" else None,
                agent_name=agent_name,
                service_overrides=service_overrides,
                service_configs=service_configs,
            )
            inst = dr.get(iid)
            if inst:
                inst.flow_fqn = flow_config.get("fqn") or template_id
                inst.flow_scope = flow_scope
                dr._save_instance(inst)
            flowfile.set_content(json.dumps(
                {"ok": True, "instance_id": iid, "scope": deploy_scope,
                 "flow_scope": flow_scope}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "promote_flow":
        iid = body.get("instance_id", "")
        target_scope = body.get("target_scope", "user")
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        if target_scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps(
                {"error": "Requires admin role for global scope"}).encode())
            return [flowfile]
        try:
            from core.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            if user_id and inst.owner and inst.owner != user_id:
                flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                return [flowfile]
            if not inst.conversation_id:
                flowfile.set_content(json.dumps({"error": "Flow is already user-scoped"}).encode())
                return [flowfile]
            inst.conversation_id = None
            dr._save_instance(inst)
            flowfile.set_content(json.dumps({"ok": True, "scope": "user"}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "get_flow_instance":
        iid = body.get("instance_id", "")
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        try:
            from core.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            # Load template deployment schema for reference
            template_params = {}
            deploy_schema = {}
            try:
                raw = _load_flow_instance_template_raw(inst, user_id)
                if raw:
                    template_params = raw.get("parameters", {})
                    deploy_schema = _flow_deploy_schema_payload(
                        raw, parameters=inst.parameters,
                        service_overrides=inst.service_overrides,
                        service_configs=inst.service_configs)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            payload = {
                "instance_id": inst.instance_id,
                "flow_name": inst.flow_name,
                "flow_id": inst.flow_id,
                "status": inst.status,
                "parameters": inst.parameters,
                "template_parameters": template_params,
                "parameters_schema": deploy_schema.get("parameters_schema", {}),
                "parameter_values": deploy_schema.get("parameter_values", {}),
                "services": deploy_schema.get("services", {}),
                "service_overrides": inst.service_overrides,
                "service_configs": inst.service_configs,
                "owner": inst.owner,
                "scope": "conversation" if inst.conversation_id else "user" if inst.owner else "global",
            }
            flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "create_server_workspace":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            conv_id = flowfile.get_attribute("http.conversation_id") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.server_relay_manager import ServerRelayManager
            meta = ServerRelayManager.get_instance().spawn(conv_id, user_id)

            def _bg_wait_workspace():
                time.sleep(3)
                logger.info("[workspace] Server workspace ready: %s", meta["relay_id"])
                _publish_command_result(conv_id, {
                    "ok": True,
                    "relay_id": meta["relay_id"],
                    "ws_url": meta["ws_url"],
                    "volume": meta["volume"],
                    "message": (
                        f"Server workspace ready. "
                        f"Use filesystem service '{meta['relay_id']}' to access your files."
                    ),
                })

            threading.Thread(target=_bg_wait_workspace, daemon=True).start()
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Starting server workspace..."
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "destroy_server_workspace":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            conv_id = flowfile.get_attribute("http.conversation_id") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.server_relay_manager import ServerRelayManager
            destroyed = ServerRelayManager.get_instance().destroy(conv_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "destroyed": destroyed,
                "message": "Server workspace destroyed." if destroyed else "No server workspace found.",
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "create_server_execution_relay":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            conv_id = flowfile.get_attribute("http.conversation_id") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.server_relay_manager import ServerRelayManager
            meta = ServerRelayManager.get_instance().ensure_minimal(conv_id, user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "relay_id": meta["relay_id"],
                "ws_url": meta["ws_url"],
                "volume": meta["volume"],
                "kind": meta.get("kind", "minimal"),
                "message": (
                    f"Server execution relay ready. Use relay '{meta['relay_id']}' "
                    "as an explicit flow parameter value."
                ),
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "destroy_server_execution_relay":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            conv_id = flowfile.get_attribute("http.conversation_id") or ""
        if not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.server_relay_manager import ServerRelayManager
            destroyed = ServerRelayManager.get_instance().destroy_minimal(conv_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "destroyed": destroyed,
                "message": "Server execution relay destroyed." if destroyed else "No server execution relay found.",
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "server_execution_relay_status":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            conv_id = flowfile.get_attribute("http.conversation_id") or ""
        try:
            from core.server_relay_manager import ServerRelayManager
            mgr = ServerRelayManager.get_instance()
            meta = mgr.get_metadata(conv_id, kind="minimal") if conv_id else None
            if not meta:
                flowfile.set_content(json.dumps({"exists": False}).encode())
            else:
                running = mgr._is_container_running(meta.get("container_id", ""))
                flowfile.set_content(json.dumps({
                    "exists": True,
                    "relay_id": meta["relay_id"],
                    "running": running,
                    "volume": meta.get("volume", ""),
                    "kind": meta.get("kind", "minimal"),
                }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "server_workspace_status":
        conv_id = body.get("conversation_id", "")
        if not conv_id:
            conv_id = flowfile.get_attribute("http.conversation_id") or ""
        try:
            from core.server_relay_manager import ServerRelayManager
            meta = ServerRelayManager.get_instance().get_metadata(conv_id) if conv_id else None
            if not meta:
                flowfile.set_content(json.dumps({"exists": False}).encode())
            else:
                from core.server_relay_manager import ServerRelayManager as _SRM
                running = _SRM.get_instance()._is_container_running(meta.get("container_id", ""))
                flowfile.set_content(json.dumps({
                    "exists": True,
                    "relay_id": meta["relay_id"],
                    "running": running,
                    "volume": meta.get("volume", ""),
                }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "update_flow_params":
        iid = body.get("instance_id", "")
        params = body.get("parameters", {})
        service_overrides = body.get("service_overrides")
        service_configs = body.get("service_configs")
        replace_parameters = bool(body.get("replace_parameters"))
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        try:
            from core.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            if user_id and inst.owner and inst.owner != user_id:
                flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                return [flowfile]
            if replace_parameters:
                _set_instance_config(
                    inst, parameters=params,
                    service_overrides=service_overrides,
                    service_configs=service_configs)
            else:
                inst.parameters.update(params)
                _set_instance_config(
                    inst, service_overrides=service_overrides,
                    service_configs=service_configs)
            dr._save_instance(inst)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Terminal / code-server on relay ──────────────────────────

    def _find_relay_svc(relay_id):
        """Find relay service in global then user registry."""
        from core.service_registry import ServiceRegistry
        greg = ServiceRegistry.get_instance()
        svc = greg.get_live_instance("global", "", relay_id)
        if svc:
            return svc
        from core.service_registry import ServiceRegistry
        ureg = ServiceRegistry.get_instance()
        return ureg.get_live_instance("user", user_id, relay_id) if user_id else None

    def _ensure_vnc_routes():
        """Ensure /vnc/ and /audio/ HTTP+WS routes exist on the HTTP listener.

        Same registration as claude_code_server_login — idempotent.
        """
        # Routes must land on the HTTP listener that served THIS request,
        # not any random listener (admin vs chat can run on different ports).
        # http.listener.port is set by httpReceiver when the request comes in.
        _req_port = flowfile.get_attribute("http.listener.port") or ""
        if not _req_port:
            logger.warning("[vnc] No http.listener.port on flowfile — cannot target listener")
            return
        try:
            from services.vnc_proxy import vnc_ws_proxy, vnc_http_proxy
            from services.audio_proxy import audio_ws_proxy
            from services.http_listener_service import _instances
            _http_svc = _instances.get(int(_req_port))
            if not _http_svc:
                logger.warning("[vnc] No live listener on port %s (instances: %s)",
                               _req_port, list(_instances.keys()))
                return
            _vnc_owner = "_vnc_proxy"
            existing = [r for r in _http_svc.get_routes() if r.get("owner") == _vnc_owner]
            if not existing:
                _http_svc.register_route("GET", "/vnc/{session_id}/{token}/websockify",
                                         _vnc_owner, callback=lambda req: None,
                                         ws_handler=vnc_ws_proxy)
                _http_svc.register_route("GET", "/vnc/{session_id}/{token}/{path+}",
                                         _vnc_owner, callback=vnc_http_proxy)
                logger.info("[vnc] Registered VNC routes on port %s", _req_port)
            _audio_exists = [r for r in _http_svc.get_routes()
                             if r.get("pattern", "").startswith("/audio/")]
            if not _audio_exists:
                _http_svc.register_route("GET", "/audio/{session_id}/{token}/stream",
                                         _vnc_owner, callback=lambda req: None,
                                         ws_handler=audio_ws_proxy)
        except Exception as e:
            logger.warning("[vnc] Route registration failed: %s", e)

    def _audio_lookup_token(sid: str) -> str:
        """Return the capability token minted for an audio session, or
        empty string if there is none. Used by the URL builders that
        emit `audio_token` alongside `audio_session` so the frontend
        can build /audio/<sid>/<token>/stream."""
        try:
            from services.audio_proxy import get_audio_token
            return get_audio_token(sid)
        except Exception:
            return ""

    def _get_desktop_host_port(relay_id):
        """Get the published host port for desktop noVNC.

        Same pattern as Claude login: find the container, docker port 6080.
        """
        import subprocess  # nosec B404
        from core.docker_utils import docker_cmd as _dkr_cmd

        # 1) Server relay: container name in conversation metadata
        try:
            from core.server_relay_manager import ServerRelayManager
            for entry in ServerRelayManager.get_instance().list_all():
                if entry.get("relay_id") == relay_id:
                    # Stored at spawn time
                    hp = entry.get("desktop_host_port", 0)
                    if hp:
                        return hp
                    # Fallback: docker port on the container
                    cname = entry.get("container_name", "")
                    if cname:
                        r = subprocess.run(  # nosec B603
                            _dkr_cmd() + ["port", cname, "6080"],
                            capture_output=True, text=True, timeout=5)
                        if r.returncode == 0:
                            return int(r.stdout.strip().split(":")[-1])
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # 2) Any relay: get container_id from relay info, then docker port
        svc = _find_relay_svc(relay_id)
        if svc:
            container_id = getattr(svc, '_relay_info', {}).get('container_id', '')
            if container_id:
                try:
                    r = subprocess.run(  # nosec B603
                        _dkr_cmd() + ["port", container_id, "6080"],
                        capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return int(r.stdout.strip().split(":")[-1])
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return 0

    def _get_container_port(relay_id, container_port):
        """Get the published host port for a given container port."""
        import subprocess  # nosec B404
        from core.docker_utils import docker_cmd as _dkr_cmd
        try:
            from core.server_relay_manager import ServerRelayManager
            for entry in ServerRelayManager.get_instance().list_all():
                if entry.get("relay_id") == relay_id:
                    cname = entry.get("container_name", "")
                    if cname:
                        r = subprocess.run(  # nosec B603
                            _dkr_cmd() + ["port", cname, str(container_port)],
                            capture_output=True, text=True, timeout=5)
                        if r.returncode == 0:
                            return int(r.stdout.strip().split(":")[-1])
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        svc = _find_relay_svc(relay_id)
        if svc:
            cid = getattr(svc, '_relay_info', {}).get('container_id', '')
            if cid:
                try:
                    r = subprocess.run(  # nosec B603
                        _dkr_cmd() + ["port", cid, str(container_port)],
                        capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return int(r.stdout.strip().split(":")[-1])
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return 0

    if action == "open_terminal":
        relay_id = body.get("relay_id", "")
        local = body.get("local", False)
        cols = body.get("cols", 80)
        rows = body.get("rows", 24)
        shell = body.get("shell")  # None = relay default
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]
            _term_action = "open_local_terminal" if local else "open_terminal"
            terminal_kwargs = {"shell": shell} if shell else {}
            result = svc._request(_term_action, cols=cols, rows=rows,
                                  **terminal_kwargs)
            session_id = result.get("session_id", "") if isinstance(result, dict) else str(result)

            # Register terminal session for WS proxy
            # Both Docker and local terminals use the same relay WS path
            # (local terminal data arrives via host helper → relay → progress → dispatch)
            from services.terminal_proxy import register_terminal, terminal_ws_handler
            _term_token = register_terminal(
                session_id, relay_id, relay_service=svc,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "")

            # Register WS route (once)
            _owner = "_terminal_proxy"
            http_svc = None
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            for _sid, _sdef in greg.get_all("global", "").items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    http_svc = greg.get_live_instance("global", "", _sid)
                    if http_svc:
                        break
            if http_svc:
                existing = [r for r in http_svc.get_routes() if r.get("owner") == _owner]
                if not existing:
                    http_svc.register_route(
                        "GET", "/terminal/{session_id}/{token}",
                        _owner,
                        callback=lambda req: None,
                        ws_handler=terminal_ws_handler,
                    )

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "token": _term_token,
                "relay_id": relay_id,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "list_cc_interactive_terminals":
        conversation_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        service_id = body.get("service_id", "") or ""
        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            sessions = InteractiveClaudeCodePool.instance().list_sessions(
                user_id, conversation_id, service_id=service_id)
            flowfile.set_content(json.dumps({"sessions": sessions}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "open_cc_interactive_terminal":
        agent_name = body.get("agent_name", "") or body.get("agent", "")
        conversation_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        service_id = body.get("service_id", "") or ""
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            import uuid
            from core.claude_code_interactive_pool import InteractiveClaudeCodePool
            from core.docker_utils import docker_cmd
            from services.terminal_proxy import register_terminal, terminal_ws_handler

            state = InteractiveClaudeCodePool.instance().find_session(
                user_id, conversation_id, agent_name, service_id=service_id)
            if not state:
                flowfile.set_content(json.dumps({
                    "error": f"No live Claude Code interactive tmux session for agent '{agent_name}'"
                }).encode())
                return [flowfile]

            session_id = f"cci_term_{uuid.uuid4().hex[:12]}"
            cols = int(body.get("cols", 120) or 120)
            rows = int(body.get("rows", 30) or 30)
            bridge_script = r'''
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

rows = int(os.environ.get("PAWFLOW_TERM_ROWS", "30") or "30")
cols = int(os.environ.get("PAWFLOW_TERM_COLS", "120") or "120")
master, slave = pty.openpty()
try:
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
except Exception:
    pass
env = dict(os.environ)
env.setdefault("TERM", "xterm-256color")
for option in (("mouse", "on"), ("history-limit", "50000")):
    try:
        subprocess.run(["tmux", "set-option", "-g", *option],
                       capture_output=True, timeout=2)
    except Exception:
        pass
proc = subprocess.Popen(
    ["tmux", "attach-session", "-t", "pawflow"],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    close_fds=True,
    start_new_session=True,
    env=env,
)
os.close(slave)
time.sleep(0.1)
try:
    os.write(master, b"\x0c")
except Exception:
    pass
stdin_fd = sys.stdin.fileno()
stdout = sys.stdout.buffer
try:
    while True:
        if proc.poll() is not None:
            try:
                data = os.read(master, 65536)
                if data:
                    stdout.write(data)
                    stdout.flush()
            except OSError:
                pass
            break
        readable, _, _ = select.select([stdin_fd, master], [], [], 0.2)
        if master in readable:
            data = os.read(master, 65536)
            if not data:
                break
            stdout.write(data)
            stdout.flush()
        if stdin_fd in readable:
            data = os.read(stdin_fd, 65536)
            if not data:
                break
            os.write(master, data)
finally:
    try:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.close(master)
    except Exception:
        pass
'''
            cmd = docker_cmd() + [
                "exec", "-i", "--user", "1000:1000",
                "-e", f"PAWFLOW_TERM_COLS={cols}",
                "-e", f"PAWFLOW_TERM_ROWS={rows}",
                "-e", "TERM=xterm-256color",
                state.name,
                "python3", "-c", bridge_script,
            ]
            _term_token = register_terminal(
                session_id, "__server__", relay_service=None,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                server_pipe_command=cmd,
                server_pipe_resize_command=(docker_cmd() + [
                    "exec", "--user", "1000:1000", state.name,
                    "tmux", "resize-window", "-t", "pawflow",
                    "-x", "{cols}", "-y", "{rows}",
                ]))

            _owner = "_terminal_proxy"
            http_svc = None
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            for _sid, _sdef in greg.get_all("global", "").items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    http_svc = greg.get_live_instance("global", "", _sid)
                    if http_svc:
                        break
            if http_svc:
                existing = [r for r in http_svc.get_routes() if r.get("owner") == _owner]
                if not existing:
                    http_svc.register_route(
                        "GET", "/terminal/{session_id}/{token}",
                        _owner,
                        callback=lambda req: None,
                        ws_handler=terminal_ws_handler,
                    )

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "token": _term_token,
                "relay_id": f"cc:{agent_name}",
                "container": state.name,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action in {"open_antigravity_interactive_terminal", "start_antigravity_observer"}:
        agent_name = body.get("agent_name", "") or body.get("agent", "")
        conversation_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        service_id = body.get("service_id", "") or ""
        model = body.get("model", "") or ""
        if not agent_name:
            flowfile.set_content(json.dumps({"error": "Missing agent_name"}).encode())
            return [flowfile]
        if not conversation_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id"}).encode())
            return [flowfile]
        try:
            import uuid
            from core.antigravity_observer_pool import AntigravityObserverPool
            from core.docker_utils import docker_cmd
            from services.terminal_proxy import register_terminal, terminal_ws_handler

            if not service_id:
                try:
                    from core.conv_agent_config import get_agent_config
                    service_id = (get_agent_config(conversation_id, agent_name).get("llm_service") or "")
                except Exception:
                    service_id = ""
            pool = AntigravityObserverPool.instance()
            state = pool.find_session(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_name=agent_name,
                service_id=service_id,
            )
            if not state and service_id:
                state = pool.find_session(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    service_id="",
                )
            if not state:
                flowfile.set_content(json.dumps({
                    "error": f"No live Antigravity tmux session for agent '{agent_name}'"
                }).encode())
                return [flowfile]

            session_id = f"agy_term_{uuid.uuid4().hex[:12]}"
            cols = int(body.get("cols", 120) or 120)
            rows = int(body.get("rows", 30) or 30)
            bridge_script = r'''
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

rows = int(os.environ.get("PAWFLOW_TERM_ROWS", "30") or "30")
cols = int(os.environ.get("PAWFLOW_TERM_COLS", "120") or "120")
tmux_session = os.environ.get("PAWFLOW_TMUX_SESSION", "pawflow-agy")
master, slave = pty.openpty()
try:
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
except Exception:
    pass
env = dict(os.environ)
env.setdefault("TERM", "xterm-256color")
for option in (("mouse", "on"), ("history-limit", "50000")):
    try:
        subprocess.run(["tmux", "set-option", "-g", *option],
                       capture_output=True, timeout=2)
    except Exception:
        pass
proc = subprocess.Popen(
    ["tmux", "attach-session", "-t", tmux_session],
    stdin=slave,
    stdout=slave,
    stderr=slave,
    close_fds=True,
    start_new_session=True,
    env=env,
)
os.close(slave)
time.sleep(0.1)
try:
    os.write(master, b"\x0c")
except Exception:
    pass
stdin_fd = sys.stdin.fileno()
stdout = sys.stdout.buffer
try:
    while True:
        if proc.poll() is not None:
            try:
                data = os.read(master, 65536)
                if data:
                    stdout.write(data)
                    stdout.flush()
            except OSError:
                pass
            break
        readable, _, _ = select.select([stdin_fd, master], [], [], 0.2)
        if master in readable:
            data = os.read(master, 65536)
            if not data:
                break
            stdout.write(data)
            stdout.flush()
        if stdin_fd in readable:
            data = os.read(stdin_fd, 65536)
            if not data:
                break
            os.write(master, data)
finally:
    try:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        pass
    try:
        os.close(master)
    except Exception:
        pass
'''
            cmd = docker_cmd() + [
                "exec", "-i", "--user", "1000:1000",
                "-e", f"PAWFLOW_TERM_COLS={cols}",
                "-e", f"PAWFLOW_TERM_ROWS={rows}",
                "-e", "PAWFLOW_TMUX_SESSION=pawflow-agy",
                "-e", "TERM=xterm-256color",
                state.name,
                "python3", "-c", bridge_script,
            ]
            _term_token = register_terminal(
                session_id, "__server__", relay_service=None,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                server_pipe_command=cmd,
                server_pipe_resize_command=(docker_cmd() + [
                    "exec", "--user", "1000:1000", state.name,
                    "tmux", "resize-window", "-t", "pawflow-agy",
                    "-x", "{cols}", "-y", "{rows}",
                ]))

            _owner = "_terminal_proxy"
            http_svc = None
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            for _sid, _sdef in greg.get_all("global", "").items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    http_svc = greg.get_live_instance("global", "", _sid)
                    if http_svc:
                        break
            if http_svc:
                existing = [r for r in http_svc.get_routes() if r.get("owner") == _owner]
                if not existing:
                    http_svc.register_route(
                        "GET", "/terminal/{session_id}/{token}",
                        _owner,
                        callback=lambda req: None,
                        ws_handler=terminal_ws_handler,
                    )

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "token": _term_token,
                "relay_id": f"agy:{agent_name}",
                "container": state.name,
                "log_path": state.log_path,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "close_terminal":
        session_id = body.get("session_id", "")
        relay_id = body.get("relay_id", "")
        if not session_id:
            flowfile.set_content(json.dumps({"error": "Missing session_id"}).encode())
            return [flowfile]
        # Look up relay_id from terminal session if not provided
        if not relay_id:
            try:
                from services.terminal_proxy import get_terminal
                tsess = get_terminal(session_id)
                if tsess:
                    relay_id = tsess.get("relay_service_id", "")
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if svc:
                svc._request("close_terminal", session_id=session_id)
            from services.terminal_proxy import unregister_terminal
            unregister_terminal(session_id)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "open_code_server":
        relay_id = body.get("relay_id", "")
        local = body.get("local", False)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]
            _cs_action = "start_local_code_server" if local else "start_code_server"
            logger.info("[open_code_server] Starting %s on relay %s", _cs_action, relay_id)
            result = svc._request(_cs_action)
            logger.debug("[open_code_server] start_code_server result: %s", result)
            port = result.get("port") if isinstance(result, dict) else None
            if not port:
                flowfile.set_content(json.dumps({"error": "Failed to get code-server port", "detail": str(result)}).encode())
                return [flowfile]

            # Register HTTP/WS proxy routes (tunneled via relay)
            from services.code_server_proxy import (
                register_code_server, code_http_proxy, code_ws_proxy,
            )
            _cs_session_id, _cs_token = register_code_server(
                relay_id, port, svc,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "")

            _owner = "_code_server_proxy"
            http_svc = None
            from core.service_registry import ServiceRegistry
            greg = ServiceRegistry.get_instance()
            for _sid, _sdef in greg.get_all("global", "").items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    http_svc = greg.get_live_instance("global", "", _sid)
                    if http_svc:
                        break
            logger.debug("[open_code_server] http_svc=%s", http_svc)
            if http_svc:
                existing = [r for r in http_svc.get_routes() if r.get("owner") == _owner]
                logger.debug("[open_code_server] existing code routes: %s", existing)
                if not existing:
                    # Root route (trailing slash, empty path) — matches
                    # the URL we hand to the user (`/code/<sid>/<tok>/`).
                    # The `{path+}` pattern alone requires at least one
                    # segment after the slash, so without this entry the
                    # iframe lands on a 404.
                    http_svc.register_route(
                        "GET", "/code/{session_id}/{token}/",
                        _owner,
                        callback=code_http_proxy,
                        ws_handler=code_ws_proxy,
                    )
                    http_svc.register_route(
                        "GET", "/code/{session_id}/{token}/{path+}",
                        _owner,
                        callback=code_http_proxy,
                        ws_handler=code_ws_proxy,
                    )
                    for _m in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
                        http_svc.register_route(
                            _m, "/code/{session_id}/{token}/",
                            _owner,
                            callback=code_http_proxy,
                        )
                        http_svc.register_route(
                            _m, "/code/{session_id}/{token}/{path+}",
                            _owner,
                            callback=code_http_proxy,
                        )

            conv_id = body.get("conversation_id", "")
            _rl = relay_id
            _pt = port
            _csid = _cs_session_id
            _ctok = _cs_token

            def _bg_wait_code():
                time.sleep(2)
                logger.info("[code-server] Ready on relay %s port %s", _rl, _pt)
                if conv_id:
                    _url = f"/code/{_csid}/{_ctok}/"
                    _publish_command_result(conv_id, {
                        "ok": True, "port": _pt, "relay_id": _rl,
                        "session_id": _csid, "token": _ctok,
                        "url": _url,
                        "message": f"Code server ready at {_url}",
                    })

            threading.Thread(target=_bg_wait_code, daemon=True).start()
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Starting code server...",
                "port": port, "relay_id": relay_id,
                "session_id": _cs_session_id, "token": _cs_token,
                "url": f"/code/{_cs_session_id}/{_cs_token}/",
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "close_code_server":
        relay_id = body.get("relay_id", "")
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if svc:
                svc._request("stop_code_server")
            # Unregister proxy session (routes stay for other relays)
            try:
                from services.code_server_proxy import unregister_code_server
                unregister_code_server(relay_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "open_desktop":
        relay_id = body.get("relay_id", "")
        local_screen = body.get("local_screen", False)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]

            _action_start = "start_local_desktop" if local_screen else "start_desktop"
            _action_status_key = "local_screen_running" if local_screen else "running"
            _session_prefix = "local_desktop" if local_screen else "desktop"

            # Check if already running (idempotent)
            status = svc._request("desktop_status")
            logger.info("[open_desktop] desktop_status for %s: %s (key=%s)", relay_id, status, _action_status_key)
            if isinstance(status, dict) and status.get(_action_status_key):
                _login_sid = flowfile.get_attribute("auth.session_id") or ""
                if local_screen:
                    _novnc_port = status.get("local_screen_novnc_port")
                    if _novnc_port:
                        _sid = f"{_session_prefix}_{relay_id}"
                        from services.vnc_proxy import register_session
                        _vtok = register_session(
                            _sid, _novnc_port,
                            owner_user_id=user_id,
                            login_session_id=_login_sid)
                        _ensure_vnc_routes()
                        # Re-register audio for already-running desktop
                        _audio_token = ""  # nosec B105
                        try:
                            from services.audio_proxy import register_audio_source
                            _audio_port = status.get("local_screen_audio_port")
                            if _audio_port:
                                _relay_addr = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                                _audio_token = register_audio_source(_sid, _relay_addr, _audio_port,
                                                                     owner_user_id=user_id,
                                                                     login_session_id=_login_sid)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        flowfile.set_content(json.dumps({
                            "ok": True, "already_running": True, "local_screen": True,
                            "relay_id": relay_id,
                            "url": f"/vnc/{_sid}/{_vtok}/vnc.html?autoconnect=true&resize=scale&path=vnc/{_sid}/{_vtok}/websockify",
                            "audio_session": _sid if _audio_token else "",
                            "audio_token": _audio_token,
                        }).encode())
                        return [flowfile]
                else:
                    _hp = _get_desktop_host_port(relay_id)
                    logger.info("[open_desktop] already running, host_port=%s for %s", _hp, relay_id)
                    if _hp:
                        _sid = f"{_session_prefix}_{relay_id}"
                        from services.vnc_proxy import register_session
                        _vtok = register_session(
                            _sid, _hp,
                            owner_user_id=user_id,
                            login_session_id=_login_sid)
                        _ensure_vnc_routes()
                        # Re-register audio for already-running desktop
                        _audio_token = ""  # nosec B105
                        try:
                            from services.audio_proxy import register_audio_source
                            _ahp = 0
                            try:
                                from core.server_relay_manager import ServerRelayManager
                                for _entry in ServerRelayManager.get_instance().list_all():
                                    if _entry.get("relay_id") == relay_id:
                                        _ahp = _entry.get("audio_host_port", 0)
                                        break
                            except Exception:
                                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                            if not _ahp:
                                _ahp = _get_container_port(relay_id, 6180)
                            if _ahp:
                                _audio_token = register_audio_source(_sid, "127.0.0.1", _ahp,
                                                                     owner_user_id=user_id,
                                                                     login_session_id=_login_sid)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        flowfile.set_content(json.dumps({
                            "ok": True, "already_running": True,
                            "relay_id": relay_id,
                            "url": f"/vnc/{_sid}/{_vtok}/vnc.html?autoconnect=true&resize=scale&path=vnc/{_sid}/{_vtok}/websockify",
                            "audio_session": _sid if _audio_token else "",
                            "audio_token": _audio_token,
                        }).encode())
                        return [flowfile]

            logger.info("[open_desktop] Starting %s on relay %s", _action_start, relay_id)
            result = svc._request(_action_start)
            logger.debug("[open_desktop] %s result: %s", _action_start, result)
            # _request() unwraps the relay response — result is the inner data dict directly
            novnc_port = result.get("novnc_port") if isinstance(result, dict) else None
            if not novnc_port:
                flowfile.set_content(json.dumps({"error": f"Failed to start {_action_start}", "detail": str(result)}).encode())
                return [flowfile]

            _login_sid = flowfile.get_attribute("auth.session_id") or ""
            if local_screen:
                # Local screen: the relay runs VNC+websockify on its own machine.
                # The novnc_port is directly on the relay's host (not in Docker).
                # Use the relay's address to proxy.
                _relay_addr = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                host_port = novnc_port
                # For local relays connecting from the same machine, use the port directly
                session_id = f"{_session_prefix}_{relay_id}"
                from services.vnc_proxy import register_session
                _vtok = register_session(
                    session_id, host_port,
                    owner_user_id=user_id,
                    login_session_id=_login_sid,
                    host=_relay_addr)
            else:
                # Docker: get the published host port
                host_port = _get_desktop_host_port(relay_id)
                if not host_port:
                    flowfile.set_content(json.dumps({"error": "Desktop started but host port not found"}).encode())
                    return [flowfile]
                session_id = f"{_session_prefix}_{relay_id}"
                from services.vnc_proxy import register_session
                _vtok = register_session(
                    session_id, host_port,
                    owner_user_id=user_id,
                    login_session_id=_login_sid)

            _ensure_vnc_routes()

            # Register audio source if available
            _audio_token = ""  # nosec B105
            try:
                from services.audio_proxy import register_audio_source
                if local_screen:
                    # Local relay: audio_capture runs on relay host
                    _audio_port = result.get("audio_port") if isinstance(result, dict) else None
                    if _audio_port:
                        _audio_token = register_audio_source(session_id, _relay_addr, _audio_port,
                                                             owner_user_id=user_id,
                                                             login_session_id=_login_sid)
                else:
                    # Docker: get audio_host_port from relay metadata, fallback to docker port 6180
                    _audio_host_port = 0
                    try:
                        from core.server_relay_manager import ServerRelayManager
                        for _entry in ServerRelayManager.get_instance().list_all():
                            if _entry.get("relay_id") == relay_id:
                                _audio_host_port = _entry.get("audio_host_port", 0)
                                break
                    except Exception:
                        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                    if not _audio_host_port:
                        _audio_host_port = _get_container_port(relay_id, 6180)
                    if _audio_host_port:
                        _audio_token = register_audio_source(session_id, "127.0.0.1", _audio_host_port,
                                                             owner_user_id=user_id,
                                                             login_session_id=_login_sid)
            except Exception as _ae:
                logger.debug("[open_desktop] Audio registration skipped: %s", _ae)

            flowfile.set_content(json.dumps({
                "ok": True, "relay_id": relay_id, "local_screen": local_screen,
                "url": f"/vnc/{session_id}/{_vtok}/vnc.html?autoconnect=true&resize=scale&path=vnc/{session_id}/{_vtok}/websockify",
                "audio_session": session_id if _audio_token else "",
                "audio_token": _audio_token,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "close_desktop":
        relay_id = body.get("relay_id", "")
        local_screen = body.get("local_screen", False)
        if not relay_id:
            flowfile.set_content(json.dumps({"error": "Missing relay_id"}).encode())
            return [flowfile]
        try:
            svc = _find_relay_svc(relay_id)
            if svc:
                svc._request("stop_local_desktop" if local_screen else "stop_desktop")
            from services.vnc_proxy import unregister_session
            _prefix = "local_desktop" if local_screen else "desktop"
            _session_id = f"{_prefix}_{relay_id}"
            unregister_session(_session_id)
            try:
                from services.audio_proxy import unregister_audio_source
                unregister_audio_source(_session_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Port forwarding ─────────────────────────────────────────────

    if action == "port_forward_add":
        relay_id = body.get("relay_id", "")
        int_port = body.get("port", 0) or body.get("int_port", 0)
        ext_port = body.get("ext_port", 0) or int_port
        if not relay_id or not int_port:
            flowfile.set_content(json.dumps({"error": "Missing relay_id or port"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            int_port = int(int_port)
            ext_port = int(ext_port)
            svc = _find_relay_svc(relay_id)
            if not svc:
                flowfile.set_content(json.dumps({"error": f"Relay '{relay_id}' not found"}).encode())
                return [flowfile]

            from services.port_forward_proxy import add_forward, fwd_http_proxy, fwd_root_redirect, _ROUTE_OWNER
            _ttl = int(body.get("ttl_seconds", 28800)) or 28800
            first, _fwd_id, _fwd_token = add_forward(
                relay_id, int_port, svc, ext_port=ext_port,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                ttl_seconds=_ttl,
                description=body.get("description", "") or "")

            # Register generic routes once (shared by all forwards).
            # The root pattern (trailing slash, no `{path+}`) is needed
            # because `{path+}` requires at least one segment, so the
            # exact URL we hand to the user (`/fwd/<fid>/<tok>/`) would
            # otherwise 404. fwd_root_redirect on the no-slash variant
            # nudges browsers that drop the trailing slash.
            if first:
                http_svc = _find_http_listener()
                if http_svc:
                    for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                        http_svc.register_route(method, "/fwd/{forward_id}/{token}/",
                                                _ROUTE_OWNER, callback=fwd_http_proxy)
                        http_svc.register_route(method, "/fwd/{forward_id}/{token}/{path+}",
                                                _ROUTE_OWNER, callback=fwd_http_proxy)
                    http_svc.register_route("GET", "/fwd/{forward_id}/{token}",
                                            _ROUTE_OWNER, callback=fwd_root_redirect)

            _url = f"/fwd/{_fwd_id}/{_fwd_token}/"
            flowfile.set_content(json.dumps({
                "ok": True, "relay_id": relay_id,
                "forward_id": _fwd_id, "token": _fwd_token,
                "int_port": int_port, "ext_port": ext_port, "url": _url,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "port_forward_remove":
        forward_id = body.get("forward_id", "") or ""
        relay_id = body.get("relay_id", "")
        ext_port = body.get("ext_port", 0) or body.get("port", 0)
        if not forward_id and (not relay_id or not ext_port):
            flowfile.set_content(json.dumps({
                "error": "Missing forward_id (or relay_id+port for legacy)",
            }).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from services.port_forward_proxy import remove_forward, _ROUTE_OWNER
            if forward_id:
                last = remove_forward(forward_id=forward_id)
            else:
                last = remove_forward(relay_id=relay_id, ext_port=int(ext_port))
            if last:
                http_svc = _find_http_listener()
                if http_svc:
                    http_svc.unregister_routes(_ROUTE_OWNER)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "port_forward_list":
        from services.port_forward_proxy import list_forwards
        flowfile.set_content(json.dumps({"forwards": list_forwards()}).encode())
        return [flowfile]

    # ── Private gateway admin ────────────────────────────────────────

    def _private_gateway_for_body():
        service_id = body.get("service_id", "") or body.get("private_gateway_service_id", "")
        if not service_id:
            return None
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(service_id, user_id=user_id)
        if not svc or getattr(svc, "TYPE", "") != "privateGateway":
            raise ValueError(f"Private gateway service '{service_id}' not found")
        return svc

    if action == "private_gateway_list_bans":
        try:
            svc = _private_gateway_for_body()
            if svc is not None:
                bans = svc.list_bans()
            else:
                from services.private_gateway import list_bans
                bans = list_bans()
            flowfile.set_content(json.dumps({"bans": bans}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "private_gateway_unban":
        ip = body.get("ip", "")
        if not ip:
            flowfile.set_content(json.dumps({"error": "Missing ip"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            svc = _private_gateway_for_body()
            if svc is not None:
                was_banned = svc.unban_ip(ip)
            else:
                from services.private_gateway import unban_ip
                was_banned = unban_ip(ip)
            flowfile.set_content(json.dumps({"ok": True, "was_banned": was_banned}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "private_gateway_status":
        try:
            svc = _private_gateway_for_body()
            if svc is not None:
                enabled = svc.is_enabled()
                bans = svc.list_bans()
            else:
                from services.private_gateway import PrivateGateway, list_bans
                enabled = PrivateGateway.is_enabled_static()
                bans = list_bans()
            flowfile.set_content(json.dumps({
                "enabled": enabled,
                "banned_count": len(bans),
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Docker VM management ──────────────────────────────────────

    if action == "list_vms":
        from core.docker_utils import list_containers, get_server_id
        owner = body.get("owner", "")  # empty = all pf-* containers
        containers = list_containers(owner)
        _srv_id = get_server_id()
        # Enrich with ownership info
        for c in containers:
            name = c.get("name", "")
            if _srv_id and f"pf-{_srv_id[:12]}" in name.replace(".", "-").replace("_", "-"):
                c["owner"] = "server"
            else:
                c["owner"] = "client"
        flowfile.set_content(json.dumps({"vms": containers}).encode())
        return [flowfile]

    if action == "kill_vm":
        container_id = body.get("container_id", "")
        if not container_id:
            flowfile.set_content(json.dumps({"error": "Missing container_id"}).encode())
            return [flowfile]
        from core.docker_utils import docker_rm
        try:
            docker_rm(container_id, force=True)
            flowfile.set_content(json.dumps({"ok": True, "killed": container_id}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Codex login via server (noVNC) ──────────────────────────────
    # Mirror of claude_code_server_login but drives `codex login` (OAuth
    # PKCE against auth.openai.com) inside the same shared image. Each
    # CLI keeps its own action namespace (codex_server_login_*) so the
    # three login flows can evolve separately.

    if action == "codex_server_login":
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            sdef = ServiceRegistry.get_instance().resolve_definition(service_id)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            if _credential_provider_for_service(service_id, user_id) != "codex-app-server":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a codex credential provider"}).encode())
                return [flowfile]
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Cannot verify service: {e}"}).encode())
            return [flowfile]

        try:
            import uuid as _uuid
            from pawflow_relay.utils import find_free_port as _find_free_port
            session_id = _uuid.uuid4().hex[:12]
            free_port = _find_free_port()
            container_name = f"pawflow-codex-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"
            logger.info("[codex-login] Creating session %s (port %d)", session_id, free_port)
            from services.vnc_proxy import register_session, vnc_ws_proxy, vnc_http_proxy
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                container=container_name, service_id=service_id,
                user_id=user_id, volume=volume_name,
                launch_time=time.time(), ready=False)
        except Exception as e:
            logger.error("[codex-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup():
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--tmpfs", "/workspace:rw,size=64m",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    "--entrypoint", "bash",
                    image,
                    "/opt/pawflow/codex_auth_login.sh",
                ]
                logger.info("[codex-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[codex-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[codex-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            import urllib.request
            for _attempt in range(15):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{free_port}/", timeout=2)  # nosec B310 - local noVNC readiness probe.
                    logger.info("[codex-login] noVNC ready on port %d", free_port)
                    break
                except Exception:
                    time.sleep(1)

            # Register VNC proxy routes (once, shared by all sessions across CLIs).
            # Without this the iframe URL /vnc/{session_id}/vnc.html 404s and Chrome
            # shows "refused to connect" — was the codex-only-fails-when-claude-not-
            # logged-in-first symptom.
            try:
                svc = None
                try:
                    from core.service_registry import ServiceRegistry
                    greg = ServiceRegistry.get_instance()
                    for _sid, _sdef in greg.get_all("global", "").items():
                        if getattr(_sdef, "service_type", "") == "httpListener":
                            svc = greg.get_live_instance("global", "", _sid)
                            if svc:
                                break
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if svc:
                    _vnc_owner = "_vnc_proxy"
                    existing = [r for r in svc.get_routes() if r.get("owner") == _vnc_owner]
                    if not existing:
                        svc.register_route("GET", "/vnc/{session_id}/{token}/websockify",
                                           _vnc_owner, callback=lambda req: None,
                                           ws_handler=vnc_ws_proxy)
                        svc.register_route("GET", "/vnc/{session_id}/{token}/{path+}",
                                           _vnc_owner, callback=vnc_http_proxy)
                else:
                    logger.warning("[codex-login] HTTPListenerService NOT FOUND")
            except Exception as e:
                logger.warning("[codex-login] Route registration failed: %s", e)

            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id, "service_id": service_id,
                    "token": _vnc_token,
                    "cli": "codex",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup, daemon=True, name=f"codex-login-{session_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting codex login container...",
        }).encode())
        return [flowfile]

    if action == "codex_server_login_cleanup":
        session_id = body.get("session_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if session:
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", session.get("container", "")],  # nosec B603
                        capture_output=True, timeout=10)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            unregister_session(session_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "codex_server_login_status":
        session_id = body.get("session_id", "")
        service_id = body.get("service_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if not session:
            flowfile.set_content(json.dumps({"error": "Unknown session"}).encode())
            return [flowfile]
        if session.get("error"):
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": session["error"]}).encode())
            return [flowfile]
        if not session.get("ready"):
            flowfile.set_content(json.dumps({"status": "starting"}).encode())
            return [flowfile]

        import subprocess as _sp  # nosec B404
        from core.docker_utils import docker_cmd as _docker_cmd
        container = session["container"]
        launch_time = session.get("launch_time", 0)
        if time.time() - launch_time > 180:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": "Codex login timed out (3 min)"}).encode())
            return [flowfile]

        try:
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "stat -c %Y /home/pawflow/.codex/auth.json 2>/dev/null || stat -c %Y /workspace/auth.json 2>/dev/null"],
                capture_output=True, text=True, timeout=5)
            if stat_result.returncode != 0:
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            file_mtime = int(stat_result.stdout.strip())
            if file_mtime < int(launch_time):
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
        except Exception:
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]

        try:
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.codex/auth.json 2>/dev/null || cat /workspace/auth.json"],
                capture_output=True, text=True, timeout=10)
            from core.llm_providers.codex_session import parse_auth_json
            parsed = parse_auth_json(read_result.stdout)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read codex credentials: {e}"}).encode())
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            return [flowfile]

        access_token = parsed.get("access_token", "")
        refresh_token = parsed.get("refresh_token", "")
        expires_at = parsed.get("expires_at", 0)
        account = parsed.get("account", "")
        id_token = parsed.get("id_token", "")
        if access_token and refresh_token:
            try:
                _store_codex_tokens(service_id, access_token, refresh_token, expires_at, account=account, id_token=id_token)
            except Exception as e:
                logger.warning("Failed to save codex credentials: %s", e)
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        if not access_token:
            flowfile.set_content(json.dumps({"error": "No access_token in codex auth.json"}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Codex credentials saved!",
        }).encode())
        return [flowfile]

    # ── Gemini login via server (noVNC) ─────────────────────────────
    # Mirror of claude_code_server_login but drives the gemini OAuth dance
    # (first interactive launch with selectedAuthType=oauth-personal triggers
    # the Google OAuth browser flow). Independent action namespace.

    if action == "gemini_server_login":
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            sdef = ServiceRegistry.get_instance().resolve_definition(service_id)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            if _credential_provider_for_service(service_id, user_id) != "gemini":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a gemini credential provider"}).encode())
                return [flowfile]
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Cannot verify service: {e}"}).encode())
            return [flowfile]

        try:
            import uuid as _uuid
            from pawflow_relay.utils import find_free_port as _find_free_port
            session_id = _uuid.uuid4().hex[:12]
            free_port = _find_free_port()
            container_name = f"pawflow-gemini-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"
            logger.info("[gemini-login] Creating session %s (port %d)", session_id, free_port)
            from services.vnc_proxy import register_session, vnc_ws_proxy, vnc_http_proxy
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                container=container_name, service_id=service_id,
                user_id=user_id, volume=volume_name,
                launch_time=time.time(), ready=False)
        except Exception as e:
            logger.error("[gemini-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup_gemini():
            import os as _os
            import subprocess as _sp  # nosec B404
            from core.docker_utils import (
                docker_cmd as _docker_cmd,
                to_host_path as _to_host_path,
                translate_path as _translate_path,
            )
            try:
                _project_root = _os.path.dirname(_os.path.dirname(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
                _script_src = _os.path.join(
                    _project_root, "docker", "claude-code", "gemini_auth_login.sh")
                _script_mount = []
                if _os.path.exists(_script_src):
                    _script_mount = [
                        "-v",
                        f"{_translate_path(_to_host_path(_script_src))}:/opt/pawflow/gemini_auth_login.sh:ro",
                    ]
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--tmpfs", "/workspace:rw,size=64m",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    *_script_mount,
                    "--entrypoint", "bash",
                    image,
                    "/opt/pawflow/gemini_auth_login.sh",
                ]
                logger.info("[gemini-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[gemini-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[gemini-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            import urllib.request
            for _attempt in range(15):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{free_port}/", timeout=2)  # nosec B310 - local noVNC readiness probe.
                    logger.info("[gemini-login] noVNC ready on port %d", free_port)
                    break
                except Exception:
                    time.sleep(1)

            # Register VNC proxy routes (once, shared by all sessions across CLIs).
            try:
                svc = None
                try:
                    from core.service_registry import ServiceRegistry
                    greg = ServiceRegistry.get_instance()
                    for _sid, _sdef in greg.get_all("global", "").items():
                        if getattr(_sdef, "service_type", "") == "httpListener":
                            svc = greg.get_live_instance("global", "", _sid)
                            if svc:
                                break
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                if svc:
                    _vnc_owner = "_vnc_proxy"
                    existing = [r for r in svc.get_routes() if r.get("owner") == _vnc_owner]
                    if not existing:
                        svc.register_route("GET", "/vnc/{session_id}/{token}/websockify",
                                           _vnc_owner, callback=lambda req: None,
                                           ws_handler=vnc_ws_proxy)
                        svc.register_route("GET", "/vnc/{session_id}/{token}/{path+}",
                                           _vnc_owner, callback=vnc_http_proxy)
                else:
                    logger.warning("[gemini-login] HTTPListenerService NOT FOUND")
            except Exception as e:
                logger.warning("[gemini-login] Route registration failed: %s", e)

            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id, "service_id": service_id,
                    "token": _vnc_token,
                    "cli": "gemini",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup_gemini, daemon=True, name=f"gemini-login-{session_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting gemini login container...",
        }).encode())
        return [flowfile]

    if action == "gemini_server_login_cleanup":
        session_id = body.get("session_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if session:
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", session.get("container", "")],  # nosec B603
                        capture_output=True, timeout=10)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            unregister_session(session_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "gemini_server_login_status":
        session_id = body.get("session_id", "")
        service_id = body.get("service_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if not session:
            flowfile.set_content(json.dumps({"error": "Unknown session"}).encode())
            return [flowfile]
        if session.get("error"):
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": session["error"]}).encode())
            return [flowfile]
        if not session.get("ready"):
            flowfile.set_content(json.dumps({"status": "starting"}).encode())
            return [flowfile]

        import subprocess as _sp  # nosec B404
        from core.docker_utils import docker_cmd as _docker_cmd
        container = session["container"]
        launch_time = session.get("launch_time", 0)
        if time.time() - launch_time > 180:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": "Gemini login timed out (3 min)"}).encode())
            return [flowfile]

        try:
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "stat -c %Y /home/pawflow/.gemini/oauth_creds.json 2>/dev/null || stat -c %Y /workspace/oauth_creds.json 2>/dev/null"],
                capture_output=True, text=True, timeout=5)
            if stat_result.returncode != 0:
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            file_mtime = int(stat_result.stdout.strip())
            if file_mtime < int(launch_time):
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
        except Exception:
            flowfile.set_content(json.dumps({"status": "pending"}).encode())
            return [flowfile]

        try:
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.gemini/oauth_creds.json 2>/dev/null || cat /workspace/oauth_creds.json"],
                capture_output=True, text=True, timeout=10)
            from core.llm_providers.gemini_session import parse_oauth_creds_json
            parsed = parse_oauth_creds_json(read_result.stdout)
            # Also try to read google_accounts.json for the account label.
            account = ""
            try:
                acc_result = _sp.run(  # nosec B603
                    _docker_cmd() + ["exec", container, "bash", "-c",
                                      "cat /home/pawflow/.gemini/google_accounts.json 2>/dev/null || cat /workspace/google_accounts.json"],
                    capture_output=True, text=True, timeout=5)
                if acc_result.returncode == 0 and acc_result.stdout.strip():
                    _accs = json.loads(acc_result.stdout)
                    if isinstance(_accs, dict) and _accs:
                        account = next(iter(_accs.keys()), "") or ""
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read gemini credentials: {e}"}).encode())
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
            unregister_session(session_id)
            return [flowfile]

        access_token = parsed.get("access_token", "")
        refresh_token = parsed.get("refresh_token", "")
        expires_at = parsed.get("expires_at", 0)
        if access_token and refresh_token:
            try:
                _store_gemini_tokens(service_id, access_token, refresh_token, expires_at, account=account)
            except Exception as e:
                logger.warning("Failed to save gemini credentials: %s", e)
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        if not access_token:
            flowfile.set_content(json.dumps({"error": "No access_token in gemini oauth_creds.json"}).encode())
            return [flowfile]
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Gemini credentials saved!",
        }).encode())
        return [flowfile]

    # -- Rclone OAuth login via server (noVNC) ------------------------

    if action == "rclone_server_login":
        service_id = body.get("service_id", "")
        conversation_id = body.get("conversation_id", "")
        scope_arg = body.get("scope", "")
        if not service_id:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            sdef = _resolve_service_definition_for_action(
                service_id, user_id, conversation_id, scope_arg)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            if sdef.service_type != "rcloneOAuthCredentials":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not an rclone OAuth credential service"}).encode())
                return [flowfile]
            if sdef.scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global rclone credential login"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            cfg = sdef.config or {}
            rclone_type = str(cfg.get("provider") or "").strip()
            if rclone_type not in {"drive", "onedrive"}:
                flowfile.set_content(json.dumps({
                    "error": "Server login is only available for drive and onedrive rclone OAuth credentials",
                }).encode())
                return [flowfile]
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Cannot verify service: {e}"}).encode())
            return [flowfile]

        try:
            import uuid as _uuid
            from pawflow_relay.utils import find_free_port as _find_free_port
            session_id = _uuid.uuid4().hex[:12]
            free_port = _find_free_port()
            container_name = f"pawflow-rclone-login-{session_id}"
            image = "pawflow-claude-code:latest"
            logger.info("[rclone-login] Creating session %s (port %d)", session_id, free_port)
            from services.vnc_proxy import register_session, vnc_ws_proxy, vnc_http_proxy
            _vnc_token = register_session(
                session_id, free_port,
                owner_user_id=user_id,
                conversation_id=conversation_id,
                login_session_id=flowfile.get_attribute("auth.session_id") or "",
                container=container_name, service_id=service_id,
                service_scope=sdef.scope, service_scope_id=sdef.scope_id,
                rclone_type=rclone_type, user_id=user_id,
                launch_time=time.time(), ready=False)
        except Exception as e:
            logger.error("[rclone-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup_rclone():
            import os as _os
            import subprocess as _sp  # nosec B404
            from core.docker_utils import (
                docker_cmd as _docker_cmd,
                to_host_path as _to_host_path,
                translate_path as _translate_path,
            )
            try:
                _project_root = _os.path.dirname(_os.path.dirname(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
                _script_src = _os.path.join(
                    _project_root, "docker", "claude-code", "rclone_auth_login.sh")
                _script_mount = []
                if _os.path.exists(_script_src):
                    _script_mount = [
                        "-v",
                        f"{_translate_path(_to_host_path(_script_src))}:/opt/pawflow/rclone_auth_login.sh:ro",
                    ]
                docker_cmd = _docker_cmd() + [
                    "run", "--rm", "--detach",
                    "--name", container_name,
                    "-p", f"{free_port}:6080",
                    "--shm-size", "512m",
                    "-e", "HOME=/home/pawflow",
                    "-e", f"PAWFLOW_RCLONE_TYPE={rclone_type}",
                    *_script_mount,
                    "--entrypoint", "bash",
                    image,
                    "/opt/pawflow/rclone_auth_login.sh",
                ]
                logger.info("[rclone-login] Starting container %s on port %d", container_name, free_port)
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
                if result.returncode != 0:
                    logger.error("[rclone-login] Docker failed: %s", result.stderr[:300])
                    from services.vnc_proxy import update_session_error
                    update_session_error(session_id, f"Docker failed: {result.stderr[:200]}")
                    _publish_command_result(_conv_id, {"error": f"Docker failed: {result.stderr[:200]}"})
                    return
            except Exception as e:
                logger.error("[rclone-login] Docker error: %s", e)
                from services.vnc_proxy import update_session_error
                update_session_error(session_id, str(e))
                _publish_command_result(_conv_id, {"error": f"Login failed: {e}"})
                return

            import urllib.request
            for _attempt in range(15):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{free_port}/", timeout=2)  # nosec B310 - local noVNC readiness probe.
                    logger.info("[rclone-login] noVNC ready on port %d", free_port)
                    break
                except Exception:
                    time.sleep(1)

            try:
                svc = _find_http_listener()
                if svc:
                    _vnc_owner = "_vnc_proxy"
                    existing = [r for r in svc.get_routes() if r.get("owner") == _vnc_owner]
                    if not existing:
                        svc.register_route("GET", "/vnc/{session_id}/{token}/websockify",
                                           _vnc_owner, callback=lambda req: None,
                                           ws_handler=vnc_ws_proxy)
                        svc.register_route("GET", "/vnc/{session_id}/{token}/{path+}",
                                           _vnc_owner, callback=vnc_http_proxy)
                else:
                    logger.warning("[rclone-login] HTTPListenerService NOT FOUND")
            except Exception as e:
                logger.warning("[rclone-login] Route registration failed: %s", e)

            from services.vnc_proxy import update_session_ready
            update_session_ready(session_id)
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                conversation_id, "vnc_login_ready", {
                    "session_id": session_id,
                    "service_id": service_id,
                    "scope": sdef.scope,
                    "token": _vnc_token,
                    "cli": "rclone",
                })

        _conv_id = conversation_id
        threading.Thread(target=_bg_setup_rclone, daemon=True, name=f"rclone-login-{session_id}").start()
        flowfile.set_content(json.dumps({
            "ok": True, "message": "Starting rclone login container...",
        }).encode())
        return [flowfile]

    if action == "rclone_server_login_cleanup":
        session_id = body.get("session_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if session:
            import subprocess as _sp  # nosec B404
            from core.docker_utils import docker_cmd as _docker_cmd
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", session.get("container", "")],  # nosec B603
                        capture_output=True, timeout=10)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            unregister_session(session_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "rclone_server_login_status":
        session_id = body.get("session_id", "")
        service_id = body.get("service_id", "")
        from services.vnc_proxy import _sessions as _vnc_sessions, unregister_session
        session = _vnc_sessions.get(session_id)
        if not session:
            flowfile.set_content(json.dumps({"error": "Unknown session"}).encode())
            return [flowfile]
        if session.get("error"):
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": session["error"]}).encode())
            return [flowfile]
        if not session.get("ready"):
            flowfile.set_content(json.dumps({"status": "starting"}).encode())
            return [flowfile]

        import subprocess as _sp  # nosec B404
        from core.docker_utils import docker_cmd as _docker_cmd
        container = session["container"]
        try:
            result_dir = "/tmp/pawflow-rclone-login"  # nosec B108 - relay-container rclone login scratch dir.
            error_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c", f"cat {result_dir}/rclone_error.txt 2>/dev/null"],
                capture_output=True, text=True)
            if error_result.returncode == 0 and error_result.stdout.strip():
                flowfile.set_content(json.dumps({"error": error_result.stdout.strip()[:500]}).encode())
                return [flowfile]
            stat_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c", f"test -s {result_dir}/rclone_config_body.txt"],
                capture_output=True, text=True)
            if stat_result.returncode != 0:
                flowfile.set_content(json.dumps({"status": "pending"}).encode())
                return [flowfile]
            read_result = _sp.run(  # nosec B603
                _docker_cmd() + ["exec", container, "bash", "-c", f"cat {result_dir}/rclone_config_body.txt"],
                capture_output=True, text=True)
            rclone_config = read_result.stdout.strip()
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read rclone config: {e}"}).encode())
            return [flowfile]

        if not rclone_config or "type =" not in rclone_config:
            flowfile.set_content(json.dumps({"error": "Generated rclone config is empty or invalid"}).encode())
            return [flowfile]

        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            service_scope = session.get("service_scope", "") or _normalize_service_scope(body.get("scope", ""))
            service_scope_id = session.get("service_scope_id", "") or _service_scope_id(
                service_scope, user_id, body.get("conversation_id", ""))
            reg.update_config(service_scope, service_scope_id, service_id, {
                "rclone_config": rclone_config,
            })
            try:
                from core.remote_fs_bindings import notify_linked_relays
                notify_linked_relays(session.get("conversation_id", ""), user_id)
            except Exception:
                logger.debug("Remote FS relay notification after rclone login failed", exc_info=True)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to save rclone config: {e}"}).encode())
            return [flowfile]

        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)  # nosec B603
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        unregister_session(session_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": "Rclone config saved in service.",
        }).encode())
        return [flowfile]

    return None


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

