const test = require('node:test');
const assert = require('node:assert/strict');
const {create, connectionState} = require('../crates/anvildroid-gui/src-frontend/adb.js');
function setup(overrides={}) {
  const elements = new Map(), calls=[], toasts=[];
  const document={getElementById(id){if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',addEventListener(){}});return elements.get(id);}};
  const api=create({document, active:()=>false, escape:String, toast:(...a)=>toasts.push(a), copy:async()=>{},
    request:async r=>{calls.push(r); if(r.op==='list')return [{id:'r-one',name:'Test',state:'Running'}];if(r.op==='refresh')return {id:'default',name:'Default runtime',state:'Frozen'};return {enabled:r.op==='adb_set'?r.enabled:true,endpoint:'127.0.0.1:44444'};},
    invoke:async (op,args)=>{calls.push({op,...args}); return op==='adb_host_status'?{available:true,devices:[{endpoint:'127.0.0.1:44444',state:'unauthorized'}]}:'connected';}, ...overrides});
  return {api,elements,calls,toasts};
}
test('ADB reflects actual host transport state, rather than claiming connected on request',()=>{
  for(const [value, label] of [['device','Connected'],['offline','Offline'],['unauthorized','Unauthorized']])
    assert.equal(connectionState({enabled:true,endpoint:'127.0.0.1:44'}, {devices:[{endpoint:'127.0.0.1:44',state:value}]}),label);
  assert.equal(connectionState({enabled:true,endpoint:'127.0.0.1:44'},{devices:[]}), 'Ready');
  assert.equal(connectionState({enabled:false},{devices:[]}), 'Disabled');
});
test('ADB page includes default and managed runtimes and unauthorized advice', async()=>{
  const {api,elements,toasts,calls}=setup();await api.refresh();
  assert.match(elements.get('adb-runtime-rows').innerHTML,/Default runtime/);
  assert.match(elements.get('adb-runtime-rows').innerHTML,/Test/);
  await api.action('default','connect');
  assert.ok(calls.some(r=>r.op==='adb_set'&&r.id==='default'&&r.enabled));
  assert.ok(calls.some(r=>r.op==='adb_host_action'&&r.action==='connect'));
  assert.ok(toasts.some(([text,level])=>text.includes('Allow USB debugging')&&level==='info'));
});
test('Disconnect always disables relay even if host ADB is unavailable',async()=>{
  const {api,calls}=setup({invoke:async()=>({available:false,devices:[]})});await api.refresh();await api.action('default','disconnect');
  assert.ok(calls.some(r=>r.op==='adb_set'&&!r.enabled));
});
test('Inventory errors retain default row and stop state hides Connect',async()=>{
  const {api,elements}=setup({request:async r=>{if(r.op==='list')throw Error('controller offline');if(r.op==='refresh')return {id:'default',name:'Default runtime',state:'Stopped'};return {enabled:false};}});
  await api.refresh();assert.match(elements.get('adb-runtime-rows').innerHTML,/Default runtime/);
  assert.match(elements.get('adb-runtime-rows').innerHTML,/data-adb-action="connect"[^>]+disabled/);
  assert.match(elements.get('adb-status').textContent,/controller offline/);
});
