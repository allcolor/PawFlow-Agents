// Conversation-scoped simplified turn presentation. Existing renderers create
// every durable node; this controller only reparents those canonical nodes.
const TURN_TEXT_COALESCE_MS = 300;
const TURN_ANIMATION_MS = 1500;
const TURN_TRANSIENT_MAX_CHARS = 180;
const TURN_TRANSIENT_MAX_QUEUE = 3;

let PAWFLOW_CHAT_VIEW_MODE = 'classic';
const simplifiedTurns = new Map();
const _turnUserAnchors = new Map();
const _turnPendingRows = new Map();

function turnViewIsSimplified() { return PAWFLOW_CHAT_VIEW_MODE === 'simplified'; }

function _turnText(key, fallback, vars) {
  try { const value = t(key, vars || {}); return value === key ? fallback : value; }
  catch (_e) { return fallback; }
}

function _turnId(data) {
  if (!data) return '';
  const src = data.source || {};
  return String(data.turn_id || data.request_msg_id || src.turn_id || '').trim();
}

function _turnSvg(kind) {
  const common = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
  if (kind === 'thinking') return '<svg ' + common + '><path d="M9 4a3 3 0 0 0-3 3v1a3 3 0 0 0-1 5.2A3 3 0 0 0 8 17h1m6-13a3 3 0 0 1 3 3v1a3 3 0 0 1 1 5.2A3 3 0 0 1 16 17h-1M9 4v16m6-16v16M9 9h3m0 5h3"/></svg>';
  if (kind === 'tools') return '<svg ' + common + '><path d="M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-2.4 2.4-3-3z"/></svg>';
  if (kind === 'artifacts') return '<svg ' + common + '><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 13h6M9 17h6"/></svg>';
  return '<svg ' + common + '><path d="M4 5h16v11H8l-4 4z"/><path d="M8 9h8M8 12h5"/></svg>';
}

function turnViewSetMode(mode) {
  PAWFLOW_CHAT_VIEW_MODE = mode === 'simplified' ? 'simplified' : 'classic';
  document.documentElement.classList.toggle('simplified-chat-view', turnViewIsSimplified());
  if (!turnViewIsSimplified()) turnViewReset();
}

function turnViewReset() {
  for (const state of simplifiedTurns.values()) _turnStopTransient(state);
  simplifiedTurns.clear();
  _turnUserAnchors.clear();
  _turnPendingRows.clear();
}

function turnViewRegisterUser(extra, element) {
  if (!turnViewIsSimplified() || !element) return;
  const id = _turnId(extra) || String((extra && extra.msg_id) || (element.dataset && element.dataset.msgid) || '').trim();
  if (!id) return;
  element.dataset.turnId = id;
  _turnUserAnchors.set(id, element);
  const state = simplifiedTurns.get(id);
  if (state) { state.userEl = element; _turnPlaceBlock(state); }
  const pending = _turnPendingRows.get(id);
  if (pending && pending.length) {
    _turnPendingRows.delete(id);
    for (const row of pending) turnViewIngest(row.kind, row.data, row.element);
  }
}

