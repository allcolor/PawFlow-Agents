"""Agent Diary — per-agent persistent journal.

Each agent maintains a diary of observations, decisions, and learnings
that persists across conversations. The diary is injected into the
system prompt after the memory digest.

Storage: data/memories/{user}/diary_{agent}.jsonl
"""

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

import core.paths as _paths


class AgentDiary:
    """Per-agent diary stored as JSONL."""

    def __init__(self, store_dir: str = ""):
        self._store_dir = Path(store_dir or str(_paths.MEMORIES_DIR))

    @classmethod
    def instance(cls) -> "AgentDiary":
        if not hasattr(cls, "_instance") or cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _diary_path(self, user_id: str, agent_name: str) -> Path:
        safe_user = user_id.replace("/", "_").replace("\\", "_")
        safe_agent = agent_name.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._store_dir / safe_user / f"diary_{safe_agent}.jsonl"

    def write(self, user_id: str, agent_name: str, entry: str,
              entry_type: str = "observation", tags: List[str] = None) -> Dict:
        """Append a diary entry. Returns the entry dict."""
        if not user_id or not agent_name or not entry:
            raise ValueError("user_id, agent_name, and entry are required")
        path = self._diary_path(user_id, agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)

        record = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "type": entry_type,  # observation, decision, learning, reflection
            "text": entry,
            "tags": [t.lower().strip() for t in (tags or [])],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def read(self, user_id: str, agent_name: str,
             limit: int = 20, entry_type: str = "") -> List[Dict]:
        """Read recent diary entries (newest first)."""
        if not user_id or not agent_name:
            return []
        path = self._diary_path(user_id, agent_name)
        if not path.exists():
            return []
        entries = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if entry_type and record.get("type") != entry_type:
                        continue
                    entries.append(record)
                except json.JSONDecodeError:
                    continue
        # Newest first
        entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
        return entries[:limit]

    def build_diary_digest(self, user_id: str, agent_name: str,
                           max_chars: int = 600) -> str:
        """Build compact diary digest for system prompt injection."""
        entries = self.read(user_id, agent_name, limit=10)
        if not entries:
            return ""
        lines = []
        for e in entries:
            text = e.get("text", "")
            _type = e.get("type", "")
            lines.append(f"[{_type}] {text[:100]}")
        digest = "\n".join(lines)
        if len(digest) > max_chars:
            digest = digest[:max_chars - 3] + "..."
        return digest
