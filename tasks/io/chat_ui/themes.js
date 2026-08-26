// -- Chat themes --------------------------------------------------------------

const THEME_COOKIE = 'pawflow_theme_ref';
const DEFAULT_THEME_REF = 'global:pawflow_dark';

let _themeLoadSeq = 0;
let _themeApplySeq = 0;
let _activeThemeRef = window.PAWFLOW_INITIAL_THEME_REF || '';
let _activeThemeContext = '';
let _conversationThemeRef = '';

function _themeGetCookie(name) {
  const m = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]+)'));
  return m ? decodeURIComponent(m[1]) : '';
}

function _themeSetCookie(name, value) {
  document.cookie = name + '=' + encodeURIComponent(value || '')
    + ';path=/;max-age=31536000;samesite=lax';
}

function _themeGetGlobalRef() {
  const ref = _themeGetCookie(THEME_COOKIE) || DEFAULT_THEME_REF;
  return ref.indexOf('builtin:') === 0 ? 'global:' + ref.split(':')[1] : ref;
}

function _themeSetGlobalRef(ref) {
  _themeSetCookie(THEME_COOKIE, ref || DEFAULT_THEME_REF);
}

function applyThemeCss(css) {
  let themeEl = document.getElementById('custom-theme');
  if (!themeEl) {
    themeEl = document.createElement('style');
    themeEl.id = 'custom-theme';
    const contract = document.getElementById('component-contract-css');
    document.head.insertBefore(themeEl, contract || null);
  }
  themeEl.textContent = css || '';
}

function _themeLabel(t) {
  const scope = t.scope === 'builtin' ? 'built-in' : t.scope;
  return (t.title || t.name || t.ref) + ' (' + scope + ')';
}

function _themeOption(t) {
  return '<option value="' + escapeHtml(t.ref) + '">' + escapeHtml(_themeLabel(t)) + '</option>';
}

function _themeValidRef(ref, refs, fallback) {
  return refs.has(ref) ? ref : fallback;
}

function _themeSyncAppearanceSelector() {
  const select = document.getElementById('appearanceThemeSelect');
  if (!select) return;
  const themes = window._chatThemes || [];
  const conversationScope = typeof _appearanceScope !== 'undefined'
    && _appearanceScope === 'conversation' && !!conversationId;
  const available = conversationScope
    ? themes : themes.filter(theme => theme.scope !== 'conversation');
  select.innerHTML = (conversationScope
    ? '<option value="">' + escapeHtml(t('useGlobalTheme')) + '</option>' : '')
    + available.map(_themeOption).join('');
  select.value = conversationScope ? (_conversationThemeRef || '') : _themeGetGlobalRef();
  const label = document.getElementById('appearanceThemeLabel');
  if (label) label.textContent = t(conversationScope ? 'convThemeLabel' : 'globalThemeTitle');
}

async function loadThemeSelector() {
  const seq = ++_themeLoadSeq;
  const requestedConversationId = conversationId || '';
  const appearanceSelect = document.getElementById('appearanceThemeSelect');
  if (!appearanceSelect) return;

  try {
    const data = await rxjs.firstValueFrom(action$('list_chat_themes', {
      conversation_id: requestedConversationId,
    }));
    if (seq !== _themeLoadSeq || requestedConversationId !== (conversationId || '')) return;
    if (data.error) { addMsg('error', data.error); return; }

    const themes = data.themes || [];
    window._chatThemes = themes;
    const allRefs = new Set(themes.map(t => t.ref));
    const globalThemes = themes.filter(t => t.scope !== 'conversation');
    const globalRefs = new Set(globalThemes.map(t => t.ref));

    let globalRef = _themeValidRef(_themeGetGlobalRef(), globalRefs, DEFAULT_THEME_REF);
    if (globalRef !== _themeGetGlobalRef()) _themeSetGlobalRef(globalRef);

    let convRef = requestedConversationId && typeof data.conversation_theme_ref === 'string'
      ? data.conversation_theme_ref : '';
    if (convRef && !allRefs.has(convRef)) {
      convRef = '';
    }
    _conversationThemeRef = convRef;
    _themeSyncAppearanceSelector();

    const effectiveRef = convRef || globalRef;
    await applyThemeRef(effectiveRef, false, !!convRef, requestedConversationId);
  } catch (e) {
    addMsg('error', t('themeLoadFailed', { error: e.message }));
  }
}

async function applyThemeRef(ref, force, conversationOverride, requestedConversationId) {
  const targetConversationId = requestedConversationId === undefined
    ? (conversationId || '') : requestedConversationId;
  const applySeq = ++_themeApplySeq;
  const nextRef = ref || DEFAULT_THEME_REF;
  const contextKey = nextRef.indexOf('conversation:') === 0 ? targetConversationId : '';
  if (!force && _activeThemeRef === nextRef && _activeThemeContext === contextKey
      && document.getElementById('custom-theme')) return;
  const res = await rxjs.firstValueFrom(action$('apply_chat_theme', {
    conversation_id: targetConversationId,
    theme_ref: nextRef,
    conversation_override: !!conversationOverride,
  }));
  if (applySeq !== _themeApplySeq
      || targetConversationId !== (conversationId || '')) return;
  if (res.error) { addMsg('error', res.error); return; }
  _activeThemeRef = res.theme_ref || nextRef;
  _activeThemeContext = contextKey;
  applyThemeCss(res.css || '');
  if (window._pawflowExtRuntime) {
    window._pawflowExtRuntime.fireHook('theme_changed', { themeRef: _activeThemeRef });
  }
}

