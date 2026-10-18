// Pure frontend regression checks. No Tauri, Android or host services are used.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/src-frontend/main.js`, 'utf8');

function fixture() {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {style:{display:'none'}, setAttribute(){}, classList:{add(){}, remove(){}, contains:()=>true}, removeAttribute(){}});
    return nodes.get(id);
  };
  const ctx = vm.createContext({
    document:{getElementById:node, querySelectorAll:selector=>selector === '[data-controller-op="restart"]' ? [node('restart')] : []},
    btnStart:node('start'), btnStop:node('stop'), btnRecover:node('recover'),
    controllerMutation:false, runtimeBatchBusy:false, controllerLoading:false, runtimeOperations:new Map(),
    defaultRuntimeOperation:null, runtimeTransition:null, pendingLaunch:null,
    pendingAppAction:false, probeInstallBusy:false, apkInstallBusy:false,
    existingRuntime:{id:'default', name:'Default', state:'Stopped', managed:true},
    existingRuntimeError:'', libraryDefaultState:'Stopped', controllerRecords:[],
    runtimeBatchSelection:new Set(), controllerRestartBlocked:()=>false,
    imageDownloadBusy:false, imageDownloadActive:()=>false, controllerRequirements:{can_start:true},
    RuntimeBatch:require('./src-frontend/runtime-batch.js'),
    runtimeSettingsOpen:new Set(), escapeHtml:String,
    renderRuntimeUsage:()=>'', updateDefaultRuntimeBadge(){},
    renderGpuControls:()=>'', renderRuntimeGraphics:()=>'', renderRuntimeArm:()=>'',
    renderRuntimeGoogle:()=>'', renderResourceControls:()=>'', renderRuntimeHealth:()=>'', renderRuntimeDevice:()=>'',
    renderController(){ctx.renderExistingRuntime();}, refreshExistingRuntime:async()=>{},
    refreshApps(){}, refreshStatus(){}, setTimeout(){}, showAppToast(){},
  });
  for (const [start, end] of [
    ['function updateRuntimeButtons(', 'async function refreshStatus('],
    ['function runtimeOperationBusy(', 'function runtimePlanBlocked('],
    ['function runtimePlanBlocked(', 'function runtimeBatchRecords('],
    ['function runtimeBatchRecords(', "document.getElementById('runtime-table').addEventListener('change'"],
    ['function renderExistingRuntime(', 'let imageCatalogBusy'],
    ['async function runtimeAction(', "btnStart.addEventListener"],
  ]) vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))), ctx);
  node('restart').dataset = {controllerId:'default'};
  ctx.runtimeRecord = id => id === 'default' ? ctx.existingRuntime : ctx.controllerRecords.find(record => record.id === id);
  return {ctx, node};
}

test('first Default render displays its lifecycle action without a separate health refresh', () => {
  const {ctx, node} = fixture();
  for (const [state, start, stop, recover] of [
    ['Stopped', '', 'none', 'none'],
    ['Running', 'none', '', 'none'],
    ['Frozen', 'none', '', 'none'],
    ['Starting', 'none', '', 'none'],
    ['SessionLost', 'none', 'none', ''],
  ]) {
    ctx.existingRuntime.state = state;
    ctx.renderExistingRuntime();
    assert.equal(node('start').style.display, start, state);
    assert.equal(node('stop').style.display, stop, state);
    assert.equal(node('recover').style.display, recover, state);
  }
});

test('another runtime stopping never locks Default lifecycle, even if its settings cannot load', () => {
  const {ctx, node} = fixture();
  ctx.runtimeOperations.set('other', 'stop');
  for (const record of [null, {...ctx.existingRuntime, detailsLoading:true}, {...ctx.existingRuntime, unavailable:true}]) {
    ctx.existingRuntime = record;
    ctx.renderExistingRuntime();
    for (const id of ['start','stop','recover']) assert.equal(node(id).disabled, false);
    assert.equal(ctx.runtimeOperationBusy('other'), true);
  }
});

test('Default remains locked for its own lifecycle and shared mutations', () => {
  const {ctx, node} = fixture();
  for (const flag of ['controllerMutation','runtimeBatchBusy','defaultRuntimeOperation','runtimeTransition','pendingLaunch','pendingAppAction','probeInstallBusy']) {
    ctx[flag] = true;
    ctx.renderExistingRuntime();
    for (const id of ['start','stop','recover']) assert.equal(node(id).disabled, true, flag);
    ctx[flag] = null;
  }
  ctx.runtimeOperations.set('default', 'stop');
  ctx.renderExistingRuntime();
  assert.equal(node('start').disabled, true);
  ctx.runtimeOperations.clear();
  ctx.renderExistingRuntime();
  assert.equal(node('start').disabled, false);
});

test('refreshing after an unrelated Stop does not block Default Start/Stop/Restart plans', () => {
  const {ctx, node} = fixture();
  ctx.controllerLoading = true;
  ctx.runtimeOperations.set('other', 'stop');
  ctx.existingRuntime.state = 'Running';
  ctx.renderRuntimeBatchSelection();
  assert.equal(node('restart').disabled, false);
  for (const op of ['start','stop','restart']) {
    assert.equal(ctx.runtimePlanBlocked(op, [{id:'default'}]), false);
    assert.equal(ctx.runtimePlanBlocked(op, [{id:'other'}]), true);
  }
  assert.equal(ctx.runtimePlanBlocked('delete', [{id:'default'}]), true);
  ctx.runtimeOperations.set('default', 'stop');
  ctx.renderRuntimeBatchSelection();
  assert.equal(node('restart').disabled, true);
});

test('Default action executes while another Stop is pending and rejects duplicate clicks through refresh', {timeout:1000}, async () => {
  const {ctx, node} = fixture();
  ctx.runtimeOperations.set('other', 'stop');
  ctx.existingRuntime.unavailable = true;
  let calls = 0, finish;
  ctx.invoke = async () => {calls++; return {success:true};};
  ctx.trackRuntimeTransition = work => work();
  ctx.refreshExistingRuntime = () => new Promise(resolve => {finish = resolve;});
  const action = ctx.runtimeAction('start_runtime', node('start'));
  await Promise.resolve(); await Promise.resolve();
  ctx.renderExistingRuntime();
  assert.equal(node('start').disabled, true);
  await ctx.runtimeAction('start_runtime', node('start'));
  assert.equal(calls, 1);
  finish(); await action;
  ctx.renderExistingRuntime();
  assert.equal(node('start').disabled, false);
  assert.equal(ctx.runtimeOperationBusy('other'), true);
});
