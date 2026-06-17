// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

function _flowFieldValueHtml(value) {
  if (value == null) return '';
  if (typeof value === 'object') return escapeHtml(JSON.stringify(value, null, 2));
  return escapeHtml(String(value));
}

function _flowFieldValue(schema, values, key) {
  if (values && values[key] != null) return values[key];
  const spec = schema[key] || {};
  return spec.default != null ? spec.default : '';
}

function _flowSchemaEntryFromValue(value) {
  if (typeof value === 'boolean') return { type: 'boolean', default: value };
  if (Number.isInteger(value)) return { type: 'integer', default: value };
  if (typeof value === 'number') return { type: 'float', default: value };
  if (value && typeof value === 'object') return { type: 'object', default: value };
  return { type: 'string', default: value == null ? '' : String(value) };
}

function _renderFlowSchemaFields(schema, values, cssClass, serviceId) {
  let html = '';
  for (const [key, spec0] of Object.entries(schema || {})) {
    const spec = spec0 || {};
    const type = spec.type || 'string';
    const value = _flowFieldValue(schema, values || {}, key);
    const dataAttrs = ' class="' + cssClass + '" data-key="' + escapeHtml(key)
      + '" data-type="' + escapeHtml(type) + '"'
      + (serviceId ? ' data-service-id="' + escapeHtml(serviceId) + '"' : '');
    html += '<div style="margin-bottom:8px;">'
      + '<label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(key)
      + (spec.required ? ' <span style="color:var(--pf-danger);">*</span>' : '')
      + _renderParamHelp(spec.description, key) + '</label>';
    if (type === 'boolean') {
      html += '<label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;"><input type="checkbox"'
        + dataAttrs + (value ? ' checked' : '') + ' style="accent-color:var(--pf-accent);">'
        + '<span style="color:var(--pf-text);font-size:12px;">' + escapeHtml(t('enabled')) + '</span></label>';
    } else if (type === 'select' && spec.options) {
      html += '<select' + dataAttrs + ' style="' + _svcInputStyle + '">';
      for (const opt0 of spec.options) {
        const optVal = typeof opt0 === 'object' ? opt0.value : opt0;
        const optLabel = typeof opt0 === 'object' ? (opt0.label || opt0.value) : opt0;
        html += '<option value="' + escapeHtml(String(optVal)) + '"'
          + (String(value) === String(optVal) ? ' selected' : '') + '>'
          + escapeHtml(String(optLabel)) + '</option>';
      }
      html += '</select>';
    } else if (type === 'textarea' || type === 'map' || type === 'object') {
      html += '<textarea' + dataAttrs + ' style="' + _svcInputStyle + 'min-height:80px;font-family:monospace;resize:vertical;">'
        + _flowFieldValueHtml(value) + '</textarea>';
    } else if (type === 'integer' || type === 'float') {
      html += '<input type="number"' + (type === 'float' ? ' step="any"' : '')
        + dataAttrs + ' value="' + _flowFieldValueHtml(value) + '" style="' + _svcInputStyle + 'width:140px;">';
    } else {
      html += '<input type="' + (spec.sensitive ? 'password' : 'text') + '"'
        + dataAttrs + ' value="' + _flowFieldValueHtml(value) + '" style="' + _svcInputStyle + '">';
    }
    html += '</div>';
  }
  return html;
}

function _readFlowConfigField(el) {
  const type = el.dataset.type || 'string';
  if (type === 'boolean') return el.checked;
  if (type === 'integer') return parseInt(el.value) || 0;
  if (type === 'float') return parseFloat(el.value) || 0;
  if (type === 'map' || type === 'object') return JSON.parse(el.value || '{}');
  return el.value;
}

function _collectFlowDeploymentConfig(root) {
  const parameters = {};
  root.querySelectorAll('.flow-param-field').forEach(el => {
    parameters[el.dataset.key] = _readFlowConfigField(el);
  });
  const service_overrides = {};
  const service_configs = {};
  root.querySelectorAll('.flow-service-card').forEach(card => {
    const sid = card.dataset.serviceId;
    const mode = (card.querySelector('.flow-service-mode') || {}).value || 'local';
    if (mode && mode !== 'local') {
      service_overrides[sid] = mode;
    } else {
      const cfg = {};
      card.querySelectorAll('.flow-service-param-field').forEach(el => {
        cfg[el.dataset.key] = _readFlowConfigField(el);
      });
      service_configs[sid] = cfg;
    }
  });
  return { parameters, service_overrides, service_configs };
}

