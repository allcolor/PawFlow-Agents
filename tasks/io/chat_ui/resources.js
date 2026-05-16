// ── Resources (services, flows) ──────────────────────────────────
// Canonical service lister. Pass a `serviceType` filter (e.g. 'llmConnection',
// 'tool_relay_service') to get a subset. This is the ONLY way the UI should
// fetch services — never through agent/resource actions.
function listServices$(serviceType) {
  const payload = serviceType ? { service_type: serviceType } : {};
  if (typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  return action$('list_services', payload);
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
      if (data.installed) addMsg('system', t('serviceInstalled', { id: data.id, type: data.type }));
      else if (data.uninstalled) addMsg('system', t('serviceUninstalled', { id: data.id }));
      else if (data.enabled) addMsg('system', t('serviceEnabled', { id: data.id }));
      else if (data.disabled) addMsg('system', t('serviceDisabled', { id: data.id }));
      else addMsg('system', JSON.stringify(data, null, 2));
    })
  ));
}

function cmdSkillList() {
  action$('list_skills', {}).subscribe(data => {
    const skills = data.skills || [];
    if (!skills.length) { addMsg('system', t('noSkillsUsage')); return; }
    let lines = [t('yourSkillsHeader')];
    skills.forEach(s => {
      const mark = s.active ? '\\u2705' : '\\u2B1C';
      lines.push(`${mark} **${s.name}** — ${s.description || s.prompt}`);
    });
    addMsg('system', lines.join('\\n'));
  });
}

function cmdListResources() {
  action$('list_resources', {}).subscribe(data => {
    let lines = [];
    if (data.agents && data.agents.length) {
      lines.push(t('agentsHeader'));
      data.agents.forEach(a => {
        const mark = a.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${a.name} ${a.description ? '— ' + a.description : ''}`);
      });
    }
    if (data.skills && data.skills.length) {
      lines.push(t('skillsHeader'));
      data.skills.forEach(s => {
        const mark = s.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${s.name} ${s.description ? '— ' + s.description : ''}`);
      });
    }
    if (data.mcp_servers && data.mcp_servers.length) {
      lines.push(t('mcpServersHeader'));
      data.mcp_servers.forEach(m => {
        const mark = m.active ? '\\u2705' : '\\u2B1C';
        lines.push(`  ${mark} ${m.name} (${m.url})`);
      });
    }
    if (!lines.length) lines.push(t('noResourcesDefinedUsage'));
    addMsg('system', lines.join('\\n'));
  });
}

// ── Sidebar Resources ───────────────────────────────────────────
function _scopeBadge(s) {
  if (!s) return '';
  const colors = { global: 'var(--pf-accent-2)', user: 'var(--pf-accent)', conversation: 'var(--pf-success)', conv: 'var(--pf-success)' };
  const labels = { global: 'G', user: 'U', conversation: 'C', conv: 'C' };
  return `<span style="font-size:9px;padding:0 3px;border-radius:3px;background:${colors[s]||'var(--pf-border)'};color:var(--pf-text);margin-right:3px;" title="${s}">${labels[s]||s[0]}</span>`;
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
  return escapeHtml(String(value == null ? '' : value)).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _pfpObjectLabel(obj) {
  return [obj.object_id || obj.id || '', obj.kind || obj.type || '', obj.name || obj.service_id || obj.task_type || '']
    .filter(Boolean).join(' · ');
}

function _pfpShortHash(value) {
  const raw = String(value || '');
  return raw.length > 22 ? raw.slice(0, 18) + '...' : raw;
}

function _pfpHashForRow(row) {
  const value = row.to_hash || row.hash || row.from_hash || row.sha256 || row.package_sha256 || '';
  return String(value || '').replace(/^sha256:/, '');
}

function _pfpCopy(value) {
  value = String(value || '');
  if (!value || !navigator.clipboard) return;
  navigator.clipboard.writeText(value).then(() => {
    addMsg('system', t('copiedCharsToClipboard', { n: value.length }));
  }).catch(e => addMsg('error', t('copyFailed', { error: e.message })));
}

function _pfpList(values, emptyLabel) {
  const items = (values || []).filter(v => String(v || '').trim());
  if (!items.length) return '<span style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(emptyLabel || t('none')) + '</span>';
  return items.map(v => '<span style="font-size:10px;color:var(--pf-text);background:color-mix(in srgb, var(--pf-muted) 12%, var(--pf-panel));border:1px solid var(--pf-border);border-radius:3px;padding:1px 5px;">' + escapeHtml(String(v)) + '</span>').join(' ');
}

function _pfpCapabilityRefs(items) {
  return (items || []).map(item => item.ref || item.name || (item.package && item.object ? item.package + '/' + item.object : '') || item.object || item.package || '').filter(Boolean);
}

function _renderPfpCapabilities(plan) {
  const caps = (plan && plan.capabilities) || {};
  const deps = (caps.dependencies || []).map(d => d.package ? (d.package + (d.version ? '@' + d.version : '') + (d.object ? '/' + d.object : '')) : '');
  const secrets = (caps.secrets || []).map(s => (s.name || '') + (s.env ? ' -> ' + s.env : ''));
  const rows = [
    [t('pfpRuntimeObjects'), caps.runtime_objects || []],
    [t('pfpDependencies'), deps],
    [t('pfpAllowedTools'), _pfpCapabilityRefs(caps.allowed_tools)],
    [t('pfpAllowedServices'), _pfpCapabilityRefs(caps.allowed_services)],
    [t('pfpProvides'), caps.provides || []],
    [t('pfpSecrets'), secrets],
  ];
  return rows.map(([label, values]) => '<div style="margin-bottom:6px;"><div style="color:var(--pf-muted);font-size:10px;margin-bottom:2px;">' + escapeHtml(label) + '</div><div style="display:flex;flex-wrap:wrap;gap:3px;">' + _pfpList(values, t('none')) + '</div></div>').join('');
}

function _renderPfpUpdateDiff(plan) {
  const diff = (plan && plan.update_diff) || {};
  if (!diff.installed) return '';
  const objectById = {};
  ((plan && plan.objects) || []).forEach(row => { objectById[row.id || ''] = row; });
  const rows = (diff.objects || []).map(item => {
    const color = item.change === 'remove' ? 'var(--pf-danger)' : item.change === 'add' ? 'var(--pf-success)' : item.change === 'update' ? 'var(--pf-warning)' : 'var(--pf-muted)';
    const hash = _pfpHashForRow(item);
    const objectRow = objectById[item.id || ''] || {};
    const selected = objectRow.selected === false ? t('pfpNotSelected') : t('pfpSelected');
    return '<div class="pfp-update-row" data-change="' + _pfpAttr(item.change || 'unchanged') + '" style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">'
      + '<span style="font-size:10px;color:' + color + ';min-width:64px;">' + escapeHtml(item.change || '') + '</span>'
      + '<span style="font-size:11px;color:var(--pf-text);flex:1;">' + escapeHtml(item.id || '') + '</span>'
      + '<span style="font-size:9px;color:var(--pf-muted);min-width:62px;">' + escapeHtml(selected) + '</span>'
      + (hash ? '<code title="sha256:' + _pfpAttr(hash) + '" style="font-size:9px;color:var(--pf-muted);max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(_pfpShortHash(hash)) + '</code>' : '')
      + '</div>';
  }).join('');
  return '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
    + '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;flex:1;">' + escapeHtml(t('pfpUpdateDiff')) + ': '
    + escapeHtml((diff.from_version || '') + ' -> ' + (diff.to_version || '') + ' (' + (diff.version_change || '') + ')') + '</div>'
    + ['all','add','update','remove','unchanged'].map(change => '<button type="button" class="pfp-update-filter" data-change="' + change + '" style="background:var(--pf-border);color:var(--pf-text);border:none;border-radius:3px;padding:2px 5px;cursor:pointer;font-size:9px;">' + escapeHtml(t('pfpFilter' + change.charAt(0).toUpperCase() + change.slice(1))) + '</button>').join('')
    + '</div>'
    + (rows || '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noChanges')) + '</div>')
    + '</div>';
}

function _renderPfpObjectSelector(plan) {
  const objects = (plan && plan.objects) || [];
  if (!objects.length) return '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noObjects')) + '</div>';
  return objects.map(row => {
    const disabled = !row.installable || ['blocked', 'missing_dependency', 'unsupported_runtime'].includes(row.status || '');
    const checked = row.selected && !disabled ? ' checked' : '';
    const disabledAttr = disabled ? ' disabled' : '';
    const riskColor = row.risk === 'high' ? 'var(--pf-danger)' : row.risk === 'medium' ? 'var(--pf-warning)' : 'var(--pf-muted)';
    const reason = row.reason ? '<div style="color:var(--pf-muted);font-size:10px;margin-left:24px;">' + escapeHtml(row.reason) + '</div>' : '';
    const change = row.update_diff && row.update_diff.change && row.update_diff.change !== 'unchanged'
      ? '<span style="font-size:9px;color:var(--pf-warning);border:1px solid var(--pf-warning);border-radius:3px;padding:0 3px;">' + escapeHtml(row.update_diff.change) + '</span>' : '';
    return '<div style="border-bottom:1px solid color-mix(in srgb, var(--pf-border) 55%, transparent);padding:5px 0;">'
      + '<label style="display:flex;align-items:center;gap:6px;cursor:' + (disabled ? 'not-allowed' : 'pointer') + ';">'
      + '<input type="checkbox" class="pfp-object-check" value="' + _pfpAttr(row.id || '') + '"' + checked + disabledAttr + '/>'
      + '<span style="font-size:12px;color:var(--pf-text);flex:1;">' + escapeHtml(row.id || '') + '</span>'
      + change
      + (row.hash ? '<code title="' + _pfpAttr(row.hash) + '" style="font-size:9px;color:var(--pf-muted);max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(_pfpShortHash(row.hash)) + '</code>' : '')
      + '<span style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(row.status || '') + '</span>'
      + '<span style="font-size:10px;color:' + riskColor + ';">' + escapeHtml(row.risk || 'low') + '</span>'
      + '</label>' + reason + '</div>';
  }).join('');
}

function _renderPfpSecretBindings(plan) {
  const secrets = (((plan || {}).capabilities || {}).secrets || []).filter(s => s && s.name);
  if (!secrets.length) return '';
  return '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('pfpSecretBindings')) + '</div>'
    + secrets.map(s => '<div style="margin-bottom:6px;">'
      + '<label style="display:block;color:var(--pf-muted);font-size:10px;margin-bottom:2px;">' + escapeHtml(s.name + (s.env ? ' -> ' + s.env : '')) + '</label>'
      + '<input class="pfp-secret-binding" data-secret="' + _pfpAttr(s.name) + '" placeholder="' + _pfpAttr(t('storedSecretKey')) + '" style="' + _svcInputStyle + '"/>'
      + '</div>').join('') + '</div>';
}

function _renderPfpRegistryResults(data) {
  const results = (data && data.results) || [];
  const errors = (data && data.errors) || [];
  let html = '';
  if (!results.length) {
    html += '<div style="color:var(--pf-muted);font-size:11px;padding:4px 0;">' + escapeHtml(t('noPackageResults')) + '</div>';
  } else {
    html += results.map(row => {
      const objects = (row.objects || []).slice(0, 4).join(', ');
      const more = (row.objects || []).length > 4 ? ' +' + ((row.objects || []).length - 4) : '';
      const tags = (row.tags || []).slice(0, 5).map(tag => '<span style="font-size:9px;color:var(--pf-muted);border:1px solid var(--pf-border);border-radius:3px;padding:0 3px;">' + escapeHtml(tag) + '</span>').join(' ');
      const trustRows = [
        [t('sourceUrl'), row.url || ''],
        [t('sha256'), row.sha256 || ''],
        [t('developerKey'), row.developer_key || ''],
      ].filter(item => item[1]);
      const trustPolicy = row.registry_trusted ? t('pfpTrustPolicyTrusted') : t('pfpTrustPolicyUntrusted');
      const warning = row.registry_trusted ? '' : '<div style="margin-top:5px;color:var(--pf-warning);font-size:10px;">' + escapeHtml(t('pfpRegistryUntrustedWarning')) + '</div>';
      const trustHtml = trustRows.length
        ? '<div style="margin-top:5px;border-top:1px solid color-mix(in srgb, var(--pf-border) 55%, transparent);padding-top:5px;">'
          + '<div style="display:flex;gap:5px;font-size:9px;line-height:1.35;margin-bottom:2px;">'
          + '<span style="color:var(--pf-muted);min-width:70px;">' + escapeHtml(t('trustPolicy')) + '</span>'
          + '<span style="color:' + (row.registry_trusted ? 'var(--pf-success)' : 'var(--pf-warning)') + ';">' + escapeHtml(trustPolicy) + '</span>'
          + '</div>'
          + trustRows.map(item => '<div style="display:flex;gap:5px;font-size:9px;line-height:1.35;">'
            + '<span style="color:var(--pf-muted);min-width:70px;">' + escapeHtml(item[0]) + '</span>'
            + '<code style="color:var(--pf-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">' + escapeHtml(item[1]) + '</code>'
            + (item[0] === t('sha256') ? '<button type="button" onclick="_pfpCopy(this.dataset.copy)" data-copy="' + _pfpAttr(item[1]) + '" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:3px;padding:0 4px;cursor:pointer;font-size:9px;">' + escapeHtml(t('copy')) + '</button>' : '')
            + '</div>').join('') + '</div>'
        : '';
      return '<div style="border:1px solid var(--pf-border);border-radius:4px;padding:7px;margin-bottom:6px;background:color-mix(in srgb, var(--pf-panel) 90%, var(--pf-muted));">'
        + '<div style="display:flex;gap:8px;align-items:center;">'
        + '<div style="flex:1;min-width:0;">'
        + '<div style="font-size:12px;color:var(--pf-text);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(row.ref || '') + '</div>'
        + '<div style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(row.registry || '')
        + (row.registry_trusted ? ' <span style="color:var(--pf-success);">' + escapeHtml(t('trusted')) + '</span>' : '') + '</div>'
        + '</div>'
        + '<button class="pfp-result-inspect" data-ref="' + _pfpAttr(row.ref || row.url || '') + '" data-sha="' + _pfpAttr(row.sha256 || '') + '" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:5px 8px;border-radius:4px;cursor:pointer;font-size:11px;">' + escapeHtml(t('inspect')) + '</button>'
        + '</div>'
        + (row.description ? '<div style="font-size:11px;color:var(--pf-text);margin-top:4px;">' + escapeHtml(row.description) + '</div>' : '')
        + (objects ? '<div style="font-size:10px;color:var(--pf-muted);margin-top:4px;">' + escapeHtml(objects + more) + '</div>' : '')
        + (tags ? '<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:5px;">' + tags + '</div>' : '')
        + trustHtml
        + warning
        + '</div>';
    }).join('');
  }
  if (errors.length) {
    html += '<div style="border-top:1px solid var(--pf-border);margin-top:6px;padding-top:6px;color:var(--pf-warning);font-size:10px;">'
      + errors.map(err => escapeHtml((err.registry || '') + ': ' + (err.error || ''))).join('<br/>') + '</div>';
  }
  return html;
}

function _renderPfpRegistries(data) {
  const registries = (data && data.registries) || [];
  if (!registries.length) {
    return '<div style="color:var(--pf-muted);font-size:11px;padding:4px 0;">' + escapeHtml(t('noPfpRegistries')) + '</div>';
  }
  return registries.map(row => '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">'
    + '<div style="flex:1;min-width:0;">'
    + '<div style="font-size:11px;color:var(--pf-text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(row.name || row.url || '') + '</div>'
    + '<div style="font-size:10px;color:var(--pf-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(row.url || '') + '</div>'
    + '</div>'
    + (row.trusted ? '<span style="font-size:9px;color:var(--pf-success);border:1px solid var(--pf-success);border-radius:3px;padding:0 3px;">' + escapeHtml(t('trusted')) + '</span>' : '')
    + '<span style="font-size:10px;color:var(--pf-muted);">' + escapeHtml(String(row.package_count || 0)) + '</span>'
    + '<button class="pfp-registry-remove" data-registry="' + _pfpAttr(row.name || row.url || '') + '" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);padding:2px 5px;border-radius:3px;cursor:pointer;font-size:10px;">' + escapeHtml(t('remove')) + '</button>'
    + '</div>').join('');
}

function _findPfpInstalledPackage(packageId, scope) {
  const packages = (_lastResourcesData && _lastResourcesData.pfp_packages) || [];
  return packages.find(pkg => (pkg.package || '') === packageId && ((pkg._scope || pkg.scope || 'user') === scope)) || null;
}

async function _uninstallPfpPackage(packageId, scope, force) {
  packageId = String(packageId || '').trim();
  scope = String(scope || 'user').trim() || 'user';
  if (!packageId) return;
  try {
    const result = await rxjs.firstValueFrom(action$('pfp_uninstall', {
      package: packageId,
      scope: scope,
      conversation_id: conversationId,
      force: force,
    }));
    if (result.error) { addMsg('error', result.error); return; }
    if (result.ok === false) {
      const blockers = (result.blocked_by || []).map(row => row.package || '').filter(Boolean).join(', ');
      addMsg('error', blockers ? t('pfpUninstallBlockedBy', { packages: blockers }) : JSON.stringify(result));
      return;
    }
    addMsg('system', t('pfpUninstallComplete', { package: packageId }));
    loadResources();
  } catch (e) {
    addMsg('error', e.message);
  }
}

function _showPfpUninstallDialog(packageId, scope) {
  packageId = String(packageId || '').trim();
  scope = String(scope || 'user').trim() || 'user';
  if (!packageId) return;
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:18px;width:460px;max-width:calc(100vw - 32px);border:1px solid var(--pf-border);';
  const pkg = _findPfpInstalledPackage(packageId, scope) || {};
  const objects = pkg.objects || [];
  const blockers = pkg.blocked_by || [];
  const objectRows = objects.length
    ? objects.map(obj => '<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">'
      + '<span style="font-size:10px;color:var(--pf-muted);min-width:72px;">' + escapeHtml(obj.kind || obj.resource_type || '') + '</span>'
      + '<span style="font-size:11px;color:var(--pf-text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(obj.object_id || _pfpObjectLabel(obj)) + '</span>'
      + '</div>').join('')
    : '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noObjects')) + '</div>';
  const blockerRows = blockers.length
    ? blockers.map(row => '<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">'
      + '<span style="font-size:11px;color:var(--pf-warning);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml((row.package || '') + (row.version ? '@' + row.version : '')) + '</span>'
      + '</div>').join('')
    : '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('none')) + '</div>';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('uninstall')) + '</h3>'
    + '<button class="pfp-uninstall-cancel" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
    + '</div>'
    + '<div style="font-size:12px;color:var(--pf-text);margin-bottom:8px;">' + escapeHtml(t('pfpUninstallConfirm', { package: packageId, scope: scope })) + '</div>'
    + '<div style="display:flex;gap:6px;margin-bottom:10px;">'
    + '<span style="font-size:10px;color:var(--pf-muted);border:1px solid var(--pf-border);border-radius:3px;padding:1px 5px;">' + escapeHtml(scope) + '</span>'
    + (pkg.version ? '<span style="font-size:10px;color:var(--pf-muted);border:1px solid var(--pf-border);border-radius:3px;padding:1px 5px;">v' + escapeHtml(pkg.version) + '</span>' : '')
    + '</div>'
    + '<div style="border:1px solid var(--pf-border);border-radius:4px;padding:8px;margin-bottom:10px;max-height:160px;overflow-y:auto;">'
    + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('objects')) + '</div>'
    + objectRows
    + '</div>'
    + '<div style="border:1px solid ' + (blockers.length ? 'var(--pf-warning)' : 'var(--pf-border)') + ';border-radius:4px;padding:8px;margin-bottom:10px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('pfpBlockingDependents')) + '</div>'
    + blockerRows
    + '</div>'
    + '<div style="font-size:11px;color:var(--pf-muted);margin-bottom:14px;">' + escapeHtml(t('pfpUninstallHelp')) + '</div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;">'
    + '<button class="pfp-uninstall-cancel" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button id="pfp-uninstall-soft" style="background:var(--pf-warning);color:var(--pf-bg);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('uninstall')) + '</button>'
    + '<button id="pfp-uninstall-force" style="background:var(--pf-danger);color:var(--pf-bg);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('force')) + '</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  panel.querySelectorAll('.pfp-uninstall-cancel').forEach(btn => btn.addEventListener('click', () => overlay.remove()));
  panel.querySelector('#pfp-uninstall-soft').addEventListener('click', async () => {
    overlay.remove();
    await _uninstallPfpPackage(packageId, scope, false);
  });
  panel.querySelector('#pfp-uninstall-force').addEventListener('click', async () => {
    overlay.remove();
    await _uninstallPfpPackage(packageId, scope, true);
  });
}

