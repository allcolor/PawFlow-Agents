// Audio streaming — WebCodecs decoder + AudioWorklet ring buffer.
// Pipeline: WS → Opus → AudioDecoder → PCM batch → AudioWorklet ring buffer → speakers

var _audioCtx = null;
var _audioWs = null;
var _audioSessionId = null;
var _audioMuted = false;
var _audioVolume = 1.0;
var _audioGain = null;
var _audioDecoder = null;
var _audioTimestamp = 0;
var _audioWorkletNode = null;
var _audioWorkletReady = false;
var _audioWorkletModuleLoaded = false;

// PCM batch accumulator (reduces postMessage frequency)
var _pcmBatch = null;       // Float32Array(960) = 20ms
var _pcmBatchPos = 0;
var _PCM_BATCH_SIZE = 960;   // 20ms at 48kHz — one Opus frame, minimal latency
var _pcmFlushTimer = null;   // timer to flush partial batches

// Pre-buffer: accumulate before sending to worklet
var _preBuffer = [];          // array of Float32Array chunks
var _preBufferSamples = 0;
var _preBufferDone = false;
var _PRE_BUFFER_TARGET = 1440; // 30ms at 48kHz — minimal pre-buffer for tight A/V sync

// Diagnostic stats
var _audioStats = {
  wsMessages: 0,
  decoderResets: 0,
  decoderErrors: 0,
  batchesSent: 0,
  partialFlushes: 0,
  underruns: 0,
  ringFill: 0,
  lastLogTime: 0,
};
var _statsInterval = null;

var _WORKLET_CODE = `
class AudioRingProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ring = new Float32Array(48000 * 8); // 8s ring buffer
    this.wPos = 0;
    this.rPos = 0.0; // fractional read position for smooth resampling
    this.underruns = 0;
    this.port.onmessage = (e) => {
      if (e.data === 'stats') {
        this.port.postMessage({
          type: 'stats',
          fill: this.wPos - Math.floor(this.rPos),
          underruns: this.underruns,
        });
        this.underruns = 0;
        return;
      }
      if (e.data === 'reset') {
        this.wPos = 0;
        this.rPos = 0.0;
        this.underruns = 0;
        return;
      }
      const samples = e.data;
      const len = this.ring.length;
      for (let i = 0; i < samples.length; i++) {
        this.ring[this.wPos % len] = samples[i];
        this.wPos++;
      }
      // Overflow protection: hard skip only at ring capacity
      if (this.wPos - this.rPos > len) {
        this.rPos = this.wPos - len + 7200;
      }
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    const len = this.ring.length;
    const irPos = Math.floor(this.rPos);
    const available = this.wPos - irPos;
    // Continuous proportional clock drift compensation.
    // Target fill: 2400 samples (50ms). Step adjusts proportionally
    // to distance from target — always correcting, never accumulating drift.
    const TARGET = 2400; // 50ms
    let step = 1.0;
    if (available > 96000) {
      // Extreme (>2s): hard skip
      this.rPos = this.wPos - TARGET;
      step = 1.0;
    } else {
      // Proportional: +0.01 per 1000 samples above target (max +5%)
      // Below target: -0.003 per 1000 samples below (max -0.5%)
      const diff = available - TARGET;
      if (diff > 0) {
        step = 1.0 + Math.min(diff * 0.00001, 0.05);
      } else {
        step = 1.0 + Math.max(diff * 0.000003, -0.005);
      }
    }
    if (available < out.length) {
      this.underruns++;
    }
    // Read with linear interpolation for smooth resampling
    for (let i = 0; i < out.length; i++) {
      const ri = Math.floor(this.rPos);
      if (ri < this.wPos) {
        const frac = this.rPos - ri;
        const s0 = this.ring[ri % len];
        const s1 = (ri + 1 < this.wPos) ? this.ring[(ri + 1) % len] : s0;
        out[i] = s0 + frac * (s1 - s0);
        this.rPos += step;
      } else {
        out[i] = 0;
      }
    }
    return true;
  }
}
registerProcessor('audio-ring-processor', AudioRingProcessor);
`;

// Keep AudioContext alive — browsers suspend it on focus/visibility changes.
// Periodic check + event handlers. Reset ring buffer after resume to skip stale audio.
function _resumeAudio() {
  if (_audioCtx && _audioCtx.state === 'suspended') {
    _audioCtx.resume().then(function() {
      console.log('[audio] resumed AudioContext');
      if (_audioWorkletNode && _audioWorkletReady) {
        _audioWorkletNode.port.postMessage('reset');
      }
    });
  }
}
setInterval(_resumeAudio, 500);
document.addEventListener('visibilitychange', function() {
  if (!document.hidden) _resumeAudio();
});
window.addEventListener('focus', _resumeAudio);
window.addEventListener('click', _resumeAudio);
window.addEventListener('keydown', _resumeAudio);

