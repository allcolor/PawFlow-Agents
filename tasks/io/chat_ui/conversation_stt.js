// Live conversation STT. Captures microphone audio in the browser, sends it
// to the selected PawFlow STT service, then inserts/sends the transcript.

var _convSttServices = [];
var _convSttSelectedService = '';
var _convSttServicesLoaded = false;
var _convSttRefreshInFlight = false;
var _convSttStartAfterRefresh = false;
var _convSttMediaRecorder = null;
var _convSttStream = null;
var _convSttAudioContext = null;
var _convSttSource = null;
var _convSttProcessor = null;
var _convSttWavChunks = [];
var _convSttWavSampleRate = 0;
var _convSttChunks = [];
var _convSttRecording = false;
var _convSttInputWasEmpty = true;

function _convSttConfig() {
  const cfg = { service: _convSttSelectedService || '', language: '', autoSend: true };
  try {
    cfg.language = localStorage.getItem('pawflow_stt_language') || '';
    cfg.autoSend = localStorage.getItem('pawflow_stt_auto_send') !== 'false';
  } catch (_err) {}
  return cfg;
}

function _convSttUpdateButton() {
  const btn = document.getElementById('speechInputBtn');
  if (!btn) return;
  btn.style.display = _convSttServices.length ? 'inline-flex' : 'none';
  btn.classList.toggle('active', _convSttRecording);
  btn.setAttribute('aria-pressed', _convSttRecording ? 'true' : 'false');
  btn.title = _convSttRecording
    ? (typeof t === 'function' ? t('speechInputStopTitle') : 'Stop recording')
    : (typeof t === 'function' ? t('speechInputStartTitle') : 'Dictate and send');
  btn.innerHTML = _convSttRecording ? '&#x23F9;' : '&#x1F3A4;';
}

function _convSttSetServices(services) {
  _convSttServices = Array.isArray(services) ? services : [];
  _convSttServicesLoaded = true;
  if (!_convSttServices.length) {
    _convSttSelectedService = '';
  } else if (!_convSttSelectedService || !_convSttServices.some(s => s.id === _convSttSelectedService)) {
    let stored = '';
    try { stored = localStorage.getItem('pawflow_stt_service') || ''; } catch (_err) {}
    _convSttSelectedService = _convSttServices.some(s => s.id === stored)
      ? stored
      : _convSttServices[0].id;
  }
  _convSttUpdateButton();
}

function refreshConversationSTTServices(startAfterRefresh) {
  if (startAfterRefresh) _convSttStartAfterRefresh = true;
  if (_convSttRefreshInFlight) return;
  if (typeof action$ !== 'function') { _convSttUpdateButton(); return; }
  _convSttRefreshInFlight = true;
  action$('list_stt_services', {}, { silent: true }).subscribe(data => {
    _convSttRefreshInFlight = false;
    const services = Array.isArray(data) ? data : ((data && data.services) || []);
    _convSttSetServices(services);
    if (_convSttStartAfterRefresh) {
      _convSttStartAfterRefresh = false;
      if (_convSttServices.length > 1 && !_convSttSelectedService) _convSttShowServiceDialog();
      else if (_convSttServices.length) _convSttStartRecording();
    }
  }, _err => {
    _convSttRefreshInFlight = false;
    _convSttSetServices([]);
  });
}

function _convSttSelectService(serviceId) {
  if (!serviceId) return;
  _convSttSelectedService = serviceId;
  try { localStorage.setItem('pawflow_stt_service', serviceId); } catch (_err) {}
  const overlay = document.getElementById('convSttServiceDialog');
  if (overlay) overlay.remove();
  _convSttStartRecording();
}

function _convSttShowServiceDialog() {
  const old = document.getElementById('convSttServiceDialog');
  if (old) old.remove();
  const overlay = document.createElement('div');
  overlay.id = 'convSttServiceDialog';
  overlay.className = 'dialog-bg';
  let html = '<div class="exec-dialog" style="min-width:320px;max-width:460px;">'
    + '<h3>' + escapeHtml(typeof t === 'function' ? t('speechInputChooseService') : 'Choose speech input service') + '</h3>';
  _convSttServices.forEach(s => {
    const label = s.id + (s.type ? ' (' + s.type + ')' : '');
    html += '<button class="btn" style="display:block;width:100%;margin:6px 0;text-align:left;" '
      + 'onclick="_convSttSelectService(this.dataset.service)" data-service="' + escapeHtml(s.id) + '">'
      + escapeHtml(label) + '</button>';
  });
  html += '<div class="dialog-actions" style="margin-top:12px;"><button class="btn" onclick="document.getElementById(\'convSttServiceDialog\').remove()">'
    + escapeHtml(typeof t === 'function' ? t('cancel') : 'Cancel') + '</button></div></div>';
  overlay.innerHTML = html;

  document.body.appendChild(overlay);
}

function toggleConversationSTT() {
  if (_convSttRecording) {
    _convSttStopRecording();
    return;
  }
  if (!_convSttServicesLoaded) {
    refreshConversationSTTServices(true);
    return;
  }
  if (!_convSttServices.length) return;
  if (_convSttServices.length > 1 && !_convSttSelectedService) _convSttShowServiceDialog();
  else _convSttStartRecording();
}

async function _convSttStartRecording() {
  if (_convSttRecording) return;
  if (!navigator.mediaDevices) {
    addMsg('error', 'Browser microphone recording is not available');
    return;
  }
  if (!_convSttSelectedService && _convSttServices.length) _convSttSelectedService = _convSttServices[0].id;
  const input = document.getElementById('input');
  _convSttInputWasEmpty = !input || !String(input.value || '').trim();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    addMsg('error', 'Microphone permission denied: ' + (err && err.message ? err.message : err));
    return;
  }
  if (window.MediaRecorder) {
    _convSttStartMediaRecorder(stream);
    return;
  }
  if (!_convSttStartWavRecorder(stream)) {
    stream.getTracks().forEach(track => track.stop());
    addMsg('error', 'Browser microphone recording is not available');
  }
}

