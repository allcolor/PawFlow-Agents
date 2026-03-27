// ── Secrets & Variables ──────────────────────────────────────────
async function cmdListSecrets() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_secrets' }),
    });
    const data = await resp.json();
    const result = data.result || '';
    if (!result || result.includes('No secrets')) {
      addMsg('system', t('secretListEmpty'));
    } else {
      addMsg('system', result);
    }
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdAddVariable(name, value) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_variable', key: name, value: value }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('variableAdded', { name, ref: data.key || name, short: name }));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListVariables() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_variables' }),
    });
    const data = await resp.json();
    const result = data.result || '';
    if (!result || result.includes('No variables')) {
      addMsg('system', t('variableListEmpty'));
    } else {
      addMsg('system', result);
    }
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

// ── Files panel ─────────────────────────────────────────────────
async function toggleFilesPanel() {
  const panel = document.getElementById('filesPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadConvFiles();
  } else {
    panel.style.display = 'none';
  }
}

async function loadConvFiles() {
  if (!conversationId) return;
  const list = document.getElementById('filesList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_conv_files', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const files = data.files || [];
    if (files.length === 0) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">No files in this conversation.</span>';
      return;
    }
    const available = files.filter(f => f.available);
    if (!available.length) {
      list.innerHTML = '<span style="color:#555;font-size:12px">No files</span>';
      return;
    }
    list.innerHTML = '';
    for (const f of available) {
      const href = window.location.origin + '/files/' + f.file_id + '/' + f.filename;
      const chip = document.createElement('span');
      chip.className = 'file-chip';
      chip.innerHTML = `<span class="file-status available" title="Available"></span><a href="${href}" target="_blank" title="Download">${escapeHtml(f.filename)}</a>`;
      chip.addEventListener('contextmenu', (e) => showFileMenu(e, f.file_id, f.filename));
      list.appendChild(chip);
    }
  } catch (e) {
    list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load files</span>';
  }
}