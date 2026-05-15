// ── PawFlow UI Extension runtime (ui.v1) ────────────────────────────
// Contract surface exposed to UI extensions shipped via PFP packages.
//
//   pawflow.register(packageId, callback)
//     The callback receives a per-package `pfp` API object:
//       pfp.id            string  package id
//       pfp.t(key)        i18n lookup (namespaced)
//       pfp.ui.slot(slot, entryId, renderFn)
//       pfp.ui.openDialog(title, contentNode, opts?)
//       pfp.ui.closeDialog()
//       pfp.ui.openPanel(id, renderFn)   // right-side panel
//       pfp.ui.closePanel()
//       pfp.on(hook, cb)     subscribe to a UI hook
//       pfp.off(hook, cb)    unsubscribe
//       pfp.publish(local, data)   inter-extension bus
//       pfp.subscribe(local, cb)
//       pfp.call(action, body)     wraps action$ with _ext = package id
//       pfp.command(name, spec)    register a slash command
//
// All slot/hook names are versioned under ui.v1. Adding a new slot or
// hook is additive; renaming or removing one bumps to ui.v2 and the
// install plan must refuse incompatible packages.
//
// This file does not load any extension by itself. The boot script in
// serve_chat_ui.py injects `window.PAWFLOW_EXTENSIONS` with the asset
// manifest of installed UI extensions; this runtime fetches the JS files
// and waits for each extension to call `pawflow.register(...)`.