function _convSttStopRecording() {
  if (!_convSttRecording) return;
  if (_convSttProcessor || _convSttAudioContext) {
    _convSttStopWavRecorder();
    return;
  }
  if (_convSttMediaRecorder) {
    try { _convSttMediaRecorder.stop(); } catch (_err) {}
  }
}

function _convSttStartMediaRecorder(stream) {
  _convSttChunks = [];
  _convSttMediaRecorder = new MediaRecorder(stream);
  _convSttMediaRecorder.ondataavailable = function(e) {
    if (e.data && e.data.size) _convSttChunks.push(e.data);
  };
  _convSttMediaRecorder.onstop = function() {
    stream.getTracks().forEach(track => track.stop());
    _convSttRecording = false;
    _convSttUpdateButton();
    _convSttTranscribeBlob(new Blob(_convSttChunks, { type: _convSttMediaRecorder.mimeType || 'audio/webm' }));
  };
  _convSttMediaRecorder.start();
  _convSttRecording = true;
  _convSttUpdateButton();
}

function _convSttStartWavRecorder(stream) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return false;
  _convSttStream = stream;
  _convSttWavChunks = [];
  _convSttAudioContext = new AudioCtx();
  _convSttWavSampleRate = _convSttAudioContext.sampleRate || 48000;
  if (_convSttAudioContext.state === 'suspended' && _convSttAudioContext.resume) {
    _convSttAudioContext.resume().catch(function() {});
  }
  _convSttSource = _convSttAudioContext.createMediaStreamSource(stream);
  _convSttProcessor = _convSttAudioContext.createScriptProcessor(4096, 1, 1);
  _convSttProcessor.onaudioprocess = function(e) {
    if (!_convSttRecording) return;
    const input = e.inputBuffer.getChannelData(0);
    _convSttWavChunks.push(new Float32Array(input));
  };
  _convSttSource.connect(_convSttProcessor);
  _convSttProcessor.connect(_convSttAudioContext.destination);
  _convSttRecording = true;
  _convSttUpdateButton();
  return true;
}

function _convSttStopWavRecorder() {
  const chunks = _convSttWavChunks.slice();
  const sampleRate = _convSttWavSampleRate || 48000;
  if (_convSttProcessor) {
    try { _convSttProcessor.disconnect(); } catch (_err) {}
    _convSttProcessor.onaudioprocess = null;
  }
  if (_convSttSource) {
    try { _convSttSource.disconnect(); } catch (_err) {}
  }
  if (_convSttStream) {
    _convSttStream.getTracks().forEach(track => track.stop());
  }
  if (_convSttAudioContext) {
    try { _convSttAudioContext.close(); } catch (_err) {}
  }
  _convSttStream = null;
  _convSttAudioContext = null;
  _convSttSource = null;
  _convSttProcessor = null;
  _convSttRecording = false;
  _convSttUpdateButton();
  if (!chunks.length) {
    addMsg('error', 'No microphone audio was captured');
    return;
  }
  _convSttTranscribeBlob(_convSttEncodeWav(chunks, sampleRate));
}

function _convSttEncodeWav(chunks, sampleRate) {
  let length = 0;
  chunks.forEach(chunk => { length += chunk.length; });
  const dataSize = length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  function writeString(offset, value) {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  }
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);
  let offset = 44;
  chunks.forEach(chunk => {
    for (let i = 0; i < chunk.length; i++) {
      const sample = Math.max(-1, Math.min(1, chunk[i]));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  });
  return new Blob([buffer], { type: 'audio/wav' });
}

function _convSttBlobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(String(reader.result || '').split(',', 2)[1] || '');
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

async function _convSttTranscribeBlob(blob) {
  if (!blob || !blob.size) {
    addMsg('error', 'No microphone audio was captured');
    return;
  }
  const cfg = _convSttConfig();
  let b64 = '';
  try {
    b64 = await _convSttBlobToBase64(blob);
  } catch (err) {
    addMsg('error', 'Microphone audio encoding failed: ' + (err && err.message ? err.message : err));
    return;
  }
  action$('stt_transcribe', {
    conversation_id: conversationId,
    service: cfg.service,
    audio_b64: b64,
    mime_type: blob.type || 'audio/webm',
    filename: blob.type === 'audio/wav' ? 'speech.wav' : 'speech.webm',
    language: cfg.language,
  }, { silent: true }).subscribe(result => {
    if (!result || result.error) {
      addMsg('error', result && result.error ? result.error : 'Speech transcription failed');
      return;
    }
    const text = String(result.text || '').trim();
    if (!text) {
      addMsg('error', 'Speech transcription returned no text');
      return;
    }
    const input = document.getElementById('input');
    if (!input) return;
    const current = String(input.value || '').trim();
    input.value = current ? (current + '\n' + text) : text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 160) + 'px';
    if (cfg.autoSend && _convSttInputWasEmpty && typeof send === 'function') send();
  }, err => {
    addMsg('error', 'Speech transcription request failed: ' + (err && err.message ? err.message : err));
  });
}

document.addEventListener('DOMContentLoaded', function() {
  _convSttUpdateButton();
  refreshConversationSTTServices();
});

document.addEventListener('visibilitychange', function() {
  if (!document.hidden && !_convSttServices.length) refreshConversationSTTServices();
});

