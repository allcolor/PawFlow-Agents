"""Contracts for effective cognitive-tool discovery and routing."""

from pathlib import Path

from core.tool_selection import build_tool_selection_hint
from core.handlers.diary import DiaryReadHandler, DiaryWriteHandler
from core.handlers.project_wiki import ProjectWikiHandler
from core.handlers.scratchpad import ScratchpadHandler
from core.handlers.todolist import TodoListHandler


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_hint_routes_every_cognitive_and_work_state_family():
    hint = build_tool_selection_hint({
        "remember", "semantic_recall", "kg_add", "diary_write", "diary_read",
        "todolist", "scratchpad", "project_graph", "project_wiki", "learn",
        "conversation_search", "read_history",
    })
    for tool in (
        "remember", "semantic_recall", "kg_add", "diary_write", "todolist",
        "scratchpad", "project_graph", "project_wiki", "learn",
        "conversation_search", "read_history",
    ):
        assert tool in hint
    assert "non-obvious decision or lesson" in hint
    assert "note bodies are never auto-injected" in hint
    assert "authoritative unfinished work" in hint


def test_context_builder_uses_the_canonical_hint():
    source = (ROOT / "tasks" / "ai" / "_agentctx_p3.py").read_text(
        encoding="utf-8")
    assert "from core.tool_selection import build_tool_selection_hint" in source
    assert "handler.name for handler in st.registry.list_tools()" in source
    assert 'st.system_prompt += "\\n\\n" + st._selection_hint' in source


def test_diary_descriptions_match_cross_agent_read_behavior():
    write_description = DiaryWriteHandler().description
    read_description = DiaryReadHandler().description
    assert "non-obvious decision" in write_description
    assert "Do not write routine turn summaries" in write_description
    assert "other agents cannot read" not in write_description
    assert "explicit agents list" in write_description
    assert "automatic diary digest" in read_description
    assert "do not call it mechanically" in read_description


def test_multi_action_work_tools_describe_every_parameter():
    for handler in (
        TodoListHandler(), ScratchpadHandler(), ProjectWikiHandler(),
    ):
        properties = handler.parameters_schema["properties"]
        assert properties["action"]["description"]
        assert all(value.get("description") for value in properties.values())
