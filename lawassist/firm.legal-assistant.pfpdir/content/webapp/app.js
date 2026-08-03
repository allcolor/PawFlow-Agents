// legal-shell dashboard — vanilla JS, no framework, no build step.
// Same-origin session as /chat: calls /api/ui directly (no pfp.call
// wrapper on a web_app page — see PFP_DEVELOPER_GUIDE.md "Standalone
// Pages"). _ext identifies the installed package whose ui_extension
// handlers should run; scope is left at the default ("user"), so no
// per-conversation enable step is required just to read the dashboard.

const PACKAGE_ID = 'firm.legal-assistant';
const DB_PATH = '/workspace/legal.db';

const AGENTS = [
  { name: 'assistant', label: 'Assistant', tag: 'Retrouve, ne rédige pas.' },
  { name: 'collegue', label: 'Collègue', tag: 'Relis et propose, ne décide pas.' },
  { name: 'secretaire', label: 'Secrétaire', tag: 'Exécute le répétitif, jamais le sensible.' },
  { name: 'expert', label: 'Expert', tag: 'Cite, ne suppose jamais.' },
];

function callHandler(action, args) {
  return fetch('/api/ui', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ action: action, _ext: PACKAGE_ID }, args || {})),
  }).then(function (r) { return r.json(); });
}

function el(tag, opts) {
  var n = document.createElement(tag);
  opts = opts || {};
  if (opts.text) n.textContent = opts.text;
  if (opts.cls) n.className = opts.cls;
  return n;
}

function renderAgents() {
  var grid = document.getElementById('agent-grid');
  AGENTS.forEach(function (a) {
    var card = el('div', { cls: 'agent-card' });
    card.appendChild(el('div', { cls: 'agent-name', text: a.label }));
    card.appendChild(el('div', { cls: 'agent-tag', text: a.tag }));
    var btn = el('button', { text: 'Ouvrir le chat' });
    btn.addEventListener('click', function () {
      // Chat opens whichever conversation the pawflow_conv cookie names
      // (see tasks/io/serve_chat_ui.py); this only points the browser at
      // /chat, it does not itself pick an agent inside that conversation.
      window.open('/chat', '_blank');
    });
    card.appendChild(btn);
    grid.appendChild(card);
  });
}

function urgencyLabel(jours) {
  if (jours === null || jours === undefined) return '?';
  if (jours <= 1) return 'J-1';
  if (jours <= 7) return 'J-7';
  if (jours <= 30) return 'J-30';
  return String(jours) + 'j';
}

function loadDossiers() {
  var status = document.getElementById('delais-status');
  status.textContent = 'Chargement…';
  callHandler('legal.list_dossiers', { db_path: DB_PATH }).then(function (resp) {
    var result = (resp && resp.result) || {};
    if (resp.error || result.error) {
      status.textContent = (resp.error || result.error);
      return;
    }
    status.textContent = '';

    var delais = result.delais || [];
    var dTable = document.getElementById('delais-table');
    var dBody = dTable.querySelector('tbody');
    dBody.innerHTML = '';
    delais.forEach(function (d) {
      var tr = document.createElement('tr');
      var urg = urgencyLabel(d.jours_restants);
      tr.className = 'urg-' + (urg === 'J-1' ? 'high' : urg === 'J-7' ? 'mid' : 'low');
      [urg, d.client, d.type_acte, d.date_butoir, d.confirme_par_avocate ? 'oui' : 'À VÉRIFIER'].forEach(function (v) {
        var td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });
      dBody.appendChild(tr);
    });
    dTable.hidden = delais.length === 0;
    if (delais.length === 0) status.textContent = 'Aucune échéance active.';

    var dossiers = result.dossiers || [];
    var oTable = document.getElementById('dossiers-table');
    var oBody = oTable.querySelector('tbody');
    oBody.innerHTML = '';
    dossiers.forEach(function (d) {
      var tr = document.createElement('tr');
      [d.client, d.statut, d.derniere_activite].forEach(function (v) {
        var td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });
      oBody.appendChild(tr);
    });
    oTable.hidden = dossiers.length === 0;
  }).catch(function () {
    status.textContent = 'Erreur réseau lors du chargement.';
  });
}

function wireDefaultEndpoint() {
  var input = document.getElementById('conv-input');
  var status = document.getElementById('default-status');
  callHandler('legal.get_default', {}).then(function (resp) {
    var result = (resp && resp.result) || {};
    if (result.default_conversation_id) input.value = result.default_conversation_id;
  });
  document.getElementById('set-default-btn').addEventListener('click', function () {
    var id = input.value.trim();
    if (!id) { status.textContent = 'Entrez un ID de conversation.'; return; }
    callHandler('legal.set_default', { conversation_id: id }).then(function (resp) {
      var result = (resp && resp.result) || {};
      if (resp.error || result.error) {
        status.textContent = resp.error || result.error;
        return;
      }
      // Mirror the preference into the cookie /chat reads, so "Ouvrir le
      // chat" above opens this conversation right away too.
      document.cookie = 'pawflow_conv=' + encodeURIComponent(id) + ';path=/';
      status.textContent = 'Défini.';
    });
  });
}

renderAgents();
loadDossiers();
wireDefaultEndpoint();
