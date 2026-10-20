(function(){
  'use strict';
  const panel=document.getElementById('keymap-saved-list'),model=window.AnvilKeymapDrafts;
  if(!panel||!model)return;
  const filter=document.getElementById('keymap-runtime-filter'),refreshButton=document.getElementById('keymap-list-refresh');
  const inventory=new Map();
  const schemeCache=new Map();
  let epoch=0,records=new Map([['default',{id:'default',name:'Default runtime',state:'Unavailable'}]]),drafts=[],failures=[];
  function node(tag,text,className){
    const item=document.createElement(tag);
    if(text!==undefined)item.textContent=text;
    if(className)item.className=className;
    return item;
  }
  function physicalLabel(key,source){
    if(source==='pointer')return 'Pointer';
    if(source==='mouse')return ({272:'Mouse left',273:'Mouse right',274:'Mouse middle',275:'Mouse back',276:'Mouse forward'})[key];
    const codes=[16,17,18,19,20,21,22,23,24,25,30,31,32,33,34,35,36,37,38,44,45,46,47,48,49,50];
    const at=codes.indexOf(key);if(at>=0)return 'QWERTYUIOPASDFGHJKLZXCVBNM'[at];
    return ({57:'Space',28:'Enter',15:'Tab',14:'Backspace',103:'↑',108:'↓',105:'←',106:'→',
      29:'Left Ctrl',97:'Right Ctrl',42:'Left Shift',54:'Right Shift',56:'Left Alt',100:'Right Alt',
      125:'Left Meta',126:'Right Meta',59:'F1',60:'F2',61:'F3',62:'F4',63:'F5',64:'F6',65:'F7',66:'F8',67:'F9',68:'F10',87:'F11',88:'F12'})[key]||`Key ${key}`;
  }
  function validateInventory(result,id){
    if(result?.runtime_id!==id||!Array.isArray(result.profiles)||result.profiles.length>128||
       !Number.isInteger(result.invalid_count)||result.invalid_count<0)throw new Error('Invalid keymap inventory');
    const packages=new Set();
    return result.profiles.map(p=>{
      if(p.runtime!==id||typeof p.package!=='string'||p.package.length>240||
         !/^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$/.test(p.package)||packages.has(p.package)||
         !Array.isArray(p.bindings)||p.bindings.length>64)throw new Error('Invalid keymap scope');
      packages.add(p.package);const keys=new Set(),reserved=new Set();
      for(const b of p.bindings){
        const source=b?.input_source||'keyboard',v3=b?.input_source!==undefined;
        const bindingKey=`${source}:${b?.evdev}`;
        if(!b||!['keyboard','mouse','pointer'].includes(source)||!Number.isInteger(b.evdev)||
           (source==='keyboard'&&(b.evdev<2||b.evdev>767))||(source==='mouse'&&(b.evdev<272||b.evdev>276))||
           ((source==='pointer')!==(b.kind==='mouse_touch'))||(source==='pointer'&&b.evdev!==0)||keys.has(bindingKey)||
           !['tap','hold','wasd','swipe','repeat','skill','macro','aim','mouse_touch','look','walk'].includes(b.kind)||!['nx','ny'].every(k=>Number.isInteger(b[k])&&b[k]>=0&&b[k]<=65535))throw new Error('Invalid keymap binding');
        if(!['tap','hold'].includes(b.kind)&&
           (!['end_nx','end_ny'].every(k=>Number.isInteger(b[k])&&b[k]>=0&&b[k]<=65535)||
            !Number.isInteger(b.duration_ms)||b.duration_ms<50||b.duration_ms>5000||
            !Number.isInteger(b.repeat_count)||b.repeat_count<1||b.repeat_count>20||
            (b.kind==='macro'&&b.duration_ms*b.repeat_count*2>10000)||
            (b.kind==='wasd'&&(b.evdev!==(b.up_key||17)||source!=='keyboard'))))throw new Error('Invalid advanced keymap binding');
        if((['mouse_touch','look'].includes(b.kind)&&!v3)||(v3&&(
           !['legacy','release','hold'].includes(b.end_mode)||!['hold','capture','auto'].includes(b.activation)||
           (b.activation==='auto'&&b.kind!=='look')||
           !Number.isInteger(b.invert_y)||![0,1].includes(b.invert_y)||
           !Number.isInteger(b.radius)||(b.radius!==0&&(b.radius<256||b.radius>32767))||
           !Number.isInteger(b.ramp_ms)||b.ramp_ms<0||b.ramp_ms>1000||
           !['look_width','look_height'].every(k=>Number.isInteger(b[k])&&b[k]>=0&&b[k]<=32767)||
           !['sensitivity_x','sensitivity_y'].every(k=>Number.isInteger(b[k])&&b[k]>=0&&b[k]<=1000)||
           (b.kind==='look'&&(b.look_width<256||b.look_height<256||b.nx<b.look_width||b.nx+b.look_width>65535||
             b.ny<b.look_height||b.ny+b.look_height>65535||b.sensitivity_x<10||b.sensitivity_y<10))
        )))throw new Error('Invalid V3 keymap binding');
        const v4=b.up_key!==undefined,fields=['up_key','left_key','down_key','right_key','walk_key','walk_radius','suspend_key','drag_shot','skill_mode','cancel_key','cancel_x','cancel_y'];
        if(fields.some(k=>b[k]!==undefined)!==v4||(v4&&(!v3||!fields.every(k=>Number.isInteger(b[k])&&b[k]>=0&&b[k]<=65535))))throw new Error('Invalid V4 keymap fields');
        const directions=[b.up_key||0,b.left_key||0,b.down_key||0,b.right_key||0];
        if(v4&&((directions.some(Boolean)&&(b.kind!=='wasd'||!directions.every(Boolean)))||
          (b.walk_key&&b.kind!=='wasd')||(b.walk_radius&&(!b.walk_key||b.walk_radius<10||b.walk_radius>100))||
          (b.suspend_key&&(b.kind!=='look'||!['capture','auto'].includes(b.activation)))||![0,1].includes(b.drag_shot)||
          (b.drag_shot&&(b.kind!=='hold'||source!=='mouse'||b.end_mode==='release'))||b.skill_mode>2||
          ([b.skill_mode,b.cancel_key,b.cancel_x,b.cancel_y].some(Boolean)&&b.kind!=='skill')||
          ((b.cancel_x||b.cancel_y)&&!b.cancel_key)))throw new Error('Invalid V4 keymap binding');
        if(b.kind==='walk'&&(!v4||source!=='mouse'||b.evdev!==273||!b.radius))throw new Error('Invalid pointer movement');
        const claims=b.kind==='wasd'?(b.up_key?directions:[17,30,31,32]):source==='keyboard'?[b.evdev]:[];
        claims.push(...[b.walk_key,b.suspend_key,b.cancel_key].filter(Boolean));
        if(new Set(claims).size!==claims.length||claims.some(k=>k<2||k>767||[60,61,62].includes(k)||reserved.has(k)))throw new Error('Conflicting keyboard controls');
        for(const k of claims)reserved.add(k);
        keys.add(bindingKey);
      }
      if(p.bindings.reduce((n,b)=>n+(b.drag_shot||0),0)>1)throw new Error('Duplicate drag shot controls');
      if(['mouse_touch','look'].some(kind=>p.bindings.filter(b=>b.kind===kind).length>1)||
         (p.bindings.some(b=>b.kind==='aim')&&keys.has('mouse:272')))throw new Error('Conflicting pointer controls');
      return {...p,source:'runtime'};
    });
  }
  function bindingTitle(b){
    const titles={tap:'Tap',hold:'Hold',wasd:b.up_key?'Custom movement joystick':'WASD joystick',swipe:'Swipe',repeat:'Repeated click',skill:'Skill cast',macro:'Bounded macro',aim:'Aim / left-click fire',mouse_touch:'Mouse touch',look:'Look',walk:'Right-click to walk'};
    let title=titles[b.kind];
    if(['swipe','repeat','macro'].includes(b.kind))title+=` · ${b.duration_ms} ms`;
    if(b.kind==='macro')title+=` · ${b.repeat_count} clicks`;
    if(['swipe','macro','aim'].includes(b.kind))title+=` · endpoint ${b.end_nx}, ${b.end_ny}`;
    if(b.input_source){
      title+=` · ${b.input_source} · ${b.end_mode}`;
      if(b.radius)title+=` · radius ${b.radius}`;
      if(b.ramp_ms)title+=` · ramp ${b.ramp_ms} ms`;
      if(b.kind==='look')title+=` · region ±${b.look_width}, ±${b.look_height} · sensitivity ${b.sensitivity_x}% / ${b.sensitivity_y}% · ${b.invert_y?'inverted Y · ':''}${b.activation}`;
    }
    if(b.up_key)title+=` · directions ${[b.up_key,b.left_key,b.down_key,b.right_key].map(k=>physicalLabel(k)).join(' / ')}`;
    if(b.walk_key)title+=` · walk ${physicalLabel(b.walk_key)} · ${b.walk_radius||40}% radius`;
    if(b.suspend_key)title+=` · show cursor ${physicalLabel(b.suspend_key)}`;
    if(b.drag_shot)title+=' · drag shot';
    if(b.skill_mode)title+=` · ${b.skill_mode===1?'cast immediately':'toggle cast'}`;
    if(b.cancel_key)title+=` · cancel ${physicalLabel(b.cancel_key)} at ${b.cancel_x}, ${b.cancel_y}`;
    return title;
  }
  function available(r){return ['Running','Frozen'].includes(r?.state)&&!r.error;}
  function validateSchemes(result,p){
    if(result?.runtime_id!==p.runtime||result.package!==p.package||!/^[0-9a-f]{64}$/.test(result.revision)||
       !Array.isArray(result.schemes)||!result.schemes.length||result.schemes.length>16)throw new Error('Invalid scheme response');
    const ids=new Set();
    for(const s of result.schemes){
      if(!s||typeof s.id!=='string'||!(s.id==='default'||/^s-[0-9a-f]{32}$/.test(s.id))||ids.has(s.id)||
         typeof s.name!=='string'||!s.name.length||[...s.name].length>48||/[\x00-\x1f\x7f]/.test(s.name)||
         !Number.isInteger(s.binding_count)||s.binding_count<0||s.binding_count>64)throw new Error('Invalid scheme metadata');
      if(s.reference!==null&&s.reference!==undefined&&(!s.reference||
         !['width','height'].every(k=>Number.isInteger(s.reference[k])&&s.reference[k]>=1&&s.reference[k]<=8192)||
         !['portrait','landscape'].includes(s.reference.orientation)))throw new Error('Invalid scheme dimensions');
      ids.add(s.id);
    }
    if(!ids.has('default')||!ids.has(result.selected))throw new Error('Invalid active scheme');
    if(result.bindings!==undefined)validateInventory({runtime_id:p.runtime,invalid_count:0,profiles:[{runtime:p.runtime,package:p.package,bindings:result.bindings}]},p.runtime);
    return result;
  }
  async function schemeRequest(p,action='list',scheme_id='',name='',content=''){
    if(typeof name!=='string'||[...name].length>48)throw new Error('Scheme names may contain at most 48 characters');
    const key=JSON.stringify([p.runtime,p.package]),cached=schemeCache.get(key);
    const request={op:'keymap_scheme',id:p.runtime,package:p.package,action,scheme_id,name,
      revision:action==='list'?'':cached?.revision||'',content};
    const result=validateSchemes(await window.__TAURI__.core.invoke('runtime_controller',{request}),p);
    schemeCache.set(key,result);return result;
  }
  function schemeControls(p,updated){
    const details=node('details',undefined,'controls-schemes'),summary=node('summary','Schemes');details.appendChild(summary);
    const body=node('div',undefined,'controls-scheme-body'),status=node('p','Open to load schemes.','hint');status.setAttribute('role','status');
    details.appendChild(body);body.appendChild(status);
    let result=null,busy=false,loaded=false;
    const showError=error=>{status.textContent=error.message||String(error);};
    async function run(action,scheme='',name='',content=''){
      if(busy||!available(records.get(p.runtime)))return;
      busy=true;body.setAttribute('aria-busy','true');
      for(const b of buttons)b.disabled=true;
      try{
        if(action==='export'){
          const path=await window.__TAURI__.dialog.save({defaultPath:`${p.package}-${scheme}.anvildroid.json`,
            title:'Export AnvilDroid control scheme',filters:[{name:'AnvilDroid scheme JSON',extensions:['json']}]});
          if(!path){status.textContent='Export cancelled.';return;}
          const saved=await window.__TAURI__.core.invoke('export_keymap_scheme',{id:p.runtime,package:p.package,schemeId:scheme,revision:result.revision,path});
          if(saved?.runtime_id!==p.runtime||saved.package!==p.package||saved.scheme_id!==scheme||saved.path!==path||
             !Number.isSafeInteger(saved.bytes)||saved.bytes<=0||saved.bytes>24577)throw new Error('Scheme export was not confirmed');
          status.textContent='Scheme file saved. Existing files are preserved.';
        }else{
          result=await schemeRequest(p,action,scheme,name,content);loaded=true;
          if(result.bindings){p.bindings=result.bindings;updated();}
          draw();
          if(action!=='list')status.textContent=action==='select'?'Scheme selected. Focus the app to play.':'Scheme saved.';
        }
      }catch(error){showError(error);}
      finally{busy=false;body.setAttribute('aria-busy','false');for(const b of buttons)b.disabled=!available(records.get(p.runtime));}
    }
    let buttons=[];
    function draw(){
      buttons=[];body.replaceChildren();
      const select=node('select');select.setAttribute('aria-label',`Scheme · ${p.package}`);
      for(const s of result.schemes){const o=node('option',`${s.name}${s.id===result.selected?' · active':''} · ${s.binding_count} controls`);o.value=s.id;select.appendChild(o);}
      select.value=result.selected;
      const name=node('input');name.type='text';name.maxLength=96;name.placeholder='Scheme name';name.setAttribute('aria-label','Scheme name');
      const tools=node('div',undefined,'controls-scheme-tools');tools.appendChild(select);tools.appendChild(name);
      function button(label,action,tip){const b=node('button',label,'btn');b.type='button';b.title=tip||label;b.disabled=!available(records.get(p.runtime));
        b.addEventListener('click',()=>run(action,action==='create'?'':select.value,['create','clone','rename'].includes(action)?name.value:''));tools.appendChild(b);buttons.push(b);return b;}
      button('Use','select','Save or cancel the app editor before switching schemes');
      button('New','create','Create an empty scheme; enter a name first');button('Clone','clone','Copy the selected scheme; enter a name first');
      button('Rename','rename','Rename the selected scheme');button('Reset','reset','Clear all controls in the selected scheme');
      button('Remove','remove','Remove an inactive custom scheme');button('Export','export','Download the selected scheme as AnvilDroid JSON');
      const file=node('input');file.type='file';file.accept='.json,application/json';file.setAttribute('aria-label','Import AnvilDroid scheme JSON');
      file.addEventListener('change',async()=>{const source=file.files?.[0];if(!source)return;
        if(source.size>24576){showError(new Error('Scheme file exceeds 24 KiB'));return;}
        try{await run('import','',name.value,await source.text());}catch(error){showError(error);}finally{file.value='';}});
      tools.appendChild(file);buttons.push(file,select,name);body.appendChild(tools);
      body.appendChild(node('p','Save or cancel the open app editor before changing schemes. Import supports AnvilDroid scheme JSON v1. Reset clears the selected scheme.','hint'));
      status.textContent='';body.appendChild(status);
    }
    details.addEventListener('toggle',()=>{if(details.open&&!loaded)run('list');});
    return details;
  }
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
        const updateKeys=()=>{chips.replaceChildren();for(const b of p.bindings){const chip=node('kbd',physicalLabel(b.evdev,b.input_source));chip.title=bindingTitle(b);chips.appendChild(chip);}
          if(!p.bindings.length)chips.appendChild(node('span','—','hint'));};
        updateKeys();app.appendChild(schemeControls(p,updateKeys));
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
  window.AnvilKeymapLibrary={refresh,record:id=>records.get(id),updateDiagnostics,schemeRequest,validateSchemes};
})();
