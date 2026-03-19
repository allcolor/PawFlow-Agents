"""ResourceStore — User-scoped CRUD for agents, skills, MCP servers, prompts, task definitions.

Each resource type is stored in its own JSON file under config/.
Keys are namespaced by user_id: "user_id.resource_name".

Resource types:
- agent:    { name, prompt, model?, tools?, max_depth?, timeout?, description? }
- skill:    { name, prompt, description? }
- mcp:      { name, url, auth?, discovered_tools? }
- prompt:   { name, content, title?, category?, description? }
- task_def: { name, prompt, criteria?, default_interval?, description?, created_by? }
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path("config")

# File paths per resource type
_RESOURCE_FILES = {
    "agent": _CONFIG_DIR / "agents.json",
    "skill": _CONFIG_DIR / "skills.json",
    "mcp": _CONFIG_DIR / "mcp_servers.json",
    "prompt": _CONFIG_DIR / "prompts.json",
    "task_def": _CONFIG_DIR / "task_defs.json",
}

VALID_TYPES = frozenset(_RESOURCE_FILES.keys())
GLOBAL_USER_ID = "__global__"

# Required fields per type
_REQUIRED_FIELDS = {
    "agent": ("prompt",),
    "skill": ("prompt",),
    "mcp": ("url",),
    "prompt": ("content",),
    "task_def": ("prompt",),
}

# Default values per type
_DEFAULTS = {
    "agent": {
        "model": "",
        "tools": [],
        "max_depth": 1,
        "timeout": 120,
        "description": "",
        "llm_service": "",
    },
    "skill": {
        "description": "",
    },
    "mcp": {
        "auth": {},
        "discovered_tools": [],
    },
    "prompt": {
        "title": "",
        "category": "",
        "description": "",
    },
    "task_def": {
        "criteria": "",
        "default_interval": "6/1m",
        "description": "",
        "created_by": "",
    },
}


class ResourceStore:
    """Thread-safe singleton store for user-scoped resources."""

    _instance: Optional["ResourceStore"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._data: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._store_lock = threading.Lock()
        self._loaded: set = set()
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

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

    def _ensure_loaded(self, resource_type: str):
        """Lazy-load a resource file from disk."""
        if resource_type in self._loaded:
            return
        self._loaded.add(resource_type)
        path = _RESOURCE_FILES.get(resource_type)
        if not path or not path.exists():
            self._data[resource_type] = {}
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self._data[resource_type] = raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.warning("Failed to load %s: %s", path, e)
            self._data[resource_type] = {}

    def _save(self, resource_type: str):
        """Persist a resource type to disk."""
        path = _RESOURCE_FILES.get(resource_type)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data.get(resource_type, {}),
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception as e:
            logger.error("Failed to save %s: %s", path, e)

    @staticmethod
    def _key(user_id: str, name: str) -> str:
        return f"{user_id}:{name}"

    @staticmethod
    def _parse_key(key: str) -> tuple:
        """Split 'user_id:name' → (user_id, name)."""
        parts = key.split(":", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return "", parts[0]

    def create(self, resource_type: str, name: str, user_id: str,
               data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a resource. Raises ValueError if it already exists."""
        if resource_type not in VALID_TYPES:
            raise ValueError(f"Invalid resource type: {resource_type}")
        for field in _REQUIRED_FIELDS.get(resource_type, ()):
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        key = self._key(user_id, name)
        entry = dict(_DEFAULTS.get(resource_type, {}))
        # Extract system field before merging user data
        created_by = data.pop("_created_by", "")
        entry.update(data)
        entry["name"] = name
        entry["created_at"] = time.time()
        entry["updated_at"] = time.time()
        if created_by:
            entry["created_by"] = created_by

        with self._store_lock:
            self._ensure_loaded(resource_type)
            if key in self._data[resource_type]:
                raise ValueError(f"{resource_type} '{name}' already exists")
            self._data[resource_type][key] = entry
            self._save(resource_type)

        return entry

    def get(self, resource_type: str, name: str,
            user_id: str) -> Optional[Dict[str, Any]]:
        """Get a single resource by name (case-insensitive fallback)."""
        if resource_type not in VALID_TYPES:
            return None
        key = self._key(user_id, name)
        with self._store_lock:
            self._ensure_loaded(resource_type)
            result = self._data[resource_type].get(key)
            if result is not None:
                return result
            # Case-insensitive fallback
            name_lower = name.lower()
            for k, v in self._data[resource_type].items():
                if k.startswith(user_id + ".") and k[len(user_id) + 1:].lower() == name_lower:
                    return v
            return None

    def update(self, resource_type: str, name: str, user_id: str,
               data: Dict[str, Any]) -> Dict[str, Any]:
        """Update a resource. Raises KeyError if not found."""
        if resource_type not in VALID_TYPES:
            raise ValueError(f"Invalid resource type: {resource_type}")
        key = self._key(user_id, name)

        with self._store_lock:
            self._ensure_loaded(resource_type)
            existing = self._data[resource_type].get(key)
            if existing is None:
                raise KeyError(f"{resource_type} '{name}' not found")
            existing.update(data)
            existing["updated_at"] = time.time()
            self._save(resource_type)
            return dict(existing)

    def delete(self, resource_type: str, name: str,
               user_id: str) -> bool:
        """Delete a resource. Returns True if deleted."""
        if resource_type not in VALID_TYPES:
            return False
        key = self._key(user_id, name)

        with self._store_lock:
            self._ensure_loaded(resource_type)
            if key not in self._data[resource_type]:
                return False
            del self._data[resource_type][key]
            self._save(resource_type)
        return True

    def list(self, resource_type: str,
             user_id: str = "") -> List[Dict[str, Any]]:
        """List resources, optionally filtered by user_id."""
        if resource_type not in VALID_TYPES:
            return []

        with self._store_lock:
            self._ensure_loaded(resource_type)
            results = []
            for key, entry in self._data[resource_type].items():
                uid, rname = self._parse_key(key)
                if user_id and uid != user_id:
                    continue
                item = dict(entry)
                item["name"] = rname
                item["user_id"] = uid
                results.append(item)
        results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        return results

    def list_all(self, resource_type: str,
                 user_id: str,
                 conversation_id: str = "") -> List[Dict[str, Any]]:
        """List resources: conversation → user → global (with dedup and disable filter).

        Conversation agents are stored in ConversationStore extras.
        Disabled agents (per-conversation) are filtered out.
        """
        user_items = self.list(resource_type, user_id=user_id)
        if user_id == GLOBAL_USER_ID:
            result = user_items
        else:
            global_items = self.list(resource_type, user_id=GLOBAL_USER_ID)
            seen = {item["name"] for item in user_items}
            for gi in global_items:
                if gi["name"] not in seen:
                    gi["_scope"] = "global"
                    user_items.append(gi)
            # Tag user items
            for ui in user_items:
                if "_scope" not in ui:
                    ui["_scope"] = "user"
            result = user_items

        # Add conversation-scoped resources
        if conversation_id and resource_type == "agent":
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                conv_agents = store.get_extra(conversation_id, "conversation_agents") or {}
                disabled = set(store.get_extra(conversation_id, "disabled_agents") or [])
                seen_names = {item["name"] for item in result}
                for name, data in conv_agents.items():
                    if name not in seen_names:
                        entry = dict(data)
                        entry["name"] = name
                        entry["_scope"] = "conversation"
                        result.append(entry)
                # Filter disabled
                result = [r for r in result if r["name"] not in disabled]
            except Exception:
                pass

        return result

    def get_any(self, resource_type: str, name: str,
                user_id: str,
                conversation_id: str = "") -> Optional[Dict[str, Any]]:
        """Get a resource by name: conversation → user → global."""
        # 1. Conversation-scoped
        if conversation_id and resource_type == "agent":
            try:
                from core.conversation_store import ConversationStore
                store = ConversationStore.instance()
                # Check disabled
                disabled = set(store.get_extra(conversation_id, "disabled_agents") or [])
                if name in disabled:
                    return None
                conv_agents = store.get_extra(conversation_id, "conversation_agents") or {}
                if name in conv_agents:
                    entry = dict(conv_agents[name])
                    entry["name"] = name
                    entry["_scope"] = "conversation"
                    return entry
            except Exception:
                pass
        # 2. User-scoped
        result = self.get(resource_type, name, user_id)
        if result is not None:
            result["_scope"] = "user"
            return result
        # 3. Global
        if user_id != GLOBAL_USER_ID:
            result = self.get(resource_type, name, GLOBAL_USER_ID)
            if result is not None:
                result["_scope"] = "global"
                return result
        return None

    def exists(self, resource_type: str, name: str,
               user_id: str) -> bool:
        """Check if a resource exists."""
        return self.get(resource_type, name, user_id) is not None
