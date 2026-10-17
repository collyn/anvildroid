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
  const calls = [], elements = new Map(), previous = {id:'r-one',state:'Stopped',display:{mode:'desktop'},gpu:{node:'gpu'},arm:{enabled:true}};
  const ctx = vm.createContext({
    controllerLoading:false, controllerMutation:false, runtimeOperations:new Map(), controllerTimer:null, controllerRequirements:{can_prepare:true,can_start:true,checks:[]},
    controllerRecords:[previous], controllerSetupError:'', armSourceState:{sources:[{sha256:'abc'}]},
    controllerNotice:'',controllerError:'',controllerRefreshDone:null,finishControllerRefresh:null,armSourcesRefresh:null,
    existingRuntime:{id:'default',state:'Stopped',gpu:{node:'gpu'},arm:{enabled:true}},existingRuntimeLoading:false,existingRuntimeError:'',
    document:{getElementById(id){if(!elements.has(id))elements.set(id,{style:{}});return elements.get(id);}},
    controllerRequest:async request=>{calls.push(request);if(request.op==='list')return [{id:'r-one',state:ctx.state}];if(request.op==='refresh')return {id:request.id,state:ctx.state};if(request.op==='requirements')return {can_prepare:true,can_start:true,checks:[]};if(request.op==='arm_sources')return [];return {};},
    RuntimeUsage:{sample(){},retain(){}},invalidateRuntimeGraphics(){},renderControllerSetup(){},renderArmSources(){},renderController(){},
    renderImageSourceOptions(){},renderRuntimeBatchSelection(){},scheduleRuntimeUsage(){},controllerHasActiveWork:()=>false,
    clearTimeout(){},setTimeout(){},state:'Stopped',currentPage:'runtime'
  });
  vm.runInContext(fn('refreshExistingRuntime', 'function renderExistingRuntime'),ctx);
  vm.runInContext(source.slice(source.indexOf('function refreshArmSources('), source.indexOf('let customUrlState =')),ctx);
  vm.runInContext(fn('refreshController', 'function imageForRequest'),ctx);
  return {ctx,calls};
}
test('light runtime refresh retains configuration, refreshes state and usage, and avoids setup probes', async () => {
  const {ctx,calls} = controllerContext();
  await ctx.refreshController({light:true});
  assert.equal(calls.length, 8);
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
