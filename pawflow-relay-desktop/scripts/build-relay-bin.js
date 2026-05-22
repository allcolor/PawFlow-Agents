const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const desktopRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(desktopRoot, '..');
const runtimeRoot = path.join(desktopRoot, 'runtime');
const binDir = path.join(runtimeRoot, 'bin');
const buildDir = path.join(desktopRoot, 'build', 'pyinstaller');
const entry = path.join(__dirname, 'relay-bin-entry.py');
const exeName = process.platform === 'win32' ? 'pawflow-relay.exe' : 'pawflow-relay';
const exePath = path.join(binDir, exeName);

function pythonCommand() {
  return process.env.PAWFLOW_RELAY_PYTHON || process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || desktopRoot,
    env: { ...process.env, ...(options.env || {}) },
    stdio: 'inherit',
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function ensurePyInstaller() {
  const check = spawnSync(pythonCommand(), ['-m', 'PyInstaller', '--version'], {
    cwd: desktopRoot,
    encoding: 'utf8',
    env: process.env,
  });
  if (check.status === 0) return;
  console.error('PyInstaller is required to build the packaged relay binary.');
  console.error(`Install it for this Python: ${pythonCommand()} -m pip install pyinstaller`);
  process.exit(check.status || 1);
}

ensurePyInstaller();
fs.mkdirSync(binDir, { recursive: true });
fs.rmSync(exePath, { force: true });
fs.rmSync(buildDir, { recursive: true, force: true });
fs.mkdirSync(buildDir, { recursive: true });

const pyPath = process.env.PYTHONPATH
  ? `${repoRoot}${path.delimiter}${process.env.PYTHONPATH}`
  : repoRoot;

run(pythonCommand(), [
  '-m', 'PyInstaller',
  '--clean',
  '--onefile',
  '--name', 'pawflow-relay',
  '--distpath', binDir,
  '--workpath', buildDir,
  '--specpath', buildDir,
  '--paths', repoRoot,
  '--hidden-import', 'pawflow_relay.manager_cli',
  '--hidden-import', 'pawflow_relay.thread',
  '--hidden-import', 'pawflow_relay.worker',
  '--hidden-import', 'pawflow_cli.auth',
  entry,
], {
  env: {
    PYTHONPATH: pyPath,
  },
});

if (!fs.existsSync(exePath)) {
  throw new Error(`Relay binary was not produced: ${exePath}`);
}
if (process.platform !== 'win32') {
  fs.chmodSync(exePath, 0o755);
}

console.log(`Built PawFlow relay binary at ${exePath}`);

