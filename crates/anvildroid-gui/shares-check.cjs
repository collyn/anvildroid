const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('./src-frontend/runtime-shares.js');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.events={}; this.value=''; this._text=''; this.disabled=false;this.dataset={};this.parentNode=null; }
  set innerHTML(_) { throw new Error('Dynamic HTML is forbidden'); }
  set textContent(value) { this.replaceChildren();this._text=String(value); }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  append(...items) {
    for(const item of items) { item.remove();item.parentNode=this;this.children.push(item); }
  }
  remove() {
    if(this.parentNode) { const siblings=this.parentNode.children;const index=siblings.indexOf(this);if(index>=0)siblings.splice(index,1);this.parentNode=null; }
  }
  replaceChildren(...items) { for(const child of [...this.children])child.remove();this._text='';this.append(...items); }
  setAttribute(name,value) { this[name]=value; }
  addEventListener(type,handler) { (this.events[type]||=[]).push(handler); }
  async fire(type='click',force=false) { if(this.disabled&&type==='click'&&!force)return;await Promise.all((this.events[type]||[]).map(handler=>handler({preventDefault(){}}))); }
  find(predicate) { return predicate(this)?this:this.children.map(child=>child.find(predicate)).find(Boolean); }
  all(predicate) { return [...(predicate(this)?[this]:[]),...this.children.flatMap(child=>child.all(predicate))]; }
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function share(change={}) { return {name:'Docs',source:'/home/user/docs',target:'/sdcard/AnvilDroid/Docs',read_only:true,mounted:false,configured:true,error:null,...change}; }
function report(id='one',change={}) { return {runtime_id:id,shares:[share()],editable:true,activation:'restart',max_shares:16,...change}; }
function fixture(options={}) {
  const body=new Element('body'),host=new Element('div'),created=[],calls=[],messages=[],choices=[];
  const document={body,createElement:tag=>{const node=new Element(tag);created.push(node);return node;},createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;}};
  const ui=create({document,request:async args=>{calls.push(args);return options.request?options.request(args):report(args.id);},
    chooseDirectory:async()=>{choices.push('choose');return options.chooseDirectory?options.chooseDirectory():'/home/user/selected';},
    toast:(message,type)=>messages.push({message,type})});
  const section=created.find(node=>node.tag==='section'&&node.className?.split(' ').includes('runtime-shares'));
  const button=text=>section.find(node=>node.tag==='button'&&node.textContent===text);
  return {ui,body,host,created,section,calls,messages,choices,button,form:()=>section.find(node=>node.tag==='form'),
    notice:()=>section.find(node=>node.role==='status'),name:()=>section.find(node=>node['aria-label']==='Share name'),
    source:()=>section.find(node=>node['aria-label']==='Selected source folder'),ro:()=>section.find(node=>node.type==='checkbox'),
    mount:(id='one',state='Stopped',target=host)=>ui.mount(target,{id,state})};
}

test('mount reads only selected runtime and defaults read-only without selecting or granting a directory',async()=>{
  const f=fixture();await f.mount();
  assert.deepEqual(f.calls,[{op:'shares_get',id:'one'}]);assert.equal(f.choices.length,0);
  assert.equal(f.source().value,'');assert.equal(f.ro().checked,true);assert.equal(f.button('Save share').disabled,false);
  assert.match(f.section.textContent,/configured, not mounted/);
});

test('explicit chosen directory and mode are saved to the selected runtime',async()=>{
  const f=fixture();await f.mount('two');await f.button('Choose folder').fire();f.name().value='Selected';f.ro().checked=false;
  await f.form().fire('submit');
  assert.deepEqual(f.calls[1],{op:'shares_set',name:'Selected',source:'/home/user/selected',read_only:false,id:'two'});
  assert.equal(f.ro().checked,false);assert.equal(f.button('Save share').disabled,false);
});

test('empty selection and invalid local share name cannot send mutation requests',async()=>{
  const f=fixture();await f.mount();await f.form().fire('submit');assert.match(f.notice().textContent,/Choose a source/);
  await f.button('Choose folder').fire();f.name().value='../escape';await f.form().fire('submit');
  assert.match(f.notice().textContent,/alphanumeric/);assert.equal(f.calls.length,1);
});

test('variable source and error text cannot become markup',async()=>{
  const hostile='<img src=x onerror=steal()> & "docs"';
  const f=fixture({request:args=>report(args.id,{shares:[share({source:'/home/user/'+hostile,error:hostile})]})});
  await f.mount();assert.ok(f.section.textContent.includes(hostile));assert.equal(f.button('Remove').disabled,false);
});

