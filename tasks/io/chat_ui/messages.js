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
  const parts = [];
  if (model) parts.push(model);
  if (provider && provider !== model) parts.push(provider);
  if (tokIn || tokOut) parts.push('\u2191' + tokIn + ' \u2193' + tokOut);
  if (dur) parts.push((dur / 1000).toFixed(1) + 's');
  if (!parts.length) return '';
  // Compact summary line (always visible)
  let line = '<span class="meta-summary">' + parts.join(' \u00b7 ') + '</span>';
  // Expandable details
  const details = [];
  if (baseUrl) details.push('endpoint: ' + escapeHtml(baseUrl));
  if (tokIn || tokOut) details.push('tokens: ' + tokIn + ' in / ' + tokOut + ' out (' + (tokIn + tokOut) + ' total)');
  if (dur) details.push('duration: ' + (dur / 1000).toFixed(1) + 's');
  if (details.length) {
    line += '<span class="meta-details">' + details.join(' \u00b7 ') + '</span>';
  }
  return '<div class="msg-meta" onclick="this.classList.toggle(\'expanded\')">' + line + '</div>';
}

// Global set of seen msg_ids for dedup (SSE + poll + replay)
const _seenMsgIds = new Set();

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
  el.dataset.rawText = (text || '').substring(0, 500);  // for dedup comparison
  if (extra && extra.raw_index !== undefined) el.dataset.rawIndex = extra.raw_index;
  const badge = (extra && extra.source) ? sourceBadge(extra.source) : '';
  // Timestamp — use provided timestamp or current time
  const msgTime = (extra && extra.timestamp) ? new Date(extra.timestamp * 1000) : new Date();
  const timeStr = msgTime.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  const timeHtml = '<span class="msg-time">' + timeStr + '</span>';

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
    // Unified tool_call rendering (used by BOTH SSE live AND transcript reload)
    const toolName = (extra && (extra.tool_name || extra.tool)) || text || '?';
    const toolArgs = (extra && extra.tool_args) || (extra && extra.arguments) || {};
    const srcAgent = (extra && extra.source) ? (extra.source.name || extra.agent_name || '') : (extra && extra.agent_name || '');
    const srcSvc = (extra && extra.source) ? (extra.source.llm_service || '') : (extra && extra.llm_service || '');
    const srcLabel = displayAgentName(srcAgent) + (srcSvc ? ' via ' + srcSvc : '');
    // Parse args if string
    let args = toolArgs;
    if (typeof args === 'string') { try { args = JSON.parse(args); } catch(e) {} }

    if (toolName === 'spawn_agents' && args && args.tasks) {
      const lines = args.tasks.map(task => {
        const dst = displayAgentName(task.agent || '?');
        const preview = (task.message || '').substring(0, 80);
        return '\u27A1 ' + srcLabel + ' \u2192 ' + dst + (preview ? ': ' + preview : '');
      });
      el.innerHTML = escapeHtml(lines.join('\n'));
    } else if (toolName === 'filesystem' && args.action === 'edit' && args.path) {
      el.innerHTML = _renderToolCallEdit(srcLabel, args);
    } else {
      const argKeys = typeof args === 'object' && args ? Object.keys(args) : [];
      let argPreview = '';
      if (argKeys.length > 0) {
        argPreview = argKeys.map(k => {
          const v = typeof args[k] === 'string' ? args[k].substring(0, 60) : JSON.stringify(args[k]).substring(0, 60);
          return k + '=' + v;
        }).join(', ');
        if (argPreview.length > 120) argPreview = argPreview.substring(0, 120) + '...';
      }
      el.innerHTML = '\u{1F527} [' + escapeHtml(srcLabel) + '] ' + escapeHtml(toolName)
        + (argPreview ? '(' + escapeHtml(argPreview) + ')' : '');
    }
  } else if (role === 'tool_result') {
    // Unified tool_result rendering
    const toolName = (extra && extra.tool_name) || (extra && extra.tool) || '';
    const srcAgent = (extra && extra.source) ? (extra.source.name || '') : (extra && extra.agent_name || '');
    const srcSvc = (extra && extra.source) ? (extra.source.llm_service || '') : (extra && extra.llm_service || '');
    const srcLabel = displayAgentName(srcAgent) + (srcSvc ? ' via ' + srcSvc : '');
    const resultText = text || '';
    // Collapsible if long
    if (resultText.length > 200) {
      const firstLine = resultText.split('\n')[0].substring(0, 100);
      el.innerHTML = '<details><summary style="color:#4ecdc4;font-size:11px;cursor:pointer">\u2705 ['
        + escapeHtml(srcLabel) + '] ' + escapeHtml(toolName || '') + ': ' + escapeHtml(firstLine)
        + '</summary><pre style="font-size:11px;margin:4px 0 0 0;max-height:300px;overflow-y:auto"><code>'
        + escapeHtml(resultText) + '</code></pre></details>';
    } else {
      const diffHtml = _renderDiff(resultText);
      if (diffHtml) {
        el.innerHTML = '<span style="color:#4ecdc4;font-size:11px">\u2705 </span>' + diffHtml;
      } else {
        el.innerHTML = '<span style="color:#4ecdc4;font-size:11px">\u2705 [' + escapeHtml(srcLabel) + '] '
          + escapeHtml(toolName ? toolName + ': ' : '') + escapeHtml(resultText) + '</span>';
      }
    }
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
    el.innerHTML = sourceBadge(src) + '<em>' + escapeHtml(text) + '</em>';
  } else if (role === 'user') {
    el.innerHTML = replyQuoteHtml + actionsHtml + timeHtml + badge + escapeHtml(text);
  } else if (role === 'sub_agent_trace') {
    el.innerHTML = renderSubAgentTrace(text, extra);
  } else if (role === 'agent-result') {
    const agentName = (extra && typeof extra === 'string') ? extra : '';
    el.innerHTML = (agentName ? '<strong>' + escapeHtml(agentName) + ':</strong> ' : '') + renderMarkdown(text);
  } else {
    el.textContent = text;
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
  fetch(API, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify({ action: 'check_files', file_ids: fileIds }),
    credentials: 'same-origin',
  }).then(r => r.json()).then(data => {
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
  }).catch(() => {
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
  const parentAgent = source.parent_agent || 'assistant';
  const agentName = source.name || 'sub-agent';
  // Summarize trace for header
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
  const header = escapeHtml(displayAgentName(parentAgent)) + ' \u2192 ' + escapeHtml(displayAgentName(agentName))
    + ' (' + totalTools + ' tool' + (totalTools !== 1 ? 's' : '') + ', ' + tokensIn + '\u2191 ' + tokensOut + '\u2193)';
  // Build trace entries
  let traceHtml = '';
  for (const entry of trace) {
    if (entry.type === 'iteration') {
      traceHtml += '<div class="trace-entry">iteration ' + entry.iteration + ' \u00b7 ' + (entry.total_tools || 0) + ' tools</div>';
    } else if (entry.type === 'tool_call') {
      traceHtml += '<div class="trace-entry">\u26a1 ' + escapeHtml(entry.tool || '?') + '</div>';
    } else if (entry.type === 'done') {
      const status = entry.status || 'done';
      traceHtml += '<div class="trace-entry done">\u2713 ' + escapeHtml(status) + ' (' + (entry.tokens_in || 0) + '\u2191 ' + (entry.tokens_out || 0) + '\u2193)</div>';
    }
  }
  // Content preview (truncated in header, full in body)
  const contentText = content || '';
  if (contentText) {
    traceHtml += '<div class="trace-content">' + renderMarkdown(contentText) + '</div>';
  }
  return '<div class="sub-agent-trace"' + (traceId ? ' data-trace-id="' + escapeHtml(traceId) + '"' : '') + '>'
    + '<div class="sub-trace-header" onclick="toggleTrace(this)">\u25b6 ' + header + '</div>'
    + '<div class="sub-trace-body" style="display:none">' + traceHtml + '</div>'
    + '</div>';
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
