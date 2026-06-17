// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

async function showResourceCreator(rtype) {
  if (rtype === '_flow') { showDeployFlowDialog(); return; }
  if (rtype === '_svc') { showServiceInstallForm(); return; }
  if (rtype === 'mcp') await _loadResourceRelayOptions();
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
  const createAssignBtn = (rtype === 'task_def' || rtype === 'skill')
    ? '<button onclick="_saveResourceCreate(\'' + rtype + '\', true)" style="background:color-mix(in srgb, var(--pf-accent) 16%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('create')) + ' + ' + escapeHtml(t('assign')) + '</button>'
    : '';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(t('newResourceTitle', { type: rtype === '_tool' ? t('tool') : rtype }))}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, {}, true)
    + _targetOwnerFieldHtml('res-target-owner')
    + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
    ${createAssignBtn}
    <button onclick="_saveResourceCreate('${rtype}')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('create'))}</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  _populateTargetOwnerField('res-target-owner');
  // Populate skills picker if present (empty selection for new)
  var skPicker = panel.querySelector('[data-type="skills_picker"]');
  if (skPicker) _loadSkillsPicker(skPicker, [], false);
  var saWidget = panel.querySelector('[data-type="skill_assets"]');
  if (saWidget) _renderSkillAssets(saWidget);
}

function _saveResourceCreate(rtype, assignAfterCreate) {
  const nameEl = document.getElementById('res-name');
  const scopeEl = document.getElementById('res-scope');
  const name = (nameEl && nameEl.value || '').trim();
  const scope = scopeEl ? scopeEl.value : 'user';
  if (!name) { alert(t('nameRequired')); return; }
  if (rtype === 'skill' && (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(name)
      || name.length > 64
      || name.indexOf('anthropic') >= 0 || name.indexOf('claude') >= 0)) {
    alert(t('skillNameInvalid'));
    return;
  }
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    if (type === 'skills_picker') { data[key] = _collectSkillsPicker(key) || []; continue; }
    if (type === 'params_editor') { const p = _collectParams(key); if (p) data[key] = p; continue; }
    if (type === 'skill_assets') { const pf = _collectSkillAssets(key); if (pf) data[key] = pf; continue; }
    const el = document.getElementById('res-' + key);
    if (el) {
      if (type === 'number') data[key] = parseInt(el.value) || 0;
      else if (type === 'checkbox') data[key] = !!el.checked;
      else if (type === 'json') {
        try { data[key] = el.value.trim() ? JSON.parse(el.value) : (key === 'args' ? [] : {}); }
        catch(e) { alert(t('fieldMustBeValidJson', { field: key })); return; }
      } else if (key === 'allowed-tools') {
        data[key] = el.value.split(/[,\s]+/).filter(Boolean);
      } else data[key] = el.value;
    }
  }
  // Dynamic tools use a dedicated action (CreateToolHandler pipeline)
  if (rtype === '_tool') {
    let params = {};
    try { params = data.parameters ? JSON.parse(data.parameters) : {}; } catch(e) { alert(t('parametersMustBeValidJson')); return; }
    action$('create_dynamic_tool', {
      tool_name: name, tool_description: data.tool_description || '',
      parameters: params, code: data.code || ''
    }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else { addMsg('system', t('toolCreated', { name: name })); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
    });
    return;
  }
  const payload = { resource_type: rtype, name, scope, data };
  if (scope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  // Admin creating on behalf of another user (user scope only — global has no
  // owner; conversation scope targets the current conv which the caller owns).
  if (scope === 'user') {
    const _tgt = _targetOwnerValue('res-target-owner');
    if (_tgt) payload.target_user_id = _tgt;
  }
  function _submit(force) {
    const p = force ? Object.assign({}, payload, { force: true }) : payload;
    action$('create_resource', p, { skipConversationId: scope !== 'conversation' }).subscribe(d => {
      // The user has the final word: a blocked review comes back as
      // requires_confirmation — show the findings and offer a forced rerun.
      if (d && d.requires_confirmation) {
        _showSkillReviewConfirm(d.review, d.message, function() { _submit(true); });
        return;
      }
      if (d.error) addMsg('error', d.error);
      else {
        addMsg('system', t('resourceCreated', { type: rtype, name: name }));
        document.getElementById('resourceEditorOverlay').remove();
        loadResources();
        if (assignAfterCreate && rtype === 'task_def') {
          setTimeout(function() { _showAssignDialog(name); }, 0);
        } else if (assignAfterCreate && rtype === 'skill') {
          setTimeout(function() { _showSkillAssignDialog(name); }, 0);
        }
      }
    });
  }
  _submit(false);
}

