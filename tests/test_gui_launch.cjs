const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

function gui(invoke, confirm = async () => true, search = "", setup = {can_prepare: true, can_start: true, checks: []}, storageViaInvoke = false) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      id, value:'', textContent:'', innerHTML:'', disabled:false, checked:false, hidden:false, style: {}, dataset: {}, attributes: {}, listeners: {}, classList: { values: new Set(), add(value) { this.values.add(value); },
        remove(value) { this.values.delete(value); }, toggle(value, enabled) {
          if (enabled === undefined) enabled = !this.values.has(value);
          if (enabled) this.values.add(value); else this.values.delete(value);
        }, contains(value) { return this.values.has(value); } },
      addEventListener(name, handler) { this.listeners[name] = handler; }, querySelectorAll: () => [], querySelector: () => null,
      setAttribute(name, value) { this.attributes[name] = value; },
      removeAttribute(name) { delete this.attributes[name]; },
      showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
    });
    return elements.get(id);
  };
  const views = ['grid', 'list'].map(view => { const button = element('view-' + view); button.dataset.view = view; return button; });
  const nav = ['apps', 'runtime', 'settings', 'controls'].map(page => { const item = element('nav-' + page); item.dataset.page = page; return item; });
  const pageNodes = ['apps','runtime','runtime-settings','images','settings','controls'].map(page => element('page-'+page));
  const storage = new Map();
  const context = vm.createContext({
    AppDialog:{confirm, prompt:async()=>null},
    AnvilLazyFeatures:{create:()=>({ensure:async()=>{}})},
    AnvilPageLoads:{create:()=>require('../crates/anvildroid-gui/src-frontend/page-loads.js').create({frame:callback=>callback()})},
    RuntimeUsage:require('../crates/anvildroid-gui/src-frontend/runtime-usage.js'),
    RuntimeWindow:require('../crates/anvildroid-gui/src-frontend/runtime-window.js'),
    RuntimeBatch:require('../crates/anvildroid-gui/src-frontend/runtime-batch.js'),
    RuntimeSettings:require('../crates/anvildroid-gui/src-frontend/runtime-settings.js'),
    window: { AnvilKeymapLibrary:{refresh:async()=>{}}, location: { search }, __TAURI__: { core: { invoke: (command, args) => command === 'runtime_controller' && args?.request.op === 'requirements' ? Promise.resolve(setup) : command === 'runtime_controller' && args?.request.op === 'storage_info' && !storageViaInvoke ? Promise.resolve({path:'/var/lib/anvildroid-controller/instances/' + args.request.id, job:null,previous_path:null,error:null}) : invoke(command, args) }, dialog: { confirm } }, addEventListener() {} },
    document: { getElementById: element, querySelectorAll: selector => selector === '.view-button[data-view]' ? views : selector === '.nav-item:not(.disabled)' ? nav : selector === '.page' ? pageNodes : [], addEventListener() {} },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    setTimeout: () => 0, clearTimeout() {}, console,
  });
  vm.runInContext(fs.readFileSync(process.env.ANVIL_TEST_MAIN || 'crates/anvildroid-gui/src-frontend/main.js', 'utf8'), context);
  return { context, banner: element('app-toast-message'), element, storage };
}

test('Existing management refresh uses default without adding it to managed inventory', async () => {
  const calls=[];
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller')return;
    const request=args.request; calls.push(request);
    if(request.op==='refresh')return {id:'default',name:'Existing <Android>',state:'Stopped',managed:true,display:{mode:'desktop'}};
    if(request.op==='resources_info')return {configured:{memory_mib:2048,cpu_count:2},host:{supported:true,memory_total_mib:16000,cpu_count:8},scope:'Android container'};
    if(request.op==='gpu_info')return {selection:'auto',devices:[]};
    if(request.op==='arm_info')return {enabled:false,supported:false,detail:'Keep installed provider'};
    return [];
  },undefined,'?view=controls');
  await vm.runInContext('refreshExistingRuntime();',context);
  vm.runInContext('renderController()',context);
  const html=element('existing-runtime-management').innerHTML;
  assert.match(html,/RAM and CPU limits/);
  assert.match(html,/data-controller-id="default"/);
  assert.match(html,/Keep installed provider/);
  assert.equal(element('existing-runtime-label').textContent,'Existing <Android>');
  assert.equal(vm.runInContext('controllerRecords.length',context),0);
  assert.ok(calls.every(request=>request.id==='default'));
  assert.ok(calls.every(request=>!['start','prepare','existing_claim'].includes(request.op)));
});

test('Existing Play Store action uses legacy launcher with explicit default target',async()=>{
  const calls=[];
  const {context}=gui(async(command,args)=>{calls.push({command,args});return {success:true,message:'Opened'};},undefined,'?view=controls');
  calls.length=0;
  const result=await vm.runInContext("controllerRequest({op:'app_action',id:'default',action:'launch',package:'com.android.vending'})",context);
  assert.equal(result.status,'Succeeded');
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{command:'launch_app',args:{runtimeId:'default',package:'com.android.vending'}}]);
});

test('Existing management retains saved context and disables claim after refresh failure',async()=>{
  let failed=false;
  const {context,element}=gui(async(command,args)=>{
    if(failed)throw new Error('another desktop user');
    if(args?.request.op==='refresh')return {id:'default',name:'Existing',state:'Running',managed:false,display:{mode:'desktop'}};
  },undefined,'?view=controls');
  await vm.runInContext('refreshExistingRuntime()',context);
  vm.runInContext('renderController()',context);
  assert.match(element('existing-runtime-management').innerHTML,/Enable management/);
  failed=true;
  await vm.runInContext('refreshExistingRuntime()',context);
  vm.runInContext('renderController()',context);
  assert.match(element('existing-runtime-management').innerHTML,/another desktop user/);
  assert.match(element('existing-runtime-management').innerHTML,/data-controller-op="existing_claim"[^>]*disabled/);
  failed=false;
  await vm.runInContext('refreshExistingRuntime()',context);
  vm.runInContext('renderController()',context);
  assert.match(element('existing-runtime-management').innerHTML,/Enable management/);
});

test('Existing import confirms copying then selects new runtime without starting either',async()=>{
  const calls=[],prompts=[];
  const id='r-'+'b'.repeat(32);
  const {context}=gui(async(command,args)=>{
    if(command!=='runtime_controller')return;
    calls.push(args.request);
    if(args.request.op==='existing_import')return {id,name:'Imported',state:'Provisioning',job:{operation:'prepare',status:'Running'}};
    if(args.request.op==='list')return [{id,name:'Imported',state:'Provisioning'}];
    if(args.request.op==='refresh')return args.request.id===id ? {id,name:'Imported',state:'Provisioning',import_status:'pending',job:{operation:'prepare',status:'Running'}} : {id:'default',name:'Existing',state:'Stopped',managed:true,display:{mode:'desktop'}};
  },async prompt=>{prompts.push(prompt);return true;},'?view=controls');
  await vm.runInContext("mutateController({op:'existing_import',id:'default',name:'Imported'})",context);
  assert.match(prompts[0],/original is retained/);
  assert.equal(calls.filter(request=>request.op==='existing_import').length,1);
  assert.equal(vm.runInContext('selectedRuntimeId',context),id);
  assert.equal(vm.runInContext('controllerRecords.some(record=>record.id === "default")',context),false);
  assert.equal(calls.some(request=>['start','stop','delete'].includes(request.op)),false);
});

test('per-app labels never infer process state from Running runtime', () => {
  const {context}=gui(async()=>undefined,undefined,'?view=settings');
  const label=app=>vm.runInContext(`appExecutionLabel(${JSON.stringify(app)})`,context);
  const base={runtime_state:'Running',available:true,status_checked_at:Date.now()/1000};
  assert.equal(label(base),'Status unknown');
  assert.equal(label({...base,execution_state:'foreground'}),'Foreground');
  assert.equal(label({...base,execution_state:'background'}),'Background process');
  assert.equal(label({...base,execution_state:'not_running'}),'Not running');
  assert.equal(label({...base,execution_state:'foreground',status_checked_at:1}),'Status unknown');
  assert.equal(label({...base,execution_state:'foreground',available:false}),'Status unknown');
  assert.equal(label({...base,execution_state:'foreground',runtime_state:'Stopped'}),'Not running');
  assert.equal(label({...base,desktop_entry:true}),'Runtime launcher');
});

test('app status poll is runtime scoped, does not fetch icons and clears failed samples', async () => {
  const calls=[];let fail=false;
  const {context}=gui(async(command,args)=>{
    calls.push(args?.request);
    if(fail)throw new Error('offline');
    if(args?.request.op==='app_states')return {runtime_id:args.request.id,state:'Running',
      states:{'com.game.open':'foreground','com.game.cached':'background'},checked_at:Date.now()/1000};
  },undefined,'?view=settings');
  vm.runInContext(`libraryPage=true; managedLibrary=[{id:'r-one',state:'Running'}];
    managedSnapshots.set('r-one',[{package:'com.game.open'},{package:'com.game.cached'},{package:'com.game.closed'}]);`,context);
  await vm.runInContext('refreshAppStates()',context);
  assert.equal(vm.runInContext("managedSnapshots.get('r-one')[2].execution_state",context),'not_running');
  assert.ok(calls.some(r=>r?.op==='app_states'&&r.id==='r-one'));
  assert.ok(!calls.some(r=>['apps','start','app_action'].includes(r?.op)));
  fail=true;
  await vm.runInContext('refreshAppStates()',context);
  assert.equal(vm.runInContext("managedSnapshots.get('r-one')[0].execution_state",context),'unknown');
  const count=calls.length;
  vm.runInContext('document.hidden=true',context);
  await vm.runInContext('refreshAppStates()',context);
  assert.equal(calls.length,count);
});

test('Controls is enabled in sidebar and reachable through navigation and direct launch', () => {
  const html=fs.readFileSync('crates/anvildroid-gui/src-frontend/index.html','utf8');
  const nav=html.match(/<li\b[^>]*data-page="controls"[^>]*>/)?.[0];
  assert.ok(nav); assert.doesNotMatch(nav,/\bdisabled\b/);
  const direct=gui(async()=>undefined,undefined,'?view=controls');
  assert.equal(direct.element('page-controls').classList.contains('active'),true);
  const fromSettings=gui(async()=>undefined,undefined,'?view=settings');
  vm.runInContext("navigatePage('controls')",fromSettings.context);
  assert.equal(fromSettings.element('page-controls').classList.contains('active'),true);
});
test('Edit keymap routes only to the selected managed app and waits for matching job',async()=>{
  const id='r-'+'a'.repeat(32),calls=[];
  const {context,banner}=gui(async(command,args)=>{
    calls.push({command,args});
    if(command==='runtime_controller'&&args.request.op==='app_action')return{id:'edit-one',runtime_id:id,package:'com.game.one',action:'keymap_edit',status:'Succeeded',result:{message:'Overlay attached'}};
  },undefined,'?view=controls');
  await vm.runInContext(`appMenuAction('keymap','com.game.one','Game',null,'${id}')`,context);
  const request=calls.find(c=>c.args?.request?.action==='keymap_edit');
  assert.equal(request.args.request.id,id);assert.equal(request.args.request.package,'com.game.one');
  assert.match(banner.textContent,/Overlay attached/);
  assert.ok(!calls.some(c=>c.args?.request?.op==='start'||c.args?.request?.action==='launch'));
});
test('Edit keymap routes Default directly and confirms the overlay reply',async()=>{
  const calls=[];const {context,banner}=gui(async(command,args)=>{
    calls.push({command,args});
    if(command==='runtime_controller'&&args.request.op==='app_action')return{action:'keymap_edit',package:'com.game.one',success:true,message:'Overlay attached'};
  },undefined,'?view=controls');
  await vm.runInContext("appMenuAction('keymap','com.game.one','Game',null,'default')",context);
  const request=calls.find(c=>c.args?.request?.op==='app_action');
  assert.equal(request.args.request.id,'default');
  assert.match(banner.textContent,/Overlay attached/);
  assert.ok(!calls.some(c=>c.args?.request?.op==='app_job'||c.args?.request?.op==='start'));
});

test('Settings distinguishes configured values from unmeasured limits and escapes metadata', async () => {
  const { context, element } = gui(async name => {
    if (name === 'get_runtime_catalog') return {
      registry: { default_runtime_id: 'default', runtimes: [{ id: 'default', name: '<runtime>', data_dir: '/data', last_applied: null }] },
      configured: { settings: { image_kind: 'Vanilla', renderer: 'Gles', gpu_device: null }, images_dir: '/images' },
      concurrent_supported: false, management_note: 'Import complete',
    };
  });
  await vm.runInContext('refreshSettings()', context);
  const html = element('settings-runtime').innerHTML;
  assert.match(html, /&lt;runtime&gt;/);
  assert.match(html, /Renderer \(configured\)/);
  assert.match(html, /inspect active limits under Runtime &gt; Default runtime/);
  assert.match(html, /Not available yet/);
  assert.equal(element('btn-refresh-settings').disabled, false);
});

test('Settings reports registry errors and enables retry', async () => {
  const { context, element } = gui(async name => {
    if (name === 'get_runtime_catalog') throw new Error('Unsupported registry schema');
  });
  await vm.runInContext('refreshSettings()', context);
  assert.match(element('settings-runtime').textContent, /Unsupported registry schema/);
  assert.equal(element('btn-refresh-settings').disabled, false);
});

const tick = () => new Promise(resolve => setImmediate(resolve));

test('forced Stop stays stopped but warns instead of claiming all data was saved', () => {
  const {context,element}=gui(async()=>undefined,async()=>true,'?view=settings');
  vm.runInContext("runtimeSettingsId='r-one'",context);
  vm.runInContext("controllerRecords=[{id:'r-one',name:'One',state:'Stopped',shutdown_warning:'Forced cleanup: <unsaved>',job:{operation:'stop',status:'Succeeded',error:null}}];renderController()",context);
  const html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/runtime-status warning/);
  assert.match(html,/Forced cleanup: &lt;unsaved&gt;/);
  assert.doesNotMatch(html,/data saved/);
  assert.match(html,/data-controller-op="start"[^>]*aria-label="Start · One"/);
});

test('official images check is explicit, deduplicates clicks and reports progress', async () => {
  const calls=[], pending=deferred();
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller') {
      calls.push(args.request);
      if(args.request.op==='image_catalog') return pending.promise;
      if(args.request.op==='list') return [];
    }
  },async()=>true,'?view=settings');
  await tick();
  assert.equal(calls.filter(request=>request.op==='image_catalog').length,0);
  const first=vm.runInContext('checkOfficialImages()',context);
  await vm.runInContext('checkOfficialImages()',context);
  assert.equal(element('btn-check-images').disabled,true);
  assert.match(element('btn-check-images').textContent,/Checking/);
  pending.resolve({status:'Checking',report:null,error:null});await first;
  assert.equal(calls.filter(request=>request.op==='image_catalog').length,1);
  assert.equal(calls.find(request=>request.op==='image_catalog').refresh,true);
  assert.equal(calls.some(request=>request.op==='start'||request.op==='prepare'),false);
});

test('failed image catalog preserves prior source metadata without enabling a mismatched pair', () => {
  const {context,element}=gui(async()=>undefined,async()=>true,'?view=settings');
  vm.runInContext(`renderImageCatalog({status:'Failed',error:'<offline>',report:{checked_at:1,architecture:'x86_64',variants:[{flavor:'GAPPS',download_bytes:1073741824,matching_versions:false,system:{version:'20.0',filename:'<system.zip>',sha256:'expected'},vendor:{filename:'vendor.zip',sha256:'vendor'}}]}})`,context);
  const summary=element('settings-images').innerHTML;
  const images=element('available-runtime-images').innerHTML;
  assert.match(summary,/Previous result \(not current\)/);
  assert.match(summary,/&lt;offline&gt;/);
  assert.match(images,/Waydroid GAPPS/);
  assert.match(images,/Not downloaded/);
  assert.match(images,/1.00 GiB/);
  assert.match(images,/data-download-flavor="GAPPS"[^>]*disabled/);
  assert.doesNotMatch(images,/data-image-use=/);
  assert.equal(element('btn-check-images').disabled,false);
});

test('image catalog transport failure permits retry', async () => {
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller'&&args.request.op==='image_catalog') throw new Error('Service unavailable');
  },async()=>true,'?view=settings');
  await vm.runInContext('checkOfficialImages()',context);
  assert.match(element('settings-images').textContent,/Service unavailable/);
  assert.equal(element('btn-check-images').disabled,false);
});

test('image download pins displayed checksums, suppresses repeats and targets cancellation by job', async () => {
  const calls=[], pending=deferred();
  const running={id:'job-1',flavor:'GAPPS',status:'Downloading',can_cancel:true,cache_path:'/cache',free_bytes:999,received_bytes:10,download_bytes:100,phase:'Downloading system'};
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller') {
      calls.push(args.request);
      if(args.request.op==='image_download') return pending.promise;
      if(args.request.op==='image_download_cancel') return {...running,status:'Cancelling',can_cancel:false};
    }
  },async()=>true,'?view=settings');
  await tick();calls.length=0;
  vm.runInContext("imageCatalogState={status:'Ready',report:{checked_at:1,architecture:'x86_64',variants:[{flavor:'GAPPS',matching_versions:true,download_bytes:100,system:{version:'20.0',sha256:'system-hash',filename:'system.zip'},vendor:{sha256:'vendor-hash',filename:'vendor.zip'}}]}}",context);
  const first=vm.runInContext("startImageDownload('GAPPS')",context);
  await vm.runInContext("startImageDownload('GAPPS')",context);
  assert.equal(calls.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{op:'image_download',flavor:'GAPPS',system_sha256:'system-hash',vendor_sha256:'vendor-hash'});
  pending.resolve(running);await first;
  assert.match(element('settings-image-download').innerHTML,/Downloading system/);
  await vm.runInContext('cancelImageDownload()',context);
  assert.equal(calls[1].op,'image_download_cancel');assert.equal(calls[1].job_id,'job-1');
  assert.equal(calls.some(x=>['start','prepare','create'].includes(x.op)),false);
});

test('download failure restores action buttons and explains cache space', async () => {
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller'&&args.request.op==='image_download') throw new Error('Insufficient cache space');
  },async()=>true,'?view=settings');
  await tick();
  vm.runInContext("imageCatalogState={status:'Ready',report:{checked_at:1,architecture:'x86_64',variants:[{flavor:'GAPPS',matching_versions:true,download_bytes:100,system:{version:'20.0',sha256:'a',filename:'system.zip'},vendor:{sha256:'b',filename:'vendor.zip'}}]}}",context);
  await vm.runInContext("startImageDownload('GAPPS')",context);
  assert.match(element('settings-image-download').textContent,/Insufficient cache space/);
  assert.doesNotMatch(element('settings-images').innerHTML,/data-download-flavor="GAPPS" disabled/);
});

test('late download status cannot overwrite a newly started job', async () => {
  const old=deferred(); let reads=0;
  const {context}=gui(async(command,args)=>{
    if(command==='runtime_controller'&&args.request.op==='image_download_status') {
      reads++; if(reads>1) return old.promise;
      return {status:'Idle',cache_path:'/cache',free_bytes:100};
    }
    if(command==='runtime_controller'&&args.request.op==='image_download') return {id:'new',flavor:'GAPPS',status:'Downloading',received_bytes:0,download_bytes:100,cache_path:'/cache',free_bytes:100};
  },async()=>true,'?view=settings');
  await tick();
  const refresh=vm.runInContext('refreshImageDownload()',context);
  vm.runInContext("imageCatalogState={status:'Ready',report:{checked_at:1,architecture:'x86_64',variants:[{flavor:'GAPPS',matching_versions:true,download_bytes:100,system:{version:'20.0',sha256:'a',filename:'system.zip'},vendor:{sha256:'b',filename:'vendor.zip'}}]}}",context);
  await vm.runInContext("startImageDownload('GAPPS')",context);
  old.resolve({status:'Idle',cache_path:'/cache',free_bytes:100});await refresh;
  assert.equal(vm.runInContext('imageDownloadState.id',context),'new');
});

test('frontend runtime calls carry an explicit target and catalog/version calls stay global', async () => {
  const calls = [];
  const { context } = gui(async (name, args) => { calls.push([name, args]); });
  await vm.runInContext("invoke('stop_runtime'); invoke('set_favorite', { package: 'com.example.game', enabled: true }); invoke('get_runtime_catalog')", context);
  for (const [name, args] of calls) {
    if (['get_runtime_catalog', 'get_app_version', 'runtime_controller'].includes(name)) assert.equal(args?.runtimeId, undefined);
    else assert.equal(args.runtimeId, 'default', name);
  }
  assert.equal(calls.find(([name]) => name === 'set_favorite')[1].package, 'com.example.game');
});

test('opening library does not start Android; explicit launch is sent once and waits for backend boot', async () => {
  const launched=deferred(),calls=[];
  const {context,element}=gui(async name=>{
    calls.push(name);
    if(name==='get_apps') throw new Error('Stopped');
    if(name==='runtime_controller') return [];
    if(name==='get_health_report') return {status:{state:'Stopped'}};
    if(name==='launch_app') return launched.promise;
  });
  await tick();
  assert.equal(calls.filter(x=>x==='start_runtime').length,0);
  const first=vm.runInContext("launchApp('com.android.settings')",context);
  await tick();
  await vm.runInContext("launchApp('com.android.settings')",context);
  assert.equal(calls.filter(x=>x==='launch_app').length,1);
  assert.match(element('app-toast-message').textContent,/waiting for Android/);
  launched.resolve({success:true,message:'Launch request sent'});await first;
  assert.equal(element('app-toast-message').textContent,'Launch requested: com.android.settings');
});

test('backend launch boot failure clears pending state and allows explicit retry', async () => {
  let launches=0;
  const {context,element}=gui(async name=>{
    if(name==='get_apps') return [];
    if(name==='runtime_controller') return [];
    if(name==='get_health_report') return {status:{state:'Stopped'}};
    if(name==='launch_app') { launches++; return {success:false,message:launches===1?'Boot timed out':'Failed with code: -1'}; }
  });
  await tick();
  await vm.runInContext("launchApp('com.android.settings')",context);
  assert.match(element('app-toast-message').textContent,/Boot timed out/);
  await vm.runInContext("launchApp('com.android.settings')",context);
  assert.equal(launches,2);assert.match(element('app-toast-message').textContent,/Failed with code: -1/);
});

