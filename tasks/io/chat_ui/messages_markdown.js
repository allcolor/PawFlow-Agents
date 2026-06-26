// \xe2\x94\x80\xe2\x94\x80 Markdown render + delegate/sub-agent traces + scroll wiring \xe2\x94\x80\xe2\x94\x80
// Split from messages.js (<=800 lines). Global. Holds the load-time
// #messages scroll listeners; loads last of the messages_* group.

function renderMarkdown(text) {
  // Detect __show_file__ markers from show_file tool
  try {
    if (text.includes('__show_file__')) {
      const parsed = JSON.parse(text);
      if (parsed && parsed.__show_file__) {
        // Convert fs://filestore/<id>/<name> to /files/<id> for the
        // native <img>/<audio>/<video> tags (which need a real HTTP URL,
        // same-origin so the auth cookie applies).
        let _httpUrl = parsed.url;
        const _fsm = String(parsed.url || '').match(/^fs:\/\/filestore\/([a-f0-9]+)\//);
        if (_fsm) _httpUrl = '/files/' + _fsm[1] + '/' + encodeURIComponent(parsed.filename || 'file');
        if (isImageFile(parsed.filename)) {
          return inlineImageHtml(_httpUrl, parsed.filename, parsed.size_kb + ' KB');
        }
        if (isAudioFile(parsed.filename)) {
          return inlineAudioHtml(_httpUrl, parsed.filename);
        }
        if (isVideoFile(parsed.filename)) {
          return inlineVideoHtml(_httpUrl, parsed.filename);
        }
        setTimeout(() => openFileViewer(parsed.url), 100);
        return '<span style="cursor:pointer;color:#6c5ce7;" data-file-url="' + escapeHtml(parsed.url) + '" onclick="openFileViewer(this.dataset.fileUrl)">\uD83D\uDCC4 ' + escapeHtml(parsed.filename) + ' (' + escapeHtml(String(parsed.size_kb)) + ' KB) \u2014 Click to view</span>';
      }
    }
  } catch(e) {}
  // 1. Extract code blocks BEFORE escaping (preserve their content as-is)
  const _codeBlocks = [];
  text = text.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
    var cls = lang ? ' class="language-' + lang + '"' : '';
    const copyLabel = escapeHtml(t('copy'));
    _codeBlocks.push('<div class="code-block">'
      + '<button class="code-block-copy" onclick="copyCodeBlock(this,event)" title="' + copyLabel + '" aria-label="' + copyLabel + '">\u{1F4CB}</button>'
      + '<pre><code' + cls + '>' + escapeHtml(code) + '</code></pre>'
      + '</div>');
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
    const fileUrl = normalizePawFlowFileUrl(url);
    if (fileUrl.match(/\/files\/[a-f0-9]+\//)) {
      if (isImageFile(label) || isImageFile(url)) {
        return inlineImageHtml(fileUrl, label, '');
      }
      if (isAudioFile(label) || isAudioFile(url)) {
        return inlineAudioHtml(fileUrl, label);
      }
      if (isVideoFile(label) || isVideoFile(url)) {
        return inlineVideoHtml(fileUrl, label);
      }
      return '<a class="flink" href="' + fileUrl + '" style="color:#6c5ce7;cursor:pointer;" data-file-url="' + fileUrl + '" onclick="event.preventDefault();openFileViewer(this.dataset.fileUrl)">\uD83D\uDCC4 ' + label + '</a>';
    }
    return '<a href="' + url + '" target="_blank">' + label + '</a>';
  });
  // Bare file URLs (not already inside a tag attribute)
  text = text.replace(/(^|[\s>])(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g, function(_, pre, url, fname) {
    const fileUrl = normalizePawFlowFileUrl(url);
    if (isImageFile(fname)) {
      return pre + inlineImageHtml(fileUrl, fname, '');
    }
    if (isAudioFile(fname)) {
      return pre + inlineAudioHtml(fileUrl, fname);
    }
    if (isVideoFile(fname)) {
      return pre + inlineVideoHtml(fileUrl, fname);
    }
    return pre + '<a class="flink" href="' + fileUrl + '" style="color:#6c5ce7;cursor:pointer;" data-file-url="' + fileUrl + '" onclick="event.preventDefault();openFileViewer(this.dataset.fileUrl)">\uD83D\uDCC4 ' + fname + '</a>';
  });
  text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // fs:// URLs — clickable links to filesystem files. For media files
  // stored in FileStore (`fs://filestore/<id>/<name>.png` etc.) render
  // an inline player/image so generate_image / generate_audio /
  // generate_video / screen results display directly in chat without
  // an extra click.
  text = text.replace(/(fs:\/\/([^\s&<"']+))/g, function(_, url, rest) {
    const parts = rest.split('/');
    const service = parts[0];
    const fpath = parts.slice(1).join('/');
    const fname = parts[parts.length - 1] || fpath;
    const isDir = url.endsWith('/');
    if (!isDir && service === 'filestore') {
      const fidMatch = fpath.match(/^([a-f0-9]+)(?:\/|$)/);
      const fid = fidMatch ? fidMatch[1] : '';
      if (fid) {
        const httpUrl = '/files/' + fid + '/' + encodeURIComponent(fname);
        if (isImageFile(fname)) return inlineImageHtml(httpUrl, fname, '');
        if (isAudioFile(fname)) return inlineAudioHtml(httpUrl, fname);
        if (isVideoFile(fname)) return inlineVideoHtml(httpUrl, fname);
      }
    } else if (!isDir && (isImageFile(fname) || isAudioFile(fname) || isVideoFile(fname))) {
      // Non-filestore service (a user relay) — proxy through the
      // /fs/<service>/<path> route registered in pawflow_agent.json.
      // Same-origin URL so the auth cookie applies and the browser
      // can stream / cache like any normal HTTP media.
      const httpUrl = '/fs/' + encodeURIComponent(service) + '/'
          + fpath.split('/').map(encodeURIComponent).join('/');
      if (isImageFile(fname)) return inlineImageHtml(httpUrl, fname, '');
      if (isAudioFile(fname)) return inlineAudioHtml(httpUrl, fname);
      if (isVideoFile(fname)) return inlineVideoHtml(httpUrl, fname);
    }
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

function _renderDelegateTraceContent(content, trace, message) {
  let html = '';
  if (message) {
    html += '<div class="delegate-message">\u{1F4E9} ' + renderMarkdown(message) + '</div>';
  }
  const toolCalls = trace.filter(e => e.type === 'tool_call');
  for (const tc of toolCalls) {
    const display = (_TOOL_DISPLAY[tc.tool] || tc.tool || '?');
    let argSummary = '';
    if (tc.arguments && typeof tc.arguments === 'object') {
      const keys = Object.keys(tc.arguments);
      if (keys.length === 1) {
        argSummary = String(tc.arguments[keys[0]]).substring(0, 120);
      } else if (keys.length > 1) {
        argSummary = keys.map(k => k + '=' + String(tc.arguments[k]).substring(0, 60)).join(', ').substring(0, 120);
      }
    }
    html += '<div class="delegate-tool"><span class="tc-bullet done">\u25cf</span> '
      + escapeHtml(display) + '(' + escapeHtml(argSummary) + ')</div>';
  }
  const doneEntry = trace.find(e => e.type === 'done');
  if (doneEntry && doneEntry.status === 'needs_input' && doneEntry.question) {
    html += '<div class="delegate-question">\u{1F4AC} ' + renderMarkdown(doneEntry.question) + '</div>';
  } else if (content) {
    html += '<div class="delegate-response">\u{1F4E8} ' + renderMarkdown(content) + '</div>';
  } else if (doneEntry && doneEntry.error) {
    html += '<div class="delegate-error">\u274C ' + escapeHtml(doneEntry.error) + '</div>';
  }
  const tokensIn = doneEntry ? (doneEntry.tokens_in || 0) : 0;
  const tokensOut = doneEntry ? (doneEntry.tokens_out || 0) : 0;
  const parts = [];
  if (doneEntry && doneEntry.model) parts.push(doneEntry.model);
  parts.push('\u2191' + tokensIn + ' \u2193' + tokensOut);
  parts.push(trace.filter(e => e.type === 'tool_call').length + ' tools');
  html += '<div class="delegate-stats">' + parts.join(' \u00b7 ') + '</div>';
  return html;
}

function renderDelegateBlock(content, extra) {
  const source = (extra && extra.source) || {};
  const trace = (extra && extra.trace) || [];
  const agentName = source.name || 'sub-agent';
  const parentAgent = source.parent_agent || '';
  const llmService = source.llm_service || '';
  const message = source.message || '';
  const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
  // Group header (first agent) — delegate is not a task, no status badge
  let html = '<summary class="delegate-header">\u{1F500} '
    + '<span class="delegate-src">' + escapeHtml(displayAgentName(parentAgent)) + '</span> \u2192 '
    + '<span class="delegate-dst">' + escapeHtml(displayAgentName(agentName)) + '</span>'
    + svcLabel
    + ' <span class="delegate-group-count"></span>'
    + '</summary>';
  html += '<div class="delegate-body">';
  html += _renderDelegateTraceContent(content, trace, message);
  html += '</div>';
  return html;
}

function renderDelegateSubBlock(content, extra) {
  const source = (extra && extra.source) || {};
  const trace = (extra && extra.trace) || [];
  const agentName = source.name || 'sub-agent';
  const llmService = source.llm_service || '';
  const message = source.message || '';
  const svcLabel = llmService ? ' via ' + escapeHtml(llmService) : '';
  let html = '<summary class="delegate-sub-header">\u25b8 '
    + '<span class="delegate-dst">' + escapeHtml(displayAgentName(agentName)) + '</span>'
    + svcLabel
    + '</summary>';
  html += '<div class="delegate-sub-body">';
  html += _renderDelegateTraceContent(content, trace, message);
  html += '</div>';
  return html;
}

function toggleTrace(headerEl) {
  const body = headerEl.nextElementSibling;
  if (!body) return;
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? '' : 'none';
  headerEl.textContent = (isHidden ? '\u25bc ' : '\u25b6 ') + headerEl.textContent.substring(2);
}

// Auto-scroll state: true by default. Only explicit user scroll input may turn
// it off; DOM growth/reflow must not be interpreted as the user scrolling up.
let _autoScroll = true;
let _suppressTopLoadUntil = 0;
function isNearBottom() { return _autoScroll; }

(function() {
  const m = document.getElementById('messages');
  if (!m) return;
  let userScrollIntentUntil = 0;
  let scrollbarDragActive = false;

  function atBottom() {
    return m.scrollHeight - m.scrollTop - m.clientHeight <= 5;
  }

  function markUserScrollIntent() {
    userScrollIntentUntil = Date.now() + 700;
  }

  function hasUserScrollIntent() {
    return scrollbarDragActive || Date.now() <= userScrollIntentUntil;
  }

  function isScrollbarPointerEvent(e) {
    const rect = m.getBoundingClientRect();
    const scrollbarWidth = Math.max(12, m.offsetWidth - m.clientWidth);
    return e.clientX >= rect.right - scrollbarWidth - 2;
  }

  m.addEventListener('wheel', markUserScrollIntent, { passive: true });
  m.addEventListener('touchstart', markUserScrollIntent, { passive: true });
  m.addEventListener('pointerdown', (e) => {
    if (isScrollbarPointerEvent(e)) {
      scrollbarDragActive = true;
      markUserScrollIntent();
    }
  });
  window.addEventListener('pointerup', () => {
    if (scrollbarDragActive) markUserScrollIntent();
    scrollbarDragActive = false;
  });
  m.addEventListener('keydown', (e) => {
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', 'Space'].includes(e.key)) {
      markUserScrollIntent();
    }
  });

  m.addEventListener('scroll', () => {
    if (atBottom()) {
      _autoScroll = true;
    } else if (hasUserScrollIntent()) {
      _autoScroll = false;
    }
  });
})();

function setMessagesScrollTop(value) {
  const m = document.getElementById('messages');
  if (m) m.scrollTop = value;
}

function refreshMessagesScrollMetrics(forceBottom) {
  const m = document.getElementById('messages');
  if (!m) return;
  if (forceBottom) _autoScroll = true;
  const settle = () => {
    if (forceBottom || _autoScroll) setMessagesScrollTop(m.scrollHeight);
    updateScrollNav();
  };
  settle();
  window.requestAnimationFrame(() => {
    settle();
    window.requestAnimationFrame(settle);
  });
}

function scrollMessagesTop() {
  _autoScroll = false;
  _suppressTopLoadUntil = Date.now() + 700;
  setMessagesScrollTop(0);
  updateScrollNav();
}

function scrollBottom(force) {
  refreshMessagesScrollMetrics(!!force);
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
  if (this.scrollTop === 0 && Date.now() > _suppressTopLoadUntil && hasMoreMessages && !loadingMore) {
    loadMoreMessages();
  }
});
