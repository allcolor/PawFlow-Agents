"""End-to-end scenario tests for the shared-pyramid compaction pipeline.

Covers the concrete scenarios we walked through during design:
  A. Young conv (< L1_TRIGGER): no bg bucket, no pyramid, no compact fire
  B. Auto trigger (80% ctx, force=False): async bg trigger, no hot-path build
  C. /compact on huge empty-pyramid conv: bulk mode builds SB-like bucket
  D. /compact with existing pyramid + 80 msgs gap: partial bucket flushed
  E. /compact with ≥150 msgs gap mid-conv: normal L1 chunking
  F. /compact with <37 msgs gap: partial skipped, msgs stay uncovered
  G. Rollup fires when object count exceeds threshold
  H. Memory extractor fires on each bucket build
  I. Post-compact agent_ctx = [sys + pyramid_header + tail]
  J. Pyramid header PRIVATE compression (step 2c) when too big for agent
  K. Tail PRIVATE digest (step 2b) when tail dominates cap

Uses the _FakeBuilder harness from test_bg_bucket_builder.py — wires a
mock summarize_fn + resolver so no real LLM call happens.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import pytest

from core.bucket_store import (
    BucketStore, L1_TRIGGER_MSGS, ROLLUP_TRIGGER_COUNT, HEADER_BUDGET,
    TAIL_RESERVE,
)
from tests.test_bg_bucket_builder import (
    _FakeBuilder, _fake_client, _shared_msg, _write_shared,
    _make_summarize_fn, PARTIAL_MIN,
)


@pytest.fixture
def fake_builder(tmp_path: Path):
    conv_dir = tmp_path / "conv"
    conv_dir.mkdir()
    shared_path = conv_dir / "shared.jsonl"
    transcript_path = conv_dir / "transcript.jsonl"
    b = _FakeBuilder(shared_path, transcript_path, conv_dir)
    yield b
    b._executor.shutdown(wait=True)


def _wire(builder, summarize_output: str = "ok summary with enough chars to pass the 20-char length gate"):
    fn, calls = _make_summarize_fn(summarize_output)
    builder.set_summarizer_resolver(
        lambda uid: (_fake_client(), 128000, "svc"))
    builder.set_summarize_fn(fn)
    return fn, calls


# ── A. Young conv ──────────────────────────────────────────────────


def test_young_conv_no_bucket_no_pyramid(fake_builder):
    """Few enough msgs that async build_now_sync(allow_partial=False)
    has no L1-sized chunk to flush, and the pyramid stays empty."""
    # Below L1_TRIGGER → no L1 chunk is buildable; with
    # allow_partial=False a partial flush isn't allowed either.
    n = max(0, L1_TRIGGER_MSGS - 1)
    _write_shared(fake_builder._shared_path, n)
    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    assert result["buckets_built"] == 0
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.object_count == 0
    assert store.last_seq == 0
    assert calls == []


# ── C. /compact on huge empty-pyramid conv (bulk mode) ─────────────


def test_bulk_catchup_absorbs_bulk_and_preserves_tail(fake_builder):
    """pyramid empty + gap >> L1: bulk absorbs all pre-tail, last
    TAIL_RESERVE msgs stay un-bucketed as the recent window."""
    n = L1_TRIGGER_MSGS * 7  # well past bulk_threshold (L1×5)
    _write_shared(fake_builder._shared_path, n)
    fn, calls = _wire(fake_builder)

    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    assert result["buckets_built"] == 1  # only the bulk bucket
    assert calls[0]["n_msgs"] == n - TAIL_RESERVE  # all but tail reserve

    store = BucketStore.get(fake_builder._conv_dir)
    assert store.object_count == 1
    # Last TAIL_RESERVE msgs stay uncovered — they are the tail
    assert store.last_seq == n - TAIL_RESERVE


def test_bulk_not_fired_with_preexisting_pyramid(fake_builder):
    """Mid-conv compact shouldn't bulk — pyramid already seeded.
    Pick gap so that available = 2×L1 + leftover<L1 → 2 L1 chunks, no
    bulk."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")

    # 2 L1 chunks worth + tail reserve
    n_new = 2 * L1_TRIGGER_MSGS + TAIL_RESERVE
    _write_shared(fake_builder._shared_path, n_new, start_seq=101)
    fn, calls = _wire(fake_builder)

    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    for c in calls:
        assert c["n_msgs"] == L1_TRIGGER_MSGS
    assert result["buckets_built"] == 2


