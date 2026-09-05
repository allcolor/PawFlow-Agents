// Shared lifecycle and viewport placement for WebChat tooltips, menus and popovers.
//
// Every channel owns at most one surface. Opening a replacement tears down the
// previous listeners before the next surface is mounted, so local menu owners
// do not need delayed document listeners or their own clamp logic.
(function(root) {
  'use strict';

  if (!root || root.pfFloatingLayer) return;

  const doc = root.document;
  const layers = new Map();
  const counters = {
    opened: 0,
    closed: 0,
    listeners: 0,
  };

  function _diagnosticsEnabled() {
    return root.__PF_FLOATING_DIAGNOSTICS__ === true;
  }

  function _listen(record, target, type, listener, options) {
    if (!target || typeof target.addEventListener !== 'function') return;
    target.addEventListener(type, listener, options);
    record.listeners.push({target: target, type: type, listener: listener, options: options});
    counters.listeners += 1;
  }

  function _removeListeners(record) {
    record.listeners.forEach(function(binding) {
      binding.target.removeEventListener(
        binding.type, binding.listener, binding.options
      );
      counters.listeners = Math.max(0, counters.listeners - 1);
    });
    record.listeners.length = 0;
  }

  function _contains(element, node) {
    return !!(element && node && (
      element === node
      || (typeof element.contains === 'function' && element.contains(node))
    ));
  }

  function _focusTrigger(record) {
    if (!record.restoreFocus || !record.trigger
        || typeof record.trigger.focus !== 'function') return;
    try { record.trigger.focus({preventScroll: true}); } catch (_error) {
      try { record.trigger.focus(); } catch (_ignored) {}
    }
  }

  function _focusInitial(record) {
    const target = record.options.initialFocus;
    if (!target || typeof target.focus !== 'function') return;
    try { target.focus({preventScroll: true}); } catch (_error) {
      try { target.focus(); } catch (_ignored) {}
    }
  }

  function _modalFocusables(record) {
    const scope = record.motionElement || record.element;
    if (!scope || typeof scope.querySelectorAll !== 'function') return [];
    return Array.from(scope.querySelectorAll(
      'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'
    )).filter(function(element) {
      return !element.hasAttribute('disabled') && !element.hasAttribute('inert')
        && element.getAttribute('aria-hidden') !== 'true';
    });
  }

  function _trapModalFocus(record, event) {
    const focusable = _modalFocusables(record);
    if (!focusable.length) {
      if (typeof event.preventDefault === 'function') event.preventDefault();
      _focusInitial(record);
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = doc.activeElement;
    const scope = record.motionElement || record.element;
    const outside = !_contains(scope, active);
    const fromInitial = active === record.options.initialFocus;
    let target = null;
    if (event.shiftKey && (active === first || outside || fromInitial)) target = last;
    else if (!event.shiftKey && (active === last || outside || fromInitial)) target = first;
    if (!target) return;
    if (typeof event.preventDefault === 'function') event.preventDefault();
    target.focus();
  }

  function _closeRecord(record, options) {
    if (!record) return Promise.resolve(false);
    if (record.closed) return record.closing || Promise.resolve(false);
    options = options || {};
    record.closed = true;
    if (layers.get(record.channel) === record) layers.delete(record.channel);
    _removeListeners(record);
    if (record.controller) record.controller.abort();
    if (root.pfMotion && typeof root.pfMotion.cancel === 'function') {
      root.pfMotion.cancel(record.motionElement, 'floating-layer');
    }

    if (record.element) {
      record.element.setAttribute('inert', '');
      record.element.style.pointerEvents = 'none';
    }
    counters.closed += 1;
    const motion = root.pfMotion;
    const exit = record.options.animate === false || !motion
        || typeof motion.replace !== 'function'
      ? Promise.resolve({status: 'immediate'})
      : motion.replace(record.motionElement, 'floating-layer', [
        {opacity: 1, transform: 'translateY(0) scale(1)'},
        {opacity: 0, transform: 'translateY(2px) scale(.98)'},
      ], {
        duration: Number(record.options.exitDuration === undefined
          ? 90 : record.options.exitDuration),
        easing: 'cubic-bezier(.4, 0, 1, 1)',
      });
    record.closing = Promise.resolve(exit).catch(function() {
      return {status: 'cancelled'};
    }).then(function() {
      const reused = Array.from(layers.values()).some(function(active) {
        return active.element === record.element;
      });
      if (!reused) {
        if (record.removeOnClose) {
          if (record.element && typeof record.element.remove === 'function') {
            record.element.remove();
          }
        } else if (record.element) {
          record.element.setAttribute('aria-hidden', 'true');
        }
        if (typeof record.onClose === 'function') {
          record.onClose(options.reason || 'close');
        }
        if (options.restoreFocus !== false) _focusTrigger(record);
      }
      return true;
    });
    return record.closing;
  }

  function close(channel, options) {
    return _closeRecord(layers.get(String(channel || 'floating')), options);
  }

  function closeAll(options) {
    Array.from(layers.values()).forEach(function(record) {
      _closeRecord(record, options);
    });
  }

  function _viewport() {
    const element = doc && doc.documentElement;
    return {
      width: Number(root.innerWidth || (element && element.clientWidth) || 0),
      height: Number(root.innerHeight || (element && element.clientHeight) || 0),
    };
  }

  function _coordinates(record) {
    const options = record.options;
    const elementRect = record.element.getBoundingClientRect();
    const viewport = _viewport();
    const edge = Number(options.edge === undefined ? 8 : options.edge);
    const gap = Number(options.gap === undefined ? 10 : options.gap);
    const point = options.point;
    const triggerRect = record.trigger
      && typeof record.trigger.getBoundingClientRect === 'function'
      ? record.trigger.getBoundingClientRect() : null;
    const width = Number(elementRect.width || 0);
    const height = Number(elementRect.height || 0);
    let left = edge;
    let top = edge;
    let origin = 'top left';

    if (point) {
      const x = Number(point.x || 0);
      const y = Number(point.y || 0);
      left = x;
      top = y;
      if (left + width > viewport.width - edge) {
        left = x - width;
        origin = 'top right';
      }
      if (top + height > viewport.height - edge) {
        top = y - height;
        origin = origin === 'top right' ? 'bottom right' : 'bottom left';
      }
    } else if (triggerRect) {
      const placement = String(options.placement || 'bottom');
      if (placement === 'left') {
        origin = 'right center';
        left = Number(triggerRect.left || 0) - width - gap;
        top = Number(triggerRect.top || 0)
          + (Number(triggerRect.height || 0) - height) / 2;
        if (left < edge) {
          left = Number(triggerRect.right || 0) + gap;
          origin = 'left center';
        }
      } else if (placement === 'right') {
        origin = 'left center';
        left = Number(triggerRect.right || 0) + gap;
        top = Number(triggerRect.top || 0)
          + (Number(triggerRect.height || 0) - height) / 2;
        if (left + width > viewport.width - edge) {
          left = Number(triggerRect.left || 0) - width - gap;
          origin = 'right center';
        }
      } else if (placement === 'top') {
        origin = 'bottom center';
        left = Number(triggerRect.left || 0)
          + (Number(triggerRect.width || 0) - width) / 2;
        top = Number(triggerRect.top || 0) - height - gap;
        if (top < edge) {
          top = Number(triggerRect.bottom || 0) + gap;
          origin = 'top center';
        }
      } else {
        origin = 'top center';
        left = Number(triggerRect.left || 0)
          + (Number(triggerRect.width || 0) - width) / 2;
        top = Number(triggerRect.bottom || 0) + gap;
        if (top + height > viewport.height - edge) {
          top = Number(triggerRect.top || 0) - height - gap;
          origin = 'bottom center';
        }
      }
    }

    left = Math.max(edge, Math.min(left, viewport.width - width - edge));
    top = Math.max(edge, Math.min(top, viewport.height - height - edge));
    return {left: Math.round(left), top: Math.round(top), origin: origin};
  }

  function _place(record) {
    if (record.options.managePlacement === false) return Promise.resolve(true);
    const motion = root.pfMotion;
    const read = motion && typeof motion.read === 'function'
      ? motion.read : function(callback) { return Promise.resolve(callback()); };
    const write = motion && typeof motion.write === 'function'
      ? motion.write : function(callback) { return Promise.resolve(callback()); };
    let position = null;

    return read(function() {
      if (record.closed || layers.get(record.channel) !== record) return null;
      position = _coordinates(record);
      return position;
    }, record.signal).then(function() {
      return write(function() {
        if (!position || record.closed || layers.get(record.channel) !== record) {
          return false;
        }
        record.element.style.left = position.left + 'px';
        record.element.style.top = position.top + 'px';
        record.element.style.transformOrigin = position.origin;
        return true;
      }, record.signal);
    });
  }

  function _animateOpen(record) {
    const motion = root.pfMotion;
    if (record.options.animate === false || !motion
        || typeof motion.replace !== 'function') return;
    motion.replace(record.motionElement, 'floating-layer', [
      {opacity: 0, transform: 'translateY(3px) scale(.98)'},
      {opacity: 1, transform: 'translateY(0) scale(1)'},
    ], {
      duration: Number(record.options.duration === undefined
        ? 120 : record.options.duration),
      easing: 'cubic-bezier(.2, .8, .2, 1)',
    }, record.signal);
  }

  function _menuItems(element) {
    if (!element || typeof element.querySelectorAll !== 'function') return [];
    return Array.from(element.querySelectorAll('.ctx-menu-item')).filter(function(item) {
      return item.getAttribute('aria-disabled') !== 'true';
    });
  }

  function _prepareMenu(record) {
    if (record.closed || layers.get(record.channel) !== record) return;
    const items = _menuItems(record.element);
    record.element.setAttribute('role', 'menu');
    items.forEach(function(item, index) {
      item.setAttribute('role', 'menuitem');
      item.setAttribute('tabindex', index === 0 ? '0' : '-1');
    });
    if (record.options.focusOnOpen && items[0]
        && typeof items[0].focus === 'function') items[0].focus();
  }

  function _handleMenuKey(record, event) {
    const items = _menuItems(record.element);
    if (!items.length || !event) return;
    let index = items.indexOf(doc.activeElement);
    if (event.key === 'ArrowDown') index = (index + 1 + items.length) % items.length;
    else if (event.key === 'ArrowUp') index = (index - 1 + items.length) % items.length;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = items.length - 1;
    else if ((event.key === 'Enter' || event.key === ' ') && index >= 0) {
      if (typeof event.preventDefault === 'function') event.preventDefault();
      if (typeof items[index].click === 'function') items[index].click();
      return;
    } else if (event.key.length === 1 && !event.altKey && !event.ctrlKey
        && !event.metaKey) {
      const now = Date.now();
      record.typeahead = now - record.typeaheadAt > 500
        ? event.key.toLocaleLowerCase()
        : record.typeahead + event.key.toLocaleLowerCase();
      record.typeaheadAt = now;
      const start = Math.max(0, index);
      for (let offset = 1; offset <= items.length; offset++) {
        const candidate = items[(start + offset) % items.length];
        const label = String(candidate.textContent || '').trim().toLocaleLowerCase();
        if (label.indexOf(record.typeahead) === 0) {
          index = (start + offset) % items.length;
          break;
        }
      }
    } else {
      return;
    }
    if (typeof event.preventDefault === 'function') event.preventDefault();
    items.forEach(function(item, itemIndex) {
      item.setAttribute('tabindex', itemIndex === index ? '0' : '-1');
    });
    if (typeof items[index].focus === 'function') items[index].focus();
  }

  function open(options) {
    options = options || {};
    if (!doc || !doc.body) throw new Error('pfFloatingLayer requires document.body');
    if (!options.element) throw new TypeError('pfFloatingLayer.open requires an element');

    const channel = String(options.channel || 'floating');
    close(channel, {reason: 'replaced', restoreFocus: false});

    const element = options.element;
    if (!element.parentNode) doc.body.appendChild(element);
    if (options.managePlacement !== false) element.style.position = 'fixed';
    element.style.pointerEvents = '';
    element.removeAttribute('inert');
    element.setAttribute('aria-hidden', 'false');

    const controller = typeof root.AbortController === 'function'
      ? new root.AbortController() : null;
    const record = {
      channel: channel,
      element: element,
      motionElement: options.motionElement || element,
      trigger: options.trigger || doc.activeElement || null,
      restoreFocus: options.restoreFocus === true,
      removeOnClose: options.removeOnClose !== false,
      onClose: options.onClose || null,
      options: options,
      controller: controller,
      signal: controller ? controller.signal : null,
      listeners: [],
      closed: false,
      closing: null,
      typeahead: '',
      typeaheadAt: 0,
    };
    layers.set(channel, record);
    counters.opened += 1;
    if (channel === 'context-menu' && record.trigger
        && typeof record.trigger.setAttribute === 'function') {
      record.trigger.setAttribute('aria-haspopup', 'menu');
      record.trigger.setAttribute('aria-expanded', 'true');
      record.onClose = function(reason) {
        record.trigger.setAttribute('aria-expanded', 'false');
        if (typeof options.onClose === 'function') options.onClose(reason);
      };
    }

    const closeWith = function(reason, restoreFocus) {
      close(channel, {reason: reason, restoreFocus: restoreFocus});
    };
    _listen(record, doc, 'keydown', function(event) {
      if (event && event.key === 'Escape') {
        if (typeof event.preventDefault === 'function') event.preventDefault();
        closeWith('escape', true);
      } else if (event && event.key === 'Tab' && options.modal === true) {
        _trapModalFocus(record, event);
      }
    });
    if (options.closeOnOutside !== false) {
      _listen(record, doc, 'pointerdown', function(event) {
        const target = event && event.target;
        if (_contains(element, target)) return;
        if (options.keepOnTrigger !== false && _contains(record.trigger, target)) return;
        closeWith('outside', true);
      }, true);
      _listen(record, doc, 'pointercancel', function() {
        closeWith('pointercancel', true);
      }, true);
    }
    if (options.closeOnEnvironment !== false) {
      _listen(record, root, 'resize', function() {
        closeWith('resize', false);
      });
      _listen(record, root, 'scroll', function(event) {
        const target = event && event.target;
        // Transcript auto-scroll must not dismiss a sidebar menu. Scrolling
        // inside a long menu also leaves its position and ownership intact.
        if (_contains(element, target)) return;
        if (target && target !== root && target !== doc
            && !_contains(target, record.trigger)) return;
        closeWith('scroll', false);
      }, true);
      _listen(record, root, 'blur', function() {
        closeWith('blur', false);
      });
    }
    if (options.closeOnSelect !== false) {
      _listen(record, element, 'click', function() {
        closeWith('select', true);
      });
    }
    if (channel === 'context-menu') {
      _listen(record, element, 'keydown', function(event) {
        _handleMenuKey(record, event);
      });
      Promise.resolve().then(function() { _prepareMenu(record); });
    }

    _place(record).then(function(placed) {
      if (placed && !record.closed && layers.get(record.channel) === record) {
        _animateOpen(record);
        _focusInitial(record);
      }
    });
    return {
      channel: channel,
      signal: record.signal,
      close: function(closeOptions) { return close(channel, closeOptions); },
      reposition: function() { return _place(record); },
    };
  }

  function diagnostics() {
    if (!_diagnosticsEnabled()) return null;
    return {
      activeLayers: layers.size,
      listeners: counters.listeners,
      opened: counters.opened,
      closed: counters.closed,
    };
  }

  const api = {
    open: open,
    close: close,
    closeAll: closeAll,
    diagnostics: diagnostics,
  };
  root.pfFloatingLayer = api;

  // Compatibility facade used by every existing context-menu owner. Keeping
  // this one name avoids a broad command/markup rewrite while moving placement
  // and lifecycle ownership into the shared controller.
  root._positionMenu = function(menu, event) {
    event = event || {};
    return open({
      channel: 'context-menu',
      element: menu,
      trigger: event.currentTarget || event.target || doc.activeElement || null,
      point: {x: Number(event.clientX || 0), y: Number(event.clientY || 0)},
      placement: 'cursor',
      removeOnClose: true,
      restoreFocus: true,
      focusOnOpen: event.type === 'keydown'
        || (event.type === 'contextmenu' && event.detail === 0),
    });
  };
})(typeof window !== 'undefined' ? window : globalThis);
