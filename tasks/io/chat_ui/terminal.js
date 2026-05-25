// ── Terminal + Code-server (xterm.js / iframe via tabs) ──
// /terminal [relay_name] — open a new terminal tab
// /terminal close       — close current terminal tab
// /code [relay_name]    — open code-server tab
// /code close           — close code-server tab

let _xtermLoaded = false;

/** Load xterm.js + fit addon from CDN (once). */
function _loadXterm() {
  if (_xtermLoaded) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css';
    document.head.appendChild(link);
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js';
    script.onload = () => {
      const fit = document.createElement('script');
      fit.src = 'https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js';
      fit.onload = () => { _xtermLoaded = true; resolve(); };
      fit.onerror = reject;
      document.head.appendChild(fit);
    };
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

/** Get all connected relays. */
/** Get relays linked to the current conversation (with details from list_resources cache). */
function _getRelays() {
  return new Promise((resolve, reject) => {
    action$('list_resources').subscribe({
      next: data => {
        var rb = data.relay_bindings || { linked: {}, default: {}, details: {} };
        var linked = rb.linked || {};
        var details = rb.details || {};
        // Collect unique relay IDs across all scopes
        var seen = {};
        var relays = [];
        Object.keys(linked).forEach(function(scope) {
          (linked[scope] || []).forEach(function(rid) {
            if (seen[rid]) return;
            seen[rid] = true;
            var det = details[rid] || {};
            relays.push({
              id: rid,
              connected: det.connected !== false,
              root: det.root || '',
              host_root: det.host_root || '',
              allow_local: det.allow_local || false,
            });
          });
        });
        resolve(relays.filter(r => r.connected));
      },
      error: e => reject(e),
    });
  });
}

/** Pick a relay: auto if 1, dialog if multiple, null if none. */
function _pickRelay(relays) {
  if (!relays.length) return Promise.resolve(null);
  if (relays.length === 1) return Promise.resolve(relays[0].id);
  return new Promise(resolve => {
    const bg = document.createElement('div');
    bg.className = 'exec-overlay';
    bg.innerHTML = '<div class="exec-dialog" style="min-width:320px;">'
      + '<h3>' + escapeHtml(t('chooseRelay')) + '</h3>'
      + '<div id="_relayPickList" style="margin:12px 0;"></div>'
      + '<div class="exec-btns"><button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button></div>'
      + '</div>';
    const list = bg.querySelector('#_relayPickList');
    for (const r of relays) {
      const btn = document.createElement('button');
      btn.className = 'exec-approve';
      btn.style.cssText = 'display:block;width:100%;margin-bottom:6px;text-align:left;';
      var label = r.id;
      if (r.host_root) label += ' \u2014 ' + r.host_root;
      else if (r.root) label += ' \u2014 ' + r.root;
      btn.textContent = label;
      btn.onclick = () => { bg.remove(); resolve(r.id); };
      list.appendChild(btn);
    }
    document.body.appendChild(bg);
  });
}

/** Pick docker/local mode for a relay that supports both. Returns 'docker', 'local', or null (cancelled). */
function _pickMode(relayId) {
  return new Promise(resolve => {
    const bg = document.createElement('div');
    bg.className = 'exec-overlay';
    bg.innerHTML = '<div class="exec-dialog" style="min-width:280px;">'
      + '<h3>' + escapeHtml(t('executionMode')) + '</h3>'
      + '<div style="margin:12px 0;color:#aaa;">' + escapeHtml(t('relaySupportsBothModes', { relay: relayId })).replace(escapeHtml(relayId), '<b>' + escapeHtml(relayId) + '</b>') + '</div>'
      + '<div class="exec-btns" style="flex-direction:column;gap:8px;">'
      + '<button class="exec-approve" style="width:100%;" onclick="this.closest(\'.exec-overlay\').remove();window._pickModeResolve(\'docker\');">\u{1F433} ' + escapeHtml(t('dockerSandboxed')) + '</button>'
      + '<button class="exec-approve" style="width:100%;background:#4ecdc4;" onclick="this.closest(\'.exec-overlay\').remove();window._pickModeResolve(\'local\');">\u{1F4BB} ' + escapeHtml(t('localHostMachine')) + '</button>'
      + '<button class="exec-deny" style="width:100%;" onclick="this.closest(\'.exec-overlay\').remove();window._pickModeResolve(null);">' + escapeHtml(t('contextCancel')) + '</button>'
      + '</div></div>';
    window._pickModeResolve = resolve;
    document.body.appendChild(bg);
  });
}

function _listCCInteractiveTerminals() {
  return new Promise((resolve, reject) => {
    action$('list_cc_interactive_terminals').subscribe({
      next: data => resolve(Array.isArray(data.sessions) ? data.sessions : []),
      error: e => reject(e),
    });
  });
}

function _pickCCInteractiveTerminal(sessions) {
  if (!sessions.length) return Promise.resolve(null);
  if (sessions.length === 1) return Promise.resolve(sessions[0]);
  return new Promise(resolve => {
    const bg = document.createElement('div');
    bg.className = 'exec-overlay';
    bg.innerHTML = '<div class="exec-dialog" style="min-width:360px;">'
      + '<h3>' + escapeHtml(t('chooseCCInteractiveTerminal')) + '</h3>'
      + '<div id="_cciTermPickList" style="margin:12px 0;"></div>'
      + '<div class="exec-btns"><button class="exec-deny" id="_cciTermCancel">' + escapeHtml(t('contextCancel')) + '</button></div>'
      + '</div>';
    const list = bg.querySelector('#_cciTermPickList');
    bg.querySelector('#_cciTermCancel').onclick = () => { bg.remove(); resolve(null); };
    for (const session of sessions) {
      const btn = document.createElement('button');
      btn.className = 'exec-approve';
      btn.style.cssText = 'display:block;width:100%;margin-bottom:6px;text-align:left;';
      const bits = [session.agent_name || ''];
      if (session.service_id) bits.push(session.service_id);
      if (session.container_name) bits.push(session.container_name);
      btn.textContent = bits.filter(Boolean).join(' - ');
      btn.onclick = () => { bg.remove(); resolve(session); };
      list.appendChild(btn);
    }
    document.body.appendChild(bg);
  });
}

function _agentLlmService(agentName) {
  const name = String(agentName || '').toLowerCase();
  const data = (typeof _lastResourcesData !== 'undefined' && _lastResourcesData) ? _lastResourcesData : null;
  const agents = (data && Array.isArray(data.agents)) ? data.agents : [];
  const agent = agents.find(a => String(a.name || '').toLowerCase() === name);
  return agent && agent.llm_service ? String(agent.llm_service) : '';
}

function _llmProviderForService(serviceId) {
  const id = String(serviceId || '');
  const data = (typeof _lastResourcesData !== 'undefined' && _lastResourcesData) ? _lastResourcesData : null;
  const services = (data && Array.isArray(data.services)) ? data.services : [];
  const svc = services.find(s => String(s.service_id || '') === id);
  return svc && svc.provider ? String(svc.provider) : '';
}

function _agentLlmProvider(agentName) {
  return _llmProviderForService(_agentLlmService(agentName));
}

/** /terminal command */
async function cmdTerminal(text, parts) {
  const sub = (parts[1] || '').toLowerCase();

  if (sub === 'close') {
    // Close the currently active terminal tab
    if (_activeTab && _activeTab.startsWith('term-')) {
      closeTerminalTab(_activeTab);
      addMsg('system', t('terminalClosed'));
    } else {
      addMsg('system', t('noActiveTerminal'));
    }
    return true;
  }

  let relayId = parts[1] || '';
  let localMode = false;
  if (sub === 'local') { localMode = true; relayId = parts[2] || ''; }
  else if (sub === 'docker') { relayId = parts[2] || ''; }

  var allRelays = [];
  if (!relayId) {
    try {
      allRelays = await _getRelays();
      relayId = await _pickRelay(allRelays);
      if (!relayId) {
        addMsg('system', t('terminalNoRelayUsage'));
        return true;
      }
    } catch (e) {
      addMsg('system', t('failedToListRelays', { error: e.message }));
      return true;
    }
  }
  // Offer docker/local choice if relay supports it and not already specified
  if (!localMode && sub !== 'docker') {
    if (!allRelays.length) try { allRelays = await _getRelays(); } catch(e) {}
    var relay = allRelays.find(r => r.id === relayId);
    if (relay && relay.allow_local) {
      var mode = await _pickMode(relayId);
      if (mode === null) return true;
      localMode = mode === 'local';
    }
  }

  addMsg('system', t('openingTerminalOn', { relay: relayId, mode: localMode ? ' (' + t('local') + ')' : '' }));

  const termSize = _estimateTerminalSize();
  action$('open_terminal', { relay_id: relayId, cols: termSize.cols, rows: termSize.rows, local: localMode }).subscribe({
    next: async (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }

      const sessionId = resp.session_id;
      const token = resp.token || '';
      if (!token) {
        addMsg('system', t('terminalMissingToken'));
        return;
      }
      await _loadXterm();

      // Create tab and init xterm inside it
      const tabId = addTerminalTab(sessionId, relayId);
      const panel = document.getElementById('tabContent_' + tabId);
      const container = panel.querySelector('.xterm-container');
      _initXterm(container, sessionId, token);
    },
    error: (e) => {
      addMsg('system', t('failedToOpenTerminal', { error: e.message }));
    },
  });
  return true;
}

