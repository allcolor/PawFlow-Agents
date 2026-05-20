"""Regression tests for chat UI message ordering."""

from pathlib import Path


MESSAGES_JS = Path("tasks/io/chat_ui/messages.js").read_text(encoding="utf-8")
CONVERSATIONS_JS = Path("tasks/io/chat_ui/conversations.js").read_text(encoding="utf-8")
SSE_JS = Path("tasks/io/chat_ui/sse.js").read_text(encoding="utf-8")
TEMPLATE_HTML = Path("tasks/io/chat_ui/template.html").read_text(encoding="utf-8")
AGENT_CORE = Path("tasks/ai/agent_core.py").read_text(encoding="utf-8")
TASK_MANAGEMENT = Path("core/handlers/task_management.py").read_text(encoding="utf-8")


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
    assert "if (role === 'sub_agent_trace') return false;" in MESSAGES_JS
    assert "'tool_result', 'thinking'," in MESSAGES_JS
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
    assert "_attachToolResult(tcEl, _resultText(data.result || ''));" in SSE_JS
    assert "const groupTechnicalMessages = !!data.group_technical_messages" in CONVERSATIONS_JS
    assert "setTechnicalMessageGrouping(groupTechnicalMessages)" in CONVERSATIONS_JS
    assert "updateViewMenuItem('technical', groupTechnicalMessages)" in CONVERSATIONS_JS
    assert "suspendTechnicalMessageGrouping()" in CONVERSATIONS_JS
    assert "resumeTechnicalMessageGrouping(false)" in CONVERSATIONS_JS
    assert "applyTechnicalMessageGrouping();" in SSE_JS


def test_view_menu_grouping_toggles_set_conversation_parameter_and_reload():
    assert 'id="viewMenuToggle"' in TEMPLATE_HTML
    assert 'id="viewItemTechnical"' in TEMPLATE_HTML
    assert 'id="viewItemTask"' in TEMPLATE_HTML
    assert 'id="viewItemDelegate"' in TEMPLATE_HTML
    assert "onclick=\"onViewGroupingToggle('technical')\"" in TEMPLATE_HTML
    assert "onclick=\"onViewGroupingToggle('task')\"" in TEMPLATE_HTML
    assert "onclick=\"onViewGroupingToggle('delegate')\"" in TEMPLATE_HTML
    assert "paramKey: 'chat.group_technical_messages'" in CONVERSATIONS_JS
    assert "paramKey: 'chat.group_task_messages'" in CONVERSATIONS_JS
    assert "paramKey: 'chat.group_delegate_messages'" in CONVERSATIONS_JS
    assert "action$('set_param'" in CONVERSATIONS_JS
    assert "scope: 'conversation'" in CONVERSATIONS_JS
    assert "key: cfg.paramKey" in CONVERSATIONS_JS
    assert "value: next ? 'true' : 'false'" in CONVERSATIONS_JS
    assert "resumeConv(conversationId, true)" in CONVERSATIONS_JS
    assert "wrap.style.display = conversationId ? 'inline-flex' : 'none'" in CONVERSATIONS_JS


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


def test_chat_scroll_container_has_stable_flex_height_and_post_render_refresh():
    assert ".main { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }" in TEMPLATE_HTML
    assert ".messages-wrap { flex: 1; position: relative; overflow: hidden; display: flex; flex-direction: column; min-width: 0; min-height: 0; width: 100%; }" in TEMPLATE_HTML
    assert ".messages { flex: 1 1 auto; width: 100%; min-width: 0; min-height: 0; overflow-y: auto;" in TEMPLATE_HTML
    assert "overscroll-behavior: contain" in TEMPLATE_HTML
    assert "function refreshMessagesScrollMetrics(forceBottom)" in MESSAGES_JS
    assert "window.requestAnimationFrame(() =>" in MESSAGES_JS
    assert "window.requestAnimationFrame(settle)" in MESSAGES_JS
    assert "refreshMessagesScrollMetrics(!!force)" in MESSAGES_JS
    assert "const themeLoad = typeof loadThemeSelector === 'function' ? loadThemeSelector() : null" in CONVERSATIONS_JS
    assert "refreshMessagesScrollMetrics(true)" in CONVERSATIONS_JS


def test_tool_results_carry_tc_id_for_reload_grouping():
    assert "if (tcId) el.dataset.tcId = tcId;" in MESSAGES_JS
    assert "if (tcId) _inner.dataset.tcId = tcId;" in MESSAGES_JS


