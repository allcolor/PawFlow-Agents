'use strict';

const fs = require('fs');
const path = require('path');
const { randomUUID } = require('crypto');
const { WebContentsView, session, shell } = require('electron');
const {
  sameOrigin,
  safeExternalUrl,
  sanitizeDownloadName,
} = require('./url_policy');

function encodedForm(values) {
  return new URLSearchParams(values).toString();
}

function availableDownloadPath(directory, filename) {
  const parsed = path.parse(filename);
  let candidate = path.join(directory, filename);
  let suffix = 1;
  while (fs.existsSync(candidate)) {
    candidate = path.join(directory, `${parsed.name} (${suffix})${parsed.ext}`);
    suffix += 1;
  }
  return candidate;
}

class TabManager {
  constructor({ window, downloadsDirectory, onState, onAuthRequired, onDownload }) {
    this.window = window;
    this.downloadsDirectory = downloadsDirectory;
    this.onState = onState || (() => {});
    this.onAuthRequired = onAuthRequired || (() => {});
    this.onDownload = onDownload || (() => {});
    this.tabs = new Map();
    this.activeId = '';
    this.chromeHeight = 84;
    this.configuredPartitions = new Set();
    this.window.on('resize', () => this.layout());
  }

  partition(profileId) {
    return `persist:pawflow-chat-${profileId}`;
  }

  configureSession(profile) {
    const partition = this.partition(profile.id);
    const current = session.fromPartition(partition);
    if (this.configuredPartitions.has(partition)) return current;
    this.configuredPartitions.add(partition);
    current.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    current.setPermissionCheckHandler(() => false);
    current.on('will-download', (_event, item) => {
      try {
        const chain = typeof item.getURLChain === 'function' ? item.getURLChain() : [item.getURL()];
        if (!chain.length || chain.some(url => !sameOrigin(profile.base_url, url))) {
          item.cancel();
          throw new Error('The download left the configured PawFlow origin');
        }
        const filename = sanitizeDownloadName(item.getFilename());
        fs.mkdirSync(this.downloadsDirectory, { recursive: true });
        const savePath = availableDownloadPath(this.downloadsDirectory, filename);
        item.setSavePath(savePath);
        this.onDownload({ profileId: profile.id, state: 'started', filename, savePath });
        item.once('done', (_doneEvent, state) => {
          this.onDownload({ profileId: profile.id, state, filename, savePath });
        });
      } catch (error) {
        item.cancel();
        this.onDownload({ profileId: profile.id, state: 'cancelled', error: error.message });
      }
    });
    return current;
  }