function _onFlowServiceModeChange(sel) {
  const card = sel.closest('.flow-service-card');
  if (!card) return;
  const local = card.querySelector('.flow-service-local');
  if (local) local.style.display = (sel.value && sel.value !== 'local') ? 'none' : 'block';
}

async function _renderFlowDeploymentConfig(schemaData) {
  const paramsSchema = Object.assign({}, schemaData.parameters_schema || {});
  const paramValues = Object.assign(
    {}, schemaData.template_parameters || {}, schemaData.parameter_values || {}, schemaData.parameters || {}
  );
  for (const [key, value] of Object.entries(paramValues)) {
    if (!String(key).startsWith('_') && !paramsSchema[key]) paramsSchema[key] = _flowSchemaEntryFromValue(value);
  }
  let html = '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>';
  if (Object.keys(paramsSchema).length) {
    html += _renderFlowSchemaFields(paramsSchema, paramValues, 'flow-param-field');
  } else {
    html += '<div style="color:var(--pf-muted);font-size:12px;margin-bottom:8px;">' + escapeHtml(t('flowNoParameters')) + '</div>';
  }
  html += '</div>';

  const services = schemaData.services || {};
  if (Object.keys(services).length) {
    html += '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
      + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('services')) + '</div>';
    for (const [sid, svc] of Object.entries(services)) {
      const current = svc.override || 'local';
      let options = '<option value="local"' + (current === 'local' ? ' selected' : '') + '>' + escapeHtml(t('flowConfigureLocalService')) + '</option>';
      try {
        const listed = await rxjs.firstValueFrom(listServices$(svc.service_type || ''));
        for (const s of (listed.services || [])) {
          const ref = s.ref || (s.scope === 'global' ? 'global:' + s.service_id : s.service_id);
          const label = s.service_id + (s.scope ? ' [' + s.scope + ']' : '') + (s.provider ? ' - ' + s.provider : '');
          options += '<option value="' + escapeHtml(ref) + '"' + (current === ref ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
        }
      } catch (e) {}
      if (current && current !== 'local' && options.indexOf('value="' + escapeHtml(current) + '"') < 0) {
        options += '<option value="' + escapeHtml(current) + '" selected>' + escapeHtml(t('missingItem', { value: current })) + '</option>';
      }
      const localDisplay = current && current !== 'local' ? 'display:none;' : '';
      html += '<div class="flow-service-card" data-service-id="' + escapeHtml(sid) + '" style="background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:6px;padding:8px;margin-bottom:8px;">'
        + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
        + '<div style="color:var(--pf-text);font-size:12px;font-weight:600;">' + escapeHtml(sid) + '</div>'
        + '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(svc.service_type || '') + '</div></div>'
        + '<select class="flow-service-mode" onchange="_onFlowServiceModeChange(this)" style="' + _svcInputStyle + '">' + options + '</select>'
        + '<div class="flow-service-local" style="margin-top:8px;' + localDisplay + '">'
        + _renderFlowSchemaFields(svc.parameters_schema || {}, svc.parameter_values || {}, 'flow-service-param-field', sid)
        + '</div></div>';
    }
    html += '</div>';
  }
  return html;
}

