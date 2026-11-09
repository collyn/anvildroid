const test = require('node:test');
const assert = require('node:assert/strict');
const Tabs = require('../crates/anvildroid-gui/src-frontend/settings-tabs.js');

test('categories follow the gamer-first order and keep unknown ones last', () => {
  assert.deepEqual(Tabs.order(['storage', 'diagnostics', 'graphics', 'custom thing', 'performance', 'display']),
    ['graphics', 'display', 'performance', 'storage', 'diagnostics', 'custom thing']);
});

test('saved category wins only while it is still available', () => {
  assert.equal(Tabs.pick(['graphics', 'storage'], 'storage'), 'storage');
  assert.equal(Tabs.pick(['graphics', 'performance'], 'storage'), 'graphics');
  assert.equal(Tabs.pick([], 'storage'), null);
});

test('summary titles normalise to stable keys', () => {
  assert.equal(Tabs.keyOf('  Device   Identity '), 'device identity');
});
