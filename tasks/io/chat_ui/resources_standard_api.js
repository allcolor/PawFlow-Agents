// Standard OpenAI / Anthropic publication controls.
// Loaded before resources_a2a.js. This file owns rendering, validation,
// endpoint/snippet formatting, and standard-API lifecycle handlers.

const _STANDARD_API_MATERIAL_FIELDS = [
  'standard_api_enabled',
  'api_model_id',
  'api_permission_mode',
  'api_session_ttl_seconds',
  'api_max_sessions_per_key',
  'api_max_concurrent_runs_per_key',
  'strict_fields',
  'api_request_overrides_json',
  'api_input_modalities_json',
  'api_chat_completions_enabled',
  'api_responses_enabled',
  'api_anthropic_messages_enabled',
  'api_disconnect_policy',
];

function _standardApiCapabilities(state) {
  const capabilities = (state && state.standard_api_capabilities) || {};
  capabilities.dialects = capabilities.dialects || {};
  capabilities.permission_modes = capabilities.permission_modes || [];
  capabilities.modalities = capabilities.modalities || [];
  capabilities.disconnect_policies = capabilities.disconnect_policies || [];
  capabilities.request_override_fields = capabilities.request_override_fields || {};
  capabilities.bounds = capabilities.bounds || {};
  capabilities.suggestions = capabilities.suggestions || {};
  return capabilities;
}

function _standardApiStoredValue(publication, field, fallback) {
  return publication && Object.prototype.hasOwnProperty.call(publication, field)
    ? publication[field] : fallback;
}

function _standardApiDraft(publication, state) {
  const capabilities = _standardApiCapabilities(state);
  const suggestions = publication ? {} : capabilities.suggestions;
  const suggested = function(field, fallback) {
    return Object.prototype.hasOwnProperty.call(suggestions, field)
      ? suggestions[field] : fallback;
  };
  return {
    standard_api_enabled: Boolean(_standardApiStoredValue(
      publication, 'standard_api_enabled', false)),
    api_model_id: String(_standardApiStoredValue(
      publication, 'api_model_id', '') || ''),
    api_permission_mode: String(_standardApiStoredValue(
      publication, 'api_permission_mode',
      suggested('api_permission_mode', '')) || ''),
    api_session_ttl_seconds: _standardApiStoredValue(
      publication, 'api_session_ttl_seconds',
      suggested('api_session_ttl_seconds', 0)),
    api_max_sessions_per_key: _standardApiStoredValue(
      publication, 'api_max_sessions_per_key',
      suggested('api_max_sessions_per_key', 0)),
    api_max_concurrent_runs_per_key: _standardApiStoredValue(
      publication, 'api_max_concurrent_runs_per_key',
      suggested('api_max_concurrent_runs_per_key', 0)),
    strict_fields: Boolean(_standardApiStoredValue(
      publication, 'strict_fields', suggested('strict_fields', false))),
    api_request_overrides_json: _standardApiStoredValue(
      publication, 'api_request_overrides_json',
      suggested('api_request_overrides_json', {})) || {},
    api_input_modalities_json: _standardApiStoredValue(
      publication, 'api_input_modalities_json',
      suggested('api_input_modalities_json', [])) || [],
    api_chat_completions_enabled: Boolean(_standardApiStoredValue(
      publication, 'api_chat_completions_enabled', false)),
    api_responses_enabled: Boolean(_standardApiStoredValue(
      publication, 'api_responses_enabled', false)),
    api_anthropic_messages_enabled: Boolean(_standardApiStoredValue(
      publication, 'api_anthropic_messages_enabled', false)),
    api_disconnect_policy: String(_standardApiStoredValue(
      publication, 'api_disconnect_policy',
      suggested('api_disconnect_policy', '')) || ''),
  };
}

function _standardApiErrorTarget(field) {
  return 'standardApiError_' + field;
}

function _standardApiInlineError(field) {
  return '<span id="' + _pfpAttr(_standardApiErrorTarget(field))
    + '" class="standard-api-field-error" role="alert"'
    + ' style="display:block;min-height:14px;color:var(--pf-danger);font-size:10px;"></span>';
}

function _standardApiSelectOptions(values, selected, labelPrefix) {
  return (values || []).map(function(value) {
    const suffix = String(value).replace(/_([a-z])/g, function(_m, letter) {
      return letter.toUpperCase();
    });
    const labelKey = labelPrefix + suffix.charAt(0).toUpperCase() + suffix.slice(1);
    return '<option value="' + _pfpAttr(value) + '"'
      + (value === selected ? ' selected' : '') + '>'
      + escapeHtml(t(labelKey)) + '</option>';
  }).join('');
}

