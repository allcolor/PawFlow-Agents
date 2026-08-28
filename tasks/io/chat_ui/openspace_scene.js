// ── Decor ────────────────────────────────────────────────────────
// Low-poly office props: plants, a rug under the visitor row, a couch
// facing the wall screen, and a water cooler. Pure cosmetics — nothing
// here is raycast-targeted or stateful.
function _osBuildDecor() {
  const T = _osThree;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const mat = (c) => new T.MeshLambertMaterial({ color: c });
  const plant = (x, z) => {
    const g = new T.Group();
    const pot = new T.Mesh(new T.CylinderGeometry(0.35, 0.28, 0.5, 10), mat(0xb5651d));
    pot.position.y = 0.25;
    const leaves = new T.Mesh(new T.ConeGeometry(0.55, 1.1, 8), mat(0x2f9e44));
    leaves.position.y = 1.1;
    const crown = new T.Mesh(new T.ConeGeometry(0.42, 0.9, 8), mat(0x37b24d));
    crown.position.y = 1.6;
    g.add(pot, leaves, crown);
    g.position.set(x, 0, z);
    _osScene.add(g);
  };
  plant(cx - 8.5, -6.5); plant(cx + 8.5, -6.5);
  plant(cx - 8.5, 4); plant(cx + 8.5, 4);
  const rug = new T.Mesh(new T.CircleGeometry(4.4, 24), mat(0x27305c));
  rug.rotation.x = -Math.PI / 2;
  rug.position.set(cx, 0.02, -4.5);
  _osScene.add(rug);
  if (_osRoomMats) _osRoomMats.rug = rug.material;
  const couch = new T.Group();
  const seat = new T.Mesh(new T.BoxGeometry(4.2, 0.55, 1.4), mat(0x5f3dc4));
  seat.position.y = 0.45;
  const back = new T.Mesh(new T.BoxGeometry(4.2, 0.9, 0.35), mat(0x6741d9));
  back.position.set(0, 0.95, 0.55);
  if (_osRoomMats) { _osRoomMats.couch = seat.material; _osRoomMats.couchBack = back.material; }
  const armL = new T.Mesh(new T.BoxGeometry(0.35, 0.8, 1.4), mat(0x6741d9));
  armL.position.set(-2.1, 0.65, 0);
  const armR = armL.clone();
  armR.position.x = 2.1;
  couch.add(seat, back, armL, armR);
  couch.position.set(cx + 7.5, 0, -5.5);
  _osScene.add(couch);
  const cooler = new T.Group();
  const body = new T.Mesh(new T.BoxGeometry(0.6, 1.2, 0.6), mat(0xdee2e6));
  body.position.y = 0.6;
  const bottle = new T.Mesh(
    new T.CylinderGeometry(0.24, 0.24, 0.5, 10),
    new T.MeshLambertMaterial({ color: 0x74c0fc, transparent: true, opacity: 0.85 }));
  bottle.position.y = 1.45;
  cooler.add(body, bottle);
  cooler.position.set(cx - 7.5, 0, -5.5);
  _osScene.add(cooler);
}

