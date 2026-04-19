"""Conversation resource linking.

Only relays are conversation-linkable. Every other resource type (agents,
mcps, skills, tasks, services, tools, flows) is auto-available when
accessible in scope (global + user + conversation). This module is a thin
dispatch shim over ``core.relay_bindings``.
"""

from typing import List

LINKABLE_TYPES = {"relays"}


def _assert_relays(rtype: str) -> None:
    if rtype != "relays":
        raise ValueError(
            f"Only 'relays' can be linked to a conversation (got '{rtype}'). "
            "Other resource types are auto-available in scope."
        )


def get_linked(conv_id: str, rtype: str) -> List[str]:
    """Get list of linked resource names for a type."""
    _assert_relays(rtype)
    from core.relay_bindings import get_linked as _rl
    return _rl(conv_id)


def link(conv_id: str, rtype: str, name: str) -> List[str]:
    """Link a resource to a conversation. Returns updated list."""
    _assert_relays(rtype)
    from core.relay_bindings import get_linked as _rl, link_relay
    link_relay(conv_id, name)
    return _rl(conv_id)


def unlink(conv_id: str, rtype: str, name: str) -> List[str]:
    """Unlink a resource from a conversation. Returns updated list."""
    _assert_relays(rtype)
    from core.relay_bindings import get_linked as _rl, unlink_relay
    unlink_relay(conv_id, name)
    return _rl(conv_id)


def is_linked(conv_id: str, rtype: str, name: str) -> bool:
    """Check if a resource is linked to a conversation."""
    return name in get_linked(conv_id, rtype)
