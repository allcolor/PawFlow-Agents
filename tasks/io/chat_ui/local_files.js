    });
    const data = await r.json();
    const prompts = data.prompts || [];
    if (!prompts.length) { addMsg('system', 'No prompts available. Create prompts via /prompt or manage_resource.'); return; }
    // Build a simple selection overlay
    let overlay = document.getElementById('promptOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'promptOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';
    let html = '<div style="background:#1a1a2e;border:1px solid #0f3460;border-radius:12px;max-width:500px;width:90%;max-height:70vh;overflow-y:auto;padding:20px">';
    html += '<h3 style="margin:0 0 12px;color:#e94560">Prompt Library</h3>';
    for (const p of prompts) {
      html += '<div class="prompt-item" data-name="' + escapeHtml(p.name) + '" style="padding:10px;margin:4px 0;background:#16213e;border-radius:8px;cursor:pointer;border:1px solid transparent" onmouseenter="this.style.borderColor=\'#e94560\'" onmouseleave="this.style.borderColor=\'transparent\'">';
      html += '<div style="font-weight:600;color:#fff">' + escapeHtml(p.title || p.name) + '</div>';
      if (p.category) html += '<span style="font-size:11px;color:#888;margin-right:8px">' + escapeHtml(p.category) + '</span>';
      if (p.description) html += '<span style="font-size:11px;color:#aaa">' + escapeHtml(p.description) + '</span>';
      if (p.preview) html += '<div style="font-size:11px;color:#666;margin-top:4px">' + escapeHtml(p.preview) + '...</div>';
      html += '</div>';
    }
    html += '<button onclick="document.getElementById(\'promptOverlay\').remove()" style="margin-top:12px;padding:6px 16px;background:#0f3460;color:#fff;border:none;border-radius:6px;cursor:pointer">Close</button>';
    html += '</div>';
    overlay.innerHTML = html;
    overlay.querySelectorAll('.prompt-item').forEach(item => {
      item.addEventListener('click', async () => {
        const name = item.dataset.name;
        try {
          const r2 = await fetch(AGENT_PATH, {
            method: 'POST', headers: {'Content-Type':'application/json', ...authHeaders()},
            body: JSON.stringify({action:'get_prompt', name: name, conversation_id: conversationId})
          });
          const d2 = await r2.json();
          if (d2.content) {
            document.getElementById('input').value = d2.content;
            document.getElementById('input').focus();
          }
        } catch(e) { addMsg('error', 'Failed to load prompt: ' + e.message); }
        overlay.remove();
      });
    });
    document.body.appendChild(overlay);
  } catch (e) { addMsg('error', 'Failed to list prompts: ' + e.message); }
}

async function openLocalFolder() {
  if (!window.showDirectoryPicker) {
    alert(t('folderUnsupported'));
    return;
  }
  try {
    localDirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
    localDirName = localDirHandle.name;
    const btn = document.getElementById('folderBtn');
    btn.classList.add('active');
    btn.title = t('folderActive', {name: localDirName});
  } catch (e) {
    if (e.name !== 'AbortError') console.error('Directory picker error:', e);
  }
}

async function resolvePathHandle(dirHandle, pathStr, create) {
  const parts = pathStr.replace(/\\/g, '/').split('/').filter(Boolean);
  let current = dirHandle;
  for (let i = 0; i < parts.length - 1; i++) {
    current = await current.getDirectoryHandle(parts[i], { create: !!create });
  }
  return { parent: current, name: parts[parts.length - 1] || '' };
}

async function listLocalDir(path) {
  let target = localDirHandle;
  if (path && path !== '.' && path !== '/') {
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean);
    for (const part of parts) { target = await target.getDirectoryHandle(part); }
  }
  const entries = [];
  for await (const [name, handle] of target) {
    if (handle.kind === 'file') {
      try {
        const f = await handle.getFile();
        entries.push({ name, kind: 'file', size: f.size });
      } catch { entries.push({ name, kind: 'file' }); }
    } else {
      entries.push({ name, kind: 'directory' });
    }
  }
  entries.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === 'directory' ? -1 : 1));
  return { path: path || '.', entries };
}

async function readLocalFile(path) {
  const { parent, name } = await resolvePathHandle(localDirHandle, path, false);
  const fileHandle = await parent.getFileHandle(name);
  const file = await fileHandle.getFile();
  const text = await file.text();
  if (text.length > 100000) {
    return { content: text.substring(0, 100000), truncated: true, total_size: text.length };
  }
  return { content: text, size: text.length };
}

async function writeLocalFile(path, content) {
  const { parent, name } = await resolvePathHandle(localDirHandle, path, true);
  const fileHandle = await parent.getFileHandle(name, { create: true });
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
  return { written: true, path, size: content.length };
}

async function handleFileRequest(data) {
  const { request_id, action, path, content } = data;
  let result;
  try {
    if (!localDirHandle) {
      result = { error: 'No local directory open. Ask the user to click the folder button.' };
    } else if (action === 'list_dir') {
      result = await listLocalDir(path);
    } else if (action === 'read_file') {
      result = await readLocalFile(path);
    } else if (action === 'write_file') {
      result = await writeLocalFile(path, content || '');
    } else {
      result = { error: 'Unknown action: ' + action };
    }
  } catch (e) {
    result = { error: e.message || String(e) };
  }
  // POST result back to agent
  try {
    await fetch(API, {
      method: 'POST',
      headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'file_result',
        request_id: request_id,
        result: result,
        conversation_id: conversationId,
      }),
    });
  } catch (e) { console.error('Failed to send file result:', e); }
}

// ── Exec approval dialog ─────────────────────────────────────────
function showExecApprovalDialog(data) {
  const { request_id, action, command, risk_level, cwd, editable } = data;
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  const riskLabel = risk_level.charAt(0).toUpperCase() + risk_level.slice(1);
  const cmdHtml = editable