const header = document.querySelector('[data-header]');
const nav = document.querySelector('[data-nav]');
const toggle = document.querySelector('[data-nav-toggle]');

// Fallback when the GitHub API is unreachable or rate-limited. Keep the
// version in sync with the latest release tag on a best-effort basis — the
// live fetch below overrides it on every page load.
const PAWFLOW_RELEASE = {
  version: '1.0.0-beta.241',
  repo: 'https://github.com/allcolor/PawFlow-Agents',
};

// Asset names as published on releases (dots, not spaces, in the desktop
// names). Used to build fallback URLs and to match live asset lists.
const releaseAssets = (version) => ({
  installer: `pawflow-install-${version}.zip`,
  mcpClientZip: `pawflow-mcp-client-${version}.zip`,
  mcpClientTar: `pawflow-mcp-client-${version}.tar.gz`,
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
  androidApk: `pawflow-android-${version}-debug.apk`,
});

// Patterns to pick each download out of the live release asset list, so
// renamed or re-versioned assets keep resolving without a website deploy.
const ASSET_PATTERNS = {
  installer: /^pawflow-install-.*\.zip$/,
  mcpClientZip: /^pawflow-mcp-client-.*\.zip$/,
  mcpClientTar: /^pawflow-mcp-client-.*\.tar\.gz$/,
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
  androidApk: /^pawflow-android-.*\.apk$/,
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

// Ambient soundtrack. Browsers may reject audible autoplay before the first
// user gesture; in that case the same requested playback starts on the first
// click, key press, or wheel gesture. A visitor's explicit mute always wins.
(function initAmbientSound() {
  const preferenceKey = 'pawflow-site-sound';
  const playbackKey = 'pawflow-site-sound-playback';
  const audio = document.createElement('audio');
  const toggle = document.createElement('button');
  let wanted = true;
  let blocked = false;
  let savedPlayback = null;
  let lastSavedAt = 0;

  try {
    wanted = localStorage.getItem(preferenceKey) !== 'off';
    savedPlayback = JSON.parse(sessionStorage.getItem(playbackKey) || 'null');
  } catch (_) {}

  audio.className = 'site-ambient-audio';
  audio.src = 'assets/media/audio/music_suno_brand.mp3';
  audio.autoplay = true;
  audio.loop = true;
  audio.preload = 'auto';
  audio.volume = .2;
  audio.setAttribute('playsinline', '');

  toggle.className = 'site-sound-toggle';
  toggle.type = 'button';
  toggle.innerHTML = `
    <span class="site-sound-bars" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="site-sound-label">Sound on</span>`;

  function update() {
    const playing = wanted && !audio.paused;
    const label = !wanted ? 'Sound off' : blocked && !playing ? 'Start sound' : 'Sound on';
    toggle.classList.toggle('is-on', playing);
    toggle.classList.toggle('is-blocked', blocked && wanted && !playing);
    toggle.setAttribute('aria-pressed', String(wanted));
    toggle.setAttribute('aria-label', label + '. Toggle background music.');
    toggle.querySelector('.site-sound-label').textContent = label;
  }

  function remember() {
    try { localStorage.setItem(preferenceKey, wanted ? 'on' : 'off'); } catch (_) {}
  }

  function savePosition(force = false) {
    const now = Date.now();
    if (!force && now - lastSavedAt < 750) return;
    if (!Number.isFinite(audio.currentTime)) return;
    lastSavedAt = now;
    try {
      sessionStorage.setItem(playbackKey, JSON.stringify({
        position: audio.currentTime,
        savedAt: now,
        playing: wanted && !audio.paused && !document.hidden,
      }));
    } catch (_) {}
  }

  function restorePosition() {
    if (savedPlayback && Number.isFinite(savedPlayback.position) &&
        Number.isFinite(audio.duration) && audio.duration > 0) {
      const transit = savedPlayback.playing && Number.isFinite(savedPlayback.savedAt)
        ? Math.max(0, (Date.now() - savedPlayback.savedAt) / 1000)
        : 0;
      audio.currentTime = (savedPlayback.position + transit) % audio.duration;
    }
    if (wanted) play();
  }

  async function play() {
    if (!wanted || document.hidden || !audio.paused) {
      update();
      return;
    }
    try {
      await audio.play();
      blocked = false;
    } catch (_) {
      blocked = true;
    }
    update();
  }

  function resumeOnGesture(event) {
    if (event.target && event.target.closest && event.target.closest('.site-sound-toggle')) return;
    if (wanted && audio.paused) play();
  }

  toggle.addEventListener('click', () => {
    if (toggle.classList.contains('is-blocked') || (wanted && audio.paused)) {
      wanted = true;
      blocked = false;
      remember();
      play();
      return;
    }
    wanted = !wanted;
    blocked = false;
    remember();
    if (wanted) play();
    else audio.pause();
    update();
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      savePosition(true);
      audio.pause();
    }
    else if (wanted) play();
    update();
  });
  audio.addEventListener('playing', () => {
    blocked = false;
    update();
  });
  audio.addEventListener('pause', update);
  audio.addEventListener('timeupdate', () => savePosition());
  window.addEventListener('pagehide', () => savePosition(true));
  document.addEventListener('pointerdown', resumeOnGesture, { passive: true });
  document.addEventListener('keydown', resumeOnGesture);
  window.addEventListener('wheel', resumeOnGesture, { passive: true });

  document.body.append(audio, toggle);
  update();
  if (audio.readyState >= 1) restorePosition();
  else audio.addEventListener('loadedmetadata', restorePosition, { once: true });
})();

