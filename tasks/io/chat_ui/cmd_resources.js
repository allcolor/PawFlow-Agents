// ── Resource commands ───────────────────────────────────────────
// /flow, /task, /skill, /install, /uninstall, /resources, /activate, /deactivate,
// /share, /prompt, /service, /imgservice, /vidservice, /view
// Loaded before commands.js — all functions are global.

function _parseTaskAssignOptions(qargs, startIndex) {
  let interval = null, maxIter = 0, verifier = '', criteria = '';
  let maxBudget = '', maxTurnTime = '', maxTotalTime = '', maxReschedules = 0, autoAllow = false, interactive = false, context = '';
  const variables = {};
  for (let i = startIndex; i < qargs.length; i++) {
    if (qargs[i] === '--criteria' && qargs[i+1]) { criteria = qargs[++i]; }
    else if (qargs[i] === '--interval' && qargs[i+1]) { interval = qargs[++i]; }
    else if (qargs[i] === '--max' && qargs[i+1]) { maxIter = parseInt(qargs[++i]) || 0; }
    else if (qargs[i] === '--verifier' && qargs[i+1]) { verifier = stripTarget(qargs[++i]); }
    else if (qargs[i] === '--budget' && qargs[i+1]) { maxBudget = qargs[++i]; }
    else if (qargs[i] === '--turn-time' && qargs[i+1]) { maxTurnTime = qargs[++i]; }
    else if (qargs[i] === '--total-time' && qargs[i+1]) { maxTotalTime = qargs[++i]; }
    else if (qargs[i] === '--max-reschedules' && qargs[i+1]) { maxReschedules = parseInt(qargs[++i]) || 0; }
    else if (qargs[i] === '--context' && qargs[i+1]) { context = qargs[++i]; }
    else if (qargs[i] === '--auto-allow') { autoAllow = true; }
    else if (qargs[i] === '--interactive') { interactive = true; }
    else if (qargs[i] === '--var' && qargs[i+1]) {
      const kv = qargs[++i];
      const eq = kv.indexOf('=');
      if (eq > 0) variables[kv.substring(0, eq)] = kv.substring(eq + 1);
    }
  }
  return { interval, maxIter, verifier, criteria, maxBudget, maxTurnTime, maxTotalTime, maxReschedules, autoAllow, interactive, context, variables };
}

function _applyTaskAssignOptions(params, opts) {
  if (opts.criteria) params.criteria = opts.criteria;
  if (opts.interval != null) params.interval = opts.interval;
  if (opts.maxIter) params.max_iterations = opts.maxIter;
  if (opts.verifier) params.verifier = opts.verifier;
  if (opts.context) params.context = opts.context;
  if (Object.keys(opts.variables).length) params.variables = opts.variables;
  if (opts.maxBudget) params.max_budget = opts.maxBudget;
  if (opts.maxTurnTime) params.max_turn_time = opts.maxTurnTime;
  if (opts.maxTotalTime) params.max_total_time = opts.maxTotalTime;
  if (opts.maxReschedules) params.max_reschedules = opts.maxReschedules;
  if (opts.autoAllow) params.auto_allow = true;
  if (opts.interactive) params.interactive = true;
  return params;
}

function _taskAssignLooksInline(text) {
  const rest = text.replace(/^\/task\s+assign\s+/i, '');
  return /^(?:@"[^"]+"|@'[^']+'|@\S+|"[^"]+"|'[^']+'|\S+)\s+["']/.test(rest);
}

function cmdGoal(text, parts) {
  const qargs = parseQuotedArgs(text);
  let idx = 1;
  let agent = '';
  if (qargs[idx] && qargs[idx].startsWith('@')) agent = stripTarget(qargs[idx++]);
  const prompt = qargs[idx++] || '';
  if (!prompt) {
    addMsg('system', t('usageLine', { usage: '/goal [@agent] "objective" [--criteria "..."] [--interval XX] [--verifier @agent]' }));
    return true;
  }
  const opts = _parseTaskAssignOptions(qargs, idx);
  const params = _applyTaskAssignOptions({ prompt }, opts);
  if (agent) params.agent_name = agent;
  action$('goal', params).subscribe(data => {
    if (data.error) addMsg('error', data.error);
    else addMsg('system', data.result || t('taskAssigned'));
    loadResources();
  });
  return true;
}

