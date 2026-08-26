// Workflow-agent binding form shared by add/configuration dialogs.
// Depends on schema_form.js and is invoked only after the deferred UI modules load.

function _workflowAgentSchema(workflow) {
  const schema = {};
  Object.entries((workflow || {}).parameters || {}).forEach(([name, raw]) => {
    const spec = Object.assign({}, raw);
    if (spec.type === 'number') spec.type = 'float';
    if (spec.type === 'array') spec.type = 'json';
    if (spec.type === 'service_ref') spec.service_type = spec.capability || '';
    schema[name] = spec;
  });
  return schema;
}

function mountWorkflowAgentForm(root, workflows, binding, options) {
  workflows = workflows || [];
  binding = binding || {};
  options = options || {};
  const prefix = options.prefix || 'wf';
  const selectId = prefix + '-flow';
  const fieldsId = prefix + '-fields';
  const originalFqn = binding.flow_fqn || '';
  root.innerHTML = '<label for="' + selectId + '" style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('workflowExactFlow')) + '</label>'
    + '<select id="' + selectId + '" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin:2px 0 8px;">'
    + '<option value="">' + escapeHtml(t('workflowSelectFlow')) + '</option>'
    + workflows.map(w => '<option value="' + escapeHtml(w.flow_fqn) + '"' + (w.flow_fqn === originalFqn ? ' selected' : '') + '>'
      + escapeHtml(w.flow_fqn + ' [' + w.scope + ']') + '</option>').join('')
    + '</select><div id="' + fieldsId + '"></div>';
  const select = root.querySelector('#' + CSS.escape(selectId));
  const fields = root.querySelector('#' + CSS.escape(fieldsId));

  function selectedWorkflow() {
    return workflows.find(w => w.flow_fqn === select.value);
  }

  function renderFields() {
    const workflow = selectedWorkflow();
    if (!workflow) { fields.innerHTML = ''; return; }
    const sameBinding = workflow.flow_fqn === originalFqn;
    const values = sameBinding ? (binding.parameters || {}) : {};
    const limits = sameBinding ? (binding.limits || {}) : {};
    const policies = workflow.supported_preempt_policies || [];
    const policy = sameBinding ? binding.preempt_policy : policies[0];
    fields.innerHTML = '<div style="font-size:11px;color:var(--pf-accent);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('workflowContractParameters')) + '</div>'
      + PawFlowSchemaForm.renderSchemaFields(_workflowAgentSchema(workflow), values)
      + '<label for="' + prefix + '-preempt" style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('workflowPreemptPolicy')) + '</label>'
      + '<select id="' + prefix + '-preempt" style="width:100%;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);padding:6px;border-radius:4px;margin:2px 0 8px;">'
      + policies.map(p => '<option value="' + escapeHtml(p) + '"' + (p === policy ? ' selected' : '') + '>' + escapeHtml(p) + '</option>').join('') + '</select>'
      + '<div style="font-size:11px;color:var(--pf-accent);margin-bottom:6px;font-weight:600;">' + escapeHtml(t('workflowLimits')) + '</div>'
      + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">'
      + _workflowLimitField(prefix + '-duration', 'workflowMaxDuration', limits.max_duration_seconds || 0)
      + _workflowLimitField(prefix + '-llm-calls', 'workflowMaxLlmCalls', limits.max_llm_calls || 0)
      + _workflowLimitField(prefix + '-flowfiles', 'workflowMaxFlowFiles', limits.max_flowfiles || 0)
      + _workflowLimitField(prefix + '-fanout', 'workflowMaxFanout', limits.max_fanout || 0)
      + '</div>';
    PawFlowSchemaForm.populateServiceRefs(fields);
  }

  select.onchange = function() {
    renderFields();
    if (options.onSelectionChange) options.onSelectionChange(select.value, originalFqn);
  };
  renderFields();

  return {
    isUpgrade: () => !!originalFqn && select.value !== originalFqn,
    getBinding: function() {
      const workflow = selectedWorkflow();
      if (!workflow) throw new Error(t('workflowFlowRequiredMessage'));
      const schema = _workflowAgentSchema(workflow);
      const parameters = PawFlowSchemaForm.collectSchemaValues(schema, fields);
      const missing = Object.keys(schema).filter(name => schema[name].required && (parameters[name] === '' || parameters[name] == null));
      if (missing.length) throw new Error(t('workflowRequiredParameters', { names: missing.join(', ') }));
      const limits = {
        max_duration_seconds: parseInt(root.querySelector('#' + CSS.escape(prefix + '-duration')).value, 10),
        max_llm_calls: parseInt(root.querySelector('#' + CSS.escape(prefix + '-llm-calls')).value, 10),
        max_flowfiles: parseInt(root.querySelector('#' + CSS.escape(prefix + '-flowfiles')).value, 10),
        max_fanout: parseInt(root.querySelector('#' + CSS.escape(prefix + '-fanout')).value, 10),
      };
      if (Object.values(limits).some(value => !Number.isInteger(value) || value < 0)) throw new Error(t('workflowPositiveLimits'));
      return {
        flow_fqn: workflow.flow_fqn,
        input_port: workflow.input_port,
        terminal_port: workflow.terminal_port,
        preempt_policy: root.querySelector('#' + CSS.escape(prefix + '-preempt')).value,
        allowed_effects: workflow.allowed_effects,
        parameters,
        limits,
      };
    },
  };
}

function _workflowLimitField(id, labelKey, value) {
  return '<label style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t(labelKey))
    + '<input id="' + id + '" type="number" min="0" value="' + escapeHtml(value) + '" style="' + _svcInputStyle + '"></label>';
}
