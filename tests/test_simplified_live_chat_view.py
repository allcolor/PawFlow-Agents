"""Contract tests for the conversation-scoped simplified live chat view."""
from pathlib import Path

from chat_ui_testing import rendered_chat_html
import json
import logging

from core.conversation_store import ConversationStore
from tasks.ai.agent_emitter import StreamEmitter
from tasks.ai.agent_loop import AgentLoopTask


def _final_ids(rows):
    """msg_ids the simplified view will lift out of the activity block."""
    return [r.get("msg_id") for r in rows if r.get("turn_final")]


def _finals_per_turn(rows):
    counts = {}
    for row in rows:
        if row.get("turn_final"):
            counts[row.get("turn_id")] = counts.get(row.get("turn_id"), 0) + 1
    return counts


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"


class _Bus:
    def __init__(self):
        self.events = []

    def publish_event(self, cid, event_type, data):
        self.events.append((cid, event_type, data))


# ── Turn identity has to reach the turn in the first place ──
#
# Everything below the emitter keys off ctx["request_msg_id"], which comes from
# the flowfile attribute agent.request_msg_id. If the submitting path does not
# set it, no row and no event carries a turn_id and the simplified view has
# nothing to group -- it renders a classic transcript while reporting itself as
# simplified.


def test_web_chat_submission_carries_its_user_message_id_as_the_turn_id():
    from core import FlowFile
    from tasks.ai.agent_streaming import stamp_turn_identity

    flowfile = FlowFile(content=b"")

    stamped = stamp_turn_identity(flowfile, "user-1")

    assert stamped == "user-1"
    assert flowfile.get_attribute("agent.request_msg_id") == "user-1"
    assert flowfile.get_attribute("_user_msg_id") == "user-1"


def test_a_turn_id_the_caller_already_chose_is_never_overridden():
    from core import FlowFile
    from tasks.ai.agent_streaming import stamp_turn_identity

    flowfile = FlowFile(content=b"")
    flowfile.set_attribute("agent.request_msg_id", "runtime-api-turn")

    stamped = stamp_turn_identity(flowfile, "user-1")

    assert stamped == "runtime-api-turn"


def test_a_submission_without_a_message_id_invents_no_turn_id():
    from core import FlowFile
    from tasks.ai.agent_streaming import stamp_turn_identity

    flowfile = FlowFile(content=b"")

    assert stamp_turn_identity(flowfile, "") == ""
    assert not (flowfile.get_attribute("agent.request_msg_id") or "")


def test_the_context_reads_the_attribute_the_submission_writes():
    # The bug was two names for one thing: the submission wrote _user_msg_id
    # while the context read agent.request_msg_id. Pin the seam so a rename on
    # either side fails here instead of silently emptying every turn id.
    from core import FlowFile
    from tasks.ai.agent_streaming import stamp_turn_identity

    flowfile = FlowFile(content=b"")
    stamp_turn_identity(flowfile, "user-1")

    class _St:
        pass

    st = _St()
    st.flowfile = flowfile
    ctx_value = st.flowfile.get_attribute("agent.request_msg_id") or ""

    emitter = StreamEmitter(
        "conv-1", _Bus(), {"request_msg_id": ctx_value,
                          "active_agent_name": "assistant"},
        agent=None, gen_key="conv-1", generation=1)
    emitter._emit("tool_call", {"tool": "read"})

    assert emitter.bus.events[0][2]["turn_id"] == "user-1"


def test_stream_emitter_stamps_turn_correlation_on_live_events():
    bus = _Bus()
    emitter = StreamEmitter(
        "conv-1", bus,
        {"request_msg_id": "user-1",
         "active_agent_name": "assistant"},
        agent=None, gen_key="conv-1", generation=1)

    emitter._emit("thinking", {"detail": "working"})

    data = bus.events[0][2]
    assert data["turn_id"] == "user-1"
    assert data["request_msg_id"] == "user-1"


