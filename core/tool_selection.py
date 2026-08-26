"""Canonical, availability-aware routing for overlapping agent tools."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


# Each route is: (label, exact runtime tool names, use_when, not_for).
TOOL_FAMILIES = {
    "discovery": {
        "title": "Tool discovery",
        "routes": (
            ("schema lookup", ("get_tool_schema",),
             "inspect one tool or compare a family before calling it",
             "executing the target operation"),
            ("lazy execution", ("use_tool",),
             "execute a lazily exposed tool after reading its schema",
             "wrapping either meta-tool inside itself"),
            ("platform help", ("pawflow_help",),
             "discover PawFlow tasks, services, flows, or capabilities",
             "guessing an exact runtime schema"),
        ),
    },
    "files": {
        "title": "Files, code, execution, and artifacts",
        "routes": (
            ("contextual search", ("search",),
             "combine regex, glob filtering, and contextual snippets",
             "a file-name-only listing"),
            ("file listing", ("glob",),
             "list paths matching a file pattern", "searching contents"),
            ("simple content search", ("grep",),
             "find a simple pattern in file contents",
             "combined discovery that needs ranked context"),
            ("model file read", ("read",),
             "return file content to the model",
             "opening the file in the user's viewer"),
            ("user file viewer", ("show_file",),
             "open an existing file for the user",
             "reading its content into model context"),
            ("downloadable artifact", ("share_file",),
             "upload an artifact and return a download URL",
             "ordinary workspace editing"),
            ("single edit", ("edit",), "make one exact replacement",
             "three or more separate changes in one file"),
            ("repeated edit", ("batch_edit",),
             "apply repeated replacements across files",
             "unrelated multi-hunk changes"),
            ("atomic patch", ("apply_patch",),
             "apply several related hunks atomically",
             "a single exact replacement"),
            ("test runner", ("run_tests",),
             "run project tests with structured failure reporting",
             "generic non-test commands"),
            ("shell command", ("bash",),
             "run a command with no suitable dedicated tool",
             "reading, searching, or editing via shell utilities"),
            ("media inspection", ("see",),
             "analyze an image, video, or audio artifact",
             "controlling a live desktop or browser"),
            ("desktop control", ("screen",),
             "inspect and control a live desktop",
             "browser-DOM-specific automation"),
            ("browser control", ("browser",),
             "navigate and interact with a browser session",
             "general desktop control"),
        ),
    },
    "delegation": {
        "title": "Delegation",
        "routes": (
            ("existing conversation agent", ("delegate",),
             "ask a named agent that keeps its context and tools",
             "disposable independent parallel work"),
            ("temporary parallel worker", ("flash_delegate",),
             "run independent self-contained work in a disposable context",
             "tightly coupled edits with one shared invariant"),
            ("tool-free second opinion", ("consult_agent",),
             "let a thin interface ask the configured agent brain once",
             "recursive self-consultation from a full agent turn"),
            ("remote agent task", ("a2a",),
             "call a configured agent outside this conversation or runtime",
             "an agent already present in this conversation"),
        ),
    },
    "work": {
        "title": "Todo, plans, tasks, and flows",
        "routes": (
            ("unfinished-work ledger", ("todolist",),
             "track this agent's unfinished work across compaction",
             "orchestrating agents or storing notes"),
            ("workflow proposal tools", (
                "propose_workflow", "get_workflow_proposal",
                "review_workflow_proposal"),
             "orchestrate visible steps with approval or verification",
             "a private lightweight work ledger"),
            ("autonomous task tools",
             ("assign_task", "complete_task", "verify_task"),
             "run a predefined task over scheduled work sessions",
             "an ad-hoc todo without a task definition"),
            ("deterministic flow", ("manage_flow",),
             "deploy repeatable DAG automation with explicit structure",
             "one-off exploratory agent work"),
        ),
    },
    "waiting": {
        "title": "Waiting, resuming, and user contact",
        "routes": (
            ("short blocking monitor", ("Monitor",),
             "wait up to about 60 seconds or for an early regex match",
             "keeping a turn alive for long-running work"),
            ("passive work continuation", ("schedule_continuation",),
             "end the turn and resume current long-running work later",
             "a user-requested calendar time"),
            ("future check-in", ("ScheduleWakeup",),
             "check at a specific future time or recurring interval",
             "polling a running command log"),
            ("blocking user decision", ("ask_user",),
             "pause because work needs clarification or approval",
             "an informational alert"),
            ("informational alert", ("notify_user",),
             "inform the user without blocking for an answer",
             "requesting a decision"),
        ),
    },
    "cognition": {
        "title": "Knowledge and work state",
        "routes": (
            ("memory", (
                "remember", "recall", "semantic_recall", "check_duplicate",
                "forget"),
             "store or retrieve durable facts, preferences, events, discoveries, or advice",
             "unfinished work or the agent's own reflections"),
            ("knowledge graph", (
                "kg_add", "kg_query", "query_graph", "kg_invalidate",
                "kg_timeline", "kg_stats", "kg_god_nodes"),
             "store or traverse subject-to-relationship-to-object facts",
             "facts that do not fit a clean relationship"),
            ("agent diary", ("diary_write", "diary_read"),
             "record a non-obvious decision or lesson for future work",
             "routine summaries, user facts, task state, or transient evidence"),
            ("todo state", ("todolist",),
             "record authoritative unfinished work before multi-step work",
             "an orchestrated plan or a notes store"),
            ("scratchpad", ("scratchpad",),
             "keep expiring evidence, hypotheses, decisions, or resume cues",
             "durable facts; note bodies are never auto-injected"),
            ("project graph", ("project_graph",),
             "inspect relay-scoped AST structure or refactor impact",
             "text, comments, configs, or changes newer than the graph"),
            ("project wiki", ("project_wiki",),
             "query relay-scoped sourced architecture and project knowledge",
             "acting on stale claims without checking source"),
            ("user-pattern learning", ("learn",),
             "extract user preferences and communication patterns",
             "ordinary fact storage"),
            ("conversation history", ("conversation_search", "read_history"),
             "find what was said in past or compacted conversation history",
             "facts already available in memory"),
        ),
    },
    "resources": {
        "title": "Resources and platform management",
        "routes": (
            ("resource CRUD", ("manage_resource",),
             "create or update agents, skills, MCPs, task definitions, or tools",
             "installing a signed package"),
            ("package lifecycle", ("manage_package",),
             "build, inspect, install, update, or remove a signed .pfp",
             "editing one resource directly"),
            ("relay binding", ("link_resource",),
             "link or select a relay for this conversation",
             "linking automatically available agents, skills, or tools"),
            ("secret storage", ("store_secret", "list_secrets"),
             "store a secret or inspect secret names without revealing values",
             "placing secret values in prompts or files"),
        ),
    },
}

INJECTED_TOOL_FAMILIES = ("delegation", "work", "waiting", "cognition")


def _name_set(available_tools: Iterable[str]) -> Set[str]:
    return {str(name) for name in available_tools if str(name)}


def declared_tool_names() -> Set[str]:
    """Return every runtime name referenced by the routing registry."""
    return {
        name
        for family in TOOL_FAMILIES.values()
        for _label, tools, _use_when, _not_for in family["routes"]
        for name in tools
    }


def render_tool_family(family_name: str,
                       available_tools: Iterable[str]) -> Dict[str, Any]:
    """Return one comparison filtered to the caller's actual registry."""
    family_name = str(family_name or "").strip().lower()
    family = TOOL_FAMILIES.get(family_name)
    if family is None:
        return {
            "error": f"Unknown tool family '{family_name}'",
            "available_families": sorted(TOOL_FAMILIES),
        }
    names = _name_set(available_tools)
    routes: List[Dict[str, Any]] = []
    for label, tools, use_when, not_for in family["routes"]:
        available = [name for name in tools if name in names]
        if available:
            routes.append({
                "label": label,
                "tools": available,
                "use_when": use_when,
                "not_for": not_for,
            })
    return {
        "family": family_name,
        "title": family["title"],
        "available": bool(routes),
        "routes": routes,
    }