function cmdTask(text, parts) {
  const sub = (parts[1] || 'status').toLowerCase();
  if (sub === 'create') {
    const rawText = text.replace(/^\/task\s+create\s+/i, '');
    const nameMatch = rawText.match(/^(\S+)/);
    const taskName = nameMatch ? nameMatch[1] : '';
    const afterName = rawText.substring(taskName.length).trim();
    function extractOpt(txt, opt) {
      const re = new RegExp('--' + opt + '\\s+(?:"([\\s\\S]*?)"|\'([\\s\\S]*?)\'|(\\S+))', 'i');
      const m = txt.match(re);
      return m ? (m[1] ?? m[2] ?? m[3] ?? '') : '';
    }
    let taskPrompt = extractOpt(afterName, 'prompt');
    let criteria = extractOpt(afterName, 'criteria');
    let interval = extractOpt(afterName, 'interval');
    let interactive = /(?:^|\s)--interactive(?:\s|$)/.test(afterName);
    if (!taskPrompt) {
      const qargs = parseQuotedArgs(text);
      taskPrompt = qargs[3] || '';
      for (let i = 4; i < qargs.length; i++) {
        if (qargs[i] === '--criteria' && qargs[i+1]) { if (!criteria) criteria = qargs[i+1]; i++; }
        else if (qargs[i] === '--interval' && qargs[i+1]) { if (!interval) interval = qargs[i+1]; i++; }
        else if (qargs[i] === '--interactive') interactive = true;
      }
    }
    if (!taskName || !taskPrompt) {
      addMsg('system', t('usageLine', { usage: '/task create <name> --prompt "..." [--criteria "..."] [--interval XX]\\n       /task create <name> "inline prompt" [--criteria "..."]' }));
      return true;
    }
    action$('create_task_def', {
      name: taskName,
      data: { prompt: taskPrompt, criteria, default_interval: interval || '6/1m', interactive },
    }).subscribe(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', t('taskDefinitionCreated', { name: taskName }));
    });
  } else if (sub === 'assign') {
    const qargs = parseQuotedArgs(text);
    const taskAgent = stripTarget(qargs[2] || '');
    const taskArg = qargs[3] || '';
    if (!taskAgent || !taskArg) {
      addMsg('system', t('usageLine', { usage: '/task assign @<agent> <task_def_name> [--interval N] [--max N] [--verifier @agent] [--var key=val]' }));
      return true;
    }
    const opts = _parseTaskAssignOptions(qargs, 4);
    const actionName = _taskAssignLooksInline(text) ? 'create_and_assign_task_def' : 'assign_task';
    const params = _applyTaskAssignOptions({ agent_name: taskAgent }, opts);
    if (actionName === 'create_and_assign_task_def') params.prompt = taskArg;
    else params.task_def_name = taskArg;
    action$(actionName, params).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', data.result || t('taskAssigned')); }
      loadResources();
    });
  } else if (sub === 'delete' || sub === 'del') {
    const taskName = parts[2] || '';
    if (!taskName) { addMsg('system', t('usageLine', { usage: '/task delete <task_def_name|task_id>' })); return true; }
    const isTaskInstance = taskName.startsWith('t_');
    if (isTaskInstance) {
      action$('delete_task', { task_id: taskName }).subscribe(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', t('taskInstanceDeleted', { name: taskName }));
      });
    } else {
      action$('delete_task_def', { name: taskName }).subscribe(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', t('taskDefinitionDeleted', { name: taskName }));
      });
    }
  } else if (sub === 'status' || sub === 'list') {
    const listAgent = stripTarget(parts[2] || '');
    const listParams = { include_library: true };
    if (listAgent) listParams.agent_name = listAgent;
    action$('task_status', listParams).subscribe(data => {
      const defs = data.definitions || [];
      const tasks = data.tasks || [];
      const lines = [];
      if (defs.length) {
        lines.push('**' + t('taskLibraryHeader') + '**');
        for (const d of defs) {
          lines.push('\u2022 `' + d.name + '` — ' + (d.description || d.prompt.substring(0, 60)) + ' [' + (d.default_interval || '6/1m') + ']');
        }
      }
      const formatTask = (task) => {
        let line = '\u2022 `' + (task.task_id || '?') + '` ' + task.agent + ': ' + task.task.substring(0, 80);
        const ivLabel = typeof task.interval === 'object' ? (task.interval.spec || task.interval.min + '-' + task.interval.max + 's') : task.interval + 's';
        const iterLabel = task.max_iterations > 0 ? (task.iterations + '/' + task.max_iterations) : ('' + task.iterations);
        line += ' [' + task.status + ', ' + t('taskIterLabel', { iter: iterLabel }) + ', ' + ivLabel + ']';
        if (task.task_def_name) line += ' (' + t('taskDefLabel', { name: task.task_def_name }) + ')';
        if (task.verifier) line += ' (' + t('taskVerifierLabel', { verifier: task.verifier }) + ')';
        if (task.last_result) line += '\n  ' + t('taskLastLabel', { result: task.last_result.substring(0, 100) });
        const limits = [];
        if (task.max_budget) limits.push(t('taskBudgetLimit', { budget: task.max_budget, used: (task.total_cost || 0).toFixed(4) }));
        if (task.timeout) limits.push(t('taskTurnLimit', { seconds: task.timeout }));
        if (task.max_total_time) limits.push(t('taskTotalLimit', { seconds: task.max_total_time }));
        if (task.max_reschedules) limits.push(t('taskRescheduleLimit', { count: task.reschedule_count || 0, max: task.max_reschedules }));
        if (limits.length) line += '\n  ' + t('taskLimitsLabel', { limits: limits.join(', ') });
        return line;
      };
      const activeTasks = tasks.filter(t => t.status === 'active' || t.status === 'paused');
      if (activeTasks.length) {
        if (lines.length) lines.push('');
        lines.push('**' + t('taskRunningHeader') + '**');
        for (const t of activeTasks) lines.push(formatTask(t));
      }
      if (!lines.length) addMsg('system', t('noTaskDefinitionsOrRunning'));
      else addMsg('system', lines.join('\n'));
    });
  } else if (sub === 'pause' || sub === 'resume' || sub === 'cancel') {
    const taskAgentRaw = parts[2];
    if (!taskAgentRaw) { addMsg('system', t('usageLine', { usage: '/task ' + sub + ' <task_id|@agent>' })); return true; }
    const taskAgent = stripTarget(taskAgentRaw);
    action$(sub + '_task', {
      task_id: taskAgent.startsWith('t_') ? taskAgent : '',
      agent_name: taskAgent.startsWith('t_') ? '' : taskAgent,
    }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', t('taskActionFor', { action: sub + 'd', target: taskAgent })); }
    });
  } else if (sub === 'edit' || sub === 'set') {
    const taskId = parts[2] || '';
    if (!taskId || !taskId.startsWith('t_')) {
      addMsg('system', t('usageLine', { usage: '/task edit <task_id> [--budget $X] [--turn-time Xm] [--total-time Xh] [--max-reschedules N] [--max N] [--interval X]' }));
      return true;
    }
    const eqargs = parseQuotedArgs(text);
    const editParams = { task_id: taskId };
    for (let i = 3; i < eqargs.length; i++) {
      if (eqargs[i] === '--budget' && eqargs[i+1]) { editParams.max_budget = eqargs[++i]; }
      else if (eqargs[i] === '--turn-time' && eqargs[i+1]) { editParams.max_turn_time = eqargs[++i]; }
      else if (eqargs[i] === '--total-time' && eqargs[i+1]) { editParams.max_total_time = eqargs[++i]; }
      else if (eqargs[i] === '--max-reschedules' && eqargs[i+1]) { editParams.max_reschedules = parseInt(eqargs[++i]) || 0; }
      else if (eqargs[i] === '--max' && eqargs[i+1]) { editParams.max_iterations = parseInt(eqargs[++i]) || 0; }
      else if (eqargs[i] === '--interval' && eqargs[i+1]) { editParams.interval = eqargs[++i]; }
    }
    action$('edit_task', editParams).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', t('taskUpdatedFields', { fields: (data.changed || []).join(', ') })); }
    });
  } else {
    addMsg('system', t('usageLine', { usage: '/task create | assign | list | edit | delete | pause | resume | cancel' }));
  }
  return true;
}

