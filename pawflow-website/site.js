const header = document.querySelector('[data-header]');
const nav = document.querySelector('[data-nav]');
const toggle = document.querySelector('[data-nav-toggle]');

// Fallback when the GitHub API is unreachable or rate-limited. Keep the
// version in sync with the latest release tag on a best-effort basis — the
// live fetch below overrides it on every page load.
const PAWFLOW_RELEASE = {
  version: '1.0.0-beta.12',
  repo: 'https://github.com/allcolor/PawFlow-Agents',
};

// Asset names as published on releases (dots, not spaces, in the desktop
// names). Used to build fallback URLs and to match live asset lists.
const releaseAssets = (version) => ({
  installer: `pawflow-install-${version}.zip`,
  pawcodeLinuxTar: `pawcode-${version}-linux-x86_64.tar.gz`,
  pawcodeLinuxZip: `pawcode-${version}-linux-x86_64.zip`,
  pawcodeWindowsZip: `pawcode-${version}-win-x86_64.zip`,
  pawcodeDeb: `pawcode_${version}_amd64.deb`,
  relayCliLinuxTar: `pawflow-relay-cli-${version}-linux-x86_64.tar.gz`,
  relayCliLinuxZip: `pawflow-relay-cli-${version}-linux-x86_64.zip`,
  relayCliWindowsZip: `pawflow-relay-cli-${version}-win-x86_64.zip`,
  relayDesktopTar: `pawflow-relay-desktop-${version}.tar.gz`,
  relayDesktopDeb: `pawflow-relay-desktop_${version}_amd64.deb`,
  relayDesktopAppImage: `PawFlow.Relay.Desktop-${version}.AppImage`,
  relayDesktopWindows: `PawFlow.Relay.Desktop.Setup.${version}.exe`,
  relayDesktopWindowsZip: `PawFlow.Relay.Desktop-${version}-win.zip`,
  vscodeVsix: `pawflow-vscode-${version}.vsix`,
});

// Patterns to pick each download out of the live release asset list, so
// renamed or re-versioned assets keep resolving without a website deploy.
const ASSET_PATTERNS = {
  installer: /^pawflow-install-.*\.zip$/,
  pawcodeLinuxTar: /^pawcode-.*-linux-x86_64\.tar\.gz$/,
  pawcodeLinuxZip: /^pawcode-.*-linux-x86_64\.zip$/,
  pawcodeWindowsZip: /^pawcode-.*-win-x86_64\.zip$/,
  pawcodeDeb: /^pawcode_.*_amd64\.deb$/,
  relayCliLinuxTar: /^pawflow-relay-cli-.*-linux-x86_64\.tar\.gz$/,
  relayCliLinuxZip: /^pawflow-relay-cli-.*-linux-x86_64\.zip$/,
  relayCliWindowsZip: /^pawflow-relay-cli-.*-win-x86_64\.zip$/,
  relayDesktopTar: /^pawflow-relay-desktop-.*\.tar\.gz$/,
  relayDesktopDeb: /^pawflow-relay-desktop_.*_amd64\.deb$/,
  relayDesktopAppImage: /Relay[ .]Desktop-.*\.AppImage$/,
  relayDesktopWindows: /Relay[ .]Desktop[ .]Setup[ .].*\.exe$/,
  relayDesktopWindowsZip: /Relay[ .]Desktop-.*-win\.zip$/,
  vscodeVsix: /^pawflow-vscode-.*\.vsix$/,
};

const release = {
  ...PAWFLOW_RELEASE,
  tagUrl: `${PAWFLOW_RELEASE.repo}/releases/tag/${PAWFLOW_RELEASE.version}`,
  assets: releaseAssets(PAWFLOW_RELEASE.version),
};

function releaseDownloadUrl(assetName) {
  return `${release.repo}/releases/download/${release.version}/${encodeURIComponent(assetName)}`;
}

