// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

function _showEditLimitsDialog(taskId) {
  // Fetch current task data
  action$('task_status', {}).subscribe(d => {
    const task = (d.tasks || []).find(t => t.task_id === taskId);
    if (!task) { addMsg('error', t('taskNotFound', { id: taskId })); return; }
    const overlay = document.createElement('div');
    overlay.id = 'resourceEditorOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:var(--pf-shadow);z-index:9999;display:flex;align-items:center;justify-content:center;';

    const panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:8px;padding:20px;min-width:340px;max-width:420px;color:var(--pf-text);';
    const _f = (id, label, val, ph) => `<div style="margin-bottom:8px;"><label style="font-size:11px;color:var(--pf-muted);">${label}</label><input id="${id}" value="${val||''}" placeholder="${ph}" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-size:12px;"/></div>`;
    panel.innerHTML = `<div style="font-weight:bold;margin-bottom:12px;">${escapeHtml(t('editLimitsTitle', { id: taskId }))}</div>`
      + _f('el-budget', t('maxBudget'), task.max_budget || '', '$5.00')
      + _f('el-turn', t('maxTurnTime'), task.timeout ? task.timeout+'s' : '', '5m')
      + _f('el-total', t('maxTotalTime'), task.max_total_time ? task.max_total_time+'s' : '', '1h')
      + _f('el-resched', t('maxReschedules'), task.max_reschedules || '', '50')
      + _f('el-maxiter', t('maxIterations'), task.max_iterations || '', '50')
      + `<div style="font-size:10px;color:var(--pf-muted);margin-bottom:8px;">${escapeHtml(t('currentTaskLimits', { cost: (task.total_cost||0).toFixed(4), reschedules: task.reschedule_count||0 }))}</div>`
      + `<div style="display:flex;gap:8px;justify-content:flex-end;"><button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button><button id="el-save" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextSave'))}</button></div>`;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    document.getElementById('el-save').onclick = () => {
      const params = { task_id: taskId };
      const bv = document.getElementById('el-budget').value.trim();
      const tv = document.getElementById('el-turn').value.trim();
      const ttv = document.getElementById('el-total').value.trim();
      const rv = document.getElementById('el-resched').value.trim();
      const mv = document.getElementById('el-maxiter').value.trim();
      if (bv) params.max_budget = bv;
      if (tv) params.max_turn_time = tv;
      if (ttv) params.max_total_time = ttv;
      if (rv) params.max_reschedules = parseInt(rv) || 0;
      if (mv) params.max_iterations = parseInt(mv) || 0;
      action$('edit_task', params).subscribe(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', t('taskLimitsUpdated', { changes: (data.changed||[]).join(', ') }));
        overlay.remove();
        loadResources();
      });
    };
  });
}

function _showTaskDefLog(defName) {
  // Show all task instances for this definition, with their logs
  action$('list_resources', {}).subscribe(d => {
    const instances = (d.all_tasks || []).filter(t => t.task_def_name === defName);
    if (!instances.length) { addMsg('system', t('noTaskInstancesForDefinition', { name: defName })); return; }
    let lines = instances.map(t => {
      const icon = t.status === 'active' ? '\u25B6' : t.status === 'paused' ? '\u23F8' : t.status === 'completed' ? '\u2705' : t.status === 'cancelled' ? '\u2718' : '\u26A0';
      return icon + ' ' + t.task_id + ' (' + t.agent + ') — ' + t.status + ' [' + t.iterations + '/' + t.max_iterations + ']';
    }).join('\n');
    addMsg('system', t('taskInstancesTitle', { name: defName, lines: lines }));
    // Also fetch logs for each instance
    for (const inst of instances) {
      action$('task_log', { name: inst.task_id }).subscribe(ld => {
        const log = ld.log || [];
        if (log.length) {
          const logLines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
          addMsg('system', t('logTitle', { id: inst.task_id, lines: logLines }));
        }
      });
    }
  });
}

