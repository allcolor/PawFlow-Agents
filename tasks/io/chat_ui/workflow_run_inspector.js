// Redacted workflow-agent run inspector and safe recovery controls.

function _workflowRunDate(value) {
  if (!value) return '\u2014';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function _workflowRunUsageHtml(usage) {
  const rows = Object.entries(usage || {});
  if (!rows.length) return '<span style="color:var(--pf-muted);">\u2014</span>';
  return rows.map(function(entry) {
    return '<span style="display:inline-block;margin:2px 6px 2px 0;padding:2px 5px;border-radius:3px;background:var(--pf-sidebar);">'
      + escapeHtml(entry[0].replaceAll('_', ' ')) + ': ' + escapeHtml(String(entry[1])) + '</span>';
  }).join('');
}

function _workflowRunGroupMetaHtml(data) {
  const keys = [
    'group_name', 'member_id', 'round', 'rounds', 'disposition', 'stop_reason',
    'member_count', 'participant_calls', 'max_rounds', 'max_tokens', 'tokens',
    'cost', 'confidence', 'required',
  ];
  const rows = keys.filter(function(key) {
    return data[key] !== undefined && data[key] !== null && data[key] !== '';
  });
  if (!rows.length) return '';
  return '<div style="font-size:10px;margin-top:3px;">' + rows.map(function(key) {
    return '<span style="display:inline-block;margin:2px 6px 2px 0;padding:2px 5px;border-radius:3px;background:var(--pf-bg);">'
      + escapeHtml(key.replaceAll('_', ' ')) + ': ' + escapeHtml(String(data[key])) + '</span>';
  }).join('') + '</div>';
}

function _workflowRunCitationsHtml(citations) {
  if (!Array.isArray(citations) || !citations.length) return '';
  return '<div style="color:var(--pf-muted);font-size:10px;margin-top:3px;">'
    + citations.map(function(citation) {
      return [citation.label || citation.title, citation.url].filter(Boolean)
        .map(String).join(' \u00b7 ');
    }).filter(Boolean).map(escapeHtml).join('<br>') + '</div>';
}

function _workflowRunStructuredValueHtml(value, depth) {
  const level = Number(depth) || 0;
  if (Array.isArray(value)) {
    if (!value.length) return '<span style="color:var(--pf-muted);">\u2014</span>';
    return '<ol style="margin:4px 0 4px 18px;padding:0;">' + value.map(function(item) {
      return '<li style="margin:5px 0;">' + _workflowRunStructuredValueHtml(item, level + 1) + '</li>';
    }).join('') + '</ol>';
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value);
    if (!entries.length) return '<span style="color:var(--pf-muted);">\u2014</span>';
    return '<dl style="margin:4px 0;display:grid;grid-template-columns:minmax(110px,0.35fr) minmax(0,1fr);gap:5px 9px;">'
      + entries.map(function(entry) {
        return '<dt style="color:var(--pf-muted);font-weight:700;overflow-wrap:anywhere;">'
          + escapeHtml(entry[0].replaceAll('_', ' ')) + '</dt>'
          + '<dd style="margin:0;min-width:0;overflow-wrap:anywhere;">'
          + _workflowRunStructuredValueHtml(entry[1], level + 1) + '</dd>';
      }).join('') + '</dl>';
  }
  if (value === null || value === undefined || value === '') {
    return '<span style="color:var(--pf-muted);">\u2014</span>';
  }
  return '<span style="white-space:pre-wrap;overflow-wrap:anywhere;">'
    + escapeHtml(String(value)) + '</span>';
}

function _workflowRunMessageValueHtml(data) {
  if (data.structured_content) {
    return _workflowRunStructuredValueHtml(data.structured_content);
  }
  const value = data.content || data.reason || '';
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      return _workflowRunStructuredValueHtml(JSON.parse(trimmed));
    } catch (_error) {
      return '<span>' + escapeHtml(t('workflowRunStructuredIncomplete')) + '</span>';
    }
  }
  return '<span style="white-space:pre-wrap;">' + escapeHtml(String(value)) + '</span>';
}