function _standardApiDialectToggle(id, field, labelKey, dialect, draft, capabilities) {
  const available = capabilities.dialects[dialect] === true;
  const checked = available && draft[field];
  return '<label for="' + id + '" style="display:flex;align-items:center;gap:6px;">'
    + '<input id="' + id + '" type="checkbox"'
    + (checked ? ' checked' : '') + (available ? '' : ' disabled')
    + ' data-standard-api-dialect="' + _pfpAttr(dialect) + '"> '
    + escapeHtml(t(labelKey))
    + (available ? '' : ' <span style="color:var(--pf-muted);font-size:10px;">'
      + escapeHtml(t('standardApiUnavailableBuild')) + '</span>')
    + '</label>';
}

function _standardApiModalities(draft, capabilities) {
  return capabilities.modalities.map(function(modality) {
    const id = 'standardApiModality_' + modality;
    const mandatory = modality === 'text';
    const checked = draft.api_input_modalities_json.indexOf(modality) >= 0
      || (mandatory && draft.standard_api_enabled);
    return '<label for="' + _pfpAttr(id)
      + '" style="display:flex;align-items:center;gap:6px;">'
      + '<input id="' + _pfpAttr(id) + '" type="checkbox"'
      + ' data-standard-api-modality="' + _pfpAttr(modality) + '"'
      + (checked ? ' checked' : '') + (mandatory ? ' disabled' : '') + '> '
      + escapeHtml(modality === 'text' ? t('standardApiModalitiesText') : modality)
      + (mandatory ? ' <span style="font-size:10px;color:var(--pf-muted);">'
        + escapeHtml(t('standardApiRequired')) + '</span>' : '')
      + '</label>';
  }).join('');
}

function _standardApiDate(seconds) {
  const value = Number(seconds || 0);
  return value > 0 ? new Date(value * 1000).toLocaleString() : '';
}

function _standardApiKeyMetadata(key) {
  const parts = [];
  if (key.created_at) {
    parts.push(t('standardApiKeyCreated') + ': ' + _standardApiDate(key.created_at));
  }
  parts.push(key.last_used_at
    ? t('standardApiKeyLastUsed') + ': ' + _standardApiDate(key.last_used_at)
    : t('standardApiKeyNeverUsed'));
  return parts.join(' · ');
}