function cmdVidservice(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_video_services', {}).subscribe(data => {
      const services = Array.isArray(data) ? data : (data.services || []);
      if (!services.length) {
        addMsg('system', t('noVideoServicesDeployed'));
      } else {
        const lines = services.map(s => {
          let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
          if (s.selected_for && s.selected_for.length > 0) {
            line += ' \u2190 ' + t('selectedFor', { agents: s.selected_for.join(', ') });
          }
          return line;
        });
        addMsg('system', t('videoServicesAvailable') + '\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'select' && parts[2]) {
    const serviceName = stripTarget(parts[2]);
    const agentName = parts[3] ? stripTarget(parts[3]) : '';
    if (!agentName) { addMsg('system', t('usageLine', { usage: '/vidservice select @<service> @<agent|ALL>' })); return true; }
    action$('set_video_service', {
      service_name: serviceName, agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        const target = agentName === 'ALL' ? t('allAgents') : agentName;
        addMsg('system', t('videoServiceSetFor', { service: serviceName, target: target }));
      } else {
        addMsg('error', data.error || t('failedToSetVideoService'));
      }
    });
  } else if (sub === 'clear') {
    const agentName = stripTarget(parts[2] || '');
    action$('clear_video_service', {
      agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        addMsg('system', agentName
          ? t('videoServicePreferenceClearedFor', { agent: agentName })
          : t('allVideoServicePreferencesCleared'));
      } else {
        addMsg('error', data.error || t('failedToClear'));
      }
    });
  } else {
    addMsg('system', t('usageLine', { usage: '/vidservice list | select <name> [@agent] | clear [@agent]' }));
  }
  return true;
}

