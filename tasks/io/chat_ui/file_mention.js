// Unified composer picker. Slash commands come from HELP_DATA; @ mentions come
// from the configured and active agents already present in the page state.

var _composerPickerState = {
  kind: '',
  start: -1,
  end: -1,
  selected: 0,
  items: [],
};

function _composerUsesMobileActions() {
  return !!(window.matchMedia && window.matchMedia('(max-width: 768px)').matches);
}

function _composerSetActionsOpen(open) {
  const panel = document.getElementById('composerMobileActions');
  const toggle = document.getElementById('composerMobileActionsBtn');
  if (!panel || !toggle) return;
  const expanded = _composerUsesMobileActions() && !!open;
  panel.dataset.open = expanded ? 'true' : 'false';
  panel.setAttribute('aria-hidden', _composerUsesMobileActions() && !expanded ? 'true' : 'false');
  toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
}

function toggleComposerMobileActions(force) {
  const panel = document.getElementById('composerMobileActions');
  if (!panel || !_composerUsesMobileActions()) return;
  const next = typeof force === 'boolean' ? force : panel.dataset.open !== 'true';
  _composerSetActionsOpen(next);
}

function _composerPickerElement() {
  return document.getElementById('composerPicker');
}

function _composerInput() {
  return document.getElementById('input');
}

function _composerAgentPickerElement() {
  return document.getElementById('composerAgentPicker');
}

function updateComposerAgentBadge() {
  const overlay = document.getElementById('composerAgentOverlay');
  const badge = document.getElementById('composerAgentBadge');
  const label = document.getElementById('composerAgentBadgeLabel');
  const agent = typeof selectedAgent !== 'undefined' ? String(selectedAgent || '') : '';
  if (!overlay || !badge || !label) return;
  overlay.hidden = !agent;
  if (!agent) {
    label.textContent = '';
    _composerCloseAgentPicker();
    return;
  }
  const display = typeof displayAgentName === 'function' ? displayAgentName(agent) : agent;
  label.textContent = t('selectedAgentLabel', { name: display || agent });
  badge.title = t('selectAgent') + ': ' + (display || agent);
}

function _composerCloseAgentPicker() {
  const picker = _composerAgentPickerElement();
  const badge = document.getElementById('composerAgentBadge');
  if (picker) picker.hidden = true;
  if (badge) badge.setAttribute('aria-expanded', 'false');
}

function _composerSelectAgent(name) {
  _composerCloseAgentPicker();
  if (!name || name === selectedAgent || typeof cmdAgentSelect !== 'function') return;
  const selection = cmdAgentSelect(name);
  if (selection && typeof selection.catch === 'function') {
    selection.catch(error => console.error('composer: agent selection failed', error));
  }
}

function _composerRenderAgentPicker() {
  const picker = _composerAgentPickerElement();
  const badge = document.getElementById('composerAgentBadge');
  if (!picker || !badge) return;
  const items = _composerMentionChoices('');
  picker.innerHTML = items.length ? items.map(item => {
    const current = item.value === selectedAgent;
    return '<button class="composer-agent-option" type="button" role="option" aria-selected="'
      + (current ? 'true' : 'false') + '" data-agent="' + escapeHtml(item.value) + '">'
      + '<span class="composer-agent-option-dot" aria-hidden="true"></span>'
      + '<span class="composer-agent-option-name">' + escapeHtml(item.label) + '</span>'
      + (item.description ? '<span class="composer-agent-option-real">' + escapeHtml(item.description) + '</span>' : '')
      + '</button>';
  }).join('') : '<div class="composer-picker-empty">' + escapeHtml(t('noAgents')) + '</div>';
  picker.querySelectorAll('.composer-agent-option').forEach(row => {
    row.addEventListener('click', () => _composerSelectAgent(row.dataset.agent));
  });
  picker.hidden = false;
  badge.setAttribute('aria-expanded', 'true');
}

function toggleComposerAgentPicker(force) {
  const picker = _composerAgentPickerElement();
  if (!picker) return;
  const open = typeof force === 'boolean' ? force : picker.hidden;
  _composerClosePicker();
  _composerSetActionsOpen(false);
  if (open) _composerRenderAgentPicker();
  else _composerCloseAgentPicker();
}