test('a click during manual startup stays pending until launch finishes', async () => {
  const boot = deferred();
  const launched = deferred();
  let launches = 0;
  const { context, element } = gui(async name => {
    if (name === 'get_health_report') return { status: { state: 'Running' } };
    if (name === 'get_apps') return [];
    if (name === 'start_runtime') return boot.promise;
    if (name === 'launch_app') { launches++; return launched.promise; }
  });
  await tick();
  const starting = vm.runInContext("runtimeAction('start_runtime', btnStart)", context);
  const opening = vm.runInContext("launchApp('com.android.settings')", context);
  await tick();
  assert.equal(launches, 0);
  boot.resolve({ success: true, message: 'Android is ready.' });
  await starting;
  await tick();
  assert.equal(launches, 1);
  assert.match(element('app-toast-message').textContent, /waiting for Android/);
  launched.resolve({ success: true, message: 'Launch request sent' });
  await opening;
  assert.equal(element('app-toast-message').textContent, 'Launch requested: com.android.settings');
});

test('menu shortcut waits for boot and suppresses duplicate actions', async () => {
  const boot = deferred();
  const calls = [];
  const { context, banner, element } = gui(async name => {
    calls.push(name);
    if (name === 'get_health_report') return { status: { state: 'Starting' } };
    if (name === 'start_runtime') return boot.promise;
    if (name === 'get_apps') return [];
    return { success: true, message: 'Shortcut created' };
  });
  await tick();
  vm.runInContext("trackRuntimeTransition(() => invoke('start_runtime'))", context);
  const shortcut = vm.runInContext("appMenuAction('shortcut', 'com.example.game', 'Game')", context);
  await tick();
  await vm.runInContext("appMenuAction('shortcut', 'com.example.game', 'Game')", context);
  assert.equal(calls.filter(x => x === 'create_app_shortcut').length, 0);
  boot.resolve({ success: true });
  await shortcut;
  assert.equal(calls.filter(x => x === 'create_app_shortcut').length, 1);
  assert.equal(banner.textContent, 'Shortcut created');
});

test('cancelled and protected uninstall never call the backend', async () => {
  const calls = [];
  let confirmations = 0;
  const { context } = gui(async name => {
    calls.push(name);
    if (name === 'get_health_report') return { status: { state: 'Running' } };
    if (name === 'get_apps') return [];
  }, async () => { confirmations++; return false; });
  await tick();
  await vm.runInContext("appMenuAction('uninstall', 'com.example.game', 'Game')", context);
  await vm.runInContext("appMenuAction('uninstall', 'org.anvildroid.ime', 'IME', 'Protected')", context);
  assert.equal(confirmations, 1);
  assert.equal(calls.filter(x => x === 'uninstall_app').length, 0);
});

test('confirmed uninstall refreshes apps and App Info invokes the real backend', async () => {
  const calls = [];
  let prompt;
  const { context, banner, element } = gui(async (name, args) => {
    calls.push([name, args?.package]);
    if (name === 'get_health_report') return { status: { state: 'Running' } };
    if (name === 'get_apps') return [];
    return { success: true, message: 'Done' };
  }, async message => { prompt = message; return true; });
  await tick();
  calls.length = 0;
  await vm.runInContext("appMenuAction('uninstall', 'com.example.game', 'Game')", context);
  assert.match(prompt, /com.example.game/);
  assert.match(prompt, /permanently/);
  assert.deepEqual(calls.filter(([name]) => name !== 'runtime_controller'), [['uninstall_app', 'com.example.game'], ['get_health_report', undefined], ['get_apps', undefined]]);
  await vm.runInContext("appMenuAction('info', 'com.example.game', 'Game')", context);
  assert.deepEqual(calls.at(-1), ['open_app_info', 'com.example.game']);
  assert.equal(banner.textContent, 'Done');
});

test('confirmed uninstall remains busy until Android returns its verified result', async () => {
  const verification = deferred();
  const calls = [];
  const { context, banner } = gui(async name => {
    calls.push(name);
    if (name === 'get_health_report') return { status: { state: 'Running' } };
    if (name === 'get_apps') return [];
    if (name === 'uninstall_app') return verification.promise;
  });
  await tick();
  const removal = vm.runInContext("appMenuAction('uninstall', 'com.example.game', 'Game')", context);
  await tick();
  assert.equal(banner.textContent, 'Uninstalling Game…');
  assert.equal(vm.runInContext('pendingAppAction', context), true);
  await vm.runInContext("appMenuAction('uninstall', 'com.example.game', 'Game')", context);
  assert.equal(calls.filter(name => name === 'uninstall_app').length, 1);
  verification.resolve({ success: true, message: 'Uninstalled com.example.game' });
  await removal;
  assert.equal(banner.textContent, 'Uninstalled com.example.game');
  assert.equal(vm.runInContext('pendingAppAction', context), false);
  assert.equal(calls.includes('check_uninstall'), false);
});

test('Android uninstall rejection is reported and unlocks the app library', async () => {
  const calls = [];
  const { context, banner } = gui(async name => {
    calls.push(name);
    if (name === 'get_health_report') return { status: { state: 'Running' } };
    if (name === 'get_apps') return [{package:'com.example.game'}];
    if (name === 'uninstall_app') return { success: false, message: 'Cannot uninstall a system or protected app' };
  });
  await tick();
  await vm.runInContext("appMenuAction('uninstall', 'com.example.game', 'Game')", context);
  assert.equal(banner.textContent, 'Failed: Cannot uninstall a system or protected app');
  assert.equal(vm.runInContext('pendingAppAction', context), false);
  assert.equal(vm.runInContext('allApps.some(app => app.package === "com.example.game")', context), true);
  assert.equal(calls.includes('check_uninstall'), false);
});

test('favorites persist through backend and recent sorting uses saved history', async () => {
  let saved={schema_version:1,favorites:[],recent:{'com.example.b':42},sort:'recent'};
  const {context}=gui(async (name,args) => {
    if(name==='get_library') return saved;
    if(name==='get_health_report') return {status:{state:'Running'}};
    if(name==='get_apps') return [{package:'com.example.a'},{package:'com.example.b'}];
    if(name==='set_favorite') { saved={...saved,favorites:args.enabled?[args.package]:[]}; return saved; }
  });
  await tick();
  await vm.runInContext("toggleFavorite('com.example.a')",context);
  assert.deepEqual(saved.favorites,['com.example.a']);
  assert.equal(vm.runInContext("sortApps(allApps,'recent')[0].package",context),'com.example.b');
  assert.equal(vm.runInContext("sortApps(allApps,'favorites')[0].package",context),'com.example.a');
});

test('force-stop has its own confirmation and never becomes an uninstall', async () => {
  const calls=[];
  const {context}=gui(async name=>{
    calls.push(name);
    if(name==='get_health_report') return {status:{state:'Running'}};
    if(name==='get_apps') return [];
    if(name==='force_stop_app') return {success:true,message:'Stopped'};
  });
  await tick();
  await vm.runInContext("appMenuAction('force-stop','com.example.game','Game',null)",context);
  assert.equal(calls.filter(n=>n==='force_stop_app').length,1);
  assert.ok(!calls.includes('uninstall_app'));
  await vm.runInContext("appMenuAction('force-stop','com.android.settings','Settings','Protected')",context);
  assert.equal(calls.filter(n=>n==='force_stop_app').length,1);
  const cancelled=gui(async name=>{
    if(name==='get_health_report') return {status:{state:'Running'}};
    if(name==='get_apps') return [];
    assert.notEqual(name,'force_stop_app');
  },async()=>false);
  await tick();
  await vm.runInContext("appMenuAction('force-stop','com.example.game','Game',null)",cancelled.context);
});

test('explicit library refresh reads changes without launching or installing apps', async () => {
  let apps=[{package:'com.example.a'}];
  const {context}=gui(async name=>{
    if(name==='get_health_report') return {status:{state:'Running'}};
    if(name==='get_apps') return apps;
    if(name==='get_library') return {favorites:[],recent:{},sort:'name-asc'};
    throw Error(`unexpected mutation: ${name}`);
  });
  await tick();
  apps=[{package:'com.example.b'}];
  await vm.runInContext('(async()=>{await refreshApps();await loadLibrary();})()',context);
  assert.equal(vm.runInContext('allApps[0].package',context),'com.example.b');
});

test('controller preserves explicit target and escapes runtime metadata', async () => {
  const calls = [];
  const record = { id: 'r-123', name: '<Android>', state: 'Running', job: { operation: 'start', status: 'Succeeded', error: '<failure>' } };
  const { context, element } = gui(async (command, args) => {
    if (command !== 'runtime_controller') return;
    calls.push(args);
    return args.request.op === 'list' ? [record] : record;
  });
  await vm.runInContext("runtimeSettingsId='r-123'",context);
  await vm.runInContext('refreshController()', context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /&lt;Android&gt;/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /&lt;failure&gt;/);
  assert.equal(calls.find(call => call.request.op === 'refresh' && call.request.id !== 'default').request.id, 'r-123');
  assert.ok(calls.every(args => args.runtimeId === undefined));
});

test('controller reserves mutation before confirmation and honors cancellation', async () => {
  const confirmation = deferred();
  const calls = [];
  const { context } = gui(async (command, args) => {
    if (command !== 'runtime_controller') return;
    calls.push(args.request);
    return [];
  }, () => confirmation.promise, '?view=settings');
  await tick(); calls.length=0;
  vm.runInContext("controllerRecords=[{id:'r-123',name:'One',state:'Allocated'}]",context);
  const first = vm.runInContext("mutateController({ op: 'prepare', id: 'r-123' })", context);
  await vm.runInContext("mutateController({ op: 'prepare', id: 'r-456' })", context);
  assert.equal(calls.length, 0);
  confirmation.resolve(false);
  await first;
  assert.deepEqual(calls.filter(r => r.id !== 'default').map(r => r.op), ['list', 'arm_sources']);
  assert.ok(calls.every(r => ['list', 'arm_sources', 'resources_info', 'refresh'].includes(r.op)));
});

test('controller failure disables creation and clears stale runtime actions', async () => {
  const { context, element } = gui(async command => {
    if (command === 'runtime_controller') throw new Error('socket unavailable');
  });
  element('controller-runtimes').innerHTML = 'stale buttons';
  await vm.runInContext('refreshController()', context);
  assert.equal(element('controller-create-button').disabled, true);
  assert.doesNotMatch(element('controller-runtimes').innerHTML, /data-controller-op=/);
  assert.match(element('controller-message').textContent, /socket unavailable/);
});


test('Settings deep link does not start legacy Android', async () => {
  const calls = [];
  const { element } = gui(async (command, args) => {
    calls.push(command);
    if (command === 'runtime_controller') return [];
    throw new Error('legacy registry unavailable');
  }, async () => true, '?view=settings');
  await tick();
  assert.ok(calls.includes('get_runtime_catalog'));
  assert.ok(calls.includes('runtime_controller')); // Read startup inventory only.
  assert.ok(!calls.includes('start_runtime'));
  assert.ok(!calls.includes('get_health_report'));
  assert.match(element('settings-runtime').textContent, /legacy registry unavailable/);
});

test('rename pins runtime ID, saves entered name and suppresses repeat submits', async () => {
  const pending = deferred();
  const record = { id: 'r-123', name: 'Original', state: 'Allocated', job: null };
  const calls = [];
  const { context, element } = gui(async (command, args) => {
    if (command !== 'runtime_controller') return;
    calls.push(args.request);
    if (args.request.op === 'rename') { await pending.promise; record.name = args.request.name; }
    return args.request.op === 'list' ? [record] : record;
  });
  await vm.runInContext('refreshController();', context);
  vm.runInContext("openRuntimeRename('r-123')", context);
  element('runtime-rename-name').value = 'Renamed Android';
  const first = vm.runInContext('saveRuntimeRename()', context);
  await tick();
  await vm.runInContext('saveRuntimeRename()', context);
  assert.equal(element('runtime-rename-save').disabled, true);
  assert.equal(calls.filter(r => r.op === 'rename').length, 1);
  assert.equal(calls.find(r => r.op === 'rename').id, 'r-123');
  pending.resolve();
  await first;
  assert.equal(record.name, 'Renamed Android');
  assert.equal(element('runtime-rename-dialog').open, false);
});

test('failed rename stays editable and polling preserves operation error', async () => {
  const record = { id: 'r-123', name: 'Original', state: 'Allocated', job: null };
  const { context, element } = gui(async (command, args) => {
    if (command !== 'runtime_controller') return;
    if (args.request.op === 'rename') throw new Error('Name rejected');
    return args.request.op === 'list' ? [record] : record;
  });
  await vm.runInContext('refreshController()', context);
  vm.runInContext("openRuntimeRename('r-123')", context);
  element('runtime-rename-name').value = 'Changed';
  await vm.runInContext('saveRuntimeRename()', context);
  await vm.runInContext('refreshController()', context);
  assert.equal(element('runtime-rename-dialog').open, true);
  assert.match(element('runtime-rename-error').textContent, /Name rejected/);
  assert.equal(element('controller-message').textContent, '');
});

test('mutation waits for in-flight polling without losing the click', async () => {
  const poll = deferred();
  const calls = [];
  let first = true;
  const record={id:'r-123',name:'One',state:'Allocated'};
  const { context } = gui(async (command, args) => {
    if (command !== 'runtime_controller') return;
    calls.push(args.request);
    if (args.request.op === 'list' && first) { first = false; return poll.promise; }
    return args.request.op==='list' ? [record] : args.request.op==='refresh' ? {...record} : null;
  }, undefined, '?view=settings');
  const refresh = vm.runInContext('refreshController()', context);
  const mutation = vm.runInContext("mutateController({op: 'prepare', id: 'r-123'})", context);
  await tick();
  assert.ok(!calls.some(r => r.op === 'prepare'));
  poll.resolve([record]);
  await refresh;
  await mutation;
  assert.equal(calls.filter(r => r.op === 'prepare').length, 1);
});


test('opening Apps after Settings initializes once without starting the legacy runtime', async () => {
  const calls = [];
  const { context } = gui(async command => {
    calls.push(command);
    if (command === 'runtime_controller' || command === 'get_apps') return [];
    if (command === 'get_health_report') return {status: {state: 'Running'}};
    if (command === 'start_runtime') return {success: true, message: 'Started'};
  }, async () => true, '?view=settings');
  await tick();
  assert.equal(calls.filter(c => c === 'start_runtime').length, 0);
  vm.runInContext("navigatePage('apps'); navigatePage('apps')", context);
  await tick();
  assert.equal(calls.filter(c => c === 'start_runtime').length, 0);
  assert.equal(calls.filter(c => c === 'get_apps').length, 1);
});


test('runtime card shows confirmation, immediate acceptance, progress and completion', async () => {
  const confirmation = deferred();
  const accepted = deferred();
  let record = {id: 'r-progress', name: 'Android', state: 'Allocated', job: null};
  const { context, element } = gui(async (command, args) => {
    if (command !== 'runtime_controller') return;
    if (args.request.op === 'prepare') { record = await accepted.promise; return record; }
    return args.request.op === 'list' ? [record] : record;
  }, () => confirmation.promise);
  await vm.runInContext("runtimeSettingsId='r-progress'",context);
  await vm.runInContext('refreshController()', context);
  const operation = vm.runInContext("mutateController({op:'prepare',id:'r-progress'})", context);
  await tick();
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /Waiting for confirmation/);
  confirmation.resolve(true);
  await tick();
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /Sending request/);
  accepted.resolve({...record, state:'Provisioning',job:{id:'job1',operation:'prepare',status:'Running',error:null}});
  await operation;
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /Preparing/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /role="progressbar"/);
  record = {...record,state:'Prepared',job:{...record.job,status:'Succeeded'}};
  await vm.runInContext('refreshController()', context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /Prepared/);
  assert.doesNotMatch((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML), /role="progressbar"/);
});

test('missing Waydroid explains setup and disables preparation while keeping rename', async () => {
  const record = {id:'r-setup',name:'Android',state:'Allocated',job:null};
  const setup = {can_prepare:false,can_start:false,checks:[{id:'waydroid',label:'Waydroid',available:false,detail:'Install Waydroid first.'}]};
  const {context,element} = gui(async (command,args) => {
    if(command === 'runtime_controller') return args.request.op === 'list' ? [record] : record;
  }, async()=>true, '', setup);
  await vm.runInContext("runtimeSettingsId='r-setup'",context);
  await vm.runInContext('refreshController()',context);
  assert.match(element('controller-setup').innerHTML,/Setup required/);
  assert.match(element('controller-setup').innerHTML,/Install Waydroid first/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-controller-op="prepare"[^>]*disabled/);
  assert.doesNotMatch((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-controller-op="rename"[^>]*disabled/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Prepare \/ Start is unavailable/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Install Waydroid first/);
});

test('failed preparation offers prepare retry and successful boot keeps headless settings visible', async () => {
  let record = {id:'r-error',name:'Android',state:'Error',job:{operation:'prepare',status:'Failed',error:'Disk full'}};
  const {context,element} = gui(async (command,args) => {
    if(command === 'runtime_controller') return args.request.op === 'list' ? [record] : record;
  });
  vm.runInContext("runtimeSettingsId='r-error'",context);
  await vm.runInContext('refreshController()',context);
  let html = (element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/Retry preparation/);
  assert.match(html,/Disk full/);
  assert.doesNotMatch(html,/data-controller-op="start"/);
  record = {...record,state:'Running',job:{operation:'start',status:'Succeeded',error:null}};
  await vm.runInContext('refreshController()',context);
  html = (element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/runtime-status success[^>]*>Running/);
  assert.match(html,/Display mode: Headless/);
  assert.match(html,/Background mode; no app windows/);
  assert.match(html,/aria-label="Stop · Android"/);
});


test('storage move pins destination and runtime ID and retains progress feedback', async () => {
  const calls = [];
  const {context,element} = gui(async (command,args) => {
    if(command !== 'runtime_controller') return;
    calls.push(args.request);
    return args.request.op === 'list' ? [] : {};
  });
  vm.runInContext("controllerRecords = [{id:'r-move',name:'My Android',state:'Stopped',job:null,storage:{path:'/old',previous_path:null}}]; openRuntimeStorage('r-move','move')",context);
  element('runtime-storage-path').value = '/mnt/new disk';
  await vm.runInContext('saveRuntimeStorage()',context);
  const move = calls.find(r=>r.op==='move');
  assert.equal(move.id,'r-move');
  assert.equal(move.destination,'/mnt/new disk');
  assert.equal(element('runtime-storage-dialog').open,false);
});

test('retained-copy removal requires the exact runtime name', async () => {
  const calls = [];
  const {context,element} = gui(async (command,args) => {
    if(command === 'runtime_controller') { calls.push(args.request); return []; }
  }, undefined, '?view=settings');
  await tick(); calls.length=0;
  vm.runInContext("controllerRecords = [{id:'r-move',name:'My Android',state:'Stopped',job:null,storage:{path:'/new',previous_path:'/old'}}]; openRuntimeStorage('r-move','discard_previous')",context);
  element('runtime-storage-confirm-name').value = 'wrong';
  await vm.runInContext('saveRuntimeStorage()',context);
  assert.equal(calls.length,0);
  assert.match(element('runtime-storage-error').textContent,/My Android/);
  element('runtime-storage-confirm-name').value = 'My Android';
  await vm.runInContext('saveRuntimeStorage()',context);
  assert.equal(calls.find(r=>r.op==='discard_previous').confirmed_name,'My Android');
});

test('storage progress disables lifecycle mutations and unavailable disk blocks start', () => {
  const {context,element} = gui(async()=>{});
  vm.runInContext("runtimeSettingsId='r-move'",context);
  vm.runInContext("controllerRequirements={can_start:true,can_prepare:true}; controllerRecords=[{id:'r-move',name:'My Android',state:'Stopped',job:null,storage:{path:'/disk',job:{status:'Running',phase:'verify'},error:null}}]; renderController()",context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Verifying the copied data/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-controller-op="start"[^>]*disabled/);
  vm.runInContext("controllerRecords[0].storage={path:'/disk',error:'Disk unavailable'}; renderController()",context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Disk unavailable/);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-controller-op="start"[^>]*disabled/);
});


test('failed preparation allows relocation and explains hidden storage and retained space', () => {
  const {context,element} = gui(async()=>{});
  vm.runInContext("runtimeSettingsId='r-test'",context);
 vm.runInContext("controllerRequirements={can_prepare:true}; controllerRecords=[{id:'r-test',name:'2',state:'Error',job:{operation:'prepare',status:'Failed',error:'Insufficient space'},storage:{path:'/disk/.anvildroid-example',previous_path:'/old',job:{status:'Succeeded'}}}]; renderController()",context);
  let html = (element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/Ctrl\+H/);
  assert.match(html,/does not free space/);
  assert.doesNotMatch(html,/Stop Android before changing/);
  assert.match(html,/Choose a storage location before Prepare/);
  vm.runInContext('controllerRecords[0].storage.previous_path=null; renderController()',context);
  html = (element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/data-controller-op="move"[^>]*>/);
  assert.doesNotMatch(html,/data-controller-op="move"[^>]*disabled/);
  vm.runInContext("controllerRecords[0].job.operation='start'; renderController()",context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-controller-op="move"[^>]*disabled/);
});


test('health pins runtime ID, suppresses duplicate probes and escapes worker errors', async () => {
  const pending = deferred();
  const calls = [];
  const {context,element} = gui(async (command,args) => {
    if (command === 'runtime_controller' && args.request.op === 'health') { calls.push(args.request); return pending.promise; }
  }, undefined, '?view=settings');
  await tick();
  vm.runInContext("runtimeSettingsId='r-health'",context);
  vm.runInContext("controllerRecords=[{id:'r-health',name:'Health',state:'Running'}]",context);
  const first = vm.runInContext("checkRuntimeHealth('r-health')",context);
  await vm.runInContext("checkRuntimeHealth('r-health')",context);
  assert.equal(calls.length,1);
  assert.equal(calls[0].id,'r-health');
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Checking graphics, worker and storage/);
  pending.resolve({runtime_id:'r-health',checked_at:1000,storage_path:'/new/disk',free_bytes:1073741824,total_bytes:2147483648,worker_state:'Error',graphics:{gles:'AMD <renderer>',renderer_kind:'reported',scope:'Android compositor'},gpu_devices:[{node:'/dev/dri/renderD128',driver:'amdgpu',available:true}],checks:[{name:'Worker',status:'error',detail:'<bad worker>'}]});
  await first;
  const html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/1.00 GiB free/);
  assert.match(html,/&lt;bad worker&gt;/);
  assert.match(html,/Health snapshot/);
  assert.match(html,/AMD &lt;renderer&gt;/);
  assert.match(html,/renderD128/);
  assert.doesNotMatch(html,/Software renderer detected/);
});

