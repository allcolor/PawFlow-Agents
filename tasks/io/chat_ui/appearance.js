// User-owned visual preferences: UI scale plus optional image/video atmosphere.
// A per-user global value is inherited by every conversation unless that
// conversation owns an override. Uploaded media lives in IndexedDB so large
// blobs never inflate cookies or conversation files.

const APPEARANCE_PREFS_VERSION = 1;
const APPEARANCE_DB_NAME = 'pawflow-appearance';
const APPEARANCE_DB_STORE = 'backgrounds';
const APPEARANCE_MAX_MEDIA_BYTES = 80 * 1024 * 1024;
const APPEARANCE_DEFAULTS = Object.freeze({
  version: APPEARANCE_PREFS_VERSION,
  scale: 100,
  source: 'none',
  kind: 'image',
  url: '',
  name: '',
  dim: 38,
  blur: 0,
  saturation: 100,
  panel: 88,
  motion: false,
});

var _appearancePrefs = Object.assign({}, APPEARANCE_DEFAULTS);
var _appearanceObjectUrl = '';
var _appearanceKey = '';
var _appearanceScope = 'global';
var _appearanceApplySequence = 0;

function _appearanceUserId() {
  return String(window._userId || (window.PAWFLOW_EXTENSION_CONTEXT || {}).user || 'local').trim() || 'local';
}

function _appearanceGlobalStorageKey() {
  return 'pawflow.appearance.v1:' + _appearanceUserId();
}

function _appearanceConversationId() {
  return typeof conversationId !== 'undefined' ? String(conversationId || '') : '';
}

function _appearanceConversationStorageKey() {
  const cid = _appearanceConversationId();
  return cid ? _appearanceGlobalStorageKey() + ':conversation:' + cid : '';
}

function _appearanceStorageKey(scope) {
  const wanted = scope || _appearanceScope;
  return wanted === 'conversation' && _appearanceConversationStorageKey()
    ? _appearanceConversationStorageKey() : _appearanceGlobalStorageKey();
}

function _appearanceBlobKey(scope) {
  const cid = _appearanceConversationId();
  return scope === 'conversation' && cid
    ? _appearanceUserId() + ':conversation:' + cid : _appearanceUserId();
}

function _appearanceClamp(value, min, max, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.min(max, Math.max(min, number)) : fallback;
}

function _appearanceNormalize(raw) {
  const prefs = Object.assign({}, APPEARANCE_DEFAULTS, raw || {});
  prefs.scale = _appearanceClamp(prefs.scale, 75, 150, 100);
  prefs.dim = _appearanceClamp(prefs.dim, 0, 80, 38);
  prefs.blur = _appearanceClamp(prefs.blur, 0, 24, 0);
  prefs.saturation = _appearanceClamp(prefs.saturation, 50, 150, 100);
  prefs.panel = _appearanceClamp(prefs.panel, 55, 100, 88);
  prefs.source = ['none', 'upload', 'url'].includes(prefs.source) ? prefs.source : 'none';
  prefs.kind = prefs.kind === 'video' ? 'video' : 'image';
  prefs.url = typeof prefs.url === 'string' ? prefs.url : '';
  prefs.name = typeof prefs.name === 'string' ? prefs.name : '';
  prefs.motion = !!prefs.motion;
  prefs.version = APPEARANCE_PREFS_VERSION;
  return prefs;
}

function _appearanceLoadPrefs() {
  const conversationKey = _appearanceConversationStorageKey();
  _appearanceScope = conversationKey && localStorage.getItem(conversationKey) !== null
    ? 'conversation' : 'global';
  _appearanceKey = _appearanceStorageKey(_appearanceScope);
  try { _appearancePrefs = _appearanceNormalize(JSON.parse(localStorage.getItem(_appearanceKey) || '{}')); }
  catch (_err) { _appearancePrefs = Object.assign({}, APPEARANCE_DEFAULTS); }
}

