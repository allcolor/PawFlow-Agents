// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

// ── Resources (services, flows) ──────────────────────────────────
// Canonical service lister. Pass a `serviceType` filter (e.g. 'llmConnection',
// 'tool_relay_service') to get a subset. This is the ONLY way the UI should
// fetch services — never through agent/resource actions.
function listServices$(serviceType, withView) {
  let payload = serviceType ? { service_type: serviceType } : {};
  if (typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  // Only the Services PANEL opts into admin view-all; service pickers
  // (LLM dropdowns, relay selectors) must stay scoped to the caller.
  if (withView) payload = _withView(payload);
  return action$('list_services', payload);
}

function notifyServiceConfigurationChanged() {
  if (typeof refreshConversationTTSServices === 'function') {
    try { refreshConversationTTSServices(); } catch (_err) {}
  }
}

function cmdServiceList() {
  listServices$().subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    const svcs = data.services || [];
    if (!svcs.length) { addMsg('system', t('noServicesInstalledUsage')); return; }
    let lines = [t('yourServicesHeader')];
    svcs.forEach(s => {
      const icon = !s.enabled ? '\u{1F534}' : s.started ? '\u{1F7E2}' : '\u{1F7E1}';
      let tag = '';
      if (s.relay_info && s.relay_info.containerized) {
        const img = s.relay_info.docker_image;
        tag = ' \u{1F433}' + (img ? ` [${img}]` : ' [container]');
      }
      lines.push(`  ${icon} **${s.service_id}** (\`${s.service_type}\`)${tag} ${s.description || ''}`);
    });
    addMsg('system', lines.join('\n'));
  });
}

function cmdServiceAction(action, extra) {
  return rxjs.firstValueFrom(action$(action, { ...extra }).pipe(
    rxjs.tap(data => {
      if (data.error) { addMsg('error', data.error); return; }
      notifyServiceConfigurationChanged();
      if (data.installed) addMsg('system', t('serviceInstalled', { id: data.id, type: data.type }));
      else if (data.uninstalled) addMsg('system', t('serviceUninstalled', { id: data.id }));
      else if (data.enabled) addMsg('system', t('serviceEnabled', { id: data.id }));
      else if (data.disabled) addMsg('system', t('serviceDisabled', { id: data.id }));
      else addMsg('system', JSON.stringify(data, null, 2));
    })
  ));
}

// Conversation-scoped payload so conversation-scoped skills/agents are included.
function _convScope(extra) {
  const p = { ...(extra || {}) };
  if (typeof conversationId !== 'undefined' && conversationId) p.conversation_id = conversationId;
  return p;
}

function cmdSkillList() {
  action$('list_skills', _convScope()).subscribe(data => {
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', t('noSkillsUsage')); return; }
    let lines = [t('yourSkillsHeader')];
    skills.forEach(s => {
      if (s.invalid) {
        lines.push(`⚠ **${s.name}** — invalid: ${s.invalid}`);
        return;
      }
      const mark = s.active ? '✅' : '⬜';
      lines.push(`${mark} **${s.name}** — ${s.description || s.preview || ''}`);
    });
    addMsg('system', lines.join('\n'));
  });
}

function cmdListResources() {
  action$('list_resources', {}).subscribe(data => {
    let lines = [];
    if (data.agents && data.agents.length) {
      lines.push(t('agentsHeader'));
      data.agents.forEach(a => {
        const mark = a.active ? '✅' : '⬜';
        lines.push(`  ${mark} ${a.name} ${a.description ? '— ' + a.description : ''}`);
      });
    }
    if (data.skills && data.skills.length) {
      lines.push(t('skillsHeader'));
      data.skills.forEach(s => {
        const mark = s.active ? '✅' : '⬜';
        lines.push(`  ${mark} ${s.name} ${s.description ? '— ' + s.description : ''}`);
      });
    }
    if (data.mcp_servers && data.mcp_servers.length) {
      lines.push(t('mcpServersHeader'));
      data.mcp_servers.forEach(m => {
        const mark = m.active ? '✅' : '⬜';
        lines.push(`  ${mark} ${m.name} (${m.url})`);
      });
    }
    if (!lines.length) lines.push(t('noResourcesDefinedUsage'));
    addMsg('system', lines.join('\n'));
  });
}

