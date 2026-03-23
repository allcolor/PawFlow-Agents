// ── runtime_queues.js — Queue stats & KPIs ──

function renderQueuesTab(data) {
    var stats = data.queue_stats || [];
    if (stats.length === 0 && data.status !== "running") {
        return '<div style="color:var(--text-dim)">Queue stats available when flow is running</div>';
    }
    if (stats.length === 0) {
        return '<div style="color:var(--text-dim)">No queues</div>';
    }
    return renderQueuesFromStats(stats);
}

function renderQueuesFromStats(stats) {
    if (!stats || stats.length === 0) {
        return '<div style="color:var(--text-dim)">No queue data</div>';
    }

    var html = '<div class="card">';
    html += '<div class="card-title">Connection Queues</div>';
    html += '<table>';
    html += '<tr><th>Source</th><th>Target</th><th>Size</th><th>Capacity</th><th>Fill</th><th>Backpressure</th></tr>';

    stats.forEach(function(q) {
        var size = q.queue_size || 0;
        var max = q.max_queue_size || 1000;
        var pct = max > 0 ? Math.round((size / max) * 100) : 0;
        var fillClass = pct < 50 ? "green" : pct < 80 ? "yellow" : "red";
        var bp = q.backpressured;
        var bpHtml = bp
            ? '<span style="color:var(--red);font-weight:600">ACTIVE</span>'
            : '<span style="color:var(--green)">OK</span>';

        html += '<tr>'
            + '<td>' + esc(q.source || "") + '</td>'
            + '<td>' + esc(q.target || "") + '</td>'
            + '<td>' + size + '</td>'
            + '<td>' + max + '</td>'
            + '<td style="min-width:120px">'
            + '<div style="display:flex;align-items:center;gap:8px">'
            + '<div class="progress-bar" style="flex:1">'
            + '<div class="progress-fill ' + fillClass + '" style="width:' + pct + '%"></div>'
            + '</div>'
            + '<span style="font-size:12px;color:var(--text-dim)">' + pct + '%</span>'
            + '</div></td>'
            + '<td>' + bpHtml + '</td>'
            + '</tr>';
    });

    html += '</table></div>';

    // Summary KPIs
    var totalSize = 0;
    var totalCapacity = 0;
    var bpCount = 0;
    stats.forEach(function(q) {
        totalSize += q.queue_size || 0;
        totalCapacity += q.max_queue_size || 0;
        if (q.backpressured) bpCount++;
    });

    var summaryHtml = '<div class="kpi-grid" style="margin-top:16px">';
    summaryHtml += kpiCard(stats.length, "Queues");
    summaryHtml += kpiCard(totalSize, "Total Items");
    summaryHtml += kpiCard(totalCapacity > 0 ? Math.round((totalSize / totalCapacity) * 100) + "%" : "—", "Avg Fill");
    summaryHtml += kpiCard(bpCount, "Backpressured");
    summaryHtml += '</div>';

    return summaryHtml + html;
}