function onGlobalThemeSelectChange(value) {
  _themeSetGlobalRef(value || DEFAULT_THEME_REF);
  loadThemeSelector()
    .then(() => loadResources())
    .catch(e => addMsg('error', t('themeApplyFailed', { error: e.message })));
}

function onConversationThemeSelectChange(value) {
  if (!conversationId) return;
  _conversationThemeRef = value || '';
  _themeSyncAppearanceSelector();
  applyThemeRef(value || _themeGetGlobalRef(), true, !!value)
    .then(() => loadResources())
    .catch(e => addMsg('error', t('themeApplyFailed', { error: e.message })));
}

function onAppearanceThemeSelectChange(value) {
  const conversationScope = typeof _appearanceScope !== 'undefined'
    && _appearanceScope === 'conversation' && !!conversationId;
  if (conversationScope) onConversationThemeSelectChange(value);
  else onGlobalThemeSelectChange(value);
}

function _applyThemeFromResource(ref) {
  if (!ref) return;
  if (ref.indexOf('conversation:') === 0) onConversationThemeSelectChange(ref);
  else onGlobalThemeSelectChange(ref);
}

function _showThemeMenu(e, ref, builtin, scope) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;min-width:160px;';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.className = 'ctx-menu-item' + (danger ? ' danger' : '');
    d.textContent = label;
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  item(ref.indexOf('conversation:') === 0 ? t('applyToConversation') : t('applyGlobally'), () => _applyThemeFromResource(ref));
  if (!builtin) {
    item(t('delete'), () => _deleteTheme(ref), true);
  }
  document.body.appendChild(menu);
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) menu.style.top = Math.max(0, e.clientY - rect.height) + 'px';
    if (rect.right > window.innerWidth) menu.style.left = Math.max(0, e.clientX - rect.width) + 'px';
  });
  setTimeout(() => document.addEventListener('click', function _close() { menu.remove(); document.removeEventListener('click', _close); }), 0);
}

function showThemeCreator() {
  let overlay = document.getElementById('themeCreatorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'themeCreatorOverlay';
  overlay.className = 'dialog-bg';
  const panel = document.createElement('div');
  panel.className = 'exec-dialog';
  panel.style.width = '560px';
  panel.style.maxHeight = '85vh';
  panel.style.overflowY = 'auto';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;font-size:14px;">' + escapeHtml(t('addTheme')) + '</h3>'
    + '<button onclick="document.getElementById(\'themeCreatorOverlay\').remove()" style="background:none;border:none;cursor:pointer;font-size:18px;">&times;</button></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">' + escapeHtml(t('name')) + '</label><input id="theme-name" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">' + escapeHtml(t('title')) + '</label><input id="theme-title" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">' + escapeHtml(t('scope')) + '</label><select id="theme-scope" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;">'
    + (_isAdmin && _isAdmin() ? '<option value="global">' + escapeHtml(t('global')) + '</option>' : '')
    + '<option value="user">' + escapeHtml(t('user')) + '</option><option value="conversation">' + escapeHtml(t('conversation')) + '</option></select></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">' + escapeHtml(t('cssOrZipPackage')) + '</label><input id="theme-file" type="file" accept=".css,.zip,text/css,application/zip" style="width:100%;margin-top:4px;"/></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">' + escapeHtml(t('cssOverride')) + '</label><textarea id="theme-css" style="width:100%;min-height:160px;padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;resize:vertical;"></textarea></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">' + escapeHtml(t('description')) + '</label><input id="theme-description" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'themeCreatorOverlay\').remove()" style="padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('cancel')) + '</button>'
    + '<button onclick="_saveThemeCreate()" style="padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('create')) + '</button></div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _readThemeUpload() {
  const file = (document.getElementById('theme-file') || {}).files?.[0];
  if (!file) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ filename: file.name, mime_type: file.type || '', base64: String(reader.result || '') });
    reader.onerror = () => reject(reader.error || new Error('file read failed'));
    reader.readAsDataURL(file);
  });
}

async function _saveThemeCreate() {
  const upload = await _readThemeUpload();
  const name = (document.getElementById('theme-name').value || '').trim();
  const scope = document.getElementById('theme-scope').value || 'user';
  const res = await rxjs.firstValueFrom(action$('create_chat_theme', {
    conversation_id: conversationId,
    name,
    title: (document.getElementById('theme-title').value || '').trim(),
    scope,
    css: document.getElementById('theme-css').value || '',
    description: (document.getElementById('theme-description').value || '').trim(),
    upload,
  }));
  if (res.error) { addMsg('error', res.error); return; }
  document.getElementById('themeCreatorOverlay').remove();
  addMsg('system', t('themeCreated'));
  loadThemeSelector();
  loadResources();
}

function _deleteTheme(ref) {
  if (!confirm(t('deleteThemeConfirm', { theme: ref }))) return;
  action$('delete_chat_theme', { conversation_id: conversationId, theme_ref: ref }).subscribe(res => {
    if (res.error) { addMsg('error', res.error); return; }
    if (_themeGetGlobalRef() === ref) _themeSetGlobalRef(DEFAULT_THEME_REF);
    addMsg('system', t('themeDeleted'));
    loadThemeSelector();
    loadResources();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  loadThemeSelector();
});
