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

# Pending OAuth flows (in-memory, keyed by service_id)
_oauth_pending: Dict[str, Dict[str, str]] = {}


def _handle_service_flow(self, action, body, store, user_id, flowfile):
    """Handle service flow actions. Returns [flowfile] or None."""


    if action == "service_list":
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            registry = UserServiceRegistry.get_instance()
            defs = registry.get_all_for_user(user_id)
            services = []
            for sid, sdef in sorted(defs.items()):
                services.append({
                    "id": sid,
                    "type": sdef.service_type,
                    "enabled": sdef.enabled,
                    "connected": registry.is_connected(user_id, sid),
                    "description": sdef.description,
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
        for stype in sorted(ServiceFactory.list_types()):
            try:
                cls = ServiceFactory.get(stype)
                types.append({
                    "type": stype,
                    "name": getattr(cls, "NAME", stype),
                    "description": getattr(cls, "DESCRIPTION", ""),
                })
            except Exception:
                types.append({"type": stype, "name": stype, "description": ""})
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
            scope = body.get("scope", "user")
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
            description = body.get("description", "")
            if scope == "global":
                from gui.services.global_service_registry import GlobalServiceRegistry
                gsvc = GlobalServiceRegistry.get_instance()
                gsvc.install(service_id=svc_name, service_type=svc_type,
                             config=config, description=description)
            else:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                registry.install(user_id=user_id, service_id=svc_name,
                                 service_type=svc_type, config=config,
                                 description=description)
            flowfile.set_content(json.dumps({
                "installed": True, "id": svc_name, "type": svc_type,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "service_uninstall":
        try:
            svc_id = body.get("service_id", "")
            scope = body.get("scope", "")
            if not svc_id:
                flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
                return [flowfile]
            # Try global first if scope says so, or auto-detect
            from gui.services.global_service_registry import GlobalServiceRegistry
            gsvc = GlobalServiceRegistry.get_instance()
            if scope == "global" or (not scope and gsvc.get_definition(svc_id)):
                if "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
                    flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                gsvc.uninstall(svc_id)
            else:
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                if not registry.get_definition(user_id, svc_id):
                    flowfile.set_content(json.dumps({"error": f"Service '{svc_id}' not found."}).encode())
                    return [flowfile]
                registry.uninstall(user_id, svc_id)
            flowfile.set_content(json.dumps({
                "uninstalled": True, "id": svc_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "service_enable":
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            registry = UserServiceRegistry.get_instance()
            svc_id = body.get("service_id", "")
            if not registry.get_definition(user_id, svc_id):
                flowfile.set_content(json.dumps({
                    "error": f"Service '{svc_id}' not found.",
                }).encode())
                return [flowfile]
            registry.enable(user_id, svc_id)
            flowfile.set_content(json.dumps({
                "enabled": True, "id": svc_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "service_disable":
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            registry = UserServiceRegistry.get_instance()
            svc_id = body.get("service_id", "")
            if not registry.get_definition(user_id, svc_id):
                flowfile.set_content(json.dumps({
                    "error": f"Service '{svc_id}' not found.",
                }).encode())
                return [flowfile]
            registry.disable(user_id, svc_id)
            flowfile.set_content(json.dumps({
                "disabled": True, "id": svc_id,
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "get_service_detail":
        sid = body.get("service_id", "")
        scope = body.get("scope", "global")
        if not sid:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        try:
            if scope == "user" and user_id:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                sdef = ureg.get_all_for_user(user_id).get(sid)
            else:
                from gui.services.global_service_registry import GlobalServiceRegistry
                sdef = GlobalServiceRegistry.get_instance().get_all_definitions().get(sid)
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
        scope = body.get("scope", "global")
        config = body.get("config", {})
        if not sid:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        # Admin check for global services
        if scope == "global":
            _role = flowfile.get_attribute("http.auth.roles") or ""
            if _role != "admin":
                flowfile.set_content(json.dumps({"error": "Only admin can modify global services"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
        try:
            if scope == "user" and user_id:
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                ureg.update_config(user_id, sid, config)
            else:
                from gui.services.global_service_registry import GlobalServiceRegistry
                GlobalServiceRegistry.get_instance().update_config(sid, config)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "toggle_service":
        sid = body.get("service_id", "")
        enabled = body.get("enabled", True)
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            gsvc = GlobalServiceRegistry.get_instance()
            if gsvc.get_definition(sid):
                # Global service
                if "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
                    flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                if enabled:
                    gsvc.enable(sid)
                else:
                    gsvc.disable(sid)
            else:
                # User service
                from gui.services.user_service_registry import UserServiceRegistry
                ureg = UserServiceRegistry.get_instance()
                uid = user_id or "anonymous"
                ureg.set_enabled(uid, sid, enabled)
            flowfile.set_content(json.dumps({"ok": True, "enabled": enabled}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_service":
        sid = body.get("service_id", "")
        scope = body.get("scope", "user")
        if scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            return [flowfile]
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            uid = user_id or "anonymous"
            UserServiceRegistry.get_instance().uninstall(uid, sid)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    # ── Claude Code OAuth login ──────────────────────────────────────

    if action == "claude_code_login_url":
        """Return instructions for Claude Code login.

        The user runs `claude auth login` on their own machine, then
        pastes the credentials JSON content.
        """
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
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            sdef = greg.get_definition(service_id)
            if sdef:
                # Global service — check admin permission
                _roles = flowfile.get_attribute("http.auth.roles") or ""
                if action == "claude_code_auth" and "admin" not in _roles:
                    flowfile.set_content(json.dumps({
                        "error": f"Admin permission required for global service '{service_id}'"
                    }).encode())
                    flowfile.set_attribute("http.response.status", "403")
                    return [flowfile]
                _cfg = getattr(sdef, "config", {}) or {}
                if _cfg.get("provider") != "claude-code":
                    flowfile.set_content(json.dumps({
                        "error": f"Service '{service_id}' is not a claude-code provider"
                    }).encode())
                    return [flowfile]
                sdef.config["claude_access_token"] = access_token
                sdef.config["claude_refresh_token"] = refresh_token
                sdef.config["claude_expires_at"] = expires_at
                greg._save_to_disk()
                _stored = True
                logger.info("Claude Code credentials stored for global service '%s'", service_id)

            if not _stored:
                # Try user services
                try:
                    from gui.services.user_service_registry import UserServiceRegistry
                    ureg = UserServiceRegistry.get_instance()
                    usdef = ureg.get_definition(user_id, service_id)
                    if usdef:
                        _ucfg = getattr(usdef, "config", {}) or {}
                        if _ucfg.get("provider") != "claude-code":
                            flowfile.set_content(json.dumps({
                                "error": f"Service '{service_id}' is not a claude-code provider"
                            }).encode())
                            return [flowfile]
                        usdef.config["claude_access_token"] = access_token
                        usdef.config["claude_refresh_token"] = refresh_token
                        usdef.config["claude_expires_at"] = expires_at
                        ureg._save_to_disk(user_id)
                        _stored = True
                        logger.info("Claude Code credentials stored for user service '%s'", service_id)
                except Exception:
                    pass

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
        from gui.services.user_service_registry import UserServiceRegistry
        from gui.services.global_service_registry import GlobalServiceRegistry
        ureg = UserServiceRegistry.get_instance()
        greg = GlobalServiceRegistry.get_instance()

        source_svc = None
        if relay_source:
            # Explicit source
            source_svc = ureg.get_live_instance(user_id, relay_source)
            if not source_svc:
                source_svc = greg.get_live_instance(relay_source)
        else:
            # Find user's first connected filesystem service
            for sid, sdef in ureg.get_all_for_user(user_id).items():
                if getattr(sdef, "service_type", "") in ("filesystem", "browserFilesystem", "serverFilesystem"):
                    svc = ureg.get_live_instance(user_id, sid)
                    if svc and hasattr(svc, '_relay_pool') and svc._relay_pool:
                        source_svc = svc
                        relay_source = sid
                        break

        if not source_svc:
            flowfile.set_content(json.dumps({
                "error": f"No connected relay found{' for ' + relay_source if relay_source else ''}. "
                         "Connect a relay first (pawcode, vscode plugin, or python relay)."
            }).encode())
            return [flowfile]

        # Generate IDs for the child relay
        import hashlib
        _dir_hash = hashlib.md5(path.encode()).hexdigest()[:8]
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
                from services.filesystem_service import FilesystemWSListener
                await FilesystemWSListener._ws_send_raw(_writer, _spawn_msg)

            asyncio.run_coroutine_threadsafe(_send_spawn(), _loop).result(timeout=5)

            # Wait for the child relay to connect (it registers as a new service)
            time.sleep(3)

            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Relay spawned: {child_relay_id} → {path}",
                "service_id": child_relay_id,
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
        from gui.services.user_service_registry import UserServiceRegistry
        ureg = UserServiceRegistry.get_instance()
        svc = ureg.get_live_instance(user_id, service_id)
        if svc:
            try:
                svc.disconnect()
            except Exception:
                pass
            try:
                ureg.uninstall_service(user_id, service_id)
            except Exception:
                pass

        # Also try to send stop_relay to all connected relays
        try:
            for sid, sdef in ureg.get_all_for_user(user_id).items():
                _svc = ureg.get_live_instance(user_id, sid)
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
                            from services.filesystem_service import FilesystemWSListener
                            await FilesystemWSListener._ws_send_raw(_writer, _stop_msg)

                        asyncio.run_coroutine_threadsafe(_send_stop(), _loop).result(timeout=5)
                    except Exception:
                        pass
        except Exception:
            pass

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
            from gui.services.executor_registry import ExecutorRegistry
            from gui.services.deployment_registry import DeploymentRegistry
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
                                       parameters=inst.parameters)
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
        try:
            from pathlib import Path as _Path
            flows_dir = _Path("flows")
            templates = []
            if flows_dir.is_dir():
                for fp in sorted(flows_dir.glob("*.json")):
                    try:
                        raw = json.loads(fp.read_text(encoding="utf-8"))
                        templates.append({
                            "id": raw.get("id", fp.stem),
                            "name": raw.get("name", fp.stem),
                            "version": raw.get("version", ""),
                            "description": raw.get("description", ""),
                            "scope": raw.get("scope", "independent"),
                            "tasks_count": len(raw.get("tasks", {})),
                            "services_count": len(raw.get("services", {})),
                            "file_path": str(fp),
                        })
                    except Exception:
                        pass
            flowfile.set_content(json.dumps({"templates": templates}, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "deploy_flow":
        template_id = body.get("template_id", "")
        deploy_scope = body.get("scope", "user")
        params = body.get("parameters", {})
        conv_id = body.get("conversation_id", "")
        if deploy_scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps(
                {"error": "Requires admin role for global scope"}).encode())
            return [flowfile]
        if not template_id:
            flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
            return [flowfile]
        try:
            from pathlib import Path as _Path
            from gui.services.deployment_registry import DeploymentRegistry
            flows_dir = _Path("flows")
            tpath = None
            for fp in flows_dir.glob("*.json"):
                try:
                    raw = json.loads(fp.read_text(encoding="utf-8"))
                    if raw.get("id", fp.stem) == template_id:
                        tpath = fp
                        break
                except Exception:
                    pass
            if not tpath:
                candidate = flows_dir / f"{template_id}.json"
                if candidate.exists():
                    tpath = candidate
            if not tpath:
                flowfile.set_content(json.dumps(
                    {"error": f"Template '{template_id}' not found in flows/"}).encode())
                return [flowfile]

            # Read flow scope from template (runtime dependency declaration)
            flow_config = json.loads(tpath.read_text(encoding="utf-8"))
            flow_scope = flow_config.get("scope", "independent")

            # Validate runtime dependencies
            uid = user_id or "anonymous"
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
            iid = dr.deploy(
                template_path=str(tpath),
                owner=uid,
                parameters=params,
                source="agent",
                conversation_id=conv_id if deploy_scope == "conversation" else None,
            )
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
            from gui.services.deployment_registry import DeploymentRegistry
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
            from gui.services.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            # Load template parameters schema for reference
            template_params = {}
            try:
                from pathlib import Path as _Path
                raw = json.loads(_Path(inst.flow_path).read_text(encoding="utf-8"))
                template_params = raw.get("parameters", {})
            except Exception:
                pass
            flowfile.set_content(json.dumps({
                "instance_id": inst.instance_id,
                "flow_name": inst.flow_name,
                "flow_id": inst.flow_id,
                "status": inst.status,
                "parameters": inst.parameters,
                "template_parameters": template_params,
                "owner": inst.owner,
                "scope": "conversation" if inst.conversation_id else "user" if inst.owner else "global",
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "update_flow_params":
        iid = body.get("instance_id", "")
        params = body.get("parameters", {})
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        try:
            from gui.services.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            if user_id and inst.owner and inst.owner != user_id:
                flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                return [flowfile]
            inst.parameters.update(params)
            dr._save_instance(inst)
            flowfile.set_content(json.dumps({"ok": True}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    return None