// ── Sidebar Resources ───────────────────────────────────────────
function _scopeBadge(s) {
  if (!s) return '';
  const colors = { global: 'var(--pf-accent-2)', user: 'var(--pf-accent)', conversation: 'var(--pf-success)', conv: 'var(--pf-success)' };
  const labels = { global: 'G', user: 'U', conversation: 'C', conv: 'C' };
  return `<span style="font-size:9px;padding:0 3px;border-radius:3px;background:${colors[s]||'var(--pf-border)'};color:var(--pf-text);margin-right:3px;" title="${s}">${labels[s]||s[0]}</span>`;
}

// ── Admin cross-user view-all + owner labelling ──────────────────
// Admin-only: switch the resource/service/flow listings to a cross-user
// "view all" mode (sends view='all'); rows then carry owner_id/owner_display
// (+ conv_id/conv_title) which _ownerBadge renders. Strictly additive: a
// non-admin never sees the toggle and never sends the flag.
window._scopeViewAll = window._scopeViewAll || false;
function _viewAllActive() {
  return (typeof _isAdmin === 'function' && _isAdmin()) && !!window._scopeViewAll;
}
function _withView(payload) {
  const p = Object.assign({}, payload || {});
  if (_viewAllActive()) p.view = 'all';
  return p;
}
function _toggleScopeViewAll() {
  window._scopeViewAll = !window._scopeViewAll;
  if (typeof loadResources === 'function') loadResources();
}
function _viewAllBarHtml() {
  if (typeof _isAdmin !== 'function' || !_isAdmin()) return '';
  const on = !!window._scopeViewAll;
  return '<div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;margin-bottom:6px;padding:0 2px;">'
    + '<span style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(t('adminScopeView')) + '</span>'
    + '<span onclick="_toggleScopeViewAll()" style="cursor:pointer;font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--pf-border);background:'
    + (on ? 'var(--pf-accent)' : 'transparent') + ';color:' + (on ? 'var(--pf-bg)' : 'var(--pf-muted)') + ';" title="' + _pfpAttr(t('adminScopeViewHint')) + '">'
    + escapeHtml(on ? t('adminScopeAll') : t('adminScopeMine')) + '</span>'
    + '</div>';
}
function _ownerBadge(item) {
  if (!item || !item.owner_id) return '';
  let label = item.owner_display || item.owner_id;
  if (item.conv_id) label += ' \u00B7 ' + (item.conv_title || String(item.conv_id).slice(0, 8));
  return '<span style="font-size:9px;padding:0 4px;border-radius:3px;margin-left:4px;'
    + 'background:color-mix(in srgb, var(--pf-accent-2) 22%, var(--pf-panel));color:var(--pf-accent-2);" '
    + 'title="' + _pfpAttr(t('owner')) + '">\u{1F464} ' + escapeHtml(label) + '</span>';
}

