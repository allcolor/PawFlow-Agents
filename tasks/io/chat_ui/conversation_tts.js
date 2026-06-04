// Live conversation TTS. This is intentionally UI-local: it listens to the
// existing SSE message stream and calls a silent UI action that returns audio.

var _convTtsEnabled = false;
var _convTtsEnabledAt = 0;
var _convTtsRunId = 0;
var _convTtsQueue = [];
var _convTtsSynthInFlight = 0;
var _convTtsNextAudioSeq = 1;
var _convTtsPlayAudioSeq = 1;
var _convTtsPendingAudio = {};
var _convTtsPlaying = false;
var _convTtsCurrentAudio = null;
var _convTtsCurrentAudioFileId = '';
var _convTtsBuffers = {};
var _convTtsRecentText = '';
var _convTtsSpokenMessages = new Set();
var _convTtsStreamedMessages = new Set();
var _convTtsServices = [];
var _convTtsSelectedService = '';
var _convTtsServicesLoaded = false;
var _convTtsRefreshInFlight = false;
var _convTtsStartAfterRefresh = false;
var _convTtsAfterRefresh = null;
var _convTtsAfterServiceSelect = null;
var _convTtsOneShotRunId = 0;
var _convTtsOneShotAudio = null;
var _convTtsOneShotFileId = '';
var _convTtsOneShotInFlight = 0;
var _convTtsOneShotQueue = [];
var _convTtsOneShotPendingAudio = {};
var _convTtsOneShotNextSeq = 1;
var _convTtsOneShotPlaySeq = 1;
var _convTtsOneShotPlaying = false;

function _convTtsPrepareAudio(item) {
  if (!item || !item.url || item.audio) return item;
  const audio = new Audio(item.url);
  audio.preload = 'auto';
  item.audio = audio;
  try { audio.load(); } catch (_err) {}
  return item;
}

function _convTtsConfig() {
  const cfg = { service: _convTtsSelectedService || '', voice: '', language: '' };
  try {
    cfg.voice = localStorage.getItem('pawflow_tts_voice') || '';
    cfg.language = localStorage.getItem('pawflow_tts_language') || '';
  } catch (_err) {}
  return cfg;
}

function _convTtsReportError(message) {
  const text = message || 'Speech synthesis failed';
  console.warn('[conversation-tts] ' + text);
  if (typeof addMsg === 'function') addMsg('error', text);
}

function _convTtsUpdateButton() {
  const btn = document.getElementById('speakToggleBtn');
  if (!btn) return;
  btn.classList.toggle('active', _convTtsEnabled);
  btn.style.display = _convTtsServices.length ? 'inline-flex' : 'none';
  btn.setAttribute('aria-pressed', _convTtsEnabled ? 'true' : 'false');
  btn.title = _convTtsEnabled
    ? (typeof t === 'function' ? t('liveSpeechStopTitle') : 'Stop live speech')
    : (typeof t === 'function' ? t('liveSpeechStartTitle') : 'Speak agent messages live');
  btn.innerHTML = _convTtsEnabled ? '&#x1F50A;' : '&#x1F507;';
}

function _convTtsSetServices(services) {
  _convTtsServices = Array.isArray(services) ? services : [];
  _convTtsServicesLoaded = true;
  if (!_convTtsServices.length) {
    _convTtsSelectedService = '';
    if (_convTtsEnabled) toggleConversationTTS();
  } else if (!_convTtsSelectedService || !_convTtsServices.some(s => s.id === _convTtsSelectedService)) {
    let stored = '';
    try { stored = localStorage.getItem('pawflow_tts_service') || ''; } catch (_err) {}
    _convTtsSelectedService = _convTtsServices.some(s => s.id === stored)
      ? stored
      : _convTtsServices[0].id;
  }
  _convTtsUpdateButton();
}

