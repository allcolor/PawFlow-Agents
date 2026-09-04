// ACP registry catalogue picker for llmConnection service forms.
(function(root) {
  'use strict';

  if (!root || root.PawFlowAcpRegistry) return;

  const JOB_TIMEOUT_MS = 5 * 60 * 1000;
  const JOB_POLL_MS = 1000;

  function _text(key, fallback, values) {
    let value = key;
    if (typeof root.t === 'function') {
      try { value = root.t(key, values || {}); } catch (_) { value = key; }
    }
    if (!value || value === key) value = fallback;
    Object.entries(values || {}).forEach(function(entry) {
      value = String(value).split('{' + entry[0] + '}').join(String(entry[1]));
    });
    return String(value);
  }

  function _escape(value) {
    if (typeof root.escapeHtml === 'function') return root.escapeHtml(value == null ? '' : value);
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _distributionNote(kind, entry, catalogue) {
    if (kind === 'binary' && catalogue.platform
        && !(entry.platforms || []).includes(catalogue.platform)) {
      return _text(
        'acpRegistryUnavailableOnPlatform',
        'not available for {platform}',
        { platform: catalogue.platform },
      );
    }
    if ((kind === 'npx' || kind === 'uvx')
        && !(catalogue.runners || {})[kind]) {
      return _text(
        'acpRegistryRunnerUnavailable',
        '{runner} is not installed on this server',
        { runner: kind },
      );
    }
    return '';
  }

  function _renderEntry(entry, catalogue) {
    const quarantined = !!entry.quarantined;
    const distributions = entry.distributions || [];
    const choices = distributions.map(function(kind) {
      return { kind: kind, note: _distributionNote(kind, entry, catalogue) };
    });
    const usable = choices.filter(function(choice) { return !choice.note; });
    const options = usable.concat(
      choices.filter(function(choice) { return !!choice.note; }),
    ).map(function(choice) {
      const kind = choice.kind;
      const note = choice.note;
      return '<option value="' + _escape(kind) + '"'
        + (note ? ' disabled' : '') + '>'
        + _escape(kind + (note ? ' — ' + note : '')) + '</option>';
    }).join('');
    const auth = (entry.auth_types || []).join(', ')
      || _text('acpRegistryNoAuth', 'not advertised');
    const capabilities = entry.load_session === true
      ? _text('acpRegistryLoadSession', 'loadSession')
      : entry.load_session === false
        ? _text('acpRegistryNoLoadSession', 'no loadSession')
        : _text('acpRegistryCapabilitiesUnknown', 'not advertised');
    const license = entry.license || 'proprietary';
    const disabled = quarantined || !usable.length;
    const quarantine = quarantined
      ? '<div style="color:var(--pf-danger);font-size:11px;margin-top:5px;">'
        + _escape(_text('acpRegistryQuarantined', 'Quarantined: {reason}', {
          reason: entry.quarantine_reason || '',
        })) + '</div>'
      : '';
    const noDistribution = !quarantined && !usable.length
      ? '<div style="color:var(--pf-warning);font-size:11px;margin-top:5px;">'
        + _escape(_text(
          'acpRegistryNoDistribution',
          'No usable distribution for this server',
        )) + '</div>'
      : '';
    const licenseUrl = entry.license_url
      ? '<div style="color:var(--pf-muted);font-size:10px;overflow-wrap:anywhere;">'
        + _escape(entry.license_url) + '</div>'
      : '';

    return '<article data-acp-registry-agent="' + _escape(entry.id)
      + '" style="border:1px solid var(--pf-border);border-radius:6px;padding:9px;margin-top:8px;background:var(--pf-panel);">'
      + '<div style="display:flex;gap:8px;align-items:start;">'
      + '<div style="flex:1;min-width:0;">'
      + '<div style="font-weight:600;color:var(--pf-text);">' + _escape(entry.name || entry.id) + '</div>'
      + '<div style="color:var(--pf-muted);font-size:11px;margin-top:2px;">'
      + _escape(entry.description || '') + '</div>'
      + '</div>'
      + '<span style="color:var(--pf-muted);font-size:10px;">'
      + _escape(_text('acpRegistryVersion', 'Version')) + ' ' + _escape(entry.version || '') + '</span>'
      + '</div>'
      + '<div style="display:grid;grid-template-columns:auto 1fr;gap:3px 8px;margin-top:7px;font-size:10px;">'
      + '<span style="color:var(--pf-muted);">' + _escape(_text('acpRegistryLicense', 'License')) + '</span>'
      + '<span style="color:var(--pf-text);">' + _escape(license) + '</span>'
      + '<span style="color:var(--pf-muted);">' + _escape(_text('acpRegistryAuth', 'Authentication')) + '</span>'
      + '<span style="color:var(--pf-text);">' + _escape(auth) + '</span>'
      + '<span style="color:var(--pf-muted);">' + _escape(_text('capabilities', 'Capabilities')) + '</span>'
      + '<span style="color:var(--pf-text);">' + _escape(capabilities) + '</span>'
      + '</div>'
      + licenseUrl + quarantine + noDistribution
      + '<div style="display:flex;gap:6px;align-items:center;margin-top:8px;">'
      + '<label style="color:var(--pf-muted);font-size:10px;">'
      + _escape(_text('acpRegistryDistribution', 'Distribution')) + '</label>'
      + '<select data-acp-registry-distribution'
      + (disabled ? ' disabled' : '')
      + ' style="flex:1;min-width:0;background:var(--pf-bg);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:4px;padding:5px;">'
      + options + '</select>'
      + '<button type="button" data-acp-registry-import'
      + (disabled ? ' disabled' : '')
      + ' style="background:var(--pf-accent);color:var(--pf-bg);border:none;border-radius:4px;padding:6px 10px;cursor:pointer;">'
      + _escape(_text('acpRegistryApply', 'Fill the form from the registry')) + '</button>'
      + '</div>'
      + '<div data-acp-registry-status aria-live="polite" style="color:var(--pf-muted);font-size:10px;margin-top:5px;"></div>'
      + '</article>';
  }

  function renderCatalogue(catalogue) {
    const entries = catalogue.entries || [];
    const stale = catalogue.stale
      ? '<div style="color:var(--pf-warning);font-size:11px;margin-top:6px;">'
        + _escape(_text('acpRegistryStale', 'The cached registry catalogue is being shown.')) + '</div>'
      : '';
    const body = entries.length
      ? entries.map(function(entry) { return _renderEntry(entry, catalogue); }).join('')
      : '<div style="color:var(--pf-muted);font-size:11px;margin-top:8px;">'
        + _escape(_text('acpRegistryNoEntries', 'No ACP registry agents are available.')) + '</div>';
    return '<div style="display:flex;align-items:center;gap:8px;">'
      + '<strong style="color:var(--pf-text);font-size:12px;flex:1;">'
      + _escape(_text('acpRegistryTitle', 'Import from ACP registry')) + '</strong>'
      + '<button type="button" data-acp-registry-refresh style="background:none;border:1px solid var(--pf-border);color:var(--pf-muted);border-radius:4px;padding:4px 7px;cursor:pointer;">'
      + _escape(_text('refresh', 'Refresh')) + '</button>'
      + '<button type="button" data-acp-registry-close aria-label="'
      + _escape(_text('close', 'Close'))
      + '" style="background:none;border:none;color:var(--pf-muted);font-size:16px;cursor:pointer;">&times;</button>'
      + '</div>' + stale + body;
  }

  function _dispatchChange(element) {
    if (!element) return;
    if (typeof element.dispatchEvent === 'function' && typeof root.Event === 'function') {
      element.dispatchEvent(new root.Event('change', { bubbles: true }));
    } else if (typeof element.dispatch === 'function') {
      element.dispatch('change', { bubbles: true });
    }
  }

  function applyConfig(config) {
    let applied = 0;
    const entries = Object.entries(config || {}).sort(function(left, right) {
      if (left[0] === 'provider') return -1;
      if (right[0] === 'provider') return 1;
      return 0;
    });
    entries.forEach(function(entry) {
      const element = root.document.getElementById('svc-p-' + entry[0]);
      if (!element) return;
      if (element.type === 'checkbox') element.checked = !!entry[1];
      else element.value = String(entry[1] == null ? '' : entry[1]);
      _dispatchChange(element);
      applied++;
    });

    const installName = root.document.getElementById('svc-install-name');
    if (installName && !String(installName.value || '').trim()) {
      try {
        const record = JSON.parse(config.acp_registry || '{}');
        if (record.id) installName.value = String(record.id).replace(/[^A-Za-z0-9_-]/g, '_');
      } catch (_) {}
    }
    return applied;
  }

  function _call(action, payload) {
    return root.rxjs.firstValueFrom(root.action$(action, payload));
  }

  function _delay(milliseconds) {
    return new Promise(function(resolve) { root.setTimeout(resolve, milliseconds); });
  }

  async function _waitForJob(jobId, picker) {
    const deadline = root.Date.now() + JOB_TIMEOUT_MS;
    while (root.Date.now() < deadline) {
      if (!picker.isConnected) return { cancelled: true };
      await _delay(JOB_POLL_MS);
      const result = await _call('acp_registry_prepare_status', { job_id: jobId });
      if (result.error || result.status !== 'pending') return result;
    }
    return { error: _text('acpRegistryTimedOut', 'Registry import timed out.') };
  }

  function _ancestorWithAgent(node) {
    let current = node;
    while (current && !(current.dataset && current.dataset.acpRegistryAgent)) {
      current = current.parentElement || current.parentNode;
    }
    return current;
  }

  function _wire(picker, payload, reload) {
    const close = picker.querySelector('[data-acp-registry-close]');
    if (close) close.addEventListener('click', function() { picker.remove(); });
    const refresh = picker.querySelector('[data-acp-registry-refresh]');
    if (refresh) refresh.addEventListener('click', function() { reload(true); });

    Array.from(picker.querySelectorAll('[data-acp-registry-import]')).forEach(function(button) {
      button.addEventListener('click', async function() {
        const row = _ancestorWithAgent(button);
        const select = row && row.querySelector('[data-acp-registry-distribution]');
        const status = row && row.querySelector('[data-acp-registry-status]');
        if (!row || !select || !select.value) return;
        button.disabled = true;
        button.textContent = _text('acpRegistryPreparing', 'Preparing...');
        if (status) status.textContent = _text('acpRegistryPreparing', 'Preparing...');
        try {
          const body = {
            agent_id: row.dataset.acpRegistryAgent,
            distribution: select.value,
          };
          const cwd = payload && payload.config && payload.config.acp_cwd;
          if (cwd) body.cwd = cwd;
          let result = await _call('acp_registry_prepare', body);
          if (result.status === 'pending' && result.job_id) {
            result = await _waitForJob(result.job_id, picker);
          }
          if (result.cancelled) return;
          if (result.error) throw new Error(result.error);
          if (result.status !== 'ready' || !result.config) {
            throw new Error(_text('acpRegistryInvalidResponse', 'The registry returned an invalid import result.'));
          }
          const applied = applyConfig(result.config);
          const appliedMessage = _text(
            'acpRegistryApplied',
            '{n} fields filled from the registry. Review them, then save the service.',
            { n: applied },
          );
          if (status) status.textContent = appliedMessage;
          button.textContent = _text('acpRegistryAppliedShort', 'Applied');
          if (typeof root.addMsg === 'function') {
            root.addMsg('system', appliedMessage);
          }
        } catch (error) {
          if (status) status.textContent = error.message || String(error);
          button.disabled = false;
          button.textContent = _text('acpRegistryApply', 'Fill the form from the registry');
        }
      });
    });
  }

  async function open(anchor, payload) {
    const host = anchor && (anchor.parentElement || anchor.parentNode);
    if (!host) throw new Error(_text('acpRegistryMissingHost', 'The service action container is unavailable.'));
    Array.from(host.querySelectorAll('[data-acp-registry-picker]')).forEach(function(node) {
      node.remove();
    });
    const picker = root.document.createElement('div');
    picker.dataset.acpRegistryPicker = '1';
    picker.style.cssText = 'margin-top:8px;border:1px solid var(--pf-border);border-radius:6px;padding:9px;background:var(--pf-sidebar);max-height:min(520px,65vh);overflow:auto;';
    host.appendChild(picker);

    const load = async function(refresh) {
      picker.innerHTML = '<div style="color:var(--pf-muted);font-size:11px;">'
        + _escape(_text('acpRegistryLoading', 'Loading the ACP registry...')) + '</div>';
      try {
        const catalogue = await _call('acp_registry_catalogue', { refresh: !!refresh });
        if (catalogue.error) throw new Error(catalogue.error);
        picker.innerHTML = renderCatalogue(catalogue);
        _wire(picker, payload || {}, load);
      } catch (error) {
        picker.innerHTML = '<div style="color:var(--pf-danger);font-size:11px;">'
          + _escape(error.message || String(error)) + '</div>';
      }
    };

    await load(false);
    return picker;
  }

  root.PawFlowAcpRegistry = {
    applyConfig: applyConfig,
    open: open,
    renderCatalogue: renderCatalogue,
  };
})(typeof window !== 'undefined' ? window : globalThis);
