// \xe2\x94\x80\xe2\x94\x80 Conversation delete / export / import \xe2\x94\x80\xe2\x94\x80
// Split out of conversations.js (<=800 lines). All globals; the list/
// state/render core (escapeHtml etc.) loads before this file.

function deleteConv(event, cid) {
  event.stopPropagation();
  deleteConversationById(cid);
}

function deleteCurrentConv() {
  deleteConversationById(conversationId);
}

function deleteConversationById(cid) {
  if (!cid) return;
  if (!confirm(t('confirmDelete'))) return;
  var wasActive = (cid === conversationId);
  action$('delete_conversation', { conversation_id: cid }).subscribe(() => {
    if (wasActive) _switchAfterDelete(cid);
    else loadConversations();
  });
}

// After deleting the current conv, fetch the fresh list from the server
// and resume the next one (single source of truth — no stale DOM reads,
// no duplicated switch logic). Falls back to the new-chat empty state
// when no conv remains.
function _switchAfterDelete(deletedCid) {
  action$('list_conversations', {}).subscribe(data => {
    var convs = (data && data.conversations) || [];
    renderConvList(convs);
    // Pick neighbor of deleted in the sidebar order (already sorted
    // updated_at DESC by the backend).
    var idx = -1;
    for (var i = 0; i < convs.length; i++) {
      if (convs[i].conversation_id === deletedCid) { idx = i; break; }
    }
    // Deleted is already gone from the fresh list — pick the entry that
    // occupied the slot (same index, or last if out of range).
    var next = null;
    if (convs.length) {
      next = convs[idx >= 0 && idx < convs.length ? idx : convs.length - 1];
      if (!next && convs.length) next = convs[0];
    }
    if (next) {
      resumeConv(next.conversation_id, true);
    } else {
      renderEmptyState();
    }
  });
}

function exportConversation(cid) {
  const targetCid = cid || conversationId;
  if (!targetCid) return;
  document.getElementById('status').textContent = t('exporting');
  // Export needs the full history — subscribe to load_history result
  action$('load_history', { conversation_id: targetCid, limit: 99999, offset: 0 })
    .subscribe(async data => {
      try {
        const messages = data.messages || [];
        const fileUrls = [];
        const fileUrlRe = /(https?:\/\/[^\s<"']*\/files\/[a-f0-9]+\/([^\s<"')]+))/g;
        for (const m of messages) {
          const content = m.content || '';
          let match;
          while ((match = fileUrlRe.exec(content)) !== null) fileUrls.push({ url: match[1], name: match[2] });
          fileUrlRe.lastIndex = 0;
        }
        const hasImages = fileUrls.some(f => isImageFile(f.name));
        const htmlContent = buildExportHtml(messages, data.nicknames || {}, fileUrls);
        if (hasImages) {
          addMsg('system', t('exportingWithImages'));
          const files = [{ name: 'conversation.html', content: new TextEncoder().encode(htmlContent) }];
          const token = getToken();
          const headers = {};
          if (token) headers['Authorization'] = 'Bearer ' + token;
          for (const f of fileUrls) {
            if (isImageFile(f.name)) {
              try {
                const imgResp = await fetch(f.url, { headers, credentials: 'same-origin' });
                if (imgResp.ok) {
                  const blob = await imgResp.blob();
                  const buf = await blob.arrayBuffer();
                  files.push({ name: 'images/' + f.name, content: new Uint8Array(buf) });
                }
              } catch(e) { console.warn('Failed to fetch image for export:', f.name); }
            }
          }
          const zipBlob = buildSimpleZip(files);
          const a = document.createElement('a');
          a.href = URL.createObjectURL(zipBlob);
          a.download = 'conversation_' + targetCid.substring(0, 8) + '.zip';
          a.click();
          URL.revokeObjectURL(a.href);
        } else {
          const blob = new Blob([htmlContent], { type: 'text/html;charset=utf-8' });
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'conversation_' + targetCid.substring(0, 8) + '.html';
          a.click();
          URL.revokeObjectURL(a.href);
        }
        addMsg('system', t('exported'));
      } catch (e) {
        addMsg('error', t('exportFailed', { error: e.message }));
      }
      document.getElementById('status').textContent = t('ready');
    });
}

function _showImportProgress(label) {
  var overlay = document.createElement('div');
  overlay.id = 'importProgressOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;';
  var box = document.createElement('div');
  box.style.cssText = 'background:#1a1a2e;border:1px solid #6c5ce7;border-radius:10px;padding:24px 32px;display:flex;align-items:center;gap:14px;color:#e0e0e0;font-size:14px;min-width:260px;box-shadow:0 10px 40px rgba(0,0,0,0.5);';
  box.innerHTML = '<span class="spinner" style="color:#6c5ce7;font-size:22px;animation:spin 1.2s linear infinite;display:inline-block;">\u273B</span><span id="importProgressLabel">' + label + '</span>';
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  return {
    setLabel: function(s) { var el = document.getElementById('importProgressLabel'); if (el) el.textContent = s; },
    close: function() { var el = document.getElementById('importProgressOverlay'); if (el) el.remove(); },
  };
}

function importConversation() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip,.jsonl';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const ext = file.name.split('.').pop().toLowerCase();
    const fmt = ext === 'zip' ? 'pawflow' : ext === 'jsonl' ? 'claude_code' : null;
    if (!fmt) { addMsg('error', t('unsupportedImportFormat')); return; }
    var progress = _showImportProgress(t('uploadingFile', { file: file.name }));
    document.getElementById('status').textContent = t('uploading');
    try {
      const info = await uploadFileToStore(file);
      progress.setLabel(t('analyzingConversation'));
      document.getElementById('status').textContent = t('analyzing');
      action$('conv_import_analyze', { file_id: info.file_id, format: fmt }).subscribe(result => {
        progress.close();
        document.getElementById('status').textContent = t('ready');
        if (result.error) { addMsg('error', t('importFailed', { error: result.error })); return; }
        _showImportConvDialog(result, fmt);
      });
    } catch(e) {
      progress.close();
      document.getElementById('status').textContent = t('ready');
      addMsg('error', t('uploadFailed', { error: e.message }));
    }
  };
  input.click();
}

