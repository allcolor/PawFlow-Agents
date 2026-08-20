// ── Architectural office environment ─────────────────────────────
// OpenSpace interactions live in the other modules. This file owns the
// visual shell: a cutaway office, textured floors, walls, prop clusters and
// vacant workstations that keep small conversations from looking abandoned.

const OSV_ROOM = {
  minX: -9.5, maxX: 29.3, minZ: -11.5, maxZ: 18.5,
  wallHeight: 4.5,
};
const OSV_DOOR_X = 1.3;
const OSV_DOOR_Z = -9.45;
const OSV_RESOURCE_WALL = {
  x: 20.1, faceX: 19.94, zStart: 1.3, columns: 6, zStep: 1.55,
};
const OSV_PLACEHOLDER_DESKS = 9;
const _osVacantDesks = new Map();
let _osEnvironmentLights = [];

function _osEnvMat(color, roughness, metalness) {
  return new _osThree.MeshStandardMaterial({
    color: color,
    roughness: roughness == null ? 0.78 : roughness,
    metalness: metalness == null ? 0.02 : metalness,
  });
}

function _osShadow(obj, cast, receive) {
  obj.traverse((part) => {
    if (!part.isMesh) return;
    part.castShadow = cast !== false;
    part.receiveShadow = receive !== false;
  });
  return obj;
}

