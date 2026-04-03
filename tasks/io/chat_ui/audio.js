// Audio streaming — WebCodecs decoder + SharedArrayBuffer ring buffer.
// Pipeline: WS → Opus → AudioDecoder → PCM → SharedArrayBuffer → AudioWorklet → speakers
// SharedArrayBuffer eliminates postMessage latency — worklet reads directly from shared memory.

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

// SharedArrayBuffer ring buffer (shared between main thread and worklet)
var _sharedBuf = null;       // SharedArrayBuffer
var _sharedRing = null;      // Float32Array view of ring data
var _sharedCtrl = null;      // Int32Array: [wPos, rPosFrac_hi, rPosFrac_lo, underruns]
var _RING_SIZE = 48000 * 4;  // 4s ring buffer
var _useSAB = typeof SharedArrayBuffer !== 'undefined' && typeof Atomics !== 'undefined';

// Fallback: postMessage path (when SAB not available)
var _pcmBatch = null;
var _pcmBatchPos = 0;
var _PCM_BATCH_SIZE = 960;
var _pcmFlushTimer = null;

// Pre-buffer
var _preBuffer = [];
var _preBufferSamples = 0;
var _preBufferDone = false;
var _PRE_BUFFER_TARGET = 4800; // 100ms at 48kHz — matches worklet TARGET

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

