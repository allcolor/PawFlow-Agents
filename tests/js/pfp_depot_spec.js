'use strict';

const fs = require('fs');

var _lastResourcesData = null;

function t(key) {
  return ({ install: 'Install', installed: 'Installed', pfpInstallPackage: 'Install package',
    pfpDepotBundled: 'bundled', pfpDepotUploaded: 'uploaded' })[key] || key;
}

function escapeHtml(value) { return String(value || ''); }
function _pfpAttr(value) { return String(value || ''); }
function _formatFileSize(value) { return String(value || 0); }

const pfpDepot = eval(
  fs.readFileSync('tasks/io/chat_ui/resources_pfp.js', 'utf8')
  + '\n;({ renderRow: _pfpDepotRowHtml });');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const pixazo = {
  package: 'pawflow.pixazo-provider',
  version: '1.0.0',
  ref: 'depot:pixazo',
  source: 'bundled',
  objects: [],
};

_lastResourcesData = {
  pfp_packages: [{
    package: 'pawflow.pixazo-provider',
    version: '1.0.0',
    _scope: 'user',
  }],
};

const installedHtml = pfpDepot.renderRow(pixazo);
assert(installedHtml.includes('class="pfp-depot-installed"'),
  'the exact installed package version must render an installed state');
assert(!installedHtml.includes('<button class="pfp-depot-install"'),
  'the exact installed package version must not offer installation');

const otherVersionHtml = pfpDepot.renderRow(Object.assign({}, pixazo, { version: '1.1.0' }));
assert(otherVersionHtml.includes('<button class="pfp-depot-install"'),
  'a different package version must remain available for update inspection');

const unrelatedHtml = pfpDepot.renderRow(Object.assign({}, pixazo, {
  package: 'pawflow.wavespeed-provider',
}));
assert(unrelatedHtml.includes('<button class="pfp-depot-install"'),
  'an uninstalled package must still offer installation');

console.log('pfp depot spec: ok');
