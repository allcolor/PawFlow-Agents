const header = document.querySelector('[data-header]');
const nav = document.querySelector('[data-nav]');
const toggle = document.querySelector('[data-nav-toggle]');

const PAWFLOW_RELEASE = {
  version: '1.0.0.prealpha.1',
  repo: 'https://github.com/allcolor/PawFlow-Agents',
};

function npmPackageVersion(version) {
  return version.replace(/^(\d+\.\d+\.\d+)\.([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)$/, '$1-$2');
}

const releaseAssets = (version) => {
  const desktopVersion = npmPackageVersion(version);
  return {
    installer: `pawflow-install-${version}.zip`,
    pawcodeLinuxTar: `pawcode-${version}-linux-x86_64.tar.gz`,
    pawcodeLinuxZip: `pawcode-${version}-linux-x86_64.zip`,
    pawcodeWindowsZip: `pawcode-${version}-win-x86_64.zip`,
    pawcodeDeb: `pawcode_${version}_amd64.deb`,
    relayCliLinuxTar: `pawflow-relay-cli-${version}-linux-x86_64.tar.gz`,
    relayCliLinuxZip: `pawflow-relay-cli-${version}-linux-x86_64.zip`,
    relayCliWindowsZip: `pawflow-relay-cli-${version}-win-x86_64.zip`,
    relayDesktopTar: `pawflow-relay-desktop-${desktopVersion}.tar.gz`,
    relayDesktopDeb: `pawflow-relay-desktop_${desktopVersion}_amd64.deb`,
    relayDesktopAppImage: `PawFlow Relay Desktop-${desktopVersion}.AppImage`,
    relayDesktopWindows: `PawFlow Relay Desktop Setup ${desktopVersion}.exe`,
    vscodeVsix: `pawflow-vscode-${desktopVersion}.vsix`,
  };
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
bash scripts/install-pawflow.sh --port 19990 --pull-images --version "$PAWFLOW_VERSION"</code>`;
  });
}

renderReleaseReferences();

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