// ── Projection wall ──────────────────────────────────────────────
// A cinema screen behind the visitor row, facing the desks. The WebGL
// mesh is only the bezel and pole; the picture itself is the real
// simplified-view DOM, reparented (not copied) so it stays live and
// scrollable, and mapped onto the wall quad every frame.
function _osBuildBigScreen() {
  const T = _osThree;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const sw = 5.4, sh = sw * OSV_SCREEN_H / OSV_SCREEN_W;
  const screenBottom = 0.25, sy = screenBottom + sh / 2, sz = OSV_SCREEN_Z;
  const titleHeight = 0.65, titleGap = 0.1;
  const titleBottom = sy + sh / 2 + titleGap;
  const titleTop = titleBottom + titleHeight;
  const bezel = new T.Mesh(
    new T.BoxGeometry(sw + 0.7, sh + 0.7, 0.3),
    new T.MeshLambertMaterial({ color: 0x222a4d }));
  bezel.position.set(cx, sy, sz - 0.18);
  bezel.userData.osvBigScreen = true;
  _osScene.add(bezel);
  // Title frame above the screen: a bezel plus a projected DOM strip
  // showing the conversation title (same quad transform as the screen).
  const titleBezel = new T.Mesh(
    new T.BoxGeometry(sw + 0.7, titleHeight + 0.1, 0.25),
    new T.MeshLambertMaterial({ color: 0x222a4d }));
  titleBezel.position.set(cx, (titleBottom + titleTop) / 2, sz - 0.2);
  titleBezel.userData.osvBigScreen = true;
  _osScene.add(titleBezel);
  _osTitleCorners = [
    { x: cx - sw / 2, y: titleTop, z: sz },
    { x: cx + sw / 2, y: titleTop, z: sz },
    { x: cx - sw / 2, y: titleBottom, z: sz },
    { x: cx + sw / 2, y: titleBottom, z: sz },
  ];
  if (!_osTitleEl && _osOverlay) {
    _osTitleEl = document.createElement('div');
    _osTitleEl.className = 'osv-convtitle';
    _osOverlay.appendChild(_osTitleEl);
  }
  _osScreenCorners = [
    { x: cx - sw / 2, y: sy + sh / 2, z: sz },
    { x: cx + sw / 2, y: sy + sh / 2, z: sz },
    { x: cx - sw / 2, y: sy - sh / 2, z: sz },
    { x: cx + sw / 2, y: sy - sh / 2, z: sz },
  ];

  // Blackboard on the left side of the office, facing the desks (+x).
  // Viewer's left is +z, so the top-left corner has the larger z.
  const bw = 6, bh = bw * OSV_BOARD_H / OSV_BOARD_W;
  const bx = -6.5, by = 2.4, bcz = 2;
  const frame = new T.Mesh(
    new T.BoxGeometry(0.25, bh + 0.5, bw + 0.5),
    new T.MeshLambertMaterial({ color: 0x5d3d21 }));
  frame.position.set(bx - 0.15, by, bcz);
  _osScene.add(frame);
  [-1, 1].forEach((s) => {
    const post = new T.Mesh(
      new T.BoxGeometry(0.18, by + bh / 2 + 0.3, 0.18),
      new T.MeshLambertMaterial({ color: 0x4a3118 }));
    post.position.set(bx - 0.15, (by + bh / 2 + 0.3) / 2, bcz + s * (bw / 2 + 0.1));
    _osScene.add(post);
  });
  _osBoardCorners = [
    { x: bx, y: by + bh / 2, z: bcz + bw / 2 },
    { x: bx, y: by + bh / 2, z: bcz - bw / 2 },
    { x: bx, y: by - bh / 2, z: bcz + bw / 2 },
    { x: bx, y: by - bh / 2, z: bcz - bw / 2 },
  ];
  if (!_osBoardEl && _osOverlay) {
    _osBoardEl = document.createElement('div');
    _osBoardEl.className = 'osv-board';
    const title = document.createElement('div');
    title.className = 'osv-board-title';
    title.textContent = t('osvBoardTitle');
    _osBoardListEl = document.createElement('div');
    _osBoardListEl.className = 'osv-board-list';
    _osBoardEl.append(title, _osBoardListEl);
    _osOverlay.appendChild(_osBoardEl);
  }
}

function _osStripProjectionIds(root) {
  if (!root) return root;
  if (root.id) root.removeAttribute('id');
  root.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
  return root;
}

function _osRefreshMessageProjection() {
  _osProjectionRaf = 0;
  const messages = document.getElementById('messages');
  if (!messages || !_osProjectedMessages) return;
  const wasAtBottom = _osProjectedMessages.scrollHeight
    - _osProjectedMessages.scrollTop - _osProjectedMessages.clientHeight < 40;
  _osProjectedMessages.innerHTML = '';
  Array.from(messages.children).forEach((node) => {
    _osProjectedMessages.appendChild(_osStripProjectionIds(node.cloneNode(true)));
  });
  if (wasAtBottom) _osProjectedMessages.scrollTop = _osProjectedMessages.scrollHeight;
}

