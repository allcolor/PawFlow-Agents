// ── Secrets & Variables ──────────────────────────────────────────
function cmdListSecrets() {
  action$('list_secrets').subscribe({
    next: (data) => {
      const result = data.result || '';
      if (!result || result.includes('No secrets')) {
        addMsg('system', t('secretListEmpty'));
      } else {
        addMsg('system', result);
      }
    },
    error: (e) => addMsg('error', 'Failed: ' + e.message),
  });
}

function cmdAddVariable(name, value) {
  action$('add_variable', { key: name, value: value }).subscribe({
    next: (data) => {
      if (data.error) { addMsg('error', data.error); return; }
      addMsg('system', t('variableAdded', { name, ref: data.key || name, short: name }));
    },
    error: (e) => addMsg('error', 'Failed: ' + e.message),
  });
}

function cmdListVariables() {
  action$('list_variables').subscribe({
    next: (data) => {
      const result = data.result || '';
      if (!result || result.includes('No variables')) {
        addMsg('system', t('variableListEmpty'));
      } else {
        addMsg('system', result);
      }
    },
    error: (e) => addMsg('error', 'Failed: ' + e.message),
  });
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

function loadConvFiles() {
  if (!conversationId) return;
  const list = document.getElementById('filesList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  action$('list_conv_files', { conversation_id: conversationId }).subscribe({
    next: (data) => {
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
    },
    error: () => {
      list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load files</span>';
    },
  });
}
