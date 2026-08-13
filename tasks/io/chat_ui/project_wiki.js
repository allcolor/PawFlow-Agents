// Project Wiki browser/editor for the active relay project.
let _pwState = { pages: [], current: null, query: '', status: null, relay: '' };
let _pwSearchTimer = null;

function _pwButton(label, onclick, primary) {
  return '<button type="button" onclick="' + onclick + '" style="background:'
    + (primary ? '#1e3a5f' : '#252542') + ';color:'
    + (primary ? '#4fc3f7' : '#c0c0d0')
    + ';border:1px solid #444;border-radius:6px;padding:4px 9px;cursor:pointer;font-size:11px">'
    + escapeHtml(label) + '</button>';
}

function closeProjectWikiOverlay() {
  const overlay = document.getElementById('projectWikiOverlay');
  if (overlay) overlay.remove();
}

function cmdShowProjectWiki() {
  showProjectWikiOverlay();
}

function showProjectWikiOverlay() {
  closeProjectWikiOverlay();
  const overlay = document.createElement('div');
  overlay.id = 'projectWikiOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:9999';
  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:16px;width:min(1050px,94vw);height:min(760px,88vh);display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:7px;margin-bottom:10px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">Project Wiki</h3>'
    + '<label style="color:#888;font-size:11px">' + escapeHtml(t('relays'))
    + ' <select id="pwRelay" onchange="pwRelayChanged()" style="background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:6px;padding:3px 7px"><option>'
    + escapeHtml(t('loadingRelays')) + '</option></select></label>'
    + '<span id="pwStatus" style="color:#777;font-size:11px"></span>'
    + _pwButton('Graph', "closeProjectWikiOverlay();showProjectGraphOverlay()", false)
    + _pwButton('Scratchpad', "closeProjectWikiOverlay();showScratchpadOverlay()", false)
    + _pwButton('Refresh sources', 'pwRefresh()', false)
    + _pwButton('Lint', 'pwLint()', false)
    + _pwButton('New page', 'pwEditPage()', true)
    + '<button type="button" onclick="closeProjectWikiOverlay()" style="margin-left:auto;background:none;border:none;color:#aaa;cursor:pointer;font-size:20px">&times;</button>'
    + '</div>'
    + '<div style="display:grid;grid-template-columns:minmax(220px,30%) 1fr;gap:10px;min-height:0;flex:1">'
    + '<aside style="display:flex;flex-direction:column;min-height:0;border:1px solid #292944;border-radius:8px;background:#111124">'
    + '<input id="pwSearch" type="search" placeholder="Search wiki pages and content" style="margin:9px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:6px;padding:7px" oninput="pwScheduleSearch(this.value)">'
    + '<div id="pwPages" style="overflow:auto;padding:0 8px 8px;flex:1"></div>'
    + '</aside>'
    + '<main id="pwContent" style="overflow:auto;border:1px solid #292944;border-radius:8px;background:#0d1117;padding:14px;color:#d0d0dc">'
    + '<div style="color:#777;text-align:center;padding:30px">Select a page or create one.</div>'
    + '</main></div></div>';
  document.body.appendChild(overlay);
  const previousRelay = _pwState.relay;
  _pwState = { pages: [], current: null, query: '', status: null, relay: previousRelay };
  _cognitiveLoadResources(function(data) {
    const available = _cognitiveRelays(data, selectedAgent);
    const ids = available.relays.map(function(relay) { return relay.id; });
    if (ids.indexOf(_pwState.relay) < 0) {
      _pwState.relay = ids.indexOf(available.preferred) >= 0 ? available.preferred : (ids[0] || '');
    }
    const select = document.getElementById('pwRelay');
    if (!available.relays.length) {
      select.innerHTML = '<option value="">' + escapeHtml(t('noRelaysLinked')) + '</option>';
      _pwError(t('noRelaysLinked'));
      return;
    }
    select.innerHTML = _cognitiveRelayOptions(available.relays, _pwState.relay);
    pwLoadStatus();
    pwLoadPages('');
  }, _pwError);
}

function _pwArgs(extra) {
  return Object.assign({ relay_id: _pwState.relay }, extra || {});
}

function pwRelayChanged() {
  _pwState = { pages: [], current: null, query: '', status: null,
    relay: document.getElementById('pwRelay').value };
  const content = document.getElementById('pwContent');
  if (content) content.innerHTML = '<div style="color:#777;text-align:center;padding:30px">'
    + escapeHtml(t('loadingRelayData')) + '</div>';
  pwLoadStatus();
  pwLoadPages('');
}

