      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    if (data.created) addMsg('system', `Created: ${extra.name || ''}`);
    else if (data.deleted) addMsg('system', `Deleted: ${extra.name || ''}`);
    else if (data.activated) addMsg('system', `Activated ${data.type} "${data.name}" in this conversation`);
    else if (data.deactivated) addMsg('system', `Deactivated ${data.type} "${data.name}"`);
    else if (data.shared) addMsg('system', `Shared ${data.type} "${data.name}" to conversation ${data.target.substring(0,8)}...`);
    else addMsg('system', JSON.stringify(data, null, 2));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdServiceList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'service_list', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    const svcs = data.services || [];
    if (!svcs.length) { addMsg('system', 'No services installed. Use /service install <type> <name> [key=val,...] to add one.'); return; }
    let lines = ['**Your services:**'];
    svcs.forEach(s => {
      const icon = s.connected ? '\u{1F7E2}' : (s.enabled ? '\u{1F534}' : '\u26AB');
      lines.push(`  ${icon} **${s.id}** (\`${s.type}\`) ${s.description || ''}`);
    });
    addMsg('system', lines.join('\n'));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdServiceAction(action, extra) {
  try {
    const payload = { action, conversation_id: conversationId, ...extra };
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    if (data.installed) addMsg('system', `Service '${data.id}' installed (${data.type}).`);
    else if (data.uninstalled) addMsg('system', `Service '${data.id}' uninstalled.`);
    else if (data.enabled) addMsg('system', `Service '${data.id}' enabled.`);
    else if (data.disabled) addMsg('system', `Service '${data.id}' disabled.`);
    else addMsg('system', JSON.stringify(data, null, 2));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdSkillList() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_skills', conversation_id: conversationId }),
    });
    const data = await resp.json();
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', 'No skills defined. Use /add-skill <name> <prompt>'); return; }
    let lines = ['**Your skills:**'];
    skills.forEach(s => {
      const mark = s.active ? '\\u2705' : '\\u2B1C';
      lines.push(`${mark} **${s.name}** — ${s.description || s.prompt}`);
    });
    addMsg('system', lines.join('\\n'));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListResources() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_resources', conversation_id: conversationId }),
    });
    const data = await resp.json();
    let lines = [];
    if (data.agents && data.agents.length) {
      lines.push('**Agents:**');
      data.agents.forEach(a => {
        const mark = a.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${a.name} ${a.description ? '— ' + a.description : ''}`);
      });
    }
    if (data.skills && data.skills.length) {
      lines.push('**Skills:**');
      data.skills.forEach(s => {
        const mark = s.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${s.name} ${s.description ? '— ' + s.description : ''}`);
      });
    }
    if (data.mcp_servers && data.mcp_servers.length) {
      lines.push('**MCP Servers:**');
      data.mcp_servers.forEach(m => {
        const mark = m.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${m.name} (${m.url})`);
      });
    }
    if (!lines.length) lines.push('No resources defined. Use /agent create, /add-skill, etc.');
    addMsg('system', lines.join('\\n'));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

// ── Sidebar Resources ───────────────────────────────────────────
function _scopeBadge(s) {
  if (!s) return '';
  const colors = { global: '#2d5a8e', user: '#5a2d8e', conversation: '#8e5a2d' };
  const labels = { global: 'G', user: 'U', conversation: 'C' };
  return `<span style="font-size:9px;padding:0 3px;border-radius:3px;background:${colors[s]||'#444'};color:#ccc;margin-right:3px;" title="${s}">${labels[s]||s[0]}</span>`;
}

// Collapsed state per section (persisted in localStorage)
const _collapsedSections = JSON.parse(localStorage.getItem('pawflow_collapsed_sections') || '{}');
function _toggleSection(id) {
  _collapsedSections[id] = !_collapsedSections[id];
  localStorage.setItem('pawflow_collapsed_sections', JSON.stringify(_collapsedSections));
  const el = document.getElementById('res-section-' + id);
  if (el) el.style.display = _collapsedSections[id] ? 'none' : 'block';
  const arrow = document.getElementById('res-arrow-' + id);
  if (arrow) arrow.textContent = _collapsedSections[id] ? '\u25B6' : '\u25BC';
}
// Default collapsed: variables, secrets
if (!('_param' in _collapsedSections)) _collapsedSections['_param'] = true;
if (!('_secret' in _collapsedSections)) _collapsedSections['_secret'] = true;

function _sectionHeader(title, rtype) {
  const isParamSecret = rtype === '_param' || rtype === '_secret';
  const onclick = isParamSecret
    ? `_showParamEditor('','',${rtype === '_secret'},true)`
    : `showResourceCreator('${rtype}')`;
  const collapsed = _collapsedSections[rtype] || false;
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
    <span style="cursor:pointer;color:#6c5ce7;font-weight:600;user-select:none;" onclick="_toggleSection('${rtype}')"><span id="res-arrow-${rtype}">${arrow}</span> ${title}</span>
    <span style="cursor:pointer;font-size:13px;color:#6c5ce7;padding:0 4px;" onclick="${onclick}" title="Create new">+</span>
  </div><div id="res-section-${rtype}" style="display:${collapsed ? 'none' : 'block'};">`;
}
function _sectionFooter() { return '</div>'; }

async function loadResources() {
  if (!conversationId) { document.getElementById('resourcesPanel').style.display = 'none'; return; }
  document.getElementById('resourcesPanel').style.display = 'block';
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_resources', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { console.warn('[loadResources] error:', data.error); return; }
    // Store user role for permission checks (admin can edit globals)
    window._userRole = data.user_role || 'viewer';
    // Load tool schemas (async, don't block rendering)
    if (!window._cachedTools) {
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'get_tool_schemas', conversation_id: conversationId }),
      }).then(r => r.json()).then(d => {
        window._cachedTools = d.tools || [];
        // Re-render to show tools
        const toolSection = document.querySelector('[data-section="_tool"]');
        if (toolSection) loadResources();
      }).catch(() => {});
    }
    const el = document.getElementById('resourcesContent');
    let html = '';
    // Agents
    if (data.agents && data.agents.length) {
      html += _sectionHeader('Agents', 'agent');
      data.agents.forEach(a => {
        const active = a.active;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showResourceMenu(event,'agent','${a.name}','${a.scope||''}','${a.autoconv||''}');return false;">
          <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'agent',name:'${a.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
          ${_scopeBadge(a.scope)}<span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;cursor:pointer;" onclick="cmdAgentSelect('${a.name}')">${a.name}</span>${a.autoconv ? '<span style="font-size:9px;color:#4ecdc4;margin-left:4px;" title="Autoconv: ' + a.autoconv + '">\u{1F504} ' + a.autoconv + '</span>' : ''}
        </div>`;
      });
      html += _sectionFooter();
    }
    // Skills (always show header + [+] even when empty)
    html += _sectionHeader('Skills', 'skill');
    (data.skills || []).forEach(s => {
      const active = s.active;
      html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showResourceMenu(event,'skill','${s.name}','${s.scope||''}');return false;">
        <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'skill',name:'${s.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
        ${_scopeBadge(s.scope)}<span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${s.name}</span>
      </div>`;
    });
    html += _sectionFooter();
    // MCP (always show header)
    html += _sectionHeader('MCP', 'mcp');
    (data.mcp_servers || []).forEach(m => {
      const active = m.active;
      html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showResourceMenu(event,'mcp','${m.name}','${m.scope||''}');return false;">
        <span style="cursor:pointer;font-size:11px;" onclick="cmdResourceAction('${active ? 'deactivate_resource' : 'activate_resource'}',{resource_type:'mcp',name:'${m.name}'}).then(loadResources)">${active ? '\u2705' : '\u2B1C'}</span>
        ${_scopeBadge(m.scope)}<span style="color:${active ? '#e0e0e0' : '#666'};font-size:12px;">${m.name}</span>
      </div>`;
    });
    html += _sectionFooter();
    // Tools (builtin + dynamic — clickable to open call dialog)
    html += _sectionHeader('Tools', '_tool');
    if (!_collapsedSections['_tool']) {
      const tools = window._cachedTools || [];
      tools.forEach(t => {
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer" onclick="showToolCallDialog('${escapeHtml(t.name)}')">
          <span style="color:#6c5ce7;font-size:11px">\u26A1</span>
          <span style="font-size:12px;color:#c0c0d0">${escapeHtml(t.name)}</span>
        </div>`;
      });
      if (!tools.length) html += '<div style="margin-left:8px;font-size:11px;color:#666">Loading...</div>';
    }
    html += _sectionFooter();
    // Task definitions (always show header)
    html += _sectionHeader('Tasks', 'task_def');
    (data.task_defs || []).forEach(t => {
      html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showResourceMenu(event,'task_def','${t.name}','${t.scope||''}');return false;">
        ${_scopeBadge(t.scope)}<span style="color:#8888aa;font-size:12px;cursor:default;" title="${escapeHtml(t.description)}">${t.name}</span>
        <span style="color:#555;font-size:10px;">[${t.default_interval}]</span>
      </div>`;
    });
    html += _sectionFooter();
    // Running task instances
    if (data.running_tasks && data.running_tasks.length) {
      html += _sectionHeader('Running Tasks', '_running');
      data.running_tasks.forEach(t => {
        const statusColor = t.status === 'active' ? '#4ecdc4' : t.status === 'paused' ? '#f0ad4e' : '#666';
        const statusIcon = t.status === 'active' ? '\u25B6' : t.status === 'paused' ? '\u23F8' : '\u23F9';
        const label = (t.task_def_name || t.task.substring(0, 30)) + ' \u2192 ' + t.agent;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showRunningTaskMenu(event,'${t.task_id}','${t.agent}','${t.status}');return false;">
          <span style="color:${statusColor};font-size:11px;">${statusIcon}</span>
          <span style="color:#8888aa;font-size:11px;" title="${escapeHtml(t.task)}">${escapeHtml(label)}</span>
          <span style="color:#555;font-size:10px;">[${t.iterations}/${t.max_iterations}]</span>
        </div>`;
      });
      html += _sectionFooter();
    }
    // Services (always show for [+] install button)
    html += _sectionHeader('Services', '_svc');
    if (data.services && data.services.length) {
      data.services.forEach(s => {
        const statusDot = s.enabled ? '\u{1F7E2}' : '\u{1F534}';
        const svcCtx = ` oncontextmenu="showServiceMenu(event,'${s.service_id}','${s.scope}',${s.enabled});return false;"`;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${svcCtx}>
          ${_scopeBadge(s.scope)}<span style="color:#8888aa;font-size:11px;">${statusDot} <b>${s.service_id}</b> <span style="color:#555">(${s.service_type})</span></span>
        </div>`;
      });
    } else {
      html += '<div style="color:#555;font-size:10px;margin-left:8px;">No services installed</div>';
    }
    html += _sectionFooter();
    // Deployed flows (always show section for [+] deploy button)
    html += _sectionHeader('Flows', '_flow');
    if (data.flows && data.flows.length) {
      data.flows.forEach(f => {
        const statusIcon = f.status === 'running' ? '\u25B6' : f.status === 'stopped' ? '\u23F9' : '\u26A0';
        const statusColor = f.status === 'running' ? '#4ecdc4' : f.status === 'stopped' ? '#666' : '#e94560';
        const flowCtx = ` oncontextmenu="showFlowInstanceMenu(event,'${f.instance_id}','${f.status}','${f.scope}');return false;"`;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${flowCtx}>
          ${_scopeBadge(f.scope)}<span style="color:${statusColor};font-size:11px;">${statusIcon} ${f.flow_name || f.instance_id}</span>
        </div>`;
      });
    } else {
      html += '<div style="color:#555;font-size:10px;margin-left:8px;">No deployed flows</div>';
    }
    html += _sectionFooter();
    // Variables & Secrets (separate fetch)
    try {
      const psResp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'list_params_secrets', conversation_id: conversationId }),
      });
      const ps = await psResp.json();
      if (ps.parameters && ps.parameters.length) {
        html += _sectionHeader('Variables', '_param');
        ps.parameters.forEach(p => {
          const truncVal = p.value.length > 30 ? p.value.substring(0, 30) + '...' : p.value;
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${p.key}','${p.scope}');return false;">
            ${_scopeBadge(p.scope)}<span style="color:#8888aa;font-size:11px;"><b>${escapeHtml(p.key)}</b> = ${escapeHtml(truncVal)}</span>
          </div>`;
        });
        html += _sectionFooter();
      }
      if (ps.secrets && ps.secrets.length) {
        html += _sectionHeader('Secrets', '_secret');
        ps.secrets.forEach(s => {
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${s.key}','${s.scope}',true);return false;">
            ${_scopeBadge(s.scope)}<span style="color:#8888aa;font-size:11px;"><b>${escapeHtml(s.key)}</b> = ********</span>
          </div>`;
        });
        html += _sectionFooter();
      }
    } catch (_) {}

    // Linked Accounts section
    try {
      const linksResp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'list_linked_accounts', conversation_id: conversationId }),
      });
      const linksData = await linksResp.json();
      const links = linksData.links || {};
      const linkKeys = Object.keys(links);
      html += '<div style="margin-top:6px;padding:4px 6px;font-size:11px;color:#888;border-top:1px solid #222;">';
      html += '<b>Linked Accounts</b>';
      if (linkKeys.length) {
        linkKeys.forEach(provider => {
          html += `<div style="display:flex;align-items:center;gap:6px;margin:3px 0 3px 8px;">
            <span style="font-size:11px;color:#e0e0e0;">${escapeHtml(provider)}</span>
            <span style="font-size:10px;color:#666;">${escapeHtml(links[provider])}</span>
            <span style="cursor:pointer;font-size:10px;color:#e94560;" title="Unlink" onclick="cmdResourceAction('unlink_account',{provider:'${provider}'}).then(loadResources)">\u2715</span>
          </div>`;
        });
      } else {
        html += '<div style="color:#555;font-size:10px;margin-left:8px;">No linked accounts</div>';
      }
      html += '</div>';
    } catch (_la) {}

    if (!html) html = '<div style="color:#555;font-size:11px;">No resources. Use [+] or /agent create, /task create</div>';
    el.innerHTML = html;
  } catch (e) {
    document.getElementById('resourcesContent').innerHTML = '';
  }
}