function cmdImgservice(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_image_services', {}).subscribe(data => {
      const services = Array.isArray(data) ? data : (data.services || []);
      if (!services.length) {
        addMsg('system', t('noImageServicesDeployed'));
      } else {
        const lines = services.map(s => {
          let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
          if (s.selected_for && s.selected_for.length > 0) {
            line += ' \u2190 ' + t('selectedFor', { agents: s.selected_for.join(', ') });
          }
          return line;
        });
        addMsg('system', t('imageServicesAvailable') + '\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'select' && parts[2]) {
    const serviceName = stripTarget(parts[2]);
    const agentName = parts[3] ? stripTarget(parts[3]) : '';
    if (!agentName) { addMsg('system', t('usageLine', { usage: '/imgservice select @<service> @<agent|ALL>' })); return true; }
    action$('set_image_service', {
      service_name: serviceName, agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        const target = agentName === 'ALL' ? t('allAgents') : agentName;
        addMsg('system', t('imageServiceSetFor', { service: serviceName, target: target }));
      } else {
        addMsg('error', data.error || t('failedToSetImageService'));
      }
    });
  } else if (sub === 'clear') {
    const agentName = stripTarget(parts[2] || '');
    action$('clear_image_service', {
      agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        addMsg('system', agentName
          ? t('imageServicePreferenceClearedFor', { agent: agentName })
          : t('allImageServicePreferencesCleared'));
      } else {
        addMsg('error', data.error || t('failedToClear'));
      }
    });
  } else {
    addMsg('system', t('usageLine', { usage: '/imgservice list | select <name> [@agent] | clear [@agent]' }));
  }
  return true;
}

