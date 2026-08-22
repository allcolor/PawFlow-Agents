"""Tests for the compact composer, memory cards, code blocks, and search."""

from pathlib import Path
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
    assert "4 passing" in result.stdout


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