def test_display_classification_preserves_explicit_turn_metadata():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "assistant", "content": "answer",
         "msg_id": "final-1", "turn_id": "user-1",
         "turn_final": True},
        {"role": "tool_call", "content": "", "msg_id": "call-1",
         "tool_call_id": "tc-1", "tool_name": "read",
         "arguments": {"path": "a.py"}, "turn_id": "user-1",
         "turn_final": False},
        {"role": "tool", "content": "ok", "msg_id": "result-1",
         "tool_call_id": "tc-1", "turn_id": "user-1",
         "turn_final": False},
    ])

    by_id = {row.get("msg_id"): row for row in rows}
    assert by_id["final-1"]["turn_id"] == "user-1"
    assert by_id["final-1"]["turn_final"] is True
    assert by_id["call-1"]["turn_id"] == "user-1"
    assert by_id["result-1"]["turn_id"] == "user-1"


def test_display_classification_derives_legacy_turn_at_user_boundary_only():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "assistant", "content": "orphan", "msg_id": "old-1"},
        {"role": "user", "content": "first", "msg_id": "user-1"},
        {"role": "assistant", "content": "answer one", "msg_id": "a-1"},
        {"role": "user", "content": "second", "msg_id": "user-2"},
        {"role": "assistant", "content": "answer two", "msg_id": "a-2"},
    ])

    by_id = {row.get("msg_id"): row for row in rows}
    assert "turn_id" not in by_id["old-1"]
    assert by_id["a-1"]["turn_id"] == "user-1"
    assert by_id["a-2"]["turn_id"] == "user-2"


# ── Reconstruction: every turn must expose exactly one final answer ──
#
# The view lifts the row carrying turn_final out of the activity block and
# renders it as the standalone answer. A turn with no such row renders as a
# user message followed by a collapsed block that hides the answer.


def test_legacy_turns_without_stored_metadata_still_expose_their_answer():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "first", "msg_id": "user-1"},
        {"role": "assistant", "content": "answer one", "msg_id": "a-1"},
        {"role": "user", "content": "second", "msg_id": "user-2"},
        {"role": "assistant", "content": "answer two", "msg_id": "a-2"},
    ])

    assert _final_ids(rows) == ["a-1", "a-2"]
    assert _finals_per_turn(rows) == {"user-1": 1, "user-2": 1}


def test_derived_final_is_the_last_assistant_row_not_an_intermediate_one():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "assistant", "content": "let me look", "msg_id": "a-1"},
        {"role": "tool", "content": "ok", "msg_id": "result-1",
         "tool_call_id": "tc-1"},
        {"role": "assistant", "content": "here it is", "msg_id": "a-2"},
    ])

    assert _final_ids(rows) == ["a-2"]
    by_id = {r.get("msg_id"): r for r in rows}
    assert not by_id["a-1"].get("turn_final")


def test_stored_turn_final_wins_and_is_never_doubled_by_derivation():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "assistant", "content": "the answer", "msg_id": "a-1",
         "turn_id": "user-1", "turn_final": True},
        {"role": "assistant", "content": "a later aside", "msg_id": "a-2",
         "turn_id": "user-1", "turn_final": False},
    ])

    assert _final_ids(rows) == ["a-1"]
    by_id = {r.get("msg_id"): r for r in rows}
    assert "turn_final_derived" not in by_id["a-1"]


def test_derivation_marks_itself_so_a_missed_patch_stays_diagnosable():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "assistant", "content": "answer", "msg_id": "a-1",
         "turn_id": "user-1"},
    ])

    by_id = {r.get("msg_id"): r for r in rows}
    assert by_id["a-1"]["turn_final"] is True
    assert by_id["a-1"]["turn_final_derived"] is True


def test_authoritative_active_turn_never_derives_a_final_from_partial_output():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-live"},
        {"role": "assistant", "content": "partial", "msg_id": "a-live",
         "turn_id": "user-live"},
    ], active_turn_ids={"user-live"})

    assert not rows[-1].get("turn_final")


def test_turn_without_a_visible_answer_never_manufactures_one():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "tool_call", "content": "", "msg_id": "call-1",
         "tool_call_id": "tc-1", "tool_name": "read",
         "arguments": {"path": "a.py"}},
        {"role": "tool", "content": "ok", "msg_id": "result-1",
         "tool_call_id": "tc-1"},
    ])

    assert _final_ids(rows) == []


