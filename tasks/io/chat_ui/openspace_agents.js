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
  if (rec) {
    if (opts && opts.runtimeKind) rec.runtimeKind = opts.runtimeKind;
    if (rec.labelEl) rec.labelEl.textContent = rec.name + (rec.runtimeKind === 'external_agui' ? ' · AG-UI' : '');
    return rec;
  }
  // Guests hand their slot back on retirement; reuse those first so
  // repeated flash delegations do not march desks toward the horizon.
  const seatIndex = _osFreeSeats.length ? _osFreeSeats.shift() : _osSeatCount++;
  rec = {
    key: key,
    name: name,
    kind: 'agent',
    guest: !!(opts && opts.guest),
    runtimeKind: (opts && opts.runtimeKind) || 'llm',
    state: 'idle',
    stateSince: Date.now(),
    seat: _osSeatPosition(seatIndex),
    seatIndex: seatIndex,
    color: _osAgentColor(name),
    log: [],
    tools: [],
    lastSpeech: null, lastThought: null,
    group: null, avatar: null, rig: null, screenMat: null,
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
    // Never put the first visitor on the wall-screen's optical axis: the
    // conversation camera would look straight through that avatar.
    seat: { x: cx + (_osUserCount % 2 === 0 ? 1 : -1)
            * (Math.floor(_osUserCount / 2) + 1) * 4.0, z: -4.5 },
    color: _osAgentColor(clean),
    log: [],
    tools: [],
    lastSpeech: null, lastThought: null,
    group: null, avatar: null, rig: null, screenMat: null,
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
  _osClaimDeskSlot(rec.seatIndex);
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
  pc.traverse((o) => { o.userData.osvAgentPc = rec.key; });
  g.add(pc);
  rec.screenMat = screenMat;
  const chair = _osBuildChair(0x596b7f);
  chair.position.set(0, 0, 1.35);
  chair.rotation.y = Math.PI;
  g.add(chair);

  // Avatar: chibi mascot; front features live on local +z, π turns it toward
  // its desk and PC.
  const avatar = _osBuildChibi(rec);
  avatar.position.set(rec.seat.x, 0, rec.seat.z + 1.35);
  avatar.rotation.y = Math.PI;
  avatar.traverse((o) => { o.userData.osvAgentAvatar = rec.key; });
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

  _osScene.add(_osShadow(g));
  rec.group = g;

  _osBuildOverlayEls(rec, rec.name + (rec.runtimeKind === 'external_agui' ? ' · AG-UI' : ''), '');
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
  const arms = [], feet = [], eyes = [], pupils = [];
  let hash = 0;
  for (const ch of rec.key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
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
    arm.position.set(0, -0.12, 0);
    const armJoint = new T.Group();
    armJoint.position.set(0.62 * s, 0.97, 0.1);
    armJoint.add(arm);
    const foot = new T.Mesh(new T.SphereGeometry(0.15, 8, 6), bodyMat);
    foot.scale.set(1, 0.55, 1.25);
    foot.position.set(0.26 * s, 0.09, 0.22);
    g.add(eye, pupil, blush, armJoint, foot);
    eyes.push(eye); pupils.push(pupil); arms.push(armJoint); feet.push(foot);
  });
  const smile = new T.Mesh(
    new T.TorusGeometry(0.11, 0.025, 6, 12, Math.PI), mat(0x101018));
  smile.position.set(0, 1.02, 0.55);
  smile.rotation.z = Math.PI;   // arc opens upward → a smile
  g.add(smile);
  rec.rig = { body: body, arms: arms, feet: feet, eyes: eyes,
              pupils: pupils, mouth: smile, blinkOffset: hash % 3600 };
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
  // itself with the next message/thought (_osShowBubble re-shows); the
  // dismissed flag keeps the idle rule from restoring it meanwhile.
  [speech, thought].forEach((el) => {
    const x = document.createElement('span');
    x.className = 'osv-bubble-close';
    x.textContent = '\u00D7';
    x.onclick = (ev) => {
      ev.stopPropagation();
      el.style.display = 'none';
      if (el === speech) rec.speechDismissed = true; else rec.thoughtDismissed = true;
    };
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
  avatar.traverse((o) => { o.userData.osvUser = rec.key; });
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
    rec.thoughtDismissed = false;
  } else {
    rec.speechAt = stamp;
    rec.lastSpeech = { text: full, at: stamp };
    rec.speechDismissed = false;
    rec.speechSeeded = false;
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
  // A bubble restored from history stays up (the scene always shows the
  // last thing each participant said); only LIVE user bubbles fade out.
  if (kind === 'speech') rec.speechSeeded = true;
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

// Liveness reference: the active-agents tracker (server poll `list_active`,
// SSE hints in between) knows which agents are really running. Keyed by
// avatar key; when one agent runs several tasks the freshest entry wins.
function _osLiveAgents() {
  const live = new Map();
  if (typeof activeInteractions === 'undefined' || !activeInteractions) return live;
  Object.values(activeInteractions).forEach((it) => {
    if (!it || !it.name) return;
    const key = _osKey(it.name);
    const prev = live.get(key);
    if (!prev || (it.updatedAt || 0) > (prev.updatedAt || 0)) live.set(key, it);
  });
  return live;
}

function _osExpireBubbles(now) {
  const live = _osLiveAgents();
  _osAgents.forEach((rec) => {
    if (rec.kind === 'user') {
      // Live user messages are transient: they fade out after 10s. The
      // bubble restored at load keeps showing until a live one replaces it.
      const shown = rec.speechEl && rec.speechEl.style.display !== 'none';
      if (shown && !rec.speechSeeded
          && now - rec.speechAt > OSV_USER_BUBBLE_FADE_MS) {
        rec.speechText = '';
        rec.speechEl.style.display = 'none';
      }
      return;
    }
    if (rec.state === 'idle') {
      // Zzz rule: an idle agent always shows its last MESSAGE, never its
      // thinking — the thought bubble goes away and the last speech comes
      // back dimmed (unless the viewer dismissed it with ✕).
      if (rec.thoughtEl && rec.thoughtEl.style.display !== 'none') {
        rec.thoughtText = '';
        rec.thoughtEl.style.display = 'none';
      }
      if (rec.lastSpeech && !rec.speechDismissed && rec.speechEl
          && rec.speechEl.style.display === 'none') {
        rec.speechEl.classList.add('osv-stale');
        _osSetBubbleText(rec, 'speech', _osFull(rec.lastSpeech.text));
        rec.speechAt = rec.lastSpeech.at;
      }
    }
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
    // An agent the tracker lists as running never drifts to Zzz because
    // its provider stayed quiet: a long tool run or an unstreamed thinking
    // pass (flash delegates only forward tool_call/tool_result/thinking_
    // content) easily outlasts the linger window. One that went idle but
    // is still reported after that (fresh poll or SSE hint) wakes back up.
    const it = live.get(rec.key);
    if (it) {
      if (rec.state === 'idle' && (it.updatedAt || 0) > rec.stateSince) {
        const busyTool = it.activeTools && it.activeTools.length ? it.lastTool : '';
        _osSetState(rec, busyTool ? 'tool' : 'thinking', busyTool);
      }
      return;
    }
    // Agents whose turn ended drift back to idle without an explicit
    // done event for them (providers that only emit done for the
    // primary) — only once the tracker no longer lists them.
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
    _osApplyRoomStyle(cid);
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