function _stripAt(s) { return s ? s.replace(/^@/, '') : ''; }

// `/skill add` derives the manifest description from the first non-empty
// line of the body — used only as a fallback when no `::` separator is given.
function _skillShortDescription(body) {
  const lines = String(body || '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim().replace(/^#+/, '').trim();
    if (line) return line.slice(0, 200);
  }
  return '';
}

// Split a `/skill add|update` body into an explicit description and the
// instructions. The `::` separator marks the boundary; without it, the
// description falls back to the first non-empty line (legacy behaviour).
function _skillBodyParts(body) {
  const raw = String(body || '');
  const idx = raw.indexOf('::');
  if (idx >= 0) {
    return { description: raw.slice(0, idx).trim(),
             instructions: raw.slice(idx + 2).trim() };
  }
  return { description: _skillShortDescription(raw), instructions: raw.trim() };
}

function cmdSkill(text, parts) {
  // `--force` lets the user clear the human-review gate on their own skill.
  const force = parts.includes('--force');
  if (force) parts = parts.filter(p => p !== '--force');
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    cmdSkillList();
  } else if (sub === 'add' || sub === 'create') {
    const name = _stripAt(parts[2]);
    const { description, instructions } = _skillBodyParts(parts.slice(3).join(' '));
    if (!name || !instructions) { addMsg('system', t('usageLine', { usage: '/skill add [--force] @name <description> :: <instructions>' })); return true; }
    cmdResourceAction('create_skill', {name, description, instructions, force});
  } else if (sub === 'update') {
    const name = _stripAt(parts[2]);
    const { description, instructions } = _skillBodyParts(parts.slice(3).join(' '));
    if (!name || !instructions) { addMsg('system', t('usageLine', { usage: '/skill update [--force] @name <description> :: <instructions>' })); return true; }
    cmdResourceAction('update_skill', {name, description, instructions, force});
  } else if (sub === 'del' || sub === 'delete') {
    const name = _stripAt(parts[2]);
    if (!name) { addMsg('system', t('usageLine', { usage: '/skill del @name' })); return true; }
    cmdResourceAction('delete_skill', {name});
  } else if (sub === 'assign') {
    const agent = _stripAt(parts[2]);
    const skill = _stripAt(parts[3]);
    if (!agent || !skill) { addMsg('system', t('usageLine', { usage: '/skill assign @agent @skill' })); return true; }
    cmdResourceAction('assign_skill', {agent_name: agent, skill_name: skill}).then(() => {
      loadResources();
    });
  } else if (sub === 'unassign') {
    const agent = _stripAt(parts[2]);
    const skill = _stripAt(parts[3]);
    if (!agent || !skill) { addMsg('system', t('usageLine', { usage: '/skill unassign @agent @skill' })); return true; }
    cmdResourceAction('unassign_skill', {agent_name: agent, skill_name: skill}).then(() => {
      loadResources();
    });
  } else if (sub === 'assigned') {
    const agent = _stripAt(parts[2]);
    if (!agent) { addMsg('system', t('usageLine', { usage: '/skill assigned @agent' })); return true; }
    cmdSkillAssigned(agent);
  } else if (sub === 'run' || sub === 'search' || sub === 'import') {
    return tryServerCommand(text);
  } else {
    addMsg('system', t('usageLine', { usage: '/skill list | add [--force] @name <description> :: <instructions> | update [--force] @name <description> :: <instructions> | del @name | assign @agent @skill | unassign @agent @skill | assigned @agent | run [@agent] <name> [args...]' }));
  }
  return true;
}