test('health request failure remains attached to the selected runtime', async () => {
  const {context,element}=gui(async()=>{throw new Error('Worker timeout');});
  vm.runInContext("runtimeSettingsId='r-health'",context);
  vm.runInContext("controllerRecords=[{id:'r-health',name:'Health',state:'Stopped'}]",context);
  await vm.runInContext("checkRuntimeHealth('r-health')",context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Health check failed.*Worker timeout/);
  assert.doesNotMatch((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Checking health/);
});


test('package listing targets the chosen runtime and never invokes the legacy app backend', async () => {
  const calls=[];
  const record={id:'r-two',name:'Two',state:'Running'};
  const {context,element}=gui(async(command,args)=>{
    calls.push([command,args]);
    if(command!=='runtime_controller')return;
    if(args.request.op==='list')return [record];
    if(args.request.op==='refresh')return {...record};
    if(args.request.op==='apps')return {runtime_id:args.request.id,apps:[{package:'com.example.test',label:'<test>',launchable:true}]};
  },undefined,'?view=settings');
  await tick();calls.length=0;
  await vm.runInContext('refreshManagedLibrary()',context);
  const inventory=calls.filter(([name,args])=>name==='runtime_controller'&&args.request.op==='apps');
  assert.equal(inventory.length,1);
  assert.equal(inventory[0][1].request.id,'r-two');
  assert.ok(!calls.some(([name])=>name==='get_apps'));
  assert.match(element('apps-grid').innerHTML,/&lt;test&gt;/);
  assert.match(element('apps-grid').innerHTML,/data-runtime-id="r-two"/);
});

test('package listing rejects a result for another runtime', async () => {
  const record={id:'r-two',name:'Two',state:'Running'};
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller')return;
    if(args.request.op==='list')return [record];
    if(args.request.op==='refresh')return {...record};
    if(args.request.op==='apps')return {runtime_id:'r-other',apps:[{package:'com.other',launchable:true}]};
  },undefined,'?view=settings');
  await tick();
  await vm.runInContext('refreshManagedLibrary()',context);
  assert.match(element('library-runtime-status').innerHTML,/Invalid app inventory/);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="com.other"/);
  assert.equal(vm.runInContext("managedSnapshots.has('r-two')",context),false);
});

test('headless app actions confirm their target, reserve clicks and report a job', async () => {
  const consent=deferred();
  const calls=[];
  let submitted=false;
  const record={id:'r-action',name:'My runtime',state:'Running'};
  const job={id:'job-one',runtime_id:'r-action',package:'com.android.settings',action:'launch',status:'Running'};
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    const request=args.request;
    calls.push(request);
    if(request.op==='app_action') {submitted=true;return job;}
    if(request.op==='list') return [record];
    if(request.op==='refresh') return {...record};
    if(request.op==='app_job') return submitted?job:null;
  },async(message)=>{ assert.match(message,/headless Android/);assert.match(message,/My runtime/);return consent.promise; });
  await tick();
  vm.runInContext("runtimeSettingsId='r-action'",context);
  vm.runInContext("controllerRecords=[{id:'r-action',name:'My runtime',state:'Running'}]",context);
  const task=vm.runInContext("mutateController({op:'app_action',id:'r-action',action:'launch',package:'com.android.settings'})",context);
  await tick();
  await vm.runInContext("mutateController({op:'app_action',id:'r-action',action:'launch',package:'com.android.settings'})",context);
  assert.equal(calls.filter(r=>r.op==='app_action').length,0);
  consent.resolve(true);
  await task;
  assert.equal(calls.filter(r=>r.op==='app_action').length,1);
  assert.equal(vm.runInContext('controllerRecords.length',context),1);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Launching app in headless Android/);
});

test('cancelled force-stop sends no app action', async () => {
  const actions=[];
  const {context}=gui(async(command,args)=>{
    if(command==='runtime_controller') {actions.push(args.request);return args.request.op==='list'?[]:{};}
  },async(message)=>{assert.match(message,/Unsaved app work/);return false;});
  await vm.runInContext("mutateController({op:'app_action',id:'r-action',action:'force_stop',package:'com.android.settings'})",context);
  assert.equal(actions.filter(r=>r.op==='app_action').length,0);
});

test('desktop mode exposes window actions and applies a display change with restart while running', () => {
  const {context,element}=gui(async()=>{});
  vm.runInContext("runtimeSettingsId='r-desktop';controllerRecords=[{id:'r-desktop',name:'Desktop',state:'Running',display:{mode:'desktop',effective_mode:'desktop'}}];renderController()",context);
  const html=element('controller-runtimes').innerHTML+element('runtime-settings-content').innerHTML;
  assert.match(html,/Display mode: Desktop/);
  assert.match(html,/data-controller-op="open_waydroid"[^>]*aria-label="Open Waydroid · Desktop"/);
  assert.doesNotMatch(html,/data-controller-op="display_set"[^>]*disabled/);
  assert.match(html,/Use Headless & restart/);
  vm.runInContext("controllerRecords[0].state='Stopped';renderController()",context);
  const stopped=element('runtime-settings-content').innerHTML;
  assert.doesNotMatch(stopped,/data-controller-op="display_set"[^>]*disabled/);
  assert.doesNotMatch(stopped,/Use Headless & restart/);
});

test('saving display mode retains the runtime record instead of inserting a configuration as a runtime', async()=>{
  const record={id:'r-mode',name:'Mode',state:'Prepared'};
  const {context}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    const request=args.request;
    if(request.op==='display_set'||request.op==='display_info') return {mode:'desktop',effective_mode:null};
    if(request.op==='list') return [record];
    if(request.op==='refresh') return {...record};
    return null;
  });
  vm.runInContext("controllerRecords=[{id:'r-mode',name:'Mode',state:'Prepared'}]",context);
  await vm.runInContext("mutateController({op:'display_set',id:'r-mode',mode:'desktop'})",context);
  assert.equal(vm.runInContext('controllerRecords.length',context),1);
  assert.equal(vm.runInContext('controllerRecords[0].display.mode',context),'desktop');
});

 test('older package snapshots never offer actions for unverified system services', async () => {
  const record={id:'r-desktop',name:'Desktop',state:'Running',display:{mode:'desktop'}};
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller')return;
    if(args.request.op==='list')return [record];
    if(args.request.op==='refresh')return {...record};
    if(args.request.op==='apps')return {runtime_id:record.id,apps:[{package:'android.ext.services'}]};
  },undefined,'?view=settings');
  await tick();
  await vm.runInContext('refreshManagedLibrary()',context);
  assert.match(element('library-runtime-status').innerHTML,/Stop and start/);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="android.ext.services"/);
  assert.equal(vm.runInContext("managedSnapshots.has('r-desktop')",context),false);
});

test('library aggregates duplicate packages by runtime and composes runtime/search filters', async () => {
  const records=[{id:'r-one',name:'One <Android>',state:'Running'},{id:'r-two',name:'Two',state:'Running'}];
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    const r=args.request;
    if(r.op==='list') return records;
    if(r.op==='refresh') return records.find(record=>record.id===r.id);
    if(r.op==='apps') return {runtime_id:r.id,apps:[{package:'com.example.same',launchable:true}]};
  },undefined,'?view=settings');
  await vm.runInContext('refreshManagedLibrary()',context);
  let html=element('apps-grid').innerHTML;
  assert.equal((html.match(/data-package="com.example.same"/g)||[]).length,2);
  assert.match(html,/One &lt;Android&gt;/);
  vm.runInContext("setLibraryRuntimeFilter(['r-two'])",context);
  vm.runInContext('renderApps(allApps)',context);
  html=element('apps-grid').innerHTML;
  assert.match(html,/data-runtime-id="r-two"/);
  assert.doesNotMatch(html,/data-runtime-id="r-one"/);
  element('search-apps').value='missing';
  vm.runInContext('renderApps(allApps)',context);
  assert.match(element('apps-grid').innerHTML,/No matching apps/);
  element('search-apps').value='same';
  element('favorites-only').checked=true;
  vm.runInContext("library.favorites=['com.example.same'];renderApps(allApps)",context);
  assert.match(element('apps-grid').innerHTML,/No matching apps/); // legacy favorite must not leak
});

test('stopped runtime keeps saved apps, partial failure keeps other runtimes usable', async()=>{
  let stopped=false;
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    const r=args.request;
    if(r.op==='list') return [{id:'r-one',name:'One',state:stopped?'Stopped':'Running'},{id:'r-two',name:'Two',state:'Running'}];
    if(r.id==='r-two') throw new Error('Unavailable <disk>');
    if(r.op==='refresh') return {id:'r-one',name:'One',state:stopped?'Stopped':'Running'};
    if(r.op==='apps') return {runtime_id:'r-one',apps:[{package:'com.example.app',launchable:true}]};
  },undefined,'?view=settings');
  await vm.runInContext('refreshManagedLibrary()',context);
  assert.match(element('apps-grid').innerHTML,/com.example.app/);
  assert.match(element('library-runtime-status').innerHTML,/Unavailable &lt;disk&gt;/);
  stopped=true;
  await vm.runInContext('refreshManagedLibrary()',context);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="com.example.app"/);
  assert.equal(vm.runInContext("managedSnapshots.get('r-one')[0].package",context),'com.example.app');
  assert.match(element('library-runtime-status').innerHTML,/Select this runtime to start it/);
  await vm.runInContext("launchApp('com.example.app','r-one')",context);
  assert.match(element('app-toast-message').textContent,/Start this runtime on the Runtime page/);
});

test('managed library launch pins runtime and never invokes legacy actions', async()=>{
  const calls=[];
  const {context,element}=gui(async(command,args)=>{
    calls.push([command,args]);
    if(command!=='runtime_controller') return;
    const r=args.request;
    if(r.op==='display_info') return {mode:'desktop',effective_mode:'desktop'};
    if(r.op==='app_action') return {id:'job',runtime_id:r.id,status:'Succeeded',action:r.action,package:r.package,result:{message:'Opened'}};
  },undefined,'?view=settings');
  vm.runInContext("managedLibrary=[{id:'r-two',name:'Two',state:'Running'}];managedSnapshots.set('r-two',[{package:'com.example.same',launchable:true}])",context);
  await tick();calls.length=0;
  await vm.runInContext("launchApp('com.example.same','r-two')",context);
  assert.deepEqual(calls.filter(([name])=>name==='runtime_controller').map(([name,args])=>[name,args.request.op,args.request.id]),[['runtime_controller','display_info','r-two'],['runtime_controller','app_action','r-two']]);
  assert.match(element('app-toast-message').textContent,/Launch requested: com.example.same.*Two/);
  calls.length=0;
  await vm.runInContext("appMenuAction('uninstall','com.example.same','Same',null,'r-two')",context);
  assert.equal(calls.some(([name])=>name==='uninstall_app'),false);
  assert.ok(calls.some(([name,args])=>name==='runtime_controller' && args.request.op==='app_action' && args.request.action==='uninstall' && args.request.id==='r-two'));
});

test('old workers and cross-runtime inventory cannot populate library actions', async()=>{
  let wrong=false;
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    const r=args.request;
    if(r.op==='list') return [{id:'r-one',name:'One',state:'Running'}];
    if(r.op==='refresh') return {id:'r-one',name:'One',state:'Running'};
    if(r.op==='apps') return {runtime_id:wrong?'r-other':'r-one',apps:[{package:'android.ext.services'}]};
  },undefined,'?view=settings');
  await vm.runInContext('refreshManagedLibrary()',context);
  // Unverified snapshots must not expose any app actions.
  const packages=[...element('apps-grid').innerHTML.matchAll(/data-package="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(packages,[]);
  assert.match(element('library-runtime-status').innerHTML,/Stop and start/);
  wrong=true;
  await vm.runInContext('refreshManagedLibrary()',context);
  assert.match(element('library-runtime-status').innerHTML,/Invalid app inventory/);
});


test('force-stop confirmation retains original runtime when filter changes and blocks duplicates', async()=>{
  const consent=deferred(); const calls=[];
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    const r=args.request; calls.push(r);
    if(r.op==='display_info') return {mode:'desktop'};
    if(r.op==='app_action') return {id:'job',runtime_id:r.id,package:r.package,action:r.action,status:'Succeeded'};
  },()=>consent.promise,'?view=settings');
  await tick(); calls.length=0;
  vm.runInContext("managedLibrary=[{id:'r-two',name:'Two',state:'Running'}];managedSnapshots.set('r-two',[{package:'com.example.same',launchable:true}])",context);
  const pending=vm.runInContext("managedAppAction('force_stop','com.example.same','r-two')",context);
  vm.runInContext("setLibraryRuntimeFilter(['default'])",context);
  await vm.runInContext("managedAppAction('force_stop','com.example.same','r-two')",context);
  assert.equal(calls.length,0);
  consent.resolve(true);await pending;
  assert.equal(calls.filter(r=>r.op==='app_action').length,1);
  assert.equal(calls.find(r=>r.op==='app_action').id,'r-two');
});

test('stopped runtime loads persisted snapshot after reopening without starting Android', async()=>{
  const calls=[];
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller') return;
    calls.push(args.request.op);
    if(args.request.op==='list') return [{id:'r-one',name:'One',state:'Stopped'}];
    if(args.request.op==='refresh') return {id:'r-one',name:'One',state:'Stopped'};
  },undefined,'?view=settings');
  await tick(); calls.length=0;
  vm.runInContext("localStorage.getItem = key => key === 'anvildroid_managed_apps:r-one' ? JSON.stringify([{package:'com.example.saved',launchable:true}]) : null",context);
  await vm.runInContext('refreshManagedLibrary()',context);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="com.example.saved"/);
  assert.equal(vm.runInContext("managedSnapshots.get('r-one')[0].package",context),'com.example.saved');
  assert.match(element('library-runtime-status').innerHTML,/Select this runtime to start it/);
  assert.deepEqual(calls,['list','refresh']);
});


test('Settings creates, prepares and starts a new runtime after saving Desktop mode', async()=>{
 const calls=[]; let created=false;
 const record={id:'r-'+'1'.repeat(32),name:'New',state:'Allocated',job:null};
 const {context,element,banner}=gui(async(command,args)=>{
  if(command!=='runtime_controller') return;
  const r=args.request;calls.push(r);
  if(r.op==='create'){created=true;return {...record};}
  if(r.op==='display_set'||r.op==='display_info')return {mode:'desktop',effective_mode:null};
  if(['prepare','start'].includes(r.op)){
   record.state=r.op==='prepare'?'Provisioning':'Starting';record.job={id:r.op,operation:r.op,status:'Running'};return {...record};
  }
  if(r.op==='list')return created?[record]:[];
  if(r.op==='refresh'){
   if(r.id==='default')return {id:'default',name:'Default',state:'Stopped'};
   if(record.job){record.state=record.job.operation==='prepare'?'Prepared':'Running';record.job={...record.job,status:'Succeeded'};}
   return {...record};
  }
 },undefined,'?view=settings');
 await tick();calls.length=0;
 element('controller-name').value='New';
 await vm.runInContext("mutateController({op:'create',name:'New'})",context);
 assert.deepEqual(calls.filter(r=>['create','display_set','prepare','start'].includes(r.op)).map(r=>r.op),['create','display_set','prepare','start']);
 assert.ok(calls.filter(r=>['display_set','prepare','start'].includes(r.op)).every(r=>r.id===record.id));
 assert.equal(calls.some(r=>r.op==='stop'),false);
 assert.equal(vm.runInContext('controllerRecords[0].display.mode',context),'desktop');
 assert.equal(vm.runInContext('controllerRecords[0].state',context),'Running');
 assert.equal(vm.runInContext('selectedRuntimeId',context),record.id);
 assert.equal(element('controller-name').value,'');
 assert.match(banner.textContent,/Android is running/);
});

for(const failure of ['display_set','prepare-job','start']){
 test(`creation retains the new runtime and reports ${failure} without a duplicate or later action`,async()=>{
  const calls=[],prompts=[];let created=false;
  const record={id:'r-'+'3'.repeat(32),name:'New',state:'Allocated',job:null};
  const {context,element,banner}=gui(async(command,args)=>{
   if(command!=='runtime_controller')return;
   const r=args.request;calls.push(r);
   if(r.op==='create'){created=true;return {...record};}
   if(r.op===failure)throw Error('Fixture stage failure');
   if(r.op==='display_set'||r.op==='display_info')return {mode:'desktop'};
   if(r.op==='prepare'){
    record.state='Provisioning';record.job={id:'prepare',operation:'prepare',status:'Running'};return {...record};
   }
   if(r.op==='list')return created?[record]:[];
   if(r.op==='refresh'){
    if(r.id==='default')return {id:'default',state:'Stopped'};
    if(record.job){
     record.state=failure==='prepare-job'?'Error':'Prepared';
     record.job={...record.job,status:failure==='prepare-job'?'Failed':'Succeeded',error:failure==='prepare-job'?'Fixture prepare failure':null};
    }
    return {...record};
   }
  },async text=>{prompts.push(text);return true;},'?view=settings');
  await tick();calls.length=0;element('controller-name').value='New';
  await vm.runInContext("mutateController({op:'create',name:'New'})",context);
  assert.equal(calls.filter(r=>r.op==='create').length,1);
  assert.equal(calls.filter(r=>r.op==='start').length,failure==='start'?1:0);
  assert.equal(vm.runInContext('controllerRecords.length',context),1);
  assert.equal(vm.runInContext('selectedRuntimeId',context),record.id);
  assert.equal(vm.runInContext('controllerMutation',context),false);
  assert.equal(element('controller-name').value,'');
  assert.equal(prompts.length,0);
  assert.match(banner.textContent,/was created, but/);
  assert.match(banner.textContent,/Fixture/);
  assert.doesNotMatch(banner.textContent,/Android is running/);
  assert.ok(calls.filter(r=>['display_set','prepare','start','stop','delete'].includes(r.op)).every(r=>r.id===record.id));
 });
}

test('pending creation shows its stage and blocks duplicate Create until boot completes',async()=>{
 const calls=[],prepared=deferred();let created=false;
 const record={id:'r-'+'4'.repeat(32),name:'New',state:'Allocated',job:null};
 const {context,element}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  const r=args.request;calls.push(r);
  if(r.op==='create'){created=true;return {...record};}
  if(r.op==='display_set'||r.op==='display_info')return {mode:'desktop'};
  if(['prepare','start'].includes(r.op)){
   record.state=r.op==='prepare'?'Provisioning':'Starting';record.job={id:r.op,operation:r.op,status:'Running'};return {...record};
  }
  if(r.op==='list')return created?[record]:[];
  if(r.op==='refresh'){
   if(r.id==='default')return {id:'default',state:'Stopped'};
   if(record.job?.operation==='prepare'&&record.job.status==='Running')return prepared.promise;
   if(record.job?.operation==='start'){record.state='Running';record.job={...record.job,status:'Succeeded'};}
   return {...record};
  }
 },undefined,'?view=settings');
 await tick();calls.length=0;
 const work=vm.runInContext("mutateController({op:'create',name:'New'})",context);
 await tick();
 assert.match(element('controller-message').textContent,/Preparing images/);
 assert.equal(element('controller-create-button').disabled,true);
 assert.equal(vm.runInContext('controllerPending.id',context),record.id);
 assert.equal(vm.runInContext('controllerRecords[0].state',context),'Provisioning');
 await vm.runInContext("mutateController({op:'create',name:'Duplicate'})",context);
 assert.equal(calls.filter(r=>r.op==='create').length,1);
 assert.equal(calls.filter(r=>r.op==='start').length,0);
 record.state='Prepared';record.job={...record.job,status:'Succeeded'};prepared.resolve({...record});
 await work;
 assert.equal(calls.filter(r=>r.op==='start').length,1);
 assert.equal(vm.runInContext('controllerRecords[0].state',context),'Running');
 assert.equal(vm.runInContext('controllerMutation',context),false);
 assert.equal(element('controller-create-button').disabled,false);
});

test('managed app icons use their own snapshot and never the default icon route',()=>{
 const {context,element}=gui(async()=>{},undefined,'?view=settings');
 vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'},{id:'r-two',name:'Two',state:'Running'}];managedSnapshots.set('r-one',[{package:'com.example.same',launchable:true,icon_data:'AAAA'}]);managedSnapshots.set('r-two',[{package:'com.example.same',launchable:true,icon_data:'BBBB'}]);renderApps(allApps)",context);
 const html=element('apps-grid').innerHTML;
 assert.match(html,/src="data:image\/png;base64,AAAA"/);
 assert.match(html,/src="data:image\/png;base64,BBBB"/);
 assert.doesNotMatch(html,/appicon:\/\/localhost\/default/);
 assert.equal(vm.runInContext("libraryIconUrl({runtime_id:'r-two',icon_data:'bad\" onclick=\"alert(1)'})",context),null);
 assert.equal(vm.runInContext("libraryIconUrl({runtime_id:'r-two',package:'com.example.same',icon_path:'/default/icon.png'})",context),null);
 assert.equal(vm.runInContext("libraryIconUrl({package:'com.example.same',icon_path:'/legacy/icon.png'})",context),'appicon://localhost/default/com.example.same.png');
});


test('managed favorite uses the pinned runtime, filters independently and does not alter legacy', async()=>{
 const saved=deferred();const writes=[];
 const {context,element}=gui(async(command,args)=>{
  if(command==='set_favorite'){writes.push(args);return saved.promise;}
 },undefined,'?view=settings');
 await tick();
 vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'},{id:'r-two',name:'Two',state:'Running'}];for(const id of ['r-one','r-two']){managedSnapshots.set(id,[{package:'com.example.same',launchable:true}]);managedPreferences.set(id,{favorites:[],recent:{}})};library.favorites=['com.example.same']",context);
 const pending=vm.runInContext("appMenuAction('favorite','com.example.same','Same',null,'r-two')",context);
 vm.runInContext("setLibraryRuntimeFilter(['r-one'])",context);
 await vm.runInContext("toggleManagedFavorite('com.example.same','r-two')",context);
 assert.equal(writes.length,1);assert.equal(writes[0].runtimeId,'r-two');assert.equal(writes[0].enabled,true);
 saved.resolve({favorites:['com.example.same'],recent:{}});await pending;
 assert.equal(vm.runInContext("isFavorite({package:'com.example.same',runtime_id:'r-one'})",context),false);
 assert.equal(vm.runInContext("isFavorite({package:'com.example.same',runtime_id:'r-two'})",context),true);
 assert.equal(vm.runInContext("isFavorite({package:'com.example.same'})",context),true);
 element('favorites-only').checked=true;vm.runInContext('renderApps(allApps)',context);
 assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="com.example.same"/);
 assert.match(element('apps-grid').innerHTML,/data-package="org.anvildroid.desktop"/);
 vm.runInContext("setLibraryRuntimeFilter(['r-two'])",context);vm.runInContext('renderApps(allApps)',context);
 assert.match(element('apps-grid').innerHTML,/favorite-mark/);
});

