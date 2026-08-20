// ── Door, conversation rooms, title ─────────────────────────────
// The office door: clicking it opens the conversation picker, and a2a
// (cross-conversation) trips walk to it. Each conversation is a
// different "room": a palette derived deterministically from the
// conversation id (same conversation → same colors, always).
function _osBuildDoor() {
  const T = _osThree;
  const g = new T.Group();
  const frame = new T.Mesh(
    new T.BoxGeometry(2.4, 3.4, 0.25),
    new T.MeshLambertMaterial({ color: 0x5d3d21 }));
  frame.position.y = 1.7;
  const panel = new T.Mesh(
    new T.BoxGeometry(1.9, 3.0, 0.18),
    new T.MeshLambertMaterial({ color: 0x8a5a33 }));
  panel.position.set(0, 1.5, 0.12);
  const knob = new T.Mesh(
    new T.SphereGeometry(0.09, 10, 8),
    new T.MeshLambertMaterial({ color: 0xffd43b }));
  knob.position.set(0.7, 1.5, 0.26);
  g.add(frame, panel, knob);
  g.position.set(OSV_DOOR_X, 0, OSV_DOOR_Z);
  g.traverse((o) => { o.userData.osvDoor = true; });
  _osScene.add(g);
  _osDoorPos = { x: OSV_DOOR_X, z: OSV_DOOR_Z };
}

// ── FileStore TV ─────────────────────────────────────────────────
// A lounge television on the left wall, past the blackboard, facing the
// desks. Clicking it lists the conversation's FileStore files; picking
// one plays/shows it on the TV screen — a projected DOM panel, so the
// native <video>/<audio> controls keep working on the 3D surface.
function _osBuildTv() {
  const T = _osThree;
  const tx = -6.3, ty = 1.75, tz = 8.5;   // body center
  const sw = 3.4, sh = sw * OSV_TV_H / OSV_TV_W;
  const g = new T.Group();
  const body = new T.Mesh(
    new T.BoxGeometry(0.5, sh + 0.5, sw + 0.5),
    new T.MeshLambertMaterial({ color: 0x1b2140 }));
  body.position.y = ty;
  const screen = new T.Mesh(
    new T.BoxGeometry(0.06, sh, sw),
    new T.MeshLambertMaterial({ color: 0x0b0e1d }));
  screen.position.set(0.26, ty, 0);
  [-1, 1].forEach((s) => {
    const leg = new T.Mesh(
      new T.BoxGeometry(0.18, ty - sh / 2 - 0.25 + 0.5, 0.18),
      new T.MeshLambertMaterial({ color: 0x14182e }));
    leg.position.set(0, (ty - sh / 2 - 0.25 + 0.5) / 2, s * (sw / 2 - 0.2));
    g.add(leg);
  });
  const antenna = new T.Mesh(
    new T.CylinderGeometry(0.03, 0.03, 0.9, 6),
    new T.MeshLambertMaterial({ color: 0x8a93b8 }));
  antenna.position.set(0, ty + sh / 2 + 0.6, 0.35);
  antenna.rotation.x = 0.5;
  g.add(body, screen, antenna);
  g.position.set(tx, 0, tz);
  g.traverse((o) => { o.userData.osvTv = true; });
  _osScene.add(g);
  // Screen quad faces +x (toward the desks); the viewer's left is +z.
  const px = tx + 0.30;
  _osTvCorners = [
    { x: px, y: ty + sh / 2, z: tz + sw / 2 },
    { x: px, y: ty + sh / 2, z: tz - sw / 2 },
    { x: px, y: ty - sh / 2, z: tz + sw / 2 },
    { x: px, y: ty - sh / 2, z: tz - sw / 2 },
  ];
  if (!_osTvEl && _osOverlay) {
    _osTvEl = document.createElement('div');
    _osTvEl.className = 'osv-tv';
    const head = document.createElement('div');
    head.className = 'osv-tv-head';
    _osTvTitleEl = document.createElement('span');
    _osTvTitleEl.className = 'osv-tv-title';
    const stop = document.createElement('button');
    stop.className = 'osv-tv-stop';
    stop.textContent = '\u2715';
    stop.onclick = (e) => { e.stopPropagation(); openspaceTvStop(); };
    head.append(_osTvTitleEl, stop);
    _osTvBodyEl = document.createElement('div');
    _osTvBodyEl.className = 'osv-tv-body';
    _osTvEl.append(head, _osTvBodyEl);
    // Clicking the idle screen opens the picker too (same as the mesh).
    _osTvBodyEl.onclick = () => { if (!_osTvMedia) openspaceOpenTvDialog(); };
    _osOverlay.appendChild(_osTvEl);
  }
  _osTvIdle();
}