function _turnCreateState(turnId, data) {
  const userEl = _turnUserAnchors.get(turnId);
  if (!userEl || !userEl.isConnected) return null;
  const state = {
    turnId, userMsgId: userEl.dataset.msgid || turnId, userEl, agentName: '', llmService: '',
    status: 'working', expanded: false, activeTab: 'messages', finalMsgId: '', finalEl: null,
    identityRendered: false,
    elementsByMsgId: new Map(), toolElementsByCallId: new Map(),
    artifactElementsByFileId: new Map(), artifactFileIdByCallId: new Map(),
    transient: { current: null, queue: [], timer: null, coalesceTimer: null,
                 pendingText: '', pendingKind: '' },
    tabs: {},
  };
  const block = document.createElement('section');
  block.className = 'msg simple-turn-block';
  block.dataset.turnId = turnId;
  const header = document.createElement('button');
  header.type = 'button'; header.className = 'simple-turn-header'; header.setAttribute('aria-expanded', 'false');
  header.innerHTML = '<span class="simple-turn-chevron" aria-hidden="true">&#9656;</span>'
    + '<span class="simple-turn-title"></span><span class="simple-turn-service"></span>'
    + '<span class="simple-turn-status working"></span>';
  header.addEventListener('click', () => _turnSetExpanded(state, !state.expanded));
  block.appendChild(header);
  const ephemeral = document.createElement('div'); ephemeral.className = 'simple-turn-ephemeral';
  ephemeral.setAttribute('aria-live', 'polite');
  ephemeral.innerHTML = '<span class="simple-turn-ephemeral-icons" aria-hidden="true"></span>'
    + '<span class="simple-turn-ephemeral-text"></span>';
  block.appendChild(ephemeral);
  const details = document.createElement('div'); details.className = 'simple-turn-details';
  const tablist = document.createElement('div'); tablist.className = 'simple-turn-tabs'; tablist.setAttribute('role', 'tablist');
  const panels = document.createElement('div');
  const defs = [
    ['messages', 'turnMessages', 'Messages'], ['thinking', 'turnThinking', 'Thinking'],
    ['tools', 'turnToolCalls', 'Tool calls'], ['artifacts', 'turnArtifacts', 'Artifacts'],
  ];
  defs.forEach((def, index) => {
    const key = def[0];
    const tab = document.createElement('button'); tab.type = 'button'; tab.className = 'simple-turn-tab';
    tab.id = 'turn-tab-' + turnId + '-' + key; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', index ? 'false' : 'true');
    tab.setAttribute('tabindex', index ? '-1' : '0'); tab.setAttribute('aria-controls', 'turn-panel-' + turnId + '-' + key);
    tab.innerHTML = _turnSvg(key) + '<span>' + escapeHtml(_turnText(def[1], def[2])) + '</span>';
    tab.addEventListener('click', () => _turnActivateTab(state, key, true));
    tab.addEventListener('keydown', ev => _turnTabKeydown(state, key, ev));
    tablist.appendChild(tab);
    const panel = document.createElement('div'); panel.className = 'simple-turn-panel'; panel.id = 'turn-panel-' + turnId + '-' + key;
    panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id); panel.hidden = index !== 0;
    const body = document.createElement('div'); body.className = 'simple-turn-panel-scroll' + (key === 'artifacts' ? ' simple-turn-artifact-grid' : '');
    panel.appendChild(body); panels.appendChild(panel);
    state.tabs[key] = { tabEl: tab, panelEl: panel, bodyEl: body, unread: 0 };
  });
  details.appendChild(tablist); details.appendChild(panels); block.appendChild(details);
  state.blockEl = block; state.headerEl = header; state.ephemeralEl = ephemeral;
  simplifiedTurns.set(turnId, state);
  _turnUpdateIdentity(state, data || {}); _turnUpdateStatus(state, 'working'); _turnPlaceBlock(state);
  return state;
}

function _turnPlaceBlock(state) {
  if (!state.userEl || !state.userEl.parentNode) return;
  const parent = state.userEl.parentNode;
  const anchor = state.finalEl && state.finalEl.parentNode === parent ? state.finalEl : state.userEl.nextSibling;
  if (state.blockEl.parentNode !== parent || state.blockEl.nextSibling !== state.finalEl) parent.insertBefore(state.blockEl, anchor);
}

function _turnUpdateIdentity(state, data) {
  const src = data.source || {};
  const agentName = data.agent_name || src.name || state.agentName || '';
  const llmService = data.llm_service || data.model || state.llmService || '';
  // Called once per streamed token. Identity almost never changes mid-turn,
  // so re-rendering it per token is pure waste on the hot path.
  if (state.identityRendered && agentName === state.agentName
      && llmService === state.llmService) return;
  state.agentName = agentName; state.llmService = llmService;
  state.identityRendered = true;
  const title = state.agentName ? displayAgentName(state.agentName) : _turnText('agentWorking', 'Agent activity');
  state.headerEl.querySelector('.simple-turn-title').textContent = title;
  const serviceEl = state.headerEl.querySelector('.simple-turn-service');
  serviceEl.textContent = state.llmService; serviceEl.title = state.llmService;
}

