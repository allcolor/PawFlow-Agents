"""Tests for segmented JSONL storage compatibility."""

import json
import time
import uuid

from core.conversation_store import ConversationStore
from core.segmented_jsonl import DEFAULT_MAX_ROWS, SegmentedJsonl


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


def test_segmented_jsonl_reads_legacy_and_rewrites_to_segments(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps({"seq": 1, "content": "one"}) + "\n"
        + json.dumps({"seq": 2, "content": "two"}) + "\n",
        encoding="utf-8",
    )
    log = SegmentedJsonl(path, max_rows=2)

    assert [row["seq"] for row in log.iter_rows()] == [1, 2]

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
    assert store.append_display_trace(
        cid, trace_id, {"kind": "step", "label": "run"}, "trace text")
    traces = [m for m in store.load(cid) if m.get("trace_id") == trace_id]
    assert traces[0]["trace"][0]["label"] == "run"
    assert traces[0]["content"] == "trace text"

    assert store.delete_message(cid, msg["msg_id"])
    assert [m.get("msg_id") for m in store.load(cid)] == [traces[0]["msg_id"]]

    ConversationStore.reset()
