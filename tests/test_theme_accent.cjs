const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../crates/anvildroid-gui/src-frontend/theme.js'), 'utf8');

function load(stored = {}) {
  const store = {...stored};
  const root = {dataset: {}};
  const window = {matchMedia: () => ({matches: false, addEventListener() {}})};
  const context = {
    window, localStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }},
    document: {documentElement: root, querySelectorAll: () => [], addEventListener() {}, dispatchEvent() {}},
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
  };
  vm.runInNewContext(source, context);
  return {theme: window.AnvilTheme, root, store};
}

test('blue is the default accent and is applied before paint', () => {
  const {theme, root} = load();
  assert.equal(theme.accent(), 'blue');
  assert.equal(root.dataset.accent, 'blue');
});

test('a saved preset is restored; unknown values fall back to blue', () => {
  assert.equal(load({'anvildroid.accent': 'crimson'}).root.dataset.accent, 'crimson');
  assert.equal(load({'anvildroid.accent': '<script>'}).root.dataset.accent, 'blue');
});

test('setAccent saves known presets and ignores others', () => {
  const {theme, root, store} = load();
  theme.setAccent('ice');
  assert.equal(root.dataset.accent, 'ice');
  assert.equal(store['anvildroid.accent'], 'ice');
  theme.setAccent('rainbow');
  assert.equal(root.dataset.accent, 'ice');
});

test('every preset has dark and light rules and a swatch in theme.css', () => {
  const css = fs.readFileSync(path.join(__dirname, '../crates/anvildroid-gui/src-frontend/theme.css'), 'utf8');
  const {theme} = load();
  for (const name of theme.accents.filter(name => name !== 'blue')) {
    assert.match(css, new RegExp(`:root\\[data-accent="${name}"\\]:not\\(\\[data-theme="light"\\]\\)`));
    assert.match(css, new RegExp(`:root\\[data-accent="${name}"\\]\\[data-theme="light"\\]`));
  }
  for (const name of theme.accents) assert.match(css, new RegExp(`data-swatch="${name}"`));
});
