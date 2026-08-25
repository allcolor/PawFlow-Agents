// Generic semantic fallback for durable pawflow.ui-surface.v1 instances.
var _vscodeUiSurfaces = Object.create(null);
var VSCODE_UI_SURFACE_CAPABILITIES = Object.freeze([
  'client.vscode', 'semantic.basic', 'semantic.form',
]);

function _vscodeSurfaceHas(required) {
  return (required || []).every(function(capability) {
    return VSCODE_UI_SURFACE_CAPABILITIES.indexOf(capability) >= 0;
  });
}

function _vscodeSurfaceValue(input, type) {
  if (type === 'boolean') return input.checked;
  if (type === 'number' || type === 'integer') {
    return input.value === '' ? null : Number(input.value);
  }
  return input.value;
}

function _vscodeSurfaceAction(surface, action, inputs) {
  if (!_vscodeSurfaceHas(action.requires)) return;
  if (action.confirm && !window.confirm(action.confirm)) return;
  var args = Object.assign({}, (action.dispatch || {}).arguments || {});
  Object.keys(inputs).forEach(function(id) {
    var row = inputs[id];
    var value = _vscodeSurfaceValue(row.input, row.type);
    if (row.input.required && (value === '' || value == null)) {
      row.input.focus();
      throw new Error((row.input.getAttribute('aria-label') || id) + ' is required');
    }
    args[id] = value;
  });
  var dispatch = action.dispatch || {};
  vscode.postMessage({
    type: 'uiSurfaceAction', action: dispatch.action || '',
    arguments: args, extension: dispatch.extension || '',
  });
}

function _vscodeSurfaceInput(field) {
  var input;
  if (field.type === 'boolean') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = field.value === true;
  } else {
    input = field.type === 'string'
      ? document.createElement('textarea') : document.createElement('input');
    if (field.type === 'number' || field.type === 'integer') input.type = 'number';
    if (field.value != null) input.value = typeof field.value === 'string'
      ? field.value : JSON.stringify(field.value);
  }
  input.required = field.required === true;
  input.setAttribute('aria-label', field.label || field.id);
  if (field.placeholder) input.placeholder = field.placeholder;
  return input;
}

function _renderVscodeUiSurface(surface) {
  var existing = document.querySelector(
    '[data-ui-surface-id="' + CSS.escape(surface.surface_id) + '"]');
  if (surface.status === 'resolved' || surface.status === 'cancelled') {
    if (existing) existing.remove();
    return;
  }
  var card = existing || document.createElement('article');
  card.className = 'ui-surface-card msg';
  card.dataset.uiSurfaceId = surface.surface_id;
  card.replaceChildren();
  var semantic = surface.semantic || {};
  var title = document.createElement('strong');
  title.textContent = semantic.title || 'Interactive surface';
  card.appendChild(title);
  var status = document.createElement('span');
  status.className = 'ui-surface-status';
  status.textContent = String(surface.status || '').replaceAll('_', ' ');
  card.appendChild(status);
  if (semantic.summary || semantic.body) {
    var body = document.createElement('div');
    body.className = 'ui-surface-body';
    body.textContent = [semantic.summary, semantic.body].filter(Boolean).join('\n');
    card.appendChild(body);
  }
  var inputs = Object.create(null);
  (semantic.fields || []).forEach(function(field) {
    var label = document.createElement('label');
    label.className = 'ui-surface-field';
    var caption = document.createElement('span');
    caption.textContent = field.label || field.id;
    var input = _vscodeSurfaceInput(field);
    inputs[field.id] = {input: input, type: field.type};
    label.appendChild(caption);
    label.appendChild(input);
    card.appendChild(label);
  });
  var actions = document.createElement('div');
  actions.className = 'ui-surface-actions';
  (semantic.actions || []).forEach(function(action) {
    var button = document.createElement('button');
    button.textContent = action.label || action.id;
    button.disabled = !_vscodeSurfaceHas(action.requires);
    button.onclick = function() {
      try { _vscodeSurfaceAction(surface, action, inputs); }
      catch (error) { addMsg('error', error.message || String(error)); }
    };
    actions.appendChild(button);
    if (button.disabled && action.handoff && action.handoff.message) {
      var handoff = document.createElement('div');
      handoff.className = 'ui-surface-handoff';
      handoff.textContent = action.handoff.message;
      actions.appendChild(handoff);
    }
  });
  card.appendChild(actions);
  if (!existing) messagesEl.appendChild(card);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function vscodeUiSurfaceEvent(data) {
  var surface = data && data.surface;
  if (!surface || surface.format !== 'pawflow.ui-surface.v1') return;
  if (currentHistoryConvId && surface.conversation_id !== currentHistoryConvId) return;
  var current = _vscodeUiSurfaces[surface.surface_id];
  if (current && Number(current.revision) > Number(surface.revision)) return;
  _vscodeUiSurfaces[surface.surface_id] = surface;
  _renderVscodeUiSurface(surface);
}
