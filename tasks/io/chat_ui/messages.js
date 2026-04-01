const _TOOL_DISPLAY = {
  bash: 'Bash', read: 'Read', write: 'Write', edit: 'Update',
  glob: 'Glob', grep: 'Grep', delete: 'Delete', mkdir: 'Mkdir',
  stat: 'Stat', exists: 'Exists', list_dir: 'ListDir',
  batch_edit: 'BatchEdit', apply_patch: 'ApplyPatch',
  find_replace: 'FindReplace', notebook_edit: 'NotebookEdit',
  copy: 'Copy', execute_script: 'Script',
  web_search: 'WebSearch', fetch: 'Fetch',
  generate_image: 'ImageGen', generate_video: 'VideoGen',
  remember: 'Remember', recall: 'Recall', semantic_recall: 'SemanticRecall',
  forget: 'Forget', delegate: 'Delegate',
  show_file: 'ShowFile', compact_result: 'CompactResult',
  get_tool_schema: 'GetToolSchema',
};

function _toolCallSummary(name, args) {
  const display = _TOOL_DISPLAY[name] || name;
  // Build summary from actual args sent (not hardcoded param names)
  let summary = '';
  if (args && typeof args === 'object') {
    const keys = Object.keys(args);
    if (keys.length === 0) {
      summary = '';
    } else if (keys.length === 1) {
      // Single arg: show value directly (truncated)
      const val = String(args[keys[0]]);
      summary = val.length > 200 ? val.substring(0, 200) + '...' : val;
    } else {
      // Multiple args: show key=value pairs (truncated)
      const parts = [];
      let total = 0;
      for (const k of keys) {
        const val = String(args[k]);
        const short = val.length > 80 ? val.substring(0, 80) + '...' : val;
        const part = k + '=' + short;
        if (total + part.length > 200) { parts.push('...'); break; }
        parts.push(part);
        total += part.length;
      }
      summary = parts.join(', ');
    }
  }
  return display + '(' + summary + ')';
}

function sourceBadge(source) {
  if (!source) return '';
  const name = source.name ? displayAgentName(source.name) : '';
  const svc = source.llm_service || '';
  if (source.type === 'agent') {
    // Hash name to color
    let h = 0;
    for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0;
    const hue = Math.abs(h) % 360;
    let label = svc ? name + ' via ' + svc : name;
    if (source.containerized) label += ' \uD83D\uDC33';  // whale emoji = Docker
    if (source.reply_to) label += ' \u2192 ' + displayAgentName(source.reply_to);
    return '<span class="source-badge" style="background:hsl(' + hue + ',60%,25%);color:hsl(' + hue + ',80%,80%)">' + escapeHtml(label) + '</span> ';
  }
  if (source.type === 'user') {
    let userLabel = (name && name !== 'anonymous') ? name : '';
    const target = source.target_agent;
    const isBtw = source.btw;
    if (target) {
      const prefix = isBtw ? '[btw \u2192 ' : '[\u2192 ';
      userLabel = (userLabel ? userLabel + ' ' : '') + prefix + displayAgentName(target) + ']';
    } else if (isBtw) {
      userLabel = (userLabel ? userLabel + ' ' : '') + 'btw';
    }
    if (userLabel) {
      return '<span class="source-badge" style="background:#1a3a2a;color:#4ecdc4">' + escapeHtml(userLabel) + '</span> ';
    }
  }
  return '';
}

function buildMetaLine(extra) {
  if (!extra) return '';
  // Collect metadata parts: model, provider, base_url, tokens, duration
  // Also check source object for provider/base_url (from persisted messages)
  const src = extra.source || {};
  const model = extra.model || src.model || '';
  const provider = extra.provider || src.provider || '';
  const baseUrl = extra.base_url || src.base_url || '';
  const tokIn = extra.tokens_in || src.tokens_in || 0;
  const tokOut = extra.tokens_out || src.tokens_out || 0;
  const dur = extra.duration_ms || 0;
  const costUsd = extra.cost_usd || 0;
  const parts = [];
  if (model) parts.push(model);
  if (provider && provider !== model) parts.push(provider);
  if (tokIn || tokOut) parts.push('\u2191' + tokIn + ' \u2193' + tokOut);
  if (costUsd) parts.push('$' + costUsd.toFixed(4));
  if (dur) parts.push((dur / 1000).toFixed(1) + 's');
  if (!parts.length) return '';
  // Compact summary line (always visible)
  let line = '<span class="meta-summary">' + parts.join(' \u00b7 ') + '</span>';
  // Expandable details
  const details = [];
  if (baseUrl) details.push('endpoint: ' + escapeHtml(baseUrl));
  if (tokIn || tokOut) details.push('tokens: ' + tokIn + ' in / ' + tokOut + ' out (' + (tokIn + tokOut) + ' total)');
  if (costUsd) details.push('cost: $' + costUsd.toFixed(6));
  if (dur) details.push('duration: ' + (dur / 1000).toFixed(1) + 's');
  if (details.length) {
    line += '<span class="meta-details">' + details.join(' \u00b7 ') + '</span>';
  }
  return '<div class="msg-meta" onclick="this.classList.toggle(\'expanded\')">' + line + '</div>';
}


