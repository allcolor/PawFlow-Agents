// ── runtime_detail.js — Flow detail: header, tabs, services, params ──

var currentFlowData = null;

function loadFlowDetail(instanceId) {
    adminFetch("admin_get_flow", {instance_id: instanceId}).then(function(data) {
        currentFlowData = data;
        renderFlowDetail(data);
    }).catch(function() {
        document.getElementById("flow-detail").innerHTML =
            '<div style="color:var(--red)">Failed to load flow detail</div>';
    });
}

function renderFlowDetail(data) {
    var container = document.getElementById("flow-detail");
    var isRunning = data.status === "running";
    var badgeClass = "badge-" + data.status;

    // Header
    var html = '<div class="detail-header">'
        + '<div class="detail-title">'
        + '<h2>' + esc(data.flow_name) + '</h2>'
        + '<span class="badge ' + badgeClass + '">' + esc(data.status) + '</span>';
    if (isRunning && data.last_started) {
        html += '<span style="color:var(--text-dim);font-size:12px">up ' + uptime(data.last_started) + '</span>';
    }
    html += '</div><div class="btn-group">';
    if (isRunning) {
        html += '<button class="btn btn-ghost" onclick="stopFlow(\'' + esc(data.instance_id) + '\')">Stop</button>';
        html += '<button class="btn btn-ghost" onclick="restartFlow(\'' + esc(data.instance_id) + '\')">Restart</button>';
        html += '<button class="btn btn-ghost" onclick="hotReloadFlow(\'' + esc(data.instance_id) + '\')">Hot Reload</button>';
    } else {
        html += '<button class="btn btn-success" onclick="startFlow(\'' + esc(data.instance_id) + '\')">Start</button>';
    }
    html += '<button class="btn btn-danger btn-sm" onclick="undeployFlow(\'' + esc(data.instance_id) + '\')">Undeploy</button>';
    html += '</div></div>';

    // Tabs
    html += '<div class="tab-bar">'
        + '<button class="tab-btn active" onclick="switchTab(this, \'tab-overview\')">Overview</button>'
        + '<button class="tab-btn" onclick="switchTab(this, \'tab-services\')">Services</button>'
        + '<button class="tab-btn" onclick="switchTab(this, \'tab-params\')">Parameters</button>'
        + '<button class="tab-btn" onclick="switchTab(this, \'tab-queues\')">Queues</button>'
        + '</div>';

    // Tab: Overview
    html += '<div id="tab-overview" class="tab-content active">';
    html += renderOverviewTab(data);
    html += '</div>';

    // Tab: Services
    html += '<div id="tab-services" class="tab-content">';
    html += renderServicesTab(data);
    html += '</div>';

    // Tab: Parameters
    html += '<div id="tab-params" class="tab-content">';
    html += renderParamsTab(data);
    html += '</div>';

    // Tab: Queues
    html += '<div id="tab-queues" class="tab-content">';
    html += '<div id="queues-content">';
    html += renderQueuesTab(data);
    html += '</div></div>';

    container.innerHTML = html;
}

function switchTab(btn, tabId) {
    btn.parentElement.querySelectorAll(".tab-btn").forEach(function(b) { b.classList.remove("active"); });
    btn.classList.add("active");
    var parent = btn.closest("#flow-detail") || document;
    parent.querySelectorAll(".tab-content").forEach(function(t) { t.classList.remove("active"); });
    document.getElementById(tabId).classList.add("active");
}

// ── Overview Tab ──