/** Open the live tmux session for the selected interactive agent. */
async function cmdAgentTmux(agentName) {
  const targetAgent = agentName || (typeof selectedAgent !== 'undefined' ? selectedAgent : '') || '';
  if (!targetAgent) {
    addMsg('system', t('ccInteractiveTerminalNoAgent'));
    return true;
  }
  const serviceId = _agentLlmService(targetAgent);
  const provider = _llmProviderForService(serviceId);
  if (provider === 'antigravity-interactive') {
    return _openAntigravityAgentTmux(targetAgent, serviceId);
  }
  if (provider !== 'claude-code-interactive') {
    addMsg('system', t('ccInteractiveTerminalNoLive'));
    return true;
  }
  return _openCCInteractiveAgentTmux(targetAgent, serviceId);
}

async function cmdCCInteractiveTerminal(agentName) {
  return cmdAgentTmux(agentName);
}

async function _openCCInteractiveAgentTmux(targetAgent, serviceId) {
  const termSize = _estimateTerminalSize();
  addMsg('system', t('openingCCInteractiveTerminal', { agent: targetAgent }));
  action$('open_cc_interactive_terminal', {
    agent_name: targetAgent,
    service_id: serviceId || '',
    cols: termSize.cols,
    rows: termSize.rows,
  }).subscribe({
    next: async (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }
      const sessionId = resp.session_id;
      const token = resp.token || '';
      if (!token) {
        addMsg('system', t('terminalMissingToken'));
        return;
      }
      await _loadXterm();
      const tabId = addTerminalTab(sessionId, resp.relay_id || ('cc:' + targetAgent));
      const panel = document.getElementById('tabContent_' + tabId);
      const container = panel.querySelector('.xterm-container');
      _initXterm(container, sessionId, token);
    },
    error: (e) => {
      addMsg('system', t('failedToOpenTerminal', { error: e.message }));
    },
  });
  return true;
}

