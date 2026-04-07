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

Return a JSON array of objects with keys: "text", "hall", "room"
- hall: one of "facts", "events", "discoveries", "preferences", "advice"
- room: a short topic slug (e.g. "auth", "docker", "ci-pipeline")

Example:
[{"text": "User prefers JSON over SQLite for storage", "hall": "preferences", "room": "storage"},
 {"text": "Auth middleware rewrite driven by compliance", "hall": "facts", "room": "auth"}]

Summary:
"""


def auto_extract_memories(
    user_id: str,
    summary: str,
    agent_name: str = "",
    llm_client=None,
) -> int:
    """Extract and store memories from a compaction summary.

    Uses LLM if available, otherwise uses heuristic extraction.
    Returns the number of memories stored.
    """
    if not user_id or not summary:
        return 0

    facts = []
    if llm_client:
        facts = _extract_with_llm(llm_client, summary)

    if not facts:
        facts = _extract_heuristic(summary)

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
            store.remember(
                user_id=user_id,
                text=text,
                tags=["auto-extracted", "compaction"],
                source="compaction",
                agent=agent_name,
                hall=fact.get("hall", "facts"),
                room=fact.get("room", ""),
            )
            count += 1
        except Exception as e:
            logger.debug(f"[auto-extract] failed to store: {e}")

    if count:
        logger.info(f"[auto-extract] stored {count} memories for user {user_id[:8]}")
    return count


def _extract_with_llm(client, summary: str) -> list:
    """Use LLM to extract structured facts from summary."""
    try:
        from core.llm_client import LLMMessage
        messages = [
            LLMMessage(role="user", content=_EXTRACT_PROMPT + summary),
        ]
        resp = client.complete(
            messages=messages,
            temperature=0.2,
            max_tokens=1000,
            response_format="json",
        )
        content = resp.content.strip()
        # Parse JSON array from response (handle markdown code blocks)
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.debug(f"[auto-extract] LLM extraction failed: {e}")
    return []


def _extract_heuristic(summary: str) -> list:
    """Simple heuristic: extract sentences that look like facts/decisions."""
    sentences = re.split(r'(?<=[.!?])\s+', summary)
    # Filter for sentences that contain decision/preference/fact indicators
    indicators = (
        "prefer", "decided", "chose", "using", "switched",
        "works with", "responsible", "deadline", "must", "should",
        "always", "never", "important", "key", "role",
    )
    facts = []
    for s in sentences:
        s = s.strip()
        if len(s) < 20 or len(s) > 300:
            continue
        lower = s.lower()
        if any(ind in lower for ind in indicators):
            # Guess hall based on content
            hall = "facts"
            if any(w in lower for w in ("prefer", "like", "want", "chose")):
                hall = "preferences"
            elif any(w in lower for w in ("decided", "deadline", "release")):
                hall = "events"
            elif any(w in lower for w in ("should", "must", "always", "never")):
                hall = "advice"
            facts.append({"text": s, "hall": hall, "room": ""})
        if len(facts) >= 5:
            break
    return facts