function showServiceMenu(e, serviceId, scope, enabled) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  item('\u{1F441} ' + t('viewConfigMenu'), () => showServiceEditForm(serviceId, scope, true));
  if (_canEditScope(scope)) {
    item('\u270F ' + t('editWithEllipsis'), () => showServiceEditForm(serviceId, scope));
  }
  item(enabled ? '\u23F8 ' + t('blocked') : '\u25B6 ' + t('enabled'), () => {
    action$('toggle_service', { service_id: serviceId, scope, enabled: !enabled, conversation_id: conversationId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else { notifyServiceConfigurationChanged(); loadResources(); }
    });
  });
  if (_canEditScope(scope)) {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(sep);
    const normScope = scope === 'conv' ? 'conversation' : scope;
    const moveService = (toScope) => {
      const payload = { service_id: serviceId, from_scope: normScope, to_scope: toScope };
      if ((normScope === 'conversation' || toScope === 'conversation') && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
      action$('move_service_scope', payload, { skipConversationId: !(normScope === 'conversation' || toScope === 'conversation') }).subscribe(d => {
        if (d.error) addMsg('error', d.error);
        else { notifyServiceConfigurationChanged(); loadResources(); }
      });
    };
    if (normScope !== 'user') item('\u2191 ' + (normScope === 'conversation' ? 'Promote to user' : 'Demote to user'), () => moveService('user'));
    if (normScope !== 'conversation' && typeof conversationId !== 'undefined' && conversationId) item('\u2193 Move to conversation', () => moveService('conversation'));
    if (normScope !== 'global' && _isAdmin()) item('\u2191 Promote to global', () => moveService('global'));
    const sep2 = document.createElement('div');
    sep2.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(sep2);
    item('\u{1F5D1} ' + t('delete'), () => {
      if (!confirm(t('deleteServiceConfirm', { id: serviceId }))) return;
      action$('delete_service', { service_id: serviceId, scope, conversation_id: conversationId }).subscribe(d => {
        if (d.error) addMsg('error', d.error);
        else { addMsg('system', t('serviceDeleted', { id: serviceId })); notifyServiceConfigurationChanged(); loadResources(); }
      });
    }, true);
  }
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

// ── Service schema-based form helpers ─────────────────────────────
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
  if (readonly || !pdef || !pdef.fill_helper) return '';
  const helper = escapeAttr(JSON.stringify(pdef.fill_helper));
  const title = escapeAttr((pdef.fill_helper && pdef.fill_helper.label) || 'Fill');
  return '<button type="button" class="svc-param-fill" data-param="' + escapeAttr(pname)
    + '" data-helper="' + helper + '" onclick="_openParamFillHelper(this,event)"'
    + ' aria-label="Fill: ' + title + '" title="' + title + '" style="' + _svcFillStyle + '">[...]</button>';
}

function _renderHelpMarkdown(markdown) {
  if (typeof renderMarkdown === 'function') return renderMarkdown(markdown || '');
  return escapeHtml(markdown || '').replace(/\n/g, '<br>');
}

