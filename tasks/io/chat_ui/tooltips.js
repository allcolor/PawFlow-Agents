// Shared CSS tooltip portal for compact icon controls. The tooltip lives outside
// scrollable docks so revealing it cannot change their overflow dimensions.
(function() {
  const TARGET_SELECTOR = 'button, .action-dock-menu > .action-menu-item, .conversation-control-button, '
    + '.header-dock-item, .pf-grip, .hdr-icon-btn, [data-pf-title]';
  let activeTarget = null;
  let pendingTarget = null;
  let showTimer = 0;
  let hideTimer = 0;
  let lastHiddenAt = 0;

  function clearTimer(name) {
    const timer = name === 'show' ? showTimer : hideTimer;
    if (timer) window.clearTimeout(timer);
    if (name === 'show') showTimer = 0;
    else hideTimer = 0;
  }

  function setDescribedBy(target, enabled) {
    if (!target) return;
    const id = 'pfCssTooltip';
    const ids = String(target.getAttribute('aria-describedby') || '')
      .split(/\s+/).filter(Boolean).filter(value => value !== id);
    if (enabled) ids.push(id);
    if (ids.length) target.setAttribute('aria-describedby', ids.join(' '));
    else target.removeAttribute('aria-describedby');
  }

  function tooltipElement() {
    return document.getElementById('pfCssTooltip');
  }

  function tooltipText(target) {
    const label = target.querySelector('.ami-label');
    const desc = target.querySelector('.ami-desc');
    const buttonText = target.matches && target.matches('button')
      ? (target.textContent || target.value || '') : '';
    return {
      label: (label ? label.textContent
              : target.dataset.pfTitle || target.getAttribute('aria-label')
                || buttonText).trim(),
      desc: (desc ? desc.textContent : '').trim(),
    };
  }

  // ONE tooltip look everywhere: every native `title` in the app is adopted
  // on first contact. The text moves to data-pf-title so the browser tooltip
  // never paints and the shared CSS tooltip takes over. Titles re-set later
  // (i18n language switch, dynamic updates) are re-adopted on the next hover,
  // which always fires before the browser's own tooltip delay.
  function adoptNativeTitles(node) {
    let el = node && node.closest ? node.closest('[title]') : null;
    while (el) {
      const text = (el.getAttribute('title') || '').trim();
      el.removeAttribute('title');
      if (text) el.dataset.pfTitle = text;
      el = el.parentElement && el.parentElement.closest
        ? el.parentElement.closest('[title]') : null;
    }
  }

  function hideTooltip() {
    clearTimer('show');
    clearTimer('hide');
    pendingTarget = null;
    const tooltip = tooltipElement();
    if (!tooltip) return;
    if (window.pfFloatingLayer) {
      window.pfFloatingLayer.close('tooltip', {
        reason: 'hide',
        restoreFocus: false,
      });
      return;
    }
    activeTarget = null;
    lastHiddenAt = Date.now();
    tooltip.classList.remove('visible');
    tooltip.setAttribute('aria-hidden', 'true');
  }

  function showTooltip(target) {
    const tooltip = tooltipElement();
    if (!tooltip || !target) return;
    const text = tooltipText(target);
    if (!text.label) {
      hideTooltip();
      return;
    }

    tooltip.replaceChildren();
    const label = document.createElement('div');
    label.className = 'pf-css-tooltip-label';
    label.textContent = text.label;
    tooltip.appendChild(label);
    if (text.desc) {
      const desc = document.createElement('div');
      desc.className = 'pf-css-tooltip-desc';
      desc.textContent = text.desc;
      tooltip.appendChild(desc);
    }

    const dock = target.closest('.action-dock-menu');
    const verticalDock = dock && window.getComputedStyle(dock).flexDirection === 'column';
    if (activeTarget && activeTarget !== target) {
      setDescribedBy(activeTarget, false);
    }
    if (window.pfFloatingLayer) {
      window.pfFloatingLayer.close('tooltip', {
        reason: 'replaced',
        restoreFocus: false,
      });
    }
    activeTarget = target;
    pendingTarget = null;
    setDescribedBy(target, true);
    tooltip.classList.add('visible');
    tooltip.setAttribute('aria-hidden', 'false');
    if (window.pfFloatingLayer) {
      window.pfFloatingLayer.open({
        channel: 'tooltip',
        element: tooltip,
        trigger: target,
        placement: verticalDock ? 'left' : 'top',
        removeOnClose: false,
        restoreFocus: false,
        closeOnSelect: false,
        keepOnTrigger: false,
        onClose: function() {
          setDescribedBy(target, false);
          if (activeTarget === target) activeTarget = null;
          lastHiddenAt = Date.now();
          tooltip.classList.remove('visible');
          tooltip.setAttribute('aria-hidden', 'true');
        },
      });
    }
  }

  function queueTooltip(target, immediate) {
    clearTimer('hide');
    if (!target || target === activeTarget || target === pendingTarget) return;
    clearTimer('show');
    pendingTarget = target;
    const grouped = activeTarget || Date.now() - lastHiddenAt < 300;
    const delay = immediate || grouped ? 0 : 140;
    showTimer = window.setTimeout(function() {
      showTimer = 0;
      if (pendingTarget === target) showTooltip(target);
    }, delay);
  }

  function queueHide() {
    clearTimer('show');
    pendingTarget = null;
    clearTimer('hide');
    hideTimer = window.setTimeout(hideTooltip, 60);
  }

  function targetFrom(node) {
    return node && node.closest ? node.closest(TARGET_SELECTOR) : null;
  }

  document.addEventListener('mouseover', function(event) {
    adoptNativeTitles(event.target);
    const target = targetFrom(event.target);
    if (target && target !== activeTarget) queueTooltip(target, false);
  });
  document.addEventListener('mouseout', function(event) {
    if (activeTarget && !activeTarget.contains(event.relatedTarget)) queueHide();
    else if (pendingTarget && !pendingTarget.contains(event.relatedTarget)) queueHide();
  });
  document.addEventListener('focusin', function(event) {
    adoptNativeTitles(event.target);
    const target = targetFrom(event.target);
    if (target) queueTooltip(target, true);
  });
  document.addEventListener('focusout', function(event) {
    if (activeTarget && !activeTarget.contains(event.relatedTarget)) queueHide();
  });
})();