test('server editable false locks every mutation control and guards forged callbacks',async()=>{
  const f=fixture({request:args=>report(args.id,{editable:false})});await f.mount();
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
    const f=fixture({request:()=>value});await assert.doesNotReject(f.mount());
    assert.equal(f.button('Save share').disabled,true,JSON.stringify(value));assert.equal(f.button('Choose folder').disabled,true);
    assert.equal(f.button('Refresh').disabled,false);assert.match(f.notice().textContent,/Invalid shared-folder/);
    assert.equal(f.section.all(node=>node.tag==='button'&&node.textContent==='Remove').length,0);
    f.source().value='/home/user/docs';f.name().value='Docs';await f.form().fire('submit');assert.equal(f.calls.length,1);
  }
});

test('save and remove stay locked while mutation is pending',async()=>{
  const pending=deferred();const f=fixture({request:args=>args.op==='shares_get'?report(args.id):pending.promise});await f.mount();
  const remove=f.button('Remove');const mutation=remove.fire();
  assert.equal(remove.disabled,true);assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Choose folder').disabled,true);
  assert.equal(f.button('Refresh').disabled,true);await remove.fire('click',true);await f.form().fire('submit');
  assert.equal(f.calls.length,2);assert.deepEqual(f.calls[1],{op:'shares_remove',name:'Docs',id:'one'});
  pending.resolve(report());await mutation;assert.equal(f.button('Remove').disabled,false);
});

test('failed refresh invalidates prior actions, rejects detached callbacks and recovers only after refresh',async()=>{
  let fail=false;const f=fixture({request:args=>{if(fail)throw new Error('Worker unavailable');return report(args.id);}});await f.mount();
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
    await f.mount();const oldRemove=f.button('Remove');await oldRemove.fire();
    assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Refresh').disabled,false);
    assert.match(f.notice().textContent,/Refresh before retrying/i);await oldRemove.fire('click',true);assert.equal(f.calls.length,2);
    await f.button('Refresh').fire();assert.equal(f.button('Remove').disabled,false);
  }
});

test('read responses and read errors from an older runtime cannot update the selected runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({request:args=>args.id==='one'?pending.promise:report('two')});
    const old=f.mount();await f.mount('two');
    if(fail)pending.reject(new Error('Stale error'));else pending.resolve(report('one',{editable:false}));await old;
    assert.equal(f.button('Save share').disabled,false);assert.doesNotMatch(f.notice().textContent,/Stale error/);
    await f.button('Remove').fire();assert.equal(f.calls.at(-1).id,'two');
  }
});

test('old remove callbacks cannot mutate a replacement runtime or newly refreshed snapshot',async()=>{
  const f=fixture();await f.mount();const oldRemove=f.button('Remove');await f.mount('two');
  await oldRemove.fire('click',true);assert.equal(f.calls.length,2);
  const twoRemove=f.button('Remove');await f.button('Refresh').fire();await twoRemove.fire('click',true);assert.equal(f.calls.length,3);
});

test('late mutation completion and errors cannot overwrite a new runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({request:args=>args.op==='shares_get'?report(args.id):pending.promise});await f.mount();
    const mutation=f.button('Remove').fire();await f.mount('two');
    if(fail)pending.reject(new Error('Old mutation failure'));else pending.resolve(report('one',{editable:false}));await mutation;
    assert.equal(f.button('Save share').disabled,false);assert.doesNotMatch(f.notice().textContent,/Old mutation failure/);
    assert.equal(f.messages.length,0);
  }
});

test('directory picker locks actions, ignores duplicate clicks and releases controls after cancel',async()=>{
  const pending=deferred();const f=fixture({chooseDirectory:()=>pending.promise});await f.mount();const remove=f.button('Remove');
  const choice=f.button('Choose folder').fire();assert.equal(remove.disabled,true);assert.equal(f.button('Save share').disabled,true);
  await f.button('Choose folder').fire('click',true);await remove.fire('click',true);assert.equal(f.choices.length,1);assert.equal(f.calls.length,1);
  pending.resolve(null);await choice;assert.equal(f.source().value,'');assert.equal(f.button('Save share').disabled,false);
});

test('stale picker values and errors cannot affect another runtime or toast after deactivation',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();const f=fixture({chooseDirectory:()=>pending.promise});await f.mount();
    const choice=f.button('Choose folder').fire();f.ui.deactivate();await f.mount('two');
    if(fail)pending.reject(new Error('Old picker error'));else pending.resolve('/home/user/old');await choice;
    assert.equal(f.source().value,'');assert.equal(f.messages.length,0);assert.equal(f.button('Save share').disabled,false);
    assert.doesNotMatch(f.notice().textContent,/Old picker error/);
  }
});

