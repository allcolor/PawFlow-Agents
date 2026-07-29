// Conversation-scoped simplified turn presentation. Existing renderers create
// every durable node; this controller only reparents those canonical nodes.
const TURN_TEXT_COALESCE_MS = 300;
// Each cue lives on its own: it enters blurred, sharpens, holds, then blurs
// out again and removes itself. Cues do not take turns -- several are on
// screen at once, stacked, each one at its own point in that arc.
const TURN_CUE_LIFETIME_MS = 2600;
const TURN_TRANSIENT_MAX_CHARS = 180;
const TURN_TRANSIENT_MAX_STACK = 4;

let PAWFLOW_CHAT_VIEW_MODE = 'classic';
const simplifiedTurns = new Map();
// Turn boundaries are positional, not correlated: a user message opens a turn,
// the terminal answer and the next user message close it. Everything rendered
// in between belongs to the open turn's detail block. A turn id, when the
// server provides one, only names a turn that is already open -- it is never
// required, so a row nobody stamped still lands where it belongs.
let _turnOpen = null;
let _turnSeq = 0;

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
  _turnOpen = null;
}

// A user message is a boundary: it closes whatever turn was open and opens the
// next one. Nothing here may fail on missing metadata -- this is the only thing
// the whole view is built on. The block itself is built on the turn's first
// row, so a user message nothing followed (a pending prompt, the tail of a
// page) is not given an empty one.
function turnViewRegisterUser(extra, element) {
  if (!turnViewIsSimplified() || !element) return;
  if (_turnOpen && _turnOpen.state) _turnStopTransient(_turnOpen.state);
  const id = _turnId(extra)
    || String((extra && extra.msg_id) || (element.dataset && element.dataset.msgid) || '').trim()
    || ('turn-' + (++_turnSeq));
  element.dataset.turnId = id;
  _turnOpen = { turnId: id, userEl: element, data: extra || {}, state: null };
}