function _composerSlashChoices(query) {
  const needle = String(query || '').toLocaleLowerCase();
  if (typeof HELP_DATA === 'undefined') return [];
  return Object.keys(HELP_DATA).sort().filter(command => {
    const spec = HELP_DATA[command] || {};
    if (spec.alias) return false;
    const searchable = command + ' ' + String(spec.short || '') + ' ' + String(spec.usage || '');
    return !needle || searchable.toLocaleLowerCase().includes(needle);
  }).map(command => {
    const spec = HELP_DATA[command] || {};
    return {
      value: command,
      label: command,
      description: String(spec.short || spec.usage || ''),
    };
  });
}

function _composerMentionChoices(query) {
  const names = [];
  const seen = new Set();
  const add = value => {
    const name = String(value || '').trim();
    const key = name.toLocaleLowerCase();
    if (!name || seen.has(key)) return;
    seen.add(key);
    names.push(name);
  };
  if (typeof selectedAgent !== 'undefined') add(selectedAgent);
  if (typeof _lastResourcesData !== 'undefined' && _lastResourcesData) {
    ((_lastResourcesData.agents || [])).forEach(agent => add(agent && agent.name));
  }
  if (typeof activeInteractions !== 'undefined' && activeInteractions) {
    Object.values(activeInteractions).forEach(interaction => add(interaction && interaction.name));
  }
  if (typeof nicknameMap !== 'undefined' && nicknameMap) Object.keys(nicknameMap).forEach(add);

  const needle = String(query || '').toLocaleLowerCase();
  return names.map(name => {
    const label = typeof displayAgentName === 'function' ? displayAgentName(name) : name;
    return {
      value: name,
      label: label || name,
      description: label && label !== name ? name : '',
    };
  }).filter(item => !needle
    || item.value.toLocaleLowerCase().includes(needle)
    || item.label.toLocaleLowerCase().includes(needle));
}

function _composerTriggerAtCaret(input) {
  if (!input) return null;
  const end = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
  const before = String(input.value || '').slice(0, end);
  const match = before.match(/(^|\s)([/@])([^\s]*)$/);
  if (!match) return null;
  const trigger = match[2];
  return {
    kind: trigger === '/' ? 'slash' : 'mention',
    query: match[3],
    start: end - match[3].length - 1,
    end,
  };
}

function _composerSetExpanded(kind, expanded) {
  const slash = document.getElementById('composerSlashBtn');
  const mention = document.getElementById('composerMentionBtn');
  if (slash) slash.setAttribute('aria-expanded', expanded && kind === 'slash' ? 'true' : 'false');
  if (mention) mention.setAttribute('aria-expanded', expanded && kind === 'mention' ? 'true' : 'false');
}

function _composerClosePicker() {
  const picker = _composerPickerElement();
  if (picker) picker.hidden = true;
  _composerSetExpanded('', false);
  _composerPickerState = { kind: '', start: -1, end: -1, selected: 0, items: [] };
}

function _composerRenderPicker(trigger) {
  const picker = _composerPickerElement();
  if (!picker || !trigger) { _composerClosePicker(); return; }
  const items = (trigger.kind === 'slash'
    ? _composerSlashChoices(trigger.query)
    : _composerMentionChoices(trigger.query)).slice(0, 14);
  _composerPickerState = {
    kind: trigger.kind,
    start: trigger.start,
    end: trigger.end,
    selected: Math.min(_composerPickerState.selected, Math.max(0, items.length - 1)),
    items,
  };
  const title = trigger.kind === 'slash' ? t('availableCommands') : t('agents');
  const empty = trigger.kind === 'slash' ? t('noMatchingCommands') : t('noAgents');
  picker.innerHTML = '<div class="composer-picker-head"><span>' + escapeHtml(title)
    + '</span><kbd>\u2191\u2193 \u00b7 Enter \u00b7 Esc</kbd></div>'
    + (items.length ? items.map((item, index) => (
      '<div class="composer-picker-item' + (index === _composerPickerState.selected ? ' selected' : '')
      + '" id="composerPickerOption' + index + '" role="option" aria-selected="'
      + (index === _composerPickerState.selected ? 'true' : 'false') + '" data-index="' + index + '">'
      + '<span class="composer-picker-value">' + escapeHtml(item.label) + '</span>'
      + '<span class="composer-picker-description">' + escapeHtml(item.description) + '</span></div>'
    )).join('') : '<div class="composer-picker-empty">' + escapeHtml(empty) + '</div>');
  picker.querySelectorAll('.composer-picker-item').forEach(row => {
    row.addEventListener('mousedown', event => {
      event.preventDefault();
      _composerChoose(Number(row.dataset.index));
    });
  });
  picker.hidden = false;
  _composerSetExpanded(trigger.kind, true);
  const input = _composerInput();
  if (input && items.length) input.setAttribute(
    'aria-activedescendant', 'composerPickerOption' + _composerPickerState.selected);
}