function _showImportConvDialog(info, fmt) {
  // info: {temp_id, agents: [{name, definition},...], message_count, format}
  Promise.all([
    rxjs.firstValueFrom(action$('list_repo_agents', {})),
    rxjs.firstValueFrom(listServices$('llmConnection')),
    rxjs.firstValueFrom(action$('relay_list_available', {})),
  ]).then(results => {
    var repoAgents = results[0].agents || [];
    var llmServices = (results[1].services || []).filter(s => s.enabled);
    var availableRelays = (results[2].relays || []).filter(r => r.connected);
    var svcOpts = llmServices.map(s =>
      '<option value="' + escapeHtml(s.service_id) + '">' + escapeHtml(s.service_id) + (s.description ? ' \u2014 ' + escapeHtml(s.description) : '') + '</option>'
    ).join('');

    function _guessLlm(name) {
      for (var i = 0; i < llmServices.length; i++) {
        if (llmServices[i].service_id === name + '_llm_service') return name + '_llm_service';
      }
      for (var i = 0; i < llmServices.length; i++) {
        if (llmServices[i].service_id === name + '_llm') return name + '_llm';
      }
      return llmServices.length ? llmServices[0].service_id : '';
    }

    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center';
    var box = document.createElement('div');
    box.style.cssText = 'background:var(--bg2,#1e1e2e);border:1px solid var(--border,#444);border-radius:8px;padding:20px;min-width:640px;max-width:780px;max-height:85vh;display:flex;flex-direction:column;gap:12px;overflow-y:auto;position:relative;';
    box.onclick = e => e.stopPropagation();

    var _listCss = 'width:100%;min-height:100px;max-height:240px;overflow-y:auto;border:1px solid var(--border,#444);border-radius:4px;padding:4px;background:var(--bg,#141420);';
    var _relCss = 'width:100%;min-height:60px;max-height:120px;overflow-y:auto;border:1px solid var(--border,#444);border-radius:4px;padding:4px;background:var(--bg,#141420);';
    var _btnCss = 'padding:4px 10px;border:1px solid var(--border,#444);border-radius:4px;background:var(--bg2,#1e1e2e);color:inherit;cursor:pointer;font-size:16px;font-weight:600;';

    var fileRestoreHtml = '';
    if (fmt === 'pawflow' && (info.filestore_count || 0) > 0) {
      var mb = ((info.filestore_bytes || 0) / (1024 * 1024)).toFixed(1);
      fileRestoreHtml = '<label style="display:flex;gap:8px;align-items:center;font-size:12px;color:#ccc;border:1px solid var(--border,#444);border-radius:4px;padding:8px;background:var(--bg,#141420);">'
        + '<input id="_impRestoreFiles" type="checkbox" checked style="margin:0;">'
        + '<span>Restore attached/generated files (' + escapeHtml(String(info.filestore_count)) + ' files, ' + escapeHtml(mb) + ' MB)</span>'
        + '</label>';
    }

    box.innerHTML =
      '<span id="_impCloseX" style="position:absolute;top:8px;right:12px;cursor:pointer;color:#888;font-size:18px;" title="' + escapeHtml(t('contextCancel')) + '">\u2715</span>'
      + '<div style="font-weight:600;font-size:1.1em;">' + escapeHtml(t('importConversation')) + '</div>'
      + '<div style="color:#888;font-size:11px;">' + escapeHtml(t('contextMessages', { n: info.message_count })) + ' \u2014 ' + escapeHtml(t('formatLabel', { format: fmt })) + '</div>'
      + '<div><label style="font-size:11px;color:#888;">' + escapeHtml(t('title')) + '</label>'
      + '<input id="_impTitle" type="text" value="' + escapeHtml(t('importedConversationTitle')) + '" style="width:100%;padding:6px 10px;border-radius:5px;border:1px solid var(--border,#444);background:var(--bg,#141420);color:inherit;font-size:0.95em;box-sizing:border-box;"></div>'
      + fileRestoreHtml
      + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">' + escapeHtml(t('agentMapping')) + '</div>'
      + '<div style="display:flex;gap:12px;align-items:stretch;">'
      +   '<div id="_impAgentTree" style="' + _listCss + 'flex:1;"></div>'
      +   '<div id="_impAgentDetail" style="flex:1;border:1px solid var(--border,#444);border-radius:4px;padding:10px;background:var(--bg,#141420);min-height:100px;max-height:240px;overflow-y:auto;font-size:12px;color:#aaa;display:flex;align-items:center;justify-content:center;">' + escapeHtml(t('selectAgentConfigure')) + '</div>'
      + '</div>'
      + '<div style="font-size:12px;font-weight:600;color:#6c5ce7;">' + escapeHtml(t('relays')) + '</div>'
      + '<div style="display:flex;gap:8px;align-items:stretch;">'
      +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">' + escapeHtml(t('available')) + '</div><div id="_impRelaysAvail" style="' + _relCss + '"></div></div>'
      +   '<div style="display:flex;flex-direction:column;justify-content:center;gap:4px;">'
      +     '<button id="_impRelayAdd" style="' + _btnCss + '" title="' + escapeHtml(t('link')) + '">\u25B6</button>'
      +     '<button id="_impRelayRem" style="' + _btnCss + '" title="' + escapeHtml(t('unlink')) + '">\u25C0</button>'
      +   '</div>'
      +   '<div style="flex:1;"><div style="font-size:10px;color:#888;margin-bottom:2px;">' + escapeHtml(t('linkedRelaysDefaultHint')) + '</div><div id="_impRelaysSel" style="' + _relCss + '"></div></div>'
      + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:4px;">'
      +   '<button id="_impCancelBtn" style="padding:6px 14px;border-radius:5px;border:1px solid var(--border,#444);background:transparent;color:inherit;cursor:pointer;">' + escapeHtml(t('contextCancel')) + '</button>'
      +   '<button id="_impGoBtn" style="padding:6px 14px;border-radius:5px;border:none;background:var(--accent,#7c6af7);color:#fff;cursor:pointer;font-weight:600;">' + escapeHtml(t('import')) + '</button>'
      + '</div>';

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function _cancelImport() {
      overlay.remove();
      action$('conv_import_cleanup', { temp_id: info.temp_id }).subscribe(() => {});
    }

    var agentInstances = {};
    var focusedAgent = '';

    info.agents.forEach(a => {
      var bestDef = repoAgents.find(r => r.name === a.definition) ? a.definition
                  : repoAgents.find(r => r.name === a.name) ? a.name
                  : repoAgents.length ? repoAgents[0].name : '';
      agentInstances[a.name] = {
        definition: bestDef,
        llm_service: _guessLlm(bestDef || a.name),
        params: { name: a.name },
      };
    });

    function _renderTree() {
      var tree = document.getElementById('_impAgentTree');
      tree.innerHTML = '';
      var hdr = document.createElement('div');
      hdr.style.cssText = 'font-size:10px;color:#666;padding:2px 4px;';
      hdr.textContent = t('importedAgentsCount', { n: Object.keys(agentInstances).length });
      tree.appendChild(hdr);
      Object.keys(agentInstances).forEach(iname => {
        var inst = agentInstances[iname];
        var row = document.createElement('div');
        row.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;cursor:pointer;font-size:12px;'
          + (focusedAgent === iname ? 'background:rgba(124,106,247,0.15);' : '');
        var label = document.createElement('span');
        label.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        label.textContent = iname;
        var badge = document.createElement('span');
        badge.style.cssText = 'font-size:10px;color:#888;';
        badge.textContent = inst.definition ? '\u2192 ' + inst.definition : '\u26A0 ' + t('unmapped');
        row.appendChild(label);
        row.appendChild(badge);
        row.onclick = () => { _commitCurrentDetail(); focusedAgent = iname; _renderTree(); _renderDetail(); };
        tree.appendChild(row);
      });
    }

    // Auto-commit the detail panel into agentInstances. Called on every
    // input change, before switching focused agent, and on Import. This
    // replaces the old explicit "Apply Changes" button — the state you see
    // IS the state that gets imported.
    function _commitCurrentDetail() {
      if (!focusedAgent || !agentInstances[focusedAgent]) return;
      var panel = document.getElementById('_impAgentDetail');
      if (!panel) return;
      var nameEl = document.getElementById('_impInstName');
      var defEl  = document.getElementById('_impDefSelect');
      var llmEl  = document.getElementById('_impLlmSelect');
      if (!nameEl || !defEl || !llmEl) return;
      var newName = (nameEl.value || '').trim() || focusedAgent;
      var params = { name: newName };
      panel.querySelectorAll('[data-param]').forEach(inp => { params[inp.dataset.param] = inp.value; });
      var updated = { definition: defEl.value, llm_service: llmEl.value, params: params };
      if (newName !== focusedAgent) {
        delete agentInstances[focusedAgent];
        focusedAgent = newName;
      }
      agentInstances[focusedAgent] = updated;
    }

    function _renderDetail() {
      var panel = document.getElementById('_impAgentDetail');
      if (!focusedAgent || !agentInstances[focusedAgent]) {
        panel.innerHTML = '<span style="color:#666;">' + escapeHtml(t('selectAgentConfigure')) + '</span>';
        panel.style.display = 'flex'; panel.style.alignItems = 'center'; panel.style.justifyContent = 'center';
        return;
      }
      panel.style.display = 'block'; panel.style.alignItems = ''; panel.style.justifyContent = '';
      var inst = agentInstances[focusedAgent];
      var defAgent = repoAgents.find(a => a.name === inst.definition);
      var paramSchema = defAgent && defAgent.parameters ? defAgent.parameters : {};
      var paramKeys = Object.keys(paramSchema).filter(k => k !== 'name');

      var defOptions = repoAgents.map(a =>
        '<option value="' + escapeHtml(a.name) + '"' + (a.name === inst.definition ? ' selected' : '') + '>'
        + escapeHtml(a.name) + (a.description ? ' \u2014 ' + escapeHtml(a.description) : '') + '</option>'
      ).join('');

      var html = '<div style="font-weight:600;font-size:13px;color:#fff;margin-bottom:8px;">' + escapeHtml(focusedAgent) + '</div>';
      html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:#888;">' + escapeHtml(t('instanceName')) + '</label>';
      html += '<input id="_impInstName" value="' + escapeHtml(focusedAgent) + '" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;box-sizing:border-box;"/></div>';
      html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:#888;">' + escapeHtml(t('definitionRequired')) + '</label>';
      html += '<select id="_impDefSelect" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;">' + defOptions + '</select></div>';
      html += '<div style="margin-bottom:6px;"><label style="font-size:10px;color:#888;">' + escapeHtml(t('llmServiceRequired')) + '</label>';
      html += '<select id="_impLlmSelect" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;">' + svcOpts + '</select></div>';
      if (paramKeys.length) {
        html += '<div style="margin-bottom:6px;"><div style="font-size:10px;color:#888;margin-bottom:4px;">' + escapeHtml(t('parameters')) + '</div>';
        paramKeys.forEach(k => {
          var spec = paramSchema[k] || {};
          var val = inst.params[k] || spec.default || '';
          html += '<div style="margin-bottom:4px;"><label style="font-size:10px;color:#888;">' + escapeHtml(k + (spec.required ? ' *' : '')) + '</label>';
          html += '<input data-param="' + escapeHtml(k) + '" value="' + escapeHtml(String(val)) + '" style="width:100%;padding:4px 6px;border-radius:4px;border:1px solid var(--border,#444);background:var(--bg2,#1e1e2e);color:inherit;font-size:12px;box-sizing:border-box;"/></div>';
        });
        html += '</div>';
      }

      panel.innerHTML = html;
      var llmSel = document.getElementById('_impLlmSelect');
      if (llmSel) llmSel.value = inst.llm_service || _guessLlm(inst.definition);

      // Auto-commit on any change — no Apply button. Def change resets
      // the llm_service guess + re-renders so param schema updates.
      var nameEl = document.getElementById('_impInstName');
      var defSel = document.getElementById('_impDefSelect');
      if (nameEl) {
        nameEl.oninput = () => _commitCurrentDetail();
        nameEl.onblur = () => { _commitCurrentDetail(); _renderTree(); };
      }
      if (defSel) defSel.onchange = () => {
        _commitCurrentDetail();
        agentInstances[focusedAgent].llm_service = _guessLlm(defSel.value);
        _renderTree(); _renderDetail();
      };
      if (llmSel) llmSel.onchange = () => _commitCurrentDetail();
      panel.querySelectorAll('[data-param]').forEach(inp => { inp.oninput = () => _commitCurrentDetail(); });
    }

    // ── Relay selector (same UX as New Conv dialog) ──
    var selRelays = [], defaultRelay = '';
    function _makeRelayItem(text, id) {
      var d = document.createElement('div');
      d.textContent = text; d.dataset.id = id;
      d.style.cssText = 'padding:3px 6px;cursor:pointer;border-radius:3px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
      d.onmouseenter = function() { d.style.background = 'rgba(124,106,247,0.15)'; };
      d.onmouseleave = function() { if (!d.classList.contains('_sel')) d.style.background = ''; };
      d.onclick = function() {
        d.parentNode.querySelectorAll('div').forEach(function(x) { x.classList.remove('_sel'); x.style.background = ''; });
        d.classList.add('_sel'); d.style.background = 'rgba(124,106,247,0.3)';
      };
      return d;
    }
    function _renderRelays() {
      var avail = document.getElementById('_impRelaysAvail');
      var sel = document.getElementById('_impRelaysSel');
      avail.innerHTML = ''; sel.innerHTML = '';
      availableRelays.forEach(function(r) {
        if (selRelays.indexOf(r.relay_id) >= 0) return;
        var label = r.relay_id + (r.host_root ? ' (' + r.host_root + ')' : r.root ? ' (' + r.root + ')' : '');
        avail.appendChild(_makeRelayItem(label, r.relay_id));
      });
      selRelays.forEach(function(rid) {
        var d = _makeRelayItem(rid, rid);
        var isDefault = rid === defaultRelay;
        var radio = document.createElement('span');
        radio.innerHTML = isDefault ? '\u2605' : '\u2606';
        radio.style.cssText = 'cursor:pointer;color:' + (isDefault ? '#4ecdc4' : '#555') + ';margin-right:4px;font-size:14px;';
        radio.title = t('setDefaultRelay');
        radio.onclick = function(e) { e.stopPropagation(); defaultRelay = rid; _renderRelays(); };
        d.insertBefore(radio, d.firstChild);
        sel.appendChild(d);
      });
    }

    _renderTree();
    _renderRelays();
    if (info.agents.length) { focusedAgent = info.agents[0].name; _renderTree(); _renderDetail(); }

    document.getElementById('_impRelayAdd').onclick = function() {
      var s = document.querySelector('#_impRelaysAvail ._sel');
      if (s) { selRelays.push(s.dataset.id); if (selRelays.length === 1) defaultRelay = s.dataset.id; _renderRelays(); }
    };
    document.getElementById('_impRelayRem').onclick = function() {
      var s = document.querySelector('#_impRelaysSel ._sel');
      if (s) { selRelays = selRelays.filter(function(x) { return x !== s.dataset.id; }); if (defaultRelay === s.dataset.id) defaultRelay = selRelays[0] || ''; _renderRelays(); }
    };
    document.getElementById('_impRelaysAvail').ondblclick = function(e) {
      var t = e.target.closest('[data-id]'); if (t) { selRelays.push(t.dataset.id); if (selRelays.length === 1) defaultRelay = t.dataset.id; _renderRelays(); }
    };
    document.getElementById('_impRelaysSel').ondblclick = function(e) {
      var t = e.target.closest('[data-id]'); if (t) { selRelays = selRelays.filter(function(x) { return x !== t.dataset.id; }); if (defaultRelay === t.dataset.id) defaultRelay = selRelays[0] || ''; _renderRelays(); }
    };

    document.getElementById('_impCloseX').onclick = _cancelImport;
    document.getElementById('_impCancelBtn').onclick = _cancelImport;
    document.getElementById('_impGoBtn').onclick = () => {
      _commitCurrentDetail();  // flush visible panel into agentInstances
      if (!Object.keys(agentInstances).length) { alert(t('atLeastOneAgentRequired')); return; }
      var title = (document.getElementById('_impTitle').value || '').trim() || t('imported');
      overlay.remove();
      document.getElementById('status').textContent = t('importing');
      action$('conv_import_execute', {
        temp_id: info.temp_id, format: fmt,
        agent_mapping: agentInstances, title,
        relays: selRelays, default_relay: defaultRelay,
        restore_filestore: !!(document.getElementById('_impRestoreFiles') && document.getElementById('_impRestoreFiles').checked),
        file_id_policy: 'preserve_or_remap',
      }).subscribe(result => {
        if (result.error) { addMsg('error', t('importFailed', { error: result.error })); document.getElementById('status').textContent = t('ready'); return; }
        addMsg('system', t('importSuccess'));
        // Imported conversations should become the active chat immediately.
        // Refresh once now and once after the route switch so VPS-side cache or
        // SSE timing cannot leave the sidebar stale until a hard reload.
        resumeConv(result.conversation_id, true);
        loadConversations();
        setTimeout(function() {
          loadConversations();
          if (conversationId === result.conversation_id) highlightConv(result.conversation_id);
        }, 250);
        document.getElementById('status').textContent = t('ready');
      });
    };
  });
}

