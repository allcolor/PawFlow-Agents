// ── editor_toolbar.js — Save, load, validate, layout, zoom ──

// ── View switching (runtime ↔ editor) ──

function showEditorView() {
    document.getElementById("runtime-view").style.display = "none";
    document.getElementById("editor-view").style.display = "flex";
    // Load palette task types
    if (paletteTaskTypes.length === 0) loadPaletteTaskTypes();
    initEditor();
    renderEditorServices();
}

function showRuntimeView() {
    document.getElementById("editor-view").style.display = "none";
    document.getElementById("runtime-view").style.display = "flex";
}

// ── Toolbar actions ──

function editorNewFlow() {
    if (editorFlowData && Object.keys(editorFlowData.tasks || {}).length > 0) {
        if (!confirm("Create new flow? Unsaved changes will be lost.")) return;
    }
    editorFlowData = {
        id: "flow-" + Math.random().toString(36).substr(2, 8),
        name: "New Flow", version: "1.0.0", description: "", author: "",
        parameters: {}, variables: {},
        tasks: {}, relations: [], services: {},
        entries: [], exits: []
    };
    editorSelection = [];
    editorUndoStack = [];
    editorRedoStack = [];
    renderCanvas();
    renderEditorConfig();
    renderEditorServices();
    showToast("New flow created", "info");
}

function editorLoadFlow() {
    // Show modal with template picker or JSON upload
    var html = '<div class="modal-title">Load Flow</div>';
    html += '<div class="tab-bar" style="margin-bottom:16px">'
        + '<button class="tab-btn active" onclick="switchTab(this, \'load-from-template\')">From Template</button>'
        + '<button class="tab-btn" onclick="switchTab(this, \'load-from-json\')">From JSON</button>'
        + '</div>';

    html += '<div id="load-from-template" class="tab-content active">';
    html += '<div id="load-template-list" style="color:var(--text-dim)">Loading...</div>';
    html += '</div>';

    html += '<div id="load-from-json" class="tab-content">';
    html += '<div class="form-group"><label class="form-label">Paste Flow JSON</label>'
        + '<textarea class="form-input" id="load-json-text" rows="10" style="font-family:monospace;font-size:11px" placeholder="{}"></textarea></div>';
    html += '<div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
        + '<button class="btn btn-primary" onclick="editorLoadFromJson()">Load</button></div>';
    html += '</div>';

    html += '<div class="modal-actions" id="load-template-actions" style="display:none">'
        + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button></div>';

    showModal(html);

    // Load templates
    adminFetch("admin_list_templates").then(function(templates) {
        var listHtml = '';
        templates.forEach(function(t) {
            var path = t.path || "";
            var tid = t.id || "";
            listHtml += '<div class="flow-item" style="cursor:pointer;border-left:none" '
                + 'onclick="editorLoadTemplate(\'' + esc(path) + '\', \'' + esc(tid) + '\')">'
                + '<div class="flow-info">'
                + '<div class="flow-name">' + esc(t.name) + '</div>'
                + '<div class="flow-meta">' + esc(t.category || "") + ' &middot; ' + (t.tasks_count || "?") + ' tasks</div>'
                + '</div></div>';
        });
        document.getElementById("load-template-list").innerHTML = listHtml || '<div style="color:var(--text-dim)">No templates</div>';
    });
}

function editorLoadTemplate(path, templateId) {
    adminFetch("admin_get_template", {template_path: path, template_id: templateId}).then(function(data) {
        closeModal();
        loadFlowIntoEditor(data);
    });
}

function editorLoadFromJson() {
    var text = document.getElementById("load-json-text").value.trim();
    if (!text) { showToast("Paste JSON first", "error"); return; }
    try {
        var data = JSON.parse(text);
        if (!data.tasks) { showToast("Invalid flow JSON (no tasks)", "error"); return; }
        closeModal();
        loadFlowIntoEditor(data);
    } catch(e) {
        showToast("Invalid JSON: " + e.message, "error");
    }
}

