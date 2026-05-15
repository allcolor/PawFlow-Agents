// Hello UI extension — minimal example for PFP ui.v1
// Demonstrates: slot rendering, hook subscription, dialog, command, host call.
//
// Note: this example uses createElement + textContent + appendChild rather
// than `.innerHTML =`. Both work, but the safer pattern keeps the static
// review pipeline silent during install (innerHTML is flagged as a DOM
// injection sink because review cannot tell text from user input).

function _mkSpan(text, cssText) {
  var span = document.createElement('span');
  if (cssText) span.style.cssText = cssText;
  span.textContent = text;
  return span;
}

function _mkLabel(label, desc) {
  var wrap = document.createElement('div');
  var l = document.createElement('div');
  l.className = 'ami-label';
  l.textContent = label;
  var d = document.createElement('div');
  d.className = 'ami-desc';
  d.textContent = desc;
  wrap.appendChild(l);
  wrap.appendChild(d);
  return wrap;
}

pawflow.register('examples.ui-hello', function (pfp) {
  // Add an entry to the action menu.
  pfp.ui.slot('action_menu', 'hello.open', function () {
    var el = document.createElement('div');
    el.className = 'action-menu-item';
    el.style.cssText = 'cursor:pointer;padding:8px 12px;';
    el.appendChild(_mkSpan('👋', null));
    el.appendChild(_mkLabel('Hello extension', 'Open the example dialog'));
    el.addEventListener('click', function () {
      var body = document.createElement('div');
      var p1 = document.createElement('p');
      p1.textContent = 'Hello from examples.ui-hello!';
      var p2 = document.createElement('p');
      p2.textContent = 'This is a PFP UI extension running in your browser.';
      body.appendChild(p1);
      body.appendChild(p2);
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
