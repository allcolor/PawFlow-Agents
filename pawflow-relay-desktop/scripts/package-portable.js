const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const desktopRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(desktopRoot, '..');
const outDir = path.join(repoRoot, 'dist', 'pawflow-relay-desktop');

function copy(srcName) {
  const src = path.join(desktopRoot, srcName);
  const dest = path.join(outDir, srcName);
  fs.cpSync(src, dest, {
    recursive: true,
    force: true,
    filter: entry => !entry.includes(`${path.sep}node_modules${path.sep}`),
  });
}

const prep = spawnSync(process.execPath, [path.join(__dirname, 'prepare-runtime.js')], {
  cwd: desktopRoot,
  stdio: 'inherit',
});
if (prep.status !== 0) process.exit(prep.status || 1);

fs.rmSync(outDir, { recursive: true, force: true });
fs.mkdirSync(outDir, { recursive: true });

for (const item of [
  'package.json',
  'package-lock.json',
  'README.md',
  'start-windows.ps1',
  'src',
  'scripts',
  'runtime',
]) {
  copy(item);
}

console.log(`Portable PawFlow Relay Desktop prepared at ${outDir}`);
