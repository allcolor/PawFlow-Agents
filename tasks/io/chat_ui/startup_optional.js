// Only the usage dashboard has an isolated public entry point. Its budget
// refresh hook is already guarded by usage_cost.js and requires an open panel.
(() => {
  const ready = new Promise(resolve => {
    if (document.readyState === 'complete') resolve();
    else document.addEventListener('DOMContentLoaded', resolve, { once: true });
  });
  let pending = null;
  const open = function(...args) {
    if (pending) return pending;
    pending = ready.then(() => new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = window.PAWFLOW_LAZY_URLS['usage_dashboard.js'];
      script.onload = () => {
        if (window.showUsageDashboard === open) {
          // An execution failure can leave global declarations installed.
          // Reload through the existing recovery path instead of evaluating twice.
          window.__pawflowAssetLoadFailed();
          reject(new Error('Usage dashboard did not initialize'));
          return;
        }
        resolve();
      };
      script.onerror = () => {
        script.remove();
        reject(new Error('Could not load the usage dashboard. Please try again.'));
      };
      document.head.appendChild(script);
    })).then(() => window.showUsageDashboard(...args)).catch(error => {
      console.warn('[startup]', error);
      if (typeof addMsg === 'function') addMsg('error', error.message);
      return false;
    }).finally(() => { pending = null; });
    return pending;
  };
  window.showUsageDashboard = open;
})();
