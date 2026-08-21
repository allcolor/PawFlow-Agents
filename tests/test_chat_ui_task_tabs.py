from pathlib import Path

from chat_ui_testing import rendered_chat_html

CHAT_UI = Path("tasks/io/chat_ui")


def test_task_tabs_module_registered_and_loads_after_its_deps():
    src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    assert '"task_tabs.js"' in src
    assert (CHAT_UI / "task_tabs.js").exists()

    # Load order only matters for the handful of things task_tabs.js calls
    # at its own top level (none) vs. what other <script defer> modules call
    # into it via onclick (any order works). Still assert active_agents.js
    # (the row it hooks a button into) appears before it, matching the
    # module's own doc comment about being paired with it.
    idx_active = src.index('"active_agents.js"')
    idx_task_tabs = src.index('"task_tabs.js"')
    assert idx_active < idx_task_tabs


def test_task_tabs_panel_and_dock_present_in_template():
    html = rendered_chat_html()
    assert 'id="taskTabDock"' in html
    assert 'id="taskTabPanel"' in html
    assert 'id="taskTabPanelTitle"' in html
    assert 'id="taskTabPanelBody"' in html
    assert 'onclick="closeActiveTaskTab()"' in html
    assert ".task-tab-panel.open" in html
    assert ".task-tab-dock" in html


def test_task_tabs_js_exposes_expected_api():
    src = (CHAT_UI / "task_tabs.js").read_text(encoding="utf-8")
    for fn in [
        "function openTaskTab(",
        "function closeTaskTab(",
        "function closeActiveTaskTab(",
        "function switchTaskTab(",
    ]:
        assert fn in src
    assert "window._taskTabsReset" in src
    # Filters strictly on direct children so grouped (details) and
    # ungrouped (loose .msg) rendering both yield exactly one match set.
    assert ":scope > [data-task-id=" in src


def test_add_msg_and_task_block_tag_dataset_task_id():
    render_src = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")
    assert "el.dataset.taskId = _msgTaskId" in render_src

    sse_state_src = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    assert "details.dataset.taskId = taskId" in sse_state_src
    assert "openTaskTab(" in sse_state_src


def test_active_agents_panel_can_open_a_task_tab():
    src = (CHAT_UI / "active_agents.js").read_text(encoding="utf-8")
    assert "openTaskTab(" in src
    assert "info.taskId ?" in src


def test_conversation_switch_resets_task_tabs():
    src = (CHAT_UI / "conversations.js").read_text(encoding="utf-8")
    assert "window._taskTabsReset" in src


def test_task_tab_i18n_keys_present_in_every_locale():
    import json

    required = {
        "openInTaskTab",
        "closeTaskTabTitle",
        "taskTabEmpty",
        "taskTabPanelTitle",
        "taskTabsTitle",
    }
    for locale_file in (CHAT_UI / "i18n").glob("*.json"):
        if locale_file.name == "languages.json":
            continue
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        missing = required - data.keys()
        assert not missing, f"{locale_file.name} missing {missing}"
