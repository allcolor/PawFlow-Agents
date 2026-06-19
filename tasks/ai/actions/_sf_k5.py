"""AgentLoopTask actions  - service flow"""

import json
import logging
import time
import threading

from tasks.ai.actions._sf_base import _UNHANDLED
from tasks.ai.actions._sf_base import (
    _is_admin,
    _resolve_flow_template_path,
    _flow_template_storage_info,
    _validate_flow_package_name,
    _ensure_template_scope_edit_allowed,
    _rewrite_flow_template_package,
    _flow_deploy_schema_payload,
    _flow_one_shot_trigger_payload,
    _load_flow_instance_template_raw,
)
from tasks.ai.actions._sf_routes import (
    _publish_command_result,
)

logger = logging.getLogger(__name__)


def _handle_sf_k5(self, action, body, store, user_id, flowfile, _helpers):
    """service_flow cluster _sf_k5. Returns result or _UNHANDLED."""
    (_find_relay_svc, _audio_lookup_token, _get_server_relay_container_ip,
     _get_relay_published_port, _server_relay_proxy_target, _private_gateway_for_body) = _helpers
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
        requested_scope = body.get("scope", "")
        agent_name = (
            body.get("_agent_name", "")
            or body.get("call_agent_name", "")
            or flowfile.get_attribute("call_agent_name")
            or getattr(self, "_agent_name", "")
            or ""
        )
        deploy_scope = requested_scope or ("conversation" if conv_id and agent_name else "user")
        if conv_id and agent_name:
            deploy_scope = "conversation"
        params = body.get("parameters", {})
        service_overrides = body.get("service_overrides", {})
        service_configs = body.get("service_configs", {})
        if deploy_scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
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
            iid = dr.deploy(
                template_path=str(tpath),
                owner=None if deploy_scope == "global" else uid,
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

    if action == "move_flow_template_package":
        template_id = body.get("template_id", "")
        target_package = body.get("package", "")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if not template_id:
            flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
            return [flowfile]
        try:
            import shutil
            from core.paths import flow_package_dir

            target_package = _validate_flow_package_name(target_package)
            tpath = _resolve_flow_template_path(template_id, user_id, conv_id)
            if not tpath:
                flowfile.set_content(json.dumps({"error": f"Template '{template_id}' not found"}).encode())
                return [flowfile]
            info = _flow_template_storage_info(tpath, user_id, conv_id)
            denied = _ensure_template_scope_edit_allowed(flowfile, info["scope"])
            if denied:
                flowfile.set_content(json.dumps(denied).encode())
                return [flowfile]
            if target_package == info["storage_package"]:
                flowfile.set_content(json.dumps({"ok": True, "unchanged": True}).encode())
                return [flowfile]
            dest_pkg = flow_package_dir(
                target_package, info["repo_scope"], user_id, conv_id)
            dest_dir = dest_pkg / info["flow_name"]
            if dest_dir.exists():
                flowfile.set_content(json.dumps(
                    {"error": f"Flow already exists in package '{target_package}'"}).encode())
                return [flowfile]
            dest_pkg.mkdir(parents=True, exist_ok=True)
            pkg_file = dest_pkg / "package.json"
            if not pkg_file.exists():
                pkg_file.write_text(json.dumps({
                    "name": target_package,
                    "description": "",
                    "author": "",
                }, indent=2) + "\n", encoding="utf-8")
            shutil.move(str(info["flow_dir"]), str(dest_dir))
            _rewrite_flow_template_package(dest_dir, target_package)
            from tasks.ai.actions.agent_resource import invalidate_flow_templates_cache
            invalidate_flow_templates_cache(user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "template_id": template_id,
                "package": target_package,
                "scope": info["scope"],
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "promote_flow_template":
        template_id = body.get("template_id", "")
        target_scope = body.get("target_scope", "")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if not template_id:
            flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
            return [flowfile]
        if target_scope not in {"conversation", "user", "global"}:
            flowfile.set_content(json.dumps({"error": "Invalid target_scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if target_scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if target_scope == "global" and not _is_admin(flowfile):
            flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        try:
            from core.repository import ScopedRepository

            # The user/conv side belongs to an owner; an admin may target
            # another user (e.g. demote a global template down to user X).
            from core import admin_scope
            _owner_scope = "conv" if target_scope == "conversation" else "user"
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
            _res_user = _owner_user or user_id
            _res_conv = _owner_conv if target_scope == "conversation" else conv_id

            tpath = _resolve_flow_template_path(template_id, _res_user, _res_conv)
            if not tpath:
                flowfile.set_content(json.dumps({"error": f"Template '{template_id}' not found"}).encode())
                return [flowfile]
            info = _flow_template_storage_info(tpath, _res_user, _res_conv)
            denied = _ensure_template_scope_edit_allowed(flowfile, info["scope"])
            if denied:
                flowfile.set_content(json.dumps(denied).encode())
                return [flowfile]
            if target_scope == info["scope"]:
                flowfile.set_content(json.dumps({"ok": True, "unchanged": True}).encode())
                return [flowfile]
            fqn = f"{info['storage_package']}.{info['flow_name']}:{info['version']}"
            from_repo_scope = info["repo_scope"]
            to_repo_scope = "conv" if target_scope == "conversation" else target_scope
            if {"conversation": 0, "user": 1, "global": 2}[target_scope] > {"conversation": 0, "user": 1, "global": 2}[info["scope"]]:
                ScopedRepository().promote(
                    "flows", fqn, from_repo_scope, to_repo_scope,
                    user_id=_res_user, conv_id=_res_conv, move=True)
            else:
                ScopedRepository().demote(
                    "flows", fqn, from_repo_scope, to_repo_scope,
                    user_id=_res_user, conv_id=_res_conv, move=True)
            from tasks.ai.actions.agent_resource import invalidate_flow_templates_cache
            invalidate_flow_templates_cache(_res_user)
            flowfile.set_content(json.dumps({
                "ok": True,
                "template_id": template_id,
                "from_scope": info["scope"],
                "scope": target_scope,
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "delete_flow_template":
        template_id = body.get("template_id", "")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if not template_id:
            flowfile.set_content(json.dumps({"error": "Missing template_id"}).encode())
            return [flowfile]
        try:
            import shutil

            tpath = _resolve_flow_template_path(template_id, user_id, conv_id)
            if not tpath:
                flowfile.set_content(json.dumps({"error": f"Template '{template_id}' not found"}).encode())
                return [flowfile]
            info = _flow_template_storage_info(tpath, user_id, conv_id)
            denied = _ensure_template_scope_edit_allowed(flowfile, info["scope"])
            if denied:
                flowfile.set_content(json.dumps(denied).encode())
                return [flowfile]
            shutil.rmtree(info["flow_dir"])
            from tasks.ai.actions.agent_resource import invalidate_flow_templates_cache
            invalidate_flow_templates_cache(user_id)
            flowfile.set_content(json.dumps({
                "ok": True,
                "template_id": template_id,
                "scope": info["scope"],
            }).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    if action == "promote_flow":
        iid = body.get("instance_id", "")
        target_scope = body.get("target_scope", "user")
        conv_id = body.get("conversation_id", "") or flowfile.get_attribute("http.conversation_id") or ""
        if not iid:
            flowfile.set_content(json.dumps({"error": "Missing instance_id"}).encode())
            return [flowfile]
        if target_scope not in {"conversation", "user", "global"}:
            flowfile.set_content(json.dumps({"error": "Invalid target_scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if target_scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "Missing conversation_id for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if target_scope == "global" and "admin" not in (flowfile.get_attribute("http.auth.roles") or ""):
            flowfile.set_content(json.dumps(
                {"error": "Requires admin role for global scope"}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        try:
            from core.deployment_registry import DeploymentRegistry
            dr = DeploymentRegistry.get_instance()
            inst = dr.get(iid)
            if not inst:
                flowfile.set_content(json.dumps({"error": "Instance not found"}).encode())
                return [flowfile]
            if inst.owner is None and target_scope != "global" and not _is_admin(flowfile):
                flowfile.set_content(json.dumps({"error": "Requires admin role for global scope"}).encode())
                flowfile.set_attribute("http.response.status", "403")
                return [flowfile]
            # An admin may operate on another user's instance; a non-admin is
            # restricted to their own.
            if (user_id and inst.owner and inst.owner != user_id
                    and not _is_admin(flowfile)):
                flowfile.set_content(json.dumps({"error": "Permission denied"}).encode())
                return [flowfile]
            # The user/conv side belongs to an owner; an admin may target
            # another user ("which user to demote to"). Default = caller.
            from core import admin_scope
            _owner_scope = "conv" if target_scope == "conversation" else "user"
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
            if target_scope == "global":
                dr.set_owner(iid, None)
                inst = dr.get(iid)
                if inst:
                    inst.conversation_id = None
                    dr._save_instance(inst)
            elif target_scope == "conversation":
                dr.set_owner(iid, _owner_user)
                inst = dr.get(iid)
                if inst:
                    inst.conversation_id = _owner_conv or conv_id
                    dr._save_instance(inst)
            else:
                dr.set_owner(iid, _owner_user)
                inst = dr.get(iid)
                if inst:
                    inst.conversation_id = None
                    dr._save_instance(inst)
            flowfile.set_content(json.dumps({"ok": True, "scope": target_scope}).encode())
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
            one_shot_meta = {}
            try:
                raw = _load_flow_instance_template_raw(inst, user_id)
                if raw:
                    template_params = raw.get("parameters", {})
                    deploy_schema = _flow_deploy_schema_payload(
                        raw, parameters=inst.parameters,
                        service_overrides=inst.service_overrides,
                        service_configs=inst.service_configs)
                    one_shot_meta = _flow_one_shot_trigger_payload(raw)
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
                "one_shot_triggers": one_shot_meta.get("one_shot_triggers", []),
                "has_persistent_sources": one_shot_meta.get("has_persistent_sources", False),
                "is_one_shot_flow": one_shot_meta.get("is_one_shot_flow", False),
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
                    "workspace_dir": meta.get("workspace_dir", ""),
                    "message": (
                        f"Server workspace ready. "
                        f"Use relay service '{meta['relay_id']}' to access your files."
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

    return _UNHANDLED
