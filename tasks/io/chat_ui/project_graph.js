// ── Project Graph Panel ─────────────────────────────────────────
let _pgReportCache = null;
let _pgQueryCache = [];
let _pgNodeCache = null;

function cmdShowProjectGraph() {
  showProjectGraphOverlay();
}

function showProjectGraphOverlay() {
  let overlay = document.getElementById('pgOverlay');
  if (overlay) overlay.remove();
  overlay = document.createElement('div');
  overlay.id = 'pgOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;z-index:9999';

  overlay.innerHTML = '<div style="background:#1a1a2e;border:1px solid #333;border-radius:12px;padding:20px;max-width:750px;width:90%;max-height:80vh;display:flex;flex-direction:column">'
    + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">'
    + '<h3 style="margin:0;color:#e0e0e0;font-size:16px">Project Graph</h3>'
    + '<button onclick="pgBuild()" style="background:#1e3a5f;color:#4fc3f7;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600">Build</button>'
    + '<button onclick="pgReport()" style="background:#1b4332;color:#52b788;border:none;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px;font-weight:600">Report</button>'
    + '<button onclick="document.getElementById(\'pgOverlay\').remove()" style="background:none;border:none;color:#aaa;cursor:pointer;font-size:18px;margin-left:auto">&times;</button>'
    + '</div>'
    + '<div style="display:flex;gap:6px;margin-bottom:10px">'
    + '<input id="pgSearchInput" type="text" placeholder="' + escapeHtml(t('searchNodesEdges')) + '" style="flex:1;background:#1e1e3a;color:#c0c0d0;border:1px solid #444;border-radius:6px;padding:5px 10px;font-size:12px" onkeydown="if(event.key===\'Enter\')pgSearch()">'
    + '<button onclick="pgSearch()" style="background:#2a2a4a;color:#a0a0c0;border:1px solid #444;border-radius:6px;padding:3px 10px;cursor:pointer;font-size:11px">Search</button>'
    + '</div>'
    + '<div id="pg-content" style="flex:1;overflow-y:auto;border:1px solid #222;border-radius:8px;background:#0d1117;padding:12px">'
    + '<div style="color:#6c6c8a;text-align:center;padding:20px">Use <b>Build</b> to index the codebase, <b>Report</b> to view stats, or <b>Search</b> to query the graph.</div>'
    + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.addEventListener('click', function(e) { if (e.target === overlay) overlay.remove(); });
}

function pgBuild() {
  var content = document.getElementById('pg-content');
  if (content) {
    content.innerHTML = '<div style="color:#4fc3f7;text-align:center;padding:20px">'
      + '<div style="display:inline-block;width:20px;height:20px;border:2px solid #4fc3f7;border-top-color:transparent;border-radius:50%;animation:pgSpin 0.8s linear infinite"></div>'
      + '<div style="margin-top:8px">Building project graph...</div>'
      + '<style>@keyframes pgSpin { to { transform: rotate(360deg); } }</style>'
      + '</div>';
  }
  // Synchronous server-side build (cognitive_ui.project_graph_build).
  // The previous 'call_tool' path dispatched via the agent loop and
  // returned {status:'accepted'} immediately — the panel was stuck on
  // that ack forever.
  action$('project_graph_build', {}).subscribe({
    next: function(data) {
      if (data.error) {
        _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(data.error) + '</div>');
        return;
      }
      var status = data.status || 'unknown';
      var nodes = data.nodes || 0;
      var edges = data.edges || 0;
      var files = data.files || 0;
      var reparsed = data.reparsed;
      var removed = data.removed;
      var msg;
      if (status === 'unchanged') {
        msg = 'Up to date — ' + nodes + ' nodes, ' + edges
          + ' edges, ' + files + ' files (no changes detected)';
      } else if (status === 'built') {
        msg = 'Built — ' + nodes + ' nodes, ' + edges + ' edges, ' + files + ' files';
        if (reparsed !== undefined || removed !== undefined) {
          msg += ' (reparsed=' + (reparsed || 0) + ', removed=' + (removed || 0) + ')';
        }
      } else if (status === 'skipped') {
        msg = t('projectGraphSkipped', { reason: data.reason || t('noReasonGiven') });
      } else if (status === 'error') {
        msg = t('projectGraphErrorStatus', { error: data.reason || t('unknownError') });
      } else {
        msg = JSON.stringify(data);
      }
      var color = (status === 'error') ? '#e74c3c' : '#52b788';
      _pgSetContent('<div style="color:' + color
        + ';padding:8px;white-space:pre-wrap">' + escapeHtml(msg) + '</div>');
    },
    error: function(e) {
      _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(t('projectGraphBuildFailed', { error: e.message })) + '</div>');
    },
  });
}

