"""Source invariants for Project Graph, Project Wiki and Scratchpad panels."""

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


def test_action_menu_and_commands_expose_all_three_panels():
    template = _text("tasks/io/chat_ui/template.html")
    commands = _text("tasks/io/chat_ui/commands.js")
    assert "cmdShowProjectGraph()" in template
    assert "cmdShowProjectWiki()" in template
    assert "cmdShowScratchpad()" in template
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


def test_cognitive_panel_files_stay_below_split_limit():
    for relative in (
        "tasks/io/chat_ui/project_graph.js",
        "tasks/io/chat_ui/project_wiki.js",
        "tasks/io/chat_ui/scratchpad.js",
        "tasks/ai/_agentctx_p3.py",
    ):
        assert len(_text(relative).splitlines()) <= 800, relative