// Admin target-owner picker (create / promote / demote on behalf of a user).
let _adminUsersCache = null;
function _ensureAdminUsers() {
  if (_adminUsersCache) return Promise.resolve(_adminUsersCache);
  if (typeof _isAdmin !== 'function' || !_isAdmin()) return Promise.resolve([]);
  return rxjs.firstValueFrom(action$('admin_users_list', {})).then(function(d) {
    _adminUsersCache = (d && d.users) || [];
    return _adminUsersCache;
  }).catch(function() { return []; });
}
function _targetOwnerFieldHtml(selectId) {
  // Admin-only owner override field for create dialogs. Empty value = self
  // (current behaviour). Populated async by _populateTargetOwnerField.
  if (typeof _isAdmin !== 'function' || !_isAdmin()) return '';
  return '<div style="margin-bottom:8px;">'
    + '<label style="display:block;font-size:11px;color:var(--pf-muted);margin-bottom:3px;">' + escapeHtml(t('targetOwner')) + '</label>'
    + '<select id="' + selectId + '" style="' + _svcInputStyle + '"><option value="">' + escapeHtml(t('targetOwnerSelf')) + '</option></select>'
    + '</div>';
}
function _populateTargetOwnerField(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  _ensureAdminUsers().then(function(users) {
    const cur = sel.value;
    let html = '<option value="">' + escapeHtml(t('targetOwnerSelf')) + '</option>';
    users.forEach(function(u) {
      const uname = u.username || '';
      const disp = u.display_name || uname;
      const lab = disp !== uname ? (disp + ' (' + uname + ')') : uname;
      html += '<option value="' + escapeHtml(uname) + '">' + escapeHtml(lab) + '</option>';
    });
    sel.innerHTML = html;
    if (cur) sel.value = cur;
  });
}
function _targetOwnerValue(selectId) {
  const sel = document.getElementById(selectId);
  return sel ? (sel.value || '') : '';
}

// Collapsed state per resource tree section. The first load keeps the
// predictable default (only Agents open); user toggles are persisted so a
// browser reload restores exactly what was open or closed.
const _RESOURCE_TREE_STATE_KEY = 'pawflow.resource_tree.collapsed.v1';
const _ALL_SECTIONS = [
  'agent','_running','_flow','_svc','_relay','_remote_fs','_summarizer','_param','_secret',
  '_pfp','_agent_repo','skill','prompt','theme','voice','task_def','_mcp_repo','_tool','_flow_repo'
];
const _collapsedSections = {};
let _lastResourcesData = null;
function _defaultCollapsedSections() {
  const state = {};
  for (const k of _ALL_SECTIONS) state[k] = (k !== 'agent');
  return state;
}
function _loadCollapsedSections() {
  Object.assign(_collapsedSections, _defaultCollapsedSections());
  try {
    const raw = window.localStorage ? window.localStorage.getItem(_RESOURCE_TREE_STATE_KEY) : '';
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return;
    Object.keys(saved).forEach(function(k) {
      _collapsedSections[k] = saved[k] !== false;
    });
  } catch (_err) {}
}
function _saveCollapsedSections() {
  try {
    if (window.localStorage) window.localStorage.setItem(_RESOURCE_TREE_STATE_KEY, JSON.stringify(_collapsedSections));
  } catch (_err) {}
}
function _isSectionCollapsed(id) {
  if (Object.prototype.hasOwnProperty.call(_collapsedSections, id)) return _collapsedSections[id];
  _collapsedSections[id] = (id !== 'agent');
  return _collapsedSections[id];
}
_loadCollapsedSections();
function _toggleSection(id) {
  _collapsedSections[id] = !_isSectionCollapsed(id);
  _saveCollapsedSections();
  const isOpening = !_collapsedSections[id];
  const el = document.getElementById('res-section-' + id);
  if (el) el.style.display = isOpening ? 'block' : 'none';
  const arrow = document.getElementById('res-arrow-' + id);
  if (arrow) arrow.textContent = isOpening ? '\u25BC' : '\u25B6';
  if (isOpening && _lastResourcesData) _renderResourcesData(_lastResourcesData);
  // Opening a repository or runtime section refreshes from disk after the cached render.
  if (isOpening && (id.endsWith('_repo') || id === '_svc' || id === '_relay' || id === '_remote_fs' || id === '_summarizer' || id === '_flow' || id === '_pfp')) loadResources();
}


