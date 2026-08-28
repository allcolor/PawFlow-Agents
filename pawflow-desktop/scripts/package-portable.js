'use strict';

const fs = require('fs');
const path = require('path');

const desktopRoot = path.resolve(__dirname, '..');
const repositoryRoot = path.resolve(desktopRoot, '..');
const output = path.join(repositoryRoot, 'dist', 'pawflow-desktop');

fs.rmSync(output, { recursive: true, force: true });
fs.mkdirSync(output, { recursive: true });

for (const name of ['package.json', 'package-lock.json', 'README.md', 'src']) {
  const source = path.join(desktopRoot, name);
  if (!fs.existsSync(source)) {
    if (name === 'package-lock.json') continue;
    throw new Error(`Required desktop payload is missing: ${name}`);
  }
  fs.cpSync(source, path.join(output, name), { recursive: true, force: true });
}

console.log(`Portable PawFlow Desktop prepared at ${output}`);