// ── Resource context menu ─────────────────────────────────────────
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
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
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
  const sep = () => {
    const s = document.createElement('div');
    s.style.cssText = 'height:1px;background:#333;margin:4px 0;';
    menu.appendChild(s);
  };

  // View config — always available (read-only for non-admin on globals)
  item('\u{1F441} View...', () => showResourceEditor(rtype, name, !_canEditScope(scope)));
  // Edit — admin can edit globals, owners can edit their own
  if (_canEditScope(scope)) {
    item('\u270F Edit...', () => showResourceEditor(rtype, name));
  }
  if (rtype === 'agent') {
    item('\u25B6 Select', () => cmdAgentSelect(name));
    if (autoconv) {
      item('\u23F9 Autoconv off', () => {
        fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'random_thought', sub: 'off', agent: name, conversation_id: conversationId }),
        }).then(r => r.json()).then(d => {
          addMsg('system', d.error || 'Autoconv disabled for ' + name);
          loadResources();
        }).catch(e => addMsg('error', e.message));
      });
    } else {
      item('\u{1F504} Autoconv on...', () => {
        const freq = prompt('Frequency (e.g. 6/1m, 2-3/h, 1/2h):', '6/1m');
        if (!freq) return;
        fetch(API, { method: 'POST', headers: getAuthHeaders(),
          body: JSON.stringify({ action: 'random_thought', sub: 'on', agent: name, frequency: freq, conversation_id: conversationId }),
        }).then(r => r.json()).then(d => {
          addMsg('system', d.error || 'Autoconv enabled for ' + name + ' (' + freq + ')');
          loadResources();
        }).catch(e => addMsg('error', e.message));
      });
    }
  }
  if (rtype === 'task_def') {
    item('\u25B6 Assign to agent...', () => _showAssignDialog(name));
  }
  sep();
  // Copy between scopes
  if (_isAdmin()) item('\u2191 Copy to Global', () => _copyResource(rtype, name, 'global'));
  if (scope !== 'user') item('\u2191 Copy to User', () => _copyResource(rtype, name, 'user'));
  if (scope !== 'conversation') item('\u2191 Copy to Conversation', () => _copyResource(rtype, name, 'conversation'));
  if (_canEditScope(scope)) {
    sep();
    item('\u{1F5D1} Delete', () => _deleteResource(rtype, name, scope), true);
  }

  setTimeout(() => document.addEventListener('click', function _close() {
    menu.remove(); document.removeEventListener('click', _close);
  }), 0);
}

