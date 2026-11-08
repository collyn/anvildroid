const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {create} = require('../crates/anvildroid-gui/src-frontend/page-loads.js');
const source = fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js', 'utf8');
function clock() {
  let time = 0;
  const frames = [], errors = [];
  const loader = create({now: () => time, frame: fn => frames.push(fn), onError: e => errors.push(e)});
  return {loader, errors, advance: ms => time += ms, paint: () => frames.splice(0).forEach(fn => fn())};
}
test('tab loads run after paint, reuse recent content and deduplicate pending work', async () => {
  const c = clock(); let calls = 0, finish;
  const load = () => {calls++; return new Promise(resolve => finish = resolve);};
  const first = c.loader.schedule('runtime', () => true, load);
  assert.equal(c.loader.schedule('runtime', () => true, load), first);
  assert.equal(calls, 0); c.paint(); await Promise.resolve(); assert.equal(calls, 0);
  c.paint(); await Promise.resolve(); assert.equal(calls, 1);
  finish(); await first;
  await c.loader.schedule('runtime', () => true, load); assert.equal(calls, 1);
  c.advance(15001);
  const next = c.loader.schedule('runtime', () => true, async () => calls++);
  c.paint(); c.paint(); await next; assert.equal(calls, 2);
});
test('rapid navigation cancels loads for tabs left before paint and allows retry', async () => {
  const c = clock(); let active = true, calls = 0;
  const first = c.loader.schedule('images', () => active, async () => calls++);
  active = false; c.paint(); c.paint(); await first; assert.equal(calls, 0);
  active = true;
  const next = c.loader.schedule('images', () => active, async () => calls++);
  c.paint(); c.paint(); await next; assert.equal(calls, 1);
});
test('rejected loads are reported and can be retried immediately', async () => {
  const c = clock(); let calls = 0;
  const load = () => {calls++; throw new Error('offline');};
  for (let i = 0; i < 2; i++) {
    const p = c.loader.schedule('settings', () => true, load); c.paint(); c.paint(); await p;
  }
  assert.equal(calls, 2); assert.equal(c.errors.length, 2);
});
function fn(name, next) {return source.slice(source.indexOf(`async function ${name}`), source.indexOf(next, source.indexOf(`async function ${name}`)));}
test('Images only loads image data; entering does not start an online catalog check', async () => {
  const calls = [], button = {};
  const ctx = vm.createContext({document:{getElementById:()=>button}, pageLoads:{mark(){}},
    refreshImageDownload:async()=>calls.push('download'), checkOfficialImages:async online=>calls.push(online)});
  vm.runInContext(fn('refreshImagesPage', '// --- Runtime status'), ctx);
  await ctx.refreshImagesPage({online:false}); assert.deepEqual(calls, ['download', false]); assert.equal(button.disabled, false);
  calls.length = 0; await ctx.refreshImagesPage(); assert.deepEqual(calls, ['download', true]);
});
function controllerContext() {
  const calls = [], elements = new Map(), previous = {id:'r-one',state:'Stopped',display:{mode:'desktop'},gpu:{node:'gpu'},arm:{enabled:true},notifications:{enabled:true,blocked:[]}};
  const ctx = vm.createContext({
    controllerLoading:false, controllerMutation:false, runtimeOperations:new Map(), controllerTimer:null, controllerRequirements:{can_prepare:true,can_start:true,checks:[]},
    controllerRecords:[previous], controllerSetupError:'', armSourceState:{sources:[{sha256:'abc'}]},
    controllerNotice:'',controllerError:'',controllerRefreshDone:null,finishControllerRefresh:null,armSourcesRefresh:null,
    existingRuntime:{id:'default',state:'Stopped',gpu:{node:'gpu'},arm:{enabled:true}},existingRuntimeLoading:false,existingRuntimeError:'',
    document:{getElementById(id){if(!elements.has(id))elements.set(id,{style:{}});return elements.get(id);}},
    controllerRequest:async request=>{calls.push(request);if(request.op==='list')return [{id:'r-one',state:ctx.state}];if(request.op==='refresh')return {id:request.id,state:ctx.state};if(request.op==='requirements')return {can_prepare:true,can_start:true,checks:[]};if(request.op==='arm_sources')return [];return {};},
    RuntimeUsage:{sample(){},retain(){}},invalidateRuntimeGraphics(){},renderControllerSetup(){},renderArmSources(){},renderController(){},
    renderImageSourceOptions(){},renderRuntimeBatchSelection(){},scheduleRuntimeUsage(){},controllerHasActiveWork:()=>false,
    clearTimeout(){},setTimeout(){},state:'Stopped',currentPage:'runtime',syncLoginStart(){}
  });
  vm.runInContext(fn('refreshExistingRuntime', 'function renderExistingRuntime'),ctx);
  vm.runInContext(source.slice(source.indexOf('function refreshArmSources('), source.indexOf('let customUrlState =')),ctx);
  vm.runInContext(fn('refreshController', 'function imageForRequest'),ctx);
  return {ctx,calls};
}
test('light runtime refresh retains configuration, refreshes state and usage, and avoids setup probes', async () => {
  const {ctx,calls} = controllerContext();
  await ctx.refreshController({light:true});
  assert.equal(calls.length, 10);
  assert.equal(calls.filter(r=>r.op==='device_info').length, 2);
  assert.ok(calls.some(r=>r.op==='app_job'));
  assert.ok(calls.some(r=>r.op==='storage_info'));
  assert.ok(!calls.some(r=>['gpu_info','arm_info','display_info','requirements','arm_sources'].includes(r.op)));
  assert.equal(ctx.controllerRecords[0].display.mode,'desktop'); assert.equal(ctx.existingRuntime.gpu.node,'gpu');
});
test('explicit refresh and changed runtime states reread configuration', async () => {
  for (const changed of [false,true]) {
    const {ctx,calls} = controllerContext(); if(changed)ctx.state='Running';
    await ctx.refreshController({light:changed});
    assert.equal(calls.filter(r=>r.op==='gpu_info').length, 2);
    assert.equal(calls.filter(r=>r.op==='arm_info').length, 2);
    assert.equal(calls.filter(r=>r.op==='display_info').length, 1);
    assert.equal(ctx.controllerLoading, false);
  }
});
test('first Runtime visit publishes names and live state before slow details finish', async () => {
  const {ctx} = controllerContext();
  let release; const held = new Promise(resolve => release = resolve);
  const original = ctx.controllerRequest;
  const renders = [];
  ctx.controllerRecords = []; ctx.existingRuntime = null;
  ctx.renderController = () => renders.push({rows:ctx.controllerRecords.map(r=>({...r})),default:ctx.existingRuntime && {...ctx.existingRuntime}});
  ctx.controllerRequest = async request => {
    if(request.op==='storage_info')return held;
    return original(request);
  };
  const refresh = ctx.refreshController({light:true});
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(renders.some(r=>r.rows[0]?.state==='Stopped'&&r.rows[0].detailsLoading));
  assert.ok(renders.some(r=>r.default?.state==='Stopped'&&r.default.detailsLoading));
  assert.equal(ctx.controllerLoading,true);
  release({}); await refresh;
  assert.equal(ctx.controllerRecords[0].detailsLoading,undefined);
  assert.equal(ctx.existingRuntime.detailsLoading,undefined);
  assert.equal(ctx.controllerLoading,false);
});
function appsContext({fallback=false}={}) {
  const calls=[],renders=[];
  const ctx=vm.createContext({libraryDefaultState:'Unknown',allApps:[{package:'com.cached.app'}],appStatesLoading:false,
    refreshingApps:false,btnRefreshApps:{},appsGrid:{},activeRuntimeId:'default',libraryPage:true,
    controllerRequest:async request=>{calls.push(request.op);if(fallback)throw new Error('offline');return {id:'default',state:'Running'};},
    invoke:async command=>{calls.push(command);if(command==='get_health_report')return {status:{state:'Running'}};return [{package:'com.live.app'}];},
    refreshManagedLibrary:async()=>{},saveAppsCache(){},renderManagedLibraryStatus(){},renderApps:apps=>renders.push(apps.map(a=>a.package)),
    libraryRuntimeRunning:()=>['Running','Frozen'].includes(ctx.libraryDefaultState)
  });
  vm.runInContext(fn('refreshLibraryDefaultState','function renderLibraryEmpty'),ctx);
  vm.runInContext(fn('refreshApps','function libraryRuntimeRunning'),ctx);
  return {ctx,calls,renders};
}
test('first Apps load shows cached apps after live state and requests inventory only once', async () => {
  const {ctx,calls,renders}=appsContext();await ctx.refreshApps();
  assert.deepEqual(calls,['refresh','get_apps']);
  assert.ok(renders.some(apps=>apps.includes('com.cached.app')));
  assert.ok(renders.some(apps=>apps.includes('com.live.app')));
  assert.equal(ctx.refreshingApps,false);
});
test('Apps retains legacy health fallback when controller is unavailable', async () => {
  const {ctx,calls}=appsContext({fallback:true});await ctx.refreshApps();
  assert.deepEqual(calls,['refresh','get_health_report','get_apps']);
  assert.equal(ctx.libraryDefaultState,'Running');
});
test('login entry follows start-at-login runtimes and retries after a failed write', async () => {
  const calls = [];
  let fail = false;
  const ctx = vm.createContext({invoke: (command, args) => { calls.push([command, args.enabled]); return fail ? Promise.reject(new Error('io')) : Promise.resolve(); }});
  const start = source.indexOf('let loginStartSynced = null;');
  vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), ctx);
  ctx.syncLoginStart([{start_on_boot:false}]);
  ctx.syncLoginStart([{start_on_boot:false}]);                 // unchanged: no rewrite
  ctx.syncLoginStart([{start_on_boot:false},{start_on_boot:true}]);
  ctx.syncLoginStart(null);                                    // invalid inventory: untouched
  assert.deepEqual(calls, [['set_login_start', false], ['set_login_start', true]]);
  fail = true;
  ctx.syncLoginStart([]);
  await new Promise(resolve => setImmediate(resolve));
  fail = false;
  ctx.syncLoginStart([]);                                      // the failed write is retried
  assert.deepEqual(calls.slice(2), [['set_login_start', false], ['set_login_start', false]]);
});
function quickContext(state = 'Running') {
  const sent = [], toasts = [];
  const record = {id:'r-one', name:'One', state,
    device:{profile:'default', revision:'rev1', profiles:[{id:'pixel', name:'Pixel 8', values:{manufacturer:'Google'}}]},
    gpu:{selection:'auto', software_supported:true, devices:[{node:'/dev/dri/renderD128', driver:'amdgpu', selectable:true}, {node:'/dev/dri/renderD129', driver:'nvidia', selectable:false}]}};
  const ctx = vm.createContext({escapeHtml:value => String(value).replaceAll('<','&lt;').replaceAll('"','&quot;'),
    runtimeRecord:id => id === record.id ? record : null, runtimeOperations:new Map(), controllerMutation:false, apkInstallBusy:false,
    runtimeBatchBusy:false, runtimeOperationBusy:() => false, runtimeDeviceDraft:new Map([['r-one', {}]]), runtimeGpuDraft:new Map([['r-one', 'x']]),
    showAppToast:(text, kind) => toasts.push([text, kind]), mutateController:async request => { sent.push(request); }});
  const start = source.indexOf('function quickDeviceOptions(');
  vm.runInContext(source.slice(start, source.indexOf('\nfunction changeRuntimeDeviceProfile(', start)), ctx);
  return {ctx, record, sent, toasts};
}
test('runtime table offers device and GPU quick choices that save through the restart path', async () => {
  const {ctx, sent, toasts} = quickContext();
  const markup = ctx.renderQuickSettings(ctx.runtimeRecord('r-one'), false);
  assert.match(markup, /data-quick-device="r-one"[^>]*title="[^"]*restarts Android/);
  assert.match(markup, /<optgroup label="Google"><option value="pixel" >Pixel 8<\/option>/);
  assert.match(markup, /value="\/dev\/dri\/renderD129"  disabled>nvidia · renderD129/);
  await ctx.changeQuickSetting({dataset:{quickDevice:'r-one'}, value:'pixel'});
  await ctx.changeQuickSetting({dataset:{quickGpu:'r-one'}, value:'/dev/dri/renderD128'});
  await ctx.changeQuickSetting({dataset:{quickGpu:'r-one'}, value:'auto'});       // unchanged: nothing sent
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [
    {op:'device_set', id:'r-one', profile:'pixel', custom:null, revision:'rev1'},
    {op:'gpu_set', id:'r-one', node:'/dev/dri/renderD128'}]);
  assert.equal(ctx.runtimeDeviceDraft.size + ctx.runtimeGpuDraft.size, 0);   // stale page drafts dropped
  ctx.controllerMutation = true;
  const select = {dataset:{quickGpu:'r-one'}, value:'software'};
  await ctx.changeQuickSetting(select);
  assert.equal(select.value, 'auto');                                          // reverted while busy
  assert.equal(sent.length, 2); assert.equal(toasts[0][1], 'error');
});
test('quick choices are locked while busy and GPU waits for prepared images', () => {
  const {ctx, record} = quickContext('Allocated');
  const markup = ctx.renderQuickSettings(record, false);
  assert.doesNotMatch(markup, /data-quick-device="r-one"[^>]*disabled/);
  assert.match(markup, /data-quick-gpu="r-one"[^>]*disabled/);
  assert.match(ctx.renderQuickSettings({...record, state:'Stopped'}, true), /data-quick-device="r-one"[^>]*disabled/);
  assert.equal(ctx.renderQuickSettings({id:'x', state:'Stopped'}, false), '<div class="runtime-quick"></div>');
});
test('settings default to Simple and remember Advanced per viewer', () => {
  const page = {classList:{set:new Set(['settings-simple']), toggle(name, on) { on ? this.set.add(name) : this.set.delete(name); }}};
  const buttons = ['simple', 'advanced'].map(mode => ({dataset:{settingsMode:mode}, attrs:{}, setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(_, fn) { this.click = fn; }}));
  const store = new Map();
  const ctx = vm.createContext({localStorage:{getItem:k => store.get(k) ?? null, setItem:(k, v) => store.set(k, v)},
    document:{getElementById:() => page, querySelectorAll:() => buttons}});
  const start = source.indexOf('const SETTINGS_MODE_KEY');
  vm.runInContext(source.slice(start, source.indexOf("document.getElementById('runtime-settings-back')", start)), ctx);
  assert.ok(page.classList.set.has('settings-simple'));
  buttons[1].click();
  assert.ok(!page.classList.set.has('settings-simple'));
  assert.equal(store.get('anvildroid.settingsMode'), 'advanced');
  assert.equal(buttons[1].attrs['aria-pressed'], 'true');
  ctx.localStorage.getItem = () => { throw new Error('blocked'); };
  assert.equal(ctx.readSettingsMode(), 'simple');                              // storage failures fall back safely
});
test('GPU chips use readable model names', () => {
  const {ctx} = quickContext();
  assert.equal(ctx.gpuShortName({vendor_id:'0x8086', name:'Intel TigerLake-LP GT2 [Iris Xe Graphics]'}), 'Intel Iris Xe Graphics');
  assert.equal(ctx.gpuShortName({vendor_id:'0x10de', name:'NVIDIA GeForce RTX 3060'}), 'NVIDIA GeForce RTX 3060');
  assert.equal(ctx.gpuShortName({vendor_id:'0x1002', name:'Navi 23 [Radeon RX 6600]'}), 'AMD Radeon RX 6600');
  assert.equal(ctx.gpuShortName({driver:'virtio_gpu', node:'/dev/dri/renderD130'}), 'virtio_gpu · renderD130');
});
test('runtime page refreshes when the controller watch reports a change, and stops for old controllers', async () => {
  const replies = [{token:'a'.repeat(32), changed:false}, {token:'b'.repeat(32), changed:true}, new Error('UNSUPPORTED: Operation is not available')];
  const sent = [], refreshes = [];
  const ctx = vm.createContext({document:{hidden:false}, currentPage:'runtime', controllerLoading:false, controllerMutation:false, runtimeOperations:new Map(),
    setTimeout, refreshController:async options => { refreshes.push(options); },
    controllerRequest:async request => { sent.push(request.since); const next = replies.shift(); if (next instanceof Error) throw next; return next; }});
  const start = source.indexOf('let controllerWatchToken = null');
  vm.runInContext(source.slice(start, source.indexOf('\nasync function refreshController(', start)), ctx);
  await ctx.runControllerWatch();
  assert.deepEqual(sent, [null, 'a'.repeat(32), 'b'.repeat(32)]);      // baseline, then wait from each token
  assert.deepEqual(JSON.parse(JSON.stringify(refreshes)), [{light:true}]); // only the real change refreshed
  assert.equal(vm.runInContext('controllerWatchUnsupported', ctx), true);
  await ctx.runControllerWatch();                                       // no retry against an old controller
  assert.equal(sent.length, 3);
});
test('controller watch only runs while a runtime page is visible', async () => {
  const sent = [];
  const ctx = vm.createContext({document:{hidden:false}, currentPage:'apps', controllerLoading:false, controllerMutation:false, runtimeOperations:new Map(), setTimeout,
    refreshController:async () => {}, controllerRequest:async request => { sent.push(request); ctx.document.hidden = true; return {token:'c'.repeat(32), changed:false}; }});
  const start = source.indexOf('let controllerWatchToken = null');
  vm.runInContext(source.slice(start, source.indexOf('\nasync function refreshController(', start)), ctx);
  await ctx.runControllerWatch();
  assert.equal(sent.length, 0);                                         // Apps page: no watch
  ctx.currentPage = 'runtime-settings';
  await ctx.runControllerWatch();
  assert.equal(sent.length, 1);                                         // hidden after one round: loop ends
  assert.deepEqual({...sent[0]}, {op:'watch', since:null, timeout:20});
});
function updateContext({confirm = true, invokeImpl} = {}) {
  const nodes = new Map(), toasts = [], progress = [], invoked = [];
  const node = id => { if (!nodes.has(id)) nodes.set(id, {id, hidden:false, disabled:false, textContent:'', listeners:{},
    addEventListener(type, fn) { this.listeners[type] = fn; }, querySelector() { return node(id + ':pre'); }}); return nodes.get(id); };
  const store = new Map();
  const ctx = vm.createContext({document:{getElementById:node}, setTimeout:(fn) => { ctx.pending = fn; return 1; }, clearTimeout(){},
    localStorage:{getItem:k => store.get(k) ?? null, setItem:(k, v) => store.set(k, v)}, Date,
    showAppToast:(text, kind) => toasts.push([kind, text]), showProgressToast:(key, item) => progress.push(item), clearProgressToast(){},
    AppDialog:{confirm:async () => confirm},
    tauriInvoke:async (command, args) => { invoked.push(command); return invokeImpl ? invokeImpl(command, args) : null; }});
  const start = source.indexOf('// --- Updates (GitHub Releases');
  vm.runInContext(source.slice(start, source.indexOf('\nasync function refreshSettings(', start)), ctx);
  return {ctx, node, toasts, progress, invoked, store};
}
const RELEASE = {current:'0.1.6', latest:'0.1.7', update_available:true, installable:true, kind:'deb', asset:'anvildroid-controller_0.1.7_amd64.deb',
  notes:'Fixes', pages:'https://collyn.github.io/anvildroid'};
