// A2A publications and named local/remote targets.
// Loaded after resources_render.js; all functions are page globals.

let _a2aState = null;

function _a2aAction(action, payload, onSuccess) {
  action$(action, Object.assign({ conversation_id: conversationId }, payload || {})).subscribe({
    next: function(data) {
      if (data && data.error) { addMsg('error', data.error); return; }
      if (onSuccess) onSuccess(data || {});
    },
    error: function(error) { addMsg('error', String(error && error.message || error)); },
  });
}

function _a2aClose() {
  const overlay = document.getElementById('a2aConfigOverlay');
  if (overlay) overlay.remove();
  _a2aState = null;
}

function _a2aEndpoint(publicationId) {
  return new URL('/a2a/' + encodeURIComponent(publicationId), window.location.origin).href;
}

// The same publication (id, Bearer keys, enable flag) is also served over
// AG-UI; the panel shows both URLs so the AG-UI export is discoverable.
function _aguiEndpoint(publicationId) {
  return new URL('/agui/' + encodeURIComponent(publicationId), window.location.origin).href;
}

function _a2aCopyValue(value) {
  if (!value) return;
  navigator.clipboard.writeText(value).then(function() {
    addMsg('system', t('a2aCopied'));
  }).catch(function(error) { addMsg('error', String(error && error.message || error)); });
}

function _a2aAgents() {
  return ((_lastResourcesData && _lastResourcesData.agents) || [])
    .map(function(agent) { return String(agent.name || ''); }).filter(Boolean);
}

function _a2aPublicationRows(publications) {
  if (!publications.length) return '<div style="color:var(--pf-muted);">' + escapeHtml(t('a2aNoPublications')) + '</div>';
  return publications.map(function(pub) {
    const endpoint = _a2aEndpoint(pub.publication_id);
    const aguiEndpoint = _aguiEndpoint(pub.publication_id);
    const cardUrl = endpoint + '/agent-card.json';
    const keys = Array.isArray(pub.keys) ? pub.keys : [];
    const keyRows = keys.length ? keys.map(function(key) {
      return '<div style="display:flex;gap:6px;align-items:center;margin-top:4px;">'
        + '<span style="flex:1;font-size:11px;">' + escapeHtml(key.label || key.prefix) + '</span>'
        + '<code style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(key.revoked ? t('a2aRevoked') : key.prefix + '...') + '</code>'
        + (key.revoked ? '' : '<button type="button" onclick="_a2aRevokeKey(' + _pfpJsArg(pub.publication_id) + ',' + _pfpJsArg(key.key_id) + ')">' + escapeHtml(t('a2aRevoke')) + '</button>')
        + '</div>';
    }).join('') : '<div style="font-size:11px;color:var(--pf-muted);margin-top:4px;">' + escapeHtml(t('a2aNoKeys')) + '</div>';
    return '<div style="border:1px solid var(--pf-border);border-radius:6px;padding:9px;margin:7px 0;">'
      + '<div style="display:flex;align-items:center;gap:7px;"><strong style="flex:1;">' + escapeHtml(pub.label || pub.agent_name) + '</strong>'
      + '<span style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(pub.context_policy) + '</span>'
      + (pub.managed_mode ? '<span style="font-size:10px;color:var(--pf-accent);">' + escapeHtml(t('a2aManagedBadge')) + '</span>' : '')
      + '<button type="button" onclick="_a2aEditPublication(' + _pfpJsArg(pub.publication_id) + ')">' + escapeHtml(t('a2aEdit')) + '</button>'
      + '<button type="button" style="color:var(--pf-danger);" onclick="_a2aDeletePublication(' + _pfpJsArg(pub.publication_id) + ')">' + escapeHtml(t('a2aDelete')) + '</button></div>'
      + '<div style="display:flex;gap:5px;margin-top:6px;"><input readonly value="' + _pfpAttr(cardUrl) + '" style="flex:1;font-size:11px;">'
      + '<button type="button" onclick="_a2aCopyValue(' + _pfpJsArg(cardUrl) + ')">' + escapeHtml(t('a2aCopyCard')) + '</button>'
      + '<button type="button" onclick="_a2aCopyValue(' + _pfpJsArg(endpoint) + ')">' + escapeHtml(t('a2aCopyEndpoint')) + '</button></div>'
      + '<div style="display:flex;gap:5px;margin-top:4px;align-items:center;"><span style="font-size:10px;color:var(--pf-muted);white-space:nowrap;">' + escapeHtml(t('a2aAguiEndpoint')) + '</span>'
      + '<input readonly value="' + _pfpAttr(aguiEndpoint) + '" style="flex:1;font-size:11px;">'
      + '<button type="button" onclick="_a2aCopyValue(' + _pfpJsArg(aguiEndpoint) + ')">' + escapeHtml(t('a2aCopyAguiEndpoint')) + '</button></div>'
      + keyRows
      + '<div style="display:flex;gap:5px;margin-top:6px;"><input id="a2aKeyLabel_' + _pfpAttr(pub.publication_id) + '" placeholder="' + _pfpAttr(t('a2aKeyLabel')) + '" style="flex:1;">'
      + '<button type="button" onclick="_a2aCreateKey(' + _pfpJsArg(pub.publication_id) + ')">' + escapeHtml(t('a2aCreateKey')) + '</button></div>'
      + '<div id="a2aNewKey_' + _pfpAttr(pub.publication_id) + '"></div></div>';
  }).join('');
}

