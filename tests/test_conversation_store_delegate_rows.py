"""Delegate rows are partitioned per caller and memoised per transcript segment."""

import random

import pytest

from core import _conversation_store_delegate_rows as delegate_rows
from core._conversation_store_delegate_rows import DelegateRowMemo, is_delegate_row
from core.conversation_store import ConversationStore
from core.segmented_jsonl import SegmentedJsonl


def _delegate(task_id, ts, sender="caller", target="worker"):
    return {"role": "user", "content": f"task {task_id}", "timestamp": ts,
            "source": {"type": "agent_delegate", "from": sender,
                       "to": target, "task_id": task_id}}


def _reply(task_id, ts, role="tool_call", turn_final=False, sender="worker",
           target="caller"):
    row = {"role": role, "content": f"{role} {task_id}", "timestamp": ts,
           "source": {"type": "agent_delegate", "from": sender, "to": target,
                      "kind": "reply", "task_id": task_id}}
    if turn_final is not None:
        row["turn_final"] = turn_final
    return row


def _trace(task_id, ts, parent="caller"):
    return {"role": "sub_agent_trace", "content": "trace " + task_id,
            "timestamp": ts, "trace_id": task_id,
            "source": {"name": "helper", "parent_agent": parent,
                       "task_id": task_id}}


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


def _tasks(rows):
    return [row["source"]["task_id"] for row in rows]


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

    first = memo.rows("conv", log, "caller")
    assert _tasks(first) == ["t1", "t2"]
    assert len(reads) == len(log.iter_paths()) >= 2

    reads.clear()
    assert memo.rows("conv", log, "caller") == first
    assert reads == []


def test_appended_rows_reread_only_the_changed_segment(tmp_path, reads):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _chat(2.0), _chat(3.0)])
    memo = DelegateRowMemo()
    memo.rows("conv", log, "caller")
    last = log.iter_paths()[-1].name

    reads.clear()
    log.append_dicts([_delegate("t2", 4.0)])
    rows = memo.rows("conv", log, "caller")

    assert _tasks(rows) == ["t1", "t2"]
    assert last in reads
    assert log.iter_paths()[0].name not in reads


def test_rewritten_segment_drops_stale_rows(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _delegate("t2", 2.0)])
    memo = DelegateRowMemo()
    assert len(memo.rows("conv", log, "caller")) == 2

    log.replace_dicts([_delegate("t3", 3.0)])
    assert _tasks(memo.rows("conv", log, "caller")) == ["t3"]


def test_shared_rows_stay_compact_and_content_is_read_on_demand(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_chat(1.0), _delegate("t1", 2.0)])
    log.codec = _Codec()

    memo = DelegateRowMemo()
    (row,) = memo.rows("conv", log, "caller")
    assert "content" not in row
    assert log.codec.decoded == []

    assert memo.content(log, row["_ref"]) == "task t1"
    assert log.codec.decoded == ["task t1"]


def test_content_of_a_moved_row_is_empty(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0)])
    memo = DelegateRowMemo()
    (row,) = memo.rows("conv", log, "caller")

    log.replace_dicts([_delegate("t2", 2.0)])
    assert memo.content(log, row["_ref"]) == ""


def test_a_caller_reads_only_its_own_rows(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([
        _delegate("t1", 1.0),                                # caller -> worker
        _delegate("t2", 2.0, sender="other", target="worker"),
        _reply("t2", 3.0, sender="worker", target="other"),
        _delegate("t3", 4.0, sender="other", target="caller"),  # contact
        _trace("f1", 5.0),
        _trace("f2", 6.0, parent="other"),
        {"t": "trace_update", "trace_id": "f2", "timestamp": 7.0},
    ])
    memo = DelegateRowMemo()
    rows = memo.rows("conv", log, "Caller")
    assert [row.get("trace_id") or row["source"]["task_id"] for row in rows] == [
        "t1", "t3", "f1", "f2"]
    assert rows[-1]["t"] == "trace_update"  # every caller reads trace updates


def test_a_run_of_non_terminal_replies_keeps_its_last_row(tmp_path):
    log = SegmentedJsonl(tmp_path / "transcript.jsonl", max_rows=100)
    log.append_dicts([
        _delegate("t1", 1.0),
        _reply("t1", 2.0, "assistant"), _reply("t1", 3.0, "tool_call"),
        _reply("t1", 4.0, "tool"),
        _reply("t1", 3.5, "tool"),           # time went back: a new run
        _reply("t1", 5.0, "user", None),     # never collapsed
        _reply("t1", 6.0, "assistant", True),
    ])
    rows = DelegateRowMemo().rows("conv", log, "caller")
    assert [(row["role"], row["timestamp"]) for row in rows] == [
        ("user", 1.0), ("tool", 4.0), ("tool", 3.5), ("user", 5.0),
        ("assistant", 6.0)]
    assert rows[-1]["terminal"] is True


def test_a_restarted_process_reads_only_the_tail_segment(tmp_path, reads):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _chat(2.0), _trace("f1", 3.0),
                      _chat(4.0), _delegate("t2", 5.0)])
    first = DelegateRowMemo().rows("conv", log, "caller")
    paths = log.iter_paths()
    assert len(paths) >= 3

    reads.clear()
    assert DelegateRowMemo().rows("conv", log, "caller") == first
    assert reads == [paths[-1].name]


