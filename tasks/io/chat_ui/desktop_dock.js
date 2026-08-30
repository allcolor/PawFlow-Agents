// ── Active Desktops dock (WS7) ──
// Backend-truth inventory of running relay Desktops: a compact dock button
// with a count badge, a popover listing one row per BACKEND session, and an
// explicit per-session stop confirmation carrying the exact session ID.
//
// Invariants (docs/MULTI_WORKSPACE_RELAY_DESKTOP_IMPLEMENTATION_PLAN.md §13):
// - the list mirrors the server inventory, never open browser tabs;
// - Open/Reattach never starts a Desktop (desktop_attach refuses cold);
// - Stop sends the OBSERVED desktop_session_id; a conflict re-syncs instead
//   of stopping a newer session; rows disappear only after backend ack;
// - there is no bulk stop.

var _desktopDockItems = [];   // last known inventory rows (server truth)
var _desktopDockLoaded = false;

function _desktopDockBadge() { return document.getElementById('desktopDockBadge'); }
function _desktopDockPop() { return document.getElementById('desktopDockPop'); }

function _desktopDockSetItems(rows) {
  _desktopDockItems = Array.isArray(rows) ? rows : [];
  _desktopDockLoaded = true;
  const badge = _desktopDockBadge();
  if (badge) {
    // unknown counts too: it is an unconfirmed live session, not a
    // stopped one — a zero badge would misreport it as gone.
    const count = _desktopDockItems.filter(
      r => r.state === 'running' || r.state === 'stopping'
        || r.state === 'unknown').length;
    badge.textContent = String(count);
    badge.hidden = count === 0;
  }
  const pop = _desktopDockPop();
  if (pop && !pop.hidden) _desktopDockRender();
}

function _desktopDockRefresh(probe) {
  action$('desktop_list_active', probe ? { probe: true } : {}).subscribe({
    next: (data) => _desktopDockSetItems(data && data.desktops),
    error: (e) => console.warn('[desktop-dock] refresh failed', e),
  });
}

function _desktopDockWireSSE() {
  if (!eventSource) return;
  eventSource.addEventListener('desktop_inventory_changed', (e) => {
    try {
      const data = JSON.parse(e.data);
      _desktopDockSetItems(data.desktops);
    } catch (_) { /* malformed event: keep last known state */ }
  });
}

function _desktopDockStateLabel(state) {
  const keys = {
    running: 'desktopDockStateRunning',
    stopping: 'desktopDockStateStopping',
    unknown: 'desktopDockStateUnknown',
  };
  return t(keys[state] || 'desktopDockStateUnknown');
}

function _desktopDockRowHtml(row, index) {
  const isolated = row.workspace_isolated;
  const badgeKey = isolated ? 'desktopDockIsolated' : 'desktopDockSharedHost';
  const started = row.started_at
    ? new Date(row.started_at * 1000).toLocaleTimeString() : '';
  const unknown = row.state === 'unknown';
  return '<div class="desktop-dock-row" data-index="' + index + '">'
    + '<div class="desktop-dock-row-main">'
    + '<span class="desktop-dock-relay">' + escapeHtml(row.relay_id) + '</span>'
    + '<span class="desktop-dock-badge ' + (isolated ? 'isolated' : 'shared')
    + '">' + escapeHtml(t(badgeKey)) + '</span>'
    + '<span class="desktop-dock-state state-' + escapeHtml(row.state) + '">'
    + escapeHtml(_desktopDockStateLabel(row.state))
    + (started ? ' · ' + escapeHtml(started) : '') + '</span>'
    + '</div>'
    + '<div class="desktop-dock-row-actions">'
    + (unknown ? '' : '<button type="button" onclick="desktopDockAttach('
      + index + ')">' + escapeHtml(t('desktopDockOpen')) + '</button>')
    + (row.can_stop ? '<button type="button" class="desktop-dock-stop"'
      + ' onclick="desktopDockRequestStop(' + index + ')">'
      + escapeHtml(t('desktopDockStop')) + '</button>' : '')
    + '</div></div>';
}