function renderReleaseReferences() {
  document.querySelectorAll('[data-release-version]').forEach((node) => {
    node.textContent = release.version;
  });
  document.querySelectorAll('[data-release-url]').forEach((node) => {
    node.setAttribute('href', release.tagUrl);
  });
  document.querySelectorAll('[data-release-download]').forEach((node) => {
    const key = node.dataset.releaseDownload;
    const asset = release.assets[key];
    if (!asset) return;
    node.setAttribute('href', releaseDownloadUrl(asset));
    const nameNode = node.querySelector('[data-release-asset]');
    if (nameNode) nameNode.textContent = asset;
  });
  document.querySelectorAll('[data-install-command]').forEach((node) => {
    const version = release.version;
    const installer = release.assets.installer;
    node.innerHTML = `<code>PAWFLOW_VERSION="${version}"
curl -L -o "${installer}" \\
  "${releaseDownloadUrl(installer)}"
unzip "${installer}"
cd "pawflow-install-${version}"
bash scripts/install-pawflow.sh --port 19990 --pull-images</code>`;
  });
  document.querySelectorAll('[data-install-command-compact]').forEach((node) => {
    const installer = release.assets.installer;
    const dir = installer.replace(/\.zip$/, '');
    node.innerHTML = `<code>curl -L -O "${releaseDownloadUrl(installer)}" && unzip "${installer}" && cd "${dir}" && bash scripts/install-pawflow.sh --port 19990 --pull-images</code>`;
  });
}

renderReleaseReferences();

// Resolve the CURRENT release from the GitHub API and re-render. The static
// block above is only the offline/rate-limited fallback — hardcoded versions
// went stale and served 404 download links.
(async () => {
  try {
    const resp = await fetch(
      'https://api.github.com/repos/allcolor/PawFlow-Agents/releases/latest',
      { headers: { Accept: 'application/vnd.github+json' } });
    if (!resp.ok) return;
    const data = await resp.json();
    const tag = (data.tag_name || '').trim();
    if (!tag) return;
    const liveNames = (data.assets || []).map((a) => a.name);
    release.version = tag;
    release.tagUrl = data.html_url || `${release.repo}/releases/tag/${tag}`;
    const templated = releaseAssets(tag);
    const resolved = {};
    Object.keys(ASSET_PATTERNS).forEach((key) => {
      resolved[key] = liveNames.find((n) => ASSET_PATTERNS[key].test(n)) || templated[key];
    });
    release.assets = resolved;
    renderReleaseReferences();
  } catch (error) {
    // Offline or rate-limited: the fallback render stays in place.
  }
})();

function setScrolled() {
  if (!header) return;
  header.classList.toggle('is-scrolled', window.scrollY > 8);
}

setScrolled();
window.addEventListener('scroll', setScrolled, { passive: true });

if (toggle && nav) {
  toggle.addEventListener('click', () => {
    const open = nav.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', String(open));
  });
}

const currentPage = document.body.dataset.page;
document.querySelectorAll('.site-nav a').forEach((link) => {
  const href = link.getAttribute('href') || '';
  if (currentPage && href.startsWith(currentPage + '.html')) link.classList.add('is-active');
  if (currentPage === 'home' && href === 'index.html') link.classList.add('is-active');
});

const revealItems = document.querySelectorAll('[data-reveal]');
if ('IntersectionObserver' in window) {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  revealItems.forEach((item) => observer.observe(item));
} else {
  revealItems.forEach((item) => item.classList.add('is-visible'));
}

document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    const target = document.querySelector(button.dataset.copy);
    if (!target) return;
    const text = target.innerText.trim();
    try {
      await navigator.clipboard.writeText(text);
      const original = button.textContent;
      button.textContent = 'Copied';
      setTimeout(() => { button.textContent = original; }, 1200);
    } catch (error) {
      button.textContent = 'Select';
    }
  });
});

