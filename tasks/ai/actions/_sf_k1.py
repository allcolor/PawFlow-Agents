"""AgentLoopTask actions  - service flow"""

import json
import logging
import secrets

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _is_admin,
    _service_scope_id,
    _normalize_service_scope,
    _DISABLED_DIRECT_SERVICE_INSTALL_TYPES,
    _DISABLED_DIRECT_SERVICE_INSTALL_MESSAGES,
    _service_category,
    _service_type_sort_key,
    _service_requires_connected_state,
    _wait_for_service_connected,
    _validate_required_service_config,
    _service_started_for_listing,
    _service_install_state_for_listing,
    _credential_provider_for_service,
    _credential_module,
)

logger = logging.getLogger(__name__)


def _handle_sf_k1(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k1. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
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
        # only services of that type. The `llm` capability includes direct and
        # aggregate LLM services for agent pickers.
        # Consumers needing a subset (LLM dropdowns, relay pickers, etc.) call
        # this action with the appropriate filter — never embedded inside
        # unrelated actions.
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            filter_type = body.get("service_type", "") or ""
            def _matches_filter(sdef):
                if not filter_type:
                    return True
                if filter_type == "llm":
                    return sdef.service_type in {"llmConnection", "llmAggregator"}
                return sdef.service_type == filter_type
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            services = []
            from core import admin_scope
            if admin_scope.wants_view_all(body, flowfile):
                # Admin cross-user view: every populated scope, owner-labelled.
                # Definitions only -- live 'started' probing is per-scope and
                # too costly across the whole fleet, so it is skipped here.
                cidx = admin_scope.conv_index()
                conv_pairs = [(v.get("owner", ""), cid)
                              for cid, v in cidx.items() if v.get("owner")]
                for _scope, _sid_scope, _owner, _cid in reg.iter_all_scopes(
                        conv_pairs=conv_pairs):
                    if _scope == "global":
                        _ref_prefix = "global:"
                    elif _scope == "user":
                        _ref_prefix = f"user:{_sid_scope}:"
                    else:
                        _ref_prefix = f"conv:{_sid_scope}:"
                    for sid, sdef in sorted(
                            reg.get_all(_scope, _sid_scope).items()):
                        if not _matches_filter(sdef):
                            continue
                        services.append({
                            "service_id": sid,
                            "ref": f"{_ref_prefix}{sid}",
                            "service_type": sdef.service_type,
                            "enabled": getattr(sdef, "enabled", True),
                            "started": False,
                            "description": sdef.description,
                            "scope": _scope,
                            "provider": (sdef.config or {}).get("provider", ""),
                            "install_state": _service_install_state_for_listing(
                                _scope, _sid_scope, sid),
                            "owner_id": _owner,
                            "owner_display": (
                                admin_scope.display_name_for(_owner)
                                if _owner else ""),
                            "conv_id": _cid,
                            "conv_title": cidx.get(_cid, {}).get("title", ""),
                        })
                flowfile.set_content(json.dumps({
                    "services": services, "view": "all",
                }, ensure_ascii=False).encode())
                return [flowfile]
            for sid, sdef in sorted(reg.get_all("global", "").items()):
                if not _matches_filter(sdef):
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
                    "install_state": _service_install_state_for_listing("global", "", sid),
                })
            for sid, sdef in sorted(reg.get_all("user", user_id).items()):
                if not _matches_filter(sdef):
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
                    "install_state": _service_install_state_for_listing("user", user_id, sid),
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
                    if not _matches_filter(sdef):
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
                        "install_state": _service_install_state_for_listing("conv", conv_id, sid),
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
            if stype in _DISABLED_DIRECT_SERVICE_INSTALL_TYPES:
                continue
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
            from core.service_parameter_helpers import apply_service_parameter_helpers
            schema = apply_service_parameter_helpers(svc_type, schema)
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

    if action == "get_service_parameter_helper":
        svc_type = body.get("service_type", "")
        parameter = body.get("parameter", "")
        if not svc_type or not parameter:
            flowfile.set_content(json.dumps({"error": "service_type and parameter are required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            from core.service_parameter_helpers import get_service_parameter_helper
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            payload = get_service_parameter_helper(
                svc_type,
                parameter,
                body.get("config") or {},
                user_id=user_id,
                conversation_id=conv_id,
                store=store,
            )
            flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
        return [flowfile]

    if action in {"service_install_status", "service_install_log", "service_install_cancel"}:
        try:
            svc_name = body.get("service_name", "") or body.get("service_id", "")
            conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
            agent_name = (
                body.get("_agent_name", "")
                or body.get("call_agent_name", "")
                or flowfile.get_attribute("call_agent_name")
                or flowfile.get_attribute("agent_name")
                or ""
            )
            scope = _normalize_service_scope(
                body.get("scope", "") or ("conv" if conv_id and agent_name else "user"))
            if scope == "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            scope_id = _service_scope_id(scope, user_id, conv_id)
            if not svc_name:
                flowfile.set_content(json.dumps({"error": "service_name is required"}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            from core.service_install import (
                read_install_state, read_install_log, request_install_cancel,
            )
            if action == "service_install_status":
                payload = {"install_state": read_install_state(scope, scope_id, svc_name)}
            elif action == "service_install_log":
                limit = int(body.get("limit", 200) or 200)
                payload = read_install_log(scope, scope_id, svc_name, limit=limit)
                payload["install_state"] = read_install_state(scope, scope_id, svc_name)
                if str(body.get("download", "")).lower() in {"1", "true", "yes", "on"}:
                    if not conv_id:
                        payload["download_error"] = "conversation_id is required to export install logs"
                    else:
                        from core.file_store import FileStore
                        filename = f"service_install_{svc_name}.json"
                        fid = FileStore.instance().store(
                            filename,
                            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
                            "application/json",
                            conversation_id=conv_id,
                            user_id=user_id,
                            category="service_install_log",
                        )
                        payload["download_url"] = f"fs://filestore/{fid}/{filename}"
            else:
                payload = {"install_state": request_install_cancel(scope, scope_id, svc_name)}
            flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
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
            # Agent tool calls are sandboxed to the conversation, but UI/user
            # calls must keep the explicitly selected scope even when the UI
            # carries conversation_id for replies and resolution context.
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
            if svc_type in _DISABLED_DIRECT_SERVICE_INSTALL_TYPES:
                flowfile.set_content(json.dumps({
                    "error": _DISABLED_DIRECT_SERVICE_INSTALL_MESSAGES[svc_type],
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
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
            managed_server_relay = False
            if svc_type == "relay" and not str(config.get("token", "") or "").strip():
                config["token"] = secrets.token_urlsafe(32)
                config.setdefault("mode", "readwrite")
                config["server_managed"] = True
                config.setdefault("server_kind", "workspace")
                managed_server_relay = True
            description = body.get("description", "")
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            # Admin may create on behalf of another owner (target_user_id /
            # target_conversation_id). Default = caller, unchanged.
            from core import admin_scope
            try:
                _owner_user, _owner_conv = admin_scope.effective_owner(
                    body, user_id, conv_id, flowfile, scope)
            except PermissionError as _pe:
                flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            except ValueError as _ve:
                flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            if scope == "global":
                scope_id = ""
            elif scope == "conversation" or scope == "conv":
                scope_id = _owner_conv or ""
                scope = "conv"
            else:
                scope_id = _owner_user
                scope = "user"
            owner_user_id = _owner_user or user_id
            server_relay_manager = None
            if managed_server_relay:
                from core.server_relay_manager import ServerRelayManager
                server_relay_manager = ServerRelayManager.get_instance()
                config.update(server_relay_manager.service_relay_config(
                    svc_name,
                    scope=scope,
                    scope_id=scope_id,
                    user_id=owner_user_id,
                    kind=str(config.get("server_kind") or "workspace"),
                ))
            from core import ServiceFactory
            from core.service_install import (
                ServiceInstallReporter,
                read_install_state,
                service_install_session,
                update_install_state,
            )
            svc_cls = ServiceFactory.get(svc_type)
            _validate_required_service_config(svc_cls, config)
            prepare_result = None
            reporter = ServiceInstallReporter(
                conversation_id=conv_id,
                service_id=svc_name,
                service_type=svc_type,
                scope=scope,
                scope_id=scope_id,
            )
            try:
                with service_install_session(scope, scope_id, svc_name, svc_type):
                    reporter.step("queued", "Preparing service installation", progress=0.0)
                    if hasattr(svc_cls, "prepare_install"):
                        prepare_svc = svc_cls(config)
                        prepare_result = prepare_svc.prepare_install(reporter)
                    reporter.step("registering", "Registering service", progress=0.95)
                    reg.install(scope, scope_id, service_id=svc_name,
                                service_type=svc_type, config=config,
                                description=description)
                    if managed_server_relay:
                        live_svc = reg.get_live_instance(scope, scope_id, svc_name)
                        if not getattr(live_svc, "_managed_container_started", False):
                            reporter.step("starting", "Starting managed server relay", progress=0.98)
                            try:
                                server_relay_manager.spawn_service_relay(
                                    svc_name,
                                    config["token"],
                                    scope=scope,
                                    scope_id=scope_id,
                                    user_id=user_id,
                                    kind=str(config.get("server_kind") or "workspace"),
                                )
                                if live_svc is not None:
                                    setattr(live_svc, "_managed_container_started", True)
                            except Exception:
                                reg.uninstall(scope, scope_id, svc_name)
                                raise
                        reporter.step("connecting", "Waiting for managed server relay connection", progress=0.99)
                        if not _wait_for_service_connected(reg, scope, scope_id, svc_name):
                            reg.uninstall(scope, scope_id, svc_name)
                            raise RuntimeError(
                                f"Managed server relay '{svc_name}' container started but did not connect. "
                                f"Check Docker logs for {config.get('server_container_name', svc_name)}."
                            )
                    if _service_requires_connected_state(svc_type) and not reg.is_connected(scope, scope_id, svc_name):
                        reg.uninstall(scope, scope_id, svc_name)
                        raise RuntimeError(
                            f"Service '{svc_name}' did not start. Check server logs for the provider error.")
                    reporter.step("ready", "Service installation is ready", "ready", progress=1.0)
                    update_install_state(
                        scope, scope_id, svc_name,
                        status="ready",
                        service_type=svc_type,
                        phase="ready",
                        message="Service installation is ready",
                        progress=1.0,
                        result=prepare_result,
                    )
            except Exception as exc:
                if "already running" not in str(exc):
                    reporter.step("failed", str(exc), "failed")
                flowfile.set_content(json.dumps({
                    "error": str(exc),
                    "install_prepared": False,
                    "install_state": read_install_state(scope, scope_id, svc_name),
                }, ensure_ascii=False).encode())
                return [flowfile]
            flowfile.set_content(json.dumps({
                "installed": True, "id": svc_name, "type": svc_type,
                "install_prepared": prepare_result,
                "install_state": read_install_state(scope, scope_id, svc_name),
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action in ("llm_credential_pool_list", "claude_pool_list"):
        svc_id = body.get("service_id", "")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        provider = _credential_provider_for_service(svc_id, user_id) or "claude-code"
        mod = _credential_module(provider)
        pool = mod._load_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)
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
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        provider = _credential_provider_for_service(svc_id, user_id) or "claude-code"
        mod = _credential_module(provider)
        mod.reset_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Credentials pool cleared for {svc_id or provider}.",
        }).encode())
        return [flowfile]

    if action in ("llm_credential_pool_remove", "claude_pool_remove"):
        svc_id = body.get("service_id", "")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        idx = int(body.get("index", -1))
        provider = _credential_provider_for_service(svc_id, user_id) or "claude-code"
        mod = _credential_module(provider)
        if mod.remove_credential_from_pool(idx, svc_id, user_id=user_id, conv_id=conv_id):
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Credential {idx} removed from pool.",
            }).encode())
        else:
            flowfile.set_content(json.dumps({
                "error": f"Invalid index {idx}.",
            }).encode())
        return [flowfile]

    return _UNHANDLED
