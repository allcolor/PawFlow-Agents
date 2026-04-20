"""Tests for core/bucket_store.py — hierarchical compaction cache.

Cover:
- disk I/O + meta persistence
- add_bucket
- rollup_all_except_last (keeps the last object)
- collapse_all (merges every object into one)
- get_rollup_input / get_collapse_input
- assemble_summary_header ordering
- wipe + disk source-of-truth
"""

import json

import pytest

from core.bucket_store import BucketStore


def _fresh_store(tmp_path, agent="claude"):
    return BucketStore.get(tmp_path / "conv1", agent)


def test_cold_start_has_no_objects(tmp_path):
    s = _fresh_store(tmp_path)
    assert s.object_count == 0
    assert s.last_seq == 0
    assert not s.has_any()
    assert s.assemble_summary_header() == ""


def test_add_bucket_persists_and_updates_meta(tmp_path):
    s = _fresh_store(tmp_path)
    bid = s.add_bucket(first_seq=1, last_seq=500,
                        first_ts=100.0, last_ts=200.0,
                        summary="## 1. USER_INTENT\ntest work")
    assert bid == "B_00001"
    assert s.object_count == 1
    assert s.last_seq == 500
    assert (s._dir / "meta.json").exists()
    assert (s._dir / "B_00001.json").exists()
    # Fresh instance reads state from disk
    s2 = BucketStore.get(tmp_path / "conv1", "claude")
    assert s2.object_count == 1
    assert s2.last_seq == 500


