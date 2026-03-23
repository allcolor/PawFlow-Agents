
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
  item('\u{1F441} View', () => _showParamEditor(key, scope, isSecret, false, !_canEditScope(scope)));
  if (_canEditScope(scope)) {
    item('\u270F Edit...', () => _showParamEditor(key, scope, isSecret, false));
    item('\u{1F5D1} Delete', () => {
      if (!confirm(`Delete ${isSecret ? 'secret' : 'variable'} '${key}' (${scope})?`)) return;
      fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: isSecret ? 'delete_secret' : 'delete_param', key, scope, conversation_id: conversationId }),
      }).then(() => loadResources()).catch(e => addMsg('error', e.message));
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
    formHtml += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Key</label><input id="pv-key" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>';
    formHtml += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Scope</label><select id="pv-scope" style="background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"><option value="conversation">Conversation</option><option value="user">User</option></select></div>';
  }
  const inputType = isSecret ? 'password' : 'text';
  formHtml += `<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Value</label><input id="pv-value" type="${inputType}" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>`;
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:400px;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${title}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>` + formHtml + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
    <button onclick="_saveParam('${key}','${scope}',${isSecret},${isNew})" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Save</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _saveParam(origKey, origScope, isSecret, isNew) {
  const key = isNew ? (document.getElementById('pv-key').value || '').trim() : origKey;
  const scope = isNew ? (document.getElementById('pv-scope').value || 'conversation') : origScope;
  const value = document.getElementById('pv-value').value;
  if (!key) { alert('Key is required'); return; }
  const action = isSecret ? 'set_secret' : 'set_param';
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action, key, value, scope, conversation_id: conversationId }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else { document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  }).catch(e => addMsg('error', e.message));
}

function toggleResourcesSection() {
  const el = document.getElementById('resourcesContent');
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// ── File Viewer ─────────────────────────────────────────────────
function fetchFsFile(service, fpath) {
  // Fetch file from filesystem service and open in viewer
  fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({
      action: 'call_tool', tool_name: 'filesystem',
      arguments: { action: 'read_file', path: fpath, service: service },
      conversation_id: conversationId,
    }),
  }).then(r => r.json()).then(data => {
    if (data.error) { addMsg('error', 'Failed to read fs://' + service + '/' + fpath + ': ' + data.error); return; }
    const result = data.result || '';
    // Check if it's text or base64 binary
    if (result.startsWith('(binary file')) {
      addMsg('system', 'Binary file: fs://' + service + '/' + fpath);
    } else {
      // Create blob and open in viewer
      const blob = new Blob([result], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      openFileViewer(url);
    }
  }).catch(e => addMsg('error', 'Failed: ' + e.message));
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
        <a id="viewerDownload" download style="background:#6c5ce7;color:#fff;text-decoration:none;font-size:13px;padding:4px 12px;border-radius:4px;cursor:pointer;display:inline-block;">\u2B07 Download</a>
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
  if (!filenameOrUrl.startsWith('http')) {
    // Search in conversation files
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
      if (['png','jpg','jpeg','gif','svg','webp','bmp'].includes(ext) || ct.startsWith('image/')) {
        contentEl.innerHTML = '<img src="' + blobUrl + '" style="max-width:100%;max-height:40vh;object-fit:contain;">';
      } else if (ext === 'pdf' || ct === 'application/pdf') {
        contentEl.innerHTML = '<iframe src="' + blobUrl + '" style="width:100%;height:40vh;border:none;"></iframe>';
      } else if (ext === 'html' || ct === 'text/html') {
        contentEl.innerHTML = '<iframe src="' + blobUrl + '" sandbox="allow-same-origin" style="width:100%;height:40vh;border:none;background:#fff;"></iframe>';
      } else {
        // Text/code preview
        blob.text().then(text => {
          sizeEl.textContent = (text.length / 1024).toFixed(1) + ' KB';
          contentEl.innerHTML = '<pre style="margin:0;white-space:pre-wrap;word-break:break-all;color:#ddd;font-size:13px;font-family:monospace;">' + text.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</pre>';
        });
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
