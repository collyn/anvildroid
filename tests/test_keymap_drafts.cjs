const {test}=require('node:test');const assert=require('node:assert/strict');
const model=require('../crates/anvildroid-gui/src-frontend/keymap-drafts.js');
function storage(){const data=new Map();return{getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};}
test('profile library lists only saved nonempty app drafts and preserves corrupt entries',()=>{
  const data=new Map(),db={get length(){return data.size;},key:i=>[...data.keys()][i],getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};
  const store=new model.Store(db),empty=store.open('r-one','com.empty.app');store.save(empty);
  const p=store.open('r-one','com.game.one'),b=store.add(p);store.assign(p,b.id,key('KeyJ'));store.save(p);
  data.set('unrelated','do not touch');data.set(model.scope('r-one','com.bad.app'),'broken');
  const result=model.listSaved(db);
  assert.equal(result.profiles.length,1);assert.equal(result.profiles[0].package,'com.game.one');
  assert.equal(result.errors.length,1);assert.equal(data.get(model.scope('r-one','com.bad.app')),'broken');
});
const key=code=>({code,ctrl:false,alt:false,shift:false,meta:false});
test('empty app profile supports user-defined keys and survives GUI reopen',()=>{
  const db=storage(),store=new model.Store(db),a=store.open('r-one','com.game.one');
  assert.equal(a.profile.bindings.length,0);
  const p=store.add(a);assert.equal(p.key,null);assert.throws(()=>store.save(a));
  store.assign(a,p.id,key('KeyJ'));p.nx=50000;p.ny=10000;p.kind='hold';
  store.save(a);assert.equal(a.dirty,false);
  assert.deepEqual(new model.Store(db).open('r-one','com.game.one').profile,a.profile);
});
test('switching apps/runtimes retains independent unsaved drafts',()=>{
  const store=new model.Store(storage()),a=store.open('r-one','com.game.one');store.add(a);
  assert.equal(store.open('r-one','com.game.two').profile.bindings.length,0);
  assert.equal(store.open('r-two','com.game.one').profile.bindings.length,0);
  assert.equal(store.open('r-one','com.game.one'),a);assert.equal(a.dirty,true);
});
test('duplicate keys rejected, modifier combinations and remove supported',()=>{
  const store=new model.Store(storage()),a=store.open('default','com.game.one');
  const p=store.add(a),q=store.add(a);store.assign(a,p.id,key('Space'));
  assert.throws(()=>store.assign(a,q.id,key('Space')),/already mapped/);
  store.assign(a,q.id,{...key('Space'),ctrl:true});store.save(a);
  store.remove(a,p.id);assert.equal(a.profile.bindings.length,1);assert.equal(a.dirty,true);
  assert.throws(()=>store.assign(a,q.id,key('Escape')));
});
test('reject corrupt, cross-scope and future schema without overwriting',()=>{
  const db=storage(),k=model.scope('r-one','com.game.one');
  for(const raw of ['broken','x'.repeat(131073),JSON.stringify({version:2}),JSON.stringify({version:1,runtime:'r-other'})]){
    db.setItem(k,raw);assert.throws(()=>new model.Store(db).open('r-one','com.game.one'));assert.equal(db.getItem(k),raw);
  }
});
test('concurrent GUI edits fail rather than silently overwriting another save',()=>{
  const db=storage(),one=new model.Store(db),two=new model.Store(db);
  const a=one.open('r-one','com.game.one'),b=two.open('r-one','com.game.one');
  const p=one.add(a);one.assign(a,p.id,key('KeyA'));one.save(a);
  const q=two.add(b);two.assign(b,q.id,key('KeyB'));assert.throws(()=>two.save(b),/another window/);
  assert.equal(b.dirty,true);assert.equal(two.discard(b).profile.bindings[0].key.code,'KeyA');
});
test('control count, coordinates and scope validation are bounded',()=>{
  const store=new model.Store(storage()),a=store.open('r-one','com.game.one');
  for(let i=0;i<128;i++)store.add(a);assert.throws(()=>store.add(a));
  a.profile.bindings[0].nx=65536;assert.throws(()=>model.validate(a.profile,'r-one','com.game.one'));
  assert.throws(()=>store.open('../bad','com.game.one'));assert.throws(()=>store.open('r-one','bad/package'));
});