function _removeAgentFromConv(name) {
  var convAgents = document.querySelectorAll('#res-section-agent > div');
  if (convAgents.length <= 1) {
    if (!confirm(t('removeLastAgentConfirm'))) return;
  }
  cmdResourceAction('remove_agent_from_conv', {name: name, conversation_id: conversationId})
    .then(loadResources);
}

async function showAddAgentToConvDialog(presetDefinition) {
  var existing = document.getElementById('resourceEditorOverlay');
  if (existing) existing.remove();
  var overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  var panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = '<p style="color:var(--pf-text);font-weight:600;">' + escapeHtml(t('addAgentToConversation')) + '</p><p style="color:var(--pf-muted);">' + escapeHtml(t('loading')) + '</p>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  try {
    var data = await rxjs.firstValueFrom(action$('list_repo_agents', {}));
    var svcData = await rxjs.firstValueFrom(listServices$('llmConnection'));
    var definitions = data.agents || [];
    var llmServices = (svcData.services || []).filter(function(s) { return s.enabled; });
    var selectedDef = null;
    panel.innerHTML = '';

    var header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';
    header.innerHTML = '<strong style="color:var(--pf-text);">' + escapeHtml(t('addAgentToConversation')) + '</strong>';
    var closeBtn = document.createElement('button');
    closeBtn.textContent = '\u00d7';
    closeBtn.style.cssText = 'background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;';
    closeBtn.onclick = function() { overlay.remove(); };
    header.appendChild(closeBtn);
    panel.appendChild(header);

    // Definition selector
    var defLabel = document.createElement('label');
    defLabel.style.cssText = 'color:var(--pf-muted);font-size:11px;';
    defLabel.textContent = t('definitionTemplate');
    panel.appendChild(defLabel);
    var defSelect = document.createElement('select');
    defSelect.style.cssText = 'width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin:4px 0 12px;';
    defSelect.innerHTML = '<option value="">' + escapeHtml(t('selectDefinitionOption')) + '</option>'
      + definitions.map(function(d) {
        var sel = (presetDefinition && d.name === presetDefinition) ? ' selected' : '';
        return '<option value="' + escapeHtml(d.name) + '"' + sel + '>' + escapeHtml(d.name)
          + (d.description ? ' \u2014 ' + escapeHtml(d.description) : '') + '</option>';
      }).join('');
    panel.appendChild(defSelect);
    if (presetDefinition && definitions.some(function(d) { return d.name === presetDefinition; })) {
      selectedDef = presetDefinition;
      // _renderForm defined below; fire after selectedDef set
      setTimeout(function() { _renderForm(); }, 0);
    }

    // Form area (rendered when definition is selected)
    var formArea = document.createElement('div');
    formArea.id = '_addAgentForm';
    panel.appendChild(formArea);

    function _guessLlm(name) {
      for (var i = 0; i < llmServices.length; i++) {
        if (llmServices[i].service_id === name + '_llm_service') return llmServices[i].service_id;
        if (llmServices[i].service_id === name + '_llm') return llmServices[i].service_id;
      }
      return llmServices.length ? llmServices[0].service_id : '';
    }

    function _renderForm() {
      formArea.innerHTML = '';
      if (!selectedDef) return;
      var def = definitions.find(function(d) { return d.name === selectedDef; });
      if (!def) return;
      var paramSchema = def.parameters || {};
      var paramKeys = Object.keys(paramSchema);
      var svcOpts = llmServices.map(function(s) {
        var sel = s.service_id === _guessLlm(selectedDef) ? ' selected' : '';
        return '<option value="' + escapeHtml(s.service_id) + '"' + sel + '>'
          + escapeHtml(s.service_id) + '</option>';
      }).join('');
      var html = '<div style="padding:10px;border:1px solid var(--pf-border);border-radius:4px;background:var(--pf-code-bg);">';
      // Instance name
      html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('instanceNameRequired')) + '</label>'
        + '<input id="_addInstName" value="' + escapeHtml(selectedDef) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
      // LLM Service
      html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('llmServiceRequired')) + '</label>'
        + '<select id="_addLlm" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">'
        + svcOpts + '</select></div>';
      // Params from schema — skip 'name' (always synced from instance_name)
      var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
      if (visibleParamKeys.length) {
        html += '<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--pf-border);">'
          + '<div style="font-size:11px;color:var(--pf-accent);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>';
        visibleParamKeys.forEach(function(k) {
          var spec = paramSchema[k] || {};
          var defVal = spec.default || '';
          html += '<div style="margin-bottom:6px;"><label style="color:var(--pf-muted);font-size:11px;">'
            + escapeHtml(k + (spec.required ? ' *' : '')) + '</label>'
            + '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(defVal)) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
        });
        html += '</div>';
      }
      html += '</div>';
      formArea.innerHTML = html;
    }

    defSelect.onchange = function() {
      selectedDef = defSelect.value;
      _renderForm();
    };

    // Create link + buttons
    var createLink = document.createElement('div');
    createLink.style.cssText = 'margin-top:12px;border-top:1px solid var(--pf-border);padding-top:10px;font-size:11px;';
    var cl = document.createElement('span');
    cl.style.cssText = 'color:var(--pf-accent);cursor:pointer;';
    cl.textContent = '+ ' + t('createNewDefinitionRepository');
    cl.onclick = function() { overlay.remove(); showResourceCreator('agent'); };
    createLink.appendChild(cl);
    panel.appendChild(createLink);

    var btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:12px;';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = t('contextCancel');
    cancelBtn.style.cssText = 'background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;';
    cancelBtn.onclick = function() { overlay.remove(); };
    var addBtn = document.createElement('button');
    addBtn.textContent = t('addAgent');
    addBtn.style.cssText = 'background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;';
    addBtn.onclick = async function() {
      if (!selectedDef) { alert(t('selectDefinitionFirst')); return; }
      var instName = (document.getElementById('_addInstName') || {}).value || '';
      var llm = (document.getElementById('_addLlm') || {}).value || '';
      if (!instName.trim()) { alert(t('instanceNameRequiredMessage')); return; }
      if (!llm) { alert(t('llmServiceRequiredMessage')); return; }
      var params = { name: instName.trim() };
      formArea.querySelectorAll('[data-param]').forEach(function(inp) {
        params[inp.dataset.param] = inp.value;
      });
      overlay.remove();
      await cmdResourceAction('add_agent_to_conv', {
        instance_name: instName.trim(),
        definition: selectedDef,
        params: params,
        llm_service: llm,
        conversation_id: conversationId,
      });
      loadResources();
    };
    btns.appendChild(cancelBtn); btns.appendChild(addBtn);
    panel.appendChild(btns);
  } catch(e) {
    var err = document.createElement('div');
    err.style.cssText = 'color:var(--pf-danger);font-size:12px;';
    err.textContent = t('error') + ': ' + e.message;
    panel.appendChild(err);
  }
}

