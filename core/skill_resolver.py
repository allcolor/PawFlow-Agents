"""Skill resolver — resolves skill entries to prompt blocks.

Used by agent_context.py (main conv, task sub-conv) and
agent_executor.py (delegate sub-agents).
"""

import logging
import os
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def normalize_skill_entry(entry) -> Tuple[str, Dict[str, str], str]:
    """Normalize a skill entry to (name, params, condition).

    Accepts:
      - "skill_name"              → ("skill_name", {}, "")
      - {"name": "x", "params": {"k": "v"}, "condition": "${...}"}
        → ("x", {"k": "v"}, "${...}")
    """
    if isinstance(entry, str):
        return entry, {}, ""
    if isinstance(entry, dict):
        return entry.get("name", ""), entry.get("params") or {}, entry.get("condition", "")
    return "", {}, ""


def _evaluate_condition_for_scope(condition: str, user_id: str,
                                  conversation_id: str = "") -> bool:
    """Evaluate a condition expression with conversation scope available."""
    if not condition:
        return True
    from core.expression import resolve_value
    resolved = resolve_value(
        condition, owner=user_id, conversation_id=conversation_id or None)
    return bool(resolved) and resolved not in ("false", "False", "0")


def _safe_skill_path_part(value: str, fallback: str) -> str:
    safe = re.sub(r'[^a-zA-Z0-9_.-]+', '-', str(value or '')).strip('.-')
    return safe or fallback


def _skills_repo_base() -> str:
    """Server-side root of the skills repository tree."""
    from core.paths import REPOSITORY_DIR
    return str((REPOSITORY_DIR / "skills"))


def skill_mount_dir(skill_name: str, skill_def: Dict[str, Any] = None) -> str:
    """Return the container path where a skill directory is visible.

    The skills repository scope directories are bind-mounted read-only into
    CLI provider containers under /skills, mirroring the server layout (see
    core.cli_workspace_mounts.build_skill_mount_args). The in-container path
    therefore mirrors the skill's path under data/repository/skills, e.g.
    /skills/global/<name> or /skills/users/<uid>/<name>. SKILL.md asset
    references such as ${CLAUDE_SKILL_DIR}/scripts/foo.py resolve here.

    Falls back to a flat /skills/<name> when the skill root is unknown.
    """
    root = str((skill_def or {}).get("skill_root") or "")
    if root:
        try:
            rel = os.path.relpath(root, _skills_repo_base())
        except Exception:
            rel = ""
        if rel and not rel.startswith(".."):
            parts = [_safe_skill_path_part(p, "skill")
                     for p in rel.replace("\\", "/").split("/") if p]
            if parts:
                return "/skills/" + "/".join(parts)
    return "/skills/" + _safe_skill_path_part(skill_name, "skill")


def _skill_instructions(skill_def: Dict[str, Any]) -> str:
    return str(skill_def.get("instructions") or skill_def.get("prompt") or "").strip()


def _get_skill_any(rs, skill_name: str, user_id: str,
                   conversation_id: str = ""):
    return rs.get_any(
        "skill", skill_name, user_id, conversation_id=conversation_id)


def _resolve_prompt_chain(skill_name: str, rs, user_id: str,
                          conversation_id: str = "") -> str:
    """Return the canonical SKILL.md instructions for a skill."""
    skill_def = _get_skill_any(rs, skill_name, user_id, conversation_id)
    return _skill_instructions(skill_def or {})


def _skill_allowed_tools(skill_def: Dict[str, Any]) -> List[str]:
    """Return the skill's declared allowed-tools as a clean list."""
    raw = (skill_def.get("declared_allowed_tools")
           or skill_def.get("allowed-tools") or [])
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]


def _allowed_tools_directive(skill_def: Dict[str, Any]) -> str:
    """Return a tool-preference directive for a skill, or '' if none declared.

    Agent Skills `allowed-tools` is surfaced as advisory guidance, not an
    enforced restriction: PawFlow does not filter the tool registry while a
    skill is active (load_skill output persists in the main agent context).
    """
    tools = _skill_allowed_tools(skill_def)
    if not tools:
        return ""
    return (
        "\n\nPreferred tools (advisory): this skill is designed to work with "
        "these tools: " + ", ".join(tools) + ". Prefer them for the skill's "
        "work and avoid unrelated tools unless the task genuinely requires "
        "one; this is guidance, not an enforced restriction."
    )


