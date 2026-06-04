const { app, BrowserWindow, ipcMain, dialog, Menu, Tray, nativeImage } = require('electron');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

if (process.platform === 'win32') {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch('disable-gpu-sandbox');
  app.commandLine.appendSwitch('use-angle', 'swiftshader');
  app.commandLine.appendSwitch('enable-unsafe-swiftshader');
}

const runningRelays = new Map();
let mainWindow = null;
let tray = null;
let isQuitting = false;
let shutdownComplete = false;
let shutdownPromise = null;

function pythonCommand() {
  return process.env.PAWFLOW_RELAY_PYTHON || process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
}

function relayBinaryName() {
  return process.platform === 'win32' ? 'pawflow-relay.exe' : 'pawflow-relay';
}

function appRoot() {
  return path.resolve(__dirname, '..');
}

function repoRoot() {
  if (app.isPackaged) return runtimeRoot();
  return path.resolve(__dirname, '..', '..');
}

function runtimeRoot() {
  if (process.env.PAWFLOW_RELAY_RUNTIME_ROOT) return process.env.PAWFLOW_RELAY_RUNTIME_ROOT;
  if (app.isPackaged) return path.join(process.resourcesPath, 'runtime');
  const localRuntime = path.join(appRoot(), 'runtime');
  return localRuntime;
}

function relayBinaryPath() {
  const candidates = [];
  if (process.env.PAWFLOW_RELAY_BIN) candidates.push(process.env.PAWFLOW_RELAY_BIN);
  candidates.push(path.join(runtimeRoot(), 'bin', relayBinaryName()));
  return firstExistingPath(candidates);
}

function relayClientCommand(extraArgs = []) {
  const binary = relayBinaryPath();
  if (binary) {
    return {
      command: binary,
      args: extraArgs,
      cwd: runtimeRoot(),
      env: pythonEnv(),
      description: binary,
    };
  }
  return {
    command: pythonCommand(),
    args: ['-m', 'pawflow_relay', ...extraArgs],
    cwd: repoRoot(),
    env: pythonEnv(),
    description: `${pythonCommand()} -m pawflow_relay`,
  };
}

function pythonRoots() {
  const roots = [runtimeRoot(), repoRoot()];
  return Array.from(new Set(roots));
}

function pythonEnv() {
  const env = { ...process.env };
  const roots = pythonRoots();
  env.PAWFLOW_RELAY_RUNTIME_ROOT = runtimeRoot();
  env.PYTHONPATH = env.PYTHONPATH
    ? `${roots.join(path.delimiter)}${path.delimiter}${env.PYTHONPATH}`
    : roots.join(path.delimiter);
  return env;
}

