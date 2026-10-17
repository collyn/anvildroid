const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {create} = require('../crates/anvildroid-gui/src-frontend/runtime-tools.js');
const FIRST='r-'+'1'.repeat(32), SECOND='r-'+'2'.repeat(32), DIGEST='a'.repeat(64), OTHER_DIGEST='b'.repeat(64);
class Element {
  constructor(tag) { this.tagName=tag;this.children=[];this.listeners={};this._text='';this._value='';this._selected=false;this.disabled=false;this.className='';this.dataset={}; }
  set innerHTML(_) { throw new Error('Dynamic HTML is forbidden'); }
  set textContent(value) { this._text=String(value);this.replaceChildren(); }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  get parentNode() { return this.parent||null; }
  get value() { return this.tagName==='select' ? (this.children.find(child=>child.selected)||this.children[0])?.value||'' : this._value; }
  set value(value) { if(this.tagName==='select') { for(const child of this.children)child._selected=child.value===value; } else this._value=value; }
  get selected() { return this._selected; }
  set selected(value) { this._selected=value;if(value&&this.parent?.tagName==='select')for(const child of this.parent.children)if(child!==this)child._selected=false; }
  append(...children) { for(const child of children) { child.remove();child.parent=this;this.children.push(child); } }
  appendChild(child) { this.append(child);return child; }
  removeChild(child) { const index=this.children.indexOf(child);if(index>=0){this.children.splice(index,1);child.parent=null;}return child; }
  remove() { this.parent?.removeChild(this); }
  replaceChildren(...children) { for(const child of this.children)child.parent=null;this.children=[];this.append(...children); }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(type,handler) { (this.listeners[type]||=[]).push(handler); }
  async fire(type='click') { if(type==='click'&&this.disabled)return;await Promise.all((this.listeners[type]||[]).map(handler=>handler({preventDefault(){}}))); }
  find(predicate) { return predicate(this)?this:this.children.map(child=>child.find(predicate)).find(Boolean); }
  all(predicate) { return [...(predicate(this)?[this]:[]),...this.children.flatMap(child=>child.all(predicate))]; }
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function fixture(options={}) {
  const body=new Element('body'),host=new Element('div'),calls=[],invocations=[],choices=[],messages=[];body.append(host);
  const ui=create({document:{body,createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}},
    request:async payload=>{calls.push(payload);throw new Error('Mount must not request runtime data');},
    invoke:async(name,args)=>{invocations.push({name,args});return options.invoke?options.invoke(name,args):{runtime_id:args.id,bytes:321};},
    chooseReport:async name=>{choices.push(name);return options.chooseReport?options.chooseReport(name):'/tmp/report.json';},
    toast:(message,type)=>messages.push({message,type})});
  const diagnostics=()=>body.find(node=>node.className.split(' ').includes('runtime-diagnostic-export'));
  const button=(_text,section=diagnostics())=>section.find(node=>node.tagName==='button'&&/^Save report/.test(node.textContent));
  return {ui,body,host,calls,invocations,choices,messages,diagnostics,button,
    notice:(section=diagnostics())=>section.find(node=>node.role==='status'),
    logs:()=>diagnostics().find(node=>node.tagName==='input'),
    mount:(value={id:FIRST,name:'First runtime'})=>ui.mount(host,value)};
}

test('diagnostic mount is inert and logs are opt-in, reset on runtime change',async()=>{
  const f=fixture();f.mount({id:FIRST});
  assert.equal(f.logs().checked,false);assert.equal(f.calls.length,0);assert.equal(f.invocations.length,0);assert.equal(f.choices.length,0);
  f.logs().checked=true;f.mount({id:SECOND});assert.equal(f.logs().checked,false);
  await f.button('Save report…',f.diagnostics()).fire();
  assert.deepEqual(f.invocations,[{name:'export_runtime_diagnostics',args:{id:SECOND,path:'/tmp/report.json',includeLogs:false}}]);
  assert.match(f.choices[0],new RegExp(SECOND));
});

test('explicit diagnostic save captures selected runtime, file path and log choice',async()=>{
  const f=fixture();f.mount({id:FIRST});f.logs().checked=true;
  await f.button('Save report…',f.diagnostics()).fire();
  assert.deepEqual(f.invocations[0],{name:'export_runtime_diagnostics',args:{id:FIRST,path:'/tmp/report.json',includeLogs:true}});
  assert.equal(f.messages[0].type,'success');assert.match(f.notice(f.diagnostics()).textContent,/Report saved/);
  assert.equal(f.calls.length,0);
});

