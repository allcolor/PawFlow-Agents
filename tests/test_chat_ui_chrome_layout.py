"""Structural assertions for the simplified chat chrome."""

import json

from chat_ui_testing import rendered_chat_html
from pathlib import Path


TEMPLATE_HTML = rendered_chat_html()
TOOLTIPS_JS = Path("tasks/io/chat_ui/tooltips.js").read_text(encoding="utf-8")
TODOS_JS = Path("tasks/io/chat_ui/todos.js").read_text(encoding="utf-8")
ATTACHMENTS_JS = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
STATE_JS = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    start_index = TEMPLATE_HTML.index(start)
    return TEMPLATE_HTML[start_index:TEMPLATE_HTML.index(end, start_index)]


HEADER_OPEN = '<div class="header" id="headerBar">'


def test_appearance_and_language_are_compact_controls_in_the_header():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    header_lead = header[:header.index('id="actionMenuWrap"')]
    dock = _between('<div class="action-menu-wrap action-dock"', '<!-- /action dock -->')

    for element_id in ("appearanceBtn", "languageSelect"):
        marker = f'id="{element_id}"'
        assert marker in header_lead
        assert marker not in dock
    assert header_lead.count(
        'class="header-select-control header-dock-item"'
    ) == 1
    assert header_lead.count('class="header-icon-select"') == 1
    assert 'id="appearanceBtn"' in header_lead
    assert '<path d="M12 3a9 9 0 1 0' in header_lead
    assert '&#x1F310;' in header_lead
    assert (
        header_lead.index('<h1 class="header-logo">')
        < header_lead.index('id="appearanceBtn"')
        < header_lead.index('id="languageSelect"')
        < header_lead.index('id="actionLoading"')
        < header_lead.index('id="activeAgentBadge"')
        < header_lead.index('id="usageCostBadge"')
        < header_lead.index('<div class="actions">')
    )


def test_appearance_and_language_reuse_dock_tooltips_and_hover_zoom():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    header_controls = header[:header.index('id="actionMenuWrap"')]

    for control_id in ("languageSelectControl",):
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
    assert 'id="themeSelectControl"' not in header_controls
    assert 'id="appearanceBtn"' in header_controls


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
    # The username is NOT shown on the icon: it is the tooltip's ami-desc
    # (still inside linkAccountBtn) and appears in the linked-accounts dialog.
    btn_end = header_controls.index('</button>', header_controls.index('id="linkAccountBtn"'))
    link_btn = header_controls[header_controls.index('id="linkAccountBtn"'):btn_end]
    assert '<div class="ami-desc user-info" id="userInfo"></div>' in link_btn
    assert "#linkAccountBtn { width: auto;" not in TEMPLATE_HTML
    assert "linked-account-me" in STATE_JS
    assert ".linked-account-me {" in TEMPLATE_HTML
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
        # linkAccountBtn's desc is the username (id="userInfo"), so the desc
        # class may carry extra classes; presence is what matters.
        assert 'class="ami-desc' in button

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


def test_conversation_expiry_opens_from_context_menu_but_theme_lives_in_appearance():
    sidebar = _between(
        '<div class="sidebar collapsed"',
        '<div class="dialog-bg" id="conversationSettingsDialog"',
    )
    menu_js = Path("tasks/io/chat_ui/conversations_menu.js").read_text(encoding="utf-8")

    assert 'id="ttlSelect"' not in sidebar
    assert 'id="conversationThemeSelect"' not in sidebar
    assert 'id="conversationSettingsDialog"' in TEMPLATE_HTML
    assert 'id="ttlSelect"' in TEMPLATE_HTML
    assert 'id="conversationThemeSelect"' not in TEMPLATE_HTML
    assert 'id="appearanceThemeSelect"' in TEMPLATE_HTML
    assert "#conversationSettingsDialog select { width: 100%;" in TEMPLATE_HTML
    assert "showConversationSettings(cid)" in menu_js
    assert "function showConversationSettings(cid)" in menu_js


def test_conversation_context_menu_uses_theme_and_hides_busy_git_actions():
    menu_js = Path("tasks/io/chat_ui/conversations_menu.js").read_text(encoding="utf-8")

    assert "d.className = 'ctx-menu-item'" in menu_js
    assert "d.onmouseenter" not in menu_js
    assert "disabled: !idle" not in menu_js
    assert "if (idle) {" in menu_js
    assert "var(--pf-panel, #16213e)" in TEMPLATE_HTML
    assert "var(--pf-text, #c0c0d0)" in TEMPLATE_HTML
    assert ".ctx-menu-item:hover { background: var(--pf-assistant" in TEMPLATE_HTML


def test_context_menus_use_shared_theme_classes():
    services_js = Path("tasks/io/chat_ui/services.js").read_text(encoding="utf-8")
    sharing_js = Path("tasks/io/chat_ui/conversations_share.js").read_text(
        encoding="utf-8"
    )
    viewer_js = Path("tasks/io/chat_ui/file_viewer.js").read_text(
        encoding="utf-8"
    )
    menu_sources = (
        services_js.split("function showFlowInstanceMenu", 1)[1].split(
            "async function _openFlowGraphTab", 1
        )[0],
        sharing_js.split("function showSharedConvMenu", 1)[1].split(
            "function respondToShareInvite", 1
        )[0],
        viewer_js.split("function showParamMenu", 1)[1].split(
            "function _showParamEditor", 1
        )[0],
    )

    for source in menu_sources:
        assert "d.className = 'ctx-menu-item'" in source
        assert "d.onmouseenter = () => d.style.background = '#2a2a4a'" not in source
        assert "background:#1a1a2e" not in source
        assert "danger ? '#e94560' : '#e0e0e0'" not in source


