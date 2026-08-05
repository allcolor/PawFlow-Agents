'use strict';

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

function tick() {
  return new Promise(resolve => setTimeout(resolve, 5));
}

async function main() {
  global.Node = function Node() {};
  global.document = {
    readyState: 'complete',
    documentElement: { lang: 'en' },
    body: { appendChild() {} },
    head: { appendChild() {} },
    addEventListener() {},
    querySelector() { return null; },
    getElementById() { return null; },
    createElement() {
      return {
        addEventListener() {},
        appendChild() {},
        remove() {},
        setAttribute() {},
        style: {},
      };
    },
  };
  global.window = {
    PAWFLOW_EXTENSIONS: [{
      package: 'test.media',
      assets: [],
      slots: [],
      hooks: [
        'media_track_subscribed',
        'media_audio_frame',
        'realtime_state_changed',
        'media_track_unsubscribed',
        'theme_changed',
      ],
    }, {
      package: 'test.undeclared',
      assets: [],
      slots: [],
      hooks: ['theme_changed'],
    }],
    PAWFLOW_EXTENSION_CONTEXT: {
      user: 'u1', conversation: 'c1',
    },
    addEventListener() {},
  };

  const source = fs.readFileSync(
    'tasks/io/chat_ui/ext_runtime.js', 'utf8');
  vm.runInThisContext(source, { filename: 'ext_runtime.js' });

  let pfp = null;
  assert.strictEqual(window.pawflow.register('test.media', function(api) {
    pfp = api;
  }), true);
  await tick();
  assert.ok(pfp, 'registration callback did not run');

  let undeclaredPfp = null;
  assert.strictEqual(window.pawflow.register(
    'test.undeclared',
    function(api) { undeclaredPfp = api; }), true);
  await tick();
  assert.ok(undeclaredPfp, 'undeclared-hook package did not register');
  assert.strictEqual(
    undeclaredPfp.on('media_audio_frame', function() {}), false,
    'a known but undeclared media hook was accepted');
  assert.strictEqual(typeof undeclaredPfp.on(
    'theme_changed', function() {}), 'function');

  const subscribed = [];
  const frames = [];
  const states = [];
  const detached = [];
  pfp.on('media_track_subscribed', event => subscribed.push(event));
  pfp.on('media_audio_frame', event => frames.push(event));
  pfp.on('realtime_state_changed', event => states.push(event));
  pfp.on('media_track_unsubscribed', event => detached.push(event));

  const runtime = window._pawflowExtRuntime;
  const track = { sid: 'track-1' };
  const element = { id: 'audio-1' };
  const descriptor = runtime.mediaTrackSubscribed({
    id: 'livekit:track-1',
    transport: 'livekit',
    kind: 'audio',
    conversation: 'c1',
    track: track,
    element: element,
  });
  assert.ok(Object.isFrozen(descriptor));
  await tick();
  assert.strictEqual(subscribed.length, 1);
  assert.strictEqual(subscribed[0].track, track);
  assert.strictEqual(subscribed[0].element, element);

  const samples = new Float32Array([0.25, -0.5]);
  assert.strictEqual(runtime.mediaAudioFrame('livekit:track-1', {
    format: 'f32',
    sample_rate: 24000,
    channels: 1,
    samples: samples,
  }), true);
  samples[0] = 0.9;
  runtime.mediaStateChanged({
    state: 'speaking', transport: 'livekit', conversation: 'c1',
  });
  await tick();
  assert.strictEqual(frames.length, 1);
  assert.ok(Object.isFrozen(frames[0]));
  assert.ok(Object.isFrozen(frames[0].source));
  assert.notStrictEqual(frames[0].samples, samples);
  assert.strictEqual(frames[0].samples[0], 0.25);
  assert.strictEqual(states.length, 1);
  assert.ok(Object.isFrozen(states[0]));

  assert.strictEqual(
    runtime.mediaTrackUnsubscribed('livekit:track-1', 'track_unsubscribed'),
    true);
  await tick();
  assert.strictEqual(detached.length, 1);
  assert.strictEqual(detached[0].reason, 'track_unsubscribed');
  assert.ok(Object.isFrozen(detached[0]));
  assert.strictEqual(
    runtime.mediaAudioFrame('livekit:track-1', {
      samples: new Float32Array([1]),
    }),
    false);

  runtime.mediaTrackSubscribed({
    id: 'pcm:agent',
    transport: 'pcm',
    kind: 'audio',
    conversation: 'c1',
  });
  await tick();
  const beforeReset = detached.length;
  runtime.resetMedia('conversation_changed');
  await tick();
  assert.strictEqual(detached.length, beforeReset + 1);
  assert.strictEqual(detached[detached.length - 1].reason,
                     'conversation_changed');

  global.conversationId = 'c1';
  global.selectedAgent = 'assistant';
  class FakeAudioContext {
    constructor() {
      this.currentTime = 0;
      this.destination = {};
    }
    createBuffer(_channels, length, rate) {
      const channel = new Float32Array(length);
      return {
        duration: length / rate,
        getChannelData() { return channel; },
      };
    }
    createBufferSource() {
      return { connect() {}, start() {}, stop() {}, onended: null };
    }
    close() {}
  }
  window.AudioContext = FakeAudioContext;
  vm.runInThisContext(fs.readFileSync(
    'tasks/io/chat_ui/conversation_voice.js', 'utf8'), {
    filename: 'conversation_voice.js',
  });
  const pcmSubscribedBefore = subscribed.length;
  const pcmFramesBefore = frames.length;
  _voiceActive = true;
  _voiceMediaAttach();
  _voicePlayChunk(new Int16Array([8192, -16384]).buffer);
  await tick();
  assert.strictEqual(subscribed.length, pcmSubscribedBefore + 1);
  assert.strictEqual(subscribed[subscribed.length - 1].transport, 'pcm');
  assert.strictEqual(frames.length, pcmFramesBefore + 1);
  assert.strictEqual(frames[frames.length - 1].source.transport, 'pcm');
  assert.strictEqual(frames[frames.length - 1].samples[0], 0.25);
  _voiceMediaDetach('adapter_test');
  _voiceActive = false;
  await tick();

  vm.runInThisContext(fs.readFileSync(
    'tasks/io/chat_ui/conversation_livekit.js', 'utf8'), {
    filename: 'conversation_livekit.js',
  });
  const livekitSubscribedBefore = subscribed.length;
  const livekitDetachedBefore = detached.length;
  const livekitElement = {
    autoplay: false,
    style: {},
    remove() { this.removed = true; },
  };
  const livekitTrack = {
    kind: 'audio',
    attach() { return livekitElement; },
    detach(element_) { assert.strictEqual(element_, livekitElement); },
  };
  _lkSession = { session_id: 'session-1' };
  _lkAttachAudioTrack(
    livekitTrack, { trackSid: 'remote-1' }, { identity: 'agent-1' });
  await tick();
  assert.strictEqual(subscribed.length, livekitSubscribedBefore + 1);
  assert.strictEqual(subscribed[subscribed.length - 1].transport, 'livekit');
  assert.strictEqual(subscribed[subscribed.length - 1].track, livekitTrack);
  assert.strictEqual(subscribed[subscribed.length - 1].element,
                     livekitElement);
  _lkDetachAllAudio('adapter_test');
  await tick();
  assert.strictEqual(detached.length, livekitDetachedBefore + 1);
  assert.strictEqual(livekitElement.removed, true);

  const lateFrames = [];
  pfp.on('media_audio_frame', event => lateFrames.push(event));
  runtime.mediaTrackSubscribed({
    id: 'pcm:late',
    transport: 'pcm',
    kind: 'audio',
    conversation: 'c1',
  });
  runtime.mediaAudioFrame('pcm:late', {
    samples: new Float32Array([0.75]),
    sample_rate: 24000,
    channels: 1,
  });
  assert.strictEqual(window.pawflow.unregister('test.media'), true);
  await tick();
  assert.strictEqual(lateFrames.length, 0,
    'disabled extension received a queued media callback');

  console.log('pfp media runtime spec passed');
}

main().catch(err => {
  console.error(err);
  process.exitCode = 1;
});
