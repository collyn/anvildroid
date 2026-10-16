// Evaluate the real app action with inert Android/DOM dependencies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(`${__dirname}/src-frontend/main.js`, 'utf8');

function fixture({confirm = true, uninstall = async () => ({success:true,message:'Removed'})} = {}) {
  const calls = [], messages = [], prompts = [];
  const context = vm.createContext({
    probeInstallBusy:false, pendingLaunch:null, pendingAppAction:false,
    runtimeTransition:null, detailPackage:null, appToastTimer:17,
    AppDialog:{confirm:async (...args) => { prompts.push(args); return confirm; }},
    invoke:async (command,args) => { calls.push([command,args]); return uninstall(); },
    showAppToast:(message,type) => messages.push({message,type}),
    clearTimeout:() => calls.push(['keep-progress-visible']),
    refreshApps:async () => calls.push(['refresh']),
  });
  const start = source.indexOf('async function appMenuAction(');
  vm.runInContext(source.slice(start, source.indexOf('function showContextMenu(', start)), context);
  return {context,calls,messages,prompts,run:(pkg='com.example.app', reason) => context.appMenuAction('uninstall',pkg,'Example',reason)};
}

test('uninstall cancellation and protected launcher do not reach Android', async () => {
  const cancelled = fixture({confirm:false});
  await cancelled.run();
  assert.equal(cancelled.calls.length,0);
  assert.equal(cancelled.context.pendingAppAction,false);
  const protectedApp = fixture();
  await protectedApp.run('cu.axel.smartdock','Android system launcher');
  assert.equal(protectedApp.calls.length,0);
  assert.equal(protectedApp.prompts.length,0);
  assert.match(protectedApp.messages[0].message,/system launcher/);
});

test('uninstall stays busy and visible until the verified backend result', async () => {
  let finish;
  const pending = new Promise(resolve => { finish=resolve; });
  const f = fixture({uninstall:() => pending});
  const action = f.run();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(f.prompts[0][0],/com.example.app/);
  assert.match(f.prompts[0][0],/permanently removes/);
  assert.equal(f.context.pendingAppAction,true);
  assert.equal(f.messages.at(-1).message,'Uninstalling Example…');
  assert.ok(f.calls.some(([name])=>name==='keep-progress-visible'));
  await f.run();
  assert.equal(f.calls.filter(([name])=>name==='uninstall_app').length,1);
  finish({success:true,message:'Uninstalled com.example.app'});
  await action;
  assert.equal(f.context.pendingAppAction,false);
  assert.equal(f.messages.at(-1).message,'Uninstalled com.example.app');
  assert.equal(f.calls.filter(([name])=>name==='refresh').length,1);
  assert.equal(f.calls.some(([name])=>name==='check_uninstall'),false);
});

test('Android failure is reported and releases the app action', async () => {
  const f = fixture({uninstall:async()=>({success:false,message:'Cannot uninstall a system or protected app'})});
  await f.run();
  assert.equal(f.context.pendingAppAction,false);
  assert.equal(f.messages.at(-1).type,'error');
  assert.match(f.messages.at(-1).message,/Failed: Cannot uninstall/);
  assert.equal(f.calls.some(([name])=>name==='check_uninstall'),false);
});

test('lost backend response reports uncertainty and allows refreshing', async () => {
  const f = fixture({uninstall:async()=>{throw new Error('Outcome unknown; refresh before retrying');}});
  await f.run();
  assert.equal(f.context.pendingAppAction,false);
  assert.equal(f.messages.at(-1).type,'error');
  assert.match(f.messages.at(-1).message,/Outcome unknown/);
});