// ── Worklet code (runs in AudioWorklet thread) ──────────────────────
var _WORKLET_CODE = `
class AudioRingProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.useSAB = false;
    this.underruns = 0;
    // Base step: ratio between source rate (48kHz Opus) and output rate (hardware)
    this.baseStep = 48000 / sampleRate;
    // postMessage fallback ring
    this.ring = new Float32Array(48000 * 4);
    this.wPos = 0;
    this.rPos = 0.0;

    this.port.onmessage = (e) => {
      if (e.data && e.data.type === 'init-sab') {
        // SharedArrayBuffer mode: receive shared buffers
        this.sabRing = new Float32Array(e.data.ring);
        this.sabCtrl = new Int32Array(e.data.ctrl);
        this.useSAB = true;
        this.sabRPos = 0.0;
        return;
      }
      if (e.data === 'stats') {
        const fill = this.useSAB
          ? Atomics.load(this.sabCtrl, 0) - Math.floor(this.sabRPos)
          : this.wPos - Math.floor(this.rPos);
        const curStep = this.useSAB ? (this._smoothStep || this.baseStep) : (this._pmSmoothStep || this.baseStep);
        const measN = this.useSAB ? (this._measN || 0) : (this._pmMeasN || 0);
        this.port.postMessage({ type: 'stats', fill: fill, underruns: this.underruns, sampleRate: sampleRate, baseStep: this.baseStep, curStep: curStep });
        this.underruns = 0;
        return;
      }
      if (e.data === 'reset') {
        if (this.useSAB) {
          this.sabRPos = Atomics.load(this.sabCtrl, 0);
        } else {
          this.wPos = 0;
          this.rPos = 0.0;
        }
        this.underruns = 0;
        return;
      }
      // postMessage fallback: receive PCM samples
      if (!this.useSAB) {
        const samples = e.data;
        const len = this.ring.length;
        for (let i = 0; i < samples.length; i++) {
          this.ring[this.wPos % len] = samples[i];
          this.wPos++;
        }
        if (this.wPos - this.rPos > len) {
          this.rPos = this.wPos - len + 2400;
        }
      }
    };
  }

  process(inputs, outputs) {
    const out = outputs[0][0];
    if (this.useSAB) {
      this._processSAB(out);
    } else {
      this._processPostMsg(out);
    }
    return true;
  }

  _processSAB(out) {
    const ring = this.sabRing;
    const len = ring.length;
    const wPos = Atomics.load(this.sabCtrl, 0);
    const available = wPos - Math.floor(this.sabRPos);
    const TARGET = 4800; // 100ms at 48kHz

    // Rate measurement: every ~2s, compute actual input/output ratio
    if (this._measWPos === undefined) {
      this._measWPos = wPos;
      this._measN = 0;
      this._smoothStep = this.baseStep;
    }
    this._measN++;
    if (this._measN >= 750) { // 750 calls * 128 samples = 96000 = 2s at 48kHz
      const samplesIn = wPos - this._measWPos;
      const samplesOut = this._measN * out.length;
      if (samplesOut > 0 && samplesIn > 0) {
        const ratio = samplesIn / samplesOut;
        this._smoothStep = this._smoothStep * 0.6 + ratio * 0.4;
      }
      this._measWPos = wPos;
      this._measN = 0;
    }
    this._smoothStep = Math.max(this.baseStep * 0.85, Math.min(this.baseStep * 1.15, this._smoothStep));

    // Micro-correction: nudge step based on buffer fill (keeps fill near TARGET)
    const error = available - TARGET;
    const step = this._smoothStep + error * 0.000002;

    // Emergency snap only if buffer exceeds 2 seconds (96000 samples)
    if (available > 96000) {
      this.sabRPos = wPos - TARGET;
    }

    const wasUnderrun = this._sabWasUnderrun || false;
    if (available < out.length) this.underruns++;
    this._sabWasUnderrun = (available < out.length);
    if (wasUnderrun && available >= out.length && available > TARGET * 2) {
      this.sabRPos = wPos - TARGET;
    }
    let fadeIn = wasUnderrun && available >= out.length ? 64 : 0;

    for (let i = 0; i < out.length; i++) {
      const ri = Math.floor(this.sabRPos);
      if (ri < wPos) {
        const frac = this.sabRPos - ri;
        const s0 = ring[ri % len];
        const s1 = (ri + 1 < wPos) ? ring[(ri + 1) % len] : s0;
        let sample = s0 + frac * (s1 - s0);
        if (fadeIn > 0) {
          sample *= (64 - fadeIn) / 64;
          fadeIn--;
        }
        out[i] = sample;
        this.sabRPos += step;
      } else {
        out[i] = i > 0 ? out[i-1] * 0.99 : 0;
      }
    }
  }

  _processPostMsg(out) {
    const len = this.ring.length;
    const irPos = Math.floor(this.rPos);
    const available = this.wPos - irPos;
    const TARGET = 4800;

    // Rate measurement: same as SAB path
    if (this._pmMeasWPos === undefined) {
      this._pmMeasWPos = this.wPos;
      this._pmMeasN = 0;
      this._pmSmoothStep = this.baseStep;
    }
    this._pmMeasN++;
    if (this._pmMeasN >= 750) {
      const samplesIn = this.wPos - this._pmMeasWPos;
      const samplesOut = this._pmMeasN * out.length;
      if (samplesOut > 0 && samplesIn > 0) {
        const ratio = samplesIn / samplesOut;
        this._pmSmoothStep = this._pmSmoothStep * 0.6 + ratio * 0.4;
      }
      this._pmMeasWPos = this.wPos;
      this._pmMeasN = 0;
    }
    this._pmSmoothStep = Math.max(this.baseStep * 0.85, Math.min(this.baseStep * 1.15, this._pmSmoothStep));

    const error = available - TARGET;
    const step = this._pmSmoothStep + error * 0.000002;

    if (available > 96000) {
      this.rPos = this.wPos - TARGET;
    }

    const wasUnderrun = this._pmWasUnderrun || false;
    if (available < out.length) this.underruns++;
    this._pmWasUnderrun = (available < out.length);
    if (wasUnderrun && available >= out.length && available > TARGET * 2) {
      this.rPos = this.wPos - TARGET;
    }
    let fadeIn = wasUnderrun && available >= out.length ? 64 : 0;

    for (let i = 0; i < out.length; i++) {
      const ri = Math.floor(this.rPos);
      if (ri < this.wPos) {
        const frac = this.rPos - ri;
        const s0 = this.ring[ri % len];
        const s1 = (ri + 1 < this.wPos) ? this.ring[(ri + 1) % len] : s0;
        let sample = s0 + frac * (s1 - s0);
        if (fadeIn > 0) {
          sample *= (64 - fadeIn) / 64;
          fadeIn--;
        }
        out[i] = sample;
        this.rPos += step;
      } else {
        out[i] = i > 0 ? out[i-1] * 0.99 : 0;
      }
    }
  }
}
registerProcessor('audio-ring-processor', AudioRingProcessor);
`;

