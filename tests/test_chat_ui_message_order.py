"""Regression tests for chat UI message ordering."""

from pathlib import Path


MESSAGES_JS = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")
CONVERSATIONS_JS = Path("tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")
SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
TEMPLATE_HTML = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")


def test_add_msg_inserts_by_message_timestamp():
    """Late SSE tool events must not render after newer assistant text."""
    assert "function _messageSortTs(extra)" in MESSAGES_JS
    assert "function _insertMessageChronologically(container, el, sortTs)" in MESSAGES_JS
    assert "childTs > sortTs" in MESSAGES_JS
    assert "_insertMessageChronologically(container, el, _ts)" in MESSAGES_JS


def test_notification_rows_use_same_ordering_path():
    assert "const notifSortTs = _messageSortTs(extra);" in MESSAGES_JS
    assert "_insertMessageChronologically(notifContainer, notifEl, notifSortTs)" in MESSAGES_JS


def test_technical_grouping_is_expression_driven_and_post_rendered():
    assert "window.PAWFLOW_GROUP_TECHNICAL_MESSAGES = true" in MESSAGES_JS
    assert "function applyTechnicalMessageGrouping()" in MESSAGES_JS
    assert "window.PAWFLOW_SUSPEND_TECHNICAL_GROUPING = 0" in MESSAGES_JS
    assert "function suspendTechnicalMessageGrouping()" in MESSAGES_JS
    assert "function resumeTechnicalMessageGrouping(applyNow)" in MESSAGES_JS
    assert "'thinking', 'sub_agent_trace'" in MESSAGES_JS
    assert "'system-compact'" not in MESSAGES_JS
    assert "contains('system-compact')" not in MESSAGES_JS
    assert "function collapseTechnicalGroups()" in MESSAGES_JS
    assert "function _openLiveTechnicalElement(el)" in MESSAGES_JS
    assert "function _hasVisibleTechnicalContent(el)" in MESSAGES_JS
    assert "function _isAssistantPlaceholderElement(el)" in MESSAGES_JS
    assert "function _isAgentChromeOnlyElement(el)" in MESSAGES_JS
    assert "/^(assistant|user)(\\s+\\d{1,2}:\\d{2}(:\\d{2})?)?$/i.test(text)" in MESSAGES_JS
    assert "if (_isAgentChromeOnlyElement(el)) return false;" in MESSAGES_JS
    assert "function _extractNonTechnicalChildren(group)" in MESSAGES_JS
    assert "dataset.transientUi === '1' || el.dataset.technicalBoundary === '1'" in MESSAGES_JS
    assert "el.dataset.technicalBoundary = '1'" in MESSAGES_JS
    assert "!String(text || '').trim()) el.dataset.transientUi = '1'" in MESSAGES_JS
    assert "delete s.el.dataset.transientUi" in SSE_JS
    assert "addMsg('system-compact'" not in SSE_JS
    assert "drop empty technical element" in MESSAGES_JS
    assert "function _markTechnicalGroupSettled(group)" in MESSAGES_JS
    assert "function _markTechnicalGroupUserIntent(group)" in MESSAGES_JS
    assert "group.dataset.userOpen === '1' || _isLiveTechnicalElement(group)" in MESSAGES_JS
    assert "summary.addEventListener('click', () => _markTechnicalGroupUserIntent(group))" in MESSAGES_JS
    assert "t('technicalDetailsSummary'" in MESSAGES_JS
    assert "Technical details ·" not in MESSAGES_JS
    assert "function findToolCallElement(tcId, root)" in MESSAGES_JS
    assert "className = 'msg technical-group'" in MESSAGES_JS
    assert "el.dataset.messageRole = role" in MESSAGES_JS
    assert "details.dataset.live = '1'" in SSE_JS
    assert "te.el.setAttribute('open', '')" in SSE_JS
    assert "String(text || '').trim()) collapseTechnicalGroups()" in MESSAGES_JS
    assert "displayText.trim() && s.el && !s.el.dataset.technicalGroupsCollapsed" in SSE_JS
    assert "_attachToolResult(tcEl, data.result || '');" in SSE_JS
    assert "if (typeof applyTechnicalMessageGrouping === 'function') applyTechnicalMessageGrouping();\n        return;" in SSE_JS
    assert "const groupTechnicalMessages = !!data.group_technical_messages" in CONVERSATIONS_JS
    assert "setTechnicalMessageGrouping(groupTechnicalMessages)" in CONVERSATIONS_JS
    assert "updateTechnicalGroupingToggle(groupTechnicalMessages)" in CONVERSATIONS_JS
    assert "suspendTechnicalMessageGrouping()" in CONVERSATIONS_JS
    assert "resumeTechnicalMessageGrouping(false)" in CONVERSATIONS_JS
    assert "applyTechnicalMessageGrouping();" in SSE_JS