function _osTvIdle() {
  if (!_osTvBodyEl) return;
  _osTvBodyEl.innerHTML = '';
  const idle = document.createElement('div');
  idle.className = 'osv-tv-note';
  idle.textContent = '\u{1F4FA} ' + t('osvTvIdle');
  _osTvBodyEl.appendChild(idle);
  if (_osTvTitleEl) _osTvTitleEl.textContent = t('osvTvTitle');
}

function openspaceTvStop() {
  if (_osTvMedia) {
    try {
      if (typeof _osTvMedia.pause === 'function') _osTvMedia.pause();
      _osTvMedia.removeAttribute('src');
      if (typeof _osTvMedia.load === 'function') _osTvMedia.load();
    } catch (_e) { /* media teardown is best-effort */ }
    _osTvMedia = null;
  }
  _osTvIdle();
}

function _osTvFileIcon(type) {
  if (type.startsWith('video/')) return '\u{1F3AC}';
  if (type.startsWith('image/')) return '\u{1F5BC}\uFE0F';
  if (type.startsWith('audio/')) return '\u{1F3B5}';
  return '\u{1F4C4}';
}

function _osTvSize(bytes) {
  const n = Number(bytes) || 0;
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
  if (n >= 1024) return Math.round(n / 1024) + ' KB';
  return n + ' B';
}

function openspaceOpenTvDialog() {
  const prior = document.getElementById('osvTvDialog');
  if (prior) prior.remove();
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.id = 'osvTvDialog';
  const dialog = document.createElement('div');
  dialog.className = 'exec-dialog cog-dialog osv-dialog';
  const head = document.createElement('div');
  head.className = 'cog-head';
  const title = document.createElement('h3');
  title.textContent = '\u{1F4FA} ' + t('osvTvTitle');
  head.appendChild(title);
  const close = document.createElement('button');
  close.className = 'cog-close';
  close.innerHTML = '&times;';
  close.onclick = () => overlay.remove();
  const list = document.createElement('div');
  list.className = 'osv-log';
  dialog.append(close, head, list);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  if (typeof action$ !== 'function') return;
  action$('list_conv_files', { conversation_id: conversationId }).subscribe((data) => {
    const files = (data && data.files) || [];
    files.forEach((f) => {
      if (!f || !f.file_id) return;
      const row = document.createElement('div');
      row.className = 'osv-block osv-flow-row';
      const type = String(f.content_type || '').toLowerCase();
      const name = document.createElement('span');
      name.textContent = _osTvFileIcon(type) + ' ' + (f.filename || f.file_id)
        + ' \u00B7 ' + _osTvSize(f.size);
      row.appendChild(name);
      if (f.available === false) {
        row.classList.add('osv-tv-unavailable');
      } else {
        row.style.cursor = 'pointer';
        row.onclick = () => { overlay.remove(); openspaceTvShow(f); };
      }
      list.appendChild(row);
    });
    if (!list.childElementCount) {
      const empty = document.createElement('div');
      empty.className = 'osv-log-empty';
      empty.textContent = t('osvTvEmpty');
      list.appendChild(empty);
    }
  });
}

function openspaceTvShow(f) {
  if (!_osTvBodyEl || !f || !f.file_id) return;
  openspaceTvStop();
  _osTvBodyEl.innerHTML = '';
  const url = '/files/' + encodeURIComponent(f.file_id) + '/'
    + encodeURIComponent(f.filename || 'file');
  const type = String(f.content_type || '').toLowerCase();
  let media = null;
  if (type.startsWith('video/')) {
    media = document.createElement('video');
    media.controls = true;
    media.autoplay = true;
    media.src = url;
  } else if (type.startsWith('image/')) {
    media = document.createElement('img');
    media.src = url;
    media.alt = f.filename || '';
  } else if (type.startsWith('audio/')) {
    const note = document.createElement('div');
    note.className = 'osv-tv-note';
    note.textContent = '\u{1F3B5} ' + (f.filename || '');
    _osTvBodyEl.appendChild(note);
    media = document.createElement('audio');
    media.controls = true;
    media.autoplay = true;
    media.src = url;
  } else {
    // Unknown/unsupported format: say so ON the TV and point at the
    // Files menu, which owns download/preview for everything else.
    const note = document.createElement('div');
    note.className = 'osv-tv-note';
    note.textContent = '\u{1F4C4} ' + (f.filename || '') + '\n'
      + t('osvTvUnsupported');
    _osTvBodyEl.appendChild(note);
  }
  if (media) {
    _osTvBodyEl.appendChild(media);
    _osTvMedia = media;
  }
  if (_osTvTitleEl) _osTvTitleEl.textContent = f.filename || '';
}

