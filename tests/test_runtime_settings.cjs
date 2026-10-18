const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {apply, wait} = require('../crates/anvildroid-gui/src-frontend/runtime-settings.js');

function fixture(state = 'Running', failure) {
  const calls = [];
  return {calls, options: {
    send: async request => {
      calls.push(request.op);
      if (request.op === 'refresh') return {id:'test', state};
      assert.notEqual(state, 'Running');
      if (failure === 'apply') throw new Error('Invalid payload');
      return {enabled:true};
    },
    transition: async op => {
      calls.push(op);
      if (failure === op) throw new Error(`${op} failed`);
      state = op === 'stop' ? 'Stopped' : 'Running';
    },
  }};
}
for (const op of ['arm_set', 'gpu_set', 'resources_set', 'display_set', 'backend_set', 'device_set']) {
  test(`${op} automatically stops, applies and restarts a running runtime`, async () => {
    const {calls, options} = fixture();
    const result = await apply({op,id:'test'}, options);
    assert.equal(result.restarted, true);
    assert.deepEqual(calls, ['refresh','stop',op,'start']);
  });
}
test('stopped runtime remains stopped', async () => {
  const {calls, options} = fixture('Stopped');
  assert.equal((await apply({op:'arm_set',id:'test'}, options)).restarted, false);
  assert.deepEqual(calls, ['refresh','arm_set']);
});
test('failed stop never writes a setting or starts a second worker', async () => {
  const {calls, options} = fixture('Running','stop');
  await assert.rejects(apply({op:'arm_set',id:'test'}, options), /stop failed/);
  assert.deepEqual(calls, ['refresh','stop']);
});
test('rejected setting restores previously running Android and reports the failure', async () => {
  const {calls, options} = fixture('Running','apply');
  await assert.rejects(apply({op:'arm_set',id:'test'}, options), /Invalid payload.*restarted/);
  assert.deepEqual(calls, ['refresh','stop','arm_set','start']);
});
test('restart failure does not report success or retry in a loop', async () => {
  const {calls, options} = fixture('Running','start');
  await assert.rejects(apply({op:'arm_set',id:'test'}, options), /Setting saved.*Restart failed/);
  assert.deepEqual(calls, ['refresh','stop','arm_set','start']);
});
test('wait tracks the submitted job through completion', async () => {
  let calls = 0;
  const r = await wait(async () => ({id:'test',state:++calls === 1 ? 'Stopping' : 'Stopped',job:{id:'job',status:calls === 1 ? 'Running' : 'Succeeded'}}), 'test','job','Stopped',{sleep:async()=>{}});
  assert.equal(r.state, 'Stopped');
  assert.equal(calls, 2);
});
test('wait rejects another operation, failed boot and bounded timeout', async () => {
  await assert.rejects(wait(async () => ({id:'test',job:{id:'other'}}),'test','job','Stopped'), /operation changed/);
  await assert.rejects(wait(async () => ({id:'test',state:'Error',job:{id:'job',status:'Failed',error:'Bad image'}}),'test','job','Running'), /Bad image/);
  let clock = 0;
  await assert.rejects(wait(async () => ({id:'test',state:'Starting',job:{id:'job',status:'Running'}}),'test','job','Running',{now:()=>clock, sleep:async()=>{clock += 1000;}, timeout:2000}), /Timed out/);
});

const source = fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
const ctx = vm.createContext({runtimeGpuDraft:new Map(), runtimeArmDraft:new Map(), runtimeResourceDraft:new Map(), escapeHtml:String, resourceBudgetHtml:()=>''});
vm.runInContext(source.slice(source.indexOf('function renderResourceControls('),source.indexOf('async function checkRuntimeGraphics(')),ctx);
vm.runInContext(source.slice(source.indexOf('const ARM_BUILTIN_SHA'),source.indexOf('async function installRuntimeArm(')),ctx);
const record = {id:'test', state:'Running', gpu:{selection:'auto',devices:[]}, arm:{supported:true,enabled:false}, resources:{host:{supported:true,memory_total_mib:8192,memory_available_mib:4096,cpu_count:4},configured:{memory_mib:2048,cpu_count:2}}};
test('running managed runtime exposes ARM, graphics and limits with restart labels', () => {
  for (const render of ['renderRuntimeArm','renderGpuControls','renderResourceControls']) {
    const markup = ctx[render](record,false);
    const button = markup.match(/<button[^>]*data-controller-op="(?:arm_set|gpu_set|resources_set)"[^>]*>(.*?)<\/button>/)[0];
    assert.doesNotMatch(button, /\sdisabled/);
    assert.match(button, /restart/);
    assert.match(ctx[render]({...record,state:'Starting'},false), /\sdisabled/);
    assert.match(ctx[render](record,true), /\sdisabled/);
  }
});
test('managed image with inherited ARM bridge still allows provider selection', () => {
  assert.match(ctx.renderRuntimeArm({...record,arm:{...record.arm,inherited:true}},false), /data-arm-engine/);
});