function makeTimeHtml(tsEpoch) {
  const msgTime = tsEpoch ? new Date(tsEpoch * 1000) : new Date();
  const _today = new Date();
  const _sameDay = msgTime.toDateString() === _today.toDateString();
  const timeStr = _sameDay
    ? msgTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})
    : msgTime.toLocaleDateString([], {day: '2-digit', month: '2-digit'}) + ' '
      + msgTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  return '<span class="msg-time">' + timeStr + '</span>';
}

function addMsg(role, text, extra) {
  // Dedup by msg_id — if we've already displayed this message, skip
  const msgId = (extra && extra.msg_id) || '';
  if (msgId) {
    if (_seenMsgIds.has(msgId)) {
      console.log('[dedup] skipping duplicate msg_id:', msgId);
      return null;
    }
    _seenMsgIds.add(msgId);
  }
  const el = document.createElement('div');
  // Map roles to CSS classes
  let cssClass = role;
  if (role === 'tool_call' || role === 'tool_result') cssClass = 'tool';
  else if (role === 'narration') cssClass = 'narration';
  else if (role === 'assistant' && extra && extra.source && extra.source.type === 'agent') {
    const srcName = (extra.source.name || '').toLowerCase();
    if (srcName) cssClass = 'subagent';
  }
  el.className = 'msg ' + cssClass;
  if (msgId) el.dataset.msgid = msgId;
  el.addEventListener('click', function(e) {
    if (e.ctrlKey || e.shiftKey) { e.preventDefault(); toggleMsgSelect(this, e); }
  });
  el.dataset.rawText = (text || '').substring(0, 500);
  if (extra && extra.raw_index !== undefined) el.dataset.rawIndex = extra.raw_index;
  const badge = (extra && extra.source) ? sourceBadge(extra.source) : '';
  // Timestamp — use provided timestamp or current time
  const _ts = extra && (extra.timestamp || extra.ts);
  const timeHtml = makeTimeHtml(_ts || 0);

  // Action buttons (copy + delete + reply) for all user-visible messages
  let actionsHtml = '';
  if (role === 'user' || role === 'assistant') {
    actionsHtml = '<span class="msg-actions">'
      + '<button onclick="setReplyTo(this)" title="Reply">\u21A9</button>'
      + '<button onclick="copyMsg(this)" title="Copy">\u{1F4CB}</button>'
      + '<button onclick="deleteMsg(this)" title="Delete">\u{1F5D1}</button>'
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

  if (role === 'assistant') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + renderMarkdown(text) + buildMetaLine(extra);
  } else if (role === 'tool_call' || role === 'tool') {
    const toolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
    const toolArgs = (extra && extra.tool_args) || (extra && extra.arguments) || {};
    let args = toolArgs;
    if (typeof args === 'string') { try { args = JSON.parse(args); } catch(e) {} }
    const tcId = (extra && extra.tc_id) || '';
    if (tcId) el.dataset.tcId = tcId;
    el.dataset.tool = toolName;
    if (args && args.path) el.dataset.path = args.path;
    if (args && args.command) el.dataset.command = args.command.substring(0, 200);

    const isLive = extra && extra.live;
    const bulletClass = isLive ? 'pending' : 'done';
    const bgBtn = (tcId && isLive) ? ' <button class="tc-bg-btn" onclick="backgroundTool(\'' + tcId + '\')" title="Run in background">\u2192 BG</button>' : '';
    const klBtn = (tcId && isLive) ? ' <button class="tc-kl-btn" onclick="killTool(\'' + tcId + '\')" title="Kill">\u2718</button>' : '';
    if (toolName === 'edit' && args && args.path) {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + _renderToolCallEdit('', args) + bgBtn + klBtn;
    } else {
      el.innerHTML = timeHtml + '<span class="tc-bullet ' + bulletClass + '">\u25cf</span> ' + escapeHtml(_toolCallSummary(toolName, args || {})) + bgBtn + klBtn;
    }
  } else if (role === 'tool_result') {
    const tcId = (extra && extra.tc_id) || '';
    const resultText = text || '';
    // Try to attach to the matching tool_call element
    if (tcId) {
      const tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
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
    // Reload: always collapsed
    el.innerHTML = timeHtml + '<span class="tc-bullet done">\u25cf</span> ' + escapeHtml(display)
      + '<div class="tc-result"><details><summary>\u23bf ' + escapeHtml(firstLine) + '</summary>'
      + rendered + '</details></div>';
  } else if (role === 'thinking') {
    // Collapsible thinking block (same as SSE thinking_content)
    el.className = 'msg thinking-block';
    el.style.cssText = 'margin:4px 0;border-left:3px solid #6b7280;padding:4px 8px;opacity:0.7;';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.style.cssText = 'cursor:pointer;font-size:12px;color:#6b7280;user-select:none;';
    summary.textContent = 'Thought';
    details.appendChild(summary);
    const content = document.createElement('div');
    content.style.cssText = 'font-size:12px;color:#9ca3af;font-style:italic;white-space:pre-wrap;max-height:300px;overflow-y:auto;';
    content.textContent = text;
    details.appendChild(content);
    el.innerHTML = '';
    el.appendChild(details);
  } else if (role === 'narration') {
    const srcN = (extra && extra.source) ? displayAgentName(extra.source.name || '') : '';
    const src = extra && extra.source ? extra.source : {type: 'agent', name: srcN};
    el.innerHTML = timeHtml + sourceBadge(src) + '<em>' + escapeHtml(text) + '</em>';
  } else if (role === 'user') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + escapeHtml(text);
  } else if (role === 'sub_agent_trace') {
    el.innerHTML = timeHtml + renderSubAgentTrace(text, extra);
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
  // Insert before typing indicator so it always stays at the bottom
  const typingEl = document.getElementById('typing');
  if (typingEl) {
    container.insertBefore(el, typingEl);
  } else {
    container.appendChild(el);
  }
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
  return el;
}

function escapeHtml(t) {
  const d = document.createElement('div');
  d.textContent = t;
  return d.innerHTML;
}

var _diffLangMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',jsx:'javascript',tsx:'typescript',vue:'xml',svelte:'xml'};

function _synLine(code, lang) {
  // Syntax-highlight a single line of code (returns HTML)
  if (!lang || typeof hljs === 'undefined') return escapeHtml(code);
  try { return hljs.highlight(code, {language: lang, ignoreIllegals: true}).value; }
  catch(e) { return escapeHtml(code); }
}

function _renderToolOutput(text, toolHint, pathHint) {
  // Smart rendering with highlight.js for syntax highlighting
  const lines = text.split('\n');
  const _ext = pathHint ? (pathHint.split('.').pop() || '').toLowerCase() : '';
  const _hljs = typeof hljs !== 'undefined';

  // Extension → highlight.js language mapping
  const _langMap = {
    py:'python', js:'javascript', ts:'typescript', jsx:'javascript', tsx:'typescript',
    java:'java', go:'go', rs:'rust', rb:'ruby', c:'c', cpp:'cpp', cs:'csharp',
    php:'php', sh:'bash', bash:'bash', yaml:'yaml', yml:'yaml', toml:'ini',
    sql:'sql', html:'html', css:'css', xml:'xml', json:'json', md:'markdown',
    dockerfile:'dockerfile', makefile:'makefile',
  };

  // Detect diff
  const diffLines = lines.filter(l => /^\s*\d*\s*[+-] /.test(l) || l.startsWith('+ ') || l.startsWith('- '));
  const isDiff = diffLines.length >= 2 && (
    text.includes('replacement') || text.includes('Edited ') || text.includes('Written ')
    || text.includes('@@') || text.includes('diff ') || lines.some(l => l.startsWith('---') || l.startsWith('+++')));
  if (isDiff) {
    return '<pre class="tc-output"><code class="language-diff hljs">' + lines.map(l => {
      const s = l.trimStart();
      if (s.startsWith('+ ') || /^\s*\d+\s+\+ /.test(l)) return '<span class="hljs-addition">' + escapeHtml(l) + '</span>';
      if (s.startsWith('- ') || /^\s*\d+\s+- /.test(l)) return '<span class="hljs-deletion">' + escapeHtml(l) + '</span>';
      if (s.startsWith('@@')) return '<span class="hljs-meta">' + escapeHtml(l) + '</span>';
      return escapeHtml(l);
    }).join('\n') + '</code></pre>';
  }

  // Detect markdown
  if (/^```|^\#{1,3} |^\*\*|^\- \[/.test(text) || text.includes('\n```')) {
    return '<div class="tc-md">' + renderMarkdown(text) + '</div>';
  }

  // Detect file read with line numbers → extract code, detect language, highlight
  const hasLineNumbers = lines.length > 3 && lines.filter(l => /^\s*\d+\t/.test(l)).length > lines.length * 0.5;
  if (hasLineNumbers && _hljs) {
    const lang = _langMap[_ext] || '';
    // Separate line numbers from code
    const codeLines = lines.map(l => {
      const m = l.match(/^(\s*\d+)\t(.*)$/);
      return m ? m[2] : l;
    });
    const nums = lines.map(l => {
      const m = l.match(/^(\s*\d+)\t/);
      return m ? m[1] : '';
    });
    let highlighted;
    try {
      highlighted = lang
        ? hljs.highlight(codeLines.join('\n'), {language: lang, ignoreIllegals: true}).value
        : hljs.highlightAuto(codeLines.join('\n')).value;
    } catch(e) { highlighted = escapeHtml(codeLines.join('\n')); }
    // Re-inject line numbers
    const hLines = highlighted.split('\n');
    const final = hLines.map((hl, i) => '<span class="ln">' + (nums[i] || '') + '</span>\t' + hl).join('\n');
    return '<pre class="tc-output"><code class="hljs">' + final + '</code></pre>';
  }

  // JSON
  const trimmed = text.trim();
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    if (_hljs) {
      try {
        const h = hljs.highlight(text, {language: 'json', ignoreIllegals: true}).value;
        return '<pre class="tc-output"><code class="hljs language-json">' + h + '</code></pre>';
      } catch(e) {}
    }
    return '<pre class="tc-output">' + escapeHtml(text) + '</pre>';
  }

  // Code file by extension
  if (_ext && _langMap[_ext] && _hljs) {
    try {
      const h = hljs.highlight(text, {language: _langMap[_ext], ignoreIllegals: true}).value;
      return '<pre class="tc-output"><code class="hljs language-' + _langMap[_ext] + '">' + h + '</code></pre>';
    } catch(e) {}
  }

  // Grep results
  if (toolHint === 'grep' || toolHint === 'glob') {
    return '<pre class="tc-output">' + lines.map(l => {
      const m = l.match(/^([^:]+:\d+:)\s*(.*)$/);
      if (m) return '<span class="grep-loc">' + escapeHtml(m[1]) + '</span> ' + escapeHtml(m[2]);
      return escapeHtml(l);
    }).join('\n') + '</pre>';
  }

  // Git output (commit, log, show) — auto-detect
  if (_hljs && (toolHint === 'bash' || !toolHint)) {
    const isGit = lines.some(l => /^commit [0-9a-f]{40}/.test(l) || /^Author:/.test(l));
    if (isGit) {
      return '<pre class="tc-output">' + lines.map(l => {
        if (/^commit [0-9a-f]/.test(l)) return '<span class="hljs-string">' + escapeHtml(l) + '</span>';
        if (/^Author:/.test(l)) return '<span class="hljs-attr">' + escapeHtml(l) + '</span>';
        if (/^Date:/.test(l)) return '<span class="hljs-comment">' + escapeHtml(l) + '</span>';
        if (l.startsWith('    ')) return '<span class="hljs-title">' + escapeHtml(l) + '</span>';
        return escapeHtml(l);
      }).join('\n') + '</pre>';
    }
    // Shell output: try auto-detect
    try {
      const h = hljs.highlightAuto(text, ['bash', 'shell', 'plaintext']).value;
      if (h !== escapeHtml(text)) {
        return '<pre class="tc-output"><code class="hljs">' + h + '</code></pre>';
      }
    } catch(e) {}
  }

  // Default
  return '<pre class="tc-output">' + escapeHtml(text) + '</pre>';
}

