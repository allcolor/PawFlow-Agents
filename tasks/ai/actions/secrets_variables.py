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


def _handle_secrets_variables(self, action, body, store, user_id, flowfile):
    """Handle secrets variables actions. Returns [flowfile] or None."""


    if action == "add_secret":
        key = body.get("key", "").strip()
        value = body.get("value", "")
        if not key or not value:
            flowfile.set_content(json.dumps({"error": "key and value are required"}).encode())
            flowfile.set_attribute("http.response.status", "400")
            return [flowfile]
        uid = user_id or "anonymous"
        from pathlib import Path
        from core.secrets import get_secrets_manager
        sm = get_secrets_manager()
        encrypted = sm.encrypt(value)
        secrets_path = Path("config/users") / uid / "secrets.json"
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets = {}
        if secrets_path.exists():
            try:
                secrets = json.loads(secrets_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        secrets[key] = encrypted
        secrets_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")
        flowfile.set_content(json.dumps({
            "result": f"Secret '{key}' stored. Use ${{secrets.user.{key}}} in flows.",
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
        uid = user_id or "anonymous"
        from pathlib import Path
        params_path = Path("config/users") / uid / "parameters.json"
        params_path.parent.mkdir(parents=True, exist_ok=True)
        params = {}
        if params_path.exists():
            try:
                params = json.loads(params_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        params[key] = value
        params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
        flowfile.set_content(json.dumps({
            "result": f"Parameter '{key}' stored. Use ${{user.{key}}} in flows.",
            "key": key,
        }).encode())
        return [flowfile]

    if action == "list_secrets":
        uid = user_id or "anonymous"
        from pathlib import Path
        secrets_path = Path("config/users") / uid / "secrets.json"
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
            lines.append(f"- {k} â†’ ${{secrets.user.{k}}}")
        flowfile.set_content(json.dumps({"result": "\n".join(lines)}).encode())
        return [flowfile]

    if action == "list_variables":
        uid = user_id or "anonymous"
        from pathlib import Path
        params_path = Path("config/users") / uid / "parameters.json"
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
        uid = user_id or "anonymous"
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
        scope = body.get("scope", "user")
        conv_id = body.get("conversation_id", "")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot write global parameters from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        if scope == "conversation" and conv_id:
            cp = store.get_extra(conv_id, "conv_parameters") or {}
            cp[key] = value
            store.set_extra(conv_id, "conv_parameters", cp)
        else:  # user
            uid = user_id or "anonymous"
            from core.config_store import ConfigStore
            from pathlib import Path as _CfgPath
            path = _CfgPath(f"config/users/{uid}/parameters.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ConfigStore.load_params(path)
            data[key] = value
            ConfigStore.save_params(path, data)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "delete_param":
        key = body.get("key", "").strip()
        scope = body.get("scope", "user")
        conv_id = body.get("conversation_id", "")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot delete global parameters from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        if scope == "conversation" and conv_id:
            cp = store.get_extra(conv_id, "conv_parameters") or {}
            cp.pop(key, None)
            store.set_extra(conv_id, "conv_parameters", cp)
        else:  # user
            uid = user_id or "anonymous"
            from core.config_store import ConfigStore
            from pathlib import Path as _CfgPath
            path = _CfgPath(f"config/users/{uid}/parameters.json")
            data = ConfigStore.load_params(path)
            data.pop(key, None)
            ConfigStore.save_params(path, data)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "set_secret":
        key = body.get("key", "").strip()
        value = body.get("value", "")
        scope = body.get("scope", "user")
        conv_id = body.get("conversation_id", "")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot write global secrets from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        from core.secrets import SecretsManager
        sm = SecretsManager.get_instance()
        if scope == "conversation" and conv_id:
            cs = store.get_extra(conv_id, "conv_secrets") or {}
            cs[key] = sm.encrypt(value)
            store.set_extra(conv_id, "conv_secrets", cs)
        else:  # user
            uid = user_id or "anonymous"
            from core.config_store import ConfigStore
            from pathlib import Path as _CfgPath
            path = _CfgPath(f"config/users/{uid}/secrets.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            data = ConfigStore.load_secrets(path)
            data[key] = value  # ConfigStore.save_secrets encrypts
            ConfigStore.save_secrets(path, data)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    if action == "delete_secret":
        key = body.get("key", "").strip()
        scope = body.get("scope", "user")
        conv_id = body.get("conversation_id", "")
        if scope == "global":
            flowfile.set_content(json.dumps({"error": "Cannot delete global secrets from chat. Use the admin GUI."}).encode())
            flowfile.set_attribute("http.response.status", "403")
            return [flowfile]
        if not key:
            flowfile.set_content(json.dumps({"error": "Missing key"}).encode())
            return [flowfile]
        if scope == "conversation" and conv_id:
            cs = store.get_extra(conv_id, "conv_secrets") or {}
            cs.pop(key, None)
            store.set_extra(conv_id, "conv_secrets", cs)
        else:  # user
            uid = user_id or "anonymous"
            from core.config_store import ConfigStore
            from pathlib import Path as _CfgPath
            path = _CfgPath(f"config/users/{uid}/secrets.json")
            data = ConfigStore.load_secrets(path)
            data.pop(key, None)
            ConfigStore.save_secrets(path, data)
        flowfile.set_content(json.dumps({"ok": True}).encode())
        return [flowfile]

    return None