function _osQueueMessageProjection() {
  if (_osProjectionRaf) return;
  _osProjectionRaf = requestAnimationFrame(_osRefreshMessageProjection);
}

// Mirror the canonical transcript instead of moving it. A single MutationObserver
// keeps this read-only wall projection current while the Webchat surface remains
// independently visible and interactive in another tile.
function _osProjectMessages(on) {
  const messages = document.getElementById('messages');
  if (!messages) return;
  if (on) {
    if (!_osScreenEl) {
      const overlay = document.getElementById('openspaceOverlay');
      if (!overlay) return;
      _osScreenEl = document.createElement('div');
      _osScreenEl.className = 'osv-bigscreen';
      overlay.appendChild(_osScreenEl);
    }
    if (!_osProjectedMessages) {
      _osProjectedMessages = document.createElement('div');
      _osProjectedMessages.className = 'messages osv-projected';
      _osProjectedMessages.setAttribute('aria-hidden', 'true');
      _osScreenEl.appendChild(_osProjectedMessages);
    }
    _osScreenEl.style.display = '';
    _osRefreshMessageProjection();
    if (!_osProjectionObserver && typeof MutationObserver !== 'undefined') {
      _osProjectionObserver = new MutationObserver(_osQueueMessageProjection);
      _osProjectionObserver.observe(messages, {
        childList: true, subtree: true, characterData: true, attributes: true,
      });
    }
    _osProjectedMessages.scrollTop = _osProjectedMessages.scrollHeight;
  } else {
    if (_osProjectionObserver) {
      _osProjectionObserver.disconnect();
      _osProjectionObserver = null;
    }
    if (_osProjectionRaf) cancelAnimationFrame(_osProjectionRaf);
    _osProjectionRaf = 0;
    if (_osScreenEl) _osScreenEl.style.display = 'none';
    _osScreenOcclusionRect = null;
    if (_osScreenOcclusionCanvas) _osScreenOcclusionCanvas.style.display = 'none';
  }
}

// Projective mapping (homography) from the element's pixel rect to the
// projected wall quad — adjugate method, no matrix library needed.
function _osAdj(m) {
  return [
    m[4] * m[8] - m[5] * m[7], m[2] * m[7] - m[1] * m[8], m[1] * m[5] - m[2] * m[4],
    m[5] * m[6] - m[3] * m[8], m[0] * m[8] - m[2] * m[6], m[2] * m[3] - m[0] * m[5],
    m[3] * m[7] - m[4] * m[6], m[1] * m[6] - m[0] * m[7], m[0] * m[4] - m[1] * m[3]];
}

function _osMulMM(a, b) {
  const c = [];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      c[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j]
        + a[i * 3 + 2] * b[6 + j];
    }
  }
  return c;
}

function _osBasisToPoints(p1, p2, p3, p4) {
  const m = [p1.x, p2.x, p3.x, p1.y, p2.y, p3.y, 1, 1, 1];
  const a = _osAdj(m);
  const v = [
    a[0] * p4.x + a[1] * p4.y + a[2],
    a[3] * p4.x + a[4] * p4.y + a[5],
    a[6] * p4.x + a[7] * p4.y + a[8]];
  return _osMulMM(m, [v[0], 0, 0, 0, v[1], 0, 0, 0, v[2]]);
}

function _osQuadTransform(w, h, pts) {
  const src = _osBasisToPoints(
    { x: 0, y: 0 }, { x: w, y: 0 }, { x: 0, y: h }, { x: w, y: h });
  const dst = _osBasisToPoints(pts[0], pts[1], pts[2], pts[3]);
  const t = _osMulMM(dst, _osAdj(src));
  if (!t.every(isFinite) || Math.abs(t[8]) < 1e-12) return null;
  for (let i = 0; i < 9; i++) t[i] /= t[8];
  return 'matrix3d(' + [
    t[0], t[3], 0, t[6],
    t[1], t[4], 0, t[7],
    0, 0, 1, 0,
    t[2], t[5], 0, t[8]].join(',') + ')';
}