function _showPfpInstallDialog(initialRef) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:760px;max-width:calc(100vw - 32px);max-height:88vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('pfpInstallPackage')) + '</h3>'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
    + '</div>'
    + '<div style="border:1px solid var(--pf-border);border-radius:4px;padding:8px;margin-bottom:10px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('pfpRegistrySearch')) + '</div>'
    + '<div style="display:grid;grid-template-columns:1fr auto;gap:8px;">'
    + '<input id="pfp-search-query" placeholder="' + _pfpAttr(t('pfpRegistrySearchPlaceholder')) + '" style="' + _svcInputStyle + '"/>'
    + '<button id="pfp-search-btn" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('search')) + '</button>'
    + '</div>'
    + '<div id="pfp-search-results" style="margin-top:8px;max-height:220px;overflow-y:auto;"></div>'
    + '<div style="border-top:1px solid var(--pf-border);margin-top:8px;padding-top:8px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('pfpRegistries')) + '</div>'
    + '<div style="display:grid;grid-template-columns:1fr 130px auto auto;gap:8px;align-items:center;">'
    + '<input id="pfp-registry-url" placeholder="' + _pfpAttr(t('registryUrl')) + '" style="' + _svcInputStyle + '"/>'
    + '<input id="pfp-registry-name" placeholder="' + _pfpAttr(t('registryNameOptional')) + '" style="' + _svcInputStyle + '"/>'
    + '<label style="color:var(--pf-muted);font-size:11px;display:flex;gap:4px;align-items:center;"><input id="pfp-registry-trusted" type="checkbox"/> ' + escapeHtml(t('trusted')) + '</label>'
    + '<button id="pfp-registry-add-btn" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('add')) + '</button>'
    + '</div>'
    + '<div id="pfp-registry-list" style="margin-top:8px;max-height:110px;overflow-y:auto;"></div>'
    + '</div>'
    + '</div>'
    + '<div style="display:grid;grid-template-columns:1fr 130px;gap:8px;margin-bottom:8px;">'
    + '<input id="pfp-ref" value="' + _pfpAttr(initialRef || '') + '" placeholder="' + _pfpAttr(t('pfpPathOrRef')) + '" style="' + _svcInputStyle + '"/>'
    + '<select id="pfp-scope" style="' + _svcInputStyle + '"><option value="user">' + escapeHtml(t('user')) + '</option><option value="conversation">' + escapeHtml(t('conversation')) + '</option></select>'
    + '</div>'
    + '<div style="display:grid;grid-template-columns:1fr auto auto;gap:8px;margin-bottom:10px;align-items:center;">'
    + '<input id="pfp-sha" placeholder="' + _pfpAttr(t('optionalSha256')) + '" style="' + _svcInputStyle + '"/>'
    + '<label style="color:var(--pf-muted);font-size:11px;display:flex;gap:4px;align-items:center;"><input id="pfp-force" type="checkbox"/> ' + escapeHtml(t('force')) + '</label>'
    + '<label style="color:var(--pf-muted);font-size:11px;display:flex;gap:4px;align-items:center;"><input id="pfp-replace" type="checkbox"/> ' + escapeHtml(t('replace')) + '</label>'
    + '</div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-bottom:10px;">'
    + '<button id="pfp-inspect-btn" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('inspect')) + '</button>'
    + '<button id="pfp-install-btn" disabled style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;opacity:0.6;">' + escapeHtml(t('install')) + '</button>'
    + '</div>'
    + '<div id="pfp-review" style="border-top:1px solid var(--pf-border);padding-top:10px;color:var(--pf-text);"></div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  let plan = null;
  const inspectBtn = panel.querySelector('#pfp-inspect-btn');
  const installBtn = panel.querySelector('#pfp-install-btn');
  const review = panel.querySelector('#pfp-review');
  const searchBtn = panel.querySelector('#pfp-search-btn');
  const searchQuery = panel.querySelector('#pfp-search-query');
  const searchResults = panel.querySelector('#pfp-search-results');
  const registryAddBtn = panel.querySelector('#pfp-registry-add-btn');
  const registryList = panel.querySelector('#pfp-registry-list');
  const inspect = async () => {
    const ref = (panel.querySelector('#pfp-ref').value || '').trim();
    if (!ref) { alert(t('pfpPathRequired')); return; }
    inspectBtn.disabled = true;
    inspectBtn.textContent = t('loading');
    installBtn.disabled = true;
    review.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('loading')) + '</div>';
    try {
      const scope = panel.querySelector('#pfp-scope').value;
      const sha = (panel.querySelector('#pfp-sha').value || '').trim();
      const data = await rxjs.firstValueFrom(action$('pfp_inspect', { path: ref, scope, conversation_id: conversationId, sha256: sha }));
      if (data.error) { review.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;">' + escapeHtml(data.error) + '</div>'; return; }
      plan = data;
      const riskColor = data.risk === 'high' ? 'var(--pf-danger)' : data.risk === 'medium' ? 'var(--pf-warning)' : 'var(--pf-muted)';
      review.innerHTML = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
        + '<div style="font-size:13px;color:var(--pf-text);font-weight:600;flex:1;">' + escapeHtml((data.package || '') + '@' + (data.version || '')) + '</div>'
        + '<span style="font-size:10px;color:' + riskColor + ';border:1px solid ' + riskColor + ';border-radius:3px;padding:1px 5px;">' + escapeHtml(data.risk || 'low') + '</span>'
        + '<span style="font-size:10px;color:var(--pf-muted);">' + (data.verified ? '\u2713 ' : '') + escapeHtml(t('verified')) + '</span>'
        + '</div>'
        + _renderPfpUpdateDiff(data)
        + '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
        + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:4px;">' + escapeHtml(t('objects')) + '</div>'
        + _renderPfpObjectSelector(data) + '</div>'
        + _renderPfpSecretBindings(data)
        + '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
        + '<div style="color:var(--pf-muted);font-size:11px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('capabilities')) + '</div>'
        + _renderPfpCapabilities(data) + '</div>';
      review.querySelectorAll('.pfp-update-filter').forEach(btn => {
        btn.addEventListener('click', () => {
          const wanted = btn.dataset.change || 'all';
          review.querySelectorAll('.pfp-update-row').forEach(row => {
            row.style.display = (wanted === 'all' || row.dataset.change === wanted) ? 'flex' : 'none';
          });
        });
      });
      installBtn.disabled = false;
      installBtn.style.opacity = '1';
      installBtn.textContent = data.update_diff && data.update_diff.installed ? t('update') : t('install');
    } catch (e) {
      review.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;">' + escapeHtml(e.message) + '</div>';
    } finally {
      inspectBtn.disabled = false;
      inspectBtn.textContent = t('inspect');
    }
  };
  const refreshRegistries = async () => {
    registryList.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;padding:4px 0;">' + escapeHtml(t('loading')) + '</div>';
    try {
      const data = await rxjs.firstValueFrom(action$('pfp_registry_list', {}));
      if (data.error) { registryList.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(data.error) + '</div>'; return; }
      registryList.innerHTML = _renderPfpRegistries(data);
      registryList.querySelectorAll('.pfp-registry-remove').forEach(btn => {
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          try {
            const result = await rxjs.firstValueFrom(action$('pfp_registry_remove', { name: btn.dataset.registry || '' }));
            if (result.error) { addMsg('error', result.error); btn.disabled = false; return; }
            refreshRegistries();
          } catch (e) {
            addMsg('error', e.message);
            btn.disabled = false;
          }
        });
      });
    } catch (e) {
      registryList.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(e.message) + '</div>';
    }
  };
  registryAddBtn.addEventListener('click', async () => {
    const urlInput = panel.querySelector('#pfp-registry-url');
    const nameInput = panel.querySelector('#pfp-registry-name');
    const url = (urlInput.value || '').trim();
    if (!url) { alert(t('registryUrlRequired')); return; }
    registryAddBtn.disabled = true;
    registryAddBtn.textContent = t('loading');
    try {
      const result = await rxjs.firstValueFrom(action$('pfp_registry_add', {
        url,
        name: (nameInput.value || '').trim(),
        trusted: panel.querySelector('#pfp-registry-trusted').checked,
      }));
      if (result.error) { addMsg('error', result.error); return; }
      urlInput.value = '';
      nameInput.value = '';
      panel.querySelector('#pfp-registry-trusted').checked = false;
      refreshRegistries();
    } catch (e) {
      addMsg('error', e.message);
    } finally {
      registryAddBtn.disabled = false;
      registryAddBtn.textContent = t('add');
    }
  });
  const searchRegistry = async () => {
    searchBtn.disabled = true;
    searchBtn.textContent = t('searching');
    searchResults.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;padding:4px 0;">' + escapeHtml(t('searching')) + '</div>';
    try {
      const data = await rxjs.firstValueFrom(action$('pfp_search', { query: (searchQuery.value || '').trim(), limit: 20 }));
      if (data.error) { searchResults.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(data.error) + '</div>'; return; }
      searchResults.innerHTML = _renderPfpRegistryResults(data);
      searchResults.querySelectorAll('.pfp-result-inspect').forEach(btn => {
        btn.addEventListener('click', () => {
          panel.querySelector('#pfp-ref').value = btn.dataset.ref || '';
          panel.querySelector('#pfp-sha').value = btn.dataset.sha || '';
          inspect();
        });
      });
    } catch (e) {
      searchResults.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(e.message) + '</div>';
    } finally {
      searchBtn.disabled = false;
      searchBtn.textContent = t('search');
    }
  };
  searchBtn.addEventListener('click', searchRegistry);
  searchQuery.addEventListener('keydown', event => {
    if (event.key === 'Enter') searchRegistry();
  });
  inspectBtn.addEventListener('click', inspect);
  installBtn.addEventListener('click', async () => {
    if (!plan) return;
    const ref = (panel.querySelector('#pfp-ref').value || '').trim();
    const include = Array.from(panel.querySelectorAll('.pfp-object-check:checked')).map(el => el.value);
    if (!include.length) { alert(t('selectAtLeastOneObject')); return; }
    const secret_bindings = {};
    panel.querySelectorAll('.pfp-secret-binding').forEach(el => {
      const value = (el.value || '').trim();
      if (value) secret_bindings[el.dataset.secret] = value;
    });
    const payload = {
      path: ref,
      scope: panel.querySelector('#pfp-scope').value,
      conversation_id: conversationId,
      sha256: (panel.querySelector('#pfp-sha').value || '').trim(),
      include,
      force: panel.querySelector('#pfp-force').checked,
      replace: panel.querySelector('#pfp-replace').checked,
      secret_bindings,
    };
    const action = plan.update_diff && plan.update_diff.installed ? 'pfp_update' : 'pfp_install';
    installBtn.disabled = true;
    installBtn.textContent = t('installing');
    try {
      const result = await rxjs.firstValueFrom(action$(action, payload));
      if (result.error) { addMsg('error', result.error); installBtn.disabled = false; installBtn.textContent = t('install'); return; }
      if (result.ok === false) { addMsg('error', result.reason || JSON.stringify(result)); installBtn.disabled = false; installBtn.textContent = t('install'); return; }
      addMsg('system', t('pfpInstallComplete', { package: result.package || plan.package || '' }));
      overlay.remove();
      loadResources();
    } catch (e) {
      addMsg('error', e.message);
      installBtn.disabled = false;
      installBtn.textContent = t('install');
    }
  });
  refreshRegistries();
  if (initialRef) inspect();
}

function _flowPackageSectionId(packageName) {
  const raw = String(packageName || 'default').toLowerCase();
  return '_flow_pkg_' + raw.replace(/[^a-z0-9_]+/g, '_');
}

function _renderFlowPackageGroup(packageName, flows) {
  const sectionId = _flowPackageSectionId(packageName);
  const collapsed = _isSectionCollapsed(sectionId);
  const arrow = collapsed ? '\u25B6' : '\u25BC';
  const display = collapsed ? 'none' : 'block';
  let html = `<div style="margin:2px 0 4px 8px;">
    <div style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;" onclick="_toggleSection('${sectionId}')">
      <span id="res-arrow-${sectionId}" style="font-size:10px;color:var(--pf-muted);">${arrow}</span>
      <span style="font-size:12px;color:var(--pf-text);font-weight:600;flex:1;">${escapeHtml(packageName || 'default')}</span>
    </div>
    <div id="res-section-${sectionId}" style="display:${display};margin-top:2px;">`;
  flows.forEach(t => {
    const ver = t.version ? ` v${escapeHtml(t.version)}` : '';
    const desc = t.description ? ` title="${escapeHtml(t.description)}"` : '';
    html += `<div style="display:flex;align-items:center;gap:4px;margin-left:14px;margin-bottom:2px;cursor:pointer;"${desc} onclick="showDeployFlowDialog('${escapeHtml(t.id)}')">
      ${_scopeBadge(t.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(t.name)}${ver}</span>
      <span style="color:var(--pf-muted);font-size:10px;">[${t.tasks_count} tasks]</span>
    </div>`;
  });
  html += '</div></div>';
  return html;
}

function _showRelayLinkDialog() {
  action$('relay_list_available').subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var relays = data.relays || [];
    if (!relays.length) { addMsg('system', t('noRelaysAvailableConnectFirst')); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var options = relays.map(function(r) {
      var label = r.relay_id;
      if (r.host_root) label += ' \u2014 ' + r.host_root;
      else if (r.root) label += ' \u2014 ' + r.root;
      var status = r.connected ? '\u{1F7E2}' : '\u{1F534}';
      return '<option value="' + escapeHtml(r.relay_id) + '">' + status + ' ' + escapeHtml(label) + '</option>';
    }).join('');
    overlay.innerHTML =
      '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>' + escapeHtml(t('linkRelay')) + '</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_relayLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doRelayLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}
function _showRelayInfoDialog(relayId, details) {
  if (typeof details === 'string') try { details = JSON.parse(details); } catch(e) { details = {}; }
  var d = details || {};
  var dl = d._default_local || {};
  var rows = [
    [t('relayId'), relayId],
    [t('connected'), d.connected ? '\u{1F7E2} ' + t('yes') : '\u{1F534} ' + t('no')],
    [t('dockerRoot'), d.root || '\u2014'],
    [t('localRoot'), d.host_root || '\u2014'],
    [t('platform'), d.platform || '\u2014'],
    [t('containerized'), d.containerized ? t('yes') : t('no')],
    [t('allowLocal'), d.allow_local ? '\u2705 ' + t('yes') : '\u274c ' + t('no')],
  ];
  var infoHtml = '<table style="margin:8px 0;">' + rows.map(function(r) {
    return '<tr><td style="color:var(--pf-muted);padding:3px 12px 3px 0;font-size:12px;white-space:nowrap;">' + escapeHtml(r[0]) + '</td>'
      + '<td style="font-size:12px;">' + r[1] + '</td></tr>';
  }).join('') + '</table>';

  // Default local toggles (only if allow_local)
  var localHtml = '';
  if (d.allow_local) {
    var convLocal = dl['*'];
    var convLabel = convLocal === true ? t('local') : convLocal === false ? t('docker') : t('notSet');
    var convColor = convLocal === true ? 'var(--pf-success)' : convLocal === false ? 'var(--pf-danger)' : 'var(--pf-muted)';
    localHtml += '<div style="margin-top:8px;font-size:12px;font-weight:600;color:var(--pf-accent);">' + escapeHtml(t('defaultExecutionMode')) + '</div>';
    localHtml += '<div style="display:flex;align-items:center;gap:8px;margin:6px 0;font-size:12px;">'
      + '<span style="color:var(--pf-muted);min-width:80px;">' + escapeHtml(t('conversation')) + ':</span>'
      + '<span style="color:' + convColor + ';">' + convLabel + '</span>'
      + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-success);cursor:pointer;" '
      + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',true,\'\')">' + escapeHtml(t('local')) + '</button>'
      + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-danger);cursor:pointer;" '
      + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',false,\'\')">' + escapeHtml(t('docker')) + '</button>'
      + '</div>';
    // Per-agent toggles (from conversation agents)
    try {
      var agentEls = document.querySelectorAll('#_ncAgentsSel [data-id], .res-agent-name');
      // Simpler: get agents from resource panel
      var rpAgents = [];
      document.querySelectorAll('[data-agent-name]').forEach(function(el) { rpAgents.push(el.dataset.agentName); });
      if (!rpAgents.length) {
        // Fallback: get from active_resources in cached data
        var cachedAgents = window._lastResourceData && window._lastResourceData.agents;
        if (cachedAgents) rpAgents = cachedAgents.filter(function(a) { return a.active; }).map(function(a) { return a.name; });
      }
      rpAgents.forEach(function(agentName) {
        var aLocal = dl[agentName];
        var aLabel = aLocal === true ? t('local') : aLocal === false ? t('docker') : t('notSet');
        var aColor = aLocal === true ? 'var(--pf-success)' : aLocal === false ? 'var(--pf-danger)' : 'var(--pf-muted)';
        localHtml += '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px;">'
          + '<span style="color:var(--pf-muted);min-width:80px;">@' + escapeHtml(agentName) + ':</span>'
          + '<span style="color:' + aColor + ';">' + aLabel + '</span>'
          + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-success);cursor:pointer;" '
          + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',true,\'' + escapeHtml(agentName) + '\')">' + escapeHtml(t('local')) + '</button>'
          + '<button style="font-size:10px;padding:2px 6px;border:1px solid var(--pf-border);border-radius:3px;background:var(--pf-panel);color:var(--pf-danger);cursor:pointer;" '
          + 'onclick="_setRelayLocal(\'' + escapeHtml(relayId) + '\',false,\'' + escapeHtml(agentName) + '\')">' + escapeHtml(t('docker')) + '</button>'
          + '</div>';
      });
    } catch(e) {}
  }

  var overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.innerHTML = '<div class="exec-dialog" style="min-width:340px;">'
    + '<h3>' + escapeHtml(t('relayTitle', { id: relayId })) + '</h3>'
    + infoHtml + localHtml
    + '<div class="exec-btns"><button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('close')) + '</button></div>'
    + '</div>';
  document.body.appendChild(overlay);
}

function _setRelayLocal(relayId, local, agent) {
  action$('relay_set_local', {relay_id: relayId, local: local, agent: agent}).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', data.message || t('ok'));
    // Close dialog and refresh
    var ov = document.querySelector('.exec-overlay');
    if (ov) ov.remove();
    setTimeout(loadResources, 300);
  });
}

function _doRelayLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_relayLinkSelect');
  var rid = sel ? sel.value : '';
  overlay.remove();
  if (rid) {
    fireAction('relay_link', {relay_id: rid});
    setTimeout(loadResources, 500);
  }
}