function _turnUpdateStatus(state, status) {
  state.status = status;
  const labels = { working: ['turnWorking', 'Working'], completed: ['turnCompleted', 'Completed'],
    stopped: ['turnStopped', 'Stopped'], cancelled: ['turnCancelled', 'Cancelled'], error: ['turnError', 'Error'] };
  const pair = labels[status] || labels.working;
  const el = state.headerEl.querySelector('.simple-turn-status');
  el.className = 'simple-turn-status ' + status; el.textContent = _turnText(pair[0], pair[1]);
}

function _turnSetExpanded(state, expanded) {
  state.expanded = !!expanded; state.blockEl.classList.toggle('expanded', state.expanded);
  state.headerEl.setAttribute('aria-expanded', state.expanded ? 'true' : 'false');
  state.headerEl.setAttribute('aria-label', _turnText(state.expanded ? 'collapseTurnDetails' : 'expandTurnDetails', state.expanded ? 'Collapse turn details' : 'Expand turn details'));
  if (state.expanded) { _turnStopTransient(state); _turnActivateTab(state, state.activeTab, false); }
}

function _turnActivateTab(state, key, focus) {
  if (!state.tabs[key]) return; state.activeTab = key;
  for (const [name, tab] of Object.entries(state.tabs)) {
    const active = name === key; tab.tabEl.setAttribute('aria-selected', active ? 'true' : 'false');
    tab.tabEl.setAttribute('tabindex', active ? '0' : '-1'); tab.panelEl.hidden = !active;
  }
  const current = state.tabs[key]; current.unread = 0; current.tabEl.classList.remove('has-unread');
  if (focus) current.tabEl.focus();
}

function _turnTabKeydown(state, key, ev) {
  const keys = Object.keys(state.tabs); const index = keys.indexOf(key);
  if (ev.key === 'Escape') { ev.preventDefault(); _turnSetExpanded(state, false); state.headerEl.focus(); return; }
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) return;
  ev.preventDefault(); let next = index;
  if (ev.key === 'ArrowLeft') next = (index + keys.length - 1) % keys.length;
  if (ev.key === 'ArrowRight') next = (index + 1) % keys.length;
  if (ev.key === 'Home') next = 0; if (ev.key === 'End') next = keys.length - 1;
  _turnActivateTab(state, keys[next], true);
}

function _turnTabForKind(kind) {
  if (kind === 'thinking' || kind === 'thinking_delta' || kind === 'thinking_content') return 'thinking';
  if (kind === 'tool_call' || kind === 'tool_result') return 'tools';
  if (kind === 'artifact') return 'artifacts';
  return 'messages';
}

function turnViewIngest(kind, data, element) {
  if (!turnViewIsSimplified()) return false;
  const id = _turnId(data); if (!id) return false;
  let state = simplifiedTurns.get(id) || _turnCreateState(id, data);
  if (!state) {
    const pending = _turnPendingRows.get(id) || [];
    if (!pending.some(row => row.element === element && row.kind === kind)) pending.push({ kind, data, element });
    _turnPendingRows.set(id, pending.slice(-100));
    return false;
  }
  _turnUpdateIdentity(state, data || {});
  if (kind === 'tool_result' && turnViewHandleToolResult(data, element)) return true;
  const tabKey = _turnTabForKind(kind); const tab = state.tabs[tabKey];
  if (element) {
    const msgId = String((data && data.msg_id) || (element.dataset && element.dataset.msgid) || '');
    if (msgId) state.elementsByMsgId.set(msgId, element);
    const tcId = String((data && (data.tc_id || data.tool_call_id)) || (element.dataset && element.dataset.tcId) || '');
    if (tcId) state.toolElementsByCallId.set(tcId, element);
    if (data && data.turn_final
        && turnViewFinalize(Object.assign({}, data, { final_msg_id: msgId }))) return true;
    // A rejected finalize (a derived guess losing to an established final) is
    // an ordinary in-turn row: it still belongs in a tab, never left floating.
    if (element.parentNode !== tab.bodyEl) tab.bodyEl.appendChild(element);
  }
  if (state.expanded && state.activeTab !== tabKey) { tab.unread++; tab.tabEl.classList.add('has-unread'); }
  const text = data && (data.text || data.content || data.response || '');
  if (!(data && data._history)) _turnOfferTransient(state, tabKey, text);
  return true;
}

