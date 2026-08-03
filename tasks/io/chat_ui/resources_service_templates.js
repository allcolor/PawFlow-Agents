// Installed service-template catalog. Selecting one only prefills the existing
// service form; creation still goes through the canonical service_install path.

function showServiceCreateDialog() {
  const old = document.getElementById('resourceEditorOverlay');
  if (old) old.remove();
  const overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:8px;padding:20px;width:440px;max-width:calc(100vw - 32px);color:var(--pf-text);';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">'
    + '<h3 style="margin:0;font-size:14px;">' + escapeHtml(t('chooseServiceCreation')) + '</h3>'
    + '<button id="svc-create-close" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button></div>'
    + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'
    + '<button id="svc-create-empty" style="background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:7px;padding:18px 10px;cursor:pointer;font-weight:600;">' + escapeHtml(t('newService')) + '</button>'
    + '<button id="svc-create-template" style="background:color-mix(in srgb,var(--pf-accent) 12%,var(--pf-panel));color:var(--pf-accent);border:1px solid var(--pf-accent);border-radius:7px;padding:18px 10px;cursor:pointer;font-weight:600;">' + escapeHtml(t('newFromTemplate')) + '</button>'
    + '</div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  panel.querySelector('#svc-create-close').onclick = () => overlay.remove();
  panel.querySelector('#svc-create-empty').onclick = () => showServiceInstallForm();
  panel.querySelector('#svc-create-template').onclick = () => showServiceTemplatePicker();
}

async function showServiceTemplatePicker() {
  let templates = [];
  try {
    const data = await rxjs.firstValueFrom(action$('list_service_templates', {
      conversation_id: conversationId || '',
    }));
    if (data.error) { addMsg('error', data.error); return; }
    templates = data.service_templates || [];
  } catch (e) { addMsg('error', e.message); return; }

  const old = document.getElementById('resourceEditorOverlay');
  if (old) old.remove();
  const overlay = document.createElement('div');
  overlay.id = 'resourceEditorOverlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:var(--pf-shadow);display:flex;align-items:center;justify-content:center;z-index:9999;';
  const panel = document.createElement('div');
  panel.style.cssText = 'background:var(--pf-panel);border:1px solid var(--pf-border);border-radius:8px;padding:20px;width:680px;max-width:calc(100vw - 32px);max-height:85vh;display:flex;flex-direction:column;color:var(--pf-text);';
  panel.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
    + '<h3 style="margin:0;font-size:14px;">' + escapeHtml(t('serviceTemplates')) + '</h3>'
    + '<button id="svc-template-close" style="background:none;border:none;color:var(--pf-muted);cursor:pointer;font-size:18px;">&times;</button></div>'
    + '<div style="display:grid;grid-template-columns:minmax(0,1fr) 180px;gap:8px;margin-bottom:10px;">'
    + '<input id="svc-template-search" placeholder="' + escapeAttr(t('search')) + '" style="' + _svcInputStyle + 'margin:0;"/>'
    + '<select id="svc-template-category" style="' + _svcInputStyle + 'margin:0;"></select></div>'
    + '<div id="svc-template-list" style="overflow:auto;min-height:120px;"></div>';
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  panel.querySelector('#svc-template-close').onclick = () => overlay.remove();

  const search = panel.querySelector('#svc-template-search');
  const category = panel.querySelector('#svc-template-category');
  const list = panel.querySelector('#svc-template-list');
  const categories = Array.from(new Set(templates.map(row => String(row.category || 'Other')))).sort();
  category.innerHTML = '<option value="">' + escapeHtml(t('all')) + '</option>'
    + categories.map(value => '<option value="' + escapeAttr(value) + '">' + escapeHtml(value) + '</option>').join('');

  const render = () => {
    const needle = search.value.trim().toLowerCase();
    const wantedCategory = category.value;
    const visible = templates.filter(row => {
      const rowCategory = String(row.category || 'Other');
      const corpus = [row.name, row.title, row.description, row.service_type]
        .concat(row.tags || []).join(' ').toLowerCase();
      return (!wantedCategory || rowCategory === wantedCategory)
        && (!needle || corpus.includes(needle));
    });
    list.innerHTML = '';
    if (!visible.length) {
      list.innerHTML = '<div style="color:var(--pf-muted);padding:20px;text-align:center;">' + escapeHtml(t('noServiceTemplates')) + '</div>';
      return;
    }
    visible.forEach(template => {
      const button = document.createElement('button');
      button.type = 'button';
      button.style.cssText = 'display:block;width:100%;text-align:left;background:var(--pf-sidebar);color:var(--pf-text);border:1px solid var(--pf-border);border-radius:7px;padding:11px;margin-bottom:7px;cursor:pointer;';
      const origin = (template.installed_from || {}).package || '';
      button.innerHTML = '<div style="font-weight:650;">' + escapeHtml(template.title || template.name || '') + '</div>'
        + '<div style="font-size:11px;color:var(--pf-accent);margin-top:2px;">' + escapeHtml(template.category || 'Other') + ' · ' + escapeHtml(template.service_type || '') + '</div>'
        + (template.description ? '<div style="font-size:12px;color:var(--pf-muted);margin-top:5px;">' + escapeHtml(template.description) + '</div>' : '')
        + (origin ? '<div style="font-size:10px;color:var(--pf-muted);margin-top:5px;">' + escapeHtml(origin) + '</div>' : '');
      button.onclick = () => showServiceInstallForm(template);
      list.appendChild(button);
    });
  };
  search.addEventListener('input', render);
  category.addEventListener('change', render);
  render();
  search.focus();
}

function _applyServiceTemplateValues(container, config) {
  Object.entries(config || {}).forEach(([name, value]) => {
    const el = container.querySelector('#svc-p-' + name);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!value;
    else if (value && typeof value === 'object') el.value = JSON.stringify(value, null, 2);
    else el.value = String(value == null ? '' : value);
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
}
