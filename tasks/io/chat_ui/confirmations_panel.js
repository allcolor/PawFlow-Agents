// ── Durable confirmation requests ────────────────────────────────
// An agent (request_confirmation tool) or a flow (requestConfirmation
// task) asks the user something: yes/no, single choice, or multi choice.
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
      ? t('confAnswered', { answer: Array.isArray(c.answer) ? c.answer.join(', ') : String(c.answer) })
      : t('confClosed', { status: c.status });
    body.appendChild(done);
    return body;
  }
  const actions = document.createElement('div');
  actions.className = 'conf-actions';
  if (c.mode === 'multi') {
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
      if (values.length) respondConfirmation(c.request_id, values);
    };
    actions.appendChild(submit);
  } else {
    (c.options || []).forEach((o) => {
      const btn = document.createElement('button');
      btn.className = 'btn conf-btn';
      btn.textContent = o.label || o.value;
      btn.onclick = () => respondConfirmation(c.request_id, o.value);
      actions.appendChild(btn);
    });
  }
  body.appendChild(actions);
  return body;
}

// Inline block inside #messages (live SSE + re-hydrated after reload).
function renderConfirmationBlock(c) {
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

function _confMarkDone(c) {
  const el = document.getElementById(_confBlockId(c.request_id));
  if (!el) return;
  const body = el.querySelector('.conf-body');
  if (body) body.replaceWith(_confBody(c));
}

function respondConfirmation(requestId, answer) {
  if (typeof action$ !== 'function') return;
  action$('respond_confirmation', {
    request_id: requestId, answer: answer,
    conversation_id: conversationId,
  }).subscribe((data) => {
    if (data && data.error) {
      addMsg('system', t('confRespondFailed', { error: data.error }));
      return;
    }
    if (data && data.confirmation) _confMarkDone(data.confirmation);
    hydrateConfirmations();
    if (document.getElementById('confirmationsPanel')
        && document.getElementById('confirmationsPanel').style.display !== 'none') {
      loadConfirmations();
    }
  });
}

// ── Pending panel ────────────────────────────────────────────────

async function toggleConfirmationsPanel() {
  const panel = document.getElementById('confirmationsPanel');
  if (!panel) return;
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    loadConfirmations();
  } else {
    panel.style.display = 'none';
  }
}

function loadConfirmations() {
  const list = document.getElementById('confirmationsList');
  if (!list || typeof action$ !== 'function') return;
  list.innerHTML = '<span style="color:#808090;font-size:12px">' + t('loading') + '</span>';
  // ALL pending requests of the user, every conversation: the panel is
  // the durable inbox — a request must stay reachable days later even if
  // its conversation is not the open one.
  action$('list_confirmations', { status: 'pending' }).subscribe((data) => {
    const rows = (data && data.confirmations) || [];
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

// Called after each history render: re-shows this conversation's pending
// requests as actionable inline blocks (durable ≠ SSE event) and refreshes
// the global badge.
function hydrateConfirmations() {
  if (typeof action$ !== 'function') return;
  action$('list_confirmations', { status: 'pending' }).subscribe((data) => {
    const rows = (data && data.confirmations) || [];
    _confBadgeSet(rows.length);
    rows.filter((c) => c.conversation_id === conversationId)
      .forEach((c) => renderConfirmationBlock(c));
  });
}

function cmdConfirmations() {
  toggleConfirmationsPanel();
  return true;
}
