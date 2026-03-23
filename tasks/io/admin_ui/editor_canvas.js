// ── editor_canvas.js — Interactive SVG-based flow editor ──

var editorFlowData = null;
var editorSelection = [];  // selected node IDs
var editorDrag = null;     // {id, offsetX, offsetY} during drag
var editorConnect = null;  // {sourceId} during connection draw
var editorPan = null;      // {startX, startY, origTx, origTy}
var editorTransform = {x: 0, y: 0, scale: 1};
var editorUndoStack = [];
var editorRedoStack = [];
var NODE_W = 160, NODE_H = 48, PORT_R = 6;

// Category colors matching gui/components/color_scheme.py
var CATEGORY_COLORS = {
    System: "#6c757d", IO: "#0d6efd", Cloud: "#0dcaf0", Data: "#198754",
    Control: "#fd7e14", Messaging: "#20c997", Sync: "#6f42c1",
    Monitoring: "#6610f2", AI: "#d63384", Plugins: "#adb5bd"
};
var TASK_CATEGORIES = {
    log:"System", updateAttribute:"System", replace_text:"System", wait:"System",
    fail:"System", generateFlowFile:"System", hashContent:"System", listFiles:"System",
    executeScript:"System", getFile:"IO", putFile:"IO", fetchHTTP:"IO", listenHTTP:"IO",
    getSFTP:"IO", putSFTP:"IO", listSFTP:"IO", getFTP:"IO", putFTP:"IO",
    httpReceiver:"IO", handleHTTPResponse:"IO", validateHTTPAuth:"IO", scraplingFetch:"IO",
    putS3:"Cloud", getS3:"Cloud", putGCS:"Cloud", getGCS:"Cloud",
    putAzureBlob:"Cloud", getAzureBlob:"Cloud",
    transformJSON:"Data", evaluateJSONPath:"Data", extractText:"Data",
    compressContent:"Data", validateJSON:"Data", convertCharset:"Data",
    filterContent:"Data", base64Encode:"Data", countText:"Data",
    convertCSVToJSON:"Data", convertJSONToCSV:"Data", executeSQL:"Data", putSQL:"Data",
    putCache:"Data", getCache:"Data", detectDuplicate:"Data", attributesToJSON:"Data",
    splitJSON:"Data", routeOnAttribute:"Control", splitContent:"Control",
    mergeContent:"Control", duplicateContent:"Control", funnel:"Control",
    inputPort:"Control", outputPort:"Control", controlRate:"Control",
    publishKafka:"Messaging", consumeKafka:"Messaging", publishMQTT:"Messaging",
    consumeMQTT:"Messaging", sendEmail:"Messaging", notifySlack:"Messaging",
    waitForSignal:"Sync", notify:"Sync", reporting:"Monitoring",
    inferLLM:"AI", agentLoop:"AI"
};

function taskColor(type) {
    var cat = TASK_CATEGORIES[type] || "Plugins";
    return CATEGORY_COLORS[cat] || "#adb5bd";
}

function taskCategory(type) {
    return TASK_CATEGORIES[type] || "Plugins";
}

// ── Init editor ──

function initEditor() {
    if (!editorFlowData) {
        editorFlowData = {
            id: "", name: "New Flow", version: "1.0.0", description: "", author: "",
            parameters: {}, variables: {},
            tasks: {}, relations: [], services: {},
            entries: [], exits: []
        };
    }
    renderCanvas();
}

function loadFlowIntoEditor(flowJson) {
    pushUndo();
    editorFlowData = JSON.parse(JSON.stringify(flowJson));
    // Ensure positions exist
    var tasks = editorFlowData.tasks || {};
    var i = 0;
    Object.keys(tasks).forEach(function(tid) {
        if (tasks[tid].x == null) { tasks[tid].x = 100 + (i % 4) * 200; }
        if (tasks[tid].y == null) { tasks[tid].y = 80 + Math.floor(i / 4) * 100; }
        i++;
    });
    editorSelection = [];
    renderCanvas();
    renderEditorPalette();
    renderEditorConfig();
    showToast("Flow loaded: " + (editorFlowData.name || ""), "success");
}

// ── Canvas rendering ──

