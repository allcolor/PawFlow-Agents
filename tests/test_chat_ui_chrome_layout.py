"""Structural assertions for the simplified chat chrome."""

import json
from pathlib import Path


TEMPLATE_HTML = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
TOOLTIPS_JS = Path("tasks/io/chat_ui/tooltips.js").read_text(encoding="utf-8")
TODOS_JS = Path("tasks/io/chat_ui/todos.js").read_text(encoding="utf-8")
ATTACHMENTS_JS = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
STATE_JS = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    start_index = TEMPLATE_HTML.index(start)
    return TEMPLATE_HTML[start_index:TEMPLATE_HTML.index(end, start_index)]


HEADER_OPEN = '<div class="header" id="headerBar">'


def test_theme_and_language_are_compact_controls_in_the_header():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    header_lead = header[:header.index('id="actionMenuWrap"')]
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')

    for element_id in ("themeSelect", "languageSelect"):
        marker = f'id="{element_id}"'
        assert marker in header_lead
        assert marker not in dock
    assert header_lead.count(
        'class="header-select-control header-dock-item"'
    ) == 2
    assert header_lead.count('class="header-icon-select"') == 2
    assert '&#x1F3A8;' in header_lead
    assert '&#x1F310;' in header_lead
    assert (
        header_lead.index('<h1 class="header-logo">')
        < header_lead.index('id="themeSelect"')
        < header_lead.index('id="languageSelect"')
        < header_lead.index('id="actionLoading"')
        < header_lead.index('id="activeAgentBadge"')
        < header_lead.index('id="usageCostBadge"')
        < header_lead.index('<div class="actions">')
    )


def test_theme_and_language_reuse_dock_tooltips_and_hover_zoom():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    header_controls = header[:header.index('id="actionMenuWrap"')]

    for control_id in ("themeSelectControl", "languageSelectControl"):
        control_index = header_controls.index(f'id="{control_id}"')
        start = header_controls.rindex("<label", 0, control_index)
        control = header_controls[start:header_controls.index("</label>", start)]
        assert "header-dock-item" in control
        assert "title=" not in control
        assert 'class="header-select-icon ami-icon"' in control
        assert 'class="ami-label"' in control
        assert 'class="ami-desc"' in control

    assert ".header-dock-item" in TOOLTIPS_JS
    assert "transform: scale(1.4);" in TEMPLATE_HTML


def test_account_actions_live_in_header_not_composer_dock_or_resource_tree():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")
    state_js = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")
    resources_js = Path("tasks/io/chat_ui/resources_render.js").read_text(encoding="utf-8")

    for element_id in ("linkAccountBtn", "userInfo", "logoutBtn"):
        marker = f'id="{element_id}"'
        assert marker in header[:header.index('id="actionMenuWrap"')]
        assert marker not in dock

    header_controls = header[:header.index('id="actionMenuWrap"')]
    assert (
        header_controls.index('id="linkAccountBtn"')
        < header_controls.index('id="userInfo"')
        < header_controls.index('id="logoutBtn"')
    )
    # The username is the LABEL of the linked-accounts button itself, not a
    # separate span beside it: userInfo must sit inside linkAccountBtn (after
    # its opening tag, before its closing </button>) so the header renders one
    # control [icon username] instead of [icon] [username].
    btn_end = header_controls.index('</button>', header_controls.index('id="linkAccountBtn"'))
    assert 'id="userInfo"' in header_controls[
        header_controls.index('id="linkAccountBtn"'):btn_end]
    assert "#linkAccountBtn { width: auto; min-width: 30px; padding: 0 8px; gap: 5px; }" in TEMPLATE_HTML
    assert "#linkAccountBtn .user-info" in TEMPLATE_HTML
    assert "window.PAWFLOW_EXTENSION_CONTEXT" in state_js
    assert "userInfo.textContent = activeUser" in state_js
    assert "userInfo.style.display = activeUser ? '' : 'none'" in state_js

    assert 'id="accountMenuWrap"' not in TEMPLATE_HTML
    assert "_setText('#logoutBtn'" not in i18n_js
    assert 'onclick="showLinkedAccountsDialog()"' in header
    assert "function showLinkedAccountsDialog()" in state_js
    assert "list_linked_accounts" in state_js
    assert "list_linked_accounts" not in resources_js
    assert "linkedAccounts" not in resources_js

    logout_start = header_controls.index('id="logoutBtn"')
    logout_end = header_controls.index("</button>", logout_start)
    logout_button = header_controls[logout_start:logout_end]
    assert "&#x23FB;" not in logout_button
    assert '<svg class="ami-icon"' in logout_button
    assert 'viewBox="0 0 24 24"' in logout_button
    assert "#logoutBtn svg.ami-icon" in TEMPLATE_HTML


