// ── Resource commands ───────────────────────────────────────────
// /flow, /task, /skill, /install, /uninstall, /resources, /activate, /deactivate,
// /share, /prompt, /service, /imgservice, /vidservice, /view
// Loaded before commands.js — all functions are global.

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
    if (!taskPrompt) {
      const qargs = parseQuotedArgs(text);
      taskPrompt = qargs[3] || '';
      if (!criteria) {
        for (let i = 4; i < qargs.length; i++) {
          if (qargs[i] === '--criteria' && qargs[i+1]) criteria = qargs[++i];
          else if (qargs[i] === '--interval' && qargs[i+1]) interval = qargs[++i];
        }
      }
    }
    if (!taskName || !taskPrompt) {
      addMsg('system', 'Usage: /task create <name> --prompt "..." [--criteria "..."] [--interval XX]\n       /task create <name> "inline prompt" [--criteria "..."]');
      return true;
    }
    action$('create_task_def', {
      name: taskName,
      data: { prompt: taskPrompt, criteria, default_interval: interval || '6/1m' },
    }).subscribe(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', `Task definition '${taskName}' created.`);
    });
  } else if (sub === 'assign') {
    const qargs = parseQuotedArgs(text);
    const taskAgent = stripTarget(qargs[2] || '');
    const taskArg = qargs[3] || '';
    if (!taskAgent || !taskArg) {
      addMsg('system', 'Usage: /task assign @<agent> <task_def_name> [--interval N] [--max N] [--verifier @agent] [--var key=val]');
      return true;
    }
    let interval = null, maxIter = 0, verifier = '';
    let maxBudget = '', maxTurnTime = '', maxTotalTime = '', maxReschedules = 0, autoAllow = false;
    const variables = {};
    for (let i = 4; i < qargs.length; i++) {
      if (qargs[i] === '--interval' && qargs[i+1]) { interval = qargs[++i]; }
      else if (qargs[i] === '--max' && qargs[i+1]) { maxIter = parseInt(qargs[++i]) || 0; }
      else if (qargs[i] === '--verifier' && qargs[i+1]) { verifier = stripTarget(qargs[++i]); }
      else if (qargs[i] === '--budget' && qargs[i+1]) { maxBudget = qargs[++i]; }
      else if (qargs[i] === '--turn-time' && qargs[i+1]) { maxTurnTime = qargs[++i]; }
      else if (qargs[i] === '--total-time' && qargs[i+1]) { maxTotalTime = qargs[++i]; }
      else if (qargs[i] === '--max-reschedules' && qargs[i+1]) { maxReschedules = parseInt(qargs[++i]) || 0; }
      else if (qargs[i] === '--auto-allow') { autoAllow = true; }
      else if (qargs[i] === '--var' && qargs[i+1]) {
        const kv = qargs[++i];
        const eq = kv.indexOf('=');
        if (eq > 0) variables[kv.substring(0, eq)] = kv.substring(eq + 1);
      }
    }
    action$('assign_task', {
      agent_name: taskAgent, max_iterations: maxIter, verifier,
      task_def_name: taskArg,
      ...(interval != null ? { interval } : {}),
      ...(Object.keys(variables).length ? { variables } : {}),
      ...(maxBudget ? { max_budget: maxBudget } : {}),
      ...(maxTurnTime ? { max_turn_time: maxTurnTime } : {}),
      ...(maxTotalTime ? { max_total_time: maxTotalTime } : {}),
      ...(maxReschedules ? { max_reschedules: maxReschedules } : {}),
      ...(autoAllow ? { auto_allow: true } : {}),
    }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', data.result || 'Task assigned.'); }
    });
  } else if (sub === 'delete' || sub === 'del') {
    const taskName = parts[2] || '';
    if (!taskName) { addMsg('system', 'Usage: /task delete <task_def_name|task_id>'); return true; }
    const isTaskInstance = taskName.startsWith('t_');
    if (isTaskInstance) {
      action$('delete_task', { task_id: taskName }).subscribe(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', `Task instance '${taskName}' deleted.`);
      });
    } else {
      action$('delete_task_def', { name: taskName }).subscribe(data => {
        if (data.error) addMsg('error', data.error);
        else addMsg('system', `Task definition '${taskName}' deleted.`);
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
        lines.push('**Library:**');
        for (const d of defs) {
          lines.push('\u2022 `' + d.name + '` — ' + (d.description || d.prompt.substring(0, 60)) + ' [' + (d.default_interval || '6/1m') + ']');
        }
      }
      const formatTask = (t) => {
        let line = '\u2022 `' + (t.task_id || '?') + '` ' + t.agent + ': ' + t.task.substring(0, 80);
        const ivLabel = typeof t.interval === 'object' ? (t.interval.spec || t.interval.min + '-' + t.interval.max + 's') : t.interval + 's';
        const iterLabel = t.max_iterations > 0 ? (t.iterations + '/' + t.max_iterations) : ('' + t.iterations);
        line += ' [' + t.status + ', iter ' + iterLabel + ', ' + ivLabel + ']';
        if (t.task_def_name) line += ' (def: ' + t.task_def_name + ')';
        if (t.verifier) line += ' (verifier: ' + t.verifier + ')';
        if (t.last_result) line += '\n  Last: ' + t.last_result.substring(0, 100);
        const limits = [];
        if (t.max_budget) limits.push('budget: $' + t.max_budget + ' (used: $' + (t.total_cost || 0).toFixed(4) + ')');
        if (t.timeout) limits.push('turn: ' + t.timeout + 's');
        if (t.max_total_time) limits.push('total: ' + t.max_total_time + 's');
        if (t.max_reschedules) limits.push('reschedules: ' + (t.reschedule_count || 0) + '/' + t.max_reschedules);
        if (limits.length) line += '\n  Limits: ' + limits.join(', ');
        return line;
      };
      const activeTasks = tasks.filter(t => t.status === 'active' || t.status === 'paused');
      if (activeTasks.length) {
        if (lines.length) lines.push('');
        lines.push('**Running:**');
        for (const t of activeTasks) lines.push(formatTask(t));
      }
      if (!lines.length) addMsg('system', 'No task definitions or running tasks.');
      else addMsg('system', lines.join('\n'));
    });
  } else if (sub === 'pause' || sub === 'resume' || sub === 'cancel') {
    const taskAgentRaw = parts[2];
    if (!taskAgentRaw) { addMsg('system', 'Usage: /task ' + sub + ' <task_id|@agent>'); return true; }
    const taskAgent = stripTarget(taskAgentRaw);
    action$(sub + '_task', {
      task_id: taskAgent.startsWith('t_') ? taskAgent : '',
      agent_name: taskAgent.startsWith('t_') ? '' : taskAgent,
    }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Task ' + sub + 'd for ' + taskAgent + '.'); }
    });
  } else if (sub === 'edit' || sub === 'set') {
    const taskId = parts[2] || '';
    if (!taskId || !taskId.startsWith('t_')) {
      addMsg('system', 'Usage: /task edit <task_id> [--budget $X] [--turn-time Xm] [--total-time Xh] [--max-reschedules N] [--max N] [--interval X]');
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
      else { addMsg('system', 'Task updated: ' + (data.changed || []).join(', ')); }
    });
  } else {
    addMsg('system', 'Usage: /task create | assign | list | edit | delete | pause | resume | cancel');
  }
  return true;
}