function _attachToolResult(tcEl, resultText) {
  const bullet = tcEl.querySelector('.tc-bullet');
  if (bullet) { bullet.classList.remove('pending'); bullet.classList.add('done'); }
  // Remove BG/KL buttons (tool is done)
  const bgBtn = tcEl.querySelector('.tc-bg-btn');
  if (bgBtn) bgBtn.remove();
  const klBtn = tcEl.querySelector('.tc-kl-btn');
  if (klBtn) klBtn.remove();
  const toolHint = tcEl.dataset.tool || '';
  const pathHint = tcEl.dataset.path || '';
  const resultDiv = document.createElement('div');
  resultDiv.className = 'tc-result';
  const firstLine = resultText.split('\n')[0].substring(0, 120);
  const rendered = _renderToolOutput(resultText, toolHint, pathHint);
  // Open while streaming, auto-collapse after 1.5s
  resultDiv.innerHTML = '<details open><summary>\u23bf ' + escapeHtml(firstLine)
    + '</summary>' + rendered + '</details>';
  tcEl.appendChild(resultDiv);
  // Auto-collapse after brief display
  const _det = resultDiv.querySelector('details');
  if (_det) setTimeout(() => { _det.removeAttribute('open'); }, 1500);
  // Auto-scroll
  if (isNearBottom()) {
    const container = document.getElementById('messages');
    if (container) container.scrollTop = container.scrollHeight;
  }
}

