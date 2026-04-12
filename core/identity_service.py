"""IdentityService — Cross-channel identity mapping.

Each identity (channel user ID) has a directory in data/system/users/{id}/
containing identity_mapping.json with its principal user ID.

Resolution: message from "12345" → read users/12345/identity_mapping.json
→ principal: "compte.google" → use as auth principal.

Principal's own file lists all linked channels (for UI/admin).
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

import core.paths as _paths


class IdentityService:
    """Singleton service for cross-channel identity mapping."""

    _instance: Optional["IdentityService"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._store_lock = threading.Lock()

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

    # ── Paths ─────────────────────────────────────────────────────

    @staticmethod
    def _safe_id(identity: str) -> str:
        """Sanitize identity for use as directory name."""
        return identity.replace(":", "__").replace("/", "_").replace("\\", "_")

    @classmethod
    def _mapping_path(cls, identity: str) -> Path:
        return _paths.USER_CONFIG_DIR / cls._safe_id(identity) / "identity_mapping.json"

    @classmethod
    def _read_mapping(cls, identity: str) -> dict:
        path = _paths.USER_CONFIG_DIR / cls._safe_id(identity) / "identity_mapping.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @classmethod
    def _write_mapping(cls, identity: str, data: dict):
        path = _paths.USER_CONFIG_DIR / cls._safe_id(identity) / "identity_mapping.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(path)

    # ── Link / Unlink ─────────────────────────────────────────────

    def link(self, user_id: str, channel: str, channel_id: str,
             bot_token: str = "") -> bool:
        """Link a channel identity to a PawFlow user (principal).

        Creates/updates:
          users/{channel_id}/identity_mapping.json → principal: user_id
          users/{user_id}/identity_mapping.json   → channels.{channel}: channel_id
        """
        with self._store_lock:
            # Check conflict: channel_id already linked to another principal
            existing = self._read_mapping(channel_id)
            if existing.get("principal") and existing["principal"] != user_id:
                return False

            # Write alias mapping: channel_id → principal
            self._write_mapping(channel_id, {"principal": user_id})

            # Update principal's channel list
            principal_data = self._read_mapping(user_id)
            principal_data.setdefault("principal", user_id)
            channels = principal_data.setdefault("channels", {})
            channels[channel] = channel_id
            if bot_token:
                tokens = principal_data.setdefault("bot_tokens", {})
                tokens[channel] = bot_token
            self._write_mapping(user_id, principal_data)

            logger.info("Linked %s:%s → %s", channel, channel_id, user_id)
            return True

    def unlink(self, user_id: str, channel: str) -> bool:
        """Remove a channel link."""
        with self._store_lock:
            principal_data = self._read_mapping(user_id)
            channels = principal_data.get("channels", {})
            channel_id = channels.pop(channel, None)
            if not channel_id:
                return False

            # Remove bot token
            tokens = principal_data.get("bot_tokens", {})
            tokens.pop(channel, None)

            # Remove active conv for this channel
            active = principal_data.get("active_conv", {})
            active.pop(channel, None)

            self._write_mapping(user_id, principal_data)

            # Remove alias directory
            alias_dir = _paths.USER_CONFIG_DIR / self._safe_id(channel_id)
            alias_file = alias_dir / "identity_mapping.json"
            if alias_file.exists():
                alias_file.unlink()
                try:
                    alias_dir.rmdir()
                except OSError:
                    pass

            logger.info("Unlinked %s:%s from %s", channel, channel_id, user_id)
            return True

    # ── Resolve ───────────────────────────────────────────────────

    def resolve_user(self, channel: str, channel_id: str) -> Optional[str]:
        """Resolve a channel identity to a principal user_id. O(1) lookup."""
        data = self._read_mapping(channel_id)
        return data.get("principal") or None

    def get_channel_id(self, user_id: str, channel: str) -> Optional[str]:
        """Get the channel ID for a user."""
        data = self._read_mapping(user_id)
        return data.get("channels", {}).get(channel)

    def get_bot_token(self, user_id: str, channel: str) -> Optional[str]:
        """Get the personal bot token for a user on a channel."""
        data = self._read_mapping(user_id)
        return data.get("bot_tokens", {}).get(channel)

    # ── Active conversation per channel ───────────────────────────

    def set_active_conv(self, user_id: str, channel: str,
                        conversation_id: str) -> bool:
        """Set the active conversation for a user on a channel."""
        with self._store_lock:
            data = self._read_mapping(user_id)
            if not data:
                return False
            active = data.setdefault("active_conv", {})
            active[channel] = conversation_id
            self._write_mapping(user_id, data)
            return True

    def get_active_conv(self, user_id: str, channel: str) -> Optional[str]:
        """Get the active conversation for a user on a channel."""
        data = self._read_mapping(user_id)
        return data.get("active_conv", {}).get(channel)

    # ── Query ─────────────────────────────────────────────────────

    def get_links(self, user_id: str) -> Dict[str, str]:
        """Get all channel links for a user."""
        data = self._read_mapping(user_id)
        return data.get("channels", {})

    def resolve(self, provider: str, provider_id: str) -> Optional[str]:
        """Alias for resolve_user."""
        return self.resolve_user(provider, provider_id)

    def list_all(self) -> Dict[str, Dict[str, str]]:
        """List all principals with their channel links (admin)."""
        result = {}
        if not _paths.USER_CONFIG_DIR.exists():
            return result
        for user_dir in _paths.USER_CONFIG_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            data = self._read_mapping(user_dir.name)
            channels = data.get("channels")
            if channels:
                result[user_dir.name] = channels
        return result
