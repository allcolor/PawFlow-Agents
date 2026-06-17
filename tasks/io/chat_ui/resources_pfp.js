// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

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
      let data = await rxjs.firstValueFrom(action$('pfp_inspect', { path: ref, scope, conversation_id: conversationId, sha256: sha }));
      if (data && data.requires_confirmation) {
        const message = data.message || 'Confirm package download to continue.';
        if (!confirm(message)) {
          review.innerHTML = '<div style="color:var(--pf-warning);font-size:12px;">' + escapeHtml(message) + '</div>';
          return;
        }
        data = await rxjs.firstValueFrom(action$('pfp_inspect', {
          path: ref,
          scope,
          conversation_id: conversationId,
          sha256: sha,
          confirm_download: true,
        }));
      }
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
      confirm_download: true,
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

