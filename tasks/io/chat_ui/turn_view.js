// Conversation-scoped simplified turn presentation. Existing renderers create
// every durable node; this controller only reparents those canonical nodes.
const TURN_TEXT_COALESCE_MS = 300;
// A cue has no lifetime of its own: it holds the front of the stack until the
// next one arrives and pushes it back. Nothing on this surface disappears
// because a timer said so -- what the reader last saw stays readable until
// there is something newer to read.
const TURN_TRANSIENT_MAX_CHARS = 400;
const TURN_TRANSIENT_MAX_STACK = 4;
const TURN_ELAPSED_TICK_MS = 1000;
// The waiting state is a rain of glyphs behind the cues, and a cue arrives by
// condensing out of it: its own characters land one after another out of the
// same alphabet. 15 fps on a timer rather than a frame loop -- it is a
// background texture, it costs what it costs even when four turns run at once,
// and a timer is something a test can advance.
const TURN_RAIN_TICK_MS = 66;
const TURN_RAIN_COLUMN_PX = 12;
const TURN_SCRAMBLE_TICK_MS = 40;
const TURN_SCRAMBLE_TICKS = 14;
const TURN_SCRAMBLE_MAX_CHARS = 400;
const TURN_GLYPHS = 'アイウエオカキクケコサシスセソタチツテトナニヌネノabcdefghijklmnopqrstuvwxyz0123456789<>/\\{}[]()=+*-#$%&@';
// Every live rain canvas, driven by one shared ticker: N blocks must not mean
// N timers.
const _turnRainCanvases = new Set();
let _turnRainTimer = null;

let PAWFLOW_CHAT_VIEW_MODE = 'classic';
const simplifiedTurns = new Map();
const _turnUsers = new Map();
const _turnRuntime = new Map();
// Durable turn ids are authoritative when present and allow overlapping turns
// to keep receiving their own rows. Legacy/id-less traffic still uses the
// positional open turn and the next user message closes that positional turn.
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
  for (const state of simplifiedTurns.values()) { _turnStopTransient(state); _turnStopElapsed(state); }
  simplifiedTurns.clear();
  _turnUsers.clear();
  _turnRuntime.clear();
  _turnOpen = null;
}

function turnViewSetRuntimeTurns(turns) {
  _turnRuntime.clear();
  for (const turn of (turns || [])) {
    const id = _turnId(turn);
    if (id) _turnRuntime.set(id, turn);
  }
}

// A user message is a boundary: it closes whatever turn was open and opens the
// next one. Nothing here may fail on missing metadata -- this is the only thing
// the whole view is built on. The block itself is built on the turn's first
// row, so a user message nothing followed (a pending prompt, the tail of a
// page) is not given an empty one.
function turnViewRegisterUser(extra, element) {
  if (!turnViewIsSimplified() || !element) return;
  // A new user message ends the previous turn, whatever the server did or did
  // not send: leaving the old block on "working" with a ticking clock, above a
  // message the reader has already moved past, states something false. A turn
  // that already ended on an error or a stop keeps that status -- only a turn
  // still claiming to work is closed here.
  // Replaying an older history page must not stop the live turn currently on
  // screen. The DOM reconciliation below rebuilds positional boundaries once
  // the page is inserted; only a genuinely new live user message closes the
  // current state here.
  const suppliedId = _turnId(extra)
    || String((extra && extra.msg_id) || (element.dataset && element.dataset.msgid) || '').trim();
  if (_turnOpen && _turnOpen.state && !(extra && extra._history)) {
    const previous = _turnOpen.state;
    const canOverlap = previous.hasDurableTurnId || _turnRuntime.has(previous.turnId);
    if (!canOverlap) {
      _turnStopTransient(previous);
      if (previous.status === 'working') _turnUpdateStatus(previous, 'completed');
      _turnStopElapsed(previous);
    }
  }
  const id = suppliedId || ('turn-' + (++_turnSeq));
  element.dataset.turnId = id;
  const existing = _turnUsers.get(id);
  const record = {
    turnId: id, userEl: element, data: extra || {},
    state: existing ? existing.state : null,
  };
  _turnUsers.set(id, record);
  _turnOpen = record;
}

