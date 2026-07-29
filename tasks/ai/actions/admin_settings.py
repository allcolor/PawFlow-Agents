"""AgentLoopTask actions - admin settings."""

import json
from typing import Any, Dict, List

from core import FlowFile


SYSTEM_PARAM_MANIFEST: List[Dict[str, Any]] = [
    {
        "key": "embedding_llm_service",
        "type": "service_ref",
        "service_type": "llmConnection",
        "storage": "param",
        "scope": "global",
        "section": "Memory",
        "label": "Memory embedding LLM service",
        "description": "Embedding-capable LLM service used by memory embeddings.",
        "apply": "immediate",
    },
    {
        "key": "PAWFLOW_USE_RTK",
        "type": "boolean",
        "storage": "param",
        "scope": "global",
        "section": "Tools",
        "label": "Use RTK relay rewriting",
        "description": "Enables RTK command/path rewriting for compatible relay tools.",
        "apply": "immediate",
    },
]

_MANIFEST_BY_KEY = {item["key"]: item for item in SYSTEM_PARAM_MANIFEST}


def _is_admin(flowfile: FlowFile) -> bool:
    return "admin" in (flowfile.get_attribute("http.auth.roles") or "")


def _json(flowfile: FlowFile, payload: Dict[str, Any], status: str = "200"):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode())
    if status != "200":
        flowfile.set_attribute("http.response.status", status)
    return [flowfile]


def _require_admin(flowfile: FlowFile):
    if _is_admin(flowfile):
        return None
    return _json(flowfile, {"error": "Requires admin role"}, "403")


def _role(value: str):
    from core.security import Role
    raw = str(value or "user").strip().lower()
    if raw not in {"admin", "user"}:
        raise ValueError(f"Invalid role '{value}'")
    return Role(raw)


def _users_with_identities():
    from core.identity_service import IdentityService
    from core.security import SecurityManager

    sm = SecurityManager.get_instance()
    identities = IdentityService.instance().list_all()
    users = []
    for user in sm.list_users():
        username = user.get("username", "")
        user["identities"] = identities.get(username, {})
        users.append(user)
    return users


def _set_global_param(key: str, value: Any):
    from core.config_store import ConfigStore
    from core.config_value import ConfigValue
    from core.paths import GLOBAL_PARAMS_FILE

    GLOBAL_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = ConfigStore.load_params(GLOBAL_PARAMS_FILE)
    data[key] = ConfigValue(value=value)
    ConfigStore.save_params(GLOBAL_PARAMS_FILE, data)


def _publish_conv_event(conversation_id: str, event: str, payload: Dict[str, Any]):
    if not conversation_id:
        return
    from core.conversation_event_bus import ConversationEventBus
    ConversationEventBus.instance().publish_event(conversation_id, event, payload)


def _publish_build_event(conversation_id: str, payload: Dict[str, Any]):
    _publish_conv_event(conversation_id, "cli_image_build", payload)


def _throttled(emit, interval: float = 0.5):
    """Wrap a progress emitter so it fires at most once per ``interval``.

    A build emits thousands of lines and the dialog only ever shows the latest.
    """
    import time

    last = [0.0]

    def _emit(line: str):
        now = time.monotonic()
        if now - last[0] < interval:
            return
        last[0] = now
        emit(line)

    return _emit


def _start_cli_image_rebuild(force: bool, conversation_id: str, user_id: str):
    """Run the CLI tools image rebuild in a background thread.

    The build outlives the action call (minutes), so the action acks immediately
    and progress arrives on the conversation's SSE channel as `cli_image_build`
    events. Rebuilding this image affects only the next CLI pool spawn — no
    running relay, conversation or user data is touched.
    """
    import logging
    import threading

    from core import update_manager

    logger = logging.getLogger(__name__)
    # docker build runs against the host socket: effectively root on the machine.
    # Trace who triggered it, with what, alongside the build output itself.
    logger.info("[update] CLI image rebuild requested by %s (force=%s, image=%s)",
                user_id or "?", force, update_manager.cli_image_name())

    def _run():
        _on_output = _throttled(lambda line: _publish_build_event(
            conversation_id, {"status": "progress", "line": line}))

        _publish_build_event(conversation_id, {"status": "started", "forced": force})
        try:
            result = update_manager.rebuild_cli_image(force=force, on_output=_on_output)
        except Exception as exc:
            logger.error("[update] CLI image rebuild crashed: %s", exc, exc_info=True)
            _publish_build_event(conversation_id, {"status": "error", "error": str(exc)})
            return
        logger.info("[update] CLI image rebuild finished by %s: ok=%s exit=%s",
                    user_id or "?", result.get("ok"), result.get("exit_code"))
        _publish_build_event(conversation_id, {
            "status": "done" if result.get("ok") else "error",
            "forced": force,
            "image": result.get("image", ""),
            "exit_code": result.get("exit_code"),
            "output": result.get("output", ""),
        })

    threading.Thread(target=_run, name="cli-image-rebuild", daemon=True).start()