# ── D. Partial flush (force=True with small gap) ────────────────────


def test_partial_flush_when_allowed_and_above_min(fake_builder):
    """available in [PARTIAL_MIN, L1) → one partial bucket; tail
    reserve preserved."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")
    _avail = max(PARTIAL_MIN, L1_TRIGGER_MSGS // 3)
    n = _avail + TAIL_RESERVE
    _write_shared(fake_builder._shared_path, n, start_seq=101)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    assert result["buckets_built"] == 1
    assert calls[0]["n_msgs"] == _avail


# ── F. Gap ≤ TAIL_RESERVE → no bucket (everything is tail) ─────────


def test_gap_below_tail_reserve_leaves_all_uncovered(fake_builder):
    """gap ≤ TAIL_RESERVE → available ≤ 0 → no bucket."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")
    # write exactly TAIL_RESERVE rows past the seed: available = 0
    _write_shared(fake_builder._shared_path, TAIL_RESERVE, start_seq=101)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    assert result["buckets_built"] == 0
    assert calls == []
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == 100  # unchanged


# ── E. Mid-conv /compact with multi-L1 gap ─────────────────────────


def test_midconv_compact_chunks_l1_sized(fake_builder):
    """Normal mid-conv /compact builds L1-sized chunks + preserves tail."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 100, 0.0, 1.0, summary="seed")
    # 2 full L1 chunks + small leftover < PARTIAL_MIN + tail reserve
    leftover = max(0, PARTIAL_MIN - 1)
    n = 2 * L1_TRIGGER_MSGS + leftover + TAIL_RESERVE
    _write_shared(fake_builder._shared_path, n, start_seq=101)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    assert result["buckets_built"] == 2
    for c in calls:
        assert c["n_msgs"] == L1_TRIGGER_MSGS


# ── G. Rollup fires when object_count exceeds threshold ─────────────


def test_rollup_fires_on_object_count_threshold(fake_builder):
    """Past ROLLUP_TRIGGER_COUNT, _maybe_rollup consolidates."""
    store = BucketStore.get(fake_builder._conv_dir)
    # Seed with ROLLUP_TRIGGER_COUNT buckets so the next L1 triggers rollup
    for i in range(ROLLUP_TRIGGER_COUNT):
        store.add_bucket(i * 10 + 1, (i + 1) * 10,
                          float(i), float(i) + 1,
                          summary=f"phase {i} " * 10)
    # Need gap ≥ L1 + TAIL_RESERVE to trigger one new L1 bucket
    _write_shared(fake_builder._shared_path,
                   L1_TRIGGER_MSGS + TAIL_RESERVE,
                   start_seq=ROLLUP_TRIGGER_COUNT * 10 + 1)

    fn, calls = _wire(fake_builder)
    result = fake_builder.build_now_sync("cid", "uid", allow_partial=False)
    # 1 new L1 added, then rollup consolidates 30 → 1 SB, keep 1 B
    # Final state: 1 SB + 1 B (the new L1) = 2 objects
    assert result["buckets_built"] == 1
    assert result["rollups_fired"] >= 1
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.object_count <= 2 + 1


# ── H. Memory extractor fires on each bucket ───────────────────────


def test_memory_extractor_called_per_bucket(fake_builder, monkeypatch):
    """auto_extract_memories should fire once per bucket build."""
    calls_to_extract = []

    def _fake_extract(user_id, summary, agent_name="", llm_client=None, **kwargs):
        calls_to_extract.append({
            "user_id": user_id,
            "summary_len": len(summary),
            "agent_name": agent_name,
        })
        return 0

    monkeypatch.setattr("core.memory_auto_extract.auto_extract_memories",
                         _fake_extract)

    # 2 L1 chunks worth + tail reserve, no leftover
    _write_shared(fake_builder._shared_path,
                   2 * L1_TRIGGER_MSGS + TAIL_RESERVE)
    fn, _ = _wire(fake_builder, summarize_output="detailed summary " * 10)
    fake_builder.build_now_sync("cid", "uid", allow_partial=False)

    # 2 L1 buckets built → 2 extract calls
    assert len(calls_to_extract) == 2
    for c in calls_to_extract:
        assert c["user_id"] == "uid"
        assert c["summary_len"] > 0


# ── trace + pyramid header integration ─────────────────────────────


def test_bucket_doc_carries_tool_trace(fake_builder):
    """Shared-scan + transcript extract populates tool_trace on the bucket."""
    # Need gap ≥ L1 + TAIL_RESERVE for a bucket to be built
    _write_shared(fake_builder._shared_path,
                   L1_TRIGGER_MSGS + TAIL_RESERVE)
    # Inject an edit tool_call on a transcript msg in-range
    with open(fake_builder._transcript_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "role": "assistant", "seq": 50, "ts": 150.0,
            "msg_id": "a50",
            "tool_calls": [
                {"id": "tc1", "name": "edit",
                 "arguments": {"path": "src/foo.py"}},
                {"id": "tc2", "name": "bash",
                 "arguments": {"command": "pytest"}},
            ],
        }) + "\n")
        f.write(json.dumps({
            "role": "tool", "seq": 51, "tool_call_id": "tc2",
            "content": "2 passed",
        }) + "\n")

    fn, _ = _wire(fake_builder)
    fake_builder.build_now_sync("cid", "uid", allow_partial=False)

    store = BucketStore.get(fake_builder._conv_dir)
    docs = store.get_all_summaries()
    assert len(docs) == 1
    trace = docs[0]["tool_trace"]
    assert trace["edits"] == {"src/foo.py": 1}
    assert len(trace["commands"]) == 1
    assert trace["commands"][0]["cmd"] == "pytest"
    assert "2 passed" in trace["commands"][0]["result"]


def test_assemble_header_includes_trace(fake_builder):
    """assemble_summary_header renders tool_trace below the narrative."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(
        1, 100, 0.0, 1.0,
        summary="## Narrative\nUser asked to fix bug X.",
        tool_trace={
            "edits": {"src/bug.py": 2},
            "creates": [], "reads": {}, "deletes": [],
            "commands": [{"cmd": "pytest", "result": "ok"}],
            "delegations": [],
        },
    )
    header = store.assemble_summary_header()
    assert "User asked to fix bug X" in header
    assert "Files edited:" in header
    assert "src/bug.py" in header
    assert "pytest" in header


