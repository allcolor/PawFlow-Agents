// ── Conversation cost gauge + detail panel ──────────────────────
// Header badge shows the conversation's cumulative cost from the server
// usage ledger (task sub-conversations included). Hydrated on conversation
// open via the `usage_conversation` action, refreshed live by the
// `usage.updated` SSE event published after each turn. Clicking the badge
// opens a breakdown panel (totals, by agent/channel/model, recent turns).

window._usageCost = window._usageCost || {
  totalUsd: 0, virtualUsd: 0, tokensIn: 0, tokensOut: 0, hydratedFor: '',
};

function _usageFmtUsd(v) {
  v = Number(v) || 0;
  if (v === 0) return '$0';
  if (v < 0.01) return '$' + v.toFixed(4);
  if (v < 1) return '$' + v.toFixed(3);
  return '$' + v.toFixed(2);
}

function _usageFmtTok(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1000) return Math.round(n / 1000) + 'k';
  return String(n);
}

function renderUsageCostBadge() {
  const el = document.getElementById('usageCostBadge');
  if (!el) return;
  const u = window._usageCost;
  const hasData = u.totalUsd > 0 || u.virtualUsd > 0
    || u.tokensIn > 0 || u.tokensOut > 0;
  if (!conversationId || u.hydratedFor !== conversationId || !hasData) {
    el.style.display = 'none';
    return;
  }
  // Real spend leads; a subscription-only conversation shows its virtual
  // (API-equivalent) cost with a tilde so it never reads as real money.
  const showVirtual = !(u.totalUsd > 0) && u.virtualUsd > 0;
  el.textContent = showVirtual
    ? '\u007e ' + _usageFmtUsd(u.virtualUsd)
    : '\u2248 ' + _usageFmtUsd(u.totalUsd);
  let title = t('usageCostBadgeTitle', {
    usd: _usageFmtUsd(u.totalUsd),
    tin: _usageFmtTok(u.tokensIn),
    tout: _usageFmtTok(u.tokensOut),
  });
  if (u.virtualUsd > 0) {
    title += ' ' + t('usageCostVirtualTitle',
                     { usd: _usageFmtUsd(u.virtualUsd) });
  }
  el.title = title;
  el.style.display = '';
}

function hydrateUsageCost() {
  if (!conversationId || typeof action$ !== 'function') return;
  const cid = conversationId;
  action$('usage_conversation', { conversation_id: cid }).subscribe(data => {
    // Stale guard: a late response from a prior switch must not paint
    // another conversation's totals onto the current one.
    if (cid !== conversationId || !data || data.error) return;
    const tot = data.totals || {};
    window._usageCost = {
      totalUsd: tot.cost_usd || 0,
      virtualUsd: tot.virtual_cost_usd || 0,
      tokensIn: tot.tokens_in || 0,
      tokensOut: tot.tokens_out || 0,
      hydratedFor: cid,
    };
    renderUsageCostBadge();
  }, () => {});
}

// Called by sse.js after each (re)connect — same pattern as _lkWireSSE.
function _usageWireSSE() {
  if (typeof eventSource === 'undefined' || !eventSource) return;
  eventSource.addEventListener('usage.updated', function (e) {
    let d = {};
    try { d = JSON.parse(e.data || '{}'); } catch (_err) { return; }
    if (!conversationId || d.conversation_id !== conversationId) return;
    window._usageCost = {
      totalUsd: d.total_usd || 0,
      virtualUsd: d.total_virtual_usd || 0,
      tokensIn: d.total_tokens_in || 0,
      tokensOut: d.total_tokens_out || 0,
      hydratedFor: conversationId,
    };
    renderUsageCostBadge();
    // Live-refresh the panel if the user has it open.
    if (document.getElementById('usageCostPanelBody')) {
      _usageLoadPanelContent();
    }
  });
  // A spend budget crossed a 50/80/100% threshold — refresh the budgets
  // section of the dashboard if it's open (usage_dashboard.js).
  eventSource.addEventListener('budget.updated', function () {
    if (typeof _usageDashLoadBudgets === 'function'
        && document.getElementById('usageDashBudgetsWrap')) {
      _usageDashLoadBudgets();
    }
  });
}

// ── Breakdown panel ─────────────────────────────────────────────

function showUsageCostPanel() {
  if (!conversationId) return;
  if (document.getElementById('usageCostPanelOverlay')) return;
  const overlay = document.createElement('div');
  overlay.id = 'usageCostPanelOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);'
    + 'display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeUsageCostPanel();
  });
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel, #1a1a2e);color:var(--pf-text, #e0e0e0);'
    + 'border:1px solid var(--pf-accent-2, #444);border-radius:10px;'
    + 'width:min(680px, 94vw);max-height:84vh;overflow-y:auto;'
    + 'padding:16px 20px;box-shadow:0 8px 30px rgba(0,0,0,0.5);font-size:13px;';
  panel.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">'
    + '<h3 style="margin:0;font-size:15px;">' + escapeHtml(t('usageCostPanelTitle')) + '</h3>'
    + '<button onclick="closeUsageCostPanel()" style="background:none;border:none;'
    + 'color:inherit;font-size:18px;cursor:pointer;" title="' + escapeAttr(t('close')) + '">\u2715</button>'
    + '</div>'
    + '<div id="usageCostPanelBody">\u2026</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  _usageLoadPanelContent();
}

function closeUsageCostPanel() {
  const overlay = document.getElementById('usageCostPanelOverlay');
  if (overlay) overlay.remove();
}

