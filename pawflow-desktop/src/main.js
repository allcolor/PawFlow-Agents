'use strict';

const path = require('path');
const {
  app,
  BrowserWindow,
  ipcMain,
  net,
  safeStorage,
  session,
  shell,
} = require('electron');
const { ProfileStore } = require('./profile_store');
const { AuthClient } = require('./auth');
const { createPkce } = require('./pkce');
const { TabManager } = require('./tab_manager');
const { parseDeepLink, safeExternalUrl } = require('./url_policy');

let mainWindow = null;
let profileStore = null;
let authClient = null;
let tabManager = null;
const pendingLinks = [];

function publicProfile(profile) {
  const { secret_ref: _secretRef, ...safe } = profile;
  return safe;
}

function send(channel, value) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, value);
}

function errorMessage(error) {
  return error && error.message ? error.message : String(error);
}

async function providersFor(profileId) {
  const profile = profileStore.get(profileId);
  const gatewayKey = profileStore.gatewayKey(profileId);
  const response = await authClient.providers(profile, gatewayKey);
  return Array.isArray(response.providers) ? response.providers : [];
}

async function handleAuthRequired(profileId) {
  try {
    send('auth:required', {
      profile: publicProfile(profileStore.get(profileId)),
      providers: await providersFor(profileId),
    });
  } catch (error) {
    send('app:error', errorMessage(error));
  }
}

async function handleDeepLink(raw) {
  if (!profileStore || !tabManager) {
    pendingLinks.push(raw);
    return;
  }
  try {
    const link = parseDeepLink(raw);
    if (link.action === 'oauth') {
      const pending = profileStore.loadPendingOAuth();
      if (!pending || pending.flowId !== link.flowId) {
        throw new Error('OAuth callback does not match the pending login');
      }
      const profile = profileStore.get(pending.profileId);
      profileStore.clearPendingOAuth();
      if (link.error) throw new Error(link.error);
      tabManager.openHandoff(profile, link.code, pending.verifier);
      tabManager.show();
      send('auth:oauth-complete', { profile: publicProfile(profile), tabs: tabManager.state(profile.id) });
      return;
    }
    const profile = profileStore.get(link.serverId);
    const gatewayKey = profileStore.gatewayKey(profile.id);
    tabManager.openGateway(profile, gatewayKey, link.path);
    tabManager.show();
    send('chat:tabs', tabManager.state(profile.id));
  } catch (error) {
    send('app:error', errorMessage(error));
  }
}

function configureIpc() {
  ipcMain.handle('profile:list', () => profileStore.list().map(publicProfile));
  ipcMain.handle('profile:save', (_event, input) => publicProfile(profileStore.save(input || {})));
  ipcMain.handle('profile:remove', async (_event, id) => {
    const profile = profileStore.get(id);
    profileStore.remove(id);
    await session.fromPartition(tabManager.partition(id)).clearStorageData();
    return publicProfile(profile);
  });
  ipcMain.handle('profile:connect', async (_event, id) => {
    const profile = profileStore.get(id);
    const gatewayKey = profileStore.gatewayKey(id);
    const providers = await providersFor(id);
    tabManager.openGateway(profile, gatewayKey, '/chat');
    tabManager.show();
    return { profile: publicProfile(profile), providers, tabs: tabManager.state(id) };
  });
  ipcMain.handle('auth:builtin', async (_event, input) => {
    const profile = profileStore.get(input.profileId);
    const gatewayKey = profileStore.gatewayKey(profile.id);
    const pkce = createPkce();
    const result = await authClient.builtin(
      profile, gatewayKey, input.username, input.password, pkce.challenge);
    tabManager.openHandoff(profile, result.handoff_code, pkce.verifier);
    tabManager.show();
    return { profile: publicProfile(profile), tabs: tabManager.state(profile.id) };
  });
  ipcMain.handle('auth:oauth', async (_event, input) => {
    const profile = profileStore.get(input.profileId);
    const gatewayKey = profileStore.gatewayKey(profile.id);
    const pkce = createPkce();
    const result = await authClient.startOAuth(
      profile, gatewayKey, input.provider, pkce.challenge);
    profileStore.savePendingOAuth({
      profileId: profile.id,
      flowId: result.flow_id,
      verifier: pkce.verifier,
    });
    await shell.openExternal(safeExternalUrl(result.authorization_url));
    return { started: true, profile: publicProfile(profile) };
  });
  ipcMain.handle('chat:add', (_event, profileId) => {
    const profile = profileStore.get(profileId);
    tabManager.openUrl(profile, '/chat');
    tabManager.show();
    return tabManager.state(profile.id);
  });
  ipcMain.handle('chat:activate', (_event, tabId) => {
    tabManager.activate(tabId);
    tabManager.show();
    return tabManager.state();
  });
  ipcMain.handle('chat:close', (_event, tabId) => {
    tabManager.close(tabId);
    return tabManager.state();
  });
  ipcMain.handle('chat:navigate', (_event, action) => {
    tabManager.navigate(action);
    return true;
  });
  ipcMain.handle('chat:show', () => {
    tabManager.show();
    return tabManager.state();
  });
  ipcMain.handle('chat:hide', () => {
    tabManager.hide();
    return true;
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 760,
    minHeight: 540,
    backgroundColor: '#111827',
    title: 'PawFlow Desktop',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });
  mainWindow.removeMenu();
  mainWindow.loadFile(path.join(__dirname, 'index.html'));
  mainWindow.webContents.setWindowOpenHandler(details => {
    try {
      shell.openExternal(safeExternalUrl(details.url));
    } catch (_error) {
      // Local chrome never opens non-HTTPS external URLs.
    }
    return { action: 'deny' };
  });

  profileStore = new ProfileStore({
    root: path.join(app.getPath('userData'), 'state'),
    safeStorage,
  });
  authClient = new AuthClient((url, options) => net.fetch(url, options));
  tabManager = new TabManager({
    window: mainWindow,
    downloadsDirectory: app.getPath('downloads'),
    onState: (profileId, state) => {
      if (profileId) profileStore.saveTabState(profileId, state);
      send('chat:tabs', state);
    },
    onAuthRequired: handleAuthRequired,
    onDownload: value => send('chat:download', value),
  });
  configureIpc();
  while (pendingLinks.length) handleDeepLink(pendingLinks.shift());

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

if (!app.isDefaultProtocolClient('pawflow')) app.setAsDefaultProtocolClient('pawflow');

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) {
  app.quit();
} else {
  app.on('second-instance', (_event, argv) => {
    const link = argv.find(value => value.startsWith('pawflow://'));
    if (link) handleDeepLink(link);
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.on('open-url', (event, value) => {
    event.preventDefault();
    handleDeepLink(value);
  });
  app.whenReady().then(createWindow);
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
}
