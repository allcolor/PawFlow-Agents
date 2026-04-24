"""Auto-extract memories from compaction summaries.

When a conversation is compacted, key facts from the summary
can be persisted as long-term memories so they survive across sessions.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """Extract 3-5 key facts from this conversation summary that would be useful to remember long-term.
Focus on: user preferences, project decisions, technical choices, people/roles, deadlines.
Skip: ephemeral task details, code snippets, debugging steps.

Return a JSON array of objects with keys: "text", "category"
- category: one of "facts", "events", "discoveries", "preferences", "advice"

Example:
[{"text": "User prefers JSON over SQLite for storage", "category": "preferences"},
 {"text": "Auth middleware rewrite driven by compliance", "category": "facts"}]

Summary:
"""


def auto_extract_memories(
    user_id: str,
    summary: str,
    agent_name: str = "",
    llm_client=None,
    embed_fn=None,
) -> int:
    """Extract and store memories from a compaction summary.

    Uses LLM if available, otherwise uses heuristic extraction.
    Returns the number of memories stored.
    """
    if not user_id or not summary:
        return 0

    if not llm_client:
        return 0  # No LLM = no extraction (no heuristic fallback)

    facts = _extract_with_llm(llm_client, summary, user_id=user_id)

    if not facts:
        return 0

    from core.memory_store import MemoryStore
    store = MemoryStore.instance()
    count = 0
    for fact in facts[:5]:
        text = fact.get("text", "").strip()
        if not text or len(text) < 10:
            continue
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
                tags=["auto-extracted", "compaction"],
                source="compaction",
                embedding=embedding,
                agent=agent_name,
                category=fact.get("category", "") or fact.get("hall", "facts"),
            )
            count += 1
        except Exception as e:
            logger.debug(f"[auto-extract] failed to store: {e}")

    if count:
        logger.info(f"[auto-extract] stored {count} memories for user {user_id[:8]}")
    return count


def _extract_with_llm(client, summary: str, user_id: str = "") -> list:
    """Use LLM to extract structured facts from summary.

    CRITICAL: we ISOLATE this call from the caller's active conversation.
    Callers (agent_streaming periodic save, bg_bucket_builder) pass the
    main agent's LLM client, which still carries `_conversation_id` /
    `_agent_name` / `_event_cid` / `_user_id` from the main stream's
    context. If we don't swap those to a `_memory_extract` sentinel, the
    extract prompt gets pushed on the main agent's live CC session
    (via cc_live_registry reuse) — polluting the main conv with a rogue
    "extract facts" turn whose reply lands in the user's chat as an
    empty-text stop. Same pattern as tasks/ai/agent_compaction.py
    _auto_extract_memories, centralised here so every caller benefits.
    """
    try:
        from core.llm_client import LLMMessage
        # Swap sentinel context on the inner client (claude-code keeps
        # conv/agent state on the provider mixin, not just on messages).
        _inner = getattr(client, "_client", client)
        _saved = (
            getattr(_inner, "_conversation_id", ""),
            getattr(_inner, "_agent_name", ""),
            getattr(_inner, "_user_id", ""),
            getattr(_inner, "_event_cid", ""),
        )
        _inner._conversation_id = "_memory_extract"
        _inner._agent_name = "memory"
        if user_id:
            _inner._user_id = user_id
        _inner._event_cid = ""
        try:
            messages = [
                LLMMessage(role="user", content=_EXTRACT_PROMPT + summary,
                            conversation_id="_memory_extract"),
            ]
            resp = client.complete(
                messages=messages,
                temperature=0.2,
                max_tokens=1000,
                response_format="json",
            )
            content = resp.content.strip()
        finally:
            (_inner._conversation_id, _inner._agent_name,
             _inner._user_id, _inner._event_cid) = _saved
        # Parse JSON array from response (handle markdown code blocks)
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.debug(f"[auto-extract] LLM extraction failed: {e}")
    return []


