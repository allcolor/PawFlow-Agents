/* forms.js — Form overlays */

var _cfInputStyle = 'width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px';
var _cfTextareaStyle = 'width:100%;min-height:60px;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px;font-family:var(--vscode-editor-font-family);resize:vertical';
var _cfLabelStyle = 'font-size:11px;color:var(--vscode-descriptionForeground)';

function _scopeSelect() {
  var globalOpt = (window._userRole === 'admin') ? '<option value="global">Global</option>' : '';
  return '<label style="' + _cfLabelStyle + '">Scope</label>'
    + '<select id="cf-scope" style="' + _cfInputStyle + '">'
    + globalOpt
    + '<option value="user">User</option><option value="conversation">Conversation</option></select>';
}

// ── Resource Edit Form ──
var _resFieldDefs = {
  agent:    [['prompt','textarea'],['description','text'],['llm_service','text'],['model','text'],['tools','text'],['max_depth','number'],['timeout','number']],
  skill:    [['prompt','textarea'],['description','text']],
  mcp:      [['url','text'],['auth','text'],['description','text']],
  task_def: [['prompt','textarea'],['criteria','textarea'],['default_interval','text'],['verifier','text'],['description','text']],
  prompt:   [['content','textarea'],['title','text'],['category','text'],['description','text']],
};

var _pendingEdit = null;

function showEditResourceForm(rtype, name) {
  vscode.postMessage({ type: 'command', command: 'get_resource_detail', arg: JSON.stringify({ resource_type: rtype, name: name }) });
  _pendingEdit = { rtype: rtype, name: name };
}

function _renderEditForm(rtype, name, data) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  var fields = _resFieldDefs[rtype] || [];
  var scope = data._scope || data.scope || 'user';

  var html = '<div class="panel-header"><h4>Edit ' + rtype + ': ' + esc(name) + ' [' + scope + ']</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
  html += '<div style="padding:4px">';
  for (var i = 0; i < fields.length; i++) {
    var key = fields[i][0];
    var type = fields[i][1];
    var val = (data[key] != null) ? String(data[key]) : '';
    html += '<label style="' + _cfLabelStyle + '">' + esc(key) + '</label>';
    if (type === 'textarea') {
      html += '<textarea id="ef-' + key + '" style="' + _cfTextareaStyle + '">' + esc(val) + '</textarea>';
    } else if (type === 'number') {
      html += '<input id="ef-' + key + '" type="number" value="' + esc(val) + '" style="' + _cfInputStyle + '">';
    } else {
      html += '<input id="ef-' + key + '" value="' + esc(val) + '" style="' + _cfInputStyle + '">';
    }
  }
  html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitEditForm(\'' + esc(rtype) + '\',\'' + esc(name) + '\',\'' + scope + '\')" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Save</button>'
    + '</div></div>';
  overlay.innerHTML = html;
}

