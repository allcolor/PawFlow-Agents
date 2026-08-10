// Published-conversation MCP server configuration.
// Loaded after resources_render.js; all functions are page globals.

let _publishedMcpState = null;

function _publishedMcpEndpoint(serverId) {
  return new URL('/mcp/' + encodeURIComponent(serverId), window.location.origin).href;
}

function _publishedMcpClose() {
  const overlay = document.getElementById('publishedMcpOverlay');
  if (overlay) overlay.remove();
  _publishedMcpState = null;
}

function _publishedMcpAction(action, payload, onSuccess) {
  action$(action, Object.assign({ conversation_id: conversationId }, payload || {})).subscribe({
    next: function(data) {
      if (data && data.error) {
        addMsg('error', data.error);
        return;
      }
      if (onSuccess) onSuccess(data || {});
    },
    error: function(error) {
      addMsg('error', String(error && error.message || error));
    },
  });
}

function _publishedMcpAgents() {
  return ((_lastResourcesData && _lastResourcesData.agents) || [])
    .map(function(agent) { return String(agent.name || ''); })
    .filter(Boolean);
}

function _publishedMcpCliConfig(server) {
  if (!server) return '';
  return JSON.stringify({
    mcpServers: {
      pawflow: {
        command: 'pawflow-mcp',
        args: ['--url', _publishedMcpEndpoint(server.server_id)],
        env: {
          PAWFLOW_MCP_API_KEY: '<API key created below>',
          PAWFLOW_GATEWAY_KEY: '<Private Gateway key>',
        },
      },
    },
  }, null, 2);
}

function _publishedMcpCopy(elementId) {
  const element = document.getElementById(elementId);
  const value = element ? (element.value || element.textContent || '') : '';
  if (!value) return;
  navigator.clipboard.writeText(value).then(function() {
    addMsg('system', t('mcpPublishCopied'));
  }).catch(function(error) {
    addMsg('error', String(error && error.message || error));
  });
}

function _publishedMcpRender(state) {
  _publishedMcpState = state || {};
  let overlay = document.getElementById('publishedMcpOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'publishedMcpOverlay';
    overlay.className = 'exec-overlay';
    document.body.appendChild(overlay);
  }
  const server = _publishedMcpState.server || null;
  const agents = _publishedMcpAgents();
  const selected = server ? server.agent_name
    : ((typeof selectedAgent !== 'undefined' && selectedAgent) || agents[0] || '');
  const keys = server && Array.isArray(server.keys) ? server.keys : [];
  const endpoint = server ? _publishedMcpEndpoint(server.server_id) : '';
  const imageOutput = server ? String(server.image_output || 'native') : 'native';
  const relay = server && server.client_active
    ? escapeHtml(t('mcpPublishClientActive', { client: server.active_client_name || 'CLI' }))
    : escapeHtml(t('mcpPublishNoClient'));
  const disconnectClient = server && server.active_client_id
    ? '<button type="button" onclick="_publishedMcpDisconnectClient()">'
      + escapeHtml(t('mcpPublishDisconnectClient')) + '</button>'
    : '';
  const agentOptions = agents.map(function(name) {
    return '<option value="' + _pfpAttr(name) + '"' + (name === selected ? ' selected' : '') + '>'
      + escapeHtml(name) + '</option>';
  }).join('');
  const keyRows = keys.length ? keys.map(function(key) {
    const label = escapeHtml(key.label || key.prefix || key.key_id);
    const stateLabel = key.revoked ? t('mcpPublishRevoked') : (key.prefix + '...');
    const action = key.revoked ? '' : '<button type="button" onclick="_publishedMcpRevokeKey('
      + _pfpJsArg(key.key_id) + ')" style="color:var(--pf-danger);">' + escapeHtml(t('mcpPublishRevoke')) + '</button>';
    return '<div style="display:flex;align-items:center;gap:8px;margin:5px 0;">'
      + '<span style="flex:1;color:var(--pf-text);">' + label + '</span>'
      + '<code style="color:var(--pf-muted);">' + escapeHtml(stateLabel) + '</code>' + action + '</div>';
  }).join('') : '<div style="color:var(--pf-muted);">' + escapeHtml(t('mcpPublishNoKeys')) + '</div>';

  overlay.innerHTML = '<div class="exec-dialog" style="width:min(720px,92vw);max-height:88vh;overflow:auto;">'
    + '<h3>' + escapeHtml(t('mcpPublishTitle')) + '</h3>'
    + '<div style="color:var(--pf-muted);font-size:12px;margin-bottom:12px;">' + escapeHtml(t('mcpPublishDescription')) + '</div>'
    + '<label style="display:block;margin-bottom:10px;">' + escapeHtml(t('agent'))
    + '<select id="publishedMcpAgent" style="display:block;width:100%;margin-top:4px;">' + agentOptions + '</select></label>'
    + '<label style="display:flex;align-items:center;gap:7px;margin-bottom:12px;"><input id="publishedMcpEnabled" type="checkbox"'
    + ((!server || server.enabled) ? ' checked' : '') + '> ' + escapeHtml(t('mcpPublishEnabled')) + '</label>'
    + '<label style="display:block;margin-bottom:12px;">' + escapeHtml(t('mcpPublishImageOutput'))
    + '<select id="publishedMcpImageOutput" style="display:block;width:100%;margin-top:4px;">'
    + '<option value="native"' + (imageOutput === 'native' ? ' selected' : '') + '>'
    + escapeHtml(t('mcpPublishImageNative')) + '</option>'
    + '<option value="describe"' + (imageOutput === 'describe' ? ' selected' : '') + '>'
    + escapeHtml(t('mcpPublishImageDescribe')) + '</option></select></label>'
    + '<div style="display:flex;gap:8px;margin-bottom:14px;"><button type="button" onclick="_publishedMcpSave()">'
    + escapeHtml(server ? t('contextSave') : t('mcpPublishCreate')) + '</button>'
    + (server ? '<button type="button" onclick="_publishedMcpDelete()" style="color:var(--pf-danger);">'
      + escapeHtml(t('mcpPublishDelete')) + '</button>' : '') + '</div>'
    + (server ? '<div style="border-top:1px solid var(--pf-border);padding-top:12px;">'
      + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
      + '<span style="font-size:12px;color:var(--pf-muted);flex:1;">' + relay + '</span>'
      + disconnectClient + '</div>'
      + '<label style="display:block;margin-bottom:10px;">' + escapeHtml(t('mcpPublishEndpoint'))
      + '<div style="display:flex;gap:5px;"><input id="publishedMcpEndpoint" readonly value="' + _pfpAttr(endpoint)
      + '" style="flex:1;"><button type="button" onclick="_publishedMcpCopy(\'publishedMcpEndpoint\')">'
      + escapeHtml(t('copy')) + '</button></div></label>'
      + '<div style="font-weight:600;margin:10px 0 5px;">' + escapeHtml(t('mcpPublishKeys')) + '</div>'
      + keyRows
      + '<div style="display:flex;gap:5px;margin:8px 0 12px;"><input id="publishedMcpKeyLabel" placeholder="'
      + _pfpAttr(t('mcpPublishKeyLabel')) + '" style="flex:1;"><button type="button" onclick="_publishedMcpCreateKey()">'
      + escapeHtml(t('mcpPublishCreateKey')) + '</button></div>'
      + '<div id="publishedMcpNewKey"></div>'
      + '<div style="font-weight:600;margin:12px 0 5px;">' + escapeHtml(t('mcpPublishCliConfig')) + '</div>'
      + '<pre id="publishedMcpConfig" style="white-space:pre-wrap;user-select:text;">'
      + escapeHtml(_publishedMcpCliConfig(server)) + '</pre>'
      + '<button type="button" onclick="_publishedMcpCopy(\'publishedMcpConfig\')">' + escapeHtml(t('copy')) + '</button>'
      + '</div>' : '')
    + '<div class="exec-btns" style="margin-top:14px;"><button class="exec-deny" type="button" onclick="_publishedMcpClose()">'
    + escapeHtml(t('contextClose')) + '</button></div></div>';
}