function pgReport() {
  var content = document.getElementById('pg-content');
  if (content) {
    content.innerHTML = '<div style="color:#6c6c8a;text-align:center;padding:20px">' + escapeHtml(t('loadingReport')) + '</div>';
  }
  action$('project_graph_report', {}).subscribe({
    next: function(data) {
      if (data.error) {
        _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(data.error) + '</div>');
        return;
      }
      _pgReportCache = data;
      _pgRenderReport(data);
    },
    error: function(e) {
      _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(t('projectGraphReportLoadFailed', { error: e.message })) + '</div>');
    },
  });
}

function _pgRenderReport(data) {
  if (!data.has_graph) {
    _pgSetContent('<div style="color:#6c6c8a;text-align:center;padding:20px">' + escapeHtml(t('projectGraphEmptyBuildHint')) + '</div>');
    return;
  }
  var report = data.report || '';
  // Parse the report text into structured display
  var lines = report.split('\n');
  var html = '';

  // Metadata section
  html += '<div style="margin-bottom:12px">';
  html += '<div style="color:#e0e0e0;font-size:14px;font-weight:600;margin-bottom:6px">' + escapeHtml(lines[0] || 'Project Graph') + '</div>';
  if (lines[1]) {
    html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:4px">';
    var parts = lines[1].split(',').map(function(s) { return s.trim(); });
    parts.forEach(function(part) {
      var kv = part.split(':').map(function(s) { return s.trim(); });
      if (kv.length === 2) {
        html += '<span style="background:#1e1e3a;color:#a0a0c0;padding:2px 8px;border-radius:6px;font-size:11px">'
          + '<span style="color:#6c6c8a">' + escapeHtml(kv[0]) + ':</span> '
          + '<span style="color:#e0e0e0;font-weight:600">' + escapeHtml(kv[1]) + '</span></span>';
      }
    });
    html += '</div>';
  }
  html += '</div>';

  // Confidence breakdown
  if (lines[2] && lines[2].startsWith('Confidence:')) {
    html += '<div style="margin-bottom:12px">';
    html += '<div style="color:#a0a0c0;font-size:12px;margin-bottom:4px">' + escapeHtml(t('confidenceBreakdown')) + '</div>';
    var confStr = lines[2].replace('Confidence: ', '');
    var confParts = confStr.split(',').map(function(s) { return s.trim(); });
    html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
    confParts.forEach(function(part) {
      var kv = part.split('=');
      if (kv.length === 2) {
        var conf = kv[0].trim().toUpperCase();
        var count = kv[1].trim();
        var confBg, confColor;
        if (conf === 'INFERRED') { confBg = '#1e3a5f'; confColor = '#4fc3f7'; }
        else if (conf === 'AMBIGUOUS') { confBg = '#5a3a1a'; confColor = '#ffb347'; }
        else { confBg = '#1b4332'; confColor = '#52b788'; }
        html += '<span style="background:' + confBg + ';color:' + confColor
          + ';padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600">'
          + conf + ': ' + count + '</span>';
      }
    });
    html += '</div></div>';
  }

  // God nodes
  var godIdx = lines.findIndex(function(l) { return l.includes('God nodes'); });
  if (godIdx >= 0) {
    html += '<div style="margin-bottom:8px">';
    html += '<div style="color:#a0a0c0;font-size:12px;margin-bottom:6px">' + escapeHtml(t('mostConnectedNodes')) + '</div>';
    for (var i = godIdx + 1; i < lines.length; i++) {
      var line = lines[i].trim();
      if (!line) continue;
      // Parse "  Label (N connections)"
      var match = line.match(/^(.+?)\s*\((\d+)\s+connections?\)$/);
      if (match) {
        var label = match[1];
        var deg = match[2];
        html += '<div onclick="pgNodeDetail(\'' + escapeHtml(label).replace(/'/g, "\\'") + '\')" '
          + 'style="padding:4px 8px;border-bottom:1px solid #222;cursor:pointer;display:flex;align-items:center;gap:8px" '
          + 'onmouseover="this.style.background=\'#1e1e3a\'" onmouseout="this.style.background=\'transparent\'">'
          + '<span style="color:#e0e0e0;font-size:12px;font-weight:600">' + escapeHtml(label) + '</span>'
          + '<span style="color:#6c6c8a;font-size:11px;margin-left:auto">' + escapeHtml(t('connectionsCount', { n: deg })) + '</span>'
          + '</div>';
      }
    }
    html += '</div>';
  }

  _pgSetContent(html);
}

function pgSearch() {
  var input = document.getElementById('pgSearchInput');
  if (!input) return;
  var query = input.value.trim();
  if (!query) return;

  var content = document.getElementById('pg-content');
  if (content) {
    content.innerHTML = '<div style="color:#6c6c8a;text-align:center;padding:20px">' + escapeHtml(t('searching')) + '</div>';
  }
  action$('project_graph_query', { question: query }).subscribe({
    next: function(data) {
      if (data.error) {
        _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(data.error) + '</div>');
        return;
      }
      _pgQueryCache = data.edges || [];
      _pgRenderEdges(_pgQueryCache, query);
    },
    error: function(e) {
      _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(t('projectGraphQueryFailed', { error: e.message })) + '</div>');
    },
  });
}