async function refreshAppearanceContext() {
  const conversationKey = _appearanceConversationStorageKey();
  const nextScope = conversationKey && localStorage.getItem(conversationKey) !== null
    ? 'conversation' : 'global';
  const nextKey = _appearanceStorageKey(nextScope);
  if (nextKey === _appearanceKey && nextScope === _appearanceScope) return;
  _appearanceSetObjectUrl(null);
  _appearanceLoadPrefs();
  await applyAppearance();
}

async function refreshAppearanceIdentity() {
  _appearanceKey = '';
  await refreshAppearanceContext();
}

function _appearanceSavePrefs() {
  localStorage.setItem(_appearanceKey || _appearanceStorageKey(_appearanceScope), JSON.stringify(_appearancePrefs));
}

function _appearanceDb() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) { reject(new Error('IndexedDB unavailable')); return; }
    const request = indexedDB.open(APPEARANCE_DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(APPEARANCE_DB_STORE)) {
        request.result.createObjectStore(APPEARANCE_DB_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB open failed'));
  });
}

async function _appearanceBlob(action, value, scope) {
  // Resolve the key before opening IndexedDB. A conversation switch can happen
  // while the database request is pending.
  const key = _appearanceBlobKey(scope || _appearanceScope);
  const db = await _appearanceDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(APPEARANCE_DB_STORE, action === 'get' ? 'readonly' : 'readwrite');
    const store = tx.objectStore(APPEARANCE_DB_STORE);
    const request = action === 'get' ? store.get(key)
      : action === 'put' ? store.put(value, key)
      : store.delete(key);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB operation failed'));
    tx.oncomplete = () => db.close();
  });
}

function _appearanceSetObjectUrl(blob) {
  if (_appearanceObjectUrl) URL.revokeObjectURL(_appearanceObjectUrl);
  _appearanceObjectUrl = blob ? URL.createObjectURL(blob) : '';
  return _appearanceObjectUrl;
}

function _appearanceSetMedia(kind, url) {
  const root = document.documentElement;
  const image = document.getElementById('pfAtmosphereImage');
  const video = document.getElementById('pfAtmosphereVideo');
  if (!image || !video) return;
  video.pause();
  video.removeAttribute('src');
  image.style.backgroundImage = 'none';
  root.dataset.pfAtmosphere = url ? 'on' : 'off';
  root.dataset.pfAtmosphereKind = url ? kind : '';
  if (!url) return;
  if (kind === 'video') {
    video.src = url;
    if (!_appearanceReducedMotion() && !document.hidden) video.play().catch(() => {});
  } else {
    image.style.backgroundImage = 'url(' + JSON.stringify(url) + ')';
  }
}

function _appearanceReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

function _appearanceApplyEffects() {
  const root = document.documentElement;
  root.style.setProperty('--pf-atmosphere-dim', String(_appearancePrefs.dim / 100));
  root.style.setProperty('--pf-atmosphere-blur', _appearancePrefs.blur + 'px');
  root.style.setProperty('--pf-atmosphere-saturation', _appearancePrefs.saturation + '%');
  root.style.setProperty('--pf-atmosphere-panel-opacity', _appearancePrefs.panel + '%');
  root.dataset.pfAtmosphereMotion = _appearancePrefs.motion && !_appearanceReducedMotion() ? 'on' : 'off';
  document.body.style.zoom = String(_appearancePrefs.scale / 100);
}

async function applyAppearance() {
  const sequence = ++_appearanceApplySequence;
  _appearanceApplyEffects();
  if (_appearancePrefs.source === 'url' && _appearancePrefs.url) {
    _appearanceSetMedia(_appearancePrefs.kind, _appearancePrefs.url);
  } else if (_appearancePrefs.source === 'upload') {
    try {
      const record = await _appearanceBlob('get');
      if (sequence !== _appearanceApplySequence) return;
      const url = record && record.blob ? _appearanceSetObjectUrl(record.blob) : '';
      _appearanceSetMedia(record && record.kind || _appearancePrefs.kind, url);
    } catch (_err) {
      _appearanceSetMedia('', '');
    }
  } else {
    _appearanceSetMedia('', '');
  }
  _appearanceSyncDialog();
}