def test_error_row_is_not_promoted_to_the_turn_answer():
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "assistant", "content": "partial answer", "msg_id": "a-1"},
        {"role": "assistant", "content": "boom", "msg_id": "e-1",
         "is_error": True},
    ])

    assert _final_ids(rows) == ["a-1"]


def test_no_tool_is_hidden_from_the_transcript():
    """A tool the model ran always has a row, `get_tool_schema` included.

    It used to be filtered out of the display along with its result, so a
    reload showed a turn with fewer tools than the model had actually run.
    The result keeps its name too — a nameless output attached to no call is
    exactly what the user saw before.
    """
    rows = AgentLoopTask._classify_messages_for_display([
        {"role": "user", "content": "go", "msg_id": "user-1"},
        {"role": "tool_call", "content": "", "msg_id": "call-1",
         "tool_call_id": "tc-1", "tool_name": "get_tool_schema",
         "arguments": {}},
        {"role": "tool", "content": "schema", "msg_id": "result-1",
         "tool_call_id": "tc-1"},
    ])

    named = [(r.get("type"), r.get("tool_name")) for r in rows]
    assert ("tool_call", "get_tool_schema") in named
    assert ("tool_result", "get_tool_schema") in named


def test_every_turn_with_an_answer_gets_exactly_one_final_across_a_mixed_history():
    rows = AgentLoopTask._classify_messages_for_display([
        # legacy turn: nothing stored
        {"role": "user", "content": "one", "msg_id": "user-1"},
        {"role": "assistant", "content": "answer one", "msg_id": "a-1"},
        # modern turn: patch landed
        {"role": "user", "content": "two", "msg_id": "user-2"},
        {"role": "assistant", "content": "work", "msg_id": "a-2",
         "turn_id": "user-2", "turn_final": False},
        {"role": "assistant", "content": "answer two", "msg_id": "a-3",
         "turn_id": "user-2", "turn_final": True},
        # modern turn: patch was lost
        {"role": "user", "content": "three", "msg_id": "user-3"},
        {"role": "assistant", "content": "answer three", "msg_id": "a-4",
         "turn_id": "user-3", "turn_final": False},
    ])

    assert _finals_per_turn(rows) == {"user-1": 1, "user-2": 1, "user-3": 1}
    assert _final_ids(rows) == ["a-1", "a-3", "a-4"]


def test_patch_message_warns_when_it_matches_no_row(tmp_path, caplog):
    ConversationStore.reset()
    try:
        store = ConversationStore(store_dir=str(tmp_path / "conversations"))
        cid = store.generate_id()
        store.save(cid, [], user_id="testuser")
        store.append_message(
            cid, {"role": "assistant", "content": "hi", "msg_id": "real-1"},
            user_id="testuser")

        with caplog.at_level(logging.WARNING):
            store.patch_message(cid, "does-not-exist", turn_final=True)
        assert "matched no row" in caplog.text

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            store.patch_message(cid, "real-1", turn_final=True)
        assert "matched no row" not in caplog.text
    finally:
        ConversationStore.reset()


def test_turn_view_module_loads_between_renderer_and_sse_handlers():
    source = (ROOT / "tasks" / "io" / "serve_chat_ui.py").read_text(encoding="utf-8")
    assert source.index('"messages_markdown.js"') < source.index('"turn_view.js"')
    assert source.index('"turn_view.js"') < source.index('"sse_state.js"')


def test_simplified_view_owns_accessible_tabs_and_terminal_handoff():
    source = (CHAT_UI / "turn_view.js").read_text(encoding="utf-8")
    for contract in (
        "function turnViewSetMode(mode)",
        "function turnViewRegisterUser(extra, element)",
        "function turnViewIngest(kind, data, element)",
        "function turnViewFinalize(data)",
        "function turnViewFail(turnId, status, message)",
        "function turnViewReconcile()",
        "setAttribute('role', 'tablist')",
        "setAttribute('role', 'tab')",
        "setAttribute('role', 'tabpanel')",
        "s.blockEl.parentNode.insertBefore(s.finalEl, s.blockEl.nextSibling)",
    ):
        assert contract in source


