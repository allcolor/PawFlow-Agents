
function showParamMenu(e, key, scope, isSecret) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:4px 0;min-width:140px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? '#e94560' : '#e0e0e0');
    d.onmouseenter = () => d.style.background = '#2a2a4a';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  item('\u{1F441} ' + t('view'), () => _showParamEditor(key, scope, isSecret, false, true));
  if (_canEditScope(scope)) {
    item('✏ ' + t('editWithEllipsis'), () => _showParamEditor(key, scope, isSecret, false));
    const sep = () => { const s = document.createElement('div'); s.style.cssText = 'height:1px;background:#333;margin:4px 0;'; menu.appendChild(s); };
    sep();
    const move = (toScope) => {
      const label = (scope === 'conversation' && toScope !== 'conversation') ? 'Promote to ' + toScope
        : (scope === 'global' && toScope !== 'global') ? 'Demote to ' + toScope
        : 'Move to ' + toScope;
      item(label, () => {
        const payload = { key, from_scope: scope, to_scope: toScope };
        if ((scope === 'conversation' || toScope === 'conversation') && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
        action$(isSecret ? 'move_secret_scope' : 'move_param_scope', payload, { skipConversationId: !(scope === 'conversation' || toScope === 'conversation') })
          .subscribe({ next: d => { if (d.error) addMsg('error', d.error); else loadResources(); }, error: e => addMsg('error', e.message) });
      });
    };
    if (scope !== 'user') move('user');
    if (scope !== 'conversation' && typeof conversationId !== 'undefined' && conversationId) move('conversation');
    if (scope !== 'global' && _isAdmin()) move('global');
    sep();
    item('\u{1F5D1} ' + t('delete'), () => {
      if (!confirm('Delete ' + (isSecret ? 'secret' : 'variable') + " '" + key + "' (" + scope + ')?')) return;
      const payload = { key, scope };
      if (scope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
      action$(isSecret ? 'delete_secret' : 'delete_param', payload, { skipConversationId: scope !== 'conversation' })
        .subscribe({ next: () => loadResources(), error: e => addMsg('error', e.message) });
    }, true);
  }
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

function _showParamEditor(key, scope, isSecret, isNew) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const title = isNew ? `New ${isSecret ? 'secret' : 'variable'}` : `Edit ${isSecret ? 'secret' : 'variable'}: ${key}`;
  let formHtml = '';
  if (isNew) {
    formHtml += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">' + t('key') + '</label><input id="pv-key" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>';
    formHtml += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">' + t('scope') + '</label><select id="pv-scope" style="background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">'
      + (_isAdmin() ? '<option value="global">' + t('global') + '</option>' : '')
      + '<option value="user">' + t('user') + '</option><option value="conversation">' + t('conversation') + '</option></select></div>';
  }
  const inputType = isSecret ? 'password' : 'text';
  formHtml += `<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">${t('value')}</label><input id="pv-value" type="${inputType}" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>`;
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:400px;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${escapeHtml(title)}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>` + formHtml + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
    <button onclick="_saveParam(${jsStringArg(key)},${jsStringArg(scope)},${isSecret},${isNew})" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextSave'))}</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _saveParam(origKey, origScope, isSecret, isNew) {
  const key = isNew ? (document.getElementById('pv-key').value || '').trim() : origKey;
  const scope = isNew ? (document.getElementById('pv-scope').value || 'conversation') : origScope;
  const value = document.getElementById('pv-value').value;
  if (!key) { alert(t('keyRequired')); return; }
  const actionName = isSecret ? 'set_secret' : 'set_param';
  const payload = { key, value, scope };
  if (scope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  action$(actionName, payload, { skipConversationId: scope !== 'conversation' }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
}

function toggleResourcesSection() {
  const el = document.getElementById('resourcesContent');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// ── File Viewer ─────────────────────────────────────────────────
function fetchFsFile(service, fpath) {
  // Fetch file from filesystem service and open in viewer
  action$('call_tool', { tool_name: 'read', arguments: { path: fpath, service: service } })
    .subscribe(data => {
      if (data.error) { addMsg('error', t('failedReadFile', { service: service, path: fpath, error: data.error })); return; }
      const result = data.result || '';
      // Check if it's text or base64 binary
      if (result.startsWith('(binary file')) {
        addMsg('system', t('binaryFile', { service: service, path: fpath }));
      } else {
        // Create blob and open in viewer
        const blob = new Blob([result], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        openFileViewer(url);
      }
    });
}

function openFileViewer(filenameOrUrl) {
  let viewer = document.getElementById('fileViewer');
  if (!viewer) {
    viewer = document.createElement('div');
    viewer.id = 'fileViewer';
    viewer.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#1e1e2e;border-bottom:2px solid #6c5ce7;max-height:50vh;display:flex;flex-direction:column;';
    viewer.innerHTML = `
      <div style="display:flex;align-items:center;padding:8px 16px;gap:12px;background:#2d2d44;">
        <span id="viewerFileName" style="flex:1;color:#ccc;font-size:14px;"></span>
        <span id="viewerFileSize" style="color:#888;font-size:12px;"></span>
        <a id="viewerDownload" download style="background:#6c5ce7;color:#fff;text-decoration:none;font-size:13px;padding:4px 12px;border-radius:4px;cursor:pointer;display:inline-block;">\u2B07 ${t('download')}</a>
        <button onclick="closeFileViewer()" style="background:#ff6b6b;border:none;color:#fff;font-size:13px;padding:4px 10px;border-radius:4px;cursor:pointer;">\u2715</button>
      </div>
      <div id="viewerContent" style="flex:1;overflow:auto;padding:16px;"></div>
    `;
    document.body.prepend(viewer);
  }
  viewer.style.display = 'flex';
  const contentEl = document.getElementById('viewerContent');
  const nameEl = document.getElementById('viewerFileName');
  const sizeEl = document.getElementById('viewerFileSize');
  const dlEl = document.getElementById('viewerDownload');

  // Determine if it's a URL or filename
  let url = filenameOrUrl;
  if (filenameOrUrl.startsWith('/files/')) {
    // Already a /files/<id>/<name> path — make absolute
    url = location.origin + filenameOrUrl;
  } else if (!filenameOrUrl.startsWith('http')) {
    // Bare filename — search in conversation files
    url = API.replace(/\/[^\/]*$/, '') + '/files/' + encodeURIComponent(filenameOrUrl);
  }
  const fname = filenameOrUrl.split('/').pop();
  const ext = fname.split('.').pop().toLowerCase();
  nameEl.textContent = fname;
  dlEl.download = fname;
  contentEl.innerHTML = '<p style="color:#888;">Loading...</p>';

  // All file fetches go through authenticated fetch to avoid auth redirects
  const authHeaders = {};
  const token = getToken();
  if (token) authHeaders['Authorization'] = 'Bearer ' + token;

  fetch(url, { headers: authHeaders, credentials: 'same-origin' }).then(r => {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const ct = r.headers.get('content-type') || '';
    sizeEl.textContent = r.headers.get('content-length')
      ? (parseInt(r.headers.get('content-length')) / 1024).toFixed(1) + ' KB' : '';
    // Create blob for download button
    return r.blob().then(blob => {
      const blobUrl = URL.createObjectURL(blob);
      dlEl.href = blobUrl;
      // Render based on type
      const IMG = ['png','jpg','jpeg','gif','svg','webp','bmp','ico','avif'];
      const VID = ['mp4','webm','mov','mkv','avi','m4v','ogv'];
      const AUD = ['mp3','wav','ogg','oga','m4a','flac','opus','aac','weba'];
      const TEXTY = ['txt','log','md','markdown','json','jsonl','ndjson','xml','yaml','yml','toml','ini','cfg','conf','csv','tsv','py','js','ts','tsx','jsx','css','scss','less','sh','bash','zsh','ps1','go','rs','java','c','cc','cpp','h','hpp','cs','rb','php','lua','sql','dockerfile','env','gitignore'];
      if (IMG.includes(ext) || ct.startsWith('image/')) {
        contentEl.innerHTML = '<img src="' + blobUrl + '" style="max-width:100%;max-height:40vh;object-fit:contain;">';
      } else if (VID.includes(ext) || ct.startsWith('video/')) {
        contentEl.innerHTML = '<video controls src="' + blobUrl + '" style="max-width:100%;max-height:40vh;background:#000;"></video>';
      } else if (AUD.includes(ext) || ct.startsWith('audio/')) {
        contentEl.innerHTML = '<audio controls src="' + blobUrl + '" style="width:100%;"></audio>';
      } else if (ext === 'pdf' || ct === 'application/pdf') {
        contentEl.innerHTML = '<iframe src="' + blobUrl + '" style="width:100%;height:40vh;border:none;"></iframe>';
      } else if (ext === 'html' || ct === 'text/html') {
        contentEl.innerHTML = '<iframe src="' + blobUrl + '" sandbox="allow-same-origin" style="width:100%;height:40vh;border:none;background:#fff;"></iframe>';
      } else if (TEXTY.includes(ext) || ct.startsWith('text/') || ct.includes('json') || ct.includes('xml') || ct.includes('yaml')) {
        blob.text().then(text => {
          sizeEl.textContent = (text.length / 1024).toFixed(1) + ' KB';
          contentEl.innerHTML = '<pre style="margin:0;white-space:pre-wrap;word-break:break-all;color:#ddd;font-size:13px;font-family:monospace;">' + text.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
        });
      } else {
        // Unknown binary — don't try to render, offer download only
        contentEl.innerHTML = '<div style="color:#aaa;padding:20px;text-align:center;">' +
          '<p style="font-size:14px;margin:0 0 8px 0;">Binary file — preview not available.</p>' +
          '<p style="font-size:12px;margin:0;color:#888;">Type: ' + escapeHtml(ct || 'unknown') + ' \u00B7 ' + escapeHtml(ext || 'no ext') + '</p>' +
          '<p style="font-size:12px;margin:8px 0 0 0;">Use the Download button above.</p></div>';
      }
    });
  }).catch((err) => {
    contentEl.innerHTML = '<p style="color:#ff6b6b;">Could not load file: ' + escapeHtml(err.message) + '</p>';
    dlEl.href = '#';
  });
}

function closeFileViewer() {
  const v = document.getElementById('fileViewer');
  if (v) v.style.display = 'none';
}

// Intercept file links in messages to open viewer
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[href*="/files/"]');
  if (a) {
    e.preventDefault();
    openFileViewer(a.href);
  }
});
