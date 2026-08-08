// Runtime-only notification center.
//
// Notifications live for the lifetime of this browser tab. They are never
// written to the conversation, localStorage, IndexedDB, or the server. The
// mute preference remains localStorage-backed because it is a user setting,
// not notification history.

var _pfNotifAudioCtx = null;
var _pfNotifToastEl = null;
var _pfNotifTabFlashInterval = null;
var _pfNotifOriginalTitle = null;
var _pfNotifications = [];
var _pfNotificationSequence = 0;
var _pfNotificationUnread = 0;
var _pfNotificationDialogOpen = false;
var _pfNotificationToastTimers = {};

function isNotificationsMuted() {
  try { return localStorage.getItem('pawflow.notif.muted') === '1'; }
  catch (_err) { return false; }
}

function setNotificationsMuted(muted) {
  try {
    if (muted) localStorage.setItem('pawflow.notif.muted', '1');
    else localStorage.removeItem('pawflow.notif.muted');
  } catch (_err) { /* storage disabled */ }
}

function requestNotificationPermission() {
  if (typeof Notification === 'undefined') return Promise.resolve('unsupported');
  if (Notification.permission === 'granted') return Promise.resolve('granted');
  if (Notification.permission === 'denied') return Promise.resolve('denied');
  return Notification.requestPermission();
}

function playNotificationBell() {
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  if (!_pfNotifAudioCtx) _pfNotifAudioCtx = new AC();
  var ctx = _pfNotifAudioCtx;
  if (ctx.state === 'suspended') {
    try { ctx.resume(); } catch (_err) {}
  }
  var now = ctx.currentTime;
  var master = ctx.createGain();
  master.gain.value = 0.25;
  master.connect(ctx.destination);
  [880, 1320].forEach(function (freq, index) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    var start = now + (index * 0.05);
    gain.gain.setValueAtTime(0.0, start);
    gain.gain.linearRampToValueAtTime(0.6, start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.6);
    osc.connect(gain).connect(master);
    osc.start(start);
    osc.stop(start + 0.65);
  });
}

function _pfNotificationPlainText(html) {
  var probe = document.createElement('div');
  probe.innerHTML = String(html || '');
  return (probe.textContent || '').replace(/\s+/g, ' ').trim();
}

function _pfNotificationLevel(value) {
  var level = String(value || 'info').toLowerCase();
  return ['info', 'success', 'warning', 'error', 'progress'].indexOf(level) >= 0
    ? level : 'info';
}

function _pfNotificationDefaultTitle(level) {
  var keys = {
    info: 'notificationInfo',
    success: 'notificationSuccess',
    warning: 'notificationWarning',
    error: 'notificationError',
    progress: 'notificationProgress',
  };
  return t(keys[level] || keys.info);
}

function _pfNotificationTime(value) {
  var numeric = Number(value || 0);
  if (numeric > 0 && numeric < 100000000000) numeric *= 1000;
  var date = numeric > 0 ? new Date(numeric) : new Date();
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function _pfNotificationConversationId(options) {
  if (options && options.conversationId !== undefined) {
    return String(options.conversationId || '');
  }
  return typeof conversationId !== 'undefined' ? String(conversationId || '') : '';
}

function _pfNotificationSource(options) {
  if (!options || !options.source) return '';
  if (typeof options.source === 'string') return options.source;
  return String(options.source.name || options.source.type || '');
}

function _pfUpdateNotificationButton() {
  var badge = document.getElementById('notificationCenterBadge');
  var button = document.getElementById('notificationCenterBtn');
  if (!badge || !button) return;
  badge.textContent = _pfNotificationUnread > 99 ? '99+' : String(_pfNotificationUnread);
  badge.hidden = _pfNotificationUnread < 1;
  button.setAttribute('aria-label', _pfNotificationUnread
    ? t('notificationsUnread', { n: _pfNotificationUnread })
    : t('notifications'));
}

function _pfEnsureToastStack() {
  if (_pfNotifToastEl && _pfNotifToastEl.isConnected) return _pfNotifToastEl;
  _pfNotifToastEl = document.createElement('div');
  _pfNotifToastEl.id = 'pf-notif-stack';
  _pfNotifToastEl.setAttribute('aria-live', 'polite');
  _pfNotifToastEl.setAttribute('aria-relevant', 'additions text');
  document.body.appendChild(_pfNotifToastEl);
  return _pfNotifToastEl;
}

function _pfDismissNotificationToast(id) {
  var state = _pfNotificationToastTimers[id];
  if (state && state.timer) clearTimeout(state.timer);
  delete _pfNotificationToastTimers[id];
  var toasts = document.querySelectorAll('.pf-notif-toast[data-notification-id="' + id + '"]');
  toasts.forEach(function (toast) {
    toast.classList.add('leaving');
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 220);
  });
}

