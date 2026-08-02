"""A captured tmux turn must stream like any other turn.

Production sequence (2026-07-28): a background tool result reached Claude Code
and it resumed on its own. The tmux worked for minutes — text, tool calls,
tool results, all observed by the MITM proxy — and the webchat showed nothing
until the turn ended, because the capture built its coordinator with no
callbacks and persisted a single lump at the end.

Everything the proxy intercepts must reach the SSE listeners while it happens,
whoever started the turn. The Antigravity observer already works that way (its
manual ingest streams out-of-band tmux activity by default); this is the same
rule for Claude Code interactive.
"""

import pytest

from services.cc_interactive_event_service import CCInteractiveEventService


@pytest.fixture
def captured(monkeypatch):
    """Return (service, state, written, published)."""
    written = []
    published = []

    class _Writer:
        @staticmethod
        def for_conversation(_cid):
            return _Writer()

        def enqueue_message(self, msg, **kw):
            written.append((msg, kw.get("sse_events") or []))

    class _Bus:
        @staticmethod
        def instance():
            return _Bus()

        def publish_event(self, cid, kind, data):
            published.append((cid, kind, data))

    import core.conversation_writer as cw
    import core.conversation_event_bus as bus
    monkeypatch.setattr(cw, "ConversationWriter", _Writer)
    monkeypatch.setattr(bus, "ConversationEventBus", _Bus)

    svc = CCInteractiveEventService({"token": "tok", "_service_id": "events"})
    state = svc.register_session("sess", user_id="allcol",
                                 conversation_id="80c37670",
                                 agent_name="claude")
    return svc, state, written, published


def _sse_types(written):
    return [e["type"] for _msg, events in written for e in events]


def test_text_streams_live_then_persists_under_the_same_id(captured):
    """The streamed preview and the stored message must share a msg_id.

    Otherwise the client cannot replace its live preview and the answer shows
    up twice.
    """
    svc, state, written, published = captured
    text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

    text_cb("Hel")
    text_cb("lo")
    assert [d["text"] for _c, kind, d in published if kind == "token"] == [
        "Hel", "lo"]
    live_id = published[0][2]["msg_id"]
    assert all(d["msg_id"] == live_id for _c, _k, d in published)

    block_cb("text", {"text": "Hello"})
    assert len(written) == 1
    msg, events = written[0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "Hello"
    assert msg["msg_id"] == live_id
    assert events[0]["type"] == "new_message"


def test_a_second_text_block_gets_a_fresh_live_id(captured):
    svc, state, written, published = captured
    text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

    text_cb("one")
    block_cb("text", {"text": "one"})
    text_cb("two")
    block_cb("text", {"text": "two"})

    assert written[0][0]["msg_id"] != written[1][0]["msg_id"]


def test_tool_calls_and_results_are_persisted_and_published(captured):
    """They used to be dropped entirely: the capture kept only the text."""
    svc, state, written, published = captured
    _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

    block_cb("tool_use", {"id": "tc1", "name": "read",
                          "arguments": {"path": "/x"}})
    block_cb("tool_result", {"tc_id": "tc1", "tool": "read",
                            "result": "contents"})

    assert _sse_types(written) == ["tool_call", "tool_result"]
    call_msg, call_events = written[0]
    assert call_msg["tool_calls"][0]["name"] == "read"
    assert call_events[0]["data"]["tc_id"] == "tc1"
    result_msg, _ = written[1]
    assert result_msg["role"] == "tool"
    assert result_msg["tool_call_id"] == "tc1"
    # Same wrapping the agent loop applies, so a transcript row does not
    # betray which path produced it.
    from tasks.ai.agent_core import AgentCoreMixin
    assert result_msg["content"] == AgentCoreMixin._wrap_tool_output(
        "read", "contents")


def test_incomplete_mcp_tool_call_is_not_persisted(captured):
    """A call with empty args renders bare and is dropped downstream."""
    svc, state, written, _published = captured
    _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

    block_cb("tool_use", {"id": "tc1", "name": "use_tool", "arguments": {}})

    assert written == []


def test_thinking_is_persisted_and_published(captured):
    svc, state, written, _published = captured
    _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

    block_cb("thinking_content", {"text": "reasoning"})

    assert _sse_types(written) == ["thinking_content"]
    assert written[0][0]["thinking"] == "reasoning"


@pytest.mark.parametrize("event_type,payload", [
    ("text", {"text": "   "}),
    ("thinking_content", {"text": ""}),
])
def test_empty_blocks_are_not_persisted(captured, event_type, payload):
    svc, state, written, _published = captured
    _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

    block_cb(event_type, payload)

    assert written == []


def test_a_failing_block_never_kills_the_capture(captured, monkeypatch):
    """A persist failure must not abort the turn still being observed."""
    svc, state, _written, _published = captured

    class _Boom:
        @staticmethod
        def for_conversation(_cid):
            raise RuntimeError("writer down")

    import core.conversation_writer as cw
    monkeypatch.setattr(cw, "ConversationWriter", _Boom)

    _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)
    block_cb("text", {"text": "hello"})  # must not raise