// Keep AudioContext alive
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

  // Setup SharedArrayBuffer if available
  if (_useSAB) {
    try {
      var ctrlBuf = new SharedArrayBuffer(16); // 4 x Int32
      var ringBuf = new SharedArrayBuffer(_RING_SIZE * 4); // Float32
      _sharedCtrl = new Int32Array(ctrlBuf);
      _sharedRing = new Float32Array(ringBuf);
      _sharedCtrl[0] = 0; // wPos
      console.log('[audio] SharedArrayBuffer ring allocated (' + (_RING_SIZE * 4 / 1024) + 'KB)');
    } catch(e) {
      console.warn('[audio] SAB alloc failed, falling back to postMessage:', e.message);
      _useSAB = false;
      _sharedBuf = null;
      _sharedRing = null;
      _sharedCtrl = null;
    }
  }

  function _setupWorkletNode() {
    _audioWorkletNode = new AudioWorkletNode(_audioCtx, 'audio-ring-processor');
    _audioWorkletNode.connect(_audioGain);
    _audioWorkletNode.port.onmessage = function(e) {
      if (e.data && e.data.type === 'stats') {
        _audioStats.underruns += e.data.underruns;
        _audioStats.ringFill = e.data.fill;
        if (e.data.sampleRate) _audioStats.hwRate = e.data.sampleRate;
        if (e.data.baseStep) _audioStats.baseStep = e.data.baseStep;
        if (e.data.curStep) _audioStats.curStep = e.data.curStep;
      }
    };
    // Send SAB references to worklet
    if (_useSAB && _sharedCtrl && _sharedRing) {
      _audioWorkletNode.port.postMessage({
        type: 'init-sab',
        ctrl: _sharedCtrl.buffer,
        ring: _sharedRing.buffer,
      });
      console.log('[audio] SAB mode active — zero-copy ring buffer');
    }
    _audioWorkletReady = true;
    console.log('[audio] worklet ready' + (_useSAB ? ' (SAB)' : ' (postMessage fallback)'));
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
        ' dec_queue=' + (_audioDecoder ? _audioDecoder.decodeQueueSize : 'N/A') +
        ' mode=' + (_useSAB ? 'SAB' : 'postMsg') +
        ' hw_rate=' + (_audioStats.hwRate || '?') +
        ' baseStep=' + (_audioStats.baseStep || '?') +
        ' curStep=' + ((_audioStats.curStep || 0).toFixed(5)));
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
    if (_audioCtx && _audioCtx.state === 'suspended') _audioCtx.resume();
    if (!_audioDecoder) _createDecoder();
    if (!_audioDecoder || _audioDecoder.state !== 'configured') return;

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

  // Pre-buffering phase
  if (!_preBufferDone) {
    _preBuffer.push(samples);
    _preBufferSamples += samples.length;
    if (_preBufferSamples >= _PRE_BUFFER_TARGET) {
      _preBufferDone = true;
      for (var i = 0; i < _preBuffer.length; i++) {
        _pushSamples(_preBuffer[i]);
      }
      _preBuffer = [];
      console.log('[audio] pre-buffer filled (' + _preBufferSamples + ' samples)');
    }
    return;
  }

  _pushSamples(samples);
}

function _pushSamples(samples) {
  if (!_audioWorkletReady || !_audioWorkletNode) return;

  if (_useSAB && _sharedRing && _sharedCtrl) {
    // SAB path: write directly to shared ring buffer (zero-copy to worklet)
    var wPos = Atomics.load(_sharedCtrl, 0);
    var len = _sharedRing.length;
    for (var i = 0; i < samples.length; i++) {
      _sharedRing[(wPos + i) % len] = samples[i];
    }
    Atomics.store(_sharedCtrl, 0, wPos + samples.length);
    _audioStats.batchesSent++;
  } else {
    // postMessage fallback
    _addToBatch(samples);
  }
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
  _sharedRing = null;
  _sharedCtrl = null;
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
