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
  const canAuthor = typeof _canEditScope !== 'function' || _canEditScope(_flowEditorScope(tpl));
  item('\u25B6 ' + t('deploy'), () => showDeployFlowDialog(templateId));
  item('\uD83D\uDCC8 ' + t('flowViewGraph'), () => _openFlowTemplateGraphTab(templateId));
  if (canAuthor) item('\u270E ' + t('flowEditDraft'), () => _editFlowTemplate(templateId, tpl));
  item('\u2442 ' + t('flowFork'), () => _showForkFlowDialog(templateId, tpl));
  item('\uD83D\uDD52 ' + t('flowVersions'), () => _showFlowVersionsDialog(templateId, tpl));
  if (canAuthor) item('\u00B1 ' + t('flowDiff'), () => _showFlowDiffDialog(templateId, tpl));
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

function _flowEditorScope(tpl) {
  const raw = String((tpl && (tpl.scope || tpl._scope)) || 'user');
  return raw.startsWith('conv') ? 'conversation' : raw.startsWith('global') ? 'global' : 'user';
}

function _flowEditorFqn(templateId, tpl) {
  if (tpl && tpl.fqn) return String(tpl.fqn);
  const pkg = String((tpl && tpl.package) || 'default');
  const name = String((tpl && (tpl.name || tpl.id)) || templateId || 'flow');
  const version = String((tpl && tpl.version) || '');
  const base = name.includes('.') ? name : pkg + '.' + name;
  return version && !base.includes(':') ? base + ':' + version : base;
}

function _flowScopeOptions(selected) {
  const options = [['user', t('user')], ['global', t('global')]];
  if (typeof conversationId !== 'undefined' && conversationId) options.splice(1, 0, ['conversation', t('conversation')]);
  return options.map(([value, label]) => '<option value="' + value + '"' + (value === selected ? ' selected' : '') + '>' + escapeHtml(label) + '</option>').join('');
}

// Every authoring dialog closes through its \u2715, Escape, or any
// [data-close-dialog] button in its body: no overlay is ever stuck open.
function _flowAuthoringDialog(title, bodyHtml) {
  document.getElementById('_flowAuthoringOverlay')?.remove();
  const overlay = document.createElement('div');
  overlay.id = '_flowAuthoringOverlay';
  overlay.className = 'exec-overlay';
  overlay.innerHTML = '<div class="exec-dialog" style="min-width:440px;max-width:min(720px,calc(100vw - 32px));max-height:85vh;overflow:auto;">'
    + '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px;">'
    + '<h3 style="margin:0;">' + escapeHtml(title) + '</h3>'
    + '<button data-close-dialog title="' + _pfpAttr(t('close')) + '" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;line-height:1;">&times;</button></div>'
    + bodyHtml + '</div>';
  const onKey = (ev) => {
    if (!overlay.isConnected) { document.removeEventListener('keydown', onKey); return; }
    if (ev.key === 'Escape') { ev.preventDefault(); overlay.remove(); document.removeEventListener('keydown', onKey); }
  };
  overlay.querySelectorAll('[data-close-dialog]').forEach(button => button.onclick = () => { overlay.remove(); document.removeEventListener('keydown', onKey); });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(overlay);
  return overlay;
}

function _flowDialogCloseFooter() {
  return '<div class="exec-btns"><button class="exec-deny" data-close-dialog>' + escapeHtml(t('close')) + '</button></div>';
}

function _flowDialogField(label, id, value, textarea) {
  const tag = textarea
    ? '<textarea id="' + id + '" rows="4" style="width:100%;">' + escapeHtml(value || '') + '</textarea>'
    : '<input id="' + id + '" value="' + _pfpAttr(value || '') + '" style="width:100%;">';
  return '<label style="display:block;margin:8px 0;color:var(--pf-muted);font-size:12px;">'
    + escapeHtml(label) + tag + '</label>';
}

function _flowDialogScope(selected) {
  return '<label style="display:block;margin:8px 0;color:var(--pf-muted);font-size:12px;">'
    + escapeHtml(t('scope')) + '<select id="_feScope" style="width:100%;">'
    + _flowScopeOptions(selected) + '</select></label>';
}