async function _openAntigravityAgentTmux(targetAgent, serviceId) {
  const termSize = _estimateTerminalSize();
  addMsg('system', t('openingCCInteractiveTerminal', { agent: targetAgent }));
  action$('open_antigravity_interactive_terminal', {
    agent_name: targetAgent,
    service_id: serviceId || '',
    cols: termSize.cols,
    rows: termSize.rows,
  }).subscribe({
    next: async (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }
      const sessionId = resp.session_id;
      const token = resp.token || '';
      if (!token) {
        addMsg('system', t('terminalMissingToken'));
        return;
      }
      await _loadXterm();
      const tabId = addTerminalTab(sessionId, resp.relay_id || ('agy:' + targetAgent));
      const panel = document.getElementById('tabContent_' + tabId);
      const container = panel.querySelector('.xterm-container');
      _initXterm(container, sessionId, token);
    },
    error: (e) => {
      addMsg('system', t('failedToOpenTerminal', { error: e.message }));
    },
  });
  return true;
}

function _terminalInputB64(data) {
  const bytes = new TextEncoder().encode(data);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function _sendTerminalInput(ws, data) {
  if (!data || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: 'terminal_input', data: _terminalInputB64(data) }));
}

function _copyTerminalSelection(term) {
  const selected = term.getSelection ? term.getSelection() : '';
  if (!selected) return false;
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    const textarea = document.createElement('textarea');
    textarea.value = selected;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    try { document.execCommand('copy'); } finally { textarea.remove(); }
    addMsg('system', t('copiedCharsToClipboard', { n: selected.length }));
    return true;
  }
  navigator.clipboard.writeText(selected).then(() => {
    addMsg('system', t('copiedCharsToClipboard', { n: selected.length }));
  }).catch(e => addMsg('error', t('copyFailed', { error: e.message })));
  return true;
}

