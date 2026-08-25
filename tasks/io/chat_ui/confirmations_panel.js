// ── Durable typed user interactions ──────────────────────────────
// An agent or flow can request confirmation, choices, text, numbers,
// dates, a FileStore reference, or a structured form.
// The request is DURABLE — it survives reloads and restarts and the user
// answers whenever they want: from the inline block in the conversation
// (rendered live on SSE and re-rendered from the store after a reload)
// or from the pending panel (header ✅ button, badge, /confirmations).
// Answering resumes the requester (agent wake-up / durable flow signal).

var _confirmationsPending = 0;

function _confBadgeSet(count) {
  _confirmationsPending = count;
  const badge = document.getElementById('confirmationsBadge');
  if (!badge) return;
  badge.textContent = String(count);
  badge.hidden = !count;
}

function _confBlockId(requestId) { return 'conf-' + requestId; }

function _confAge(createdAt) {
  const s = Math.max(0, (Date.now() / 1000) - (Number(createdAt) || 0));
  if (s < 90) return t('confAgeNow');
  if (s < 5400) return Math.round(s / 60) + ' min';
  if (s < 172800) return Math.round(s / 3600) + ' h';
  return Math.round(s / 86400) + ' j';
}

function _confKind(c) { return c.kind || c.mode || 'confirm'; }

function _confAnswerText(answer) {
  if (Array.isArray(answer)) return answer.join(', ');
  if (answer && typeof answer === 'object') return JSON.stringify(answer);
  return String(answer);
}

function _confTypedInput(kind, schema) {
  schema = schema || {};
  if (kind === 'multiline') {
    const textarea = document.createElement('textarea');
    textarea.className = 'conf-input conf-textarea';
    if (schema.max_length) textarea.maxLength = Number(schema.max_length);
    return { element: textarea, read: () => textarea.value };
  }
  if (kind === 'integer' || kind === 'decimal') {
    const input = document.createElement('input');
    input.className = 'conf-input';
    input.type = 'number';
    input.step = kind === 'integer' ? '1' : 'any';
    if (schema.minimum !== undefined) input.min = String(schema.minimum);
    if (schema.maximum !== undefined) input.max = String(schema.maximum);
    return { element: input, read: () => input.value };
  }
  if (kind === 'date' || kind === 'datetime') {
    const input = document.createElement('input');
    input.className = 'conf-input';
    input.type = kind === 'date' ? 'date' : 'datetime-local';
    return {
      element: input,
      read: () => kind === 'datetime' && input.value
        ? new Date(input.value).toISOString() : input.value,
    };
  }
  if (kind === 'file') {
    const input = document.createElement('input');
    input.className = 'conf-input';
    input.type = 'file';
    return {
      element: input,
      read: async () => {
        const file = input.files && input.files[0];
        if (!file) return null;
        if (typeof uploadFileToStore !== 'function') throw new Error('File upload unavailable');
        const uploaded = await uploadFileToStore(file);
        return {
          file_id: uploaded.file_id,
          name: uploaded.filename || file.name,
          mime_type: uploaded.mime_type || file.type || '',
          size: uploaded.size || file.size,
        };
      },
    };
  }
  const input = document.createElement('input');
  input.className = 'conf-input';
  input.type = 'text';
  if (schema.max_length) input.maxLength = Number(schema.max_length);
  return { element: input, read: () => input.value };
}

