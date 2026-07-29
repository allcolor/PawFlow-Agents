"""Periodic self-reflection nudge (learning loop P5).

The diary already accepts a ``reflection`` entry type, and nothing ever asks
for one: an agent writes observations and decisions as it works, and the
synthesis across them never happens. This deepens the diary rather than adding
a system -- one prompt block, injected only when a reflection is actually due.

Due is measured in *diary activity*, not in wall-clock alone. A time-only
trigger nags an agent that has done nothing since yesterday; an
activity-only trigger fires three times in one busy afternoon. Both
conditions have to hold.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Entries (of any type) written since the last reflection before another one
# is worth asking for.
MIN_ENTRIES_SINCE = 5
# And at least this long since the last one, so a burst of work in one
# afternoon does not trigger three reflections.
MIN_INTERVAL_S = 6 * 3600
# How far back to look. A diary with more entries than this since its last
# reflection is well past due either way.
_SCAN_LIMIT = 200

BLOCK_TITLE = "Reflection due"

REFLECTION_HINT = (
    "You have written {count} diary entries since your last reflection."
    " Before this conversation moves on, write ONE `diary_write` entry with"
    " `type='reflection'` that synthesizes them: what you actually learned,"
    " what you would do differently, what keeps recurring. Not a summary of"
    " what happened -- the observations already say that."
    "\nThen check the synthesis for two things:"
    "\n- a durable relationship between entities worth a `kg_add` triple;"
    "\n- a repeatable procedure worth crystallizing as a skill via"
    " `manage_resource` (see the Skill loop section)."
    "\nIf neither applies, write the reflection anyway and say so. Do this"
    " once; it will not be asked again until you have accumulated more"
    " entries."
)


def entries_since_last_reflection(user_id: str, agent_name: str,
                                  diary=None) -> tuple:
    """``(count, last_reflection_ts)`` over the agent's own diary.

    ``count`` excludes the reflection itself. A diary that has never held a
    reflection returns ``(total, 0.0)``.
    """
    if not user_id or not agent_name:
        return 0, 0.0
    if diary is None:
        from core.agent_diary import AgentDiary
        diary = AgentDiary.instance()
    try:
        entries = diary.read(user_id, agent_name, limit=_SCAN_LIMIT)
    except Exception:
        logger.debug("[reflection] diary unreadable", exc_info=True)
        return 0, 0.0
    count = 0
    for entry in entries:  # newest first
        if entry.get("type") == "reflection":
            return count, float(entry.get("ts") or 0.0)
        count += 1
    return count, 0.0


def reflection_due(user_id: str, agent_name: str, *, now: float = 0.0,
                   diary=None) -> int:
    """Entries awaiting synthesis, or 0 when no reflection is due."""
    count, last_ts = entries_since_last_reflection(user_id, agent_name,
                                                   diary=diary)
    if count < MIN_ENTRIES_SINCE:
        return 0
    if last_ts > 0:
        now = now or time.time()
        if now - last_ts < MIN_INTERVAL_S:
            return 0
    return count


def pending_block(user_id: str, agent_name: str, *, now: float = 0.0,
                  diary=None) -> str:
    """The nudge to inject this turn, or "" when none is due. Never raises."""
    try:
        count = reflection_due(user_id, agent_name, now=now, diary=diary)
    except Exception:
        logger.debug("[reflection] due check failed", exc_info=True)
        return ""
    if not count:
        return ""
    return REFLECTION_HINT.format(count=count)
