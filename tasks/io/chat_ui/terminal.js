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
function _getRelays() {
  return new Promise((resolve, reject) => {
    action$('service_list').subscribe({
      next: data => resolve((data.services || []).filter(s => s.type === 'relay' && s.started)),
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
    bg.className = 'dialog-bg';
    bg.innerHTML = '<div class="dialog" style="max-width:340px">'
      + '<div class="dialog-title">Choose relay</div>'
      + '<div class="dialog-body" id="_relayPickList"></div>'
      + '<div class="dialog-actions"><button onclick="this.closest(\'.dialog-bg\').remove()" class="btn">Cancel</button></div>'
      + '</div>';
    const list = bg.querySelector('#_relayPickList');
    for (const r of relays) {
      const btn = document.createElement('button');
      btn.className = 'btn btn-primary';
      btn.style.cssText = 'display:block;width:100%;margin-bottom:6px;text-align:left;';
      btn.textContent = r.id + (r.name && r.name !== r.id ? ' (' + r.name + ')' : '');
      btn.onclick = () => { bg.remove(); resolve(r.id); };
      list.appendChild(btn);
    }
    document.body.appendChild(bg);
  });
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
      const relays = await _getRelays();
      relayId = await _pickRelay(relays);
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

  action$('open_terminal', { relay_id: relayId, cols: 120, rows: 30 }).subscribe({
    next: async (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }

      const sessionId = resp.session_id;
      await _loadXterm();

      // Create tab and init xterm inside it
      const tabId = addTerminalTab(sessionId, relayId);
      const panel = document.getElementById('tabContent_' + tabId);
      const container = panel.querySelector('.xterm-container');
      _initXterm(container, sessionId);
    },
    error: (e) => {
      addMsg('system', 'Failed to open terminal: ' + e.message);
    },
  });
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
    // Close the active vscode tab, or the only one
    if (_activeTab && _activeTab.startsWith('vscode-')) {
      closeVSCodeTab(_activeTab);
    } else {
      // Find any vscode tab
      const btn = document.querySelector('.tab-btn[data-tab^="vscode-"]');
      if (btn) closeVSCodeTab(btn.dataset.tab);
    }
    addMsg('system', 'Code server closed.');
    return true;
  }

  let relayId = parts[1] || '';
  if (!relayId) {
    try {
      const relays = await _getRelays();
      relayId = await _pickRelay(relays);
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
  action$('open_code_server', { relay_id: relayId }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }

      addVSCodeTab(relayId, resp.url || '/code/' + relayId + '/');
      addMsg('system', 'code-server started. Loading editor...');
    },
    error: (e) => {
      addMsg('system', 'Failed: ' + e.message);
    },
  });
  return true;
}

/** /desktop command */
async function cmdDesktop(text, parts) {
  const sub = (parts[1] || '').toLowerCase();
  if (sub === 'close') {
    if (_activeTab && _activeTab.startsWith('desktop-')) {
      closeDesktopTab(_activeTab);
    } else {
      const btn = document.querySelector('.tab-btn[data-tab^="desktop-"]');
      if (btn) closeDesktopTab(btn.dataset.tab);
    }
    addMsg('system', 'Desktop closed.');
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
        addMsg('system', 'No connected relay found. Usage: /desktop <relay_name>');
        return true;
      }
    } catch (e) {
      addMsg('system', 'Failed to list relays: ' + e.message);
      return true;
    }
  }

  // Check if the relay supports local screen and offer choice
  if (!localScreen && sub !== 'docker') {
    try {
      const relays = await _getRelays();
      const relay = relays.find(r => r.id === relayId);
      const info = relay && relay.relay_info;
      if (info && info.allow_local_screen) {
        const choice = await _pickDesktopMode();
        if (choice === null) return true;  // cancelled
        localScreen = choice === 'local';
      }
    } catch (e) {
      // Fall through to default (docker)
    }
  }

  addMsg('system', 'Starting ' + (localScreen ? 'local screen' : 'desktop') + ' on ' + relayId + '...');
  action$('open_desktop', { relay_id: relayId, local_screen: localScreen }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }

      const _prefix = localScreen ? 'local_desktop' : 'desktop';
      const _desktopSid = _prefix + '_' + relayId;
      const _tabLabel = localScreen ? relayId + ' (local)' : relayId;
      addDesktopTab(_tabLabel, resp.url || '/vnc/' + _desktopSid + '/vnc.html?autoconnect=true&resize=scale&path=vnc/' + _desktopSid + '/websockify');
      addMsg('system', (localScreen ? 'Local screen' : 'Desktop') + ' ready.');
    },
    error: (e) => {
      addMsg('system', 'Failed: ' + e.message);
    },
  });
  return true;
}

