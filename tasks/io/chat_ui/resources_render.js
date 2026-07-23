// Part of the resources sidebar, split from resources.js (<=800 lines/file).
// Load order matters: see _JS_MODULES in tasks/io/serve_chat_ui.py.

var _loadResourcesTimer = null;
async function loadResources() {
  // Debounce: coalesce rapid calls into one (300ms window)
  if (_loadResourcesTimer) clearTimeout(_loadResourcesTimer);
  _loadResourcesTimer = setTimeout(_loadResourcesNow, 300);
}
function _loadResourcesNow() {
  _loadResourcesTimer = null;
  // The panel is shown even with no conversation selected: _renderResourcesData
  // renders only the scope-independent sections (Flows, Services, Packages,
  // Variables, Secrets, Agent/Flows repositories) in that case. Only the
  // conversation-scoped data fetch is skipped below. (Previously this returned
  // early and hid the whole panel, so a user with no conversation — e.g. a
  // freshly-created/technical user — could never see it.)
  var _panel = document.getElementById('resourcesPanel');
  if (_panel) _panel.style.display = 'block';
  var _noConv = !conversationId;
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
  action$('list_resources', _withView({})).subscribe(d => { _resData = d || {}; _tryRender(); });
  listServices$(null, true).subscribe(d => { _svcData = d || { services: [] }; _tryRender(); });
  action$('pfp_list_installed', { scope: 'user', conversation_id: conversationId || '' }).subscribe(d => { _pfpUserData = d || { packages: [] }; _tryRender(); });
  if (_noConv) {
    // No conversation → no conversation-scoped packages to fetch.
    _pfpConvData = { packages: [] };
  } else {
    action$('pfp_list_installed', { scope: 'conversation', conversation_id: conversationId }).subscribe(d => { _pfpConvData = d || { packages: [] }; _tryRender(); });
  }
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

    // With no conversation selected (e.g. a freshly-created user before any
    // conv exists) the panel shows ONLY the scope-independent sections the
    // user can act on without a conv: Flows, Services, Packages, Variables,
    // Secrets, Agent Repository, Flows Repository. The conversation-scoped
    // sections (Agents, Tasks, Relays, Filesystem, Summarizer, Linked
    // Accounts) and the conv-irrelevant repos (Skills/Prompts/Themes/Voices/
    // Tasks/MCP/AgentHooks/Tools) are hidden until a conv is selected.
    const noConv = !(typeof conversationId !== 'undefined' && conversationId);

    // ─────────────────────────────────────────────────────────────
    // LIVE sections (conversation state): Agents, Tasks, Flows,
    // Services, Relays. Built synchronously into `liveHtml`.
    // ─────────────────────────────────────────────────────────────
    let liveHtml = '';

    if (!noConv) {
    // Agents (conversation members)
    liveHtml += _sectionHeader(t('agents'), 'agent');
    if (data.agents && data.agents.length) {
      data.agents.forEach(function(a) {
        var isPrimary = a.active;
        var aName = String(a.name || '');
        var aNameHtml = escapeHtml(aName);
        var aNameAttr = _pfpAttr(aName);
        var aKeyLc = aName.toLowerCase();
        var primaryColor = isPrimary ? 'var(--pf-success)' : 'var(--pf-muted)';
        var textColor = isPrimary ? 'var(--pf-text)' : 'var(--pf-muted)';
        var primaryTitle = isPrimary ? t('primaryAgent') : t('setPrimaryAgent');
        var primaryArrow = isPrimary ? '&#9654;' : '&#9655;';
        var autoconvTag = a.autoconv ? '<span style="font-size:9px;color:var(--pf-success);margin-left:2px;">' + String.fromCodePoint(0x1F504) + '</span>' : '';
        // Hydrate the global cache through the same monotonic path used by
        // Resource polling must not touch the context gauge. The gauge is
        // updated only by live context events and the explicit /context view.
        liveHtml += '<div data-agent-name="' + aNameAttr + '" style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"'
          + ' oncontextmenu="showAgentMenu(event,' + _pfpJsArg(aName) + ',' + _pfpJsArg(a.scope || '') + ',' + (a.autoconv ? 'true' : 'false') + ');return false;">'
          + '<span style="cursor:pointer;color:' + primaryColor + ';font-size:11px;" title="' + _pfpAttr(primaryTitle) + '"'
          + ' onclick="_selectAgentAndRefresh(this.dataset.n)" data-n="' + aNameAttr + '">' + primaryArrow + '</span>'
          + _scopeBadge(a.scope)
          + '<span style="color:' + textColor + ';font-size:12px;cursor:pointer;flex:1;"'
          + ' onclick="_selectAgentAndRefresh(this.dataset.n)" data-n="' + aNameAttr + '">' + aNameHtml + '</span>'
          + autoconvTag
          + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + _pfpAttr(t('removeFromConversation')) + '"'
          + ' onclick="_removeAgentFromConv(this.dataset.n)" data-n="' + aNameAttr + '">&times;</span>'
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
          liveHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showRunningTaskMenu(event,${_pfpJsArg(t.task_id)},${_pfpJsArg(t.agent)},${_pfpJsArg(t.status)});return false;">
            <span style="color:${statusColor};font-size:11px;">${statusIcon}</span>
            <span style="color:var(--pf-muted);font-size:11px;" title="${_pfpAttr(t.task)}">${escapeHtml(label)}</span>
            <span style="color:var(--pf-muted);font-size:10px;">[${escapeHtml(t.iterations)}/${escapeHtml(t.max_iterations)}]</span>
          </div>`;
        });
      } else {
        liveHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('noTasksRunning')) + '</div>';
      }
    }
    liveHtml += _sectionFooter();
    }

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
        const flowCtx = ` oncontextmenu="showFlowInstanceMenu(event,${_pfpJsArg(f.instance_id)},${_pfpJsArg(f.status)},${_pfpJsArg(f.scope)});return false;"`;
        liveHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${flowCtx}>
          ${_scopeBadge(f.scope)}<span style="color:${statusColor};font-size:11px;">${statusIcon} ${escapeHtml(f.flow_name || f.instance_id)}</span>${_ownerBadge(f)}
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
        const svcCtx = ` oncontextmenu="showServiceMenu(event,${_pfpJsArg(s.service_id)},${_pfpJsArg(s.scope)},${s.enabled});return false;"`;
        liveHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;"${svcCtx}>
          ${_scopeBadge(s.scope)}<span style="color:var(--pf-muted);font-size:11px;">${statusDot} <b>${escapeHtml(s.service_id)}</b> <span style="color:var(--pf-muted)">(${escapeHtml(s.service_type)})</span>${escapeHtml(dockerTag)}</span>${_ownerBadge(s)}
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
          const webApps = objects.filter(o => o.kind === 'web_app' && o.url);
          const webAppLinks = webApps.map(w => '<a href="' + _pfpAttr(w.url) + '" target="_blank" rel="noopener" style="font-size:11px;color:var(--pf-accent);text-decoration:none;padding:0 3px;" title="' + _pfpAttr(t('pfpOpenWebApp', { name: w.name || '' })) + '">\u2197</a>').join('');
          liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" title="' + objectTitle + '">'
            + _scopeBadge(scope)
            + '<span style="color:var(--pf-text);font-size:12px;flex:1;">' + pkgName + '</span>'
            + (blockers.length ? '<span style="color:var(--pf-warning);font-size:10px;" title="' + _pfpAttr(t('pfpBlockingDependents')) + '">!' + escapeHtml(String(blockers.length)) + '</span>' : '')
            + '<span style="color:var(--pf-muted);font-size:10px;">[' + escapeHtml(objectLabel) + ']</span>'
            + webAppLinks
            + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + escapeHtml(t('uninstall')) + '" onclick="_showPfpUninstallDialog(this.dataset.package, this.dataset.scope)" data-package="' + packageId + '" data-scope="' + packageScope + '">&times;</span>'
            + '</div>';
        });
      } else {
        liveHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noPfpPackagesInstalled')) + '</div>';
      }
    }
    liveHtml += _sectionFooter();

    if (!noConv) {
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
          var isConvDefault = _rbDefaults['*'] === rid || (!_rbDefaults['*'] && _relayIds.length === 1);
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
          var clickDefault = isConvDefault ? '' : ' onclick="event.stopPropagation(); fireAction(\'relay_default\',{relay_id:' + _pfpJsArg(rid) + '}); setTimeout(loadResources, 500)"';
          var det = _rbDetails[rid] || {};
          // 🟢 connected / 🟡 connecting (enabled, dialing back / lazy) / 🔴 down.
          // Same tri-state the Services list uses for a relay's started dot.
          var connDot = det.connected ? '\u{1F7E2}' : (det.connecting ? '\u{1F7E1}' : '\u{1F534}');
          var pathInfo = '';
          if (det.root) pathInfo += '<div style="font-size:10px;color:var(--pf-muted);margin-left:20px;">docker: <code>' + escapeHtml(det.root) + '</code></div>';
          if (det.host_root) pathInfo += '<div style="font-size:10px;color:var(--pf-muted);margin-left:20px;">local: <code>' + escapeHtml(det.host_root) + '</code></div>';
          var _rbDefaultLocal = (_rb.default_local || {})[rid] || {};
          var _detWithLocal = Object.assign({}, det, {_default_local: _rbDefaultLocal});
          var _detJson = _pfpAttr(JSON.stringify(_detWithLocal));
          var defaultBadge = isConvDefault ? ' <span style="font-size:9px;color:var(--pf-success);">' + escapeHtml(t('defaultRelay')) + '</span>' : '';
          liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" onclick="_showRelayInfoDialog(' + _pfpJsArg(rid) + ',' + _detJson + ',' + (isConvDefault ? 'true' : 'false') + ');return false;" oncontextmenu="_showRelayInfoDialog(' + _pfpJsArg(rid) + ',' + _detJson + ',' + (isConvDefault ? 'true' : 'false') + ');return false;">'
            + '<span style="color:' + color + ';font-size:11px;cursor:pointer;" title="' + _pfpAttr(titleText) + '"' + clickDefault + '>' + icon + '</span>'
            + '<span style="font-size:11px;">' + connDot + '</span>'
            + '<span style="color:' + color + ';font-size:12px;">' + escapeHtml(rid) + star + '</span>' + defaultBadge
            + agentTags
            + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + _pfpAttr(t('unlink')) + '"'
            + ' onclick="event.stopPropagation(); fireAction(\'relay_unlink\',{relay_id:' + _pfpJsArg(rid) + '}); setTimeout(loadResources, 500)">&times;</span>'
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
          var serviceId = String(s.service_id || '');
          var serviceIdAttr = _pfpAttr(serviceId);
          var scope = String(s.scope || 'user');
          var isRclone = s.service_type === 'rcloneFilesystem';
          var mountPath = escapeHtml(isRclone ? (s.mount_path || '') : '');
          var tag = escapeHtml(isRclone ? 'rclone' : (s.service_type || 'filesystem'));
          var enabledDot = s.enabled === false ? '\u{1F534}' : '\u{1F7E2}';
          liveHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;">'
            + _scopeBadge(scope)
            + '<span style="font-size:11px;">' + enabledDot + '</span>'
            + '<span style="color:var(--pf-text);font-size:12px;flex:1;">' + escapeHtml(serviceId) + '</span>'
            + '<span style="font-size:9px;color:var(--pf-muted);background:color-mix(in srgb, var(--pf-muted) 14%, var(--pf-panel));padding:1px 4px;border-radius:3px;">' + tag + '</span>'
            + '<span style="cursor:pointer;font-size:11px;color:var(--pf-danger);padding:0 3px;" title="' + _pfpAttr(t('unlink')) + '"'
            + ' onclick="_unlinkRemoteFs(this.dataset.serviceId)" data-service-id="' + serviceIdAttr + '">&times;</span>'
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
          var aName = String(a.name || '');
          var aNameAttr = _pfpAttr(aName);
          repoHtml += '<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;"'
            + ' oncontextmenu="showResourceMenu(event,\'agent\',' + _pfpJsArg(aName) + ',' + _pfpJsArg(a.scope || '') + ',null);return false;">'
            + _scopeBadge(a.scope)
            + '<span style="color:var(--pf-muted);font-size:12px;flex:1;">' + escapeHtml(aName) + '</span>'
            + _ownerBadge(a)
            + '<span style="color:var(--pf-accent);font-size:10px;cursor:pointer;padding:0 4px;" title="' + _pfpAttr(t('addToConversation')) + '"'
            + ' onclick="showAddAgentToConvDialog(this.dataset.n)" data-n="' + aNameAttr + '">+</span>'
            + '</div>';
        });
      } else {
        repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted);">' + escapeHtml(t('allAgentsInConversation')) + '</div>';
      }
    }
    repoHtml += _sectionFooter();

    if (!noConv) {
    // ── Skills Repository ──
    repoHtml += _repoSectionHeader(t('skillsRepository'), 'skill', {
      createOnclick: "showSkillAddDialog()",
    });
    { const allSkills = data.skills || [];
      if (allSkills.length) {
        allSkills.forEach(s => {
          const assignedTo = s.assigned_to || [];
          const assignedTag = assignedTo.length ? ' <span style="color:var(--pf-muted);font-size:9px;">\u2192 ' + assignedTo.map(escapeHtml).join(', ') + '</span>' : '';
          const skillInvalid = s.invalid
            ? ' <span style="font-size:9px;" title="' + _pfpAttr(s.invalid) + '">⚠ ' + escapeHtml(s.invalid) + '</span>'
            : '';
          const skillColor = s.invalid ? 'var(--pf-danger,#e05260)' : 'var(--pf-text)';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'skill',${_pfpJsArg(s.name)},${_pfpJsArg(s.scope || '')});return false;">
            ${_scopeBadge(s.scope)}<span style="color:${skillColor};font-size:12px;flex:1;">${escapeHtml(s.name)}${skillInvalid || assignedTag}</span>${_ownerBadge(s)}
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
          const desc = p.description ? ' title="' + _pfpAttr(p.description) + '"' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer"${desc}
            onclick="_usePrompt(${_pfpJsArg(p.name)},${p.has_parameters})" oncontextmenu="showResourceMenu(event,'prompt',${_pfpJsArg(p.name)},${_pfpJsArg(p.scope || '')});return false;">
            ${_scopeBadge(p.scope)}<span style="font-size:11px">${icon}</span>
            <span style="font-size:12px;color:var(--pf-text)">${escapeHtml(title)}</span>${_ownerBadge(p)}
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
          const desc = t.description ? ' title="' + _pfpAttr(t.description) + '"' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer"${desc}
            onclick="_applyThemeFromResource(${_pfpJsArg(ref)})" oncontextmenu="_showThemeMenu(event,${_pfpJsArg(ref)},${builtinArg},${_pfpJsArg(t.scope || '')});return false;">
            ${_scopeBadge(t.scope)}<span style="font-size:11px;color:var(--pf-accent);">\u25A3</span>
            <span style="font-size:12px;color:var(--pf-text);flex:1;">${escapeHtml(t.title || t.name)}</span>
            <span style="color:var(--pf-muted);font-size:10px;">${escapeHtml(cssLabel)}</span>
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
            ? `<span style="cursor:pointer;color:var(--pf-accent);font-size:11px;padding:0 4px;" title="${_pfpAttr(t('previewReferenceAudio'))}" onclick="_previewVoice(${_pfpJsArg(previewUrl)})">\u25B6</span>`
            : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" title="${_pfpAttr((v.provider || '') + ' \u2014 ' + paradigm)}">
            <span style="color:${pColor};font-size:9px;font-weight:600;border:1px solid ${pColor};border-radius:3px;padding:0 3px;">${escapeHtml(pBadge)}</span>
            <span style="color:var(--pf-text);font-size:12px;flex:1;">\u{1F399} ${escapeHtml(v.name)}<span style="color:var(--pf-muted);font-size:10px;">${prov}</span></span>
            ${previewBtn}
            <span style="cursor:pointer;color:var(--pf-muted);font-size:11px;padding:0 4px;" title="${_pfpAttr(t('renameVoiceClone'))}" onclick="_renameVoiceClone(${_pfpJsArg(v.name)})">\u270E</span>
            <span style="cursor:pointer;color:var(--pf-danger);font-size:11px;padding:0 4px;" title="${_pfpAttr(t('deleteVoiceClone'))}" onclick="_deleteVoiceClone(${_pfpJsArg(v.name)})">\u2716</span>
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
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'task_def',${_pfpJsArg(t.name)},${_pfpJsArg(t.scope || '')});return false;">
            ${_scopeBadge(t.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;" title="${_pfpAttr(t.description)}">${escapeHtml(t.name)}</span>${_ownerBadge(t)}
            <span style="color:var(--pf-muted);font-size:10px;">[${escapeHtml(t.default_interval)}]</span>
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
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;" oncontextmenu="showResourceMenu(event,'mcp',${_pfpJsArg(m.name)},${_pfpJsArg(m.scope || '')});return false;">
            ${_scopeBadge(m.scope)}<span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(m.name)}</span>${_ownerBadge(m)}
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
          const desc = h.description ? ' title="' + _pfpAttr(h.description) + '"' : '';
          repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer;"${desc} oncontextmenu="showResourceMenu(event,'agent_hook',${_pfpJsArg(h.name)},${_pfpJsArg(h.scope || '')});return false;">
            ${_scopeBadge(h.scope)}<span style="color:var(--pf-accent);font-size:11px">\u2693</span>
            <span style="color:var(--pf-text);font-size:12px;flex:1;">${escapeHtml(h.name)}</span>${_ownerBadge(h)}
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
        repoHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;cursor:pointer" onclick="showToolCallDialog(${_pfpJsArg(t.name)})">
          <span style="color:var(--pf-accent);font-size:11px">\u26A1</span>
          <span style="font-size:12px;color:var(--pf-text)">${escapeHtml(t.name)}</span>
        </div>`;
      });
      if (!tools.length) repoHtml += '<div style="margin-left:8px;font-size:11px;color:var(--pf-muted)">' + escapeHtml(t('loading')) + '</div>';
    }
    repoHtml += _sectionFooter();
    }

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
      // Variables and Secrets headers render unconditionally — like every
      // other section (Services, Flows, …). Gating the header on a non-empty
      // list hid the whole section when empty, taking the '+' create button
      // with it, so a fresh user could never add a first variable/secret.
      varSecHtml += _sectionHeader(t('variables'), '_param');
      if (ps.parameters && ps.parameters.length) {
        ps.parameters.forEach(p => {
          const truncVal = p.value.length > 30 ? p.value.substring(0, 30) + '...' : p.value;
          varSecHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${p.key}','${p.scope}');return false;">
            ${_scopeBadge(p.scope)}<span style="color:var(--pf-muted);font-size:11px;"><b>${escapeHtml(p.key)}</b> = ${escapeHtml(truncVal)}</span>
          </div>`;
        });
      } else {
        varSecHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noVariables')) + '</div>';
      }
      varSecHtml += _sectionFooter();
      varSecHtml += _sectionHeader(t('secrets'), '_secret');
      if (ps.secrets && ps.secrets.length) {
        ps.secrets.forEach(s => {
          varSecHtml += `<div style="display:flex;align-items:center;gap:4px;margin-left:8px;margin-bottom:2px;" oncontextmenu="showParamMenu(event,'${s.key}','${s.scope}',true);return false;">
            ${_scopeBadge(s.scope)}<span style="color:var(--pf-muted);font-size:11px;"><b>${escapeHtml(s.key)}</b> = ********</span>
          </div>`;
        });
      } else {
        varSecHtml += '<div style="color:var(--pf-muted);font-size:10px;margin-left:8px;">' + escapeHtml(t('noSecrets')) + '</div>';
      }
      varSecHtml += _sectionFooter();
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
      const fullHtml = _viewAllBarHtml() + liveHtml + varSecHtml + repoHtml + (noConv ? '' : linksHtml);
      // Only update DOM if content actually changed (prevents flash/blink)
      if (el.innerHTML !== fullHtml) el.innerHTML = fullHtml;
    });
  } catch (e) {
    document.getElementById('resourcesContent').innerHTML = '';
  }
}

// ── Resource context menu ────────────────────────────────────────────────────────────
