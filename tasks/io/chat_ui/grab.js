/** Grab mode — the chat composer types straight into the agent's live tmux.
 *
 * When the selected agent runs on an interactive CLI provider and its tmux is
 * up, "grab" turns the composer into a direct input to that terminal: what you
 * type lands in the TUI exactly as if you were attached to it. Releasing it
 * puts the composer back on the normal /api/agent path.
 *
 * Two rules make this a real conversation turn rather than a blind write:
 *
 * 1. It goes through the terminal WebSocket (`terminal_input`, raw bytes into
 *    the PTY) and NEVER through the pool's send_text(). send_text() records a
 *    SHA-256 ticket in injected_prompts.jsonl whose whole purpose is to stop
 *    the UserPromptSubmit hook from mirroring the prompt into the
 *    conversation — correct for a PawFlow-injected prompt, exactly wrong for
 *    one a human typed. Typed here, the hook mirrors it as a channel="tmux"
 *    user message and the MITM captures the answer, so the turn shows up in
 *    the chat on its own. That is also why nothing is echoed locally: the
 *    hook is the one that files it, and echoing would double it.
 *
 * 2. A multiline prompt is sent as a bracketed paste, then Enter — the same
 *    paste-then-submit discipline the pool uses. Writing raw newlines into a
 *    TUI composer is what unfolds one prompt into several submissions.
 */

// Providers whose tmux `open_cc_interactive_terminal` knows how to attach.
const _GRAB_PROVIDERS = ['claude-code-interactive', 'codex-interactive'];
// Newline inside the TUI's own composer. Codex, Claude Code and Antigravity
// all break the line on Ctrl+Enter, which modern terminals encode as the CSI u
// sequence below -- the same one PawCode binds on its own prompt (see
// pawflow_cli/app.py). Forwarding the key beats assembling a multiline block
// here: the newline is made by the TUI, in its composer, exactly as it would
// be for a human at the keyboard.
const _GRAB_CTRL_ENTER = '\x1b[13;5u';
const _GRAB_SHIFT_ENTER = '\x1b[13;2u';
// A block that arrives already multiline did not come from the keyboard -- it
// was pasted into the composer. Bracketed paste is how a terminal says "this
// is one paste", so the TUI collapses it into a chip instead of reading each
// newline as a submission.
const _GRAB_PASTE_START = '\x1b[200~';
const _GRAB_PASTE_END = '\x1b[201~';
// Let the TUI finish ingesting a paste before Enter. Same reason the pool has
// _PASTE_SETTLE_DEFAULT: an Enter inside the paste window is swallowed as a
// newline instead of submitting.
const _GRAB_SUBMIT_DELAY_MS = 400;
// How stale the live-session set may get. It only drives button visibility;
// the authoritative answer is the open call itself.
const _GRAB_LIVE_TTL_MS = 5000;

let _grab = {
  on: false,
  ws: null,
  sessionId: '',
  agent: '',
  provider: '',
  connecting: false,
  liveAgents: {},      // agent name → provider, from list_cc_interactive_terminals
  liveCheckedAt: 0,
  liveInFlight: false,
};

function grabActive() {
  return !!(_grab.on && _grab.ws && _grab.ws.readyState === 1);
}

/** The interactive provider of `agentName`, or '' if grab cannot apply. */
function _grabProviderFor(agentName) {
  if (!agentName) return '';
  const provider = (typeof _agentLlmProvider === 'function')
    ? _agentLlmProvider(agentName) : '';
  return _GRAB_PROVIDERS.indexOf(provider) === -1 ? '' : provider;
}

/** Refresh the set of agents with a live tmux, throttled. */
function _grabRefreshLive() {
  const now = Date.now();
  if (_grab.liveInFlight) return;
  if (now - _grab.liveCheckedAt < _GRAB_LIVE_TTL_MS) return;
  if (typeof conversationId === 'undefined' || !conversationId) return;
  _grab.liveInFlight = true;
  action$('list_cc_interactive_terminals').subscribe({
    next: data => {
      _grab.liveInFlight = false;
      _grab.liveCheckedAt = Date.now();
      const live = {};
      const sessions = (data && Array.isArray(data.sessions)) ? data.sessions : [];
      for (const s of sessions) {
        if (s && s.agent_name) live[String(s.agent_name)] = String(s.provider || '');
      }
      const had = Object.keys(_grab.liveAgents).join(',');
      _grab.liveAgents = live;
      if (had !== Object.keys(live).join(',')) _grabRenderButton();
      // The session we are holding just went away (restart, sweep, kill).
      if (_grab.on && _grab.agent && !live[_grab.agent]) releaseGrab(true);
    },
    error: () => {
      _grab.liveInFlight = false;
      _grab.liveCheckedAt = Date.now();
    },
  });
}

