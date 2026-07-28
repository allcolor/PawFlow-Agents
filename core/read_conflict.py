"""Cross-agent read conflicts — tell an agent when code moved under it.

When several agents work in one conversation they share the same relay, and
therefore the same files. Agent B reads ``service.py``, reasons about it for a
few turns, and meanwhile agent A rewrites it. Nothing today tells B: it keeps
editing against a view that no longer exists, and the collision only surfaces
as a failed ``old_string`` match — or worse, as a silently clobbered change.

This module closes that hole using state the edit guard already keeps.
:mod:`core.handlers._edit_guard` records, per agent, the hash of every file
that agent has read. So when a write lands we can ask the exact question that
matters: *which other agents have read this path, and does what they saw still
match what is on disk?*

Three properties keep it cheap and quiet:

* **Zero cost when alone.** A conversation with one agent has no other
  readers, so a write does a single dict scan and stops. The common case pays
  nothing.
* **Silent when nothing actually changed.** When the writer hands us the new
  bytes we compare hashes: rewriting a file with identical content notifies
  nobody. Relay-backed writes, where fetching the new content would cost a
  round trip, invalidate unconditionally — a successful edit there means the
  content did change.
* **Cleared by a re-read.** The moment the notified agent reads the file
  again, its view is current and the notice is dropped. It is told once, not
  every turn until the conversation ends.

The notice rides the same dynamic-metadata channel as passive recall, so it
sits after every cache breakpoint and costs nothing in prompt-cache terms.

Known limitation: identity is the canonical path, without the filesystem
service. Two agents bound to *different* relays that both hold a file at the
same absolute path would produce a spurious notice. The notice is advisory —
it asks the agent to re-read, it never refuses an operation — so the cost of
that case is one wasted read.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

BLOCK_TITLE = "Files that changed under you"

#: Paths rendered in one block. Past this the agent should re-read the area it
#: is working on, not walk a list.
MAX_PATHS = 10

#: Conversations holding pending notices at once.
MAX_TRACKED = 256

_LOCK = threading.Lock()

# (user_id, conv_id, agent_name) -> OrderedDict[canonical_path, _Notice]
_PENDING: "OrderedDict[Tuple[str, str, str], OrderedDict]" = OrderedDict()


class _Notice:
    """One path that changed under one agent, since that agent last read it."""

    __slots__ = ("display_path", "writers", "changes")

    def __init__(self, display_path: str, writer: str):
        self.display_path = display_path
        self.writers = [writer] if writer else []
        self.changes = 1

    def add(self, writer: str) -> None:
        self.changes += 1
        if writer and writer not in self.writers:
            self.writers.append(writer)


def _key(user_id: str, conv_id: str, agent_name: str) -> Tuple[str, str, str]:
    return (user_id or "", conv_id or "", agent_name or "")


def note_write(user_id: str, conv_id: str, agent_name: str, path: str,
               new_content: Optional[bytes] = None) -> int:
    """Record that ``agent_name`` wrote ``path``; notify stale readers.

    ``new_content`` is the bytes now on disk when the caller has them, which
    lets an identical rewrite stay silent. Pass ``None`` when fetching them
    would cost a round trip: every other reader is then treated as stale.

    Returns the number of agents notified — 0 in the single-agent case.
    """
    if not (user_id and conv_id and path):
        return 0
    try:
        from core.handlers._edit_guard import canonical_path, readers_of, track_write

        # The writer's own view is now current, whatever happens below.
        if new_content is not None:
            track_write(user_id, conv_id, agent_name, path, new_content)

        stale = readers_of(user_id, conv_id, path,
                           exclude_agent=agent_name,
                           content=new_content)
        if not stale:
            return 0
        canon = canonical_path(path)
    except Exception:
        # A conflict notice is a convenience. It must never be the reason a
        # write reports failure.
        logger.debug("Read-conflict bookkeeping failed", exc_info=True)
        return 0

    with _LOCK:
        for reader in stale:
            key = _key(user_id, conv_id, reader)
            paths = _PENDING.get(key)
            if paths is None:
                paths = OrderedDict()
                _PENDING[key] = paths
            _PENDING.move_to_end(key)
            existing = paths.get(canon)
            if existing is None:
                paths[canon] = _Notice(path, agent_name)
            else:
                existing.add(agent_name)
            paths.move_to_end(canon)
            while len(paths) > MAX_PATHS:
                paths.popitem(last=False)
        while len(_PENDING) > MAX_TRACKED:
            _PENDING.popitem(last=False)
    return len(stale)


def clear_path(user_id: str, conv_id: str, agent_name: str, canon: str) -> None:
    """Drop this agent's notice for one path — it has a fresh view again.

    Called from the read tracker: an agent that re-reads the file is told
    nothing, because there is nothing left to tell it.
    """
    if not (user_id and conv_id and agent_name and canon):
        return
    key = _key(user_id, conv_id, agent_name)
    with _LOCK:
        paths = _PENDING.get(key)
        if not paths:
            return
        paths.pop(canon, None)
        if not paths:
            _PENDING.pop(key, None)


def render(paths: Dict[str, _Notice]) -> str:
    """Render pending notices as a compact block."""
    lines = []
    for notice in paths.values():
        who = ", ".join(f"'{w}'" for w in notice.writers)
        by = f" by {who}" if who else ""
        times = "" if notice.changes == 1 else f", {notice.changes} times"
        lines.append(f"- {notice.display_path} — changed{by}{times} "
                     f"since you read it")
    if not lines:
        return ""
    lines.append("Re-read any of these you are about to edit or reason about: "
                 "what you have in context is out of date.")
    return "\n".join(lines)


def pending_block(user_id: str, conv_id: str, agent_name: str) -> str:
    """Return this agent's pending notices as a block, and clear them.

    Cleared on read: the agent has now been told. If it ignores the notice and
    edits anyway, the edit guard's mismatch diagnostic is the next line of
    defence.
    """
    key = _key(user_id, conv_id, agent_name)
    with _LOCK:
        paths = _PENDING.pop(key, None)
    if not paths:
        return ""
    return render(paths)


def clear_conversation(user_id: str, conv_id: str) -> None:
    """Drop every pending notice for a conversation."""
    if not (user_id and conv_id):
        return
    with _LOCK:
        for key in [k for k in _PENDING if k[0] == user_id and k[1] == conv_id]:
            _PENDING.pop(key, None)


def clear_agent(user_id: str, conv_id: str, agent_name: str) -> None:
    """Drop every pending notice for one agent in one conversation."""
    if not (user_id and conv_id and agent_name):
        return
    with _LOCK:
        _PENDING.pop(_key(user_id, conv_id, agent_name), None)


def stats() -> dict:
    """Current state size — for leak detection in tests and ops."""
    with _LOCK:
        return {
            "agents_with_notices": len(_PENDING),
            "paths": sum(len(v) for v in _PENDING.values()),
        }


def reset_for_tests() -> None:
    """Clear all state. Only for tests."""
    with _LOCK:
        _PENDING.clear()
