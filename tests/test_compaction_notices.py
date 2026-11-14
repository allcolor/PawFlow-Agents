"""Compaction and background-summary notices (core.compaction_notices)."""

import inspect
from unittest.mock import MagicMock

import pytest

from core import compaction_notices as notices
from core.conversation_store import ConversationStore
from core.segmented_jsonl import SegmentedJsonl

CFG = {"l1_trigger_msgs": 8, "tail_reserve_msgs": 2, "chars_per_token": 1.0,
       "min_input_multiplier": 4.0, "bucket_target_tokens": 25}


@pytest.fixture
def conv(tmp_path, monkeypatch):
    ConversationStore.reset()
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    cid = store.generate_id()
    store.save(cid, [], user_id="alice")
    monkeypatch.setattr(ConversationStore, "instance", classmethod(lambda cls: store))
    builder = MagicMock()
    builder._bg_compact_config.return_value = dict(CFG)
    monkeypatch.setattr("core.bg_bucket_builder.BgBucketBuilder.instance",
                        classmethod(lambda cls: builder))
    yield store, cid
    ConversationStore.reset()


def _shared(store, cid, seqs, content="x" * 20):
    SegmentedJsonl(store._shared_ctx_path(cid)).append_dicts([
        {"role": "user", "content": content, "seq": s, "ts": float(s),
         "msg_id": f"m{s}"} for s in seqs])


def test_notice_is_a_transcript_only_system_row(monkeypatch):
    writer = MagicMock()
    monkeypatch.setattr(
        "core.conversation_writer.ConversationWriter.for_conversation",
        lambda _cid: writer)

    notices.publish_notice("conv1", "alice", "summaries failing",
                           "summary_failing", first_seq=3)

    message = writer.enqueue_message.call_args.args[0]
    assert message["role"] == "system"
    assert message["display_only"] is True
    assert message["msg_id"] and message["ts"]
    assert message["source"] == {"type": "system", "name": "compaction",
                                 "event": "summary_failing", "first_seq": 3}
    sse = writer.enqueue_message.call_args.kwargs["sse_events"]
    assert sse == [{"type": "new_message", "data": message}]


def test_gap_between_pyramid_and_tail_is_measured(conv):
    store, cid = conv
    _shared(store, cid, range(101, 141))

    gap = notices.find_history_gap(cid, "alice", 100, [150, 0, 160])

    assert gap == notices.HistoryGap(first_seq=101, last_seq=140, rows=40,
                                     tokens=800)
    assert "seq 101..140" in gap.agent_note()
    assert "read_history" in gap.agent_note()
    assert "GameDev3" in gap.user_notice("GameDev3")


def test_normal_builder_lag_is_not_a_gap(conv):
    store, cid = conv
    _shared(store, cid, range(101, 110))  # fewer rows than l1 + reserve
    assert notices.find_history_gap(cid, "alice", 100, [150]) is None

    _shared(store, cid, range(110, 130), content="x")  # enough rows, too few tokens
    assert notices.find_history_gap(cid, "alice", 109, [150]) is None


def test_tail_touching_the_pyramid_has_no_gap(conv):
    store, cid = conv
    _shared(store, cid, range(101, 141))
    assert notices.find_history_gap(cid, "alice", 140, [141]) is None
    assert notices.find_history_gap(cid, "alice", 100, []) is None


def test_report_notifies_user_and_returns_agent_note(conv, monkeypatch):
    store, cid = conv
    _shared(store, cid, range(101, 141))
    sent = []
    monkeypatch.setattr(notices, "publish_notice",
                        lambda c, u, text, event, **d: sent.append((event, d)))

    note = notices.report_history_gap(cid, "alice", "GameDev3", 100, [150])

    assert note.startswith("\n[History gap]")
    assert sent == [("history_gap", {"agent": "GameDev3", "first_seq": 101,
                                     "last_seq": 140, "rows": 40})]
    sent.clear()
    assert notices.report_history_gap(cid, "alice", "GameDev3", 140, [141]) == ""
    assert sent == []


def test_report_never_breaks_the_compaction(monkeypatch):
    def _boom(*_args):
        raise OSError("disk gone")
    monkeypatch.setattr(notices, "find_history_gap", _boom)
    assert notices.report_history_gap("conv1", "alice", "a", 0, [5]) == ""


def test_compact_puts_the_gap_note_into_the_header_it_builds():
    from tasks.ai._agent_compact_core import _AgentCompactCoreMixin
    src = inspect.getsource(_AgentCompactCoreMixin._compact)
    report = src.index("_pyramid_header += report_history_gap(")
    assert src.index("[compact] tail walk-back") < report
    assert report < src.index("compacted = _build_output(saved_recent)")
