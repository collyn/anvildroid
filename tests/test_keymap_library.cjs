const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const model=require('../crates/anvildroid-gui/src-frontend/keymap-drafts.js');
const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/keymap-library.js','utf8');
class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.events={};this.attributes={};this.value='';this.disabled=false;this.text='';}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(c=>c.textContent).join(' ');}
  appendChild(child){if(child.tag==='#fragment')this.children.push(...child.children);else this.children.push(child);return child;}
  replaceChildren(...children){this.children=[];this.text='';for(const child of children)this.appendChild(child);}
  addEventListener(name,callback){this.events[name]=callback;}
  setAttribute(name,value){this.attributes[name]=value;}
}
function setup(){
  const elements=new Map(),calls=[],edits=[],exports=[],dialogs=[],states={managed:[],default:{id:'default',state:'Running'},profiles:new Map(),schemes:null,exportPath:null,exportError:null,failList:false};
  const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const invoke=async(command,payload)=>{
    if(command==='export_keymap_scheme'){
      exports.push(payload);if(states.exportError)throw states.exportError;
      return {runtime_id:payload.id,package:payload.package,scheme_id:payload.schemeId,path:payload.path,bytes:128};
    }
    const {request}=payload;
    calls.push(request);
    if(request.op==='list'){if(states.failList)throw new Error('Controller unavailable');return states.managed;}
    if(request.op==='refresh')return states.default;
    if(request.op==='keymaps'){const report=states.profiles.get(request.id);if(report instanceof Error)throw report;return report||{runtime_id:request.id,profiles:[],invalid_count:0};}
    if(request.op==='keymap_scheme'){if(states.schemes instanceof Error)throw states.schemes;return states.schemes;}
    throw new Error('Unexpected operation');
  };
  const ctx=vm.createContext({window:{AnvilKeymapDrafts:model,AnvilKeymapActions:{open:async(...args)=>edits.push(args)},localStorage:{length:0},__TAURI__:{core:{invoke},dialog:{save:async options=>{dialogs.push(options);return states.exportPath;}}},addEventListener(){}},document:{getElementById:el,createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('#fragment')}});
  vm.runInContext(source,ctx);
  return {el,calls,edits,exports,dialogs,states,api:ctx.window.AnvilKeymapLibrary};
}
function report(runtime,pkg='com.test.game'){
  return {runtime_id:runtime,invalid_count:0,profiles:[{runtime,package:pkg,bindings:[{evdev:30,nx:100,ny:200,kind:'tap'}],source:'runtime'}]};
}
function descendants(element,tag){return element.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...descendants(c,tag)]);}
test('Controls entry stays library-only and diagnostics/shortcuts start collapsed',()=>{
  const html=fs.readFileSync('crates/anvildroid-gui/src-frontend/index.html','utf8');
  assert.doesNotMatch(html,/id="keymap-editor-development"|src="keymap-editor.js"|Refresh saved keymaps|not active/);
  assert.match(html,/<details class="controls-details">\s*<summary>Touch diagnostics/);
  const {calls,edits}=setup();assert.equal(calls.length,0);assert.equal(edits.length,0);
});
test('Default Frozen profiles load alongside managed profiles, with scoped editing and filter',async()=>{
  const {api,el,states,calls,edits}=setup();states.default.state='Frozen';
  states.managed=[{id:'r-one',name:'Gaming',state:'Running'}];
  states.profiles.set('default',report('default'));states.profiles.set('r-one',report('r-one'));
  await api.refresh();
  assert.ok(calls.some(r=>r.op==='keymaps'&&r.id==='default'));
  assert.match(el('keymap-saved-list').textContent,/Default runtime/);assert.match(el('keymap-saved-list').textContent,/Gaming/);
  assert.doesNotMatch(el('keymap-saved-list').textContent,/not active|Legacy/);
  assert.ok(el('probe-runtime').children.some(o=>o.value==='default'));
  el('probe-runtime').value='default';el('probe-runtime').events.change();assert.equal(el('install-touch-probe').disabled,false);
  el('keymap-runtime-filter').value='default';el('keymap-runtime-filter').events.change();
  assert.doesNotMatch(el('keymap-saved-list').textContent,/Gaming/);
  const edit=descendants(el('keymap-saved-list'),'button')[0];await edit.events.click();
  assert.deepEqual(edits,[['default','com.test.game']]);
});
test('Managed inventory failure does not block Default, failed refresh retains profiles',async()=>{
  const {api,el,states}=setup();states.failList=true;states.profiles.set('default',report('default'));
  await api.refresh();assert.match(el('keymap-saved-list').textContent,/com.test.game/);
  states.profiles.set('default',new Error('Read timed out'));await api.refresh();
  assert.match(el('keymap-saved-list').textContent,/com.test.game/);assert.match(el('keymap-saved-list').textContent,/Read timed out/);
  states.profiles.set('default',{runtime_id:'default',profiles:[],invalid_count:0});await api.refresh();
  assert.doesNotMatch(el('keymap-saved-list').textContent,/com.test.game/);
  assert.equal(el('keymap-list-refresh').disabled,false);assert.equal(el('keymap-saved-list').attributes['aria-busy'],'false');
});
test('Cached stopped profiles remain visible, but editing is disabled',async()=>{
  const {api,el,states}=setup();states.profiles.set('default',report('default'));await api.refresh();
  states.default.state='Stopped';await api.refresh();
  assert.match(el('keymap-saved-list').textContent,/com.test.game/);
  assert.equal(descendants(el('keymap-saved-list'),'button')[0].disabled,true);
  assert.equal(el('install-touch-probe').disabled,true);
});
test('Invalid native scope, duplicate packages or bindings never replace cached profiles',async()=>{
  const {api,el,states}=setup();states.profiles.set('default',report('default'));await api.refresh();
  for(const mutate of [r=>r.runtime_id='r-other',r=>r.profiles[0].runtime='r-other',r=>r.profiles[0].bindings[0].nx=65536,r=>r.profiles.push(r.profiles[0])]){
    const bad=report('default','com.bad.profile');mutate(bad);states.profiles.set('default',bad);await api.refresh();
    assert.match(el('keymap-saved-list').textContent,/com.test.game/);assert.doesNotMatch(el('keymap-saved-list').textContent,/com.bad.profile/);
  }
});
test('Late refresh cannot overwrite a newer runtime snapshot',async()=>{
  const {api,el,states}=setup();let resolveOld;
  states.profiles.set('default',report('default','com.old.app'));
  // Hold only the first runtime inventory; the second refresh completes first.
  const old=states.profiles.get('default');states.profiles.set('default',new Promise(resolve=>{resolveOld=resolve;}));
  const first=api.refresh();await new Promise(resolve=>setImmediate(resolve));
  states.profiles.set('default',report('default','com.new.app'));await api.refresh();
  resolveOld(old);await first;
  assert.match(el('keymap-saved-list').textContent,/com.new.app/);assert.doesNotMatch(el('keymap-saved-list').textContent,/com.old.app/);
});