def test_header_account_actions_reuse_dock_tooltip_and_hover_zoom():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    header_controls = header[:header.index('id="actionMenuWrap"')]

    for element_id in ("linkAccountBtn", "logoutBtn"):
        element_id_index = header_controls.index(f'id="{element_id}"')
        start = header_controls.rindex("<button", 0, element_id_index)
        button = header_controls[start:header_controls.index("</button>", start)]
        assert "header-dock-item" in button
        assert "title=" not in button
        assert 'class="ami-icon"' in button
        assert 'class="ami-label"' in button
        assert 'class="ami-desc"' in button

    assert ".header-dock-item" in TOOLTIPS_JS
    assert (
        ".action-dock-menu > .action-menu-item,\n.header-dock-item {"
        in TEMPLATE_HTML
    )
    assert (
        ".action-dock-menu > .action-menu-item:hover,\n.header-dock-item:hover {"
        in TEMPLATE_HTML
    )
    assert "transform: scale(1.4);" in TEMPLATE_HTML


def test_linked_account_dialog_keeps_identity_visible_beside_unlink_action():
    state_js = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")

    assert "linked-account-row" in state_js
    assert "linked-account-provider" in state_js
    assert "linked-account-value" in state_js
    assert "linked-account-unlink" in state_js
    assert ".linked-account-row {" in TEMPLATE_HTML
    assert ".linked-account-unlink {" in TEMPLATE_HTML
    assert "width: auto;" in TEMPLATE_HTML


def test_conversation_expiry_and_theme_open_from_the_context_menu():
    sidebar = _between(
        '<div class="sidebar collapsed"',
        '<div class="dialog-bg" id="conversationSettingsDialog"',
    )
    menu_js = Path("tasks/io/chat_ui/conversations_menu.js").read_text(encoding="utf-8")

    assert 'id="ttlSelect"' not in sidebar
    assert 'id="conversationThemeSelect"' not in sidebar
    assert 'id="conversationSettingsDialog"' in TEMPLATE_HTML
    assert 'id="ttlSelect"' in TEMPLATE_HTML
    assert 'id="conversationThemeSelect"' in TEMPLATE_HTML
    assert "#conversationSettingsDialog select { width: 100%;" in TEMPLATE_HTML
    assert "showConversationSettings(cid)" in menu_js
    assert "function showConversationSettings(cid)" in menu_js


def test_language_selector_renders_catalog_flags():
    languages = json.loads(
        Path("tasks/io/chat_ui/i18n/languages.json").read_text(encoding="utf-8")
    )
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")

    assert all(language.get("flag") for language in languages)
    assert "lang.flag" in i18n_js


def test_view_audio_permissions_and_dictation_live_in_left_prompt_panel():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
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