function _a2aTargetRows(targets) {
  if (!targets.length) return '<div style="color:var(--pf-muted);">' + escapeHtml(t('a2aNoTargets')) + '</div>';
  return targets.map(function(target) {
    let detail = target.kind === 'local'
      ? target.target_agent + ' · ' + target.target_conversation_id.slice(0, 10)
      : target.agent_card_url;
    return '<div style="display:flex;align-items:center;gap:7px;margin:6px 0;padding:6px;border:1px solid var(--pf-border);border-radius:5px;">'
      + '<span style="font-weight:600;">' + escapeHtml(target.alias) + '</span>'
      + '<span style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(target.kind) + '</span>'
      + '<span style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;" title="' + _pfpAttr(detail) + '">' + escapeHtml(detail) + '</span>'
      + '<button type="button" style="color:var(--pf-danger);" onclick="_a2aDeleteTarget(' + _pfpJsArg(target.target_id) + ')">' + escapeHtml(t('a2aDelete')) + '</button></div>';
  }).join('');
}

function _a2aRender(state) {
  _a2aState = state || { publications: [], targets: [], local_choices: [] };
  let overlay = document.getElementById('a2aConfigOverlay');
  if (!overlay) {
    overlay = document.createElement('div'); overlay.id = 'a2aConfigOverlay';
    overlay.className = 'exec-overlay'; document.body.appendChild(overlay);
  }
  const agents = _a2aAgents();
  const edited = _a2aState.edit_publication || null;
  const selectedAgent = edited ? edited.agent_name : (agents[0] || '');
  const agentOptions = agents.map(function(name) {
    return '<option value="' + _pfpAttr(name) + '"' + (name === selectedAgent ? ' selected' : '') + '>' + escapeHtml(name) + '</option>';
  }).join('');
  const localOptions = [];
  (_a2aState.local_choices || []).forEach(function(conv) {
    (conv.agents || []).forEach(function(agent) {
      const index = localOptions.length;
      localOptions.push({ conversation_id: conv.conversation_id, agent: agent,
                          label: (conv.title || conv.conversation_id) + ' — ' + agent, index: index });
    });
  });
  _a2aState.flat_local_choices = localOptions;
  const localHtml = localOptions.map(function(item) {
    return '<option value="' + item.index + '">' + escapeHtml(item.label) + '</option>';
  }).join('');
  overlay.innerHTML = '<div class="exec-dialog" style="width:min(820px,94vw);max-height:90vh;overflow:auto;">'
    + '<h3>' + escapeHtml(t('a2aTitle')) + '</h3><div style="font-size:12px;color:var(--pf-muted);margin-bottom:6px;">' + escapeHtml(t('a2aDescription')) + '</div>'
    + '<div style="font-size:11px;color:var(--pf-muted);margin-bottom:12px;">' + escapeHtml(t('a2aAguiHint')) + '</div>'
    + '<h4>' + escapeHtml(t('a2aPublishAgent')) + '</h4>'
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
    + '<label>' + escapeHtml(t('agent')) + '<select id="a2aPubAgent"' + (edited ? ' disabled' : '') + ' style="display:block;width:100%;">' + agentOptions + '</select></label>'
    + '<label>' + escapeHtml(t('a2aLabel')) + '<input id="a2aPubLabel" value="' + _pfpAttr(edited ? edited.label : selectedAgent) + '" style="display:block;width:100%;"></label>'
    + '<label style="grid-column:1/3;">' + escapeHtml(t('a2aAgentDescription')) + '<input id="a2aPubDescription" value="' + _pfpAttr(edited ? edited.description : '') + '" style="display:block;width:100%;"></label>'
    + '<label>' + escapeHtml(t('a2aContextPolicy')) + '<select id="a2aPubPolicy" style="display:block;width:100%;"><option value="isolated"' + (!edited || edited.context_policy === 'isolated' ? ' selected' : '') + '>' + escapeHtml(t('a2aIsolated')) + '</option><option value="shared"' + (edited && edited.context_policy === 'shared' ? ' selected' : '') + '>' + escapeHtml(t('a2aShared')) + '</option></select></label>'
    + '<label style="display:flex;align-items:center;gap:6px;padding-top:18px;"><input id="a2aPubEnabled" type="checkbox"' + (!edited || edited.enabled ? ' checked' : '') + '> ' + escapeHtml(t('a2aEnabled')) + '</label></div>'
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;">'
    + '<label style="display:flex;align-items:center;gap:6px;" title="' + _pfpAttr(t('a2aManagedModeHelp')) + '"><input id="a2aPubManaged" type="checkbox"' + (edited && edited.managed_mode ? ' checked' : '') + '> ' + escapeHtml(t('a2aManagedMode')) + '</label>'
    + '<label>' + escapeHtml(t('a2aThreadTtl')) + '<input id="a2aPubThreadTtl" type="number" min="0" step="1" value="' + _pfpAttr(edited && edited.thread_ttl_seconds != null ? String(edited.thread_ttl_seconds) : '0') + '" style="display:block;width:100%;"></label></div>'
    + '<div style="margin:8px 0;"><button type="button" onclick="_a2aSavePublication()">' + escapeHtml(t('a2aSavePublication')) + '</button>'
    + (edited ? ' <button type="button" onclick="_a2aCancelEdit()">' + escapeHtml(t('a2aCancelEdit')) + '</button>' : '') + '</div>'
    + _a2aPublicationRows(_a2aState.publications || [])
    + '<hr style="border:0;border-top:1px solid var(--pf-border);margin:16px 0;"><h4>' + escapeHtml(t('a2aTargets')) + '</h4>'
    + '<div style="font-size:11px;color:var(--pf-muted);margin-bottom:8px;">' + escapeHtml(t('a2aTargetsHelp')) + '</div>'
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;"><label>' + escapeHtml(t('a2aAlias')) + '<input id="a2aTargetAlias" style="display:block;width:100%;"></label>'
    + '<label>' + escapeHtml(t('a2aTargetKind')) + '<select id="a2aTargetKind" onchange="_a2aTargetKindChanged()" style="display:block;width:100%;"><option value="local">' + escapeHtml(t('a2aLocal')) + '</option><option value="remote">' + escapeHtml(t('a2aRemote')) + '</option></select></label></div>'
    + '<div id="a2aLocalFields"><label>' + escapeHtml(t('a2aLocalAgent')) + '<select id="a2aLocalChoice" style="display:block;width:100%;">' + localHtml + '</select></label></div>'
    + '<div id="a2aRemoteFields" style="display:none;"><label>' + escapeHtml(t('a2aCardUrl')) + '<input id="a2aCardUrl" placeholder="https://agent.example/.well-known/agent-card.json" style="display:block;width:100%;"></label>'
    + '<label>' + escapeHtml(t('a2aSecretName')) + '<input id="a2aSecretName" placeholder="REMOTE_A2A_KEY" style="display:block;width:100%;"></label>'
    + '<label style="display:flex;gap:6px;align-items:center;margin-top:6px;"><input id="a2aAllowPrivate" type="checkbox"> ' + escapeHtml(t('a2aAllowPrivate')) + '</label></div>'
    + '<button type="button" style="margin:8px 0;" onclick="_a2aSaveTarget()">' + escapeHtml(t('a2aAddTarget')) + '</button>'
    + _a2aTargetRows(_a2aState.targets || [])
    + '<div class="exec-btns" style="margin-top:14px;"><button class="exec-deny" type="button" onclick="_a2aClose()">' + escapeHtml(t('close')) + '</button></div></div>';
}