  create(profile) {
    const id = randomUUID();
    const view = new WebContentsView({
      webPreferences: {
        partition: this.partition(profile.id),
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webSecurity: true,
        allowRunningInsecureContent: false,
      },
    });
    this.configureSession(profile);
    const tab = {
      id,
      profile,
      view,
      title: 'PawFlow',
      url: '',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    this.tabs.set(id, tab);
    this.window.contentView.addChildView(view);
    view.setVisible(false);

    view.webContents.setWindowOpenHandler(details => {
      if (sameOrigin(profile.base_url, details.url)) {
        this.openUrl(profile, details.url);
      } else {
        try {
          shell.openExternal(safeExternalUrl(details.url));
        } catch (_error) {
          // Unsupported schemes are intentionally ignored.
        }
      }
      return { action: 'deny' };
    });
    view.webContents.on('will-navigate', (event, targetUrl) => {
      if (!sameOrigin(profile.base_url, targetUrl)) {
        event.preventDefault();
        try {
          shell.openExternal(safeExternalUrl(targetUrl));
        } catch (_error) {
          // Unsupported schemes are intentionally ignored.
        }
        return;
      }
      const parsed = new URL(targetUrl);
      if (parsed.pathname === '/auth/login' || parsed.pathname.startsWith('/auth/login/')) {
        event.preventDefault();
        this.hide();
        this.onAuthRequired(profile.id);
      }
    });
    view.webContents.on('page-title-updated', (_event, title) => {
      tab.title = String(title || 'PawFlow').slice(0, 120);
      tab.updatedAt = new Date().toISOString();
      this.emitState(profile.id);
    });
    view.webContents.on('did-navigate', (_event, targetUrl) => {
      if (sameOrigin(profile.base_url, targetUrl)) tab.url = targetUrl;
      tab.updatedAt = new Date().toISOString();
      this.emitState(profile.id);
    });
    view.webContents.on('render-process-gone', (_event, details) => {
      this.onDownload({
        profileId: profile.id,
        state: 'renderer-gone',
        error: String(details.reason || 'unknown'),
      });
    });
    this.activate(id);
    return tab;
  }

  openPost(profile, route, form) {
    const tab = this.create(profile);
    const target = new URL(route, profile.base_url).toString();
    tab.view.webContents.loadURL(target, {
      postData: [{
        type: 'rawData',
        bytes: Buffer.from(form, 'utf8'),
      }],
      extraHeaders: 'Content-Type: application/x-www-form-urlencoded\r\n',
    });
    return tab.id;
  }

  openGateway(profile, gatewayKey, nextPath = '/chat') {
    return this.openPost(profile, '/_gateway', encodedForm({
      secret: gatewayKey,
      next: nextPath,
    }));
  }

  openHandoff(profile, handoffCode, verifier) {
    return this.openPost(profile, '/auth/mobile/consume', encodedForm({
      code: handoffCode,
      code_verifier: verifier,
    }));
  }

  openUrl(profile, target = '/chat') {
    const resolved = new URL(target, profile.base_url);
    if (!sameOrigin(profile.base_url, resolved)) throw new Error('Chat URL changed origin');
    const tab = this.create(profile);
    tab.view.webContents.loadURL(resolved.toString());
    return tab.id;
  }

  activate(id) {
    const tab = this.tabs.get(id);
    if (!tab) throw new Error('Chat tab not found');
    for (const item of this.tabs.values()) item.view.setVisible(item.id === id);
    this.activeId = id;
    this.layout();
    this.emitState(tab.profile.id);
  }

  close(id) {
    const tab = this.tabs.get(id);
    if (!tab) throw new Error('Chat tab not found');
    this.window.contentView.removeChildView(tab.view);
    tab.view.webContents.close();
    this.tabs.delete(id);
    if (this.activeId === id) {
      const next = Array.from(this.tabs.values()).find(item => item.profile.id === tab.profile.id)
        || Array.from(this.tabs.values())[0];
      this.activeId = next ? next.id : '';
      if (next) this.activate(next.id);
    }
    this.emitState(tab.profile.id);
  }

  hide() {
    for (const item of this.tabs.values()) item.view.setVisible(false);
  }

  show() {
    if (this.activeId && this.tabs.has(this.activeId)) {
      this.tabs.get(this.activeId).view.setVisible(true);
      this.layout();
    }
  }

  navigate(action) {
    const tab = this.tabs.get(this.activeId);
    if (!tab) throw new Error('No active chat tab');
    if (action === 'back' && tab.view.webContents.canGoBack()) tab.view.webContents.goBack();
    else if (action === 'forward' && tab.view.webContents.canGoForward()) tab.view.webContents.goForward();
    else if (action === 'reload') tab.view.webContents.reload();
    else if (!['back', 'forward', 'reload'].includes(action)) throw new Error('Unknown navigation action');
  }

  layout() {
    const tab = this.tabs.get(this.activeId);
    if (!tab || !tab.view.getVisible()) return;
    const [width, height] = this.window.getContentSize();
    tab.view.setBounds({
      x: 0,
      y: this.chromeHeight,
      width: Math.max(0, width),
      height: Math.max(0, height - this.chromeHeight),
    });
  }

  state(profileId = '') {
    const values = Array.from(this.tabs.values())
      .filter(tab => !profileId || tab.profile.id === profileId)
      .map(tab => ({
        id: tab.id,
        profile_id: tab.profile.id,
        title: tab.title,
        url: tab.url,
        created_at: tab.createdAt,
        updated_at: tab.updatedAt,
      }));
    return { active_tab_id: this.activeId, tabs: values };
  }

  emitState(profileId) {
    this.onState(profileId, this.state(profileId));
  }
}

module.exports = { TabManager, availableDownloadPath, encodedForm };
