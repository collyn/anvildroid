const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const root = path.join(__dirname, '..');
const frontend = path.join(root, 'crates/anvildroid-gui/src-frontend');
const script = path.join(root, 'scripts/update-contributors.py');

test('the committed contributor list holds plain GitHub names and loads before the app code', () => {
  const names = require(path.join(frontend, 'contributors.js'));
  assert.ok(Array.isArray(names) && names.length > 0);
  for (const name of names) assert.match(name, /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/);
  const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
  assert.ok(html.indexOf('src="contributors.js"') > 0 && html.indexOf('src="contributors.js"') < html.indexOf('src="main.js"'));
});

test('About lists each contributor as an escaped GitHub link button', () => {
  const source = fs.readFileSync(path.join(frontend, 'main.js'), 'utf8');
  const start = source.indexOf('// The list is written into contributors.js');
  const code = source.slice(start, source.indexOf("document.getElementById('page-about')", start));
  const people = {innerHTML: 'old'};
  const escapeHtml = value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  vm.runInNewContext(code, {document: {getElementById: () => people}, escapeHtml, ANVIL_CONTRIBUTORS: ['collyn', 'a"><b']});
  assert.equal((people.innerHTML.match(/data-github-user=/g) || []).length, 2);
  assert.match(people.innerHTML, /data-github-user="collyn" title="Open collyn on GitHub">collyn</);
  assert.ok(people.innerHTML.includes('data-github-user="a&quot;>&lt;b"'));
  assert.doesNotMatch(people.innerHTML, /<b[^u]/);
  vm.runInNewContext(code, {document: {getElementById: () => people}, escapeHtml});  // file missing: empty, no error
  assert.equal(people.innerHTML, '');
});

test('the release script keeps people in order, drops bots and odd names, and never leaves the list empty', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'anvil-contributors-'));
  try {
    const source = path.join(directory, 'api.json'), output = path.join(directory, 'contributors.js');
    fs.writeFileSync(source, JSON.stringify([{login: 'first', type: 'User'}, {login: 'dependabot[bot]', type: 'Bot'},
      {login: 'renovate', type: 'Bot'}, {login: 'second-one', type: 'User'}, {login: 'first', type: 'User'},
      {login: 'bad/name', type: 'User'}, {login: '-edge', type: 'User'}, {type: 'User'}, 'junk']));
    execFileSync('python3', [script, '--source', source, '--output', output], {stdio: 'pipe'});
    assert.deepEqual(JSON.parse(vm.runInNewContext(fs.readFileSync(output, 'utf8') + ';JSON.stringify(ANVIL_CONTRIBUTORS)')), ['first', 'second-one']);
    // Nothing usable (or GitHub unreachable): the committed list is shipped instead.
    for (const body of ['[]', '{"message":"rate limited"}', 'not json']) {
      fs.writeFileSync(source, body);
      execFileSync('python3', [script, '--source', source, '--output', output], {stdio: 'pipe'});
      assert.equal(fs.readFileSync(output, 'utf8'), fs.readFileSync(path.join(frontend, 'contributors.js'), 'utf8'));
    }
  } finally { fs.rmSync(directory, {recursive: true, force: true}); }
});