def test_technical_grouping_toggle_sets_conversation_parameter_and_reloads():
    assert 'id="technicalGroupingToggle"' in TEMPLATE_HTML
    assert "onclick=\"onTechnicalGroupingToggle()\"" in TEMPLATE_HTML
    assert "const TECHNICAL_GROUPING_PARAM = 'chat.group_technical_messages'" in CONVERSATIONS_JS
    assert "action$('set_param'" in CONVERSATIONS_JS
    assert "scope: 'conversation'" in CONVERSATIONS_JS
    assert "key: TECHNICAL_GROUPING_PARAM" in CONVERSATIONS_JS
    assert "value: next ? 'true' : 'false'" in CONVERSATIONS_JS
    assert "resumeConv(conversationId, true)" in CONVERSATIONS_JS
    assert "btn.style.display = conversationId ? 'inline-flex' : 'none'" in CONVERSATIONS_JS
    assert "btn.innerHTML = active ? '&#x25A3;' : '&#x25A1;'" in CONVERSATIONS_JS
    assert "btn.setAttribute('aria-label', label)" in CONVERSATIONS_JS
    assert 'title="Group technical details.' in TEMPLATE_HTML
    assert 'Group tech</button>' not in TEMPLATE_HTML


def test_autoscroll_only_stops_on_user_scroll_intent():
    assert "let _autoScroll = true" in MESSAGES_JS
    assert "function setMessagesScrollTop(value)" in MESSAGES_JS
    assert "m.addEventListener('wheel', markUserScrollIntent" in MESSAGES_JS
    assert "m.addEventListener('pointerdown'" in MESSAGES_JS
    assert "isScrollbarPointerEvent(e)" in MESSAGES_JS
    assert "m.addEventListener('touchstart', markUserScrollIntent" in MESSAGES_JS
    assert "m.addEventListener('keydown'" in MESSAGES_JS
    assert "hasUserScrollIntent()" in MESSAGES_JS
    assert "m.scrollTop < _lastScrollTop" not in MESSAGES_JS
    assert "container.scrollTop = container.scrollHeight - prevHeight" not in CONVERSATIONS_JS


def test_inline_audio_uses_stable_global_player():
    assert "function pawflowInlineAudioToggle(btn)" in MESSAGES_JS
    assert "function pawflowInlineAudioSeek(input)" in MESSAGES_JS
    assert "var _inlineAudioEl = null" in MESSAGES_JS
    assert "new Audio(url)" in MESSAGES_JS
    assert "data-audio-url" in MESSAGES_JS
    assert "<audio controls" not in MESSAGES_JS


def test_primary_chat_controls_are_i18n_bound():
    assert 'id="input"' in TEMPLATE_HTML
    assert 'data-i18n-placeholder="placeholder"' in TEMPLATE_HTML
    assert 'id="sendBtn"' in TEMPLATE_HTML
    assert 'data-i18n="send"' in TEMPLATE_HTML
    assert 'id="stopBtn"' in TEMPLATE_HTML
    assert 'data-i18n-title="stopTitle"' in TEMPLATE_HTML
    assert "title=\"Reply\"" not in MESSAGES_JS
    assert "title=\"Copy\"" not in MESSAGES_JS
    assert "title=\"Delete\"" not in MESSAGES_JS
    assert "Thinking..." not in SSE_JS
    assert "Thought for " not in SSE_JS



def test_active_agents_sse_hint_restores_panel_before_poll():
    active_agents_js = Path("tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")
    assert "function trackAgentStart(agentName, msgPreview) { /* no-op */ }" not in active_agents_js
    assert "activeInteractions[key] = {" in active_agents_js
    assert "setConversationWorking(conversationId, true)" in active_agents_js
    assert "updateActivePanel();" in active_agents_js
