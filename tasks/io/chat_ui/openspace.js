// ── Openspace 3D view ────────────────────────────────────────────
// A playful, non-technical presentation of the conversation: every agent
// sits at a desk in a low-poly isometric office. Speech and thought
// bubbles mirror the live SSE stream, the PC screen lights up while a
// tool runs, and delegating agents walk over to their delegate's desk.
// Clicking an agent's PC opens a dialog with that agent's recent
// activity as stacked detail blocks.
//
// Selectable from the view menu (eye icon) as a third `chat.view_mode`
// alongside classic/simplified. Under the hood the classic renderers
// keep producing every durable node — this module is a pure consumer of
// the SSE stream and never mutates conversation state.
//
// three.js is vendored flat as three.module.min.js (the /chat/js/{path}
// route matches a single segment, so no vendor/ subdirectory) and
// loaded lazily with a dynamic import the first time the mode is
// activated: users who never open the view never pay for it.

// Layout: desks on a fixed grid, seats allocated in order of first
// appearance. Deterministic within a session; stable enough visually.
const OSV_GRID_COLS = 3;
const OSV_DESK_SPACING = 7;
// Bubbles show the WHOLE message/thought (scrollable body); the cap is a
// runaway guard, not a display truncation.
const OSV_BUBBLE_MAX_CHARS = 8000;
const OSV_BUBBLE_COALESCE_MS = 250;
const OSV_BUBBLE_LINGER_MS = 6000;
// Per-agent activity log for the PC dialog (bounded ring).
const OSV_LOG_MAX = 120;
const OSV_LOG_BLOCK_PREVIEW = 160;
// Walk animation duration for a delegation trip (ms).
const OSV_WALK_MS = 1100;
const OSV_IDLE_AFTER_MS = 1500;
// Tool-drop animation: each tool_call drops a tool object onto the desk;
// it fades away once its result arrives (or the agent goes idle).
const OSV_TOOL_DROP_MS = 650;
const OSV_TOOL_FADE_MS = 900;
const OSV_TOOL_MAX = 4;
const OSV_TOOL_EMOJI = [
  [/read|cat|history/i, '\u{1F4D6}'],
  [/write|edit|patch|apply/i, '\u270F\uFE0F'],
  [/bash|shell|exec|terminal|cmd|run/i, '\u{1F4BB}'],
  [/grep|search|find|glob|query/i, '\u{1F50D}'],
  [/web|http|fetch|browser|url|screen/i, '\u{1F310}'],
  [/git/i, '\u{1F33F}'],
  [/test/i, '\u{1F9EA}'],
  [/image|photo|vision|generate/i, '\u{1F5BC}\uFE0F'],
  [/memory|remember|recall|kg_|diary/i, '\u{1F9E0}'],
  [/delegate|agent|a2a/i, '\u{1F91D}'],
];

let _osActive = false;
let _osThree = null;          // three.js module namespace (lazy import)
let _osThreeLoading = null;   // in-flight import promise
let _osScene = null, _osCamera = null, _osRenderer = null;
let _osRaf = 0;
let _osCanvas = null, _osOverlay = null;
let _osClock = 0;
let _osRaycaster = null;
let _osTweens = [];           // {obj, from:{x,z}, to:{x,z}, start, dur, onDone}
let _osCamAngle = Math.PI / 4, _osCamDist = 26, _osCamHeight = 18;
// right-drag / shift-drag pans on the floor plane; Ctrl+drag lifts the
// look-at target above the plane (y).
const _osCamPan = { x: 0, y: 0, z: 0 };
let _osDrag = null;
// agentKey → record. Never removed while active: an agent that spoke
// once keeps its desk for the whole session (flash agents keep a
// "guest" flag so V2 can retire them).
const _osAgents = new Map();
let _osSeatCount = 0;
// Users stand in a visitor row facing the desks (shared conversations can
// have several humans; each gets their own avatar keyed by author name).
let _osUserCount = 0;
// History seeding state: which conversation the records belong to, and
// which msg_ids are already reflected (seeded or received live).
let _osSeedConvId = null;
const _osSeededIds = new Set();
// Projection wall: the live simplified transcript is reparented into a
// DOM element that is perspective-mapped onto a big screen in the scene.
const OSV_SCREEN_W = 960, OSV_SCREEN_H = 540;
let _osScreenEl = null;
let _osScreenCorners = null;
let _osScreenHome = null;   // where #messages goes back on deactivation
// Blackboard: chalk roster of the active agents, projected like the wall
// screen. Batteries above heads mirror window._contextUsage.
const OSV_BOARD_W = 500, OSV_BOARD_H = 300;
let _osBoardEl = null, _osBoardListEl = null, _osBoardCorners = null;
let _osBoardAt = 0, _osBoardText = '';
let _osBattAt = 0;
let _osResizeObs = null;
// V4: resource posters on the right wall (click one → its panel) and a
// projected 3D stage showing one deployed flow live (blocks + links +
// moving current fed by flow_runtime_graph).
let _osFlow = null;          // {id, name, group, nodes, edges, timer, prevPan}
let _osFlowPollBusy = false;
const OSV_FLOW_POLL_MS = 2500;
const OSV_FLOW_RANK_DX = 4.2;
const OSV_FLOW_ROW_DZ = 3.0;
// Refresh cadence for the sub-menu dialog mirror (see the Resource
// sub-menu boards section).
const OSV_RES_SYNC_MS = 2000;
// V5: office door (conversation picker + a2a trips), per-conversation
// room palette, conversation title frame, mobile touch controls.
let _osDoorPos = null;
let _osTitleEl = null, _osTitleCorners = null, _osTitleText = '';
// FileStore TV: a lounge television that plays the conversation's
// FileStore files (video/image/audio) on a projected DOM panel.
const OSV_TV_W = 640, OSV_TV_H = 400;
let _osTvEl = null, _osTvBodyEl = null, _osTvTitleEl = null;
let _osTvCorners = null, _osTvMedia = null;
let _osRoomMats = null;            // recolored by _osApplyRoomStyle
let _osResizeTimer = 0, _osLastW = 0, _osLastH = 0;
const _osTouches = new Map();      // pointerId → {x,y} (pinch/two-finger)
let _osPinchPrev = null;
const _osFreeSeats = [];           // desk slots retired guests gave back
const OSV_TITLE_W = 600, OSV_TITLE_H = 70;
// Camera follows the viewer's avatar unless the user pans manually;
// walking (floor click) or the ⌂ reset re-engages the follow.
let _osFollow = true;

function openspaceIsActive() { return _osActive; }

function _osKey(name) { return String(name || '').toLowerCase(); }

function _osEventAgent(data) {
  return (data && (data.agent_name || (data.source && data.source.name)))
    || (typeof selectedAgent !== 'undefined' && selectedAgent) || '';
}

// Deterministic pastel from the agent name — same hue every session.
function _osAgentColor(name) {
  let h = 0;
  const s = _osKey(name);
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  return 'hsl(' + (Math.abs(h) % 360) + ', 62%, 55%)';
}

// ── Activation (called by the view-mode selector) ───────────────
function openspaceSetActive(on) {
  on = !!on;
  if (on === _osActive) return;
  _osActive = on;
  document.body.classList.toggle('openspace-active', on);
  const wrap = document.getElementById('openspaceWrap');
  if (!wrap) { _osActive = false; return; }
  wrap.style.display = on ? '' : 'none';
  _osProjectMessages(on);
  if (on) {
    // The resource screens mirror #resourcesContent; make sure it is
    // populated even if the sidebar Resources section was never opened.
    if (typeof loadResources === 'function') loadResources();
    _osEnsureThree().then(() => {
      if (!_osActive) return;
      _osBuildScene(wrap);
      _osSeedAgents();
      _osStartLoop();
    }).catch((e) => {
      console.error('openspace: three.js load failed', e);
      const err = document.createElement('div');
      err.className = 'osv-error';
      err.textContent = t('osvLoadError');
      wrap.appendChild(err);
    });
  } else {
    openspaceCloseFlow();
    openspaceTvStop();
    _osStopLoop();
  }
}

function _osEnsureThree() {
  if (_osThree) return Promise.resolve(_osThree);
  if (!_osThreeLoading) {
    const v = (typeof window !== 'undefined' && window.PAWFLOW_ASSET_VERSION) || '0';
    _osThreeLoading = import('/chat/js/three.module.min.js?v=' + encodeURIComponent(v))
      .then((mod) => { _osThree = mod; return mod; });
  }
  return _osThreeLoading;
}

// Desks for agents already known before the view opened: the selected
// agent plus everything the active-agents tracker has seen.
function _osSeedAgents() {
  if (typeof selectedAgent !== 'undefined' && selectedAgent) _osEnsureAgent(selectedAgent);
  if (typeof activeInteractions !== 'undefined') {
    Object.values(activeInteractions || {}).forEach((it) => {
      if (it && it.name) _osEnsureAgent(it.name);
    });
  }
  _osAgents.forEach((rec) => {
    if (rec.log.length) _osRefreshScreen(rec);
    _osRestoreBubbles(rec);
  });
}

// ── Scene ────────────────────────────────────────────────────────
function _osBuildScene(wrap) {
  const T = _osThree;
  if (_osRenderer) { _osResize(); return; }
  _osScene = new T.Scene();
  _osScene.background = new T.Color(0x10142a);
  _osScene.fog = new T.Fog(0x10142a, 40, 90);

  _osCamera = new T.PerspectiveCamera(42, 1, 0.1, 200);
  _osUpdateCamera();

  _osRenderer = new T.WebGLRenderer({ antialias: true });
  _osRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  _osCanvas = _osRenderer.domElement;
  _osCanvas.className = 'osv-canvas';
  wrap.appendChild(_osCanvas);

  _osOverlay = document.getElementById('openspaceOverlay');

  const ambient = new T.AmbientLight(0xffffff, 0.75);
  const sun = new T.DirectionalLight(0xffffff, 1.4);
  sun.position.set(12, 25, 8);
  _osScene.add(ambient, sun);

  const floor = new T.Mesh(
    new T.PlaneGeometry(120, 120),
    new T.MeshLambertMaterial({ color: 0x1a2140 }));
  floor.rotation.x = -Math.PI / 2;
  floor.name = 'floor';
  _osScene.add(floor);
  _osRoomMats = { floor: floor.material };
  const grid = new T.GridHelper(120, 60, 0x2c3560, 0x232b52);
  grid.position.y = 0.01;
  _osScene.add(grid);

  _osRaycaster = new T.Raycaster();
  _osBuildBigScreen();
  _osBuildDecor();
  _osBuildPosters();
  _osBuildDoor();
  _osBuildTv();
  _osCanvas.addEventListener('pointerdown', _osPointerDown);
  _osCanvas.addEventListener('pointermove', _osPointerMove);
  _osCanvas.addEventListener('pointerup', _osPointerUp);
  _osCanvas.addEventListener('pointercancel', _osPointerUp);
  _osCanvas.addEventListener('wheel', _osWheel, { passive: false });
  _osCanvas.addEventListener('contextmenu', (e) => e.preventDefault());
  window.addEventListener('resize', _osResize);
  document.addEventListener('visibilitychange', _osVisibility);
  // Escape always closes the flow stage (the ✕ button plus a keyboard
  // path that cannot be occluded by any projected panel).
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') openspaceCloseFlow();
  });
  // The wrap can resize without a window resize (sidebars, panels). A
  // stale canvas size stretches the WebGL image while overlay math uses
  // fresh dimensions — every projected element drifts off its mesh.
  if (typeof ResizeObserver === 'function') {
    _osResizeObs = new ResizeObserver(() => _osResize());
    _osResizeObs.observe(wrap);
  }
  if (!wrap.querySelector('.osv-help')) {
    const help = document.createElement('div');
    help.className = 'osv-help';
    help.textContent = t('osvHelp');
    wrap.appendChild(help);
  }
  if (!wrap.querySelector('.osv-mobile-ctl')) {
    // Touch controls: pinch/two-finger pan work on the canvas itself;
    // these buttons cover zoom and "I'm lost" on small screens.
    const ctl = document.createElement('div');
    ctl.className = 'osv-mobile-ctl';
    // Rotation belongs to the finger (one-finger drag orbits). Buttons:
    // ▲▼ raise/lower the camera, ◀▶ strafe left/right (same math as
    // the two-finger drag) — ⌂ re-engages the follow.
    const pan = (dx, dy) => {
      _osFollow = false;
      const k = _osCamDist * 0.0016;
      const a = _osCamAngle;
      _osCamPan.x += (-Math.sin(a) * dx + Math.cos(a) * dy) * k;
      _osCamPan.z += (Math.cos(a) * dx + Math.sin(a) * dy) * k;
      _osCamPan.x = Math.max(-40, Math.min(40, _osCamPan.x));
      _osCamPan.z = Math.max(-40, Math.min(40, _osCamPan.z));
    };
    [['\u25B2', () => { _osCamHeight = Math.min(40, _osCamHeight + 3); }],
     ['\u25BC', () => { _osCamHeight = Math.max(6, _osCamHeight - 3); }],
     ['\u25C0', () => pan(60, 0)],
     ['\u25B6', () => pan(-60, 0)],
     ['\u2795', () => { _osCamDist = Math.max(10, _osCamDist - 5); }],
     ['\u2796', () => { _osCamDist = Math.min(60, _osCamDist + 5); }],
     ['\u2302', () => {
       _osCamAngle = Math.PI / 4; _osCamDist = 26; _osCamHeight = 18;
       _osCamPan.x = 0; _osCamPan.y = 0; _osCamPan.z = 0;
       _osFollow = true;
     }]].forEach(([txt, fn]) => {
      const b = document.createElement('button');
      b.textContent = txt;
      b.onclick = () => { fn(); _osUpdateCamera(); };
      ctl.appendChild(b);
    });
    wrap.appendChild(ctl);
  }
  _osResize();
  _osApplyRoomStyle();
}