def test_language_selector_renders_catalog_flags():
    languages = json.loads(
        Path("tasks/io/chat_ui/i18n/languages.json").read_text(encoding="utf-8")
    )
    i18n_js = Path("tasks/io/chat_ui/i18n.js").read_text(encoding="utf-8")

    assert all(language.get("flag") for language in languages)
    assert "lang.flag" in i18n_js


def test_view_audio_and_permissions_live_in_left_prompt_panel_but_dictation_does_not():
    header = _between(HEADER_OPEN, '<!-- Chat tab content -->')
    controls = _between(
        '<div class="prompt-controls-panel"', '<div class="composer-action-mount"'
    )

    for element_id in ("permissionModeBtn", "permissionModeMenu", "speakToggleBtn", "viewMenuWrap"):
        marker = f'id="{element_id}"'
        assert marker in controls
        assert marker not in header

    assert controls.index('id="refreshConvBtn"') < controls.index('id="conversationAppearanceBtn"')

    composer_css = Path("tasks/io/chat_ui/css/50_composer.css").read_text(encoding="utf-8")
    state_js = Path("tasks/io/chat_ui/state.js").read_text(encoding="utf-8")
    assert '<select id="permissionMode"' not in controls
    assert ".conversation-quick-theme" not in composer_css
    assert "button.setAttribute('aria-label', accessibleLabel)" in state_js
    assert "function togglePermissionModeMenu(force)" in state_js
    assert "function closePermissionModeMenu()" in state_js
    assert "document.getElementById('permissionMode').style" not in state_js
    assert "const permissionControl = document.getElementById('permissionModeWrap');" in state_js
    assert "if (permissionControl) permissionControl.style.display" in state_js

    assert 'id="speechInputBtn"' not in controls
    composer = _between('<div class="input-row composer-shell"', '</div><!-- /tab-content chat -->')
    assert 'id="speechInputBtn"' in composer
    assert 'id="grabBtn"' in composer

    assert 'class="composer-context-controls"' not in TEMPLATE_HTML
    assert ".prompt-controls-panel {" in TEMPLATE_HTML
    assert "position: fixed; bottom: 70px; left: 20px" not in TEMPLATE_HTML


def test_composer_context_row_orders_controls_and_action_dock():
    input_area = _between('<div class="input-area">', '</div><!-- /tab-content chat -->')
    context_row = _between(
        '<div class="composer-context-row">', '<div class="input-row composer-shell">'
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
    # A three-column grid is valid for the separate mobile prompt shell; only
    # the context row itself must keep the symmetric desktop columns above.
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
    assert '.conversation-control-button' in TOOLTIPS_JS
    assert "getBoundingClientRect()" in TOOLTIPS_JS


def test_conversation_control_buttons_are_thin_and_share_dock_motion():
    css = Path("tasks/io/chat_ui/css/50_composer.css").read_text(encoding="utf-8")

    assert "padding: 2px 4px 3px" in css
    assert ".conversation-control-button { width: 30px !important; min-width: 30px; height: 30px;" in css
    assert ".conversation-control-button {" in css
    assert ".conversation-quick-theme" not in css
    assert "background: var(--pf-sidebar) !important" in css
    assert "border: 1px solid var(--pf-accent) !important" in css
    assert "transform: scale(1.4)" in css
    assert "box-shadow: 0 6px 16px" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_attachment_thumbnails_stack_before_send_and_expand_past_three():
    input_row = _between('<div class="input-row composer-shell">', '</div>\n</div>\n</div><!-- /tab-content chat -->')

    assert input_row.index('id="attachPreview"') < input_row.index('id="input"')
    assert input_row.index('id="input"') < input_row.index('id="sendBtn"')
    assert ".attachments-preview { display: flex;" in TEMPLATE_HTML
    assert ".att-item + .att-item { margin-left:" in TEMPLATE_HTML
    assert "pendingFiles.slice(0, 3)" in ATTACHMENTS_JS
    assert "attachment-overflow-count" in ATTACHMENTS_JS
    assert "toggleAttachmentPreview" in ATTACHMENTS_JS


def test_unified_composer_owns_prompt_actions_but_not_conversation_controls():
    input_row = _between('<div class="input-row composer-shell">', '</div>\n</div>\n</div><!-- /tab-content chat -->')
    before_prompt = input_row[:input_row.index('id="input"')]

    assert 'id="promptsBtn"' not in input_row
    assert 'id="fileAttachBtn"' in before_prompt
    for element_id in ("composerSlashBtn", "composerMentionBtn"):
        assert f'id="{element_id}"' in before_prompt
    for element_id in ("speechInputBtn", "grabBtn", "sendBtn"):
        assert f'id="{element_id}"' in input_row
    assert (
        input_row.index('id="input"')
        < input_row.index('id="speechInputBtn"')
        < input_row.index('id="grabBtn"')
        < input_row.index('id="sendBtn"')
    )
    for element_id in ("voiceModeBtn", "refreshConvBtn"):
        assert f'id="{element_id}"' not in input_row


def test_realtime_voice_and_refresh_remain_in_conversation_controls():
    controls = _between(
        '<div class="prompt-controls-panel"', '<div class="composer-action-mount"'
    )

    for element_id in ("voiceModeBtn", "refreshConvBtn"):
        assert f'id="{element_id}"' in controls
    assert 'id="speechInputBtn"' not in controls
    assert 'id="grabBtn"' not in controls


def test_task_dock_stays_fixed_while_action_dock_joins_composer():
    assert ".action-dock-menu { display: flex; position: static;" in TEMPLATE_HTML
    assert ".task-tab-dock { position: fixed;" in TEMPLATE_HTML
    assert ".action-dock { position: relative;" in TEMPLATE_HTML
