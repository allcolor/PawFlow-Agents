// Shared CSS tooltip portal for compact icon controls. The tooltip lives outside
// scrollable docks so revealing it cannot change their overflow dimensions.
(function() {
  const TARGET_SELECTOR = '.action-dock-menu > .action-menu-item, .prompt-controls-row button, '
    + '.header-dock-item, .pf-grip, .hdr-icon-btn, [data-pf-title]';
  let activeTarget = null;

  function tooltipElement() {
    return document.getElementById('pfCssTooltip');
  }

  function tooltipText(target) {
    const label = target.querySelector('.ami-label');
    const desc = target.querySelector('.ami-desc');
    return {
      label: (label ? label.textContent
              : target.dataset.pfTitle || target.getAttribute('aria-label') || '').trim(),
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
    const tooltip = tooltipElement();
    activeTarget = null;
    if (!tooltip) return;
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

    activeTarget = target;
    tooltip.classList.add('visible');
    tooltip.setAttribute('aria-hidden', 'false');

    const gap = 10;
    const edge = 8;
    const targetRect = target.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const dock = target.closest('.action-dock-menu');
    const verticalDock = dock && window.getComputedStyle(dock).flexDirection === 'column';
    let left;
    let top;

    if (verticalDock) {
      left = targetRect.left - tooltipRect.width - gap;
      top = targetRect.top + (targetRect.height - tooltipRect.height) / 2;
    } else {
      left = targetRect.left + (targetRect.width - tooltipRect.width) / 2;
      top = targetRect.top - tooltipRect.height - gap;
      if (top < edge) top = targetRect.bottom + gap;
    }

    left = Math.max(edge, Math.min(left, window.innerWidth - tooltipRect.width - edge));
    top = Math.max(edge, Math.min(top, window.innerHeight - tooltipRect.height - edge));
    tooltip.style.left = Math.round(left) + 'px';
    tooltip.style.top = Math.round(top) + 'px';
  }

  function targetFrom(node) {
    return node && node.closest ? node.closest(TARGET_SELECTOR) : null;
  }

  document.addEventListener('mouseover', function(event) {
    adoptNativeTitles(event.target);
    const target = targetFrom(event.target);
    if (target && target !== activeTarget) showTooltip(target);
  });
  document.addEventListener('mouseout', function(event) {
    if (activeTarget && !activeTarget.contains(event.relatedTarget)) hideTooltip();
  });
  document.addEventListener('focusin', function(event) {
    adoptNativeTitles(event.target);
    const target = targetFrom(event.target);
    if (target) showTooltip(target);
  });
  document.addEventListener('focusout', function(event) {
    if (activeTarget && !activeTarget.contains(event.relatedTarget)) hideTooltip();
  });
  document.addEventListener('click', hideTooltip);
  window.addEventListener('resize', hideTooltip);
  window.addEventListener('scroll', hideTooltip, true);
})();
