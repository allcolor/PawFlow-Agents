"""Build the tool surface handed to the model for one turn.

Split out of ``_agentctx_p3.py``, which is held to 800 lines. The mode decides
the SHAPE of the surface; which tools exist was already settled upstream by
``core/tool_mcp_filters.py``.
"""

import logging

from core._llm_types import LLMToolDefinition

logger = logging.getLogger(__name__)


def resolve_exposure(conversation_id: str, agent_name: str,
                     service_config: dict, is_cli_provider: bool) -> tuple:
    """Return ``(resolved_mode, agent_override)`` for this turn.

    The agent's own setting wins, then the llmConnection default, then ``api``
    -- the historical behaviour and still the default.
    """
    from core.tool_exposure import resolve_mode

    agent_override = ""
    try:
        from core.conv_agent_config import get_agent_config
        agent_override = (get_agent_config(
            conversation_id or "", agent_name or "") or {}).get(
                "tool_exposure", "")
    except Exception:
        logger.debug("Failed to read the agent tool_exposure override",
                     exc_info=True)
    mode = resolve_mode(agent_override, (service_config or {}).get(
        "tool_exposure"))
    # CLI providers do not reach their tools through tool_defs at all: they go
    # through the stdio MCP bridge, which advertises exactly get_tool_schema +
    # use_tool (tools/mcp_bridge.py). Honouring another mode there means
    # changing the bridge, so refuse it loudly rather than letting the setting
    # look applied while nothing changes.
    if is_cli_provider and mode != "api":
        logger.warning(
            "[context:%s] tool_exposure=%s ignored: CLI providers reach tools "
            "through the MCP bridge, which only advertises get_tool_schema + "
            "use_tool", (conversation_id or "")[:8], mode)
        mode = "api"
    return mode, agent_override


def build_tool_defs(registry, mode: str, meta_handlers: tuple) -> list:
    """Declare the tools for ``mode``.

    ``api`` / ``api_readonly`` declare the two meta tools and let UseToolHandler
    enforce read-only at execution time, exactly as the MCP gateway does.
    ``full`` / ``full_readonly`` declare every tool the agent may use, removing
    the discovery round trip at the cost of putting every schema in the prompt
    on every request -- which is why ``api`` remains the default.
    """
    from core.tool_exposure import (
        is_full_mode, is_read_only_tool, is_readonly_mode)

    schema_handler, use_handler = meta_handlers
    if not is_full_mode(mode):
        return [
            LLMToolDefinition(
                name=schema_handler.name,
                description=schema_handler.description,
                parameters=schema_handler.parameters_schema,
            ),
            LLMToolDefinition(
                name=use_handler.name,
                description=use_handler.description,
                parameters=use_handler.parameters_schema,
            ),
        ]
    meta_names = {schema_handler.name, use_handler.name}
    readonly = is_readonly_mode(mode)
    tool_defs = []
    for definition in registry.get_tool_definitions():
        name = str(definition.get("name") or "").strip()
        if not name or name in meta_names:
            continue
        if readonly and not is_read_only_tool(name):
            continue
        tool_defs.append(LLMToolDefinition(
            name=name,
            description=str(definition.get("description") or ""),
            parameters=definition.get("parameters") or {},
        ))
    return tool_defs
