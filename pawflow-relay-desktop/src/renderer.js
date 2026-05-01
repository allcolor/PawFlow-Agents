const state = {
  servers: [],
  workspaces: [],
  dockerImages: [],
  imageCatalog: null,
  running: new Set(),
  selected: { type: 'home', name: '' },
};

const $ = selector => document.querySelector(selector);

function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.style.borderColor = isError ? '#bd3c50' : '#37527e';
  el.classList.add('visible');
  setTimeout(() => el.classList.remove('visible'), 3200);
}

function appendLog(name, text) {
  const logs = $('#logs');
  logs.textContent += `[${name}] ${text}`;
  logs.scrollTop = logs.scrollHeight;
}

function setSelected(type, name = '') {
  state.selected = { type, name };
  render();
}

async function refresh() {
  const data = await window.pawflowRelay.list();
  state.servers = data.servers || [];
  state.workspaces = data.workspaces || [];
  state.running = new Set(await window.pawflowRelay.running());
  try {
    state.dockerImages = await window.pawflowRelay.listDockerImages();
  } catch (err) {
    state.dockerImages = [];
    appendLog('docker', `[docker] ${err.message}\n`);
  }
  try {
    state.imageCatalog = await window.pawflowRelay.relayImageCatalog();
  } catch (err) {
    appendLog('image-build', `[catalog] ${err.message}\n`);
  }
  if (state.selected.name) {
    const exists = state.selected.type === 'server'
      ? state.servers.some(s => s.name === state.selected.name)
      : state.workspaces.some(w => w.name === state.selected.name);
    if (!exists) state.selected = { type: 'home', name: '' };
  }
  render();
}

function render() {
  renderTree();
  renderPanel();
}

function renderTree() {
  const serverRoot = $('#serverTree');
  const workspaceRoot = $('#workspaceTree');
  serverRoot.innerHTML = '';
  workspaceRoot.innerHTML = '';

  if (!state.servers.length) {
    serverRoot.innerHTML = '<div class="empty-tree">No server</div>';
  }
  for (const server of state.servers) {
    serverRoot.appendChild(treeItem({
      type: 'server',
      name: server.name,
      label: server.name,
      meta: server.session_token ? 'logged in' : 'login needed',
      ok: Boolean(server.session_token),
    }));
  }

  if (!state.workspaces.length) {
    workspaceRoot.innerHTML = '<div class="empty-tree">No relay</div>';
  }
  for (const share of state.workspaces) {
    const running = state.running.has(share.name);
    workspaceRoot.appendChild(treeItem({
      type: 'workspace',
      name: share.name,
      label: share.name,
      meta: running ? 'running' : `${share.server} / ${share.mode || 'rw'}`,
      ok: running,
      run: running,
    }));
  }
}

function treeItem(item) {
  const button = document.createElement('button');
  const selectedClass = isSelected(item.type, item.name) ? 'selected' : '';
  const runClass = item.run ? 'run' : '';
  button.className = `tree-item ${selectedClass}`;
  button.innerHTML = `
    <span class="dot ${runClass}"></span>
    <span class="name">${escapeHtml(item.label)}</span>
    <span class="meta">${escapeHtml(item.meta)}</span>
  `;
  button.addEventListener('click', () => setSelected(item.type, item.name));
  button.addEventListener('contextmenu', event => {
    event.preventDefault();
    showContextMenu(event.clientX, event.clientY, item.type, item.name);
  });
  return button;
}

function isSelected(type, name) {
  return state.selected.type === type && state.selected.name === name;
}

function renderPanel() {
  if (state.selected.type === 'server') {
    const server = state.servers.find(s => s.name === state.selected.name);
    if (server) return renderServerPanel(server);
  }
  if (state.selected.type === 'workspace') {
    const share = state.workspaces.find(w => w.name === state.selected.name);
    if (share) return renderWorkspacePanel(share);
  }
  if (state.selected.type === 'new-server') return renderServerPanel(null);
  if (state.selected.type === 'new-workspace') return renderWorkspacePanel(null);
  if (state.selected.type === 'image-builder') return renderImageBuilderPanel();
  renderHomePanel();
}

