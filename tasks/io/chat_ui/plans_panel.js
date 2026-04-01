// ── Plans Panel ─────────────────────────────────────────────────

async function togglePlansPanel() {
  const panel = document.getElementById('plansPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadPlans();
  } else {
    panel.style.display = 'none';
  }
}

function loadPlans() {
  if (!conversationId) return Promise.resolve();
  const list = document.getElementById('plansList');
  list.innerHTML = '<span style="color:#808090;font-size:12px">Loading...</span>';
  return new Promise(resolve => {
    action$('get_plans').subscribe(data => {
      // plans can be an array (new) or dict (legacy)
      let planArr = [];
      if (Array.isArray(data.plans)) {
        planArr = data.plans;
      } else if (data.plans && typeof data.plans === 'object') {
        planArr = Object.values(data.plans);
      }
      if (planArr.length === 0) {
        list.innerHTML = '<div style="margin-bottom:8px;"><button onclick="showCreatePlanDialog()" style="padding:4px 10px;background:#6c5ce7;color:white;border:none;border-radius:4px;cursor:pointer;font-size:11px;">+ Create Plan</button></div>'
          + '<span style="color:#808090;font-size:12px">No active plans.</span>';
        resolve();
        return;
      }
      list.innerHTML = '<div style="margin-bottom:8px;"><button onclick="showCreatePlanDialog()" style="padding:4px 10px;background:#6c5ce7;color:white;border:none;border-radius:4px;cursor:pointer;font-size:11px;">+ Create Plan</button></div>';
      for (const plan of planArr) {
        if (!plan || !plan.title) continue;
        const pid = plan.id || 'unknown';
        const steps = plan.steps || [];
        const doneCount = steps.filter(s => s.status === 'done').length;
        const total = steps.length;
        const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;
        const planStatus = plan.status || 'unknown';

        const planDiv = document.createElement('div');
        planDiv.className = 'plan-card';
        planDiv.style.cssText = 'margin:4px 0;padding:6px 8px;background:#1a1a2e;border-radius:6px;border-left:3px solid ' + _planStatusColor(planStatus) + ';';

        // Header with title + status badge + progress
        const header = document.createElement('div');
        header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;cursor:pointer;';
        header.innerHTML = '<div style="display:flex;align-items:center;gap:6px;">' +
          '<span style="font-weight:600;font-size:12px;color:#e0e0e0;">' + escapeHtml(plan.title) + '</span>' +
          '<span style="font-size:9px;padding:1px 5px;border-radius:3px;background:' + _planStatusColor(planStatus) + '22;color:' + _planStatusColor(planStatus) + ';border:1px solid ' + _planStatusColor(planStatus) + '44;">' + escapeHtml(planStatus) + '</span>' +
          '</div>' +
          '<span style="font-size:10px;color:#808090;">' + doneCount + '/' + total + ' (' + pct + '%)</span>';

        // Progress bar
        const progressBar = document.createElement('div');
        progressBar.style.cssText = 'height:3px;background:#333;border-radius:2px;margin:4px 0;overflow:hidden;';
        const progressFill = document.createElement('div');
        const barColor = pct === 100 ? '#4ecdc4' : pct > 50 ? '#6c5ce7' : '#f0ad4e';
        progressFill.style.cssText = 'height:100%;background:' + barColor + ';width:' + pct + '%;transition:width 0.3s;border-radius:2px;';
        progressBar.appendChild(progressFill);

        // Steps (collapsible)
        const stepsDiv = document.createElement('div');
        stepsDiv.style.cssText = 'display:none;margin-top:4px;';
        for (const step of steps) {
          const stepDiv = document.createElement('div');
          stepDiv.style.cssText = 'display:flex;align-items:center;gap:4px;margin:2px 0;padding:2px 4px;border-radius:3px;font-size:11px;';
          stepDiv.oncontextmenu = (function(planId, stepIndex, currentStatus) {
            return function(e) { showPlanStepMenu(e, planId, stepIndex, currentStatus); };
          })(pid, step.index, step.status);

          const icon = _planStepIcon(step.status);
          const color = _planStepColor(step.status);
          const textDecor = step.status === 'skipped' ? 'line-through' : 'none';
          const assignee = step.assigned_to ? ' [' + step.assigned_to + ']' : '';
          const verifier = (step.verifier || plan.verifier) ? ' \uD83D\uDD0D' + (step.verifier || plan.verifier) : '';
          const canAssign = step.status === 'pending' || step.status === 'in_progress' || step.status === 'error';
          stepDiv.style.cssText += 'justify-content:space-between;';
          const stepLeft = document.createElement('div');
          stepLeft.style.cssText = 'display:flex;align-items:center;gap:4px;flex:1;min-width:0;';
          stepLeft.innerHTML = '<span style="color:' + color + ';font-size:13px;flex-shrink:0;">' + icon + '</span>' +
            '<span style="color:' + color + ';text-decoration:' + textDecor + ';overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + step.index + '. ' + escapeHtml(step.description) + '</span>' +
            (assignee ? '<span style="color:#6c5ce7;font-size:9px;flex-shrink:0;">' + escapeHtml(assignee) + '</span>' : '') +
            (verifier ? '<span style="color:#e0a040;font-size:9px;flex-shrink:0;">' + escapeHtml(verifier) + '</span>' : '') +
            (step.note ? '<span style="color:#555;font-size:10px;margin-left:4px;font-style:italic;flex-shrink:0;">' + escapeHtml(step.note) + '</span>' : '');
          stepDiv.appendChild(stepLeft);
          if (canAssign && planStatus !== 'cancelled' && planStatus !== 'completed') {
            const assignBtn = document.createElement('button');
            assignBtn.title = 'Assign step to agent';
            assignBtn.textContent = '\u{1F464}';
            assignBtn.style.cssText = 'background:none;border:none;cursor:pointer;font-size:11px;padding:0 2px;color:#6c5ce7;flex-shrink:0;';
            assignBtn.onclick = (function(planId, stepIdx) {
              return function(e) { e.stopPropagation(); showAssignStepDialog(planId, stepIdx); };
            })(pid, step.index);
            stepDiv.appendChild(assignBtn);
          }
          stepsDiv.appendChild(stepDiv);
        }

        // Toggle steps on click
        header.onclick = function() {
          stepsDiv.style.display = stepsDiv.style.display === 'none' ? 'block' : 'none';
        };

        // Context menu on the plan card
        planDiv.oncontextmenu = (function(planId, status) {
          return function(e) { showPlanMenu(e, planId, status); };
        })(pid, planStatus);

        planDiv.appendChild(header);
        planDiv.appendChild(progressBar);
        planDiv.appendChild(stepsDiv);
        list.appendChild(planDiv);
      }
      resolve();
    });
  });
}

