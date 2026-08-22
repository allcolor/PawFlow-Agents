// Conversation search overlay shared by Ctrl/Cmd+K and /search.
let _conversationSearchMessages = [];
let _conversationSearchRequest = 0;

function _conversationSearchOverlay() {
  return document.getElementById('conversationSearchDialog');
}

function closeConversationSearch() {
  const overlay = _conversationSearchOverlay();
  if (overlay) overlay.style.display = 'none';
}

function _conversationSearchRole(message) {
  return String(message.type || message.role || 'message');
}

function _conversationSearchText(message) {
  const content = message && message.content;
  if (typeof content === 'string') return content;
  if (content === null || content === undefined) return '';
  try { return JSON.stringify(content); }
  catch (_error) { return String(content); }
}

function _conversationSearchRender() {
  const input = document.getElementById('conversationSearchInput');
  const list = document.getElementById('conversationSearchResults');
  const count = document.getElementById('conversationSearchCount');
  if (!input || !list || !count) return;

  const query = input.value.trim().toLocaleLowerCase();
  if (!query) {
    list.innerHTML = '<div class="conversation-search-empty">'
      + escapeHtml(t('searchConversationHint')) + '</div>';
    count.textContent = '';
    return;
  }

  const matches = _conversationSearchMessages.filter(message =>
    _conversationSearchText(message).toLocaleLowerCase().includes(query));
  count.textContent = t('matchesFound', { n: matches.length });
  if (!matches.length) {
    list.innerHTML = '<div class="conversation-search-empty">'
      + escapeHtml(t('noMatchesFound')) + '</div>';
    return;
  }

  list.innerHTML = matches.slice(0, 100).map((message, index) => {
    const text = _conversationSearchText(message).replace(/\s+/g, ' ').trim();
    const role = _conversationSearchRole(message);
    const msgId = String(message.msg_id || message.id || '');
    return '<button type="button" class="conversation-search-result"'
      + ' data-msg-id="' + escapeHtml(msgId) + '" onclick="openConversationSearchResult(this)">'
      + '<span class="conversation-search-role">' + escapeHtml(role) + '</span>'
      + '<span class="conversation-search-preview">' + escapeHtml(text.slice(0, 320)) + '</span>'
      + '<span class="conversation-search-index">' + (index + 1) + '</span>'
      + '</button>';
  }).join('');
}

function _conversationSearchLoad() {
  const request = ++_conversationSearchRequest;
  const list = document.getElementById('conversationSearchResults');
  if (list) list.innerHTML = '<div class="conversation-search-empty">'
    + escapeHtml(t('searching')) + '</div>';
  action$('load_history', {
    conversation_id: conversationId,
    limit: 500,
    offset: 0,
  }).subscribe({
    next: data => {
      if (request !== _conversationSearchRequest) return;
      _conversationSearchMessages = data.messages || [];
      _conversationSearchRender();
    },
    error: error => {
      if (request !== _conversationSearchRequest || !list) return;
      list.innerHTML = '<div class="conversation-search-empty conversation-search-error">'
        + escapeHtml(error.message || String(error)) + '</div>';
    },
  });
}

function showConversationSearch(initialQuery) {
  if (!conversationId) {
    addMsg('system', t('noConv'));
    return false;
  }
  const overlay = _conversationSearchOverlay();
  const input = document.getElementById('conversationSearchInput');
  if (!overlay || !input) return false;
  overlay.style.display = 'flex';
  input.value = String(initialQuery || '');
  _conversationSearchMessages = [];
  _conversationSearchLoad();
  window.requestAnimationFrame(() => {
    input.focus();
    input.select();
  });
  return true;
}

function openConversationSearchResult(button) {
  const msgId = button && button.dataset ? button.dataset.msgId : '';
  closeConversationSearch();
  if (!msgId) return;
  const message = document.querySelector('[data-msgid="' + CSS.escape(msgId) + '"]');
  if (!message) return;
  message.scrollIntoView({ block: 'center', behavior: 'smooth' });
  message.classList.add('conversation-search-target');
  window.setTimeout(() => message.classList.remove('conversation-search-target'), 1600);
}

function insertComposerToken(token) {
  const input = document.getElementById('input');
  if (!input) return;
  const value = String(token || '');
  const start = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
  const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : start;
  input.setRangeText(value, start, end, 'end');
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
}

document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
    event.preventDefault();
    showConversationSearch('');
    return;
  }
  if (event.key === 'Escape'
      && _conversationSearchOverlay()?.style.display === 'flex') {
    closeConversationSearch();
  }
});

document.addEventListener('DOMContentLoaded', () => {
  const overlay = _conversationSearchOverlay();
  if (overlay) overlay.addEventListener('mousedown', event => {
    if (event.target === overlay) closeConversationSearch();
  });
});