function cmdVidservice(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_video_services', {}).subscribe(data => {
      const services = Array.isArray(data) ? data : (data.services || []);
      if (!services.length) {
        addMsg('system', 'No video generation services deployed.');
      } else {
        const lines = services.map(s => {
          let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
          if (s.selected_for && s.selected_for.length > 0) {
            line += ' \u2190 selected for: ' + s.selected_for.join(', ');
          }
          return line;
        });
        addMsg('system', 'Video services available:\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'select' && parts[2]) {
    const serviceName = stripTarget(parts[2]);
    const agentName = parts[3] ? stripTarget(parts[3]) : '';
    if (!agentName) { addMsg('system', 'Usage: /vidservice select @<service> @<agent|ALL>'); return true; }
    action$('set_video_service', {
      service_name: serviceName, agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        const target = agentName === 'ALL' ? 'all agents' : agentName;
        addMsg('system', 'Video service set to "' + serviceName + '" for ' + target + '.');
      } else {
        addMsg('error', data.error || 'Failed to set video service');
      }
    });
  } else if (sub === 'clear') {
    const agentName = stripTarget(parts[2] || '');
    action$('clear_video_service', {
      agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        addMsg('system', agentName
          ? 'Video service preference cleared for ' + agentName + '.'
          : 'All video service preferences cleared.');
      } else {
        addMsg('error', data.error || 'Failed to clear');
      }
    });
  } else {
    addMsg('system', 'Usage: /vidservice list | select <name> [@agent] | clear [@agent]');
  }
  return true;
}

