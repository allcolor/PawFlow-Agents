"""Contract tests for the conversation-scoped simplified live chat view."""
from pathlib import Path
import json

from tasks.ai.agent_emitter import StreamEmitter
from tasks.ai.agent_loop import AgentLoopTask


ROOT = Path(__file__).resolve().parents[1]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"


class _Bus:
    def __init__(self):
        self.events = []

    def publish_event(self, cid, event_type, data):
        self.events.append((cid, event_type, data))


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
        "state.blockEl.parentNode.insertBefore(state.finalEl, state.blockEl.nextSibling)",
    ):
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
    template = (CHAT_UI / "template.html").read_text(encoding="utf-8")
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
