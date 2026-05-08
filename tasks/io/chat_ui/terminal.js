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
      addMsg('system', 'Failed to list relays: ' + e.message);
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

  action$('open_terminal', { relay_id: relayId, cols: 120, rows: 30, local: localMode }).subscribe({
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
      addMsg('system', 'Failed to open terminal: ' + e.message);
    },
  });
  return true;
}

/** Initialize xterm.js inside a container element. */
function _initXterm(container, sessionId, token) {
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
  const ws = new WebSocket(proto + '//' + location.host + '/terminal/' + sessionId + '/' + token);
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
        term.write('\r\n[' + t('processExited') + ']\r\n');
      }
    } catch (err) {}
  };

  ws.onclose = () => {
    term.write('\r\n[' + t('disconnected') + ']\r\n');
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
        addMsg('system', 'No connected relay found. Usage: /code <relay_name>');
        return true;
      }
    } catch (e) {
      addMsg('system', 'Failed to list relays: ' + e.message);
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

  addMsg('system', 'Starting code-server on ' + relayId + (localMode ? ' (local)' : '') + '...');
  action$('open_code_server', { relay_id: relayId, local: localMode }).subscribe({
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
    // Just close the tab locally (desktop keeps running)
    if (_activeTab && _activeTab.startsWith('desktop-')) {
      closeDesktopTab(_activeTab);
    } else {
      const btn = document.querySelector('.tab-btn[data-tab^="desktop-"]');
      if (btn) closeDesktopTab(btn.dataset.tab);
    }
    addMsg('system', 'Desktop tab closed (desktop still running). Use /desktop stop to shut down.');
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
      addMsg('system', 'No active desktop to stop. Usage: /desktop stop [relay_id]');
      return true;
    }
    addMsg('system', 'Stopping desktop on ' + _stopRelayId + '...');
    fireAction('close_desktop', { relay_id: _stopRelayId });
    // Close all desktop tabs for this relay
    document.querySelectorAll('[id^="tabContent_desktop-"]').forEach(p => {
      if (p.dataset.relayId === _stopRelayId) {
        const tId = p.id.replace('tabContent_', '');
        closeDesktopTab(tId);
      }
    });
    addMsg('system', 'Desktop stopped.');
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
    var _dRelays = [];
    try { _dRelays = await _getRelays(); } catch(e) {}
    var _dRelay = _dRelays.find(r => r.id === relayId);
    if (_dRelay && _dRelay.allow_local) {
      var _dMode = await _pickMode(relayId);
      if (_dMode === null) return true;
      localScreen = _dMode === 'local';
    }
  }

  addMsg('system', 'Starting ' + (localScreen ? 'local screen' : 'desktop') + ' on ' + relayId + '...');
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
        addMsg('error', 'Desktop ready but server did not return a URL');
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
      addMsg('system', (localScreen ? 'Local screen' : 'Desktop') + ' ready.');
    },
    error: (e) => {
      addMsg('system', 'Failed: ' + e.message);
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
      addMsg('system', 'Audio tab closed.');
    } else {
      addMsg('system', 'No audio tab open.');
    }
    return true;
  }

  let relayId = parts[1] || '';
  if (!relayId) {
    try {
      const relays = await _getRelays();
      relayId = await _pickRelay(relays);
      if (!relayId) {
        addMsg('system', 'No connected relay found.');
        return true;
      }
    } catch (e) {
      addMsg('system', 'Failed to list relays: ' + e.message);
      return true;
    }
  }

  addMsg('system', 'Starting audio on ' + relayId + '...');
  action$('open_desktop', { relay_id: relayId }).subscribe({
    next: (resp) => {
      if (resp.error) {
        addMsg('system', '\u26a0 ' + resp.error);
        return;
      }
      if (resp.audio_session && resp.audio_token) {
        addAudioTab(relayId, resp.audio_session, resp.audio_token);
        audioConnect(resp.audio_session, resp.audio_token);
        addMsg('system', 'Audio streaming from ' + relayId + '.');
      } else if (resp.audio_session) {
        addMsg('system', '\u26a0 Audio source is available but no capability token was returned.');
      } else {
        addMsg('system', '\u26a0 No audio available on this relay.');
      }
    },
    error: (e) => {
      addMsg('system', 'Failed: ' + e.message);
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
    // The URL now embeds a capability token — look it up via
    // port_forward_list rather than reconstructing it from
    // (relay_id, port), which would land on a 401/403.
    action$('port_forward_list', {}).subscribe({
      next: (resp) => {
        const entries = (resp && resp.forwards) || [];
        const match = entries.find((e) =>
          e.relay_id === relayId && Number(e.ext_port) === Number(port));
        if (!match) {
          addMsg('system', '⚠ No forward for ' + relayId + ':' + port + ' — add it first.');
          return;
        }
        addBrowserTab(relayId + ':' + port, match.url);
      },
      error: (e) => addMsg('system', 'Failed: ' + e.message),
    });
    return true;
  }

  addMsg('system', 'Usage: /port-forward <add|remove|list|open> [relay_id] [port] [ext_port]');
  return true;
}


/** /vm command — list and manage Docker containers */
function cmdVm(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();

  if (sub === 'list' || sub === 'ls') {
    action$('list_vms', {}).subscribe(data => {
      const vms = data.vms || [];
      if (vms.length === 0) {
        addMsg('system', 'No active Docker containers.');
        return;
      }
      let lines = ['**Docker Containers** (' + vms.length + '):\n'];
      for (const vm of vms) {
        const ownerBadge = vm.owner === 'server'
          ? '\u{1F5A5} server'
          : '\u{1F4BB} client';
        lines.push(
          '  `' + vm.id.slice(0, 12) + '` '
          + '**' + vm.name + '** '
          + '(' + ownerBadge + ') '
          + vm.status + ' '
          + '*' + vm.image + '*'
        );
      }
      lines.push('\nUse `/vm kill <id>` to stop a container.');
      addMsg('system', lines.join('\n'));
    });
    return true;
  }

  if (sub === 'kill' || sub === 'rm' || sub === 'stop') {
    const target = parts[2] || '';
    if (!target) {
      addMsg('system', 'Usage: /vm kill <container_id or name>');
      return true;
    }
    action$('kill_vm', { container_id: target }).subscribe(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', '\u2705 Container killed: ' + (data.killed || target));
    });
    return true;
  }

  if (sub === 'killall') {
    action$('list_vms', {}).subscribe(data => {
      const vms = data.vms || [];
      if (vms.length === 0) {
        addMsg('system', 'No containers to kill.');
        return;
      }
      let killed = 0;
      for (const vm of vms) {
        fireAction('kill_vm', { container_id: vm.id });
        killed++;
      }
      addMsg('system', '\u2705 Killing ' + killed + ' container(s)...');
    });
    return true;
  }

  addMsg('system', 'Usage: /vm <list|kill|killall> [container_id]\n'
    + '  /vm list              \u2014 List all PawFlow Docker containers\n'
    + '  /vm kill <id>         \u2014 Kill a specific container\n'
    + '  /vm killall           \u2014 Kill all PawFlow containers');
  return true;
}