// ── Param/Secret context menu + create ────────────────────────────
// ── Service context menu ──────────────────────────────────────────
// ── Assign task dialog (agent + variables) ────────────────────────
function _showAssignDialog(taskDefName) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:420px;border:1px solid var(--pf-border);';
  const convAgents = ((_lastResourcesData || {}).agents || []).map(function(a) { return a.name || ''; }).filter(Boolean);
  const agentField = convAgents.length
    ? `<select id="assign-agent" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">${convAgents.map(function(a) { return `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`; }).join('')}</select>`
    : `<input id="assign-agent" value="" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/>`;
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(t('assignTitle', { name: taskDefName }))}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('agent'))}</label>
    ${agentField}</div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('contextMode'))}</label>
    <select id="assign-context" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">
      <option value="isolated">${escapeHtml(t('contextModeIsolated'))}</option>
      <option value="last:10">${escapeHtml(t('contextModeLast10'))}</option>
      <option value="last:20">${escapeHtml(t('contextModeLast20'))}</option>
      <option value="last:50">${escapeHtml(t('contextModeLast50'))}</option>
      <option value="summary:2000">${escapeHtml(t('contextModeSummary2000'))}</option>
      <option value="summary:4000">${escapeHtml(t('contextModeSummary4000'))}</option>
      <option value="full">${escapeHtml(t('contextModeFull'))}</option>
    </select></div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('intervalOptionalOverride'))}</label>
    <input id="assign-interval" placeholder="e.g. 6/1m, 2/1h, 60" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('variablesKeyValue'))}</label>
    <textarea id="assign-vars" placeholder="nbr_images=20&#10;style=cyberpunk" style="width:100%;min-height:60px;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;"></textarea></div>
  <details style="margin-bottom:8px;"><summary style="color:var(--pf-muted);font-size:11px;cursor:pointer;">${escapeHtml(t('limitsOptional'))}</summary>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px;">
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('maxBudget'))}</label><input id="assign-budget" placeholder="$5" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('turnTime'))}</label><input id="assign-turn-time" placeholder="5m" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('totalTime'))}</label><input id="assign-total-time" placeholder="1h" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('maxReschedules'))}</label><input id="assign-max-resched" placeholder="50" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
    </div></details>
  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
    <button onclick="_submitAssign(${_pfpJsArg(taskDefName)})" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('assign'))}</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  document.getElementById('assign-agent').focus();
}

