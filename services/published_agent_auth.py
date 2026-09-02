"""Protocol-neutral lookup and authentication for published PawFlow agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.a2a_store import A2AStore
from services.mcp_server_endpoint import _bearer, _origin_allowed


@dataclass(frozen=True)
class PublishedAgentAccess:
    """A neutral result that lets each wire dialect shape its own errors."""

    publication: Optional[Dict[str, Any]] = None
    key: Optional[Dict[str, Any]] = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.publication is not None and not self.error


def resolve_published_agent(
        req,
        *,
        authenticate: bool = True,
        credential: Optional[str] = None,
) -> PublishedAgentAccess:
    """Resolve one live publication without writing an HTTP response."""

    publication_id = str(
        (getattr(req, "path_params", None) or {}).get("publication_id") or "")
    store = A2AStore.instance()
    publication = store.get_publication(publication_id)
    if not publication or float(publication.get("delete_requested_at") or 0):
        return PublishedAgentAccess(error="not_found")

    key = None
    if authenticate:
        if not _origin_allowed(req):
            return PublishedAgentAccess(error="origin_forbidden")
        raw_key = credential
        if raw_key is None:
            raw_key = _bearer(getattr(req, "headers", None) or {})
        key = store.validate_key(publication_id, raw_key or "")
        if not key:
            return PublishedAgentAccess(error="unauthorized")

    try:
        from core.conversation_store import ConversationStore
        owner = ConversationStore.instance().resolve_owner(
            publication["conversation_id"])
        from core.conv_agent_config import get_all_agent_configs
        configs = get_all_agent_configs(publication["conversation_id"]) or {}
        needle = str(publication["agent_name"]).lower()
        canonical = next(
            (name for name in configs
             if isinstance(name, str) and name.lower() == needle),
            "",
        )
    except Exception:
        owner, canonical = "", ""
    if owner != publication["owner_user_id"] or not canonical:
        return PublishedAgentAccess(error="unavailable")

    canonical_publication = dict(publication)
    canonical_publication["agent_name"] = canonical
    return PublishedAgentAccess(
        publication=canonical_publication,
        key=key,
    )


__all__ = ["PublishedAgentAccess", "resolve_published_agent"]
