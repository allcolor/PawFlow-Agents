"""Tests for direct Scratchpad webchat actions."""

import json

import core.paths as paths
from core.scratchpad_store import ScratchpadStore
from tasks.ai.actions.cognitive_ui import _handle_cognitive_ui


class _FlowFile:
    def __init__(self):
        self.content = b""

    def set_content(self, content):
        self.content = content

    def payload(self):
        return json.loads(self.content)


def test_scratchpad_ui_crud_is_scoped_to_selected_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SCRATCHPADS_DIR", tmp_path / "scratchpads")
    ScratchpadStore._instance = None
    flowfile = _FlowFile()
    base = {"conversation_id": "conv", "agent_name": "assistant"}
    try:
        _handle_cognitive_ui(
            None, "scratchpad_save", {
                **base, "topic": "Resume", "content": "Run tests",
                "ttl_hours": 24,
            }, None, "alice", flowfile)
        note = flowfile.payload()
        assert note["topic"] == "Resume"

        _handle_cognitive_ui(
            None, "scratchpad_list", {**base, "query": "tests"},
            None, "alice", flowfile)
        assert flowfile.payload()["notes"][0]["id"] == note["id"]

        _handle_cognitive_ui(
            None, "scratchpad_list", {
                "conversation_id": "conv", "agent_name": "other"},
            None, "alice", flowfile)
        assert flowfile.payload()["notes"] == []

        _handle_cognitive_ui(
            None, "scratchpad_delete", {**base, "note_id": note["id"]},
            None, "alice", flowfile)
        assert flowfile.payload()["deleted"] is True
    finally:
        ScratchpadStore._instance = None
