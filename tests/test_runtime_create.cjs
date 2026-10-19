const {test} = require('node:test');
const assert = require('node:assert/strict');
const {initialize} = require('../crates/anvildroid-gui/src-frontend/runtime-settings.js');

const initial = () => ({id:'r-'+'a'.repeat(32),name:'New Android',state:'Allocated',job:null});
function fixture(failure, pending = 1) {
  const record = initial(), calls = [], phases = [], updates = [];
  let polls = 0, clock = 0;
  const options = {
    progress:phase => phases.push(phase),
    update:record => updates.push(structuredClone(record)),
    waitOptions:{now:()=>clock,sleep:async()=>{clock += 60000;}},
    send:async request => {
      assert.equal(request.id,record.id);
      calls.push(request.op);
      if (request.op === failure) throw new Error('Fixture rejected '+failure);
      if (request.op === 'display_set') return {mode:'desktop'};
      if (request.op === 'prepare' || request.op === 'start') {
        if (request.op === 'start') {
          assert.equal(record.state,'Prepared');
          assert.equal(record.job.status,'Succeeded');
        }
        polls = 0;
        record.state = request.op === 'prepare' ? 'Provisioning' : 'Starting';
        record.job = {id:request.op+'-job',operation:request.op,status:'Running',error:null};
      } else if (request.op === 'refresh') {
        polls++;
        if (failure === record.job.operation+'-job') {
          record.state = 'Error';record.job.status = 'Failed';record.job.error = 'Fixture job failure';
        } else if (polls > (record.job.operation === 'prepare' ? pending : 1)) {
          record.state = record.job.operation === 'prepare' ? 'Prepared' : 'Running';
          record.job.status = 'Succeeded';
        }
      }
      return structuredClone(record);
    },
  };
  return {record,calls,phases,updates,options,clock:()=>clock};
}

test('new runtime waits for each job and retains Desktop mode through boot', async()=>{
  const f=fixture();
  const result=await initialize(initial(),f.options);
  assert.deepEqual(f.calls,['display_set','prepare','refresh','refresh','start','refresh','refresh']);
  assert.deepEqual(f.phases,['configuring','preparing','booting']);
  assert.equal(result.state,'Running');assert.equal(result.display.mode,'desktop');
  assert.ok(f.updates.some(record=>record.state==='Provisioning'));
  assert.ok(f.updates.some(record=>record.state==='Prepared'));
  assert.ok(f.updates.some(record=>record.state==='Starting'));
});

for (const failure of ['display_set','prepare','prepare-job','start','start-job']) {
  test(`new runtime stops the sequence and reports its recovery step after ${failure} fails`,async()=>{
    const f=fixture(failure);
    const stage=failure==='display_set'?'desktop setup':failure.startsWith('prepare')?'image preparation':'automatic start';
    await assert.rejects(initialize(initial(),f.options),error=>
      error.message.includes('New Android was created') && error.message.includes(stage) && error.message.includes('Fixture'));
    assert.equal(f.calls.filter(op=>op==='prepare').length,failure==='display_set'?0:1);
    assert.equal(f.calls.filter(op=>op==='start').length,failure.startsWith('start')?1:0);
    assert.ok(!f.calls.some(op=>['create','delete','stop'].includes(op)));
    if (failure.endsWith('-job')) assert.equal(f.updates.at(-1).state,'Error');
  });
}

test('preparation may exceed the six-minute boot timeout before automatic start',async()=>{
  const f=fixture(null,8);
  const result=await initialize(initial(),f.options);
  assert.equal(result.state,'Running');assert.ok(f.clock()>360000);
});

test('uncompleted preparation times out without submitting Start',async()=>{
  const f=fixture(null,100);
  await assert.rejects(initialize(initial(),f.options),/image preparation failed.*Timed out/);
  assert.ok(!f.calls.includes('start'));
  assert.equal(f.updates.at(-1).state,'Provisioning');
});

test('changed runtime or job identity cancels the chain before Start',async()=>{
  for (const field of ['id','job']) {
    const f=fixture();const send=f.options.send;
    f.options.send=async request=>{
      const result=await send(request);
      return request.op==='refresh'?{...result,[field]:field==='id'?'r-'+'b'.repeat(32):{...result.job,id:'replacement'}}:result;
    };
    await assert.rejects(initialize(initial(),f.options),/operation changed/);
    assert.ok(!f.calls.includes('start'));
  }
});

test('invalid creation, Desktop reply or submitted job never triggers a later stage',async()=>{
  for (const record of [null,{...initial(),id:'default'},{...initial(),id:'bad'},
      {...initial(),state:'Running'},{...initial(),job:{id:'existing'}}]) {
    let calls=0;
    await assert.rejects(initialize(record,{send:async()=>{calls++;}}),/Invalid newly created runtime/);
    assert.equal(calls,0);
  }
  for (const bad of ['desktop','identity','operation','job']) {
    const f=fixture();const send=f.options.send;
    f.options.send=async request=>{
      const result=await send(request);
      if (request.op==='display_set' && bad==='desktop') return {mode:'headless'};
      if (request.op==='prepare') {
        if (bad==='identity') return {...result,id:'other'};
        if (bad==='operation') return {...result,job:{...result.job,operation:'stop'}};
        if (bad==='job') return {...result,job:null};
      }
      return result;
    };
    await assert.rejects(initialize(initial(),f.options),/was created, but/);
    assert.ok(!f.calls.includes('start'));
  }
});