function _planStatusColor(status) {
  return {
    'pending_approval': '#f0ad4e',
    'approved': '#6c5ce7',
    'in_progress': '#3498db',
    'completed': '#4ecdc4',
    'cancelled': '#e94560',
  }[status] || '#808090';
}

function _planStepIcon(status) {
  return {'pending': '\u25cb', 'in_progress': '\u25d4', 'done': '\u2713', 'skipped': '\u2013', 'error': '\u2717', 'pending_verification': '\u2690'}[status] || '\u25cb';
}

function _planStepColor(status) {
  return {'pending': '#808090', 'in_progress': '#6c5ce7', 'done': '#4ecdc4', 'skipped': '#555', 'error': '#e94560', 'pending_verification': '#e0a040'}[status] || '#808090';
}

// ── Plan context menu ──────────────────────────────────────────
function showPlanMenu(e, planId, planStatus) {
  e.preventDefault();
  e.stopPropagation();
  closePlanMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'planCtxMenu';
  _positionMenu(menu, e);
  let items = '';
  if (planStatus === 'pending_approval') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();planAction(\'approve_plan\',\'' + planId + '\');closePlanMenu();">&#x2705; Approve</div>';
  }
  if (planStatus !== 'cancelled' && planStatus !== 'completed') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();showAssignPlanDialog(\'' + planId + '\');closePlanMenu();">&#x1F464; Assign to...</div>';
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();showSetVerifierDialog(\'' + planId + '\',0);closePlanMenu();">&#x1F50D; Set verifier...</div>';
  }
  if (planStatus !== 'cancelled' && planStatus !== 'completed') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();planAction(\'cancel_plan\',\'' + planId + '\');closePlanMenu();">&#x23F9; Cancel</div>';
  }
  if (planStatus !== 'pending_approval') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();planAction(\'reset_plan\',\'' + planId + '\');closePlanMenu();">&#x1F504; Reset</div>';
  }
  items += '<div class="ctx-menu-item danger" onclick="event.stopPropagation();planAction(\'delete_plan\',\'' + planId + '\');closePlanMenu();">&#x1F5D1; Delete</div>';
  menu.innerHTML = items;
  setTimeout(() => document.addEventListener('click', closePlanMenu, {once: true}), 0);
}

function closePlanMenu() {
  const m = document.getElementById('planCtxMenu');
  if (m) m.remove();
}

function showPlanStepMenu(e, planId, stepIndex, currentStatus) {
  e.preventDefault();
  e.stopPropagation();
  closePlanMenu();
  const menu = document.createElement('div');
  menu.className = 'ctx-menu';
  menu.id = 'planCtxMenu';
  _positionMenu(menu, e);
  let items = '';
  if (currentStatus !== 'done') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();updatePlanStep(\'' + planId + '\',' + stepIndex + ',\'done\');closePlanMenu();">&#x2705; Mark Done</div>';
  }
  if (currentStatus !== 'in_progress') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();updatePlanStep(\'' + planId + '\',' + stepIndex + ',\'in_progress\');closePlanMenu();">&#x25D4; In Progress</div>';
  }
  if (currentStatus !== 'skipped') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();updatePlanStep(\'' + planId + '\',' + stepIndex + ',\'skipped\');closePlanMenu();">&#x2013; Skip</div>';
  }
  if (currentStatus !== 'pending') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();updatePlanStep(\'' + planId + '\',' + stepIndex + ',\'pending\');closePlanMenu();">&#x25CB; Reset to Pending</div>';
  }
  if (currentStatus === 'pending' || currentStatus === 'in_progress' || currentStatus === 'error') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();showAssignStepDialog(\'' + planId + '\',' + stepIndex + ');closePlanMenu();">&#x1F464; Assign to...</div>';
  }
  items += '<div class="ctx-menu-item" onclick="event.stopPropagation();showSetVerifierDialog(\'' + planId + '\',' + stepIndex + ');closePlanMenu();">&#x1F50D; Set verifier...</div>';
  if (currentStatus === 'pending_verification') {
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();verifyPlanStep(\'' + planId + '\',' + stepIndex + ',true);closePlanMenu();">&#x2705; Approve step</div>';
    items += '<div class="ctx-menu-item" onclick="event.stopPropagation();verifyPlanStep(\'' + planId + '\',' + stepIndex + ',false);closePlanMenu();">&#x274C; Reject step</div>';
  }
  menu.innerHTML = items;
  setTimeout(() => document.addEventListener('click', closePlanMenu, {once: true}), 0);
}

function updatePlanStep(planId, stepIndex, status) {
  action$('update_plan_step', {
    plan_id: planId,
    step: stepIndex,
    status: status,
  }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      loadPlans();
    }
  });
}