def test_sidecar_keeps_trace_rows_encoded(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_trace("f1", 1.0), _chat(2.0), _chat(3.0)])
    DelegateRowMemo().rows("conv", log, "caller")
    log.codec = _Codec()

    (row,) = DelegateRowMemo().rows("conv", log, "caller")
    assert row["content"] == "trace f1"
    assert log.codec.decoded == ["trace f1"]


def test_a_segment_sealed_after_caching_gets_its_sidecar(tmp_path, reads):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0)])
    memo = DelegateRowMemo()
    memo.rows("conv", log, "caller")
    log.append_dicts([_chat(2.0), _delegate("t2", 3.0)])
    memo.rows("conv", log, "caller")

    reads.clear()
    assert _tasks(DelegateRowMemo().rows("conv", log, "caller")) == ["t1", "t2"]
    assert reads == [log.iter_paths()[-1].name]


def test_a_rewritten_sealed_segment_ignores_its_sidecar(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _chat(2.0), _chat(3.0)])
    DelegateRowMemo().rows("conv", log, "caller")

    log.replace_dicts([_delegate("t3", 1.0), _delegate("t4", 2.0), _chat(3.0)])
    assert _tasks(DelegateRowMemo().rows("conv", log, "caller")) == ["t3", "t4"]


def test_a_previous_sidecar_version_is_rebuilt(tmp_path, reads):
    log = _log(tmp_path)
    log.append_dicts([_delegate("t1", 1.0), _chat(2.0), _chat(3.0)])
    DelegateRowMemo().rows("conv", log, "caller")
    sidecar = delegate_rows._sidecar_path(log, log.iter_paths()[0])
    sidecar.write_text(sidecar.read_text().replace('"v": 2', '"v": 1', 1))

    reads.clear()
    assert _tasks(DelegateRowMemo().rows("conv", log, "caller")) == ["t1"]
    assert log.iter_paths()[0].name in reads
    assert '"v": 2' in sidecar.read_text()


def test_lines_without_a_delegate_value_are_not_decoded(tmp_path):
    log = _log(tmp_path)
    log.append_dicts([_chat(1.0), _delegate("t1", 2.0)])
    path = str(log.iter_paths()[0])
    assert [row["source"]["task_id"]
            for _, row in delegate_rows._iter_offsets(path)] == ["t1"]


# -- the partition and the collapse change no caller's state -----------------

_AGENTS = ["root", "dev1", "dev2", "dev3"]


def _random_transcript(seed):
    """Interleaved multi-agent delegate traffic, flash traces and chat."""
    rng = random.Random(seed)
    rows, open_tasks, at = [], [], 1_000.0
    for n in range(400):
        at += rng.choice([0.5, 1.0, 1.0, -0.25])  # clocks may step back
        kind = rng.random()
        if kind < 0.15 or not open_tasks:
            sender, target = rng.sample(_AGENTS, 2)
            task = f"t{n}"
            open_tasks.append((task, sender, target))
            rows.append(_delegate(task, at, sender, target))
        elif kind < 0.80:
            task, sender, target = rng.choice(open_tasks)
            role = rng.choice(["assistant", "tool_call", "tool", "thinking"])
            final = rng.choice([False, False, False, False, True, None])
            rows.append(_reply(task, at, role, final, sender=target, target=sender))
            if final is not False and rng.random() < 0.5:
                open_tasks.remove((task, sender, target))
        elif kind < 0.85:
            task, sender, target = rng.choice(open_tasks)
            rows.append(_reply(task, at, "user", None, sender=target, target=sender))
        elif kind < 0.90:
            rows.append(_trace(f"f{n}", at, parent=rng.choice(_AGENTS)))
        elif kind < 0.95:
            flash = [row["trace_id"] for row in rows if row.get("role") == "sub_agent_trace"]
            if flash:
                rows.append({"t": "trace_update", "trace_id": rng.choice(flash),
                             "timestamp": at, "content_update": "part ",
                             "entry": rng.choice([{}, {"type": "done",
                                                       "status": "completed"}])})
        else:
            rows.append(_chat(at))
    return rows


def _states(tmp_path, name, rows, monkeypatch, naive):
    store = ConversationStore(store_dir=str(tmp_path / name))
    store.save("conv", [], user_id="user1")
    log = store._transcript_log("conv")
    SegmentedJsonl(log.flat_path, max_rows=7).append_dicts(rows)
    with monkeypatch.context() as patch:
        if naive:
            patch.setattr(delegate_rows, "_callers", lambda _row: [delegate_rows._ALL])
            patch.setattr(delegate_rows, "_run_key", lambda _row: None)
        passes = []
        for _ in range(2):  # cold, then a restarted process on the sidecars
            patch.setattr(delegate_rows, "DELEGATE_ROWS", DelegateRowMemo())
            passes.append({agent: store.load_delegate_state("conv", agent)
                           for agent in _AGENTS})
    return passes


# A wrong collapse (one ignoring turn_final, or the task) shows on about half
# of the seeds: twelve leave no realistic chance of missing it.
@pytest.mark.parametrize("seed", range(12))
def test_partitioned_collapsed_state_equals_the_full_replay(tmp_path, monkeypatch, seed):
    rows = _random_transcript(seed)
    expected, expected_again = _states(tmp_path, "naive", rows, monkeypatch, True)
    cold, restarted = _states(tmp_path, "memo", rows, monkeypatch, False)
    assert expected == expected_again
    assert any(state["finished"] or state["live"] for state in expected.values())
    assert cold == expected
    assert restarted == expected
