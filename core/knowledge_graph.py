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

    # add_triple is defined below (after graph traversal methods)
    # with full confidence tracking (EXTRACTED/INFERRED/AMBIGUOUS)

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

    # ── Graph traversal (BFS/DFS) ────────────────────────────────

    def query_graph(self, question: str, mode: str = "bfs",
                    depth: int = 3, max_results: int = 50) -> List[Dict]:
        """Traverse the KG from entities matching the question.

        Args:
            question: text to match against entity names
            mode: 'bfs' (broad context) or 'dfs' (trace a path)
            depth: max traversal depth
            max_results: max triples to return
        """
        with self._lock:
            # Find matching entities
            q = question.lower()
            seeds = [
                name for name in self._entities
                if q in name.lower() or any(
                    w in name.lower() for w in q.split() if len(w) > 2
                )
            ]
            if not seeds:
                # Try matching in triple subjects/objects
                for t in self._triples:
                    if t["valid_to"]:
                        continue
                    for field in ("subject", "object"):
                        if q in t[field].lower() and t[field] not in seeds:
                            seeds.append(t[field])
            if not seeds:
                return []

            # Build adjacency from active triples
            adj: Dict[str, List[Dict]] = {}
            for t in self._triples:
                if t["valid_to"]:
                    continue
                adj.setdefault(t["subject"], []).append(t)
                adj.setdefault(t["object"], []).append(t)

            visited = set()
            results = []

            if mode == "dfs":
                # DFS: trace deep paths from first seed
                def _dfs(entity, d):
                    if d <= 0 or entity in visited or len(results) >= max_results:
                        return
                    visited.add(entity)
                    for t in adj.get(entity, []):
                        results.append(t)
                        other = t["object"] if t["subject"] == entity else t["subject"]
                        _dfs(other, d - 1)
                _dfs(seeds[0], depth)
            else:
                # BFS: broad context around all seeds
                queue = [(s, 0) for s in seeds]
                while queue and len(results) < max_results:
                    entity, d = queue.pop(0)
                    if entity in visited or d > depth:
                        continue
                    visited.add(entity)
                    for t in adj.get(entity, []):
                        if t["id"] not in {r["id"] for r in results}:
                            results.append(t)
                        other = t["object"] if t["subject"] == entity else t["subject"]
                        if other not in visited:
                            queue.append((other, d + 1))

            return [{
                "subject": t["subject"],
                "predicate": t["predicate"],
                "object": t["object"],
                "confidence": t.get("confidence", 1.0),
                "source": t.get("source", ""),
            } for t in results]

    # ── God nodes ──────────────────────────────────────────────────

    def god_nodes(self, limit: int = 10) -> List[Dict]:
        """Return the most connected entities in the KG."""
        with self._lock:
            degree: Dict[str, int] = {}
            for t in self._triples:
                if t["valid_to"]:
                    continue
                degree[t["subject"]] = degree.get(t["subject"], 0) + 1
                degree[t["object"]] = degree.get(t["object"], 0) + 1
            ranked = sorted(degree.items(), key=lambda x: -x[1])
            return [
                {"entity": name, "connections": count}
                for name, count in ranked[:limit]
            ]

    # ── Hyperedges ─────────────────────────────────────────────────

    def get_hyperedges(self) -> List[Dict]:
        """Find group relationships: same subject+predicate with 3+ objects."""
        with self._lock:
            groups: Dict[str, List[str]] = {}
            for t in self._triples:
                if t["valid_to"]:
                    continue
                key = f"{t['subject']}::{t['predicate']}"
                groups.setdefault(key, []).append(t["object"])
            return [
                {"subject": key.split("::")[0],
                 "predicate": key.split("::")[1],
                 "objects": objs,
                 "count": len(objs)}
                for key, objs in groups.items()
                if len(objs) >= 3
            ]

    # ── Surprising connections ─────────────────────────────────────

    def surprises(self, limit: int = 10) -> List[Dict]:
        """Find surprising cross-domain connections."""
        with self._lock:
            # Score each active triple by "surprise"
            scored = []
            for t in self._triples:
                if t["valid_to"]:
                    continue
                score = 0
                s, o = t["subject"], t["object"]
                # Cross-entity-type bonus
                s_type = self._entities.get(s, {}).get("type", "")
                o_type = self._entities.get(o, {}).get("type", "")
                if s_type and o_type and s_type != o_type:
                    score += 2
                # INFERRED bonus
                conf = t.get("confidence", 1.0)
                if isinstance(conf, str):
                    if conf == "AMBIGUOUS":
                        score += 3
                    elif conf == "INFERRED":
                        score += 2
                elif conf < 0.8:
                    score += 2
                # Low-connection entities bonus (peripheral → hub)
                s_deg = sum(1 for x in self._triples if x["subject"] == s or x["object"] == s)
                o_deg = sum(1 for x in self._triples if x["subject"] == o or x["object"] == o)
                if min(s_deg, o_deg) <= 2 and max(s_deg, o_deg) >= 5:
                    score += 2
                if score > 0:
                    scored.append({
                        "subject": s, "predicate": t["predicate"], "object": o,
                        "confidence": conf, "score": score,
                    })
            scored.sort(key=lambda x: -x["score"])
            return scored[:limit]

    # ── Community detection ───────────────────────────────────────

    def detect_communities(self, max_iterations: int = 20) -> Dict[int, List[str]]:
        """Detect communities using label propagation (no external deps).

        Returns {community_id: [entity_names]}, ordered by size descending.
        """
        import random
        with self._lock:
            # Build undirected adjacency from active triples
            adj: Dict[str, set] = {}
            for t in self._triples:
                if t["valid_to"]:
                    continue
                adj.setdefault(t["subject"], set()).add(t["object"])
                adj.setdefault(t["object"], set()).add(t["subject"])

            if not adj:
                return {}

            # Initialize: each node in its own community
            labels = {node: i for i, node in enumerate(adj)}
            nodes = list(adj.keys())

            for _ in range(max_iterations):
                changed = False
                random.shuffle(nodes)
                for node in nodes:
                    if not adj[node]:
                        continue
                    # Count neighbor labels
                    counts: Dict[int, int] = {}
                    for neighbor in adj[node]:
                        nl = labels.get(neighbor, -1)
                        if nl >= 0:
                            counts[nl] = counts.get(nl, 0) + 1
                    if not counts:
                        continue
                    # Pick most frequent label
                    best = max(counts, key=counts.get)
                    if labels[node] != best:
                        labels[node] = best
                        changed = True
                if not changed:
                    break

            # Group by label
            groups: Dict[int, List[str]] = {}
            for node, label in labels.items():
                groups.setdefault(label, []).append(node)

            # Re-index by size descending
            sorted_groups = sorted(groups.values(), key=len, reverse=True)
            return {i: members for i, members in enumerate(sorted_groups)}

    def get_communities_report(self) -> str:
        """Generate a text report of communities."""
        communities = self.detect_communities()
        if not communities:
            return "No communities detected (KG is empty)."
        lines = [f"Communities ({len(communities)}):"]
        for cid, members in communities.items():
            # Compute cohesion: ratio of intra-community edges to total edges of members
            member_set = set(members)
            intra = 0
            total = 0
            for t in self._triples:
                if t["valid_to"]:
                    continue
                s_in = t["subject"] in member_set
                o_in = t["object"] in member_set
                if s_in or o_in:
                    total += 1
                if s_in and o_in:
                    intra += 1
            cohesion = intra / max(total, 1)
            sample = ", ".join(members[:5])
            if len(members) > 5:
                sample += f", ... +{len(members) - 5}"
            lines.append(f"  [{cid}] {len(members)} entities (cohesion: {cohesion:.2f}): {sample}")
        return "\n".join(lines)

    # ── Confidence helpers ─────────────────────────────────────────

    def add_triple(self, subject: str, predicate: str, obj: str,
                   valid_from: str = "", confidence=1.0,
                   source: str = "") -> Dict:
        """Add a fact triple. Returns dict with status + optional contradiction."""
        # Accept both float and string confidence
        _conf = confidence
        _conf_score = 0.0
        if isinstance(confidence, str):
            _conf = confidence.upper()
            if _conf not in ("EXTRACTED", "INFERRED", "AMBIGUOUS"):
                _conf = "EXTRACTED"
            _conf_score = {"EXTRACTED": 1.0, "INFERRED": 0.7, "AMBIGUOUS": 0.3}.get(_conf, 1.0)
        else:
            _conf_score = float(confidence)
            if _conf_score >= 0.9:
                _conf = "EXTRACTED"
            elif _conf_score >= 0.5:
                _conf = "INFERRED"
            else:
                _conf = "AMBIGUOUS"

        with self._lock:
            self._ensure_entity(subject)
            self._ensure_entity(obj)

            # Check contradiction
            contradictions = []
            for t in self._triples:
                if (t["subject"] == subject and t["predicate"] == predicate
                        and t["object"] != obj and t["valid_to"] == ""):
                    contradictions.append(t["object"])

            # Check duplicate
            for t in self._triples:
                if (t["subject"] == subject and t["predicate"] == predicate
                        and t["object"] == obj and t["valid_to"] == ""):
                    return {
                        "status": "duplicate", "triple_id": t["id"],
                        "contradictions": contradictions,
                    }

            triple = {
                "id": uuid.uuid4().hex[:12],
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "valid_from": valid_from,
                "valid_to": "",
                "confidence": _conf,
                "confidence_score": _conf_score,
                "source": source,
                "extracted_at": time.time(),
            }
            self._triples.append(triple)
            self._save()
            return {
                "status": "added", "triple_id": triple["id"],
                "contradictions": contradictions,
            }

    # ── Factory ────────────────────────────────────────────────────

    @classmethod
    def for_user(cls, user_id: str, store_dir: str = "") -> "KnowledgeGraph":
        """Get or create a KnowledgeGraph for a user."""
        d = Path(store_dir or _DEFAULT_DIR)
        d.mkdir(parents=True, exist_ok=True)
        return cls(str(d / f"{_safe_filename(user_id)}.json"))
