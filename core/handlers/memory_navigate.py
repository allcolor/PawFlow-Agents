"""Memory navigate handler — browse memory categories."""

import logging
from collections import defaultdict
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class MemoryNavigateHandler(ToolHandler):
    """Browse and explore the memory taxonomy (categories)."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "memory_navigate"

    @property
    def description(self) -> str:
        return (
            "Browse the memory taxonomy structure. List categories "
            "(fact types), get a taxonomy overview, or view graph stats."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_categories", "get_taxonomy", "graph_stats"],
                    "description": (
                        "list_categories: memory type categories with counts; "
                        "get_taxonomy: full {category: count} overview; "
                        "graph_stats: overall memory statistics"
                    ),
                },
            },
            "required": ["action"],
        }

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def execute(self, arguments: Dict[str, Any]) -> str:
        if not self._user_id:
            return "Error: user_id not set"

        action = arguments.get("action", "")

        from core.memory_store import MemoryStore
        ms = MemoryStore.instance()

        # Load all entries for user
        with ms._store_lock:
            ms._ensure_loaded(self._user_id)
            entries = list(ms._memories.get(self._user_id, []))

        if not entries:
            return "No memories stored yet."

        if action == "list_categories":
            categories = sorted({e.category for e in entries if e.category})
            if not categories:
                return "No categories defined. Memories have no category attribute set."
            counts = {c: sum(1 for e in entries if e.category == c) for c in categories}
            lines = [f"- {c} ({counts[c]} memories)" for c in categories]
            return f"Categories ({len(categories)}):\n" + "\n".join(lines)

        elif action == "get_taxonomy":
            cat_counts: Dict[str, int] = defaultdict(int)
            for e in entries:
                cat_counts[e.category or "(none)"] += 1
            lines = [f"  {c}: {n}" for c, n in sorted(cat_counts.items())]
            return "Taxonomy:\n" + "\n".join(lines)

        elif action == "graph_stats":
            categories = {e.category for e in entries if e.category}
            # Category distribution
            cat_counts: Dict[str, int] = defaultdict(int)
            for e in entries:
                cat_counts[e.category or "(none)"] += 1
            # Ended memories
            ended = sum(1 for e in entries if e.ended)
            lines = [
                f"Total memories: {len(entries)}",
                f"Categories: {len(categories)}",
                f"Ended (obsolete): {ended}",
                f"Active: {len(entries) - ended}",
                "Category distribution:",
            ]
            for c in sorted(cat_counts):
                lines.append(f"  {c}: {cat_counts[c]}")
            return "\n".join(lines)

        return f"Unknown action: {action}"
