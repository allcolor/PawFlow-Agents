// Connect SSE for a conversation
var _sseOnReadyCallback = null;
var _sseClientId = null;
var _sseCreatedAt = 0;

function getSSEClientId() {
  if (_sseClientId) return _sseClientId;
  try {
    _sseClientId = sessionStorage.getItem('pawflow_sse_client_id');
    if (!_sseClientId) {
      _sseClientId = 'tab-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
      sessionStorage.setItem('pawflow_sse_client_id', _sseClientId);
    }
  } catch (_err) {
    _sseClientId = 'tab-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }
  return _sseClientId;
}

function connectSSE(cid, onReady, opts) {
  if (eventSource) eventSource.close();
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  _sseOnReadyCallback = onReady || null;
  startActiveSync();
  sseRetryCount = 0;  // reset so onopen doesn't think we're reconnecting
  const token = getToken();

  // Fan-out every named SSE event to UI extensions. Wraps addEventListener
  // on the new EventSource only so extensions see all events without each
  // native listener having to call _pawflowExtRuntime explicitly.
  function _wrapSseForExtensions(es) {
    if (!window._pawflowExtRuntime) return;
    var _orig = es.addEventListener.bind(es);
    es.addEventListener = function (type, listener, opts) {
      function wrapped(e) {
        try { listener(e); }
        finally {
          try {
            var data = null;
            if (e && typeof e.data === 'string' && e.data.length) {
              try { data = JSON.parse(e.data); } catch (_p) { data = e.data; }
            }
            window._pawflowExtRuntime.fireHook('sse_event',
              { event: type, data: data, conversationId: cid });
            if (type === 'tool_call') {
              window._pawflowExtRuntime.fireHook('tool_call_started', data || {});
            } else if (type === 'tool_result') {
              window._pawflowExtRuntime.fireHook('tool_call_completed', data || {});
            }
          } catch (_ext) { /* never let an extension hook break SSE */ }
        }
      }
      return _orig(type, wrapped, opts);
    };
  }
  // noReplay=true: caller is an explicit reload/switch that just refetched
  // the authoritative history from disk. The server must skip replaying
  // buffered events to this socket -- otherwise
  // the client _seenMsgIds gets populated with ids from the replayed
  // message_meta/done events before _renderHistory runs, and addMsg() dedups
  // legitimate history entries out of the render (transcript truncation).
  // A reload means reload, not replay.
  const _noReplay = !!(opts && opts.noReplay);
  const url = SSE_URL + '?conversation_id=' + encodeURIComponent(cid)
    + '&client_id=' + encodeURIComponent(getSSEClientId())
    + (token ? '&token=' + encodeURIComponent(token) : '')
    + (_noReplay ? '&replay=false' : '');
  _sseCreatedAt = Date.now();
  eventSource = new EventSource(url);
  _wrapSseForExtensions(eventSource);
  // Reset per-connection SSE state (declared in sse_state.js) and wire
  // event handlers (registered in sse_handlers_*.js) onto this socket.
  _sseCid = cid;
  _taskBlocks = {}; _pendingToolResults = {}; _serviceInstallProgress = {};
  thinkingElements = {}; delegateThinkingElements = {};
  _delegateGroups = {}; _delegateSubBlocks = {};
  btwElements = {}; btwTexts = {};
  _sseWireA();
  _sseWireB();
  // realtime.* listeners for LiveKit live sessions (conversation_livekit.js)
  if (typeof _lkWireSSE === 'function') _lkWireSSE();
  // usage.updated listener for the conversation cost gauge (usage_cost.js)
  if (typeof _usageWireSSE === 'function') _usageWireSSE();
  let sseHadError = false;  // track any error on this EventSource
  let sseEverConnected = false;  // distinguish reconnects from initial connect hiccups

  eventSource.onerror = (err) => {
    const state = eventSource ? eventSource.readyState : EventSource.CLOSED;
    console.warn('[SSE] error, readyState:', state, err);
    sseHadError = true;
    document.getElementById('status').textContent = t('reconnecting');
    // Do not rely on the browser's opaque EventSource retry after the server
    // intentionally closes a long-lived stream. Own the reconnect so live
    // rendering stays on the SSE channel instead of falling back to polling.
    if (eventSource) { try { eventSource.close(); } catch (_) {} }
    eventSource = null;
    // An expired/invalid session makes the events endpoint answer 401, but
    // EventSource exposes that only as an opaque onerror — so without this
    // probe the stream would back off forever behind a blank screen with no
    // hint to the user. Classify the failure once: 401/403 → re-auth,
    // anything else (network blip, proxy idle-kill) → silent backoff.
    _probeSSEAuth(cid);
    _scheduleSSEReconnect(cid);
  };

  eventSource.onopen = () => {
    pawflowDebugLog('[SSE] connected for', cid, sseHadError ? '(reconnect)' : '(initial)');
    if (_sseOnReadyCallback) { _sseOnReadyCallback(); _sseOnReadyCallback = null; }
    const wasDisconnected = sseEverConnected && sseHadError;
    sseEverConnected = true;
    sseRetryCount = 0;
    sseHadError = false;
    lastSSEActivity = Date.now();  // prime the watchdog
    // SSE health timer always on — protects paths that open a conversation
    // without explicitly arming the watchdog (direct URL, refresh).
    startSSEHealthTimer();
    if (wasDisconnected) {
      pawflowDebugLog('[SSE] reconnected; continuing with live SSE events');
      syncActiveFromServer();
    }
  };

  // Server emits `sse_ping` alongside the comment keepalive every ~15s.
  // The comment form is invisible to JS (SSE spec), the typed ping lets
  // us watchdog a silently half-open socket where EventSource never fires
  // onerror (laptop sleep, NAT eviction, proxy idle-kill).
  eventSource.addEventListener('sse_ping', () => {
    lastSSEActivity = Date.now();
  });

  eventSource.addEventListener('sse_reconnect', (e) => {
    lastSSEActivity = Date.now();
    pawflowDebugLog('[SSE] server requested reconnect', e.data || '');
    if (eventSource) { try { eventSource.close(); } catch (_) {} }
    eventSource = null;
    _scheduleSSEReconnect(cid);
  });
}

function _forceSSEReconnect(cid, opts) {
  if (!cid || cid !== conversationId) return;
  if (eventSource) { try { eventSource.close(); } catch (_) {} }
  eventSource = null;
  lastSSEActivity = 0;
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  connectSSE(cid, null, opts || { noReplay: true });
}

function _waitForSSEOpen(timeoutMs) {
  if (!eventSource) return Promise.resolve(false);
  if (eventSource.readyState === EventSource.OPEN) return Promise.resolve(true);
  const es = eventSource;
  return new Promise(resolve => {
    let done = false;
    const finish = ok => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(!!ok && es === eventSource && eventSource.readyState === EventSource.OPEN);
    };
    const timer = setTimeout(() => finish(false), timeoutMs || 2000);
    es.addEventListener('open', () => finish(true), { once: true });
    es.addEventListener('error', () => finish(false), { once: true });
  });
}

