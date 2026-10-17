const { test } = require('node:test');
const assert = require('node:assert/strict');
const g = require('../crates/anvildroid-gui/src-frontend/keymap-geometry.js');
test('letterbox excludes header and black bars', () => {
  const r = g.gameRect({x:0,y:40,width:1000,height:800},{width:1920,height:1080},'contain');
  assert.deepEqual(r,{x:0,y:158.75,width:1000,height:562.5});
  assert.equal(g.normalize({x:100,y:50},r),null);
  assert.equal(g.normalize({x:100,y:780},r),null);
});
test('same stored point follows resize/restore without mutation or drift', () => {
  const point = Object.freeze({nx:52428,ny:45875});
  const normal = {x:20,y:32,width:1000,height:700};
  const max = {x:100,y:50,width:1600,height:900};
  const original = g.androidPoint(point,normal);
  for(let i=0;i<100;i++) {
    assert.deepEqual(g.androidPoint(point,max),{x:1379,y:679}); // native fixture matches
    assert.deepEqual(g.androidPoint(point,normal),original);
  }
});
test('DPI conversion uses Android bounds, not CSS scale twice', () => {
  const point={nx:32768,ny:32768};
  assert.deepEqual(g.androidPoint(point,{x:0,y:0,width:1000,height:600}),{x:500,y:300});
  assert.deepEqual(g.androidPoint(point,{x:0,y:0,width:1250,height:750}),{x:625,y:375});
  assert.deepEqual(g.androidPoint(point,{x:0,y:0,width:2000,height:1200}),{x:1000,y:600});
});
test('edge controls never map beyond the final Android pixel', () => {
  assert.deepEqual(g.androidPoint({nx:65535,ny:65535},{x:20,y:32,width:1000,height:700}),{x:1019,y:731});
  assert.deepEqual(g.androidPoint({nx:0,ny:0},{x:20,y:32,width:1000,height:700}),{x:20,y:32});
});
test('drag roundtrip preserves normalized point within quantization', () => {
  const r=g.gameRect({x:17,y:39,width:853,height:577},{width:16,height:9},'contain');
  for(const nx of [0,100,32768,52428,65535]) {
    const p={nx,ny:32768}; assert.deepEqual(g.normalize(g.project(p,r),r),p);
  }
});
test('invalid/unverified geometry fails instead of guessing', () => {
  assert.throws(()=>g.project({nx:-1,ny:0},{x:0,y:0,width:100,height:100}));
  assert.throws(()=>g.androidPoint({nx:0,ny:0},{x:0,y:0,width:0,height:100}));
  assert.throws(()=>g.androidPoint({nx:0,ny:0},{x:0,y:0,width:1250.5,height:750}));
  assert.throws(()=>g.gameRect({x:0,y:0,width:100,height:100},null,'contain'));
  assert.throws(()=>g.gameRect({x:0,y:0,width:100,height:100},{width:NaN,height:100},'contain'));
});
