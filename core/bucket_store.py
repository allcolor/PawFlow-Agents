"""Hierarchical compaction buckets — per-(conversation, agent) pyramidal summary cache.

Buckets are the storage layer used by _compact's reduce-to-cap loop:
  * add_bucket(...)               — fresh level-1 summary of raw messages.
  * rollup_all_except_last(text)  — collapse [B_1..B_{N-1}] into one SB,
                                    keeping the most recent object.
  * collapse_all(text)            — replace every object with ONE.

Numbering: _next_b_num / _next_sb_num are monotonic forever.

meta.json v2 schema:
    {"version": 2, "last_seq": int, "last_ts": float,
     "objects": ["SB_00001", "B_00007", ...],
     "_next_b_num": int, "_next_sb_num": int}
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BucketStore:
    """Per-agent pyramidal summary cache for a single conversation."""

    def __init__(self, conv_dir: Path, agent_name: str):
        self._dir = conv_dir / "summaries" / (agent_name or "_shared")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._dir / "meta.json"
        self._lock = threading.Lock()
        self._meta = self._load_meta()

    @classmethod
    def get(cls, conv_dir: Path, agent_name: str) -> "BucketStore":
        return cls(conv_dir, agent_name)

    def _empty_meta(self) -> Dict:
        return {"version": 2, "last_seq": 0, "last_ts": 0.0,
                "objects": [], "_next_b_num": 1, "_next_sb_num": 1}

    def _load_meta(self) -> Dict:
        if not self._meta_path.exists():
            return self._empty_meta()
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("[bucket-store] meta.json corrupt (%s) - reinitializing", e)
            return self._empty_meta()

    def _save_meta(self):
        tmp = self._meta_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)
        tmp.replace(self._meta_path)

    @property
    def last_seq(self) -> int:
        return int(self._meta.get("last_seq", 0))

    @property
    def object_count(self) -> int:
        return len(self._meta.get("objects", []))

    def has_any(self) -> bool:
        return self.object_count > 0

    def _bucket_path(self, bid: str) -> Path:
        return self._dir / f"{bid}.json"

    def _write_doc(self, bid: str, doc: Dict):
        path = self._bucket_path(bid)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def add_bucket(self, first_seq: int, last_seq: int,
                   first_ts: float, last_ts: float,
                   summary: str, model: str = "",
                   prompt_version: str = "v1") -> str:
        """Append a new level-1 object (fresh compact output)."""
        with self._lock:
            n = int(self._meta.get("_next_b_num", 1))
            self._meta["_next_b_num"] = n + 1
            bid = f"B_{n:05d}"
            doc = {
                "bucket_id": bid,
                "level": 1,
                "first_seq": int(first_seq),
                "last_seq": int(last_seq),
                "first_ts": float(first_ts),
                "last_ts": float(last_ts),
                "covers": None,
                "model": model,
                "prompt_version": prompt_version,
                "summary": summary,
            }
            self._write_doc(bid, doc)
            self._meta["objects"].append(bid)
            self._meta["last_seq"] = max(self._meta.get("last_seq", 0),
                                          int(last_seq))
            self._meta["last_ts"] = max(self._meta.get("last_ts", 0.0),
                                         float(last_ts))
            self._save_meta()
            logger.info("[bucket-store] added %s (seq %d..%d, %d chars)",
                        bid, first_seq, last_seq, len(summary))
            return bid

    def rollup_all_except_last(self, super_summary: str, model: str = "",
                                prompt_version: str = "v1") -> Optional[str]:
        """Consolidate [B_1..B_{N-1}] into one SB, keep B_N untouched.

        Used by _compact step 2 when the output is still above the cap
        after adding a fresh bucket. Requires ≥ 3 objects — with 2, go
        straight to collapse_all instead. A newly-produced SB can itself
        be consolidated on a later rollup, giving the pyramidal shape.
        """
        with self._lock:
            ids = list(self._meta.get("objects", []))
            if len(ids) < 3:
                return None
            to_consolidate = ids[:-1]
            last_id = ids[-1]
            first_seq = None
            last_seq = 0
            first_ts = None
            last_ts = 0.0
            max_level = 1
            for bid in to_consolidate:
                d = self._read_bucket(bid)
                if not d:
                    continue
                if first_seq is None or d["first_seq"] < first_seq:
                    first_seq = d["first_seq"]
                if d["last_seq"] > last_seq:
                    last_seq = d["last_seq"]
                if first_ts is None or d["first_ts"] < first_ts:
                    first_ts = d["first_ts"]
                if d["last_ts"] > last_ts:
                    last_ts = d["last_ts"]
                lv = int(d.get("level", 1))
                if lv > max_level:
                    max_level = lv
            n = int(self._meta.get("_next_sb_num", 1))
            self._meta["_next_sb_num"] = n + 1
            sid = f"SB_{n:05d}"
            doc = {
                "bucket_id": sid,
                "level": max_level + 1,
                "first_seq": int(first_seq or 0),
                "last_seq": int(last_seq),
                "first_ts": float(first_ts or 0.0),
                "last_ts": float(last_ts),
                "covers": list(to_consolidate),
                "model": model,
                "prompt_version": prompt_version,
                "summary": super_summary,
            }
            self._write_doc(sid, doc)
            for bid in to_consolidate:
                p = self._bucket_path(bid)
                try:
                    if p.exists():
                        p.unlink()
                except Exception as e:
                    logger.warning("[bucket-store] failed to delete %s: %s", bid, e)
            self._meta["objects"] = [sid, last_id]
            self._save_meta()
            logger.info("[bucket-store] rolled up %d objects into %s (level=%d)",
                        len(to_consolidate), sid, max_level + 1)
            return sid

    def _read_bucket(self, bid: str) -> Optional[Dict]:
        p = self._bucket_path(bid)
        if not p.exists():
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("[bucket-store] failed to read %s: %s", bid, e)
            return None

    def get_all_summaries(self) -> List[Dict]:
        """All object docs in chronological order."""
        out = []
        for bid in self._meta.get("objects", []):
            d = self._read_bucket(bid)
            if d:
                out.append(d)
        return out

    def get_rollup_input(self) -> List[Dict]:
        """Docs for rollup_all_except_last (every object but the last).

        Returns [] when fewer than 3 objects exist.
        """
        ids = list(self._meta.get("objects", []))
        if len(ids) < 3:
            return []
        return [d for d in (self._read_bucket(b) for b in ids[:-1]) if d]

    def get_collapse_input(self) -> List[Dict]:
        """Docs for collapse_all (every object).

        Returns [] when fewer than 2 objects exist.
        """
        ids = list(self._meta.get("objects", []))
        if len(ids) < 2:
            return []
        return [d for d in (self._read_bucket(b) for b in ids) if d]

    def collapse_all(self, combined_summary: str, model: str = "",
                     prompt_version: str = "v1") -> Optional[str]:
        """Replace every object with a single new SB.

        Used by _compact step 3 when rollup_all_except_last wasn't
        enough and the output is still above cap with 2 buckets.
        Requires ≥ 2 objects.
        """
        with self._lock:
            ids = list(self._meta.get("objects", []))
            if len(ids) < 2:
                return None
            first_seq = None
            last_seq = 0
            first_ts = None
            last_ts = 0.0
            max_level = 1
            for bid in ids:
                d = self._read_bucket(bid)
                if not d:
                    continue
                if first_seq is None or d["first_seq"] < first_seq:
                    first_seq = d["first_seq"]
                if d["last_seq"] > last_seq:
                    last_seq = d["last_seq"]
                if first_ts is None or d["first_ts"] < first_ts:
                    first_ts = d["first_ts"]
                if d["last_ts"] > last_ts:
                    last_ts = d["last_ts"]
                lv = int(d.get("level", 1))
                if lv > max_level:
                    max_level = lv
            n = int(self._meta.get("_next_sb_num", 1))
            self._meta["_next_sb_num"] = n + 1
            sid = f"SB_{n:05d}"
            doc = {
                "bucket_id": sid,
                "level": max_level + 1,
                "first_seq": int(first_seq or 0),
                "last_seq": int(last_seq),
                "first_ts": float(first_ts or 0.0),
                "last_ts": float(last_ts),
                "covers": list(ids),
                "model": model,
                "prompt_version": prompt_version,
                "summary": combined_summary,
            }
            self._write_doc(sid, doc)
            for bid in ids:
                p = self._bucket_path(bid)
                try:
                    if p.exists():
                        p.unlink()
                except Exception as e:
                    logger.warning("[bucket-store] failed to delete %s: %s", bid, e)
            self._meta["objects"] = [sid]
            self._save_meta()
            logger.info("[bucket-store] collapsed %d objects into %s (level=%d)",
                        len(ids), sid, max_level + 1)
            return sid

    def assemble_summary_header(self) -> str:
        """Concatenate all summaries into one historical-context block."""
        docs = self.get_all_summaries()
        if not docs:
            return ""
        parts = ["[Conversation summary - earlier messages compacted]\n"]
        for d in docs:
            lv = int(d.get("level", 1))
            tag = "Archived phase" if lv >= 2 else "Recent phase"
            parts.append(
                f"\n=== {tag} ({d.get('bucket_id')}, level={lv}, seq "
                f"{d.get('first_seq')}..{d.get('last_seq')}) ===\n"
                f"{d.get('summary', '')}\n"
            )
        return "".join(parts)

    def wipe(self):
        """Delete all objects - used by /compact --rebuild."""
        with self._lock:
            for bid in list(self._meta.get("objects", [])):
                p = self._bucket_path(bid)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            self._meta = self._empty_meta()
            self._save_meta()
            logger.info("[bucket-store] wiped %s", self._dir)
