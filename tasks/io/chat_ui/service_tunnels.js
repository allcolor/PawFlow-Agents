// Service Tunnel manager for relay-approved, loopback-only TCP access.
// Loaded after resources_render.js; shared helpers are page globals.

let _serviceTunnelState = {
  tunnels: [],
  relays: [],
  catalogRelay: '',
  services: [],
};

function _serviceTunnelAction(action, payload, onSuccess) {
  action$(action, Object.assign({
    conversation_id: conversationId,
  }, payload || {})).subscribe({
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

function _serviceTunnelClose() {
  const overlay = document.getElementById('serviceTunnelsOverlay');
  if (overlay) overlay.remove();
}

function _serviceTunnelEligibleRelays() {
  return (_serviceTunnelState.relays || []).filter(function(relay) {
    return !!relay.connected && !!relay.allow_service_tunnels;
  });
}

function _serviceTunnelRelayOptions(selected) {
  return _serviceTunnelEligibleRelays().map(function(relay) {
    const id = String(relay.relay_id || '');
    return '<option value="' + _pfpAttr(id) + '"'
      + (id === selected ? ' selected' : '') + '>' + escapeHtml(id) + '</option>';
  }).join('');
}

function _serviceTunnelServiceOptions(selected) {
  return (_serviceTunnelState.services || []).map(function(service) {
    const id = String(service.service_id || '');
    const label = String(service.name || id) + ' — '
      + String(service.target_host || '') + ':' + String(service.target_port || '');
    return '<option value="' + _pfpAttr(id) + '"'
      + (id === selected ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
  }).join('');
}

function _serviceTunnelRows() {
  const tunnels = _serviceTunnelState.tunnels || [];
  if (!tunnels.length) {
    return '<div style="color:var(--pf-muted);font-size:12px;">'
      + escapeHtml(t('serviceTunnelNoTunnels')) + '</div>';
  }
  return tunnels.map(function(tunnel) {
    const status = String(tunnel.status || 'pending');
    const color = status === 'connected' ? 'var(--pf-success)'
      : status === 'error' ? 'var(--pf-danger)' : 'var(--pf-muted)';
    const endpoint = String(tunnel.bind_host || '127.0.0.1') + ':'
      + String(tunnel.bind_port || '');
    const target = String(tunnel.service_relay || '') + ' / '
      + String(tunnel.service_name || tunnel.service_id || '');
    const idArg = _pfpJsArg(tunnel.tunnel_id || '');
    const primaryAction = status === 'connected'
      ? '<button type="button" onclick="_serviceTunnelStop(' + idArg + ')">'
        + escapeHtml(t('serviceTunnelStop')) + '</button>'
      : '<button type="button" onclick="_serviceTunnelStart(' + idArg + ')">'
        + escapeHtml(t('serviceTunnelStart')) + '</button>';
    return '<div style="border:1px solid var(--pf-border);border-radius:6px;padding:9px;margin:7px 0;">'
      + '<div style="display:flex;align-items:center;gap:7px;">'
      + '<span style="color:' + color + ';">●</span>'
      + '<strong style="flex:1;">' + escapeHtml(tunnel.name || tunnel.display_name || tunnel.tunnel_id) + '</strong>'
      + '<span style="font-size:10px;color:' + color + ';">' + escapeHtml(status) + '</span>'
      + primaryAction
      + '<button type="button" onclick="_serviceTunnelStatus(' + idArg + ')">'
      + escapeHtml(t('serviceTunnelRefresh')) + '</button>'
      + '<button type="button" style="color:var(--pf-danger);" onclick="_serviceTunnelDelete(' + idArg + ')">'
      + escapeHtml(t('serviceTunnelDelete')) + '</button></div>'
      + '<div style="font-size:11px;color:var(--pf-muted);margin-top:5px;">'
      + '<code>' + escapeHtml(endpoint) + '</code> → ' + escapeHtml(target) + '</div>'
      + (tunnel.error ? '<div style="font-size:11px;color:var(--pf-danger);margin-top:4px;">'
        + escapeHtml(tunnel.error) + '</div>' : '') + '</div>';
  }).join('');
}

function _serviceTunnelCatalogRows() {
  const services = _serviceTunnelState.services || [];
  if (!services.length) {
    return '<div style="color:var(--pf-muted);font-size:11px;">'
      + escapeHtml(t('serviceTunnelNoServices')) + '</div>';
  }
  return services.map(function(service) {
    return '<div style="display:flex;align-items:center;gap:7px;margin:5px 0;">'
      + '<strong style="flex:1;">' + escapeHtml(service.name || service.service_id) + '</strong>'
      + '<code style="font-size:10px;color:var(--pf-muted);">'
      + escapeHtml(String(service.target_host || '') + ':' + String(service.target_port || '')) + '</code>'
      + '<button type="button" style="color:var(--pf-danger);" onclick="_serviceTunnelCatalogDelete('
      + _pfpJsArg(service.service_id || '') + ')">' + escapeHtml(t('serviceTunnelDelete'))
      + '</button></div>';
  }).join('');
}

function _serviceTunnelRender() {
  let overlay = document.getElementById('serviceTunnelsOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'serviceTunnelsOverlay';
    overlay.className = 'exec-overlay';
    document.body.appendChild(overlay);
  }
  const relays = _serviceTunnelEligibleRelays();
  if (!_serviceTunnelState.catalogRelay && relays.length) {
    _serviceTunnelState.catalogRelay = String(relays[0].relay_id || '');
  }
  const accessSelected = relays.length > 1 ? String(relays[0].relay_id || '') : '';
  const serviceSelected = _serviceTunnelState.catalogRelay;
  const accessOptions = _serviceTunnelRelayOptions(accessSelected);
  const serviceRelayOptions = _serviceTunnelRelayOptions(serviceSelected);
  const approvedServiceOptions = _serviceTunnelServiceOptions('');
  const unavailable = !relays.length
    ? '<div style="color:var(--pf-danger);font-size:12px;margin-bottom:10px;">'
      + escapeHtml(t('serviceTunnelNoEligibleRelays')) + '</div>' : '';

  overlay.innerHTML = '<div class="exec-dialog" style="width:min(860px,94vw);max-height:90vh;overflow:auto;">'
    + '<h3>' + escapeHtml(t('serviceTunnels')) + '</h3>'
    + '<div style="font-size:12px;color:var(--pf-muted);margin-bottom:12px;">'
    + escapeHtml(t('serviceTunnelsDescription')) + '</div>' + unavailable
    + '<h4>' + escapeHtml(t('serviceTunnels')) + '</h4>' + _serviceTunnelRows()
    + '<hr style="border:0;border-top:1px solid var(--pf-border);margin:16px 0;">'
    + '<h4>' + escapeHtml(t('serviceTunnelCreate')) + '</h4>'
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
    + '<label>' + escapeHtml(t('serviceTunnelName'))
    + '<input id="serviceTunnelName" style="display:block;width:100%;"></label>'
    + '<label>' + escapeHtml(t('serviceTunnelLocalPort'))
    + '<input id="serviceTunnelPort" type="number" min="1" max="65535" value="22022" style="display:block;width:100%;"></label>'
    + '<label>' + escapeHtml(t('serviceTunnelAccessRelay'))
    + '<select id="serviceTunnelAccessRelay" style="display:block;width:100%;">' + accessOptions + '</select></label>'
    + '<label>' + escapeHtml(t('serviceTunnelServiceRelay'))
    + '<select id="serviceTunnelServiceRelay" onchange="_serviceTunnelCatalogChanged(this.value)" style="display:block;width:100%;">'
    + serviceRelayOptions + '</select></label>'
    + '<label style="grid-column:1/3;">' + escapeHtml(t('serviceTunnelService'))
    + '<select id="serviceTunnelService" style="display:block;width:100%;">' + approvedServiceOptions + '</select></label>'
    + '<label style="grid-column:1/3;display:flex;gap:6px;align-items:center;"><input id="serviceTunnelPersistent" type="checkbox" checked> '
    + escapeHtml(t('serviceTunnelPersistent')) + '</label></div>'
    + '<div style="font-size:11px;color:var(--pf-muted);margin:7px 0;">'
    + escapeHtml(t('serviceTunnelLoopbackHelp')) + ' <code>127.0.0.1</code></div>'
    + '<button type="button" onclick="_serviceTunnelCreate()">' + escapeHtml(t('serviceTunnelCreate')) + '</button>'
    + '<hr style="border:0;border-top:1px solid var(--pf-border);margin:16px 0;">'
    + '<h4>' + escapeHtml(t('serviceTunnelApprovedServices')) + '</h4>'
    + '<label>' + escapeHtml(t('serviceTunnelServiceRelay'))
    + '<select id="serviceTunnelCatalogRelay" onchange="_serviceTunnelCatalogChanged(this.value)" style="display:block;width:100%;margin:4px 0 8px;">'
    + serviceRelayOptions + '</select></label>'
    + _serviceTunnelCatalogRows()
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:9px;">'
    + '<input id="serviceTunnelCatalogId" placeholder="service-id">'
    + '<input id="serviceTunnelCatalogName" placeholder="' + _pfpAttr(t('serviceTunnelName')) + '">'
    + '<input id="serviceTunnelTargetHost" value="127.0.0.1" placeholder="' + _pfpAttr(t('serviceTunnelTargetHost')) + '">'
    + '<input id="serviceTunnelTargetPort" type="number" min="1" max="65535" value="22" placeholder="' + _pfpAttr(t('serviceTunnelTargetPort')) + '"></div>'
    + '<button type="button" style="margin-top:7px;" onclick="_serviceTunnelCatalogSave()">'
    + escapeHtml(t('serviceTunnelCatalogAdd')) + '</button>'
    + '<div class="exec-btns" style="margin-top:14px;"><button class="exec-deny" type="button" onclick="_serviceTunnelClose()">'
    + escapeHtml(t('close')) + '</button></div></div>';
}

function _serviceTunnelLoadCatalog(relayId, done) {
  _serviceTunnelState.catalogRelay = String(relayId || '');
  if (!_serviceTunnelState.catalogRelay) {
    _serviceTunnelState.services = [];
    if (done) done();
    return;
  }
  _serviceTunnelAction('service_tunnel_catalog', {
    relay_id: _serviceTunnelState.catalogRelay,
  }, function(data) {
    _serviceTunnelState.services = data.services || [];
    if (done) done();
  });
}

function showServiceTunnelsDialog() {
  if (!conversationId) {
    addMsg('error', t('serviceTunnelSelectConversation'));
    return;
  }
  _serviceTunnelAction('relay_list_available', {}, function(relayData) {
    _serviceTunnelState.relays = relayData.relays || [];
    _serviceTunnelAction('service_tunnels_list', {}, function(tunnelData) {
      _serviceTunnelState.tunnels = tunnelData.tunnels || [];
      const eligible = _serviceTunnelEligibleRelays();
      const relayId = _serviceTunnelState.catalogRelay
        || (eligible[0] && eligible[0].relay_id) || '';
      _serviceTunnelLoadCatalog(relayId, _serviceTunnelRender);
    });
  });
}

function _serviceTunnelCatalogChanged(relayId) {
  _serviceTunnelLoadCatalog(relayId, _serviceTunnelRender);
}

function _serviceTunnelRefresh() {
  _serviceTunnelAction('service_tunnels_list', {}, function(data) {
    _serviceTunnelState.tunnels = data.tunnels || [];
    _serviceTunnelRender();
  });
}

function _serviceTunnelCreate() {
  _serviceTunnelAction('service_tunnel_create', {
    name: document.getElementById('serviceTunnelName').value,
    access_relay: document.getElementById('serviceTunnelAccessRelay').value,
    service_relay: document.getElementById('serviceTunnelServiceRelay').value,
    service_id: document.getElementById('serviceTunnelService').value,
    bind_host: '127.0.0.1',
    bind_port: Number(document.getElementById('serviceTunnelPort').value),
    persistent: document.getElementById('serviceTunnelPersistent').checked,
  }, _serviceTunnelRefresh);
}

function _serviceTunnelLifecycle(action, tunnelId) {
  _serviceTunnelAction(action, { tunnel_id: tunnelId }, _serviceTunnelRefresh);
}
function _serviceTunnelStart(tunnelId) { _serviceTunnelLifecycle('service_tunnel_start', tunnelId); }
function _serviceTunnelStop(tunnelId) { _serviceTunnelLifecycle('service_tunnel_stop', tunnelId); }
function _serviceTunnelStatus(tunnelId) { _serviceTunnelLifecycle('service_tunnel_status', tunnelId); }
function _serviceTunnelDelete(tunnelId) {
  if (!confirm(t('serviceTunnelConfirmDelete'))) return;
  _serviceTunnelLifecycle('service_tunnel_delete', tunnelId);
}

function _serviceTunnelCatalogSave() {
  _serviceTunnelAction('service_tunnel_catalog_save', {
    relay_id: _serviceTunnelState.catalogRelay,
    service: {
      service_id: document.getElementById('serviceTunnelCatalogId').value,
      name: document.getElementById('serviceTunnelCatalogName').value,
      protocol: 'tcp',
      target_host: document.getElementById('serviceTunnelTargetHost').value,
      target_port: Number(document.getElementById('serviceTunnelTargetPort').value),
    },
  }, function() {
    _serviceTunnelLoadCatalog(_serviceTunnelState.catalogRelay, _serviceTunnelRender);
  });
}

function _serviceTunnelCatalogDelete(serviceId) {
  _serviceTunnelAction('service_tunnel_catalog_delete', {
    relay_id: _serviceTunnelState.catalogRelay,
    service_id: serviceId,
  }, function() {
    _serviceTunnelLoadCatalog(_serviceTunnelState.catalogRelay, _serviceTunnelRender);
  });
}
