"""AgentLoopTask action dispatcher — PFP UI extension handlers.

UI extensions in the browser call `pfp.call(action, body)` which posts
`{action: "<ext.action>", _ext: "<package_id>", ...}` to `/api/ui`. The
dispatcher below recognizes any body carrying `_ext`, resolves the
matching installed handler, and runs it through the existing relay
subprocess sandbox via `core.pfp_runtime.invoke_ui_handler`.

All trust boundary guarantees of the PFP runtime apply here:
  - the entrypoint hash is verified against the signed install record
    on every call;
  - the relay child runs with the relay token / runner flag scrubbed from
    its env, so the handler can only re-enter PawFlow through brokered
    `pfp.call_tool` / `pfp.call_service` envelopes;
  - `PackageCapabilityBroker` re-authorizes every host call against the
    `allowed_tools` / `allowed_services` grants declared at install time.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from core import FlowFile

logger = logging.getLogger(__name__)


def _handle_pfp_ui(self, action: str, body: Dict[str, Any], store, user_id: str,
                   flowfile: FlowFile) -> Optional[List[FlowFile]]:
    """Run a UI extension handler when the action body carries `_ext`."""
    package_id = str((body or {}).get("_ext") or "").strip()
    if not package_id:
        return None
    if not user_id:
        flowfile.set_content(json.dumps({
            "error": "authentication required for PFP UI handlers",
            "_ext": package_id,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "401")
        return [flowfile]

    conversation_id = str(body.get("conversation_id") or "").strip()
    arguments = {
        k: v for k, v in (body or {}).items()
        if k not in {
            "action", "_ext", "_call_id", "_reply_conversation_id",
            "conversation_id", "_callId",
        }
    }

    # Kill switch + per-conv toggle. Both must succeed for the handler to
    # run, even with valid auth and a healthy install record.
    from core.tool_mcp_filters import (
        _ui_extensions_globally_disabled, is_extension_enabled,
    )
    if _ui_extensions_globally_disabled():
        flowfile.set_content(json.dumps({
            "error": "UI extensions are disabled on this server",
            "_ext": package_id,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "503")
        return [flowfile]
    if conversation_id and not is_extension_enabled(conversation_id, package_id):
        flowfile.set_content(json.dumps({
            "error": "extension disabled for this conversation",
            "_ext": package_id,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "403")
        return [flowfile]

    from core import pfp_package, pfp_runtime

    scope = "conversation" if conversation_id else "user"
    resolved = pfp_package.resolve_ui_handler(
        package_id, action,
        user_id=user_id, conversation_id=conversation_id, scope=scope)
    if resolved is None:
        flowfile.set_content(json.dumps({
            "error": f"PFP UI handler not found: {package_id}/{action}",
            "_ext": package_id, "action": action,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "404")
        return [flowfile]

    agent_name = str(body.get("agent_name") or "").strip()
    context = {
        "user_id": user_id,
        "conversation_id": conversation_id,
        "scope": resolved.get("scope") or scope,
        "agent_name": agent_name,
    }
    try:
        result = pfp_runtime.invoke_ui_handler(
            resolved["package_runtime"],
            resolved["installed_from"],
            action, arguments, context,
        )
    except pfp_runtime.PackageRuntimeError as exc:
        logger.warning("PFP UI handler %s/%s failed: %s", package_id, action, exc)
        flowfile.set_content(json.dumps({
            "error": str(exc), "_ext": package_id, "action": action,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "502")
        return [flowfile]
    except Exception as exc:
        logger.exception("PFP UI handler %s/%s crashed", package_id, action)
        flowfile.set_content(json.dumps({
            "error": f"PFP UI handler crashed: {exc}",
            "_ext": package_id, "action": action,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "500")
        return [flowfile]

    payload: Dict[str, Any] = {
        "action": action,
        "_ext": package_id,
        "result": result,
    }
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    flowfile.set_attribute("http.response.status", "200")
    flowfile.set_attribute("http.response.header.Content-Type", "application/json")
    return [flowfile]
