"""One-pass memory and skill learning from compaction summaries.

The two maintenance roles may be bound to different LLM services. Their reads
are combined only when both roles resolve to the same live client instance;
explicitly distinct bindings keep their independent calls.
"""

import json
import logging
import re
import uuid
from typing import Any, Dict

logger = logging.getLogger(__name__)

_COMBINED_PROMPT = """Review one conversation compaction summary and perform TWO independent analyses.

Return exactly one JSON object:
{
  "memories": [
    {
      "text": "concise self-contained memory",
      "category": "facts|events|discoveries|preferences|advice",
      "importance": "low|medium|high|critical",
      "durability": "ephemeral|session|project|durable",
      "scope": "global|agent|conversation|private",
      "ttl_days": 0,
      "tags": ["optional", "stable", "tags"]
    }
  ],
  "skill": null
}

The "memories" array contains at most 2 durable, future-useful facts. Prefer
stable user preferences, operating rules, project decisions, and architectural
constraints. Exclude current task status, validation results, stack traces,
temporary debugging details, tool chronology, line numbers, and commits. Use
global scope only for durable high/critical user preferences or rules. Return
an empty array when nothing qualifies.

The "skill" value is either null or ONE object:
{
  "name": "kebab-case-name",
  "description": "one line: what it does and when to use it",
  "steps": ["step 1", "step 2"],
  "trigger": "condition that should make an agent load this skill"
}

Propose a skill only for a reusable multi-step procedure discovered through
real work, likely to recur, described with concrete actions or checks, and not
already covered by an existing skill. Product or domain overlap alone is not
coverage. Release, deployment, migration, incident-response, validation, and
recovery procedures are good candidates.

Existing skills:
{existing}

Analyze each field independently: no qualifying memory does not prevent a
skill proposal, and no qualifying skill does not prevent durable memories.

Summary:
"""


def process_post_compaction_learning(
    *,
    user_id: str,
    summary: str,
    conversation_id: str = "",
    agent_name: str = "",
    embed_fn=None,
) -> Dict[str, Any]:
    """Run post-compaction memory and skill learning with bounded LLM reads."""
    result = {
        "mode": "skipped",
        "llm_calls": 0,
        "memory_count": 0,
        "memory_outcome": "skipped",
        "skill_outcome": "skipped",
    }
    if not user_id or not summary:
        return result

    from core.memory_auto_extract import _memory_extraction_allowed

    memory_allowed = _memory_extraction_allowed(
        user_id, summary, conversation_id)
    memory_client = (
        _resolve_client("auto_memory", user_id, conversation_id)
        if memory_allowed else None
    )
    skill_client = _resolve_client(
        "skill_learning", user_id, conversation_id)

    if memory_client is not None and memory_client is skill_client:
        return _process_combined(
            result, memory_client, user_id=user_id, summary=summary,
            conversation_id=conversation_id, agent_name=agent_name,
            embed_fn=embed_fn)

    result["mode"] = "separate"
    from core.memory_auto_extract import _auto_extract_with_client
    from core.skill_loop import _propose_skill_with_client

    if memory_client is not None:
        result["llm_calls"] += 1
        try:
            count = _auto_extract_with_client(
                memory_client, user_id=user_id, summary=summary,
                agent_name=agent_name, embed_fn=embed_fn,
                conversation_id=conversation_id)
            result["memory_count"] = count
            result["memory_outcome"] = "stored" if count else "rejected"
        except Exception:
            result["memory_outcome"] = "error"
            logger.warning(
                "[post-compaction] memory analysis failed user=%s cid=%s",
                user_id[:8], conversation_id[:8], exc_info=True)

    if skill_client is not None:
        result["llm_calls"] += 1
        try:
            result["skill_outcome"] = _propose_skill_with_client(
                skill_client, user_id=user_id, summary=summary,
                conversation_id=conversation_id)
        except Exception:
            result["skill_outcome"] = "error"
            logger.warning(
                "[post-compaction] skill analysis failed user=%s cid=%s",
                user_id[:8], conversation_id[:8], exc_info=True)

    return result


