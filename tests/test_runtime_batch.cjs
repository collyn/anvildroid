const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const Batch = require('../crates/anvildroid-gui/src-frontend/runtime-batch.js');
const Window = require('../crates/anvildroid-gui/src-frontend/runtime-window.js');
const Settings = require('../crates/anvildroid-gui/src-frontend/runtime-settings.js');
const source = fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js', 'utf8');
const record = (id, state = 'Stopped', extra = {}) => ({id, name: id, state, ...extra});
const ready = {can_start:true};
function harness(records = [record('one'), record('two')]) {
  const nodes = new Map(), listeners = new Map();
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {classList:{toggle(){}}, hidden:true, value:'', options:[{value:'installed'}], dataset:{},
      setAttribute(k,v){this[k]=v;}, addEventListener(type, fn){listeners.set(`${id}:${type}`,fn);},
      showModal(){this.open=true;},close(){this.open=false;listeners.get(`${id}:close`)?.();}});
    return nodes.get(id);
  }
  const checks = records.map(r => ({dataset:{runtimeCheck:r.id}}));
  const buttons = Batch.operations.map(op => ({dataset:{runtimeBatch:op}}));
  const context = {lazyFeatures:{ensure:async()=>{}}, controllerBusy:e=>e.code==='BUSY', RuntimeUsage:require('../crates/anvildroid-gui/src-frontend/runtime-usage.js'), RuntimeBatch:Batch, RuntimeSettings:Settings, RuntimeWindow:Window, window:{}, console:{debug(){}}, controllerRecords:records,
    existingRuntime:null, runtimeBatchSelection:new Set(), runtimeBatchBusy:false, runtimeBatchPlan:null,
    runtimeBadgeKind:state=>['Error','SessionLost','NotInstalled','Unavailable'].includes(state)?'error':['Running','Prepared'].includes(state)?'success':'info', controllerRestartBlocked:()=>false, controllerRestarting:false, apkInstallBusy:false, controllerMutation:false, controllerLoading:false, imageDownloadBusy:false, imageDownloadActive:()=>false, defaultRuntimeOperation:null, runtimeTransition:false, pendingLaunch:false, pendingAppAction:false, probeInstallBusy:false,
    controllerRequirements:ready, controllerTimer:null, controllerRefreshDone:Promise.resolve(),
    imageDownloadState:{available_images:[]}, escapeHtml:String, clearTimeout(){}, setTimeout,
    showAppToast(message, type){node('app-toast-message').textContent=message;node('app-toast').className=type;}, renderController(){}, renderAvailableImages(){}, refreshController:async()=>{}, refreshStatus:async()=>{},
    runtimeRecord:id=>context.controllerRecords.find(r=>r.id===id),
    activateRequestImage:async()=>{}, controllerRequest:async()=>{throw Error('unexpected request');},
    tauriInvoke:async()=>{throw Error('unexpected Tauri call');},
    document:{getElementById:node,querySelectorAll:selector=>selector==='[data-runtime-check]'?checks:selector==='[data-runtime-batch]'?buttons:[]}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('// Runtime table selection is independent')),context);
  return {context, node, checks, buttons, listeners};
}
(async () => {
  // Eligibility matrix, host requirements, busy/error/deletion states and default restrictions.
  const expected = {
    Allocated:['reinstall','delete'], Prepared:['start','reinstall','delete'],
    Stopped:['start','reinstall','delete'], Running:['stop','restart'], Frozen:['stop','restart'],
    Error:['start','stop','reinstall'], Provisioning:[], Starting:[], Stopping:[], Unknown:[],
  };
  for (const [state, ops] of Object.entries(expected)) assert.deepEqual(Batch.operations.filter(op=>Batch.eligible(record('r',state),op,ready)),ops);
  for (const extra of [{refreshError:'offline'},{unavailable:true},{job:{status:'Running'}},{appJob:{status:'Running'}},{storage:{job:{status:'Running'}}}])
    assert(Batch.operations.every(op=>!Batch.eligible(record('r','Stopped',extra),op,ready)));
  assert(!Batch.eligible(record('r'), 'start', {}));
  assert(!Batch.eligible(record('r','Stopped',{storage:{error:'disk missing'}}),'start',ready));
  assert(!Batch.eligible(record('r','Stopped',{import_status:'pending'}),'start',ready));
  assert(!Batch.eligible(record('default'), 'delete', ready));
  assert(!Batch.eligible(record('default'), 'reinstall', ready));
  assert(Batch.eligible(record('default','Stopped',{managed:true}), 'reinstall', ready));
  assert(Batch.eligible(record('default','Frozen'), 'open', ready));
  assert(Batch.eligible(record('r','Frozen',{display:{mode:'desktop'}}), 'open', ready));
  assert(!Batch.eligible(record('r','Error',{job:{operation:'prepare'}}),'start',ready));
  assert(Batch.eligible(record('r','Error',{job:{operation:'prepare'}}),'delete',ready));
  assert.deepEqual(Batch.operations.filter(op=>Batch.eligible(record('r','Stopped',{deletion_pending:true}),op,ready)),['delete']);
  const calls=[];
  const states={a:record('a','Running'),b:record('b','Running')};
  await Batch.run(Object.values(states),'restart',{requirements:ready,
    refresh:async id=>({...states[id]}),execute:async(r,op)=>{calls.push(`${r.id}:${op}`);states[r.id].state=op==='stop'?'Stopped':'Running';}});
  assert.deepEqual(calls,['a:stop','a:start','b:stop','b:start']);
  const recoveredRestart=await Batch.run([record('recover','Running')],'restart',{
    requirements:ready,refresh:async()=>record('recover','Stopped',{shutdown_warning:'Forced cleanup; recent unsaved changes may be lost.'}),
    execute:async()=>{},
  });
  assert.equal(recoveredRestart[0].status,'skipped');
  let refreshed=0;const recoveredCalls=[];
  const warnedRestart=await Batch.run([record('recover','Running')],'restart',{
    requirements:ready,refresh:async()=>record('recover',refreshed++ ? 'Stopped' : 'Running'),
    execute:async(r,op)=>{recoveredCalls.push(op);return op==='stop'?{shutdown_warning:'Forced cleanup; recent unsaved changes may be lost.'}:{};},
  });
  assert.deepEqual(recoveredCalls,['stop','start']);assert.equal(warnedRestart[0].status,'succeeded');
  assert.match(warnedRestart[0].detail,/unsaved/);
  const results=await Batch.run([record('bad','Running'),record('changed','Running'),record('ok','Running')],'restart',{
    requirements:ready,refresh:async id=>record(id,id==='changed'?'Stopped':'Running'),
    execute:async(r,op)=>{if(r.id==='bad')throw Error('stop failed');if(op==='start')throw Error('should not start without Stopped');},});
  assert.deepEqual(results.map(r=>r.status),['failed','skipped','failed']);
  assert.match(results[0].detail,/stop failed/);assert.match(results[2].detail,/Stop did not complete/);
  let writes=0;
  const renamed=await Batch.run([record('r')],'delete',{requirements:ready,refresh:async()=>record('r','Stopped',{name:'new name'}),execute:async()=>writes++});
  assert.equal(writes,0);assert.equal(renamed[0].status,'failed');
  const identity=await Batch.run([record('r')],'start',{requirements:ready,refresh:async()=>record('other'),execute:async()=>writes++});
  assert.equal(identity[0].status,'failed');assert.equal(writes,0);

  // Table selection, indeterminate select-all, pruning and busy locks.
  const h=harness();const c=h.context;
  h.listeners.get('runtime-table:change')({target:{id:'runtime-select-all',checked:true}});
  assert.equal(c.runtimeBatchSelection.size,2);assert(h.node('runtime-select-all').checked);
  h.listeners.get('runtime-table:change')({target:{dataset:{runtimeCheck:'one'},checked:false}});
  assert(h.node('runtime-select-all').indeterminate);assert.equal(c.runtimeBatchSelection.size,1);
  assert.equal(h.buttons.find(b=>b.dataset.runtimeBatch==='start').textContent,'Start (1)');
  c.controllerRecords=[record('one')];c.renderRuntimeBatchSelection();assert.equal(c.runtimeBatchSelection.size,0);assert(h.node('runtime-batch-toolbar').hidden);
  c.runtimeBatchBusy=true;h.listeners.get('runtime-table:change')({target:{id:'runtime-select-all',checked:true}});assert.equal(c.runtimeBatchSelection.size,0);
  c.renderRuntimeBatchSelection();assert(h.node('runtime-select-all').disabled);

  // Typed destructive confirmation and pinned targets; no writes on invalid confirmation/image/cancel.
  const d=harness();const dc=d.context;dc.runtimeBatchSelection.add('one');dc.renderRuntimeBatchSelection();
  const del=d.buttons.find(b=>b.dataset.runtimeBatch==='delete');
  d.listeners.get('runtime-batch-toolbar:click')({target:{closest:()=>del}});
  assert.equal(dc.runtimeBatchPlan.targets.length,1);
  await d.listeners.get('runtime-batch-form:submit')({preventDefault(){}});
  assert.match(d.node('runtime-batch-dialog-error').textContent,/DELETE/);assert(!dc.controllerMutation);
  d.listeners.get('runtime-batch-cancel:click')();assert.equal(dc.runtimeBatchPlan,null);
  const reinstall=d.buttons.find(b=>b.dataset.runtimeBatch==='reinstall');
  d.listeners.get('runtime-batch-toolbar:click')({target:{closest:()=>reinstall}});d.node('runtime-batch-confirm').value='REINSTALL';
  await d.listeners.get('runtime-batch-form:submit')({preventDefault(){}});assert.match(d.node('runtime-batch-dialog-error').textContent,/image/);assert(!dc.controllerMutation);

  // Managed transitions use the accepted job ID and wait for completion.
  const m=harness();let sent=[], waits=[];
  m.context.controllerRequest=async request=>{sent.push(request);return {...record(request.id,'Starting'),job:{id:'job-1'}};};
  m.context.RuntimeSettings={wait:async(send,id,job,expected)=>{waits.push({id,job,expected});return record(id,expected);}};
  await m.context.executeBatchOperation(record('one'),'start');assert.equal(sent[0].op,'start');assert.equal(waits[0].expected,'Running');
  await m.context.executeBatchOperation(record('one','Running'),'stop');assert.equal(waits[1].expected,'Stopped');
  await m.context.executeBatchOperation(record('one'),'reinstall',{flavor:'installed'});assert.equal(sent[2].confirmed_name,'one');assert.equal(waits[2].expected,'Prepared');
  m.context.controllerRequest=async()=>({id:'wrong',job:{id:'wrong'}});
  await assert.rejects(m.context.executeBatchOperation(record('one'),'start'),/Invalid runtime/);

  // Delete completes by disappearance, including the list/app_job race; failed jobs remain failures.
  for(const race of [false,true]){
    const t=harness();let lists=0;
    t.context.controllerRequest=async r=>{
      if(r.op==='delete')return {runtime_id:r.id,id:'delete-job'};
      if(r.op==='list')return ++lists===1&&race?[record('one')]:[];
      if(r.op==='app_job')throw Error('NOT_FOUND');
      throw Error('unexpected');
    };
    await t.context.executeBatchOperation(record('one'),'delete');
  }
  const f=harness();f.context.controllerRequest=async r=>r.op==='delete'?{runtime_id:r.id,id:'delete-job'}:r.op==='list'?[record('one')]:{id:'delete-job',status:'Failed',error:'disk error'};
  await assert.rejects(f.context.executeBatchOperation(record('one'),'delete'),/disk error/);

  // Existing runtime uses the existing Tauri lifecycle commands, not unsupported controller start/stop.
  const e=harness();const native=[];
  e.context.tauriInvoke=async(cmd,args)=>{native.push({cmd,args});return {success:true};};
  e.context.refreshBatchRecord=async()=>record('default','Running');
  await e.context.executeBatchOperation(record('default'),'start');assert.equal(native[0].cmd,'start_runtime');
  e.context.refreshBatchRecord=async()=>record('default','Stopped');
  await e.context.executeBatchOperation(record('default','Running'),'stop');assert.equal(native[1].cmd,'stop_runtime');
  e.context.tauriInvoke=async()=>({success:false,message:'native failed'});
  await assert.rejects(e.context.executeBatchOperation(record('default'),'start'),/native failed/);

  // Full submission releases locks, preserves failures for retry and summarizes partial results.
  const u=harness();const uc=u.context;uc.runtimeBatchSelection.add('one');uc.runtimeBatchSelection.add('two');uc.renderRuntimeBatchSelection();
  const start=u.buttons.find(b=>b.dataset.runtimeBatch==='start');u.listeners.get('runtime-batch-toolbar:click')({target:{closest:()=>start}});
  uc.refreshBatchRecord=async id=>record(id);uc.executeBatchOperation=async r=>{assert(!uc.controllerMutation&&!uc.runtimeBatchBusy);assert(vm.runInContext(`runtimeOperations.has('${r.id}')`,uc));if(r.id==='two')throw Error('boot failed');};
  await u.listeners.get('runtime-batch-form:submit')({preventDefault(){}});
  assert(!uc.controllerMutation&&!uc.runtimeBatchBusy);assert(!uc.runtimeBatchSelection.has('one'));assert(uc.runtimeBatchSelection.has('two'));
  assert.match(u.node('runtime-batch-result').innerHTML,/1 completed · 1 failed/);
  assert.match(u.node('runtime-batch-result').innerHTML,/boot failed/);
  // Failure, timeout and operation replacement never claim completion.
  for (const op of ['start','delete']) {
    const t=harness();let time=0;
    t.context.Date={now:()=>time+=400000};
    t.context.controllerRequest=async r=>op==='delete'?{runtime_id:r.id,id:'job'}:{id:r.id,job:{id:'job'}};
    if(op==='start')t.context.RuntimeSettings={wait:async()=>{throw Error('Timed out waiting');}};
    await assert.rejects(t.context.executeBatchOperation(record('one'),op),/Timed out/);
  }
  const changedJob=harness();changedJob.context.controllerRequest=async r=>r.op==='delete'?{runtime_id:r.id,id:'original'}:r.op==='list'?[record('one')]:{id:'replacement',status:'Succeeded'};
  await assert.rejects(changedJob.context.executeBatchOperation(record('one'),'delete'),/operation changed/);
  const existingTimeout=harness();let time=0;existingTimeout.context.Date={now:()=>time+=400000};
  existingTimeout.context.tauriInvoke=async()=>({success:true});
  await assert.rejects(existingTimeout.context.executeBatchOperation(record('default'),'start'),/Timed out/);
  const noStorage=harness();noStorage.context.controllerRequest=async r=>{assert.equal(r.op,'refresh');return record('default');};
  assert.equal((await noStorage.context.refreshBatchRecord('default')).state,'Stopped');
  // Render actual table templates: five columns, escaped names and retained checkboxes.
  const render=harness([record('one','Stopped',{name:'<unsafe>',storage:{path:'/tmp/one'},display:{mode:'desktop'}})]);
  const rc=render.context;
  Object.assign(rc,{selectedRuntimeId:'one',runtimeSettingsId:'one',controllerPending:null,runtimeSettingsOpen:new Set(),runtimeGraphics:new Map(),runtimeHealth:new Map(),
    renderExistingRuntime(){},syncRuntimeSettingsFeatures(){},document:{...rc.document,activeElement:null},
    escapeHtml:value=>String(value).replaceAll('<','&lt;').replaceAll('>','&gt;')});
  for(const name of ['renderGpuControls','renderRuntimeGraphics','renderRuntimeArm','renderRuntimeGoogle','renderResourceControls','renderRuntimeHealth','renderRuntimeDevice'])rc[name]=()=>'';
  const from=source.indexOf('function renderRuntimeUsage('),to=source.indexOf('\nfunction beginReinstall',from);
  vm.runInContext(source.slice(from,to),rc);
  rc.runtimeBatchSelection.add('one');rc.renderController();
  const markup=render.node('controller-runtimes').innerHTML;
  assert.match(markup,/data-runtime-check="one"[^>]*checked/);assert(!markup.includes('runtime-detail-row'));
  assert.match(render.node('runtime-settings-content').innerHTML,/managed-runtime/);
  assert(!markup.includes('<unsafe>'));assert(markup.includes('&lt;unsafe&gt;'));
  rc.controllerMutation=true;rc.runtimeBatchBusy=true;rc.renderController();assert.match(render.node('controller-runtimes').innerHTML,/data-runtime-check="one"[^>]*disabled/);
  // Per-row restart uses the shared dialog without inheriting table selection.
  const single = harness([record('one','Running'), record('two','Running')]);
  single.context.runtimeBatchSelection.add('two');
  const actionStart = source.indexOf('function handleRuntimeAction(');
  const actionEnd = source.indexOf("document.getElementById('runtime-table').addEventListener('click', handleRuntimeAction)", actionStart);
  vm.runInContext(source.slice(actionStart, actionEnd), single.context);
  const restartButton = {dataset:{controllerOp:'restart',controllerId:'one'},disabled:false};
  single.context.handleRuntimeAction({target:{closest:()=>restartButton}});
  assert(single.node('runtime-batch-dialog').open);
  assert.equal(single.context.runtimeBatchPlan.op,'restart');
  assert.deepEqual(Array.from(single.context.runtimeBatchPlan.targets, r=>r.id),['one']);
  assert(single.context.runtimeBatchSelection.has('two'));
  single.node('runtime-batch-dialog').close();
  single.context.controllerRecords[0].state='Stopped';
  single.context.handleRuntimeAction({target:{closest:()=>restartButton}});
  assert(!single.node('runtime-batch-dialog').open);
  single.context.controllerRecords[0].state='Running';
  single.context.controllerMutation=true;
  single.context.handleRuntimeAction({target:{closest:()=>restartButton}});
  assert(!single.node('runtime-batch-dialog').open);
  // Compact row action has an accessible label, and restart is state-gated.
  const summary = markup.slice(0, markup.indexOf('runtime-detail-row'));
  assert.match(summary,/runtime-action-icon[^>]*data-controller-op="restart"[^>]*aria-label="Restart · &lt;unsafe&gt;"[^>]*disabled/);
  rc.controllerRecords[0].state='Running';rc.renderController();
  const runningSummary=render.node('controller-runtimes').innerHTML.split('runtime-detail-row')[0];
  assert.match(runningSummary,/data-controller-op="restart"[^>]*><svg/);
  // Composite restart remains visible throughout both underlying states.
  vm.runInContext("runtimeBatchRestarting.add('one')",rc);
  for(const state of ['Running','Stopping','Stopped','Starting']) {
    rc.controllerRecords[0].state=state;rc.renderController();
    assert.match(render.node('controller-runtimes').innerHTML,/role="status">Restarting/);
    assert.match(render.node('controller-runtimes').innerHTML,/aria-busy="true"/);
  }
  vm.runInContext('runtimeBatchRestarting.clear()',rc);rc.controllerRecords[0].state='Running';rc.renderController();
  assert(!render.node('controller-runtimes').innerHTML.includes('Restarting'));
  for(const fail of [false,true]) {
    const r=harness([record('one','Running'),record('two','Running')]);const ctx=r.context;
    const state={one:'Running',two:'Running'};
    ctx.refreshBatchRecord=async id=>record(id,state[id]);
    ctx.executeBatchOperation=async(rec,op)=>{
      assert(vm.runInContext(`runtimeBatchRestarting.has('${rec.id}')`,ctx));
      const other=rec.id==='one'?'two':'one';
      assert(!vm.runInContext(`runtimeBatchRestarting.has('${other}')`,ctx));
      if(fail&&rec.id==='one')throw Error('stop failed');
      state[rec.id]=op==='stop'?'Stopped':'Running';
    };
    await ctx.runRuntimeBatchPlan({op:'restart',targets:ctx.controllerRecords,skipped:0});
    assert.equal(vm.runInContext('runtimeBatchRestarting.size',ctx),0);
    assert.equal(state.one, 'Running');assert.equal(state.two,'Running');
    if(fail)assert.match(r.node('runtime-batch-result').innerHTML,/stop failed/);
  }
  // Independent lifecycle requests overlap; each row stays locked until its own
  // request settles, including errors, and duplicate requests never execute.
  for (const op of ['start', 'stop', 'restart']) {
    const state = op === 'start' ? 'Stopped' : 'Running';
    rc.controllerRecords = [record('one', state), record('two', state)];
    rc.controllerMutation = false; rc.runtimeBatchBusy = false;
    const releases = new Map();
    const calls = [];
    rc.refreshBatchRecord = async id => rc.controllerRecords.find(r => r.id === id);
    rc.executeBatchOperation = async (rec, action) => {
      calls.push(`${rec.id}:${action}`);
      await new Promise((resolve, reject) => releases.set(rec.id, {resolve, reject}));
      rec.state = action === 'stop' ? 'Stopped' : 'Running';
      return rec;
    };
    const plan = id => ({op, targets:[{id, name:id}], skipped:0});
    const first = rc.runRuntimeBatchPlan(plan('one'));
    for (let i=0; i<8; i++) await Promise.resolve();
    let html = render.node('controller-runtimes').innerHTML;
    assert.match(html, /data-controller-op="(?:start|stop)" data-controller-id="one"[^>]*disabled/);
    assert.match(html, /data-controller-op="(?:start|stop)" data-controller-id="two"[^>]*><svg/);
    assert.match(html, /data-controller-op="restart" data-controller-id="two"[^>]*><svg/);
    assert(!rc.runtimeOperationBusy('default'));
    await rc.runRuntimeBatchPlan(plan('one'));
    assert.equal(calls.length, 1);
    const second = rc.runRuntimeBatchPlan(plan('two'));
    for (let i=0; i<8; i++) await Promise.resolve();
    assert.equal(calls.length, 2);
    releases.get('one').reject(Error('lifecycle failed'));
    await first;
    assert(!rc.runtimeOperationBusy('one'));
    assert(rc.runtimeOperationBusy('two'));
    releases.get('two').resolve();
    if (op === 'restart') {
      for (let i=0; i<8; i++) await Promise.resolve();
      assert.equal(calls.at(-1), 'two:start');
      releases.get('two').resolve();
    }
    await second;
    assert(!rc.runtimeOperationBusy('two'));
    assert(!rc.controllerMutation && !rc.runtimeBatchBusy);
  }
  rc.runtimeTransition = true;
  assert(rc.runtimeOperationBusy('default'));
  assert(!rc.runtimeOperationBusy('one'));
  rc.runtimeTransition = false;
  // Full Waydroid windows stay scoped to each running Desktop runtime.
  assert(Batch.eligible(record('r','Running',{display:{mode:'desktop'}}),'open'));
  assert(!Batch.eligible(record('r','Running',{display:{mode:'headless'}}),'open'));
  assert(!Batch.eligible(record('r','Stopped',{display:{mode:'desktop'}}),'open'));
  assert(Batch.eligible(record('default','Running'),'open'));
  const windows=harness([record('one','Running',{display:{mode:'desktop'}}),record('two','Running',{display:{mode:'desktop'}})]);
  const wc=windows.context;const launches=[];
  wc.setTimeout=resolve=>resolve();
  wc.refreshBatchRecord=async id=>wc.controllerRecords.find(r=>r.id===id);
  wc.controllerRequest=async r=>{
    launches.push({...r});
    return {id:'job-'+r.id,runtime_id:r.id,action:'full_ui',package:'org.anvildroid.desktop',status:r.op==='app_action'?'Running':'Succeeded'};
  };
  let tokensRequested = 0;
  wc.tauriInvoke = async command => {assert.equal(command, 'request_waydroid_activation'); return 'token-' + ++tokensRequested;};
  const originalRequest = wc.controllerRequest;
  wc.controllerRequest = async request => {
    assert.equal(tokensRequested, 2); // Both source tokens captured before opening a window.
    if(request.op === 'app_action') assert.equal(request.activation_token, request.id === 'one' ? 'token-1' : 'token-2');
    return originalRequest(request);
  };
  wc.runtimeBatchSelection.add('one');wc.runtimeBatchSelection.add('two');
  await wc.runRuntimeBatchPlan({op:'open',targets:wc.controllerRecords.map(r=>({id:r.id,name:r.name})),skipped:0});
  assert.deepEqual(launches.map(r=>[r.op,r.id]),[['app_action','one'],['app_action','two'],['app_job','one'],['app_job','two']]);
  assert.equal(wc.runtimeBatchSelection.size,2);
  assert.equal(windows.node('runtime-batch-result').innerHTML,'');
  assert.match(windows.node('app-toast-message').textContent,/Waydroid: 2 request sent/);
  assert.equal(windows.node('app-toast').className,'info');
  // Open reports request acceptance separately from verified readiness.
  const successRow = (id, state, activation) => ({id, name:id, status:'succeeded', result:{window:{state, activation}}});
  const renderOpen = (rows, skipped=0) => wc.renderRuntimeBatchResult({op:'open',skipped}, rows);
  const singleRequest = renderOpen([successRow('test','unverified')]);
  assert.match(singleRequest, /test: Open request sent/);
  assert(!singleRequest.includes('completed') && !singleRequest.includes('unverified') && !singleRequest.includes('<strong>'));
  wc.escapeHtml = value => String(value).replaceAll('<','&lt;').replaceAll('>','&gt;');
  const mixedOpen = renderOpen([successRow('a','ready','active'),successRow('b','pending'),successRow('c','unverified'),{id:'d',name:'<unsafe>',status:'failed',detail:'<failure>'}],1);
  assert.match(mixedOpen,/Waydroid: 1 ready · 2 request sent · 1 failed · 1 skipped/);
  assert.match(mixedOpen,/a: Waydroid ready and focused/);
  assert.match(mixedOpen,/b: Opening Waydroid/);
  assert(!mixedOpen.includes('<unsafe>') && !mixedOpen.includes('<failure>'));
  assert.match(renderOpen([successRow('a','ready'),successRow('b','ready')]), /Waydroid: 2 ready/);
  // Old worker results remain compatible and retain a diagnostic reason.
  wc.controllerRequest=async r=>({id:'old-job',runtime_id:r.id,action:'full_ui',package:'org.anvildroid.desktop',status:'Succeeded',result:{action:'full_ui',message:'requested'}});
  const oldResult=await wc.requestRuntimeWaydroidWindow(record('one','Running',{display:{mode:'desktop'}}));
  assert.equal(oldResult.window.state,'unverified');assert.equal(oldResult.window.reason,'worker_upgrade_required');assert.equal(oldResult.message,'requested');
  wc.controllerRequest=async r=>({id:'job',runtime_id:'wrong',action:'full_ui',package:'org.anvildroid.desktop',status:'Succeeded'});
  await assert.rejects(wc.launchRuntimeWaydroidWindow(record('one','Running',{display:{mode:'desktop'}})),/Invalid Waydroid/);
  wc.controllerRequest=async r=>({id:r.op==='app_action'?'original':'replacement',runtime_id:r.id,action:'full_ui',package:'org.anvildroid.desktop',status:'Running'});
  await assert.rejects(wc.launchRuntimeWaydroidWindow(record('one','Running',{display:{mode:'desktop'}})),/operation changed/);
  wc.controllerRequest=async r=>({id:'job',runtime_id:r.id,action:'full_ui',package:'org.anvildroid.desktop',status:'Failed',error:'window failed'});
  await assert.rejects(wc.launchRuntimeWaydroidWindow(record('one','Running',{display:{mode:'desktop'}})),/window failed/);
  let clock=0;wc.Date={now:()=>clock+=60000};
  wc.controllerRequest=async r=>({id:'job',runtime_id:r.id,action:'full_ui',package:'org.anvildroid.desktop',status:'Running'});
  await assert.rejects(wc.launchRuntimeWaydroidWindow(record('one','Running',{display:{mode:'desktop'}})),/Timed out/);
  for(const value of [null, '', 'a b', 'a'.repeat(513), 'é']) {
    wc.tauriInvoke = async () => value;
    assert.equal(await wc.waydroidActivationToken(), null);
  }
  wc.tauriInvoke = async () => {throw Error('unsupported');};
  assert.equal(await wc.waydroidActivationToken(), null);
  const defaultWindow=harness();let nativeWindow;
  defaultWindow.context.tauriInvoke=async cmd=>{nativeWindow=cmd;return {success:true};};
  await defaultWindow.context.launchRuntimeWaydroidWindow(record('default','Running'));assert.equal(nativeWindow,'open_waydroid');
  defaultWindow.context.tauriInvoke=async()=>({success:false,message:'window unavailable'});
  await assert.rejects(defaultWindow.context.launchRuntimeWaydroidWindow(record('default','Running')),/window unavailable/);
  assert(source.includes("rowButton('open_waydroid', 'Open Waydroid'"));
  // Window queue overlaps only two independent launches and retains input order.
  let running=0,peak=0;const releases=[];
  const queued=Batch.run([record('a','Running',{display:{mode:'desktop'}}),record('b','Running',{display:{mode:'desktop'}}),record('c','Running',{display:{mode:'desktop'}})],'open',{
    refresh:async id=>record(id,'Running',{display:{mode:'desktop'}}),
    execute:async r=>{running++;peak=Math.max(peak,running);await new Promise(resolve=>releases.push(resolve));running--;return {window:{state:'ready'}};},requirements:ready,
  });
  for(let i=0;i<5;i++)await Promise.resolve();assert.equal(running,2);assert.equal(releases.length,2);
  releases[0]();for(let i=0;i<5;i++)await Promise.resolve();assert.equal(releases.length,3);
  releases[1]();releases[2]();const queueResult=await queued;
  assert.equal(peak,2);assert.deepEqual(queueResult.map(r=>r.id),['a','b','c']);assert(queueResult.every(r=>r.result.window.state==='ready'));
  console.log('PASS: batch eligibility, serial restart, state/name/identity races, selection, confirmation, lifecycle jobs, deletion races, default runtime and partial failures');
})().catch(error=>{console.error(error);process.exitCode=1;});
