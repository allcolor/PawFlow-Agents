from core.handlers.history import ReadHistoryHandler


class FakeConversationStore:
    """The bounded reader API the handler is allowed to use.

    ``load`` is deliberately absent: the handler must never ask for the whole
    transcript. A conversation reaches hundreds of thousands of messages and
    read_history is called in chains -- a handler that materialises the file
    once per call takes the server down with it, which is exactly what
    happened. If a future change reintroduces a full load, these tests fail
    with AttributeError rather than passing quietly on a small fixture.
    """

    #: Small on purpose: the tests then exercise the window boundary itself
    #: rather than always fitting in a single window.
    WINDOW = 2

    def __init__(self, messages):
        self.messages = messages

    def message_count(self, conversation_id):
        return len(self.messages)

    def _load_cache(self, conversation_id):
        return {"user_id": ""}

    def iter_display_windows(self, conversation_id, chunk=0):
        step = int(chunk or self.WINDOW)
        for start in range(0, len(self.messages), step):
            yield start, self.messages[start:start + step]

    def load_window_by_index(self, conversation_id, start, count):
        if count <= 0 or start < 0:
            return []
        return self.messages[start:start + count]

    def find_display_index(self, conversation_id, msg_id="", seq=0, ts=0.0,
                           backward=False):
        best = -1
        for i, m in enumerate(self.messages):
            if msg_id:
                if m.get("msg_id") == msg_id:
                    return i
                continue
            if seq:
                row_seq = int(m.get("seq") or 0)
                if row_seq == seq:
                    return i
                if not row_seq:
                    continue
                if backward:
                    if row_seq <= seq:
                        best = i
                elif row_seq >= seq:
                    return i
                continue
            if ts and float(m.get("ts") or m.get("timestamp") or 0.0) >= ts:
                return i
        return best


def test_read_history_search_falls_back_to_keyword_matches():
    handler = ReadHistoryHandler()
    query = "rtk PAWFLOW_USE_RTK rewrite bash grep glob read"
    messages = [
        {"role": "assistant", "content": "Unrelated scheduler note."},
        {
            "role": "assistant",
            "content": (
                "Je vais faire un premier passage utilisable et sur: bash via "
                "rtk rewrite pour les commandes supportees, puis grep/glob/read."
            ),
        },
        {
            "role": "assistant",
            "content": "Je verifie PAWFLOW_USE_RTK pour eviter une activation accidentelle.",
        },
    ]

    result = handler._do_search(
        FakeConversationStore(messages),
        {"query": query, "limit": 20},
        role_filter="",
        agent_filter="",
    )

    assert "Found 2 match(es)" in result
    assert "rtk rewrite" in result
    assert "PAWFLOW_USE_RTK" in result


def test_read_history_search_prefers_exact_phrase_matches():
    handler = ReadHistoryHandler()
    messages = [
        {"role": "assistant", "content": "alpha beta"},
        {"role": "assistant", "content": "alpha only"},
    ]

    result = handler._do_search(
        FakeConversationStore(messages),
        {"query": "alpha beta", "limit": 20},
        role_filter="",
        agent_filter="",
    )

    assert "Found 1 match(es)" in result
    assert "alpha beta" in result
    assert "alpha only" not in result


def test_read_history_keyword_fallback_rejects_single_generic_term_hits():
    handler = ReadHistoryHandler()
    messages = [
        {"role": "assistant", "content": "exact wording elsewhere"},
        {"role": "assistant", "content": "another hotpatch discussion"},
        {"role": "assistant", "content": "the manifest was regenerated"},
        {"role": "assistant", "content": "hotpatch manifest verified"},
        {"role": "assistant", "content": "revision 91 was published"},
    ]

    result = handler._do_search(
        FakeConversationStore(messages),
        {"query": "exact hotpatch manifest 91", "limit": 20},
        role_filter="",
        agent_filter="",
    )

    assert "Found 1 match(es)" in result
    assert "hotpatch manifest verified" in result
    assert "exact wording elsewhere" not in result
    assert "another hotpatch discussion" not in result
    assert "revision 91" not in result


def test_read_history_keyword_fallback_keeps_distinctive_identifiers():
    handler = ReadHistoryHandler()
    messages = [
        {"role": "assistant", "content": "A generic rewrite happened."},
        {
            "role": "assistant",
            "content": "PAWFLOW_USE_RTK remains disabled.",
        },
    ]

    result = handler._do_search(
        FakeConversationStore(messages),
        {"query": "rtk PAWFLOW_USE_RTK rewrite bash grep glob read", "limit": 20},
        role_filter="",
        agent_filter="",
    )

    assert "Found 1 match(es)" in result
    assert "PAWFLOW_USE_RTK" in result
    assert "generic rewrite" not in result


