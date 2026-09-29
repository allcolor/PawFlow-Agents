"""read_history search answered from the conversation full-text index.

The scan decodes every transcript segment that mentions a search term: about
10 s on a 2 GB conversation for a common word (2026-09-29). The index
(``core.conversation_index``) already holds each user and assistant message
with its display position, speaker and involved agents, so a search filtered
on one of those roles reads SQLite instead of the transcript.

The matching rules are the scan's -- exact substring first, the keyword
fallback only when nothing matched exactly -- applied in Python to the
indexed text. SQL only narrows the candidates. Anything the index cannot
answer for (another role, no user, an encrypted or not-yet-indexed
conversation) returns None and the caller scans as before.
"""

import heapq
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# The index holds user and assistant rows only; tool payloads are left out on
# purpose (see conversation_index._INDEXED_ROLES).
INDEXED_ROLE_FILTERS = frozenset({"user", "assistant"})

Hits = Tuple[List[Tuple[int, Dict[str, Any]]], int]


def indexed_search_hits(store, conversation_id: str, user_id: str,
                        query: str, tokens: List[str], role_filter: str,
                        agent_filter: str, budget: int) -> Optional[Hits]:
    """``(hits, total)`` as the scan would return them, or None to scan."""
    if role_filter not in INDEXED_ROLE_FILTERS or not user_id:
        return None
    try:
        # One index per user: it answers only for that user's conversations.
        if (store._load_cache(conversation_id) or {}).get("user_id") != user_id:
            return None
        from core.conversation_index import ConversationIndex
        index = ConversationIndex.for_user(user_id)
        if not index.available or not index.ensure_current(store,
                                                           conversation_id):
            return None
        return _collect(index, conversation_id, query, tokens, role_filter,
                        agent_filter, budget)
    except Exception:
        logger.debug("indexed history search unavailable for %s",
                     str(conversation_id)[:8], exc_info=True)
        return None


def _ascii(needles: Iterable[str]) -> List[str]:
    """Needles SQLite may prefilter on; its lower() folds ASCII only."""
    needles = list(needles)
    return needles if all(n.isascii() for n in needles) else []


def _message(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the handler's formatter reads from a message."""
    return {"role": row["role"], "content": row["content"],
            "source": {"name": row["speaker"] or ""}}


def _collect(index, cid: str, query: str, tokens: List[str],
             role_filter: str, agent_filter: str, budget: int) -> Hits:
    from core.handlers.history import _keyword_score

    query_lower = query.lower()
    exact_hits: List[Tuple[int, Dict[str, Any]]] = []
    exact_total = 0
    for row in index.search_conversation(cid, role_filter, agent_filter,
                                         _ascii([query_lower])):
        if query_lower in (row["content"] or "").lower():
            exact_total += 1
            if len(exact_hits) < budget:
                exact_hits.append((int(row["pos"]), _message(row)))
    if exact_total or not tokens:
        return exact_hits, exact_total

    token_hits: list = []  # min-heap of (score, -index, index, msg)
    token_total = 0
    for row in index.search_conversation(cid, role_filter, agent_filter,
                                         _ascii(tokens)):
        score = _keyword_score(tokens, (row["content"] or "").lower())
        if not score:
            continue
        token_total += 1
        pos = int(row["pos"])
        candidate = (score, -pos, pos, _message(row))
        if len(token_hits) < budget:
            heapq.heappush(token_hits, candidate)
        elif candidate[:2] > token_hits[0][:2]:
            heapq.heapreplace(token_hits, candidate)
    token_hits.sort(key=lambda h: (-h[0], h[2]))
    return [(i, msg) for _score, _neg, i, msg in token_hits], token_total