function _pwError(error) {
  const content = document.getElementById('pwContent');
  if (content) content.innerHTML = '<div style="color:#e74c3c">'
    + escapeHtml((error && error.message) || String(error || 'Unknown error')) + '</div>';
}

function pwLoadStatus() {
  action$('project_wiki_status', _pwArgs()).subscribe({
    next: function(data) {
      if (!data || data.error) return;
      _pwState.status = data;
      const label = document.getElementById('pwStatus');
      if (label) label.textContent = (data.pages || 0) + ' pages, '
        + (data.dirty_sources || 0) + ' pending, relay ' + (data.relay_id || '?');
    },
  });
}

function pwScheduleSearch(value) {
  _pwState.query = String(value || '').trim();
  if (_pwSearchTimer) clearTimeout(_pwSearchTimer);
  _pwSearchTimer = setTimeout(function() {
    _pwSearchTimer = null;
    pwLoadPages(_pwState.query);
  }, 250);
}

function pwLoadPages(query) {
  const list = document.getElementById('pwPages');
  if (list) list.innerHTML = '<div style="color:#777;padding:10px">Loading...</div>';
  const action = query ? 'project_wiki_query' : 'project_wiki_pages';
  action$(action, _pwArgs({ query: query || '', limit: 25 })).subscribe({
    next: function(data) {
      if (!data || data.error) { _pwError((data && data.error) || 'Cannot load wiki'); return; }
      _pwState.pages = Array.isArray(data.pages) ? data.pages : [];
      pwRenderPages();
    },
    error: _pwError,
  });
}

function pwRenderPages() {
  const list = document.getElementById('pwPages');
  if (!list) return;
  if (!_pwState.pages.length) {
    list.innerHTML = '<div style="color:#777;padding:10px">No matching pages.</div>';
    return;
  }
  list.innerHTML = _pwState.pages.map(function(page) {
    const slug = encodeURIComponent(page.slug || '');
    const stale = Array.isArray(page.stale) && page.stale.length;
    return '<button type="button" onclick="pwOpenPage(decodeURIComponent(\''
      + slug + '\'))" style="display:block;width:100%;text-align:left;background:'
      + ((_pwState.current && _pwState.current.slug === page.slug) ? '#242447' : 'transparent')
      + ';border:0;border-bottom:1px solid #292944;color:#ddd;padding:9px 6px;cursor:pointer">'
      + '<strong style="font-size:12px">' + escapeHtml(page.title || page.slug || '?') + '</strong>'
      + (stale ? '<span style="color:#ffb347;font-size:10px;margin-left:6px">STALE</span>' : '')
      + '<div style="font-size:10px;color:#888;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
      + escapeHtml(page.summary || page.excerpt || '') + '</div></button>';
  }).join('');
}

function pwOpenPage(slug) {
  const content = document.getElementById('pwContent');
  if (content) content.innerHTML = '<div style="color:#777">Loading page...</div>';
  action$('project_wiki_page', _pwArgs({ slug: slug })).subscribe({
    next: function(data) {
      if (!data || data.error) { _pwError((data && data.error) || 'Cannot load page'); return; }
      _pwState.current = data;
      pwRenderPages();
      pwRenderPage(data);
    },
    error: _pwError,
  });
}

function pwRenderPage(page) {
  const content = document.getElementById('pwContent');
  if (!content) return;
  const sources = Array.isArray(page.sources) ? page.sources : [];
  const stale = Array.isArray(page.stale) ? page.stale : [];
  content.innerHTML = '<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:10px">'
    + '<div><h2 style="margin:0;color:#eee">' + escapeHtml(page.title || page.slug) + '</h2>'
    + '<div style="font-size:11px;color:#888;margin-top:3px">' + escapeHtml(page.summary || '') + '</div></div>'
    + '<div style="margin-left:auto;display:flex;gap:6px">'
    + _pwButton('Edit', 'pwEditPage()', true)
    + _pwButton('Delete', 'pwDeletePage()', false)
    + '</div></div>'
    + (stale.length ? '<div style="background:#4d3218;color:#ffcf80;padding:7px;border-radius:6px;margin-bottom:10px;font-size:11px">Stale: '
      + escapeHtml(stale.join(', ')) + '</div>' : '')
    + '<div class="pw-markdown" style="line-height:1.5">' + renderMarkdown(page.content || '') + '</div>'
    + '<div style="border-top:1px solid #292944;margin-top:16px;padding-top:8px;color:#777;font-size:10px">Sources: '
    + escapeHtml(sources.join(', ') || 'none') + '</div>';
}

