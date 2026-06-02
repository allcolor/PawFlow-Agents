// ── File context menu ──────────────────────────────────────────
var _convFilesSelected = new Set();
var _convFilesLastSelected = '';

function showFileMenu(e, fileId, filename) {
  e.preventDefault();
  closeFileMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'fileCtxMenu';
  _positionMenu(menu, e);
  const href = window.location.origin + '/files/' + fileId;
  menu.innerHTML =
    '<div class="ctx-menu-item" onclick="event.stopPropagation();openFileViewer(\'' + href + '\');closeFileMenu();">&#x1F441; ' + escapeHtml(t('view')) + '</div>' +
    '<div class="ctx-menu-item" onclick="event.stopPropagation();window.open(\'' + href + '\',\'_blank\');closeFileMenu();">&#x2B07; ' + escapeHtml(t('download')) + '</div>' +
    '<div class="ctx-menu-item danger" onclick="event.stopPropagation();deleteFile(\'' + fileId + '\');closeFileMenu();">&#x1F5D1; ' + escapeHtml(t('delete')) + '</div>';
  setTimeout(() => document.addEventListener('click', closeFileMenu, {once: true}), 0);
}

function closeFileMenu() {
  const m = document.getElementById('fileCtxMenu');
  if (m) m.remove();
}

function deleteFile(fileId) {
  action$('delete_file', { file_id: fileId }).subscribe(data => {
    if (data.error) {
      addMsg('system', t('deleteFailed', { error: data.error || t('unknownError') }));
    } else if (data.ok) {
      loadConvFiles();
    } else {
      addMsg('system', t('deleteFailedUnknown'));
    }
  });
}

function deleteSelectedFiles() {
  const ids = Array.from(_convFilesSelected || []);
  if (!ids.length) return;
  if (!confirm(t('deleteItemsConfirm', { label: t('itemsSelected', { n: ids.length }) }))) return;
  action$('delete_files', { file_ids: ids, conversation_id: conversationId }).subscribe(data => {
    if (data.error) addMsg('system', t('deleteFailed', { error: data.error || t('unknownError') }));
    else loadConvFiles();
  });
}

function clearAllConversationFiles() {
  if (!conversationId) return;
  if (!confirm(t('clearFileStoreConfirm'))) return;
  action$('clear_store', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) addMsg('error', data.error);
    else {
      _convFilesSelected.clear();
      addMsg('system', t('fileStoreDeleted', { n: data.deleted || 0, scope: data.scope ? ' (' + data.scope + ')' : '' }));
      loadConvFiles();
    }
  });
}

function updateFileSelectionBar() {
  const bar = document.getElementById('fileSelectBar');
  if (!bar) return;
  const count = _convFilesSelected.size;
  const label = bar.querySelector('[data-file-selection-count]');
  if (label) label.textContent = t('itemsSelected', { n: count });
  bar.style.display = count > 0 ? 'flex' : 'none';
}

function clearFileSelection() {
  _convFilesSelected.clear();
  _convFilesLastSelected = '';
  document.querySelectorAll('#filesList tr.file-selected').forEach(row => row.classList.remove('file-selected'));
  document.querySelectorAll('#filesList input[data-file-select]').forEach(cb => { cb.checked = false; });
  updateFileSelectionBar();
}

function toggleFileSelection(fileId, row, checked) {
  if (!fileId || !row) return;
  if (checked) {
    _convFilesSelected.add(fileId);
    row.classList.add('file-selected');
  } else {
    _convFilesSelected.delete(fileId);
    row.classList.remove('file-selected');
  }
  const cb = row.querySelector('input[data-file-select]');
  if (cb) cb.checked = checked;
  updateFileSelectionBar();
}

function handleFileRowSelection(e, fileId) {
  const row = e.currentTarget && e.currentTarget.closest
    ? e.currentTarget.closest('tr[data-file-id]') : null;
  if (!row || !fileId) return;
  const rows = Array.from(document.querySelectorAll('#filesList tbody tr[data-file-id]'));
  if (e.shiftKey && _convFilesLastSelected) {
    const start = rows.findIndex(r => r.dataset.fileId === _convFilesLastSelected);
    const end = rows.findIndex(r => r.dataset.fileId === fileId);
    if (start >= 0 && end >= 0) {
      const lo = Math.min(start, end);
      const hi = Math.max(start, end);
      for (let i = lo; i <= hi; i++) toggleFileSelection(rows[i].dataset.fileId, rows[i], true);
      return;
    }
  }
  if (e.ctrlKey || e.metaKey || e.shiftKey || e.target.matches('input[data-file-select]')) {
    toggleFileSelection(fileId, row, !_convFilesSelected.has(fileId));
  } else {
    clearFileSelection();
    toggleFileSelection(fileId, row, true);
  }
  _convFilesLastSelected = fileId;
}