function _pasteClipboardToTerminal(ws) {
  if (!navigator.clipboard || !navigator.clipboard.readText) {
    addMsg('error', t('pasteFailed', { error: 'Clipboard API unavailable' }));
    return;
  }
  navigator.clipboard.readText().then(text => {
    _sendTerminalInput(ws, text || '');
  }).catch(e => addMsg('error', t('pasteFailed', { error: e.message })));
}

function _estimateTerminalSize() {
  const main = document.querySelector('.main') || document.body;
  const probe = document.createElement('div');
  probe.style.cssText = 'position:absolute;visibility:hidden;left:-9999px;top:-9999px;font:13px Menlo, Monaco, "Courier New", monospace;white-space:pre;';
  probe.textContent = 'W'.repeat(80);
  document.body.appendChild(probe);
  const charWidth = Math.max(1, probe.getBoundingClientRect().width / 80);
  const lineHeight = Math.max(1, probe.getBoundingClientRect().height || 16);
  probe.remove();
  const rect = main.getBoundingClientRect();
  const header = main.querySelector('.header');
  const headerHeight = header ? header.getBoundingClientRect().height : 0;
  return {
    cols: Math.max(80, Math.floor((rect.width - 16) / charWidth)),
    rows: Math.max(24, Math.floor((rect.height - headerHeight - 16) / lineHeight)),
  };
}

function _fitAndNotifyTerminal(container) {
  if (!container || !container._xterm || !container._fitAddon) return;
  try { container._fitAddon.fit(); } catch (e) {}
  const ws = container._ws;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: 'terminal_resize',
      cols: container._xterm.cols,
      rows: container._xterm.rows,
    }));
  }
}

/** Initialize xterm.js inside a container element. */
function _initXterm(container, sessionId, token) {
  const term = new window.Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    scrollback: 10000,
    fastScrollModifier: 'alt',
    fastScrollSensitivity: 5,
    theme: { background: '#0f0f23', foreground: '#e0e0e0', cursor: '#e94560' },
  });
  const fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(container);
  setTimeout(() => { _fitAndNotifyTerminal(container); term.focus(); }, 50);
  setTimeout(() => { _fitAndNotifyTerminal(container); }, 250);

  // Store refs on the container for cleanup
  container._xterm = term;
  container._fitAddon = fitAddon;

  // Resize observer
  const ro = new ResizeObserver(() => {
    _fitAndNotifyTerminal(container);
  });
  ro.observe(container);
  container._resizeObserver = ro;

  // Connect WS
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/terminal/' + sessionId + '/' + token);
  container._ws = ws;

  term.attachCustomKeyEventHandler((ev) => {
    const key = (ev.key || '').toLowerCase();
    const accel = ev.ctrlKey || ev.metaKey;
    if (ev.type === 'keydown' && accel && ev.shiftKey && key === 'c') {
      _copyTerminalSelection(term);
      return false;
    }
    if (ev.type === 'keydown' && accel && ev.shiftKey && key === 'v') {
      _pasteClipboardToTerminal(ws);
      return false;
    }
    return true;
  });

  container.addEventListener('paste', (ev) => {
    const text = ev.clipboardData && ev.clipboardData.getData('text/plain');
    if (text) {
      ev.preventDefault();
      _sendTerminalInput(ws, text);
    }
  });

  container.addEventListener('contextmenu', (ev) => {
    if (_copyTerminalSelection(term)) {
      ev.preventDefault();
      return;
    }
    if (navigator.clipboard && navigator.clipboard.readText) {
      ev.preventDefault();
      _pasteClipboardToTerminal(ws);
    }
  });

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'terminal_resize', cols: term.cols, rows: term.rows }));
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'terminal_data') {
        term.write(Uint8Array.from(atob(msg.data), c => c.charCodeAt(0)));
      } else if (msg.type === 'terminal_exit') {
        term.write('\r\n[' + t('processExited') + ']\r\n');
      }
    } catch (err) {}
  };

  ws.onclose = () => {
    term.write('\r\n[' + t('disconnected') + ']\r\n');
  };

  term.onData((data) => {
    _sendTerminalInput(ws, data);
  });

  term.onResize(({ cols, rows }) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'terminal_resize', cols, rows }));
    }
  });
}

