// Realtime voice mode. Full-duplex conversation with the agent over the
// /ws/realtime/{conversation_id} bridge: continuous mic PCM16 uplink,
// streamed agent PCM16 downlink, live captions from transcript events.
// Final transcripts are persisted server-side and arrive as normal
// messages through SSE — this module only handles audio + the live
// voice-mode overlay (orb, state, captions, tool activity, mute).
// A "voice-native" agent (realtime_voice_service in its conv config)
// pins its service: no picker, emphasized mic button.

var _voiceServices = [];
var _voiceSelectedService = '';
var _voiceLinkedService = '';
var _voiceActive = false;
var _voiceStarting = false; // a second click while connecting must not open a parallel capture/session
var _voiceMuted = false;
var _voiceWs = null;
var _voiceStream = null;
var _voiceCaptureCtx = null;
var _voiceCaptureSource = null;
var _voiceCaptureProcessor = null;
var _voicePlayCtx = null;
var _voicePlayTime = 0;
var _voicePlaySources = [];
var _voiceState = 'idle';

// OpenAI realtime `pcm16` is FIXED at 24 kHz mono little-endian in both
// directions — the bridge relays frames verbatim, so the browser must
// capture at that rate too (16 kHz uplink would play 1.5x fast provider-side
// and wreck transcription).
var _VOICE_UPLINK_RATE = 24000;   // PCM16 mono sent to the bridge
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
  // Voice-native agent: emphasized button (its service is pinned)
  if (_voiceLinkedService && !_voiceActive) {
    btn.style.color = 'var(--pf-accent, #7aa2f7)';
    btn.title = _voiceT('voiceLinkedTitle', 'Voice agent — start voice conversation');
  } else {
    btn.style.color = '';
  }
}

function refreshRealtimeVoiceServices(done) {
  if (typeof action$ !== 'function') { if (done) done(); return; }
  action$('list_realtime_services', {
    conversation_id: _voiceConversationId(),
    agent_name: (typeof selectedAgent !== 'undefined' && selectedAgent) ? selectedAgent : '',
  }, { silent: true }).subscribe(res => {
    // {services, linked} since P2b; tolerate the P1 bare-array shape.
    _voiceServices = Array.isArray(res) ? res : ((res && res.services) || []);
    _voiceLinkedService = (!Array.isArray(res) && res && res.linked) || '';
    if (_voiceLinkedService) {
      _voiceSelectedService = _voiceLinkedService;
    } else if ((!_voiceSelectedService || !_voiceServices.some(s => s.id === _voiceSelectedService))
               && _voiceServices.length) {
      _voiceSelectedService = _voiceServices[0].id;
    }
    _voiceUpdateButton();
    if (done) done();
  }, () => { _voiceServices = []; _voiceLinkedService = ''; _voiceUpdateButton(); if (done) done(); });
}

// ── voice-mode overlay ───────────────────────────────────────────────