test('Advanced runtime profiles validate and show truthful type and bounded parameters',async()=>{
  const {api,el,states}=setup();
  const result=report('default');
  result.profiles[0].bindings=['wasd','swipe','repeat','skill','macro','aim'].map((kind,i)=>({
    evdev:i===0?17:100+i,nx:100,ny:200,kind,end_nx:400,end_ny:500,duration_ms:250,repeat_count:3}));
  states.profiles.set('default',result);await api.refresh();
  const titles=descendants(el('keymap-saved-list'),'kbd').map(c=>c.title);
  assert.deepEqual(titles,['WASD joystick','Swipe · 250 ms · endpoint 400, 500','Repeated click · 250 ms',
    'Skill cast','Bounded macro · 250 ms · 3 clicks · endpoint 400, 500','Aim / left-click fire · endpoint 400, 500']);
  for(const mutate of [b=>b.kind='unknown',b=>b.duration_ms=49,b=>b.duration_ms=NaN,
                      b=>b.repeat_count=21,b=>b.end_nx=65536,b=>b.duration_ms=5000]){
    const bad=report('default','com.bad.advanced');
    bad.profiles[0].bindings=[{evdev:100,nx:0,ny:0,kind:'macro',end_nx:0,end_ny:0,duration_ms:250,repeat_count:3}];
    mutate(bad.profiles[0].bindings[0]);states.profiles.set('default',bad);await api.refresh();
    assert.doesNotMatch(el('keymap-saved-list').textContent,/com.bad.advanced/);
    assert.match(el('keymap-saved-list').textContent,/com.test.game/);
  }
  const conflict=report('default','com.bad.advanced');conflict.profiles[0].bindings.push({
    evdev:17,nx:0,ny:0,kind:'wasd',end_nx:100,end_ny:100,duration_ms:250,repeat_count:3});
  states.profiles.set('default',conflict);await api.refresh();
  assert.doesNotMatch(el('keymap-saved-list').textContent,/com.bad.advanced/);
});