function _turnCreateState(turnId, userEl, data, anchorBeforeEl) {
  if (!userEl && !anchorBeforeEl) return null;
  const state = {
    turnId, userMsgId: (userEl && userEl.dataset.msgid) || turnId, userEl: userEl || null,
    agentName: '', llmService: '',
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
    + '<span class="simple-turn-elapsed" aria-hidden="true"></span>'
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
  state.elapsedEl = header.querySelector('.simple-turn-elapsed');
  const runtime = _turnRuntime.get(turnId) || {};
  const runtimeStarted = Number(runtime.started_at || 0) * 1000;
  const runtimeDuration = Number(runtime.duration || 0) * 1000;
  state.startedAt = runtimeStarted || (runtimeDuration ? Date.now() - runtimeDuration : Date.now());
  state.runtimeStatus = runtime.status || ''; state.elapsedTimer = null;
  // A stamped user row names the block, but only a correlated activity event
  // proves that late rows can be routed back here after another user boundary.
  state.hasDurableTurnId = false;
  simplifiedTurns.set(turnId, state);
  const record = _turnUsers.get(turnId);
  if (record) record.state = state;
  _turnUpdateIdentity(state, data || {}); _turnUpdateStatus(state, 'working');
  // A turn opened by the agent itself has no user row to sit under: its block
  // takes the place of the first row it is about to swallow.
  if (!userEl && anchorBeforeEl && anchorBeforeEl.parentNode) {
    anchorBeforeEl.parentNode.insertBefore(block, anchorBeforeEl);
  } else {
    _turnPlaceBlock(state);
  }
  _turnStartElapsed(state); _turnStartRain(state); _turnSyncIdle(state);
  return state;
}

// A stretch of activity with no user row above it -- a history window that
// opens mid-turn, work resumed after a turn the server already closed. The
// reader is owed a block for it exactly like any other turn.
function _turnOpenOrphanTurn(element, suppliedId, data) {
  if (!element || !element.parentNode) return null;
  const container = document.getElementById('messages');
  if (container && element.parentNode !== container) return null;
  const id = suppliedId || ('turn-' + (++_turnSeq));
  const state = _turnCreateState(id, null, data || {}, element);
  if (!state) return null;
  _turnOpen = { turnId: id, userEl: null, data: data || {}, state };
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

// How long the turn has been running, ticking while it runs and frozen at the
// moment it ends. It is the only thing on the header that keeps moving during
// a long silent stretch, which is precisely when the reader wonders whether
// anything is still happening.
function _turnFormatElapsed(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return total + 's';
  const mins = Math.floor(total / 60); const secs = total % 60;
  if (mins < 60) return mins + 'm ' + String(secs).padStart(2, '0') + 's';
  return Math.floor(mins / 60) + 'h ' + String(mins % 60).padStart(2, '0') + 'm';
}

function _turnRenderElapsed(state) {
  if (!state.elapsedEl) return;
  state.elapsedEl.textContent = _turnFormatElapsed(Date.now() - state.startedAt);
}

function _turnStartElapsed(state) {
  _turnRenderElapsed(state);
  if (state.elapsedTimer) return;
  state.elapsedTimer = setInterval(() => _turnRenderElapsed(state), TURN_ELAPSED_TICK_MS);
}

// Freeze, do not clear: the total a turn took is worth as much after the fact
// as during, and a reader scrolling back is entitled to it.
function _turnStopElapsed(state) {
  if (state.elapsedTimer) clearInterval(state.elapsedTimer);
  state.elapsedTimer = null;
  _turnRenderElapsed(state);
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
  if (status === 'working') _turnStartElapsed(state); else _turnStopElapsed(state);
}

function _turnSetExpanded(state, expanded) {
  state.expanded = !!expanded; state.blockEl.classList.toggle('expanded', state.expanded);
  state.headerEl.setAttribute('aria-expanded', state.expanded ? 'true' : 'false');
  state.headerEl.setAttribute('aria-label', _turnText(state.expanded ? 'collapseTurnDetails' : 'expandTurnDetails', state.expanded ? 'Collapse turn details' : 'Expand turn details'));
  if (state.expanded) { _turnStopTransient(state); _turnActivateTab(state, state.activeTab, false); }
  // Collapsing a turn that is still running brings its surface back: the rain
  // and the pulse are what say it is alive, and expanding to look at a tab must
  // not cost the reader that for the rest of the turn.
  else if (state.status === 'working') { _turnStartRain(state); _turnSyncIdle(state); }
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
  if (kind === 'tool_call' || kind === 'tool_result' || kind === 'sub_agent_trace') return 'tools';
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
  // A turn outlives its anchor. The user row can be moved, folded into a
  // technical group or evicted; what the reader sees is the block, and the
  // block is what decides whether the turn is still on screen. Requiring the
  // user row instead dropped every following row to top level -- the whole
  // view collapsing to a flat transcript because one element moved.
  if (!_turnOpen) return null;
  const anchor = (_turnOpen.state && _turnOpen.state.blockEl) || _turnOpen.userEl;
  if (!anchor || !anchor.isConnected) return null;
  if (!_turnOpen.state && create) {
    _turnOpen.state = _turnCreateState(_turnOpen.turnId, _turnOpen.userEl, _turnOpen.data);
  }
  return _turnOpen.state;
}

function _turnStateForData(data, element, create) {
  const id = _turnId(data);
  if (!id) return _turnCurrentState(create) || (create ? _turnOpenOrphanTurn(element) : null);
  let state = simplifiedTurns.get(id) || null;
  if (state && state.blockEl && state.blockEl.isConnected) {
    state.hasDurableTurnId = true;
    return state;
  }
  const record = _turnUsers.get(id);
  if (record) {
    if (!record.state && create) {
      record.state = _turnCreateState(id, record.userEl, record.data || data || {});
    }
    if (record.state) record.state.hasDurableTurnId = true;
    return record.state;
  }
  if (!create) return null;
  return _turnOpenOrphanTurn(element, id, data);
}

function turnViewIngest(kind, data, element) {
  if (!turnViewIsSimplified()) return false;
  // No open turn means the agent is working without a user row above it: a
  // history page that starts mid-turn, a provider that ended a turn and went
  // back to work, a delegate returning. That is still a turn, and its rows
  // still belong in a block -- top level holds user rows, blocks, and the last
  // message of a block, never activity.
  const state = _turnStateForData(data, element, true);
  if (!state || !state.blockEl.isConnected) return false;
  _turnUpdateIdentity(state, data || {});
  if (kind === 'tool_result' && turnViewHandleToolResult(data, element)) return true;
  const tabKey = _turnTabForKind(kind); const tab = state.tabs[tabKey];
  if (element) {
    const msgId = String((data && data.msg_id) || (element.dataset && element.dataset.msgid) || '');
    if (msgId) state.elementsByMsgId.set(msgId, element);
    const tcId = String((data && (data.tc_id || data.tool_call_id)) || (element.dataset && element.dataset.tcId) || '');
    if (tcId) state.toolElementsByCallId.set(tcId, element);
    // The visible answer is positional: the last message of a turn is the one
    // the reader is owed, and it lives *outside* the block. Deciding it from
    // the done payload instead left it inside whenever the server did not name
    // a final message -- with the block stuck on "working" and the answer
    // buried in a collapsed tab. A live message row takes the outside spot as
    // it arrives and hands it back to the block when a newer one appears.
    //
    // Replayed history is not live: an older page arrives *after* rows that
    // came before it, so "the last one ingested" is not "the last one of the
    // turn". Those rows are filed, and only what is marked final is promoted.
    //
    // A system notice -- compact finished, git history pruned -- is not the
    // agent's answer either. It goes in the record like everything else, but
    // it must never take the outside spot, or it displaces the very message
    // the reader came for.
    const claimed = data && data.turn_final
      && turnViewFinalize(Object.assign({}, data, { final_msg_id: msgId }));
    if (!claimed) {
      if (tabKey === 'messages' && kind !== 'system' && !(data && data._history)) _turnPromoteLast(state, element);
      else if (element.parentNode !== tab.bodyEl) tab.bodyEl.appendChild(element);
    }
  }
  if (state.expanded && state.activeTab !== tabKey) { tab.unread++; tab.tabEl.classList.add('has-unread'); }
  const text = data && (data.text || data.content || data.response || '');
  if (!(data && data._history)) _turnOfferTransient(state, tabKey, text, element);
  return true;
}

// The one place the outside spot changes hands. The row that held it goes back
// into the Messages tab -- appended, because it is the newest thing the tab has
// seen -- and the newcomer takes its place directly under the block.
function _turnPromoteLast(state, element) {
  if (!element) return;
  if (state.finalEl && state.finalEl !== element && state.tabs.messages) {
    state.tabs.messages.bodyEl.appendChild(state.finalEl);
  }
  state.finalEl = element;
  state.finalMsgId = String((element.dataset && element.dataset.msgid) || state.finalMsgId || '');
  const parent = state.blockEl.parentNode;
  if (parent && element.parentNode !== parent) parent.insertBefore(element, state.blockEl.nextSibling);
  else if (parent && state.blockEl.nextSibling !== element) parent.insertBefore(element, state.blockEl.nextSibling);
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

function _turnOfferTransient(state, kind, text, element) {
  if (state.expanded || state.status !== 'working') return;
  let label = String(text || '').replace(/\s+/g, ' ').trim();
  // A tool call shows itself. "Calling tool..." named neither the tool nor its
  // arguments, so the one surface meant to say what is happening said the
  // least at the moment most worth watching. The canonical row is already
  // rendered; the cue carries a copy of it, exactly as the classic view draws
  // it, and the original stays the tab's own.
  if (kind === 'tools') {
    if (element) { _turnEnqueueTransient(state, { kind, node: element }); return; }
    label = label || _turnText('turnCallingTool', 'Calling tool...');
  }
  if (!label) return; label = label.slice(0, TURN_TRANSIENT_MAX_CHARS);
  // Streaming text arrives token by token: hold the newest excerpt and emit
  // one cue per coalescing window instead of one per token. Tool and artifact
  // cues are discrete events and are queued immediately.
  if (kind === 'messages' || kind === 'thinking') {
    // One buffer per kind. Sharing a single one meant a message arriving in the
    // same window overwrote the reasoning that preceded it, and the thinking
    // the reader was told they would see never reached the surface at all.
    if (state.transient.pendingKind && state.transient.pendingKind !== kind) {
      _turnFlushTransient(state);
    }
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
  const body = document.createElement('span');
  const entry = { el: cue, enter: null, scramble: null };
  if (item.node) {
    // A copy, never the row itself: the original is the tab's canonical node
    // and moving it here would take it out of the record the reader opens.
    body.className = 'simple-turn-ephemeral-node';
    const clone = item.node.cloneNode(true);
    if (clone.removeAttribute) { clone.removeAttribute('id'); clone.removeAttribute('data-msgid'); }
    if (clone.classList) clone.classList.add('simple-turn-cue-copy');
    body.appendChild(clone);
    _turnScrambleNode(clone, entry);
  } else {
    body.className = 'simple-turn-ephemeral-text';
    _turnScrambleInto(body, item.text, entry);
  }
  cue.appendChild(icons); cue.appendChild(body);
  // Enter blurred and offset; the transition to depth 0 is the arrival. The
  // newest goes on top, so the reader's eye lands on it and follows the older
  // ones down as they fade.
  _turnStyleCue(cue, -1);
  // After the rain canvas, which stays at the back of the surface.
  state.ephemeralEl.insertBefore(cue, state.rainEl ? state.rainEl.nextSibling
                                                  : state.ephemeralEl.firstChild);
  // Restack rather than jump to the front: a cue that arrives during this
  // delay has already taken depth 0, and this one belongs behind it.
  entry.enter = setTimeout(() => { entry.enter = null; _turnRestackCues(state); }, 16);
  state.transient.cues.push(entry);
  _turnRestackCues(state);
  _turnSyncIdle(state);
}

// ── The rain, and the cues that condense out of it ──────────────────────

function _turnGlyph() {
  return TURN_GLYPHS.charAt(Math.floor(Math.random() * TURN_GLYPHS.length));
}

function _turnReducedMotion() {
  try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch (_e) { return false; }
}

// One timer for every canvas on the page. It stops itself the moment the last
// block finishes, so a quiet conversation costs nothing.
function _turnRainTick() {
  for (const canvas of Array.from(_turnRainCanvases)) {
    if (!canvas.isConnected) { _turnRainCanvases.delete(canvas); continue; }
    _turnRainDraw(canvas);
  }
  if (!_turnRainCanvases.size && _turnRainTimer) {
    clearInterval(_turnRainTimer); _turnRainTimer = null;
  }
}

function _turnRainDraw(canvas) {
  const ctx = canvas._pfCtx || (typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null);
  if (!ctx) return;
  canvas._pfCtx = ctx;
  const width = canvas.clientWidth || canvas.width || 0;
  const height = canvas.clientHeight || canvas.height || 0;
  if (!width || !height) return;
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width; canvas.height = height; canvas._pfDrops = null;
  }
  const columns = Math.max(1, Math.floor(width / TURN_RAIN_COLUMN_PX));
  if (!canvas._pfDrops || canvas._pfDrops.length !== columns) {
    canvas._pfDrops = Array.from({ length: columns },
      () => Math.random() * height / TURN_RAIN_COLUMN_PX);
  }
  // The trail: each frame paints the whole surface with a translucent wash, so
  // older glyphs decay instead of being erased.
  ctx.fillStyle = 'rgba(0, 0, 0, 0.14)';
  ctx.fillRect(0, 0, width, height);
  ctx.font = (TURN_RAIN_COLUMN_PX - 2) + 'px monospace';
  ctx.textBaseline = 'top';
  // Head bright, trail dimmed through globalAlpha rather than a second colour:
  // the accent arrives as whatever string the theme defines, and deriving a
  // faded variant from an arbitrary colour notation is not this code's job.
  const drops = canvas._pfDrops;
  ctx.fillStyle = canvas._pfHead || '#7cf';
  for (let i = 0; i < drops.length; i++) {
    const y = drops[i] * TURN_RAIN_COLUMN_PX;
    ctx.globalAlpha = 1;
    ctx.fillText(_turnGlyph(), i * TURN_RAIN_COLUMN_PX, y);
    ctx.globalAlpha = 0.5;
    ctx.fillText(_turnGlyph(), i * TURN_RAIN_COLUMN_PX, y - TURN_RAIN_COLUMN_PX);
    ctx.globalAlpha = 0.25;
    ctx.fillText(_turnGlyph(), i * TURN_RAIN_COLUMN_PX, y - TURN_RAIN_COLUMN_PX * 2);
    drops[i] = (y > height && Math.random() > 0.965) ? 0 : drops[i] + 1;
  }
  ctx.globalAlpha = 1;
}

// The colours are the theme's, read off the block itself: the rain belongs to
// the skin the user chose, it does not impose one.
function _turnRainColours(state, canvas) {
  let accent = '#7cf';
  try {
    const cs = getComputedStyle(state.blockEl);
    accent = (cs.getPropertyValue('--pf-accent') || '').trim()
      || (cs.getPropertyValue('--pf-accent-2') || '').trim() || accent;
  } catch (_e) { /* no computed style (tests): the default stands */ }
  canvas._pfHead = accent;
}

function _turnStartRain(state) {
  if (!state.ephemeralEl || state.rainEl || _turnReducedMotion()) return;
  const canvas = document.createElement('canvas');
  canvas.className = 'simple-turn-rain';
  canvas.setAttribute('aria-hidden', 'true');
  state.ephemeralEl.insertBefore(canvas, state.ephemeralEl.firstChild);
  state.rainEl = canvas;
  _turnRainColours(state, canvas);
  _turnRainCanvases.add(canvas);
  if (!_turnRainTimer) _turnRainTimer = setInterval(_turnRainTick, TURN_RAIN_TICK_MS);
}

function _turnStopRain(state) {
  if (!state.rainEl) return;
  _turnRainCanvases.delete(state.rainEl);
  if (state.rainEl.parentNode) state.rainEl.parentNode.removeChild(state.rainEl);
  state.rainEl = null;
  if (!_turnRainCanvases.size && _turnRainTimer) {
    clearInterval(_turnRainTimer); _turnRainTimer = null;
  }
}

// A cue does not appear, it condenses: every character starts as a glyph from
// the rain and resolves, left to right, into what it really is. The element
// always ends on the true text -- the scramble is decoration on top of a value
// that is set from the first tick, never a substitute for it.
function _turnScrambleInto(el, text, entry) {
  const full = String(text == null ? '' : text);
  if (_turnReducedMotion() || full.length > TURN_SCRAMBLE_MAX_CHARS) {
    el.textContent = full; return;
  }
  let tick = 0;
  const render = () => {
    const revealed = Math.ceil(full.length * (tick / TURN_SCRAMBLE_TICKS));
    let out = full.slice(0, revealed);
    for (let i = revealed; i < full.length; i++) {
      out += full.charAt(i) === ' ' ? ' ' : _turnGlyph();
    }
    el.textContent = out;
  };
  render();
  const timer = setInterval(() => {
    tick++;
    if (tick >= TURN_SCRAMBLE_TICKS) {
      clearInterval(timer);
      if (entry) entry.scramble = null;
      el.textContent = full;
      return;
    }
    render();
  }, TURN_SCRAMBLE_TICK_MS);
  if (entry) entry.scramble = timer;
}

// The same effect over a copied row: its text nodes are scrambled in place, so
// a tool call materialises as the call it is, rendered as the classic view
// draws it, rather than as a label describing one.
function _turnScrambleNode(root, entry) {
  if (_turnReducedMotion()) return;
  const texts = [];
  const walk = node => {
    for (const child of Array.from(node.childNodes || [])) {
      if (child.nodeType === 3) {
        const value = String(child.textContent || '');
        if (value.trim()) texts.push({ node: child, full: value });
      } else if (child.nodeType === 1) walk(child);
    }
  };
  walk(root);
  const total = texts.reduce((sum, t) => sum + t.full.length, 0);
  if (!texts.length || total > TURN_SCRAMBLE_MAX_CHARS) return;
  let tick = 0;
  const render = () => {
    const ratio = tick / TURN_SCRAMBLE_TICKS;
    for (const item of texts) {
      const revealed = Math.ceil(item.full.length * ratio);
      let out = item.full.slice(0, revealed);
      for (let i = revealed; i < item.full.length; i++) {
        out += /\s/.test(item.full.charAt(i)) ? item.full.charAt(i) : _turnGlyph();
      }
      item.node.textContent = out;
    }
  };
  render();
  const timer = setInterval(() => {
    tick++;
    if (tick >= TURN_SCRAMBLE_TICKS) {
      clearInterval(timer);
      if (entry) entry.scramble = null;
      for (const item of texts) item.node.textContent = item.full;
      return;
    }
    render();
  }, TURN_SCRAMBLE_TICK_MS);
  if (entry) entry.scramble = timer;
}

// The surface is never blank while the turn runs. Between two events -- and
// the gap can be minutes -- an empty band reads as a stall, so the waiting
// state is drawn as such: a pulse that says the turn is alive, standing in
// for the cue that has not arrived yet, and stepping aside as soon as one does.
function _turnSyncIdle(state) {
  if (!state.ephemeralEl) return;
  const busy = state.transient.cues.length > 0;
  if (busy) {
    if (state.idleEl && state.idleEl.parentNode) state.idleEl.parentNode.removeChild(state.idleEl);
    state.idleEl = null;
    return;
  }
  if (state.idleEl && state.idleEl.parentNode) return;
  const idle = document.createElement('span');
  idle.className = 'simple-turn-idle';
  idle.innerHTML = '<span class="simple-turn-idle-dot"></span><span class="simple-turn-idle-dot"></span>'
    + '<span class="simple-turn-idle-dot"></span>';
  const label = document.createElement('span');
  label.className = 'simple-turn-idle-label';
  label.textContent = _turnText('turnWorking', 'Working');
  idle.appendChild(label);
  state.ephemeralEl.appendChild(idle);
  state.idleEl = idle;
}

// depth 0 is the newest, at the top of the column. -1 is the pre-entry pose;
// anything past the last visible step is gone.
//
// A column, not a pile: cues used to share one spot and stack in depth, which
// works for four words and falls apart for a tool call rendered in full --
// three cues on the same square inch, each blurred through the others, and
// nothing readable. Here the newest arrives at the top at full strength and
// pushes the older ones down, fading as they go.
function _turnStyleCue(el, depth) {
  el.style.transform = depth < 0 ? 'translateY(-14px) scale(.96)' : 'translateY(0) scale(1)';
  el.style.opacity = depth < 0 ? '0' : String(Math.max(0.12, 1 - depth * 0.28));
  el.style.filter = depth < 0 ? 'blur(6px)' : 'blur(' + (depth * 1.1) + 'px)';
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
  _turnSyncIdle(state);
}

function _turnRetireCue(state, entry) {
  if (!entry) return;
  const index = state.transient.cues.indexOf(entry);
  if (index >= 0) state.transient.cues.splice(index, 1);
  if (entry.enter) clearTimeout(entry.enter);
  if (entry.scramble) clearInterval(entry.scramble);
  entry.enter = null; entry.scramble = null;
  if (entry.el && entry.el.parentNode) entry.el.parentNode.removeChild(entry.el);
}

function _turnStopTransient(state) {
  if (state.transient.coalesceTimer) clearTimeout(state.transient.coalesceTimer);
  state.transient.coalesceTimer = null;
  state.transient.pendingText = ''; state.transient.pendingKind = '';
  while (state.transient.cues.length) _turnRetireCue(state, state.transient.cues[0]);
  if (state.idleEl && state.idleEl.parentNode) state.idleEl.parentNode.removeChild(state.idleEl);
  state.idleEl = null;
  _turnStopRain(state);
}

function turnViewFinalize(data) {
  if (!turnViewIsSimplified()) return false;
  const state = _turnStateForData(data, null, false); if (!state) return false;
  const finalId = String((data && (data.final_msg_id || data.msg_id)) || '');
  // A derived marker is a reconstruction guess, not an authority: pagination
  // classifies each page on its own, so an older page can derive a second
  // final for a turn whose real answer is already placed. Never let a guess
  // displace a final that is already established.
  if (state.finalEl && state.finalMsgId && state.finalMsgId !== finalId
      && data && data.turn_final_derived) return false;
  let element = finalId ? state.elementsByMsgId.get(finalId) : null;
  if (!element && finalId) element = document.querySelector('#messages [data-msgid="' + CSS.escape(finalId) + '"]');
  // A named final only moves the outside spot when the server names a row the
  // positional rule did not already put there. Closing the turn does not
  // depend on it: a done that names nothing still ends the block, or the
  // header stays on "working" over a turn that is visibly finished.
  if (element) { element.classList.remove('streaming'); _turnPromoteLast(state, element); }
  if (finalId) state.finalMsgId = finalId;
  _turnStopTransient(state); _turnUpdateStatus(state, 'completed'); _turnPlaceBlock(state);
  return true;
}

function turnViewFail(turnId, status, message) {
  if (!turnViewIsSimplified()) return false;
  // Never build a block here: a turn that failed before producing a single row
  // has nothing to show, and an empty block is worse than none.
  const state = turnId ? (simplifiedTurns.get(String(turnId)) || null)
    : _turnCurrentState(false);
  if (!state) return false;
  if (state.status === 'completed') return false;
  _turnStopTransient(state); _turnUpdateStatus(state, ['stopped', 'cancelled', 'error'].includes(status) ? status : 'error');
  if (message) _turnOfferTransient(state, 'messages', message);
  return true;
}

// Rows that are activity: they belong inside a block, never at top level.
// Everything else at top level is left alone -- an approval, a question, an
// error or a notification is something the reader must act on, and burying it
// in a collapsed block would hide it.
const TURN_FILABLE_ROLES = new Set([
  'assistant', 'agent-result', 'thinking', 'tool_call', 'tool_result',
  'system', 'sub_agent_trace',
]);

function _turnRowRole(el) {
  return (el && el.dataset && el.dataset.messageRole) || '';
}

function _turnIsUserRow(el) {
  return _turnRowRole(el) === 'user';
}

// ── The display rule, enforced on the DOM ──────────────────────────────────
// Top level is this, repeated, and nothing else:
//
//     USER > BLOCK > the block's last message
//
// The event path already builds it as rows arrive. This pass is what makes it
// true *whatever happened* -- a reload, a page of older history, a row created
// by a path that never called the view, an anchor that moved into a technical
// group. It reads the DOM rather than the event stream, so it cannot be wrong
// about what is actually on screen: every stray row is filed into the block of
// the turn it falls in, and a stretch of activity with no user row above it
// gets a block of its own.
function turnViewReconcile() {
  if (!turnViewIsSimplified()) return;
  const container = document.getElementById('messages');
  if (!container) return;
  const touched = new Set();
  let state = null;
  // What the live path must carry on into once the pass is over: the last turn
  // on screen. Leaving it on whatever the previous render left behind is how a
  // reload ends up filing new rows into a turn that scrolled away -- or into
  // none at all.
  let open = null;
  for (const el of Array.from(container.children)) {
    if (!el || !el.dataset || !el.classList) continue;
    if (el.classList.contains('simple-turn-block')) {
      const owner = simplifiedTurns.get(el.dataset.turnId || '');
      if (owner) {
        state = owner; touched.add(owner);
        open = { turnId: owner.turnId, userEl: owner.userEl, data: {}, state: owner };
      }
      continue;
    }
    if (_turnIsUserRow(el)) {
      // A user boundary seen in the final chronological DOM closes the turn
      // before it. This also settles a historical page that ended mid-turn,
      // without touching the newest live turn (nothing follows that one).
      if (state && state.status === 'working' && !_turnRuntime.has(state.turnId)) {
        _turnStopTransient(state);
        _turnUpdateStatus(state, 'completed');
      }
      // A user row closes whatever came before and opens the next turn. Its
      // block is built by the first row that follows: a user message nothing
      // followed is not given an empty one.
      const id = el.dataset.turnId || el.dataset.msgid || ('turn-' + (++_turnSeq));
      el.dataset.turnId = id;
      const existing = simplifiedTurns.get(id);
      if (existing && existing.blockEl.isConnected) {
        state = existing; touched.add(existing);
        open = { turnId: id, userEl: el, data: {}, state: existing };
      } else {
        state = null;
        open = { turnId: id, userEl: el, data: {}, state: null };
      }
      _turnUsers.set(id, open);
      _turnOpen = open;
      continue;
    }
    if (!TURN_FILABLE_ROLES.has(_turnRowRole(el))) continue;
    if (state && el === state.finalEl) continue;
    if (!state) {
      state = (_turnOpen && _turnOpen.userEl && _turnOpen.userEl.isConnected)
        ? _turnCurrentState(true) : _turnOpenOrphanTurn(el);
      if (!state) continue;
      touched.add(state);
      open = _turnOpen;
      if (el.parentNode !== container) continue;  // the block took its place
    }
    touched.add(state);
    _turnFileRow(state, el);
  }
  if (open) _turnOpen = open;
  for (const s of touched) {
    // Every block owes the reader its last message, under it. A live row takes
    // that spot as it arrives; a replayed turn has to be given it here, or a
    // reload shows a block with the answer buried inside it.
    if (!_turnRuntime.has(s.turnId) && (!s.finalEl || !s.finalEl.isConnected)) {
      _turnPromoteRecordedLast(s);
    }
    _turnPlaceBlock(s);
    if (s.finalEl && s.blockEl.parentNode) s.blockEl.parentNode.insertBefore(s.finalEl, s.blockEl.nextSibling);
  }
}

function turnViewHydrateRuntimeTurns() {
  if (!turnViewIsSimplified()) return;
  for (const [turnId, runtime] of _turnRuntime.entries()) {
    const state = simplifiedTurns.get(turnId);
    if (!state || !state.blockEl.isConnected) continue;
    const startedAt = Number(runtime.started_at || 0) * 1000;
    const duration = Number(runtime.duration || 0) * 1000;
    state.startedAt = startedAt || (duration ? Date.now() - duration : state.startedAt);
    state.runtimeStatus = runtime.status || 'running';
    state.blockEl.dataset.runtimeStatus = state.runtimeStatus;
    _turnUpdateIdentity(state, runtime);
    _turnUpdateStatus(state, 'working');
    _turnStartRain(state);
    _turnSyncIdle(state);
    const preview = String(runtime.message_preview || '').trim();
    if (preview && !state.transient.cues.length) {
      _turnOfferTransient(state, 'messages', preview);
      _turnFlushTransient(state);
    }
  }
}

// The last thing the turn said, taken from its own record. Only consulted when
// nothing holds the spot below the block: a final the server named wins.
function _turnPromoteRecordedLast(state) {
  const tab = state.tabs && state.tabs.messages;
  if (!tab) return;
  const rows = Array.from(tab.bodyEl.children);
  for (let i = rows.length - 1; i >= 0; i--) {
    const el = rows[i];
    if (_turnRowRole(el) === 'system') continue;
    if (!String((el.dataset && el.dataset.rawText) || '').trim()) continue;
    _turnPromoteLast(state, el);
    return;
  }
}

// File one stray row into its turn, in the tab its role belongs to. The last
// message of the turn is hoisted back out immediately after: the block's own
// answer is the one thing that sits below it.
function _turnFileRow(state, el) {
  const role = _turnRowRole(el);
  // A delegate box is a tool call by another name -- it is what the `delegate`
  // and `flash_delegate` calls draw -- so it lives with the tool calls.
  const tabKey = _turnTabForKind(role === 'sub_agent_trace' ? 'tool_call' : role);
  const tab = state.tabs[tabKey];
  if (!tab) return;
  const msgId = (el.dataset && el.dataset.msgid) || '';
  if (msgId) state.elementsByMsgId.set(msgId, el);
  if (tabKey === 'messages' && role !== 'system' && String(el.dataset.rawText || '').trim()) {
    _turnPromoteLast(state, el);
    return;
  }
  if (el.parentNode !== tab.bodyEl) tab.bodyEl.appendChild(el);
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
      _turnStopTransient(state); _turnStopElapsed(state); simplifiedTurns.delete(id);
      if (_turnOpen && _turnOpen.state === state) _turnOpen = null;
      return;
    }
  }
}
