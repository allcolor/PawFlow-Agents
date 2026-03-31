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
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'create_task_def',
        name: taskName,
        data: { prompt: taskPrompt, criteria, default_interval: interval || '6/1m' },
      }),
    }).then(r => r.json()).then(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', `Task definition '${taskName}' created.`);
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'assign') {
    const qargs = parseQuotedArgs(text);
    const taskAgent = stripTarget(qargs[2] || '');
    const taskArg = qargs[3] || '';
    if (!taskAgent || !taskArg) {
      addMsg('system', 'Usage: /task assign @<agent> <taskname> [--interval N]\n       /task assign @<agent> "<inline description>" [--criteria "..."] [--interval N]');
      return true;
    }
    let interval = null, maxIter = 50, verifier = '', criteria = '';
    const variables = {};
    for (let i = 4; i < qargs.length; i++) {
      if (qargs[i] === '--interval' && qargs[i+1]) { interval = qargs[++i]; }
      else if (qargs[i] === '--max' && qargs[i+1]) { maxIter = parseInt(qargs[++i]) || 50; }
      else if (qargs[i] === '--verifier' && qargs[i+1]) { verifier = stripTarget(qargs[++i]); }
      else if (qargs[i] === '--criteria' && qargs[i+1]) { criteria = qargs[++i]; }
      else if (qargs[i] === '--var' && qargs[i+1]) {
        const kv = qargs[++i];
        const eq = kv.indexOf('=');
        if (eq > 0) variables[kv.substring(0, eq)] = kv.substring(eq + 1);
      }
    }
    const isLibrary = !taskArg.includes(' ') && !criteria;
    const body = {
      action: 'assign_task', conversation_id: conversationId,
      agent_name: taskAgent, max_iterations: maxIter, verifier,
      ...(interval != null ? { interval } : {}),
      ...(Object.keys(variables).length ? { variables } : {}),
    };
    if (isLibrary) {
      body.task_def_name = taskArg;
    } else {
      body.task = taskArg;
      body.completion_criteria = criteria;
    }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', data.result || 'Task assigned.'); }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'delete' || sub === 'del') {
    const taskName = parts[2] || '';
    if (!taskName) { addMsg('system', 'Usage: /task delete <taskname>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'delete_task_def',
        name: taskName,
      }),
    }).then(r => r.json()).then(data => {
      if (data.error) addMsg('error', data.error);
      else addMsg('system', `Task definition '${taskName}' deleted.`);
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'status' || sub === 'list') {
    const listAgent = stripTarget(parts[2] || '');
    const listBody = { action: 'task_status', conversation_id: conversationId, include_library: true };
    if (listAgent) listBody.agent_name = listAgent;
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify(listBody),
    }).then(r => r.json()).then(data => {
      const defs = data.definitions || [];
      const tasks = data.tasks || [];
      const lines = [];
      if (defs.length) {
        lines.push('**Library:**');
        for (const d of defs) {
          lines.push('\u2022 `' + d.name + '` — ' + (d.description || d.prompt.substring(0, 60)) + ' [' + (d.default_interval || '6/1m') + ']');
        }
      }
      if (tasks.length) {
        if (lines.length) lines.push('');
        lines.push('**Running:**');
        for (const t of tasks) {
          let line = '\u2022 `' + (t.task_id || '?') + '` ' + t.agent + ': ' + t.task.substring(0, 80);
          const ivLabel = typeof t.interval === 'object' ? (t.interval.spec || t.interval.min + '-' + t.interval.max + 's') : t.interval + 's';
          line += ' [' + t.status + ', iter ' + t.iterations + '/' + t.max_iterations + ', ' + ivLabel + ']';
          if (t.task_def_name) line += ' (def: ' + t.task_def_name + ')';
          if (t.verifier) line += ' (verifier: ' + t.verifier + ')';
          if (t.last_result) line += '\n  Last: ' + t.last_result.substring(0, 100);
          lines.push(line);
        }
      }
      if (!lines.length) addMsg('system', 'No task definitions or running tasks.');
      else addMsg('system', lines.join('\n'));
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'pause' || sub === 'resume' || sub === 'cancel') {
    const taskAgentRaw = parts[2];
    if (!taskAgentRaw) { addMsg('system', 'Usage: /task ' + sub + ' <task_id|@agent>'); return true; }
    const taskAgent = stripTarget(taskAgentRaw);
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: sub + '_task', conversation_id: conversationId,
        task_id: taskAgent.startsWith('t_') ? taskAgent : '',
        agent_name: taskAgent.startsWith('t_') ? '' : taskAgent,
      }),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Task ' + sub + 'd for ' + taskAgent + '.'); }
    }).catch(e => addMsg('error', e.message));
  } else {
    addMsg('system', 'Usage: /task create | assign | list | delete | pause | resume | cancel');
  }
  return true;
}