function renderHomePanel() {
  $('#panelTitle').textContent = 'Relay Manager';
  $('#panelSubtitle').textContent = 'Select a server or relay from the sidebar.';
  $('#detailPanel').innerHTML = `
    <div class="card">
      <div class="card-head">
        <div>
          <h2>Standalone client relays</h2>
          <p>Servers hold PawFlow login details. Relays share local workspaces through a selected server.</p>
        </div>
      </div>
      <div class="info-list">
        <div class="info-row"><span>Servers</span><strong>${state.servers.length}</strong></div>
        <div class="info-row"><span>Relays</span><strong>${state.workspaces.length}</strong></div>
        <div class="info-row"><span>Running</span><strong>${state.running.size}</strong></div>
      </div>
      <div class="actions">
        <button id="homeAddServer">Add Server</button>
        <button id="homeAddWorkspace" class="secondary">Add Relay</button>
        <button id="homeBuildImage" class="secondary">Build Relay Image</button>
      </div>
    </div>
  `;
  $('#homeAddServer').addEventListener('click', () => setSelected('new-server'));
  $('#homeAddWorkspace').addEventListener('click', () => setSelected('new-workspace'));
  $('#homeBuildImage').addEventListener('click', () => setSelected('image-builder'));
}

function renderServerPanel(server) {
  const isNew = !server;
  $('#panelTitle').textContent = isNew ? 'Add Server' : server.name;
  $('#panelSubtitle').textContent = isNew ? 'Register a PawFlow server profile.' : server.url;
  const title = isNew ? 'New server' : 'Server settings';
  const help = isNew
    ? 'Add the PawFlow URL and optional private gateway key.'
    : 'Edit connection details, login, or remove this server profile.';
  const nameReadonly = isNew ? '' : 'readonly';
  const serverButtons = isNew ? '' : [
    '<button type="button" id="loginServerBtn" class="button secondary">Login / Status</button>',
    '<button type="button" id="deleteServerBtn" class="button danger">Delete Server</button>',
  ].join('');
  $('#detailPanel').innerHTML = `
    <form id="serverForm" class="card form-card">
      <div class="card-head">
        <div>
          <h2>${title}</h2>
          <p>${help}</p>
        </div>
      </div>
      <div class="form-grid">
        <label>Name<input name="name" value="${escapeAttr(server?.name || '')}" ${nameReadonly} required /></label>
        <label>URL<input name="url" value="${escapeAttr(server?.url || '')}" placeholder="https://pawflow.example:9090" required /></label>
        <label class="wide">Gateway key<input name="gatewayKey" type="password" value="${escapeAttr(server?.gateway_key || '')}" placeholder="optional" /></label>
      </div>
      ${serverInfo(server)}
      <div class="actions">
        <button class="button primary" type="submit">Save</button>
        <button class="button ghost" type="button" id="cancelServerBtn">Cancel</button>
        ${serverButtons}
      </div>
    </form>
  `;
  $('#serverForm').addEventListener('submit', saveServer);
  $('#cancelServerBtn')?.addEventListener('click', () => setSelected('home'));
  if (server) {
    $('#loginServerBtn')?.addEventListener('click', () => loginServer(server.name));
    $('#deleteServerBtn')?.addEventListener('click', () => deleteServer(server.name));
  }
}

function serverInfo(server) {
  if (!server) return '';
  const status = server.session_token ? `Logged in as ${escapeHtml(server.username || '-')}` : 'Not logged in';
  const workspaceCount = state.workspaces.filter(w => w.server === server.name).length;
  return `
    <div class="info-list">
      <div class="info-row"><span>Status</span><strong>${status}</strong></div>
      <div class="info-row"><span>Relays</span><strong>${workspaceCount}</strong></div>
      <div class="info-row"><span>Updated</span><span>${escapeHtml(server.updated_at || '-')}</span></div>
    </div>
  `;
}

