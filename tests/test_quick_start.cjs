const test = require('node:test');
const assert = require('node:assert/strict');
const QuickStart = require('../crates/anvildroid-gui/src-frontend/quick-start.js');

const status = state => QuickStart.steps(state).map(step => step.status);
const actions = state => QuickStart.steps(state).map(step => step.action?.id || null);

test('first run offers only the download', () => {
  assert.deepEqual(status({}), ['todo', 'locked', 'locked']);
  assert.deepEqual(actions({}), ['download', null, null]);
});

test('missing Waydroid tools point to setup instead of downloading', () => {
  assert.deepEqual(actions({setupMissing:true}), ['setup', null, null]);
});

test('download in progress shows percent and offers nothing to click', () => {
  const [image] = QuickStart.steps({imageBusy:true, imagePercent:42.7});
  assert.equal(image.status, 'busy');
  assert.equal(image.action, null);
  assert.match(image.detail, /42%/);
});

test('ready image unlocks device creation, then start, then install', () => {
  assert.deepEqual(actions({imageReady:true}), [null, 'create', null]);
  assert.deepEqual(actions({imageReady:true, hasDevice:true}), [null, 'start', null]);
  assert.deepEqual(status({hasDevice:true, deviceBusy:true}), ['done', 'busy', 'locked']);
  assert.deepEqual(actions({hasDevice:true, deviceRunning:true}), [null, null, 'install']);
});

test('render escapes text and marks each step with its status', () => {
  const html = QuickStart.render({imageError:'Cannot <reach> server'}, value => String(value).replace(/</g, '&lt;').replace(/>/g, '&gt;'));
  assert.match(html, /Cannot &lt;reach&gt; server/);
  assert.match(html, /data-status="todo"[^]*role="alert"/);
  assert.match(html, /data-quick-start="download"/);
  assert.equal((html.match(/class="quick-start-step"/g) || []).length, 3);
});
