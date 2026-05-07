// ── i18n ───────────────────────────────────────────────────────────
const I18N_STORAGE_KEY = 'pawflow.language';
const I18N_BASE_PATH = '/chat/js/i18n/';
let _i18nLanguages = [];
let _i18nFallback = {};
let _i18nCurrent = {};
let _currentLanguage = 'en';

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

function _readJsonSync(url) {
  const embedded = _embeddedJson(url);
  if (embedded !== null) return embedded;
  const xhr = new XMLHttpRequest();
  const version = window.PAWFLOW_ASSET_VERSION ? '?v=' + encodeURIComponent(window.PAWFLOW_ASSET_VERSION) : '';
  xhr.open('GET', url + version, false);
  xhr.send(null);
  if (xhr.status < 200 || xhr.status >= 300) throw new Error('Failed to load ' + url);
  return JSON.parse(xhr.responseText || '{}');
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
  return _readJsonSync(I18N_BASE_PATH + code + '.json');
}

function _safeLoadLanguageCatalog(lang) {
  try {
    return _loadLanguageCatalog(lang);
  } catch (err) {
    console.warn('[i18n] Failed to load catalog', lang, err);
    return {};
  }
}

function _builtinEnglishCatalog() {
  return {
    languageTitle: 'Language', languageEn: 'English', languageFr: 'French', languageEs: 'Spanish',
    pageTitle: 'PawFlow Agent Chat', ready: 'Ready', send: 'Send', logout: 'Logout',
    placeholder: 'Type a message... (Enter to send, Shift+Enter for newline)',
    conversations: 'Conversations', newChat: '+ New', resources: 'Resources',
  };
}

function _initI18n() {
  try {
    _i18nLanguages = _readJsonSync(I18N_BASE_PATH + 'languages.json');
  } catch (_err) {
    _i18nLanguages = [{ code: 'en', label: 'English', native_label: 'English' }];
  }
  if (!_isSupportedLanguage('en')) _i18nLanguages.unshift({ code: 'en', label: 'English', native_label: 'English' });
  _i18nFallback = _safeLoadLanguageCatalog('en');
  if (!Object.keys(_i18nFallback).length) _i18nFallback = _builtinEnglishCatalog();
  _currentLanguage = _storedLanguage() || _browserLanguage();
  _i18nCurrent = _currentLanguage === 'en' ? _i18nFallback : _safeLoadLanguageCatalog(_currentLanguage);
  if (!Object.keys(_i18nCurrent).length) _i18nCurrent = _i18nFallback;
  document.documentElement.lang = _currentLanguage;
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
  const signature = _i18nLanguages.map(lang => lang.code + ':' + (lang.native_label || lang.label || '')).join('|');
  if (select.dataset.i18nSignature !== signature) {
    select.replaceChildren();
    _i18nLanguages.forEach(lang => {
      const key = 'language' + lang.code.charAt(0).toUpperCase() + lang.code.slice(1);
      const opt = document.createElement('option');
      opt.value = lang.code;
      opt.textContent = t(key) || lang.native_label || lang.label || lang.code;
      select.appendChild(opt);
    });
    select.dataset.i18nSignature = signature;
  } else {
    Array.from(select.options).forEach(opt => {
      const key = 'language' + opt.value.charAt(0).toUpperCase() + opt.value.slice(1);
      opt.textContent = t(key) || opt.textContent;
    });
  }
  select.value = _currentLanguage;
  select.title = t('languageTitle');
  select.setAttribute('aria-label', t('languageTitle'));
  select.style.display = 'inline-flex';
}

function applyI18n(root) {
  document.documentElement.lang = _currentLanguage;
  document.title = t('pageTitle');
  _setText('#status', t('ready'));
  _setText('#sendBtn', t('send'));
  _setText('#logoutBtn', t('logout'));
  _setPlaceholder('#input', t('placeholder'));
  _setTitle('.btn-attach', t('promptLibraryTitle'));
  _setTitle('#fileAttachBtn', t('attachTitle'));
  _setTitle('#stopBtn', t('stopTitle'));
  _setTitle('#themeSelect', t('globalThemeTitle'));
  _setTitle('#conversationThemeSelect', t('convThemeLabel'));
  _setTitle('#technicalGroupingToggle', t('groupTechnicalDisabledTitle'));
  _setTitle('#permissionMode', t('permissionModeTitle'));
  _setText('.sidebar-header h2', t('conversations'));
  _setText('.btn-new', t('newChat'));
  _setText('#ttlLabel', t('ttlLabel'));
  _setText('#convThemeLabel', t('convThemeLabel'));
  const convTheme = document.getElementById('conversationThemeSelect');
  if (convTheme && convTheme.options.length) convTheme.options[0].textContent = t('useGlobalTheme');
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
}

function setLanguage(lang) {
  const code = _isSupportedLanguage(lang);
  if (!code || code === _currentLanguage) return;
  _currentLanguage = code;
  _i18nCurrent = code === 'en' ? _i18nFallback : _safeLoadLanguageCatalog(code);
  if (!Object.keys(_i18nCurrent).length) _i18nCurrent = _i18nFallback;
  try { if (window.localStorage) window.localStorage.setItem(I18N_STORAGE_KEY, code); } catch (_err) {}
  applyI18n(document);
  if (typeof updateTechnicalGroupingToggle === 'function') updateTechnicalGroupingToggle(window.PAWFLOW_GROUP_TECHNICAL_MESSAGES);
  if (typeof loadResources === 'function') loadResources();
  window.dispatchEvent(new CustomEvent('pawflow:languagechange', { detail: { language: code } }));
}

_initI18n();
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => applyI18n(document));
} else {
  applyI18n(document);
}

// App state variables are in state.js
