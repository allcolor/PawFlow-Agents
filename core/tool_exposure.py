"""How a tool registry is advertised to a consumer.

One vocabulary, two consumers. MCP publications already had these four modes
(``core/mcp_server_store.py``); agents gained them later. Both import from
here so the two surfaces cannot drift into meaning different things under the
same name.

The mode decides *how* tools are advertised, never *which* tools exist. The
candidate set is settled before this module sees it -- for an agent by
``core/tool_mcp_filters.py`` (conversation ``disabled_tools`` plus a per-agent
custom selection), for a publication by its ``tool_allowlist``. This module
then filters that set for read-only and picks the advertising shape.
"""

from __future__ import annotations

from typing import Iterable

#: - ``api``            the meta tools only; every tool reached via use_tool.
#: - ``full``           every tool declared directly, with real annotations.
#: - ``api_readonly``   meta tools, restricted to read-only tools.
#: - ``full_readonly``  direct declarations, restricted to read-only tools.
MODES = frozenset({"api", "full", "api_readonly", "full_readonly"})

DEFAULT_MODE = "api"


def normalize_mode(value: object) -> str:
    """Return a mode from MODES, falling back to the default.

    An unknown value resolves to the narrowest surface rather than raising: a
    typo in stored config must never widen what a consumer can reach.
    """
    mode = str(value or "").strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def is_full_mode(value: object) -> bool:
    return normalize_mode(value).startswith("full")


def is_readonly_mode(value: object) -> bool:
    return normalize_mode(value).endswith("_readonly")


def resolve_mode(agent_value: object, service_value: object) -> str:
    """Agent override first, then the service default, then ``api``.

    An override REPLACES the service value; the two levels are never merged.
    Merging would make the effective surface impossible to read off either
    screen.
    """
    agent_mode = str(agent_value or "").strip().lower()
    if agent_mode in MODES:
        return agent_mode
    return normalize_mode(service_value)


def is_read_only_tool(tool_name: str) -> bool:
    """The single definition of read-only, shared with MCP publications."""
    from core.tool_approval import ToolApprovalGate
    return ToolApprovalGate.is_read_only_allowed(tool_name)


def filter_read_only(names: Iterable[str], mode: object) -> list[str]:
    """Drop write tools when the mode is read-only, preserving order."""
    ordered = [str(name) for name in names if name]
    if not is_readonly_mode(mode):
        return ordered
    return [name for name in ordered if is_read_only_tool(name)]
