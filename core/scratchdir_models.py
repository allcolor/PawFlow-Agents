"""Typed ScratchDir contracts shared by the server lifecycle layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

DEFAULT_TTL_HOURS = 168
MAX_TTL_HOURS = 720
DEFAULT_QUOTA_BYTES = 1024 * 1024 * 1024
DEFAULT_QUOTA_FILES = 10_000
MAX_QUOTA_BYTES = 100 * 1024 * 1024 * 1024
MAX_QUOTA_FILES = 1_000_000
SCRATCHDIR_FORMAT = "pawflow.scratchdir.v1"


class ScratchDirState(str, Enum):
    """Durable lifecycle states for one scoped ScratchDir."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CLEARING = "clearing"
    CLEARED = "cleared"
    ORPHANED = "orphaned"


class ScratchDirError(RuntimeError):
    """Typed failure whose stable code is safe to return to clients."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code or "scratchdir_error")
        super().__init__(message)


@dataclass(frozen=True)
class ScratchDirRecord:
    """One relay-backed directory scoped to a conversation agent."""

    id: str
    user_id: str
    conversation_id: str
    agent_name: str
    relay_id: str
    locator: str
    state: str
    epoch: int
    revision: int
    quota_bytes: int
    quota_files: int
    observed_bytes: int
    observed_files: int
    operation_id: str
    created_at: float
    updated_at: float
    expires_at: float
    cleared_at: float
    reconciled_at: float

    def public_dict(self) -> dict[str, Any]:
        """Return the model-visible record without owner or physical metadata."""

        value = asdict(self)
        for key in ("user_id", "conversation_id", "agent_name", "locator"):
            value.pop(key, None)
        value["format"] = SCRATCHDIR_FORMAT
        value["url"] = "fs://scratchdir/"
        value["mount_path"] = "/scratch"
        return value


def require_scope(user_id: str, conversation_id: str, agent_name: str,
                  relay_id: str) -> tuple[str, str, str, str]:
    """Validate and normalize the authenticated ScratchDir scope."""

    scope = tuple(str(item or "").strip() for item in (
        user_id, conversation_id, agent_name, relay_id))
    if not all(scope):
        raise ScratchDirError(
            "scratchdir_context_missing",
            "user_id, conversation_id, agent_name and relay_id are required")
    return scope


def validate_ttl(value: Any) -> int:
    """Return a bounded TTL in hours."""

    ttl = int(DEFAULT_TTL_HOURS if value is None else value)
    if ttl < 1 or ttl > MAX_TTL_HOURS:
        raise ScratchDirError(
            "scratchdir_ttl_invalid",
            f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
    return ttl


def context_hint() -> str:
    """Steer for the agent prompt: temporary files go to the ScratchDir.

    Unlike the Scratchpad hint, this one does not report state -- it has to be
    present *before* anything is written, because the failure it prevents is an
    agent reaching for /tmp on the relay or the server. That path looks like it
    works and then loses the file on the next container restart, with nothing
    scoped to the user, the conversation or the agent.

    Kept free of any relay round trip so it can be built on every turn.
    """
    return (
        "Temporary files belong in the ScratchDir, never in /tmp, /var/tmp or a "
        "hidden directory inside the project. Address it as `fs://scratchdir/` "
        "from any filesystem tool (read, write, edit, bash, glob, grep): it is "
        "scoped to this user + conversation + agent, survives tool calls, "
        "compaction and provider restarts, and expires on its own. Manage its "
        "lifecycle with the `scratchdir` tool (status, ensure, renew, clear). "
        "Use FileStore for durable deliverables and the workspace for source "
        "changes."
    )


def validate_quotas(quota_bytes: Any = None,
                    quota_files: Any = None) -> tuple[int, int]:
    """Return explicit bounded byte and file quotas."""

    byte_limit = int(DEFAULT_QUOTA_BYTES if quota_bytes is None else quota_bytes)
    file_limit = int(DEFAULT_QUOTA_FILES if quota_files is None else quota_files)
    if byte_limit < 1 or byte_limit > MAX_QUOTA_BYTES:
        raise ScratchDirError(
            "scratchdir_quota_invalid",
            f"quota_bytes must be between 1 and {MAX_QUOTA_BYTES}")
    if file_limit < 1 or file_limit > MAX_QUOTA_FILES:
        raise ScratchDirError(
            "scratchdir_quota_invalid",
            f"quota_files must be between 1 and {MAX_QUOTA_FILES}")
    return byte_limit, file_limit