function cmdAddSkill(text, parts) {
  const force = parts.includes('--force');
  if (force) parts = parts.filter(p => p !== '--force');
  const name = _stripAt(parts[1]);
  const { description, instructions } = _skillBodyParts(parts.slice(2).join(' '));
  if (!name || !instructions) { addMsg('system', t('usageLine', { usage: '/add-skill [--force] @name <description> :: <instructions>' })); return true; }
  cmdResourceAction('create_skill', {name, description, instructions, force});
  return true;
}

function cmdSkillAssigned(agentName) {
  action$('list_agent_skills', { agent_name: agentName }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    const skills = data.skills || [];
    if (!skills.length) {
      addMsg('system', t('agentHasNoAssignedSkills', { agent: agentName }));
      return;
    }
    let msg = t('skillsAssignedTo', { agent: agentName }) + '\n';
    skills.forEach(s => {
      msg += `  • **${s.name}**` + (s.description ? ` — ${s.description}` : '') + '\n';
    });
    addMsg('system', msg);
  });
}

function cmdResources() {
  cmdListResources();
  return true;
}

function cmdActivate(text, parts) {
  const rtype = parts[1];
  const rname = stripTarget(parts[2]);
  if (!rtype || !rname) { addMsg('system', t('usageLine', { usage: '/activate <agent|mcp> @<name>' })); return true; }
  cmdResourceAction('activate_resource', {resource_type: rtype, name: rname});
  return true;
}

function cmdDeactivate(text, parts) {
  const rtype = parts[1];
  const rname = stripTarget(parts[2]);
  if (!rtype || !rname) { addMsg('system', t('usageLine', { usage: '/deactivate <agent|mcp> @<name>' })); return true; }
  cmdResourceAction('deactivate_resource', {resource_type: rtype, name: rname});
  return true;
}

function cmdShare(text, parts) {
  const rtype = parts[1];
  const rname = stripTarget(parts[2]);
  const targetConv = parts[3];
  if (!rtype || !rname || !targetConv) {
    addMsg('system', t('usageLine', { usage: '/share <agent|skill|mcp> <name> <conversation_id>' }));
    return true;
  }
  cmdResourceAction('share_resource', {
    resource_type: rtype, name: rname, target_conversation_id: targetConv
  });
  return true;
}

function cmdView(text, parts) {
  const filename = parts.slice(1).join(' ');
  if (!filename) { addMsg('system', t('usageLine', { usage: '/view <filename>' })); return true; }
  openFileViewer(filename);
  return true;
}

function cmdService(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    cmdServiceList();
  } else if (sub === 'install') {
    const svcType = parts[2];
    const svcName = parts[3];
    const configStr = parts.slice(4).join(' ');
    if (!svcType || !svcName) {
      addMsg('system', t('usageLine', { usage: '/service install <type> <name> [key=val,key2=val2,...]' }));
      return true;
    }
    cmdServiceAction('service_install', {
      service_type: svcType, service_name: svcName, config_str: configStr
    });
  } else if (sub === 'uninstall') {
    const svcName = parts[2];
    if (!svcName) { addMsg('system', t('usageLine', { usage: '/service uninstall <name>' })); return true; }
    cmdServiceAction('service_uninstall', {service_id: svcName});
  } else if (sub === 'enable') {
    const svcName = parts[2];
    if (!svcName) { addMsg('system', t('usageLine', { usage: '/service enable <name>' })); return true; }
    cmdServiceAction('service_enable', {service_id: svcName});
  } else if (sub === 'disable') {
    const svcName = parts[2];
    if (!svcName) { addMsg('system', t('usageLine', { usage: '/service disable <name>' })); return true; }
    cmdServiceAction('service_disable', {service_id: svcName});
  } else {
    addMsg('system', t('usageLine', { usage: '/service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>' }));
  }
  return true;
}