test('cancelled report picker does not collect or save diagnostics',async()=>{
  const f=fixture({chooseReport:()=>null});f.mount({id:FIRST});
  await f.button('Save report…',f.diagnostics()).fire();
  assert.equal(f.invocations.length,0);assert.equal(f.messages.length,0);assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);
});

test('diagnostic export is serialized and controls stay disabled during picker and save',async()=>{
  const picker=deferred(),save=deferred();const f=fixture({chooseReport:()=>picker.promise,invoke:()=>save.promise});
  f.mount({id:FIRST});const button=f.button('Save report…',f.diagnostics());const task=button.fire();
  assert.equal(button.disabled,true);assert.equal(f.logs().disabled,true);await button.fire();assert.equal(f.choices.length,1);
  picker.resolve('/tmp/chosen.json');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.invocations.length,1);assert.equal(button.disabled,true);
  save.resolve({runtime_id:FIRST,bytes:123});await task;assert.equal(button.disabled,false);assert.equal(f.logs().disabled,false);
});

test('diagnostic identity and byte-count validation reject malformed success reports',async()=>{
  for(const result of [null,{runtime_id:SECOND,bytes:12},{runtime_id:FIRST,bytes:0},{runtime_id:FIRST,bytes:-1},
    {runtime_id:FIRST,bytes:NaN},{runtime_id:FIRST,bytes:Infinity},{runtime_id:FIRST,bytes:'10'},
    {runtime_id:FIRST,bytes:1.5},{runtime_id:FIRST,bytes:Number.MAX_SAFE_INTEGER+1}]) {
    const f=fixture({invoke:()=>result});f.mount({id:FIRST});await f.button('Save report…',f.diagnostics()).fire();
    assert.equal(f.messages.length,0,JSON.stringify(result));assert.match(f.notice(f.diagnostics()).textContent,/did not confirm/);
    assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);
  }
});

test('diagnostic picker and invocation errors render as text and release busy state',async()=>{
  for(const stage of ['chooseReport','invoke']) {
    const f=fixture({[stage]:()=>{throw new Error('<img src=x onerror=steal()>');}});f.mount({id:FIRST});
    await f.button('Save report…',f.diagnostics()).fire();
    assert.match(f.notice(f.diagnostics()).textContent,/<img src=x onerror=steal\(\)>/);
    assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);assert.equal(f.messages.length,0);
  }
});

test('diagnostic deactivate during picker prevents collection after picker resolves',async()=>{
  const pending=deferred();const f=fixture({chooseReport:()=>pending.promise});f.mount({id:FIRST});
  const task=f.button('Save report…',f.diagnostics()).fire();f.ui.deactivate();
  pending.resolve('/tmp/chosen.json');await task;assert.equal(f.invocations.length,0);assert.equal(f.messages.length,0);
});

test('stale diagnostic completion cannot overwrite, toast or disable a replacement runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({invoke:()=>pending.promise});f.mount({id:FIRST});
    const task=f.button('Save report…',f.diagnostics()).fire();await new Promise(resolve=>setImmediate(resolve));
    f.ui.deactivate();f.mount({id:SECOND});
    if(fail)pending.reject(new Error('old failure'));else pending.resolve({runtime_id:FIRST,bytes:123});await task;
    assert.equal(f.notice(f.diagnostics()).textContent,'');assert.equal(f.messages.length,0);
    assert.equal(f.button('Save report…',f.diagnostics()).disabled,false);
  }
});

test('inline report export has one accessible section and no dialog or extra action',()=>{
  const f=fixture();f.mount();
  const section=f.diagnostics();assert.ok(section);
  assert.ok(section.className.split(' ').includes('runtime-inline-feature'));
  assert.equal(section.find(n=>n.tagName==='h4').textContent,'Export report');
  assert.equal(section.all(n=>n.tagName==='button').length,1);
  assert.match(section.find(n=>n.tagName==='label').textContent,/logs/i);
  assert.equal(f.body.all(n=>n.tagName==='dialog').length,0);
  assert.equal(f.calls.length+f.invocations.length+f.choices.length,0);
});

test('same runtime reparent preserves opt-in, completed status and stable export section',async()=>{
  const f=fixture();f.mount();const section=f.diagnostics();f.logs().checked=true;
  await f.button().fire();const status=f.notice().textContent;
  const replacement=new Element('div');f.body.append(replacement);
  f.ui.mount(replacement,{id:FIRST,name:'Renamed runtime'});
  assert.equal(f.diagnostics(),section);assert.equal(section.parentNode,replacement);
  assert.equal(f.logs().checked,true);assert.equal(f.notice().textContent,status);
  assert.equal(f.choices.length,1);assert.equal(f.invocations.length,1);
});