const _osScreenVec = { v: null };
function _osProjectScreen() {
  const screenPts = _osProjectPanel(
    _osScreenEl, _osScreenCorners, OSV_SCREEN_W, OSV_SCREEN_H);
  _osSyncScreenOcclusion(screenPts);
  _osProjectPanel(_osBoardEl, _osBoardCorners, OSV_BOARD_W, OSV_BOARD_H);
  _osProjectPanel(_osTitleEl, _osTitleCorners, OSV_TITLE_W, OSV_TITLE_H);
  _osProjectPanel(_osTvEl, _osTvCorners, OSV_TV_W, OSV_TV_H);
}

function _osSyncScreenOcclusion(pts) {
  if (!_osScreenOcclusionCanvas || !_osOverlay || !pts) {
    _osScreenOcclusionRect = null;
    if (_osScreenOcclusionCanvas) _osScreenOcclusionCanvas.style.display = 'none';
    return;
  }
  const ow = _osOverlay.clientWidth, oh = _osOverlay.clientHeight;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const left = Math.max(0, Math.floor(Math.min(...xs)));
  const top = Math.max(0, Math.floor(Math.min(...ys)));
  const right = Math.min(ow, Math.ceil(Math.max(...xs)));
  const bottom = Math.min(oh, Math.ceil(Math.max(...ys)));
  if (right <= left || bottom <= top) {
    _osScreenOcclusionRect = null;
    _osScreenOcclusionCanvas.style.display = 'none';
    return;
  }
  _osScreenOcclusionRect = { left: left, top: top, right: right, bottom: bottom };
  const polygon = 'polygon(' + pts.map((p) => p.x + 'px ' + p.y + 'px').join(',') + ')';
  _osScreenOcclusionCanvas.style.clipPath = polygon;
  _osScreenOcclusionCanvas.style.webkitClipPath = polygon;
  _osScreenOcclusionCanvas.style.display = 'block';
}

function _osProjectPanel(el, corners, w, h) {
  if (!el || !corners || !_osCamera || !_osOverlay) return;
  const T = _osThree;
  if (!_osScreenVec.v) _osScreenVec.v = new T.Vector3();
  const v = _osScreenVec.v;
  const ow = _osOverlay.clientWidth, oh = _osOverlay.clientHeight;
  const pts = [];
  let zsum = 0;
  for (const c of corners) {
    v.set(c.x, c.y, c.z).project(_osCamera);
    if (v.z > 1) { el.style.display = 'none'; return null; }
    zsum += v.z;
    pts.push({ x: (v.x * 0.5 + 0.5) * ow, y: (-v.y * 0.5 + 0.5) * oh });
  }
  // Backface/edge-on culling: painting a quad seen from behind smears a
  // mirrored image across the scene, and an edge-on quad is a stretched
  // unreadable sliver — hide both instead of drawing garbage.
  const ux = pts[1].x - pts[0].x, uy = pts[1].y - pts[0].y;
  const wx = pts[2].x - pts[0].x, wy = pts[2].y - pts[0].y;
  if (ux * wy - uy * wx < 600) { el.style.display = 'none'; return null; }
  const transform = _osQuadTransform(w, h, pts);
  if (!transform) { el.style.display = 'none'; return null; }
  // The stylesheet default is display:none, so clearing the inline style
  // would hide the panel — it must be set explicitly.
  el.style.display = 'block';
  el.style.transform = transform;
  // DOM has no depth buffer: stack projected panels by camera distance
  // so a nearer screen always paints over a farther one.
  el.style.zIndex = String(Math.min(2500,
    Math.max(1, Math.round((1 - zsum / 4) * 2500))));
  return pts;
}