function _standardApiFieldset(publication, state) {
  const capabilities = _standardApiCapabilities(state);
  const draft = _standardApiDraft(publication, state);
  const permissionOptions = '<option value=""></option>'
    + _standardApiSelectOptions(
      capabilities.permission_modes, draft.api_permission_mode,
      'standardApiPermission');
  const disconnectOptions = '<option value=""></option>'
    + _standardApiSelectOptions(
      capabilities.disconnect_policies, draft.api_disconnect_policy,
      'standardApiDisconnect');
  const overrides = JSON.stringify(draft.api_request_overrides_json, null, 2);
  const overrideFields = Object.keys(capabilities.request_override_fields);
  const advancedHelp = overrideFields.length
    ? t('standardApiOverrideFields') + ' ' + overrideFields.join(', ')
    : t('standardApiNoOverrides');
  const generation = publication ? Number(publication.api_generation || 0) : 0;
  const runtime = publication && publication.runtime || {};
  const expanded = draft.standard_api_enabled;
  const shared = publication && publication.context_policy === 'shared';

  return '<fieldset id="standardApiFieldset" style="grid-column:1/3;border:1px solid var(--pf-border);border-radius:6px;padding:9px;margin-top:4px;">'
    + '<legend>' + escapeHtml(t('standardApiTitle')) + '</legend>'
    + '<label for="standardApiEnabled" style="display:flex;align-items:center;gap:6px;">'
    + '<input id="standardApiEnabled" type="checkbox"'
    + (draft.standard_api_enabled ? ' checked' : '')
    + (shared && !draft.standard_api_enabled ? ' disabled' : '')
    + ' onchange="_standardApiToggleEnabled()"> '
    + escapeHtml(t('standardApiEnable')) + '</label>'
    + '<div id="standardApiContextHelp" style="font-size:10px;color:var(--pf-muted);margin:3px 0;">'
    + (shared ? escapeHtml(t('standardApiRequiresIsolated')) : '') + '</div>'
    + _standardApiInlineError('standard_api_enabled')
    + '<div id="standardApiFields"' + (expanded ? '' : ' hidden') + '>'
    + '<div style="font-size:11px;color:var(--pf-muted);margin-bottom:8px;">'
    + escapeHtml(t('standardApiDisableDrainHelp')) + '</div>'
    + '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:7px;">'
    + _standardApiDialectToggle(
      'standardApiChat', 'api_chat_completions_enabled',
      'standardApiChatCompletions', 'chat_completions', draft, capabilities)
    + _standardApiDialectToggle(
      'standardApiResponses', 'api_responses_enabled',
      'standardApiResponses', 'responses', draft, capabilities)
    + _standardApiDialectToggle(
      'standardApiAnthropic', 'api_anthropic_messages_enabled',
      'standardApiAnthropicMessages', 'anthropic_messages', draft, capabilities)
    + '</div>' + _standardApiInlineError('dialects')
    + '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;margin-top:8px;">'
    + '<label for="standardApiModel">' + escapeHtml(t('standardApiModelId'))
    + '<input id="standardApiModel" value="' + _pfpAttr(draft.api_model_id)
    + '" maxlength="' + _pfpAttr(String(
      capabilities.bounds.api_model_id_max_length || 128))
    + '" style="display:block;width:100%;" aria-describedby="'
    + _standardApiErrorTarget('api_model_id') + '"></label>'
    + _standardApiInlineError('api_model_id')
    + '<label for="standardApiPermission">' + escapeHtml(t('standardApiPermissionMode'))
    + '<select id="standardApiPermission" style="display:block;width:100%;" aria-describedby="'
    + _standardApiErrorTarget('api_permission_mode') + '">'
    + permissionOptions + '</select></label>'
    + _standardApiInlineError('api_permission_mode')
    + '<label for="standardApiSessionTtl">' + escapeHtml(t('standardApiSessionTtl'))
    + '<input id="standardApiSessionTtl" type="number" step="1" value="'
    + _pfpAttr(String(draft.api_session_ttl_seconds || ''))
    + '" style="display:block;width:100%;" aria-describedby="'
    + _standardApiErrorTarget('api_session_ttl_seconds') + '"></label>'
    + _standardApiInlineError('api_session_ttl_seconds')
    + '<label for="standardApiMaxSessions">' + escapeHtml(t('standardApiMaxSessions'))
    + '<input id="standardApiMaxSessions" type="number" step="1" value="'
    + _pfpAttr(String(draft.api_max_sessions_per_key || ''))
    + '" style="display:block;width:100%;" aria-describedby="'
    + _standardApiErrorTarget('api_max_sessions_per_key') + '"></label>'
    + _standardApiInlineError('api_max_sessions_per_key')
    + '<label for="standardApiMaxConcurrent">' + escapeHtml(t('standardApiMaxConcurrent'))
    + '<input id="standardApiMaxConcurrent" type="number" step="1" value="'
    + _pfpAttr(String(draft.api_max_concurrent_runs_per_key || ''))
    + '" style="display:block;width:100%;" aria-describedby="'
    + _standardApiErrorTarget('api_max_concurrent_runs_per_key') + '"></label>'
    + _standardApiInlineError('api_max_concurrent_runs_per_key')
    + '<label for="standardApiDisconnect">' + escapeHtml(t('standardApiDisconnectPolicy'))
    + '<select id="standardApiDisconnect" style="display:block;width:100%;" aria-describedby="'
    + _standardApiErrorTarget('api_disconnect_policy') + '">'
    + disconnectOptions + '</select></label>'
    + _standardApiInlineError('api_disconnect_policy')
    + '<div style="font-size:10px;color:var(--pf-warning);">'
    + escapeHtml(t('standardApiFinishDetachedWarning')) + '</div>'
    + '</div>'
    + '<div style="margin-top:8px;"><strong>' + escapeHtml(t('standardApiInputModalities'))
    + '</strong>' + _standardApiModalities(draft, capabilities)
    + _standardApiInlineError('api_input_modalities_json') + '</div>'
    + '<label for="standardApiStrict" style="display:flex;align-items:center;gap:6px;margin-top:8px;">'
    + '<input id="standardApiStrict" type="checkbox"'
    + (draft.strict_fields ? ' checked' : '') + '> '
    + escapeHtml(t('standardApiStrictFields')) + '</label>'
    + '<div style="font-size:10px;color:var(--pf-muted);">'
    + escapeHtml(t('standardApiStrictHelp')) + '</div>'
    + '<details style="margin-top:8px;"><summary>' + escapeHtml(t('standardApiAdvanced'))
    + '</summary><div style="font-size:10px;color:var(--pf-muted);margin:4px 0;">'
    + escapeHtml(advancedHelp) + '</div>'
    + '<label for="standardApiOverrides">' + escapeHtml(t('standardApiRequestOverrides'))
    + '<textarea id="standardApiOverrides" rows="3" style="display:block;width:100%;font-family:monospace;"'
    + (overrideFields.length ? '' : ' disabled') + ' aria-describedby="'
    + _standardApiErrorTarget('api_request_overrides_json') + '">'
    + escapeHtml(overrides) + '</textarea></label>'
    + _standardApiInlineError('api_request_overrides_json') + '</details>'
    + '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:9px;">'
    + '<span>' + escapeHtml(t('standardApiGeneration')) + ': <strong>'
    + escapeHtml(String(generation)) + '</strong></span>'
    + '<span>' + escapeHtml(t('standardApiSessions')) + ': '
    + escapeHtml(String(runtime.session_count || 0)) + '</span>'
    + '<span>' + escapeHtml(t('standardApiActiveRuns')) + ': '
    + escapeHtml(String(runtime.active_run_count || 0)) + '</span>'
    + ((runtime.draining_generations || []).length
      ? '<span>' + escapeHtml(t('standardApiDrainingGenerations')) + ': '
        + escapeHtml(runtime.draining_generations.join(', ')) + '</span>' : '')
    + (publication && generation
      ? '<button type="button" onclick="_standardApiResetSessions('
        + _pfpJsArg(publication.publication_id) + ')">'
        + escapeHtml(t('standardApiResetSessions')) + '</button>' : '')
    + '</div>'
    + _standardApiInlineError('general')
    + '</div></fieldset>';
}