function _osHashSeed(s) {
  let h = 0;
  for (const ch of String(s || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h;
}

function _osApplyRoomStyle(seed) {
  if (!_osScene || !_osRoomMats) return;
  const cid = String(seed || _osSeedConvId
    || (typeof conversationId !== 'undefined' && conversationId) || 'default');
  const hue = (_osHashSeed(cid) % 360) / 360;
  _osScene.background.setHSL(hue, 0.30, 0.56);
  if (_osScene.fog) _osScene.fog.color.copy(_osScene.background);
  if (_osRoomMats.floor) _osRoomMats.floor.color.setHSL((hue + 0.04) % 1, 0.18, 0.88);
  (_osRoomMats.walls || []).forEach((mat) => {
    mat.color.setHSL((hue + 0.94) % 1, 0.18, 0.88);
  });
  if (_osRoomMats.rug) _osRoomMats.rug.color.setHSL((hue + 0.08) % 1, 0.50, 0.28);
  if (_osRoomMats.couch) _osRoomMats.couch.color.setHSL((hue + 0.55) % 1, 0.5, 0.5);
  if (_osRoomMats.couchBack) _osRoomMats.couchBack.color.setHSL((hue + 0.55) % 1, 0.55, 0.55);
}

// Conversation title in the frame above the wall screen (1s cadence,
// written only on change).
let _osTitleFetchFor = '';
function _osRefreshTitle() {
  if (!_osTitleEl) return;
  const cid = (typeof conversationId !== 'undefined' && conversationId) || '';
  const all = ((typeof window !== 'undefined' && window._ownConvs) || [])
    .concat((typeof window !== 'undefined' && window._sharedConvs) || []);
  const c = all.find((x) => x && x.conversation_id === cid);
  if (!c && cid && cid !== _osTitleFetchFor && typeof action$ === 'function') {
    // The sidebar cache starts empty when its Conversations section was
    // never opened (typical on mobile): fetch once per conversation so
    // the frame shows the real title, not "New conversation".
    _osTitleFetchFor = cid;
    action$('list_conversations', {}).subscribe((d) => {
      if (d && d.conversations) window._ownConvs = d.conversations;
    });
  }
  const title = c ? (c.title || c.preview || t('newConversation'))
    : (cid ? '' : t('newConversation'));
  if (!title) return;   // keep the current text until the fetch lands
  if (title !== _osTitleText) { _osTitleText = title; _osTitleEl.textContent = title; }
}

function openspaceOpenConvDialog() {
  const prior = document.getElementById('osvConvDialog');
  if (prior) prior.remove();
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.id = 'osvConvDialog';
  const dialog = document.createElement('div');
  dialog.className = 'exec-dialog cog-dialog osv-dialog';
  const head = document.createElement('div');
  head.className = 'cog-head';
  const title = document.createElement('h3');
  title.textContent = t('conversations');
  head.appendChild(title);
  const close = document.createElement('button');
  close.className = 'cog-close';
  close.innerHTML = '&times;';
  close.onclick = () => overlay.remove();
  const list = document.createElement('div');
  list.className = 'osv-log';
  dialog.append(close, head, list);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  const addRow = (c, isShared) => {
    if (!c || !c.conversation_id) return;
    const row = document.createElement('div');
    row.className = 'osv-block osv-flow-row'
      + (c.conversation_id === conversationId ? ' osv-conv-current' : '');
    const name = document.createElement('span');
    const date = c.updated_at
      ? new Date(c.updated_at * 1000).toLocaleDateString() : '';
    name.textContent = (isShared ? '\u{1F465} ' : '')
      + (c.title || c.preview || t('newConversation'))
      + (date ? ' \u00B7 ' + date : '');
    row.append(name);
    row.style.cursor = 'pointer';
    row.onclick = () => {
      overlay.remove();
      if (c.conversation_id !== conversationId
          && typeof resumeConv === 'function') resumeConv(c.conversation_id);
    };
    list.appendChild(row);
  };
  // Always fetch live: the sidebar cache (window._ownConvs) is empty
  // until the user opens the Conversations section, which on mobile may
  // never happen — rendering from it showed "no conversations" wrongly.
  if (typeof action$ !== 'function') return;
  action$('list_conversations', {}).subscribe((data) => {
    ((data && data.conversations) || []).forEach((c) => addRow(c, false));
    ((typeof window !== 'undefined' && window._sharedConvs) || [])
      .forEach((c) => addRow(c, true));
    if (!list.childElementCount) {
      const empty = document.createElement('div');
      empty.className = 'osv-log-empty';
      empty.textContent = t('noConversationsHint');
      list.appendChild(empty);
    }
  });
}

// A trip to the door: the agent walks over, says what it sends out
// (a2a / cross-conversation delegation), and walks back home.
// Smoothly keep the camera target glued to a clicked participant, or to
// the viewer's avatar when no explicit focus owns the camera.
function _osFollowUser() {
  if (_osFlow || !_osCamera) return;
  let me = _osFocusKey ? _osAgents.get(_osFocusKey) : null;
  if (_osFocusKey && !me) { _osFocusKey = ''; _osFollow = true; }
  if (!me && _osFollow) {
    const key = 'user:' + _osKey(
      (typeof window !== 'undefined' && window._userId) || 'user');
    me = _osAgents.get(key);
  }
  if (!me || !me.avatar) return;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const rows = Math.max(1, Math.ceil(Math.max(_osSeatCount, 1) / OSV_GRID_COLS));
  const cz = ((rows - 1) * OSV_DESK_SPACING) / 2;
  const tx = me.avatar.position.x - cx, tz = me.avatar.position.z - cz;
  const dx = tx - _osCamPan.x, dz = tz - _osCamPan.z;
  if (Math.abs(dx) + Math.abs(dz) < 0.01) return;
  _osCamPan.x += dx * 0.06;
  _osCamPan.z += dz * 0.06;
  _osUpdateCamera();
}

function _osFocusAgent(key) {
  const rec = _osAgents.get(key);
  if (!rec || !rec.avatar) return;
  _osFocusKey = key;
  _osFollow = false;
}

function _osDoorTrip(rec, label) {
  if (!rec || !rec.avatar || !_osDoorPos || rec.kind === 'user' || rec.awayAt) return;
  rec.awayAt = 'door';
  _osWalkTo(rec, { x: _osDoorPos.x + 1.4, z: _osDoorPos.z + 1.8 }, () => {
    if (label) _osShowBubble(rec, 'speech', label);
    setTimeout(() => {
      if (_osAgents.get(rec.key) !== rec || rec.awayAt !== 'door') return;
      rec.awayAt = null;
      _osWalkTo(rec, { x: rec.homeSeat.x, z: rec.homeSeat.z + 1.35 });
    }, 1100);
  });
}

// Flash-delegate guests are temporary: their desk appears with the
// delegation and is dismantled once the sub-agent finishes (the seat
// slot goes back into the pool for the next guest).
function _osRetireAgent(rec) {
  if (!rec) return;
  _osAgents.delete(rec.key);
  if (typeof rec.seatIndex === 'number') _osFreeSeats.push(rec.seatIndex);
  [rec.labelEl, rec.speechEl, rec.thoughtEl, rec.statusEl, rec.battEl]
    .forEach((el) => { if (el) el.remove(); });
  (rec.tools || []).slice().forEach((entry) => _osRemoveTool(rec, entry));
  _osClearOrbit(rec);   // sprite textures are not reached by the traverse below
  const seen = new Set();
  [rec.group, rec.avatar].forEach((obj) => {
    if (!obj || seen.has(obj) || !_osScene) return;
    seen.add(obj);
    _osScene.remove(obj);
    obj.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material && o.material.dispose) o.material.dispose();
    });
  });
  if (typeof rec.seatIndex === 'number') _osReleaseDeskSlot(rec.seatIndex);
}

