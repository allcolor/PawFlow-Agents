// ── PC dialog: stacked detail blocks for one agent ──────────────
function openspaceOpenAgentDialog(key) {
  const rec = _osAgents.get(_osKey(key));
  if (!rec) return;
  const prior = document.getElementById('osvAgentDialog');
  if (prior) prior.remove();
  const overlay = document.createElement('div');
  overlay.className = 'exec-overlay';
  overlay.id = 'osvAgentDialog';
  // No background-click dismissal: modal overlays close only through
  // their explicit close control (repo-wide convention, see
  // test_chat_ui_resources_static.py).

  const dialog = document.createElement('div');
  dialog.className = 'exec-dialog cog-dialog osv-dialog';
  const head = document.createElement('div');
  head.className = 'cog-head';
  const title = document.createElement('h3');
  title.textContent = rec.name + ' \u2014 ' + t('osvActivity');
  head.appendChild(title);
  const close = document.createElement('button');
  close.className = 'cog-close';
  close.innerHTML = '&times;';
  close.onclick = () => overlay.remove();
  dialog.append(close, head);

  const list = document.createElement('div');
  list.className = 'osv-log';
  if (!rec.log.length) {
    const empty = document.createElement('div');
    empty.className = 'osv-log-empty';
    empty.textContent = t('osvNoActivity');
    list.appendChild(empty);
  }
  // Newest first: the block you want is almost always the last thing
  // that happened.
  rec.log.slice().reverse().forEach((entry) => {
    const block = document.createElement('div');
    block.className = 'osv-block osv-block-' + entry.kind;
    const header = document.createElement('div');
    header.className = 'osv-block-head';
    const when = new Date(entry.ts);
    const hh = String(when.getHours()).padStart(2, '0');
    const mm = String(when.getMinutes()).padStart(2, '0');
    const ss = String(when.getSeconds()).padStart(2, '0');
    const icons = { message: '\u{1F4AC}', thought: '\u{1F4AD}',
                    tool: '\u2699\uFE0F', tool_result: '\u2705',
                    delegate: '\u{1F91D}', ask: '\u2753' };
    header.textContent = (icons[entry.kind] || '\u2022') + ' '
      + hh + ':' + mm + ':' + ss + ' \u2014 ' + entry.title;
    const body = document.createElement('div');
    body.className = 'osv-block-body';
    const full = entry.body || '';
    const preview = full.length > OSV_LOG_BLOCK_PREVIEW
      ? full.slice(0, OSV_LOG_BLOCK_PREVIEW) + '\u2026' : full;
    body.textContent = preview;
    if (full.length > OSV_LOG_BLOCK_PREVIEW) {
      block.classList.add('osv-expandable');
      block.addEventListener('click', () => {
        const expanded = block.classList.toggle('osv-expanded');
        body.textContent = expanded ? full : preview;
      });
    }
    block.append(header, body);
    list.appendChild(block);
  });
  dialog.appendChild(list);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
}