function _appearanceSyncDialog() {
  const values = {
    appearanceScale: _appearancePrefs.scale,
    appearanceDim: _appearancePrefs.dim,
    appearanceBlur: _appearancePrefs.blur,
    appearanceSaturation: _appearancePrefs.saturation,
    appearancePanel: _appearancePrefs.panel,
  };
  Object.keys(values).forEach(id => { const el = document.getElementById(id); if (el) el.value = values[id]; });
  const outputs = {
    appearanceScaleValue: _appearancePrefs.scale + '%',
    appearanceDimValue: _appearancePrefs.dim + '%',
    appearanceBlurValue: _appearancePrefs.blur + 'px',
    appearanceSaturationValue: _appearancePrefs.saturation + '%',
    appearancePanelValue: _appearancePrefs.panel + '%',
  };
  Object.keys(outputs).forEach(id => { const el = document.getElementById(id); if (el) el.textContent = outputs[id]; });
  const motion = document.getElementById('appearanceMotion');
  if (motion) motion.checked = _appearancePrefs.motion;
  const kind = document.getElementById('appearanceUrlKind');
  if (kind) kind.value = _appearancePrefs.kind;
  const url = document.getElementById('appearanceUrl');
  if (url) url.value = _appearancePrefs.source === 'url' ? _appearancePrefs.url : '';
  const name = document.getElementById('appearanceMediaName');
  if (name) name.textContent = _appearancePrefs.name || t('backgroundNone');
  const scope = document.getElementById('appearanceScope');
  if (scope) {
    scope.value = _appearanceScope;
    const option = scope.querySelector('option[value="conversation"]');
    if (option) option.disabled = !_appearanceConversationId();
  }
}

function showAppearanceDialog() {
  const dialog = document.getElementById('appearanceDialog');
  if (!dialog) return;
  _appearanceSyncDialog();
  dialog.style.display = 'flex';
  const first = document.getElementById('appearanceScale');
  if (first) first.focus();
}

function closeAppearanceDialog() {
  const dialog = document.getElementById('appearanceDialog');
  if (dialog) dialog.style.display = 'none';
}

async function setAppearanceScope(scope) {
  const cid = _appearanceConversationId();
  if (scope === 'conversation' && cid) {
    if (_appearanceScope === 'conversation') return;
    const inherited = Object.assign({}, _appearancePrefs);
    if (inherited.source === 'upload') {
      try {
        const record = await _appearanceBlob('get', null, 'global');
        if (record) await _appearanceBlob('put', record, 'conversation');
      } catch (_err) {}
    }
    _appearanceScope = 'conversation';
    _appearanceKey = _appearanceStorageKey('conversation');
    _appearancePrefs = inherited;
    _appearanceSavePrefs();
    await applyAppearance();
    return;
  }
  const conversationKey = _appearanceConversationStorageKey();
  if (conversationKey) localStorage.removeItem(conversationKey);
  if (conversationKey) {
    try { await _appearanceBlob('delete', null, 'conversation'); } catch (_err) {}
  }
  _appearanceSetObjectUrl(null);
  _appearanceScope = 'global';
  _appearanceKey = _appearanceGlobalStorageKey();
  try {
    _appearancePrefs = _appearanceNormalize(JSON.parse(localStorage.getItem(_appearanceKey) || '{}'));
  } catch (_err) {
    _appearancePrefs = Object.assign({}, APPEARANCE_DEFAULTS);
  }
  await applyAppearance();
}

function setAppearanceScale(value) {
  _appearancePrefs.scale = _appearanceClamp(value, 75, 150, 100);
  _appearanceSavePrefs();
  _appearanceApplyEffects();
  _appearanceSyncDialog();
}

function stepAppearanceScale(delta) {
  setAppearanceScale(_appearancePrefs.scale + Number(delta || 0));
}

