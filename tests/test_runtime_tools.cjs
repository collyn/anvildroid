const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('../crates/anvildroid-gui/src-frontend/runtime-tools.js');
const FIRST='r-'+'1'.repeat(32), SECOND='r-'+'2'.repeat(32), DIGEST='a'.repeat(64), OTHER_DIGEST='b'.repeat(64);
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this._text='';this._value='';this._selected=false;this.disabled=false;this.open=false; }
  set innerHTML(_) { throw new Error('Dynamic HTML is forbidden'); }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  get value() { return this.tagName==='select' ? (this.children.find(child=>child.selected)||this.children[0])?.value||'' : this._value; }
  set value(value) { if(this.tagName==='select') { for(const child of this.children)child._selected=child.value===value; } else this._value=value; }
  get selected() { return this._selected; }
  set selected(value) { this._selected=value; if(value&&this.parent?.tagName==='select')for(const child of this.parent.children)if(child!==this)child._selected=false; }
  append(...children) { for(const child of children) {child.parent=this;this.children.push(child);} }
  replaceChildren(...children) { this.children=[];this._text='';this.append(...children); }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(type,handler) { (this.listeners[type]||=[]).push(handler); }
  async fire(type='click') { if(type==='click'&&this.disabled)return; await Promise.all((this.listeners[type]||[]).map(handler=>handler({preventDefault(){}}))); }
  showModal() { this.open=true; }
  focus() {}
  close() { this.open=false; void this.fire('close'); }
  find(predicate) { return predicate(this)?this:this.children.map(child=>child.find(predicate)).find(Boolean); }
  all(predicate) { return [...(predicate(this)?[this]:[]),...this.children.flatMap(child=>child.all(predicate))]; }
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function pkg(change={}) { return {sha256:DIGEST,version:'0.2.3',label:'libndk 0.2.3',cached:true,verification:'verify_on_install',...change}; }
function arm(change={}) { return {id:'libndk',name:'libndk_translation',kind:'arm_translation',status:'available',installed:false,
  inherited:false,installed_sha256:null,installed_version:null,conflicts:['libhoudini'],architectures:['x86_64'],android_sdks:[33],
  can_enable:true,can_disable:false,state_source:'saved_configuration',observed_active:null,activation:'runtime_restart',experimental:true,
  reason:null,packages:[pkg()],...change}; }
function report(id=FIRST,change={}) { return {schema_version:1,runtime_id:id,revision:'c'.repeat(64),architecture:'x86_64',android_sdk:33,
  components:[arm(),{id:'gapps',name:'Google Play services',kind:'image',installed:null,status:'unknown',configured_image:null,
    conflicts:['microg'],can_enable:false,can_disable:false,action:'create_gapps_runtime',activation:'new_runtime',reason:'Use a separate GAPPS runtime'}],...change}; }
function fixture(options={}) {
  const body=new Element('body'),calls=[],invocations=[],choices=[],confirms=[],messages=[],images=[],refreshed=[];
  const ui=create({document:{body,createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}},
    request:async payload=>{calls.push(payload);return options.request?options.request(payload):report(payload.id);},
    invoke:async(name,args)=>{invocations.push({name,args});return options.invoke?options.invoke(name,args):{runtime_id:args.id,bytes:321};},
    chooseReport:async name=>{choices.push(name);return options.chooseReport?options.chooseReport(name):'/tmp/report.json';},
    confirm:async(...args)=>{confirms.push(args);return options.confirm?options.confirm(...args):true;},
    toast:(message,type)=>messages.push({message,type}),openImages:tab=>images.push(tab),refreshRuntime:async id=>refreshed.push(id)});
  const components=()=>body.find(node=>node.id==='runtime-components-dialog');
  const diagnostics=()=>body.find(node=>node.id==='runtime-diagnostics-dialog');
  const button=(text,dialog=components())=>dialog.find(node=>node.tagName==='button'&&node.textContent===text);
  return {ui,body,calls,invocations,choices,confirms,messages,images,refreshed,components,diagnostics,button,
    notice:(dialog=components())=>dialog.find(node=>node.role==='status'),
    logs:()=>diagnostics().find(node=>node.tagName==='input'),
    open:(change={})=>ui.components({id:FIRST,name:'First runtime',state:'Stopped',...change})};
}

