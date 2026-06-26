// \xe2\x94\x80\xe2\x94\x80 Terminal engine: xterm.js loader + relay/mode pickers + I/O helpers \xe2\x94\x80\xe2\x94\x80
// Command handlers (/terminal, /code, /desktop, /audio, /port-forward, /vm,
// and agent tmux) are in terminal_commands.js, loaded right after this file.
// Everything here is a global (no ES modules) \xe2\x80\x94 see _JS_MODULES order.

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
        container._terminalExited = true;
        try { ws.close(); } catch (_) {}
      }
    } catch (err) {}
  };

  ws.onclose = () => {
    if (!container._terminalExited) {
      term.write('\r\n[' + t('disconnected') + ']\r\n');
    }
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