(function () {
  'use strict';

  var UI_API_VERSION = 'ui.v1';

  var KNOWN_SLOTS = [
    'action_menu', 'gear_menu', 'resources_panel',
    'sidebar_top', 'sidebar_bottom',
    'header_actions', 'tab_bar',
  ];

  var KNOWN_HOOKS = [
    'boot', 'shutdown',
    'conversation_changed', 'conversation_created', 'conversation_deleted',
    'message_appended', 'message_streaming',
    'tool_call_started', 'tool_call_completed',
    'command_submitted', 'command_result',
    'before_send',
    'agent_changed', 'theme_changed',
    'tab_switched', 'permission_mode_changed',
    'sse_event',
  ];

  // Registered packages: id -> { ready: bool, callback, pfp, slots: [], hooks: {}, commands: [], localBus: {} }
  var _packages = Object.create(null);
  // Pending hook listeners across all packages: hook -> [{pkg, cb}, ...]
  var _hookListeners = Object.create(null);
  // Slot entries across all packages: slot -> [{pkg, id, render}]
  var _slotEntries = Object.create(null);
  // Per-extension slash commands: name -> {pkg, spec}
  var _commands = Object.create(null);
  // Last `boot` payload — replayed for packages that load late.
  var _bootPayload = null;
  var _booted = false;

  function _isString(v) { return typeof v === 'string' && v.length > 0; }
  function _isFn(v) { return typeof v === 'function'; }
  function _slotEl(slotName) {
    return document.querySelector('[data-pf-slot="' + slotName + '_ext"]');
  }

  function _logExtError(pkg, where, err) {
    try {
      var msg = (err && err.message) || String(err || 'error');
      console.warn('[ext:' + (pkg || '?') + '] ' + where + ': ' + msg);
    } catch (_e) { /* swallow */ }
  }

  // ── Slot rendering ─────────────────────────────────────────────────
  function _renderSlot(slotName) {
    var host = _slotEl(slotName);
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    var entries = _slotEntries[slotName] || [];
    entries.forEach(function (entry) {
      var node;
      try { node = entry.render(); }
      catch (err) { _logExtError(entry.pkg, 'render(' + slotName + '/' + entry.id + ')', err); return; }
      if (!node) return;
      if (typeof node === 'string') {
        var wrap = document.createElement('div');
        wrap.innerHTML = node;
        node = wrap;
      }
      if (!(node instanceof Node)) {
        _logExtError(entry.pkg, 'slot(' + slotName + '/' + entry.id + ')', 'render() must return a Node or string');
        return;
      }
      var wrapper = document.createElement('div');
      wrapper.setAttribute('data-pf-ext', entry.pkg);
      wrapper.setAttribute('data-pf-slot-entry', slotName + '/' + entry.id);
      wrapper.appendChild(node);
      host.appendChild(wrapper);
    });
  }

  function _renderAllSlots() {
    KNOWN_SLOTS.forEach(_renderSlot);
  }

  // ── Hook firing ────────────────────────────────────────────────────
  function _fireHook(hookName, payload) {
    var listeners = _hookListeners[hookName];
    if (!listeners || !listeners.length) return;
    // Snapshot to avoid mutation surprises during dispatch.
    var snapshot = listeners.slice();
    snapshot.forEach(function (entry) {
      // Async-fire so a slow listener cannot freeze the caller.
      setTimeout(function () {
        try { entry.cb(payload); }
        catch (err) { _logExtError(entry.pkg, 'hook(' + hookName + ')', err); }
      }, 0);
    });
  }

  // Mutable hook variant: each subscriber can return a transformed payload.
  // Synchronous, with a per-listener try/catch fail-open policy.
  function _fireFilter(hookName, payload) {
    var listeners = _hookListeners[hookName];
    if (!listeners || !listeners.length) return payload;
    var current = payload;
    listeners.forEach(function (entry) {
      try {
        var out = entry.cb(current);
        if (out !== undefined) current = out;
      } catch (err) {
        _logExtError(entry.pkg, 'filter(' + hookName + ')', err);
      }
    });
    return current;
  }

  // ── Per-package pfp API ────────────────────────────────────────────
  function _makePfpFor(packageId) {
    var pkg = _packages[packageId];
    var localBus = pkg.localBus;
    var i18nNamespace = packageId + '.';
    var api = {
      id: packageId,
      version: UI_API_VERSION,
      t: function (key, vars) {
        if (typeof t === 'function') {
          var nsKey = i18nNamespace + key;
          var translated = t(nsKey, vars);
          if (translated !== nsKey) return translated;
          // Fallback: try the bare key (shared with PawFlow catalogs)
          return t(key, vars);
        }
        return key;
      },
      ui: {
        slot: function (slotName, entryId, renderFn) {
          if (KNOWN_SLOTS.indexOf(slotName) < 0) {
            _logExtError(packageId, 'slot', 'unknown slot: ' + slotName);
            return false;
          }
          if (!_isString(entryId) || !_isFn(renderFn)) {
            _logExtError(packageId, 'slot', 'entryId(string) and renderFn(function) are required');
            return false;
          }
          if (!_slotEntries[slotName]) _slotEntries[slotName] = [];
          // Reject duplicate id from the same package — append-only contract.
          var dupe = _slotEntries[slotName].some(function (e) {
            return e.pkg === packageId && e.id === entryId;
          });
          if (dupe) {
            _logExtError(packageId, 'slot', 'duplicate entry: ' + slotName + '/' + entryId);
            return false;
          }
          _slotEntries[slotName].push({ pkg: packageId, id: entryId, render: renderFn });
          pkg.slots.push({ slot: slotName, id: entryId });
          _renderSlot(slotName);
          return true;
        },
        openDialog: function (title, content, opts) {
          var host = document.getElementById('pf-ext-modal-host');
          if (!host) return false;
          host.innerHTML = '';
          var overlay = document.createElement('div');
          overlay.className = 'pf-ext-modal-overlay';
          overlay.setAttribute('data-pf-ext', packageId);
          overlay.style.cssText =
            'position:fixed;inset:0;background:rgba(0,0,0,0.6);'
            + 'display:flex;align-items:center;justify-content:center;'
            + 'z-index:10000;';
          var box = document.createElement('div');
          box.className = 'pf-ext-modal-box';
          box.style.cssText =
            'background:var(--pf-panel,#16213e);color:var(--pf-text,#e0e0e0);'
            + 'border:1px solid var(--pf-border,#0f3460);border-radius:8px;'
            + 'min-width:320px;max-width:80vw;max-height:80vh;overflow:auto;'
            + 'box-shadow:0 8px 24px var(--pf-shadow,rgba(0,0,0,0.5));';
          if (title) {
            var head = document.createElement('div');
            head.style.cssText =
              'padding:10px 14px;border-bottom:1px solid var(--pf-border,#0f3460);'
              + 'font-size:13px;color:var(--pf-accent,#e94560);display:flex;'
              + 'align-items:center;justify-content:space-between;';
            var titleEl = document.createElement('span');
            titleEl.textContent = String(title);
            var closeBtn = document.createElement('button');
            closeBtn.textContent = '×';
            closeBtn.style.cssText = 'background:none;border:none;color:inherit;font-size:18px;cursor:pointer;';
            closeBtn.addEventListener('click', function () { api.ui.closeDialog(); });
            head.appendChild(titleEl);
            head.appendChild(closeBtn);
            box.appendChild(head);
          }
          var body = document.createElement('div');
          body.style.cssText = 'padding:12px 14px;';
          if (content instanceof Node) body.appendChild(content);
          else if (typeof content === 'string') body.innerHTML = content;
          box.appendChild(body);
          overlay.appendChild(box);
          if (!(opts && opts.modal === false)) {
            overlay.addEventListener('click', function (e) {
              if (e.target === overlay) api.ui.closeDialog();
            });
          }
          host.appendChild(overlay);
          return true;
        },
        closeDialog: function () {
          var host = document.getElementById('pf-ext-modal-host');
          if (host) host.innerHTML = '';
          return true;
        },
        openPanel: function (panelId, renderFn) {
          var host = document.getElementById('pf-ext-panel-host');
          if (!host || !_isFn(renderFn)) return false;
          host.innerHTML = '';
          host.setAttribute('data-pf-ext', packageId);
          host.setAttribute('data-pf-panel', String(panelId || ''));
          host.style.display = 'block';
          var node;
          try { node = renderFn(); }
          catch (err) { _logExtError(packageId, 'openPanel(' + panelId + ')', err); return false; }
          if (node instanceof Node) host.appendChild(node);
          else if (typeof node === 'string') host.innerHTML = node;
          return true;
        },
        closePanel: function () {
          var host = document.getElementById('pf-ext-panel-host');
          if (host) {
            host.innerHTML = '';
            host.style.display = 'none';
            host.removeAttribute('data-pf-ext');
            host.removeAttribute('data-pf-panel');
          }
          return true;
        },
      },
      on: function (hookName, cb) {
        if (KNOWN_HOOKS.indexOf(hookName) < 0) {
          _logExtError(packageId, 'on', 'unknown hook: ' + hookName);
          return false;
        }
        if (!_isFn(cb)) return false;
        if (!_hookListeners[hookName]) _hookListeners[hookName] = [];
        _hookListeners[hookName].push({ pkg: packageId, cb: cb });
        if (!pkg.hooks[hookName]) pkg.hooks[hookName] = [];
        pkg.hooks[hookName].push(cb);
        // Replay `boot` if the extension subscribed after boot already fired.
        if (hookName === 'boot' && _booted) {
          setTimeout(function () {
            try { cb(_bootPayload || {}); }
            catch (err) { _logExtError(packageId, 'hook(boot, replay)', err); }
          }, 0);
        }
        return true;
      },
      off: function (hookName, cb) {
        var listeners = _hookListeners[hookName];
        if (!listeners) return false;
        for (var i = listeners.length - 1; i >= 0; i--) {
          if (listeners[i].pkg === packageId && listeners[i].cb === cb) {
            listeners.splice(i, 1);
          }
        }
        var own = pkg.hooks[hookName] || [];
        for (var j = own.length - 1; j >= 0; j--) {
          if (own[j] === cb) own.splice(j, 1);
        }
        return true;
      },
      publish: function (localEvent, data) {
        if (!_isString(localEvent)) return false;
        var subs = localBus[localEvent];
        if (!subs || !subs.length) return true;
        subs.slice().forEach(function (sub) {
          setTimeout(function () {
            try { sub(data); }
            catch (err) { _logExtError(packageId, 'localBus(' + localEvent + ')', err); }
          }, 0);
        });
        return true;
      },
      subscribe: function (localEvent, cb) {
        if (!_isString(localEvent) || !_isFn(cb)) return false;
        if (!localBus[localEvent]) localBus[localEvent] = [];
        localBus[localEvent].push(cb);
        return true;
      },
      call: function (action, body) {
        var payload = Object.assign({}, body || {}, { _ext: packageId });
        if (typeof action$ !== 'function') {
          return Promise.reject(new Error('action$ is not available'));
        }
        return new Promise(function (resolve, reject) {
          action$(action, payload).subscribe({
            next: function (data) { resolve(data); },
            error: function (err) { reject(err); },
          });
        });
      },
      command: function (name, spec) {
        if (!_isString(name) || name.charAt(0) !== '/') {
          _logExtError(packageId, 'command', 'name must start with /');
          return false;
        }
        var key = name.toLowerCase();
        if (_commands[key]) {
          _logExtError(packageId, 'command', 'duplicate: ' + name);
          return false;
        }
        if (!spec || !_isFn(spec.handler)) {
          _logExtError(packageId, 'command', 'spec.handler is required');
          return false;
        }
        _commands[key] = {
          pkg: packageId,
          usage: String(spec.usage || name),
          short: String(spec.short || ''),
          handler: spec.handler,
        };
        pkg.commands.push(key);
        return true;
      },
    };
    return api;
  }

  // ── Public registry ────────────────────────────────────────────────
  function _ensurePackage(packageId) {
    if (!_packages[packageId]) {
      _packages[packageId] = {
        ready: false, callback: null, pfp: null,
        slots: [], hooks: {}, commands: [], localBus: Object.create(null),
      };
    }
    return _packages[packageId];
  }

  function register(packageId, callback) {
    if (!_isString(packageId)) {
      _logExtError('?', 'register', 'package id is required');
      return false;
    }
    if (!_isFn(callback)) {
      _logExtError(packageId, 'register', 'callback is required');
      return false;
    }
    var pkg = _ensurePackage(packageId);
    if (pkg.ready) {
      _logExtError(packageId, 'register', 'duplicate registration');
      return false;
    }
    pkg.callback = callback;
    pkg.pfp = _makePfpFor(packageId);
    pkg.ready = true;
    // Defer the actual callback invocation until the DOM is loaded so
    // slot containers are available. If the DOM is already ready, run
    // on the next microtask to keep ordering deterministic across many
    // extensions registering back-to-back.
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        _runRegistration(packageId);
      }, { once: true });
    } else {
      setTimeout(function () { _runRegistration(packageId); }, 0);
    }
    return true;
  }

  function _runRegistration(packageId) {
    var pkg = _packages[packageId];
    if (!pkg || !pkg.callback) return;
    try { pkg.callback(pkg.pfp); }
    catch (err) { _logExtError(packageId, 'register(callback)', err); }
  }

  function listPackages() {
    return Object.keys(_packages).map(function (id) {
      var p = _packages[id];
      return {
        id: id,
        ready: p.ready,
        slots: p.slots.slice(),
        hooks: Object.keys(p.hooks),
        commands: p.commands.slice(),
      };
    });
  }

  function getCommand(name) {
    var key = (name || '').toLowerCase();
    return _commands[key] || null;
  }

  function listCommands() {
    return Object.keys(_commands).map(function (k) {
      var c = _commands[k];
      return { name: k, pkg: c.pkg, usage: c.usage, short: c.short };
    });
  }

  // Internal accessors used by hook firing points in other JS modules.
  var _internal = {
    fireHook: _fireHook,
    fireFilter: _fireFilter,
    renderSlot: _renderSlot,
    renderAllSlots: _renderAllSlots,
    markBooted: function (payload) {
      _bootPayload = payload || {};
      _booted = true;
    },
    isBooted: function () { return _booted; },
    KNOWN_SLOTS: KNOWN_SLOTS,
    KNOWN_HOOKS: KNOWN_HOOKS,
    UI_API_VERSION: UI_API_VERSION,
  };

  // Expose to the page.
  window.pawflow = {
    version: UI_API_VERSION,
    register: register,
    listPackages: listPackages,
    getCommand: getCommand,
    listCommands: listCommands,
  };
  // Internal namespace, deliberately separate so extensions cannot
  // override the hook-firing primitives by writing to window.pawflow.
  window._pawflowExtRuntime = _internal;

  // Fire `boot` once after DOMContentLoaded AND after all extension assets
  // declared in PAWFLOW_EXTENSIONS have finished loading. Extension scripts
  // registered via `register()` may be late (lazy-loaded from /chat/ext/...),
  // so we wait for the manifest before firing boot. Subscribers attached
  // during the setup phase receive the boot payload; later subscribers get
  // the cached payload replayed via the replay path inside `on`.
  function _bootDispatch() {
    var payload = {
      ui_api_version: UI_API_VERSION,
      extensions_count: (window.PAWFLOW_EXTENSIONS || []).length,
      asset_version: window.PAWFLOW_ASSET_VERSION || '',
    };
    _internal.markBooted(payload);
    _fireHook('boot', payload);
    // Slot containers may exist before any extension registered (the
    // browser receives the page before the bootstrap manifest loads any
    // remote extension assets). Trigger one round of rendering so static
    // slot entries declared from `register()` callbacks become visible.
    _renderAllSlots();
  }

  function _loadOneAsset(asset) {
    return new Promise(function (resolve) {
      if (!asset || !asset.url) { resolve(); return; }
      var kind = asset.kind;
      if (kind === 'script') {
        var s = document.createElement('script');
        s.src = asset.url;
        s.async = false;
        s.defer = true;
        s.crossOrigin = 'anonymous';
        s.onload = function () { resolve(); };
        s.onerror = function () {
          _logExtError('?', 'asset_load', 'script ' + asset.url);
          resolve();
        };
        document.head.appendChild(s);
      } else if (kind === 'style') {
        var l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = asset.url;
        l.onload = function () { resolve(); };
        l.onerror = function () {
          _logExtError('?', 'asset_load', 'style ' + asset.url);
          resolve();
        };
        document.head.appendChild(l);
      } else {
        // i18n / other — not auto-loaded; the extension can fetch on demand.
        resolve();
      }
    });
  }

  function _loadAllExtensions() {
    var manifest = window.PAWFLOW_EXTENSIONS || [];
    if (!manifest.length) return Promise.resolve();
    var promises = [];
    manifest.forEach(function (entry) {
      if (!entry || entry.version_compat !== UI_API_VERSION) return;
      var assets = entry.assets || [];
      // Load styles first so script logic can rely on CSS variables.
      assets.filter(function (a) { return a.kind === 'style'; })
            .forEach(function (a) { promises.push(_loadOneAsset(a)); });
      // Scripts load with async=false; the browser keeps execution order
      // matching insertion order, so multiple extensions remain isolated.
      assets.filter(function (a) { return a.kind === 'script'; })
            .forEach(function (a) { promises.push(_loadOneAsset(a)); });
    });
    return Promise.all(promises);
  }

  function _boot() {
    _loadAllExtensions().then(function () {
      // Wait one tick so register() callbacks scheduled by extension
      // scripts (via setTimeout 0) have run before we mark booted.
      setTimeout(_bootDispatch, 0);
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _boot, { once: true });
  } else {
    _boot();
  }
})();
