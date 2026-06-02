"""Tests for segmented JSONL storage."""

import json
import os
import threading
import time
import uuid
from unittest.mock import patch

from core.conversation_store import ConversationStore
from core.segmented_jsonl import DEFAULT_MAX_BYTES, DEFAULT_MAX_ROWS, SegmentedJsonl


def _msg(role="user", content="hello", source=None, **kw):
    msg = {
        "role": role,
        "content": content,
        "msg_id": uuid.uuid4().hex[:12],
        "ts": time.time(),
    }
    if source:
        msg["source"] = source
    msg.update(kw)
    return msg


def test_default_segment_size_is_five_thousand_rows():
    assert DEFAULT_MAX_ROWS == 5000


def test_default_segment_size_has_byte_cap():
    assert DEFAULT_MAX_BYTES == 8 * 1024 * 1024


def test_segmented_jsonl_ignores_flat_file_and_writes_segments(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps({"seq": 1, "content": "one"}) + "\n"
        + json.dumps({"seq": 2, "content": "two"}) + "\n",
        encoding="utf-8",
    )
    log = SegmentedJsonl(path, max_rows=2)

    assert list(log.iter_rows()) == []
    assert not log.exists()

    log.replace_dicts({"seq": i, "content": str(i)} for i in range(1, 6))

    assert not path.exists()
    assert (tmp_path / "transcript" / "index.json").exists()
    assert [row["seq"] for row in log.iter_rows()] == [1, 2, 3, 4, 5]
    assert [row["seq"] for row in log.iter_rows_reverse()] == [5, 4, 3, 2, 1]

    log.append_dicts([{"seq": 6, "content": "six"}])

    assert [row["seq"] for row in log.iter_rows()] == [1, 2, 3, 4, 5, 6]


def test_segmented_jsonl_index_write_uses_unique_temp_path(tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=2)

    log.append_dicts([{"seq": 1, "content": "one"}])

    assert (tmp_path / "transcript" / "index.json").exists()
    assert not (tmp_path / "transcript" / "index.tmp").exists()