def test_capture_passes_the_callbacks_to_its_coordinator():
    """Guards the regression directly: a capture built with no callbacks."""
    import inspect
    src = inspect.getsource(CCInteractiveEventService._run_manual_capture)
    assert "_capture_stream_callbacks(state)" in src
    assert "callback=_text_cb" in src
    assert "block_callback=_block_cb" in src


class TestCaptureSharesTheSessionToolDedup:
    """A capture must not re-emit tool ids an earlier turn already emitted.

    Claude Code replays its ENTIRE context on every API request, so the proxy
    observes every prior tool_use again. The PawFlow-driven turns dedup against
    the container's sets; a capture that built its coordinator with fresh sets
    re-emitted the whole history — one persisted transcript row and one
    `tool_call` event each. The webchat keys tool blocks by tc_id and absorbs
    the repeat; Telegram does not, so a user got a hundred tool-call messages
    in one burst after a background result resumed the session
    (production, 2026-07-29).
    """

    def test_it_reuses_the_pooled_containers_sets(self, captured, monkeypatch):
        svc, state, _written, _published = captured

        class _Container:
            session_token = "sess"
            emitted_tool_use_ids = {"tc-from-an-earlier-turn"}
            emitted_tool_result_ids = {"tr-from-an-earlier-turn"}

        class _Pool:
            @staticmethod
            def instance():
                return _Pool()

            def find_by_session_token(self, token):
                return _Container() if token == "sess" else None

        import core.claude_code_interactive_pool as pool_mod
        monkeypatch.setattr(pool_mod, "InteractiveClaudeCodePool", _Pool)

        use_ids, result_ids = svc._capture_dedup_sets(state)

        assert "tc-from-an-earlier-turn" in use_ids
        assert "tr-from-an-earlier-turn" in result_ids

    def test_without_a_pooled_container_it_still_dedups_per_session(
            self, captured, monkeypatch):
        # Two chained captures of one session must not forget between them.
        svc, state, _written, _published = captured

        class _Pool:
            @staticmethod
            def instance():
                return _Pool()

            def find_by_session_token(self, _token):
                return None

        import core.claude_code_interactive_pool as pool_mod
        monkeypatch.setattr(pool_mod, "InteractiveClaudeCodePool", _Pool)

        first_use, first_result = svc._capture_dedup_sets(state)
        first_use.add("tc1")
        first_result.add("tr1")
        second_use, second_result = svc._capture_dedup_sets(state)

        assert second_use is first_use and second_result is first_result

    def test_a_broken_pool_lookup_does_not_break_the_capture(
            self, captured, monkeypatch):
        svc, state, _written, _published = captured

        class _Pool:
            @staticmethod
            def instance():
                raise RuntimeError("pool unavailable")

        import core.claude_code_interactive_pool as pool_mod
        monkeypatch.setattr(pool_mod, "InteractiveClaudeCodePool", _Pool)

        use_ids, result_ids = svc._capture_dedup_sets(state)

        assert use_ids is state.emitted_tool_use_ids
        assert result_ids is state.emitted_tool_result_ids

    def test_the_coordinator_actually_receives_them(self):
        import inspect
        src = inspect.getsource(CCInteractiveEventService._run_manual_capture)
        assert "emitted_tool_use_ids=_use_ids" in src
        assert "emitted_tool_result_ids=_result_ids" in src