# ── invariant: shared pyramid is per-conv, not per-agent ────────────


def test_pyramid_path_is_shared_not_per_agent(fake_builder):
    """summaries/_shared/ is the only pyramid path; no per-agent dir."""
    store = BucketStore.get(fake_builder._conv_dir)
    store.add_bucket(1, 10, 0.0, 1.0, summary="x")
    assert (fake_builder._conv_dir / "summaries" / "_shared").is_dir()
    # No per-agent dirs should have been created by BucketStore
    other_dirs = [
        d for d in (fake_builder._conv_dir / "summaries").iterdir()
        if d.is_dir() and d.name != "_shared"
    ]
    assert other_dirs == []


# ── invariant: bg writer is the only API surface for mutation ──────


def test_tail_reserve_invariant_after_sync_build(fake_builder):
    """Core invariant: after build_now_sync on any gap size, the last
    TAIL_RESERVE shared msgs stay un-bucketed — they form the recent
    window every post-compact output carries."""
    from core.bucket_store import TAIL_RESERVE

    # Large backlog
    n = 50_000
    _write_shared(fake_builder._shared_path, n)
    fn, _ = _wire(fake_builder)
    fake_builder.build_now_sync("cid", "uid", allow_partial=True)

    store = BucketStore.get(fake_builder._conv_dir)
    # Last TAIL_RESERVE msgs NOT in pyramid → their seqs > pyramid.last_seq
    assert store.last_seq == n - TAIL_RESERVE
    # Which means a downstream pre-filter (m.seq > last_seq) keeps
    # exactly the last TAIL_RESERVE msgs as tail.
    tail_msgs = [s for s in range(n - TAIL_RESERVE + 1, n + 1)]
    assert len(tail_msgs) == TAIL_RESERVE


def test_tail_reserve_invariant_across_iterations(fake_builder):
    """After multiple build_now_sync iterations on accumulating
    shared, the tail reserve invariant holds every time."""
    from core.bucket_store import TAIL_RESERVE

    fn, _ = _wire(fake_builder)

    # First batch: 500 msgs
    _write_shared(fake_builder._shared_path, 500)
    fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == 500 - TAIL_RESERVE  # tail reserved

    # Add 300 more. Total = 800.
    _write_shared(fake_builder._shared_path, 300, start_seq=501)
    fake_builder.build_now_sync("cid", "uid", allow_partial=True)
    store = BucketStore.get(fake_builder._conv_dir)
    assert store.last_seq == 800 - TAIL_RESERVE


