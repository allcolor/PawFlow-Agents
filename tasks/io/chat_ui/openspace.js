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
const OSV_USER_BUBBLE_FADE_MS = 10000;  // live user bubbles disappear after this
// Per-agent activity log for the PC dialog (bounded ring).
const OSV_LOG_MAX = 120;
const OSV_LOG_BLOCK_PREVIEW = 160;
// Walking keeps a stable world-space speed; short hops still remain readable.
const OSV_WALK_UNITS_PER_SEC = 3;
const OSV_WALK_MIN_MS = 500;
const OSV_WALK_MAX_MS = 2400;
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
let _osEnvironmentLoading = null; // hotpatch-safe optional module loader
let _osScene = null, _osCamera = null, _osRenderer = null;
let _osScreenOcclusionRenderer = null;
let _osRaf = 0;
const OSV_DPR_MIN = 0.75;
let _osDprMax = 1, _osPixelRatio = 1, _osSoftwareRenderer = false;
let _osFrameMs = 16.7, _osLastFrameTs = 0, _osQualityAt = 0;
let _osCanvas = null, _osOverlay = null, _osScreenOcclusionCanvas = null;
let _osClock = 0;
let _osRaycaster = null;
let _osTweens = [];           // {obj, from:{x,z}, to:{x,z}, start, dur, onDone}
let _osCamAngle = Math.PI / 4, _osCamDist = 36, _osCamHeight = 25;
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
const _osHistoryByConversation = new Map();
// Projection wall: a read-only projection of the canonical transcript is
// perspective-mapped onto a big screen in the scene. The real #messages stays
// in the Webchat surface so both surfaces can be visible in a tiled workspace.
const OSV_SCREEN_W = 960, OSV_SCREEN_H = 540;
const OSV_SCREEN_Z = -9;
const OSV_SCREEN_OCCLUSION_EPSILON = 0.01;
let _osScreenEl = null;
let _osScreenCorners = null;
let _osProjectedMessages = null;
let _osProjectionController = null;
let _osProjectionSource = null;
let _osScreenOcclusionRect = null;
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
let _osSurfaceFocus = '';
let _osWebchatTransitionRaf = 0;

function openspaceIsActive() { return _osActive; }

function _osKey(name) { return String(name || '').toLowerCase(); }

function _osEventAgent(data) {
  const source = (data && data.source) || {};
  // User-authored messages can carry source.name="user" before the real
  // identity is hydrated. That is an author label, never an agent identity:
  // accepting it here creates a phantom agent desk and attributes subsequent
  // tool activity to it. An explicit agent_name remains authoritative, so a
  // genuinely configured agent named "user" still works.
  const sourceAgent = source.type !== 'user' ? (source.name || '') : '';
  return (data && data.agent_name) || sourceAgent
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
function _osModulesReady() {
  // Conversation data can arrive while the ordered defer scripts are still
  // executing. DOMContentLoaded fires only after that whole list completed,
  // so runtime handlers from the later OpenSpace modules are then available.
  if (document.readyState !== 'loading') return Promise.resolve();
  return new Promise((resolve) => {
    document.addEventListener('DOMContentLoaded', resolve, { once: true });
  });
}

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
    _osModulesReady().then(() => _osEnsureThree())
      .then(() => _osEnsureEnvironment()).then(() => {
      if (!_osActive) return;
      _osBuildScene(wrap);
      // Entering OpenSpace is a fresh overview, never a restoration of the
      // close-up or manually moved camera left behind before Webchat.
      _osSetCameraView('home');
      _osSeedAgents();
      _osStartLoop();
    }).catch((e) => {
      console.error('openspace: initialization failed', e);
      const err = document.createElement('div');
      err.className = 'osv-error';
      err.textContent = t('osvLoadError');
      wrap.appendChild(err);
    });
  } else {
    if (_osWebchatTransitionRaf) cancelAnimationFrame(_osWebchatTransitionRaf);
    _osWebchatTransitionRaf = 0;
    wrap.classList.remove('osv-webchat-transition');
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

// Normal releases include openspace_environment.js in the ordered module
// list. A running server hotpatch cannot refresh that Python list without a
// restart, so load the same file once on demand when its global is absent.
function _osEnsureEnvironment() {
  if (typeof _osBuildEnvironment === 'function') return Promise.resolve();
  if (_osEnvironmentLoading) return _osEnvironmentLoading;
  _osEnvironmentLoading = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    const v = (typeof window !== 'undefined' && window.PAWFLOW_ASSET_VERSION) || Date.now();
    script.src = '/chat/js/openspace_environment.js?v=' + encodeURIComponent(v);
    script.onload = () => {
      if (typeof _osBuildEnvironment === 'function') resolve();
      else reject(new Error('openspace environment module did not initialize'));
    };
    script.onerror = () => reject(new Error('openspace environment module failed to load'));
    document.head.appendChild(script);
  });
  return _osEnvironmentLoading;
}