/** Pick between Docker desktop and local screen. */
function _pickDesktopMode() {
  return new Promise(resolve => {
    const bg = document.createElement('div');
    bg.className = 'dialog-bg';
    bg.innerHTML = '<div class="dialog" style="max-width:340px">'
      + '<div class="dialog-title">Choose desktop mode</div>'
      + '<div class="dialog-body" id="_desktopPickList"></div>'
      + '<div class="dialog-actions"><button onclick="this.closest(\'.dialog-bg\').remove()" class="btn">Cancel</button></div>'
      + '</div>';
    const list = bg.querySelector('#_desktopPickList');
    const btnDocker = document.createElement('button');
    btnDocker.className = 'btn btn-primary';
    btnDocker.style.cssText = 'display:block;width:100%;margin-bottom:6px;text-align:left;';
    btnDocker.textContent = 'Docker Desktop (virtual screen in container)';
    btnDocker.onclick = () => { bg.remove(); resolve('docker'); };
    list.appendChild(btnDocker);
    const btnLocal = document.createElement('button');
    btnLocal.className = 'btn btn-primary';
    btnLocal.style.cssText = 'display:block;width:100%;margin-bottom:6px;text-align:left;';
    btnLocal.textContent = 'Local Screen (user\'s display)';
    btnLocal.onclick = () => { bg.remove(); resolve('local'); };
    list.appendChild(btnLocal);
    bg.querySelector('.dialog-actions button').onclick = () => { bg.remove(); resolve(null); };
    document.body.appendChild(bg);
  });
}

/** /port-forward command */
async function cmdPortForward(text, parts) {
  const sub = (parts[1] || '').toLowerCase();

  if (sub === 'list' || !sub) {
    action$('port_forward_list').subscribe({
      next: (resp) => {
        const fwds = resp.forwards || [];
        if (!fwds.length) {
          addMsg('system', 'No active port forwards.');
        } else {
          const lines = fwds.map(f => f.relay_id + ':' + f.int_port + (f.int_port !== f.ext_port ? ' (ext ' + f.ext_port + ')' : '') + ' \u2192 ' + f.url);
          addMsg('system', 'Active forwards:\n' + lines.join('\n'));
        }
      },
      error: (e) => {
        addMsg('system', 'Failed: ' + e.message);
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
          addMsg('system', 'No connected relay found.');
          return true;
        }
        relayId = relayId || await _pickRelay(relays);
        if (!relayId) return true;
        if (!port) {
          port = prompt('Port to forward from ' + relayId + ':');
          if (!port) return true;
        }
      } catch (e) {
        addMsg('system', 'Failed: ' + e.message);
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
          addMsg('system', 'Port forward added: ' + relayId + ':' + port + ' \u2192 ' + resp.url);
        }
      },
      error: (e) => {
        addMsg('system', 'Failed: ' + e.message);
      },
    });
    return true;
  }

  if (sub === 'remove' || sub === 'rm') {
    const relayId = parts[2] || '';
    const port = parts[3] || '';
    if (!relayId || !port) {
      addMsg('system', 'Usage: /port-forward remove <relay_id> <ext_port>');
      return true;
    }
    action$('port_forward_remove', { relay_id: relayId, ext_port: parseInt(port) }).subscribe({
      next: (resp) => {
        if (resp.error) {
          addMsg('system', '\u26a0 ' + resp.error);
        } else {
          addMsg('system', 'Port forward removed.');
        }
      },
      error: (e) => {
        addMsg('system', 'Failed: ' + e.message);
      },
    });
    return true;
  }

  if (sub === 'open') {
    const relayId = parts[2] || '';
    const port = parts[3] || '';
    if (!relayId || !port) {
      addMsg('system', 'Usage: /port-forward open <relay_id> <port>');
      return true;
    }
    const url = '/fwd/' + relayId + '/' + port + '/';
    addBrowserTab(relayId + ':' + port, url);
    return true;
  }

  addMsg('system', 'Usage: /port-forward <add|remove|list|open> [relay_id] [port] [ext_port]');
  return true;
}