# Skill asset inlining caps. The skill directory is normally reachable as a
# read-only mount, but an agent with no connected relay cannot read it; the
# bundled text files are then inlined into the loaded skill block instead.
_ASSET_INLINE_MAX_BYTES = 12_000
_ASSET_INLINE_TOTAL_BYTES = 48_000
_ASSET_TEXT_EXTENSIONS = {
    ".css", ".csv", ".html", ".js", ".json", ".md", ".mjs", ".ps1",
    ".py", ".rb", ".sh", ".sql", ".toml", ".ts", ".txt", ".xml",
    ".yaml", ".yml",
}


def _iter_skill_asset_files(skill_root: str) -> List[Tuple[str, str]]:
    """Return sorted (relpath, abspath) for files bundled with a skill.

    Excludes SKILL.md. Skips entries that resolve outside the skill root
    (symlink-escape guard).
    """
    raw = str(skill_root or "").strip()
    if not raw:
        return []
    base = os.path.realpath(raw)
    if not os.path.isdir(base):
        return []
    found: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for fn in sorted(filenames):
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, base).replace(os.sep, "/")
            if rel == "SKILL.md":
                continue
            real = os.path.realpath(abspath)
            try:
                if real != base and os.path.commonpath([base, real]) != base:
                    continue
            except ValueError:
                continue
            found.append((rel, abspath))
    return found


