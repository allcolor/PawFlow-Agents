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
// Bubble text is a cue, not the record — the PC dialog holds the log.
const OSV_BUBBLE_MAX_CHARS = 200;
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
  if (on) {
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
  const grid = new T.GridHelper(120, 60, 0x2c3560, 0x232b52);
  grid.position.y = 0.01;
  _osScene.add(grid);

  _osRaycaster = new T.Raycaster();
  _osCanvas.addEventListener('pointerdown', _osPointerDown);
  _osCanvas.addEventListener('pointermove', _osPointerMove);
  _osCanvas.addEventListener('pointerup', _osPointerUp);
  _osCanvas.addEventListener('wheel', _osWheel, { passive: false });
  window.addEventListener('resize', _osResize);
  document.addEventListener('visibilitychange', _osVisibility);
  _osResize();
}

function _osUpdateCamera() {
  if (!_osCamera) return;
  const cx = ((OSV_GRID_COLS - 1) * OSV_DESK_SPACING) / 2;
  const rows = Math.max(1, Math.ceil(Math.max(_osSeatCount, 1) / OSV_GRID_COLS));
  const cz = ((rows - 1) * OSV_DESK_SPACING) / 2;
  _osCamera.position.set(
    cx + Math.cos(_osCamAngle) * _osCamDist,
    _osCamHeight,
    cz + Math.sin(_osCamAngle) * _osCamDist);
  _osCamera.lookAt(cx, 0, cz);
}

function _osResize() {
  const wrap = document.getElementById('openspaceWrap');
  if (!wrap || !_osRenderer || !_osCamera) return;
  const w = wrap.clientWidth || 1, h = wrap.clientHeight || 1;
  _osRenderer.setSize(w, h);
  _osCamera.aspect = w / h;
  _osCamera.updateProjectionMatrix();
}