function _copyResource(rtype, name, targetScope) {
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'copy_resource_scope', resource_type: rtype,
      name, target_scope: targetScope, conversation_id: conversationId }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', `${rtype} '${name}' copied to ${targetScope}.`);
    loadResources();
  }).catch(e => addMsg('error', e.message));
}

function _deleteResource(rtype, name, scope) {
  if (!confirm(`Delete ${rtype} '${name}' (${scope})?`)) return;
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'delete_resource', resource_type: rtype,
      name, scope: scope || 'user' }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', `${rtype} '${name}' deleted.`);
    loadResources();
  }).catch(e => addMsg('error', e.message));
}

// ── Deploy flow dialog ───────────────────────────────────────────
async function showDeployFlowDialog() {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">Deploy Flow</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div><div style="color:#888;font-size:12px;">Loading templates...</div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_available_flows' }) });
    const data = await resp.json();
    const templates = data.templates || [];
    if (!templates.length) {
      panel.querySelector('div:last-child').innerHTML = '<div style="color:#888;font-size:12px;">No flow templates found in flows/ directory.</div>';
      return;
    }
    let optionsHtml = templates.map(t =>
      `<option value="${t.id}" data-scope="${t.scope || 'independent'}">${t.name} (${t.tasks_count} tasks)${t.version ? ' v' + t.version : ''} [${t.scope || 'independent'}]</option>`
    ).join('');
    panel.querySelector('div:last-child').innerHTML = `
      <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Template</label>
        <select id="deploy-template" onchange="_onDeployTemplateChange()" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">${optionsHtml}</select></div>
      <div id="deploy-scope-info" style="margin-bottom:8px;font-size:11px;color:#aaa;"></div>
      <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Deploy scope</label>
        <select id="deploy-scope" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">
          <option value="user">User</option>
          <option value="conversation">Conversation</option>
        </select></div>
      <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Parameters (JSON, optional)</label>
        <textarea id="deploy-params" placeholder='{"key": "value"}' style="width:100%;min-height:60px;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;"></textarea></div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
        <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
        <button onclick="_submitDeployFlow()" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Deploy</button>
      </div>`;
  } catch (e) {
    panel.querySelector('div:last-child').innerHTML = '<div style="color:#e94560;">Error loading templates: ' + e.message + '</div>';
  }
}
function _submitDeployFlow() {
  const templateId = document.getElementById('deploy-template').value;
  const scope = document.getElementById('deploy-scope').value;
  let params = {};
  const paramsText = (document.getElementById('deploy-params').value || '').trim();
  if (paramsText) {
    try { params = JSON.parse(paramsText); } catch { alert('Invalid JSON in parameters'); return; }
  }
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'deploy_flow', template_id: templateId, scope, parameters: params, conversation_id: conversationId }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `Flow deployed: ${d.instance_id} (${scope})`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  }).catch(e => addMsg('error', e.message));
}
function _onDeployTemplateChange() {
  var sel = document.getElementById('deploy-template');
  var opt = sel.options[sel.selectedIndex];
  var flowScope = opt ? opt.getAttribute('data-scope') || 'independent' : 'independent';
  var info = document.getElementById('deploy-scope-info');
  var scopeSel = document.getElementById('deploy-scope');
  if (flowScope === 'conversation') {
    info.innerHTML = '<span style="color:#f4a261;">This flow requires a conversation context.</span>';
    scopeSel.value = 'conversation';
    scopeSel.disabled = true;
  } else if (flowScope === 'user') {
    info.innerHTML = '<span style="color:#58a6ff;">This flow requires a user context.</span>';
    scopeSel.disabled = false;
  } else {
    info.innerHTML = '<span style="color:#3fb950;">Independent flow — no runtime dependencies.</span>';
    scopeSel.disabled = false;
  }
}
// Trigger on initial load
setTimeout(function() { if (document.getElementById('deploy-template')) _onDeployTemplateChange(); }, 100);