/** /code command */
async function cmdCode(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (sub === 'close') {
    // Close the active vscode tab, or the only one
    if (_activeTab && _activeTab.startsWith('vscode-')) {
      closeVSCodeTab(_activeTab);
    } else {
      // Find any vscode tab
      const btn = document.querySelector('.tab-btn[data-tab^="vscode-"]');
      if (btn) closeVSCodeTab(btn.dataset.tab);
    }
    addMsg('system', t('codeServerClosed'));
    return true;
  }

  let relayId = parts[1] || '';
  let localMode = false;
  if (sub === 'local') { localMode = true; relayId = parts[2] || ''; }
  else if (sub === 'docker') { relayId = parts[2] || ''; }

  var allRelays = [];
  if (!relayId) {
    try {
      allRelays = await _getRelays();
      relayId = await _pickRelay(allRelays);
      if (!relayId) {
        addMsg('system', t('codeNoRelayUsage'));
        return true;
      }
    } catch (e) {
      addMsg('system', t('failedToListRelays', { error: e.message }));
      return true;
    }
  }
  if (!localMode && sub !== 'docker') {
    if (!allRelays.length) try { allRelays = await _getRelays(); } catch(e) {}
    var relay = allRelays.find(r => r.id === relayId);
    if (relay && relay.allow_local) {
      var mode = await _pickMode(relayId);
      if (mode === null) return true;
      localMode = mode === 'local';
    }
  }

  addMsg('system', t('startingCodeServer', { relay: relayId, mode: localMode ? ' (' + t('local') + ')' : '' }));
  action$('open_code_server', { relay_id: relayId, local: localMode }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }

      addVSCodeTab(relayId, resp.url || '/code/' + relayId + '/');
      addMsg('system', t('codeServerStartedLoading'));
    },
    error: (e) => {
      addMsg('system', t('failed', { error: e.message }));
    },
  });
  return true;
}

/** /desktop command */
async function cmdDesktop(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (sub === 'close') {
    // Just close the tab locally (desktop keeps running)
    if (_activeTab && _activeTab.startsWith('desktop-')) {
      closeDesktopTab(_activeTab);
    } else {
      const btn = document.querySelector('.tab-btn[data-tab^="desktop-"]');
      if (btn) closeDesktopTab(btn.dataset.tab);
    }
    addMsg('system', t('desktopTabClosed'));
    return true;
  }

  if (sub === 'stop') {
    // Actually stop the desktop on the relay
    const relayToStop = parts[2] || '';
    let _stopRelayId = relayToStop;
    if (!_stopRelayId) {
      // Find relay from active desktop tab
      const panel = _activeTab && document.getElementById('tabContent_' + _activeTab);
      _stopRelayId = panel && panel.dataset.relayId;
      if (!_stopRelayId) {
        const anyPanel = document.querySelector('[id^="tabContent_desktop-"]');
        _stopRelayId = anyPanel && anyPanel.dataset.relayId;
      }
    }
    if (!_stopRelayId) {
      addMsg('system', t('desktopStopUsage'));
      return true;
    }
    addMsg('system', t('stoppingDesktop', { relay: _stopRelayId }));
    fireAction('close_desktop', { relay_id: _stopRelayId });
    // Close all desktop tabs for this relay
    document.querySelectorAll('[id^="tabContent_desktop-"]').forEach(p => {
      if (p.dataset.relayId === _stopRelayId) {
        const tId = p.id.replace('tabContent_', '');
        closeDesktopTab(tId);
      }
    });
    addMsg('system', t('desktopStopped'));
    return true;
  }

  let relayId = parts[1] || '';
  let localScreen = false;
  if (sub === 'local') {
    localScreen = true;
    relayId = parts[2] || '';
  } else if (sub === 'docker') {
    relayId = parts[2] || '';
  }

  if (!relayId) {
    try {
      const relays = await _getRelays();
      relayId = await _pickRelay(relays);
      if (!relayId) {
        addMsg('system', t('desktopNoRelayUsage'));
        return true;
      }
    } catch (e) {
      addMsg('system', t('failedToListRelays', { error: e.message }));
      return true;
    }
  }

  // Check if the relay supports local screen and offer choice
  if (!localScreen && sub !== 'docker') {
    var _dRelays = [];
    try { _dRelays = await _getRelays(); } catch(e) {}
    var _dRelay = _dRelays.find(r => r.id === relayId);
    if (_dRelay && _dRelay.allow_local) {
      var _dMode = await _pickMode(relayId);
      if (_dMode === null) return true;
      localScreen = _dMode === 'local';
    }
  }

  addMsg('system', t('startingDesktop', { target: localScreen ? t('localScreen') : t('desktopLower'), relay: relayId }));
  action$('open_desktop', { relay_id: relayId, local_screen: localScreen }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }

      // Close audio-only tab if open (desktop includes audio)
      const _audioTab = document.querySelector('[id^="tabContent_audio-"]');
      if (_audioTab) {
        const _atId = _audioTab.id.replace('tabContent_', '');
        closeAudioTab(_atId);
      }
      const _prefix = localScreen ? 'local_desktop' : 'desktop';
      const _desktopSid = _prefix + '_' + relayId;
      const _tabLabel = localScreen ? relayId + ' (local)' : relayId;
      // Backend always returns a tokenised URL via resp.url — without
      // the capability token in the path the proxy returns 401/403,
      // so a hand-built fallback URL would never work anyway.
      if (!resp.url) {
        addMsg('error', t('desktopNoUrl'));
        return;
      }
      addDesktopTab(_tabLabel, resp.url);
      // Connect audio if available. The capability token is separate
      // from the VNC one (different resource_type) — audioConnect needs
      // both the session id and the audio token to build the WS URL.
      if (resp.audio_session && resp.audio_token) {
        audioConnect(resp.audio_session, resp.audio_token);
      } else if (resp.audio_session) {
        console.warn('[audio] desktop returned audio session without capability token; skipping websocket');
      }
      addMsg('system', localScreen ? t('localScreenReady') : t('desktopReady'));
    },
    error: (e) => {
      addMsg('system', t('failed', { error: e.message }));
    },
  });
  return true;
}