function submitEditForm(rtype, name, scope) {
  var fields = _resFieldDefs[rtype] || [];
  var data = {};
  for (var i = 0; i < fields.length; i++) {
    var key = fields[i][0];
    var type = fields[i][1];
    var el = document.getElementById('ef-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }
  vscode.postMessage({ type: 'command', command: 'update_resource',
    arg: JSON.stringify({ resource_type: rtype, name: name, scope: scope, data: data }) });
  closePanel();
  statusEl.textContent = rtype + ' "' + name + '" updated';
  setTimeout(function() { statusEl.textContent = ''; }, 3000);
  setTimeout(function() { loadResourcesPanel(); }, 500);
}

// ── Service Edit Form ──
var _editSvcId = '';

function showEditServiceForm(serviceId) {
  vscode.postMessage({ type: 'command', command: 'get_service_detail', arg: JSON.stringify({ service_id: serviceId }) });
  _pendingEdit = { rtype: '_service', name: serviceId };
}

function _renderServiceEditForm(serviceId, data) {
  _editSvcId = serviceId;
  var config = data.config || data;
  var svcType = data.service_type || '';
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';

  var html = '<div class="panel-header"><h4>Edit: ' + esc(serviceId) + (svcType ? ' (' + esc(svcType) + ')' : '') + '</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
  html += '<div style="padding:4px"><div id="cf-svc-params"><div style="color:var(--vscode-descriptionForeground);font-size:11px">Loading schema...</div></div>';
  html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitServiceEdit()" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Save</button>'
    + '</div></div>';
  overlay.innerHTML = html;

  if (svcType) {
    window._editSvcConfig = config;
    vscode.postMessage({ type: 'command', command: 'get_service_schema', arg: JSON.stringify({ service_type: svcType }) });
  } else {
    _renderSvcSchemaParams({}, config);
  }
}

function submitServiceEdit() {
  if (!_editSvcId) return;
  var config = {};
  if (_cachedSvcSchema) {
    for (var pname in _cachedSvcSchema) {
      var el = document.getElementById('cf-sp-' + pname);
      if (!el) continue;
      var pdef = _cachedSvcSchema[pname];
      if (pdef.type === 'boolean') config[pname] = el.checked;
      else if (pdef.type === 'integer') config[pname] = parseInt(el.value) || 0;
      else if (pdef.type === 'float') config[pname] = parseFloat(el.value) || 0;
      else if (pdef.type === 'map' || pdef.type === 'object') {
        try { config[pname] = JSON.parse(el.value || '{}'); } catch(e) { config[pname] = {}; }
      } else config[pname] = el.value || '';
    }
  }
  vscode.postMessage({ type: 'command', command: 'update_service',
    arg: JSON.stringify({ service_id: _editSvcId, config: config }) });
  closePanel();
  statusEl.textContent = 'Service "' + _editSvcId + '" updated';
  _editSvcId = '';
  setTimeout(function() { statusEl.textContent = ''; }, 3000);
  setTimeout(function() { loadResourcesPanel(); }, 500);
}

function showAssignForm(taskName) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  overlay.innerHTML = '<div class="panel-header"><h4>Assign: ' + esc(taskName) + '</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>'
    + '<div style="padding:4px">'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Agent</label>'
    + '<input id="af-agent" value="" style="width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px">'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Context mode</label>'
    + '<select id="af-context" style="width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px">'
    + '<option value="isolated">isolated (default)</option>'
    + '<option value="last:10">last:10</option>'
    + '<option value="last:20">last:20</option>'
    + '<option value="last:50">last:50</option>'
    + '<option value="summary:2000">summary:2000</option>'
    + '<option value="summary:4000">summary:4000</option>'
    + '<option value="full">full (entire context)</option>'
    + '</select>'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Interval (optional)</label>'
    + '<input id="af-interval" placeholder="e.g. 6/1m, 2/1h, 60" style="width:100%;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px">'
    + '<label style="font-size:11px;color:var(--vscode-descriptionForeground)">Variables (key=value, one per line)</label>'
    + '<textarea id="af-vars" placeholder="nbr_images=20\nstyle=cyberpunk" style="width:100%;min-height:50px;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);padding:4px 6px;border-radius:3px;margin:2px 0 8px;font-size:12px;font-family:var(--vscode-editor-font-family);resize:vertical"></textarea>'
    + '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitAssignForm(\'' + esc(taskName).replace(/'/g, "\\'") + '\')" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Assign</button>'
    + '</div></div>';
  document.getElementById('af-agent').focus();
}

function submitAssignForm(taskName) {
  var agent = document.getElementById('af-agent').value.trim();
  var context = document.getElementById('af-context').value;
  var interval = document.getElementById('af-interval').value.trim();
  var varsText = document.getElementById('af-vars').value.trim();
  if (!agent) { return; }

  var params = { agent_name: agent, task_name: taskName, context: context };
  if (interval) params.interval = interval;
  if (varsText) {
    var variables = {};
    varsText.split('\n').forEach(function(line) {
      var eq = line.indexOf('=');
      if (eq > 0) variables[line.slice(0,eq).trim()] = line.slice(eq+1).trim();
    });
    if (Object.keys(variables).length) params.variables = variables;
  }
  vscode.postMessage({ type: 'command', command: 'assign_task', arg: JSON.stringify(params) });
  closePanel();
}

var _flowStartInstanceId = '';
var _flowStartEditOnly = false;
function showFlowStartForm(instanceId, editOnly) {
  _flowStartInstanceId = instanceId;
  _flowStartEditOnly = !!editOnly;
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  overlay.innerHTML = '<div class="panel-header"><h4>' + (editOnly ? 'Edit Flow Params' : 'Start Flow') + ': ' + esc(instanceId) + '</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>'
    + '<div id="flowParamsContent" style="padding:4px;color:var(--vscode-descriptionForeground)">Loading parameters...</div>';
  vscode.postMessage({ type: 'command', command: 'get_flow_instance', arg: JSON.stringify({ instance_id: instanceId }) });
}
function _renderFlowStartParams(data) {
  var el = document.getElementById('flowParamsContent');
  if (!el) return;
  if (data.error) { el.innerHTML = '<span style="color:#f85149">' + esc(data.error) + '</span>'; return; }
  var tplParams = data.template_parameters || {};
  var instParams = data.parameters || {};
  var merged = Object.assign({}, tplParams, instParams);
  var keys = Object.keys(merged);
  var html = '';
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
    var v = typeof merged[k] === 'object' ? JSON.stringify(merged[k]) : String(merged[k]);
    html += '<label style="' + _cfLabelStyle + '">' + esc(k) + '</label>'
      + '<input class="fp-input" data-key="' + esc(k) + '" value="' + esc(v) + '" style="' + _cfInputStyle + '">';
  }
  if (!html) html = '<div style="color:var(--vscode-descriptionForeground)">No parameters</div>';
  var btnLabel = _flowStartEditOnly ? 'Save' : 'Start';
  html += '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitFlowStart()" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">' + btnLabel + '</button>'
    + '</div>';
  el.innerHTML = html;
}
function submitFlowStart() {
  var params = {};
  document.querySelectorAll('.fp-input').forEach(function(el) {
    params[el.dataset.key] = el.value;
  });
  vscode.postMessage({ type: 'command', command: 'update_flow_params', arg: JSON.stringify({ instance_id: _flowStartInstanceId, parameters: params }) });
  if (!_flowStartEditOnly) {
    setTimeout(function() {
      vscode.postMessage({ type: 'command', command: 'start_flow', arg: JSON.stringify({ instance_id: _flowStartInstanceId }) });
    }, 300);
  }
  closePanel();
  statusEl.textContent = _flowStartEditOnly ? 'Parameters saved' : 'Flow starting...';
  setTimeout(function() { statusEl.textContent = ''; loadResourcesPanel(); }, 2000);
}

function showCreateForm(rtype) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  var titleMap = {agents:'Create Agent',skills:'Create Skill',task_defs:'Create Task',prompts:'Create Prompt',variables:'Create Variable',secrets:'Create Secret',services:'Install Service'};
  var title = titleMap[rtype] || 'Create';

  var fields = '';
  if (rtype === 'agents') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_agent">'
      + '<label style="' + _cfLabelStyle + '">System prompt</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="You are a helpful assistant..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Model (optional)</label>'
      + '<input id="cf-model" style="' + _cfInputStyle + '" placeholder="gpt-4o">'
      + '<label style="' + _cfLabelStyle + '">LLM Service (optional)</label>'
      + '<input id="cf-llm" style="' + _cfInputStyle + '" placeholder="default">'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">';
  } else if (rtype === 'skills') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_skill">'
      + '<label style="' + _cfLabelStyle + '">Prompt</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="Skill instructions..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">';
  } else if (rtype === 'task_defs') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_task">'
      + '<label style="' + _cfLabelStyle + '">Task prompt</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="What the task should do..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Criteria (optional)</label>'
      + '<input id="cf-criteria" style="' + _cfInputStyle + '">'
      + '<label style="' + _cfLabelStyle + '">Interval (optional)</label>'
      + '<input id="cf-interval" style="' + _cfInputStyle + '" placeholder="6/1m">'
      + '<label style="' + _cfLabelStyle + '">Verifier agent (optional)</label>'
      + '<input id="cf-verifier" style="' + _cfInputStyle + '">';
  } else if (rtype === 'prompts') {
    fields = '<label style="' + _cfLabelStyle + '">Name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_prompt">'
      + '<label style="' + _cfLabelStyle + '">Content</label>'
      + '<textarea id="cf-prompt" style="' + _cfTextareaStyle + '" placeholder="Prompt content..."></textarea>'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">';
  } else if (rtype === 'variables') {
    fields = '<label style="' + _cfLabelStyle + '">Key</label>'
      + '<input id="cf-key" style="' + _cfInputStyle + '" placeholder="my_variable">'
      + '<label style="' + _cfLabelStyle + '">Value</label>'
      + '<input id="cf-value" style="' + _cfInputStyle + '">'
      + '<label style="' + _cfLabelStyle + '">Scope</label>'
      + _scopeSelect();
  } else if (rtype === 'secrets') {
    fields = '<label style="' + _cfLabelStyle + '">Key</label>'
      + '<input id="cf-key" style="' + _cfInputStyle + '" placeholder="my_secret">'
      + '<label style="' + _cfLabelStyle + '">Value</label>'
      + '<input id="cf-value" type="password" style="' + _cfInputStyle + '">'
      + '<label style="' + _cfLabelStyle + '">Scope</label>'
      + _scopeSelect();
  } else if (rtype === 'services') {
    title = 'Install Service';
    fields = '<label style="' + _cfLabelStyle + '">Service type (loading...)</label>'
      + '<select id="cf-svctype" onchange="_onSvcTypeChange()" style="' + _cfInputStyle + '"><option value="">Loading...</option></select>'
      + '<label style="' + _cfLabelStyle + '">Service name</label>'
      + '<input id="cf-name" style="' + _cfInputStyle + '" placeholder="my_service">'
      + '<label style="' + _cfLabelStyle + '">Description (optional)</label>'
      + '<input id="cf-desc" style="' + _cfInputStyle + '">'
      + '<div id="cf-svc-params"></div>';
    setTimeout(function() {
      vscode.postMessage({ type: 'command', command: 'list_service_types' });
    }, 50);
  } else if (rtype === 'flows') {
    title = 'Deploy Flow';
    fields = '<label style="' + _cfLabelStyle + '">Template (loading...)</label>'
      + '<select id="cf-template" style="' + _cfInputStyle + '"><option>Loading...</option></select>'
      + '<label style="' + _cfLabelStyle + '">Scope</label>'
      + _scopeSelect()
      + '<label style="' + _cfLabelStyle + '">Parameters (JSON, optional)</label>'
      + '<textarea id="cf-params" style="' + _cfTextareaStyle + '" placeholder=\'{"key": "value"}\'></textarea>';
    setTimeout(function() {
      vscode.postMessage({ type: 'command', command: 'list_available_flows' });
    }, 50);
  }

  overlay.innerHTML = '<div class="panel-header"><h4>' + title + '</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>'
    + '<div style="padding:4px">' + fields
    + '<div style="display:flex;gap:6px;justify-content:flex-end;margin-top:8px">'
    + '<button onclick="closePanel()" style="background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Cancel</button>'
    + '<button onclick="submitCreateForm(\'' + rtype + '\')" style="background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:4px 12px;border-radius:3px;cursor:pointer;font-size:12px">Create</button>'
    + '</div></div>';
  var nameEl = document.getElementById('cf-name') || document.getElementById('cf-key') || document.getElementById('cf-svctype');
  if (nameEl) nameEl.focus();
}