function renderCanvas() {
    var container = document.getElementById("editor-canvas");
    if (!container) return;
    var data = editorFlowData;
    if (!data) { container.innerHTML = ""; return; }

    var svgW = container.clientWidth || 800;
    var svgH = container.clientHeight || 500;
    var t = editorTransform;

    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" '
        + 'id="editor-svg" style="background:#0a0f1e">';
    svg += '<defs>';
    svg += '<marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">'
        + '<path d="M0,0 L8,3 L0,6" fill="#556" /></marker>';
    svg += '<marker id="arrow-sel" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">'
        + '<path d="M0,0 L8,3 L0,6" fill="#4a9eff" /></marker>';
    svg += '</defs>';
    svg += '<g id="canvas-group" transform="translate(' + t.x + ',' + t.y + ') scale(' + t.scale + ')">';

    // Connections
    var rels = data.relations || [];
    rels.forEach(function(r, idx) {
        var src = (data.tasks || {})[r.from];
        var tgt = (data.tasks || {})[r.to];
        if (!src || !tgt) return;
        var x1 = (src.x || 0) + NODE_W;
        var y1 = (src.y || 0) + NODE_H / 2;
        var x2 = (tgt.x || 0);
        var y2 = (tgt.y || 0) + NODE_H / 2;
        var mx = (x1 + x2) / 2;
        var relColor = r.type === "failure" ? "#f85149" : r.type === "success" ? "#3fb950" : "#556";
        svg += '<path d="M' + x1 + ',' + y1 + ' C' + mx + ',' + y1 + ' ' + mx + ',' + y2 + ' ' + x2 + ',' + y2 + '"'
            + ' fill="none" stroke="' + relColor + '" stroke-width="2" marker-end="url(#arrow)"'
            + ' data-edge="' + idx + '" class="edge" />';
        // Relationship label
        if (r.type) {
            var lx = mx, ly = (y1 + y2) / 2 - 8;
            svg += '<text x="' + lx + '" y="' + ly + '" fill="#667" font-size="10" text-anchor="middle">'
                + esc(r.type) + '</text>';
        }
    });

    // Nodes
    var tasks = data.tasks || {};
    Object.keys(tasks).forEach(function(tid) {
        var task = tasks[tid];
        var x = task.x || 0, y = task.y || 0;
        var color = taskColor(task.type || "");
        var sel = editorSelection.indexOf(tid) >= 0;
        var strokeColor = sel ? "#4a9eff" : "#2a3a5c";
        var strokeW = sel ? 2.5 : 1;

        svg += '<g class="node" data-id="' + esc(tid) + '" transform="translate(' + x + ',' + y + ')">';
        // Shadow
        svg += '<rect x="2" y="2" rx="6" ry="6" width="' + NODE_W + '" height="' + NODE_H + '" fill="rgba(0,0,0,0.3)" />';
        // Body
        svg += '<rect rx="6" ry="6" width="' + NODE_W + '" height="' + NODE_H
            + '" fill="#1a2744" stroke="' + strokeColor + '" stroke-width="' + strokeW + '" />';
        // Category bar
        svg += '<rect rx="6" ry="0" width="' + NODE_W + '" height="4" fill="' + color + '" />';
        svg += '<rect x="0" y="2" width="' + NODE_W + '" height="2" fill="' + color + '" />';
        // Label
        var label = task.name || tid;
        if (label.length > 20) label = label.substring(0, 18) + "..";
        svg += '<text x="' + (NODE_W / 2) + '" y="22" fill="#e0e0e0" font-size="12" font-weight="500" text-anchor="middle">'
            + esc(label) + '</text>';
        // Type sublabel
        svg += '<text x="' + (NODE_W / 2) + '" y="38" fill="#667" font-size="10" text-anchor="middle">'
            + esc(task.type || "") + '</text>';
        // Input port
        svg += '<circle cx="0" cy="' + (NODE_H / 2) + '" r="' + PORT_R + '" fill="#1a2744" stroke="' + color + '" stroke-width="1.5" class="port port-in" />';
        // Output port
        svg += '<circle cx="' + NODE_W + '" cy="' + (NODE_H / 2) + '" r="' + PORT_R + '" fill="' + color + '" class="port port-out" />';
        svg += '</g>';
    });

    svg += '</g></svg>';
    container.innerHTML = svg;

    // Attach events
    var svgEl = document.getElementById("editor-svg");
    if (svgEl) {
        svgEl.onmousedown = canvasMouseDown;
        svgEl.onmousemove = canvasMouseMove;
        svgEl.onmouseup = canvasMouseUp;
        svgEl.onwheel = canvasWheel;
    }
}

