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
    ROLLUP_TRIGGER_COUNT, TAIL_RESERVE, TAIL_TOKEN_BUDGET, BucketStore,
)
from core.tool_activity_digest import (
    extract_tool_activity, format_activity_digest, is_empty, merge_traces,
)

logger = logging.getLogger(__name__)


_BG_COMPACT_DEFAULTS: Dict[str, Any] = {
    "l1_trigger_msgs": L1_TRIGGER_MSGS,
    "bucket_target_tokens": BUCKET_OUTPUT_TARGET,
    "header_budget_tokens": HEADER_BUDGET,
    "rollup_trigger_count": ROLLUP_TRIGGER_COUNT,
    "tail_reserve_msgs": TAIL_RESERVE,
    "tail_token_budget": TAIL_TOKEN_BUDGET,
    "token_trigger_fraction": 0.7,
    "bulk_catchup_multiplier": 5,
    "partial_min_msgs": 5,
    "min_input_multiplier": 4,
    "chars_per_token": 3.5,
    "overshoot_warn_multiplier": 1.5,
    "header_char_multiplier": 3.0,
}

_BG_COMPACT_PARAM_PREFIX = "pawflow.bg_compact."


def _build_embed_fn(client: Any):
    """Return a callable that embeds text using `client`'s credentials when
    available, falling back to the local sentence-transformer otherwise.
    `EmbeddingProvider.embed(provider="auto", ...)` does that selection.
    Returns None if `client` is missing so the caller can keep embed_fn=None.
    """
    api_key = getattr(client, "api_key", "") or ""
    base_url = getattr(client, "base_url", "") or ""

    def _embed(text: str):
        from core.embeddings import EmbeddingProvider
        vecs = EmbeddingProvider.instance().embed(
            [text], provider="auto", api_key=api_key, base_url=base_url,
        )
        return vecs[0] if vecs else []

    return _embed


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

    def _resolve_bg_compact_param(self, short_name: str, default: Any,
                                  cid: str, user_id: str) -> Any:
        full_name = _BG_COMPACT_PARAM_PREFIX + short_name
        template = "${" + full_name + "}"
        try:
            from core.expression import resolve_expression
            value = resolve_expression(
                template, owner=user_id or None,
                conversation_id=cid or None)
        except Exception:
            logger.debug("[bg-bucket] failed to resolve %s", full_name,
                         exc_info=True)
            return default
        if value == template:
            return default
        return value

    def _bg_compact_config(self, cid: str = "", user_id: str = "") -> Dict[str, Any]:
        raw = {
            name: self._resolve_bg_compact_param(name, default, cid, user_id)
            for name, default in _BG_COMPACT_DEFAULTS.items()
        }
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

    def maybe_trigger(self, cid: str, user_id: str) -> None:
        """Fast O(1) check + async enqueue. Called from
        ConversationStore._append_shared_ctx and _append_ctx_file after
        each write, WHILE THE CONV LOCK IS HELD.

        CRITICAL: this method must NEVER touch disk. The caller holds
        ConversationStore._get_conv_lock(cid) and any file I/O here
        would stall every other write on that conv. All decisions use
        in-memory caches fed by note_* hooks.

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
        the final bucket may be smaller than the configured L1 trigger.
        Gap msgs "in progress" are flushed into a partial bucket rather
        than left in the agent's tail. The configured partial-min floor
        avoids paying for a tiny LLM call.

        Used by manual /compact on a conv whose pyramid is behind (or
        empty: import, long-idle). Blocks the caller. Emits
        compact_progress SSE per bucket built and per rollup fired so
        the UI can show progression.

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

        client, ctx_max, _svc_id = self._summarizer_resolver(user_id)
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

                if self._maybe_rollup(store, client, user_id, ctx_max, cid,
                                      cfg=cfg):
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
        transcript_path = conv_dir / "transcript.jsonl"
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
        return BgBucketBuilder._sum_chars_in_range(
            path, after_seq + 1, 1 << 62)

    @staticmethod
    def _sum_chars_in_range(path, first_seq: int, last_seq: int) -> int:
        """Sum of payload char counts in JSONL rows with
        first_seq ≤ seq ≤ last_seq. 0 if file missing.
        """
        if not path.exists():
            return 0
        import json as _json
        total = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
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
        """Count JSONL rows with seq > after_seq. 0 if file missing."""
        if not path.exists():
            return 0
        import json as _json
        n = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if int(d.get("seq") or 0) > after_seq:
                        n += 1
        except Exception:
            logger.debug("[bg-bucket] _count_rows_since failed", exc_info=True)
        return n

    @staticmethod
    def _read_last_seq(path) -> int:
        """Read the seq of the last JSONL record in `path`, O(1) in
        file size. Returns 0 if the file is missing or empty."""
        import json as _json
        import os as _os
        if not path.exists():
            return 0
        try:
            with open(path, "rb") as f:
                f.seek(0, _os.SEEK_END)
                file_size = f.tell()
                if file_size == 0:
                    return 0
                # Walk backwards in 4 KB chunks until we have a line.
                chunk = 4096
                data = b""
                pos = file_size
                while pos > 0:
                    read_size = min(chunk, pos)
                    pos -= read_size
                    f.seek(pos)
                    data = f.read(read_size) + data
                    # Strip trailing newlines to find the real last line
                    stripped = data.rstrip(b"\n\r")
                    nl = stripped.rfind(b"\n")
                    if nl >= 0:
                        last_line = stripped[nl + 1:]
                        try:
                            row = _json.loads(last_line.decode(
                                "utf-8", errors="replace"))
                            return int(row.get("seq") or 0)
                        except (ValueError, _json.JSONDecodeError):
                            return 0
                # File had only one line (no internal newline before it)
                last_line = data.strip()
                if not last_line:
                    return 0
                row = _json.loads(last_line.decode(
                    "utf-8", errors="replace"))
                return int(row.get("seq") or 0)
        except (OSError, ValueError, _json.JSONDecodeError):
            return 0

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
        client, ctx_max, _svc_id = self._summarizer_resolver(user_id)
        if not client:
            logger.info(
                "[bg-bucket] no summarizer for user=%s cid=%s — skipping",
                user_id, cid[:8])
            return

        cfg = self._bg_compact_config(cid, user_id)

        built = 0
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
            self._maybe_rollup(store, client, user_id, ctx_max, cid,
                               cfg=cfg)

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
            _tp = _conv_dir / "transcript.jsonl"
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
                embed_fn=_build_embed_fn(client),
                conversation_id=cid)
        except Exception:
            logger.debug(
                "[bg-bucket] auto_extract_memories failed for cid=%s "
                "bucket seq %d..%d",
                cid[:8], first_seq, last_seq, exc_info=True)
        return True

    def _maybe_rollup(self, store: BucketStore, client: Any,
                       user_id: str, ctx_max: int, cid: str,
                       cfg: Optional[Dict[str, Any]] = None) -> bool:
        """Fire rollup_all_except_last when header exceeds budget OR
        object_count exceeds trigger count. Return True if a rollup or
        collapse actually fired."""
        cfg = cfg or self._bg_compact_config(cid, user_id)
        bucket_target_tokens = int(cfg["bucket_target_tokens"])
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
                    embed_fn=_build_embed_fn(client),
                    conversation_id=cid)
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
