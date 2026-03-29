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

/** Auto-detect the first connected relay. */
async function _findRelay() {
  const resp = await fetch(API, {
    method: 'POST', headers: getAuthHeaders(),
    body: JSON.stringify({ action: 'service_list' }),
  }).then(r => r.json());
  const relays = (resp.services || []).filter(s => s.type === 'relay' && s.started);
  return relays.length ? relays[0].id : null;
}

/** /terminal command */
async function cmdTerminal(text, parts) {
  const sub = (parts[1] || '').toLowerCase();

  if (sub === 'close') {
    // Close the currently active terminal tab
    if (_activeTab && _activeTab.startsWith('term-')) {
      closeTerminalTab(_activeTab);
      addMsg('system', 'Terminal closed.');
    } else {
      addMsg('system', 'No active terminal to close.');
    }
    return true;
  }

  let relayId = parts[1] || '';
  if (!relayId) {
    try {
      relayId = await _findRelay();
      if (!relayId) {
        addMsg('system', 'No connected relay found. Usage: /terminal <relay_name>');
        return true;
      }
    } catch (e) {
      addMsg('system', 'Failed to list relays: ' + e.message);
      return true;
    }
  }

  addMsg('system', 'Opening terminal on ' + relayId + '...');

  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'open_terminal', relay_id: relayId, cols: 120, rows: 30 }),
    }).then(r => r.json());

    if (resp.error) {
      addMsg('system', '\u26a0 ' + resp.error);
      return true;
    }

    const sessionId = resp.session_id;
    await _loadXterm();

    // Create tab and init xterm inside it
    const tabId = addTerminalTab(sessionId, relayId);
    const panel = document.getElementById('tabContent_' + tabId);
    const container = panel.querySelector('.xterm-container');
    _initXterm(container, sessionId);
  } catch (e) {
    addMsg('system', 'Failed to open terminal: ' + e.message);
  }
  return true;
}

/** Initialize xterm.js inside a container element. */
function _initXterm(container, sessionId) {
  const term = new window.Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    theme: { background: '#0f0f23', foreground: '#e0e0e0', cursor: '#e94560' },
  });
  const fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(container);
  setTimeout(() => { fitAddon.fit(); term.focus(); }, 50);

  // Store refs on the container for cleanup
  container._xterm = term;
  container._fitAddon = fitAddon;

  // Resize observer
  const ro = new ResizeObserver(() => {
    try { fitAddon.fit(); } catch(e) {}
  });
  ro.observe(container);
  container._resizeObserver = ro;

  // Connect WS
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/terminal/' + sessionId);
  container._ws = ws;

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'terminal_resize', cols: term.cols, rows: term.rows }));
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'terminal_data') {
        term.write(Uint8Array.from(atob(msg.data), c => c.charCodeAt(0)));
      } else if (msg.type === 'terminal_exit') {
        term.write('\r\n[Process exited]\r\n');
      }
    } catch (err) {}
  };

  ws.onclose = () => {
    term.write('\r\n[Disconnected]\r\n');
  };

  term.onData((data) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'terminal_input', data: btoa(data) }));
    }
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
    closeVSCodeTab();
    addMsg('system', 'Code server closed.');
    return true;
  }

  let relayId = parts[1] || '';
  if (!relayId) {
    try {
      relayId = await _findRelay();
      if (!relayId) {
        addMsg('system', 'No connected relay found. Usage: /code <relay_name>');
        return true;
      }
    } catch (e) {
      addMsg('system', 'Failed to list relays: ' + e.message);
      return true;
    }
  }

  addMsg('system', 'Starting code-server on ' + relayId + '...');
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'open_code_server', relay_id: relayId }),
    }).then(r => r.json());

    if (resp.error) {
      addMsg('system', '\u26a0 ' + resp.error);
      return true;
    }

    addVSCodeTab(relayId, resp.url || '/code/' + relayId + '/');
    addMsg('system', 'code-server started. Loading editor...');
  } catch (e) {
    addMsg('system', 'Failed: ' + e.message);
  }
  return true;
}
