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
from typing import Any, Callable, Dict, Optional, Set, Tuple

from core.bucket_store import (
    BUCKET_OUTPUT_TARGET, HEADER_BUDGET, L1_TRIGGER_MSGS,
    ROLLUP_TRIGGER_COUNT, TAIL_RESERVE, TAIL_TOKEN_BUDGET, BucketStore,
)
from core._bg_bucket_build import _BgBucketBuildMixin

logger = logging.getLogger(__name__)


class BgBucketBuilder(_BgBucketBuildMixin):
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

    # Minimum size for a partial bucket. With token-based triggering,
    # bg fires whenever transcript expansion threatens to bust the
    # /compact tail budget — we want to be able to flush even small
    # shared msg counts (5-10) when the underlying transcript has
    # ballooned with tool I/O. Floor stays > 0 so we don't pay the
    # LLM cost for trivial chunks.
    _PARTIAL_MIN = 5
    _MIN_BG_INPUT_MULTIPLIER = 4
    _CHARS_PER_TOKEN_EST = 3.5

    @staticmethod
    def _coerce_positive_int(name: str, raw: Any, default: int,
                             minimum: int = 1) -> int:
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            logger.warning("[bg-bucket] invalid %s=%r; using default %r",
                           name, raw, default)
            return default
        if value < minimum:
            logger.warning("[bg-bucket] invalid %s=%r; minimum is %d; using default %r",
                           name, raw, minimum, default)
            return default
        return value

    @staticmethod
    def _coerce_positive_float(name: str, raw: Any, default: float,
                               minimum: float = 0.0) -> float:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            logger.warning("[bg-bucket] invalid %s=%r; using default %r",
                           name, raw, default)
            return default
        if value <= minimum:
            logger.warning("[bg-bucket] invalid %s=%r; must be > %s; using default %r",
                           name, raw, minimum, default)
            return default
        return value

    def _bg_compact_config(self, cid: str = "", user_id: str = "") -> Dict[str, Any]:
        try:
            from core.summarizer_bindings import resolve_service
            summarizer, _sdef, _explicit = resolve_service(user_id, cid)
            if summarizer and hasattr(summarizer, "bg_compact_config"):
                return summarizer.bg_compact_config()
        except Exception:
            logger.debug("[bg-bucket] failed to resolve summarizer bg config",
                         exc_info=True)
        from services.summarizer_service import SummarizerService
        raw = SummarizerService({"llm_service": "_unused"}).bg_compact_config()
        return {
            "l1_trigger_msgs": self._coerce_positive_int(
                "l1_trigger_msgs", raw["l1_trigger_msgs"], L1_TRIGGER_MSGS),
            "bucket_target_tokens": self._coerce_positive_int(
                "bucket_target_tokens", raw["bucket_target_tokens"], BUCKET_OUTPUT_TARGET),
            "header_budget_tokens": self._coerce_positive_int(
                "header_budget_tokens", raw["header_budget_tokens"], HEADER_BUDGET),
            "rollup_trigger_count": self._coerce_positive_int(
                "rollup_trigger_count", raw["rollup_trigger_count"], ROLLUP_TRIGGER_COUNT),
            "tail_reserve_msgs": self._coerce_positive_int(
                "tail_reserve_msgs", raw["tail_reserve_msgs"], TAIL_RESERVE,
                minimum=0),
            "tail_token_budget": self._coerce_positive_int(
                "tail_token_budget", raw["tail_token_budget"], TAIL_TOKEN_BUDGET),
            "token_trigger_fraction": self._coerce_positive_float(
                "token_trigger_fraction", raw["token_trigger_fraction"], 0.7),
            "bulk_catchup_multiplier": self._coerce_positive_float(
                "bulk_catchup_multiplier", raw["bulk_catchup_multiplier"], 5.0),
            "partial_min_msgs": self._coerce_positive_int(
                "partial_min_msgs", raw["partial_min_msgs"], self._PARTIAL_MIN),
            "min_input_multiplier": self._coerce_positive_float(
                "min_input_multiplier", raw["min_input_multiplier"],
                float(self._MIN_BG_INPUT_MULTIPLIER)),
            "chars_per_token": self._coerce_positive_float(
                "chars_per_token", raw["chars_per_token"], self._CHARS_PER_TOKEN_EST),
            "overshoot_warn_multiplier": self._coerce_positive_float(
                "overshoot_warn_multiplier", raw["overshoot_warn_multiplier"], 1.5),
            "header_char_multiplier": self._coerce_positive_float(
                "header_char_multiplier", raw["header_char_multiplier"], 3.0),
        }

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
        self._seed_pending: Set[str] = set()
        self._trigger_pending: Set[str] = set()
        self._trigger_dirty: Set[str] = set()
        self._pending_lock = threading.Lock()
        # Resolver: user_id -> (client, ctx_max, svc_id) or (None, 0, "")
        self._summarizer_resolver: Optional[Callable[[str], Tuple]] = None
        # Summarize function (delegates internal chunking to the
        # existing AgentSummarizeMixin._summarize_messages pipeline).
        self._summarize_fn: Optional[Callable] = None
        # O(1) seq caches for maybe_trigger. The gap in SEQ space tracks
        # TRANSCRIPT ACTIVITY (not just shared content): every transcript
        # line — tool_use, tool_result, msg_patch, trace_update, etc. —
        # consumes a seq via _stamp_line. So seq_gap ≈ transcript rows
        # since last bucket.
        #
        # But seq_gap alone isn't enough to DECIDE to submit: if shared
        # has fewer than _PARTIAL_MIN + TAIL_RESERVE rows since the last
        # bucket, _pick_chunk returns [] (not even a partial fits), the
        # job no-ops, _pending discards, and the next transcript line
        # re-fires the trigger — a submit-storm with no real work.
        # _shared_unbucketed_rows_cache tracks the row count since the
        # last bucket so maybe_trigger can skip hopeless submits.
        self._shared_seq_cache: Dict[str, int] = {}
        self._pyramid_seq_cache: Dict[str, int] = {}
        self._shared_unbucketed_rows_cache: Dict[str, int] = {}
        self._shared_unbucketed_chars_cache: Dict[str, int] = {}
        # Transcript-side token estimate accumulated since last pyramid
        # advance. The /compact tail budget is measured in transcript
        # tokens (it includes tool_use/tool_result/msg_patch rows that
        # never reach shared.jsonl). When this estimate threatens
        # TAIL_TOKEN_BUDGET we fire a bucket build asynchronously so
        # the agent's hot-path compact stays deterministic.
        # Stored in chars (cheap to accumulate); converted to tokens
        # via /3.5 at decision time. Decremented when a bucket lands
        # (proportionally to that bucket's transcript char weight).
        self._transcript_chars_post_pyramid_cache: Dict[str, int] = {}
        self._seq_cache_lock = threading.Lock()
        # Diagnostic: throttle maybe_trigger log spam. Track last
        # (reason, state) logged per cid so we only log when the
        # decision changes — otherwise every shared write prints a
        # line.
        self._last_trigger_log: Dict[str, str] = {}


    def _seed_seq_caches_async(self, cid: str, user_id: str) -> bool:
        """Seed cold seq caches without blocking the foreground writer.

        maybe_trigger is called from ConversationStore while the conv lock
        is held, so cold-cache disk reads must happen in the bg executor.
        Returns True when a seed job was queued or already exists.
        """
        with self._pending_lock:
            if cid in self._seed_pending:
                return True
            self._seed_pending.add(cid)

        def _run_seed():
            try:
                self._seed_seq_caches(cid)
            except Exception:
                logger.warning("[bg-bucket] seq cache seed failed cid=%s",
                               cid[:8], exc_info=True)
            finally:
                with self._pending_lock:
                    self._seed_pending.discard(cid)
            self.maybe_trigger(cid, user_id)

        try:
            self._executor.submit(_run_seed)
        except RuntimeError:
            with self._pending_lock:
                self._seed_pending.discard(cid)
            return False
        return True

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

    def _resolve_summarizer(self, user_id: str, cid: str):
        """Call injected resolver with conversation scope when supported."""
        if self._summarizer_resolver is None:
            return None, 0, ""
        try:
            return self._summarizer_resolver(user_id, cid)
        except TypeError:
            return self._summarizer_resolver(user_id)

    def note_shared_seq(self, cid: str, seq: int) -> None:
        """O(1) hint that shared.jsonl now has a record at this seq.

        Called from ConversationStore._append_shared_ctx after writes
        complete — the whole point is to keep maybe_trigger off the
        disk while the conv lock is held. seq values are monotonic
        per conv (enforced by _stamp_line) so we only need max.
        """
        if not cid or not isinstance(seq, int):
            return
        with self._seq_cache_lock:
            cur = self._shared_seq_cache.get(cid, 0)
            if seq > cur:
                self._shared_seq_cache[cid] = seq

    def note_shared_rows_appended(self, cid: str, n: int) -> None:
        """O(1) hint that n new rows were appended to shared.jsonl.

        Called from ConversationStore._append_shared_ctx alongside
        note_shared_seq with the batch size. Accumulates into
        _shared_unbucketed_rows_cache; the counter is subtracted
        (not reset) by note_pyramid_rows_bucketed so in-flight appends
        racing with a bucket build don't get lost.
        """
        if not cid or not isinstance(n, int) or n <= 0:
            return
        with self._seq_cache_lock:
            if cid in self._shared_unbucketed_rows_cache:
                self._shared_unbucketed_rows_cache[cid] += n
            # Cold cache: _seed_seq_caches will populate it from disk.

    def note_shared_chars_appended(self, cid: str, n_chars: int) -> None:
        """O(1) hint for shared content chars since the last bucket."""
        if not cid or not isinstance(n_chars, int) or n_chars <= 0:
            return
        with self._seq_cache_lock:
            if cid in self._shared_unbucketed_chars_cache:
                self._shared_unbucketed_chars_cache[cid] += n_chars
            # Cold cache: _seed_seq_caches will populate it from disk.

    def note_shared_chars_bucketed(self, cid: str, n_chars: int) -> None:
        """O(1) decrement after a bucket absorbs shared content chars."""
        if not cid or not isinstance(n_chars, int) or n_chars <= 0:
            return
        with self._seq_cache_lock:
            if cid in self._shared_unbucketed_chars_cache:
                self._shared_unbucketed_chars_cache[cid] = max(
                    0, self._shared_unbucketed_chars_cache[cid] - n_chars)

    def note_transcript_bytes_appended(self, cid: str, n_chars: int) -> None:
        """O(1) hint that n_chars of transcript content were appended.

        Called from ConversationStore._append_ctx_file (the WRITER for
        transcript.jsonl) — that path already iterates each row, so
        summing payload chars is essentially free.

        These chars are tool_use / tool_result / msg_patch /
        trace_update payloads, NOT shared.jsonl conversational content
        (that has its own counter via note_shared_seq /
        note_shared_rows_appended). Conversational content also writes
        to transcript so it gets counted here too — fine, the tail
        budget is on transcript total.

        Decremented (never reset) by note_pyramid_chars_bucketed when
        a bucket lands; the bucket builder estimates the transcript
        chars covered by each chunk via _extract_trace + activity
        digest size.
        """
        if not cid or not isinstance(n_chars, int) or n_chars <= 0:
            return
        with self._seq_cache_lock:
            if cid in self._transcript_chars_post_pyramid_cache:
                self._transcript_chars_post_pyramid_cache[cid] += n_chars
            # Cold cache: _seed_seq_caches will populate from disk.

    def note_pyramid_chars_bucketed(self, cid: str, n_chars: int) -> None:
        """O(1) decrement after a bucket absorbs transcript content.

        Mirrors note_pyramid_rows_bucketed but for the chars counter.
        Called from _build_one_bucket with the chunk's transcript
        char-weight estimate.
        """
        if not cid or not isinstance(n_chars, int) or n_chars <= 0:
            return
        with self._seq_cache_lock:
            if cid in self._transcript_chars_post_pyramid_cache:
                self._transcript_chars_post_pyramid_cache[cid] = max(
                    0,
                    self._transcript_chars_post_pyramid_cache[cid] - n_chars)

    def note_pyramid_seq(self, cid: str, seq: int) -> None:
        """O(1) hint that the pyramid now covers up to this seq.

        Called from _build_one_bucket after add_bucket persists.
        Mirrors BucketStore.last_seq for maybe_trigger's gap math
        without forcing it to instantiate a BucketStore (which reads
        meta.json and ends up on disk).
        """
        if not cid or not isinstance(seq, int):
            return
        with self._seq_cache_lock:
            cur = self._pyramid_seq_cache.get(cid, 0)
            if seq > cur:
                self._pyramid_seq_cache[cid] = seq

    def note_pyramid_rows_bucketed(self, cid: str, n: int) -> None:
        """O(1) hint that n shared rows were just added to a bucket.

        Called from _build_one_bucket with chunk length after
        add_bucket. Decrements the unbucketed-rows counter so
        maybe_trigger's "can we build?" check sees the catch-up.
        """
        if not cid or not isinstance(n, int) or n <= 0:
            return
        with self._seq_cache_lock:
            if cid in self._shared_unbucketed_rows_cache:
                self._shared_unbucketed_rows_cache[cid] = max(
                    0, self._shared_unbucketed_rows_cache[cid] - n)

    # ── Public trigger API ────────────────────────────────────────

    def maybe_trigger_async(self, cid: str, user_id: str) -> bool:
        """Queue the trigger decision off the foreground writer path.

        ConversationStore calls this while holding no conv lock, but still
        before SSE publish. The decision path resolves parameters and may
        log state transitions, so keep it out of append_message and coalesce
        repeated requests for the same conversation.
        """
        if not cid or not user_id:
            return False
        with self._pending_lock:
            if cid in self._trigger_pending:
                self._trigger_dirty.add(cid)
                return True
            self._trigger_pending.add(cid)

        def _run_trigger():
            try:
                while True:
                    self.maybe_trigger(cid, user_id)
                    with self._pending_lock:
                        if cid not in self._trigger_dirty:
                            self._trigger_pending.discard(cid)
                            return
                        self._trigger_dirty.discard(cid)
            except Exception:
                logger.warning("[bg-bucket] trigger failed cid=%s",
                               cid[:8], exc_info=True)
                with self._pending_lock:
                    self._trigger_pending.discard(cid)
                    self._trigger_dirty.discard(cid)

        try:
            self._executor.submit(_run_trigger)
        except RuntimeError:
            with self._pending_lock:
                self._trigger_pending.discard(cid)
                self._trigger_dirty.discard(cid)
            return False
        return True

    def maybe_trigger(self, cid: str, user_id: str) -> None:
        """Check cached counters and enqueue a background bucket job.

        ConversationStore calls maybe_trigger_async() from the writer path;
        this method runs in the bg executor. It must still avoid disk I/O
        because it can run frequently, but it is no longer allowed to hold
        the conversation write lock while resolving config or logging.

        Trigger conditions (any of):
          1. Token-budget breach: estimated transcript-tokens-since-
             pyramid > TAIL_TOKEN_BUDGET × 0.7. This is the primary
             trigger — the agent's hot-path /compact tail budget is
             measured in transcript tokens (which include tool I/O),
             so we eagerly flush before /compact would otherwise have
             to digest-tail at hot path. Tool-heavy turns trip this
             with a small shared-msg gap; fine, the bucket is small
             but covers a lot of expansion.
          2. Shared-msg gap: shared_seq - pyramid_seq >= L1_TRIGGER +
             TAIL_RESERVE. Legacy gate — useful for low-tool sessions
             where transcript chars accumulate slowly, but a full L1
             worth of conversational content has built up.

        No-op if:
          - resolver not injected (no summarizer);
          - a job for this cid is already in flight;
          - neither trigger condition met.
        """
        if not cid or not user_id:
            return

        def _log_once(state: str):
            """Log state transitions once per cid — silent otherwise."""
            prev = self._last_trigger_log.get(cid)
            if prev == state:
                return
            self._last_trigger_log[cid] = state
            logger.info("[bg-bucket] maybe_trigger cid=%s: %s",
                         cid[:8], state)

        if self._summarizer_resolver is None:
            _log_once("resolver=None (AgentLoopTask.initialize not run?)")
            return

        with self._seq_cache_lock:
            shared_seq = self._shared_seq_cache.get(cid)
            pyramid_seq = self._pyramid_seq_cache.get(cid)
            unbucketed_rows = self._shared_unbucketed_rows_cache.get(cid)
            shared_chars = self._shared_unbucketed_chars_cache.get(cid)
            transcript_chars = self._transcript_chars_post_pyramid_cache.get(cid)

        # Cold path: seed all caches from disk on first access. This method
        # runs from ConversationStore while the conv lock is held, so the
        # seed must be asynchronous; otherwise background maintenance can
        # stall a foreground append after restart or cache invalidation.
        if (shared_seq is None or pyramid_seq is None
                or unbucketed_rows is None or shared_chars is None
                or transcript_chars is None):
            self._seed_seq_caches_async(cid, user_id)
            _log_once("seq cache cold — seeding asynchronously")
            return

        cfg = self._bg_compact_config(cid, user_id)
        seq_gap = shared_seq - pyramid_seq
        chars_per_token = float(cfg["chars_per_token"])
        transcript_tokens_est = int(transcript_chars / chars_per_token)
        shared_tokens_est = int(shared_chars / chars_per_token)
        min_input_tokens = int(
            cfg["bucket_target_tokens"] * cfg["min_input_multiplier"])
        min_input_chars = int(min_input_tokens * chars_per_token)

        # Need at least a few unbucketable shared rows for _pick_chunk
        # to return non-[] (tail_reserve_msgs + partial_min_msgs).
        buildable_threshold = (
            int(cfg["tail_reserve_msgs"]) + int(cfg["partial_min_msgs"]))

        if unbucketed_rows < buildable_threshold:
            _log_once(
                f"only {unbucketed_rows} unbucketed shared rows < "
                f"{buildable_threshold} needed — _pick_chunk would "
                f"return []; waiting for shared catch-up "
                f"(transcript_tokens_est={transcript_tokens_est})")
            return

        if shared_chars < min_input_chars:
            _log_once(
                f"only ~{shared_tokens_est} shared tokens < {min_input_tokens} "
                f"minimum ({cfg['min_input_multiplier']}x target); "
                f"waiting for useful bg bucket input "
                f"(shared_chars={shared_chars}, transcript_tokens_est="
                f"{transcript_tokens_est})")
            return

        # Trigger A: transcript token budget. When the gap-since-pyramid
        # in transcript tokens passes the configured fraction of the tail
        # token budget, fire so the next agent /compact finds the tail
        # already small.
        token_threshold = int(
            cfg["tail_token_budget"] * cfg["token_trigger_fraction"])
        token_trigger = transcript_tokens_est >= token_threshold

        # Trigger B: shared-msg gap (legacy). For low-tool sessions
        # where transcript accumulates slowly, fire when an L1 worth
        # of conversational content sits unbucketed past the tail
        # reserve. With small TAIL_RESERVE this still requires
        # ~L1_TRIGGER_MSGS shared msgs of conversation.
        msg_threshold = int(cfg["l1_trigger_msgs"] + cfg["tail_reserve_msgs"])
        msg_trigger = seq_gap >= msg_threshold

        if not (token_trigger or msg_trigger):
            _log_once(
                f"below both triggers: seq_gap={seq_gap}<{msg_threshold} "
                f"AND transcript_tokens_est={transcript_tokens_est}<"
                f"{token_threshold}")
            return

        with self._pending_lock:
            if cid in self._pending:
                _log_once("job already in flight, skipping")
                return
            self._pending.add(cid)

        try:
            self._executor.submit(self._run_job, cid, user_id)
            _trigger_kind = ("token" if token_trigger else "") + (
                ("+msg" if msg_trigger and token_trigger
                 else "msg" if msg_trigger else ""))
            _log_once(
                f"submitted job ({_trigger_kind}): shared_seq={shared_seq} "
                f"pyramid_seq={pyramid_seq} seq_gap={seq_gap} "
                f"unbucketed_rows={unbucketed_rows} "
                f"shared_tokens_est={shared_tokens_est} "
                f"transcript_tokens_est={transcript_tokens_est}")
        except RuntimeError as _e:
            # Executor has been shutdown (e.g. test teardown)
            with self._pending_lock:
                self._pending.discard(cid)
            _log_once(f"submit failed: {_e}")

    def flush(self, timeout: float = 60.0) -> None:
        """Block until every in-flight job for this process completes.

        Useful in tests to ensure async work settled before inspection.
        Manual /compact does not call this; the same hot path as provider
        compact reads the already-built pyramid and a bounded raw tail.
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
        the final bucket may be smaller than the configured L1 trigger.
        Gap msgs "in progress" are flushed into a partial bucket rather
        than left in the agent's tail. The configured partial-min floor
        avoids paying for a tiny LLM call.

        Used by explicit rebuild/maintenance flows that choose to block until
        the shared pyramid catches up. Manual /compact must not call this; it
        uses the provider-trigger path and remains bounded by the current
        pyramid plus raw tail.

        Bulk catchup shortcut: when the pyramid is empty AND the gap
        exceeds l1_trigger_msgs * bulk_catchup_multiplier, the first
        chunk absorbs everything except the configured tail reserve in
        a single bucket. Internal chunking in the summarize pipeline
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

        client, ctx_max, _svc_id = self._resolve_summarizer(user_id, cid)
        if not client:
            logger.warning(
                "[bg-bucket] build_now_sync: summarizer unavailable for "
                "user=%s cid=%s", user_id, cid[:8])
            return {"buckets_built": 0, "rollups_fired": 0,
                     "final_object_count": store.object_count,
                     "final_last_seq": store.last_seq}

        cfg = self._bg_compact_config(cid, user_id)

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
                    allow_partial=allow_partial, cfg=cfg)
                if not chunk:
                    break

                if not self._build_one_bucket(
                        cid, user_id, store, chunk, client, ctx_max,
                        cfg=cfg):
                    break
                buckets_built += 1
                self._publish_progress(cid, "bucket_building", {
                    "buckets_built": buckets_built,
                    "object_count": store.object_count,
                    "bucket_msg_count": len(chunk),
                })

                _fired = self._rollup_until_stable(
                    store, client, user_id, ctx_max, cid, cfg=cfg)
                for _ in range(_fired):
                    rollups_fired += 1
                    self._publish_progress(cid, "rollup_merging", {
                        "rollups_fired": rollups_fired,
                        "object_count": store.object_count,
                    })

            _fired = self._rollup_until_stable(
                store, client, user_id, ctx_max, cid, cfg=cfg)
            for _ in range(_fired):
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

    def _rollup_until_stable(self, store: BucketStore, client: Any,
                             user_id: str, ctx_max: int, cid: str,
                             cfg: Optional[Dict[str, Any]] = None) -> int:
        """Run rollup/collapse until the pyramid no longer changes.

        A conversation can already be over the header budget when no new
        shared chunk is bucketable. In that state the old code never called
        _maybe_rollup(), so /compact assembled an oversized header forever.
        """
        fired = 0
        while self._maybe_rollup(store, client, user_id, ctx_max, cid,
                                 cfg=cfg):
            fired += 1
        return fired

    # ── Internals ─────────────────────────────────────────────────

