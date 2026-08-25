// Generic renderer for durable pawflow.ui-surface.v1 instances.
const _uiSurfaces = new Map();
let _uiSurfaceConversation = '';

const UI_SURFACE_CAPABILITIES = Object.freeze([
  'client.web', 'semantic.basic', 'semantic.form', 'ui.component',
  'workflow.editor', 'workflow.mini-graph',
]);

function _uiSurfaceHasCapabilities(required) {
  return (required || []).every(item => UI_SURFACE_CAPABILITIES.includes(item));
}

function _uiSurfaceButton(action, inputs, surface) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = action.kind || '';
  button.textContent = action.label || action.id;
  const available = _uiSurfaceHasCapabilities(action.requires);
  button.disabled = !available;
  if (!available) {
    const missing = (action.requires || []).filter(
      item => !UI_SURFACE_CAPABILITIES.includes(item));
    button.title = (action.handoff && action.handoff.message)
      || ('Requires: ' + missing.join(', '));
  }
  button.onclick = () => _uiSurfaceDispatch(surface, action, inputs);
  return button;
}

function _uiSurfaceInput(field) {
  let input;
  const options = Array.isArray(field.options) ? field.options : [];
  if (options.length) {
    input = document.createElement('select');
    if (!field.required) {
      const empty = document.createElement('option');
      empty.value = '';
      empty.textContent = '';
      input.appendChild(empty);
    }
    options.forEach(option => {
      const row = document.createElement('option');
      const value = option && typeof option === 'object'
        ? option.value : option;
      row.value = String(value == null ? '' : value);
      row.textContent = String(
        option && typeof option === 'object' ? option.label : option);
      input.appendChild(row);
    });
  } else if (field.type === 'boolean') {
    input = document.createElement('input');
    input.type = 'checkbox';
  } else {
    input = field.type === 'string'
      ? document.createElement('textarea')
      : document.createElement('input');
    if (input.tagName === 'TEXTAREA') input.rows = 2;
    else input.type = field.type === 'number' || field.type === 'integer'
      ? 'number' : 'text';
  }
  input.className = 'ui-surface-input';
  input.dataset.fieldId = field.id;
  input.required = field.required === true;
  if (field.placeholder) input.placeholder = String(field.placeholder);
  if (field.value != null) {
    if (field.type === 'boolean') input.checked = field.value === true;
    else input.value = typeof field.value === 'string'
      ? field.value : JSON.stringify(field.value);
  }
  input.setAttribute('aria-label', field.label || field.id);
  return input;
}

function _uiSurfaceReadInputs(inputs) {
  const values = {};
  inputs.forEach((input, id) => {
    let value;
    if (input.type === 'checkbox') value = input.checked;
    else if (input.type === 'number') {
      value = input.value === '' ? null : Number(input.value);
    } else value = input.value;
    if (input.required && (value === '' || value == null)) {
      input.focus();
      throw new Error(input.getAttribute('aria-label') + ' is required');
    }
    values[id] = value;
  });
  return values;
}

function _uiSurfaceDispatch(surface, action, inputs) {
  if (!_uiSurfaceHasCapabilities(action.requires)) return;
  if (action.confirm && !window.confirm(action.confirm)) return;
  let values;
  try { values = _uiSurfaceReadInputs(inputs); }
  catch (error) {
    if (typeof addMsg === 'function') addMsg('error', error.message);
    return;
  }
  const dispatch = action.dispatch || {};
  const args = Object.assign({}, dispatch.arguments || {}, values);
  if (dispatch.action === 'open_client_uri') {
    if (args.uri === 'pawflow://workflow-editor'
        && typeof _openFlowEditorTab === 'function') {
      _openFlowEditorTab(args.draft_id || '', '', args.proposal_id || '');
    }
    return;
  }
  if (dispatch.extension) args._ext = dispatch.extension;
  if (typeof action$ !== 'function') return;
  action$(dispatch.action, args).subscribe({
    next: data => {
      if (data && data.surface) uiSurfaceUpsert(data.surface);
      if (data && data.error && typeof addMsg === 'function') {
        addMsg('error', data.message || data.error);
      }
    },
    error: error => {
      if (typeof addMsg === 'function') addMsg('error', error.message || String(error));
    },
  });
}

function _renderWorkflowMiniGraph(surface, card) {
  const presentation = surface.presentation || {};
  if (presentation.component !== 'pawflow.builtin:workflow-mini-graph'
      || !_uiSurfaceHasCapabilities(presentation.requires)) return;
  const props = presentation.props || {};
  const blocks = Array.isArray(props.blocks) ? props.blocks : [];
  if (!blocks.length) return;
  const relations = Array.isArray(props.relations) ? props.relations : [];
  const columns = Math.min(4, Math.max(1, blocks.length));
  const rows = Math.ceil(blocks.length / columns);
  const width = 360;
  const height = Math.max(76, rows * 62 + 14);
  const cellWidth = width / columns;
  const positions = Object.create(null);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Workflow preview');
  svg.classList.add('ui-surface-mini-graph');
  blocks.forEach((block, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    positions[String(block.id)] = {
      x: column * cellWidth + cellWidth / 2,
      y: row * 62 + 38,
    };
  });
  relations.forEach(relation => {
    const source = positions[String(relation.from)];
    const target = positions[String(relation.to)];
    if (!source || !target) return;
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', String(source.x));
    line.setAttribute('y1', String(source.y));
    line.setAttribute('x2', String(target.x));
    line.setAttribute('y2', String(target.y));
    line.classList.add('ui-surface-mini-edge');
    svg.appendChild(line);
  });
  blocks.forEach(block => {
    const point = positions[String(block.id)];
    const group = document.createElementNS(ns, 'g');
    const rect = document.createElementNS(ns, 'rect');
    rect.setAttribute('x', String(point.x - Math.min(42, cellWidth / 2 - 4)));
    rect.setAttribute('y', String(point.y - 16));
    rect.setAttribute('width', String(Math.min(84, cellWidth - 8)));
    rect.setAttribute('height', '32');
    rect.setAttribute('rx', '6');
    const label = document.createElementNS(ns, 'text');
    label.setAttribute('x', String(point.x));
    label.setAttribute('y', String(point.y + 3));
    label.textContent = String(block.label || block.id).slice(0, 18);
    const title = document.createElementNS(ns, 'title');
    title.textContent = String(block.label || block.id);
    group.append(rect, label, title);
    svg.appendChild(group);
  });
  card.appendChild(svg);
}

