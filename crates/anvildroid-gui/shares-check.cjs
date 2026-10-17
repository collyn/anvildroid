const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('./src-frontend/runtime-shares.js');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.value=''; this._text=''; this.open=false;this.disabled=false; }
  set innerHTML(_) { throw new Error('Dynamic HTML is forbidden'); }
  set textContent(value) { this._text=String(value);this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text='';this.children=items; }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(type,handler) { (this.events[type]||=[]).push(handler); }
  async fire(type='click',force=false) { if(this.disabled&&type==='click'&&!force)return;await Promise.all((this.events[type]||[]).map(handler=>handler({preventDefault(){}}))); }
  showModal() { this.open=true; }
  focus() {}
  close() { this.open=false;void this.fire('close'); }
  find(predicate) { return predicate(this)?this:this.children.map(child=>child.find(predicate)).find(Boolean); }
  all(predicate) { return [...(predicate(this)?[this]:[]),...this.children.flatMap(child=>child.all(predicate))]; }
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function share(change={}) { return {name:'Docs',source:'/home/user/docs',target:'/sdcard/AnvilDroid/Docs',read_only:true,mounted:false,configured:true,error:null,...change}; }
function report(id='one',change={}) { return {runtime_id:id,shares:[share()],editable:true,activation:'restart',max_shares:16,...change}; }
function fixture(options={}) {
  const body=new Element('body'),calls=[],messages=[],choices=[];
  const document={body,createElement:tag=>new Element(tag),createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}};
  const ui=create({document,request:async args=>{calls.push(args);return options.request?options.request(args):report(args.id);},
    chooseDirectory:async()=>{choices.push('choose');return options.chooseDirectory?options.chooseDirectory():'/home/user/selected';},
    toast:(message,type)=>messages.push({message,type})});
  const dialog=body.find(node=>node.tag==='dialog');
  const button=text=>dialog.find(node=>node.tag==='button'&&node.textContent===text);
  return {ui,body,dialog,calls,messages,choices,button,form:()=>dialog.find(node=>node.tag==='form'),
    notice:()=>dialog.find(node=>node.role==='status'),name:()=>dialog.find(node=>node['aria-label']==='Share name'),
    source:()=>dialog.find(node=>node['aria-label']==='Selected source folder'),ro:()=>dialog.find(node=>node.type==='checkbox'),
    open:(id='one')=>ui.open({id})};
}

test('open reads only selected runtime and defaults read-only without selecting or granting a directory',async()=>{
  const f=fixture();await f.open();
  assert.deepEqual(f.calls,[{op:'shares_get',id:'one'}]);assert.equal(f.choices.length,0);
  assert.equal(f.source().value,'');assert.equal(f.ro().checked,true);assert.equal(f.button('Save share').disabled,false);
  assert.match(f.dialog.textContent,/configured, not mounted/);
});

test('explicit chosen directory and mode are saved to the selected runtime',async()=>{
  const f=fixture();await f.open('two');await f.button('Choose folder').fire();f.name().value='Selected';f.ro().checked=false;
  await f.form().fire('submit');
  assert.deepEqual(f.calls[1],{op:'shares_set',name:'Selected',source:'/home/user/selected',read_only:false,id:'two'});
  assert.equal(f.ro().checked,false);assert.equal(f.button('Save share').disabled,false);
});

test('empty selection and invalid local share name cannot send mutation requests',async()=>{
  const f=fixture();await f.open();await f.form().fire('submit');assert.match(f.notice().textContent,/Choose a source/);
  await f.button('Choose folder').fire();f.name().value='../escape';await f.form().fire('submit');
  assert.match(f.notice().textContent,/alphanumeric/);assert.equal(f.calls.length,1);
});

test('variable source and error text cannot become markup',async()=>{
  const hostile='<img src=x onerror=steal()> & "docs"';
  const f=fixture({request:args=>report(args.id,{shares:[share({source:'/home/user/'+hostile,error:hostile})]})});
  await f.open();assert.ok(f.dialog.textContent.includes(hostile));assert.equal(f.button('Remove').disabled,false);
});