function _showRemoteFsLinkDialog() {
  action$('remote_fs_status', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var linkedIds = new Set((data.linked || []).map(function(s) { return s.service_id || ''; }));
    var services = (data.available || []).filter(function(s) {
      return s.service_id && !linkedIds.has(s.service_id);
    });
    if (!services.length) { addMsg('system', t('noRemoteFilesystemsAvailable')); return; }
    window._remoteFsLinkOptions = services;
    var options = services.map(function(s, idx) {
      var access = s.service_type === 'rcloneFilesystem' ? t('mountedInRelays') : t('availableToTools');
      var label = '[' + (s.scope || 'user') + '] ' + s.service_id + ' (' + s.service_type + ', ' + access + ')';
      return '<option value="' + idx + '">' + escapeHtml(label) + '</option>';
    }).join('');
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    overlay.innerHTML =
      '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>' + escapeHtml(t('linkFilesystem')) + '</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_remoteFsLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doRemoteFsLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}

function _doRemoteFsLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_remoteFsLinkSelect');
  var idx = sel ? Number(sel.value) : -1;
  var svc = (window._remoteFsLinkOptions || [])[idx];
  overlay.remove();
  if (!svc) return;
  action$('remote_fs_link', {
    conversation_id: conversationId,
    service_id: svc.service_id,
    scope: svc.scope,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _unlinkRemoteFs(serviceId) {
  action$('remote_fs_unlink', {
    conversation_id: conversationId,
    service_id: serviceId,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _showSummarizerLinkDialog() {
  action$('summarizer_list_available', { conversation_id: conversationId }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    var services = data.available || [];
    if (!services.length) { addMsg('system', t('noSummarizerServices')); return; }
    window._summarizerLinkOptions = services;
    var options = services.map(function(s, idx) {
      var llm = s.llm_service ? ' \u2192 ' + s.llm_service : '';
      var label = '[' + (s.scope || 'global') + '] ' + s.service_id + llm;
      return '<option value="' + idx + '">' + escapeHtml(label) + '</option>';
    }).join('');
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    overlay.innerHTML =
      '<div class="exec-dialog" style="min-width:350px;">'
      + '<h3>' + escapeHtml(t('linkSummarizer')) + '</h3>'
      + '<div style="margin:12px 0;">'
      + '<select id="_summarizerLinkSelect" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">'
      + options
      + '</select>'
      + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button class="exec-approve" onclick="_doSummarizerLink(this)">' + escapeHtml(t('link')) + '</button>'
      + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
  });
}

function _doSummarizerLink(btn) {
  var overlay = btn.closest('.exec-overlay');
  var sel = overlay.querySelector('#_summarizerLinkSelect');
  var idx = sel ? Number(sel.value) : -1;
  var svc = (window._summarizerLinkOptions || [])[idx];
  overlay.remove();
  if (!svc) return;
  action$('summarizer_link', {
    conversation_id: conversationId,
    scope: svc.scope,
    service_id: svc.service_id,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _unlinkSummarizer() {
  action$('summarizer_unlink', { conversation_id: conversationId }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _showSummarizerMenu(e, canUnlink) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  item(t('linkSummarizer') + '...', _showSummarizerLinkDialog);
  if (canUnlink) item(t('unlink'), _unlinkSummarizer, true);
  setTimeout(() => document.addEventListener('click', function _close() {
    menu.remove(); document.removeEventListener('click', _close);
  }), 0);
}

var _loadResourcesTimer = null;
async function loadResources() {
  // Debounce: coalesce rapid calls into one (300ms window)
  if (_loadResourcesTimer) clearTimeout(_loadResourcesTimer);
  _loadResourcesTimer = setTimeout(_loadResourcesNow, 300);
}
function _loadResourcesNow() {
  _loadResourcesTimer = null;
  if (!conversationId) { document.getElementById('resourcesPanel').style.display = 'none'; return; }
  document.getElementById('resourcesPanel').style.display = 'block';
  // Fetch resources and services in parallel — merge then render.
  var _resData = null, _svcData = null, _pfpUserData = null, _pfpConvData = null;
  function _tryRender() {
    if (_resData === null || _svcData === null || _pfpUserData === null || _pfpConvData === null) return;
    var services = (_svcData.services || []).slice();
    var seenServices = new Set(services.map(s => (s.scope || '') + ':' + (s.service_id || '')));
    var summarizer = (_resData && _resData.summarizer) || {};
    var summarizers = (summarizer.available || []).slice();
    if (summarizer.effective) summarizers.push(summarizer.effective);
    summarizers.forEach(function(s) {
      var key = (s.scope || '') + ':' + (s.service_id || '');
      if (s.service_id && !seenServices.has(key)) {
        seenServices.add(key);
        services.push(s);
      }
    });
    var pfpPackages = [];
    ((_pfpUserData && _pfpUserData.packages) || []).forEach(function(p) { pfpPackages.push(Object.assign({_scope: 'user'}, p)); });
    ((_pfpConvData && _pfpConvData.packages) || []).forEach(function(p) { pfpPackages.push(Object.assign({_scope: 'conversation'}, p)); });
    var merged = Object.assign({}, _resData, { services: services, pfp_packages: pfpPackages });
    _lastResourcesData = merged;
    _renderResourcesFromSSE(merged);
  }
  action$('list_resources', {}).subscribe(d => { _resData = d || {}; _tryRender(); });
  listServices$().subscribe(d => { _svcData = d || { services: [] }; _tryRender(); });
  action$('pfp_list_installed', { scope: 'user', conversation_id: conversationId }).subscribe(d => { _pfpUserData = d || { packages: [] }; _tryRender(); });
  action$('pfp_list_installed', { scope: 'conversation', conversation_id: conversationId }).subscribe(d => { _pfpConvData = d || { packages: [] }; _tryRender(); });
  if (!window._cachedTools) {
    action$('get_tool_schemas', {}).subscribe(data => _renderResourcesFromSSE(data));
  }
}
function _renderResourcesFromSSE(data) {
  if (!data) return;
  if (data.user_role) window._userRole = data.user_role;
  if (typeof updateAdminSettingsButton === 'function') updateAdminSettingsButton();
  if (data.tools) { window._cachedTools = data.tools; return; }  // tool schemas response
  _lastResourcesData = data;
  _renderResourcesData(data);
}
async function _renderResourcesData(data) {
  try {
    const el = document.getElementById('resourcesContent');

    // ─────────────────────────────────────────────────────────────
    // LIVE sections (conversation state): Agents, Tasks, Flows,
    // Services, Relays. Built synchronously into `liveHtml`.
    // ─────────────────────────────────────────────────────────────
    let liveHtml = '';

    // Agents (conversation members)
    liveHtml += _sectionHeader(t('agents'), 'agent');
    if (data.agents && data.agents.length) {
      data.agents.forEach(function(a) {
        var isPrimary = a.active;
        var aName = escapeHtml(a.name);
        var aKeyLc = (a.name || '').toLowerCase();
        var primaryColor = isPrimary ? 'var(--pf-success)' : 'var(--pf-muted)';
        var textColor = isPrimary ? 'var(--pf-text)' : 'var(--pf-muted)';
        var primaryTitle = isPrimary ? t('primaryAgent') : t('setPrimaryAgent');
        var primaryArrow = isPrimary ? '&#9654;' : '&#9655;';
        var autoconvTag = a.autoconv ? '<span style="font-size:9px;color:var(--pf-success);margin-left:2px;">' + String.fromCodePoint(0x1F504) + '</span>' : '';
        // Hydrate the global cache through the same monotonic path used by
        // Resource polling must not touch the context gauge. The gauge is
        // updated only by live context events and the explicit /context view.
        liveHtml += '<div data-agent-name="' + aName + '" style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"'
          + ' oncontextmenu="showAgentMenu(event,\'' + aName + '\',\'' + escapeHtml(a.scope || '') + '\',' + (a.autoconv ? 'true' : 'false') + ');return false;">'
          + '<span style="cursor:pointer;color:' + primaryColor + ';font-size:11px;" title="' + primaryTitle + '"'
          + ' onclick="cmdAgentSelect(this.dataset.n).then(loadResources)" data-n="' + aName + '">' + primaryArrow + '</span>'
          + _scopeBadge(a.scope)
          + '<span style="color:' + textColor + ';font-size:12px;cursor:pointer;flex:1;"'
          + ' onclick="cmdAgentSelect(this.dataset.n).then(loadResources)" data-n="' + aName + '">' + aName + '</span>'
          + autoconvTag
          + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + escapeHtml(t('removeFromConversation')) + '"'
          + ' onclick="_removeAgentFromConv(this.dataset.n)" data-n="' + aName + '">&times;</span>'
          + '</div>';
        // Per-agent context-window gauge (persisted on the conversation,
        // updated in-place via setContextUsage on SSE message_meta/done).
        var _ctxUsage = (window._contextUsage || {})[aKeyLc];
        var _ctxHtml = (typeof renderCtxGauge === 'function' && _ctxUsage)
          ? renderCtxGauge(_ctxUsage) : '';
        liveHtml += '<div style="margin-left:24px;margin-bottom:3px;min-height:6px;" data-ctx-agent="' + escapeHtml(aKeyLc) + '">'
          + _ctxHtml
          + '</div>';
        // Show LLM service + assigned skills as small tags
        var aLlm = a.llm_service || '';
        var aSkills = a.assigned_skills || [];
        if (aLlm || aSkills.length) {
          liveHtml += '<div style="margin-left:24px;margin-bottom:3px;display:flex;flex-wrap:wrap;gap:3px;">';
          if (aLlm) {
            liveHtml += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:color-mix(in srgb, var(--pf-accent-2) 16%, var(--pf-panel));color:var(--pf-accent-2);">' + escapeHtml(aLlm) + '</span>';
          }
          aSkills.forEach(function(sk) {
            liveHtml += '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:color-mix(in srgb, var(--pf-accent) 16%, var(--pf-panel));color:var(--pf-accent);">' + escapeHtml(sk) + '</span>';
          });
          liveHtml += '</div>';
        }
      });
    } else {
      liveHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noAgents')) + ' <span style="color:var(--pf-accent);cursor:pointer;" onclick="showAddAgentToConvDialog()">+ ' + escapeHtml(t('add')) + '</span></div>';
    }
    liveHtml += _sectionFooter();
    // After the Agents section is rebuilt, refresh the header badge so its
    // inline gauge picks up any freshly-hydrated cache value.
    if (typeof updateActiveAgentBadge === 'function'
        && typeof selectedAgent !== 'undefined' && selectedAgent) {
      setTimeout(updateActiveAgentBadge, 0);
    }

    // ── Tasks (running instances in this conversation) ──
    // Always visible, even when empty - this is where users look for
    // 'what tasks are active in this conv right now'. No '+' here:
    // launching a task happens through the context menu of a task_def
    // entry in Tasks Repository (further below).
    liveHtml += _sectionHeader(t('tasks'), '_running', {
      hideCreate: true,
    });
    { const running = data.running_tasks || [];
      if (running.length) {
        running.forEach(t => {
          const statusColor = t.status === 'active' ? 'var(--pf-success)' : t.status === 'paused' ? 'var(--pf-warning)' : 'var(--pf-muted)';
          const statusIcon = t.status === 'active' ? '\u25B6' : t.status === 'paused' ? '\u23F8' : '\u23F9';
          const label = (t.task_def_name || (t.task || '').substring(0, 30) || t.task_id) + ' \u2192 ' + t.agent;
          liveHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showRunningTaskMenu(event,'${t.task_id}','${t.agent}','${t.status}');return false;">
            <span style="color:${statusColor};font-size:11px;">${statusIcon}</span>
            <span style="color:var(--pf-muted);font-size:11px;" title="${escapeHtml(t.task)}">${escapeHtml(label)}</span>
            <span style="color:var(--pf-muted);font-size:10px;">[${t.iterations}/${t.max_iterations}]</span>
          </div>`;
        });
      } else {
        liveHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noTasksRunning')) + '</div>';
      }
    }
    liveHtml += _sectionFooter();

    // ── Flows (running deployed instances; deploy a new one with '+',
    //    rebuild registry with ↻ since the deploy list is live state).
    //    Naming mirrors Tasks: this section = active state in the conv,
    //    "Flows Repository" below = catalog on disk.
    liveHtml += _sectionHeader(t('flows'), '_flow', {
      refreshOnclick: "event.stopPropagation();fireAction('reload_disk',{});setTimeout(loadResources,300)",
      refreshTitle: t('reloadFromDisk'),
      createTitle: t('deployFlow'),
    });
    if (data.flows && data.flows.length) {
      data.flows.forEach(f => {
        const statusIcon = f.status === 'running' ? '\u25B6' : f.status === 'stopped' ? '\u23F9' : '\u26A0';
        const statusColor = f.status === 'running' ? 'var(--pf-success)' : f.status === 'stopped' ? 'var(--pf-muted)' : 'var(--pf-danger)';
        const flowCtx = ` oncontextmenu="showFlowInstanceMenu(event,'${f.instance_id}','${f.status}','${f.scope}');return false;"`;
        liveHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${flowCtx}>
          ${_scopeBadge(f.scope)}<span style="color:${statusColor};font-size:11px;">${statusIcon} ${f.flow_name || f.instance_id}</span>
        </div>`;
      });
    } else {
      liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noDeployedFlows')) + '</div>';
    }
    liveHtml += _sectionFooter();

    // Services (install with '+', reload from disk with ↻ on the left)
    liveHtml += _sectionHeader(t('services'), '_svc', {
      refreshOnclick: "event.stopPropagation();fireAction('reload_disk',{});setTimeout(loadResources,300)",
      refreshTitle: t('reloadFromDisk'),
      createTitle: t('installService'),
    });
    if (data.services && data.services.length) {
      data.services.forEach(s => {
        const statusDot = !s.enabled ? '\u{1F534}'
          : s.started ? '\u{1F7E2}' : '\u{1F7E1}';
        let dockerTag = '';
        if (s.relay_info && s.relay_info.containerized) {
          const img = s.relay_info.docker_image;
          dockerTag = ' \u{1F433}' + (img ? ` [${img}]` : '');
        }
        const svcCtx = ` oncontextmenu="showServiceMenu(event,'${s.service_id}','${s.scope}',${s.enabled});return false;"`;
        liveHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${svcCtx}>
          ${_scopeBadge(s.scope)}<span style="color:var(--pf-muted);font-size:11px;">${statusDot} <b>${s.service_id}</b> <span style="color:var(--pf-muted)">(${s.service_type})</span>${dockerTag}</span>
        </div>`;
      });
    } else {
      liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noServicesInstalled')) + '</div>';
    }
    liveHtml += _sectionFooter();

    // Installed PawFlow Packages. Package objects still appear in their normal
    // sections; this section gives users the package-level provenance surface.
    liveHtml += _sectionHeader(t('pfpPackages'), '_pfp', {
      createOnclick: "_showPfpInstallDialog()",
      createTitle: t('pfpInstallPackage'),
      refreshOnclick: "event.stopPropagation();loadResources()",
    });
    { const packages = data.pfp_packages || [];
      if (packages.length) {
        packages.forEach(pkg => {
          const objects = pkg.objects || [];
          const blockers = pkg.blocked_by || [];
          const scope = pkg._scope || pkg.scope || 'user';
          const objectLabel = objects.length + ' ' + t('objects');
          const pkgName = escapeHtml((pkg.package || '') + '@' + (pkg.version || ''));
          const packageId = _pfpAttr(pkg.package || '');
          const packageScope = _pfpAttr(scope);
          const objectTitle = _pfpAttr(objects.map(_pfpObjectLabel).join('\n'));
          liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" title="' + objectTitle + '">'
            + _scopeBadge(scope)
            + '<span style="color:var(--pf-text);font-size:12px;flex:1;">' + pkgName + '</span>'
            + (blockers.length ? '<span style="color:var(--pf-warning);font-size:10px;" title="' + _pfpAttr(t('pfpBlockingDependents')) + '">!' + escapeHtml(String(blockers.length)) + '</span>' : '')
            + '<span style="color:var(--pf-muted);font-size:10px;">[' + escapeHtml(objectLabel) + ']</span>'
            + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + escapeHtml(t('uninstall')) + '" onclick="_showPfpUninstallDialog(this.dataset.package, this.dataset.scope)" data-package="' + packageId + '" data-scope="' + packageScope + '">&times;</span>'
            + '</div>';
        });
      } else {
        liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noPfpPackagesInstalled')) + '</div>';
      }
    }
    liveHtml += _sectionFooter();

    // Relay bindings for this conversation (always show section)
    {
      var rbCollapsed = _isSectionCollapsed('_relay');
      var rbArrow = rbCollapsed ? '\u25B6' : '\u25BC';
      var rbDisplay = rbCollapsed ? 'none' : 'block';
      liveHtml += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
        + '<span style="cursor:pointer;color:var(--pf-resource-heading, var(--pf-accent));font-weight:600;user-select:none;" onclick="_toggleSection(\'_relay\')">'
        + '<span id="res-arrow-_relay">' + rbArrow + '</span> ' + escapeHtml(t('relays')) + '</span>'
        + '<span style="cursor:pointer;font-size:13px;color:var(--pf-accent);padding:0 4px;" onclick="_showRelayLinkDialog()" title="' + escapeHtml(t('linkRelay')) + '">+</span>'
        + '</div><div id="res-section-_relay" style="display:' + rbDisplay + ';">';
      var _rb = (data.relay_bindings && data.relay_bindings.linked) ? data.relay_bindings : {linked:{}, default:{}};
      var _rbLinked = _rb.linked || {};
      var _rbDefaults = _rb.default || {};
      var _rbDetails = _rb.details || {};
      // Collect all unique relay IDs and which scopes they belong to
      var _allRelays = {};  // relay_id → [scope1, scope2, ...]
      Object.keys(_rbLinked).forEach(function(scope) {
        (_rbLinked[scope] || []).forEach(function(rid) {
          if (!_allRelays[rid]) _allRelays[rid] = [];
          _allRelays[rid].push(scope);
        });
      });
      var _relayIds = Object.keys(_allRelays);
      if (_relayIds.length) {
        _relayIds.forEach(function(rid) {
          var scopes = _allRelays[rid];
          var isConvDefault = _rbDefaults['*'] === rid;
          var agentDefaults = [];
          Object.keys(_rbDefaults).forEach(function(scope) {
            if (scope !== '*' && _rbDefaults[scope] === rid) agentDefaults.push(scope);
          });
          var star = isConvDefault ? ' \u2605' : '';
          var agentTags = '';
          scopes.forEach(function(s) {
            if (s !== '*') agentTags += ' <span style="font-size:9px;color:var(--pf-accent);background:color-mix(in srgb, var(--pf-accent) 14%, var(--pf-panel));padding:1px 4px;border-radius:3px;">' + escapeHtml(s) + '</span>';
          });
          agentDefaults.forEach(function(a) {
            agentTags += ' <span style="font-size:9px;color:var(--pf-success);" title="' + escapeHtml(t('defaultForAgent', { agent: a })) + '">\u2605' + escapeHtml(a) + '</span>';
          });
          var color = isConvDefault ? 'var(--pf-success)' : 'var(--pf-muted)';
          var icon = isConvDefault ? '\u25C9' : '\u25CB';
          var titleText = isConvDefault ? t('defaultRelay') : t('setDefaultRelay');
          var clickDefault = isConvDefault ? '' : ' onclick="fireAction(\'relay_default\',{relay_id:\'' + escapeHtml(rid) + '\'}); setTimeout(loadResources, 500)"';
          var det = _rbDetails[rid] || {};
          var connDot = det.connected ? '\u{1F7E2}' : '\u{1F534}';
          var pathInfo = '';
          if (det.root) pathInfo += '<div style="font-size:10px;color:var(--pf-muted);margin-left:20px;">docker: <code>' + escapeHtml(det.root) + '</code></div>';
          if (det.host_root) pathInfo += '<div style="font-size:10px;color:var(--pf-muted);margin-left:20px;">local: <code>' + escapeHtml(det.host_root) + '</code></div>';
          var _rbDefaultLocal = (_rb.default_local || {})[rid] || {};
          var _detWithLocal = Object.assign({}, det, {_default_local: _rbDefaultLocal});
          var _detJson = escapeHtml(JSON.stringify(_detWithLocal).replace(/'/g, "\\'"));
          liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="_showRelayInfoDialog(\'' + escapeHtml(rid) + '\',' + _detJson + ');return false;">'
            + '<span style="color:' + color + ';font-size:11px;cursor:pointer;" title="' + titleText + '"' + clickDefault + '>' + icon + '</span>'
            + '<span style="font-size:11px;">' + connDot + '</span>'
            + '<span style="color:' + color + ';font-size:12px;">' + escapeHtml(rid) + star + '</span>'
            + agentTags
            + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + escapeHtml(t('unlink')) + '"'
            + ' onclick="fireAction(\'relay_unlink\',{relay_id:\'' + escapeHtml(rid) + '\'}); setTimeout(loadResources, 500)">&times;</span>'
            + '</div>' + pathInfo;
        });
      } else {
        liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noRelaysLinked')) + '</div>';
      }
      liveHtml += _sectionFooter();
    }

    // Filesystem bindings: rclone is mounted inside linked relays; native API
    // filesystems are made available directly to tools.
    {
      var fsCollapsed = _isSectionCollapsed('_remote_fs');
      var fsArrow = fsCollapsed ? '\u25B6' : '\u25BC';
      var fsDisplay = fsCollapsed ? 'none' : 'block';
      liveHtml += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
        + '<span style="cursor:pointer;color:var(--pf-resource-heading, var(--pf-accent));font-weight:600;user-select:none;" onclick="_toggleSection(\'_remote_fs\')">'
        + '<span id="res-arrow-_remote_fs">' + fsArrow + '</span> ' + escapeHtml(t('remoteFilesystems')) + '</span>'
        + '<span style="cursor:pointer;font-size:13px;color:var(--pf-accent);padding:0 4px;" onclick="_showRemoteFsLinkDialog()" title="' + escapeHtml(t('linkFilesystem')) + '">+</span>'
        + '</div><div id="res-section-_remote_fs" style="display:' + fsDisplay + ';">';
      var _remoteFs = data.remote_filesystems || { linked: [], available: [] };
      var _linkedFs = _remoteFs.linked || [];
      if (_linkedFs.length) {
        _linkedFs.forEach(function(s) {
          var serviceId = escapeHtml(s.service_id || '');
          var scope = escapeHtml(s.scope || 'user');
          var isRclone = s.service_type === 'rcloneFilesystem';
          var mountPath = escapeHtml(isRclone ? (s.mount_path || '') : '');
          var tag = escapeHtml(isRclone ? 'rclone' : (s.service_type || 'filesystem'));
          var enabledDot = s.enabled === false ? '\u{1F534}' : '\u{1F7E2}';
          liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">'
            + _scopeBadge(scope)
            + '<span style="font-size:11px;">' + enabledDot + '</span>'
            + '<span style="color:var(--pf-text);font-size:12px;flex:1;">' + serviceId + '</span>'
            + '<span style="font-size:9px;color:var(--pf-muted);background:color-mix(in srgb, var(--pf-muted) 14%, var(--pf-panel));padding:1px 4px;border-radius:3px;">' + tag + '</span>'
            + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + escapeHtml(t('unlink')) + '"'
            + ' onclick="_unlinkRemoteFs(this.dataset.serviceId)" data-service-id="' + serviceId + '">&times;</span>'
            + '</div>'
            + (mountPath ? '<div style="font-size:10px;color:var(--pf-muted);margin-left:24px;"><code>' + mountPath + '</code></div>' : '<div style="font-size:10px;color:var(--pf-muted);margin-left:24px;">' + escapeHtml(t('availableToTools')) + '</div>');
        });
      } else {
        liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noRemoteFilesystemsLinked')) + '</div>';
      }
      liveHtml += _sectionFooter();
    }

    // Summarizer binding/effective service for this conversation.
    {
      var smCollapsed = _isSectionCollapsed('_summarizer');
      var smArrow = smCollapsed ? '\u25B6' : '\u25BC';
      var smDisplay = smCollapsed ? 'none' : 'block';
      liveHtml += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
        + '<span style="cursor:pointer;color:var(--pf-resource-heading, var(--pf-accent));font-weight:600;user-select:none;" onclick="_toggleSection(\'_summarizer\')">'
        + '<span id="res-arrow-_summarizer">' + smArrow + '</span> ' + escapeHtml(t('summarizer')) + '</span>'
        + '<span style="cursor:pointer;font-size:13px;color:var(--pf-accent);padding:0 4px;" onclick="_showSummarizerLinkDialog()" title="' + escapeHtml(t('linkSummarizer')) + '">+</span>'
        + '</div><div id="res-section-_summarizer" style="display:' + smDisplay + ';">';
      var _sm = data.summarizer || {};
      var _smEffective = _sm.effective || null;
      var _smExplicit = !!_sm.explicit;
      if (_smEffective) {
        var _smColor = _smExplicit ? 'var(--pf-success)' : 'var(--pf-muted)';
        var _smMode = _smExplicit ? t('explicitSummarizer') : t('autoSummarizer');
        var _smLlm = _smEffective.llm_service ? ' \u2192 ' + escapeHtml(_smEffective.llm_service) : '';
        liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="_showSummarizerMenu(event,' + (_smExplicit ? 'true' : 'false') + ');return false;">'
          + _scopeBadge(_smEffective.scope)
          + '<span style="color:' + _smColor + ';font-size:12px;flex:1;">' + escapeHtml(_smEffective.service_id) + _smLlm + '</span>'
          + '<span style="font-size:9px;color:' + _smColor + ';background:color-mix(in srgb, ' + _smColor + ' 14%, var(--pf-panel));padding:1px 4px;border-radius:3px;">' + escapeHtml(_smMode) + '</span>'
          + (_smExplicit ? '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + escapeHtml(t('unlink')) + '" onclick="_unlinkSummarizer()">&times;</span>' : '')
          + '</div>';
      } else {
        liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noSummarizerEffective')) + '</div>';
      }
      liveHtml += _sectionFooter();
    }

    // ─────────────────────────────────────────────────────────────
    // REPO sections (catalog on disk): Agent Repo, Skills Repo,
    // Prompts Repo, Voices Repo, Tasks Repo, MCP Repo, Tools Repo,
    // Flows Repo. Built synchronously into `repoHtml`.
    // Variables + Secrets come BEFORE these via the async block.
    // ─────────────────────────────────────────────────────────────
    let repoHtml = '';

    // Agent Repository (repo agents not yet in conv, collapsed by default)
    repoHtml += _repoSectionHeader(t('agentRepository'), "_agent_repo", {
      createOnclick: "showResourceCreator('agent')",
      createTitle: t('createNewAgent'),
    });
    if (!_isSectionCollapsed("_agent_repo")) {
      var repoAgents = (data.repo_agents || []).filter(function(a) { return !a.in_conversation; });
      if (repoAgents.length) {
        repoAgents.forEach(function(a) {
          var aName = escapeHtml(a.name);
          repoHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">'
            + _scopeBadge(a.scope)
            + '<span style="color:var(--pf-muted);font-size:12px;flex:1;">' + aName + '</span>'
            + '<span style="color:var(--pf-accent);font-size:10px;cursor:pointer;padding:0 4px;" title="' + escapeHtml(t('addToConversation')) + '"'
            + ' onclick="showAddAgentToConvDialog(this.dataset.n)" data-n="' + aName + '">+</span>'
            + '</div>';
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('allAgentsInConversation')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Skills Repository ──
    repoHtml += _repoSectionHeader(t('skillsRepository'), 'skill', {
      createOnclick: "showResourceCreator('skill')",
    });
    { const allSkills = data.skills || [];
      if (allSkills.length) {
        allSkills.forEach(s => {
          const assignedTo = s.assigned_to || [];
          const assignedTag = assignedTo.length ? ' <span style="color:var(--pf-muted);font-size:9px;">\u2192 ' + assignedTo.map(escapeHtml).join(', ') + '</span>' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'skill','${escapeHtml(s.name)}','${s.scope||''}');return false;">
            ${_scopeBadge(s.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(s.name)}${assignedTag}</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noSkillsDefined')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Prompts Repository (click to paste into chat input) ──
    repoHtml += _repoSectionHeader(t('promptsRepository'), 'prompt', {
      createOnclick: "showResourceCreator('prompt')",
    });
    if (!_isSectionCollapsed('prompt')) {
      const prompts = data.prompts || [];
      if (prompts.length) {
        prompts.forEach(p => {
          const title = p.title || p.name;
          const icon = p.has_parameters ? '\u{1F4DD}' : '\u{1F4CB}';
          const desc = p.description ? ' title="' + escapeHtml(p.description) + '"' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer"${desc}
            onclick="_usePrompt('${escapeHtml(p.name)}',${p.has_parameters})" oncontextmenu="showResourceMenu(event,'prompt','${p.name}','${p.scope||''}');return false;">
            ${_scopeBadge(p.scope)}<span style="font-size:11px">${icon}</span>
            <span style="font-size:12px;color:var(--pf-text)">${escapeHtml(title)}</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noPrompts')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Themes Repository (directory CSS resources) ──
    repoHtml += _repoSectionHeader(t('themesRepository'), 'theme', {
      createOnclick: "showThemeCreator()",
      createTitle: t('addTheme'),
    });
    if (!_isSectionCollapsed('theme')) {
      const themes = data.themes || [];
      if (themes.length) {
        themes.forEach(t => {
          const ref = t.ref || ((t.scope || 'user') + ':' + t.name);
          const builtin = !!t.builtin;
          const builtinArg = builtin ? 'true' : 'false';
          const cssLabel = (t.css_length || 0) + ' css';
          const desc = t.description ? ' title="' + escapeHtml(t.description) + '"' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer"${desc}
            onclick="_applyThemeFromResource('${escapeHtml(ref)}')" oncontextmenu="_showThemeMenu(event,'${escapeHtml(ref)}',${builtinArg},'${escapeHtml(t.scope || '')}');return false;">
            ${_scopeBadge(t.scope)}<span style="font-size:11px;color:var(--pf-accent);">\u25A3</span>
            <span style="font-size:12px;color:var(--pf-text);flex:1;">${escapeHtml(t.title || t.name)}</span>
            <span style="color:var(--pf-muted);font-size:10px;">${cssLabel}</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noThemes')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Voices Repository (cloned voices, user scope) ──
    repoHtml += _repoSectionHeader(t('voicesRepository'), 'voice', {
      createOnclick: "showResourceCreator('voice')",
    });
    if (!_isSectionCollapsed('voice')) {
      const voices = data.voices || [];
      if (voices.length) {
        voices.forEach(v => {
          const paradigm = v.paradigm || 'zero-shot';
          const pBadge = paradigm === 'voice_id' ? 'id' : 'zs';
          const pColor = paradigm === 'voice_id' ? 'var(--pf-success)' : 'var(--pf-muted)';
          const prov = v.provider ? ` (${escapeHtml(v.provider)})` : '';
          const previewUrl = v.ref_audio_fid
            ? `/files/${encodeURIComponent(v.ref_audio_fid)}` : '';
          const previewBtn = previewUrl
            ? `<span style="cursor:pointer;color:var(--pf-accent);font-size:11px;padding:0 4px;" title="${escapeHtml(t('previewReferenceAudio'))}" onclick="_previewVoice('${escapeHtml(previewUrl)}')">\u25B6</span>`
            : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" title="${escapeHtml(v.provider)} \u2014 ${paradigm}">
            <span style="color:${pColor};font-size:9px;font-weight:600;border:1px solid ${pColor};border-radius:3px;padding:0 3px;">${pBadge}</span>
            <span style="color:var(--pf-text);font-size:12px;flex:1;">\u{1F399} ${escapeHtml(v.name)}<span style="color:var(--pf-muted);font-size:10px;">${prov}</span></span>
            ${previewBtn}
            <span style="cursor:pointer;color:var(--pf-muted);font-size:11px;padding:0 4px;" title="${escapeHtml(t('renameVoiceClone'))}" onclick="_renameVoiceClone('${escapeHtml(v.name)}')">\u270E</span>
            <span style="cursor:pointer;color:var(--pf-danger);font-size:11px;padding:0 4px;" title="${escapeHtml(t('deleteVoiceClone'))}" onclick="_deleteVoiceClone('${escapeHtml(v.name)}')">\u2716</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + t('noVoices') + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Tasks Repository (definitions, muted style like Agent Repository) ──
    repoHtml += _repoSectionHeader(t('tasksRepository'), 'task_def', {
      createOnclick: "showResourceCreator('task_def')",
    });
    { const allTasks = data.task_defs || [];
      if (allTasks.length) {
        allTasks.forEach(t => {
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'task_def','${escapeHtml(t.name)}','${t.scope||''}');return false;">
            ${_scopeBadge(t.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;" title="${escapeHtml(t.description)}">${escapeHtml(t.name)}</span>
            <span style="color:var(--pf-muted);font-size:10px;">[${t.default_interval}]</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noTaskDefinitions')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── MCP Repository (all in-scope MCPs are auto-active — no linking) ──
    // Presence in the repo == available to the conversation. Any MCP visible
    // in global + user + conv scope is automatically registered.
    repoHtml += _repoSectionHeader(t('mcpRepository'), '_mcp_repo', {
      createOnclick: "showResourceCreator('mcp')",
      createTitle: t('createNew'),
    });
    if (!_isSectionCollapsed('_mcp_repo')) {
      const mcps = data.mcp_servers || [];
      repoHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:4px;cursor:pointer;color:var(--pf-accent-2);font-size:11px;" onclick="_showToolMcpFilterDialog(\'\', \'conversation\')">\u2699 ' + escapeHtml(t('configureAvailability')) + '</div>';
      if (mcps.length) {
        mcps.forEach(m => {
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'mcp','${escapeHtml(m.name)}','${m.scope||''}');return false;">
            ${_scopeBadge(m.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(m.name)}</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noMcpServers')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Agent Hooks Repository (runtime hooks selectable from conversation config) ──
    repoHtml += _repoSectionHeader(t('agentHooksRepository'), 'agent_hook', {
      createOnclick: "showResourceCreator('agent_hook')",
      createTitle: t('createNewAgentHook'),
    });
    if (!_isSectionCollapsed('agent_hook')) {
      const hooks = data.agent_hooks || [];
      repoHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:4px;cursor:pointer;color:var(--pf-accent-2);font-size:11px;" onclick="_showAgentHooksDialog()">\u2699 ' + escapeHtml(t('configureBindings')) + '</div>';
      if (hooks.length) {
        hooks.forEach(h => {
          const events = Array.isArray(h.events) ? h.events.join(', ') : '';
          const desc = h.description ? ' title="' + escapeHtml(h.description) + '"' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;"${desc} oncontextmenu="showResourceMenu(event,'agent_hook','${escapeHtml(h.name)}','${h.scope||''}');return false;">
            ${_scopeBadge(h.scope)}<span style="color:var(--pf-accent);font-size:11px">\u2693</span>
            <span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(h.name)}</span>
            <span style="color:var(--pf-muted);font-size:10px;">${escapeHtml(events)}</span>
          </div>`;
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noAgentHooks')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ── Tools Repository (always available, no linking) ──
    repoHtml += _repoSectionHeader(t('toolsRepository'), '_tool', {
      createOnclick: "showResourceCreator('_tool')",
      createTitle: t('createNewTool'),
    });
    if (!_isSectionCollapsed('_tool')) {
      const tools = window._cachedTools || [];
      repoHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:4px;cursor:pointer;color:var(--pf-accent-2);font-size:11px;" onclick="_showToolMcpFilterDialog(\'\', \'conversation\')">\u2699 ' + escapeHtml(t('configureAvailability')) + '</div>';
      tools.forEach(t => {
        repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer" onclick="showToolCallDialog('${escapeHtml(t.name)}')">
          <span style="color:var(--pf-accent);font-size:11px">\u26A1</span>
          <span style="font-size:12px;color:var(--pf-text)">${escapeHtml(t.name)}</span>
        </div>`;
      });
      if (!tools.length) repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted)">' + escapeHtml(t('loading')) + '</div>';
    }
    repoHtml += _sectionFooter();

    // ── Flows Repository (flow templates on disk under
    //    data/repository/flows/*.json) ──
    repoHtml += _repoSectionHeader(t('flowsRepository'), '_flow_repo', {
      createOnclick: "showDeployFlowDialog()",
      createTitle: t('deployFlowFromTemplate'),
    });
    { const tpls = data.flow_templates || [];
      if (tpls.length) {
        const byPackage = new Map();
        tpls.forEach(t => {
          const packageName = t.package || 'default';
          if (!byPackage.has(packageName)) byPackage.set(packageName, []);
          byPackage.get(packageName).push(t);
        });
        Array.from(byPackage.keys()).sort((a, b) => a.localeCompare(b)).forEach(packageName => {
          const flows = byPackage.get(packageName).slice().sort((a, b) => {
            const byName = String(a.name || '').localeCompare(String(b.name || ''));
            if (byName) return byName;
            return String(a.version || '').localeCompare(String(b.version || ''));
          });
          repoHtml += _renderFlowPackageGroup(packageName, flows);
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noFlowTemplates')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    // ─────────────────────────────────────────────────────────────
    // Async: Variables + Secrets (rendered between live & repo) and
    // Linked Accounts (appended at the very end).
    // ─────────────────────────────────────────────────────────────
    if (!liveHtml && !repoHtml) {
      liveHtml = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noResourcesHint')) + '</div>';
    }
    rxjs.forkJoin([
      action$('list_params_secrets', { conversation_id: conversationId }).pipe(rxjs.catchError(() => rxjs.of({}))),
      action$('list_linked_accounts', { conversation_id: conversationId }).pipe(rxjs.catchError(() => rxjs.of({}))),
    ]).subscribe(([ps, linksData]) => {
      let varSecHtml = '';
      if (ps.parameters && ps.parameters.length) {
        varSecHtml += _sectionHeader(t('variables'), '_param');
        ps.parameters.forEach(p => {
          const truncVal = p.value.length > 30 ? p.value.substring(0, 30) + '...' : p.value;
          varSecHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${p.key}','${p.scope}');return false;">
            ${_scopeBadge(p.scope)}<span style="color:var(--pf-muted);font-size:11px;"><b>${escapeHtml(p.key)}</b> = ${escapeHtml(truncVal)}</span>
          </div>`;
        });
        varSecHtml += _sectionFooter();
      }
      if (ps.secrets && ps.secrets.length) {
        varSecHtml += _sectionHeader(t('secrets'), '_secret');
        ps.secrets.forEach(s => {
          varSecHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${s.key}','${s.scope}',true);return false;">
            ${_scopeBadge(s.scope)}<span style="color:var(--pf-muted);font-size:11px;"><b>${escapeHtml(s.key)}</b> = ********</span>
          </div>`;
        });
        varSecHtml += _sectionFooter();
      }
      const links = (linksData && linksData.links) || {};
      const linkKeys = Object.keys(links);
      let linksHtml = '<div style="margin-top:6px;padding:4px 6px;font-size:11px;color:var(--pf-muted);border-top:1px solid var(--pf-border);">';
      linksHtml += '<b>' + escapeHtml(t('linkedAccounts')) + '</b>';
      if (linkKeys.length) {
        linkKeys.forEach(provider => {
          linksHtml += `<div style="display:flex;align-items:center;gap:6px;margin:3px 0 3px 8px;">
            <span style="font-size:11px;color:var(--pf-text);">${escapeHtml(provider)}</span>
            <span style="font-size:10px;color:var(--pf-muted);">${escapeHtml(links[provider])}</span>
            <span style="cursor:pointer;font-size:10px;color:var(--pf-danger);" title="${escapeHtml(t('unlink'))}" onclick="cmdResourceAction('unlink_account',{provider:'${provider}'}).then(loadResources)">\u2715</span>
          </div>`;
        });
      } else {
        linksHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noLinkedAccounts')) + '</div>';
      }
      linksHtml += '</div>';
      // Final assembly: live → variables/secrets → repos → linked accounts
      const fullHtml = liveHtml + varSecHtml + repoHtml + linksHtml;
      // Only update DOM if content actually changed (prevents flash/blink)
      if (el.innerHTML !== fullHtml) el.innerHTML = fullHtml;
    });
  } catch (e) {
    document.getElementById('resourcesContent').innerHTML = '';
  }
}

// ── Resource context menu ────────────────────────────────────────────────────────────
function _positionMenu(menu, e) {
  // Position context menu, flip up if it would overflow the viewport
  document.body.appendChild(menu);
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.bottom > window.innerHeight) {
      menu.style.top = Math.max(0, e.clientY - rect.height) + 'px';
    }
    if (rect.right > window.innerWidth) {
      menu.style.left = Math.max(0, e.clientX - rect.width) + 'px';
    }
  });
}

function showResourceMenu(e, rtype, name, scope, autoconv) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);

  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  const sep = () => {
    const s = document.createElement('div');
    s.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(s);
  };

  // View config — always available (read-only for non-admin on globals)
  item('\u{1F441} ' + t('viewWithEllipsis'), () => showResourceEditor(rtype, name, true));
  // Edit — admin can edit globals, owners can edit their own
  if (_canEditScope(scope)) {
    item('\u270F ' + t('editWithEllipsis'), () => showResourceEditor(rtype, name));
  }
  if (rtype === 'agent') {
    item('\u25B6 ' + t('select'), () => cmdAgentSelect(name));
    item('\u2699 ' + t('toolsMcpOverrideMenu'), () => _showToolMcpFilterDialog(name, 'agent'));
    if (autoconv) {
      item('\u23F9 ' + t('autoconvOff'), () => {
        action$('random_thought', { sub: 'off', agent: name }).subscribe(d => {
          addMsg('system', d.error || t('autoconvDisabledFor', { agent: name }));
          loadResources();
        });
      });
    } else {
      item('\u{1F504} ' + t('autoconvOnMenu'), () => {
        const freq = prompt(t('autoconvFrequencyPrompt'), '6/1m');
        if (!freq) return;
        action$('random_thought', { sub: 'on', agent: name, frequency: freq }).subscribe(d => {
          addMsg('system', d.error || t('autoconvEnabledFor', { agent: name, freq: freq }));
          loadResources();
        });
      });
    }
  }
  if (rtype === 'skill') {
    item('\u{1F517} ' + t('assignToAgentMenu'), () => _showSkillAssignDialog(name));
  }
  if (rtype === 'task_def') {
    item('\u25B6 ' + t('assignToAgentMenu'), () => _showAssignDialog(name));
    item('\u{1F4DC} ' + t('viewLogMenu'), () => _showTaskDefLog(name));
  }
  sep();
  // Copy between scopes
  if (_isAdmin()) item('\u2191 ' + t('copyToGlobal'), () => _copyResource(rtype, name, 'global'));
  if (scope !== 'user') item('\u2191 ' + t('copyToUser'), () => _copyResource(rtype, name, 'user'));
  if (scope !== 'conversation') item('\u2191 ' + t('copyToConversation'), () => _copyResource(rtype, name, 'conversation'));
  if (_canEditScope(scope)) {
    sep();
    item('\u{1F5D1} ' + t('delete'), () => _deleteResource(rtype, name, scope), true);
  }

  setTimeout(() => document.addEventListener('click', function _close() {
    menu.remove(); document.removeEventListener('click', _close);
  }), 0);
}

function _copyResource(rtype, name, targetScope) {
  action$('copy_resource_scope', { resource_type: rtype,
    name, target_scope: targetScope }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('resourceCopiedToScope', { type: rtype, name: name, scope: targetScope }));
    loadResources();
  });
}

function _deleteResource(rtype, name, scope) {
  if (!confirm(t('resourceDeleteConfirm', { type: rtype, name: name, scope: scope }))) return;
  action$('delete_resource', { resource_type: rtype,
    name, scope: scope || 'user' }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else addMsg('system', t('resourceDeleted', { type: rtype, name: name }));
    loadResources();
  });
}

function _toolMcpRows(items, selectedNames, cls, disabled, mode, dynamicNames) {
  const dis = disabled ? ' disabled' : '';
  const roStyle = disabled ? 'opacity:0.55;cursor:not-allowed;' : '';
  dynamicNames = dynamicNames || [];
  if (!items.length) return '<div style="color:var(--pf-muted);font-size:11px;margin:3px 0 6px 18px;">' + escapeHtml(t('none')) + '</div>';
  return items.map(function(item) {
    const name = item.name || '';
    const isExternalDynamic = item.source === 'dynamic' && item.scope !== 'conversation';
    let isChecked = false;
    if (mode === 'mcp_allow' || mode === 'tool_selected') {
      isChecked = selectedNames.indexOf(name) >= 0;
    } else if (mode === 'tool_default') {
      isChecked = isExternalDynamic
        ? dynamicNames.indexOf(name) >= 0
        : selectedNames.indexOf(name) < 0;
    }
    const checked = isChecked ? ' checked' : '';
    const meta = item.source || item.transport || item.scope || '';
    return '<label style="display:flex;align-items:center;gap:6px;margin:2px 0 2px 18px;font-size:12px;color:var(--pf-text);' + roStyle + '">'
      + '<input type="checkbox" class="' + cls + '" data-name="' + escapeHtml(name) + '" data-source="' + escapeHtml(item.source || '') + '" data-scope="' + escapeHtml(item.scope || '') + '"' + checked + dis + ' style="accent-color:var(--pf-accent);"/> '
      + '<span style="color:var(--pf-text);">' + escapeHtml(name) + '</span>'
      + (meta ? '<span style="color:var(--pf-muted);font-size:10px;">' + escapeHtml(meta) + '</span>' : '')
      + '</label>';
  }).join('');
}

function _defaultSelectedTools(items, filters) {
  const disabledTools = filters.disabled_tools || [];
  const enabledDynamic = filters.enabled_dynamic_tools || [];
  return (items || []).filter(function(item) {
    const name = item.name || '';
    const isExternalDynamic = item.source === 'dynamic' && item.scope !== 'conversation';
    return isExternalDynamic ? enabledDynamic.indexOf(name) >= 0 : disabledTools.indexOf(name) < 0;
  }).map(function(item) { return item.name || ''; }).filter(Boolean);
}

function _renderToolMcpAgentOverride() {
  const data = window._toolMcpFilterData || {};
  const filters = data.filters || {};
  const agents = data.agents || [];
  const agent = document.getElementById('tmf-agent')?.value || '';
  const target = document.getElementById('tmf-agent-panel');
  if (!target) return;
  if (!agent) {
    target.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('selectAgentOverride')) + '</div>';
    return;
  }
  const ov = ((filters.agent_overrides || {})[agent]) || {};
  const toolsCfg = ov.tools || { mode: 'inherit', selected: [] };
  const mcpsCfg = ov.mcps || { mode: 'inherit', enabled: [] };
  const cfgCustom = toolsCfg.mode === 'custom' || mcpsCfg.mode === 'custom';
  const customEl = document.getElementById('tmf-agent-custom');
  const custom = customEl ? customEl.checked : cfgCustom;
  const toolMode = custom ? 'tool_selected' : 'tool_default';
  const toolNames = custom
    ? (cfgCustom ? (toolsCfg.selected || []) : _defaultSelectedTools(data.tools || [], filters))
    : (filters.disabled_tools || []);
  const dynNames = custom ? [] : (filters.enabled_dynamic_tools || []);
  const mcpNames = custom
    ? (cfgCustom ? (mcpsCfg.enabled || []) : (filters.enabled_mcps || []))
    : (filters.enabled_mcps || []);
  target.innerHTML = '<label style="display:flex;align-items:center;gap:6px;margin-bottom:8px;color:var(--pf-text);font-size:12px;">'
    + '<input id="tmf-agent-custom" type="checkbox"' + (custom ? ' checked' : '') + ' onchange="_renderToolMcpAgentOverride()" style="accent-color:var(--pf-accent);"/> ' + escapeHtml(t('overrideConversationDefaultsFor', { agent: agent })) + '</label>'
    + '<div style="color:var(--pf-muted);font-size:11px;margin-top:6px;">' + escapeHtml(t('tools')) + '</div>'
    + _toolMcpRows(data.tools || [], toolNames, 'tmf-agent-tool', !custom, toolMode, dynNames)
    + '<div style="color:var(--pf-muted);font-size:11px;margin-top:8px;">' + escapeHtml(t('mcpServers')) + '</div>'
    + _toolMcpRows(data.mcps || [], mcpNames, 'tmf-agent-mcp', !custom, 'mcp_allow');
}

function _collectDisabled(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return !el.checked;
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

function _collectEnabled(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return el.checked;
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

function _collectDisabledDefaultTools(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return !el.checked && !(el.dataset.source === 'dynamic' && el.dataset.scope !== 'conversation');
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

function _collectEnabledExternalDynamicTools(cls) {
  return Array.from(document.querySelectorAll('.' + cls)).filter(function(el) {
    return el.checked && el.dataset.source === 'dynamic' && el.dataset.scope !== 'conversation';
  }).map(function(el) { return el.dataset.name || ''; }).filter(Boolean);
}

async function _showToolMcpFilterDialog(agentName, mode) {
  mode = mode || (agentName ? 'agent' : 'conversation');
  let data = {};
  try {
    data = await rxjs.firstValueFrom(action$('get_tool_mcp_filters', { conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); return; }
  } catch (e) { addMsg('error', e.message); return; }
  window._toolMcpFilterData = data;
  const filters = data.filters || {};
  const agents = data.agents || [];
  const selected = agentName || agents[0] || '';
  const showConv = mode !== 'agent';
  const showAgent = mode !== 'conversation';
  const title = showConv && showAgent ? t('toolsMcpAvailability')
    : showConv ? t('conversationToolsMcpAvailability')
    : t('toolsMcpOverrideFor', { agent: selected });
  let overlay = document.getElementById('toolMcpFilterOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'toolMcpFilterOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:680px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(title) + '</h3>'
    + '<button onclick="document.getElementById(\'toolMcpFilterOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button></div>'
    + (showConv ? '<div style="color:var(--pf-accent);font-size:12px;font-weight:600;margin:8px 0 4px;">' + escapeHtml(t('conversationDefaults')) + '</div>'
      + '<div style="color:var(--pf-muted);font-size:11px;margin-top:6px;">' + escapeHtml(t('tools')) + '</div>'
      + _toolMcpRows(data.tools || [], filters.disabled_tools || [], 'tmf-conv-tool', false, 'tool_default', filters.enabled_dynamic_tools || [])
      + '<div style="color:var(--pf-muted);font-size:11px;margin-top:8px;">' + escapeHtml(t('mcpServers')) + '</div>'
      + _toolMcpRows(data.mcps || [], filters.enabled_mcps || [], 'tmf-conv-mcp', false, 'mcp_allow') : '')
    + (showConv && showAgent ? '<div style="border-top:1px solid color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel));margin:14px 0 10px;"></div>' : '')
    + (showAgent ? '<div style="color:var(--pf-accent);font-size:12px;font-weight:600;margin-bottom:6px;">' + escapeHtml(t('agentOverride')) + '</div>'
      + '<select id="tmf-agent" onchange="_renderToolMcpAgentOverride()" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-bottom:8px;' + (agentName ? 'display:none;' : '') + '">'
      + '<option value="">' + escapeHtml(t('noAgentOption')) + '</option>'
      + agents.map(function(a) { return '<option value="' + escapeHtml(a) + '"' + (a === selected ? ' selected' : '') + '>@' + escapeHtml(a) + '</option>'; }).join('')
      + '</select><div id="tmf-agent-panel"></div>' : '')
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">'
    + '<button onclick="document.getElementById(\'toolMcpFilterOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button onclick="_saveToolMcpFilterDialog()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button></div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  window._toolMcpFilterMode = mode;
  if (showAgent) _renderToolMcpAgentOverride();
}

function _saveToolMcpFilterDialog() {
  const data = window._toolMcpFilterData || {};
  const filters = JSON.parse(JSON.stringify(data.filters || {}));
  const mode = window._toolMcpFilterMode || 'conversation';
  if (mode !== 'agent') {
    filters.disabled_tools = _collectDisabledDefaultTools('tmf-conv-tool');
    filters.enabled_dynamic_tools = _collectEnabledExternalDynamicTools('tmf-conv-tool');
    filters.enabled_mcps = _collectEnabled('tmf-conv-mcp');
  }
  filters.agent_overrides = filters.agent_overrides || {};
  const agent = document.getElementById('tmf-agent')?.value || '';
  if (mode !== 'conversation' && agent) {
    const custom = !!document.getElementById('tmf-agent-custom')?.checked;
    filters.agent_overrides[agent] = {
      tools: { mode: custom ? 'custom' : 'inherit', selected: custom ? _collectEnabled('tmf-agent-tool') : [] },
      mcps: { mode: custom ? 'custom' : 'inherit', enabled: custom ? _collectEnabled('tmf-agent-mcp') : [] },
    };
  }
  action$('update_tool_mcp_filters', { conversation_id: conversationId, filters }).subscribe(function(res) {
    if (res.error) { addMsg('error', res.error); return; }
    addMsg('system', t('toolsMcpAvailabilityUpdated'));
    document.getElementById('toolMcpFilterOverlay')?.remove();
    loadResources();
  });
}

function _normalizeAgentHookBindings(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map(function(item) {
    if (typeof item === 'string') return { name: item, enabled: true };
    return item && typeof item === 'object' ? item : null;
  }).filter(Boolean);
}

function _splitCommaList(value) {
  return String(value || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean);
}

function _joinList(value) {
  return Array.isArray(value) ? value.join(', ') : String(value || '');
}

async function _showAgentHooksDialog() {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  let data = {};
  try {
    data = await rxjs.firstValueFrom(action$('get_conversation_hooks', { conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); return; }
  } catch (e) { addMsg('error', e.message); return; }

  const hooks = data.hooks || [];
  const bindings = _normalizeAgentHookBindings(data.bindings || []);
  const byName = {};
  bindings.forEach(function(b) { byName[b.name || b.ref || ''] = b; });
  let overlay = document.getElementById('agentHooksOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'agentHooksOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:740px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('conversationAgentHooks')) + '</h3>'
    + '<button onclick="document.getElementById(\'agentHooksOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button></div>';
  if (hooks.length) {
    html += '<div style="display:grid;grid-template-columns:34px 1fr 1fr 1fr 70px 88px;gap:6px;align-items:center;color:var(--pf-muted);font-size:10px;margin-bottom:4px;">'
      + '<div></div><div>' + escapeHtml(t('name')) + '</div><div>' + escapeHtml(t('events')) + '</div><div>' + escapeHtml(t('filters')) + '</div><div>' + escapeHtml(t('priority')) + '</div><div>' + escapeHtml(t('failPolicy')) + '</div></div>';
    hooks.forEach(function(h, idx) {
      const name = h.name || '';
      const b = byName[name] || null;
      const enabled = !!b && b.enabled !== false;
      const events = b ? _joinList(b.events) : '';
      const agents = b ? _joinList(b.agents) : '';
      const tools = b ? _joinList(b.tools) : '';
      const priority = b ? (b.priority || 0) : 0;
      const fp = (b && b.fail_policy) || h.fail_policy || 'open';
      html += '<div class="agent-hook-binding-row" data-hook-name="' + escapeHtml(name) + '" style="display:grid;grid-template-columns:34px 1fr 1fr 1fr 70px 88px;gap:6px;align-items:center;margin-bottom:6px;padding:6px;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;">'
        + '<input class="ah-enabled" type="checkbox"' + (enabled ? ' checked' : '') + ' style="accent-color:var(--pf-accent);"/>'
        + '<div style="min-width:0;">' + _scopeBadge(h._scope || h.scope || '') + '<span style="font-size:12px;color:var(--pf-text);">' + escapeHtml(name) + '</span></div>'
        + '<input class="ah-events" value="' + escapeHtml(events) + '" placeholder="' + escapeHtml(_joinList(h.events || [])) + '" style="width:100%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/>'
        + '<div style="display:flex;gap:4px;"><input class="ah-agents" value="' + escapeHtml(agents) + '" placeholder="@" style="width:50%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/><input class="ah-tools" value="' + escapeHtml(tools) + '" placeholder="tool" style="width:50%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/></div>'
        + '<input class="ah-priority" type="number" value="' + escapeHtml(String(priority)) + '" style="width:100%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"/>'
        + '<select class="ah-fail" style="width:100%;box-sizing:border-box;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;font-size:11px;"><option value="open"' + (fp === 'open' ? ' selected' : '') + '>open</option><option value="closed"' + (fp === 'closed' ? ' selected' : '') + '>closed</option></select>'
        + '</div>';
    });
  } else {
    html += '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noAgentHooks')) + '</div>';
  }
  html += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px;">'
    + '<button onclick="document.getElementById(\'agentHooksOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button onclick="_saveAgentHooksDialog()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button></div>';
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function _saveAgentHooksDialog() {
  const rows = document.querySelectorAll('#agentHooksOverlay .agent-hook-binding-row');
  const bindings = Array.from(rows).map(function(row) {
    return {
      name: row.dataset.hookName || '',
      enabled: !!row.querySelector('.ah-enabled')?.checked,
      events: _splitCommaList(row.querySelector('.ah-events')?.value || ''),
      agents: _splitCommaList(row.querySelector('.ah-agents')?.value || ''),
      tools: _splitCommaList(row.querySelector('.ah-tools')?.value || ''),
      priority: parseInt(row.querySelector('.ah-priority')?.value || '0') || 0,
      fail_policy: row.querySelector('.ah-fail')?.value || 'open',
    };
  }).filter(function(b) { return b.name && b.enabled; });
  action$('update_conversation_hooks', { conversation_id: conversationId, bindings: bindings }).subscribe(function(res) {
    if (res.error) { addMsg('error', res.error); return; }
    addMsg('system', t('agentHooksUpdated'));
    document.getElementById('agentHooksOverlay')?.remove();
    loadResources();
  });
}

function showAgentMenu(e, name, scope, autoconv) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  const sep = () => { const s = document.createElement('div'); s.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;'; menu.appendChild(s); };

  item('\u{1F441} ' + t('viewDefinitionMenu'), () => showResourceEditor('agent', name, true));
  if (_canEditScope(scope)) item('\u270F ' + t('editDefinitionMenu'), () => showResourceEditor('agent', name));
  item('\u2699 ' + t('configureConversationMenu'), () => _showAgentConvConfigDialog(name));
  item('\u2699 ' + t('toolsMcpOverrideMenu'), () => _showToolMcpFilterDialog(name, 'agent'));
  item('\u25B6 ' + t('select'), () => cmdAgentSelect(name).then(loadResources));
  item('\u{1F9E9} ' + t('manageSkillsMenu'), () => _showAgentSkillsDialog(name));
  if (autoconv) {
    item('\u23F9 ' + t('autoconvOff'), () => { action$('random_thought', { sub: 'off', agent: name }).subscribe(d => { addMsg('system', d.error || t('autoconvDisabledFor', { agent: name })); loadResources(); }); });
  } else {
    item('\u{1F504} ' + t('autoconvOnMenu'), () => { const freq = prompt(t('autoconvFrequencyPrompt'), '6/1m'); if (!freq) return; action$('random_thought', { sub: 'on', agent: name, frequency: freq }).subscribe(d => { addMsg('system', d.error || t('autoconvEnabledFor', { agent: name, freq: freq })); loadResources(); }); });
  }
  sep();
  if (_isAdmin()) item('\u2191 ' + t('copyToGlobal'), () => _copyResource('agent', name, 'global'));
  if (scope !== 'user') item('\u2191 ' + t('copyToUser'), () => _copyResource('agent', name, 'user'));
  sep();
  item('\u2716 ' + t('removeFromConversation'), () => _removeAgentFromConv(name), true);
  if (_canEditScope(scope)) {
    item('\u{1F5D1} ' + t('deleteDefinitionMenu'), () => _deleteResource('agent', name, scope), true);
  }
  setTimeout(() => document.addEventListener('click', function _close() { menu.remove(); document.removeEventListener('click', _close); }), 0);
}

function _showSkillAssignDialog(skillName) {
  action$('list_resources', {}).subscribe(data => {
    var agents = (data.agents || []).concat((data.repo_agents || []).filter(a => !a.in_conversation));
    if (!agents.length) { addMsg('system', t('noAgentsAvailable')); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var options = agents.map(a => '<option value="' + escapeHtml(a.name) + '">' + escapeHtml(a.name) + '</option>').join('');
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:320px;">'
      + '<h3 style="margin:0 0 12px;">' + escapeHtml(t('skillAssignTitle', { skill: skillName })) + '</h3>'
      + '<select id="_skAssignAgent" style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;font-size:13px;">' + options + '</select>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'
      + '<button onclick="this.closest(\'.exec-overlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="_skAssignBtn" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('assign')) + '</button>'
      + '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('_skAssignBtn').onclick = function() {
      var agent = document.getElementById('_skAssignAgent').value;
      overlay.remove();
      cmdResourceAction('assign_skill', { agent_name: agent, skill_name: skillName }).then(loadResources);
    };
  });
}

function _showAgentConvConfigDialog(agentName) {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  Promise.all([
    rxjs.firstValueFrom(action$('get_agent_conv_config', { name: agentName, conversation_id: conversationId })),
    rxjs.firstValueFrom(listServices$('llmConnection')),
  ]).then(function(results) {
    var data = results[0], svcData = results[1];
    if (data.error) { addMsg('error', data.error); return; }
    var cfg = data.config || {};
    var paramsSchema = data.parameters_schema || {};
    var instParams = cfg.params || {};
    var services = (svcData.services || []).filter(function(s) { return s.enabled; });
    var serviceOpts = services.map(function(s) {
      var sel = s.service_id === cfg.llm_service ? ' selected' : '';
      return '<option value="' + escapeHtml(s.service_id) + '"' + sel + '>'
        + escapeHtml(s.service_id) + ' (' + escapeHtml(s.provider || '') + ')</option>';
    }).join('');
    var toolsStr = Array.isArray(cfg.tools) ? cfg.tools.join(', ') : (cfg.tools || '');
    var overlay = document.createElement('div');
    overlay.id = 'agentConvConfigOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
    var panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:520px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
    var html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
      + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('configureAgentTitle', { agent: agentName })) + '</h3>'
      + '<button onclick="document.getElementById(\'agentConvConfigOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
      + '</div>';
    // Definition info
    if (cfg.definition) {
      html += '<div style="margin-bottom:10px;padding:6px 8px;background:var(--pf-sidebar);border-radius:4px;font-size:11px;">'
        + '<span style="color:var(--pf-muted);">' + escapeHtml(t('definition')) + ':</span> <span style="color:var(--pf-accent);">' + escapeHtml(cfg.definition) + '</span></div>';
    }
    // Instance parameters — skip 'name' (synced from instance_name, immutable here)
    var paramKeys = Object.keys(paramsSchema);
    var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
    if (visibleParamKeys.length) {
      html += '<div style="margin-bottom:10px;padding:8px;border:1px solid var(--pf-border);border-radius:4px;">'
        + '<div style="font-size:11px;color:var(--pf-accent);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('instanceParameters')) + '</div>';
      visibleParamKeys.forEach(function(k) {
        var spec = paramsSchema[k] || {};
        var val = instParams[k] || spec.default || '';
        var label = k + (spec.required ? ' *' : '');
        html += '<div style="margin-bottom:6px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(label) + '</label>'
          + '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(val)) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
      });
      html += '</div>';
    }
    // Runtime config
    html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('llmServiceRequired')) + '</label>'
      + '<select id="acc-llm" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">'
      + serviceOpts + '</select></div>'
      + '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('modelOverride')) + '</label>'
      + '<input id="acc-model" value="' + escapeHtml(cfg.model || '') + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>'
      + '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('toolsCommaSeparated')) + '</label>'
      + '<input id="acc-tools" value="' + escapeHtml(toolsStr) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>'
      + '<div style="margin-bottom:8px;">'
      + '<label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('maxIterationsAgentLoop')) + '</label>'
      + '<input id="acc-depth" type="number" value="' + (cfg.max_depth || 1000) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/>'
      + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
      + '<button onclick="document.getElementById(\'agentConvConfigOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="acc-save" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button>'
      + '</div>';
    panel.innerHTML = html;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    document.getElementById('acc-save').onclick = function() {
      var llm = document.getElementById('acc-llm').value;
      var model = document.getElementById('acc-model').value;
      var tools = document.getElementById('acc-tools').value
        .split(',').map(function(s) { return s.trim(); }).filter(function(s) { return s; });
      var depth = parseInt(document.getElementById('acc-depth').value) || 1000;
      // Collect params — name is always the instance name
      var params = { name: agentName };
      panel.querySelectorAll('[data-param]').forEach(function(inp) {
        params[inp.dataset.param] = inp.value;
      });
      action$('update_agent_conv_config', {
        name: agentName, conversation_id: conversationId,
        config: { llm_service: llm, model: model, tools: tools,
                   max_depth: depth, params: params },
      }).subscribe(function(r) {
        if (r.error) { addMsg('error', r.error); return; }
        addMsg('system', t('agentConfigUpdated', { agent: agentName }));
        overlay.remove();
        loadResources();
      });
    };
  });
}

function _showAgentSkillsDialog(agentName) {
  // Load all skills + agent's current assigned skills
  Promise.all([
    rxjs.firstValueFrom(action$('list_skills', {})),
    rxjs.firstValueFrom(action$('list_agent_skills', { agent_name: agentName })),
  ]).then(function(results) {
    var allSkills = results[0].skills || [];
    var assigned = (results[1].skills || []).map(s => s.name);
    if (!allSkills.length) { addMsg('system', t('noSkillsCreateFirst')); return; }
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    var checkboxes = allSkills.map(s => {
      var checked = assigned.indexOf(s.name) >= 0 ? ' checked' : '';
      return '<label style="display:flex;align-items:center;gap:8px;padding:4px 0;cursor:pointer;font-size:13px;color:var(--pf-text);">'
        + '<input type="checkbox" class="agent-sk-cb" value="' + escapeHtml(s.name) + '"' + checked + ' style="accent-color:var(--pf-accent);"/>'
        + escapeHtml(s.name)
        + (s.description ? ' <span style="color:var(--pf-muted);font-size:11px;">\u2014 ' + escapeHtml(s.description) + '</span>' : '')
        + '</label>';
    }).join('');
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:360px;">'
      + '<h3 style="margin:0 0 12px;">' + escapeHtml(t('agentSkillsTitle', { agent: agentName })) + '</h3>'
      + '<div style="max-height:200px;overflow-y:auto;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;padding:8px;">' + checkboxes + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">'
      + '<button onclick="this.closest(\'.exec-overlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="_agentSkSave" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextSave')) + '</button>'
      + '</div></div>';
    document.body.appendChild(overlay);
    document.getElementById('_agentSkSave').onclick = function() {
      var newAssigned = Array.from(overlay.querySelectorAll('.agent-sk-cb:checked')).map(cb => cb.value);
      // Compute diff and send assign/unassign calls
      var toAssign = newAssigned.filter(s => assigned.indexOf(s) < 0);
      var toUnassign = assigned.filter(s => newAssigned.indexOf(s) < 0);
      overlay.remove();
      var calls = [];
      toAssign.forEach(sk => calls.push(rxjs.firstValueFrom(action$('assign_skill', { agent_name: agentName, skill_name: sk }))));
      toUnassign.forEach(sk => calls.push(rxjs.firstValueFrom(action$('unassign_skill', { agent_name: agentName, skill_name: sk }))));
      Promise.all(calls).then(() => {
        var msg = [];
        if (toAssign.length) msg.push(t('assignedList', { items: toAssign.join(', ') }));
        if (toUnassign.length) msg.push(t('removedList', { items: toUnassign.join(', ') }));
        if (msg.length) addMsg('system', t('agentSkillsUpdated', { agent: agentName, details: msg.join('. ') }));
        loadResources();
      });
    };
  });
}

// ── Deploy flow dialog ───────────────────────────────────────────
function _flowFieldValueHtml(value) {
  if (value == null) return '';
  if (typeof value === 'object') return escapeHtml(JSON.stringify(value, null, 2));
  return escapeHtml(String(value));
}

function _flowFieldValue(schema, values, key) {
  if (values && values[key] != null) return values[key];
  const spec = schema[key] || {};
  return spec.default != null ? spec.default : '';
}

function _flowSchemaEntryFromValue(value) {
  if (typeof value === 'boolean') return { type: 'boolean', default: value };
  if (Number.isInteger(value)) return { type: 'integer', default: value };
  if (typeof value === 'number') return { type: 'float', default: value };
  if (value && typeof value === 'object') return { type: 'object', default: value };
  return { type: 'string', default: value == null ? '' : String(value) };
}

function _renderFlowSchemaFields(schema, values, cssClass, serviceId) {
  let html = '';
  for (const [key, spec0] of Object.entries(schema || {})) {
    const spec = spec0 || {};
    const type = spec.type || 'string';
    const value = _flowFieldValue(schema, values || {}, key);
    const dataAttrs = ' class="' + cssClass + '" data-key="' + escapeHtml(key)
      + '" data-type="' + escapeHtml(type) + '"'
      + (serviceId ? ' data-service-id="' + escapeHtml(serviceId) + '"' : '');
    html += '<div style="margin-bottom:8px;">'
      + '<label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(key)
      + (spec.required ? ' <span style="color:var(--pf-danger);">*</span>' : '') + '</label>';
    if (spec.description) html += '<div style="color:var(--pf-muted);font-size:10px;margin-top:1px;">' + escapeHtml(spec.description) + '</div>';
    if (type === 'boolean') {
      html += '<label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;"><input type="checkbox"'
        + dataAttrs + (value ? ' checked' : '') + ' style="accent-color:var(--pf-accent);">'
        + '<span style="color:var(--pf-text);font-size:12px;">' + escapeHtml(t('enabled')) + '</span></label>';
    } else if (type === 'select' && spec.options) {
      html += '<select' + dataAttrs + ' style="' + _svcInputStyle + '">';
      for (const opt0 of spec.options) {
        const optVal = typeof opt0 === 'object' ? opt0.value : opt0;
        const optLabel = typeof opt0 === 'object' ? (opt0.label || opt0.value) : opt0;
        html += '<option value="' + escapeHtml(String(optVal)) + '"'
          + (String(value) === String(optVal) ? ' selected' : '') + '>'
          + escapeHtml(String(optLabel)) + '</option>';
      }
      html += '</select>';
    } else if (type === 'textarea' || type === 'map' || type === 'object') {
      html += '<textarea' + dataAttrs + ' style="' + _svcInputStyle + 'min-height:80px;font-family:monospace;resize:vertical;">'
        + _flowFieldValueHtml(value) + '</textarea>';
    } else if (type === 'integer' || type === 'float') {
      html += '<input type="number"' + (type === 'float' ? ' step="any"' : '')
        + dataAttrs + ' value="' + _flowFieldValueHtml(value) + '" style="' + _svcInputStyle + 'width:140px;">';
    } else {
      html += '<input type="' + (spec.sensitive ? 'password' : 'text') + '"'
        + dataAttrs + ' value="' + _flowFieldValueHtml(value) + '" style="' + _svcInputStyle + '">';
    }
    html += '</div>';
  }
  return html;
}

function _readFlowConfigField(el) {
  const type = el.dataset.type || 'string';
  if (type === 'boolean') return el.checked;
  if (type === 'integer') return parseInt(el.value) || 0;
  if (type === 'float') return parseFloat(el.value) || 0;
  if (type === 'map' || type === 'object') return JSON.parse(el.value || '{}');
  return el.value;
}

function _collectFlowDeploymentConfig(root) {
  const parameters = {};
  root.querySelectorAll('.flow-param-field').forEach(el => {
    parameters[el.dataset.key] = _readFlowConfigField(el);
  });
  const service_overrides = {};
  const service_configs = {};
  root.querySelectorAll('.flow-service-card').forEach(card => {
    const sid = card.dataset.serviceId;
    const mode = (card.querySelector('.flow-service-mode') || {}).value || 'local';
    if (mode && mode !== 'local') {
      service_overrides[sid] = mode;
    } else {
      const cfg = {};
      card.querySelectorAll('.flow-service-param-field').forEach(el => {
        cfg[el.dataset.key] = _readFlowConfigField(el);
      });
      service_configs[sid] = cfg;
    }
  });
  return { parameters, service_overrides, service_configs };
}

function _onFlowServiceModeChange(sel) {
  const card = sel.closest('.flow-service-card');
  if (!card) return;
  const local = card.querySelector('.flow-service-local');
  if (local) local.style.display = (sel.value && sel.value !== 'local') ? 'none' : 'block';
}

async function _renderFlowDeploymentConfig(schemaData) {
  const paramsSchema = Object.assign({}, schemaData.parameters_schema || {});
  const paramValues = Object.assign(
    {}, schemaData.template_parameters || {}, schemaData.parameter_values || {}, schemaData.parameters || {}
  );
  for (const [key, value] of Object.entries(paramValues)) {
    if (!String(key).startsWith('_') && !paramsSchema[key]) paramsSchema[key] = _flowSchemaEntryFromValue(value);
  }
  let html = '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
    + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>';
  if (Object.keys(paramsSchema).length) {
    html += _renderFlowSchemaFields(paramsSchema, paramValues, 'flow-param-field');
  } else {
    html += '<div style="color:var(--pf-muted);font-size:12px;margin-bottom:8px;">' + escapeHtml(t('flowNoParameters')) + '</div>';
  }
  html += '</div>';

  const services = schemaData.services || {};
  if (Object.keys(services).length) {
    html += '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
      + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('services')) + '</div>';
    for (const [sid, svc] of Object.entries(services)) {
      const current = svc.override || 'local';
      let options = '<option value="local"' + (current === 'local' ? ' selected' : '') + '>' + escapeHtml(t('flowConfigureLocalService')) + '</option>';
      try {
        const listed = await rxjs.firstValueFrom(listServices$(svc.service_type || ''));
        for (const s of (listed.services || [])) {
          const ref = s.ref || (s.scope === 'global' ? 'global:' + s.service_id : s.service_id);
          const label = s.service_id + (s.scope ? ' [' + s.scope + ']' : '') + (s.provider ? ' - ' + s.provider : '');
          options += '<option value="' + escapeHtml(ref) + '"' + (current === ref ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
        }
      } catch (e) {}
      if (current && current !== 'local' && options.indexOf('value="' + escapeHtml(current) + '"') < 0) {
        options += '<option value="' + escapeHtml(current) + '" selected>' + escapeHtml(t('missingItem', { value: current })) + '</option>';
      }
      const localDisplay = current && current !== 'local' ? 'display:none;' : '';
      html += '<div class="flow-service-card" data-service-id="' + escapeHtml(sid) + '" style="background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:6px;padding:8px;margin-bottom:8px;">'
        + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
        + '<div style="color:var(--pf-text);font-size:12px;font-weight:600;">' + escapeHtml(sid) + '</div>'
        + '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(svc.service_type || '') + '</div></div>'
        + '<select class="flow-service-mode" onchange="_onFlowServiceModeChange(this)" style="' + _svcInputStyle + '">' + options + '</select>'
        + '<div class="flow-service-local" style="margin-top:8px;' + localDisplay + '">'
        + _renderFlowSchemaFields(svc.parameters_schema || {}, svc.parameter_values || {}, 'flow-service-param-field', sid)
        + '</div></div>';
    }
    html += '</div>';
  }
  return html;
}

async function showDeployFlowDialog() {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:560px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(t('deployFlow'))}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div><div style="color:var(--pf-muted);font-size:12px;">${escapeHtml(t('loadingTemplates'))}</div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  try {
    const data = await rxjs.firstValueFrom(action$('list_available_flows', {}));
    const templates = data.templates || [];
    if (!templates.length) {
      panel.querySelector('div:last-child').innerHTML = '<div style="color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('noFlowTemplates')) + '</div>';
      return;
    }
    let optionsHtml = templates.map(t => {
      const versionLabel = t.version ? ' v' + t.version : '';
      const scopeLabel = t.scope || 'independent';
      return '<option value="' + escapeHtml(t.id) + '" data-scope="' + escapeHtml(scopeLabel) + '">'
        + escapeHtml(t.name) + ' (' + escapeHtml(String(t.tasks_count)) + ' tasks)' + escapeHtml(versionLabel)
        + ' [' + escapeHtml(scopeLabel) + ']</option>';
    }).join('');
    panel.querySelector('div:last-child').innerHTML = `
      <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('template'))}</label>
        <select id="deploy-template" onchange="_onDeployTemplateChange()" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">${optionsHtml}</select></div>
      <div id="deploy-scope-info" style="margin-bottom:8px;font-size:11px;color:var(--pf-muted);"></div>
      <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('deployScope'))}</label>
        <select id="deploy-scope" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">
          <option value="user">${escapeHtml(t('user'))}</option>
          <option value="conversation">${escapeHtml(t('conversation'))}</option>
        </select></div>
      <div id="deploy-config" style="margin-bottom:8px;color:var(--pf-muted);font-size:12px;">${escapeHtml(t('loadingDeploymentSchema'))}</div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
        <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
        <button onclick="_submitDeployFlow()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('deploy'))}</button>
      </div>`;
    _onDeployTemplateChange();
  } catch (e) {
    panel.querySelector('div:last-child').innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(t('templatesLoadFailed', { error: e.message })) + '</div>';
  }
}
function _submitDeployFlow() {
  const templateId = document.getElementById('deploy-template').value;
  const scope = document.getElementById('deploy-scope').value;
  let cfg;
  try {
    cfg = _collectFlowDeploymentConfig(document.getElementById('deploy-config'));
  } catch (e) {
    alert(t('invalidJsonInParameters', { error: e.message }));
    return;
  }
  action$('deploy_flow', {
    template_id: templateId,
    scope,
    parameters: cfg.parameters,
    service_overrides: cfg.service_overrides,
    service_configs: cfg.service_configs,
  }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', t('flowDeployed', { id: d.instance_id, scope: scope })); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
}
async function _onDeployTemplateChange() {
  var sel = document.getElementById('deploy-template');
  var opt = sel.options[sel.selectedIndex];
  var flowScope = opt ? opt.getAttribute('data-scope') || 'independent' : 'independent';
  var info = document.getElementById('deploy-scope-info');
  var scopeSel = document.getElementById('deploy-scope');
  if (flowScope === 'conversation') {
    info.innerHTML = '<span style="color:var(--pf-warning);">' + escapeHtml(t('flowRequiresConversationContext')) + '</span>';
    scopeSel.value = 'conversation';
    scopeSel.disabled = true;
  } else if (flowScope === 'user') {
    info.innerHTML = '<span style="color:var(--pf-accent-2);">' + escapeHtml(t('flowRequiresUserContext')) + '</span>';
    scopeSel.disabled = false;
  } else {
    info.innerHTML = '<span style="color:var(--pf-success);">' + escapeHtml(t('flowIndependentNoDependencies')) + '</span>';
    scopeSel.disabled = false;
  }
  var config = document.getElementById('deploy-config');
  if (!config || !sel.value) return;
  config.innerHTML = '<div style="color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('loadingDeploymentSchema')) + '</div>';
  try {
    const schema = await rxjs.firstValueFrom(action$('get_flow_deploy_schema', { template_id: sel.value }));
    if (schema.error) { config.innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(schema.error) + '</div>'; return; }
    config.innerHTML = await _renderFlowDeploymentConfig(schema);
  } catch (e) {
    config.innerHTML = '<div style="color:var(--pf-danger);">' + escapeHtml(t('deploymentSchemaLoadFailed', { error: e.message || e })) + '</div>';
  }
}


// ── Prompt use (click to paste) ─────────────────────────────────
function _usePrompt(name, hasParams) {
  action$('get_prompt', { name }).subscribe(data => {
    if (data.error) { addMsg('system', data.error); return; }
    if (!hasParams || !data.parameters || !Object.keys(data.parameters).length) {
      const input = document.getElementById('input');
      input.value = data.prompt;
      input.focus();
      input.dispatchEvent(new Event('input'));
      return;
    }
    // Build parameter dialog
    const params = data.parameters;
    let ov = document.getElementById('promptParamOverlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'promptParamOverlay';
    ov.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:420px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
    let formHtml = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(data.title || name)}</h3>
      <button onclick="document.getElementById('promptParamOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
    </div>`;
    for (const [key, schema] of Object.entries(params)) {
      const def = schema.default || '';
      const desc = schema.description || key;
      formHtml += `<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(desc)}</label>`
        + `<input id="prompt-param-${key}" value="${escapeHtml(String(def))}" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>`;
    }
    formHtml += `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
      <button onclick="document.getElementById('promptParamOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
      <button id="promptParamPaste" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('promptPaste'))}</button>
    </div>`;
    panel.innerHTML = formHtml;
    ov.appendChild(panel);
    document.body.appendChild(ov);
    document.getElementById('promptParamPaste').onclick = () => {
      const values = {};
      for (const key of Object.keys(params)) {
        values[key] = (document.getElementById('prompt-param-' + key) || {}).value || '';
      }
      action$('use_prompt', { name, params: values }).subscribe(res => {
        if (res.error) { addMsg('system', res.error); return; }
        const input = document.getElementById('input');
        input.value = res.resolved;
        input.focus();
        input.dispatchEvent(new Event('input'));
        document.getElementById('promptParamOverlay').remove();
      });
    };
  });
}

// ── Voice clones ─────────────────────────────────────────────────
function _previewVoice(url) {
  // Stop any previously playing preview before starting a new one.
  try { if (window._voicePreviewAudio) window._voicePreviewAudio.pause(); } catch (e) {}
  const a = new Audio(url);
  window._voicePreviewAudio = a;
  a.play().catch(err => addMsg('system', t('audioPreviewFailed', { error: err.message })));
}

function _deleteVoiceClone(name) {
  if (!confirm(t('deleteVoiceConfirm', { name: name }))) return;
  action$('delete_voice_clone', { name }).subscribe(res => {
    if (res.error) { addMsg('system', t('deleteFailed', { error: res.error })); return; }
    const parts = [];
    if (res.voice_id_deleted) parts.push(t('providerVoiceFreed'));
    if (res.ref_audio_deleted) parts.push(t('refAudioPurged'));
    if (res.tts_cached_purged) parts.push(t('cachedRenderingsPurged', { n: res.tts_cached_purged }));
    addMsg('system', t('voiceDeleted', { name: name, details: parts.length ? ' (' + parts.join(', ') + ')' : '' }));
    setTimeout(loadResources, 200);
  });
}

function _renameVoiceClone(name) {
  const newName = prompt(t('renameVoicePrompt', { name: name }), name);
  if (!newName || newName === name) return;
  action$('rename_voice_clone', { name, new_name: newName }).subscribe(res => {
    if (res.error) { addMsg('system', t('renameFailed', { error: res.error })); return; }
    if (res.unchanged) { addMsg('system', t('voiceNameUnchanged')); return; }
    addMsg('system', t('voiceRenamed', { old: name, name: res.name }));
    setTimeout(loadResources, 200);
  });
}

// ── Resource editor overlay ───────────────────────────────────────
const _RESOURCE_FIELDS = {
  agent:    [['prompt','textarea'],['description','text']],
  skill:    [['prompt','textarea'],['description','text']],
  mcp:      [['transport','mcp_transport'],['via','mcp_via'],['relay_service','mcp_relay'],['local','checkbox'],['url','text'],['command','text'],['args','json'],['env','json'],['auth','json'],['description','text']],
  task_def: [['prompt','textarea'],['criteria','textarea'],['default_interval','text'],['verifier','text'],['interactive','checkbox'],['skills','skills_picker'],['description','text']],
  prompt:   [['prompt','textarea'],['parameters','params_editor'],['title','text'],['category','text'],['description','text']],
  agent_hook: [['events','json'],['allowed_tools','json'],['allowed_services','json'],['fail_policy','hook_fail_policy'],['description','text'],['source','textarea']],
  _tool:    [['tool_description','text'],['parameters','textarea'],['code','textarea']],
};

async function _loadResourceRelayOptions() {
  try {
    const data = await rxjs.firstValueFrom(action$('relay_list_available', {}));
    window._resourceRelayOptions = data.relays || [];
  } catch (e) {
    window._resourceRelayOptions = [];
  }
}

function _buildResourceForm(rtype, data, isNew, readonly) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  let html = '';
  if (isNew) {
    html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + t('name') + '</label><input id="res-name" value="" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>';
    if (rtype !== '_tool') {
      html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + t('scope') + '</label><select id="res-scope" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">'
        + (_isAdmin() ? '<option value="global">' + t('global') + '</option>' : '')
        + '<option value="user">' + t('user') + '</option><option value="conversation">' + t('conversation') + '</option></select></div>';
    }
  }
  for (const [key, type] of fields) {
    let val = (data && data[key] != null) ? data[key] : '';
    if (typeof val === 'object') val = JSON.stringify(val, null, 2);
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    html += `<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${key}</label>`;
    if (type === 'textarea') {
      html += `<textarea id="res-${key}"${dis} style="width:100%;min-height:120px;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;resize:vertical;${roS}">${escaped}</textarea>`;
    } else if (type === 'json') {
      const jsonVal = (data && data[key] != null && typeof data[key] === 'object') ? JSON.stringify(data[key], null, 2) : (val || (key === 'args' ? '[]' : '{}'));
      html += `<textarea id="res-${key}"${dis} data-json="1" style="width:100%;min-height:70px;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;resize:vertical;${roS}">${String(jsonVal).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</textarea>`;
    } else if (type === 'checkbox') {
      const checkedAttr = (val === true || val === 'true') ? ' checked' : '';
      const checkboxText = key === 'local'
        ? 'Run stdio on relay host helper'
        : (key === 'interactive'
          ? 'Interactive task: scheduled wake-ups are system-marked, not user input'
          : key);
      html += `<label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;"><input id="res-${key}" type="checkbox"${checkedAttr}${dis} style="accent-color:var(--pf-accent);"/> <span style="color:var(--pf-text);font-size:12px;">${escapeHtml(checkboxText)}</span></label>`;
    } else if (type === 'mcp_transport') {
      const httpSelected = (val === 'http' || !val) ? ' selected' : '';
      const stdioSelected = val === 'stdio' ? ' selected' : '';
      html += `<select id="res-${key}"${dis} style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;${roS}"><option value="http"${httpSelected}>HTTP JSON-RPC</option><option value="stdio"${stdioSelected}>Command-line stdio</option></select>`;
    } else if (type === 'mcp_via') {
      const directSelected = (val === 'direct' || !val) ? ' selected' : '';
      const relaySelected = val === 'relay' ? ' selected' : '';
      html += `<select id="res-${key}"${dis} style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;${roS}"><option value="direct"${directSelected}>Direct HTTP from PawFlow server</option><option value="relay"${relaySelected}>Via relay</option></select>`;
    } else if (type === 'mcp_relay') {
      const relays = window._resourceRelayOptions || [];
      const current = String(val || '');
      let options = '<option value="">Default linked relay</option>';
      if (current && !relays.some(r => r.relay_id === current)) {
        options += '<option value="' + escapeHtml(current) + '" selected>' + escapeHtml(current) + '</option>';
      }
      relays.forEach(function(r) {
        const rid = r.relay_id || '';
        const selected = rid === current ? ' selected' : '';
        let label = rid;
        if (r.host_root) label += ' - ' + r.host_root;
        else if (r.root) label += ' - ' + r.root;
        options += '<option value="' + escapeHtml(rid) + '"' + selected + '>' + escapeHtml(label) + '</option>';
      });
      html += `<select id="res-${key}"${dis} style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;${roS}">${options}</select>`;
    } else if (type === 'hook_fail_policy') {
      const openSelected = (val === 'open' || !val) ? ' selected' : '';
      const closedSelected = val === 'closed' ? ' selected' : '';
      html += `<select id="res-${key}"${dis} style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;${roS}"><option value="open"${openSelected}>open</option><option value="closed"${closedSelected}>closed</option></select>`;
    } else if (type === 'params_editor') {
      const params = (data && typeof data[key] === 'object' && data[key]) ? data[key] : {};
      html += `<div id="res-${key}" data-type="params_editor" style="margin-top:2px;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;padding:6px;${roS}">`;
      html += '<table style="width:100%;border-collapse:collapse;font-size:11px;">';
      html += '<tr style="color:var(--pf-muted);"><th style="text-align:left;padding:2px 4px;">Name</th><th style="text-align:left;padding:2px 4px;">Type</th><th style="text-align:left;padding:2px 4px;">Default</th><th style="text-align:left;padding:2px 4px;">Description</th>';
      if (!ro) html += '<th style="width:24px;"></th>';
      html += '</tr>';
      for (const [pname, pdef] of Object.entries(params)) {
        const pt = (pdef.type || 'string').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const pd = (pdef.default || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const pdesc = (pdef.description || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        const pn = pname.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
        html += `<tr class="param-row" style="border-top:1px solid var(--pf-border);">`;
        html += `<td style="padding:3px 4px;"><input class="pe-name" value="${pn}"${dis} style="width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;${roS}"/></td>`;
        html += `<td style="padding:3px 4px;"><select class="pe-type"${dis} style="background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;${roS}">`;
        for (const t of ['string','number','boolean']) html += `<option value="${t}"${pt===t?' selected':''}>${t}</option>`;
        html += '</select></td>';
        html += `<td style="padding:3px 4px;"><input class="pe-default" value="${pd}"${dis} style="width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;${roS}"/></td>`;
        html += `<td style="padding:3px 4px;"><input class="pe-desc" value="${pdesc}"${dis} style="width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;${roS}"/></td>`;
        if (!ro) html += `<td style="padding:3px 2px;"><button onclick="this.closest('tr').remove()" style="background:none;border:none;color:var(--pf-danger);cursor:pointer;font-size:14px;">&times;</button></td>`;
        html += '</tr>';
      }
      html += '</table>';
      if (!ro) html += `<button onclick="_addParamRow(this.parentElement)" style="margin-top:4px;background:var(--pf-border);color:var(--pf-muted);border:1px solid var(--pf-border);padding:3px 10px;border-radius:3px;cursor:pointer;font-size:11px;">+ Add Parameter</button>`;
      html += '</div>';
    } else if (type === 'skills_picker') {
      html += `<div id="res-${key}" data-type="skills_picker" style="margin-top:2px;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;padding:6px;max-height:120px;overflow-y:auto;${roS}">`;
      html += '<div style="color:var(--pf-muted);font-size:11px;">Loading skills...</div>';
      html += '</div>';
    } else if (type === 'number') {
      html += `<input id="res-${key}" type="number" value="${escaped}"${dis} style="width:80px;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;${roS}"/>`;
    } else {
      html += `<input id="res-${key}" value="${escaped}"${dis} style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;${roS}"/>`;
    }
    html += '</div>';
  }
  return html;
}

function _addParamRow(container) {
  const table = container.querySelector('table');
  const tr = document.createElement('tr');
  tr.className = 'param-row';
  tr.style.borderTop = '1px solid var(--pf-border)';
  tr.innerHTML = '<td style="padding:3px 4px;"><input class="pe-name" value="" style="width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;"/></td>'
    + '<td style="padding:3px 4px;"><select class="pe-type" style="background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;"><option value="string">string</option><option value="number">number</option><option value="boolean">boolean</option></select></td>'
    + '<td style="padding:3px 4px;"><input class="pe-default" value="" style="width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;"/></td>'
    + '<td style="padding:3px 4px;"><input class="pe-desc" value="" style="width:100%;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;"/></td>'
    + '<td style="padding:3px 2px;"><button onclick="this.closest(\'tr\').remove()" style="background:none;border:none;color:var(--pf-danger);cursor:pointer;font-size:14px;">&times;</button></td>';
  table.appendChild(tr);
}

function _collectParams(key) {
  const container = document.getElementById('res-' + key);
  if (!container || container.dataset.type !== 'params_editor') return undefined;
  const rows = container.querySelectorAll('.param-row');
  const params = {};
  rows.forEach(row => {
    const name = (row.querySelector('.pe-name')?.value || '').trim();
    if (!name) return;
    const entry = { type: row.querySelector('.pe-type')?.value || 'string' };
    const def = (row.querySelector('.pe-default')?.value || '').trim();
    if (def) entry.default = def;
    const desc = (row.querySelector('.pe-desc')?.value || '').trim();
    if (desc) entry.description = desc;
    params[name] = entry;
  });
  return Object.keys(params).length ? params : undefined;
}

function _loadSkillsPicker(container, selected, readonly) {
  action$('list_skills', {}).subscribe(data => {
    const skills = data.skills || [];
    if (!skills.length) {
      container.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + t('noSkillsDefined') + '</div>';
      return;
    }
    const dis = readonly ? ' disabled' : '';
    container.innerHTML = skills.map(s => {
      const checked = selected.indexOf(s.name) >= 0 ? ' checked' : '';
      return '<label style="display:flex;align-items:center;gap:6px;padding:2px 0;cursor:' + (readonly ? 'default' : 'pointer') + ';font-size:12px;color:var(--pf-text);">'
        + '<input type="checkbox" class="skill-cb" value="' + escapeHtml(s.name) + '"' + checked + dis + ' style="accent-color:var(--pf-accent);"/>'
        + escapeHtml(s.name)
        + (s.description ? ' <span style="color:var(--pf-muted);font-size:10px;">\u2014 ' + escapeHtml(s.description) + '</span>' : '')
        + '</label>';
    }).join('');
  });
}

function _collectSkillsPicker(key) {
  var container = document.getElementById('res-' + key);
  if (!container || container.getAttribute('data-type') !== 'skills_picker') return null;
  var cbs = container.querySelectorAll('.skill-cb:checked');
  return Array.from(cbs).map(function(cb) { return cb.value; });
}

async function showResourceEditor(rtype, name, readonly) {
  // Fetch current data
  let data = {};
  try {
    data = await rxjs.firstValueFrom(action$('get_resource_detail', { resource_type: rtype, name }));
    if (data.error) { addMsg('error', data.error); return; }
  } catch (e) { addMsg('error', e.message); return; }

  const scope = data._scope || 'user';
  if (rtype === 'mcp') await _loadResourceRelayOptions();
  const ro = !!readonly;
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
  const title = ro ? t('view') : t('contextEdit');
  let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(title)} ${escapeHtml(rtype)}: ${escapeHtml(name)} ${_scopeBadge(scope)}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, data, false, ro);
  if (ro) {
    html += `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('close'))}</button>
    </div>`;
  } else {
    html += `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
    <button onclick="_saveResourceEdit('${rtype}','${name}','${scope}')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextSave'))}</button>
    </div>`;
  }
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  // Populate skills picker if present
  var skPicker = panel.querySelector('[data-type="skills_picker"]');
  if (skPicker) {
    var selected = Array.isArray(data.assigned_skills) ? data.assigned_skills : [];
    _loadSkillsPicker(skPicker, selected, !!readonly);
  }
}

function _saveResourceEdit(rtype, name, scope) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    if (type === 'skills_picker') { data[key] = _collectSkillsPicker(key) || []; continue; }
    if (type === 'params_editor') { const p = _collectParams(key); if (p) data[key] = p; continue; }
    const el = document.getElementById('res-' + key);
    if (el) {
      if (type === 'number') data[key] = parseInt(el.value) || 0;
      else if (type === 'checkbox') data[key] = !!el.checked;
      else if (type === 'json') {
        try { data[key] = el.value.trim() ? JSON.parse(el.value) : (key === 'args' ? [] : {}); }
        catch(e) { alert(t('fieldMustBeValidJson', { field: key })); return; }
      } else data[key] = el.value;
    }
  }
  action$('update_resource', { resource_type: rtype, name, scope, data }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', t('resourceUpdated', { type: rtype, name: name })); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
  });
}

async function showResourceCreator(rtype) {
  if (rtype === '_flow') { showDeployFlowDialog(); return; }
  if (rtype === '_svc') { showServiceInstallForm(); return; }
  if (rtype === 'mcp') await _loadResourceRelayOptions();
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:500px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
  const createAssignBtn = rtype === 'task_def'
    ? '<button onclick="_saveResourceCreate(\'' + rtype + '\', true)" style="background:color-mix(in srgb, var(--pf-accent) 16%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('create')) + ' + ' + escapeHtml(t('assign')) + '</button>'
    : '';
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(t('newResourceTitle', { type: rtype === '_tool' ? t('tool') : rtype }))}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div>` + _buildResourceForm(rtype, {}, true)
    + `<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
    ${createAssignBtn}
    <button onclick="_saveResourceCreate('${rtype}')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('create'))}</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  // Populate skills picker if present (empty selection for new)
  var skPicker = panel.querySelector('[data-type="skills_picker"]');
  if (skPicker) _loadSkillsPicker(skPicker, [], false);
}

function _saveResourceCreate(rtype, assignAfterCreate) {
  const nameEl = document.getElementById('res-name');
  const scopeEl = document.getElementById('res-scope');
  const name = (nameEl && nameEl.value || '').trim();
  const scope = scopeEl ? scopeEl.value : 'user';
  if (!name) { alert(t('nameRequired')); return; }
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    if (type === 'skills_picker') { data[key] = _collectSkillsPicker(key) || []; continue; }
    if (type === 'params_editor') { const p = _collectParams(key); if (p) data[key] = p; continue; }
    const el = document.getElementById('res-' + key);
    if (el) {
      if (type === 'number') data[key] = parseInt(el.value) || 0;
      else if (type === 'checkbox') data[key] = !!el.checked;
      else if (type === 'json') {
        try { data[key] = el.value.trim() ? JSON.parse(el.value) : (key === 'args' ? [] : {}); }
        catch(e) { alert(t('fieldMustBeValidJson', { field: key })); return; }
      } else data[key] = el.value;
    }
  }
  // Dynamic tools use a dedicated action (CreateToolHandler pipeline)
  if (rtype === '_tool') {
    let params = {};
    try { params = data.parameters ? JSON.parse(data.parameters) : {}; } catch(e) { alert(t('parametersMustBeValidJson')); return; }
    action$('create_dynamic_tool', {
      tool_name: name, tool_description: data.tool_description || '',
      parameters: params, code: data.code || ''
    }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else { addMsg('system', t('toolCreated', { name: name })); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
    });
    return;
  }
  action$('create_resource', { resource_type: rtype, name, scope, data }).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else {
      addMsg('system', t('resourceCreated', { type: rtype, name: name }));
      document.getElementById('resourceEditorOverlay').remove();
      loadResources();
      if (assignAfterCreate && rtype === 'task_def') {
        setTimeout(function() { _showAssignDialog(name); }, 0);
      }
    }
  });
}

function _removeAgentFromConv(name) {
  var convAgents = document.querySelectorAll('#res-section-agent > div');
  if (convAgents.length <= 1) {
    if (!confirm(t('removeLastAgentConfirm'))) return;
  }
  cmdResourceAction('remove_agent_from_conv', {name: name, conversation_id: conversationId})
    .then(loadResources);
}

async function showAddAgentToConvDialog(presetDefinition) {
  var existing = document.getElementById('resourceEditorOverlay');
  if (existing) existing.remove();
  var overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  var panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';
  panel.innerHTML = '<p style="color:var(--pf-text);font-weight:600;">' + escapeHtml(t('addAgentToConversation')) + '</p><p style="color:var(--pf-muted);">' + escapeHtml(t('loading')) + '</p>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
  try {
    var data = await rxjs.firstValueFrom(action$('list_repo_agents', {}));
    var svcData = await rxjs.firstValueFrom(listServices$('llmConnection'));
    var definitions = data.agents || [];
    var llmServices = (svcData.services || []).filter(function(s) { return s.enabled; });
    var selectedDef = null;
    panel.innerHTML = '';

    var header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;';
    header.innerHTML = '<strong style="color:var(--pf-text);">' + escapeHtml(t('addAgentToConversation')) + '</strong>';
    var closeBtn = document.createElement('button');
    closeBtn.textContent = '\u00d7';
    closeBtn.style.cssText = 'background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;';
    closeBtn.onclick = function() { overlay.remove(); };
    header.appendChild(closeBtn);
    panel.appendChild(header);

    // Definition selector
    var defLabel = document.createElement('label');
    defLabel.style.cssText = 'color:var(--pf-muted);font-size:11px;';
    defLabel.textContent = t('definitionTemplate');
    panel.appendChild(defLabel);
    var defSelect = document.createElement('select');
    defSelect.style.cssText = 'width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin:4px 0 12px;';
    defSelect.innerHTML = '<option value="">' + escapeHtml(t('selectDefinitionOption')) + '</option>'
      + definitions.map(function(d) {
        var sel = (presetDefinition && d.name === presetDefinition) ? ' selected' : '';
        return '<option value="' + escapeHtml(d.name) + '"' + sel + '>' + escapeHtml(d.name)
          + (d.description ? ' \u2014 ' + escapeHtml(d.description) : '') + '</option>';
      }).join('');
    panel.appendChild(defSelect);
    if (presetDefinition && definitions.some(function(d) { return d.name === presetDefinition; })) {
      selectedDef = presetDefinition;
      // _renderForm defined below; fire after selectedDef set
      setTimeout(function() { _renderForm(); }, 0);
    }

    // Form area (rendered when definition is selected)
    var formArea = document.createElement('div');
    formArea.id = '_addAgentForm';
    panel.appendChild(formArea);

    function _guessLlm(name) {
      for (var i = 0; i < llmServices.length; i++) {
        if (llmServices[i].service_id === name + '_llm_service') return llmServices[i].service_id;
        if (llmServices[i].service_id === name + '_llm') return llmServices[i].service_id;
      }
      return llmServices.length ? llmServices[0].service_id : '';
    }

    function _renderForm() {
      formArea.innerHTML = '';
      if (!selectedDef) return;
      var def = definitions.find(function(d) { return d.name === selectedDef; });
      if (!def) return;
      var paramSchema = def.parameters || {};
      var paramKeys = Object.keys(paramSchema);
      var svcOpts = llmServices.map(function(s) {
        var sel = s.service_id === _guessLlm(selectedDef) ? ' selected' : '';
        return '<option value="' + escapeHtml(s.service_id) + '"' + sel + '>'
          + escapeHtml(s.service_id) + '</option>';
      }).join('');
      var html = '<div style="padding:10px;border:1px solid var(--pf-border);border-radius:4px;background:var(--pf-code-bg);">';
      // Instance name
      html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('instanceNameRequired')) + '</label>'
        + '<input id="_addInstName" value="' + escapeHtml(selectedDef) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
      // LLM Service
      html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('llmServiceRequired')) + '</label>'
        + '<select id="_addLlm" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">'
        + svcOpts + '</select></div>';
      // Params from schema — skip 'name' (always synced from instance_name)
      var visibleParamKeys = paramKeys.filter(function(k) { return k !== 'name'; });
      if (visibleParamKeys.length) {
        html += '<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--pf-border);">'
          + '<div style="font-size:11px;color:var(--pf-accent);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>';
        visibleParamKeys.forEach(function(k) {
          var spec = paramSchema[k] || {};
          var defVal = spec.default || '';
          html += '<div style="margin-bottom:6px;"><label style="color:var(--pf-muted);font-size:11px;">'
            + escapeHtml(k + (spec.required ? ' *' : '')) + '</label>'
            + '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(defVal)) + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:5px;border-radius:4px;margin-top:2px;box-sizing:border-box;font-size:12px;"/></div>';
        });
        html += '</div>';
      }
      html += '</div>';
      formArea.innerHTML = html;
    }

    defSelect.onchange = function() {
      selectedDef = defSelect.value;
      _renderForm();
    };

    // Create link + buttons
    var createLink = document.createElement('div');
    createLink.style.cssText = 'margin-top:12px;border-top:1px solid var(--pf-border);padding-top:10px;font-size:11px;';
    var cl = document.createElement('span');
    cl.style.cssText = 'color:var(--pf-accent);cursor:pointer;';
    cl.textContent = '+ ' + t('createNewDefinitionRepository');
    cl.onclick = function() { overlay.remove(); showResourceCreator('agent'); };
    createLink.appendChild(cl);
    panel.appendChild(createLink);

    var btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;margin-top:12px;';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = t('contextCancel');
    cancelBtn.style.cssText = 'background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;';
    cancelBtn.onclick = function() { overlay.remove(); };
    var addBtn = document.createElement('button');
    addBtn.textContent = t('addAgent');
    addBtn.style.cssText = 'background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;';
    addBtn.onclick = async function() {
      if (!selectedDef) { alert(t('selectDefinitionFirst')); return; }
      var instName = (document.getElementById('_addInstName') || {}).value || '';
      var llm = (document.getElementById('_addLlm') || {}).value || '';
      if (!instName.trim()) { alert(t('instanceNameRequiredMessage')); return; }
      if (!llm) { alert(t('llmServiceRequiredMessage')); return; }
      var params = { name: instName.trim() };
      formArea.querySelectorAll('[data-param]').forEach(function(inp) {
        params[inp.dataset.param] = inp.value;
      });
      overlay.remove();
      await cmdResourceAction('add_agent_to_conv', {
        instance_name: instName.trim(),
        definition: selectedDef,
        params: params,
        llm_service: llm,
        conversation_id: conversationId,
      });
      loadResources();
    };
    btns.appendChild(cancelBtn); btns.appendChild(addBtn);
    panel.appendChild(btns);
  } catch(e) {
    var err = document.createElement('div');
    err.style.cssText = 'color:var(--pf-danger);font-size:12px;';
    err.textContent = t('error') + ': ' + e.message;
    panel.appendChild(err);
  }
}

// ── Param/Secret context menu + create ────────────────────────────
// ── Service context menu ──────────────────────────────────────────
// ── Assign task dialog (agent + variables) ────────────────────────
function _showAssignDialog(taskDefName) {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:420px;border:1px solid var(--pf-border);';
  const convAgents = ((_lastResourcesData || {}).agents || []).map(function(a) { return a.name || ''; }).filter(Boolean);
  const agentField = convAgents.length
    ? `<select id="assign-agent" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">${convAgents.map(function(a) { return `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`; }).join('')}</select>`
    : `<input id="assign-agent" value="" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/>`;
  panel.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h3 style="margin:0;color:var(--pf-text);font-size:14px;">${escapeHtml(t('assignTitle', { name: taskDefName }))}</h3>
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>
  </div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('agent'))}</label>
    ${agentField}</div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('contextMode'))}</label>
    <select id="assign-context" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">
      <option value="isolated">${escapeHtml(t('contextModeIsolated'))}</option>
      <option value="last:10">${escapeHtml(t('contextModeLast10'))}</option>
      <option value="last:20">${escapeHtml(t('contextModeLast20'))}</option>
      <option value="last:50">${escapeHtml(t('contextModeLast50'))}</option>
      <option value="summary:2000">${escapeHtml(t('contextModeSummary2000'))}</option>
      <option value="summary:4000">${escapeHtml(t('contextModeSummary4000'))}</option>
      <option value="full">${escapeHtml(t('contextModeFull'))}</option>
    </select></div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('intervalOptionalOverride'))}</label>
    <input id="assign-interval" placeholder="e.g. 6/1m, 2/1h, 60" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;"/></div>
  <div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(t('variablesKeyValue'))}</label>
    <textarea id="assign-vars" placeholder="nbr_images=20&#10;style=cyberpunk" style="width:100%;min-height:60px;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-family:monospace;font-size:12px;"></textarea></div>
  <details style="margin-bottom:8px;"><summary style="color:var(--pf-muted);font-size:11px;cursor:pointer;">${escapeHtml(t('limitsOptional'))}</summary>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:6px;">
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('maxBudget'))}</label><input id="assign-budget" placeholder="$5" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('turnTime'))}</label><input id="assign-turn-time" placeholder="5m" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('totalTime'))}</label><input id="assign-total-time" placeholder="1h" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
      <div><label style="color:var(--pf-muted);font-size:10px;">${escapeHtml(t('maxReschedules'))}</label><input id="assign-max-resched" placeholder="50" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:4px;border-radius:4px;font-size:11px;"/></div>
    </div></details>
  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
    <button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button>
    <button onclick="_submitAssign('${taskDefName}')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('assign'))}</button>
  </div>`;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  document.getElementById('assign-agent').focus();
}

function _submitAssign(taskDefName) {
  const agent = (document.getElementById('assign-agent').value || '').trim();
  const context = (document.getElementById('assign-context').value || '').trim();
  const interval = (document.getElementById('assign-interval').value || '').trim();
  const varsText = (document.getElementById('assign-vars').value || '').trim();
  if (!agent) { alert(t('agentRequired')); return; }
  const params = { agent_name: agent, task_def_name: taskDefName };
  if (context && context !== 'isolated') params.context = context;
  if (interval) params.interval = interval;
  if (varsText) {
    const variables = {};
    for (const line of varsText.split('\n')) {
      const eq = line.indexOf('=');
      if (eq > 0) variables[line.substring(0, eq).trim()] = line.substring(eq + 1).trim();
    }
    if (Object.keys(variables).length) params.variables = variables;
  }
  const _bv = (document.getElementById('assign-budget') || {}).value || '';
  const _tv = (document.getElementById('assign-turn-time') || {}).value || '';
  const _ttv = (document.getElementById('assign-total-time') || {}).value || '';
  const _rv = (document.getElementById('assign-max-resched') || {}).value || '';
  if (_bv.trim()) params.max_budget = _bv.trim();
  if (_tv.trim()) params.max_turn_time = _tv.trim();
  if (_ttv.trim()) params.max_total_time = _ttv.trim();
  if (_rv.trim()) params.max_reschedules = parseInt(_rv) || 0;
  action$('assign_task', params).subscribe(d => {
    if (d.error) addMsg('error', d.error);
    else { addMsg('system', d.result || t('taskAssigned')); loadResources(); }
    document.getElementById('resourceEditorOverlay').remove();
  });
}

// ── Task instance context menu (Tasks section – management, not lifecycle) ──
function showTaskInstanceMenu(e, taskId, agent, status) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:140px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  const _taskAction = (action) => {
    action$(action + '_task', { task_id: taskId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', t('taskActionDone', { id: taskId, action: action }));
      loadResources();
    });
  };
  // View task log
  item('\u{1F4CB} ' + t('viewLogMenu'), () => {
    action$('task_log', { name: taskId }).subscribe(d => {
      const log = d.log || [];
      if (!log.length) { addMsg('system', t('noLogEntriesFor', { id: taskId })); return; }
      const lines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
      addMsg('system', t('taskLogTitle', { id: taskId, lines: lines }));
    });
  });
  // View task details
  item('\u{1F441} ' + t('viewDetails'), () => {
    action$('list_resources', {}).subscribe(d => {
      const task = (d.all_tasks || []).find(t => t.task_id === taskId);
      if (!task) { addMsg('system', t('taskNotFound', { id: taskId })); return; }
      const info = t('taskDetails', { id: task.task_id, agent: task.agent, status: task.status, iterations: task.iterations, max: task.max_iterations, definition: task.task_def_name || '-', prompt: task.task });
      addMsg('system', info);
    });
  });
  // Delete
  const sep = document.createElement('div');
  sep.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
  menu.appendChild(sep);
  item('\u{1F5D1} ' + t('delete'), () => _taskAction('delete'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

// ── Running task context menu ─────────────────────────────────────
function showRunningTaskMenu(e, taskId, agent, status) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:140px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  const _taskAction = (action) => {
    action$(action + '_task', { task_id: taskId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else addMsg('system', t('taskActionDone', { id: taskId, action: action }));
      loadResources();
    });
  };
  // View task log
  item('\u{1F4CB} ' + t('viewLogMenu'), () => {
    action$('task_log', { name: taskId }).subscribe(d => {
      const log = d.log || [];
      if (!log.length) { addMsg('system', t('noLogEntriesFor', { id: taskId })); return; }
      const lines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
      addMsg('system', t('taskLogTitle', { id: taskId, lines: lines }));
    });
  });
  // Edit limits
  item('\u270F ' + t('editLimits'), () => _showEditLimitsDialog(taskId));
  // Status-specific actions
  if (status === 'active') {
    item('\u23F8 ' + t('pause'), () => _taskAction('pause'));
  } else if (status === 'paused') {
    item('\u25B6 ' + t('resume'), () => _taskAction('resume'));
  } else if (status === 'cancelled' || status === 'failed') {
    item('\u25B6 ' + t('restart'), () => _taskAction('resume'));
  }
  if (status === 'active' || status === 'paused') {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(sep);
    item('\u{1F5D1} ' + t('cancel'), () => _taskAction('cancel'), true);
  }
  // Delete: remove task instance entirely
  const sep2 = document.createElement('div');
  sep2.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
  menu.appendChild(sep2);
  item('\u{1F5D1} ' + t('delete'), () => _taskAction('delete'), true);
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

function _showEditLimitsDialog(taskId) {
  // Fetch current task data
  action$('task_status', {}).subscribe(d => {
    const task = (d.tasks || []).find(t => t.task_id === taskId);
    if (!task) { addMsg('error', t('taskNotFound', { id: taskId })); return; }
    const overlay = document.createElement('div');
    overlay.id = 'resourceEditorOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:var(--pf-shadow);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.onclick = (ev) => { if (ev.target === overlay) overlay.remove(); };
    const panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:8px;padding:20px;min-width:340px;max-width:420px;color:var(--pf-text);';
    const _f = (id, label, val, ph) => `<div style="margin-bottom:8px;"><label style="font-size:11px;color:var(--pf-muted);">${label}</label><input id="${id}" value="${val||''}" placeholder="${ph}" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-size:12px;"/></div>`;
    panel.innerHTML = `<div style="font-weight:bold;margin-bottom:12px;">${escapeHtml(t('editLimitsTitle', { id: taskId }))}</div>`
      + _f('el-budget', t('maxBudget'), task.max_budget || '', '$5.00')
      + _f('el-turn', t('maxTurnTime'), task.timeout ? task.timeout+'s' : '', '5m')
      + _f('el-total', t('maxTotalTime'), task.max_total_time ? task.max_total_time+'s' : '', '1h')
      + _f('el-resched', t('maxReschedules'), task.max_reschedules || '', '50')
      + _f('el-maxiter', t('maxIterations'), task.max_iterations || '', '50')
      + `<div style="font-size:10px;color:var(--pf-muted);margin-bottom:8px;">${escapeHtml(t('currentTaskLimits', { cost: (task.total_cost||0).toFixed(4), reschedules: task.reschedule_count||0 }))}</div>`
      + `<div style="display:flex;gap:8px;justify-content:flex-end;"><button onclick="document.getElementById('resourceEditorOverlay').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextCancel'))}</button><button id="el-save" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextSave'))}</button></div>`;
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    document.getElementById('el-save').onclick = () => {
      const params = { task_id: taskId };
      const bv = document.getElementById('el-budget').value.trim();
      const tv = document.getElementById('el-turn').value.trim();
      const ttv = document.getElementById('el-total').value.trim();
      const rv = document.getElementById('el-resched').value.trim();
      const mv = document.getElementById('el-maxiter').value.trim();
      if (bv) params.max_budget = bv;
      if (tv) params.max_turn_time = tv;
      if (ttv) params.max_total_time = ttv;
      if (rv) params.max_reschedules = parseInt(rv) || 0;
      if (mv) params.max_iterations = parseInt(mv) || 0;
      action$('edit_task', params).subscribe(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', t('taskLimitsUpdated', { changes: (data.changed||[]).join(', ') }));
        overlay.remove();
        loadResources();
      });
    };
  });
}