function _workflowRunStageHtml(event) {
  const data = event.data || {};
  const label = data.label
    || (event.event_type === 'agent_message' && data.role
      ? data.role + (data.model ? ' \u00b7 ' + data.model : '') : '')
    || data.tool_name
    || data.task_id || data.task_type || event.event_type || '\u2014';
  const detail = [data.stage, data.phase, data.tool_name, data.outcome, data.service_id]
    .filter(Boolean).join(' \u00b7 ');
  return '<li style="list-style:none;position:relative;padding:7px 9px;margin:0 0 6px;border:1px solid var(--pf-border);border-radius:5px;background:var(--pf-sidebar);">'
    + '<div style="display:flex;justify-content:space-between;gap:8px;">'
    + '<strong style="color:var(--pf-text);font-size:12px;">' + escapeHtml(label) + '</strong>'
    + '<span style="color:var(--pf-muted);font-size:10px;">'
    + (data.iteration ? 'iter ' + escapeHtml(String(data.iteration)) + ' \u00b7 ' : '')
    + '#' + escapeHtml(String(event.sequence || 0)) + '</span></div>'
    + (detail ? '<div style="color:var(--pf-muted);font-size:11px;margin-top:2px;">' + escapeHtml(detail) + '</div>' : '')
    + (data.reason ? '<div style="color:var(--pf-warning,#d29922);font-size:11px;margin-top:2px;">' + escapeHtml(data.reason) + '</div>' : '')
    + _workflowRunGroupMetaHtml(data)
    + (data.arguments ? '<div style="color:var(--pf-text);font-size:10px;margin:5px 0 0;padding:6px;background:var(--pf-bg);border-radius:4px;">'
      + _workflowRunStructuredValueHtml(data.arguments) + '</div>' : '')
    + (data.structured_content || data.content ? '<div style="color:var(--pf-text);font-size:11px;margin-top:5px;padding:7px;background:var(--pf-bg);border-radius:4px;">'
      + _workflowRunMessageValueHtml(data) + '</div>' : '')
    + _workflowRunCitationsHtml(data.citations)
    + '<div style="font-size:10px;margin-top:3px;">' + _workflowRunUsageHtml(data.usage || data.token_usage) + '</div></li>';
}

