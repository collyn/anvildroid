const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js', 'utf8');
function launcher(send, tick = () => {}) {
  const notices = [];
  const context = vm.createContext({
    probeInstallBusy:false, pendingLaunch:null, pendingAppAction:false, controllerMutation:false, runtimeBatchBusy:false,
    RuntimeWindow:require('../crates/anvildroid-gui/src-frontend/runtime-window.js'),
    RuntimeBatch:require('../crates/anvildroid-gui/src-frontend/runtime-batch.js'), window:{}, console:{debug(){}}, renderController(){},
    document:{getElementById:()=>({})},
    refreshBatchRecord:async()=>({id:'runtime',name:'Test',state:'Running',display:{mode:'desktop'}}),
    pendingWaydroidRuntime:null, allApps:[], managedLibrary:[{id:'runtime', state:'Running', name:'Test'}],
    controllerRequest:send, tauriInvoke:async()=>null, renderApps:() => {},
    showAppToast:(message, type) => notices.push({message, type}),
    setTimeout:callback => { tick(context); callback(); },
  });
  vm.runInContext(source.slice(source.indexOf('function controllerBusy('), source.indexOf('const controllerRequest =')), context);
  vm.runInContext(source.slice(source.indexOf('async function openWaydroid('), source.indexOf("document.getElementById('probe-open-waydroid')")), context);
  vm.runInContext(source.slice(source.indexOf('async function launchRuntimeWaydroidWindow(')), context);
  return {context, notices, open:() => vm.runInContext("openWaydroid('runtime')", context)};
}
const success = {id:'job', runtime_id:'runtime', action:'full_ui', package:'org.anvildroid.desktop', status:'Succeeded', result:{window:{state:'ready',activation:'active'}}};

test('Waydroid retains a click until the preceding app action finishes', async () => {
  let calls = 0;
  const app = launcher(async () => { calls++; return success; }, context => { context.pendingAppAction = false; });
  app.context.pendingAppAction = true;
  await app.open();
  assert.equal(calls, 1);
  assert.equal(app.notices.at(-1).type, 'success');
  assert.equal(app.context.pendingAppAction, false);
});

test('Waydroid retries BUSY before acceptance, but never resubmits an accepted job', async () => {
  const calls = [];
  const app = launcher(async request => {
    calls.push(request.op);
    if (calls.length === 1) throw new Error('Runtime controller: Runtime already has an operation');
    return {...success, status:request.op === 'app_action' ? 'Running' : 'Succeeded'};
  });
  await app.open();
  assert.deepEqual(calls, ['app_action', 'app_action', 'app_job']);
  assert.equal(app.notices.at(-1).type, 'success');
});

test('repeated clicks do not submit duplicate Waydroid jobs', async () => {
  let finish, calls = 0;
  const app = launcher(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  const first = app.open();
  await app.open();
  for(let i=0;calls===0&&i<10;i++)await Promise.resolve();
  assert.equal(calls, 1);
  assert.match(app.notices.at(-1).message, /already opening/);
  finish(success);
  await first;
  assert.equal(app.context.pendingWaydroidRuntime, null);
});

test('real launch failures are not retried and release the action guard', async () => {
  let calls = 0;
  const app = launcher(async () => { calls++; throw new Error("Can't find service: package"); });
  await app.open();
  assert.equal(calls, 1);
  assert.equal(app.notices.at(-1).type, 'error');
  assert.equal(app.context.pendingAppAction, false);
  assert.equal(app.context.pendingWaydroidRuntime, null);
});

test('waiting times out without clearing another action guard', async () => {
  let calls = 0;
  const app = launcher(async () => { calls++; return success; });
  app.context.pendingAppAction = true;
  await app.open();
  assert.equal(calls, 0);
  assert.equal(app.context.pendingAppAction, true);
  assert.equal(app.context.pendingWaydroidRuntime, null);
  assert.equal(app.notices.at(-1).type, 'error');
});

test('persistent BUSY has a bounded retry count', async () => {
  let calls = 0;
  const app = launcher(async () => { calls++; throw {code:'BUSY'}; });
  await app.open();
  assert.equal(calls, 20);
  assert.equal(app.context.pendingAppAction, false);
});
