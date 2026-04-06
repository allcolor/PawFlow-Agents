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

# Pending OAuth flows (in-memory, keyed by service_id)
_oauth_pending: Dict[str, Dict[str, str]] = {}


def _store_claude_tokens(service_id, access_token, refresh_token, expires_at):
    """Add Claude Code tokens to the credentials pool (encrypted).

    Each /cls login adds a new credential to the pool.
    """
    from core.llm_providers.claude_code_session import add_credential_to_pool
    add_credential_to_pool(
        access_token, refresh_token, expires_at,
        service_id=service_id)
    logger.info("Claude Code credential added to pool for '%s'", service_id)


def _handle_service_flow(self, action, body, store, user_id, flowfile):
    """Handle service flow actions. Returns [flowfile] or None."""


    if action == "service_list":
        try:
            from gui.services.user_service_registry import UserServiceRegistry
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            registry = UserServiceRegistry.get_instance()
            services = []
            for sid, sdef in sorted(greg.get_all_definitions().items()):
                _enabled = getattr(sdef, "enabled", True)
                try:
                    _started = greg.is_connected(sid) if _enabled else False
                except Exception:
                    _started = False
                services.append({
                    "id": sid,
                    "type": sdef.service_type,
                    "enabled": _enabled,
                    "started": _started,
                    "description": sdef.description,
                    "scope": "global",
                })
            defs = registry.get_all_for_user(user_id)
            for sid, sdef in sorted(defs.items()):
                try:
                    _started = registry.is_connected(user_id, sid) if sdef.enabled else False
                except Exception:
                    _started = False
                entry = {
                    "id": sid,
                    "type": sdef.service_type,
                    "enabled": sdef.enabled,
                    "started": _started,
                    "description": sdef.description,
                    "scope": "user",
                }
                svc = registry.get_live_instance(user_id, sid) if sdef.enabled else None
                if svc and hasattr(svc, '_relay_info') and svc._relay_info:
                    entry["relay_info"] = svc._relay_info
                elif sdef.config and sdef.config.get("docker_image"):
                    # Fallback: CLI passed docker_image in service config
                    entry["relay_info"] = {
                        "containerized": True,
                        "docker_image": sdef.config["docker_image"],
                    }
                services.append(entry)
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

    if action == "claude_pool_list":
        svc_id = body.get("service_id", "")
        from core.llm_providers.claude_code_session import _load_credentials_pool
        pool = _load_credentials_pool(svc_id)
        import time as _time
        entries = []
        for i, cred in enumerate(pool):
            exp = cred.get("expires_at", 0)
            exp_s = exp / 1000 if exp > 1e12 else exp
            remaining = exp_s - _time.time() if exp_s else 0
            entries.append({
                "index": i,
                "account": cred.get("account", ""),
                "expires_in": f"{remaining/3600:.1f}h" if remaining > 0 else "expired",
                "added_at": cred.get("added_at", 0),
            })
        flowfile.set_content(json.dumps({
            "pool": entries,
            "count": len(entries),
            "message": f"{len(entries)} credential(s) in pool for {svc_id or 'default CC service'}",
        }).encode())
        return [flowfile]

    if action == "claude_pool_reset":
        svc_id = body.get("service_id", "")
        from core.llm_providers.claude_code_session import reset_credentials_pool
        reset_credentials_pool(svc_id)
        flowfile.set_content(json.dumps({
            "ok": True,
            "message": f"Credentials pool cleared for {svc_id or 'default CC service'}.",
        }).encode())
        return [flowfile]

    if action == "claude_pool_remove":
        svc_id = body.get("service_id", "")
        idx = int(body.get("index", -1))
        from core.llm_providers.claude_code_session import remove_credential_from_pool
        if remove_credential_from_pool(idx, svc_id):
            flowfile.set_content(json.dumps({
                "ok": True,
                "message": f"Credential {idx} removed from pool.",
            }).encode())
        else:
            flowfile.set_content(json.dumps({
                "error": f"Invalid index {idx}.",
            }).encode())
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
            from gui.services.global_service_registry import GlobalServiceRegistry
            svc = GlobalServiceRegistry.get_instance().get_live_instance(svc_id)
        except Exception:
            pass
        if not svc:
            try:
                from gui.services.user_service_registry import UserServiceRegistry
                svc = UserServiceRegistry.get_instance().get_live_instance(user_id, svc_id)
            except Exception:
                pass
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
                        pass
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
                from gui.services.user_service_registry import UserServiceRegistry
                registry = UserServiceRegistry.get_instance()
                for sid, sdef in registry.get_all_for_user(user_id).items():
                    if not sdef.enabled or sdef.service_type != "relay":
                        continue
                    if any(r["relay_id"] == sid for r in relay_list):
                        continue
                    svc = registry.get_live_instance(user_id, sid)
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
                from gui.services.user_service_registry import UserServiceRegistry
                relay_svc = UserServiceRegistry.get_instance().get_live_instance(user_id, relay_id)
            except Exception:
                pass
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

        threading.Thread(target=_bg_relay_login, daemon=True, name=f"relay-login-{relay_id}").start()

        flowfile.set_content(json.dumps({
            "ok": True, "message": "Login started — authorize in the browser that opens on the relay."
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
        # Validate service exists and is a claude-code provider
        try:
            from gui.services.global_service_registry import GlobalServiceRegistry
            sdef = GlobalServiceRegistry.get_instance().get_definition(service_id)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' not found"}).encode())
                return [flowfile]
            if sdef.config.get("provider") != "claude-code":
                flowfile.set_content(json.dumps({"error": f"Service '{service_id}' is not a claude-code provider"}).encode())
                return [flowfile]
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Cannot verify service: {e}"}).encode())
            return [flowfile]

        try:
            import uuid as _uuid
            from core.server_relay_manager import _find_free_port

            session_id = _uuid.uuid4().hex[:12]
            free_port = _find_free_port()
            container_name = f"pawflow-claude-login-{session_id}"
            volume_name = f"pawflow_ws_{conversation_id}" if conversation_id else f"pawflow_login_{session_id}"
            image = "pawflow-claude-code:latest"

            logger.info("[vnc-login] Creating session %s (port %d)", session_id, free_port)

            # Pre-register session so status endpoint works immediately
            from services.vnc_proxy import register_session, vnc_ws_proxy, vnc_http_proxy
            register_session(session_id, free_port,
                             container=container_name, service_id=service_id,
                             user_id=user_id, volume=volume_name,
                             launch_time=time.time(), ready=False)
        except Exception as e:
            logger.error("[vnc-login] Setup failed: %s", e, exc_info=True)
            flowfile.set_content(json.dumps({"error": f"Login setup failed: {e}"}).encode())
            return [flowfile]

        def _bg_setup():
            import subprocess as _sp
            from core.server_relay_manager import _docker_cmd
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
                result = _sp.run(docker_cmd, capture_output=True, text=True, timeout=30)
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
                    urllib.request.urlopen(f"http://127.0.0.1:{free_port}/", timeout=2)
                    logger.info("[vnc-login] noVNC ready on port %d", free_port)
                    break
                except Exception:
                    time.sleep(1)

            # Register VNC proxy routes (once, shared by all sessions)
            try:
                svc = None
                try:
                    from gui.services.global_service_registry import GlobalServiceRegistry
                    greg = GlobalServiceRegistry.get_instance()
                    for _sid, _sdef in greg.get_all_definitions().items():
                        if getattr(_sdef, "service_type", "") == "httpListener":
                            svc = greg.get_live_instance(_sid)
                            if svc:
                                break
                except Exception:
                    pass
                if svc:
                    _vnc_owner = "_vnc_proxy"
                    existing = [r for r in svc.get_routes() if r.get("owner") == _vnc_owner]
                    if not existing:
                        svc.register_route("GET", "/vnc/{session_id}/websockify",
                                           _vnc_owner, callback=lambda req: None,
                                           ws_handler=vnc_ws_proxy)
                        svc.register_route("GET", "/vnc/{session_id}/{path+}",
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
                })

        _conv_id = conversation_id
        import threading
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
            import subprocess as _sp
            from core.server_relay_manager import _docker_cmd
            try:
                _sp.run(_docker_cmd() + ["rm", "-f", session.get("container", "")],
                        capture_output=True, timeout=10)
            except Exception:
                pass
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

        import subprocess as _sp
        from core.server_relay_manager import _docker_cmd
        container = session["container"]
        launch_time = session.get("launch_time", 0)

        # Check timeout (2 min max)
        if time.time() - launch_time > 120:
            _sp.run(_docker_cmd() + ["rm", "-f", container],
                    capture_output=True, timeout=10)
            unregister_session(session_id)
            flowfile.set_content(json.dumps({"error": "Login timed out (2 min)"}).encode())
            return [flowfile]

        # Check if .credentials.json was updated since launch
        try:
            stat_result = _sp.run(
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
            read_result = _sp.run(
                _docker_cmd() + ["exec", container, "bash", "-c",
                                  "cat /home/pawflow/.credentials.json 2>/dev/null || cat /workspace/.credentials.json"],
                capture_output=True, text=True, timeout=10)
            credentials = json.loads(read_result.stdout)
        except Exception as e:
            flowfile.set_content(json.dumps({"error": f"Failed to read credentials: {e}"}).encode())
            # Cleanup
            _sp.run(_docker_cmd() + ["rm", "-f", container],
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
            _sp.run(_docker_cmd() + ["rm", "-f", container], capture_output=True, timeout=10)
            unregister_session(session_id)
            return [flowfile]

        # Cleanup container (volume stays)
        try:
            _sp.run(_docker_cmd() + ["rm", "-f", container],
                    capture_output=True, timeout=10)
        except Exception:
            pass
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
                _store_claude_tokens(service_id, access_token, refresh_token, expires_at)
                _stored = True

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
                        _store_claude_tokens(service_id, access_token, refresh_token, expires_at)
                        _stored = True
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
                if getattr(sdef, "service_type", "") in ("relay", "filesystem"):
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
                from services.filesystem_service import WSListener
                await WSListener._ws_send_raw(_writer, _spawn_msg)

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
                            from services.filesystem_service import WSListener
                            await WSListener._ws_send_raw(_writer, _stop_msg)

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

    # ── Terminal / code-server on relay ──────────────────────────

    def _find_relay_svc(relay_id):
        """Find relay service in global then user registry."""
        from gui.services.global_service_registry import GlobalServiceRegistry
        greg = GlobalServiceRegistry.get_instance()
        svc = greg.get_live_instance(relay_id)
        if svc:
            return svc
        from gui.services.user_service_registry import UserServiceRegistry
        ureg = UserServiceRegistry.get_instance()
        return ureg.get_live_instance(user_id, relay_id) if user_id else None

    def _ensure_vnc_routes():
        """Ensure /vnc/ and /audio/ HTTP+WS routes exist on the HTTP listener.

        Same registration as claude_code_server_login — idempotent.
        """
        try:
            from services.vnc_proxy import vnc_ws_proxy, vnc_http_proxy
            from services.audio_proxy import audio_ws_proxy
            from gui.services.global_service_registry import GlobalServiceRegistry
            _greg = GlobalServiceRegistry.get_instance()
            for _sid2, _sdef in _greg.get_all_definitions().items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    _http_svc = _greg.get_live_instance(_sid2)
                    if _http_svc:
                        _vnc_owner = "_vnc_proxy"
                        existing = [r for r in _http_svc.get_routes() if r.get("owner") == _vnc_owner]
                        if not existing:
                            _http_svc.register_route("GET", "/vnc/{session_id}/websockify",
                                                     _vnc_owner, callback=lambda req: None,
                                                     ws_handler=vnc_ws_proxy)
                            _http_svc.register_route("GET", "/vnc/{session_id}/{path+}",
                                                     _vnc_owner, callback=vnc_http_proxy)
                        # Audio WebSocket route (same owner, idempotent)
                        _audio_exists = [r for r in _http_svc.get_routes()
                                         if r.get("pattern", "").startswith("/audio/")]
                        if not _audio_exists:
                            _http_svc.register_route("GET", "/audio/{session_id}/stream",
                                                     _vnc_owner, callback=lambda req: None,
                                                     ws_handler=audio_ws_proxy)
                        return
        except Exception as e:
            logger.warning("[vnc] Route registration failed: %s", e)

    def _get_desktop_host_port(relay_id):
        """Get the published host port for desktop noVNC.

        Same pattern as Claude login: find the container, docker port 6080.
        """
        import subprocess
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
                        r = subprocess.run(
                            _dkr_cmd() + ["port", cname, "6080"],
                            capture_output=True, text=True, timeout=5)
                        if r.returncode == 0:
                            return int(r.stdout.strip().split(":")[-1])
        except Exception:
            pass

        # 2) Any relay: check relay_info for host-network ports, then docker port
        svc = _find_relay_svc(relay_id)
        if svc:
            _ri = getattr(svc, '_relay_info', {}) or {}
            # --network host: ports are in relay_info directly
            _ri_port = _ri.get('desktop_novnc_port', 0)
            if _ri_port:
                return _ri_port
            container_id = _ri.get('container_id', '')
            if container_id:
                try:
                    r = subprocess.run(
                        _dkr_cmd() + ["port", container_id, "6080"],
                        capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return int(r.stdout.strip().split(":")[-1])
                except Exception:
                    pass
        return 0

    def _get_container_port(relay_id, container_port):
        """Get the published host port for a given container port."""
        import subprocess
        from core.docker_utils import docker_cmd as _dkr_cmd
        try:
            from core.server_relay_manager import ServerRelayManager
            for entry in ServerRelayManager.get_instance().list_all():
                if entry.get("relay_id") == relay_id:
                    cname = entry.get("container_name", "")
                    if cname:
                        r = subprocess.run(
                            _dkr_cmd() + ["port", cname, str(container_port)],
                            capture_output=True, text=True, timeout=5)
                        if r.returncode == 0:
                            return int(r.stdout.strip().split(":")[-1])
        except Exception:
            pass
        svc = _find_relay_svc(relay_id)
        if svc:
            cid = getattr(svc, '_relay_info', {}).get('container_id', '')
            if cid:
                try:
                    r = subprocess.run(
                        _dkr_cmd() + ["port", cid, str(container_port)],
                        capture_output=True, text=True, timeout=5)
                    if r.returncode == 0:
                        return int(r.stdout.strip().split(":")[-1])
                except Exception:
                    pass
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
            result = svc._request(_term_action, cols=cols, rows=rows,
                                  **(dict(shell=shell) if shell else {}))
            session_id = result.get("session_id", "") if isinstance(result, dict) else str(result)

            # Register terminal session for WS proxy
            # Both Docker and local terminals use the same relay WS path
            # (local terminal data arrives via host helper → relay → progress → dispatch)
            from services.terminal_proxy import register_terminal, terminal_ws_handler
            register_terminal(session_id, relay_id, relay_service=svc)

            # Register WS route (once)
            _owner = "_terminal_proxy"
            http_svc = None
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for _sid, _sdef in greg.get_all_definitions().items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    http_svc = greg.get_live_instance(_sid)
                    if http_svc:
                        break
            if http_svc:
                existing = [r for r in http_svc.get_routes() if r.get("owner") == _owner]
                if not existing:
                    http_svc.register_route(
                        "GET", "/terminal/{session_id}",
                        _owner,
                        callback=lambda req: None,
                        ws_handler=terminal_ws_handler,
                    )

            flowfile.set_content(json.dumps({
                "ok": True,
                "session_id": session_id,
                "relay_id": relay_id,
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
                pass
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
            register_code_server(relay_id, port, svc)

            _owner = "_code_server_proxy"
            http_svc = None
            from gui.services.global_service_registry import GlobalServiceRegistry
            greg = GlobalServiceRegistry.get_instance()
            for _sid, _sdef in greg.get_all_definitions().items():
                if getattr(_sdef, "service_type", "") == "httpListener":
                    http_svc = greg.get_live_instance(_sid)
                    if http_svc:
                        break
            logger.debug("[open_code_server] http_svc=%s", http_svc)
            if http_svc:
                existing = [r for r in http_svc.get_routes() if r.get("owner") == _owner]
                logger.debug("[open_code_server] existing code routes: %s", existing)
                if not existing:
                    http_svc.register_route(
                        "GET", "/code/{path+}",
                        _owner,
                        callback=code_http_proxy,
                        ws_handler=code_ws_proxy,
                    )
                    http_svc.register_route(
                        "POST", "/code/{path+}",
                        _owner,
                        callback=code_http_proxy,
                    )
                    http_svc.register_route(
                        "PUT", "/code/{path+}",
                        _owner,
                        callback=code_http_proxy,
                    )
                    http_svc.register_route(
                        "DELETE", "/code/{path+}",
                        _owner,
                        callback=code_http_proxy,
                    )
                    http_svc.register_route(
                        "PATCH", "/code/{path+}",
                        _owner,
                        callback=code_http_proxy,
                    )
                    http_svc.register_route(
                        "OPTIONS", "/code/{path+}",
                        _owner,
                        callback=code_http_proxy,
                    )

            conv_id = body.get("conversation_id", "")
            _rl = relay_id
            _pt = port

            def _bg_wait_code():
                time.sleep(2)
                logger.info("[code-server] Ready on relay %s port %s", _rl, _pt)
                if conv_id:
                    _publish_command_result(conv_id, {
                        "ok": True, "port": _pt, "relay_id": _rl,
                        "url": f"/code/{_rl}/",
                        "message": f"Code server ready at /code/{_rl}/",
                    })

            threading.Thread(target=_bg_wait_code, daemon=True).start()
            flowfile.set_content(json.dumps({
                "ok": True, "message": "Starting code server...",
                "port": port, "relay_id": relay_id, "url": f"/code/{relay_id}/",
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
                pass
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
                if local_screen:
                    _novnc_port = status.get("local_screen_novnc_port")
                    if _novnc_port:
                        _sid = f"{_session_prefix}_{relay_id}"
                        from services.vnc_proxy import register_session
                        register_session(_sid, _novnc_port)
                        _ensure_vnc_routes()
                        # Re-register audio for already-running desktop
                        try:
                            from services.audio_proxy import register_audio_source
                            _audio_port = status.get("local_screen_audio_port")
                            if _audio_port:
                                _relay_addr = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                                register_audio_source(_sid, _relay_addr, _audio_port)
                        except Exception:
                            pass
                        flowfile.set_content(json.dumps({
                            "ok": True, "already_running": True, "local_screen": True,
                            "relay_id": relay_id,
                            "url": f"/vnc/{_sid}/vnc.html?autoconnect=true&resize=scale&path=vnc/{_sid}/websockify",
                            "audio_session": _sid,
                        }).encode())
                        return [flowfile]
                else:
                    _hp = _get_desktop_host_port(relay_id)
                    logger.info("[open_desktop] already running, host_port=%s for %s", _hp, relay_id)
                    if _hp:
                        _sid = f"{_session_prefix}_{relay_id}"
                        from services.vnc_proxy import register_session
                        register_session(_sid, _hp)
                        _ensure_vnc_routes()
                        # Re-register audio for already-running desktop
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
                                pass
                            if not _ahp:
                                _ahp = _get_container_port(relay_id, 6180)
                            if _ahp:
                                register_audio_source(_sid, "127.0.0.1", _ahp)
                        except Exception:
                            pass
                        flowfile.set_content(json.dumps({
                            "ok": True, "already_running": True,
                            "relay_id": relay_id,
                            "url": f"/vnc/{_sid}/vnc.html?autoconnect=true&resize=scale&path=vnc/{_sid}/websockify",
                            "audio_session": _sid,
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

            if local_screen:
                # Local screen: the relay runs VNC+websockify on its own machine.
                # The novnc_port is directly on the relay's host (not in Docker).
                # Use the relay's address to proxy.
                _relay_addr = getattr(svc, '_relay_addr', None) or '127.0.0.1'
                host_port = novnc_port
                # For local relays connecting from the same machine, use the port directly
                session_id = f"{_session_prefix}_{relay_id}"
                from services.vnc_proxy import register_session
                register_session(session_id, host_port, host=_relay_addr)
            else:
                # Docker: get the published host port
                host_port = _get_desktop_host_port(relay_id)
                if not host_port:
                    flowfile.set_content(json.dumps({"error": "Desktop started but host port not found"}).encode())
                    return [flowfile]
                session_id = f"{_session_prefix}_{relay_id}"
                from services.vnc_proxy import register_session
                register_session(session_id, host_port)

            _ensure_vnc_routes()

            # Register audio source if available
            try:
                from services.audio_proxy import register_audio_source
                if local_screen:
                    # Local relay: audio_capture runs on relay host
                    _audio_port = result.get("audio_port") if isinstance(result, dict) else None
                    if _audio_port:
                        register_audio_source(session_id, _relay_addr, _audio_port)
                else:
                    # Docker: use port from start_desktop result first
                    _audio_host_port = result.get("audio_port", 0) if isinstance(result, dict) else 0
                    if not _audio_host_port:
                        _svc = _find_relay_svc(relay_id)
                        if _svc:
                            _ri = getattr(_svc, '_relay_info', {}) or {}
                            _audio_host_port = _ri.get('desktop_audio_port', 0)
                    if not _audio_host_port:
                        try:
                            from core.server_relay_manager import ServerRelayManager
                            for _entry in ServerRelayManager.get_instance().list_all():
                                if _entry.get("relay_id") == relay_id:
                                    _audio_host_port = _entry.get("audio_host_port", 0)
                                    break
                        except Exception:
                            pass
                    if not _audio_host_port:
                        _audio_host_port = _get_container_port(relay_id, 6180)
                    if _audio_host_port:
                        register_audio_source(session_id, "127.0.0.1", _audio_host_port)
            except Exception as _ae:
                logger.debug("[open_desktop] Audio registration skipped: %s", _ae)

            flowfile.set_content(json.dumps({
                "ok": True, "relay_id": relay_id, "local_screen": local_screen,
                "url": f"/vnc/{session_id}/vnc.html?autoconnect=true&resize=scale&path=vnc/{session_id}/websockify",
                "audio_session": session_id,
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
                pass
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
            first = add_forward(relay_id, int_port, svc, ext_port=ext_port)

            # Register generic routes once (shared by all forwards)
            if first:
                http_svc = _find_http_listener()
                if http_svc:
                    for method in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
                        http_svc.register_route(method, "/fwd/{relay_id}/{ext_port}/{path+}",
                                                _ROUTE_OWNER, callback=fwd_http_proxy)
                    http_svc.register_route("GET", "/fwd/{relay_id}/{ext_port}",
                                            _ROUTE_OWNER, callback=fwd_root_redirect)

            _url = f"/fwd/{relay_id}/{ext_port}/"
            flowfile.set_content(json.dumps({
                "ok": True, "relay_id": relay_id,
                "int_port": int_port, "ext_port": ext_port, "url": _url,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "port_forward_remove":
        relay_id = body.get("relay_id", "")
        ext_port = body.get("ext_port", 0) or body.get("port", 0)
        if not relay_id or not ext_port:
            flowfile.set_content(json.dumps({"error": "Missing relay_id or port"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        try:
            ext_port = int(ext_port)
            from services.port_forward_proxy import remove_forward, _ROUTE_OWNER
            last = remove_forward(relay_id, ext_port)
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

    if action == "private_gateway_list_bans":
        from services.private_gateway import list_bans
        flowfile.set_content(json.dumps({"bans": list_bans()}).encode())
        return [flowfile]

    if action == "private_gateway_unban":
        ip = body.get("ip", "")
        if not ip:
            flowfile.set_content(json.dumps({"error": "Missing ip"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        from services.private_gateway import unban_ip
        was_banned = unban_ip(ip)
        flowfile.set_content(json.dumps({"ok": True, "was_banned": was_banned}).encode())
        return [flowfile]

    if action == "private_gateway_status":
        from services.private_gateway import is_enabled, list_bans
        flowfile.set_content(json.dumps({
            "enabled": is_enabled(),
            "banned_count": len(list_bans()),
        }).encode())
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

    return None


def _find_http_listener():
    """Find the live HTTPListenerService instance."""
    from gui.services.global_service_registry import GlobalServiceRegistry
    greg = GlobalServiceRegistry.get_instance()
    for _sid, _sdef in greg.get_all_definitions().items():
        if getattr(_sdef, "service_type", "") == "httpListener":
            svc = greg.get_live_instance(_sid)
            if svc:
                return svc
    return None