function refreshConversationTTSServices(startAfterRefresh) {
  if (typeof startAfterRefresh === 'function') _convTtsAfterRefresh = startAfterRefresh;
  else if (startAfterRefresh) _convTtsStartAfterRefresh = true;
  if (_convTtsRefreshInFlight) return;
  if (typeof action$ !== 'function') { _convTtsUpdateButton(); return; }
  _convTtsRefreshInFlight = true;
  action$('list_tts_services', {}, { silent: true }).subscribe(data => {
    _convTtsRefreshInFlight = false;
    const services = Array.isArray(data) ? data : ((data && data.services) || []);
    _convTtsSetServices(services);
    const afterRefresh = _convTtsAfterRefresh;
    _convTtsAfterRefresh = null;
    if (afterRefresh) {
      afterRefresh();
    } else if (_convTtsStartAfterRefresh) {
      _convTtsStartAfterRefresh = false;
      _convTtsStartFromAvailableServices();
    }
  });
}

function _convTtsStartFromAvailableServices() {
  _convTtsChooseService(_convTtsStart);
}

function _convTtsSelectService(serviceId) {
  if (!serviceId) return;
  _convTtsSelectedService = serviceId;
  try { localStorage.setItem('pawflow_tts_service', serviceId); } catch (_err) {}
  const overlay = document.getElementById('convTtsServiceDialog');
  if (overlay) overlay.remove();
  const afterSelect = _convTtsAfterServiceSelect;
  _convTtsAfterServiceSelect = null;
  if (afterSelect) afterSelect();
  else _convTtsStart();
}

function _convTtsChooseService(afterSelect) {
  if (!_convTtsServicesLoaded) {
    refreshConversationTTSServices(() => _convTtsChooseService(afterSelect));
    return;
  }
  if (!_convTtsServices.length) {
    _convTtsUpdateButton();
    return;
  }
  _convTtsAfterServiceSelect = afterSelect;
  if (_convTtsServices.length > 1) _convTtsShowServiceDialog();
  else _convTtsSelectService(_convTtsServices[0].id);
}

function _convTtsShowServiceDialog() {
  const old = document.getElementById('convTtsServiceDialog');
  if (old) old.remove();
  const overlay = document.createElement('div');
  overlay.id = 'convTtsServiceDialog';
  overlay.className = 'dialog-bg';
  let html = '<div class="exec-dialog" style="min-width:320px;max-width:460px;">'
    + '<h3>' + escapeHtml(typeof t === 'function' ? t('liveSpeechChooseService') : 'Choose speech service') + '</h3>';
  _convTtsServices.forEach(s => {
    const label = s.id + (s.type ? ' (' + s.type + ')' : '');
    html += '<button class="btn" style="display:block;width:100%;margin:6px 0;text-align:left;" '
      + 'onclick="_convTtsSelectService(this.dataset.service)" data-service="' + escapeHtml(s.id) + '">'
      + escapeHtml(label) + '</button>';
  });
  html += '<div class="dialog-actions" style="margin-top:12px;"><button class="btn" onclick="document.getElementById(\'convTtsServiceDialog\').remove()">'
    + escapeHtml(typeof t === 'function' ? t('cancel') : 'Cancel') + '</button></div></div>';
  overlay.innerHTML = html;

  document.body.appendChild(overlay);
}

function _convTtsStart() {
  _convTtsEnabled = true;
  _convTtsRunId += 1;
  _convTtsEnabledAt = Date.now();
  _convTtsQueue = [];
  _convTtsSynthInFlight = 0;
  _convTtsNextAudioSeq = 1;
  _convTtsPlayAudioSeq = 1;
  _convTtsPendingAudio = {};
  _convTtsBuffers = {};
  _convTtsRecentText = '';
  _convTtsSpokenMessages.clear();
  _convTtsStreamedMessages.clear();
  document.querySelectorAll('#messages [data-msgid]').forEach(el => {
    if (el.dataset && el.dataset.msgid) _convTtsSpokenMessages.add(el.dataset.msgid);
  });
  _convTtsUpdateButton();
  _convTtsWarmup();
}

function _convTtsWarmup() {
  if (typeof action$ !== 'function') return;
  const cfg = _convTtsConfig();
  action$('tts_warmup', {
    conversation_id: conversationId,
    service: cfg.service,
    voice: cfg.voice,
    language: cfg.language,
  }, { silent: true }).subscribe(result => {
    if (result && result.error) console.warn('[conversation-tts] warmup failed', result.error);
  }, err => {
    console.warn('[conversation-tts] warmup request failed', err);
  });
}