def test_per_agent_context_op_lock_isolation():
    """Agent-scoped context op lock must not block other agents on the
    same conversation, and must not block any agent on OTHER convs."""
    import threading
    from tasks.ai.agent_loop import AgentLoopTask

    task = AgentLoopTask.__new__(AgentLoopTask)
    # Clear any state from prior tests
    AgentLoopTask._context_op_events.clear()

    # Conv "c1", acquire lock on agent "claude"
    assert task._acquire_context_op("c1", "claude", timeout=1.0)

    # claude on c1 is blocked
    assert not task._is_context_op_free("c1", "claude")
    # qwen on c1 is FREE — different agent
    assert task._is_context_op_free("c1", "qwen")
    # claude on c2 is FREE — different conv
    assert task._is_context_op_free("c2", "claude")
    # "" (whole-conv) on c1 is FREE (no whole-conv op in progress)
    assert task._is_context_op_free("c1", "")

    task._release_context_op("c1", "claude")
    assert task._is_context_op_free("c1", "claude")

    # Whole-conv op blocks everyone on that conv
    assert task._acquire_context_op("c1", "", timeout=1.0)
    assert not task._is_context_op_free("c1", "claude")
    assert not task._is_context_op_free("c1", "qwen")
    assert not task._is_context_op_free("c1", "")
    # But other convs are untouched
    assert task._is_context_op_free("c2", "claude")
    task._release_context_op("c1", "")


def test_maybe_trigger_is_o1_no_full_scan(fake_builder, monkeypatch):
    """maybe_trigger must not scan shared.jsonl on the hot path.
    Cache is populated at first access (using tail-read for seq) then
    subsequent calls are O(1) dict lookups."""
    # Simulate shared.jsonl with 10k msgs via the fake builder's shared_path
    import json as _json
    with open(fake_builder._shared_path, "w", encoding="utf-8") as f:
        for i in range(1, 10001):
            f.write(_json.dumps(_shared_msg(i)) + "\n")

    _wire(fake_builder)

    # Track calls to _read_last_seq — should happen AT MOST once per cid
    read_calls = []
    orig = fake_builder.__class__._read_last_seq

    def _counted(path):
        read_calls.append(path)
        return orig(path)

    monkeypatch.setattr(
        fake_builder.__class__, "_read_last_seq", staticmethod(_counted))

    # Simulate what ConversationStore._append_shared_ctx does — caller
    # hints the latest seq before invoking maybe_trigger.
    # First call: cold cache → seed (one last-line read).
    fake_builder.note_shared_seq("cid_test", 10000)
    fake_builder.maybe_trigger("cid_test", "user_test")
    # Second, third, fourth call: warm cache, zero disk I/O.
    fake_builder.note_shared_seq("cid_test", 10001)
    fake_builder.maybe_trigger("cid_test", "user_test")
    fake_builder.note_shared_seq("cid_test", 10002)
    fake_builder.maybe_trigger("cid_test", "user_test")

    # With note_shared_seq populating the cache, _read_last_seq should
    # NEVER have been called — maybe_trigger never hits the seeding
    # fallback because the cache was already populated.
    assert read_calls == []


