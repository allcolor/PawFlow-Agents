// ── i18n ───────────────────────────────────────────────────────────
const I18N_STORAGE_KEY = 'pawflow.language';
const I18N_BASE_PATH = '/chat/js/i18n/';
let _i18nLanguages = [];
let _i18nFallback = {};
let _i18nCurrent = {};
let _currentLanguage = 'en';
let _i18nChangeId = 0;
const _i18nPending = new Map();

function _embeddedJson(url) {
  const name = String(url || '').split('/').pop() || '';
  if (name === 'languages.json' && Array.isArray(window.PAWFLOW_I18N_LANGUAGES)) {
    return window.PAWFLOW_I18N_LANGUAGES;
  }
  const match = name.match(/^([a-z]+)\.json$/);
  if (match && window.PAWFLOW_I18N_CATALOGS && window.PAWFLOW_I18N_CATALOGS[match[1]]) {
    return window.PAWFLOW_I18N_CATALOGS[match[1]];
  }
  return null;
}

function _normalizeLanguage(lang) {
  return String(lang || '').toLowerCase().replace('_', '-').split('-')[0];
}

function getSupportedLanguages() {
  return _i18nLanguages.slice();
}

function _isSupportedLanguage(lang) {
  const code = _normalizeLanguage(lang);
  return _i18nLanguages.some(l => l.code === code) ? code : '';
}

function _browserLanguage() {
  const langs = Array.isArray(navigator.languages) && navigator.languages.length
    ? navigator.languages : [navigator.language || 'en'];
  for (const lang of langs) {
    const supported = _isSupportedLanguage(lang);
    if (supported) return supported;
  }
  return 'en';
}

function _storedLanguage() {
  try {
    return window.localStorage ? _isSupportedLanguage(window.localStorage.getItem(I18N_STORAGE_KEY)) : '';
  } catch (_err) {
    return '';
  }
}

function _loadLanguageCatalog(lang) {
  const code = _isSupportedLanguage(lang) || 'en';
  const embedded = _embeddedJson(I18N_BASE_PATH + code + '.json');
  if (embedded !== null) return Promise.resolve(embedded);
  if (_i18nPending.has(code)) return _i18nPending.get(code);
  const url = window.PAWFLOW_I18N_URLS[code];
  const pending = fetch(url).then(response => {
    if (!response.ok) throw new Error('Failed to load ' + url);
    return response.json();
  }).then(catalog => {
    if (!catalog || Array.isArray(catalog) || typeof catalog !== 'object'
        || !Object.keys(catalog).length) throw new Error('Invalid catalog ' + code);
    window.PAWFLOW_I18N_CATALOGS[code] = catalog;
    return catalog;
  }).finally(() => _i18nPending.delete(code));
  _i18nPending.set(code, pending);
  return pending;
}

function _builtinEnglishCatalog() {
  return {
    languageTitle: 'Language', languageEn: 'English', languageFr: 'French', languageEs: 'Spanish',
    pageTitle: 'PawFlow Agent Chat', ready: 'Ready', send: 'Send', logout: 'Logout',
    placeholder: 'Type a message... (Enter to send, Shift+Enter for newline)',
    placeholderMobile: 'Type a message... (Enter for newline, tap Send to send)',
    placeholderDisabled: 'Create a conversation first (click + New)',
    conversations: 'Conversations', newChat: '+ New', resources: 'Resources',
  };
}

function _initI18n() {
  _i18nLanguages = (_embeddedJson(I18N_BASE_PATH + 'languages.json')
    || [{ code: 'en', label: 'English', native_label: 'English' }]).slice();
  if (!_isSupportedLanguage('en')) _i18nLanguages.unshift({ code: 'en', label: 'English', native_label: 'English' });
  window.PAWFLOW_I18N_CATALOGS = window.PAWFLOW_I18N_CATALOGS || {};
  _i18nFallback = window.PAWFLOW_I18N_CATALOGS.en || {};
  if (!Object.keys(_i18nFallback).length) _i18nFallback = _builtinEnglishCatalog();
  const desired = _storedLanguage()
    || _isSupportedLanguage(window.PAWFLOW_I18N_LANGUAGE) || _browserLanguage();
  _currentLanguage = window.PAWFLOW_I18N_CATALOGS[desired] ? desired : 'en';
  _i18nCurrent = window.PAWFLOW_I18N_CATALOGS[_currentLanguage] || _i18nFallback;
  document.documentElement.lang = _currentLanguage;
  _storeLanguageCookie(_currentLanguage);
  if (desired === _currentLanguage) return Promise.resolve(true);
  // Old localStorage preferences may differ from the server's cookie/header.
  // Fetch immediately, but refresh app surfaces only after their scripts ran.
  const changeId = _i18nChangeId;
  const catalog = _loadLanguageCatalog(desired).catch(err => {
    console.warn('[i18n] Failed to load catalog', desired, err);
    return null;
  });
  return new Promise(resolve => {
    const apply = () => catalog.then(value => resolve(
      value && changeId === _i18nChangeId ? setLanguage(desired) : false));
    if (document.readyState === 'complete') apply();
    else document.addEventListener('DOMContentLoaded', apply, { once: true });
  });
}

function _storeLanguageCookie(code) {
  document.cookie = 'pawflow_language=' + encodeURIComponent(code)
    + '; Path=/; Max-Age=31536000; SameSite=Lax';
}

function getLanguage() {
  return _currentLanguage;
}

function t(key, vars) {
  let s = _i18nCurrent[key] || _i18nFallback[key] || key;
  if (vars) Object.keys(vars).forEach(k => { s = s.split('{' + k + '}').join(vars[k]); });
  return s;
}

