// ── editor_config.js — Task/service config, schema-based forms ──

var taskSchemaCache = {};  // taskType → schema

function renderEditorConfig() {
    var container = document.getElementById("editor-config");
    if (!container) return;

    if (!editorFlowData || editorSelection.length === 0) {
        container.innerHTML = '<div style="color:var(--text-dim);padding:8px;font-size:12px">Select a task to configure</div>';
        return;
    }

    var tid = editorSelection[0];
    var task = editorFlowData.tasks[tid];
    if (!task) {
        container.innerHTML = '<div style="color:var(--text-dim);padding:8px">Task not found</div>';
        return;
    }

    var color = taskColor(task.type || "");
    var cat = taskCategory(task.type || "");

    var html = '<div style="margin-bottom:12px">';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">';
    html += '<span class="palette-dot" style="background:' + color + ';width:10px;height:10px"></span>';
    html += '<span style="font-size:14px;font-weight:600;color:var(--text-bright)">' + esc(tid) + '</span>';
    html += '</div>';
    html += '<span class="badge" style="background:' + color + '22;color:' + color + '">' + esc(cat) + ' / ' + esc(task.type || "") + '</span>';
    html += '</div>';

    // Task name
    html += '<div class="form-group">';
    html += '<label class="form-label">Name</label>';
    html += '<input type="text" class="form-input" id="cfg-task-name" value="' + esc(task.name || tid)
        + '" onchange="editorSetTaskName(\'' + esc(tid) + '\', this.value)">';
    html += '</div>';

    // Connections summary
    var rels = editorFlowData.relations || [];
    var incoming = rels.filter(function(r) { return (r.to || r.target) === tid; });
    var outgoing = rels.filter(function(r) { return (r.from || r.source) === tid; });
    if (incoming.length > 0 || outgoing.length > 0) {
        html += '<div class="card-title" style="margin-top:12px">Connections</div>';
        incoming.forEach(function(r, i) {
            html += '<div style="font-size:12px;padding:2px 0;color:var(--text-dim)">'
                + '\u2B05 ' + esc(r.from || r.source) + ' <span style="color:var(--green)">[' + esc(r.type || "?") + ']</span>'
                + '</div>';
        });
        outgoing.forEach(function(r, i) {
            var relIdx = rels.indexOf(r);
            html += '<div style="font-size:12px;padding:2px 0;display:flex;align-items:center;justify-content:space-between">';
            html += '<span style="color:var(--text-dim)">\u27A1 ' + esc(r.to || r.target) + ' <span style="color:var(--green)">[' + esc(r.type || "?") + ']</span></span>';
            html += '<button class="btn btn-ghost btn-sm" style="font-size:10px;padding:1px 4px" onclick="editorDeleteConnection(' + relIdx + ')">x</button>';
            html += '</div>';
        });
    }

    // Parameters — from schema or raw
    html += '<div class="card-title" style="margin-top:16px">Parameters</div>';
    html += '<div id="cfg-params-container">';
    html += renderRawParams(tid, task);
    html += '</div>';

    // Load schema for better form if available
    html += '<button class="btn btn-ghost btn-sm" onclick="loadTaskSchema(\'' + esc(tid) + '\', \'' + esc(task.type || "") + '\')" style="margin-top:8px;font-size:11px">Load Schema</button>';

    // Actions
    html += '<div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">';
    html += '<button class="btn btn-ghost btn-sm" onclick="editorDuplicateTask(\'' + esc(tid) + '\')">Duplicate</button> ';
    html += '<button class="btn btn-danger btn-sm" onclick="editorDeleteSelected()">Delete</button>';
    html += '</div>';

    container.innerHTML = html;
}

function renderRawParams(tid, task) {
    var params = task.parameters || {};
    var keys = Object.keys(params);
    var html = '';

    keys.forEach(function(k) {
        var val = params[k];
        var strVal = typeof val === "object" ? JSON.stringify(val) : String(val || "");
        html += '<div class="form-group">';
        html += '<label class="form-label">' + esc(k) + '</label>';
        if (strVal.length > 100) {
            html += '<textarea class="form-input cfg-param" data-tid="' + esc(tid)
                + '" data-key="' + esc(k) + '" rows="3" style="resize:vertical">' + esc(strVal) + '</textarea>';
        } else {
            html += '<input type="text" class="form-input cfg-param" data-tid="' + esc(tid)
                + '" data-key="' + esc(k) + '" value="' + esc(strVal) + '">';
        }
        html += '</div>';
    });

    // Add new parameter
    html += '<div style="display:flex;gap:4px;margin-top:4px">';
    html += '<input type="text" class="form-input" id="cfg-new-key-' + esc(tid) + '" placeholder="key" style="flex:1;font-size:11px">';
    html += '<input type="text" class="form-input" id="cfg-new-val-' + esc(tid) + '" placeholder="value" style="flex:1;font-size:11px">';
    html += '<button class="btn btn-ghost btn-sm" style="font-size:10px" onclick="editorAddParam(\'' + esc(tid) + '\')">+</button>';
    html += '</div>';

    return html;
}

