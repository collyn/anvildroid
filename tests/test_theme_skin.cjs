const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const frontend = path.join(__dirname, '../crates/anvildroid-gui/src-frontend');
const source = fs.readFileSync(path.join(frontend, 'theme.js'), 'utf8');

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

test('HUD is the default theme and is applied before paint', () => {
  const {theme, root} = load();
  assert.equal(theme.skin(), 'hud');
  assert.equal(root.dataset.skin, 'hud');
});

test('a saved theme is restored; unknown values fall back to HUD', () => {
  assert.equal(load({'anvildroid.skin': 'classic'}).root.dataset.skin, 'classic');
  assert.equal(load({'anvildroid.skin': '"><img>'}).root.dataset.skin, 'hud');
});

test('setSkin saves known themes and ignores others', () => {
  const {theme, root, store} = load();
  theme.setSkin('classic');
  assert.equal(root.dataset.skin, 'classic');
  assert.equal(store['anvildroid.skin'], 'classic');
  theme.setSkin('neon');
  assert.equal(root.dataset.skin, 'classic');
});

test('every HUD rule is scoped so Classic is untouched', () => {
  const css = fs.readFileSync(path.join(frontend, 'skin.css'), 'utf8').replace(/\/\*[^]*?\*\//g, '');
  const selectors = [];
  // Rule heads: text before "{" that is not an at-rule.
  // Split a selector list on commas outside parentheses (:is/:not nest).
  const topLevel = list => {
    const parts = [''];
    let depth = 0;
    for (const ch of list) {
      if (ch === '(') depth++;
      if (ch === ')') depth--;
      if (ch === ',' && depth === 0) parts.push(''); else parts[parts.length - 1] += ch;
    }
    return parts.map(part => part.trim());
  };
  for (const match of css.matchAll(/(^|[{}])\s*([^{}@\s][^{}]*?)\s*\{/g)) selectors.push(...topLevel(match[2]));
  assert.ok(selectors.length > 50);
  for (const selector of selectors) assert.match(selector, /data-skin="hud"/, selector);
});

test('every theme in theme.js has a picker button and shared layout loads before the skin', () => {
  const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
  for (const name of load().theme.skins) assert.match(html, new RegExp(`data-skin-choice="${name}"`));
  assert.ok(html.indexOf('href="layout.css"') > 0 && html.indexOf('href="layout.css"') < html.indexOf('href="skin.css"'));
});