function _ensureSSEBeforeUserAction() {
  if (!conversationId) return Promise.resolve(false);
  if (_sseIsHealthy()) return Promise.resolve(true);
  if (eventSource && eventSource.readyState === EventSource.CONNECTING
      && Date.now() - _sseCreatedAt < 3000) {
    return _waitForSSEOpen(2000);
  }
  console.warn('[SSE] user action while stream is stale — reconnecting before send');
  _forceSSEReconnect(conversationId, {});
  return _waitForSSEOpen(2000);
}

// SSE liveness watchdog. Pings arrive every ~15s; if we haven't seen one
// in 45s the stream is silently dead even if readyState still says OPEN.
// Close + reconnect forcefully; event rendering must stay SSE-only.
var _sseWatchdogTimer = null;
function _startSSEWatchdog() {
  if (_sseWatchdogTimer) clearInterval(_sseWatchdogTimer);
  _sseWatchdogTimer = setInterval(() => {
    if (!eventSource || !conversationId) return;
    if (!lastSSEActivity) return;  // not yet connected
    const silentFor = Date.now() - lastSSEActivity;
    if (silentFor > 45000) {
      console.warn('[SSE] watchdog: no activity for', silentFor, 'ms — forcing reconnect');
      _forceSSEReconnect(conversationId, { noReplay: true });
    }
  }, 10000);
}
_startSSEWatchdog();

// ── Command result dispatchers ──────────────────────────────────
// Old _dispatchCommandResult and _renderLoadedHistory removed —
// all dispatch is now via RxJS action$ subscriptions in each module.

