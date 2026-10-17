const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
test('Waydroid stays pinned first for every sort without changing ordinary app order',()=>{
 const ctx=vm.createContext({isFavorite:a=>!!a.favorite,lastOpened:a=>a.recent||0});
 vm.runInContext(source.slice(source.indexOf('function sortApps('),source.indexOf('function appExecutionLabel(')),ctx);
 const apps=[{package:'z.app',label:'Z',recent:3},{package:'a.app',label:'A',favorite:true},
 {package:'org.anvildroid.desktop',label:'Waydroid',desktop_entry:true,runtime_id:'r-one'},
 {package:'org.anvildroid.desktop',label:'Waydroid',desktop_entry:true,runtime_id:'r-two'}];
 for(const mode of ['name-asc','name-desc','recent','favorites','package']){
   const result=ctx.sortApps(apps,mode);
   assert.equal(result[0].desktop_entry,true);assert.equal(result[1].desktop_entry,true);
   assert.equal(result[2].desktop_entry,undefined);
   const ordinary=ctx.sortApps(apps.filter(a=>!a.desktop_entry),mode);
   assert.equal(result.slice(2).map(a=>a.package).join(),ordinary.map(a=>a.package).join());
 }
 assert.equal(apps[0].package,'z.app');
});
function setup(confirmed=true){
 const elements=new Map(),calls=[],id='r-'+'a'.repeat(32);
 const el=k=>{if(!elements.has(k))elements.set(k,{value:id,disabled:false,textContent:'',addEventListener(n,f){this[n]=f;}});return elements.get(k);};
 const ctx=vm.createContext({AppDialog:{confirm:async()=>confirmed},document:{getElementById:el},pendingLaunch:null,pendingAppAction:false,
 controllerMutation:false,runtimeBatchBusy:false,pendingWaydroidRuntime:null,allApps:[],renderApps(){},showAppToast(){},
 waydroidActivationToken:async()=>null,describeWaydroidWindow:()=>'',
 managedLibrary:[{id,name:'test',state:'Running'}],window:{__TAURI__:{dialog:{confirm:async()=>confirmed}}},
 tauriInvoke:async(cmd,args)=>{calls.push({cmd,args});return{success:true,message:'Installed'};},
 refreshManagedLibrary:async()=>{},showBanner(){},
 refreshBatchRecord:async(runtimeId)=>{const record=ctx.managedLibrary.find(r=>r.id===runtimeId)||ctx.defaultRecord;if(!record||!['Running','Frozen'].includes(record.state))throw new Error('Start runtime');return record;},
 launchRuntimeWaydroidWindow:async record=>{calls.push({id:record.id,action:'full_ui',package:'org.anvildroid.desktop'});return{window:{state:'ready'}};},
 controllerRequest:async request=>{
 calls.push(request);return{id:'job',runtime_id:id,action:'full_ui',status:'Succeeded'};}});
 vm.runInContext(source.slice(source.indexOf('var probeInstallBusy'),source.indexOf('function iconUrl')),ctx);
 return{ctx,calls,el,id};
}
test('cancelled desktop confirmation never installs',async()=>{
 const {calls,el}=setup(false);await el('install-touch-probe').click();assert.equal(calls.length,0);assert.equal(el('probe-runtime').disabled,false);
});
test('confirmed install pins runtime and uses bundled command, not arbitrary path',async()=>{
 const {calls,el,id}=setup();await el('install-touch-probe').click();assert.equal(calls[0].cmd,'install_touch_probe');assert.equal(calls[0].args.runtimeId,id);assert.equal(calls[0].args.path,undefined);
});
test('Waydroid launch routes to selected managed runtime only',async()=>{
 const {ctx,calls,id}=setup();await ctx.openWaydroid(id);assert.equal(calls[0].id,id);assert.equal(calls[0].action,'full_ui');assert.equal(calls[0].package,'org.anvildroid.desktop');
});
test('stopped runtime does not launch or install',async()=>{
 const {ctx,calls,el,id}=setup();ctx.managedLibrary[0].state='Stopped';await ctx.openWaydroid(id);await el('install-touch-probe').click();assert.equal(calls.length,0);
});

test('Touch Probe supports Default runtime and keeps the target pinned during confirmation',async()=>{
 const {ctx,calls,el}=setup();ctx.defaultRecord={id:'default',name:'Default runtime',state:'Frozen'};
 ctx.window.AnvilKeymapLibrary={record:()=>ctx.defaultRecord,updateDiagnostics(){}};
 el('probe-runtime').value='default';
 ctx.AppDialog.confirm=async()=>{el('probe-runtime').value='r-other';return true;};
 await el('install-touch-probe').click();assert.equal(calls[0].args.runtimeId,'default');
});