// ── Mouse interactions ──

function svgPoint(e) {
    var t = editorTransform;
    var rect = document.getElementById("editor-svg").getBoundingClientRect();
    return {
        x: (e.clientX - rect.left - t.x) / t.scale,
        y: (e.clientY - rect.top - t.y) / t.scale
    };
}

function canvasMouseDown(e) {
    var node = e.target.closest(".node");
    var port = e.target.closest(".port-out");

    if (port) {
        // Start connection from output port
        var nodeEl = port.closest(".node");
        if (nodeEl) {
            editorConnect = {sourceId: nodeEl.dataset.id};
            e.stopPropagation();
            return;
        }
    }

    if (node) {
        var tid = node.dataset.id;
        if (e.shiftKey) {
            // Toggle multi-select
            var idx = editorSelection.indexOf(tid);
            if (idx >= 0) editorSelection.splice(idx, 1);
            else editorSelection.push(tid);
        } else if (editorSelection.indexOf(tid) < 0) {
            editorSelection = [tid];
        }
        // Start drag
        var pt = svgPoint(e);
        var task = editorFlowData.tasks[tid];
        if (task) {
            editorDrag = {id: tid, offsetX: pt.x - (task.x || 0), offsetY: pt.y - (task.y || 0)};
        }
        renderCanvas();
        renderEditorConfig();
        e.stopPropagation();
        return;
    }

    // Click on background — deselect or start pan
    if (e.target === document.getElementById("editor-svg") || e.target.closest("#canvas-group")) {
        if (!e.target.closest(".node") && !e.target.closest(".edge")) {
            editorSelection = [];
            renderCanvas();
            renderEditorConfig();
        }
        editorPan = {startX: e.clientX, startY: e.clientY, origTx: editorTransform.x, origTy: editorTransform.y};
    }
}

function canvasMouseMove(e) {
    if (editorDrag) {
        var pt = svgPoint(e);
        var task = editorFlowData.tasks[editorDrag.id];
        if (task) {
            task.x = Math.round(pt.x - editorDrag.offsetX);
            task.y = Math.round(pt.y - editorDrag.offsetY);
            renderCanvas();
        }
    } else if (editorPan) {
        editorTransform.x = editorPan.origTx + (e.clientX - editorPan.startX);
        editorTransform.y = editorPan.origTy + (e.clientY - editorPan.startY);
        var g = document.getElementById("canvas-group");
        if (g) g.setAttribute("transform", "translate(" + editorTransform.x + "," + editorTransform.y + ") scale(" + editorTransform.scale + ")");
    }
}

function canvasMouseUp(e) {
    if (editorDrag) {
        editorDrag = null;
    }
    if (editorConnect) {
        // Check if dropped on an input port or node
        var node = e.target.closest(".node");
        if (node && node.dataset.id !== editorConnect.sourceId) {
            pushUndo();
            editorFlowData.relations = editorFlowData.relations || [];
            editorFlowData.relations.push({
                from: editorConnect.sourceId,
                to: node.dataset.id,
                type: "success"
            });
            renderCanvas();
            showToast("Connection added", "info");
        }
        editorConnect = null;
    }
    if (editorPan) { editorPan = null; }
}

function canvasWheel(e) {
    e.preventDefault();
    var delta = e.deltaY > 0 ? -0.1 : 0.1;
    editorTransform.scale = Math.max(0.2, Math.min(3, editorTransform.scale + delta));
    var g = document.getElementById("canvas-group");
    if (g) g.setAttribute("transform", "translate(" + editorTransform.x + "," + editorTransform.y + ") scale(" + editorTransform.scale + ")");
}

// ── Node operations ──

function editorAddTask(taskType, taskName, x, y) {
    pushUndo();
    var id = taskType + "_" + Math.random().toString(36).substr(2, 5);
    editorFlowData.tasks = editorFlowData.tasks || {};
    editorFlowData.tasks[id] = {
        type: taskType,
        name: taskName || taskType,
        parameters: {},
        x: x != null ? x : 200 + Object.keys(editorFlowData.tasks).length * 30,
        y: y != null ? y : 100 + Object.keys(editorFlowData.tasks).length * 20
    };
    editorSelection = [id];
    renderCanvas();
    renderEditorConfig();
    return id;
}