def _start_relay_image_rebuild(key: str, force: bool, conversation_id: str, user_id: str):
    """Rebuild a relay image in a background thread.

    Progress arrives as `relay_image_build` events. Building changes nothing for
    the relays already running: they keep the image they were started from until
    an explicit restart recreates their containers.
    """
    import logging
    import threading

    from core import update_manager

    logger = logging.getLogger(__name__)
    logger.info("[update] Relay image rebuild requested by %s (image=%s, force=%s, tag=%s)",
                user_id or "?", key, force, update_manager.relay_image_name(key))

    def _emit(payload: Dict[str, Any]):
        _publish_conv_event(conversation_id, "relay_image_build", {"image_key": key, **payload})

    def _run():
        _on_output = _throttled(lambda line: _emit({"status": "progress", "line": line}))
        _emit({"status": "started", "forced": force})
        try:
            result = update_manager.rebuild_relay_image(key, force=force, on_output=_on_output)
        except Exception as exc:
            logger.error("[update] Relay image rebuild crashed: %s", exc, exc_info=True)
            _emit({"status": "error", "error": str(exc)})
            return
        logger.info("[update] Relay image rebuild finished by %s: image=%s ok=%s exit=%s",
                    user_id or "?", key, result.get("ok"), result.get("exit_code"))
        _emit({
            "status": "done" if result.get("ok") else "error",
            "forced": force,
            "image": result.get("image", ""),
            "exit_code": result.get("exit_code"),
            "output": result.get("output", ""),
        })

    threading.Thread(target=_run, name="relay-image-rebuild", daemon=True).start()


def _start_relay_restart(conversation_id: str, user_id: str):
    """Recreate every server relay container in a background thread.

    Each relay is briefly unavailable while its container is replaced; the
    workspace, the volumes and the relay identity survive the operation.
    Progress arrives as `relay_restart` events, one per relay.
    """
    import logging
    import threading

    from core import update_manager

    logger = logging.getLogger(__name__)
    logger.info("[update] Server relay restart requested by %s", user_id or "?")

    def _emit(payload: Dict[str, Any]):
        _publish_conv_event(conversation_id, "relay_restart", payload)

    def _run():
        _emit({"status": "started"})
        try:
            result = update_manager.restart_server_relays(
                on_progress=lambda p: _emit({"status": "progress", **p}))
        except Exception as exc:
            logger.error("[update] Server relay restart crashed: %s", exc, exc_info=True)
            _emit({"status": "error", "error": str(exc)})
            return
        logger.info("[update] Server relay restart finished by %s: %s/%s recreated",
                    user_id or "?", result.get("restarted"), result.get("total"))
        _emit({"status": "done", **result})

    threading.Thread(target=_run, name="relay-restart", daemon=True).start()