function _openParamHelpWindow(btn, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  document.querySelectorAll('.svc-help-window').forEach(el => el.remove());
  const title = btn.dataset.helpTitle || 'Parameter help';
  const markdown = btn.dataset.help || '';
  const win = document.createElement('div');
  win.className = 'svc-help-window';
  win.style.cssText = 'position:fixed;z-index:10050;width:min(560px,calc(100vw - 24px));max-height:min(460px,calc(100vh - 24px));display:flex;flex-direction:column;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:8px;box-shadow:0 12px 36px rgba(0,0,0,0.55);overflow:hidden;';
  win.innerHTML = '<div class="svc-help-titlebar" style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--pf-sidebar);border-bottom:1px solid var(--pf-border);cursor:move;user-select:none;">'
    + '<strong style="font-size:12px;color:var(--pf-text);flex:1;">' + escapeHtml(title) + '</strong>'
    + '<button type="button" class="svc-help-close" aria-label="Close" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;width:22px;height:22px;line-height:18px;cursor:pointer;">&times;</button>'
    + '</div><div class="svc-help-content" style="padding:10px 12px;overflow:auto;user-select:text;font-size:12px;line-height:1.45;white-space:normal;">'
    + _renderHelpMarkdown(markdown) + '</div>';
  document.body.appendChild(win);
  const content = win.querySelector('.svc-help-content');
  content.querySelectorAll('pre').forEach(pre => {
    pre.style.margin = '8px 0';
    pre.style.padding = '8px';
    pre.style.background = 'var(--pf-code-bg)';
    pre.style.border = '1px solid var(--pf-border)';
    pre.style.borderRadius = '6px';
    pre.style.overflow = 'auto';
    pre.style.userSelect = 'text';
  });
  content.querySelectorAll('code').forEach(code => {
    code.style.userSelect = 'text';
  });
  content.querySelectorAll('a').forEach(a => {
    a.style.color = 'var(--pf-accent-2)';
  });
  const rect = btn.getBoundingClientRect();
  const pad = 12;
  const left = Math.min(Math.max(pad, rect.left + 18), window.innerWidth - win.offsetWidth - pad);
  const top = Math.min(Math.max(pad, rect.bottom + 8), window.innerHeight - win.offsetHeight - pad);
  win.style.left = left + 'px';
  win.style.top = top + 'px';
  win.querySelector('.svc-help-close').onclick = () => win.remove();
  _makeParamHelpDraggable(win, win.querySelector('.svc-help-titlebar'));
}

async function _openParamFillHelper(btn, ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  let helper = {};
  try { helper = JSON.parse(btn.dataset.helper || '{}'); } catch (_) { helper = {}; }
  const parameter = btn.dataset.param || helper.parameter || '';
  const panel = btn.closest('#resourceEditorOverlay > div') || document.querySelector('#resourceEditorOverlay > div');
  let schema = {};
  try { schema = JSON.parse(panel?.dataset.schema || '{}'); } catch (_) { schema = {}; }
  const config = _collectSchemaValues(schema);
  document.querySelectorAll('.svc-fill-window').forEach(el => el.remove());
  const win = document.createElement('div');
  win.className = 'svc-fill-window';
  win.style.cssText = 'position:fixed;z-index:10055;width:min(620px,calc(100vw - 24px));max-height:min(520px,calc(100vh - 24px));display:flex;flex-direction:column;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:8px;box-shadow:0 12px 36px rgba(0,0,0,0.55);overflow:hidden;';
  win.innerHTML = '<div class="svc-fill-titlebar" style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--pf-sidebar);border-bottom:1px solid var(--pf-border);cursor:move;user-select:none;">'
    + '<strong style="font-size:12px;color:var(--pf-text);flex:1;">' + escapeHtml(helper.label || 'Fill') + '</strong>'
    + '<button type="button" class="svc-fill-close" aria-label="Close" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;width:22px;height:22px;line-height:18px;cursor:pointer;">&times;</button>'
    + '</div><div class="svc-fill-content" style="padding:10px 12px;overflow:auto;font-size:12px;line-height:1.4;color:var(--pf-text);">'
    + '<div style="color:var(--pf-muted);font-size:11px;">Loading...</div></div>';
  document.body.appendChild(win);
  const rect = btn.getBoundingClientRect();
  const pad = 12;
  win.style.left = Math.min(Math.max(pad, rect.left + 18), window.innerWidth - win.offsetWidth - pad) + 'px';
  win.style.top = Math.min(Math.max(pad, rect.bottom + 8), window.innerHeight - win.offsetHeight - pad) + 'px';
  win.querySelector('.svc-fill-close').onclick = () => win.remove();
  _makeParamHelpDraggable(win, win.querySelector('.svc-fill-titlebar'));
  const content = win.querySelector('.svc-fill-content');
  try {
    const data = await rxjs.firstValueFrom(action$('get_service_parameter_helper', {
      service_type: helper.service_type || '',
      parameter,
      config,
      conversation_id: conversationId,
    }));
    if (data.error) { content.innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(data.error) + '</div>'; return; }
    const values = data.values || [];
    let html = '';
    if (data.warning) html += '<div style="color:var(--pf-warning);border:1px solid color-mix(in srgb, var(--pf-warning) 45%, var(--pf-border));background:color-mix(in srgb, var(--pf-warning) 10%, var(--pf-panel));border-radius:6px;padding:7px;margin-bottom:8px;">' + escapeHtml(data.warning) + '</div>';
    if (!values.length) html += '<div style="color:var(--pf-muted);">No suggestions.</div>';
    for (let i = 0; i < values.length; i++) {
      const item = values[i] || {};
      const encoded = escapeAttr(JSON.stringify(item.value == null ? '' : item.value));
      html += '<div style="display:grid;grid-template-columns:1fr auto;gap:8px;align-items:start;border:1px solid var(--pf-border);border-radius:6px;padding:8px;margin-bottom:6px;background:var(--pf-sidebar);">'
        + '<div><div style="font-weight:600;color:var(--pf-text);word-break:break-word;">' + escapeHtml(item.label || item.value || '') + '</div>'
        + (item.description ? '<div style="color:var(--pf-muted);font-size:11px;margin-top:3px;word-break:break-word;">' + escapeHtml(item.description) + '</div>' : '') + '</div>'
        + '<button type="button" data-value="' + encoded + '" onclick="_applyParamFillSuggestion(' + _pfpJsArg(parameter) + ', this); this.closest(\'.svc-fill-window\').remove();" style="background:var(--pf-accent);color:var(--pf-bg);border:none;border-radius:4px;padding:5px 10px;cursor:pointer;font-size:12px;">Use</button>'
        + '</div>';
    }
    content.innerHTML = html;
  } catch (e) {
    content.innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(e.message) + '</div>';
  }
}