function showExportDialog(cid) {
  const targetCid = cid || conversationId;
  if (!targetCid) return;
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.dataset.conversationId = targetCid;
  const panel = document.createElement('div');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:340px;border:1px solid #333;';
  panel.innerHTML = '<h3 style="margin:0 0 16px;color:#e0e0e0;font-size:14px;">Export Conversation</h3>'
    + '<div style="display:flex;flex-direction:column;gap:8px;">'
    + '<button onclick="var p=this.closest(\'div[style*=fixed]\');var cid=p.dataset.conversationId;p.remove();exportConversation(cid)" style="background:#0f3460;color:#e0e0e0;border:1px solid #333;padding:10px;border-radius:6px;cursor:pointer;text-align:left;"><b>HTML</b><br><span style=font-size:11px;color:#888>Standalone HTML file for viewing/sharing</span></button>'
    + '<label style="display:flex;gap:8px;align-items:center;color:#ccc;font-size:12px;background:#0f3460;border:1px solid #333;padding:8px 10px;border-radius:6px;"><input id="_expIncludeFiles" type="checkbox" style="margin:0;"> Include attached/generated files</label>'
    + '<button onclick="var p=this.closest(\'div[style*=fixed]\');var cb=p.querySelector(\'#_expIncludeFiles\');var cid=p.dataset.conversationId;p.remove();exportPawflow(cb&&cb.checked,cid)" style="background:#0f3460;color:#e0e0e0;border:1px solid #333;padding:10px;border-radius:6px;cursor:pointer;text-align:left;"><b>PawFlow (.pfconv.zip)</b><br><span style=font-size:11px;color:#888>Full conversation archive, re-importable</span></button>'
    + '<button onclick="var p=this.closest(\'div[style*=fixed]\');var cid=p.dataset.conversationId;p.remove();exportClaudeCode(cid)" style="background:#0f3460;color:#e0e0e0;border:1px solid #333;padding:10px;border-radius:6px;cursor:pointer;text-align:left;"><b>Claude Code (.jsonl)</b><br><span style=font-size:11px;color:#888>Claude Code compatible format</span></button>'
    + '</div>'
    + '<div style="margin-top:12px;text-align:right;"><button onclick="this.closest(\'div[style*=fixed]\').remove()" style="background:#333;color:#ccc;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;">Cancel</button></div>';
  overlay.appendChild(panel);

  document.body.appendChild(overlay);
}

