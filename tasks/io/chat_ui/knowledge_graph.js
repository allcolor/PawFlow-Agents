// ── ' + t('knowledgeGraph') + ' Panel ────────────────────────────────────────
let _kgCache = [];
let _kgFilter = '';

function cmdShowKg() {
  action$('kg_list', {}).subscribe({
    next: (data) => {
      _kgCache = data.triples || [];
      showKgOverlay(_kgCache);
    },
    error: (e) => addMsg('error', t('failedLoadKg', { error: e.message })),
  });
}

function showKgOverlay(triples) {
  let overlay = document.getElementById('kgOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'kgOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';

  // Stats
  const current = triples.filter(t => !t.ended);
  const entities = new Set();
  triples.forEach(t => { entities.add(t.subject); entities.add(t.object); });
  const statsHtml = '<span style="color:#6c6c8a;font-size:12px">'
    + t('kgStats', { entities: entities.size, triples: triples.length, current: current.length })
    + '</span>';

  // Filter input
  const filterHtml = '<input id="kgFilterInput" type="text" placeholder="' + t('filterTriples') + '" value="'
    + escapeHtml(_kgFilter) + '" oninput="kgFilterChanged(this.value)"'
    + ' style="background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:3px 8px;font-size:12px;width:160px">';

  // Build triple rows
  const filtered = _kgFilterTriples(triples, _kgFilter);
  const rowsHtml = _kgBuildRows(filtered);

  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:750px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">' + t('knowledgeGraph') + '</h3>'
    + statsHtml
    + filterHtml
    + '<button onclick="kgAddNew()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600;margin-left:auto">+ ' + escapeHtml(t('add')) + '</button>'
    + '<button onclick="document.getElementById(\'kgOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px">&times;</button>'
    + '</div>'
    + '<div id="kg-list" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117">' + rowsHtml + '</div>'
    + '</div>';
  document.body.appendChild(overlay);

}

function _kgFilterTriples(triples, query) {
  if (!query) return triples;
  const q = query.toLowerCase();
  return triples.filter(t =>
    (t.subject || '').toLowerCase().includes(q)
    || (t.predicate || '').toLowerCase().includes(q)
    || (t.object || '').toLowerCase().includes(q)
  );
}

function _kgBuildRows(triples) {
  if (triples.length === 0) {
    return '<div style="color:#6c6c8a;text-align:center;padding:20px">' + t('noTriplesFound') + '</div>';
  }
  let html = '';
  triples.forEach((t, i) => {
    const ended = !!t.ended;
    // Confidence badge
    const conf = (t.confidence || 'EXTRACTED').toUpperCase();
    let confColor, confBg;
    if (conf === 'INFERRED') { confBg = '#1e3a5f'; confColor = '#4fc3f7'; }
    else if (conf === 'AMBIGUOUS') { confBg = '#5a3a1a'; confColor = '#ffb347'; }
    else { confBg = '#1b4332'; confColor = '#52b788'; }  // EXTRACTED
    const confBadge = '<span style="background:' + confBg + ';color:' + confColor
      + ';padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">' + conf + '</span>';

    // Status badge
    const statusBadge = ended
      ? '<span style="color:#e74c3c;font-size:11px" title="' + t('endedTitle') + '">\u2717 ' + t('ended') + '</span>'
      : '<span style="color:#52b788;font-size:11px" title="' + t('currentTitle') + '">\u2713 ' + t('current') + '</span>';

    // Invalidate button (only for current triples)
    const invalidateBtn = ended ? '' : '<button onclick="event.stopPropagation();kgInvalidate(\'' + (t.id || '').replace(/'/g, "\\'") + '\')"'
      + ' style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:11px;padding:0 3px" title="' + t('invalidate') + '">\u2717</button>';

    // Age
    const age = t.valid_from ? _kgFormatAge(t.valid_from) : '';

    // Triple display
    const subj = escapeHtml(t.subject || '');
    const pred = escapeHtml(t.predicate || '');
    const obj = escapeHtml(t.object || '');
    const opacity = ended ? 'opacity:0.5;' : '';

    html += '<div style="padding:6px 8px;border-bottom:1px solid #222;' + opacity + '">'
      + '<div style="display:flex;align-items:center;gap:6px">'
      + '<span style="color:#e0e0e0;font-size:12px;font-weight:600">' + subj + '</span>'
      + '<span style="color:#6c6c8a;font-size:11px">\u2192</span>'
      + '<span style="color:#a0a0c0;font-size:12px">' + pred + '</span>'
      + '<span style="color:#6c6c8a;font-size:11px">\u2192</span>'
      + '<span style="color:#e0e0e0;font-size:12px;font-weight:600">' + obj + '</span>'
      + '<span style="margin-left:auto;display:flex;align-items:center;gap:6px">'
      + confBadge + statusBadge
      + (age ? '<span style="color:#6c6c8a;font-size:10px">' + age + '</span>' : '')
      + invalidateBtn
      + '</span>'
      + '</div>'
      + '</div>';
  });
  return html;
}

function _kgFormatAge(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return t('justNow');
  if (s < 3600) return t('minutesAgo', { n: Math.floor(s / 60) });
  if (s < 86400) return t('hoursAgo', { n: Math.floor(s / 3600) });
  return t('daysAgo', { n: Math.floor(s / 86400) });
}

function kgFilterChanged(val) {
  _kgFilter = val;
  const list = document.getElementById('kg-list');
  if (!list) return;
  const filtered = _kgFilterTriples(_kgCache, _kgFilter);
  list.innerHTML = _kgBuildRows(filtered);
}

function kgInvalidate(tripleId) {
  if (!confirm(t('invalidateTripleConfirm'))) return;
  action$('kg_invalidate', { triple_id: tripleId }).subscribe({
    next: () => cmdShowKg(),
    error: (e) => addMsg('error', t('failedInvalidate', { error: e.message })),
  });
}

function kgAddNew() {
  const list = document.getElementById('kg-list');
  if (!list) return;
  const form = document.createElement('div');
  form.style.cssText = 'padding:8px;border-bottom:1px solid #444;background:#1a1a2e';
  form.innerHTML = '<div style="display:flex;gap:6px;margin-bottom:4px">'
    + '<input id="kg-new-subject" placeholder="' + t('subject') + '" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px 6px;font-size:12px">'
    + '<input id="kg-new-predicate" placeholder="' + t('predicate') + '" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px 6px;font-size:12px">'
    + '<input id="kg-new-object" placeholder="' + t('object') + '" style="flex:1;background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:4px 6px;font-size:12px">'
    + '</div>'
    + '<div style="display:flex;gap:6px;align-items:center">'
    + '<label style="color:#6c6c8a;font-size:11px">' + t('confidence') + '</label>'
    + '<select id="kg-new-confidence" style="background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<option value="EXTRACTED">EXTRACTED</option>'
    + '<option value="INFERRED">INFERRED</option>'
    + '<option value="AMBIGUOUS">AMBIGUOUS</option>'
    + '</select>'
    + '<label style="color:#6c6c8a;font-size:11px">' + t('validFrom') + '</label>'
    + '<input id="kg-new-valid-from" type="date" style="background:#0d1117;color:#c0c0d0;border:1px solid #444;border-radius:4px;padding:2px 6px;font-size:11px">'
    + '<button onclick="kgSaveNew()" style="background:#1b4332;color:#52b788;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px;margin-left:auto">Add</button>'
    + '<button onclick="cmdShowKg()" style="background:#333;color:#aaa;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">Cancel</button>'
    + '</div>';
  list.insertBefore(form, list.firstChild);
  document.getElementById('kg-new-subject').focus();
}

function kgSaveNew() {
  const subject = document.getElementById('kg-new-subject').value.trim();
  const predicate = document.getElementById('kg-new-predicate').value.trim();
  const object = document.getElementById('kg-new-object').value.trim();
  const confidence = document.getElementById('kg-new-confidence').value;
  const validFromStr = document.getElementById('kg-new-valid-from').value;
  if (!subject || !predicate || !object) {
    addMsg('error', t('kgRequired'));
    return;
  }
  const params = { subject, predicate, object, confidence };
  if (validFromStr) {
    params.valid_from = Math.floor(new Date(validFromStr).getTime() / 1000);
  }
  action$('kg_add', params).subscribe({
    next: () => cmdShowKg(),
    error: (e) => addMsg('error', t('failedAddTriple', { error: e.message })),
  });
}

function kgQuickAdd(subject, predicate, object) {
  action$('kg_add', { subject, predicate, object, confidence: 'EXTRACTED' }).subscribe({
    next: (data) => addMsg('system', t('kgTripleAdded', { subject: subject, predicate: predicate, object: object })),
    error: (e) => addMsg('error', t('failedAddTriple', { error: e.message })),
  });
}

function kgShowStats() {
  action$('kg_stats', {}).subscribe({
    next: (data) => {
      const lines = [
        '**' + t('knowledgeGraph') + ' Stats**',
        '  ' + t('entities') + ': ' + (data.entity_count || 0),
        '  ' + t('triples') + ': ' + (data.triple_count || 0),
        '  ' + t('currentTitle') + ': ' + (data.current_count || 0),
        '  ' + t('endedTitle') + ': ' + (data.ended_count || 0),
      ];
      if (data.by_confidence) {
        for (const [conf, count] of Object.entries(data.by_confidence)) {
          lines.push('  ' + conf + ': ' + count);
        }
      }
      addMsg('system', lines.join('\n'));
    },
    error: (e) => addMsg('error', t('failedLoadKgStats', { error: e.message })),
  });
}