function cmdFlow(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_conv_flows', {}).subscribe(data => {
      const flows = data.flows || [];
      if (!flows.length) { addMsg('system', t('noDeployedFlows')); }
      else {
        const lines = flows.map(function(f) { return (f.status === 'running' ? '\u25b6' : '\u23f9') + ' ' + f.id + ' \u2014 ' + f.name + ' [' + f.status + ']'; });
        addMsg('system', t('flowsHeader') + '\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'templates') {
    action$('list_available_flows', {}).subscribe(data => {
      const templates = data.templates || [];
      if (!templates.length) { addMsg('system', t('noFlowTemplates')); }
      else {
        const lines = templates.map(function(tmpl) { return tmpl.id + (tmpl.version ? ' v' + tmpl.version : '') + ' \u2014 ' + tmpl.name + ' (' + t('taskCount', { count: tmpl.tasks_count }) + ')'; });
        addMsg('system', t('flowTemplatesHeader') + '\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'deploy') {
    const templateId = parts[2];
    const scope = parts[3] || 'user';
    if (!templateId) { addMsg('system', t('usageLine', { usage: '/flow deploy <template_id> [user|conversation]' })); return true; }
    action$('deploy_flow', { template_id: templateId, scope }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', t('flowDeployed', { id: data.instance_id || '?', scope: scope })); }
    });
  } else if (sub === 'start') {
    const iid = parts[2];
    if (!iid) { addMsg('system', t('usageLine', { usage: '/flow start <instance_id> [key=val ...]' })); return true; }
    const overrides = {};
    for (let i = 3; i < parts.length; i++) {
      if (parts[i].includes('=')) {
        const [k, ...v] = parts[i].split('=');
        overrides[k] = v.join('=');
      }
    }
    const startFlow = () => {
      action$('start_flow', { instance_id: iid }).subscribe(data => {
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', t('flowStarted', { id: iid })); }
      });
    };
    if (Object.keys(overrides).length) {
      action$('update_flow_params', { instance_id: iid, parameters: overrides }).subscribe(() => startFlow());
    } else {
      startFlow();
    }
  } else if (sub === 'stop') {
    const iid = parts[2];
    if (!iid) { addMsg('system', t('usageLine', { usage: '/flow stop <instance_id>' })); return true; }
    action$('stop_flow', { instance_id: iid }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', t('flowStopped', { id: iid })); }
    });
  } else if (sub === 'params') {
    const iid = parts[2];
    if (!iid) { addMsg('system', t('usageLine', { usage: '/flow params <instance_id>' })); return true; }
    action$('get_flow_instance', { instance_id: iid }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else {
        const params = { ...(data.template_parameters || {}), ...(data.parameters || {}) };
        const lines = Object.entries(params).map(function(entry) { return '  ' + entry[0] + ' = ' + entry[1]; });
        addMsg('system', t('flowStatusHeader', { name: data.flow_name || iid, status: data.status || '?' }) + '\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'undeploy') {
    const iid = parts[2];
    if (!iid) { addMsg('system', t('usageLine', { usage: '/flow undeploy <instance_id>' })); return true; }
    action$('undeploy_flow', { instance_id: iid }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', t('flowUndeployed', { id: iid })); }
    });
  } else if (sub === 'promote') {
    const iid = parts[2];
    const targetScope = parts[3] || 'user';
    if (!iid) { addMsg('system', t('usageLine', { usage: '/flow promote <instance_id> [user|conversation|global]' })); return true; }
    const payload = { instance_id: iid, target_scope: targetScope };
    if (targetScope === 'conversation' && typeof conversationId !== 'undefined' && conversationId) payload.conversation_id = conversationId;
    action$('promote_flow', payload, { skipConversationId: targetScope !== 'conversation' }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', t('flowPromotedToUserScope', { id: iid })); }
    });
  } else {
    addMsg('system', t('usageLine', { usage: '/flow list | templates | deploy | start | stop | params | undeploy | promote' }));
  }
  return true;
}

function cmdInstall() {
  addMsg('system', t('installToolInstructions'));
  return true;
}

function cmdUninstall(text, parts) {
  const toolName = parts[1];
  if (!toolName) { addMsg('system', t('usageLine', { usage: '/uninstall <tool_name>' })); return true; }
  cmdUninstallTool(toolName);
  return true;
}