/** Show/hide and re-style the grab button. Called from the active-agent poll. */
function updateGrabButton() {
  _grabRefreshLive();
  _grabRenderButton();
}

function _grabRenderButton() {
  const btn = document.getElementById('grabBtn');
  if (!btn) return;
  const agent = (typeof selectedAgent !== 'undefined' && selectedAgent) ? selectedAgent : '';
  const provider = _grabProviderFor(agent);
  const live = !!(provider && _grab.liveAgents[agent]);
  btn.style.display = live ? '' : 'none';
  if (!live && _grab.on) releaseGrab(true);
  btn.classList.toggle('on', !!_grab.on);
  btn.title = _grab.on ? t('grabOnTitle', { agent: agent })
                       : t('grabTitle', { agent: agent });
  const area = document.querySelector('.input-area');
  if (area) area.classList.toggle('grab-on', !!_grab.on);
  const input = document.getElementById('input');
  if (input) {
    if (_grab.on) {
      if (!input._grabPrevPlaceholder) input._grabPrevPlaceholder = input.placeholder;
      input.placeholder = t('grabPlaceholder', { agent: _grab.agent || agent });
    } else if (input._grabPrevPlaceholder) {
      input.placeholder = input._grabPrevPlaceholder;
      input._grabPrevPlaceholder = '';
    }
  }
}

function toggleGrab() {
  if (_grab.on || _grab.connecting) { releaseGrab(false); return; }
  const agent = (typeof selectedAgent !== 'undefined' && selectedAgent) ? selectedAgent : '';
  const provider = _grabProviderFor(agent);
  if (!agent || !provider) { addMsg('system', t('grabNoLive')); return; }
  _grab.connecting = true;
  const size = (typeof _estimateTerminalSize === 'function')
    ? _estimateTerminalSize() : { cols: 220, rows: 50 };
  action$('open_cc_interactive_terminal', {
    agent_name: agent,
    service_id: (typeof _agentLlmService === 'function') ? _agentLlmService(agent) : '',
    provider: provider,
    cols: size.cols,
    rows: size.rows,
  }).subscribe({
    next: resp => {
      _grab.connecting = false;
      if (!resp || resp.error) {
        addMsg('system', '\u26a0 ' + ((resp && resp.error) || t('grabNoLive')));
        return;
      }
      if (!resp.token) { addMsg('system', t('terminalMissingToken')); return; }
      _grabOpenSocket(agent, provider, resp.session_id, resp.token);
    },
    error: e => {
      _grab.connecting = false;
      addMsg('system', t('grabFailed', { error: (e && e.message) || String(e) }));
    },
  });
}

function _grabOpenSocket(agent, provider, sessionId, token) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(
    proto + '//' + location.host + '/terminal/' + sessionId + '/' + token);
  _grab.ws = ws;
  _grab.sessionId = sessionId;
  _grab.agent = agent;
  _grab.provider = provider;
  ws.onopen = () => {
    _grab.on = true;
    _grabRenderButton();
    addMsg('system', t('grabOn', { agent: agent }));
    const input = document.getElementById('input');
    if (input) input.focus();
  };
  // Grab is write-only: the terminal tab is where you watch the pane, and the
  // conversation is where the turn shows up. Incoming PTY data is dropped on
  // purpose rather than rendered into the chat.
  ws.onmessage = () => {};
  ws.onclose = () => { if (_grab.on) releaseGrab(true); };
  ws.onerror = () => { if (_grab.on) releaseGrab(true); };
}

/** Leave grab mode. `silent` when the session went away on its own. */
function releaseGrab(silent) {
  const was = _grab.on || _grab.connecting;
  const agent = _grab.agent;
  _grab.on = false;
  _grab.connecting = false;
  if (_grab.ws) {
    try { _grab.ws.close(); } catch (e) { /* already gone */ }
  }
  _grab.ws = null;
  _grab.sessionId = '';
  _grab.agent = '';
  _grab.provider = '';
  _grabRenderButton();
  if (was && !silent) addMsg('system', t('grabOff', { agent: agent }));
}