function _pgRenderEdges(edges, query) {
  if (edges.length === 0) {
    _pgSetContent('<div style="color:#6c6c8a;text-align:center;padding:20px">' + escapeHtml(t('noConnectionsFoundFor', { query: query })) + '</div>');
    return;
  }
  var html = '<div style="color:#a0a0c0;font-size:12px;margin-bottom:8px">' + escapeHtml(t('edgesForQuery', { n: edges.length, query: query })) + '</div>';
  edges.forEach(function(e) {
    var source = e.source || '?';
    var relation = e.relation || '?';
    var target = e.target || '?';
    var conf = (e.confidence || 'EXTRACTED').toUpperCase();
    var confBg, confColor;
    if (conf === 'INFERRED') { confBg = '#1e3a5f'; confColor = '#4fc3f7'; }
    else if (conf === 'AMBIGUOUS') { confBg = '#5a3a1a'; confColor = '#ffb347'; }
    else { confBg = '#1b4332'; confColor = '#52b788'; }
    var confBadge = '<span style="background:' + confBg + ';color:' + confColor
      + ';padding:1px 6px;border-radius:6px;font-size:10px;font-weight:600">' + conf + '</span>';

    html += '<div style="padding:5px 8px;border-bottom:1px solid #222;display:flex;align-items:center;gap:6px">'
      + '<span onclick="pgNodeDetail(\'' + escapeHtml(source).replace(/'/g, "\\'") + '\')" style="color:#e0e0e0;font-size:12px;font-weight:600;cursor:pointer;text-decoration:underline dotted #444" title="' + escapeHtml(t('viewNode')) + '">' + escapeHtml(source) + '</span>'
      + '<span style="color:#6c6c8a;font-size:11px">\u2192</span>'
      + '<span style="color:#a0a0c0;font-size:12px">' + escapeHtml(relation) + '</span>'
      + '<span style="color:#6c6c8a;font-size:11px">\u2192</span>'
      + '<span onclick="pgNodeDetail(\'' + escapeHtml(target).replace(/'/g, "\\'") + '\')" style="color:#e0e0e0;font-size:12px;font-weight:600;cursor:pointer;text-decoration:underline dotted #444" title="' + escapeHtml(t('viewNode')) + '">' + escapeHtml(target) + '</span>'
      + '<span style="margin-left:auto">' + confBadge + '</span>'
      + '</div>';
  });
  _pgSetContent(html);
}

function pgNodeDetail(label) {
  var content = document.getElementById('pg-content');
  if (content) {
    content.innerHTML = '<div style="color:#6c6c8a;text-align:center;padding:20px">' + escapeHtml(t('loadingNode')) + '</div>';
  }
  action$('project_graph_node', { label: label }).subscribe({
    next: function(data) {
      if (data.error) {
        _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(data.error) + '</div>');
        return;
      }
      _pgNodeCache = data;
      _pgRenderNode(data);
    },
    error: function(e) {
      _pgSetContent('<div style="color:#e74c3c;padding:8px">' + escapeHtml(t('projectGraphNodeLoadFailed', { error: e.message })) + '</div>');
    },
  });
}

function _pgRenderNode(node) {
  var html = '<div style="margin-bottom:10px">'
    + '<button onclick="pgReport()" style="background:none;border:none;color:#4fc3f7;cursor:pointer;font-size:11px;padding:0">\u2190 ' + escapeHtml(t('backToReport')) + '</button>'
    + '</div>';

  html += '<div style="margin-bottom:12px">';
  html += '<div style="color:#e0e0e0;font-size:14px;font-weight:600;margin-bottom:6px">' + escapeHtml(node.label || node.id || '?') + '</div>';
  html += '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:4px">';

  if (node.source_file) {
    html += '<span style="background:#1e1e3a;color:#a0a0c0;padding:2px 8px;border-radius:6px;font-size:11px">'
      + '<span style="color:#6c6c8a">' + escapeHtml(t('file')) + ':</span> <span style="color:#e0e0e0">' + escapeHtml(node.source_file) + '</span></span>';
  }
  if (node.file_type) {
    html += '<span style="background:#1e1e3a;color:#a0a0c0;padding:2px 8px;border-radius:6px;font-size:11px">'
      + '<span style="color:#6c6c8a">' + escapeHtml(t('type')) + ':</span> <span style="color:#e0e0e0">' + escapeHtml(node.file_type) + '</span></span>';
  }
  if (node.source_location) {
    html += '<span style="background:#1e1e3a;color:#a0a0c0;padding:2px 8px;border-radius:6px;font-size:11px">'
      + '<span style="color:#6c6c8a">' + escapeHtml(t('location')) + ':</span> <span style="color:#e0e0e0">' + escapeHtml(node.source_location) + '</span></span>';
  }
  if (node.neighbors !== undefined) {
    html += '<span style="background:#1e1e3a;color:#a0a0c0;padding:2px 8px;border-radius:6px;font-size:11px">'
      + '<span style="color:#6c6c8a">' + escapeHtml(t('neighbors')) + ':</span> <span style="color:#e0e0e0">' + node.neighbors + '</span></span>';
  }
  html += '</div></div>';

  // Neighbor edges
  var edges = node.neighbor_edges || [];
  if (edges.length > 0) {
    html += '<div style="color:#a0a0c0;font-size:12px;margin-bottom:6px">' + escapeHtml(t('connectionsHeading', { n: edges.length + (edges.length >= 20 ? '+' : '') })) + '</div>';
    edges.forEach(function(e) {
      var isSource = e.source === node.id;
      var other = isSource ? e.target : e.source;
      var direction = isSource ? '\u2192' : '\u2190';
      var relation = e.relation || '?';
      var conf = (e.confidence || 'EXTRACTED').toUpperCase();
      var confBg, confColor;
      if (conf === 'INFERRED') { confBg = '#1e3a5f'; confColor = '#4fc3f7'; }
      else if (conf === 'AMBIGUOUS') { confBg = '#5a3a1a'; confColor = '#ffb347'; }
      else { confBg = '#1b4332'; confColor = '#52b788'; }
      var confBadge = '<span style="background:' + confBg + ';color:' + confColor
        + ';padding:1px 5px;border-radius:6px;font-size:10px;font-weight:600">' + conf + '</span>';

      html += '<div style="padding:4px 8px;border-bottom:1px solid #222;display:flex;align-items:center;gap:6px">'
        + '<span style="color:#6c6c8a;font-size:11px">' + direction + '</span>'
        + '<span style="color:#a0a0c0;font-size:12px">' + escapeHtml(relation) + '</span>'
        + '<span style="color:#6c6c8a;font-size:11px">' + direction + '</span>'
        + '<span onclick="pgNodeDetail(\'' + escapeHtml(other).replace(/'/g, "\\'") + '\')" style="color:#e0e0e0;font-size:12px;font-weight:600;cursor:pointer;text-decoration:underline dotted #444" title="' + escapeHtml(t('viewNode')) + '">' + escapeHtml(other) + '</span>'
        + '<span style="margin-left:auto">' + confBadge + '</span>'
        + '</div>';
    });
  } else {
    html += '<div style="color:#6c6c8a;font-size:12px">' + escapeHtml(t('noConnectionsFound')) + '</div>';
  }

  _pgSetContent(html);
}

function _pgSetContent(html) {
  var content = document.getElementById('pg-content');
  if (content) content.innerHTML = html;
}
