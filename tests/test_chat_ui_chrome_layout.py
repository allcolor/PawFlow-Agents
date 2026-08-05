"""Structural assertions for the simplified chat chrome."""

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


def test_account_actions_share_one_split_control_at_the_right():
    header_actions = _between('<div class="actions">', '<!-- Chat tab content -->')
    account = header_actions[
        header_actions.index('id="accountMenuWrap"'):
        header_actions.index('</details>', header_actions.index('id="accountMenuWrap"'))
    ]

    assert 'class="account-menu-wrap"' in account
    assert 'id="linkAccountBtn"' in account
    assert 'id="logoutBtn"' in account
    assert account.index('id="linkAccountBtn"') < account.index('id="logoutBtn"')


def test_view_audio_and_permissions_live_in_prompt_controls_not_header():
    header = _between('<div class="header">', '<!-- Chat tab content -->')
    composer = _between('<div class="composer-context-controls"', '<div class="attachments-preview"')

    for element_id in ("speakToggleBtn", "viewMenuWrap", "permissionMode"):
        marker = f'id="{element_id}"'
        assert marker in composer
        assert marker not in header


def test_conversation_actions_are_an_always_visible_right_dock():
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')

    assert 'id="actionMenuWrap"' in dock
    assert 'id="actionMenu"' in dock
    assert 'id="adminSettingsWrap"' in dock
    assert 'action-dock-menu' in dock
    assert 'action-menu-btn' not in dock
    assert ".action-dock-menu { display: flex; position: fixed;" in TEMPLATE_HTML


def test_action_and_task_docks_have_distinct_right_offsets():
    assert ".action-dock-menu { display: flex; position: fixed;" in TEMPLATE_HTML
    assert ".task-tab-dock { position: fixed;" in TEMPLATE_HTML
    assert "right: 42px" in TEMPLATE_HTML