async function showDeployFlowDialog(initialTemplateId) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:560px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(t('deployFlow'))}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div><div style="color:var(--pf-muted);font-size:12px;">${escapeHtml(t('loadingTemplates'))}</div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  try {
    const data = await rxjs.firstValueFrom(action$('list_available_flows', _withView({})));
    const templates = data.templates || [];
    if (!templates.length) {
      panel.querySelector('div:last-child').innerHTML = '<div style="color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('noFlowTemplates')) + '</div>';
      return;
    }
    let optionsHtml = templates.map(t => {
      const versionLabel = t.version ? ' v' + t.version : '';
      const scopeLabel = t.scope || 'independent';
      const selected = initialTemplateId && t.id === initialTemplateId ? ' selected' : '';
      return '<option value="' + escapeHtml(t.id) + '" data-scope="' + escapeHtml(scopeLabel) + '"' + selected + '>'
        + escapeHtml(t.name) + ' (' + escapeHtml(String(t.tasks_count)) + ' tasks)' + escapeHtml(versionLabel)
        + ' [' + escapeHtml(scopeLabel) + ']</option>';
    }).join('');
    const deployScopeOptions = (_isAdmin()
      ? '<option value="global">' + escapeHtml(t('global')) + '</option>'
      : '')
      + '<option value="user">' + escapeHtml(t('user')) + '</option>'
      + '<option value="conversation">' + escapeHtml(t('conversation')) + '</option>';
    panel.querySelector('div:last-child').innerHTML = `
      <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('template'))}</label>
        <select id="deploy-template" onchange="_onDeployTemplateChange()" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">${optionsHtml}</select></div>
      <div id="deploy-scope-info" style="margin-bottom:8px;font-size:11px;color:var(--pf-muted);"></div>
      <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('deployScope'))}</label>
        <select id="deploy-scope" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">
          ${deployScopeOptions}
        </select></div>
      <div id="deploy-config" style="margin-bottom:8px;color:var(--pf-muted);font-size:12px;">${escapeHtml(t('loadingDeploymentSchema'))}</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
        <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
        <button onclick="_submitDeployFlow()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('deploy'))}</button>
      </div>`;
    _onDeployTemplateChange();
  } catch (e) {
    panel.querySelector('div:last-child').innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(t('templatesLoadFailed', { error: e.message })) + '</div>';
  }
}
function _submitDeployFlow() {
  const templateId = document.getElementById('deploy-template').value;
  const scope = document.getElementById('deploy-scope').value;
  if (!confirm(t('flowDeployConfirm', { id: templateId, scope: scope }))) return;
  let cfg;
  try {
    cfg = _collectFlowDeploymentConfig(document.getElementById('deploy-config'));
  } catch (e) {
    alert(t('invalidJsonInParameters', { error: e.message }));
    return;
  }
  action$('deploy_flow', {
    template_id: templateId,
    scope,
    parameters: cfg.parameters,
    service_overrides: cfg.service_overrides,
    service_configs: cfg.service_configs,
  }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', t('flowDeployed', { id: d.instance_id, scope: scope })); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
}
async function _onDeployTemplateChange() {
  var sel = document.getElementById('deploy-template');
  var opt = sel.options[sel.selectedIndex];
  var flowScope = opt ? opt.getAttribute('data-scope') || 'independent' : 'independent';
  var info = document.getElementById('deploy-scope-info');
  var scopeSel = document.getElementById('deploy-scope');
  if (flowScope === 'conversation') {
    info.innerHTML = '<span style="color:var(--pf-warning);">' + escapeHtml(t('flowRequiresConversationContext')) + '</span>';
    scopeSel.value = 'conversation';
    scopeSel.disabled = true;
  } else if (flowScope === 'user') {
    info.innerHTML = '<span style="color:var(--pf-accent-2);">' + escapeHtml(t('flowRequiresUserContext')) + '</span>';
    scopeSel.disabled = false;
  } else {
    info.innerHTML = '<span style="color:var(--pf-success);">' + escapeHtml(t('flowIndependentNoDependencies')) + '</span>';
    scopeSel.disabled = false;
  }
  var config = document.getElementById('deploy-config');
  if (!config || !sel.value) return;
  config.innerHTML = '<div style="color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('loadingDeploymentSchema')) + '</div>';
  try {
    const schema = await rxjs.firstValueFrom(action$('get_flow_deploy_schema', { template_id: sel.value }));
    if (schema.error) { config.innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(schema.error) + '</div>'; return; }
    config.innerHTML = await _renderFlowDeploymentConfig(schema);
  } catch (e) {
    config.innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(t('deploymentSchemaLoadFailed', { error: e.message || e })) + '</div>';
  }
}


// ── Prompt use (click to paste) ─────────────────────────────────
