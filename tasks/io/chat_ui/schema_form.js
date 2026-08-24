// Generic schema-driven form renderer (extracted from resources_service_dialogs.js).
// ONE renderer for every parameter form in PawFlow: service editor, service
// login/templates, the Flow Editor properties drawer (tasks), flow parameters
// and subflow parameter mapping. Standalone: it also runs inside the Flow
// Graph page (no chat shell), so every shell helper it needs has a fallback.
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

if (typeof escapeHtml !== 'function') {
  window.escapeHtml = function (s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  };
}
if (typeof escapeAttr !== 'function') { window.escapeAttr = function (s) { return escapeHtml(s); }; }
if (typeof t !== 'function') { window.t = function (key) { return key === 'showHide' ? 'Show / hide' : key; }; }
if (typeof _togglePwdVis !== 'function') {
  window._togglePwdVis = function (id) {
    const el = document.getElementById(id);
    if (el) el.type = el.type === 'password' ? 'text' : 'password';
  };
}
if (typeof _openParamHelpWindow !== 'function') {
  window._openParamHelpWindow = function (btn, ev) {
    if (ev) { ev.preventDefault(); ev.stopPropagation(); }
    window.alert((btn.dataset.helpTitle || 'Help') + '\n\n' + (btn.dataset.help || ''));
  };
}

// Service listing used by service_ref fields. The chat shell provides
// listServices$ (rxjs); other hosts install their own lister:
//   window._schemaFormListServices = async (serviceType) => ({ services: [...] })
async function _schemaFormServices(serviceType) {
  if (typeof window._schemaFormListServices === 'function') return window._schemaFormListServices(serviceType);
  if (typeof listServices$ === 'function' && typeof rxjs !== 'undefined') return rxjs.firstValueFrom(listServices$(serviceType));
  return { services: [] };
}

const _svcInputStyle = 'width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-size:12px;';
const _svcLabelStyle = 'color:var(--pf-muted);font-size:11px;';
const _svcHelpStyle = 'display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:50%;border:1px solid var(--pf-border);color:var(--pf-muted);font-size:10px;line-height:14px;margin-left:5px;cursor:pointer;background:var(--pf-sidebar);font-weight:700;vertical-align:middle;padding:0;';
const _svcFillStyle = 'display:inline-flex;align-items:center;justify-content:center;width:34px;min-width:34px;height:28px;border-radius:4px;border:1px solid var(--pf-accent);color:var(--pf-accent);font-size:11px;line-height:14px;cursor:pointer;background:var(--pf-sidebar);font-weight:700;padding:0;margin-top:2px;';

function _renderParamHelp(description, label) {
  if (!description) return '';
  const help = escapeAttr(description);
  const title = escapeAttr(label || 'Parameter help');
  return '<button type="button" class="svc-param-help" data-help-title="' + title
    + '" data-help="' + help + '" onclick="_openParamHelpWindow(this,event)"'
    + ' aria-label="Help: ' + title + '" style="' + _svcHelpStyle + '">?</button>';
}

function _renderParamFillHelper(pdef, pname, readonly) {
  // Fill helpers call the chat shell (service parameter helper action).
  if (readonly || !pdef || !pdef.fill_helper || typeof _openParamFillHelper !== 'function') return '';
  const helper = escapeAttr(JSON.stringify(pdef.fill_helper));
  const title = escapeAttr((pdef.fill_helper && pdef.fill_helper.label) || 'Fill');
  return '<button type="button" class="svc-param-fill" data-param="' + escapeAttr(pname)
    + '" data-helper="' + helper + '" onclick="_openParamFillHelper(this,event)"'
    + ' aria-label="Fill: ' + title + '" title="' + title + '" style="' + _svcFillStyle + '">[...]</button>';
}

