// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

function _positionMenu(menu, e) {
  // Position context menu, flip up if it would overflow the viewport
  document.body.appendChild(menu);
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) {
      menu.style.top = Math.max(0, e.clientY - rect.height) + 'px';
    }
    if (rect.right > window.innerWidth) {
      menu.style.left = Math.max(0, e.clientX - rect.width) + 'px';
    }
  });
}

function showResourceMenu(e, rtype, name, scope, autoconv) {
  e.preventDefault();
  const isRepoAgent = rtype === 'agent' && autoconv === null;
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
  const sep = () => {
    const s = document.createElement('div');
    s.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(s);
  };

  // View config — always available (read-only for non-admin on globals)
  item('\u{1F441} ' + t('viewWithEllipsis'), () => showResourceEditor(rtype, name, true));
  // Edit — admin can edit globals, owners can edit their own
  if (_canEditScope(scope)) {
    item('\u270F ' + t('editWithEllipsis'), () => showResourceEditor(rtype, name));
  }
  if (rtype === 'agent') {
    if (isRepoAgent) {
      item('+ ' + t('addToConversation'), () => showAddAgentToConvDialog(name));
    } else {
      item('\u25B6 ' + t('select'), () => cmdAgentSelect(name));
      item('\u2699 ' + t('toolsMcpOverrideMenu'), () => _showToolMcpFilterDialog(name, 'agent'));
      if (autoconv) {
        item('\u23F9 ' + t('autoconvOff'), () => {
          action$('random_thought', { sub: 'off', agent: name }).subscribe(d => {
            addMsg('system', d.error || t('autoconvDisabledFor', { agent: name }));
            loadResources();
          });
        });
      } else {
        item('\u{1F504} ' + t('autoconvOnMenu'), () => {
          const freq = prompt(t('autoconvFrequencyPrompt'), '6/1m');
          if (!freq) return;
          action$('random_thought', { sub: 'on', agent: name, frequency: freq }).subscribe(d => {
            addMsg('system', d.error || t('autoconvEnabledFor', { agent: name, freq: freq }));
            loadResources();
          });
        });
      }
    }
  }
  if (rtype === 'skill') {
    item('\u{1F517} ' + t('assignToAgentMenu'), () => _showSkillAssignDialog(name));
  }
  if (rtype === 'task_def') {
    item('\u25B6 ' + t('assignToAgentMenu'), () => _showAssignDialog(name));
    item('\u{1F4DC} ' + t('viewLogMenu'), () => _showTaskDefLog(name));
  }
  sep();
  // Move between scopes. The source scope is explicit so the backend does
  // not resolve another resource with the same name through the read cascade.
  if (_canEditScope(scope)) {
    if (scope !== 'user') item('\u2191 ' + (scope === 'conversation' ? 'Promote to user' : 'Demote to user'), () => _moveResourceAsk(rtype, name, scope, 'user'));
    if (scope !== 'conversation' && typeof conversationId !== 'undefined' && conversationId) item('\u2193 Move to conversation', () => _moveResource(rtype, name, scope, 'conversation'));
    if (scope !== 'global' && _isAdmin()) item('\u2191 Promote to global', () => _moveResource(rtype, name, scope, 'global'));
  }
  if (_canEditScope(scope)) {
    sep();
    item('\u{1F5D1} ' + t('delete'), () => _deleteResource(rtype, name, scope), true);
  }

  setTimeout(() => document.addEventListener('click', function _close() {
    menu.remove(); document.removeEventListener('click', _close);
  }), 0);
}

function _moveResource(rtype, name, fromScope, targetScope, opts) {
  opts = opts || {};
  const payload = { resource_type: rtype, name, from_scope: fromScope, target_scope: targetScope };
  if ((fromScope === 'conversation' || targetScope === 'conversation') && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  if (opts.target_user_id) payload.target_user_id = opts.target_user_id;
  if (opts.target_conversation_id) payload.target_conversation_id = opts.target_conversation_id;
  action$('copy_resource_scope', payload, { skipConversationId: !(fromScope === 'conversation' || targetScope === 'conversation') }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('resourceCopiedToScope', { type: rtype, name: name, scope: targetScope }));
    loadResources();
  });
}

