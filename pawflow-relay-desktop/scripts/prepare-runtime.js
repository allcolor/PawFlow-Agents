const fs = require('fs');
const path = require('path');

const desktopRoot = path.resolve(__dirname, '..');
const repoRootArg = process.argv.find(arg => arg.startsWith('--repo-root='));
const repoRoot = repoRootArg
  ? path.resolve(repoRootArg.slice('--repo-root='.length))
  : path.resolve(desktopRoot, '..');
const runtimeRoot = path.join(desktopRoot, 'runtime');

const toolFiles = [
  'pawflow_relay_launcher.py',
  'fs_actions.py',
  'fs_exec.py',
  'fs_screen.py',
  'fs_mcp.py',
  'fs_common.py',
  'fs_http.py',
  'audio_capture.py',
  'screen_actions.py',
];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFile(src, dest) {
  if (!fs.existsSync(src)) {
    throw new Error(`Required runtime file missing: ${src}`);
  }
  ensureDir(path.dirname(dest));
  fs.copyFileSync(src, dest);
}

function copyDir(src, dest) {
  if (!fs.existsSync(src)) {
    throw new Error(`Required runtime directory missing: ${src}`);
  }
  fs.cpSync(src, dest, {
    recursive: true,
    force: true,
    filter: entry => {
      const base = path.basename(entry);
      if (base === '__pycache__' || base === '.pytest_cache') return false;
      if (base.endsWith('.pyc') || base.endsWith('.pyo')) return false;
      return true;
    },
  });
}

fs.rmSync(runtimeRoot, { recursive: true, force: true });
ensureDir(runtimeRoot);

for (const file of toolFiles) {
  copyFile(path.join(repoRoot, 'tools', file), path.join(runtimeRoot, 'tools', file));
}

copyFile(
  path.join(repoRoot, 'docker', 'pawflow_sdk', 'pawflow.py'),
  path.join(runtimeRoot, 'docker', 'pawflow_sdk', 'pawflow.py'),
);
copyFile(
  path.join(repoRoot, 'config', 'relay_image_catalog.json'),
  path.join(runtimeRoot, 'config', 'relay_image_catalog.json'),
);
copyFile(
  path.join(repoRoot, 'scripts', 'generate-relay-image.py'),
  path.join(runtimeRoot, 'scripts', 'generate-relay-image.py'),
);
copyDir(path.join(repoRoot, 'pawflow_relay'), path.join(runtimeRoot, 'pawflow_relay'));
copyDir(path.join(repoRoot, 'pawflow_cli'), path.join(runtimeRoot, 'pawflow_cli'));

console.log(`Prepared PawFlow Relay Desktop runtime at ${runtimeRoot}`);
