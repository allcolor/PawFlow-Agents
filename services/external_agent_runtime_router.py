"""Protocol-neutral routing for externally operated conversation agents."""

def route_external_agent_prompt(conversation_id: str, agent_name: str,
                                content: str, message_id: str, *, channel: str,
                                attachments=None) -> tuple[str, bool | None]:
    from core.conv_agent_config import get_agent_config
    config = get_agent_config(conversation_id, agent_name)
    kind = str(config.get("runtime_kind") or "llm")
    if kind == "external_agui":
        from core.agui_client_runtime import submit
        return kind, submit(
            conversation_id, agent_name, message_id, content, config,
            attachments=attachments)
    # The publication store remains authoritative for the older MCP runtime.
    # Some existing conversations and injected test/runtime paths predate the
    # runtime_kind field but still have a valid published terminal.
    from services.mcp_terminal_router import route_published_terminal_prompt
    routed = route_published_terminal_prompt(
        conversation_id, agent_name, content, message_id,
        channel=channel, attachments=attachments)
    if routed is not None:
        return "external_mcp", routed
    return kind, None


__all__ = ["route_external_agent_prompt"]
