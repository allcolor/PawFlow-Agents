"""Hierarchical compaction buckets — per-(conversation, agent) pyramidal summary cache.

Layout (under each conversation's directory):

    {conv_dir}/summaries/{agent}/
        meta.json                         # index: last_seq, buckets[], super_buckets[]
        B_00001.json                      # level-1 bucket (first 500 msgs of tail)
        B_00002.json
        ...
        SB_00001.json                     # level-2 super-bucket (covers B_00001..B_00030)

Each bucket file is **immutable** once written — regeneration requires
explicit wipe (debug / `--rebuild`). This is what lets pre-compact be
cheap: a bucket that already exists is reused as-is at every subsequent
compact instead of being re-summarized from raw messages.

The store is NOT responsible for summarization itself — only for the
disk layout, metadata, and the pre-compact decisions (which bucket to
create, when to roll up). The actual LLM call is driven by the
compaction mixin.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default thresholds — can be overridden per call.
BUCKET_MSG_THRESHOLD = 500       # create a bucket when tail hits this many msgs
BUCKET_TOKEN_THRESHOLD = 100_000  # ... or this many estimated tokens
ROLLUP_K = 30                    # consolidate this many buckets into one super-bucket


class BucketStore:
    """Per-agent pyramidal summary cache for a single conversation.

    Usage:
        store = BucketStore(conv_dir, agent_name)
        if store.should_create_bucket(tail_msg_count, tail_tokens):
            # caller summarizes `tail` → summary_text
            store.add_bucket(first_seq, last_seq, first_ts, last_ts, summary_text)
        if store.should_rollup():
            # caller consolidates the first K buckets → super_summary_text
            store.rollup(super_summary_text)
        # Build the compact input:
        header = store.assemble_summary_header()
        # (caller appends raw tail messages after `header`)

    Concurrency: one lock per (conv, agent) instance. Compaction blocks
    the agent already, so lock contention is not a real concern.
    """

    def __init__(self, conv_dir: Path, agent_name: str):
        self._dir = conv_dir / "summaries" / (agent_name or "_shared")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._dir / "meta.json"
        self._lock = threading.Lock()
        self._meta = self._load_meta()

    @classmethod
    def get(cls, conv_dir: Path, agent_name: str) -> "BucketStore":
        """Build a fresh instance that reads meta.json from disk.

        No in-memory singleton: the previous cache kept stale state
        when buckets were deleted on disk (e.g., user cleanup), causing
        the next compact to think 1964 old messages were still covered.
        Reading a tiny meta.json file per compact is negligible.
        """
        return cls(conv_dir, agent_name)

    # ── Meta I/O ────────────────────────────────────────────────────

    def _load_meta(self) -> Dict:
        if not self._meta_path.exists():
            return {
                "version": 1,
                "last_seq": 0,
                "last_ts": 0.0,
                "buckets": [],        # list of bucket_id strings in order
                "super_buckets": [],  # list of SB ids in order
            }
        try:
            with open(self._meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("[bucket-store] meta.json corrupt (%s) — reinitializing", e)
            return {"version": 1, "last_seq": 0, "last_ts": 0.0,
                    "buckets": [], "super_buckets": []}

    def _save_meta(self):
        tmp = self._meta_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)
        tmp.replace(self._meta_path)

    # ── Public state accessors ──────────────────────────────────────

    @property
    def last_seq(self) -> int:
        """Largest seq covered by an existing bucket. 0 if cold start."""
        return int(self._meta.get("last_seq", 0))

    @property
    def bucket_count(self) -> int:
        return len(self._meta.get("buckets", []))

    @property
    def super_bucket_count(self) -> int:
        return len(self._meta.get("super_buckets", []))

    def has_any(self) -> bool:
        return self.bucket_count > 0 or self.super_bucket_count > 0

    # ── Pre-compact decisions ───────────────────────────────────────

    def should_create_bucket(self, tail_msg_count: int,
                             tail_token_estimate: int,
                             msg_threshold: int = BUCKET_MSG_THRESHOLD,
                             token_threshold: int = BUCKET_TOKEN_THRESHOLD) -> bool:
        """Tail hit the size threshold — promote it to a new bucket."""
        return (tail_msg_count >= msg_threshold
                or tail_token_estimate >= token_threshold)

    def should_rollup(self, k: int = ROLLUP_K) -> bool:
        return self.bucket_count >= k

    # ── Writes ──────────────────────────────────────────────────────

    def _bucket_path(self, bid: str) -> Path:
        return self._dir / f"{bid}.json"

    def add_bucket(self, first_seq: int, last_seq: int,
                   first_ts: float, last_ts: float,
                   summary: str, model: str = "",
                   prompt_version: str = "v1") -> str:
        """Append a new level-1 bucket. Returns its id."""
        with self._lock:
            n = len(self._meta["buckets"]) + 1
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
            path = self._bucket_path(bid)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
            self._meta["buckets"].append(bid)
            self._meta["last_seq"] = max(self._meta.get("last_seq", 0),
                                          int(last_seq))
            self._meta["last_ts"] = max(self._meta.get("last_ts", 0.0),
                                         float(last_ts))
            self._save_meta()
            logger.info("[bucket-store] added %s (seq %d..%d, %d chars)",
                        bid, first_seq, last_seq, len(summary))
            return bid

    def rollup(self, super_summary: str, k: int = ROLLUP_K,
               model: str = "", prompt_version: str = "v1") -> Optional[str]:
        """Consolidate the first K buckets into a new SB, delete them.

        Returns the new SB id, or None if rollup didn't fire.
        """
        with self._lock:
            if len(self._meta["buckets"]) < k:
                return None
            to_consolidate = self._meta["buckets"][:k]
            # Read first/last seq+ts from the buckets being consolidated
            first_seq = None
            last_seq = 0
            first_ts = None
            last_ts = 0.0
            for bid in to_consolidate:
                b = self._read_bucket(bid)
                if not b:
                    continue
                if first_seq is None or b["first_seq"] < first_seq:
                    first_seq = b["first_seq"]
                if b["last_seq"] > last_seq:
                    last_seq = b["last_seq"]
                if first_ts is None or b["first_ts"] < first_ts:
                    first_ts = b["first_ts"]
                if b["last_ts"] > last_ts:
                    last_ts = b["last_ts"]

            sn = len(self._meta["super_buckets"]) + 1
            sid = f"SB_{sn:05d}"
            doc = {
                "bucket_id": sid,
                "level": 2,
                "first_seq": int(first_seq or 0),
                "last_seq": int(last_seq),
                "first_ts": float(first_ts or 0.0),
                "last_ts": float(last_ts),
                "covers": to_consolidate,
                "model": model,
                "prompt_version": prompt_version,
                "summary": super_summary,
            }
            path = self._bucket_path(sid)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
            tmp.replace(path)

            # Delete the consolidated buckets (files + index entries)
            for bid in to_consolidate:
                p = self._bucket_path(bid)
                try:
                    if p.exists():
                        p.unlink()
                except Exception as e:
                    logger.warning("[bucket-store] failed to delete %s: %s", bid, e)
            self._meta["buckets"] = self._meta["buckets"][k:]
            self._meta["super_buckets"].append(sid)
            self._save_meta()
            logger.info("[bucket-store] rolled up %d buckets into %s", k, sid)
            return sid

    # ── Reads ───────────────────────────────────────────────────────

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
        """Return all summaries in chronological order: [SBs..., Bs...]."""
        out = []
        for sid in self._meta.get("super_buckets", []):
            doc = self._read_bucket(sid)
            if doc:
                out.append(doc)
        for bid in self._meta.get("buckets", []):
            doc = self._read_bucket(bid)
            if doc:
                out.append(doc)
        return out

    def get_consolidation_input(self, k: int = ROLLUP_K) -> List[Dict]:
        """Return the first K bucket docs (for caller to consolidate into SB)."""
        ids = self._meta.get("buckets", [])[:k]
        out = []
        for bid in ids:
            doc = self._read_bucket(bid)
            if doc:
                out.append(doc)
        return out

    # ── Assembly ────────────────────────────────────────────────────

    def assemble_summary_header(self) -> str:
        """Concatenate all existing summaries into one historical-context block.

        Shape sent to the final compact:

            [Conversation summary — earlier messages compacted]

            === Archived phase 1 (msgs 1..15000, days 1..10) ===
            <super-bucket summary 1>

            === Recent phase (msgs 15001..15500) ===
            <bucket summary 1>
            ...

        The "archived phase N" framing tells the final summarizer what
        it's reading — a stack of previously-compacted summaries, not
        new material to re-summarize from scratch.
        """
        docs = self.get_all_summaries()
        if not docs:
            return ""
        parts = ["[Conversation summary — earlier messages compacted]\n"]
        for d in docs:
            tag = "Archived phase" if d.get("level") == 2 else "Recent phase"
            parts.append(
                f"\n=== {tag} ({d.get('bucket_id')}, seq "
                f"{d.get('first_seq')}..{d.get('last_seq')}) ===\n"
                f"{d.get('summary', '')}\n"
            )
        return "".join(parts)

    # ── Maintenance ─────────────────────────────────────────────────

    def wipe(self):
        """Delete all buckets — used by `/compact --rebuild`."""
        with self._lock:
            for bid in list(self._meta.get("buckets", [])):
                p = self._bucket_path(bid)
                if p.exists():
                    p.unlink()
            for sid in list(self._meta.get("super_buckets", [])):
                p = self._bucket_path(sid)
                if p.exists():
                    p.unlink()
            self._meta = {"version": 1, "last_seq": 0, "last_ts": 0.0,
                          "buckets": [], "super_buckets": []}
            self._save_meta()
            logger.info("[bucket-store] wiped %s", self._dir)
