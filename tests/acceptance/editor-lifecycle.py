"""Extra opt-in scenarios, using the authenticated controls probe's endpoints.

Never creates an endpoint or selects a host desktop. All operations use the
parent probe's verified disposable guest, private display and bounded runner.
"""
import re
import time


def exercise(c):
    require=c['require']
    stage=c['v3_stage'];move=c['v3_move'];finish=c['v3_finish']
    click=c['editor_click'];open_editor=c['editor_open'];measure=c['editor_measure']
    saved_bytes=c['editor_bytes'];reload=c['v3_reload'];run=c['run'];guest=c['guest']
    observed=c['observed'];count=c['action_count'];results=c['results']
    output=c['args'].output;runtime=c['m']['id'];nested=c['nested']
    targets=c['editor_targets'];complete=c['gesture_complete']
    package=c['PACKAGE'];window=lambda:c['editor_window']()[0]
    def record(name,**extra):results.append(dict(scenario=name,passed=True,**extra))
    def quiet(label,sequence):
        rows=stage(label,sequence,settle=.25)
        require(not observed(rows),label+': editor leaked Android touch')
        return rows
    def pixels(label,geometry,frame,center,size=(60,30)):
        ox,oy,w,h=geometry;cx,cy=center;cw,ch=size
        quiet(label+'-pointer',move(frame[0]+ox+10,frame[1]+oy+h//2))
        # Re-read the current scene offset after maximize/restore.
        offset=c['editor_window']()[1] if not nested else c['scene_rectangle'](
            run(c['input_prefix']+['env','XDG_RUNTIME_DIR=/run/user/'+str(c['m']['owner_uid']),
                'WAYLAND_DISPLAY='+c['transport']['wayland_display'],'weston-debug','scene-graph'],timeout=5),runtime)
        crop=f'{cw}x{ch}+{offset[0]+frame[0]+ox+cx-cw//2}+{offset[1]+frame[1]+oy+cy-ch//2}'
        target=output/(label+'.rgb')
        run(c['input_prefix']+c['input_env']+['import','-window',window(),'-crop',crop,'+repage','-depth','8','rgb:'+str(target)])
        data=target.read_bytes();require(len(data)==cw*ch*3,'Incomplete pixel capture')
        return data
    def rows_from(text):
        lines=text.splitlines();header=lines[0].split()
        require(header[:4]==['ANVIL_KEYMAP','4',runtime,package],'Saved profile lost runtime/package scope')
        rows=[tuple(map(int,line.split())) for line in lines[1:]]
        require(len(rows)==int(header[4]) and all(len(row)==31 for row in rows),'Incomplete V4 profile')
        return rows
    def wait_save(label,before):
        deadline=time.monotonic()+5
        while True:
            data=saved_bytes();(output/(label+'.txt')).write_text(data)
            if data!=before:return data,rows_from(data)
            require(time.monotonic()<deadline,label+': profile did not change');time.sleep(.1)
    def playback(label,key):
        rows=stage(label+'-down',['keydown',key],lambda r:count(r,'DOWN')==1)
        rows+=stage(label+'-up',['keyup',key],lambda r:complete(rows+r))
        finish(label,rows,downs=1,peak=1)
        return rows
    baseline=saved_bytes();original=rows_from(baseline)
    # An empty saved profile has no passive hints object. Try must still own
    # focus cleanup for its unsaved draft, even when only keyboard focus leaves
    # the app/outer Weston and the pointer remains completely stationary.
    reload([],'editor-life-empty-try-baseline')
    empty=saved_bytes();require(rows_from(empty)==[],'Empty Try fixture retained saved controls')
    geometry,frame,_=open_editor('editor-life-empty-try-open')
    w,h=geometry[2:];buttons=targets(w,h)
    point=((w-min(w-16,276)-8)//2,round(h*.64))
    click('editor-life-empty-try-add',geometry,frame,buttons['add'])
    click('editor-life-empty-try-place',geometry,frame,point)
    quiet('editor-life-empty-try-bind',['key','j'])
    # A freshly placed Basic defaults to Tap. Explicitly switch its named
    # second inspector row to Hold, then witness that it stays down below.
    click('editor-life-empty-try-hold',geometry,frame,buttons['tap'])
    click('editor-life-empty-try-start',geometry,frame,buttons['try'])
    require(saved_bytes()==empty,'Try persisted its first unsaved control')
    held=stage('editor-life-empty-try-down',['keydown','j'],lambda r:count(r,'DOWN')==1,settle=.35)
    require(not count(held,'UP') and not count(held,'CANCEL'),
            'Draft Basic did not remain held while its physical key was down')
    pointer_before=run(c['x']+['getmouselocation','--shell'])
    root_window=c['root_window'][1]
    blurred=stage('editor-life-empty-try-keyboard-blur',['windowfocus','--sync',root_window],
                  lambda r:complete(held+r),settle=.25)
    require(int(run(c['x']+['getwindowfocus']).strip(),0)==int(root_window,0),
            'Keyboard-only blur did not focus the private X11 root')
    pointer_after=run(c['x']+['getmouselocation','--shell'])
    def pointer_xy(text):
        values=dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        return values.get('X'),values.get('Y')
    require(pointer_xy(pointer_before)==pointer_xy(pointer_after),'Keyboard-only blur moved the pointer')
    (output/'editor-life-empty-try-focus.txt').write_text(
        'private_root='+root_window+'\nBEFORE\n'+pointer_before+'AFTER\n'+pointer_after)
    held+=blurred
    finish('editor-life-empty-try-blur-terminal',held,downs=1,peak=1)
    # No key-up has been sent yet: restoring keyboard focus must not replay
    # the held trigger, and releasing that cancelled key must remain silent.
    refocused=quiet('editor-life-empty-try-refocus',['windowfocus','--sync',window(),'sleep','.35'])
    c['require_no_mapped_contact'](refocused,'Empty Try replayed its held key during refocus')
    require(int(run(c['x']+['getwindowfocus']).strip(),0)==int(window(),0),
            'Keyboard-only refocus did not restore the private app/Weston focus')
    if nested:
        # X11 FocusIn restores the outer Weston window, not its inner
        # wl_keyboard focus. The stationary-pointer blur above is already
        # proved. Activate this app away from Return and the sole draft point,
        # then require one ordinary mouse click and no mapped finger replay.
        ox,oy=geometry[:2]
        activation=stage('editor-life-empty-try-inner-activation',
                         move(frame[0]+ox+w//2,frame[1]+oy+80)+['click','1'],settle=.25)
        c['require_no_mapped_contact'](activation,'Empty Try replayed a held key during inner app activation')
        clicks=observed(activation)
        require(len(clicks)==2 and ' ACTION_DOWN ' in clicks[0] and
                ' ACTION_UP ' in clicks[-1] and 'activeAfter=0' in clicks[-1] and
                all('source=0x2002' in row and 'tool=MOUSE(3)' in row for row in clicks),
                'Empty Try inner activation was not one ordinary mouse click')
    released=quiet('editor-life-empty-try-cancelled-keyup',['keyup','j'])
    c['require_no_mapped_contact'](released,'Empty Try emitted touch when the cancelled key was released')
    playback('editor-life-empty-try-fresh','j')
    quiet('editor-life-empty-try-return',['key','Escape'])
    require(saved_bytes()==empty,'Returning from Try persisted the draft')
    click('editor-life-empty-try-cancel',geometry,frame,buttons['cancel'])
    require(saved_bytes()==empty,'Cancelling the first draft changed empty persisted bytes')
    record('editor-life-empty-try-keyboard-focus',empty_profile_preserved=True,
           stationary_pointer=True,held_refocus_suppressed=True,fresh_press_recovered=True)
    reload(original,'editor-life-empty-try-restore')
    require(saved_bytes()==baseline,'Empty Try test did not restore the original profile bytes')

    geometry,frame,_=open_editor('editor-life-open');w,h=geometry[2:];buttons=targets(w,h)
    authored=original[-1];point=(round(authored[1]*(w-1)/65535),round(authored[2]*(h-1)/65535))
    click('editor-life-select',geometry,frame,point)
    destination=((w-min(w-16,276)-8)//2,round(h*.71));ox,oy=geometry[:2]
    quiet('editor-life-drag',move(frame[0]+ox+point[0],frame[1]+oy+point[1])+['mousedown','1']+
          move(frame[0]+ox+destination[0],frame[1]+oy+destination[1])+['sleep','.15','mouseup','1'])
    click('editor-life-bind',geometry,frame,buttons['bind_key'])
    # WASD owns W even though it is represented by one control. The rejected
    # rebind must keep the original Z control and show a rendered error.
    panel_x=w-min(w-16,276)-8;status_width=min(panel_x-24,220)
    require(status_width>=32,'Editor status area too narrow for pixel oracle')
    status=(12+status_width//2,h-27)
    before=pixels('editor-life-before-conflict',geometry,frame,status,(status_width,24))
    quiet('editor-life-conflict',['key','w'])
    after=pixels('editor-life-conflict-pixels',geometry,frame,status,(status_width,24))
    require(after!=before,'Binding conflict had no visible feedback')
    require(any(after[i]>180 and after[i+1]<190 and after[i+2]<180 for i in range(0,len(after),3)),
            'Conflict feedback did not render its error color')
    require(saved_bytes()==baseline,'Rejected binding changed persisted bytes')
    # Deliberately different bindings in each concurrent runtime.
    key,evdev=('c',46) if nested else ('x',45)
    quiet('editor-life-rebind',['key',key])
    click('editor-life-save',geometry,frame,buttons['save'])
    saved,rows=wait_save('editor-life-drag-rebind-profile',baseline)
    require(rows[:-1]==original[:-1] and rows[-1][0]==evdev,'Rebinding changed unrelated controls')
    require(abs(rows[-1][1]-round(destination[0]*65535/(w-1)))<=2 and
            abs(rows[-1][2]-round(destination[1]*65535/(h-1)))<=2,'Dragged center was not persisted')
    playback('editor-life-drag-rebind-playback',key)
    quiet('editor-life-old-binding',['key','z'])
    record('editor-life-conflict-recovery',rendered_error=True,unrelated_rows_unchanged=True)

    geometry,frame,_=open_editor('editor-life-toolbar-delete-open')
    w,h=geometry[2:];buttons=targets(w,h)
    click('editor-life-toolbar-delete-select',geometry,frame,
          (round(rows[-1][1]*(w-1)/65535),round(rows[-1][2]*(h-1)/65535)))
    click('editor-life-toolbar-delete',geometry,frame,buttons['remove'])
    click('editor-life-toolbar-delete-save',geometry,frame,buttons['save'])
    deleted,remaining=wait_save('editor-life-toolbar-delete-profile',saved)
    require(remaining==rows[:-1],'Toolbar Delete changed unrelated controls')
    quiet('editor-life-toolbar-delete-old-key',['key',key])
    record('editor-life-toolbar-delete',persisted_count=len(remaining),removed_binding_inactive=True)
    # Restore the test baseline through the same explicit fixture setup path
    # used by the V3 input cases; the deletion above was authored graphically.
    reload(rows,'editor-life-after-delete')
    require(saved_bytes()==saved,'Test baseline restore changed profile bytes')

    geometry,frame,_=open_editor('editor-life-invalid-open');w,h=geometry[2:];buttons=targets(w,h)
    point=(round(w*.27),round(h*.45))
    click('editor-life-invalid-add',geometry,frame,buttons['add'])
    click('editor-life-invalid-place',geometry,frame,point)
    click('editor-life-invalid-save',geometry,frame,buttons['save'])
    require(saved_bytes()==saved,'Unassigned control replaced a good saved profile')
    # Local X delete while binding capture is armed exercises the lifecycle
    # regression independently of the toolbar Delete path.
    click('editor-life-delete-arm',geometry,frame,buttons['bind_key'])
    click('editor-life-delete-badge',geometry,frame,(point[0]+25,point[1]-25))
    click('editor-life-delete-save',geometry,frame,buttons['save'])
    playback('editor-life-invalid-delete-recovery',key)
    require(saved_bytes()==saved,'Deleting the invalid draft changed existing controls')
    record('editor-life-invalid-save-delete',profile_preserved=True)

    # Pixel visibility is independent of playback. Keep the observer static
    # and compare RGB, with the pointer away in every sample.
    geometry,frame,_=open_editor('editor-life-marker-measure')
    click('editor-life-marker-close',geometry,frame,targets(*geometry[2:])['cancel'])
    w,h=geometry[2:];marker=(round(rows[-1][1]*(w-1)/65535),round(rows[-1][2]*(h-1)/65535))
    visible=pixels('editor-life-marker-visible',geometry,frame,marker)
    quiet('editor-life-marker-hide',['key','F4'])
    hidden=pixels('editor-life-marker-hidden',geometry,frame,marker)
    require(hidden!=visible,'F4 did not hide marker pixels')
    quiet('editor-life-marker-show',['key','F4'])
    restored=pixels('editor-life-marker-restored',geometry,frame,marker)
    require(restored==visible,'F4 did not restore the exact passive marker pixels')
    quiet('editor-life-marker-hide-playback',['key','F4'])
    playback('editor-life-hidden-marker-playback',key)
    quiet('editor-life-marker-final-show',['key','F4'])
    record('editor-life-marker-pixels',hidden_and_restored=True)

    # Cancel a dirty draft by replacing its app process/task, then require the
    # persisted profile and a fresh mapped press after reopening.
    geometry,frame,_=open_editor('editor-life-replacement-open')
    click('editor-life-replacement-add',geometry,frame,targets(*geometry[2:])['add'])
    click('editor-life-replacement-place',geometry,frame,(round(geometry[2]*.27),round(geometry[3]*.45)))
    reload(rows,'editor-life-replacement')
    require(saved_bytes()==saved,'App replacement persisted an unsaved draft')
    playback('editor-life-app-reopen',key)
    record('editor-life-dirty-task-replacement',profile_preserved=True)

    # Use production CSD controls rather than changing the personal desktop or
    # resizing the outer nested transport. A maximize/restore configure must
    # actually change the scoped app's fitted editor rectangle.
    def maximize(label):
        geometry,frame,_=measure(label+'-before')
        ox,oy,w,h=geometry
        # Header is immediately above the raw client surface, including fitted
        # offsets. The production button center is 54px from its right edge.
        px=frame[0]+ox+w-54;py=frame[1]+oy-17
        if nested:return stage(label,move(px,py)+['click','1'],settle=2.5)
        # This fixture deliberately has no desktop WM. Supply its EWMH state
        # and ConfigureNotify exactly as a WM would, without starting another
        # desktop session or claiming physical KWin/GNOME acceptance.
        info=dict(line.split('=',1) for line in run(c['x']+['getwindowgeometry','--shell',window()]).splitlines() if '=' in line)
        maximizing='restore' not in label
        if maximizing:c['normal_x11_size']=(info['WIDTH'],info['HEIGHT'])
        width,height=('1510','938') if maximizing else c['normal_x11_size']
        state='_NET_WM_STATE_MAXIMIZED_VERT, _NET_WM_STATE_MAXIMIZED_HORZ' if maximizing else ''
        def configure():
            run(c['input_prefix']+c['input_env']+['xprop','-id',window(),'-f','_NET_WM_STATE','32a','-set','_NET_WM_STATE',state])
            run(c['x']+['windowsize',window(),width,height])
        return stage(label,configure,settle=2.5)
    held=stage('editor-life-maximize-down',['keydown','w'],lambda r:count(r,'DOWN')==1)
    before=measure('editor-life-maximize-geometry-before')[0]
    held+=maximize('editor-life-maximize')
    after=measure('editor-life-maximize-geometry-after')[0]
    require(after!=before,'Maximize did not change the actual app geometry')
    held+=stage('editor-life-maximize-release',['keyup','w'],lambda r:complete(held+r))
    finish('editor-life-maximize-held-contact',held,downs=1,peak=1)
    playback('editor-life-maximize-fresh',key)
    maximize('editor-life-restore')
    restored=measure('editor-life-restore-geometry')[0]
    require(restored!=after,'Restore did not change maximized geometry')
    require(saved_bytes()==saved,'Maximize/restore rewrote normalized coordinates')
    record('editor-life-maximize-restore',before=list(before),maximized=list(after),restored=list(restored),
           host_control='production-csd' if nested else 'private-ewmh-wm-fixture')

    # Resize during an editor drag. Resizing may preserve the overlay or
    # replace its Android surface; either way an old press cannot save a draft.
    geometry,frame,_=open_editor('editor-life-resize-open')
    w,h=geometry[2:];p=(round(rows[-1][1]*(w-1)/65535),round(rows[-1][2]*(h-1)/65535))
    ox,oy=geometry[:2]
    quiet('editor-life-resize-drag-down',move(frame[0]+ox+p[0],frame[1]+oy+p[1])+['mousedown','1'])
    task_text=run(guest+['--','/system/bin/cmd','activity','stack','list'])
    task_ids=re.findall(r'taskId=(\d+):\s*'+re.escape(package)+r'/',task_text)
    require(len(task_ids)==1,'Task resize requires one scoped observer task')
    task=task_ids[0]
    bounds=re.search(r'taskId='+task+r':[^\n]*bounds=\[(\d+),(\d+)\]\[(\d+),(\d+)\]',task_text)
    require(bounds is not None,'Original task bounds unavailable')
    quiet('editor-life-resize-during-drag',lambda:run(guest+['--','/system/bin/cmd','activity','task','resize',task,'0','0','780','560']))
    quiet('editor-life-resize-stale-release',['mouseup','1'])
    require(saved_bytes()==saved,'Resize committed an unsaved editor drag')
    run(guest+['--','/system/bin/cmd','activity','task','resize',task,*bounds.groups()])
    time.sleep(1)
    reload(rows,'editor-life-resize-reopen')
    playback('editor-life-resize-fresh',key)
    record('editor-life-resize-draft-cancel',profile_preserved=True)

    density_text=run(guest+['--','/system/bin/wm','density'])
    densities=re.findall(r'(?:Physical|Override) density: (\d+)',density_text)
    require(densities,'Android density unavailable')
    density=220 if int(densities[-1])!=220 else 240
    held=stage('editor-life-density-down',['keydown','w'],lambda r:count(r,'DOWN')==1)
    held+=stage('editor-life-density-change',lambda:run(guest+['--','/system/bin/wm','density',str(density)]),settle=1)
    density_after=run(guest+['--','/system/bin/wm','density'])
    (output/'editor-life-density.txt').write_text(density_text+'\nAFTER\n'+density_after)
    require('Override density: '+str(density) in density_after,'Density override did not take effect')
    require(any(' SIZE contentPx=' in row and 'density='+str(density/160) in row for row in held),
            'Observer did not receive the new Android density')
    held+=stage('editor-life-density-release',['keyup','w'],lambda r:complete(held+r,True))
    finish('editor-life-density-held-contact',held,downs=1,peak=1,framework_cancel=True)
    reload(rows,'editor-life-density-reopen');playback('editor-life-density-fresh',key)
    require(saved_bytes()==saved,'DPI change rewrote profile coordinates')
    run(guest+['--','/system/bin/wm','density','reset'])
    reload(rows,'editor-life-density-reset')
    record('editor-life-density',density_override=density,profile_preserved=True)

    held=stage('editor-life-rotation-down',['keydown','w'],lambda r:count(r,'DOWN')==1)
    held+=stage('editor-life-rotation-change',lambda:run(guest+['--','/system/bin/wm','user-rotation','lock','1']),settle=1)
    rotation=run(guest+['--','/system/bin/dumpsys','window','displays'])
    (output/'editor-life-rotation.txt').write_text(rotation)
    require(re.search(r'(?:mRotation=|rotation=)(?:ROTATION_90|1)(?:\s|\b)',rotation) is not None,
            'Actual Android display did not rotate to 90 degrees')
    held+=stage('editor-life-rotation-release',['keyup','w'],lambda r:complete(held+r,True))
    finish('editor-life-rotation-held-contact',held,downs=1,peak=1,framework_cancel=True)
    reload(rows,'editor-life-rotation-reopen');playback('editor-life-rotation-fresh',key)
    require(saved_bytes()==saved,'Rotation rewrote normalized profile coordinates')
    run(guest+['--','/system/bin/wm','user-rotation','lock','0'])
    reload(rows,'editor-life-rotation-reset')
    record('editor-life-rotation',actual_rotation=90,profile_preserved=True)
