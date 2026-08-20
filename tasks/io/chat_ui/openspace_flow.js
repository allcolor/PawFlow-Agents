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
    // A paused queue shows NO current on the 3D stage either: the link
    // greys out and its dots stop (mirrors the Flow Runtime Console).
    rec.paused = !!e.paused;
    const src = nodes[e.source] || {};
    rec.active = !rec.paused
      && (rec.queue > 0 || !!src.in_flight || src.state === 'running');
    rec.line.material.color.setHex(
      rec.paused ? 0x8a93b8 : (rec.backpressured ? 0xe94560 : 0x4da3ff));
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