// ── Resource editor overlay ───────────────────────────────────────
const _RESOURCE_FIELDS = {
  agent:    [['prompt','textarea'],['description','text'],['llm_service','text'],['model','text'],['tools','text'],['max_depth','number'],['timeout','number']],
  skill:    [['prompt','textarea'],['description','text']],
  mcp:      [['url','text'],['auth','text'],['description','text']],
  task_def: [['prompt','textarea'],['criteria','textarea'],['default_interval','text'],['verifier','text'],['description','text']],
  prompt:   [['content','textarea'],['title','text'],['category','text'],['description','text']],
};

function _buildResourceForm(rtype, data, isNew, readonly) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  let html = '';
  if (isNew) {
    html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Name</label><input id="res-name" value="" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>';
    html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Scope</label><select id="res-scope" style="background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">'
      + (_isAdmin() ? '<option value="global">Global</option>' : '')
      + '<option value="user">User</option><option value="conversation">Conversation</option></select></div>';
  }
  for (const [key, type] of fields) {
    const val = (data && data[key] != null) ? data[key] : '';
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    html += `<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">${key}</label>`;
    if (type === 'textarea') {
      html += `<textarea id="res-${key}"${dis} style="width:100%;min-height:120px;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;resize:vertical;${roS}">${escaped}</textarea>`;
    } else if (type === 'number') {
      html += `<input id="res-${key}" type="number" value="${escaped}"${dis} style="width:80px;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;${roS}"/>`;
    } else {
      html += `<input id="res-${key}" value="${escaped}"${dis} style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;${roS}"/>`;
    }
    html += '</div>';
  }
  return html;
}