// ── Flow context menu ──────────────────────────────────────────
function showFlowMenu(e, flowId, flowStatus) {
  e.preventDefault();
  closeFlowMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'flowCtxMenu';
  _positionMenu(menu, e);

  if (flowStatus === 'running') {
    menu.innerHTML = '<div class="ctx-menu-item" onclick="flowAction(\'' + flowId + '\', \'stop\')">&#x23F9; ' + escapeHtml(t('flowStop')) + '</div>' +
      '<div class="ctx-menu-item danger" onclick="flowAction(\'' + flowId + '\', \'delete\')">&#x1F5D1; ' + escapeHtml(t('delete')) + '</div>';
  } else {
    menu.innerHTML = '<div class="ctx-menu-item" onclick="flowAction(\'' + flowId + '\', \'start\')">&#x25B6; ' + escapeHtml(t('flowStart')) + '</div>' +
      '<div class="ctx-menu-item danger" onclick="flowAction(\'' + flowId + '\', \'delete\')">&#x1F5D1; ' + escapeHtml(t('delete')) + '</div>';
  }
  setTimeout(() => document.addEventListener('click', closeFlowMenu, {once: true}), 0);
}

function closeFlowMenu() {
  const m = document.getElementById('flowCtxMenu');
  if (m) m.remove();
}

function flowAction(flowId, action) {
  closeFlowMenu();
  action$('manage_conv_flow', {
    flow_id: flowId,
    flow_action: action,
  }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      addMsg('system', '\u2705 ' + (data.message || t('flowActionDone', { action: action })));
    }
    loadResources();
  });
}

// ── Scheduled Tasks panel ──────────────────────────────────────
async function toggleSchedsPanel() {
  const panel = document.getElementById('schedsPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadConvScheds();
  } else {
    panel.style.display = 'none';
  }
}

function loadConvScheds() {
  if (!conversationId) return;
  const list = document.getElementById('schedsList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">' + t('loading') + '</span>';
  action$('list_schedules').subscribe(data => {
    if (data.error) {
      list.innerHTML = '<span style="color:#e94560;font-size:12px">' + t('failedToLoadSchedules') + '</span>';
      return;
    }
    const scheds = data.schedules || [];
    if (scheds.length === 0) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">' + t('noScheduledTasks') + '</span>';
      return;
    }
    list.innerHTML = scheds.map(s => {
      const at = new Date(s.recheck_at * 1000);
      const now = Date.now();
      const isPast = at.getTime() < now;
      const timeStr = at.toLocaleString();
      const relative = isPast ? t('overdue') : formatRelative(at.getTime() - now);
      const reason = s.reason ? escapeHtml(s.reason) : t('recheck');
      return '<span class="sched-chip">' +
        '<span class="sched-icon">&#x23F0;</span> ' +
        escapeHtml(reason) +
        ' <span style="color:#808090;font-size:11px">(' + timeStr + ', ' + relative + ')</span>' +
        '</span>';
    }).join('');
  });
}

function formatRelative(ms) {
  if (ms < 0) return 'overdue';
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return secs + 's';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + 'min';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ' + (mins % 60) + 'min';
  const days = Math.floor(hrs / 24);
  return days + 'd ' + (hrs % 24) + 'h';
}

// Upload a file via multipart to /api/upload, returns {file_id, filename, mime_type, size, url}
async function uploadFileToStore(file) {
  const fd = new FormData();
  fd.append('file', file);
  if (typeof currentConvId !== 'undefined' && currentConvId) fd.append('conversation_id', currentConvId);
  const ttlSelect = document.getElementById('ttlSelect');
  const ttlVal = ttlSelect ? parseInt(ttlSelect.value, 10) : 0;
  if (ttlVal > 0) fd.append('ttl', String(ttlVal));
  const resp = await fetch('/api/upload', { method: 'POST', body: fd, headers: {'Authorization': getAuthHeaders()['Authorization'] || ''}, credentials: 'same-origin' });
  const data = await resp.json();
  if (!data.ok || !data.files || !data.files.length) throw new Error(data.error || t('uploadFailed'));
  return data.files[0];
}

// File upload handling
function handleFiles(fileList) {
  for (const file of fileList) {
    // .py files → offer to install as dynamic tool
    if (file.name.endsWith('.py')) {
      const textReader = new FileReader();
      textReader.onload = (e) => {
        const source = e.target.result;
        addMsg('system', t('installingToolFrom', { file: file.name }));
        action$('install_tool', { filename: file.name, source }).subscribe(data => {
          if (data.error) { addMsg('error', t('installFailed', { error: data.error })); }
          else { addMsg('system', t('toolInstalled', { tool: data.tool_name, description: data.description })); }
        });
      };
      textReader.readAsText(file);
      continue;
    }
    // Upload via multipart (no size limit, no base64 OOM)
    const mime = file.type || 'application/octet-stream';
    const isImage = mime.startsWith('image/');
    // Show placeholder immediately
    const idx = pendingFiles.length;
    const placeholder = { filename: file.name, mime_type: mime, uploading: true };
    if (isImage) placeholder.dataUrl = URL.createObjectURL(file);
    pendingFiles.push(placeholder);
    renderAttachments();
    uploadFileToStore(file).then(info => {
      // Replace placeholder with uploaded info
      const entry = pendingFiles.find(f => f === placeholder);
      if (entry) {
        entry.file_id = info.file_id;
        entry.url = info.url;
        entry.size = info.size;
        entry.uploading = false;
        if (isImage && !entry.dataUrl) entry.dataUrl = info.url;
        renderAttachments();
      }
    }).catch(err => {
      addMsg('error', t('uploadFailedFor', { file: file.name, error: err.message }));
      const i = pendingFiles.indexOf(placeholder);
      if (i >= 0) { pendingFiles.splice(i, 1); renderAttachments(); }
    });
  }
  // Reset file input so same file can be re-selected
  document.getElementById('fileInput').value = '';
}
