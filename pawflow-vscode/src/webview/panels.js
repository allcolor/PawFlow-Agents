/* panels.js — Panel system */

function showPanel(name) {
  var overlay = document.getElementById('panelOverlay');
  overlay.className = 'panel-overlay visible';
  overlay.innerHTML = '<div class="panel-header"><h4>' + name.charAt(0).toUpperCase() + name.slice(1) + '</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div><div class="msg system">Loading...</div>';

  if (name === 'resources') loadResourcesPanel();
  else if (name === 'context') loadContextPanel();
  else if (name === 'files') loadFilesPanel();
  else if (name === 'tools') loadToolsPanel();
  else if (name === 'accounts') loadAccountsPanel();
  else if (name === 'plans') loadPlansPanel();
}

function closePanel() {
  document.getElementById('panelOverlay').className = 'panel-overlay';
}

var _resMenuRtype = '';
var _resMenuName = '';

function showResMenu(e, rtype, name) {
  e.preventDefault();
  e.stopPropagation();
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();

  _resMenuRtype = rtype;
  _resMenuName = name;

  var menu = document.createElement('div');
  menu.className = 'res-ctx';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  function addItem(label, action) {
    var d = document.createElement('div');
    d.textContent = label;
    d.onclick = function() { menu.remove(); doResAction(action); };
    menu.appendChild(d);
  }
  function addSep() {
    var hr = document.createElement('hr');
    menu.appendChild(hr);
  }

  if (rtype === 'agents' || rtype === 'skills' || rtype === 'mcp' || rtype === 'prompts' || rtype === 'task_defs') {
    addItem('Edit...', 'edit_resource');
    addSep();
  }
  if (rtype === 'agents' || rtype === 'skills' || rtype === 'mcp' || rtype === 'prompts') {
    addItem('Activate', 'activate');
    addItem('Deactivate', 'deactivate');
  }
  if (rtype === 'agents') {
    addSep();
    addItem('Enable agent', 'agent_enable');
    addItem('Disable agent', 'agent_disable');
  }
  if (rtype === 'task_defs') {
    addItem('Assign to agent...', 'assign_task');
  }
  if (rtype === 'services') {
    addItem('Edit...', 'edit_service');
    addSep();
    addItem('Enable', 'svc_enable');
    addItem('Disable', 'svc_disable');
  }
  if (rtype === 'parameters' || rtype === 'secrets') {
    addItem('Edit...', 'edit_param');
  }
  if (rtype === 'flows') {
    var flowItem = null;
    try {
      var allFlows = _resData && _resData.flows ? _resData.flows : [];
      for (var fi = 0; fi < allFlows.length; fi++) {
        if ((allFlows[fi].instance_id || allFlows[fi].id || allFlows[fi].name) === name) { flowItem = allFlows[fi]; break; }
      }
    } catch(e) {}
    if (flowItem && flowItem.status === 'running') {
      addItem('\u23f9 Stop', 'flow_stop');
    } else {
      addItem('\u25b6 Start...', 'flow_start');
    }
    addItem('\u270f Edit params...', 'flow_edit_params');
    if (flowItem && flowItem.scope === 'conversation') {
      addItem('\u2b06 Promote to user', 'flow_promote');
    }
    addSep();
    addItem('\ud83d\uddd1 Undeploy', 'flow_undeploy');
  }
  if (rtype !== 'flows') {
    addSep();
    if (rtype === 'services') addItem('Uninstall', 'svc_uninstall');
    else if (rtype === 'parameters' || rtype === 'secrets') addItem('Delete', 'del_param');
    else if (rtype === 'task_defs') addItem('Delete', 'del_task');
    else addItem('Delete', 'delete_res');
  }

  document.body.appendChild(menu);
  setTimeout(function() {
    document.addEventListener('click', function rm() { menu.remove(); document.removeEventListener('click', rm); });
  }, 0);
}

