// ── Global Usage & Cost dashboard ───────────────────────────────
// Full-panel overlay: KPI cards, a stacked daily cost/token chart (canvas,
// no external charting dependency), and top conversations/agents. Backed
// by the single `usage_dashboard` action (core/usage_ledger.py) plus a
// lightweight `usage_timeseries` re-query when only the stack dimension
// changes. Admins can switch to an all-users view.

window._usageDash = window._usageDash || {
  days: 30, bucket: 'day', groupBy: 'llm_service', allUsers: false,
  data: null,
};

const USAGE_DASH_PALETTE = [
  '#4ecdc4', '#e94560', '#f0ad4e', '#74b9ff', '#a29bfe',
  '#55efc4', '#ffeaa7', '#fd79a8', '#00cec9', '#e17055',
];

function _usageDashColor(i) {
  return USAGE_DASH_PALETTE[i % USAGE_DASH_PALETTE.length];
}

function showUsageDashboard() {
  if (document.getElementById('usageDashOverlay')) {
    _usageDashLoad();
    return;
  }
  const overlay = document.createElement('div');
  overlay.id = 'usageDashOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.75);'
    + 'display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.addEventListener('click', function (e) {
    if (e.target === overlay) closeUsageDashboard();
  });
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel, #16213e);color:var(--pf-text, #e0e0e0);'
    + 'border:1px solid var(--pf-accent-2, #4ecdc4);border-radius:10px;'
    + 'width:min(980px, 96vw);max-height:92vh;overflow-y:auto;'
    + 'padding:16px 22px 22px;box-shadow:0 8px 30px rgba(0,0,0,0.5);font-size:13px;';
  panel.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
    + '<h3 style="margin:0;font-size:16px;">' + escapeHtml(t('usageDashTitle')) + '</h3>'
    + '<button onclick="closeUsageDashboard()" style="background:none;border:none;'
    + 'color:inherit;font-size:18px;cursor:pointer;" title="' + escapeAttr(t('close')) + '">\u2715</button>'
    + '</div>'
    + _usageDashFiltersHtml()
    + '<div id="usageDashBody">\u2026</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  _usageDashLoad();
}

function closeUsageDashboard() {
  const overlay = document.getElementById('usageDashOverlay');
  if (overlay) overlay.remove();
}

function _usageDashFiltersHtml() {
  const s = window._usageDash;
  const selStyle = 'background:var(--pf-user,#0f3460);color:var(--pf-text,#e0e0e0);'
    + 'border:1px solid var(--pf-accent-2,#4ecdc4);border-radius:6px;padding:4px 8px;'
    + 'font-size:12px;cursor:pointer;';
  let html = '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 14px;">';
  html += '<select style="' + selStyle + '" onchange="_usageDashSetDays(this.value)">';
  [[7, '7d'], [30, '30d'], [90, '90d']].forEach(function (o) {
    html += '<option value="' + o[0] + '"' + (s.days === o[0] ? ' selected' : '')
      + '>' + escapeHtml(o[1]) + '</option>';
  });
  html += '</select>';
  html += '<select style="' + selStyle + '" onchange="_usageDashSetGroupBy(this.value)">';
  [['llm_service', t('usageCostByServiceOpt')], ['agent_name', t('usageCostByAgent')],
   ['model', t('usageCostByModel')], ['channel', t('usageCostByChannel')]].forEach(function (o) {
    html += '<option value="' + o[0] + '"' + (s.groupBy === o[0] ? ' selected' : '')
      + '>' + escapeHtml(o[1]) + '</option>';
  });
  html += '</select>';
  if (typeof _isAdmin === 'function' && _isAdmin()) {
    html += '<label style="display:inline-flex;align-items:center;gap:4px;opacity:0.85;cursor:pointer;">'
      + '<input type="checkbox" ' + (s.allUsers ? 'checked' : '') + ' onchange="_usageDashSetAllUsers(this.checked)">'
      + escapeHtml(t('usageDashAllUsers')) + '</label>';
  }
  html += '</div>';
  return html;
}

function _usageDashSetDays(v) {
  window._usageDash.days = Number(v) || 30;
  _usageDashLoad();
}

function _usageDashSetGroupBy(v) {
  window._usageDash.groupBy = v;
  _usageDashLoad();
}

function _usageDashSetAllUsers(checked) {
  window._usageDash.allUsers = !!checked;
  _usageDashLoad();
}