function exportPawflow(includeFilestore, cid) {
  const targetCid = cid || conversationId;
  if (!targetCid) return;
  document.getElementById('status').textContent = t('exporting');
  action$('conv_export_pawflow', { conversation_id: targetCid, include_filestore: !!includeFilestore }, { label: includeFilestore ? t('exportingWithImages') : t('exporting') }).subscribe(data => {
    if (data.error) { addMsg('error', t('exportFailed', { error: data.error })); }
    else { const a = document.createElement('a'); a.href = data.url; a.download = data.filename; a.click(); addMsg('system', t('exportedFile', { file: data.filename })); }
    document.getElementById('status').textContent = t('ready');
  });
}

function exportClaudeCode(cid) {
  const targetCid = cid || conversationId;
  if (!targetCid) return;
  document.getElementById('status').textContent = t('exporting');
  action$('conv_export_claude_code', { conversation_id: targetCid }, { label: t('exporting') }).subscribe(data => {
    if (data.error) { addMsg('error', t('exportFailed', { error: data.error })); }
    else { const a = document.createElement('a'); a.href = data.url; a.download = data.filename; a.click(); addMsg('system', t('exportedFile', { file: data.filename })); }
    document.getElementById('status').textContent = t('ready');
  });
}

function buildExportHtml(messages, nicknames, fileUrls) {
  const nicks = nicknames || {};
  function nickLookup(name) {
    const lk = (name || '').toLowerCase();
    for (const k of Object.keys(nicks)) { if (k.toLowerCase() === lk) return nicks[k]; }
    return name || '';
  }
  let body = '';
  for (const m of messages) {
    const type = m.type || m.role;
    if (type === 'system') continue;
    let cssClass = type;
    let content = m.content || '';
    if (Array.isArray(content)) content = content.filter(c => c.type === 'text').map(c => c.text).join('\n');
    if (typeof content !== 'string') content = JSON.stringify(content);
    let badge = '';
    if (type === 'assistant' || type === 'user') {
      const src = m.source || {};
      const srcName = nickLookup(src.name);
      if (srcName) {
        const h = [...srcName].reduce((a, c) => ((a << 5) - a + c.charCodeAt(0)) | 0, 0);
        const hue = Math.abs(h) % 360;
        badge = '<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:8px;margin-right:4px;font-weight:600;background:hsl(' + hue + ',60%,25%);color:hsl(' + hue + ',80%,80%)">' + escapeHtml(srcName) + '</span>';
      }
      if (type === 'assistant' && src.type === 'agent' && src.name) cssClass = 'subagent';
      content = content.replace(/^\[[^\]]+\]:\s*/, '');
    }
    if (type === 'tool_call' || type === 'tool_result') cssClass = 'tool';
    let html = escapeHtml(content);
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(_, lang, code) {
      var cls = lang ? ' class="language-' + lang + '"' : '';
      return '<pre><code' + cls + '>' + code + '</code></pre>';
    });
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    for (const f of fileUrls) {
      if (isImageFile(f.name)) {
        html = html.split(escapeHtml(f.url)).join('<br><img src="images/' + f.name + '" style="max-width:512px;max-height:512px;border-radius:8px;"><br>');
      }
    }
    body += '<div class="msg ' + cssClass + '">' + badge + html + '</div>\n';
  }
  return '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
    + '<title>PawFlow Conversation Export</title>'
    + '<style>'
    + 'body { font-family: -apple-system, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; max-width: 900px; margin: 0 auto; }'
    + '.msg { padding: 10px 14px; border-radius: 12px; margin-bottom: 12px; line-height: 1.5; font-size: 14px; white-space: pre-wrap; word-wrap: break-word; }'
    + '.msg a { color: #4fc3f7; }'
    + '.msg code { background: rgba(0,0,0,0.3); padding: 1px 5px; border-radius: 3px; }'
    + '.msg pre { background: rgba(0,0,0,0.4); padding: 10px; border-radius: 6px; overflow-x: auto; }'
    + '.msg.user { background: #0f3460; color: white; margin-left: 20%; border-left: 3px solid #4ecdc4; }'
    + '.msg.assistant { background: #16213e; border: 1px solid #0f3460; margin-right: 20%; border-left: 3px solid #e94560; }'
    + '.msg.subagent { background: #0d1b2a; border: 1px solid #1a3a5c; margin-right: 20%; border-left: 3px solid #6c5ce7; }'
    + '.msg.tool { background: #0f1629; color: #808090; font-size: 12px; border-left: 2px solid #0f3460; margin-right: 30%; }'
    + '.msg.btw { background: #0d1b2a; font-size: 13px; border-left: 3px solid #60a5fa; margin-right: 20%; font-style: italic; }'
    + 'img { display: block; margin: 8px 0; }'
    + '</style></head><body>'
    + '<h1 style="color:#e94560;margin-bottom:20px;">PawFlow Conversation Export</h1>'
    + '<p style="color:#6c6c8a;margin-bottom:20px;">Exported: ' + new Date().toLocaleString() + '</p>'
    + body + '</body></html>';
}