test('picker failures remain visible as safe text and leave verified configuration available',async()=>{
  const f=fixture({chooseDirectory:()=>{throw new Error('<script>picker failure</script>');}});await f.mount();
  await f.button('Choose folder').fire();assert.match(f.notice().textContent,/<script>picker failure<\/script>/);
  assert.equal(f.messages[0].type,'error');assert.equal(f.button('Save share').disabled,false);
});

test('mounting another runtime resets selection and read-only, and a locked response cannot inherit old editable state',async()=>{
  const pending=deferred();const f=fixture({request:args=>args.id==='one'?report('one'):pending.promise});await f.mount();
  await f.button('Choose folder').fire();f.ro().checked=false;f.name().value='Old';
  const task=f.mount('two');assert.equal(f.source().value,'');assert.equal(f.name().value,'');assert.equal(f.ro().checked,true);
  assert.equal(f.button('Save share').disabled,true);pending.resolve(report('two',{editable:false}));await task;
  assert.equal(f.button('Save share').disabled,true);assert.equal(f.button('Choose folder').disabled,true);
});

test('construction is inert and mounting uses one inline section with no dialog or Close action',async()=>{
  const f=fixture();assert.equal(f.calls.length,0);assert.equal(f.body.children.length,0);
  assert.ok(f.section);assert.equal(f.section.parentNode,null);
  assert.equal(f.created.some(node=>node.tag==='dialog'),false);
  assert.equal(f.section.find(node=>node.tag==='button'&&node.textContent==='Close'),undefined);
  assert.equal(f.section.find(node=>node.tag==='h4').textContent,'Shared folders');
  await f.mount();assert.equal(f.section.parentNode,f.host);
  assert.ok(f.section.className.split(' ').includes('runtime-inline-feature'));
});

test('same runtime and state reparent the existing section without refetching or resetting drafts',async()=>{
  const f=fixture();await f.mount();await f.button('Choose folder').fire();f.name().value='Draft';f.ro().checked=false;
  const original=f.section,remove=f.button('Remove'),nextHost=new Element('div');
  await f.mount();await f.mount('one','Stopped',nextHost);
  assert.equal(f.calls.length,1);assert.equal(f.section,original);assert.equal(original.parentNode,nextHost);
  assert.equal(f.host.children.length,0);assert.deepEqual(nextHost.children,[original]);
  assert.equal(f.name().value,'Draft');assert.equal(f.source().value,'/home/user/selected');assert.equal(f.ro().checked,false);
  assert.equal(f.button('Remove'),remove);assert.equal(f.button('Save share').disabled,false);
});

test('same-target mount preserves pending read and settles it in the replacement host',async()=>{
  const pending=deferred();const f=fixture({request:()=>pending.promise});const reading=f.mount();
  const nextHost=new Element('div');await f.mount('one','Stopped',nextHost);
  assert.equal(f.calls.length,1);assert.equal(f.section.parentNode,nextHost);assert.equal(f.button('Refresh').disabled,true);
  pending.resolve(report());await reading;
  assert.equal(f.button('Remove').disabled,false);assert.equal(f.section.parentNode,nextHost);assert.equal(f.calls.length,1);
});

test('same-target reparent preserves pending picker, draft and busy state',async()=>{
  const pending=deferred();const f=fixture({chooseDirectory:()=>pending.promise});await f.mount();f.name().value='PickerDraft';
  const choosing=f.button('Choose folder').fire(),nextHost=new Element('div');
  await f.mount('one','Stopped',nextHost);
  assert.equal(f.calls.length,1);assert.equal(f.choices.length,1);assert.equal(f.name().value,'PickerDraft');
  assert.equal(f.button('Save share').disabled,true);assert.equal(f.section.parentNode,nextHost);
  pending.resolve('/home/user/picker-result');await choosing;
  assert.equal(f.source().value,'/home/user/picker-result');assert.equal(f.button('Save share').disabled,false);
});

test('same-target reparent preserves pending mutation without issuing another request',async()=>{
  const pending=deferred();const f=fixture({request:args=>args.op==='shares_get'?report(args.id):pending.promise});await f.mount();
  const removing=f.button('Remove').fire(),nextHost=new Element('div');await f.mount('one','Stopped',nextHost);
  assert.equal(f.calls.length,2);assert.equal(f.button('Save share').disabled,true);assert.equal(f.section.parentNode,nextHost);
  pending.resolve(report('one',{shares:[]}));await removing;
  assert.equal(f.calls.length,2);assert.match(f.section.textContent,/No folders shared/);assert.equal(f.button('Save share').disabled,false);
});

