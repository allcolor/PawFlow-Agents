"""Tests for tool metrics slash command and action."""

import json

from core import FlowFile
from core.tool_registry import ToolRegistry
from tasks.ai.actions.command_dispatch import _parse_command
from tasks.ai.actions.tools_exec import _handle_tools_exec


def test_tool_metrics_slash_command_parses_to_action():
    body = _parse_command("/tool-metrics", "conv1", "user1", "agent1")

    assert body["action"] == "tool_metrics"
    assert body["conversation_id"] == "conv1"


def test_toolmetrics_alias_parses_to_action():
    body = _parse_command("/toolmetrics", "conv1", "user1", "agent1")

    assert body["action"] == "tool_metrics"
    assert body["conversation_id"] == "conv1"


def test_tool_metrics_action_returns_metrics_snapshot():
    ToolRegistry.reset_metrics()
    ToolRegistry._record_metric("read", True, 12.5)
    ToolRegistry._record_metric("bash", False, 2.0, error="Error: denied")

    ff = FlowFile(content=b"")
    result = _handle_tools_exec(object(), "tool_metrics", {}, None, "user1", ff)

    assert result == [ff]
    payload = json.loads(ff.get_content().decode("utf-8"))
    assert "Tool metrics" in payload["output"]
    assert "read: calls=1 ok=1 errors=0" in payload["output"]
    assert "bash: calls=1 ok=0 errors=1" in payload["output"]
    assert payload["metrics"]["bash"]["last_error"] == "Error: denied"