function _osUpdateCamera() {
  if (!_osCamera) return;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const rows = Math.max(1, Math.ceil(Math.max(_osSeatCount, 1) / OSV_GRID_COLS));
  const cz = ((rows - 1) * OSV_DESK_SPACING) / 2;
  _osCamera.position.set(
    cx + _osCamPan.x + Math.cos(_osCamAngle) * _osCamDist,
    _osCamHeight + _osCamPan.y,
    cz + _osCamPan.z + Math.sin(_osCamAngle) * _osCamDist);
  _osCamera.lookAt(cx + _osCamPan.x, _osCamPan.y, cz + _osCamPan.z);
}

function _osResize() {
  const wrap = document.getElementById('openspaceWrap');
  if (!wrap || !_osRenderer || !_osCamera) return;
  const w = wrap.clientWidth || 1, h = wrap.clientHeight || 1;
  if (w === _osLastW && h === _osLastH) return;
  const apply = () => {
    _osResizeTimer = 0;
    const w2 = wrap.clientWidth || 1, h2 = wrap.clientHeight || 1;
    _osLastW = w2; _osLastH = h2;
    _osRenderer.setSize(w2, h2);
    _osCamera.aspect = w2 / h2;
    _osCamera.updateProjectionMatrix();
  };
  // First sizing is immediate; later ones are debounced because mobile
  // keyboards animate the viewport and fire a resize per keystroke —
  // resizing the WebGL buffer each time blinks the whole scene.
  if (!_osLastW) { apply(); return; }
  if (_osResizeTimer) clearTimeout(_osResizeTimer);
  _osResizeTimer = setTimeout(apply, 150);
}

function _osVisibility() {
  if (document.hidden) _osStopLoop();
  else if (_osActive) _osStartLoop();
}

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
  const sw = 13, sh = sw * OSV_SCREEN_H / OSV_SCREEN_W;
  const sy = 2.3 + sh / 2, sz = -9;
  const bezel = new T.Mesh(
    new T.BoxGeometry(sw + 0.7, sh + 0.7, 0.3),
    new T.MeshLambertMaterial({ color: 0x222a4d }));
  bezel.position.set(cx, sy, sz - 0.18);
  const pole = new T.Mesh(
    new T.BoxGeometry(0.4, 2.3, 0.4),
    new T.MeshLambertMaterial({ color: 0x1b2140 }));
  pole.position.set(cx, 1.15, sz - 0.18);
  _osScene.add(bezel, pole);
  // Title frame above the screen: a bezel plus a projected DOM strip
  // showing the conversation title (same quad transform as the screen).
  const titleBezel = new T.Mesh(
    new T.BoxGeometry(sw + 0.7, 1.5, 0.25),
    new T.MeshLambertMaterial({ color: 0x222a4d }));
  titleBezel.position.set(cx, sy + sh / 2 + 1.0, sz - 0.2);
  _osScene.add(titleBezel);
  _osTitleCorners = [
    { x: cx - sw / 2, y: sy + sh / 2 + 1.6, z: sz },
    { x: cx + sw / 2, y: sy + sh / 2 + 1.6, z: sz },
    { x: cx - sw / 2, y: sy + sh / 2 + 0.45, z: sz },
    { x: cx + sw / 2, y: sy + sh / 2 + 0.45, z: sz },
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

// Reparent the real #messages element onto the screen (and back). A
// live move, never a copy: expanding blocks, scrolling and streaming
// all keep working because it is the same DOM the renderers write to.
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
    if (!_osScreenHome) {
      _osScreenHome = { parent: messages.parentNode, next: messages.nextSibling };
    }
    messages.classList.add('osv-projected');
    _osScreenEl.appendChild(messages);
    messages.scrollTop = messages.scrollHeight;
  } else if (_osScreenHome) {
    messages.classList.remove('osv-projected');
    _osScreenHome.parent.insertBefore(messages, _osScreenHome.next);
    _osScreenHome = null;
    if (_osScreenEl) _osScreenEl.style.display = 'none';
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
  _osProjectPanel(_osScreenEl, _osScreenCorners, OSV_SCREEN_W, OSV_SCREEN_H);
  _osProjectPanel(_osBoardEl, _osBoardCorners, OSV_BOARD_W, OSV_BOARD_H);
  _osProjectPanel(_osTitleEl, _osTitleCorners, OSV_TITLE_W, OSV_TITLE_H);
  _osProjectPanel(_osTvEl, _osTvCorners, OSV_TV_W, OSV_TV_H);
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
    if (v.z > 1) { el.style.display = 'none'; return; }
    zsum += v.z;
    pts.push({ x: (v.x * 0.5 + 0.5) * ow, y: (-v.y * 0.5 + 0.5) * oh });
  }
  // Backface/edge-on culling: painting a quad seen from behind smears a
  // mirrored image across the scene, and an edge-on quad is a stretched
  // unreadable sliver — hide both instead of drawing garbage.
  const ux = pts[1].x - pts[0].x, uy = pts[1].y - pts[0].y;
  const wx = pts[2].x - pts[0].x, wy = pts[2].y - pts[0].y;
  if (ux * wy - uy * wx < 600) { el.style.display = 'none'; return; }
  const transform = _osQuadTransform(w, h, pts);
  if (!transform) { el.style.display = 'none'; return; }
  // The stylesheet default is display:none, so clearing the inline style
  // would hide the panel — it must be set explicitly.
  el.style.display = 'block';
  el.style.transform = transform;
  // DOM has no depth buffer: stack projected panels by camera distance
  // so a nearer screen always paints over a farther one.
  el.style.zIndex = String(Math.max(1, Math.round((1 - zsum / 4) * 2500)));
}

// Battery above each agent's head: context USED, mirroring the header
// gauge exactly (same source, same percentage, same colors) so the two
// never disagree. Hidden until the first reading exists.
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
    rec.battEl.style.display = 'block';
    rec.battFill.style.width = (pct * 100).toFixed(0) + '%';
    rec.battFill.style.background = pct >= 0.80 ? '#f0ad4e' : '#4ecdc4';
    rec.battEl.title = Math.round(pct * 100) + '%';
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
             batt: pct ? '\u{1F50B}' + Math.round(pct * 100) + '%' : '' };
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
  ['plans', '\u{1F5C2}\uFE0F', 'plans',
   () => { if (typeof togglePlansPanel === 'function') togglePlansPanel(); }],
  ['scheduled', '\u23F0', 'scheduledTasks',
   () => { if (typeof toggleSchedsPanel === 'function') toggleSchedsPanel(); }],
  ['files', '\u{1F4C1}', 'files',
   () => { if (typeof toggleFilesPanel === 'function') toggleFilesPanel(); }],
  ['desktop', '\u{1F5A5}\uFE0F', 'desktop',
   () => { if (typeof cmdDesktop === 'function') cmdDesktop('/desktop', ['/desktop']); }],
  ['terminal', '\u2328\uFE0F', 'terminal',
   () => { if (typeof cmdTerminal === 'function') cmdTerminal('/terminal', ['/terminal']); }],
  ['tmux', '\u{1F4DF}', 'osvTmux',
   () => { if (typeof toggleGrab === 'function') toggleGrab(); }],
];
// Posters hang in rows of 9 along the right wall; a second row opens
// above the first when the list outgrows it.
const OSV_POSTERS_PER_ROW = 9;

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
  const x = (OSV_GRID_COLS - 1) * OSV_DESK_SPACING + 6.5;
  OSV_POSTERS.forEach((p, i) => {
    const z = -5 + (i % OSV_POSTERS_PER_ROW) * 2.1;
    const y = 2.5 + Math.floor(i / OSV_POSTERS_PER_ROW) * 1.9;
    const mesh = new T.Mesh(
      new T.PlaneGeometry(2.1, 1.4),
      new T.MeshBasicMaterial({ map: _osPosterTexture(p[1], t(p[2])) }));
    mesh.position.set(x, y, z);
    mesh.rotation.y = -Math.PI / 2;   // face the desks (-x)
    mesh.userData.osvPoster = p[0];
    _osScene.add(mesh);
    if (i < OSV_POSTERS_PER_ROW) {
      const post = new T.Mesh(
        new T.BoxGeometry(0.12, 2.0, 0.12),
        new T.MeshLambertMaterial({ color: 0x3b3f54 }));
      post.position.set(x + 0.12, 1.0, z);
      _osScene.add(post);
    }
  });
}

function _osOpenPoster(key) {
  const p = OSV_POSTERS.find((e) => e[0] === key);
  if (p) p[3]();
}