def _skill_assets_block(skill_def: Dict[str, Any]) -> str:
    """Return a block listing a skill's bundled asset files.

    The skill directory is normally reachable as a read-only mount (a Docker
    bind mount for CLI providers, the relay /skills FUSE for others). When
    that mount is unavailable — e.g. an agent with no connected relay — the
    agent still needs the assets, so this block enumerates every bundled file
    and inlines small text files as a context-only fallback.
    """
    files = _iter_skill_asset_files((skill_def or {}).get("skill_root") or "")
    if not files:
        return ""
    listing: List[str] = []
    inlined: List[str] = []
    inlined_total = 0
    for rel, abspath in files:
        try:
            size = os.path.getsize(abspath)
        except OSError:
            continue
        listing.append(f"- {rel} ({size} bytes)")
        ext = os.path.splitext(rel)[1].lower()
        if (ext in _ASSET_TEXT_EXTENSIONS
                and 0 < size <= _ASSET_INLINE_MAX_BYTES
                and inlined_total + size <= _ASSET_INLINE_TOTAL_BYTES):
            try:
                with open(abspath, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            inlined_total += size
            inlined.append(f"#### {rel}\n```\n{text}\n```")
    block = (
        "\n\n### Skill assets\n"
        "These files are bundled with the skill and also live in the "
        "read-only skill directory; the inlined copies below are a fallback "
        "for when that directory is not mounted.\n"
        + "\n".join(listing)
    )
    if inlined:
        block += "\n\n" + "\n\n".join(inlined)
    return block


def resolve_skill_prompts(
    skill_entries: List,
    user_id: str,
    conversation_id: str = "",
    agent_name: str = "",
) -> List[str]:
    """Resolve a list of skill entries to formatted prompt blocks.

    Args:
        skill_entries: List of skill names (str) or dicts with name+params.
        user_id: For ResourceStore lookup.
        conversation_id: Optional runtime context for programmable skills.
        agent_name: Optional current agent for programmable skills.

    Returns:
        List of formatted prompt strings ready to inject in system prompt.
    """
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    blocks = []
    seen = set()
    for entry in skill_entries:
        name, params, condition = normalize_skill_entry(entry)
        if not name or name in seen:
            continue
        seen.add(name)
        if condition and not _evaluate_condition_for_scope(
                condition, user_id, conversation_id):
            continue
        skill_def = _get_skill_any(rs, name, user_id, conversation_id)
        if skill_def and skill_def.get("_invalid"):
            logger.warning("Skipping invalid skill %r: %s",
                           name, skill_def.get("_invalid"))
            continue
        if not skill_def or not _skill_instructions(skill_def):
            continue
        prompt = _resolve_prompt_chain(
            name, rs, user_id, conversation_id=conversation_id)
        desc = skill_def.get("description", "")
        skill_dir = skill_mount_dir(name, skill_def)
        blocks.append(
            f"## Skill: {name}\n"
            f"{desc}\n"
            f"Skill directory: {skill_dir} "
            f"(read-only; assets like scripts/ and references/ live here)\n\n"
            f"{prompt}"
            f"{_allowed_tools_directive(skill_def)}"
            f"{_skill_assets_block(skill_def)}"
        )
    return blocks


def resolve_runnable_skill_prompt(skill_name: str, user_id: str,
                                  conversation_id: str,
                                  agent_name: str,
                                  arguments: str = "") -> str:
    """Resolve a visible skill for immediate one-shot invocation.

    Unlike load_skill, this does not require the skill to be assigned to the
    target agent. It is used for explicit user commands such as
    `/skill run [@agent] name args...`.
    """
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    skill_def = _get_skill_any(rs, skill_name, user_id, conversation_id)
    if not skill_def or not _skill_instructions(skill_def):
        return ""
    # SKILL.md content is delivered verbatim — no placeholder substitution.
    # The skill directory and run arguments are passed as explicit lines below.
    prompt = _resolve_prompt_chain(
        skill_name, rs, user_id, conversation_id=conversation_id)
    skill_dir = skill_mount_dir(skill_name, skill_def)
    desc = skill_def.get("description", "")
    arg_line = arguments or ""
    return (
        f"## Skill Invocation: {skill_name}\n"
        f"Target agent: {agent_name}\n"
        f"Arguments: {arg_line}\n"
        f"Skill directory: {skill_dir}\n\n"
        f"{desc}\n\n"
        f"{prompt}"
        f"{_allowed_tools_directive(skill_def)}"
        f"{_skill_assets_block(skill_def)}\n\n"
        "Run this skill now for the provided arguments. "
        "Use normal PawFlow tools if the skill requires files, commands, or scripts."
    )


def _skill_summary(skill_def: Dict[str, Any]) -> str:
    desc = str(skill_def.get("description", "") or "").strip()
    if desc:
        return desc[:500]
    return "No description provided."


def resolve_skill_manifests(
    skill_entries: List,
    user_id: str,
    conversation_id: str = "",
) -> List[str]:
    """Resolve assigned skills to lightweight availability manifest lines."""
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    lines = []
    seen = set()
    for entry in skill_entries:
        name, _params, condition = normalize_skill_entry(entry)
        if not name or name in seen:
            continue
        seen.add(name)
        if condition and not _evaluate_condition_for_scope(
                condition, user_id, conversation_id):
            continue
        skill_def = _get_skill_any(rs, name, user_id, conversation_id)
        if not skill_def:
            continue
        if skill_def.get("_invalid"):
            logger.warning("Assigned skill %r is invalid and not advertised: %s",
                           name, skill_def.get("_invalid"))
            continue
        summary = _skill_summary(skill_def)
        lines.append(
            f"- {name}: {summary}\n"
            f"  Use `load_skill(name=\"{name}\")` to load the full skill when relevant."
        )
    return lines


def available_skill_context_message(skill_name: str,
                                    skill_def: Dict[str, Any]) -> str:
    """Return the context delta sent when a skill becomes available."""
    summary = _skill_summary(skill_def or {})
    return (
        f"Skill available: {skill_name}\n"
        f"Description: {summary}\n"
        f"Use `load_skill(name=\"{skill_name}\")` to load the full skill when relevant."
    )


def removed_skill_context_message(skill_name: str) -> str:
    """Return the context delta sent when a skill is removed."""
    return (
        f"Skill removed: {skill_name}\n"
        "This skill is no longer available to this agent."
    )


def inject_available_skills_into_prompt(system_prompt: str, skill_entries: List,
                                        user_id: str,
                                        conversation_id: str = "") -> str:
    """Append only lightweight skill manifests to the provider system prompt."""
    lines = resolve_skill_manifests(
        skill_entries, user_id, conversation_id=conversation_id)
    if lines:
        system_prompt += "\n\n# Available Skills\n\n" + "\n".join(lines)
    return system_prompt


def _agent_assigned_skill_entry(skill_name: str, user_id: str,
                                conversation_id: str,
                                agent_name: str):
    if not skill_name or not agent_name:
        return None
    from core.resource_store import ResourceStore
    rs = ResourceStore.instance()
    def_name = agent_name
    if conversation_id:
        try:
            from core.conv_agent_config import get_agent_config
            def_name = get_agent_config(conversation_id, agent_name).get("definition") or agent_name
        except Exception:
            def_name = agent_name
    agent_def = rs.get_any("agent", def_name, user_id,
                           conversation_id=conversation_id) or rs.get_any(
                               "agent", def_name, user_id) or {}
    for entry in agent_def.get("assigned_skills") or []:
        name, _params, condition = normalize_skill_entry(entry)
        if name != skill_name:
            continue
        if condition and not _evaluate_condition_for_scope(
                condition, user_id, conversation_id):
            return None
        return entry
    return None


def resolve_assigned_skill_prompt(skill_name: str, user_id: str,
                                  conversation_id: str,
                                  agent_name: str) -> str:
    """Resolve a full skill prompt only if assigned to the current agent."""
    entry = _agent_assigned_skill_entry(
        skill_name, user_id, conversation_id, agent_name)
    if not entry:
        return ""
    blocks = resolve_skill_prompts(
        [entry], user_id, conversation_id=conversation_id,
        agent_name=agent_name)
    return blocks[0] if blocks else ""