function doResAction(action) {
  var rtype = _resMenuRtype;
  var name = _resMenuName;
  var singularType = rtype.replace(/s$/, '');
  var cmd = '';
  var params = {};

  if (action === 'activate') { cmd = 'activate_resource'; params = { resource_type: singularType, name: name }; }
  else if (action === 'deactivate') { cmd = 'deactivate_resource'; params = { resource_type: singularType, name: name }; }
  else if (action === 'delete_res') { cmd = 'delete_resource'; params = { resource_type: singularType, name: name }; }
  else if (action === 'svc_enable') { cmd = 'service_enable'; params = { service_id: name }; }
  else if (action === 'svc_disable') { cmd = 'service_disable'; params = { service_id: name }; }
  else if (action === 'svc_uninstall') { cmd = 'service_uninstall'; params = { service_id: name }; }
  else if (action === 'agent_enable') { cmd = 'agent_enable'; params = { agent_name: name }; }
  else if (action === 'agent_disable') { cmd = 'agent_disable'; params = { agent_name: name }; }
  else if (action === 'del_task') { cmd = 'delete_task_def'; params = { name: name }; }
  else if (action === 'edit_param') {
    showCreateForm(rtype === 'secrets' ? 'secrets' : 'variables');
    setTimeout(function() {
      var keyEl = document.getElementById('cf-key');
      if (keyEl) keyEl.value = name;
    }, 0);
    return;
  }
  else if (action === 'del_param') {
    cmd = rtype === 'secrets' ? 'delete_secret' : 'delete_param';
    params = { key: name, scope: 'user' };
  }
  else if (action === 'assign_task') {
    showAssignForm(name);
    return;
  }
  else if (action === 'flow_start') { showFlowStartForm(name); return; }
  else if (action === 'flow_edit_params') { showFlowStartForm(name, true); return; }
  else if (action === 'flow_promote') { cmd = 'promote_flow'; params = { instance_id: name, target_scope: 'user' }; }
  else if (action === 'flow_stop') { cmd = 'stop_flow'; params = { instance_id: name }; }
  else if (action === 'flow_undeploy') {
    if (!confirm('Undeploy flow \'' + name + '\'?')) return;
    cmd = 'undeploy_flow'; params = { instance_id: name };
  }
  else if (action === 'edit_resource') {
    showEditResourceForm(singularType, name);
    return;
  }
  else if (action === 'edit_service') {
    showEditServiceForm(name);
    return;
  }

  if (cmd) {
    vscode.postMessage({ type: 'command', command: cmd, arg: JSON.stringify(params) });
    setTimeout(function() { loadResourcesPanel(); }, 500);
  }
}

function loadResourcesPanel() {
  vscode.postMessage({ type: 'command', command: 'list_resources' });
  _pendingPanel = 'resources';
}

function loadContextPanel() {
  vscode.postMessage({ type: 'command', command: 'get_context' });
  _pendingPanel = 'context';
}

function loadFilesPanel() {
  vscode.postMessage({ type: 'command', command: 'list_conv_files' });
  _pendingPanel = 'files';
}

function loadToolsPanel() {
  vscode.postMessage({ type: 'command', command: 'list_tools' });
  _pendingPanel = 'tools';
}

function loadAccountsPanel() {
  vscode.postMessage({ type: 'command', command: 'list_linked_accounts' });
  _pendingPanel = 'accounts';
}

function loadPlansPanel() {
  vscode.postMessage({ type: 'command', command: 'get_plans' });
  _pendingPanel = 'plans';
}

function unlinkAccount(provider) {
  if (!confirm('Unlink ' + provider + ' account?')) return;
  vscode.postMessage({ type: 'command', command: 'unlink_account', arg: JSON.stringify({ provider: provider }) });
  setTimeout(function() { loadAccountsPanel(); }, 500);
}

var _pendingPanel = '';

