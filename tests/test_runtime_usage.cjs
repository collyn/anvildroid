const {test} = require('node:test');
const assert = require('node:assert/strict');
const Usage = require('../crates/anvildroid-gui/src-frontend/runtime-usage.js');
const GiB = 1024 ** 3;
const record = (id = 'one', usage = 0, scope = 'scope-one') => ({id, state:'Running', resources:{host:{cpu_count:8,memory_total_mib:16384},effective:{scope,cpu_usage_usec:usage,cpu_quota_count:2,memory_current_bytes:GiB,memory_max_bytes:4*GiB}},storage:{used_bytes:25*GiB,total_bytes:100*GiB,free_bytes:75*GiB}});
test('CPU uses counter deltas and quota capacity; first sample is unavailable', () => {
  const first=record('cpu',1000000);Usage.sample(first,1000);
  assert.equal(Usage.metrics(first)[1].percent,null);
  const next=record('cpu',2000000);Usage.sample(next,2000);
  assert.equal(Usage.metrics(next)[1].percent,50);
  assert.equal(Usage.metrics(next)[1].text,'50.0%');
});
test('unlimited CPU uses host capacity and samples remain runtime scoped', () => {
  const a=record('a',0);a.resources.effective.cpu_quota_count=null;Usage.sample(a,1000);
  const b=record('b',1000000);Usage.sample(b,1000);
  const next=record('a',4000000);next.resources.effective.cpu_quota_count=null;Usage.sample(next,2000);
  assert.equal(next.cpuUsagePercent,50);
  assert.equal(b.cpuUsagePercent,null);
});
test('restart, counter reset, capacity change and invalid sample cannot fabricate CPU usage', () => {
  for(const change of ['scope','counter','capacity','time']) {
    const a=record(change,5000000);Usage.sample(a,1000);
    const b=record(change,6000000);
    if(change==='scope')b.resources.effective.scope='new';
    if(change==='counter')b.resources.effective.cpu_usage_usec=0;
    if(change==='capacity')b.resources.effective.cpu_quota_count=4;
    Usage.sample(b,change==='time'?1000:2000);
    assert.equal(b.cpuUsagePercent,null);
  }
  const stopped=record('stopped');Usage.sample(stopped,1000);stopped.state='Stopped';Usage.sample(stopped,2000);
  stopped.state='Running';Usage.sample(stopped,3000);assert.equal(stopped.cpuUsagePercent,null);
});
test('memory and runtime disk bars have correct ratios and readable values', () => {
  const metrics=Usage.metrics(record());
  assert.equal(metrics[0].percent,25);assert.equal(metrics[0].text,'1.0 / 4.0 GiB');
  assert.equal(metrics[2].percent,25);assert.equal(metrics[2].text,'25.0 GiB');
  assert.match(metrics[2].hint,/Runtime allocated/);
  const unlimited=record();unlimited.resources.effective.memory_max_bytes=null;
  assert.equal(Usage.metrics(unlimited)[0].percent,6.25);
});
test('unknown, stopped and invalid metrics never display configured limits as usage', () => {
  assert(Usage.metrics({id:'empty'}).every(m=>m.percent===null && m.text==='—'));
  const stopped=record();stopped.state='Stopped';stopped.cpuUsagePercent=50;
  assert.equal(Usage.metrics(stopped)[0].percent,null);assert.equal(Usage.metrics(stopped)[1].percent,null);
  const invalid=record();invalid.storage.free_bytes=null;invalid.resources.effective.memory_current_bytes=NaN;
  assert.equal(Usage.metrics(invalid)[0].text,'—');assert.equal(Usage.metrics(invalid)[2].percent,null);
  const full=record();full.resources.effective.memory_current_bytes=8*GiB;assert.equal(Usage.metrics(full)[0].percent,100);
});
test('deleted runtime samples are discarded', () => {
  Usage.sample(record('deleted',0),1000);Usage.retain(new Set());
  const next=record('deleted',1000000);Usage.sample(next,2000);assert.equal(next.cpuUsagePercent,null);
});

test('GPU labels describe configured hardware, automatic and CPU choices', () => {
  for (const [vendor_id, text] of [['0x8086','Intel'],['0x1002','AMD'],['0x10de','NVIDIA']]) {
    const result = Usage.gpu({gpu:{selection:'/dev/dri/renderD130',devices:[{node:'/dev/dri/renderD130',vendor_id,driver:'fixture'}]}});
    assert.equal(result.text,text);assert.match(result.hint,/renderD130/);
  }
  assert.equal(Usage.gpu({gpu:{selection:'auto'}}).text,'Automatic');
  assert.equal(Usage.gpu({gpu:{selection:'software'}}).text,'CPU rendering');
  assert.equal(Usage.gpu({}).text,'—');
  assert.equal(Usage.gpu({gpu:{selection:'/dev/dri/renderD199',devices:[]}}).text,'renderD199');
});

test('GPU label uses its full model when available',()=>{assert.equal(Usage.gpu({gpu:{selection:'/dev/dri/renderD128',devices:[{node:'/dev/dri/renderD128',vendor_id:'0x10de',name:'NVIDIA GeForce RTX 2060'}]}}).text,'NVIDIA GeForce RTX 2060');});