// ── Help widget (talks to the web_help_bot flow: POST /api/help) ──────
// Same-origin endpoint, fronted by Caddy (keep the listener port private).
// Disable on a page with <body data-no-help>; override the path with
// <body data-help-endpoint="/api/help">.
(function initHelpWidget() {
  if (document.body.dataset.noHelp !== undefined) return;
  const ENDPOINT = document.body.dataset.helpEndpoint || '/api/help';
  const STATUS = {
    400: 'Please type a message first.',
    429: 'You are sending messages too fast. Please wait a moment.',
    503: 'The help bot is temporarily unavailable. Please try again later.',
    504: 'Sorry, this took too long. Please try again.',
  };

  const SVG_NS = 'http://www.w3.org/2000/svg';
  function icon(paths, size) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    if (size) { svg.setAttribute('width', size); svg.setAttribute('height', size); }
    paths.forEach((d) => {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      svg.appendChild(p);
    });
    return svg;
  }

  const launcher = document.createElement('button');
  launcher.type = 'button';
  launcher.className = 'pf-help-launcher';
  launcher.setAttribute('aria-label', 'Open the PawFlow help chat');
  launcher.appendChild(icon(['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'], 18));
  launcher.appendChild(document.createTextNode('Ask PawFlow'));

  const panel = document.createElement('div');
  panel.className = 'pf-help-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'PawFlow help chat');
  panel.setAttribute('aria-modal', 'false');

  const head = document.createElement('div');
  head.className = 'pf-help-head';
  const dot = document.createElement('span');
  dot.className = 'pf-help-dot';
  const titles = document.createElement('div');
  titles.className = 'pf-help-titles';
  const h3 = document.createElement('h3');
  h3.textContent = 'PawFlow help';
  const sub = document.createElement('p');
  sub.textContent = 'Ask about install, flows, agents, tools.';
  titles.append(h3, sub);
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'pf-help-close';
  closeBtn.setAttribute('aria-label', 'Close help chat');
  closeBtn.appendChild(icon(['M18 6 6 18', 'M6 6l12 12'], 18));
  head.append(dot, titles, closeBtn);

  const log = document.createElement('div');
  log.className = 'pf-help-log';
  const intro = document.createElement('div');
  intro.className = 'pf-help-msg intro';
  intro.textContent = 'Hi! I am the PawFlow help bot. Ask me anything about running PawFlow.';
  log.appendChild(intro);

  const form = document.createElement('form');
  form.className = 'pf-help-form';
  const input = document.createElement('textarea');
  input.rows = 1;
  input.placeholder = 'Type your question...';
  input.setAttribute('aria-label', 'Your message');
  const send = document.createElement('button');
  send.type = 'submit';
  send.className = 'pf-help-send';
  send.textContent = 'Send';
  form.append(input, send);

  panel.append(head, log, form);
  document.body.append(launcher, panel);

  let busy = false;
  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function addMsg(role, text) {
    const el = document.createElement('div');
    el.className = 'pf-help-msg ' + role;
    el.textContent = text;
    log.appendChild(el);
    scrollDown();
    return el;
  }
  function showTyping() {
    const t = document.createElement('div');
    t.className = 'pf-help-typing';
    t.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
    log.appendChild(t);
    scrollDown();
    return t;
  }

  function open() {
    panel.classList.add('is-open');
    launcher.classList.add('is-hidden');
    pinFloating();
    setTimeout(() => input.focus(), 50);
  }
  function close() {
    panel.classList.remove('is-open');
    launcher.classList.remove('is-hidden');
    launcher.focus();
  }
  launcher.addEventListener('click', open);
  closeBtn.addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && panel.classList.contains('is-open')) close();
  });

  // ── Floating-window behaviour: drag by the header; resize via the CSS
  // grip (bottom-right). Only on wider viewports — on phones the panel stays
  // full-screen (see the max-width: 520px media query).
  const FLOAT_MIN_VW = 520;
  function floatable() { return window.innerWidth > FLOAT_MIN_VW; }
  function pinFloating() {
    // Switch from the default right/bottom anchoring to left/top so dragging
    // and the resize grip both behave like a normal window. Done once.
    if (!floatable() || panel.dataset.pinned) return;
    const r = panel.getBoundingClientRect();
    panel.style.left = r.left + 'px';
    panel.style.top = r.top + 'px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.dataset.pinned = '1';
  }
  let drag = null;
  head.addEventListener('pointerdown', (e) => {
    if (!floatable() || e.target.closest('.pf-help-close')) return;
    pinFloating();
    const r = panel.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    head.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  head.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const maxL = window.innerWidth - panel.offsetWidth;
    const maxT = window.innerHeight - panel.offsetHeight;
    panel.style.left = Math.max(0, Math.min(maxL, e.clientX - drag.dx)) + 'px';
    panel.style.top = Math.max(0, Math.min(maxT, e.clientY - drag.dy)) + 'px';
  });
  function endDrag(e) {
    if (!drag) return;
    drag = null;
    try { head.releasePointerCapture(e.pointerId); } catch (_) {}
  }
  head.addEventListener('pointerup', endDrag);
  head.addEventListener('pointercancel', endDrag);

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  async function ask(text) {
    busy = true;
    send.disabled = true;
    const typing = showTyping();
    try {
      const resp = await fetch(ENDPOINT, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      let data = {};
      try { data = await resp.json(); } catch (err) { data = {}; }
      typing.remove();
      if (resp.ok) {
        addMsg('bot', (data.response || '').trim() || 'No response.');
      } else {
        addMsg('error', data.error || STATUS[resp.status] || ('Something went wrong (' + resp.status + ').'));
      }
    } catch (err) {
      typing.remove();
      addMsg('error', 'Network error. Please check your connection and try again.');
    } finally {
      busy = false;
      send.disabled = false;
      input.focus();
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (busy) return;
    const text = input.value.trim();
    if (!text) return;
    addMsg('user', text);
    input.value = '';
    input.style.height = 'auto';
    ask(text);
  });
})();
