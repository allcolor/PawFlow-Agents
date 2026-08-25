// Durable typed user-interaction cards for the VS Code webview.
var _vscodeInteractions = Object.create(null);

function _vscInteractionId(requestId) {
  return 'interaction-' + String(requestId || '').replace(/[^A-Za-z0-9_-]/g, '');
}

function _vscInteractionKind(interaction) {
  return interaction.kind || interaction.mode || 'confirm';
}

function _vscChoiceValue(option) {
  return typeof option === 'object' ? String(option.value || '') : String(option || '');
}

function _vscChoiceLabel(option) {
  return typeof option === 'object'
    ? String(option.label || option.value || '') : String(option || '');
}

function _vscScalarInput(kind, schema, options) {
  schema = schema || {};
  if (kind === 'choice') {
    var select = document.createElement('select');
    select.className = 'interaction-input';
    (options || []).forEach(function(option) {
      select.appendChild(new Option(_vscChoiceLabel(option), _vscChoiceValue(option)));
    });
    return {element: select, read: function() { return select.value; }};
  }
  if (kind === 'multi') {
    var wrap = document.createElement('span');
    wrap.className = 'interaction-choices';
    var boxes = [];
    (options || []).forEach(function(option) {
      var label = document.createElement('label');
      var box = document.createElement('input');
      box.type = 'checkbox';
      box.value = _vscChoiceValue(option);
      boxes.push(box);
      label.append(box, document.createTextNode(' ' + _vscChoiceLabel(option)));
      wrap.appendChild(label);
    });
    return {
      element: wrap,
      read: function() {
        return boxes.filter(function(box) { return box.checked; })
          .map(function(box) { return box.value; });
      },
    };
  }
  var input = kind === 'multiline'
    ? document.createElement('textarea') : document.createElement('input');
  input.className = 'interaction-input';
  if (kind === 'integer' || kind === 'decimal') {
    input.type = 'number';
    input.step = kind === 'integer' ? '1' : 'any';
    if (schema.minimum !== undefined) input.min = String(schema.minimum);
    if (schema.maximum !== undefined) input.max = String(schema.maximum);
  } else if (kind === 'date') {
    input.type = 'date';
  } else if (kind === 'datetime') {
    input.type = 'datetime-local';
  } else {
    input.type = 'text';
  }
  if (kind === 'file') input.placeholder = 'FileStore file ID';
  if (schema.max_length) input.maxLength = Number(schema.max_length);
  return {
    element: input,
    read: function() {
      if (kind === 'datetime' && input.value) return new Date(input.value).toISOString();
      if (kind === 'file') return {file_id: input.value.trim()};
      return input.value;
    },
  };
}

function _vscFormInput(schema) {
  var wrapper = document.createElement('div');
  wrapper.className = 'interaction-form';
  var readers = [];
  ((schema || {}).fields || []).forEach(function(field) {
    var label = document.createElement('label');
    label.className = 'interaction-field';
    var caption = document.createElement('span');
    caption.textContent = (field.label || field.name) + (field.required ? ' *' : '');
    var typed = _vscScalarInput(field.type || 'text', field, field.options || []);
    label.append(caption, typed.element);
    wrapper.appendChild(label);
    readers.push({name: field.name, read: typed.read});
  });
  return {
    element: wrapper,
    read: function() {
      var result = {};
      readers.forEach(function(field) {
        var value = field.read();
        if (value !== '' && value !== null && (!Array.isArray(value) || value.length)) {
          result[field.name] = value;
        }
      });
      return result;
    },
  };
}

function respondVscodeInteraction(requestId, answer, cancel) {
  vscode.postMessage({
    type: 'interactionResponse',
    requestId: requestId,
    answer: answer,
    cancel: !!cancel,
  });
}

function renderVscodeInteraction(interaction) {
  if (!interaction || !interaction.request_id) return;
  var requestId = String(interaction.request_id);
  if (interaction.status && interaction.status !== 'pending') {
    closeVscodeInteraction(requestId);
    return;
  }
  _vscodeInteractions[requestId] = interaction;
  var existing = document.getElementById(_vscInteractionId(requestId));
  if (existing) existing.remove();

  var card = document.createElement('div');
  card.className = 'msg interaction-card';
  card.id = _vscInteractionId(requestId);

  var heading = document.createElement('strong');
  heading.textContent = interaction.title || ('User input · ' + _vscInteractionKind(interaction));
  var message = document.createElement('div');
  message.className = 'interaction-message';
  message.textContent = interaction.message || '';
  var actions = document.createElement('div');
  actions.className = 'interaction-actions';
  var kind = _vscInteractionKind(interaction);

  if (kind === 'confirm') {
    (interaction.options || []).forEach(function(option) {
      var button = document.createElement('button');
      button.textContent = _vscChoiceLabel(option);
      button.onclick = function() {
        respondVscodeInteraction(requestId, _vscChoiceValue(option), false);
        button.disabled = true;
      };
      actions.appendChild(button);
    });
  } else {
    var typed = kind === 'form'
      ? _vscFormInput(interaction.response_schema || {})
      : _vscScalarInput(kind, interaction.response_schema || {}, interaction.options || []);
    actions.appendChild(typed.element);
    var submit = document.createElement('button');
    submit.textContent = 'Submit';
    submit.onclick = function() {
      submit.disabled = true;
      respondVscodeInteraction(requestId, typed.read(), false);
    };
    actions.appendChild(submit);
  }
  var cancel = document.createElement('button');
  cancel.className = 'interaction-cancel';
  cancel.textContent = 'Cancel';
  cancel.onclick = function() {
    cancel.disabled = true;
    respondVscodeInteraction(requestId, null, true);
  };
  actions.appendChild(cancel);
  card.append(heading, message, actions);
  messagesEl.appendChild(card);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function closeVscodeInteraction(requestId) {
  delete _vscodeInteractions[requestId];
  var card = document.getElementById(_vscInteractionId(requestId));
  if (card) card.remove();
}

function vscodeInteractionEvent(eventType, data) {
  if (eventType === 'interaction_request' || eventType === 'confirmation_request') {
    renderVscodeInteraction(data);
  } else if (eventType === 'interaction_answered' || eventType === 'confirmation_answered') {
    closeVscodeInteraction(data && data.request_id);
  }
}
