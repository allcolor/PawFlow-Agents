// Realtime voice mode. Full-duplex conversation with the agent over the
// /ws/realtime/{conversation_id} bridge: continuous mic PCM16 uplink,
// streamed agent PCM16 downlink, live captions from transcript events.
// Final transcripts are persisted server-side and arrive as normal
// messages through SSE — this module only handles audio + live captions.

var _voiceServices = [];
var _voiceSelectedService = '';
var _voiceActive = false;
var _voiceWs = null;
var _voiceStream = null;
var _voiceCaptureCtx = null;
var _voiceCaptureSource = null;
var _voiceCaptureProcessor = null;
var _voicePlayCtx = null;
var _voicePlayTime = 0;
var _voicePlaySources = [];
var _voiceState = 'idle';

var _VOICE_UPLINK_RATE = 16000;   // PCM16 mono sent to the bridge
var _VOICE_DOWNLINK_RATE = 24000; // PCM16 mono received from the bridge

function _voiceConversationId() {
  return (typeof conversationId !== 'undefined' && conversationId) ? conversationId : '';
}

function _voiceT(key, fallback) {
  try { if (typeof t === 'function') { const v = t(key); if (v && v !== key) return v; } } catch (_err) {}
  return fallback;
}

function _voiceUpdateButton() {
  const btn = document.getElementById('voiceModeBtn');
  if (!btn) return;
  btn.style.display = _voiceServices.length ? 'inline-flex' : 'none';
  btn.classList.toggle('active', _voiceActive);
  btn.setAttribute('aria-pressed', _voiceActive ? 'true' : 'false');
  btn.title = _voiceActive
    ? _voiceT('voiceModeStopTitle', 'Stop voice conversation')
    : _voiceT('voiceModeStartTitle', 'Start realtime voice conversation');
  btn.innerHTML = _voiceActive ? '&#x1F50A;' : '&#x1F399;&#xFE0F;';
}

function refreshRealtimeVoiceServices() {
  if (typeof action$ !== 'function') return;
  action$('list_realtime_services', {
    conversation_id: _voiceConversationId(),
  }, { silent: true }).subscribe(list => {
    _voiceServices = Array.isArray(list) ? list : [];
    if (!_voiceSelectedService && _voiceServices.length) {
      _voiceSelectedService = _voiceServices[0].id;
    }
    _voiceUpdateButton();
  }, () => { _voiceServices = []; _voiceUpdateButton(); });
}

// ── captions ─────────────────────────────────────────────────────────

function _voiceCaptions() {
  let el = document.getElementById('voiceCaptions');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'voiceCaptions';
  el.style.cssText = 'position:absolute;left:12px;right:12px;bottom:100%;margin-bottom:6px;'
    + 'background:rgba(20,20,32,.92);border:1px solid var(--pf-border,#333);border-radius:8px;'
    + 'padding:8px 12px;font-size:13px;color:#ddd;max-height:120px;overflow-y:auto;z-index:40;';
  const anchor = document.getElementById('input');
  if (anchor && anchor.parentElement) {
    anchor.parentElement.style.position = 'relative';
    anchor.parentElement.appendChild(el);
  } else {
    document.body.appendChild(el);
  }
  return el;
}

function _voiceCaption(role, text, final_) {
  const box = _voiceCaptions();
  const id = 'voiceCap_' + role;
  let line = document.getElementById(id);
  if (!line) {
    line = document.createElement('div');
    line.id = id;
    box.appendChild(line);
  }
  const label = role === 'user' ? _voiceT('you', 'You') : (typeof displayAgentName === 'function' ? displayAgentName(selectedAgent || 'agent') : 'Agent');
  if (final_) {
    // Final text arrives as a normal message via SSE — clear the live line.
    line.textContent = '';
    line.dataset.acc = '';
    return;
  }
  line.dataset.acc = (line.dataset.acc || '') + text;
  line.textContent = label + ': ' + line.dataset.acc;
  box.scrollTop = box.scrollHeight;
}

function _voiceRemoveCaptions() {
  const el = document.getElementById('voiceCaptions');
  if (el) el.remove();
}

// ── playback (agent audio) ───────────────────────────────────────────

function _voicePlayChunk(buf) {
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) return;
  if (!_voicePlayCtx) {
    _voicePlayCtx = new AudioCtx({ sampleRate: _VOICE_DOWNLINK_RATE });
    _voicePlayTime = 0;
  }
  const pcm = new Int16Array(buf);
  if (!pcm.length) return;
  const f32 = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;
  const audioBuf = _voicePlayCtx.createBuffer(1, f32.length, _VOICE_DOWNLINK_RATE);
  audioBuf.getChannelData(0).set(f32);
  const src = _voicePlayCtx.createBufferSource();
  src.buffer = audioBuf;
  src.connect(_voicePlayCtx.destination);
  const now = _voicePlayCtx.currentTime;
  if (_voicePlayTime < now + 0.05) _voicePlayTime = now + 0.05;
  src.start(_voicePlayTime);
  _voicePlayTime += audioBuf.duration;
  _voicePlaySources.push(src);
  src.onended = function() {
    const idx = _voicePlaySources.indexOf(src);
    if (idx >= 0) _voicePlaySources.splice(idx, 1);
  };
}

function _voiceFlushPlayback() {
  // Barge-in: the user spoke — drop everything scheduled.
  _voicePlaySources.forEach(src => { try { src.stop(); } catch (_err) {} });
  _voicePlaySources = [];
  if (_voicePlayCtx) _voicePlayTime = _voicePlayCtx.currentTime;
}

// ── capture (mic) ────────────────────────────────────────────────────