function runRelayClientJson(args = []) {
  return new Promise((resolve, reject) => {
    const relay = relayClientCommand(['--json', ...args]);
    const proc = spawn(relay.command, relay.args, {
      cwd: relay.cwd,
      env: relay.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', chunk => { stdout += chunk.toString(); });
    proc.stderr.on('data', chunk => { stderr += chunk.toString(); });
    proc.on('error', reject);
    proc.on('close', code => {
      if (code !== 0) {
        reject(new Error((stderr || stdout || `${relay.description} exited ${code}`).trim()));
        return;
      }
      try {
        resolve(stdout.trim() ? JSON.parse(stdout) : null);
      } catch (err) {
        reject(new Error(`Invalid JSON from ${relay.description}: ${err.message}\n${stdout}`));
      }
    });
  });
}

function runCommand(command, args = [], options = {}) {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, {
      cwd: options.cwd || repoRoot(),
      env: { ...process.env, ...(options.env || {}) },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    const logName = options.logName || '';
    proc.stdout.on('data', chunk => {
      const text = chunk.toString();
      stdout += text;
      if (logName) appendLog(logName, text);
    });
    proc.stderr.on('data', chunk => {
      const text = chunk.toString();
      stderr += text;
      if (logName) appendLog(logName, text);
    });
    proc.on('error', reject);
    proc.on('close', code => {
      if (code === 0) {
        resolve(stdout);
      } else {
        reject(new Error((stderr || stdout || `${command} exited ${code}`).trim()));
      }
    });
  });
}

function firstExistingPath(candidates) {
  return candidates.find(candidate => fs.existsSync(candidate));
}

function dockerCommand() {
  return process.env.PAWFLOW_RELAY_DOCKER || 'docker';
}

function wslBaseArgs() {
  const distro = process.env.PAWFLOW_RELAY_WSL_DISTRO || '';
  return distro ? ['-d', distro, '--'] : ['--'];
}

function dockerConnectError(message) {
  const text = String(message || '').toLowerCase();
  return (
    text.includes('dockerdesktoplinuxengine')
    || text.includes('npipe:')
    || text.includes('pipe/docker_engine')
    || text.includes('cannot connect to the docker daemon')
    || text.includes('failed to connect to the docker api')
    || text.includes('the system cannot find the file specified')
    || text.includes('enoent')
  );
}

async function runWslCommand(args, options = {}) {
  return runCommand('wsl.exe', [...wslBaseArgs(), ...args], options);
}

async function wslPath(winPath) {
  if (process.platform !== 'win32') return winPath;
  return (await runWslCommand(['wslpath', '-a', winPath])).trim();
}

async function runDocker(args, options = {}) {
  if (process.env.PAWFLOW_RELAY_DOCKER) {
    return runCommand(dockerCommand(), args, options);
  }
  try {
    return await runCommand('docker', args, options);
  } catch (err) {
    if (process.platform !== 'win32' || !dockerConnectError(err.message)) {
      throw err;
    }
    appendLog('docker', '[docker] Windows Docker unavailable; trying WSL docker\n');
    return runWslCommand(['docker', ...args], options);
  }
}

async function runDockerBuild(imageName, contextDir, options = {}) {
  const args = ['build', '-t', imageName, contextDir];
  if (process.env.PAWFLOW_RELAY_DOCKER) {
    return runCommand(dockerCommand(), args, options);
  }
  try {
    return await runCommand('docker', args, options);
  } catch (err) {
    if (process.platform !== 'win32' || !dockerConnectError(err.message)) {
      throw err;
    }
    appendLog('docker', '[docker] Windows Docker unavailable; trying WSL docker\n');
    const wslContextDir = await wslPath(contextDir);
    return runWslCommand(['docker', 'build', '-t', imageName, wslContextDir], options);
  }
}

function relayImageCatalogPath() {
  const found = firstExistingPath([
    path.join(runtimeRoot(), 'config', 'relay_image_catalog.json'),
    path.join(repoRoot(), 'config', 'relay_image_catalog.json'),
  ]);
  if (!found) throw new Error('Relay image catalog not found');
  return found;
}

function relayImageGeneratorPath() {
  const found = firstExistingPath([
    path.join(runtimeRoot(), 'scripts', 'generate-relay-image.py'),
    path.join(repoRoot(), 'scripts', 'generate-relay-image.py'),
  ]);
  if (!found) throw new Error('Relay image generator not found');
  return found;
}

function loadRelayImageCatalog() {
  return JSON.parse(fs.readFileSync(relayImageCatalogPath(), 'utf8'));
}

function defaultRelayImageName() {
  const repo = process.env.PAWFLOW_RELAY_DEV_IMAGE_REPO || 'ghcr.io/allcolor/pawflow-relay-dev';
  const tag = process.env.PAWFLOW_RELAY_IMAGE_TAG || process.env.PAWFLOW_VERSION || 'latest';
  return `${repo}:${tag}`;
}

async function listDockerImages() {
  try {
    const out = await runDocker([
      'images',
      '--format',
      '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}',
    ]);
    const images = out.split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => {
        const [repository, tag, id, size] = line.split('\t');
        return { name: `${repository}:${tag}`, repository, tag, id, size };
      })
      .filter(image => image.repository && image.tag && image.repository !== '<none>' && image.tag !== '<none>')
      .sort((a, b) => a.name.localeCompare(b.name));
    return { images, error: '' };
  } catch (err) {
    const message = (err && err.message) ? err.message : String(err);
    appendLog('docker', `[docker] ${message}\n`);
    return { images: [], error: message };
  }
}