test('managed recent history records only a successful launch and sort uses the runtime timestamp', async()=>{
 const calls=[];let fail=false;
 const {context}=gui(async(command,args)=>{
  if(command==='record_runtime_launch'){calls.push(args);return {favorites:[],recent:{'com.example.same':200}};}
  if(command==='runtime_controller'){
   const r=args.request;
   if(r.op==='display_info')return {mode:'desktop'};
   if(r.op==='app_action')return {id:'job',runtime_id:r.id,action:r.action,package:r.package,status:fail?'Failed':'Succeeded',error:fail?'No launcher':null};
  }
 },undefined,'?view=settings');
 await tick();
 vm.runInContext("managedLibrary=[{id:'r-two',name:'Two',state:'Running'}];managedSnapshots.set('r-two',[{package:'com.example.same',launchable:true}]);library.recent={'com.example.same':100}",context);
 await vm.runInContext("launchApp('com.example.same','r-two')",context);
 assert.equal(calls.length,1);assert.equal(calls[0].runtimeId,'r-two');assert.equal(calls[0].jobId,'job');
 assert.equal(vm.runInContext("sortApps([{package:'com.example.same'},{package:'com.example.same',runtime_id:'r-two'}],'recent')[0].runtime_id",context),'r-two');
 fail=true;await vm.runInContext("launchApp('com.example.same','r-two')",context);
 assert.equal(calls.length,1);
});

test('late preference refresh cannot overwrite a newly saved managed favorite', async()=>{
 const stale=deferred();let waiting=false;
 const record={id:'r-one',name:'One',state:'Stopped'};
 const {context}=gui(async(command,args)=>{
  if(command==='get_library'&&args.runtimeId==='r-one'){waiting=true;return stale.promise;}
  if(command==='set_favorite')return {favorites:['com.example.app'],recent:{}};
  if(command==='runtime_controller')return args.request.op==='list'?[record]:record;
 },undefined,'?view=settings');
 await tick();
 vm.runInContext("managedPreferences.set('r-one',{favorites:[],recent:{}})",context);
 const loading=vm.runInContext('refreshManagedLibrary()',context);
 await tick();assert.equal(waiting,true);
 await vm.runInContext("toggleManagedFavorite('com.example.app','r-one')",context);
 stale.resolve({favorites:[],recent:{}});await loading;
 assert.equal(vm.runInContext("isFavorite({runtime_id:'r-one',package:'com.example.app'})",context),true);
});


test('managed menu shortcut pins target and releases busy state on failure', async () => {
  const calls = [];
  const {context, banner} = gui(async (name,args) => {
    if (name === 'create_app_shortcut') { calls.push(args); return {success:false,message:'Start this runtime'}; }
  });
  await tick();
  await vm.runInContext("appMenuAction('shortcut', 'com.example.app', 'Calculator', null, 'r-22222222222222222222222222222222')", context);
  assert.equal(calls.length,1);
  assert.equal(calls[0].runtimeId,'r-22222222222222222222222222222222');
  assert.equal(calls[0].package,'com.example.app');
  assert.match(banner.textContent,/Start this runtime/);
  assert.equal(vm.runInContext('pendingAppAction',context),false);
});

for (const mode of ['success','partial-failure','transport-error','stopped','renamed','frozen']) {
 test(`APK progress and sequential installs: ${mode}`,async()=>{
  const calls=[];
  const records=[{id:'default',name:'Default',state:'Frozen'},{id:'r-two',name:'Second',state:'Running'}];
  const {context,element}=gui(async(command,args)=>{
   if(command==='install_apk') {
    calls.push(args);
    assert.equal(element('apk-install-bar').value,args.runtimeId==='default'?0:1);
    assert.match(element('apk-install-bar').className,/is-installing/);
    if(mode==='transport-error'&&args.runtimeId==='default')throw Error('Connection lost');
    return {success:!(mode==='partial-failure'&&args.runtimeId==='default'),message:mode==='partial-failure'?'Android install failed: Requested internal only, but not enough space <stack>':'Install result'};
   }
  });await tick();
  context.installFixtures=records;
  vm.runInContext(`refreshBatchRecord=async id=>({...installFixtures.find(record=>record.id===id),...(${JSON.stringify(mode)}==='stopped'&&id==='default'?{state:'Stopped'}:${JSON.stringify(mode)}==='renamed'&&id==='default'?{name:'Changed'}:{})});`,context);
  const results=await context.runApkInstall(records,['default','r-two'],'/tmp/game.apk');
  assert.equal(calls.length,['stopped','renamed'].includes(mode)?1:2);
  assert(calls.every(call=>call.path==='/tmp/game.apk'));
  assert.equal(element('apk-install-bar').max,2);
  assert.equal(element('apk-install-bar').value,2);
  assert.equal(element('apk-install-bar').className,'');
  if(['partial-failure','transport-error'].includes(mode))assert.equal(results[0].status,'failed');
  if(mode==='partial-failure'){assert.equal(element('apk-target-state-default').textContent,'Failed · Not enough Android storage');assert.match(element('apk-install-error-details').innerHTML,/&lt;stack&gt;/);}
  if(['stopped','renamed'].includes(mode))assert.equal(results[0].status,'skipped');
  assert.equal(results[1].status,'completed');
 });
}

test('APK modal opens before loading runtimes even when none are running',async()=>{
 const gate=deferred();const {context,element}=gui(async()=>undefined);await tick();
 context.targetGate=gate.promise;
 vm.runInContext('loadApkTargets=()=>targetGate; runtimeTransition=null;',context);
 const task=context.installApk();
 assert.equal(element('apk-install-dialog').open,true);
 assert.equal(element('apk-install-pick').disabled,true);
 gate.resolve([{id:'default',name:'Default',state:'Stopped'}]);await tick();
 assert.match(element('apk-install-error').textContent,/Start a runtime/);
 assert.match(element('apk-install-targets').innerHTML,/disabled/);
 assert.equal(element('apk-install-submit').disabled,true);
 assert.equal(element('apk-install-pick').disabled,false);
 element('apk-install-cancel').onclick();await task;
 assert.equal(vm.runInContext('pendingAppAction',context),false);
});

test('APK modal selects file and Frozen runtimes, stays open during progress and blocks duplicates',async()=>{
 const gate=deferred();let installs=0;
 const {context,element}=gui(async(command,args)=>{
  if(command==='install_apk'){installs++;return gate.promise;}
 });await tick();
 vm.runInContext(`runtimeTransition=null; loadApkTargets=async()=>[{id:'default',name:'Default',state:'Frozen'}]; refreshBatchRecord=async()=>({id:'default',name:'Default',state:'Frozen'}); refreshApps=async()=>{}; refreshManagedLibrary=async()=>{};`,context);
 const input={value:'default',checked:true,disabled:false};
 element('apk-install-targets').querySelectorAll=()=>[input];
 context.window.__TAURI__.dialog.open=async()=>'/tmp/game.apk';
 const task=context.installApk();await tick();
 assert.equal(element('apk-install-submit').disabled,true);
 await element('apk-install-pick').onclick();
 assert.equal(element('apk-install-file').textContent,'game.apk');
 assert.equal(element('apk-install-submit').disabled,false);
 element('apk-install-form').onsubmit({preventDefault(){}});await tick();
 assert.equal(element('apk-install-dialog').open,true);
 assert.equal(element('apk-install-cancel').disabled,true);
 let prevented=false;element('apk-install-dialog').oncancel({preventDefault(){prevented=true;}});assert(prevented);
 await context.installApk();assert.equal(installs,1);
 gate.resolve({success:true,message:'Installed'});await task;
 assert.match(element('apk-install-progress-label').textContent,/1 completed/);
 assert.equal(element('apk-install-cancel').textContent,'Close');
 assert.equal(element('apk-install-cancel').disabled,false);
 element('apk-install-cancel').onclick();assert.equal(element('apk-install-dialog').open,false);
 assert.equal(element('btn-install-apk').disabled,false);
});

test('APK modal Escape cancels selection and file picker cancellation leaves Install disabled',async()=>{
 const {context,element}=gui(async()=>undefined);await tick();
 const inputs=[{value:'default',checked:true},{value:'second',checked:false},{value:'stopped',checked:false,disabled:true}];
 element('apk-install-targets').querySelectorAll=selector=>selector.endsWith(':checked')?inputs.filter(input=>input.checked):inputs;
 context.window.__TAURI__.dialog.open=async()=>null;
 const choice=context.chooseApkTargets([{id:'default',name:'<Default>',state:'Frozen'},{id:'second',name:'Second',state:'Running'},{id:'stopped',name:'Stopped',state:'Stopped'}]);
 assert.match(element('apk-install-targets').innerHTML,/&lt;Default&gt;/);
 const all=element('apk-install-all');assert.equal(all.indeterminate,true);
 all.checked=true;all.onchange();assert.equal(inputs[1].checked,true);assert.equal(inputs[2].checked,false);assert.equal(all.indeterminate,false);
 all.checked=false;all.onchange();assert(inputs.every(input=>!input.checked));
 await element('apk-install-pick').onclick();assert.equal(element('apk-install-submit').disabled,true);
 element('apk-install-dialog').oncancel({preventDefault(){}});
 assert.equal(await choice,null);
});

test('Runtime page refresh lists every controller runtime alongside legacy without starting any', async () => {
  const calls=[];
  const records=[{id:'r-one',name:'First Android',state:'Running',job:null},{id:'r-two',name:'Second Android',state:'Stopped',job:null}];
  const {context,element}=gui(async (command,args) => {
    calls.push([command,args]);
    if(command==='runtime_controller') {
      if(args.request.op==='list') return records;
      if(args.request.op==='refresh') return {...records.find(r=>r.id===args.request.id)};
      if(args.request.op==='display_info') return {mode:'desktop'};
      return null;
    }
    if(command==='get_health_report') throw new Error('Legacy unavailable');
    throw new Error('Unexpected command '+command);
  }, async()=>true, '?view=settings');
  await tick(); calls.length=0;
  await vm.runInContext('refreshRuntimePage()',context);
  const html=element('controller-runtimes').innerHTML;
  assert.match(html,/First Android/); assert.match(html,/Second Android/);
  assert.match(html,/data-controller-id="r-one"/); assert.match(html,/data-controller-id="r-two"/);
  assert.equal(element('runtime-state').textContent,'Unavailable');
  assert.ok(calls.some(([name,args])=>name==='runtime_controller' && args.request.op==='refresh' && args.request.id==='default'));
  assert.ok(calls.every(([name,args])=>name!=='start_runtime' && args?.request?.op!=='start'));
  assert.equal(element('btn-refresh-runtimes').disabled,false);
  const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/index.html','utf8');
  const runtimePage=source.split('id="page-runtime"')[1].split('id="page-settings"')[0];
  assert.match(runtimePage,/id="controller-runtimes"/);
  assert.match(runtimePage,/id="controller-create"/);
  assert.match(runtimePage,/id="runtime-state"/);
});

test('one unavailable runtime remains visible without hiding other runtime controls', async () => {
  const records=[{id:'r-one',name:'<Offline>',state:'Running',job:null},{id:'r-two',name:'Healthy',state:'Stopped',job:null}];
  const {context,element}=gui(async (command,args)=>{
    if(command!=='runtime_controller') throw new Error('No legacy data');
    if(args.request.op==='list') return records;
    if(args.request.op==='refresh') {
      if(args.request.id==='r-one') throw new Error('Worker unavailable');
      return {...records[1]};
    }
    if(args.request.op==='display_info') return {mode:'desktop'};
    return null;
  }, async()=>true, '?view=settings');
  await tick();
  await vm.runInContext("runtimeSettingsId='r-one'",context);
  await vm.runInContext('refreshController()',context);
  const html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/&lt;Offline&gt;/);
  assert.match(html,/Worker unavailable/);
  assert.match(html,/Healthy/);
  assert.match(html,/data-controller-op="start" data-controller-id="r-two"/);
  assert.doesNotMatch(html,/data-controller-op="start" data-controller-id="r-one"/);
  assert.match(html,/data-controller-op="settings" data-controller-id="r-one"/);
  assert.equal(element('controller-connection').textContent,'Service connected');
});

test('runtime row selection keeps configuration out of the table', async () => {
  const {context,element}=gui(async()=>[],undefined,'?view=controls');await tick();
  vm.runInContext("controllerRecords=[{id:'r-one',name:'One',state:'Running'},{id:'r-two',name:'Two',state:'Stopped'}];renderController()",context);
  assert.doesNotMatch(element('controller-runtimes').innerHTML,/runtime-detail-row|runtime-config-grid/);
  vm.runInContext("selectRuntime('r-two')",context);
  assert.match(element('controller-runtimes').innerHTML,/data-runtime-select="r-two" aria-pressed="true"/);
  assert.equal(element('runtime-settings-content').innerHTML,'');
  assert.equal(vm.runInContext('runtimeSettingsId',context),null);
  assert.equal(element('legacy-runtime-detail').hidden,true);
});

test('delete runtime requires its exact name and keeps its target after selection changes', async () => {
  const calls=[];
  const record={id:'r-two',name:'Second',state:'Stopped',job:null,storage:{path:'/disk/runtime-two'}};
  const {context,element}=gui(async (command,args)=>{
    if(command==='runtime_controller') {
      calls.push(args.request);
      if(args.request.op==='delete') return {id:'delete-job',runtime_id:'r-two',action:'delete',status:'Running'};
      if(args.request.op==='list') return [];
    }
  },async()=>true,'?view=settings');
  await tick();calls.length=0;
  context.fixture=record;
  vm.runInContext("controllerRecords=[fixture];openRuntimeStorage('r-two','delete')",context);
  assert.match(element('runtime-storage-description').textContent,/Permanently delete runtime "Second"/);
  assert.match(element('runtime-storage-description').textContent,/cannot be undone/);
  element('runtime-storage-confirm-name').value='wrong';
  await vm.runInContext('saveRuntimeStorage()',context);
  assert.equal(calls.length,0);
  element('runtime-storage-confirm-name').value='Second';
  vm.runInContext("selectedRuntimeId='default'",context);
  await vm.runInContext('saveRuntimeStorage()',context);
  const removed=calls.find(r=>r.op==='delete');
  assert.equal(removed.id,'r-two');assert.equal(removed.confirmed_name,'Second');
  assert.equal(element('runtime-storage-dialog').open,false);
});

test('delete is disabled while running and incomplete deletion only offers retry', async () => {
  const {context,element}=gui(async command=>{if(command==='runtime_controller')return [];},async()=>true,'?view=settings');
  await tick();
  vm.runInContext("runtimeSettingsId='r-two'",context);
  vm.runInContext("controllerRecords=[{id:'r-two',name:'Second',state:'Running',job:null}];renderController()",context);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-controller-op="delete" data-controller-id="r-two" disabled/);
  vm.runInContext("controllerRecords[0]={id:'r-two',name:'Second',state:'Stopped',deletion_pending:true,job:null,appJob:{action:'delete',status:'Failed',error:'Disk unavailable'}};renderController()",context);
  const html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/Retry delete…/);assert.match(html,/Deletion incomplete/);
  assert.doesNotMatch(html,/data-controller-op="start"/);
});

test('manual refresh and background sync keep stopped runtime snapshots without starting anything', async () => {
  const calls=[];
  const records=[{id:'r-stopped',name:'Stopped runtime',state:'Stopped'},{id:'r-running',name:'Running runtime',state:'Running'}];
  const {context,element}=gui(async (command,args)=>{
    calls.push([command,args]);
    if(command==='get_apps') throw new Error('Default is stopped; keep cached apps');
    if(command==='runtime_controller') {
      const r=args.request;
      if(r.op==='list') return records;
      if(r.op==='refresh') return records.find(record=>record.id===r.id);
      if(r.op==='apps') { assert.equal(r.id,'r-running');return {runtime_id:r.id,apps:[{package:'com.running.app',launchable:true}]}; }
      if(r.op==='display_info')return {mode:'desktop'};
    }
    if(command==='get_library')return {favorites:[],recent:{}};
  },async()=>true,'?view=settings');
  await tick();calls.length=0;
  vm.runInContext("allApps=[{package:'com.cached.default',label:'Default cached'}];managedSnapshots.set('r-stopped',[{package:'com.cached.stopped',launchable:true}]);appLibraryStarted=true",context);
  await vm.runInContext('refreshApps();',context);
  await vm.runInContext('(async()=>{await refreshApps();await loadLibrary();})()',context);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="com.cached.default"/);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package="com.cached.stopped"/);
  assert.equal(vm.runInContext("allApps[0].package",context),'com.cached.default');
  assert.equal(vm.runInContext("managedSnapshots.get('r-stopped')[0].package",context),'com.cached.stopped');
  assert.match(element('apps-grid').innerHTML,/com.running.app/);
  assert.ok(calls.every(([command,args])=>command!=='start_runtime' && command!=='launch_app' && !['start','prepare','app_action'].includes(args?.request?.op)));
  assert.ok(calls.filter(([command,args])=>command==='runtime_controller' && args.request.op==='apps').every(([,args])=>args.request.id==='r-running'));
});

test('Google services check pins runtime, detects partial setup and never starts Android', async () => {
  const calls=[];
  const {context,element}=gui(async (command,args)=>{
    if(command!=='runtime_controller')return;
    calls.push(args.request);
    if(args.request.op==='list')return [];
    if(args.request.op==='google_services')return {runtime_id:'r-two',generation:'one',checked_at:1,packages:{'com.android.vending':true,'com.google.android.gms':false,'com.google.android.gsf':false}};
  },async()=>true,'?view=settings');
  await tick();calls.length=0;
  vm.runInContext("controllerRecords=[{id:'r-two',name:'Second',state:'Running',display:{mode:'desktop'},job:null}]",context);
  await vm.runInContext("checkRuntimeGoogle('r-two')",context);
  assert.equal(calls.length,1);assert.equal(calls[0].op,'google_services');assert.equal(calls[0].id,'r-two');
  let html=vm.runInContext('renderRuntimeGoogle(controllerRecords[0])',context);
  assert.match(html,/components are incomplete/);
  assert.match(html,/data-controller-id="r-two" data-app-action="launch" data-package="com.android.vending" >Open Play Store/);
  vm.runInContext("controllerRecords[0].state='Stopped'",context);
  await vm.runInContext("checkRuntimeGoogle('r-two')",context);
  assert.equal(calls.length,1);
  html=vm.runInContext('renderRuntimeGoogle(controllerRecords[0])',context);
  assert.match(html,/saved result/);assert.match(html,/disabled>Open Play Store/);
});

test('cross-runtime Google result is rejected and does not enable Play Store', async () => {
  const {context}=gui(async(command,args)=>{
    if(command==='runtime_controller' && args.request.op==='list')return [];
    if(command==='runtime_controller' && args.request.op==='google_services')return {runtime_id:'other',generation:'one',checked_at:1,packages:{'com.android.vending':true,'com.google.android.gms':true,'com.google.android.gsf':true}};
  },async()=>true,'?view=settings');
  await tick();
  vm.runInContext("controllerRecords=[{id:'r-two',name:'Second',state:'Running',display:{mode:'desktop'},job:null}]",context);
  await vm.runInContext("checkRuntimeGoogle('r-two')",context);
  const html=vm.runInContext('renderRuntimeGoogle(controllerRecords[0])',context);
  assert.match(html,/Invalid Google services result/);assert.match(html,/disabled>Open Play Store/);
});


test('GAPPS creation pins the ready image pair then automatically prepares and starts it', async()=>{
 const calls=[];const record={id:'r-'+'2'.repeat(32),name:'Play',state:'Allocated',job:null};let created=false;
 const {context,element}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  const r=args.request;calls.push(r);
  if(r.op==='image_select')return {status:'Ready',flavor:'GAPPS',images:{system:{sha256:'system'},vendor:{sha256:'vendor'}}};
  if(r.op==='create_image'){created=true;return {...record};}
  if(r.op==='display_set'||r.op==='display_info')return {mode:'desktop'};
  if(['prepare','start'].includes(r.op)){
   record.state=r.op==='prepare'?'Provisioning':'Starting';record.job={id:r.op,operation:r.op,status:'Running'};return {...record};
  }
  if(r.op==='list')return created?[record]:[];
  if(r.op==='refresh'){
   if(r.id==='default')return {id:'default',name:'Default',state:'Stopped'};
   if(record.job){record.state=record.job.operation==='prepare'?'Prepared':'Running';record.job={...record.job,status:'Succeeded'};}
   return {...record};
  }
 },undefined,'?view=settings');
 await tick();calls.length=0;
 element('controller-name').value='Play';element('controller-image').value='GAPPS';
 vm.runInContext("submitRuntimeCreate({preventDefault(){}})",context);
 assert.equal(calls.length,0);assert.match(element('controller-message').textContent,/Download and verify GAPPS/);
 vm.runInContext("imageDownloadState={status:'Ready',flavor:'GAPPS',available_images:[{library_id:'gapps-one',status:'Ready',flavor:'GAPPS',images:{system:{sha256:'system'},vendor:{sha256:'vendor'}}}],images:{system:{sha256:'system'},vendor:{sha256:'vendor'}}};document.getElementById('controller-image').value='GAPPS';submitRuntimeCreate({preventDefault(){}})",context);
 await tick();await tick();
 const request=calls.find(r=>r.op==='create_image');
 assert.equal(request.flavor,'GAPPS');assert.equal(request.system_sha256,'system');assert.equal(request.vendor_sha256,'vendor');
 assert.deepEqual(calls.filter(r=>['image_select','create_image','display_set','prepare','start','image_download'].includes(r.op)).map(r=>r.op),['image_select','create_image','display_set','prepare','start']);
 assert.equal(calls.find(r=>r.op==='display_set').id,record.id);
 assert.equal(vm.runInContext('controllerRecords[0].state',context),'Running');
});


test('image library shows full identities and distinguishes an exact cached official pair',()=>{
 const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
 vm.runInContext(`imageDownloadState={status:'Ready',flavor:'GAPPS',available_images:[{library_id:'gapps-one',status:'Ready',flavor:'GAPPS',image_bytes:3143479296,images:{system:{filename:'lineage-20.0-20260403-GAPPS-waydroid_x86_64-system.zip',sha256:'system'},vendor:{filename:'lineage-20.0-20260428-MAINLINE-waydroid_x86_64-vendor.zip',sha256:'vendor'}}}]};renderImageCatalog({status:'Ready',report:{checked_at:1,architecture:'x86_64',variants:[{flavor:'GAPPS',download_bytes:1380122641,matching_versions:true,system:{version:'20.0',sha256:'system'},vendor:{version:'20.0',sha256:'vendor'}}]}})`,context);
 let html=element('available-runtime-images').innerHTML;
 for(const text of ['Waydroid GAPPS','lineage-20.0-20260403-GAPPS-waydroid_x86_64-system.zip','lineage-20.0-20260428-MAINLINE-waydroid_x86_64-vendor.zip','SHA-256: <code>system</code>','SHA-256: <code>vendor</code>','2.93 GiB'])assert.ok(html.includes(text),text);
 assert.match(html,/data-image-use="gapps-one"/);
 assert.doesNotMatch(html,/data-download-flavor="GAPPS"/);
 vm.runInContext("imageDownloadState.available_images[0].images.vendor.sha256='other';renderAvailableImages()",context);
 html=element('available-runtime-images').innerHTML;
 assert.match(html,/data-download-flavor="GAPPS"/);
 assert.match(html,/Not downloaded/);
});