function submitCreateForm(rtype) {
  var nameEl = document.getElementById('cf-name');
  var promptEl = document.getElementById('cf-prompt');
  var name = (nameEl ? nameEl.value : '').trim();
  var prompt = (promptEl ? promptEl.value : '').trim();
  if (!name && rtype !== 'variables' && rtype !== 'secrets') return;

  var cmd = '';
  var params = {};

  if (rtype === 'agents') {
    cmd = 'create_agent';
    params = { name: name, prompt: prompt };
    var modelEl = document.getElementById('cf-model');
    var llmEl = document.getElementById('cf-llm');
    var descEl = document.getElementById('cf-desc');
    var model = modelEl ? modelEl.value.trim() : '';
    var llm = llmEl ? llmEl.value.trim() : '';
    var desc = descEl ? descEl.value.trim() : '';
    if (model) params.model = model;
    if (llm) params.llm_service = llm;
    if (desc) params.description = desc;
  } else if (rtype === 'skills') {
    cmd = 'create_resource';
    params = { resource_type: 'skill', name: name, prompt: prompt };
    var descEl2 = document.getElementById('cf-desc');
    var desc2 = descEl2 ? descEl2.value.trim() : '';
    if (desc2) params.description = desc2;
  } else if (rtype === 'task_defs') {
    cmd = 'create_task_def';
    params = { name: name, prompt: prompt };
    var criteriaEl = document.getElementById('cf-criteria');
    var intervalEl = document.getElementById('cf-interval');
    var verifierEl = document.getElementById('cf-verifier');
    var criteria = criteriaEl ? criteriaEl.value.trim() : '';
    var interval = intervalEl ? intervalEl.value.trim() : '';
    var verifier = verifierEl ? verifierEl.value.trim() : '';
    if (criteria) params.criteria = criteria;
    if (interval) params.interval = interval;
    if (verifier) params.verifier = verifier;
  } else if (rtype === 'prompts') {
    cmd = 'create_resource';
    params = { resource_type: 'prompt', name: name, content: prompt };
    var descEl3 = document.getElementById('cf-desc');
    var desc3 = descEl3 ? descEl3.value.trim() : '';
    if (desc3) params.description = desc3;
  } else if (rtype === 'variables') {
    var vkeyEl = document.getElementById('cf-key');
    var vvalEl = document.getElementById('cf-value');
    var vscopeEl = document.getElementById('cf-scope');
    var vkey = vkeyEl ? vkeyEl.value.trim() : '';
    var vvalue = vvalEl ? vvalEl.value : '';
    var vscope = vscopeEl ? vscopeEl.value : 'user';
    if (!vkey) return;
    cmd = 'set_param';
    params = { key: vkey, value: vvalue, scope: vscope };
  } else if (rtype === 'secrets') {
    var skeyEl = document.getElementById('cf-key');
    var svalEl = document.getElementById('cf-value');
    var sscopeEl = document.getElementById('cf-scope');
    var skey = skeyEl ? skeyEl.value.trim() : '';
    var svalue = svalEl ? svalEl.value : '';
    var sscope = sscopeEl ? sscopeEl.value : 'user';
    if (!skey) return;
    cmd = 'set_secret';
    params = { key: skey, value: svalue, scope: sscope };
  } else if (rtype === 'services') {
    var svcTypeEl = document.getElementById('cf-svctype');
    var svcNameEl = document.getElementById('cf-name');
    var svcDescEl = document.getElementById('cf-desc');
    var svcType = svcTypeEl ? svcTypeEl.value : '';
    var svcName = svcNameEl ? svcNameEl.value.trim() : '';
    var svcDesc = svcDescEl ? svcDescEl.value.trim() : '';
    if (!svcType || !svcName) return;
    var config = {};
    var paramsDiv = document.getElementById('cf-svc-params');
    if (paramsDiv && _cachedSvcSchema) {
      for (var pname in _cachedSvcSchema) {
        var el = document.getElementById('cf-sp-' + pname);
        if (!el) continue;
        var pdef = _cachedSvcSchema[pname];
        if (pdef.type === 'boolean') config[pname] = el.checked;
        else if (pdef.type === 'integer') config[pname] = parseInt(el.value) || 0;
        else if (pdef.type === 'float') config[pname] = parseFloat(el.value) || 0;
        else if (pdef.type === 'map' || pdef.type === 'object') {
          try { config[pname] = JSON.parse(el.value || '{}'); } catch(e) { config[pname] = {}; }
        } else config[pname] = el.value || '';
      }
    }
    cmd = 'service_install';
    params = { service_type: svcType, service_name: svcName, description: svcDesc, config: config };
  } else if (rtype === 'flows') {
    var templateEl = document.getElementById('cf-template');
    var flowScopeEl = document.getElementById('cf-scope');
    var flowParamsEl = document.getElementById('cf-params');
    var templateId = templateEl ? templateEl.value.trim() : '';
    var flowScope = flowScopeEl ? flowScopeEl.value : 'user';
    var flowParams = flowParamsEl ? flowParamsEl.value.trim() : '';
    if (!templateId) return;
    cmd = 'deploy_flow';
    params = { template_id: templateId, scope: flowScope };
    if (flowParams) {
      try { params.parameters = JSON.parse(flowParams); } catch(e) { statusEl.textContent = 'Invalid JSON'; return; }
    }
  }

  if (cmd) {
    vscode.postMessage({ type: 'command', command: cmd, arg: JSON.stringify(params) });
  }
  closePanel();
  statusEl.textContent = rtype.replace(/s$/, '') + ' "' + name + '" created';
  setTimeout(function() { statusEl.textContent = ''; }, 3000);
  setTimeout(function() { loadResourcesPanel(); }, 500);
}