// ── Door, conversation rooms, title ─────────────────────────────
// The office door: clicking it opens the conversation picker, and a2a
// (cross-conversation) trips walk to it. Each conversation is a
// different "room": a palette derived deterministically from the
// conversation id (same conversation → same colors, always).
function _osBuildDoor() {
  const T = _osThree;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
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
  g.position.set(cx - 10.5, 0, -9);
  g.traverse((o) => { o.userData.osvDoor = true; });
  _osScene.add(g);
  _osDoorPos = { x: cx - 10.5, z: -9 };
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

function _osApplyRoomStyle() {
  if (!_osScene || !_osRoomMats) return;
  const cid = (typeof conversationId !== 'undefined' && conversationId) || 'default';
  const hue = (_osHashSeed(cid) % 360) / 360;
  _osScene.background.setHSL(hue, 0.42, 0.11);
  if (_osScene.fog) _osScene.fog.color.copy(_osScene.background);
  _osRoomMats.floor.color.setHSL(hue, 0.35, 0.17);
  if (_osRoomMats.rug) _osRoomMats.rug.color.setHSL((hue + 0.08) % 1, 0.42, 0.26);
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
// Smoothly keep the camera target glued to the viewer's avatar (the
// flow stage owns the pan while it is open).
function _osFollowUser() {
  if (!_osFollow || _osFlow || !_osCamera) return;
  const key = 'user:' + _osKey(
    (typeof window !== 'undefined' && window._userId) || 'user');
  const me = _osAgents.get(key);
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
}

// ── Resource sub-menu boards ─────────────────────────────────
// Clicking the Resources poster pops one labeled board per sub-section
// (Agents, Tasks, Flows, Services, Packages, Variables, Secrets, the
// repositories…). The sidebar renderer stays the single source of
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
  const x = (OSV_GRID_COLS - 1) * OSV_DESK_SPACING + 6.5;
  _osResSections().forEach((s, i) => {
    const count = s.body.querySelectorAll('div').length;
    const mesh = new T.Mesh(
      new T.PlaneGeometry(2.1, 1.1),
      new T.MeshBasicMaterial({ map: _osResBoardTexture(s.title, count) }));
    // Pop above the poster rows, however many the poster list needs.
    const boardBase = 2.5
      + Math.ceil(OSV_POSTERS.length / OSV_POSTERS_PER_ROW) * 1.9 + 0.2;
    mesh.position.set(x, boardBase + Math.floor(i / 8) * 1.4, -5 + (i % 8) * 2.1);
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
  // Live: actions (create, delete, move…) re-render the sidebar; the
  // dialog mirrors it on the same cadence.
  _osResDialogTimer = setInterval(fill, OSV_RES_SYNC_MS);
}

// ── Flows dialog + projected 3D workflow stage ──────────────────
function openspaceOpenFlowsDialog() {
  const prior = document.getElementById('osvFlowsDialog');
  if (prior) prior.remove();
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.id = 'osvFlowsDialog';
  const dialog = document.createElement('div');
  dialog.className = 'exec-dialog cog-dialog osv-dialog';
  const head = document.createElement('div');
  head.className = 'cog-head';
  const title = document.createElement('h3');
  title.textContent = t('flows');
  head.appendChild(title);
  const close = document.createElement('button');
  close.className = 'cog-close';
  close.innerHTML = '&times;';
  close.onclick = () => overlay.remove();
  const hint = document.createElement('div');
  hint.className = 'osv-flow-hint';
  hint.textContent = t('osvFlowPick');
  const list = document.createElement('div');
  list.className = 'osv-log';
  dialog.append(close, head, hint, list);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  if (typeof action$ !== 'function') return;
  // Same source as the sidebar Flows section: list_resources returns ALL
  // deployed instances visible to the user (conversation/user/global
  // scopes), where list_conv_flows only returns owner-scoped ones.
  action$('list_resources', {}).subscribe((data) => {
    const flows = (data && data.flows) || [];
    if (!flows.length) {
      const empty = document.createElement('div');
      empty.className = 'osv-log-empty';
      empty.textContent = t('noDeployedFlows');
      list.appendChild(empty);
      return;
    }
    flows.forEach((f) => {
      const id = f.instance_id || f.id;
      const label = f.flow_name || f.name || id;
      const scope = f.scope ? String(f.scope).charAt(0).toUpperCase() : '';
      const row = document.createElement('div');
      row.className = 'osv-block osv-flow-row';
      const name = document.createElement('span');
      name.textContent = (f.status === 'running' ? '\u25B6 ' : '\u23F9 ')
        + (scope ? '[' + scope + '] ' : '') + label + ' [' + (f.status || '?') + ']';
      const btn = document.createElement('button');
      btn.className = 'osv-flow-view-btn';
      btn.textContent = '\u{1F3AC} ' + t('osvFlowView');
      btn.onclick = () => { overlay.remove(); openspaceShowFlow(id, label); };
      row.append(name, btn);
      list.appendChild(row);
    });
  });
}

// The stage sits past the poster wall so it never overlaps the desks;
// opening a flow pans the camera there (orbit/zoom stay free), closing
// restores the exact previous framing.
function _osFlowZone() {
  return { x: (OSV_GRID_COLS - 1) * OSV_DESK_SPACING + 16, z: 0 };
}

function openspaceShowFlow(instanceId, name) {
  openspaceCloseFlow();
  const T = _osThree;
  if (!T || !_osScene) return;
  const zone = _osFlowZone();
  const group = new T.Group();
  group.position.set(zone.x, 0, zone.z);
  _osScene.add(group);
  _osFlow = { id: instanceId, name: name, group: group,
              nodes: new Map(), edges: [], timer: 0,
              prevPan: { x: _osCamPan.x, y: _osCamPan.y, z: _osCamPan.z },
              // Drill-down stack: level 0 is the instance; entering a
              // process group / subflow pushes its flow_ref.
              stack: [{ name: name }], lastNodes: {} };
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const rows = Math.max(1, Math.ceil(Math.max(_osSeatCount, 1) / OSV_GRID_COLS));
  const cz = ((rows - 1) * OSV_DESK_SPACING) / 2;
  _osCamPan.x = zone.x - cx + 4;
  _osCamPan.z = zone.z - cz;
  _osCamPan.y = 1.5;
  _osUpdateCamera();
  const wrap = document.getElementById('openspaceWrap');
  if (wrap && !wrap.querySelector('.osv-flow-close')) {
    const btn = document.createElement('button');
    btn.className = 'osv-flow-close';
    btn.textContent = '\u2715 ' + String(name || instanceId);
    btn.title = t('osvFlowClose');
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      openspaceCloseFlow();
    });
    // Some environments swallow the click after a pointer capture;
    // pointerdown is the earliest reliable signal.
    btn.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      openspaceCloseFlow();
    });
    wrap.appendChild(btn);
  }
  _osFlowPoll();
  _osFlow.timer = setInterval(_osFlowPoll, OSV_FLOW_POLL_MS);
}

function openspaceCloseFlow() {
  if (!_osFlow) return;
  const f = _osFlow;
  _osFlow = null;
  if (f.timer) clearInterval(f.timer);
  if (_osScene && f.group) {
    _osScene.remove(f.group);
    f.group.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) {
        if (o.material.map) o.material.map.dispose();
        o.material.dispose();
      }
    });
  }
  const btn = document.querySelector('#openspaceWrap .osv-flow-close');
  if (btn) btn.remove();
  _osCamPan.x = f.prevPan.x; _osCamPan.y = f.prevPan.y; _osCamPan.z = f.prevPan.z;
  _osUpdateCamera();
}

function _osFlowPoll() {
  if (!_osFlow || _osFlowPollBusy || typeof action$ !== 'function') return;
  _osFlowPollBusy = true;
  const id = _osFlow.id;
  const level = _osFlow.stack[_osFlow.stack.length - 1];
  const body = level && level.flow_ref
    ? { flow_ref: level.flow_ref } : { instance_id: id };
  const depth = _osFlow.stack.length;
  action$('flow_runtime_graph', body).subscribe({
    next: (d) => {
      _osFlowPollBusy = false;
      if (!_osFlow || _osFlow.id !== id || _osFlow.stack.length !== depth
          || !d || d.error) return;
      _osFlowApply(d.nodes || {}, d.edges || []);
    },
    error: () => { _osFlowPollBusy = false; },
  });
}

// Clear the stage geometry (level change) and refetch immediately.
function _osFlowRebuild() {
  const f = _osFlow;
  if (!f) return;
  f.group.children.slice().forEach((o) => {
    f.group.remove(o);
    o.traverse((c) => {
      if (c.geometry) c.geometry.dispose();
      if (c.material) {
        if (c.material.map) c.material.map.dispose();
        c.material.dispose();
      }
    });
  });
  f.nodes.clear();
  f.edges = [];
  f.lastNodes = {};
  _osFlowPollBusy = false;
  _osFlowPoll();
}

// Enter a process group / subflow block; the poll switches to its
// static flow_ref graph.
function _osFlowDrill(id) {
  const f = _osFlow;
  if (!f) return;
  const st = f.lastNodes[id];
  const ref = st && st.subflow_ref;
  if (!ref || !Object.keys(ref).length) return;
  f.stack.push({ flow_ref: ref, name: (st.group_name || id) });
  _osFlowRebuild();
}

function _osFlowUp() {
  const f = _osFlow;
  if (!f || f.stack.length < 2) return;
  f.stack.pop();
  _osFlowRebuild();
}

// Longest-path ranking left→right; a bounded relaxation so cycles
// simply stop moving once ranks saturate instead of looping forever.
function _osFlowLayout(nodes, edges) {
  const ids = Object.keys(nodes).sort();
  const rank = {};
  ids.forEach((id) => { rank[id] = 0; });
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    edges.forEach((e) => {
      if (!(e.source in rank) || !(e.target in rank)) return;
      if (rank[e.target] < rank[e.source] + 1 && rank[e.target] < ids.length) {
        rank[e.target] = rank[e.source] + 1; moved = true;
      }
    });
    if (!moved) break;
  }
  const lanes = {};
  const pos = {};
  ids.forEach((id) => {
    const r = rank[id];
    lanes[r] = (lanes[r] || 0) + 1;
    pos[id] = { x: r * OSV_FLOW_RANK_DX, z: (lanes[r] - 1) * OSV_FLOW_ROW_DZ };
  });
  ids.forEach((id) => {
    pos[id].z -= ((lanes[rank[id]] - 1) * OSV_FLOW_ROW_DZ) / 2;
  });
  return pos;
}

function _osFlowNodeColor(st) {
  if (!st) return 0x555b77;
  if ((st.error_count || 0) > 0 || st.error) return 0xe94560;
  // Process groups / subflows are doors: blue says "click to enter".
  if (st.subflow_ref && Object.keys(st.subflow_ref).length) return 0x4dabf7;
  return st.state === 'running' ? 0x2f9e44 : 0x555b77;
}

function _osFlowLabel(text) {
  const T = _osThree;
  const canvas = document.createElement('canvas');
  canvas.width = 256; canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.textAlign = 'center';
  ctx.fillStyle = '#e6ecff';
  ctx.font = 'bold 22px sans-serif';
  ctx.fillText(String(text).slice(0, 22), 128, 28);
  const sprite = new T.Sprite(new T.SpriteMaterial({
    map: new T.CanvasTexture(canvas), transparent: true }));
  sprite.scale.set(3.4, 0.85, 1);
  return sprite;
}