test('launch toast replaces previous notice, dismisses automatically and does not occupy the app banner',()=>{
 const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
 const timers=[];const cancelled=[];
 context.setTimeout=(fn,delay)=>{timers.push({fn,delay});return timers.length;};
 context.clearTimeout=id=>cancelled.push(id);
 vm.runInContext("showAppToast('Opening Browser','info');showAppToast('Launch requested: Browser','success')",context);
 assert.equal(element('app-toast').hidden,false);
 assert.equal(timers[1].delay,3500);assert.ok(cancelled.includes(1));
 assert.equal(element('apps-status').textContent,'');
 timers[1].fn();assert.equal(element('app-toast').hidden,true);
 vm.runInContext("showAppToast('<error>','error')",context);
 assert.equal(element('app-toast-message').textContent,'<error>');assert.equal(timers[2].delay,10000);
 vm.runInContext('dismissAppToast()',context);assert.equal(element('app-toast').hidden,true);
});

test('ARM controls distinguish stopped, running, inherited and unavailable runtimes',()=>{
 const {context}=gui(async()=>undefined,undefined,'?view=settings');
 const render = (state,arm,busy=false)=>vm.runInContext(`renderRuntimeArm(${JSON.stringify({id:'r-arm',state,arm})},${busy})`,context);
 const arm={enabled:false,available:true,supported:true,provider:'libndk_translation 0.2.3'};
 assert.match(render('Allocated',{...arm,supported:false,default_enabled:true}),/enabled automatically during Prepare/);
 assert.match(render('Stopped',arm),/data-arm-select="r-arm"/);
 assert.match(render('Stopped',arm),/option value="libndk"[^>]*>libndk_translation/);
 assert.match(render('Stopped',arm),/option value="libhoudini"/);
 assert.doesNotMatch(render('Running',arm),/data-arm-engine="r-arm" disabled/);
 assert.match(render('Running',arm),/Disable translation & restart/);
 assert.match(render('Stopped',arm,true),/data-arm-select="r-arm" disabled/);
 assert.match(render('Stopped',{...arm,enabled:true}),/option value="libndk" selected/);
 assert.match(render('Stopped',{...arm,available:false}),/No ARM archive is cached/);
 assert.doesNotMatch(render('Stopped',{...arm,inherited:true,supported:false}),/data-controller-op="arm_set"/);
});

test('ARM setting pins its runtime without starting it or replacing the runtime record',async()=>{
 const calls=[]; const record={id:'r-arm',name:'ARM runtime',state:'Stopped',job:null};
 const arm={enabled:false,available:true,supported:true,provider:'libndk_translation 0.2.3'};
 const {context}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  const r=args.request; calls.push(r);
  if(r.op==='list')return [record];
  if(r.op==='refresh')return {...record};
  if(r.op==='arm_set'){arm.enabled=r.enabled;return {...arm};}
  if(r.op==='arm_info')return {...arm};
  if(r.op==='display_info')return {mode:'desktop'};
 },undefined,'?view=settings');
 await tick(); calls.length=0;
 await vm.runInContext("mutateController({op:'arm_set',id:'r-arm',enabled:true})",context);
 assert.equal(calls.filter(r=>r.op==='arm_set').length,1);
 assert.equal(calls.find(r=>r.op==='arm_set').id,'r-arm');
 assert.equal(calls.filter(r=>r.op==='start').length,0);
 assert.equal(vm.runInContext('controllerRecords[0].name',context),'ARM runtime');
 assert.equal(vm.runInContext('controllerRecords[0].arm.enabled',context),true);
});

test('Houdini version downloads and installs into the pinned runtime only',async()=>{
 const digest='2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5';
 const source={engine:'libhoudini',version:'14.0.0',sha256:digest,installed:false};
 const calls=[];
 const record={id:'r-arm',name:'ARM test',state:'Stopped',arm:{enabled:false,supported:true,sources:[source]},job:null};
 const {context}=gui(async(command,args)=>{
  calls.push([command,args]);
  if(command==='import_arm_translation')return {sha256:digest};
  if(command==='runtime_controller'){
   const r=args.request;
   if(r.op==='list')return [record];
   if(r.op==='refresh')return {...record};
   if(r.op==='arm_info'||r.op==='arm_set')return {...record.arm};
   if(r.op==='arm_sources')return [source];
  }
 },undefined,'?view=controls');
 vm.runInContext(`controllerRecords=[${JSON.stringify(record)}]`,context);
 context.document.querySelector=selector=>({value:selector.includes('arm-engine')?'libhoudini':digest});
 await vm.runInContext("installRuntimeArm('r-arm')",context);
 const downloaded=calls.find(([command])=>command==='import_arm_translation')[1];
 assert.equal(downloaded.source,digest);assert.equal(downloaded.sourceKind,'builtin');
 const installed=calls.find(([,args])=>args?.request?.op==='arm_set')[1].request;
 assert.equal(installed.id,'r-arm');assert.equal(installed.archive_sha256,digest);
 assert.equal(installed.enabled,true);
 assert.ok(!calls.some(([,args])=>args?.request?.op==='start'));
});

test('Failed Houdini download does not change current runtime translator',async()=>{
 const calls=[];const digest='a'.repeat(64);
 const {context,element}=gui(async(command,args)=>{calls.push([command,args]);if(command==='import_arm_translation')throw new Error('Download interrupted');},undefined,'?view=controls');
 vm.runInContext(`controllerRecords=[{id:'r-arm',name:'Test',state:'Stopped',arm:{supported:true,sources:[{engine:'libhoudini',sha256:'${digest}',version:'custom',installed:false}]}}]`,context);
 context.document.querySelector=selector=>({value:selector.includes('arm-engine')?'libhoudini':digest});
 await vm.runInContext("installRuntimeArm('r-arm')",context);
 assert.match(element('app-toast-message').textContent,/Download interrupted/);
 assert.ok(!calls.some(([,args])=>args?.request?.op==='arm_set'));
 assert.equal(vm.runInContext('armInstallBusy',context),null);
});

test('Settings groups remain visible and retain runtime-specific expansion state',()=>{
 const {context,element}=gui(async()=>undefined,undefined,'?view=controls');
 vm.runInContext("runtimeSettingsId='r-one'",context);
  vm.runInContext("controllerRecords=[{id:'r-one',name:'One',state:'Stopped'},{id:'r-two',name:'Two',state:'Stopped'}];renderController()",context);
 assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-settings-key="r-one:resources" ><summary/);
 vm.runInContext("runtimeSettingsOpen.add('r-one:advanced');runtimeSettingsOpen.add('r-one:resources');renderController()",context);
 assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-settings-key="r-one:resources" open/);
 vm.runInContext("runtimeSettingsId='r-two';renderController()",context);
 assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/data-settings-key="r-two:resources" ><summary/);
});

test('managed Apps render while the existing runtime inventory is still pending', async () => {
 const pending=deferred();
 const record={id:'r-fast',name:'Fast runtime',state:'Running'};
 const {context,element}=gui(async(command,args)=>{
  if(command==='get_apps') return pending.promise;
  if(command==='get_library') return {favorites:[],recent:{},sort:'name-asc'};
  if(command==='runtime_controller') {
   if(args.request.op==='list')return [record];
   if(args.request.op==='refresh')return record;
   if(args.request.op==='apps')return {runtime_id:record.id,apps:[{package:'com.fast.app',label:'Available now',launchable:true}]};
  }
 });
 await tick();
 assert.match(element('apps-grid').innerHTML,/Available now/);
 pending.resolve([]);await tick();
 assert.equal(element('btn-refresh-apps').disabled,false);
});

test('unchanged app inventory preserves the existing card DOM across polling', async () => {
 const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
 await tick();
 const grid=element('apps-grid');let writes=0;let value='';
 Object.defineProperty(grid,'innerHTML',{get:()=>value,set:v=>{writes++;value=v;},configurable:true});
 vm.runInContext("libraryDefaultState='Running';allApps=[{package:'com.test.app',label:'Test'}];renderApps(allApps);renderApps(allApps)",context);
 assert.equal(writes,1);
 vm.runInContext("renderApps([]);renderApps(allApps)",context);
 assert.equal(writes,3);
});

test('managed Uninstall confirms its runtime, routes only there and removes its cached card',async()=>{
 const calls=[];let confirmation='';let installed=true;
 const record={id:'r-one',name:'Test runtime',state:'Running'};
 const app={package:'com.example.game',label:'Game',launchable:true};
 const {context,element}=gui(async(command,args)=>{
  if(command==='get_library')return {favorites:[],recent:{}};
  if(command!=='runtime_controller')return;
  const r=args.request;calls.push(r);
  if(r.op==='list')return [record];
  if(r.op==='refresh')return record;
  if(r.op==='display_info')return {mode:'desktop'};
  if(r.op==='apps')return {runtime_id:record.id,apps:installed?[app]:[]};
  if(r.op==='app_action') {installed=false;return {id:'job',runtime_id:r.id,package:r.package,action:r.action,status:'Succeeded',result:{message:'App uninstalled'}};}
 },async text=>{confirmation=text;return true;},'?view=settings');
 await tick();
 vm.runInContext(`managedLibrary=[${JSON.stringify(record)}];managedSnapshots.set('r-one',[${JSON.stringify(app)}]);managedSnapshots.set('r-other',[${JSON.stringify(app)}]);`,context);
 await vm.runInContext("appMenuAction('uninstall','com.example.game','Game',null,'r-one')",context);
 assert.match(confirmation,/Test runtime/);assert.match(confirmation,/removes its data/);
 assert.deepEqual(calls.filter(r=>r.op==='app_action').map(r=>[r.id,r.action,r.package]),[['r-one','uninstall','com.example.game']]);
 assert.equal(vm.runInContext("managedSnapshots.get('r-one').length",context),0);
 assert.equal(vm.runInContext("managedSnapshots.get('r-other').length",context),1);
 assert.equal(vm.runInContext('pendingAppAction',context),false);
});

test('cancelled managed Uninstall sends no backend request',async()=>{
 const calls=[];
 const {context}=gui(async(command,args)=>{calls.push([command,args]);},async()=>false,'?view=settings');
 await tick();calls.length=0;
 vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'}];managedSnapshots.set('r-one',[{package:'com.example.game',launchable:true}])",context);
 await vm.runInContext("managedAppAction('uninstall','com.example.game','r-one')",context);
 assert.equal(calls.length,0);assert.equal(vm.runInContext('pendingAppAction',context),false);
});

test('GPU check stays in the GPU panel without expanding health diagnostics', async () => {
  const pending=deferred(); const calls=[];
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller' && args.request.op==='health') { calls.push(args.request); return pending.promise; }
  },undefined,'?view=settings');
  await tick();
  vm.runInContext("runtimeSettingsId='r-gpu'",context);
  vm.runInContext("controllerRecords=[{id:'r-gpu',name:'GPU',state:'Running'}]",context);
  const check=vm.runInContext("checkRuntimeGraphics('r-gpu')",context);
  await vm.runInContext("checkRuntimeGraphics('r-gpu')",context);
  assert.equal(calls.length,1);
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/Checking GPU and Android renderer/);
  assert.equal(vm.runInContext('runtimeHealth.size',context),0);
  pending.resolve({runtime_id:'r-gpu',checked_at:1000,graphics:{gles:'AMD <GPU>'},checks:[]});
  await check;
  assert.match((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),/AMD &lt;GPU&gt;/);
  assert.equal(vm.runInContext('runtimeHealth.size',context),0);
});

test('CPU graphics choice and GPU-first automatic fallback support save and restart', () => {
  const {context} = gui(async () => ({}));
  const render = (supported, state) => vm.runInContext(`renderGpuControls({id:'r-cpu',state:'${state}',gpu:{selection:'software',software_supported:${supported},devices:[]}},false)`, context);
  assert.match(render(true, 'Stopped'), /value="software" selected>CPU/);
  assert.match(render(true, 'Stopped'), /value="auto"/);
  assert.doesNotMatch(render(true, 'Stopped'), /software.*unavailable/);
  assert.doesNotMatch(render(true, 'Running'), /data-controller-op="gpu_set"[^>]*disabled/);
  assert.match(render(true, 'Running'), /Save & restart/);
  assert.match(render(true, 'Stopped'), /Selects compatible hardware automatically/);
  assert.doesNotMatch(render(false, 'Stopped'), />CPU · software rendering<\/option>/);
});

test('GPU selection keeps draft across renders and permits save with restart while running', async () => {
  const {context,element}=gui(async()=>{},undefined,'?view=settings'); await tick();
  vm.runInContext("runtimeSettingsId='r-gpu'",context);
 vm.runInContext("controllerRecords=[{id:'r-gpu',name:'GPU',state:'Stopped',gpu:{selection:'auto',devices:[{node:'/dev/dri/renderD129',driver:'amdgpu',selectable:true}]}}]; runtimeGpuDraft.set('r-gpu','/dev/dri/renderD129'); renderController()",context);
  let html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.match(html,/value="\/dev\/dri\/renderD129" selected/);
  assert.doesNotMatch(html,/data-controller-op="gpu_set"[^>]*disabled/);
  vm.runInContext("controllerRecords[0].state='Running'; renderController()",context);
  html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
  assert.doesNotMatch(html,/data-controller-op="gpu_set"[^>]*disabled/);
  assert.doesNotMatch(html,/data-gpu-select="r-gpu" disabled/);
  assert.match(html,/Save & restart/);
});

test('runtime polling does not replace a focused GPU select, including an in-flight response', async () => {
  const pending = deferred();
  let polling = false;
  const record = {id:'r-gpu',name:'GPU',state:'Stopped'};
  const {context,element}=gui(async(command,args)=>{
    if(command !== 'runtime_controller') return;
    if(args.request.op === 'list') return polling ? pending.promise : [];
    if(args.request.op === 'refresh') return {...record,name:'Updated GPU runtime'};
    if(args.request.op === 'gpu_info') return {selection:'auto',devices:[]};
  },undefined,'?view=settings');
  await tick();
  vm.runInContext(`controllerRecords=[${JSON.stringify(record)}]; renderController()`,context);
  const panel=element('controller-runtimes');
  let html=panel.innerHTML, writes=0;
  Object.defineProperty(panel,'innerHTML',{get:()=>html,set:value=>{html=value;writes++;}});
  polling = true;
  const refresh=vm.runInContext('refreshController()',context);
  // The user opens the popup after polling has already begun.
  context.document.activeElement={dataset:{gpuSelect:'r-gpu'}};
  pending.resolve([record]);
  await refresh;
  assert.equal(writes,0);
  assert.doesNotMatch(html,/Updated GPU runtime/);
  assert.equal(vm.runInContext('controllerRecords[0].name',context),'Updated GPU runtime');
  vm.runInContext("runtimeGpuDraft.set('r-gpu','auto'); renderController()",context);
  assert.equal(writes,0);
  context.document.activeElement=null;
  vm.runInContext('renderController()',context);
  assert.equal(writes,1);
  assert.match(html,/Updated GPU runtime/);
});

test('a failed runtime poll also preserves the focused native GPU popup', async () => {
  const {context,element}=gui(async()=>{throw new Error('Controller offline');},undefined,'?view=settings');
  await tick();
  vm.runInContext("controllerRecords=[{id:'r-gpu',name:'GPU',state:'Stopped'}]; renderController()",context);
  const panel=element('controller-runtimes'); const original=panel.innerHTML;
  context.document.activeElement={dataset:{gpuSelect:'r-gpu'}};
  await vm.runInContext('refreshController()',context);
  assert.equal(panel.innerHTML,original);
  assert.match(element('controller-message').textContent,/Controller offline/);
  context.document.activeElement=null;
  vm.runInContext('renderController()',context);
  assert.match(panel.innerHTML,/No additional runtimes/);
});

test('GPU verification separates observed compositor usage from saved configuration', async()=>{
  const {context}=gui(async()=>{},undefined,'?view=settings');await tick();
  vm.runInContext("runtimeGraphics.set('r-gpu',{report:{checked_at:1000,gpu_selection:'/dev/dri/renderD129',gpu_node:'/dev/dri/renderD129',graphics:{gles:'Intel Mesa',android_node:'/dev/dri/renderD129',compositor_nodes:['/dev/dri/renderD129']},gpu_verification:{status:'verified',detail:'Compositor only <not game proof>'}}})",context);
  let html=vm.runInContext("renderRuntimeGraphics('r-gpu')",context);
  assert.match(html,/GPU verified · Android compositor/);
  assert.match(html,/Opened by compositor: \/dev\/dri\/renderD129/);
  assert.match(html,/&lt;not game proof&gt;/);
  for(const [status,label] of [['software','Software rendering · CPU'],['mismatch','GPU mismatch'],['unknown','GPU usage not verified']]){
    vm.runInContext(`runtimeGraphics.get('r-gpu').report.gpu_verification.status='${status}'`,context);
    html=vm.runInContext("renderRuntimeGraphics('r-gpu')",context);
    assert.ok(html.includes(label));assert.doesNotMatch(html,/GPU verified · Android compositor/);
  }
});

test('GPU verification snapshots expire after a lifecycle or GPU selection change',async()=>{
  const {context}=gui(async()=>{},undefined,'?view=settings');await tick();
  for(const change of ["state:'Stopped'","job:{id:'new'}","gpu:{selection:'/dev/dri/renderD129'}"]){
    vm.runInContext(`runtimeGraphics.set('r-gpu',{report:{}});runtimeHealth.set('r-gpu',{report:{}});
      invalidateRuntimeGraphics({id:'r-gpu',state:'Running',job:{id:'old'},gpu:{selection:'auto'}},
        {id:'r-gpu',state:'Running',job:{id:'old'},gpu:{selection:'auto'},${change}})`,context);
    assert.equal(vm.runInContext("runtimeGraphics.has('r-gpu') || runtimeHealth.has('r-gpu')",context),false);
  }
});

test('NVIDIA options are disabled and explicitly experimental',async()=>{
 const {context}=gui(async()=>{},undefined,'?view=settings');await tick();
 const html=vm.runInContext(`renderGpuControls({id:'r-gpu',state:'Stopped',gpu:{selection:'auto',devices:[{node:'/dev/dri/renderD130',driver:'nvidia',selectable:false,reason:'NVIDIA · Experimental — not available yet'}]}},false)`,context);
 assert.match(html,/value="\/dev\/dri\/renderD130"[^>]*disabled/);
 assert.match(html,/NVIDIA · Experimental — not available yet/);
});

test('resource limits distinguish saved settings from kernel readback and survive focus',async()=>{
 const {context,element}=gui(async()=>{},undefined,'?view=settings');await tick();
 vm.runInContext("runtimeSettingsId='r-limits'",context);
 vm.runInContext(`controllerRecords=[{id:'r-limits',name:'Limits',state:'Stopped',resources:{configured:{memory_mib:4096,cpu_count:2},host:{supported:true,memory_total_mib:8192,memory_available_mib:4096,cpu_count:4},effective:null}}];renderController()`,context);
 let html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
 assert.match(html,/value="4096" data-resource-id/);
 assert.match(html,/Active limits are measured after Start/);
 assert.doesNotMatch(html,/data-controller-op="resources_set"[^>]*disabled/);
 context.document.activeElement={dataset:{resourceField:'memory_mib'}};
 vm.runInContext("controllerRecords[0].name='Updated';renderController()",context);
 assert.equal((element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML),html);
 context.document.activeElement=null;
 vm.runInContext("controllerRecords[0].state='Running';controllerRecords[0].resources.effective={memory_max_bytes:4294967296,memory_current_bytes:1073741824,cpu_quota_count:2,cpu_throttled_usec:250000,swap_current_bytes:0,swap_max_bytes:0,oom_kills:0};renderController()",context);
 html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
 assert.match(html,/Active limits · kernel readback/);
 assert.match(html,/1024 MiB used \/ 4096 MiB/);
 assert.doesNotMatch(html,/data-controller-op="resources_set"[^>]*disabled/);
 assert.match(html,/Save & restart/);
});

test('resource presets update custom fields without replacing the focused panel',async()=>{
 const {context,element}=gui(async()=>{},undefined,'?view=settings');await tick();
 vm.runInContext("runtimeSettingsId='r-limits'",context);
  vm.runInContext(`controllerRecords=[{id:'r-limits',name:'Limits',state:'Stopped',resources:{configured:{memory_mib:0,cpu_count:0},host:{supported:true,memory_total_mib:16384,memory_available_mib:8192,cpu_count:8},presets:[{name:'Light',memory_mib:2048,cpu_count:2},{name:'Balanced',memory_mib:4096,cpu_count:4},{name:'Unlimited',memory_mib:0,cpu_count:0}],budget:{active_count:0,memory_limit_mib:0,cpu_quota_count:0,available_for_growth_mib:7000,unused_memory_mib:0,target_current_mib:0,host_reserve_mib:1024,unbounded_memory_count:0,unbounded_cpu_count:0,unknown_count:0}}}];renderController()`,context);
 let html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
 assert.match(html,/Balanced · 4096 MiB \/ 4 CPU/);
 vm.runInContext("applyResourcePreset('r-limits','Balanced')",context);
 assert.equal(vm.runInContext("runtimeResourceDraft.get('r-limits').memory_mib",context),4096);
 assert.equal(vm.runInContext("runtimeResourceDraft.get('r-limits').cpu_count",context),4);
 html=(element('controller-runtimes').innerHTML + element('runtime-settings-content').innerHTML);
 assert.match(html,/RAM limits 0 MiB/);
});