// ── Resource sub-menu boards ─────────────────────────────────
// Clicking the Resources poster pops one labeled board per sub-section
// (Agents, Tasks, Flows, Services, Packages, Variables, Secrets, the
// repositories...). The sidebar renderer stays the single source of
// truth: clicking a board opens a DIALOG cloning that section's live
// DOM, refreshed while open. Inline onclick handlers survive, so +/↻/context-menu actions work from the scene.
let _osResBoards = [];
let _osResDialogTimer = 0;

function _osResSections() {
  const content = document.getElementById('resourcesContent');
  if (!content) return [];
  const out = [];
  content.querySelectorAll('[id^="res-section-"]').forEach((body) => {
    const header = body.previousElementSibling;
    if (!header) return;
    const title = (header.textContent || '')
      .replace(/[\u25B6\u25BC\u21BB+]/g, ' ').replace(/\s+/g, ' ').trim();
    out.push({ rtype: body.id.slice('res-section-'.length),
               title: title, header: header, body: body });
  });
  return out;
}

function openspaceToggleResourceBoards() {
  if (_osResBoards.length) { _osClearResourceBoards(); return; }
  if (typeof loadResources === 'function') loadResources();
  _osBuildResourceBoards();
  // The sidebar data may still be loading on first use: rebuild once
  // shortly after so every sub-section gets its screen.
  setTimeout(() => {
    if (!_osResBoards.length) return;
    _osClearResourceBoards();
    _osBuildResourceBoards();
  }, 1500);
}

