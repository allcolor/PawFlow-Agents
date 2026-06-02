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
    error: (e) => addMsg('error', t('failed', { error: e.message })),
  });
}

function cmdAddVariable(name, value) {
  action$('add_variable', { key: name, value: value }).subscribe({
    next: (data) => {
      if (data.error) { addMsg('error', data.error); return; }
      addMsg('system', t('variableAdded', { name, ref: data.key || name, short: name }));
    },
    error: (e) => addMsg('error', t('failed', { error: e.message })),
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
    error: (e) => addMsg('error', t('failed', { error: e.message })),
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

function _formatFileSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
  return (n / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

function _formatFileDate(epochSeconds) {
  const t = Number(epochSeconds) || 0;
  if (t <= 0) return '—';
  const d = new Date(t * 1000);
  // Local short form: YYYY-MM-DD HH:mm — sortable + human readable.
  const pad = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
    + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

function loadConvFiles() {
  if (!conversationId) return;
  const list = document.getElementById('filesList');
  if (typeof clearFileSelection === 'function') clearFileSelection();
  list.innerHTML = '<span style="color:#808090;font-size:12px">' + t('loading') + '</span>';
  action$('list_conv_files', { conversation_id: conversationId }).subscribe({
    next: (data) => {
      const files = (data.files || []).slice();
      if (files.length === 0) {
        list.innerHTML = '<div class="file-panel-actions"><button class="btn btn-sm" onclick="clearAllConversationFiles()">' + escapeHtml(t('clearFileStore')) + '</button></div>'
          + '<span style="color:#808090;font-size:12px">' + t('noFilesInConversation') + '</span>';
        return;
      }
      // Newest-first. Server pre-sorts, but re-sort defensively.
      files.sort((a, b) => (Number(b.created_at) || 0) - (Number(a.created_at) || 0));

      const table = document.createElement('table');
      table.className = 'files-table';
      const thead = document.createElement('thead');
      thead.innerHTML = '<tr>'
        + '<th class="col-select"></th>'
        + '<th class="col-name">' + t('fileName') + '</th>'
        + '<th class="col-type">' + t('fileType') + '</th>'
        + '<th class="col-size">' + t('fileSize') + '</th>'
        + '<th class="col-date">' + t('fileDate') + '</th>'
        + '</tr>';
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const f of files) {
        const tr = document.createElement('tr');
        tr.dataset.fileId = f.file_id;
        if (!f.available) tr.className = 'unavailable';
        const href = window.location.origin + '/files/' + f.file_id;
        const selectTd = document.createElement('td');
        selectTd.className = 'col-select';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.fileSelect = '1';
        cb.addEventListener('click', (e) => { e.stopPropagation(); handleFileRowSelection(e, f.file_id); });
        selectTd.appendChild(cb);
        const nameTd = document.createElement('td');
        nameTd.className = 'col-name';
        if (f.available) {
          const a = document.createElement('a');
          a.href = href;
          a.target = '_blank';
          a.title = t('download');
          a.textContent = f.filename || f.file_id;
          nameTd.appendChild(a);
        } else {
          nameTd.textContent = t('missingItem', { value: f.filename || f.file_id });
        }
        const typeTd = document.createElement('td');
        typeTd.className = 'col-type';
        typeTd.textContent = f.content_type || '';
        const sizeTd = document.createElement('td');
        sizeTd.className = 'col-size';
        sizeTd.textContent = _formatFileSize(f.size);
        const dateTd = document.createElement('td');
        dateTd.className = 'col-date';
        dateTd.textContent = _formatFileDate(f.created_at);
        tr.appendChild(selectTd);
        tr.appendChild(nameTd);
        tr.appendChild(typeTd);
        tr.appendChild(sizeTd);
        tr.appendChild(dateTd);
        tr.addEventListener('click', (e) => handleFileRowSelection(e, f.file_id));
        tr.addEventListener('contextmenu',
          (e) => showFileMenu(e, f.file_id, f.filename || f.file_id));
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      list.innerHTML = '';
      const actions = document.createElement('div');
      actions.className = 'file-panel-actions';
      actions.innerHTML = '<button class="btn btn-sm" onclick="clearAllConversationFiles()">' + escapeHtml(t('clearFileStore')) + '</button>'
        + '<div id="fileSelectBar" style="display:none;align-items:center;gap:8px;margin-left:8px;">'
        + '<span data-file-selection-count style="font-size:12px;color:#8a8aa0"></span>'
        + '<button class="btn btn-sm btn-danger" onclick="deleteSelectedFiles()">' + escapeHtml(t('deleteSelected')) + '</button>'
        + '<button class="btn btn-sm" onclick="clearFileSelection()">' + escapeHtml(t('cancel')) + '</button>'
        + '</div>';
      list.appendChild(actions);
      list.appendChild(table);
    },
    error: () => {
      list.innerHTML = '<span style="color:#e94560;font-size:12px">' + escapeHtml(t('failedToLoadFiles')) + '</span>';
    },
  });
}