function renderWorkspacePanel(share) {
  const isNew = !share;
  $('#panelTitle').textContent = isNew ? 'Add Relay' : share.name;
  $('#panelSubtitle').textContent = isNew ? 'Share a local workspace through a PawFlow server.' : share.path;
  const serverOptions = state.servers.map(server => {
    const selected = server.name === share?.server ? 'selected' : '';
    return `<option value="${escapeAttr(server.name)}" ${selected}>${escapeHtml(server.name)}</option>`;
  }).join('');
  const dockerOptions = dockerImageOptions(share?.docker_image || '');
  const running = share ? state.running.has(share.name) : false;
  const title = isNew ? 'New relay' : 'Relay settings';
  const help = isNew
    ? 'Choose a server, local path, permissions, and relay image.'
    : 'Edit the workspace share or control the relay process.';
  const nameReadonly = isNew ? '' : 'readonly';
  const rwSelected = (share?.mode || 'rw') === 'rw' ? 'selected' : '';
  const roSelected = share?.mode === 'ro' ? 'selected' : '';
  const allowExecChecked = share?.allow_exec === false ? '' : 'checked';
  const allowRemoteDesktopChecked = share?.allow_remote_desktop === false ? '' : 'checked';
  const allowLocalChecked = share?.allow_local ? 'checked' : '';
  const startDisabled = running ? 'disabled' : '';
  const stopDisabled = running ? '' : 'disabled';
  const workspaceButtons = isNew ? '' : [
    `<button type="button" id="startRelayBtn" class="button secondary" ${startDisabled}>Start</button>`,
    `<button type="button" id="stopRelayBtn" class="button secondary" ${stopDisabled}>Stop</button>`,
    '<button type="button" id="deleteRelayBtn" class="button danger">Delete Relay</button>',
  ].join('');
  $('#detailPanel').innerHTML = `
    <form id="workspaceForm" class="card form-card">
      <div class="card-head">
        <div>
          <h2>${title}</h2>
          <p>${help}</p>
        </div>
      </div>
      <div class="form-grid">
        <label>Name<input name="name" value="${escapeAttr(share?.name || '')}" ${nameReadonly} required /></label>
        <label>Server<select name="server" required>${serverOptions}</select></label>
        <label class="wide">Path
          <div class="path-picker">
            <input name="path" value="${escapeAttr(share?.path || '')}" placeholder="/home/me/project or \\server\\share" required />
            <button class="button secondary" type="button" id="browsePathBtn">Browse</button>
          </div>
        </label>
        <label>Mode<select name="mode">
          <option value="rw" ${rwSelected}>Read/write</option>
          <option value="ro" ${roSelected}>Read-only</option>
        </select></label>
        <label>Docker image
          <div class="image-picker">
            <select name="dockerImage">${dockerOptions}</select>
            <button class="button secondary" type="button" id="buildImageBtn">Build</button>
          </div>
        </label>
      </div>
      <div class="toggle-grid">
        <label class="toggle"><input name="allowExec" type="checkbox" ${allowExecChecked} /><span></span><strong>Allow exec</strong></label>
        <label class="toggle"><input name="allowRemoteDesktop" type="checkbox" ${allowRemoteDesktopChecked} /><span></span><strong>Allow remote desktop</strong></label>
        <label class="toggle"><input name="allowLocal" type="checkbox" ${allowLocalChecked} /><span></span><strong>Allow local access</strong></label>
      </div>
      ${workspaceInfo(share, running)}
      <div class="actions">
        <button class="button primary" type="submit">Save</button>
        <button class="button ghost" type="button" id="cancelWorkspaceBtn">Cancel</button>
        ${workspaceButtons}
      </div>
    </form>
  `;
  $('#workspaceForm').addEventListener('submit', saveWorkspace);
  $('#browsePathBtn')?.addEventListener('click', browseWorkspacePath);
  $('#buildImageBtn')?.addEventListener('click', () => setSelected('image-builder'));
  $('#cancelWorkspaceBtn')?.addEventListener('click', () => setSelected('home'));
  if (share) {
    $('#startRelayBtn')?.addEventListener('click', () => startRelay(share.name));
    $('#stopRelayBtn')?.addEventListener('click', () => stopRelay(share.name));
    $('#deleteRelayBtn')?.addEventListener('click', () => deleteWorkspace(share.name));
  }
}

function workspaceInfo(share, running) {
  if (!share) return '';
  const status = running ? 'Running' : 'Stopped';
  return `
    <div class="info-list">
      <div class="info-row"><span>Status</span><strong>${status}</strong></div>
      <div class="info-row"><span>Relay ID</span><code>${escapeHtml(share.relay_id || '-')}</code></div>
      <div class="info-row"><span>Updated</span><span>${escapeHtml(share.updated_at || '-')}</span></div>
    </div>
  `;
}

