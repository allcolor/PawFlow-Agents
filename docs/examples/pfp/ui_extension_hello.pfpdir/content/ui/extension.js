// Hello UI extension — minimal example for PFP ui.v1
// Demonstrates: slot rendering, hook subscription, dialog, command, host call.

pawflow.register('examples.ui-hello', function (pfp) {
  // Add an entry to the action menu.
  pfp.ui.slot('action_menu', 'hello.open', function () {
    var el = document.createElement('div');
    el.className = 'action-menu-item';
    el.style.cssText = 'cursor:pointer;padding:8px 12px;';
    el.innerHTML = '<span class="ami-icon">👋</span>'
      + '<div><div class="ami-label">Hello extension</div>'
      + '<div class="ami-desc">Open the example dialog</div></div>';
    el.addEventListener('click', function () {
      var body = document.createElement('div');
      body.innerHTML = '<p>Hello from <code>examples.ui-hello</code>!</p>'
        + '<p>This is a PFP UI extension running in your browser.</p>';
      pfp.ui.openDialog('Hello', body);
    });
    return el;
  });

  // Add a collapsible section to the resources panel.
  pfp.ui.slot('resources_panel', 'hello.section', function () {
    var details = document.createElement('details');
    details.style.cssText = 'margin-top:4px;font-size:12px;';
    var summary = document.createElement('summary');
    summary.textContent = 'Hello extension';
    summary.style.cssText = 'cursor:pointer;color:var(--pf-muted);';
    details.appendChild(summary);
    var body = document.createElement('div');
    body.style.cssText = 'margin-top:6px;color:var(--pf-text);';
    body.textContent = 'Active.';
    details.appendChild(body);
    return details;
  });

  // React to conversation changes.
  pfp.on('conversation_changed', function (payload) {
    console.info('[ui-hello] conversation switched',
      payload.oldCid, '→', payload.newCid);
  });

  pfp.on('boot', function (info) {
    console.info('[ui-hello] boot, ui.v1 =', info.ui_api_version);
  });
});