function _confFormInput(schema) {
  const wrapper = document.createElement('div');
  wrapper.className = 'conf-form';
  const readers = [];
  (schema.fields || []).forEach((field) => {
    const row = document.createElement('label');
    row.className = 'conf-form-field';
    const title = document.createElement('span');
    title.textContent = (field.label || field.name) + (field.required ? ' *' : '');
    row.appendChild(title);
    let reader;
    if (field.type === 'choice') {
      const select = document.createElement('select');
      select.className = 'conf-input';
      if (!field.required) select.appendChild(new Option('', ''));
      (field.options || []).forEach((option) => {
        const value = typeof option === 'object' ? option.value : option;
        const label = typeof option === 'object' ? (option.label || value) : value;
        select.appendChild(new Option(label, value));
      });
      row.appendChild(select);
      reader = () => select.value || null;
    } else if (field.type === 'multi') {
      const boxes = [];
      const choices = document.createElement('span');
      choices.className = 'conf-form-choices';
      (field.options || []).forEach((option) => {
        const value = typeof option === 'object' ? option.value : option;
        const label = typeof option === 'object' ? (option.label || value) : value;
        const choice = document.createElement('label');
        const box = document.createElement('input');
        box.type = 'checkbox'; box.value = value; boxes.push(box);
        choice.append(box, document.createTextNode(' ' + label));
        choices.appendChild(choice);
      });
      row.appendChild(choices);
      reader = () => boxes.filter((box) => box.checked).map((box) => box.value);
    } else {
      const typed = _confTypedInput(field.type || 'text', field);
      row.appendChild(typed.element);
      reader = typed.read;
    }
    readers.push({ name: field.name, read: reader });
    wrapper.appendChild(row);
  });
  return {
    element: wrapper,
    read: async () => {
      const answer = {};
      for (const field of readers) {
        const value = await field.read();
        if (value !== null && value !== '' && (!Array.isArray(value) || value.length)) {
          answer[field.name] = value;
        }
      }
      return answer;
    },
  };
}

// Build the actionable body (buttons / checkboxes) for one request.
function _confBody(c) {
  const body = document.createElement('div');
  body.className = 'conf-body';
  const msg = document.createElement('div');
  msg.className = 'conf-message';
  msg.textContent = c.message || '';
  body.appendChild(msg);
  if (c.status !== 'pending') {
    const done = document.createElement('div');
    done.className = 'conf-done';
    done.textContent = c.status === 'answered'
      ? t('confAnswered', { answer: _confAnswerText(c.answer) })
      : t('confClosed', { status: c.status });
    body.appendChild(done);
    return body;
  }
  const actions = document.createElement('div');
  actions.className = 'conf-actions';
  const kind = _confKind(c);
  if (kind === 'multi') {
    const boxes = [];
    (c.options || []).forEach((o) => {
      const label = document.createElement('label');
      label.className = 'conf-check';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = o.value;
      boxes.push(cb);
      label.append(cb, document.createTextNode(' ' + (o.label || o.value)));
      actions.appendChild(label);
    });
    const submit = document.createElement('button');
    submit.className = 'btn conf-btn conf-submit';
    submit.textContent = t('confValidate');
    submit.onclick = () => {
      const values = boxes.filter((b) => b.checked).map((b) => b.value);
      if (values.length) respondInteraction(c.request_id, values);
    };
    actions.appendChild(submit);
  } else if (kind === 'confirm' || kind === 'choice') {
    (c.options || []).forEach((o) => {
      const btn = document.createElement('button');
      btn.className = 'btn conf-btn';
      btn.textContent = o.label || o.value;
      btn.onclick = () => respondInteraction(c.request_id, o.value);
      actions.appendChild(btn);
    });
  } else {
    const typed = kind === 'form'
      ? _confFormInput(c.response_schema || {})
      : _confTypedInput(kind, c.response_schema || {});
    actions.appendChild(typed.element);
    const submit = document.createElement('button');
    submit.className = 'btn conf-btn conf-submit';
    submit.textContent = t('confValidate');
    submit.onclick = async () => {
      submit.disabled = true;
      try {
        await respondInteraction(c.request_id, await typed.read());
      } catch (error) {
        addMsg('system', t('confRespondFailed', { error: error.message || String(error) }));
        submit.disabled = false;
      }
    };
    actions.appendChild(submit);
  }
  body.appendChild(actions);
  return body;
}