def test_segmented_jsonl_append_does_not_rename_hot_index(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    calls = []

    original_replace = type(path).replace

    def track_replace(self, target):
        calls.append((self, target))
        return original_replace(self, target)

    monkeypatch.setattr(type(path), "replace", track_replace)

    log.append_dicts([{"seq": 1, "content": "one"}])
    log.append_dicts([{"seq": 2, "content": "two"}])

    assert calls == []
    assert [row["seq"] for row in log.iter_rows()] == [1, 2]
    assert log.total_rows() == 2


def test_segmented_jsonl_append_defers_hot_index_until_flush_due(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    writes = {"count": 0}
    original_write = log._write_index_hot

    def count_write(index):
        writes["count"] += 1
        return original_write(index)

    monkeypatch.setattr(log, "_write_index_hot", count_write)

    for i in range(5):
        log.append_dicts([{"seq": i + 1, "content": str(i)}])

    assert writes["count"] == 1
    index = json.loads((tmp_path / "transcript" / "index.json").read_text(encoding="utf-8"))
    assert index["total_rows"] == 1

    SegmentedJsonl.flush_dirty_indexes(tmp_path, force=True)

    assert writes["count"] == 1
    index = json.loads((tmp_path / "transcript" / "index.json").read_text(encoding="utf-8"))
    assert index["total_rows"] == 5
    assert [segment["rows"] for segment in index["segments"]] == [5]
    assert log.total_rows() == 5


def test_segmented_jsonl_append_does_not_flush_hot_index_on_timer(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=100)
    writes = {"count": 0}
    original_write = SegmentedJsonl._write_index_hot

    def count_write(self, index):
        writes["count"] += 1
        return original_write(self, index)

    monkeypatch.setattr(SegmentedJsonl, "_write_index_hot", count_write)
    monkeypatch.setattr("core.segmented_jsonl._INDEX_FLUSH_SECONDS", 0.0)

    log.append_dicts([{"seq": 1, "content": "one"}])
    log.append_dicts([{"seq": 2, "content": "two"}])

    assert writes["count"] == 1
    index = json.loads((tmp_path / "transcript" / "index.json").read_text(encoding="utf-8"))
    assert index["total_rows"] == 1
    assert [row["seq"] for row in log.iter_rows()] == [1, 2]


def test_segmented_jsonl_cached_append_does_not_stat_index(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=100)
    log.append_dicts([{"seq": 1, "content": "one"}])

    original_exists = type(path).exists

    def fail_for_index(self):
        if self == log.index_path:
            raise AssertionError("cached append must not stat index.json")
        return original_exists(self)

    monkeypatch.setattr(type(path), "exists", fail_for_index)

    log.append_dicts([{"seq": 2, "content": "two"}])


def test_segmented_jsonl_deferred_hot_index_load_does_not_scan_segments(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=100)
    log.replace_dicts([{"seq": 1, "content": "one"}])
    SegmentedJsonl.invalidate_index_cache(tmp_path)

    def fail_glob(_self, _pattern):
        raise AssertionError("deferred hot index load must not scan segments")

    monkeypatch.setattr(SegmentedJsonl, "_defer_hot_index_writes", lambda self: True)
    monkeypatch.setattr(type(path), "glob", fail_glob)

    warmed = SegmentedJsonl(path, max_rows=100)
    warmed.append_dicts([{"seq": 2, "content": "two"}])


def test_segmented_jsonl_rotates_when_active_segment_exceeds_byte_cap(tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=100, max_bytes=90)

    log.append_dicts([{"seq": 1, "content": "x" * 40}])
    log.append_dicts([{"seq": 2, "content": "y" * 40}])

    paths = log.iter_paths()
    assert [p.name for p in paths] == ["000000.jsonl", "000001.jsonl"]
    assert [row["seq"] for row in log.iter_rows()] == [1, 2]

    index = json.loads((tmp_path / "transcript" / "index.json").read_text(encoding="utf-8"))
    assert index["max_bytes"] == 90
    assert [segment["rows"] for segment in index["segments"]] == [1, 1]
    assert all(segment["bytes"] > 0 for segment in index["segments"])


def test_segmented_jsonl_restart_cache_warmup_refreshes_disk_index(tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    log.append_dicts([{"seq": 1, "content": "one"}])

    from core import segmented_jsonl as sj_mod
    with sj_mod._INDEX_CACHE_LOCK:
        sj_mod._INDEX_CACHE.pop(str(tmp_path / "transcript"), None)

    warmed = SegmentedJsonl(path, max_rows=10)
    warmed.append_dicts([{"seq": 2, "content": "two"}])

    assert warmed.total_rows() == 2
    SegmentedJsonl.flush_dirty_indexes(tmp_path, force=True)

    index = json.loads((tmp_path / "transcript" / "index.json").read_text(encoding="utf-8"))
    assert index["total_rows"] == 2
    assert [segment["rows"] for segment in index["segments"]] == [2]
    assert [row["seq"] for row in warmed.iter_rows()] == [1, 2]


def test_segmented_jsonl_trusts_disk_index_without_recounting(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    log.append_dicts([{"seq": 1, "content": "one"}])
    SegmentedJsonl.close_all_append_handles()

    segment = tmp_path / "transcript" / "000000.jsonl"
    now = time.time()
    os.utime(segment, (now + 10, now + 10))

    from core import segmented_jsonl as sj_mod
    with sj_mod._INDEX_CACHE_LOCK:
        sj_mod._INDEX_CACHE.pop(str(tmp_path / "transcript"), None)

    def fail_glob(_self, _pattern):
        raise AssertionError("index load must not scan segments")

    monkeypatch.setattr(type(path), "glob", fail_glob)

    warmed = SegmentedJsonl(path, max_rows=10)
    warmed.append_dicts([{"seq": 2, "content": "two"}])


def test_segmented_jsonl_append_reuses_hot_segment_handle(tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    real_open = open
    segment_opens = {"count": 0}

    def counting_open(file, *args, **kwargs):
        if str(file).endswith("000000.jsonl") and args and args[0] in ("a", "ab"):
            segment_opens["count"] += 1
        return real_open(file, *args, **kwargs)

    with patch("core.segmented_jsonl.open", counting_open):
        for i in range(5):
            log.append_dicts([{"seq": i + 1, "content": str(i)}])
            assert [row["seq"] for row in log.iter_rows()] == list(range(1, i + 2))

    assert segment_opens["count"] == 1


def test_segmented_jsonl_hot_append_handles_are_bounded(monkeypatch, tmp_path):
    from core import segmented_jsonl as sj_mod

    SegmentedJsonl.close_all_append_handles()
    monkeypatch.setattr(sj_mod, "_APPEND_HANDLE_MAX", 2)
    paths = [tmp_path / f"c{i}" / "transcript.jsonl" for i in range(4)]

    for i, path in enumerate(paths):
        SegmentedJsonl(path, max_rows=10).append_dicts([{"seq": i}])

    with sj_mod._APPEND_HANDLES_LOCK:
        assert len(sj_mod._APPEND_HANDLES) <= 2
    SegmentedJsonl.close_all_append_handles()


def test_segmented_jsonl_auto_flushes_when_append_buffer_fills(monkeypatch, tmp_path):
    from core import segmented_jsonl as sj_mod

    SegmentedJsonl.close_all_append_handles()
    monkeypatch.setattr(sj_mod, "_APPEND_BUFFER_BYTES", 64)
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)

    log.append_dicts([{"seq": 1, "content": "a"}])
    segment = tmp_path / "transcript" / "000000.jsonl"
    assert segment.read_text(encoding="utf-8") == ""

    log.append_dicts([{"seq": 2, "content": "b" * 80}])
    raw = segment.read_text(encoding="utf-8")
    assert '"seq": 1' in raw
    assert '"seq": 2' in raw
    SegmentedJsonl.close_all_append_handles()


def test_segmented_jsonl_flush_append_handles_is_scoped(tmp_path):
    from core import segmented_jsonl as sj_mod

    class _Handle:
        closed = False

        def __init__(self):
            self.flushes = 0

        def flush(self):
            self.flushes += 1

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    handle_a = _Handle()
    handle_b = _Handle()

    with sj_mod._APPEND_HANDLES_LOCK:
        sj_mod._APPEND_HANDLES.clear()
        sj_mod._APPEND_HANDLES[str(root_a / "transcript" / "000000.jsonl")] = {
            "fh": handle_a,
            "lock": threading.RLock(),
            "last_used": 0.0,
        }
        sj_mod._APPEND_HANDLES[str(root_b / "transcript" / "000000.jsonl")] = {
            "fh": handle_b,
            "lock": threading.RLock(),
            "last_used": 0.0,
        }

    try:
        SegmentedJsonl.flush_append_handles(root_a)

        assert handle_a.flushes == 1
        assert handle_b.flushes == 0
    finally:
        with sj_mod._APPEND_HANDLES_LOCK:
            sj_mod._APPEND_HANDLES.clear()


def test_segmented_jsonl_index_cache_is_bounded(monkeypatch, tmp_path):
    from core import segmented_jsonl as sj_mod

    with sj_mod._INDEX_CACHE_LOCK:
        sj_mod._INDEX_CACHE.clear()
    monkeypatch.setattr(sj_mod, "_INDEX_CACHE_MAX", 2)

    for i in range(4):
        path = tmp_path / f"c{i}" / "transcript.jsonl"
        SegmentedJsonl(path, max_rows=10).append_dicts([{"seq": i}])

    with sj_mod._INDEX_CACHE_LOCK:
        assert len(sj_mod._INDEX_CACHE) <= 2


def test_segmented_jsonl_replace_opens_each_segment_once(tmp_path):
    path = tmp_path / "context.jsonl"
    log = SegmentedJsonl(path, max_rows=2)
    real_open = open
    segment_opens = {"count": 0}

    def counting_open(file, *args, **kwargs):
        if str(file).endswith(".jsonl") and args and args[0] in ("a", "w"):
            segment_opens["count"] += 1
        return real_open(file, *args, **kwargs)

    with patch("core.segmented_jsonl.open", counting_open):
        log.replace_dicts({"seq": i, "content": str(i)} for i in range(1, 6))

    assert segment_opens["count"] == 3
    assert [row["seq"] for row in log.iter_rows()] == [1, 2, 3, 4, 5]


def test_segmented_jsonl_patch_first_by_msg_id_rewrites_only_matching_segment(tmp_path):
    path = tmp_path / "context.jsonl"
    log = SegmentedJsonl(path, max_rows=2)
    log.append_dicts([
        {"msg_id": "m1", "content": "one"},
        {"msg_id": "m2", "content": "two"},
        {"msg_id": "m3", "content": "three"},
    ])
    SegmentedJsonl.flush_append_handles(tmp_path / "context")
    SegmentedJsonl.flush_dirty_indexes(tmp_path, force=True)
    first_segment = tmp_path / "context" / "000000.jsonl"
    first_mtime = first_segment.stat().st_mtime_ns

    patched = log.patch_first_by_msg_id("m3", {"content": "patched"})

    assert patched["content"] == "patched"
    assert first_segment.stat().st_mtime_ns == first_mtime
    assert [row["content"] for row in log.iter_rows()] == ["one", "two", "patched"]


def test_segmented_jsonl_total_rows_rebuilds_from_segments_when_index_missing(tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    log.append_dicts([{"seq": 1, "content": "one"}])
    index_path = tmp_path / "transcript" / "index.json"

    with open(tmp_path / "transcript" / "000000.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"seq": 2, "content": "two"}) + "\n")
    index_path.unlink()

    from core import segmented_jsonl as sj_mod
    with sj_mod._INDEX_CACHE_LOCK:
        sj_mod._INDEX_CACHE.pop(str(tmp_path / "transcript"), None)

    assert SegmentedJsonl(path, max_rows=10).total_rows() == 2


def test_segmented_jsonl_append_does_not_repair_stale_disk_index(monkeypatch, tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=2)
    log.append_dicts({"seq": i, "content": str(i)} for i in range(1, 5))
    index_path = tmp_path / "transcript" / "index.json"
    stale = json.loads(index_path.read_text(encoding="utf-8"))
    stale["segments"][0]["rows"] = 2
    stale["segments"][1]["rows"] = 1
    stale["segments"][1]["bytes"] = 1
    stale["total_rows"] = 3
    index_path.write_text(json.dumps(stale), encoding="utf-8")
    old = time.time() - 10
    os.utime(index_path, (old, old))

    from core import segmented_jsonl as sj_mod
    with sj_mod._INDEX_CACHE_LOCK:
        sj_mod._INDEX_CACHE.pop(str(tmp_path / "transcript"), None)

    def fail_glob(_self, _pattern):
        raise AssertionError("append must not scan segments to repair index")

    monkeypatch.setattr(type(path), "glob", fail_glob)

    SegmentedJsonl(path, max_rows=2).append_dicts([{"seq": 5, "content": "5"}])


def test_segmented_jsonl_rebuilds_from_segments_when_hot_index_is_corrupt(tmp_path):
    path = tmp_path / "transcript.jsonl"
    log = SegmentedJsonl(path, max_rows=10)
    log.append_dicts([{"seq": 1, "content": "one"}, {"seq": 2, "content": "two"}])

    (tmp_path / "transcript" / "index.json").write_text("{", encoding="utf-8")

    assert [row["seq"] for row in log.iter_rows()] == [1, 2]
    assert log.total_rows() == 2


def test_segmented_jsonl_index_replace_retries_transient_windows_permission(monkeypatch, tmp_path):
    src = tmp_path / "index.json.tmp"
    dst = tmp_path / "index.json"
    src.write_text("{}", encoding="utf-8")
    calls = []

    original_replace = type(src).replace

    def flaky_replace(self, target):
        calls.append((self, target))
        if len(calls) == 1:
            raise PermissionError("transient Windows file lock")
        return original_replace(self, target)

    monkeypatch.setattr("core.segmented_jsonl.os.name", "nt")
    monkeypatch.setattr("core.segmented_jsonl.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(type(src), "replace", flaky_replace)

    SegmentedJsonl._replace_path(src, dst)

    assert len(calls) == 2
    assert dst.read_text(encoding="utf-8") == "{}"


def test_conversation_store_mutates_segmented_transcript(tmp_path):
    ConversationStore.reset()
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    cid = store.generate_id()
    user_id = "testuser"
    store.save(cid, [], user_id=user_id)
    conv_dir = store._conv_dir(cid)

    assert not (conv_dir / "transcript.jsonl").exists()
    assert (conv_dir / "transcript" / "index.json").exists()

    msg = _msg(
        content="before",
        source={"type": "user", "name": user_id, "target_agent": "bot"},
    )
    store.append_message(cid, msg, agent_name="bot", user_id=user_id)

    page = store.load_page(cid, limit=10, offset=0)
    assert [m["content"] for m in page["messages"]] == ["before"]

    assert store.edit_message(cid, msg["msg_id"], "after") == 1
    assert store.load(cid)[0]["content"] == "after"

    trace_id = uuid.uuid4().hex[:12]
    assert store.create_display_trace(cid, trace_id, {"type": "agent", "name": "bot"})
    with patch.object(SegmentedJsonl, "rewrite",
                      side_effect=AssertionError("trace append must not rewrite")):
        assert store.append_display_trace(
            cid, trace_id, {"kind": "step", "label": "run"}, "trace text")
    raw = list(store._transcript_log(cid).iter_rows())
    assert [r.get("t") for r in raw if r.get("t")] == ["trace_update"]
    traces = [m for m in store.load(cid) if m.get("trace_id") == trace_id]
    assert traces[0]["trace"][0]["label"] == "run"
    assert traces[0]["content"] == "trace text"
    page_traces = [
        m for m in store.load_page(cid, limit=10)["messages"]
        if m.get("trace_id") == trace_id
    ]
    assert page_traces[0]["trace"][0]["label"] == "run"
    assert page_traces[0]["content"] == "trace text"

    assert store.delete_message(cid, msg["msg_id"])
    assert [m.get("msg_id") for m in store.load(cid)] == [traces[0]["msg_id"]]

    ConversationStore.reset()
