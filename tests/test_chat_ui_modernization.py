"""Tests for the compact composer, memory cards, code blocks, and search."""

from pathlib import Path
import re
import subprocess

from chat_ui_testing import rendered_chat_html


ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "tasks" / "io" / "chat_ui"


def test_search_behavioural_js_suite():
    result = subprocess.run(
        ["node", "tests/js/search_spec.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passing" in result.stdout


def test_composer_picker_behavioural_js_suite():
    result = subprocess.run(
        ["node", "tests/js/composer_picker_spec.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passing" in result.stdout


def test_resource_dialogs_behavioural_js_suite():
    result = subprocess.run(
        ["node", "tests/js/resource_dialogs_spec.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passing" in result.stdout


def test_operation_progress_behavioural_js_suite():
    result = subprocess.run(
        ["node", "tests/js/progress_dialog_spec.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passing" in result.stdout


def test_dialogs_share_beautiful_ui_surfaces_buttons_and_tables():
    css = (UI / "css" / "99_theme_bridge.css").read_text(encoding="utf-8")

    for surface in (
        ".dialog",
        ".exec-dialog",
        ".cog-dialog",
        "#resourceEditorOverlay > div",
        ".pf-ext-modal-box",
    ):
        assert surface in css
    assert "cubic-bezier(.34, 1.56, .64, 1)" in css
    assert "table tbody tr:hover > td" in css
    assert "prefers-reduced-motion: reduce" in css
    assert 'body > [id$="Overlay"]' in css
    assert 'body > [id$="Dialog"]' in css


def test_dynamic_dialog_family_has_no_private_color_palette():
    modules = (
        "project_wiki.js", "project_graph.js", "knowledge_graph.js",
        "scratchpad.js", "scratchdir.js", "diary.js", "dialogs.js",
        "resources_resource_dialogs.js", "resources_service_dialogs.js",
    )
    literal = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\(")
    for module in modules:
        source = (UI / module).read_text(encoding="utf-8")
        assert not literal.search(source), module


def test_service_creation_choices_use_shared_dialog_buttons():
    source = (UI / "resources_service_templates.js").read_text(encoding="utf-8")
    css = (UI / "css" / "99_theme_bridge.css").read_text(encoding="utf-8")

    assert "resource-create-choice-dialog" in source
    assert 'class="dialog-choice-button"' in source
    assert 'class="dialog-choice-button btn-primary"' in source
    assert ".dialog-choice-button" in css


def test_search_overlay_and_unified_composer_are_rendered():
    html = rendered_chat_html()
    serve = (ROOT / "tasks" / "io" / "serve_chat_ui.py").read_text(
        encoding="utf-8"
    )
    command = (UI / "cmd_conversation.js").read_text(encoding="utf-8")

    assert 'id="conversationSearchDialog"' in html
    assert 'id="conversationSearchInput"' in html
    assert "onclick=\"showConversationSearch('')\"" in html
    assert 'class="input-row composer-shell"' in html
    assert 'id="composerPicker"' in html
    assert "openComposerPicker('slash')" in html
    assert "openComposerPicker('mention')" in html
    assert 'id="speechInputBtn"' in html
    assert 'id="grabBtn"' in html
    assert '"search.js"' in serve
    assert '"58_modern_ui.css"' in serve
    assert '"75_composer_shell.css"' in serve
    assert "showConversationSearch(query)" in command


def test_memory_panel_uses_themeable_records_without_inline_palette():
    source = (UI / "memories.js").read_text(encoding="utf-8")

    assert "className = 'memory-overlay'" in source
    assert 'class="memory-card' in source
    assert 'class="memory-scope' in source
    assert 'class="memory-edit-field"' in source
    for color in ("#1a1a2e", "#0d1117", "#c0c0d0", "#4fc3f7"):
        assert color not in source


def test_code_blocks_have_language_header_and_accessible_copy_action():
    source = (UI / "messages_markdown.js").read_text(encoding="utf-8")
    attachments = (UI / "attachments.js").read_text(encoding="utf-8")

    assert 'class="code-block-header"' in source
    assert 'class="code-block-language"' in source
    assert "escapeHtml(lang || 'code')" in source
    assert "btn.textContent = t('copied')" in attachments