function _standardApiIntegerValue(id) {
  const input = document.getElementById(id);
  const raw = input ? String(input.value || '').trim() : '';
  if (!raw) return 0;
  return /^\d+$/.test(raw) ? Number.parseInt(raw, 10) : NaN;
}

function _standardApiCollectPayload() {
  const overridesInput = document.getElementById('standardApiOverrides');
  let overrides = {};
  try {
    overrides = overridesInput && overridesInput.value.trim()
      ? JSON.parse(overridesInput.value) : {};
  } catch (_error) {
    overrides = null;
  }
  const modalities = Array.from(document.querySelectorAll(
    '[data-standard-api-modality]')).filter(function(input) {
      return input.checked;
    }).map(function(input) {
      return input.getAttribute('data-standard-api-modality');
    });
  return {
    standard_api_enabled: document.getElementById('standardApiEnabled').checked,
    api_model_id: document.getElementById('standardApiModel').value.trim(),
    api_permission_mode: document.getElementById('standardApiPermission').value,
    api_session_ttl_seconds: _standardApiIntegerValue('standardApiSessionTtl'),
    api_max_sessions_per_key: _standardApiIntegerValue('standardApiMaxSessions'),
    api_max_concurrent_runs_per_key: _standardApiIntegerValue('standardApiMaxConcurrent'),
    strict_fields: document.getElementById('standardApiStrict').checked,
    api_request_overrides_json: overrides,
    api_input_modalities_json: modalities,
    api_chat_completions_enabled: document.getElementById('standardApiChat').checked,
    api_responses_enabled: document.getElementById('standardApiResponses').checked,
    api_anthropic_messages_enabled: document.getElementById('standardApiAnthropic').checked,
    api_disconnect_policy: document.getElementById('standardApiDisconnect').value,
  };
}

