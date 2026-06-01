"""AgentLoopTask actions — secrets variables"""

import json
import logging
import time
import threading
from typing import Dict, Any, List, Optional

from core import FlowFile
from core.llm_client import LLMMessage, LLMClient
from core.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


def _is_admin(flowfile) -> bool:
    return "admin" in (flowfile.get_attribute("http.auth.roles") or "")


def _requested_scope(body: Dict[str, Any], *, default: str = "user") -> str:
    scope = str(body.get("scope") or default).strip().lower()
    if scope not in {"conversation", "user", "global"}:
        raise ValueError("scope must be conversation, user, or global")
    return scope


def _scope_error(flowfile, message: str):
    flowfile.set_content(json.dumps({"error": message}).encode())
    flowfile.set_attribute("http.response.status", "403")
    return [flowfile]


def _param_path(scope: str, user_id: str):
    if scope == "global":
        from core.paths import GLOBAL_PARAMS_FILE
        return GLOBAL_PARAMS_FILE
    from core.paths import user_params_path
    return user_params_path(user_id)


def _secret_path(scope: str, user_id: str):
    if scope == "global":
        from core.paths import GLOBAL_SECRETS_FILE
        return GLOBAL_SECRETS_FILE
    from core.paths import user_secrets_path
    return user_secrets_path(user_id)


def _load_param_value(scope: str, key: str, store, user_id: str, conv_id: str):
    if scope == "conversation":
        data = store.get_extra(conv_id, "conv_parameters") or {}
        return data.get(key)
    from core.config_store import ConfigStore
    data = ConfigStore.load_params(_param_path(scope, user_id))
    item = data.get(key)
    return None if item is None else item.value


def _load_secret_value(scope: str, key: str, store, user_id: str, conv_id: str):
    from core.secrets import get_secrets_manager
    sm = get_secrets_manager()
    if scope == "conversation":
        data = store.get_extra(conv_id, "conv_secrets") or {}
        value = data.get(key)
        return None if value is None else sm.decrypt(value)
    from core.config_store import ConfigStore
    data = ConfigStore.load_secrets(_secret_path(scope, user_id))
    item = data.get(key)
    return None if item is None else item.value