function _showTaskDefLog(defName) {
  // Show all task instances for this definition, with their logs
  action$('list_resources', {}).subscribe(d => {
    const instances = (d.all_tasks || []).filter(t => t.task_def_name === defName);
    if (!instances.length) { addMsg('system', t('noTaskInstancesForDefinition', { name: defName })); return; }
    let lines = instances.map(t => {
      const icon = t.status === 'active' ? '\u25B6' : t.status === 'paused' ? '\u23F8' : t.status === 'completed' ? '\u2705' : t.status === 'cancelled' ? '\u2718' : '\u26A0';
      return icon + ' ' + t.task_id + ' (' + t.agent + ') — ' + t.status + ' [' + t.iterations + '/' + t.max_iterations + ']';
    }).join('\n');
    addMsg('system', t('taskInstancesTitle', { name: defName, lines: lines }));
    // Also fetch logs for each instance
    for (const inst of instances) {
      action$('task_log', { name: inst.task_id }).subscribe(ld => {
        const log = ld.log || [];
        if (log.length) {
          const logLines = log.map(l => (l.ts ? new Date(l.ts*1000).toLocaleTimeString() + ' ' : '') + (l.event || '') + (l.detail ? ': ' + l.detail : '')).join('\n');
          addMsg('system', t('logTitle', { id: inst.task_id, lines: logLines }));
        }
      });
    }
  });
}