def test_simplified_view_forces_delegate_boxes_when_classic_grouping_is_off():
    """The delegate box is simplified mode's only sub-agent renderer.

    A classic-mode `false` must not follow the user into simplified: the
    toggle lives in viewClassicOptions, which that mode hides, so the box
    would be gone with no way to bring it back.
    """
    source = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    contract = (
        "setDelegateMessageGrouping(viewMode === 'simplified' ? true : "
        "groupDelegateMessages)"
    )
    assert contract in source


def test_show_file_artifact_parser_is_strict_and_dedupes_by_file_id():
    markdown = (CHAT_UI / "messages_markdown.js").read_text(encoding="utf-8")
    controller = (CHAT_UI / "turn_view.js").read_text(encoding="utf-8")
    assert "function parseShowFileArtifact(resultText, toolName)" in markdown
    assert "toLowerCase() !== 'show_file'" in markdown
    assert "parsed.__show_file__ !== true" in markdown
    assert "match[1] !== fileId" in markdown
    assert "artifactElementsByFileId.get(artifact.file_id)" in controller
    assert "artifactFileIdByCallId.set(tcId, artifact.file_id)" in controller


def test_view_mode_selector_and_locale_key_parity():
    template = rendered_chat_html()
    conversations = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    assert 'id="viewItemClassic"' in template
    assert 'id="viewItemSimplified"' in template
    assert "key: 'chat.view_mode'" in conversations
    assert "resumeConv(conversationId, true)" in conversations
    catalogs = [json.loads((CHAT_UI / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
                for lang in ("en", "fr", "es")]
    assert set(catalogs[0]) == set(catalogs[1]) == set(catalogs[2])
    required = {
        "classicView", "simplifiedView", "turnMessages", "turnThinking",
        "turnToolCalls", "turnArtifacts", "turnCallingTool",
        "expandTurnDetails", "collapseTurnDetails",
    }
    assert required <= catalogs[0].keys()


def test_every_live_row_creator_hands_its_row_to_the_turn_view():
    # Observed in the browser against beta.47: tool_result rows sat at top
    # level between the block and the next user message, and a user message
    # from another client landed inside the previous turn's Messages tab.
    # Top level must only ever be user / block / final answer.
    state = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    handlers_a = (CHAT_UI / "sse_handlers_a.js").read_text(encoding="utf-8")
    handlers_b = (CHAT_UI / "sse_handlers_b.js").read_text(encoding="utf-8")
    # A result whose tool call never arrived is rendered standalone after a
    # 750ms grace period -- that row was the one nobody handed over.
    fallback = state.split("function _queueUnmatchedToolResult", 1)[1]
    assert "turnViewIngest('tool_result', pending.data, row)" in fallback
    # A live user message opens a turn; it is never turn content.
    assert "if (data.role === 'user') {" in handlers_a
    assert "turnViewRegisterUser(data, el)" in handlers_a
    # Standalone assistant narration, with no delegate or task frame to hold it.
    assert "turnViewIngest('assistant', data, dEl)" in handlers_a
    assert "turnViewIngest('assistant', data, rEl)" in handlers_b


def test_turn_block_cannot_be_squeezed_by_the_message_column():
    # .messages is a scrolling column flex container. A flex item whose overflow
    # is not visible loses its automatic minimum size, so an overflowing
    # transcript shrinks this block to its padding: a bare bar with no readable
    # header and no reachable click target. It must opt out of shrinking.
    template = rendered_chat_html()
    columns = [chunk.split("}", 1)[0] for chunk in template.split(".messages {")[1:]]
    assert any("display: flex; flex-direction: column" in rule for rule in columns)
    rule = template.split(".msg.simple-turn-block {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in rule
    assert "flex: none" in rule
    assert "padding: 0" in rule
    # The .msg padding is declared after this rule, so equal specificity loses.
    assert ".msg.simple-turn-block {" in template
    # A reloaded transcript is all finished turns: no empty animation band.
    assert ".simple-turn-block:not(.turn-working) .simple-turn-ephemeral { display: none; }" in template
    # _turnSvg emits a viewBox and no dimensions: unsized, one icon fills its
    # whole tab column and pushes the label out of the block.
    assert "function _turnSvg(kind)" in (CHAT_UI / "turn_view.js").read_text(encoding="utf-8")
    assert ".simple-turn-tab svg { width: 16px; height: 16px; flex: none; }" in template