def _process_combined(
    result: Dict[str, Any],
    client,
    *,
    user_id: str,
    summary: str,
    conversation_id: str,
    agent_name: str,
    embed_fn,
) -> Dict[str, Any]:
    result["mode"] = "combined"
    result["llm_calls"] = 1
    try:
        analysis = _analyze_with_llm(
            client, summary, user_id, conversation_id)
    except Exception:
        result["memory_outcome"] = "error"
        result["skill_outcome"] = "error"
        logger.warning(
            "[post-compaction] combined analysis failed user=%s cid=%s",
            user_id[:8], conversation_id[:8], exc_info=True)
        return result

    from core.memory_auto_extract import _store_extracted_memories
    from core.skill_loop import (
        _normalize_skill_analysis,
        _store_skill_draft_analysis,
    )

    facts = analysis.get("memories")
    if isinstance(facts, list):
        try:
            count = _store_extracted_memories(
                user_id=user_id, facts=facts, agent_name=agent_name,
                embed_fn=embed_fn, conversation_id=conversation_id)
            result["memory_count"] = count
            result["memory_outcome"] = "stored" if count else "rejected"
        except Exception:
            result["memory_outcome"] = "error"
            logger.warning(
                "[post-compaction] memory storage failed user=%s cid=%s",
                user_id[:8], conversation_id[:8], exc_info=True)
    else:
        result["memory_outcome"] = "invalid"
        logger.info(
            "[post-compaction] memory outcome=invalid user=%s cid=%s",
            user_id[:8], conversation_id[:8])

    try:
        outcome, draft = _normalize_skill_analysis(analysis)
        result["skill_outcome"] = _store_skill_draft_analysis(
            user_id=user_id, outcome=outcome, draft=draft,
            conversation_id=conversation_id)
    except Exception:
        result["skill_outcome"] = "error"
        logger.warning(
            "[post-compaction] skill storage failed user=%s cid=%s",
            user_id[:8], conversation_id[:8], exc_info=True)

    return result


def _resolve_client(role: str, user_id: str, conversation_id: str):
    try:
        from core.linked_service_bindings import resolve_llm_override
        client, _definition, _service_id, _explicit = resolve_llm_override(
            role, user_id, conversation_id)
        if client is None:
            from core.summarizer_bindings import resolve_llm_client
            client, _context_size, _service_id = resolve_llm_client(
                user_id, conversation_id)
        return client
    except Exception:
        logger.debug(
            "[post-compaction] LLM resolution failed role=%s cid=%s",
            role, conversation_id[:8], exc_info=True)
        return None


def _analyze_with_llm(
    client,
    summary: str,
    user_id: str,
    conversation_id: str,
) -> Dict[str, Any]:
    from core.llm_client import LLMMessage
    from core.skill_loop import _existing_skill_lines

    existing = _existing_skill_lines(user_id, conversation_id)
    prompt = _COMBINED_PROMPT.replace("{existing}", existing) + summary
    inner = getattr(client, "_client", client)
    call_client = inner.clone_for_call()
    safe_cid = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in (conversation_id or "learning"))[:48]
    scope_id = f"_post_compaction_{safe_cid}_{uuid.uuid4().hex[:8]}"
    response = call_client.complete(
        messages=[LLMMessage(
            role="user", content=prompt, conversation_id=scope_id)],
        temperature=0.2,
        max_tokens=0,
        response_format="json",
        call_user_id=user_id,
        call_conversation_id=scope_id,
        call_agent_name="post-compaction-learning",
        call_event_cid="",
        call_ephemeral_stream=True,
    )
    match = re.search(r"\{.*\}", str(response.content or "").strip(), re.DOTALL)
    if not match:
        raise ValueError("combined analysis did not return a JSON object")
    analysis = json.loads(match.group())
    if not isinstance(analysis, dict):
        raise ValueError("combined analysis must be a JSON object")
    return analysis