function _osUsesSoftwareWebGL() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return false;
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = ext
      ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    return /swiftshader|llvmpipe|softpipe|software/i.test(String(renderer || ''));
  } catch (_) {
    return false;
  }
}

// Ensure every configured conversation member has a desk, including idle,
// rate-limited or otherwise inactive agents that emitted no live event.
// list_resources may complete before or after the 3D view opens, so this
// function is deliberately safe to call from both paths.
function openspaceSyncAgents(agents) {
  (Array.isArray(agents) ? agents : []).forEach((agent) => {
    const name = typeof agent === 'string' ? agent : (agent && agent.name);
    if (name) _osEnsureAgent(name, { runtimeKind: agent && agent.runtime_kind });
  });
}

// Desks for agents already known before the view opened: all configured
// conversation members, the selected agent, and everything the live
// active-agents tracker has seen.
function _osSeedAgents() {
  if (typeof _lastResourcesData !== 'undefined' && _lastResourcesData) {
    openspaceSyncAgents(_lastResourcesData.agents);
  }
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
  _osScene.background = new T.Color(0x758a78);
  _osScene.fog = new T.Fog(0x758a78, 58, 110);

  _osCamera = new T.PerspectiveCamera(36, 1, 0.03, 250);
  _osUpdateCamera();

  _osSoftwareRenderer = _osUsesSoftwareWebGL();
  _osDprMax = _osSoftwareRenderer
    ? 1 : Math.min(window.devicePixelRatio || 1, 2);
  _osPixelRatio = _osDprMax;
  _osRenderer = new T.WebGLRenderer({ antialias: !_osSoftwareRenderer });
  _osRenderer.setPixelRatio(_osPixelRatio);
  _osRenderer.shadowMap.enabled = !_osSoftwareRenderer;
  _osRenderer.shadowMap.type = T.PCFSoftShadowMap;
  _osRenderer.toneMapping = T.ACESFilmicToneMapping;
  _osRenderer.toneMappingExposure = 1.08;
  _osRenderer.outputColorSpace = T.SRGBColorSpace;
  _osCanvas = _osRenderer.domElement;
  _osCanvas.className = 'osv-canvas';
  wrap.appendChild(_osCanvas);

  _osOverlay = document.getElementById('openspaceOverlay');
  if (_osOverlay) {
    // Projected panels are DOM, so the main WebGL depth buffer cannot place
    // foreground geometry (for example a ceiling light) in front of them.
    // A transparent second pass restores that occlusion inside the wall-screen
    // quad while keeping the transcript live and interactive.
    _osScreenOcclusionRenderer = new T.WebGLRenderer({
      antialias: !_osSoftwareRenderer, alpha: true,
    });
    _osScreenOcclusionRenderer.setPixelRatio(_osPixelRatio);
    _osScreenOcclusionRenderer.setClearColor(0x000000, 0);
    _osScreenOcclusionRenderer.toneMapping = T.ACESFilmicToneMapping;
    _osScreenOcclusionRenderer.toneMappingExposure = 1.08;
    _osScreenOcclusionRenderer.outputColorSpace = T.SRGBColorSpace;
    _osScreenOcclusionRenderer.clippingPlanes = [new T.Plane(
      new T.Vector3(0, 0, 1), -OSV_SCREEN_Z - OSV_SCREEN_OCCLUSION_EPSILON)];
    _osScreenOcclusionCanvas = _osScreenOcclusionRenderer.domElement;
    _osScreenOcclusionCanvas.className = 'osv-screen-occlusion';
    _osOverlay.appendChild(_osScreenOcclusionCanvas);
  }

  const ambient = new T.AmbientLight(0xffffff, 0.35);
  const sky = new T.HemisphereLight(0xdcecff, 0x66704f, 1.35);
  const sun = new T.DirectionalLight(0xfff2d4, 2.35);
  sun.position.set(18, 34, 22);
  sun.castShadow = !_osSoftwareRenderer;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.camera.left = -34;
  sun.shadow.camera.right = 34;
  sun.shadow.camera.top = 34;
  sun.shadow.camera.bottom = -34;
  sun.shadow.camera.near = 1;
  sun.shadow.camera.far = 90;
  sun.shadow.bias = -0.0005;
  _osScene.add(ambient, sky, sun);
  _osRoomMats = {};
  _osBuildEnvironment();

  _osRaycaster = new T.Raycaster();
  _osBuildBigScreen();
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
  if (!wrap.querySelector('.osv-camera-views')) {
    const views = document.createElement('div');
    views.className = 'osv-camera-views';
    [
      ['home', '\u2302', 'osvViewHome'],
      ['conversation', '\u{1F4AC}', 'osvViewConversation'],
      ['board', '\u{1F4CB}', 'osvViewBoard'],
      ['tv', '\u{1F4FA}', 'osvViewTv'],
      ['resources', '\u{1F9F0}', 'osvViewResources'],
    ].forEach(([kind, icon, key]) => {
      const button = document.createElement('button');
      button.textContent = icon;
      button.title = t(key);
      button.setAttribute('aria-label', t(key));
      button.onclick = () => _osSetCameraView(kind);
      views.appendChild(button);
    });
    wrap.appendChild(views);
  }
  if (!wrap.querySelector('.osv-webchat-btn')) {
    const webchat = document.createElement('button');
    webchat.className = 'osv-webchat-btn';
    webchat.textContent = '\u{1F4AC} ' + t('osvWebchat');
    webchat.title = t('osvWebchatTitle');
    webchat.setAttribute('aria-label', t('osvWebchatTitle'));
    webchat.onclick = openspaceZoomToWebchat;
    wrap.appendChild(webchat);
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
    [['\u25B2', () => { _osCamHeight = Math.min(60, _osCamHeight + 3); }],
     ['\u25BC', () => { _osCamHeight = Math.max(0, _osCamHeight - 3); }],
     ['\u25C0', () => pan(60, 0)],
     ['\u25B6', () => pan(-60, 0)],
     ['\u2795', () => { _osCamDist = Math.max(3, _osCamDist - 5); }],
     ['\u2796', () => { _osCamDist = Math.min(90, _osCamDist + 5); }],
     ['\u2302', () => _osSetCameraView('home')]].forEach(([txt, fn]) => {
      const b = document.createElement('button');
      b.textContent = txt;
      b.onclick = () => { fn(); _osUpdateCamera(); };
      ctl.appendChild(b);
    });
    wrap.appendChild(ctl);
  }
  _osResize();
  _osApplyRoomStyle(_osSeedConvId
    || (typeof conversationId !== 'undefined' && conversationId));
}