def test_composer_context_row_orders_controls_and_action_dock():
    input_area = _between('<div class="input-area">', '</div><!-- /tab-content chat -->')
    context_row = _between(
        '<div class="composer-context-row">', '<div class="input-row">'
    )

    assert 'id="promptControlsPanel"' in context_row
    assert 'id="composerActionMount"' in context_row
    # The active-agents box left the composer: it lives in the header
    # popover now (see test_header_chrome.py).
    assert 'id="composerActiveMount"' not in TEMPLATE_HTML
    assert 'id="attachPreview"' not in context_row
    assert (
        context_row.index('id="promptControlsPanel"')
        < context_row.index('id="composerActionMount"')
    )
    assert "actionMount.appendChild(actionDock)" in STATE_JS
    assert "activePop.appendChild(activePanel)" in STATE_JS
    assert context_row in input_area
    assert ".composer-context-row { display: grid;" in TEMPLATE_HTML
    assert (
        "grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);"
        in TEMPLATE_HTML
    )
    assert "grid-template-columns: auto minmax(0, 1fr) auto;" not in TEMPLATE_HTML
    assert ".prompt-controls-panel { justify-self: start; }" in TEMPLATE_HTML
    assert (
        ".input-area .composer-context-row { grid-template-columns: minmax(0, 1fr); }"
        in TEMPLATE_HTML
    )
    assert ".composer-context-row > .composer-action-mount { justify-self: stretch; }" in TEMPLATE_HTML


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


def test_todo_list_has_a_dock_action_and_safe_read_only_dialog():
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')
    serve_src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")

    assert 'id="todosMenuItem"' in dock
    assert 'onclick="closeActionMenu();showTodosDialog()"' in dock
    assert 'data-i18n="todoList"' in dock
    assert 'data-i18n="todoListDesc"' in dock
    assert '"todos.js"' in serve_src
    assert "action$('list_todos'," in TODOS_JS
    assert "status: _todosState.status" in TODOS_JS
    assert "limit: TODO_PAGE_SIZE" in TODOS_JS
    assert "offset: _todosState.tasks.length" in TODOS_JS
    assert "todo-search" in TODOS_JS
    assert "todo-tab" in TODOS_JS
    assert "todo-load-more" in TODOS_JS
    assert "function showTodosDialog()" in TODOS_JS
    assert "function closeTodosDialog()" in TODOS_JS
    assert "function closeTodosDialog() {\n  _todosDialogAgent = '';" in TODOS_JS
    assert "textContent" in TODOS_JS
    assert ".innerHTML" not in TODOS_JS
    assert "todolist" not in TODOS_JS


def test_control_docks_use_an_external_css_tooltip_without_horizontal_scroll():
    serve_src = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')

    assert 'id="pfCssTooltip"' in TEMPLATE_HTML
    assert '.pf-css-tooltip {' in TEMPLATE_HTML
    # The dock stays scrollable when narrow, but its scrollbar is hidden and
    # end padding absorbs the 1.4x hover growth of the first/last icon.
    assert 'overflow-x: auto; overflow-y: visible;' in TEMPLATE_HTML
    assert 'padding: 4px 8px;' in TEMPLATE_HTML
    assert 'scrollbar-width: none;' in TEMPLATE_HTML
    assert '.action-dock-menu::-webkit-scrollbar' in TEMPLATE_HTML
    assert 'height: 0;' in TEMPLATE_HTML
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


def test_composer_keeps_only_attachment_before_prompt():
    input_row = _between('<div class="input-row">', '</div>\n</div>\n</div><!-- /tab-content chat -->')
    before_prompt = input_row[:input_row.index('id="input"')]

    assert 'id="promptsBtn"' not in input_row
    assert 'id="fileAttachBtn"' in before_prompt
    for element_id in ("voiceModeBtn", "grabBtn", "refreshConvBtn"):
        assert f'id="{element_id}"' not in input_row


def test_voice_grab_and_refresh_live_in_conversation_controls():
    controls = _between(
        '<div class="prompt-controls-panel"', '<div class="composer-action-mount"'
    )

    for element_id in ("voiceModeBtn", "grabBtn", "refreshConvBtn"):
        assert f'id="{element_id}"' in controls


def test_task_dock_stays_fixed_while_action_dock_joins_composer():
    assert ".action-dock-menu { display: flex; position: static;" in TEMPLATE_HTML
    assert ".task-tab-dock { position: fixed;" in TEMPLATE_HTML
    assert ".action-dock { position: relative;" in TEMPLATE_HTML
