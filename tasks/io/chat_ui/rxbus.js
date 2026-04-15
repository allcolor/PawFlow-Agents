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
  _pendingActions++;
  _updateLoadingState();

  fetch(API, {
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
      if (data && data.status !== 'accepted') {
        // Sync response (no conversation_id = server ran it inline)
        _commandResult$.next({ action: actionName, result: JSON.stringify(data) });
      }
      // else: accepted → result will arrive via SSE command_result
    });
  }).catch(err => {
    console.warn('[action$] fetch failed for', actionName, err);
    _commandResult$.next({ action: actionName, error: err.message || 'Network error' });
  });

  // Return filtered observable: wait for the matching command_result
  return _commandResult$.pipe(
    filter(r => r.action === actionName),
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