function _osSetCameraView(kind) {
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const rows = Math.max(1, Math.ceil(Math.max(_osSeatCount, 1) / OSV_GRID_COLS));
  const cz = ((rows - 1) * OSV_DESK_SPACING) / 2;
  const presets = {
    conversation: { x: 7, y: 1.8, z: -9, angle: Math.PI / 2, dist: 6.2 },
    board: { x: -6.5, y: 2.4, z: 2, angle: 0, dist: 6.0 },
    tv: { x: -6.3, y: 1.75, z: 8.5, angle: 0, dist: 5.0 },
    resources: { x: OSV_RESOURCE_WALL.faceX, y: 1.5, z: 5.2,
                 angle: Math.PI, dist: 6.0 },
  };
  const view = presets[kind];
  if (!view) {
    _osCamAngle = Math.PI / 4; _osCamDist = 36; _osCamHeight = 25;
    _osCamPan.x = 0; _osCamPan.y = 0; _osCamPan.z = 0;
    _osSurfaceFocus = ''; _osFollow = true;
  } else {
    _osCamPan.x = view.x - cx; _osCamPan.y = view.y; _osCamPan.z = view.z - cz;
    _osCamAngle = view.angle; _osCamDist = view.dist; _osCamHeight = 0.15;
    _osSurfaceFocus = kind; _osFollow = false;
  }
  _osUpdateCamera();
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

function openspaceZoomToWebchat() {
  if (!_osActive || !_osCamera || _osWebchatTransitionRaf) return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    if (typeof workspaceOpenWebchat === 'function') workspaceOpenWebchat();
    return;
  }
  const wrap = document.getElementById('openspaceWrap');
  const button = wrap && wrap.querySelector('.osv-webchat-btn');
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const rows = Math.max(1, Math.ceil(Math.max(_osSeatCount, 1) / OSV_GRID_COLS));
  const cz = ((rows - 1) * OSV_DESK_SPACING) / 2;
  const from = {
    angle: _osCamAngle, dist: _osCamDist, height: _osCamHeight,
    x: _osCamPan.x, y: _osCamPan.y, z: _osCamPan.z,
  };
  const to = {
    angle: Math.PI / 2, dist: 3.35, height: 0.08,
    x: 7 - cx, y: 1.8, z: OSV_SCREEN_Z - cz,
  };
  let angleDelta = to.angle - from.angle;
  while (angleDelta > Math.PI) angleDelta -= Math.PI * 2;
  while (angleDelta < -Math.PI) angleDelta += Math.PI * 2;
  const started = performance.now();
  const duration = 900;
  if (wrap) wrap.classList.add('osv-webchat-transition');
  if (button) button.disabled = true;
  const step = (now) => {
    if (!_osActive) { _osWebchatTransitionRaf = 0; return; }
    const progress = Math.min(1, (now - started) / duration);
    const eased = progress < 0.5
      ? 4 * progress * progress * progress
      : 1 - Math.pow(-2 * progress + 2, 3) / 2;
    _osCamAngle = from.angle + angleDelta * eased;
    _osCamDist = from.dist + (to.dist - from.dist) * eased;
    _osCamHeight = from.height + (to.height - from.height) * eased;
    _osCamPan.x = from.x + (to.x - from.x) * eased;
    _osCamPan.y = from.y + (to.y - from.y) * eased;
    _osCamPan.z = from.z + (to.z - from.z) * eased;
    _osSurfaceFocus = 'conversation';
    _osFollow = false;
    _osUpdateCamera();
    if (progress < 1) {
      _osWebchatTransitionRaf = requestAnimationFrame(step);
      return;
    }
    _osWebchatTransitionRaf = 0;
    if (typeof workspaceOpenWebchat === 'function') workspaceOpenWebchat();
    setTimeout(() => {
      if (wrap) wrap.classList.remove('osv-webchat-transition');
      if (button) button.disabled = false;
    }, 1200);
  };
  _osWebchatTransitionRaf = requestAnimationFrame(step);
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
    if (_osScreenOcclusionRenderer) _osScreenOcclusionRenderer.setSize(w2, h2);
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
