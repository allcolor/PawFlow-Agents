// ── Tab management ──
// Vertical tab bar: Chat (permanent), Terminal tabs (multiple), VSCode (one per relay)

let _activeTab = 'chat';
let _terminalCounter = 0;

/** Switch to a tab by id. */
function switchTab(tabId) {
  _activeTab = tabId;
  // Update tab buttons
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  // Update content panels
  document.querySelectorAll('.tab-content').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.tab === tabId);
  });
  // Focus terminal if switching to one
  if (tabId.startsWith('term-')) {
    const container = document.querySelector(`#tabContent_${tabId} .xterm-container`);
    if (container && container._xterm) {
      setTimeout(() => container._xterm.focus(), 50);
    }
  }
}

/** Add a terminal tab. Returns the tab id. */
function addTerminalTab(sessionId, relayId) {
  _terminalCounter++;
  const tabId = 'term-' + sessionId;

  // Create tab button (insert before spacer)
  const btn = document.createElement('button');
  btn.className = 'tab-btn';
  btn.dataset.tab = tabId;
  btn.title = 'Terminal ' + _terminalCounter;
  btn.onclick = (e) => {
    if (e.target.classList.contains('tab-close')) return;
    switchTab(tabId);
  };
  btn.innerHTML = '<span style="font-family:monospace;font-weight:bold;font-size:13px">&gt;_</span>'
    + '<span class="tab-close" onclick="closeTerminalTab(\'' + tabId + '\')">&times;</span>';

  const spacer = document.querySelector('.tab-spacer');
  spacer.parentNode.insertBefore(btn, spacer);

  // Create content panel
  const panel = document.createElement('div');
  panel.className = 'tab-content';
  panel.id = 'tabContent_' + tabId;
  panel.dataset.tab = tabId;
  panel.dataset.sessionId = sessionId;
  panel.dataset.relayId = relayId;
  panel.style.cssText = 'background:#0f0f23;';

  // Terminal fills entire panel
  const termContainer = document.createElement('div');
  termContainer.className = 'xterm-container';
  termContainer.style.cssText = 'flex:1;overflow:hidden;padding:4px;';
  panel.appendChild(termContainer);

  document.querySelector('.main').appendChild(panel);

  // Switch to the new tab
  switchTab(tabId);
  return tabId;
}

/** Close a terminal tab. */
function closeTerminalTab(tabId) {
  const panel = document.getElementById('tabContent_' + tabId);
  if (panel) {
    const sessionId = panel.dataset.sessionId;
    // Clean up xterm WS
    const container = panel.querySelector('.xterm-container');
    if (container && container._ws) {
      try { container._ws.close(); } catch(e) {}
    }
    // Tell server to close the PTY
    if (sessionId) {
      fireAction('close_terminal', { session_id: sessionId, relay_id: '' });
    }
    panel.remove();
  }
  // Remove tab button
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  if (btn) btn.remove();
  // Switch to chat if this was the active tab
  if (_activeTab === tabId) switchTab('chat');
}

/** Add a VSCode tab (one per relay). Returns the tab id. */
function addVSCodeTab(relayId, iframeSrc) {
  const tabId = 'vscode-' + relayId;
  // If already exists for this relay, just switch to it
  if (document.getElementById('tabContent_' + tabId)) {
    switchTab(tabId);
    return tabId;
  }

  // Create tab button (insert before spacer)
  const btn = document.createElement('button');
  btn.className = 'tab-btn';
  btn.dataset.tab = tabId;
  btn.title = 'VS Code (' + relayId + ')';
  btn.onclick = (e) => {
    if (e.target.classList.contains('tab-close')) return;
    switchTab(tabId);
  };
  btn.innerHTML = '<span style="font-size:14px">\u2699</span>'
    + '<span class="tab-close" onclick="closeVSCodeTab(\'' + tabId + '\')">&times;</span>';

  const spacer = document.querySelector('.tab-spacer');
  spacer.parentNode.insertBefore(btn, spacer);

  // Create content panel with iframe
  const panel = document.createElement('div');
  panel.className = 'tab-content';
  panel.id = 'tabContent_' + tabId;
  panel.dataset.tab = tabId;
  panel.dataset.relayId = relayId;

  const iframe = document.createElement('iframe');
  iframe.src = iframeSrc;
  iframe.style.cssText = 'flex:1;border:none;width:100%;height:100%;';
  iframe.allow = 'clipboard-read; clipboard-write';
  panel.appendChild(iframe);

  document.querySelector('.main').appendChild(panel);
  switchTab(tabId);
  return tabId;
}

/** Close a VSCode tab. */
function closeVSCodeTab(tabId) {
  if (!tabId) tabId = 'vscode';
  const panel = document.getElementById('tabContent_' + tabId);
  if (panel) {
    const relayId = panel.dataset.relayId;
    if (relayId) {
      fireAction('close_code_server', { relay_id: relayId });
    }
    panel.remove();
  }
  const btn = document.querySelector('.tab-btn[data-tab="' + tabId + '"]');
  if (btn) btn.remove();
  if (_activeTab === tabId) switchTab('chat');
}