function _renderUiSurfaceSemantic(surface, card) {
  const semantic = surface.semantic || {};
  const header = document.createElement('div');
  header.className = 'ui-surface-head';
  const title = document.createElement('strong');
  title.textContent = semantic.title || 'Interactive surface';
  const status = document.createElement('span');
  status.className = 'ui-surface-status';
  status.textContent = String(surface.status || '').replaceAll('_', ' ');
  header.append(title, status);
  card.appendChild(header);
  if (semantic.summary) {
    const summary = document.createElement('div');
    summary.className = 'ui-surface-summary';
    summary.textContent = semantic.summary;
    card.appendChild(summary);
  }
  if (semantic.body) {
    const body = document.createElement('div');
    body.className = 'ui-surface-meta';
    body.textContent = semantic.body;
    card.appendChild(body);
  }
  _renderWorkflowMiniGraph(surface, card);

  const inputs = new Map();
  (semantic.fields || []).forEach(field => {
    const label = document.createElement('label');
    label.className = 'ui-surface-field';
    const caption = document.createElement('span');
    caption.textContent = field.label || field.id;
    const input = _uiSurfaceInput(field);
    inputs.set(field.id, input);
    label.append(caption, input);
    card.appendChild(label);
  });

  const actions = document.createElement('div');
  actions.className = 'ui-surface-actions';
  (semantic.actions || []).forEach(action => {
    actions.appendChild(_uiSurfaceButton(action, inputs, surface));
    if (!_uiSurfaceHasCapabilities(action.requires)
        && action.handoff && action.handoff.message) {
      const handoff = document.createElement('div');
      handoff.className = 'ui-surface-handoff';
      handoff.textContent = action.handoff.message;
      if (action.handoff.uri) {
        const link = document.createElement('a');
        link.href = action.handoff.uri;
        link.textContent = ' Open compatible client';
        handoff.appendChild(link);
      }
      actions.appendChild(handoff);
    }
  });
  card.appendChild(actions);
}

function _renderUiSurface(surface, stack) {
  const card = document.createElement('article');
  card.className = 'ui-surface-card ui-surface-' + surface.status;
  card.dataset.surfaceId = surface.surface_id;
  card.dataset.surfaceRevision = String(surface.revision || '');
  const presentation = surface.presentation || {};
  const ext = window._pawflowExtRuntime;
  let rendered = false;
  if (presentation.component && _uiSurfaceHasCapabilities(presentation.requires)
      && ext && typeof ext.hasComponent === 'function'
      && ext.hasComponent(presentation.component)) {
    rendered = ext.renderComponent(presentation.component, card, {
      surface: surface,
      capabilities: UI_SURFACE_CAPABILITIES.slice(),
      dispatch: function (actionId) {
        const action = (surface.semantic.actions || []).find(
          row => row.id === actionId);
        if (action) _uiSurfaceDispatch(surface, action, new Map());
      },
    });
  }
  if (!rendered) _renderUiSurfaceSemantic(surface, card);
  stack.appendChild(card);
}

function renderUiSurfaces() {
  let stack = document.getElementById('uiSurfaceStack');
  const active = Array.from(_uiSurfaces.values())
    .filter(item => item.status !== 'resolved'
      && item.status !== 'accepted' && item.status !== 'cancelled')
    .sort((a, b) => String(b.updated_at || '').localeCompare(
      String(a.updated_at || '')));
  if (!active.length) {
    if (stack) stack.remove();
    return;
  }
  if (!stack) {
    stack = document.createElement('section');
    stack.id = 'uiSurfaceStack';
    stack.className = 'ui-surface-stack';
    stack.setAttribute('aria-label', 'Interactive workflow surfaces');
    document.body.appendChild(stack);
  }
  stack.replaceChildren();
  active.forEach(surface => _renderUiSurface(surface, stack));
}

function uiSurfaceUpsert(surface) {
  if (!surface || surface.format !== 'pawflow.ui-surface.v1'
      || surface.conversation_id !== conversationId) return;
  const current = _uiSurfaces.get(surface.surface_id);
  if (current && Number(current.revision) > Number(surface.revision)) return;
  _uiSurfaces.set(surface.surface_id, surface);
  renderUiSurfaces();
}

function uiSurfaceEvent(data) {
  if (data && data.surface) uiSurfaceUpsert(data.surface);
}

function resetUiSurfaces(cid) {
  _uiSurfaceConversation = cid || '';
  _uiSurfaces.clear();
  renderUiSurfaces();
}

window.UI_SURFACE_CAPABILITIES = UI_SURFACE_CAPABILITIES;
window.uiSurfaceUpsert = uiSurfaceUpsert;
window.uiSurfaceEvent = uiSurfaceEvent;
window.resetUiSurfaces = resetUiSurfaces;