const {prepareReinstall} = require('../crates/anvildroid-gui/src-frontend/runtime-settings.js');
for (const state of ['Running','Frozen','Stopped','Allocated','Prepared','Error']) {
  test(`reinstall prepares ${state} without starting Android`, async () => {
    let current=state;const calls=[];
    await prepareReinstall({id:'test',confirmed_name:'Test'}, {
      send:async()=>{calls.push('refresh');return {id:'test',name:'Test',state:current};},
      transition:async op=>{calls.push(op);current='Stopped';},
    });
    assert.deepEqual(calls,['Running','Frozen'].includes(state)?['refresh','stop','refresh']:['refresh']);
  });
}
for (const failure of ['stop','still-running','rename','identity','busy','deleted']) {
  test(`reinstall fails closed for ${failure}`, async()=>{
    let after=false;
    await assert.rejects(prepareReinstall({id:'test',confirmed_name:'Test'}, {
      send:async()=>({id:after&&failure==='identity'?'other':'test',name:after&&failure==='rename'?'Changed':'Test',state:after&&failure!=='still-running'?'Stopped':'Running',appJob:after&&failure==='busy'?{status:'Running'}:null,deletion_pending:after&&failure==='deleted'}),
      transition:async()=>{if(failure==='stop')throw Error('Stop failed');after=true;},
    }));
  });
}

test('Frozen boot settings thaw through Stop before apply and restart',async()=>{
  const calls=[];
  const result=await apply({op:'gpu_set',id:'sleep',node:'auto'},{send:async r=>{calls.push(r.op);return r.op==='refresh'?{id:'sleep',state:'Frozen'}:{saved:true};},transition:async op=>calls.push(op)});
  assert.equal(result.restarted,true);assert.deepEqual(calls,['refresh','stop','gpu_set','start']);
});

// Exercise the production Settings ARM actions with inert DOM/IPC boundaries.
// These checks do not claim graphical rendering or Android ARM execution.
function armSettingsFixture({engine='libhoudini', installed=true, id='test', arm={}, download, mutationError=''} = {}) {
  const digest = 'b'.repeat(64);
  const calls = [];
  const drafts = new Map([[id, {engine, sha256:digest}]]);
  const record = {id, state:'Stopped', arm:{supported:true,enabled:true,provider:'libndk',sources:[{engine:'libhoudini',version:'13',android:'Android 13',sha256:digest,installed}],...arm}};
  let change;
  const context = vm.createContext({
    runtimeArmDraft:drafts, controllerMutation:false, controllerError:'', escapeHtml:String,
    runtimeRecord:requested => requested === id ? record : null,
    document:{querySelector:selector => ({value:selector.includes('arm-engine') ? engine : engine === 'none' ? 'none' : digest}),addEventListener:(type, listener) => { assert.equal(type,'change'); change=listener; }},
    renderController:()=>calls.push({op:'render'}),
    tauriInvoke:async (op,request)=>{calls.push({op,...request});return download ? download(request) : {sha256:digest};},
    mutateController:async request=>{calls.push({...request});context.controllerError=mutationError;},
    showAppToast:(message,kind)=>calls.push({op:'toast',message,kind}),
  });
  vm.runInContext(source.slice(source.indexOf('const ARM_BUILTIN_SHA'),source.indexOf('// Start only after all runtime request helpers')),context);
  return {context,calls,drafts,record,digest,change:()=>change};
}

