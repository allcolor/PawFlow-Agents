// ── runtime_list.js — Flow list sidebar + deploy modal ──

function loadFlowList() {
    adminFetch("admin_list_flows").then(function(data) {
        flowListData = data;
        renderFlowList(data);
    }).catch(function(err) {
        document.getElementById("flow-list").innerHTML =
            '<div style="padding:16px;color:var(--text-dim)">Failed to load flows</div>';
    });
}

function renderFlowList(flows) {
    var container = document.getElementById("flow-list");
    if (!flows || flows.length === 0) {
        container.innerHTML = '<div style="padding:16px;color:var(--text-dim)">No flows deployed</div>';
        return;
    }
    // Sort: running first, then by name
    flows.sort(function(a, b) {
        if (a.status === "running" && b.status !== "running") return -1;
        if (a.status !== "running" && b.status === "running") return 1;
        return (a.flow_name || "").localeCompare(b.flow_name || "");
    });
    var html = "";
    flows.forEach(function(f) {
        var active = f.instance_id === selectedFlowId ? " active" : "";
        var taskInfo = f.tasks_total ? f.tasks_total + " tasks" : "";
        var queueInfo = f.total_queued ? ", " + f.total_queued + " queued" : "";
        var owner = f.owner ? f.owner : "global";
        html += '<div class="flow-item' + active + '" data-id="' + esc(f.instance_id) + '"'
            + ' onclick="selectFlow(\'' + esc(f.instance_id) + '\')"'
            + ' oncontextmenu="flowContextMenu(event, \'' + esc(f.instance_id) + '\')">'
            + '<div class="flow-dot ' + esc(f.status) + '"></div>'
            + '<div class="flow-info">'
            + '<div class="flow-name">' + esc(f.flow_name) + '</div>'
            + '<div class="flow-meta">' + esc(owner) + (taskInfo ? " &middot; " + taskInfo : "") + queueInfo + '</div>'
            + '</div></div>';
    });
    container.innerHTML = html;
}

function filterFlowList() {
    var q = document.getElementById("flow-search").value.toLowerCase();
    var filtered = flowListData.filter(function(f) {
        return (f.flow_name || "").toLowerCase().indexOf(q) !== -1
            || (f.instance_id || "").toLowerCase().indexOf(q) !== -1
            || (f.flow_id || "").toLowerCase().indexOf(q) !== -1;
    });
    renderFlowList(filtered);
}

function flowContextMenu(e, instanceId) {
    var flow = flowListData.find(function(f) { return f.instance_id === instanceId; });
    if (!flow) return;
    var items = [];
    if (flow.status === "running") {
        items.push({label: "Stop", action: function() { stopFlow(instanceId); }});
        items.push({label: "Restart", action: function() { restartFlow(instanceId); }});
        items.push({label: "Hot Reload", action: function() { hotReloadFlow(instanceId); }});
    } else {
        items.push({label: "Start", action: function() { startFlow(instanceId); }});
    }
    items.push("---");
    items.push({label: "Undeploy", action: function() { undeployFlow(instanceId); }});
    showContextMenu(e, items);
}

function startFlow(instanceId) {
    adminFetch("admin_start_flow", {instance_id: instanceId}).then(function() {
        showToast("Flow started", "success");
        loadFlowList();
        if (selectedFlowId === instanceId) loadFlowDetail(instanceId);
    });
}

function stopFlow(instanceId) {
    adminFetch("admin_stop_flow", {instance_id: instanceId}).then(function() {
        showToast("Flow stopped", "info");
        loadFlowList();
        if (selectedFlowId === instanceId) loadFlowDetail(instanceId);
    });
}

function restartFlow(instanceId) {
    adminFetch("admin_restart_flow", {instance_id: instanceId}).then(function() {
        showToast("Flow restarted", "success");
        loadFlowList();
        if (selectedFlowId === instanceId) loadFlowDetail(instanceId);
    });
}

function hotReloadFlow(instanceId) {
    adminFetch("admin_hot_reload", {instance_id: instanceId}).then(function(data) {
        showToast("Reloaded (v" + (data.version || "?") + ")", "success");
    });
}