function cmdVidservice(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_video_services', conversation_id: conversationId }),
    }).then(r => r.json()).then(services => {
      if (!Array.isArray(services) || services.length === 0) {
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
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'select' && parts[2]) {
    const serviceName = parts[2];
    const agentName = parts[3] ? stripTarget(parts[3]) : '*';
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'set_video_service', conversation_id: conversationId,
        service_name: serviceName, agent_name: agentName,
      }),
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        const target = agentName === '*' ? 'all agents' : agentName;
        addMsg('system', 'Video service set to "' + serviceName + '" for ' + target + '.');
      } else {
        addMsg('error', data.error || 'Failed to set video service');
      }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'clear') {
    const agentName = stripTarget(parts[2] || '');
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'clear_video_service', conversation_id: conversationId,
        agent_name: agentName,
      }),
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        addMsg('system', agentName
          ? 'Video service preference cleared for ' + agentName + '.'
          : 'All video service preferences cleared.');
      } else {
        addMsg('error', data.error || 'Failed to clear');
      }
    }).catch(e => addMsg('error', e.message));
  } else {
    addMsg('system', 'Usage: /vidservice list | select <name> [@agent] | clear [@agent]');
  }
  return true;
}

function cmdImgservice(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_image_services', conversation_id: conversationId }),
    }).then(r => r.json()).then(services => {
      if (!Array.isArray(services) || services.length === 0) {
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
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'select' && parts[2]) {
    const serviceName = parts[2];
    const agentName = parts[3] ? stripTarget(parts[3]) : '*';
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'set_image_service', conversation_id: conversationId,
        service_name: serviceName, agent_name: agentName,
      }),
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        const target = agentName === '*' ? 'all agents' : agentName;
        addMsg('system', 'Image service set to "' + serviceName + '" for ' + target + '.');
      } else {
        addMsg('error', data.error || 'Failed to set image service');
      }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'clear') {
    const agentName = stripTarget(parts[2] || '');
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({
        action: 'clear_image_service', conversation_id: conversationId,
        agent_name: agentName,
      }),
    }).then(r => r.json()).then(data => {
      if (data.ok) {
        addMsg('system', agentName
          ? 'Image service preference cleared for ' + agentName + '.'
          : 'All image service preferences cleared.');
      } else {
        addMsg('error', data.error || 'Failed to clear');
      }
    }).catch(e => addMsg('error', e.message));
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

async function cmdSkillAssigned(agentName) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_agent_skills', agent_name: agentName, conversation_id: conversationId }),
    });
    const data = await resp.json();
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
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

function cmdResources() {
  cmdListResources();
  return true;
}

function cmdActivate(text, parts) {
  const rtype = parts[1];
  const rname = parts[2];
  if (!rtype || !rname) { addMsg('system', 'Usage: /activate <agent|skill|mcp> <name>'); return true; }
  cmdResourceAction('activate_resource', {resource_type: rtype, name: rname});
  return true;
}

function cmdDeactivate(text, parts) {
  const rtype = parts[1];
  const rname = parts[2];
  if (!rtype || !rname) { addMsg('system', 'Usage: /deactivate <agent|skill|mcp> <name>'); return true; }
  cmdResourceAction('deactivate_resource', {resource_type: rtype, name: rname});
  return true;
}

