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
    assert "window.PAWFLOW_GROUP_TECHNICAL_MESSAGES = false" in MESSAGES_JS
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
    assert "drop empty technical element" in MESSAGES_JS
    assert "function _markTechnicalGroupSettled(group)" in MESSAGES_JS
    assert "function findToolCallElement(tcId, root)" in MESSAGES_JS
    assert "className = 'msg technical-group'" in MESSAGES_JS
    assert "el.dataset.messageRole = role" in MESSAGES_JS
    assert "details.dataset.live = '1'" in SSE_JS
    assert "te.el.setAttribute('open', '')" in SSE_JS
    assert "String(text || '').trim()) collapseTechnicalGroups()" in MESSAGES_JS
    assert "displayText.trim() && s.el && !s.el.dataset.technicalGroupsCollapsed" in SSE_JS
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