test('same runtime rerender while picker or export is pending preserves serialization',async()=>{
  for(const stage of ['picker','export']) {
    const pending=deferred();const f=fixture(stage==='picker'?{chooseReport:()=>pending.promise}:{invoke:()=>pending.promise});
    f.mount();f.logs().checked=true;const section=f.diagnostics(),task=f.button().fire();
    await new Promise(resolve=>setImmediate(resolve));
    const replacement=new Element('div');f.body.append(replacement);f.ui.mount(replacement,{id:FIRST});
    assert.equal(f.diagnostics(),section);assert.equal(f.logs().checked,true);
    assert.equal(f.button().disabled,true);assert.equal(f.logs().disabled,true);
    await f.button().fire();assert.equal(f.choices.length,1);
    pending.resolve(stage==='picker'?'/tmp/chosen.json':{runtime_id:FIRST,bytes:321});await task;
    assert.equal(f.invocations.length,1);assert.equal(f.messages.length,1);
    assert.equal(f.button().disabled,false);assert.equal(f.logs().disabled,false);
  }
});

test('deactivate detaches section, invalidates errors and resets even when same runtime returns',async()=>{
  for(const stage of ['picker','export']) {
    for(const fail of [false,true]) {
      const pending=deferred();const f=fixture(stage==='picker'?{chooseReport:()=>pending.promise}:{invoke:()=>pending.promise});
      f.mount();f.logs().checked=true;const section=f.diagnostics(),task=f.button().fire();
      await new Promise(resolve=>setImmediate(resolve));f.ui.deactivate();
      assert.equal(section.parentNode,null);assert.equal(f.diagnostics(),undefined);
      f.mount({id:FIRST});assert.equal(f.logs().checked,false);assert.equal(f.notice().textContent,'');
      if(fail)pending.reject(new Error('stale failure'));
      else pending.resolve(stage==='picker'?'/tmp/stale.json':{runtime_id:FIRST,bytes:321});
      await task;assert.equal(f.messages.length,0);assert.equal(f.notice().textContent,'');
      assert.equal(f.button().disabled,false);assert.equal(f.logs().disabled,false);
      assert.equal(f.invocations.length,stage==='picker'?0:1);
    }
  }
});

test('different runtime during picker cannot start an export for the previous runtime',async()=>{
  const pending=deferred(),f=fixture({chooseReport:()=>pending.promise});f.mount();
  const task=f.button().fire();f.mount({id:SECOND});
  assert.equal(f.logs().checked,false);assert.equal(f.button().disabled,false);
  pending.resolve('/tmp/stale.json');await task;
  assert.equal(f.invocations.length,0);assert.equal(f.messages.length,0);assert.equal(f.notice().textContent,'');
});

const mainPath='crates/anvildroid-gui/src-frontend/main.js';
function armFixture({cached=true,downloadDigest=DIGEST,downloadError=false,engine='libndk',digest=DIGEST}={}) {
  const main=fs.readFileSync(mainPath,'utf8'),start=main.indexOf('const ARM_BUILTIN_SHA =');
  const end=main.indexOf("document.addEventListener('change'",start);
  assert.ok(start>=0&&end>start,'Retained ARM installer entrypoints must exist');
  const calls=[],imports=[],messages=[],draft=new Map();let renders=0;
  const record={id:FIRST,name:'First runtime',state:'Stopped',arm:{supported:true,enabled:true,available:true,provider:'libndk',archive_sha256:DIGEST,
    sources:[{engine:'libndk',version:'0.2.3',sha256:DIGEST,installed:cached,label:'<img src=x onerror=steal()>',android:'Android 13 only'},
      {engine:'libndk',version:'0.2.4',sha256:OTHER_DIGEST,installed:true,label:'Other cached version'}]}};
  const ctx=vm.createContext({runtimeArmDraft:draft,controllerMutation:false,controllerError:'',
    runtimeRecord:id=>id===record.id?record:null,escapeHtml:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
    document:{querySelector:selector=>({value:selector.includes('arm-engine')?engine:digest})},
    renderController:()=>renders++,showAppToast:(...args)=>messages.push(args),
    mutateController:async payload=>calls.push({...payload}),
    tauriInvoke:async(name,args)=>{imports.push({name,args:{...args}});if(downloadError)throw new Error('Download failed');return {sha256:downloadDigest};}});
  vm.runInContext(main.slice(start,end)+'\nglobalThis.api={renderRuntimeArm,installRuntimeArm,runtimeArmSources};',ctx);
  return {api:ctx.api,ctx,record,calls,imports,messages,draft,renders:()=>renders};
}