function planAction(action, planId) {
  action$(action, { plan_id: planId }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      if (data.plan && data.plan.status === 'approved') addMsg('system', '\u2705 Plan approved');
      else if (data.plan && data.plan.status === 'cancelled') addMsg('system', '\u23F9 Plan cancelled');
      else if (data.deleted) addMsg('system', '\u2705 Plan deleted');
      loadPlans();
    }
  });
}

function _fetchConvAgents() {
  return new Promise((resolve, reject) => {
    action$('list_resources').subscribe({
      next: data => {
        resolve((data.agents || []).map(function(a) { return a.name || a; }));
      },
      error: reject,
    });
  });
}

async function showAssignPlanDialog(planId) {
  let agents = [];
  try {
    agents = await _fetchConvAgents();
  } catch (e) { addMsg('error', 'Failed to list agents'); return; }

  // Build dialog
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

  let agentBtns = agents.map(function(a) {
    return '<button onclick="assignPlanTo(\'' + escapeHtml(planId) + '\',\'' + escapeHtml(a) + '\',\'\');this.closest(\'[data-overlay]\').remove();" '
      + 'style="display:block;width:100%;text-align:left;padding:8px 12px;margin:2px 0;background:#1e1e3f;color:#e0e0e0;border:1px solid #333;border-radius:4px;cursor:pointer;font-size:13px;">'
      + escapeHtml(a) + '</button>';
  }).join('');

  const panel = document.createElement('div');
  panel.setAttribute('data-overlay', '1');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:360px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = '<h3 style="margin:0 0 12px 0;color:#e0e0e0;font-size:14px;">Assign Plan</h3>'
    + '<div style="margin-bottom:12px;">'
    + '<label style="color:#a0a0c0;font-size:12px;">Step range (optional):</label>'
    + '<input id="assignStepRange" type="text" placeholder="e.g. 1-3, remaining, or empty for all" '
    + 'style="width:100%;padding:6px;margin-top:4px;background:#0f1629;color:#e0e0e0;border:1px solid #333;border-radius:4px;font-size:12px;">'
    + '</div>'
    + '<div style="color:#a0a0c0;font-size:12px;margin-bottom:8px;">Select agent:</div>'
    + agentBtns
    + '<button onclick="this.closest(\'[data-overlay]\').remove();" '
    + 'style="margin-top:12px;padding:6px 16px;background:#333;color:#ccc;border:none;border-radius:4px;cursor:pointer;font-size:12px;">Cancel</button>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function assignPlanTo(planId, agent, stepRange) {
  // Get step range from dialog input if not provided
  const rangeInput = document.getElementById('assignStepRange');
  const sr = stepRange || (rangeInput ? rangeInput.value.trim() : '');
  action$('assign_plan', {
    plan_id: planId,
    agent: agent,
    step_range: sr,
  }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      addMsg('system', '\u2705 Plan assigned to ' + agent + (sr ? ' (steps ' + sr + ')' : ''));
      loadPlans();
    }
  });
}