function safeImageBuildName(imageName) {
  return imageName.replace(/[^a-zA-Z0-9_.-]+/g, '-').replace(/^-+|-+$/g, '') || 'relay-image';
}

function validateDockerImageName(imageName) {
  if (!imageName || !/^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$/.test(imageName)) {
    throw new Error('Docker image name must look like pawflow-relay-custom:latest');
  }
}

function normalizeRelayImageName(imageName) {
  return String(imageName || '').trim() || defaultRelayImageName();
}

async function buildRelayImage(input) {
  const imageName = normalizeRelayImageName(input.imageName);
  validateDockerImageName(imageName);
  const profile = String(input.profile || '');
  const features = Array.isArray(input.features) ? input.features.map(String).filter(Boolean) : [];
  const buildRoot = path.join(app.getPath('userData'), 'relay-image-builds');
  const outDir = path.join(buildRoot, safeImageBuildName(imageName));
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(buildRoot, { recursive: true });

  appendLog('image-build', `Generating relay image context for ${imageName}\n`);
  const generateArgs = [
    relayImageGeneratorPath(),
    '--catalog', relayImageCatalogPath(),
    '--profile', profile,
    '--out', outDir,
    '--image', imageName,
  ];
  for (const feature of features) {
    generateArgs.push('--feature', feature);
  }
  await runCommand(pythonCommand(), generateArgs, { cwd: repoRoot(), env: pythonEnv(), logName: 'image-build' });

  appendLog('image-build', `Building Docker image ${imageName}\n`);
  await runDockerBuild(imageName, outDir, { logName: 'image-build' });
  appendLog('image-build', 'Pruning Docker build cache\n');
  try {
    await runDocker(['builder', 'prune', '-f'], { logName: 'image-build' });
  } catch (err) {
    appendLog('image-build', `[prune] ${err.message}\n`);
  } finally {
    fs.rmSync(outDir, { recursive: true, force: true });
  }
  return { ok: true, image: imageName };
}

async function relayImageIds() {
  const out = await runDocker(['images', '--format', '{{.Repository}}\t{{.Tag}}\t{{.ID}}']);
  return out.split('\n')
    .map(line => line.trim().split('\t'))
    .filter(parts => parts.length === 3)
    .filter(([repo, tag]) => repo && tag && repo !== '<none>' && tag !== '<none>')
    .filter(([repo]) => repo === 'ghcr.io/allcolor/pawflow-relay-dev'
      || repo === 'ghcr.io/allcolor/pawflow-relay-minimal')
    .map(([, , id]) => id);
}

async function cleanupOldRelayImages(currentImage, oldIds) {
  const keep = new Set([currentImage, 'pawflow-relay-dev:latest']);
  appendLog('image-download', 'Cleaning older PawFlow relay image tags\n');
  const out = await runDocker(['images', '--format', '{{.Repository}}\t{{.Tag}}\t{{.ID}}']);
  for (const line of out.split('\n')) {
    const [repo, tag] = line.trim().split('\t');
    if (!repo || !tag || repo === '<none>' || tag === '<none>') continue;
    if (repo !== 'ghcr.io/allcolor/pawflow-relay-dev'
        && repo !== 'ghcr.io/allcolor/pawflow-relay-minimal') continue;
    const ref = `${repo}:${tag}`;
    if (keep.has(ref)) continue;
    appendLog('image-download', `Removing old image tag ${ref}\n`);
    try {
      await runDocker(['rmi', ref], { logName: 'image-download' });
    } catch (err) {
      appendLog('image-download', `[cleanup] ${err.message}\n`);
    }
  }
  const currentIds = new Set((await runDocker(['images', '--format', '{{.ID}}'])).split('\n').map(line => line.trim()).filter(Boolean));
  for (const id of Array.from(new Set(oldIds || []))) {
    if (!id || !currentIds.has(id)) continue;
    let hasTag = true;
    try {
      const tag = (await runDocker(['image', 'inspect', '-f', '{{index .RepoTags 0}}', id])).trim();
      hasTag = Boolean(tag && tag !== '<no value>' && !tag.startsWith('<none>'));
    } catch (_err) {
      hasTag = false;
    }
    if (hasTag) continue;
    appendLog('image-download', `Removing old untagged PawFlow relay image id ${id}\n`);
    try {
      await runDocker(['rmi', '-f', id], { logName: 'image-download' });
    } catch (err) {
      appendLog('image-download', `[cleanup] ${err.message}\n`);
    }
  }
  try {
    await runDocker(['image', 'prune', '-f', '--filter', 'dangling=true'], { logName: 'image-download' });
  } catch (err) {
    appendLog('image-download', `[prune] ${err.message}\n`);
  }
}

