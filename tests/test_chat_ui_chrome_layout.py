"""Structural assertions for the simplified chat chrome."""

import json
from pathlib import Path


TEMPLATE_HTML = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
TOOLTIPS_JS = Path("tasks/io/chat_ui/tooltips.js").read_text(encoding="utf-8")
ATTACHMENTS_JS = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
STATE_JS = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")


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


def test_account_actions_live_in_composer_dock_not_header():
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


def test_view_audio_permissions_and_dictation_live_in_left_prompt_panel():
    header = _between('<div class="header">', '<!-- Chat tab content -->')
    controls = _between(
        '<div class="prompt-controls-panel"', '<div class="composer-action-mount"'
    )

    for element_id in (
        "permissionMode", "speakToggleBtn", "speechInputBtn", "viewMenuWrap"
    ):
        marker = f'id="{element_id}"'
        assert marker in controls
        assert marker not in header

    assert 'class="composer-context-controls"' not in TEMPLATE_HTML
    assert ".prompt-controls-panel {" in TEMPLATE_HTML
    assert "position: fixed; bottom: 70px; left: 20px" not in TEMPLATE_HTML


def test_composer_context_row_orders_controls_action_dock_and_active_agents():
    input_area = _between('<div class="input-area">', '</div><!-- /tab-content chat -->')
    context_row = _between(
        '<div class="composer-context-row">', '<div class="input-row">'
    )

    assert 'id="promptControlsPanel"' in context_row
    assert 'id="composerActionMount"' in context_row
    assert 'id="composerActiveMount"' in context_row
    assert 'id="attachPreview"' not in context_row
    assert (
        context_row.index('id="promptControlsPanel"')
        < context_row.index('id="composerActionMount"')
        < context_row.index('id="composerActiveMount"')
    )
    assert "actionMount.appendChild(actionDock)" in STATE_JS
    assert "activeMount.appendChild(activePanel)" in STATE_JS
    assert context_row in input_area
    assert ".composer-context-row { display: grid;" in TEMPLATE_HTML


def test_conversation_actions_are_an_horizontal_composer_dock():
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
    assert ".action-dock-menu { display: flex; position: static;" in TEMPLATE_HTML
    assert "flex-direction: row;" in TEMPLATE_HTML
    assert "#adminSettingsMenu { position: absolute;" in TEMPLATE_HTML
    assert "button.contains(e.target)" in admin_js
    assert "menu.contains(e.target)" in admin_js


def test_control_docks_use_an_external_css_tooltip_without_horizontal_scroll():
    serve_src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')

    assert 'id="pfCssTooltip"' in TEMPLATE_HTML
    assert '.pf-css-tooltip {' in TEMPLATE_HTML
    assert 'overflow-x: auto; overflow-y: hidden;' in TEMPLATE_HTML
    assert '.action-menu-item:hover > div' not in TEMPLATE_HTML
    assert 'title="Server settings"' not in dock
    assert "_setTitle('#viewMenuToggle'" not in i18n_js
    assert '"tooltips.js"' in serve_src
    assert '.action-dock-menu > .action-menu-item' in TOOLTIPS_JS
    assert '.prompt-controls-row button' in TOOLTIPS_JS
    assert "getBoundingClientRect()" in TOOLTIPS_JS


def test_attachment_thumbnails_stack_before_send_and_expand_past_three():
    input_row = _between('<div class="input-row">', '</div>\n</div>\n</div><!-- /tab-content chat -->')

    assert input_row.index('id="input"') < input_row.index('id="attachPreview"')
    assert input_row.index('id="attachPreview"') < input_row.index('id="sendBtn"')
    assert ".attachments-preview { display: flex;" in TEMPLATE_HTML
    assert ".att-item + .att-item { margin-left:" in TEMPLATE_HTML
    assert "pendingFiles.slice(0, 3)" in ATTACHMENTS_JS
    assert "attachment-overflow-count" in ATTACHMENTS_JS
    assert "toggleAttachmentPreview" in ATTACHMENTS_JS


def test_task_dock_stays_fixed_while_action_dock_joins_composer():
    assert ".action-dock-menu { display: flex; position: static;" in TEMPLATE_HTML
    assert ".task-tab-dock { position: fixed;" in TEMPLATE_HTML
    assert ".action-dock { position: relative;" in TEMPLATE_HTML
