// Scoped temporary-file UI for the selected conversation agent.
let _sdState = { agent: '', status: null };

function closeScratchDirOverlay() {
  const overlay = document.getElementById('scratchdirOverlay');
  if (overlay) overlay.remove();
}

function cmdShowScratchDir() {
  showScratchDirOverlay();
}

function showScratchDirOverlay() {
  if (!conversationId) {
    addMsg('error', t('noConv'));
    return;
  }
  closeScratchDirOverlay();
  const overlay = document.createElement('div');
  overlay.id = 'scratchdirOverlay';
  overlay.className = 'overlay';
  overlay.onclick = function(event) {
    if (event.target === overlay) closeScratchDirOverlay();
  };
  overlay.innerHTML = '<div class="cog-dialog" style="width:min(900px,94vw);height:min(720px,90vh)">'
    + '<div class="cog-head"><h2 id="sdTitle" style="margin:0;color:#eee;font-size:17px">ScratchDir</h2>'
    + '<select id="sdAgent" onchange="sdAgentChanged()" style="margin-left:auto;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:5px"></select>'
    + '<button type="button" class="btn" onclick="sdEnsure()">Ensure</button>'
    + '<button type="button" class="btn" onclick="sdRenew()">Renew</button>'
    + '<button type="button" class="btn" onclick="sdClear()">Clear</button>'
    + '<button type="button" class="btn" onclick="closeScratchDirOverlay()">Close</button>'
    + '<button type="button" class="cog-close" onclick="closeScratchDirOverlay()" aria-label="Close">&times;</button></div>'
    + '<div id="sdBody" style="overflow:auto;min-height:0;flex:1;padding:14px;color:#d0d0dc"></div></div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') closeScratchDirOverlay();
  });
  overlay.tabIndex = -1;
  overlay.focus();
  _cognitiveLoadResources(function(data) {
    const agents = _cognitiveAgentNames(data);
    const select = document.getElementById('sdAgent');
    if (!agents.length) {
      select.innerHTML = '<option value="">' + escapeHtml(t('noAgents')) + '</option>';
      sdError(t('noAgents'));
      return;
    }
    _sdState.agent = agents.indexOf(selectedAgent) >= 0 ? selectedAgent : agents[0];
    select.innerHTML = agents.map(function(agent) {
      return '<option value="' + escapeHtml(agent) + '"'
        + (agent === _sdState.agent ? ' selected' : '') + '>'
        + escapeHtml(agent) + '</option>';
    }).join('');
    sdLoad();
  }, sdError);
}

function sdAgentChanged() {
  _sdState.agent = document.getElementById('sdAgent').value;
  sdLoad();
}

function sdPayload(extra) {
  return Object.assign({ agent_name: _sdState.agent }, extra || {});
}

function sdDate(seconds) {
  return Number(seconds || 0) > 0 ? new Date(Number(seconds) * 1000).toLocaleString() : '—';
}

function sdBytes(value) {
  let size = Number(value || 0);
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return (unit ? size.toFixed(1) : Math.round(size)) + ' ' + units[unit];
}

function sdError(error) {
  const body = document.getElementById('sdBody');
  if (body) body.innerHTML = '<div style="color:#e74c3c">'
    + escapeHtml((error && error.message) || String(error || 'Unknown error')) + '</div>';
}

function sdLoad() {
  const body = document.getElementById('sdBody');
  if (body) body.innerHTML = '<div style="color:#777">Loading...</div>';
  action$('scratchdir_tree', sdPayload({ max_entries: 200 })).subscribe({
    next: function(data) {
      if (!data || data.error) { sdError((data && data.error) || 'Cannot load ScratchDir'); return; }
      _sdState.status = data;
      sdRender(data);
    },
    error: sdError,
  });
}

function sdRender(data) {
  const body = document.getElementById('sdBody');
  if (!body) return;
  const status = escapeHtml(data.status || 'absent');
  const usage = sdBytes(data.observed_bytes) + ' / ' + sdBytes(data.quota_bytes);
  const files = Number(data.observed_files || 0) + ' / ' + Number(data.quota_files || 0);
  let html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:14px">'
    + sdMetric('Status', status) + sdMetric('Relay', escapeHtml(data.relay_id || '—'))
    + sdMetric('Usage', escapeHtml(usage)) + sdMetric('Files', escapeHtml(files))
    + sdMetric('Expires', escapeHtml(sdDate(data.expires_at))) + '</div>';
  const entries = Array.isArray(data.entries) ? data.entries : [];
  if (!entries.length) {
    html += '<div style="color:#777;text-align:center;padding:28px">No temporary files.</div>';
  } else {
    html += '<div style="border:1px solid #292944;border-radius:7px;overflow:hidden">';
    entries.forEach(function(entry) {
      const path = String(entry.path || '');
      html += '<div style="display:flex;align-items:center;gap:9px;padding:8px;border-bottom:1px solid #292944">'
        + '<span style="color:#999">' + (entry.kind === 'directory' ? 'DIR' : 'FILE') + '</span>'
        + '<code style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(path) + '</code>'
        + '<span style="margin-left:auto;color:#777">' + (entry.kind === 'file' ? escapeHtml(sdBytes(entry.size)) : '') + '</span>'
        + (entry.kind === 'file' ? '<button type="button" class="btn" onclick="sdPromote(decodeURIComponent(\''
          + encodeURIComponent(path) + '\'))">Copy to FileStore</button>' : '') + '</div>';
    });
    html += '</div>';
    if (data.truncated) html += '<div style="color:#f0ad4e;margin-top:8px">Tree limited to 200 entries.</div>';
  }
  body.innerHTML = html;
}

function sdMetric(label, value) {
  return '<div style="border:1px solid #292944;border-radius:7px;padding:9px;background:#111124">'
    + '<div style="font-size:10px;color:#777;text-transform:uppercase">' + label + '</div>'
    + '<div style="margin-top:4px">' + value + '</div></div>';
}

function sdEnsure() {
  sdMutate('scratchdir_ensure', { ttl_hours: 168 });
}

function sdRenew() {
  const raw = prompt('Lifetime in hours (1-720)', '168');
  if (raw === null) return;
  const ttl = Number(raw);
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > 720) {
    sdError('Lifetime must be an integer between 1 and 720 hours.');
    return;
  }
  sdMutate('scratchdir_renew', { ttl_hours: ttl });
}

function sdClear() {
  if (!confirm('Delete every temporary file for ' + _sdState.agent + '?')) return;
  sdMutate('scratchdir_clear');
}

function sdMutate(action, extra) {
  action$(action, sdPayload(extra)).subscribe({
    next: function(data) {
      if (!data || data.error) { sdError((data && data.error) || 'ScratchDir action failed'); return; }
      sdLoad();
    },
    error: sdError,
  });
}

function sdPromote(path) {
  action$('scratchdir_promote', sdPayload({ path: path })).subscribe({
    next: function(data) {
      if (!data || data.error) { sdError((data && data.error) || 'File promotion failed'); return; }
      addMsg('system', 'Copied ' + data.filename + ' to FileStore: ' + data.file_id);
    },
    error: sdError,
  });
}
