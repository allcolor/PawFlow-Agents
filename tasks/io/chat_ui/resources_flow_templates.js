// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

function _flowPackageSectionId(packageName) {
  const raw = String(packageName || 'default').toLowerCase();
  return '_flow_pkg_' + raw.replace(/[^a-z0-9_]+/g, '_');
}

function showFlowTemplateMenu(e, templateId) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const tpl = _findFlowTemplate(templateId) || {};
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:150px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)') + ';';
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  const sep = () => { const s = document.createElement('div'); s.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;'; menu.appendChild(s); };
  item('\u25B6 ' + t('deploy'), () => showDeployFlowDialog(templateId));
  item('\uD83D\uDCC8 ' + t('flowViewGraph'), () => _openFlowTemplateGraphTab(templateId));
  sep();
  item('\uD83D\uDCE6 ' + t('flowMoveToPackage'), () => _moveFlowTemplateToPackage(templateId, tpl));
  item('\u2191 ' + t('promote'), () => _moveFlowTemplateScope(templateId, 'global'));
  item('\u2195 ' + t('flowMoveToUserScope'), () => _moveFlowTemplateScope(templateId, 'user'));
  if (typeof conversationId !== 'undefined' && conversationId) {
    item('\u2193 ' + t('flowMoveToConversationScope'), () => _moveFlowTemplateScope(templateId, 'conversation'));
  }
  sep();
  item('\u{1F5D1} ' + t('delete'), () => _deleteFlowTemplate(templateId), true);
  document.body.appendChild(menu);
  _positionMenu(menu, e);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

function _findFlowTemplate(templateId) {
  const templates = (_lastResourcesData && _lastResourcesData.flow_templates) || [];
  return templates.find(tpl => tpl.id === templateId || tpl.fqn === templateId || tpl.name === templateId) || null;
}

function _flowTemplatePayload(templateId, extra) {
  const payload = Object.assign({ template_id: templateId }, extra || {});
  if (typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  return payload;
}

function _flowTemplateMutationOptions(targetScope) {
  return { skipConversationId: !(targetScope === 'conversation') };
}

function _refreshResourcesNow() {
  if (_loadResourcesTimer) clearTimeout(_loadResourcesTimer);
  _loadResourcesNow();
}

function _moveFlowTemplateToPackage(templateId, tpl) {
  const current = (tpl && tpl.package) || 'default';
  const nextPackage = prompt(t('flowMoveToPackagePrompt', { current: current }), current);
  if (!nextPackage || nextPackage === current) return;
  if (!confirm(t('flowMoveToPackageConfirm', { id: templateId, package: nextPackage }))) return;
  action$('move_flow_template_package', _flowTemplatePayload(templateId, { package: nextPackage })).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('flowTemplateMovedToPackage', { id: templateId, package: nextPackage }));
    _refreshResourcesNow();
  });
}

function _moveFlowTemplateScope(templateId, targetScope) {
  if (!confirm(t('flowTemplateMoveScopeConfirm', { id: templateId, scope: targetScope }))) return;
  action$('promote_flow_template', _flowTemplatePayload(templateId, { target_scope: targetScope }), _flowTemplateMutationOptions(targetScope)).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('flowTemplateMovedToScope', { id: templateId, scope: targetScope }));
    _refreshResourcesNow();
  });
}

function _deleteFlowTemplate(templateId) {
  if (!confirm(t('flowTemplateDeleteConfirm', { id: templateId }))) return;
  action$('delete_flow_template', _flowTemplatePayload(templateId)).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('flowTemplateDeleted', { id: templateId }));
    _refreshResourcesNow();
  });
}

function _renderFlowPackageGroup(packageName, flows) {
  const sectionId = _flowPackageSectionId(packageName);
  const collapsed = _isSectionCollapsed(sectionId);
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  const display = collapsed ? 'none' : 'block';
  let html = `<div style="margin:2px 0 4px 8px;">
    <div style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;" onclick="_toggleSection('${sectionId}')">
      <span id="res-arrow-${sectionId}" style="font-size:10px;color:var(--pf-muted);">${arrow}</span>
      <span style="font-size:12px;color:var(--pf-text);font-weight:600;flex:1;">${escapeHtml(packageName || 'default')}</span>
    </div>
    <div id="res-section-${sectionId}" style="display:${display};margin-top:2px;">`;
  flows.forEach(t => {
    const ver = t.version ? ` v${escapeHtml(t.version)}` : '';
    const desc = t.description ? ` title="${_pfpAttr(t.description)}"` : '';
    html += `<div style="display:flex;align-items:center;gap:4px;margin-left:14px;margin-bottom:2px;cursor:pointer;"${desc} onclick="showDeployFlowDialog(${_pfpJsArg(t.id)})" oncontextmenu="showFlowTemplateMenu(event,${_pfpJsArg(t.id)});return false;">
      ${_scopeBadge(t.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(t.name)}${ver}</span>
      <span style="color:var(--pf-muted);font-size:10px;">[${escapeHtml(t.tasks_count)} tasks]</span>${_ownerBadge(t)}
    </div>`;
  });
  html += '</div></div>';
  return html;
}

