"""Background builder for the shared pyramid (summaries/_shared/).

One worker pool, one job per conversation at a time. Fires from
ConversationStore._append_shared_ctx when the shared-seq gap since
the last bucket exceeds L1_TRIGGER_MSGS. Reads shared.jsonl for
conversational content, reads transcript.jsonl for tool activity
(stripped from shared), feeds both to the summarizer, persists a
level-1 bucket. If the pyramid header outgrows HEADER_BUDGET or the
object count exceeds ROLLUP_TRIGGER_COUNT, also fires a rollup.

This module is the ONLY writer of BucketStore in production code
paths. Agent compact is read-only on the pyramid.

Resolver injection: the summarizer service lives behind mixin
methods on AgentLoopTask that require task-context (self.config,
schema, etc.). Rather than import the mixin here (circular), the bg
worker accepts a resolver callable at startup:

    BgBucketBuilder.instance().set_summarizer_resolver(
        lambda uid: some_agent_task._get_summarizer_client(uid))

If no resolver is set, maybe_trigger is a silent no-op (the hot path
handles "pyramid empty" gracefully, so this is non-fatal).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.bucket_store import (
    BUCKET_OUTPUT_TARGET, HEADER_BUDGET, L1_TRIGGER_MSGS,
    ROLLUP_TRIGGER_COUNT, BucketStore,
)
from core.tool_activity_digest import (
    extract_tool_activity, format_activity_digest, is_empty, merge_traces,
)

logger = logging.getLogger(__name__)


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
    "paths VERBATIM when you have them — never paraphrase paths."
)

# Extra compact_instructions for rollup/collapse (N → 1 consolidation).
_ROLLUP_COMPACT_INSTRUCTIONS = (
    "You are merging consecutive phase summaries of a multi-agent "
    "conversation into one super-summary. Keep decisions, user intent "
    "evolution, agent handoffs. UNION the `## Files & operations` "
    "lists — deduplicate file paths, keep the most-touched up to 30. "
    "Be denser than the sources."
)


class BgBucketBuilder:
    """Thread-pooled background bucket builder. Singleton.

    Two dependencies are injected at startup (DI avoids circular imports
    with tasks/ai/):
      - summarizer_resolver(user_id) -> (client, ctx_max, svc_id)
      - summarize_fn(messages, client, **kwargs) -> str
        Expected signature: AgentSummarizeMixin._summarize_messages.
        The bg worker relies on the existing chunked-summarize pipeline
        (_summarize_chunked) to handle arbitrarily large inputs — no
        hand-rolled chunking here.
    """

    _instance_lock = threading.Lock()
    _instance: Optional["BgBucketBuilder"] = None

    # Trigger the "bulk catchup" shortcut when pyramid is empty and gap
    # is large: absorb all older msgs into one (big) bucket instead of
    # N L1 buckets. Internal chunking in _summarize_messages handles
    # oversize input — we only choose the slice.
    _BULK_CATCHUP_MULTIPLIER = 5  # gap > N × L1_TRIGGER_MSGS → bulk mode

    # Minimum size for a partial bucket (flushed in sync mode when the
    # caller forces a compact with msgs still "in progress"). Below
    # this, the gap is small enough to stay in the agent's tail cheaply.
    _PARTIAL_MIN = L1_TRIGGER_MSGS // 4  # ≈ 37 msgs

    @classmethod
    def instance(cls) -> "BgBucketBuilder":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="bg-bucket")
        self._pending: Set[str] = set()
        self._pending_lock = threading.Lock()
        # Resolver: user_id -> (client, ctx_max, svc_id) or (None, 0, "")
        self._summarizer_resolver: Optional[Callable[[str], Tuple]] = None
        # Summarize function (delegates internal chunking to the
        # existing AgentSummarizeMixin._summarize_messages pipeline).
        self._summarize_fn: Optional[Callable] = None

    def set_summarizer_resolver(
            self, resolver: Callable[[str], Tuple]) -> None:
        """Inject the summarizer client resolver.

        Expected signature matches AgentSummarizeMixin._get_summarizer_client:
            (user_id) -> (client_or_service, ctx_max_tokens, svc_id)
        """
        self._summarizer_resolver = resolver

    def set_summarize_fn(self, fn: Callable) -> None:
        """Inject the summarize function. Signature matches
        AgentSummarizeMixin._summarize_messages:
            (old_messages, client, max_tokens, target_tokens=0,
             conversation_id="", agent_name="", compact_instructions="",
             user_id="") -> str
        """
        self._summarize_fn = fn

    # ── Public trigger API ────────────────────────────────────────

    def maybe_trigger(self, cid: str, user_id: str) -> None:
        """Fast O(1) check + async enqueue. Called from
        ConversationStore._append_shared_ctx after each shared write.

        No-op if:
          - resolver not injected (no summarizer available);
          - a job for this cid is already in flight;
          - shared seq gap since last bucket < L1_TRIGGER_MSGS.
        """
        if not cid or not user_id:
            return
        if self._summarizer_resolver is None:
            return
        try:
            gap = self._shared_gap(cid)
            if gap < L1_TRIGGER_MSGS:
                return
        except Exception:
            logger.debug("[bg-bucket] trigger check failed", exc_info=True)
            return

        with self._pending_lock:
            if cid in self._pending:
                return
            self._pending.add(cid)

        try:
            self._executor.submit(self._run_job, cid, user_id)
        except RuntimeError:
            # Executor has been shutdown (e.g. test teardown)
            with self._pending_lock:
                self._pending.discard(cid)

    def flush(self, timeout: float = 60.0) -> None:
        """Block until every in-flight job for this process completes.

        Useful in tests to ensure async work settled before inspection.
        Manual /compact uses build_now_sync instead — it wants a
        guarantee on a specific conversation, not a global flush.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._pending_lock:
                if not self._pending:
                    return
            time.sleep(0.05)
        logger.warning("[bg-bucket] flush timed out after %.1fs", timeout)

    def build_now_sync(self, cid: str, user_id: str,
                        allow_partial: bool = True) -> Dict[str, Any]:
        """Synchronously build buckets for this conversation until the
        pyramid covers all of shared.jsonl.

        When allow_partial=True (default for manual/forced compacts),
        the final bucket may be smaller than L1_TRIGGER_MSGS — gap
        msgs "in progress" are flushed into a partial bucket rather
        than left in the agent's tail. Floor at _PARTIAL_MIN (~37
        msgs) to avoid paying for a tiny LLM call.

        Used by manual /compact on a conv whose pyramid is behind (or
        empty: import, long-idle). Blocks the caller. Emits
        compact_progress SSE per bucket built and per rollup fired so
        the UI can show progression.

        Bulk catchup shortcut: when the pyramid is empty AND the gap
        exceeds L1_TRIGGER_MSGS × _BULK_CATCHUP_MULTIPLIER, the first
        chunk absorbs everything except the last L1_TRIGGER_MSGS msgs
        in a single bucket. Internal chunking in the summarize pipeline
        handles oversize input. This keeps /compact on a 50k-msg
        imported conv bounded to ~20 LLM calls instead of ~333.
        """
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        conv_dir = cs._conv_dir(cid)
        store = BucketStore.get(conv_dir)

        if self._summarizer_resolver is None or self._summarize_fn is None:
            logger.warning(
                "[bg-bucket] build_now_sync: resolver or summarize_fn "
                "not registered — pyramid cannot advance. cid=%s", cid[:8])
            return {"buckets_built": 0, "rollups_fired": 0,
                     "final_object_count": store.object_count,
                     "final_last_seq": store.last_seq}

        client, ctx_max, _svc_id = self._summarizer_resolver(user_id)
        if not client:
            logger.warning(
                "[bg-bucket] build_now_sync: summarizer unavailable for "
                "user=%s cid=%s", user_id, cid[:8])
            return {"buckets_built": 0, "rollups_fired": 0,
                     "final_object_count": store.object_count,
                     "final_last_seq": store.last_seq}

        # Mark in-flight so maybe_trigger doesn't race us
        with self._pending_lock:
            self._pending.add(cid)

        buckets_built = 0
        rollups_fired = 0
        try:
            while True:
                shared_msgs = self._load_shared_since(cid, store.last_seq)
                chunk = self._pick_chunk(
                    shared_msgs, store.object_count,
                    allow_partial=allow_partial)
                if not chunk:
                    break

                if not self._build_one_bucket(
                        cid, user_id, store, chunk, client, ctx_max):
                    break
                buckets_built += 1
                self._publish_progress(cid, "bucket_building", {
                    "buckets_built": buckets_built,
                    "object_count": store.object_count,
                    "bucket_msg_count": len(chunk),
                })

                if self._maybe_rollup(store, client, user_id, ctx_max):
                    rollups_fired += 1
                    self._publish_progress(cid, "rollup_merging", {
                        "rollups_fired": rollups_fired,
                        "object_count": store.object_count,
                    })
        finally:
            with self._pending_lock:
                self._pending.discard(cid)

        result = {
            "buckets_built": buckets_built,
            "rollups_fired": rollups_fired,
            "final_object_count": store.object_count,
            "final_last_seq": store.last_seq,
        }
        if buckets_built or rollups_fired:
            logger.info(
                "[bg-bucket] build_now_sync done cid=%s: %s",
                cid[:8], result)
        return result

    # ── Internals ─────────────────────────────────────────────────

    def _shared_gap(self, cid: str) -> int:
        """Count shared msgs with seq > pyramid.last_seq."""
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        conv_dir = cs._conv_dir(cid)
        store = BucketStore.get(conv_dir)
        shared_path = cs._shared_ctx_path(cid)
        if not shared_path.exists():
            return 0
        last_seq = store.last_seq
        count = 0
        import json as _json
        with open(shared_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if int(row.get("seq") or 0) > last_seq:
                    count += 1
        return count

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

        if self._summarizer_resolver is None or self._summarize_fn is None:
            return
        client, ctx_max, _svc_id = self._summarizer_resolver(user_id)
        if not client:
            logger.info(
                "[bg-bucket] no summarizer for user=%s cid=%s — skipping",
                user_id, cid[:8])
            return

        built = 0
        while True:
            shared_msgs = self._load_shared_since(cid, store.last_seq)
            # Async path: strict — never builds partial buckets.
            # Partial finalisation is the sync path's job.
            chunk = self._pick_chunk(
                shared_msgs, store.object_count, allow_partial=False)
            if not chunk:
                break

            if not self._build_one_bucket(
                    cid, user_id, store, chunk, client, ctx_max):
                break
            built += 1
            self._maybe_rollup(store, client, user_id, ctx_max)

        if built:
            self._publish_built(cid, built, store)

    def _load_shared_since(self, cid: str, after_seq: int) -> List[Dict]:
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        path = cs._shared_ctx_path(cid)
        if not path.exists():
            return []
        import json as _json
        out: List[Dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if int(row.get("seq") or 0) > after_seq:
                    out.append(row)
        # shared.jsonl order is (ts, seq)-sortable; sort defensively
        out.sort(key=lambda m: (
            float(m.get("ts") or m.get("timestamp") or 0.0),
            int(m.get("seq") or 0)))
        return out

    def _extract_trace(self, cid: str, first_seq: int, last_seq: int
                       ) -> Dict[str, Any]:
        """Load raw transcript slice and extract tool activity."""
        from core.conversation_store import ConversationStore
        cs = ConversationStore.instance()
        transcript = cs.load(cid) or []
        return extract_tool_activity(transcript, first_seq, last_seq)

    def _pick_chunk(self, shared_msgs: List[Dict],
                     current_object_count: int,
                     allow_partial: bool = False) -> List[Dict]:
        """Choose the next chunk of shared msgs for a single bucket.

        - Normal case: L1_TRIGGER_MSGS msgs.
        - Bulk catchup: pyramid is empty AND we have
          >= L1_TRIGGER_MSGS × _BULK_CATCHUP_MULTIPLIER msgs waiting →
          absorb everything except the last L1_TRIGGER_MSGS into one
          bucket. The summarize pipeline's internal chunker handles
          oversize input — no manual chunking here.
        - Partial (sync only): gap < L1_TRIGGER_MSGS and allow_partial
          → build a smaller bucket with whatever remains. Floor at
          _PARTIAL_MIN to avoid paying an LLM call for a tiny chunk.
        """
        n = len(shared_msgs)
        bulk_threshold = L1_TRIGGER_MSGS * self._BULK_CATCHUP_MULTIPLIER
        if current_object_count == 0 and n >= bulk_threshold:
            bulk_size = n - L1_TRIGGER_MSGS
            return shared_msgs[:bulk_size]
        if n >= L1_TRIGGER_MSGS:
            return shared_msgs[:L1_TRIGGER_MSGS]
        if allow_partial and n >= self._PARTIAL_MIN:
            return shared_msgs[:n]
        return []

    def _build_one_bucket(self, cid: str, user_id: str,
                           store: BucketStore, chunk: List[Dict],
                           client: Any, ctx_max: int) -> bool:
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
                target_tokens=BUCKET_OUTPUT_TARGET,
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
        logger.info(
            "[bg-bucket] built bucket cid=%s seq %d..%d (%d msgs, "
            "summary=%d chars)",
            cid[:8], first_seq, last_seq, len(chunk), len(summary))

        # Feed the memory extractor: each bucket summary is a distilled
        # phase of the conversation — exactly the right granularity for
        # long-term facts/preferences/decisions. Runs here (bg worker)
        # so the hot path compact stays fast. Best-effort, failures
        # never propagate.
        try:
            from core.memory_auto_extract import auto_extract_memories
            auto_extract_memories(
                user_id=user_id, summary=summary,
                agent_name="", llm_client=client)
        except Exception:
            logger.debug(
                "[bg-bucket] auto_extract_memories failed for cid=%s "
                "bucket seq %d..%d",
                cid[:8], first_seq, last_seq, exc_info=True)
        return True

    def _maybe_rollup(self, store: BucketStore, client: Any,
                       user_id: str, ctx_max: int) -> bool:
        """Fire rollup_all_except_last when header exceeds budget OR
        object_count exceeds trigger count. Return True if a rollup or
        collapse actually fired."""
        over_count = store.object_count > ROLLUP_TRIGGER_COUNT
        # HEADER_BUDGET is a token budget; compare against chars with a
        # ~4-chars-per-token approximation (conservative — overestimates
        # rollup frequency a bit, which is fine).
        over_budget = store.estimated_header_chars() > HEADER_BUDGET * 4
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
                inputs, client, user_id, ctx_max)
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
            inputs, client, user_id, ctx_max)
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
                                     ctx_max: int) -> str:
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
        from core.llm_client import LLMMessage
        llm_msgs: List[LLMMessage] = []
        _cid = "_bucket_rollup"
        for d in bucket_docs:
            bid = d.get("bucket_id", "?")
            fs = d.get("first_seq", 0)
            ls = d.get("last_seq", 0)
            summary = d.get("summary", "") or ""
            content = f"=== Phase {bid} (seq {fs}..{ls}) ===\n{summary}"
            llm_msgs.append(LLMMessage(
                role="user", content=content, conversation_id=_cid))
        try:
            result = self._summarize_fn(
                llm_msgs, client,
                max_tokens=ctx_max or 0,
                target_tokens=BUCKET_OUTPUT_TARGET,
                conversation_id=_cid,
                agent_name="",
                compact_instructions=_ROLLUP_COMPACT_INSTRUCTIONS,
                user_id=user_id,
            )
        except Exception:
            logger.exception("[bg-bucket] consolidate failed")
            return ""

        if result and user_id:
            try:
                from core.memory_auto_extract import auto_extract_memories
                auto_extract_memories(
                    user_id=user_id, summary=result,
                    agent_name="", llm_client=client)
            except Exception:
                logger.debug("[bg-bucket] consolidate memory extract failed",
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
