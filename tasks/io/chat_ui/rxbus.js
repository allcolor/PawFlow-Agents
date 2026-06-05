// ── RxJS Action Bus ─────────────────────────────────────────────
// Central reactive bus for all server actions.
// Every action is fire-and-forget via fetch, result arrives via SSE.
//
// Usage:
//   action$('load_history', {conversation_id: cid, limit: 50})
//     .subscribe(data => renderHistory(data));
//
//   fireAction('compact', {conversation_id: cid});  // fire-and-forget, no result needed

const { filter, take, map, catchError, of, EMPTY, first, finalize, defer } = rxjs;

// Central command-result bus. Replay a short window so an SSE replay or
// ultra-fast inline response cannot be lost before action$ subscribers attach.
const _commandResult$ = new rxjs.ReplaySubject(200, 30000);

// Dedicated UI command-result stream. Action results must not depend on the
// conversation SSE lifecycle: resumeConv deliberately closes/reopens that
// stream around history rendering. This per-tab bus stays open and receives
// command_result for every action$ call via _reply_conversation_id.
let _uiActionEventSource = null;
let _uiActionBusId = '';
let _uiActionLastActivity = 0;
let _uiActionCreatedAt = 0;
let _uiActionReconnectTimer = null;

function _actionClientId() {
  try {
    let cid = sessionStorage.getItem('pawflow_sse_client_id');
    if (!cid) {
      cid = 'tab-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      sessionStorage.setItem('pawflow_sse_client_id', cid);
    }
    return cid;
  } catch (_err) {
    return 'tab-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }
}

function _uiActionConversationId() {
  if (!_uiActionBusId) _uiActionBusId = '__ui__:' + _actionClientId();
  return _uiActionBusId;
}

function _closeUIActionSSE() {
  if (_uiActionReconnectTimer) {
    clearTimeout(_uiActionReconnectTimer);
    _uiActionReconnectTimer = null;
  }
  if (_uiActionEventSource) {
    try { _uiActionEventSource.close(); } catch (_) {}
  }
  _uiActionEventSource = null;
  _uiActionLastActivity = 0;
  _uiActionCreatedAt = 0;
}

function _reconnectUIActionSSE() {
  _closeUIActionSSE();
  _ensureUIActionSSE(true);
}

function _scheduleUIActionReconnect() {
  if (_uiActionReconnectTimer) return;
  _uiActionReconnectTimer = setTimeout(() => {
    _uiActionReconnectTimer = null;
    if (!_uiActionEventSource || _uiActionEventSource.readyState !== EventSource.OPEN) {
      _reconnectUIActionSSE();
    }
  }, 1000);
}

function _uiActionSSEIsStale() {
  if (!_uiActionEventSource) return false;
  const now = Date.now();
  if (_uiActionLastActivity && now - _uiActionLastActivity > 30000) return true;
  return !_uiActionLastActivity && _uiActionCreatedAt
    && now - _uiActionCreatedAt > 5000;
}

function _ensureUIActionSSE(force) {
  const busId = _uiActionConversationId();
  if (force || _uiActionSSEIsStale()) _closeUIActionSSE();
  if (_uiActionEventSource && _uiActionEventSource.readyState !== EventSource.CLOSED) return;
  const token = getToken();
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(busId)
    + '&client_id=' + encodeURIComponent(_actionClientId() + ':actions')
    + (token ? '&token=' + encodeURIComponent(token) : '')
    + '&replay=true';
  _uiActionCreatedAt = Date.now();
  _uiActionEventSource = new EventSource(url);
  _uiActionEventSource.onopen = () => {
    _uiActionLastActivity = Date.now();
    _syncPendingActionsFromServer();
  };
  _uiActionEventSource.addEventListener('sse_ping', () => {
    _uiActionLastActivity = Date.now();
    _syncPendingActionsFromServer();
  });
  _uiActionEventSource.addEventListener('command_result', (e) => {
    _uiActionLastActivity = Date.now();
    const data = JSON.parse(e.data || '{}');
    _pushCommandResult(data);
  });
  _uiActionEventSource.onerror = () => {
    // Browser EventSource can remain half-open across restarts. Recreate the
    // per-tab action stream so context-menu actions do not wait forever on a
    // dead command_result channel.
    if (!_uiActionEventSource || _uiActionEventSource.readyState === EventSource.CLOSED) {
      _uiActionEventSource = null;
      _uiActionLastActivity = 0;
    } else {
      _scheduleUIActionReconnect();
    }
  };
}

// Track pending UI actions for the loading indicator. Silent action$ calls are
// excluded so background polling does not flicker the header.
let _pendingActions = 0;
const _pendingActionItems = new Map();
const _PENDING_ACTION_SHOW_AFTER_MS = 500;

function _formatActionLabel(actionName, opts) {
  if (opts && opts.label) return String(opts.label);
  return String(actionName || 'action')
    .replace(/^conv_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, ch => ch.toUpperCase());
}

function _escapeActionHtml(text) {
  if (typeof escapeHtml === 'function') return escapeHtml(String(text || ''));
  return String(text || '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
}

function _trackPendingAction(callId, actionName, opts) {
  _pendingActions++;
  _pendingActionItems.set(callId, {
    action: actionName,
    label: _formatActionLabel(actionName, opts),
    startedAt: Date.now(),
    visible: false,
  });
  window.setTimeout(() => {
    const item = _pendingActionItems.get(callId);
    if (!item) return;
    item.visible = true;
    _updateLoadingState();
  }, _PENDING_ACTION_SHOW_AFTER_MS);
  _updateLoadingState();
}

function _untrackPendingAction(callId) {
  if (!_pendingActionItems.has(callId)) return;
  _pendingActionItems.delete(callId);
  _pendingActions = Math.max(0, _pendingActions - 1);
  _updateLoadingState();
}

function _updateLoadingState() {
  const el = document.getElementById('actionLoading');
  if (!el) return;
  const visibleItems = Array.from(_pendingActionItems.values()).filter(item => item.visible);
  if (!visibleItems.length) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }
  const label = visibleItems.length === 1
    ? visibleItems[0].label
    : visibleItems.length + ' actions';
  const working = (typeof t === 'function') ? t('working') : 'Working';
  el.innerHTML = '<span class="ual-spinner">✻</span>'
    + '<span class="ual-text">' + _escapeActionHtml(working) + ': ' + _escapeActionHtml(label) + '<span class="ual-dots"></span></span>'
    + '<span class="ual-bar" aria-hidden="true"></span>';
  el.style.display = 'inline-flex';
}

function _syncPendingActionsFromServer() {
  if (!_pendingActionItems.size) return;
  const callIds = Array.from(_pendingActionItems.keys());
  const body = {
    action: 'list_ui_action_status',
    reply_conversation_id: _uiActionConversationId(),
    call_ids: callIds,
  };
  if (typeof conversationId !== 'undefined' && conversationId) {
    body.conversation_id = conversationId;
  }
  fetch(API.replace(/\/api\/agent$/, '/api/ui'), {
    method: 'POST',
    headers: getAuthHeaders(),
    body: JSON.stringify(body),
    credentials: 'same-origin',
  }).then(resp => resp.json()).then(data => {
    const rows = data && Array.isArray(data.actions) ? data.actions : [];
    rows.forEach(row => {
      if (!row || !row._callId) return;
      if (row.status === 'done' || row.status === 'error') {
        _pushCommandResult(row);
      } else if (row.status === 'unknown') {
        _untrackPendingAction(row._callId);
      }
    });
  }).catch(err => {
    console.warn('[action$] pending action sync failed', err);
  });
}

/**
 * Fire an action and return an Observable of the result.
 * The Observable emits once (the result) then completes.
 * NO timeout: long-running actions (compact, context rebuild, media gen)
 * can take minutes. The only timeout in PawFlow is the LLM watchdog.
 * If a result never arrives, the page reload clears it.
 *
 * @param {string} actionName - Server action name
 * @param {object} params - Action parameters (conversation_id, etc.)
 * @param {object} opts - Options: {silent: bool}
 * @returns {rxjs.Observable} Observable that emits the parsed result
 */
function action$(actionName, params = {}, opts = {}) {
  return defer(() => {
    // Fire the fetch (no await). Results are always delivered through the
    // per-tab UI SSE bus, not through the HTTP response body.
    _ensureUIActionSSE();
    const body = { action: actionName, ...params };
    if (!opts.skipConversationId && !body.conversation_id && typeof conversationId !== 'undefined' && conversationId) {
      body.conversation_id = conversationId;
    }
    // Capture the conversation_id this call is scoped to. The filter
    // below rejects results from a DIFFERENT conv that happen to share
    // the same action name, so a subscription left over from a previous
    // conv can't swallow the current conv's result.
    const _callConvId = body.conversation_id || '';
    // Unique call id so multiple concurrent calls to the same action
    // on the same conv don't route their sync results to each other.
    const _callId = Math.random().toString(36).slice(2) + Date.now().toString(36);
    body._call_id = _callId;
    body._reply_conversation_id = _uiActionConversationId();
    const _trackPending = !opts.silent;
    if (_trackPending) {
      _trackPendingAction(_callId, actionName, opts);
    }

    // UI commands go to /api/ui (dedicated task slot, isolated from
    // agent execution). Derive from API (/api/agent) by swapping the
    // last path segment so custom deployments keep working.
    const _uiUrl = API.replace(/\/api\/agent$/, '/api/ui');
    fetch(_uiUrl, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify(body),
      credentials: 'same-origin',
    }).then(resp => {
      if (resp.status === 401 || resp.status === 403) {
        if (typeof LOGIN_URL !== 'undefined' && LOGIN_URL) {
          window.location.href = LOGIN_URL;
        }
        return;
      }
      // Read response body — if it's NOT {"status":"accepted"}, it's a sync result
      return resp.json().then(data => {
        // Server-restart probe: every response carries server_start_time
        // so we can detect a backend bounce before the UI notices SSE is
        // stale. Defined in sse.js; may not be loaded in embedded views.
        try { if (typeof _checkServerRestart === 'function') _checkServerRestart(data); } catch (_) {}
        if (data && data.status !== 'accepted') {
          // Sync response — tag with call's conv + id so the filter
          // below routes it to the right subscriber.
          _commandResult$.next({
            action: actionName, result: JSON.stringify(data),
            conversation_id: _callConvId, _callId,
          });
        }
        // else: accepted → result will arrive via SSE command_result
      });
    }).catch(err => {
      console.warn('[action$] fetch failed for', actionName, err);
      _commandResult$.next({
        action: actionName, error: err.message || 'Network error',
        conversation_id: _callConvId, _callId,
      });
    });

    // Return filtered observable: wait for the exact per-call result.
    // The bus is replayed, so accepting untagged historical results would
    // route stale responses into new calls. Browser action$ always sends
    // _call_id, and the backend echoes it as _callId for every result path.
    return _commandResult$.pipe(
      filter(r => {
        if (r.action !== actionName) return false;
        if (r._callId !== _callId) return false;
        if (_callConvId && r.conversation_id && r.conversation_id !== _callConvId) return false;
        return true;
      }),
      first(),
      map(r => {
        if (r.error) throw new Error(r.error);
        // Parse result string into object
        if (typeof r.result === 'string') {
          try { return JSON.parse(r.result); } catch { return r.result; }
        }
        return r.result || r;
      }),
      catchError(err => of({ error: err.message || String(err) })),
      finalize(() => {
        if (_trackPending) _untrackPendingAction(_callId);
      }),
    );
  });
}

/**
 * Fire an action with no result handling (fire-and-forget).
 * Still decrements pending count when result arrives.
 */
function fireAction(actionName, params = {}) {
  action$(actionName, params).subscribe({
    error: () => {},  // silently consumed
  });
}

/**
 * Feed an SSE command_result event into the bus.
 * Called from the SSE event handler.
 */
function _pushCommandResult(data) {
  _commandResult$.next(data);
  if (window._pawflowExtRuntime && data && data.action) {
    window._pawflowExtRuntime.fireHook('command_result', {
      action: data.action,
      conversationId: data.conversation_id || null,
      callId: data._callId || '',
      hasError: !!data.error,
    });
  }
}

/**
 * Subscribe to all results for a specific action (long-lived, not take(1)).
 * Useful for actions that may fire multiple times (polling, etc.)
 */
function onAction$(actionName) {
  return _commandResult$.pipe(
    filter(r => r.action === actionName),
    map(r => {
      if (typeof r.result === 'string') {
        try { return JSON.parse(r.result); } catch { return r.result; }
      }
      return r.result || r;
    }),
  );
}