async function downloadRelayImage(input = {}) {
  const imageName = normalizeRelayImageName(input.imageName);
  validateDockerImageName(imageName);
  const oldIds = await relayImageIds();
  appendLog('image-download', `Pulling Docker image ${imageName}\n`);
  await runDocker(['pull', imageName], { logName: 'image-download' });
  if (imageName.startsWith('ghcr.io/allcolor/pawflow-relay-dev:')) {
    appendLog('image-download', `Tagging ${imageName} as pawflow-relay-dev:latest\n`);
    await runDocker(['tag', imageName, 'pawflow-relay-dev:latest'], { logName: 'image-download' });
  }
  await cleanupOldRelayImages(imageName, oldIds);
  return { ok: true, image: imageName };
}

function appendLog(name, text) {
  const win = mainWindow || BrowserWindow.getAllWindows()[0];
  if (!win || win.isDestroyed()) return;
  const contents = win.webContents;
  if (!contents || contents.isDestroyed()) return;
  try {
    contents.send('relay-log', { name, text });
  } catch (err) {
    if (!String(err && err.message || err).includes('Render frame was disposed')) {
      console.error('Error sending relay log:', err);
    }
  }
}

function getRelayState() {
  return runRelayClientJson(['status']);
}

function waitForProcessExit(proc, timeoutMs) {
  return new Promise(resolve => {
    if (!proc || proc.exitCode !== null || proc.signalCode !== null) {
      resolve(true);
      return;
    }
    let done = false;
    const finish = exited => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      proc.off('close', onClose);
      proc.off('exit', onClose);
      resolve(exited);
    };
    const onClose = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    proc.once('close', onClose);
    proc.once('exit', onClose);
  });
}

function cleanupRelayRuntime(name) {
  return runRelayClientJson(['cleanup', name || '']);
}

function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  mainWindow.show();
  mainWindow.focus();
}