function _showRelayLinkDialog() {
  action$('relay_list_available').subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var relays = data.relays || [];
    if (!relays.length) { addMsg('system', t('noRelaysAvailableConnectFirst')); return; }
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
      + '<h3>' + escapeHtml(t('linkRelay')) + '</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_relayLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doRelayLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}
function _showRelayInfoDialog(relayId, details, isDefault) {
  if (typeof details === 'string') try { details = JSON.parse(details); } catch(e) { details = {}; }
  var d = details || {};
  var dl = d._default_local || {};
  var rows = [
    [t('relayId'), relayId],
    [t('connected'), d.connected ? '\u{1F7E2} ' + t('yes') : (d.connecting ? '\u{1F7E1} ' + t('starting') : '\u{1F534} ' + t('no'))],
    [t('dockerRoot'), d.root || '\u2014'],
    [t('localRoot'), d.host_root || '\u2014'],
    [t('platform'), d.platform || '\u2014'],
    [t('containerized'), d.containerized ? t('yes') : t('no')],
    [t('allowLocal'), d.allow_local ? '\u2705 ' + t('yes') : '\u274c ' + t('no')],
  ];
  var infoHtml = '<table style="margin:8px 0;">' + rows.map(function(r) {
    return '<tr><td style="color:var(--pf-muted);padding:3px 12px 3px 0;font-size:12px;white-space:nowrap;">' + escapeHtml(r[0]) + '</td>'
      + '<td style="font-size:12px;">' + r[1] + '</td></tr>';
  }).join('') + '</table>';

  // Default local toggles (only if allow_local)
  var localHtml = '';
  if (d.allow_local) {
    var convLocal = dl['*'];
    var convLabel = convLocal === true ? t('local') : convLocal === false ? t('docker') : t('notSet');
    var convColor = convLocal === true ? 'var(--pf-success)' : convLocal === false ? 'var(--pf-danger)' : 'var(--pf-muted)';
    localHtml += '<div style="margin-top:8px;font-size:12px;font-weight:600;color:var(--pf-accent);">' + escapeHtml(t('defaultExecutionMode')) + '</div>';
    localHtml += '<div style="display:flex;align-items:center;gap:8px;margin:6px 0;font-size:12px;">'
      + '<span style="color:var(--pf-muted);min-width:80px;">' + escapeHtml(t('conversation')) + ':</span>'
      + '<span style="color:' + convColor + ';">' + convLabel + '</span>'
      + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-success);cursor:pointer;" '
      + 'onclick="_setRelayLocal(' + _pfpJsArg(relayId) + ',true,\'\')">' + escapeHtml(t('local')) + '</button>'
      + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-danger);cursor:pointer;" '
      + 'onclick="_setRelayLocal(' + _pfpJsArg(relayId) + ',false,\'\')">' + escapeHtml(t('docker')) + '</button>'
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
        var aLabel = aLocal === true ? t('local') : aLocal === false ? t('docker') : t('notSet');
        var aColor = aLocal === true ? 'var(--pf-success)' : aLocal === false ? 'var(--pf-danger)' : 'var(--pf-muted)';
        localHtml += '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px;">'
          + '<span style="color:var(--pf-muted);min-width:80px;">@' + escapeHtml(agentName) + ':</span>'
          + '<span style="color:' + aColor + ';">' + aLabel + '</span>'
          + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-success);cursor:pointer;" '
          + 'onclick="_setRelayLocal(' + _pfpJsArg(relayId) + ',true,' + _pfpJsArg(agentName) + ')">' + escapeHtml(t('local')) + '</button>'
          + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-danger);cursor:pointer;" '
          + 'onclick="_setRelayLocal(' + _pfpJsArg(relayId) + ',false,' + _pfpJsArg(agentName) + ')">' + escapeHtml(t('docker')) + '</button>'
          + '</div>';
      });
    } catch(e) {}
  }

  var overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  var defaultBtn = isDefault ? '' : '<button class="exec-approve" onclick="fireAction(\'relay_default\',{relay_id:' + _pfpJsArg(relayId) + '}); this.closest(\'.exec-overlay\').remove(); setTimeout(loadResources, 500)">' + escapeHtml(t('setDefaultRelay')) + '</button>';
  overlay.innerHTML = '<div class="exec-dialog" style="min-width:340px;">'
    + '<h3>' + escapeHtml(t('relayTitle', { id: relayId })) + '</h3>'
    + infoHtml + localHtml
    + '<div class="exec-btns">'
    + '<button class="exec-deny" onclick="fireAction(\'relay_unlink\',{relay_id:' + _pfpJsArg(relayId) + '}); this.closest(\'.exec-overlay\').remove(); setTimeout(loadResources, 500)">' + escapeHtml(t('unlink')) + '</button>'
    + defaultBtn
    + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('close')) + '</button></div>'
    + '</div>';
  document.body.appendChild(overlay);
}

