// -- Chat themes --------------------------------------------------------------

const THEME_COOKIE = 'pawflow_theme_ref';
const CONV_THEME_COOKIE = 'pawflow_conv_theme_refs';
const DEFAULT_THEME_REF = 'global:pawflow_dark';

let _themeLoadSeq = 0;
let _activeThemeRef = window.PAWFLOW_INITIAL_THEME_REF || '';
let _activeThemeContext = '';

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

function _themeGetConversationMap() {
  const raw = _themeGetCookie(CONV_THEME_COOKIE);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_) {
    return {};
  }
}

function _themeSetConversationMap(map) {
  _themeSetCookie(CONV_THEME_COOKIE, JSON.stringify(map || {}));
}

function _themeGetConversationRef(cid) {
  if (!cid) return '';
  const map = _themeGetConversationMap();
  return typeof map[cid] === 'string' ? map[cid] : '';
}

function _themeSetConversationRef(cid, ref) {
  if (!cid) return;
  const map = _themeGetConversationMap();
  if (ref) map[cid] = ref;
  else delete map[cid];
  _themeSetConversationMap(map);
}

function applyThemeCss(css) {
  let themeEl = document.getElementById('custom-theme');
  if (!themeEl) {
    themeEl = document.createElement('style');
    themeEl.id = 'custom-theme';
    document.head.appendChild(themeEl);
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

async function loadThemeSelector() {
  const seq = ++_themeLoadSeq;
  const globalSel = document.getElementById('themeSelect');
  const convSel = document.getElementById('conversationThemeSelect');
  if (!globalSel && !convSel) return;

  try {
    const data = await rxjs.firstValueFrom(action$('list_chat_themes', {
      conversation_id: conversationId || '',
    }));
    if (seq !== _themeLoadSeq) return;
    if (data.error) { addMsg('error', data.error); return; }

    const themes = data.themes || [];
    window._chatThemes = themes;
    const allRefs = new Set(themes.map(t => t.ref));
    const globalThemes = themes.filter(t => t.scope !== 'conversation');
    const globalRefs = new Set(globalThemes.map(t => t.ref));

    let globalRef = _themeValidRef(_themeGetGlobalRef(), globalRefs, DEFAULT_THEME_REF);
    if (globalRef !== _themeGetGlobalRef()) _themeSetGlobalRef(globalRef);

    let convRef = conversationId ? _themeGetConversationRef(conversationId) : '';
    if (convRef && !allRefs.has(convRef)) {
      _themeSetConversationRef(conversationId, '');
      convRef = '';
    }

    if (globalSel) {
      globalSel.innerHTML = globalThemes.map(_themeOption).join('');
      globalSel.value = globalRef;
      globalSel.style.display = '';
    }

    if (convSel) {
      convSel.innerHTML = '<option value="">' + escapeHtml(t('useGlobalTheme')) + '</option>'
        + themes.map(_themeOption).join('');
      convSel.value = convRef || '';
      convSel.style.display = conversationId ? '' : 'none';
      const label = document.getElementById('convThemeLabel');
      if (label) label.style.display = conversationId ? '' : 'none';
    }

    const effectiveRef = convRef || globalRef;
    await applyThemeRef(effectiveRef, false);
  } catch (e) {
    addMsg('error', 'Theme load failed: ' + e.message);
  }
}

async function applyThemeRef(ref, force) {
  const nextRef = ref || DEFAULT_THEME_REF;
  const contextKey = nextRef.indexOf('conversation:') === 0 ? (conversationId || '') : '';
  if (!force && _activeThemeRef === nextRef && _activeThemeContext === contextKey
      && document.getElementById('custom-theme')) return;
  const res = await rxjs.firstValueFrom(action$('apply_chat_theme', {
    conversation_id: conversationId || '',
    theme_ref: nextRef,
    conversation_override: false,
  }));
  if (res.error) { addMsg('error', res.error); return; }
  _activeThemeRef = res.theme_ref || nextRef;
  _activeThemeContext = contextKey;
  applyThemeCss(res.css || '');
}

function onGlobalThemeSelectChange(value) {
  _themeSetGlobalRef(value || DEFAULT_THEME_REF);
  loadThemeSelector()
    .then(() => loadResources())
    .catch(e => addMsg('error', 'Theme apply failed: ' + e.message));
}

function onConversationThemeSelectChange(value) {
  if (!conversationId) return;
  _themeSetConversationRef(conversationId, value || '');
  loadThemeSelector()
    .then(() => loadResources())
    .catch(e => addMsg('error', 'Theme apply failed: ' + e.message));
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
  item(ref.indexOf('conversation:') === 0 ? 'Apply to conversation' : 'Apply globally', () => _applyThemeFromResource(ref));
  if (!builtin) {
    item('Delete', () => _deleteTheme(ref), true);
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
    + '<h3 style="margin:0;font-size:14px;">Add theme</h3>'
    + '<button onclick="document.getElementById(\'themeCreatorOverlay\').remove()" style="background:none;border:none;cursor:pointer;font-size:18px;">&times;</button></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">Name</label><input id="theme-name" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">Title</label><input id="theme-title" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">Scope</label><select id="theme-scope" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;">'
    + (_isAdmin && _isAdmin() ? '<option value="global">Global</option>' : '')
    + '<option value="user">User</option><option value="conversation">Conversation</option></select></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">CSS or ZIP package</label><input id="theme-file" type="file" accept=".css,.zip,text/css,application/zip" style="width:100%;margin-top:4px;"/></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">CSS override</label><textarea id="theme-css" style="width:100%;min-height:160px;padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;resize:vertical;"></textarea></div>'
    + '<div style="margin-bottom:8px;"><label style="font-size:11px;">Description</label><input id="theme-description" style="width:100%;padding:6px;border-radius:4px;margin-top:2px;"/></div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'themeCreatorOverlay\').remove()" style="padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button>'
    + '<button onclick="_saveThemeCreate()" style="padding:8px 16px;border-radius:4px;cursor:pointer;">Create</button></div>';
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
  addMsg('system', 'Theme created.');
  loadThemeSelector();
  loadResources();
}

function _deleteTheme(ref) {
  if (!confirm('Delete theme ' + ref + '?')) return;
  action$('delete_chat_theme', { conversation_id: conversationId, theme_ref: ref }).subscribe(res => {
    if (res.error) { addMsg('error', res.error); return; }
    if (conversationId && _themeGetConversationRef(conversationId) === ref) {
      _themeSetConversationRef(conversationId, '');
    }
    if (_themeGetGlobalRef() === ref) _themeSetGlobalRef(DEFAULT_THEME_REF);
    addMsg('system', 'Theme deleted.');
    loadThemeSelector();
    loadResources();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  loadThemeSelector();
});