function _turnResolvedToolName(data, element, state) {
  const tcId = String((data && (data.tc_id || data.tool_call_id)) || (element && element.dataset && element.dataset.tcId) || '');
  const callEl = (tcId && state && state.toolElementsByCallId.get(tcId))
    || (tcId && typeof findToolCallElement === 'function' ? findToolCallElement(tcId) : null);
  return String((data && (data.tool || data.tool_name))
    || (callEl && callEl.dataset && callEl.dataset.tool)
    || (element && element.dataset && element.dataset.tool) || '');
}

function _turnCompactPresentedResult(state, tcId, callEl) {
  if (!callEl) return;
  callEl.querySelectorAll('.tc-result').forEach(el => el.remove());
  const bullet = callEl.querySelector('.tc-bullet');
  if (bullet) { bullet.classList.remove('pending'); bullet.classList.add('done'); }
  callEl.querySelectorAll('.tc-bg-btn, .tc-kl-btn').forEach(el => el.remove());
  let link = callEl.querySelector('.simple-turn-artifact-link');
  if (!link) {
    link = document.createElement('button'); link.type = 'button'; link.className = 'simple-turn-artifact-link';
    link.textContent = _turnText('turnPresentedArtifacts', 'Presented in Artifacts');
    link.addEventListener('click', ev => { ev.stopPropagation(); _turnSetExpanded(state, true); _turnActivateTab(state, 'artifacts', true); });
    callEl.appendChild(link);
  }
  if (tcId) link.dataset.tcId = tcId;
}

function turnViewHandleToolResult(data, resultElement) {
  if (!turnViewIsSimplified()) return false;
  const id = _turnId(data); const state = simplifiedTurns.get(id); if (!state) return false;
  const tcId = String((data && (data.tc_id || data.tool_call_id)) || (resultElement && resultElement.dataset && resultElement.dataset.tcId) || '');
  const callEl = (tcId && state.toolElementsByCallId.get(tcId))
    || (tcId && typeof findToolCallElement === 'function' ? findToolCallElement(tcId) : null);
  const toolName = _turnResolvedToolName(data, resultElement, state);
  const resultText = data && (data.result !== undefined ? data.result : data.content);
  const artifact = typeof parseShowFileArtifact === 'function' ? parseShowFileArtifact(resultText, toolName) : null;
  if (!artifact) return false;
  if (callEl) {
    state.toolElementsByCallId.set(tcId, callEl);
    if (callEl.parentNode !== state.tabs.tools.bodyEl) state.tabs.tools.bodyEl.appendChild(callEl);
  }
  _turnCompactPresentedResult(state, tcId, callEl);
  if (resultElement) {
    resultElement.style.display = 'none';
    if (resultElement.parentNode !== state.tabs.tools.bodyEl) state.tabs.tools.bodyEl.appendChild(resultElement);
  }
  let card = state.artifactElementsByFileId.get(artifact.file_id);
  if (!card) {
    card = document.createElement('article'); card.className = 'simple-turn-artifact-card';
    card.dataset.fileId = artifact.file_id; card.dataset.fileUrl = artifact.url;
    card.innerHTML = '<div class="simple-turn-artifact-preview">'
      + (typeof renderShowFileArtifactHtml === 'function' ? renderShowFileArtifactHtml(artifact) : escapeHtml(artifact.filename))
      + '</div><div class="simple-turn-artifact-meta"><strong>' + escapeHtml(artifact.filename) + '</strong>'
      + '<span>' + escapeHtml(artifact.content_type || '') + (artifact.size_kb ? ' · ' + escapeHtml(String(artifact.size_kb)) + ' KB' : '') + '</span></div>';
    state.tabs.artifacts.bodyEl.appendChild(card); state.artifactElementsByFileId.set(artifact.file_id, card);
  }
  if (tcId) { state.artifactFileIdByCallId.set(tcId, artifact.file_id); card.dataset.tcId = tcId; }
  if (state.expanded && state.activeTab !== 'artifacts') {
    state.tabs.artifacts.unread++; state.tabs.artifacts.tabEl.classList.add('has-unread');
  }
  if (!(data && data._history)) _turnOfferTransient(state, 'artifacts', _turnText('turnPresentedArtifacts', 'Presented in Artifacts') + ': ' + artifact.filename);
  return true;
}

