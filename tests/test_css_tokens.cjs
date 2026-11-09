const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dir = path.join(__dirname, '../crates/anvildroid-gui/src-frontend');
const files = fs.readdirSync(dir).filter(name => /\.(css|js|html)$/.test(name));
const source = files.map(name => fs.readFileSync(path.join(dir, name), 'utf8')).join('\n');

test('every CSS variable that is used without a fallback is defined', () => {
  const defined = new Set([...source.matchAll(/(--[\w-]+)\s*:/g)].map(match => match[1]));
  for (const match of source.matchAll(/setProperty\(['"](--[\w-]+)/g)) defined.add(match[1]);
  // var(--x) with no fallback; var(--x, …) is allowed to be undefined.
  const missing = [...new Set([...source.matchAll(/var\((--[\w-]+)\s*\)/g)].map(match => match[1]))]
    .filter(name => !defined.has(name));
  assert.deepEqual(missing, [], `undefined CSS variables: ${missing.join(', ')}`);
});

test('modals have an opaque surface token', () => {
  const theme = fs.readFileSync(path.join(dir, 'theme.css'), 'utf8');
  assert.match(theme, /--dialog-surface:\s*var\(--bg-panel\)/);
  assert.match(theme, /--dialog-border:\s*var\(--border-strong\)/);
});