function _desktopDockRender() {
  const pop = _desktopDockPop();
  if (!pop) return;
  const hasUnknown = _desktopDockItems.some(r => r.state === 'unknown');
  let html = '<div class="desktop-dock-head">'
    + '<span>' + escapeHtml(t('desktopDockTitle')) + '</span>'
    + '<button type="button" class="desktop-dock-refresh"'
    + ' onclick="_desktopDockRefresh(true)">&#x21BB;</button>'
    + '</div>';
  if (!_desktopDockItems.length) {
    html += '<div class="desktop-dock-empty">'
      + escapeHtml(t('desktopDockEmpty')) + '</div>';
  } else {
    html += _desktopDockItems.map(_desktopDockRowHtml).join('');
  }
  if (hasUnknown) {
    html += '<div class="desktop-dock-note">'
      + escapeHtml(t('desktopDockUnknownNote')) + '</div>';
  }
  pop.innerHTML = html;
}

function toggleDesktopDock() {
  let pop = _desktopDockPop();
  if (!pop) {
    pop = document.createElement('div');
    pop.id = 'desktopDockPop';
    pop.className = 'desktop-dock-pop';
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', t('desktopDockTitle'));
    document.body.appendChild(pop);
    document.addEventListener('click', (ev) => {
      const btn = document.getElementById('desktopDockBtn');
      if (!pop.hidden && !pop.contains(ev.target)
          && !(btn && btn.contains(ev.target))) {
        pop.hidden = true;
      }
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && !pop.hidden) {
        pop.hidden = true;
        const btn = document.getElementById('desktopDockBtn');
        if (btn) btn.focus();
      }
    });
  }
  if (pop.hidden === false) { pop.hidden = true; return; }
  const btn = document.getElementById('desktopDockBtn');
  if (btn) {
    const rect = btn.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(
      rect.left, window.innerWidth - 340)) + 'px';
    pop.style.bottom = (window.innerHeight - rect.top + 8) + 'px';
  }
  pop.hidden = false;
  _desktopDockRender();
  const refresh = pop.querySelector('.desktop-dock-refresh');
  if (refresh) refresh.focus();
  _desktopDockRefresh(true);
}

/** Stamp a desktop viewer tab with its backend identity so later
 * lifecycle operations can target the exact relay/mode/session instead
 * of matching display labels. */
function desktopDockStampTab(tabId, relayId, mode, sessionId) {
  const panel = document.getElementById('tabContent_' + tabId);
  if (!panel) return;
  panel.dataset.relayId = relayId;
  panel.dataset.desktopMode = mode || 'docker';
  if (sessionId) panel.dataset.desktopSessionId = sessionId;
}

function desktopDockAttach(index) {
  const row = _desktopDockItems[index];
  if (!row) return;
  action$('desktop_attach', {
    relay_id: row.relay_id, mode: row.mode, source: 'webchat-dock',
  }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        if (resp.code === 'not_running') _desktopDockRefresh(true);
        return;
      }
      if (!resp.url) { addMsg('error', t('desktopNoUrl')); return; }
      const label = row.mode === 'host'
        ? row.relay_id + ' (local)' : row.relay_id;
      const tabId = addDesktopTab(label, resp.url);
      desktopDockStampTab(tabId, row.relay_id, row.mode,
                          row.desktop_session_id);
      if (resp.audio_session && resp.audio_token) {
        audioConnect(resp.audio_session, resp.audio_token);
      }
    },
    error: (e) => addMsg('system', t('failed', { error: e.message })),
  });
}

function desktopDockRequestStop(index) {
  const row = _desktopDockItems[index];
  if (!row) return;
  desktopDockRequestStopRow(row);
}

