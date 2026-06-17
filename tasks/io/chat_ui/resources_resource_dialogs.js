// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

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
  skill:    [['description','text'],['instructions','textarea'],['allowed-tools','text'],['license','text'],['metadata','json'],['package_files','skill_assets']],
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
    if (rtype === 'skill') {
      html += '<div style="color:var(--pf-muted);font-size:10px;margin:-4px 0 8px;">Lowercase letters, digits and single hyphens; max 64 characters; must not contain "anthropic" or "claude".</div>';
    }
    if (rtype !== '_tool') {
      html += '<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">' + t('scope') + '</label><select id="res-scope" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin-top:2px;">'
        + _resourceScopeOptions() + '</select></div>';
    }
  }
  if (rtype === 'skill' && !isNew && data && data._invalid) {
    // B9: a malformed skill must surface its failure, not look editable-as-usual.
    html += '<div style="background:color-mix(in srgb, var(--pf-danger,#e05260) 14%, var(--pf-panel));border:1px solid var(--pf-danger,#e05260);color:var(--pf-text);border-radius:4px;padding:8px;margin-bottom:8px;font-size:11px;">'
      + escapeHtml('⚠ This skill is invalid: ' + data._invalid + ' — re-enter description and instructions below to repair it.')
      + '</div>';
  }
  for (const [key, type] of fields) {
    let val = (data && data[key] != null) ? data[key] : '';
    if (key === 'allowed-tools' && Array.isArray(val)) {
      // allowed-tools is a YAML list on disk but edited as a comma-separated
      // text field — render it joined, never JSON-stringified.
      val = val.join(', ');
    } else if (typeof val === 'object') {
      val = JSON.stringify(val, null, 2);
    }
    const escaped = typeof val === 'string' ? val.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') : val;
    const _fieldLabel = (type === 'skill_assets')
      ? 'bundled assets (scripts/, references/, assets/...)' : key;
    html += `<div style="margin-bottom:8px;"><label style="color:var(--pf-muted);font-size:11px;">${escapeHtml(_fieldLabel)}</label>`;
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
    } else if (type === 'skill_assets') {
      // Upload bundled skill files (scripts/, references/, binary assets/).
      // Each staged file carries an editable skill-relative path.
      html += `<div id="res-${key}" data-type="skill_assets" style="margin-top:2px;background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;padding:6px;${roS}">`;
      html += '<div class="skill-asset-list" style="font-size:11px;color:var(--pf-muted);"></div>';
      if (!readonly) {
        html += '<input type="file" multiple class="skill-asset-input" onchange="_onSkillAssetInput(this)" style="margin-top:6px;font-size:11px;color:var(--pf-text);"/>';
        html += '<div style="color:var(--pf-muted);font-size:10px;margin-top:4px;">Set a skill-relative path per file (e.g. scripts/run.py). Editing a skill: existing assets are kept unless you upload replacements.</div>';
      }
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
  action$('list_skills', _convScope()).subscribe(data => {
    const skills = data.skills || [];
    if (!skills.length) {
      container.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + t('noSkillsDefined') + '</div>';
      return;
    }
    container.innerHTML = skills.map(s => {
      const checked = selected.indexOf(s.name) >= 0 ? ' checked' : '';
      // Invalid skills cannot be (un)assigned - disable but keep an existing
      // selection so saving the form does not silently drop it.
      const locked = readonly || !!s.invalid;
      const cbDis = locked ? ' disabled' : '';
      const color = s.invalid ? 'var(--pf-danger,#e05260)' : 'var(--pf-text)';
      const invMark = s.invalid ? ' <span style="color:var(--pf-danger,#e05260);font-size:10px;" title="' + escapeHtml(s.invalid) + '">⚠</span>' : '';
      return '<label style="display:flex;align-items:center;gap:6px;padding:2px 0;cursor:' + (locked ? 'default' : 'pointer') + ';font-size:12px;color:' + color + ';">'
        + '<input type="checkbox" class="skill-cb" value="' + escapeHtml(s.name) + '"' + checked + cbDis + ' style="accent-color:var(--pf-accent);"/>'
        + escapeHtml(s.name) + invMark
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

// ── Skill bundled-asset upload widget ─────────────────────────────
// Staged files live on the widget element as widget._assets:
//   [{ path: 'scripts/run.py', b64: '<base64>' }, ...]
// Files are base64-encoded so binary assets (images under assets/)
// survive the JSON transport to create_resource/update_resource.
function _renderSkillAssets(widget) {
  if (!widget) return;
  var list = widget.querySelector('.skill-asset-list');
  if (!list) return;
  var assets = widget._assets || [];
  if (!assets.length) {
    list.innerHTML = '<span style="color:var(--pf-muted);">No files staged.</span>';
    return;
  }
  list.innerHTML = assets.map(function(a, i) {
    return '<div style="display:flex;gap:4px;align-items:center;margin-bottom:3px;">'
      + '<input class="skill-asset-path" data-idx="' + i + '" value="' + escapeHtml(a.path) + '" '
      + 'style="flex:1;background:var(--pf-code-bg);color:var(--pf-text);border:1px solid var(--pf-border);padding:3px;border-radius:3px;font-size:11px;"/>'
      + '<span style="color:var(--pf-muted);font-size:10px;">' + a.size + ' B</span>'
      + '<button data-idx="' + i + '" class="skill-asset-rm" '
      + 'style="background:none;border:none;color:var(--pf-danger);cursor:pointer;font-size:14px;">×</button>'
      + '</div>';
  }).join('');
  list.querySelectorAll('.skill-asset-rm').forEach(function(btn) {
    btn.onclick = function() {
      widget._assets.splice(parseInt(btn.dataset.idx, 10), 1);
      _renderSkillAssets(widget);
    };
  });
  list.querySelectorAll('.skill-asset-path').forEach(function(inp) {
    inp.onchange = function() {
      widget._assets[parseInt(inp.dataset.idx, 10)].path = inp.value.trim();
    };
  });
}

function _onSkillAssetInput(inputEl) {
  var widget = inputEl.closest('[data-type="skill_assets"]');
  if (!widget) return;
  if (!widget._assets) widget._assets = [];
  var files = Array.from(inputEl.files || []);
  var pending = files.length;
  if (!pending) return;
  files.forEach(function(file) {
    var reader = new FileReader();
    reader.onload = function() {
      var b64 = String(reader.result || '').split(',').pop();
      widget._assets.push({ path: file.name, b64: b64, size: file.size });
      if (--pending === 0) { inputEl.value = ''; _renderSkillAssets(widget); }
    };
    reader.onerror = function() {
      if (--pending === 0) { inputEl.value = ''; _renderSkillAssets(widget); }
    };
    reader.readAsDataURL(file);
  });
}

function _collectSkillAssets(key) {
  var widget = document.getElementById('res-' + key);
  if (!widget || widget.getAttribute('data-type') !== 'skill_assets') return undefined;
  var assets = widget._assets || [];
  var out = {};
  assets.forEach(function(a) { if (a.path) out[a.path] = a.b64; });
  return Object.keys(out).length ? out : undefined;
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
    <button onclick="_saveResourceEdit(${_pfpJsArg(rtype)},${_pfpJsArg(name)},${_pfpJsArg(scope)})" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">${escapeHtml(t('contextSave'))}</button>
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
  var saWidget = panel.querySelector('[data-type="skill_assets"]');
  if (saWidget) _renderSkillAssets(saWidget);
}

// Confirmation dialog for a blocked/flagged skill review. The user always
// has the final word: the findings are shown and `onForce` reruns the
// write with force=true.
function _showSkillReviewConfirm(review, message, onForce) {
  review = review || {};
  var findings = Array.isArray(review.findings) ? review.findings : [];
  var existing = document.getElementById('reviewConfirmOverlay');
  if (existing) existing.remove();
  var overlay = document.createElement('div');
  overlay.id = 'reviewConfirmOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:10001;';
  var panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:540px;max-height:80vh;overflow-y:auto;border:1px solid var(--pf-border);';
  var html = '<h3 style="margin:0 0 10px;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('skillReviewTitle')) + '</h3>';
  html += '<div style="color:var(--pf-muted);font-size:12px;margin-bottom:8px;">' + escapeHtml(String(message || '')) + '</div>';
  html += '<div style="color:var(--pf-text);font-size:11px;margin-bottom:6px;">' + escapeHtml(t('skillReviewRisk', { risk: String(review.risk || 'unknown') })) + '</div>';
  if (findings.length) {
    html += '<div style="background:var(--pf-sidebar);border:1px solid var(--pf-border);border-radius:4px;padding:8px;margin-bottom:10px;">';
    findings.forEach(function(f) {
      f = f || {};
      html += '<div style="margin-bottom:6px;font-size:11px;">'
        + '<span style="color:var(--pf-danger,#e05260);font-weight:600;">[' + escapeHtml(String(f.severity || '')) + '] ' + escapeHtml(String(f.category || '')) + '</span><br/>'
        + '<span style="color:var(--pf-text);">' + escapeHtml(String(f.reason || '')) + '</span>'
        + (f.evidence ? '<br/><code style="color:var(--pf-muted);font-size:10px;word-break:break-all;">' + escapeHtml(String(f.evidence)) + '</code>' : '')
        + '</div>';
    });
    html += '</div>';
  }
  html += '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:10px;">' + escapeHtml(t('skillReviewFinalWord')) + '</div>';
  html += '<div style="display:flex;gap:8px;justify-content:flex-end;">'
    + '<button id="reviewCancelBtn" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
    + '<button id="reviewForceBtn" style="background:var(--pf-danger,#e05260);color:#fff;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('skillReviewProceedAnyway')) + '</button>'
    + '</div>';
  panel.innerHTML = html;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  panel.querySelector('#reviewCancelBtn').onclick = function() { overlay.remove(); };
  panel.querySelector('#reviewForceBtn').onclick = function() {
    overlay.remove();
    if (typeof onForce === 'function') onForce();
  };
}

function _saveResourceEdit(rtype, name, scope) {
  const fields = _RESOURCE_FIELDS[rtype] || [];
  const data = {};
  for (const [key, type] of fields) {
    if (type === 'skills_picker') { data[key] = _collectSkillsPicker(key) || []; continue; }
    if (type === 'params_editor') { const p = _collectParams(key); if (p) data[key] = p; continue; }
    if (type === 'skill_assets') { const pf = _collectSkillAssets(key); if (pf) data[key] = pf; continue; }
    const el = document.getElementById('res-' + key);
    if (el) {
      if (type === 'number') data[key] = parseInt(el.value) || 0;
      else if (type === 'checkbox') data[key] = !!el.checked;
      else if (type === 'json') {
        try { data[key] = el.value.trim() ? JSON.parse(el.value) : (key === 'args' ? [] : {}); }
        catch(e) { alert(t('fieldMustBeValidJson', { field: key })); return; }
      } else if (key === 'allowed-tools') {
        data[key] = el.value.split(/[,\s]+/).filter(Boolean);
      } else data[key] = el.value;
    }
  }
  const payload = { resource_type: rtype, name, scope, data };
  if (scope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
  function _submit(force) {
    const p = force ? Object.assign({}, payload, { force: true }) : payload;
    action$('update_resource', p).subscribe(d => {
      // The user has the final word: a blocked review comes back as
      // requires_confirmation, not an error — show the findings and let
      // the user rerun with force.
      if (d && d.requires_confirmation) {
        _showSkillReviewConfirm(d.review, d.message, function() { _submit(true); });
        return;
      }
      if (d.error) addMsg('error', d.error);
      else { addMsg('system', t('resourceUpdated', { type: rtype, name: name })); document.getElementById('resourceEditorOverlay').remove(); loadResources(); }
    });
  }
  _submit(false);
}

function showSkillAddDialog() {
  let overlay = document.getElementById('resourceEditorOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border-radius:8px;padding:20px;width:720px;max-width:calc(100vw - 32px);max-height:88vh;overflow-y:auto;border:1px solid var(--pf-border);';
  const tabStyle = 'border:1px solid var(--pf-border);border-radius:4px;padding:6px 10px;cursor:pointer;font-size:12px;';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;color:var(--pf-text);font-size:14px;">' + escapeHtml(t('skillAddTitle')) + '</h3>'
    + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button>'
    + '</div>'
    + '<div style="display:flex;gap:6px;margin-bottom:12px;">'
    + '<button id="skill-create-tab" style="' + tabStyle + '">' + escapeHtml(t('skillCreateMode')) + '</button>'
    + '<button id="skill-import-tab" style="' + tabStyle + '">' + escapeHtml(t('skillImportMode')) + '</button>'
    + '</div>'
    + '<div id="skill-add-body"></div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);

  const body = panel.querySelector('#skill-add-body');
  const createTab = panel.querySelector('#skill-create-tab');
  const importTab = panel.querySelector('#skill-import-tab');
  function setMode(mode) {
    createTab.style.background = mode === 'create' ? 'var(--pf-accent)' : 'var(--pf-border)';
    createTab.style.color = mode === 'create' ? 'var(--pf-bg)' : 'var(--pf-text)';
    importTab.style.background = mode === 'import' ? 'var(--pf-accent)' : 'var(--pf-border)';
    importTab.style.color = mode === 'import' ? 'var(--pf-bg)' : 'var(--pf-text)';
    if (mode === 'create') {
      body.innerHTML = _buildResourceForm('skill', {}, true)
        + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">'
        + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
        + '<button onclick="_saveResourceCreate(\'skill\', true)" style="background:color-mix(in srgb, var(--pf-accent) 16%, var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('create')) + ' + ' + escapeHtml(t('assign')) + '</button>'
        + '<button onclick="_saveResourceCreate(\'skill\')" style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('create')) + '</button>'
        + '</div>';
      var saWidget = body.querySelector('[data-type="skill_assets"]');
      if (saWidget) _renderSkillAssets(saWidget);
      return;
    }
    body.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:10px;">' + escapeHtml(t('skillImportSourceHelp')) + '</div>'
      + '<div style="display:grid;grid-template-columns:1fr auto;gap:8px;margin-bottom:8px;">'
      + '<input id="skill-import-ref" placeholder="' + _pfpAttr(t('skillImportRepoPlaceholder')) + '" style="' + _svcInputStyle + '"/>'
      + '<button id="skill-import-resolve" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('skillImportResolve')) + '</button>'
      + '</div>'
      + '<div style="display:grid;grid-template-columns:1fr 1fr 130px;gap:8px;margin-bottom:8px;">'
      + '<select id="skill-import-selected-ref" style="' + _svcInputStyle + '"></select>'
      + '<input id="skill-import-root-path" placeholder="' + _pfpAttr(t('skillImportPathPlaceholder')) + '" style="' + _svcInputStyle + '"/>'
      + '<select id="skill-import-scope" style="' + _svcInputStyle + '">' + _resourceScopeOptions() + '</select>'
      + '</div>'
      + '<div id="skill-import-paths" style="border:1px solid var(--pf-border);border-radius:4px;padding:8px;min-height:70px;margin-bottom:8px;color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('skillImportResolveFirst')) + '</div>'
      + '<div id="skill-import-review" style="border-top:1px solid var(--pf-border);padding-top:10px;color:var(--pf-text);margin-bottom:10px;"></div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;">'
      + '<button onclick="document.getElementById(\'resourceEditorOverlay\').remove()" style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      + '<button id="skill-import-review-btn" disabled style="background:var(--pf-border);color:var(--pf-text);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;opacity:0.6;">' + escapeHtml(t('skillImportReview')) + '</button>'
      + '<button id="skill-import-btn" disabled style="background:var(--pf-accent);color:var(--pf-bg);border:none;padding:8px 16px;border-radius:4px;cursor:pointer;opacity:0.6;">' + escapeHtml(t('import')) + '</button>'
      + '</div>';
    wireImportMode();
  }

  function selectedImportRef() {
    const checked = body.querySelector('input[name="skill-import-path"]:checked');
    return checked ? checked.value : '';
  }

  function renderResolved(data) {
    const refSelect = body.querySelector('#skill-import-selected-ref');
    const refs = (data && data.refs) || {};
    const options = [];
    (refs.branches || []).forEach(v => options.push([v, t('skillImportBranch')]));
    (refs.tags || []).forEach(v => options.push([v, t('skillImportTag')]));
    if (!options.some(row => row[0] === data.selected_ref)) options.unshift([data.selected_ref || '', t('skillImportSelectedRef')]);
    refSelect.innerHTML = options.map(row => '<option value="' + _pfpAttr(row[0]) + '"' + (row[0] === data.selected_ref ? ' selected' : '') + '>' + escapeHtml(row[0] + (row[1] ? ' · ' + row[1] : '')) + '</option>').join('');
    const rows = data.paths || [];
    const pathBox = body.querySelector('#skill-import-paths');
    if (!rows.length) {
      pathBox.innerHTML = '<div style="color:var(--pf-warning);font-size:11px;">' + escapeHtml(t('skillImportNoPaths')) + '</div>';
    } else {
      pathBox.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;">' + escapeHtml(t('skillImportFoundPaths', { n: rows.length })) + '</div>'
        + rows.map((row, idx) => '<label style="display:flex;align-items:center;gap:6px;margin-bottom:4px;cursor:pointer;color:var(--pf-text);font-size:12px;">'
          + '<input type="radio" name="skill-import-path" value="' + _pfpAttr(row.import_ref || row.url || '') + '"' + (idx === 0 ? ' checked' : '') + '/>'
          + '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(row.path || '/') + '</span>'
          + '<code style="color:var(--pf-muted);font-size:10px;">' + escapeHtml(row.ref || '') + '</code>'
          + '</label>').join('');
    }
    setImportButtons(!!rows.length);
  }

  function setImportButtons(enabled) {
    ['#skill-import-review-btn', '#skill-import-btn'].forEach(sel => {
      const btn = body.querySelector(sel);
      btn.disabled = !enabled;
      btn.style.opacity = enabled ? '1' : '0.6';
    });
  }

  function renderReview(data) {
    const review = body.querySelector('#skill-import-review');
    if (!data) { review.innerHTML = ''; return; }
    if (data.error) { review.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;">' + escapeHtml(data.error) + '</div>'; return; }
    const skill = data.skill || {};
    const pkg = data.package || {};
    const rv = data.review || {};
    const riskColor = rv.risk === 'high' || data.blocked ? 'var(--pf-danger)' : rv.risk === 'medium' ? 'var(--pf-warning)' : 'var(--pf-muted)';
    review.innerHTML = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">'
      + '<div style="font-size:13px;color:var(--pf-text);font-weight:600;flex:1;">' + escapeHtml(skill.name || pkg.skill_name || '') + '</div>'
      + '<span style="font-size:10px;color:' + riskColor + ';border:1px solid ' + riskColor + ';border-radius:3px;padding:1px 5px;">' + escapeHtml(rv.risk || 'unknown') + '</span>'
      + '</div>'
      + '<div style="color:var(--pf-muted);font-size:11px;margin-bottom:6px;">' + escapeHtml(skill.description || '') + '</div>'
      + '<div style="color:var(--pf-muted);font-size:10px;word-break:break-all;">' + escapeHtml(pkg.url || selectedImportRef()) + '</div>'
      + (data.message ? '<div style="color:' + (data.blocked || data.requires_human_review ? 'var(--pf-warning)' : 'var(--pf-muted)') + ';font-size:11px;margin-top:8px;white-space:pre-wrap;">' + escapeHtml(data.message) + '</div>' : '');
  }

  function wireImportMode() {
    const resolveBtn = body.querySelector('#skill-import-resolve');
    const refInput = body.querySelector('#skill-import-ref');
    const selectedRef = body.querySelector('#skill-import-selected-ref');
    const rootPath = body.querySelector('#skill-import-root-path');
    const reviewBtn = body.querySelector('#skill-import-review-btn');
    const importBtn = body.querySelector('#skill-import-btn');
    async function resolve() {
      const ref = (refInput.value || '').trim();
      if (!ref) { alert(t('skillImportRepoRequired')); return; }
      resolveBtn.disabled = true;
      resolveBtn.textContent = t('loading');
      body.querySelector('#skill-import-paths').innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('loading')) + '</div>';
      renderReview(null);
      setImportButtons(false);
      try {
        const data = await rxjs.firstValueFrom(action$('resolve_skill_import_source', {
          ref,
          selected_ref: (selectedRef.value || '').trim(),
          path: (rootPath.value || '').trim(),
          limit: 40,
        }));
        if (data.error) { body.querySelector('#skill-import-paths').innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(data.error) + '</div>'; return; }
        renderResolved(data);
      } catch (e) {
        body.querySelector('#skill-import-paths').innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">' + escapeHtml(e.message) + '</div>';
      } finally {
        resolveBtn.disabled = false;
        resolveBtn.textContent = t('skillImportResolve');
      }
    }
    async function importSkill(reviewOnly, force) {
      const importRef = selectedImportRef();
      if (!importRef) { alert(t('skillImportSelectedPathRequired')); return; }
      const btn = reviewOnly ? reviewBtn : importBtn;
      btn.disabled = true;
      btn.textContent = reviewOnly ? t('loading') : t('importing');
      try {
        const payload = {
          source: 'github',
          ref: importRef,
          review_only: !!reviewOnly,
          force: !!force,
          scope: body.querySelector('#skill-import-scope').value,
          conversation_id: conversationId,
        };
        const data = await rxjs.firstValueFrom(action$('import_skill_marketplace', payload));
        renderReview(data);
        if (data && data.requires_confirmation) {
          _showSkillReviewConfirm(data.review, data.message, function() { importSkill(false, true); });
          return;
        }
        if (data.error) { addMsg('error', data.error); return; }
        if (data.imported) {
          addMsg('system', data.message || t('skillImportImported', { name: data.name || '' }));
          overlay.remove();
          loadResources();
        }
      } catch (e) {
        renderReview({ error: e.message });
      } finally {
        btn.disabled = false;
        btn.textContent = reviewOnly ? t('skillImportReview') : t('import');
      }
    }
    resolveBtn.addEventListener('click', resolve);
    refInput.addEventListener('keydown', event => { if (event.key === 'Enter') resolve(); });
    selectedRef.addEventListener('change', resolve);
    rootPath.addEventListener('keydown', event => { if (event.key === 'Enter') resolve(); });
    reviewBtn.addEventListener('click', () => importSkill(true, false));
    importBtn.addEventListener('click', () => importSkill(false, false));
  }

  createTab.addEventListener('click', () => setMode('create'));
  importTab.addEventListener('click', () => setMode('import'));
  setMode('create');
}