function _usageDashLoad() {
  const s = window._usageDash;
  const body = document.getElementById('usageDashBody');
  if (body) body.style.opacity = '0.5';
  action$('usage_dashboard', {
    days: s.days, bucket: s.bucket, group_by: s.groupBy,
    user: s.allUsers ? 'ALL' : '',
  }).subscribe(data => {
    const b = document.getElementById('usageDashBody');
    if (!b) return;
    b.style.opacity = '1';
    if (!data || data.error) {
      b.textContent = (data && data.error) || 'error';
      return;
    }
    window._usageDash.data = data;
    b.innerHTML = _usageDashKpisHtml(data.kpis)
      + '<div style="margin:16px 0 6px;font-weight:600;opacity:0.85;">'
      + escapeHtml(t('usageDashChartTitle', { days: window._usageDash.days })) + '</div>'
      + '<div id="usageDashChartWrap" style="position:relative;"></div>'
      + '<div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:16px;">'
      + '<div style="flex:1;min-width:280px;">' + _usageDashTopTable(
          t('usageDashTopConversations'), data.top_conversations) + '</div>'
      + '<div style="flex:1;min-width:280px;">' + _usageDashTopTable(
          t('usageDashTopAgents'), data.top_agents) + '</div>'
      + '</div>';
    _usageDashDrawChart(data.timeseries, data.group_by);
  }, () => { if (b) b.style.opacity = '1'; });
}

function _usageDashKpisHtml(kpis) {
  kpis = kpis || {};
  const card = (label, s) => {
    s = s || {};
    const anyCost = (s.cost_usd || 0) > 0;
    const primary = anyCost ? _usageFmtUsd(s.cost_usd)
      : ((s.virtual_cost_usd || 0) > 0 ? '\u007e ' + _usageFmtUsd(s.virtual_cost_usd)
                                       : _usageFmtUsd(0));
    const cacheTotal = (s.tokens_in || 0) + (s.cache_read || 0);
    const hitPct = cacheTotal > 0 ? Math.round((s.cache_read || 0) / cacheTotal * 100) : 0;
    return '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:10px 14px;min-width:130px;flex:1;">'
      + '<div style="font-size:11px;opacity:0.65;text-transform:uppercase;letter-spacing:0.04em;">'
      + escapeHtml(label) + '</div>'
      + '<div style="font-size:19px;font-weight:600;margin-top:2px;">' + primary + '</div>'
      + '<div style="font-size:11px;opacity:0.6;margin-top:2px;">'
      + _usageFmtTok(s.tokens_in) + ' / ' + _usageFmtTok(s.tokens_out) + ' tok'
      + (hitPct ? (' \u00b7 ' + hitPct + '% ' + escapeHtml(t('usageDashCacheHit'))) : '')
      + '</div>'
      + '</div>';
  };
  let html = '<div style="display:flex;gap:10px;flex-wrap:wrap;">';
  html += card(t('usageDashToday'), kpis.today);
  html += card(t('usageDash7d'), kpis.d7);
  html += card(t('usageDash30d'), kpis.d30);
  const d7 = kpis.d7 || {};
  const dailyAvg = (d7.cost_usd || 0) / 7;
  const projected = dailyAvg * 30;
  html += '<div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:10px 14px;min-width:130px;flex:1;">'
    + '<div style="font-size:11px;opacity:0.65;text-transform:uppercase;letter-spacing:0.04em;">'
    + escapeHtml(t('usageDashProjected')) + '</div>'
    + '<div style="font-size:19px;font-weight:600;margin-top:2px;">' + _usageFmtUsd(projected) + '</div>'
    + '<div style="font-size:11px;opacity:0.6;margin-top:2px;">' + escapeHtml(t('usageDashProjectedHint')) + '</div>'
    + '</div>';
  html += '</div>';
  return html;
}

function _usageDashTopTable(title, rows) {
  rows = (rows || []).filter(r => r.value);
  let html = '<div style="font-weight:600;opacity:0.85;margin-bottom:4px;">' + escapeHtml(title) + '</div>';
  if (!rows.length) {
    return html + '<div style="opacity:0.6;font-size:12px;">' + escapeHtml(t('usageCostNoData')) + '</div>';
  }
  const anyCost = rows.some(r => (r.cost_usd || 0) > 0);
  const anyVirtual = rows.some(r => (r.virtual_cost_usd || 0) > 0);
  const metric = r => anyCost ? (r.cost_usd || 0)
    : (anyVirtual ? (r.virtual_cost_usd || 0) : ((r.tokens_in || 0) + (r.tokens_out || 0)));
  const max = Math.max.apply(null, rows.map(metric).concat([1e-12]));
  html += '<table style="width:100%;border-collapse:collapse;font-size:12px;">';
  rows.forEach(r => {
    const pct = Math.max(2, Math.round(metric(r) / max * 100));
    const label = String(r.value);
    const shown = label.length > 26 ? (label.slice(0, 12) + '\u2026' + label.slice(-10)) : label;
    const costCell = (r.cost_usd || 0) > 0 || !(r.virtual_cost_usd > 0)
      ? _usageFmtUsd(r.cost_usd) : '\u007e ' + _usageFmtUsd(r.virtual_cost_usd);
    html += '<tr title="' + escapeAttr(label) + '">'
      + '<td style="padding:2px 6px 2px 0;font-family:monospace;white-space:nowrap;">' + escapeHtml(shown) + '</td>'
      + '<td style="width:34%;"><span style="display:inline-block;height:6px;border-radius:3px;'
      + 'background:#4ecdc4;width:' + pct + '%;"></span></td>'
      + '<td style="padding:2px 0;text-align:right;white-space:nowrap;">' + costCell + '</td>'
      + '</tr>';
  });
  return html + '</table>';
}