async function showResourceEditor(rtype, name, readonly) {
  // Fetch current data
  let data = {};
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_resource_detail', resource_type: rtype, name, conversation_id: conversationId }),
    });
    data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
  } catch (e) { addMsg('error', e.message); return; }

  const scope = data._scope || 'user';
  const ro = !!readonly;
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  const title = ro ? 'View' : 'Edit';
  let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${title} ${rtype}: ${name} ${_scopeBadge(scope)}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, data, false, ro);
  if (ro) {
    html += `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Close</button>
    </div>`;
  } else {
    html += `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
    <button onclick="_saveResourceEdit('${rtype}','${name}','${scope}')" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Save</button>
    </div>`;
  }
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _saveResourceEdit(rtype, name, scope) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    const el = document.getElementById('res-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'update_resource', resource_type: rtype, name, scope, data }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `${rtype} '${name}' updated.`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  }).catch(e => addMsg('error', e.message));
}

function showResourceCreator(rtype) {
  if (rtype === '_flow') { showDeployFlowDialog(); return; }
  if (rtype === '_svc') { showServiceInstallForm(); return; }
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">New ${rtype}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, {}, true)
    + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
    <button onclick="_saveResourceCreate('${rtype}')" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Create</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _saveResourceCreate(rtype) {
  const nameEl = document.getElementById('res-name');
  const scopeEl = document.getElementById('res-scope');
  const name = (nameEl && nameEl.value || '').trim();
  const scope = scopeEl ? scopeEl.value : 'user';
  if (!name) { alert('Name is required'); return; }
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    const el = document.getElementById('res-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'create_resource', resource_type: rtype, name, scope, data }),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `${rtype} '${name}' created.`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  }).catch(e => addMsg('error', e.message));
}

// ── Param/Secret context menu + create ────────────────────────────
// ── Service context menu ──────────────────────────────────────────
// ── Assign task dialog (agent + variables) ────────────────────────
function _showAssignDialog(taskDefName) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:420px;border:1px solid #333;';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">Assign: ${escapeHtml(taskDefName)}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>
  <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Agent</label>
    <input id="assign-agent" value="" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>
  <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Context mode</label>
    <select id="assign-context" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">
      <option value="isolated">isolated (default — only task prompt)</option>
      <option value="last:10">last:10 (last 10 messages)</option>
      <option value="last:20">last:20 (last 20 messages)</option>
      <option value="last:50">last:50 (last 50 messages)</option>
      <option value="summary:2000">summary:2000 (summarized ~2000 tokens)</option>
      <option value="summary:4000">summary:4000 (summarized ~4000 tokens)</option>
      <option value="full">full (entire conversation context)</option>
    </select></div>
  <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Interval (optional override)</label>
    <input id="assign-interval" placeholder="e.g. 6/1m, 2/1h, 60" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>
  <div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Variables (key=value, one per line)</label>
    <textarea id="assign-vars" placeholder="nbr_images=20&#10;style=cyberpunk" style="width:100%;min-height:60px;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;"></textarea></div>
  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
    <button onclick="_submitAssign('${taskDefName}')" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Assign</button>
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
  if (!agent) { alert('Agent is required'); return; }
  const body = { action: 'assign_task', conversation_id: conversationId,
    agent_name: agent, task_def_name: taskDefName };
  if (context && context !== 'isolated') body.context = context;
  if (interval) body.interval = interval;
  if (varsText) {
    const variables = {};
    for (const line of varsText.split('\n')) {
      const eq = line.indexOf('=');
      if (eq > 0) variables[line.substring(0, eq).trim()] = line.substring(eq + 1).trim();
    }
    if (Object.keys(variables).length) body.variables = variables;
  }
  fetch(API, { method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify(body),
  }).then(r => r.json()).then(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', d.result || 'Task assigned.'); loadResources(); }
    document.getElementById('resourceEditorOverlay').remove();
  }).catch(e => addMsg('error', e.message));
}

// ── Running task context menu ─────────────────────────────────────
function showRunningTaskMenu(e, taskId, agent, status) {
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
  const _taskAction = (action) => {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: action + '_task', conversation_id: conversationId, task_id: taskId }),
    }).then(r => r.json()).then(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', `Task ${taskId} ${action}d.`);
      loadResources();
    }).catch(e => addMsg('error', e.message));
  };
  if (status === 'active') {
    item('\u23F8 Pause', () => _taskAction('pause'));
  } else if (status === 'paused') {
    item('\u25B6 Resume', () => _taskAction('resume'));
  }
  const sep = document.createElement('div');
  sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
  menu.appendChild(sep);
  item('\u{1F5D1} Cancel', () => _taskAction('cancel'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

function showServiceMenu(e, serviceId, scope, enabled) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,0.5);';
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
  item('\u{1F441} View config...', () => showServiceEditForm(serviceId, scope, !_canEditScope(scope)));
  if (_canEditScope(scope)) {
    item('\u270F Edit...', () => showServiceEditForm(serviceId, scope));
  }
  item(enabled ? '\u23F8 Disable' : '\u25B6 Enable', () => {
    fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'toggle_service', service_id: serviceId, enabled: !enabled }),
    }).then(r => r.json()).then(d => {
      if (d.error) addMsg('error', d.error);
      else loadResources();
    }).catch(e => addMsg('error', e.message));
  });
  if (_canEditScope(scope)) {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
    menu.appendChild(sep);
    item('\u{1F5D1} Delete', () => {
      if (!confirm(`Delete service '${serviceId}'?`)) return;
      fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'delete_service', service_id: serviceId, scope }),
    }).then(r => r.json()).then(d => {
      if (d.error) addMsg('error', d.error);
      else { addMsg('system', `Service '${serviceId}' deleted.`); loadResources(); }
    }).catch(e => addMsg('error', e.message));
    }, true);
  }
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