function showServiceMenu(e, serviceId, scope, enabled) {
  e.preventDefault();
  const old = document.querySelector('.ctx-menu');
  if (old) old.remove();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.style.cssText = 'position:fixed;z-index:10000;background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px var(--pf-shadow);';
  _positionMenu(menu, e);
  const item = (label, fn, danger) => {
    const d = document.createElement('div');
    d.textContent = label;
    d.style.cssText = 'padding:6px 16px;cursor:pointer;font-size:12px;color:' + (danger ? 'var(--pf-danger)' : 'var(--pf-text)');
    d.onmouseenter = () => d.style.background = 'color-mix(in srgb, var(--pf-accent) 12%, var(--pf-panel))';
    d.onmouseleave = () => d.style.background = '';
    d.onclick = () => { menu.remove(); fn(); };
    menu.appendChild(d);
  };
  item('\u{1F441} ' + t('viewConfigMenu'), () => showServiceEditForm(serviceId, scope, true));
  if (_canEditScope(scope)) {
    item('\u270F ' + t('editWithEllipsis'), () => showServiceEditForm(serviceId, scope));
  }
  item(enabled ? '\u23F8 ' + t('blocked') : '\u25B6 ' + t('enabled'), () => {
    action$('toggle_service', { service_id: serviceId, scope, enabled: !enabled, conversation_id: conversationId }).subscribe(d => {
      if (d.error) addMsg('error', d.error);
      else loadResources();
    });
  });
  if (_canEditScope(scope)) {
    const sep = document.createElement('div');
    sep.style.cssText = 'height:1px;background:var(--pf-border);margin:4px 0;';
    menu.appendChild(sep);
    item('\u{1F5D1} ' + t('delete'), () => {
      if (!confirm(t('deleteServiceConfirm', { id: serviceId }))) return;
      action$('delete_service', { service_id: serviceId, scope, conversation_id: conversationId }).subscribe(d => {
        if (d.error) addMsg('error', d.error);
        else { addMsg('system', t('serviceDeleted', { id: serviceId })); loadResources(); }
      });
    }, true);
  }
  setTimeout(() => document.addEventListener('click', function _c() { menu.remove(); document.removeEventListener('click', _c); }), 0);
}