function dockerImageOptions(current) {
  const seen = new Set(['']);
  const rows = ['<option value="">Default image</option>'];
  if (current) {
    seen.add(current);
    rows.push(`<option value="${escapeAttr(current)}" selected>${escapeHtml(current)}</option>`);
  }
  for (const image of state.dockerImages || []) {
    if (!image.name || seen.has(image.name)) continue;
    seen.add(image.name);
    const label = image.size ? `${image.name} (${image.size})` : image.name;
    rows.push(`<option value="${escapeAttr(image.name)}">${escapeHtml(label)}</option>`);
  }
  return rows.join('');
}

function renderImageBuilderPanel() {
  $('#panelTitle').textContent = 'Build Relay Image';
  $('#panelSubtitle').textContent = 'Create a local Docker image for client relays.';
  const catalog = state.imageCatalog;
  if (!catalog || !catalog.features) {
    $('#detailPanel').innerHTML = `
      <div class="card">
        <h2>Relay image catalog unavailable</h2>
        <p>Refresh the app after generating the desktop runtime.</p>
      </div>
    `;
    return;
  }
  const profiles = catalog.profiles || {};
  const profileOptions = [
    '<option value="">Custom: base only</option>',
    ...Object.entries(profiles).map(([id, profile]) => (
      `<option value="${escapeAttr(id)}">${escapeHtml(profile.label || id)}</option>`
    )),
  ].join('');
  const groups = {};
  for (const [id, feature] of Object.entries(catalog.features || {})) {
    const category = feature.category || 'other';
    if (!groups[category]) groups[category] = [];
    groups[category].push([id, feature]);
  }
  const required = new Set(catalog.required_features || []);
  const featureHtml = Object.entries(groups).map(([category, items]) => {
    const rows = items.sort(([a], [b]) => a.localeCompare(b)).map(([id, feature]) => {
      const isRequired = required.has(id) || feature.required;
      const checked = isRequired ? 'checked' : '';
      const disabled = isRequired ? 'disabled' : '';
      const size = feature.estimated_size_mb ? `${feature.estimated_size_mb} MB` : '';
      const requiredClass = isRequired ? 'required' : '';
      const sizeText = size ? ` · ${escapeHtml(size)}` : '';
      return `
        <label class="feature-item ${requiredClass}">
          <input type="checkbox" name="feature" value="${escapeAttr(id)}" ${checked} ${disabled} />
          <span>
            <strong>${escapeHtml(feature.label || id)}</strong>
            <small>${escapeHtml(id)}${sizeText}</small>
          </span>
        </label>
      `;
    }).join('');
    return `
      <section class="feature-group">
        <h3>${escapeHtml(categoryLabel(category))}</h3>
        <div class="feature-grid">${rows}</div>
      </section>
    `;
  }).join('');
  $('#detailPanel').innerHTML = `
    <form id="imageBuildForm" class="card image-builder-card">
      <div class="card-head">
        <div>
          <h2>Relay image builder</h2>
          <p>Select a preset or keep custom, then add individual capabilities. Docker build cache is pruned after a successful build.</p>
        </div>
      </div>
      <div class="form-grid">
        <label>Image name<input name="imageName" value="pawflow-relay-custom:latest" required /></label>
        <label>Preset<select name="profile">${profileOptions}</select></label>
      </div>
      <div class="feature-list">${featureHtml}</div>
      <div class="actions">
        <button class="button primary" type="submit" id="buildRelayImageSubmit">Build Image</button>
        <button class="button ghost" type="button" id="cancelImageBuildBtn">Cancel</button>
      </div>
    </form>
  `;
  $('#imageBuildForm').addEventListener('submit', buildRelayImage);
  $('#cancelImageBuildBtn')?.addEventListener('click', () => setSelected('home'));
}

function categoryLabel(category) {
  return String(category || 'other').replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());
}

async function saveServer(event) {
  event.preventDefault();
  const input = formData(event.currentTarget);
  try {
    const saved = await window.pawflowRelay.addServer(input);
    toast(`Saved server ${saved.name}`);
    await refresh();
    setSelected('server', saved.name);
  } catch (err) {
    toast(err.message, true);
  }
}