test('runtime summary shows RAM CPU and disk usage without opening details',async()=>{
 const {context}=gui(async()=>{},undefined,'?view=settings');await tick();
 vm.runInContext(`controllerRecords=[{id:'r-summary',name:'Summary',state:'Running',resources:{configured:{memory_mib:4096,cpu_count:2},effective:{memory_current_bytes:2147483648,memory_max_bytes:4294967296,cpu_quota_count:2}},storage:{used_bytes:1073741824,limit_bytes:10737418240}}];renderController()`,context);
 const html=vm.runInContext("document.getElementById('controller-runtimes').innerHTML",context);
 assert.match(html,/2\.0 \/ 4\.0 GiB/);
 assert.match(html,/aria-label="Memory"[^>]*aria-valuenow="50.0"/);
 assert.match(html,/runtime-usage-label">CPU/);
 assert.match(html,/1\.0 \/ 10\.0 GiB/);
 assert.match(html,/Runtime allocated data and private images/);
});

test('runtime summary labels configured and unavailable resource states',async()=>{
 const {context}=gui(async()=>{},undefined,'?view=settings');await tick();
 vm.runInContext(`controllerRecords=[{id:'r-summary',name:'Summary',state:'Stopped',resources:{configured:{memory_mib:4096,cpu_count:2}},storage:{}}];renderController()`,context);
 let html=vm.runInContext("document.getElementById('controller-runtimes').innerHTML",context);
 assert.match(html,/runtime-usage-track unavailable/);assert.doesNotMatch(html,/aria-valuenow=/);assert.doesNotMatch(html,/4096 MiB limit/);
 vm.runInContext("controllerRecords[0].resources=null;renderController()",context);
 html=vm.runInContext("document.getElementById('controller-runtimes').innerHTML",context);
 assert.match(html,/runtime-usage-value">—/);assert.doesNotMatch(html,/aria-valuenow=/);
});

test('missing Wayland is optional for headless setup and capability is shown',async()=>{
 const setup={can_prepare:true,can_start:true,checks:[{id:'waydroid',label:'Waydroid',available:true,detail:'Installed'},{id:'desktop',label:'Desktop session',available:false,detail:'No Wayland socket'}],desktop:{desktop:false,session:'none',detail:'No Wayland socket is available; headless mode can still run.'}};
 const {context,element}=gui(async()=>{},undefined,'?view=settings',setup); await tick();
 vm.runInContext('controllerRequirements='+JSON.stringify(setup)+';renderControllerSetup()',context);
 const html=element('controller-setup').innerHTML;
 assert.doesNotMatch(html,/Setup required/);
 assert.match(html,/Desktop preview unavailable/);
 assert.match(html,/Headless mode remains available/);
});

test('Wayland capability reports unverified decoration instead of claiming KDE',async()=>{
 const setup={can_prepare:true,can_start:true,checks:[],desktop:{desktop:true,session:'wayland',decoration:'unverified',placement:'compositor-managed'}};
 const {context,element}=gui(async()=>{},undefined,'?view=settings',setup); await tick();
 vm.runInContext('controllerRequirements='+JSON.stringify(setup)+';renderControllerSetup()',context);
 const html=element('controller-setup').innerHTML;
 assert.match(html,/decoration unverified/); assert.match(html,/placement compositor-managed/);
 assert.doesNotMatch(html,/KDE required/);
 assert.match(html,/Experimental/);
 assert.match(html,/Socket presence does not verify window controls/);
});

test('desktop capability explanation is visible and escaped',async()=>{
 const setup={can_prepare:true,can_start:true,checks:[],desktop:{desktop:true,session:'wayland',decoration:'unverified',placement:'compositor-managed',detail:'SSD or CSD prerequisites <probe>'}};
 const {context,element}=gui(async()=>{},undefined,'?view=settings',setup); await tick();
 vm.runInContext('controllerRequirements='+JSON.stringify(setup)+';renderControllerSetup()',context);
 const html=element('controller-setup').innerHTML;
 assert.match(html,/SSD or CSD prerequisites &lt;probe&gt;/);
 assert.doesNotMatch(html,/<probe>/);
});

test('idle runtimes stop polling; only unfinished operations need polling', async () => {
 const {context}=gui(async()=>undefined,async()=>true,'?view=settings');
 await tick();
 for (const state of ['Running','Stopped','Prepared','Allocated','Error']) {
  vm.runInContext(`controllerRecords=[{state:'${state}',job:{status:'Succeeded'}}]`,context);
  assert.equal(vm.runInContext('controllerHasActiveWork()',context),false);
 }
 for (const record of [{state:'Starting'},{state:'Provisioning'},{state:'Stopping'},
   {state:'Running',appJob:{status:'Running'}},{state:'Stopped',storage:{job:{status:'Running'}}}]) {
  vm.runInContext(`controllerRecords=[${JSON.stringify(record)}]`,context);
  assert.equal(vm.runInContext('controllerHasActiveWork()',context),true);
 }
});

test('unchanged runtime rendering preserves DOM instead of resetting details', async () => {
 const {context,element}=gui(async()=>undefined,async()=>true,'?view=settings');
 await tick();
 vm.runInContext("controllerRecords=[{id:'r-stable',name:'Stable',state:'Stopped'}];renderController()",context);
 const panel=element('controller-runtimes');
 let writes=0,value=panel.innerHTML;
 Object.defineProperty(panel,'innerHTML',{get:()=>value,set:v=>{writes++;value=v;}});
 vm.runInContext('renderController();renderController()',context);
 assert.equal(writes,0);
 vm.runInContext("controllerRecords[0].state='Starting';renderController()",context);
 assert.equal(writes,1);
});

test('app inventory has no recurring or focus-triggered refresh registration', () => {
 const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
 assert.doesNotMatch(source,/setInterval\?\.\(syncLibrary/);
 assert.doesNotMatch(source,/addEventListener\('focus', syncLibrary\)/);
});

test('missing waydroid binary: confirm dialog gates the bundled installer, then polling completes', async () => {
  const calls = [];
  let confirmations = 0;
  let statusCalls = 0;
  const { context, element } = gui(async (command, args) => {
    calls.push([command, args]);
    if (command === 'runtime_controller' && args?.request?.op === 'list') return [];
    if (command === 'runtime_controller') return undefined;
    if (command === 'setup_waydroid') return { ok: true, message: 'Waydroid initialization started in the background (VANILLA).' };
    if (command === 'waydroid_init_status') { statusCalls++; return statusCalls === 1 ? { state: 'none' } : { state: 'done', detail: 'Waydroid initialized.' }; }
    return undefined;
  }, async () => { confirmations++; return true; }, '?view=runtime',
     { can_prepare: false, can_start: false, checks: [{ id: 'waydroid', label: 'Waydroid', available: false, detail: 'Install Waydroid for your Linux distribution first.' }] });
  await vm.runInContext('refreshController()', context);
  assert.equal(element('btn-init-waydroid').textContent, 'Set up Default runtime');
  assert.equal(element('waydroid-setup-actions').hidden, false);
  await vm.runInContext('initializeWaydroid()', context);
  assert.equal(confirmations, 1);
  const setupCall = calls.find(([command]) => command === 'setup_waydroid');
  assert.ok(setupCall);
  assert.equal(setupCall[1].flavor, 'VANILLA');
  await tick(); await tick();
  assert.equal(element('waydroid-init-status').textContent, '');
  assert.equal(element('app-toast-message').textContent, 'Waydroid initialized.');
});

test('missing support files uses the selected library image after confirmation', async () => {
  const calls = [];
  let confirmations = 0;
  const { context, element } = gui(async (command, args) => {
    calls.push([command, args]);
    if (command === 'runtime_controller' && args?.request?.op === 'list') return [];
    if (command === 'setup_waydroid') return { ok: true, message: 'Waydroid initialization started in the background (GAPPS).' };
    if (command === 'waydroid_init_status') return { state: 'none' };
    return undefined;
  }, async () => { confirmations++; return true; }, '?view=runtime',
     { can_prepare: false, can_start: false, checks: [{ id: 'support', label: 'Waydroid setup', available: false, detail: 'Complete Waydroid initialization before starting an additional runtime.' }] });
  await vm.runInContext('refreshController()', context);
  assert.equal(element('btn-init-waydroid').textContent, 'Choose image');
  vm.runInContext("imageDownloadState={status:'Ready',available_images:[{library_id:'a'.repeat(64),flavor:'GAPPS'}]};updateWaydroidSetupActions()",context);
  element('waydroid-init-flavor').value = 'a'.repeat(64);
  await vm.runInContext('initializeWaydroid()', context);
  assert.equal(confirmations, 1);
  const setupCall = calls.find(([command]) => command === 'setup_waydroid');
  assert.ok(setupCall);
  assert.equal(setupCall[1].flavor, 'GAPPS');
  assert.equal(setupCall[1].imageId, 'a'.repeat(64));
});

test('initialization already in progress: click resumes polling without invoking setup again', async () => {
  const calls = [];
  const { context } = gui(async (command, args) => {
    calls.push([command, args]);
    if (command === 'runtime_controller' && args?.request?.op === 'list') return [];
    if (command === 'waydroid_init_status') return { state: 'initializing', detail: 'Downloading system image…' };
    return undefined;
  }, async () => true, '?view=runtime',
     { can_prepare: false, can_start: false, checks: [{ id: 'support', label: 'Waydroid setup', available: false, detail: 'x' }] });
  await vm.runInContext('refreshController()', context);
  await vm.runInContext('initializeWaydroid()', context);
  assert.ok(!calls.some(([command]) => command === 'setup_waydroid'));
  assert.equal(vm.runInContext('waydroidInitPolling', context), true);
});

test('X11 session renders its native backend in the desktop line', async () => {
  const setup={can_prepare:true,can_start:true,checks:[],desktop:{desktop:true,session:'x11',backend:'native-x11',decoration:'wm-managed',placement:'wm-managed',detail:'X11 display :0 detected; AnvilDroid uses a native host window.'}};
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller')return args.request.op==='list'?[]:undefined;
  },async()=>true,'?view=runtime',setup);
  await vm.runInContext('refreshController()',context);
  assert.match(element('controller-setup').innerHTML,/X11/);
  assert.match(element('controller-setup').innerHTML,/wm-managed/);
});

test('missing runtime tools expose in-app repair even when Waydroid is initialized', async () => {
  let confirmations = 0;
  const calls = [];
  const {context, element} = gui(async (command, args) => {
    calls.push([command, args]);
    if (command === 'runtime_controller' && args?.request?.op === 'list') return [];
    if (command === 'waydroid_init_status') return {state:'none'};
    if (command === 'setup_waydroid') return {ok:false, message:'Cancelled'};
  }, async () => { confirmations++; return true; }, '?view=runtime',
  {can_prepare:true, can_start:false, checks:[{id:'waydroid', available:true}, {id:'support', available:true}, {id:'tools', available:false, detail:'Missing: weston'}]});
  await vm.runInContext('refreshController()', context);
  assert.equal(element('waydroid-setup-actions').hidden, false);
  assert.equal(element('btn-init-waydroid').textContent, 'Set up Default runtime');
  await vm.runInContext('initializeWaydroid()', context);
  assert.equal(confirmations, 1);
  assert.ok(calls.some(([c]) => c === 'setup_waydroid'));
});


test('library view toggles presentation and persists without invoking Android', () => {
  const {context, element, storage} = gui(async () => undefined, undefined, '?view=controls');
  assert.equal(element('apps-grid').dataset.view, 'grid');
  element('view-list').listeners.click();
  assert.equal(element('apps-grid').classList.contains('list-view'), true);
  assert.equal(element('view-list').attributes['aria-pressed'], 'true');
  assert.equal(element('view-grid').attributes['aria-pressed'], 'false');
  assert.equal(storage.get('anvildroid.library-view'), 'list');
  vm.runInContext("localStorage.setItem = () => { throw new Error('blocked'); }; applyLibraryView('grid')", context);
  assert.equal(element('apps-grid').dataset.view, 'grid');
});

test('library runtime status reports observed states and escapes failures', () => {
  const {context, element} = gui(async () => undefined, undefined, '?view=controls');
  vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'},{id:'r-two',name:'Two',state:'Starting'},{id:'r-three',name:'<Three>',state:'Running',error:'<offline>'}];managedSnapshots.set('r-one',[{package:'com.example',launchable:true}]);renderManagedLibraryStatus()", context);
  const html=element('library-runtime-status').innerHTML;
  assert.match(html,/<strong>One<\/strong> · Running · 1 apps/);
  assert.match(html,/<strong>Two<\/strong> · Starting · Select this runtime to start it/);
  assert.match(html,/&lt;Three&gt;.*&lt;offline&gt;/);
  vm.runInContext("managedLibraryError='Cannot load <registry>';renderManagedLibraryStatus()",context);
  assert.match(element('library-runtime-status').innerHTML,/Cannot load &lt;registry&gt;/);
});

test('sidebar keyboard activation navigates and identifies the current page', () => {
  const {element} = gui(async () => undefined, undefined, '?view=controls');
  let prevented = false;
  element('nav-controls').listeners.keydown({key:' ', preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(element('page-controls').classList.contains('active'), true);
  assert.equal(element('nav-controls').attributes['aria-current'], 'page');
});

test('runtime usage computes CPU delta, falls back to host memory and pauses while hidden', async()=>{
  const calls=[];
  let memoryLimit=4294967296,hostMemory=16384;
  const {context,element}=gui(async(command,args)=>{
    if(command!=='runtime_controller')return;
    calls.push(args.request);
    if(args.request.op==='resources_info')return {host:{cpu_count:4,memory_total_mib:hostMemory},effective:{scope:'sample',cpu_usage_usec:3000000,memory_current_bytes:1073741824,memory_max_bytes:memoryLimit}};
    if(args.request.op==='storage_info')return {used_bytes:1073741824,free_bytes:9663676416};
  },undefined,'?view=settings',undefined,true);
  await tick();calls.length=0;
  element('page-runtime').classList.add('active');
  const cell={dataset:{runtimeUsage:'r-two'},innerHTML:''};
  context.document.querySelectorAll=selector=>selector==='[data-runtime-usage]'?[cell]:[];
  context.seedSample=record=>context.RuntimeUsage.sample(record,performance.now()-1000);
  vm.runInContext("existingRuntime=null;controllerRecords=[{id:'r-two',state:'Running',resources:{host:{cpu_count:4},effective:{scope:'sample',cpu_usage_usec:1000000}}}];seedSample(controllerRecords[0])",context);
  await vm.runInContext('refreshRuntimeUsage()',context);
  assert.ok(calls.every(r=>r.id==='r-two'));
  const cpu=Number(cell.innerHTML.match(/aria-label="CPU"[^>]*aria-valuenow="([\d.]+)"/)[1]);
  assert.ok(cpu>40&&cpu<60);
  assert.match(cell.innerHTML,/1.0 \/ 4.0 GiB/);
  assert.match(cell.innerHTML,/aria-label="Disk"[^>]*aria-valuenow="10.0"/);
  calls.length=0;
  memoryLimit=null;
  await vm.runInContext('refreshRuntimeUsage()',context);
  assert.deepEqual(calls.map(r=>r.op),['resources_info','storage_info']);
  assert.match(cell.innerHTML,/1.0 \/ 16.0 GiB/);
  assert.match(cell.innerHTML,/Runtime memory \/ host RAM/);
  hostMemory=undefined;
  await vm.runInContext('refreshRuntimeUsage()',context);
  assert.match(cell.innerHTML,/runtime-usage-value">1.0 GiB/);
  assert.doesNotMatch(cell.innerHTML,/1.0 \/ 16.0 GiB/);
  context.document.hidden=true;calls.length=0;
  await vm.runInContext('refreshRuntimeUsage()',context);
  assert.equal(calls.length,0);
});

test('tag select filters custom tags and resets selection after deletion without duplicated options',()=>{
  const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
  vm.runInContext("libraryDefaultState='Running';appTags={tags:['Work'],assignments:{'com.test.one':['Work']}};allApps=[{package:'com.test.one',label:'One'},{package:'com.test.two',label:'Two'}];renderTagFilters();renderTagFilters()",context);
  assert.equal((element('filter-tag').innerHTML.match(/value="tag:Work"/g)||[]).length,1);
  element('filter-tag').value='tag:Work';
  element('filter-tag').listeners.change();
  assert.match(element('apps-grid').innerHTML,/com.test.one/);
  assert.doesNotMatch(element('apps-grid').innerHTML,/com.test.two/);
  vm.runInContext("appTags.tags=[];renderTagFilters();renderApps(allApps)",context);
  assert.equal(element('filter-tag').value,'all');
  assert.match(element('apps-grid').innerHTML,/com.test.two/);
});

test('Google registration always shows copy control and only copies decimal GSF ID', async () => {
  const {context, element} = gui(async () => undefined, undefined, '?view=controls');
  const html = vm.runInContext("renderRuntimeGoogle({id:'r-two',state:'Running'})", context);
  assert.match(html, /GSF Android ID/);
  assert.match(html, /copy_android_id[^>]*disabled/);
  vm.runInContext("runtimeGoogle.set('r-two',{report:{gsf_android_id:'1234567890123456789',android_id:'abcdef',checked_at:1,packages:{'com.android.vending':true,'com.google.android.gms':true,'com.google.android.gsf':true}}})", context);
  const ready = vm.runInContext("renderRuntimeGoogle({id:'r-two',state:'Running'})", context);
  assert.match(ready, /1234567890123456789/);
  assert.doesNotMatch(ready, /settings get secure|abcdef|copy_android_id[^>]*disabled/);
  let copied;
  context.navigator = {clipboard:{writeText: async value => {copied=value;}}};
  element('runtime-table').listeners.click({target:{closest:()=>({dataset:{controllerOp:'copy_android_id',controllerId:'r-two'}})}});
  await tick();
  assert.equal(copied, '1234567890123456789');
});

test('Apps registration notice is running-runtime scoped and opens its ID dialog', async () => {
  const calls=[];
  const {context,element}=gui(async(command,args)=>{
    calls.push([command,args]);
    if(command==='runtime_controller' && args.request.op==='google_services') return {
      runtime_id:args.request.id,generation:'one',checked_at:1,gsf_android_id:'1234567890123456789',
      packages:{'com.android.vending':true,'com.google.android.gms':true,'com.google.android.gsf':true}};
  },undefined,'?view=controls');
  vm.runInContext("managedLibrary=[{id:'r-one',name:'One <test>',state:'Running',image_selection:{flavor:'GAPPS'}},{id:'r-two',name:'Two',state:'Stopped',image_selection:{flavor:'GAPPS'}},{id:'r-three',name:'Vanilla',state:'Running'}];renderGoogleNotices()",context);
  let html=element('library-google-notices').innerHTML;
  assert.match(html,/One &lt;test&gt;/);
  assert.match(html,/Registration not verified/);
  assert.doesNotMatch(html,/data-google-registration="r-two"|data-google-registration="r-three"/);
  await element('library-google-notices').listeners.click({target:{closest:()=>({dataset:{googleRegistration:'r-one'}})}});
  assert.equal(element('google-registration-dialog').open,true);
  assert.match(element('google-registration-content').innerHTML,/1234567890123456789/);
  assert.match(element('google-registration-content').innerHTML,/Open Google registration/);
  assert.equal(calls.filter(([cmd,args])=>cmd==='runtime_controller' && args.request.op==='google_services')[0][1].request.id,'r-one');
  element('google-registration-content').listeners.click({target:{closest:()=>({dataset:{controllerOp:'google_register',controllerId:'r-one'}})}});
  await tick();
  assert.equal(calls.filter(([cmd])=>cmd==='open_google_device_registration').length,1);
  vm.runInContext("setLibraryRuntimeFilter(['r-three']);renderGoogleNotices()",context);
  assert.equal(element('library-google-notices').innerHTML,'');
});

test('Google check reports component results without claiming registration verified', async () => {
  let failed=false;
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller' && args.request.op==='google_services') {
      if(failed) throw new Error('worker unavailable');
      return {runtime_id:'r-one',generation:'one',checked_at:1,gsf_android_id:'1234567890123456789',packages:{'com.android.vending':true,'com.google.android.gms':true,'com.google.android.gsf':true}};
    }
  },undefined,'?view=controls');
  vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'}];googleRegistrationTarget='r-one'",context);
  element('google-registration-dialog').scrollTop=120;
  await vm.runInContext("checkRuntimeGoogle('r-one')",context);
  const html=element('google-registration-content').innerHTML;
  assert.match(html,/Check complete/);
  assert.match(html,/3\/3 Google components installed/);
  assert.match(html,/GSF Android ID read successfully/);
  assert.match(html,/registration status cannot be verified/);
  assert.match(html,/<summary>Already registered\?/);
  assert.equal(element('google-registration-dialog').scrollTop,120);
  failed=true;
  await vm.runInContext("checkRuntimeGoogle('r-one')",context);
  assert.match(element('google-registration-content').innerHTML,/worker unavailable/);
  assert.doesNotMatch(element('google-registration-content').innerHTML,/Check complete/);
});

test('Google Play reminder dismissal persists per runtime and can be restored', () => {
  const {context,element,storage}=gui(async()=>undefined,undefined,'?view=controls');
  vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running',image_selection:{flavor:'GAPPS'}},{id:'r-new',name:'One',state:'Running',image_selection:{flavor:'GAPPS'}}];googleRegistrationTarget='r-one';runtimeGoogle.set('r-one',{report:{gsf_android_id:'123'}});setGooglePlayWorks('r-one',true)",context);
  assert.doesNotMatch(element('library-google-notices').innerHTML,/data-google-registration="r-one"/);
  assert.match(element('library-google-notices').innerHTML,/data-google-registration="r-new"/);
  assert.match(element('google-registration-content').innerHTML,/marked as working by you/);
  assert.equal(JSON.parse(storage.get('anvildroid_google_play_works:r-one')).confirmed,true);
  vm.runInContext('googlePlayAcknowledged.clear()',context);
  assert.equal(vm.runInContext("googlePlayWorks('r-one')",context),true);
  vm.runInContext("runtimeGoogle.set('r-one',{report:{gsf_android_id:'456'}})",context);
  assert.equal(vm.runInContext("googlePlayWorks('r-one')",context),false);
  vm.runInContext("setGooglePlayWorks('r-one',true);setGooglePlayWorks('r-one',false)",context);
  assert.match(element('library-google-notices').innerHTML,/data-google-registration="r-one"/);
});

test('custom image picker sends local ZIP and creates only after import is Ready', async () => {
  const calls=[];
  const state={id:'import-one',status:'Ready',flavor:'CUSTOM',image_bytes:8192,images:{system:{sha256:'a'.repeat(64),filename:'custom-system.zip'},vendor:{sha256:'b'.repeat(64),filename:'custom-vendor.zip'}}};
  const {context,element}=gui(async(command,args)=>{calls.push([command,args]);if(command==='import_runtime_image') return state;},undefined,'?view=controls');
  context.window.__TAURI__.dialog.open=async()=>'/home/user/AxionOS.zip';
  vm.runInContext("document.getElementById('controller-image').value='CUSTOM';renderCreateImageHint()",context);
  assert.equal(element('btn-import-runtime-image').disabled,false);
  assert.match(element('controller-image-hint').textContent,/Import both/);
  await vm.runInContext('importCustomImage()',context);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.find(([cmd])=>cmd==='import_runtime_image'))),['import_runtime_image',{path:'/home/user/AxionOS.zip',name:'Custom image'}]);
  vm.runInContext('renderCreateImageHint()',context);
  assert.match(element('controller-image-hint').textContent,/Experimental compatibility/);
  assert.match(element('controller-image-hint').textContent,/ARM translation must be configured separately/);
  let request;
  context.capture=value=>{request=JSON.parse(JSON.stringify(value));};
  vm.runInContext("mutateController=request=>capture(request);document.getElementById('controller-name').value='Custom Android';submitRuntimeCreate({preventDefault(){}})",context);
  assert.equal(request.op,'create_image');assert.equal(request.flavor,'CUSTOM');assert.equal(request.system_sha256,'a'.repeat(64));
  request=null;
  vm.runInContext("imageDownloadState.status='Verifying';submitRuntimeCreate({preventDefault(){}})",context);
  assert.equal(request,null);
  assert.match(element('controller-message').textContent,/import a custom image ZIP/);
});

