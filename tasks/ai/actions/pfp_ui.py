"""AgentLoopTask action dispatcher — PFP UI extension handlers.

UI extensions in the browser call `pfp.call(action, body)` which posts
`{action: "<ext.action>", _ext: "<package_id>", ...}` to `/api/ui`. The
dispatcher below recognizes any body carrying `_ext`, resolves the
matching installed handler, and runs it through the existing relay
subprocess sandbox via `core.pfp_runtime.invoke_ui_handler`.

Trust domain shared by all installed UI extensions
--------------------------------------------------
UI extensions run as plain JavaScript in the user's tab, same origin as
PawFlow itself. Any installed extension can read every other extension's
DOM, redefine `window.pawflow`, intercept the SSE bus, and call
`fetch('/api/ui', { body: '{"_ext": "victim.pkg", ...}' })` directly.
The `_ext` field on the request body is therefore **self-declared by the
caller**: it identifies which installed package's handler to run, not
which extension initiated the call.

The practical consequence is that two installed extensions from
different vendors share the same browser trust domain. A malicious A
can invoke B's handlers with B's `allowed_tools` grants. This is the
same trust model as Chrome extensions executing in a page, or VS Code
extensions in a workspace: install consent is the gate, not runtime
isolation. The kill switch and per-conversation toggle let a user
contain a misbehaving extension without uninstalling.

Within that domain, the PFP runtime keeps its full set of guarantees:
  - the entrypoint hash is verified against the signed install record
    on every call;
  - the relay child runs with the relay token / runner flag scrubbed from
    its env, so the handler can only re-enter PawFlow through brokered
    `pfp.call_tool` / `pfp.call_service` envelopes;
  - `PackageCapabilityBroker` re-authorizes every host call against the
    `allowed_tools` / `allowed_services` grants declared at install time;
  - the `_ext` field is recorded in the audit log of every invocation so
    cross-package calls are visible to a human auditor.

Future work: a true per-extension isolation would require sandboxed
iframes with a postMessage broker. The current architecture is the
right base layer for that change, but it does not provide it today.
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

    requested_scope = str(body.get("scope") or "user").strip().lower()
    if requested_scope in {"conv", "conversation"}:
        scope = "conversation"
    elif requested_scope == "global":
        scope = "global"
    else:
        scope = "user"
    if scope == "conversation" and not conversation_id:
        flowfile.set_content(json.dumps({
            "error": "conversation_id is required for conversation scope",
            "_ext": package_id, "action": action,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]
    if scope == "global":
        flowfile.set_content(json.dumps({
            "error": "PFP UI extensions do not support global scope",
            "_ext": package_id, "action": action,
        }).encode("utf-8"))
        flowfile.set_attribute("http.response.status", "400")
        return [flowfile]
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
    # Audit log: who called what, with which grants. The `_ext` is self-
    # declared by the browser caller so this log is the primary signal
    # for spotting cross-package abuse in a multi-extension install.
    logger.info(
        "PFP UI handler invoke: user=%s conv=%s _ext=%s action=%s grants=tools:%d/services:%d",
        user_id, conversation_id or "-", package_id, action,
        len(resolved["package_runtime"].get("allowed_tools") or []),
        len(resolved["package_runtime"].get("allowed_services") or []))
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
