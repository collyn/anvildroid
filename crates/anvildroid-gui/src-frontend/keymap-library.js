(function(){
  'use strict';
  const panel=document.getElementById('keymap-saved-list'),model=window.AnvilKeymapDrafts;
  if(!panel||!model)return;
  const filter=document.getElementById('keymap-runtime-filter'),refreshButton=document.getElementById('keymap-list-refresh');
  const inventory=new Map();
  let epoch=0,records=new Map([['default',{id:'default',name:'Default runtime',state:'Unavailable'}]]),drafts=[],failures=[];
  function node(tag,text,className){
    const item=document.createElement(tag);
    if(text!==undefined)item.textContent=text;
    if(className)item.className=className;
    return item;
  }
  function physicalLabel(key){
    const codes=[16,17,18,19,20,21,22,23,24,25,30,31,32,33,34,35,36,37,38,44,45,46,47,48,49,50];
    const at=codes.indexOf(key);if(at>=0)return 'QWERTYUIOPASDFGHJKLZXCVBNM'[at];
    return ({57:'Space',28:'Enter',15:'Tab',14:'Backspace',103:'↑',108:'↓',105:'←',106:'→'})[key]||`Key ${key}`;
  }
  function validateInventory(result,id){
    if(result?.runtime_id!==id||!Array.isArray(result.profiles)||result.profiles.length>128||
       !Number.isInteger(result.invalid_count)||result.invalid_count<0)throw new Error('Invalid keymap inventory');
    const packages=new Set();
    return result.profiles.map(p=>{
      if(p.runtime!==id||typeof p.package!=='string'||p.package.length>240||
         !/^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$/.test(p.package)||packages.has(p.package)||
         !Array.isArray(p.bindings)||p.bindings.length>64)throw new Error('Invalid keymap scope');
      packages.add(p.package);const keys=new Set();
      for(const b of p.bindings){
        if(!b||!Number.isInteger(b.evdev)||b.evdev<2||b.evdev>767||keys.has(b.evdev)||
           !['tap','hold'].includes(b.kind)||!['nx','ny'].every(k=>Number.isInteger(b[k])&&b[k]>=0&&b[k]<=65535))throw new Error('Invalid keymap binding');
        keys.add(b.evdev);
      }
      return {...p,source:'runtime'};
    });
  }
  function available(r){return ['Running','Frozen'].includes(r?.state)&&!r.error;}
  function updateSelectors(){
    const selected=filter.value||'all';filter.replaceChildren(node('option','All runtimes'));filter.children[0].value='all';
    for(const r of records.values()){const option=node('option',r.name);option.value=r.id;filter.appendChild(option);}
    filter.value=records.has(selected)?selected:'all';
    const probe=document.getElementById('probe-runtime');
    if(probe&&!probe.disabled){
      const previous=probe.value;probe.replaceChildren(node('option','Select runtime'));probe.children[0].value='';
      for(const r of records.values())if(available(r)){const option=node('option',`${r.name} · ${r.state}`);option.value=r.id;probe.appendChild(option);}
      probe.value=available(records.get(previous))?previous:'';
      updateDiagnostics();
    }
  }
  function updateDiagnostics(){
    const r=records.get(document.getElementById('probe-runtime')?.value);
    document.getElementById('install-touch-probe').disabled=!available(r);
    document.getElementById('probe-open-waydroid').disabled=!available(r);
  }
  function render(){
    const selected=filter.value||'all',rows=[];
    for(const [id,profiles] of inventory)for(const p of profiles)if(selected==='all'||id===selected)rows.push(p);
    rows.sort((a,b)=>a.package.localeCompare(b.package)||a.runtime.localeCompare(b.runtime));
    const content=document.createDocumentFragment();
    if(!rows.length){
      const empty=node('div',undefined,'controls-empty');empty.appendChild(node('p','No saved keymaps'));
      empty.appendChild(node('span','Open an app → header menu → Edit keymap, or press F2.','hint'));
      const scoped=selected==='all'?[...records.values()]:[records.get(selected)];
      if(scoped.some(r=>r&&!available(r)))empty.appendChild(node('span','Start a runtime to load its keymaps.','hint'));
      content.appendChild(empty);
    }else{
      const wrapper=node('div',undefined,'runtime-table-scroll controls-table-scroll'),table=node('table',undefined,'runtime-table controls-table');
      const head=node('thead'),header=node('tr');
      for(const title of ['App','Runtime','Keys','Actions'])header.appendChild(node('th',title));
      head.appendChild(header);table.appendChild(head);
      const body=node('tbody');
      for(const p of rows){
        const r=records.get(p.runtime),row=node('tr'),app=node('td'),runtime=node('td'),keys=node('td'),actions=node('td');
        app.appendChild(node('span',p.package,'controls-package'));
        runtime.appendChild(node('span',r?.name||p.runtime,'controls-runtime-name'));
        runtime.appendChild(node('span',r?.state||'Unavailable','controls-runtime-state'));
        if(r?.state!=='Running'&&r?.state!=='Frozen')runtime.title='Last loaded keymaps';
        const chips=node('div',undefined,'controls-key-chips');
        for(const b of p.bindings){const chip=node('kbd',physicalLabel(b.evdev));chip.title=b.kind==='hold'?'Hold':'Tap';chips.appendChild(chip);}
        if(!p.bindings.length)chips.appendChild(node('span','—','hint'));
        keys.appendChild(chips);
        const edit=node('button',undefined,'btn runtime-action-icon');edit.type='button';
        edit.title=available(r)?'Edit keymap in an open app':'Start this runtime to edit';edit.setAttribute('aria-label',`Edit keymap · ${p.package} · ${r?.name||p.runtime}`);
        edit.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16 3 5 5-12 12-6 1 1-6Z"/><path d="m14 5 5 5"/></svg>';
        edit.disabled=!available(r);
        edit.addEventListener('click',async()=>{if(edit.disabled)return;edit.disabled=true;try{await window.AnvilKeymapActions?.open(p.runtime,p.package);}finally{edit.disabled=!available(records.get(p.runtime));}});
        actions.appendChild(edit);for(const cell of [app,runtime,keys,actions])row.appendChild(cell);body.appendChild(row);
      }
      table.appendChild(body);wrapper.appendChild(table);content.appendChild(wrapper);
    }
    const legacy=drafts.filter(p=>selected==='all'||p.runtime===selected);
    if(legacy.length){
      const details=node('details',undefined,'controls-details');details.appendChild(node('summary',`Legacy drafts (${legacy.length})`));
      details.appendChild(node('p','Saved drafts are preserved. Create active keymaps in the app window.','hint'));
      for(const p of legacy)details.appendChild(node('p',`${p.package} · ${records.get(p.runtime)?.name||p.runtime} · ${p.bindings.length} keys`));
      content.appendChild(details);
    }
    if(failures.length){
      const details=node('details',undefined,'controls-details controls-errors');details.appendChild(node('summary',`Could not load all keymaps (${failures.length})`));
      for(const failure of failures)details.appendChild(node('p',failure));content.appendChild(details);
    }
    panel.replaceChildren(content);
  }
  async function refresh(){
    const request=++epoch;refreshButton.disabled=true;panel.setAttribute('aria-busy','true');
    const nextFailures=[];let nextDrafts=[];
    try{
      const saved=model.listSaved(window.localStorage);nextDrafts=saved.profiles;
      if(saved.errors.length)nextFailures.push(`${saved.errors.length} invalid legacy drafts preserved.`);
    }catch(error){nextFailures.push(`Legacy drafts: ${error.message||error}`);}
    try{
      const invoke=window.__TAURI__?.core?.invoke;
      let nextRecords=new Map(records),updates=[];
      if(invoke){
        const [managed,existing]=await Promise.allSettled([
          invoke('runtime_controller',{request:{op:'list'}}),
          invoke('runtime_controller',{request:{op:'refresh',id:'default'}})
        ]);
        if(managed.status==='fulfilled'&&Array.isArray(managed.value)){
          nextRecords=new Map([['default',records.get('default')]]);
          for(const r of managed.value)if(r&&typeof r.id==='string'&&r.id!=='default')nextRecords.set(r.id,{...r,name:r.name||r.id});
        }else{
          for(const [id,r] of nextRecords)if(id!=='default')nextRecords.set(id,{...r,state:'Unavailable'});
          nextFailures.push(`Runtimes: ${managed.reason?.message||managed.reason||'Invalid runtime list'}`);
        }
        if(existing.status==='fulfilled'&&existing.value?.id==='default')nextRecords.set('default',{...existing.value,name:'Default runtime'});
        else{nextRecords.set('default',{id:'default',name:'Default runtime',state:'Unavailable'});nextFailures.push(`Default runtime: ${existing.reason?.message||existing.reason||'Unavailable'}`);}
        updates=await Promise.all([...nextRecords.values()].filter(r=>available(r)).map(async r=>{
          try{
            const result=await invoke('runtime_controller',{request:{op:'keymaps',id:r.id}}),profiles=validateInventory(result,r.id);
            return {id:r.id,profiles,invalid:result.invalid_count};
          }catch(error){return {id:r.id,error:`${r.name}: ${error.message||error}`};}
        }));
      }
      if(request!==epoch)return;
      records=nextRecords;drafts=nextDrafts;
      for(const id of inventory.keys())if(!records.has(id))inventory.delete(id);
      for(const update of updates){
        if(update.error)nextFailures.push(update.error);
        else{inventory.set(update.id,update.profiles);if(update.invalid)nextFailures.push(`${records.get(update.id).name}: ${update.invalid} invalid profiles preserved.`);}
      }
      failures=nextFailures;updateSelectors();render();
    }finally{
      if(request===epoch){refreshButton.disabled=false;panel.setAttribute('aria-busy','false');}
    }
  }
  refreshButton.addEventListener('click',refresh);
  filter.addEventListener('change',render);
  document.getElementById('probe-runtime')?.addEventListener('change',updateDiagnostics);
  window.addEventListener('storage',event=>{if(!event.key||event.key.includes('anvildroid-keymap-v1'))refresh();});
  window.AnvilKeymapLibrary={refresh,record:id=>records.get(id),updateDiagnostics};
  if(window.location?.search==='?view=controls')refresh();
})();