function _workflowFlowHtml(run) {
  const graph = run.flow_graph || {};
  const tasks = Array.isArray(graph.tasks) ? graph.tasks : [];
  if (!tasks.length) return '';
  const relations = Array.isArray(graph.relations) ? graph.relations : [];
  const direction = graph.direction || 'LR';
  const fallbackHorizontal = direction === 'LR' || direction === 'RL';
  const positioned = tasks.map(function(task, index) {
    const width = Math.max(120, Number(task.width) || 190);
    const height = Math.max(58, Number(task.height) || 72);
    let x = Number(task.x);
    let y = Number(task.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      x = fallbackHorizontal ? index * 240 : 0;
      y = fallbackHorizontal ? 0 : index * 120;
    }
    return Object.assign({}, task, { x, y, width, height });
  });
  const byId = new Map(positioned.map(function(task) { return [task.id, task]; }));
  const minX = Math.min.apply(null, positioned.map(function(task) { return task.x; }));
  const minY = Math.min.apply(null, positioned.map(function(task) { return task.y; }));
  const maxX = Math.max.apply(null, positioned.map(function(task) { return task.x + task.width; }));
  const maxY = Math.max.apply(null, positioned.map(function(task) { return task.y + task.height; }));
  const pad = 32;
  const viewX = minX - pad;
  const viewY = minY - pad;
  const viewWidth = Math.max(240, maxX - minX + pad * 2);
  const viewHeight = Math.max(130, maxY - minY + pad * 2);
  const edgeHtml = relations.map(function(relation) {
    const source = byId.get(relation.from);
    const target = byId.get(relation.to);
    if (!source || !target) return '';
    const horizontal = Math.abs(target.x - source.x) >= Math.abs(target.y - source.y);
    const sx = horizontal
      ? source.x + (target.x >= source.x ? source.width : 0)
      : source.x + source.width / 2;
    const sy = horizontal
      ? source.y + source.height / 2
      : source.y + (target.y >= source.y ? source.height : 0);
    const tx = horizontal
      ? target.x + (target.x >= source.x ? 0 : target.width)
      : target.x + target.width / 2;
    const ty = horizontal
      ? target.y + target.height / 2
      : target.y + (target.y >= source.y ? 0 : target.height);
    const c1x = horizontal ? (sx + tx) / 2 : sx;
    const c1y = horizontal ? sy : (sy + ty) / 2;
    const c2x = horizontal ? (sx + tx) / 2 : tx;
    const c2y = horizontal ? ty : (sy + ty) / 2;
    return '<path d="M ' + sx + ' ' + sy + ' C ' + c1x + ' ' + c1y + ', '
      + c2x + ' ' + c2y + ', ' + tx + ' ' + ty
      + '" fill="none" stroke="var(--pf-muted)" stroke-width="2" marker-end="url(#workflow-run-arrow)" />';
  }).join('');
  const nodeHtml = positioned.map(function(task) {
    const status = task.status || 'pending';
    const running = status === 'running';
    const stroke = running ? 'var(--pf-accent)'
      : (status === 'completed' ? 'var(--pf-success)'
        : (status === 'failed' ? 'var(--pf-danger)' : 'var(--pf-border)'));
    const fill = running
      ? 'color-mix(in srgb,var(--pf-accent) 22%,var(--pf-sidebar))'
      : 'var(--pf-sidebar)';
    const label = String(task.label || task.id);
    const shortLabel = label.length > 30 ? label.slice(0, 29) + '\u2026' : label;
    return '<g data-flow-task="' + _pfpAttr(task.id) + '" data-flow-status="'
      + _pfpAttr(status) + '" transform="translate(' + task.x + ' ' + task.y + ')">'
      + '<title>' + escapeHtml(label + ' \u2014 ' + status) + '</title>'
      + '<rect width="' + task.width + '" height="' + task.height
      + '" rx="8" fill="' + fill + '" stroke="' + stroke
      + '" stroke-width="' + (running ? 3 : 2) + '" />'
      + '<text x="12" y="27" fill="var(--pf-text)" font-size="13" font-weight="700">'
      + escapeHtml(shortLabel) + '</text>'
      + '<text x="12" y="48" fill="var(--pf-muted)" font-size="10">'
      + escapeHtml(task.type || '') + '</text>'
      + (running ? '<circle cx="' + (task.width - 15)
        + '" cy="15" r="6" fill="var(--pf-accent)"><animate attributeName="opacity" values="1;.35;1" dur="1.2s" repeatCount="indefinite" /></circle>' : '')
      + '</g>';
  }).join('');
  const baseViewBox = [viewX, viewY, viewWidth, viewHeight].join(' ');
  return '<div data-workflow-flow style="position:relative;margin:10px 0 14px;border:1px solid var(--pf-border);border-radius:8px;background:var(--pf-bg);overflow:hidden;">'
    + '<div style="position:absolute;z-index:2;top:8px;right:8px;display:flex;gap:4px;">'
    + '<button type="button" data-flow-zoom="in" aria-label="' + _pfpAttr(t('workflowRunZoomIn')) + '" title="' + _pfpAttr(t('workflowRunZoomIn')) + '">+</button>'
    + '<button type="button" data-flow-zoom="out" aria-label="' + _pfpAttr(t('workflowRunZoomOut')) + '" title="' + _pfpAttr(t('workflowRunZoomOut')) + '">−</button>'
    + '<button type="button" data-flow-zoom="reset" aria-label="' + _pfpAttr(t('workflowRunResetView')) + '" title="' + _pfpAttr(t('workflowRunResetView')) + '">↺</button></div>'
    + '<svg data-workflow-flow-svg data-base-view-box="' + baseViewBox
    + '" role="img" aria-label="' + _pfpAttr(t('workflowRunFlow'))
    + '" viewBox="' + baseViewBox
    + '" style="display:block;width:100%;min-height:300px;max-height:520px;touch-action:none;cursor:grab;">'
    + '<defs><marker id="workflow-run-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    + '<path d="M 0 0 L 10 5 L 0 10 z" fill="var(--pf-muted)" /></marker></defs>'
    + edgeHtml + nodeHtml + '</svg></div>';
}