function backgroundTool(tcId) {
  if (!conversationId || !tcId) return;
  action$('background_tool', { tc_id: tcId }).subscribe(d => {
    if (d.ok) {
      const tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
      if (tcEl) {
        const btn = tcEl.querySelector('.tc-bg-btn');
        if (btn) btn.remove();
        const bullet = tcEl.querySelector('.tc-bullet');
        if (bullet) { bullet.classList.add('bg'); bullet.title = 'Running in background'; }
        // Add Kill button
        const klBtn = document.createElement('button');
        klBtn.className = 'tc-kl-btn';
        klBtn.onclick = () => killTool(tcId);
        klBtn.title = 'Kill background task';
        klBtn.textContent = '\u2717 KL';
        const preEl = tcEl.querySelector('pre');
        if (preEl) tcEl.insertBefore(klBtn, preEl);
        else tcEl.appendChild(klBtn);
      }
    }
  });
}

function killTool(tcId) {
  if (!conversationId || !tcId) return;
  // Optimistic UI: mark as killed
  const tcEl = document.querySelector('[data-tc-id="' + tcId + '"]');
  if (tcEl) {
    tcEl.querySelectorAll('.tc-kl-btn, .tc-bg-btn').forEach(b => b.remove());
    const bullet = tcEl.querySelector('.tc-bullet');
    if (bullet) { bullet.classList.remove('bg', 'pending'); bullet.classList.add('done'); bullet.style.color = '#e94560'; bullet.title = 'Killed'; }
  }
  // Kill via tool relay (in-flight tools) AND background system
  fireAction('kill_tool', { tc_id: tcId });
  cancelBgTool(tcId);
}