// ── Service schema-based form helpers ─────────────────────────────
const _svcInputStyle = 'width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-size:12px;';
const _svcLabelStyle = 'color:#aaa;font-size:11px;';
const _svcDescStyle = 'color:#666;font-size:10px;margin-top:1px;';

function _renderSchemaFields(schema, values, readonly) {
  let html = '';
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  for (const [pname, pdef] of Object.entries(schema)) {
    const val = (values && values[pname] != null) ? values[pname] : (pdef.default != null ? pdef.default : '');
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    html += '<div class="svc-field" data-field="' + pname + '" style="margin-bottom:8px;">';
    html += '<label style="' + _svcLabelStyle + '">' + pname + '</label>';
    if (pdef.description) html += '<div style="' + _svcDescStyle + '">' + pdef.description + '</div>';
    const ptype = pdef.type || 'string';
    if (ptype === 'boolean') {
      html += '<label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;"><input id="svc-p-' + pname + '" type="checkbox"' + (val ? ' checked' : '') + dis + ' style="accent-color:#6c5ce7;"/> <span style="color:#e0e0e0;font-size:12px;">Enabled</span></label>';
    } else if (ptype === 'select' && pdef.options) {
      html += '<select id="svc-p-' + pname + '"' + dis + ' style="' + _svcInputStyle + roS + '">';
      for (const opt of pdef.options) {
        html += '<option value="' + opt + '"' + (String(val) === String(opt) ? ' selected' : '') + '>' + opt + '</option>';
      }
      html += '</select>';
    } else if (ptype === 'textarea' || ptype === 'map' || ptype === 'object') {
      const tval = (ptype === 'map' || ptype === 'object') && typeof val === 'object' ? JSON.stringify(val, null, 2) : escaped;
      html += '<textarea id="svc-p-' + pname + '"' + dis + ' style="' + _svcInputStyle + roS + 'min-height:80px;font-family:monospace;resize:vertical;">' + tval + '</textarea>';
    } else if (ptype === 'integer' || ptype === 'float') {
      html += '<input id="svc-p-' + pname + '" type="number"' + (ptype === 'float' ? ' step="any"' : '') + ' value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + 'width:120px;"/>';
    } else if (pdef.sensitive) {
      html += '<div style="display:flex;gap:4px;align-items:center;">'
        + '<input id="svc-p-' + pname + '" type="password" value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + 'flex:1;"/>'
        + '<button type="button" onclick="_togglePwdVis(\'svc-p-' + pname + '\',this)" style="background:none;border:1px solid #333;color:#888;border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px;" title="Show/hide">\u{1F441}</button>'
        + '</div>';
    } else {
      html += '<input id="svc-p-' + pname + '" type="text" value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + '"/>';
    }
    html += '</div>';
  }
  return html;
}

function _collectSchemaValues(schema) {
  const config = {};
  for (const [pname, pdef] of Object.entries(schema)) {
    const el = document.getElementById('svc-p-' + pname);
    if (!el) continue;
    const ptype = pdef.type || 'string';
    if (ptype === 'boolean') {
      config[pname] = el.checked;
    } else if (ptype === 'integer') {
      config[pname] = parseInt(el.value) || 0;
    } else if (ptype === 'float') {
      config[pname] = parseFloat(el.value) || 0;
    } else if (ptype === 'map' || ptype === 'object') {
      try { config[pname] = JSON.parse(el.value || '{}'); } catch { config[pname] = el.value; }
    } else {
      config[pname] = el.value;
    }
  }
  return config;
}

