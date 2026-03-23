// ── editor_palette.js — Task type palette with drag-drop ──

var paletteTaskTypes = [];

// Category display order and icons
var PALETTE_CATEGORIES = [
    {id: "System", icon: "\u2699\uFE0F", label: "System"},
    {id: "IO", icon: "\uD83D\uDCC1", label: "IO"},
    {id: "Cloud", icon: "\u2601\uFE0F", label: "Cloud"},
    {id: "Data", icon: "\uD83D\uDD04", label: "Data"},
    {id: "Control", icon: "\uD83D\uDD00", label: "Control"},
    {id: "Messaging", icon: "\u2709\uFE0F", label: "Messaging"},
    {id: "Sync", icon: "\uD83D\uDD17", label: "Sync"},
    {id: "Monitoring", icon: "\uD83D\uDCCA", label: "Monitoring"},
    {id: "AI", icon: "\uD83E\uDD16", label: "AI"},
    {id: "Plugins", icon: "\uD83E\uDDE9", label: "Plugins"}
];

function loadPaletteTaskTypes() {
    adminFetch("admin_list_task_types").then(function(data) {
        paletteTaskTypes = data || [];
        renderEditorPalette();
    }).catch(function() {
        paletteTaskTypes = [];
        renderEditorPalette();
    });
}

function renderEditorPalette() {
    var container = document.getElementById("editor-palette");
    if (!container) return;

    var searchVal = "";
    var searchEl = document.getElementById("palette-search");
    if (searchEl) searchVal = searchEl.value.toLowerCase();

    var html = '<input type="text" id="palette-search" class="form-input" placeholder="Search tasks..."'
        + ' oninput="renderEditorPalette()" value="' + esc(searchVal) + '" style="margin-bottom:8px">';

    // Group tasks by category
    var byCategory = {};
    paletteTaskTypes.forEach(function(t) {
        var cat = t.category || "Plugins";
        if (!byCategory[cat]) byCategory[cat] = [];
        byCategory[cat].push(t);
    });

    var collapsedPalette = window._paletteColl || {};

    PALETTE_CATEGORIES.forEach(function(catDef) {
        var tasks = byCategory[catDef.id];
        if (!tasks || tasks.length === 0) return;

        // Filter by search
        var filtered = tasks;
        if (searchVal) {
            filtered = tasks.filter(function(t) {
                return (t.name || "").toLowerCase().indexOf(searchVal) >= 0
                    || (t.type || "").toLowerCase().indexOf(searchVal) >= 0;
            });
            if (filtered.length === 0) return;
        }

        var isCollapsed = collapsedPalette[catDef.id] && !searchVal;
        var color = CATEGORY_COLORS[catDef.id] || "#adb5bd";

        html += '<div class="palette-category">';
        html += '<div class="palette-cat-header" onclick="togglePaletteCategory(\'' + catDef.id + '\')"'
            + ' style="border-left:3px solid ' + color + '">';
        html += '<span>' + catDef.icon + ' ' + esc(catDef.label)
            + ' <span style="color:var(--text-dim);font-size:11px">(' + filtered.length + ')</span></span>';
        html += '<span>' + (isCollapsed ? '\u25B6' : '\u25BC') + '</span>';
        html += '</div>';

        if (!isCollapsed) {
            filtered.forEach(function(t) {
                html += '<div class="palette-item" draggable="true"'
                    + ' ondragstart="paletteDragStart(event, \'' + esc(t.type) + '\')"'
                    + ' ondblclick="editorAddTask(\'' + esc(t.type) + '\')"'
                    + ' title="' + esc(t.description || t.type) + '">';
                html += '<span class="palette-dot" style="background:' + color + '"></span>';
                html += '<span class="palette-name">' + esc(t.name || t.type) + '</span>';
                html += '</div>';
            });
        }

        html += '</div>';
    });

    container.innerHTML = html;
}

function togglePaletteCategory(catId) {
    window._paletteColl = window._paletteColl || {};
    window._paletteColl[catId] = !window._paletteColl[catId];
    renderEditorPalette();
}

function paletteDragStart(e, taskType) {
    e.dataTransfer.setData("text/plain", taskType);
    e.dataTransfer.effectAllowed = "copy";
}

// ── Service list in editor ──