function _turnCreateState(turnId, userEl, data) {
  if (!userEl) return null;
  const state = {
    turnId, userMsgId: userEl.dataset.msgid || turnId, userEl, agentName: '', llmService: '',
    status: 'working', expanded: false, activeTab: 'messages', finalMsgId: '', finalEl: null,
    identityRendered: false,
    elementsByMsgId: new Map(), toolElementsByCallId: new Map(),
    artifactElementsByFileId: new Map(), artifactFileIdByCallId: new Map(),
    transient: { cues: [], coalesceTimer: null, pendingText: '', pendingKind: '' },
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
  // The ephemeral surface only ever shows a cue while the turn runs. A reloaded
  // transcript is all finished turns, so leaving it laid out would give every
  // one of them an empty band under its header.
  state.blockEl.classList.toggle('turn-working', status === 'working');
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

// Every row belongs to the turn currently open -- position decides, and only
// position. A turn_id is never consulted here: when the two disagree the layout
// on screen has to win. A second user message arriving before the answer gives
// user / block / user / block / answer, with the answer under the LAST block,
// which is where the reader is looking; routing it back under the first block
// because the done event still carries the first turn's id would put it above
// content that came after it.
//
// Rows that precede any user message -- a partial first page, a system notice --
// have no open turn and stay top level rather than being forced into a wrong one.
function _turnCurrentState(create) {
  if (!_turnOpen || !_turnOpen.userEl.isConnected) return null;
  if (!_turnOpen.state && create) {
    _turnOpen.state = _turnCreateState(_turnOpen.turnId, _turnOpen.userEl, _turnOpen.data);
  }
  return _turnOpen.state;
}

function turnViewIngest(kind, data, element) {
  if (!turnViewIsSimplified()) return false;
  const state = _turnCurrentState(true);
  if (!state || !state.blockEl.isConnected) return false;
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
  const state = _turnCurrentState(false); if (!state) return false;
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

// The cues share one spot and stack in depth, not in a column. The newest
// zooms in at the front; every cue behind it is pushed back a step -- smaller,
// dimmer, blurrier -- until it falls off the back of the stack. Depth is a
// function of arrival order alone, so it is recomputed, never accumulated.
function _turnEnqueueTransient(state, item) {
  if (!state.ephemeralEl) return;
  const cue = document.createElement('span');
  cue.className = 'simple-turn-cue ' + item.kind;
  const icons = document.createElement('span');
  icons.className = 'simple-turn-ephemeral-icons'; icons.setAttribute('aria-hidden', 'true');
  const icon = document.createElement('span');
  icon.className = 'simple-turn-ephemeral-icon'; icon.innerHTML = _turnSvg(item.kind);
  icons.appendChild(icon);
  const text = document.createElement('span');
  text.className = 'simple-turn-ephemeral-text'; text.textContent = item.text;
  cue.appendChild(icons); cue.appendChild(text);
  // Enter small and blurred; the transition to depth 0 is the zoom in.
  _turnStyleCue(cue, -1);
  state.ephemeralEl.appendChild(cue);
  const entry = { el: cue, timer: null, enter: null };
  // Restack rather than jump to the front: a cue that arrives during this
  // delay has already taken depth 0, and this one belongs behind it.
  entry.enter = setTimeout(() => { entry.enter = null; _turnRestackCues(state); }, 16);
  entry.timer = setTimeout(() => _turnRetireCue(state, entry), TURN_CUE_LIFETIME_MS);
  state.transient.cues.push(entry);
  _turnRestackCues(state);
}

// depth 0 is the front. -1 is the pre-entry pose; anything past the last
// visible step is gone.
function _turnStyleCue(el, depth) {
  const scale = depth < 0 ? 0.72 : Math.max(0.4, 1 - depth * 0.15);
  const lift = depth < 0 ? 10 : -depth * 7;
  const blur = depth < 0 ? 9 : depth * 2.2;
  el.style.transform = 'translate(-50%, calc(-50% + ' + lift + 'px)) scale(' + scale + ')';
  el.style.opacity = depth < 0 ? '0' : String(Math.max(0, 1 - depth * 0.34));
  el.style.filter = 'blur(' + blur + 'px)';
  el.style.zIndex = String(20 - Math.max(0, depth));
}

function _turnRestackCues(state) {
  // Snapshot: retiring splices the live array, and depth is read off the
  // order as it was when the pass started.
  const cues = state.transient.cues.slice();
  for (let i = 0; i < cues.length; i++) {
    const depth = cues.length - 1 - i;
    if (depth >= TURN_TRANSIENT_MAX_STACK) { _turnRetireCue(state, cues[i]); continue; }
    if (!cues[i].enter) _turnStyleCue(cues[i].el, depth);
  }
}

function _turnRetireCue(state, entry) {
  if (!entry) return;
  const index = state.transient.cues.indexOf(entry);
  if (index >= 0) state.transient.cues.splice(index, 1);
  if (entry.timer) clearTimeout(entry.timer);
  if (entry.enter) clearTimeout(entry.enter);
  entry.timer = null; entry.enter = null;
  if (entry.el && entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
}

function _turnStopTransient(state) {
  if (state.transient.coalesceTimer) clearTimeout(state.transient.coalesceTimer);
  state.transient.coalesceTimer = null;
  state.transient.pendingText = ''; state.transient.pendingKind = '';
  while (state.transient.cues.length) _turnRetireCue(state, state.transient.cues[0]);
}

function turnViewFinalize(data) {
  if (!turnViewIsSimplified()) return false;
  const state = _turnCurrentState(false); if (!state) return false;
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
  // Never build a block here: a turn that failed before producing a single row
  // has nothing to show, and an empty block is worse than none.
  const state = _turnCurrentState(false); if (!state) return false;
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
      _turnStopTransient(state); simplifiedTurns.delete(id);
      if (_turnOpen && _turnOpen.state === state) _turnOpen = null;
      return;
    }
  }
}