function audioConnect(sessionId) {
  if (_audioWs) audioDisconnect();
  _audioSessionId = sessionId;

  if (typeof AudioDecoder === 'undefined') {
    console.error('[audio] WebCodecs not available');
    return;
  }

  _audioTimestamp = 0;
  _audioWorkletReady = false;
  _pcmBatch = new Float32Array(_PCM_BATCH_SIZE);
  _pcmBatchPos = 0;
  _preBuffer = [];
  _preBufferSamples = 0;
  _preBufferDone = false;
  _audioStats = { wsMessages: 0, decoderResets: 0, decoderErrors: 0, batchesSent: 0, partialFlushes: 0, underruns: 0, ringFill: 0, lastLogTime: 0 };

  // Always recreate AudioContext to avoid stale graph state after reconnect
  if (_audioCtx) {
    try { _audioCtx.close(); } catch(e) {}
  }
  _audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
  _audioGain = _audioCtx.createGain();
  _audioGain.connect(_audioCtx.destination);
  _audioGain.gain.value = _audioMuted ? 0 : _audioVolume;
  _audioWorkletModuleLoaded = false;
  if (_audioCtx.state === 'suspended') _audioCtx.resume();

  function _setupWorkletNode() {
    _audioWorkletNode = new AudioWorkletNode(_audioCtx, 'audio-ring-processor');
    _audioWorkletNode.connect(_audioGain);
    _audioWorkletNode.port.onmessage = function(e) {
      if (e.data && e.data.type === 'stats') {
        _audioStats.underruns += e.data.underruns;
        _audioStats.ringFill = e.data.fill;
      }
    };
    _audioWorkletReady = true;
    console.log('[audio] worklet ready');
  }

  if (!_audioWorkletModuleLoaded) {
    var blob = new Blob([_WORKLET_CODE], { type: 'application/javascript' });
    var blobUrl = URL.createObjectURL(blob);
    _audioCtx.audioWorklet.addModule(blobUrl).then(function() {
      URL.revokeObjectURL(blobUrl);
      _audioWorkletModuleLoaded = true;
      _setupWorkletNode();
    }).catch(function(e) {
      console.error('[audio] worklet init failed:', e);
    });
  } else {
    _setupWorkletNode();
  }

  _createDecoder();

  // Stats logging every 5 seconds
  _statsInterval = setInterval(function() {
    if (_audioWorkletNode && _audioWorkletReady) {
      _audioWorkletNode.port.postMessage('stats');
    }
    setTimeout(function() {
      console.log(new Date().toISOString().substr(11,8) + ' [audio-stats] ws_msgs=' + _audioStats.wsMessages +
        ' dec_resets=' + _audioStats.decoderResets +
        ' dec_errors=' + _audioStats.decoderErrors +
        ' batches=' + _audioStats.batchesSent +
        ' partial_flush=' + _audioStats.partialFlushes +
        ' underruns=' + _audioStats.underruns +
        ' ring_fill=' + _audioStats.ringFill +
        ' (' + Math.round(_audioStats.ringFill / 48) + 'ms)' +
        ' dec_queue=' + (_audioDecoder ? _audioDecoder.decodeQueueSize : 'N/A'));
      _audioStats.wsMessages = 0;
      _audioStats.decoderResets = 0;
      _audioStats.decoderErrors = 0;
      _audioStats.batchesSent = 0;
      _audioStats.partialFlushes = 0;
    }, 100);
  }, 5000);

  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var url = proto + '//' + location.host + '/audio/' + sessionId + '/stream';
  _audioWs = new WebSocket(url);
  _audioWs.binaryType = 'arraybuffer';

  _audioWs.onopen = function() {
    console.log('[audio] connected to', sessionId);
    _updateAudioUI(true);
  };

  _audioWs.onmessage = function(evt) {
    if (_audioMuted) return;
    if (!(evt.data instanceof ArrayBuffer) || evt.data.byteLength === 0) return;
    _audioStats.wsMessages++;
    // Resume AudioContext if browser suspended it (tab switch, focus loss)
    if (_audioCtx && _audioCtx.state === 'suspended') _audioCtx.resume();
    if (!_audioDecoder) _createDecoder();
    if (!_audioDecoder || _audioDecoder.state !== 'configured') return;

    // Reset decoder if queue is backing up
    if (_audioDecoder.decodeQueueSize > 10) {
      _audioDecoder.reset();
      _audioDecoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1 });
      _audioTimestamp += 20000;
      _audioStats.decoderResets++;
      return;
    }

    try {
      _audioDecoder.decode(new EncodedAudioChunk({
        type: 'key',
        timestamp: _audioTimestamp,
        duration: 20000,
        data: evt.data
      }));
    } catch(e) {
      _audioStats.decoderErrors++;
      try {
        _audioDecoder.reset();
        _audioDecoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1 });
      } catch(e2) {
        _createDecoder();
      }
    }
    _audioTimestamp += 20000;
  };

  _audioWs.onclose = function() {
    console.log('[audio] disconnected');
    _audioWs = null;
    _updateAudioUI(false);
  };
  _audioWs.onerror = function(e) { console.warn('[audio] ws error:', e); };
}