function _osResBoardTexture(title, count) {
  const canvas = document.createElement('canvas');
  canvas.width = 256; canvas.height = 128;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#243b64';
  ctx.fillRect(0, 0, 256, 128);
  ctx.strokeStyle = '#74c0fc';
  ctx.lineWidth = 5;
  ctx.strokeRect(4, 4, 248, 120);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#e6ecff';
  ctx.font = 'bold 26px sans-serif';
  ctx.fillText(String(title).slice(0, 16), 128, 58);
  ctx.fillStyle = '#9fc2ff';
  ctx.font = '20px sans-serif';
  ctx.fillText(String(count), 128, 98);
  return new _osThree.CanvasTexture(canvas);
}

function _osBuildResourceBoards() {
  const T = _osThree;
  if (!T || !_osScene) return;
  // Overlay the accessible meeting-partition gallery, on the office face.
  const x = OSV_RESOURCE_WALL.faceX - 0.12;
  _osResSections().forEach((s, i) => {
    const count = s.body.querySelectorAll('div').length;
    const mesh = new T.Mesh(
      new T.PlaneGeometry(1.32, 0.78),
      new T.MeshBasicMaterial({ map: _osResBoardTexture(s.title, count) }));
    mesh.position.set(
      x, 0.55 + Math.floor(i / OSV_RESOURCE_WALL.columns) * 0.94,
      OSV_RESOURCE_WALL.zStart + (i % OSV_RESOURCE_WALL.columns) * OSV_RESOURCE_WALL.zStep);
    mesh.rotation.y = -Math.PI / 2;
    mesh.userData.osvResSection = s.rtype;
    mesh.userData.osvResTitle = s.title;
    _osScene.add(mesh);
    _osResBoards.push(mesh);
  });
}

function _osClearResourceBoards() {
  _osResBoards.forEach((m) => {
    if (_osScene) _osScene.remove(m);
    if (m.geometry) m.geometry.dispose();
    if (m.material) {
      if (m.material.map) m.material.map.dispose();
      m.material.dispose();
    }
  });
  _osResBoards = [];
}

function openspaceOpenResSectionDialog(rtype, title) {
  const prior = document.getElementById('osvResDialog');
  if (prior) prior.remove();
  if (_osResDialogTimer) { clearInterval(_osResDialogTimer); _osResDialogTimer = 0; }
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.id = 'osvResDialog';
  const dialog = document.createElement('div');
  dialog.className = 'exec-dialog cog-dialog osv-dialog';
  const head = document.createElement('div');
  head.className = 'cog-head';
  const h3 = document.createElement('h3');
  h3.textContent = title || rtype;
  head.appendChild(h3);
  const close = document.createElement('button');
  close.className = 'cog-close';
  close.innerHTML = '&times;';
  close.onclick = () => {
    if (_osResDialogTimer) { clearInterval(_osResDialogTimer); _osResDialogTimer = 0; }
    overlay.remove();
  };
  const bodyWrap = document.createElement('div');
  bodyWrap.className = 'osv-resdialog-body';
  dialog.append(close, head, bodyWrap);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  let sig = '';
  const fill = () => {
    if (!document.body.contains(overlay)) {
      if (_osResDialogTimer) { clearInterval(_osResDialogTimer); _osResDialogTimer = 0; }
      return;
    }
    const s = _osResSections().find((x) => x.rtype === rtype);
    if (!s) return;
    const cur = s.header.outerHTML + s.body.innerHTML;
    if (cur === sig) return;
    sig = cur;
    bodyWrap.textContent = '';
    const h = s.header.cloneNode(true);
    const b = s.body.cloneNode(true);
    b.style.display = 'block';
    b.style.maxHeight = 'none';
    [h, b].forEach((root) => {
      if (root.id) root.removeAttribute('id');
      root.querySelectorAll('[id]').forEach((n) => n.removeAttribute('id'));
      root.querySelectorAll('[onclick*="_toggleSection"]')
        .forEach((n) => n.removeAttribute('onclick'));
    });
    bodyWrap.append(h, b);
  };
  fill();
  // Live: actions (create, delete, move...) re-render the sidebar; the
  // dialog mirrors it on the same cadence.
  _osResDialogTimer = setInterval(fill, OSV_RES_SYNC_MS);
}