def test_rollup_all_except_last_consolidates(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(5):
        s.add_bucket(i * 10 + 1, (i + 1) * 10,
                      float(i), float(i) + 1, summary=f"bucket {i}")
    assert s.object_count == 5
    sid = s.rollup_all_except_last("CONSOLIDATED SUMMARY")
    assert sid == "SB_00001"
    # 2 objects left: the new SB + the last B
    assert s.object_count == 2
    docs = s.get_all_summaries()
    assert docs[0]["bucket_id"] == "SB_00001"
    assert docs[0]["level"] == 2
    assert docs[1]["bucket_id"] == "B_00005"
    assert docs[1]["level"] == 1
    # Consolidated B files have been deleted on disk
    for i in range(1, 5):
        assert not (s._dir / f"B_{i:05d}.json").exists()
    # SB carries the span of its sources
    sb_doc = json.loads((s._dir / "SB_00001.json").read_text())
    assert sb_doc["first_seq"] == 1
    assert sb_doc["last_seq"] == 40
    assert sb_doc["covers"] == [f"B_{i+1:05d}" for i in range(4)]


def test_rollup_below_three_objects_is_noop(tmp_path):
    s = _fresh_store(tmp_path)
    s.add_bucket(1, 10, 0.0, 1.0, summary="a")
    s.add_bucket(11, 20, 1.0, 2.0, summary="b")
    # Only 2 objects — rollup_all_except_last would leave nothing to consolidate
    assert s.rollup_all_except_last("SB text") is None
    assert s.object_count == 2


def test_cascaded_rollup_increments_level(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(5):
        s.add_bucket(i * 10 + 1, (i + 1) * 10,
                      float(i), float(i) + 1, summary=f"bucket {i}")
    s.rollup_all_except_last("SB level 2")
    for i in range(3):
        s.add_bucket(100 + i * 10, 100 + (i + 1) * 10,
                      10.0 + i, 11.0 + i, summary=f"more {i}")
    # Now objects = [SB_00001(level=2), B_00005, B_00006, B_00007, B_00008]
    assert s.object_count == 5
    sid2 = s.rollup_all_except_last("SB level 3")
    assert sid2 == "SB_00002"
    docs = s.get_all_summaries()
    # Consolidated 4 (SB + 3 B) → new level = max(2, 1, 1, 1) + 1 = 3
    assert docs[0]["bucket_id"] == "SB_00002"
    assert docs[0]["level"] == 3
    assert docs[1]["bucket_id"] == "B_00008"


def test_collapse_all_merges_every_object(tmp_path):
    """collapse_all replaces the WHOLE object list with one new SB."""
    s = _fresh_store(tmp_path)
    s.add_bucket(1, 10, 0.0, 1.0, summary="a")
    s.add_bucket(11, 20, 1.0, 2.0, summary="b")
    sid = s.collapse_all("MERGED")
    assert sid == "SB_00001"
    assert s.object_count == 1
    docs = s.get_all_summaries()
    assert docs[0]["bucket_id"] == "SB_00001"
    assert docs[0]["level"] == 2
    assert docs[0]["first_seq"] == 1
    assert docs[0]["last_seq"] == 20


def test_collapse_all_below_two_is_noop(tmp_path):
    s = _fresh_store(tmp_path)
    s.add_bucket(1, 10, 0.0, 1.0, summary="only")
    assert s.collapse_all("x") is None
    assert s.object_count == 1


def test_numbering_is_monotonic_across_rollup(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(5):
        s.add_bucket(i * 10 + 1, (i + 1) * 10,
                      float(i), float(i) + 1, summary=f"bucket {i}")
    s.rollup_all_except_last("SB")
    new_bid = s.add_bucket(200, 210, 100.0, 101.0, summary="post-rollup")
    # Counter did NOT reset — new bucket is B_00006 after B_00005 was kept
    assert new_bid == "B_00006"
    assert s.object_count == 3


def test_assemble_header_orders_chronologically(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(4):
        s.add_bucket(i * 10 + 1, (i + 1) * 10,
                      float(i), float(i) + 1, summary=f"bucket {i}")
    s.rollup_all_except_last("OLDER PHASE")
    s.add_bucket(9001, 9100, 1000.0, 1001.0, summary="recent bucket")
    header = s.assemble_summary_header()
    i_sb = header.index("SB_00001")
    i_b_recent = header.index("B_00005")
    assert i_sb < i_b_recent
    assert "Archived phase" in header
    assert "Recent phase" in header
    assert "level=2" in header
    assert "level=1" in header


def test_wipe_clears_everything(tmp_path):
    s = _fresh_store(tmp_path)
    s.add_bucket(1, 10, 0.0, 1.0, summary="a")
    s.add_bucket(11, 20, 1.0, 2.0, summary="b")
    assert s.object_count == 2
    s.wipe()
    assert s.object_count == 0
    assert s.last_seq == 0
    assert not (s._dir / "B_00001.json").exists()
    # Counters reset after wipe (clean slate)
    assert s._meta["_next_b_num"] == 1
    assert s._meta["_next_sb_num"] == 1


def test_disk_is_source_of_truth(tmp_path):
    a1 = BucketStore.get(tmp_path / "conv1", "claude")
    a1.add_bucket(1, 500, 1.0, 2.0, summary="x" * 100)
    assert a1.last_seq == 500
    import shutil
    shutil.rmtree(a1._dir)
    a2 = BucketStore.get(tmp_path / "conv1", "claude")
    assert a2.last_seq == 0
    assert a2.object_count == 0


def test_get_rollup_input_excludes_last(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(4):
        s.add_bucket(i * 10 + 1, (i + 1) * 10,
                      float(i), float(i) + 1, summary=f"bucket {i}")
    inputs = s.get_rollup_input()
    assert len(inputs) == 3
    assert [d["bucket_id"] for d in inputs] == ["B_00001", "B_00002", "B_00003"]
    # Below 3 objects → []
    s2 = _fresh_store(tmp_path, agent="other")
    s2.add_bucket(1, 10, 0.0, 1.0, summary="only")
    s2.add_bucket(11, 20, 1.0, 2.0, summary="two")
    assert s2.get_rollup_input() == []


def test_get_collapse_input_returns_every_object(tmp_path):
    s = _fresh_store(tmp_path)
    for i in range(3):
        s.add_bucket(i * 10 + 1, (i + 1) * 10,
                      float(i), float(i) + 1, summary=f"b {i}")
    inputs = s.get_collapse_input()
    assert [d["bucket_id"] for d in inputs] == ["B_00001", "B_00002", "B_00003"]
    # Below 2 → []
    s2 = _fresh_store(tmp_path, agent="other")
    s2.add_bucket(1, 10, 0.0, 1.0, summary="only")
    assert s2.get_collapse_input() == []
