"""Project graph digest — compact summary for system prompt injection.

Gives the agent a passive view of the codebase structure (when a
project_graph has been built for the conv) without forcing a
project_graph(action='report') call. Only fires when has_graph() is
true — silent for conversations that haven't built one.

The digest content is filtered for SIGNAL: builtin/dict/list/str method
nodes are skipped from god-nodes (they dominate by sheer ubiquity but
don't tell the agent anything about the project's architecture), and a
top-modules list is surfaced because 'where to look first' is decided
at the file/module level, not the symbol level.
"""

import logging
import os.path
from collections import Counter, defaultdict
from typing import List, Set

logger = logging.getLogger(__name__)


# Builtins / stdlib noise that dominates god-nodes by ubiquity but tells
# the agent nothing about the project's architecture. Skipped from the
# god-nodes section so the signal is the project-specific identifiers
# (ConversationStore, LLMMessage, BaseTask, ...).
_BUILTIN_NOISE: Set[str] = {
    # dict / list / set common methods
    ".get", ".set", ".pop", ".append", ".extend", ".remove", ".keys",
    ".values", ".items", ".copy", ".update", ".clear", ".add", ".discard",
    # str common methods
    ".strip", ".split", ".join", ".replace", ".startswith", ".endswith",
    ".lower", ".upper", ".format", ".encode", ".decode", ".find",
    # generic introspection
    ".isinstance", ".hasattr", ".getattr", ".setattr", ".len", ".type",
    # Python type names — useless as god nodes
    "str", "int", "float", "bool", "bytes", "list", "dict", "set",
    "tuple", "frozenset", "None", "True", "False",
    "object", "type", "property", "staticmethod", "classmethod",
    # Python builtins frequently shown as call/import targets
    "print", "open", "range", "len", "isinstance", "hasattr",
    "getattr", "setattr", "super", "format", "repr", "id",
    "int", "min", "max", "sum", "any", "all", "sorted", "reversed",
    "enumerate", "zip", "map", "filter", "iter", "next",
    # Common stdlib modules pulled in via import edges everywhere
    "os", "sys", "json", "re", "time", "logging", "typing", "pathlib",
    "threading", "asyncio", "functools", "collections", "itertools",
    "datetime", "uuid", "hashlib", "struct", "socket", "subprocess",
}


_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "c++", ".cc": "c++",
    ".cxx": "c++", ".hpp": "c++",
    ".rb": "ruby", ".cs": "csharp",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala", ".php": "php", ".swift": "swift",
    ".lua": "lua", ".zig": "zig",
    ".ps1": "powershell", ".ex": "elixir", ".exs": "elixir",
}


def _is_noise(node_id: str, label: str) -> bool:
    """Heuristic: builtin/method-of-builtin noise that shouldn't be
    surfaced as a god node."""
    if not node_id:
        return True
    # Normalize callable labels: 'foo()' / '.foo()' → 'foo' / '.foo'.
    nid = node_id[:-2] if node_id.endswith("()") else node_id
    lab = label[:-2] if label.endswith("()") else label
    if nid in _BUILTIN_NOISE or lab in _BUILTIN_NOISE:
        return True
    # Bare 1-2 char names — almost always loop variables / abbreviations
    # that bubble up through call edges (`i`, `x`, `e`, `f` ...).
    if len(lab) <= 2:
        return True
    return False


def _lang_from_path(rel_path: str) -> str:
    if not rel_path:
        return ""
    _, ext = os.path.splitext(rel_path.lower())
    return _EXT_TO_LANG.get(ext, "")


def build_project_graph_digest(user_id: str, conv_id: str,
                                 max_chars: int = 600,
                                 top_god: int = 5,
                                 top_modules: int = 5) -> str:
    """Build a compact project-graph summary for system-prompt injection.

    Returns "" when no graph has been built for this conv.
    Output sections (each line, ordered for skim-friendliness):
      1. counts + language breakdown,
      2. top modules by entity density (where the action is),
      3. project-specific god nodes (filtered to drop builtin noise).
    """
    if not user_id or not conv_id:
        return ""
    try:
        from core.project_graph import ProjectGraph
        pg = ProjectGraph.for_conversation(user_id, conv_id)
    except Exception:
        logger.debug("[pg-digest] load failed", exc_info=True)
        return ""

    if not pg.has_graph():
        return ""

    nodes = pg.nodes
    edges = pg.edges
    if not nodes:
        return ""

    # Languages distribution — prefer node metadata, fall back to file
    # extension lookup so the digest never says 'unknown' when the data
    # is right there in source_file.
    langs: Counter = Counter()
    for n in nodes:
        lang = (n.get("language") or n.get("lang") or "").lower()
        if not lang:
            lang = _lang_from_path(n.get("source_file", ""))
        if lang:
            langs[lang] += 1
    lang_summary = ", ".join(f"{k} ({v})"
                              for k, v in langs.most_common(5)) or "unknown"

    # Top modules — group nodes by source_file's directory (or the file
    # itself for shallow trees), surface the densest ones. This is
    # what the agent should look at first when asked to navigate the
    # codebase — a god-node tells you a name; a top-module tells you
    # where to open the editor.
    by_module: Counter = Counter()
    for n in nodes:
        src = n.get("source_file", "") or ""
        if not src:
            continue
        # Group by file path — fine grain (per-file) is more actionable
        # than per-directory for navigating the codebase.
        by_module[src] += 1
    top_module_pairs = by_module.most_common(top_modules)

    # God nodes — most-connected entities, with builtin noise filtered.
    degree: dict = defaultdict(int)
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    label_by_id = {n["id"]: n.get("label", n["id"]) for n in nodes}
    ranked = sorted(degree.items(), key=lambda x: -x[1])
    god_pairs = []
    for nid, d in ranked:
        label = label_by_id.get(nid, nid)
        if _is_noise(nid, label):
            continue
        god_pairs.append((label, d))
        if len(god_pairs) >= top_god:
            break

    lines: List[str] = [
        f"Codebase indexed: {len(nodes)} entities, {len(edges)} edges. "
        f"Languages: {lang_summary}."
    ]
    if top_module_pairs:
        lines.append(
            "Top files: "
            + ", ".join(f"{path} ({n})" for path, n in top_module_pairs))
    if god_pairs:
        lines.append(
            "God nodes: "
            + ", ".join(f"{label} ({d})" for label, d in god_pairs))

    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars - 3] + "..."
    return digest