/** /audio command — forward audio only (no VNC). Reuses open_desktop backend. */
async function cmdAudio(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (sub === 'stop' || sub === 'close') {
    const audioTab = document.querySelector('[id^="tabContent_audio-"]');
    if (audioTab) {
      const tId = audioTab.id.replace('tabContent_', '');
      closeAudioTab(tId);
      addMsg('system', t('audioTabClosed'));
    } else {
      addMsg('system', t('noAudioTabOpen'));
    }
    return true;
  }

  let relayId = parts[1] || '';
  if (!relayId) {
    try {
      const relays = await _getRelays();
      relayId = await _pickRelay(relays);
      if (!relayId) {
        addMsg('system', t('noConnectedRelay'));
        return true;
      }
    } catch (e) {
      addMsg('system', t('failedToListRelays', { error: e.message }));
      return true;
    }
  }

  addMsg('system', t('startingAudio', { relay: relayId }));
  action$('open_desktop', { relay_id: relayId }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }
      if (resp.audio_session && resp.audio_token) {
        addAudioTab(relayId, resp.audio_session, resp.audio_token);
        audioConnect(resp.audio_session, resp.audio_token);
        addMsg('system', t('audioStreamingFrom', { relay: relayId }));
      } else if (resp.audio_session) {
        addMsg('system', '\u26a0 ' + t('audioNoToken'));
      } else {
        addMsg('system', '\u26a0 ' + t('audioUnavailable'));
      }
    },
    error: (e) => {
      addMsg('system', t('failed', { error: e.message }));
    },
  });
  return true;
}

/** Pick between Docker desktop and local screen. */
// _pickDesktopMode removed — replaced by unified _pickMode