test('V3 sources display mouse, pointer and look settings and reject invalid regions',async()=>{
  const {api,el,states}=setup();
  const binding=(kind,input_source,evdev)=>({kind,input_source,evdev,nx:32768,ny:32768,end_nx:49152,end_ny:32768,
    duration_ms:250,repeat_count:3,end_mode:'release',radius:8192,ramp_ms:80,look_width:8192,look_height:8192,
    sensitivity_x:100,sensitivity_y:150,invert_y:1,activation:'capture'});
  const good=report('default');good.profiles[0].bindings=[binding('look','mouse',273),binding('mouse_touch','pointer',0),binding('tap','keyboard',273)];
  states.profiles.set('default',good);await api.refresh();
  const chips=descendants(el('keymap-saved-list'),'kbd');
  assert.deepEqual(chips.map(c=>c.textContent),['Mouse right','Pointer','Key 273']);
  assert.match(chips[0].title,/Look · mouse · release.*region ±8192, ±8192.*100%.*inverted Y · capture/);
  for(const mutate of [b=>b.input_source='invalid',b=>b.evdev=277,b=>b.look_width=32768,b=>b.nx=0,
    b=>b.sensitivity_y=9,b=>b.invert_y=2,b=>b.activation='invalid',b=>b.radius=255,b=>b.ramp_ms=1001]){
    const bad=report('default','com.bad.vthree');bad.profiles[0].bindings=[binding('look','mouse',273)];
    mutate(bad.profiles[0].bindings[0]);states.profiles.set('default',bad);await api.refresh();
    assert.doesNotMatch(el('keymap-saved-list').textContent,/com.bad.vthree/);
    assert.match(el('keymap-saved-list').textContent,/com.test.game/);
  }
  const duplicate=report('default','com.bad.duplicate');duplicate.profiles[0].bindings=[binding('look','mouse',273),binding('look','keyboard',17)];
  states.profiles.set('default',duplicate);await api.refresh();assert.doesNotMatch(el('keymap-saved-list').textContent,/com.bad.duplicate/);
});

test('Scheme requests use exact typed scope, fresh revisions and validated response',async()=>{
  const {api,states,calls}=setup(),p={runtime:'default',package:'com.test.game'};
  const result={runtime_id:'default',package:p.package,revision:'a'.repeat(64),selected:'default',schemes:[{id:'default',name:'Default',binding_count:1,reference:null}]};
  states.schemes=result;await api.schemeRequest(p);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))),{op:'keymap_scheme',id:'default',package:p.package,action:'list',scheme_id:'',name:'',revision:'',content:''});
  await api.schemeRequest(p,'clone','default','New name');
  assert.equal(calls.at(-1).revision,result.revision);assert.equal(calls.at(-1).name,'New name');
  states.schemes={...result,package:'com.other.app',revision:'b'.repeat(64)};
  await assert.rejects(api.schemeRequest(p,'rename','default','Bad response'),/Invalid scheme response/);
  states.schemes=result;await api.schemeRequest(p,'rename','default','Good response');
  assert.equal(calls.at(-1).revision,result.revision);
  for(const bad of [{...result,selected:'missing'},{...result,schemes:[...result.schemes,...result.schemes]},
    {...result,schemes:[{...result.schemes[0],binding_count:65}]},
    {...result,schemes:[{...result.schemes[0],reference:{width:0,height:1080,orientation:'landscape'}}]}]){
    assert.throws(()=>api.validateSchemes(bad,p));
  }
});

test('Schemes live in existing app library row and load only when expanded',async()=>{
  const {api,states,el,calls}=setup();states.profiles.set('default',report('default'));
  states.schemes={runtime_id:'default',package:'com.test.game',revision:'a'.repeat(64),selected:'default',schemes:[{id:'default',name:'Default',binding_count:1,reference:null}]};
  await api.refresh();assert.ok(!calls.some(r=>r.op==='keymap_scheme'));
  const detail=descendants(el('keymap-saved-list'),'details').find(d=>d.children.some(c=>c.textContent==='Schemes'));
  assert.ok(detail);detail.open=true;detail.events.toggle();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(calls.some(r=>r.op==='keymap_scheme'&&r.action==='list'));
  for(const label of ['Use','New','Clone','Rename','Reset','Remove','Export'])assert.ok(detail.textContent.includes(label));
  assert.match(detail.textContent,/Save or cancel the open app editor/);
});