function showA2AConfigDialog() {
  if (!conversationId) { addMsg('error', t('a2aSelectConversation')); return; }
  _a2aAction('a2a_get', {}, function(data) { _a2aRender(data); });
}

function _a2aRefresh(mutator) {
  _a2aAction('a2a_get', {}, function(data) { if (mutator) mutator(data); _a2aRender(data); });
}

function _a2aEditPublication(publicationId) {
  const pub = (_a2aState.publications || []).find(function(row) { return row.publication_id === publicationId; });
  if (!pub) return; _a2aState.edit_publication = pub; _a2aRender(_a2aState);
}

function _a2aCancelEdit() { delete _a2aState.edit_publication; _a2aRender(_a2aState); }

function _a2aSavePublication() {
  const edited = _a2aState.edit_publication || null;
  const ttlRaw = document.getElementById('a2aPubThreadTtl').value;
  _a2aAction('a2a_publication_configure', {
    publication_id: edited ? edited.publication_id : '',
    agent_name: document.getElementById('a2aPubAgent').value,
    label: document.getElementById('a2aPubLabel').value,
    description: document.getElementById('a2aPubDescription').value,
    context_policy: document.getElementById('a2aPubPolicy').value,
    enabled: document.getElementById('a2aPubEnabled').checked,
    managed_mode: document.getElementById('a2aPubManaged').checked,
    thread_ttl_seconds: ttlRaw === '' ? null : Number(ttlRaw),
  }, function() { _a2aRefresh(); });
}