function _standardApiValidatePayload(payload, contextPolicy, capabilities) {
  const errors = {};
  const bounds = capabilities.bounds || {};
  const dialectFields = {
    chat_completions: 'api_chat_completions_enabled',
    responses: 'api_responses_enabled',
    anthropic_messages: 'api_anthropic_messages_enabled',
  };
  Object.keys(dialectFields).forEach(function(dialect) {
    if (payload[dialectFields[dialect]] && capabilities.dialects[dialect] !== true) {
      errors.dialects = t('standardApiUnavailableSelection');
    }
  });
  const integerFields = [
    'api_session_ttl_seconds',
    'api_max_sessions_per_key',
    'api_max_concurrent_runs_per_key',
  ];
  integerFields.forEach(function(field) {
    const value = payload[field];
    const range = bounds[field] || {};
    if (!Number.isInteger(value)
        || (value !== 0 && (value < range.min || value > range.max))) {
      errors[field] = t('standardApiIntegerRange') + ' '
        + String(range.min || '') + '-' + String(range.max || '');
    }
  });
  if (payload.api_request_overrides_json === null
      || typeof payload.api_request_overrides_json !== 'object'
      || Array.isArray(payload.api_request_overrides_json)) {
    errors.api_request_overrides_json = t('standardApiOverridesJson');
  } else {
    const allowedOverrides = capabilities.request_override_fields || {};
    if (Object.keys(payload.api_request_overrides_json).some(function(field) {
      return !Object.prototype.hasOwnProperty.call(allowedOverrides, field);
    })) {
      errors.api_request_overrides_json = t('standardApiUnsupportedOverride');
    }
  }
  if (payload.standard_api_enabled) {
    if (contextPolicy !== 'isolated') {
      errors.standard_api_enabled = t('standardApiRequiresIsolated');
    }
    const maxModelLength = Number(bounds.api_model_id_max_length || 128);
    if (!payload.api_model_id
        || payload.api_model_id.length > maxModelLength
        || !/^[A-Za-z0-9][A-Za-z0-9._:/-]*$/.test(payload.api_model_id)) {
      errors.api_model_id = t('standardApiModelInvalid');
    }
    if ((capabilities.permission_modes || []).indexOf(
      payload.api_permission_mode) < 0) {
      errors.api_permission_mode = t('standardApiPermissionRequired');
    }
    integerFields.forEach(function(field) {
      if (!payload[field] && !errors[field]) {
        errors[field] = t('standardApiRequired');
      }
    });
    if (!Object.keys(dialectFields).some(function(dialect) {
      return capabilities.dialects[dialect] === true
        && payload[dialectFields[dialect]];
    })) {
      errors.dialects = t('standardApiSelectDialect');
    }
    if (payload.api_input_modalities_json.indexOf('text') < 0) {
      errors.api_input_modalities_json = t('standardApiTextRequired');
    }
    if ((capabilities.disconnect_policies || []).indexOf(
      payload.api_disconnect_policy) < 0) {
      errors.api_disconnect_policy = t('standardApiDisconnectRequired');
    }
  }
  return errors;
}

function _standardApiShowErrors(errors) {
  document.querySelectorAll('.standard-api-field-error').forEach(function(node) {
    node.textContent = '';
  });
  document.querySelectorAll('#standardApiFieldset [aria-invalid="true"]').forEach(
    function(node) { node.removeAttribute('aria-invalid'); });
  Object.keys(errors || {}).forEach(function(field) {
    const target = document.getElementById(_standardApiErrorTarget(field));
    if (target) target.textContent = errors[field];
    const described = document.querySelector(
      '#standardApiFieldset [aria-describedby="' + _standardApiErrorTarget(field) + '"]');
    if (described) described.setAttribute('aria-invalid', 'true');
  });
  const first = document.querySelector('#standardApiFieldset [aria-invalid="true"]');
  if (first) first.focus();
}

function _standardApiShowServerError(message) {
  const text = String(message || t('standardApiSaveFailed'));
  const field = _STANDARD_API_MATERIAL_FIELDS.find(function(name) {
    return text.indexOf(name) >= 0;
  });
  const errors = {};
  errors[field || 'general'] = text;
  _standardApiShowErrors(errors);
}

function _standardApiToggleEnabled() {
  const checkbox = document.getElementById('standardApiEnabled');
  const policy = document.getElementById('a2aPubPolicy');
  if (!checkbox || !policy) return;
  if (checkbox.checked && policy.value !== 'isolated') {
    checkbox.checked = false;
    _standardApiShowErrors({
      standard_api_enabled: t('standardApiRequiresIsolated'),
    });
  }
  const textModality = document.querySelector(
    '[data-standard-api-modality="text"]');
  if (checkbox.checked && textModality) textModality.checked = true;
  const fields = document.getElementById('standardApiFields');
  if (fields) fields.hidden = !checkbox.checked;
}

function _standardApiContextPolicyChanged() {
  const checkbox = document.getElementById('standardApiEnabled');
  const policy = document.getElementById('a2aPubPolicy');
  const help = document.getElementById('standardApiContextHelp');
  if (!checkbox || !policy) return;
  if (policy.value !== 'isolated' && checkbox.checked) {
    policy.value = 'isolated';
    _standardApiShowErrors({
      standard_api_enabled: t('standardApiDisableBeforeShared'),
    });
  }
  checkbox.disabled = policy.value !== 'isolated';
  if (help) {
    help.textContent = policy.value === 'isolated'
      ? '' : t('standardApiRequiresIsolated');
  }
}