function loginServer(name) {
  return new Promise((resolve, reject) => {
    const relay = relayClientCommand(['server', 'login', name]);
    const proc = spawn(relay.command, relay.args, {
      cwd: relay.cwd,
      env: relay.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '';
    proc.stdout.on('data', chunk => {
      const text = chunk.toString();
      output += text;
      appendLog(`server:${name}`, text);
    });
    proc.stderr.on('data', chunk => {
      const text = chunk.toString();
      output += text;
      appendLog(`server:${name}`, text);
    });
    proc.on('error', reject);
    proc.on('close', code => {
      if (code === 0) {
        resolve({ ok: true, output });
      } else {
        reject(new Error(output || `Login failed with exit code ${code}`));
      }
    });
  });
}

function startRelay(name) {
  if (runningRelays.has(name)) {
    return { ok: true, alreadyRunning: true };
  }
  const relay = relayClientCommand(['start', name]);
  const proc = spawn(relay.command, relay.args, {
    cwd: relay.cwd,
    env: relay.env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const entry = { proc, stopRequested: false };
  runningRelays.set(name, entry);
  appendLog(name, `[relay] starting ${name}\n`);
  proc.stdout.on('data', chunk => appendLog(name, chunk.toString()));
  proc.stderr.on('data', chunk => appendLog(name, chunk.toString()));
  proc.on('error', err => appendLog(name, `[relay] error: ${err.message}\n`));
  proc.on('close', code => {
    if (runningRelays.get(name) === entry) {
      runningRelays.delete(name);
    }
    const suffix = entry.stopRequested ? ' after stop request' : '';
    appendLog(name, `[relay] exited with code ${code}${suffix}\n`);
    refreshTrayMenu();
  });
  refreshTrayMenu();
  return { ok: true };
}

async function stopRelay(name) {
  const entry = runningRelays.get(name);
  const proc = entry && entry.proc;
  if (entry && proc) {
    entry.stopRequested = true;
    appendLog(name, `[relay] stop requested\n`);
    try {
      proc.kill(process.platform === 'win32' ? 'SIGTERM' : 'SIGINT');
    } catch (err) {
      appendLog(name, `[relay] process stop failed: ${err.message}\n`);
    }
    const exited = await waitForProcessExit(proc, 5000);
    if (!exited) {
      appendLog(name, `[relay] process did not exit after SIGTERM; killing\n`);
      try {
        proc.kill('SIGKILL');
      } catch (err) {
        appendLog(name, `[relay] process kill failed: ${err.message}\n`);
      }
      await waitForProcessExit(proc, 2000);
    }
    if (runningRelays.get(name) === entry) {
      runningRelays.delete(name);
    }
  }
  try {
    const cleanup = await cleanupRelayRuntime(name);
    appendLog(name, `[relay] runtime cleanup: ${JSON.stringify(cleanup)}\n`);
  } catch (err) {
    appendLog(name, `[relay] runtime cleanup failed: ${err.message}\n`);
  }
  refreshTrayMenu();
  return { ok: true, alreadyStopped: !proc };
}

async function stopAllRelays() {
  if (shutdownPromise) return shutdownPromise;
  shutdownPromise = Promise.all(Array.from(runningRelays.keys()).map(name => stopRelay(name)));
  return shutdownPromise;
}

async function quitApp() {
  isQuitting = true;
  await stopAllRelays();
  shutdownComplete = true;
  app.quit();
}

function trayIcon() {
  const iconPath = path.join(__dirname, 'assets', 'tray-icon.png');
  const image = nativeImage.createFromPath(iconPath);
  if (!image.isEmpty()) {
    return image.resize({ width: 16, height: 16 });
  }
  return nativeImage.createFromDataURL('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAANUlEQVR42mP8z8Dwn4ECwESJ5lEDRgYGBob/DAwM/5kYGBj+MzAwMGQANcS0cWQ8FAAAAABJRU5ErkJggg==');
}

async function refreshTrayMenu() {
  if (!tray) return;
  let state = { servers: [], workspaces: [] };
  try {
    state = await getRelayState();
  } catch (err) {
    appendLog('tray', `[tray] ${err.message}\n`);
  }
  const running = new Set(runningRelays.keys());
  const serverItems = (state.servers || []).length
    ? (state.servers || []).map(server => {
        const status = server.logged_in ? ' (logged in)' : ' (login needed)';
        return {
          label: `${server.name}${status}`,
          submenu: [
            { label: 'Login', click: () => loginServer(server.name).then(refreshTrayMenu).catch(err => appendLog(`server:${server.name}`, `${err.message}\n`)) },
            { label: 'Open GUI', click: showMainWindow },
          ],
        };
      })
    : [{ label: 'No server configured', enabled: false }];
  const relayItems = (state.workspaces || []).length
    ? (state.workspaces || []).map(workspace => {
        const active = running.has(workspace.name);
        const status = active ? ' (running)' : '';
        return {
          label: `${workspace.name}${status}`,
          submenu: [
            { label: 'Start', enabled: !active, click: () => startRelay(workspace.name) },
            { label: 'Stop', enabled: active, click: () => stopRelay(workspace.name).catch(err => appendLog(workspace.name, `${err.message}\n`)) },
            { label: 'Open GUI', click: showMainWindow },
          ],
        };
      })
    : [{ label: 'No relay configured', enabled: false }];
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open PawFlow Relay', click: showMainWindow },
    { type: 'separator' },
    { label: 'Relays', submenu: relayItems },
    { label: 'Servers', submenu: serverItems },
    { type: 'separator' },
    { label: 'Quit', click: () => quitApp().catch(err => {
      appendLog('tray', `[quit] ${err.message}\n`);
      shutdownComplete = true;
      app.quit();
    }) },
  ]));
}

function createTray() {
  if (tray) return;
  tray = new Tray(trayIcon());
  tray.setToolTip('PawFlow Relay Desktop');
  tray.on('click', showMainWindow);
  tray.on('right-click', refreshTrayMenu);
  refreshTrayMenu();
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1040,
    height: 720,
    minWidth: 860,
    minHeight: 560,
    title: 'PawFlow Relay Desktop',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow = win;
  win.on('close', event => {
    if (isQuitting) return;
    event.preventDefault();
    win.hide();
  });
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null;
  });
  win.loadFile(path.join(__dirname, 'index.html'));
}

