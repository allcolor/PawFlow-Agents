// ── Terminal panel (xterm.js over WebSocket) ────────────────────
// /terminal [relay_name] — open a terminal on a relay
// /terminal close        — close current terminal
// /code [relay_name]     — open code-server on a relay

let _terminalOverlay = null;
let _terminalWs = null;
let _terminalSessionId = null;
let _xtermLoaded = false;

/** Load xterm.js + fit addon from CDN (once). */
function _loadXterm() {
  if (_xtermLoaded) return Promise.resolve();
  return new Promise((resolve, reject) => {
    // CSS
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css';
    document.head.appendChild(link);
    // JS
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js';
    script.onload = () => {
      // Fit addon
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

/** Open a terminal on the given relay. */
async function cmdTerminal(text, parts) {
  const sub = (parts[1] || '').toLowerCase();

  if (sub === 'close') {
    _closeTerminalPanel();
    addMsg('system', 'Terminal closed.');
    return true;
  }

  // Get relay name (first arg or default filesystem)
  let relayId = parts[1] || '';
  if (!relayId) {
    // Try to find the first relay service
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'service_list' }),
      }).then(r => r.json());
      const relays = (resp.services || []).filter(s => s.type === 'relay' && s.started);
      if (relays.length === 0) {
        addMsg('system', 'No connected relay found. Usage: /terminal <relay_name>');
        return true;
      }
      relayId = relays[0].id;
    } catch (e) {
      addMsg('system', 'Failed to list relays: ' + e.message);
      return true;
    }
  }

  addMsg('system', 'Opening terminal on ' + relayId + '...');

  // Ask server to open a PTY on the relay
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
    _terminalSessionId = sessionId;

    await _loadXterm();
    _openTerminalPanel(sessionId, relayId);
  } catch (e) {
    addMsg('system', 'Failed to open terminal: ' + e.message);
  }
  return true;
}

/** Open code-server on the given relay. */
async function cmdCode(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (sub === 'close') {
    const relayId = parts[2] || '';
    if (relayId) {
      await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'close_code_server', relay_id: relayId }),
      });
    }
    // Remove overlay if present
    const ov = document.getElementById('code-server-overlay');
    if (ov) ov.remove();
    addMsg('system', 'Code server closed.');
    return true;
  }

  let relayId = parts[1] || '';
  if (!relayId) {
    try {
      const resp = await fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'service_list' }),
      }).then(r => r.json());
      const relays = (resp.services || []).filter(s => s.type === 'relay' && s.started);
      if (relays.length === 0) {
        addMsg('system', 'No connected relay found. Usage: /code <relay_name>');
        return true;
      }
      relayId = relays[0].id;
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

    addMsg('system', 'code-server started on relay port ' + resp.port
      + '. Note: code-server is only accessible from the relay host. '
      + 'A reverse proxy will be needed for remote access.');
  } catch (e) {
    addMsg('system', 'Failed: ' + e.message);
  }
  return true;
}

function _openTerminalPanel(sessionId, relayId) {
  // Close existing
  _closeTerminalPanel();

  // Create overlay
  const overlay = document.createElement('div');
  overlay.id = 'terminal-overlay';
  overlay.style.cssText = 'position:fixed;bottom:0;left:0;width:100%;height:45%;'
    + 'background:#1a1a2e;z-index:9999;display:flex;flex-direction:column;'
    + 'border-top:2px solid #e94560;transition:height 0.2s;';

  // Header with drag handle
  const header = document.createElement('div');
  header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;'
    + 'padding:4px 12px;background:#16213e;cursor:ns-resize;user-select:none;flex-shrink:0;';
  header.innerHTML = '<span style="color:#aaa;font-size:12px;">Terminal \u2014 ' + escapeHtml(relayId) + '</span>'
    + '<div>'
    + '<button id="term-minimize" style="background:none;border:none;color:#aaa;font-size:14px;cursor:pointer;margin-right:8px;">\u2500</button>'
    + '<button id="term-close" style="background:none;border:none;color:#e94560;font-size:16px;cursor:pointer;">\u00d7</button>'
    + '</div>';

  // Terminal container
  const termDiv = document.createElement('div');
  termDiv.id = 'terminal-container';
  termDiv.style.cssText = 'flex:1;overflow:hidden;padding:4px;';

  overlay.appendChild(header);
  overlay.appendChild(termDiv);
  document.body.appendChild(overlay);
  _terminalOverlay = overlay;

  // Resize drag
  let dragging = false;
  header.addEventListener('mousedown', (e) => {
    if (e.target.tagName === 'BUTTON') return;
    dragging = true;
    e.preventDefault();
  });
  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const h = window.innerHeight - e.clientY;
    overlay.style.height = Math.max(100, Math.min(h, window.innerHeight - 50)) + 'px';
    if (_termFit) setTimeout(() => _termFit.fit(), 50);
  });
  document.addEventListener('mouseup', () => { dragging = false; });

  // Buttons
  document.getElementById('term-close').onclick = () => _closeTerminalPanel();
  document.getElementById('term-minimize').onclick = () => {
    if (overlay.style.height === '32px') {
      overlay.style.height = '45%';
      termDiv.style.display = '';
      if (_termFit) setTimeout(() => _termFit.fit(), 100);
    } else {
      overlay.style.height = '32px';
      termDiv.style.display = 'none';
    }
  };

  // Init xterm.js
  const term = new window.Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    theme: {
      background: '#0f0f23',
      foreground: '#e0e0e0',
      cursor: '#e94560',
    },
  });
  const fitAddon = new window.FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(termDiv);
  setTimeout(() => { fitAddon.fit(); term.focus(); }, 50);
  window._termFit = fitAddon;

  // Resize observer
  const ro = new ResizeObserver(() => {
    try { fitAddon.fit(); } catch(e) {}
  });
  ro.observe(termDiv);

  // Connect WS
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/terminal/' + sessionId);
  _terminalWs = ws;

  ws.onopen = () => {
    console.log('[terminal] WS connected');
    ws.send(JSON.stringify({
      type: 'terminal_resize',
      cols: term.cols,
      rows: term.rows,
    }));
  };

  ws.onerror = (e) => {
    console.error('[terminal] WS error:', e);
  };

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'terminal_data') {
        const bytes = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0));
        term.write(bytes);
      } else if (msg.type === 'terminal_exit') {
        term.write('\r\n[Process exited]\r\n');
      }
    } catch (err) {
      console.warn('[terminal] bad message:', err);
    }
  };

  ws.onclose = (e) => {
    console.log('[terminal] WS closed:', e.code, e.reason);
    term.write('\r\n[Disconnected]\r\n');
  };

  // Input: user types → relay PTY
  term.onData((data) => {
    console.log('[terminal] onData:', data.length, 'bytes, ws state:', ws.readyState);
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'terminal_input',
        data: btoa(data),
      }));
    }
  });

  // Resize: terminal size changes → relay PTY resize
  term.onResize(({ cols, rows }) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'terminal_resize',
        cols, rows,
      }));
    }
  });
}

function _closeTerminalPanel() {
  if (_terminalWs) {
    try { _terminalWs.close(); } catch(e) {}
    _terminalWs = null;
  }
  if (_terminalOverlay) {
    _terminalOverlay.remove();
    _terminalOverlay = null;
  }
  if (_terminalSessionId) {
    // Tell server to close the PTY
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'close_terminal', session_id: _terminalSessionId,
        relay_id: '' }), // relay_id retrieved server-side from session
    }).catch(() => {});
    _terminalSessionId = null;
  }
  window._termFit = null;
}