test('update section shows the available release, the matching repository command and install controls', async () => {
  const {ctx, node, store} = updateContext({invokeImpl:command => command === 'check_update' ? RELEASE : null});
  await ctx.checkForUpdate();
  assert.equal(node('btn-install-update').hidden, false);
  assert.equal(node('btn-install-update').textContent, 'Install 0.1.7');
  assert.equal(node('update-status').textContent, 'AnvilDroid 0.1.7 is available.');
  assert.equal(node('update-summary').textContent, 'AnvilDroid 0.1.6 · Debian/Ubuntu package');
  assert.equal(node('update-repo-command').textContent, 'curl -fsSL https://collyn.github.io/anvildroid/install.sh | sudo bash');
  assert.equal(node('update-notes').hidden, false);
  assert.ok(Number(store.get('anvildroid.updateCheckedAt')) > 0);
  ctx.updateInfo = {...RELEASE, update_available:false};
  vm.runInContext('updateInfo = ' + JSON.stringify({...RELEASE, update_available:false}) + '; renderUpdate()', ctx);
  assert.equal(node('btn-install-update').hidden, true);
  assert.equal(node('update-status').textContent, 'AnvilDroid is up to date.');
  vm.runInContext('updateInfo = ' + JSON.stringify({...RELEASE, kind:'portable', installable:false}) + '; renderUpdate()', ctx);
  assert.equal(node('update-repo').hidden, true);
  assert.match(node('update-status').textContent, /Download it from the release page/);
});
test('installing asks first, polls progress and offers a restart only after success', async () => {
  const statuses = [{status:'Downloading', percent:40, phase:'Downloading x', version:'0.1.7'}, {status:'Installed', version:'0.1.7'}];
  const {ctx, node, toasts, progress, invoked} = updateContext({invokeImpl:command =>
    command === 'check_update' ? RELEASE : command === 'install_update' ? {status:'Downloading', percent:0} : statuses.shift()});
  await ctx.checkForUpdate();
  await ctx.installUpdate();
  assert.ok(invoked.includes('install_update'));
  await ctx.pending();                       // Downloading 40 %
  assert.equal(progress.at(-1).percent, 40);
  assert.equal(node('btn-install-update').disabled, true);
  await ctx.pending();                       // Installed
  assert.equal(node('btn-restart-update').hidden, false);
  assert.equal(node('btn-install-update').hidden, true);
  assert.deepEqual(toasts.at(-1), ['success', 'AnvilDroid 0.1.7 installed. Restart AnvilDroid to use it.']);
  const declined = updateContext({confirm:false, invokeImpl:command => command === 'check_update' ? RELEASE : null});
  await declined.ctx.checkForUpdate();
  await declined.ctx.installUpdate();
  assert.ok(!declined.invoked.includes('install_update'));
});
test('automatic update checks run at most once a day and tolerate clock changes', () => {
  const {ctx, store} = updateContext();
  const now = 1_800_000_000_000;
  assert.equal(ctx.updateCheckDue(now), true);
  store.set('anvildroid.updateCheckedAt', String(now - 3600 * 1000));
  assert.equal(ctx.updateCheckDue(now), false);
  store.set('anvildroid.updateCheckedAt', String(now - 25 * 3600 * 1000));
  assert.equal(ctx.updateCheckDue(now), true);
  store.set('anvildroid.updateCheckedAt', String(now + 3600 * 1000));  // clock moved back
  assert.equal(ctx.updateCheckDue(now), true);
});