function _convTtsStop() {
  _convTtsEnabled = false;
  _convTtsRunId += 1;
  _convTtsQueue = [];
  _convTtsSynthInFlight = 0;
  _convTtsNextAudioSeq = 1;
  _convTtsPlayAudioSeq = 1;
  _convTtsBuffers = {};
  _convTtsRecentText = '';
  _convTtsStreamedMessages.clear();
  if (_convTtsCurrentAudio) {
    try { _convTtsCurrentAudio.pause(); } catch (_err) {}
    _convTtsCurrentAudio = null;
  }
  Object.keys(_convTtsPendingAudio).forEach(key => {
    _convTtsDeleteFile(_convTtsPendingAudio[key] && _convTtsPendingAudio[key].file_id);
  });
  _convTtsPendingAudio = {};
  _convTtsDeleteFile(_convTtsCurrentAudioFileId);
  _convTtsCurrentAudioFileId = '';
  _convTtsPlaying = false;
  _convTtsUpdateButton();
}

function _convTtsDeleteFile(fileId) {
  if (!fileId || typeof action$ !== 'function') return;
  action$('tts_delete', {
    conversation_id: conversationId,
    file_id: fileId,
  }, { silent: true }).subscribe(function() {}, function(err) {
    console.warn('[conversation-tts] cleanup failed', err);
  });
}

function toggleConversationTTS() {
  if (_convTtsEnabled) {
    _convTtsStop();
    return;
  }
  refreshConversationTTSServices(true);
}

function _convTtsEventIsNew(data) {
  const ts = data && (data.ts || data.timestamp || data.created_at || data.updated_at);
  if (!ts) return true;
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return true;
  const ms = n < 100000000000 ? n * 1000 : n;
  return ms >= (_convTtsEnabledAt - 1000);
}

