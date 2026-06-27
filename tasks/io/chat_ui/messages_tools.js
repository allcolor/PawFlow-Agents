// \xe2\x94\x80\xe2\x94\x80 Tool-output / diff rendering + escape helpers + inline media \xe2\x94\x80\xe2\x94\x80
// Split from messages.js (<=800 lines). Global. escapeHtml is the canonical
// definition in state.js (loads first); escapeAttr/jsStringArg wrap it here.

function escapeAttr(t) {
  return escapeHtml(t);
}

function jsStringArg(t) {
  return escapeAttr(JSON.stringify(String(t == null ? '' : t)));
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

  // Default — escape but auto-inline any fs://filestore or /files/<id> media URLs
  // so generate_image / screen / see / edit_image results show the image
  // directly in the tool-result bubble instead of printing the raw URL.
  return '<pre class="tc-output">' + renderTextWithInlineMedia(text) + '</pre>';
}

function _attachToolResult(tcEl, resultText) {
  // Guard: don't attach if result already present
  if (tcEl.querySelector('.tc-result')) return;
  if (typeof resultText !== 'string') {
    try { resultText = JSON.stringify(resultText, null, 2); }
    catch (_err) { resultText = String(resultText || ''); }
  }
  const bullet = tcEl.querySelector('.tc-bullet');
  if (bullet) { bullet.classList.remove('pending'); bullet.classList.add('done'); }
  // Remove BG/KL buttons (tool is done)
  const bgBtn = tcEl.querySelector('.tc-bg-btn');
  if (bgBtn) bgBtn.remove();
  const klBtn = tcEl.querySelector('.tc-kl-btn');
  if (klBtn) klBtn.remove();
  const toolHint = tcEl.dataset.tool || '';
  let pathHint = tcEl.dataset.path || '';
  // Render filet: when an edit/apply_patch call's streamed args arrived empty
  // (large input the CCI observer could not reconstruct), the header rendered
  // as a bare "Update()". Recover the file path from the result line
  // ("Edited <path> (line N), ...") and patch it into the .tc-summary span so
  // it reads "Update(<path>)" instead of empty, useless parens.
  if (!pathHint && (toolHint === 'edit' || toolHint === 'apply_patch')) {
    const _m = resultText.match(/^Edited\s+(.+?)(?:\s+\(line\b|:\s)/);
    const _recovered = _m && _m[1];
    if (_recovered) {
      pathHint = _recovered;
      tcEl.dataset.path = _recovered;
      const _sum = tcEl.querySelector('.tc-summary');
      if (_sum && /\(\s*\)\s*$/.test(_sum.textContent)) {
        _sum.textContent = _sum.textContent.replace(/\(\s*\)\s*$/, '(' + _recovered + ')');
      }
    }
  }
  const resultDiv = document.createElement('div');
  resultDiv.className = 'tc-result';
  const firstLine = resultText.split('\n')[0].substring(0, 120);
  const rendered = _renderToolOutput(resultText, toolHint, pathHint);
  // see sends the image to the LLM, not the user — don't render it twice
  const inlineMedia = toolHint === 'see' ? '' : _extractInlineMedia(resultText);
  // Open while streaming, auto-collapse after 1.5s
  resultDiv.innerHTML = inlineMedia
    + '<details open><summary>\u23bf ' + escapeHtml(firstLine)
    + '</summary>' + rendered + '</details>';
  tcEl.appendChild(resultDiv);
  // Auto-collapse after brief display
  const _det = resultDiv.querySelector('details');
  if (_det) setTimeout(() => { _det.removeAttribute('open'); }, 1500);
  // Auto-scroll
  if (isNearBottom()) scrollBottom();
}

function backgroundTool(tcId) {
  if (!conversationId || !tcId) return;
  action$('background_tool', { tc_id: tcId }).subscribe(d => {
    if (d.ok) {
      const tcEl = findToolCallElement(tcId);
      if (tcEl) {
        const btn = tcEl.querySelector('.tc-bg-btn');
        if (btn) btn.remove();
        const bullet = tcEl.querySelector('.tc-bullet');
        if (bullet) { bullet.classList.add('bg'); bullet.title = t('runningInBackground'); }
        // Add Kill button
        const klBtn = document.createElement('button');
        klBtn.className = 'tc-kl-btn';
        klBtn.onclick = () => killTool(tcId);
        klBtn.title = t('killBackgroundTask');
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
  const tcEl = findToolCallElement(tcId);
  if (tcEl) {
    tcEl.querySelectorAll('.tc-kl-btn, .tc-bg-btn').forEach(b => b.remove());
    const bullet = tcEl.querySelector('.tc-bullet');
    if (bullet) { bullet.classList.remove('bg', 'pending'); bullet.classList.add('done'); bullet.style.color = '#e94560'; bullet.title = t('killed'); }
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

function _renderToolCallPatch(srcLabel, args) {
  const fpath = args.path || '?';
  const patch = args.patch || '';
  let patchHtml = '<span style="color:#4ecdc4;font-size:11px">\u270E [' + escapeHtml(srcLabel) + '] ApplyPatch(' + escapeHtml(fpath) + ')</span>';
  if (!patch) return patchHtml;
  const lines = patch.split('\n');
  let added = 0, removed = 0;
  lines.forEach(function(line) {
    if (line.startsWith('+') && !line.startsWith('+++')) added++;
    else if (line.startsWith('-') && !line.startsWith('---')) removed++;
  });
  const parts = [];
  if (added) parts.push(added + ' added');
  if (removed) parts.push(removed + ' removed');
  if (parts.length) patchHtml += '<span style="color:#8b949e;font-size:10px;margin-left:8px">(' + parts.join(', ') + ')</span>';
  patchHtml += '<pre class="diff-output" style="margin:2px 0 0 0;font-size:11px"><code class="language-diff hljs">' + lines.map(function(line) {
    if (line.startsWith('+') && !line.startsWith('+++')) return '<span class="hljs-addition">' + escapeHtml(line) + '</span>';
    if (line.startsWith('-') && !line.startsWith('---')) return '<span class="hljs-deletion">' + escapeHtml(line) + '</span>';
    if (line.startsWith('@@') || line.startsWith('***')) return '<span class="hljs-meta">' + escapeHtml(line) + '</span>';
    return escapeHtml(line);
  }).join('\n') + '</code></pre>';
  return patchHtml;
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

function normalizePawFlowFileUrl(url) {
  const raw = String(url || '');
  const m = raw.match(/^https?:\/\/[^/]+(\/files\/[a-f0-9]+\/[^\s<"'`]+)$/i);
  return m ? m[1] : raw;
}


function _extractInlineMedia(text) {
  if (!text) return '';
  const urlRe = /(fs:\/\/[^\s<"'`]+|https?:\/\/[^\s<"'`]*\/files\/[a-f0-9]+\/[^\s<"'`]+|\/files\/[a-f0-9]+\/[^\s<"'`]+)/g;
  const seen = new Set();
  let out = '';
  let m;
  while ((m = urlRe.exec(text)) !== null) {
    const url = m[0];
    if (seen.has(url)) continue;
    seen.add(url);
    let fname = '';
    let httpUrl = url;
    const fsMatch = url.match(/^fs:\/\/([^/]+)\/(.+)$/);
    if (fsMatch) {
      const service = fsMatch[1];
      const fpath = fsMatch[2];
      fname = fpath.split('/').pop() || fpath;
      if (service === 'filestore') {
        const fidMatch = fpath.match(/^([a-f0-9]+)(?:\/|$)/);
        // /files/<id>/<name> — the trailing "/<name>" matters:
        // _flushPendingImages matches /\/files\/([a-f0-9]+)\// and
        // won't recognize a /files/<id> without the slash, leaving the
        // <img> permanently hidden (display:none).
        httpUrl = fidMatch ? '/files/' + fidMatch[1] + '/' + encodeURIComponent(fname) : '';
      } else {
        httpUrl = '/fs/' + encodeURIComponent(service) + '/'
          + fpath.split('/').map(encodeURIComponent).join('/');
      }
    } else {
      const fm = url.match(/\/files\/[a-f0-9]+\/([^?#]+)/);
      fname = fm ? fm[1] : '';
      httpUrl = normalizePawFlowFileUrl(url);
    }
    if (!httpUrl || !fname) continue;
    if (isImageFile(fname)) out += inlineImageHtml(httpUrl, fname, '');
    else if (isAudioFile(fname)) out += inlineAudioHtml(httpUrl, fname);
    else if (isVideoFile(fname)) out += inlineVideoHtml(httpUrl, fname);
  }
  return out;
}

/** Escape text for HTML but render fs://filestore/<id>/<name>.ext,
 * fs://<relay>/<path>/<name>.ext, and /files/<id>/<name>.ext media URLs
 * as inline <img>/<audio>/<video>. Used in tool-result default renderer
 * so generate_image / screen / see outputs show the image directly
 * instead of printing the raw URL. */
function renderTextWithInlineMedia(text) {
  if (!text) return '';
  // Single regex that matches either a full fs:// URL or an HTTP /files/<id>/<name> URL.
  const urlRe = /(fs:\/\/[^\s<"'`]+|https?:\/\/[^\s<"'`]*\/files\/[a-f0-9]+\/[^\s<"'`]+|\/files\/[a-f0-9]+\/[^\s<"'`]+)/g;
  let out = '';
  let last = 0;
  let m;
  while ((m = urlRe.exec(text)) !== null) {
    // Escaped text before the match
    if (m.index > last) out += escapeHtml(text.slice(last, m.index));
    const url = m[0];
    let fname = '';
    let httpUrl = url;
    const fsMatch = url.match(/^fs:\/\/([^/]+)\/(.+)$/);
    if (fsMatch) {
      const service = fsMatch[1];
      const fpath = fsMatch[2];
      fname = fpath.split('/').pop() || fpath;
      if (service === 'filestore') {
        const fidMatch = fpath.match(/^([a-f0-9]+)(?:\/|$)/);
        httpUrl = fidMatch ? '/files/' + fidMatch[1] + '/' + encodeURIComponent(fname) : '';
      } else {
        httpUrl = '/fs/' + encodeURIComponent(service) + '/'
          + fpath.split('/').map(encodeURIComponent).join('/');
      }
    } else {
      // /files/... URL (absolute or relative)
      const fm = url.match(/\/files\/[a-f0-9]+\/([^?#]+)/);
      fname = fm ? fm[1] : '';
      httpUrl = normalizePawFlowFileUrl(url);
    }
    if (httpUrl && fname && isImageFile(fname)) {
      out += inlineImageHtml(httpUrl, fname, '');
    } else if (httpUrl && fname && isAudioFile(fname)) {
      out += inlineAudioHtml(httpUrl, fname);
    } else if (httpUrl && fname && isVideoFile(fname)) {
      out += inlineVideoHtml(httpUrl, fname);
    } else {
      out += escapeHtml(url);
    }
    last = m.index + url.length;
  }
  if (last < text.length) out += escapeHtml(text.slice(last));
  return out;
}

function isAudioFile(name) {
  return /\.(mp3|wav|ogg|m4a|flac|opus)$/i.test(name || '');
}

function isVideoFile(name) {
  return /\.(mp4|webm|mov|m4v)$/i.test(name || '');
}

var _inlineAudioEl = null;
var _inlineAudioUrl = '';
var _inlineAudioTimer = null;

function _inlineAudioFormat(seconds) {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return '--:--';
  const total = Math.floor(n);
  const m = Math.floor(total / 60);
  const s = String(total % 60).padStart(2, '0');
  return m + ':' + s;
}

function _inlineAudioWrappers(url) {
  return Array.from(document.querySelectorAll('.inline-audio'))
    .filter(el => !url || el.dataset.audioUrl === url);
}

function _inlineAudioSync(url) {
  const audio = _inlineAudioEl;
  const isCurrent = !!audio && _inlineAudioUrl === url;
  const duration = isCurrent && Number.isFinite(audio.duration) ? audio.duration : 0;
  const current = isCurrent ? audio.currentTime || 0 : 0;
  const playing = isCurrent && !audio.paused && !audio.ended;
  for (const wrapper of _inlineAudioWrappers(url)) {
    const btn = wrapper.querySelector('.inline-audio-play');
    const range = wrapper.querySelector('.inline-audio-progress');
    const time = wrapper.querySelector('.inline-audio-time');
    if (btn) {
      btn.innerHTML = playing ? '&#10074;&#10074;' : '&#9658;';
      btn.title = playing ? 'Pause' : 'Play';
    }
    if (range) {
      range.value = duration > 0 ? String(Math.min(1000, Math.round((current / duration) * 1000))) : '0';
      range.disabled = duration <= 0;
    }
    if (time) time.textContent = _inlineAudioFormat(current) + ' / ' + _inlineAudioFormat(duration);
  }
}

function _inlineAudioStartTimer() {
  if (_inlineAudioTimer) return;
  _inlineAudioTimer = setInterval(function() {
    if (!_inlineAudioEl || _inlineAudioEl.paused || _inlineAudioEl.ended) {
      clearInterval(_inlineAudioTimer);
      _inlineAudioTimer = null;
    }
    if (_inlineAudioUrl) _inlineAudioSync(_inlineAudioUrl);
  }, 250);
}

function _inlineAudioFor(url) {
  if (_inlineAudioEl && _inlineAudioUrl === url) return _inlineAudioEl;
  if (_inlineAudioEl) {
    try { _inlineAudioEl.pause(); } catch(e) {}
    if (_inlineAudioUrl) _inlineAudioSync(_inlineAudioUrl);
  }
  const audio = new Audio(url);
  audio.preload = 'metadata';
  _inlineAudioEl = audio;
  _inlineAudioUrl = url;
  ['loadedmetadata', 'durationchange', 'timeupdate', 'play', 'pause', 'ended', 'error'].forEach(function(ev) {
    audio.addEventListener(ev, function() { _inlineAudioSync(url); });
  });
  return audio;
}

function pawflowInlineAudioToggle(btn) {
  const wrapper = btn && btn.closest ? btn.closest('.inline-audio') : null;
  const url = wrapper && wrapper.dataset ? wrapper.dataset.audioUrl : '';
  if (!url) return;
  const audio = _inlineAudioFor(url);
  if (!audio.paused && !audio.ended) {
    audio.pause();
    _inlineAudioSync(url);
    return;
  }
  audio.play().then(function() {
    _inlineAudioStartTimer();
    _inlineAudioSync(url);
  }).catch(function(err) {
    console.warn('[inline-audio] play failed', err);
    _inlineAudioSync(url);
  });
}

function pawflowInlineAudioSeek(input) {
  const wrapper = input && input.closest ? input.closest('.inline-audio') : null;
  const url = wrapper && wrapper.dataset ? wrapper.dataset.audioUrl : '';
  if (!url) return;
  const audio = _inlineAudioFor(url);
  if (!Number.isFinite(audio.duration) || audio.duration <= 0) return;
  audio.currentTime = (Number(input.value) / 1000) * audio.duration;
  _inlineAudioSync(url);
}

function inlineAudioHtml(url, filename) {
  const safeUrl = escapeHtml(url || '');
  return '<div class="audio-wrapper inline-audio" data-audio-url="' + safeUrl + '" style="margin:6px 0;max-width:512px;">'
    + '<div style="display:flex;align-items:center;gap:8px;background:rgba(255,255,255,0.7);border-radius:999px;padding:8px 10px;">'
    + '<button class="inline-audio-play" type="button" onclick="pawflowInlineAudioToggle(this)" title="Play" style="width:28px;height:28px;border:0;border-radius:50%;background:#eef4ff;color:#1f2937;cursor:pointer;">&#9658;</button>'
    + '<span class="inline-audio-time" style="font-size:12px;color:#374151;min-width:72px;text-align:center;">0:00 / --:--</span>'
    + '<input class="inline-audio-progress" type="range" min="0" max="1000" value="0" disabled oninput="pawflowInlineAudioSeek(this)" style="flex:1;min-width:90px;">'
    + '</div>'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83D\uDD0A ' + escapeHtml(filename || 'audio')
    + ' <a class="flink" href="#" onclick="event.preventDefault();openFileViewer(this.closest(\'.inline-audio\').dataset.audioUrl)" style="color:#6c5ce7;">open</a>'
    + '</div></div>';
}

function inlineVideoHtml(url, filename) {
  return '<div class="video-wrapper" style="margin:6px 0;">'
    + '<video controls preload="metadata" src="' + escapeHtml(url) + '" '
    + 'style="max-width:512px;max-height:512px;border-radius:8px;border:1px solid #0f3460;"></video>'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83C\uDFAC ' + escapeHtml(filename || 'video')
    + ' <a class="flink" href="#" data-file-url="' + escapeHtml(url) + '" onclick="event.preventDefault();openFileViewer(this.dataset.fileUrl)" style="color:#6c5ce7;">open</a>'
    + '</div></div>';
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
  // Extract file_ids from URLs: /files/{file_id}
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
    + '<img id="' + imgId + '" data-file-url="' + escapeHtml(url) + '" style="display:none;max-width:512px;max-height:512px;border-radius:8px;cursor:pointer;border:1px solid #0f3460;" '
    + 'onclick="openFileViewer(this.dataset.fileUrl)" title="' + t('clickFullSize') + '" />'
    + '<div style="font-size:11px;color:#6c6c8a;margin-top:2px;">'
    + '\uD83D\uDCC4 ' + escapeHtml(filename || 'image') + (sizeInfo ? ' (' + sizeInfo + ')' : '')
    + '</div></div>';
}

