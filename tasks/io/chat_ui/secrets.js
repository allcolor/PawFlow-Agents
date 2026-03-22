  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListSecrets() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_secrets' }),
    });
    const data = await resp.json();
    const result = data.result || '';
    if (!result || result.includes('No secrets')) {
      addMsg('system', t('secretListEmpty'));
    } else {
      addMsg('system', result);
    }
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdAddVariable(name, value) {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'add_variable', key: name, value: value }),
    });
    const data = await resp.json();
    if (data.error) { addMsg('error', data.error); return; }
    addMsg('system', t('variableAdded', { name, ref: data.key || name, short: name }));
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

async function cmdListVariables() {
  try {
    const resp = await fetch(API, {
      method: 'POST', headers: getAuthHeaders(),
      body: JSON.stringify({ action: 'list_variables' }),
    });
    const data = await resp.json();
    const result = data.result || '';
    if (!result || result.includes('No variables')) {
      addMsg('system', t('variableListEmpty'));
    } else {
      addMsg('system', result);
    }
  } catch (e) { addMsg('error', 'Failed: ' + e.message); }
}

// ── Files panel ─────────────────────────────────────────────────
async function toggleFilesPanel() {
  const panel = document.getElementById('filesPanel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    await loadConvFiles();
  } else {
    panel.style.display = 'none';
  }
}

async function loadConvFiles() {
  if (!conversationId) return;