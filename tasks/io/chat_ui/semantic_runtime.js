// Generic semantic registry and authenticated browser correlation for PFP UI extensions.
(function () {
  'use strict';

  var MAX_JSON_BYTES = 64 * 1024;
  var ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
  var ROLE_RE = /^[A-Za-z][A-Za-z0-9._-]{0,63}$/;
  var OPERATIONS = ['list', 'get', 'invoke'];
  var _nodes = Object.create(null);
  var _packages = Object.create(null);
  var _heartbeatTimer = null;

  function _assertJsonValue(value, label, seen) {
    var type = typeof value;
    if (type === 'function' || type === 'undefined' || type === 'symbol'
        || type === 'bigint') {
      throw new Error(label + ' must be JSON-serializable');
    }
    if (!value || type !== 'object') return;
    if (value === window || value === document || value.nodeType
        || (typeof Node !== 'undefined' && value instanceof Node)) {
      throw new Error(label + ' must be JSON-serializable');
    }
    seen = seen || [];
    if (seen.indexOf(value) >= 0) {
      throw new Error(label + ' must be JSON-serializable');
    }
    seen.push(value);
    Object.keys(value).forEach(function (key) {
      _assertJsonValue(value[key], label, seen);
    });
    seen.pop();
  }

  function _jsonClone(value, label) {
    var encoded;
    _assertJsonValue(value, label);
    try { encoded = JSON.stringify(value); }
    catch (_err) { throw new Error(label + ' must be JSON-serializable'); }
    if (typeof encoded === 'undefined') {
      throw new Error(label + ' must be JSON-serializable');
    }
    if (encoded.length > MAX_JSON_BYTES) {
      throw new Error(label + ' is too large');
    }
    return JSON.parse(encoded);
  }

  function _deepFreeze(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
    Object.keys(value).forEach(function (key) { _deepFreeze(value[key]); });
    return Object.freeze(value);
  }

  function _qualify(packageId, nodeId) {
    var raw = String(nodeId || '').trim();
    if (!raw) throw new Error('semantic node id is required');
    if (raw.indexOf(':') >= 0) {
      if (raw.indexOf(packageId + ':') !== 0) {
        throw new Error('package does not own semantic node: ' + raw);
      }
      raw = raw.slice(packageId.length + 1);
    }
    if (!ID_RE.test(raw)) throw new Error('invalid semantic node id: ' + raw);
    return packageId + ':' + raw;
  }

  function _validateParameters(parameters) {
    if (!parameters || typeof parameters !== 'object' || Array.isArray(parameters)) {
      throw new Error('semantic action parameters must be an object');
    }
    var allowed = ['string', 'number', 'integer', 'boolean', 'object', 'array'];
    var copy = {};
    Object.keys(parameters).forEach(function (name) {
      if (!ID_RE.test(name)) throw new Error('invalid semantic parameter: ' + name);
      var spec = parameters[name];
      if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
        throw new Error('semantic parameter spec must be an object: ' + name);
      }
      var type = String(spec.type || '');
      if (allowed.indexOf(type) < 0) {
        throw new Error('invalid semantic parameter type: ' + type);
      }
      copy[name] = {
        type: type,
        required: spec.required === true,
      };
      if (Array.isArray(spec.enum)) copy[name].enum = _jsonClone(spec.enum, 'semantic enum');
    });
    return copy;
  }

  function _validateArguments(schema, args) {
    if (!args || typeof args !== 'object' || Array.isArray(args)) {
      throw new Error('semantic action arguments must be an object');
    }
    Object.keys(args).forEach(function (name) {
      if (!Object.prototype.hasOwnProperty.call(schema, name)) {
        throw new Error('unknown semantic action argument: ' + name);
      }
    });
    Object.keys(schema).forEach(function (name) {
      var spec = schema[name];
      var has = Object.prototype.hasOwnProperty.call(args, name);
      if (spec.required && !has) {
        throw new Error('semantic action argument is required: ' + name);
      }
      if (!has) return;
      var value = args[name];
      var ok = (
        (spec.type === 'string' && typeof value === 'string')
        || (spec.type === 'number' && typeof value === 'number' && Number.isFinite(value))
        || (spec.type === 'integer' && Number.isInteger(value))
        || (spec.type === 'boolean' && typeof value === 'boolean')
        || (spec.type === 'object' && value && typeof value === 'object' && !Array.isArray(value))
        || (spec.type === 'array' && Array.isArray(value))
      );
      if (!ok) throw new Error('invalid semantic action argument type: ' + name);
      if (spec.enum && spec.enum.indexOf(value) < 0) {
        throw new Error('semantic action argument is outside enum: ' + name);
      }
    });
    return _jsonClone(args, 'semantic action arguments');
  }

  function _snapshot(row) {
    var state = row.state ? row.state() : {};
    state = _jsonClone(state == null ? {} : state, 'semantic node state');
    return _deepFreeze({
      id: row.id,
      role: row.role,
      label: row.label,
      parent: row.parent,
      state: state,
      actions: Object.keys(row.actions).reduce(function (out, name) {
        out[name] = { parameters: _jsonClone(
          row.actions[name].parameters, 'semantic action schema') };
        return out;
      }, {}),
    });
  }

  function _register(packageId, spec) {
    if (!_packages[packageId]) throw new Error('semantic package is not registered');
    if (!spec || typeof spec !== 'object') {
      throw new Error('semantic node spec is required');
    }
    var id = _qualify(packageId, spec.id);
    if (_nodes[id]) throw new Error('duplicate semantic node: ' + id);
    var role = String(spec.role || '').trim();
    var label = String(spec.label || '').trim();
    if (!ROLE_RE.test(role)) throw new Error('invalid semantic node role');
    if (!label || label.length > 256) throw new Error('invalid semantic node label');
    var parent = spec.parent ? String(spec.parent) : '';
    if (parent && parent.length > 256) throw new Error('invalid semantic node parent');
    if (spec.state != null && typeof spec.state !== 'function') {
      throw new Error('semantic node state must be a function');
    }
    var actions = spec.actions || {};
    if (!actions || typeof actions !== 'object' || Array.isArray(actions)) {
      throw new Error('semantic node actions must be an object');
    }
    var normalizedActions = {};
    Object.keys(actions).forEach(function (name) {
      if (!ID_RE.test(name)) throw new Error('invalid semantic action: ' + name);
      var action = actions[name];
      if (!action || typeof action.run !== 'function') {
        throw new Error('semantic action run function is required: ' + name);
      }
      normalizedActions[name] = {
        parameters: _validateParameters(action.parameters || {}),
        run: action.run,
      };
    });
    var row = {
      id: id, package: packageId, role: role, label: label, parent: parent,
      state: spec.state || function () { return {}; },
      actions: normalizedActions,
    };
    _snapshot(row);
    _nodes[id] = row;
    return id;
  }

  function _unregister(packageId, nodeId) {
    var id = _qualify(packageId, nodeId);
    if (!_nodes[id]) return false;
    delete _nodes[id];
    return true;
  }

  function _list(packageId) {
    return _deepFreeze(Object.keys(_nodes).filter(function (id) {
      return _nodes[id].package === packageId;
    }).sort().map(function (id) { return _snapshot(_nodes[id]); }));
  }

  function _get(packageId, nodeId) {
    var id = _qualify(packageId, nodeId);
    var row = _nodes[id];
    return row ? _snapshot(row) : null;
  }

  function _invoke(packageId, nodeId, actionName, args) {
    var id = _qualify(packageId, nodeId);
    var row = _nodes[id];
    if (!row) return Promise.reject(new Error('semantic node not found: ' + id));
    var action = row.actions[String(actionName || '')];
    if (!action) {
      return Promise.reject(new Error('semantic action not found: ' + actionName));
    }
    var clean;
    try { clean = _validateArguments(action.parameters, args || {}); }
    catch (err) { return Promise.reject(err); }
    try { return Promise.resolve(action.run(clean)); }
    catch (err) { return Promise.reject(err); }
  }

  function _apiFor(packageId) {
    return Object.freeze({
      register: function (spec) { return _register(packageId, spec); },
      unregister: function (nodeId) { return _unregister(packageId, nodeId); },
      list: function () { return _list(packageId); },
      get: function (nodeId) { return _get(packageId, nodeId); },
      invoke: function (nodeId, action, args) {
        try { return _invoke(packageId, nodeId, action, args); }
        catch (err) { return Promise.reject(err); }
      },
    });
  }

  function _context() {
    var initial = window.PAWFLOW_EXTENSION_CONTEXT || {};
    return {
      user: String(window._userId || initial.user || ''),
      conversation: (typeof conversationId !== 'undefined' && conversationId)
        ? String(conversationId) : String(initial.conversation || ''),
    };
  }

  function _post(body, keepalive) {
    if (typeof fetch !== 'function') return Promise.resolve();
    var url = (typeof API !== 'undefined' && API)
      ? API.replace(/\/api\/agent$/, '/api/ui') : '/api/ui';
    return fetch(url, {
      method: 'POST',
      headers: (typeof getAuthHeaders === 'function') ? getAuthHeaders() : {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      credentials: 'same-origin',
      keepalive: keepalive === true,
    }).catch(function () {});
  }

  function _tabId() {
    return (typeof _actionClientId === 'function') ? _actionClientId() : '';
  }

  function _busId() {
    return (typeof _uiActionConversationId === 'function')
      ? _uiActionConversationId() : '';
  }

  function _registerTab() {
    var ctx = _context();
    var packages = Object.keys(_packages).sort();
    if (!ctx.user || !ctx.conversation || !_tabId() || !packages.length) return;
    if (typeof _ensureUIActionSSE === 'function') _ensureUIActionSSE();
    _post({
      action: 'pfp_semantic_tab_register',
      conversation_id: ctx.conversation,
      tab_id: _tabId(),
      bus_id: _busId(),
      packages: packages,
      active: document.visibilityState !== 'hidden'
        && (typeof document.hasFocus !== 'function' || document.hasFocus()),
    });
  }

  function _unregisterTab() {
    var ctx = _context();
    if (!ctx.user || !ctx.conversation || !_tabId()) return;
    _post({
      action: 'pfp_semantic_tab_unregister',
      conversation_id: ctx.conversation,
      tab_id: _tabId(),
    }, true);
  }

  function _enablePackage(packageId) {
    _packages[packageId] = true;
    _registerTab();
  }

  function _disablePackage(packageId) {
    Object.keys(_nodes).forEach(function (id) {
      if (_nodes[id].package === packageId) delete _nodes[id];
    });
    delete _packages[packageId];
    if (Object.keys(_packages).length) _registerTab();
    else _unregisterTab();
  }

  function _sendResult(requestId, result, error) {
    var ctx = _context();
    var body = {
      action: 'pfp_semantic_result',
      conversation_id: ctx.conversation,
      tab_id: _tabId(),
      request_id: String(requestId || ''),
    };
    if (error) body.error = String(error).slice(0, 2048);
    else body.result = _jsonClone(result, 'semantic browser result');
    return _post(body);
  }

  function _handleRequest(request) {
    request = request || {};
    var requestId = String(request.request_id || '');
    var packageId = String(request.target_package || '');
    var operation = String(request.operation || '');
    var args = request.arguments || {};
    var promise;
    if (!_packages[packageId]) {
      promise = Promise.reject(new Error(
        'semantic target package is not registered: ' + packageId));
    } else if (OPERATIONS.indexOf(operation) < 0) {
      promise = Promise.reject(new Error(
        'unsupported semantic browser operation: ' + operation));
    } else if (operation === 'list') {
      promise = Promise.resolve({ nodes: _list(packageId) });
    } else if (operation === 'get') {
      promise = Promise.resolve(_get(packageId, args.node));
    } else {
      promise = _invoke(packageId, args.node, args.action, args.arguments || {});
    }
    return promise.then(function (result) {
      return _sendResult(requestId, result, '');
    }).catch(function (err) {
      return _sendResult(requestId, null, err && err.message || String(err));
    });
  }

  function _startHeartbeat() {
    if (_heartbeatTimer || typeof window.setInterval !== 'function') return;
    _heartbeatTimer = window.setInterval(_registerTab, 15000);
  }

  if (document && typeof document.addEventListener === 'function') {
    document.addEventListener('visibilitychange', _registerTab);
  }
  if (window && typeof window.addEventListener === 'function') {
    window.addEventListener('focus', _registerTab);
    window.addEventListener('blur', _registerTab);
    window.addEventListener('pagehide', _unregisterTab);
  }
  _startHeartbeat();

  window._pawflowSemanticRuntime = {
    apiFor: _apiFor,
    enablePackage: _enablePackage,
    disablePackage: _disablePackage,
    registerTab: _registerTab,
    unregisterTab: _unregisterTab,
    handleRequest: _handleRequest,
    list: _list,
    get: _get,
    invoke: _invoke,
  };
})();