test('retained managed ARM installer selects exact cached version without a download',async()=>{
  const f=armFixture({digest:OTHER_DIGEST});f.draft.set(FIRST,{engine:'libndk',sha256:OTHER_DIGEST});
  const markup=f.api.renderRuntimeArm(f.record,false);
  assert.match(markup,/data-arm-engine=/);assert.match(markup,/data-arm-select=/);
  assert.equal((markup.match(/data-controller-op="arm_set"/g)||[]).length,1);
  assert.match(markup,/Install selected version/);assert.match(markup,/0\.2\.4/);
  await f.api.installRuntimeArm(FIRST);
  assert.deepEqual(f.calls,[{op:'arm_set',id:FIRST,enabled:true,archive_sha256:OTHER_DIGEST}]);
  assert.equal(f.imports.length,0);assert.equal(f.draft.has(FIRST),false);
});

test('uncached ARM version downloads selected digest and verifies checksum before applying',async()=>{
  const f=armFixture({cached:false});assert.match(f.api.renderRuntimeArm(f.record,false),/Download &amp; install|Download & install/);
  await f.api.installRuntimeArm(FIRST);
  assert.deepEqual(f.imports,[{name:'import_arm_translation',args:{source:DIGEST,sourceKind:'builtin',expectedSha256:''}}]);
  assert.deepEqual(f.calls,[{op:'arm_set',id:FIRST,enabled:true,archive_sha256:DIGEST}]);
  for(const options of [{cached:false,downloadDigest:OTHER_DIGEST},{cached:false,downloadError:true}]) {
    const bad=armFixture(options);await bad.api.installRuntimeArm(FIRST);
    assert.equal(bad.calls.length,0);assert.equal(bad.messages.length,1);assert.match(bad.ctx.controllerError,/ARM installation failed/);
  }
});

test('ARM disable omits archive selection and installer refuses unknown selections or busy mutations',async()=>{
  const disabled=armFixture({engine:'none',digest:'none'});await disabled.api.installRuntimeArm(FIRST);
  assert.deepEqual(disabled.calls,[{op:'arm_set',id:FIRST,enabled:false}]);assert.equal(disabled.imports.length,0);
  const unknown=armFixture({digest:'f'.repeat(64)});await unknown.api.installRuntimeArm(FIRST);assert.equal(unknown.calls.length+unknown.imports.length,0);
  const busy=armFixture();busy.ctx.controllerMutation=true;await busy.api.installRuntimeArm(FIRST);assert.equal(busy.calls.length+busy.imports.length,0);
});

test('managed ARM retains metadata, compatibility notes and lifecycle gating',()=>{
  const f=armFixture(),arm=f.record.arm;
  const html=f.api.renderRuntimeArm(f.record,false);assert.match(html,/Compatibility notes/);assert.match(html,/Android 13 only/);
  assert.match(html,/SHA-256/);assert.ok(html.includes(DIGEST));assert.match(html,/Cached archive/);
  assert.match(html,/checksum verified on installation/);assert.match(html,/saved runtime configuration/);
  arm.provider='<img src=x onerror=steal()>';assert.doesNotMatch(f.api.renderRuntimeArm(f.record,false),/<img/);
  assert.match(f.api.renderRuntimeArm({...f.record,state:'Starting'},false),/data-arm-engine="[^"]+" disabled/);
  assert.match(f.api.renderRuntimeArm({...f.record,state:'Running'},false),/restart/);
  assert.match(f.api.renderRuntimeArm({...f.record,arm:{inherited:true,supported:false}},false),/provided by this image/);
});

test('Default keeps its supported ARM toggle without managed archive selectors',()=>{
  const f=armFixture();const html=f.api.renderRuntimeArm({...f.record,id:'default'},false);
  assert.doesNotMatch(html,/data-arm-engine|data-arm-select|arm_sources|Import another version/);
  assert.match(html,/data-controller-op="arm_set"/);
});

