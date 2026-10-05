(function(){
  'use strict';
  const panel=document.getElementById('keymap-saved-list'),model=window.AnvilKeymapDrafts;
  if(!panel||!model)return;
  function line(parent,text,tag='p'){
    const item=document.createElement(tag);item.textContent=text;parent.appendChild(item);return item;
  }
  let epoch=0;
  function physicalLabel(key){
    const codes=[16,17,18,19,20,21,22,23,24,25,30,31,32,33,34,35,36,37,38,44,45,46,47,48,49,50];
    const at=codes.indexOf(key);if(at>=0)return 'QWERTYUIOPASDFGHJKLZXCVBNM'[at];
    return ({57:'Space',28:'Enter',15:'Tab',14:'Backspace',103:'Up',108:'Down',105:'Left',106:'Right'})[key]||`Key ${key}`;
  }
  async function refresh(){
    const request=++epoch;
    panel.replaceChildren();
    try{
      const {profiles,errors}=model.listSaved(window.localStorage);
      const failures=[];
      if(window.__TAURI__?.core){
        const invoke=window.__TAURI__.core.invoke;
        try{
          const runtimes=await invoke('runtime_controller',{request:{op:'list'}});
          if(!Array.isArray(runtimes))throw new Error('Invalid runtime list');
          for(const r of runtimes.filter(r=>r.state==='Running')){
            try{
              const result=await invoke('runtime_controller',{request:{op:'keymaps',id:r.id}});
              if(result?.runtime_id!==r.id||!Array.isArray(result.profiles))throw new Error('Invalid keymap inventory');
              for(const p of result.profiles){
                if(p.runtime!==r.id||typeof p.package!=='string'||!Array.isArray(p.bindings))throw new Error('Invalid keymap scope');
                const index=profiles.findIndex(old=>old.runtime===p.runtime&&old.package===p.package);if(index>=0)profiles.splice(index,1);
                profiles.push(p);
              }
              if(result.invalid_count)failures.push(`${r.name}: ${result.invalid_count} invalid profiles preserved`);
            }catch(error){failures.push(`${r.name}: ${error.message||error}`);}
          }
        }catch(error){failures.push(String(error));}
      }
      if(request!==epoch)return;
      if(!profiles.length)line(panel,'No saved keymaps found. Open an app, then right-click its card → Edit keymap. Stopped runtimes are not read.');
      for(const p of profiles){
        const row=document.createElement('article');row.className='settings-section';
        line(row,p.package,'h3');
        line(row,`${p.runtime==='default'?'Existing runtime':p.runtime} · ${p.bindings.length} controls · ${p.source==='runtime'?'Runtime profile':'Legacy GUI draft'} (not active)`);
        line(row,p.bindings.map(b=>`${b.evdev!==undefined?physicalLabel(b.evdev):model.keyLabel(b.key)} (${b.kind})`).join(' · '));
        if(p.source==='runtime'){
          const edit=line(row,'Edit in running app','button');edit.type='button';edit.className='btn';
          edit.addEventListener('click',()=>window.AnvilKeymapActions?.open(p.runtime,p.package));
        }
        panel.appendChild(row);
      }
      if(errors.length)line(panel,`${errors.length} invalid or unsupported saved drafts were left unchanged.`);
      for(const failure of failures)line(panel,failure);
    }catch(error){line(panel,`Cannot read saved keymaps: ${error.message}`);}
  }
  document.getElementById('keymap-list-refresh').addEventListener('click',refresh);
  window.addEventListener('storage',refresh);
  window.AnvilKeymapLibrary={refresh};
  if(window.location?.search==='?view=controls')refresh();
})();
