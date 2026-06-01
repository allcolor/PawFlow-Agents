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
    item('\u23F9 ' + t('stop'), () => _flowAction(instanceId, 'stop_flow'));
  } else {
    item('\u25B6 ' + t('flowStartMenu'), () => _showFlowStartDialog(instanceId));
  }
  item('\u270F ' + t('flowEditParamsMenu'), () => _showFlowStartDialog(instanceId, true));
  item('\ud83d\udcc8 ' + t('flowViewGraph'), () => _openFlowGraphTab(instanceId));
  if (scope === 'conversation') {
    item('\u2B06 ' + t('flowPromoteToUser'), () => {
      action$('promote_flow', { instance_id: instanceId, target_scope: 'user' }).subscribe({
        next: (d) => {
          if (d.error) addMsg('error', d.error);
          else { addMsg('system', t('flowPromotedToUser', { id: instanceId })); loadResources(); }
        },
        error: (e) => addMsg('error', e.message),
      });
    });
  }
  const sep = document.createElement('div');
  sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
  menu.appendChild(sep);
  item('\u{1F5D1} ' + t('flowUndeploy'), () => {
    if (!confirm(t('flowUndeployConfirm', { id: instanceId }))) return;
    _flowAction(instanceId, 'undeploy_flow');
  }, true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

async function _openFlowGraphTab(instanceId) {
  try {
    const graphUrl = '/chat/js/flow_graph.html?instance_id=' + encodeURIComponent(instanceId);
    const resp = await fetch(graphUrl, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    let html = await resp.text();
    const bootstrap = '<script>window.__PAWFLOW_FLOW_INSTANCE_ID=' + JSON.stringify(instanceId) + ';<\/script>\n';
    html = html.replace('<script type="module">', bootstrap + '<script type="module">');
    addBlobHtmlTab(instanceId, html);
  } catch (e) {
    addMsg('error', t('flowGraphOpenFailed', { error: e.message || e }));
  }
}

async function _openFlowTemplateGraphTab(templateId) {
  try {
    const graphUrl = '/chat/js/flow_graph.html?template_id=' + encodeURIComponent(templateId);
    const resp = await fetch(graphUrl, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    let html = await resp.text();
    const bootstrap = '<script>window.__PAWFLOW_FLOW_TEMPLATE_ID=' + JSON.stringify(templateId) + ';<\/script>\n';
    html = html.replace('<script type="module">', bootstrap + '<script type="module">');
    addBlobHtmlTab('template-' + templateId, html);
  } catch (e) {
    addMsg('error', t('flowGraphOpenFailed', { error: e.message || e }));
  }
}
function _showFlowStartDialog(instanceId, editOnly) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  const title = editOnly ? t('flowEditParameters') : t('flowStart');
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${escapeHtml(title)}: ${escapeHtml(instanceId)}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div><div style="color:#888;font-size:12px;">${escapeHtml(t('flowLoadingParameters'))}</div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  action$('get_flow_instance', { instance_id: instanceId }).subscribe({
    next: async (data) => {
      if (data.error) { panel.querySelector('div:last-child').innerHTML = `<div style="color:#e94560;">${data.error}</div>`; return; }
      let fieldsHtml = '';
      try {
        fieldsHtml = await _renderFlowDeploymentConfig(data);
      } catch (e) {
        panel.querySelector('div:last-child').innerHTML = '<div style="color:#e94560;">' + escapeHtml(t('error')) + ': ' + escapeHtml(e.message || e) + '</div>';
        return;
      }
      const btnLabel = editOnly ? t('contextSave') : t('flowStart');
      panel.querySelector('div:last-child').innerHTML = '<div id="flow-instance-config">' + fieldsHtml + '</div>'
        + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
          <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
          <button id="flowStartBtn" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(btnLabel)}</button>
        </div>`;
      document.getElementById('flowStartBtn').onclick = () => {
        let cfg;
        try {
          cfg = _collectFlowDeploymentConfig(document.getElementById('flow-instance-config'));
        } catch (e) {
          alert(t('invalidJsonInParameters', { error: e.message }));
          return;
        }
        action$('update_flow_params', {
          instance_id: instanceId,
          parameters: cfg.parameters,
          replace_parameters: true,
          service_overrides: cfg.service_overrides,
          service_configs: cfg.service_configs,
        }).subscribe({
          next: (d) => {
            if (d.error) { addMsg('error', d.error); return; }
            if (editOnly) {
              addMsg('system', t('flowConfigurationUpdated', { id: instanceId }));
              document.getElementById('resourceEditorOverlay').remove();
              loadResources();
            } else {
              _flowAction(instanceId, 'start_flow');
              document.getElementById('resourceEditorOverlay').remove();
            }
          },
          error: (e) => addMsg('error', e.message),
        });
      };
    },
    error: (e) => {
      panel.querySelector('div:last-child').innerHTML = '<div style="color:#e94560;">' + escapeHtml(t('error')) + ': ' + escapeHtml(e.message) + '</div>';
    },
  });
}

function _flowAction(instanceId, action) {
  action$(action, { instance_id: instanceId }).subscribe({
    next: (d) => {
      if (d.error) addMsg('error', d.error);
      else { addMsg('system', `${action.replace('_', ' ')}: ${instanceId}`); loadResources(); }
    },
    error: (e) => addMsg('error', e.message),
  });
}
