"""Scheme API and live reload checks on the parent's authenticated guest only.

The parent supplies its verified worker request, private input display, editor
helpers and Android touch oracle. This module never creates an endpoint, changes
credentials, restarts the observer, or launches a fixture of its own.
"""
import json
import re


def exercise(c):
    require=c['require'];stage=c['v3_stage'];finish=c['v3_finish']
    request=c['request'];saved_bytes=c['editor_bytes'];open_editor=c['editor_open']
    click=c['editor_click'];targets=c['editor_targets'];count=c['action_count']
    observed=c['observed'];complete=c['gesture_complete'];results=c['results']
    output=c['args'].output;runtime=c['m']['id'];package=c['PACKAGE']
    run=c['run'];guest=c['guest']
    state=None;created=[];editing=False

    def record(name,**extra):
        results.append(dict(scenario=name,passed=True,**extra))

    def pid():
        value=run(guest+['--','/system/bin/pidof',package]).strip()
        require(value.isdecimal(),'Scheme observer PID is missing or ambiguous')
        return value

    def payload(action,scheme_id='',name='',content='',revision=None):
        return dict(op='keymap_scheme',id=runtime,package=package,action=action,
                    scheme_id=scheme_id,name=name,content=content,
                    revision='' if action=='list' else revision if revision is not None else state['revision'])

    def validate(value):
        require(isinstance(value,dict) and value.get('runtime_id')==runtime and
                value.get('package')==package and
                re.fullmatch(r'[0-9a-f]{64}',value.get('revision','')) and
                isinstance(value.get('schemes'),list),'Invalid actual scheme response')
        ids=[item.get('id') for item in value['schemes']]
        require(1<=len(ids)<=16 and len(ids)==len(set(ids)) and
                'default' in ids and value.get('selected') in ids,'Invalid actual scheme selection')
        return value

    def api(label,action,scheme_id='',name='',content='',revision=None):
        nonlocal state
        response=[]
        rows=stage(label,lambda:response.append(request(payload(action,scheme_id,name,content,revision))),settle=.3)
        require(not observed(rows),label+': scheme request emitted an unsolicited touch')
        require(len(response)==1 and 'keymap_scheme' in response[0],'Worker omitted scheme response')
        state=validate(response[0]['keymap_scheme'])
        (output/(label+'.json')).write_text(json.dumps(state,indent=2)+'\n')
        return state

    def create(label,action,name,scheme_id='',content=''):
        before={item['id'] for item in state['schemes']}
        api(label,action,scheme_id,name,content)
        added={item['id'] for item in state['schemes']}-before
        require(len(added)==1,label+': operation did not add exactly one inactive scheme')
        identifier=added.pop();created.append(identifier)
        require(identifier!=state['selected'],label+': new scheme was silently selected')
        return identifier

    def reject(label,action,reason,scheme_id='',name='',content='',revision=None):
        before=saved_bytes();prior=state['revision'];failure=[]
        def attempt():
            try:request(payload(action,scheme_id,name,content,revision))
            except RuntimeError as error:failure.append(str(error))
        rows=stage(label,attempt,settle=.25)
        require(not observed(rows),label+': refused request emitted Android touch')
        require(len(failure)==1 and re.search(reason,failure[0],re.IGNORECASE),
                label+': request was not refused for the expected reason: '+str(failure))
        (output/(label+'-refusal.txt')).write_text(failure[0]+'\n')
        require(saved_bytes()==before,label+': refused request changed exact active bytes')
        api(label+'-unchanged','list')
        require(state['revision']==prior,label+': refused request changed scheme metadata')

    def playback(label,key):
        rows=stage(label+'-down',['keydown',key],lambda lines:count(lines,'DOWN')==1)
        rows+=stage(label+'-up',['keyup',key],lambda lines:complete(rows+lines))
        finish(label,rows,downs=1,peak=1)
        require(pid()==observer_pid,label+': observer restarted during live scheme switching')
        return rows

    def quiet_key(label,key):
        rows=stage(label,['key',key],settle=.3)
        require(not observed(rows),label+': inactive scheme still injected a finger')
        require(not any('AnvilKeymap:' in row and 'touch package='+package in row for row in rows),
                label+': inactive scheme still attempted mapped input')
        require(pid()==observer_pid,label+': observer restarted')
        return rows

    original_bytes=saved_bytes();original=api('schemes-initial-list','list')
    original_id=original['selected'];original_ids={item['id'] for item in original['schemes']}
    require(len(original_ids)<=13,'Scheme acceptance needs room for three temporary schemes')
    header=original_bytes.splitlines()[0].split()
    require(header[:1]==['ANVIL_KEYMAP'] and header[2:4]==[runtime,package],
            'Original profile has unexpected scope')
    profile_rows=[tuple(map(int,line.split())) for line in original_bytes.splitlines()[1:]]
    require(profile_rows,'Scheme reload requires an existing mapped control')
    letters=dict(zip((16,17,18,19,20,21,22,23,24,25,30,31,32,33,34,35,36,37,38,44,45,46,47,48,49,50),
                     'qwertyuiopasdfghjklzxcvbnm'))
    candidates=[row for row in profile_rows if len(row)>=9 and row[4]==0 and
                (len(row)<19 or row[9]==0) and row[0] in letters]
    if not candidates:
        candidates=[row for row in profile_rows if len(row)>=9 and row[4]==1 and row[0] in letters]
    require(candidates,'Scheme reload needs a known keyboard Basic or joystick control')
    key=letters[candidates[0][0]];observer_pid=pid()
    (output/'schemes-original-profile.txt').write_text(original_bytes)

    try:
        playback('schemes-original-key',key)
        stale=state['revision']
        clone=create('schemes-clone-active','clone','Acceptance clone',original_id)
        require(saved_bytes()==original_bytes and state['selected']==original_id,
                'Cloning changed the original active profile')
        empty=create('schemes-create-empty','create','Acceptance empty')
        empty_entry=next(item for item in state['schemes'] if item['id']==empty)
        require(empty_entry['binding_count']==0 and saved_bytes()==original_bytes,
                'New inactive scheme changed active bindings')
        record('schemes-list-clone-create',inactive_creation=True)

        reject('schemes-stale-revision','rename',r'changed|refresh',clone,'Stale rename',revision=stale)
        record('schemes-stale-revision-refused',exact_profile_preserved=True,metadata_preserved=True)

        api('schemes-use-empty','select',empty)
        require(state['selected']==empty and not state.get('bindings'),
                'Empty scheme did not become the active empty mapping')
        require(int(saved_bytes().splitlines()[0].split()[4])==0,'Use empty did not persist an empty profile')
        quiet_key('schemes-empty-key-inactive',key)
        record('schemes-empty-live-reload',observer_pid=observer_pid,old_key_inactive=True)

        api('schemes-use-clone','select',clone)
        require(saved_bytes()==original_bytes and state['selected']==clone,
                'Selecting clone did not restore the exact authored profile')
        playback('schemes-clone-live-reload',key)

        editing=True
        geometry,frame,_=open_editor('schemes-editor-open')
        buttons=targets(geometry[2],geometry[3])
        # Make an unsaved visual draft before checking the worker's editor guard.
        click('schemes-editor-tools',geometry,frame,buttons['add'])
        w,h=geometry[2:]
        panel_left=w-min(w-16,276)-8
        occupied=[]
        for row in profile_rows:
            occupied.append((round(row[1]*(w-1)/65535),round(row[2]*(h-1)/65535)))
            if len(row)>=9:occupied.append((round(row[5]*(w-1)/65535),round(row[6]*(h-1)/65535)))
        positions=[(x,y) for y in range(120,h-48,48) for x in range(36,panel_left-28,36)
                   if all((x-px)**2+(y-py)**2>52**2 for px,py in occupied)]
        require(positions,'No uncovered editor position for the unsaved scheme draft')
        click('schemes-editor-place-unsaved',geometry,frame,positions[0])
        rows=stage('schemes-editor-bind-unsaved',['key','v'])
        require(not observed(rows),'Editor binding leaked gameplay touch')
        before=saved_bytes()
        api('schemes-editor-list-allowed','list')
        exported=api('schemes-editor-export-allowed','export',clone)
        envelope=json.loads(exported['content'])
        require(envelope['profile']==original_bytes and saved_bytes()==before,
                'Read-only export included unsaved draft or changed profile')
        reject('schemes-editor-mutation-refused','select',r'editor|save or cancel',empty)
        click('schemes-editor-cancel',geometry,frame,buttons['cancel']);editing=False
        require(saved_bytes()==original_bytes,'Editor Cancel changed exact scheme bytes')
        playback('schemes-editor-cancel-restored',key)
        record('schemes-editor-read-write-guard',list_allowed=True,export_allowed=True,
               mutation_refused=True,exact_profile_preserved=True)

        imported=dict(envelope)
        source_header=header.copy();source_header[2]='r-'+'f'*32
        source_header[3]='org.anvildroid.importsource'
        imported['profile']=' '.join(source_header)+'\n'+'\n'.join(original_bytes.splitlines()[1:])+'\n'
        imported['reference']={'width':464,'height':824,'orientation':'portrait'}
        imported_id=create('schemes-import-rebased','import','Acceptance import',content=json.dumps(imported))
        require(state['selected']==clone and saved_bytes()==original_bytes,'Import silently changed active scheme')
        exported=api('schemes-export-rebased','export',imported_id)
        rebased=json.loads(exported['content'])
        require(rebased['profile'].splitlines()[0].split()[2:4]==[runtime,package] and
                rebased['profile'].splitlines()[1:]==original_bytes.splitlines()[1:] and
                rebased['reference']==imported['reference'],'Imported scheme lost rebase, rows or reference metadata')
        record('schemes-import-export-rebase',inactive_import=True,reference_preserved=True)

        invalid=dict(imported);bad_rows=invalid['profile'].splitlines()
        bad=bad_rows[1].split();bad[1]='65536';bad_rows[1]=' '.join(bad)
        invalid['profile']='\n'.join(bad_rows)+'\n'
        reject('schemes-import-bounds-refused','import',r'unsupported|invalid scheme profile',
               name='Invalid bounds',content=json.dumps(invalid))
        invalid=dict(imported);invalid['profile']='ANVIL_KEYMAP 1 default org.anvildroid.importsource 0\n'+' '*16384+'\n'
        reject('schemes-profile-cap-refused','import',r'profile size|profile exceeds|invalid scheme',
               name='Oversized profile',content=json.dumps(invalid))
        reject('schemes-envelope-cap-refused','import',r'import exceeds limit',
               name='Oversized envelope',content=' '*24577)
        record('schemes-import-validation',coordinate_bounds=True,native_size_limit=True,envelope_size_limit=True)
    finally:
        # Restore only through the authenticated API; do not rewrite active files
        # behind the editor or discard any scheme that existed before this test.
        if editing:
            stage('schemes-cleanup-editor',['key','Escape','key','Escape','key','Escape'],settle=.3)
        api('schemes-cleanup-list','list')
        if state['selected']!=original_id:
            api('schemes-cleanup-original','select',original_id)
        for identifier in reversed(created):
            if identifier in {item['id'] for item in state['schemes']}:
                api('schemes-cleanup-remove-'+identifier[-8:],'remove',identifier)
        require(state['selected']==original_id and
                {item['id'] for item in state['schemes']}==original_ids,
                'Scheme cleanup did not restore original selection and inventory')
        require(saved_bytes()==original_bytes,'Scheme cleanup changed original exact profile bytes')
        require(pid()==observer_pid,'Scheme suite restarted the observer')
        record('schemes-original-restored',exact_profile_preserved=True,temporary_schemes_removed=True,
               observer_pid=observer_pid)