test('custom folder picker requests directory and reports missing images inline', async () => {
  const calls=[];
  const {context,element}=gui(async(command,args)=>{calls.push([command,args]);if(command==='import_runtime_image_folder')throw new Error('Cannot read vendor.img in selected folder');},undefined,'?view=controls');
  let options;
  context.window.__TAURI__.dialog.open=async value=>{options=value;return '/home/user/Images';};
  vm.runInContext("document.getElementById('controller-image').value='CUSTOM'",context);
  await vm.runInContext('importCustomImage(true)',context);
  assert.equal(options.directory,true);
  assert.equal(options.filters,undefined);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.find(([cmd])=>cmd==='import_runtime_image_folder'))),['import_runtime_image_folder',{path:'/home/user/Images',name:'Custom image'}]);
  assert.match(element('custom-image-status').textContent,/vendor.img/);
  assert.equal(element('btn-import-runtime-folder').disabled,false);
  assert.equal(element('btn-import-runtime-image').disabled,false);
});


test('usage poll updates only usage cells with live resource samples', async()=>{
 const requests=[];
 const {context,element}=gui(async(command,args)=>{
  if(command==='runtime_controller') {
   requests.push(args.request.op);
   if(args.request.op==='resources_info')return {host:{cpu_count:8},effective:{scope:'test-scope',cpu_usage_usec:1000000,memory_current_bytes:1073741824,memory_max_bytes:2147483648}};
   if(args.request.op==='storage_info')return {used_bytes:1073741824,free_bytes:9663676416};
  }
 },undefined,'?view=controls',undefined,true);await tick();requests.length=0;
 element('page-runtime').classList.add('active');
 const cell={dataset:{runtimeUsage:'usage-live'},innerHTML:''};
 context.document.querySelectorAll=selector=>selector==='[data-runtime-usage]'?[cell]:[];
 vm.runInContext("controllerRecords=[{id:'usage-live',name:'Live',state:'Running'}];existingRuntime=null",context);
 await vm.runInContext('refreshRuntimeUsage()',context);
 assert.deepEqual(requests,['resources_info','storage_info']);
 assert.match(cell.innerHTML,/aria-label="Memory"[^>]*aria-valuenow="50.0"/);
 assert.match(cell.innerHTML,/aria-label="Disk"[^>]*aria-valuenow="10.0"/);
 assert(!requests.includes('apps'));assert(!requests.includes('refresh'));
});
test('usage poll scans cells once and skips unchanged disk-only rows', async()=>{
 const {context,element}=gui(async(command,args)=>{
  if(command==='runtime_controller' && args.request.op==='storage_info')return {used_bytes:100,total_bytes:1000};
 },undefined,'?view=controls');await tick();
 element('page-runtime').classList.add('active');
 let scans=0,writes=0;
 const cells=['usage-a','usage-b'].map(id=>{
  let html='';
  return {dataset:{runtimeUsage:id},get innerHTML(){return html;},set innerHTML(value){writes++;html=value;}};
 });
 context.document.querySelectorAll=selector=>{if(selector!=='[data-runtime-usage]')return [];scans++;return cells;};
 vm.runInContext("controllerRecords=[{id:'usage-a',state:'Stopped'},{id:'usage-b',state:'Stopped'}];existingRuntime=null",context);
 await vm.runInContext('refreshRuntimeUsage()',context);
 assert.equal(scans,1);assert.equal(writes,2);
 await vm.runInContext('refreshRuntimeUsage()',context);
 assert.equal(scans,2);assert.equal(writes,2);
});
test('usage poll pauses outside runtime page and during mutations', async()=>{
 const requests=[];
 const {context,element}=gui(async(command,args)=>{requests.push(command);},undefined,'?view=controls');await tick();requests.length=0;
 vm.runInContext("controllerRecords=[{id:'usage-hidden',state:'Running'}]",context);
 await vm.runInContext('refreshRuntimeUsage()',context);assert.equal(requests.length,0);
 element('page-runtime').classList.add('active');vm.runInContext('controllerMutation=true',context);
 await vm.runInContext('refreshRuntimeUsage()',context);assert.equal(requests.length,0);
});
test('usage poll discards a late sample after runtime identity changes', async()=>{
 const pending=deferred();
 const {context,element}=gui(async(command,args)=>{
  if(command==='runtime_controller' && args.request.op==='resources_info')return pending.promise;
  if(command==='runtime_controller' && args.request.op==='storage_info')return {};
 },undefined,'?view=controls');await tick();element('page-runtime').classList.add('active');
 vm.runInContext("controllerRecords=[{id:'usage-race',state:'Running'}];existingRuntime=null",context);
 const poll=vm.runInContext('refreshRuntimeUsage()',context);
 vm.runInContext("controllerRecords=[{id:'usage-race',state:'Stopped'}]",context);
 pending.resolve({effective:{scope:'old',cpu_usage_usec:10,memory_current_bytes:100}});await poll;
 assert.equal(vm.runInContext('controllerRecords[0].resources',context),undefined);
});

test('Settings action opens a separate runtime page and Back restores the list', async()=>{
 const calls=[];
 const {context,element}=gui(async(command,args)=>{calls.push([command,args]);return [];},undefined,'?view=controls');await tick();calls.length=0;
 vm.runInContext("controllerRecords=[{id:'r-config',name:'Config <runtime>',state:'Stopped'}];renderController()",context);
 const button={disabled:false,dataset:{controllerOp:'settings',controllerId:'r-config'}};
 element('runtime-table').listeners.click({target:{closest:()=>button}});
 assert(element('page-runtime-settings').classList.contains('active'));
 assert(element('nav-runtime').classList.contains('active'));
 assert.equal(element('runtime-settings-title').textContent,'Config <runtime>');
 assert.match(element('runtime-settings-content').innerHTML,/data-controller-id="r-config"/);
 assert.doesNotMatch(element('controller-runtimes').innerHTML,/runtime-config-grid|data-controller-op="rename"/);
 assert.equal(vm.runInContext('runtimeSettingsId',context),'r-config');
 element('runtime-settings-back').listeners.click();
 assert(element('page-runtime').classList.contains('active'));
 assert(!element('page-runtime-settings').classList.contains('active'));
 assert.equal(calls.length,0);
});
test('runtime Settings supports Existing runtime and handles a deleted target', async()=>{
 const {context,element}=gui(async()=>[],undefined,'?view=controls');await tick();
 vm.runInContext("existingRuntime={id:'default',name:'Existing custom name',state:'Stopped',managed:true};openRuntimeSettings('default')",context);
 assert.equal(element('legacy-runtime-detail').hidden,false);
 assert.equal(element('runtime-settings-content').innerHTML,'');
 assert.equal(element('runtime-settings-title').textContent,'Existing custom name');
 vm.runInContext("controllerRecords=[{id:'r-gone',name:'Gone',state:'Stopped'}];openRuntimeSettings('r-gone');controllerRecords=[];renderController()",context);
 assert.equal(element('legacy-runtime-detail').hidden,true);
 assert.match(element('runtime-settings-content').innerHTML,/no longer available/);
 assert.doesNotMatch(element('runtime-settings-content').innerHTML,/data-controller-op=/);
});
test('runtime Settings delegates configuration input and actions to the correct target', async()=>{
 const {context,element}=gui(async()=>[],undefined,'?view=controls');await tick();
 context.document.querySelector=()=>null;
 vm.runInContext("controllerRecords=[{id:'r-config',name:'Config',state:'Stopped',resources:{configured:{memory_mib:0,cpu_count:0}}}];openRuntimeSettings('r-config')",context);
 element('page-runtime-settings').listeners.change({target:{dataset:{gpuSelect:'r-config'},value:'software'}});
 assert.equal(vm.runInContext("runtimeGpuDraft.get('r-config')",context),'software');
 element('page-runtime-settings').listeners.input({target:{dataset:{resourceId:'r-config',resourceField:'memory_mib'},value:'4096'}});
 assert.equal(vm.runInContext("runtimeResourceDraft.get('r-config').memory_mib",context),4096);
 let target;context.mutateController=request=>{target=request;};
 const button={disabled:false,dataset:{controllerOp:'stop',controllerId:'r-config'}};
 element('page-runtime-settings').listeners.click({target:{closest:()=>button}});
 assert.equal(target.id,'r-config');assert.equal(target.op,'stop');
});
test('table Status contains compact state names and keeps operation detail in Settings',async()=>{
 const {context,element}=gui(async()=>[],undefined,'?view=controls');await tick();
 for(const state of ['Running','Starting','Stopping','Stopped','Frozen','Error']) {
  vm.runInContext(`controllerRecords=[{id:'r-state',name:'State',state:'${state}'}];renderController()`,context);
  assert.match(element('controller-runtimes').innerHTML,new RegExp('role="status">'+state+'</span>'));
  assert.doesNotMatch(element('controller-runtimes').innerHTML,/Android is running|Starting Android|Stopping Android|Action failed/);
 }
 vm.runInContext("runtimeBatchRestarting.add('r-state');renderController()",context);
 assert.match(element('controller-runtimes').innerHTML,/role="status">Restarting<\/span>/);
});


test('runtime Settings exposes every original configuration group directly', async()=>{
 const {context,element}=gui(async()=>[],undefined,'?view=controls');await tick();
 vm.runInContext("controllerRecords=[{id:'r-options',name:'Options',state:'Stopped',display:{mode:'desktop'},storage:{path:'/tmp/android'},resources:{configured:{memory_mib:4096,cpu_count:2},host:{supported:true,memory_total_mib:16384,cpu_count:8}},gpu:{selection:'auto',software_supported:true,devices:[]}}];openRuntimeSettings('r-options')",context);
 const html=element('runtime-settings-content').innerHTML;
 for(const key of ['gpu','storage','arm','resources','display','google','health']) assert.match(html,new RegExp('data-settings-key="r-options:'+key+'"'));
 assert(!html.includes(':advanced'));
 assert(!html.includes('More settings'));
 for(const op of ['rename','reinstall','delete','display_set','gpu_set','resources_set']) assert.match(html,new RegExp('data-controller-op="'+op+'"'));
 vm.runInContext("existingRuntime={id:'default',name:'Existing',state:'Stopped',managed:true};openRuntimeSettings('default')",context);
 const existing=element('existing-runtime-management').innerHTML;
 for(const key of ['gpu','storage','arm','resources','google','health','import'])assert.match(existing,new RegExp('data-settings-key="default:'+key+'"'));
 assert(!existing.includes(':advanced'));
});

test('runtime settings opens important sections and enables running reinstall',()=>{
 const {context,element}=gui(async()=>({}));
 vm.runInContext("controllerRecords=[{id:'r-test',name:'Test',state:'Running'}];openRuntimeSettings('r-test')",context);
 const html=element('runtime-settings-content').innerHTML;
 for(const key of ['gpu','resources','display'])assert.match(html,new RegExp('data-settings-key="r-test:'+key+'" open'));
 assert.doesNotMatch(html,/data-controller-op="reinstall"[^>]*disabled/);
 vm.runInContext("controllerRecords[0].state='Stopping';renderController()",context);
 assert.match(element('runtime-settings-content').innerHTML,/data-controller-op="reinstall"[^>]*disabled/);
});
for (const mode of ['success','cancel','stop-failure','name-change','double-click','default-success','default-failure']) {
 test(`confirmed running reinstall integration: ${mode}`,async()=>{
  const id=mode.startsWith('default')?'default':'r-test';let state='Running',name='Test';const calls=[];
  const gate=deferred();let waiting=false;
  const {context}=gui(async(command,args)=>{
   if(command==='stop_runtime') {calls.push('stop');if(mode==='default-failure')return {success:false,message:'Shutdown failed'};state='Stopped';return {success:true};}
   if(command!=='runtime_controller')return {};
   const r=args.request;
   if(r.op==='refresh')return {id,name,state,managed:true,job:{id:'stop-job',status:'Succeeded'}};
   if(r.op==='stop'){calls.push('stop');if(mode==='stop-failure')throw Error('Shutdown failed');if(mode==='double-click'){waiting=true;await gate.promise;}state='Stopped';if(mode==='name-change')name='Changed';return {id,job:{id:'stop-job'}};}
   if(r.op==='reinstall'){calls.push('reinstall');assert.equal(state,'Stopped');return {id,name,state:'Stopped'};}
   if(r.op==='list')return id==='default'?[]:[{id,name,state}];
   if(r.op==='app_job')return null;
   if(r.op==='display_info')return {mode:'desktop'};
   return {};
  },async()=>mode!=='cancel', '?view=settings');
  await tick();
  vm.runInContext(id==='default'?"existingRuntime={id:'default',name:'Test',state:'Running',managed:true}":"controllerRecords=[{id:'r-test',name:'Test',state:'Running'}]",context);
  const run=()=>vm.runInContext(`mutateController({op:'reinstall',id:'${id}',confirmed_name:'Test',flavor:'installed'})`,context);
  const task=run();
  if(mode==='double-click'){await tick();assert.ok(waiting);await run();gate.resolve();}
  await task;
  assert.deepEqual(calls,mode==='cancel'?[]:['stop-failure','name-change','default-failure'].includes(mode)?['stop']:['stop','reinstall']);
 });
}


test('app status updates preserve card identity and scroll position', async () => {
  const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
  await tick();
  const grid=element('apps-grid');
  vm.runInContext("libraryDefaultState='Stopped';managedLibrary=[{id:'r-one',name:'One',state:'Running'}];managedSnapshots.set('r-one',[{package:'com.test.app',label:'Test',launchable:true,execution_state:'not_running',status_checked_at:Date.now()/1000}]);renderApps(allApps)",context);
  const cards=['Runtime launcher','Not running'].map(status=>{
    const dot={classList:{toggle(){}},setAttribute(name,value){this[name]=value;}};
    return {dataset:{appStatus:status},setAttribute(name,value){this[name]=value;},querySelector(){return dot;}};
  });
  grid.querySelectorAll=()=>cards;
  grid.scrollTop=120;
  let writes=0;const markup=grid.innerHTML;
  Object.defineProperty(grid,'innerHTML',{get:()=>markup,set:()=>{writes++;},configurable:true});
  vm.runInContext("managedSnapshots.get('r-one')[0].execution_state='foreground';renderApps(allApps)",context);
  assert.equal(writes,0);
  assert.equal(cards[1].dataset.appStatus,'Foreground');
  assert.match(cards[1]['aria-label'],/Foreground/);
  assert.equal(grid.scrollTop,120);
});

test('routine app-state polling skips inventory and commits status once', async () => {
  const requests=[];
  const record={id:'r-one',name:'One',state:'Running'};
  const {context}=gui(async(command,args)=>{
    if(command==='get_health_report') return {status:{state:'Stopped'}};
    if(command==='get_library') return {favorites:[],recent:{}};
    if(command==='runtime_controller') {
      requests.push(args.request.op);
      if(args.request.op==='list') return [record];
      if(args.request.op==='refresh') return record;
      if(args.request.op==='apps') return {runtime_id:record.id,apps:[{package:'com.test.app',label:'Test',launchable:true}]};
      if(args.request.op==='app_states') return {runtime_id:record.id,state:'Running',states:{'com.test.app':'foreground'},checked_at:Date.now()/1000};
    }
  },undefined,'?view=settings');
  await tick(); requests.length=0;
  vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'}];managedSnapshots.set('r-one',[{package:'com.test.app',label:'Test',launchable:true}]);managedInventoryChecked.set('r-one',Date.now());libraryPage=true;renderCount=0;renderApps=()=>renderCount++",context);
  await vm.runInContext('refreshAppStates()',context);
  assert.equal(requests.includes('apps'),false);
  assert.equal(vm.runInContext('renderCount',context),1);
  assert.equal(vm.runInContext("managedSnapshots.get('r-one')[0].execution_state",context),'foreground');
  vm.runInContext("managedInventoryChecked.set('r-one',Date.now()-61000)",context);
  requests.length=0;
  await vm.runInContext('refreshAppStates()',context);
  assert.equal(requests.filter(op=>op==='apps').length,1);
});

for (const mode of ['success', 'cancel-dialog', 'cancel-auth', 'failure', 'double-click', 'busy', 'became-busy', 'reconnect-delay', 'reconnect-failure']) {
  test(`controller restart: ${mode}`, async () => {
    const calls=[];const gate=deferred();let confirmCount=0;let restartSent=false;let probes=0;
    const {context,element}=gui(async(command,args)=>{
      if(command==='restart_runtime_controller') {
        calls.push(command);restartSent=true;
        if(mode==='double-click')await gate.promise;
        if(mode==='failure')throw new Error('Unit not found');
        if(mode==='cancel-auth')return {ok:false,message:'Authentication cancelled'};
        return {ok:true,message:''};
      }
      if(command==='runtime_controller') {
        const op=args.request.op;calls.push(op);
        if(op==='list'&&restartSent) {probes++;if(mode==='reconnect-failure'||(mode==='reconnect-delay'&&probes===1))throw Error('Socket not ready');}
        if(op==='list'||op==='arm_sources')return [];
        if(op==='refresh')return {id:'default',state:'Stopped'};
        return {};
      }
      return {};
    },async()=>{confirmCount++;if(mode==='became-busy')vm.runInContext('controllerMutation=true',context);return mode!=='cancel-dialog';},'?view=settings');
    await tick();calls.length=0;
    vm.runInContext('controllerLoading=false;controllerMutation=false;controllerRecords=[];existingRuntime=null;controllerHasActiveWork=()=>false',context);
    element('btn-restart-controller').disabled=false;
    element('btn-restart-controller').textContent='Restart controller';
    context.setTimeout=(callback,ms)=>{if(ms===1000)callback();return 0;};
    if(mode==='busy')vm.runInContext('controllerMutation=true',context);
    const run=()=>vm.runInContext('restartRuntimeController()',context);
    const task=run();
    if(mode==='double-click') {
      await tick();assert.equal(element('btn-restart-controller').textContent,'Restarting…');
      assert.equal(vm.runInContext('controllerMutation',context),true);
      await run();gate.resolve();
    }
    await task;
    assert.equal(calls.filter(op=>op==='restart_runtime_controller').length,['busy','cancel-dialog','became-busy'].includes(mode)?0:1);
    assert.equal(confirmCount,mode==='busy'?0:1);
    if(mode==='reconnect-delay')assert.ok(probes>=2);
    if(mode==='reconnect-failure')assert.ok(probes>=30);
    if(['success','double-click','reconnect-delay'].includes(mode)) {
      assert.equal(element('controller-connection').textContent,'Service connected');
      assert.equal(element('app-toast-message').textContent,'Controller restarted.');
      assert.equal(element('controller-message').textContent,'');
    }
    if(['failure','cancel-auth','reconnect-failure'].includes(mode)) assert.match(element('app-toast-message').textContent,/Cannot restart controller/);
    if(!['busy','became-busy'].includes(mode))assert.equal(vm.runInContext('controllerMutation',context),false);
    assert.equal(element('btn-restart-controller').textContent,'Restart controller');
  });
}

for(const mode of ['official-library','custom-library','installed','no-image','cancel','double-click']) {
 test(`Default setup: ${mode}`,async()=>{
  const calls=[];const gate=deferred();const id='a'.repeat(64);
  const {context,element}=gui(async(command,args)=>{
   if(command==='setup_waydroid'){calls.push(args);return {ok:false,message:'Authentication cancelled'};}
   return {};
  },async()=>{if(mode==='double-click')await gate.promise;return mode!=='cancel';},'?view=settings');
  await tick();
  vm.runInContext(`controllerRequirements={installed_images:${mode==='installed'},checks:[{id:'support',available:${mode==='installed'}}]};controllerMutation=false;waydroidInitBusy=false;setupNavigation='';navigatePage=page=>setupNavigation=page;imageDownloadState={status:'Idle',available_images:${mode==='no-image'?'[]':JSON.stringify([{library_id:id,flavor:mode==='custom-library'?'CUSTOM':'GAPPS',custom_name:mode==='custom-library'?'Custom Android':undefined}])}};updateWaydroidSetupActions()`,context);
  element('waydroid-init-flavor').value=mode==='installed'?'installed':id;
  const run=()=>vm.runInContext('initializeWaydroid()',context);
  const task=run();
  if(mode==='double-click'){await tick();await run();gate.resolve();}
  await task;
  assert.equal(calls.length,['no-image','cancel'].includes(mode)?0:1);
  if(mode==='no-image')assert.equal(vm.runInContext('setupNavigation',context),'images');
  if(calls.length)assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),{flavor:['installed','custom-library'].includes(mode)?'VANILLA':'GAPPS',imageId:mode==='installed'?null:id});
  assert.equal(vm.runInContext('waydroidInitBusy',context),false);
 });
}

for (const failed of [false, true]) test(`runtime icon busy: preserves SVG after ${failed ? 'failure' : 'success'}`, async () => {
  const boot = deferred();
  const {context, element} = gui(async name => {
    if (name === 'start_runtime') return boot.promise;
    if (name === 'get_apps') return [];
    if (name === 'get_health_report') return {status:{state:'Stopped'}};
  });
  await tick();
  const button = element('btn-start');
  button.classList.add('runtime-action-icon');
  button.innerHTML = '<svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7z"/></svg>';
  const icon = button.innerHTML;
  const starting = vm.runInContext("runtimeAction('start_runtime', btnStart)",context);
  assert.equal(button.attributes['aria-busy'],'true');
  assert.ok(button.classList.contains('is-working'));
  assert.equal(button.textContent,'');
  assert.equal(button.innerHTML,icon);
  boot.resolve({success:!failed,message:failed?'Failed':'Started'});
  await starting;
  assert.ok(!button.classList.contains('is-working'));
  assert.equal(button.attributes['aria-busy'],undefined);
  assert.equal(button.innerHTML,icon);
});

test('runtime GPU UI: Default warning and GPU beneath Disk are escaped and scoped', async () => {
  const {context} = gui(async()=>undefined);
  await tick();
  const record = {id:'default',state:'Stopped',gpu:{selection:'/dev/dri/renderD129',devices:[{node:'/dev/dri/renderD129',vendor_id:'0x10de',driver:'nvidia',selectable:false}]}};
  context.gpuFixture = record;
  let html=vm.runInContext('renderGpuControls(gpuFixture,false)',context);
  assert.match(html,/NVIDIA integration is not active/);
  assert.match(html,/Current NVIDIA selection cannot boot/);
  html=vm.runInContext('renderRuntimeUsage(gpuFixture)',context);
  assert.ok(html.indexOf('>GPU<')>html.indexOf('>Disk<'));
  assert.match(html,/>NVIDIA<\/span>/);
  record.gpu.system_nvidia_supported=true;
  html=vm.runInContext('renderGpuControls(gpuFixture,false)',context);
  assert.doesNotMatch(html,/Enable NVIDIA integration|NVIDIA integration is not active/);
  record.id='managed';
  html=vm.runInContext('renderGpuControls(gpuFixture,false)',context);
  assert.doesNotMatch(html,/NVIDIA integration is not active/);
  record.gpu.selection='<bad>';record.gpu.devices=[];
  html=vm.runInContext('renderRuntimeUsage(gpuFixture)',context);
  assert.doesNotMatch(html,/<bad>/);assert.match(html,/&lt;bad&gt;/);
});

