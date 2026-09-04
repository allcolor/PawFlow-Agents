// Shared scheduling and animation policy for the WebChat.
//
// This is deliberately a small classic-script utility rather than a component
// framework. Consumers enqueue geometry reads before writes and replace named
// animation channels instead of stacking work.
(function(root) {
  'use strict';

  if (!root || root.pfMotion) return;

  const media = typeof root.matchMedia === 'function'
    ? root.matchMedia('(prefers-reduced-motion: reduce)') : null;
  let reducedMotion = !!(media && media.matches);
  const readQueue = [];
  const writeQueue = [];
  const channels = new WeakMap();
  const surfaceActivity = new WeakMap();
  const diagnostics = {
    activeAnimations: 0,
    scheduledFrames: 0,
    completedAnimations: 0,
    cancelledAnimations: 0,
  };
  let frameId = 0;
  let flushing = false;
  let animationGeneration = 0;

  function _diagnosticsEnabled() {
    return root.__PF_MOTION_DIAGNOSTICS__ === true;
  }

  function _requestFrame(callback) {
    if (typeof root.requestAnimationFrame === 'function') {
      return root.requestAnimationFrame(callback);
    }
    return root.setTimeout(function() { callback(Date.now()); }, 0);
  }

  function _cancelFrame(id) {
    if (typeof root.cancelAnimationFrame === 'function') {
      root.cancelAnimationFrame(id);
    } else if (typeof root.clearTimeout === 'function') {
      root.clearTimeout(id);
    }
  }

  function _schedule() {
    if (frameId || flushing) return;
    // The sentinel also makes deterministic synchronous requestAnimationFrame
    // stubs safe: a callback may clear frameId before the call returns.
    frameId = -1;
    const requested = _requestFrame(_flush);
    if (frameId) frameId = requested || -1;
    if (_diagnosticsEnabled()) diagnostics.scheduledFrames += 1;
  }

  function _runBatch(queue) {
    const batch = queue.splice(0);
    batch.forEach(function(job) {
      if (job.done) return;
      if (job.signal && job.signal.aborted) {
        job.done = true;
        job.resolve(undefined);
        return;
      }
      job.done = true;
      if (job.signal && job.abort) {
        job.signal.removeEventListener('abort', job.abort);
      }
      try {
        job.resolve(job.callback());
      } catch (error) {
        job.reject(error);
      }
    });
  }

  function _flush() {
    frameId = 0;
    flushing = true;
    _runBatch(readQueue);
    _runBatch(writeQueue);
    flushing = false;
    if (readQueue.length || writeQueue.length) _schedule();
  }

  function _enqueue(queue, callback, signal) {
    if (typeof callback !== 'function') {
      return Promise.reject(new TypeError('pfMotion callback must be a function'));
    }
    if (signal && signal.aborted) return Promise.resolve(undefined);
    return new Promise(function(resolve, reject) {
      const job = {callback: callback, signal: signal || null, resolve: resolve,
        reject: reject, abort: null, done: false};
      if (signal) {
        job.abort = function() {
          if (job.done) return;
          job.done = true;
          resolve(undefined);
        };
        signal.addEventListener('abort', job.abort, {once: true});
      }
      queue.push(job);
      _schedule();
    });
  }

  function read(callback, signal) {
    return _enqueue(readQueue, callback, signal);
  }

  function write(callback, signal) {
    return _enqueue(writeQueue, callback, signal);
  }

  function _channelMap(element) {
    let map = channels.get(element);
    if (!map) {
      map = new Map();
      channels.set(element, map);
    }
    return map;
  }

  function _settle(record, status) {
    if (record.settled) return;
    record.settled = true;
    if (record.signal && record.abort) {
      record.signal.removeEventListener('abort', record.abort);
    }
    const map = channels.get(record.element);
    if (map && map.get(record.channel) === record) {
      map.delete(record.channel);
      if (!map.size) channels.delete(record.element);
    }
    if (record.counted) {
      diagnostics.activeAnimations = Math.max(0, diagnostics.activeAnimations - 1);
      record.counted = false;
    }
    if (_diagnosticsEnabled()) {
      if (status === 'finished') diagnostics.completedAnimations += 1;
      else diagnostics.cancelledAnimations += 1;
    }
    record.resolve({
      status: status,
      generation: record.generation,
      animation: record.animation || null,
    });
  }

  function _cancel(record, status) {
    if (!record || record.settled) return;
    if (record.animation && typeof record.animation.cancel === 'function') {
      try { record.animation.cancel(); } catch (_error) {}
    }
    _settle(record, status || 'cancelled');
  }

  function replace(element, channel, keyframes, options, signal) {
    if (!element) {
      return Promise.reject(new TypeError('pfMotion.replace requires an element'));
    }
    channel = String(channel || '');
    if (!channel) {
      return Promise.reject(new TypeError('pfMotion.replace requires a channel'));
    }
    const map = _channelMap(element);
    _cancel(map.get(channel), 'replaced');

    let resolveFinished;
    const record = {
      element: element,
      channel: channel,
      generation: ++animationGeneration,
      signal: signal || null,
      animation: null,
      abort: null,
      counted: false,
      settled: false,
      resolve: null,
      finished: null,
    };
    record.finished = new Promise(function(resolve) { resolveFinished = resolve; });
    record.resolve = resolveFinished;
    map.set(channel, record);

    if (signal && signal.aborted) {
      _settle(record, 'aborted');
      return record.finished;
    }
    if (signal) {
      record.abort = function() { _cancel(record, 'aborted'); };
      signal.addEventListener('abort', record.abort, {once: true});
    }

    const animationOptions = Object.assign({}, options || {});
    const duration = Number(animationOptions.duration || 0);
    if (reducedMotion || duration <= 0 || typeof element.animate !== 'function') {
      Promise.resolve().then(function() { _settle(record, 'finished'); });
      return record.finished;
    }

    try {
      record.animation = element.animate(keyframes, animationOptions);
      record.counted = true;
      diagnostics.activeAnimations += 1;
      Promise.resolve(record.animation.finished).then(
        function() { _settle(record, 'finished'); },
        function() { _settle(record, 'cancelled'); }
      );
    } catch (_error) {
      _settle(record, 'cancelled');
    }
    return record.finished;
  }

  function whenSettled(element, channel) {
    const map = element && channels.get(element);
    const record = map && map.get(String(channel || ''));
    return record ? record.finished : Promise.resolve({status: 'idle', generation: 0});
  }

  function cancel(element, channel) {
    const map = element && channels.get(element);
    _cancel(map && map.get(String(channel || '')), 'cancelled');
  }

  function _flipTransform(delta, scale) {
    const translated = 'translate(' + delta.dx + 'px,' + delta.dy + 'px)';
    return scale ? translated + ' scale(' + delta.sx + ',' + delta.sy + ')'
      : translated;
  }

  function flipGroup(elements, mutate, options, signal) {
    if (!elements || typeof mutate !== 'function') {
      return Promise.reject(new TypeError('pfMotion.flipGroup requires elements and mutate callback'));
    }
    options = options || {};
    const list = Array.from(elements).filter(function(element) {
      return element && typeof element.getBoundingClientRect === 'function';
    });
    if (!list.length) return Promise.resolve({status: 'idle', results: []});

    const channel = options.channel || 'layout';
    let first = null;
    let mutated;
    try {
      first = list.map(function(element) { return element.getBoundingClientRect(); });
      list.forEach(function(element) { cancel(element, channel); });
      mutated = mutate();
    } catch (error) {
      return Promise.reject(error);
    }
    if (mutated === false) return Promise.resolve({status: 'stale', results: []});

    let deltas = null;
    const measured = read(function() {
      deltas = list.map(function(element, index) {
        const before = first[index];
        const after = element.getBoundingClientRect();
        return {
          element: element,
          dx: Number(before.left || 0) - Number(after.left || 0),
          dy: Number(before.top || 0) - Number(after.top || 0),
          sx: options.scale && Number(after.width || 0) > 0
            ? Number(before.width || 0) / Number(after.width || 0) : 1,
          sy: options.scale && Number(after.height || 0) > 0
            ? Number(before.height || 0) / Number(after.height || 0) : 1,
        };
      });
      return deltas;
    }, signal);
    const animated = write(function() {
      if (!deltas) return {status: 'stale', results: []};
      const animations = deltas.map(function(delta) {
        const moved = Math.abs(delta.dx) >= 0.5 || Math.abs(delta.dy) >= 0.5;
        const resized = options.scale
          && (Math.abs(delta.sx - 1) >= 0.005 || Math.abs(delta.sy - 1) >= 0.005);
        if (!moved && !resized) return Promise.resolve({status: 'idle'});
        return replace(delta.element, channel, [
          {transform: _flipTransform(delta, !!options.scale), transformOrigin: '0 0'},
          {transform: options.scale ? 'translate(0,0) scale(1,1)' : 'translate(0,0)',
            transformOrigin: '0 0'},
        ], {
          duration: Number(options.duration === undefined ? 180 : options.duration),
          easing: options.easing || 'cubic-bezier(.2, .8, .2, 1)',
        }, signal);
      });
      return Promise.all(animations).then(function(results) {
        return {status: 'finished', results: results};
      });
    }, signal);
    return Promise.all([measured, animated]).then(function(results) {
      return results[1] || {status: 'stale', results: []};
    });
  }

  function flip(element, mutate, options, signal) {
    if (!element) {
      return Promise.reject(new TypeError('pfMotion.flip requires an element and mutate callback'));
    }
    return flipGroup([element], mutate, options, signal).then(function(group) {
      return group.results && group.results.length ? group.results[0] : {status: group.status};
    });
  }

  function reduced() {
    return reducedMotion;
  }

  function setSurfaceActive(owner, active) {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) return;
    surfaceActivity.set(owner, !!active);
  }

  function surfaceActive(owner) {
    return !owner || surfaceActivity.get(owner) !== false;
  }

  function snapshot() {
    if (!_diagnosticsEnabled()) return null;
    return Object.assign({
      queuedReads: readQueue.filter(function(job) { return !job.done; }).length,
      queuedWrites: writeQueue.filter(function(job) { return !job.done; }).length,
      framePending: !!frameId,
      reducedMotion: reducedMotion,
    }, diagnostics);
  }

  function destroy() {
    if (frameId) _cancelFrame(frameId);
    frameId = 0;
    readQueue.splice(0).forEach(function(job) {
      if (!job.done) { job.done = true; job.resolve(undefined); }
    });
    writeQueue.splice(0).forEach(function(job) {
      if (!job.done) { job.done = true; job.resolve(undefined); }
    });
    if (media) {
      if (typeof media.removeEventListener === 'function') {
        media.removeEventListener('change', _mediaChanged);
      } else if (typeof media.removeListener === 'function') {
        media.removeListener(_mediaChanged);
      }
    }
  }

  function _mediaChanged(event) {
    reducedMotion = !!event.matches;
  }

  if (media) {
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', _mediaChanged);
    } else if (typeof media.addListener === 'function') {
      media.addListener(_mediaChanged);
    }
  }

  root.pfMotion = {
    reduced: reduced,
    read: read,
    write: write,
    replace: replace,
    whenSettled: whenSettled,
    cancel: cancel,
    flip: flip,
    flipGroup: flipGroup,
    setSurfaceActive: setSurfaceActive,
    surfaceActive: surfaceActive,
    diagnostics: snapshot,
    destroy: destroy,
  };
})(typeof window !== 'undefined' ? window : globalThis);