function _usageLoadPanelContent() {
  const cid = conversationId;
  action$('usage_conversation', { conversation_id: cid }).subscribe(data => {
    const body = document.getElementById('usageCostPanelBody');
    if (!body || cid !== conversationId) return;
    if (!data || data.error) {
      body.textContent = (data && data.error) || 'error';
      return;
    }
    body.innerHTML = _usageRenderBreakdown(data);
  }, () => {});
}

function _usageRenderBreakdown(data) {
  const tot = data.totals || {};
  const anyCost = (tot.cost_usd || 0) > 0;
  if (!tot.calls) {
    return '<div style="opacity:0.7;">' + escapeHtml(t('usageCostNoData')) + '</div>';
  }
  let html =
    '<div style="margin-bottom:12px;">'
    + '<span style="font-size:20px;font-weight:600;">' + _usageFmtUsd(tot.cost_usd) + '</span>'
    + '<span style="margin-left:10px;opacity:0.75;">' + escapeHtml(t('usageCostTotalsLine', {
        calls: tot.calls || 0,
        tin: _usageFmtTok(tot.tokens_in), tout: _usageFmtTok(tot.tokens_out),
        cr: _usageFmtTok(tot.cache_read), cw: _usageFmtTok(tot.cache_write),
      })) + '</span>'
    + '</div>';
  if ((tot.virtual_cost_usd || 0) > 0) {
    html += '<div style="margin:-6px 0 12px;opacity:0.8;color:#a8dadc;">'
      + '\u007e ' + _usageFmtUsd(tot.virtual_cost_usd) + ' '
      + escapeHtml(t('usageCostVirtualLine')) + '</div>';
  }
  html += _usageDimTable(t('usageCostByAgent'), data.by_agent, anyCost);
  html += _usageDimTable(t('usageCostByChannel'), data.by_channel, anyCost);
  html += _usageDimTable(t('usageCostByModel'), data.by_model, anyCost);
  html += _usageRecentTable(data.recent, anyCost);
  return html;
}

// One dimension table (by agent/channel/model): value, bar, tokens, cost.
// Bars scale on cost when the conversation has any priced usage, else on
// total tokens (subscription services record tokens at $0).
function _usageDimTable(title, rows, anyCost) {
  rows = (rows || []).filter(r => r.value);
  if (!rows.length) return '';
  const anyVirtual = rows.some(r => (r.virtual_cost_usd || 0) > 0);
  const metric = r => anyCost ? (r.cost_usd || 0)
    : (anyVirtual ? (r.virtual_cost_usd || 0)
                  : ((r.tokens_in || 0) + (r.tokens_out || 0)));
  const max = Math.max.apply(null, rows.map(metric).concat([1e-12]));
  let html = '<div style="margin:10px 0 4px;font-weight:600;opacity:0.85;">'
    + escapeHtml(title) + '</div>'
    + '<table style="width:100%;border-collapse:collapse;">';
  rows.forEach(r => {
    const pct = Math.max(2, Math.round(metric(r) / max * 100));
    html += '<tr>'
      + '<td style="padding:2px 6px 2px 0;max-width:180px;overflow:hidden;'
      + 'text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(r.value) + '</td>'
      + '<td style="width:40%;"><span style="display:inline-block;height:7px;'
      + 'border-radius:3px;background:#4ecdc4;width:' + pct + '%;"></span></td>'
      + '<td style="padding:2px 6px;text-align:right;opacity:0.75;white-space:nowrap;">'
      + _usageFmtTok(r.tokens_in) + ' / ' + _usageFmtTok(r.tokens_out) + '</td>'
      + '<td style="padding:2px 0;text-align:right;white-space:nowrap;">'
      + ((r.cost_usd || 0) > 0 || !(r.virtual_cost_usd > 0)
          ? _usageFmtUsd(r.cost_usd)
          : '\u007e ' + _usageFmtUsd(r.virtual_cost_usd)) + '</td>'
      + '</tr>';
  });
  return html + '</table>';
}

function _usageRecentTable(rows, anyCost) {
  rows = rows || [];
  if (!rows.length) return '';
  const metric = r => anyCost ? (r.cost_usd || 0)
                              : ((r.tokens_in || 0) + (r.tokens_out || 0));
  const max = Math.max.apply(null, rows.map(metric).concat([1e-12]));
  let html = '<div style="margin:14px 0 4px;font-weight:600;opacity:0.85;">'
    + escapeHtml(t('usageCostRecent')) + '</div>'
    + '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
  rows.forEach(r => {
    const d = new Date((r.ts || 0) * 1000);
    const hh = ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    const label = (r.agent_name || r.channel || '?')
      + (r.channel && r.channel !== 'chat' ? ' \u00b7 ' + r.channel : '');
    const pct = Math.max(2, Math.round(metric(r) / max * 100));
    html += '<tr>'
      + '<td style="padding:1px 6px 1px 0;opacity:0.6;white-space:nowrap;">' + hh + '</td>'
      + '<td style="padding:1px 6px 1px 0;max-width:170px;overflow:hidden;'
      + 'text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(label) + '</td>'
      + '<td style="width:32%;"><span style="display:inline-block;height:5px;'
      + 'border-radius:2px;background:#f0ad4e;width:' + pct + '%;"></span></td>'
      + '<td style="padding:1px 6px;text-align:right;opacity:0.75;white-space:nowrap;">'
      + _usageFmtTok(r.tokens_in) + ' / ' + _usageFmtTok(r.tokens_out) + '</td>'
      + '<td style="padding:1px 0;text-align:right;white-space:nowrap;">'
      + _usageFmtUsd(r.cost_usd) + '</td>'
      + '</tr>';
  });
  return html + '</table>';
}
