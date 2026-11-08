const test = require('node:test');
const assert = require('node:assert/strict');
const {itemsFor, nextEnabled} = require('../crates/anvildroid-gui/src-frontend/select-menu.js');

const option = (label, extra = {}) => ({tagName: 'OPTION', label, textContent: label, disabled: false, hidden: false, selected: false, ...extra});
const group = (label, children, extra = {}) => ({tagName: 'OPTGROUP', label, children, disabled: false, hidden: false, ...extra});

test('menu items follow options and groups, skipping hidden ones', () => {
  const select = {children: [option('Original device', {selected: true}), group('Google', [option('Pixel 8'), option('Old', {hidden: true})]),
    group('Locked', [option('X')], {disabled: true}), group('Gone', [option('Y')], {hidden: true})]};
  const items = itemsFor(select);
  assert.deepEqual(items.map(item => [item.kind, item.label, Boolean(item.disabled), Boolean(item.grouped)]), [
    ['option', 'Original device', false, false], ['group', 'Google', false, false], ['option', 'Pixel 8', false, true],
    ['group', 'Locked', false, false], ['option', 'X', true, true]]);
});

test('keyboard movement skips group labels and disabled options', () => {
  const items = [{kind: 'option'}, {kind: 'group'}, {kind: 'option', disabled: true}, {kind: 'option'}];
  assert.equal(nextEnabled(items, 0, 1), 3);
  assert.equal(nextEnabled(items, 3, -1), 0);
  assert.equal(nextEnabled(items, 3, 1), 3);       // stays at the end
  assert.equal(nextEnabled(items, -1, 1), 0);       // Home
  assert.equal(nextEnabled(items, items.length, -1), 3); // End
});