function _turnOfferTransient(state, kind, text) {
  if (state.expanded || state.status !== 'working') return;
  let label = String(text || '').replace(/\s+/g, ' ').trim();
  if (kind === 'tools') label = _turnText('turnCallingTool', 'Calling tool...');
  if (!label) return; label = label.slice(0, TURN_TRANSIENT_MAX_CHARS);
  // Streaming text arrives token by token: hold the newest excerpt and emit
  // one cue per coalescing window instead of one per token. Tool and artifact
  // cues are discrete events and are queued immediately.
  if (kind === 'messages' || kind === 'thinking') {
    state.transient.pendingKind = kind;
    state.transient.pendingText = label;
    if (!state.transient.coalesceTimer) {
      state.transient.coalesceTimer = setTimeout(() => {
        state.transient.coalesceTimer = null;
        _turnFlushTransient(state);
      }, TURN_TEXT_COALESCE_MS);
    }
    return;
  }
  _turnEnqueueTransient(state, { kind, text: label });
}

function _turnFlushTransient(state) {
  const kind = state.transient.pendingKind; const text = state.transient.pendingText;
  state.transient.pendingKind = ''; state.transient.pendingText = '';
  if (!text || state.expanded || state.status !== 'working') return;
  _turnEnqueueTransient(state, { kind, text });
}

function _turnEnqueueTransient(state, item) {
  const kind = item.kind;
  const tail = state.transient.queue[state.transient.queue.length - 1];
  if (tail && tail.kind === kind && (kind === 'messages' || kind === 'thinking')) Object.assign(tail, item);
  else { state.transient.queue.push(item); if (state.transient.queue.length > TURN_TRANSIENT_MAX_QUEUE) state.transient.queue.shift(); }
  if (!state.transient.timer) _turnRunTransient(state);
}

function _turnRunTransient(state) {
  if (state.expanded || state.status !== 'working') { _turnStopTransient(state); return; }
  const item = state.transient.queue.shift(); if (!item) { state.transient.timer = null; return; }
  state.transient.current = item;
  const textEl = state.ephemeralEl.querySelector('.simple-turn-ephemeral-text');
  const iconEl = state.ephemeralEl.querySelector('.simple-turn-ephemeral-icons');
  textEl.textContent = item.text; iconEl.innerHTML = '<span class="simple-turn-ephemeral-icon">' + _turnSvg(item.kind) + '</span>';
  textEl.style.animation = 'none'; iconEl.firstChild.style.animation = 'none'; void textEl.offsetWidth;
  textEl.style.animation = ''; iconEl.firstChild.style.animation = '';
  state.transient.timer = setTimeout(() => { state.transient.timer = null; _turnRunTransient(state); }, TURN_ANIMATION_MS);
}