def test_compact_never_calls_build_now_sync(monkeypatch):
    """Compact must be INSTANT — no synchronous bucket build, no
    CC subprocess spawn for summarization. Bg-builder runs async in
    the background and keeps the pyramid up to date there; the user-
    facing /compact only assembles existing pyramid + walks back the
    transcript by token budget. If build_now_sync ever fires here,
    a partial bucket build can drag a 60s LLM call into what should
    be a sub-second operation."""
    from core.llm_client import LLMMessage
    from core import bg_bucket_builder as _bb_mod
    from core import bucket_store as _bs_mod

    sync_calls = []

    class _StubBB:
        @classmethod
        def instance(cls):
            return cls()

        def build_now_sync(self, conversation_id, user_id,
                            allow_partial=True):
            sync_calls.append({
                "conversation_id": conversation_id,
                "user_id": user_id,
                "allow_partial": allow_partial,
            })
            return {"buckets_built": 0, "rollups_fired": 0,
                     "final_object_count": 0, "final_last_seq": 0}

        def maybe_trigger(self, *a, **kw):
            pass

    class _StubBS:
        @classmethod
        def get(cls, conv_dir):
            return cls()

        @property
        def last_seq(self):
            return 0  # empty pyramid → no pre-filter, no header bridge

        @property
        def object_count(self):
            return 0

        def assemble_summary_header(self):
            return ""

    monkeypatch.setattr(_bb_mod, "BgBucketBuilder", _StubBB)
    monkeypatch.setattr(_bs_mod, "BucketStore", _StubBS)

    from tasks.ai.agent_loop import AgentLoopTask
    instance = AgentLoopTask.__new__(AgentLoopTask)

    msgs = [LLMMessage(role="system", content="sys",
                        conversation_id="cid", seq=1)]
    msgs += [LLMMessage(role="user", content="hi",
                          conversation_id="cid", seq=10)]

    class _C:
        api_key = ""
        base_url = ""
    out = instance._compact(
        list(msgs), _C(),
        max_tokens=200_000,
        target_fraction=0.25,
        force=True,
        conversation_id="cid_test",
        agent_name="claude",
        user_id="uid",
    )
    assert out is not None
    # The whole point: compact MUST NOT spawn a synchronous bucket
    # build. The async bg-builder is responsible for keeping the
    # pyramid current; compact just reads it.
    assert sync_calls == [], (
        f"compact called build_now_sync ({len(sync_calls)} times) "
        f"— this defeats the 'compact is instant' guarantee. "
        f"calls={sync_calls!r}")


def test_compact_drops_previous_synthetic_context_from_tail(monkeypatch):
    """Repeated compacts rebuild header + raw tail, never compact the bridge."""
    from core.llm_client import LLMMessage
    from core import bucket_store as _bs_mod
    from core.conversation_store import ConversationStore
    from tasks.ai.agent_loop import AgentLoopTask

    class _StubCS:
        def _conv_dir(self, conversation_id):
            return Path("/tmp/pawflow-test-compact")

    class _StubBS:
        @classmethod
        def get(cls, conv_dir):
            return cls()

        def assemble_summary_header(self):
            return "[Conversation summary - earlier messages compacted]\nold work"

    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: _StubCS()))
    monkeypatch.setattr(_bs_mod, "BucketStore", _StubBS)
    monkeypatch.setattr(
        AgentLoopTask, "_persist_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        AgentLoopTask, "_cleanup_orphan_files", staticmethod(lambda *a, **kw: None))

    class _C:
        api_key = ""
        base_url = ""

    instance = AgentLoopTask.__new__(AgentLoopTask)
    msgs = [
        LLMMessage(role="system", content="sys", conversation_id="cid", seq=1),
        LLMMessage(
            role="user",
            content="OLD COMPACT BRIDGE should not survive",
            source={"type": "context"},
            conversation_id="cid",
            seq=10,
        ),
        LLMMessage(
            role="assistant",
            content="OLD COMPACT ACK should not survive",
            source={"type": "context"},
            conversation_id="cid",
            seq=11,
        ),
        LLMMessage(role="user", content="fresh raw user", conversation_id="cid", seq=20),
        LLMMessage(role="assistant", content="fresh raw assistant", conversation_id="cid", seq=21),
    ]

    out = instance._compact(
        list(msgs), _C(),
        max_tokens=200_000,
        force=True,
        conversation_id="cid_test",
        agent_name="assistant",
        user_id="uid",
        budget_config={"compact_target_tokens": 25_000},
    )

    joined = "\n".join(m.content or "" for m in out)
    assert "old work" in joined
    assert "fresh raw user" in joined
    assert "fresh raw assistant" in joined
    assert "OLD COMPACT BRIDGE" not in joined
    assert "OLD COMPACT ACK" not in joined


def test_compact_backfills_tail_budget_past_oversized_tool_result(monkeypatch):
    """Target tokens are a budget to fill, not just uncovered tail messages.

    A raw tool result can be larger than the remaining tail budget even though
    its deterministic compacted form is small. The tail selector must account
    for that post-truncation cost, otherwise it stops early and leaves the
    compacted context far below compact_target_tokens.
    """
    from core import bucket_store as _bs_mod
    from core.conversation_store import ConversationStore
    from core.llm_client import LLMMessage, LLMToolCall
    from tasks.ai.agent_loop import AgentLoopTask

    class _StubCS:
        def _conv_dir(self, conversation_id):
            return Path("/tmp/pawflow-test-compact-fill")

        def message_count(self, conversation_id):
            return 5

    class _StubBS:
        @classmethod
        def get(cls, conv_dir):
            return cls()

        def assemble_summary_header(self):
            return "H" * 72_000

    def _estimate(self, msgs, **kwargs):
        total = 0
        for m in msgs:
            content = getattr(m, "content", "") or ""
            if isinstance(content, list):
                content = " ".join(
                    str(p.get("text", "")) for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            total += len(str(content)) // 4 + 4
            total += 8 * len(getattr(m, "tool_calls", None) or [])
        return total

    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: _StubCS()))
    monkeypatch.setattr(_bs_mod, "BucketStore", _StubBS)
    monkeypatch.setattr(AgentLoopTask, "_persist_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        AgentLoopTask, "_cleanup_orphan_files", staticmethod(lambda *a, **kw: None))
    monkeypatch.setattr(AgentLoopTask, "_estimate_tokens", _estimate)

    instance = AgentLoopTask.__new__(AgentLoopTask)
    tool_call = LLMToolCall(id="tc_big", name="bash", arguments={"command": "pytest"})
    msgs = [
        LLMMessage(role="system", content="sys", conversation_id="cid", seq=1),
        LLMMessage(
            role="user",
            content="OLDER_FILL_SHOULD_BE_KEPT " + ("fill words " * 2_000),
            conversation_id="cid",
            seq=10,
        ),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[tool_call],
            conversation_id="cid",
            seq=11,
        ),
        LLMMessage(
            role="tool",
            content="large tool output line " * 2_000,
            tool_call_id="tc_big",
            conversation_id="cid",
            seq=12,
        ),
        LLMMessage(
            role="user",
            content="RECENT_TAIL_MESSAGE",
            conversation_id="cid",
            seq=13,
        ),
    ]

    class _C:
        api_key = ""
        base_url = ""

    out = instance._compact(
        list(msgs), _C(),
        max_tokens=200_000,
        force=True,
        conversation_id="cid_test",
        agent_name="assistant",
        user_id="uid",
        budget_config={"compact_target_tokens": 25_000},
    )

    joined = "\n".join(m.content or "" for m in out)
    assert "OLDER_FILL_SHOULD_BE_KEPT" in joined
    assert "RECENT_TAIL_MESSAGE" in joined
    assert "...[compacted" in joined
    assert _estimate(instance, out) <= 25_000
    assert _estimate(instance, out) > 23_000