function cancelBgTool(tcId) {
  if (!conversationId || !tcId) return;
  fireAction('cancel_bg_tool', { tc_id: tcId });
}

function _renderToolCallEdit(srcLabel, args) {
  const fpath = args.path || '?';
  const oldStr = args.old_string || '';
  const newStr = args.new_string || '';
  const startLn = args.start_line || '';
  const endLn = args.end_line || '';
  let editHtml = '<span style="color:#4ecdc4;font-size:11px">\u270E [' + escapeHtml(srcLabel) + '] Edit(' + escapeHtml(fpath) + ')</span>';
  if (startLn && endLn) {
    editHtml += '<span style="color:#8b949e;font-size:11px"> lines ' + startLn + '-' + endLn + '</span>';
  }
  const _ext = fpath.split('.').pop().toLowerCase();
  const _langMap = {js:'javascript',ts:'typescript',py:'python',rb:'ruby',rs:'rust',go:'go',java:'java',cpp:'cpp',c:'c',cs:'csharp',php:'php',sh:'bash',bash:'bash',json:'json',html:'xml',xml:'xml',css:'css',sql:'sql',yaml:'yaml',yml:'yaml',md:'markdown',jsx:'javascript',tsx:'typescript',vue:'xml',svelte:'xml'};
  const _lang = _langMap[_ext] || '';
  const oldLines = oldStr ? oldStr.split('\n') : [];
  const newLines = newStr ? newStr.split('\n') : [];
  let cpx = 0;
  while (cpx < oldLines.length && cpx < newLines.length && oldLines[cpx] === newLines[cpx]) cpx++;
  let csx = 0;
  while (csx < (oldLines.length - cpx) && csx < (newLines.length - cpx) && oldLines[oldLines.length - 1 - csx] === newLines[newLines.length - 1 - csx]) csx++;
  const diffLines = [];
  const ctxPrefix = oldLines.slice(Math.max(0, cpx - 2), cpx);
  ctxPrefix.forEach(l => { diffLines.push('<div><span style="color:#8b949e;user-select:none">  </span>' + _synLine(l, _lang) + '</div>'); });
  const removed = oldLines.slice(cpx, oldLines.length - csx);
  removed.slice(0, 6).forEach(l => { diffLines.push('<div style="background:rgba(248,81,73,0.15)"><span style="color:#f85149;user-select:none">- </span>' + _synLine(l, _lang) + '</div>'); });
  if (removed.length > 6) diffLines.push('<div style="color:#8b949e">  ... +' + (removed.length - 6) + ' lines removed</div>');
  const added = newLines.slice(cpx, newLines.length - csx);
  added.slice(0, 6).forEach(l => { diffLines.push('<div style="background:rgba(63,185,80,0.15)"><span style="color:#3fb950;user-select:none">+ </span>' + _synLine(l, _lang) + '</div>'); });
  if (added.length > 6) diffLines.push('<div style="color:#8b949e">  ... +' + (added.length - 6) + ' lines added</div>');
  const ctxSuffix = oldLines.slice(oldLines.length - csx, oldLines.length - csx + 2);
  ctxSuffix.forEach(l => { diffLines.push('<div><span style="color:#8b949e;user-select:none">  </span>' + _synLine(l, _lang) + '</div>'); });
  const _addedCount = added.length, _removedCount = removed.length;
  if (_addedCount || _removedCount) {
    const parts = [];
    if (_addedCount) parts.push(_addedCount + ' added');
    if (_removedCount) parts.push(_removedCount + ' removed');
    editHtml += '<span style="color:#8b949e;font-size:10px;margin-left:8px">(' + parts.join(', ') + ')</span>';
  }
  if (diffLines.length > 0) {
    editHtml += '<pre class="diff-output' + (_lang ? ' language-' + _lang : '') + '" style="margin:2px 0 0 0;font-size:11px">' + diffLines.join('') + '</pre>';
  }
  return editHtml;
}