async function showAssignStepDialog(planId, stepIndex) {
  let agents = [];
  try {
    agents = await _fetchConvAgents();
  } catch (e) { addMsg('error', 'Failed to list agents'); return; }

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

  let agentBtns = agents.map(function(a) {
    return '<button onclick="assignPlanTo(\'' + escapeHtml(planId) + '\',\'' + escapeHtml(a) + '\',\'' + stepIndex + '\');this.closest(\'[data-overlay]\').remove();" '
      + 'style="display:block;width:100%;text-align:left;padding:8px 12px;margin:2px 0;background:#1e1e3f;color:#e0e0e0;border:1px solid #333;border-radius:4px;cursor:pointer;font-size:13px;">'
      + escapeHtml(a) + '</button>';
  }).join('');

  const panel = document.createElement('div');
  panel.setAttribute('data-overlay', '1');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:360px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = '<h3 style="margin:0 0 12px 0;color:#e0e0e0;font-size:14px;">Assign Step ' + stepIndex + '</h3>'
    + '<div style="color:#a0a0c0;font-size:12px;margin-bottom:8px;">Select agent:</div>'
    + agentBtns
    + '<button onclick="this.closest(\'[data-overlay]\').remove();" '
    + 'style="margin-top:12px;padding:6px 16px;background:#333;color:#ccc;border:none;border-radius:4px;cursor:pointer;font-size:12px;">Cancel</button>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

async function showCreatePlanDialog() {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

  const panel = document.createElement('div');
  panel.setAttribute('data-overlay', '1');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:460px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = '<h3 style="margin:0 0 12px 0;color:#e0e0e0;font-size:14px;">Create Plan</h3>'
    + '<label style="color:#a0a0c0;font-size:12px;">Title:</label>'
    + '<input id="newPlanTitle" type="text" placeholder="Plan title" '
    + 'style="width:100%;padding:6px;margin:4px 0 12px 0;background:#0f1629;color:#e0e0e0;border:1px solid #333;border-radius:4px;font-size:13px;">'
    + '<label style="color:#a0a0c0;font-size:12px;">Steps (one per line):</label>'
    + '<textarea id="newPlanSteps" rows="8" placeholder="Step 1 description\nStep 2 description\nStep 3 description" '
    + 'style="width:100%;padding:6px;margin:4px 0 12px 0;background:#0f1629;color:#e0e0e0;border:1px solid #333;border-radius:4px;font-size:12px;resize:vertical;font-family:inherit;"></textarea>'
    + '<div style="display:flex;gap:8px;justify-content:flex-end;">'
    + '<button onclick="this.closest(\'[data-overlay]\').remove();" style="padding:8px 16px;background:#333;color:#ccc;border:none;border-radius:4px;cursor:pointer;">Cancel</button>'
    + '<button onclick="submitCreatePlan();this.closest(\'[data-overlay]\').remove();" style="padding:8px 16px;background:#6c5ce7;color:white;border:none;border-radius:4px;cursor:pointer;">Create</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  setTimeout(function() { document.getElementById('newPlanTitle').focus(); }, 50);
}

function submitCreatePlan() {
  const title = (document.getElementById('newPlanTitle') || {}).value || '';
  const stepsText = (document.getElementById('newPlanSteps') || {}).value || '';
  const steps = stepsText.split('\n').map(function(s) { return s.trim(); }).filter(Boolean);
  if (!title || !steps.length) {
    addMsg('system', 'Title and at least one step are required.');
    return;
  }
  action$('create_plan_user', {
    title: title,
    steps: steps,
  }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      addMsg('system', '\u2705 Plan created: ' + title);
      loadPlans();
    }
  });
}