function _pfShowNotificationToast(entry, timeoutMs) {
  _pfDismissNotificationToast(entry.id);
  var stack = _pfEnsureToastStack();
  var toast = document.createElement('div');
  toast.className = 'pf-notif-toast pf-notif-' + entry.level;
  toast.dataset.notificationId = String(entry.id);
  toast.setAttribute('role', entry.level === 'error' ? 'alert' : 'status');

  var content = document.createElement('button');
  content.type = 'button';
  content.className = 'pf-notif-toast-content';
  content.onclick = function () {
    _pfDismissNotificationToast(entry.id);
    openNotificationCenter(entry.id);
  };

  var title = document.createElement('span');
  title.className = 'pf-notif-toast-title';
  title.textContent = entry.title;
  var message = document.createElement('span');
  message.className = 'pf-notif-toast-message';
  message.textContent = entry.message;
  content.appendChild(title);
  content.appendChild(message);

  var dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'pf-notif-toast-dismiss';
  dismiss.setAttribute('aria-label', t('dismissNotification'));
  dismiss.textContent = '\u00d7';
  dismiss.onclick = function (event) {
    event.stopPropagation();
    _pfDismissNotificationToast(entry.id);
  };

  toast.appendChild(content);
  toast.appendChild(dismiss);
  stack.appendChild(toast);
  requestAnimationFrame(function () { toast.classList.add('visible'); });

  if (timeoutMs > 0) {
    var state = {
      remaining: timeoutMs,
      startedAt: Date.now(),
      timer: null,
    };
    var startTimer = function () {
      state.startedAt = Date.now();
      state.timer = setTimeout(function () {
        _pfDismissNotificationToast(entry.id);
      }, state.remaining);
    };
    toast.addEventListener('mouseenter', function () {
      if (!state.timer) return;
      clearTimeout(state.timer);
      state.timer = null;
      state.remaining = Math.max(250, state.remaining - (Date.now() - state.startedAt));
    });
    toast.addEventListener('mouseleave', function () {
      if (!state.timer) startTimer();
    });
    toast.addEventListener('focusin', function () {
      if (!state.timer) return;
      clearTimeout(state.timer);
      state.timer = null;
      state.remaining = Math.max(250, state.remaining - (Date.now() - state.startedAt));
    });
    toast.addEventListener('focusout', function () {
      if (!state.timer) startTimer();
    });
    _pfNotificationToastTimers[entry.id] = state;
    startTimer();
  }
}

function _pfRenderNotificationCenter() {
  var body = document.getElementById('notificationCenterBody');
  var clearButton = document.getElementById('notificationCenterClear');
  if (!body) return;
  body.replaceChildren();
  if (clearButton) clearButton.disabled = _pfNotifications.length === 0;

  if (!_pfNotifications.length) {
    var empty = document.createElement('div');
    empty.className = 'pf-notif-empty';
    empty.textContent = t('notificationCenterEmpty');
    body.appendChild(empty);
    return;
  }

  _pfNotifications.slice().reverse().forEach(function (entry) {
    var row = document.createElement('article');
    row.className = 'pf-notif-history-row pf-notif-' + entry.level;
    row.dataset.notificationId = String(entry.id);

    var header = document.createElement('div');
    header.className = 'pf-notif-history-header';
    var title = document.createElement('strong');
    title.textContent = entry.title;
    var time = document.createElement('time');
    time.dateTime = entry.createdAt.toISOString();
    time.textContent = entry.createdAt.toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
    header.appendChild(title);
    header.appendChild(time);

    var message = document.createElement('div');
    message.className = 'pf-notif-history-message';
    message.textContent = entry.message;
    row.appendChild(header);
    row.appendChild(message);

    var metaParts = [];
    if (entry.agent) metaParts.push(entry.agent);
    else if (entry.source) metaParts.push(entry.source);
    if (entry.conversationId) metaParts.push(entry.conversationId.slice(0, 8));
    if (metaParts.length) {
      var meta = document.createElement('div');
      meta.className = 'pf-notif-history-meta';
      meta.textContent = metaParts.join(' \u00b7 ');
      row.appendChild(meta);
    }

    if (entry.detailHtml || entry.detail) {
      var details = document.createElement('details');
      details.className = 'pf-notif-history-details';
      var summary = document.createElement('summary');
      summary.textContent = t('notificationDetails');
      var detail = document.createElement('div');
      detail.className = 'pf-notif-history-detail';
      if (entry.detailHtml) detail.innerHTML = entry.detailHtml;
      else detail.textContent = entry.detail;
      details.appendChild(summary);
      details.appendChild(detail);
      row.appendChild(details);
    }
    body.appendChild(row);
  });
}