function _applyRules(container, rules, actions, serviceId) {
  if (!rules || !rules.length) return;
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
      if (lbl) lbl.querySelector('.svc-req')?.remove();
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
            lbl.insertAdjacentHTML('beforeend', ' <span class="svc-req" style="color:#e94560">*</span>');
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

function _renderServiceActions(actions, serviceId) {
  if (!actions || !actions.length) return '';
  let html = '<div class="svc-actions" style="margin-top:12px;padding-top:8px;border-top:1px solid #333;">';
  for (const a of actions) {
    const whenAttr = a.when ? ' data-action-when=\'' + JSON.stringify(a.when).replace(/'/g, '&#39;') + '\'' : '';
    html += '<button type="button" onclick="_executeServiceAction(\'' + a.id + '\',\'' + serviceId + '\',\'' + (a.flow || 'simple') + '\',\'' + (a.server_action || '') + '\')"'
      + whenAttr + ' style="background:#1a1a3e;color:#6c5ce7;border:1px solid #6c5ce7;border-radius:4px;padding:6px 12px;cursor:pointer;font-size:12px;margin-right:8px;">'
      + (a.icon || '') + ' ' + (a.label || a.id) + '</button>';
  }
  html += '</div>';
  return html;
}

async function _executeServiceAction(actionId, serviceId, flow, serverAction) {
  const btn = event && event.target ? event.target : null;
  if (flow === 'oauth_code') {
    try {
      // Step 1: get instructions
      const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: serverAction, service_id: serviceId })
      }).then(r => r.json());
      if (resp.error) { addMsg('error', resp.error); return; }

      // Step 2: show instructions + textarea for credentials
      const container = btn ? btn.parentElement : null;
      if (container) {
        const loginDiv = document.createElement('div');
        loginDiv.style.cssText = 'margin-top:8px;';
        loginDiv.innerHTML = '<div style="color:#aaa;font-size:11px;white-space:pre-line;margin-bottom:6px;">' + escapeHtml(resp.message) + '</div>'
          + '<textarea id="svc-creds-input" placeholder="Paste .credentials.json content here..." '
          + 'style="' + _svcInputStyle + 'min-height:80px;font-family:monospace;font-size:11px;"></textarea>'
          + '<button type="button" id="svc-creds-submit" style="background:#6c5ce7;color:white;border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;margin-top:4px;">Save Credentials</button>';
        container.appendChild(loginDiv);

        const submitBtn = document.getElementById('svc-creds-submit');
        submitBtn.addEventListener('click', async () => {
          const creds = document.getElementById('svc-creds-input').value.trim();
          if (!creds) return;
          submitBtn.textContent = '...';
          submitBtn.disabled = true;
          try {
            const result = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
              body: JSON.stringify({ action: serverAction.replace('_url', '_code'),
                                     service_id: serviceId, credentials: creds })
            }).then(r => r.json());
            if (result.ok) {
              loginDiv.innerHTML = '<span style="color:#2ecc71;font-size:12px;">\u2714 ' + (result.message || 'Saved!') + '</span>';
            } else {
              submitBtn.textContent = 'Save Credentials';
              submitBtn.disabled = false;
              loginDiv.insertAdjacentHTML('beforeend',
                '<div style="color:#e94560;font-size:11px;margin-top:4px;">' + escapeHtml(result.error) + '</div>');
            }
          } catch (e) {
            loginDiv.innerHTML = '<span style="color:#e94560;font-size:12px;">\u2718 ' + e.message + '</span>';
          }
        });
      }
    } catch (e) { addMsg('error', 'Action failed: ' + e.message); }
  } else {
    if (flow === 'confirm' && !confirm('Execute ' + actionId + '?')) return;
    try {
      const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: serverAction, service_id: serviceId })
      }).then(r => r.json());
      addMsg('system', resp.message || resp.error || JSON.stringify(resp));
    } catch (e) { addMsg('error', e.message); }
  }
}

// Legacy compat
function _applyShowWhen(container) { /* replaced by _applyRules */ }

let _svcSchemaCache = {};

async function _fetchServiceSchema(serviceType) {
  if (_svcSchemaCache[serviceType]) return _svcSchemaCache[serviceType];
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_service_schema', service_type: serviceType }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return {parameters: {}, rules: [], actions: []}; }
    _svcSchemaCache[serviceType] = {
      parameters: data.parameters || {},
      rules: data.rules || [],
      actions: data.actions || [],
    };
    return _svcSchemaCache[serviceType];
  } catch (e) { addMsg('error', e.message); return {parameters: {}, rules: [], actions: []}; }
}

async function showServiceInstallForm() {
  let serviceTypes = [];
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_service_types', conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    serviceTypes = data.service_types || [];
  } catch (e) { addMsg('error', e.message); return; }
  if (!serviceTypes.length) { addMsg('error', 'No service types available.'); return; }

  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid #333;';

  let typeOpts = '';
  for (const st of serviceTypes) {
    typeOpts += '<option value="' + st.type + '">' + st.name + (st.description ? ' - ' + st.description : '') + '</option>';
  }

  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:14px;">Install Service</h3>'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>'
    + '</div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">Name <span style="color:#e94560;">*</span></label>'
    + '<input id="svc-install-name" style="' + _svcInputStyle + '" placeholder="my_service"/></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">Type <span style="color:#e94560;">*</span></label>'
    + '<select id="svc-install-type" style="' + _svcInputStyle + '">' + typeOpts + '</select></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">Description</label>'
    + '<input id="svc-install-desc" style="' + _svcInputStyle + '" placeholder="Optional description"/></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">Scope</label>'
    + '<select id="svc-install-scope" style="' + _svcInputStyle + '">'
    + (_isAdmin() ? '<option value="global">Global</option>' : '')
    + '<option value="user">User</option></select></div>'
    + '<div id="svc-install-params" style="border-top:1px solid #333;padding-top:8px;margin-top:8px;"></div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
    + '<button id="svc-install-btn" onclick="_submitServiceInstall()" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Install</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  const typeSelect = document.getElementById('svc-install-type');
  const loadParams = async () => {
    const paramsDiv = document.getElementById('svc-install-params');
    paramsDiv.innerHTML = '<div style="color:#666;font-size:11px;">Loading parameters...</div>';
    const schemaData = await _fetchServiceSchema(typeSelect.value);
    panel.dataset.schema = JSON.stringify(schemaData.parameters || {});
    panel.dataset.rules = JSON.stringify(schemaData.rules || []);
    panel.dataset.actions = JSON.stringify(schemaData.actions || []);
    const params = schemaData.parameters || {};
    if (Object.keys(params).length === 0) {
      paramsDiv.innerHTML = '<div style="color:#666;font-size:11px;">No configurable parameters for this service type.</div>';
    } else {
      paramsDiv.innerHTML = '<div style="color:#8888aa;font-size:11px;margin-bottom:6px;font-weight:600;">Parameters</div>'
        + _renderSchemaFields(params, {})
        + _renderServiceActions(schemaData.actions || [], '');
      _applyRules(paramsDiv, schemaData.rules || [], schemaData.actions || [], '');
    }
  };
  typeSelect.addEventListener('change', loadParams);
  await loadParams();
  document.getElementById('svc-install-name').focus();
}