test('components read only the selected runtime and use safe text rendering',async()=>{
  const hostile='<img src=x onerror=steal()> & "translator"';
  const f=fixture({request:payload=>report(payload.id,{components:[arm({name:hostile,packages:[pkg({label:hostile})]})]})});
  await f.open({name:'<script>Runtime</script>'});
  assert.deepEqual(f.calls,[{op:'components_get',id:FIRST}]);
  assert.ok(f.components().textContent.includes(hostile));
  assert.ok(f.components().textContent.includes('<script>Runtime</script>'));
  assert.ok(f.components().textContent.includes('saved configuration'));
  assert.equal(f.invocations.length,0);assert.equal(f.choices.length,0);
});

test('cached version selection sends selected digest, revision, confirmation and selected runtime',async()=>{
  const f=fixture();await f.open();
  await f.button('Enable selected version').fire();
  assert.equal(f.confirms.length,1);assert.match(f.confirms[0][0],/First runtime/);
  assert.deepEqual(f.calls[1],{op:'components_set',id:FIRST,component_id:'libndk',enabled:true,archive_sha256:DIGEST,revision:'c'.repeat(64)});
  assert.deepEqual(f.refreshed,[FIRST]);assert.equal(f.messages[0].type,'success');
  assert.match(f.notice().textContent,/Start this runtime/);
});

test('manually selected cached version uses its digest and latest returned revision',async()=>{
  const versions=[pkg(),pkg({sha256:OTHER_DIGEST,label:'Other cached version'})];
  const f=fixture({request:payload=>report(payload.id,{revision:(payload.op==='components_set'?'d':'c').repeat(64),components:[arm({can_disable:true,packages:versions})]})});
  await f.open();f.components().find(node=>node.tagName==='select').value=OTHER_DIGEST;
  await f.button('Enable selected version').fire();
  assert.equal(f.calls[1].archive_sha256,OTHER_DIGEST);
  await f.button('Disable').fire();assert.equal(f.calls[2].revision,'d'.repeat(64));
});

test('uncached first version cannot become implicit default when another version is cached',async()=>{
  const f=fixture({request:payload=>report(payload.id,{components:[arm({packages:[pkg({sha256:OTHER_DIGEST,cached:false}),pkg()]})]})});
  await f.open();
  const options=f.components().all(node=>node.tagName==='option');
  assert.equal(options[0].disabled,true);assert.equal(options[1].disabled,false);
  await f.button('Enable selected version').fire();
  assert.equal(f.calls[1].archive_sha256,DIGEST);
});

test('all uncached versions show download and do not enable or install implicitly',async()=>{
  const f=fixture({request:payload=>report(payload.id,{components:[arm({can_enable:false,status:'not_cached',packages:[pkg({cached:false})]})]})});
  await f.open();
  assert.equal(f.button('Enable selected version').disabled,true);
  assert.match(f.components().textContent,/download required/);
  await f.button('Enable selected version').fire();assert.equal(f.calls.length,1);
  await f.button('Download a catalog version…').fire();assert.deepEqual(f.images,['arm']);
  assert.equal(f.components().open,false);assert.equal(f.calls.length,1);
});

test('image workflow goes to GAPPS image library without changing installed runtime',async()=>{
  const f=fixture();await f.open();await f.button('Open image library').fire();
  assert.deepEqual(f.images,['gapps']);assert.equal(f.calls.length,1);assert.equal(f.components().open,false);
});

test('running and frozen components disable change controls while preserving read access',async()=>{
  for(const state of ['Running','Frozen','Starting']) {
    const f=fixture({request:payload=>report(payload.id,{components:[arm({can_disable:true})]})});
    await f.open({state});
    assert.equal(f.button('Enable selected version').disabled,true,state);
    assert.equal(f.button('Disable').disabled,true,state);
    assert.equal(f.components().find(node=>node.tagName==='select').disabled,true,state);
    await f.button('Enable selected version').fire();assert.equal(f.calls.length,1);
  }
});

test('disable sends no archive and cancellation performs no mutation',async()=>{
  const f=fixture({request:payload=>report(payload.id,{components:[arm({can_disable:true})]})});await f.open();
  await f.button('Disable').fire();assert.equal(f.calls[1].enabled,false);assert.equal(f.calls[1].archive_sha256,null);
  const cancelled=fixture({confirm:()=>false});await cancelled.open();await cancelled.button('Enable selected version').fire();
  assert.equal(cancelled.calls.length,1);assert.equal(cancelled.button('Enable selected version').disabled,false);
});