// Inline block inside #messages (live SSE + re-hydrated after reload).
function renderInteractionBlock(c) {
  if (!c || !c.request_id) return;
  const existing = document.getElementById(_confBlockId(c.request_id));
  if (existing) existing.remove();
  const el = document.createElement('div');
  el.className = 'msg confirmation-block';
  el.id = _confBlockId(c.request_id);
  el.dataset.messageRole = 'confirmation';
  el.dataset.sortTs = String(c.created_at || (Date.now() / 1000));
  const head = document.createElement('div');
  head.className = 'conf-head';
  head.textContent = '\u2705 ' + (c.title || t('confTitle'))
    + ' \u00B7 ' + (c.requester || c.requester_kind || '');
  el.appendChild(head);
  el.appendChild(_confBody(c));
  const container = document.getElementById('messages');
  if (!container) return;
  const typing = document.getElementById('typing');
  if (typing) container.insertBefore(el, typing);
  else container.appendChild(el);
  if (typeof scrollBottom === 'function') scrollBottom();
}

function renderConfirmationBlock(c) { renderInteractionBlock(c); }

function _confMarkDone(c) {
  const el = document.getElementById(_confBlockId(c.request_id));
  if (!el) return;
  const body = el.querySelector('.conf-body');
  if (body) body.replaceWith(_confBody(c));
}

function respondInteraction(requestId, answer) {
  if (typeof action$ !== 'function') return Promise.resolve();
  return new Promise((resolve, reject) => action$('respond_interaction', {
    request_id: requestId, answer: answer,
    conversation_id: conversationId,
  }).subscribe({ next: (data) => {
    if (data && data.error) {
      reject(new Error(data.error));
      return;
    }
    if (data && data.interaction) _confMarkDone(data.interaction);
    hydrateInteractions();
    if (document.getElementById('confirmationsPanel')
        && document.getElementById('confirmationsPanel').style.display !== 'none') {
      loadInteractions();
    }
    resolve(data);
  }, error: reject }));
}

function respondConfirmation(requestId, answer) {
  return respondInteraction(requestId, answer);
}

// ── Pending panel ────────────────────────────────────────────────

async function toggleConfirmationsPanel() {
  const panel = document.getElementById('confirmationsPanel');
  if (!panel) return;
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    loadInteractions();
  } else {
    panel.style.display = 'none';
  }
}

function loadInteractions() {
  const list = document.getElementById('confirmationsList');
  if (!list || typeof action$ !== 'function') return;
  list.innerHTML = '<span style="color:#808090;font-size:12px">' + t('loading') + '</span>';
  // ALL pending requests of the user, every conversation: the panel is
  // the durable inbox — a request must stay reachable days later even if
  // its conversation is not the open one.
  action$('list_interactions', { status: 'pending' }).subscribe((data) => {
    const rows = (data && data.interactions) || [];
    _confBadgeSet(rows.length);
    list.innerHTML = '';
    if (!rows.length) {
      list.innerHTML = '<span style="color:#808090;font-size:12px">' + t('confNonePending') + '</span>';
      return;
    }
    rows.forEach((c) => {
      const row = document.createElement('div');
      row.className = 'confirmation-block conf-panel-row';
      const head = document.createElement('div');
      head.className = 'conf-head';
      head.textContent = '\u2705 ' + (c.title || t('confTitle'))
        + ' \u00B7 ' + (c.requester || '') + ' \u00B7 ' + _confAge(c.created_at)
        + (c.conversation_id !== conversationId ? ' \u00B7 \u{1F4AC}' : '');
      row.appendChild(head);
      row.appendChild(_confBody(c));
      list.appendChild(row);
    });
  });
}

function loadConfirmations() { loadInteractions(); }

// Called after each history render: re-shows this conversation's pending
// requests as actionable inline blocks (durable ≠ SSE event) and refreshes
// the global badge.
function hydrateInteractions() {
  if (typeof action$ !== 'function') return;
  action$('list_interactions', { status: 'pending' }).subscribe((data) => {
    const rows = (data && data.interactions) || [];
    _confBadgeSet(rows.length);
    rows.filter((c) => c.conversation_id === conversationId)
      .forEach((c) => renderInteractionBlock(c));
  });
}

function hydrateConfirmations() { hydrateInteractions(); }

function cmdConfirmations() {
  toggleConfirmationsPanel();
  return true;
}
