"""Fast authenticated UI actions for the PFP semantic browser bridge."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core import FlowFile


_ACTIONS = {
    "pfp_semantic_tab_register",
    "pfp_semantic_tab_unregister",
    "pfp_semantic_result",
}


def _response(flowfile: FlowFile, payload: Dict[str, Any],
              status: int = 200) -> List[FlowFile]:
    flowfile.set_content(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    flowfile.set_attribute("http.response.status", str(status))
    flowfile.set_attribute(
        "http.response.header.Content-Type", "application/json")
    return [flowfile]


def _handle_pfp_semantic(
        self, action: str, body: Dict[str, Any], store, user_id: str,
        flowfile: FlowFile) -> Optional[List[FlowFile]]:
    """Register tabs and complete correlated semantic browser calls."""
    if action not in _ACTIONS:
        return None
    if not user_id:
        return _response(
            flowfile, {"error": "authentication required"}, 401)
    conversation_id = str(body.get("conversation_id") or "").strip()
    if not conversation_id:
        return _response(
            flowfile, {"error": "conversation_id is required"}, 400)

    from core.conversation_access import ConversationAccessError, require_read
    try:
        require_read(conversation_id, user_id)
    except ConversationAccessError:
        return _response(
            flowfile, {"error": "Conversation not found"}, 404)

    from core.semantic_browser_bridge import (
        SemanticBrowserBridge, SemanticBrowserError)
    bridge = SemanticBrowserBridge.instance()
    try:
        if action == "pfp_semantic_tab_register":
            requested = body.get("packages") or []
            if not isinstance(requested, list):
                raise SemanticBrowserError(
                    "semantic browser packages must be a list")
            from core.tool_mcp_filters import (
                _ui_extensions_globally_disabled, is_extension_enabled)
            if _ui_extensions_globally_disabled():
                raise SemanticBrowserError(
                    "UI extensions are disabled on this server")
            from core import pfp_package
            installed = pfp_package.list_installed_ui_extensions(
                user_id=user_id, conversation_id=conversation_id,
                scope="conversation")
            available = {
                str(row.get("package") or "") for row in installed
                if row.get("package")
                and is_extension_enabled(
                    conversation_id, str(row.get("package") or ""))
            }
            packages = sorted({
                str(package) for package in requested
                if str(package) in available
            })
            result = bridge.register_tab(
                user_id=user_id,
                conversation_id=conversation_id,
                tab_id=str(body.get("tab_id") or ""),
                bus_id=str(body.get("bus_id") or ""),
                packages=packages,
                active=bool(body.get("active")),
            )
            return _response(flowfile, result)
        if action == "pfp_semantic_tab_unregister":
            result = bridge.unregister_tab(
                user_id=user_id,
                conversation_id=conversation_id,
                tab_id=str(body.get("tab_id") or ""),
            )
            return _response(flowfile, {"ok": True, "removed": result})
        result = bridge.complete(
            user_id=user_id,
            conversation_id=conversation_id,
            tab_id=str(body.get("tab_id") or ""),
            request_id=str(body.get("request_id") or ""),
            result=body.get("result"),
            error=str(body.get("error") or ""),
        )
        return _response(flowfile, {"ok": result})
    except SemanticBrowserError as exc:
        return _response(flowfile, {"error": str(exc)}, 409)
