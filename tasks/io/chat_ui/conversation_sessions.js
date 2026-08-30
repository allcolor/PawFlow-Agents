// Live conversation sessions for the tiled workspace.
//
// Legacy renderers intentionally keep using the canonical global names and DOM
// IDs. A session callback activates its own state and transcript synchronously,
// then restores the focused session. This provides an incremental migration seam
// without duplicating the Webchat application or transcript authority.
var _conversationSessions = new Map();
var _conversationFocusedSession = null;
var _conversationActiveSession = null;
var _conversationCanonicalClaimed = false;
var _conversationFocusGeneration = 0;

function _conversationSurfaceId(conversationId) {
  return 'webchat-' + String(conversationId || '').replace(/[^a-zA-Z0-9._-]/g, '_');
}

function _conversationSessionPrefix(conversationId) {
  var value = String(conversationId || '');
  var hash = 2166136261;
  for (var index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return 'pf-conv-' + (hash >>> 0).toString(36) + '-';
}

function _newConversationSession(conversationId) {
  var cid = String(conversationId || '').trim();
  if (!cid) throw new Error('BUG: conversation session missing conversation id');
  return {
    conversationId: cid,
    surfaceId: _conversationSurfaceId(cid),
    title: cid.slice(0, 8),
    panel: null,
    messagesRoot: null,
    loaded: false,
    loading: false,
    loadGeneration: 0,
    focusGeneration: 0,
    connectionState: 'idle',
    createdAt: Date.now(),
    lastFocusedAt: 0,
    domPrefix: _conversationSessionPrefix(cid),
    eventSource: null,
    sending: false,
    pendingAgent: null,
    selectedAgent: '',
    sseRetryCount: 0,
    sseReconnectTimer: null,
    streams: {},
    permissionMode: 'default',
    nicknameMap: {},
    pendingFiles: [],
    lastSSEActivity: 0,
    serverMsgCount: 0,
    sseHealthTimer: null,
    resourcesTimer: null,
    currentOffset: 0,
    historyCursor: { offset: 0, before_msg_id: '' },
    hasMoreMessages: false,
    loadingMore: false,
    replyTo: null,
    seenMsgIds: new Set(),
    liveCountedMsgIds: new Set(),
    selectedMsgIds: new Set(),
    histTaskBlocks: {},
    activeInteractions: {},
    activeDoneAt: {},
    typingInterval: null,
    viewMode: 'classic',
    simplifiedTurns: new Map(),
    turnRuntime: new Map(),
    turnOpen: null,
    turnSeq: 0,
    taskBlocks: {},
    pendingToolResults: {},
    thinkingElements: {},
    delegateThinkingElements: {},
    delegateGroups: {},
    delegateSubBlocks: {},
    btwElements: {},
    btwTexts: {},
    pendingThinkingPreviews: {},
    sseCid: cid,
    sseOnReadyCallback: null,
    sseCreatedAt: 0,
    contextUsage: {},
    compactPending: {},
    statusText: typeof t === 'function' ? t('ready') : 'Ready',
    autoScroll: true,
    suppressTopLoadUntil: 0,
  };
}

function getConversationSession(conversationId) {
  return _conversationSessions.get(String(conversationId || '')) || null;
}

function captureConversationSession() {
  return _conversationActiveSession || _conversationFocusedSession || null;
}

function focusedConversationSession() {
  return _conversationFocusedSession;
}

function focusedConversationId() {
  return _conversationFocusedSession ? _conversationFocusedSession.conversationId
    : (typeof conversationId !== 'undefined' ? conversationId : null);
}

function focusedConversationAgent() {
  return _conversationFocusedSession ? _conversationFocusedSession.selectedAgent
    : (typeof selectedAgent !== 'undefined' ? selectedAgent : '');
}

function isFocusedConversationSession(sessionOrId) {
  var session = typeof sessionOrId === 'string'
    ? getConversationSession(sessionOrId) : sessionOrId;
  return !!session && session === _conversationFocusedSession;
}

function _conversationDomNodes(session) {
  if (!session || !session.messagesRoot) return [];
  var nodes = [session.messagesRoot];
  if (typeof session.messagesRoot.querySelectorAll === 'function') {
    nodes = nodes.concat(Array.from(
      session.messagesRoot.querySelectorAll('[id], [data-conversation-local-id]')
    ));
  }
  if (session.panel && typeof session.panel.querySelectorAll === 'function') {
    nodes = nodes.concat(Array.from(
      session.panel.querySelectorAll('[data-conversation-local-id]')
    ));
  }
  return Array.from(new Set(nodes));
}

function _setConversationSessionDomActive(session, active) {
  _conversationDomNodes(session).forEach(function(node) {
    if (!node || !node.dataset) return;
    var localId = node.dataset.conversationLocalId || '';
    if (!localId && node.id) {
      localId = String(node.id);
      if (localId.indexOf(session.domPrefix) === 0) {
        localId = localId.slice(session.domPrefix.length);
      }
      node.dataset.conversationLocalId = localId;
    }
    if (!localId) return;
    node.id = active ? localId : session.domPrefix + localId;
  });
}

function _saveConversationSessionState(session) {
  if (!session) return;
  session.eventSource = eventSource;
  session.sending = sending;
  session.pendingAgent = pendingAgent;
  session.selectedAgent = selectedAgent;
  session.sseRetryCount = sseRetryCount;
  session.sseReconnectTimer = sseReconnectTimer;
  session.streams = streams;
  session.permissionMode = permissionMode;
  session.nicknameMap = nicknameMap;
  session.pendingFiles = pendingFiles;
  session.lastSSEActivity = lastSSEActivity;
  session.serverMsgCount = serverMsgCount;
  session.sseHealthTimer = sseHealthTimer;
  session.resourcesTimer = resourcesTimer;
  session.currentOffset = currentOffset;
  session.historyCursor = historyCursor;
  session.hasMoreMessages = hasMoreMessages;
  session.loadingMore = loadingMore;
  session.replyTo = _replyTo;
  session.seenMsgIds = _seenMsgIds;
  session.liveCountedMsgIds = _liveCountedMsgIds;
  session.selectedMsgIds = _selectedMsgIds;
  session.histTaskBlocks = _histTaskBlocks;
  session.activeInteractions = activeInteractions;
  session.activeDoneAt = _activeDoneAt;
  session.typingInterval = typingInterval;
  session.viewMode = PAWFLOW_CHAT_VIEW_MODE;
  session.simplifiedTurns = simplifiedTurns;
  session.turnRuntime = _turnRuntime;
  session.turnOpen = _turnOpen;
  session.turnSeq = _turnSeq;
  session.taskBlocks = _taskBlocks;
  session.pendingToolResults = _pendingToolResults;
  session.thinkingElements = thinkingElements;
  session.delegateThinkingElements = delegateThinkingElements;
  session.delegateGroups = _delegateGroups;
  session.delegateSubBlocks = _delegateSubBlocks;
  session.btwElements = btwElements;
  session.btwTexts = btwTexts;
  session.pendingThinkingPreviews = _pendingThinkingPreviews;
  session.sseCid = _sseCid;
  session.sseOnReadyCallback = _sseOnReadyCallback;
  session.sseCreatedAt = _sseCreatedAt;
  session.contextUsage = window._contextUsage || {};
  session.compactPending = window._compactPending || {};
  var status = document.getElementById('status');
  if (status) session.statusText = status.textContent || '';
  session.autoScroll = _autoScroll;
  session.suppressTopLoadUntil = _suppressTopLoadUntil;
}

function _applyConversationSessionState(session) {
  if (!session) return;
  conversationId = session.conversationId;
  eventSource = session.eventSource;
  sending = session.sending;
  pendingAgent = session.pendingAgent;
  selectedAgent = session.selectedAgent;
  sseRetryCount = session.sseRetryCount;
  sseReconnectTimer = session.sseReconnectTimer;
  streams = session.streams;
  permissionMode = session.permissionMode;
  nicknameMap = session.nicknameMap;
  pendingFiles = session.pendingFiles;
  lastSSEActivity = session.lastSSEActivity;
  serverMsgCount = session.serverMsgCount;
  sseHealthTimer = session.sseHealthTimer;
  resourcesTimer = session.resourcesTimer;
  currentOffset = session.currentOffset;
  historyCursor = session.historyCursor;
  hasMoreMessages = session.hasMoreMessages;
  loadingMore = session.loadingMore;
  _replyTo = session.replyTo;
  _seenMsgIds = session.seenMsgIds;
  _liveCountedMsgIds = session.liveCountedMsgIds;
  _selectedMsgIds = session.selectedMsgIds;
  _histTaskBlocks = session.histTaskBlocks;
  activeInteractions = session.activeInteractions;
  _activeDoneAt = session.activeDoneAt;
  typingInterval = session.typingInterval;
  PAWFLOW_CHAT_VIEW_MODE = session.viewMode;
  simplifiedTurns = session.simplifiedTurns;
  _turnRuntime = session.turnRuntime;
  _turnOpen = session.turnOpen;
  _turnSeq = session.turnSeq;
  _taskBlocks = session.taskBlocks;
  _pendingToolResults = session.pendingToolResults;
  thinkingElements = session.thinkingElements;
  delegateThinkingElements = session.delegateThinkingElements;
  _delegateGroups = session.delegateGroups;
  _delegateSubBlocks = session.delegateSubBlocks;
  btwElements = session.btwElements;
  btwTexts = session.btwTexts;
  _pendingThinkingPreviews = session.pendingThinkingPreviews;
  _sseCid = session.sseCid;
  _sseOnReadyCallback = session.sseOnReadyCallback;
  _sseCreatedAt = session.sseCreatedAt;
  window._contextUsage = session.contextUsage;
  window._compactPending = session.compactPending;
  var status = document.getElementById('status');
  if (status) status.textContent = session.statusText || '';
  _autoScroll = session.autoScroll;
  _suppressTopLoadUntil = session.suppressTopLoadUntil;
  if (document.documentElement && document.documentElement.classList) {
    document.documentElement.classList.toggle(
      'simplified-chat-view', session.viewMode === 'simplified'
    );
  }
}

function _wrapConversationSessionCallback(sessionOrId, callback) {
  var session = typeof sessionOrId === 'string'
    ? getConversationSession(sessionOrId) : sessionOrId;
  if (!session || typeof callback !== 'function') return callback;
  return function() {
    var args = arguments;
    var self = this;
    return withConversationSession(session, function() {
      return callback.apply(self, args);
    });
  };
}

function captureConversationSessionCallback(callback) {
  return _wrapConversationSessionCallback(captureConversationSession(), callback);
}

function withConversationSession(sessionOrId, callback) {
  var session = typeof sessionOrId === 'string'
    ? getConversationSession(sessionOrId) : sessionOrId;
  if (!session || typeof callback !== 'function') {
    return typeof callback === 'function' ? callback() : undefined;
  }
  var previous = _conversationActiveSession;
  if (previous === session) {
    try { return callback(); }
    finally {
      if (_conversationActiveSession === session) {
        _saveConversationSessionState(session);
      }
    }
  }
  if (previous) {
    _saveConversationSessionState(previous);
    _setConversationSessionDomActive(previous, false);
  }
  _conversationActiveSession = session;
  _applyConversationSessionState(session);
  _setConversationSessionDomActive(session, true);
  try {
    return callback();
  } finally {
    // A callback may intentionally focus/open another conversation. In that
    // case focusConversationSession already saved this session and projected
    // the new one; restoring `previous` here would undo the user's action and
    // copy the new globals into the old session.
    if (_conversationActiveSession === session) {
      _saveConversationSessionState(session);
      _setConversationSessionDomActive(session, false);
      _conversationActiveSession = previous;
      if (previous) {
        _applyConversationSessionState(previous);
        _setConversationSessionDomActive(previous, true);
      }
    }
  }
}

function bindObservableToConversationSession(observable, session) {
  if (!observable || !session || typeof rxjs === 'undefined' || !rxjs.Observable) {
    return observable;
  }
  return new rxjs.Observable(function(observer) {
    var subscription = observable.subscribe({
      next: _wrapConversationSessionCallback(session, function(value) {
        observer.next(value);
      }),
      error: _wrapConversationSessionCallback(session, function(error) {
        observer.error(error);
      }),
      complete: _wrapConversationSessionCallback(session, function() {
        observer.complete();
      }),
    });
    return function() { subscription.unsubscribe(); };
  });
}

function _conversationTitle(conversationId, fallback) {
  var rows = (window._ownConvs || []).concat(window._sharedConvs || []);
  var match = rows.find(function(row) {
    return row && row.conversation_id === conversationId;
  });
  return String((match && (match.title || match.preview)) || fallback
    || (typeof t === 'function' ? t('newConversation') : 'Conversation'));
}

function updateConversationSessionTitle(sessionOrId, value) {
  var session = typeof sessionOrId === 'string'
    ? getConversationSession(sessionOrId) : sessionOrId;
  var title = String(value || '').trim();
  if (!session || !title) return false;
  session.title = title;
  if (typeof workspaceSetConversationTitle === 'function') {
    workspaceSetConversationTitle(session.conversationId, title);
  } else if (typeof workspaceSetSurfaceTitle === 'function') {
    workspaceSetSurfaceTitle(session.surfaceId, title);
  }
  if (session === _conversationFocusedSession) {
    var input = document.getElementById('input');
    if (input) input.setAttribute('aria-label', 'Message conversation ' + title);
  }
  return true;
}

function syncConversationSessionTitles(rows) {
  (rows || []).forEach(function(row) {
    if (!row || !row.conversation_id) return;
    var title = row.title || row.preview;
    if (title) updateConversationSessionTitle(row.conversation_id, title);
  });
}

function _moveConversationSharedPanels() {
  var shell = document.getElementById('workspaceShell');
  var scroller = document.getElementById('workspaceScroller');
  if (!shell || !scroller) return;
  var host = document.getElementById('workspaceSharedPanels');
  if (!host) {
    host = document.createElement('div');
    host.id = 'workspaceSharedPanels';
    host.className = 'workspace-shared-panels';
    shell.insertBefore(host, scroller);
  }
  ['confirmationsPanel', 'schedsPanel', 'filesPanel'].forEach(function(id) {
    var panel = document.getElementById(id);
    if (panel && panel.parentNode !== host) host.appendChild(panel);
  });
}

function _createConversationMessagesRoot(session, body) {
  var wrap = document.createElement('div');
  wrap.className = 'messages-wrap';
  var messages = document.createElement('div');
  messages.className = 'messages';
  messages.dataset.conversationLocalId = 'messages';
  wrap.appendChild(messages);

  var nav = document.createElement('div');
  nav.className = 'scroll-nav';
  nav.dataset.conversationLocalId = 'scrollNav';
  var top = document.createElement('button');
  top.type = 'button';
  top.innerHTML = '&#x2191;';
  top.title = 'Scroll to top';
  top.onclick = _wrapConversationSessionCallback(session, function() {
    if (typeof scrollMessagesTop === 'function') scrollMessagesTop();
  });
  var bottom = document.createElement('button');
  bottom.type = 'button';
  bottom.innerHTML = '&#x2193;';
  bottom.title = 'Scroll to bottom';
  bottom.onclick = _wrapConversationSessionCallback(session, function() {
    if (typeof scrollBottom === 'function') scrollBottom(true);
  });
  nav.append(top, bottom);
  wrap.appendChild(nav);
  body.appendChild(wrap);
  return messages;
}

function _createConversationSurface(session) {
  _moveConversationSharedPanels();
  var panel = session.panel || null;
  var messages = session.messagesRoot || null;
  if (!_conversationCanonicalClaimed) {
    panel = document.getElementById('tabContentChat');
    messages = document.getElementById('messages');
    if (panel && messages) {
      _conversationCanonicalClaimed = true;
      if (typeof workspaceUnregisterSurface === 'function') {
        workspaceUnregisterSurface('chat', true);
      }
      panel.id = 'tabContent_' + session.surfaceId;
      panel.dataset.tab = session.surfaceId;
      messages.dataset.conversationLocalId = 'messages';
      var scrollNav = document.getElementById('scrollNav');
      if (scrollNav) scrollNav.dataset.conversationLocalId = 'scrollNav';
    }
  }
  if (!panel || !messages) {
    panel = document.createElement('div');
    panel.className = 'tab-content conversation-workspace-surface';
    panel.id = 'tabContent_' + session.surfaceId;
    panel.dataset.tab = session.surfaceId;
    var body = document.createElement('div');
    body.className = 'workspace-surface-body';
    panel.appendChild(body);
    messages = _createConversationMessagesRoot(session, body);
  }
  session.panel = panel;
  session.messagesRoot = messages;
  panel.classList.add('conversation-workspace-surface');
  if (typeof installMessagesRootHandlers === 'function') {
    installMessagesRootHandlers(messages, session);
  }
  _setConversationSessionDomActive(session, false);
  if (typeof workspaceRegisterSurface === 'function') {
    workspaceRegisterSurface(panel, {
      tabId: session.surfaceId,
      type: 'webchat',
      title: session.title,
      conversationId: session.conversationId,
      closable: true,
      close: function() { closeConversationSession(session.conversationId); },
    });
  }
  if (typeof _msgObserver !== 'undefined' && _msgObserver && messages) {
    try { _msgObserver.observe(messages, { childList: true }); } catch (_error) {}
  }
}

function ensureConversationSurface(session) {
  if (!session) return null;
  var registered = typeof _workspaceSurfaces !== 'undefined'
    && _workspaceSurfaces[session.surfaceId];
  if (!registered) _createConversationSurface(session);
  return session.panel;
}

function ensureConversationSession(conversationId, options) {
  var cid = String(conversationId || '').trim();
  if (!cid) throw new Error('BUG: conversation session missing conversation id');
  var session = _conversationSessions.get(cid);
  if (session) {
    ensureConversationSurface(session);
    if (options && options.title) updateConversationSessionTitle(session, options.title);
    return session;
  }
  session = _newConversationSession(cid);
  session.title = _conversationTitle(cid, options && options.title);
  _conversationSessions.set(cid, session);
  ensureConversationSurface(session);
  return session;
}

function _projectFocusedConversation(session) {
  if (!session) return;
  if (typeof highlightConv === 'function') highlightConv(session.conversationId);
  if (typeof refreshAppearanceContext === 'function') refreshAppearanceContext();
  if (typeof _setInputEnabled === 'function') _setInputEnabled(true);
  if (typeof updateDeleteBtn === 'function') updateDeleteBtn();
  if (typeof updateActiveAgentBadge === 'function') updateActiveAgentBadge();
  if (typeof updatePermissionBadge === 'function') updatePermissionBadge();
  if (typeof updateViewMenuVisibility === 'function') updateViewMenuVisibility();
  if (typeof updateActivePanel === 'function') updateActivePanel();
  if (typeof renderAttachments === 'function') renderAttachments();
  if (typeof loadResources === 'function') loadResources(session.conversationId);
  var input = document.getElementById('input');
  if (input) input.setAttribute('aria-label', 'Message conversation ' + session.title);
}

function focusConversationSession(sessionOrId, options) {
  var session = typeof sessionOrId === 'string'
    ? getConversationSession(sessionOrId) : sessionOrId;
  if (!session) return false;
  var previous = _conversationActiveSession;
  if (previous) {
    _saveConversationSessionState(previous);
    _setConversationSessionDomActive(previous, false);
  }
  _conversationFocusedSession = session;
  _conversationActiveSession = session;
  _conversationFocusGeneration++;
  session.focusGeneration = _conversationFocusGeneration;
  session.lastFocusedAt = Date.now();
  _applyConversationSessionState(session);
  _setConversationSessionDomActive(session, true);
  if (!options || options.project !== false) _projectFocusedConversation(session);
  return true;
}

function openWorkspaceConversation(conversationId, options) {
  options = options || {};
  var session = ensureConversationSession(conversationId, options);
  if (typeof workspaceFocusSurface === 'function') {
    workspaceFocusSurface(session.surfaceId, {
      noConversationFocus: true,
      noScroll: !!options.noScroll,
    });
  }
  focusConversationSession(session);
  if ((!session.loaded && !session.loading) || options.force) {
    if (typeof loadConversationSession === 'function') {
      loadConversationSession(session, !!options.force);
    }
  }
  return session;
}

function releaseConversationSessionIfUnused(conversationId) {
  var session = getConversationSession(conversationId);
  if (!session) return false;
  var hasBoundSurface = typeof _workspaceSurfaces !== 'undefined'
    && Object.keys(_workspaceSurfaces).some(function(tabId) {
      var entry = _workspaceSurfaces[tabId];
      return entry && entry.conversationId === session.conversationId;
    });
  if (hasBoundSurface) return false;
  withConversationSession(session, function() {
    if (eventSource) { try { eventSource.close(); } catch (_error) {} eventSource = null; }
    if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
    if (sseHealthTimer) { clearInterval(sseHealthTimer); sseHealthTimer = null; }
    if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
  });
  var wasFocused = session === _conversationFocusedSession;
  if (typeof workspaceUnregisterSurface === 'function') {
    workspaceUnregisterSurface(session.surfaceId);
  }
  if (session.panel) session.panel.remove();
  _conversationSessions.delete(session.conversationId);
  if (wasFocused) {
    _conversationFocusedSession = null;
    _conversationActiveSession = null;
    var nextTab = typeof workspaceSelectedTab === 'function' ? workspaceSelectedTab() : '';
    var entry = typeof _workspaceSurfaces !== 'undefined' ? _workspaceSurfaces[nextTab] : null;
    if (entry && entry.conversationId) {
      focusConversationSession(entry.conversationId);
    } else {
      var next = _conversationSessions.values().next().value || null;
      if (next) focusConversationSession(next);
      else {
        conversationId = null;
        if (typeof renderEmptyState === 'function') renderEmptyState();
      }
    }
  }
  return true;
}

function closeConversationSession(conversationId) {
  var session = getConversationSession(conversationId);
  if (!session) return false;
  if (typeof workspaceUnregisterSurface === 'function') {
    workspaceUnregisterSurface(session.surfaceId);
  }
  if (session.panel) session.panel.remove();
  releaseConversationSessionIfUnused(session.conversationId);
  return true;
}

function closeAllConversationSessions() {
  Array.from(_conversationSessions.values()).forEach(function(session) {
    withConversationSession(session, function() {
      if (eventSource) { try { eventSource.close(); } catch (_error) {} eventSource = null; }
      if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
      if (sseHealthTimer) { clearInterval(sseHealthTimer); sseHealthTimer = null; }
    });
  });
}

function workspaceConversationIsOpen(conversationId) {
  return _conversationSessions.has(String(conversationId || ''));
}