function renderPanelResult(action, data) {
  // Handle edit form data responses
  if (_pendingEdit && action === 'get_resource_detail') {
    _renderEditForm(_pendingEdit.rtype, _pendingEdit.name, data);
    _pendingEdit = null;
    return true;
  }
  if (_pendingEdit && _pendingEdit.rtype === '_service' && action === 'get_service_detail') {
    _renderServiceEditForm(_pendingEdit.name, data.config || data);
    _pendingEdit = null;
    return true;
  }

  if (action === 'get_flow_instance') {
    _renderFlowStartParams(data);
    return true;
  }

  if (action === 'list_available_flows') {
    var sel = document.getElementById('cf-template');
    if (sel) {
      var templates = data.templates || [];
      sel.innerHTML = templates.map(function(t) {
        return '<option value="' + esc(t.id) + '">' + esc(t.name) + ' (' + t.tasks_count + ' tasks)' + (t.version ? ' v' + t.version : '') + '</option>';
      }).join('') || '<option>(no templates)</option>';
      var lbl = sel.previousElementSibling;
      if (lbl) lbl.textContent = 'Template';
    }
    return true;
  }

  if (action === 'list_service_types') {
    var svcSel = document.getElementById('cf-svctype');
    if (svcSel) {
      var types = data.service_types || [];
      svcSel.innerHTML = types.map(function(t) {
        return '<option value="' + esc(t.type) + '">' + esc(t.name || t.type) + '</option>';
      }).join('') || '<option>(no types)</option>';
      var svcLbl = svcSel.previousElementSibling;
      if (svcLbl) svcLbl.textContent = 'Service type';
      if (types.length) _onSvcTypeChange();
    }
    return true;
  }

  if (action === 'get_service_schema') {
    var editConfig = window._editSvcConfig || {};
    _renderSvcSchemaParams(data.parameters || {}, editConfig);
    window._editSvcConfig = null;
    return true;
  }

  var overlay = document.getElementById('panelOverlay');
  if (!overlay || overlay.className !== 'panel-overlay visible') return false;

  if (action === 'list_resources' && _pendingPanel === 'resources') {
    _resData = data;
    var html = '<div class="panel-header"><h4>Resources</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';

    var sectionOrder = ['agents','skills','mcp','prompts','task_defs','flows','services','parameters','secrets'];
    var sectionLabels = {agents:'Agents',skills:'Skills',mcp:'MCP Servers',prompts:'Prompts',task_defs:'Tasks',flows:'Flows',services:'Services',parameters:'Variables',secrets:'Secrets'};

    for (var si = 0; si < sectionOrder.length; si++) {
      var rtype = sectionOrder[si];
      var items = data[rtype];
      if (!items) continue;
      if (!Array.isArray(items)) {
        if (typeof items === 'object') {
          items = Object.entries(items).map(function(e) {
            var v = typeof e[1] === 'object' ? e[1] : {};
            v.id = v.id || e[0];
            v.name = v.name || v.id || e[0];
            return v;
          });
        } else continue;
      }
      if (!items.length) continue;

      var label = sectionLabels[rtype] || rtype;
      var canCreate = ['agents','skills','task_defs','prompts','services','parameters','secrets','flows'].indexOf(rtype) >= 0;
      var createType = rtype === 'parameters' ? 'variables' : rtype;
      var addBtn = canCreate ? ' <button style="background:none;border:none;color:var(--vscode-textLink-foreground);cursor:pointer;font-size:11px" onclick="event.stopPropagation();showCreateForm(\'' + createType + '\')">[+]</button>' : '';
      html += '<div class="res-section" onclick="this.classList.toggle(\'collapsed\')">'
        + '<span class="res-arrow">\u25BC</span> <strong>' + esc(label) + '</strong> ' + addBtn + ' <span style="color:var(--vscode-descriptionForeground)">(' + items.length + ')</span></div>';
      html += '<div class="res-items">';
      for (var ii = 0; ii < items.length; ii++) {
        var item = items[ii];
        var itemName = item.name || item.id || item.service_id || item.instance_id || item.flow_name || item.key || '?';
        var scope = item.scope || item._scope || 'user';
        var scopeBadge = scope === 'global' ? ' <span style="color:var(--vscode-descriptionForeground);font-size:9px">[global]</span>' : (scope === 'conversation' ? ' <span style="color:var(--vscode-descriptionForeground);font-size:9px">[conv]</span>' : '');
        var active = item.active ? ' <span style="color:#3fb950">\u2713</span>' : '';
        var enabled = item.enabled === false ? ' <span style="color:#f85149">(disabled)</span>' : '';
        var connected = item.connected ? ' <span style="color:#3fb950">(connected)</span>' : '';
        var desc = item.description || item.prompt || item.type || item.service_type || '';
        if (rtype === 'flows') {
          itemName = item.flow_name || item.instance_id || item.name || '?';
          var flowStatus = item.status || 'stopped';
          desc = flowStatus === 'running' ? '\u25b6 running' : flowStatus === 'error' ? '\u26a0 error' : '\u23f9 stopped';
          if (item.template) desc += ' (' + item.template + ')';
          itemName = item.instance_id || itemName;
        }
        if (rtype === 'parameters' && item.value != null) {
          desc = '= ' + String(item.value).slice(0, 40);
        }
        if (rtype === 'secrets') {
          desc = '(encrypted)';
        }
        if (desc.length > 60) desc = desc.slice(0, 60) + '...';
        var statusBadge = active || enabled || connected;

        var ctxAttr = scope !== 'global' ? 'oncontextmenu="showResMenu(event,\'' + esc(rtype) + '\',\'' + esc(itemName).replace(/'/g, "\\'") + '\')"' : '';
        html += '<div class="panel-item" ' + ctxAttr + '>'
          + '<span style="font-weight:500">' + esc(itemName) + '</span>' + scopeBadge + statusBadge
          + (desc ? '<br><span style="color:var(--vscode-descriptionForeground);font-size:10px">' + esc(desc) + '</span>' : '')
          + '</div>';
      }
      html += '</div>';
    }

    overlay.innerHTML = html;
    _pendingPanel = '';
    return true;
  }

  if (action === 'get_context' && _pendingPanel === 'context') {
    var msgs = data.context || data.messages || [];
    var tokens = data.token_estimate || 0;
    var ctxs = data.agent_contexts || {};
    var html2 = '<div class="panel-header"><h4>LLM Context (' + msgs.length + ' msgs, ~' + tokens + ' tokens)</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
    if (Object.keys(ctxs).length) {
      html2 += '<div style="font-size:10px;color:var(--vscode-descriptionForeground);margin-bottom:6px">Contexts: '
        + Object.entries(ctxs).filter(function(e){return e[0]!=="*"}).map(function(e){return e[0]+" ("+e[1]+")"}).join(", ") + '</div>';
    }
    var lastMsgs = msgs.slice(-30);
    for (var mi = 0; mi < lastMsgs.length; mi++) {
      var m = lastMsgs[mi];
      var role = m.role || '?';
      var ctxContent = (m.content || '').slice(0, 150);
      var roleColors = {system:"#6c6c8a",user:"#4fc3f7",assistant:"#4ecdc4",tool:"#f4a261"};
      html2 += '<div class="panel-item"><span style="color:' + (roleColors[role]||"#808090") + '">' + role + '</span> ' + esc(ctxContent) + '</div>';
    }
    overlay.innerHTML = html2;
    _pendingPanel = '';
    return true;
  }

  if (action === 'list_conv_files' && _pendingPanel === 'files') {
    var files = data.files || [];
    var html3 = '<div class="panel-header"><h4>Files (' + files.length + ')</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
    if (!files.length) html3 += '<div class="msg system">No files</div>';
    for (var fi2 = 0; fi2 < files.length; fi2++) {
      var f = files[fi2];
      html3 += '<div class="panel-item">' + esc((f.file_id || '?').slice(0,8)) + ' ' + esc(f.filename || '?') + ' (' + (f.size||0).toLocaleString() + ' bytes)</div>';
    }
    overlay.innerHTML = html3;
    _pendingPanel = '';
    return true;
  }

  if (action === 'list_linked_accounts' && _pendingPanel === 'accounts') {
    var links = data.links || {};
    var providers = Object.keys(links);
    var html4 = '<div class="panel-header"><h4>Linked Accounts (' + providers.length + ')</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
    if (!providers.length) {
      html4 += '<div class="msg system">No linked accounts. Use /link &lt;provider&gt; &lt;id&gt; to link one.</div>';
    }
    for (var pi = 0; pi < providers.length; pi++) {
      var provider = providers[pi];
      var channelId = links[provider];
      html4 += '<div class="panel-item" style="display:flex;align-items:center;justify-content:space-between">'
        + '<span><strong>' + esc(provider) + '</strong> \u2014 ' + esc(String(channelId)) + '</span>'
        + '<button onclick="unlinkAccount(\'' + esc(provider) + '\')" style="background:none;border:none;color:var(--vscode-errorForeground);cursor:pointer;font-size:11px">\u2715 Unlink</button>'
        + '</div>';
    }
    overlay.innerHTML = html4;
    _pendingPanel = '';
    return true;
  }

  if (action === 'list_tools' && _pendingPanel === 'tools') {
    var tools = data.tools || [];
    var html5 = '<div class="panel-header"><h4>Tools (' + tools.length + ')</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
    for (var ti = 0; ti < tools.length; ti++) {
      var t = tools[ti];
      html5 += '<div class="panel-item"><strong>' + esc(t.name || '?') + '</strong> <span style="color:var(--vscode-descriptionForeground)">' + esc((t.description||'').slice(0,80)) + '</span></div>';
    }
    overlay.innerHTML = html5;
    _pendingPanel = '';
    return true;
  }

  if (action === 'get_plans' && _pendingPanel === 'plans') {
    var planArr = Array.isArray(data.plans) ? data.plans : Object.values(data.plans || {});
    var html6 = '<div class="panel-header"><h4>Plans (' + planArr.length + ')</h4><button class="panel-close" onclick="closePanel()">\u2715</button></div>';
    html6 += '<div style="padding:4px 8px"><button onclick="createPlanDialog()" style="padding:4px 10px;background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;border-radius:4px;cursor:pointer;font-size:11px">+ Create Plan</button></div>';
    if (!planArr.length) {
      html6 += '<div class="msg system">No active plans. Use /plan &lt;description&gt; to ask the agent to create one.</div>';
    }
    for (var pli = 0; pli < planArr.length; pli++) {
      var plan = planArr[pli];
      var pid = plan.id || ('plan_' + pli);
      if (!plan || !plan.title) continue;
      var steps = plan.steps || [];
      var doneCount = steps.filter(function(s) { return s.status === 'done'; }).length;
      var total = steps.length;
      var pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;
      var planStatus = plan.status || 'unknown';
      var statusColors = {'pending_approval':'#f0ad4e','approved':'#6c5ce7','in_progress':'#3498db','completed':'#4ecdc4','cancelled':'#e94560'};
      var sColor = statusColors[planStatus] || '#808090';

      html6 += '<div class="panel-item" style="border-left:3px solid ' + sColor + ';padding:6px 8px;margin:4px 0" oncontextmenu="showPlanCtx(event,\'' + esc(pid) + '\',\'' + esc(planStatus) + '\');return false">';
      html6 += '<div style="display:flex;justify-content:space-between;align-items:center">';
      html6 += '<strong>' + esc(plan.title) + '</strong>';
      html6 += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;color:' + sColor + '">' + esc(planStatus) + '</span>';
      html6 += '</div>';
      var barColor = pct === 100 ? '#4ecdc4' : pct > 50 ? '#6c5ce7' : '#f0ad4e';
      html6 += '<div style="height:3px;background:var(--vscode-panel-border);border-radius:2px;margin:4px 0;overflow:hidden">';
      html6 += '<div style="height:100%;width:' + pct + '%;background:' + barColor + ';border-radius:2px"></div></div>';
      html6 += '<div style="font-size:10px;color:var(--vscode-descriptionForeground)">' + doneCount + '/' + total + ' steps done (' + pct + '%)</div>';
      for (var sti = 0; sti < steps.length; sti++) {
        var step = steps[sti];
        var stepIcons = {'pending':'\u25cb','in_progress':'\u25d4','done':'\u2713','skipped':'\u2013','error':'\u2717'};
        var stepColors = {'pending':'var(--vscode-descriptionForeground)','in_progress':'#6c5ce7','done':'#4ecdc4','skipped':'#555','error':'#e94560'};
        var sIcon = stepIcons[step.status] || '\u25cb';
        var sSColor = stepColors[step.status] || 'var(--vscode-descriptionForeground)';
        var sDeco = step.status === 'skipped' ? 'line-through' : 'none';
        var assignee = step.assigned_to ? ' [' + esc(step.assigned_to) + ']' : '';
        html6 += '<div style="font-size:11px;color:' + sSColor + ';text-decoration:' + sDeco + ';margin:1px 0;padding-left:8px" oncontextmenu="showPlanStepCtx(event,\'' + esc(pid) + '\',' + step.index + ',\'' + esc(step.status) + '\');return false">';
        html6 += sIcon + ' ' + step.index + '. ' + esc(step.description) + assignee;
        if (step.note) html6 += ' <span style="color:#555;font-style:italic">' + esc(step.note) + '</span>';
        html6 += '</div>';
      }
      html6 += '</div>';
    }
    overlay.innerHTML = html6;
    _pendingPanel = '';
    return true;
  }

  return false;
}

// ── Plan context menus ──
function showPlanCtx(e, planId, planStatus) {
  e.preventDefault();
  e.stopPropagation();
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  var menu = document.createElement('div');
  menu.className = 'res-ctx';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  function addItem(label, fn) {
    var d = document.createElement('div');
    d.textContent = label;
    d.onclick = function() { menu.remove(); fn(); };
    menu.appendChild(d);
  }
  if (planStatus === 'pending_approval') {
    addItem('\u2705 Approve', function() { sendCmd('approve_plan', JSON.stringify({plan_id: planId})); setTimeout(loadPlansPanel, 500); });
  }
  if (planStatus !== 'cancelled' && planStatus !== 'completed') {
    addItem('\u27A4 Assign to...', function() { assignPlanDialog(planId); });
  }
  if (planStatus !== 'cancelled' && planStatus !== 'completed') {
    addItem('\u23F9 Cancel', function() { sendCmd('cancel_plan', JSON.stringify({plan_id: planId})); setTimeout(loadPlansPanel, 500); });
  }
  addItem('\u2716 Delete', function() { sendCmd('delete_plan', JSON.stringify({plan_id: planId})); setTimeout(loadPlansPanel, 500); });
  document.body.appendChild(menu);
  setTimeout(function() { document.addEventListener('click', function() { menu.remove(); }, {once: true}); }, 0);
}

function showPlanStepCtx(e, planId, stepIndex, currentStatus) {
  e.preventDefault();
  e.stopPropagation();
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  var menu = document.createElement('div');
  menu.className = 'res-ctx';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  function addItem(label, fn) {
    var d = document.createElement('div');
    d.textContent = label;
    d.onclick = function() { menu.remove(); fn(); };
    menu.appendChild(d);
  }
  if (currentStatus !== 'done') {
    addItem('\u2713 Mark Done', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'done'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus !== 'in_progress') {
    addItem('\u25d4 In Progress', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'in_progress'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus !== 'skipped') {
    addItem('\u2013 Skip', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'skipped'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus !== 'pending') {
    addItem('\u25cb Reset', function() { sendCmd('update_plan_step', JSON.stringify({plan_id: planId, step: stepIndex, status: 'pending'})); setTimeout(loadPlansPanel, 500); });
  }
  if (currentStatus === 'pending' || currentStatus === 'in_progress' || currentStatus === 'error') {
    addItem('\u27A4 Assign to...', function() { assignStepDialog(planId, stepIndex); });
  }
  document.body.appendChild(menu);
  setTimeout(function() { document.addEventListener('click', function() { menu.remove(); }, {once: true}); }, 0);
}

function assignPlanDialog(planId) {
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  vscode.postMessage({ type: 'command', command: 'assign_plan_dialog', arg: planId });
}

function assignStepDialog(planId, stepIndex) {
  var old = document.querySelector('.res-ctx');
  if (old) old.remove();
  vscode.postMessage({ type: 'command', command: 'assign_step_dialog', arg: JSON.stringify({ plan_id: planId, step: stepIndex }) });
}

function createPlanDialog() {
  vscode.postMessage({ type: 'command', command: 'create_plan_dialog' });
}