/** Confirmation dialog for one inventory row (also used by /desktop stop). */
function desktopDockRequestStopRow(row) {
  if (!row || !row.desktop_session_id) return;
  // Confirmation names the exact relay and session and spells out the
  // consequences; the safe alternative (detach) is stated explicitly.
  const overlay = document.createElement('div');
  overlay.className = 'desktop-dock-confirm';
  overlay.setAttribute('role', 'alertdialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute(
    'aria-label', t('desktopDockConfirmTitle', { relay: row.relay_id }));
  overlay.innerHTML = '<div class="desktop-dock-confirm-box">'
    + '<div class="desktop-dock-confirm-title">'
    + escapeHtml(t('desktopDockConfirmTitle', { relay: row.relay_id }))
    + '</div>'
    + '<div class="desktop-dock-confirm-body">'
    + escapeHtml(t('desktopDockConfirmBody'))
    + '<br><code>' + escapeHtml(row.desktop_session_id) + '</code></div>'
    + '<div class="desktop-dock-confirm-actions">'
    + '<button type="button" class="ddc-cancel">'
    + escapeHtml(t('cancel')) + '</button>'
    + '<button type="button" class="ddc-stop">'
    + escapeHtml(t('desktopDockStop')) + '</button>'
    + '</div></div>';
  document.body.appendChild(overlay);
  const previousFocus = document.activeElement;
  const close = () => {
    overlay.remove();
    if (previousFocus && previousFocus.focus) previousFocus.focus();
  };
  overlay.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.stopPropagation(); close(); }
  });
  const cancelBtn = overlay.querySelector('.ddc-cancel');
  cancelBtn.onclick = close;
  cancelBtn.focus();
  overlay.querySelector('.ddc-stop').onclick = () => {
    close();
    _desktopDockConfirmStop(row);
  };
}

function _desktopDockConfirmStop(row) {
  action$('desktop_stop_confirm', {
    relay_id: row.relay_id,
    mode: row.mode,
    desktop_session_id: row.desktop_session_id,
    source: 'webchat-dock',
  }).subscribe({
    next: (resp) => {
      if (resp.code === 'session_conflict') {
        addMsg('system', t('desktopDockConflict', { relay: row.relay_id }));
        _desktopDockRefresh(true);
        return;
      }
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }
      // Backend acknowledged: close local viewer tabs for that session and
      // re-sync the list (the row leaves only through server truth).
      // Match by stamped identity so stopping one mode never detaches the
      // other mode's viewer on the same relay.
      document.querySelectorAll('[id^="tabContent_desktop-"]').forEach(p => {
        const mode = p.dataset.desktopMode || 'docker';
        if (p.dataset.relayId === row.relay_id && mode === row.mode) {
          closeDesktopTab(p.id.replace('tabContent_', ''));
        }
      });
      addMsg('system', t('desktopStopped'));
      _desktopDockRefresh(true);
    },
    error: (e) => addMsg('system', t('failed', { error: e.message })),
  });
}

// Slash-command surface (called from cmdDesktop in terminal_commands.js).
function desktopDockCliList() {
  action$('desktop_list_active', { probe: true }).subscribe({
    next: (data) => {
      _desktopDockSetItems(data && data.desktops);
      if (!_desktopDockItems.length) {
        addMsg('system', t('desktopDockEmpty'));
        return;
      }
      const lines = _desktopDockItems.map(r =>
        '- `' + r.relay_id + '` [' + r.mode + '] ' + r.state
        + ' (session `' + r.desktop_session_id + '`)');
      addMsg('system', t('desktopDockTitle') + ':\n' + lines.join('\n'));
    },
    error: (e) => addMsg('system', t('failed', { error: e.message })),
  });
}

// Keyboard activation for the dock button (it is a div with role=button,
// so Enter/Space must be wired explicitly).
(function _desktopDockInit() {
  const btn = document.getElementById('desktopDockBtn');
  if (!btn) return;
  btn.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
      ev.preventDefault();
      toggleDesktopDock();
    }
  });
})();