// Battery above each agent's head: context LEFT (100 − used %), mirroring
// the header gauge exactly (same source, same percentage, same colors) so
// the two never disagree. Hidden until the first reading exists.
function _osRefreshBatteries(now) {
  if (now - _osBattAt < 1000) return;
  _osBattAt = now;
  const usage = (typeof window !== 'undefined' && window._contextUsage) || null;
  if (!usage) return;
  _osAgents.forEach((rec) => {
    if (rec.kind === 'user' || !rec.battEl) return;
    const entry = usage[_osKey(rec.name)];
    if (!entry || !entry.max) { rec.battEl.style.display = 'none'; return; }
    const pct = Math.max(0, Math.min(1, entry.pct || 0));
    const leftInt = 100 - Math.round(pct * 100);
    rec.battEl.style.display = 'block';
    rec.battFill.style.width = leftInt + '%';
    rec.battFill.style.background = pct >= 0.80 ? '#f0ad4e' : '#4ecdc4';
    rec.battEl.title = leftInt + '%';
  });
}

// Chalk roster: one row per active agent (name — current tool/status,
// battery) plus per-agent controls: ⏸ interrupt and ■ stop reuse the
// active-agents tracker actions, so the board is also a control panel.
// The DOM is rebuilt only when the row model actually changes.
function _osUpdateBoard(now) {
  if (!_osBoardListEl || now - _osBoardAt < 1000) return;
  _osBoardAt = now;
  _osRefreshTitle();
  const rows = [];
  if (typeof activeInteractions !== 'undefined') {
    Object.values(activeInteractions || {}).forEach((it) => {
      if (it && it.name) rows.push(it);
    });
  }
  const usage = (typeof window !== 'undefined' && window._contextUsage) || {};
  const icons = { thinking: '\u{1F4AD}', talking: '\u{1F4AC}',
                  tool: '\u2699\uFE0F', waiting: '\u2753' };
  const model = rows.map((it) => {
    const entry = usage[_osKey(it.name)] || {};
    const pct = entry.pct || it.contextPct || 0;
    // Prefer the avatar's live state over the (staler) tracker status.
    const desk = _osAgents.get(_osKey(it.name));
    const doing = desk && icons[desk.state]
      ? icons[desk.state] + (desk.state === 'tool' && it.lastTool
        ? ' ' + it.lastTool : '')
      : (it.lastTool || it.status || '');
    return { name: it.name, taskId: it.taskId || '', doing: doing,
             batt: pct ? '\u{1F50B}' + (100 - Math.round(pct * 100)) + '%' : '' };
  });
  const sig = JSON.stringify(model);
  if (sig === _osBoardText) return;
  _osBoardText = sig;
  _osBoardListEl.textContent = '';
  if (!model.length) { _osBoardListEl.textContent = t('osvBoardIdle'); return; }
  model.forEach((m) => {
    const row = document.createElement('div');
    row.className = 'osv-board-row';
    const label = document.createElement('span');
    label.className = 'osv-board-row-label';
    label.textContent = '\u2022 ' + m.name
      + (m.doing ? ' \u2014 ' + m.doing : '') + (m.batt ? '  ' + m.batt : '');
    const pause = document.createElement('button');
    pause.className = 'osv-board-btn';
    pause.textContent = '\u23F8';
    pause.title = t('stopTitle');
    pause.onclick = () => {
      if (typeof interruptSingle === 'function') interruptSingle(m.name, m.taskId);
    };
    const stop = document.createElement('button');
    stop.className = 'osv-board-btn osv-board-btn-stop';
    stop.textContent = '\u25A0';
    stop.title = t('stop');
    stop.onclick = () => {
      if (typeof stopSingle === 'function') stopSingle(m.name, m.taskId);
    };
    row.append(label, pause, stop);
    _osBoardListEl.appendChild(row);
  });
}