function _voiceEnsureStyles() {
  if (document.getElementById('voiceOverlayStyles')) return;
  const st = document.createElement('style');
  st.id = 'voiceOverlayStyles';
  st.textContent = ''
    + '#voiceOverlay{position:fixed;inset:0;z-index:9998;display:flex;flex-direction:column;'
    + 'align-items:center;justify-content:center;gap:18px;background:rgba(10,10,18,.88);backdrop-filter:blur(6px);}'
    + '#voiceOrb{width:120px;height:120px;border-radius:50%;transition:transform .08s ease-out, background .3s, box-shadow .3s;'
    + 'background:radial-gradient(circle at 35% 30%, #9db8ff, #3b5bdb);box-shadow:0 0 40px rgba(80,120,255,.45);}'
    + '#voiceOverlay.vs-connecting #voiceOrb{animation:voicePulse 1.2s infinite;opacity:.7;}'
    + '#voiceOverlay.vs-listening #voiceOrb{background:radial-gradient(circle at 35% 30%, #8ef0c0, #14a06a);box-shadow:0 0 40px rgba(40,200,140,.45);}'
    + '#voiceOverlay.vs-thinking #voiceOrb{background:radial-gradient(circle at 35% 30%, #ffe29a, #d9930d);box-shadow:0 0 40px rgba(240,180,50,.45);animation:voicePulse 1.4s infinite;}'
    + '#voiceOverlay.vs-speaking #voiceOrb{background:radial-gradient(circle at 35% 30%, #9db8ff, #3b5bdb);box-shadow:0 0 60px rgba(80,120,255,.7);}'
    + '#voiceOverlay.vs-tool #voiceOrb{background:radial-gradient(circle at 35% 30%, #e2b8ff, #8b3bdb);box-shadow:0 0 40px rgba(170,80,255,.5);animation:voicePulse 1s infinite;}'
    + '@keyframes voicePulse{0%,100%{transform:scale(1);}50%{transform:scale(1.07);}}'
    + '#voiceStateLabel{color:#dfe3f0;font-size:15px;letter-spacing:.4px;}'
    + '#voiceAgentLabel{color:#8f96ad;font-size:12px;}'
    + '#voiceToolLine{color:#c9a6ff;font-size:13px;min-height:18px;}'
    + '#voiceCaptions{width:min(680px,86vw);max-height:26vh;overflow-y:auto;color:#cfd4e4;'
    + 'font-size:14px;line-height:1.45;text-align:center;}'
    + '#voiceCaptions div{margin:2px 0;}'
    + '.voice-ctl{display:flex;gap:14px;margin-top:6px;}'
    + '.voice-ctl button{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.18);'
    + 'color:#e8ebf5;border-radius:24px;padding:10px 20px;font-size:14px;cursor:pointer;}'
    + '.voice-ctl button:hover{background:rgba(255,255,255,.16);}'
    + '.voice-ctl button.danger{background:rgba(220,60,80,.25);border-color:rgba(220,60,80,.5);}'
    + '.voice-ctl button.muted{background:rgba(240,180,50,.25);border-color:rgba(240,180,50,.5);}';
  document.head.appendChild(st);
}

function _voiceShowOverlay() {
  _voiceEnsureStyles();
  let ov = document.getElementById('voiceOverlay');
  if (ov) return ov;
  ov = document.createElement('div');
  ov.id = 'voiceOverlay';
  const agentLabel = (typeof displayAgentName === 'function')
    ? displayAgentName((typeof selectedAgent !== 'undefined' && selectedAgent) || 'agent')
    : 'Agent';
  ov.innerHTML = ''
    + '<div id="voiceOrb"></div>'
    + '<div id="voiceStateLabel"></div>'
    + '<div id="voiceAgentLabel"></div>'
    + '<div id="voiceToolLine"></div>'
    + '<div id="voiceCaptions"></div>'
    + '<div class="voice-ctl">'
    + '<button id="voiceMuteBtn"></button>'
    + '<button id="voiceCommitBtn" style="display:none"></button>'
    + '<button id="voiceHangupBtn" class="danger"></button>'
    + '</div>';
  document.body.appendChild(ov);
  ov.querySelector('#voiceAgentLabel').textContent = agentLabel
    + (_voiceSelectedService ? ' · ' + _voiceSelectedService : '');
  const muteBtn = ov.querySelector('#voiceMuteBtn');
  muteBtn.onclick = function() { _voiceToggleMute(); };
  const commitBtn = ov.querySelector('#voiceCommitBtn');
  commitBtn.textContent = '📤 ' + _voiceT('voiceSendTurn', 'Send');
  commitBtn.onclick = function() { _voiceCommitTurn(); };
  ov.querySelector('#voiceHangupBtn').textContent = '⏹ ' + _voiceT('voiceHangUp', 'End');
  ov.querySelector('#voiceHangupBtn').onclick = function() { stopVoiceMode('user'); };
  _voiceRenderMute();
  return ov;
}

function _voiceCommitTurn() {
  // Manual VAD (push-to-talk): the user finished speaking — commit the
  // audio buffer so the agent answers. Hidden in server-VAD sessions.
  if (_voiceWs && _voiceWs.readyState === WebSocket.OPEN) {
    _voiceWs.send(JSON.stringify({ type: 'commit' }));
    _voiceSetState('thinking');
  }
}