function renderOverviewTab(data) {
    var taskCount = Object.keys(data.tasks || {}).length;
    var svcCount = Object.keys(data.services || {}).length;
    var relCount = (data.relations || []).length;
    var es = data.executor_status;

    var html = '<div class="kpi-grid" id="kpi-grid">';
    if (es) {
        html += kpiCard(es.tasks_total, "Tasks");
        html += kpiCard(es.tasks_running, "Running");
        html += kpiCard(es.tasks_errored, "Errors");
        html += kpiCard(es.total_queued_flowfiles, "Queued");
    } else {
        html += kpiCard(taskCount, "Tasks");
        html += kpiCard(svcCount, "Services");
        html += kpiCard(relCount, "Connections");
    }
    html += '</div>';

    // Info card
    html += '<div class="card">';
    html += '<div class="card-title">Info</div>';
    html += '<table>';
    html += infoRow("Instance ID", data.instance_id);
    html += infoRow("Flow ID", data.flow_id);
    html += infoRow("Template", data.flow_path);
    html += infoRow("Owner", data.owner || "global");
    html += infoRow("Source", data.source);
    html += infoRow("Created", formatTime(data.created_at));
    html += infoRow("Last Started", formatTime(data.last_started));
    html += infoRow("Workers / Retries", data.max_workers + " / " + data.max_retries);
    if (data.error_message) {
        html += '<tr><td style="color:var(--red)">Error</td><td style="color:var(--red)">' + esc(data.error_message) + '</td></tr>';
    }
    html += '</table></div>';

    var runtimeLinks = data.runtime_links || [];
    var ports = data.ports || {};
    if (runtimeLinks.length || Object.keys(ports).length) {
        html += '<div class="card">';
        html += '<div class="card-title">Runtime Links</div>';
        if (Object.keys(ports).length) {
            html += '<table><tr><th>Port</th><th>Type</th><th>Task</th><th>Description</th></tr>';
            Object.keys(ports).forEach(function(pid) {
                var p = ports[pid] || {};
                html += '<tr><td>' + esc(pid) + '</td><td>' + esc(p.type || "")
                    + '</td><td>' + esc(p.task || "") + '</td><td>'
                    + esc(p.description || "") + '</td></tr>';
            });
            html += '</table>';
        }
        if (runtimeLinks.length) {
            html += '<table><tr><th>From</th><th>Target Port</th><th>Type</th><th>Description</th></tr>';
            runtimeLinks.forEach(function(link) {
                var target = resolveRuntimeDetailTarget(
                    link.to || "", data.resolved_parameters || data.parameters || {});
                html += '<tr><td>' + esc(link.from || "") + '</td><td>'
                    + esc(target) + '</td><td>' + esc(link.type || "")
                    + '</td><td>' + esc(link.description || "") + '</td></tr>';
            });
            html += '</table>';
        }
        html += '</div>';
    }

    // Task states (if running)
    if (data.task_states && Object.keys(data.task_states).length > 0) {
        html += '<div class="card">';
        html += '<div class="card-title">Task States</div>';
        html += '<table><tr><th>Task</th><th>Type</th><th>State</th><th>Runs</th><th>Errors</th><th>FF In</th><th>FF Out</th></tr>';
        Object.keys(data.task_states).forEach(function(tid) {
            var s = data.task_states[tid];
            var stateColor = s.state === "running" ? "var(--green)" : s.state === "error" ? "var(--red)" : "var(--text-dim)";
            html += '<tr>'
                + '<td>' + esc(tid) + '</td>'
                + '<td style="color:var(--text-dim)">' + esc(s.task_type || "") + '</td>'
                + '<td style="color:' + stateColor + '">' + esc(s.state) + '</td>'
                + '<td>' + (s.run_count || 0) + '</td>'
                + '<td>' + (s.error_count || 0) + '</td>'
                + '<td>' + (s.flowfiles_in || 0) + '</td>'
                + '<td>' + (s.flowfiles_out || 0) + '</td>'
                + '</tr>';
        });
        html += '</table></div>';
    }

    return html;
}

function resolveRuntimeDetailTarget(value, params) {
    return String(value || "").replace(/\$\{([^}]+)\}/g, function(_, key) {
        var v = params[key];
        if (v && typeof v === "object" && v.default != null) return v.default;
        return v != null ? v : "${" + key + "}";
    });
}

function kpiCard(value, label) {
    return '<div class="kpi-card"><div class="kpi-value">' + (value != null ? value : "—")
        + '</div><div class="kpi-label">' + esc(label) + '</div></div>';
}

function infoRow(label, value) {
    return '<tr><td style="color:var(--text-dim);width:150px">' + esc(label) + '</td><td>' + esc(String(value || "—")) + '</td></tr>';
}

// ── Services Tab ──

function renderServicesTab(data) {
    var services = data.services || {};
    var keys = Object.keys(services);
    if (keys.length === 0) return '<div style="color:var(--text-dim)">No services defined</div>';

    var isRunning = data.status === "running";
    var html = '<div class="card"><table>';
    html += '<tr><th>Service ID</th><th>Type</th><th>Mode</th><th>Action</th></tr>';
    keys.forEach(function(sid) {
        var svc = services[sid];
        var override = (data.service_overrides || {})[sid];
        var mode = override ? override : "local";
        html += '<tr>'
            + '<td>' + esc(sid) + '</td>'
            + '<td style="color:var(--text-dim)">' + esc(svc.type || "") + '</td>'
            + '<td>' + esc(mode) + '</td>'
            + '<td>';
        if (!isRunning) {
            html += '<button class="btn btn-ghost btn-sm" onclick="editServiceConfig(\''
                + esc(data.instance_id) + '\', \'' + esc(sid) + '\')">Configure</button>';
        }
        html += '</td></tr>';
    });
    html += '</table></div>';
    return html;
}