// Admin demoting a global resource down to a user must pick WHICH user. For
// non-admins, or moves that don't change the owning user, this is a direct
// move (unchanged behaviour).
function _moveResourceAsk(rtype, name, fromScope, targetScope) {
  const needsOwner = (typeof _isAdmin === 'function' && _isAdmin())
    && fromScope === 'global' && targetScope === 'user';
  if (!needsOwner) { _moveResource(rtype, name, fromScope, targetScope); return; }
  _promptTargetOwner(t('demoteToUserTitle', { name: name })).then(function(target) {
    if (target === null) return;  // cancelled
    _moveResource(rtype, name, fromScope, targetScope,
      target ? { target_user_id: target } : {});
  });
}

// Minimal modal: pick a target user (admin). Resolves to username, '' (self),
// or null (cancelled).
function _promptTargetOwner(title) {
  return new Promise(function(resolve) {
    let overlay = document.getElementById('targetOwnerOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'targetOwnerOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:10001;';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:380px;border:1px solid var(--pf-border);';
    panel.innerHTML = '<h3 style="margin:0 0 12px;color:var(--pf-text);font-size:14px;">' + escapeHtml(title || t('targetOwner')) + '</h3>'
      + '<select id="target-owner-pick" style="' + _svcInputStyle + '"><option value="">' + escapeHtml(t('targetOwnerSelf')) + '</option></select>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">'
      + '<button id="target-owner-cancel" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="target-owner-ok" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('confirm')) + '</button>'
      + '</div>';
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    _populateTargetOwnerField('target-owner-pick');
    const done = function(val) { overlay.remove(); resolve(val); };
    panel.querySelector('#target-owner-cancel').onclick = function() { done(null); };
    panel.querySelector('#target-owner-ok').onclick = function() { done(_targetOwnerValue('target-owner-pick')); };
  });
}

function _copyResource(rtype, name, targetScope) {
  _moveResource(rtype, name, '', targetScope);
}

function _deleteResource(rtype, name, scope) {
  if (!confirm(t('resourceDeleteConfirm', { type: rtype, name: name, scope: scope }))) return;
  action$('delete_resource', { resource_type: rtype,
    name, scope: scope || 'user' }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('resourceDeleted', { type: rtype, name: name }));
    loadResources();
  });
}

function _toolMcpRows(items, selectedNames, cls, disabled, mode, dynamicNames) {
  const dis = disabled ? ' disabled' : '';
  const roStyle = disabled ? 'opacity:0.55;cursor:not-allowed;' : '';
  dynamicNames = dynamicNames || [];
  if (!items.length) return '<div style="color:var(--pf-muted);font-size:11px;margin:3px 0 6px 18px;">' + escapeHtml(t('none')) + '</div>';
  return items.map(function(item) {
    const name = item.name || '';
    const isExternalDynamic = item.source === 'dynamic' && item.scope !== 'conversation';
    let isChecked = false;
    if (mode === 'mcp_allow' || mode === 'tool_selected') {
      isChecked = selectedNames.indexOf(name) >= 0;
    } else if (mode === 'tool_default') {
      isChecked = isExternalDynamic
        ? dynamicNames.indexOf(name) >= 0
        : selectedNames.indexOf(name) < 0;
    }
    const checked = isChecked ? ' checked' : '';
    const meta = item.source || item.transport || item.scope || '';
    return '<label style="display:flex;align-items:center;gap:6px;margin:2px 0 2px 18px;font-size:12px;color:var(--pf-text);' + roStyle + '">'
      + '<input type="checkbox" class="' + cls + '" data-name="' + escapeHtml(name) + '" data-source="' + escapeHtml(item.source || '') + '" data-scope="' + escapeHtml(item.scope || '') + '"' + checked + dis + ' style="accent-color:var(--pf-accent);"/> '
      + '<span style="color:var(--pf-text);">' + escapeHtml(name) + '</span>'
      + (meta ? '<span style="color:var(--pf-muted);font-size:10px;">' + escapeHtml(meta) + '</span>' : '')
      + '</label>';
  }).join('');
}