function _setText(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.textContent = value;
}

function _setTitle(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.title = value;
}

function _setPlaceholder(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.placeholder = value;
}

/** The narrow layout uses a touch-first composer: Enter edits, Send submits. */
function composerEnterCreatesNewline() {
  try {
    return !!(window.matchMedia
      && window.matchMedia('(max-width: 768px)').matches);
  } catch (_err) {
    return Number(window.innerWidth || 0) <= 768;
  }
}

function _setComposerPlaceholder() {
  _setPlaceholder(
    '#input',
    t(composerEnterCreatesNewline() ? 'placeholderMobile' : 'placeholder'));
}

function _applyGenericI18n(root) {
  const scope = root || document;
  scope.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  scope.querySelectorAll('[data-i18n-title]').forEach(el => { el.title = t(el.dataset.i18nTitle); });
  scope.querySelectorAll('[data-i18n-placeholder]').forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  scope.querySelectorAll('[data-i18n-aria-label]').forEach(el => { el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel)); });
  scope.querySelectorAll('[data-i18n-permission]').forEach(el => {
    const icon = (el.textContent || '').trim().split(/\s+/, 1)[0] || '';
    el.textContent = (icon ? icon + ' ' : '') + t(el.dataset.i18nPermission);
  });
}

function _renderLanguageSelect() {
  const select = document.getElementById('languageSelect');
  if (!select) return;
  const signature = _i18nLanguages.map(lang => lang.code + ':' + (lang.flag || '') + ':' + (lang.native_label || lang.label || '')).join('|');
  if (select.dataset.i18nSignature !== signature) {
    select.replaceChildren();
    _i18nLanguages.forEach(lang => {
      const key = 'language' + lang.code.charAt(0).toUpperCase() + lang.code.slice(1);
      const opt = document.createElement('option');
      opt.value = lang.code;
      opt.textContent = (lang.flag ? lang.flag + ' ' : '') + (t(key) || lang.native_label || lang.label || lang.code);
      select.appendChild(opt);
    });
    select.dataset.i18nSignature = signature;
  } else {
    Array.from(select.options).forEach(opt => {
      const key = 'language' + opt.value.charAt(0).toUpperCase() + opt.value.slice(1);
      const lang = _i18nLanguages.find(item => item.code === opt.value) || {};
      opt.textContent = (lang.flag ? lang.flag + ' ' : '') + (t(key) || lang.native_label || lang.label || opt.value);
    });
  }
  select.value = _currentLanguage;
  select.setAttribute('aria-label', t('languageTitle'));
  select.style.display = '';
  const control = document.getElementById('languageSelectControl');
  if (control) control.style.display = 'flex';
}

function applyI18n(root) {
  document.documentElement.lang = _currentLanguage;
  document.title = t('pageTitle');
  _setText('#status', t('ready'));
  _setTitle('.btn-attach', t('promptLibraryTitle'));
  _setTitle('#fileAttachBtn', t('attachTitle'));
  _setTitle('#permissionModeBtn', t('permissionModeTitle'));
  _setText('.sidebar-header h2', t('conversations'));
  _setText('.btn-new', t('newChat'));
  _setText('#ttlLabel', t('ttlLabel'));
  if (typeof _themeSyncAppearanceSelector === 'function') _themeSyncAppearanceSelector();
  const ttl = document.getElementById('ttlSelect');
  if (ttl && ttl.options.length >= 5) {
    ttl.options[0].textContent = t('ttlNone');
    ttl.options[1].textContent = t('ttl1h');
    ttl.options[2].textContent = t('ttl6h');
    ttl.options[3].textContent = t('ttl24h');
    ttl.options[4].textContent = t('ttl7d');
  }
  _renderLanguageSelect();
  _applyGenericI18n(root || document);
  // Generic data-i18n processing applies the desktop placeholder first.
  // Override it last when the viewport uses the mobile composer contract.
  _setComposerPlaceholder();
}

async function setLanguage(lang) {
  const code = _isSupportedLanguage(lang);
  if (!code) return false;
  const changeId = ++_i18nChangeId;
  if (code === _currentLanguage) {
    _storeLanguageCookie(code);
    try { if (window.localStorage) window.localStorage.setItem(I18N_STORAGE_KEY, code); } catch (_err) {}
    _renderLanguageSelect();
    return true;
  }
  let catalog;
  try {
    catalog = code === 'en' ? _i18nFallback : await _loadLanguageCatalog(code);
  } catch (err) {
    console.warn('[i18n] Failed to load catalog', code, err);
    if (changeId === _i18nChangeId) _renderLanguageSelect();
    return false;
  }
  if (changeId !== _i18nChangeId) return false;
  _currentLanguage = code;
  _i18nCurrent = catalog;
  _storeLanguageCookie(code);
  try { if (window.localStorage) window.localStorage.setItem(I18N_STORAGE_KEY, code); } catch (_err) {}
  applyI18n(document);
  if (typeof updateTechnicalGroupingToggle === 'function') updateTechnicalGroupingToggle(window.PAWFLOW_GROUP_TECHNICAL_MESSAGES);
  if (typeof loadResources === 'function') loadResources();
  window.dispatchEvent(new CustomEvent('pawflow:languagechange', { detail: { language: code } }));
  return true;
}

window.PAWFLOW_I18N_READY = _initI18n();
window.addEventListener('resize', _setComposerPlaceholder);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => applyI18n(document));
} else {
  applyI18n(document);
}

// App state variables are in state.js
