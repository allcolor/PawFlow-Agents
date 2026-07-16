"""Background bucket-build pipeline for BgBucketBuilder: transcript scanning,
chunk selection, bucket construction, rollup/consolidation, and progress
publishing.

Split out of bg_bucket_builder.py as a leaf mixin so the file stays <= 800
lines. These methods rely on host state/methods provided by BgBucketBuilder
(self._lock, the seq caches, note_* accounting, _resolve_summarizer,
_bg_compact_config, etc.) resolved through the MRO.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.bucket_store import BucketStore
from core.segmented_jsonl import SegmentedJsonl
from core.tool_activity_digest import (
    extract_tool_activity, format_activity_digest, is_empty, merge_traces,
)

logger = logging.getLogger(__name__)


def _build_embed_fn(user_id: str = "", conversation_id: str = ""):
    """Return the configured memory embedding function.

    `${embedding_llm_service}` wins when it points to an embedding-capable
    LLM service. Otherwise PawFlow keeps the existing local MiniLM fallback.
    The summarizer LLM service is intentionally not reused implicitly for
    embeddings.
    """
    from core.embeddings import build_memory_embed_fn
    return build_memory_embed_fn(user_id=user_id, conversation_id=conversation_id)


# Extra compact_instructions passed to the injected summarize_fn for
# bucket building. Orients the summarizer toward multi-agent context
# and the Files & operations requirement. Activity digest is appended
# at call time when present (see _build_one_bucket).
_BUCKET_COMPACT_INSTRUCTIONS = (
    "Summarise this phase of a multi-agent conversation. Messages are "
    "prefixed with [Agent X]: / [User to agent X]: — preserve agent "
    "attribution in the narrative. End the summary with a "
    "`## Files & operations` section listing files touched (edited / "
    "read / created / deleted), commands run, delegations. Copy file "
    "paths VERBATIM when you have them — never paraphrase paths. If a "
    "message quotes a compacted context block or provider context dump "
    "for debugging, describe it as quoted evidence; do not reproduce "
    "`<conversation_history>`, `</conversation_history>`, `Recent "
    "messages below`, or file-restore postambles verbatim as current "
    "conversation state."
)

# Extra compact_instructions for rollup/collapse (N → 1 consolidation).
_ROLLUP_COMPACT_INSTRUCTIONS = (
    "You are merging consecutive phase summaries of a multi-agent "
    "conversation into one super-summary. Keep decisions, user intent "
    "evolution, agent handoffs. UNION the `## Files & operations` "
    "lists — deduplicate file paths, keep the most-touched up to 30. "
    "Be denser than the sources. If a source summary mentions quoted "
    "compact/provider context dumps, preserve the diagnosis but do not "
    "copy wrapper markers or file-restore postambles verbatim."
)




class _BgBucketBuildMixin:
    """Background bucket build/rollup/publish pipeline for BgBucketBuilder."""

    def _seed_seq_caches(self, cid: str) -> None:
        """One-shot seed of shared/pyramid/transcript caches for a cid
        on first access per process. Subsequent writes feed the caches
        via note_* — so this runs AT MOST once per cid per process.

        - Pyramid last_seq: meta.json read (one small file).
        - Shared max seq: last-line read (O(1) in file size).
        - Unbucketed shared rows: count of lines in shared.jsonl with
          seq > pyramid last_seq. One scan, proportional to file size,
          but runs at most once per cid per process.
        - Transcript chars post-pyramid: sum of payload chars in
          transcript.jsonl with seq > pyramid last_seq. Same one-shot
          scan policy.
        """
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        conv_dir = cs._conv_dir(cid)

        # Pyramid seed: meta.json read (one small file).
        store = BucketStore.get(conv_dir)
        pyramid_seq = store.last_seq

        # Shared seed: last-line read (O(1) in file size).
        shared_path = cs._shared_ctx_path(cid)
        shared_seq = self._read_last_seq(shared_path)

        # Unbucketed rows/chars: count shared rows and payload chars with
        # seq > pyramid last_seq.
        unbucketed = self._count_rows_since(shared_path, pyramid_seq)
        shared_chars = self._sum_chars_since(shared_path, pyramid_seq)

        # Transcript chars post-pyramid: sum payload chars (one scan).
        transcript_path = cs._transcript_path(cid)
        transcript_chars = self._sum_chars_since(transcript_path, pyramid_seq)

        with self._seq_cache_lock:
            # Don't clobber if another thread populated via note_* already
            self._shared_seq_cache.setdefault(cid, shared_seq)
            self._pyramid_seq_cache.setdefault(cid, pyramid_seq)
            self._shared_unbucketed_rows_cache.setdefault(cid, unbucketed)
            self._shared_unbucketed_chars_cache.setdefault(cid, shared_chars)
            self._transcript_chars_post_pyramid_cache.setdefault(
                cid, transcript_chars)

    @staticmethod
    def _sum_chars_since(path, after_seq: int) -> int:
        """Sum of `content` (str) char counts in JSONL rows with
        seq > after_seq. 0 if file missing.

        Used at cold-cache seed only. Hot path uses note_* cache.
        """
        return _BgBucketBuildMixin._sum_chars_in_range(
            path, after_seq + 1, 1 << 62)

    @staticmethod
    def _sum_chars_in_range(path, first_seq: int, last_seq: int) -> int:
        """Sum of payload char counts in logical JSONL rows with
        first_seq <= seq <= last_seq. 0 if the stream is missing.
        """
        log = SegmentedJsonl(path)
        if not log.exists():
            return 0
        total = 0
        try:
            for d in log.iter_rows():
                s = int(d.get("seq") or 0)
                if s < first_seq or s > last_seq:
                    continue
                c = d.get("content")
                if isinstance(c, str):
                    total += len(c)
                elif isinstance(c, list):
                    for p in c:
                        if isinstance(p, dict):
                            t = p.get("text") or ""
                            if isinstance(t, str):
                                total += len(t)
        except Exception:
            logger.debug("[bg-bucket] _sum_chars_in_range failed",
                          exc_info=True)
        return total

    @staticmethod
    def _count_rows_since(path, after_seq: int) -> int:
        """Count logical JSONL rows with seq > after_seq. 0 if missing."""
        log = SegmentedJsonl(path)
        if not log.exists():
            return 0
        n = 0
        try:
            for d in log.iter_rows():
                if int(d.get("seq") or 0) > after_seq:
                    n += 1
        except Exception:
            logger.debug("[bg-bucket] _count_rows_since failed", exc_info=True)
        return n

    @staticmethod
    def _read_last_seq(path) -> int:
        """Read the seq of the last logical JSONL record."""
        log = SegmentedJsonl(path)
        if not log.exists():
            return 0
        try:
            for row in log.iter_rows_reverse():
                return int(row.get("seq") or 0)
        except (OSError, ValueError):
            return 0
        return 0

    def _shared_gap(self, cid: str) -> int:
        """Count shared rows with seq > pyramid.last_seq."""
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        conv_dir = cs._conv_dir(cid)
        store = BucketStore.get(conv_dir)
        return self._count_rows_since(cs._shared_ctx_path(cid), store.last_seq)

    def _run_job(self, cid: str, user_id: str) -> None:
        try:
            self._build_pending_buckets(cid, user_id)
        except Exception:
            logger.exception("[bg-bucket] job failed for cid=%s", cid[:8])
        finally:
            with self._pending_lock:
                self._pending.discard(cid)

    def _build_pending_buckets(self, cid: str, user_id: str) -> None:
        """Build L1 buckets until the shared gap is below trigger, then
        fire rollups as needed.

        Uses _build_one_bucket as the unit of work — the same primitive
        used by build_now_sync. The only difference between bg and sync
        paths is who calls _build_one_bucket (thread pool vs caller
        thread) and whether SSE progress is emitted.
        """
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        conv_dir = cs._conv_dir(cid)
        store = BucketStore.get(conv_dir)

        logger.info(
            "[bg-bucket] job start cid=%s user=%s pyramid_last_seq=%d "
            "objects=%d",
            cid[:8], user_id, store.last_seq, store.object_count)

        if self._summarizer_resolver is None or self._summarize_fn is None:
            logger.warning(
                "[bg-bucket] job abort cid=%s: resolver=%s summarize_fn=%s",
                cid[:8], self._summarizer_resolver is not None,
                self._summarize_fn is not None)
            return
        client, ctx_max, _svc_id = self._resolve_summarizer(user_id, cid)
        if not client:
            logger.info(
                "[bg-bucket] no summarizer for user=%s cid=%s — skipping",
                user_id, cid[:8])
            return

        cfg = self._bg_compact_config(cid, user_id)

        built = 0
        rollups_fired = 0
        while True:
            shared_msgs = self._load_shared_since(cid, store.last_seq)
            # allow_partial=True: trigger fires on seq gap (captures
            # transcript activity including tools). When a tool-heavy
            # conv hits the threshold, shared might not have a full
            # configured L1 worth of new rows yet — without partial
            # mode, _pick_chunk would return [] and the job no-ops
            # silently. Partial lets us bucket whatever shared does
            # have above the configured partial floor so the pyramid
            # keeps advancing even in tool-dominated conversations.
            chunk = self._pick_chunk(
                shared_msgs, store.object_count, allow_partial=True,
                cfg=cfg)
            if not chunk:
                break

            if not self._build_one_bucket(
                    cid, user_id, store, chunk, client, ctx_max,
                    cfg=cfg):
                break
            built += 1
            rollups_fired += self._rollup_until_stable(
                store, client, user_id, ctx_max, cid, cfg=cfg)

        rollups_fired += self._rollup_until_stable(
            store, client, user_id, ctx_max, cid, cfg=cfg)

        if built:
            self._publish_built(cid, built, store)

    def _load_shared_since(self, cid: str, after_seq: int) -> List[Dict]:
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        log = SegmentedJsonl(cs._shared_ctx_path(cid))
        if not log.exists():
            return []
        out: List[Dict] = []
        for row in log.iter_rows():
            if int(row.get("seq") or 0) > after_seq:
                out.append(row)
        # shared context order is (ts, seq)-sortable; sort defensively
        out.sort(key=lambda m: (
            float(m.get("ts") or m.get("timestamp") or 0.0),
            int(m.get("seq") or 0)))
        return out

    def _extract_trace(self, cid: str, first_seq: int, last_seq: int
                       ) -> Dict[str, Any]:
        """Load only the raw transcript seq window and extract tool activity."""
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        transcript = cs.load_transcript_seq_range(cid, first_seq, last_seq)
        return extract_tool_activity(transcript, first_seq, last_seq)

    def _pick_chunk(self, shared_msgs: List[Dict],
                     current_object_count: int,
                     allow_partial: bool = False,
                     cfg: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """Choose the next chunk of shared msgs for a single bucket.

        CORE INVARIANT: the last configured tail-reserve msgs are NEVER
        bucketed. They form the "recent window" that every post-compact
        output carries. Every branch subtracts tail_reserve_msgs from
        `n` before deciding — `available` is what's legitimately
        bucketable in this call.

        - Bulk catchup: pyramid empty AND total gap >= l1_trigger_msgs *
          bulk_catchup_multiplier -> one big bucket absorbs all pre-tail
          msgs (available). Internal chunker in _summarize_messages
          handles oversize input.
        - Normal L1: available >= l1_trigger_msgs.
        - Partial (sync only): available in [partial_min_msgs, L1)
          and allow_partial=True -> flush what's bucketable.
        - Tail-only: available ≤ 0 → return [], nothing to do.
        """
        cfg = cfg or self._bg_compact_config()
        l1_trigger = int(cfg["l1_trigger_msgs"])
        tail_reserve = int(cfg["tail_reserve_msgs"])
        partial_min = int(cfg["partial_min_msgs"])
        bulk_multiplier = float(cfg["bulk_catchup_multiplier"])

        n = len(shared_msgs)
        available = n - tail_reserve
        if available <= 0:
            return []
        bulk_threshold = int(l1_trigger * bulk_multiplier)
        if current_object_count == 0 and n >= bulk_threshold:
            return shared_msgs[:available]
        if available >= l1_trigger:
            return shared_msgs[:l1_trigger]
        if allow_partial and available >= partial_min:
            return shared_msgs[:available]
        return []

    def _build_one_bucket(self, cid: str, user_id: str,
                           store: BucketStore, chunk: List[Dict],
                           client: Any, ctx_max: int,
                           cfg: Optional[Dict[str, Any]] = None) -> bool:
        """Summarize one chunk of shared msgs and append as a bucket.

        Delegates the summarize call (and its internal chunking for
        oversized inputs) to the injected self._summarize_fn (i.e.
        AgentSummarizeMixin._summarize_messages). Persists narrative
        + structured tool_trace via store.add_bucket.

        Return True on success, False on empty/failed summary
        (caller breaks the loop).
        """
        if not chunk or self._summarize_fn is None:
            return False
        cfg = cfg or self._bg_compact_config(cid, user_id)
        bucket_target_tokens = int(cfg["bucket_target_tokens"])
        chars_per_token = float(cfg["chars_per_token"])
        overshoot_multiplier = float(cfg["overshoot_warn_multiplier"])

        first_seq = int(chunk[0].get("seq") or 0)
        last_seq = int(chunk[-1].get("seq") or 0)
        first_ts = float(chunk[0].get("ts")
                           or chunk[0].get("timestamp") or 0.0)
        last_ts = float(chunk[-1].get("ts")
                          or chunk[-1].get("timestamp") or 0.0)
        first_msg_id = chunk[0].get("msg_id") or ""
        last_msg_id = chunk[-1].get("msg_id") or ""

        trace = self._extract_trace(cid, first_seq, last_seq)

        # Turn shared-msg dicts into LLMMessages for the summarize_fn.
        # Shared content is already prefixed with [Agent X]: etc. —
        # no further attribution needed.
        from core.llm_client import LLMMessage
        llm_msgs: List[LLMMessage] = []
        chunk_shared_chars = 0
        for m in chunk:
            role = m.get("role", "user")
            content = m.get("content", "")
            if not isinstance(content, str):
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text")
                else:
                    content = str(content)
            chunk_shared_chars += len(content)
            llm_msgs.append(LLMMessage(
                role=role, content=content,
                conversation_id=cid,
                msg_id=m.get("msg_id", "") or "",
                timestamp=float(m.get("ts")
                                 or m.get("timestamp") or 0.0),
                seq=int(m.get("seq") or 0),
            ))

        # compact_instructions carries the activity digest as a bias so
        # the summarizer knows to cite exact paths/commands.
        activity_text = (format_activity_digest(trace)
                          if not is_empty(trace) else "")
        extra_instr = _BUCKET_COMPACT_INSTRUCTIONS
        if activity_text:
            extra_instr = (
                f"{extra_instr}\n\n"
                f"Use this tool activity reference (copy paths "
                f"VERBATIM; group by operation type):\n\n{activity_text}"
            )

        try:
            summary = self._summarize_fn(
                llm_msgs, client,
                max_tokens=ctx_max or 0,
                target_tokens=bucket_target_tokens,
                conversation_id=cid,
                agent_name="",
                compact_instructions=extra_instr,
                user_id=user_id,
            )
        except Exception:
            logger.exception(
                "[bg-bucket] summarize failed cid=%s seq %d..%d",
                cid[:8], first_seq, last_seq)
            return False

        if not summary or len(summary.strip()) < 20:
            logger.warning(
                "[bg-bucket] empty summary cid=%s seq %d..%d — aborting",
                cid[:8], first_seq, last_seq)
            return False

        try:
            model = getattr(client, "default_model", "") or ""
        except Exception:
            model = ""
        store.add_bucket(
            first_seq=first_seq, last_seq=last_seq,
            first_ts=first_ts, last_ts=last_ts,
            summary=summary,
            first_msg_id=first_msg_id, last_msg_id=last_msg_id,
            msg_count=len(chunk),
            model=model, tool_trace=trace,
        )
        # Keep the O(1) cache in sync so subsequent maybe_trigger calls
        # reflect the new pyramid coverage without re-reading meta.json.
        self.note_pyramid_seq(cid, last_seq)
        self.note_pyramid_rows_bucketed(cid, len(chunk))
        self.note_shared_chars_bucketed(cid, chunk_shared_chars)
        # Decrement transcript-chars counter by the chars-weight covered
        # by [first_seq..last_seq]. We compute this from the same
        # transcript scan _extract_trace already paid for, so no extra
        # I/O. The estimate is rough (sum of payload chars in covered
        # rows) — accurate enough to keep the token-budget trigger
        # honest without paying tokenizer cost in the bg path.
        try:
            from core.conversation_store import ConversationStore
            _conv_dir = ConversationStore.instance()._conv_dir(cid)
            _tp = ConversationStore.instance()._transcript_path(cid)
            _covered_chars = self._sum_chars_in_range(
                _tp, first_seq, last_seq)
            if _covered_chars:
                self.note_pyramid_chars_bucketed(cid, _covered_chars)
        except Exception:
            logger.debug(
                "[bg-bucket] chars-bucketed decrement failed cid=%s",
                cid[:8], exc_info=True)
        logger.info(
            "[bg-bucket] built bucket cid=%s seq %d..%d (%d msgs, "
            "summary=%d chars)",
            cid[:8], first_seq, last_seq, len(chunk), len(summary))
        # Summarizer overshoot surveillance: when the LLM returns a
        # summary far above `target_tokens=BUCKET_OUTPUT_TARGET`, the
        # pyramid header bloats proportionally — 5 buckets at 3x target
        # put the header at 30k tokens, matching the entire nominal
        # HEADER_BUDGET, and /compact has to compress it privately
        # every time (step 2c). Make the drift visible so we can tune
        # the summarizer prompt or lower the target if it's chronic.
        _summary_tokens_est = int(len(summary) / chars_per_token)
        if _summary_tokens_est > bucket_target_tokens * overshoot_multiplier:
            logger.warning(
                "[bg-bucket] L1 summary overshoot cid=%s seq %d..%d: "
                "~%d tokens (target=%d, %.1fx). Pyramid header will "
                "bloat faster than designed; rollup fires earlier.",
                cid[:8], first_seq, last_seq, _summary_tokens_est,
                bucket_target_tokens,
                _summary_tokens_est / float(bucket_target_tokens))

        # Feed the memory extractor: each bucket summary is a distilled
        # phase of the conversation — exactly the right granularity for
        # long-term facts/preferences/decisions. Runs here (bg worker)
        # so the hot path compact stays fast. Best-effort, failures
        # never propagate.
        try:
            from core.memory_auto_extract import auto_extract_memories
            auto_extract_memories(
                user_id=user_id, summary=summary,
                agent_name="", llm_client=client,
                embed_fn=_build_embed_fn(user_id=user_id, conversation_id=cid),
                conversation_id=cid)
        except Exception:
            logger.debug(
                "[bg-bucket] auto_extract_memories failed for cid=%s "
                "bucket seq %d..%d",
                cid[:8], first_seq, last_seq, exc_info=True)
        # Skill loop: same distilled summary may contain a reusable
        # procedure worth proposing as a skill draft. Best-effort.
        try:
            from core.skill_loop import propose_skill_draft_from_summary
            propose_skill_draft_from_summary(
                user_id=user_id, summary=summary,
                llm_client=client, conversation_id=cid)
        except Exception:
            logger.debug(
                "[bg-bucket] skill draft proposal failed for cid=%s",
                cid[:8], exc_info=True)
        return True

    def _maybe_rollup(self, store: BucketStore, client: Any,
                       user_id: str, ctx_max: int, cid: str,
                       cfg: Optional[Dict[str, Any]] = None) -> bool:
        """Fire rollup_all_except_last when header exceeds budget OR
        object_count exceeds trigger count. Return True if a rollup or
        collapse actually fired."""
        cfg = cfg or self._bg_compact_config(cid, user_id)
        rollup_trigger_count = int(cfg["rollup_trigger_count"])
        header_budget_tokens = int(cfg["header_budget_tokens"])
        header_char_multiplier = float(cfg["header_char_multiplier"])
        over_count = store.object_count > rollup_trigger_count
        # HEADER_BUDGET is a token budget; convert to chars via 3.5
        # chars/token (consistent with agent_utils._estimate_tokens
        # default and agent_compaction._compact). The previous `*4`
        # assumed ~4 chars/token and over-estimated the char budget by
        # ~14%, letting the header drift to ~34k tokens before rollup.
        # Combined with LLM summarizers systematically overshooting
        # BUCKET_OUTPUT_TARGET (2-3× observed), a 5-bucket pyramid hit
        # ~32k tokens of header and sat just under the old `*4` ceiling
        # of 120k chars forever — rollup never fired, /compact had to
        # compress the header privately on every run (step 2c).
        # Tighten to `*3` (~26k tokens trigger, 20% margin under the
        # 30k nominal budget) so rollup kicks in early and keeps the
        # header well under HEADER_BUDGET even with summarizer drift.
        over_budget = (
            store.estimated_header_chars()
            > header_budget_tokens * header_char_multiplier)
        if not (over_count or over_budget):
            return False

        if store.object_count < 3:
            if store.object_count < 2:
                return False
            inputs = store.get_collapse_input()
            if not inputs:
                return False
            merged_trace = merge_traces(d.get("tool_trace") for d in inputs
                                          if d.get("tool_trace"))
            combined = self._consolidate_via_summarize(
                inputs, client, user_id, ctx_max, cid, cfg=cfg)
            if combined and len(combined.strip()) >= 20:
                try:
                    model = getattr(client, "default_model", "") or ""
                except Exception:
                    model = ""
                store.collapse_all(
                    combined, model=model,
                    tool_trace=merged_trace if not is_empty(merged_trace) else None)
                return True
            return False

        inputs = store.get_rollup_input()
        if not inputs:
            return False
        merged_trace = merge_traces(d.get("tool_trace") for d in inputs
                                      if d.get("tool_trace"))
        consolidated = self._consolidate_via_summarize(
            inputs, client, user_id, ctx_max, cid, cfg=cfg)
        if consolidated and len(consolidated.strip()) >= 20:
            try:
                model = getattr(client, "default_model", "") or ""
            except Exception:
                model = ""
            store.rollup_all_except_last(
                consolidated, model=model,
                tool_trace=merged_trace if not is_empty(merged_trace) else None)
            return True
        return False

    def _consolidate_via_summarize(self, bucket_docs: List[Dict],
                                     client: Any, user_id: str,
                                     ctx_max: int, cid: str,
                                     cfg: Optional[Dict[str, Any]] = None) -> str:
        """Merge N bucket summaries into one via the injected summarize
        pipeline. Inputs stay as text (each phase is one synthetic
        LLMMessage carrying the previous bucket's summary). The
        pipeline's internal chunking handles oversized consolidation.

        The resulting super-summary is also fed to the memory extractor
        — it's the highest-signal distillation we produce (N phases
        merged), so facts that emerge here are strong candidates for
        long-term storage."""
        if not bucket_docs or self._summarize_fn is None:
            return ""
        cfg = cfg or self._bg_compact_config(cid, user_id)
        bucket_target_tokens = int(cfg["bucket_target_tokens"])
        chars_per_token = float(cfg["chars_per_token"])
        overshoot_multiplier = float(cfg["overshoot_warn_multiplier"])
        from core.llm_client import LLMMessage
        llm_msgs: List[LLMMessage] = []
        for d in bucket_docs:
            bid = d.get("bucket_id", "?")
            fs = d.get("first_seq", 0)
            ls = d.get("last_seq", 0)
            summary = d.get("summary", "") or ""
            content = f"=== Phase {bid} (seq {fs}..{ls}) ===\n{summary}"
            llm_msgs.append(LLMMessage(
                role="user", content=content, conversation_id=cid))
        try:
            result = self._summarize_fn(
                llm_msgs, client,
                max_tokens=ctx_max or 0,
                target_tokens=bucket_target_tokens,
                conversation_id=cid,
                agent_name="",
                compact_instructions=_ROLLUP_COMPACT_INSTRUCTIONS,
                user_id=user_id,
            )
        except Exception:
            logger.exception("[bg-bucket] consolidate failed")
            return ""

        # Surveillance symmetric with L1 build: a rolled-up SB that
        # overshoots target_tokens defeats the rollup's whole purpose
        # (the point is to shrink N buckets into one small summary).
        # Flag it so chronic drift doesn't silently keep the header
        # above budget even post-rollup.
        if result:
            _result_tokens_est = int(len(result) / chars_per_token)
            if _result_tokens_est > bucket_target_tokens * overshoot_multiplier:
                logger.warning(
                    "[bg-bucket] SB consolidation overshoot cid=%s: "
                    "~%d tokens (target=%d, %.1fx, from %d buckets). "
                    "Post-rollup header will stay large.",
                    cid[:8], _result_tokens_est, bucket_target_tokens,
                    _result_tokens_est / float(bucket_target_tokens),
                    len(bucket_docs))

        if result and user_id:
            try:
                from core.memory_auto_extract import auto_extract_memories
                auto_extract_memories(
                    user_id=user_id, summary=result,
                    agent_name="", llm_client=client,
                    embed_fn=_build_embed_fn(user_id=user_id, conversation_id=cid),
                    conversation_id=cid)
            except Exception:
                logger.debug("[bg-bucket] consolidate memory extract failed",
                              exc_info=True)
            try:
                from core.skill_loop import propose_skill_draft_from_summary
                propose_skill_draft_from_summary(
                    user_id=user_id, summary=result,
                    llm_client=client, conversation_id=cid)
            except Exception:
                logger.debug("[bg-bucket] consolidate skill draft failed",
                              exc_info=True)
        return result

    def _publish_progress(self, cid: str, stage: str,
                           payload: Dict[str, Any]) -> None:
        """Publish compact_progress SSE with a pyramid-build stage.
        Stages: bucket_building, rollup_merging."""
        try:
            from core.conversation_event_bus import ConversationEventBus
            data = {"stage": stage, **payload}
            ConversationEventBus.instance().publish_event(
                cid, "compact_progress", data)
        except Exception:
            logger.debug("[bg-bucket] progress SSE failed", exc_info=True)

    def _publish_built(self, cid: str, count: int,
                        store: BucketStore) -> None:
        try:
            from core.conversation_event_bus import ConversationEventBus
            ConversationEventBus.instance().publish_event(
                cid, "shared_bucket_built", {
                    "built": count,
                    "object_count": store.object_count,
                    "last_seq": store.last_seq,
                })
        except Exception:
            logger.debug("[bg-bucket] SSE publish failed", exc_info=True)