// First response builds blocks/links once; every poll only refreshes
// colors and current so the stage never flickers.
function _osFlowApply(nodes, edges) {
  const T = _osThree;
  const f = _osFlow;
  if (!f) return;
  f.lastNodes = nodes;
  if (!f.nodes.size) {
    const pos = _osFlowLayout(nodes, edges);
    const spans = Object.values(pos);
    const w = spans.reduce((m, p) => Math.max(m, p.x), 0) + 6;
    const d = spans.reduce((m, p) => Math.max(m, Math.abs(p.z)), 0) * 2 + 8;
    const stage = new T.Mesh(
      new T.BoxGeometry(w, 0.06, d),
      new T.MeshLambertMaterial({ color: 0x151b38 }));
    stage.position.set(w / 2 - 3, 0.03, 0);
    f.group.add(stage);
    // In-scene close control: a raycast click on it always works, even
    // if some overlay is eating the DOM button's events.
    const closeCanvas = document.createElement('canvas');
    closeCanvas.width = closeCanvas.height = 64;
    const cctx = closeCanvas.getContext('2d');
    cctx.fillStyle = '#e94560';
    cctx.beginPath();
    cctx.arc(32, 32, 30, 0, Math.PI * 2);
    cctx.fill();
    cctx.strokeStyle = '#fff';
    cctx.lineWidth = 8;
    cctx.beginPath();
    cctx.moveTo(20, 20); cctx.lineTo(44, 44);
    cctx.moveTo(44, 20); cctx.lineTo(20, 44);
    cctx.stroke();
    const closeSprite = new T.Sprite(new T.SpriteMaterial({
      map: new T.CanvasTexture(closeCanvas), transparent: true }));
    closeSprite.scale.set(1.3, 1.3, 1);
    closeSprite.position.set(-2.6, 3.6, 0);
    closeSprite.userData.osvFlowClose = true;
    f.group.add(closeSprite);
    if (f.stack.length > 1) {
      // 3D up-arrow: one level back out of the subflow.
      const upCanvas = document.createElement('canvas');
      upCanvas.width = upCanvas.height = 64;
      const uctx = upCanvas.getContext('2d');
      uctx.fillStyle = '#2f9e44';
      uctx.beginPath();
      uctx.arc(32, 32, 30, 0, Math.PI * 2);
      uctx.fill();
      uctx.fillStyle = '#fff';
      uctx.beginPath();
      uctx.moveTo(32, 12); uctx.lineTo(50, 34); uctx.lineTo(38, 34);
      uctx.lineTo(38, 52); uctx.lineTo(26, 52); uctx.lineTo(26, 34);
      uctx.lineTo(14, 34);
      uctx.closePath();
      uctx.fill();
      const upSprite = new T.Sprite(new T.SpriteMaterial({
        map: new T.CanvasTexture(upCanvas), transparent: true }));
      upSprite.scale.set(1.3, 1.3, 1);
      upSprite.position.set(-2.6, 2.1, 0);
      upSprite.userData.osvFlowUp = true;
      f.group.add(upSprite);
    }
    Object.keys(pos).forEach((id) => {
      const mesh = new T.Mesh(
        new T.BoxGeometry(2.0, 1.1, 1.4),
        new T.MeshLambertMaterial({ color: 0x555b77 }));
      mesh.position.set(pos[id].x, 0.8, pos[id].z);
      mesh.userData.osvFlowNode = id;
      const label = _osFlowLabel(id);
      label.position.set(pos[id].x, 1.95, pos[id].z);
      label.userData.osvFlowNode = id;
      f.group.add(mesh, label);
      f.nodes.set(id, { mesh: mesh, label: label, pos: pos[id], inFlight: false });
    });
    edges.forEach((e) => {
      const a = f.nodes.get(e.source), b = f.nodes.get(e.target);
      if (!a || !b) return;
      const line = new T.Line(
        new T.BufferGeometry().setFromPoints([
          new T.Vector3(a.pos.x, 0.8, a.pos.z),
          new T.Vector3(b.pos.x, 0.8, b.pos.z)]),
        new T.LineBasicMaterial({ color: 0x4da3ff }));
      f.group.add(line);
      const particles = [];
      for (let i = 0; i < 3; i++) {
        const dot = new T.Mesh(
          new T.SphereGeometry(0.09, 8, 6),
          new T.MeshBasicMaterial({ color: 0x74c0fc }));
        dot.visible = false;
        f.group.add(dot);
        particles.push({ mesh: dot, phase: i / 3 });
      }
      f.edges.push({
        key: e.source + '>' + e.target + '>' + (e.relationship || ''),
        src: a, dst: b, line: line, particles: particles,
        queue: 0, backpressured: false, active: false });
    });
  }
  f.nodes.forEach((n, id) => {
    const st = nodes[id];
    n.mesh.material.color.setHex(_osFlowNodeColor(st));
    n.inFlight = !!(st && st.in_flight);
  });
  edges.forEach((e) => {
    const key = e.source + '>' + e.target + '>' + (e.relationship || '');
    const rec = f.edges.find((x) => x.key === key);
    if (!rec) return;
    rec.queue = e.queue_size || 0;
    rec.backpressured = !!e.backpressured;
    const src = nodes[e.source] || {};
    rec.active = rec.queue > 0 || !!src.in_flight || src.state === 'running';
    rec.line.material.color.setHex(rec.backpressured ? 0xe94560 : 0x4da3ff);
  });
}

// Moving current: dots run along every active link; the queue size sets
// how many dots show, backpressure turns the whole link red.
function _osTickFlow(ts) {
  const f = _osFlow;
  if (!f) return;
  f.nodes.forEach((n) => {
    n.mesh.position.y = n.inFlight
      ? 0.8 + Math.abs(Math.sin(ts / 140)) * 0.15 : 0.8;
  });
  f.edges.forEach((e) => {
    const show = e.active
      ? Math.min(e.particles.length, 1 + Math.min(2, e.queue)) : 0;
    e.particles.forEach((p, i) => {
      if (i >= show) { p.mesh.visible = false; return; }
      p.mesh.visible = true;
      p.mesh.material.color.setHex(e.backpressured ? 0xe94560 : 0x74c0fc);
      const u = ((ts / 1400) + p.phase) % 1;
      p.mesh.position.set(
        e.src.pos.x + (e.dst.pos.x - e.src.pos.x) * u,
        0.95 + Math.sin(u * Math.PI) * 0.25,
        e.src.pos.z + (e.dst.pos.z - e.src.pos.z) * u);
    });
  });
}

// ── Agents & desks ───────────────────────────────────────────────
function _osSeatPosition(index) {
  const col = index % OSV_GRID_COLS;
  const row = Math.floor(index / OSV_GRID_COLS);
  return { x: col * OSV_DESK_SPACING, z: row * OSV_DESK_SPACING };
}

function _osEnsureAgent(name, opts) {
  const key = _osKey(name);
  if (!key) return null;
  let rec = _osAgents.get(key);
  if (rec) return rec;
  // Guests hand their slot back on retirement; reuse those first so
  // repeated flash delegations do not march desks toward the horizon.
  const seatIndex = _osFreeSeats.length ? _osFreeSeats.shift() : _osSeatCount++;
  rec = {
    key: key,
    name: name,
    kind: 'agent',
    guest: !!(opts && opts.guest),
    state: 'idle',
    stateSince: Date.now(),
    seat: _osSeatPosition(seatIndex),
    seatIndex: seatIndex,
    color: _osAgentColor(name),
    log: [],
    tools: [],
    lastSpeech: null, lastThought: null,
    group: null, avatar: null, screenMat: null,
    labelEl: null, speechEl: null, thoughtEl: null, statusEl: null,
    speechText: '', speechAt: 0, speechFlushTimer: 0,
    thoughtText: '', thoughtAt: 0,
    homeSeat: null, awayAt: null,
  };
  rec.homeSeat = rec.seat;
  _osAgents.set(key, rec);
  if (_osScene && _osThree) _osBuildDesk(rec);
  _osUpdateCamera();
  return rec;
}

// A human participant. No desk, no PC: a standing visitor in front of the
// office, one per distinct author (shared conversations have several).
function _osEnsureUser(name) {
  const clean = String(name || '').trim() || 'user';
  const key = 'user:' + _osKey(clean);
  let rec = _osAgents.get(key);
  if (rec) return rec;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  rec = {
    key: key,
    name: clean,
    kind: 'user',
    guest: false,
    state: 'idle',
    stateSince: Date.now(),
    seat: { x: cx + (_osUserCount % 2 === 0 ? 1 : -1)
            * Math.ceil(_osUserCount / 2) * 3.5, z: -4.5 },
    color: _osAgentColor(clean),
    log: [],
    tools: [],
    lastSpeech: null, lastThought: null,
    group: null, avatar: null, screenMat: null,
    labelEl: null, speechEl: null, thoughtEl: null, statusEl: null,
    speechText: '', speechAt: 0, speechFlushTimer: 0,
    thoughtText: '', thoughtAt: 0,
    homeSeat: null, awayAt: null,
  };
  rec.homeSeat = rec.seat;
  _osUserCount++;
  _osAgents.set(key, rec);
  if (_osScene && _osThree) _osBuildDesk(rec);
  return rec;
}

function _osBuildDesk(rec) {
  const T = _osThree;
  if (rec.kind === 'user') { _osBuildVisitor(rec); return; }
  const g = new T.Group();
  g.position.set(rec.seat.x, 0, rec.seat.z);

  const desk = new T.Mesh(
    new T.BoxGeometry(3.2, 0.25, 1.6),
    new T.MeshLambertMaterial({ color: 0x8a5a33 }));
  desk.position.y = 1.1;
  g.add(desk);
  [[-1.4, -0.6], [1.4, -0.6], [-1.4, 0.6], [1.4, 0.6]].forEach((p) => {
    const leg = new T.Mesh(
      new T.BoxGeometry(0.15, 1.1, 0.15),
      new T.MeshLambertMaterial({ color: 0x5d3d21 }));
    leg.position.set(p[0], 0.55, p[1]);
    g.add(leg);
  });

  // The PC: body + screen. The screen material's emissive channel is the
  // "working" signal; raycast hits on any part open the agent dialog.
  const pc = new T.Group();
  const screenMat = new T.MeshLambertMaterial({
    color: 0x101018, emissive: 0x000000 });
  const screen = new T.Mesh(new T.BoxGeometry(1.5, 0.9, 0.08), screenMat);
  screen.position.set(0, 1.85, -0.45);
  const stand = new T.Mesh(
    new T.BoxGeometry(0.15, 0.5, 0.15),
    new T.MeshLambertMaterial({ color: 0x333344 }));
  stand.position.set(0, 1.35, -0.45);
  pc.add(screen, stand);
  pc.traverse((o) => { o.userData.osvAgent = rec.key; });
  g.add(pc);
  rec.screenMat = screenMat;

  // Avatar: chibi mascot; front features live on local +z, π turns it toward
  // its desk and PC.
  const avatar = _osBuildChibi(rec);
  avatar.position.set(rec.seat.x, 0, rec.seat.z + 1.35);
  avatar.rotation.y = Math.PI;
  avatar.traverse((o) => { o.userData.osvAgent = rec.key; });
  _osScene.add(avatar);
  rec.avatar = avatar;

  // Selection halo, toggled per-frame against the live selectedAgent.
  const halo = new T.Mesh(
    new T.RingGeometry(0.9, 1.15, 32),
    new T.MeshBasicMaterial({ color: 0x4da3ff, transparent: true, opacity: 0.8 }));
  halo.rotation.x = -Math.PI / 2;
  halo.position.y = 0.03;
  halo.visible = false;
  avatar.add(halo);
  rec.halo = halo;

  _osScene.add(g);
  rec.group = g;

  _osBuildOverlayEls(rec, rec.name, '');
  _osRestoreBubbles(rec);
}