function editorDeleteSelected() {
    if (editorSelection.length === 0) return;
    pushUndo();
    editorSelection.forEach(function(tid) {
        delete editorFlowData.tasks[tid];
        editorFlowData.relations = (editorFlowData.relations || []).filter(function(r) {
            return r.from !== tid && r.to !== tid;
        });
        // Remove from entries/exits
        editorFlowData.entries = (editorFlowData.entries || []).filter(function(e) { return e !== tid; });
        editorFlowData.exits = (editorFlowData.exits || []).filter(function(e) { return e !== tid; });
    });
    editorSelection = [];
    renderCanvas();
    renderEditorConfig();
    showToast("Deleted", "info");
}

function editorDeleteConnection(idx) {
    pushUndo();
    editorFlowData.relations.splice(idx, 1);
    renderCanvas();
}

// ── Undo / Redo ──

function pushUndo() {
    editorUndoStack.push(JSON.stringify(editorFlowData));
    if (editorUndoStack.length > 50) editorUndoStack.shift();
    editorRedoStack = [];
}

function editorUndo() {
    if (editorUndoStack.length === 0) return;
    editorRedoStack.push(JSON.stringify(editorFlowData));
    editorFlowData = JSON.parse(editorUndoStack.pop());
    editorSelection = [];
    renderCanvas();
    renderEditorConfig();
}

function editorRedo() {
    if (editorRedoStack.length === 0) return;
    editorUndoStack.push(JSON.stringify(editorFlowData));
    editorFlowData = JSON.parse(editorRedoStack.pop());
    editorSelection = [];
    renderCanvas();
    renderEditorConfig();
}

// ── Auto-layout (simple layered) ──

function editorAutoLayout() {
    if (!editorFlowData || !editorFlowData.tasks) return;
    pushUndo();
    var tasks = editorFlowData.tasks;
    var rels = editorFlowData.relations || [];
    var ids = Object.keys(tasks);
    if (ids.length === 0) return;

    // Build adjacency: target → sources
    var incoming = {};
    ids.forEach(function(id) { incoming[id] = []; });
    rels.forEach(function(r) {
        if (incoming[r.to]) incoming[r.to].push(r.from);
    });

    // Assign layers via topological sort
    var layers = {};
    var visited = {};
    function assignLayer(id) {
        if (visited[id]) return layers[id] || 0;
        visited[id] = true;
        var maxParent = -1;
        (incoming[id] || []).forEach(function(pid) {
            if (tasks[pid]) maxParent = Math.max(maxParent, assignLayer(pid));
        });
        layers[id] = maxParent + 1;
        return layers[id];
    }
    ids.forEach(assignLayer);

    // Group by layer
    var byLayer = {};
    ids.forEach(function(id) {
        var l = layers[id] || 0;
        if (!byLayer[l]) byLayer[l] = [];
        byLayer[l].push(id);
    });

    // Position
    var layerKeys = Object.keys(byLayer).map(Number).sort(function(a,b) { return a-b; });
    layerKeys.forEach(function(l, li) {
        var nodes = byLayer[l];
        nodes.forEach(function(id, ni) {
            tasks[id].x = 80 + li * 220;
            tasks[id].y = 60 + ni * 80;
        });
    });

    renderCanvas();
    showToast("Auto-layout applied", "info");
}

// ── Keyboard shortcuts ──

document.addEventListener("keydown", function(e) {
    // Only when editor tab is active
    if (!document.getElementById("editor-canvas")) return;
    if (document.activeElement && document.activeElement.tagName === "INPUT") return;
    if (document.activeElement && document.activeElement.tagName === "TEXTAREA") return;
    if (document.activeElement && document.activeElement.tagName === "SELECT") return;

    if (e.key === "Delete" || e.key === "Backspace") {
        editorDeleteSelected();
        e.preventDefault();
    } else if (e.ctrlKey && e.key === "z") {
        editorUndo();
        e.preventDefault();
    } else if (e.ctrlKey && e.key === "y") {
        editorRedo();
        e.preventDefault();
    } else if (e.ctrlKey && e.key === "s") {
        editorSaveFlow();
        e.preventDefault();
    }
});

// ── Drop from palette ──

function canvasHandleDrop(e) {
    e.preventDefault();
    var taskType = e.dataTransfer.getData("text/plain");
    if (!taskType) return;
    var pt = svgPoint(e);
    editorAddTask(taskType, null, Math.round(pt.x), Math.round(pt.y));
}

function canvasHandleDragOver(e) { e.preventDefault(); }