function _renderSchemaFields(schema, values, readonly) {
  let html = '';
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  for (const [pname, pdef] of Object.entries(schema || {})) {
    if (!pdef || pdef.internal || pdef.server_only || pdef.hidden || pdef.type === 'hidden') continue;
    const val = (values && values[pname] != null) ? values[pname] : (pdef.default != null ? pdef.default : '');
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    const label = escapeHtml(pdef.label || pname);
    const req = pdef.required ? ' data-required="1"' : '';
    const fillHelper = _renderParamFillHelper(pdef, pname, readonly);
    html += '<div class="svc-field" data-field="' + pname + '"' + req + ' style="margin-bottom:8px;">';
    html += '<label style="' + _svcLabelStyle + '">' + label
      + (pdef.required ? ' <span class="svc-req" style="color:var(--pf-danger)">*</span>' : '')
      + _renderParamHelp(pdef.description, pdef.label || pname) + '</label>';
    const ptype = pdef.type || 'string';
    if (ptype === 'boolean') {
      html += '<label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;"><input id="svc-p-' + pname + '" type="checkbox"' + (val ? ' checked' : '') + dis + ' style="accent-color:var(--pf-accent);"/> <span style="color:var(--pf-text);font-size:12px;">Enabled</span></label>';
    } else if (ptype === 'select' && pdef.options) {
      if (fillHelper) html += '<div style="display:flex;gap:4px;align-items:flex-start;">';
      html += '<select id="svc-p-' + pname + '"' + dis + ' style="' + _svcInputStyle + roS + (fillHelper ? 'flex:1;min-width:0;' : '') + '">';
      for (const opt of pdef.options) {
        html += '<option value="' + opt + '"' + (String(val) === String(opt) ? ' selected' : '') + '>' + opt + '</option>';
      }
      html += '</select>';
      if (fillHelper) html += fillHelper + '</div>';
    } else if (ptype === 'service_ref_list') {
      const list = Array.isArray(val) ? val : [];
      const st = escapeHtml(pdef.service_type || '');
      html += '<div id="svc-p-' + pname + '" data-service-ref-list="1" data-service-type="' + st + '"' + (readonly ? ' data-readonly="1"' : '') + '>';
      html += '<div class="svc-ref-list-rows">';
      list.forEach((item, index) => { html += _renderServiceRefListRow(item, index, readonly); });
      html += '</div>';
      if (!readonly) html += '<button type="button" class="svc-ref-list-add" style="margin-top:5px;">+ Add candidate</button>';
      html += '</div>';
    } else if (ptype === 'service_ref') {
      const st = (pdef.service_type || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const pf = (pdef.provider_field || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const fp = (pdef.provider || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const aliases = JSON.stringify(pdef.provider_aliases || {}).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      if (fillHelper) html += '<div style="display:flex;gap:4px;align-items:flex-start;">';
      html += '<select id="svc-p-' + pname + '" data-service-ref="1" data-service-type="' + st + '" data-service-required="' + (pdef.required ? '1' : '0') + '" data-provider-field="' + pf + '" data-provider="' + fp + '" data-provider-aliases=\'' + aliases + '\' data-current="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + (fillHelper ? 'flex:1;min-width:0;' : '') + '">';
      html += '<option value="' + escaped + '">' + (escaped || (pdef.required ? t('workflowServiceSelect') : t('workflowServiceDisabled'))) + '</option>';
      html += '</select>';
      if (fillHelper) html += fillHelper + '</div>';
    } else if (ptype === 'textarea' || ptype === 'map' || ptype === 'object' || ptype === 'json') {
      const tval = (ptype === 'map' || ptype === 'object' || ptype === 'json') && typeof val === 'object' ? JSON.stringify(val, null, 2) : escaped;
      if (fillHelper) html += '<div style="display:flex;gap:4px;align-items:flex-start;">';
      html += '<textarea id="svc-p-' + pname + '"' + dis + ' style="' + _svcInputStyle + roS + 'min-height:80px;font-family:monospace;resize:vertical;' + (fillHelper ? 'flex:1;min-width:0;' : '') + '">' + tval + '</textarea>';
      if (fillHelper) html += fillHelper + '</div>';
    } else if (ptype === 'integer' || ptype === 'float') {
      if (fillHelper) html += '<div style="display:flex;gap:4px;align-items:flex-start;">';
      html += '<input id="svc-p-' + pname + '" type="number"' + (ptype === 'float' ? ' step="any"' : '') + ' value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + 'width:120px;"/>';
      if (fillHelper) html += fillHelper + '</div>';
    } else if (pdef.sensitive) {
      html += '<div style="display:flex;gap:4px;align-items:center;">'
        + '<input id="svc-p-' + pname + '" type="password" value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + 'flex:1;min-width:0;"/>'
        + '<button type="button" onclick="_togglePwdVis(\'svc-p-' + pname + '\',this)" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px;" title="' + escapeHtml(t('showHide')) + '">\u{1F441}</button>'
        + fillHelper
        + '</div>';
    } else {
      if (fillHelper) html += '<div style="display:flex;gap:4px;align-items:flex-start;">';
      html += '<input id="svc-p-' + pname + '" type="text" value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + (fillHelper ? 'flex:1;min-width:0;' : '') + '"/>';
      if (fillHelper) html += fillHelper + '</div>';
    }
    html += '</div>';
  }
  return html;
}

function _renderServiceRefListRow(item, index, readonly) {
  item = item || {};
  const dis = readonly ? ' disabled' : '';
  const id = escapeHtml(item.service_id || '');
  const priority = Number.isFinite(Number(item.priority)) ? Number(item.priority) : ((index + 1) * 10);
  const checked = item.enabled !== false ? ' checked' : '';
  return '<div class="svc-ref-list-row" draggable="' + (!readonly) + '" data-index="' + index + '" style="display:grid;grid-template-columns:minmax(140px,1fr) 82px 70px 28px;gap:5px;align-items:center;margin:4px 0;">'
    + '<select data-candidate-service data-current="' + id + '"' + dis + ' style="' + _svcInputStyle + '"><option value="' + id + '">' + (id || '(select)') + '</option></select>'
    + '<input data-candidate-priority type="number" value="' + priority + '"' + dis + ' title="Priority" style="' + _svcInputStyle + '"/>'
    + '<label style="font-size:11px;color:var(--pf-muted);"><input data-candidate-enabled type="checkbox"' + checked + dis + '> enabled</label>'
    + (readonly ? '<span></span>' : '<button type="button" data-candidate-remove title="Remove">\u00d7</button>')
    + '</div>';
}

function _wireServiceRefLists(container) {
  container.querySelectorAll('[data-service-ref-list="1"]').forEach(list => {
    const rows = list.querySelector('.svc-ref-list-rows');
    const readonly = list.dataset.readonly === '1';
    const wireRows = () => {
      rows.querySelectorAll('[data-candidate-remove]').forEach(btn => btn.onclick = () => { btn.closest('.svc-ref-list-row').remove(); });
      let dragged = null;
      rows.querySelectorAll('.svc-ref-list-row').forEach(row => {
        row.ondragstart = () => { dragged = row; };
        row.ondragover = e => e.preventDefault();
        row.ondrop = e => { e.preventDefault(); if (dragged && dragged !== row) rows.insertBefore(dragged, row); };
      });
    };
    if (!readonly) list.querySelector('.svc-ref-list-add').onclick = () => {
      rows.insertAdjacentHTML('beforeend', _renderServiceRefListRow({}, rows.children.length, false));
      _populateServiceRefs(container); wireRows();
    };
    wireRows();
  });
}

function _serviceRefProviderMatches(serviceProvider, wantedProvider, aliases) {
  const canonical = (provider) => {
    provider = String(provider || '').trim();
    return (aliases && aliases[provider]) || provider;
  };
  return !wantedProvider || canonical(serviceProvider) === canonical(wantedProvider);
}

async function _populateServiceRefs(container) {
  _wireServiceRefLists(container);
  const refs = Array.from(container.querySelectorAll('select[data-service-ref="1"]'));
  for (const sel of refs) {
    const serviceType = sel.dataset.serviceType || '';
    const required = sel.dataset.serviceRequired === '1';
    const providerField = sel.dataset.providerField || '';
    const providerEl = providerField ? container.querySelector('#svc-p-' + providerField) : null;
    const wantedProvider = (sel.dataset.provider || '') || (providerEl ? providerEl.value : '');
    const current = sel.value || sel.dataset.current || '';
    let aliases = {};
    try { aliases = JSON.parse(sel.dataset.providerAliases || '{}'); } catch (_) { aliases = {}; }
    try {
      const data = await _schemaFormServices(serviceType);
      const services = (data.services || []).filter(s => _serviceRefProviderMatches(s.provider, wantedProvider, aliases));
      let html = '<option value="">' + escapeHtml(t(required ? 'workflowServiceSelect' : 'workflowServiceDisabled')) + '</option>';
      for (const s of services) {
        const id = String(s.service_id || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const label = id + (s.scope ? ' [' + s.scope + ']' : '');
        html += '<option value="' + id + '">' + label + '</option>';
      }
      sel.innerHTML = html;
      sel.value = current;
      if (current && sel.value !== current) {
        sel.insertAdjacentHTML('afterbegin', '<option value="' + current + '">' + current + ' (missing)</option>');
        sel.value = current;
      }
      if (providerEl && !providerEl.dataset.serviceRefListener) {
        providerEl.dataset.serviceRefListener = '1';
        providerEl.addEventListener('change', () => _populateServiceRefs(container));
      }
    } catch (e) {
      // Keep the raw current option if service listing fails.
    }
  }
  for (const list of container.querySelectorAll('[data-service-ref-list="1"]')) {
    try {
      const data = await _schemaFormServices(list.dataset.serviceType || '');
      const services = data.services || [];
      list.querySelectorAll('select[data-candidate-service]').forEach(sel => {
        const current = sel.value || sel.dataset.current || '';
        sel.innerHTML = '<option value="">(select)</option>' + services.map(s => {
          const id = escapeHtml(String(s.service_id || ''));
          const label = id + (s.scope ? ' [' + escapeHtml(s.scope) + ']' : '');
          return '<option value="' + id + '">' + label + '</option>';
        }).join('');
        sel.value = current;
        if (current && sel.value !== current) {
          sel.insertAdjacentHTML('afterbegin', '<option value="' + escapeHtml(current) + '">' + escapeHtml(current) + ' (missing)</option>');
          sel.value = current;
        }
      });
    } catch (_) {}
  }
}

// `root` scopes the lookup to one form (several forms may coexist on a page).
function _collectSchemaValues(schema, root) {
  const scope = root || document;
  const byId = (id) => (scope === document ? document.getElementById(id) : scope.querySelector('#' + CSS.escape(id)));
  const config = {};
  for (const [pname, pdef] of Object.entries(schema || {})) {
    if (!pdef || pdef.internal || pdef.server_only || pdef.hidden || pdef.type === 'hidden') continue;
    const el = byId('svc-p-' + pname);
    if (!el) continue;
    const wrapper = el.closest('.svc-field');
    if (wrapper && wrapper.style.display === 'none') continue;
    const ptype = pdef.type || 'string';
    if (ptype === 'service_ref_list') {
      config[pname] = Array.from(el.querySelectorAll('.svc-ref-list-row')).map((row, index) => ({
        service_id: row.querySelector('[data-candidate-service]').value,
        priority: parseInt(row.querySelector('[data-candidate-priority]').value, 10) || ((index + 1) * 10),
        weight: 1.0,
        enabled: row.querySelector('[data-candidate-enabled]').checked,
      }));
    } else if (ptype === 'boolean') {
      config[pname] = el.checked;
    } else if (ptype === 'integer') {
      config[pname] = parseInt(el.value) || 0;
    } else if (ptype === 'float') {
      config[pname] = parseFloat(el.value) || 0;
    } else if (ptype === 'map' || ptype === 'object' || ptype === 'json') {
      try { config[pname] = JSON.parse(el.value || '{}'); } catch { config[pname] = el.value; }
    } else {
      config[pname] = el.value;
    }
  }
  return config;
}

function _applyRules(container, rules, actions, serviceId) {
  rules = rules || [];
  actions = actions || [];
  const getVal = (name) => {
    const el = container.querySelector('#svc-p-' + name);
    if (!el) return null;
    return el.type === 'checkbox' ? String(el.checked) : el.value;
  };
  const _matchWhen = (when) => Object.entries(when).every(([field, values]) =>
    Array.isArray(values) ? values.includes(getVal(field)) : getVal(field) === values
  );

  const apply = () => {
    // Reset: all fields visible, none required
    container.querySelectorAll('.svc-field').forEach(f => {
      f.style.display = '';
      const lbl = f.querySelector('label');
      if (lbl) {
        lbl.querySelector('.svc-req')?.remove();
        if (f.dataset.required === '1') {
          lbl.insertAdjacentHTML('beforeend', ' <span class="svc-req" style="color:var(--pf-danger)">*</span>');
        }
      }
    });
    // Evaluate rules in order
    for (const rule of rules) {
      if (!_matchWhen(rule.when)) continue;
      for (const [field, effects] of Object.entries(rule.set || {})) {
        const wrapper = container.querySelector('[data-field="' + field + '"]');
        if (!wrapper) continue;
        if (effects.visible === false) wrapper.style.display = 'none';
        if (effects.visible === true) wrapper.style.display = '';
        if (effects.required) {
          const lbl = wrapper.querySelector('label');
          if (lbl && !lbl.querySelector('.svc-req'))
            lbl.insertAdjacentHTML('beforeend', ' <span class="svc-req" style="color:var(--pf-danger)">*</span>');
        }
        if (effects.default !== undefined) {
          const input = wrapper.querySelector('input,select,textarea');
          if (input && !input.value) input.value = effects.default;
        }
        if (effects.options) {
          const sel = wrapper.querySelector('select');
          if (sel) {
            const cur = sel.value;
            sel.innerHTML = effects.options.map(o =>
              '<option value="' + o + '"' + (o === cur ? ' selected' : '') + '>' + o + '</option>').join('');
          }
        }
      }
    }
    // Show/hide action buttons based on when conditions
    container.querySelectorAll('[data-action-when]').forEach(btn => {
      try {
        const when = JSON.parse(btn.dataset.actionWhen);
        btn.style.display = _matchWhen(when) ? '' : 'none';
      } catch { btn.style.display = ''; }
    });
  };

  // Listen to trigger fields
  const triggers = new Set(rules.flatMap(r => Object.keys(r.when)));
  if (actions) actions.forEach(a => { if (a.when) Object.keys(a.when).forEach(k => triggers.add(k)); });
  triggers.forEach(name => {
    const el = container.querySelector('#svc-p-' + name);
    if (el) el.addEventListener('change', apply);
  });
  apply();
}

// Public, prefix-free names (the underscored ones stay for existing callers).
function renderSchemaFields(schema, values, options) {
  return _renderSchemaFields(schema, values, !!(options && options.readonly));
}
function collectSchemaValues(schema, root) { return _collectSchemaValues(schema, root); }
function applySchemaRules(container, rules, actions) { return _applyRules(container, rules, actions); }
function populateServiceRefs(container) { return _populateServiceRefs(container); }

// Classic-script export used by standalone hosts such as flow_graph.html.
// The chat shell keeps calling the established underscored functions.
window.PawFlowSchemaForm = {
  renderSchemaFields,
  collectSchemaValues,
  applySchemaRules,
  populateServiceRefs,
};
