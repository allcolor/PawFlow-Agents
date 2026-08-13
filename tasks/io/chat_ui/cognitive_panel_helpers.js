// Shared conversation-agent and relay selectors for cognitive panels.
function _cognitiveLoadResources(onSuccess, onError) {
  action$('list_resources', {}).subscribe({
    next: function(data) { onSuccess(data || {}); },
    error: onError || function(error) { addMsg('error', error.message); },
  });
}

function _cognitiveAgentNames(data) {
  return (data.agents || []).map(function(agent) {
    return String((agent && agent.name) || agent || '');
  }).filter(Boolean);
}

function _cognitiveRelays(data, agentName) {
  const bindings = data.relay_bindings || {};
  const linked = bindings.linked || {};
  const details = bindings.details || {};
  const defaults = bindings.default || {};
  const seen = {};
  const relays = [];
  Object.keys(linked).forEach(function(scope) {
    (linked[scope] || []).forEach(function(relayId) {
      if (seen[relayId]) return;
      seen[relayId] = true;
      relays.push({
        id: String(relayId),
        connected: !!(details[relayId] || {}).connected,
      });
    });
  });
  const preferred = defaults[agentName] || defaults['*'] || '';
  return { relays: relays, preferred: preferred };
}

function _cognitiveRelayOptions(relays, selected) {
  return relays.map(function(relay) {
    const status = relay.connected ? '\u{1F7E2} ' : '\u{1F534} ';
    return '<option value="' + escapeHtml(relay.id) + '"'
      + (relay.id === selected ? ' selected' : '') + '>'
      + status + escapeHtml(relay.id) + '</option>';
  }).join('');
}
