"""Relay Bindings — conversation-scoped, per-agent relay management.

Relays are linked to a conversation and optionally to a specific agent.
Each scope (conv-wide or per-agent) can have its own default relay.

Storage: conversation extras key 'relay_bindings'
  {
    "linked": {"*": ["relay_a", "relay_b"], "claude": ["relay_c"]},
    "default": {"*": "relay_a", "claude": "relay_c"}
  }

"*" = conversation-wide scope (all agents).
Named agent = only that agent can use the relay.

Resolution order for get_default/get_linked:
  1. Agent-specific bindings
  2. Conv-wide ("*") bindings
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EXTRA_KEY = "relay_bindings"
_CONV = "*"  # conv-wide scope key


def _get_store():
    from core.conversation_store import ConversationStore
    return ConversationStore.instance()


def _invalidate_cli_after_mount_change(cid: str, agent: str = "") -> None:
    """Invalidate CLI sessions whose workspace mounts may have changed."""
    try:
        from core.cli_workspace_mounts import cli_workspace_mount_enabled
        if not cli_workspace_mount_enabled():
            return
        store = _get_store()
        if agent:
            store.invalidate_claude_session_for_agent(cid, agent)
        else:
            store.invalidate_claude_sessions(cid)
    except Exception:
        logger.debug("CLI workspace mount invalidation failed", exc_info=True)


def get_bindings(cid: str) -> Dict[str, Any]:
    """Get relay bindings for a conversation."""
    try:
        raw = _get_store().get_extra_cached(cid, _EXTRA_KEY, default=None)
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        return {"linked": {}, "default": {}}
    raw.setdefault("linked", {})
    raw.setdefault("default", {})
    return raw


def _binding_cids(cid: str) -> List[str]:
    cids = [cid]
    for marker in ("::task::", "::task_verify::", "::delegate::"):
        if cid and marker in cid:
            parent = cid.split(marker, 1)[0]
            if parent and parent != cid:
                cids.append(parent)
            break
    return cids


def get_linked(cid: str, agent: str = "") -> List[str]:
    """Get relay IDs available to an agent.

    Returns agent-specific relays + conv-wide relays (union, deduped).
    """
    result = []
    for lookup_cid in _binding_cids(cid):
        b = get_bindings(lookup_cid)
        linked = b.get("linked", {})
        for rid in linked.get(_CONV, []):
            if rid not in result:
                result.append(rid)
        if agent and agent != _CONV:
            for rid in linked.get(agent, []):
                if rid not in result:
                    result.append(rid)
    return result


def get_default(cid: str, agent: str = "") -> Optional[str]:
    """Get the default relay for an agent.

    Resolution: agent-specific default → conv-wide default.
    """
    for lookup_cid in _binding_cids(cid):
        b = get_bindings(lookup_cid)
        defaults = b.get("default", {})
        if agent and agent != _CONV:
            d = defaults.get(agent)
            if d:
                return d
        d = defaults.get(_CONV)
        if d:
            return d
    return None


def link_relay(cid: str, relay_id: str, agent: str = "") -> bool:
    """Link a relay to a conversation (optionally to a specific agent)."""
    scope = agent if agent else _CONV
    b = get_bindings(cid)
    linked = b.setdefault("linked", {})
    scope_list = linked.setdefault(scope, [])
    if relay_id in scope_list:
        return False
    scope_list.append(relay_id)
    # Auto-set default if first relay in this scope
    defaults = b.setdefault("default", {})
    if scope not in defaults:
        defaults[scope] = relay_id
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    _invalidate_cli_after_mount_change(cid, agent)
    scope_label = f"agent '{scope}'" if scope != _CONV else "conversation"
    logger.info("Relay '%s' linked to %s in %s", relay_id, scope_label, cid[:8])
    return True


def unlink_relay(cid: str, relay_id: str, agent: str = "") -> bool:
    """Unlink a relay from a conversation (optionally from a specific agent)."""
    scope = agent if agent else _CONV
    b = get_bindings(cid)
    linked = b.get("linked", {})
    scope_list = linked.get(scope, [])
    if relay_id not in scope_list:
        return False
    scope_list.remove(relay_id)
    if not scope_list:
        linked.pop(scope, None)
    defaults = b.get("default", {})
    if defaults.get(scope) == relay_id:
        defaults[scope] = scope_list[0] if scope_list else None
        if defaults[scope] is None:
            defaults.pop(scope, None)
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    _invalidate_cli_after_mount_change(cid, agent)
    scope_label = f"agent '{scope}'" if scope != _CONV else "conversation"
    logger.info("Relay '%s' unlinked from %s in %s", relay_id, scope_label, cid[:8])
    return True


def set_default_relay(cid: str, relay_id: str, agent: str = "") -> bool:
    """Set the default relay for a scope. Must be linked first."""
    scope = agent if agent else _CONV
    b = get_bindings(cid)
    linked = b.get("linked", {})
    # Relay must be linked in this scope or conv-wide
    all_linked = set(linked.get(_CONV, []))
    if scope != _CONV:
        all_linked.update(linked.get(scope, []))
    if relay_id not in all_linked:
        return False
    defaults = b.setdefault("default", {})
    defaults[scope] = relay_id
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    _invalidate_cli_after_mount_change(cid, agent)
    scope_label = f"agent '{scope}'" if scope != _CONV else "conversation"
    logger.info("Relay '%s' set as default for %s in %s", relay_id, scope_label, cid[:8])
    return True


def get_default_local(cid: str, relay_id: str = "", agent: str = "") -> Optional[bool]:
    """Get the default local mode for a relay + scope.

    Resolution: relay+agent → relay+conv → None.
    Returns True (local), False (docker), or None (not set).
    """
    b = get_bindings(cid)
    dl = b.get("default_local", {})
    # If relay_id specified, look up that relay's settings
    rid = relay_id or get_default(cid, agent) or ""
    if not rid:
        return None
    relay_dl = dl.get(rid, {})
    if not isinstance(relay_dl, dict):
        return None
    scope = agent if agent else _CONV
    if scope and scope != _CONV and scope in relay_dl:
        return relay_dl[scope]
    if _CONV in relay_dl:
        return relay_dl[_CONV]
    return None


def set_default_local(cid: str, relay_id: str, local: bool, agent: str = "") -> bool:
    """Set the default local mode for a specific relay + scope."""
    if not relay_id:
        return False
    scope = agent if agent else _CONV
    b = get_bindings(cid)
    dl = b.setdefault("default_local", {})
    relay_dl = dl.setdefault(relay_id, {})
    relay_dl[scope] = local
    _get_store().set_extra(cid, _EXTRA_KEY, b)
    scope_label = f"agent '{scope}'" if scope != _CONV else "conversation"
    logger.info("Relay '%s' default local=%s for %s in %s", relay_id, local, scope_label, cid[:8])
    return True


def list_available_relays(user_id: str = "") -> List[Dict[str, Any]]:
    """List all connected relay services across all scopes."""
    relays = []
    try:
        from core.service_registry import ServiceRegistry
        reg = ServiceRegistry.get_instance()
        all_defs = reg.resolve_all(user_id=user_id)
        for sid, sdef in all_defs.items():
            if sdef.service_type not in ("filesystem", "relay"):
                continue
            connected = reg.is_connected(sdef.scope, sdef.scope_id, sid)
            svc = reg.resolve(sid, user_id=user_id)
            _ri = getattr(svc, '_relay_info', {}) or {} if svc else {}
            relays.append({
                "relay_id": sid,
                "connected": connected,
                "user_id": sdef.scope_id if sdef.scope == "user" else "",
                "root": _ri.get("root", ""),
                "host_root": _ri.get("host_root", ""),
                "allow_local": bool(_ri.get('allow_local', False)),
                "allow_local_screen": bool(_ri.get('allow_local_screen', False)),
            })
    except Exception:
        logging.getLogger(__name__).debug("Ignored exception", exc_info=True)
    return relays


def resolve_relay(cid: str, relay_param: Optional[str] = None,
                  agent: str = "") -> Tuple[str, Any]:
    """Resolve a relay for a conversation + agent.

    Resolution order:
      1. Explicit relay_param (must be linked)
      2. Agent-specific default
      3. Conv-wide default
      4. Single linked relay (unambiguous)
    """
    linked = get_linked(cid, agent)
    default = get_default(cid, agent)

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

    # Resolve service instance across all scopes
    try:
        from core.service_registry import ServiceRegistry
        svc = ServiceRegistry.get_instance().resolve(relay_id)
    except Exception:
        svc = None
    if svc is None:
        raise ValueError(f"Relay '{relay_id}' is linked but not connected.")

    return relay_id, svc
