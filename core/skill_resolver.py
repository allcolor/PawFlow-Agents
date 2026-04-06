"""Skill resolver — resolves skill entries to prompt blocks.

Used by agent_context.py (main conv, task sub-conv) and
agent_executor.py (delegate sub-agents).
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def normalize_skill_entry(entry) -> Tuple[str, Dict[str, str]]:
    """Normalize a skill entry to (name, params).

    Accepts:
      - "skill_name"              → ("skill_name", {})
      - {"name": "x", "params": {"k": "v"}} → ("x", {"k": "v"})
    """
    if isinstance(entry, str):
        return entry, {}
    if isinstance(entry, dict):
        return entry.get("name", ""), entry.get("params") or {}
    return "", {}


def _substitute_params(prompt: str, params: Dict[str, str],
                       defaults: Dict[str, Any]) -> str:
    """Replace ${param_name} in prompt with values from params, falling back to defaults."""
    if not params and not defaults:
        return prompt
    merged = {}
    for k, v in defaults.items():
        if isinstance(v, dict):
            merged[k] = v.get("default", "")
        else:
            merged[k] = str(v)
    merged.update({k: str(v) for k, v in params.items()})
    if not merged:
        return prompt

    def _replace(m):
        key = m.group(1)
        return merged.get(key, m.group(0))

    return re.sub(r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}', _replace, prompt)


def resolve_skill_prompts(
    skill_entries: List,
    user_id: str,
) -> List[str]:
    """Resolve a list of skill entries to formatted prompt blocks.

    Args:
        skill_entries: List of skill names (str) or dicts with name+params.
        user_id: For ResourceStore lookup.

    Returns:
        List of formatted prompt strings ready to inject in system prompt.
    """
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    blocks = []
    for entry in skill_entries:
        name, params = normalize_skill_entry(entry)
        if not name:
            continue
        skill_def = rs.get_any("skill", name, user_id)
        if not skill_def or not skill_def.get("prompt"):
            continue
        prompt = skill_def["prompt"]
        # Substitute parameters
        declared_params = skill_def.get("parameters") or {}
        if params or declared_params:
            prompt = _substitute_params(prompt, params, declared_params)
        desc = skill_def.get("description", "")
        blocks.append(
            f"## Skill: {name}\n"
            f"{desc}\n\n"
            f"{prompt}"
        )
    return blocks


def inject_skills_into_prompt(system_prompt: str, skill_entries: List,
                              user_id: str) -> str:
    """Append resolved skill blocks to system prompt. Returns modified prompt."""
    blocks = resolve_skill_prompts(skill_entries, user_id)
    if blocks:
        system_prompt += "\n\n# Assigned Skills\n\n" + "\n\n".join(blocks)
    return system_prompt