def _write_param_value(scope: str, key: str, value: Any, store, user_id: str, conv_id: str):
    if scope == "conversation":
        data = store.get_extra(conv_id, "conv_parameters") or {}
        data[key] = value
        store.set_extra(conv_id, "conv_parameters", data)
        return
    from core.config_store import ConfigStore
    from core.config_value import ConfigValue
    path = _param_path(scope, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ConfigStore.load_params(path)
    data[key] = ConfigValue(value=value)
    ConfigStore.save_params(path, data)


def _write_secret_value(scope: str, key: str, value: str, store, user_id: str, conv_id: str):
    if scope == "conversation":
        from core.secrets import get_secrets_manager
        data = store.get_extra(conv_id, "conv_secrets") or {}
        data[key] = get_secrets_manager().encrypt(value)
        store.set_extra(conv_id, "conv_secrets", data)
        return
    from core.config_store import ConfigStore
    from core.config_value import ConfigValue
    path = _secret_path(scope, user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ConfigStore.load_secrets(path)
    data[key] = ConfigValue(value=value)
    ConfigStore.save_secrets(path, data)


def _delete_param_value(scope: str, key: str, store, user_id: str, conv_id: str):
    if scope == "conversation":
        data = store.get_extra(conv_id, "conv_parameters") or {}
        data.pop(key, None)
        store.set_extra(conv_id, "conv_parameters", data)
        return
    from core.config_store import ConfigStore
    path = _param_path(scope, user_id)
    data = ConfigStore.load_params(path)
    data.pop(key, None)
    ConfigStore.save_params(path, data)


def _delete_secret_value(scope: str, key: str, store, user_id: str, conv_id: str):
    if scope == "conversation":
        data = store.get_extra(conv_id, "conv_secrets") or {}
        data.pop(key, None)
        store.set_extra(conv_id, "conv_secrets", data)
        return
    from core.config_store import ConfigStore
    path = _secret_path(scope, user_id)
    data = ConfigStore.load_secrets(path)
    data.pop(key, None)
    ConfigStore.save_secrets(path, data)


def _handle_secrets_variables(self, action, body, store, user_id, flowfile):
    """Handle secrets variables actions. Returns [flowfile] or None."""


    if action == "add_secret":
        key = body.get("key", "").strip()
        value = body.get("value", "")
        if not key or not value:
            flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        uid = user_id
        from pathlib import Path
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        encrypted = sm.encrypt(value)
        from core.paths import user_secrets_path; secrets_path = user_secrets_path(uid)
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets = {}
        if secrets_path.exists():
            try:
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        secrets[key] = encrypted
        secrets_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")
        flowfile.set_content(json.dumps({
            "result": f"Secret '{key}' stored. Use ${{{key}}} in expressions.",
            "key": key,
        }).encode())
        return [flowfile]

    if action == "add_variable":
        key = body.get("key", "").strip()
        value = body.get("value", "")
        if not key or not value:
            flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        uid = user_id
        from pathlib import Path
        from core.paths import user_params_path; params_path = user_params_path(uid)
        params_path.parent.mkdir(parents=True, exist_ok=True)
        params = {}
        if params_path.exists():
            try:
                params = json.loads(params_path.read_text(encoding="utf-8"))
            except Exception:
                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        params[key] = value
        params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
        flowfile.set_content(json.dumps({
            "result": f"Parameter '{key}' stored. Use ${{user.{key}}} in flows.",
            "key": key,
        }).encode())
        return [flowfile]

    if action == "list_secrets":
        uid = user_id
        from pathlib import Path
        from core.paths import user_secrets_path; secrets_path = user_secrets_path(uid)
        if not secrets_path.exists():
            flowfile.set_content(json.dumps({"result": "No secrets stored."}).encode())
            return [flowfile]
        try:
            secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
        except Exception:
            flowfile.set_content(json.dumps({"result": "Error reading secrets."}).encode())
            return [flowfile]
        if not secrets:
            flowfile.set_content(json.dumps({"result": "No secrets stored."}).encode())
            return [flowfile]
        lines = [f"Secrets ({len(secrets)}):"]
        for k in sorted(secrets.keys()):
            lines.append(f"- {k} → ${{{k}}}")
        flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
        return [flowfile]

    if action == "list_variables":
        uid = user_id
        from pathlib import Path
        from core.paths import user_params_path; params_path = user_params_path(uid)
        if not params_path.exists():
            flowfile.set_content(json.dumps({"result": "No parameters stored."}).encode())
            return [flowfile]
        try:
            params = json.loads(params_path.read_text(encoding="utf-8"))
        except Exception:
            flowfile.set_content(json.dumps({"result": "Error reading parameters."}).encode())
            return [flowfile]
        if not params:
            flowfile.set_content(json.dumps({"result": "No parameters stored."}).encode())
            return [flowfile]
        lines = [f"Parameters ({len(params)}):"]
        for k, v in sorted(params.items()):
            lines.append(f"- {k} = {v} â†’ ${{user.{k}}}")
        flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
        return [flowfile]

    if action == "list_params_secrets":
        conv_id = body.get("conversation_id", "")
        uid = user_id
        params_out = []
        secrets_out = []
        # Global params
        from core.expression import _load_global_parameters, _load_global_secrets
        for k, v in _load_global_parameters().items():
            params_out.append({"key": k, "value": str(v), "scope": "global"})
        # User params
        if uid and uid != "anonymous":
            from core.expression import _load_user_parameters, _load_user_secrets
            for k, v in _load_user_parameters(uid).items():
                params_out.append({"key": k, "value": str(v), "scope": "user"})
            # User secrets (names only)
            for k in _load_user_secrets(uid).keys():
                secrets_out.append({"key": k, "scope": "user"})
        # Global secrets (names only)
        for k in _load_global_secrets().keys():
            secrets_out.append({"key": k, "scope": "global"})
        # Conv params/secrets
        if conv_id:
            cp = store.get_extra(conv_id, "conv_parameters") or {}
            for k, v in cp.items():
                params_out.append({"key": k, "value": str(v), "scope": "conversation"})
            cs = store.get_extra(conv_id, "conv_secrets") or {}
            for k in cs.keys():
                secrets_out.append({"key": k, "scope": "conversation"})
        flowfile.set_content(json.dumps({
            "parameters": params_out, "secrets": secrets_out,
        }, ensure_ascii=False).encode())
        return [flowfile]

    if action == "set_param":
        key = body.get("key", "").strip()
        value = body.get("value", "")
        conv_id = body.get("conversation_id", "")
        try:
            scope = _requested_scope(body)
        except ValueError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "conversation_id is required for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if scope == "global" and not _is_admin(flowfile):
            return _scope_error(flowfile, "Cannot write global parameters from chat. Use the admin GUI.")
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        _write_param_value(scope, key, value, store, user_id, conv_id)
        flowfile.set_content(json.dumps({"ok": True, "scope": scope}).encode())
        return [flowfile]

    if action == "delete_param":
        key = body.get("key", "").strip()
        scope = _requested_scope(body)
        conv_id = body.get("conversation_id", "")
        if scope == "global" and not _is_admin(flowfile):
            return _scope_error(flowfile, "Cannot delete global parameters from chat. Use the admin GUI.")
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        _delete_param_value(scope, key, store, user_id, conv_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "set_secret":
        key = body.get("key", "").strip()
        value = body.get("value", "")
        conv_id = body.get("conversation_id", "")
        try:
            scope = _requested_scope(body)
        except ValueError as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if scope == "conversation" and not conv_id:
            flowfile.set_content(json.dumps({"error": "conversation_id is required for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if scope == "global" and not _is_admin(flowfile):
            return _scope_error(flowfile, "Cannot write global secrets from chat. Use the admin GUI.")
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        _write_secret_value(scope, key, value, store, user_id, conv_id)
        flowfile.set_content(json.dumps({"ok": True, "scope": scope}).encode())
        return [flowfile]

    if action == "delete_secret":
        key = body.get("key", "").strip()
        scope = _requested_scope(body)
        conv_id = body.get("conversation_id", "")
        if scope == "global" and not _is_admin(flowfile):
            return _scope_error(flowfile, "Cannot delete global secrets from chat. Use the admin GUI.")
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        _delete_secret_value(scope, key, store, user_id, conv_id)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action in {"move_param_scope", "move_secret_scope"}:
        key = body.get("key", "").strip()
        conv_id = body.get("conversation_id", "")
        from_scope = str(body.get("from_scope") or body.get("scope") or "").strip().lower()
        to_scope = str(body.get("to_scope") or body.get("target_scope") or "").strip().lower()
        if not key or from_scope not in {"conversation", "user", "global"} or to_scope not in {"conversation", "user", "global"}:
            flowfile.set_content(json.dumps({"error": "Missing key, from_scope, or to_scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if "global" in {from_scope, to_scope} and not _is_admin(flowfile):
            return _scope_error(flowfile, "Requires admin role for global scope")
        if "conversation" in {from_scope, to_scope} and not conv_id:
            flowfile.set_content(json.dumps({"error": "conversation_id is required for conversation scope"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        if from_scope == to_scope:
            flowfile.set_content(json.dumps({"ok": True, "unchanged": True, "scope": to_scope}).encode())
            return [flowfile]
        try:
            if action == "move_secret_scope":
                value = _load_secret_value(from_scope, key, store, user_id, conv_id)
                if value is None:
                    flowfile.set_content(json.dumps({"error": f"Secret '{key}' not found in {from_scope}"}).encode())
                    flowfile.set_attribute("http.response.status", "404")
                    return [flowfile]
                _write_secret_value(to_scope, key, value, store, user_id, conv_id)
                _delete_secret_value(from_scope, key, store, user_id, conv_id)
            else:
                value = _load_param_value(from_scope, key, store, user_id, conv_id)
                if value is None:
                    flowfile.set_content(json.dumps({"error": f"Variable '{key}' not found in {from_scope}"}).encode())
                    flowfile.set_attribute("http.response.status", "404")
                    return [flowfile]
                _write_param_value(to_scope, key, value, store, user_id, conv_id)
                _delete_param_value(from_scope, key, store, user_id, conv_id)
            flowfile.set_content(json.dumps({"ok": True, "key": key, "from_scope": from_scope, "scope": to_scope}).encode())
        except Exception as e:
            flowfile.set_content(json.dumps({"error": str(e)}).encode())
        return [flowfile]

    return None