def _handle_admin_settings(self, action, body, store, user_id, flowfile):
    """Handle admin settings actions. Returns [flowfile] or None."""

    if action == "admin_users_list":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        return _json(flowfile, {"users": _users_with_identities()})

    if action == "admin_user_create":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        username = str(body.get("username", "") or "").strip()
        password = str(body.get("password", "") or "")
        if not username or not password:
            return _json(flowfile, {"error": "username and password are required"}, "400")
        try:
            from core.security import SecurityManager
            sm = SecurityManager.get_instance()
            user = sm.create_user(
                username, password, _role(body.get("role", "user")),
                email=str(body.get("email", "") or ""),
                display_name=str(body.get("display_name", "") or ""),
            )
            if body.get("enabled") is False:
                sm.update_user(username, enabled=False)
            payload = {k: v for k, v in user.to_dict().items() if k != "password_hash"}
            return _json(flowfile, {"ok": True, "user": payload})
        except Exception as exc:
            return _json(flowfile, {"error": str(exc)}, "400")

    if action == "admin_user_update":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        username = str(body.get("username", "") or "").strip()
        if not username:
            return _json(flowfile, {"error": "Missing username"}, "400")
        try:
            kwargs: Dict[str, Any] = {}
            if "role" in body:
                kwargs["role"] = _role(body.get("role"))
            for key in ("enabled", "email", "display_name"):
                if key in body:
                    kwargs[key] = body.get(key)
            from core.security import SecurityManager
            SecurityManager.get_instance().update_user(username, **kwargs)
            return _json(flowfile, {"ok": True})
        except Exception as exc:
            return _json(flowfile, {"error": str(exc)}, "400")

    if action == "admin_user_reset_password":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        username = str(body.get("username", "") or "").strip()
        password = str(body.get("password", "") or "")
        if not username or not password:
            return _json(flowfile, {"error": "username and password are required"}, "400")
        try:
            from core.security import SecurityManager
            SecurityManager.get_instance().update_user(username, password=password)
            return _json(flowfile, {"ok": True})
        except Exception as exc:
            return _json(flowfile, {"error": str(exc)}, "400")

    if action == "admin_user_delete":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        username = str(body.get("username", "") or "").strip()
        if not username:
            return _json(flowfile, {"error": "Missing username"}, "400")
        try:
            from core.security import SecurityManager
            SecurityManager.get_instance().delete_user(username)
            return _json(flowfile, {"ok": True})
        except Exception as exc:
            return _json(flowfile, {"error": str(exc)}, "400")

    if action == "admin_identity_link":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        username = str(body.get("username", "") or "").strip()
        channel = str(body.get("channel", "") or "").strip()
        channel_id = str(body.get("channel_id", "") or "").strip()
        old_channel = str(body.get("old_channel", "") or "").strip()
        if not username or not channel or not channel_id:
            return _json(flowfile, {"error": "username, channel, and channel_id are required"}, "400")
        from core.security import SecurityManager
        if not SecurityManager.get_instance().get_user(username):
            return _json(flowfile, {"error": "user does not exist"}, "400")
        from core.identity_service import IdentityService
        ids = IdentityService.instance()
        old_channel_id = ids.get_channel_id(username, old_channel) if old_channel else ""
        if old_channel and old_channel != channel and old_channel_id == channel_id:
            ids.unlink(username, old_channel)
        ok = ids.link(username, channel, channel_id)
        if not ok:
            return _json(flowfile, {"error": "identity is already linked to another user"}, "409")
        if old_channel and old_channel != channel and old_channel_id != channel_id:
            ids.unlink(username, old_channel)
        return _json(flowfile, {"ok": True})

    if action == "admin_identity_unlink":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        username = str(body.get("username", "") or "").strip()
        channel = str(body.get("channel", "") or "").strip()
        if not username or not channel:
            return _json(flowfile, {"error": "username and channel are required"}, "400")
        from core.identity_service import IdentityService
        ok = IdentityService.instance().unlink(username, channel)
        return _json(flowfile, {"ok": ok})

    if action == "admin_oauth_tokens_list":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core import oauth_invite_tokens
        return _json(flowfile, {"tokens": oauth_invite_tokens.list_tokens()})

    if action == "admin_oauth_token_create":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        role = str(body.get("role", "user") or "user").strip()
        link_username = str(body.get("link_username", "") or "").strip()
        try:
            ttl_seconds = int(body.get("ttl_seconds", 3600) or 3600)
        except (TypeError, ValueError):
            return _json(flowfile, {"error": "ttl_seconds must be an integer"}, "400")
        if ttl_seconds < 60:
            return _json(flowfile, {"error": "ttl_seconds must be at least 60"}, "400")
        try:
            _role(role)
            if link_username:
                from core.security import SecurityManager
                if not SecurityManager.get_instance().get_user(link_username):
                    return _json(flowfile, {"error": "link user does not exist"}, "400")
            from core import oauth_invite_tokens
            token = oauth_invite_tokens.create_token(
                role=role,
                link_username=link_username,
                ttl_seconds=ttl_seconds,
                created_by=user_id,
            )
            return _json(flowfile, {"ok": True, "token": token})
        except Exception as exc:
            return _json(flowfile, {"error": str(exc)}, "400")

    if action == "admin_oauth_token_revoke":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        token_id = str(body.get("token_id", "") or "").strip()
        if not token_id:
            return _json(flowfile, {"error": "Missing token_id"}, "400")
        from core import oauth_invite_tokens
        return _json(flowfile, {"ok": oauth_invite_tokens.revoke_token(token_id)})

    if action == "system_params_get":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core.expression import _load_global_parameters
        current = _load_global_parameters()
        values = {item["key"]: str(current.get(item["key"], ""))
                  for item in SYSTEM_PARAM_MANIFEST}
        return _json(flowfile, {"manifest": SYSTEM_PARAM_MANIFEST, "values": values})

    if action == "system_param_set":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        key = str(body.get("key", "") or "").strip()
        if key not in _MANIFEST_BY_KEY:
            return _json(flowfile, {"error": f"Unsupported system parameter '{key}'"}, "400")
        _set_global_param(key, body.get("value", ""))
        return _json(flowfile, {"ok": True})

    if action == "admin_check_updates":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core import update_manager
        report = update_manager.check_updates()
        report["build_running"] = update_manager.cli_build_running()
        report["relay_build_running"] = update_manager.relay_build_running()
        report["relay_restart_running"] = update_manager.relay_restart_running()
        report["relay_images"] = [
            {"key": t["key"], "label": t["label"],
             "image": update_manager.relay_image_name(t["key"])}
            for t in update_manager.RELAY_BUILD_TARGETS]
        return _json(flowfile, report)

    if action == "admin_rebuild_cli_image":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core import update_manager
        if update_manager.cli_build_running():
            return _json(flowfile, {"error": "A rebuild is already running"}, "409")
        force = bool(body.get("force"))
        conversation_id = str(body.get("conversation_id", "") or "")
        _start_cli_image_rebuild(force, conversation_id, user_id)
        return _json(flowfile, {"ok": True, "started": True, "forced": force,
                               "image": update_manager.cli_image_name()})

    if action == "admin_rebuild_relay_image":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core import update_manager
        key = str(body.get("image", "") or "")
        try:
            update_manager.relay_build_target(key)
        except ValueError as exc:
            return _json(flowfile, {"error": str(exc)}, "400")
        if update_manager.relay_build_running():
            return _json(flowfile, {"error": "A relay image rebuild is already running"}, "409")
        force = bool(body.get("force"))
        conversation_id = str(body.get("conversation_id", "") or "")
        _start_relay_image_rebuild(key, force, conversation_id, user_id)
        return _json(flowfile, {"ok": True, "started": True, "forced": force,
                               "image_key": key,
                               "image": update_manager.relay_image_name(key)})

    if action == "admin_server_update_check":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core import update_manager
        return _json(flowfile, update_manager.server_update_preflight())

    if action == "admin_update_server":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        import logging
        from core import update_manager
        # Restarting kills every running agent turn — the same cost as running
        # `docker compose up -d` by hand. The UI names that cost; the decision
        # is the operator's, so nothing here refuses on their behalf.
        pull_source = bool(body.get("pull_source"))
        # Off by default: the host artifacts refreshed from the new image
        # include the start script the update is about to run, so a failed
        # refresh aborts unless the operator explicitly accepts starting the
        # new server image with the previous version's host side.
        force_artifacts = bool(body.get("force_artifacts"))
        logging.getLogger(__name__).warning(
            "[update] Server update requested by %s (pull_source=%s, "
            "force_artifacts=%s, %s agent turn(s) in flight)",
            user_id or "?", pull_source, force_artifacts,
            update_manager.running_agent_count())
        result = update_manager.update_server(pull_source=pull_source,
                                              force_artifacts=force_artifacts)
        if not result.get("ok"):
            return _json(flowfile, {"error": result.get("reason", "Update refused")}, "409")
        return _json(flowfile, result)

    if action == "admin_restart_relays":
        denied = _require_admin(flowfile)
        if denied:
            return denied
        from core import update_manager
        if update_manager.relay_restart_running():
            return _json(flowfile, {"error": "A relay restart is already running"}, "409")
        conversation_id = str(body.get("conversation_id", "") or "")
        _start_relay_restart(conversation_id, user_id)
        return _json(flowfile, {"ok": True, "started": True})

    return None
