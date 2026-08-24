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

function _workflowRunStageHtml(event) {
  const data = event.data || {};
  const label = data.label || data.task_id || data.task_type || event.event_type || '\u2014';
  const detail = [data.stage, data.service_id].filter(Boolean).join(' \u00b7 ');
  return '<li style="list-style:none;position:relative;padding:7px 9px;margin:0 0 6px;border:1px solid var(--pf-border);border-radius:5px;background:var(--pf-sidebar);">'
    + '<div style="display:flex;justify-content:space-between;gap:8px;">'
    + '<strong style="color:var(--pf-text);font-size:12px;">' + escapeHtml(label) + '</strong>'
    + '<span style="color:var(--pf-muted);font-size:10px;">#' + escapeHtml(String(event.sequence || 0)) + '</span></div>'
    + (detail ? '<div style="color:var(--pf-muted);font-size:11px;margin-top:2px;">' + escapeHtml(detail) + '</div>' : '')
    + (data.reason ? '<div style="color:var(--pf-warning,#d29922);font-size:11px;margin-top:2px;">' + escapeHtml(data.reason) + '</div>' : '')
    + _workflowRunGroupMetaHtml(data)
    + (data.content ? '<div style="white-space:pre-wrap;color:var(--pf-text);font-size:11px;margin-top:5px;">' + escapeHtml(data.content) + '</div>' : '')
    + _workflowRunCitationsHtml(data.citations)
    + '<div style="font-size:10px;margin-top:3px;">' + _workflowRunUsageHtml(data.usage || data.token_usage) + '</div></li>';
}

function _workflowRunDetailHtml(run) {
  const flow = run.flow || {};
  const events = run.events || [];
  const terminal = run.terminal_commit || {};
  const stages = events.length
    ? '<ol aria-label="' + _pfpAttr(t('workflowRunStages')) + '" style="padding:0;margin:6px 0;">'
      + events.map(_workflowRunStageHtml).join('') + '</ol>'
    : '<div style="color:var(--pf-muted);font-size:11px;">' + escapeHtml(t('workflowRunNoStages')) + '</div>';
  return '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;font-size:11px;">'
    + '<div><strong>' + escapeHtml(t('workflowRunStatus')) + ':</strong> ' + escapeHtml(run.status || '\u2014') + '</div>'
    + '<div><strong>' + escapeHtml(t('workflowRunGeneration')) + ':</strong> ' + escapeHtml(String(run.generation || 0)) + '</div>'
    + '<div style="grid-column:1/-1;"><strong>' + escapeHtml(t('workflowRunFlow')) + ':</strong> ' + escapeHtml(flow.name || '\u2014') + '</div>'
    + '<div><strong>' + escapeHtml(t('workflowRunCreated')) + ':</strong> ' + escapeHtml(_workflowRunDate(run.created_at)) + '</div>'
    + '<div><strong>' + escapeHtml(t('workflowRunUpdated')) + ':</strong> ' + escapeHtml(_workflowRunDate(run.updated_at)) + '</div>'
    + (run.failure_reason ? '<div style="grid-column:1/-1;color:var(--pf-danger);"><strong>' + escapeHtml(t('workflowRunFailure')) + ':</strong> ' + escapeHtml(run.failure_reason) + '</div>' : '')
    + '</div><div style="margin-top:10px;"><strong style="font-size:11px;">' + escapeHtml(t('workflowRunUsage')) + '</strong><div>'
    + _workflowRunUsageHtml(run.usage) + '</div></div>'
    + '<div style="margin-top:10px;"><strong style="font-size:11px;">' + escapeHtml(t('workflowRunTerminal')) + '</strong>'
    + '<div style="font-size:11px;color:var(--pf-muted);">'
    + escapeHtml(t('workflowRunMessageCommit')) + ': ' + (terminal.message_committed ? '\u2713' : '\u2014') + ' \u00b7 '
    + escapeHtml(t('workflowRunInboxAck')) + ': ' + (terminal.inbox_acknowledged ? '\u2713' : '\u2014') + ' \u00b7 '
    + escapeHtml(t('workflowRunEventDelivery')) + ': ' + (terminal.outbox_enqueued ? '\u2713' : '\u2014') + '</div></div>'
    + '<div style="margin-top:10px;"><strong style="font-size:11px;">' + escapeHtml(t('workflowRunStages')) + '</strong>' + stages + '</div>';
}

