"""KnowledgeGraph — Per-user temporal entity-relationship graph.

Stores facts as (subject, predicate, object) triples with temporal validity.
Uses JSON files for storage (one per user) — same pattern as MemoryStore.

Key features:
- Temporal: each triple has valid_from / valid_to
- Contradiction detection: warns when conflicting facts exist
- Query by entity with optional temporal filtering
"""

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_DIR = "data/knowledge_graphs"


def _safe_filename(user_id: str) -> str:
    return re.sub(r'[^a-zA-Z0-9._-]', '_', user_id)


class KnowledgeGraph:
    """Per-user temporal entity-relationship graph backed by JSON."""

    def __init__(self, json_path: str):
        self._path = Path(json_path)
        self._lock = threading.Lock()
        self._triples: List[Dict[str, Any]] = []
        self._entities: Dict[str, Dict[str, Any]] = {}  # name -> {type, properties, created_at}
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._triples = data.get("triples", [])
                self._entities = data.get("entities", {})
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("KG load failed (%s): %s", self._path, e)

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"entities": self._entities, "triples": self._triples},
            separators=(',', ':'),
        ))
        tmp.replace(self._path)

    def _ensure_entity(self, name: str):
        if name not in self._entities:
            self._entities[name] = {"type": "", "created_at": time.time()}

    def add_triple(self, subject: str, predicate: str, obj: str,
                   valid_from: str = "", confidence: float = 1.0,
                   source: str = "") -> Tuple[str, Optional[str]]:
        """Add a fact triple. Returns (triple_id, contradiction_warning or None)."""
        with self._lock:
            self._ensure_entity(subject)
            self._ensure_entity(obj)

            # Check contradiction: same subject+predicate, different object, still active
            contradiction = None
            others = [
                t["object"] for t in self._triples
                if t["subject"] == subject and t["predicate"] == predicate
                and t["object"] != obj and t["valid_to"] == ""
            ]
            if others:
                contradiction = (
                    f"Contradiction: {subject} -> {predicate} already has active value(s): "
                    + ", ".join(others)
                    + f". New value: {obj}. Consider invalidating the old fact."
                )

            # Check duplicate
            for t in self._triples:
                if (t["subject"] == subject and t["predicate"] == predicate
                        and t["object"] == obj and t["valid_to"] == ""):
                    return t["id"], contradiction

            triple = {
                "id": uuid.uuid4().hex[:12],
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "valid_from": valid_from,
                "valid_to": "",
                "confidence": confidence,
                "source": source,
                "extracted_at": time.time(),
            }
            self._triples.append(triple)
            self._save()
            return triple["id"], contradiction

    def query_entity(self, entity: str, as_of: str = "",
                     direction: str = "both") -> List[Dict[str, Any]]:
        """Query all facts about an entity.

        Args:
            entity: Entity name to query.
            as_of: Date string — only return facts valid at this time.
            direction: 'outgoing', 'incoming', or 'both'.
        """
        with self._lock:
            results = []
            for t in self._triples:
                is_subj = t["subject"] == entity
                is_obj = t["object"] == entity
                if direction == "outgoing" and not is_subj:
                    continue
                if direction == "incoming" and not is_obj:
                    continue
                if direction == "both" and not (is_subj or is_obj):
                    continue
                if as_of and not self._valid_at(t, as_of):
                    continue
                results.append({
                    "direction": "outgoing" if is_subj else "incoming",
                    "subject": t["subject"],
                    "predicate": t["predicate"],
                    "object": t["object"],
                    "valid_from": t["valid_from"],
                    "valid_to": t["valid_to"],
                    "confidence": t["confidence"],
                    "current": t["valid_to"] == "",
                    "id": t["id"],
                })
            return results

    @staticmethod
    def _valid_at(t: Dict, as_of: str) -> bool:
        vf = t["valid_from"]
        vt = t["valid_to"]
        if vf and as_of < vf:
            return False
        if vt and as_of > vt:
            return False
        return True

    def invalidate(self, subject: str, predicate: str, obj: str,
                   ended: str = "") -> int:
        """Mark a fact as no longer valid. Returns number of triples updated."""
        with self._lock:
            ended = ended or time.strftime("%Y-%m-%d")
            count = 0
            for t in self._triples:
                if (t["subject"] == subject and t["predicate"] == predicate
                        and t["object"] == obj and t["valid_to"] == ""):
                    t["valid_to"] = ended
                    count += 1
            if count:
                self._save()
            return count

    def timeline(self, entity: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        """Get chronological list of facts, optionally filtered by entity."""
        with self._lock:
            if entity:
                filtered = [
                    t for t in self._triples
                    if t["subject"] == entity or t["object"] == entity
                ]
            else:
                filtered = list(self._triples)
            filtered.sort(key=lambda t: t["extracted_at"], reverse=True)
            return [
                {
                    "id": t["id"],
                    "subject": t["subject"],
                    "predicate": t["predicate"],
                    "object": t["object"],
                    "valid_from": t["valid_from"],
                    "valid_to": t["valid_to"],
                    "confidence": t["confidence"],
                    "current": t["valid_to"] == "",
                }
                for t in filtered[:limit]
            ]

    def stats(self) -> Dict[str, Any]:
        """Return summary statistics."""
        with self._lock:
            current = sum(1 for t in self._triples if t["valid_to"] == "")
            preds = list({t["predicate"] for t in self._triples})
            return {
                "entities": len(self._entities),
                "triples": len(self._triples),
                "current_facts": current,
                "expired_facts": len(self._triples) - current,
                "relationship_types": preds,
            }

    # ── Factory ────────────────────────────────────────────────────

    @classmethod
    def for_user(cls, user_id: str, store_dir: str = "") -> "KnowledgeGraph":
        """Get or create a KnowledgeGraph for a user."""
        d = Path(store_dir or _DEFAULT_DIR)
        d.mkdir(parents=True, exist_ok=True)
        return cls(str(d / f"{_safe_filename(user_id)}.json"))