function _renderDiff(text, filePath) {
  var lines = text.split('\n');
  var hasDiffLines = lines.some(function(l) {
    var s = l.trimStart();
    return s.startsWith('+ ') || s.startsWith('- ') || s.startsWith('@@');
  });
  var hasDiffContext = /replacement|edited |written |hunks/i.test(text);
  if (!hasDiffLines || !hasDiffContext) return null;

  // Detect language from file path for syntax coloring within diff lines
  var ext = (filePath || '').split('.').pop().toLowerCase();
  var lang = _diffLangMap[ext] || '';

  return '<pre class="diff-output">' + lines.map(function(line) {
    var s = line.trimStart();
    // Extract line number prefix and +/- marker, highlight only the code part
    var m = s.match(/^(\d+\s+)?([+-] )(.*)/);
    if (m) {
      var prefix = (m[1] || '') + m[2];
      var code = m[3];
      var bg = m[2].startsWith('+') ? 'rgba(63,185,80,0.1)' : 'rgba(248,81,73,0.1)';
      var markerColor = m[2].startsWith('+') ? '#3fb950' : '#f85149';
      return '<div style="background:' + bg + '"><span style="color:' + markerColor + ';user-select:none">' + escapeHtml(prefix) + '</span>' + _synLine(code, lang) + '</div>';
    }
    if (s.startsWith('+ ')) {
      return '<div style="background:rgba(63,185,80,0.1)"><span style="color:#3fb950;user-select:none">+ </span>' + _synLine(s.slice(2), lang) + '</div>';
    }
    if (s.startsWith('- ')) {
      return '<div style="background:rgba(248,81,73,0.1)"><span style="color:#f85149;user-select:none">- </span>' + _synLine(s.slice(2), lang) + '</div>';
    }
    if (s.startsWith('@@')) {
      return '<div><span style="color:#58a6ff">' + escapeHtml(line) + '</span></div>';
    }
    if (/^(Edited |Written |replacement)/i.test(s)) {
      return '<div><strong>' + escapeHtml(line) + '</strong></div>';
    }
    return '<div><span style="color:#8b949e">' + _synLine(line, lang) + '</span></div>';
  }).join('') + '</pre>';
}

function isImageFile(name) {
  return /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(name || '');
}

// Batch image loading: collect pending images, check availability in one call,
// then fetch only existing ones. Avoids 50+ sequential 404s blocking the page.
let _pendingImages = [];  // [{imgId, url}]
let _imageFlushTimer = null;

function _flushPendingImages() {
  _imageFlushTimer = null;
  const batch = _pendingImages.splice(0);
  if (!batch.length) return;
  const token = getToken();
  const headers = {};
  if (token) headers['Authorization'] = 'Bearer ' + token;
  // Extract file_ids from URLs: /files/{file_id}/filename
  const fileIds = [];
  const byId = {};
  for (const item of batch) {
    const m = item.url.match(/\/files\/([a-f0-9]+)\//);
    if (m) { fileIds.push(m[1]); byId[m[1]] = item; }
    else { byId[item.imgId] = item; fileIds.push(item.imgId); }
  }
  // Batch check: ask server which file_ids exist
  action$('check_files', { file_ids: fileIds }).subscribe({
    next: data => {
      const available = new Set(data.available || []);
      for (const fid of fileIds) {
        const item = byId[fid];
        if (!item) continue;
        const el = document.getElementById(item.imgId);
        if (!el) continue;
        const wrapper = el.closest('.img-wrapper');
        if (!available.has(fid)) {
          // File doesn't exist — hide entirely
          if (wrapper) wrapper.style.display = 'none';
          continue;
        }
        // File exists — fetch the blob
        fetch(item.url, { headers, credentials: 'same-origin' }).then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.blob();
        }).then(blob => {
          el.src = URL.createObjectURL(blob);
          el.style.display = 'block';
        }).catch(() => { if (wrapper) wrapper.style.display = 'none'; });
      }
    },
    error: () => {
      // Fallback: try each individually
      for (const item of batch) {
        const el = document.getElementById(item.imgId);
        if (!el) continue;
        const wrapper = el.closest('.img-wrapper');
        fetch(item.url, { headers, credentials: 'same-origin' }).then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.blob();
        }).then(blob => {
          el.src = URL.createObjectURL(blob);
          el.style.display = 'block';
        }).catch(() => { if (wrapper) wrapper.style.display = 'none'; });
      }
    },
  });
}

function inlineImageHtml(url, filename, sizeInfo) {
  // Render authenticated inline image (max 512px) with click-to-view
  const imgId = 'img_' + Math.random().toString(36).substring(2, 8);
  // Queue for batch loading (flushed after 100ms of no new images)
  _pendingImages.push({ imgId, url });
  if (_imageFlushTimer) clearTimeout(_imageFlushTimer);
  _imageFlushTimer = setTimeout(_flushPendingImages, 100);
  return '<div class="img-wrapper" style="margin:6px 0;">'
    + '<img id="' + imgId + '" style="display:none;max-width:512px;max-height:512px;border-radius:8px;cursor:pointer;border:1px solid #0f3460;" '
    + 'onclick="openFileViewer(\'' + url + '\')" title="Click to view full size" />'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83D\uDCC4 ' + escapeHtml(filename || 'image') + (sizeInfo ? ' (' + sizeInfo + ')' : '')
    + '</div></div>';
}