function _bindWorkflowFlowControls(root) {
  root.querySelectorAll('[data-workflow-flow]').forEach(function(viewport) {
    const svg = viewport.querySelector('[data-workflow-flow-svg]');
    if (!svg || svg.dataset.flowBound === 'true') return;
    svg.dataset.flowBound = 'true';
    const base = svg.dataset.baseViewBox.split(' ').map(Number);
    const minimumWidth = base[2] / 4;
    const maximumWidth = base[2] * 4;
    function box() {
      const value = svg.viewBox.baseVal;
      return { x: value.x, y: value.y, width: value.width, height: value.height };
    }
    function setBox(value) {
      svg.setAttribute('viewBox', [value.x, value.y, value.width, value.height].join(' '));
    }
    function zoom(factor, clientX, clientY) {
      const current = box();
      const width = Math.max(minimumWidth, Math.min(maximumWidth, current.width * factor));
      const height = current.height * (width / current.width);
      const rect = svg.getBoundingClientRect();
      const rx = clientX === undefined ? 0.5 : (clientX - rect.left) / rect.width;
      const ry = clientY === undefined ? 0.5 : (clientY - rect.top) / rect.height;
      setBox({
        x: current.x + (current.width - width) * rx,
        y: current.y + (current.height - height) * ry,
        width: width,
        height: height,
      });
    }
    viewport.querySelectorAll('[data-flow-zoom]').forEach(function(button) {
      button.onclick = function() {
        const action = button.dataset.flowZoom;
        if (action === 'reset') {
          svg.setAttribute('viewBox', svg.dataset.baseViewBox);
        } else {
          zoom(action === 'in' ? 0.8 : 1.25);
        }
      };
    });
    svg.addEventListener('wheel', function(event) {
      event.preventDefault();
      zoom(event.deltaY < 0 ? 0.88 : 1.14, event.clientX, event.clientY);
    }, { passive: false });
    let drag = null;
    svg.addEventListener('pointerdown', function(event) {
      drag = { x: event.clientX, y: event.clientY };
      svg.setPointerCapture(event.pointerId);
      svg.style.cursor = 'grabbing';
    });
    svg.addEventListener('pointermove', function(event) {
      if (!drag) return;
      const current = box();
      const rect = svg.getBoundingClientRect();
      current.x -= (event.clientX - drag.x) * current.width / rect.width;
      current.y -= (event.clientY - drag.y) * current.height / rect.height;
      drag = { x: event.clientX, y: event.clientY };
      setBox(current);
    });
    function stopDrag(event) {
      if (!drag) return;
      drag = null;
      if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
      svg.style.cursor = 'grab';
    }
    svg.addEventListener('pointerup', stopDrag);
    svg.addEventListener('pointercancel', stopDrag);
  });
}