// _sectionHeader(title, rtype, opts?)
//   opts.createTitle     tooltip for '+' (default 'Create new'; for
//                        rtype='agent' defaults to 'Add agent to conversation')
//   opts.createOnclick   override the '+' click handler
//   opts.refreshOnclick  override the refresh click handler (default:
//                        loadResources() - sufficient for every section
//                        that reads from the ResourceStore or from conv
//                        extras, both of which hit disk on every call).
//                        Use 'reload_disk + loadResources' for sections
//                        backed by a live registry (Services,
//                        Deployed Flows) where the in-memory state must
//                        be rebuilt from disk before the list refetch.
//   opts.refreshTitle    tooltip for the refresh button
//   opts.hideRefresh     hide the refresh button (default: show)
//   opts.hideCreate      hide the '+' button entirely
function _sectionHeader(title, rtype, opts) {
  opts = opts || {};
  const isParamSecret = rtype === '_param' || rtype === '_secret';
  const createOnclick = opts.createOnclick !== undefined ? opts.createOnclick
    : isParamSecret ? `_showParamEditor('','',${rtype === '_secret'},true)`
    : rtype === 'agent' ? 'showAddAgentToConvDialog()'
    : `showResourceCreator('${rtype}')`;
  const createTitle = opts.createTitle
    || (rtype === 'agent' ? t('addAgentToConversation') : t('createNew'));
  // Refresh: shown by default on every section (every listing reads
  // from disk, and the user may edit those files manually out-of-band).
  const refreshOnclick = opts.refreshOnclick
    || "event.stopPropagation();loadResources()";
  const refreshBtn = opts.hideRefresh ? ''
    : `<span style="cursor:pointer;font-size:11px;color:var(--pf-muted);padding:0 2px;" onclick="${refreshOnclick}" title="${opts.refreshTitle || t('refreshFromDisk')}">\u21BB</span>`;
  const createBtn = opts.hideCreate ? ''
    : `<span style="cursor:pointer;font-size:13px;color:var(--pf-accent);padding:0 4px;" onclick="${createOnclick}" title="${createTitle}">+</span>`;
  const collapsed = _isSectionCollapsed(rtype);
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
    <span style="cursor:pointer;color:var(--pf-resource-heading, var(--pf-accent));font-weight:600;user-select:none;" onclick="_toggleSection('${rtype}')"><span id="res-arrow-${rtype}">${arrow}</span> ${title}</span>
    <span style="display:flex;gap:4px;align-items:center;">${refreshBtn}${createBtn}</span>
  </div><div id="res-section-${rtype}" style="display:${collapsed ? 'none' : 'block'};max-height:260px;overflow-y:auto;">`;
}
// _repoSectionHeader(title, rtype, opts?)
//   opts.createOnclick   if set, render a '+' button next to the refresh
//   opts.createTitle     tooltip for the '+' button (default 'Create new')
//   opts.refreshOnclick  override the refresh handler (default loadResources)
//   opts.refreshTitle    tooltip for the refresh button
function _repoSectionHeader(title, rtype, opts) {
  opts = opts || {};
  const collapsed = _isSectionCollapsed(rtype);
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  const createBtn = opts.createOnclick
    ? `<span style="cursor:pointer;font-size:13px;color:var(--pf-accent);padding:0 4px;" onclick="${opts.createOnclick}" title="${opts.createTitle || t('createNew')}">+</span>`
    : '';
  const refreshOnclick = opts.refreshOnclick
    || "event.stopPropagation();loadResources()";
  return `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
    <span style="cursor:pointer;color:var(--pf-resource-subheading, var(--pf-muted));font-weight:500;font-size:11px;user-select:none;" onclick="_toggleSection('${rtype}')"><span id="res-arrow-${rtype}">${arrow}</span> ${title}</span>
    <span style="display:flex;gap:4px;align-items:center;">
      <span style="cursor:pointer;font-size:11px;color:var(--pf-muted);padding:0 2px;" onclick="${refreshOnclick}" title="${opts.refreshTitle || t('refreshFromDisk')}">\u21BB</span>
      ${createBtn}
    </span>
  </div><div id="res-section-${rtype}" style="display:${collapsed ? 'none' : 'block'};">`;
}
function _sectionFooter() { return '</div>'; }

function _pfpAttr(value) {
  return escapeAttr(String(value == null ? '' : value));
}

function _pfpJsArg(value) {
  return jsStringArg(value);
}