// Cute low-poly mascot: round body, big eyes, smile, blush, stubby arms
// and feet, plus a per-agent silhouette (round ears, horns, antennae, or
// smooth) derived from the name hash. Front features sit on local +z.
function _osBuildChibi(rec) {
  const T = _osThree;
  const g = new T.Group();
  const mat = (c) => new T.MeshLambertMaterial({ color: c });
  const bodyMat = mat(new T.Color(rec.color));
  const body = new T.Mesh(new T.SphereGeometry(0.62, 20, 16), bodyMat);
  body.scale.set(1, 1.12, 0.92);
  body.position.y = 0.95;
  const belly = new T.Mesh(new T.SphereGeometry(0.34, 14, 10), mat(0xf6f3ee));
  belly.scale.set(1, 1.15, 0.55);
  belly.position.set(0, 0.78, 0.34);
  g.add(body, belly);
  [-1, 1].forEach((s) => {
    const eye = new T.Mesh(new T.SphereGeometry(0.13, 10, 8), mat(0xffffff));
    eye.position.set(0.21 * s, 1.22, 0.5);
    const pupil = new T.Mesh(new T.SphereGeometry(0.065, 8, 6), mat(0x101018));
    pupil.position.set(0.21 * s, 1.22, 0.6);
    const blush = new T.Mesh(new T.SphereGeometry(0.06, 8, 6), mat(0xffa8a8));
    blush.scale.set(1, 0.7, 0.4);
    blush.position.set(0.36 * s, 1.02, 0.46);
    const arm = new T.Mesh(new T.SphereGeometry(0.14, 8, 6), bodyMat);
    arm.scale.set(0.8, 1.5, 0.8);
    arm.position.set(0.62 * s, 0.85, 0.1);
    const foot = new T.Mesh(new T.SphereGeometry(0.15, 8, 6), bodyMat);
    foot.scale.set(1, 0.55, 1.25);
    foot.position.set(0.26 * s, 0.09, 0.22);
    g.add(eye, pupil, blush, arm, foot);
  });
  const smile = new T.Mesh(
    new T.TorusGeometry(0.11, 0.025, 6, 12, Math.PI), mat(0x101018));
  smile.position.set(0, 1.02, 0.55);
  smile.rotation.z = Math.PI;   // arc opens upward → a smile
  g.add(smile);
  let hash = 0;
  for (const ch of rec.key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const style = hash % 4;
  if (style === 0) {
    [-1, 1].forEach((s) => {
      const ear = new T.Mesh(new T.SphereGeometry(0.16, 10, 8), bodyMat);
      ear.position.set(0.42 * s, 1.62, 0);
      g.add(ear);
    });
  } else if (style === 1) {
    [-1, 1].forEach((s) => {
      const horn = new T.Mesh(new T.ConeGeometry(0.09, 0.3, 8), mat(0x3b3f54));
      horn.position.set(0.32 * s, 1.7, 0);
      horn.rotation.z = -0.5 * s;
      g.add(horn);
    });
  } else if (style === 2) {
    [-1, 1].forEach((s) => {
      const stem = new T.Mesh(
        new T.CylinderGeometry(0.025, 0.025, 0.42, 6), mat(0x3b3f54));
      stem.position.set(0.18 * s, 1.8, 0);
      stem.rotation.z = -0.35 * s;
      const tip = new T.Mesh(new T.SphereGeometry(0.07, 8, 6), mat(0xffd43b));
      tip.position.set(0.25 * s, 2.0, 0);
      g.add(stem, tip);
    });
  }
  return g;
}

// DOM overlay elements (real text beats font atlases: i18n, wrapping,
// theme CSS all come for free).
function _osBuildOverlayEls(rec, labelText, extraLabelClass) {
  if (!_osOverlay) return;
  const label = document.createElement('div');
  label.className = 'osv-label' + (extraLabelClass ? ' ' + extraLabelClass : '');
  label.textContent = labelText;
  label.style.background = rec.color;
  const speech = document.createElement('div');
  speech.className = 'osv-bubble osv-speech';
  speech.style.display = 'none';
  const speechBody = document.createElement('div');
  speechBody.className = 'osv-bubble-body';
  speech.appendChild(speechBody);
  const thought = document.createElement('div');
  thought.className = 'osv-bubble osv-thought';
  thought.style.display = 'none';
  const thoughtBody = document.createElement('div');
  thoughtBody.className = 'osv-bubble-body';
  thought.appendChild(thoughtBody);
  const status = document.createElement('div');
  status.className = 'osv-status';
  status.style.display = 'none';
  // Any bubble can spoil the view: a ✕ dismisses it. It comes back by
  // itself with the next message/thought (_osShowBubble re-shows).
  [speech, thought].forEach((el) => {
    const x = document.createElement('span');
    x.className = 'osv-bubble-close';
    x.textContent = '\u00D7';
    x.onclick = (ev) => { ev.stopPropagation(); el.style.display = 'none'; };
    el.appendChild(x);
  });
  const batt = document.createElement('div');
  batt.className = 'osv-batt';
  batt.style.display = 'none';
  const battFill = document.createElement('div');
  battFill.className = 'osv-batt-fill';
  batt.appendChild(battFill);
  _osOverlay.append(label, speech, thought, status, batt);
  rec.labelEl = label; rec.speechEl = speech;
  rec.thoughtEl = thought; rec.statusEl = status;
  rec.speechBodyEl = speechBody; rec.thoughtBodyEl = thoughtBody;
  rec.battEl = batt; rec.battFill = battFill;
}

// Standing human avatar: slimmer capsule, no desk, facing the office.
function _osBuildVisitor(rec) {
  const T = _osThree;
  const avatar = new T.Group();
  const body = new T.Mesh(
    new T.CapsuleGeometry(0.36, 0.85, 4, 12),
    new T.MeshLambertMaterial({ color: new T.Color(rec.color) }));
  body.position.y = 1.05;
  const head = new T.Mesh(
    new T.SphereGeometry(0.3, 16, 12),
    new T.MeshLambertMaterial({ color: 0xf2d0b0 }));
  head.position.y = 2.0;
  avatar.add(body, head);
  avatar.position.set(rec.seat.x, 0, rec.seat.z);
  avatar.traverse((o) => { o.userData.osvAgent = rec.key; });
  _osScene.add(avatar);
  rec.avatar = avatar;
  rec.group = avatar;  // marks the record as built (users have no desk)
  _osBuildOverlayEls(rec, '\u{1F464} ' + rec.name, 'osv-label-user');
  _osRestoreBubbles(rec);
}

// ── State machine ────────────────────────────────────────────────
// idle → thinking → talking → tool → waiting, driven purely by SSE.
function _osSetState(rec, state, detail) {
  if (!rec) return;
  rec.state = state;
  rec.stateSince = Date.now();
  // Whatever is still on the desk when the agent stops working never got
  // a result event; sweep it away instead of leaving orphaned props.
  if (state === 'idle' && rec.tools && rec.tools.length) {
    rec.tools.forEach((entry) => {
      if (entry.phase !== 'fade') {
        entry.phase = 'fade';
        entry.fadeStart = performance.now();
      }
    });
  }
  if (rec.statusEl) {
    const icons = { thinking: '\u{1F4AD}', talking: '\u{1F4AC}',
                    tool: '\u2699\uFE0F', waiting: '\u2753', idle: '' };
    const icon = icons[state] || '';
    const text = icon ? (icon + (detail ? ' ' + detail : '')) : '';
    rec.statusEl.textContent = text;
    rec.statusEl.style.display = text ? '' : 'none';
    rec.statusEl.classList.toggle('osv-status-busy',
      state === 'thinking' || state === 'talking' || state === 'tool');
  }
  _osRefreshScreen(rec);
}

function _osRefreshScreen(rec) {
  if (!rec.screenMat || !_osThree) return;
  const on = rec.state === 'tool';
  const busy = rec.state === 'thinking' || rec.state === 'talking';
  rec.screenMat.emissive.setHex(on ? 0x2f9e44 : (busy ? 0x1c4d8f : 0x000000));
}

// ── Bubbles ──────────────────────────────────────────────────────
// Stored multimodal messages carry content as an array of blocks
// ({type:'text', text} plus images/files); bubbles only ever show the
// text parts — String(array) would render "[object Object]".
function _osText(content) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((b) => {
      if (typeof b === 'string') return b;
      if (b && typeof b.text === 'string') return b.text;
      return '';
    }).join(' ');
  }
  return content == null ? '' : String(content);
}

function _osFull(text) {
  // Newlines are kept (pre-wrap body): the bubble is meant to be READ,
  // not glanced at. Only a runaway tail-cap applies.
  const s = _osText(text).replace(/^\s+|\s+$/g, '');
  return s.length > OSV_BUBBLE_MAX_CHARS
    ? '\u2026' + s.slice(-(OSV_BUBBLE_MAX_CHARS - 1)) : s;
}

// Write a bubble's scrollable body and keep it pinned to the newest text.
function _osSetBubbleText(rec, kind, text) {
  const el = kind === 'thought' ? rec.thoughtEl : rec.speechEl;
  const body = kind === 'thought' ? rec.thoughtBodyEl : rec.speechBodyEl;
  if (!el || !body) return;
  body.textContent = text;
  el.style.display = '';
  body.scrollTop = body.scrollHeight;
}

function _osShowBubble(rec, kind, text) {
  const el = kind === 'thought' ? rec.thoughtEl : rec.speechEl;
  const full = _osFull(text);
  if (!full) return;
  const stamp = Date.now();
  if (kind === 'thought') {
    rec.thoughtAt = stamp;
    rec.lastThought = { text: full, at: stamp };
  } else {
    rec.speechAt = stamp;
    rec.lastSpeech = { text: full, at: stamp };
  }
  if (!el) return;
  el.classList.remove('osv-stale');
  _osSetBubbleText(rec, kind, full);
}

// Remember a bubble without touching the DOM (history seeding). Newest
// wins; timestamps arrive in seconds from stored messages.
function _osRememberBubble(rec, kind, text, ts) {
  const full = _osFull(text);
  if (!full) return;
  const at = ts ? (ts > 1e12 ? ts : ts * 1000) : Date.now();
  const slot = kind === 'thought' ? 'lastThought' : 'lastSpeech';
  if (rec[slot] && rec[slot].at > at) return;
  rec[slot] = { text: full, at: at };
}

// Re-show the most recent remembered bubble (speech or thought) as a
// dimmed "stale" bubble. Never clobbers a live bubble.
function _osRestoreBubbles(rec) {
  const s = rec.lastSpeech, th = rec.lastThought;
  const kind = (s && th) ? (s.at >= th.at ? 'speech' : 'thought')
    : (s ? 'speech' : (th ? 'thought' : ''));
  if (!kind) return;
  const data = kind === 'speech' ? s : th;
  const el = kind === 'speech' ? rec.speechEl : rec.thoughtEl;
  if (!el || el.style.display !== 'none') return;
  el.classList.add('osv-stale');
  _osSetBubbleText(rec, kind, _osFull(data.text));
  if (kind === 'speech') rec.speechAt = data.at; else rec.thoughtAt = data.at;
}

// Token streams arrive character by character; coalesce before touching
// the DOM so four streaming agents cost four updates per tick, not four
// hundred.
function _osStreamBubble(rec, kind, chunk) {
  const prop = kind === 'thought' ? 'thoughtText' : 'speechText';
  rec[prop] = (rec[prop] || '') + String(chunk || '');
  // Accumulate the whole turn; only a runaway tail-cap applies.
  if (rec[prop].length > OSV_BUBBLE_MAX_CHARS * 2) {
    rec[prop] = rec[prop].slice(-OSV_BUBBLE_MAX_CHARS);
  }
  if (rec.speechFlushTimer) return;
  rec.speechFlushTimer = setTimeout(() => {
    rec.speechFlushTimer = 0;
    if (rec.speechText) _osShowBubble(rec, 'speech', rec.speechText);
    if (rec.thoughtText) _osShowBubble(rec, 'thought', rec.thoughtText);
  }, OSV_BUBBLE_COALESCE_MS);
}

// Flush a coalesced stream still waiting for its 250ms timer. Resets
// (done, turn_complete, final message) must call this first — clearing
// the buffer with a flush pending froze the bubble one tick short, so
// thoughts ended mid-sentence.
function _osFlushBubbles(rec) {
  if (!rec) return;
  if (rec.speechFlushTimer) {
    clearTimeout(rec.speechFlushTimer);
    rec.speechFlushTimer = 0;
  }
  if (rec.speechText) _osShowBubble(rec, 'speech', rec.speechText);
  if (rec.thoughtText) _osShowBubble(rec, 'thought', rec.thoughtText);
}

function _osExpireBubbles(now) {
  _osAgents.forEach((rec) => {
    // The last bubble never disappears: the scene always shows each
    // participant's most recent message or thought. Linger only dims it
    // (osv-stale) and hides the OLDER of the two kinds when both show.
    const speechShown = rec.speechEl && rec.speechEl.style.display !== 'none';
    const thoughtShown = rec.thoughtEl && rec.thoughtEl.style.display !== 'none';
    if (speechShown && now - rec.speechAt > OSV_BUBBLE_LINGER_MS) {
      // One-time reset at expiry: it must never run again once stale, or
      // it would keep wiping the buffer of the NEXT incoming stream every
      // frame (the bug that froze bubbles after their first turn).
      if (!rec.speechEl.classList.contains('osv-stale')) {
        rec.speechText = '';
        rec.speechEl.classList.add('osv-stale');
      }
      if (thoughtShown && rec.thoughtAt > rec.speechAt) {
        rec.speechEl.style.display = 'none';
      }
    }
    if (thoughtShown && now - rec.thoughtAt > OSV_BUBBLE_LINGER_MS) {
      if (!rec.thoughtEl.classList.contains('osv-stale')) {
        rec.thoughtText = '';
        rec.thoughtEl.classList.add('osv-stale');
      }
      if (speechShown && rec.speechAt >= rec.thoughtAt) {
        rec.thoughtEl.style.display = 'none';
      }
    }
    // Agents whose turn ended drift back to idle without an explicit
    // done event for them (delegates, providers that only emit done for
    // the primary).
    if (rec.state !== 'idle' && rec.state !== 'waiting'
        && now - rec.stateSince > OSV_BUBBLE_LINGER_MS + OSV_IDLE_AFTER_MS) {
      _osSetState(rec, 'idle');
    }
  });
}

