"""Skill learning loop — crystallize experience into reusable skills.

Two pieces:
- a system-prompt hint telling agents when to create/update skills
  (injected next to the cognitive-tools hint);
- a conservative post-compaction proposer that asks the summarizer LLM
  whether the summary contains a reusable procedure not covered by an
  existing skill, and stores the draft as a conversation-scoped memory
  (tag ``skill-draft``) so the agent sees it in its digest and can decide
  to create the skill via ``manage_resource``. Drafts are never
  auto-installed.
"""

import json
import logging
import re
import time
import uuid

logger = logging.getLogger(__name__)

_DRAFT_TTL_DAYS = 90
_MAX_EXISTING_SKILLS_LISTED = 40
_MAX_DRAFT_TEXT_CHARS = 900

SKILL_LOOP_HINT = (
    "\n\n## Skill loop"
    "\nSkills are reusable procedure documents assigned to agents "
    "(see Available Skills / `load_skill`). Keep the loop closed:"
    "\n- **Crystallize**: when you have just completed a novel multi-step "
    "procedure (worked around a quirk, found a working sequence after "
    "failures) that is likely to recur, save it as a skill via "
    "`manage_resource` (resource_type='skill', conversation scope by "
    "default). Name the trigger conditions in the description."
    "\n- **Improve**: if a loaded skill's instructions proved wrong or "
    "outdated during use, update that skill via `manage_resource` right "
    "after the task — do not leave it broken for the next run."
    "\n- **Drafts**: memories tagged `skill-draft` are proposals extracted "
    "from past conversations; create the skill if the procedure recurs, "
    "otherwise `forget` the draft."
)

SKILL_IMPROVE_FOOTER = (
    "\n\n---\n"
    "If these instructions proved wrong or outdated during use, update "
    "this skill via `manage_resource` after the task."
)

_SKILL_DRAFT_PROMPT = """You review a conversation compaction summary to decide whether it contains ONE reusable multi-step procedure worth saving as an agent skill.

Only propose a skill when ALL of these hold:
- the procedure was discovered through real work (workaround, non-obvious sequence, hard-won configuration), not generic knowledge;
- it is likely to recur in future conversations;
- it is NOT already covered by an existing skill listed below.

Existing skills:
{existing}

Return a JSON object:
{{"skill": null}} when nothing qualifies (the common case), or
{{"skill": {{"name": "kebab-case-name", "description": "one line: what it does and when to use it", "steps": ["step 1", "step 2"], "trigger": "condition that should make an agent load this skill"}}}}

Be conservative: prefer null over a weak proposal.

Summary:
"""


def propose_skill_draft_from_summary(
    user_id: str,
    summary: str,
    llm_client=None,
    conversation_id: str = "",
    agent_name: str = "",
) -> bool:
    """Best-effort: store at most one skill-draft memory from a summary.

    Returns True when a draft was stored. Never raises.
    """
    if not user_id or not summary or llm_client is None:
        return False
    try:
        draft = _propose_with_llm(llm_client, summary, user_id, conversation_id)
        if not draft:
            return False
        text = _draft_memory_text(draft)
        if not text or _draft_exists(user_id, draft["name"]):
            return False
        from core.memory_store import MemoryStore
        MemoryStore.instance().remember(
            user_id=user_id,
            text=text,
            tags=["skill-draft", "auto-extracted"],
            source="compaction",
            agent="",
            conversation_id=conversation_id or "",
            category="discoveries",
            expires_at=time.time() + _DRAFT_TTL_DAYS * 86400,
        )
        logger.info("[skill-loop] stored skill draft '%s' for user %s",
                    draft["name"], user_id[:8])
        return True
    except Exception:
        logger.debug("[skill-loop] draft proposal failed", exc_info=True)
        return False


def _existing_skill_lines(user_id: str, conversation_id: str) -> str:
    try:
        from core.resource_store import ResourceStore
        skills = ResourceStore.instance().list_all(
            "skill", user_id, conversation_id=conversation_id) or []
    except Exception:
        skills = []
    lines = []
    for s in skills[:_MAX_EXISTING_SKILLS_LISTED]:
        name = str(s.get("name", "")).strip()
        if not name:
            continue
        desc = str(s.get("description", "") or "").strip()[:120]
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(lines) if lines else "(none)"


def _propose_with_llm(client, summary: str, user_id: str,
                      conversation_id: str):
    """Isolated ephemeral LLM call, mirrors memory auto-extract."""
    from core.llm_client import LLMMessage
    existing = _existing_skill_lines(user_id, conversation_id)
    prompt = _SKILL_DRAFT_PROMPT.format(existing=existing) + summary
    _inner = getattr(client, "_client", client)
    _call_client = _inner.clone_for_call()
    safe_cid = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in (conversation_id or "skill"))[:48]
    scope_id = f"_skill_draft_{safe_cid}_{uuid.uuid4().hex[:8]}"
    resp = _call_client.complete(
        messages=[LLMMessage(role="user", content=prompt,
                             conversation_id=scope_id)],
        temperature=0.2,
        max_tokens=800,
        response_format="json",
        call_user_id=user_id,
        call_conversation_id=scope_id,
        call_agent_name="skill-loop",
        call_event_cid="",
        call_ephemeral_stream=True,
    )
    content = (resp.content or "").strip()
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    data = json.loads(match.group())
    skill = data.get("skill") if isinstance(data, dict) else None
    if not isinstance(skill, dict):
        return None
    name = str(skill.get("name", "")).strip().lower()
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-")[:64]
    description = str(skill.get("description", "")).strip()
    steps = [str(s).strip() for s in (skill.get("steps") or [])
             if str(s).strip()]
    trigger = str(skill.get("trigger", "")).strip()
    if not name or not description or not steps:
        return None
    return {"name": name, "description": description,
            "steps": steps[:8], "trigger": trigger}


def _draft_memory_text(draft) -> str:
    steps = "; ".join(
        f"{i}) {s}" for i, s in enumerate(draft["steps"], start=1))
    trigger = f" Trigger: {draft['trigger']}." if draft.get("trigger") else ""
    text = (
        f"Skill draft: `{draft['name']}` — {draft['description']}.{trigger} "
        f"Steps: {steps}. If this procedure recurs, create the skill via "
        f"manage_resource; otherwise forget this draft."
    )
    return text[:_MAX_DRAFT_TEXT_CHARS]


def _draft_exists(user_id: str, name: str) -> bool:
    try:
        from core.memory_store import MemoryStore
        prefix = f"Skill draft: `{name}`"
        return any(
            getattr(e, "text", "").startswith(prefix)
            for e in MemoryStore.instance().list_all(user_id))
    except Exception:
        return False
