(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.AnvilKeymapDrafts=api;})(globalThis,function(){
  'use strict';
  function scope(runtime,pkg){
    if(typeof runtime!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(runtime)||typeof pkg!=='string'||pkg.length>255||!/^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$/.test(pkg))throw new Error('Invalid runtime/app scope');
    return JSON.stringify(['anvildroid-keymap-v1',runtime,pkg]);
  }
  function keyValid(k){return k===null||(k&&typeof k.code==='string'&&/^[A-Za-z0-9]{1,40}$/.test(k.code)&&k.code!=='Escape'&&['ctrl','alt','shift','meta'].every(m=>typeof k[m]==='boolean'));}
  function keyId(k){return k?`${+k.ctrl}${+k.alt}${+k.shift}${+k.meta}:${k.code}`:'';}
  function keyLabel(k){return k?[k.ctrl?'Ctrl':null,k.alt?'Alt':null,k.shift?'Shift':null,k.meta?'Meta':null,k.code.replace(/^Key/,'').replace(/^Digit/,'')].filter(Boolean).join('+'):'Assign key';}
  function validate(p,runtime,pkg){
    scope(runtime,pkg);
    if(!p||p.version!==1||p.runtime!==runtime||p.package!==pkg||!['contain','stretch'].includes(p.layout)||!p.reference||!['width','height'].every(n=>Number.isInteger(p.reference[n])&&p.reference[n]>=1&&p.reference[n]<=8192)||!Array.isArray(p.bindings)||p.bindings.length>128)throw new Error('Unsupported or invalid keymap draft');
    const ids=new Set(),keys=new Set();
    for(const b of p.bindings){
      if(!b||typeof b.id!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(b.id)||ids.has(b.id)||!['tap','hold'].includes(b.kind)||!keyValid(b.key)||!['nx','ny'].every(n=>Number.isInteger(b[n])&&b[n]>=0&&b[n]<=65535))throw new Error('Invalid or duplicate control');
      ids.add(b.id);if(b.key){const key=keyId(b.key);if(keys.has(key))throw new Error('Duplicate mapped key');keys.add(key);}
    }return p;
  }
  class Store{
    constructor(storage){this.storage=storage;this.drafts=new Map();this.serial=0;}
    open(runtime,pkg){
      const key=scope(runtime,pkg);if(this.drafts.has(key))return this.drafts.get(key);
      const raw=this.storage.getItem(key);if(raw&&raw.length>131072)throw new Error('Keymap draft exceeds size limit');
      const profile=raw?validate(JSON.parse(raw),runtime,pkg):{version:1,runtime,package:pkg,layout:'contain',reference:{width:1920,height:1080},bindings:[]};
      const draft={profile,dirty:false,raw};this.drafts.set(key,draft);return draft;
    }
    add(draft){
      if(draft.profile.bindings.length>=128)throw new Error('Maximum 128 controls');
      let id;do{id=`point-${Date.now().toString(36)}-${++this.serial}`;}while(draft.profile.bindings.some(b=>b.id===id));
      const b={id,kind:'tap',key:null,nx:32768,ny:32768};
      draft.profile.bindings.push(b);draft.dirty=true;return b;
    }
    assign(draft,id,key){
      if(!keyValid(key))throw new Error('Unsupported key; Escape leaves key capture');
      const b=draft.profile.bindings.find(b=>b.id===id);if(!b)throw new Error('Select a control first');
      if(key&&draft.profile.bindings.some(b=>b.id!==id&&keyId(b.key)===keyId(key)))throw new Error('That key is already mapped in this app');
      b.key=key;draft.dirty=true;
    }
    remove(draft,id){draft.profile.bindings=draft.profile.bindings.filter(b=>b.id!==id);draft.dirty=true;}
    save(draft){
      const p=draft.profile;validate(p,p.runtime,p.package);
      if(p.bindings.some(b=>!b.key))throw new Error('Assign a key to every control before saving');
      const key=scope(p.runtime,p.package);if(this.storage.getItem(key)!==draft.raw)throw new Error('Draft changed in another window. Reload before saving.');
      const raw=JSON.stringify(p);this.storage.setItem(key,raw);draft.raw=raw;draft.dirty=false;
    }
    discard(draft){const p=draft.profile;this.drafts.delete(scope(p.runtime,p.package));return this.open(p.runtime,p.package);}
  }
  function listSaved(storage){
    const profiles=[],errors=[];
    for(let i=0;i<storage.length;++i){
      const key=storage.key(i);let parts;
      try{parts=JSON.parse(key);}catch{continue;}
      if(!Array.isArray(parts)||parts[0]!=='anvildroid-keymap-v1')continue;
      try{
        if(parts.length!==3||scope(parts[1],parts[2])!==key)throw new Error('Invalid scope key');
        const raw=storage.getItem(key);if(!raw||raw.length>131072)throw new Error('Invalid draft size');
        const p=validate(JSON.parse(raw),parts[1],parts[2]);
        if(p.bindings.length)profiles.push(p);
      }catch{errors.push(key);}
    }
    profiles.sort((a,b)=>a.package.localeCompare(b.package)||a.runtime.localeCompare(b.runtime));
    return {profiles,errors};
  }
  return Object.freeze({Store,scope,validate,keyLabel,listSaved});
});