function undeployFlow(instanceId) {
    if (!confirm("Undeploy this flow? This will stop it and remove the deployment.")) return;
    adminFetch("admin_undeploy_flow", {instance_id: instanceId}).then(function() {
        showToast("Flow undeployed", "info");
        if (selectedFlowId === instanceId) {
            selectedFlowId = null;
            document.getElementById("flow-detail").style.display = "none";
            document.getElementById("empty-state").style.display = "flex";
        }
        loadFlowList();
    });
}

// ── Deploy Modal ──

function openDeployModal() {
    showModal(
        '<div class="modal-title">Deploy Flow</div>'
        + '<div id="deploy-form"><div style="color:var(--text-dim)">Loading templates...</div></div>'
    );
    adminFetch("admin_list_templates").then(function(templates) {
        renderDeployForm(templates);
    }).catch(function() {
        document.getElementById("deploy-form").innerHTML =
            '<div style="color:var(--red)">Failed to load templates</div>';
    });
}

function renderDeployForm(templates) {
    var html = '<div class="form-group">'
        + '<label class="form-label">Template</label>'
        + '<select class="form-select" id="deploy-template" onchange="onDeployTemplateChange()">';
    html += '<option value="">-- Select template --</option>';
    templates.forEach(function(t) {
        var val = t.path || t.id || "";
        var label = t.name + (t.category ? " (" + t.category + ")" : "");
        html += '<option value="' + esc(val) + '" data-id="' + esc(t.id) + '">'
            + esc(label) + '</option>';
    });
    html += '</select></div>';
    html += '<div class="form-group">'
        + '<label class="form-label">Owner</label>'
        + '<select class="form-select" id="deploy-owner">'
        + '<option value="__global__">Global</option>'
        + '</select></div>';
    html += '<div class="form-group">'
        + '<label class="form-label">Max Workers</label>'
        + '<input type="number" class="form-input" id="deploy-workers" value="4" min="1" max="32">'
        + '</div>';
    html += '<div class="form-group">'
        + '<label class="form-label">Max Retries</label>'
        + '<input type="number" class="form-input" id="deploy-retries" value="3" min="1" max="10">'
        + '</div>';
    html += '<div id="deploy-params"></div>';
    html += '<div class="modal-actions">'
        + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
        + '<button class="btn btn-primary" onclick="submitDeploy()">Deploy & Start</button>'
        + '</div>';
    document.getElementById("deploy-form").innerHTML = html;
}

function onDeployTemplateChange() {
    var sel = document.getElementById("deploy-template");
    var path = sel.value;
    if (!path) {
        document.getElementById("deploy-params").innerHTML = "";
        return;
    }
    var templateId = sel.options[sel.selectedIndex].dataset.id || "";
    adminFetch("admin_get_template", {template_path: path, template_id: templateId}).then(function(tmpl) {
        var params = tmpl.parameters || {};
        var html = "";
        var keys = Object.keys(params);
        if (keys.length > 0) {
            html += '<div class="card-title">Parameters</div>';
            keys.forEach(function(k) {
                html += '<div class="form-group">'
                    + '<label class="form-label">' + esc(k) + '</label>'
                    + '<input type="text" class="form-input deploy-param" data-key="' + esc(k)
                    + '" value="' + esc(String(params[k])) + '">'
                    + '</div>';
            });
        }
        document.getElementById("deploy-params").innerHTML = html;
    });
}

function submitDeploy() {
    var templatePath = document.getElementById("deploy-template").value;
    if (!templatePath) {
        showToast("Select a template", "error");
        return;
    }
    var owner = document.getElementById("deploy-owner").value;
    var maxWorkers = parseInt(document.getElementById("deploy-workers").value) || 4;
    var maxRetries = parseInt(document.getElementById("deploy-retries").value) || 3;
    var params = {};
    document.querySelectorAll(".deploy-param").forEach(function(el) {
        params[el.dataset.key] = el.value;
    });

    adminFetch("admin_deploy_flow", {
        template_path: templatePath,
        owner: owner,
        parameters: params,
        max_workers: maxWorkers,
        max_retries: maxRetries,
        auto_start: true
    }).then(function(data) {
        closeModal();
        showToast("Flow deployed: " + (data.instance_id || ""), "success");
        loadFlowList();
        if (data.instance_id) selectFlow(data.instance_id);
    });
}
