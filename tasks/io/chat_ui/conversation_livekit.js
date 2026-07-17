// LiveKit engine voice/video mode (P3, docs/REALTIME_MULTIMODAL_LIVEKIT_PLAN.md).
// Media rides WebRTC through the LiveKit server (vendored livekit-client SDK,
// lazy-loaded from /api/realtime/livekit/sdk.js) — no PCM websocket bridge.
// Control stays on PawFlow: POST /api/realtime/livekit/start|stop, captions,
// state, and tool activity arrive as realtime.* SSE events published by the
// worker-control channel. Reuses the voice overlay (orb/captions/tool line)
// from conversation_voice.js; adds camera/screen-share controls when the
// service enables video_input. Final transcripts arrive as normal messages
// via SSE, exactly like the legacy voice mode.

var _lkActive = false;
var _lkStarting = false;
var _lkRoom = null;
var _lkSession = null;      // start payload: {session_id, room, token, ...}
var _lkMicMuted = false;
var _lkCamOn = false;
var _lkScreenOn = false;
var _lkAudioEls = [];
var _lkSseWired = false;

function _lkAuthHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const token = (typeof getToken === 'function' && getToken()) || '';
  if (token) headers['Authorization'] = 'Bearer ' + token;
  return headers;
}

function _lkLoadSdk() {
  if (window.LivekitClient) return Promise.resolve();
  return new Promise(function(resolve, reject) {
    const existing = document.getElementById('livekitSdk');
    if (existing) {
      existing.addEventListener('load', function() { resolve(); });
      existing.addEventListener('error', function() { reject(new Error('SDK load failed')); });
      return;
    }
    const s = document.createElement('script');
    s.id = 'livekitSdk';
    s.src = '/api/realtime/livekit/sdk.js';
    s.onload = function() { window.LivekitClient ? resolve() : reject(new Error('LivekitClient missing after load')); };
    s.onerror = function() { reject(new Error('SDK load failed')); };
    document.head.appendChild(s);
  });
}

// ── SSE wiring (called by sse.js after each (re)connect) ─────────────

function _lkWireSSE() {
  if (typeof eventSource === 'undefined' || !eventSource) return;
  const wire = function(type, handler) {
    eventSource.addEventListener(type, function(e) {
      if (!_lkActive) return;
      let data = {};
      try { data = JSON.parse(e.data || '{}'); } catch (_err) { return; }
      if (_lkSession && data.session_id && data.session_id !== _lkSession.session_id) return;
      handler(data);
    });
  };
  wire('realtime.session.ready', function() { _voiceSetState('listening'); });
  wire('realtime.agent.state', function(d) {
    const map = { initializing: 'connecting', listening: 'listening', thinking: 'thinking', speaking: 'speaking' };
    _voiceSetState(map[d.state] || _voiceState);
  });
  wire('realtime.user.transcript.delta', function(d) { _voiceCaption('user', d.text || '', false); });
  wire('realtime.user.transcript.final', function(d) { _voiceCaption('user', d.text || '', true); });
  wire('realtime.agent.transcript.delta', function(d) { _voiceCaption('agent', d.text || '', false); });
  wire('realtime.agent.transcript.final', function(d) { _voiceCaption('agent', d.text || '', true); });
  wire('realtime.tool.started', function(d) { _voiceSetState('tool'); _voiceToolActivity(d.tool || '', 'running'); });
  wire('realtime.tool.completed', function(d) { _voiceToolActivity(d.tool || '', d.status === 'background' ? 'background' : ''); });
  wire('realtime.tool.rejected', function(d) { _voiceToolActivity(d.tool || '', 'denied'); });
  wire('realtime.session.closed', function() { stopLiveKitVoiceMode('closed'); });
}

// ── overlay controls (extends the shared voice overlay) ─────────────

function _lkExtendOverlay(videoInput, videoSource) {
  const ctl = document.querySelector('#voiceOverlay .voice-ctl');
  if (!ctl) return;
  // The shared overlay's mute/hangup buttons target the legacy bridge —
  // repoint them at the LiveKit room.
  const muteBtn = document.getElementById('voiceMuteBtn');
  if (muteBtn) muteBtn.onclick = function() { _lkToggleMic(); };
  const hangBtn = document.getElementById('voiceHangupBtn');
  if (hangBtn) hangBtn.onclick = function() { stopLiveKitVoiceMode('user'); };
  const commitBtn = document.getElementById('voiceCommitBtn');
  if (commitBtn) commitBtn.style.display = 'none';
  if (!videoInput) return;
  if ((videoSource === 'camera' || videoSource === 'both') && !document.getElementById('lkCamBtn')) {
    const cam = document.createElement('button');
    cam.id = 'lkCamBtn';
    ctl.insertBefore(cam, hangBtn);
    cam.onclick = function() { _lkToggleCamera(); };
  }
  if ((videoSource === 'screen' || videoSource === 'both') && !document.getElementById('lkScreenBtn')) {
    const scr = document.createElement('button');
    scr.id = 'lkScreenBtn';
    ctl.insertBefore(scr, hangBtn);
    scr.onclick = function() { _lkToggleScreen(); };
  }
  _lkRenderControls();
}

