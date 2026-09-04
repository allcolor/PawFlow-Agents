// Keyed, incremental read-only DOM projections for filtered and OpenSpace views.
(function(root) {
  'use strict';

  if (!root || root.pfProjection) return;

  const counters = {
    controllers: 0,
    batches: 0,
    dirtyKeys: 0,
    clones: 0,
    patchedRows: 0,
    removedRows: 0,
  };

  function _diagnosticsEnabled() {
    return root.__PF_MOTION_DIAGNOSTICS__ === true;
  }

  function defaultKey(node) {
    if (!node || !node.dataset) {
      throw new Error('projection source row has no dataset');
    }
    const fields = [
      ['projection', node.dataset.projectionKey],
      ['msg', node.dataset.msgid || node.dataset.messageId],
      ['turn', node.dataset.turnId],
      ['group', node.dataset.groupKey],
      ['delegate-group', node.dataset.delegateKey],
      ['task', node.dataset.taskId],
      ['delegate', node.dataset.delegateTaskId],
      ['local', node.dataset.conversationLocalId],
    ];
    for (const pair of fields) {
      if (pair[1]) return pair[0] + ':' + String(pair[1]);
    }
    if (node.id) return 'id:' + String(node.id);
    throw new Error('projection source row is missing a durable identity');
  }

  function create(options) {
    options = options || {};
    const source = options.source;
    const destination = options.destination;
    if (!source || !destination || typeof options.project !== 'function') {
      throw new TypeError('pfProjection.create requires source, destination, and project');
    }

    const keyFor = options.key || defaultKey;
    const projectedByKey = new Map();
    const sourceKeyByNode = new WeakMap();
    const pendingNodes = new Set();
    const removedKeys = new Set();
    let observer = null;
    let frameId = 0;
    let active = false;
    let destroyed = false;
    let fullReconcile = false;
    let reorder = false;
    counters.controllers += 1;

    function _requestFrame(callback) {
      if (typeof root.requestAnimationFrame === 'function') {
        return root.requestAnimationFrame(callback);
      }
      return root.setTimeout(callback, 0);
    }

    function _cancelFrame(id) {
      if (typeof root.cancelAnimationFrame === 'function') root.cancelAnimationFrame(id);
      else if (typeof root.clearTimeout === 'function') root.clearTimeout(id);
    }

    function _isActive() {
      return typeof options.isActive === 'function' ? !!options.isActive() : active;
    }

    function _sourceOwner(node) {
      let current = node && (node.nodeType === 1 ? node : node.parentNode);
      while (current && current.parentNode && current.parentNode !== source) {
        current = current.parentNode;
      }
      return current && current.parentNode === source ? current : null;
    }

    function _safeKey(node) {
      try { return keyFor(node); } catch (_error) { return ''; }
    }

    function _dispose(node) {
      if (node && typeof options.dispose === 'function') options.dispose(node);
    }

    function _removeKey(key) {
      const existing = projectedByKey.get(key);
      if (!existing) return;
      _dispose(existing);
      if (existing.remove) existing.remove();
      else if (existing.parentNode) existing.parentNode.removeChild(existing);
      projectedByKey.delete(key);
      if (_diagnosticsEnabled()) counters.removedRows += 1;
    }

    function _replace(key, sourceNode) {
      const existing = projectedByKey.get(key) || null;
      if (existing && typeof options.beforePatch === 'function') {
        options.beforePatch(existing, sourceNode);
      }
      const projected = options.project(sourceNode, key, existing);
      if (!projected) {
        _removeKey(key);
        return null;
      }
      projected.dataset.pfProjectionKey = key;
      if (typeof options.hydrate === 'function') options.hydrate(projected, sourceNode, key);
      if (_diagnosticsEnabled()) counters.clones += 1;
      if (existing) {
        destination.insertBefore(projected, existing);
        _dispose(existing);
        if (existing.remove) existing.remove();
        else destination.removeChild(existing);
      } else {
        destination.appendChild(projected);
      }
      projectedByKey.set(key, projected);
      if (_diagnosticsEnabled()) counters.patchedRows += 1;
      return projected;
    }

    function _orderedKeys() {
      const seen = new Set();
      const ordered = [];
      Array.from(source.children || []).forEach(function(node) {
        const key = keyFor(node);
        if (seen.has(key)) throw new Error('duplicate projection key: ' + key);
        seen.add(key);
        sourceKeyByNode.set(node, key);
        ordered.push(key);
      });
      return ordered;
    }

    function _reorder(orderedKeys) {
      let index = 0;
      orderedKeys.forEach(function(key) {
        const node = projectedByKey.get(key);
        if (!node) return;
        const current = destination.children[index] || null;
        if (current !== node) destination.insertBefore(node, current);
        index += 1;
      });
    }

    function _renderEmpty() {
      if (typeof options.renderEmpty === 'function') {
        options.renderEmpty(destination, projectedByKey.size);
      }
    }

    function _flush() {
      frameId = 0;
      if (destroyed || !active) return;
      if (!_isActive()) {
        setActive(false);
        return;
      }
      const wasAtBottom = destination.scrollHeight
        - destination.scrollTop - destination.clientHeight < 40;
      const dirtyCount = fullReconcile
        ? Array.from(source.children || []).length : pendingNodes.size + removedKeys.size;
      if (_diagnosticsEnabled()) {
        counters.batches += 1;
        counters.dirtyKeys += dirtyCount;
      }

      const orderedKeys = (fullReconcile || reorder) ? _orderedKeys() : null;
      if (fullReconcile) {
        const liveKeys = new Set(orderedKeys);
        Array.from(projectedByKey.keys()).forEach(function(key) {
          if (!liveKeys.has(key)) _removeKey(key);
        });
        Array.from(source.children || []).forEach(function(node) {
          _replace(sourceKeyByNode.get(node), node);
        });
      } else {
        removedKeys.forEach(_removeKey);
        pendingNodes.forEach(function(node) {
          const owner = _sourceOwner(node) || node;
          if (!owner || owner.parentNode !== source) return;
          const oldKey = sourceKeyByNode.get(owner);
          const nextKey = keyFor(owner);
          if (oldKey && oldKey !== nextKey) _removeKey(oldKey);
          sourceKeyByNode.set(owner, nextKey);
          _replace(nextKey, owner);
        });
      }

      if (orderedKeys) _reorder(orderedKeys);
      fullReconcile = false;
      reorder = false;
      pendingNodes.clear();
      removedKeys.clear();
      _renderEmpty();
      if ((options.stickToBottom === true
          || (typeof options.stickToBottom === 'function' && options.stickToBottom()))
          && wasAtBottom) {
        destination.scrollTop = destination.scrollHeight;
      }
      if (typeof options.afterPatch === 'function') options.afterPatch();
    }

    function _schedule() {
      if (destroyed || !active || frameId) return;
      frameId = -1;
      const requested = _requestFrame(_flush);
      if (frameId) frameId = requested || -1;
    }

    function _queueRecords(records) {
      if (destroyed || !active) return;
      if (!_isActive()) {
        setActive(false);
        return;
      }
      (records || []).forEach(function(record) {
        const targetOwner = _sourceOwner(record.target);
        if (targetOwner) pendingNodes.add(targetOwner);
        Array.from(record.removedNodes || []).forEach(function(node) {
          const key = sourceKeyByNode.get(node) || _safeKey(node);
          if (key) removedKeys.add(key);
          const owner = _sourceOwner(record.target);
          if (owner) pendingNodes.add(owner);
        });
        Array.from(record.addedNodes || []).forEach(function(node) {
          const owner = _sourceOwner(node) || _sourceOwner(record.target);
          if (owner) pendingNodes.add(owner);
        });
        if (record.type === 'childList' && record.target === source) reorder = true;
      });
      _schedule();
    }

    function _observe() {
      if (observer || typeof root.MutationObserver !== 'function') return;
      observer = new root.MutationObserver(_queueRecords);
      observer.observe(source, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeOldValue: true,
      });
    }

    function setActive(next) {
      next = !!next;
      if (destroyed || active === next) return;
      active = next;
      if (!active) {
        if (observer) observer.disconnect();
        observer = null;
        if (frameId) _cancelFrame(frameId);
        frameId = 0;
        pendingNodes.clear();
        removedKeys.clear();
        fullReconcile = false;
        reorder = false;
        return;
      }
      _observe();
      reconcileAll();
    }

    function reconcileAll() {
      if (destroyed) return;
      fullReconcile = true;
      reorder = true;
      _schedule();
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (observer) observer.disconnect();
      observer = null;
      if (frameId) _cancelFrame(frameId);
      frameId = 0;
      pendingNodes.clear();
      removedKeys.clear();
      projectedByKey.forEach(_dispose);
      projectedByKey.clear();
      counters.controllers = Math.max(0, counters.controllers - 1);
    }

    setActive(true);
    return {
      reconcileAll: reconcileAll,
      setActive: setActive,
      destroy: destroy,
      size: function() { return projectedByKey.size; },
    };
  }

  root.pfProjection = {
    create: create,
    key: defaultKey,
    diagnostics: function() {
      return _diagnosticsEnabled() ? Object.assign({}, counters) : null;
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