function _submitAssign(taskDefName) {
  const agent = (document.getElementById('assign-agent').value || '').trim();
  const context = (document.getElementById('assign-context').value || '').trim();
  const interval = (document.getElementById('assign-interval').value || '').trim();
  const varsText = (document.getElementById('assign-vars').value || '').trim();
  if (!agent) { alert(t('agentRequired')); return; }
  const params = { agent_name: agent, task_def_name: taskDefName };
  if (context && context !== 'isolated') params.context = context;
  if (interval) params.interval = interval;
  if (varsText) {
    const variables = {};
    for (const line of varsText.split('\n')) {
      const eq = line.indexOf('=');
      if (eq > 0) variables[line.substring(0, eq).trim()] = line.substring(eq + 1).trim();
    }
    if (Object.keys(variables).length) params.variables = variables;
  }
  const _bv = (document.getElementById('assign-budget') || {}).value || '';
  const _tv = (document.getElementById('assign-turn-time') || {}).value || '';
  const _ttv = (document.getElementById('assign-total-time') || {}).value || '';
  const _rv = (document.getElementById('assign-max-resched') || {}).value || '';
  if (_bv.trim()) params.max_budget = _bv.trim();
  if (_tv.trim()) params.max_turn_time = _tv.trim();
  if (_ttv.trim()) params.max_total_time = _ttv.trim();
  if (_rv.trim()) params.max_reschedules = parseInt(_rv) || 0;
  action$('assign_task', params).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', d.result || t('taskAssigned')); loadResources(); }
    document.getElementById('resourceEditorOverlay').remove();
  });
}

// ── Task instance context menu (Tasks section – management, not lifecycle) ──
function showTaskInstanceMenu(e, taskId, agent, status) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:140px;box-shadow:0 4px 12px var(--pf-shadow);';
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
  const _taskAction = (action) => {
    action$(action + '_task', { task_id: taskId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', t('taskActionDone', { id: taskId, action: action }));
      loadResources();
    });
  };
  // View task log
  item('\u{1F4CB} ' + t('viewLogMenu'), () => {
    action$('task_log', { name: taskId }).subscribe(d => {
      const log = d.log || [];
      if (!log.length) { addMsg('system', t('noLogEntriesFor', { id: taskId })); return; }
      const lines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
      addMsg('system', t('taskLogTitle', { id: taskId, lines: lines }));
    });
  });
  // View task details
  item('\u{1F441} ' + t('viewDetails'), () => {
    action$('list_resources', {}).subscribe(d => {
      const task = (d.all_tasks || []).find(t => t.task_id === taskId);
      if (!task) { addMsg('system', t('taskNotFound', { id: taskId })); return; }
      const info = t('taskDetails', { id: task.task_id, agent: task.agent, status: task.status, iterations: task.iterations, max: task.max_iterations, definition: task.task_def_name || '-', prompt: task.task });
      addMsg('system', info);
    });
  });
  // Delete
  const sep = document.createElement('div');
  sep.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
  menu.appendChild(sep);
  item('\u{1F5D1} ' + t('delete'), () => _taskAction('delete'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

// ── Running task context menu ─────────────────────────────────────
function showRunningTaskMenu(e, taskId, agent, status) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:140px;box-shadow:0 4px 12px var(--pf-shadow);';
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
  const _taskAction = (action) => {
    action$(action + '_task', { task_id: taskId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', t('taskActionDone', { id: taskId, action: action }));
      loadResources();
    });
  };
  // View task log
  item('\u{1F4CB} ' + t('viewLogMenu'), () => {
    action$('task_log', { name: taskId }).subscribe(d => {
      const log = d.log || [];
      if (!log.length) { addMsg('system', t('noLogEntriesFor', { id: taskId })); return; }
      const lines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
      addMsg('system', t('taskLogTitle', { id: taskId, lines: lines }));
    });
  });
  // Edit limits
  item('\u270F ' + t('editLimits'), () => _showEditLimitsDialog(taskId));
  // Status-specific actions
  if (status === 'active') {
    item('\u23F8 ' + t('pause'), () => _taskAction('pause'));
  } else if (status === 'paused') {
    item('\u25B6 ' + t('resume'), () => _taskAction('resume'));
  } else if (status === 'cancelled' || status === 'failed') {
    item('\u25B6 ' + t('restart'), () => _taskAction('resume'));
  }
  if (status === 'active' || status === 'paused') {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(sep);
    item('\u{1F5D1} ' + t('cancel'), () => _taskAction('cancel'), true);
  }
  // Delete: remove task instance entirely
  const sep2 = document.createElement('div');
  sep2.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
  menu.appendChild(sep2);
  item('\u{1F5D1} ' + t('delete'), () => _taskAction('delete'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