function _applyParamFillSuggestion(parameter, valueButton) {
  const el = document.getElementById('svc-p-' + parameter);
  if (!el) return;
  let value = '';
  try { value = JSON.parse(valueButton.dataset.value || '""'); } catch (_) { value = valueButton.dataset.value || ''; }
  if (typeof value === 'object') value = JSON.stringify(value, null, 2);
  if (el.type === 'checkbox') {
    el.checked = !!value;
  } else {
    el.value = String(value == null ? '' : value);
  }
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

function _makeParamHelpDraggable(win, handle) {
  if (!win || !handle) return;
  handle.addEventListener('mousedown', function(e) {
    if (e.target.closest('.svc-help-close')) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startLeft = parseFloat(win.style.left || '0');
    const startTop = parseFloat(win.style.top || '0');
    const move = function(ev) {
      const pad = 8;
      const nextLeft = Math.min(Math.max(pad, startLeft + ev.clientX - startX), window.innerWidth - win.offsetWidth - pad);
      const nextTop = Math.min(Math.max(pad, startTop + ev.clientY - startY), window.innerHeight - win.offsetHeight - pad);
      win.style.left = nextLeft + 'px';
      win.style.top = nextTop + 'px';
    };
    const up = function() {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
}

function _serviceCategoryLabel(category) {
  const key = 'serviceCategory.' + (category || 'other');
  const label = t(key);
  return label === key ? (category || 'Other') : label;
}

function _renderServiceTypeOptions(serviceTypes) {
  const groups = [];
  const byCategory = {};
  for (const st of serviceTypes) {
    const category = st.category || 'other';
    if (!byCategory[category]) {
      byCategory[category] = [];
      groups.push(category);
    }
    byCategory[category].push(st);
  }
  let html = '';
  for (const category of groups) {
    html += '<optgroup label="' + escapeHtml(_serviceCategoryLabel(category)) + '">';
    for (const st of byCategory[category]) {
      const label = (st.name || st.type) + (st.description ? ' - ' + st.description : '');
      html += '<option value="' + escapeHtml(st.type) + '">' + escapeHtml(label) + '</option>';
    }
    html += '</optgroup>';
  }
  return html;
}

function _installSchemaForServiceType(serviceType, schema) {
  schema = Object.assign({}, schema || {});
  if (serviceType === 'relay') {
    delete schema.token;
  }
  return schema;
}

function _renderSchemaFields(schema, values, readonly) {
  let html = '';
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  for (const [pname, pdef] of Object.entries(schema)) {
    if (pdef.internal || pdef.server_only || pdef.hidden || pdef.type === 'hidden') continue;
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
    } else if (ptype === 'service_ref') {
      const st = (pdef.service_type || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const pf = (pdef.provider_field || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const fp = (pdef.provider || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const aliases = JSON.stringify(pdef.provider_aliases || {}).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      if (fillHelper) html += '<div style="display:flex;gap:4px;align-items:flex-start;">';
      html += '<select id="svc-p-' + pname + '" data-service-ref="1" data-service-type="' + st + '" data-provider-field="' + pf + '" data-provider="' + fp + '" data-provider-aliases=\'' + aliases + '\' data-current="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + (fillHelper ? 'flex:1;min-width:0;' : '') + '">';
      html += '<option value="' + escaped + '">' + (escaped || '(auto)') + '</option>';
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

function _serviceRefProviderMatches(serviceProvider, wantedProvider, aliases) {
  const canonical = (provider) => {
    provider = String(provider || '').trim();
    return (aliases && aliases[provider]) || provider;
  };
  return !wantedProvider || canonical(serviceProvider) === canonical(wantedProvider);
}

async function _populateServiceRefs(container) {
  const refs = Array.from(container.querySelectorAll('select[data-service-ref="1"]'));
  for (const sel of refs) {
    const serviceType = sel.dataset.serviceType || '';
    const providerField = sel.dataset.providerField || '';
    const providerEl = providerField ? container.querySelector('#svc-p-' + providerField) : null;
    const wantedProvider = (sel.dataset.provider || '') || (providerEl ? providerEl.value : '');
    const current = sel.value || sel.dataset.current || '';
    let aliases = {};
    try { aliases = JSON.parse(sel.dataset.providerAliases || '{}'); } catch (_) { aliases = {}; }
    try {
      const data = await rxjs.firstValueFrom(listServices$(serviceType));
      const services = (data.services || []).filter(s => _serviceRefProviderMatches(s.provider, wantedProvider, aliases));
      let html = '<option value="">(auto)</option>';
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
}

function _collectSchemaValues(schema) {
  const config = {};
  for (const [pname, pdef] of Object.entries(schema)) {
    if (pdef.internal || pdef.server_only || pdef.hidden || pdef.type === 'hidden') continue;
    const el = document.getElementById('svc-p-' + pname);
    if (!el) continue;
    const wrapper = el.closest('.svc-field');
    if (wrapper && wrapper.style.display === 'none') continue;
    const ptype = pdef.type || 'string';
    if (ptype === 'boolean') {
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

function _renderServiceActions(actions, serviceId, scope) {
  if (!actions || !actions.length) return '';
  scope = scope || '';
  let html = '<div class="svc-actions" style="margin-top:12px;padding-top:8px;border-top:1px solid var(--pf-border);">';
  for (const a of actions) {
    const whenAttr = a.when ? ' data-action-when=\'' + JSON.stringify(a.when).replace(/'/g, '&#39;') + '\'' : '';
    html += '<button type="button" onclick="_executeServiceAction(' + _pfpJsArg(a.id) + ',' + _pfpJsArg(serviceId) + ',' + _pfpJsArg(a.flow || 'simple') + ',' + _pfpJsArg(a.server_action || '') + ',' + _pfpJsArg(scope) + ')"'
      + whenAttr + ' style="background:color-mix(in srgb, var(--pf-accent) 14%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);border-radius:4px;padding:6px 12px;cursor:pointer;font-size:12px;margin-right:8px;">'
      + escapeHtml(a.icon || '') + ' ' + escapeHtml(a.label || a.id) + '</button>';
  }
  html += '</div>';
  return html;
}

// -- Slash command handlers for claude login --

