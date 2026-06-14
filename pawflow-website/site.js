const header = document.querySelector('[data-header]');
const nav = document.querySelector('[data-nav]');
const toggle = document.querySelector('[data-nav-toggle]');

// Fallback when the GitHub API is unreachable or rate-limited. Keep the
// version in sync with the latest release tag on a best-effort basis — the
// live fetch below overrides it on every page load.
const PAWFLOW_RELEASE = {
  version: '1.0.0-alpha.12',
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