class _Resp:
    def __init__(self, model="opus", tokens_in=12, tokens_out=34):
        self.content = "hi"
        self.model = model
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


class TestTheMetaLineUnderACapturedTurn:
    """A captured message must end up carrying the same facts as any other.

    The turn is persisted while it is written, so the model and the token
    counts do not exist yet at that moment. They arrive when the coordinator
    returns, and the message has to be updated then -- a meta line that stays
    half-empty reads as a turn that cost nothing.
    """

    def test_the_source_carries_the_provider(self, captured):
        """With no provider the client renders no meta line at all."""
        svc, state, written, _published = captured
        _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)

        block_cb("text", {"text": "Hello"})

        assert written[0][0]["source"]["provider"] == state.provider

    def test_the_real_numbers_update_the_message_that_was_written(
            self, captured):
        svc, state, written, published = captured
        _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)
        block_cb("text", {"text": "Hello"})
        written_id = written[0][0]["msg_id"]

        svc._publish_capture_meta(state, _Resp())

        metas = [d for _c, kind, d in published if kind == "message_meta"]
        assert len(metas) == 1
        assert metas[0]["msg_id"] == written_id
        assert metas[0]["model"] == "opus"
        assert (metas[0]["tokens_in"], metas[0]["tokens_out"]) == (12, 34)

    def test_the_last_block_is_the_one_labelled(self, captured):
        """The numbers describe the turn, and the answer ends it."""
        svc, state, written, published = captured
        _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)
        block_cb("text", {"text": "one"})
        block_cb("text", {"text": "two"})

        svc._publish_capture_meta(state, _Resp())

        metas = [d for _c, kind, d in published if kind == "message_meta"]
        assert metas[0]["msg_id"] == written[1][0]["msg_id"]

    def test_nothing_measured_means_nothing_claimed(self, captured):
        """Better no meta line than one asserting zeroes nobody measured."""
        svc, state, written, published = captured
        _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)
        block_cb("text", {"text": "Hello"})

        svc._publish_capture_meta(state, _Resp(model="", tokens_in=0,
                                               tokens_out=0))

        assert not [d for _c, kind, d in published if kind == "message_meta"]

    def test_a_capture_that_wrote_nothing_publishes_nothing(self, captured):
        svc, state, _written, published = captured

        svc._publish_capture_meta(state, _Resp())

        assert not [d for _c, kind, d in published if kind == "message_meta"]

    def test_a_broken_bus_never_fails_the_captured_turn(self, captured,
                                                        monkeypatch):
        """This closes a display gap; it must not cost the answer itself."""
        svc, state, _written, _published = captured
        _text_cb, block_cb, _final_cb = svc._capture_stream_callbacks(state)
        block_cb("text", {"text": "Hello"})

        class _Broken:
            @staticmethod
            def instance():
                raise RuntimeError("bus down")

        import core.conversation_event_bus as bus
        monkeypatch.setattr(bus, "ConversationEventBus", _Broken)

        svc._publish_capture_meta(state, _Resp())

    def test_a_new_capture_does_not_label_the_previous_turns_message(self):
        """The id list is reset when a capture starts, not left to accumulate."""
        import inspect
        src = inspect.getsource(CCInteractiveEventService._run_manual_capture)
        assert src.index("captured_msg_ids = []") < src.index("coord.run()")