// ── Activity log (feeds the PC dialog) ───────────────────────────
function _osLog(rec, kind, title, body) {
  if (!rec) return;
  rec.log.push({ ts: Date.now(), kind: kind, title: _osText(title),
                 body: _osText(body) });
  if (rec.log.length > OSV_LOG_MAX) rec.log.splice(0, rec.log.length - OSV_LOG_MAX);
}

// ── History seeding ──────────────────────────────────────────────
// Called by _renderHistory after a full load: the openspace shows the
// last message/thought per participant even before any live event, and
// user avatars exist for every author already in the transcript.
function openspaceSeedHistory(messages, cid) {
  if (cid && cid !== _osSeedConvId) {
    _osSeedConvId = cid;
    openspaceResetTransient();
    // New conversation → new room palette (deterministic per id).
    _osApplyRoomStyle();
  }
  (messages || []).forEach((m) => {
    if (!m) return;
    const msgId = m.msg_id || '';
    if (msgId) {
      if (_osSeededIds.has(msgId)) return;
      _osSeededIds.add(msgId);
    }
    const role = m.type || m.role;
    const src = m.source || {};
    if (role === 'user') {
      const author = (src.type === 'user' && src.name) ? src.name
        : ((typeof window !== 'undefined' && window._userId) || 'user');
      const rec = _osEnsureUser(author);
      if (rec && m.content) {
        _osLog(rec, 'message', t('osvSaid'), m.content);
        _osRememberBubble(rec, 'speech', m.content, m.timestamp);
      }
    } else if (role === 'assistant') {
      const name = src.name
        || (typeof selectedAgent !== 'undefined' && selectedAgent) || '';
      if (!name) return;
      const rec = _osEnsureAgent(name);
      if (!rec) return;
      const content = _osText(m.content).replace(/^\[[^\]]+\]:\s*/, '');
      if (content) {
        _osLog(rec, 'message', t('osvSaid'), content);
        _osRememberBubble(rec, 'speech', content, m.timestamp);
      } else if (m.thinking) {
        _osRememberBubble(rec, 'thought', m.thinking, m.timestamp);
      }
    }
  });
  _osAgents.forEach((rec) => { _osRestoreBubbles(rec); });
}

// Conversation switch = room switch: every avatar, desk and overlay in
// the scene belongs to the previous conversation, so the office is
// emptied and the seed repopulates it with the new participants. Only
// the viewer's own avatar is recreated immediately.
function openspaceResetTransient() {
  // FileStore files are conversation-scoped: a room switch turns the TV off.
  openspaceTvStop();
  _osSeededIds.clear();
  Array.from(_osAgents.values()).forEach((rec) => _osRetireAgent(rec));
  _osAgents.clear();
  _osSeatCount = 0;
  _osUserCount = 0;
  _osFreeSeats.length = 0;
  _osUpdateCamera();
  if (_osScene && _osThree) {
    _osEnsureUser((typeof window !== 'undefined' && window._userId) || 'user');
  }
}

// Local echo from the composer. The sender's own message never comes
// back on the SSE stream, so send() reports it here directly; with
// attachments the avatar walks over and drops folders on the target
// agent's desk before returning to its spot.
function openspaceUserMessage(text, attachments, targetAgent, msgId) {
  const author = (typeof window !== 'undefined' && window._userId) || 'user';
  const rec = _osEnsureUser(author);
  if (!rec) return;
  if (msgId) _osSeededIds.add(msgId);
  if (text) {
    _osLog(rec, 'message', t('osvSaid'), text);
    _osShowBubble(rec, 'speech', text);
  }
  const names = (attachments || [])
    .map((a) => String((a && a.filename) || '').trim()).filter(Boolean);
  if (!names.length) return;
  names.forEach((name) => _osLog(rec, 'message', '\u{1F4C1} ' + name, ''));
  const dst = targetAgent ? _osEnsureAgent(targetAgent) : null;
  if (!dst || !_osActive || !rec.avatar) return;
  const home = { x: rec.homeSeat.x, z: rec.homeSeat.z };
  _osWalkTo(rec, { x: dst.seat.x + 1.9, z: dst.seat.z + 1.3 }, () => {
    names.forEach((name) => _osDropTool(dst, name, '\u{1F4C1}'));
    _osWalkTo(rec, home);
  });
}

// ── Tool drops ───────────────────────────────────────────────────
// Every tool_call drops a tool onto the desk: instantly readable "the
// agent is working on something", and the emoji says roughly what.
function _osToolEmoji(name) {
  const s = String(name || '');
  for (const pair of OSV_TOOL_EMOJI) { if (pair[0].test(s)) return pair[1]; }
  return '\u{1F527}';
}

function _osToolSprite(emoji) {
  const T = _osThree;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext('2d');
  ctx.font = '52px serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(emoji, 32, 36);
  const texture = new T.CanvasTexture(canvas);
  const sprite = new T.Sprite(
    new T.SpriteMaterial({ map: texture, transparent: true }));
  sprite.scale.set(0.9, 0.9, 1);
  return sprite;
}

function _osDropTool(rec, toolName, emoji) {
  if (!rec || !_osScene || !_osThree || rec.kind === 'user') return;
  rec.tools = rec.tools || [];
  while (rec.tools.length >= OSV_TOOL_MAX) _osRemoveTool(rec, rec.tools[0]);
  const sprite = _osToolSprite(emoji || _osToolEmoji(toolName));
  const slot = rec.tools.length;
  const restY = 1.45;
  sprite.position.set(
    rec.seat.x + (slot - (OSV_TOOL_MAX - 1) / 2) * 0.7,
    restY + 3, rec.seat.z + 0.45);
  _osScene.add(sprite);
  rec.tools.push({ name: String(toolName || ''), sprite: sprite,
                   phase: 'drop', start: performance.now(),
                   restY: restY, fadeStart: 0 });
}

function _osFadeTool(rec, toolName) {
  if (!rec || !rec.tools || !rec.tools.length) return;
  const name = String(toolName || '');
  let entry = name
    ? rec.tools.find((e) => e.phase !== 'fade' && e.name === name) : null;
  if (!entry) entry = rec.tools.find((e) => e.phase !== 'fade');
  if (!entry) return;
  entry.phase = 'fade';
  entry.fadeStart = performance.now();
}

function _osRemoveTool(rec, entry) {
  const i = rec.tools.indexOf(entry);
  if (i >= 0) rec.tools.splice(i, 1);
  if (entry.sprite) {
    if (_osScene) _osScene.remove(entry.sprite);
    if (entry.sprite.material) {
      if (entry.sprite.material.map) entry.sprite.material.map.dispose();
      entry.sprite.material.dispose();
    }
  }
}

function _osTickTools(ts) {
  _osAgents.forEach((rec) => {
    if (!rec.tools || !rec.tools.length) return;
    rec.tools.slice().forEach((entry) => {
      const sp = entry.sprite;
      if (!sp) { _osRemoveTool(rec, entry); return; }
      if (entry.phase === 'drop') {
        const p = Math.min(1, (ts - entry.start) / OSV_TOOL_DROP_MS);
        const fall = p < 0.7 ? (p / 0.7) * (p / 0.7) : 1;
        const hop = p > 0.7
          ? Math.sin((p - 0.7) / 0.3 * Math.PI) * 0.25 * (1 - p) : 0;
        sp.position.y = entry.restY + (1 - fall) * 3 + hop;
        if (p >= 1) { sp.position.y = entry.restY; entry.phase = 'rest'; }
      } else if (entry.phase === 'fade') {
        const q = Math.min(1, (ts - entry.fadeStart) / OSV_TOOL_FADE_MS);
        sp.material.opacity = 1 - q;
        sp.position.y = entry.restY + q * 0.4;
        if (q >= 1) _osRemoveTool(rec, entry);
      }
    });
  });
}

// ── State orbiters ───────────────────────────────────────────────
// The floor ring around each agent doubles as a status carousel:
// brains orbit (pulsing in/out) while the agent thinks, tools spin
// around it while one runs, and Zzz drift around an idle agent.
// Talking/waiting keep the ring empty — the bubbles and the ❓ status
// already carry those.
const OSV_ORBIT_EMOJI = {
  thinking: ['\u{1F9E0}', '\u{1F9E0}', '\u{1F9E0}'],
  tool: ['\u{1F527}', '\u{1F6E0}\uFE0F', '\u2699\uFE0F'],
  idle: ['\u{1F4A4}', '\u{1F4A4}', '\u{1F4A4}'],
};
const OSV_ORBIT_RADIUS = 1.05;   // rides the halo ring (0.9–1.15)
const OSV_ORBIT_PERIOD_MS = { thinking: 2600, tool: 1800, idle: 5200 };

function _osClearOrbit(rec) {
  if (!rec || !rec.orbit) return;
  const group = rec.orbit.group;
  rec.orbit = null;
  if (!group) return;
  if (group.parent) group.parent.remove(group);
  group.children.slice().forEach((sp) => {
    if (sp.material) {
      if (sp.material.map) sp.material.map.dispose();
      sp.material.dispose();
    }
  });
}

function _osEnsureOrbit(rec, kind) {
  if (rec.orbit && rec.orbit.kind === kind) return;
  _osClearOrbit(rec);
  if (!kind || !rec.avatar || !_osThree) return;
  const T = _osThree;
  const group = new T.Group();
  const sprites = OSV_ORBIT_EMOJI[kind].map((emoji) => {
    const sp = _osToolSprite(emoji);
    sp.scale.set(0.55, 0.55, 1);
    group.add(sp);
    return sp;
  });
  rec.avatar.add(group);
  rec.orbit = { kind: kind, group: group, sprites: sprites };
}

function _osTickOrbits(ts) {
  _osAgents.forEach((rec) => {
    if (rec.kind === 'user') return;
    _osEnsureOrbit(rec, OSV_ORBIT_EMOJI[rec.state] ? rec.state : '');
    if (!rec.orbit) return;
    const kind = rec.orbit.kind;
    const n = rec.orbit.sprites.length;
    const angle = (ts / OSV_ORBIT_PERIOD_MS[kind]) * Math.PI * 2;
    rec.orbit.sprites.forEach((sp, i) => {
      const a = angle + (i / n) * Math.PI * 2;
      sp.position.set(Math.cos(a) * OSV_ORBIT_RADIUS, 0.55,
                      Math.sin(a) * OSV_ORBIT_RADIUS);
      if (kind === 'thinking') {
        // Brains zoom in and out as they circle.
        const z = 0.4 + 0.3 * (1 + Math.sin(ts / 320 + i * 2.1)) / 2;
        sp.scale.set(z, z, 1);
      } else if (kind === 'tool') {
        // Tools spin on themselves while revolving.
        sp.material.rotation = ts / 350 + i;
      } else {
        // Zzz float gently up and down, slightly tilted.
        sp.position.y = 0.7 + 0.2 * Math.sin(ts / 900 + i * 2);
        sp.material.rotation = 0.25 * Math.sin(ts / 800 + i);
      }
    });
  });
}

