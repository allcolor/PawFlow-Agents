"""AgentLoopTask actions  - service flow"""

import json
import logging

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _is_admin,
    _service_scope_id,
    _normalize_service_scope,
    _credential_provider_for_service,
    _credential_module,
    _store_claude_tokens,
    _refresh_running_flow_service_bindings,
)
from tasks.ai.actions._sf_routes import (
    _publish_command_result,
    _notify_remote_mounts_after_service_change,
)

logger = logging.getLogger(__name__)


def _handle_sf_k2(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k2. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
    if action == "llm_credential_pool_refresh":
        svc_id = body.get("service_id", "")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        idx = int(body.get("index", -1))
        provider = _credential_provider_for_service(svc_id, user_id)
        if not svc_id or idx < 0 or not provider:
            flowfile.set_content(json.dumps({"error": "Missing service_id/provider or invalid index"}).encode())
            return [flowfile]
        mod = _credential_module(provider)
        pool = mod._load_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)
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
                    pool_index=idx,
                    user_id=user_id,
                    conv_id=conv_id)
            elif provider == "codex-app-server":
                tokens = mod.refresh_oauth_token(refresh_token)
                mod._persist_tokens_to_service(
                    tokens.get("access_token", ""),
                    tokens.get("refresh_token", refresh_token),
                    tokens.get("expires_at", 0),
                    service_id=svc_id,
                    pool_index=idx,
                    account=pool[idx].get("account", ""),
                    id_token=pool[idx].get("id_token", ""),
                    user_id=user_id,
                    conv_id=conv_id)
            else:
                tokens = mod.refresh_oauth_token(refresh_token)
                mod._persist_tokens_to_service(
                    tokens.get("access_token", ""),
                    tokens.get("refresh_token", refresh_token),
                    tokens.get("expires_at", 0),
                    service_id=svc_id,
                    pool_index=idx,
                    account=pool[idx].get("account", ""),
                    user_id=user_id,
                    conv_id=conv_id)
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
            svc = ServiceRegistry.get_instance().resolve(
                svc_id, user_id=user_id, conv_id=conv_id)
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
            pool = _load_credentials_pool(svc_id, user_id=user_id, conv_id=conv_id)
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
            svc_def = registry.get_definition(scope, scope_id, svc_id)
            if not svc_def:
                flowfile.set_content(json.dumps({"error": f"Service '{svc_id}' not found."}).encode())
                return [flowfile]
            if svc_def.service_type == "relay" and (svc_def.config or {}).get("server_managed"):
                from core.server_relay_manager import ServerRelayManager
                ServerRelayManager.get_instance().cleanup_service_relay(svc_def.config or {})
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

    if action in {"voicebox_profiles_list", "voicebox_preset_voices_list", "voicebox_profile_save", "voicebox_tasks_clear"}:
        sid = body.get("service_id", "")
        scope = _normalize_service_scope(body.get("scope", "global"))
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        config = body.get("config", {}) if isinstance(body.get("config", {}), dict) else {}
        if not sid:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            return [flowfile]
        if scope == "global" and not _is_admin(flowfile):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            svc = reg.resolve(sid, user_id=user_id, conv_id=conv_id)
            if not svc or getattr(svc, "TYPE", "") != "voicebox":
                flowfile.set_content(json.dumps({"error": f"Voicebox service '{sid}' not found"}).encode())
                return [flowfile]
            if action == "voicebox_profiles_list":
                profiles = svc.list_profiles()
                lines = [
                    f"- {p.get('name')} ({p.get('id')}) — {p.get('voice_type')}"
                    for p in profiles if isinstance(p, dict)
                ]
                flowfile.set_content(json.dumps({
                    "profiles": profiles,
                    "message": "Voicebox profiles:\n" + ("\n".join(lines) if lines else "(none)"),
                }, ensure_ascii=False).encode())
                return [flowfile]
            if action == "voicebox_preset_voices_list":
                engine = config.get("profile_engine") or body.get("engine") or "kokoro"
                voices = svc.list_preset_voices(engine)
                lines = [
                    f"- {v.get('name')} = {v.get('voice_id')} ({v.get('language')}, {v.get('gender')})"
                    for v in voices if isinstance(v, dict)
                ]
                flowfile.set_content(json.dumps({
                    "voices": voices,
                    "message": f"Voicebox preset voices for {engine}:\n" + ("\n".join(lines) if lines else "(none)"),
                }, ensure_ascii=False).encode())
                return [flowfile]
            if action == "voicebox_tasks_clear":
                result = svc.clear_tasks()
                flowfile.set_content(json.dumps({
                    "ok": True,
                    "result": result,
                    "message": result.get("message", "Voicebox task state cleared") if isinstance(result, dict) else "Voicebox task state cleared",
                }, ensure_ascii=False).encode())
                return [flowfile]
            result = svc.save_preset_profile(
                name=config.get("profile_name") or config.get("default_profile") or body.get("profile_name") or "",
                engine=config.get("profile_engine") or body.get("engine") or "kokoro",
                voice_id=config.get("profile_voice_id") or body.get("voice_id") or "",
                language=config.get("profile_language") or body.get("language") or "",
                description=config.get("profile_description") or body.get("description") or "",
                personality=config.get("profile_personality") or body.get("personality") or "",
            )
            flowfile.set_content(json.dumps({
                "ok": True,
                "profile": result,
                "message": f"Voicebox profile saved: {result.get('name', '')} ({result.get('id', '')})",
            }, ensure_ascii=False).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}, ensure_ascii=False).encode())
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
            refreshed = _refresh_running_flow_service_bindings(scope, scope_id, sid)
            if sdef:
                _notify_remote_mounts_after_service_change(sdef, conv_id, user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "refreshed_flows": refreshed,
            }).encode())
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

    if action == "move_service_scope":
        sid = body.get("service_id", "")
        from_scope = _normalize_service_scope(body.get("from_scope", "user"))
        to_scope = _normalize_service_scope(body.get("to_scope", "user"))
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if not sid:
            flowfile.set_content(json.dumps({"error": "Missing service_id"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if from_scope == to_scope:
            flowfile.set_content(json.dumps({"ok": True, "scope": to_scope}).encode())
            return [flowfile]
        if (from_scope == "conv" or to_scope == "conv") and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if (from_scope == "global" or to_scope == "global") and not _is_admin(flowfile):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        try:
            from core.service_registry import ServiceRegistry
            registry = ServiceRegistry.get_instance()
            # The user/conv side belongs to an owner; an admin may target
            # another user (e.g. demote a global service down to user X).
            from core import admin_scope
            _owner_scope = "conv" if "conv" in (from_scope, to_scope) else "user"
            try:
                _owner_user, _owner_conv = admin_scope.effective_owner(
                    body, user_id, conv_id, flowfile, _owner_scope)
            except PermissionError as _pe:
                flowfile.set_content(json.dumps({"error": str(_pe)}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            except ValueError as _ve:
                flowfile.set_content(json.dumps({"error": str(_ve)}).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            _owner_user = _owner_user or user_id
            from_scope_id = _service_scope_id(from_scope, _owner_user, _owner_conv)
            to_scope_id = _service_scope_id(to_scope, _owner_user, _owner_conv)
            sdef = registry.get_definition(from_scope, from_scope_id, sid)
            if not sdef:
                flowfile.set_content(json.dumps({"error": f"Service '{sid}' not found."}).encode())
                flowfile.set_attribute("http.response.status", "404")
                return [flowfile]
            config = dict(getattr(sdef, "config", {}) or {})
            description = getattr(sdef, "description", "") or ""
            enabled = bool(getattr(sdef, "enabled", True))
            service_type = getattr(sdef, "service_type", "")
            if service_type == "relay" and config.get("server_managed"):
                flowfile.set_content(json.dumps({
                    "error": (
                        "Managed server relays cannot be moved between scopes. "
                        "Create a new server relay in the target scope and "
                        "uninstall the old one when you no longer need its workspace."
                    )
                }).encode())
                flowfile.set_attribute("http.response.status", "400")
                return [flowfile]
            registry.uninstall(from_scope, from_scope_id, sid)
            try:
                registry.install(
                    to_scope, to_scope_id, service_id=sid,
                    service_type=service_type, config=config,
                    description=description, enabled=enabled)
            except Exception:
                try:
                    registry.install(
                        from_scope, from_scope_id, service_id=sid,
                        service_type=service_type, config=config,
                        description=description, enabled=enabled)
                except Exception:
                    logger.debug("Failed to roll back service scope move", exc_info=True)
                raise
            flowfile.set_content(json.dumps({
                "ok": True,
                "service_id": sid,
                "from_scope": "conversation" if from_scope == "conv" else from_scope,
                "scope": "conversation" if to_scope == "conv" else to_scope,
            }).encode())
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
            registry = ServiceRegistry.get_instance()
            scope_id = _service_scope_id(scope, user_id, conv_id)
            svc_def = registry.get_definition(scope, scope_id, sid)
            if svc_def and svc_def.service_type == "relay" and (svc_def.config or {}).get("server_managed"):
                from core.server_relay_manager import ServerRelayManager
                ServerRelayManager.get_instance().cleanup_service_relay(svc_def.config or {})
            registry.uninstall(scope, scope_id, sid)
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
        # Registry services — canonical scope chain (conv > user > global,
        # parent conversations included), same path as the relay link dialog.
        _cc_conv_id = (body.get("conversation_id", "")
                       or flowfile.get_attribute("http.conversation_id") or "")
        try:
            from core.relay_bindings import list_available_relays
            for r in list_available_relays(user_id=user_id, conv_id=_cc_conv_id):
                sid = r.get("relay_id", "")
                if not sid or not r.get("connected"):
                    continue
                if any(entry["relay_id"] == sid for entry in relay_list):
                    continue
                relay_list.append({
                    "relay_id": sid,
                    "platform": r.get("platform") or "unknown",
                    "root": r.get("root", ""),
                })
        except Exception as e:
            logger.debug("Failed to list relays: %s", e)
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
                relay_svc = ServiceRegistry.get_instance().resolve(
                    relay_id, user_id=user_id, conv_id=conversation_id)
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

            _store_claude_tokens(
                service_id, access_token, refresh_token, expires_at,
                user_id=user_id, conv_id=conversation_id)
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
    return _UNHANDLED