test('confirmation pending across close or replacement cannot mutate another runtime',async()=>{
  const pending=deferred();const f=fixture({confirm:()=>pending.promise});await f.open();
  const task=f.button('Enable selected version').fire();
  await f.open({id:SECOND,name:'Second runtime'});pending.resolve(true);await task;
  assert.equal(f.calls.length,2);assert.equal(f.calls[1].id,SECOND);assert.equal(f.messages.length,0);
});

test('old component read replies and errors cannot overwrite replacement runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({request:payload=>payload.id===FIRST?pending.promise:report(SECOND)});
    const old=f.open();await f.open({id:SECOND,name:'Second runtime'});
    if(fail)pending.reject(new Error('stale error'));else pending.resolve(report());await old;
    assert.match(f.components().textContent,/Second runtime/);assert.doesNotMatch(f.notice().textContent,/stale error/);
    await f.button('Enable selected version').fire();assert.equal(f.calls.at(-1).id,SECOND);
  }
});

test('late component mutation cannot toast or refresh replaced dialog',async()=>{
  const pending=deferred();const f=fixture({request:payload=>payload.op==='components_set'?pending.promise:report(payload.id)});
  await f.open();const mutation=f.button('Enable selected version').fire();
  await new Promise(resolve=>setImmediate(resolve));await f.open({id:SECOND,name:'Second runtime'});
  pending.resolve(report());await mutation;
  assert.equal(f.messages.length,0);assert.equal(f.refreshed.length,0);assert.match(f.components().textContent,/Second runtime/);
});

test('cross-runtime and malformed component reports fail closed without render exceptions',async()=>{
  const malformed=[null,report(SECOND),report(FIRST,{schema_version:2}),report(FIRST,{revision:''}),
    report(FIRST,{components:[null]}),report(FIRST,{components:[arm({packages:[null]})]}),
    report(FIRST,{components:[arm({can_enable:'false'})]}),report(FIRST,{components:[arm({packages:[pkg({sha256:'bad'})]})]})];
  for(const value of malformed) {
    const f=fixture({request:()=>value});await assert.doesNotReject(f.open(),JSON.stringify(value));
    const actions=f.components().all(node=>node.tagName==='button'&&node.textContent==='Enable selected version');
    assert.ok(actions.every(button=>button.disabled),JSON.stringify(value));
    assert.match(f.notice().textContent,/response|invalid|malformed/i);
    assert.equal(f.calls.length,1);
  }
});

test('failed component refresh removes prior actionable report until a successful reload',async()=>{
  let fail=false;const f=fixture({request:payload=>{if(fail)throw new Error('Worker unavailable');return report(payload.id);}});
  await f.open();fail=true;await f.button('Refresh').fire();
  assert.match(f.notice().textContent,/Worker unavailable/);
  const actions=f.components().all(node=>node.tagName==='button'&&node.textContent==='Enable selected version');
  assert.ok(actions.every(button=>button.disabled));
  assert.equal(f.button('Refresh').disabled,false);fail=false;await f.button('Refresh').fire();
  assert.equal(f.button('Enable selected version').disabled,false);
});

test('failed component mutation invalidates cached report and requires a successful refresh',async()=>{
  const f=fixture({request:payload=>{if(payload.op==='components_set')throw new Error('Runtime changed');return report(payload.id);}});
  await f.open();await f.button('Enable selected version').fire();
  assert.match(f.notice().textContent,/Runtime changed.*Refresh before retrying/);
  const actions=f.components().all(node=>node.tagName==='button'&&node.textContent==='Enable selected version');
  assert.ok(actions.every(button=>button.disabled));assert.equal(f.messages.length,0);
  await f.button('Refresh').fire();assert.equal(f.button('Enable selected version').disabled,false);
});

test('diagnostics open is inert and logs are opt-in, reset on every open',async()=>{
  const f=fixture();f.ui.diagnostics({id:FIRST});
  assert.equal(f.logs().checked,false);assert.equal(f.calls.length,0);assert.equal(f.invocations.length,0);assert.equal(f.choices.length,0);
  f.logs().checked=true;f.ui.diagnostics({id:SECOND});assert.equal(f.logs().checked,false);
  await f.button('Save report…',f.diagnostics()).fire();
  assert.deepEqual(f.invocations,[{name:'export_runtime_diagnostics',args:{id:SECOND,path:'/tmp/report.json',includeLogs:false}}]);
  assert.match(f.choices[0],new RegExp(SECOND));
});

