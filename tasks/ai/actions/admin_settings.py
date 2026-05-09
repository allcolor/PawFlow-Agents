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
    try:
        return Role(str(value or "viewer"))
    except ValueError:
        raise ValueError(f"Invalid role '{value}'")


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
                username, password, _role(body.get("role", "viewer")),
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

    return None