function cmdImgservice(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_image_services', {}).subscribe(data => {
      const services = Array.isArray(data) ? data : (data.services || []);
      if (!services.length) {
        addMsg('system', 'No image generation services deployed.');
      } else {
        const lines = services.map(s => {
          let line = '  \u2022 ' + s.id + ' (' + s.type + ', ' + s.scope + ')';
          if (s.selected_for && s.selected_for.length > 0) {
            line += ' \u2190 selected for: ' + s.selected_for.join(', ');
          }
          return line;
        });
        addMsg('system', 'Image services available:\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'select' && parts[2]) {
    const serviceName = stripTarget(parts[2]);
    const agentName = parts[3] ? stripTarget(parts[3]) : '';
    if (!agentName) { addMsg('system', 'Usage: /imgservice select @<service> @<agent|ALL>'); return true; }
    action$('set_image_service', {
      service_name: serviceName, agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        const target = agentName === 'ALL' ? 'all agents' : agentName;
        addMsg('system', 'Image service set to "' + serviceName + '" for ' + target + '.');
      } else {
        addMsg('error', data.error || 'Failed to set image service');
      }
    });
  } else if (sub === 'clear') {
    const agentName = stripTarget(parts[2] || '');
    action$('clear_image_service', {
      agent_name: agentName,
    }).subscribe(data => {
      if (data.ok) {
        addMsg('system', agentName
          ? 'Image service preference cleared for ' + agentName + '.'
          : 'All image service preferences cleared.');
      } else {
        addMsg('error', data.error || 'Failed to clear');
      }
    });
  } else {
    addMsg('system', 'Usage: /imgservice list | select <name> [@agent] | clear [@agent]');
  }
  return true;
}

function _stripAt(s) { return s ? s.replace(/^@/, '') : ''; }

function cmdSkill(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    cmdSkillList();
  } else if (sub === 'add' || sub === 'create') {
    const name = _stripAt(parts[2]);
    const prompt = parts.slice(3).join(' ');
    if (!name || !prompt) { addMsg('system', 'Usage: /skill add @name <prompt>'); return true; }
    cmdResourceAction('create_skill', {name, prompt});
  } else if (sub === 'del' || sub === 'delete') {
    const name = _stripAt(parts[2]);
    if (!name) { addMsg('system', 'Usage: /skill del @name'); return true; }
    cmdResourceAction('delete_skill', {name});
  } else if (sub === 'assign') {
    const agent = _stripAt(parts[2]);
    const skill = _stripAt(parts[3]);
    if (!agent || !skill) { addMsg('system', 'Usage: /skill assign @agent @skill'); return true; }
    cmdResourceAction('assign_skill', {agent_name: agent, skill_name: skill}).then(() => {
      loadResources();
    });
  } else if (sub === 'unassign') {
    const agent = _stripAt(parts[2]);
    const skill = _stripAt(parts[3]);
    if (!agent || !skill) { addMsg('system', 'Usage: /skill unassign @agent @skill'); return true; }
    cmdResourceAction('unassign_skill', {agent_name: agent, skill_name: skill}).then(() => {
      loadResources();
    });
  } else if (sub === 'assigned') {
    const agent = _stripAt(parts[2]);
    if (!agent) { addMsg('system', 'Usage: /skill assigned @agent'); return true; }
    cmdSkillAssigned(agent);
  } else {
    addMsg('system', 'Usage: /skill list | add @name <prompt> | del @name | assign @agent @skill | unassign @agent @skill | assigned @agent');
  }
  return true;
}