// Stacked bar chart on canvas — one bar per bucket, segments per group,
// stacked on cost (falls back to tokens if the window has no priced
// usage). No external charting library.
function _usageDashDrawChart(rows, groupKey) {
  const wrap = document.getElementById('usageDashChartWrap');
  if (!wrap) return;
  rows = rows || [];
  const width = Math.max(320, wrap.clientWidth || wrap.parentElement.clientWidth || 600);
  const height = 200;
  const legendH = 40; // room for up to 2 wrapped legend rows
  const dpr = window.devicePixelRatio || 1;
  const canvas = document.createElement('canvas');
  canvas.width = width * dpr;
  canvas.height = (height + legendH) * dpr;
  canvas.style.width = width + 'px';
  canvas.style.height = (height + legendH) + 'px';
  wrap.innerHTML = '';
  wrap.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  if (!rows.length) {
    ctx.fillStyle = 'rgba(255,255,255,0.5)';
    ctx.font = '12px sans-serif';
    ctx.fillText(t('usageCostNoData'), 8, 20);
    return;
  }

  // Pivot: bucket -> { group -> value }, tracking group order by total.
  const buckets = [];
  const bucketIdx = {};
  const groupTotals = {};
  const anyCost = rows.some(r => (r.cost_usd || 0) > 0);
  const metric = r => anyCost ? (r.cost_usd || 0)
    : ((r.tokens_in || 0) + (r.tokens_out || 0));
  rows.forEach(r => {
    if (!(r.bucket in bucketIdx)) {
      bucketIdx[r.bucket] = buckets.length;
      buckets.push({ label: r.bucket, values: {} });
    }
    const grp = r.grp || t('usageDashUnattributed');
    const b = buckets[bucketIdx[r.bucket]];
    b.values[grp] = (b.values[grp] || 0) + metric(r);
    groupTotals[grp] = (groupTotals[grp] || 0) + metric(r);
  });
  const groups = Object.keys(groupTotals).sort((a, b) => groupTotals[b] - groupTotals[a]);
  const groupColor = {};
  groups.forEach((g, i) => { groupColor[g] = _usageDashColor(i); });

  const padL = 46, padB = 22, padT = 8, padR = 8;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const barGap = Math.max(2, plotW / buckets.length * 0.18);
  const barW = Math.max(1, plotW / buckets.length - barGap);
  const maxTotal = Math.max.apply(null, buckets.map(
    b => Object.values(b.values).reduce((a, v) => a + v, 0)).concat([1e-12]));

  // Y axis gridlines + labels (4 lines).
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '10px sans-serif';
  for (let i = 0; i <= 4; i++) {
    const y = padT + plotH - (plotH * i / 4);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(width - padR, y);
    ctx.stroke();
    const val = maxTotal * i / 4;
    const txt = anyCost ? _usageFmtUsd(val) : _usageFmtTok(val);
    ctx.fillText(txt, 2, y + 3);
  }

  // Bars.
  buckets.forEach((b, bi) => {
    let y = padT + plotH;
    const x = padL + bi * (barW + barGap);
    groups.forEach(g => {
      const v = b.values[g] || 0;
      if (!v) return;
      const h = (v / maxTotal) * plotH;
      ctx.fillStyle = groupColor[g];
      ctx.fillRect(x, y - h, barW, h);
      y -= h;
    });
    // Sparse x labels so they don't overlap on a 90-day window.
    const everyN = Math.ceil(buckets.length / 10);
    if (bi % everyN === 0) {
      ctx.fillStyle = 'rgba(255,255,255,0.55)';
      ctx.font = '9px sans-serif';
      const short = String(b.label).slice(5); // MM-DD from YYYY-MM-DD
      ctx.save();
      ctx.translate(x + barW / 2, height - padB + 12);
      ctx.rotate(-Math.PI / 4);
      ctx.fillText(short, 0, 0);
      ctx.restore();
    }
  });

  // Legend.
  let lx = padL, ly = height + 14;
  ctx.font = '10px sans-serif';
  groups.slice(0, 8).forEach(g => {
    const label = String(g);
    const w = ctx.measureText(label).width + 20;
    if (lx + w > width - padR) { lx = padL; ly += 14; }
    ctx.fillStyle = groupColor[g];
    ctx.fillRect(lx, ly - 8, 8, 8);
    ctx.fillStyle = 'rgba(255,255,255,0.75)';
    ctx.fillText(label, lx + 12, ly);
    lx += w;
  });

  canvas.title = t('usageDashChartHint');
}
