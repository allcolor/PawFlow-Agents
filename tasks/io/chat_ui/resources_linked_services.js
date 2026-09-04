/* Optional bindings for PawFlow automatic service roles. */

var _linkedServiceState = { roles: [] };

function _linkedServiceRoleLabel(role) {
  var keys = {
    summary_compaction: 'linkedRoleSummary',
    project_wiki: 'linkedRoleWiki',
    auto_memory: 'linkedRoleMemory',
    memory_embeddings: 'linkedRoleEmbeddings',
    attachment_ocr: 'linkedRoleOcr',
    skill_learning: 'linkedRoleSkills',
    conversation_title: 'linkedRoleTitles',
    content_review: 'linkedRoleReview',
  };
  return t(keys[role] || role);
}

function _linkedServiceDefaultLabel(role) {
  var keys = {
    summary_compaction: 'linkedDefaultSummary',
    project_wiki: 'linkedDefaultWiki',
    auto_memory: 'linkedDefaultMemory',
    memory_embeddings: 'linkedDefaultEmbeddings',
    attachment_ocr: 'linkedDefaultOcr',
    skill_learning: 'linkedDefaultSkills',
    conversation_title: 'linkedDefaultTitles',
    content_review: 'linkedDefaultReview',
  };
  return t(keys[role] || 'linkedServicesPawFlowDefault');
}

function _linkedTargetLabel(target) {
  if ((target.kind || '') === 'agent') {
    return t('agent') + ' · ' + (target.instance_name || '');
  }
  var detail = target.llm_service ? ' → ' + target.llm_service : '';
  return '[' + (target.scope || 'global') + '] '
    + (target.service_id || '') + detail;
}

function _linkedTargetOptions(role) {
  var state = (_linkedServiceState.roles || []).find(function(item) {
    return item.role === role;
  }) || {};
  return state.available || [];
}

function _refreshLinkedTargetSelect() {
  var roleSelect = document.getElementById('_linkedRoleSelect');
  var targetSelect = document.getElementById('_linkedTargetSelect');
  if (!roleSelect || !targetSelect) return;
  var targets = _linkedTargetOptions(roleSelect.value);
  targetSelect.innerHTML = targets.map(function(target, index) {
    return '<option value="' + index + '">'
      + escapeHtml(_linkedTargetLabel(target)) + '</option>';
  }).join('');
  var empty = document.getElementById('_linkedTargetEmpty');
  if (empty) empty.style.display = targets.length ? 'none' : 'block';
  var apply = document.getElementById('_linkedApply');
  if (apply) apply.disabled = !targets.length;
}

function _showLinkedServiceDialog(initialRole) {
  action$('linked_services_list', {
    conversation_id: conversationId,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    _linkedServiceState = data || { roles: [] };
    var roles = data.roles || [];
    var options = roles.map(function(item) {
      var selected = item.role === initialRole ? ' selected' : '';
      return '<option value="' + escapeHtml(item.role) + '"' + selected + '>'
        + escapeHtml(_linkedServiceRoleLabel(item.role)) + '</option>';
    }).join('');
    var overlay = document.createElement('div');
    overlay.className = 'exec-overlay';
    overlay.innerHTML = '<div class="exec-dialog" style="min-width:390px;">'
      + '<h3>' + escapeHtml(t('linkedServicesLink')) + '</h3>'
      + '<label style="display:block;margin:10px 0 4px;color:var(--pf-muted);">'
      + escapeHtml(t('linkedServicesRole')) + '</label>'
      + '<select id="_linkedRoleSelect" onchange="_refreshLinkedTargetSelect()" '
      + 'style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;">'
      + options + '</select>'
      + '<label style="display:block;margin:10px 0 4px;color:var(--pf-muted);">'
      + escapeHtml(t('linkedServicesTarget')) + '</label>'
      + '<select id="_linkedTargetSelect" '
      + 'style="width:100%;padding:8px;background:var(--pf-panel);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;"></select>'
      + '<div id="_linkedTargetEmpty" style="display:none;margin-top:8px;color:var(--pf-muted);font-size:11px;">'
      + escapeHtml(t('linkedServicesNoTarget')) + '</div>'
      + '<div class="exec-btns">'
      + '<button class="exec-deny" onclick="this.closest(\'.exec-overlay\').remove()">'
      + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="_linkedApply" class="exec-approve" onclick="_doLinkedServiceLink(this)">'
      + escapeHtml(t('link')) + '</button>'
      + '</div></div>';
    document.body.appendChild(overlay);
    _refreshLinkedTargetSelect();
  });
}