/** Add a Desktop tab (one per relay, iframe to noVNC). */
function addDesktopTab(relayId, iframeSrc) {
  const tabId = 'desktop-' + relayId;
  if (document.getElementById('tabContent_' + tabId)) {
    switchTab(tabId);
    return tabId;
  }

  const btn = document.createElement('button');
  btn.className = 'tab-btn';
  btn.dataset.tab = tabId;
  btn.title = 'Desktop (' + relayId + ')';
  btn.onclick = (e) => {
    if (e.target.classList.contains('tab-close')) return;
    switchTab(tabId);
  };
  btn.innerHTML = '<span style="font-size:14px">\uD83D\uDDA5</span>'
    + '<span class="tab-close" onclick="closeDesktopTab(\'' + tabId + '\')">\u00d7</span>';

  const spacer = document.querySelector('.tab-spacer');
  spacer.parentNode.insertBefore(btn, spacer);

  const panel = document.createElement('div');
  panel.className = 'tab-content';
  panel.id = 'tabContent_' + tabId;
  panel.dataset.tab = tabId;
  panel.dataset.relayId = relayId;
  panel.style.position = 'relative';

  const fsBtn = document.createElement('button');
  fsBtn.className = 'desktop-fs-btn';
  fsBtn.innerHTML = '\u26F6';
  fsBtn.title = 'Fullscreen (Escape to exit)';
  fsBtn.onclick = function() { toggleDesktopFullscreen(tabId); };
  panel.appendChild(fsBtn);

  const iframe = document.createElement('iframe');
  iframe.src = iframeSrc;
  iframe.style.cssText = 'flex:1;border:none;width:100%;height:100%;';
  iframe.allow = 'clipboard-read; clipboard-write';
  panel.appendChild(iframe);

  document.querySelector('.main').appendChild(panel);
  switchTab(tabId);
  return tabId;
}

/** Close a Desktop tab. */
function closeDesktopTab(tabId) {
  // Just close the tab locally — does NOT stop the desktop
  // Use /desktop stop to actually shut down the desktop
  const panel = document.getElementById('tabContent_' + tabId);
  if (panel) panel.remove();
  if (typeof audioDisconnect === 'function') audioDisconnect();
  const btn = document.querySelector('.tab-btn[data-tab="' + tabId + '"]');
  if (btn) btn.remove();
  if (_activeTab === tabId) switchTab('chat');
}

/** Add an Audio-only tab (minimal controls, no VNC). */
function addAudioTab(relayId, audioSession) {
  const tabId = 'audio-' + relayId;
  if (document.getElementById('tabContent_' + tabId)) {
    switchTab(tabId);
    return tabId;
  }

  const btn = document.createElement('button');
  btn.className = 'tab-btn';
  btn.dataset.tab = tabId;
  btn.title = 'Audio (' + relayId + ')';
  btn.onclick = (e) => {
    if (e.target.classList.contains('tab-close')) return;
    switchTab(tabId);
  };
  btn.innerHTML = '<span style="font-size:14px">\uD83D\uDD0A</span>'
    + '<span class="tab-close" onclick="closeAudioTab(\'' + tabId + '\')">&times;</span>';
  const spacer = document.querySelector('.tab-spacer');
  spacer.parentNode.insertBefore(btn, spacer);

  const panel = document.createElement('div');
  panel.className = 'tab-content';
  panel.id = 'tabContent_' + tabId;
  panel.dataset.tab = tabId;
  panel.dataset.relayId = relayId;
  panel.dataset.audioSession = audioSession;
  panel.innerHTML = '<div class="audio-tab-panel">'
    + '<div class="audio-tab-controls">'
    + '<div class="audio-title">\uD83D\uDD0A Audio \u2014 ' + relayId + '</div>'
    + '<div class="audio-btns">'
    + '<button onclick="toggleAudioMute()" id="audioTabMuteBtn">Mute</button>'
    + '<button onclick="audioRestart()">Restart</button>'
    + '</div>'
    + '<div class="audio-status">Streaming from relay</div>'
    + '</div></div>';

  document.querySelector('.main').appendChild(panel);
  switchTab(tabId);
  return tabId;
}

/** Close an Audio-only tab. */
function closeAudioTab(tabId) {
  const panel = document.getElementById('tabContent_' + tabId);
  if (panel) panel.remove();
  // Only disconnect audio if no desktop tab is using it
  if (!document.querySelector('[id^="tabContent_desktop-"]')) {
    if (typeof audioDisconnect === 'function') audioDisconnect();
  }
  const btn = document.querySelector('.tab-btn[data-tab="' + tabId + '"');
  if (btn) btn.remove();
  if (_activeTab === tabId) switchTab('chat');
}

