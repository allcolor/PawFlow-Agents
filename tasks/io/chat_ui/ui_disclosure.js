// Interruptible, accessible disclosure controller built on pfMotion.
(function(root) {
  'use strict';

  if (!root || root.pfDisclosure) return;

  let disclosureSequence = 0;

  function _contains(parent, child) {
    let node = child;
    while (node) {
      if (node === parent) return true;
      node = node.parentNode;
    }
    return false;
  }

  function _setInert(element, inert) {
    if ('inert' in element) element.inert = !!inert;
    if (inert) element.setAttribute('inert', '');
    else element.removeAttribute('inert');
  }

  function _height(element) {
    if (element && typeof element.getBoundingClientRect === 'function') {
      const rect = element.getBoundingClientRect();
      if (rect && Number(rect.height) > 0) return Number(rect.height);
    }
    return Number((element && (element.offsetHeight || element.scrollHeight)) || 0);
  }

  function _captureInlineStyles(element, names) {
    return names.map(function(name) {
      const property = name.replace(/-([a-z])/g, function(_match, letter) {
        return letter.toUpperCase();
      });
      const cssom = typeof element.style.getPropertyValue === 'function';
      return {
        name: name,
        property: property,
        value: cssom ? element.style.getPropertyValue(name) : (element.style[property] || ''),
        priority: cssom && typeof element.style.getPropertyPriority === 'function'
          ? element.style.getPropertyPriority(name) : '',
      };
    });
  }

  function _restoreInlineStyles(element, snapshot) {
    const cssom = typeof element.style.removeProperty === 'function';
    snapshot.forEach(function(entry) {
      if (cssom) element.style.removeProperty(entry.name);
      else delete element.style[entry.property];
    });
    snapshot.forEach(function(entry) {
      if (entry.value) {
        if (typeof element.style.setProperty === 'function') {
          element.style.setProperty(entry.name, entry.value, entry.priority);
        } else {
          element.style[entry.property] = entry.value;
        }
      }
    });
  }

  function create(options) {
    options = options || {};
    const trigger = options.trigger;
    const panel = options.panel;
    if (!trigger || !panel) {
      throw new TypeError('pfDisclosure.create requires trigger and panel');
    }
    if (!root.pfMotion) {
      throw new Error('pfDisclosure requires ui_motion.js to load first');
    }

    const owner = new root.AbortController();
    const signal = owner.signal;
    const externalSignal = options.signal || null;
    const duration = Number(options.duration === undefined ? 300 : options.duration);
    const easing = options.easing || 'cubic-bezier(.2, .9, .25, 1)';
    let generation = 0;
    let targetOpen = !!options.open;
    let state = targetOpen ? 'open' : 'closed';
    let transitionPromise = Promise.resolve({status: state});
    let resizeQueued = false;
    let openingTargetHeight = 0;
    let destroyed = false;
    const animatedInlineStyles = _captureInlineStyles(panel, [
      'height', 'opacity', 'overflow', 'overflow-x', 'overflow-y',
    ]);

    function _restoreAnimatedInlineStyles() {
      _restoreInlineStyles(panel, animatedInlineStyles);
    }

    panel.classList.add('pf-disclosure-panel');
    if (!panel.id) panel.id = 'pf-disclosure-' + (++disclosureSequence);
    trigger.setAttribute('aria-controls', panel.id);

    function _setState(next) {
      state = next;
      panel.dataset.pfDisclosureState = next;
    }

    function _terminal(open, currentGeneration) {
      if (destroyed || currentGeneration !== generation || open !== targetOpen) {
        return {status: 'stale'};
      }
      _restoreAnimatedInlineStyles();
      openingTargetHeight = 0;
      if (open) {
        panel.hidden = false;
        panel.removeAttribute('aria-hidden');
        _setInert(panel, false);
        _setState('open');
        if (typeof options.onAfterOpen === 'function') options.onAfterOpen();
      } else {
        panel.hidden = true;
        panel.setAttribute('aria-hidden', 'true');
        _setInert(panel, true);
        _setState('closed');
        if (typeof options.onAfterClose === 'function') options.onAfterClose();
      }
      return {status: state};
    }

    function _transition(open, currentGeneration) {
      return root.pfMotion.read(function() {
        if (destroyed || currentGeneration !== generation || open !== targetOpen) return null;
        const current = open && panel.style.height === '0px' ? 0 : _height(panel);
        const natural = Number(panel.scrollHeight || current);
        if (open) openingTargetHeight = natural;
        return {
          start: current,
          end: open ? natural : 0,
        };
      }, signal).then(function(measurement) {
        if (!measurement || destroyed || currentGeneration !== generation
            || open !== targetOpen) return {status: 'stale'};
        return root.pfMotion.write(function() {
          if (destroyed || currentGeneration !== generation || open !== targetOpen) {
            return {status: 'stale'};
          }
          panel.style.overflow = 'clip';
          panel.style.height = measurement.start + 'px';
          const keyframes = open
            ? [
                {height: measurement.start + 'px', opacity: measurement.start ? 1 : 0},
                {height: measurement.end + 'px', opacity: 1},
              ]
            : [
                {height: measurement.start + 'px', opacity: 1},
                {height: '0px', opacity: 0},
              ];
          return root.pfMotion.replace(panel, 'disclosure', keyframes, {
            duration: duration,
            easing: open ? easing : 'cubic-bezier(.4, 0, 1, 1)',
            fill: 'both',
          }, signal);
        }, signal);
      }).then(function(result) {
        if (result && result.status !== 'finished' && result.status !== 'idle') return result;
        return _terminal(open, currentGeneration);
      });
    }

    function set(open) {
      open = !!open;
      if (destroyed) return Promise.resolve({status: 'destroyed'});
      if (open === targetOpen && ((open && state === 'open') || (!open && state === 'closed'))) {
        return Promise.resolve({status: state});
      }
      const openingFromClosed = open && state === 'closed';
      targetOpen = open;
      const currentGeneration = ++generation;
      trigger.setAttribute('aria-expanded', open ? 'true' : 'false');

      if (open) {
        if (openingFromClosed) {
          panel.style.height = '0px';
          panel.style.opacity = '0';
          panel.style.overflow = 'clip';
        }
        panel.hidden = false;
        panel.removeAttribute('aria-hidden');
        _setInert(panel, false);
        _setState('opening');
      } else {
        if (_contains(panel, root.document && root.document.activeElement)
            && typeof trigger.focus === 'function') {
          trigger.focus();
        }
        panel.setAttribute('aria-hidden', 'true');
        _setInert(panel, true);
        _setState('closing');
      }
      if (root.pfMotion.reduced()) {
        root.pfMotion.cancel(panel, 'disclosure');
        transitionPromise = Promise.resolve(_terminal(open, currentGeneration));
        return transitionPromise;
      }
      transitionPromise = _transition(open, currentGeneration);
      return transitionPromise;
    }

    function toggle() {
      return set(!targetOpen);
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      generation += 1;
      if (observer) observer.disconnect();
      root.pfMotion.cancel(panel, 'disclosure');
      owner.abort();
      _restoreAnimatedInlineStyles();
      panel.classList.remove('pf-disclosure-panel');
      delete panel.dataset.pfDisclosureState;
    }

    const observer = typeof root.ResizeObserver === 'function'
      ? new root.ResizeObserver(function() {
          if (destroyed || state !== 'opening' || !targetOpen || resizeQueued) return;
          resizeQueued = true;
          root.pfMotion.read(function() {
            resizeQueued = false;
            const natural = Number(panel.scrollHeight || 0);
            if (!destroyed && state === 'opening' && targetOpen
                && Math.abs(natural - openingTargetHeight) >= 1) {
              transitionPromise = _transition(true, generation);
            }
          }, signal);
        })
      : null;
    if (observer) observer.observe(panel);

    if (externalSignal) {
      if (externalSignal.aborted) destroy();
      else externalSignal.addEventListener('abort', destroy, {once: true});
    }

    trigger.setAttribute('aria-expanded', targetOpen ? 'true' : 'false');
    if (targetOpen) {
      panel.hidden = false;
      panel.removeAttribute('aria-hidden');
      _setInert(panel, false);
    } else {
      panel.hidden = true;
      panel.setAttribute('aria-hidden', 'true');
      _setInert(panel, true);
    }
    _setState(state);

    return {
      set: set,
      toggle: toggle,
      destroy: destroy,
      settled: function() { return transitionPromise; },
      state: function() { return state; },
      targetOpen: function() { return targetOpen; },
      signal: signal,
    };
  }

  root.pfDisclosure = {create: create};
})(typeof window !== 'undefined' ? window : globalThis);
