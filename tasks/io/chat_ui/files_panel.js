// ── File context menu ──────────────────────────────────────────
function showFileMenu(e, fileId, filename) {
  e.preventDefault();
  closeFileMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'fileCtxMenu';
  _positionMenu(menu, e);
  const href = window.location.origin + '/files/' + fileId;
  menu.innerHTML =
    '<div class="ctx-menu-item" onclick="event.stopPropagation();openFileViewer(\'' + href + '\');closeFileMenu();">&#x1F441; View</div>' +
    '<div class="ctx-menu-item" onclick="event.stopPropagation();window.open(\'' + href + '\',\'_blank\');closeFileMenu();">&#x2B07; Download</div>' +
    '<div class="ctx-menu-item danger" onclick="event.stopPropagation();deleteFile(\'' + fileId + '\');closeFileMenu();">&#x1F5D1; Delete</div>';
  setTimeout(() => document.addEventListener('click', closeFileMenu, {once: true}), 0);
}

function closeFileMenu() {
  const m = document.getElementById('fileCtxMenu');
  if (m) m.remove();
}

function deleteFile(fileId) {
  action$('delete_file', { file_id: fileId }).subscribe(data => {
    if (data.error) {
      addMsg('system', 'Delete failed: ' + (data.error || 'unknown'));
    } else if (data.ok) {
      loadConvFiles();
    } else {
      addMsg('system', 'Delete failed: unknown');
    }
  });
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
    menu.innerHTML = '<div class="ctx-menu-item" onclick="flowAction(\'' + flowId + '\', \'stop\')">&#x23F9; Stop</div>' +
      '<div class="ctx-menu-item danger" onclick="flowAction(\'' + flowId + '\', \'delete\')">&#x1F5D1; Delete</div>';
  } else {
    menu.innerHTML = '<div class="ctx-menu-item" onclick="flowAction(\'' + flowId + '\', \'start\')">&#x25B6; Start</div>' +
      '<div class="ctx-menu-item danger" onclick="flowAction(\'' + flowId + '\', \'delete\')">&#x1F5D1; Delete</div>';
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
      addMsg('system', '\u2705 ' + (data.message || action + ' done'));
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
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  action$('list_schedules').subscribe(data => {
    if (data.error) {
      list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load schedules</span>';
      return;
    }
    const scheds = data.schedules || [];
    if (scheds.length === 0) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">No scheduled tasks.</span>';
      return;
    }
    list.innerHTML = scheds.map(s => {
      const at = new Date(s.recheck_at * 1000);
      const now = Date.now();
      const isPast = at.getTime() < now;
      const timeStr = at.toLocaleString();
      const relative = isPast ? 'overdue' : formatRelative(at.getTime() - now);
      const reason = s.reason ? escapeHtml(s.reason) : 'recheck';
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
  const resp = await fetch('/api/upload', { method: 'POST', body: fd, headers: {'Authorization': getAuthHeaders()['Authorization'] || ''}, credentials: 'same-origin' });
  const data = await resp.json();
  if (!data.ok || !data.files || !data.files.length) throw new Error(data.error || 'Upload failed');
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
        addMsg('system', `Installing tool from ${file.name}...`);
        action$('install_tool', { filename: file.name, source }).subscribe(data => {
          if (data.error) { addMsg('error', 'Install failed: ' + data.error); }
          else { addMsg('system', `Tool **${data.tool_name}** installed: ${data.description}`); }
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
      addMsg('error', `Upload failed for ${file.name}: ${err.message}`);
      const i = pendingFiles.indexOf(placeholder);
      if (i >= 0) { pendingFiles.splice(i, 1); renderAttachments(); }
    });
  }
  // Reset file input so same file can be re-selected
  document.getElementById('fileInput').value = '';
}