test('server editable false locks every mutation control and guards forged callbacks',async()=>{
  const f=fixture({request:args=>report(args.id,{editable:false})});await f.open();
  for(const element of [f.button('Save share'),f.button('Choose folder'),f.button('Remove'),f.name(),f.source(),f.ro()])assert.equal(element.disabled,true);
  assert.equal(f.button('Refresh').disabled,false);assert.match(f.notice().textContent,/Stop the runtime/);
  f.name().value='Injected';f.source().value='/home/user/injected';
  await f.form().fire('submit');await f.button('Remove').fire('click',true);await f.button('Choose folder').fire('click',true);
  assert.equal(f.calls.length,1);assert.equal(f.choices.length,0);
});

test('missing or malformed editable and malformed rows fail closed',async()=>{
  const malformed=[null,report('wrong'),report('one',{editable:undefined}),report('one',{editable:'true'}),
    report('one',{shares:null}),report('one',{shares:[null]}),report('one',{shares:[share({name:'../bad'})]}),
    report('one',{shares:[share({source:42})]}),report('one',{shares:[share({source:'relative'})]}),
    report('one',{shares:[share({source:'/bad\0path'})]}),report('one',{shares:[share({target:'/sdcard/Other'})]}),
    report('one',{shares:[share({read_only:'false'})]}),report('one',{shares:[share({mounted:0})]}),
    report('one',{shares:[share({configured:false})]}),report('one',{shares:[share({error:{message:'bad'}})]}),
    report('one',{shares:[share(),share()]}),report('one',{unsupported_reason:42}),
    report('one',{shares:Array.from({length:17},(_,i)=>share({name:'Share'+i,target:'/sdcard/AnvilDroid/Share'+i}))})];
  for(const value of malformed) {
    const f=fixture({request:()=>value});await assert.doesNotReject(f.open());
    assert.equal(f.button('Save share').disabled,true,JSON.stringify(value));assert.equal(f.button('Choose folder').disabled,true);
    assert.equal(f.button('Refresh').disabled,false);assert.match(f.notice().textContent,/Invalid shared-folder/);
    assert.equal(f.dialog.all(node=>node.tag==='button'&&node.textContent==='Remove').length,0);
    f.source().value='/home/user/docs';f.name().value='Docs';await f.form().fire('submit');assert.equal(f.calls.length,1);
  }
});

test('save and remove stay locked while mutation is pending',async()=>{
  const pending=deferred();const f=fixture({request:args=>args.op==='shares_get'?report(args.id):pending.promise});await f.open();
  const remove=f.button('Remove');const mutation=remove.fire();
  assert.equal(remove.disabled,true);assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Choose folder').disabled,true);
  assert.equal(f.button('Refresh').disabled,true);await remove.fire('click',true);await f.form().fire('submit');
  assert.equal(f.calls.length,2);assert.deepEqual(f.calls[1],{op:'shares_remove',name:'Docs',id:'one'});
  pending.resolve(report());await mutation;assert.equal(f.button('Remove').disabled,false);
});

test('failed refresh invalidates prior actions, rejects detached callbacks and recovers only after refresh',async()=>{
  let fail=false;const f=fixture({request:args=>{if(fail)throw new Error('Worker unavailable');return report(args.id);}});await f.open();
  const oldRemove=f.button('Remove');fail=true;await f.button('Refresh').fire();
  assert.equal(oldRemove.disabled,true);assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Refresh').disabled,false);
  assert.match(f.notice().textContent,/Worker unavailable.*Refresh before retrying/);
  await oldRemove.fire('click',true);assert.equal(f.calls.length,2);
  fail=false;await f.button('Refresh').fire();assert.equal(f.button('Save share').disabled,false);
  await oldRemove.fire('click',true);assert.equal(f.calls.length,3);
});

