"""Conversation publication as an inbound MCP server."""

import json

from tasks.ai.actions._agentres_base import _UNHANDLED


_ACTIONS = {
    "mcp_server_get",
    "mcp_server_configure",
    "mcp_server_create_key",
    "mcp_server_revoke_key",
    "mcp_server_disconnect_client",
    "mcp_server_set_enabled",
    "mcp_server_delete",
}


def _reply(flowfile, payload, status=200):
    flowfile.set_content(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if status != 200:
        flowfile.set_attribute("http.response.status", str(status))
    return [flowfile]


def _owner_only(store, conversation_id, user_id, flowfile):
    owner = store.resolve_owner(conversation_id) if conversation_id else ""
    if not owner:
        return "", _reply(flowfile, {"error": "Conversation not found"}, 404)
    if owner != user_id:
        return "", _reply(
            flowfile,
            {"error": "Only the conversation owner can publish it as an MCP server"},
            403,
        )
    return owner, None


def _public_server(server, keys=None):
    if not server:
        return None
    result = dict(server)
    result["keys"] = list(keys or [])
    return result


def _handle_agentres_k6(self, action, body, store, user_id, flowfile):
    if action not in _ACTIONS:
        return _UNHANDLED
    conversation_id = str(body.get("conversation_id") or "").strip()
    if not conversation_id:
        return _reply(flowfile, {"error": "conversation_id is required"}, 400)
    owner, denied = _owner_only(store, conversation_id, user_id, flowfile)
    if denied is not None:
        return denied

    from core.mcp_server_store import MCPServerStore
    mcp_store = MCPServerStore.instance()
    server = mcp_store.get_for_conversation(conversation_id)

    if action == "mcp_server_get":
        return _reply(flowfile, {
            "server": _public_server(
                server, mcp_store.list_keys(server["server_id"]) if server else [])
        })

    if action == "mcp_server_configure":
        agent_name = str(body.get("agent_name") or "").strip()
        enabled = bool(body.get("enabled", True))
        from core.conv_agent_config import get_all_agent_configs
        configs = get_all_agent_configs(conversation_id) or {}
        needle = agent_name.lower()
        canonical = next(
            (name for name in configs if isinstance(name, str) and name.lower() == needle),
            "",
        )
        if not canonical:
            return _reply(flowfile, {
                "error": "agent_name must identify an agent attached to this conversation"
            }, 400)
        if server and (not enabled or server["agent_name"].lower() != canonical.lower()):
            from services.mcp_server_endpoint import remove_mcp_relay
            remove_mcp_relay(server)
            active_client_id = str(server.get("active_client_id") or "")
            if active_client_id:
                mcp_store.release_client(server["server_id"], active_client_id)
        configured = mcp_store.configure(
            owner, conversation_id, canonical,
            label=str(body.get("label") or canonical).strip(),
            enabled=enabled,
        )
        from services.mcp_server_endpoint import ensure_mcp_routes
        ensure_mcp_routes()
        return _reply(flowfile, {
            "server": _public_server(
                configured, mcp_store.list_keys(configured["server_id"]))
        })

    if not server:
        return _reply(flowfile, {"error": "Conversation is not published as MCP"}, 404)
    server_id = server["server_id"]

    if action == "mcp_server_create_key":
        raw, key = mcp_store.create_key(
            server_id, str(body.get("label") or "CLI key").strip())
        return _reply(flowfile, {
            "api_key": raw,
            "key": key,
            "warning": "This API key is shown only once.",
        })
    if action == "mcp_server_revoke_key":
        key_id = str(body.get("key_id") or "").strip()
        return _reply(flowfile, {"revoked": mcp_store.revoke_key(server_id, key_id)})
    if action == "mcp_server_disconnect_client":
        active_client_id = str(server.get("active_client_id") or "")
        from services.mcp_server_endpoint import remove_mcp_relay
        remove_mcp_relay(server)
        released = bool(
            active_client_id
            and mcp_store.release_client(server_id, active_client_id)
        )
        return _reply(flowfile, {"disconnected": released})
    if action == "mcp_server_set_enabled":
        enabled = bool(body.get("enabled"))
        if not enabled:
            from services.mcp_server_endpoint import remove_mcp_relay
            remove_mcp_relay(server)
            mcp_store.release_client(
                server_id, str(server.get("active_client_id") or ""))
        mcp_store.set_enabled(server_id, enabled)
        return _reply(flowfile, {"enabled": enabled})
    if action == "mcp_server_delete":
        from services.mcp_server_endpoint import remove_mcp_relay
        remove_mcp_relay(server)
        return _reply(flowfile, {"deleted": mcp_store.delete(server_id)})
    return _UNHANDLED
