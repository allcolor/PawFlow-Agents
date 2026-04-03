"""Relay Bindings — conversation-scoped relay management.

Relays must be explicitly linked to a conversation before agents or users
can use them. Each conversation can have a default relay.

Storage: conversation extras key 'relay_bindings'
  {"linked": ["relay_a", "relay_b"], "default": "relay_a"}
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EXTRA_KEY = "relay_bindings"


def _get_store():
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def get_bindings(cid: str) -> Dict[str, Any]:
    """Get relay bindings for a conversation."""
    store = _get_store()
    raw = store.get_extra_cached(cid, _EXTRA_KEY, default=None)
    if isinstance(raw, dict) and "linked" in raw:
        return raw
    return {"linked": [], "default": None}


def get_linked(cid: str) -> List[str]:
    """Get list of linked relay IDs for a conversation."""
    return get_bindings(cid).get("linked", [])


def get_default(cid: str) -> Optional[str]:
    """Get the default relay ID for a conversation (or None)."""
    return get_bindings(cid).get("default")


def link_relay(cid: str, relay_id: str) -> bool:
    """Link a relay to a conversation. Returns True if newly linked."""
    b = get_bindings(cid)
    if relay_id in b["linked"]:
        return False
    b["linked"].append(relay_id)
    # Auto-set default if first relay
    if b["default"] is None:
        b["default"] = relay_id
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    logger.info("Relay '%s' linked to conversation %s", relay_id, cid)
    return True


def unlink_relay(cid: str, relay_id: str) -> bool:
    """Unlink a relay from a conversation. Returns True if was linked."""
    b = get_bindings(cid)
    if relay_id not in b["linked"]:
        return False
    b["linked"].remove(relay_id)
    if b["default"] == relay_id:
        b["default"] = b["linked"][0] if b["linked"] else None
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    logger.info("Relay '%s' unlinked from conversation %s", relay_id, cid)
    return True


def set_default_relay(cid: str, relay_id: str) -> bool:
    """Set the default relay for a conversation. Must be linked first."""
    b = get_bindings(cid)
    if relay_id not in b["linked"]:
        return False
    b["default"] = relay_id
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    logger.info("Relay '%s' set as default for conversation %s", relay_id, cid)
    return True


def list_available_relays() -> List[Dict[str, Any]]:
    """List all connected relay services."""
    try:
        from gui.services.global_service_registry import GlobalServiceRegistry
        greg = GlobalServiceRegistry.get_instance()
        relays = []
        for sid, sdef in greg.get_all_definitions().items():
            if getattr(sdef, "service_type", "") == "filesystem":
                svc = greg.get_live_instance(sid)
                connected = svc is not None and getattr(svc, '_relay_connected', False)
                _ri = getattr(svc, '_relay_info', {}) or {} if svc else {}
                relays.append({
                    "id": sid,
                    "connected": connected,
                    "user_id": getattr(sdef, "user_id", ""),
                    "root": _ri.get("root", ""),
                    "host_root": _ri.get("host_root", ""),
                    "allow_local": bool(_ri.get('allow_local', False)),
                    "allow_local_screen": bool(_ri.get('allow_local_screen', False)),
                })
        return relays
    except Exception:
        return []


def resolve_relay(cid: str, relay_param: Optional[str] = None) -> Tuple[str, Any]:
    """Resolve a relay for a conversation.

    Args:
        cid: Conversation ID
        relay_param: Explicit relay ID (optional)

    Returns:
        (relay_id, relay_service) tuple

    Raises:
        ValueError: If no valid relay can be resolved
    """
    from gui.services.global_service_registry import GlobalServiceRegistry
    greg = GlobalServiceRegistry.get_instance()

    b = get_bindings(cid)
    linked = b["linked"]
    default = b["default"]

    if relay_param:
        if relay_param not in linked:
            raise ValueError(f"Relay '{relay_param}' is not linked to this conversation. "
                             f"Use /relay link {relay_param} first.")
        relay_id = relay_param
    elif default and default in linked:
        relay_id = default
    elif len(linked) == 1:
        relay_id = linked[0]
    elif len(linked) == 0:
        raise ValueError("No relay linked to this conversation. Use /relay link <relay_id>.")
    else:
        names = ", ".join(linked)
        raise ValueError(f"Multiple relays linked ({names}) but no default set. "
                         f"Use /relay default <relay_id> or pass relay= explicitly.")

    svc = greg.get_live_instance(relay_id)
    if svc is None:
        raise ValueError(f"Relay '{relay_id}' is linked but not connected.")

    return relay_id, svc