function _flowDialogPayload(scope) {
  const payload = { scope };
  if (scope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  return payload;
}

function _showNewFlowDialog() {
  const overlay = _flowAuthoringDialog(t('flowNew'),
    _flowDialogField(t('package'), '_fePackage', 'my_flows')
    + _flowDialogField(t('name'), '_feName', '')
    + _flowDialogField(t('version'), '_feVersion', '1.0.0')
    + _flowDialogField(t('description'), '_feDescription', '', true)
    + '<label style="display:block;margin:8px 0;color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('flowTemplateKind'))
    + '<select id="_feTemplateKind" style="width:100%;"><option value="standard">' + escapeHtml(t('flowTemplateStandard')) + '</option>'
    + '<option value="agent_workflow">' + escapeHtml(t('flowTemplateAgentWorkflow')) + '</option></select></label>'
    + _flowDialogScope('user')
    + '<div class="exec-btns"><button class="exec-deny" data-cancel>' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button class="exec-approve" data-create>' + escapeHtml(t('flowNew')) + '</button></div>');
  overlay.querySelector('[data-cancel]').onclick = () => overlay.remove();
  overlay.querySelector('[data-create]').onclick = () => {
    const scope = overlay.querySelector('#_feScope').value;
    const payload = Object.assign(_flowDialogPayload(scope), {
      package: overlay.querySelector('#_fePackage').value.trim(),
      name: overlay.querySelector('#_feName').value.trim(),
      version: overlay.querySelector('#_feVersion').value.trim(),
      description: overlay.querySelector('#_feDescription').value,
      template_kind: overlay.querySelector('#_feTemplateKind').value,
    });
    action$('flow_editor_new', payload, { skipConversationId: scope !== 'conversation' }).subscribe({
      next: d => { if (d.error) addMsg('error', d.error); else { overlay.remove(); _openFlowEditorTab(d.draft.draft_id); } },
      error: e => addMsg('error', e.message),
    });
  };
}

function _showForkFlowDialog(templateId, tpl) {
  const sourceFqn = _flowEditorFqn(templateId, tpl);
  const sourceScope = _flowEditorScope(tpl);
  const originalName = String((tpl && (tpl.name || tpl.id)) || 'flow').replace(/[^A-Za-z0-9_-]/g, '_');
  const overlay = _flowAuthoringDialog(t('flowFork'),
    '<div style="font-size:12px;color:var(--pf-muted);">' + escapeHtml(sourceFqn) + '</div>'
    + _flowDialogField(t('package'), '_fePackage', (tpl && tpl.package) || 'my_flows')
    + _flowDialogField(t('name'), '_feName', originalName + '_fork')
    + _flowDialogField(t('version'), '_feVersion', '1.0.0')
    + _flowDialogScope('user')
    + '<div class="exec-btns"><button class="exec-deny" data-cancel>' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button class="exec-approve" data-fork>' + escapeHtml(t('flowFork')) + '</button></div>');
  overlay.querySelector('[data-cancel]').onclick = () => overlay.remove();
  overlay.querySelector('[data-fork]').onclick = () => {
    const scope = overlay.querySelector('#_feScope').value;
    const payload = Object.assign(_flowDialogPayload(scope), {
      source_fqn: sourceFqn, source_scope: sourceScope,
      package: overlay.querySelector('#_fePackage').value.trim(),
      name: overlay.querySelector('#_feName').value.trim(),
      version: overlay.querySelector('#_feVersion').value.trim(),
    });
    if (sourceScope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) {
      payload.conversation_id = conversationId;
    }
    action$('flow_editor_fork', payload, {
      skipConversationId: scope !== 'conversation' && sourceScope !== 'conversation',
    }).subscribe({
      next: d => { if (d.error) addMsg('error', d.error); else { overlay.remove(); _openFlowEditorTab(d.draft.draft_id); } },
      error: e => addMsg('error', e.message),
    });
  };
}

function _showFlowVersionsDialog(templateId, tpl) {
  const fqn = _flowEditorFqn(templateId, tpl);
  const scope = _flowEditorScope(tpl);
  const canAuthor = typeof _canEditScope !== 'function' || _canEditScope(scope);
  const options = { skipConversationId: scope !== 'conversation' };
  const overlay = _flowAuthoringDialog(t('flowVersions'),
    '<div data-content>' + escapeHtml(t('loading')) + '</div>' + _flowDialogCloseFooter());
  const render = () => action$('flow_editor_versions', Object.assign({ fqn }, _flowDialogPayload(scope)), options).subscribe(d => {
    const content = overlay.querySelector('[data-content]');
    if (!content) return;
    if (d.error) { content.textContent = d.error; return; }
    const versions = d.versions || [];
    content.innerHTML = versions.map(version => {
      const versionFqn = d.flow + ':' + version;
      return '<div style="display:flex;gap:8px;align-items:center;margin:6px 0;">'
        + '<code style="flex:1;">' + escapeHtml(version) + (version === d.latest ? ' · latest' : '') + '</code>'
        + '<button data-view="' + _pfpAttr(versionFqn) + '">' + escapeHtml(t('flowViewGraph')) + '</button>'
        + '<button data-edit="' + _pfpAttr(versionFqn) + '">' + escapeHtml(t('flowEditDraft')) + '</button>'
        // Versions are immutable: they are added by publish or deleted here,
        // never edited. The last one stays (delete the flow instead).
        + (canAuthor && versions.length > 1
          ? '<button data-delete="' + _pfpAttr(versionFqn) + '" title="' + _pfpAttr(t('flowDeleteVersion')) + '" style="color:var(--pf-danger);">\u{1F5D1}</button>'
          : '')
        + '</div>';
    }).join('') || '<div>' + escapeHtml(t('noFlowTemplates')) + '</div>';
    // Opening the graph or the editor closes the dialog: the new tab would
    // otherwise sit behind a modal overlay.
    content.querySelectorAll('[data-view]').forEach(button => button.onclick = () => { overlay.remove(); _openFlowTemplateGraphTab(button.dataset.view); });
    content.querySelectorAll('[data-edit]').forEach(button => button.onclick = () => { overlay.remove(); _editFlowTemplate(button.dataset.edit, { scope }); });
    content.querySelectorAll('[data-delete]').forEach(button => button.onclick = () => {
      const versionFqn = button.dataset.delete;
      if (!confirm(t('flowDeleteVersionConfirm', { fqn: versionFqn }))) return;
      action$('flow_editor_delete_version', Object.assign({ fqn: versionFqn }, _flowDialogPayload(scope)), options).subscribe(r => {
        if (r.error) { addMsg('error', r.error); return; }
        addMsg('system', t('flowVersionDeleted', { fqn: versionFqn }));
        _refreshResourcesNow();
        render();
      });
    });
  });
  render();
}

function _showFlowDiffDialog(templateId, tpl) {
  const fqn = _flowEditorFqn(templateId, tpl);
  const scope = _flowEditorScope(tpl);
  const overlay = _flowAuthoringDialog(t('flowDiff'),
    '<div data-content>' + escapeHtml(t('loading')) + '</div>' + _flowDialogCloseFooter());
  const payload = Object.assign({ fqn, reuse_existing: true }, _flowDialogPayload(scope));
  action$('flow_editor_create_draft', payload, { skipConversationId: scope !== 'conversation' }).subscribe(d => {
    if (d.error) { overlay.querySelector('[data-content]').textContent = d.error; return; }
    action$('flow_editor_diff', { draft_id: d.draft.draft_id }).subscribe(diff => {
      if (diff.error) { overlay.querySelector('[data-content]').textContent = diff.error; return; }
      const rows = (diff.changes || []).map(change => '<li><code>' + escapeHtml(change.op + ' ' + change.kind + ' ' + change.id)
        + '</code>' + (change.runtime_impact ? ' · runtime' : '') + '</li>').join('');
      overlay.querySelector('[data-content]').innerHTML = '<div>' + escapeHtml(String(diff.count || 0)) + ' change(s)</div><ul>'
        + rows + '</ul><div class="exec-btns"><button data-open>' + escapeHtml(t('flowEditDraft')) + '</button></div>';
      overlay.querySelector('[data-open]').onclick = () => { overlay.remove(); _openFlowEditorTab(d.draft.draft_id); };
    });
  });
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
function _showRelayInfoDialog(relayId, details, isDefault, bindingAgent) {
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
  var reconnectBtn = d.server_managed && _canEditScope(d.scope)
    ? '<button class="exec-approve" onclick="_reconnectServerRelay(this,' + _pfpJsArg(relayId) + ')">' + escapeHtml(t('reconnectRelay')) + '</button>'
    : '';
  var defaultBtn = isDefault ? '' : '<button class="exec-approve" onclick="fireAction(\'relay_default\',{relay_id:' + _pfpJsArg(relayId) + ',agent:' + _pfpJsArg(bindingAgent || '') + '}); this.closest(\'.exec-overlay\').remove(); setTimeout(loadResources, 500)">' + escapeHtml(t('setDefaultRelay')) + '</button>';
  overlay.innerHTML = '<div class="exec-dialog" style="min-width:340px;">'
    + '<h3>' + escapeHtml(t('relayTitle', { id: relayId })) + '</h3>'
    + infoHtml + localHtml
    + '<div class="exec-btns">'
    + '<button class="exec-deny" onclick="fireAction(\'relay_unlink\',{relay_id:' + _pfpJsArg(relayId) + ',agent:' + _pfpJsArg(bindingAgent || '') + '}); this.closest(\'.exec-overlay\').remove(); setTimeout(loadResources, 500)">' + escapeHtml(t('unlink')) + '</button>'
    + reconnectBtn
    + defaultBtn
    + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('close')) + '</button></div>'
    + '</div>';
  document.body.appendChild(overlay);
}

function _reconnectServerRelay(button, relayId) {
  button.disabled = true;
  button.textContent = t('reconnectingRelay');
  action$('relay_reconnect', {relay_id: relayId}).subscribe(function(data) {
    if (data.error) {
      addMsg('error', data.error);
      button.disabled = false;
      button.textContent = t('reconnectRelay');
      return;
    }
    addMsg('system', data.message || t('reconnectingRelay'));
    var overlay = button.closest('.exec-overlay');
    if (overlay) overlay.remove();
    setTimeout(loadResources, 300);
    setTimeout(loadResources, 2000);
  });
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

// ── Policy gate (docs/POLICY_GATING.md) ──────────────────────────────

function _showGatingLinkDialog() {
  action$('gating_list_available', { conversation_id: conversationId }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var services = data.available || [];
    if (!services.length) { addMsg('system', t('noPolicyGateServices')); return; }
    window._gatingLinkOptions = services;
    var options = services.map(function(s, idx) {
      var detail = (s.llm_service ? ' \u2192 ' + s.llm_service : '') + (s.scripts && s.scripts.length ? ' [' + s.scripts.length + ' scripts]' : '');
      return '<option value="' + idx + '">' + escapeHtml('[' + (s.scope || 'global') + '] ' + s.service_id + detail) + '</option>';
    }).join('');
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>' + escapeHtml(t('linkPolicyGate')) + '</h3>'
      + '<div style="margin:12px 0;"><select id="_gatingLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">' + options + '</select></div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doGatingLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div></div>';
    document.body.appendChild(overlay);
  });
}

function _doGatingLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_gatingLinkSelect');
  var svc = (window._gatingLinkOptions || [])[sel ? Number(sel.value) : -1];
  overlay.remove();
  if (!svc) return;
  action$('gating_link', { conversation_id: conversationId, scope: svc.scope, service_id: svc.service_id }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _unlinkGating() {
  action$('gating_unlink', { conversation_id: conversationId }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _showGatingDecisions() {
  action$('gating_decisions', { conversation_id: conversationId, limit: 50 }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    var rows = (data.decisions || []).slice().reverse().map(function(d) {
      var when = d.created_at ? new Date(d.created_at * 1000).toLocaleTimeString() : '';
      var verdict = d.outcome ? 'outcome: ' + d.outcome : (d.decision || '');
      return '<div style="border-bottom:1px solid var(--pf-border);padding:4px 0;font-size:11px;">'
        + '<code>' + escapeHtml(when) + '</code> <strong>' + escapeHtml(verdict) + '</strong> '
        + escapeHtml(d.tool || '') + (d.agent_name ? ' (' + escapeHtml(d.agent_name) + ')' : '')
        + (d.reason ? '<div style="color:var(--pf-muted);">' + escapeHtml(String(d.reason).slice(0, 300)) + '</div>' : '')
        + '</div>';
    }).join('') || '<div style="color:var(--pf-muted);">' + escapeHtml(t('noPolicyGate')) + '</div>';
    var overlay = _flowAuthoringDialog(t('policyGateDecisions'), '<div style="max-height:60vh;overflow:auto;">' + rows + '</div>'
      + '<div class="exec-btns"><button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('close')) + '</button></div>');
    return overlay;
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