// Build the desktop How-to canvas from the canonical recipe reader. Category
// cards are an index only: full recipes keep their original, readable markup.
(function buildHowtoCanvas() {
  const body = document.body;
  const canvas = document.querySelector('[data-howto-canvas]');
  const reader = document.querySelector('.howto-reader');
  if (!body.classList.contains('howto-canvas-page') || !canvas || !reader) return;

  const requestedId = decodeURIComponent(location.hash.replace(/^#/, ''));
  const requestedNode = requestedId
    ? reader.querySelector('#' + CSS.escape(requestedId))
    : null;
  const forceReader = new URLSearchParams(location.search).has('read') ||
    (requestedNode && requestedNode.matches('.recipe'));

  if (forceReader) {
    body.classList.add('howto-reader-active');
    canvas.hidden = true;
    if (requestedNode) {
      requestAnimationFrame(() => requestedNode.scrollIntoView({ block: 'start' }));
    }
    return;
  }

  body.classList.add('howto-map-active');
  const groups = [
    {
      id: 'install',
      eyebrow: '01 / START',
      title: 'From install to the first useful task.',
      copy: 'Set up the runtime, choose the first agent, and diagnose the path without guessing.',
      recipes: ['agent-tool-selection', 'install-wizard', 'install-docker', 'server-update', 'first-agent', 'troubleshoot'],
    },
    {
      id: 'agents-interop',
      eyebrow: '02 / AGENTS + INTEROP',
      title: 'Choose models, routing, and external interfaces.',
      copy: 'Connect reasoning services without rebuilding the durable runtime around one provider.',
      recipes: ['published-mcp-client', 'agui-embed', 'delegated-vision', 'multi-llm-aggregator', 'fault-tolerant-llm', 'native-cli-plugins', 'provider-tmux'],
    },
    {
      id: 'clients',
      eyebrow: '03 / CLIENTS',
      title: 'Continue the same work from every client.',
      copy: 'Web, terminal, editor, mobile, and messaging clients share conversations and runtime state.',
      recipes: ['pawcode-installer', 'pawcode-usage', 'vscode-plugin-installer', 'vscode-code-server', 'chat-views', 'telegram', 'android-app'],
    },
    {
      id: 'relays-workspaces',
      eyebrow: '04 / RELAYS',
      title: 'Connect the machines where the work lives.',
      copy: 'Pick the right Relay client and expose only the workspace surfaces the task needs.',
      recipes: ['desktop-novnc-audio', 'relay-desktop-installer', 'relay-cli-installer', 'server-relay', 'remote-relay', 'desktop-relay', 'relay-terminals'],
    },
    {
      id: 'identity',
      eyebrow: '05 / IDENTITY + SECURITY',
      title: 'Keep access narrow and context intentional.',
      copy: 'Identity, secrets, encryption, memory, and gateway controls stay explicit.',
      recipes: ['oauth-provider', 'rclone-filesystem', 'variables-secrets', 'encryption', 'webchat-editors', 'cognitive-routing', 'compact-summarizer', 'private-gateway', 'private-demo'],
    },
    {
      id: 'resources',
      eyebrow: '06 / RESOURCES',
      title: 'Build a reusable runtime library.',
      copy: 'Curate skills, packages, tools, prompts, themes, and marketplace resources.',
      recipes: ['pawflow-depots', 'skills', 'skill-loop', 'mcp-hooks-tools-prompts', 'pfp-packages', 'marketplace', 'themes'],
    },
    {
      id: 'flows',
      eyebrow: '07 / FLOWS',
      title: 'Turn useful agent work into explicit automation.',
      copy: 'Design with agents, then schedule and operate repeatable work as durable Flows.',
      recipes: ['flows-explained', 'agent-flow-main', 'tasks-plans', 'daily-digest'],
    },
    {
      id: 'media-voice',
      eyebrow: '08 / MEDIA + VOICE',
      title: 'Add multimodal services after the core works.',
      copy: 'Connect reviewed image, video, audio, ComfyUI, speech, and realtime voice services.',
      recipes: ['media-service', 'comfyui', 'voice-service', 'realtime-voice'],
    },
  ];

  groups.forEach((group) => {
    const originalSection = reader.querySelector('#' + CSS.escape(group.id));
    if (originalSection) originalSection.removeAttribute('id');
  });

  const hero = document.createElement('section');
  hero.className = 'landing-hero howto-map-hero';
  hero.id = 'howtos-home';
  hero.innerHTML = `
    <div class="landing-hero-grid" aria-hidden="true"></div>
    <div class="landing-glow landing-glow-one" aria-hidden="true"></div>
    <div class="container howto-map-intro">
      <div data-reveal>
        <p class="landing-kicker"><span></span> Practical PawFlow guide</p>
        <h1>Learn PawFlow by doing real work.</h1>
        <p>Choose a journey, open one complete recipe, and return to the map whenever you need the next move.</p>
        <div class="hero-actions">
          <a class="button button-primary" href="?read=agent-tool-selection#agent-tool-selection">Start with tool selection →</a>
          <a class="button button-secondary" href="quickstart.html">5-minute install</a>
        </div>
      </div>
      <div class="howto-map-summary" data-reveal>
        <strong>51</strong><span>complete recipes</span>
        <strong>08</strong><span>guided journeys</span>
        <strong>01</strong><span>shared runtime</span>
      </div>
    </div>`;
  canvas.appendChild(hero);

  groups.forEach((group) => {
    const section = document.createElement('section');
    section.className = 'landing-section howto-map-scene';
    section.id = group.id;
    const container = document.createElement('div');
    container.className = 'container';
    const heading = document.createElement('header');
    heading.className = 'howto-map-heading';
    heading.innerHTML = `
      <div><p class="landing-index">${group.eyebrow}</p><h2>${group.title}</h2></div>
      <p>${group.copy}</p>`;
    container.appendChild(heading);

    const grid = document.createElement('div');
    grid.className = 'howto-map-grid';
    group.recipes.forEach((recipeId, index) => {
      const recipe = reader.querySelector('#' + CSS.escape(recipeId));
      if (!recipe) return;
      const title = recipe.querySelector('h2');
      const meta = recipe.querySelector('.recipe-meta');
      const link = document.createElement('a');
      link.className = 'howto-map-card';
      link.href = 'howtos.html?read=' + encodeURIComponent(recipeId) + '#' + recipeId;
      link.innerHTML = `
        <span>${String(index + 1).padStart(2, '0')}</span>
        <small>${meta ? meta.textContent.trim() : 'How-to'}</small>
        <h3>${title ? title.textContent.trim() : recipeId}</h3>
        <i aria-hidden="true">↗</i>`;
      grid.appendChild(link);
    });
    container.appendChild(grid);
    section.appendChild(container);
    canvas.appendChild(section);
  });
})();

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

// Mobile uses discrete full-screen scenes rather than a decorated document
// scroll. Long scenes scroll inside their viewport; only a new gesture that
// starts at a boundary can trigger the next zoom/pan transition.
(function initMobileStoryCanvas() {
  const body = document.body;
  const main = document.querySelector('.landing-main');
  if (!main || !body.classList.contains('landing-page')) return;

  const scenes = Array.from(main.children).filter(
    (node) => node.matches('.landing-hero, .landing-section'));
  const directLinks = Array.from(document.querySelectorAll('[data-zoom-target]'));
  const query = window.matchMedia(
    '(max-width: 999px) and (prefers-reduced-motion: no-preference)');
  const choreography = ['zoom', 'pan', 'zoom', 'pan', 'zoom', 'pan', 'zoom', 'pan', 'zoom'];
  let enabled = false;
  let current = 0;
  let transitioning = false;
  let transitionTimer = 0;
  let touchStart = null;

  if (scenes.length < 2) return;

  function updateNavigation() {
    scenes.forEach((scene, index) => {
      const active = index === current;
      scene.classList.toggle('is-mobile-current', active);
      scene.setAttribute('aria-hidden', String(!active));
    });
    directLinks.forEach((link) => {
      const active = Number(link.dataset.zoomTarget) === current;
      link.classList.toggle('is-current', active);
      if (active) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
    const scene = scenes[current];
    if (scene && scene.id) history.replaceState(null, '', '#' + scene.id);
  }

  function transitionTo(index, direction) {
    if (!enabled || transitioning || index === current) return;
    transitioning = true;
    const previousIndex = current;
    const outgoing = scenes[previousIndex];
    const incoming = scenes[index];
    const kindIndex = direction > 0 ? previousIndex : index;
    const kind = choreography[kindIndex] || 'pan';
    const reverse = direction < 0;

    incoming.scrollTop = reverse
      ? Math.max(0, incoming.scrollHeight - incoming.clientHeight)
      : 0;
    incoming.setAttribute('aria-hidden', 'false');
    outgoing.classList.add(
      'is-mobile-leaving', 'mobile-transition-' + kind,
      reverse ? 'is-mobile-reverse' : 'is-mobile-forward');
    incoming.classList.add(
      'is-mobile-entering', 'mobile-transition-' + kind,
      reverse ? 'is-mobile-reverse' : 'is-mobile-forward');
    body.classList.add('mobile-story-transitioning');

    transitionTimer = window.setTimeout(() => {
      outgoing.classList.remove(
        'is-mobile-current', 'is-mobile-leaving', 'mobile-transition-' + kind,
        'is-mobile-reverse', 'is-mobile-forward');
      incoming.classList.remove(
        'is-mobile-entering', 'mobile-transition-' + kind,
        'is-mobile-reverse', 'is-mobile-forward');
      current = index;
      body.classList.remove('mobile-story-transitioning');
      updateNavigation();
      transitioning = false;
    }, 760);
  }

  function move(direction) {
    const next = (current + direction + scenes.length) % scenes.length;
    transitionTo(next, direction);
  }

  function onTouchStart(event) {
    if (!enabled || transitioning || event.touches.length !== 1) return;
    const scene = scenes[current];
    touchStart = {
      y: event.touches[0].clientY,
      time: performance.now(),
      atTop: scene.scrollTop <= 2,
      atBottom: scene.scrollTop + scene.clientHeight >= scene.scrollHeight - 2,
    };
  }

  function onTouchEnd(event) {
    if (!enabled || transitioning || !touchStart || !event.changedTouches.length) return;
    const delta = event.changedTouches[0].clientY - touchStart.y;
    const elapsed = performance.now() - touchStart.time;
    const start = touchStart;
    touchStart = null;
    if (elapsed > 1100 || Math.abs(delta) < 54) return;
    if (delta < 0 && start.atBottom) move(1);
    else if (delta > 0 && start.atTop) move(-1);
  }

  function onTouchCancel() { touchStart = null; }

  function onKeydown(event) {
    if (!enabled || transitioning || event.altKey || event.ctrlKey || event.metaKey) return;
    const scene = scenes[current];
    const atTop = scene.scrollTop <= 2;
    const atBottom = scene.scrollTop + scene.clientHeight >= scene.scrollHeight - 2;
    if (['ArrowDown', 'PageDown', ' '].includes(event.key) && atBottom) {
      event.preventDefault();
      move(1);
    } else if (['ArrowUp', 'PageUp'].includes(event.key) && atTop) {
      event.preventDefault();
      move(-1);
    }
  }

  function enable() {
    if (enabled) return;
    enabled = true;
    body.classList.add('mobile-story-active');
    const hashIndex = scenes.findIndex((scene) => '#' + scene.id === location.hash);
    current = hashIndex >= 0 ? hashIndex : 0;
    scenes.forEach((scene) => {
      scene.classList.add('mobile-story-scene');
    });
    updateNavigation();
    main.addEventListener('touchstart', onTouchStart, { passive: true });
    main.addEventListener('touchend', onTouchEnd, { passive: true });
    main.addEventListener('touchcancel', onTouchCancel, { passive: true });
    window.addEventListener('keydown', onKeydown);
  }

  function disable() {
    if (!enabled) return;
    enabled = false;
    clearTimeout(transitionTimer);
    transitioning = false;
    touchStart = null;
    main.removeEventListener('touchstart', onTouchStart);
    main.removeEventListener('touchend', onTouchEnd);
    main.removeEventListener('touchcancel', onTouchCancel);
    window.removeEventListener('keydown', onKeydown);
    body.classList.remove('mobile-story-active', 'mobile-story-transitioning');
    scenes.forEach((scene) => {
      scene.classList.remove(
        'mobile-story-scene', 'is-mobile-current', 'is-mobile-leaving',
        'is-mobile-entering', 'mobile-transition-zoom', 'mobile-transition-pan',
        'is-mobile-reverse', 'is-mobile-forward');
      scene.removeAttribute('aria-hidden');
    });
  }

  function sync() {
    if (query.matches) enable();
    else disable();
  }

  sync();
  query.addEventListener('change', () => requestAnimationFrame(sync));

  directLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      if (!enabled) return;
      const index = Number(link.dataset.zoomTarget);
      if (!Number.isInteger(index) || index < 0 || index >= scenes.length) return;
      event.preventDefault();
      transitionTo(index, index > current ? 1 : -1);
    });
  });
  document.querySelectorAll('.site-nav a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      if (!enabled) return;
      const index = scenes.findIndex(
        (scene) => '#' + scene.id === link.getAttribute('href'));
      if (index < 0) return;
      event.preventDefault();
      transitionTo(index, index > current ? 1 : -1);
    });
  });
})();