test('Settings switches to an already cached provider with the exact selected digest', async()=>{
  const f=armSettingsFixture();
  await f.context.installRuntimeArm('test');
  assert.deepEqual(f.calls,[{op:'render'},{op:'arm_set',id:'test',enabled:true,archive_sha256:f.digest},{op:'render'}]);
  assert.equal(f.drafts.has('test'),false);
});
test('Settings downloads the selected verified catalog entry before applying it',async()=>{
  const f=armSettingsFixture({installed:false});
  await f.context.installRuntimeArm('test');
  assert.deepEqual(f.calls.map(call=>call.op),['render','import_arm_translation','arm_set','render']);
  assert.deepEqual(f.calls[1],{op:'import_arm_translation',source:f.digest,sourceKind:'builtin',expectedSha256:''});
});
for (const failure of ['download','digest']) {
  test(`Settings ${failure} failure never changes the current provider and permits retry`,async()=>{
    let fail=true;
    const f=armSettingsFixture({installed:false,download:()=>{if(fail){if(failure==='download')throw Error('Offline');return {sha256:'wrong'};}return {sha256:f.digest};}});
    await f.context.installRuntimeArm('test');
    assert.equal(f.calls.some(call=>call.op==='arm_set'),false);
    assert.match(f.context.controllerError,/ARM installation failed/);
    assert.equal(f.drafts.has('test'),true);
    assert.equal(f.calls.filter(call=>call.op==='toast').length,1);
    fail=false;
    await f.context.installRuntimeArm('test');
    assert.equal(f.calls.filter(call=>call.op==='arm_set').length,1);
    assert.equal(f.drafts.has('test'),false);
  });
}
test('Settings retains selected provider draft after a rejected configuration change',async()=>{
  const f=armSettingsFixture({mutationError:'Previous configuration retained'});
  await f.context.installRuntimeArm('test');
  assert.equal(f.drafts.has('test'),true);
  assert.equal(f.calls.filter(call=>call.op==='arm_set').length,1);
});
test('Settings disabling translation sends no archive or download request',async()=>{
  const f=armSettingsFixture({engine:'none'});
  await f.context.installRuntimeArm('test');
  assert.deepEqual(f.calls,[{op:'arm_set',id:'test',enabled:false}]);
  assert.equal(f.drafts.has('test'),false);
});
for (const enabled of [true,false]) {
  test(`Default Settings ${enabled?'disable':'restore'} only toggles existing provider properties`,async()=>{
    const f=armSettingsFixture({id:'default',arm:{enabled,restore_available:true}});
    await f.context.installRuntimeArm('default');
    assert.deepEqual(f.calls,[{op:'arm_set',id:'default',enabled:!enabled}]);
    assert.doesNotMatch(f.context.renderRuntimeArm(f.record,false),/data-arm-engine|Import another version/);
  });
}
test('Default Settings refuses restore without saved provider properties',async()=>{
  const f=armSettingsFixture({id:'default',arm:{enabled:false,restore_available:false}});
  await f.context.installRuntimeArm('default');
  assert.deepEqual(f.calls,[]);
  assert.match(f.context.renderRuntimeArm(f.record,false),/disabled.*>Restore translation/);
});
test('Settings rejects a digest from the other engine without downloading or applying',async()=>{
  const f=armSettingsFixture({engine:'libndk',installed:false});
  await f.context.installRuntimeArm('test');
  assert.deepEqual(f.calls,[]);
});
test('Settings serializes provider downloads against repeated clicks and other mutations',async()=>{
  let release;
  const f=armSettingsFixture({installed:false,download:()=>new Promise(resolve=>{release=resolve;})});
  const pending=f.context.installRuntimeArm('test');
  await f.context.installRuntimeArm('test');
  assert.equal(f.calls.filter(call=>call.op==='import_arm_translation').length,1);
  release({sha256:f.digest});
  await pending;
  f.context.controllerMutation=true;
  await f.context.installRuntimeArm('test');
  assert.equal(f.calls.filter(call=>call.op==='arm_set').length,1);
});

test('Settings provider selection stores a runtime-scoped draft and selects its first catalog version',()=>{
  const f=armSettingsFixture();
  let blurred=false;
  f.change()({target:{dataset:{armEngine:'test'},value:'libhoudini',blur:()=>{blurred=true;}}});
  assert.deepEqual({...f.drafts.get('test')},{engine:'libhoudini',sha256:f.digest});
  assert.equal(blurred,true);
  assert.deepEqual(f.calls,[{op:'render'}]);
});
test('Settings version selection preserves its engine and ignores unrelated change events',()=>{
  const f=armSettingsFixture();
  f.change()({target:{dataset:{unrelated:'test'},value:'ignored'}});
  assert.deepEqual(f.calls,[]);
  f.change()({target:{dataset:{armSelect:'test'},value:f.digest}});
  assert.deepEqual({...f.drafts.get('test')},{engine:'libhoudini',sha256:f.digest});
  assert.equal(f.drafts.size,1);
});

test('Settings distinguishes source variants and warns about an Android target mismatch',()=>{
  const f=armSettingsFixture({arm:{android_sdk:33}});
  f.record.arm.sources=[
    {engine:'libhoudini',version:'Brya 38795 (patched)',sha256:f.digest,installed:false,android:'Android 11; Intel / AMD',android_sdks:[30],variant:'houdini-brya-38795-patched'},
    {engine:'libhoudini',version:'Brya 38795',sha256:'c'.repeat(64),installed:true,android:'Android 11; Intel / AMD',android_sdks:[30]},
    {engine:'libhoudini',version:'Octopus 39598',sha256:'d'.repeat(64),installed:false,android:'Android 13; Intel',android_sdks:[33]},
  ];
  const markup=f.context.renderRuntimeArm(f.record,false);
  assert.match(markup,/Brya 38795 \(patched\)/);
  assert.match(markup,/value="c{64}"[^>]*>Brya 38795/);
  assert.match(markup,/Octopus 39598/);
  assert.match(markup,/Compatibility patch bytes and output checksum/);
  assert.match(markup,/arm-target-warning.*this runtime is Android SDK 33/);
  f.drafts.set('test',{engine:'libhoudini',sha256:'d'.repeat(64)});
  assert.doesNotMatch(f.context.renderRuntimeArm(f.record,false),/arm-target-warning/);
});