// ── Delegation walk ──────────────────────────────────────────────
function _osWalkTo(rec, target, onDone) {
  if (!rec.avatar || !target) { if (onDone) onDone(); return; }
  _osTweens = _osTweens.filter((tw) => tw.rec !== rec);
  _osTweens.push({
    rec: rec,
    from: { x: rec.avatar.position.x, z: rec.avatar.position.z },
    to: { x: target.x, z: target.z },
    start: performance.now(), dur: OSV_WALK_MS, onDone: onDone || null,
  });
}

function _osDelegateStart(sourceName, delegateName) {
  const src = _osEnsureAgent(sourceName);
  const dst = _osEnsureAgent(delegateName, { guest: true });
  if (!src || !dst) return;
  _osLog(src, 'delegate', t('osvDelegatesTo') + ' ' + dst.name, '');
  _osShowBubble(src, 'speech', t('osvDelegatesTo') + ' ' + dst.name);
  _osSetState(dst, 'thinking');
  if (!src.avatar || !dst.seat) return;
  src.awayAt = dst.key;
  // Walk to the delegate's desk, hand the task over, then walk home —
  // the delegating agent does not camp at the desk while the delegate
  // works.
  _osWalkTo(src, { x: dst.seat.x - 1.8, z: dst.seat.z + 1.35 }, () => {
    setTimeout(() => {
      if (_osAgents.get(src.key) !== src || src.awayAt !== dst.key) return;
      src.awayAt = null;
      _osWalkTo(src, { x: src.homeSeat.x, z: src.homeSeat.z + 1.35 });
    }, 900);
  });
}

function _osDelegateDone(sourceName) {
  const src = _osAgents.get(_osKey(sourceName));
  if (!src || !src.awayAt) return;
  src.awayAt = null;
  _osWalkTo(src, { x: src.homeSeat.x, z: src.homeSeat.z + 1.35 });
}

// ── SSE wiring (called by connectSSE after the EventSource exists) ──
function openspaceWireSSE(es) {
  if (!es) return;
  const on = (type, fn) => es.addEventListener(type, (e) => {
    let data = {};
    try { data = e.data ? JSON.parse(e.data) : {}; } catch (_) { return; }
    fn(data);
  });

  on('token', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    if (rec.state !== 'talking') _osSetState(rec, 'talking');
    if (_osActive) _osStreamBubble(rec, 'speech', d.content || d.token || '');
  });
  on('thinking', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    _osSetState(rec, 'thinking');
  });
  on('thinking_delta', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    if (rec.state !== 'thinking') _osSetState(rec, 'thinking');
    // Thinking events carry the text in `text` (see renderThinkingContent).
    if (_osActive) {
      const chunk = String(d.text || d.content || '');
      // Track the current block's preview region inside thoughtText so
      // the durable thinking_content can splice over it (the preview is
      // truncated by design: the emitter never flushes its final chunk).
      const before = (rec.thoughtText || '').length;
      if (!(rec._tbStart >= 0)) rec._tbStart = before;
      _osStreamBubble(rec, 'thought', chunk);
      if (rec.thoughtText.length !== before + chunk.length) {
        rec._tbStart = -1;  // runaway cap shifted the text: give up splicing
      } else {
        rec._tbEnd = rec.thoughtText.length;
      }
    }
  });
  on('thinking_content', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    const full = String(d.text || d.content || '');
    _osLog(rec, 'thought', t('osvThought'), full);
    if (!_osActive || !full) return;
    // Durable text supersedes this block's truncated preview: splice it
    // over the tracked region (tool emojis appended after it survive).
    const txt = rec.thoughtText || '';
    let start = rec._tbStart, end = rec._tbEnd;
    if (!(start >= 0) || start > txt.length) start = -1;
    if (start >= 0 && (!(end >= start) || end > txt.length)) end = txt.length;
    rec.thoughtText = start >= 0
      ? txt.slice(0, start) + full + txt.slice(end)
      : (txt ? txt + (txt.endsWith('\n') ? '' : '\n') + full : full);
    rec._tbStart = -1; rec._tbEnd = -1;
    _osShowBubble(rec, 'thought', rec.thoughtText);
  });
  on('tool_call', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    let args = '';
    try { args = JSON.stringify(d.arguments || {}); } catch (_) { args = ''; }
    _osLog(rec, 'tool', d.tool || 'tool', args);
    _osSetState(rec, 'tool', d.tool || '');
    if (_osActive) {
      _osDropTool(rec, d.tool || 'tool');
      // The falling prop says "working"; the thought stream logs on what,
      // inline between the accumulated thinking passages.
      _osStreamBubble(rec, 'thought',
        '\n' + _osToolEmoji(d.tool) + ' ' + (d.tool || 'tool') + '\n');
      // Cross-conversation work goes through the door: a2a calls send
      // the agent over to it before coming back to their desk.
      if (/a2a/i.test(d.tool || '')) {
        const a = d.arguments || {};
        const target = a.agent || a.agent_name || a.target || '';
        _osDoorTrip(rec, t('osvDelegatesTo') + ' ' + (target || 'a2a'));
      }
    }
  });
  on('tool_result', (d) => {
    const rec = _osAgents.get(_osKey(_osEventAgent(d)));
    if (!rec) return;
    const body = typeof d.result === 'string' ? d.result : '';
    _osLog(rec, 'tool_result', (d.tool || 'tool') + ' \u2713', body);
    _osFadeTool(rec, d.tool || '');
    _osSetState(rec, 'thinking');
  });
  on('new_message', (d) => {
    if (!d || !d.content) return;
    // Already shown: locally echoed by openspaceUserMessage or seeded.
    if (d.msg_id && _osSeededIds.has(d.msg_id)) return;
    if (d.role === 'assistant') {
      const rec = _osEnsureAgent(_osEventAgent(d));
      if (!rec) return;
      if (d.msg_id) _osSeededIds.add(d.msg_id);
      _osLog(rec, 'message', t('osvSaid'), d.content);
      if (_osActive) {
        _osFlushBubbles(rec);
        _osShowBubble(rec, 'speech', d.content);
        rec.speechText = '';
        rec.thoughtText = '';   // the answer closes the accumulated thought
        rec._tbStart = -1; rec._tbEnd = -1;
      }
    } else if (d.role === 'user') {
      const src = d.source || {};
      const author = (src.type === 'user' && src.name) ? src.name
        : ((typeof window !== 'undefined' && window._userId) || 'user');
      const rec = _osEnsureUser(author);
      if (rec) {
        if (d.msg_id) _osSeededIds.add(d.msg_id);
        _osLog(rec, 'message', t('osvSaid'), d.content);
        if (_osActive) _osShowBubble(rec, 'speech', d.content);
      }
      // The user speaking demotes everyone else's bubbles sooner.
      _osAgents.forEach((r) => {
        if (r !== rec) r.speechAt -= OSV_BUBBLE_LINGER_MS / 2;
      });
    }
  });
  on('ask_user', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    _osLog(rec, 'ask', t('osvAsksYou'), d.question || d.content || '');
    _osSetState(rec, 'waiting');
  });
  on('tool_approval_request', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (rec) _osSetState(rec, 'waiting', d.tool || '');
  });
  on('done', (d) => {
    const rec = _osAgents.get(_osKey(_osEventAgent(d)));
    if (rec) {
      _osFlushBubbles(rec);
      rec.thoughtText = '';   // turn over → next turn accumulates fresh
      _osSetState(rec, 'idle');
      _osDelegateDone(rec.name);
    }
  });
  on('turn_complete', (d) => {
    const rec = _osAgents.get(_osKey(_osEventAgent(d)));
    if (rec) { _osFlushBubbles(rec); rec.thoughtText = ''; _osSetState(rec, 'idle'); }
  });
  on('sub_agent_start', (d) => {
    if (d.source_agent && d.agent_name) _osDelegateStart(d.source_agent, d.agent_name);
    else if (d.agent_name) _osEnsureAgent(d.agent_name, { guest: true });
  });
  on('sub_agent_text', (d) => {
    const rec = _osEnsureAgent(d.agent_name, { guest: true });
    if (!rec) return;
    if (rec.state !== 'talking') _osSetState(rec, 'talking');
    if (_osActive) _osStreamBubble(rec, 'speech', d.content || '');
  });
  on('sub_agent_thinking', (d) => {
    const rec = _osEnsureAgent(d.agent_name, { guest: true });
    if (!rec) return;
    if (rec.state !== 'thinking') _osSetState(rec, 'thinking');
    // Delegate thinking arrives in `thinking` (see sse_handlers_a.js).
    if (_osActive) _osStreamBubble(rec, 'thought', d.thinking || '');
  });
  on('sub_agent_tool', (d) => {
    const rec = _osEnsureAgent(d.agent_name, { guest: true });
    if (!rec) return;
    _osLog(rec, 'tool', d.tool || 'tool', '');
    _osSetState(rec, 'tool', d.tool || '');
    if (_osActive) {
      _osDropTool(rec, d.tool || 'tool');
      _osStreamBubble(rec, 'thought',
        '\n' + _osToolEmoji(d.tool) + ' ' + (d.tool || 'tool') + '\n');
    }
  });
  on('sub_agent_done', (d) => {
    const rec = _osAgents.get(_osKey(d.agent_name));
    if (rec) {
      _osLog(rec, 'message', t('osvDone'), d.content || '');
      _osFlushBubbles(rec);
      rec.thoughtText = '';
      _osSetState(rec, 'idle');
      // Flash/out-of-roster guests pack up once their run ends; the
      // bubble gets its linger first, then the desk is dismantled.
      if (rec.guest) {
        setTimeout(() => {
          const cur = _osAgents.get(rec.key);
          if (cur === rec && cur.guest && cur.state === 'idle') _osRetireAgent(cur);
        }, OSV_BUBBLE_LINGER_MS + 800);
      }
    }
    if (d.source_agent) _osDelegateDone(d.source_agent);
  });
}

// ── Render loop ──────────────────────────────────────────────────
function _osStartLoop() {
  if (_osRaf || !_osRenderer) return;
  const step = (ts) => {
    _osRaf = 0;
    if (!_osActive || document.hidden) return;
    _osClock = ts;
    _osTick(ts);
    _osRenderer.render(_osScene, _osCamera);
    _osRaf = requestAnimationFrame(step);
  };
  _osRaf = requestAnimationFrame(step);
}

function _osStopLoop() {
  if (_osRaf) { cancelAnimationFrame(_osRaf); _osRaf = 0; }
}

function _osTick(ts) {
  const now = Date.now();
  // Tweens (walking). Finished tweens are removed BEFORE their onDone
  // runs: onDone often chains a new walk (delegate return, attachment
  // drop-off), and reassigning _osTweens after the callbacks would
  // silently discard what they pushed.
  const finished = [];
  _osTweens = _osTweens.filter((tw) => {
    const p = Math.min(1, (ts - tw.start) / tw.dur);
    const ease = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
    const av = tw.rec.avatar;
    if (av) {
      av.position.x = tw.from.x + (tw.to.x - tw.from.x) * ease;
      av.position.z = tw.from.z + (tw.to.z - tw.from.z) * ease;
      av.position.y = Math.abs(Math.sin(p * Math.PI * 6)) * 0.12;
    }
    if (p >= 1) { if (av) av.position.y = 0; finished.push(tw); return false; }
    return true;
  });
  finished.forEach((tw) => { if (tw.onDone) tw.onDone(); });
  _osFollowUser();
  // Per-agent idle animation + halo + overlay projection.
  const selKey = _osKey(typeof selectedAgent !== 'undefined' ? selectedAgent : '');
  const tweening = new Set(_osTweens.map((tw) => tw.rec));
  _osAgents.forEach((rec) => {
    if (!rec.avatar) { if (_osScene && _osThree && !rec.group) _osBuildDesk(rec); return; }
    if (rec.halo) {
      rec.halo.visible = rec.key === selKey;
      if (rec.halo.visible) rec.halo.rotation.z = ts / 900;
    }
    // The capsule avatar is rotationally symmetric: spinning it
    // (rotation.y) shows nothing. Lean (rotation.z) and bounce
    // (position.y) are the visible axes, and each state gets its own
    // rhythm so a glance says who is doing what.
    const sway = { thinking: [350, 0.18], talking: [220, 0.1],
                   tool: [90, 0.06] }[rec.state];
    rec.avatar.rotation.z = sway ? Math.sin(ts / sway[0]) * sway[1] : 0;
    if (!tweening.has(rec)) {
      const bounce = { tool: [130, 0.22], talking: [200, 0.12],
                       thinking: [480, 0.08] }[rec.state];
      rec.avatar.position.y = bounce
        ? Math.abs(Math.sin(ts / bounce[0])) * bounce[1] : 0;
    }
    // The PC screen flickers while its agent works.
    if (rec.screenMat) {
      rec.screenMat.emissiveIntensity = sway
        ? 0.75 + Math.sin(ts / 160) * 0.35 : 1;
    }
    _osProject(rec);
  });
  _osTickTools(ts);
  _osTickOrbits(ts);
  _osTickFlow(ts);
  _osExpireBubbles(now);
  _osRefreshBatteries(now);
  _osUpdateBoard(now);
  _osProjectScreen();
}