function buildSimpleZip(files) {
  const parts = [];
  const directory = [];
  let offset = 0;
  for (const f of files) {
    const nameBytes = new TextEncoder().encode(f.name);
    const data = f.content;
    const header = new Uint8Array(30 + nameBytes.length);
    const hv = new DataView(header.buffer);
    hv.setUint32(0, 0x04034b50, true);
    hv.setUint16(4, 20, true);
    hv.setUint16(8, 0, true);
    const crc = crc32(data);
    hv.setUint32(14, crc, true);
    hv.setUint32(18, data.length, true);
    hv.setUint32(22, data.length, true);
    hv.setUint16(26, nameBytes.length, true);
    header.set(nameBytes, 30);
    parts.push(header);
    parts.push(data);
    const cdEntry = new Uint8Array(46 + nameBytes.length);
    const cv = new DataView(cdEntry.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, data.length, true);
    cv.setUint32(24, data.length, true);
    cv.setUint16(28, nameBytes.length, true);
    cv.setUint32(42, offset, true);
    cdEntry.set(nameBytes, 46);
    directory.push(cdEntry);
    offset += header.length + data.length;
  }
  const cdOffset = offset;
  let cdSize = 0;
  for (const d of directory) { parts.push(d); cdSize += d.length; }
  const eocd = new Uint8Array(22);
  const ev = new DataView(eocd.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, cdOffset, true);
  parts.push(eocd);
  return new Blob(parts, { type: 'application/zip' });
}

function crc32(data) {
  let crc = 0xFFFFFFFF;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let j = 0; j < 8; j++) crc = (crc >>> 1) ^ (crc & 1 ? 0xEDB88320 : 0);
  }
  return (crc ^ 0xFFFFFFFF) >>> 0;
}