function _osBox(w, h, d, color, x, y, z, roughness, metalness) {
  const mesh = new _osThree.Mesh(
    new _osThree.BoxGeometry(w, h, d),
    _osEnvMat(color, roughness, metalness));
  mesh.position.set(x || 0, y || 0, z || 0);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function _osCylinder(rt, rb, h, color, x, y, z, segments) {
  const mesh = new _osThree.Mesh(
    new _osThree.CylinderGeometry(rt, rb, h, segments || 12),
    _osEnvMat(color));
  mesh.position.set(x || 0, y || 0, z || 0);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function _osWoodTexture() {
  const T = _osThree;
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 512;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#b98153';
  ctx.fillRect(0, 0, 512, 512);
  const plankH = 64;
  for (let row = 0; row < 8; row++) {
    const shift = row % 2 ? 128 : 0;
    for (let col = -1; col < 5; col++) {
      const x = col * 128 + shift;
      const light = 145 + ((row * 17 + col * 23) % 28);
      ctx.fillStyle = 'rgb(' + light + ',' + Math.round(light * 0.72)
        + ',' + Math.round(light * 0.49) + ')';
      ctx.fillRect(x + 2, row * plankH + 2, 124, plankH - 4);
      ctx.strokeStyle = 'rgba(73,42,24,.28)';
      ctx.strokeRect(x + 2, row * plankH + 2, 124, plankH - 4);
      ctx.strokeStyle = 'rgba(255,255,255,.08)';
      ctx.beginPath();
      ctx.moveTo(x + 12, row * plankH + 16);
      ctx.lineTo(x + 112, row * plankH + 19);
      ctx.stroke();
    }
  }
  const texture = new T.CanvasTexture(canvas);
  texture.wrapS = T.RepeatWrapping;
  texture.wrapT = T.RepeatWrapping;
  texture.repeat.set(8, 6);
  texture.colorSpace = T.SRGBColorSpace;
  texture.anisotropy = Math.min(8, _osRenderer.capabilities.getMaxAnisotropy());
  return texture;
}

function _osBuildWallBlock(x, z, w, d, h, y) {
  const wall = _osBox(w, h, d, 0xe7e0d2, x, y == null ? h / 2 : y, z, 0.92, 0);
  if (_osRoomMats) {
    if (!_osRoomMats.walls) _osRoomMats.walls = [];
    _osRoomMats.walls.push(wall.material);
  }
  _osScene.add(wall);
  return wall;
}

function _osBuildWall(x, z, w, d, h) {
  const wall = _osBuildWallBlock(x, z, w, d, h);
  const cap = _osBox(w + 0.08, 0.12, d + 0.08, 0x6d6a62,
    x, h + 0.06, z, 0.72, 0.04);
  _osScene.add(cap);
  return wall;
}

// Build the wall around openings instead of painting glass over a solid box.
// `at` is an offset from the wall centre along its span axis.
function _osBuildWallWithOpenings(x, z, w, d, h, axis, openings) {
  const span = axis === 'z' ? d : w;
  const thickness = axis === 'z' ? w : d;
  const sorted = (openings || []).slice().sort((a, b) => a.at - b.at);
  const add = (at, length, height, y) => {
    if (length <= 0.01 || height <= 0.01) return;
    if (axis === 'z') _osBuildWallBlock(x, z + at, thickness, length, height, y);
    else _osBuildWallBlock(x + at, z, length, thickness, height, y);
  };
  let cursor = -span / 2;
  sorted.forEach((opening) => {
    const left = Math.max(-span / 2, opening.at - opening.width / 2);
    const right = Math.min(span / 2, opening.at + opening.width / 2);
    add((cursor + left) / 2, left - cursor, h, h / 2);
    add(opening.at, right - left, opening.bottom, opening.bottom / 2);
    add(opening.at, right - left, h - opening.top, (opening.top + h) / 2);
    cursor = Math.max(cursor, right);
  });
  add((cursor + span / 2) / 2, span / 2 - cursor, h, h / 2);
  _osScene.add(_osBox(w + 0.08, 0.12, d + 0.08, 0x6d6a62,
    x, h + 0.06, z, 0.72, 0.04));
}

function _osBuildWindow(x, z, w, rotationY, bottom, top) {
  const T = _osThree;
  const group = new T.Group();
  const low = bottom == null ? 1.0 : bottom;
  const high = top == null ? 3.7 : top;
  const height = high - low;
  const cy = (low + high) / 2;
  const glass = new T.Mesh(
    new T.BoxGeometry(w, Math.max(0.1, height - 0.3), 0.08),
    new T.MeshStandardMaterial({
      color: 0x9dc9d8, transparent: true, opacity: 0.32,
      roughness: 0.18, metalness: 0.06,
    }));
  glass.position.y = cy;
  group.add(glass);
  [-1, 1].forEach((s) => {
    group.add(_osBox(0.12, height, 0.14, 0x44505a, s * w / 2, cy, 0, 0.5, 0.25));
  });
  group.add(_osBox(w + 0.2, 0.12, 0.14, 0x44505a, 0, low, 0, 0.5, 0.25));
  group.add(_osBox(w + 0.2, 0.12, 0.14, 0x44505a, 0, high, 0, 0.5, 0.25));
  group.position.set(x, 0, z);
  group.rotation.y = rotationY || 0;
  _osShadow(group);
  _osScene.add(group);
}

function _osBuildChair(color) {
  const T = _osThree;
  const chair = new T.Group();
  const seat = _osBox(0.72, 0.16, 0.72, color || 0x596475, 0, 0.64, 0, 0.72, 0.05);
  const back = _osBox(0.72, 0.82, 0.15, color || 0x596475, 0, 1.04, -0.31, 0.72, 0.05);
  const stem = _osCylinder(0.08, 0.08, 0.55, 0x353b43, 0, 0.32, 0, 8);
  const base = _osCylinder(0.34, 0.34, 0.07, 0x353b43, 0, 0.06, 0, 8);
  chair.add(seat, back, stem, base);
  return _osShadow(chair);
}

function _osBuildMonitor() {
  const T = _osThree;
  const monitor = new T.Group();
  const shell = _osBox(1.15, 0.74, 0.1, 0x232932, 0, 0.65, 0, 0.52, 0.2);
  const glow = new T.Mesh(
    new T.PlaneGeometry(0.98, 0.57),
    new T.MeshStandardMaterial({
      color: 0x35546f, emissive: 0x16374f, emissiveIntensity: 0.55,
      roughness: 0.42,
    }));
  glow.position.set(0, 0.65, 0.056);
  const stand = _osBox(0.1, 0.42, 0.1, 0x333941, 0, 0.25, 0, 0.5, 0.2);
  monitor.add(shell, glow, stand);
  return _osShadow(monitor);
}

function _osBuildOfficeDesk(index) {
  const T = _osThree;
  const seat = _osSeatPosition(index);
  const group = new T.Group();
  const top = _osBox(3.25, 0.18, 1.55, 0xa46d43, 0, 1.05, 0, 0.68, 0.01);
  group.add(top);
  [-1, 1].forEach((sx) => {
    [-1, 1].forEach((sz) => {
      group.add(_osBox(0.13, 1.0, 0.13, 0x424951,
        sx * 1.38, 0.5, sz * 0.6, 0.55, 0.18));
    });
  });
  const monitor = _osBuildMonitor();
  monitor.position.set(0, 1.08, -0.38);
  const chair = _osBuildChair(index % 2 ? 0x596b7f : 0x6a5f78);
  chair.position.set(0, 0, 1.22);
  chair.rotation.y = Math.PI;
  const keyboard = _osBox(0.9, 0.05, 0.32, 0x363b42, 0, 1.17, 0.25, 0.5, 0.12);
  group.add(monitor, chair, keyboard);
  group.position.set(seat.x, 0, seat.z);
  group.userData.osvVacantDesk = index;
  return _osShadow(group);
}

function _osBuildPlant(scale) {
  const T = _osThree;
  const group = new T.Group();
  const pot = _osCylinder(0.34, 0.27, 0.52, 0xa86138, 0, 0.26, 0, 12);
  group.add(pot);
  for (let i = 0; i < 7; i++) {
    const leaf = new T.Mesh(
      new T.SphereGeometry(0.32, 10, 7),
      _osEnvMat(i % 2 ? 0x4f884f : 0x3e7545));
    const a = i / 7 * Math.PI * 2;
    leaf.scale.set(0.5, 1.2, 0.42);
    leaf.position.set(Math.cos(a) * 0.22, 0.78 + (i % 3) * 0.18, Math.sin(a) * 0.22);
    leaf.rotation.z = Math.cos(a) * 0.55;
    leaf.rotation.x = Math.sin(a) * 0.55;
    group.add(leaf);
  }
  group.scale.setScalar(scale || 1);
  return _osShadow(group);
}

function _osBuildCabinet(x, z, rotationY) {
  const T = _osThree;
  const group = new T.Group();
  group.add(_osBox(1.45, 2.5, 0.65, 0x68737e, 0, 1.25, 0, 0.56, 0.18));
  [-0.78, 0, 0.78].forEach((y) => {
    const seam = _osBox(1.2, 0.035, 0.03, 0x333a42, 0, 1.25 + y, 0.34, 0.58, 0.15);
    group.add(seam);
  });
  [-0.16, 0.16].forEach((x2) => {
    group.add(_osBox(0.08, 0.28, 0.05, 0xc6c4b8, x2, 1.25, 0.36, 0.35, 0.45));
  });
  group.position.set(x, 0, z);
  group.rotation.y = rotationY || 0;
  _osScene.add(_osShadow(group));
}

function _osBuildConferenceZone() {
  const T = _osThree;
  const partitionX = 20.1, partitionZ = 3.0, partitionDepth = 0.24;
  const group = new T.Group();
  const rug = new T.Mesh(
    new T.PlaneGeometry(8.8, 8.2),
    new T.MeshStandardMaterial({ color: 0x3e5155, roughness: 0.95 }));
  rug.rotation.x = -Math.PI / 2;
  rug.position.y = 0.035;
  rug.receiveShadow = true;
  group.add(rug);
  const top = _osCylinder(2.55, 2.55, 0.2, 0x7a513b, 0, 1.05, 0, 32);
  const base = _osCylinder(0.58, 0.82, 1.0, 0x383e45, 0, 0.5, 0, 16);
  group.add(top, base);
  for (let i = 0; i < 7; i++) {
    const a = i / 7 * Math.PI * 2;
    const chair = _osBuildChair(i % 2 ? 0x53677a : 0x6b5f75);
    chair.position.set(Math.cos(a) * 3.45, 0, Math.sin(a) * 3.2);
    chair.rotation.y = -a + Math.PI / 2;
    group.add(chair);
  }
  group.position.set(24.7, 0, 7.4);
  _osScene.add(_osShadow(group));

  // The x partition is the accessible resource gallery. The other partition
  // contains a real window opening into the meeting room.
  _osBuildWall(partitionX, 5.4, partitionDepth, 8.7, 2.7);
  _osBuildWallWithOpenings(24.8, partitionZ, 9.6, partitionDepth, 2.7, 'x', [
    { at: 0.2, width: 4.2, bottom: 0.45, top: 2.35 },
  ]);
  _osBuildWindow(25.0, partitionZ, 4.2, 0, 0.45, 2.35);
}

function _osBuildLoungeZone() {
  const T = _osThree;
  const group = new T.Group();
  const rug = new T.Mesh(
    new T.PlaneGeometry(7.5, 5.5),
    new T.MeshStandardMaterial({ color: 0x6d7187, roughness: 0.96 }));
  rug.rotation.x = -Math.PI / 2;
  rug.position.y = 0.04;
  group.add(rug);
  const sofa = (z, rot, color) => {
    const s = new T.Group();
    s.add(_osBox(3.8, 0.55, 1.25, color, 0, 0.52, 0, 0.84, 0));
    s.add(_osBox(3.8, 1.05, 0.32, color, 0, 1.0, -0.48, 0.84, 0));
    [-1, 1].forEach((side) => {
      s.add(_osBox(0.3, 0.75, 1.25, color, side * 1.9, 0.63, 0, 0.84, 0));
    });
    s.position.set(0, 0, z);
    s.rotation.y = rot || 0;
    group.add(s);
  };
  sofa(-1.65, 0, 0x705879);
  sofa(1.55, Math.PI, 0x4d6674);
  const table = _osBox(2.3, 0.18, 1.25, 0x9a6a48, 0, 0.55, 0, 0.7, 0.02);
  group.add(table);
  const plant = _osBuildPlant(1.15);
  plant.position.set(3.1, 0, -1.6);
  group.add(plant);
  group.position.set(14.0, 0, -5.6);
  _osScene.add(_osShadow(group));
}

function _osBuildServiceZone() {
  const T = _osThree;
  const group = new T.Group();
  const counter = _osBox(5.5, 1.05, 1.15, 0xb07a4e, 0, 0.53, 0, 0.78, 0);
  group.add(counter);
  [-1.8, 0, 1.8].forEach((x) => {
    const machine = _osBox(0.75, 1.25, 0.65, 0x59636b, x, 1.65, 0, 0.4, 0.28);
    group.add(machine);
  });
  const fridge = _osBox(1.35, 2.9, 1.2, 0xb9c5ca, 3.4, 1.45, 0, 0.36, 0.35);
  group.add(fridge);
  group.position.set(24.4, 0, -6.7);
  _osScene.add(_osShadow(group));
  _osBuildCabinet(22.0, -10.4, 0);
  _osBuildCabinet(24.0, -10.4, 0);
  _osBuildCabinet(26.0, -10.4, 0);
}

function _osBuildOutdoor() {
  const T = _osThree;
  const ground = new T.Mesh(
    new T.PlaneGeometry(110, 95),
    new T.MeshStandardMaterial({ color: 0x617a55, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.28;
  ground.receiveShadow = true;
  _osScene.add(ground);
  [[-15, -14], [-3, -17], [12, -17], [28, -16], [37, -7], [39, 10],
   [31, 23], [12, 25], [-6, 24], [-16, 11]].forEach((p, i) => {
    const tree = new T.Group();
    tree.add(_osCylinder(0.22, 0.3, 2.7, 0x76513b, 0, 1.35, 0, 9));
    const crown = new T.Mesh(
      new T.DodecahedronGeometry(1.25 + (i % 3) * 0.14, 0),
      _osEnvMat(i % 2 ? 0x496e43 : 0x537b49));
    crown.position.y = 3.0;
    crown.scale.y = 1.25;
    tree.add(crown);
    tree.position.set(p[0], -0.25, p[1]);
    _osScene.add(_osShadow(tree));
  });
}

function _osBuildVacantDesks() {
  for (let i = 0; i < OSV_PLACEHOLDER_DESKS; i++) {
    if ([0, 1, 2, 3, 4, 5, 6, 7, 8].indexOf(i) < 0) continue;
    const group = _osBuildOfficeDesk(i);
    _osVacantDesks.set(i, group);
    _osScene.add(group);
  }
}

function _osClaimDeskSlot(index) {
  const group = _osVacantDesks.get(index);
  if (!group) return;
  _osScene.remove(group);
  group.traverse((part) => {
    if (part.geometry) part.geometry.dispose();
    if (part.material) part.material.dispose();
  });
  _osVacantDesks.delete(index);
}

function _osReleaseDeskSlot(index) {
  if (!_osScene || !_osThree || index < 0 || index >= OSV_PLACEHOLDER_DESKS
      || _osVacantDesks.has(index)) return;
  const group = _osBuildOfficeDesk(index);
  _osVacantDesks.set(index, group);
  _osScene.add(group);
}

function _osBuildEnvironment() {
  const T = _osThree;
  _osBuildOutdoor();

  const slab = _osBox(
    OSV_ROOM.maxX - OSV_ROOM.minX, 0.28,
    OSV_ROOM.maxZ - OSV_ROOM.minZ, 0x3d4144,
    (OSV_ROOM.minX + OSV_ROOM.maxX) / 2, -0.1,
    (OSV_ROOM.minZ + OSV_ROOM.maxZ) / 2, 0.95, 0.02);
  slab.receiveShadow = true;
  slab.castShadow = false;
  _osScene.add(slab);

  const floorMat = new T.MeshStandardMaterial({
    map: _osWoodTexture(), color: 0xffffff, roughness: 0.82, metalness: 0,
  });
  const floor = new T.Mesh(
    new T.PlaneGeometry(OSV_ROOM.maxX - OSV_ROOM.minX - 0.5,
      OSV_ROOM.maxZ - OSV_ROOM.minZ - 0.5),
    floorMat);
  floor.rotation.x = -Math.PI / 2;
  floor.position.set((OSV_ROOM.minX + OSV_ROOM.maxX) / 2, 0.045,
    (OSV_ROOM.minZ + OSV_ROOM.maxZ) / 2);
  floor.name = 'floor';
  floor.receiveShadow = true;
  _osScene.add(floor);
  _osRoomMats.floor = floor.material;

  const roomCx = (OSV_ROOM.minX + OSV_ROOM.maxX) / 2;
  const roomCz = (OSV_ROOM.minZ + OSV_ROOM.maxZ) / 2;
  _osBuildWallWithOpenings(roomCx, OSV_ROOM.minZ,
    OSV_ROOM.maxX - OSV_ROOM.minX, 0.32, OSV_ROOM.wallHeight, 'x', [
      { at: 1.0 - roomCx, width: 4.5, bottom: 1.0, top: 3.7 },
      { at: 18.5 - roomCx, width: 5.0, bottom: 1.0, top: 3.7 },
    ]);
  _osBuildWallWithOpenings(OSV_ROOM.minX, roomCz, 0.32,
    OSV_ROOM.maxZ - OSV_ROOM.minZ, OSV_ROOM.wallHeight, 'z', [
      { at: -4.0 - roomCz, width: 4.4, bottom: 1.0, top: 3.7 },
      { at: 12.5 - roomCz, width: 4.4, bottom: 1.0, top: 3.7 },
    ]);
  _osBuildWall(OSV_ROOM.maxX, 1.0, 0.32, 25.0, OSV_ROOM.wallHeight);
  _osBuildWall(-5.9, OSV_ROOM.maxZ, 7.2, 0.32, 1.05);
  _osBuildWall(25.1, OSV_ROOM.maxZ, 10.8, 0.32, 1.05);
  _osBuildWallWithOpenings(8.0, OSV_DOOR_Z, 17.0, 0.28, 4.2, 'x', [
    { at: OSV_DOOR_X - 8.0, width: 2.4, bottom: 0, top: 3.4 },
  ]);
  _osBuildWall(-7.0, 5.0, 0.28, 16.5, 3.8);

  _osBuildWindow(OSV_ROOM.minX, -4.0, 4.4, Math.PI / 2);
  _osBuildWindow(OSV_ROOM.minX, 12.5, 4.4, Math.PI / 2);
  _osBuildWindow(1.0, OSV_ROOM.minZ, 4.5, 0);
  _osBuildWindow(18.5, OSV_ROOM.minZ, 5.0, 0);

  _osBuildConferenceZone();
  _osBuildLoungeZone();
  _osBuildServiceZone();
  _osBuildVacantDesks();

  [[-7.2, -1.8, 1.05], [-7.0, 14.8, 1.15], [18.2, -8.8, 0.9],
   [28.5, 15.8, 1.2], [17.8, 16.5, 0.8]].forEach((p) => {
    const plant = _osBuildPlant(p[2]);
    plant.position.set(p[0], 0, p[1]);
    _osScene.add(plant);
  });

  const beamMat = _osEnvMat(0x343a40, 0.55, 0.25);
  [[-3, -2], [8, -2], [19, -2], [26, 12]].forEach((p) => {
    const lamp = new T.Mesh(new T.BoxGeometry(4.8, 0.12, 0.55), beamMat);
    lamp.position.set(p[0], 4.25, p[1]);
    lamp.castShadow = false;
    _osScene.add(lamp);
    const light = new T.PointLight(0xffeed4, 5.5, 10, 2);
    light.position.set(p[0], 4.0, p[1]);
    _osEnvironmentLights.push(light);
    _osScene.add(light);
  });
}