async function showWorkflowRunInspector(agentName) {
  if (!conversationId) { addMsg('error', t('noConv')); return; }
  document.getElementById('workflowRunInspectorOverlay')?.remove();
  const overlay = document.createElement('div');
  overlay.id = 'workflowRunInspectorOverlay';
  overlay.className = 'exec-overlay';
  const titleId = 'workflow-run-inspector-title';
  overlay.innerHTML = '<div class="exec-dialog" role="dialog" aria-modal="true" aria-labelledby="' + titleId + '" tabindex="-1" style="width:min(860px,calc(100vw - 32px));max-height:85vh;overflow:auto;">'
    + '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;">'
    + '<h3 id="' + titleId + '" style="margin:0;">' + escapeHtml(t('workflowRunsTitle', { agent: agentName })) + '</h3>'
    + '<button type="button" data-close aria-label="' + _pfpAttr(t('close')) + '" style="background:none;border:none;color:var(--pf-muted);font-size:18px;cursor:pointer;">&times;</button></div>'
    + '<div data-run-content aria-live="polite">' + escapeHtml(t('loading')) + '</div></div>';
  document.body.appendChild(overlay);
  const dialog = overlay.querySelector('[role="dialog"]');
  const content = overlay.querySelector('[data-run-content]');
  const close = function() { overlay.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = function(event) { if (event.key === 'Escape') close(); };
  overlay.querySelector('[data-close]').onclick = close;
  document.addEventListener('keydown', onKey);
  dialog.focus();

  async function inspect(runId) {
    const data = await rxjs.firstValueFrom(action$('inspect_workflow_run', {
      conversation_id: conversationId, run_id: runId,
    }));
    if (data.error) throw new Error(data.error);
    const run = data.run;
    const detail = content.querySelector('[data-run-detail]');
    detail.innerHTML = _workflowRunDetailHtml(run);
    const controls = content.querySelector('[data-run-controls]');
    controls.innerHTML = run.safe_retry
      ? '<button type="button" data-retry style="background:var(--pf-warning,#d29922);color:var(--pf-bg);border:none;padding:7px 12px;border-radius:4px;cursor:pointer;">' + escapeHtml(t('workflowRunRetry')) + '</button>'
      : '';
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
  }

  try {
    const data = await rxjs.firstValueFrom(action$('list_workflow_runs', {
      conversation_id: conversationId, agent_name: agentName,
    }));
    if (data.error) throw new Error(data.error);
    const runs = data.runs || [];
    if (!runs.length) {
      content.innerHTML = '<div style="color:var(--pf-muted);font-size:12px;">' + escapeHtml(t('workflowRunsEmpty')) + '</div>';
      return;
    }
    content.innerHTML = '<div style="display:grid;grid-template-columns:minmax(220px,0.8fr) minmax(300px,1.4fr);gap:12px;">'
      + '<div data-run-list role="list"></div><div><div data-run-controls style="display:flex;justify-content:flex-end;margin-bottom:6px;"></div><div data-run-detail></div></div></div>';
    const list = content.querySelector('[data-run-list]');
    list.innerHTML = runs.map(function(run) {
      return '<div role="listitem"><button type="button" data-run-id="' + _pfpAttr(run.run_id) + '" aria-label="' + _pfpAttr(t('workflowRunInspect', { id: run.run_id })) + '" style="display:block;width:100%;text-align:left;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:5px;padding:8px;margin-bottom:6px;cursor:pointer;">'
        + '<strong style="font-size:11px;">' + escapeHtml(run.status || '\u2014') + '</strong>'
        + '<div style="font-size:10px;color:var(--pf-muted);margin-top:2px;">' + escapeHtml(_workflowRunDate(run.updated_at)) + '</div></button></div>';
    }).join('');
    list.querySelectorAll('[data-run-id]').forEach(function(button) {
      button.onclick = function() { inspect(button.dataset.runId).catch(function(error) { addMsg('error', error.message); }); };
    });
    await inspect(runs[0].run_id);
  } catch (error) {
    content.innerHTML = '<div style="color:var(--pf-danger);font-size:12px;">' + escapeHtml(t('workflowRunLoadFailed', { error: error.message })) + '</div>';
  }
}
