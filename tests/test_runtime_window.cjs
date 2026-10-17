const assert=require('node:assert/strict');
const Window=require('../crates/anvildroid-gui/src-frontend/runtime-window.js');
(async()=>{
 let tick=0, calls=0, resolve;
 const held=new Promise(r=>resolve=r);
 const work=async stage=>{calls++;stage('sending');await held;stage('Android completed');return {window:{state:'unverified',reason:'worker_upgrade_required'},timings:{total_ms:5,stages:[{name:'presentation',duration_ms:3,elapsed_ms:3},{name:'window_feedback',duration_ms:2,elapsed_ms:5}]}};};
 const first=Window.measure({id:'r-one'},work,{now:()=>++tick});
 const second=Window.measure({id:'r-one'},work,{now:()=>++tick});
 await Promise.resolve();assert.equal(calls,1);resolve();
 const [a,b]=await Promise.all([first,second]);assert.equal(a.trace.id,b.trace.id);
 assert.equal(a.trace.visibility,'unverified');assert.equal(a.trace.window_reason,'worker_upgrade_required');assert.deepEqual(a.trace.stages.map(s=>s.name),['request','sending','Android completed']);
 assert(a.trace.elapsed_ms>0);assert.equal(a.trace.backend_timings.total_ms,5);assert.deepEqual(a.trace.backend_timings.stages.map(s=>s.duration_ms),[3,2]);
 await assert.rejects(Window.measure({id:'failure'},async()=>{throw Error('no window');}),/no window/);
 const failed=Window.diagnostics().at(-1);assert.equal(failed.outcome,'failed');assert.match(failed.error,/no window/);
 await Window.measure({id:'failure'},async()=>({}));assert.equal(Window.diagnostics().at(-1).outcome,'succeeded');
 for(let i=0;i<60;i++)await Window.measure({id:'r-'+i},async()=>({window:{state:'ready'}}));
 const traces=Window.diagnostics();assert.equal(traces.length,50);traces[0].outcome='tampered';assert.equal(Window.diagnostics()[0].outcome,'succeeded');
 console.log('PASS: launch stages, elapsed time, request-only state, deduplication, failure cleanup and bounded diagnostics');
})().catch(e=>{console.error(e);process.exitCode=1;});