// ── Resource posters ─────────────────────────────────────────────
// One poster per resources-menu entry, hung on the right wall. The
// scene is only a door: clicking a poster opens the matching regular
// panel/dialog, never a re-implementation of it.
const OSV_POSTERS = [
  ['flows', '\u{1F9E9}', 'flows',
   () => openspaceOpenFlowsDialog()],
  ['resources', '\u{1F9F0}', 'resources',
   () => openspaceToggleResourceBoards()],
  ['memories', '\u{1F9E0}', 'memories',
   () => { if (typeof cmdShowMemories === 'function') cmdShowMemories(); }],
  ['kg', '\u{1F578}\uFE0F', 'knowledgeGraph',
   () => { if (typeof cmdShowKg === 'function') cmdShowKg(); }],
  ['diary', '\u{1F4D4}', 'diary',
   () => { if (typeof cmdShowDiary === 'function') cmdShowDiary(); }],
  ['projectGraph', '\u{1F5FA}\uFE0F', 'projectGraph',
   () => { if (typeof cmdShowProjectGraph === 'function') cmdShowProjectGraph(); }],
  ['wiki', '\u{1F4DA}', 'projectWiki',
   () => { if (typeof cmdShowProjectWiki === 'function') cmdShowProjectWiki(); }],
  ['scratchpad', '\u{1F4DD}', 'scratchpad',
   () => { if (typeof cmdShowScratchpad === 'function') cmdShowScratchpad(); }],
  ['todos', '\u2705', 'osvTodos',
   () => { if (typeof showTodosDialog === 'function') showTodosDialog(); }],
  ['cost', '\u{1F4B0}', 'osvCost',
   () => { if (typeof showUsageCostPanel === 'function') showUsageCostPanel(); }],
  ['context', '\u{1F9FE}', 'context',
   () => { if (typeof cmdShowContext === 'function') cmdShowContext(); }],
  ['scheduled', '\u23F0', 'scheduledTasks',
   () => { if (typeof toggleSchedsPanel === 'function') toggleSchedsPanel(); }],
  ['files', '\u{1F4C1}', 'files',
   () => { if (typeof toggleFilesPanel === 'function') toggleFilesPanel(); }],
  ['desktop', '\u{1F5A5}\uFE0F', 'desktop',
   () => { if (typeof cmdDesktop === 'function') cmdDesktop('/desktop', ['/desktop']); }],
  ['terminal', '\u2328\uFE0F', 'terminal',
   () => { if (typeof cmdTerminal === 'function') cmdTerminal('/terminal', ['/terminal']); }],
  ['confirmations', '\u2705', 'confTitle',
   () => { if (typeof toggleConfirmationsPanel === 'function') toggleConfirmationsPanel(); }],
  ['tmux', '\u{1F4DF}', 'osvTmux',
   () => { if (typeof cmdAgentTmux === 'function') cmdAgentTmux(); }],
];
// Compact gallery on the office face of the meeting-room partition.
const OSV_POSTERS_PER_ROW = OSV_RESOURCE_WALL.columns;

function _osPosterTexture(icon, label) {
  const T = _osThree;
  const canvas = document.createElement('canvas');
  canvas.width = 256; canvas.height = 170;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#1d2447';
  ctx.fillRect(0, 0, 256, 170);
  ctx.strokeStyle = '#4da3ff';
  ctx.lineWidth = 6;
  ctx.strokeRect(5, 5, 246, 160);
  ctx.textAlign = 'center';
  ctx.font = '64px serif';
  ctx.fillText(icon, 128, 82);
  ctx.fillStyle = '#e6ecff';
  ctx.font = 'bold 26px sans-serif';
  ctx.fillText(String(label).slice(0, 18), 128, 146);
  return new T.CanvasTexture(canvas);
}

function _osBuildPosters() {
  const T = _osThree;
  const x = OSV_RESOURCE_WALL.faceX;
  OSV_POSTERS.forEach((p, i) => {
    const z = OSV_RESOURCE_WALL.zStart + (i % OSV_POSTERS_PER_ROW) * OSV_RESOURCE_WALL.zStep;
    const y = 0.55 + Math.floor(i / OSV_POSTERS_PER_ROW) * 0.94;
    const mesh = new T.Mesh(
      new T.PlaneGeometry(1.32, 0.78),
      new T.MeshBasicMaterial({ map: _osPosterTexture(p[1], t(p[2])) }));
    mesh.position.set(x, y, z);
    mesh.rotation.y = -Math.PI / 2;   // face the desks (-x)
    mesh.userData.osvPoster = p[0];
    _osScene.add(mesh);
  });
}

function _osOpenPoster(key) {
  const p = OSV_POSTERS.find((e) => e[0] === key);
  if (p) p[3]();
}
