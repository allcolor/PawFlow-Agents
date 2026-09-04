// Incremental Resources DOM ownership and disclosure lifecycle.
(function(root) {
  'use strict';

  if (!root || root.pfResources) return;

  const disclosures = new Map();

  function _attributes(node) {
    if (!node || !node.attributes) return [];
    return Array.from(node.attributes, function(attr) {
      return Array.isArray(attr)
        ? {name: attr[0], value: attr[1]}
        : {name: attr.name, value: attr.value};
    });
  }

  function _nodeKey(node) {
    if (!node || !node.dataset) return '';
    if (node.dataset.resourceSection) return 'section:' + node.dataset.resourceSection;
    if (node.dataset.resourceOwner) return 'owner:' + node.dataset.resourceOwner;
    if (node.dataset.resourceRow) return 'row:' + node.dataset.resourceRow;
    if (node.dataset.pfKey) return 'key:' + node.dataset.pfKey;
    if (node.id) return 'id:' + node.id;
    return '';
  }

  function _removeNode(node) {
    if (!node) return;
    if (typeof node.remove === 'function') node.remove();
    else if (node.parentNode) node.parentNode.removeChild(node);
  }

  function _replaceNode(parent, current, next) {
    if (typeof parent.replaceChild === 'function') parent.replaceChild(next, current);
    else {
      parent.insertBefore(next, current);
      _removeNode(current);
    }
  }

  function _replaceChildren(parent, children) {
    if (typeof parent.replaceChildren === 'function') {
      parent.replaceChildren.apply(parent, children || []);
      return;
    }
    while (parent.firstChild) parent.removeChild(parent.firstChild);
    (children || []).forEach(function(child) { parent.appendChild(child); });
  }

  function _syncAttributes(current, next, preserved) {
    const skip = new Set(preserved || []);
    _attributes(current).forEach(function(attr) {
      if (!skip.has(attr.name) && !next.hasAttribute(attr.name)) {
        current.removeAttribute(attr.name);
      }
    });
    _attributes(next).forEach(function(attr) {
      if (!skip.has(attr.name) && current.getAttribute(attr.name) !== attr.value) {
        current.setAttribute(attr.name, attr.value);
      }
    });
  }

  function _sameMarkup(current, next) {
    if (!current || !next || current.tagName !== next.tagName
        || current.className !== next.className
        || current.innerHTML !== next.innerHTML) return false;
    const currentAttrs = _attributes(current);
    const nextAttrs = _attributes(next);
    if (currentAttrs.length !== nextAttrs.length) return false;
    return nextAttrs.every(function(attr) {
      return current.getAttribute(attr.name) === attr.value;
    });
  }

  function _patchKeyedNode(current, next) {
    if (_sameMarkup(current, next)) return;
    _syncAttributes(current, next);
    const currentChildren = Array.from(current.children || []);
    const nextChildren = Array.from(next.children || []);
    if (!currentChildren.length && !nextChildren.length) {
      current.textContent = next.textContent;
      return;
    }
    _patchChildren(current, next);
  }

  function _patchChildren(currentParent, nextParent) {
    const currentChildren = Array.from(currentParent.children || []);
    const nextChildren = Array.from(nextParent.children || []);
    const byKey = new Map();
    const unkeyed = [];
    currentChildren.forEach(function(node) {
      const key = _nodeKey(node);
      if (key) byKey.set(key, node);
      else unkeyed.push(node);
    });
    nextChildren.forEach(function(next) {
      const key = _nodeKey(next);
      const current = byKey.get(key);
      let node = next;
      if (current) {
        byKey.delete(key);
        node = current;
        if (next.dataset && next.dataset.resourcePreserve === 'true') {
          _syncAttributes(current, next, ['data-resource-preserve']);
        } else if (key.indexOf('section:') === 0) {
          _patchSection(current, next);
        } else if (key.indexOf('key:') === 0) {
          _patchKeyedNode(current, next);
        } else if (!_sameMarkup(current, next)) {
          _replaceNode(currentParent, current, next);
          node = next;
        }
      }
      currentParent.appendChild(node);
    });
    byKey.forEach(_removeNode);
    unkeyed.forEach(_removeNode);
  }

  function _destroyDisclosure(sectionId) {
    const record = disclosures.get(sectionId);
    if (!record) return;
    record.controller.destroy();
    disclosures.delete(sectionId);
  }

  function _patchSection(current, next) {
    const sectionId = next.dataset.resourceSection;
    const currentHeader = current.querySelector('.resource-section-header-row');
    const nextHeader = next.querySelector('.resource-section-header-row');
    const currentBody = current.querySelector('.resource-section-body');
    const nextBody = next.querySelector('.resource-section-body');
    _syncAttributes(current, next);

    if (!currentHeader || !nextHeader || !currentBody || !nextBody) {
      _destroyDisclosure(sectionId);
      _patchChildren(current, next);
      return;
    }
    if (currentHeader.innerHTML !== nextHeader.innerHTML) {
      _destroyDisclosure(sectionId);
      _replaceNode(current, currentHeader, nextHeader);
    }
    _syncAttributes(currentBody, nextBody, [
      'aria-hidden', 'data-pf-disclosure-state', 'hidden', 'inert', 'style',
    ]);
    _patchChildren(currentBody, nextBody);
  }

  function _initDisclosure(section) {
    const sectionId = section && section.dataset && section.dataset.resourceSection;
    if (!sectionId) return null;
    const trigger = section.querySelector('.resource-section-toggle');
    const panel = section.querySelector('.resource-section-body');
    if (!trigger || !panel) return null;
    const current = disclosures.get(sectionId);
    if (current && current.section === section && current.trigger === trigger
        && current.panel === panel) return current.controller;
    if (current) current.controller.destroy();
    const controller = root.pfDisclosure.create({
      trigger: trigger,
      panel: panel,
      open: !root._isSectionCollapsed(sectionId),
    });
    disclosures.set(sectionId, {
      section: section, trigger: trigger, panel: panel, controller: controller,
    });
    return controller;
  }

  function _initAll(container) {
    Array.from(container.querySelectorAll('[data-resource-section]')).forEach(_initDisclosure);
    disclosures.forEach(function(record, sectionId) {
      if (!record.section.isConnected) _destroyDisclosure(sectionId);
    });
  }

  function setSectionOpen(sectionId, open) {
    const body = root.document.getElementById('res-section-' + sectionId);
    let section = body;
    while (section && !(section.dataset && section.dataset.resourceSection)) {
      section = section.parentNode;
    }
    const controller = section && _initDisclosure(section);
    return controller ? controller.set(!!open) : Promise.resolve({status: 'missing'});
  }

  function patchContent(container, html) {
    if (!container) return {changed: false};
    const scrollTop = container.scrollTop;
    const template = root.document.createElement('template');
    template.innerHTML = String(html || '');
    _patchChildren(container, template.content);
    _initAll(container);
    container.scrollTop = scrollTop;
    return {changed: true};
  }

  function patchHtml(container, html) {
    if (!container) return {changed: false};
    const template = root.document.createElement('template');
    template.innerHTML = String(html || '');
    _patchChildren(container, template.content);
    return {changed: true};
  }

  function clear(container) {
    disclosures.forEach(function(record) { record.controller.destroy(); });
    disclosures.clear();
    if (container) _replaceChildren(container, []);
  }

  root.pfResources = {
    patchContent: patchContent,
    patchChildren: _patchChildren,
    setSectionOpen: setSectionOpen,
    clear: clear,
  };
  root.pfDomPatch = {
    patchChildren: _patchChildren,
    patchHtml: patchHtml,
  };
  root._patchResourcesContent = patchContent;
  root._setResourceSectionOpen = setSectionOpen;
  root._clearResourcesContent = clear;
})(typeof window !== 'undefined' ? window : globalThis);