function _defaultSelectedTools(items, filters) {
  const disabledTools = filters.disabled_tools || [];
  const enabledDynamic = filters.enabled_dynamic_tools || [];
  return (items || []).filter(function(item) {
    const name = item.name || '';
    const isExternalDynamic = item.source === 'dynamic' && item.scope !== 'conversation';
    return isExternalDynamic ? enabledDynamic.indexOf(name) >= 0 : disabledTools.indexOf(name) < 0;
  }).map(function(item) { return item.name || ''; }).filter(Boolean);
}

function _renderToolMcpAgentOverride() {
  const data = window._toolMcpFilterData || {};
  const filters = data.filters || {};
  const agents = data.agents || [];
  const agent = document.getElementById('tmf-agent')?.value || '';
  const target = document.getElementById('tmf-agent-panel');
  if (!target) return;
  if (!agent) {
    target.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('selectAgentOverride')) + '</div>';
    return;
  }
  const ov = ((filters.agent_overrides || {})[agent]) || {};
  const toolsCfg = ov.tools || { mode: 'inherit', selected: [] };
  const mcpsCfg = ov.mcps || { mode: 'inherit', enabled: [] };
  const cfgCustom = toolsCfg.mode === 'custom' || mcpsCfg.mode === 'custom';
  const customEl = document.getElementById('tmf-agent-custom');
  const custom = customEl ? customEl.checked : cfgCustom;
  const toolMode = custom ? 'tool_selected' : 'tool_default';
  const toolNames = custom
    ? (cfgCustom ? (toolsCfg.selected || []) : _defaultSelectedTools(data.tools || [], filters))
    : (filters.disabled_tools || []);
  const dynNames = custom ? [] : (filters.enabled_dynamic_tools || []);
  const mcpNames = custom
    ? (cfgCustom ? (mcpsCfg.enabled || []) : (filters.enabled_mcps || []))
    : (filters.enabled_mcps || []);
  target.innerHTML = '<label style="display:flex;align-items:center;gap:6px;margin-bottom:8px;color:var(--pf-text);font-size:12px;">'
    + '<input id="tmf-agent-custom" type="checkbox"' + (custom ? ' checked' : '') + ' onchange="_renderToolMcpAgentOverride()" style="accent-color:var(--pf-accent);"/> ' + escapeHtml(t('overrideConversationDefaultsFor', { agent: agent })) + '</label>'
    + '<div style="color:var(--pf-muted);font-size:11px;margin-top:6px;">' + escapeHtml(t('tools')) + '</div>'
    + _toolMcpRows(data.tools || [], toolNames, 'tmf-agent-tool', !custom, toolMode, dynNames)
    + '<div style="color:var(--pf-muted);font-size:11px;margin-top:8px;">' + escapeHtml(t('mcpServers')) + '</div>'
    + _toolMcpRows(data.mcps || [], mcpNames, 'tmf-agent-mcp', !custom, 'mcp_allow');
}

