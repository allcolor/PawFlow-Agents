"""IdentityService — Cross-channel identity mapping.

Links external channel identities (Telegram, etc.) to PawFlow user IDs,
enabling shared conversations across channels.

Storage: JSON file at data/identity_mappings.json
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.paths import IDENTITY_MAPPINGS_FILE; _DEFAULT_PATH = str(IDENTITY_MAPPINGS_FILE)


class IdentityService:
    """Singleton service for cross-channel identity mapping."""

    _instance: Optional["IdentityService"] = None
    _lock = threading.Lock()

    def __init__(self, path: str = ""):
        self._path = Path(path or _DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Structure: { user_id: { "telegram": "123456", ... ,
        #              "active_conv": { "telegram": "conv-abc" } } }
        self._mappings: Dict[str, Dict[str, Any]] = {}
        self._store_lock = threading.Lock()
        self._loaded = False

    @classmethod
    def instance(cls) -> "IdentityService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        with cls._lock:
            cls._instance = None

    # ── Link / Unlink ──────────────────────────────────────────────

    def link(self, user_id: str, channel: str, channel_id: str,
             bot_token: str = "") -> bool:
        """Link a channel identity to a PawFlow user.

        Called from the web chat (authenticated) to link e.g. Telegram.
        Returns False if the channel_id is already linked to another user.
        Optionally stores a personal bot_token for the channel.
        """
        with self._store_lock:
            self._ensure_loaded()

            # Check if channel_id is already linked to a different user
            for uid, mapping in self._mappings.items():
                if uid != user_id and mapping.get(channel) == channel_id:
                    return False

            entry = self._mappings.setdefault(user_id, {})
            entry[channel] = channel_id
            if bot_token:
                entry[f"{channel}_bot_token"] = bot_token
            self._save()
            return True

    def unlink(self, user_id: str, channel: str) -> bool:
        """Remove a channel link (including bot token and active conv)."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._mappings.get(user_id)
            if entry and channel in entry:
                del entry[channel]
                entry.pop(f"{channel}_bot_token", None)
                # Also remove active_conv for this channel
                active = entry.get("active_conv", {})
                active.pop(channel, None)
                self._save()
                return True
            return False

    # ── Resolve ────────────────────────────────────────────────────

    def resolve_user(self, channel: str, channel_id: str) -> Optional[str]:
        """Resolve a channel identity to a PawFlow user_id.

        Returns None if no mapping exists.
        """
        with self._store_lock:
            self._ensure_loaded()
            for user_id, mapping in self._mappings.items():
                if mapping.get(channel) == channel_id:
                    return user_id
            return None

    def get_channel_id(self, user_id: str, channel: str) -> Optional[str]:
        """Get the channel ID for a user."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._mappings.get(user_id, {})
            return entry.get(channel)

    def get_bot_token(self, user_id: str, channel: str) -> Optional[str]:
        """Get the personal bot token for a user on a channel."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._mappings.get(user_id, {})
            return entry.get(f"{channel}_bot_token")

    # ── Active conversation per channel ────────────────────────────

    def set_active_conv(self, user_id: str, channel: str,
                        conversation_id: str) -> bool:
        """Set the active conversation for a user on a channel."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._mappings.get(user_id)
            if entry is None:
                return False
            active = entry.setdefault("active_conv", {})
            active[channel] = conversation_id
            self._save()
            return True

    def get_active_conv(self, user_id: str, channel: str) -> Optional[str]:
        """Get the active conversation for a user on a channel."""
        with self._store_lock:
            self._ensure_loaded()
            entry = self._mappings.get(user_id, {})
            return entry.get("active_conv", {}).get(channel)

    # ── Query ──────────────────────────────────────────────────────

    def get_links(self, user_id: str) -> Dict[str, str]:
        """Get all channel links for a user (excluding internal keys)."""
        _internal = {"active_conv"}
        with self._store_lock:
            self._ensure_loaded()
            entry = self._mappings.get(user_id, {})
            return {k: v for k, v in entry.items()
                    if k not in _internal
                    and not k.endswith("_bot_token")
                    and isinstance(v, str)}

    def resolve(self, provider: str, provider_id: str) -> Optional[str]:
        """Reverse lookup: find PawFlow username from a provider identity.

        Args:
            provider: Provider name (e.g. 'google', 'telegram', 'x', 'builtin')
            provider_id: The provider-specific user ID

        Returns:
            PawFlow username if linked, None otherwise.
        """
        with self._store_lock:
            self._ensure_loaded()
            for user_id, mapping in self._mappings.items():
                if mapping.get(provider) == provider_id:
                    return user_id
        return None

    def list_all(self) -> Dict[str, Dict[str, str]]:
        """List all mappings (admin)."""
        with self._store_lock:
            self._ensure_loaded()
            return {uid: {k: v for k, v in m.items()
                          if k != "active_conv" and isinstance(v, str)}
                    for uid, m in self._mappings.items()}

    # ── Persistence ────────────────────────────────────────────────

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                self._mappings = json.loads(
                    self._path.read_text(encoding="utf-8")
                )
            except Exception as e:
                logger.warning(f"Failed to load identity mappings: {e}")

    def _save(self):
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._mappings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"Failed to save identity mappings: {e}")
