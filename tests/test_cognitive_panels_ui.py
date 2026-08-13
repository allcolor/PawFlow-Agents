"""Source invariants for cognitive webchat panels."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cognitive_panel_modules_load_after_shared_ui_helpers():
    source = _text("tasks/io/serve_chat_ui.py")
    graph = source.index('"project_graph.js"')
    wiki = source.index('"project_wiki.js"')
    scratchpad = source.index('"scratchpad.js"')
    assert source.index('"messages_markdown.js"') < graph < wiki < scratchpad


def test_action_menu_and_commands_expose_all_cognitive_panels():
    template = _text("tasks/io/chat_ui/template.html")
    commands = _text("tasks/io/chat_ui/commands.js")
    assert "cmdShowProjectGraph()" in template
    assert "cmdShowProjectWiki()" in template
    assert "cmdShowScratchpad()" in template
    assert "cmdShowDiary()" in template
    assert "'/graph'" in commands
    assert "'/wiki'" in commands
    assert "'/scratchpad'" in commands


def test_project_graph_panel_has_build_report_search_and_node_navigation():
    source = _text("tasks/io/chat_ui/project_graph.js")
    for action in (
        "project_graph_build",
        "project_graph_report",
        "project_graph_query",
        "project_graph_node",
    ):
        assert action in source
    assert 'id="pgSearchInput"' in source
    assert 'id="pgRelaySelect"' in source
    assert "pgReport();" in source
    assert "relay_id: _pgRelay" in source


def test_wiki_panel_has_search_edit_refresh_lint_and_delete_actions():
    source = _text("tasks/io/chat_ui/project_wiki.js")
    for action in (
        "project_wiki_pages",
        "project_wiki_query",
        "project_wiki_page",
        "project_wiki_save",
        "project_wiki_delete",
        "project_wiki_refresh",
        "project_wiki_lint",
    ):
        assert action in source
    assert "renderMarkdown(page.content" in source
    assert 'id="pwRelay"' in source
    assert "relay_id: _pwState.relay" in source


def test_scratchpad_panel_has_search_crud_ttl_and_clear_actions():
    source = _text("tasks/io/chat_ui/scratchpad.js")
    for action in (
        "scratchpad_list",
        "scratchpad_get",
        "scratchpad_save",
        "scratchpad_delete",
        "scratchpad_clear",
    ):
        assert action in source
    assert 'id="spTtl"' in source
    assert 'max="720"' in source
    assert 'id="spAgent"' in source
    assert source.count("agent_name: _spState.agent") == 5


def test_diary_and_memory_panels_use_conversation_agent_selectors():
    diary = _text("tasks/io/chat_ui/diary.js")
    memories = _text("tasks/io/chat_ui/memories.js")
    assert 'id="diaryAgent"' in diary
    assert "action$('diary_list'" in diary
    assert "action$('diary_add'" in diary
    assert "tool_name: 'diary_" not in diary
    assert 'id="memAgentFilter"' in memories
    assert '<select id="mem-edit-agent"' in memories
    assert '<select id="mem-new-agent"' in memories


def test_i18n_catalogs_have_identical_keys():
    catalogs = [json.loads(_text(f"tasks/io/chat_ui/i18n/{lang}.json"))
                for lang in ("en", "fr", "es")]
    assert set(catalogs[0]) == set(catalogs[1]) == set(catalogs[2])


def test_cognitive_panel_files_stay_below_split_limit():
    for relative in (
        "tasks/io/chat_ui/project_graph.js",
        "tasks/io/chat_ui/project_wiki.js",
        "tasks/io/chat_ui/scratchpad.js",
        "tasks/io/chat_ui/cognitive_panel_helpers.js",
        "tasks/ai/_agentctx_p3.py",
    ):
        assert len(_text(relative).splitlines()) <= 800, relative