function _collectDisabled(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return !el.checked;
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

function _collectEnabled(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return el.checked;
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

function _collectDisabledDefaultTools(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return !el.checked && !(el.dataset.source === 'dynamic' && el.dataset.scope !== 'conversation');
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

function _collectEnabledExternalDynamicTools(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return el.checked && el.dataset.source === 'dynamic' && el.dataset.scope !== 'conversation';
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

async function _showToolMcpFilterDialog(agentName, mode) {
  mode = mode || (agentName ? 'agent' : 'conversation');
  let data = {};
  try {
    data = await rxjs.firstValueFrom(action$('get_tool_mcp_filters', { conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); return; }
  } catch (e) { addMsg('error', e.message); return; }
  window._toolMcpFilterData = data;
  const filters = data.filters || {};
  const agents = data.agents || [];
  const selected = agentName || agents[0] || '';
  const showConv = mode !== 'agent';
  const showAgent = mode !== 'conversation';
  const title = showConv && showAgent ? t('toolsMcpAvailability')
    : showConv ? t('conversationToolsMcpAvailability')
    : t('toolsMcpOverrideFor', { agent: selected });
  let overlay = document.getElementById('toolMcpFilterOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'toolMcpFilterOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:680px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(title) + '</h3>'
    + '<button onclick="document.getElementById(\'toolMcpFilterOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button></div>'
    + (showConv ? '<div style="color:var(--pf-accent);font-size:12px;font-weight:600;margin:8px 0 4px;">' + escapeHtml(t('conversationDefaults')) + '</div>'
      + '<div style="color:var(--pf-muted);font-size:11px;margin-top:6px;">' + escapeHtml(t('tools')) + '</div>'
      + _toolMcpRows(data.tools || [], filters.disabled_tools || [], 'tmf-conv-tool', false, 'tool_default', filters.enabled_dynamic_tools || [])
      + '<div style="color:var(--pf-muted);font-size:11px;margin-top:8px;">' + escapeHtml(t('mcpServers')) + '</div>'
      + _toolMcpRows(data.mcps || [], filters.enabled_mcps || [], 'tmf-conv-mcp', false, 'mcp_allow') : '')
    + (showConv && showAgent ? '<div style="border-top:1px solid color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel));margin:14px 0 10px;"></div>' : '')
    + (showAgent ? '<div style="color:var(--pf-accent);font-size:12px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('agentOverride')) + '</div>'
      + '<select id="tmf-agent" onchange="_renderToolMcpAgentOverride()" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-bottom:8px;' + (agentName ? 'display:none;' : '') + '">'
      + '<option value="">' + escapeHtml(t('noAgentOption')) + '</option>'
      + agents.map(function(a) { return '<option value="' + escapeHtml(a) + '"' + (a === selected ? ' selected' : '') + '>@' + escapeHtml(a) + '</option>'; }).join('')
      + '</select><div id="tmf-agent-panel"></div>' : '')
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">'
    + '<button onclick="document.getElementById(\'toolMcpFilterOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button onclick="_saveToolMcpFilterDialog()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button></div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  window._toolMcpFilterMode = mode;
  if (showAgent) _renderToolMcpAgentOverride();
}

function _saveToolMcpFilterDialog() {
  const data = window._toolMcpFilterData || {};
  const filters = JSON.parse(JSON.stringify(data.filters || {}));
  const mode = window._toolMcpFilterMode || 'conversation';
  if (mode !== 'agent') {
    filters.disabled_tools = _collectDisabledDefaultTools('tmf-conv-tool');
    filters.enabled_dynamic_tools = _collectEnabledExternalDynamicTools('tmf-conv-tool');
    filters.enabled_mcps = _collectEnabled('tmf-conv-mcp');
  }
  filters.agent_overrides = filters.agent_overrides || {};
  const agent = document.getElementById('tmf-agent')?.value || '';
  if (mode !== 'conversation' && agent) {
    const custom = !!document.getElementById('tmf-agent-custom')?.checked;
    filters.agent_overrides[agent] = {
      tools: { mode: custom ? 'custom' : 'inherit', selected: custom ? _collectEnabled('tmf-agent-tool') : [] },
      mcps: { mode: custom ? 'custom' : 'inherit', enabled: custom ? _collectEnabled('tmf-agent-mcp') : [] },
    };
  }
  action$('update_tool_mcp_filters', { conversation_id: conversationId, filters }).subscribe(function(res) {
    if (res.error) { addMsg('error', res.error); return; }
    addMsg('system', t('toolsMcpAvailabilityUpdated'));
    document.getElementById('toolMcpFilterOverlay')?.remove();
    loadResources();
  });
}

function _normalizeAgentHookBindings(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map(function(item) {
    if (typeof item === 'string') return { name: item, enabled: true };
    return item && typeof item === 'object' ? item : null;
  }).filter(Boolean);
}

function _splitCommaList(value) {
  return String(value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean);
}

function _joinList(value) {
  return Array.isArray(value) ? value.join(', ') : String(value || '');
}

async function _showAgentHooksDialog() {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  let data = {};
  try {
    data = await rxjs.firstValueFrom(action$('get_conversation_hooks', { conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); return; }
  } catch (e) { addMsg('error', e.message); return; }

  const hooks = data.hooks || [];
  const bindings = _normalizeAgentHookBindings(data.bindings || []);
  const byName = {};
  bindings.forEach(function(b) { byName[b.name || b.ref || ''] = b; });
  let overlay = document.getElementById('agentHooksOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'agentHooksOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:740px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('conversationAgentHooks')) + '</h3>'
    + '<button onclick="document.getElementById(\'agentHooksOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button></div>';
  if (hooks.length) {
    html += '<div style="display:grid;grid-template-columns:34px 1fr 1fr 1fr 70px 88px;gap:6px;align-items:center;color:var(--pf-muted);font-size:10px;margin-bottom:4px;">'
      + '<div></div><div>' + escapeHtml(t('name')) + '</div><div>' + escapeHtml(t('events')) + '</div><div>' + escapeHtml(t('filters')) + '</div><div>' + escapeHtml(t('priority')) + '</div><div>' + escapeHtml(t('failPolicy')) + '</div></div>';
    hooks.forEach(function(h, idx) {
      const name = h.name || '';
      const b = byName[name] || null;
      const enabled = !!b && b.enabled !== false;
      const events = b ? _joinList(b.events) : '';
      const agents = b ? _joinList(b.agents) : '';
      const tools = b ? _joinList(b.tools) : '';
      const priority = b ? (b.priority || 0) : 0;
      const fp = (b && b.fail_policy) || h.fail_policy || 'open';
      html += '<div class="agent-hook-binding-row" data-hook-name="' + escapeHtml(name) + '" style="display:grid;grid-template-columns:34px 1fr 1fr 1fr 70px 88px;gap:6px;align-items:center;margin-bottom:6px;padding:6px;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;">'
        + '<input class="ah-enabled" type="checkbox"' + (enabled ? ' checked' : '') + ' style="accent-color:var(--pf-accent);"/>'
        + '<div style="min-width:0;">' + _scopeBadge(h._scope || h.scope || '') + '<span style="font-size:12px;color:var(--pf-text);">' + escapeHtml(name) + '</span></div>'
        + '<input class="ah-events" value="' + escapeHtml(events) + '" placeholder="' + escapeHtml(_joinList(h.events || [])) + '" style="width:100%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/>'
        + '<div style="display:flex;gap:4px;"><input class="ah-agents" value="' + escapeHtml(agents) + '" placeholder="@" style="width:50%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/><input class="ah-tools" value="' + escapeHtml(tools) + '" placeholder="tool" style="width:50%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/></div>'
        + '<input class="ah-priority" type="number" value="' + escapeHtml(String(priority)) + '" style="width:100%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/>'
        + '<select class="ah-fail" style="width:100%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"><option value="open"' + (fp === 'open' ? ' selected' : '') + '>open</option><option value="closed"' + (fp === 'closed' ? ' selected' : '') + '>closed</option></select>'
        + '</div>';
    });
  } else {
    html += '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noAgentHooks')) + '</div>';
  }
  html += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">'
    + '<button onclick="document.getElementById(\'agentHooksOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button onclick="_saveAgentHooksDialog()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button></div>';
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _selectAgentAndRefresh(name) {
  const result = cmdAgentSelect(name);
  if (result && typeof result.then === 'function') {
    result.then(loadResources);
    return;
  }
  loadResources();
}

function _saveAgentHooksDialog() {
  const rows = document.querySelectorAll('#agentHooksOverlay .agent-hook-binding-row');
  const bindings = Array.from(rows).map(function(row) {
    return {
      name: row.dataset.hookName || '',
      enabled: !!row.querySelector('.ah-enabled')?.checked,
      events: _splitCommaList(row.querySelector('.ah-events')?.value || ''),
      agents: _splitCommaList(row.querySelector('.ah-agents')?.value || ''),
      tools: _splitCommaList(row.querySelector('.ah-tools')?.value || ''),
      priority: parseInt(row.querySelector('.ah-priority')?.value || '0') || 0,
      fail_policy: row.querySelector('.ah-fail')?.value || 'open',
    };
  }).filter(function(b) { return b.name && b.enabled; });
  action$('update_conversation_hooks', { conversation_id: conversationId, bindings: bindings }).subscribe(function(res) {
    if (res.error) { addMsg('error', res.error); return; }
    addMsg('system', t('agentHooksUpdated'));
    document.getElementById('agentHooksOverlay')?.remove();
    loadResources();
  });
}

function showAgentMenu(e, name, scope, autoconv) {
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
  const sep = () => { const s = document.createElement('div'); s.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;'; menu.appendChild(s); };

  item('\u{1F441} ' + t('viewDefinitionMenu'), () => showResourceEditor('agent', name, true));
  if (_canEditScope(scope)) item('\u270F ' + t('editDefinitionMenu'), () => showResourceEditor('agent', name));
  item('\u2699 ' + t('configureConversationMenu'), () => _showAgentConvConfigDialog(name));
  item('\u2699 ' + t('toolsMcpOverrideMenu'), () => _showToolMcpFilterDialog(name, 'agent'));
  item('\u25B6 ' + t('select'), () => {
    const result = cmdAgentSelect(name);
    if (result && typeof result.then === 'function') result.then(loadResources);
    else loadResources();
  });
  item('\u{1F9E9} ' + t('manageSkillsMenu'), () => _showAgentSkillsDialog(name));
  if (autoconv) {
    item('\u23F9 ' + t('autoconvOff'), () => { action$('random_thought', { sub: 'off', agent: name }).subscribe(d => { addMsg('system', d.error || t('autoconvDisabledFor', { agent: name })); loadResources(); }); });
  } else {
    item('\u{1F504} ' + t('autoconvOnMenu'), () => { const freq = prompt(t('autoconvFrequencyPrompt'), '6/1m'); if (!freq) return; action$('random_thought', { sub: 'on', agent: name, frequency: freq }).subscribe(d => { addMsg('system', d.error || t('autoconvEnabledFor', { agent: name, freq: freq })); loadResources(); }); });
  }
  sep();
  if (_canEditScope(scope)) {
    if (scope !== 'user') item('\u2191 ' + (scope === 'conversation' ? 'Promote to user' : 'Demote to user'), () => _moveResource('agent', name, scope, 'user'));
    if (scope !== 'conversation' && typeof conversationId !== 'undefined' && conversationId) item('\u2193 Move to conversation', () => _moveResource('agent', name, scope, 'conversation'));
    if (scope !== 'global' && _isAdmin()) item('\u2191 Promote to global', () => _moveResource('agent', name, scope, 'global'));
  }
  sep();
  item('\u2716 ' + t('removeFromConversation'), () => _removeAgentFromConv(name), true);
  if (_canEditScope(scope)) {
    item('\u{1F5D1} ' + t('deleteDefinitionMenu'), () => _deleteResource('agent', name, scope), true);
  }
  setTimeout(() => document.addEventListener('click', function _close() { menu.remove(); document.removeEventListener('click', _close); }), 0);
}

function _showSkillAssignDialog(skillName) {
  action$('list_resources', {}).subscribe(data => {
    var agents = (data.agents || []).concat((data.repo_agents || []).filter(a => !a.in_conversation));
    if (!agents.length) { addMsg('system', t('noAgentsAvailable')); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var options = agents.map(a => '<option value="' + escapeHtml(a.name) + '">' + escapeHtml(a.name) + '</option>').join('');
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:320px;">'
      + '<h3 style="margin:0 0 12px;">' + escapeHtml(t('skillAssignTitle', { skill: skillName })) + '</h3>'
      + '<select id="_skAssignAgent" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">' + options + '</select>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'
      + '<button onclick="this.closest(\'.exec-overlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="_skAssignBtn" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('assign')) + '</button>'
      + '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('_skAssignBtn').onclick = function() {
      var agent = document.getElementById('_skAssignAgent').value;
      overlay.remove();
      cmdResourceAction('assign_skill', { agent_name: agent, skill_name: skillName }).then(loadResources);
    };
  });
}

function _showAgentConvConfigDialog(agentName) {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  Promise.all([
    rxjs.firstValueFrom(action$('get_agent_conv_config', { name: agentName, conversation_id: conversationId })),
    rxjs.firstValueFrom(listServices$('llmConnection')),
  ]).then(function(results) {
    var data = results[0], svcData = results[1];
    if (data.error) { addMsg('error', data.error); return; }
    var cfg = data.config || {};
    var paramsSchema = data.parameters_schema || {};
    var instParams = cfg.params || {};
    var services = (svcData.services || []).filter(function(s) { return s.enabled; });
    var serviceOpts = services.map(function(s) {
      var sel = s.service_id === cfg.llm_service ? ' selected' : '';
      return '<option value="' + escapeHtml(s.service_id) + '"' + sel + '>'
        + escapeHtml(s.service_id) + ' (' + escapeHtml(s.provider || '') + ')</option>';
    }).join('');
    var toolsStr = Array.isArray(cfg.tools) ? cfg.tools.join(', ') : (cfg.tools || '');
    var overlay = document.createElement('div');
    overlay.id = 'agentConvConfigOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
    var panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:520px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
    var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
      + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('configureAgentTitle', { agent: agentName })) + '</h3>'
      + '<button onclick="document.getElementById(\'agentConvConfigOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
      + '</div>';
    // Definition info
    if (cfg.definition) {
      html += '<div style="margin-bottom:10px;padding:6px 8px;background:var(--pf-sidebar);border-radius:4px;font-size:11px;">'
        + '<span style="color:var(--pf-muted);">' + escapeHtml(t('definition')) + ':</span> <span style="color:var(--pf-accent);">' + escapeHtml(cfg.definition) + '</span></div>';
    }
    // Instance parameters — skip 'name' (synced from instance_name, immutable here)
    var paramKeys = Object.keys(paramsSchema);
    var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
    if (visibleParamKeys.length) {
      html += '<div style="margin-bottom:10px;padding:8px;border:1px solid var(--pf-border);border-radius:4px;">'
        + '<div style="font-size:11px;color:var(--pf-accent);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('instanceParameters')) + '</div>';
      visibleParamKeys.forEach(function(k) {
        var spec = paramsSchema[k] || {};
        var val = instParams[k] || spec.default || '';
        var label = k + (spec.required ? ' *' : '');
        html += '<div style="margin-bottom:6px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(label) + '</label>'
          + '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(val)) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
      });
      html += '</div>';
    }
    // Runtime config
    html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('llmServiceRequired')) + '</label>'
      + '<select id="acc-llm" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">'
      + serviceOpts + '</select></div>'
      + '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('modelOverride')) + '</label>'
      + '<input id="acc-model" value="' + escapeHtml(cfg.model || '') + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>'
      + '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('toolsCommaSeparated')) + '</label>'
      + '<input id="acc-tools" value="' + escapeHtml(toolsStr) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>'
      + '<div style="margin-bottom:8px;">'
      + '<label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('maxIterationsAgentLoop')) + '</label>'
      + '<input id="acc-depth" type="number" value="' + (cfg.max_depth || 1000) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/>'
      + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
      + '<button onclick="document.getElementById(\'agentConvConfigOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="acc-save" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button>'
      + '</div>';
    panel.innerHTML = html;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    document.getElementById('acc-save').onclick = function() {
      var llm = document.getElementById('acc-llm').value;
      var model = document.getElementById('acc-model').value;
      var tools = document.getElementById('acc-tools').value
        .split(',').map(function(s) { return s.trim(); }).filter(function(s) { return s; });
      var depth = parseInt(document.getElementById('acc-depth').value) || 1000;
      // Collect params — name is always the instance name
      var params = { name: agentName };
      panel.querySelectorAll('[data-param]').forEach(function(inp) {
        params[inp.dataset.param] = inp.value;
      });
      action$('update_agent_conv_config', {
        name: agentName, conversation_id: conversationId,
        config: { llm_service: llm, model: model, tools: tools,
                   max_depth: depth, params: params },
      }).subscribe(function(r) {
        if (r.error) { addMsg('error', r.error); return; }
        addMsg('system', t('agentConfigUpdated', { agent: agentName }));
        overlay.remove();
        loadResources();
      });
    };
  });
}

function _showAgentSkillsDialog(agentName) {
  // Load all skills + agent's current assigned skills
  Promise.all([
    rxjs.firstValueFrom(action$('list_skills', _convScope())),
    rxjs.firstValueFrom(action$('list_agent_skills', _convScope({ agent_name: agentName }))),
  ]).then(function(results) {
    var allSkills = results[0].skills || [];
    var assigned = (results[1].skills || []).map(s => s.name);
    if (!allSkills.length) { addMsg('system', t('noSkillsCreateFirst')); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var checkboxes = allSkills.map(s => {
      var checked = assigned.indexOf(s.name) >= 0 ? ' checked' : '';
      // Invalid skills cannot be (un)assigned - disable but keep an existing
      // assignment so the diff on save does not silently unassign it.
      var cbDis = s.invalid ? ' disabled' : '';
      var color = s.invalid ? 'var(--pf-danger,#e05260)' : 'var(--pf-text)';
      var invMark = s.invalid ? ' <span style="color:var(--pf-danger,#e05260);font-size:11px;" title="' + escapeHtml(s.invalid) + '">⚠</span>' : '';
      return '<label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:' + (s.invalid ? 'default' : 'pointer') + ';font-size:13px;color:' + color + ';">'
        + '<input type="checkbox" class="agent-sk-cb" value="' + escapeHtml(s.name) + '"' + checked + cbDis + ' style="accent-color:var(--pf-accent);"/>'
        + escapeHtml(s.name) + invMark
        + (s.description ? ' <span style="color:var(--pf-muted);font-size:11px;">\u2014 ' + escapeHtml(s.description) + '</span>' : '')
        + '</label>';
    }).join('');
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:360px;">'
      + '<h3 style="margin:0 0 12px;">' + escapeHtml(t('agentSkillsTitle', { agent: agentName })) + '</h3>'
      + '<div style="max-height:200px;overflow-y:auto;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;padding:8px;">' + checkboxes + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'
      + '<button onclick="this.closest(\'.exec-overlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="_agentSkSave" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button>'
      + '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('_agentSkSave').onclick = function() {
      var newAssigned = Array.from(overlay.querySelectorAll('.agent-sk-cb:checked')).map(cb => cb.value);
      // Compute diff and send assign/unassign calls
      var toAssign = newAssigned.filter(s => assigned.indexOf(s) < 0);
      var toUnassign = assigned.filter(s => newAssigned.indexOf(s) < 0);
      overlay.remove();
      var calls = [];
      toAssign.forEach(sk => calls.push(rxjs.firstValueFrom(action$('assign_skill', { agent_name: agentName, skill_name: sk }))));
      toUnassign.forEach(sk => calls.push(rxjs.firstValueFrom(action$('unassign_skill', { agent_name: agentName, skill_name: sk }))));
      Promise.all(calls).then((results) => {
        var errors = results.filter(r => r && r.error).map(r => r.error);
        if (errors.length) {
          addMsg('error', errors.join('\n'));
          loadResources();
          return;
        }
        var msg = [];
        if (toAssign.length) msg.push(t('assignedList', { items: toAssign.join(', ') }));
        if (toUnassign.length) msg.push(t('removedList', { items: toUnassign.join(', ') }));
        if (msg.length) addMsg('system', t('agentSkillsUpdated', { agent: agentName, details: msg.join('. ') }));
        loadResources();
      });
    };
  });
}

// ── Deploy flow dialog ───────────────────────────────────────────
