// ── Resources (services, flows) ──────────────────────────────────
function cmdServiceList() {
  action$('service_list', {}).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    const svcs = data.services || [];
    if (!svcs.length) { addMsg('system', 'No services installed. Use /service install <type> <name> [key=val,...] to add one.'); return; }
    let lines = ['**Your services:**'];
    svcs.forEach(s => {
      const icon = !s.enabled ? '\u{1F534}' : s.started ? '\u{1F7E2}' : '\u{1F7E1}';
      let tag = '';
      if (s.relay_info && s.relay_info.containerized) {
        const img = s.relay_info.docker_image;
        tag = ' \u{1F433}' + (img ? ` [${img}]` : ' [container]');
      }
      lines.push(`  ${icon} **${s.id}** (\`${s.type}\`)${tag} ${s.description || ''}`);
    });
    addMsg('system', lines.join('\n'));
  });
}

function cmdServiceAction(action, extra) {
  return rxjs.firstValueFrom(action$(action, { ...extra }).pipe(
    rxjs.tap(data => {
      if (data.error) { addMsg('error', data.error); return; }
      if (data.installed) addMsg('system', `Service '${data.id}' installed (${data.type}).`);
      else if (data.uninstalled) addMsg('system', `Service '${data.id}' uninstalled.`);
      else if (data.enabled) addMsg('system', `Service '${data.id}' enabled.`);
      else if (data.disabled) addMsg('system', `Service '${data.id}' disabled.`);
      else addMsg('system', JSON.stringify(data, null, 2));
    })
  ));
}

function cmdSkillList() {
  action$('list_skills', {}).subscribe(data => {
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', 'No skills defined. Use /add-skill <name> <prompt>'); return; }
    let lines = ['**Your skills:**'];
    skills.forEach(s => {
      const mark = s.active ? '\\u2705' : '\\u2B1C';
      lines.push(`${mark} **${s.name}** — ${s.description || s.prompt}`);
    });
    addMsg('system', lines.join('\\n'));
  });
}

function cmdListResources() {
  action$('list_resources', {}).subscribe(data => {
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
  });
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
  // Opening a repository or runtime section → refresh from disk
  if (!_collapsedSections[id] && (id.endsWith('_repo') || id === '_svc' || id === '_relay' || id === '_flow')) loadResources();
}
// Default collapsed: variables, secrets
if (!('_param' in _collapsedSections)) _collapsedSections['_param'] = true;
if (!('_secret' in _collapsedSections)) _collapsedSections['_secret'] = true;

function _sectionHeader(title, rtype) {
  const isParamSecret = rtype === '_param' || rtype === '_secret';
  const onclick = isParamSecret
    ? `_showParamEditor('','',${rtype === '_secret'},true)`
    : rtype === 'agent' ? 'showAddAgentToConvDialog()' : `showResourceCreator('${rtype}')`;
  const collapsed = _collapsedSections[rtype] || false;
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
    <span style="cursor:pointer;color:#6c5ce7;font-weight:600;user-select:none;" onclick="_toggleSection('${rtype}')"><span id="res-arrow-${rtype}">${arrow}</span> ${title}</span>
    <span style="cursor:pointer;font-size:13px;color:#6c5ce7;padding:0 4px;" onclick="${onclick}" title="Create new">+</span>
  </div><div id="res-section-${rtype}" style="display:${collapsed ? 'none' : 'block'};max-height:260px;overflow-y:auto;">`;
}
function _repoSectionHeader(title, rtype) {
  if (!(rtype in _collapsedSections)) _collapsedSections[rtype] = true;
  const collapsed = _collapsedSections[rtype] || false;
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
    <span style="cursor:pointer;color:#888;font-weight:500;font-size:11px;user-select:none;" onclick="_toggleSection('${rtype}')"><span id="res-arrow-${rtype}">${arrow}</span> ${title}</span>
    <span style="cursor:pointer;font-size:11px;color:#888;padding:0 4px;" onclick="loadResources()" title="Refresh from disk">\u21BB</span>
  </div><div id="res-section-${rtype}" style="display:${collapsed ? 'none' : 'block'};">`;
}
function _sectionFooter() { return '</div>'; }

function _showRelayLinkDialog() {
  action$('relay_list_available').subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var relays = data.relays || [];
    if (!relays.length) { addMsg('system', 'No relays available. Connect a relay first (PawCode or VS Code extension).'); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var options = relays.map(function(r) {
      var label = r.relay_id;
      if (r.host_root) label += ' \u2014 ' + r.host_root;
      else if (r.root) label += ' \u2014 ' + r.root;
      var status = r.connected ? '\u{1F7E2}' : '\u{1F534}';
      return '<option value="' + escapeHtml(r.relay_id) + '">' + status + ' ' + escapeHtml(label) + '</option>';
    }).join('');
    overlay.innerHTML =
      '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>Link Relay</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_relayLinkSelect" style="width:100%;padding:8px;background:#1a1a2e;color:#e0e0e0;border:1px solid #444;border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">Cancel</button>'
      + '<button class="exec-approve" onclick="_doRelayLink(this)">Link</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}
function _showRelayInfoDialog(relayId, details) {
  if (typeof details === 'string') try { details = JSON.parse(details); } catch(e) { details = {}; }
  var d = details || {};
  var dl = d._default_local || {};
  var rows = [
    ['Relay ID', relayId],
    ['Connected', d.connected ? '\u{1F7E2} Yes' : '\u{1F534} No'],
    ['Docker root', d.root || '\u2014'],
    ['Local root', d.host_root || '\u2014'],
    ['Platform', d.platform || '\u2014'],
    ['Containerized', d.containerized ? 'Yes' : 'No'],
    ['Allow local', d.allow_local ? '\u2705 Yes' : '\u274c No'],
  ];
  var infoHtml = '<table style="margin:8px 0;">' + rows.map(function(r) {
    return '<tr><td style="color:#888;padding:3px 12px 3px 0;font-size:12px;white-space:nowrap;">' + escapeHtml(r[0]) + '</td>'
      + '<td style="font-size:12px;">' + r[1] + '</td></tr>';
  }).join('') + '</table>';

  // Default local toggles (only if allow_local)
  var localHtml = '';
  if (d.allow_local) {
    var convLocal = dl['*'];
    var convLabel = convLocal === true ? 'Local' : convLocal === false ? 'Docker' : 'Not set';
    var convColor = convLocal === true ? '#4ecdc4' : convLocal === false ? '#e94560' : '#555';
    localHtml += '<div style="margin-top:8px;font-size:12px;font-weight:600;color:#6c5ce7;">Default execution mode</div>';
    localHtml += '<div style="display:flex;align-items:center;gap:8px;margin:6px 0;font-size:12px;">'
      + '<span style="color:#888;min-width:80px;">Conversation:</span>'
      + '<span style="color:' + convColor + ';">' + convLabel + '</span>'
      + '<button style="font-size:10px;padding:2px 6px;border:1px solid #444;border-radius:3px;background:#1a1a2e;color:#4ecdc4;cursor:pointer;" '
      + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',true,\'\')">Local</button>'
      + '<button style="font-size:10px;padding:2px 6px;border:1px solid #444;border-radius:3px;background:#1a1a2e;color:#e94560;cursor:pointer;" '
      + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',false,\'\')">Docker</button>'
      + '</div>';
    // Per-agent toggles (from conversation agents)
    try {
      var agentEls = document.querySelectorAll('#_ncAgentsSel [data-id], .res-agent-name');
      // Simpler: get agents from resource panel
      var rpAgents = [];
      document.querySelectorAll('[data-agent-name]').forEach(function(el) { rpAgents.push(el.dataset.agentName); });
      if (!rpAgents.length) {
        // Fallback: get from active_resources in cached data
        var cachedAgents = window._lastResourceData && window._lastResourceData.agents;
        if (cachedAgents) rpAgents = cachedAgents.filter(function(a) { return a.active; }).map(function(a) { return a.name; });
      }
      rpAgents.forEach(function(agentName) {
        var aLocal = dl[agentName];
        var aLabel = aLocal === true ? 'Local' : aLocal === false ? 'Docker' : 'Not set';
        var aColor = aLocal === true ? '#4ecdc4' : aLocal === false ? '#e94560' : '#555';
        localHtml += '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px;">'
          + '<span style="color:#888;min-width:80px;">@' + escapeHtml(agentName) + ':</span>'
          + '<span style="color:' + aColor + ';">' + aLabel + '</span>'
          + '<button style="font-size:10px;padding:2px 6px;border:1px solid #444;border-radius:3px;background:#1a1a2e;color:#4ecdc4;cursor:pointer;" '
          + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',true,\'' + escapeHtml(agentName) + '\')">Local</button>'
          + '<button style="font-size:10px;padding:2px 6px;border:1px solid #444;border-radius:3px;background:#1a1a2e;color:#e94560;cursor:pointer;" '
          + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',false,\'' + escapeHtml(agentName) + '\')">Docker</button>'
          + '</div>';
      });
    } catch(e) {}
  }

  var overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.innerHTML = '<div class="exec-dialog" style="min-width:340px;">'
    + '<h3>Relay: ' + escapeHtml(relayId) + '</h3>'
    + infoHtml + localHtml
    + '<div class="exec-btns"><button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">Close</button></div>'
    + '</div>';
  document.body.appendChild(overlay);
}

function _setRelayLocal(relayId, local, agent) {
  action$('relay_set_local', {relay_id: relayId, local: local, agent: agent}).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.message || 'OK');
    // Close dialog and refresh
    var ov = document.querySelector('.exec-overlay');
    if (ov) ov.remove();
    setTimeout(loadResources, 300);
  });
}

