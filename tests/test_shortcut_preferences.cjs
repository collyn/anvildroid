const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('crates/anvildroid-gui/src-frontend/main.js','utf8');
function setup(){
 const calls=[],toasts=[];let change;const box={checked:false,disabled:true};
 const ctx=vm.createContext({document:{getElementById:()=>({addEventListener:(_,fn)=>change=fn,...box})},
 tauriInvoke:async(cmd,args)=>{calls.push({cmd,args});return true;},showAppToast:(...args)=>toasts.push(args)});
 const node={...box,addEventListener:(_,fn)=>change=fn};ctx.document.getElementById=()=>node;
 vm.runInContext(source.slice(source.indexOf('async function refreshShortcutPreferences('),source.indexOf('// Boot preferences use')),ctx);
 return {ctx,node,calls,toasts,change:()=>change({target:node})};
}
test('Settings loads persistent automatic-shortcut preference and enables the checkbox',async()=>{
 const {ctx,node,calls}=setup();await ctx.refreshShortcutPreferences();
 assert.equal(node.checked,true);assert.equal(node.disabled,false);assert.equal(calls[0].cmd,'get_shortcut_preferences');
});
test('saving persists checkbox choice and reports success',async()=>{
 const {node,calls,toasts,change}=setup();node.checked=false;await change();
 assert.equal(calls[0].cmd,'set_automatic_shortcuts');assert.equal(calls[0].args.enabled,false);
 assert.equal(node.disabled,false);assert.equal(toasts[0][1],'success');
});
test('save failure restores previous choice and allows retry',async()=>{
 const {ctx,node,toasts,change}=setup();ctx.tauriInvoke=async()=>{throw Error('Service unavailable')};node.checked=false;
 await change();assert.equal(node.checked,true);assert.equal(node.disabled,false);assert.equal(toasts[0][1],'error');
});