function editServiceConfig(instanceId, svcId) {
    var data = currentFlowData;
    if (!data) return;
    var svc = (data.services || {})[svcId] || {};
    var config = (data.service_configs || {})[svcId] || svc.parameters || {};
    var override = (data.service_overrides || {})[svcId] || "";

    var html = '<div class="modal-title">Configure Service: ' + esc(svcId) + '</div>';
    html += '<div class="form-group">'
        + '<label class="form-label">Type</label>'
        + '<div style="color:var(--text-dim)">' + esc(svc.type || "unknown") + '</div>'
        + '</div>';
    html += '<div class="form-group">'
        + '<label class="form-label">Mode</label>'
        + '<select class="form-select" id="svc-mode">'
        + '<option value="local"' + (!override ? ' selected' : '') + '>Local</option>'
        + '<option value="global"' + (override && override.startsWith("global:") ? ' selected' : '') + '>Global Service</option>'
        + '</select></div>';
    html += '<div id="svc-config-fields">';
    var configKeys = Object.keys(config);
    configKeys.forEach(function(k) {
        html += '<div class="form-group">'
            + '<label class="form-label">' + esc(k) + '</label>'
            + '<input type="text" class="form-input svc-cfg-field" data-key="' + esc(k)
            + '" value="' + esc(String(config[k] || "")) + '">'
            + '</div>';
    });
    html += '</div>';
    html += '<div class="modal-actions">'
        + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
        + '<button class="btn btn-primary" onclick="saveServiceConfig(\'' + esc(instanceId) + '\', \'' + esc(svcId) + '\')">Save</button>'
        + '</div>';
    showModal(html);
}

function saveServiceConfig(instanceId, svcId) {
    var mode = document.getElementById("svc-mode").value;
    var config = {};
    document.querySelectorAll(".svc-cfg-field").forEach(function(el) {
        var val = el.value;
        // Try to parse JSON values
        try { val = JSON.parse(val); } catch(e) {}
        config[el.dataset.key] = val;
    });
    adminFetch("admin_update_service", {
        instance_id: instanceId,
        service_id: svcId,
        mode: mode,
        config: config
    }).then(function() {
        closeModal();
        showToast("Service updated", "success");
        loadFlowDetail(instanceId);
    });
}

// ── Parameters Tab ──

function renderParamsTab(data) {
    var params = data.parameters || {};
    var keys = Object.keys(params);

    // Also show template parameters for reference
    var templateParams = {};
    if (data.tasks) {
        // Get from flow template parameters
        // They're stored in the deployment parameters
    }

    if (keys.length === 0) return '<div style="color:var(--text-dim)">No parameters</div>';

    var isRunning = data.status === "running";
    var html = '<div class="card"><table>';
    html += '<tr><th>Key</th><th>Value</th><th>Action</th></tr>';
    keys.forEach(function(k) {
        html += '<tr>'
            + '<td>' + esc(k) + '</td>'
            + '<td id="param-val-' + esc(k) + '">';
        if (isRunning) {
            html += esc(String(params[k]));
        } else {
            html += '<input type="text" class="inline-edit param-edit" data-key="' + esc(k)
                + '" value="' + esc(String(params[k])) + '">';
        }
        html += '</td><td>';
        if (!isRunning) {
            html += '<button class="btn btn-ghost btn-sm" onclick="saveParam(\''
                + esc(data.instance_id) + '\', \'' + esc(k) + '\')">Save</button>';
        }
        html += '</td></tr>';
    });
    html += '</table></div>';
    return html;
}

function saveParam(instanceId, key) {
    var input = document.querySelector('.param-edit[data-key="' + key + '"]');
    if (!input) return;
    adminFetch("admin_update_parameter", {
        instance_id: instanceId,
        key: key,
        value: input.value
    }).then(function() {
        showToast("Parameter saved", "success");
    });
}

// ── KPI refresh (called by auto-refresh) ──

function refreshKpis(instanceId) {
    adminFetch("admin_get_kpis", {instance_id: instanceId}).then(function(data) {
        var grid = document.getElementById("kpi-grid");
        if (!grid) return;
        grid.innerHTML = kpiCard(data.tasks_total, "Tasks")
            + kpiCard(data.tasks_running, "Running")
            + kpiCard(data.tasks_errored, "Errors")
            + kpiCard(data.total_queued, "Queued")
            + kpiCard(data.total_ff_in, "FF In")
            + kpiCard(data.total_ff_out, "FF Out");
    }).catch(function() {});

    // Also refresh queue stats
    var qc = document.getElementById("queues-content");
    if (qc && document.getElementById("tab-queues") && document.getElementById("tab-queues").classList.contains("active")) {
        adminFetch("admin_get_queue_stats", {instance_id: instanceId}).then(function(data) {
            qc.innerHTML = renderQueuesFromStats(data.queue_stats || []);
        }).catch(function() {});
    }
}
