"""Conversation and agent-instance access policy for secrets."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

SECRET_ACCESS_KEY = "secret_access"
_VALID_SCOPES = {"conv", "user", "global"}


def _scope(value: Any) -> str:
    result = str(value or "").strip().lower()
    if result == "conversation":
        result = "conv"
    return result


@dataclass(frozen=True)
class SecretIdentity:
    """Stable identity used for authorization before materialization."""

    name: str
    source_scope: str
    source_scope_id: str = ""

    def __post_init__(self):
        if not self.name:
            raise ValueError("secret name is required")
        if _scope(self.source_scope) not in _VALID_SCOPES:
            raise ValueError("source_scope must be conv, user, or global")
        object.__setattr__(self, "source_scope", _scope(self.source_scope))


@dataclass(frozen=True)
class SecretGrant:
    name: str
    source_scope: str

    def __post_init__(self):
        if not self.name:
            raise ValueError("secret grant name is required")
        if _scope(self.source_scope) not in _VALID_SCOPES:
            raise ValueError("secret grant source_scope must be conv, user, or global")
        object.__setattr__(self, "source_scope", _scope(self.source_scope))


def _parse_policy(raw: Any) -> frozenset[SecretGrant] | None:
    """Return None for unrestricted, including a missing policy."""

    if raw is None:
        return None
    items = raw.get("allow") if isinstance(raw, Mapping) else raw
    if items is None:
        raise ValueError("secret_access must contain an allow list")
    if not isinstance(items, list):
        raise TypeError("secret_access allow must be a list")
    grants = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("each secret_access grant must be an object")
        grants.add(SecretGrant(
            name=str(item.get("name") or "").strip(),
            source_scope=item.get("source_scope"),
        ))
    return frozenset(grants)


@dataclass(frozen=True)
class SecretAccessPolicy:
    """Effective conversation ∩ agent-instance policy."""

    conversation: frozenset[SecretGrant] | None
    agent: frozenset[SecretGrant] | None

    def allows(self, identity: SecretIdentity) -> bool:
        grant = SecretGrant(identity.name, identity.source_scope)
        return ((self.conversation is None or grant in self.conversation)
                and (self.agent is None or grant in self.agent))

    @classmethod
    def load(cls, conversation_id: str = "",
             agent_name: str = "") -> SecretAccessPolicy:
        if not conversation_id:
            return cls(None, None)
        from core.conversation_store import ConversationStore
        from core.service_registry import _parent_conversation_id

        root = _parent_conversation_id(conversation_id) or conversation_id
        store = ConversationStore.instance()
        conversation = _parse_policy(
            store.get_extra(root, SECRET_ACCESS_KEY, None))
        agent = None
        if agent_name:
            from core.conv_agent_config import resolve_agent_config_entry
            _source, canonical, config = resolve_agent_config_entry(
                root, agent_name)
            if canonical and SECRET_ACCESS_KEY in config:
                agent = _parse_policy(config.get(SECRET_ACCESS_KEY))
        return cls(conversation, agent)


def serialize_secret_access(grants: Iterable[Mapping[str, Any]]) -> dict:
    """Validate grants and attach audit-safe revision metadata."""

    parsed = _parse_policy({"allow": list(grants)})
    return {
        "allow": [
            {"name": grant.name, "source_scope": grant.source_scope}
            for grant in sorted(parsed or (), key=lambda item: (
                item.source_scope, item.name))
        ],
        "revision_id": str(uuid.uuid4()),
        "updated_at": time.time(),
    }


def set_conversation_secret_access(conversation_id: str,
                                   requester_user_id: str,
                                   grants: Iterable[Mapping[str, Any]]) -> dict:
    """Owner-authorized update of a conversation's maximum secret access."""

    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not requester_user_id:
        raise ValueError("requester_user_id is required")
    from core.conversation_access import require_owner
    from core.conversation_store import ConversationStore
    from core.service_registry import _parent_conversation_id

    root = _parent_conversation_id(conversation_id) or conversation_id
    require_owner(root, requester_user_id)
    value = serialize_secret_access(grants)
    ConversationStore.instance().set_extra(
        root, SECRET_ACCESS_KEY, value, user_id=requester_user_id)
    return value


def set_agent_secret_access(conversation_id: str, agent_name: str,
                            requester_user_id: str,
                            grants: Iterable[Mapping[str, Any]]) -> dict:
    """Owner-authorized update of one agent instance's reducing whitelist."""

    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not agent_name:
        raise ValueError("agent_name is required")
    if not requester_user_id:
        raise ValueError("requester_user_id is required")
    from core.conv_agent_config import set_agent_config
    from core.conversation_access import require_owner
    from core.service_registry import _parent_conversation_id

    root = _parent_conversation_id(conversation_id) or conversation_id
    require_owner(root, requester_user_id)
    value = serialize_secret_access(grants)
    set_agent_config(root, agent_name, {SECRET_ACCESS_KEY: value})
    return value
