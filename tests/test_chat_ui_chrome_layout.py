"""Structural assertions for the simplified chat chrome."""

import json
from pathlib import Path


TEMPLATE_HTML = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    start_index = TEMPLATE_HTML.index(start)
    return TEMPLATE_HTML[start_index:TEMPLATE_HTML.index(end, start_index)]


def test_theme_and_language_sit_with_status_and_usage_before_right_header_actions():
    header = _between('<div class="header">', '<!-- Chat tab content -->')

    usage_index = header.index('id="usageCostBadge"')
    theme_index = header.index('id="themeSelect"')
    language_index = header.index('id="languageSelect"')
    actions_index = header.index('<div class="actions">')

    assert usage_index < theme_index < language_index < actions_index


def test_account_actions_live_in_right_dock_not_header():
    header = _between('<div class="header">', '<!-- Chat tab content -->')
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")

    for element_id in ("linkAccountBtn", "logoutBtn"):
        marker = f'id="{element_id}"'
        assert marker in dock
        assert marker not in header[:header.index('id="actionMenuWrap"')]

    assert 'id="accountMenuWrap"' not in TEMPLATE_HTML
    assert "_setText('#logoutBtn'" not in i18n_js


def test_language_selector_renders_catalog_flags():
    languages = json.loads(
        Path("tasks/io/chat_ui/i18n/languages.json").read_text(encoding="utf-8")
    )
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")

    assert all(language.get("flag") for language in languages)
    assert "lang.flag" in i18n_js


def test_view_audio_permissions_and_dictation_live_in_left_prompt_dock():
    header = _between('<div class="header">', '<!-- Chat tab content -->')
    controls = _between(
        '<div class="prompt-controls-panel"', '<div class="active-panel"'
    )

    for element_id in (
        "permissionMode", "speakToggleBtn", "speechInputBtn", "viewMenuWrap"
    ):
        marker = f'id="{element_id}"'
        assert marker in controls
        assert marker not in header

    assert 'class="composer-context-controls"' not in TEMPLATE_HTML
    assert ".prompt-controls-panel {" in TEMPLATE_HTML
    assert "bottom: 70px; left: 20px" in TEMPLATE_HTML


def test_conversation_actions_are_an_always_visible_right_dock():
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')
    admin_js = Path("tasks/io/chat_ui/admin_settings.js").read_text(encoding="utf-8")

    assert 'id="actionMenuWrap"' in dock
    assert 'id="actionMenu"' in dock
    assert 'id="adminSettingsBtn"' in dock
    assert 'id="adminSettingsMenu"' in dock
    assert 'id="adminSettingsWrap"' not in dock
    assert dock.index('data-pf-slot="header_actions_ext"') < dock.index('id="adminSettingsMenu"')
    assert 'action-dock-menu' in dock
    assert 'action-menu-btn' not in dock
    assert ".action-dock-menu { display: flex; position: fixed;" in TEMPLATE_HTML
    assert "#adminSettingsMenu { position: fixed;" in TEMPLATE_HTML
    assert "button.contains(e.target)" in admin_js
    assert "menu.contains(e.target)" in admin_js


def test_action_and_task_docks_have_distinct_right_offsets():
    assert ".action-dock-menu { display: flex; position: fixed;" in TEMPLATE_HTML
    assert ".task-tab-dock { position: fixed;" in TEMPLATE_HTML
    assert "right: 42px" in TEMPLATE_HTML