function cmdAddSkill(text, parts) {
  const name = _stripAt(parts[1]);
  const prompt = parts.slice(2).join(' ');
  if (!name || !prompt) { addMsg('system', 'Usage: /add-skill @name <prompt>'); return true; }
  cmdResourceAction('create_skill', {name, prompt});
  return true;
}

function cmdSkillAssigned(agentName) {
  action$('list_agent_skills', { agent_name: agentName }).subscribe(data => {
    if (data.error) { addMsg('error', data.error); return; }
    const skills = data.skills || [];
    if (!skills.length) {
      addMsg('system', `Agent **${agentName}** has no assigned skills.`);
      return;
    }
    let msg = `Skills assigned to **${agentName}**:\n`;
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
  if (!rtype || !rname) { addMsg('system', 'Usage: /activate <agent|skill|mcp> @<name>'); return true; }
  cmdResourceAction('activate_resource', {resource_type: rtype, name: rname});
  return true;
}

function cmdDeactivate(text, parts) {
  const rtype = parts[1];
  const rname = stripTarget(parts[2]);
  if (!rtype || !rname) { addMsg('system', 'Usage: /deactivate <agent|skill|mcp> @<name>'); return true; }
  cmdResourceAction('deactivate_resource', {resource_type: rtype, name: rname});
  return true;
}

function cmdShare(text, parts) {
  const rtype = parts[1];
  const rname = stripTarget(parts[2]);
  const targetConv = parts[3];
  if (!rtype || !rname || !targetConv) {
    addMsg('system', 'Usage: /share <agent|skill|mcp> <name> <conversation_id>');
    return true;
  }
  cmdResourceAction('share_resource', {
    resource_type: rtype, name: rname, target_conversation_id: targetConv
  });
  return true;
}

function cmdView(text, parts) {
  const filename = parts.slice(1).join(' ');
  if (!filename) { addMsg('system', 'Usage: /view <filename>'); return true; }
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
      addMsg('system', 'Usage: /service install <type> <name> [key=val,key2=val2,...]');
      return true;
    }
    cmdServiceAction('service_install', {
      service_type: svcType, service_name: svcName, config_str: configStr
    });
  } else if (sub === 'uninstall') {
    const svcName = parts[2];
    if (!svcName) { addMsg('system', 'Usage: /service uninstall <name>'); return true; }
    cmdServiceAction('service_uninstall', {service_id: svcName});
  } else if (sub === 'enable') {
    const svcName = parts[2];
    if (!svcName) { addMsg('system', 'Usage: /service enable <name>'); return true; }
    cmdServiceAction('service_enable', {service_id: svcName});
  } else if (sub === 'disable') {
    const svcName = parts[2];
    if (!svcName) { addMsg('system', 'Usage: /service disable <name>'); return true; }
    cmdServiceAction('service_disable', {service_id: svcName});
  } else {
    addMsg('system', 'Usage: /service list | install <type> <name> [config] | uninstall <name> | enable <name> | disable <name>');
  }
  return true;
}