function setAppearanceEffect(name, value) {
  const bounds = { dim: [0, 80, 38], blur: [0, 24, 0], saturation: [50, 150, 100], panel: [55, 100, 88] };
  if (!bounds[name]) return;
  _appearancePrefs[name] = _appearanceClamp(value, bounds[name][0], bounds[name][1], bounds[name][2]);
  _appearanceSavePrefs();
  _appearanceApplyEffects();
  _appearanceSyncDialog();
}

function setAppearanceMotion(enabled) {
  _appearancePrefs.motion = !!enabled;
  _appearanceSavePrefs();
  _appearanceApplyEffects();
}

async function setAppearanceFile(file) {
  if (!file) return;
  if (!/^(image|video)\//.test(file.type || '') || file.size > APPEARANCE_MAX_MEDIA_BYTES) {
    addMsg('error', t('backgroundFileInvalid'));
    return;
  }
  const kind = file.type.indexOf('video/') === 0 ? 'video' : 'image';
  try {
    await _appearanceBlob('put', { blob: file, kind, name: file.name, updatedAt: Date.now() });
    _appearancePrefs.source = 'upload';
    _appearancePrefs.kind = kind;
    _appearancePrefs.url = '';
    _appearancePrefs.name = file.name;
    _appearanceSavePrefs();
    _appearanceSetMedia(kind, _appearanceSetObjectUrl(file));
    _appearanceSyncDialog();
  } catch (error) {
    addMsg('error', t('backgroundStoreFailed', { error: error.message }));
  }
}

function _appearanceValidatedUrl(raw) {
  try {
    const url = new URL(String(raw || '').trim(), window.location.href);
    if (url.protocol !== 'https:' && url.origin !== window.location.origin) return '';
    return url.href;
  } catch (_err) { return ''; }
}

function applyAppearanceUrl() {
  const input = document.getElementById('appearanceUrl');
  const kind = document.getElementById('appearanceUrlKind');
  const url = _appearanceValidatedUrl(input && input.value);
  if (!url) { addMsg('error', t('backgroundUrlInvalid')); return; }
  _appearancePrefs.source = 'url';
  _appearancePrefs.kind = kind && kind.value === 'video' ? 'video' : 'image';
  _appearancePrefs.url = url;
  _appearancePrefs.name = new URL(url).hostname;
  _appearanceSavePrefs();
  _appearanceSetMedia(_appearancePrefs.kind, url);
  _appearanceSyncDialog();
}

async function removeAppearanceBackground() {
  try { await _appearanceBlob('delete'); } catch (_err) {}
  _appearanceSetObjectUrl(null);
  _appearancePrefs.source = 'none';
  _appearancePrefs.url = '';
  _appearancePrefs.name = '';
  _appearanceSavePrefs();
  _appearanceSetMedia('', '');
  _appearanceSyncDialog();
}

async function resetAppearance() {
  try { await _appearanceBlob('delete'); } catch (_err) {}
  _appearanceSetObjectUrl(null);
  _appearancePrefs = Object.assign({}, APPEARANCE_DEFAULTS);
  _appearanceSavePrefs();
  await applyAppearance();
}

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && document.getElementById('appearanceDialog')?.style.display === 'flex') {
    closeAppearanceDialog();
  }
});

document.addEventListener('visibilitychange', () => {
  const video = document.getElementById('pfAtmosphereVideo');
  if (!video || !_appearancePrefs || _appearancePrefs.kind !== 'video') return;
  if (document.hidden || _appearanceReducedMotion()) video.pause();
  else if (_appearancePrefs.source !== 'none') video.play().catch(() => {});
});

window.addEventListener('pawflow:userchange', refreshAppearanceIdentity);

document.addEventListener('DOMContentLoaded', () => {
  _appearanceLoadPrefs();
  applyAppearance();
  const dialog = document.getElementById('appearanceDialog');
  if (dialog) dialog.addEventListener('mousedown', event => {
    if (event.target === dialog) closeAppearanceDialog();
  });
});
