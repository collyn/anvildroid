const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function harness() {
  const nodes = new Map(); let focused;
  function node(id) {
    if (!nodes.has(id)) nodes.set(id, {
      textContent:'', value:'', hidden:false, open:false, listeners:new Map(),
      addEventListener(name, fn) { if(!this.listeners.has(name))this.listeners.set(name,new Set());this.listeners.get(name).add(fn); },
      removeEventListener(name, fn) { this.listeners.get(name)?.delete(fn); },
      dispatch(name) { for(const fn of [...(this.listeners.get(name)||[])])fn({preventDefault(){}}); },
      showModal() { if(this.fail)throw Error('cannot show');this.open=true; },
      close() { this.open=false;this.dispatch('close'); },
      focus() { focused=id; }, select() { this.selected=true; },
    });
    return nodes.get(id);
  }
  const context={document:{getElementById:node}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('crates/anvildroid-gui/src-frontend/app-dialog.js','utf8'),context);
  const api=vm.runInContext('AppDialog',context);
  return {node,api,focused:()=>focused};
}
(async()=>{
 const {node,api,focused}=harness();
 const confirm=api.confirm('<unsafe>\nStop runtime?',{title:'Stop',okLabel:'Stop',cancelLabel:'Keep running'});
 await Promise.resolve();
 assert(node('app-dialog').open);assert.equal(node('app-dialog-title').textContent,'Stop');
 assert.equal(node('app-dialog-message').textContent,'<unsafe>\nStop runtime?');
 assert(node('app-dialog-input-field').hidden);assert.equal(focused(),'app-dialog-cancel');
 assert.equal(node('app-dialog-submit').textContent,'Stop');assert.equal(node('app-dialog-cancel').textContent,'Keep running');
 node('app-dialog-form').dispatch('submit');assert.equal(await confirm,true);
 const canceled=api.confirm('Delete?');await Promise.resolve();node('app-dialog-cancel').dispatch('click');assert.equal(await canceled,false);
 const escape=api.confirm('Stop?');await Promise.resolve();node('app-dialog').close();assert.equal(await escape,false);
 const prompt=api.prompt('Image name','Old name',{title:'Rename image'});await Promise.resolve();
 assert(!node('app-dialog-input-field').hidden);assert(node('app-dialog-input').required);assert(node('app-dialog-input').selected);assert.equal(focused(),'app-dialog-input');
 node('app-dialog-input').value='New name';node('app-dialog-form').dispatch('submit');assert.equal(await prompt,'New name');
 const noName=api.prompt('Name','Old');await Promise.resolve();node('app-dialog').close();assert.equal(await noName,null);
 const first=api.confirm('First'),second=api.confirm('Second');await Promise.resolve();
 assert.equal(node('app-dialog-message').textContent,'First');node('app-dialog-cancel').dispatch('click');assert.equal(await first,false);
 await Promise.resolve();assert.equal(node('app-dialog-message').textContent,'Second');node('app-dialog-form').dispatch('submit');assert.equal(await second,true);
 for(const id of ['app-dialog','app-dialog-form','app-dialog-cancel'])for(const set of node(id).listeners.values())assert.equal(set.size,0);
 node('app-dialog').fail=true;await assert.rejects(api.confirm('Unavailable'),/cannot show/);node('app-dialog').fail=false;
 const recovered=api.confirm('Recovered');await Promise.resolve();node('app-dialog-cancel').dispatch('click');assert.equal(await recovered,false);
 console.log('PASS: themed confirmations, prompt values, cancellation, queue ownership, listener cleanup and recovery');
})().catch(error=>{console.error(error);process.exitCode=1;});