function _grabWrite(data) {
  if (!grabActive() || !data) return false;
  _grab.ws.send(JSON.stringify({
    type: 'terminal_input',
    data: _terminalInputB64(data),
  }));
  return true;
}

/** Push what is in the composer into the TUI and clear it.
 *
 * Returns true when the text went over as a paste, which the TUI needs a
 * moment to ingest before it will accept a key.
 */
function _grabFlush(input) {
  const box = input || document.getElementById('input');
  if (!box) return false;
  // Trailing whitespace only; leading whitespace can be meaningful in a shell.
  const text = box.value.replace(/\s+$/, '');
  box.value = '';
  box.style.height = 'auto';
  if (!text) return false;
  const pasted = text.indexOf('\n') !== -1;
  if (pasted) {
    // Already multiline, so it was pasted in rather than typed. One bracketed
    // paste: raw newlines would each read as a submission and the prompt would
    // arrive in pieces.
    _grabWrite(_GRAB_PASTE_START + text + _GRAB_PASTE_END);
  } else {
    _grabWrite(text);
  }
  return pasted;
}

/** Send the composer's contents to the TUI and submit. Called from send(). */
function grabSend() {
  const input = document.getElementById('input');
  if (!input) return;
  const text = input.value.replace(/\s+$/, '');
  if (text && typeof messageHistory !== 'undefined') {
    messageHistory.unshift(text);
    if (messageHistory.length > 50) messageHistory.pop();
    localStorage.setItem('pawflow_msg_history',
                         JSON.stringify(messageHistory.slice(0, 50)));
  }
  if (typeof historyIndex !== 'undefined') historyIndex = -1;
  const pasted = _grabFlush(input);
  // Enter on an empty composer is still meaningful when the TUI already holds
  // lines the user broke with Ctrl+Enter -- that is what submits them.
  if (pasted) setTimeout(() => _grabWrite('\r'), _GRAB_SUBMIT_DELAY_MS);
  else _grabWrite('\r');
  input.focus();
}

/** Composer keys while grabbed. Returns true when the event was consumed. */
function grabHandleKey(e) {
  if (!grabActive()) return false;
  const input = e.target;
  // Esc goes to the TUI — that is the whole point of holding it (Codex draws
  // "Esc to interrupt"). The chat's own Esc-to-interrupt is unreachable while
  // grabbed, which is correct: you are driving the TUI, not the agent loop.
  if (e.key === 'Escape') {
    e.preventDefault();
    _grabWrite('\x1b');
    return true;
  }
  // Ctrl+Enter and Shift+Enter are newline in Codex, Claude Code and
  // Antigravity alike. Grabbed, they are FORWARDED rather than applied here:
  // whatever is in the composer goes over first, then the key, so the line
  // break happens in the TUI's own composer. That is the difference between
  // driving the terminal and assembling a block to shove into it.
  if (e.key === 'Enter' && (e.shiftKey || e.ctrlKey || e.metaKey || e.altKey)) {
    e.preventDefault();
    _grabFlush(input);
    _grabWrite(e.shiftKey && !e.ctrlKey ? _GRAB_SHIFT_ENTER : _GRAB_CTRL_ENTER);
    return true;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    grabSend();
    return true;
  }
  // Ctrl+C interrupts the TUI — unless there is a selection, where the user
  // means copy. Cmd+C on macOS is always copy.
  if (e.ctrlKey && !e.metaKey && (e.key === 'c' || e.key === 'C')) {
    if (input.selectionStart !== input.selectionEnd) return false;
    e.preventDefault();
    _grabWrite('\x03');
    return true;
  }
  return false;
}

// A conversation or agent switch invalidates the held session.
function grabOnConversationSwitch() {
  _grab.liveAgents = {};
  _grab.liveCheckedAt = 0;
  if (_grab.on || _grab.connecting) releaseGrab(true);
  _grabRenderButton();
}

function grabOnAgentSwitch() {
  if ((_grab.on || _grab.connecting) && _grab.agent
      && _grab.agent !== (typeof selectedAgent !== 'undefined' ? selectedAgent : '')) {
    releaseGrab(true);
  }
  _grabRenderButton();
}

window._grabState = _grab;