test('explicit diagnostic save captures selected runtime, file path and log choice',async()=>{
  const f=fixture();f.ui.diagnostics({id:FIRST});f.logs().checked=true;
  await f.button('Save report…',f.diagnostics()).fire();
  assert.deepEqual(f.invocations[0],{name:'export_runtime_diagnostics',args:{id:FIRST,path:'/tmp/report.json',includeLogs:true}});
  assert.equal(f.messages[0].type,'success');assert.match(f.notice(f.diagnostics()).textContent,/Report saved/);
  assert.equal(f.calls.length,0);
});

test('cancelled report picker does not collect or save diagnostics',async()=>{
  const f=fixture({chooseReport:()=>null});f.ui.diagnostics({id:FIRST});
  await f.button('Save report…',f.diagnostics()).fire();
  assert.equal(f.invocations.length,0);assert.equal(f.messages.length,0);assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);
});

test('diagnostic export is serialized and controls stay disabled during picker and save',async()=>{
  const picker=deferred(),save=deferred();const f=fixture({chooseReport:()=>picker.promise,invoke:()=>save.promise});
  f.ui.diagnostics({id:FIRST});const button=f.button('Save report…',f.diagnostics());const task=button.fire();
  assert.equal(button.disabled,true);assert.equal(f.logs().disabled,true);await button.fire();assert.equal(f.choices.length,1);
  picker.resolve('/tmp/chosen.json');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.invocations.length,1);assert.equal(button.disabled,true);
  save.resolve({runtime_id:FIRST,bytes:123});await task;assert.equal(button.disabled,false);assert.equal(f.logs().disabled,false);
});

test('diagnostic identity and byte-count validation reject malformed success reports',async()=>{
  for(const result of [null,{runtime_id:SECOND,bytes:12},{runtime_id:FIRST,bytes:0},{runtime_id:FIRST,bytes:-1},
    {runtime_id:FIRST,bytes:NaN},{runtime_id:FIRST,bytes:Infinity},{runtime_id:FIRST,bytes:'10'},
    {runtime_id:FIRST,bytes:1.5},{runtime_id:FIRST,bytes:Number.MAX_SAFE_INTEGER+1}]) {
    const f=fixture({invoke:()=>result});f.ui.diagnostics({id:FIRST});await f.button('Save report…',f.diagnostics()).fire();
    assert.equal(f.messages.length,0,JSON.stringify(result));assert.match(f.notice(f.diagnostics()).textContent,/did not confirm/);
    assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);
  }
});

test('diagnostic picker and invocation errors render as text and release busy state',async()=>{
  for(const stage of ['chooseReport','invoke']) {
    const f=fixture({[stage]:()=>{throw new Error('<img src=x onerror=steal()>');}});f.ui.diagnostics({id:FIRST});
    await f.button('Save report…',f.diagnostics()).fire();
    assert.match(f.notice(f.diagnostics()).textContent,/<img src=x onerror=steal\(\)>/);
    assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);assert.equal(f.messages.length,0);
  }
});

test('diagnostic close during picker prevents collection after picker resolves',async()=>{
  const pending=deferred();const f=fixture({chooseReport:()=>pending.promise});f.ui.diagnostics({id:FIRST});
  const task=f.button('Save report…',f.diagnostics()).fire();await f.button('Close',f.diagnostics()).fire();
  pending.resolve('/tmp/chosen.json');await task;assert.equal(f.invocations.length,0);assert.equal(f.messages.length,0);
});

test('stale diagnostic completion cannot overwrite, toast or disable a replacement runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({invoke:()=>pending.promise});f.ui.diagnostics({id:FIRST});
    const task=f.button('Save report…',f.diagnostics()).fire();await new Promise(resolve=>setImmediate(resolve));
    await f.button('Close',f.diagnostics()).fire();f.ui.diagnostics({id:SECOND});
    if(fail)pending.reject(new Error('old failure'));else pending.resolve({runtime_id:FIRST,bytes:123});await task;
    assert.equal(f.notice(f.diagnostics()).textContent,'');assert.equal(f.messages.length,0);
    assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);
  }
});