async function showSetVerifierDialog(planId, stepIndex) {
  let agents = [];
  try {
    agents = await _fetchConvAgents();
  } catch (e) { addMsg('error', 'Failed to list agents'); return; }

  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999;';
  overlay.onclick = function(e) { if (e.target === overlay) overlay.remove(); };

  const label = stepIndex > 0 ? 'Step ' + stepIndex : 'Plan';
  let btns = '<button onclick="setPlanVerifier(\'' + escapeHtml(planId) + '\',' + stepIndex + ',\'\');this.closest(\'[data-overlay]\').remove();" '
    + 'style="display:block;width:100%;text-align:left;padding:8px 12px;margin:2px 0;background:#2d1f1f;color:#e08080;border:1px solid #533;border-radius:4px;cursor:pointer;font-size:13px;">'
    + '\u274C Remove verifier</button>';
  btns += agents.map(function(a) {
    return '<button onclick="setPlanVerifier(\'' + escapeHtml(planId) + '\',' + stepIndex + ',\'' + escapeHtml(a) + '\');this.closest(\'[data-overlay]\').remove();" '
      + 'style="display:block;width:100%;text-align:left;padding:8px 12px;margin:2px 0;background:#1e1e3f;color:#e0e0e0;border:1px solid #333;border-radius:4px;cursor:pointer;font-size:13px;">'
      + '\uD83D\uDD0D ' + escapeHtml(a) + '</button>';
  }).join('');

  const panel = document.createElement('div');
  panel.setAttribute('data-overlay', '1');
  panel.style.cssText = 'background:#16213e;border-radius:8px;padding:20px;width:360px;max-height:80vh;overflow-y:auto;border:1px solid #333;';
  panel.innerHTML = '<h3 style="margin:0 0 12px 0;color:#e0e0e0;font-size:14px;">Set Verifier (' + label + ')</h3>'
    + '<div style="color:#a0a0c0;font-size:12px;margin-bottom:8px;">Select verifier agent:</div>'
    + btns
    + '<button onclick="this.closest(\'[data-overlay]\').remove();" '
    + 'style="margin-top:12px;padding:6px 16px;background:#333;color:#ccc;border:none;border-radius:4px;cursor:pointer;font-size:12px;">Cancel</button>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
}

function setPlanVerifier(planId, stepIndex, verifier) {
  action$('set_plan_verifier', {
    plan_id: planId,
    step: stepIndex,
    verifier: verifier,
  }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      const label = stepIndex > 0 ? 'step ' + stepIndex : 'plan';
      addMsg('system', verifier ? '\uD83D\uDD0D Verifier set to ' + verifier + ' for ' + label : '\u274C Verifier removed for ' + label);
      loadPlans();
    }
  });
}

function verifyPlanStep(planId, stepIndex, approved) {
  const reason = approved ? '' : prompt('Reason for rejection:') || '';
  action$('verify_plan_step', {
    plan_id: planId,
    step: stepIndex,
    approved: approved,
    reason: reason,
  }).subscribe(data => {
    if (data.error) {
      addMsg('system', '\u274C ' + data.error);
    } else {
      addMsg('system', approved ? '\u2705 Step ' + stepIndex + ' approved' : '\u274C Step ' + stepIndex + ' rejected');
      loadPlans();
    }
  });
}