test('runtime settings embed sharing in Storage and report export in Diagnostics without duplicate Components UI',()=>{
  const main=fs.readFileSync(mainPath,'utf8'),html=fs.readFileSync('crates/anvildroid-gui/src-frontend/index.html','utf8');
  assert.doesNotMatch(main+html,/runtime-components-open|runtime-components-dialog|runtime-diagnostics-open|runtime-diagnostics-dialog|runtime-shares-open/);
  assert.doesNotMatch(main,/runtimeTools\?\.components|runtimeTools\.components/);
  assert.match(main,/data-runtime-shares=/);assert.match(main,/data-runtime-diagnostics=/);
  assert.match(main,/\.mount\(/);assert.match(main,/\.deactivate\(/);
  for(const name of ['storage','health'])assert.match(main,new RegExp(':'.concat(name)));
});

test('Default ARM toggle uses existing configuration only and cannot restore unavailable libraries',async()=>{
  for(const enabled of [false,true]) {
    const f=armFixture();f.record.id='default';f.record.arm.enabled=enabled;f.record.arm.restore_available=true;
    await f.api.installRuntimeArm('default');
    assert.deepEqual(f.calls,[{op:'arm_set',id:'default',enabled:!enabled}]);assert.equal(f.imports.length,0);
    const html=f.api.renderRuntimeArm(f.record,false);
    assert.match(html,/Version and archive checksum are unknown/);assert.match(html,/existing system libraries/);
  }
  for(const change of [{supported:false},{enabled:false,restore_available:false}]) {
    const f=armFixture();f.record.id='default';Object.assign(f.record.arm,change);
    await f.api.installRuntimeArm('default');assert.equal(f.calls.length+f.imports.length,0);
    assert.match(f.api.renderRuntimeArm(f.record,false),/data-controller-id="default" disabled/);
  }
});

function inlineSettingsFixture() {
  const main=fs.readFileSync(mainPath,'utf8'),start=main.indexOf('function syncRuntimeSettingsFeatures()');
  const end=main.indexOf('\nlet existingRuntime',start);assert.ok(start>=0&&end>start);
  const events=[],hosts={shares:{open:true},tools:{open:true}},record={id:FIRST,state:'Stopped'};
  for(const host of Object.values(hosts))host.closest=selector=>{assert.equal(selector,'details[data-settings-key]');return host;};
  const tools=Object.fromEntries(['shares','tools'].map(kind=>[kind,{
    mount:(host,value)=>events.push({kind,op:'mount',host,id:value.id}),
    deactivate:()=>events.push({kind,op:'deactivate'})
  }]));
  const ctx=vm.createContext({currentPage:'runtime-settings',runtimeSettingsId:FIRST,runtimeFeatureTools:null,
    runtimeRecord:id=>id===record.id?record:null,
    featureTools:()=>{events.push({op:'create'});ctx.runtimeFeatureTools=tools;return tools;},
    document:{getElementById:id=>{assert.equal(id,'page-runtime-settings');return {
      querySelector:selector=>selector===`[data-runtime-shares="${record.id}"]`?hosts.shares:
        selector===`[data-runtime-diagnostics="${record.id}"]`?hosts.tools:null
    };}}});
  vm.runInContext(main.slice(start,end)+'\nglobalThis.sync=syncRuntimeSettingsFeatures;',ctx);
  return {ctx,record,hosts,events,sync:ctx.sync};
}

test('inline settings feature sync mounts only the selected runtime with its section open',()=>{
  const f=inlineSettingsFixture();f.sync();
  assert.deepEqual(f.events.filter(e=>e.op==='mount').map(e=>[e.kind,e.id]),[['shares',FIRST],['tools',FIRST]]);
  assert.equal(f.events.find(e=>e.kind==='shares').host,f.hosts.shares);
  f.events.length=0;f.hosts.shares.open=false;f.sync();
  assert.deepEqual(f.events.filter(e=>e.kind).map(e=>[e.kind,e.op]),[['shares','deactivate'],['tools','mount']]);
  f.events.length=0;f.hosts.tools.open=false;f.sync();
  assert.deepEqual(f.events,[{kind:'shares',op:'deactivate'},{kind:'tools',op:'deactivate'}]);
});

test('inactive, collapsed, unavailable, loading, failed or deleting settings never leave features active',()=>{
  for(const change of [f=>f.ctx.currentPage='runtime',f=>f.ctx.runtimeSettingsId=SECOND,
      f=>f.record.unavailable=true,f=>f.record.refreshError='Lost worker',f=>f.record.detailsLoading=true,
      f=>f.record.deletion_pending=true,f=>{f.hosts.shares.open=f.hosts.tools.open=false;}]) {
    const fresh=inlineSettingsFixture();change(fresh);fresh.sync();assert.deepEqual(fresh.events,[],'Inactive sections must not create tools or IPC');
    const active=inlineSettingsFixture();active.sync();active.events.length=0;change(active);active.sync();
    assert.deepEqual(active.events,[{kind:'shares',op:'deactivate'},{kind:'tools',op:'deactivate'}]);
  }
});
