"""Memory navigate handler — browse the wing/hall/room taxonomy."""

import json
import logging
from collections import defaultdict
from typing import Any, Dict

from core.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


class MemoryNavigateHandler(ToolHandler):
    """Browse and explore the memory taxonomy (wings, halls, rooms)."""

    def __init__(self):
        self._user_id = ""

    @property
    def name(self) -> str:
        return "memory_navigate"

    @property
    def description(self) -> str:
        return (
            "Browse the memory taxonomy structure. List wings (projects/people), "
            "halls (fact types), rooms (topics), get a full taxonomy tree, "
            "or find tunnels (topics shared across wings)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_wings", "list_halls", "list_rooms",
                             "get_taxonomy", "find_tunnels"],
                    "description": (
                        "list_wings: all project/person scopes; "
                        "list_halls: memory type categories (optionally filtered by wing); "
                        "list_rooms: topics (optionally filtered by wing); "
                        "get_taxonomy: full {wing: {hall: {room: count}}} tree; "
                        "find_tunnels: rooms that appear in 2+ wings"
                    ),
                },
                "wing": {
                    "type": "string",
                    "description": "Filter by wing (for list_halls, list_rooms)",
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
        wing_filter = arguments.get("wing", "")

        from core.memory_store import MemoryStore
        ms = MemoryStore.instance()

        # Load all entries for user
        with ms._store_lock:
            ms._ensure_loaded(self._user_id)
            entries = list(ms._memories.get(self._user_id, []))

        if not entries:
            return "No memories stored yet."

        if action == "list_wings":
            wings = sorted({e.wing for e in entries if e.wing})
            if not wings:
                return "No wings defined. Memories have no wing attribute set."
            counts = {w: sum(1 for e in entries if e.wing == w) for w in wings}
            lines = [f"- {w} ({counts[w]} memories)" for w in wings]
            return f"Wings ({len(wings)}):\n" + "\n".join(lines)

        elif action == "list_halls":
            filtered = [e for e in entries if not wing_filter or e.wing == wing_filter]
            halls = sorted({e.hall for e in filtered if e.hall})
            if not halls:
                return "No halls defined" + (f" in wing '{wing_filter}'" if wing_filter else "") + "."
            counts = {h: sum(1 for e in filtered if e.hall == h) for h in halls}
            lines = [f"- {h} ({counts[h]} memories)" for h in halls]
            scope = f" in wing '{wing_filter}'" if wing_filter else ""
            return f"Halls{scope} ({len(halls)}):\n" + "\n".join(lines)

        elif action == "list_rooms":
            filtered = [e for e in entries if not wing_filter or e.wing == wing_filter]
            rooms = sorted({e.room for e in filtered if e.room})
            if not rooms:
                return "No rooms defined" + (f" in wing '{wing_filter}'" if wing_filter else "") + "."
            counts = {r: sum(1 for e in filtered if e.room == r) for r in rooms}
            lines = [f"- {r} ({counts[r]} memories)" for r in rooms]
            scope = f" in wing '{wing_filter}'" if wing_filter else ""
            return f"Rooms{scope} ({len(rooms)}):\n" + "\n".join(lines)

        elif action == "get_taxonomy":
            tree: Dict[str, Dict[str, Dict[str, int]]] = {}
            for e in entries:
                w = e.wing or "(no wing)"
                h = e.hall or "(no hall)"
                r = e.room or "(no room)"
                tree.setdefault(w, {}).setdefault(h, {}).setdefault(r, 0)
                tree[w][h][r] += 1
            lines = []
            for w in sorted(tree):
                lines.append(f"{w}:")
                for h in sorted(tree[w]):
                    lines.append(f"  {h}:")
                    for r in sorted(tree[w][h]):
                        lines.append(f"    {r}: {tree[w][h][r]}")
            return "Taxonomy:\n" + "\n".join(lines)

        elif action == "find_tunnels":
            # Rooms that appear in 2+ different wings
            room_wings: Dict[str, set] = defaultdict(set)
            for e in entries:
                if e.room and e.wing:
                    room_wings[e.room].add(e.wing)
            tunnels = {r: sorted(ws) for r, ws in room_wings.items() if len(ws) >= 2}
            if not tunnels:
                return "No tunnels found (no rooms shared across multiple wings)."
            lines = [f"- {r}: {', '.join(ws)}" for r, ws in sorted(tunnels.items())]
            return f"Tunnels ({len(tunnels)} rooms shared across wings):\n" + "\n".join(lines)

        return f"Unknown action: {action}"
