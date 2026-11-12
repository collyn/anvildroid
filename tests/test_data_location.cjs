const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../crates/anvildroid-gui/src-frontend/main.js'), 'utf8');
const start = source.indexOf('// Settings > Storage: the data folder');
const endMarker = "document.getElementById('btn-image-cache-move').addEventListener('click', moveImageCache);";
const end = source.indexOf(endMarker);
assert.ok(start > 0 && end > start, 'Storage settings code anchors');
const code = source.slice(start, end + endMarker.length);

function element() {
  const listeners = {};
  return {innerHTML: '', hidden: false, disabled: false, listeners,
          addEventListener(type, handler) { listeners[type] = handler; }};
}

function setup({responses, confirm = true, picked = '/mnt/data'}) {
  const ids = ['data-location-panel', 'btn-data-location-choose', 'btn-data-location-default', 'btn-image-cache-move'];
  const elements = Object.fromEntries(ids.map(id => [id, element()]));
  const requests = [], toasts = [], confirms = [];
  const context = {
    document: {getElementById: id => elements[id]},
    window: {__TAURI__: {dialog: {open: async () => picked}}},
    controllerRequest: async request => { requests.push(request); return structuredClone(responses(request)); },
    AppDialog: {confirm: async message => { confirms.push(message); return confirm; }},
    showAppToast: (message, kind) => toasts.push([kind, message]),
    escapeHtml: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    setTimeout: () => 0, clearTimeout: () => {}, structuredClone,
  };
  vm.createContext(context);
  vm.runInContext(code + '\nthis.refreshDataLocation = refreshDataLocation;', context);
  return {context, elements, requests, toasts, confirms};
}

const report = (fields = {}, cache = {}) => ({path: null, available: true, error: null, free_bytes: 2 * 1024 ** 3,
  default_path: '/var/lib/anvildroid-controller/instances', ...fields,
  cache: {path: '/var/lib/anvildroid-controller/image-cache', external: false, error: null, used_bytes: 1024 ** 3,
          job: null, shared: false, in_data_folder: false, ...cache}});

test('storage settings escape paths and only offer an image move into an available data folder', async () => {
  const {context, elements} = setup({responses: () => report({path: '/mnt/<b>data'})});
  await context.refreshDataLocation();
  const panel = elements['data-location-panel'].innerHTML;
  assert.match(panel, /\/mnt\/&lt;b&gt;data/);
  assert.doesNotMatch(panel, /<b>/);
  assert.equal(elements['btn-image-cache-move'].hidden, false);
  assert.equal(elements['btn-data-location-default'].hidden, false);

  for (const [fields, cache] of [[{path: null}, {}], [{available: false, error: 'gone'}, {}], [{}, {in_data_folder: true}],
                                 [{}, {shared: true}], [{}, {used_bytes: 0}]]) {
    const view = setup({responses: () => report({path: '/mnt/data', ...fields}, cache)});
    await view.context.refreshDataLocation();
    assert.equal(view.elements['btn-image-cache-move'].hidden, true, JSON.stringify([fields, cache]));
  }
});

test('choosing a data folder saves it, then moves downloaded images only after confirmation', async () => {
  let saved = null;
  const responses = request => request.op === 'data_location_set' ? (saved = request.path, report({path: saved})) :
    request.op === 'image_cache_move' ? report({path: saved}, {job: {status: 'Running', phase: 'copy'}}) : report({path: saved});
  const view = setup({responses});
  await view.context.refreshDataLocation();
  await view.elements['btn-data-location-choose'].listeners.click();
  assert.deepEqual(view.requests.map(r => r.op), ['data_location', 'data_location_set', 'image_cache_move']);
  assert.equal(view.requests[1].path, '/mnt/data');
  assert.equal(view.confirms.length, 1);
  assert.match(view.elements['data-location-panel'].innerHTML, /Moving downloaded images… Copying/);
  assert.equal(view.elements['btn-data-location-choose'].disabled, true);

  const declined = setup({responses, confirm: false});
  await declined.context.refreshDataLocation();
  await declined.elements['btn-data-location-choose'].listeners.click();
  assert.deepEqual(declined.requests.map(r => r.op), ['data_location', 'data_location_set']);
});

test('invalid storage responses and controller errors are reported without breaking the page', async () => {
  const invalid = setup({responses: () => ({path: '/x'})});
  await invalid.context.refreshDataLocation();
  assert.match(invalid.elements['data-location-panel'].innerHTML, /Cannot load storage settings: Invalid storage response/);

  const failing = setup({responses: request => { if (request.op === 'data_location_set') throw new Error('Choose a folder owned and writable by your Linux account'); return report(); }});
  await failing.context.refreshDataLocation();
  await failing.elements['btn-data-location-choose'].listeners.click();
  assert.deepEqual(failing.toasts, [['error', 'Cannot change the data folder: Choose a folder owned and writable by your Linux account']]);
  assert.equal(failing.elements['btn-data-location-choose'].disabled, false);
});