def test_read_history_oldest_returns_head_not_recent_tail():
    handler = ReadHistoryHandler()
    messages = [
        {"msg_id": "m1", "seq": 10, "role": "user", "content": "first"},
        {"msg_id": "m2", "seq": 20, "role": "assistant", "content": "second"},
        {"msg_id": "m3", "seq": 30, "role": "user", "content": "third"},
    ]

    result = handler._do_oldest(
        FakeConversationStore(messages),
        {"limit": 2},
        role_filter="",
        agent_filter="",
    )

    assert "Oldest messages (3 messages total)" in result
    assert "first" in result
    assert "second" in result
    assert "third" not in result


def test_read_history_around_from_missing_seq_anchors_forward_to_next_seq():
    handler = ReadHistoryHandler()
    messages = [
        {"msg_id": "m1", "seq": 10, "role": "user", "content": "alpha"},
        {"msg_id": "m2", "seq": 20, "role": "assistant", "content": "beta"},
        {"msg_id": "m3", "seq": 30, "role": "user", "content": "gamma"},
    ]

    result = handler._do_around(
        FakeConversationStore(messages),
        {"from_seq": 15, "limit": 2},
        role_filter="",
        agent_filter="",
    )

    assert "beta" in result
    assert "gamma" in result
    assert "alpha" not in result


def test_read_history_around_from_missing_seq_anchors_backward_to_previous_seq():
    handler = ReadHistoryHandler()
    messages = [
        {"msg_id": "m1", "seq": 10, "role": "user", "content": "alpha"},
        {"msg_id": "m2", "seq": 20, "role": "assistant", "content": "beta"},
        {"msg_id": "m3", "seq": 30, "role": "user", "content": "gamma"},
    ]

    result = handler._do_around(
        FakeConversationStore(messages),
        {"from_seq": 25, "limit": -2},
        role_filter="",
        agent_filter="",
    )

    assert "alpha" in result
    assert "beta" in result
    assert "gamma" not in result


# ── Ownership ───────────────────────────────────────────────────────
#
# `recent` pages through load_page, which is given the user_id and scopes
# itself. Every windowed reader -- search, oldest, range, around -- walks
# iter_display_windows with the conversation_id alone, so for those
# _owns_conversation is the only thing standing between another user's
# transcript and whoever knows its id.


class _BrokenCacheStore(FakeConversationStore):
    """Metadata unreadable: corrupted extras, a bad disk, a race on write."""

    def _load_cache(self, conversation_id):
        raise OSError("extras.json is unreadable")


class _OwnedByAliceStore(FakeConversationStore):

    def _load_cache(self, conversation_id):
        return {"user_id": "alice"}


def _reader(store_cls, messages, user_id):
    handler = ReadHistoryHandler()
    handler.set_conversation_id("c1")
    handler.set_user_id(user_id)
    return handler, store_cls(messages)


#: In the message body only. "No messages matching '<query>'" echoes the query
#: back, so asserting on the search term would pass on a refusal AND on a hit.
_SECRET = "zx-alice-only"


def test_an_unreadable_ownership_lookup_denies_instead_of_serving():
    # Fail-open here meant a metadata failure handed the transcript to any
    # authenticated user who knew the conversation id.
    handler, store = _reader(
        _BrokenCacheStore,
        [{"role": "user", "content": f"alice's private note {_SECRET}"}],
        user_id="bob")

    result = handler._do_search(store, {"query": "private", "limit": 10},
                                role_filter="", agent_filter="")

    assert _SECRET not in result


def test_another_users_conversation_is_refused():
    handler, store = _reader(
        _OwnedByAliceStore,
        [{"role": "user", "content": f"alice's private note {_SECRET}"}],
        user_id="bob")

    result = handler._do_search(store, {"query": "private", "limit": 10},
                                role_filter="", agent_filter="")

    assert _SECRET not in result


def test_the_owner_still_reads_their_own_conversation():
    handler, store = _reader(
        _OwnedByAliceStore,
        [{"role": "user", "content": f"alice's private note {_SECRET}"}],
        user_id="alice")

    result = handler._do_search(store, {"query": "private", "limit": 10},
                                role_filter="", agent_filter="")

    assert _SECRET in result
