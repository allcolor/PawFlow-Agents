"""Tests for agent-scoped Diary webchat actions."""

import json
from unittest.mock import MagicMock

from tasks.ai.actions.cognitive_ui import _handle_cognitive_ui


class _FlowFile:
    def __init__(self):
        self.content = b""

    def set_content(self, content):
        self.content = content

    def payload(self):
        return json.loads(self.content)


def test_diary_list_targets_requested_agent(monkeypatch):
    diary = MagicMock()
    diary.read.return_value = [{"id": "one", "text": "note"}]
    monkeypatch.setattr("core.agent_diary.AgentDiary.instance", lambda: diary)
    flowfile = _FlowFile()

    _handle_cognitive_ui(None, "diary_list", {
        "agent_name": "agent-b", "limit": 10, "type": "decision",
    }, None, "alice", flowfile)

    assert flowfile.payload()["count"] == 1
    diary.read.assert_called_once_with(
        "alice", "agent-b", limit=10, entry_type="decision")


def test_diary_add_requires_and_targets_agent(monkeypatch):
    flowfile = _FlowFile()
    _handle_cognitive_ui(
        None, "diary_add", {"text": "note"}, None, "alice", flowfile)
    assert flowfile.payload() == {"error": "Missing agent_name"}

    diary = MagicMock()
    diary.write.return_value = {"id": "one", "text": "note"}
    monkeypatch.setattr("core.agent_diary.AgentDiary.instance", lambda: diary)
    _handle_cognitive_ui(None, "diary_add", {
        "agent_name": "agent-b", "text": "note", "type": "learning",
        "tags": ["ui"],
    }, None, "alice", flowfile)
    diary.write.assert_called_once_with(
        "alice", "agent-b", "note", entry_type="learning", tags=["ui"])
