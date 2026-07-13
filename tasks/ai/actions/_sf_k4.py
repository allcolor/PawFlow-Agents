"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _credential_provider_for_service,
    _store_claude_tokens,
    _store_gemini_tokens,
    _flow_one_shot_trigger_payload,
    _load_flow_instance_template_raw,
)
from tasks.ai.actions._sf_routes import (
    _publish_command_result,
)

logger = logging.getLogger(__name__)


def _handle_sf_k4(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k4. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
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
                _store_gemini_tokens(
                    service_id, access_token, refresh_token, expires_at,
                    user_id=user_id, conv_id=body.get("conversation_id", ""))
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
                        _store_gemini_tokens(
                            service_id, access_token, refresh_token, expires_at,
                            user_id=user_id, conv_id=body.get("conversation_id", ""))
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
                _store_claude_tokens(
                    service_id, access_token, refresh_token, expires_at,
                    user_id=user_id, conv_id=body.get("conversation_id", ""))
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
                        _store_claude_tokens(
                            service_id, access_token, refresh_token, expires_at,
                            user_id=user_id, conv_id=body.get("conversation_id", ""))
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

        source_svc = None
        if relay_source:
            # Explicit source — resolve across conv > user > global scopes
            _cid = (body.get("conversation_id", "")
                    or flowfile.get_attribute("http.conversation_id") or "")
            source_svc = ureg.resolve(relay_source, user_id=user_id, conv_id=_cid)
        else:
            # Find the first connected relay across conv > user > global
            _cid = (body.get("conversation_id", "")
                    or flowfile.get_attribute("http.conversation_id") or "")
            for sdef in ureg.resolve_by_type("relay", user_id=user_id,
                                             conv_id=_cid):
                svc = ureg.resolve(sdef.service_id, user_id=user_id,
                                   conv_id=_cid)
                if svc and hasattr(svc, '_relay_pool') and svc._relay_pool:
                    source_svc = svc
                    relay_source = sdef.service_id
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

        # Find which relay this service is connected through — walk the
        # canonical conv > user > global chain so conv-scoped relays
        # (spawned by packages or relay_connect) can be disconnected too.
        from core.service_registry import ServiceRegistry
        ureg = ServiceRegistry.get_instance()
        _cid = (body.get("conversation_id", "")
                or flowfile.get_attribute("http.conversation_id") or "")
        _sdef = ureg.resolve_definition(service_id, user_id=user_id,
                                        conv_id=_cid)
        svc = (ureg.get_live_instance(_sdef.scope, _sdef.scope_id, service_id)
               if _sdef else None)
        if svc:
            try:
                svc.disconnect()
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
            try:
                ureg.uninstall(_sdef.scope, _sdef.scope_id, service_id)
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Also try to send stop_relay to all connected relays
        try:
            for sdef in ureg.resolve_by_type("relay", user_id=user_id,
                                             conv_id=_cid):
                _svc = ureg.get_live_instance(
                    sdef.scope, sdef.scope_id, sdef.service_id)
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
            if inst and not inst.owner:
                roles = flowfile.get_attribute("http.auth.roles") or ""
                if "admin" not in roles:
                    flowfile.set_content(json.dumps({"error": "Requires admin role"}).encode())
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
                parameter_overrides = body.get("parameters") or {}
                if parameter_overrides:
                    if not isinstance(parameter_overrides, dict):
                        flowfile.set_content(json.dumps(
                            {"error": "parameters must be an object"}).encode())
                        flowfile.set_attribute("http.response.status", "400")
                        return [flowfile]
                    inst.parameters.update(parameter_overrides)
                    dr._save_instance(inst)
                selected_trigger_ids = body.get("entry_task_ids")
                if selected_trigger_ids is None:
                    selected_trigger_ids = body.get("one_shot_trigger_ids")
                if selected_trigger_ids is not None:
                    if not isinstance(selected_trigger_ids, list):
                        flowfile.set_content(json.dumps(
                            {"error": "entry_task_ids must be a list"}).encode())
                        flowfile.set_attribute("http.response.status", "400")
                        return [flowfile]
                    selected_trigger_ids = [str(tid) for tid in selected_trigger_ids if str(tid)]
                    raw = _load_flow_instance_template_raw(inst, user_id)
                    one_shot_meta = _flow_one_shot_trigger_payload(raw or {})
                    valid_trigger_ids = {
                        item.get("task_id")
                        for item in one_shot_meta.get("one_shot_triggers", [])
                    }
                    if not one_shot_meta.get("is_one_shot_flow"):
                        flowfile.set_content(json.dumps(
                            {"error": "Flow has no selectable one-shot triggers"}).encode())
                        flowfile.set_attribute("http.response.status", "400")
                        return [flowfile]
                    if not selected_trigger_ids and valid_trigger_ids:
                        flowfile.set_content(json.dumps(
                            {"error": "Select at least one one-shot trigger"}).encode())
                        flowfile.set_attribute("http.response.status", "400")
                        return [flowfile]
                    invalid = [tid for tid in selected_trigger_ids
                               if tid not in valid_trigger_ids]
                    if invalid:
                        flowfile.set_content(json.dumps(
                            {"error": f"Unknown one-shot trigger(s): {invalid}"}).encode())
                        flowfile.set_attribute("http.response.status", "400")
                        return [flowfile]
                restored = reg._restore_instance(iid, inst.flow_path,
                                                 inst.max_workers, inst.max_retries,
                                                 flow_fqn=getattr(inst, "flow_fqn", "") or "",
                                                 flow_scope=getattr(inst, "flow_scope", "") or "",
                                                 parameters=inst.parameters,
                                                 service_overrides=inst.service_overrides,
                                                 service_configs=inst.service_configs,
                                                 owner=inst.owner or "",
                                                 conversation_id=inst.conversation_id or "",
                                                 agent_name=getattr(inst, "agent_name", "") or "",
                                                 enabled_one_shot_root_task_ids=selected_trigger_ids)
                if not restored:
                    flowfile.set_content(json.dumps({"error": "Failed to start flow"}).encode())
                    flowfile.set_attribute("http.response.status", "500")
                    return [flowfile]
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
            from core import admin_scope
            if admin_scope.wants_view_all(body, flowfile):
                # Admin cross-user view. Single walk of global + the whole
                # users/ tree; owner/conv are derived from each match's path,
                # and the first path component under users/<uid>/ that is a
                # known conversation id marks a conv-scoped template (avoids
                # the user/conv directory ambiguity + double counting).
                cidx = admin_scope.conv_index()
                _flows = REPOSITORY_DIR / "flows"

                def _emit(vfile, flow_dir, scope_label, owner_id, cid):
                    try:
                        raw = json.loads(vfile.read_text(encoding="utf-8"))
                    except Exception as e:
                        logger.debug("list_available_flows(all): skip %s: %s",
                                     vfile, e)
                        return
                    templates.append({
                        "id": raw.get("id") or flow_dir.name,
                        "name": raw.get("name") or flow_dir.name,
                        "version": vfile.stem,
                        "description": raw.get("description") or "",
                        "scope": raw.get("scope") or scope_label,
                        "tasks_count": len(raw.get("tasks", {}) or {}),
                        "services_count": len(raw.get("services", {}) or {}),
                        "file_path": str(vfile),
                        "owner_id": owner_id,
                        "owner_display": (admin_scope.display_name_for(owner_id)
                                          if owner_id else ""),
                        "conv_id": cid,
                        "conv_title": cidx.get(cid, {}).get("title", ""),
                    })

                def _walk(root, scope_label, owner_id, cid):
                    if not root.is_dir():
                        return
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
                            _emit(vfile, flow_dir, scope_label, owner_id, cid)
                        except Exception as e:
                            logger.debug(
                                "list_available_flows(all): skip %s: %s",
                                latest, e)

                _walk(_flows / "global", "global", "", "")
                users_root = _flows / "users"
                if users_root.is_dir():
                    for udir in sorted(
                            x for x in users_root.iterdir() if x.is_dir()):
                        uid = udir.name
                        for latest in udir.rglob("latest.json"):
                            flow_dir = latest.parent
                            try:
                                rel = flow_dir.relative_to(udir).parts
                            except ValueError:
                                continue
                            first = rel[0] if rel else ""
                            is_conv = first in cidx
                            try:
                                ptr = json.loads(
                                    latest.read_text(encoding="utf-8"))
                                version = (ptr.get("version") or "").strip()
                                if not version:
                                    continue
                                vfile = (flow_dir / "versions"
                                         / f"{version}.json")
                                if not vfile.is_file():
                                    continue
                                _emit(
                                    vfile, flow_dir,
                                    "conversation" if is_conv else "user",
                                    uid, first if is_conv else "")
                            except Exception as e:
                                logger.debug(
                                    "list_available_flows(all): skip %s: %s",
                                    latest, e)
                templates.sort(
                    key=lambda t: (t["scope"], t.get("owner_id", ""), t["name"]))
                flowfile.set_content(json.dumps(
                    {"templates": templates, "view": "all"},
                    ensure_ascii=False).encode())
                return [flowfile]
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


    return _UNHANDLED
