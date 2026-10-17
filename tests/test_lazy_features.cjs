const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {create}=require('../crates/anvildroid-gui/src-frontend/lazy-features.js');
test('features stay unloaded until requested; concurrent entry loads dependencies once in order',async()=>{
 const calls=[];let finish;
 const loader=create(src=>{calls.push(src);return src==='keymap-drafts.js'?new Promise(r=>finish=r):Promise.resolve();});
 assert.deepEqual(calls,[]);
 const first=loader.ensure('controls'),second=loader.ensure('controls');
 await Promise.resolve();assert.deepEqual(calls,['keymap-drafts.js']);
 finish();await Promise.all([first,second]);
 assert.deepEqual(calls,['keymap-drafts.js','keymap-library.js']);
 await loader.ensure('controls');assert.equal(calls.length,2);
 await loader.ensure('runtime');assert.equal(calls.at(-1),'runtime-settings.js');
});
test('failed feature retries only failed dependency, without rebinding successful scripts',async()=>{
 const calls=[];let fail=true;
 const loader=create(async src=>{calls.push(src);if(src==='keymap-library.js'&&fail)throw Error('offline');});
 await assert.rejects(loader.ensure('controls'),/offline/);fail=false;
 await loader.ensure('controls');
 assert.deepEqual(calls,['keymap-drafts.js','keymap-library.js','keymap-library.js']);
});
const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
function navigation(){
 const calls=[],tasks=[],page={classList:{add(){}}};
 const ctx=vm.createContext({currentPage:'apps',libraryPage:true,appStatesTimer:null,appLibraryStarted:true,adbPage:null,syncRuntimeSettingsFeatures(){},
 clearTimeout(){},setTimeout(){},navItems:[],pages:[],document:{getElementById:()=>page},
 pageLoads:{schedule:(target,active,load)=>tasks.push({target,active,load})},
 lazyFeatures:{ensure:async feature=>calls.push(feature)},window:{AnvilKeymapLibrary:{refresh:async()=>calls.push('refresh')}},
 scheduleRuntimeUsage(){},showAppToast:message=>calls.push(message)});
 vm.runInContext(source.slice(source.indexOf('function navigatePage('),source.indexOf('navItems.forEach(item =>')),ctx);
 return {ctx,calls,tasks};
}
test('Controls entry and direct launch await optional code before refresh',async()=>{
 for(const currentPage of ['apps',null]){
  const {ctx,calls,tasks}=navigation();ctx.currentPage=currentPage;
  ctx.navigatePage('controls');assert.deepEqual(calls,[]);
  await tasks[0].load();assert.deepEqual(calls,['controls','refresh']);
 }
});
test('leaving Controls while scripts load avoids stale inventory request',async()=>{
 const {ctx,calls,tasks}=navigation();let finish;
 ctx.lazyFeatures.ensure=()=>new Promise(r=>finish=r);
 ctx.navigatePage('controls');const work=tasks[0].load();ctx.navigatePage('images');
 finish();await work;assert.deepEqual(calls,[]);
});
test('failed Controls entry displays toast and same-tab click can retry',async()=>{
 const {ctx,calls,tasks}=navigation();ctx.window.AnvilKeymapLibrary=undefined;
 ctx.lazyFeatures.ensure=async()=>{throw Error('offline');};
 ctx.navigatePage('controls');await assert.rejects(tasks[0].load(),/offline/);
 assert.deepEqual(calls,['Error: offline']);ctx.navigatePage('controls');assert.equal(tasks.length,2);
});