function showUiNotification(text, options) {
  options = options || {};
  var detailHtml = options.detailHtml || '';
  if (options.html && !detailHtml) detailHtml = String(text || '');
  var message = options.html
    ? _pfNotificationPlainText(text)
    : String(text || '').trim();
  if (!message && detailHtml) message = _pfNotificationPlainText(detailHtml);
  if (!message) return null;

  var level = _pfNotificationLevel(options.level);
  var key = String(options.key || '');
  var existing = key
    ? _pfNotifications.find(function (item) { return item.key === key; })
    : null;
  var entry = existing || {
    id: ++_pfNotificationSequence,
    key: key,
    createdAt: _pfNotificationTime(options.ts),
    read: false,
  };

  entry.level = level;
  entry.title = String(options.title || _pfNotificationDefaultTitle(level));
  entry.message = message;
  entry.detail = String(options.detail || '');
  entry.detailHtml = detailHtml;
  entry.source = _pfNotificationSource(options);
  entry.agent = String(options.agent || '');
  entry.conversationId = _pfNotificationConversationId(options);
  entry.updatedAt = _pfNotificationTime(options.ts);

  if (!existing) _pfNotifications.push(entry);
  if (!_pfNotificationDialogOpen && (!existing || entry.read)) {
    entry.read = false;
    _pfNotificationUnread += 1;
  }
  _pfUpdateNotificationButton();
  if (_pfNotificationDialogOpen) _pfRenderNotificationCenter();

  var defaultTimeout = level === 'progress' ? 0 : (level === 'error' ? 9000 : 6500);
  var timeoutMs = options.timeoutMs === undefined
    ? defaultTimeout : Math.max(0, Number(options.timeoutMs) || 0);
  if (options.toast !== false) _pfShowNotificationToast(entry, timeoutMs);
  if (options.openCenter) openNotificationCenter(entry.id);
  return entry;
}

function removeUiNotificationByKey(key) {
  var wanted = String(key || '');
  var removed = _pfNotifications.filter(function (entry) { return entry.key === wanted; });
  if (!removed.length) return;
  removed.forEach(function (entry) {
    if (!entry.read) _pfNotificationUnread = Math.max(0, _pfNotificationUnread - 1);
    _pfDismissNotificationToast(entry.id);
  });
  _pfNotifications = _pfNotifications.filter(function (entry) { return entry.key !== wanted; });
  _pfUpdateNotificationButton();
  _pfRenderNotificationCenter();
}

function clearRuntimeNotifications() {
  _pfNotifications.forEach(function (entry) {
    _pfDismissNotificationToast(entry.id);
  });
  _pfNotifications = [];
  _pfNotificationUnread = 0;
  _pfUpdateNotificationButton();
  _pfRenderNotificationCenter();
}

function closeNotificationCenter() {
  var overlay = document.getElementById('notificationCenterDialog');
  if (overlay) overlay.remove();
  _pfNotificationDialogOpen = false;
}