function _setRelayLocal(relayId, local, agent) {
  action$('relay_set_local', {relay_id: relayId, local: local, agent: agent}).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.message || t('ok'));
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

function _showRemoteFsLinkDialog() {
  action$('remote_fs_status', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var linkedIds = new Set((data.linked || []).map(function(s) { return s.service_id || ''; }));
    var services = (data.available || []).filter(function(s) {
      return s.service_id && !linkedIds.has(s.service_id);
    });
    if (!services.length) { addMsg('system', t('noRemoteFilesystemsAvailable')); return; }
    window._remoteFsLinkOptions = services;
    var options = services.map(function(s, idx) {
      var access = s.service_type === 'rcloneFilesystem' ? t('mountedInRelays') : t('availableToTools');
      var label = '[' + (s.scope || 'user') + '] ' + s.service_id + ' (' + s.service_type + ', ' + access + ')';
      return '<option value="' + idx + '">' + escapeHtml(label) + '</option>';
    }).join('');
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    overlay.innerHTML =
      '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>' + escapeHtml(t('linkFilesystem')) + '</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_remoteFsLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doRemoteFsLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}

function _doRemoteFsLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_remoteFsLinkSelect');
  var idx = sel ? Number(sel.value) : -1;
  var svc = (window._remoteFsLinkOptions || [])[idx];
  overlay.remove();
  if (!svc) return;
  action$('remote_fs_link', {
    conversation_id: conversationId,
    service_id: svc.service_id,
    scope: svc.scope,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _unlinkRemoteFs(serviceId) {
  action$('remote_fs_unlink', {
    conversation_id: conversationId,
    service_id: serviceId,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _showSummarizerLinkDialog() {
  action$('summarizer_list_available', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var services = data.available || [];
    if (!services.length) { addMsg('system', t('noSummarizerServices')); return; }
    window._summarizerLinkOptions = services;
    var options = services.map(function(s, idx) {
      var llm = s.llm_service ? ' \u2192 ' + s.llm_service : '';
      var label = '[' + (s.scope || 'global') + '] ' + s.service_id + llm;
      return '<option value="' + idx + '">' + escapeHtml(label) + '</option>';
    }).join('');
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    overlay.innerHTML =
      '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>' + escapeHtml(t('linkSummarizer')) + '</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_summarizerLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doSummarizerLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}

function _doSummarizerLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_summarizerLinkSelect');
  var idx = sel ? Number(sel.value) : -1;
  var svc = (window._summarizerLinkOptions || [])[idx];
  overlay.remove();
  if (!svc) return;
  action$('summarizer_link', {
    conversation_id: conversationId,
    scope: svc.scope,
    service_id: svc.service_id,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _unlinkSummarizer() {
  action$('summarizer_unlink', { conversation_id: conversationId }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _showSummarizerMenu(e, canUnlink) {
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
  item(t('linkSummarizer') + '...', _showSummarizerLinkDialog);
  if (canUnlink) item(t('unlink'), _unlinkSummarizer, true);
  setTimeout(() => document.addEventListener('click', function _close() {
    menu.remove(); document.removeEventListener('click', _close);
  }), 0);
}

