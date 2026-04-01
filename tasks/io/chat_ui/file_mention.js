// ── @file mention autocomplete ──────────────────────────────────
let _mentionDropdown = null;
let _mentionQuery = '';
let _mentionStart = -1;
let _mentionResults = [];
let _mentionSelected = 0;
let _mentionDebounce = null;

function initFileMention() {
  const input = document.getElementById('input');
  if (!input) return;

  input.addEventListener('input', _onMentionInput);
  input.addEventListener('keydown', _onMentionKeydown);
  document.addEventListener('click', (e) => {
    if (_mentionDropdown && !_mentionDropdown.contains(e.target)) _closeMention();
  });
}

function _onMentionInput(e) {
  const input = e.target;
  const pos = input.selectionStart;
  const text = input.value;

  // Find @ before cursor (stop at whitespace or newline)
  let atPos = -1;
  for (let i = pos - 1; i >= 0; i--) {
    if (text[i] === '@') { atPos = i; break; }
    if (text[i] === ' ' || text[i] === '\n') break;
  }

  if (atPos < 0) { _closeMention(); return; }

  // Don't trigger file mention inside slash commands that use @name syntax
  // e.g. /skill assign @agent @skill, /agent del @name, /msg @agent
  const before = text.substring(0, atPos).trimStart();
  if (/^\//.test(before)) { _closeMention(); return; }

  _mentionStart = atPos;
  _mentionQuery = text.substring(atPos + 1, pos);

  // Debounce the search
  clearTimeout(_mentionDebounce);
  _mentionDebounce = setTimeout(() => _searchFiles(_mentionQuery), 200);
}

function _searchFiles(query) {
  action$('fs_search', {
    pattern: query ? '*' + query + '*' : '*',
    path: '.',
  }).subscribe(data => {
    if (data.error) { _closeMention(); return; }
    _mentionResults = (data.results || []).slice(0, 15);
    _mentionSelected = 0;
    _showMentionDropdown();
  });
}

function _showMentionDropdown() {
  if (!_mentionResults.length) { _closeMention(); return; }
  if (!_mentionDropdown) {
    _mentionDropdown = document.createElement('div');
    _mentionDropdown.id = 'mentionDropdown';
    _mentionDropdown.style.cssText =
      'position:absolute;bottom:100%;left:0;right:0;max-height:250px;'
      + 'overflow-y:auto;background:var(--bg2,#1e1e2e);border:1px solid var(--border,#444);'
      + 'border-radius:6px;z-index:1000;font-size:12px;';
    const inputArea = document.querySelector('.input-area');
    inputArea.style.position = 'relative';
    inputArea.appendChild(_mentionDropdown);
  }
  _mentionDropdown.innerHTML = _mentionResults.map((f, i) => {
    const sel = i === _mentionSelected;
    return '<div class="mention-item' + (sel ? ' selected' : '') + '" '
      + 'data-idx="' + i + '" '
      + 'style="padding:6px 10px;cursor:pointer;'
      + (sel ? 'background:var(--accent,#7c6af7);color:#fff;' : '')
      + 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
      + '\u{1F4C4} ' + escapeHtml(f) + '</div>';
  }).join('');

  // Bind click on each item (avoid inline onclick which requires global function)
  _mentionDropdown.querySelectorAll('.mention-item').forEach(el => {
    el.addEventListener('mousedown', (e) => {
      e.preventDefault(); // keep focus on textarea
      _selectMention(parseInt(el.dataset.idx, 10));
    });
  });

  _mentionDropdown.style.display = '';
}

function _closeMention() {
  if (_mentionDropdown) _mentionDropdown.style.display = 'none';
  _mentionResults = [];
  _mentionStart = -1;
}

function _isMentionOpen() {
  return _mentionDropdown
    && _mentionDropdown.style.display !== 'none'
    && _mentionResults.length > 0;
}

function _onMentionKeydown(e) {
  if (!_isMentionOpen()) return;

  if (e.key === 'ArrowDown') {
    e.preventDefault();
    e.stopImmediatePropagation();
    _mentionSelected = Math.min(_mentionSelected + 1, _mentionResults.length - 1);
    _showMentionDropdown();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    e.stopImmediatePropagation();
    _mentionSelected = Math.max(_mentionSelected - 1, 0);
    _showMentionDropdown();
  } else if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    e.stopImmediatePropagation();
    _selectMention(_mentionSelected);
  } else if (e.key === 'Escape') {
    e.preventDefault();
    e.stopImmediatePropagation();
    _closeMention();
  }
}

function _selectMention(idx) {
  const file = _mentionResults[idx];
  if (!file) return;

  // Capture positions before closing
  const start = _mentionStart;
  const input = document.getElementById('input');
  const cursorEnd = input.selectionStart;
  _closeMention();

  // Replace @query with @filename in the input
  const text = input.value;
  const replacement = '@' + file + ' ';
  input.value = text.substring(0, start) + replacement + text.substring(cursorEnd);
  const newCursor = start + replacement.length;
  input.setSelectionRange(newCursor, newCursor);
  input.focus();

  // Read file content and add as attachment
  action$('fs_read_file', { path: file }).subscribe(data => {
    if (data.error) {
      console.error('File mention read error:', data.error);
      return;
    }
    if (data.content) {
      pendingFiles.push({
        filename: file,
        content: data.content,
        data: data.encoding === 'base64' ? data.content : btoa(unescape(encodeURIComponent(data.content))),
        mime_type: 'text/plain',
      });
      renderAttachments();
    }
  });
}

// Disabled: @file autocomplete was too slow (5-6s latency) and interfered with typing.
// To re-enable, uncomment the line below.
// document.addEventListener('DOMContentLoaded', () => setTimeout(initFileMention, 100));