function _createDecoder() {
  if (_audioDecoder) {
    try { _audioDecoder.close(); } catch(e) {}
  }
  _audioDecoder = new AudioDecoder({
    output: _onDecodedAudio,
    error: function(e) {
      console.warn('[audio] decoder error:', e.message || e);
      _audioStats.decoderErrors++;
      try {
        _audioDecoder.reset();
        _audioDecoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1 });
      } catch(e2) {
        _audioDecoder = null;
      }
    }
  });
  _audioDecoder.configure({ codec: 'opus', sampleRate: 48000, numberOfChannels: 1 });
}

function _onDecodedAudio(audioData) {
  var samples = new Float32Array(audioData.numberOfFrames);
  audioData.copyTo(samples, { planeIndex: 0, format: 'f32-planar' });
  audioData.close();

  // Pre-buffering phase: accumulate before sending anything
  if (!_preBufferDone) {
    _preBuffer.push(samples);
    _preBufferSamples += samples.length;
    if (_preBufferSamples >= _PRE_BUFFER_TARGET) {
      _preBufferDone = true;
      for (var i = 0; i < _preBuffer.length; i++) {
        _addToBatch(_preBuffer[i]);
      }
      _preBuffer = [];
      console.log('[audio] pre-buffer filled (' + _preBufferSamples + ' samples)');
    }
    return;
  }

  _addToBatch(samples);
}

function _addToBatch(samples) {
  if (!_audioWorkletReady || !_audioWorkletNode) return;
  if (_pcmFlushTimer) { clearTimeout(_pcmFlushTimer); _pcmFlushTimer = null; }

  var srcOff = 0;
  var remaining = samples.length;
  while (remaining > 0) {
    var space = _PCM_BATCH_SIZE - _pcmBatchPos;
    var copy = Math.min(remaining, space);
    _pcmBatch.set(samples.subarray(srcOff, srcOff + copy), _pcmBatchPos);
    _pcmBatchPos += copy;
    srcOff += copy;
    remaining -= copy;
    if (_pcmBatchPos >= _PCM_BATCH_SIZE) {
      _audioWorkletNode.port.postMessage(_pcmBatch, [_pcmBatch.buffer]);
      _pcmBatch = new Float32Array(_PCM_BATCH_SIZE);
      _pcmBatchPos = 0;
      _audioStats.batchesSent++;
    }
  }
  if (_pcmBatchPos > 0) {
    _pcmFlushTimer = setTimeout(_flushPartialBatch, 15);
  }
}

function _flushPartialBatch() {
  _pcmFlushTimer = null;
  if (!_audioWorkletReady || !_audioWorkletNode || _pcmBatchPos === 0) return;
  var partial = _pcmBatch.subarray(0, _pcmBatchPos);
  _audioWorkletNode.port.postMessage(new Float32Array(partial));
  _pcmBatch = new Float32Array(_PCM_BATCH_SIZE);
  _pcmBatchPos = 0;
  _audioStats.partialFlushes++;
  _audioStats.batchesSent++;
}

function audioDisconnect() {
  if (_statsInterval) { clearInterval(_statsInterval); _statsInterval = null; }
  if (_pcmFlushTimer) { clearTimeout(_pcmFlushTimer); _pcmFlushTimer = null; }
  if (_audioWs) { _audioWs.close(); _audioWs = null; }
  if (_audioDecoder) {
    try { _audioDecoder.close(); } catch(e) {}
    _audioDecoder = null;
  }
  if (_audioWorkletNode) {
    _audioWorkletNode.disconnect();
    _audioWorkletNode = null;
  }
  if (_audioCtx) {
    try { _audioCtx.close(); } catch(e) {}
    _audioCtx = null;
    _audioGain = null;
  }
  _audioWorkletReady = false;
  _audioWorkletModuleLoaded = false;
  _pcmBatch = null;
  _pcmBatchPos = 0;
  _preBuffer = [];
  _preBufferDone = false;
  _audioSessionId = null;
  _updateAudioUI(false);
}

function audioToggleMute() {
  _audioMuted = !_audioMuted;
  if (_audioGain) _audioGain.gain.value = _audioMuted ? 0 : _audioVolume;
  if (_audioWs && _audioWs.readyState === WebSocket.OPEN)
    _audioWs.send(JSON.stringify({ cmd: _audioMuted ? 'mute' : 'unmute' }));
  _updateAudioUI(_audioWs !== null);
}

function audioSetVolume(val) {
  _audioVolume = Math.max(0, Math.min(1, val));
  if (_audioGain && !_audioMuted) _audioGain.gain.value = _audioVolume;
}

function _updateAudioUI(connected) {
  var btn = document.getElementById('audioToggleBtn');
  if (!btn) return;
  btn.style.display = connected ? 'inline-block' : 'none';
  btn.textContent = _audioMuted ? '\uD83D\uDD07' : '\uD83D\uDD0A';
  btn.title = _audioMuted ? 'Unmute audio' : 'Mute audio';
}
