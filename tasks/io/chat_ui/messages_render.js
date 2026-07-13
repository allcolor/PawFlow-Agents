// \xe2\x94\x80\xe2\x94\x80 Message rendering: addMsg + live-display window trim \xe2\x94\x80\xe2\x94\x80
// Split from messages.js (<=800 lines). Global; loads right after the
// messages core (tool-summary/grouping helpers) and before _tools/_markdown.

const LIVE_DISPLAY_WINDOW_MULTIPLIER = 4;

function trimLiveDisplayWindowIfAutoscrolling(wasAutoscroll) {
  if (!wasAutoscroll) return;
  const container = document.getElementById('messages');
  if (!container) return;
  const maxVisible = Math.max(displayWindow || 50, 50) * LIVE_DISPLAY_WINDOW_MULTIPLIER;
  const rows = Array.from(container.children).filter(el => {
    if (!el.classList || !el.classList.contains('msg')) return false;
    if (el.dataset && el.dataset.live === '1') return false;
    if (el.querySelector && el.querySelector('[data-live="1"]')) return false;
    return true;
  });
  let excess = rows.length - maxVisible;
  if (excess <= 0) return;
  for (const el of rows) {
    if (excess <= 0) break;
    const mid = el.dataset && el.dataset.msgid;
    if (mid && typeof _selectedMsgIds !== 'undefined' && _selectedMsgIds.has(mid)) continue;
    const evicted = [];
    if (mid) evicted.push(mid);
    if (el.querySelectorAll) {
      for (const child of el.querySelectorAll('[data-msgid]')) {
        if (child.dataset && child.dataset.msgid) evicted.push(child.dataset.msgid);
      }
    }
    if (typeof _seenMsgIds !== 'undefined') {
      for (const msgId of evicted) _seenMsgIds.delete(msgId);
    }
    el.remove();
    excess--;
  }
  hasMoreMessages = true;
  if (typeof _updateLoadMoreBanner === 'function') _updateLoadMoreBanner();
}

