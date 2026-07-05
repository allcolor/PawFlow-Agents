// \xe2\x94\x80\xe2\x94\x80 Terminal/desktop/code/audio/port-forward/vm command handlers \xe2\x94\x80\xe2\x94\x80
// The xterm engine (loader, relay/mode pickers, I/O helpers, _initXterm)
// lives in terminal.js, loaded immediately before this file. Everything
// here is a global (no ES modules) \xe2\x80\x94 see _JS_MODULES in serve_chat_ui.py.

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

function absoluteForwardUrl(url) {
  if (!url) return url;
  try {
    return new URL(url, window.location.origin).toString();
  } catch (_e) {
    return url;
  }
}

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
          const lines = fwds.map(f => f.relay_id + ':' + f.int_port + (f.int_port !== f.ext_port ? ' (ext ' + f.ext_port + ')' : '') + ' \u2192 ' + absoluteForwardUrl(f.url));
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
          addMsg('system', t('portForwardAdded', { relay: relayId, port: port, url: absoluteForwardUrl(resp.url) }));
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
        addBrowserTab(relayId + ':' + port, absoluteForwardUrl(match.url));
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
