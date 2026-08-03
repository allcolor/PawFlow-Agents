// legal-shell UI extension: no chat-page injection beyond a small resources
// panel section pointing at the dedicated web_app dashboard (served at
// /apps/firm.legal-assistant/legal-shell/, see the ↗ link PawFlow adds to
// the Packages sidebar automatically for any package with a web_app object).
// This section just makes the same link discoverable without opening the
// sidebar's Packages tab first, and keeps a place for the current default
// dossier once set from the dashboard.

pawflow.register('firm.legal-assistant', function (pfp) {
  pfp.ui.slot('resources_panel', 'legal.section', function () {
    var details = document.createElement('details');
    details.style.cssText = 'margin-top:4px;font-size:12px;';
    var summary = document.createElement('summary');
    summary.textContent = 'Cabinet — tableau de bord';
    summary.style.cssText = 'cursor:pointer;color:var(--pf-muted);';
    details.appendChild(summary);

    var body = document.createElement('div');
    body.style.cssText = 'margin-top:6px;color:var(--pf-text);';

    var link = document.createElement('a');
    link.href = '/apps/firm.legal-assistant/legal-shell/';
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Ouvrir le tableau de bord →';
    link.style.cssText = 'display:block;margin-bottom:4px;';
    body.appendChild(link);

    var hint = document.createElement('div');
    hint.style.cssText = 'color:var(--pf-muted);font-size:11px;';
    hint.textContent = 'Dossiers, échéances, accès aux 4 agents.';
    body.appendChild(hint);

    details.appendChild(body);
    return details;
  });
});
