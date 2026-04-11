"""Conversation resource linking.

Resources from the repository must be linked to a conversation before use.
This module manages linked_* lists in ConversationStore extras.

Linkable types:
  - agents   → conv_agents config (handled by conv_agent_config.py)
  - skills   → linked_skills (list of names)
  - tasks    → linked_tasks (list of names)
  - mcps     → linked_mcps (list of names)
  - relays   → relay_bindings (delegates to relay_bindings.py)

NOT linkable (global/always available):
  - services, tools, flows
"""

import logging
from typing import List

logger = logging.getLogger(__name__)

# Extras keys for simple linkable types
_LINK_KEYS = {
    "skills": "linked_skills",
    "tasks": "linked_tasks",
    "mcps": "linked_mcps",
}

# Types with their own link implementation
_DELEGATED_TYPES = {"relays"}

LINKABLE_TYPES = set(_LINK_KEYS.keys()) | _DELEGATED_TYPES


def get_linked(conv_id: str, rtype: str) -> List[str]:
    """Get list of linked resource names for a type."""
    if rtype == "relays":
        from core.relay_bindings import get_linked as _rl
        return _rl(conv_id)
    key = _LINK_KEYS.get(rtype)
    if not key:
        return []
    from core.conversation_store import ConversationStore
    return ConversationStore.instance().get_extra(conv_id, key) or []


def link(conv_id: str, rtype: str, name: str) -> List[str]:
    """Link a resource to a conversation. Returns updated list."""
    if rtype == "relays":
        from core.relay_bindings import link_relay
        link_relay(conv_id, name)
        from core.relay_bindings import get_linked as _rl
        return _rl(conv_id)
    key = _LINK_KEYS.get(rtype)
    if not key:
        raise ValueError(f"Cannot link type '{rtype}'")
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    linked = store.get_extra(conv_id, key) or []
    if name not in linked:
        linked.append(name)
        store.set_extra(conv_id, key, linked)
    return linked


def unlink(conv_id: str, rtype: str, name: str) -> List[str]:
    """Unlink a resource from a conversation. Returns updated list."""
    if rtype == "relays":
        from core.relay_bindings import unlink_relay
        unlink_relay(conv_id, name)
        from core.relay_bindings import get_linked as _rl
        return _rl(conv_id)
    key = _LINK_KEYS.get(rtype)
    if not key:
        raise ValueError(f"Cannot unlink type '{rtype}'")
    from core.conversation_store import ConversationStore
    store = ConversationStore.instance()
    linked = store.get_extra(conv_id, key) or []
    if name in linked:
        linked.remove(name)
        store.set_extra(conv_id, key, linked)
    return linked


def is_linked(conv_id: str, rtype: str, name: str) -> bool:
    """Check if a resource is linked to a conversation."""
    return name in get_linked(conv_id, rtype)