function openNotificationCenter(focusId) {
  closeNotificationCenter();
  _pfNotificationDialogOpen = true;
  _pfNotifications.forEach(function (entry) { entry.read = true; });
  _pfNotificationUnread = 0;
  _pfUpdateNotificationButton();

  var overlay = document.createElement('div');
  overlay.id = 'notificationCenterDialog';
  overlay.className = 'dialog-bg';
  overlay.onclick = function (event) {
    if (event.target === overlay) closeNotificationCenter();
  };

  var dialog = document.createElement('div');
  dialog.className = 'dialog pf-notif-dialog';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-labelledby', 'notificationCenterTitle');

  var title = document.createElement('div');
  title.id = 'notificationCenterTitle';
  title.className = 'dialog-title';
  title.textContent = t('notifications');

  var body = document.createElement('div');
  body.id = 'notificationCenterBody';
  body.className = 'dialog-body pf-notif-history';

  var actions = document.createElement('div');
  actions.className = 'dialog-actions';
  var clear = document.createElement('button');
  clear.id = 'notificationCenterClear';
  clear.type = 'button';
  clear.className = 'btn';
  clear.textContent = t('clearNotifications');
  clear.onclick = clearRuntimeNotifications;
  var close = document.createElement('button');
  close.type = 'button';
  close.className = 'btn btn-primary';
  close.textContent = t('close');
  close.onclick = closeNotificationCenter;
  actions.appendChild(clear);
  actions.appendChild(close);

  dialog.appendChild(title);
  dialog.appendChild(body);
  dialog.appendChild(actions);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  _pfRenderNotificationCenter();

  if (focusId) {
    var row = body.querySelector('[data-notification-id="' + focusId + '"]');
    if (row) {
      row.classList.add('focused');
      row.scrollIntoView({ block: 'nearest' });
      var details = row.querySelector('details');
      if (details) details.open = true;
    }
  }
  close.focus();
}

function flashTabTitle(tempTitle) {
  if (!_pfNotifOriginalTitle) _pfNotifOriginalTitle = document.title;
  if (_pfNotifTabFlashInterval) clearInterval(_pfNotifTabFlashInterval);
  var flipped = false;
  _pfNotifTabFlashInterval = setInterval(function () {
    document.title = flipped ? _pfNotifOriginalTitle : tempTitle;
    flipped = !flipped;
  }, 1000);
  var stop = function () {
    if (_pfNotifTabFlashInterval) {
      clearInterval(_pfNotifTabFlashInterval);
      _pfNotifTabFlashInterval = null;
    }
    if (_pfNotifOriginalTitle) document.title = _pfNotifOriginalTitle;
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', stop);
  };
  var onVisible = function () { if (!document.hidden) stop(); };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('focus', stop);
  setTimeout(stop, 30000);
}

function handleSseNotification(data) {
  data = data || {};
  var message = String(data.content || data.message || '').trim();
  if (!message) return null;
  var agent = String(data.agent || data.agent_name || '');
  var urgency = String(data.urgency || '').toLowerCase();
  var level = urgency === 'high' ? 'error'
    : (urgency === 'low' ? 'info' : (data.status === 'proactive' ? 'success' : 'warning'));
  var entry = showUiNotification(message, {
    key: data.msg_id ? 'sse:' + data.msg_id : '',
    title: agent ? displayAgentName(agent) : t('notifications'),
    level: level,
    source: data.status === 'proactive' ? 'agent' : 'system',
    agent: agent ? displayAgentName(agent) : '',
    ts: data.ts,
  });

  if (!isNotificationsMuted()) {
    try { playNotificationBell(); } catch (_err) {}
  }
  flashTabTitle('[!] ' + (agent || 'PawFlow') + ': ' + message.slice(0, 40));
  if (document.hidden && typeof Notification !== 'undefined'
      && Notification.permission === 'granted') {
    try {
      var nativeNotification = new Notification(
        agent ? displayAgentName(agent) : 'PawFlow',
        {
          body: message,
          tag: data.msg_id ? 'pawflow-notif-' + data.msg_id : 'pawflow-notif',
          silent: isNotificationsMuted(),
        });
      nativeNotification.onclick = function () {
        window.focus();
        nativeNotification.close();
      };
    } catch (_err) {}
  }
  return entry;
}

// Compatibility entry points used by extensions and the notification settings.
function showNotificationToast(fromAgent, message) {
  return showUiNotification(message, {
    title: fromAgent || t('notifications'),
    agent: fromAgent || '',
    source: fromAgent ? 'agent' : 'system',
  });
}

function showNotification(data) {
  return handleSseNotification(data);
}

function toggleNotificationMute() {
  var nowMuted = !isNotificationsMuted();
  setNotificationsMuted(nowMuted);
  return nowMuted;
}

function testNotification() {
  playNotificationBell();
  showUiNotification(t('notificationTestMessage'), {
    title: t('notifications'),
    level: 'success',
  });
}