test('Scheme names share the backend 48 Unicode code point bound in responses and authoring',async()=>{
  const {api,states,el,calls}=setup(),p={runtime:'default',package:'com.test.game'};
  states.profiles.set('default',report('default'));
  states.schemes={runtime_id:p.runtime,package:p.package,revision:'a'.repeat(64),selected:'default',
    schemes:[{id:'default',name:'🎮'.repeat(48),binding_count:1,reference:null}]};
  assert.equal(api.validateSchemes(states.schemes,p).schemes[0].name,states.schemes.schemes[0].name);
  assert.throws(()=>api.validateSchemes({...states.schemes,schemes:[{...states.schemes.schemes[0],name:'🎮'.repeat(49)}]},p));
  await api.refresh();
  const detail=descendants(el('keymap-saved-list'),'details').find(d=>d.children.some(c=>c.textContent==='Schemes'));
  detail.open=true;detail.events.toggle();await new Promise(resolve=>setImmediate(resolve));
  const input=descendants(detail,'input').find(e=>e.type==='text');assert.equal(input.maxLength,96);
  input.value='🎮'.repeat(48);
  await descendants(detail,'button').find(b=>b.textContent==='Rename').events.click();
  assert.equal(calls.at(-1).name,'🎮'.repeat(48));
  const sent=calls.length;
  await assert.rejects(api.schemeRequest(p,'rename','default','🎮'.repeat(49)),/at most 48 characters/);
  await assert.rejects(api.schemeRequest(p,'rename','default','A'.repeat(49)),/at most 48 characters/);
  assert.equal(calls.length,sent);
});

test('V4 custom directions, walk modifier, suspend, skill cancel and pointer walk validate',async()=>{
  const {api,el,states}=setup();
  const base={nx:32768,ny:32768,end_nx:40000,end_ny:40000,duration_ms:250,repeat_count:1,input_source:'keyboard',end_mode:'release',radius:8192,ramp_ms:80,
    look_width:8192,look_height:8192,sensitivity_x:100,sensitivity_y:100,invert_y:0,activation:'hold',
    up_key:0,left_key:0,down_key:0,right_key:0,walk_key:0,walk_radius:0,suspend_key:0,drag_shot:0,skill_mode:0,cancel_key:0,cancel_x:0,cancel_y:0};
  const r=report('default');r.profiles[0].bindings=[{...base,kind:'wasd',evdev:103,up_key:103,left_key:105,down_key:108,right_key:106,walk_key:42,walk_radius:40},
    {...base,kind:'look',evdev:57,activation:'capture',suspend_key:56},
    {...base,kind:'skill',evdev:16,skill_mode:2,cancel_key:18,cancel_x:0,cancel_y:0},
    {...base,kind:'walk',input_source:'mouse',evdev:273}];
  states.profiles.set('default',r);await api.refresh();
  const titles=descendants(el('keymap-saved-list'),'kbd').map(c=>c.title);
  assert.match(titles[0],/Custom movement joystick.*directions ↑ \/ ← \/ ↓ \/ →.*40% radius/);
  assert.match(titles[0],/walk Left Shift/);assert.match(titles[1],/show cursor Left Alt/);assert.match(titles[2],/toggle cast.*cancel/);assert.match(titles[3],/Right-click to walk/);
  for(const mutate of [b=>b.left_key=103,b=>b.walk_key=60,b=>b.walk_radius=9,b=>b.suspend_key=56,b=>delete b.right_key]){
    const bad=JSON.parse(JSON.stringify(r));bad.profiles[0].package='com.bad.vfour';mutate(bad.profiles[0].bindings[0]);
    states.profiles.set('default',bad);await api.refresh();assert.doesNotMatch(el('keymap-saved-list').textContent,/com.bad.vfour/);
  }
});

test('Export uses native Save dialog and bounded Rust file command; cancel and errors are truthful',async()=>{
  const {api,states,el,exports,dialogs}=setup();states.profiles.set('default',report('default'));
  states.schemes={runtime_id:'default',package:'com.test.game',revision:'a'.repeat(64),selected:'default',schemes:[{id:'default',name:'Default',binding_count:1,reference:null}]};
  await api.refresh();const detail=descendants(el('keymap-saved-list'),'details').find(d=>d.children.some(c=>c.textContent==='Schemes'));
  detail.open=true;detail.events.toggle();await new Promise(resolve=>setImmediate(resolve));
  const save=descendants(detail,'button').find(b=>b.textContent==='Export');
  await save.events.click();assert.equal(exports.length,0);assert.match(detail.textContent,/Export cancelled/);
  assert.match(dialogs[0].defaultPath,/com.test.game-default.anvildroid.json$/);
  states.exportPath='/tmp/my-controls.json';await save.events.click();assert.equal(exports.length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(exports[0])),{id:'default',package:'com.test.game',schemeId:'default',revision:'a'.repeat(64),path:states.exportPath});
  assert.match(detail.textContent,/Scheme file saved/);
  states.exportError=new Error('Choose a new scheme export filename');await save.events.click();
  assert.match(detail.textContent,/Choose a new scheme export filename/);assert.doesNotMatch(detail.textContent,/Scheme file saved/);
  assert.doesNotMatch(source,/createObjectURL|revokeObjectURL|link.download/);
  const rust=fs.readFileSync('crates/anvildroid-gui/src/main.rs','utf8');
  assert.match(rust,/async fn export_keymap_scheme/);assert.match(rust,/export_runtime_diagnostics,\s*export_keymap_scheme,/);
});