function _doLinkedServiceLink(button) {
  var overlay = button.closest('.exec-overlay');
  var roleSelect = overlay.querySelector('#_linkedRoleSelect');
  var targetSelect = overlay.querySelector('#_linkedTargetSelect');
  var role = roleSelect ? roleSelect.value : '';
  var targets = _linkedTargetOptions(role);
  var target = targets[targetSelect ? Number(targetSelect.value) : -1];
  if (!role || !target) return;
  var payload = {
    conversation_id: conversationId,
    role: role,
    kind: target.kind,
  };
  if (target.kind === 'agent') {
    payload.instance_name = target.instance_name;
  } else {
    payload.scope = target.scope;
    payload.service_id = target.service_id;
  }
  action$('linked_service_link', payload).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    overlay.remove();
    loadResources();
  });
}

function _unlinkLinkedService(role) {
  action$('linked_service_unlink', {
    conversation_id: conversationId,
    role: role,
  }).subscribe(function(data) {
    if (data.error) { addMsg('error', data.error); return; }
    loadResources();
  });
}

function _renderLinkedServicesSection(data) {
  var html = _sectionHeader(t('linkedServices'), '_linked_services', {
    createOnclick: '_showLinkedServiceDialog()',
    createTitle: t('linkedServicesLink'),
    hideRefresh: true,
  });
  var roles = ((data || {}).linked_services || {}).roles || [];
  roles.forEach(function(item) {
    var binding = item.binding || {};
    var explicit = !!item.explicit;
    var broken = !!item.broken;
    var color = broken ? 'var(--pf-danger)'
      : explicit ? 'var(--pf-success)' : 'var(--pf-muted)';
    var value = _linkedServiceDefaultLabel(item.role);
    if (explicit && binding.kind === 'agent') {
      value = t('agent') + ' · ' + (binding.instance_name || '');
    } else if (explicit) {
      value = binding.service_id || '';
    }
    html += '<div' + _resourceRowAttr('linked-service', item.role) + ' style="margin-left:8px;margin-bottom:6px;">'
      + '<div style="display:flex;align-items:center;gap:4px;">'
      + '<span style="font-size:11px;color:var(--pf-text);flex:1;">'
      + escapeHtml(_linkedServiceRoleLabel(item.role)) + '</span>'
      + '<span style="font-size:9px;color:' + color + ';background:color-mix(in srgb, '
      + color + ' 14%, var(--pf-panel));padding:1px 4px;border-radius:3px;">'
      + escapeHtml(broken ? t('linkedServicesBroken') : explicit
        ? t('linkedServicesOverride') : t('linkedServicesPawFlowDefault')) + '</span>'
      + '<span style="cursor:pointer;font-size:11px;color:var(--pf-accent);padding:0 3px;" '
      + 'onclick="_showLinkedServiceDialog(' + JSON.stringify(item.role).replace(/"/g, '&quot;') + ')" '
      + 'title="' + escapeHtml(t('linkedServicesLink')) + '">+</span>'
      + (explicit ? '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" '
        + 'onclick="_unlinkLinkedService(' + JSON.stringify(item.role).replace(/"/g, '&quot;') + ')" '
        + 'title="' + escapeHtml(t('linkedServicesReset')) + '">&times;</span>' : '')
      + '</div><div style="font-size:10px;color:' + color + ';">'
      + escapeHtml(value) + '</div>'
      + (broken ? '<div style="font-size:9px;color:var(--pf-danger);">'
        + escapeHtml(item.error || '') + ' · '
        + escapeHtml(t('linkedServicesFallback')) + '</div>' : '')
      + '</div>';
  });
  if (!roles.length) {
    html += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">'
      + escapeHtml(t('linkedServicesPawFlowDefault')) + '</div>';
  }
  return html + _sectionFooter();
}
