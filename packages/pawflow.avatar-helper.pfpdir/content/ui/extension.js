// PawFlow Avatar Helper — non-destructive semantic interface guidance.
(function () {
  'use strict';

  var PACKAGE_ID = 'pawflow.avatar-helper';
  var NODE_ID = 'ui.guide';
  var TARGET_IDS = [
    'sidebar',
    'conversations',
    'resources',
    'pfp.repository',
    'actions',
    'plans',
    'files',
    'agent',
    'composer',
  ];
  var TARGETS = {
    sidebar: {label: 'Conversation sidebar', elementId: 'sidebar'},
    conversations: {label: 'Conversation list', elementId: 'convList'},
    resources: {label: 'Resources', elementId: 'resourcesPanel'},
    'pfp.repository': {label: 'PFP repository', elementId: 'pfpDepotPanel'},
    actions: {label: 'Actions menu', elementId: 'actionMenu'},
    plans: {label: 'Plans panel', elementId: 'plansPanel'},
    files: {label: 'Conversation files', elementId: 'filesPanel'},
    agent: {label: 'Selected agent', elementId: 'activeAgentBadge'},
    composer: {label: 'Message composer', elementId: 'input'},
  };
  var state = {
    target: '',
    message: '',
    element: null,
    callout: null,
  };

  function targetSpec(target) {
    var normalized = String(target || '');
    var spec = TARGETS[normalized];
    if (!spec) throw new Error('Unknown PawFlow UI target: ' + normalized);
    return spec;
  }

  function targetElement(target) {
    var spec = targetSpec(target);
    return document.getElementById(spec.elementId);
  }

  function isVisible(element) {
    if (!element || element.hidden) return false;
    if (element.style && element.style.display === 'none') return false;
    if (typeof window.getComputedStyle === 'function') {
      var style = window.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    if (typeof element.getClientRects === 'function'
        && element.getClientRects().length === 0) return false;
    return true;
  }

  function targetSnapshot(target) {
    var spec = targetSpec(target);
    var element = targetElement(target);
    return {
      id: target,
      label: spec.label,
      present: !!element,
      visible: isVisible(element),
    };
  }

  function snapshot() {
    var sidebar = document.getElementById('sidebar');
    var plans = document.getElementById('plansPanel');
    var files = document.getElementById('filesPanel');
    var actions = document.getElementById('actionMenu');
    return {
      activeTarget: state.target,
      message: state.message,
      surfaces: {
        sidebarOpen: !!sidebar && !sidebar.classList.contains('collapsed'),
        plansOpen: isVisible(plans),
        filesOpen: isVisible(files),
        actionsOpen: !!actions && actions.classList.contains('open'),
      },
      targets: TARGET_IDS.map(targetSnapshot),
    };
  }

  function openSidebar() {
    var sidebar = document.getElementById('sidebar');
    if (!sidebar) throw new Error('PawFlow sidebar is unavailable');
    sidebar.classList.remove('collapsed');
    if (typeof _syncToggleBtn === 'function') _syncToggleBtn();
  }

  function openResources() {
    openSidebar();
    var panel = document.getElementById('resourcesPanel');
    var content = document.getElementById('resourcesContent');
    if (!panel || !content) throw new Error('PawFlow resources are unavailable');
    if (typeof setSidebarSection === 'function') setSidebarSection('resources');
    else panel.classList.add('active');
    if (typeof loadResources === 'function') loadResources();
  }

  function openActions() {
    var wrap = document.getElementById('actionMenuWrap');
    var menu = document.getElementById('actionMenu');
    if (!wrap || !menu || !isVisible(wrap)) {
      throw new Error('PawFlow actions menu is unavailable');
    }
    if (!menu.classList.contains('open')) {
      if (typeof toggleActionMenu === 'function') toggleActionMenu();
      else menu.classList.add('open');
    }
  }

  function openPanel(elementId, loaderName) {
    var panel = document.getElementById(elementId);
    if (!panel) throw new Error('PawFlow panel is unavailable: ' + elementId);
    panel.style.display = 'block';
    var loader = window[loaderName];
    if (typeof loader === 'function') return Promise.resolve(loader());
    return Promise.resolve();
  }

  function waitForTarget(target, timeoutMs) {
    var started = Date.now();
    return new Promise(function (resolve, reject) {
      function check() {
        var element = targetElement(target);
        if (element && isVisible(element)) {
          resolve(element);
          return;
        }
        if (Date.now() - started >= timeoutMs) {
          reject(new Error('PawFlow UI target is not visible: ' + target));
          return;
        }
        window.setTimeout(check, 50);
      }
      check();
    });
  }

  function openTarget(target) {
    targetSpec(target);
    var work;
    if (target === 'sidebar') {
      openSidebar();
      work = Promise.resolve();
    } else if (target === 'conversations') {
      openSidebar();
      if (typeof setSidebarSection === 'function') setSidebarSection('conversations');
      work = Promise.resolve();
    } else if (target === 'resources' || target === 'pfp.repository') {
      openResources();
      work = Promise.resolve();
    } else if (target === 'actions') {
      openActions();
      work = Promise.resolve();
    } else if (target === 'plans') {
      work = openPanel('plansPanel', 'loadPlans');
    } else if (target === 'files') {
      work = openPanel('filesPanel', 'loadConvFiles');
    } else {
      work = Promise.resolve();
    }
    return work.then(function () {
      return waitForTarget(target, target === 'pfp.repository' ? 2500 : 500);
    }).then(function (element) {
      if (typeof element.scrollIntoView === 'function') {
        element.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
      return targetSnapshot(target);
    });
  }

  function positionCallout() {
    if (!state.element || !state.callout
        || typeof state.element.getBoundingClientRect !== 'function') return;
    var rect = state.element.getBoundingClientRect();
    var callout = state.callout;
    var width = callout.offsetWidth || 280;
    var height = callout.offsetHeight || 50;
    var left = Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12));
    var below = rect.bottom + 10;
    var top = below + height <= window.innerHeight - 12
      ? below : Math.max(12, rect.top - height - 10);
    callout.style.left = left + 'px';
    callout.style.top = top + 'px';
  }

  function clearFocus() {
    if (state.element && state.element.classList) {
      state.element.classList.remove('pf-avatar-helper-focus');
      state.element.removeAttribute('data-pf-avatar-helper-target');
    }
    if (state.callout && state.callout.parentNode) {
      state.callout.parentNode.removeChild(state.callout);
    }
    window.removeEventListener('resize', positionCallout);
    window.removeEventListener('scroll', positionCallout, true);
    state.target = '';
    state.message = '';
    state.element = null;
    state.callout = null;
    return {cleared: true};
  }

  function focusTarget(target, message) {
    var cleanMessage = String(message || targetSpec(target).label).trim();
    if (cleanMessage.length > 500) {
      throw new Error('PawFlow helper message is too long');
    }
    clearFocus();
    return waitForTarget(target, 500).then(function (element) {
      if (typeof element.scrollIntoView === 'function') {
        element.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
      element.classList.add('pf-avatar-helper-focus');
      element.setAttribute('data-pf-avatar-helper-target', target);
      var callout = document.createElement('div');
      callout.className = 'pf-avatar-helper-callout';
      callout.setAttribute('role', 'status');
      callout.textContent = cleanMessage;
      document.body.appendChild(callout);
      state.target = target;
      state.message = cleanMessage;
      state.element = element;
      state.callout = callout;
      positionCallout();
      window.addEventListener('resize', positionCallout);
      window.addEventListener('scroll', positionCallout, true);
      return {
        target: targetSnapshot(target),
        message: cleanMessage,
      };
    });
  }

  function guideTarget(target, message) {
    return openTarget(target).then(function () {
      return focusTarget(target, message);
    });
  }

  function makeButton() {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'pf-avatar-helper-button';
    button.textContent = '?';
    button.title = 'PawFlow interface helper';
    button.setAttribute('aria-label', 'PawFlow interface helper');
    button.addEventListener('click', function () {
      if (state.target) clearFocus();
      else focusTarget('composer', 'Ask the PawFlow Helper what you want to do.').catch(function () {});
    });
    return button;
  }

  window.pawflow.register(PACKAGE_ID, function (pfp) {
    pfp.ui.slot('header_actions', 'helper.guide', makeButton);

    if (pfp.semantic) {
      pfp.semantic.register({
        id: NODE_ID,
        role: 'application',
        label: 'PawFlow interface guide',
        parent: 'pawflow.chat',
        state: snapshot,
        actions: {
          describe: {
            parameters: {},
            run: function () { return snapshot(); },
          },
          open: {
            parameters: {
              target: {type: 'string', required: true, enum: TARGET_IDS},
            },
            run: function (params) { return openTarget(params.target); },
          },
          focus: {
            parameters: {
              target: {type: 'string', required: true, enum: TARGET_IDS},
              message: {type: 'string'},
            },
            run: function (params) {
              return focusTarget(params.target, params.message);
            },
          },
          guide: {
            parameters: {
              target: {type: 'string', required: true, enum: TARGET_IDS},
              message: {type: 'string'},
            },
            run: function (params) {
              return guideTarget(params.target, params.message);
            },
          },
          clear: {
            parameters: {},
            run: clearFocus,
          },
        },
      });
    }

    pfp.on('conversation_changed', clearFocus);
    pfp.on('agent_changed', clearFocus);
    pfp.on('shutdown', clearFocus);
  });
}());