function pwEditPage() {
  const page = _pwState.current || { slug: '', title: '', summary: '', content: '', sources: [] };
  const editing = Boolean(page.slug);
  const content = document.getElementById('pwContent');
  if (!content) return;
  content.innerHTML = '<form id="pwEditor" onsubmit="pwSavePage(event)" style="display:flex;flex-direction:column;gap:9px;height:100%">'
    + '<label style="font-size:11px;color:#999">Slug<input id="pwSlug" value="' + escapeHtml(page.slug || '')
    + '" ' + (editing ? 'readonly' : '') + ' style="display:block;width:100%;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999">Title<input id="pwTitle" value="' + escapeHtml(page.title || '')
    + '" required style="display:block;width:100%;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999">Summary<input id="pwSummary" value="' + escapeHtml(page.summary || '')
    + '" style="display:block;width:100%;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999">Source paths (comma-separated)<input id="pwSources" value="'
    + escapeHtml((page.sources || []).join(', ')) + '" style="display:block;width:100%;margin-top:3px;background:#1e1e3a;color:#ddd;border:1px solid #444;border-radius:5px;padding:7px"></label>'
    + '<label style="font-size:11px;color:#999;display:flex;flex-direction:column;flex:1">Markdown<textarea id="pwBody" required style="margin-top:3px;flex:1;min-height:240px;background:#111124;color:#ddd;border:1px solid #444;border-radius:5px;padding:9px;resize:vertical">'
    + escapeHtml(page.content || '') + '</textarea></label>'
    + '<div style="display:flex;gap:7px;justify-content:flex-end">'
    + (editing ? _pwButton('Cancel', 'pwRenderPage(_pwState.current)', false) : '')
    + '<button type="submit" style="background:#1e3a5f;color:#4fc3f7;border:1px solid #355f88;border-radius:6px;padding:6px 12px;cursor:pointer">Save</button>'
    + '</div></form>';
}

function pwSavePage(event) {
  event.preventDefault();
  const payload = {
    relay_id: _pwState.relay,
    slug: document.getElementById('pwSlug').value.trim(),
    title: document.getElementById('pwTitle').value.trim(),
    summary: document.getElementById('pwSummary').value.trim(),
    content: document.getElementById('pwBody').value,
    sources: document.getElementById('pwSources').value.split(',').map(function(v) { return v.trim(); }).filter(Boolean),
  };
  action$('project_wiki_save', payload).subscribe({
    next: function(data) {
      if (!data || data.error) { _pwError((data && data.error) || 'Cannot save page'); return; }
      pwLoadStatus();
      pwLoadPages(_pwState.query);
      pwOpenPage(payload.slug || payload.title);
    },
    error: _pwError,
  });
}

function pwDeletePage() {
  if (!_pwState.current || !confirm('Delete wiki page "' + _pwState.current.title + '"?')) return;
  const slug = _pwState.current.slug;
  action$('project_wiki_delete', _pwArgs({ slug: slug })).subscribe({
    next: function(data) {
      if (!data || data.error) { _pwError((data && data.error) || 'Cannot delete page'); return; }
      _pwState.current = null;
      const content = document.getElementById('pwContent');
      if (content) content.innerHTML = '<div style="color:#777;text-align:center;padding:30px">Page deleted.</div>';
      pwLoadStatus();
      pwLoadPages(_pwState.query);
    },
    error: _pwError,
  });
}

function pwRefresh() {
  const content = document.getElementById('pwContent');
  if (content) content.innerHTML = '<div style="color:#4fc3f7">Refreshing source hashes...</div>';
  action$('project_wiki_refresh', _pwArgs({ path: '.' })).subscribe({
    next: function(data) {
      if (!data || data.error) { _pwError((data && data.error) || 'Refresh failed'); return; }
      if (content) content.innerHTML = '<pre style="white-space:pre-wrap;color:#bbb">'
        + escapeHtml(JSON.stringify(data, null, 2)) + '</pre>';
      pwLoadStatus();
      pwLoadPages(_pwState.query);
    },
    error: _pwError,
  });
}

function pwLint() {
  action$('project_wiki_lint', _pwArgs()).subscribe({
    next: function(data) {
      if (!data || data.error) { _pwError((data && data.error) || 'Lint failed'); return; }
      const content = document.getElementById('pwContent');
      if (content) content.innerHTML = '<h3 style="color:#ddd">Wiki lint</h3><pre style="white-space:pre-wrap;color:#bbb">'
        + escapeHtml(JSON.stringify(data, null, 2)) + '</pre>';
    },
    error: _pwError,
  });
}