function _standardApiConfirmSave(publication, payload, options) {
  if (!publication) return true;
  const disabling = (publication.enabled && !options.publicationEnabled)
    || (publication.standard_api_enabled && !payload.standard_api_enabled)
    || (publication.api_chat_completions_enabled
      && !payload.api_chat_completions_enabled)
    || (publication.api_responses_enabled && !payload.api_responses_enabled)
    || (publication.api_anthropic_messages_enabled
      && !payload.api_anthropic_messages_enabled);
  if (disabling) return confirm(t('standardApiConfirmDisable'));
  const material = _STANDARD_API_MATERIAL_FIELDS.some(function(field) {
    return JSON.stringify(publication[field]) !== JSON.stringify(payload[field]);
  }) || publication.context_policy !== options.contextPolicy
    || Boolean(publication.enabled) !== options.publicationEnabled;
  return !(publication.standard_api_enabled && material)
    || confirm(t('standardApiConfirmSessionReset'));
}

function _standardApiBadge(label, state) {
  const color = state === 'active' ? 'var(--pf-accent)'
    : state === 'unavailable' ? 'var(--pf-muted)' : 'var(--pf-warning)';
  return '<span style="border:1px solid ' + color
    + ';border-radius:999px;padding:1px 6px;font-size:10px;color:' + color + ';">'
    + escapeHtml(label) + '</span>';
}

function _standardApiDialectState(publication, state, dialect, field) {
  const capabilities = _standardApiCapabilities(state);
  if (capabilities.dialects[dialect] !== true) return 'unavailable';
  if (!publication[field]) return 'disabled';
  if (!publication.enabled || !publication.standard_api_enabled) return 'configured';
  return publication.runtime && publication.runtime.dialects
    && publication.runtime.dialects[dialect] ? 'active' : 'configured';
}

function _standardApiStateLabel(state) {
  if (state === 'active') return t('standardApiActive');
  if (state === 'unavailable') return t('standardApiUnavailableBuild');
  if (state === 'configured') return t('standardApiConfiguredDisabled');
  return t('standardApiDisabled');
}

function _standardApiPublicationStatus(publication, state) {
  const runtime = publication.runtime || {};
  const globalState = runtime.publication_enabled ? 'active' : 'configured';
  const rows = [
    _standardApiBadge(t('standardApiA2aTransport') + ': '
      + _standardApiStateLabel(globalState), globalState),
    _standardApiBadge(t('standardApiAguiTransport') + ': '
      + _standardApiStateLabel(globalState), globalState),
  ];
  [
    ['chat_completions', 'api_chat_completions_enabled',
      t('standardApiChatCompletions')],
    ['responses', 'api_responses_enabled', t('standardApiResponses')],
    ['anthropic_messages', 'api_anthropic_messages_enabled',
      t('standardApiAnthropicMessages')],
  ].forEach(function(item) {
    const dialectState = _standardApiDialectState(
      publication, state, item[0], item[1]);
    rows.push(_standardApiBadge(
      item[2] + ': ' + _standardApiStateLabel(dialectState), dialectState));
  });
  let detail = '<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:6px;">'
    + rows.join('') + '</div>'
    + '<div style="font-size:10px;color:var(--pf-muted);margin-top:4px;">'
    + escapeHtml(t('standardApiPermissionMode')) + ': '
    + escapeHtml(publication.api_permission_mode || '-')
    + ' · ' + escapeHtml(t('standardApiModelId')) + ': '
    + escapeHtml(publication.api_model_id || '-')
    + ' · ' + escapeHtml(t('standardApiGeneration')) + ': '
    + escapeHtml(String(publication.api_generation || 0)) + '</div>';
  if (publication.standard_api_enabled && !(runtime.live_key_count || 0)) {
    detail += '<div style="font-size:10px;color:var(--pf-warning);">'
      + escapeHtml(t('standardApiEnabledNoKey')) + '</div>';
  }
  if (runtime.deleting) {
    detail += '<div style="font-size:10px;color:var(--pf-danger);">'
      + escapeHtml(t('standardApiDeleting')) + '</div>';
  }
  if ((runtime.draining_generations || []).length) {
    detail += '<div style="font-size:10px;color:var(--pf-muted);">'
      + escapeHtml(t('standardApiDrainingGenerations')) + ': '
      + escapeHtml(runtime.draining_generations.join(', ')) + '</div>';
  }
  return detail;
}

function _standardApiOpenAiBase(publicationId) {
  return new URL('/openai/' + encodeURIComponent(publicationId) + '/v1',
    window.location.origin).href;
}

function _standardApiAnthropicBase(publicationId) {
  return new URL('/anthropic/' + encodeURIComponent(publicationId),
    window.location.origin).href;
}