function _doRelayLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_relayLinkSelect');
  var rid = sel ? sel.value : '';
  overlay.remove();
  if (rid) {
    fireAction('relay_link', {relay_id: rid});
    setTimeout(loadResources, 500);
  }
}

var _loadResourcesTimer = null;
async function loadResources() {
  // Debounce: coalesce rapid calls into one (300ms window)
  if (_loadResourcesTimer) clearTimeout(_loadResourcesTimer);
  _loadResourcesTimer = setTimeout(_loadResourcesNow, 300);
}
function _loadResourcesNow() {
  _loadResourcesTimer = null;
  if (!conversationId) { document.getElementById('resourcesPanel').style.display = 'none'; return; }
  document.getElementById('resourcesPanel').style.display = 'block';
  action$('list_resources', {}).subscribe(data => _renderResourcesFromSSE(data));
  if (!window._cachedTools) {
    action$('get_tool_schemas', {}).subscribe(data => _renderResourcesFromSSE(data));
  }
}
function _renderResourcesFromSSE(data) {
  if (!data) return;
  if (data.user_role) window._userRole = data.user_role;
  if (data.tools) { window._cachedTools = data.tools; return; }  // tool schemas response
  _renderResourcesData(data);
}
async function _renderResourcesData(data) {
  try {
    const el = document.getElementById('resourcesContent');
    let html = '';
    // Agents (conversation members)
    html += _sectionHeader('Agents', 'agent');
    if (data.agents && data.agents.length) {
      data.agents.forEach(function(a) {
        var isPrimary = a.active;
        var aName = escapeHtml(a.name);
        var primaryColor = isPrimary ? '#4ecdc4' : '#555';
        var textColor = isPrimary ? '#e0e0e0' : '#aaa';
        var primaryTitle = isPrimary ? 'Primary agent' : 'Set as primary';
        var primaryArrow = isPrimary ? '&#9654;' : '&#9655;';
        var autoconvTag = a.autoconv ? '<span style="font-size:9px;color:#4ecdc4;margin-left:2px;">' + String.fromCodePoint(0x1F504) + '</span>' : '';
        html += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showAgentMenu(event,\'' + aName + '\',\'' + (a.scope||'') + '\',\'' + (a.autoconv||'') + '\');return false;">'
          + '<span style="cursor:pointer;font-size:10px;color:' + primaryColor + ';" title="' + primaryTitle + '"'
          + ' onclick="cmdAgentSelect(this.dataset.n).then(loadResources)" data-n="' + aName + '">' + primaryArrow + '</span>'
          + _scopeBadge(a.scope)
          + '<span style="color:' + textColor + ';font-size:12px;cursor:pointer;flex:1;"'
          + ' onclick="cmdAgentSelect(this.dataset.n).then(loadResources)" data-n="' + aName + '">' + aName + '</span>'
          + autoconvTag
          + '<span style="cursor:pointer;font-size:11px;color:#e94560;padding:0 3px;" title="Remove from conversation"'
          + ' onclick="_removeAgentFromConv(this.dataset.n)" data-n="' + aName + '">&times;</span>'
          + '</div>';
        // Show LLM service + assigned skills as small tags
        var aLlm = a.llm_service || '';
        var aSkills = a.assigned_skills || [];
        if (aLlm || aSkills.length) {
          html += '<div style="margin-left:24px;margin-bottom:3px;display:flex;flex-wrap:wrap;gap:3px;">';
          if (aLlm) {
            html += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#1a2a3e;color:#64b5f6;">' + escapeHtml(aLlm) + '</span>';
          }
          aSkills.forEach(function(sk) {
            html += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:#2a1a4e;color:#b39ddb;">' + escapeHtml(sk) + '</span>';
          });
          html += '</div>';
        }
      });
    } else {
      html += '<div style="margin-left:8px;font-size:11px;color:#555;">No agents — <span style="color:#6c5ce7;cursor:pointer;" onclick="showAddAgentToConvDialog()">+ Add</span></div>';
    }
    html += _sectionFooter();

    // Agent Repository (repo agents not yet in conv, collapsed by default)
    html += _repoSectionHeader("Agent Repository", "_agent_repo");
    if (!_collapsedSections["_agent_repo"]) {
      var repoAgents = (data.repo_agents || []).filter(function(a) { return !a.in_conversation; });
      if (repoAgents.length) {
        repoAgents.forEach(function(a) {
          var aName = escapeHtml(a.name);
          html += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">'
            + _scopeBadge(a.scope)
            + '<span style="color:#888;font-size:12px;flex:1;">' + aName + '</span>'
            + '<span style="color:#6c5ce7;font-size:10px;cursor:pointer;padding:0 4px;" title="Add to conversation"'
            + ' onclick="_addAgentToConv(this.dataset.n)" data-n="' + aName + '">+</span>'
            + '</div>';
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">All agents are in this conversation</div>';
      }
    }
    html += _sectionFooter();
    // ── Skills ──
    html += _sectionHeader('Skills', 'skill');
    { const allSkills = data.skills || [];
      if (allSkills.length) {
        allSkills.forEach(s => {
          const assignedTo = s.assigned_to || [];
          const assignedTag = assignedTo.length ? ' <span style="color:#555;font-size:9px;">\u2192 ' + assignedTo.map(escapeHtml).join(', ') + '</span>' : '';
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'skill','${escapeHtml(s.name)}','${s.scope||''}');return false;">
            ${_scopeBadge(s.scope)}<span style="color:#e0e0e0;font-size:12px;flex:1;">${escapeHtml(s.name)}${assignedTag}</span>
          </div>`;
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">No skills defined</div>';
      }
    }
    html += _sectionFooter();

    // ── MCP Servers (linked to conv) ──
    html += _sectionHeader('MCP', 'mcp');
    { const linked = (data.mcp_servers || []).filter(m => m.linked);
      if (linked.length) {
        linked.forEach(m => {
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showResourceMenu(event,'mcp','${m.name}','${m.scope||''}');return false;">
            ${_scopeBadge(m.scope)}<span style="color:#e0e0e0;font-size:12px;">${escapeHtml(m.name)}</span>
            <span style="cursor:pointer;font-size:11px;color:#e94560;padding:0 3px;" title="Unlink"
              onclick="fireAction('unlink_mcp',{name:'${escapeHtml(m.name)}'});setTimeout(loadResources,400)">&times;</span>
          </div>`;
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">No MCP servers linked</div>';
      }
    }
    html += _sectionFooter();
    // MCP Repository (collapsed)
    html += _repoSectionHeader('MCP Repository', '_mcp_repo');
    if (!_collapsedSections['_mcp_repo']) {
      const repoMcps = (data.mcp_servers || []).filter(m => !m.linked);
      if (repoMcps.length) {
        repoMcps.forEach(m => {
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
            ${_scopeBadge(m.scope)}<span style="color:#888;font-size:12px;flex:1;">${escapeHtml(m.name)}</span>
            <span style="color:#6c5ce7;font-size:10px;cursor:pointer;padding:0 4px;" title="Link to conversation"
              onclick="fireAction('link_mcp',{name:'${escapeHtml(m.name)}'});setTimeout(loadResources,400)">+</span>
          </div>`;
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">All MCP servers are linked</div>';
      }
    }
    html += _sectionFooter();

    // ── Prompts (click to paste into chat input) ──
    html += _sectionHeader('Prompts', 'prompt');
    if (!_collapsedSections['prompt']) {
      const prompts = data.prompts || [];
      if (prompts.length) {
        prompts.forEach(p => {
          const title = p.title || p.name;
          const icon = p.has_parameters ? '\u{1F4DD}' : '\u{1F4CB}';
          const desc = p.description ? ' title="' + escapeHtml(p.description) + '"' : '';
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer"${desc}
            onclick="_usePrompt('${escapeHtml(p.name)}',${p.has_parameters})" oncontextmenu="showResourceMenu(event,'prompt','${p.name}','${p.scope||''}');return false;">
            ${_scopeBadge(p.scope)}<span style="font-size:11px">${icon}</span>
            <span style="font-size:12px;color:#c0c0d0">${escapeHtml(title)}</span>
          </div>`;
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">No prompts</div>';
      }
    }
    html += _sectionFooter();

    // ── Tools (always available, no linking) ──
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

    // ── Tasks (linked to conv) ──
    html += _sectionHeader('Tasks', 'task_def');
    { const linked = (data.task_defs || []).filter(t => t.linked);
      if (linked.length) {
        linked.forEach(t => {
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showResourceMenu(event,'task_def','${t.name}','${t.scope||''}');return false;">
            ${_scopeBadge(t.scope)}<span style="color:#e0e0e0;font-size:12px;" title="${escapeHtml(t.description)}">${escapeHtml(t.name)}</span>
            <span style="color:#555;font-size:10px;">[${t.default_interval}]</span>
            <span style="cursor:pointer;font-size:11px;color:#e94560;padding:0 3px;" title="Unlink"
              onclick="fireAction('unlink_task',{name:'${escapeHtml(t.name)}'});setTimeout(loadResources,400)">&times;</span>
          </div>`;
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">No tasks linked</div>';
      }
    }
    html += _sectionFooter();
    // Task Repository (collapsed)
    html += _repoSectionHeader('Task Repository', '_task_repo');
    if (!_collapsedSections['_task_repo']) {
      const repoTasks = (data.task_defs || []).filter(t => !t.linked);
      if (repoTasks.length) {
        repoTasks.forEach(t => {
          html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">
            ${_scopeBadge(t.scope)}<span style="color:#888;font-size:12px;flex:1;" title="${escapeHtml(t.description)}">${escapeHtml(t.name)}</span>
            <span style="color:#6c5ce7;font-size:10px;cursor:pointer;padding:0 4px;" title="Link to conversation"
              onclick="fireAction('link_task',{name:'${escapeHtml(t.name)}'});setTimeout(loadResources,400)">+</span>
          </div>`;
        });
      } else {
        html += '<div style="margin-left:8px;font-size:11px;color:#555;">All tasks are linked</div>';
      }
    }
    html += _sectionFooter();
    // Running task instances (active/paused)
    if (data.running_tasks && data.running_tasks.length) {
      html += _sectionHeader('Running Tasks', '_running');
      data.running_tasks.forEach(t => {
        const statusColor = t.status === 'active' ? '#4ecdc4' : t.status === 'paused' ? '#f0ad4e' : '#666';
        const statusIcon = t.status === 'active' ? '\u25B6' : t.status === 'paused' ? '\u23F8' : '\u23F9';
        const label = (t.task_def_name || (t.task || '').substring(0, 30) || t.task_id) + ' \u2192 ' + t.agent;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showRunningTaskMenu(event,'${t.task_id}','${t.agent}','${t.status}');return false;">
          <span style="color:${statusColor};font-size:11px;">${statusIcon}</span>
          <span style="color:#8888aa;font-size:11px;" title="${escapeHtml(t.task)}">${escapeHtml(label)}</span>
          <span style="color:#555;font-size:10px;">[${t.iterations}/${t.max_iterations}]</span>
        </div>`;
      });
      html += _sectionFooter();
    }
    // Services (always show for [+] install button + reload from disk)
    html += _sectionHeader('Services', '_svc').replace('</div><div',
      '<span style="cursor:pointer;font-size:11px;color:#888;padding:0 2px;" onclick="event.stopPropagation();fireAction(\'reload_disk\',{});setTimeout(loadResources,300)" title="Reload from disk">\u21BB</span></div><div');
    if (data.services && data.services.length) {
      data.services.forEach(s => {
        const statusDot = !s.enabled ? '\u{1F534}'
          : s.started ? '\u{1F7E2}' : '\u{1F7E1}';
        let dockerTag = '';
        if (s.relay_info && s.relay_info.containerized) {
          const img = s.relay_info.docker_image;
          dockerTag = ' \u{1F433}' + (img ? ` [${img}]` : '');
        }
        const svcCtx = ` oncontextmenu="showServiceMenu(event,'${s.service_id}','${s.scope}',${s.enabled});return false;"`;
        html += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${svcCtx}>
          ${_scopeBadge(s.scope)}<span style="color:#8888aa;font-size:11px;">${statusDot} <b>${s.service_id}</b> <span style="color:#555">(${s.service_type})</span>${dockerTag}</span>
        </div>`;
      });
    } else {
      html += '<div style="color:#555;font-size:10px;margin-left:8px;">No services installed</div>';
    }
    html += _sectionFooter();
    // Relay bindings for this conversation (always show section)
    {
      if (!('_relay' in _collapsedSections)) _collapsedSections['_relay'] = false;
      var rbCollapsed = _collapsedSections['_relay'] || false;
      var rbArrow = rbCollapsed ? '\u25B6' : '\u25BC';
      var rbDisplay = rbCollapsed ? 'none' : 'block';
      html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
        + '<span style="cursor:pointer;color:#6c5ce7;font-weight:600;user-select:none;" onclick="_toggleSection(\'_relay\')">'
        + '<span id="res-arrow-_relay">' + rbArrow + '</span> Relays</span>'
        + '<span style="cursor:pointer;font-size:13px;color:#6c5ce7;padding:0 4px;" onclick="_showRelayLinkDialog()" title="Link relay">+</span>'
        + '</div><div id="res-section-_relay" style="display:' + rbDisplay + ';">';
      var _rb = (data.relay_bindings && data.relay_bindings.linked) ? data.relay_bindings : {linked:{}, default:{}};
      var _rbLinked = _rb.linked || {};
      var _rbDefaults = _rb.default || {};
      var _rbDetails = _rb.details || {};
      // Collect all unique relay IDs and which scopes they belong to
      var _allRelays = {};  // relay_id → [scope1, scope2, ...]
      Object.keys(_rbLinked).forEach(function(scope) {
        (_rbLinked[scope] || []).forEach(function(rid) {
          if (!_allRelays[rid]) _allRelays[rid] = [];
          _allRelays[rid].push(scope);
        });
      });
      var _relayIds = Object.keys(_allRelays);
      if (_relayIds.length) {
        _relayIds.forEach(function(rid) {
          var scopes = _allRelays[rid];
          var isConvDefault = _rbDefaults['*'] === rid;
          var agentDefaults = [];
          Object.keys(_rbDefaults).forEach(function(scope) {
            if (scope !== '*' && _rbDefaults[scope] === rid) agentDefaults.push(scope);
          });
          var star = isConvDefault ? ' \u2605' : '';
          var agentTags = '';
          scopes.forEach(function(s) {
            if (s !== '*') agentTags += ' <span style="font-size:9px;color:#6c5ce7;background:#1a1a3e;padding:1px 4px;border-radius:3px;">' + escapeHtml(s) + '</span>';
          });
          agentDefaults.forEach(function(a) {
            agentTags += ' <span style="font-size:9px;color:#4ecdc4;" title="Default for ' + escapeHtml(a) + '">\u2605' + escapeHtml(a) + '</span>';
          });
          var color = isConvDefault ? '#4ecdc4' : '#8888aa';
          var icon = isConvDefault ? '\u25C9' : '\u25CB';
          var titleText = isConvDefault ? 'Default relay' : 'Set as default';
          var clickDefault = isConvDefault ? '' : ' onclick="fireAction(\'relay_default\',{relay_id:\'' + escapeHtml(rid) + '\'}); setTimeout(loadResources, 500)"';
          var det = _rbDetails[rid] || {};
          var connDot = det.connected ? '\u{1F7E2}' : '\u{1F534}';
          var pathInfo = '';
          if (det.root) pathInfo += '<div style="font-size:10px;color:#666;margin-left:20px;">docker: <code>' + escapeHtml(det.root) + '</code></div>';
          if (det.host_root) pathInfo += '<div style="font-size:10px;color:#666;margin-left:20px;">local: <code>' + escapeHtml(det.host_root) + '</code></div>';
          var _rbDefaultLocal = (_rb.default_local || {})[rid] || {};
          var _detWithLocal = Object.assign({}, det, {_default_local: _rbDefaultLocal});
          var _detJson = escapeHtml(JSON.stringify(_detWithLocal).replace(/'/g, "\\'"));
          html += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="_showRelayInfoDialog(\'' + escapeHtml(rid) + '\',' + _detJson + ');return false;">'
            + '<span style="color:' + color + ';font-size:11px;cursor:pointer;" title="' + titleText + '"' + clickDefault + '>' + icon + '</span>'
            + '<span style="font-size:11px;">' + connDot + '</span>'
            + '<span style="color:' + color + ';font-size:12px;">' + escapeHtml(rid) + star + '</span>'
            + agentTags
            + '<span style="cursor:pointer;font-size:11px;color:#e94560;padding:0 3px;" title="Unlink"'
            + ' onclick="fireAction(\'relay_unlink\',{relay_id:\'' + escapeHtml(rid) + '\'}); setTimeout(loadResources, 500)">&times;</span>'
            + '</div>' + pathInfo;
        });
      } else {
        html += '<div style="color:#555;font-size:10px;margin-left:8px;">No relays linked</div>';
      }
      html += _sectionFooter();
    }
    // Deployed flows (always show section for [+] deploy button + reload from disk)
    html += _sectionHeader('Flows', '_flow').replace('</div><div',
      '<span style="cursor:pointer;font-size:11px;color:#888;padding:0 2px;" onclick="event.stopPropagation();fireAction(\'reload_disk\',{});setTimeout(loadResources,300)" title="Reload from disk">\u21BB</span></div><div');
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
    // Variables & Secrets + Linked Accounts (async, appended when ready)
    if (!html) html = '<div style="color:#555;font-size:11px;">No resources. Use [+] or /agent create, /task create</div>';
    // Fetch params/secrets and linked accounts, then render all at once
    rxjs.forkJoin([
      action$('list_params_secrets', { conversation_id: conversationId }).pipe(rxjs.catchError(() => rxjs.of({}))),
      action$('list_linked_accounts', { conversation_id: conversationId }).pipe(rxjs.catchError(() => rxjs.of({}))),
    ]).subscribe(([ps, linksData]) => {
      let extra = '';
      if (ps.parameters && ps.parameters.length) {
        extra += _sectionHeader('Variables', '_param');
        ps.parameters.forEach(p => {
          const truncVal = p.value.length > 30 ? p.value.substring(0, 30) + '...' : p.value;
          extra += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${p.key}','${p.scope}');return false;">
            ${_scopeBadge(p.scope)}<span style="color:#8888aa;font-size:11px;"><b>${escapeHtml(p.key)}</b> = ${escapeHtml(truncVal)}</span>
          </div>`;
        });
        extra += _sectionFooter();
      }
      if (ps.secrets && ps.secrets.length) {
        extra += _sectionHeader('Secrets', '_secret');
        ps.secrets.forEach(s => {
          extra += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${s.key}','${s.scope}',true);return false;">
            ${_scopeBadge(s.scope)}<span style="color:#8888aa;font-size:11px;"><b>${escapeHtml(s.key)}</b> = ********</span>
          </div>`;
        });
        extra += _sectionFooter();
      }
      const links = (linksData && linksData.links) || {};
      const linkKeys = Object.keys(links);
      let linksHtml = '<div style="margin-top:6px;padding:4px 6px;font-size:11px;color:#888;border-top:1px solid #222;">';
      linksHtml += '<b>Linked Accounts</b>';
      if (linkKeys.length) {
        linkKeys.forEach(provider => {
          linksHtml += `<div style="display:flex;align-items:center;gap:6px;margin:3px 0 3px 8px;">
            <span style="font-size:11px;color:#e0e0e0;">${escapeHtml(provider)}</span>
            <span style="font-size:10px;color:#666;">${escapeHtml(links[provider])}</span>
            <span style="cursor:pointer;font-size:10px;color:#e94560;" title="Unlink" onclick="cmdResourceAction('unlink_account',{provider:'${provider}'}).then(loadResources)">\u2715</span>
          </div>`;
        });
      } else {
        linksHtml += '<div style="color:#555;font-size:10px;margin-left:8px;">No linked accounts</div>';
      }
      linksHtml += '</div>';
      const fullHtml = html + extra + linksHtml;
      // Only update DOM if content actually changed (prevents flash/blink)
      if (el.innerHTML !== fullHtml) el.innerHTML = fullHtml;
    });
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
  item('\u{1F441} View...', () => showResourceEditor(rtype, name, true));
  // Edit — admin can edit globals, owners can edit their own
  if (_canEditScope(scope)) {
    item('\u270F Edit...', () => showResourceEditor(rtype, name));
  }
  if (rtype === 'agent') {
    item('\u25B6 Select', () => cmdAgentSelect(name));
    if (autoconv) {
      item('\u23F9 Autoconv off', () => {
        action$('random_thought', { sub: 'off', agent: name }).subscribe(d => {
          addMsg('system', d.error || 'Autoconv disabled for ' + name);
          loadResources();
        });
      });
    } else {
      item('\u{1F504} Autoconv on...', () => {
        const freq = prompt('Frequency (e.g. 6/1m, 2-3/h, 1/2h):', '6/1m');
        if (!freq) return;
        action$('random_thought', { sub: 'on', agent: name, frequency: freq }).subscribe(d => {
          addMsg('system', d.error || 'Autoconv enabled for ' + name + ' (' + freq + ')');
          loadResources();
        });
      });
    }
  }
  if (rtype === 'skill') {
    item('\u{1F517} Assign to agent...', () => _showSkillAssignDialog(name));
  }
  if (rtype === 'task_def') {
    item('\u25B6 Assign to agent...', () => _showAssignDialog(name));
    item('\u{1F4DC} View Log...', () => _showTaskDefLog(name));
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
  action$('copy_resource_scope', { resource_type: rtype,
    name, target_scope: targetScope }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', `${rtype} '${name}' copied to ${targetScope}.`);
    loadResources();
  });
}

function _deleteResource(rtype, name, scope) {
  if (!confirm(`Delete ${rtype} '${name}' (${scope})?`)) return;
  action$('delete_resource', { resource_type: rtype,
    name, scope: scope || 'user' }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', `${rtype} '${name}' deleted.`);
    loadResources();
  });
}

function showAgentMenu(e, name, scope, autoconv) {
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
  const sep = () => { const s = document.createElement('div'); s.style.cssText = 'height:1px;background:#333;margin:4px 0;'; menu.appendChild(s); };

  item('\u{1F441} View definition...', () => showResourceEditor('agent', name, true));
  if (_canEditScope(scope)) item('\u270F Edit definition...', () => showResourceEditor('agent', name));
  item('\u2699 Configure in conversation...', () => _showAgentConvConfigDialog(name));
  item('\u25B6 Select', () => cmdAgentSelect(name).then(loadResources));
  item('\u{1F9E9} Manage skills...', () => _showAgentSkillsDialog(name));
  if (autoconv) {
    item('\u23F9 Autoconv off', () => { action$('random_thought', { sub: 'off', agent: name }).subscribe(d => { addMsg('system', d.error || 'Autoconv disabled for ' + name); loadResources(); }); });
  } else {
    item('\u{1F504} Autoconv on...', () => { const freq = prompt('Frequency (e.g. 6/1m, 2-3/h, 1/2h):', '6/1m'); if (!freq) return; action$('random_thought', { sub: 'on', agent: name, frequency: freq }).subscribe(d => { addMsg('system', d.error || 'Autoconv enabled for ' + name + ' (' + freq + ')'); loadResources(); }); });
  }
  sep();
  item('\u2716 Remove from conversation', () => _removeAgentFromConv(name), true);
  setTimeout(() => document.addEventListener('click', function _close() { menu.remove(); document.removeEventListener('click', _close); }), 0);
}

function _showSkillAssignDialog(skillName) {
  action$('list_resources', {}).subscribe(data => {
    var agents = (data.agents || []).concat((data.repo_agents || []).filter(a => !a.in_conversation));
    if (!agents.length) { addMsg('system', 'No agents available.'); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var options = agents.map(a => '<option value="' + escapeHtml(a.name) + '">' + escapeHtml(a.name) + '</option>').join('');
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:320px;">'
      + '<h3 style="margin:0 0 12px;">Assign skill \u201C' + escapeHtml(skillName) + '\u201D to agent</h3>'
      + '<select id="_skAssignAgent" style="width:100%;padding:8px;background:#1a1a2e;color:#e0e0e0;border:1px solid #444;border-radius:4px;font-size:13px;">' + options + '</select>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'
      + '<button onclick="this.closest(\'.exec-overlay\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
      + '<button id="_skAssignBtn" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Assign</button>'
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
  if (!conversationId) { addMsg('error', 'No active conversation'); return; }
  action$('get_agent_conv_config', { name: agentName, conversation_id: conversationId }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var cfg = data.config || {};
    var paramsSchema = data.parameters_schema || {};
    var instParams = cfg.params || {};
    var services = data.available_services || [];
    var serviceOpts = services.map(function(s) {
      var sel = s.id === cfg.llm_service ? ' selected' : '';
      return '<option value="' + escapeHtml(s.id) + '"' + sel + '>'
        + escapeHtml(s.id) + ' (' + escapeHtml(s.provider) + ')</option>';
    }).join('');
    var toolsStr = Array.isArray(cfg.tools) ? cfg.tools.join(', ') : (cfg.tools || '');
    var overlay = document.createElement('div');
    overlay.id = 'agentConvConfigOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
    var panel = document.createElement('div');
    panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:520px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
    var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
      + '<h3 style="margin:0;color:#e0e0e0;font-size:14px;">Configure: ' + escapeHtml(agentName) + '</h3>'
      + '<button onclick="document.getElementById(\'agentConvConfigOverlay\').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>'
      + '</div>';
    // Definition info
    if (cfg.definition) {
      html += '<div style="margin-bottom:10px;padding:6px 8px;background:#0f0f23;border-radius:4px;font-size:11px;">'
        + '<span style="color:#888;">Definition:</span> <span style="color:#6c5ce7;">' + escapeHtml(cfg.definition) + '</span></div>';
    }
    // Instance parameters — skip 'name' (synced from instance_name, immutable here)
    var paramKeys = Object.keys(paramsSchema);
    var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
    if (visibleParamKeys.length) {
      html += '<div style="margin-bottom:10px;padding:8px;border:1px solid #333;border-radius:4px;">'
        + '<div style="font-size:11px;color:#6c5ce7;margin-bottom:6px;font-weight:600;">Instance Parameters</div>';
      visibleParamKeys.forEach(function(k) {
        var spec = paramsSchema[k] || {};
        var val = instParams[k] || spec.default || '';
        var label = k + (spec.required ? ' *' : '');
        html += '<div style="margin-bottom:6px;"><label style="color:#aaa;font-size:11px;">' + escapeHtml(label) + '</label>'
          + '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(val)) + '" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
      });
      html += '</div>';
    }
    // Runtime config
    html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">LLM Service *</label>'
      + '<select id="acc-llm" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">'
      + serviceOpts + '</select></div>'
      + '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Model (override)</label>'
      + '<input id="acc-model" value="' + escapeHtml(cfg.model || '') + '" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
      + '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Tools (comma-separated)</label>'
      + '<input id="acc-tools" value="' + escapeHtml(toolsStr) + '" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
      + '<div style="margin-bottom:8px;">'
      + '<label style="color:#aaa;font-size:11px;">Max iterations (agent loop)</label>'
      + '<input id="acc-depth" type="number" value="' + (cfg.max_depth || 1000) + '" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/>'
      + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
      + '<button onclick="document.getElementById(\'agentConvConfigOverlay\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
      + '<button id="acc-save" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Save</button>'
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
        addMsg('system', agentName + ' config updated for this conversation.');
        overlay.remove();
        loadResources();
      });
    };
  });
}

function _showAgentSkillsDialog(agentName) {
  // Load all skills + agent's current assigned skills
  Promise.all([
    rxjs.firstValueFrom(action$('list_skills', {})),
    rxjs.firstValueFrom(action$('list_agent_skills', { agent_name: agentName })),
  ]).then(function(results) {
    var allSkills = results[0].skills || [];
    var assigned = (results[1].skills || []).map(s => s.name);
    if (!allSkills.length) { addMsg('system', 'No skills defined. Create one first with /add-skill.'); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var checkboxes = allSkills.map(s => {
      var checked = assigned.indexOf(s.name) >= 0 ? ' checked' : '';
      return '<label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer;font-size:13px;color:#c0c0d0;">'
        + '<input type="checkbox" class="agent-sk-cb" value="' + escapeHtml(s.name) + '"' + checked + ' style="accent-color:#6c5ce7;"/>'
        + escapeHtml(s.name)
        + (s.description ? ' <span style="color:#666;font-size:11px;">\u2014 ' + escapeHtml(s.description) + '</span>' : '')
        + '</label>';
    }).join('');
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:360px;">'
      + '<h3 style="margin:0 0 12px;">Skills for \u201C' + escapeHtml(agentName) + '\u201D</h3>'
      + '<div style="max-height:200px;overflow-y:auto;background:#0f0f23;border:1px solid #333;border-radius:4px;padding:8px;">' + checkboxes + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'
      + '<button onclick="this.closest(\'.exec-overlay\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
      + '<button id="_agentSkSave" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Save</button>'
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
      Promise.all(calls).then(() => {
        var msg = [];
        if (toAssign.length) msg.push('Assigned: ' + toAssign.join(', '));
        if (toUnassign.length) msg.push('Removed: ' + toUnassign.join(', '));
        if (msg.length) addMsg('system', agentName + ' skills updated. ' + msg.join('. '));
        loadResources();
      });
    };
  });
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
    const data = await rxjs.firstValueFrom(action$('list_available_flows', {}));
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
  action$('deploy_flow', { template_id: templateId, scope, parameters: params }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `Flow deployed: ${d.instance_id} (${scope})`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
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

// ── Prompt use (click to paste) ─────────────────────────────────
function _usePrompt(name, hasParams) {
  action$('get_prompt', { name }).subscribe(data => {
    if (data.error) { addMsg('system', data.error); return; }
    if (!hasParams || !data.parameters || !Object.keys(data.parameters).length) {
      const input = document.getElementById('input');
      input.value = data.prompt;
      input.focus();
      input.dispatchEvent(new Event('input'));
      return;
    }
    // Build parameter dialog
    const params = data.parameters;
    let ov = document.getElementById('promptParamOverlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'promptParamOverlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:420px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
    let formHtml = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="margin:0;color:#e0e0e0;font-size:14px;">${escapeHtml(data.title || name)}</h3>
      <button onclick="document.getElementById('promptParamOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
    </div>`;
    for (const [key, schema] of Object.entries(params)) {
      const def = schema.default || '';
      const desc = schema.description || key;
      formHtml += `<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">${escapeHtml(desc)}</label>`
        + `<input id="prompt-param-${key}" value="${escapeHtml(String(def))}" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>`;
    }
    formHtml += `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
      <button onclick="document.getElementById('promptParamOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
      <button id="promptParamPaste" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Paste</button>
    </div>`;
    panel.innerHTML = formHtml;
    ov.appendChild(panel);
    document.body.appendChild(ov);
    document.getElementById('promptParamPaste').onclick = () => {
      const values = {};
      for (const key of Object.keys(params)) {
        values[key] = (document.getElementById('prompt-param-' + key) || {}).value || '';
      }
      action$('use_prompt', { name, params: values }).subscribe(res => {
        if (res.error) { addMsg('system', res.error); return; }
        const input = document.getElementById('input');
        input.value = res.resolved;
        input.focus();
        input.dispatchEvent(new Event('input'));
        document.getElementById('promptParamOverlay').remove();
      });
    };
  });
}

// ── Resource editor overlay ───────────────────────────────────────
const _RESOURCE_FIELDS = {
  agent:    [['prompt','textarea'],['description','text']],
  skill:    [['prompt','textarea'],['description','text']],
  mcp:      [['url','text'],['auth','text'],['description','text']],
  task_def: [['prompt','textarea'],['criteria','textarea'],['default_interval','text'],['verifier','text'],['skills','skills_picker'],['description','text']],
  prompt:   [['prompt','textarea'],['parameters','params_editor'],['title','text'],['category','text'],['description','text']],
  _tool:    [['tool_description','text'],['parameters','textarea'],['code','textarea']],
};

function _buildResourceForm(rtype, data, isNew, readonly) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  let html = '';
  if (isNew) {
    html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Name</label><input id="res-name" value="" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;"/></div>';
    if (rtype !== '_tool') {
      html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Scope</label><select id="res-scope" style="background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">'
        + (_isAdmin() ? '<option value="global">Global</option>' : '')
        + '<option value="user">User</option><option value="conversation">Conversation</option></select></div>';
    }
  }
  for (const [key, type] of fields) {
    let val = (data && data[key] != null) ? data[key] : '';
    if (typeof val === 'object') val = JSON.stringify(val, null, 2);
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    html += `<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">${key}</label>`;
    if (type === 'textarea') {
      html += `<textarea id="res-${key}"${dis} style="width:100%;min-height:120px;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;resize:vertical;${roS}">${escaped}</textarea>`;
    } else if (type === 'params_editor') {
      const params = (data && typeof data[key] === 'object' && data[key]) ? data[key] : {};
      html += `<div id="res-${key}" data-type="params_editor" style="margin-top:2px;background:#0f0f23;border:1px solid #333;border-radius:4px;padding:6px;${roS}">`;
      html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">';
      html += '<tr style="color:#888;"><th style="text-align:left;padding:2px 4px;">Name</th><th style="text-align:left;padding:2px 4px;">Type</th><th style="text-align:left;padding:2px 4px;">Default</th><th style="text-align:left;padding:2px 4px;">Description</th>';
      if (!ro) html += '<th style="width:24px;"></th>';
      html += '</tr>';
      for (const [pname, pdef] of Object.entries(params)) {
        const pt = (pdef.type || 'string').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const pd = (pdef.default || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const pdesc = (pdef.description || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const pn = pname.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        html += `<tr class="param-row" style="border-top:1px solid #222;">`;
        html += `<td style="padding:3px 4px;"><input class="pe-name" value="${pn}"${dis} style="width:100%;background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;${roS}"/></td>`;
        html += `<td style="padding:3px 4px;"><select class="pe-type"${dis} style="background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;${roS}">`;
        for (const t of ['string','number','boolean']) html += `<option value="${t}"${pt===t?' selected':''}>${t}</option>`;
        html += '</select></td>';
        html += `<td style="padding:3px 4px;"><input class="pe-default" value="${pd}"${dis} style="width:100%;background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;${roS}"/></td>`;
        html += `<td style="padding:3px 4px;"><input class="pe-desc" value="${pdesc}"${dis} style="width:100%;background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;${roS}"/></td>`;
        if (!ro) html += `<td style="padding:3px 2px;"><button onclick="this.closest('tr').remove()" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:14px;">&times;</button></td>`;
        html += '</tr>';
      }
      html += '</table>';
      if (!ro) html += `<button onclick="_addParamRow(this.parentElement)" style="margin-top:4px;background:#333;color:#aaa;border:1px solid #444;padding:3px 10px;border-radius:3px;cursor:pointer;font-size:11px;">+ Add Parameter</button>`;
      html += '</div>';
    } else if (type === 'skills_picker') {
      html += `<div id="res-${key}" data-type="skills_picker" style="margin-top:2px;background:#0f0f23;border:1px solid #333;border-radius:4px;padding:6px;max-height:120px;overflow-y:auto;${roS}">`;
      html += '<div style="color:#555;font-size:11px;">Loading skills...</div>';
      html += '</div>';
    } else if (type === 'number') {
      html += `<input id="res-${key}" type="number" value="${escaped}"${dis} style="width:80px;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;${roS}"/>`;
    } else {
      html += `<input id="res-${key}" value="${escaped}"${dis} style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;${roS}"/>`;
    }
    html += '</div>';
  }
  return html;
}

function _addParamRow(container) {
  const table = container.querySelector('table');
  const tr = document.createElement('tr');
  tr.className = 'param-row';
  tr.style.borderTop = '1px solid #222';
  tr.innerHTML = '<td style="padding:3px 4px;"><input class="pe-name" value="" style="width:100%;background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;"/></td>'
    + '<td style="padding:3px 4px;"><select class="pe-type" style="background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;"><option value="string">string</option><option value="number">number</option><option value="boolean">boolean</option></select></td>'
    + '<td style="padding:3px 4px;"><input class="pe-default" value="" style="width:100%;background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;"/></td>'
    + '<td style="padding:3px 4px;"><input class="pe-desc" value="" style="width:100%;background:#0a0a1a;color:#e0e0e0;border:1px solid #333;padding:3px;border-radius:3px;font-size:11px;"/></td>'
    + '<td style="padding:3px 2px;"><button onclick="this.closest(\'tr\').remove()" style="background:none;border:none;color:#e74c3c;cursor:pointer;font-size:14px;">&times;</button></td>';
  table.appendChild(tr);
}

function _collectParams(key) {
  const container = document.getElementById('res-' + key);
  if (!container || container.dataset.type !== 'params_editor') return undefined;
  const rows = container.querySelectorAll('.param-row');
  const params = {};
  rows.forEach(row => {
    const name = (row.querySelector('.pe-name')?.value || '').trim();
    if (!name) return;
    const entry = { type: row.querySelector('.pe-type')?.value || 'string' };
    const def = (row.querySelector('.pe-default')?.value || '').trim();
    if (def) entry.default = def;
    const desc = (row.querySelector('.pe-desc')?.value || '').trim();
    if (desc) entry.description = desc;
    params[name] = entry;
  });
  return Object.keys(params).length ? params : undefined;
}

function _loadSkillsPicker(container, selected, readonly) {
  action$('list_skills', {}).subscribe(data => {
    const skills = data.skills || [];
    if (!skills.length) {
      container.innerHTML = '<div style="color:#555;font-size:11px;">No skills defined</div>';
      return;
    }
    const dis = readonly ? ' disabled' : '';
    container.innerHTML = skills.map(s => {
      const checked = selected.indexOf(s.name) >= 0 ? ' checked' : '';
      return '<label style="display:flex;align-items:center;gap:6px;padding:2px 0;cursor:' + (readonly ? 'default' : 'pointer') + ';font-size:12px;color:#c0c0d0;">'
        + '<input type="checkbox" class="skill-cb" value="' + escapeHtml(s.name) + '"' + checked + dis + ' style="accent-color:#6c5ce7;"/>'
        + escapeHtml(s.name)
        + (s.description ? ' <span style="color:#666;font-size:10px;">\u2014 ' + escapeHtml(s.description) + '</span>' : '')
        + '</label>';
    }).join('');
  });
}

function _collectSkillsPicker(key) {
  var container = document.getElementById('res-' + key);
  if (!container || container.getAttribute('data-type') !== 'skills_picker') return null;
  var cbs = container.querySelectorAll('.skill-cb:checked');
  return Array.from(cbs).map(function(cb) { return cb.value; });
}

async function showResourceEditor(rtype, name, readonly) {
  // Fetch current data
  let data = {};
  try {
    data = await rxjs.firstValueFrom(action$('get_resource_detail', { resource_type: rtype, name }));
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
  // Populate skills picker if present
  var skPicker = panel.querySelector('[data-type="skills_picker"]');
  if (skPicker) {
    var selected = Array.isArray(data.assigned_skills) ? data.assigned_skills : [];
    _loadSkillsPicker(skPicker, selected, !!readonly);
  }
}

function _saveResourceEdit(rtype, name, scope) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    if (type === 'skills_picker') { data[key] = _collectSkillsPicker(key) || []; continue; }
    if (type === 'params_editor') { const p = _collectParams(key); if (p) data[key] = p; continue; }
    const el = document.getElementById('res-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }
  action$('update_resource', { resource_type: rtype, name, scope, data }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `${rtype} '${name}' updated.`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
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
    <h3 style="margin:0;color:#e0e0e0;font-size:14px;">New ${rtype === '_tool' ? 'Tool' : rtype}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:#888;cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, {}, true)
    + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>
    <button onclick="_saveResourceCreate('${rtype}')" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Create</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  // Populate skills picker if present (empty selection for new)
  var skPicker = panel.querySelector('[data-type="skills_picker"]');
  if (skPicker) _loadSkillsPicker(skPicker, [], false);
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
    if (type === 'skills_picker') { data[key] = _collectSkillsPicker(key) || []; continue; }
    if (type === 'params_editor') { const p = _collectParams(key); if (p) data[key] = p; continue; }
    const el = document.getElementById('res-' + key);
    if (el) data[key] = type === 'number' ? parseInt(el.value) || 0 : el.value;
  }
  // Dynamic tools use a dedicated action (CreateToolHandler pipeline)
  if (rtype === '_tool') {
    let params = {};
    try { params = data.parameters ? JSON.parse(data.parameters) : {}; } catch(e) { alert('Parameters must be valid JSON'); return; }
    action$('create_dynamic_tool', {
      tool_name: name, tool_description: data.tool_description || '',
      parameters: params, code: data.code || ''
    }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else { addMsg('system', `Tool '${name}' created.`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
    });
    return;
  }
  action$('create_resource', { resource_type: rtype, name, scope, data }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', `${rtype} '${name}' created.`); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
}

function _removeAgentFromConv(name) {
  var convAgents = document.querySelectorAll('#res-section-agent > div');
  if (convAgents.length <= 1) {
    if (!confirm('Remove the last agent from this conversation?')) return;
  }
  cmdResourceAction('remove_agent_from_conv', {name: name, conversation_id: conversationId})
    .then(loadResources);
}

function _addAgentToConv(name) {
  cmdResourceAction('add_agent_to_conv', {name: name, conversation_id: conversationId})
    .then(loadResources);
}

async function showAddAgentToConvDialog() {
  var existing = document.getElementById('resourceEditorOverlay');
  if (existing) existing.remove();
  var overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  var panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = '<p style="color:#e0e0e0;font-weight:600;">Add Agent to Conversation</p><p style="color:#888;">Loading...</p>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
  try {
    var data = await rxjs.firstValueFrom(action$('list_repo_agents', {}));
    var definitions = data.agents || [];
    var llmServices = data.llm_services || [];
    var selectedDef = null;
    panel.innerHTML = '';

    var header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';
    header.innerHTML = '<strong style="color:#e0e0e0;">Add Agent to Conversation</strong>';
    var closeBtn = document.createElement('button');
    closeBtn.textContent = '\u00d7';
    closeBtn.style.cssText = 'background:none;border:none;color:#888;cursor:pointer;font-size:18px;';
    closeBtn.onclick = function() { overlay.remove(); };
    header.appendChild(closeBtn);
    panel.appendChild(header);

    // Definition selector
    var defLabel = document.createElement('label');
    defLabel.style.cssText = 'color:#aaa;font-size:11px;';
    defLabel.textContent = 'Definition (template)';
    panel.appendChild(defLabel);
    var defSelect = document.createElement('select');
    defSelect.style.cssText = 'width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin:4px 0 12px;';
    defSelect.innerHTML = '<option value="">-- Select a definition --</option>'
      + definitions.map(function(d) {
        return '<option value="' + escapeHtml(d.name) + '">' + escapeHtml(d.name)
          + (d.description ? ' \u2014 ' + escapeHtml(d.description) : '') + '</option>';
      }).join('');
    panel.appendChild(defSelect);

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
      var html = '<div style="padding:10px;border:1px solid #333;border-radius:4px;background:#0d1117;">';
      // Instance name
      html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">Instance Name *</label>'
        + '<input id="_addInstName" value="' + escapeHtml(selectedDef) + '" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
      // LLM Service
      html += '<div style="margin-bottom:8px;"><label style="color:#aaa;font-size:11px;">LLM Service *</label>'
        + '<select id="_addLlm" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;">'
        + svcOpts + '</select></div>';
      // Params from schema — skip 'name' (always synced from instance_name)
      var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
      if (visibleParamKeys.length) {
        html += '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #333;">'
          + '<div style="font-size:11px;color:#6c5ce7;margin-bottom:6px;font-weight:600;">Parameters</div>';
        visibleParamKeys.forEach(function(k) {
          var spec = paramSchema[k] || {};
          var defVal = spec.default || '';
          html += '<div style="margin-bottom:6px;"><label style="color:#aaa;font-size:11px;">'
            + escapeHtml(k + (spec.required ? ' *' : '')) + '</label>'
            + '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(defVal)) + '" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
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
    createLink.style.cssText = 'margin-top:12px;border-top:1px solid #333;padding-top:10px;font-size:11px;';
    var cl = document.createElement('span');
    cl.style.cssText = 'color:#6c5ce7;cursor:pointer;';
    cl.textContent = '+ Create new definition in repository';
    cl.onclick = function() { overlay.remove(); showResourceCreator('agent'); };
    createLink.appendChild(cl);
    panel.appendChild(createLink);

    var btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:12px;';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = 'background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;';
    cancelBtn.onclick = function() { overlay.remove(); };
    var addBtn = document.createElement('button');
    addBtn.textContent = 'Add Agent';
    addBtn.style.cssText = 'background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;';
    addBtn.onclick = async function() {
      if (!selectedDef) { alert('Select a definition first.'); return; }
      var instName = (document.getElementById('_addInstName') || {}).value || '';
      var llm = (document.getElementById('_addLlm') || {}).value || '';
      if (!instName.trim()) { alert('Instance name is required.'); return; }
      if (!llm) { alert('LLM Service is required.'); return; }
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
    err.style.cssText = 'color:#e94560;font-size:12px;';
    err.textContent = 'Error: ' + e.message;
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
  <details style="margin-bottom:8px;"><summary style="color:#888;font-size:11px;cursor:pointer;">Limits (optional)</summary>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px;">
      <div><label style="color:#888;font-size:10px;">Max Budget</label><input id="assign-budget" placeholder="$5" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:#888;font-size:10px;">Turn Time</label><input id="assign-turn-time" placeholder="5m" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:#888;font-size:10px;">Total Time</label><input id="assign-total-time" placeholder="1h" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:#888;font-size:10px;">Max Reschedules</label><input id="assign-max-resched" placeholder="50" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:4px;border-radius:4px;font-size:11px;"/></div>
    </div></details>
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
    else { addMsg('system', d.result || 'Task assigned.'); loadResources(); }
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
    action$(action + '_task', { task_id: taskId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', `Task ${taskId} ${action}d.`);
      loadResources();
    });
  };
  // View task log
  item('\u{1F4CB} View Log', () => {
    action$('task_log', { name: taskId }).subscribe(d => {
      const log = d.log || [];
      if (!log.length) { addMsg('system', 'No log entries for ' + taskId); return; }
      const lines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
      addMsg('system', '\u{1F4CB} Task log ' + taskId + ':\n' + lines);
    });
  });
  // View task details
  item('\u{1F441} View Details', () => {
    action$('list_resources', {}).subscribe(d => {
      const task = (d.all_tasks || []).find(t => t.task_id === taskId);
      if (!task) { addMsg('system', 'Task not found: ' + taskId); return; }
      const info = [`Task: ${task.task_id}`, `Agent: ${task.agent}`, `Status: ${task.status}`,
        `Iterations: ${task.iterations}/${task.max_iterations}`, `Definition: ${task.task_def_name || '-'}`,
        `Prompt: ${task.task}`].join('\n');
      addMsg('system', info);
    });
  });
  // Delete
  const sep = document.createElement('div');
  sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
  menu.appendChild(sep);
  item('\u{1F5D1} Delete', () => _taskAction('delete'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
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
    action$(action + '_task', { task_id: taskId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', `Task ${taskId} ${action}d.`);
      loadResources();
    });
  };
  // View task log
  item('\u{1F4CB} View Log', () => {
    action$('task_log', { name: taskId }).subscribe(d => {
      const log = d.log || [];
      if (!log.length) { addMsg('system', 'No log entries for ' + taskId); return; }
      const lines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
      addMsg('system', '\u{1F4CB} Task log ' + taskId + ':\n' + lines);
    });
  });
  // Edit limits
  item('\u270F Edit Limits', () => _showEditLimitsDialog(taskId));
  // Status-specific actions
  if (status === 'active') {
    item('\u23F8 Pause', () => _taskAction('pause'));
  } else if (status === 'paused') {
    item('\u25B6 Resume', () => _taskAction('resume'));
  } else if (status === 'cancelled' || status === 'failed') {
    item('\u25B6 Restart', () => _taskAction('resume'));
  }
  if (status === 'active' || status === 'paused') {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
    menu.appendChild(sep);
    item('\u{1F5D1} Cancel', () => _taskAction('cancel'), true);
  }
  // Delete: remove task instance entirely
  const sep2 = document.createElement('div');
  sep2.style.cssText = 'height:1px;background:#333;margin:4px 0;';
  menu.appendChild(sep2);
  item('\u{1F5D1} Delete', () => _taskAction('delete'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

function _showEditLimitsDialog(taskId) {
  // Fetch current task data
  action$('task_status', {}).subscribe(d => {
    const task = (d.tasks || []).find(t => t.task_id === taskId);
    if (!task) { addMsg('error', 'Task not found: ' + taskId); return; }
    const overlay = document.createElement('div');
    overlay.id = 'resourceEditorOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = (ev) => { if (ev.target === overlay) overlay.remove(); };
    const panel = document.createElement('div');
    panel.style.cssText = 'background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:20px;min-width:340px;max-width:420px;color:#e0e0e0;';
    const _f = (id, label, val, ph) => `<div style="margin-bottom:8px;"><label style="font-size:11px;color:#888;">${label}</label><input id="${id}" value="${val||''}" placeholder="${ph}" style="width:100%;background:#0f0f23;color:#e0e0e0;border:1px solid #333;padding:6px;border-radius:4px;margin-top:2px;font-size:12px;"/></div>`;
    panel.innerHTML = `<div style="font-weight:bold;margin-bottom:12px;">Edit Limits — ${taskId}</div>`
      + _f('el-budget', 'Max Budget ($)', task.max_budget || '', '$5.00')
      + _f('el-turn', 'Max Turn Time', task.timeout ? task.timeout+'s' : '', '5m')
      + _f('el-total', 'Max Total Time', task.max_total_time ? task.max_total_time+'s' : '', '1h')
      + _f('el-resched', 'Max Reschedules', task.max_reschedules || '', '50')
      + _f('el-maxiter', 'Max Iterations', task.max_iterations || '', '50')
      + `<div style="font-size:10px;color:#666;margin-bottom:8px;">Current: cost=$${(task.total_cost||0).toFixed(4)}, reschedules=${task.reschedule_count||0}</div>`
      + `<div style="display:flex;gap:8px;justify-content:flex-end;"><button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button><button id="el-save" style="background:#6c5ce7;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Save</button></div>`;
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
        else addMsg('system', 'Task limits updated: ' + (data.changed||[]).join(', '));
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
    if (!instances.length) { addMsg('system', 'No task instances for definition "' + defName + '"'); return; }
    let lines = instances.map(t => {
      const icon = t.status === 'active' ? '\u25B6' : t.status === 'paused' ? '\u23F8' : t.status === 'completed' ? '\u2705' : t.status === 'cancelled' ? '\u2718' : '\u26A0';
      return icon + ' ' + t.task_id + ' (' + t.agent + ') — ' + t.status + ' [' + t.iterations + '/' + t.max_iterations + ']';
    }).join('\n');
    addMsg('system', '\u{1F4DC} Instances of "' + defName + '":\n' + lines);
    // Also fetch logs for each instance
    for (const inst of instances) {
      action$('task_log', { name: inst.task_id }).subscribe(ld => {
        const log = ld.log || [];
        if (log.length) {
          const logLines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
          addMsg('system', '\u{1F4CB} Log ' + inst.task_id + ':\n' + logLines);
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
  item('\u{1F441} View config...', () => showServiceEditForm(serviceId, scope, true));
  if (_canEditScope(scope)) {
    item('\u270F Edit...', () => showServiceEditForm(serviceId, scope));
  }
  item(enabled ? '\u23F8 Disable' : '\u25B6 Enable', () => {
    action$('toggle_service', { service_id: serviceId, enabled: !enabled }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else loadResources();
    });
  });
  if (_canEditScope(scope)) {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:#333;margin:4px 0;';
    menu.appendChild(sep);
    item('\u{1F5D1} Delete', () => {
      if (!confirm(`Delete service '${serviceId}'?`)) return;
      action$('delete_service', { service_id: serviceId, scope }).subscribe(d => {
        if (d.error) addMsg('error', d.error);
        else { addMsg('system', `Service '${serviceId}' deleted.`); loadResources(); }
      });
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

// -- Slash command handlers for claude login --

function cmdClaudeLoginServer(parts) {
  const sub = (parts[1] || '').toLowerCase();

  // /cls pool [@service] — list credentials
  if (sub === 'pool') {
    var svcId = stripTarget(parts[2]) || 'claude_code_llm_service';
    action$('claude_pool_list', { service_id: svcId }).subscribe(data => {
      if (!data.pool || !data.pool.length) { addMsg('system', 'No credentials in pool.'); return; }
      var lines = ['**Credentials Pool** (' + data.count + '):'];
      data.pool.forEach(function(c) {
        lines.push('  ' + c.index + '. ' + (c.account || '(unknown)') + ' — expires: ' + c.expires_in);
      });
      addMsg('system', lines.join('\n'));
    });
    return true;
  }

  // /cls reset [@service] — clear all credentials
  if (sub === 'reset') {
    var svcId2 = stripTarget(parts[2]) || 'claude_code_llm_service';
    action$('claude_pool_reset', { service_id: svcId2 }).subscribe(data => {
      addMsg('system', data.message || data.error || 'Done');
    });
    return true;
  }

  // /cls remove <index> [@service] — remove one credential
  if (sub === 'remove') {
    var idx = parseInt(parts[2] || '-1', 10);
    var svcId3 = stripTarget(parts[3]) || 'claude_code_llm_service';
    if (idx < 0) { addMsg('error', 'Usage: /cls remove <index> [@service]'); return true; }
    action$('claude_pool_remove', { service_id: svcId3, index: idx }).subscribe(data => {
      addMsg('system', data.message || data.error || 'Done');
    });
    return true;
  }

  // /cls <service> — login (add credential to pool)
  var serviceId = stripTarget(sub);
  if (!serviceId) { addMsg('error', 'Usage: /cls @<service> | /cls pool | /cls reset | /cls remove <N>'); return true; }
  if (window._clsLoginPending) { addMsg('system', 'Login already in progress...'); return true; }
  window._clsLoginPending = true;
  addMsg('system', 'Starting Claude Code login for ' + serviceId + '... (container starting, please wait)');
  fireAction('claude_code_server_login', { service_id: serviceId });
  // Reset after 60s (container timeout)
  setTimeout(function() { window._clsLoginPending = false; }, 60000);
  return true;
}

function cmdClaudeLoginRelay(parts) {
  const serviceId = stripTarget(parts[1]);
  const relayId = stripTarget(parts[2]);
  if (!serviceId) { addMsg('error', 'Usage: /claude-login-relay <service_name> [relay_name]'); return true; }

  if (relayId) {
    _startRelayLogin(serviceId, relayId);
    return true;
  }

  // No relay specified — list and auto-select if single
  action$('claude_code_list_relays', { service_id: serviceId }).subscribe(resp => {
    const relays = resp.relays || [];
    if (relays.length === 0) { addMsg('error', 'No relay connected.'); return; }
    if (relays.length === 1) {
      _startRelayLogin(serviceId, relays[0].relay_id);
    } else {
      addMsg('system', 'Multiple relays available. Specify one:\n'
        + relays.map(r => '  ' + r.relay_id + ' (' + r.platform + ')').join('\n'));
    }
  });
  return true;
}

function cmdClaudeLoginCredentials(text, parts) {
  const serviceId = stripTarget(parts[1]);
  if (!serviceId) { addMsg('error', 'Usage: /claude-login-credentials <service_name> <credentials_json>'); return true; }
  const jsonStart = text.indexOf(parts[1]) + parts[1].length;
  const credsJson = text.substring(jsonStart).trim();
  if (!credsJson) { addMsg('error', 'Missing credentials JSON. Paste the content of .credentials.json'); return true; }
  try {
    JSON.parse(credsJson);
  } catch (e) {
    addMsg('error', 'Invalid JSON: ' + e.message);
    return true;
  }
  fireAction('claude_code_login_code', { service_id: serviceId, credentials: credsJson });
  return true;
}

function _openVncLoginDialog(sessionId, serviceId, triggerBtn) {
  window._clsLoginPending = false;
  // Create overlay dialog 80%x80%
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
  const dialog = document.createElement('div');
  dialog.style.cssText = 'width:80%;height:80%;background:#1a1a2e;border-radius:8px;display:flex;flex-direction:column;overflow:hidden;';
  const header = document.createElement('div');
  header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 16px;background:#16213e;';
  header.innerHTML = '<span style="color:#aaa;font-size:13px;">Claude Code Login</span>'
    + '<button id="vnc-dialog-close" style="background:none;border:none;color:#e94560;font-size:18px;cursor:pointer;">&times;</button>';
  const vncUrl = '/vnc/' + sessionId + '/vnc.html?autoconnect=true&resize=scale'
    + '&path=vnc/' + sessionId + '/websockify';
  const iframe = document.createElement('iframe');
  iframe.src = vncUrl;
  iframe.style.cssText = 'flex:1;border:none;background:#000;';
  iframe.allow = 'clipboard-read; clipboard-write';
  const status = document.createElement('div');
  status.style.cssText = 'padding:6px 16px;color:#aaa;font-size:11px;background:#16213e;';
  status.textContent = 'Waiting for authorization...';

  dialog.appendChild(header);
  dialog.appendChild(iframe);
  dialog.appendChild(status);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  function closeDialog(msg) {
    clearInterval(pollInterval);
    overlay.remove();
    if (triggerBtn) {
      triggerBtn.textContent = 'Login via server';
      triggerBtn.disabled = false;
      triggerBtn.style.display = '';
    }
    if (msg) addMsg('system', msg);
    // Tell server to cleanup the Docker container
    fireAction('claude_code_server_login_cleanup', { session_id: sessionId });
  }

  document.getElementById('vnc-dialog-close').onclick = () => closeDialog(null);
  overlay.onclick = (e) => { if (e.target === overlay) closeDialog(null); };

  // Poll for completion
  const pollInterval = setInterval(async () => {
    try {
      const st = await rxjs.firstValueFrom(action$('claude_code_server_login_status', {
        session_id: sessionId, service_id: serviceId }));
      if (st.ok) { closeDialog(st.message || 'Claude Code login successful!'); }
      else if (st.error) { closeDialog('Login error: ' + st.error); }
      else if (st.status === 'starting') { status.textContent = 'Starting container...'; }
      else if (st.status === 'pending') { status.textContent = 'Waiting for authorization...'; }
    } catch (e) { /* ignore */ }
  }, 3000);
}

function _startRelayLogin(serviceId, relayId) {
  addMsg('system', 'Starting Claude Code login via relay — authorize in the browser...');
  fireAction('claude_code_relay_login', { service_id: serviceId, relay_id: relayId });
}

async function _executeServiceAction(actionId, serviceId, flow, serverAction) {
  const btn = event && event.target ? event.target : null;
  if (flow === 'claude_login_server') {
    try {
      if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }
      fireAction(serverAction, { service_id: serviceId });
      // Dialog opens when SSE vnc_login_ready arrives
    } catch (e) { addMsg('error', 'Action failed: ' + e.message); }
  } else if (flow === 'claude_login_relay') {
    try {
      // Step 1: list relays
      const resp = await rxjs.firstValueFrom(action$(serverAction, { service_id: serviceId }));
      if (resp.error) { addMsg('error', resp.error); return; }
      const relays = resp.relays || [];
      if (relays.length === 0) {
        addMsg('system', 'No relay connected. Use "Set credentials" instead.');
        return;
      }
      // Single relay → skip selector, start directly
      if (relays.length === 1) {
        if (btn) { btn.disabled = true; btn.textContent = 'Waiting for authorization...'; }
        await _startRelayLogin(serviceId, relays[0].relay_id);
        if (btn) { btn.disabled = false; btn.textContent = 'Login via relay'; }
        return;
      }
      // Multiple relays → show selector
      const container = btn ? btn.parentElement : null;
      if (!container) return;
      const div = document.createElement('div');
      div.style.cssText = 'margin-top:8px;';
      let selectHtml = '<select id="svc-relay-select" style="' + _svcInputStyle + 'margin-bottom:4px;">';
      relays.forEach(r => {
        const label = r.relay_id + ' (' + r.platform + ')';
        selectHtml += '<option value="' + escapeHtml(r.relay_id) + '">' + escapeHtml(label) + '</option>';
      });
      selectHtml += '</select>';
      div.innerHTML = selectHtml
        + '<button type="button" id="svc-relay-login-btn" style="background:#6c5ce7;color:white;border:none;'
        + 'padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;">Start login</button>'
        + '<div id="svc-relay-status" style="color:#aaa;font-size:11px;margin-top:4px;"></div>';
      container.appendChild(div);

      document.getElementById('svc-relay-login-btn').addEventListener('click', () => {
        const relayId = document.getElementById('svc-relay-select').value;
        const statusEl = document.getElementById('svc-relay-status');
        const loginBtn = document.getElementById('svc-relay-login-btn');
        loginBtn.disabled = true;
        loginBtn.textContent = 'Waiting for authorization...';
        statusEl.textContent = 'A browser window should open on the relay machine. Authorize there.';

        fireAction('claude_code_relay_login', {
          service_id: serviceId,
          relay_id: relayId,
        });
        statusEl.textContent = 'Authorize in the browser that opens on the relay...';
        // Result arrives via SSE command_result
      });
    } catch (e) { addMsg('error', 'Action failed: ' + e.message); }
  } else if (flow === 'oauth_code') {
    try {
      // Step 1: get instructions
      const resp = await rxjs.firstValueFrom(action$(serverAction, { service_id: serviceId }));
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
            const result = await rxjs.firstValueFrom(action$(serverAction.replace('_url', '_code'), { service_id: serviceId, credentials: creds }));
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
      const resp = await rxjs.firstValueFrom(action$(serverAction, { service_id: serviceId }));
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
    const data = await rxjs.firstValueFrom(action$('get_service_schema', { service_type: serviceType }));
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
    const data = await rxjs.firstValueFrom(action$('list_service_types', {}));
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
    const data = await rxjs.firstValueFrom(action$('service_install', { service_name: name, service_type: svcType, description: desc, config, scope }));
    if (data.error) { addMsg('error', data.error); btn.disabled = false; btn.textContent = 'Install'; return; }
    addMsg('system', 'Service \'' + name + '\' installed successfully.');
    document.getElementById('resourceEditorOverlay').remove();
    loadResources();
  } catch (e) { addMsg('error', e.message); btn.disabled = false; btn.textContent = 'Install'; }
}

async function showServiceEditForm(serviceId, scope, readonly) {
  try {
    const data = await rxjs.firstValueFrom(action$('get_service_detail', { service_id: serviceId, scope }));
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
    const data = await rxjs.firstValueFrom(action$('update_service', { service_id: serviceId, scope, config }));
    if (data.error) { addMsg('error', data.error); btn.disabled = false; btn.textContent = 'Save'; return; }
    addMsg('system', 'Service \'' + serviceId + '\' updated.');
    document.getElementById('resourceEditorOverlay').remove();
    loadResources();
  } catch (e) { addMsg('error', e.message); btn.disabled = false; btn.textContent = 'Save'; }
}