test('state change refetches and rejects old read results or errors for the same runtime',async()=>{
  for(const fail of [false,true]) {
    const pending=deferred();let reads=0;
    const f=fixture({request:args=>++reads===1?pending.promise:report(args.id,{editable:true})});
    const old=f.mount('one','Stopped');await f.mount('one','Running');
    assert.equal(f.calls.length,2);assert.equal(f.button('Save share').disabled,true);
    if(fail)pending.reject(new Error('Old stopped read failed'));else pending.resolve(report('one',{editable:true}));await old;
    assert.equal(f.button('Save share').disabled,true);assert.doesNotMatch(f.notice().textContent,/Old stopped read failed/);
    await f.mount('one','Running',new Element('div'));assert.equal(f.calls.length,2);
  }
});

test('local Prepared and Stopped state must both agree with server editable permission',async()=>{
  for(const state of ['Prepared','Stopped','Running','Frozen','Starting','Stopping','Allocated','Error',undefined,'STOPPED']) {
    const f=fixture();await f.ui.mount(f.host,{id:'one',state});
    const allowed=state==='Prepared'||state==='Stopped';
    assert.equal(f.button('Save share').disabled,!allowed,String(state));
    assert.equal(f.button('Remove').disabled,!allowed,String(state));
    if(!allowed) {
      f.name().value='Injected';f.source().value='/home/user/injected';await f.form().fire('submit');
      await f.button('Remove').fire('click',true);await f.button('Choose folder').fire('click',true);
      assert.equal(f.calls.length,1);assert.equal(f.choices.length,0);
    }
  }
  const locked=fixture({request:args=>report(args.id,{editable:false})});await locked.mount('one','Prepared');
  assert.equal(locked.button('Save share').disabled,true);
});

test('state changes invalidate pending picker results and pending mutation completion',async()=>{
  const pick=deferred();const f=fixture({chooseDirectory:()=>pick.promise});await f.mount();
  const choosing=f.button('Choose folder').fire();await f.mount('one','Running');
  pick.resolve('/home/user/stale');await choosing;
  assert.equal(f.source().value,'');assert.equal(f.button('Save share').disabled,true);assert.equal(f.calls.length,2);
  const response=deferred();const g=fixture({request:args=>args.op==='shares_get'?report(args.id):response.promise});await g.mount();
  const mutating=g.button('Remove').fire();await g.mount('one','Running');response.resolve(report('one',{editable:true}));await mutating;
  assert.equal(g.button('Save share').disabled,true);assert.equal(g.calls.length,3);
});

test('Default runtime renders unsupported inline without fetching or granting any access',async()=>{
  const f=fixture();await f.mount('default','Stopped');
  assert.equal(f.calls.length,0);assert.equal(f.choices.length,0);assert.equal(f.section.parentNode,f.host);
  assert.match(f.section.textContent,/Default|managed runtime/);assert.match(f.section.textContent,/unsupported|require|unavailable|system Waydroid/i);
  assert.equal(f.button('Save share').disabled,true);await f.ui.refresh();await f.button('Refresh').fire('click',true);
  assert.equal(f.calls.length,0);
  await f.mount('one');assert.equal(f.calls.length,1);assert.equal(f.button('Save share').disabled,false);
});

test('deactivate detaches the stable section, suppresses pending replies and requires fresh mount',async()=>{
  for(const stage of ['read','picker','mutation']) {
    const pending=deferred();const f=fixture({
      request:args=>(stage==='read'||(stage==='mutation'&&args.op!=='shares_get'))?pending.promise:report(args.id),
      chooseDirectory:()=>pending.promise,
    });
    let task;
    if(stage==='read')task=f.mount();
    else { await f.mount();task=stage==='picker'?f.button('Choose folder').fire():f.button('Remove').fire(); }
    const calls=f.calls.length;f.ui.deactivate();assert.equal(f.section.parentNode,null);
    await f.ui.refresh();await f.form().fire('submit');assert.equal(f.calls.length,calls);
    pending.resolve(stage==='picker'?'/home/user/stale':report());await task;
    assert.equal(f.section.parentNode,null);assert.equal(f.messages.length,0);assert.equal(f.source().value,'');
  }
  const f=fixture();await f.mount();const section=f.section;f.ui.deactivate();await f.mount();
  assert.equal(f.calls.length,2);assert.equal(f.section,section);assert.equal(section.parentNode,f.host);
});
