// ── File context menu ──────────────────────────────────────────
function showFileMenu(e, fileId, filename) {
  e.preventDefault();
  closeFileMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'fileCtxMenu';
  _positionMenu(menu, e);
  const href = window.location.origin + '/files/' + fileId + '/' + filename;
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

async function deleteFile(fileId) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'delete_file', file_id: fileId, conversation_id: conversationId }),
      credentials: 'same-origin',
    });
    const data = await resp.json();
    if (data.ok) {
      loadConvFiles();
    } else {
      addMsg('system', 'Delete failed: ' + (data.error || 'unknown'));
    }
  } catch (e) {
    addMsg('system', 'Delete failed: ' + e.message);
  }
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

async function flowAction(flowId, action) {
  closeFlowMenu();
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'manage_conv_flow',
        conversation_id: conversationId,
        flow_id: flowId,
        flow_action: action,
      }),
    });
    const data = await resp.json();
    if (data.error) {
      addMsg('system', '\\u274C ' + data.error);
    } else {
      addMsg('system', '\\u2705 ' + (data.message || action + ' done'));
    }
    await loadResources();
  } catch (e) {
    addMsg('error', 'Flow action failed: ' + e.message);
  }
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

async function loadConvScheds() {
  if (!conversationId) return;
  const list = document.getElementById('schedsList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_schedules', conversation_id: conversationId }),
    });
    const data = await resp.json();
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
  } catch (e) {
    list.innerHTML = '<span style="color:#e94560;font-size:12px">Failed to load schedules</span>';
  }
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

// File upload handling
function handleFiles(fileList) {
  const MAX_SIZE = 10 * 1024 * 1024; // 10MB per file
  for (const file of fileList) {
    if (file.size > MAX_SIZE) {
      addMsg('error', t('fileTooLarge', {name: file.name, size: (file.size / 1024 / 1024).toFixed(1)}));
      continue;
    }
    // .py files → offer to install as dynamic tool
    if (file.name.endsWith('.py')) {
      const textReader = new FileReader();
      textReader.onload = async (e) => {
        const source = e.target.result;
        addMsg('system', `Installing tool from ${file.name}...`);
        try {
          const resp = await fetch(API, {
            method: 'POST', headers: getAuthHeaders(),
            body: JSON.stringify({ action: 'install_tool', filename: file.name, source }),
          });
          const data = await resp.json();
          if (data.error) { addMsg('error', 'Install failed: ' + data.error); }
          else { addMsg('system', `Tool **${data.tool_name}** installed: ${data.description}`); }
        } catch (err) { addMsg('error', 'Install failed: ' + err.message); }
      };
      textReader.readAsText(file);
      continue;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target.result;
      const base64 = dataUrl.split(',')[1];
      const entry = {
        file: file,
        filename: file.name,
        mime_type: file.type || 'application/octet-stream',
        data: base64,
        dataUrl: dataUrl,
      };
      pendingFiles.push(entry);
      renderAttachments();
    };
    reader.readAsDataURL(file);
  }
  // Reset file input so same file can be re-selected
  document.getElementById('fileInput').value = '';
}