function _turnStopTransient(state) {
  if (state.transient.timer) clearTimeout(state.transient.timer);
  if (state.transient.coalesceTimer) clearTimeout(state.transient.coalesceTimer);
  state.transient.timer = null; state.transient.coalesceTimer = null; state.transient.current = null; state.transient.queue = [];
  state.transient.pendingText = ''; state.transient.pendingKind = '';
  if (state.ephemeralEl) {
    const textEl = state.ephemeralEl.querySelector('.simple-turn-ephemeral-text');
    const iconEl = state.ephemeralEl.querySelector('.simple-turn-ephemeral-icons');
    if (textEl) textEl.textContent = ''; if (iconEl) iconEl.textContent = '';
  }
}

function turnViewFinalize(data) {
  if (!turnViewIsSimplified()) return false;
  const id = _turnId(data); const state = simplifiedTurns.get(id); if (!state) return false;
  const finalId = String((data && (data.final_msg_id || data.msg_id)) || '');
  // A derived marker is a reconstruction guess, not an authority: pagination
  // classifies each page on its own, so an older page can derive a second
  // final for a turn whose real answer is already placed. Never let a guess
  // displace a final that is already established.
  if (state.finalEl && state.finalMsgId && state.finalMsgId !== finalId
      && data && data.turn_final_derived) return false;
  let element = finalId ? state.elementsByMsgId.get(finalId) : null;
  if (!element && finalId) element = document.querySelector('#messages [data-msgid="' + CSS.escape(finalId) + '"]');
  if (element) {
    // Reloading a still-running turn derives a final from the last narration.
    // When the real terminal answer arrives, hand the block back its row
    // instead of leaving two standalone messages after the block.
    if (state.finalEl && state.finalEl !== element && state.finalEl.isConnected
        && state.tabs.messages) {
      state.tabs.messages.bodyEl.appendChild(state.finalEl);
    }
    state.finalMsgId = finalId; state.finalEl = element; element.classList.remove('streaming');
    const parent = state.blockEl.parentNode; if (parent) parent.insertBefore(element, state.blockEl.nextSibling);
  }
  _turnStopTransient(state); _turnUpdateStatus(state, 'completed'); _turnPlaceBlock(state);
  return true;
}

function turnViewFail(turnId, status, message) {
  if (!turnViewIsSimplified()) return false;
  const state = simplifiedTurns.get(String(turnId || '')); if (!state) return false;
  if (state.status === 'completed') return false;
  _turnStopTransient(state); _turnUpdateStatus(state, ['stopped', 'cancelled', 'error'].includes(status) ? status : 'error');
  if (message) _turnOfferTransient(state, 'messages', message);
  return true;
}

function turnViewReconcile() {
  if (!turnViewIsSimplified()) return;
  for (const state of simplifiedTurns.values()) {
    _turnPlaceBlock(state);
    if (state.finalEl && state.blockEl.parentNode) state.blockEl.parentNode.insertBefore(state.finalEl, state.blockEl.nextSibling);
  }
}

function turnViewEvictionGroup(element) {
  if (!turnViewIsSimplified() || !element) return [element];
  const id = (element.dataset && element.dataset.turnId) || '';
  let state = id ? simplifiedTurns.get(id) : null;
  if (!state) {
    for (const candidate of simplifiedTurns.values()) {
      if (candidate.userEl === element || candidate.blockEl === element || candidate.finalEl === element) { state = candidate; break; }
    }
  }
  if (!state) return [element];
  return [state.userEl, state.blockEl, state.finalEl].filter(el => el && el.isConnected);
}

function turnViewForgetElement(element) {
  if (!element) return;
  for (const [id, state] of simplifiedTurns.entries()) {
    if (state.userEl === element || state.blockEl === element || state.finalEl === element) {
      _turnStopTransient(state); simplifiedTurns.delete(id); _turnUserAnchors.delete(id); _turnPendingRows.delete(id); return;
    }
  }
}