/** /port-forward command */
async function cmdPortForward(text, parts) {
  const sub = (parts[1] || '').toLowerCase();

  if (sub === 'list' || !sub) {
    action$('port_forward_list').subscribe({
      next: (resp) => {
        const fwds = resp.forwards || [];
        if (!fwds.length) {
          addMsg('system', t('noActivePortForwards'));
        } else {
          const lines = fwds.map(f => f.relay_id + ':' + f.int_port + (f.int_port !== f.ext_port ? ' (ext ' + f.ext_port + ')' : '') + ' \u2192 ' + f.url);
          addMsg('system', t('activeForwards', { lines: lines.join('\n') }));
        }
      },
      error: (e) => {
        addMsg('system', t('failed', { error: e.message }));
      },
    });
    return true;
  }

  if (sub === 'add') {
    let relayId = parts[2] || '';
    let port = parts[3] || '';
    const extPort = parts[4] || '';
    if (!relayId || !port) {
      // Show dialog
      try {
        const relays = await _getRelays();
        if (!relays.length) {
          addMsg('system', t('noConnectedRelay'));
          return true;
        }
        relayId = relayId || await _pickRelay(relays);
        if (!relayId) return true;
        if (!port) {
          port = prompt(t('portPrompt', { relay: relayId }));
          if (!port) return true;
        }
      } catch (e) {
        addMsg('system', t('failed', { error: e.message }));
        return true;
      }
    }
    action$('port_forward_add', {
      relay_id: relayId,
      port: parseInt(port),
      ext_port: extPort ? parseInt(extPort) : undefined,
    }).subscribe({
      next: (resp) => {
        if (resp.error) {
          addMsg('system', '\u26a0 ' + resp.error);
        } else {
          addMsg('system', t('portForwardAdded', { relay: relayId, port: port, url: resp.url }));
        }
      },
      error: (e) => {
        addMsg('system', t('failed', { error: e.message }));
      },
    });
    return true;
  }

  if (sub === 'remove' || sub === 'rm') {
    const relayId = parts[2] || '';
    const port = parts[3] || '';
    if (!relayId || !port) {
      addMsg('system', t('portForwardRemoveUsage'));
      return true;
    }
    action$('port_forward_remove', { relay_id: relayId, ext_port: parseInt(port) }).subscribe({
      next: (resp) => {
        if (resp.error) {
          addMsg('system', '\u26a0 ' + resp.error);
        } else {
          addMsg('system', t('portForwardRemoved'));
        }
      },
      error: (e) => {
        addMsg('system', t('failed', { error: e.message }));
      },
    });
    return true;
  }

  if (sub === 'open') {
    const relayId = parts[2] || '';
    const port = parts[3] || '';
    if (!relayId || !port) {
      addMsg('system', t('portForwardOpenUsage'));
      return true;
    }
    // The URL now embeds a capability token — look it up via
    // port_forward_list rather than reconstructing it from
    // (relay_id, port), which would land on a 401/403.
    action$('port_forward_list', {}).subscribe({
      next: (resp) => {
        const entries = (resp && resp.forwards) || [];
        const match = entries.find((e) =>
          e.relay_id === relayId && Number(e.ext_port) === Number(port));
        if (!match) {
          addMsg('system', '\u26a0 ' + t('noForwardFor', { relay: relayId, port: port }));
          return;
        }
        addBrowserTab(relayId + ':' + port, match.url);
      },
      error: (e) => addMsg('system', t('failedWithError', { error: e.message })),
    });
    return true;
  }

  addMsg('system', t('portForwardUsage'));
  return true;
}


/** /vm command — list and manage Docker containers */
function cmdVm(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();

  if (sub === 'list' || sub === 'ls') {
    action$('list_vms', {}).subscribe(data => {
      const vms = data.vms || [];
      if (vms.length === 0) {
        addMsg('system', t('noActiveDockerContainers'));
        return;
      }
      let lines = [t('dockerContainersHeader', { n: vms.length })];
      for (const vm of vms) {
        const ownerBadge = vm.owner === 'server'
          ? '\u{1F5A5} ' + t('serverOwner')
          : '\u{1F4BB} ' + t('clientOwner');
        lines.push(
          '  `' + vm.id.slice(0, 12) + '` '
          + '**' + vm.name + '** '
          + '(' + ownerBadge + ') '
          + vm.status + ' '
          + '*' + vm.image + '*'
        );
      }
      lines.push('\n' + t('vmListHint'));
      addMsg('system', lines.join('\n'));
    });
    return true;
  }

  if (sub === 'kill' || sub === 'rm' || sub === 'stop') {
    const target = parts[2] || '';
    if (!target) {
      addMsg('system', t('vmKillUsage'));
      return true;
    }
    action$('kill_vm', { container_id: target }).subscribe(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', t('containerKilled', { target: data.killed || target }));
    });
    return true;
  }

  if (sub === 'killall') {
    action$('list_vms', {}).subscribe(data => {
      const vms = data.vms || [];
      if (vms.length === 0) {
        addMsg('system', t('noContainersToKill'));
        return;
      }
      let killed = 0;
      for (const vm of vms) {
        fireAction('kill_vm', { container_id: vm.id });
        killed++;
      }
      addMsg('system', t('killingContainers', { n: killed }));
    });
    return true;
  }

  addMsg('system', t('vmUsage'));
  return true;
}
