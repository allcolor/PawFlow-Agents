"""Delegate rows are memoised per transcript segment."""

import pytest

from core import _conversation_store_delegate_rows as delegate_rows
from core._conversation_store_delegate_rows import DelegateRowMemo, is_delegate_row
from core.segmented_jsonl import SegmentedJsonl


def _delegate(task_id, ts):
    return {"role": "user", "content": f"task {task_id}", "timestamp": ts,
            "source": {"type": "agent_delegate", "from": "caller",
                       "to": "worker", "task_id": task_id}}


def _chat(ts):
    return {"role": "assistant", "content": "chat", "timestamp": ts,
            "source": {"type": "agent"}}


class _Codec:
    def __init__(self):
        self.decoded = []

    def decode(self, row):
        self.decoded.append(row.get("content"))
        return dict(row)


@pytest.fixture
def reads(monkeypatch):
    names = []
    real = delegate_rows._iter_offsets

    def counting(path):
        names.append(path.rsplit("/", 1)[-1])
        return real(path)

    monkeypatch.setattr(delegate_rows, "_iter_offsets", counting)
    return names


def _log(tmp_path):
    return SegmentedJsonl(tmp_path / "transcript.jsonl", max_rows=2)


def test_only_delegate_rows_are_kept():
    assert is_delegate_row({"t": "trace_update"})
    assert is_delegate_row({"role": "sub_agent_trace"})
    assert is_delegate_row(_delegate("t1", 1.0))
    assert not is_delegate_row(_chat(1.0))
    assert not is_delegate_row({"role": "user", "source": "agent_delegate"})


def test_unchanged_segments_are_not_read_again(tmp_path, reads):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _chat(2.0), _chat(3.0), _delegate("t2", 4.0)])
    memo = DelegateRowMemo()

    first = memo.rows("conv", log)
    assert [row["source"]["task_id"] for row in first] == ["t1", "t2"]
    assert len(reads) == len(log.iter_paths()) >= 2

    reads.clear()
    assert memo.rows("conv", log) == first
    assert reads == []


def test_appended_rows_reread_only_the_changed_segment(tmp_path, reads):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _chat(2.0), _chat(3.0)])
    memo = DelegateRowMemo()
    memo.rows("conv", log)
    last = log.iter_paths()[-1].name

    reads.clear()
    log.append_dicts([_delegate("t2", 4.0)])
    rows = memo.rows("conv", log)

    assert [row["source"]["task_id"] for row in rows] == ["t1", "t2"]
    assert last in reads
    assert log.iter_paths()[0].name not in reads


def test_rewritten_segment_drops_stale_rows(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _delegate("t2", 2.0)])
    memo = DelegateRowMemo()
    assert len(memo.rows("conv", log)) == 2

    log.replace_dicts([_delegate("t3", 3.0)])
    assert [row["source"]["task_id"] for row in memo.rows("conv", log)] == ["t3"]


def test_shared_rows_stay_compact_and_content_is_read_on_demand(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_chat(1.0), _delegate("t1", 2.0)])
    log.codec = _Codec()

    memo = DelegateRowMemo()
    (row,) = memo.rows("conv", log)
    assert "content" not in row
    assert log.codec.decoded == []

    assert memo.content(log, row["_ref"]) == "task t1"
    assert log.codec.decoded == ["task t1"]


def test_content_of_a_moved_row_is_empty(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0)])
    memo = DelegateRowMemo()
    (row,) = memo.rows("conv", log)

    log.replace_dicts([_delegate("t2", 2.0)])
    assert memo.content(log, row["_ref"]) == ""