/** Add a browser tab (iframe pointing to a URL). One per label. */
function addBrowserTab(label, iframeSrc) {
  const tabId = 'browse-' + label.replace(/[^a-zA-Z0-9._-]/g, '_');
  // If already exists, just switch to it
  if (document.getElementById('tabContent_' + tabId)) {
    switchTab(tabId);
    return tabId;
  }

  const btn = document.createElement('button');
  btn.className = 'tab-btn';
  btn.dataset.tab = tabId;
  btn.title = label;
  btn.onclick = (e) => {
    if (e.target.classList.contains('tab-close')) return;
    switchTab(tabId);
  };
  btn.innerHTML = '<span style="font-size:13px">\ud83c\udf10</span>'
    + '<span class="tab-close" onclick="closeBrowserTab(\'' + tabId + '\')">&times;</span>';

  const spacer = document.querySelector('.tab-spacer');
  spacer.parentNode.insertBefore(btn, spacer);

  const panel = document.createElement('div');
  panel.className = 'tab-content';
  panel.id = 'tabContent_' + tabId;
  panel.dataset.tab = tabId;

  const iframe = document.createElement('iframe');
  iframe.src = iframeSrc;
  iframe.style.cssText = 'flex:1;border:none;width:100%;height:100%;';
  iframe.allow = 'clipboard-read; clipboard-write';
  panel.appendChild(iframe);

  document.querySelector('.main').appendChild(panel);
  switchTab(tabId);
  return tabId;
}

/** Close a browser tab. */
function closeBrowserTab(tabId) {
  const panel = document.getElementById('tabContent_' + tabId);
  if (panel) panel.remove();
  const btn = document.querySelector('.tab-btn[data-tab="' + tabId + '"]');
  if (btn) btn.remove();
  if (_activeTab === tabId) switchTab('chat');
}

/** Toggle the action dropdown menu. */
function toggleActionMenu() {
  const menu = document.getElementById('actionMenu');
  menu.classList.toggle('open');
  if (menu.classList.contains('open')) {
    // Close on outside click
    setTimeout(() => {
      document.addEventListener('click', _closeActionMenuOutside, { once: true, capture: true });
    }, 0);
  }
}

function _closeActionMenuOutside(e) {
  const wrap = document.getElementById('actionMenuWrap');
  if (wrap && !wrap.contains(e.target)) {
    closeActionMenu();
  } else {
    // Re-register if click was inside
    setTimeout(() => {
      document.addEventListener('click', _closeActionMenuOutside, { once: true, capture: true });
    }, 0);
  }
}

function closeActionMenu() {
  const menu = document.getElementById('actionMenu');
  if (menu) menu.classList.remove('open');
}

/** Toggle desktop fullscreen mode. */
var _desktopFullscreenTab = null;
function toggleDesktopFullscreen(tabId) {
  if (document.body.classList.contains('desktop-fullscreen')) {
    // Exit fullscreen
    document.body.classList.remove('desktop-fullscreen');
    _desktopFullscreenTab = null;
    if (document.fullscreenElement) document.exitFullscreen().catch(function(){});
    var panel = document.getElementById('tabContent_' + tabId);
    if (panel) {
      var btn = panel.querySelector('.desktop-fs-btn');
      if (btn) btn.innerHTML = '\u26F6';
      var ifr = panel.querySelector('iframe');
      if (ifr) ifr.style.transform = '';
    }
  } else {
    // Enter fullscreen
    document.body.classList.add('desktop-fullscreen');
    _desktopFullscreenTab = tabId;
    document.documentElement.requestFullscreen().catch(function(){});
    var panel = document.getElementById('tabContent_' + tabId);
    if (panel) {
      var btn = panel.querySelector('.desktop-fs-btn');
      if (btn) btn.innerHTML = '\u2716';
      // Scale iframe to cover viewport (eliminate bands from aspect ratio mismatch)
      var ifr = panel.querySelector('iframe');
      if (ifr) {
        var vr = screen.width / screen.height;
        var dr = 1280 / 800; // desktop ratio
        var s = Math.max(vr, dr) / Math.min(vr, dr);
        if (s > 1.01) ifr.style.transform = 'scale(' + s + ')';
      }
    }
  }
}

// Exit desktop fullscreen when browser exits fullscreen (Escape key)
document.addEventListener('fullscreenchange', function() {
  if (!document.fullscreenElement && document.body.classList.contains('desktop-fullscreen')) {
    document.body.classList.remove('desktop-fullscreen');
    if (_desktopFullscreenTab) {
      var panel = document.getElementById('tabContent_' + _desktopFullscreenTab);
      if (panel) {
        var btn = panel.querySelector('.desktop-fs-btn');
        if (btn) btn.innerHTML = '\u26F6';
        var ifr = panel.querySelector('iframe');
        if (ifr) ifr.style.transform = '';
      }
    }
    _desktopFullscreenTab = null;
  }
});