function renderEditorServices() {
    var container = document.getElementById("editor-services");
    if (!container || !editorFlowData) return;

    var services = editorFlowData.services || {};
    var keys = Object.keys(services);

    var html = '<div class="card-title">Services</div>';
    if (keys.length === 0) {
        html += '<div style="color:var(--text-dim);font-size:12px">No services defined</div>';
    } else {
        keys.forEach(function(sid) {
            var svc = services[sid];
            html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border)">';
            html += '<div><span style="color:var(--text-bright);font-size:12px">' + esc(sid) + '</span>'
                + '<br><span style="color:var(--text-dim);font-size:11px">' + esc(svc.type || "") + '</span></div>';
            html += '<button class="btn btn-ghost btn-sm" onclick="editorEditService(\'' + esc(sid)
                + '\')" style="font-size:11px">Edit</button>';
            html += '</div>';
        });
    }
    html += '<button class="btn btn-ghost btn-sm" onclick="editorAddService()" style="margin-top:8px;width:100%">+ Add Service</button>';
    container.innerHTML = html;
}

function editorAddService() {
    adminFetch("admin_list_service_types").then(function(types) {
        var html = '<div class="modal-title">Add Service</div>';
        html += '<div class="form-group"><label class="form-label">Service ID</label>'
            + '<input type="text" class="form-input" id="new-svc-id" placeholder="my_service"></div>';
        html += '<div class="form-group"><label class="form-label">Type</label>'
            + '<select class="form-select" id="new-svc-type">';
        (types || []).forEach(function(t) {
            html += '<option value="' + esc(t.type) + '">' + esc(t.name || t.type) + '</option>';
        });
        html += '</select></div>';
        html += '<div class="modal-actions">'
            + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
            + '<button class="btn btn-primary" onclick="editorConfirmAddService()">Add</button></div>';
        showModal(html);
    });
}

function editorConfirmAddService() {
    var id = (document.getElementById("new-svc-id").value || "").trim();
    var type = document.getElementById("new-svc-type").value;
    if (!id) { showToast("Service ID required", "error"); return; }
    pushUndo();
    editorFlowData.services = editorFlowData.services || {};
    editorFlowData.services[id] = {type: type, parameters: {}};
    closeModal();
    renderEditorServices();
    showToast("Service added: " + id, "success");
}

function editorEditService(sid) {
    var svc = (editorFlowData.services || {})[sid];
    if (!svc) return;
    var params = svc.parameters || svc.config || {};
    var html = '<div class="modal-title">Edit Service: ' + esc(sid) + '</div>';
    html += '<div style="color:var(--text-dim);margin-bottom:12px">Type: ' + esc(svc.type || "") + '</div>';
    html += '<div id="svc-edit-fields">';
    Object.keys(params).forEach(function(k) {
        html += '<div class="form-group"><label class="form-label">' + esc(k) + '</label>'
            + '<input type="text" class="form-input svc-edit-field" data-key="' + esc(k)
            + '" value="' + esc(String(params[k] || "")) + '"></div>';
    });
    html += '<div class="form-group"><label class="form-label">Add key</label>'
        + '<div style="display:flex;gap:4px"><input type="text" class="form-input" id="svc-new-key" placeholder="key" style="flex:1">'
        + '<input type="text" class="form-input" id="svc-new-val" placeholder="value" style="flex:1">'
        + '<button class="btn btn-ghost btn-sm" onclick="editorSvcAddField()">+</button></div></div>';
    html += '</div>';
    html += '<div class="modal-actions">'
        + '<button class="btn btn-danger btn-sm" onclick="editorDeleteService(\'' + esc(sid) + '\')">Delete</button>'
        + '<button class="btn btn-ghost" onclick="closeModal()">Cancel</button>'
        + '<button class="btn btn-primary" onclick="editorSaveService(\'' + esc(sid) + '\')">Save</button></div>';
    showModal(html);
}

function editorSvcAddField() {
    var k = document.getElementById("svc-new-key").value.trim();
    var v = document.getElementById("svc-new-val").value;
    if (!k) return;
    var fields = document.getElementById("svc-edit-fields");
    var div = document.createElement("div");
    div.className = "form-group";
    div.innerHTML = '<label class="form-label">' + esc(k) + '</label>'
        + '<input type="text" class="form-input svc-edit-field" data-key="' + esc(k) + '" value="' + esc(v) + '">';
    fields.insertBefore(div, fields.lastElementChild);
    document.getElementById("svc-new-key").value = "";
    document.getElementById("svc-new-val").value = "";
}

function editorSaveService(sid) {
    pushUndo();
    var params = {};
    document.querySelectorAll(".svc-edit-field").forEach(function(el) {
        var val = el.value;
        try { val = JSON.parse(val); } catch(e) {}
        params[el.dataset.key] = val;
    });
    editorFlowData.services[sid].parameters = params;
    closeModal();
    renderEditorServices();
    showToast("Service saved", "success");
}

function editorDeleteService(sid) {
    pushUndo();
    delete editorFlowData.services[sid];
    closeModal();
    renderEditorServices();
    showToast("Service deleted", "info");
}