function showPublishedMcpDialog() {
  if (!conversationId) {
    addMsg('error', t('mcpPublishSelectConversation'));
    return;
  }
  _publishedMcpAction('mcp_server_get', {}, function(data) {
    _publishedMcpRender({ server: data.server || null });
  });
}

function _publishedMcpSave() {
  const agent = document.getElementById('publishedMcpAgent')?.value || '';
  const enabled = !!document.getElementById('publishedMcpEnabled')?.checked;
  const imageOutput = document.getElementById('publishedMcpImageOutput')?.value || 'native';
  if (!agent) {
    addMsg('error', t('mcpPublishSelectAgent'));
    return;
  }
  _publishedMcpAction('mcp_server_configure', {
    agent_name: agent,
    label: agent,
    enabled: enabled,
    image_output: imageOutput,
  }, function(data) {
    _publishedMcpRender({ server: data.server || null });
    loadResources();
  });
}

function _publishedMcpCreateKey() {
  const label = document.getElementById('publishedMcpKeyLabel')?.value || '';
  _publishedMcpAction('mcp_server_create_key', { label: label }, function(data) {
    const target = document.getElementById('publishedMcpNewKey');
    if (target) {
      target.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;margin-bottom:4px;">'
        + escapeHtml(t('mcpPublishKeyOnce')) + '</div><div style="display:flex;gap:5px;">'
        + '<input id="publishedMcpRawKey" readonly value="' + _pfpAttr(data.api_key || '') + '" style="flex:1;">'
        + '<button type="button" onclick="_publishedMcpCopy(\'publishedMcpRawKey\')">' + escapeHtml(t('copy')) + '</button></div>';
    }
    _publishedMcpAction('mcp_server_get', {}, function(refreshed) {
      const raw = data.api_key || '';
      _publishedMcpRender({ server: refreshed.server || null });
      const freshTarget = document.getElementById('publishedMcpNewKey');
      if (freshTarget) {
        freshTarget.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;margin-bottom:4px;">'
          + escapeHtml(t('mcpPublishKeyOnce')) + '</div><div style="display:flex;gap:5px;">'
          + '<input id="publishedMcpRawKey" readonly value="' + _pfpAttr(raw) + '" style="flex:1;">'
          + '<button type="button" onclick="_publishedMcpCopy(\'publishedMcpRawKey\')">' + escapeHtml(t('copy')) + '</button></div>';
      }
    });
  });
}

function _publishedMcpRevokeKey(keyId) {
  if (!confirm(t('mcpPublishConfirmRevoke'))) return;
  _publishedMcpAction('mcp_server_revoke_key', { key_id: keyId }, function() {
    showPublishedMcpDialog();
  });
}

function _publishedMcpDisconnectClient() {
  if (!confirm(t('mcpPublishConfirmDisconnectClient'))) return;
  _publishedMcpAction('mcp_server_disconnect_client', {}, function() {
    showPublishedMcpDialog();
    loadResources();
  });
}

function _publishedMcpDelete() {
  if (!confirm(t('mcpPublishConfirmDelete'))) return;
  _publishedMcpAction('mcp_server_delete', {}, function() {
    _publishedMcpClose();
    loadResources();
  });
}