// ── Service schema-based form helpers ─────────────────────────────
const _svcInputStyle = 'width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;font-size:12px;';
const _svcLabelStyle = 'color:var(--pf-muted);font-size:11px;';
const _svcDescStyle = 'color:var(--pf-muted);font-size:10px;margin-top:1px;';

function _serviceCategoryLabel(category) {
  const key = 'serviceCategory.' + (category || 'other');
  const label = t(key);
  return label === key ? (category || 'Other') : label;
}

function _renderServiceTypeOptions(serviceTypes) {
  const groups = [];
  const byCategory = {};
  for (const st of serviceTypes) {
    const category = st.category || 'other';
    if (!byCategory[category]) {
      byCategory[category] = [];
      groups.push(category);
    }
    byCategory[category].push(st);
  }
  let html = '';
  for (const category of groups) {
    html += '<optgroup label="' + escapeHtml(_serviceCategoryLabel(category)) + '">';
    for (const st of byCategory[category]) {
      const label = (st.name || st.type) + (st.description ? ' - ' + st.description : '');
      html += '<option value="' + escapeHtml(st.type) + '">' + escapeHtml(label) + '</option>';
    }
    html += '</optgroup>';
  }
  return html;
}

function _renderSchemaFields(schema, values, readonly) {
  let html = '';
  const dis = readonly ? ' disabled' : '';
  const roS = readonly ? 'opacity:0.7;cursor:not-allowed;' : '';
  for (const [pname, pdef] of Object.entries(schema)) {
    const val = (values && values[pname] != null) ? values[pname] : (pdef.default != null ? pdef.default : '');
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    const label = escapeHtml(pdef.label || pname);
    const desc = pdef.description ? escapeHtml(pdef.description) : '';
    const req = pdef.required ? ' data-required="1"' : '';
    html += '<div class="svc-field" data-field="' + pname + '"' + req + ' style="margin-bottom:8px;">';
    html += '<label style="' + _svcLabelStyle + '">' + label + (pdef.required ? ' <span class="svc-req" style="color:var(--pf-danger)">*</span>' : '') + '</label>';
    if (desc) html += '<div style="' + _svcDescStyle + '">' + desc + '</div>';
    const ptype = pdef.type || 'string';
    if (ptype === 'boolean') {
      html += '<label style="display:flex;align-items:center;gap:6px;margin-top:4px;cursor:pointer;"><input id="svc-p-' + pname + '" type="checkbox"' + (val ? ' checked' : '') + dis + ' style="accent-color:var(--pf-accent);"/> <span style="color:var(--pf-text);font-size:12px;">Enabled</span></label>';
    } else if (ptype === 'select' && pdef.options) {
      html += '<select id="svc-p-' + pname + '"' + dis + ' style="' + _svcInputStyle + roS + '">';
      for (const opt of pdef.options) {
        html += '<option value="' + opt + '"' + (String(val) === String(opt) ? ' selected' : '') + '>' + opt + '</option>';
      }
      html += '</select>';
    } else if (ptype === 'service_ref') {
      const st = (pdef.service_type || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const pf = (pdef.provider_field || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const fp = (pdef.provider || '').replace(/&/g,'&amp;').replace(/"/g,'&quot;');
      const aliases = JSON.stringify(pdef.provider_aliases || {}).replace(/&/g,'&amp;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      html += '<select id="svc-p-' + pname + '" data-service-ref="1" data-service-type="' + st + '" data-provider-field="' + pf + '" data-provider="' + fp + '" data-provider-aliases=\'' + aliases + '\' data-current="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + '">';
      html += '<option value="' + escaped + '">' + (escaped || '(auto)') + '</option>';
      html += '</select>';
    } else if (ptype === 'textarea' || ptype === 'map' || ptype === 'object') {
      const tval = (ptype === 'map' || ptype === 'object') && typeof val === 'object' ? JSON.stringify(val, null, 2) : escaped;
      html += '<textarea id="svc-p-' + pname + '"' + dis + ' style="' + _svcInputStyle + roS + 'min-height:80px;font-family:monospace;resize:vertical;">' + tval + '</textarea>';
    } else if (ptype === 'integer' || ptype === 'float') {
      html += '<input id="svc-p-' + pname + '" type="number"' + (ptype === 'float' ? ' step="any"' : '') + ' value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + 'width:120px;"/>';
    } else if (pdef.sensitive) {
      html += '<div style="display:flex;gap:4px;align-items:center;">'
        + '<input id="svc-p-' + pname + '" type="password" value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + 'flex:1;"/>'
        + '<button type="button" onclick="_togglePwdVis(\'svc-p-' + pname + '\',this)" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px;" title="' + escapeHtml(t('showHide')) + '">\u{1F441}</button>'
        + '</div>';
    } else {
      html += '<input id="svc-p-' + pname + '" type="text" value="' + escaped + '"' + dis + ' style="' + _svcInputStyle + roS + '"/>';
    }
    html += '</div>';
  }
  return html;
}

function _serviceRefProviderMatches(serviceProvider, wantedProvider, aliases) {
  const canonical = (provider) => {
    provider = String(provider || '').trim();
    return (aliases && aliases[provider]) || provider;
  };
  return !wantedProvider || canonical(serviceProvider) === canonical(wantedProvider);
}

async function _populateServiceRefs(container) {
  const refs = Array.from(container.querySelectorAll('select[data-service-ref="1"]'));
  for (const sel of refs) {
    const serviceType = sel.dataset.serviceType || '';
    const providerField = sel.dataset.providerField || '';
    const providerEl = providerField ? container.querySelector('#svc-p-' + providerField) : null;
    const wantedProvider = (sel.dataset.provider || '') || (providerEl ? providerEl.value : '');
    const current = sel.value || sel.dataset.current || '';
    let aliases = {};
    try { aliases = JSON.parse(sel.dataset.providerAliases || '{}'); } catch (_) { aliases = {}; }
    try {
      const data = await rxjs.firstValueFrom(listServices$(serviceType));
      const services = (data.services || []).filter(s => _serviceRefProviderMatches(s.provider, wantedProvider, aliases));
      let html = '<option value="">(auto)</option>';
      for (const s of services) {
        const id = String(s.service_id || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        const label = id + (s.scope ? ' [' + s.scope + ']' : '');
        html += '<option value="' + id + '">' + label + '</option>';
      }
      sel.innerHTML = html;
      sel.value = current;
      if (current && sel.value !== current) {
        sel.insertAdjacentHTML('afterbegin', '<option value="' + current + '">' + current + ' (missing)</option>');
        sel.value = current;
      }
      if (providerEl && !providerEl.dataset.serviceRefListener) {
        providerEl.dataset.serviceRefListener = '1';
        providerEl.addEventListener('change', () => _populateServiceRefs(container));
      }
    } catch (e) {
      // Keep the raw current option if service listing fails.
    }
  }
}

function _collectSchemaValues(schema) {
  const config = {};
  for (const [pname, pdef] of Object.entries(schema)) {
    const el = document.getElementById('svc-p-' + pname);
    if (!el) continue;
    const wrapper = el.closest('.svc-field');
    if (wrapper && wrapper.style.display === 'none') continue;
    const ptype = pdef.type || 'string';
    if (ptype === 'boolean') {
      config[pname] = el.checked;
    } else if (ptype === 'integer') {
      config[pname] = parseInt(el.value) || 0;
    } else if (ptype === 'float') {
      config[pname] = parseFloat(el.value) || 0;
    } else if (ptype === 'map' || ptype === 'object') {
      try { config[pname] = JSON.parse(el.value || '{}'); } catch { config[pname] = el.value; }
    } else {
      config[pname] = el.value;
    }
  }
  return config;
}

function _applyRules(container, rules, actions, serviceId) {
  rules = rules || [];
  actions = actions || [];
  const getVal = (name) => {
    const el = container.querySelector('#svc-p-' + name);
    if (!el) return null;
    return el.type === 'checkbox' ? String(el.checked) : el.value;
  };
  const _matchWhen = (when) => Object.entries(when).every(([field, values]) =>
    Array.isArray(values) ? values.includes(getVal(field)) : getVal(field) === values
  );

  const apply = () => {
    // Reset: all fields visible, none required
    container.querySelectorAll('.svc-field').forEach(f => {
      f.style.display = '';
      const lbl = f.querySelector('label');
      if (lbl) {
        lbl.querySelector('.svc-req')?.remove();
        if (f.dataset.required === '1') {
          lbl.insertAdjacentHTML('beforeend', ' <span class="svc-req" style="color:var(--pf-danger)">*</span>');
        }
      }
    });
    // Evaluate rules in order
    for (const rule of rules) {
      if (!_matchWhen(rule.when)) continue;
      for (const [field, effects] of Object.entries(rule.set || {})) {
        const wrapper = container.querySelector('[data-field="' + field + '"]');
        if (!wrapper) continue;
        if (effects.visible === false) wrapper.style.display = 'none';
        if (effects.visible === true) wrapper.style.display = '';
        if (effects.required) {
          const lbl = wrapper.querySelector('label');
          if (lbl && !lbl.querySelector('.svc-req'))
            lbl.insertAdjacentHTML('beforeend', ' <span class="svc-req" style="color:var(--pf-danger)">*</span>');
        }
        if (effects.default !== undefined) {
          const input = wrapper.querySelector('input,select,textarea');
          if (input && !input.value) input.value = effects.default;
        }
        if (effects.options) {
          const sel = wrapper.querySelector('select');
          if (sel) {
            const cur = sel.value;
            sel.innerHTML = effects.options.map(o =>
              '<option value="' + o + '"' + (o === cur ? ' selected' : '') + '>' + o + '</option>').join('');
          }
        }
      }
    }
    // Show/hide action buttons based on when conditions
    container.querySelectorAll('[data-action-when]').forEach(btn => {
      try {
        const when = JSON.parse(btn.dataset.actionWhen);
        btn.style.display = _matchWhen(when) ? '' : 'none';
      } catch { btn.style.display = ''; }
    });
  };

  // Listen to trigger fields
  const triggers = new Set(rules.flatMap(r => Object.keys(r.when)));
  if (actions) actions.forEach(a => { if (a.when) Object.keys(a.when).forEach(k => triggers.add(k)); });
  triggers.forEach(name => {
    const el = container.querySelector('#svc-p-' + name);
    if (el) el.addEventListener('change', apply);
  });
  apply();
}

function _renderServiceActions(actions, serviceId, scope) {
  if (!actions || !actions.length) return '';
  scope = scope || '';
  let html = '<div class="svc-actions" style="margin-top:12px;padding-top:8px;border-top:1px solid var(--pf-border);">';
  for (const a of actions) {
    const whenAttr = a.when ? ' data-action-when=\'' + JSON.stringify(a.when).replace(/'/g, '&#39;') + '\'' : '';
    html += '<button type="button" onclick="_executeServiceAction(\'' + a.id + '\',\'' + serviceId + '\',\'' + (a.flow || 'simple') + '\',\'' + (a.server_action || '') + '\',\'' + scope + '\')"'
      + whenAttr + ' style="background:color-mix(in srgb, var(--pf-accent) 14%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);border-radius:4px;padding:6px 12px;cursor:pointer;font-size:12px;margin-right:8px;">'
      + (a.icon || '') + ' ' + (a.label || a.id) + '</button>';
  }
  html += '</div>';
  return html;
}

// -- Slash command handlers for claude login --

function cmdClaudeLoginServer(parts) {
  const sub = (parts[1] || '').toLowerCase();

  // /cls pool [@service] — list credentials
  if (sub === 'pool') {
    var svcId = stripTarget(parts[2]) || 'claude_code_llm_service';
    action$('claude_pool_list', { service_id: svcId }).subscribe(data => {
      if (!data.pool || !data.pool.length) { addMsg('system', t('noCredentialsInPool')); return; }
      var lines = ['**' + t('credentialsPoolHeader', { count: data.count }) + '**'];
      data.pool.forEach(function(c) {
        lines.push('  ' + c.index + '. ' + (c.account || t('unknown')) + ' — ' + t('expiresLabel', { value: c.expires_in }));
      });
      addMsg('system', lines.join('\n'));
    });
    return true;
  }

  // /cls reset [@service] — clear all credentials
  if (sub === 'reset') {
    var svcId2 = stripTarget(parts[2]) || 'claude_code_llm_service';
    action$('claude_pool_reset', { service_id: svcId2 }).subscribe(data => {
      addMsg('system', data.message || data.error || t('done'));
    });
    return true;
  }

  // /cls remove <index> [@service] — remove one credential
  if (sub === 'remove') {
    var idx = parseInt(parts[2] || '-1', 10);
    var svcId3 = stripTarget(parts[3]) || 'claude_code_llm_service';
    if (idx < 0) { addMsg('error', t('claudeRemoveUsage')); return true; }
    action$('claude_pool_remove', { service_id: svcId3, index: idx }).subscribe(data => {
      addMsg('system', data.message || data.error || t('done'));
    });
    return true;
  }

  // /cls <service> — login (add credential to pool)
  var serviceId = stripTarget(sub);
  if (!serviceId) { addMsg('error', t('claudeUsage')); return true; }
  if (window._clsLoginPending) { addMsg('system', t('loginAlreadyInProgress')); return true; }
  window._clsLoginPending = true;
  addMsg('system', t('startingClaudeLogin', { service: serviceId }));
  fireAction('claude_code_server_login', { service_id: serviceId });
  // Reset after 60s (container timeout)
  setTimeout(function() { window._clsLoginPending = false; }, 60000);
  return true;
}

function cmdClaudeLoginRelay(parts) {
  const serviceId = stripTarget(parts[1]);
  const relayId = stripTarget(parts[2]);
  if (!serviceId) { addMsg('error', t('claudeRelayUsage')); return true; }

  if (relayId) {
    _startRelayLogin(serviceId, relayId);
    return true;
  }

  // No relay specified — list and auto-select if single
  action$('claude_code_list_relays', { service_id: serviceId }).subscribe(resp => {
    const relays = resp.relays || [];
    if (relays.length === 0) { addMsg('error', t('noRelayConnected')); return; }
    if (relays.length === 1) {
      _startRelayLogin(serviceId, relays[0].relay_id);
    } else {
      addMsg('system', t('multipleRelaysAvailable', { relays: relays.map(r => '  ' + r.relay_id + ' (' + r.platform + ')').join('\n') }));
    }
  });
  return true;
}

function cmdClaudeLoginCredentials(text, parts) {
  const serviceId = stripTarget(parts[1]);
  if (!serviceId) { addMsg('error', t('claudeCredentialsUsage')); return true; }
  const jsonStart = text.indexOf(parts[1]) + parts[1].length;
  const credsJson = text.substring(jsonStart).trim();
  if (!credsJson) { addMsg('error', t('missingCredentialsJson')); return true; }
  try {
    JSON.parse(credsJson);
  } catch (e) {
    addMsg('error', t('invalidJsonMessage', { error: e.message }));
    return true;
  }
  fireAction('claude_code_login_code', { service_id: serviceId, credentials: credsJson });
  return true;
}

// `cli` is one of: 'claude' | 'codex' | 'gemini' | 'rclone' — picks the right server
// status/cleanup actions (each CLI has its own dedicated namespace).
// `token` is the capability token issued by the backend at session
// register time; without it the iframe URL will 401/403 — leaving it
// empty is only valid in legacy-tooling test paths.
function _openVncLoginDialog(sessionId, serviceId, token, triggerBtn, cli, scope) {
  cli = cli || 'claude';
  scope = scope || '';
  const _statusAction = {
    'claude': 'claude_code_server_login_status',
    'codex':  'codex_server_login_status',
    'gemini': 'gemini_server_login_status',
    'rclone': 'rclone_server_login_status',
  }[cli] || 'claude_code_server_login_status';
  const _cleanupAction = {
    'claude': 'claude_code_server_login_cleanup',
    'codex':  'codex_server_login_cleanup',
    'gemini': 'gemini_server_login_cleanup',
    'rclone': 'rclone_server_login_cleanup',
  }[cli] || 'claude_code_server_login_cleanup';
  const _title = {
    'claude': 'Claude Code Login',
    'codex':  'Codex Login',
    'gemini': 'Gemini Login',
    'rclone': 'Rclone Login',
  }[cli] || t('loginTitle');

  window._clsLoginPending = false;
  // Create overlay dialog 80%x80%
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:var(--pf-shadow);z-index:10000;display:flex;align-items:center;justify-content:center;';
  const dialog = document.createElement('div');
  dialog.style.cssText = 'width:80%;height:80%;background:var(--pf-panel);border-radius:8px;display:flex;flex-direction:column;overflow:hidden;';
  const header = document.createElement('div');
  header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:8px 16px;background:var(--pf-panel);';
  header.innerHTML = '<span style="color:var(--pf-muted);font-size:13px;">' + _title + '</span>'
    + '<button id="vnc-dialog-close" style="background:none;border:none;color:var(--pf-danger);font-size:18px;cursor:pointer;">&times;</button>';
  const vncUrl = '/vnc/' + sessionId + '/' + token + '/vnc.html?autoconnect=true&resize=scale'
    + '&path=vnc/' + sessionId + '/' + token + '/websockify';
  const iframe = document.createElement('iframe');
  iframe.src = vncUrl;
  iframe.style.cssText = 'flex:1;border:none;background:var(--pf-code-bg);';
  iframe.allow = 'clipboard-read; clipboard-write';
  const status = document.createElement('div');
  status.style.cssText = 'padding:6px 16px;color:var(--pf-muted);font-size:11px;background:var(--pf-panel);';
  status.textContent = t('waitingAuthorization');

  dialog.appendChild(header);
  dialog.appendChild(iframe);
  dialog.appendChild(status);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  function closeDialog(msg) {
    clearInterval(pollInterval);
    overlay.remove();
    if (triggerBtn) {
      triggerBtn.textContent = t('loginViaServer');
      triggerBtn.disabled = false;
      triggerBtn.style.display = '';
    }
    if (msg) addMsg('system', msg);
    // Tell server to cleanup the Docker container (per-CLI action namespace)
    fireAction(_cleanupAction, { session_id: sessionId });
  }

  document.getElementById('vnc-dialog-close').onclick = () => closeDialog(null);
  overlay.onclick = (e) => { if (e.target === overlay) closeDialog(null); };

  // Poll for completion (per-CLI status action)
  const pollInterval = setInterval(async () => {
    try {
      const st = await rxjs.firstValueFrom(action$(_statusAction, {
        session_id: sessionId, service_id: serviceId, scope }));
      if (st.ok) { closeDialog(st.message || t('loginSuccessful', { title: _title })); }
      else if (st.error) { closeDialog(t('loginError', { error: st.error })); }
      else if (st.status === 'starting') { status.textContent = t('startingContainer'); }
      else if (st.status === 'pending') { status.textContent = t('waitingAuthorization'); }
    } catch (e) { /* ignore */ }
  }, 3000);
}

// Per-CLI relay-login action namespace.
function _resolveRelayLoginAction(cli) {
  return ({
    'claude': 'claude_code_relay_login',
    'codex':  'codex_relay_login',
    'gemini': 'gemini_relay_login',
  })[cli] || 'claude_code_relay_login';
}

function _startRelayLogin(serviceId, relayId, cli) {
  cli = cli || 'claude';
  const _label = ({ 'claude': 'Claude Code', 'codex': 'Codex', 'gemini': 'Gemini' })[cli] || 'Claude Code';
  addMsg('system', t('startingRelayLogin', { label: _label }));
  fireAction(_resolveRelayLoginAction(cli), { service_id: serviceId, relay_id: relayId });
}

// Map a flow id (set in services/llm_connection.py get_service_actions) to the
// CLI label used by the dialog + per-CLI action selectors. Keeps the code
// shape identical across CLIs (claude/codex/gemini) while routing each one to
// its dedicated server action namespace.
function _flowToCli(flow) {
  if (flow.indexOf('codex_') === 0) return 'codex';
  if (flow.indexOf('gemini_') === 0) return 'gemini';
  if (flow.indexOf('rclone_') === 0) return 'rclone';
  return 'claude';
}

async function _renderCredentialPoolTable(serviceId, anchorBtn) {
  const container = anchorBtn ? anchorBtn.parentElement : null;
  if (!container) return;
  let panel = container.querySelector('[data-credential-pool-panel="1"]');
  if (!panel) {
    panel = document.createElement('div');
    panel.dataset.credentialPoolPanel = '1';
    panel.style.cssText = 'margin-top:8px;border:1px solid var(--pf-border);border-radius:6px;padding:8px;background:var(--pf-panel);';
    container.appendChild(panel);
  }
  panel.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + t('loadingCredentials') + '</div>';

  const load = async () => {
    const resp = await rxjs.firstValueFrom(action$('llm_credential_pool_list', { service_id: serviceId }));
    if (resp.error) {
      panel.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(resp.error) + '</div>';
      return;
    }
    const rows = resp.pool || [];
    let html = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
      + '<strong style="color:var(--pf-text);font-size:12px;">' + escapeHtml(t('providerCredentials', { provider: resp.provider || 'OAuth' })) + '</strong>'
      + '<span style="color:var(--pf-muted);font-size:11px;">' + t('loginCount', { n: rows.length }) + '</span></div>';
    if (!rows.length) {
      html += '<div style="color:var(--pf-muted);font-size:11px;">' + t('noCredentialsSaved') + '</div>';
    } else {
      html += '<table style="width:100%;border-collapse:collapse;font-size:11px;color:var(--pf-text);">'
        + '<thead><tr style="color:var(--pf-muted);text-align:left;"><th>#</th><th>' + t('account') + '</th><th>' + t('status') + '</th><th>' + t('expires') + '</th><th></th></tr></thead><tbody>';
      for (const r of rows) {
        const idx = Number(r.index || 0);
        const status = r.valid ? t('valid') : t('expired');
        const statusColor = r.valid ? 'var(--pf-success)' : 'var(--pf-danger)';
        html += '<tr data-cred-index="' + idx + '" style="border-top:1px solid var(--pf-border);">'
          + '<td style="padding:5px 4px;">' + idx + '</td>'
          + '<td style="padding:5px 4px;">' + escapeHtml(r.account || t('unknownAccount')) + '</td>'
          + '<td style="padding:5px 4px;color:' + statusColor + ';">' + status + '</td>'
          + '<td style="padding:5px 4px;">' + escapeHtml(r.expires_in || '') + '</td>'
          + '<td style="padding:5px 4px;text-align:right;white-space:nowrap;">'
          + '<button type="button" data-cred-refresh="' + idx + '" style="background:color-mix(in srgb, var(--pf-accent-2) 18%, var(--pf-panel));color:var(--pf-text);border:1px solid var(--pf-accent-2);border-radius:4px;padding:3px 7px;margin-right:4px;cursor:pointer;font-size:11px;">' + t('refresh') + '</button>'
          + '<button type="button" data-cred-delete="' + idx + '" style="background:color-mix(in srgb, var(--pf-danger) 16%, var(--pf-panel));color:var(--pf-danger);border:1px solid var(--pf-danger);border-radius:4px;padding:3px 7px;cursor:pointer;font-size:11px;">' + t('delete') + '</button>'
          + '</td></tr>';
      }
      html += '</tbody></table>';
    }
    panel.innerHTML = html;
    panel.querySelectorAll('[data-cred-refresh]').forEach(b => b.addEventListener('click', async () => {
      b.disabled = true;
      b.textContent = t('refreshing');
      const res = await rxjs.firstValueFrom(action$('llm_credential_pool_refresh', { service_id: serviceId, index: Number(b.dataset.credRefresh) }));
      if (res.error) addMsg('error', res.error);
      await load();
    }));
    panel.querySelectorAll('[data-cred-delete]').forEach(b => b.addEventListener('click', async () => {
      const idx = Number(b.dataset.credDelete);
      if (!confirm(t('deleteCredentialConfirm', { index: idx }))) return;
      b.disabled = true;
      const res = await rxjs.firstValueFrom(action$('llm_credential_pool_remove', { service_id: serviceId, index: idx }));
      if (res.error) addMsg('error', res.error);
      await load();
    }));
  };
  await load();
}

async function _executeServiceAction(actionId, serviceId, flow, serverAction, scope) {
  const btn = event && event.target ? event.target : null;
  const _cli = _flowToCli(flow);
  const payload = { service_id: serviceId };
  if (scope) payload.scope = scope;
  if (flow === 'credential_table') {
    try { await _renderCredentialPoolTable(serviceId, btn); }
    catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else if (flow === 'claude_login_server' || flow === 'codex_login_server' || flow === 'gemini_login_server' || flow === 'rclone_login_server') {
    try {
      if (btn) { btn.disabled = true; btn.textContent = t('starting'); }
      fireAction(serverAction, payload);
      // Dialog opens when SSE vnc_login_ready arrives (with `cli` field)
    } catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else if (flow === 'claude_login_relay' || flow === 'codex_login_relay' || flow === 'gemini_login_relay') {
    try {
      // Step 1: list relays
      const resp = await rxjs.firstValueFrom(action$(serverAction, payload));
      if (resp.error) { addMsg('error', resp.error); return; }
      const relays = resp.relays || [];
      if (relays.length === 0) {
        addMsg('system', t('noRelayUseCredentials'));
        return;
      }
      // Single relay → skip selector, start directly
      if (relays.length === 1) {
        if (btn) { btn.disabled = true; btn.textContent = t('waitingForAuthorization'); }
        await _startRelayLogin(serviceId, relays[0].relay_id, _cli);
        if (btn) { btn.disabled = false; btn.textContent = t('loginViaRelay'); }
        return;
      }
      // Multiple relays → show selector
      const container = btn ? btn.parentElement : null;
      if (!container) return;
      const div = document.createElement('div');
      div.style.cssText = 'margin-top:8px;';
      let selectHtml = '<select id="svc-relay-select" style="' + _svcInputStyle + 'margin-bottom:4px;">';
      relays.forEach(r => {
        const label = r.relay_id + ' (' + r.platform + ')';
        selectHtml += '<option value="' + escapeHtml(r.relay_id) + '">' + escapeHtml(label) + '</option>';
      });
      selectHtml += '</select>';
      div.innerHTML = selectHtml
        + '<button type="button" id="svc-relay-login-btn" style="background:var(--pf-accent);color:var(--pf-bg);border:none;'
        + 'padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;">' + escapeHtml(t('startLogin')) + '</button>'
        + '<div id="svc-relay-status" style="color:var(--pf-muted);font-size:11px;margin-top:4px;"></div>';
      container.appendChild(div);

      document.getElementById('svc-relay-login-btn').addEventListener('click', () => {
        const relayId = document.getElementById('svc-relay-select').value;
        const statusEl = document.getElementById('svc-relay-status');
        const loginBtn = document.getElementById('svc-relay-login-btn');
        loginBtn.disabled = true;
        loginBtn.textContent = t('waitingForAuthorization');
        statusEl.textContent = t('relayBrowserAuthorizeHint');

        fireAction(_resolveRelayLoginAction(_cli), {
          service_id: serviceId,
          relay_id: relayId,
        });
        statusEl.textContent = t('authorizeInRelayBrowser');
        // Result arrives via SSE command_result
      });
    } catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else if (flow === 'oauth_code') {
    try {
      // Step 1: get instructions
      const resp = await rxjs.firstValueFrom(action$(serverAction, payload));
      if (resp.error) { addMsg('error', resp.error); return; }

      // Step 2: show instructions + textarea for credentials
      const container = btn ? btn.parentElement : null;
      if (container) {
        const loginDiv = document.createElement('div');
        loginDiv.style.cssText = 'margin-top:8px;';
        loginDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;white-space:pre-line;margin-bottom:6px;">' + escapeHtml(resp.message) + '</div>'
          + '<textarea id="svc-creds-input" placeholder="' + escapeHtml(t('pasteCredentialsJson')) + '" '
          + 'style="' + _svcInputStyle + 'min-height:80px;font-family:monospace;font-size:11px;"></textarea>'
          + '<button type="button" id="svc-creds-submit" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px;margin-top:4px;">' + escapeHtml(t('saveCredentials')) + '</button>';
        container.appendChild(loginDiv);

        const submitBtn = document.getElementById('svc-creds-submit');
        submitBtn.addEventListener('click', async () => {
          const creds = document.getElementById('svc-creds-input').value.trim();
          if (!creds) return;
          submitBtn.textContent = '...';
          submitBtn.disabled = true;
          try {
            const result = await rxjs.firstValueFrom(action$(serverAction.replace('_url', '_code'), { ...payload, credentials: creds }));
            if (result.ok) {
              loginDiv.innerHTML = '<span style="color:var(--pf-success);font-size:12px;">\u2714 ' + escapeHtml(result.message || t('saved')) + '</span>';
            } else {
              submitBtn.textContent = t('saveCredentials');
              submitBtn.disabled = false;
              loginDiv.insertAdjacentHTML('beforeend',
                '<div style="color:var(--pf-danger);font-size:11px;margin-top:4px;">' + escapeHtml(result.error) + '</div>');
            }
          } catch (e) {
            loginDiv.innerHTML = '<span style="color:var(--pf-danger);font-size:12px;">\u2718 ' + e.message + '</span>';
          }
        });
      }
    } catch (e) { addMsg('error', t('actionFailed', { error: e.message })); }
  } else {
    if (flow === 'confirm' && !confirm(t('executeActionConfirm', { action: actionId }))) return;
    try {
      const resp = await rxjs.firstValueFrom(action$(serverAction, payload));
      addMsg('system', resp.message || resp.error || JSON.stringify(resp));
    } catch (e) { addMsg('error', e.message); }
  }
}

// Legacy compat
function _applyShowWhen(container) { /* replaced by _applyRules */ }

let _svcSchemaCache = {};

async function _fetchServiceSchema(serviceType) {
  if (_svcSchemaCache[serviceType]) return _svcSchemaCache[serviceType];
  try {
    const data = await rxjs.firstValueFrom(action$('get_service_schema', { service_type: serviceType }));
    if (data.error) { addMsg('error', data.error); return {parameters: {}, rules: [], actions: []}; }
    _svcSchemaCache[serviceType] = {
      parameters: data.parameters || {},
      rules: data.rules || [],
      actions: data.actions || [],
    };
    return _svcSchemaCache[serviceType];
  } catch (e) { addMsg('error', e.message); return {parameters: {}, rules: [], actions: []}; }
}

async function showServiceInstallForm() {
  let serviceTypes = [];
  try {
    const data = await rxjs.firstValueFrom(action$('list_service_types', {}));
    if (data.error) { addMsg('error', data.error); return; }
    serviceTypes = data.service_types || [];
  } catch (e) { addMsg('error', e.message); return; }
  if (!serviceTypes.length) { addMsg('error', t('noServiceTypesAvailable')); return; }

  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';

  const typeOpts = _renderServiceTypeOptions(serviceTypes);

  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('installService')) + '</h3>'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
    + '</div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('name')) + ' <span style="color:var(--pf-danger);">*</span></label>'
    + '<input id="svc-install-name" style="' + _svcInputStyle + '" placeholder="my_service"/></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('type')) + ' <span style="color:var(--pf-danger);">*</span></label>'
    + '<select id="svc-install-type" style="' + _svcInputStyle + '">' + typeOpts + '</select></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('description')) + '</label>'
    + '<input id="svc-install-desc" style="' + _svcInputStyle + '" placeholder="' + escapeHtml(t('optionalDescription')) + '"/></div>'
    + '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('scope')) + '</label>'
    + '<select id="svc-install-scope" style="' + _svcInputStyle + '">'
    + (_isAdmin() ? '<option value="global">' + t('global') + '</option>' : '')
    + '<option value="user">' + escapeHtml(t('user')) + '</option></select></div>'
    + '<div id="svc-install-params" style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;"></div>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('cancel')) + '</button>'
    + '<button id="svc-install-login-btn" onclick="_submitServiceInstall(true)" style="display:none;background:color-mix(in srgb, var(--pf-accent) 16%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);padding:8px 16px;border-radius:4px;cursor:pointer;">Installer + login</button>'
    + '<button id="svc-install-btn" onclick="_submitServiceInstall()" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('install')) + '</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  const typeSelect = document.getElementById('svc-install-type');
  const loadParams = async () => {
    const paramsDiv = document.getElementById('svc-install-params');
    paramsDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('loadingParameters')) + '</div>';
    const schemaData = await _fetchServiceSchema(typeSelect.value);
    panel.dataset.schema = JSON.stringify(schemaData.parameters || {});
    panel.dataset.rules = JSON.stringify(schemaData.rules || []);
    panel.dataset.actions = JSON.stringify(schemaData.actions || []);
    const params = schemaData.parameters || {};
    if (Object.keys(params).length === 0) {
      paramsDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('noConfigurableParametersForServiceType')) + '</div>';
    } else {
      paramsDiv.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>'
        + _renderSchemaFields(params, {});
      _applyRules(paramsDiv, schemaData.rules || [], schemaData.actions || [], '');
      _populateServiceRefs(paramsDiv);
      paramsDiv.addEventListener('change', _updateServiceInstallLoginButton);
    }
    _updateServiceInstallLoginButton();
  };
  typeSelect.addEventListener('change', async () => { await loadParams(); _updateServiceInstallLoginButton(); });
  await loadParams();
  document.getElementById('svc-install-name').focus();
}

function _updateServiceInstallLoginButton() {
  const typeEl = document.getElementById('svc-install-type');
  const btn = document.getElementById('svc-install-login-btn');
  if (!typeEl || !btn) return;
  const panel = document.querySelector('#resourceEditorOverlay > div');
  let schema = {};
  try { schema = JSON.parse(panel.dataset.schema || '{}'); } catch (_) { schema = {}; }
  const config = _collectSchemaValues(schema);
  const provider = String(config.provider || '').trim();
  const eligible = typeEl.value === 'rcloneOAuthCredentials' && (provider === 'drive' || provider === 'onedrive');
  btn.style.display = eligible ? '' : 'none';
}

async function _submitServiceInstall(loginAfterInstall) {
  const name = (document.getElementById('svc-install-name').value || '').trim();
  const svcType = document.getElementById('svc-install-type').value;
  const desc = (document.getElementById('svc-install-desc').value || '').trim();
  const scope = document.getElementById('svc-install-scope').value;
  if (!name) { alert(t('serviceNameRequired')); return; }
  const panel = document.querySelector('#resourceEditorOverlay > div');
  const schema = JSON.parse(panel.dataset.schema || '{}');
  const config = _collectSchemaValues(schema);
  const btn = document.getElementById('svc-install-btn');
  const loginBtn = document.getElementById('svc-install-login-btn');
  btn.disabled = true; btn.textContent = t('installing');
  if (loginBtn) { loginBtn.disabled = true; loginBtn.textContent = t('installing'); }
  try {
    const data = await rxjs.firstValueFrom(action$('service_install', { service_name: name, service_type: svcType, description: desc, config, scope, conversation_id: conversationId }));
    if (data.error) {
      addMsg('error', data.error);
      btn.disabled = false; btn.textContent = t('install');
      if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = 'Installer + login'; }
      return;
    }
    addMsg('system', t('serviceInstalledSuccessfully', { service: name }));
    document.getElementById('resourceEditorOverlay').remove();
    loadResources();
    if (loginAfterInstall) {
      fireAction('rclone_server_login', { service_id: name, scope });
    }
  } catch (e) {
    addMsg('error', e.message);
    btn.disabled = false; btn.textContent = t('install');
    if (loginBtn) { loginBtn.disabled = false; loginBtn.textContent = 'Installer + login'; }
  }
}

async function showServiceEditForm(serviceId, scope, readonly) {
  try {
    const data = await rxjs.firstValueFrom(action$('get_service_detail', { service_id: serviceId, scope, conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); return; }

    const svcType = data.service_type || '';
    const config = data.config || {};
    const schemaData = await _fetchServiceSchema(svcType);
    const schema = schemaData.parameters || schemaData;  // compat: old format was just params
    const rules = schemaData.rules || [];
    const actions = schemaData.actions || [];
    const ro = !!readonly;
    const disabledAttr = ro ? ' disabled' : '';
    const roStyle = ro ? 'opacity:0.7;cursor:not-allowed;' : '';

    let overlay = document.getElementById('resourceEditorOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'resourceEditorOverlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:85vh;overflow-y:auto;border:1px solid var(--pf-border);';

    const title = ro ? t('viewServiceTitle') : t('editServiceTitle');
    let formHtml = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
      + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + title + serviceId + ' ' + _scopeBadge(scope) + '</h3>'
      + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
      + '</div>';
    formHtml += '<div style="margin-bottom:8px;"><label style="' + _svcLabelStyle + '">' + escapeHtml(t('type')) + '</label>'
      + '<input value="' + svcType + '" disabled style="' + _svcInputStyle + 'opacity:0.6;cursor:not-allowed;"/></div>';

    if (Object.keys(schema).length > 0) {
      formHtml += '<div style="border-top:1px solid var(--pf-border);padding-top:8px;margin-top:8px;">'
        + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;font-weight:600;">' + escapeHtml(t('parameters')) + '</div>'
        + _renderSchemaFields(schema, config, ro)
        + (ro ? '' : _renderServiceActions(actions, serviceId, scope))
        + '</div>';
    } else {
      for (const [k, v] of Object.entries(config)) {
        const isSecret = k.toLowerCase().includes('key') || k.toLowerCase().includes('secret') || k.toLowerCase().includes('token');
        const inputType = isSecret ? 'password' : 'text';
        const val = String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
        if (isSecret) {
          formHtml += '<div style="margin-bottom:6px;"><label style="' + _svcLabelStyle + '">' + k + '</label>'
            + '<div style="display:flex;gap:4px;align-items:center;">'
            + '<input id="svc-p-' + k + '" type="password" value="' + val + '"' + disabledAttr + ' style="' + _svcInputStyle + roStyle + 'flex:1;"/>'
            + '<button type="button" onclick="_togglePwdVis(\'svc-p-' + k + '\',this)" style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;padding:4px 8px;cursor:pointer;font-size:12px;" title="' + t('showHide') + '">\u{1F441}</button>'
            + '</div></div>';
        } else {
          formHtml += '<div style="margin-bottom:6px;"><label style="' + _svcLabelStyle + '">' + k + '</label>'
            + '<input id="svc-p-' + k + '" type="text" value="' + val + '"' + disabledAttr + ' style="' + _svcInputStyle + roStyle + '"/></div>';
        }
      }
    }

    if (!ro) {
      formHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + t('contextCancel') + '</button>'
        + '<button id="svc-save-btn" onclick="_submitServiceEdit(\'_SVC_ID_\',\'_SVC_SCOPE_\')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + t('contextSave') + '</button>'
        + '</div>';
      formHtml = formHtml.replace('_SVC_ID_', serviceId).replace('_SVC_SCOPE_', scope);
    } else {
      formHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + t('close') + '</button>'
        + '</div>';
    }

    panel.innerHTML = formHtml;
    const effectiveSchema = Object.keys(schema).length > 0 ? schema : Object.fromEntries(Object.keys(config).map(k => [k, {type: 'string'}]));
    panel.dataset.schema = JSON.stringify(effectiveSchema);
    overlay.appendChild(panel);
    document.body.appendChild(overlay);
    panel.dataset.rules = JSON.stringify(rules);
    panel.dataset.actions = JSON.stringify(actions);
    _applyRules(panel, rules, actions, serviceId);
    _populateServiceRefs(panel);
  } catch (e) { addMsg('error', e.message); }
}

async function _submitServiceEdit(serviceId, scope) {
  const panel = document.querySelector('#resourceEditorOverlay > div');
  const schema = JSON.parse(panel.dataset.schema || '{}');
  const config = _collectSchemaValues(schema);
  const btn = document.getElementById('svc-save-btn');
  btn.disabled = true; btn.textContent = t('saving');
  try {
    const data = await rxjs.firstValueFrom(action$('update_service', { service_id: serviceId, scope, config, conversation_id: conversationId }));
    if (data.error) { addMsg('error', data.error); btn.disabled = false; btn.textContent = t('contextSave'); return; }
    addMsg('system', t('serviceUpdated', { service: serviceId }));
    document.getElementById('resourceEditorOverlay').remove();
    loadResources();
  } catch (e) { addMsg('error', e.message); btn.disabled = false; btn.textContent = t('contextSave'); }
}