function cmdShare(text, parts) {
  const rtype = parts[1];
  const rname = parts[2];
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
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_conv_flows' }),
    }).then(r => r.json()).then(data => {
      const flows = data.flows || [];
      if (!flows.length) { addMsg('system', 'No deployed flows.'); }
      else {
        const lines = flows.map(function(f) { return (f.status === 'running' ? '\u25b6' : '\u23f9') + ' ' + f.id + ' \u2014 ' + f.name + ' [' + f.status + ']'; });
        addMsg('system', 'Flows:\n' + lines.join('\n'));
      }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'templates') {
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_available_flows' }),
    }).then(r => r.json()).then(data => {
      const templates = data.templates || [];
      if (!templates.length) { addMsg('system', 'No flow templates.'); }
      else {
        const lines = templates.map(function(tmpl) { return tmpl.id + (tmpl.version ? ' v' + tmpl.version : '') + ' \u2014 ' + tmpl.name + ' (' + tmpl.tasks_count + ' tasks)'; });
        addMsg('system', 'Flow templates:\n' + lines.join('\n'));
      }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'deploy') {
    const templateId = parts[2];
    const scope = parts[3] || 'user';
    if (!templateId) { addMsg('system', 'Usage: /flow deploy <template_id> [user|conversation]'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'deploy_flow', template_id: templateId, scope, conversation_id: conversationId || '' }),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Deployed: ' + (data.instance_id || '?') + ' (' + scope + ')'); }
    }).catch(e => addMsg('error', e.message));
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
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'start_flow', instance_id: iid }),
      }).then(r => r.json()).then(data => {
        if (data.error) { addMsg('error', data.error); }
        else { addMsg('system', 'Flow \'' + iid + '\' started'); }
      }).catch(e => addMsg('error', e.message));
    };
    if (Object.keys(overrides).length) {
      fetch(API, {
        method: 'POST', headers: getAuthHeaders(),
        body: JSON.stringify({ action: 'update_flow_params', instance_id: iid, parameters: overrides }),
      }).then(() => startFlow()).catch(e => addMsg('error', e.message));
    } else {
      startFlow();
    }
  } else if (sub === 'stop') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow stop <instance_id>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'stop_flow', instance_id: iid }),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Flow \'' + iid + '\' stopped'); }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'params') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow params <instance_id>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_flow_instance', instance_id: iid }),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else {
        const params = { ...(data.template_parameters || {}), ...(data.parameters || {}) };
        const lines = Object.entries(params).map(function(entry) { return '  ' + entry[0] + ' = ' + entry[1]; });
        addMsg('system', 'Flow ' + (data.flow_name || iid) + ' [' + (data.status || '?') + ']:\n' + lines.join('\n'));
      }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'undeploy') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow undeploy <instance_id>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'undeploy_flow', instance_id: iid }),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Flow \'' + iid + '\' undeployed'); }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'promote') {
    const iid = parts[2];
    if (!iid) { addMsg('system', 'Usage: /flow promote <instance_id>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'promote_flow', instance_id: iid, target_scope: 'user' }),
    }).then(r => r.json()).then(data => {
      if (data.error) { addMsg('error', data.error); }
      else { addMsg('system', 'Flow \'' + iid + '\' promoted to user scope'); }
    }).catch(e => addMsg('error', e.message));
  } else {
    addMsg('system', 'Usage: /flow list | templates | deploy | start | stop | params | undeploy | promote');
  }
  return true;
}

function cmdPrompt(text, parts) {
  const sub = (parts[1] || 'list').toLowerCase();
  if (sub === 'list') {
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_prompts', conversation_id: conversationId || '' }),
    }).then(r => r.json()).then(data => {
      const prompts = data.prompts || [];
      if (!prompts.length) { addMsg('system', 'No prompts.'); }
      else {
        const lines = prompts.map(function(p) { return '\u2022 ' + p.name + ': ' + (p.description || p.content || '').slice(0, 60); });
        addMsg('system', 'Prompts:\n' + lines.join('\n'));
      }
    }).catch(e => addMsg('error', e.message));
  } else if (sub === 'use') {
    const name = parts[2] || '';
    if (!name) { addMsg('system', 'Usage: /prompt use <name>'); return true; }
    fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'get_prompt', conversation_id: conversationId || '', name }),
    }).then(r => r.json()).then(data => {
      if (data.content) { addMsg('system', 'Prompt \'' + name + '\':\n' + data.content); }
      else { addMsg('error', 'Prompt \'' + name + '\' not found'); }
    }).catch(e => addMsg('error', e.message));
  } else {
    addMsg('system', 'Usage: /prompt list | use <name>');
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