def test_live_tool_results_are_reconciled_when_sse_arrives_out_of_order():
    assert "const _pendingToolResults = {};" in SSE_JS
    assert "function _attachPendingToolResult(tcEl, tcId)" in SSE_JS
    assert "function _queueUnmatchedToolResult(tcId, data)" in SSE_JS
    tool_call_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('tool_call'"):
        SSE_JS.index("eventSource.addEventListener('tool_result'")]
    tool_result_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('tool_result'"):
        SSE_JS.index("eventSource.addEventListener('bg_task_update'")]
    assert "_attachPendingToolResult(tcEl, data.tc_id)" in tool_call_block
    assert "_queueUnmatchedToolResult(tcId, data)" in tool_result_block
    assert "dataset.messageRole === 'tool_call'" in MESSAGES_JS


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


def test_live_thinking_blocks_split_after_non_thinking_events():
    thinking_block = SSE_JS[
        SSE_JS.index("// ── Extended thinking"):
        SSE_JS.index("eventSource.addEventListener('token'")]
    tool_call_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('tool_call'"):
        SSE_JS.index("eventSource.addEventListener('tool_result'")]
    tool_result_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('tool_result'"):
        SSE_JS.index("eventSource.addEventListener('bg_task_update'")]
    new_message_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('new_message'"):
        SSE_JS.index("// ── Proactive notifications", SSE_JS.index("eventSource.addEventListener('new_message'"))]

    assert "softFinalized" not in SSE_JS
    assert "delete thinkingElements[aKey];" in thinking_block
    assert "delete te.el.dataset.live" in thinking_block
    assert "finalizeThinkingFromEvent(data, 'message')" in new_message_block
    assert "finalizeThinkingFromEvent(data, 'tool_call')" in tool_call_block
    assert "finalizeThinkingFromEvent(data, 'tool_result')" in tool_result_block


def test_delegate_thinking_chunks_split_after_delegate_non_thinking_events():
    delegate_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('sub_agent_thinking'"):
        SSE_JS.index("eventSource.addEventListener('sub_agent_done'")]
    assert "const delegateThinkingElements = {};" in SSE_JS
    assert "te.text += data.thinking || '';" in delegate_block
    assert "finalizeDelegateThinking(data.task_id)" in delegate_block
    assert "for (const k in delegateThinkingElements) delete delegateThinkingElements[k];" in SSE_JS


def test_task_subconv_messages_publish_live_events_on_parent_conversation():
    assert "_task_parent_cid = conversation_id.split(\"::task::\", 1)[0]" in AGENT_CORE
    assert "_evt2[\"cid\"] = _task_parent_cid" in AGENT_CORE
    assert "_data[\"task_id\"] = _task_id" in AGENT_CORE
    assert "_data[\"task_iteration\"] = _task_iteration" in AGENT_CORE
    assert "sse_events=None if _task_parent_cid else (_sse if _sse else None)" in AGENT_CORE
    assert "sse_events=_parent_sse if _parent_sse else None" in AGENT_CORE


def test_task_live_and_history_group_by_task_id_and_iteration():
    new_message_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('new_message'"):
        SSE_JS.index("// ── Proactive notifications", SSE_JS.index("eventSource.addEventListener('new_message'"))]
    assert "task_id: data.task_id || '', task_iteration: data.task_iteration" in new_message_block
    assert "_getTaskBlock(data.task_id, data.task_iteration" in new_message_block
    assert "tb.content.appendChild(el)" in new_message_block
    assert "function _getHistTaskBlock(taskId, iteration, agentName)" in CONVERSATIONS_JS
    assert "const blockKey = taskId + '::iter' + iter" in CONVERSATIONS_JS
    assert "const tb = _getHistTaskBlock(_taskId, _iter, agentName)" in CONVERSATIONS_JS
    assert "_getHistTaskBlock(_blockKey" not in CONVERSATIONS_JS
    task_progress_block = SSE_JS[
        SSE_JS.index("eventSource.addEventListener('task_progress'"):
        SSE_JS.index("eventSource.addEventListener('task_stopped'")]
    assert "data.task_iteration || data.iterations" in task_progress_block
    assert '_task_iteration = task.get("reschedule_count", task["iterations_done"])' in TASK_MANAGEMENT
    assert '"task_iteration": _task_iteration' in TASK_MANAGEMENT


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


def test_thinking_does_not_merge_across_tool_call_boundaries():
    assert "softFinalized" not in SSE_JS
    assert "finalizeThinkingFromEvent(data, 'tool_call')" in SSE_JS
    assert "delete thinkingElements[aKey];" in SSE_JS
    assert "if (te.text && textDelta)" not in SSE_JS



def test_active_agents_sse_hint_restores_panel_before_poll():
    active_agents_js = Path("tasks/io/chat_ui/active_agents.js").read_text(encoding="utf-8")
    assert "function trackAgentStart(agentName, msgPreview) { /* no-op */ }" not in active_agents_js
    assert "activeInteractions[key] = {" in active_agents_js
    assert "setConversationWorking(conversationId, true)" in active_agents_js
    assert "updateActivePanel();" in active_agents_js