function _a2aDeletePublication(publicationId) {
  if (!confirm(t('a2aConfirmDeletePublication'))) return;
  _a2aAction('a2a_publication_delete', { publication_id: publicationId }, function() { _a2aRefresh(); });
}

function _a2aCreateKey(publicationId) {
  const input = document.getElementById('a2aKeyLabel_' + publicationId);
  _a2aAction('a2a_publication_create_key', { publication_id: publicationId, label: input ? input.value : '' }, function(created) {
    _a2aAction('a2a_get', {}, function(data) {
      _a2aRender(data);
      const box = document.getElementById('a2aNewKey_' + publicationId);
      if (box) box.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;margin-top:6px;">' + escapeHtml(t('a2aKeyOnce')) + '</div><div style="display:flex;gap:5px;"><input readonly value="' + _pfpAttr(created.api_key || '') + '" style="flex:1;"><button type="button" onclick="_a2aCopyValue(' + _pfpJsArg(created.api_key || '') + ')">' + escapeHtml(t('copy')) + '</button></div>';
    });
  });
}

function _a2aRevokeKey(publicationId, keyId) {
  if (!confirm(t('a2aConfirmRevoke'))) return;
  _a2aAction('a2a_publication_revoke_key', { publication_id: publicationId, key_id: keyId }, function() { _a2aRefresh(); });
}

function _a2aTargetKindChanged() {
  const remote = document.getElementById('a2aTargetKind').value === 'remote';
  document.getElementById('a2aLocalFields').style.display = remote ? 'none' : '';
  document.getElementById('a2aRemoteFields').style.display = remote ? '' : 'none';
}

function _a2aSaveTarget() {
  const kind = document.getElementById('a2aTargetKind').value;
  const payload = { kind: kind, alias: document.getElementById('a2aTargetAlias').value };
  if (kind === 'local') {
    const choice = (_a2aState.flat_local_choices || [])[Number(document.getElementById('a2aLocalChoice').value)];
    if (!choice) { addMsg('error', t('a2aChooseLocal')); return; }
    payload.target_conversation_id = choice.conversation_id; payload.target_agent = choice.agent;
  } else {
    payload.agent_card_url = document.getElementById('a2aCardUrl').value;
    payload.auth_secret = document.getElementById('a2aSecretName').value;
    payload.allow_private = document.getElementById('a2aAllowPrivate').checked;
  }
  _a2aAction('a2a_target_save', payload, function() { _a2aRefresh(); });
}

function _a2aDeleteTarget(targetId) {
  if (!confirm(t('a2aConfirmDeleteTarget'))) return;
  _a2aAction('a2a_target_delete', { target_id: targetId }, function() { _a2aRefresh(); });
}
