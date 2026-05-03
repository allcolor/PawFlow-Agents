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
