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
  const panel = document.getElementById('tabContent_' + tabId);
  if (panel) {
    const relayId = panel.dataset.relayId;
    if (relayId) {
      fireAction('close_desktop', { relay_id: relayId });
    }
    panel.remove();
  }
  const btn = document.querySelector('.tab-btn[data-tab="' + tabId + '"]');
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