test('failed or malformed mutation responses disable stale actions and require refresh',async()=>{
  for(const malformed of [false,true]) {
    const f=fixture({request:args=>{if(args.op==='shares_get')return report(args.id);if(malformed)return report('wrong');throw new Error('Removal failed');}});
    await f.open();const oldRemove=f.button('Remove');await oldRemove.fire();
    assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Refresh').disabled,false);
    assert.match(f.notice().textContent,/Refresh before retrying/i);await oldRemove.fire('click',true);assert.equal(f.calls.length,2);
    await f.button('Refresh').fire();assert.equal(f.button('Remove').disabled,false);
  }
});

test('read responses and read errors from an older runtime cannot update the selected runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({request:args=>args.id==='one'?pending.promise:report('two')});
    const old=f.open();await f.open('two');
    if(fail)pending.reject(new Error('Stale error'));else pending.resolve(report('one',{editable:false}));await old;
    assert.equal(f.button('Save share').disabled,false);assert.doesNotMatch(f.notice().textContent,/Stale error/);
    await f.button('Remove').fire();assert.equal(f.calls.at(-1).id,'two');
  }
});

test('old remove callbacks cannot mutate a replacement runtime or newly refreshed snapshot',async()=>{
  const f=fixture();await f.open();const oldRemove=f.button('Remove');await f.open('two');
  await oldRemove.fire('click',true);assert.equal(f.calls.length,2);
  const twoRemove=f.button('Remove');await f.button('Refresh').fire();await twoRemove.fire('click',true);assert.equal(f.calls.length,3);
});

test('late mutation completion and errors cannot overwrite a new runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({request:args=>args.op==='shares_get'?report(args.id):pending.promise});await f.open();
    const mutation=f.button('Remove').fire();await f.open('two');
    if(fail)pending.reject(new Error('Old mutation failure'));else pending.resolve(report('one',{editable:false}));await mutation;
    assert.equal(f.button('Save share').disabled,false);assert.doesNotMatch(f.notice().textContent,/Old mutation failure/);
    assert.equal(f.messages.length,0);
  }
});

test('directory picker locks actions, ignores duplicate clicks and releases controls after cancel',async()=>{
  const pending=deferred();const f=fixture({chooseDirectory:()=>pending.promise});await f.open();const remove=f.button('Remove');
  const choice=f.button('Choose folder').fire();assert.equal(remove.disabled,true);assert.equal(f.button('Save share').disabled,true);
  await f.button('Choose folder').fire('click',true);await remove.fire('click',true);assert.equal(f.choices.length,1);assert.equal(f.calls.length,1);
  pending.resolve(null);await choice;assert.equal(f.source().value,'');assert.equal(f.button('Save share').disabled,false);
});

test('stale picker values and errors cannot affect another runtime or toast after close',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({chooseDirectory:()=>pending.promise});await f.open();
    const choice=f.button('Choose folder').fire();await f.button('Close').fire();await f.open('two');
    if(fail)pending.reject(new Error('Old picker error'));else pending.resolve('/home/user/old');await choice;
    assert.equal(f.source().value,'');assert.equal(f.messages.length,0);assert.equal(f.button('Save share').disabled,false);
    assert.doesNotMatch(f.notice().textContent,/Old picker error/);
  }
});

test('picker failures remain visible as safe text and leave verified configuration available',async()=>{
  const f=fixture({chooseDirectory:()=>{throw new Error('<script>picker failure</script>');}});await f.open();
  await f.button('Choose folder').fire();assert.match(f.notice().textContent,/<script>picker failure<\/script>/);
  assert.equal(f.messages[0].type,'error');assert.equal(f.button('Save share').disabled,false);
});

test('reopening resets selection and read-only, and a locked response cannot inherit old editable state',async()=>{
  const pending=deferred();const f=fixture({request:args=>args.id==='one'?report('one'):pending.promise});await f.open();
  await f.button('Choose folder').fire();f.ro().checked=false;f.name().value='Old';
  const task=f.open('two');assert.equal(f.source().value,'');assert.equal(f.name().value,'');assert.equal(f.ro().checked,true);
  assert.equal(f.button('Save share').disabled,true);pending.resolve(report('two',{editable:false}));await task;
  assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Choose folder').disabled,true);
});
