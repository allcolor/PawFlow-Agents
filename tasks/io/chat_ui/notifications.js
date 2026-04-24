// Proactive-notification helpers for PushNotification MCP events.
//
// Pure client-side — no asset downloads, bell is synthesized via the
// Web Audio API (two decaying sine tones for a clean bell timbre).
// Toast + tab-title flash + browser-native Notification API.
//
// Mute state persists in localStorage key `pawflow.notif.muted`.
// Requesting browser notification permission is one-shot per user
// (triggered by the settings button).

var _pfNotifAudioCtx = null;
var _pfNotifToastEl = null;
var _pfNotifTabFlashInterval = null;
var _pfNotifOriginalTitle = null;

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

// Synthesize a short bell sound. Two layered sine tones with exponential
// decay — gives a pleasant ding without shipping any audio asset.
function playNotificationBell() {
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  if (!_pfNotifAudioCtx) _pfNotifAudioCtx = new AC();
  var ctx = _pfNotifAudioCtx;
  // Autoplay policy: some browsers suspend context until user gesture.
  if (ctx.state === 'suspended') { try { ctx.resume(); } catch (_e) {} }
  var now = ctx.currentTime;
  var master = ctx.createGain();
  master.gain.value = 0.25;
  master.connect(ctx.destination);
  // Two sine tones (root + fifth) decaying over ~0.6s
  [880, 1320].forEach(function (freq, i) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    var start = now + (i * 0.05);
    gain.gain.setValueAtTime(0.0, start);
    gain.gain.linearRampToValueAtTime(0.6, start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.001, start + 0.6);
    osc.connect(gain).connect(master);
    osc.start(start);
    osc.stop(start + 0.65);
  });
}

function showNotificationToast(fromAgent, message) {
  // Singleton toast container — messages stack vertically, auto-expire.
  if (!_pfNotifToastEl) {
    _pfNotifToastEl = document.createElement('div');
    _pfNotifToastEl.id = 'pf-notif-stack';
    _pfNotifToastEl.style.cssText = (
      'position:fixed;top:16px;right:16px;z-index:10000;'
      + 'display:flex;flex-direction:column;gap:8px;'
      + 'max-width:380px;pointer-events:none;'
    );
    document.body.appendChild(_pfNotifToastEl);
  }
  var toast = document.createElement('div');
  toast.className = 'pf-notif-toast';
  toast.style.cssText = (
    'background:#1a3a2a;color:#4ecdc4;padding:10px 14px;'
    + 'border-radius:6px;border-left:3px solid #4ecdc4;'
    + 'box-shadow:0 4px 12px rgba(0,0,0,0.4);'
    + 'font-size:13px;line-height:1.4;pointer-events:auto;cursor:pointer;'
    + 'transition:opacity 0.3s,transform 0.3s;opacity:0;transform:translateX(20px);'
  );
  toast.innerHTML = (
    '<div style="font-weight:600;margin-bottom:2px;">🔔 '
    + escapeHtml(fromAgent) + '</div>'
    + '<div>' + escapeHtml(message) + '</div>'
  );
  toast.onclick = function () { toast.remove(); window.focus(); };
  _pfNotifToastEl.appendChild(toast);
  // Fade in on next frame, auto-dismiss after 8s.
  requestAnimationFrame(function () {
    toast.style.opacity = '1';
    toast.style.transform = 'translateX(0)';
  });
  setTimeout(function () {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(20px)';
    setTimeout(function () { toast.remove(); }, 350);
  }, 8000);
}

function flashTabTitle(tempTitle) {
  if (!_pfNotifOriginalTitle) _pfNotifOriginalTitle = document.title;
  if (_pfNotifTabFlashInterval) clearInterval(_pfNotifTabFlashInterval);
  var flipped = false;
  _pfNotifTabFlashInterval = setInterval(function () {
    document.title = flipped ? _pfNotifOriginalTitle : tempTitle;
    flipped = !flipped;
  }, 1000);
  // Stop flashing and restore on tab focus or after 30s, whichever comes first.
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

// Toggle exposed for the settings UI (cmd_misc.js or settings panel).
function toggleNotificationMute() {
  var nowMuted = !isNotificationsMuted();
  setNotificationsMuted(nowMuted);
  return nowMuted;
}

// Test hook — lets the user preview the bell + toast without waiting
// for an agent notification.
function testNotification() {
  playNotificationBell();
  showNotificationToast('system', 'Notification test — bell + toast working.');
}

// escapeHtml is already defined in messages.js (loaded earlier).