function cmdFlow(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_conv_flows', {}).subscribe(data => {
      const flows = data.flows || [];
      if (!flows.length) { addMsg('system', 'No deployed flows.'); }
      else {
        const lines = flows.map(function(f) { return (f.status === 'running' ? '\u25b6' : '\u23f9') + ' ' + f.id + ' \u2014 ' + f.name + ' [' + f.status + ']'; });
        addMsg('system', 'Flows:\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'templates') {
    action$('list_available_flows', {}).subscribe(data => {
      const templates = data.templates || [];
      if (!templates.length) { addMsg('system', 'No flow templates.'); }
      else {
        const lines = templates.map(function(tmpl) { return tmpl.id + (tmpl.version ? ' v' + tmpl.version : '') + ' \u2014 ' + tmpl.name + ' (' + tmpl.tasks_count + ' tasks)'; });
        addMsg('system', 'Flow templates:\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'deploy') {
    const templateId = parts[2];
    const scope = parts[3] || 'user';
    if (!templateId) { addMsg('system', 'Usage: /flow deploy <template_id> [user|conversation]'); return true; }
    action$('deploy_flow', { template_id: templateId, scope }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Deployed: ' + (data.instance_id || '?') + ' (' + scope + ')'); }
    });
  } else if (sub === 'start') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow start <instance_id> [key=val ...]'); return true; }
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
        else { addMsg('system', 'Flow \'' + iid + '\' started'); }
      });
    };
    if (Object.keys(overrides).length) {
      action$('update_flow_params', { instance_id: iid, parameters: overrides }).subscribe(() => startFlow());
    } else {
      startFlow();
    }
  } else if (sub === 'stop') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow stop <instance_id>'); return true; }
    action$('stop_flow', { instance_id: iid }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Flow \'' + iid + '\' stopped'); }
    });
  } else if (sub === 'params') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow params <instance_id>'); return true; }
    action$('get_flow_instance', { instance_id: iid }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else {
        const params = { ...(data.template_parameters || {}), ...(data.parameters || {}) };
        const lines = Object.entries(params).map(function(entry) { return '  ' + entry[0] + ' = ' + entry[1]; });
        addMsg('system', 'Flow ' + (data.flow_name || iid) + ' [' + (data.status || '?') + ']:\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'undeploy') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow undeploy <instance_id>'); return true; }
    action$('undeploy_flow', { instance_id: iid }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Flow \'' + iid + '\' undeployed'); }
    });
  } else if (sub === 'promote') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow promote <instance_id>'); return true; }
    action$('promote_flow', { instance_id: iid, target_scope: 'user' }).subscribe(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Flow \'' + iid + '\' promoted to user scope'); }
    });
  } else {
    addMsg('system', 'Usage: /flow list | templates | deploy | start | stop | params | undeploy | promote');
  }
  return true;
}

function cmdPrompt(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    action$('list_skills', {}).subscribe(data => {
      const skills = data.skills || [];
      if (!skills.length) { addMsg('system', 'No skills.'); }
      else {
        const lines = skills.map(function(s) { return '\u2022 ' + s.name + ': ' + (s.description || s.preview || '').slice(0, 60); });
        addMsg('system', 'Skills:\n' + lines.join('\n'));
      }
    });
  } else if (sub === 'use') {
    const name = parts[2] || '';
    if (!name) { addMsg('system', 'Usage: /skill use <name>'); return true; }
    action$('get_skill', { name }).subscribe(data => {
      if (data.prompt) { addMsg('system', 'Skill \'' + name + '\':\n' + data.prompt); }
      else { addMsg('error', 'Skill \'' + name + '\' not found'); }
    });
  } else {
    addMsg('system', 'Usage: /skill list | use <name>');
  }
  return true;
}

function cmdInstall() {
  addMsg('system', 'To install a tool, drag & drop a .py file into the chat or paste the code with:\n/install filename.py\n```python\n# your code here\n```');
  return true;
}

function cmdUninstall(text, parts) {
  const toolName = parts[1];
  if (!toolName) { addMsg('system', 'Usage: /uninstall <tool_name>'); return true; }
  cmdUninstallTool(toolName);
  return true;
}
