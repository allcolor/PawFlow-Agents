// ── Flow instance context menu ───────────────────────────────────
function showFlowInstanceMenu(e, instanceId, status, scope) {
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
  if (status === 'running') {
    item('\u23F9 Stop', () => _flowAction(instanceId, 'stop_flow'));
  } else {
    item('\u25B6 Start...', () => _showFlowStartDialog(instanceId));
  }
  item('\u270F Edit params...', () => _showFlowStartDialog(instanceId, true));
  item('\ud83d\udcc8 View graph', () => addBrowserTab(instanceId, '/chat/js/flow_graph.html?instance_id=' + encodeURIComponent(instanceId)));
  if (scope === 'conversation') {
    item('\u2B06 Promote to user', () => {
      fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'promote_flow', instance_id: instanceId, target_scope: 'user' }),
      }).then(r => r.json()).then(d => {
        if (d.error) addMsg('error', d.error);
        else { addMsg('system', `Flow '${instanceId}' promoted to user scope`); loadResources(); }
      }).catch(e => addMsg('error', e.message));
    });
  }
  const sep = document.createElement('div');
  sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
  menu.appendChild(sep);
  item('\u{1F5D1} Undeploy', () => {
    if (!confirm(`Undeploy flow '${instanceId}'?`)) return;
    _flowAction(instanceId, 'undeploy_flow');
  }, true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

async function _showFlowStartDialog(instanceId, editOnly) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${editOnly ? 'Edit Flow Parameters' : 'Start Flow'}: ${instanceId}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div><div style="color:#888;font-size:12px;">Loading parameters...</div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_flow_instance', instance_id: instanceId }) });
    const data = await resp.json();
    if (data.error) { panel.querySelector('div:last-child').innerHTML = `<div style="color:#e94560;">${data.error}</div>`; return; }
    // Merge template defaults with instance overrides
    const tplParams = data.template_parameters || {};
    const instParams = data.parameters || {};
    const merged = { ...tplParams, ...instParams };
    let fieldsHtml = '';
    for (const [k, v] of Object.entries(merged)) {
      const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
      fieldsHtml += `<div style="margin-bottom:6px;"><label style="color:#aaa;font-size:11px;">${escapeHtml(k)}</label>
        <input class="flow-param-input" data-key="${escapeHtml(k)}" value="${escapeHtml(val)}" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-size:12px;"/></div>`;
    }
    if (!fieldsHtml) fieldsHtml = '<div style="color:#555;font-size:12px;">No parameters</div>';
    const btnLabel = editOnly ? 'Save' : 'Start';
    panel.querySelector('div:last-child').innerHTML = fieldsHtml
      + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
        <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
        <button id="flowStartBtn" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${btnLabel}</button>
      </div>`;
    document.getElementById('flowStartBtn').onclick = () => {
      const params = {};
      document.querySelectorAll('.flow-param-input').forEach(el => {
        params[el.dataset.key] = el.value;
      });
      // Save params first, then optionally start
      fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'update_flow_params', instance_id: instanceId, parameters: params }) })
      .then(r => r.json()).then(d => {
        if (d.error) { addMsg('error', d.error); return; }
        if (editOnly) {
          addMsg('system', 'Parameters updated for ' + instanceId);
          document.getElementById('resourceEditorOverlay').remove();
          loadResources();
        } else {
          _flowAction(instanceId, 'start_flow');
          document.getElementById('resourceEditorOverlay').remove();
        }
      }).catch(e => addMsg('error', e.message));
    };
  } catch (e) {
    panel.querySelector('div:last-child').innerHTML = '<div style="color:#e94560;">Error: ' + e.message + '</div>';
  }
}

function _flowAction(instanceId, action) {
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action, instance_id: instanceId }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `${action.replace('_', ' ')}: ${instanceId}`); loadResources(); }
  }).catch(e => addMsg('error', e.message));
}