test('Frozen Default and managed inventories stay visible and cached refresh does not wake Default', async()=>{
  const calls=[];
  const {context,element}=gui(async(command,args)=>{
    calls.push(command);
    if(command==='get_health_report')return {status:{state:'Frozen'}};
    if(command==='launch_app')return {success:true};
    if(command==='get_apps')throw Error('Passive refresh should keep cached inventory');
  });
  vm.runInContext(`libraryDefaultState='Running'; allApps=[{package:'com.test.sleep',name:'Sleep app',launchable:true}]; managedLibrary=[{id:'r-sleep',name:'Managed',state:'Frozen',display:{mode:'desktop'}}]; managedSnapshots.set('r-sleep',[{package:'com.test.managed',label:'Managed app',launchable:true}]);`,context);
  await new Promise(resolve=>setImmediate(resolve));
  vm.runInContext(`managedLibrary=[{id:'r-sleep',name:'Managed',state:'Frozen',display:{mode:'desktop'}}];`,context);
  await context.refreshLibraryDefaultState();
  assert.equal(context.libraryRuntimeRunning('default'),true);
  assert.equal(context.libraryRuntimeRunning('r-sleep'),true);
  assert.match(element('apps-grid').innerHTML,/com.test.sleep/);
  assert.match(element('apps-grid').innerHTML,/com.test.managed/);
  assert(!calls.includes('get_apps'));
});

test('Default status uses the same badges as managed and keeps transient operation states',async()=>{
  const {context,element}=gui(async()=>undefined);
  await new Promise(resolve=>setImmediate(resolve));
  for(const [state,kind] of [['Running','success'],['Frozen','info'],['Stopped','info'],['Starting','info'],['Error','error']]) {
    context.updateDefaultRuntimeBadge(state);
    assert.equal(element('runtime-state').textContent,state);
    assert.equal(element('runtime-state').className,`runtime-status ${kind}`);
    assert.equal(context.runtimeBadgeKind(state),kind);
  }
  vm.runInContext("runtimeBatchRestarting.add('default')",context);
  context.updateDefaultRuntimeBadge('Stopped');assert.equal(element('runtime-state').textContent,'Restarting');
  vm.runInContext("runtimeBatchRestarting.clear(); defaultRuntimeOperation='stop_runtime'",context);
  context.updateDefaultRuntimeBadge('Running');assert.equal(element('runtime-state').textContent,'Stopping');
  vm.runInContext("defaultRuntimeOperation=null; controllerPending={id:'default',op:'reinstall',phase:'applying'}",context);
  context.updateDefaultRuntimeBadge('Stopped');assert.equal(element('runtime-state').textContent,'Reinstalling');
  vm.runInContext("controllerPending=null",context);
  context.updateDefaultRuntimeBadge('Unavailable','transport failed');
  assert.equal(element('runtime-state').textContent,'Unavailable');assert.equal(element('runtime-state').title,'transport failed');
});

test('Default runtime buttons tolerate missing state and retain stale-session recovery',async()=>{
  const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
  await tick();
  for (const state of [null,undefined]) {
    assert.doesNotThrow(()=>context.updateRuntimeButtons(state));
    for (const id of ['btn-start','btn-stop','btn-recover']) assert.equal(element(id).style.display,'none');
  }
  context.updateRuntimeButtons({type:'Error',detail:'session=RUNNING, container=STOPPED'});
  assert.equal(element('btn-recover').style.display,'');
  assert.equal(element('btn-recover').title,'Recover stale session');
  context.updateRuntimeButtons('Frozen');
  assert.equal(element('btn-stop').style.display,'');
  assert.equal(element('btn-recover').style.display,'none');
});

test('Default health refresh handles transport failure and recovers on the next request',async()=>{
  let failed=true;
  const {context,element}=gui(async command=>{
    if(command==='get_health_report') {
      if(failed) throw new Error('Health transport unavailable');
      return {status:{state:'Running',session_service:true,container_state:'RUNNING'}};
    }
    if(command==='runtime_controller') return [];
  },undefined,'?view=settings');
  await tick();
  vm.runInContext("libraryDefaultState='Running';updateRuntimeButtons('Running')",context);
  await assert.doesNotReject(()=>context.refreshStatus());
  assert.equal(element('runtime-state').textContent,'Unavailable');
  assert.match(element('runtime-state').title,/Health transport unavailable/);
  assert.equal(vm.runInContext('libraryDefaultState',context),'Unknown');
  assert.equal(context.libraryRuntimeRunning('default'),false);
  for(const id of ['btn-start','btn-stop','btn-recover']) assert.equal(element(id).style.display,'none');
  assert.equal(element('btn-refresh-runtimes').disabled,false);
  assert.equal(element('btn-refresh-runtimes').textContent,'Refresh');
  failed=false;
  await assert.doesNotReject(()=>context.refreshStatus());
  assert.equal(element('runtime-state').textContent,'Running');
  assert.equal(element('runtime-state').title,'');
  assert.equal(context.libraryRuntimeRunning('default'),true);
  assert.equal(element('btn-stop').style.display,'');
});

test('Default boot settings use the shared stop apply restart lifecycle',async()=>{
  const calls=[];
  const {context}=gui(async(command,args)=>{
    calls.push([command,args]);
    if(command==='runtime_controller') {
      const r=args.request;
      if(r.op==='refresh')return {id:'default',name:'Default',managed:true,state:'Running'};
      if(r.op==='gpu_set')return {selection:'auto'};
    }
  });
  await new Promise(resolve=>setImmediate(resolve));
  vm.runInContext(`existingRuntime={id:'default',name:'Default',managed:true,state:'Running'}; renderController=()=>{}; refreshController=async()=>{}; refreshExistingRuntime=async()=>{}; refreshStatus=async()=>{}; executeBatchOperation=async(record,op)=>{testTransitions.push(op);return {...record,state:op==='stop'?'Stopped':'Running'};};`,Object.assign(context,{testTransitions:[]}));
  await context.mutateController({op:'gpu_set',id:'default',node:'auto'});
  assert.deepEqual(context.testTransitions,['stop','start']);
  assert(calls.some(([cmd,args])=>cmd==='runtime_controller'&&args.request.op==='gpu_set'));
});

test('Default Running and Frozen boot controls stay enabled and advertise restart',async()=>{
  const {context}=gui(async()=>undefined);
  await new Promise(resolve=>setImmediate(resolve));
  for(const state of ['Running','Frozen']) {
    context.defaultFixture={id:'default',state,managed:true,
      gpu:{selection:'auto',devices:[],system_nvidia_supported:true},
      resources:{host:{supported:true,memory_total_mib:16384,memory_available_mib:8192,cpu_count:8},configured:{memory_mib:4096,cpu_count:4},presets:[]},
      arm:{supported:true,enabled:true,provider:'libndk',sources:[]}};
    for(const render of ['renderGpuControls','renderResourceControls']) {
      const html=vm.runInContext(`${render}(defaultFixture,false)`,context);
      assert.match(html,/>Save &amp; restart<|>Save & restart</);
      assert.doesNotMatch(html,/data-controller-op="(?:gpu_set|resources_set)"[^>]*disabled/);
    }
    const arm=vm.runInContext('renderRuntimeArm(defaultFixture,false)',context);
    assert.match(arm,/Disable translation & restart/);
    assert.doesNotMatch(arm,/data-controller-op="arm_set"[^>]*disabled/);
  }
});


test('library runtime multi-select separates duplicate app names and preserves pinned actions',async()=>{
  const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
  await tick();
  vm.runInContext(`libraryDefaultState='Frozen';allApps=[{package:'com.test.same',label:'Settings'}];
    managedLibrary=[{id:'r-one',name:'One <Android>',state:'Running'},{id:'r-two',name:'Two',state:'Frozen'}];
    for(const record of managedLibrary) managedSnapshots.set(record.id,[{package:'com.test.same',label:'Settings',launchable:true}]);
    renderApps(allApps);`,context);
  let html=element('apps-grid').innerHTML;
  assert.equal((html.match(/data-package="com.test.same"/g)||[]).length,3);
  assert.match(html,/class="app-runtime-name" title="Default runtime">Default runtime/);
  assert.match(html,/class="app-runtime-name" title="One &lt;Android&gt;">One &lt;Android&gt;/);
  assert.match(html,/class="app-runtime-name" title="Two">Two/);
  vm.runInContext("setLibraryRuntimeFilter(['default','r-two'])",context);
  html=element('apps-grid').innerHTML;
  assert.equal((html.match(/data-package="com.test.same"/g)||[]).length,2);
  assert.match(html,/data-runtime-id="default"/);
  assert.match(html,/data-runtime-id="r-two"/);
  assert.doesNotMatch(html,/data-runtime-id="r-one"/);
  assert.equal(element('runtime-filter-label').textContent,'2 runtimes');
  assert.doesNotMatch(element('library-runtime-status').innerHTML,/One &lt;Android&gt;/);
  vm.runInContext("setLibraryRuntimeFilter(['r-one'])",context);
  assert.equal(element('runtime-filter-label').textContent,'One <Android>');
  assert.match(element('apps-grid').innerHTML,/data-runtime-id="r-one"/);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-runtime-id="r-two"/);
  vm.runInContext('setLibraryRuntimeFilter([])',context);
  assert.match(element('apps-grid').innerHTML,/Select runtimes to view apps/);
  assert.doesNotMatch(element('apps-grid').innerHTML,/data-package=/);
  vm.runInContext('setLibraryRuntimeFilter(null)',context);
  assert.equal((element('apps-grid').innerHTML.match(/data-package="com.test.same"/g)||[]).length,3);
});

test('runtime filter checkboxes support all, partial, none and Escape without replacing options',async()=>{
  const {context,element}=gui(async()=>undefined,undefined,'?view=settings');
  await tick();
  vm.runInContext("managedLibrary=[{id:'r-one',name:'One',state:'Running'}];renderRuntimeFilter()",context);
  const inputs=['all','default','r-one'].map(id=>({dataset:{libraryRuntime:id}}));
  const panel=element('runtime-filter-options');
  panel.querySelectorAll=()=>inputs;
  let writes=0,html=panel.innerHTML;
  Object.defineProperty(panel,'innerHTML',{get:()=>html,set:value=>{writes++;html=value;}});
  context.renderRuntimeFilter();
  assert(inputs.every(input=>input.checked));
  element('filter-runtime').listeners.change({target:{dataset:{libraryRuntime:'r-one'},checked:false}});
  assert.equal(context.libraryRuntimeSelected('default'),true);
  assert.equal(context.libraryRuntimeSelected('r-one'),false);
  assert.equal(inputs[0].indeterminate,true);
  assert.equal(inputs[0].checked,false);
  element('filter-runtime').listeners.change({target:{dataset:{libraryRuntime:'all'},checked:false}});
  assert(inputs.every(input=>!input.checked));
  element('filter-runtime').listeners.change({target:{dataset:{libraryRuntime:'r-one'},checked:true}});
  assert.equal(context.libraryRuntimeSelected('default'),false);
  assert.equal(context.libraryRuntimeSelected('r-one'),true);
  element('filter-runtime').listeners.change({target:{dataset:{libraryRuntime:'all'},checked:true}});
  assert(inputs.every(input=>input.checked));
  assert.equal(writes,0);
  element('filter-runtime').open=true;
  element('filter-runtime').listeners.keydown({key:'Escape'});
  assert.equal(element('filter-runtime').open,false);
});

test('library refresh prunes removed selections without exposing unrelated runtimes',async()=>{
  let records=[{id:'r-one',name:'One',state:'Stopped'},{id:'r-two',name:'Two',state:'Stopped'}];
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller') {
      if(args.request.op==='list') return records;
      if(args.request.op==='refresh') return records.find(record=>record.id===args.request.id);
    }
  },undefined,'?view=settings');
  await tick();
  await context.refreshManagedLibrary();
  context.setLibraryRuntimeFilter(['r-one','r-two']);
  records=[records[1]];
  await context.refreshManagedLibrary();
  assert.equal(element('runtime-filter-label').textContent,'Two');
  assert.equal(context.libraryRuntimeSelected('r-one'),false);
  records=[];
  await context.refreshManagedLibrary();
  assert.equal(context.libraryRuntimeSelected('default'),false);
  assert.match(element('apps-grid').innerHTML,/Select runtimes to view apps/);
  context.setLibraryRuntimeFilter(null);
  records=[{id:'r-new',name:'New',state:'Stopped'}];
  await context.refreshManagedLibrary();
  assert.equal(context.libraryRuntimeSelected('r-new'),true);
});

test('application preferences load both controls and persist independent toggles', async()=>{
  const calls=[];
  const {context,element}=gui(async(command,args)=>{
    if(command==='get_application_preferences') return {minimize_to_panel:true,auto_startup:false};
    if(command==='set_application_preference') {calls.push(args);return {minimize_to_panel:true,auto_startup:true};}
  });
  await context.refreshApplicationPreferences();
  const panel=element('minimize-to-panel'),startup=element('auto-startup');
  assert.equal(panel.checked,true);assert.equal(startup.checked,false);
  startup.checked=true;
  await startup.listeners.change({target:startup});
  assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{preference:'auto_startup',enabled:true}]);
  assert.equal(panel.checked,true);assert.equal(startup.checked,true);
  assert.equal(panel.disabled,false);assert.equal(startup.disabled,false);
});

test('application preference failure rolls back checkbox and re-enables controls',async()=>{
  const {context,element,banner}=gui(async(command)=>{
    if(command==='get_application_preferences')return {minimize_to_panel:false,auto_startup:true};
    if(command==='set_application_preference')throw new Error('Permission denied');
  });
  await context.refreshApplicationPreferences();
  const panel=element('minimize-to-panel');panel.checked=true;
  await panel.listeners.change({target:panel});
  assert.equal(panel.checked,false);
  assert.equal(element('auto-startup').checked,true);
  assert.equal(panel.disabled,false);
  assert.match(banner.textContent,/Permission denied/);
});

test('application preference save disables both controls and prevents overlapping refresh',async()=>{
  const pending=deferred();let reads=0;
  const {context,element}=gui(async(command)=>{
    if(command==='get_application_preferences'){reads++;return {minimize_to_panel:false,auto_startup:false};}
    if(command==='set_application_preference')return pending.promise;
  });
  await context.refreshApplicationPreferences();
  const panel=element('minimize-to-panel');panel.checked=true;
  const save=panel.listeners.change({target:panel});
  assert.equal(panel.disabled,true);assert.equal(element('auto-startup').disabled,true);
  await context.refreshApplicationPreferences();assert.equal(reads,1);
  pending.resolve({minimize_to_panel:true,auto_startup:false});await save;
  assert.equal(panel.checked,true);assert.equal(panel.disabled,false);
});

test('startup minimize has its own persistent application preference',async()=>{
 const calls=[];
 const {context,element}=gui(async(command,args)=>{
  if(command==='get_application_preferences')return {minimize_to_panel:false,startup_minimize:true,auto_startup:false};
  if(command==='set_application_preference'){calls.push(args);return {minimize_to_panel:false,startup_minimize:false,auto_startup:false};}
 });
 await context.refreshApplicationPreferences();
 assert.equal(element('startup-minimize').checked,true);
 assert.equal(element('minimize-to-panel').checked,false);
 element('startup-minimize').checked=false;
 await element('startup-minimize').listeners.change({target:element('startup-minimize')});
 assert.equal(calls[0].preference,'startup_minimize');
 assert.equal(calls[0].enabled,false);
 assert.equal(element('minimize-to-panel').checked,false);
});

test('global runtime boot checkboxes preserve values, escape labels and pin mutations',async()=>{
 const id='r-'+'1'.repeat(32),other='r-'+'2'.repeat(32),calls=[];
 const {context,element}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  const req=args.request;calls.push(req);
  if(req.op==='list')return [{id,name:'One <test>',state:'Running',start_on_boot:true},{id:other,name:'Two',state:'Stopped',start_on_boot:false}];
  if(req.op==='start_on_boot')return {id:req.id,start_on_boot:req.enabled};
 });
 await context.refreshRuntimeStartupPreferences();
 const html=element('runtime-startup-list').innerHTML;
 assert.match(html,/One &lt;test&gt;/);
 assert.match(html,new RegExp('data-runtime-startup="'+id+'"[^>]*checked'));
 await context.changeRuntimeStartupPreference({target:{dataset:{runtimeStartup:other},checked:true}});
 assert.equal(calls.at(-1).op,'start_on_boot');assert.equal(calls.at(-1).id,other);assert.equal(calls.at(-1).enabled,true);
 assert.match(element('runtime-startup-list').innerHTML,new RegExp('data-runtime-startup="'+other+'"[^>]*checked'));
 assert.equal(calls.some(req=>['start','stop','gpu_info','arm_info','storage_info'].includes(req.op)),false);
 assert.doesNotMatch(fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8'),/data-settings-key="[^\n]+:boot/);
});

test('boot save rejects another runtime response and restores previous value',async()=>{
 const id='r-'+'3'.repeat(32),other='r-'+'4'.repeat(32);
 const {context,element,banner}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  if(args.request.op==='list')return [{id,name:'Three',state:'Stopped',start_on_boot:false}];
  if(args.request.op==='start_on_boot')return {id:other,start_on_boot:true};
 });
 await context.refreshRuntimeStartupPreferences();
 await context.changeRuntimeStartupPreference({target:{dataset:{runtimeStartup:id},checked:true}});
 assert.doesNotMatch(element('runtime-startup-list').innerHTML,/checked/);
 assert.match(banner.textContent,/Invalid runtime startup response/);
});

test('boot preferences recover after failed refresh and suppress refresh while saving',async()=>{
 const id='r-'+'5'.repeat(32),pending=deferred();let fail=false,reads=0;
 const {context,element}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  if(args.request.op==='list'){reads++;if(fail)throw Error('Offline');return [{id,name:'Five',state:'Stopped',start_on_boot:false}];}
  if(args.request.op==='start_on_boot')return pending.promise;
 });
 await context.refreshRuntimeStartupPreferences();fail=true;
 await context.refreshRuntimeStartupPreferences();assert.match(element('runtime-startup-list').innerHTML,/Offline/);
 fail=false;await context.refreshRuntimeStartupPreferences();assert.match(element('runtime-startup-list').innerHTML,/Five/);
 const saving=context.changeRuntimeStartupPreference({target:{dataset:{runtimeStartup:id},checked:true}});
 const before=reads;await context.refreshRuntimeStartupPreferences();assert.equal(reads,before);
 pending.resolve({id,start_on_boot:true});await saving;
 assert.match(element('runtime-startup-list').innerHTML,/checked/);assert.doesNotMatch(element('runtime-startup-list').innerHTML,/disabled/);
});

test('global boot table includes Default even with no managed inventory or unavailable controller',async()=>{
 let fail=false;const calls=[];
 const {context,element}=gui(async(command,args)=>{
  if(command!=='runtime_controller')return;
  calls.push(args.request);
  if(fail)throw Error('Offline');
  return [];
 });
 await context.refreshRuntimeStartupPreferences();
 assert.match(element('runtime-startup-list').innerHTML,/Default runtime/);
 assert.match(element('runtime-startup-list').innerHTML,/Waydroid service/);
 assert.match(element('runtime-startup-list').innerHTML,/runtime-table-scroll/);
 assert.doesNotMatch(element('runtime-startup-list').innerHTML,/data-runtime-startup="default"/);
 const before=calls.length;
 await context.changeRuntimeStartupPreference({target:{dataset:{runtimeStartup:'default'},checked:true}});
 assert.equal(calls.length,before);
 fail=true;await context.refreshRuntimeStartupPreferences();
 assert.match(element('runtime-startup-list').innerHTML,/Default runtime/);
 assert.match(element('runtime-startup-list').innerHTML,/Offline/);
});

test('Settings first visit loads ARM catalog even when runtime configuration fails', async () => {
  const calls=[];
  const source={engine:'libndk',sha256:'a'.repeat(64),version:'Android 11',label:'ARM 11',installed:false};
  const {context,element}=gui(async(command,args)=>{
    if(command==='runtime_controller') {
      calls.push(args.request.op);
      if(args.request.op==='arm_sources') return [source];
    }
    return {};
  },undefined,'?view=controls');
  calls.length=0;
  await vm.runInContext('refreshSettings()',context);
  assert.ok(calls.includes('arm_sources'));
  assert.equal(calls.filter(op=>op==='arm_sources').length,1);
  assert.match(element('arm-source-panel').innerHTML,/Android 11/);
  assert.doesNotMatch(element('arm-source-panel').innerHTML,/Loading ARM/);
});

test('ARM catalog refresh shares pending request and clears errors on retry', async () => {
  let calls=0, fail=true;
  const gate=deferred();
  const {context,element}=gui(async(command,args)=>{
    if(args?.request.op==='arm_sources') {
      calls++;
      if(fail) { await gate.promise; throw new Error('offline'); }
      return [{engine:'libndk',sha256:'b'.repeat(64),version:'Recovered',installed:false}];
    }
    return {};
  },undefined,'?view=controls');
  const work=vm.runInContext('Promise.all([refreshArmSources(),refreshArmSources()])',context);
  gate.resolve(); await work;
  assert.equal(calls,1);
  assert.match(element('arm-source-panel').innerHTML,/Cannot load ARM sources/);
  fail=false;
  await vm.runInContext('refreshArmSources({light:true})',context);
  assert.equal(calls,2);
  assert.match(element('arm-source-panel').innerHTML,/Recovered/);
  assert.doesNotMatch(element('arm-source-panel').innerHTML,/Cannot load ARM sources/);
});


test('Settings Refresh retries ARM after controller failure', async () => {
  let fail=true, reads=0;
  const {context,element}=gui(async(command,args)=>{
    if(args?.request.op==='arm_sources') {
      reads++;
      if(fail) throw new Error('ARM controller unavailable');
      return [{engine:'libndk',sha256:'c'.repeat(64),version:'Ready on retry',installed:false}];
    }
    return {};
  },undefined,'?view=controls');
  await context.refreshSettings();
  assert.match(element('arm-source-panel').innerHTML,/ARM controller unavailable/);
  fail=false;
  await element('btn-refresh-settings').listeners.click();
  assert.equal(reads,2);
  assert.match(element('arm-source-panel').innerHTML,/Ready on retry/);
  assert.doesNotMatch(element('arm-source-panel').innerHTML,/Cannot load ARM sources|Loading ARM/);
  assert.equal(element('btn-refresh-settings').disabled,false);
});