async function _submitServiceInstall() {
  const name = (document.getElementById('svc-install-name').value || '').trim();
  const svcType = document.getElementById('svc-install-type').value;
  const desc = (document.getElementById('svc-install-desc').value || '').trim();
  const scope = document.getElementById('svc-install-scope').value;
  if (!name) { alert('Service name is required'); return; }
  const panel = document.querySelector('#resourceEditorOverlay > div');
  const schema = JSON.parse(panel.dataset.schema || '{}');
  const config = _collectSchemaValues(schema);
  const btn = document.getElementById('svc-install-btn');
  btn.disabled = true; btn.textContent = 'Installing...';
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'service_install', service_name: name, service_type: svcType, description: desc, config, scope, conversation_id: conversationId }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); btn.disabled = false; btn.textContent = 'Install'; return; }
    addMsg('system', 'Service \'' + name + '\' installed successfully.');
    document.getElementById('resourceEditorOverlay').remove();
    loadResources();
  } catch (e) { addMsg('error', e.message); btn.disabled = false; btn.textContent = 'Install'; }
}

async function showServiceEditForm(serviceId, scope, readonly) {
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_service_detail', service_id: serviceId, scope }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }

    const svcType = data.service_type || '';
    const config = data.config || {};
    const schemaData = await _fetchServiceSchema(svcType);
    const schema = schemaData.parameters || schemaData;  // compat: old format was just params
    const rules = schemaData.rules || [];
    const actions = schemaData.actions || [];
    const ro = !!readonly;
    const disabledAttr = ro ? ' disabled' : '';
    const roStyle = ro ? 'opacity:0.7;cursor:not-allowed;' : '';

    let overlay = document.getElementById('resourceEditorOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'resourceEditorOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid #333;';

    const title = ro ? 'View Service: ' : 'Edit Service: ';
    let formHtml = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
      + '<h3 style="margin:0;color:#e0e0e0;font-size:14px;">' + title + serviceId + ' ' + _scopeBadge(scope) + '</h3>'
      + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>'
      + '</div>';
    formHtml += '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">Type</label>'
      + '<input value="' + svcType + '" disabled style="' + _svcInputStyle + 'opacity:0.6;cursor:not-allowed;"/></div>';

    if (Object.keys(schema).length > 0) {
      formHtml += '<div style="border-top:1px solid #333;padding-top:8px;margin-top:8px;">'
        + '<div style="color:#8888aa;font-size:11px;margin-bottom:6px;font-weight:600;">Parameters</div>'
        + _renderSchemaFields(schema, config, ro)
        + (ro ? '' : _renderServiceActions(actions, serviceId))
        + '</div>';
    } else {
      for (const [k, v] of Object.entries(config)) {
        const isSecret = k.toLowerCase().includes('key') || k.toLowerCase().includes('secret') || k.toLowerCase().includes('token');
        const inputType = isSecret ? 'password' : 'text';
        const val = String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
        if (isSecret) {
          formHtml += '<div style="margin-bottom:6px;"><label style="' + _svcLabelStyle + '">' + k + '</label>'
            + '<div style="display:flex;gap:4px;align-items:center;">'
            + '<input id="svc-p-' + k + '" type="password" value="' + val + '"' + disabledAttr + ' style="' + _svcInputStyle + roStyle + 'flex:1;"/>'
            + '<button type="button" onclick="_togglePwdVis(\'svc-p-' + k + '\',this)" style="background:none;border:1px solid #333;color:#888;border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px;" title="Show/hide">\u{1F441}</button>'
            + '</div></div>';
        } else {
          formHtml += '<div style="margin-bottom:6px;"><label style="' + _svcLabelStyle + '">' + k + '</label>'
            + '<input id="svc-p-' + k + '" type="text" value="' + val + '"' + disabledAttr + ' style="' + _svcInputStyle + roStyle + '"/></div>';
        }
      }
    }

    if (!ro) {
      formHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
        + '<button id="svc-save-btn" onclick="_submitServiceEdit(\'_SVC_ID_\',\'_SVC_SCOPE_\')" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Save</button>'
        + '</div>';
      formHtml = formHtml.replace('_SVC_ID_', serviceId).replace('_SVC_SCOPE_', scope);
    } else {
      formHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Close</button>'
        + '</div>';
    }

    panel.innerHTML = formHtml;
    const effectiveSchema = Object.keys(schema).length > 0 ? schema : Object.fromEntries(Object.keys(config).map(k => [k, {type: 'string'}]));
    panel.dataset.schema = JSON.stringify(effectiveSchema);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    panel.dataset.rules = JSON.stringify(rules);
    panel.dataset.actions = JSON.stringify(actions);
    _applyRules(panel, rules, actions, serviceId);
  } catch (e) { addMsg('error', e.message); }
}

async function _submitServiceEdit(serviceId, scope) {
  const panel = document.querySelector('#resourceEditorOverlay > div');
  const schema = JSON.parse(panel.dataset.schema || '{}');
  const config = _collectSchemaValues(schema);
  const btn = document.getElementById('svc-save-btn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    const resp = await fetch(API, { method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'update_service', service_id: serviceId, scope, config, conversation_id: conversationId }),
    });
    const data = await resp.json();