function editorSaveFlow() {
    if (!editorFlowData) return;
    // Collect latest params from form
    editorSaveParams();

    var html = '<div class="modal-title">Save Flow</div>';
    html += '<div class="form-group"><label class="form-label">Flow Name</label>'
        + '<input type="text" class="form-input" id="save-flow-name" value="' + esc(editorFlowData.name || "") + '"></div>';
    html += '<div class="form-group"><label class="form-label">Version</label>'
        + '<input type="text" class="form-input" id="save-flow-version" value="' + esc(editorFlowData.version || "1.0.0") + '"></div>';
    html += '<div class="form-group"><label class="form-label">Description</label>'
        + '<textarea class="form-input" id="save-flow-desc" rows="2">' + esc(editorFlowData.description || "") + '</textarea></div>';

    // Preview JSON size
    var jsonSize = JSON.stringify(editorFlowData).length;
    html += '<div style="color:var(--text-dim);font-size:11px;margin-bottom:12px">JSON size: '
        + Math.round(jsonSize / 1024) + ' KB &middot; '
        + Object.keys(editorFlowData.tasks || {}).length + ' tasks &middot; '
        + (editorFlowData.relations || []).length + ' connections</div>';

    html += '<div class="modal-actions">'
        + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
        + '<button class="btn btn-ghost" onclick="editorExportJson()">Export JSON</button>'
        + '<button class="btn btn-primary" onclick="editorConfirmSave()">Save to Server</button></div>';
    showModal(html);
}

function editorConfirmSave() {
    editorFlowData.name = document.getElementById("save-flow-name").value || editorFlowData.name;
    editorFlowData.version = document.getElementById("save-flow-version").value || editorFlowData.version;
    editorFlowData.description = document.getElementById("save-flow-desc").value || "";

    adminFetch("admin_save_flow_json", {flow: editorFlowData}).then(function(data) {
        closeModal();
        showToast("Flow saved: " + (data.path || data.flow_id || ""), "success");
    });
}

function editorExportJson() {
    editorFlowData.name = document.getElementById("save-flow-name").value || editorFlowData.name;
    editorFlowData.version = document.getElementById("save-flow-version").value || editorFlowData.version;
    editorFlowData.description = document.getElementById("save-flow-desc").value || "";

    var json = JSON.stringify(editorFlowData, null, 2);
    var blob = new Blob([json], {type: "application/json"});
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = (editorFlowData.id || "flow") + ".json";
    a.click();
    URL.revokeObjectURL(url);
    closeModal();
    showToast("Exported", "success");
}

function editorValidateFlow() {
    if (!editorFlowData) return;
    editorSaveParams();
    adminFetch("admin_validate_flow", {flow: editorFlowData}).then(function(data) {
        var errors = data.errors || [];
        var warnings = data.warnings || [];
        if (errors.length === 0 && warnings.length === 0) {
            showToast("Flow is valid!", "success");
            return;
        }
        var html = '<div class="modal-title">Validation Results</div>';
        if (errors.length > 0) {
            html += '<div class="card-title" style="color:var(--red)">Errors</div>';
            errors.forEach(function(e) {
                html += '<div style="color:var(--red);font-size:13px;padding:2px 0">\u2717 ' + esc(e) + '</div>';
            });
        }
        if (warnings.length > 0) {
            html += '<div class="card-title" style="color:var(--yellow);margin-top:8px">Warnings</div>';
            warnings.forEach(function(w) {
                html += '<div style="color:var(--yellow);font-size:13px;padding:2px 0">\u26A0 ' + esc(w) + '</div>';
            });
        }
        html += '<div class="modal-actions"><button class="btn btn-ghost" onclick="closeModal()">Close</button></div>';
        showModal(html);
    });
}

// ── Zoom controls ──

function editorZoomIn() {
    editorTransform.scale = Math.min(3, editorTransform.scale + 0.2);
    renderCanvas();
}

function editorZoomOut() {
    editorTransform.scale = Math.max(0.2, editorTransform.scale - 0.2);
    renderCanvas();
}