async function saveWorkspace(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = formData(form);
  input.allowLocal = form.elements.allowLocal.checked;
  input.allowExec = form.elements.allowExec.checked;
  input.allowRemoteDesktop = form.elements.allowRemoteDesktop.checked;
  try {
    const saved = await window.pawflowRelay.addWorkspace(input);
    toast(`Saved relay ${saved.name}`);
    await refresh();
    setSelected('workspace', saved.name);
  } catch (err) {
    toast(err.message, true);
  }
}

async function buildRelayImage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = $('#buildRelayImageSubmit');
  const input = formData(form);
  const features = Array.from(form.querySelectorAll('input[name="feature"]:checked:not(:disabled)'))
    .map(el => el.value);
  submit.disabled = true;
  submit.textContent = 'Building...';
  try {
    const result = await window.pawflowRelay.buildRelayImage({
      imageName: input.imageName,
      profile: input.profile || '',
      features,
    });
    toast(`Built image ${result.image}`);
    await refresh();
    setSelected('new-workspace');
  } catch (err) {
    toast(err.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = 'Build Image';
  }
}

async function browseWorkspacePath() {
  const field = document.querySelector('#workspaceForm input[name="path"]');
  try {
    const selected = await window.pawflowRelay.selectDirectory(field?.value || '');
    if (selected && field) field.value = selected;
  } catch (err) {
    toast(err.message, true);
  }
}

async function loginServer(name) {
  try {
    await window.pawflowRelay.loginServer(name);
    toast(`Login refreshed for ${name}`);
    await refresh();
    setSelected('server', name);
  } catch (err) {
    toast(err.message, true);
  }
}

async function deleteServer(name) {
  const count = state.workspaces.filter(w => w.server === name).length;
  const suffix = count ? ` This will also delete ${count} relay workspace(s).` : '';
  if (!confirm(`Delete server "${name}"?${suffix}`)) return;
  try {
    await window.pawflowRelay.deleteServer(name);
    toast(`Deleted server ${name}`);
    await refresh();
  } catch (err) {
    toast(err.message, true);
  }
}

async function startRelay(name) {
  try {
    await window.pawflowRelay.start(name);
    toast(`Started ${name}`);
    await refresh();
    setSelected('workspace', name);
  } catch (err) {
    toast(err.message, true);
  }
}

async function stopRelay(name) {
  try {
    await window.pawflowRelay.stop(name);
    toast(`Stopped ${name}`);
    await refresh();
    setSelected('workspace', name);
  } catch (err) {
    toast(err.message, true);
  }
}

async function deleteWorkspace(name) {
  if (!confirm(`Delete relay "${name}"?`)) return;
  try {
    if (state.running.has(name)) await window.pawflowRelay.stop(name);
    await window.pawflowRelay.deleteWorkspace(name);
    toast(`Deleted relay ${name}`);
    await refresh();
  } catch (err) {
    toast(err.message, true);
  }
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function showContextMenu(x, y, type, name) {
  const menu = $('#contextMenu');
  const items = [];
  if (type === 'server') {
    items.push(['Edit', () => setSelected('server', name)]);
    items.push(['Login / Refresh Status', () => loginServer(name)]);
    items.push(['Delete Server', () => deleteServer(name), 'danger']);
  } else if (type === 'workspace') {
    const running = state.running.has(name);
    items.push(['Edit', () => setSelected('workspace', name)]);
    items.push([running ? 'Stop' : 'Start', () => running ? stopRelay(name) : startRelay(name)]);
    items.push(['Delete Relay', () => deleteWorkspace(name), 'danger']);
  }
  menu.innerHTML = '';
  for (const [label, action, cls] of items) {
    const button = document.createElement('button');
    button.textContent = label;
    if (cls) button.className = cls;
    button.addEventListener('click', () => {
      hideContextMenu();
      action();
    });
    menu.appendChild(button);
  }
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.classList.remove('hidden');
}

function hideContextMenu() {
  $('#contextMenu').classList.add('hidden');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
}

$('#addServerBtn').addEventListener('click', () => setSelected('new-server'));
$('#addWorkspaceBtn').addEventListener('click', () => setSelected('new-workspace'));
$('#refreshBtn').addEventListener('click', () => refresh().catch(err => toast(err.message, true)));
$('#clearLogsBtn').addEventListener('click', () => { $('#logs').textContent = ''; });
document.addEventListener('click', hideContextMenu);
window.pawflowRelay.onLog(payload => appendLog(payload.name, payload.text));
refresh().catch(err => toast(err.message, true));
