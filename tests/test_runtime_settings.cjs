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
for (const op of ['arm_set', 'gpu_set', 'resources_set', 'display_set', 'backend_set']) {
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