def test_compact_fits_single_oversized_user_tail_to_budget(monkeypatch):
    """A single huge recent user message should fill, not crush, the cap.

    The tail walk-back always keeps at least the newest message. If that
    message is larger than the tail budget, compact must truncate that one
    message to the available tail budget instead of invoking global force-fit,
    which can collapse a 25k target to a much smaller context.
    """
    from core import bucket_store as _bs_mod
    from core.conversation_store import ConversationStore
    from core.llm_client import LLMMessage
    from tasks.ai.agent_loop import AgentLoopTask

    class _StubCS:
        def _conv_dir(self, conversation_id):
            return Path("/tmp/pawflow-test-compact-oversized-user")

        def message_count(self, conversation_id):
            return 426

    class _StubBS:
        @classmethod
        def get(cls, conv_dir):
            return cls()

        def assemble_summary_header(self):
            return "H" * 16_000

    def _estimate(self, msgs, **kwargs):
        total = 0
        for m in msgs:
            content = getattr(m, "content", "") or ""
            if isinstance(content, list):
                content = " ".join(
                    str(p.get("text", "")) for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            total += len(str(content)) // 4 + 4
            total += 8 * len(getattr(m, "tool_calls", None) or [])
        return total

    def _force_fit_should_not_run(*args, **kwargs):
        raise AssertionError("oversized user tail must be pre-fitted")

    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: _StubCS()))
    monkeypatch.setattr(_bs_mod, "BucketStore", _StubBS)
    monkeypatch.setattr(AgentLoopTask, "_persist_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        AgentLoopTask, "_cleanup_orphan_files", staticmethod(lambda *a, **kw: None))
    monkeypatch.setattr(AgentLoopTask, "_estimate_tokens", _estimate)
    monkeypatch.setattr(AgentLoopTask, "_force_fit_context", _force_fit_should_not_run)

    instance = AgentLoopTask.__new__(AgentLoopTask)
    msgs = [
        LLMMessage(role="system", content="sys", conversation_id="cid", seq=1),
        LLMMessage(role="assistant", content="older assistant", conversation_id="cid", seq=10),
        LLMMessage(
            role="user",
            content="BUG_COMPACT_BUDGET_START\n" + ("huge user log line\n" * 20_000)
                    + "BUG_COMPACT_BUDGET_END",
            conversation_id="cid",
            seq=426,
        ),
    ]

    class _C:
        api_key = ""
        base_url = ""

    out = instance._compact(
        list(msgs), _C(),
        max_tokens=200_000,
        force=True,
        conversation_id="cid_test",
        agent_name="assistant",
        user_id="uid",
        budget_config={"compact_target_tokens": 25_000},
    )

    joined = "\n".join(m.content or "" for m in out)
    final_tokens = _estimate(instance, out)
    assert "BUG_COMPACT_BUDGET_START" in joined
    assert "BUG_COMPACT_BUDGET_END" in joined
    assert "compacted to fit tail budget" in joined
    assert final_tokens <= 25_000
    assert final_tokens > 23_000


def test_compact_fits_oversized_boundary_user_before_recent_tail(monkeypatch):
    """A huge pre-tail user message must consume the remaining tail budget.

    Regression for compacts that kept only the final tiny assistant message
    (~50 tokens) because the previous user log was larger than the remaining
    tail budget. The boundary user message should be truncated and prepended,
    not skipped entirely.
    """
    from core import bucket_store as _bs_mod
    from core.conversation_store import ConversationStore
    from core.llm_client import LLMMessage
    from tasks.ai.agent_loop import AgentLoopTask

    class _StubCS:
        def _conv_dir(self, conversation_id):
            return Path("/tmp/pawflow-test-compact-boundary-user")

        def message_count(self, conversation_id):
            return 427

    class _StubBS:
        @classmethod
        def get(cls, conv_dir):
            return cls()

        def assemble_summary_header(self):
            return "H" * 16_000

    def _estimate(self, msgs, **kwargs):
        total = 0
        for m in msgs:
            content = getattr(m, "content", "") or ""
            if isinstance(content, list):
                content = " ".join(
                    str(p.get("text", "")) for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            total += len(str(content)) // 4 + 4
            total += 8 * len(getattr(m, "tool_calls", None) or [])
        return total

    def _force_fit_should_not_run(*args, **kwargs):
        raise AssertionError("boundary user tail must be pre-fitted")

    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: _StubCS()))
    monkeypatch.setattr(_bs_mod, "BucketStore", _StubBS)
    monkeypatch.setattr(AgentLoopTask, "_persist_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        AgentLoopTask, "_cleanup_orphan_files", staticmethod(lambda *a, **kw: None))
    monkeypatch.setattr(AgentLoopTask, "_estimate_tokens", _estimate)
    monkeypatch.setattr(AgentLoopTask, "_force_fit_context", _force_fit_should_not_run)

    instance = AgentLoopTask.__new__(AgentLoopTask)
    msgs = [
        LLMMessage(role="system", content="sys", conversation_id="cid", seq=1),
        LLMMessage(
            role="user",
            content="BOUNDARY_BUDGET_START\n" + ("large pasted log line\n" * 20_000)
                    + "BOUNDARY_BUDGET_END",
            conversation_id="cid",
            seq=425,
        ),
        LLMMessage(
            role="assistant",
            content="RECENT_AFTER_BOUNDARY",
            conversation_id="cid",
            seq=426,
        ),
    ]

    class _C:
        api_key = ""
        base_url = ""

    out = instance._compact(
        list(msgs), _C(),
        max_tokens=200_000,
        force=True,
        conversation_id="cid_test",
        agent_name="assistant",
        user_id="uid",
        budget_config={"compact_target_tokens": 25_000},
    )

    joined = "\n".join(m.content or "" for m in out)
    final_tokens = _estimate(instance, out)
    assert "BOUNDARY_BUDGET_START" in joined
    assert "BOUNDARY_BUDGET_END" in joined
    assert "RECENT_AFTER_BOUNDARY" in joined
    assert "compacted to fit tail budget" in joined
    assert final_tokens <= 25_000
    assert final_tokens > 23_000


def test_independent_compact_summarizes_head_without_bucket_store(monkeypatch):
    """Task/delegate contexts compact locally; they never read the shared pyramid."""
    from core import bucket_store as _bs_mod
    from core.llm_client import LLMMessage
    from tasks.ai.agent_loop import AgentLoopTask

    def _boom_get(_conv_dir):
        raise AssertionError("independent compact must not load BucketStore")

    persisted = {}
    monkeypatch.setattr(_bs_mod.BucketStore, "get", staticmethod(_boom_get))
    monkeypatch.setattr(AgentLoopTask, "_persist_context",
                        lambda self, msgs, cid, agent: persisted.update(
                            {"cid": cid, "agent": agent, "messages": msgs}))
    monkeypatch.setattr(AgentLoopTask, "_cleanup_orphan_files",
                        staticmethod(lambda *a, **kw: None))
    monkeypatch.setattr(
        AgentLoopTask, "_estimate_tokens",
        lambda self, msgs, **kw: sum(len(str(getattr(m, "content", ""))) // 4 + 4 for m in msgs),
    )
    monkeypatch.setattr(
        AgentLoopTask, "_summarize_messages",
        lambda self, msgs, *a, **kw: "summary of " + ",".join(
            str(getattr(m, "content", ""))[:16] for m in msgs),
    )

    instance = AgentLoopTask.__new__(AgentLoopTask)
    msgs = [
        LLMMessage(role="system", content="sys", conversation_id="parent::task::t1"),
        LLMMessage(role="user", content="old task detail " * 2000,
                   conversation_id="parent::task::t1", seq=10),
        LLMMessage(role="assistant", content="old result " * 1000,
                   conversation_id="parent::task::t1", seq=11),
        LLMMessage(role="user", content="recent instruction",
                   conversation_id="parent::task::t1", seq=20),
    ]

    class _C:
        api_key = ""
        base_url = ""

    out = instance._compact(
        list(msgs), _C(), max_tokens=20_000, force=True,
        conversation_id="parent::task::t1", agent_name="worker",
        user_id="uid", independent_context=True,
    )

    joined = "\n".join(m.content or "" for m in out)
    assert "[Independent context summary - earlier messages compacted]" in joined
    assert "old task detail" in joined
    assert "recent instruction" in joined
    assert persisted["cid"] == "parent::task::t1"
    assert persisted["agent"] == "worker"


def test_independent_compact_folds_previous_summary(monkeypatch):
    """Repeated independent compacts replace the old summary instead of stacking it."""
    from core.llm_client import LLMMessage
    from tasks.ai.agent_loop import AgentLoopTask

    monkeypatch.setattr(AgentLoopTask, "_persist_context", lambda *a, **kw: None)
    monkeypatch.setattr(AgentLoopTask, "_cleanup_orphan_files",
                        staticmethod(lambda *a, **kw: None))
    monkeypatch.setattr(
        AgentLoopTask, "_estimate_tokens",
        lambda self, msgs, **kw: sum(len(str(getattr(m, "content", ""))) // 4 + 4 for m in msgs),
    )

    summarized_inputs = []

    def _summarize(self, msgs, *a, **kw):
        text = "\n".join(str(getattr(m, "content", "")) for m in msgs)
        summarized_inputs.append(text)
        return "merged summary: OLD SUMMARY + new old work"

    monkeypatch.setattr(AgentLoopTask, "_summarize_messages", _summarize)

    instance = AgentLoopTask.__new__(AgentLoopTask)
    msgs = [
        LLMMessage(role="system", content="sys", conversation_id="p::task::t2"),
        LLMMessage(
            role="user",
            content="[Independent context summary - earlier messages compacted]\n\nOLD SUMMARY",
            source={"type": "independent_compaction"},
            conversation_id="p::task::t2",
            seq=2,
        ),
        LLMMessage(role="user", content="new old work " * 2000,
                   conversation_id="p::task::t2", seq=3),
        LLMMessage(role="assistant", content="recent answer",
                   conversation_id="p::task::t2", seq=30),
    ]

    class _C:
        api_key = ""
        base_url = ""

    out = instance._compact(
        list(msgs), _C(), max_tokens=20_000, force=True,
        conversation_id="p::task::t2", agent_name="worker",
        user_id="uid", independent_context=True,
    )

    joined = "\n".join(m.content or "" for m in out)
    assert joined.count("[Independent context summary - earlier messages compacted]") == 1
    assert "OLD SUMMARY" in summarized_inputs[0]
    assert "recent answer" in joined