function editorZoomFit() {
    if (!editorFlowData || !editorFlowData.tasks) return;
    var tasks = editorFlowData.tasks;
    var ids = Object.keys(tasks);
    if (ids.length === 0) return;

    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    ids.forEach(function(id) {
        var t = tasks[id];
        minX = Math.min(minX, t.x || 0);
        minY = Math.min(minY, t.y || 0);
        maxX = Math.max(maxX, (t.x || 0) + NODE_W);
        maxY = Math.max(maxY, (t.y || 0) + NODE_H);
    });

    var container = document.getElementById("editor-canvas");
    if (!container) return;
    var cw = container.clientWidth || 800;
    var ch = container.clientHeight || 500;
    var fw = maxX - minX + 100;
    var fh = maxY - minY + 100;
    var scale = Math.min(cw / fw, ch / fh, 2);
    editorTransform.scale = scale;
    editorTransform.x = (cw - fw * scale) / 2 - minX * scale + 50 * scale;
    editorTransform.y = (ch - fh * scale) / 2 - minY * scale + 50 * scale;
    renderCanvas();
}

// ── Flow metadata editor ──

function editorEditMetadata() {
    if (!editorFlowData) return;
    var html = '<div class="modal-title">Flow Metadata</div>';
    html += '<div class="form-group"><label class="form-label">Flow ID</label>'
        + '<input type="text" class="form-input" id="meta-id" value="' + esc(editorFlowData.id || "") + '"></div>';
    html += '<div class="form-group"><label class="form-label">Name</label>'
        + '<input type="text" class="form-input" id="meta-name" value="' + esc(editorFlowData.name || "") + '"></div>';
    html += '<div class="form-group"><label class="form-label">Version</label>'
        + '<input type="text" class="form-input" id="meta-version" value="' + esc(editorFlowData.version || "1.0.0") + '"></div>';
    html += '<div class="form-group"><label class="form-label">Description</label>'
        + '<textarea class="form-input" id="meta-desc" rows="2">' + esc(editorFlowData.description || "") + '</textarea></div>';
    html += '<div class="form-group"><label class="form-label">Author</label>'
        + '<input type="text" class="form-input" id="meta-author" value="' + esc(editorFlowData.author || "") + '"></div>';

    // Flow parameters
    var params = editorFlowData.parameters || {};
    html += '<div class="card-title" style="margin-top:16px">Flow Parameters</div>';
    Object.keys(params).forEach(function(k) {
        html += '<div class="form-group" style="display:flex;gap:4px;align-items:center">'
            + '<input type="text" class="form-input" value="' + esc(k) + '" style="flex:1" disabled>'
            + '<input type="text" class="form-input meta-param" data-key="' + esc(k) + '" value="' + esc(String(params[k])) + '" style="flex:2">'
            + '</div>';
    });
    html += '<div style="display:flex;gap:4px;margin-top:4px">'
        + '<input type="text" class="form-input" id="meta-new-param-key" placeholder="key" style="flex:1">'
        + '<input type="text" class="form-input" id="meta-new-param-val" placeholder="default value" style="flex:2">'
        + '<button class="btn btn-ghost btn-sm" onclick="metaAddParam()">+</button></div>';

    html += '<div class="modal-actions">'
        + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
        + '<button class="btn btn-primary" onclick="editorSaveMetadata()">Save</button></div>';
    showModal(html);
}

function metaAddParam() {
    var k = document.getElementById("meta-new-param-key").value.trim();
    var v = document.getElementById("meta-new-param-val").value;
    if (!k) return;
    editorFlowData.parameters = editorFlowData.parameters || {};
    editorFlowData.parameters[k] = v;
    editorEditMetadata(); // re-render modal
}

function editorSaveMetadata() {
    pushUndo();
    editorFlowData.id = document.getElementById("meta-id").value || editorFlowData.id;
    editorFlowData.name = document.getElementById("meta-name").value || editorFlowData.name;
    editorFlowData.version = document.getElementById("meta-version").value || editorFlowData.version;
    editorFlowData.description = document.getElementById("meta-desc").value || "";
    editorFlowData.author = document.getElementById("meta-author").value || "";
    // Save params
    document.querySelectorAll(".meta-param").forEach(function(el) {
        editorFlowData.parameters[el.dataset.key] = el.value;
    });
    closeModal();
    showToast("Metadata saved", "success");
}
