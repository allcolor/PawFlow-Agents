"""read_history search answered from the conversation index.

The indexed answer must be the scan's answer, character for character, and
must not decode the transcript. Anything the index cannot answer for falls
back to the scan.
"""

import time

import pytest

import core.paths as _paths
from core.conversation_index import ConversationIndex
from core.conversation_store import ConversationStore
from core.handlers.history import ReadHistoryHandler

USER = "alice"

MESSAGES = [
    {"role": "user", "content": "Please hotpatch the relay",
     "source": {"target_agent": "claude"}},
    {"role": "assistant", "content": "Hotpatch applied to the relay.",
     "source": {"type": "agent", "name": "claude"}},
    {"role": "tool", "content": "hotpatch tool output"},
    {"role": "user", "content": "qwen, review the relay change",
     "source": {"target_agent": "qwen"}},
    {"role": "assistant", "content": "Relay change reviewed, no hotpatch.",
     "source": {"type": "agent", "name": "qwen"}},
    {"role": "user", "content": "Déployer la mise à jour",
     "source": {"target_agent": "claude"}},
]

SEARCHES = [
    {"query": "hotpatch", "role_filter": "user"},
    {"query": "hotpatch", "role_filter": "assistant"},
    {"query": "HOTPATCH", "role_filter": "assistant", "agent_filter": "qwen"},
    {"query": "relay", "role_filter": "user", "agent_filter": "claude"},
    {"query": "relay review change", "role_filter": "user"},
    {"query": "MISE À", "role_filter": "user"},
    {"query": "absent words here", "role_filter": "assistant"},
    {"query": "relay", "role_filter": "user", "limit": 1, "offset": 1},
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(_paths, "CONVERSATION_INDEX_DIR",
                        tmp_path / "conversation_index")
    ConversationIndex.reset()
    ConversationStore.reset()
    store = ConversationStore(store_dir=str(tmp_path / "conversations"))
    monkeypatch.setattr(ConversationStore, "instance",
                        classmethod(lambda cls: store))
    yield store
    ConversationIndex.reset()
    ConversationStore.reset()


def _handler(cid, user_id):
    handler = ReadHistoryHandler()
    handler.set_conversation_id(cid)
    handler.set_user_id(user_id)
    return handler


def _indexed(store, cid):
    index = ConversationIndex.for_user(USER)
    index.ensure_current(store, cid)
    deadline = time.monotonic() + 10
    while not index.ensure_current(store, cid):
        assert time.monotonic() < deadline, "index never became current"
        time.sleep(0.01)
    return index


def _scan_answer(store, cid, search):
    """The scan's answer: the handler with its index path switched off."""
    import core.handlers.history as history
    real = history.indexed_search_hits
    history.indexed_search_hits = lambda *a, **k: None
    try:
        return _handler(cid, USER).execute({"action": "search", **search})
    finally:
        history.indexed_search_hits = real


def test_indexed_answers_equal_the_scan(isolated, monkeypatch):
    store = isolated
    cid = store.generate_id()
    store.save(cid, MESSAGES, user_id=USER)
    _indexed(store, cid)
    expected = [_scan_answer(store, cid, s) for s in SEARCHES]

    def no_transcript_reads(*args, **kwargs):
        raise AssertionError("indexed search decoded the transcript")

    monkeypatch.setattr(store, "iter_display_search_windows",
                        no_transcript_reads)
    monkeypatch.setattr(store, "iter_display_windows", no_transcript_reads)
    handler = _handler(cid, USER)

    got = [handler.execute({"action": "search", **s}) for s in SEARCHES]

    assert got == expected
    assert got[0].startswith("Found 1 match(es) for 'hotpatch' [role=user]")


def test_a_conversation_not_yet_indexed_is_scanned(isolated):
    store = isolated
    cid = store.generate_id()
    store.save(cid, MESSAGES, user_id=USER)

    out = _handler(cid, USER).execute(
        {"action": "search", "query": "hotpatch", "role_filter": "user"})

    assert out == _scan_answer(store, cid, {"query": "hotpatch",
                                            "role_filter": "user"})
    assert "[#0] user" in out


def test_tool_and_unfiltered_searches_keep_the_scan(isolated, monkeypatch):
    store = isolated
    cid = store.generate_id()
    store.save(cid, MESSAGES, user_id=USER)
    _indexed(store, cid)
    consulted = []
    monkeypatch.setattr(ConversationIndex, "search_conversation",
                        lambda *a, **k: consulted.append(a) or [])
    handler = _handler(cid, USER)

    tool = handler.execute({"action": "search", "query": "hotpatch",
                            "role_filter": "tool"})
    unfiltered = handler.execute({"action": "search", "query": "hotpatch"})

    assert consulted == []
    assert "hotpatch tool output" in tool
    assert "hotpatch tool output" in unfiltered


def test_another_users_conversation_is_never_answered_from_this_index(
        isolated, monkeypatch):
    store = isolated
    cid = store.generate_id()
    store.save(cid, MESSAGES, user_id="bob")
    monkeypatch.setattr(ConversationIndex, "ensure_current",
                        lambda *a, **k: pytest.fail("index consulted"))

    out = _handler(cid, USER).execute(
        {"action": "search", "query": "hotpatch", "role_filter": "user"})

    assert "No history" in out or "No messages matching" in out
