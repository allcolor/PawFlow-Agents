"""Load dynamic tools from ResourceStore (type 'tool') into a ToolRegistry.

Replaces the old DynamicToolStore + load_dynamic_tools(cid) split with a
single scope-aware loader. Tri-scoped (global/user/conv) like every other
resource type. Skipped for builtins (already in registry).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def load_tools_into_registry(registry, user_id: str,
                              conversation_id: str = "") -> int:
    """Register every dynamic tool visible to (user_id, conversation_id).

    Order: global first, then user-scoped, then conv-scoped — same merge
    semantics as ResourceStore.list_all (later scope wins on name collision
    with builtins; we never override builtins).

    Each registered handler carries:
      - h._is_dynamic    = True
      - h._origin        = "dynamic"
      - h._origin_scope  = "global" | "user" | "conversation"

    Returns the number of handlers registered.
    """
    if not user_id:
        return 0
    try:
        from core.resource_store import ResourceStore
        from core.handlers.dynamic_tool import DynamicToolHandler, PfpToolProxyHandler
        rs = ResourceStore.instance()
        entries = rs.list_all("tool", user_id,
                                conversation_id=conversation_id) or []
    except Exception as e:
        logger.warning("[tool-loader] list_all failed: %s", e)
        return 0

    loaded = 0
    for entry in entries:
        name = entry.get("name", "")
        if not name:
            continue
        existing = registry.get(name)
        if existing and not getattr(existing, "_is_dynamic", False):
            continue
        try:
            if entry.get("package_runtime"):
                handler = PfpToolProxyHandler(
                    tool_name=name,
                    tool_description=entry.get("description", ""),
                    tool_parameters=entry.get("parameters", {}) or {},
                    package_runtime=entry.get("package_runtime", {}) or {},
                    installed_from=entry.get("installed_from", {}) or {},
                )
            else:
                handler = DynamicToolHandler(
                    tool_name=name,
                    tool_description=entry.get("description", ""),
                    tool_parameters=entry.get("parameters", {}) or {},
                    code=entry.get("source", "") or entry.get("code", ""),
                )
            handler._origin = "dynamic"
            handler._origin_scope = entry.get("_scope", "") or "user"
            registry.register(handler)
            loaded += 1
        except Exception as e:
            logger.warning("[tool-loader] register '%s' failed: %s", name, e)
    if loaded:
        logger.info("[tool-loader] loaded %d dynamic tool(s) for %s/%s",
                    loaded, user_id[:6] or "?",
                    conversation_id[:8] if conversation_id else "-")
    return loaded


def cleanup_conversation_tools(user_id: str, conversation_id: str) -> int:
    """Delete every conv-scoped tool for (user_id, conversation_id).

    Used when a conversation is purged. Returns the count deleted.
    """
    if not user_id or not conversation_id:
        return 0
    try:
        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()
        entries = repo.list("tools", "conv",
                            user_id=user_id, conv_id=conversation_id)
        deleted = 0
        for e in entries:
            n = e.get("name", "")
            if n and repo.delete("tools", n, "conv",
                                  user_id=user_id, conv_id=conversation_id):
                deleted += 1
        if deleted:
            logger.info("[tool-loader] cleanup deleted %d conv tool(s) "
                         "for conv %s", deleted, conversation_id[:8])
        return deleted
    except Exception as e:
        logger.warning("[tool-loader] cleanup failed: %s", e)
        return 0
