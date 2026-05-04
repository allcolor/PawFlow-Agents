"""Auto-extract durable memories from compaction summaries.

Compaction summaries are mostly operational state. Only durable,
future-useful facts should enter long-term memory; transient debugging
state belongs in the conversation transcript and bucket summaries.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"facts", "events", "discoveries", "preferences", "advice"}
_VALID_SCOPES = {"global", "agent", "conversation", "private"}
_IMPORTANCE_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_DURABILITY_RANK = {"ephemeral": 0, "session": 1, "project": 2, "durable": 3}
_MAX_STORED_PER_EXTRACT = 2
_DEFAULT_EVENT_TTL_DAYS = 30
_DEFAULT_CONVERSATION_TTL_DAYS = 90

_EXTRACT_PROMPT = """Extract at most 2 durable long-term memories from this compaction summary.

Store ONLY facts that will still help in future conversations. Prefer:
- stable user preferences and operating rules
- project decisions that remain true after this conversation
- durable architectural constraints

Skip:
- current task status, latest validation results, in-flight files, stack traces
- temporary debugging details, tool-call chronology, line numbers, commits
- facts already phrased as "current", "latest", "recent", "after compact", or "passed"

Return a JSON array of objects with keys:
- "text": concise self-contained memory
- "category": one of "facts", "events", "discoveries", "preferences", "advice"
- "importance": "low", "medium", "high", or "critical"
- "durability": "ephemeral", "session", "project", or "durable"
- "scope": "global", "agent", "conversation", or "private"
- "ttl_days": integer; 0 for durable memories with no hard TTL
- "tags": optional list of stable tags

Rules:
- Use "global" only for durable high/critical user preferences or rules.
- Use "conversation" for project/debug facts that may become stale.
- Use "ephemeral" for temporary state; those entries will be discarded.

Example:
[{"text": "User expects compact to stop the agent, save the compacted PawFlow context, and restart the CLI from that context.", "category": "preferences", "importance": "critical", "durability": "durable", "scope": "global", "ttl_days": 0, "tags": ["compact", "preference"]}]

