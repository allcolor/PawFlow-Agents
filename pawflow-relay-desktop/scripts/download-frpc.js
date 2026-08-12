const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const VERSION = '0.70.1';
const ASSETS = {
  'win32-x64': ['windows_amd64', 'zip', '531f3cd3cc41c0b4f077b54fe6b7dd83c0ff727e7f0bf412a4c78fa279165de5'],
  'win32-arm64': ['windows_arm64', 'zip', '74d3acaf0f03ee190dd0462f9b49861dca50b0559c5488af4b36572fc951fcca'],
  'linux-x64': ['linux_amd64', 'tar.gz', '333da23d1b9009d7c01638e9ba38cf4600f7d37d393f854e96ee1396adefa9a6'],
  'linux-arm64': ['linux_arm64', 'tar.gz', '3990f396a9a490ee7f0e5f355287750ed41520064ed999eab443b5e9a78d773d'],
  'darwin-x64': ['darwin_amd64', 'tar.gz', 'cbf69cf26e5553e914e97d37f5d4367fa30f5f531d073a889465af4719281e25'],
  'darwin-arm64': ['darwin_arm64', 'tar.gz', 'cfa733b5a261c1647edee3c1fc4133d2542989b28f5602e81d47fc821d25c55f'],
};

function findFile(root, name) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const candidate = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const found = findFile(candidate, name);
      if (found) return found;
    } else if (entry.name === name) {
      return candidate;
    }
  }
  return '';
}

async function main() {
  const key = `${process.platform}-${process.arch}`;
  const asset = ASSETS[key];
  if (!asset) {
    throw new Error(`FRP ${VERSION} is not packaged for ${key}`);
  }

  const [platformName, extension, expectedSha256] = asset;
  const archiveName = `frp_${VERSION}_${platformName}.${extension}`;
  const url = `https://github.com/fatedier/frp/releases/download/v${VERSION}/${archiveName}`;
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pawflow-frpc-'));
  try {
    const archivePath = path.join(tempRoot, archiveName);
    const extractRoot = path.join(tempRoot, 'extract');
    fs.mkdirSync(extractRoot);

    const response = await fetch(url, { redirect: 'follow' });
    if (!response.ok) {
      throw new Error(`Unable to download ${url}: HTTP ${response.status}`);
    }
    const bytes = Buffer.from(await response.arrayBuffer());
    const actualSha256 = crypto.createHash('sha256').update(bytes).digest('hex');
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `FRP checksum mismatch for ${archiveName}: expected ${expectedSha256}, got ${actualSha256}`);
    }
    fs.writeFileSync(archivePath, bytes);

    const extracted = spawnSync('tar', ['-xf', archivePath, '-C', extractRoot], {
      stdio: 'inherit',
    });
    if (extracted.error) throw extracted.error;
    if (extracted.status !== 0) {
      throw new Error(`Unable to extract ${archiveName}`);
    }

    const binaryName = process.platform === 'win32' ? 'frpc.exe' : 'frpc';
    const source = findFile(extractRoot, binaryName);
    if (!source) {
      throw new Error(`${binaryName} was not found in ${archiveName}`);
    }
    const destinationDir = path.resolve(__dirname, '..', 'runtime', 'bin');
    const destination = path.join(destinationDir, binaryName);
    fs.mkdirSync(destinationDir, { recursive: true });
    fs.copyFileSync(source, destination);
    if (process.platform !== 'win32') fs.chmodSync(destination, 0o755);
    console.log(`Packaged verified FRP ${VERSION} client at ${destination}`);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
}

main().catch(error => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