function _convTtsCleanText(text) {
  return String(text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/[*_>#\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function _convTtsNormText(text) {
  return _convTtsCleanText(text).toLowerCase();
}

function _convTtsIsAgentMessage(data) {
  if (!_convTtsEnabled || !data) return false;
  const src = data.source || {};
  if (data.role && data.role !== 'assistant') return false;
  if (src.type && ['agent_delegate', 'tool', 'tool_call', 'tool_result', 'system', 'user'].includes(src.type)) return false;
  if (!_convTtsEventIsNew(data)) return false;
  return true;
}

function _convTtsFlushReady(key, force) {
  const text = _convTtsBuffers[key] || '';
  const clean = _convTtsCleanText(text);
  if (!clean) { _convTtsBuffers[key] = ''; return; }
  let cut = -1;
  const m = clean.match(/[.!?]+(\s|$)/);
  if (m && m.index !== undefined) cut = m.index + m[0].trimEnd().length;
  if (!force && cut < 0) {
    if (clean.length < 90) return;
    cut = clean.lastIndexOf(' ', 120);
    if (cut < 60) cut = Math.min(clean.length, 120);
  }
  const segment = force ? clean : clean.slice(0, cut).trim();
  const rest = force ? '' : clean.slice(cut).trim();
  _convTtsBuffers[key] = rest;
  if (segment) _convTtsEnqueue(segment, { keepAll: force });
}

function _convTtsSplitSegments(text) {
  let remaining = _convTtsCleanText(text);
  const segments = [];
  while (remaining) {
    if (remaining.length <= 180) {
      segments.push(remaining);
      break;
    }
    let cut = -1;
    const sentence = remaining.slice(0, 220).match(/[.!?]+(\s|$)/);
    if (sentence && sentence.index !== undefined) {
      cut = sentence.index + sentence[0].trimEnd().length;
    }
    if (cut < 60) cut = remaining.lastIndexOf(' ', 180);
    if (cut < 90) cut = remaining.lastIndexOf(' ', 140);
    if (cut < 60) cut = Math.min(remaining.length, 180);
    segments.push(remaining.slice(0, cut).trim());
    remaining = remaining.slice(cut).trim();
  }
  return segments.filter(Boolean);
}

function _convTtsTrimRecentOverlap(segment) {
  let clean = _convTtsCleanText(segment);
  if (!clean) return '';
  const recent = _convTtsRecentText;
  const candidate = _convTtsNormText(clean);
  if (!recent || !candidate) return clean;
  if (candidate.length < 80 && recent.endsWith(candidate)) return '';
  const max = Math.min(80, recent.length, candidate.length);
  for (let len = max; len >= 12; len--) {
    if (recent.slice(-len) === candidate.slice(0, len)) {
      clean = clean.slice(len).trim();
      break;
    }
  }
  return clean;
}

function _convTtsRememberSegment(segment) {
  const norm = _convTtsNormText(segment);
  if (!norm) return;
  _convTtsRecentText = (_convTtsRecentText + ' ' + norm).slice(-500).trim();
}

function _convTtsEnqueue(text, options) {
  const clean = _convTtsCleanText(text);
  if (!clean) return;
  const opts = options || {};
  const segments = _convTtsSplitSegments(clean);
  segments.forEach(segment => {
    const trimmed = _convTtsTrimRecentOverlap(segment);
    if (!trimmed) return;
    _convTtsQueue.push(trimmed);
    _convTtsRememberSegment(trimmed);
  });
  const maxQueued = opts.keepAll ? 0 : 12;
  if (maxQueued && _convTtsQueue.length > maxQueued) {
    _convTtsQueue.splice(0, _convTtsQueue.length - Math.max(3, Math.floor(maxQueued / 2)));
  }
  _convTtsPump();
}

function _convTtsPump() {
  if (!_convTtsEnabled) return;
  while (_convTtsSynthInFlight < 3 && _convTtsQueue.length) {
    _convTtsSynthesizeNext();
  }
}

function _convTtsSynthesizeNext() {
  const text = _convTtsQueue.shift();
  const seq = _convTtsNextAudioSeq++;
  const runId = _convTtsRunId;
  const cfg = _convTtsConfig();
  let settled = false;
  _convTtsSynthInFlight += 1;
  const timeout = window.setTimeout(() => {
    if (settled || runId !== _convTtsRunId) return;
    settled = true;
    _convTtsSynthInFlight = Math.max(0, _convTtsSynthInFlight - 1);
    console.warn('[conversation-tts] synth timed out, skipping segment');
    _convTtsPendingAudio[seq] = '';
    _convTtsPlayNext();
    _convTtsPump();
  }, 12000);
  action$('tts_synthesize', {
    conversation_id: conversationId,
    text: text,
    service: cfg.service,
    voice: cfg.voice,
    language: cfg.language,
    transient: true,
    transient_ttl: 300,
  }, { silent: true }).subscribe(result => {
    if (settled || runId !== _convTtsRunId) {
      _convTtsDeleteFile(result && result.file_id);
      return;
    }
    settled = true;
    clearTimeout(timeout);
    _convTtsSynthInFlight = Math.max(0, _convTtsSynthInFlight - 1);
    if (!_convTtsEnabled) return;
    if (!result || result.error || !result.url) {
      _convTtsReportError(result && result.error ? result.error : 'Speech synthesis returned no audio');
      _convTtsPendingAudio[seq] = '';
      _convTtsPlayNext();
      _convTtsPump();
      return;
    }
    _convTtsPendingAudio[seq] = _convTtsPrepareAudio({
      url: result.url,
      file_id: result.file_id || '',
    });
    _convTtsPlayNext();
    _convTtsPump();
  }, err => {
    if (settled || runId !== _convTtsRunId) return;
    settled = true;
    clearTimeout(timeout);
    _convTtsSynthInFlight = Math.max(0, _convTtsSynthInFlight - 1);
    _convTtsReportError('Speech synthesis request failed: ' + (err && err.message ? err.message : err));
    _convTtsPendingAudio[seq] = '';
    _convTtsPlayNext();
    _convTtsPump();
  });
}

function _convTtsPlayNext() {
  if (!_convTtsEnabled || _convTtsPlaying) return;
  while (Object.prototype.hasOwnProperty.call(_convTtsPendingAudio, _convTtsPlayAudioSeq)) {
    const item = _convTtsPendingAudio[_convTtsPlayAudioSeq];
    delete _convTtsPendingAudio[_convTtsPlayAudioSeq++];
    if (item && item.url) {
      _convTtsPlayUrl(item);
      return;
    }
  }
}

function _convTtsPlayUrl(item) {
  _convTtsPrepareAudio(item);
  const audio = item.audio;
  let cleaned = false;
  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    _convTtsDeleteFile(item.file_id);
  }
  _convTtsCurrentAudio = audio;
  _convTtsCurrentAudioFileId = item.file_id || '';
  _convTtsPlaying = true;
  audio.onended = audio.onerror = function() {
    cleanup();
    _convTtsPlaying = false;
    if (_convTtsCurrentAudio === audio) {
      _convTtsCurrentAudio = null;
      _convTtsCurrentAudioFileId = '';
    }
    _convTtsPlayNext();
  };
  audio.play().catch(err => {
    console.warn('[conversation-tts] play failed', err);
    cleanup();
    _convTtsPlaying = false;
    if (_convTtsCurrentAudio === audio) {
      _convTtsCurrentAudio = null;
      _convTtsCurrentAudioFileId = '';
    }
    _convTtsPlayNext();
  });
}

function _convTtsStopOneShot() {
  _convTtsOneShotRunId += 1;
  if (_convTtsOneShotAudio) {
    try { _convTtsOneShotAudio.pause(); } catch (_err) {}
    _convTtsOneShotAudio = null;
  }
  Object.keys(_convTtsOneShotPendingAudio).forEach(key => {
    _convTtsDeleteFile(_convTtsOneShotPendingAudio[key] && _convTtsOneShotPendingAudio[key].file_id);
  });
  _convTtsOneShotQueue = [];
  _convTtsOneShotPendingAudio = {};
  _convTtsOneShotInFlight = 0;
  _convTtsOneShotNextSeq = 1;
  _convTtsOneShotPlaySeq = 1;
  _convTtsOneShotPlaying = false;
  _convTtsDeleteFile(_convTtsOneShotFileId);
  _convTtsOneShotFileId = '';
}

function conversationTTSSpeakText(text) {
  const clean = _convTtsCleanText(text);
  if (!clean) return;
  _convTtsStopOneShot();
  const runId = _convTtsOneShotRunId;
  _convTtsChooseService(() => {
    _convTtsSpeakSegmentsOnce(_convTtsSplitSegments(clean), runId);
  });
}

function _convTtsSpeakSegmentsOnce(segments, runId) {
  if (runId !== _convTtsOneShotRunId || !segments.length) return;
  _convTtsOneShotQueue = segments.slice();
  _convTtsOneShotPendingAudio = {};
  _convTtsOneShotInFlight = 0;
  _convTtsOneShotNextSeq = 1;
  _convTtsOneShotPlaySeq = 1;
  _convTtsOneShotPlaying = false;
  _convTtsPumpOneShot(runId);
}

function _convTtsPumpOneShot(runId) {
  if (runId !== _convTtsOneShotRunId) return;
  while (_convTtsOneShotInFlight < 3 && _convTtsOneShotQueue.length) {
    _convTtsSynthesizeOneShotNext(runId);
  }
}

function _convTtsSynthesizeOneShotNext(runId) {
  const text = _convTtsOneShotQueue.shift();
  const seq = _convTtsOneShotNextSeq++;
  const cfg = _convTtsConfig();
  _convTtsOneShotInFlight += 1;
  action$('tts_synthesize', {
    conversation_id: conversationId,
    text: text,
    service: cfg.service,
    voice: cfg.voice,
    language: cfg.language,
    transient: true,
    transient_ttl: 300,
  }, { silent: true }).subscribe(result => {
    _convTtsOneShotInFlight = Math.max(0, _convTtsOneShotInFlight - 1);
    if (runId !== _convTtsOneShotRunId) {
      _convTtsDeleteFile(result && result.file_id);
      return;
    }
    if (!result || result.error || !result.url) {
      _convTtsReportError(result && result.error ? result.error : 'Speech synthesis returned no audio');
      _convTtsOneShotPendingAudio[seq] = '';
      _convTtsPlayOneShotNext(runId);
      _convTtsPumpOneShot(runId);
      return;
    }
    _convTtsOneShotPendingAudio[seq] = _convTtsPrepareAudio({
      url: result.url,
      file_id: result.file_id || '',
    });
    _convTtsPlayOneShotNext(runId);
    _convTtsPumpOneShot(runId);
  }, err => {
    _convTtsOneShotInFlight = Math.max(0, _convTtsOneShotInFlight - 1);
    _convTtsReportError('Speech synthesis request failed: ' + (err && err.message ? err.message : err));
    _convTtsOneShotPendingAudio[seq] = '';
    _convTtsPlayOneShotNext(runId);
    _convTtsPumpOneShot(runId);
  });
}

function _convTtsPlayOneShotNext(runId) {
  if (runId !== _convTtsOneShotRunId || _convTtsOneShotPlaying) return;
  while (Object.prototype.hasOwnProperty.call(_convTtsOneShotPendingAudio, _convTtsOneShotPlaySeq)) {
    const item = _convTtsOneShotPendingAudio[_convTtsOneShotPlaySeq];
    delete _convTtsOneShotPendingAudio[_convTtsOneShotPlaySeq++];
    if (item && item.url) {
      _convTtsPlayOneShot(item, runId, () => _convTtsPlayOneShotNext(runId));
      return;
    }
  }
}

function _convTtsPlayOneShot(item, runId, done) {
  if (runId !== _convTtsOneShotRunId) {
    _convTtsDeleteFile(item.file_id);
    return;
  }
  _convTtsPrepareAudio(item);
  const audio = item.audio;
  let cleaned = false;
  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    _convTtsDeleteFile(item.file_id);
  }
  _convTtsOneShotAudio = audio;
  _convTtsOneShotFileId = item.file_id || '';
  _convTtsOneShotPlaying = true;
  audio.onended = audio.onerror = function() {
    cleanup();
    _convTtsOneShotPlaying = false;
    if (_convTtsOneShotAudio === audio) {
      _convTtsOneShotAudio = null;
      _convTtsOneShotFileId = '';
    }
    if (runId === _convTtsOneShotRunId && typeof done === 'function') done();
  };
  audio.play().catch(err => {
    console.warn('[conversation-tts] message play failed', err);
    cleanup();
    if (_convTtsOneShotAudio === audio) {
      _convTtsOneShotAudio = null;
      _convTtsOneShotFileId = '';
    }
  });
}

