// ── RxJS Action Bus ─────────────────────────────────────────────
// Central reactive bus for all server actions.
// Every action is fire-and-forget via fetch, result arrives via SSE.
//
// Usage:
//   action$('load_history', {conversation_id: cid, limit: 50})
//     .subscribe(data => renderHistory(data));
//
//   fireAction('compact', {conversation_id: cid});  // fire-and-forget, no result needed

const { Subject, filter, take, map, catchError, of, EMPTY, first, tap } = rxjs;

// Central subject — all SSE command_result events flow through here
const _commandResult$ = new rxjs.Subject();

// Track pending action count for loading indicator
let _pendingActions = 0;

function _updateLoadingState() {
  const el = document.getElementById('actionLoading');
  if (el) el.style.display = _pendingActions > 0 ? 'block' : 'none';
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

  // Fire the fetch (no await)
  const body = { action: actionName, ...params };
  if (!body.conversation_id && typeof conversationId !== 'undefined' && conversationId) {
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
  _pendingActions++;
  _updateLoadingState();

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

  // Return filtered observable: wait for the matching command_result.
  // When the server tags the result with a conversation_id, only
  // accept matches for the conv this call was issued from. Untagged
  // results (older code paths without conv scoping) are accepted
  // unconditionally so nothing regresses.
  return _commandResult$.pipe(
    filter(r => {
      if (r.action !== actionName) return false;
      if (r._callId && r._callId !== _callId) return false;
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
    tap(() => {
      _pendingActions = Math.max(0, _pendingActions - 1);
      _updateLoadingState();
    }),
  );
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