function _voiceHideOverlay() {
  const ov = document.getElementById('voiceOverlay');
  if (ov) ov.remove();
}

function _voiceRenderMute() {
  const btn = document.getElementById('voiceMuteBtn');
  if (!btn) return;
  btn.textContent = _voiceMuted
    ? '🔇 ' + _voiceT('voiceUnmute', 'Unmute')
    : '🎙 ' + _voiceT('voiceMute', 'Mute');
  btn.classList.toggle('muted', _voiceMuted);
}

function _voiceToggleMute() {
  _voiceMuted = !_voiceMuted;
  _voiceRenderMute();
}

function _voiceSetState(state) {
  _voiceState = state;
  const ov = document.getElementById('voiceOverlay');
  if (!ov) return;
  ov.className = 'vs-' + state;
  const labels = {
    connecting: _voiceT('voiceStateConnecting', 'Connecting…'),
    listening: _voiceT('voiceStateListening', 'Listening'),
    thinking: _voiceT('voiceStateThinking', 'Thinking…'),
    speaking: _voiceT('voiceStateSpeaking', 'Speaking'),
    tool: _voiceT('voiceStateTool', 'Using a tool…'),
  };
  const el = ov.querySelector('#voiceStateLabel');
  if (el) el.textContent = labels[state] || state;
}

function _voiceOrbLevel(level) {
  const orb = document.getElementById('voiceOrb');
  if (!orb) return;
  const clamped = Math.min(0.35, level * 3);
  orb.style.transform = 'scale(' + (1 + clamped) + ')';
}

function _voiceToolActivity(name, status) {
  const line = document.getElementById('voiceToolLine');
  if (!line) return;
  if (status === 'running') {
    line.textContent = '🔧 ' + _voiceT('voiceToolRunning', 'Running tool:') + ' ' + name;
  } else if (status === 'background') {
    line.textContent = '🔧 ' + name + ' → ' + _voiceT('voiceToolBackground', 'continues in background');
  } else if (status === 'denied') {
    line.textContent = '🔒 ' + name + ' ' + _voiceT('voiceToolDenied', 'needs approval (text chat)');
  } else {
    line.textContent = '';
  }
}

// ── captions ─────────────────────────────────────────────────────────

function _voiceCaptions() {
  // Lives inside the overlay; falls back to body if it is somehow gone.
  let el = document.getElementById('voiceCaptions');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'voiceCaptions';
  (document.getElementById('voiceOverlay') || document.body).appendChild(el);
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
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) {
    f32[i] = pcm[i] / 32768;
    sum += f32[i] * f32[i];
  }
  _voiceOrbLevel(Math.sqrt(sum / pcm.length));
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
    if (!_voiceActive || _voiceMuted || !_voiceWs || _voiceWs.readyState !== WebSocket.OPEN) return;
    if (_voiceWs.bufferedAmount > 262144) return; // drop when badly backlogged
    const f32 = e.inputBuffer.getChannelData(0);
    if (_voiceState === 'listening') {
      let sum = 0;
      for (let i = 0; i < f32.length; i += 8) sum += f32[i] * f32[i];
      _voiceOrbLevel(Math.sqrt(sum / (f32.length / 8)));
    }
    const pcm = _voiceDownsampleToPcm16(f32, rate);
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
  if (_voiceStarting) return;
  if (_voiceActive) { stopVoiceMode('user'); return; }
  const cid = _voiceConversationId();
  if (!cid) { addMsg('error', _voiceT('voiceNoConversation', 'Open a conversation first')); return; }
  _voiceStarting = true;
  try {
    await _toggleVoiceModeStart(cid);
  } finally {
    _voiceStarting = false;
  }
}