function _lkRenderControls() {
  const muteBtn = document.getElementById('voiceMuteBtn');
  if (muteBtn) {
    muteBtn.textContent = _lkMicMuted
      ? '🔇 ' + _voiceT('voiceUnmute', 'Unmute')
      : '🎙 ' + _voiceT('voiceMute', 'Mute');
    muteBtn.classList.toggle('muted', _lkMicMuted);
  }
  const cam = document.getElementById('lkCamBtn');
  if (cam) {
    cam.textContent = (_lkCamOn ? '📷 ' : '📷 ') + (_lkCamOn
      ? _voiceT('lkCamOff', 'Camera off') : _voiceT('lkCamOn', 'Camera'));
    cam.classList.toggle('muted', _lkCamOn);
  }
  const scr = document.getElementById('lkScreenBtn');
  if (scr) {
    scr.textContent = '🖥 ' + (_lkScreenOn
      ? _voiceT('lkScreenOff', 'Stop sharing') : _voiceT('lkScreenOn', 'Share screen'));
    scr.classList.toggle('muted', _lkScreenOn);
  }
}

async function _lkToggleMic() {
  if (!_lkRoom) return;
  _lkMicMuted = !_lkMicMuted;
  try { await _lkRoom.localParticipant.setMicrophoneEnabled(!_lkMicMuted); } catch (_err) {}
  _lkRenderControls();
}

async function _lkToggleCamera() {
  if (!_lkRoom) return;
  _lkCamOn = !_lkCamOn;
  try { await _lkRoom.localParticipant.setCameraEnabled(_lkCamOn); } catch (err) { _lkCamOn = false; }
  _lkRenderControls();
}

async function _lkToggleScreen() {
  if (!_lkRoom) return;
  _lkScreenOn = !_lkScreenOn;
  try { await _lkRoom.localParticipant.setScreenShareEnabled(_lkScreenOn); } catch (err) { _lkScreenOn = false; }
  _lkRenderControls();
}

// ── session lifecycle ────────────────────────────────────────────────

async function startLiveKitVoiceMode(cid, svc) {
  if (_lkActive || _lkStarting) return;
  _lkStarting = true;
  try {
    await _lkLoadSdk();
    const resp = await fetch('/api/realtime/livekit/start', {
      method: 'POST', headers: _lkAuthHeaders(), credentials: 'same-origin',
      body: JSON.stringify({
        service: svc.id || svc,
        conversation_id: cid,
        agent_name: (typeof selectedAgent !== 'undefined' && selectedAgent) || '',
      }),
    });
    const payload = await resp.json();
    if (!resp.ok) {
      addMsg('error', _voiceT('lkStartFailed', 'Live session failed: ') + (payload.error || resp.status));
      return;
    }
    _lkSession = payload;
    const LK = window.LivekitClient;
    const room = new LK.Room({ adaptiveStream: true, dynacast: true });
    _lkRoom = room;
    room.on(LK.RoomEvent.TrackSubscribed, function(track) {
      if (track.kind === 'audio') {
        const el = track.attach();
        el.autoplay = true;
        el.style.display = 'none';
        document.body.appendChild(el);
        _lkAudioEls.push(el);
      }
    });
    room.on(LK.RoomEvent.Disconnected, function() {
      if (_lkActive) stopLiveKitVoiceMode('disconnected');
    });
    _lkActive = true;
    _lkMicMuted = false; _lkCamOn = false; _lkScreenOn = false;
    _voiceShowOverlay();
    _lkExtendOverlay(!!payload.video_input, payload.video_source || 'camera');
    _voiceSetState('connecting');
    if (typeof _voiceUpdateButton === 'function') _voiceUpdateButton();
    // Managed stack: empty livekit_url + a livekit_path — connect
    // same-origin through the PawFlow signal proxy (wss on TLS pages).
    var lkUrl = payload.livekit_url;
    if (!lkUrl && payload.livekit_path) {
      lkUrl = (location.protocol === 'https:' ? 'wss://' : 'ws://')
              + location.host + payload.livekit_path;
    }
    await room.connect(lkUrl, payload.token);
    await room.localParticipant.setMicrophoneEnabled(true);
  } catch (err) {
    addMsg('error', _voiceT('lkStartFailed', 'Live session failed: ') + (err && err.message ? err.message : err));
    stopLiveKitVoiceMode('error');
  } finally {
    _lkStarting = false;
  }
}

function stopLiveKitVoiceMode(reason) {
  if (!_lkActive && !_lkRoom) return;
  _lkActive = false;
  const room = _lkRoom;
  _lkRoom = null;
  const session = _lkSession;
  _lkSession = null;
  if (room) { try { room.disconnect(); } catch (_err) {} }
  _lkAudioEls.forEach(function(el) { try { el.remove(); } catch (_err) {} });
  _lkAudioEls = [];
  if (session && reason !== 'closed') {
    // Tell PawFlow to end the session server-side (worker shutdown, token
    // invalidation). 'closed' means the server already did.
    fetch('/api/realtime/livekit/stop', {
      method: 'POST', headers: _lkAuthHeaders(), credentials: 'same-origin',
      body: JSON.stringify({ session_id: session.session_id }),
    }).catch(function() {});
  }
  if (typeof _voiceRemoveCaptions === 'function') _voiceRemoveCaptions();
  if (typeof _voiceHideOverlay === 'function') _voiceHideOverlay();
  if (typeof _voiceUpdateButton === 'function') _voiceUpdateButton();
}
