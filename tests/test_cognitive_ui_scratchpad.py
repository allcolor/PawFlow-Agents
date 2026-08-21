"""Tests for direct Scratchpad webchat actions."""

import json

from core import paths
from core.scratchpad_store import ScratchpadStore
from tasks.ai.actions import cognitive_ui
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


def test_scratchdir_ui_actions_use_authenticated_scope(monkeypatch):
    calls = []

    class _Manager:
        def __init__(self, service):
            assert service == "relay-service"

        def tree(self, user_id, conversation_id, agent_name, relay_id,
                 *, max_entries):
            calls.append((user_id, conversation_id, agent_name, relay_id,
                          max_entries))
            return {"status": "active", "entries": []}

    monkeypatch.setattr(cognitive_ui, "_resolve_project_graph",
                        lambda user_id, body: (None, "relay-service", False, "relay-1"))
    monkeypatch.setattr("core.scratchdir_manager.ScratchDirManager", _Manager)
    flowfile = _FlowFile()

    _handle_cognitive_ui(
        None, "scratchdir_tree", {
            "conversation_id": "conv", "agent_name": "assistant",
            "max_entries": 17,
        }, None, "alice", flowfile)

    assert flowfile.payload() == {"status": "active", "entries": []}
    assert calls == [("alice", "conv", "assistant", "relay-1", 17)]