async function _toggleVoiceModeStart(cid) {
  const pick = document.getElementById('voiceServicePick');
  if (pick) pick.remove();
  // Fresh fetch: the agent link depends on the current conversation/agent,
  // and the action lazily registers the /ws/realtime route on the listener.
  await new Promise(resolve => refreshRealtimeVoiceServices(resolve));
  if (!_voiceSelectedService) { addMsg('error', _voiceT('voiceNoService', 'No realtime voice service configured')); return; }
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = proto + '://' + location.host + '/ws/realtime/' + encodeURIComponent(cid)
    + '?token=' + encodeURIComponent(getToken() || '')
    + '&service=' + encodeURIComponent(_voiceSelectedService)
    + '&agent=' + encodeURIComponent(selectedAgent || '');
  try {
    await _voiceStartCapture();
  } catch (err) {
    _voiceStopCapture(); // a failure after getUserMedia must not leave the mic held
    addMsg('error', _voiceT('voiceMicDenied', 'Microphone access denied: ') + (err && err.message ? err.message : err));
    return;
  }
  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  _voiceWs = ws;
  _voiceActive = true;
  _voiceMuted = false;
  _voiceUpdateButton();
  _voiceShowOverlay();
  _voiceSetState('connecting');
  ws.onmessage = function(e) {
    if (e.data instanceof ArrayBuffer) { _voicePlayChunk(e.data); return; }
    let msg = {};
    try { msg = JSON.parse(e.data); } catch (_err) { return; }
    if (msg.type === 'ready') {
      _voiceSetState('listening');
      // Manual VAD: the provider never auto-detects end of turn — show the
      // explicit "send" control the user presses after speaking.
      const commitBtn = document.getElementById('voiceCommitBtn');
      if (commitBtn) commitBtn.style.display = (msg.vad === 'manual') ? '' : 'none';
    }
    else if (msg.type === 'state') { _voiceSetState(msg.state || _voiceState); }
    else if (msg.type === 'speech_started') { _voiceFlushPlayback(); }
    else if (msg.type === 'transcript_user') { _voiceCaption('user', msg.text || '', !!msg.final); }
    else if (msg.type === 'transcript_agent') { _voiceCaption('agent', msg.text || '', !!msg.final); }
    else if (msg.type === 'tool') { _voiceToolActivity(msg.name || '', msg.status || ''); }
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
  _voiceMuted = false;
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
  _voiceHideOverlay();
  _voiceState = 'idle';
  _voiceUpdateButton();
}

function showVoiceServiceDialog() {
  if (_voiceLinkedService) return; // voice-native agent: service is pinned
  if (_voiceServices.length < 2) return;
  const old = document.getElementById('voiceServicePick');
  if (old) { old.remove(); return; } // toggle
  const panel = document.createElement('div');
  panel.id = 'voiceServicePick';
  panel.style.cssText = 'position:fixed;bottom:70px;right:16px;z-index:9999;'
    + 'background:var(--pf-sidebar,#1c1e2a);border:1px solid var(--pf-border,#444);'
    + 'border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:2px;'
    + 'box-shadow:0 4px 18px rgba(0,0,0,.5);';
  const title = document.createElement('div');
  title.textContent = _voiceT('voicePickService', 'Voice service:');
  title.style.cssText = 'font-size:11px;color:var(--pf-muted,#8f96ad);padding:2px 6px 6px;';
  panel.appendChild(title);
  _voiceServices.forEach(function(s) {
    const b = document.createElement('button');
    b.textContent = (s.id === _voiceSelectedService ? '✓ ' : '\u2003') + s.id
      + (s.model ? ' (' + s.model + ')' : '');
    b.style.cssText = 'text-align:left;background:none;border:none;'
      + 'color:var(--pf-text,#e8ebf5);padding:6px 10px;border-radius:4px;'
      + 'cursor:pointer;font-size:13px;';
    b.onmouseenter = function() { b.style.background = 'rgba(255,255,255,.08)'; };
    b.onmouseleave = function() { b.style.background = 'none'; };
    b.onclick = function() {
      _voiceSelectedService = s.id;
      panel.remove();
      _voiceUpdateButton();
    };
    panel.appendChild(b);
  });
  document.body.appendChild(panel);
}

document.addEventListener('DOMContentLoaded', function() {
  _voiceUpdateButton();
  refreshRealtimeVoiceServices();
});