function _standardApiConfiguredSnippet(publication, dialect, streaming, kind) {
  dialect = dialect || (
    publication.api_chat_completions_enabled ? 'chat_completions'
      : publication.api_responses_enabled ? 'responses'
        : publication.api_anthropic_messages_enabled
          ? 'anthropic_messages' : '');
  kind = kind || 'python';
  const model = publication.api_model_id || 'PUBLISHED_MODEL_ID';
  const openaiBase = _standardApiOpenAiBase(publication.publication_id);
  const anthropicBase = _standardApiAnthropicBase(publication.publication_id);
  if (kind === 'curl' && dialect === 'chat_completions') {
    return 'curl ' + JSON.stringify(openaiBase + '/chat/completions')
      + ' -H "Authorization: Bearer $PAWFLOW_API_KEY"'
      + ' -H "Content-Type: application/json"'
      + ' -d ' + JSON.stringify(JSON.stringify({
        model: model,
        messages: [{role: 'user', content: 'Hello'}],
        stream: Boolean(streaming),
      }));
  }
  if (kind === 'curl' && dialect === 'responses') {
    return 'curl ' + JSON.stringify(openaiBase + '/responses')
      + ' -H "Authorization: Bearer $PAWFLOW_API_KEY"'
      + ' -H "Content-Type: application/json"'
      + ' -d ' + JSON.stringify(JSON.stringify({
        model: model, input: 'Hello', stream: Boolean(streaming),
      }));
  }
  if (kind === 'curl' && dialect === 'anthropic_messages') {
    return 'curl ' + JSON.stringify(anthropicBase + '/v1/messages')
      + ' -H "x-api-key: $PAWFLOW_API_KEY"'
      + ' -H "anthropic-version: 2023-06-01"'
      + ' -H "Content-Type: application/json"'
      + ' -d ' + JSON.stringify(JSON.stringify({
        model: model,
        max_tokens: 1024,
        messages: [{role: 'user', content: 'Hello'}],
        stream: Boolean(streaming),
      }));
  }
  if (dialect === 'chat_completions') {
    return 'import os\nfrom openai import OpenAI\n\n'
      + 'client = OpenAI(api_key=os.environ["PAWFLOW_API_KEY"], base_url='
      + JSON.stringify(openaiBase) + ')\n'
      + (streaming
        ? 'stream = client.chat.completions.create(model=' + JSON.stringify(model)
          + ', messages=[{"role": "user", "content": "Hello"}], stream=True)\n'
          + 'for chunk in stream:\n    print(chunk.choices[0].delta.content or "", end="")'
        : 'completion = client.chat.completions.create(model='
          + JSON.stringify(model)
          + ', messages=[{"role": "user", "content": "Hello"}])\n'
          + 'print(completion.choices[0].message.content)');
  }
  if (dialect === 'responses') {
    return 'import os\nfrom openai import OpenAI\n\n'
      + 'client = OpenAI(api_key=os.environ["PAWFLOW_API_KEY"], base_url='
      + JSON.stringify(openaiBase) + ')\n'
      + (streaming
        ? 'with client.responses.stream(model=' + JSON.stringify(model)
          + ', input="Hello") as stream:\n    for event in stream:\n        print(event)'
        : 'response = client.responses.create(model=' + JSON.stringify(model)
          + ', input="Hello")\nprint(response.output_text)');
  }
  if (dialect === 'anthropic_messages') {
    return 'import os\nfrom anthropic import Anthropic\n\n'
      + 'client = Anthropic(api_key=os.environ["PAWFLOW_API_KEY"], base_url='
      + JSON.stringify(anthropicBase) + ')\n'
      + (streaming
        ? 'with client.messages.stream(model=' + JSON.stringify(model)
          + ', max_tokens=1024, messages=[{"role": "user", "content": "Hello"}]) as stream:\n'
          + '    for text in stream.text_stream:\n        print(text, end="")'
        : 'message = client.messages.create(model=' + JSON.stringify(model)
          + ', max_tokens=1024, messages=[{"role": "user", "content": "Hello"}])\n'
          + 'print(message.content[0].text)');
  }
  return '';
}