function conversationTTSOnToken(data) {
  if (!_convTtsIsAgentMessage(data)) return;
  const key = data.msg_id || ('agent:' + (data.agent_name || 'assistant'));
  _convTtsBuffers[key] = (_convTtsBuffers[key] || '') + (data.text || '');
  if (data.msg_id) _convTtsStreamedMessages.add(data.msg_id);
  _convTtsFlushReady(key, false);
}

function conversationTTSOnMessage(data) {
  if (!_convTtsIsAgentMessage(data)) return;
  const id = data.msg_id || '';
  if (id && _convTtsSpokenMessages.has(id)) return;
  if (id && _convTtsStreamedMessages.has(id)) {
    const key = id;
    if (_convTtsBuffers[key]) _convTtsFlushReady(key, true);
    _convTtsSpokenMessages.add(id);
    return;
  }
  if (id) _convTtsSpokenMessages.add(id);
  _convTtsEnqueue(data.content || data.response || '', { keepAll: true });
}

function conversationTTSOnDone(data) {
  if (!_convTtsEnabled || !data) return;
  const key = data.msg_id || ('agent:' + (data.agent_name || (data.source && data.source.name) || 'assistant'));
  if (_convTtsBuffers[key]) {
    _convTtsFlushReady(key, true);
    if (data.msg_id) _convTtsSpokenMessages.add(data.msg_id);
    return;
  }
  if (!_convTtsIsAgentMessage(data)) return;
  const id = data.msg_id || '';
  if (id && (_convTtsSpokenMessages.has(id) || _convTtsStreamedMessages.has(id))) return;
  if (id) _convTtsSpokenMessages.add(id);
  _convTtsEnqueue(data.response || data.content || '', { keepAll: true });
}

document.addEventListener('DOMContentLoaded', function() {
  _convTtsUpdateButton();
  refreshConversationTTSServices();
});

document.addEventListener('visibilitychange', function() {
  if (!document.hidden && !_convTtsServices.length) refreshConversationTTSServices();
});
