"""Edit guard state — read tracking + duplicate-retry refusal.

Two guardrails against the failure modes agents hit repeatedly:

1. **Read tracking**: reads are tracked per agent/conversation/path so tools
   can clear stale failure state and legacy callers can still enforce a
   read-before-edit policy if needed. The main edit handler is opportunistic:
   an exact unique `old_string` is enough proof to apply the edit.

2. **Duplicate-retry refusal**: If the same (path, old_string) pair
   fails twice in a row without an intervening re-read, refuse
   further attempts with the same input. Breaks the retry-loop where
   agents re-send identical failing edits instead of diagnosing.

State is module-level with a bounded size (LRU-ish eviction) so memory
stays flat even in long-running servers. Scoped by
(user_id, conversation_id, agent_name, canonical_path).
"""

import hashlib
import os
import threading
from typing import Optional


_LOCK = threading.Lock()

# (user_id, conv_id, agent_name, canonical_path) -> content_hash_at_last_read
_READ_HASHES: dict = {}

# (user_id, conv_id, agent_name, canonical_path, old_string_hash) -> count
_FAILED_EDITS: dict = {}

# Soft cap — prune oldest entries when exceeded.
_MAX_ENTRIES = 2000


def _canon(path: str) -> str:
    """Normalize a path for identity — absolute + lowercase on Windows."""
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return path


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _hash_string(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


def _prune_if_full(d: dict):
    if len(d) > _MAX_ENTRIES:
        # Evict oldest half by insertion order (dicts preserve insertion
        # order in Python 3.7+). Cheap and bounded.
        _drop_n = _MAX_ENTRIES // 2
        for k in list(d.keys())[:_drop_n]:
            d.pop(k, None)


def _have_identity(user_id, conv_id, agent_name, path) -> bool:
    return bool(user_id and conv_id and agent_name and path)


def track_read(user_id: str, conv_id: str, agent_name: str,
                path: str, content: bytes):
    """Record that this agent has read this file (with content hash)."""
    if not _have_identity(user_id, conv_id, agent_name, path):
        return
    key = (user_id, conv_id, agent_name, _canon(path))
    h = _hash_content(content)
    with _LOCK:
        _READ_HASHES[key] = h
        _prune_if_full(_READ_HASHES)
        # A fresh read clears this agent's failed-edit streak for this path.
        _clear_failed_for_path(user_id, conv_id, agent_name, path)


def track_write(user_id: str, conv_id: str, agent_name: str,
                 path: str, new_content: bytes):
    """After a successful edit/write, update the read hash so subsequent
    edits by the same agent don't require a re-read of their own output.
    """
    if not _have_identity(user_id, conv_id, agent_name, path):
        return
    key = (user_id, conv_id, agent_name, _canon(path))
    with _LOCK:
        _READ_HASHES[key] = _hash_content(new_content)


def _clear_failed_for_path(user_id: str, conv_id: str,
                            agent_name: str, path: str):
    canon = _canon(path)
    _to_drop = [k for k in _FAILED_EDITS
                if k[0] == user_id and k[1] == conv_id
                and k[2] == agent_name and k[3] == canon]
    for k in _to_drop:
        _FAILED_EDITS.pop(k, None)


def check_read_before_edit(user_id: str, conv_id: str, agent_name: str,
                            path: str) -> Optional[str]:
    """Return an error message if the edit should be refused, else None.

    Checks that THIS agent has read this file in this conversation.
    Reads by other agents don't count — each agent has its own view.
    Hash verification is deliberately omitted to avoid an extra
    round-trip; the mismatch diagnostic surfaces stale-content cases
    via divergence reporting instead.
    """
    if not _have_identity(user_id, conv_id, agent_name, path):
        # Can't enforce without identity — fail open.
        return None
    key = (user_id, conv_id, agent_name, _canon(path))
    with _LOCK:
        present = key in _READ_HASHES
    if not present:
        return (f"Read-before-Edit: you (agent '{agent_name}') have not "
                f"read '{path}' in this conversation. Read the file first "
                f"so you see the exact content before editing. A read by a "
                f"different agent does not grant you permission — you need "
                f"your own view.")
    return None


def record_edit_failure(user_id: str, conv_id: str, agent_name: str,
                         path: str, old_string: str) -> int:
    """Increment failure count for (agent, path, old_string). Returns count."""
    if not _have_identity(user_id, conv_id, agent_name, path) or not old_string:
        return 0
    key = (user_id, conv_id, agent_name, _canon(path), _hash_string(old_string))
    with _LOCK:
        _FAILED_EDITS[key] = _FAILED_EDITS.get(key, 0) + 1
        _prune_if_full(_FAILED_EDITS)
        return _FAILED_EDITS[key]


def check_duplicate_failure(user_id: str, conv_id: str, agent_name: str,
                             path: str, old_string: str) -> Optional[str]:
    """If this same old_string already failed for this agent on this path,
    refuse. Only reads state — the caller must still record failures."""
    if not _have_identity(user_id, conv_id, agent_name, path) or not old_string:
        return None
    key = (user_id, conv_id, agent_name, _canon(path), _hash_string(old_string))
    with _LOCK:
        count = _FAILED_EDITS.get(key, 0)
    if count >= 1:
        return (f"Duplicate retry refused: this exact old_string has already "
                f"failed {count} time(s) on '{path}' for you without an "
                f"intervening re-read. Re-read the file — your input is based "
                f"on a mental model that doesn't match what's on disk.")
    return None


def clear_conversation(user_id: str, conv_id: str):
    """Drop all guard state for a conversation. Call from delete paths
    so state doesn't accumulate indefinitely in long-running servers.
    """
    if not user_id or not conv_id:
        return
    with _LOCK:
        _drop_r = [k for k in _READ_HASHES
                    if k[0] == user_id and k[1] == conv_id]
        for k in _drop_r:
            _READ_HASHES.pop(k, None)
        _drop_f = [k for k in _FAILED_EDITS
                    if k[0] == user_id and k[1] == conv_id]
        for k in _drop_f:
            _FAILED_EDITS.pop(k, None)


def clear_agent(user_id: str, conv_id: str, agent_name: str):
    """Drop guard state for one agent in one conversation. Call when the
    agent is unassigned / replaced in a conversation.
    """
    if not user_id or not conv_id or not agent_name:
        return
    with _LOCK:
        _drop_r = [k for k in _READ_HASHES
                    if k[0] == user_id and k[1] == conv_id
                    and k[2] == agent_name]
        for k in _drop_r:
            _READ_HASHES.pop(k, None)
        _drop_f = [k for k in _FAILED_EDITS
                    if k[0] == user_id and k[1] == conv_id
                    and k[2] == agent_name]
        for k in _drop_f:
            _FAILED_EDITS.pop(k, None)


def stats() -> dict:
    """Return current state size — useful for leak detection in tests/ops."""
    with _LOCK:
        return {
            "read_hashes": len(_READ_HASHES),
            "failed_edits": len(_FAILED_EDITS),
        }


def reset_for_tests():
    """Clear all state. Only for tests."""
    with _LOCK:
        _READ_HASHES.clear()
        _FAILED_EDITS.clear()