// ── Service schema-based form ──
var _cachedSvcSchema = null;

function _onSvcTypeChange() {
  var sel = document.getElementById('cf-svctype');
  var svcType = sel ? sel.value : '';
  var paramsDiv = document.getElementById('cf-svc-params');
  if (!paramsDiv || !svcType) { if (paramsDiv) paramsDiv.innerHTML = ''; return; }
  paramsDiv.innerHTML = '<div style="color:var(--vscode-descriptionForeground);font-size:11px;padding:4px">Loading schema...</div>';
  vscode.postMessage({ type: 'command', command: 'get_service_schema', arg: JSON.stringify({ service_type: svcType }) });
}

function _renderSvcSchemaParams(schema, values) {
  values = values || {};
  _cachedSvcSchema = schema;
  var paramsDiv = document.getElementById('cf-svc-params');
  if (!paramsDiv) return;
  var html = '';
  for (var pname in schema) {
    var p = schema[pname];
    var val = values[pname] !== undefined ? values[pname] : (p.default !== undefined ? p.default : '');
    var req = p.required === true ? ' *' : '';
    var desc = p.description ? '<div style="font-size:10px;color:var(--vscode-descriptionForeground)">' + p.description + '</div>' : '';
    html += '<label style="' + _cfLabelStyle + '">' + pname + req + '</label>' + desc;
    if (p.type === 'boolean') {
      html += '<div style="margin:2px 0 8px"><input type="checkbox" id="cf-sp-' + pname + '"' + (val ? ' checked' : '') + '></div>';
    } else if (p.type === 'select' && p.options) {
      html += '<select id="cf-sp-' + pname + '" style="' + _cfInputStyle + '">';
      for (var oi = 0; oi < p.options.length; oi++) {
        var opt = p.options[oi];
        html += '<option value="' + opt + '"' + (opt === val ? ' selected' : '') + '>' + opt + '</option>';
      }
      html += '</select>';
    } else if (p.type === 'integer' || p.type === 'float') {
      html += '<input type="number" id="cf-sp-' + pname + '" value="' + val + '" style="' + _cfInputStyle + '">';
    } else if (p.type === 'map' || p.type === 'object' || p.type === 'textarea') {
      var textVal = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
      html += '<textarea id="cf-sp-' + pname + '" style="' + _cfTextareaStyle + '">' + textVal + '</textarea>';
    } else if (p.sensitive) {
      html += '<input type="password" id="cf-sp-' + pname + '" value="' + val + '" style="' + _cfInputStyle + '">';
    } else {
      html += '<input id="cf-sp-' + pname + '" value="' + val + '" style="' + _cfInputStyle + '">';
    }
  }
  paramsDiv.innerHTML = html;
}