// Track the server's process-start epoch. Every /api/agent + /api/ui
// ack carries server_start_time; if it moves we know the backend was
// restarted while the browser wasn't looking — its EventSource may
// still appear OPEN (half-open TCP) but the new process has no
// subscribers for this conversation, so events get buffered and the
// UI never sees them. Force a clean reconnect without replaying stale
// buffered events into the chat renderer.
var _lastServerStartTime = null;
var _lastRestartReconnectAt = 0;
function _checkServerRestart(data) {
  // Only reconnect on an explicit server bounce (start_time changed).
  // The earlier "readyState !== OPEN → reconnect" heuristic ran every
  // time the SSE was legitimately mid-CONNECTING (e.g. right after a
  // reconnect) and re-triggered itself on every subsequent ack → the
  // stream never stabilised and responses disappeared. Keep this
  // strictly gated on the start_time signal; the scheduled backoff in
  // _scheduleSSEReconnect already handles truly dead sockets.
  if (!data || typeof data.server_start_time !== 'number') return;
  const prev = _lastServerStartTime;
  _lastServerStartTime = data.server_start_time;
  if (prev === null || prev === data.server_start_time) return;
  // Debounce: if we just reconnected for this reason, don't stack
  // another reconnect for every response that's racing in behind.
  const now = Date.now();
  if (now - _lastRestartReconnectAt < 3000) return;
  _lastRestartReconnectAt = now;
  console.warn('[SSE] server restart detected (start_time ' + prev
    + ' → ' + data.server_start_time + ') — reconnecting SSE');
  if (typeof _reconnectUIActionSSE === 'function') {
    try { _reconnectUIActionSSE(); } catch (_) {}
  }
  _forceSSEReconnect(conversationId, { noReplay: true });
}

// One-shot session-expiry handler. A confirmed 401/403 on the events stream
// is terminal for this page load: stop the reconnect machinery, tell the user,
// and bounce through the server's HTML login redirect (/auth/login →
// /auth/callback) to mint a fresh session. Guarded so racing probes can't
// trigger it (or the redirect) more than once.
var _sseSessionExpired = false;
function _handleSessionExpired() {
  if (_sseSessionExpired) return;
  _sseSessionExpired = true;
  if (sseReconnectTimer) { clearTimeout(sseReconnectTimer); sseReconnectTimer = null; }
  if (eventSource) { try { eventSource.close(); } catch (_) {} eventSource = null; }
  try { document.getElementById('status').textContent = t('sessionExpired'); } catch (_) {}
  try { addMsg('error', t('sessionExpired')); } catch (_) {}
  var _dest = (typeof LOGIN_URL !== 'undefined' && LOGIN_URL) ? LOGIN_URL : '/auth/login';
  setTimeout(function () { try { window.location.href = _dest; } catch (_) {} }, 1200);
}

// Classify an opaque EventSource failure by re-hitting the same endpoint with
// fetch (which DOES expose the status). Aborts the body immediately so a
// healthy 200 stream never lingers as a second subscriber. Only a definitive
// 401/403 triggers re-auth; network errors and 200s fall through to the normal
// backoff reconnect already scheduled by the caller.
var _sseAuthProbeInFlight = false;
function _probeSSEAuth(cid) {
  if (_sseSessionExpired || _sseAuthProbeInFlight || !cid) return;
  if (typeof fetch !== 'function') return;
  _sseAuthProbeInFlight = true;
  var token = (typeof getToken === 'function') ? getToken() : null;
  var url = SSE_URL + '?probe=1&conversation_id=' + encodeURIComponent(cid)
    + (token ? '&token=' + encodeURIComponent(token) : '');
  var ctrl = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  var settled = false;
  var done = function () {
    if (settled) return;
    settled = true;
    _sseAuthProbeInFlight = false;
    if (ctrl) { try { ctrl.abort(); } catch (_) {} }
  };
  var headers = (typeof getAuthHeaders === 'function') ? getAuthHeaders() : {};
  fetch(url, {
    method: 'GET',
    credentials: 'same-origin',
    headers: headers,
    signal: ctrl ? ctrl.signal : undefined,
  }).then(function (resp) {
    var expired = resp.status === 401 || resp.status === 403
      || resp.type === 'opaqueredirect';
    done();
    if (expired) _handleSessionExpired();
  }).catch(function () {
    // Network failure / abort — not an auth problem; let backoff retry.
    done();
  });
}

