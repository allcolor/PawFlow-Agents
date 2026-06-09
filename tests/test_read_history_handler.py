from core.handlers.history import ReadHistoryHandler


class FakeConversationStore:
    def __init__(self, messages):
        self.messages = messages

    def load(self, conversation_id, user_id=None):
        return self.messages


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
