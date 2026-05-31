// ── admin_core.js — API, auth, navigation, modal, toast ──

var selectedFlowId = null;
var flowListData = [];
var refreshTimer = null;

function getAuthHeaders() {
    var cookies = document.cookie.split(";");
    for (var i = 0; i < cookies.length; i++) {
        var c = cookies[i].trim();
        if (c.startsWith("pawflow_token=")) {
            return {"X-Session-Token": c.substring(14)};
        }
    }
    return {};
}

function adminFetch(action, params) {
    var body = Object.assign({action: action}, params || {});
    return fetch(ADMIN_API, {
        method: "POST",
        headers: Object.assign({"Content-Type": "application/json"}, getAuthHeaders()),
        credentials: "same-origin",
        body: JSON.stringify(body)
    }).then(function(resp) {
        if (resp.status === 401 || resp.status === 403) {
            if (LOGIN_URL) { window.location.href = LOGIN_URL; }
            throw new Error("Authentication required");
        }
        return resp.json();
    }).then(function(data) {
        if (data && data.error) {
            showToast(data.error, "error");
            throw new Error(data.error);
        }
        return data;
    });
}

function esc(text) {
    if (!text) return "";
    var d = document.createElement("div");
    d.textContent = String(text);
    return d.innerHTML;
}

function formatTime(ts) {
    if (!ts) return "—";
    var d = new Date(ts * 1000);
    return d.toLocaleString();
}

function formatDuration(seconds) {
    if (!seconds || seconds < 0) return "—";
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    if (h > 0) return h + "h " + m + "m";
    if (m > 0) return m + "m " + s + "s";
    return s + "s";
}

function uptime(startTs) {
    if (!startTs) return "—";
    return formatDuration((Date.now() / 1000) - startTs);
}

// ── Modal ──

function showModal(html) {
    document.getElementById("modal-box").innerHTML = html;
    document.getElementById("modal-overlay").style.display = "flex";
}

function closeModal() {
    document.getElementById("modal-overlay").style.display = "none";
    document.getElementById("modal-box").innerHTML = "";
}

// ── Toast ──

function showToast(msg, type) {
    type = type || "info";
    var t = document.createElement("div");
    t.className = "toast toast-" + type;
    t.textContent = msg;
    document.getElementById("toast-container").appendChild(t);
    setTimeout(function() { t.remove(); }, 4000);
}

// ── Context menu ──

var activeCtxMenu = null;

function showContextMenu(e, items) {
    e.preventDefault();
    closeContextMenu();
    var menu = document.createElement("div");
    menu.className = "ctx-menu";
    items.forEach(function(item) {
        if (item === "---") {
            var sep = document.createElement("div");
            sep.className = "ctx-menu-sep";
            menu.appendChild(sep);
        } else {
            var el = document.createElement("div");
            el.className = "ctx-menu-item";
            el.textContent = item.label;
            el.onclick = function() { closeContextMenu(); item.action(); };
            menu.appendChild(el);
        }
    });
    menu.style.left = e.clientX + "px";
    menu.style.top = e.clientY + "px";
    document.body.appendChild(menu);
    activeCtxMenu = menu;
}

function closeContextMenu() {
    if (activeCtxMenu) { activeCtxMenu.remove(); activeCtxMenu = null; }
}

document.addEventListener("click", closeContextMenu);

// ── Navigation ──

function selectFlow(instanceId) {
    selectedFlowId = instanceId;
    // Update sidebar active state
    var items = document.querySelectorAll(".flow-item");
    items.forEach(function(el) {
        el.classList.toggle("active", el.dataset.id === instanceId);
    });
    // Load detail
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("flow-detail").style.display = "block";
    loadFlowDetail(instanceId);
}

// ── Auto-refresh ──

function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(function() {
        if (selectedFlowId) {
            refreshKpis(selectedFlowId);
        }
        refreshFlowListDots();
    }, 5000);
}

function refreshFlowListDots() {
    adminFetch("admin_list_flows").then(function(data) {
        flowListData = data;
        data.forEach(function(f) {
            var dot = document.querySelector('.flow-item[data-id="' + f.instance_id + '"] .flow-dot');
            if (dot) {
                dot.className = "flow-dot " + f.status;
            }
        });
    }).catch(function() {});
}

// ── Init ──

function adminInit() {
    document.getElementById("btn-refresh").onclick = function() {
        loadFlowList();
        if (selectedFlowId) loadFlowDetail(selectedFlowId);
    };
    document.getElementById("btn-deploy").onclick = openDeployModal;
    document.getElementById("flow-search").oninput = filterFlowList;

    // Nav toggle
    document.getElementById("nav-runtime").onclick = function() {
        setNavActive("nav-runtime");
        showRuntimeView();
    };
    document.getElementById("nav-editor").onclick = function() {
        setNavActive("nav-editor");
        showEditorView();
    };
    loadFlowList();
    startAutoRefresh();
}

function setNavActive(id) {
    document.querySelectorAll(".topbar-nav-btn").forEach(function(b) { b.classList.remove("active"); });
    var el = document.getElementById(id);
    if (el) el.classList.add("active");
}