ipcMain.handle('relay:list', async () => getRelayState());

ipcMain.handle('relay:add-server', async (_event, input) => {
  const result = await runRelayClientJson([
    'server', 'add', input.name || '', input.url || '',
    '--gateway-key', input.gatewayKey || '',
  ]);
  refreshTrayMenu();
  return result;
});

ipcMain.handle('relay:delete-server', async (_event, name) => {
  const result = await runRelayClientJson(['server', 'delete', name || '']);
  refreshTrayMenu();
  return result;
});

ipcMain.handle('relay:login-server', async (_event, name) => {
  const result = await loginServer(name);
  refreshTrayMenu();
  return result;
});

ipcMain.handle('relay:add-workspace', async (_event, input) => {
  const args = [
    'workspace', 'add', input.name || '',
    '--server', input.server || '',
    '--path', input.path || '',
    '--mode', input.mode || 'rw',
    '--docker-image', input.dockerImage || 'pawflow-relay-dev:latest',
  ];
  if (Boolean(input.allowLocal)) args.push('--allow-local');
  if (!Boolean(input.allowExec)) args.push('--no-exec');
  if (input.allowRemoteDesktop === false) args.push('--no-remote-desktop');
  const result = await runRelayClientJson(args);
  refreshTrayMenu();
  return result;
});

ipcMain.handle('relay:delete-workspace', async (_event, name) => {
  const result = await runRelayClientJson(['workspace', 'delete', name || '']);
  refreshTrayMenu();
  return result;
});

ipcMain.handle('relay:start', async (_event, name) => startRelay(name));

ipcMain.handle('relay:stop', async (_event, name) => stopRelay(name));

ipcMain.handle('relay:running', async () => {
  return Array.from(runningRelays.keys());
});

ipcMain.handle('relay:select-directory', async (_event, currentPath) => {
  const result = await dialog.showOpenDialog({
    title: 'Select workspace directory',
    defaultPath: currentPath || undefined,
    properties: ['openDirectory', 'createDirectory'],
  });
  if (result.canceled || !result.filePaths.length) return '';
  return result.filePaths[0];
});

ipcMain.handle('relay:docker-images', async () => listDockerImages());

ipcMain.handle('relay:image-catalog', async () => loadRelayImageCatalog());

ipcMain.handle('relay:build-image', async (_event, input) => buildRelayImage(input || {}));

ipcMain.handle('relay:download-image', async (_event, input) => downloadRelayImage(input || {}));

app.whenReady().then(() => {
  createWindow();
  createTray();
});

app.on('before-quit', event => {
  isQuitting = true;
  if (shutdownComplete) return;
  event.preventDefault();
  quitApp().catch(err => {
    appendLog('quit', `[quit] ${err.message}\n`);
    shutdownComplete = true;
    app.quit();
  });
});

app.on('window-all-closed', () => {
  // Keep the app alive in the tray until the user chooses Quit.
});

app.on('activate', showMainWindow);