function _workflowRunDetailHtml(run) {
  const flow = run.flow || {};
  const events = run.events || [];
  const terminal = run.terminal_commit || {};
  const error = run.error || null;
  const graph = run.flow_graph || {};
  const tasks = Array.isArray(graph.tasks) ? graph.tasks : [];
  const currentEvent = events.length ? events[events.length - 1] : null;
  const currentData = currentEvent ? (currentEvent.data || {}) : {};
  const runningTask = tasks.find(function(task) { return task.status === 'running'; });
  const currentTaskId = (runningTask && runningTask.id) || currentData.task_id || '';
  const currentTaskIndex = tasks.findIndex(function(task) {
    return task.id === currentTaskId;
  });
  const terminalStatus = ['completed', 'failed', 'cancelled', 'interrupted']
    .includes(run.status);
  const totalSteps = tasks.length;
  const completedSteps = tasks.filter(function(task) {
    return task.status === 'completed';
  }).length;
  const seenSteps = completedSteps + (runningTask ? 1 : 0);
  const currentStep = totalSteps
    ? (terminalStatus ? totalSteps : Math.min(totalSteps, Math.max(
      1, currentTaskIndex >= 0 ? currentTaskIndex + 1 : seenSteps,
    )))
    : seenSteps;
  const progress = totalSteps ? Math.round((currentStep / totalSteps) * 100) : 0;
  const activity = (runningTask && (runningTask.label || runningTask.id))
    || currentData.label || currentData.task_id
    || currentData.task_type || (currentEvent && currentEvent.event_type)
    || t('workflowRunNoStages');
  const activityDetail = [
    currentData.stage, currentData.phase, currentData.tool_name,
    currentData.outcome, currentData.service_id,
  ].filter(Boolean).join(' \u00b7 ');
  let latestReturn = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const data = events[index].data || {};
    if (data.content || data.reason || data.structured_content) {
      latestReturn = data; break;
    }
  }
  const stages = events.length
    ? '<ol aria-label="' + _pfpAttr(t('workflowRunStages')) + '" style="padding:0;margin:6px 0;">'
      + events.map(_workflowRunStageHtml).join('') + '</ol>'
    : '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('workflowRunNoStages')) + '</div>';
  const executionTypes = ['agent_message', 'tool_call', 'tool_result', 'error'];
  const executionEvents = events.filter(function(event) {
    return executionTypes.includes(event.event_type);
  });
  const execution = executionEvents.length
    ? '<ol aria-label="' + _pfpAttr(t('workflowRunExecution')) + '" style="padding:0;margin:6px 0;">'
      + executionEvents.map(_workflowRunStageHtml).join('') + '</ol>'
    : '<div style="color:var(--pf-muted);font-size:11px;">'
      + escapeHtml(t('workflowRunNoExecution')) + '</div>';
  return '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:11px;">'
    + '<div><strong>' + escapeHtml(flow.name || t('workflowRunFlow')) + '</strong><div style="color:var(--pf-muted);margin-top:2px;">'
    + escapeHtml(_workflowRunDate(run.updated_at)) + '</div></div>'
    + '<span style="padding:3px 7px;border-radius:999px;background:var(--pf-sidebar);border:1px solid var(--pf-border);">'
    + escapeHtml(run.status || '\u2014') + '</span></div>'
    + '<div style="display:flex;justify-content:space-between;gap:8px;margin-top:12px;font-size:11px;">'
    + '<strong>' + escapeHtml(t('workflowRunStages')) + ' ' + escapeHtml(String(currentStep))
    + (totalSteps ? '/' + escapeHtml(String(totalSteps)) : '') + '</strong>'
    + '<span style="color:var(--pf-muted);">' + escapeHtml(String(progress)) + '%</span></div>'
    + '<div role="progressbar" aria-label="' + _pfpAttr(t('workflowRunStages'))
    + '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + progress
    + '" style="height:7px;background:var(--pf-border);border-radius:999px;overflow:hidden;margin-top:5px;">'
    + '<div style="height:100%;width:' + progress + '%;background:var(--pf-accent);transition:width .2s ease;"></div></div>'
    + '<div style="margin-top:12px;padding:10px;border:2px solid var(--pf-accent);border-radius:7px;background:var(--pf-sidebar);">'
    + '<div style="color:var(--pf-accent);font-size:10px;font-weight:700;text-transform:uppercase;">' + escapeHtml(t('workflowRunCurrentStage')) + '</div>'
    + '<strong style="display:block;font-size:13px;margin-top:3px;">' + escapeHtml(activity) + '</strong>'
    + (activityDetail ? '<div style="color:var(--pf-muted);font-size:11px;margin-top:3px;">' + escapeHtml(activityDetail) + '</div>' : '')
    + (latestReturn ? '<div style="font-size:11px;margin-top:8px;">'
      + _workflowRunMessageValueHtml(latestReturn)
      + '</div>' : '') + '</div>'
    + '<section data-workflow-run-execution style="margin-top:12px;border:1px solid var(--pf-border);border-radius:7px;padding:9px;background:var(--pf-bg);">'
    + '<strong style="font-size:12px;">' + escapeHtml(t('workflowRunExecution')) + '</strong>'
    + '<div data-workflow-run-execution-scroll style="max-height:360px;overflow:auto;margin-top:6px;">' + execution + '</div></section>'
    + _workflowFlowHtml(run)
    + (run.failure_reason ? '<div style="margin-top:8px;color:var(--pf-danger);"><strong>' + escapeHtml(t('workflowRunFailure')) + ':</strong> ' + escapeHtml(run.failure_reason) + '</div>' : '')
    + (error && error.message ? '<div style="margin-top:8px;color:var(--pf-danger);"><strong>'
      + escapeHtml(error.code || t('workflowRunFailure')) + ':</strong> ' + escapeHtml(error.message)
      + (error.task_id ? ' \u00b7 ' + escapeHtml(error.task_id) : '') + '</div>' : '')
    + '<details data-workflow-run-metadata style="margin-top:12px;border-top:1px solid var(--pf-border);padding-top:8px;">'
    + '<summary style="cursor:pointer;color:var(--pf-muted);font-size:11px;">'
    + escapeHtml(t('workflowRunFlow')) + ' \u00b7 ' + escapeHtml(t('workflowRunStages')) + '</summary>'
    + '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;font-size:11px;margin-top:9px;">'
    + '<div><strong>' + escapeHtml(t('workflowRunGeneration')) + ':</strong> ' + escapeHtml(String(run.generation || 0)) + '</div>'
    + '<div><strong>' + escapeHtml(t('workflowRunCreated')) + ':</strong> ' + escapeHtml(_workflowRunDate(run.created_at)) + '</div></div>'
    + '<div style="margin-top:10px;"><strong style="font-size:11px;">' + escapeHtml(t('workflowRunUsage')) + '</strong><div>'
    + _workflowRunUsageHtml(run.usage) + '</div></div>'
    + '<div style="margin-top:10px;"><strong style="font-size:11px;">' + escapeHtml(t('workflowRunTerminal')) + '</strong>'
    + '<div style="font-size:11px;color:var(--pf-muted);">'
    + escapeHtml(t('workflowRunMessageCommit')) + ': ' + (terminal.message_committed ? '\u2713' : '\u2014') + ' \u00b7 '
    + escapeHtml(t('workflowRunInboxAck')) + ': ' + (terminal.inbox_acknowledged ? '\u2713' : '\u2014') + ' \u00b7 '
    + escapeHtml(t('workflowRunEventDelivery')) + ': ' + (terminal.outbox_enqueued ? '\u2713' : '\u2014') + '</div></div>'
    + '<div style="margin-top:10px;"><strong style="font-size:11px;">' + escapeHtml(t('workflowRunStages')) + '</strong>' + stages + '</div></details>';
}