// Project the avatar's head to screen space and pin the DOM elements.
const _osProjVec = { v: null };
function _osProject(rec) {
  if (!_osOverlay || !_osCamera || !rec.avatar) return;
  const T = _osThree;
  if (!_osProjVec.v) _osProjVec.v = new T.Vector3();
  const v = _osProjVec.v;
  // Anchor just above the head: chibi mascots top out around 2.0, the
  // standing visitor at about 2.3.
  v.set(rec.avatar.position.x, rec.kind === 'user' ? 2.5 : 2.2,
        rec.avatar.position.z);
  v.project(_osCamera);
  const w = _osOverlay.clientWidth, h = _osOverlay.clientHeight;
  const x = (v.x * 0.5 + 0.5) * w;
  const y = (-v.y * 0.5 + 0.5) * h;
  const off = v.z > 1 ? -10000 : 0; // behind the camera → park off-screen
  if (rec.labelEl) {
    rec.labelEl.style.transform =
      'translate(' + (x + off) + 'px,' + y + 'px) translate(-50%, 0)';
  }
  if (rec.speechEl && rec.speechEl.style.display !== 'none') {
    rec.speechEl.style.transform =
      'translate(' + (x + off) + 'px,' + (y - 34) + 'px) translate(-50%, -100%)';
  }
  if (rec.thoughtEl && rec.thoughtEl.style.display !== 'none') {
    const lift = rec.speechEl && rec.speechEl.style.display !== 'none'
      ? rec.speechEl.offsetHeight + 42 : 34;
    rec.thoughtEl.style.transform =
      'translate(' + (x + off) + 'px,' + (y - lift) + 'px) translate(-50%, -100%)';
  }
  if (rec.battEl && rec.battEl.style.display !== 'none') {
    rec.battEl.style.transform =
      'translate(' + (x + off) + 'px,' + (y - 12) + 'px) translate(-50%, -100%)';
  }
  if (rec.statusEl && rec.statusEl.style.display !== 'none') {
    rec.statusEl.style.transform =
      'translate(' + (x + off) + 'px,' + (y + 26) + 'px) translate(-50%, 0)';
  }
}

// ── Pointer: orbit drag, wheel zoom, click → PC dialog ──────────
function _osPointerDown(e) {
  _osTouches.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (_osTouches.size === 2) {
    // Second finger: switch to pinch (zoom) + two-finger pan; the
    // single-finger orbit drag in progress is cancelled.
    _osDrag = null;
    _osPinchPrev = _osPinchState();
    return;
  }
  if (_osTouches.size > 2) return;
  _osDrag = { x: e.clientX, y: e.clientY, moved: false,
              lift: e.ctrlKey,
              pan: !e.ctrlKey && (e.button === 2 || e.shiftKey) };
  try { _osCanvas.setPointerCapture(e.pointerId); } catch (_) {}
}

function _osPinchState() {
  const pts = Array.from(_osTouches.values());
  const dx = pts[0].x - pts[1].x, dy = pts[0].y - pts[1].y;
  return { dist: Math.max(1, Math.hypot(dx, dy)),
           cx: (pts[0].x + pts[1].x) / 2, cy: (pts[0].y + pts[1].y) / 2 };
}

function _osPointerMove(e) {
  if (_osTouches.has(e.pointerId)) {
    _osTouches.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (_osTouches.size === 2 && _osPinchPrev) {
      const s = _osPinchState();
      _osFollow = false;
      _osCamDist = Math.max(10, Math.min(60, _osCamDist * _osPinchPrev.dist / s.dist));
      const k = _osCamDist * 0.0016;
      const a = _osCamAngle;
      const dx = s.cx - _osPinchPrev.cx, dy = s.cy - _osPinchPrev.cy;
      _osCamPan.x += (-Math.sin(a) * dx + Math.cos(a) * dy) * k;
      _osCamPan.z += (Math.cos(a) * dx + Math.sin(a) * dy) * k;
      _osCamPan.x = Math.max(-40, Math.min(40, _osCamPan.x));
      _osCamPan.z = Math.max(-40, Math.min(40, _osCamPan.z));
      _osPinchPrev = s;
      _osUpdateCamera();
      return;
    }
  }
  if (!_osDrag) return;
  const dx = e.clientX - _osDrag.x, dy = e.clientY - _osDrag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) _osDrag.moved = true;
  if (_osDrag.moved) {
    if (_osDrag.lift) {
      // Vertical drag raises/lowers the camera target above the floor.
      _osCamPan.y = Math.max(0, Math.min(18, _osCamPan.y - dy * 0.04));
    } else if (_osDrag.pan) {
      // Drag the world: translate the camera target on the floor plane.
      _osFollow = false;
      const k = _osCamDist * 0.0016;
      const a = _osCamAngle;
      _osCamPan.x += (-Math.sin(a) * dx + Math.cos(a) * dy) * k;
      _osCamPan.z += (Math.cos(a) * dx + Math.sin(a) * dy) * k;
      _osCamPan.x = Math.max(-40, Math.min(40, _osCamPan.x));
      _osCamPan.z = Math.max(-40, Math.min(40, _osCamPan.z));
    } else {
      _osCamAngle += dx * 0.008;
      _osCamHeight = Math.max(6, Math.min(40, _osCamHeight + dy * 0.05));
    }
    _osDrag.x = e.clientX; _osDrag.y = e.clientY;
    _osUpdateCamera();
  }
}

function _osPointerUp(e) {
  _osTouches.delete(e.pointerId);
  if (_osTouches.size < 2) _osPinchPrev = null;
  const wasDrag = _osDrag && _osDrag.moved;
  _osDrag = null;
  if (wasDrag || e.button !== 0 || !_osRaycaster || !_osCamera) return;
  const bounds = _osCanvas.getBoundingClientRect();
  const T = _osThree;
  const ndc = new T.Vector2(
    ((e.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((e.clientY - bounds.top) / bounds.height) * 2 + 1);
  _osRaycaster.setFromCamera(ndc, _osCamera);
  const hits = _osRaycaster.intersectObjects(_osScene.children, true);
  for (const hit of hits) {
    const ud = hit.object && hit.object.userData;
    if (ud && ud.osvFlowClose) { openspaceCloseFlow(); return; }
    if (ud && ud.osvFlowUp) { _osFlowUp(); return; }
    if (ud && typeof ud.osvFlowNode === 'string') {
      _osFlowDrill(ud.osvFlowNode);
      return;
    }
    if (ud && ud.osvDoor) { openspaceOpenConvDialog(); return; }
    if (ud && ud.osvTv) { openspaceOpenTvDialog(); return; }
    if (ud && ud.osvResSection) {
      openspaceOpenResSectionDialog(ud.osvResSection, ud.osvResTitle);
      return;
    }
    if (ud && ud.osvPoster) { _osOpenPoster(ud.osvPoster); return; }
    if (ud && ud.osvAgent) { openspaceOpenAgentDialog(ud.osvAgent); return; }
  }
  // No agent under the cursor: clicking the floor walks YOUR avatar
  // there, and the spot becomes its new home (delivery trips return to
  // it).
  const floor = _osScene.getObjectByName('floor');
  if (!floor) return;
  const ground = _osRaycaster.intersectObject(floor, false)[0];
  if (!ground) return;
  const me = _osEnsureUser(
    (typeof window !== 'undefined' && window._userId) || 'user');
  if (!me || !me.avatar) return;
  const gx = Math.max(-35, Math.min(50, ground.point.x));
  const gz = Math.max(-35, Math.min(50, ground.point.z));
  me.homeSeat = { x: gx, z: gz };
  _osWalkTo(me, { x: gx, z: gz });
  _osFollow = true;
}

function _osWheel(e) {
  e.preventDefault();
  _osCamDist = Math.max(10, Math.min(60, _osCamDist + e.deltaY * 0.03));
  _osUpdateCamera();
}

// ── PC dialog: stacked detail blocks for one agent ──────────────
function openspaceOpenAgentDialog(key) {
  const rec = _osAgents.get(_osKey(key));
  if (!rec) return;
  const prior = document.getElementById('osvAgentDialog');
  if (prior) prior.remove();
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.id = 'osvAgentDialog';
  // No background-click dismissal: modal overlays close only through
  // their explicit close control (repo-wide convention, see
  // test_chat_ui_resources_static.py).

  const dialog = document.createElement('div');
  dialog.className = 'exec-dialog cog-dialog osv-dialog';
  const head = document.createElement('div');
  head.className = 'cog-head';
  const title = document.createElement('h3');
  title.textContent = rec.name + ' \u2014 ' + t('osvActivity');
  head.appendChild(title);
  const close = document.createElement('button');
  close.className = 'cog-close';
  close.innerHTML = '&times;';
  close.onclick = () => overlay.remove();
  dialog.append(close, head);

  const list = document.createElement('div');
  list.className = 'osv-log';
  if (!rec.log.length) {
    const empty = document.createElement('div');
    empty.className = 'osv-log-empty';
    empty.textContent = t('osvNoActivity');
    list.appendChild(empty);
  }
  // Newest first: the block you want is almost always the last thing
  // that happened.
  rec.log.slice().reverse().forEach((entry) => {
    const block = document.createElement('div');
    block.className = 'osv-block osv-block-' + entry.kind;
    const header = document.createElement('div');
    header.className = 'osv-block-head';
    const when = new Date(entry.ts);
    const hh = String(when.getHours()).padStart(2, '0');
    const mm = String(when.getMinutes()).padStart(2, '0');
    const ss = String(when.getSeconds()).padStart(2, '0');
    const icons = { message: '\u{1F4AC}', thought: '\u{1F4AD}',
                    tool: '\u2699\uFE0F', tool_result: '\u2705',
                    delegate: '\u{1F91D}', ask: '\u2753' };
    header.textContent = (icons[entry.kind] || '\u2022') + ' '
      + hh + ':' + mm + ':' + ss + ' \u2014 ' + entry.title;
    const body = document.createElement('div');
    body.className = 'osv-block-body';
    const full = entry.body || '';
    const preview = full.length > OSV_LOG_BLOCK_PREVIEW
      ? full.slice(0, OSV_LOG_BLOCK_PREVIEW) + '\u2026' : full;
    body.textContent = preview;
    if (full.length > OSV_LOG_BLOCK_PREVIEW) {
      block.classList.add('osv-expandable');
      block.addEventListener('click', () => {
        const expanded = block.classList.toggle('osv-expanded');
        body.textContent = expanded ? full : preview;
      });
    }
    block.append(header, body);
    list.appendChild(block);
  });
  dialog.appendChild(list);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}
