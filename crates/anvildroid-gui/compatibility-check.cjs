const {test} = require('node:test');
const assert = require('node:assert/strict');
const Compatibility = require('./src-frontend/app-compatibility.js');

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this.open=false; this._text=''; }
  set innerHTML(_) { throw new Error('Dynamic HTML is forbidden in this dialog'); }
  set textContent(value) { this._text=String(value); }
  get textContent() { return this._text + this.children.map(child=>child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  dispatch(type) { for (const listener of this.listeners[type] || []) listener({preventDefault(){}}); }
  focus() { this.focused=true; }
  showModal() { this.open=true; }
  close() { this.open=false; this.dispatch('close'); }
  find(predicate) { return predicate(this) ? this : this.children.map(child=>child.find(predicate)).find(Boolean); }
}
function deferred() { let resolve,reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; }
function report(id='first', packageName='com.example.game', change={}) {
  return {runtime_id:id, package:packageName, generation:'g1', revision:'a'.repeat(64), changed:false,
    settings:{fake_wifi:{explicit:false,inherited:false,effective:false},mouse_as_touch:{explicit:true,inherited:false,effective:true}},...change};
}
function fixture(handler=async payload=>report(payload.id,payload.package)) {
  const body=new Element('body'), calls=[], messages=[];
  const document={body,createElement:tag=>new Element(tag)};
  const ui=Compatibility.create({document, request:async payload=>{calls.push(payload);return handler(payload);},
    toast:(message,type)=>messages.push({message,type})});
  return {ui,body,calls,messages, input:key=>body.find(element=>element.id===`app-compatibility-${key}`),
    button:text=>body.find(element=>element.tagName==='button' && element.textContent===text),
    notice:()=>body.find(element=>element.role==='status'),dialog:()=>body.find(element=>element.tagName==='dialog')};
}

test('dialog reads only selected runtime/package, renders text safely, and requires app restart', async()=>{
  const f=fixture(); const label='<img src=x onerror=steal()> & "Game"';
  await f.ui.open({id:'first'},'com.example.game',label);
  assert.deepEqual(f.calls,[{op:'compatibility_get',id:'first',package:'com.example.game'}]);
  assert.ok(f.body.textContent.includes(label));
  assert.ok(f.body.textContent.includes('Image support varies'));
  assert.ok(f.body.textContent.includes('stop and reopen the app'));
  assert.equal(f.input('fake_wifi').checked,false);
  assert.equal(f.input('mouse_as_touch').checked,true);
  assert.equal(f.button('Save').disabled,false);
  assert.equal(f.dialog().open,true);
});

test('save sends exact boolean fields and revision; keeps controls busy until verified reply', async()=>{
  const pending=deferred();
  const f=fixture(payload=>payload.op==='compatibility_get' ? report() : pending.promise);
  await f.ui.open('first','com.example.game');
  f.input('fake_wifi').checked=true;
  const task=f.ui.save();
  assert.deepEqual(f.calls[1],{op:'compatibility_set',id:'first',package:'com.example.game',revision:'a'.repeat(64),
    settings:{fake_wifi:true,mouse_as_touch:true}});
  assert.equal(f.button('Save').disabled,true);
  assert.equal(f.button('Reset app').disabled,true);
  await f.ui.save(); await f.ui.reset(); await f.ui.refresh();
  assert.equal(f.calls.length,2);
  pending.resolve(report('first','com.example.game',{changed:true,revision:'b'.repeat(64)}));
  await task;
  assert.match(f.notice().textContent,/Stop and reopen/);
  assert.equal(f.button('Save').disabled,false);
  assert.equal(f.messages[0].type,'success');
});

test('reset removes app entries through runtime-specific reset, reports inherited global settings', async()=>{
  const inherited={explicit:true,inherited:true,effective:true};
  const f=fixture(payload=>report(payload.id,payload.package,{changed:payload.op==='compatibility_reset',
    settings:{fake_wifi:inherited,mouse_as_touch:{explicit:false,inherited:false,effective:false}}}));
  await f.ui.open('first','com.example.game');
  assert.equal(f.input('fake_wifi').disabled,true);
  assert.equal(f.input('fake_wifi').checked,true);
  assert.match(f.body.textContent,/global wildcard; per-app disable unavailable/);
  await f.ui.reset();
  assert.deepEqual(f.calls[1],{op:'compatibility_reset',id:'first',package:'com.example.game',revision:'a'.repeat(64)});
});

test('old read responses and errors cannot overwrite a newly selected runtime', async()=>{
  for (const fail of [false,true]) {
    const pending=deferred();
    const f=fixture(payload=>payload.id==='first' ? pending.promise : report('second','com.example.other'));
    const old=f.ui.open('first','com.example.game','Old');
    await f.ui.open('second','com.example.other','New');
    if (fail) pending.reject(new Error('old error')); else pending.resolve(report());
    await old;
    assert.match(f.body.textContent,/New/);
    assert.doesNotMatch(f.notice().textContent,/old error/);
    await f.ui.save();
    assert.equal(f.calls.at(-1).id,'second');
    assert.equal(f.calls.at(-1).package,'com.example.other');
  }
});

test('closing invalidates a pending response and allows reopening', async()=>{
  const pending=deferred();
  const f=fixture(payload=>payload.id==='first'?pending.promise:report('second'));
  const old=f.ui.open('first','com.example.game'); f.ui.close();
  await f.ui.open('second','com.example.game');
  pending.resolve(report()); await old;
  assert.equal(f.dialog().open,true);
  await f.ui.reset();
  assert.equal(f.calls.at(-1).id,'second');
});

test('late mutation response from a previous app does not affect the new dialog or toast', async()=>{
  const pending=deferred();
  const f=fixture(payload=>payload.op==='compatibility_set'?pending.promise:report(payload.id,payload.package));
  await f.ui.open('first','com.example.game'); const old=f.ui.save();
  await f.ui.open('second','com.example.other');
  pending.resolve(report('first','com.example.game',{changed:true})); await old;
  assert.equal(f.messages.length,0);
  assert.equal(f.button('Save').disabled,false);
  assert.equal(f.notice().textContent,'Current configuration loaded.');
});

test('read errors keep mutation controls disabled and refresh recovers', async()=>{
  let fail=true;
  const f=fixture(payload=>{if(fail)throw new Error('<script>Bad Android</script>');return report(payload.id,payload.package);});
  await f.ui.open('first','com.example.game');
  assert.match(f.notice().textContent,/<script>Bad Android<\/script>/);
  assert.equal(f.button('Save').disabled,true);
  assert.equal(f.button('Reset app').disabled,true);
  assert.equal(f.button('Refresh').disabled,false);
  await f.ui.save(); assert.equal(f.calls.length,1);
  fail=false; await f.ui.refresh();
  assert.equal(f.button('Save').disabled,false);
});

test('mutation errors invalidate revision and require explicit refresh before retrying', async()=>{
  const f=fixture(payload=>{if(payload.op==='compatibility_set')throw new Error('Rollback failed');return report();});
  await f.ui.open('first','com.example.game'); await f.ui.save();
  assert.match(f.notice().textContent,/Rollback failed.*Refresh before retrying/);
  assert.equal(f.button('Save').disabled,true);
  assert.equal(f.messages[0].type,'error');
  await f.ui.save(); assert.equal(f.calls.length,2);
  await f.ui.refresh(); assert.equal(f.button('Save').disabled,false);
});

test('spoofed runtime, package, revision, settings, and generation change fail closed', async()=>{
  const invalid=[{runtime_id:'other'},{package:'com.other.app'},{revision:'evil'},
    {settings:{}},{generation:null},{settings:{fake_wifi:{explicit:false,inherited:false,effective:true}}}];
  for (const change of invalid) {
    const f=fixture(()=>report('first','com.example.game',change));
    await f.ui.open('first','com.example.game');
    assert.equal(f.button('Save').disabled,true,JSON.stringify(change));
    assert.match(f.notice().textContent,/response changed/);
  }
  const f=fixture(payload=>report('first','com.example.game',payload.op==='compatibility_set'?{generation:'g2'}:{}));
  await f.ui.open('first','com.example.game');await f.ui.save();
  assert.equal(f.button('Save').disabled,true);
  assert.match(f.notice().textContent,/response changed/);
});

test('form submission uses bounded API and prevents browser navigation', async()=>{
  const f=fixture(); await f.ui.open('first','com.example.game');
  f.body.find(element=>element.tagName==='form').dispatch('submit');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(f.calls[1].op,'compatibility_set');
  f.button('Close').dispatch('click');
  assert.equal(f.dialog().open,false);
});