function _osVisibility() {
  if (document.hidden) _osStopLoop();
  else if (_osActive) _osStartLoop();
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
  rec = {
    key: key,
    name: name,
    kind: 'agent',
    guest: !!(opts && opts.guest),
    state: 'idle',
    stateSince: Date.now(),
    seat: _osSeatPosition(_osSeatCount),
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
  _osSeatCount++;
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

  // Avatar: capsule tinted per agent, seated in front of the desk.
  const avatar = new T.Group();
  const bodyColor = new T.Color(rec.color);
  const body = new T.Mesh(
    new T.CapsuleGeometry(0.42, 0.7, 4, 12),
    new T.MeshLambertMaterial({ color: bodyColor }));
  body.position.y = 1.0;
  const head = new T.Mesh(
    new T.SphereGeometry(0.32, 16, 12),
    new T.MeshLambertMaterial({ color: 0xf2d0b0 }));
  head.position.y = 1.95;
  avatar.add(body, head);
  avatar.position.set(rec.seat.x, 0, rec.seat.z + 1.35);
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
  const thought = document.createElement('div');
  thought.className = 'osv-bubble osv-thought';
  thought.style.display = 'none';
  const status = document.createElement('div');
  status.className = 'osv-status';
  status.style.display = 'none';
  _osOverlay.append(label, speech, thought, status);
  rec.labelEl = label; rec.speechEl = speech;
  rec.thoughtEl = thought; rec.statusEl = status;
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
function _osTrim(text) {
  const s = String(text || '').replace(/\s+/g, ' ').trim();
  return s.length > OSV_BUBBLE_MAX_CHARS
    ? s.slice(0, OSV_BUBBLE_MAX_CHARS - 1) + '\u2026' : s;
}

function _osShowBubble(rec, kind, text) {
  const el = kind === 'thought' ? rec.thoughtEl : rec.speechEl;
  const trimmed = _osTrim(text);
  if (!trimmed) return;
  const stamp = Date.now();
  if (kind === 'thought') {
    rec.thoughtAt = stamp;
    rec.lastThought = { text: trimmed, at: stamp };
  } else {
    rec.speechAt = stamp;
    rec.lastSpeech = { text: trimmed, at: stamp };
  }
  if (!el) return;
  el.textContent = trimmed;
  el.classList.remove('osv-stale');
  el.style.display = '';
}

// Remember a bubble without touching the DOM (history seeding). Newest
// wins; timestamps arrive in seconds from stored messages.
function _osRememberBubble(rec, kind, text, ts) {
  const trimmed = _osTrim(text);
  if (!trimmed) return;
  const at = ts ? (ts > 1e12 ? ts : ts * 1000) : Date.now();
  const slot = kind === 'thought' ? 'lastThought' : 'lastSpeech';
  if (rec[slot] && rec[slot].at > at) return;
  rec[slot] = { text: trimmed, at: at };
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
  el.textContent = _osTrim(data.text);
  el.classList.add('osv-stale');
  el.style.display = '';
  if (kind === 'speech') rec.speechAt = data.at; else rec.thoughtAt = data.at;
}

// Token streams arrive character by character; coalesce before touching
// the DOM so four streaming agents cost four updates per tick, not four
// hundred.
function _osStreamBubble(rec, kind, chunk) {
  const prop = kind === 'thought' ? 'thoughtText' : 'speechText';
  rec[prop] = (rec[prop] || '') + String(chunk || '');
  // Keep only the tail: the bubble shows what is being said *now*.
  if (rec[prop].length > OSV_BUBBLE_MAX_CHARS * 2) {
    rec[prop] = rec[prop].slice(-OSV_BUBBLE_MAX_CHARS);
  }
  if (rec.speechFlushTimer) return;
  rec.speechFlushTimer = setTimeout(() => {
    rec.speechFlushTimer = 0;
    if (rec.speechText) _osShowBubble(rec, 'speech', rec.speechText.slice(-OSV_BUBBLE_MAX_CHARS));
    if (rec.thoughtText) _osShowBubble(rec, 'thought', rec.thoughtText.slice(-OSV_BUBBLE_MAX_CHARS));
  }, OSV_BUBBLE_COALESCE_MS);
}

function _osExpireBubbles(now) {
  _osAgents.forEach((rec) => {
    // The last bubble never disappears: the scene always shows each
    // participant's most recent message or thought. Linger only dims it
    // (osv-stale) and hides the OLDER of the two kinds when both show.
    const speechShown = rec.speechEl && rec.speechEl.style.display !== 'none';
    const thoughtShown = rec.thoughtEl && rec.thoughtEl.style.display !== 'none';
    if (speechShown && now - rec.speechAt > OSV_BUBBLE_LINGER_MS) {
      rec.speechText = '';
      if (thoughtShown && rec.thoughtAt > rec.speechAt) {
        rec.speechEl.style.display = 'none';
      } else {
        rec.speechEl.classList.add('osv-stale');
      }
    }
    if (thoughtShown && now - rec.thoughtAt > OSV_BUBBLE_LINGER_MS) {
      rec.thoughtText = '';
      if (speechShown && rec.speechAt >= rec.thoughtAt) {
        rec.thoughtEl.style.display = 'none';
      } else {
        rec.thoughtEl.classList.add('osv-stale');
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
  rec.log.push({ ts: Date.now(), kind: kind, title: String(title || ''),
                 body: String(body || '') });
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
      const content = String(m.content || '').replace(/^\[[^\]]+\]:\s*/, '');
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

// Conversation switch: desks survive (stable layout) but bubbles, logs
// and desk props belong to the previous transcript — clear them.
function openspaceResetTransient() {
  _osSeededIds.clear();
  _osAgents.forEach((rec) => {
    rec.log = [];
    rec.lastSpeech = null; rec.lastThought = null;
    rec.speechText = ''; rec.thoughtText = '';
    if (rec.speechEl) rec.speechEl.style.display = 'none';
    if (rec.thoughtEl) rec.thoughtEl.style.display = 'none';
    (rec.tools || []).slice().forEach((entry) => _osRemoveTool(rec, entry));
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

function _osDropTool(rec, toolName) {
  if (!rec || !_osScene || !_osThree || rec.kind === 'user') return;
  rec.tools = rec.tools || [];
  while (rec.tools.length >= OSV_TOOL_MAX) _osRemoveTool(rec, rec.tools[0]);
  const sprite = _osToolSprite(_osToolEmoji(toolName));
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
  if (!src.avatar || !dst.seat) return;
  src.awayAt = dst.key;
  // Stand next to the delegate's desk, slightly to the side.
  _osWalkTo(src, { x: dst.seat.x - 1.8, z: dst.seat.z + 1.35 });
  _osShowBubble(src, 'speech', t('osvDelegatesTo') + ' ' + dst.name);
  _osSetState(dst, 'thinking');
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
    if (rec) _osSetState(rec, 'thinking');
  });
  on('thinking_delta', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    if (rec.state !== 'thinking') _osSetState(rec, 'thinking');
    if (_osActive) _osStreamBubble(rec, 'thought', d.content || '');
  });
  on('thinking_content', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    _osLog(rec, 'thought', t('osvThought'), d.content || '');
    if (_osActive) _osShowBubble(rec, 'thought', d.content || '');
  });
  on('tool_call', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    let args = '';
    try { args = JSON.stringify(d.arguments || {}); } catch (_) { args = ''; }
    _osLog(rec, 'tool', d.tool || 'tool', args);
    _osSetState(rec, 'tool', d.tool || '');
    if (_osActive) _osDropTool(rec, d.tool || 'tool');
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
    if (d.role === 'assistant') {
      const rec = _osEnsureAgent(_osEventAgent(d));
      if (!rec) return;
      if (d.msg_id) _osSeededIds.add(d.msg_id);
      _osLog(rec, 'message', t('osvSaid'), d.content);
      if (_osActive) { _osShowBubble(rec, 'speech', d.content); rec.speechText = ''; }
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
    if (rec) { _osSetState(rec, 'idle'); _osDelegateDone(rec.name); }
  });
  on('turn_complete', (d) => {
    const rec = _osAgents.get(_osKey(_osEventAgent(d)));
    if (rec) _osSetState(rec, 'idle');
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
    if (rec) _osSetState(rec, 'thinking');
  });
  on('sub_agent_tool', (d) => {
    const rec = _osEnsureAgent(d.agent_name, { guest: true });
    if (!rec) return;
    _osLog(rec, 'tool', d.tool || 'tool', '');
    _osSetState(rec, 'tool', d.tool || '');
    if (_osActive) _osDropTool(rec, d.tool || 'tool');
  });
  on('sub_agent_done', (d) => {
    const rec = _osAgents.get(_osKey(d.agent_name));
    if (rec) {
      _osLog(rec, 'message', t('osvDone'), d.content || '');
      _osSetState(rec, 'idle');
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
  // Tweens (walking).
  _osTweens = _osTweens.filter((tw) => {
    const p = Math.min(1, (ts - tw.start) / tw.dur);
    const ease = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
    const av = tw.rec.avatar;
    if (av) {
      av.position.x = tw.from.x + (tw.to.x - tw.from.x) * ease;
      av.position.z = tw.from.z + (tw.to.z - tw.from.z) * ease;
      av.position.y = Math.abs(Math.sin(p * Math.PI * 6)) * 0.12;
    }
    if (p >= 1) { if (av) av.position.y = 0; if (tw.onDone) tw.onDone(); return false; }
    return true;
  });
  // Per-agent idle animation + halo + overlay projection.
  const selKey = _osKey(typeof selectedAgent !== 'undefined' ? selectedAgent : '');
  const tweening = new Set(_osTweens.map((tw) => tw.rec));
  _osAgents.forEach((rec) => {
    if (!rec.avatar) { if (_osScene && _osThree && !rec.group) _osBuildDesk(rec); return; }
    if (rec.halo) {
      rec.halo.visible = rec.key === selKey;
      if (rec.halo.visible) rec.halo.rotation.z = ts / 900;
    }
    if (rec.state === 'thinking') {
      rec.avatar.rotation.y = Math.sin(ts / 700) * 0.15;
    } else if (rec.avatar.rotation.y !== 0) rec.avatar.rotation.y = 0;
    // Working bob: a busy agent visibly types away at the desk.
    if (!tweening.has(rec)) {
      rec.avatar.position.y = rec.state === 'tool'
        ? Math.abs(Math.sin(ts / 170)) * 0.07 : 0;
    }
    _osProject(rec);
  });
  _osTickTools(ts);
  _osExpireBubbles(now);
}

// Project the avatar's head to screen space and pin the DOM elements.
const _osProjVec = { v: null };
function _osProject(rec) {
  if (!_osOverlay || !_osCamera || !rec.avatar) return;
  const T = _osThree;
  if (!_osProjVec.v) _osProjVec.v = new T.Vector3();
  const v = _osProjVec.v;
  v.set(rec.avatar.position.x, 2.6, rec.avatar.position.z);
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
      'translate(' + (x + off) + 'px,' + (y - 14) + 'px) translate(-50%, -100%)';
  }
  if (rec.thoughtEl && rec.thoughtEl.style.display !== 'none') {
    const lift = rec.speechEl && rec.speechEl.style.display !== 'none'
      ? rec.speechEl.offsetHeight + 22 : 14;
    rec.thoughtEl.style.transform =
      'translate(' + (x + off) + 'px,' + (y - lift) + 'px) translate(-50%, -100%)';
  }
  if (rec.statusEl && rec.statusEl.style.display !== 'none') {
    rec.statusEl.style.transform =
      'translate(' + (x + off) + 'px,' + (y + 26) + 'px) translate(-50%, 0)';
  }
}

// ── Pointer: orbit drag, wheel zoom, click → PC dialog ──────────
function _osPointerDown(e) {
  _osDrag = { x: e.clientX, y: e.clientY, moved: false };
  try { _osCanvas.setPointerCapture(e.pointerId); } catch (_) {}
}

function _osPointerMove(e) {
  if (!_osDrag) return;
  const dx = e.clientX - _osDrag.x, dy = e.clientY - _osDrag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) _osDrag.moved = true;
  if (_osDrag.moved) {
    _osCamAngle += dx * 0.008;
    _osCamHeight = Math.max(6, Math.min(40, _osCamHeight + dy * 0.05));
    _osDrag.x = e.clientX; _osDrag.y = e.clientY;
    _osUpdateCamera();
  }
}

function _osPointerUp(e) {
  const wasDrag = _osDrag && _osDrag.moved;
  _osDrag = null;
  if (wasDrag || !_osRaycaster || !_osCamera) return;
  const bounds = _osCanvas.getBoundingClientRect();
  const T = _osThree;
  const ndc = new T.Vector2(
    ((e.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((e.clientY - bounds.top) / bounds.height) * 2 + 1);
  _osRaycaster.setFromCamera(ndc, _osCamera);
  const hits = _osRaycaster.intersectObjects(_osScene.children, true);
  for (const hit of hits) {
    const key = hit.object && hit.object.userData && hit.object.userData.osvAgent;
    if (key) { openspaceOpenAgentDialog(key); return; }
  }
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
