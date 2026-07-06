"""ToolRelayService MCP tool loading + filesystem resolvers + list/schema."""

import logging


logger = logging.getLogger(__name__)
# Split out of tool_relay_service.py for the <=800-line rule; composed back
# into ToolRelayService (invariant 2: MRO/shared class-state on the host).


class _ToolRelayToolsMixin:
    """MCP tool loading + filesystem resolvers + list/schema."""

    def _load_mcp_tools(self, registry, user_id: str, conversation_id: str,
                        agent_name: str = ""):
        """Load MCP server tools for the active agent into registry."""
        try:
            from core.resource_store import ResourceStore
            rs = ResourceStore.instance()

            # All MCP servers accessible in scope (global + user + conversation)
            # are auto-active — no per-conversation linking required.
            _all_mcps = rs.list_all("mcp", user_id, conversation_id=conversation_id) or []
            active_mcps = [m.get("name", "") for m in _all_mcps if m.get("name")]
            if not active_mcps:
                return

            for mcp_name in active_mcps:
                try:
                    from core.tool_mcp_filters import is_enabled
                    if not is_enabled(conversation_id, mcp_name, agent_name, kind="mcps"):
                        continue
                    raw_def = rs.get_any("mcp", mcp_name, user_id,
                                         conversation_id=conversation_id)
                    if not raw_def:
                        continue
                    from core.expression import resolve_value
                    mcp_def = resolve_value(raw_def, owner=user_id,
                                             conversation_id=conversation_id)
                    transport = mcp_def.get("transport", "http")
                    via = mcp_def.get("via", "") or (
                        "relay" if transport == "stdio" else "direct")
                    auth = mcp_def.get("auth", {})
                    if isinstance(auth, str):
                        auth = {"Authorization": auth}

                    disc_tools = []
                    relay_svc = None

                    if via == "relay":
                        _rsid = mcp_def.get("relay_service", "")
                        if _rsid:
                            try:
                                from core.service_registry import ServiceRegistry
                                relay_svc = ServiceRegistry.get_instance().resolve(
                                    _rsid, user_id=user_id, conv_id=conversation_id)
                            except Exception:
                                logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        if not relay_svc:
                            relay_svc = self._find_filesystem_service(
                                user_id, conversation_id, agent_name)
                        if not relay_svc:
                            logger.warning("[tool-relay][mcp] No relay for '%s'", mcp_name)
                            continue
                        if transport == "stdio":
                            try:
                                relay_svc._request("mcp_start", ".", **{
                                    "server_id": mcp_name,
                                    "command": mcp_def.get("command", ""),
                                    "args": mcp_def.get("args", []),
                                    "env": mcp_def.get("env", {}),
                                    "local": bool(mcp_def.get("local")),
                                })
                            except Exception as e:
                                if "already_running" not in str(e):
                                    logger.error("[tool-relay][mcp] Start failed '%s': %s",
                                                 mcp_name, e)
                                    continue
                        try:
                            disc = relay_svc._request("mcp_discover", ".",
                                                      server_id=mcp_name,
                                                      local=bool(mcp_def.get("local")))
                            disc_tools = (disc.get("tools", [])
                                          if isinstance(disc, dict) else [])
                        except Exception as e:
                            logger.error("[tool-relay][mcp] Discovery failed '%s': %s",
                                         mcp_name, e)
                    else:
                        url = mcp_def.get("url", "")
                        if not url:
                            continue
                        try:
                            from core.relay_proxy_url import maybe_transform_relay_proxy_url
                            url = maybe_transform_relay_proxy_url(
                                url, user_id=user_id, conv_id=conversation_id) or url
                        except Exception:
                            logger.debug("mcp relay-proxy URL transform failed", exc_info=True)
                        from core.tool_registry import discover_mcp_tools
                        disc_tools = discover_mcp_tools(
                            url, headers=auth, timeout=10)

                    from core.handlers.agent_tools import MCPToolHandler
                    for mt in disc_tools:
                        h = MCPToolHandler(
                            tool_name=mt["name"],
                            tool_description=mt.get("description", ""),
                            tool_parameters=mt.get("inputSchema", {
                                "type": "object", "properties": {}}),
                            server_url=url if via != "relay" else mcp_def.get("url", ""),
                            mcp_tool_name=mt["name"],
                            headers=auth,
                            transport=transport if via == "relay" else "http",
                            server_id=mcp_name,
                            relay_service=relay_svc,
                            local=bool(mcp_def.get("local")),
                            raw_url=mcp_def.get("url", ""),
                            user_id=user_id,
                            conversation_id=conversation_id,
                        )
                        registry.register(h)
                    if disc_tools:
                        logger.info("[tool-relay][mcp] Loaded %d tools from '%s' (%s/%s)",
                                    len(disc_tools), mcp_name, via, transport)
                except Exception as e:
                    logger.warning("[tool-relay][mcp] Failed to load '%s': %s", mcp_name, e)
        except Exception as e:
            logger.warning("[tool-relay] Failed to load MCP tools: %s", e)

    @staticmethod
    def _list_available_filesystem_services(user_id: str = "", conversation_id: str = "",
                                            agent_name: str = "",
                                            fs_types=("relay", "filesystem", "googleDrive", "oneDrive")):
        """List filesystem services explicitly linked to this conversation."""
        available = []
        seen = set()
        default_id = ""
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            if conversation_id:
                try:
                    from core.relay_bindings import get_default, get_linked
                    default_id = get_default(conversation_id, agent_name) or ""
                    for sid in get_linked(conversation_id, agent_name):
                        if sid in seen:
                            continue
                        sdef = reg.resolve_definition(sid, user_id=user_id, conv_id=conversation_id)
                        if not sdef or sdef.service_type not in ("relay", "filesystem"):
                            continue
                        seen.add(sid)
                        connected = True
                        try:
                            connected = reg.is_connected(sdef.scope, sdef.scope_id, sid)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        svc = None
                        try:
                            svc = reg.get_live_instance_cached(sdef.scope, sdef.scope_id, sid)
                        except Exception:
                            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                        available.append({
                            "id": sid,
                            "type": sdef.service_type,
                            "scope": sdef.scope,
                            "root": getattr(svc, "root_path", "?") if svc else "?",
                            "connected": connected,
                            "default": sid == default_id,
                        })
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                try:
                    from core.remote_fs_bindings import list_tool_filesystems
                    for item in list_tool_filesystems(user_id, conversation_id):
                        sid = item.get("id", "")
                        if not sid or sid in seen:
                            continue
                        seen.add(sid)
                        available.append(item)
                except Exception:
                    logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
                return _ToolRelayToolsMixin._default_first_available(available, default_id)

            for fs_type in fs_types:
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id):
                    if sdef.service_id in seen:
                        continue
                    svc = reg.resolve(sdef.service_id, user_id=user_id)
                    if svc:
                        seen.add(sdef.service_id)
                        available.append({
                            "id": sdef.service_id, "type": sdef.service_type,
                            "scope": sdef.scope,
                            "root": getattr(svc, "root_path", "?"),
                        })
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return available

    @staticmethod
    def _default_first_available(available, default_id: str = ""):
        if not default_id:
            return available or []
        return sorted(
            available or [],
            key=lambda item: 0 if item.get("id") == default_id else 1)

    @staticmethod
    def _default_filesystem_id(available, conversation_id: str = "",
                               agent_name: str = "") -> str:
        try:
            from core.relay_bindings import get_default
            default_id = get_default(conversation_id, agent_name) or ""
        except Exception:
            default_id = ""
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        ids = [item.get("id", "") for item in available or [] if item.get("id")]
        if default_id and default_id in ids:
            return default_id
        if not default_id and len(ids) == 1:
            return ids[0]
        return ""

    @staticmethod
    def _filesystem_service_from_available(available, user_id: str = "",
                                           conversation_id: str = "",
                                           agent_name: str = ""):
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            if conversation_id and available:
                default_id = _ToolRelayToolsMixin._default_filesystem_id(
                    available, conversation_id, agent_name)
                if default_id:
                    svc = reg.resolve(default_id, user_id=user_id,
                                      conv_id=conversation_id)
                    return svc
            for item in available or []:
                svc = reg.resolve(
                    item.get("id", ""), user_id=user_id,
                    conv_id=conversation_id)
                if svc:
                    return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    @staticmethod
    def _make_filesystem_resolver(user_id: str = "", conversation_id: str = "",
                                  agent_name: str = "", default_service=None):
        def resolver(service_id: str = "", *_args):
            try:
                from core.service_registry import ServiceRegistry
                reg = ServiceRegistry.get_instance()
                available = _ToolRelayToolsMixin._list_available_filesystem_services(
                    user_id, conversation_id, agent_name)
                allowed = [item.get("id", "") for item in available if item.get("id")]
                if service_id in ("", "workspace", "ws", "local") and default_service:
                    return default_service
                if conversation_id:
                    if service_id in ("", "workspace", "ws", "local"):
                        service_id = _ToolRelayToolsMixin._default_filesystem_id(
                            available, conversation_id, agent_name)
                    if not service_id or service_id not in allowed:
                        return None
                return reg.resolve(service_id, user_id=user_id, conv_id=conversation_id)
            except Exception:
                return None
        return resolver

    @staticmethod
    def _find_filesystem_service(user_id: str = "", conversation_id: str = "",
                                 agent_name: str = ""):
        """Find the first live filesystem service for this user.

        Same logic as agent_utils._find_filesystem_service but standalone.
        """
        try:
            from core.service_registry import ServiceRegistry
            reg = ServiceRegistry.get_instance()
            available = _ToolRelayToolsMixin._list_available_filesystem_services(
                user_id, conversation_id, agent_name)
            if conversation_id and available:
                default_id = _ToolRelayToolsMixin._default_filesystem_id(
                    available, conversation_id, agent_name)
                if default_id:
                    svc = reg.resolve(default_id, user_id=user_id,
                                      conv_id=conversation_id)
                    return svc
            if available:
                for item in available:
                    svc = reg.resolve(
                        item.get("id", ""), user_id=user_id, conv_id=conversation_id)
                    if svc:
                        return svc
            if conversation_id:
                return None
            for fs_type in ("relay", "filesystem", "googleDrive", "oneDrive"):
                for sdef in reg.resolve_by_type(fs_type, user_id=user_id):
                    svc = reg.resolve(sdef.service_id, user_id=user_id)
                    if svc:
                        return svc
        except Exception:
            logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
        return None

    def _handle_list_tools(self, request_id: str,
                           user_id: str, conversation_id: str) -> dict:
        registry = self._get_registry(user_id, conversation_id)
        tools = []
        for h in registry.list_tools():
            tools.append({
                "name": h.name,
                "display_name": h.display_name,
                "description": (h.description or "")[:150],
            })
        return {"type": "result", "request_id": request_id, "data": tools}

    def _handle_get_schema(self, request_id: str, tool_name: str,
                           user_id: str = "",
                           conversation_id: str = "") -> dict:
        registry = self._get_registry(user_id, conversation_id)
        handler = registry.get(tool_name)
        if not handler:
            available = [h.name for h in registry.list_tools()]
            return {"type": "error", "request_id": request_id,
                    "error": f"Unknown tool '{tool_name}'. Available: {', '.join(available)}"}
        try:
            from core.handlers.meta_tools import _schema_with_local
            schema = _schema_with_local(handler)
        except Exception:
            schema = handler.parameters_schema
        return {"type": "result", "request_id": request_id, "data": {
            "name": handler.name,
            "description": handler.description,
            "parameters": schema,
        }}