Summary:
"""

_EPHEMERAL_PATTERNS = re.compile(
    r"(?i)\b(current|latest|recent|in[- ]flight|actionable|validation passed|"
    r"tests? passed|after compact|post-compact|line \d+|around line|"
    r"commit [0-9a-f]{7,}|work centers on|was traced to|right now|today)\b"
)


def auto_extract_memories(
    user_id: str,
    summary: str,
    agent_name: str = "",
    llm_client=None,
    embed_fn=None,
    conversation_id: str = "",
) -> int:
    """Extract and store durable memories from a compaction summary.

    The extractor is intentionally conservative: compaction summaries are
    often full of transient operational state. Global permanent memories are
    reserved for durable high-value preferences/rules.
    """
    if not user_id or not summary:
        return 0

    if not llm_client:
        return 0

    facts = _extract_with_llm(llm_client, summary, user_id=user_id)
    if not facts:
        return 0

    from core.memory_store import MemoryStore
    store = MemoryStore.instance()
    count = 0
    for fact in facts:
        if count >= _MAX_STORED_PER_EXTRACT:
            break
        normalized = _normalize_fact(fact, agent_name, conversation_id)
        if not normalized:
            continue
        text, category, tags, agent, conv_id, expires_at = normalized
        try:
            embedding = None
            if embed_fn is not None:
                try:
                    vec = embed_fn(text)
                    if vec:
                        embedding = vec
                except Exception as exc:
                    logger.debug(f"[auto-extract] embed failed: {exc}")
            store.remember(
                user_id=user_id,
                text=text,
                tags=tags,
                source="compaction",
                embedding=embedding,
                agent=agent,
                conversation_id=conv_id,
                category=category,
                expires_at=expires_at,
            )
            count += 1
        except Exception as e:
            logger.debug(f"[auto-extract] failed to store: {e}")

    if count:
        logger.info(f"[auto-extract] stored {count} durable memories for user {user_id[:8]}")
    return count


def _normalize_fact(fact: Dict[str, Any], agent_name: str,
                    conversation_id: str) -> Tuple[str, str, List[str], str, str, float] | None:
    text = str(fact.get("text", "")).strip()
    if not text or len(text) < 20:
        return None
    if _EPHEMERAL_PATTERNS.search(text):
        return None

    category = str(fact.get("category", "") or fact.get("hall", "facts")).strip().lower()
    if category not in _VALID_CATEGORIES:
        category = "facts"

    importance = str(fact.get("importance", "medium") or "medium").strip().lower()
    durability = str(fact.get("durability", "session") or "session").strip().lower()
    if _IMPORTANCE_RANK.get(importance, 1) < 1:
        return None
    if _DURABILITY_RANK.get(durability, 1) <= 0:
        return None

    scope = str(fact.get("scope", "") or "").strip().lower()
    if scope not in _VALID_SCOPES:
        scope = _default_scope(category, importance, durability, agent_name, conversation_id)
    scope = _clamp_scope(scope, category, importance, durability, agent_name, conversation_id)

    ttl_days = _ttl_days(fact, category, scope, durability)
    expires_at = time.time() + ttl_days * 86400 if ttl_days > 0 else 0

    raw_tags = fact.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [str(t).lower().strip() for t in raw_tags if str(t).strip()]
    for tag in ("auto-extracted", "compaction"):
        if tag not in tags:
            tags.append(tag)
    if durability not in tags:
        tags.append(durability)

    if scope == "global":
        return text, category, tags, "", "", expires_at
    if scope == "agent":
        return text, category, tags, agent_name or "", "", expires_at
    if scope == "private":
        return text, category, tags, agent_name or "", conversation_id or "", expires_at
    return text, category, tags, "", conversation_id or "", expires_at


def _default_scope(category: str, importance: str, durability: str,
                   agent_name: str, conversation_id: str) -> str:
    if (category in {"preferences", "advice"}
            and _IMPORTANCE_RANK.get(importance, 1) >= 2
            and _DURABILITY_RANK.get(durability, 1) >= 3):
        return "global"
    if agent_name and not conversation_id:
        return "agent"
    return "conversation" if conversation_id else "agent"


def _clamp_scope(scope: str, category: str, importance: str, durability: str,
                 agent_name: str, conversation_id: str) -> str:
    if scope == "global":
        durable_global = (
            category in {"preferences", "advice"}
            and _IMPORTANCE_RANK.get(importance, 1) >= 2
            and _DURABILITY_RANK.get(durability, 1) >= 3
        )
        if not durable_global:
            return "conversation" if conversation_id else ("agent" if agent_name else "conversation")
    if scope == "private" and not (agent_name and conversation_id):
        return "agent" if agent_name else "conversation"
    if scope == "agent" and not agent_name and conversation_id:
        return "conversation"
    return scope


def _ttl_days(fact: Dict[str, Any], category: str, scope: str, durability: str) -> int:
    try:
        requested = int(fact.get("ttl_days", 0) or 0)
    except (TypeError, ValueError):
        requested = 0
    if requested > 0:
        return min(requested, 365)
    if scope == "global" and durability == "durable":
        return 0
    if category == "events":
        return _DEFAULT_EVENT_TTL_DAYS
    if scope in {"conversation", "private"}:
        return _DEFAULT_CONVERSATION_TTL_DAYS
    return 180


def _extract_with_llm(client, summary: str, user_id: str = "") -> list:
    """Use LLM to extract structured facts in an isolated ephemeral call."""
    try:
        from core.llm_client import LLMMessage
        _inner = getattr(client, "_client", client)
        _memory_client = _inner.clone_for_call()
        messages = [
            LLMMessage(role="user", content=_EXTRACT_PROMPT + summary,
                        conversation_id="_memory_extract"),
        ]
        resp = _memory_client.complete(
            messages=messages,
            temperature=0.2,
            max_tokens=1000,
            response_format="json",
            call_user_id=user_id,
            call_conversation_id="_memory_extract",
            call_agent_name="memory",
            call_event_cid="",
            call_ephemeral_stream=True,
        )
        content = resp.content.strip()
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug(f"[auto-extract] LLM extraction failed: {e}")
    return []
