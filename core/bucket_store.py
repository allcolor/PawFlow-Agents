"""Hierarchical compaction buckets — per-conversation pyramidal summary cache.

One pyramid per conversation, shared across all agents. Input comes from
shared.jsonl (agent-neutral, tool-stripped) and is enriched with a tool
activity digest at build time. The BucketStore itself is pure storage
— building is owned by core/bg_bucket_builder.py.

Buckets are the storage layer used by the reduce-to-cap pipeline:
  * add_bucket(...)               — fresh level-1 summary of raw messages.
  * rollup_all_except_last(text)  — collapse [B_1..B_{N-1}] into one SB,
                                    keeping the most recent object.
  * collapse_all(text)            — replace every object with ONE.

Numbering: _next_b_num / _next_sb_num are monotonic forever.

meta.json v2 schema:
    {"version": 2, "last_seq": int, "last_ts": float,
     "objects": ["SB_00001", "B_00007", ...],
     "_next_b_num": int, "_next_sb_num": int}

Bucket doc also carries an optional `tool_trace` (structured activity
record produced by core/tool_activity_digest.py).
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Absolute sizing constants (NOT relative to any agent's ctx) ──
# Pyramid is shared across agents: sizing is decoupled from each agent's
# max_tokens. A small-ctx agent will simply fall through to the private
# squeeze path more often — by design.
#
# L1_TRIGGER_MSGS: shared-msg count since last bucket that triggers a
#   new level-1 bucket in the bg worker. 150 aligns with CC's
#   compact_boundary cadence (~150-200 CC msgs ≈ ~75-125 shared msgs
#   after tool stripping), so the first bg bucket typically lands
#   before the second CC compact of a new conversation.
# BUCKET_OUTPUT_TARGET: target token size of each L1 / SB summary.
# HEADER_BUDGET: total pyramid header size budget (sum of summaries).
#   Above this, the bg worker fires a rollup to consolidate.
# ROLLUP_TRIGGER_COUNT: hard ceiling on object count (safety net even
#   if summaries stay small).
L1_TRIGGER_MSGS = 150
BUCKET_OUTPUT_TARGET = 2000
HEADER_BUDGET = 30000
ROLLUP_TRIGGER_COUNT = 30

# The last TAIL_RESERVE shared msgs are NEVER bucketed — they stay as
# the "recent window" that every post-compact context carries. The
# only way they shrink below this count is force_fit (step 2d), when
# the assembled output still busts cap even after tool-truncate +
# private digest + header compression.
#
# Set equal to L1_TRIGGER_MSGS so the bg trigger cadence (ping when
# L1_TRIGGER new msgs exist past the pyramid) matches exactly one
# bucket's worth of work while keeping one L1's worth as tail.
TAIL_RESERVE = L1_TRIGGER_MSGS


class BucketStore:
    """Per-conversation pyramidal summary cache (single _shared pyramid).

    Writers: only core.bg_bucket_builder.BgBucketWriter should call
    mutating methods (add_bucket, rollup_all_except_last, collapse_all,
    wipe). Agents read-only via assemble_summary_header() / properties.
    """

    def __init__(self, conv_dir: Path):
        self._dir = conv_dir / "summaries" / "_shared"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._dir / "meta.json"
        self._lock = threading.Lock()
        self._meta = self._load_meta()

    @classmethod
    def get(cls, conv_dir: Path) -> "BucketStore":
        return cls(conv_dir)

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
                   summary: str,
                   first_msg_id: str = "",
                   last_msg_id: str = "",
                   msg_count: int = 0,
                   model: str = "",
                   prompt_version: str = "v1",
                   tool_trace: Optional[Dict[str, Any]] = None) -> str:
        """Append a new level-1 object (fresh compact output).

        The three breadcrumb fields (first_msg_id / last_msg_id /
        msg_count) power the nav hint rendered by
        assemble_summary_header — the agent can quote them back via
        read_history(action="range", from_msg_id=..., to_msg_id=...) to
        reach the exact original messages behind this summary.

        tool_trace (optional) carries a structured activity record
        (see core/tool_activity_digest.py) — files edited/created/read,
        commands run, delegations. Persisted alongside the narrative
        summary for UI rendering and rollup aggregation.
        """
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
                "first_msg_id": first_msg_id,
                "last_msg_id": last_msg_id,
                "msg_count": int(msg_count),
                "covers": None,
                "model": model,
                "prompt_version": prompt_version,
                "summary": summary,
                "tool_trace": tool_trace or None,
            }
            self._write_doc(bid, doc)
            self._meta["objects"].append(bid)
            self._meta["last_seq"] = max(self._meta.get("last_seq", 0),
                                          int(last_seq))
            self._meta["last_ts"] = max(self._meta.get("last_ts", 0.0),
                                         float(last_ts))
            self._save_meta()
            logger.info("[bucket-store] added %s (seq %d..%d, %d msgs, %d chars)",
                        bid, first_seq, last_seq, msg_count, len(summary))
            return bid

    def rollup_all_except_last(self, super_summary: str, model: str = "",
                                prompt_version: str = "v1",
                                tool_trace: Optional[Dict[str, Any]] = None
                                ) -> Optional[str]:
        """Consolidate [B_1..B_{N-1}] into one SB, keep B_N untouched.

        Fires from the bg worker when pyramid header > HEADER_BUDGET
        or object_count > ROLLUP_TRIGGER_COUNT. Requires ≥ 3 objects
        — with 2, go straight to collapse_all. A newly-produced SB can
        itself be consolidated on a later rollup, giving the pyramid.

        tool_trace, if supplied, carries the merged activity trace
        (see core.tool_activity_digest.merge_traces) of the sources.
        """
        with self._lock:
            ids = list(self._meta.get("objects", []))
            if len(ids) < 3:
                return None
            to_consolidate = ids[:-1]
            last_id = ids[-1]
            agg = self._aggregate(to_consolidate)
            n = int(self._meta.get("_next_sb_num", 1))
            self._meta["_next_sb_num"] = n + 1
            sid = f"SB_{n:05d}"
            doc = {
                "bucket_id": sid,
                "level": agg["max_level"] + 1,
                "first_seq": agg["first_seq"],
                "last_seq": agg["last_seq"],
                "first_ts": agg["first_ts"],
                "last_ts": agg["last_ts"],
                "first_msg_id": agg["first_msg_id"],
                "last_msg_id": agg["last_msg_id"],
                "msg_count": agg["msg_count"],
                "covers": list(to_consolidate),
                "model": model,
                "prompt_version": prompt_version,
                "summary": super_summary,
                "tool_trace": tool_trace or None,
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
            logger.info("[bucket-store] rolled up %d objects into %s (level=%d, %d msgs)",
                        len(to_consolidate), sid, agg["max_level"] + 1, agg["msg_count"])
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

    def estimated_header_chars(self) -> int:
        """Rough char budget of the current pyramid header.

        Used by the bg worker's rollup-trigger check — compares to
        HEADER_BUDGET (in chars, via a 1-token≈4-char approximation
        done at the caller). Cheaper than a full tokenizer pass.
        """
        total = 0
        for d in self.get_all_summaries():
            s = d.get("summary", "") or ""
            total += len(s)
        return total

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
                     prompt_version: str = "v1",
                     tool_trace: Optional[Dict[str, Any]] = None
                     ) -> Optional[str]:
        """Replace every object with a single new SB.

        Fires from the bg worker as a last-resort consolidation when
        rollup_all_except_last isn't enough to bring the pyramid back
        under HEADER_BUDGET. Requires ≥ 2 objects.
        """
        with self._lock:
            ids = list(self._meta.get("objects", []))
            if len(ids) < 2:
                return None
            agg = self._aggregate(ids)
            n = int(self._meta.get("_next_sb_num", 1))
            self._meta["_next_sb_num"] = n + 1
            sid = f"SB_{n:05d}"
            doc = {
                "bucket_id": sid,
                "level": agg["max_level"] + 1,
                "first_seq": agg["first_seq"],
                "last_seq": agg["last_seq"],
                "first_ts": agg["first_ts"],
                "last_ts": agg["last_ts"],
                "first_msg_id": agg["first_msg_id"],
                "last_msg_id": agg["last_msg_id"],
                "msg_count": agg["msg_count"],
                "covers": list(ids),
                "model": model,
                "prompt_version": prompt_version,
                "summary": combined_summary,
                "tool_trace": tool_trace or None,
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
            logger.info("[bucket-store] collapsed %d objects into %s (level=%d, %d msgs)",
                        len(ids), sid, agg["max_level"] + 1, agg["msg_count"])
            return sid

    def _aggregate(self, bucket_ids: List[str]) -> Dict:
        """Combine seq / ts / msg_id spans + msg_count across buckets.

        Used by rollup_all_except_last and collapse_all to build the
        consolidated doc. The span is the outer envelope — the SB covers
        everything from the earliest source's first_msg_id to the latest
        source's last_msg_id. msg_count sums cleanly because source
        ranges are disjoint by construction (seq is strictly monotonic).
        """
        first_seq = None
        last_seq = 0
        first_ts = None
        last_ts = 0.0
        first_msg_id = ""
        last_msg_id = ""
        msg_count = 0
        max_level = 1
        # Track which source supplies first_msg_id / last_msg_id so we
        # don't mix endpoints from different phases.
        _earliest = None
        _latest_seq = -1
        for bid in bucket_ids:
            d = self._read_bucket(bid)
            if not d:
                continue
            _fs = int(d.get("first_seq", 0) or 0)
            _ls = int(d.get("last_seq", 0) or 0)
            if first_seq is None or _fs < first_seq:
                first_seq = _fs
                _earliest = d
            if _ls > last_seq:
                last_seq = _ls
            if _ls > _latest_seq:
                _latest_seq = _ls
                last_msg_id = d.get("last_msg_id", "") or ""
            if first_ts is None or d["first_ts"] < first_ts:
                first_ts = d["first_ts"]
            if d["last_ts"] > last_ts:
                last_ts = d["last_ts"]
            msg_count += int(d.get("msg_count", 0) or 0)
            lv = int(d.get("level", 1))
            if lv > max_level:
                max_level = lv
        if _earliest:
            first_msg_id = _earliest.get("first_msg_id", "") or ""
        return {
            "first_seq": int(first_seq or 0),
            "last_seq": int(last_seq),
            "first_ts": float(first_ts or 0.0),
            "last_ts": float(last_ts),
            "first_msg_id": first_msg_id,
            "last_msg_id": last_msg_id,
            "msg_count": msg_count,
            "max_level": max_level,
        }

    def assemble_summary_header(self) -> str:
        """Concatenate all summaries into one historical-context block.

        Each phase header carries a navigation breadcrumb — msg_id range,
        message count, full date range on both ends — so the agent can
        call read_history(action="range", from_msg_id=..., to_msg_id=...)
        to retrieve the exact original messages behind this summary.
        """
        from datetime import datetime

        docs = self.get_all_summaries()
        if not docs:
            return ""
        parts = ["[Conversation summary - earlier messages compacted]\n"]
        for d in docs:
            lv = int(d.get("level", 1))
            tag = "Archived phase" if lv >= 2 else "Recent phase"
            _fid = d.get("first_msg_id", "") or ""
            _lid = d.get("last_msg_id", "") or ""
            _mc = int(d.get("msg_count", 0) or 0)
            _fts = float(d.get("first_ts", 0.0) or 0.0)
            _lts = float(d.get("last_ts", 0.0) or 0.0)
            _fts_str = (datetime.fromtimestamp(_fts).strftime("%Y-%m-%d %H:%M")
                         if _fts else "?")
            _lts_str = (datetime.fromtimestamp(_lts).strftime("%Y-%m-%d %H:%M")
                         if _lts else "?")
            parts.append(
                f"\n=== {tag} ({d.get('bucket_id')}, level={lv}, seq "
                f"{d.get('first_seq')}..{d.get('last_seq')}"
                + (f", {_mc} msgs" if _mc else "")
                + f", {_fts_str} → {_lts_str}) ===\n"
            )
            if _fid and _lid:
                parts.append(
                    f"[To retrieve the exact original messages behind this "
                    f"phase, call read_history(action=\"range\", "
                    f"from_msg_id=\"{_fid}\", to_msg_id=\"{_lid}\").]\n"
                )
            parts.append(f"{d.get('summary', '')}\n")
            # Render structured tool_trace as an appended block. The
            # summarizer prompt already asks for a "## Files & operations"
            # section in the narrative, but the structured form is the
            # authoritative record (narrative may abridge), so we emit
            # both. Cheap duplication (~few hundred chars per bucket).
            _tt = d.get("tool_trace")
            if _tt:
                try:
                    from core.tool_activity_digest import (
                        format_activity_digest, is_empty)
                    if not is_empty(_tt):
                        parts.append(format_activity_digest(_tt) + "\n")
                except Exception:
                    logger.debug("tool_trace render failed", exc_info=True)
        return "".join(parts)

    def invalidate_from_seq(self, min_seq: int) -> int:
        """Wipe every bucket whose range overlaps with [min_seq, ∞).

        Fires when shared.jsonl is edited/deleted: the buckets whose
        summaries covered a modified seq now point to content that
        no longer matches reality. Rather than attempting partial-
        bucket patching, we wipe them wholesale and let the bg worker
        rebuild from the new shared state on the next trigger.

        Cascading SB invalidation is automatic via the shared rule
        "last_seq >= min_seq": an SB's last_seq is the max of its
        sources, so if any source is wiped the SB's last_seq (which
        also >= min_seq) ensures the SB itself is wiped.

        Returns the count of wiped buckets. Does NOT reset
        _next_b_num / _next_sb_num — the counters stay monotonic so
        future bucket ids never collide with the ones that survived.
        """
        with self._lock:
            ids = list(self._meta.get("objects", []))
            to_keep: List[str] = []
            to_wipe: List[str] = []
            for bid in ids:
                d = self._read_bucket(bid)
                if not d:
                    continue
                if int(d.get("last_seq", 0) or 0) >= min_seq:
                    to_wipe.append(bid)
                else:
                    to_keep.append(bid)
            if not to_wipe:
                return 0
            for bid in to_wipe:
                p = self._bucket_path(bid)
                try:
                    if p.exists():
                        p.unlink()
                except Exception as e:
                    logger.warning(
                        "[bucket-store] failed to delete %s: %s", bid, e)
            self._meta["objects"] = to_keep
            # Recompute last_seq / last_ts from survivors
            _max_seq = 0
            _max_ts = 0.0
            for bid in to_keep:
                d = self._read_bucket(bid)
                if d:
                    _max_seq = max(_max_seq,
                                    int(d.get("last_seq", 0) or 0))
                    _max_ts = max(_max_ts,
                                   float(d.get("last_ts", 0.0) or 0.0))
            self._meta["last_seq"] = _max_seq
            self._meta["last_ts"] = _max_ts
            self._save_meta()
            logger.info(
                "[bucket-store] invalidated %d bucket(s) from seq %d "
                "(kept %d, new last_seq=%d)",
                len(to_wipe), min_seq, len(to_keep), _max_seq)
            return len(to_wipe)

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