function _voiceDownsampleToPcm16(f32, fromRate) {
  const ratio = fromRate / _VOICE_UPLINK_RATE;
  const outLen = Math.floor(f32.length / ratio);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const v = f32[Math.floor(i * ratio)] || 0;
    const s = Math.max(-1, Math.min(1, v));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return out;
}

async function _voiceStartCapture() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  _voiceStream = stream;
  _voiceCaptureCtx = new AudioCtx();
  if (_voiceCaptureCtx.state === 'suspended' && _voiceCaptureCtx.resume) {
    _voiceCaptureCtx.resume().catch(function() {});
  }
  const rate = _voiceCaptureCtx.sampleRate || 48000;
  _voiceCaptureSource = _voiceCaptureCtx.createMediaStreamSource(stream);
  _voiceCaptureProcessor = _voiceCaptureCtx.createScriptProcessor(2048, 1, 1);
  _voiceCaptureProcessor.onaudioprocess = function(e) {
    if (!_voiceActive || !_voiceWs || _voiceWs.readyState !== WebSocket.OPEN) return;
    if (_voiceWs.bufferedAmount > 262144) return; // drop when badly backlogged
    const pcm = _voiceDownsampleToPcm16(e.inputBuffer.getChannelData(0), rate);
    if (pcm.length) _voiceWs.send(pcm.buffer);
  };
  _voiceCaptureSource.connect(_voiceCaptureProcessor);
  _voiceCaptureProcessor.connect(_voiceCaptureCtx.destination);
}

function _voiceStopCapture() {
  if (_voiceCaptureProcessor) {
    try { _voiceCaptureProcessor.disconnect(); } catch (_err) {}
    _voiceCaptureProcessor.onaudioprocess = null;
  }
  if (_voiceCaptureSource) { try { _voiceCaptureSource.disconnect(); } catch (_err) {} }
  if (_voiceStream) _voiceStream.getTracks().forEach(track => track.stop());
  if (_voiceCaptureCtx) { try { _voiceCaptureCtx.close(); } catch (_err) {} }
  _voiceStream = null; _voiceCaptureCtx = null;
  _voiceCaptureSource = null; _voiceCaptureProcessor = null;
}

// ── session lifecycle ────────────────────────────────────────────────

async function toggleVoiceMode() {
  if (_voiceActive) { stopVoiceMode('user'); return; }
  const cid = _voiceConversationId();
  if (!cid) { addMsg('error', _voiceT('voiceNoConversation', 'Open a conversation first')); return; }
  if (!_voiceSelectedService) { addMsg('error', _voiceT('voiceNoService', 'No realtime voice service configured')); return; }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = proto + '://' + location.host + '/ws/realtime/' + encodeURIComponent(cid)
    + '?token=' + encodeURIComponent(getToken() || '')
    + '&service=' + encodeURIComponent(_voiceSelectedService)
    + '&agent=' + encodeURIComponent(selectedAgent || '');
  try {
    await _voiceStartCapture();
  } catch (err) {
    addMsg('error', _voiceT('voiceMicDenied', 'Microphone access denied: ') + (err && err.message ? err.message : err));
    return;
  }
  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  _voiceWs = ws;
  _voiceActive = true;
  _voiceState = 'connecting';
  _voiceUpdateButton();
  ws.onmessage = function(e) {
    if (e.data instanceof ArrayBuffer) { _voicePlayChunk(e.data); return; }
    let msg = {};
    try { msg = JSON.parse(e.data); } catch (_err) { return; }
    if (msg.type === 'ready') { _voiceState = 'listening'; _voiceUpdateButton(); }
    else if (msg.type === 'state') { _voiceState = msg.state || _voiceState; }
    else if (msg.type === 'speech_started') { _voiceFlushPlayback(); }
    else if (msg.type === 'transcript_user') { _voiceCaption('user', msg.text || '', !!msg.final); }
    else if (msg.type === 'transcript_agent') { _voiceCaption('agent', msg.text || '', !!msg.final); }
    else if (msg.type === 'error') { addMsg('error', _voiceT('voiceError', 'Voice session error: ') + (msg.message || '')); }
    else if (msg.type === 'closed') { stopVoiceMode(msg.reason || 'closed'); }
  };
  ws.onerror = function() {
    if (_voiceActive) addMsg('error', _voiceT('voiceWsError', 'Voice session connection failed'));
  };
  ws.onclose = function() { if (_voiceActive) stopVoiceMode('disconnected'); };
}

function stopVoiceMode(reason) {
  if (!_voiceActive && !_voiceWs) return;
  _voiceActive = false;
  const ws = _voiceWs;
  _voiceWs = null;
  if (ws) {
    try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' })); } catch (_err) {}
    try { ws.close(); } catch (_err) {}
  }
  _voiceStopCapture();
  _voiceFlushPlayback();
  if (_voicePlayCtx) { try { _voicePlayCtx.close(); } catch (_err) {} _voicePlayCtx = null; }
  _voiceRemoveCaptions();
  _voiceState = 'idle';
  _voiceUpdateButton();
}

function showVoiceServiceDialog() {
  if (_voiceServices.length < 2) return;
  const names = _voiceServices.map(s => s.id + (s.model ? ' (' + s.model + ')' : ''));
  const pick = prompt(_voiceT('voicePickService', 'Voice service:') + '\n' + names.join('\n'), _voiceSelectedService);
  if (pick && _voiceServices.some(s => s.id === pick.split(' ')[0])) {
    _voiceSelectedService = pick.split(' ')[0];
  }
}

document.addEventListener('DOMContentLoaded', function() {
  _voiceUpdateButton();
  refreshRealtimeVoiceServices();
});
