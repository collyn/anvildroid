const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
function setup(){
 const calls=[],pending=[],toasts=[];
 class Element{
  constructor(){this.children=[];this.style={};this.events={};}
  setAttribute(){} addEventListener(n,f){this.events[n]=f;}
  appendChild(e){e.parent=this;this.children.push(e);}
  insertAdjacentElement(_,e){e.parent=this.parent;this.parent.children.splice(this.parent.children.indexOf(this)+1,0,e);}
  getBoundingClientRect(){return {width:220,height:this.children.length*30};}
  querySelector(){return {focus(){}};} remove(){this.removed=true;}
 }
 const ctx=vm.createContext({activeMenu:null,probeInstallBusy:false,pendingLaunch:false,pendingAppAction:false,preferenceBusy:false,
 allApps:[],library:{favorites:[]},managedPreferences:new Map(),window:{innerWidth:1200,innerHeight:800},console,
 document:{createElement:()=>new Element(),body:new Element()},
 tauriInvoke:(cmd,args)=>{calls.push({cmd,args});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
 showAppToast:(...args)=>toasts.push(args)});
 const start=source.indexOf('async function appMenuAction('),end=source.indexOf('async function openKeymapOverlay(');
 vm.runInContext(source.slice(start,end),ctx);
 return {ctx,calls,pending,toasts,items:()=>ctx.activeMenu.children.map(c=>c.textContent)};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('remove option appears only for existing shortcut and targets the selected runtime',async()=>{
 const {ctx,calls,pending,items}=setup();const runtime='r-'+'a'.repeat(32);
 ctx.showContextMenu({clientX:10,clientY:780},'com.example.game','Game',runtime);
 assert.ok(!items().includes('Edit keymap (F2 in app window)'));
 assert.ok(!items().includes('Remove menu shortcut'));
 assert.equal(calls[0].cmd,'has_app_shortcut');assert.equal(calls[0].args.runtimeId,runtime);
 pending[0].resolve(true);await flush();
 const labels=items();assert.equal(labels[labels.indexOf('Create/update menu shortcut')+1],'Remove menu shortcut');
 ctx.activeMenu.children.find(c=>c.textContent==='Remove menu shortcut').events.click();
 assert.equal(ctx.activeMenu,null);assert.equal(calls[1].cmd,'remove_app_shortcut');assert.equal(calls[1].args.runtimeId,runtime);
 assert.equal(calls[1].args.package,'com.example.game');pending[1].resolve({success:true,message:'Removed'});await flush();
 assert.equal(ctx.pendingAppAction,false);
});
test('missing shortcuts and stale checks never add a remove option to the current menu',async()=>{
 const {ctx,pending,items}=setup();
 ctx.showContextMenu({clientX:10,clientY:10},'com.example.one','One');
 ctx.showContextMenu({clientX:10,clientY:10},'com.example.two','Two');
 pending[0].resolve(true);pending[1].resolve(false);await flush();
 assert.ok(!items().includes('Remove menu shortcut'));
});
test('Default removal needs no Android transition, suppresses duplicates, and reports failures',async()=>{
 const {ctx,calls,pending,toasts}=setup();
 const work=ctx.appMenuAction('remove-shortcut','com.example.game','Game');
 await ctx.appMenuAction('remove-shortcut','com.example.game','Game');
 assert.equal(calls.length,1);assert.equal(calls[0].args.runtimeId,'default');
 pending[0].reject(Error('Permission denied'));await work;
 assert.match(toasts[0][0],/Permission denied/);assert.equal(ctx.pendingAppAction,false);
 const retry=ctx.appMenuAction('remove-shortcut','com.example.game','Game');
 pending[1].resolve({success:false,message:'Foreign entry'});await retry;
 assert.equal(toasts[1][1],'error');assert.match(toasts[1][0],/Foreign entry/);
});