def _compact_selector(route: Dict[str, Any]) -> str:
    tools = route["tools"]
    if len(tools) == 1:
        return f"`{tools[0]}`"
    if len(tools) <= 3:
        return " / ".join(f"`{name}`" for name in tools)
    return f"{route['label']} (`{tools[0]}` and related tools)"


def build_tool_selection_hint(available_tools: Iterable[str]) -> str:
    """Build the compact permanent routing map for allowed tools."""
    names = _name_set(available_tools)
    sections = []
    for family_name in INJECTED_TOOL_FAMILIES:
        rendered = render_tool_family(family_name, names)
        routes = rendered.get("routes", [])
        if len(routes) < 2:
            continue
        lines = [f"**{rendered['title']}**"]
        for route in routes:
            lines.append(
                f"- {_compact_selector(route)}: {route['use_when']}; "
                f"not {route['not_for']}."
            )
        sections.append("\n".join(lines))
    if not sections:
        return ""
    return (
        "## Tool selection\n"
        "Choose the narrowest available tool that owns the operation. Do not "
        "duplicate state across systems. Use "
        "`get_tool_schema(family='<name>')` to compare a family and "
        "`get_tool_schema(tool_name='<name>')` for the exact contract.\n\n"
        + "\n\n".join(sections)
    )


__all__ = [
    "INJECTED_TOOL_FAMILIES",
    "TOOL_FAMILIES",
    "build_tool_selection_hint",
    "declared_tool_names",
    "render_tool_family",
]