// ── Homepage inception canvas ────────────────────────────────────────────
// Desktop wheel input drives a camera through nested full-page scenes. The
// final scene contains a visual copy of the first one, so the camera can reset
// to the root without a visible seam. Mobile uses the boundary-aware scene
// canvas above, with internal scroll only when a chapter needs it.
(function initZoomStory() {
  const body = document.body;
  const main = document.querySelector('.landing-main');
  if (!main || !body.classList.contains('landing-page')) return;

  const query = window.matchMedia(
    '(min-width: 1000px) and (pointer: fine) and (prefers-reduced-motion: no-preference)');
  const sourceScenes = Array.from(main.children).filter(
    (node) => node.matches('.landing-hero, .landing-section'));
  const directLinks = Array.from(document.querySelectorAll('[data-zoom-target]'));
  const sceneCount = sourceScenes.length;
  if (sceneCount < 2) return;

  let world = null;
  let loopClone = null;
  let scenes = [];
  let placements = [];
  let progress = 0;
  let target = 0;
  let raf = 0;
  let activeIndex = -1;
  let enabled = false;
  let wheelLocked = false;
  let wheelUnlockTimer = 0;
  let lastWheelEvent = 0;
  let wheelLockUntil = 0;
  let animationFrom = 0;
  let animationStarted = 0;
  let animationDuration = 900;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const smooth = (value) => value * value * (3 - 2 * value);
  const mix = (from, to, amount) => from + (to - from) * amount;

  function stripCloneIdentity(root) {
    root.dataset.zoomClone = '';
    root.setAttribute('aria-hidden', 'true');
    root.querySelectorAll('[id]').forEach((node) => node.removeAttribute('id'));
    root.querySelectorAll('a, button, video').forEach((node) => {
      node.setAttribute('tabindex', '-1');
      if (node.tagName === 'VIDEO') node.removeAttribute('controls');
    });
  }

  function layoutScenes() {
    const width = window.innerWidth;
    const height = window.innerHeight;
    const ratio = .255;
    // Transition from one scene to the next. Equal-scale placements create a
    // camera pan; reduced-scale placements create an inception zoom.
    const choreography = body.classList.contains('howto-canvas-page')
      ? ['zoom', 'pan', 'zoom', 'pan', 'zoom', 'pan', 'zoom', 'pan', 'zoom']
      : [
          'zoom',   // Hero -> About
          'pan',    // About -> Architecture
          'zoom',   // Architecture -> Videos
          'pan',    // Videos -> Stack
          'pan',    // Stack -> Comparison
          'zoom',   // Comparison -> Install
          'zoom',   // Install -> cloned Hero, then reset
        ];
    let x = 0;
    let y = 0;
    let scale = 1;
    placements = [];

    scenes.forEach((scene, index) => {
      placements.push({ x, y, scale });
      scene.style.setProperty('--scene-x', x + 'px');
      scene.style.setProperty('--scene-y', y + 'px');
      scene.style.setProperty('--scene-scale', String(scale));
      if (index < scenes.length - 1) {
        if (choreography[index] === 'pan') {
          x += scale * width * (index % 2 ? .035 : -.025);
          y += scale * height * 1.06;
        } else {
          x += scale * width * (index % 2 ? .16 : .54);
          y += scale * height * (index % 3 === 1 ? .44 : .17);
          scale *= ratio;
        }
      }
    });

    scenes.forEach((scene) => {
      const content = scene.querySelector(':scope > .container');
      if (!content) return;
      scene.style.setProperty('--scene-content-fit', '1');
      const availableWidth = Math.max(640, width - 210);
      const availableHeight = Math.max(520, height - 126);
      const naturalWidth = Math.max(1, content.scrollWidth);
      const naturalHeight = Math.max(1, content.scrollHeight);
      const fit = Math.min(
        1,
        availableWidth / naturalWidth,
        availableHeight / naturalHeight);
      scene.style.setProperty('--scene-content-fit', String(fit));
    });
    render();
  }

  function render() {
    if (!world || !placements.length) return;
    const max = placements.length - 1;
    const safe = clamp(progress, 0, max);
    const lower = Math.min(Math.floor(safe), max - 1);
    const local = smooth(safe - lower);
    const from = placements[lower];
    const to = placements[Math.min(lower + 1, max)];
    const focusX = mix(from.x, to.x, local);
    const focusY = mix(from.y, to.y, local);
    const scale = Math.exp(mix(Math.log(from.scale), Math.log(to.scale), local) * -1);
    world.style.transform =
      'translate3d(' + (-focusX * scale) + 'px,' + (-focusY * scale) + 'px,0) scale(' + scale + ')';

    scenes.forEach((scene, index) => {
      const visible = index === lower || index === lower + 1;
      const reveal = index === lower
        ? 1 - smooth(clamp((local - .72) / .28, 0, 1))
        : index === lower + 1
          ? smooth(clamp((local - .08) / .58, 0, 1))
          : 0;
      scene.style.setProperty('--scene-opacity', String(reveal));
      const blur = index === lower
        ? smooth(local) * 5
        : index === lower + 1
          ? (1 - smooth(local)) * 5
          : 0;
      scene.style.setProperty('--scene-blur', blur.toFixed(2) + 'px');
      scene.style.visibility = visible ? 'visible' : 'hidden';
      scene.style.contentVisibility = visible ? 'visible' : 'hidden';
    });

    const nearest = clamp(Math.round(progress), 0, sceneCount);
    const publicIndex = nearest === sceneCount ? 0 : nearest;
    if (publicIndex !== activeIndex) {
      activeIndex = publicIndex;
      sourceScenes.forEach((scene, index) => {
        scene.setAttribute('aria-hidden', String(index !== publicIndex));
      });
      directLinks.forEach((link) => {
        const current = Number(link.dataset.zoomTarget) === publicIndex;
        link.classList.toggle('is-current', current);
        if (current) link.setAttribute('aria-current', 'true');
        else link.removeAttribute('aria-current');
      });
      const section = sourceScenes[publicIndex];
      if (section && section.id) {
        history.replaceState(null, '', '#' + section.id);
      }
    }
  }

  function animate(now) {
    const elapsed = Math.max(0, now - animationStarted);
    const time = clamp(elapsed / animationDuration, 0, 1);
    const eased = 1 - Math.pow(1 - time, 4);
    progress = mix(animationFrom, target, eased);
    render();
    if (time >= 1) {
      progress = target;
      render();
      raf = 0;
      if (target >= sceneCount) {
        progress = 0;
        target = 0;
        activeIndex = -1;
        render();
      }
      return;
    }
    raf = requestAnimationFrame(animate);
  }

  function moveTarget(amount) {
    const next = clamp(target + amount, 0, sceneCount);
    if (next === target) return;
    animationFrom = progress;
    target = next;
    animationStarted = performance.now();
    animationDuration = 900 + Math.min(3, Math.abs(target - progress)) * 90;
    if (!raf) raf = requestAnimationFrame(animate);
  }

  function onWheel(event) {
    if (!enabled) return;
    event.preventDefault();
    const delta = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
    const direction = Math.sign(delta);
    if (!direction) return;

    const now = performance.now();
    lastWheelEvent = now;
    clearTimeout(wheelUnlockTimer);
    const unlockAfterIdle = () => {
      const currentTime = performance.now();
      if (raf || currentTime < wheelLockUntil ||
          currentTime - lastWheelEvent < 1000) {
        wheelUnlockTimer = setTimeout(unlockAfterIdle, 100);
        return;
      }
      wheelLocked = false;
    };
    wheelUnlockTimer = setTimeout(unlockAfterIdle, 360);
    if (wheelLocked) return;
    wheelLocked = true;
    wheelLockUntil = now + 1100;

    const current = clamp(Math.round(progress), 0, sceneCount - 1);
    if (current <= 0 && direction < 0) {
      progress = sceneCount;
      target = sceneCount;
      activeIndex = -1;
      render();
      animationFrom = progress;
      target = sceneCount - 1;
      animationStarted = performance.now();
      animationDuration = 990;
      if (!raf) raf = requestAnimationFrame(animate);
      return;
    }
    if (current === sceneCount - 1 && direction > 0) {
      goTo(sceneCount, true);
      return;
    }
    goTo(current + direction);
  }

  function onKeydown(event) {
    if (!enabled || event.altKey || event.ctrlKey || event.metaKey) return;
    if (['ArrowDown', 'PageDown', ' '].includes(event.key)) {
      event.preventDefault();
      moveTarget(1);
    } else if (['ArrowUp', 'PageUp'].includes(event.key)) {
      event.preventDefault();
      if (target <= 0) {
        progress = sceneCount;
        target = sceneCount;
      }
      moveTarget(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      goTo(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      goTo(sceneCount - 1);
    }
  }

  function goTo(index, includeLoop = false) {
    animationFrom = progress;
    target = clamp(index, 0, includeLoop ? sceneCount : sceneCount - 1);
    animationStarted = performance.now();
    animationDuration = 900 + Math.min(3, Math.abs(target - progress)) * 90;
    if (!raf) raf = requestAnimationFrame(animate);
  }

  function enable() {
    if (enabled) return;
    enabled = true;
    body.classList.add('zoom-story-active');
    world = document.createElement('div');
    world.className = 'zoom-world';
    main.insertBefore(world, sourceScenes[0]);
    sourceScenes.forEach((scene) => {
      scene.classList.add('zoom-scene');
      world.appendChild(scene);
    });
    loopClone = sourceScenes[0].cloneNode(true);
    stripCloneIdentity(loopClone);
    loopClone.classList.add('zoom-scene');
    world.appendChild(loopClone);
    scenes = sourceScenes.concat(loopClone);
    const hashIndex = sourceScenes.findIndex((scene) => '#' + scene.id === location.hash);
    progress = target = hashIndex >= 0 ? hashIndex : 0;
    animationFrom = progress;
    animationStarted = performance.now();
    activeIndex = -1;
    layoutScenes();
    window.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKeydown);
    window.addEventListener('resize', layoutScenes);
  }

  function disable() {
    if (!enabled) return;
    enabled = false;
    cancelAnimationFrame(raf);
    raf = 0;
    window.removeEventListener('wheel', onWheel);
    window.removeEventListener('keydown', onKeydown);
    window.removeEventListener('resize', layoutScenes);
    sourceScenes.forEach((scene) => {
      scene.classList.remove('zoom-scene');
      scene.style.removeProperty('--scene-x');
      scene.style.removeProperty('--scene-y');
      scene.style.removeProperty('--scene-scale');
      scene.style.removeProperty('--scene-opacity');
      scene.style.removeProperty('--scene-blur');
      scene.style.removeProperty('--scene-content-fit');
      scene.style.removeProperty('visibility');
      scene.style.removeProperty('content-visibility');
      scene.removeAttribute('aria-hidden');
      main.insertBefore(scene, world);
    });
    if (world) world.remove();
    world = null;
    loopClone = null;
    scenes = [];
    clearTimeout(wheelUnlockTimer);
    wheelLocked = false;
    lastWheelEvent = 0;
    wheelLockUntil = 0;
    body.classList.remove('zoom-story-active');
  }

  directLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      if (!enabled) return;
      event.preventDefault();
      goTo(Number(link.dataset.zoomTarget));
    });
  });
  document.querySelectorAll('.site-nav a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      if (!enabled) return;
      const index = sourceScenes.findIndex(
        (scene) => '#' + scene.id === link.getAttribute('href'));
      if (index < 0) return;
      event.preventDefault();
      goTo(index);
    });
  });

  function syncMode() { query.matches ? enable() : disable(); }
  syncMode();
  query.addEventListener('change', syncMode);
})();

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