async function showWorkflowRunInspector(agentName) {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  document.getElementById('workflowRunInspectorOverlay')?.remove();
  const overlay = document.createElement('div');
  overlay.id = 'workflowRunInspectorOverlay';
  overlay.className = 'exec-overlay';
  const titleId = 'workflow-run-inspector-title';
  overlay.innerHTML = '<div class="exec-dialog" role="dialog" aria-modal="true" aria-labelledby="' + titleId + '" tabindex="-1" style="width:min(1120px,calc(100vw - 32px));max-height:90vh;overflow:auto;">'
    + '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;">'
    + '<h3 id="' + titleId + '" style="margin:0;">' + escapeHtml(t('workflowRunsTitle', { agent: agentName })) + '</h3>'
    + '<button type="button" data-close aria-label="' + _pfpAttr(t('close')) + '" style="background:none;border:none;color:var(--pf-muted);font-size:18px;cursor:pointer;">&times;</button></div>'
    + '<div data-run-content aria-live="polite">' + escapeHtml(t('loading')) + '</div></div>';
  document.body.appendChild(overlay);
  const dialog = overlay.querySelector('[role="dialog"]');
  const content = overlay.querySelector('[data-run-content]');
  let closed = false;
  let refreshTimer = null;
  let selectedRunId = '';
  let followLatest = true;
  let refreshing = false;
  let refreshPending = false;
  let lastRuns = [];
  let renderedRunId = '';
  const close = function() {
    closed = true;
    if (refreshTimer) clearTimeout(refreshTimer);
    overlay.remove();
    document.removeEventListener('keydown', onKey);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    window.removeEventListener('pawflow:workflow-progress', onWorkflowProgress);
  };
  const onKey = function(event) { if (event.key === 'Escape') close(); };
  overlay.querySelector('[data-close]').onclick = close;
  document.addEventListener('keydown', onKey);
  dialog.focus();

  function renderRunList(runs) {
    const list = content.querySelector('[data-run-list]');
    if (!list) return;
    list.innerHTML = runs.map(function(run) {
      const selected = run.run_id === selectedRunId;
      return '<div role="listitem"><button type="button" data-run-id="' + _pfpAttr(run.run_id)
        + '" data-run-selected="' + (selected ? 'true' : 'false') + '" aria-label="'
        + _pfpAttr(t('workflowRunInspect', { id: run.run_id }))
        + '" style="display:block;width:100%;text-align:left;background:var(--pf-sidebar);color:var(--pf-text);border:'
        + (selected ? '2px solid var(--pf-accent)' : '1px solid var(--pf-border)')
        + ';border-radius:5px;padding:8px;margin-bottom:6px;cursor:pointer;">'
        + '<strong style="font-size:11px;">' + escapeHtml(run.status || '\u2014') + '</strong>'
        + '<span style="float:right;font-size:9px;color:var(--pf-muted);">#'
        + escapeHtml(String(run.generation || 0)) + '</span>'
        + '<div style="font-size:10px;color:var(--pf-muted);margin-top:2px;">'
        + escapeHtml(_workflowRunDate(run.updated_at)) + '</div></button></div>';
    }).join('');
    list.querySelectorAll('[data-run-id]').forEach(function(button) {
      button.onclick = function() {
        selectedRunId = button.dataset.runId;
        followLatest = !!(lastRuns[0] && lastRuns[0].run_id === selectedRunId);
        renderRunList(lastRuns);
        refresh().catch(function(error) { addMsg('error', error.message); });
      };
    });
  }

  function renderDetail(run) {
    if (!run || closed || run.run_id !== selectedRunId) return;
    const detail = content.querySelector('[data-run-detail]');
    const previousSvg = renderedRunId === run.run_id
      ? detail.querySelector('[data-workflow-flow-svg]') : null;
    const previousViewBox = previousSvg ? previousSvg.getAttribute('viewBox') : '';
    const previousMetadata = renderedRunId === run.run_id
      ? detail.querySelector('[data-workflow-run-metadata]') : null;
    const previousMetadataOpen = !!(previousMetadata && previousMetadata.open);
    const previousExecution = renderedRunId === run.run_id
      ? detail.querySelector('[data-workflow-run-execution-scroll]') : null;
    const previousExecutionTop = previousExecution ? previousExecution.scrollTop : 0;
    const followExecution = !previousExecution
      || previousExecution.scrollHeight - previousExecution.scrollTop
        - previousExecution.clientHeight < 40;
    detail.innerHTML = _workflowRunDetailHtml(run);
    const nextSvg = detail.querySelector('[data-workflow-flow-svg]');
    if (nextSvg && previousViewBox) nextSvg.setAttribute('viewBox', previousViewBox);
    const nextMetadata = detail.querySelector('[data-workflow-run-metadata]');
    if (nextMetadata && previousMetadataOpen) nextMetadata.open = true;
    const nextExecution = detail.querySelector('[data-workflow-run-execution-scroll]');
    if (nextExecution) {
      nextExecution.scrollTop = followExecution
        ? nextExecution.scrollHeight : previousExecutionTop;
    }
    _bindWorkflowFlowControls(detail);
    renderedRunId = run.run_id;
    const controls = content.querySelector('[data-run-controls]');
    controls.innerHTML = (run.safe_retry
      ? '<button type="button" data-retry style="background:var(--pf-warning,#d29922);color:var(--pf-bg);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('workflowRunRetry')) + '</button>'
      : '') + (run.can_delete
      ? '<button type="button" data-delete style="background:var(--pf-danger);color:#fff;border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('delete')) + '</button>'
      : '');
    const retry = controls.querySelector('[data-retry]');
    if (retry) retry.onclick = async function() {
      if (!confirm(t('workflowRunRetryConfirm'))) return;
      retry.disabled = true;
      try {
        const result = await rxjs.firstValueFrom(action$('retry_workflow_run', {
          conversation_id: conversationId, run_id: run.run_id,
        }));
        if (result.error) throw new Error(result.error);
        addMsg('system', t('workflowRunRetryStarted'));
        close();
      } catch (error) {
        retry.disabled = false;
        addMsg('error', error.message);
      }
    };
    const deleteButton = controls.querySelector('[data-delete]');
    if (deleteButton) deleteButton.onclick = async function() {
      if (!confirm(t('workflowRunDeleteConfirm'))) return;
      deleteButton.disabled = true;
      try {
        const result = await rxjs.firstValueFrom(action$('delete_workflow_run', {
          conversation_id: conversationId, run_id: run.run_id,
        }));
        if (result.error) throw new Error(result.error);
        selectedRunId = '';
        followLatest = true;
        renderedRunId = '';
        await refresh();
      } catch (error) {
        deleteButton.disabled = false;
        addMsg('error', error.message);
      }
    };
  }

  function refreshDelay() {
    if (document.visibilityState === 'hidden') return 15000;
    const active = lastRuns.some(function(run) {
      return ['accepted', 'running', 'waiting', 'committing'].includes(run.status);
    });
    return active ? 2500 : 8000;
  }

  function scheduleRefresh(delay) {
    if (closed) return;
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function() {
      refreshTimer = null;
      refresh().catch(function(error) { addMsg('error', error.message); });
    }, delay === undefined ? refreshDelay() : delay);
  }

  function onWorkflowProgress(event) {
    const data = (event && event.detail) || {};
    if (String(data.agent_name || '').toLowerCase()
        !== String(agentName || '').toLowerCase()) return;
    if (data.run_id && selectedRunId && data.run_id !== selectedRunId
        && !followLatest) return;
    scheduleRefresh(0);
  }

  function onVisibilityChange() {
    if (document.visibilityState !== 'hidden') scheduleRefresh(0);
  }

  async function refresh() {
    if (closed) return;
    if (refreshing) { refreshPending = true; return; }
    refreshing = true;
    refreshPending = false;
    try {
      const requestedRunId = selectedRunId;
      const data = await rxjs.firstValueFrom(action$('workflow_run_snapshot', {
        conversation_id: conversationId, agent_name: agentName,
        run_id: requestedRunId, limit: 25,
      }));
      if (data.error) throw new Error(data.error);
      const runs = data.runs || [];
      lastRuns = runs;
      if (!runs.length) {
        content.innerHTML = '<div style="color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('workflowRunsEmpty')) + '</div>';
        return;
      }
      if (!content.querySelector('[data-run-list]')) {
        content.innerHTML = '<div style="display:grid;grid-template-columns:minmax(220px,0.8fr) minmax(300px,1.4fr);gap:12px;">'
          + '<div data-run-list role="list"></div><div><div data-run-controls style="display:flex;justify-content:flex-end;margin-bottom:6px;"></div><div data-run-detail></div></div></div>';
      }
      if (followLatest || !runs.some(function(run) { return run.run_id === selectedRunId; })) {
        selectedRunId = runs[0].run_id;
        followLatest = true;
      }
      renderRunList(runs);
      if (data.run && data.run.run_id === selectedRunId) renderDetail(data.run);
    } catch (error) {
      if (!content.querySelector('[data-run-list]')) {
        content.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;">'
          + escapeHtml(t('workflowRunLoadFailed', { error: error.message })) + '</div>';
      }
    } finally {
      refreshing = false;
      if (refreshPending) scheduleRefresh(0);
      else scheduleRefresh();
    }
  }

  window.addEventListener('pawflow:workflow-progress', onWorkflowProgress);
  document.addEventListener('visibilitychange', onVisibilityChange);
  await refresh();
}