function _composerUpdatePicker() {
  const input = _composerInput();
  const trigger = _composerTriggerAtCaret(input);
  if (!trigger) { _composerClosePicker(); return; }
  _composerRenderPicker(trigger);
}

function _composerInsertTrigger(kind) {
  const input = _composerInput();
  if (!input) return;
  const current = _composerTriggerAtCaret(input);
  if (current && current.kind === kind) {
    _composerRenderPicker(current);
    input.focus();
    return;
  }
  const caret = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length;
  const end = Number.isInteger(input.selectionEnd) ? input.selectionEnd : caret;
  const spacer = caret > 0 && !/\s/.test(input.value.charAt(caret - 1)) ? ' ' : '';
  input.setRangeText(spacer + (kind === 'slash' ? '/' : '@'), caret, end, 'end');
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  _composerUpdatePicker();
}

function openComposerPicker(kind) {
  if (kind !== 'slash' && kind !== 'mention') return;
  const picker = _composerPickerElement();
  if (picker && !picker.hidden && _composerPickerState.kind === kind) {
    _composerClosePicker();
    _composerInput()?.focus();
    return;
  }
  _composerInsertTrigger(kind);
}

function _composerMentionToken(value) {
  const clean = String(value || '').replace(/"/g, '').trim();
  return /\s/.test(clean) ? '@"' + clean + '"' : '@' + clean;
}

function _composerChoose(index) {
  const input = _composerInput();
  const item = _composerPickerState.items[index];
  if (!input || !item) return;
  const replacement = (_composerPickerState.kind === 'slash'
    ? item.value : _composerMentionToken(item.value)) + ' ';
  input.setRangeText(replacement, _composerPickerState.start, _composerPickerState.end, 'end');
  _composerClosePicker();
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
}

function _composerMoveSelection(delta) {
  const count = _composerPickerState.items.length;
  if (!count) return;
  _composerPickerState.selected = (_composerPickerState.selected + delta + count) % count;
  const input = _composerInput();
  const trigger = _composerTriggerAtCaret(input);
  if (trigger) _composerRenderPicker(trigger);
}

function _composerPickerKeydown(event) {
  const picker = _composerPickerElement();
  if (!picker || picker.hidden) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    event.stopImmediatePropagation();
    _composerMoveSelection(event.key === 'ArrowDown' ? 1 : -1);
  } else if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
    if (!_composerPickerState.items.length) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    _composerChoose(_composerPickerState.selected);
  } else if (event.key === 'Escape') {
    event.preventDefault();
    event.stopImmediatePropagation();
    _composerClosePicker();
  }
}

function initComposerPicker() {
  const input = _composerInput();
  if (!input) return;
  input.addEventListener('input', _composerUpdatePicker);
  input.addEventListener('keydown', _composerPickerKeydown, true);
  _composerSetActionsOpen(false);
  updateComposerAgentBadge();
  document.addEventListener('mousedown', event => {
    const picker = _composerPickerElement();
    if (!picker || picker.hidden || picker.contains(event.target)) return;
    if (event.target === document.getElementById('composerSlashBtn')
        || event.target === document.getElementById('composerMentionBtn')) return;
    _composerClosePicker();
  });
  document.addEventListener('mousedown', event => {
    const overlay = document.getElementById('composerAgentOverlay');
    const picker = _composerAgentPickerElement();
    if (!overlay || !picker || picker.hidden || overlay.contains(event.target)) return;
    _composerCloseAgentPicker();
  });
  document.addEventListener('click', event => {
    const panel = document.getElementById('composerMobileActions');
    const toggle = document.getElementById('composerMobileActionsBtn');
    if (!panel || panel.dataset.open !== 'true') return;
    if (event.target === toggle || toggle.contains(event.target)) return;
    if (panel.contains(event.target)) {
      if (event.target.closest && event.target.closest('button')) _composerSetActionsOpen(false);
      return;
    }
    _composerSetActionsOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      _composerSetActionsOpen(false);
      _composerCloseAgentPicker();
    }
  });
  if (window.matchMedia) {
    const media = window.matchMedia('(max-width: 768px)');
    const sync = () => _composerSetActionsOpen(false);
    if (media.addEventListener) media.addEventListener('change', sync);
    else if (media.addListener) media.addListener(sync);
  }
}

document.addEventListener('DOMContentLoaded', initComposerPicker);