function _standardApiSnippetBlock(publication, dialect, label) {
  const python = _standardApiConfiguredSnippet(publication, dialect, false, 'python');
  const streaming = _standardApiConfiguredSnippet(publication, dialect, true, 'python');
  const curl = _standardApiConfiguredSnippet(publication, dialect, false, 'curl');
  return '<details style="margin-top:6px;"><summary>' + escapeHtml(label)
    + ' · ' + escapeHtml(t('standardApiSdkSnippets')) + '</summary>'
    + '<div><strong>' + escapeHtml(t('standardApiPython')) + '</strong>'
    + '<button type="button" onclick="_standardApiCopySnippet('
    + _pfpJsArg(publication.publication_id) + ',' + _pfpJsArg(dialect)
    + ',false,' + _pfpJsArg('python') + ')">' + escapeHtml(t('standardApiCopySnippet'))
    + '</button><pre style="white-space:pre-wrap;font-size:10px;">'
    + escapeHtml(python) + '</pre></div>'
    + '<div><strong>' + escapeHtml(t('standardApiPythonStreaming')) + '</strong>'
    + '<button type="button" onclick="_standardApiCopySnippet('
    + _pfpJsArg(publication.publication_id) + ',' + _pfpJsArg(dialect)
    + ',true,' + _pfpJsArg('python') + ')">' + escapeHtml(t('standardApiCopySnippet'))
    + '</button><pre style="white-space:pre-wrap;font-size:10px;">'
    + escapeHtml(streaming) + '</pre></div>'
    + '<div><strong>curl</strong>'
    + '<button type="button" onclick="_standardApiCopySnippet('
    + _pfpJsArg(publication.publication_id) + ',' + _pfpJsArg(dialect)
    + ',false,' + _pfpJsArg('curl') + ')">' + escapeHtml(t('standardApiCopySnippet'))
    + '</button><pre style="white-space:pre-wrap;font-size:10px;">'
    + escapeHtml(curl) + '</pre></div></details>';
}

function _standardApiTransportPanel(publication, state) {
  const capabilities = _standardApiCapabilities(state);
  const openaiBase = _standardApiOpenAiBase(publication.publication_id);
  const anthropicBase = _standardApiAnthropicBase(publication.publication_id);
  let snippets = '';
  if (capabilities.dialects.chat_completions
      && publication.api_chat_completions_enabled) {
    snippets += _standardApiSnippetBlock(
      publication, 'chat_completions', t('standardApiChatCompletions'));
  }
  if (capabilities.dialects.responses && publication.api_responses_enabled) {
    snippets += _standardApiSnippetBlock(
      publication, 'responses', t('standardApiResponses'));
  }
  if (capabilities.dialects.anthropic_messages
      && publication.api_anthropic_messages_enabled) {
    snippets += _standardApiSnippetBlock(
      publication, 'anthropic_messages', t('standardApiAnthropicMessages'));
  }
  return '<details style="margin-top:7px;"><summary>'
    + escapeHtml(t('standardApiEndpointsAndSnippets')) + '</summary>'
    + '<div style="display:grid;gap:5px;margin-top:5px;">'
    + '<div style="display:flex;gap:5px;align-items:center;"><label style="flex:1;">'
    + escapeHtml(t('standardApiOpenAiBaseUrl'))
    + '<input readonly value="' + _pfpAttr(openaiBase)
    + '" style="display:block;width:100%;font-size:10px;"></label>'
    + '<button type="button" onclick="_a2aCopyValue(' + _pfpJsArg(openaiBase)
    + ')">' + escapeHtml(t('standardApiCopyBaseUrl')) + '</button></div>'
    + '<div style="display:flex;gap:5px;align-items:center;"><label style="flex:1;">'
    + escapeHtml(t('standardApiAnthropicBaseUrl'))
    + '<input readonly value="' + _pfpAttr(anthropicBase)
    + '" style="display:block;width:100%;font-size:10px;"></label>'
    + '<button type="button" onclick="_a2aCopyValue(' + _pfpJsArg(anthropicBase)
    + ')">' + escapeHtml(t('standardApiCopyBaseUrl')) + '</button></div>'
    + '<div style="display:flex;gap:5px;align-items:center;"><label style="flex:1;">'
    + escapeHtml(t('standardApiModelId'))
    + '<input readonly value="' + _pfpAttr(publication.api_model_id || '')
    + '" style="display:block;width:100%;font-size:10px;"></label>'
    + '<button type="button" onclick="_a2aCopyValue('
    + _pfpJsArg(publication.api_model_id || '') + ')">'
    + escapeHtml(t('standardApiCopyModel')) + '</button></div></div>'
    + snippets + '</details>';
}

function _standardApiCopySnippet(publicationId, dialect, streaming, kind) {
  const publication = ((_a2aState && _a2aState.publications) || []).find(
    function(row) { return row.publication_id === publicationId; });
  if (!publication) return;
  _a2aCopyValue(_standardApiConfiguredSnippet(
    publication, dialect, streaming, kind));
}

function _standardApiResetSessions(publicationId) {
  if (!confirm(t('standardApiConfirmResetSessions'))) return;
  _a2aAction('a2a_publication_reset_api_sessions', {
    publication_id: publicationId,
  }, function() {
    _a2aRefresh();
  }, _standardApiShowServerError);
}
