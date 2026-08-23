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
  const dx = target.x - rec.avatar.position.x;
  const dz = target.z - rec.avatar.position.z;
  const distance = Math.hypot(dx, dz);
  if (distance < 0.01) { if (onDone) onDone(); return; }
  rec.avatar.rotation.y = Math.atan2(dx, dz);
  _osTweens.push({
    rec: rec,
    from: { x: rec.avatar.position.x, z: rec.avatar.position.z },
    to: { x: target.x, z: target.z },
    start: performance.now(),
    dur: Math.max(OSV_WALK_MIN_MS, Math.min(OSV_WALK_MAX_MS,
      distance / OSV_WALK_UNITS_PER_SEC * 1000)),
    onDone: onDone || null,
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
  on('agui_activity', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    const activity = d.activity || {};
    const content = activity && typeof activity.content === 'object' ? activity.content : {};
    const pick = (vals) => vals.find((v) => typeof v === 'string' && v) || '';
    const label = typeof activity === 'string' ? activity
      : pick([content.message, content.label, content.name, activity.activityType,
        activity.message, activity.label, activity.name, d.event_type, 'AG-UI']);
    _osLog(rec, 'activity', 'AG-UI', String(label));
    _osSetState(rec, 'tool', String(label));
  });
  on('agui_step', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (!rec) return;
    const step = d.step || {};
    const label = step.name || step.stepName || step.message || d.event_type || 'step';
    _osLog(rec, 'step', 'AG-UI step', String(label));
    _osSetState(rec, /FINISHED|END|COMPLETED/.test(d.event_type || '') ? 'thinking' : 'tool', String(label));
  });
  on('agui_state_snapshot', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (rec) _osLog(rec, 'state', 'AG-UI state', JSON.stringify(d.state || {}));
  });
  on('agui_state_delta', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (rec) _osLog(rec, 'state', 'AG-UI state Δ', JSON.stringify(d.delta || []));
  });
  on('agui_usage', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (rec) _osLog(rec, 'usage', 'AG-UI usage', JSON.stringify(d.usage || {}));
  });
  on('agui_custom', (d) => {
    const rec = _osEnsureAgent(_osEventAgent(d));
    if (rec) _osLog(rec, 'custom', 'AG-UI event', JSON.stringify(d.event || {}));
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
function _osAdaptPixelRatio(ts) {
  if (!_osRenderer) return;
  if (_osLastFrameTs) {
    const elapsed = Math.max(1, Math.min(100, ts - _osLastFrameTs));
    _osFrameMs = _osFrameMs * 0.9 + elapsed * 0.1;
  }
  _osLastFrameTs = ts;
  if (ts - _osQualityAt < 2000) return;
  _osQualityAt = ts;
  let next = _osSoftwareRenderer ? 1 : _osPixelRatio;
  if (!_osSoftwareRenderer && _osFrameMs > 24) {
    next = Math.max(OSV_DPR_MIN, _osPixelRatio - 0.25);
  } else if (!_osSoftwareRenderer && _osFrameMs < 17) {
    next = Math.min(_osDprMax, _osPixelRatio + 0.25);
  }
  if (next !== _osPixelRatio) {
    _osPixelRatio = next;
    _osRenderer.setPixelRatio(_osPixelRatio);
    if (_osScreenOcclusionRenderer) {
      _osScreenOcclusionRenderer.setPixelRatio(_osPixelRatio);
    }
  }
}

function _osRenderScreenOcclusion() {
  const r = _osScreenOcclusionRenderer;
  const rect = _osScreenOcclusionRect;
  if (!r || !rect || !_osScreenOcclusionCanvas
      || _osScreenOcclusionCanvas.style.display === 'none') return;
  r.setScissorTest(false);
  r.clear();
  r.setScissor(rect.left, _osLastH - rect.bottom,
    rect.right - rect.left, rect.bottom - rect.top);
  r.setScissorTest(true);
  const background = _osScene.background;
  _osScene.background = null;
  try {
    r.render(_osScene, _osCamera);
  } finally {
    _osScene.background = background;
    r.setScissorTest(false);
  }
}

function _osAnimateRig(rec, ts, walking) {
  const rig = rec && rec.rig;
  if (!rig) return;
  const breath = Math.sin(ts / 700 + rig.blinkOffset) * 0.055;
  rig.body.scale.set(1, 1.12 + breath, 0.92);
  rig.body.position.y = 0.95;
  rig.body.rotation.z = 0;
  rig.arms.forEach((arm) => { arm.rotation.x = 0; arm.rotation.z = 0; });
  rig.feet.forEach((foot) => { foot.position.z = 0.22; foot.rotation.x = 0; });
  const blink = ((ts + rig.blinkOffset) % 4200) > 3880 ? 0.08 : 1;
  rig.eyes.forEach((eye) => { eye.scale.y = blink; });
  rig.pupils.forEach((pupil, i) => {
    pupil.scale.y = blink;
    pupil.position.x = 0.21 * (i ? 1 : -1);
  });
  rig.mouth.scale.set(1, 1, 1);
  if (walking) {
    const stride = Math.sin(ts / 95);
    rig.arms.forEach((arm, i) => { arm.rotation.x = stride * (i ? -0.9 : 0.9); });
    rig.feet.forEach((foot, i) => {
      foot.position.z = 0.22 + stride * (i ? 0.16 : -0.16);
    });
    return;
  }
  if (rec.state === 'thinking') {
    rig.body.rotation.z = Math.sin(ts / 650) * 0.08;
    rig.pupils.forEach((pupil) => { pupil.position.x += Math.sin(ts / 900) * 0.035; });
  } else {
    rig.body.rotation.z = rec.state === 'error' ? -0.18 : 0;
  }
  if (rec.state === 'talking') {
    rig.mouth.scale.y = 0.35 + Math.abs(Math.sin(ts / 90)) * 1.25;
  } else if (rec.state === 'tool') {
    rig.arms.forEach((arm, i) => {
      arm.rotation.x = Math.sin(ts / 75 + i * Math.PI) * 1.05;
    });
  } else if (rec.state === 'waiting') {
    rig.arms[0].rotation.z = -2.1;
  }
}

function _osStartLoop() {
  if (_osRaf || !_osRenderer) return;
  _osLastFrameTs = 0;
  _osFrameMs = 16.7;
  _osQualityAt = performance.now();
  const step = (ts) => {
    _osRaf = 0;
    if (!_osActive || document.hidden) return;
    _osClock = ts;
    _osTick(ts);
    _osAdaptPixelRatio(ts);
    _osRenderer.render(_osScene, _osCamera);
    _osRenderScreenOcclusion();
    _osRaf = requestAnimationFrame(step);
  };
  _osRaf = requestAnimationFrame(step);
}

function _osStopLoop() {
  if (_osRaf) { cancelAnimationFrame(_osRaf); _osRaf = 0; }
  _osLastFrameTs = 0;
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
    if (p >= 1) {
      if (av) {
        av.position.y = 0;
        if (tw.rec.kind === 'agent'
            && Math.abs(tw.to.x - tw.rec.homeSeat.x) < 0.01
            && Math.abs(tw.to.z - (tw.rec.homeSeat.z + 1.35)) < 0.01) {
          av.rotation.y = Math.PI;
        }
      }
      finished.push(tw);
      return false;
    }
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
    // Root lean/bounce stays readable from afar; the chibi rig adds limbs,
    // eyes and mouth at close range.
    const sway = { idle: [1100, 0.035], thinking: [350, 0.22],
                   talking: [220, 0.14], tool: [90, 0.09] }[rec.state];
    const walking = tweening.has(rec);
    rec.avatar.rotation.z = !walking && sway ? Math.sin(ts / sway[0]) * sway[1] : 0;
    if (!walking) {
      const bounce = { idle: [850, 0.055], tool: [130, 0.28],
                       talking: [200, 0.17], thinking: [480, 0.11] }[rec.state];
      rec.avatar.position.y = bounce
        ? Math.abs(Math.sin(ts / bounce[0])) * bounce[1] : 0;
    }
    _osAnimateRig(rec, ts, walking);
    // The PC screen flickers while its agent works.
    if (rec.screenMat) {
      rec.screenMat.emissiveIntensity = rec.state !== 'idle'
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

// ── Pointer: orbit drag, wheel zoom, and distinct scene actions ──
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
      _osSurfaceFocus = '';
      _osCamDist = Math.max(3, Math.min(90, _osCamDist * _osPinchPrev.dist / s.dist));
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
      _osSurfaceFocus = '';
      const k = _osCamDist * 0.0016;
      const a = _osCamAngle;
      _osCamPan.x += (-Math.sin(a) * dx + Math.cos(a) * dy) * k;
      _osCamPan.z += (Math.cos(a) * dx + Math.sin(a) * dy) * k;
      _osCamPan.x = Math.max(-40, Math.min(40, _osCamPan.x));
      _osCamPan.z = Math.max(-40, Math.min(40, _osCamPan.z));
    } else {
      _osSurfaceFocus = '';
      _osCamAngle += dx * 0.008;
      _osCamHeight = Math.max(0, Math.min(60, _osCamHeight + dy * 0.05));
    }
    _osDrag.x = e.clientX; _osDrag.y = e.clientY;
    _osUpdateCamera();
  }
}

function _osSelectAgent(key) {
  const rec = _osAgents.get(_osKey(key));
  if (!rec || rec.kind !== 'agent' || rec.guest) return Promise.resolve(false);
  if (typeof selectedAgent !== 'undefined' && _osKey(selectedAgent) === rec.key) {
    return Promise.resolve(true);
  }
  if (typeof cmdAgentSelect !== 'function') return Promise.resolve(false);
  const selection = cmdAgentSelect(rec.name);
  if (selection && typeof selection.catch === 'function') {
    selection.catch((error) => console.error('openspace: agent selection failed', error));
  }
  return selection || Promise.resolve(false);
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
    if (ud && ud.osvBigScreen) { _osSetCameraView('conversation'); return; }
    if (ud && ud.osvTv) { _osSetCameraView('tv'); openspaceOpenTvDialog(); return; }
    if (ud && ud.osvResSection) {
      openspaceOpenResSectionDialog(ud.osvResSection, ud.osvResTitle);
      return;
    }
    if (ud && ud.osvPoster) {
      _osSetCameraView('resources'); _osOpenPoster(ud.osvPoster); return;
    }
    if (ud && ud.osvAgentPc) { openspaceOpenAgentDialog(ud.osvAgentPc); return; }
    if (ud && ud.osvUser) { openspaceOpenAgentDialog(ud.osvUser); return; }
    if (ud && ud.osvAgentAvatar) { _osSelectAgent(ud.osvAgentAvatar); return; }
  }
  // No agent under the cursor: clicking the floor walks YOUR avatar
  // there, and the spot becomes its new home (delivery trips return to
  // it).
  const floor = _osScene.getObjectByName('floor');
  if (!floor) return;
  // Furniture and the wall screen block the floor ray, so a screen click
  // cannot move the visitor into the display.
  if (!hits.length || hits[0].object !== floor) return;
  const ground = _osRaycaster.intersectObject(floor, false)[0];
  if (!ground) return;
  const me = _osEnsureUser(
    (typeof window !== 'undefined' && window._userId) || 'user');
  if (!me || !me.avatar) return;
  const gx = Math.max(OSV_ROOM.minX + 1, Math.min(OSV_ROOM.maxX - 1, ground.point.x));
  // Keep a navigable buffer between visitors and the projection wall.
  const gz = Math.max(OSV_SCREEN_Z + 2.2, Math.min(OSV_ROOM.maxZ - 1, ground.point.z));
  me.homeSeat = { x: gx, z: gz };
  _osWalkTo(me, { x: gx, z: gz });
  _osFollow = true;
}

function _osWheel(e) {
  e.preventDefault();
  _osSurfaceFocus = '';
  _osCamDist = Math.max(3, Math.min(90, _osCamDist + e.deltaY * 0.03));
  _osUpdateCamera();
}
