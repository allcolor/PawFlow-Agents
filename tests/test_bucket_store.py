"""Tests for core/bucket_store.py — hierarchical compaction cache.

These tests cover the disk I/O and pre-compact decisions. LLM-driven
integration (actual summaries + rollups wired through _compact) lives
in test_compact_hierarchical.py.
"""

import json

import pytest

from core.bucket_store import (
    BUCKET_MSG_THRESHOLD,
    BUCKET_TOKEN_THRESHOLD,
    BucketStore,
    ROLLUP_K,
)


def _fresh_store(tmp_path, agent="claude"):
    return BucketStore.get(tmp_path / "conv1", agent)


def test_cold_start_has_no_buckets(tmp_path):
    s = _fresh_store(tmp_path)
    assert s.bucket_count == 0
    assert s.super_bucket_count == 0
    assert s.last_seq == 0
    assert not s.has_any()
    assert s.assemble_summary_header() == ""


def test_add_bucket_persists_and_updates_meta(tmp_path):
    s = _fresh_store(tmp_path)
    bid = s.add_bucket(first_seq=1, last_seq=500,
                        first_ts=100.0, last_ts=200.0,
                        summary="## 1. USER_INTENT\ntest work")
    assert bid == "B_00001"
    assert s.bucket_count == 1
    assert s.last_seq == 500
    # Meta and bucket file both exist on disk
    assert (s._dir / "meta.json").exists()
    assert (s._dir / "B_00001.json").exists()
    # Fresh instance reads the same state from disk
    s2 = BucketStore.get(tmp_path / "conv1", "claude")
    assert s2.bucket_count == 1
    assert s2.last_seq == 500


def test_should_create_bucket_by_msg_count(tmp_path):
    s = _fresh_store(tmp_path)
    assert not s.should_create_bucket(tail_msg_count=100, tail_token_estimate=1000)
    assert s.should_create_bucket(
        tail_msg_count=BUCKET_MSG_THRESHOLD, tail_token_estimate=1000)


def test_should_create_bucket_by_tokens(tmp_path):
    s = _fresh_store(tmp_path)
    assert s.should_create_bucket(
        tail_msg_count=10, tail_token_estimate=BUCKET_TOKEN_THRESHOLD + 1)


def test_should_rollup_threshold(tmp_path):
    s = _fresh_store(tmp_path)
    assert not s.should_rollup()
    for i in range(ROLLUP_K - 1):
        s.add_bucket(first_seq=i * 10 + 1, last_seq=(i + 1) * 10,
                      first_ts=float(i), last_ts=float(i) + 1,
                      summary=f"bucket {i}")
    assert not s.should_rollup()
    s.add_bucket(first_seq=10_000, last_seq=10_010,
                  first_ts=999.0, last_ts=1000.0, summary="last")
    assert s.should_rollup()


def test_rollup_consolidates_and_deletes_sources(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(ROLLUP_K):
        s.add_bucket(first_seq=i * 10 + 1, last_seq=(i + 1) * 10,
                      first_ts=float(i), last_ts=float(i) + 1,
                      summary=f"bucket {i}")
    assert s.bucket_count == ROLLUP_K
    sid = s.rollup("CONSOLIDATED SUMMARY")
    assert sid == "SB_00001"
    assert s.bucket_count == 0
    assert s.super_bucket_count == 1
    # Deleted bucket files no longer exist on disk
    assert not (s._dir / "B_00001.json").exists()
    assert not (s._dir / f"B_{ROLLUP_K:05d}.json").exists()
    # SB spans the full range of its sources
    with open(s._dir / "SB_00001.json", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["first_seq"] == 1
    assert doc["last_seq"] == ROLLUP_K * 10
    assert doc["covers"] == [f"B_{i+1:05d}" for i in range(ROLLUP_K)]


def test_rollup_below_k_is_noop(tmp_path):
    s = _fresh_store(tmp_path)
    s.add_bucket(first_seq=1, last_seq=10,
                  first_ts=0.0, last_ts=1.0, summary="a")
    assert s.rollup("SB text") is None
    assert s.bucket_count == 1
    assert s.super_bucket_count == 0


def test_assemble_header_orders_sb_then_buckets(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(ROLLUP_K):
        s.add_bucket(first_seq=i * 10 + 1, last_seq=(i + 1) * 10,
                      first_ts=float(i), last_ts=float(i) + 1,
                      summary=f"bucket {i}")
    s.rollup("OLDER PHASE")
    # Add fresh buckets after the rollup
    s.add_bucket(first_seq=9001, last_seq=9100,
                  first_ts=1000.0, last_ts=1001.0, summary="recent bucket")
    header = s.assemble_summary_header()
    # Super-bucket appears before recent bucket
    i_sb = header.index("SB_00001")
    i_b = header.index("B_00001")  # numbering resets after rollup (new B_00001)
    assert i_sb < i_b
    # Framing labels present
    assert "Archived phase" in header
    assert "Recent phase" in header


def test_wipe_clears_everything(tmp_path):
    s = _fresh_store(tmp_path)
    s.add_bucket(first_seq=1, last_seq=10,
                  first_ts=0.0, last_ts=1.0, summary="a")
    s.add_bucket(first_seq=11, last_seq=20,
                  first_ts=1.0, last_ts=2.0, summary="b")
    assert s.bucket_count == 2
    s.wipe()
    assert s.bucket_count == 0
    assert s.last_seq == 0
    assert not (s._dir / "B_00001.json").exists()


def test_disk_is_source_of_truth(tmp_path):
    # Deleting meta.json on disk must immediately reflect in new
    # BucketStore instances — no in-memory caching.
    a1 = BucketStore.get(tmp_path / "conv1", "claude")
    a1.add_bucket(first_seq=1, last_seq=500, first_ts=1.0, last_ts=2.0,
                   summary="x" * 100, model="m")
    assert a1.last_seq == 500
    # User wipes buckets on disk
    import shutil
    shutil.rmtree(a1._dir)
    # Fresh instance reads from disk — sees empty
    a2 = BucketStore.get(tmp_path / "conv1", "claude")
    assert a2.last_seq == 0
    assert a2.bucket_count == 0


def test_bucket_numbering_persists_across_rollup(tmp_path):
    """After rollup, new regular buckets should continue numbering from 1
    (per the spec: B_NNNNN is the nth *currently existing* regular bucket).

    This is important because assemble_summary_header reads what's on
    disk in chronological order of creation. The SB carries the global
    seq range so nothing is lost.
    """
    s = _fresh_store(tmp_path)
    for i in range(ROLLUP_K):
        s.add_bucket(first_seq=i * 10 + 1, last_seq=(i + 1) * 10,
                      first_ts=float(i), last_ts=float(i) + 1,
                      summary=f"bucket {i}")
    s.rollup("consolidation")
    new_bid = s.add_bucket(first_seq=10_000, last_seq=10_010,
                            first_ts=2000.0, last_ts=2001.0,
                            summary="post-rollup bucket")
    # Implementation numbers by current count + 1; after rollup count=0
    # so the next bucket is B_00001 again (scoped to what's alive).
    assert new_bid == "B_00001"
    assert s.bucket_count == 1
    assert s.super_bucket_count == 1
