"""Conversation index: display positions, counting, and read_history's API.

What these pin:

- the watermark counts display rows the way read_history numbers them, so a
  metadata count that lags (it did after every restart) no longer reads as a
  shrunken transcript that must be purged and re-read whole -- 80 s on the
  largest conversation;
- a full reindex streams display windows and never loads the transcript;
- every indexed row carries read_history's ``[#n]``;
- ``ensure_current`` indexes appended rows inline but hands a full reindex to
  a background thread, so read_history never waits on one.
"""

import time

import pytest

import core.paths as _paths
from core.conversation_index import ConversationIndex
from core.conversation_store import ConversationStore

USER = "alice"


@pytest.fixture(autouse=True)
def index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "CONVERSATION_INDEX_DIR",
                        tmp_path / "conversation_index")
    ConversationIndex.reset()
    yield
    ConversationIndex.reset()


@pytest.fixture(autouse=True)
def reset_store():
    ConversationStore.reset()
    yield
    ConversationStore.reset()


@pytest.fixture
def store(tmp_path):
    return ConversationStore(store_dir=str(tmp_path / "conversations"))


@pytest.fixture
def index():
    return ConversationIndex.for_user(USER)


def _conv(store, messages):
    cid = store.generate_id()
    store.save(cid, messages, user_id=USER)
    return cid


def _wait_rebuilt(index, cid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with index._conv_locks_guard:
            if cid not in index._rebuilding:
                return
        time.sleep(0.01)
    raise AssertionError("background rebuild did not finish")


def _display_positions(store, cid, role):
    return [start + i
            for start, msgs in store.iter_display_windows(cid)
            for i, msg in enumerate(msgs) if msg.get("role") == role]


class TestCounting:

    def test_a_lagging_metadata_count_does_not_reindex_whole(
            self, store, index, monkeypatch):
        cid = _conv(store, [{"role": "user", "content": f"row {i}"}
                            for i in range(3)])
        index.refresh(store)
        store.append_message(cid, {"role": "assistant",
                                   "content": "the new row"}, user_id=USER)
        # The cached metadata count trails the transcript, as it did after
        # a restart: below the watermark.
        monkeypatch.setattr(store, "message_count", lambda _cid: 1)

        def whole_read_is_the_bug(*args, **kwargs):
            raise AssertionError("an append re-read the whole transcript")

        monkeypatch.setattr(store, "load", whole_read_is_the_bug)
        monkeypatch.setattr(store, "iter_display_windows",
                            whole_read_is_the_bug)

        stats = index.refresh(store)

        assert stats["messages"] == 1
        assert len(index.search("row")) == 4

    def test_a_full_reindex_never_loads_the_whole_transcript(
            self, store, index, monkeypatch):
        cid = _conv(store, [{"role": "user", "content": "streamed needle"}])

        def full_load_is_the_bug(*args, **kwargs):
            raise AssertionError("reindex called store.load()")

        monkeypatch.setattr(store, "load", full_load_is_the_bug)
        index.refresh(store)

        assert [h["conversation_id"] for h in index.search("needle")] == [cid]


class TestPositions:

    def test_rows_carry_read_history_numbering(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "question one"},
            {"role": "assistant", "content": "answer one"},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "answer two"},
        ])
        index.refresh(store)
        store.append_message(cid, {"role": "tool", "content": "more output"},
                             user_id=USER)
        store.append_message(cid, {"role": "assistant",
                                   "content": "answer three"}, user_id=USER)
        index.refresh(store)  # incremental: positions continue past the tail

        rows = index.search_conversation(cid, "assistant")

        assert [r["pos"] for r in rows] == _display_positions(
            store, cid, "assistant")
        assert [r["content"] for r in rows] == [
            "answer one", "answer two", "answer three"]

    def test_long_messages_are_indexed_whole(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "x" * 30000 + " tail-needle"},
        ])
        index.refresh(store)

        rows = index.search_conversation(cid, "user", needles=["tail-needle"])

        assert len(rows) == 1 and rows[0]["content"].endswith("tail-needle")


class TestSearchConversation:

    def test_agent_filter_matches_whole_involved_names(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "to claude",
             "source": {"target_agent": "claude"}},
            {"role": "assistant", "content": "from claude",
             "source": {"type": "agent", "name": "claude"}},
            {"role": "user", "content": "to qwen",
             "source": {"target_agent": "qwen"}},
        ])
        index.refresh(store)

        assert [r["content"] for r in index.search_conversation(
            cid, "user", agent="claude")] == ["to claude"]
        assert index.search_conversation(cid, "user", agent="clau") == []
        speaker = index.search_conversation(cid, "assistant")[0]["speaker"]
        assert speaker == "claude"

    def test_needles_prefilter_case_insensitively(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "Deploy the HotPatch now"},
            {"role": "user", "content": "unrelated"},
        ])
        index.refresh(store)

        rows = index.search_conversation(cid, "user", needles=["hotpatch"])

        assert [r["content"] for r in rows] == ["Deploy the HotPatch now"]


class TestEnsureCurrent:

    def test_a_new_conversation_is_rebuilt_in_the_background(self, store,
                                                             index):
        cid = _conv(store, [{"role": "user", "content": "first"}])

        assert index.ensure_current(store, cid) is False
        _wait_rebuilt(index, cid)

        assert index.ensure_current(store, cid) is True
        assert [r["content"] for r in index.search_conversation(
            cid, "user")] == ["first"]

    def test_appended_rows_are_indexed_inline(self, store, index):
        cid = _conv(store, [{"role": "user", "content": "first"}])
        index.ensure_current(store, cid)
        _wait_rebuilt(index, cid)
        store.append_message(cid, {"role": "user", "content": "second",
                                   "source": {"target_agent": "claude"}},
                             user_id=USER)

        assert index.ensure_current(store, cid) is True
        assert [r["content"] for r in index.search_conversation(
            cid, "user")] == ["first", "second"]

    def test_a_rewrite_is_never_answered_from_stale_rows(self, store, index):
        cid = _conv(store, [
            {"role": "user", "content": "my password is hunter2",
             "msg_id": "m1", "ts": 1.0},
        ])
        index.ensure_current(store, cid)
        _wait_rebuilt(index, cid)
        store.edit_message(cid, "m1", "my password is REDACTED",
                           user_id=USER)

        assert index.ensure_current(store, cid) is False
        _wait_rebuilt(index, cid)

        assert index.ensure_current(store, cid) is True
        assert [r["content"] for r in index.search_conversation(
            cid, "user")] == ["my password is REDACTED"]

    def test_an_encrypted_conversation_is_never_answered(self, store, index,
                                                         monkeypatch):
        cid = _conv(store, [{"role": "user", "content": "secret"}])
        monkeypatch.setattr(store, "encryption_status",
                            lambda _cid: {"enabled": True})

        assert index.ensure_current(store, cid) is False
        with index._conv_locks_guard:
            assert cid not in index._rebuilding