function renderMarkdown(text) {
  // Detect __show_file__ markers from show_file tool
  try {
    if (text.includes('__show_file__')) {
      const parsed = JSON.parse(text);
      if (parsed && parsed.__show_file__) {
        if (isImageFile(parsed.filename)) {
          return inlineImageHtml(parsed.url, parsed.filename, parsed.size_kb + ' KB');
        }
        setTimeout(() => openFileViewer(parsed.url), 100);
        return '<span style="cursor:pointer;color:#6c5ce7;" onclick="openFileViewer(\'' + parsed.url + '\')">\uD83D\uDCC4 ' + parsed.filename + ' (' + parsed.size_kb + ' KB) \u2014 Click to view</span>';
      }
    }
  } catch(e) {}
  // 1. Extract code blocks BEFORE escaping (preserve their content as-is)
  const _codeBlocks = [];
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
    var cls = lang ? ' class="language-' + lang + '"' : '';
    _codeBlocks.push('<pre><code' + cls + '>' + escapeHtml(code) + '</code></pre>');
    return '\x00CB' + (_codeBlocks.length - 1) + '\x00';
  });
  const _inlineCodes = [];
  text = text.replace(/`([^`]+)`/g, function(_, code) {
    _inlineCodes.push('<code>' + escapeHtml(code) + '</code>');
    return '\x00IC' + (_inlineCodes.length - 1) + '\x00';
  });
  // 2. Escape ALL remaining HTML (prevents XSS from any source)
  text = escapeHtml(text);
  // 3. Restore code blocks (already escaped internally)
  text = text.replace(/\x00CB(\d+)\x00/g, function(_, i) { return _codeBlocks[parseInt(i)]; });
  text = text.replace(/\x00IC(\d+)\x00/g, function(_, i) { return _inlineCodes[parseInt(i)]; });
  // Markdown links: [text](url) — must run BEFORE bare URL detection
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function(_, label, url) {
    if (url.match(/\/files\/[a-f0-9]+\//)) {
      if (isImageFile(label) || isImageFile(url)) {
        return inlineImageHtml(url, label, '');
      }
      return '<a class="flink" href="' + url + '" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'' + url + '\')">\uD83D\uDCC4 ' + label + '</a>';
    }
    return '<a href="' + url + '" target="_blank">' + label + '</a>';
  });
  // Bare file URLs (not already inside a tag attribute)
  text = text.replace(/(^|[\s>])(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g, function(_, pre, url, fname) {
    if (isImageFile(fname)) {
      return pre + inlineImageHtml(url, fname, '');
    }
    return pre + '<a class="flink" href="' + url + '" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();openFileViewer(\'' + url + '\')">\uD83D\uDCC4 ' + fname + '</a>';
  });
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // fs:// URLs — clickable links to filesystem files
  text = text.replace(/(fs:\/\/([^\s&<"']+))/g, function(_, url, rest) {
    const parts = rest.split('/');
    const service = parts[0];
    const fpath = parts.slice(1).join('/');
    const fname = parts[parts.length - 1] || fpath;
    const isDir = url.endsWith('/');
    const icon = isDir ? '\uD83D\uDCC1' : '\uD83D\uDCC4';
    return '<a class="flink" href="#" style="color:#6c5ce7;cursor:pointer;" onclick="event.preventDefault();fetchFsFile(\'' + service + '\',\'' + fpath + '\')">'
      + icon + ' ' + fname + '</a>';
  });
  // Bare URLs (skip those already inside HTML tags or attributes)
  // Split on existing tags (<a>, <img>, <div> with onclick, etc.) to avoid double-linking
  const parts = text.split(/(<[^>]+>)/gi);
  for (let i = 0; i < parts.length; i++) {
    // Only process text nodes (not inside any HTML tag)
    if (!parts[i].startsWith('<')) {
      parts[i] = parts[i].replace(/(https?:\/\/[^\s<"']+)/g, '<a href="$1" target="_blank">$1</a>');
    }
  }
  return parts.join('');
}

function renderSubAgentTrace(content, extra) {
  const source = (extra && extra.source) || {};
  const trace = (extra && extra.trace) || [];
  const traceId = (extra && extra.trace_id) || '';
  const agentName = source.name || 'sub-agent';
  // Summarize trace
  let totalTools = 0;
  let tokensIn = 0;
  let tokensOut = 0;
  for (const entry of trace) {
    if (entry.type === 'tool_call') totalTools++;
    if (entry.type === 'done') {
      tokensIn = entry.tokens_in || 0;
      tokensOut = entry.tokens_out || 0;
    }
  }
  const tokensK = ((tokensIn + tokensOut) / 1000).toFixed(1);
  const header = escapeHtml(displayAgentName(agentName))
    + ' \u00b7 ' + totalTools + ' tool use' + (totalTools !== 1 ? 's' : '')
    + ' \u00b7 ' + tokensK + 'k tokens';
  // Tool call list (first 3 shown, rest collapsed)
  const toolCalls = trace.filter(e => e.type === 'tool_call');
  let traceHtml = '';
  const showN = 3;
  for (let i = 0; i < Math.min(showN, toolCalls.length); i++) {
    const tc = toolCalls[i];
    const display = (_TOOL_DISPLAY[tc.tool] || tc.tool || '?');
    traceHtml += '<div class="trace-entry">' + escapeHtml(display) + '(' + escapeHtml((tc.path || tc.query || '').substring(0, 60)) + ')</div>';
  }
  if (toolCalls.length > showN) {
    traceHtml += '<div class="trace-entry" style="color:#6c6c8a">+' + (toolCalls.length - showN) + ' more tool uses</div>';
  }
  // Done status
  const doneEntry = trace.find(e => e.type === 'done');
  if (doneEntry) {
    traceHtml += '<div class="trace-entry done">\u23bf  Done</div>';
  }
  // Content
  const contentText = content || '';
  if (contentText) {
    traceHtml += '<div class="trace-content">' + renderMarkdown(contentText) + '</div>';
  }
  return '<div class="sub-agent-trace"' + (traceId ? ' data-trace-id="' + escapeHtml(traceId) + '"' : '') + '>'
    + '<div class="sub-trace-header" onclick="toggleTrace(this)">\u25b6 ' + header + '</div>'
    + '<div class="sub-trace-body" style="display:none">' + traceHtml + '</div>'
    + '</div>';
}

// Render multiple sub-agent traces as a tree (Claude Code style)
function renderMultiAgentTree(traces) {
  if (!traces || traces.length === 0) return '';
  const count = traces.length;
  let html = '<div class="multi-agent-tree">';
  html += '<div class="tree-header" onclick="toggleTrace(this)">\u25b6 '
    + count + ' agent' + (count > 1 ? 's' : '') + ' finished</div>';
  html += '<div class="tree-body" style="display:none">';
  for (let i = 0; i < traces.length; i++) {
    const t = traces[i];
    const isLast = i === traces.length - 1;
    const connector = isLast ? '\u2514\u2500 ' : '\u251c\u2500 ';
    const pipe = isLast ? '   ' : '\u2502  ';
    const name = escapeHtml(t.name || 'agent');
    const tools = t.totalTools || 0;
    const tokensK = ((t.tokensTotal || 0) / 1000).toFixed(1);
    html += '<div class="tree-agent">'
      + '<span style="color:#555">' + connector + '</span>'
      + '<span style="color:#c0c0d0">' + name + '</span>'
      + ' <span style="color:#6c6c8a">\u00b7 ' + tools + ' tool uses \u00b7 ' + tokensK + 'k tokens</span>'
      + '</div>';
    html += '<div class="tree-result"><span style="color:#555">' + pipe + '</span>\u23bf  '
      + '<span style="color:#4ecdc4">' + escapeHtml(t.status || 'Done') + '</span></div>';
  }
  html += '</div></div>';
  return html;
}

function toggleTrace(headerEl) {
  const body = headerEl.nextElementSibling;
  if (!body) return;
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  headerEl.textContent = (isHidden ? '\u25bc ' : '\u25b6 ') + headerEl.textContent.substring(2);
}

// Auto-scroll state: true by default, turned off when user scrolls up manually
let _autoScroll = true;
function isNearBottom() { return _autoScroll; }

// Detect manual scroll-up by user
(function() {
  const m = document.getElementById('messages');
  if (!m) return;
  let _lastScrollTop = 0;
  m.addEventListener('scroll', () => {
    const atBottom = m.scrollHeight - m.scrollTop - m.clientHeight <= 5;
    if (atBottom) {
      _autoScroll = true;
    } else if (m.scrollTop < _lastScrollTop) {
      // User scrolled UP → disable auto-scroll
      _autoScroll = false;
    }
    _lastScrollTop = m.scrollTop;
  });
})();

function scrollBottom(force) {
  if (force) _autoScroll = true;
  if (_autoScroll) {
    const m = document.getElementById('messages');
    m.scrollTop = m.scrollHeight;
  }
  updateScrollNav();
}

function updateScrollNav() {
  const nav = document.getElementById('scrollNav');
  if (!nav) return;
  const m = document.getElementById('messages');
  const hasScroll = m.scrollHeight > m.clientHeight + 100;
  const atBottom = m.scrollHeight - m.scrollTop - m.clientHeight < 150;
  // Show buttons when there's scrollable content and user is not at the bottom
  nav.classList.toggle('visible', hasScroll && !atBottom);
}

// Listen for scroll events on the messages container
document.getElementById('messages').addEventListener('scroll', updateScrollNav);

// Auto-load older messages when user scrolls to top
document.getElementById('messages').addEventListener('scroll', function() {
  if (this.scrollTop === 0 && hasMoreMessages && !loadingMore) {
    loadMoreMessages();
  }
});