function renderSchemaForm(tid, task, schema) {
    var params = task.parameters || {};
    var html = '';

    Object.keys(schema).forEach(function(k) {
        var spec = schema[k];
        var val = params[k];
        var isRequired = spec.required;
        var defaultVal = spec.default;
        if (val == null && defaultVal != null) val = defaultVal;

        html += '<div class="form-group">';
        html += '<label class="form-label">' + esc(k);
        if (isRequired) html += ' <span style="color:var(--red)">*</span>';
        html += '</label>';

        if (spec.description) {
            html += '<div style="font-size:10px;color:var(--text-dim);margin-bottom:2px">' + esc(spec.description) + '</div>';
        }

        var fieldType = spec.type || "string";
        if (spec.enum) {
            html += '<select class="form-select cfg-param" data-tid="' + esc(tid) + '" data-key="' + esc(k) + '">';
            spec.enum.forEach(function(opt) {
                var sel = String(val) === String(opt) ? " selected" : "";
                html += '<option value="' + esc(String(opt)) + '"' + sel + '>' + esc(String(opt)) + '</option>';
            });
            html += '</select>';
        } else if (fieldType === "boolean") {
            var checked = val === true || val === "true" ? " checked" : "";
            html += '<label style="display:flex;align-items:center;gap:6px;font-size:12px">'
                + '<input type="checkbox" class="cfg-param-bool" data-tid="' + esc(tid) + '" data-key="' + esc(k) + '"' + checked + '>'
                + (val ? "true" : "false") + '</label>';
        } else if (fieldType === "number" || fieldType === "integer") {
            html += '<input type="number" class="form-input cfg-param" data-tid="' + esc(tid)
                + '" data-key="' + esc(k) + '" value="' + esc(String(val != null ? val : "")) + '">';
        } else if (fieldType === "object" || fieldType === "array") {
            var strVal = typeof val === "object" ? JSON.stringify(val, null, 2) : String(val || "");
            html += '<textarea class="form-input cfg-param" data-tid="' + esc(tid)
                + '" data-key="' + esc(k) + '" rows="3" style="resize:vertical;font-family:monospace;font-size:11px">'
                + esc(strVal) + '</textarea>';
        } else {
            var strVal2 = typeof val === "object" ? JSON.stringify(val) : String(val || "");
            if (strVal2.length > 80) {
                html += '<textarea class="form-input cfg-param" data-tid="' + esc(tid)
                    + '" data-key="' + esc(k) + '" rows="2" style="resize:vertical">' + esc(strVal2) + '</textarea>';
            } else {
                html += '<input type="text" class="form-input cfg-param" data-tid="' + esc(tid)
                    + '" data-key="' + esc(k) + '" value="' + esc(strVal2) + '">';
            }
        }
        html += '</div>';
    });

    return html;
}

function loadTaskSchema(tid, taskType) {
    if (taskSchemaCache[taskType]) {
        applySchema(tid, taskType, taskSchemaCache[taskType]);
        return;
    }
    adminFetch("admin_get_task_schema", {task_type: taskType}).then(function(data) {
        taskSchemaCache[taskType] = data.schema || data;
        applySchema(tid, taskType, taskSchemaCache[taskType]);
    }).catch(function() {
        showToast("No schema for " + taskType, "error");
    });
}

function applySchema(tid, taskType, schema) {
    var task = editorFlowData.tasks[tid];
    if (!task) return;
    var container = document.getElementById("cfg-params-container");
    if (!container) return;
    container.innerHTML = renderSchemaForm(tid, task, schema);
}

// ── Param editing ──

function editorSetTaskName(tid, name) {
    if (!editorFlowData.tasks[tid]) return;
    pushUndo();
    editorFlowData.tasks[tid].name = name;
    renderCanvas();
}

function editorAddParam(tid) {
    var keyEl = document.getElementById("cfg-new-key-" + tid);
    var valEl = document.getElementById("cfg-new-val-" + tid);
    if (!keyEl || !valEl) return;
    var k = keyEl.value.trim();
    var v = valEl.value;
    if (!k) return;
    pushUndo();
    editorFlowData.tasks[tid].parameters = editorFlowData.tasks[tid].parameters || {};
    try { v = JSON.parse(v); } catch(e) {}
    editorFlowData.tasks[tid].parameters[k] = v;
    renderEditorConfig();
}

function editorSaveParams() {
    // Collect all param fields and save to flow data
    document.querySelectorAll(".cfg-param").forEach(function(el) {
        var tid = el.dataset.tid;
        var key = el.dataset.key;
        if (!tid || !key || !editorFlowData.tasks[tid]) return;
        var val = el.value;
        try { val = JSON.parse(val); } catch(e) {}
        editorFlowData.tasks[tid].parameters[key] = val;
    });
    document.querySelectorAll(".cfg-param-bool").forEach(function(el) {
        var tid = el.dataset.tid;
        var key = el.dataset.key;
        if (!tid || !key || !editorFlowData.tasks[tid]) return;
        editorFlowData.tasks[tid].parameters[key] = el.checked;
    });
}

function editorDuplicateTask(tid) {
    var task = editorFlowData.tasks[tid];
    if (!task) return;
    pushUndo();
    var newId = tid + "_copy_" + Math.random().toString(36).substr(2, 4);
    editorFlowData.tasks[newId] = JSON.parse(JSON.stringify(task));
    editorFlowData.tasks[newId].x = (task.x || 0) + 40;
    editorFlowData.tasks[newId].y = (task.y || 0) + 40;
    editorFlowData.tasks[newId].name = (task.name || tid) + " (copy)";
    editorSelection = [newId];
    renderCanvas();
    renderEditorConfig();
    showToast("Duplicated: " + newId, "success");
}