function _scheduleSSEReconnect(cid) {
  if (_sseSessionExpired) return;  // re-auth in progress — stop retrying
  if (sseReconnectTimer) clearTimeout(sseReconnectTimer);
  // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s, 60s
  const delay = Math.min(1000 * Math.pow(2, sseRetryCount), 60000);
  sseRetryCount++;
  pawflowDebugLog('[SSE] reconnecting in', delay, 'ms (attempt', sseRetryCount, ')');
  sseReconnectTimer = setTimeout(() => {
    sseReconnectTimer = null;
    if (_sseSessionExpired) return;  // re-auth in progress — stop retrying
    if (!cid || cid !== conversationId) return;  // conversation changed, skip
    connectSSE(cid);
  }, delay);
}

// ── Idle-safe SSE reconnection ─────────────────────────────────────
function _sseIsHealthy() {
  if (!eventSource || eventSource.readyState !== EventSource.OPEN) return false;
  if (!lastSSEActivity) return false;
  return Date.now() - lastSSEActivity <= 45000;
}

function startSSEHealthTimer() {
  stopSSEHealthTimer();
  sseHealthTimer = setInterval(() => {
    if (!conversationId) return;
    if (typeof document !== 'undefined' && document.hidden && _sseIsHealthy()) return;
    if (_sseIsHealthy()) return;
    _forceSSEReconnect(conversationId, { noReplay: true });
  }, 15000);
  // Resource refresh is a fallback hydration path, not a hot idle loop.
  if (!resourcesTimer) {
    resourcesTimer = setInterval(() => {
      if (!conversationId) return;
      if (typeof document !== 'undefined' && document.hidden) return;
      loadResources();
    }, 120000);
  }
}
function stopSSEHealthTimer() {
  if (sseHealthTimer) { clearInterval(sseHealthTimer); sseHealthTimer = null; }
  if (resourcesTimer) { clearInterval(resourcesTimer); resourcesTimer = null; }
}

async function showPrompts() {
  try {
    const data = await rxjs.firstValueFrom(action$('list_skills'));
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', t('noSkillsAvailableCreateHint')); return; }
    let overlay = document.getElementById('promptOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'promptOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
    let html = '<div style="background:#1a1a2e;border:1px solid #0f3460;border-radius:12px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;padding:20px">';
    html += '<h3 style="margin:0 0 12px;color:#e94560">' + escapeHtml(t('skills')) + '</h3>';
    for (const s of skills) {
      html += '<div class="prompt-item" data-name="' + escapeHtml(s.name) + '" style="padding:10px;margin:4px 0;background:#16213e;border-radius:8px;cursor:pointer;border:1px solid transparent" onmouseenter="this.style.borderColor=\'#e94560\'" onmouseleave="this.style.borderColor=\'transparent\'">';
      html += '<div style="font-weight:600;color:#fff">' + escapeHtml(s.name) + '</div>';
      if (s.description) html += '<span style="font-size:11px;color:#aaa">' + escapeHtml(s.description) + '</span>';
      if (s.preview) html += '<div style="font-size:11px;color:#666;margin-top:4px">' + escapeHtml(s.preview) + '...</div>';
      html += '</div>';
    }
    html += '<button onclick="document.getElementById(\'promptOverlay\').remove()" style="margin-top:12px;padding:6px 16px;background:#0f3460;color:#fff;border:none;border-radius:6px;cursor:pointer">' + escapeHtml(t('close')) + '</button>';
    html += '</div>';
    overlay.innerHTML = html;
    overlay.querySelectorAll('.prompt-item').forEach(item => {
      item.addEventListener('click', async () => {
        const name = item.dataset.name;
        try {
          const d2 = await rxjs.firstValueFrom(action$('get_skill', { name: name }));
          if (d2.prompt) {
            document.getElementById('input').value = d2.prompt;
            document.getElementById('input').focus();
          }
        } catch(e) { addMsg('error', t('skillLoadFailed', { error: e.message })); }
        overlay.remove();
      });
    });
    document.body.appendChild(overlay);
  } catch (e) { addMsg('error', t('promptsListFailed', { error: e.message })); }
}