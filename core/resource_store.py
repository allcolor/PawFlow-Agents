"""ResourceStore — Facade over ScopedRepository for user-scoped resources.

Provides the same API as the original monolithic ResourceStore but delegates
all I/O to ScopedRepository (1 JSON file per resource under data/repository/).

Resource types:
- agent:    { name, prompt, model?, tools?, max_depth?, timeout?, description? }
- skill:    { name, prompt, description?, parameters?, extends? }
- mcp:      { name, url, auth?, discovered_tools? }
- task_def: { name, prompt, criteria?, default_interval?, description?, created_by? }
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GLOBAL_USER_ID = "__global__"

# Mapping: ResourceStore type name → ScopedRepository rtype (plural)
_TYPE_MAP = {
    "agent": "agents",
    "skill": "skills",
    "mcp": "mcps",
    "task_def": "tasks",
    "prompt": "prompts",
    "tool": "tools",
}


VALID_TYPES = frozenset(_TYPE_MAP.keys())

# Required fields per type
_REQUIRED_FIELDS = {
    "agent": ("prompt",),
    "skill": ("prompt",),
    "mcp": (),  # url or command required (validated in create)
    "task_def": ("prompt",),
    "prompt": ("prompt",),
    "tool": ("source",),
}

# Default values per type
_DEFAULTS = {
    "agent": {
        "description": "",
    },
    "skill": {
        "description": "",
        "parameters": {},
        "extends": "",
    },
    "mcp": {
        "url": "",              # HTTP transport: server URL
        "transport": "http",    # "http" or "stdio"
        "via": "",              # "relay" or "direct"
        "relay_service": "",
        "local": False,           # stdio via relay: run on host helper
        "command": "",          # stdio transport: command to run
        "args": [],             # stdio transport: command arguments
        "env": {},              # stdio/http: extra environment variables
        "auth": {},             # HTTP transport: auth headers
        "discovered_tools": [],
    },
    "task_def": {
        "criteria": "",
        "default_interval": "6/1m",
        "description": "",
        "created_by": "",
        "skills": [],
    },
    "prompt": {
        "title": "",
        "category": "",
        "description": "",
        "parameters": {},
    },
    "tool": {
        "source": "",
        "description": "",
        "parameters": {},
        "checksum": "",
    },
}


def _repo_type(resource_type: str) -> str:
    """Map singular resource type to plural repository type."""
    rtype = _TYPE_MAP.get(resource_type)
    if not rtype:
        raise ValueError(f"Invalid resource type: {resource_type}")
    return rtype


class ResourceStore:
    """Thread-safe singleton — facade over ScopedRepository."""

    _instance: Optional["ResourceStore"] = None
    _lock = threading.Lock()

    def __init__(self):
        pass

    @classmethod
    def instance(cls) -> "ResourceStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def reload(self, resource_type: str = ""):
        """No-op — ScopedRepository reads from disk on every call."""
        pass

    # ── CRUD ──────────────────────────────────────────────────────

    def create(self, resource_type: str, name: str, user_id: str,
               data: Dict[str, Any],
               conversation_id: str = "") -> Dict[str, Any]:
        """Create a resource. Raises ValueError if it already exists."""
        rtype = _repo_type(resource_type)
        for field in _REQUIRED_FIELDS.get(resource_type, ()):
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        entry = dict(_DEFAULTS.get(resource_type, {}))
        created_by = data.pop("_created_by", "")
        entry.update(data)
        entry["name"] = name
        entry["created_at"] = time.time()
        entry["updated_at"] = time.time()
        if created_by:
            entry["created_by"] = created_by

        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()
        scope, uid, cid = self._map_scope(user_id, conversation_id)
        return repo.create(rtype, name, scope, entry,
                           user_id=uid, conv_id=cid)

    def get(self, resource_type: str, name: str,
            user_id: str,
            conversation_id: str = "") -> Optional[Dict[str, Any]]:
        """Get a single resource by name (case-insensitive fallback)."""
        if resource_type not in VALID_TYPES:
            return None
        rtype = _repo_type(resource_type)
        scope, uid, cid = self._map_scope(user_id, conversation_id)

        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()

        result = repo.get(rtype, name, scope, user_id=uid, conv_id=cid)
        if result is not None:
            return result

        # Case-insensitive fallback
        items = repo.list(rtype, scope, user_id=uid, conv_id=cid)
        name_lower = name.lower()
        for item in items:
            if item.get("name", "").lower() == name_lower:
                return item
        return None

    def update(self, resource_type: str, name: str, user_id: str,
               data: Dict[str, Any],
               conversation_id: str = "") -> Dict[str, Any]:
        """Update a resource. Raises KeyError if not found."""
        rtype = _repo_type(resource_type)
        scope, uid, cid = self._map_scope(user_id, conversation_id)

        from core.repository import ScopedRepository
        return ScopedRepository.instance().update(
            rtype, name, scope, data, user_id=uid, conv_id=cid)

    def delete(self, resource_type: str, name: str,
               user_id: str,
               conversation_id: str = "") -> bool:
        """Delete a resource. Returns True if deleted."""
        if resource_type not in VALID_TYPES:
            return False
        rtype = _repo_type(resource_type)
        scope, uid, cid = self._map_scope(user_id, conversation_id)

        from core.repository import ScopedRepository
        return ScopedRepository.instance().delete(
            rtype, name, scope, user_id=uid, conv_id=cid)

    def list(self, resource_type: str,
             user_id: str = "",
             conversation_id: str = "") -> List[Dict[str, Any]]:
        """List resources for a specific scope."""
        if resource_type not in VALID_TYPES:
            return []
        rtype = _repo_type(resource_type)

        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()

        if user_id:
            scope, uid, cid = self._map_scope(user_id, conversation_id)
            results = repo.list(rtype, scope, user_id=uid, conv_id=cid)
        else:
            results = repo.list(rtype, "global")

        results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return results

    def list_all(self, resource_type: str,
                 user_id: str,
                 conversation_id: str = "") -> List[Dict[str, Any]]:
        """List resources: conversation → user → global (with dedup and disable filter).

        Conversation agents are stored in ConversationStore extras.
        Disabled agents (per-conversation) are filtered out.
        """
        if resource_type not in VALID_TYPES:
            return []
        rtype = _repo_type(resource_type)

        from core.repository import ScopedRepository
        repo = ScopedRepository.instance()

        # User-scoped items
        if user_id == GLOBAL_USER_ID:
            user_items = repo.list(rtype, "global")
            for item in user_items:
                item["_scope"] = "global"
            result = user_items
        else:
            user_items = repo.list(rtype, "user", user_id=user_id)
            for item in user_items:
                item["_scope"] = "user"

            global_items = repo.list(rtype, "global")
            seen = {item.get("name") for item in user_items}
            for gi in global_items:
                if gi.get("name") not in seen:
                    gi["_scope"] = "global"
                    user_items.append(gi)
            result = user_items

        # Add conversation-scoped resources (from repository conv scope)
        if conversation_id and user_id != GLOBAL_USER_ID:
            try:
                conv_items = repo.list(rtype, "conv",
                                       user_id=user_id, conv_id=conversation_id)
                seen_names = {item.get("name") for item in result}
                for item in conv_items:
                    if item.get("name") not in seen_names:
                        item["_scope"] = "conversation"
                        result.append(item)
            except Exception:
                pass
            # Also check conversation_task_defs extras (task_defs still in extras)
            if resource_type == "task_def":
                try:
                    from core.conversation_store import ConversationStore
                    store = ConversationStore.instance()
                    conv_defs = store.get_extra(conversation_id,
                                                "conversation_task_defs") or {}
                    seen_names = {item.get("name") for item in result}
                    for td_name, td_data in conv_defs.items():
                        if td_name not in seen_names:
                            entry = dict(td_data)
                            entry["name"] = td_name
                            entry["_scope"] = "conversation"
                            result.append(entry)
                except Exception:
                    pass
            # Filter disabled agents
            if resource_type == "agent":
                try:
                    from core.conversation_store import ConversationStore
                    disabled = set(
                        ConversationStore.instance().get_extra(
                            conversation_id, "disabled_agents") or [])
                    result = [r for r in result
                              if r.get("name") not in disabled]
                except Exception:
                    pass

        return result

    def get_any(self, resource_type: str, name: str,
                user_id: str,
                conversation_id: str = "") -> Optional[Dict[str, Any]]:
        """Get a resource by name: conversation → user → global."""
        # 1. Conversation-scoped (repository conv scope)
        if conversation_id and user_id != GLOBAL_USER_ID:
            # Check disabled agents
            if resource_type == "agent":
                try:
                    from core.conversation_store import ConversationStore
                    disabled = set(
                        ConversationStore.instance().get_extra(
                            conversation_id, "disabled_agents") or [])
                    if name in disabled:
                        return None
                except Exception:
                    pass
            rtype = _repo_type(resource_type)
            from core.repository import ScopedRepository
            result = ScopedRepository.instance().get(
                rtype, name, "conv",
                user_id=user_id, conv_id=conversation_id)
            if result is not None:
                result["_scope"] = "conversation"
                return result
            # Task defs: also check extras
            if resource_type == "task_def":
                try:
                    from core.conversation_store import ConversationStore
                    conv_defs = ConversationStore.instance().get_extra(
                        conversation_id, "conversation_task_defs") or {}
                    if name in conv_defs:
                        entry = dict(conv_defs[name])
                        entry["name"] = name
                        entry["_scope"] = "conversation"
                        return entry
                except Exception:
                    pass
        # 2. User-scoped
        if user_id != GLOBAL_USER_ID:
            result = self.get(resource_type, name, user_id)
            if result is not None:
                result["_scope"] = "user"
                return result
        # 3. Global
        result = self.get(resource_type, name, GLOBAL_USER_ID)
        if result is not None:
            result["_scope"] = "global"
            return result
        return None

    def exists(self, resource_type: str, name: str,
               user_id: str) -> bool:
        """Check if a resource exists."""
        return self.get(resource_type, name, user_id) is not None

    # ── Internal ──────────────────────────────────────────────────

    @staticmethod
    def _map_scope(user_id: str, conversation_id: str = ""):
        """Map ResourceStore params to (scope, user_id, conv_id)."""
        if conversation_id and user_id != GLOBAL_USER_ID:
            return "conv", user_id, conversation_id
        if user_id == GLOBAL_USER_ID:
            return "global", "", ""
        return "user", user_id, ""