function addMsg(role, text, extra) {
  // Dedup by msg_id — if we've already displayed this message, skip
  const msgId = (extra && extra.msg_id) || '';
  if (msgId) {
    if (_seenMsgIds.has(msgId)) {
      return null;
    }
    _seenMsgIds.add(msgId);
  }
  if (role === 'tool_call' && extra && extra.tc_id
      && findToolCallElement(extra.tc_id)) {
    return null;
  }
  // Background-tool results are written to transcript as role=user (that
  // is what CC's wire protocol requires for tool_result replies), but in
  // the chat UI they should render as a collapsible tool_result block,
  // not as the user's own voice. The payload sent to the LLM stays
  // role=user; this is a display-only relabel. Source tag
  // `{type:'system', name:'background'}` is set by
  // core.background_tool.py:_write_cc_result.
  //
  // role='tool_result' (not 'tool') so the renderer at the tool_result
  // branch below produces <details>…</details> — otherwise we fall
  // into the tool_call compact-header branch and the long output is
  // rendered as a single wall of text without any collapse.
  if (role === 'user' && extra && extra.source
      && extra.source.type === 'system'
      && extra.source.name === 'background') {
    role = 'tool_result';
  }
  // PushNotification bell row: proactive attention signal from an agent.
  // Rendered as a compact 🔔 line (not a full user bubble) so it doesn't
  // clutter the transcript but stays visible on history reload. Append
  // + return here — bypassing the generic role branches below.
  if (role === 'user' && extra && extra.source
      && extra.source.type === 'system'
      && extra.source.name === 'notification') {
    const notifEl = document.createElement('div');
    notifEl.className = 'msg notification-row';
    notifEl.style.cssText = (
      'background:#1a3a2a;color:#4ecdc4;border-left:3px solid #4ecdc4;'
      + 'padding:6px 12px;margin:4px 0;border-radius:4px;'
      + 'font-size:12.5px;display:flex;align-items:baseline;gap:8px;'
    );
    const fromAgent = displayAgentName(extra.source.agent || 'agent');
    const notifSortTs = _messageSortTs(extra);
    const timeHtml = makeTimeHtml(notifSortTs);
    if (extra && extra.msg_id) notifEl.dataset.msgid = extra.msg_id;
    notifEl.innerHTML = (
      '<span style="font-size:14px;">🔔</span>'
      + '<span style="font-weight:600;">' + escapeHtml(fromAgent) + ':</span>'
      + '<span style="flex:1;">' + escapeHtml(text) + '</span>'
      + timeHtml
    );
    const notifContainer = document.getElementById('messages');
    const notifShouldScroll = isNearBottom();
    _insertMessageChronologically(notifContainer, notifEl, notifSortTs);
    trimLiveDisplayWindowIfAutoscrolling(notifShouldScroll);
    scrollBottom(notifShouldScroll);
    return notifEl;
  }
  if (role === 'tool_call' || role === 'tool') {
    const rawToolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
    const rawToolArgs = (extra && extra.arguments !== undefined)
      ? extra.arguments
      : ((extra && extra.tool_args) || {});
    if (!_hasCompleteMcpDisplayedToolCall(rawToolName, rawToolArgs)) return null;
  }
  const el = document.createElement('div');
  // Map roles to CSS classes
  let cssClass = role;
  if (role === 'tool_call' || role === 'tool_result') cssClass = 'tool';
  else if (role === 'assistant' && extra && extra.source && extra.source.type === 'agent') {
    const srcName = (extra.source.name || '').toLowerCase();
    if (srcName) cssClass = 'subagent';
  }
  el.className = 'msg ' + cssClass;
  el.dataset.messageRole = role;
  if (role === 'assistant' || role === 'user') el.dataset.technicalBoundary = '1';
  if ((role === 'assistant' || role === 'user') && !String(text || '').trim()) el.dataset.transientUi = '1';
  if (extra && extra.live) el.dataset.live = '1';
  if ((role === 'user' || role === 'assistant') && String(text || '').trim()) collapseTechnicalGroups();
  if (msgId) el.dataset.msgid = msgId;
  el.dataset.insertedAt = String(Date.now());
  el.addEventListener('click', function(e) {
    if (e.ctrlKey || e.shiftKey) { e.preventDefault(); toggleMsgSelect(this, e); }
  });
  // Multi-part content (user messages with attachments after reload)
  let _attachHtml = '';
  if (Array.isArray(text)) {
    const _textParts = [];
    for (const _p of text) {
      if (_p.type === 'text') _textParts.push(_p.text || '');
      else if (_p.type === 'image_ref' && _p.file_id)
        _attachHtml += '<img class="chat-image" src="/files/' + encodeURIComponent(_p.file_id) + '/' + encodeURIComponent(_p.filename || 'image') + '">';
      else if (_p.type === 'file_ref' && _p.file_id)
        _attachHtml += '<span class="doc-badge">\u{1F4CE} ' + escapeHtml(_p.filename || 'file') + '</span> ';
    }
    text = _textParts.join('\n');
  }
  if (extra && Array.isArray(extra.attachments) && extra.attachments.length) {
    _attachHtml += renderUserAttachments(extra.attachments);
  }
  el.dataset.rawText = (text || '').substring(0, 500);
  if (extra && extra.raw_index !== undefined) el.dataset.rawIndex = extra.raw_index;
  const badge = (extra && extra.source) ? sourceBadge(extra.source) : '';
  // Timestamp — use provided timestamp or current time
  const _ts = _messageSortTs(extra);
  const timeHtml = makeTimeHtml(_ts);

  // Action buttons (copy + delete + reply) for all user-visible messages
  let actionsHtml = '';
  if (role === 'user' || role === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="setReplyTo(this)" title="' + escapeHtml(t('reply')) + '">\u21A9</button>'
      + (role === 'assistant' ? '<button onclick="speakMsg(this)" title="' + escapeHtml(t('readMessage')) + '">\u{1F50A}</button>' : '')
      + '<button onclick="copyMsg(this)" title="' + escapeHtml(t('copy')) + '">\u{1F4CB}</button>'
      + '<button onclick="copyMsgId(this)" title="' + escapeHtml(t('copyMsgId')) + '">ID</button>'
      + '<button onclick="restartFromMsg(this)" title="' + escapeHtml(t('restartFromHere')) + '">\u21BA</button>'
      + '<button onclick="deleteMsg(this)" title="' + escapeHtml(t('delete')) + '">\u{1F5D1}</button>'
      + '</span>';
  }

  // Reply-to quote (if this message is a reply)
  let replyQuoteHtml = '';
  if (extra && extra.source && extra.source.reply_to) {
    const rt = extra.source.reply_to;
    const rtAgent = rt.agent || rt.role || '';
    const rtPreview = (rt.text_preview || '').substring(0, 100);
    if (rtPreview) {
      const rtIdx = rt.raw_index !== undefined ? rt.raw_index : -1;
      replyQuoteHtml = '<div class="reply-quote" ' + (rtIdx >= 0 ? 'onclick="scrollToMessage(' + rtIdx + ')"' : '') + '>'
        + '\u21A9 ' + escapeHtml(rtAgent) + ': "' + escapeHtml(rtPreview) + '"</div>';
    }
  }

  // agent_delegate source = private channel between two agents.
  // Render as a compact delegate block regardless of the underlying
  // message role (delegate request is user-role to ingest into target,
  // delegate reply is assistant-role from the target).
  const _isDelegateMsg = extra && extra.source
      && (extra.source.type === 'agent_delegate')
      && window.PAWFLOW_GROUP_DELEGATE_MESSAGES !== false;
  pawflowDebugLog('[delegate-render]', role, 'isDelegate=', _isDelegateMsg, 'source=', extra && extra.source);
  if (_isDelegateMsg) {
    const _from = extra.source.from || '?';
    const _to = extra.source.to || '?';
    // Bidirectional key: both A→B and B→A land in the same block.
    const _pair = [_from, _to].map(s => s.toLowerCase()).sort();
    const _key = 'delegate-shared::' + _pair[0] + '::' + _pair[1];
    let _existing = document.querySelector('[data-delegate-key="' + CSS.escape(_key) + '"]');
    const _inner = document.createElement('div');
    _inner.className = 'delegate-message msg-inner-' + role;
    if (role === 'tool_call' || role === 'tool') {
      let toolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
      // Prefer `arguments` (full dict) over `tool_args` (500-char JSON
      // string — invalid parse on long commands → `Bash()`). Same fix
      // as the non-delegate branch below.
      const toolArgs = (extra && extra.arguments) || (extra && extra.tool_args) || {};
      let args = toolArgs;
      const normalized = _unwrapDisplayedToolCall(toolName, args);
      toolName = normalized.toolName;
      args = normalized.toolArgs;
      const tcId = (extra && extra.tc_id) || '';
      if (tcId) _inner.dataset.tcId = tcId;
      _inner.dataset.tool = toolName;
      const origin = _toolOriginValue(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
      if (origin) _inner.dataset.toolOrigin = origin;
      // Tag the agent that owns this tool so the `done` handler can
      // scope its pending-bullet cleanup correctly.
      const _ownerAgent = (extra && (extra.agent_name
          || (extra.source && extra.source.from))) || '';
      if (_ownerAgent) _inner.dataset.agent = String(_ownerAgent).toLowerCase();
      if (args && args.path) _inner.dataset.path = args.path;
      if (args && args.command) _inner.dataset.command = args.command.substring(0, 200);
      const isLive = extra && extra.live;
      const originBadge = _toolOriginBadge(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
      const bulletClass = isLive ? 'pending' : 'done';
      const bgBtn = (tcId && isLive) ? ' <button class="tc-bg-btn" onclick="backgroundTool(\'' + tcId + '\')" title="' + escapeHtml(t('runInBackground')) + '">\u2192 BG</button>' : '';
      const klBtn = (tcId && isLive) ? ' <button class="tc-kl-btn" onclick="killTool(\'' + tcId + '\')" title="' + escapeHtml(t('kill')) + '">\u2718</button>' : '';
      if (toolName === 'edit' && args && args.path) {
        _inner.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallEdit('', args) + bgBtn + klBtn;
      } else if (toolName === 'apply_patch' && args && args.path) {
        _inner.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallPatch('', args) + bgBtn + klBtn;
      } else {
        _inner.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + '<span class="tc-summary">' + escapeHtml(_toolCallSummary(toolName, args || {})) + '</span>' + bgBtn + klBtn;
      }
    } else if (role === 'tool_result') {
      const tcId = (extra && extra.tc_id) || '';
      if (tcId) _inner.dataset.tcId = tcId;
      if (tcId) {
        const tcEl = findToolCallElement(tcId, _existing || document);
        if (tcEl) { _attachToolResult(tcEl, text || ''); el.style.display = 'none'; return el; }
      }
      _inner.innerHTML = timeHtml + '<pre class="tool-result">' + escapeHtml((text || '').substring(0, 2000)) + '</pre>';
    } else {
      // Append buildMetaLine so delegate replies show provider/model/
      // tokens/duration (fields preserved by agent_core._append when
      // re-stamping the source as agent_delegate/kind=reply).
      _inner.innerHTML = timeHtml + renderMarkdown(text) + buildMetaLine(extra);
    }
    if (_existing) {
      const _body = _existing.querySelector('.delegate-body');
      if (_body) _body.appendChild(_inner);
      el.style.display = 'none';
      el._delegateInner = _inner;
      return _inner;
    }
    const _arrow = '\u{1F500} <span class="delegate-src">'
        + escapeHtml(displayAgentName(_from)) + '</span> \u2192 '
        + '<span class="delegate-dst">'
        + escapeHtml(displayAgentName(_to)) + '</span>';
    el.className = 'msg delegate-block delegate-shared';
    el.dataset.delegateKey = _key;
    const _body = document.createElement('div');
    _body.className = 'delegate-body';
    _body.appendChild(_inner);
    const _details = document.createElement('details');
    _details.open = true;
    const _summary = document.createElement('summary');
    _summary.className = 'delegate-header';
    _summary.innerHTML = _arrow + timeHtml;
    _details.appendChild(_summary);
    _details.appendChild(_body);
    el.appendChild(_details);
    el._delegateInner = _inner;
  } else if (role === 'assistant') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
  } else if (role === 'tool_call' || role === 'tool') {
    let toolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
    // Prefer `arguments` (untouched dict from classify) over `tool_args`
    // (JSON string truncated to 500 chars — invalid on long bash commands,
    // JSON.parse throws, summary shows `Bash()`). Fall back to the string
    // form only when `arguments` is missing.
    const toolArgs = (extra && extra.arguments) || (extra && extra.tool_args) || {};
    let args = toolArgs;
    const normalized = _unwrapDisplayedToolCall(toolName, args);
    toolName = normalized.toolName;
    args = normalized.toolArgs;
    const tcId = (extra && extra.tc_id) || '';
    if (tcId) el.dataset.tcId = tcId;
    el.dataset.tool = toolName;
    const origin = _toolOriginValue(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
    if (origin) el.dataset.toolOrigin = origin;
    if (args && args.path) el.dataset.path = args.path;
    if (args && args.command) el.dataset.command = args.command.substring(0, 200);

    const isLive = extra && extra.live;
    const originBadge = _toolOriginBadge(extra, (extra && (extra.tool_name || extra.tool)) || toolName);
    const bulletClass = isLive ? 'pending' : 'done';
    const bgBtn = (tcId && isLive) ? ' <button class="tc-bg-btn" onclick="backgroundTool(\'' + tcId + '\')" title="' + escapeHtml(t('runInBackground')) + '">\u2192 BG</button>' : '';
    const klBtn = (tcId && isLive) ? ' <button class="tc-kl-btn" onclick="killTool(\'' + tcId + '\')" title="' + escapeHtml(t('kill')) + '">\u2718</button>' : '';
    if (toolName === 'edit' && args && args.path) {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallEdit('', args) + bgBtn + klBtn;
    } else if (toolName === 'apply_patch' && args && args.path) {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + _renderToolCallPatch('', args) + bgBtn + klBtn;
    } else {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + originBadge + '<span class="tc-summary">' + escapeHtml(_toolCallSummary(toolName, args || {})) + '</span>' + bgBtn + klBtn;
    }
  } else if (role === 'tool_result') {
    const tcId = (extra && extra.tc_id) || '';
    if (tcId) el.dataset.tcId = tcId;
    let resultText = text || '';
    if (typeof resultText !== 'string') {
      try { resultText = JSON.stringify(resultText, null, 2); }
      catch (_err) { resultText = String(resultText || ''); }
    }
    // Try to attach to the matching tool_call element
    if (tcId) {
      const tcEl = findToolCallElement(tcId);
      if (tcEl) {
        _attachToolResult(tcEl, resultText);
        el.style.display = 'none';  // hide this standalone element
        return el;
      }
    }
    // Fallback: standalone tool_result (no matching tool_call found)
    const toolName = (extra && extra.tool_name) || (extra && extra.tool) || '';
    const display = _TOOL_DISPLAY[toolName] || toolName;
    const firstLine = resultText.split('\n')[0].substring(0, 120);
    const rendered = _renderToolOutput(resultText);
    const inlineMedia = _extractInlineMedia(resultText);
    // Reload: always collapsed, but pull out inline media so it stays visible
    el.innerHTML = timeHtml + '<span class="tc-bullet done">\u25cf</span> ' + escapeHtml(display)
      + '<div class="tc-result">' + inlineMedia
      + '<details><summary>\u23bf ' + escapeHtml(firstLine) + '</summary>'
      + rendered + '</details></div>';
  } else if (role === 'thinking') {
    // Collapsible thinking block (same as SSE thinking_content)
    el.className = 'msg thinking-block';
    el.style.cssText = 'margin:4px 0;border-left:3px solid #6b7280;padding:4px 8px;opacity:0.7;';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.style.cssText = 'cursor:pointer;font-size:12px;color:#6b7280;user-select:none;';
    summary.textContent = t('thought');
    details.appendChild(summary);
    const content = document.createElement('div');
    content.style.cssText = 'font-size:12px;color:#9ca3af;font-style:italic;white-space:pre-wrap;max-height:300px;overflow-y:auto;';
    content.textContent = text;
    details.appendChild(content);
    el.innerHTML = '';
    el.appendChild(details);
  } else if (role === 'user') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + escapeHtml(text) + _attachHtml;
  } else if (role === 'sub_agent_trace') {
    if (window.PAWFLOW_GROUP_DELEGATE_MESSAGES === false) {
      el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
      return el;
    }
    const dtcId = (extra && extra.source && extra.source.delegate_tc_id) || '';
    const taskId = (extra && extra.source && extra.source.task_id) || '';
    // Dedupe: if the live SSE already rendered a sub-block for this
    // sub-agent task, skip — we'd otherwise render a stale duplicate
    // (e.g. the "running" snapshot side-by-side with the "done" one).
    if (taskId && document.querySelector('[data-delegate-task-id="' + taskId + '"]')) {
      return null;
    }
    const existingGroup = dtcId ? document.querySelector('[data-delegate-group="' + dtcId + '"]') : null;
    if (existingGroup) {
      const groupBody = existingGroup.querySelector('.delegate-body');
      // On second trace, convert the inline content of the first trace into a sub-block
      if (groupBody && !existingGroup.querySelector('.delegate-sub-block')) {
        const firstSubEl = document.createElement('details');
        firstSubEl.className = 'delegate-sub-block';
        firstSubEl.setAttribute('open', '');
        const firstAgent = existingGroup.dataset.firstAgent || 'sub-agent';
        const firstSvc = existingGroup.dataset.firstSvc || '';
        const svcL = firstSvc ? ' via ' + escapeHtml(firstSvc) : '';
        firstSubEl.innerHTML = '<summary class="delegate-sub-header">\u25b8 '
          + '<span class="delegate-dst">' + escapeHtml(displayAgentName(firstAgent)) + '</span>'
          + svcL + '</summary>'
          + '<div class="delegate-sub-body">' + groupBody.innerHTML + '</div>';
        groupBody.innerHTML = '';
        groupBody.appendChild(firstSubEl);
        // Update group header to show "Delegate (N agents)"
        const parentAgent = (extra && extra.source && extra.source.parent_agent) || existingGroup.dataset.parentAgent || '';
        const summaryEl = existingGroup.querySelector('.delegate-header');
        if (summaryEl) {
          summaryEl.innerHTML = '\u{1F500} ' + escapeHtml(displayAgentName(parentAgent))
            + ' \u2192 Delegate (<span class="delegate-group-count">2 agents</span>)';
        }
      }
      // Add new sub-block
      const subHtml = renderDelegateSubBlock(text, extra);
      const subEl = document.createElement('details');
      subEl.className = 'delegate-sub-block';
      subEl.innerHTML = subHtml;
      if (groupBody) groupBody.appendChild(subEl);
      const countSpan = existingGroup.querySelector('.delegate-group-count');
      if (countSpan) {
        const n = existingGroup.querySelectorAll('.delegate-sub-block').length;
        countSpan.textContent = n + ' agents';
      }
      return existingGroup;
    }
    // First trace for this delegate_tc_id — create group
    el.className = 'msg delegate-block delegate-group';
    if (dtcId) el.dataset.delegateGroup = dtcId;
    if (taskId) el.dataset.delegateTaskId = taskId;
    const src = (extra && extra.source) || {};
    el.dataset.firstAgent = src.name || 'sub-agent';
    el.dataset.firstSvc = src.llm_service || '';
    el.dataset.parentAgent = src.parent_agent || '';
    el.innerHTML = renderDelegateBlock(text, extra);
  } else if (role === 'error') {
    el.innerHTML = timeHtml + badge + renderMarkdown(text);
  } else if (role === 'agent-result') {
    const agentName = (extra && typeof extra === 'string') ? extra : '';
    el.innerHTML = timeHtml + (agentName ? '<strong>' + escapeHtml(agentName) + ':</strong> ' : '') + renderMarkdown(text);
  } else if (extra && extra.html) {
    el.innerHTML = timeHtml + text;
  } else {
    el.innerHTML = timeHtml + escapeHtml(text);
  }
  // Check near-bottom BEFORE appending so new element doesn't shift the threshold
  const shouldScroll = isNearBottom();
  const container = document.getElementById('messages');
  _insertMessageChronologically(container, el, _ts);
  trimLiveDisplayWindowIfAutoscrolling(shouldScroll);
  scrollBottom(shouldScroll);
  // Syntax highlighting via highlight.js (if loaded)
  if (typeof hljs !== 'undefined') {
    el.querySelectorAll('pre code').forEach(function(block) { hljs.highlightElement(block); });
  }
  // Re-scroll when images finish loading (they change height after initial render)
  if (shouldScroll) {
    for (const img of el.querySelectorAll('img')) {
      img.addEventListener('load', () => scrollBottom(true), { once: true });
    }
  }
  if (window.PAWFLOW_GROUP_TECHNICAL_MESSAGES) applyTechnicalMessageGrouping();
  if (window._pawflowExtRuntime) {
    window._pawflowExtRuntime.fireHook('message_appended', {
      role: role,
      conversationId: (typeof conversationId !== 'undefined' ? conversationId : null),
      msgId: (extra && extra.msg_id) || '',
      ts: (extra && extra.ts) || 0,
      source: (extra && extra.source) || null,
    });
  }
  return el;
}

