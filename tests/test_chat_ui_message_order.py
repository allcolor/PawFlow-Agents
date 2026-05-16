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
    assert "function _technicalGroupKey(el)" not in MESSAGES_JS
    assert "function _technicalGroupShouldSplit(group, childKey)" not in MESSAGES_JS
    assert "return 'tool:' + tcId" not in MESSAGES_JS
    assert "group.dataset.technicalGroupKey" not in MESSAGES_JS
    assert "className = 'msg technical-group'" in MESSAGES_JS
    assert "el.dataset.messageRole = role" in MESSAGES_JS
    assert "details.dataset.live = '1'" in SSE_JS
    assert "te.el.setAttribute('open', '')" in SSE_JS
    assert "String(text || '').trim()) collapseTechnicalGroups()" in MESSAGES_JS
    assert "displayText.trim() && s.el && !s.el.dataset.technicalGroupsCollapsed" in SSE_JS
    assert "_attachToolResult(tcEl, data.result || '');" in SSE_JS
    assert "_attachToolResult(tcEl, data.result || '');" in SSE_JS
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
    assert "let _suppressTopLoadUntil = 0" in MESSAGES_JS
    assert "function setMessagesScrollTop(value)" in MESSAGES_JS
    assert "function scrollMessagesTop()" in MESSAGES_JS
    assert "_autoScroll = false" in MESSAGES_JS
    assert "Date.now() > _suppressTopLoadUntil" in MESSAGES_JS
    assert "scrollMessagesTop();document.getElementById('input').focus()" in TEMPLATE_HTML
    assert "m.addEventListener('wheel', markUserScrollIntent" in MESSAGES_JS
    assert "m.addEventListener('pointerdown'" in MESSAGES_JS
    assert "isScrollbarPointerEvent(e)" in MESSAGES_JS
    assert "m.addEventListener('touchstart', markUserScrollIntent" in MESSAGES_JS
    assert "m.addEventListener('keydown'" in MESSAGES_JS
    assert "hasUserScrollIntent()" in MESSAGES_JS
    assert "m.scrollTop < _lastScrollTop" not in MESSAGES_JS
    assert "container.scrollTop = container.scrollHeight - prevHeight" not in CONVERSATIONS_JS


def test_tool_results_carry_tc_id_for_reload_grouping():
    assert "if (tcId) el.dataset.tcId = tcId;" in MESSAGES_JS
    assert "if (tcId) _inner.dataset.tcId = tcId;" in MESSAGES_JS


def test_clear_keeps_conversation_and_load_more_entrypoint():
    assert "function cmdClear()" in Path("tasks/io/chat_ui/cmd_conversation.js").read_text(encoding="utf-8")
    conv_cmds = Path("tasks/io/chat_ui/cmd_conversation.js").read_text(encoding="utf-8")
    clear_block = conv_cmds[conv_cmds.index("function cmdClear()"):conv_cmds.index("function cmdClearStore")]
    assert "newChat()" not in clear_block
    assert "currentOffset = 0" in clear_block
    assert "hasMoreMessages = knownTotal > 0" in clear_block
    assert "_updateLoadMoreBanner()" in clear_block


def test_message_actions_can_copy_id_and_restart_from_msg_id():
    attachments_js = Path("tasks/io/chat_ui/attachments.js").read_text(encoding="utf-8")
    assert "copyMsgId(this)" in MESSAGES_JS
    assert "restartFromMsg(this)" in MESSAGES_JS
    assert "function copyMsgId(btn)" in attachments_js
    assert "navigator.clipboard.writeText(msg.dataset.msgid)" in attachments_js
    assert "function restartFromMsg(btn)" in attachments_js
    assert "confirm(t('restartFromHereConfirm'))" in attachments_js
    assert "action$('restart_from', { msg_id: msg.dataset.msgid })" in attachments_js


def test_restart_from_done_reloads_truncated_conversation():
    assert "data.operation === 'restart_from'" in SSE_JS
    assert "resumeConv(conversationId, true)" in SSE_JS


def test_live_tool_events_keep_chat_scrolled():
    tool_call_block = SSE_JS[SSE_JS.index("eventSource.addEventListener('tool_call'"):SSE_JS.index("eventSource.addEventListener('tool_result'")]
    tool_result_block = SSE_JS[SSE_JS.index("eventSource.addEventListener('tool_result'"):SSE_JS.index("eventSource.addEventListener('bg_task_update'")]
    assert "scrollBottom();" in tool_call_block
    assert "scrollBottom();" in tool_result_block


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
