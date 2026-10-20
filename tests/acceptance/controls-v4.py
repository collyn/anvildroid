"""Opt-in V4 touch checks on the parent's authenticated disposable guest only."""
import math
import re


def exercise(c):
    require=c['require'];stage=c['v3_stage'];move=c['v3_move']
    reload=c['v3_reload'];finish=c['v3_finish'];point=c['v4_point']
    count=c['action_count'];samples=c['finger_samples'];observed=c['observed']

    def quiet(label,sequence):
        rows=stage(label,sequence)
        require(not observed(rows),label+': unexpected Android touch')

    def last_move(rows):
        values=[sample for sample in samples(rows) if sample[0]=='MOVE']
        require(values,'Expected actual Android finger movement')
        return values[-1]

    def content_size():
        sizes=[re.search(r'SIZE contentPx=(\d+)x(\d+)',row) for row in c['logs']()]
        sizes=[tuple(map(int,size.groups())) for size in sizes if size]
        require(sizes,'Actual Android content dimensions missing')
        return sizes[-1]

    joystick=point(key=23,kind=1,nx=18000,ny=40000,radius=9000,ramp_ms=80,
                   up_key=23,left_key=36,down_key=37,right_key=38,
                   walk_key=42,walk_radius=40)
    reload([joystick],'v4-custom-movement')
    quiet('v4-old-wasd-inactive',['key','w'])
    motion=stage('v4-custom-up',['keydown','i'],lambda rows:count(rows,'MOVE')>0,settle=.25)
    origin=samples(motion)[0]
    end=last_move(motion)
    require(end[3]<origin[3]-2 and abs(end[2]-origin[2])<=2,'Custom up did not move vertically up')
    diagonal=stage('v4-custom-diagonal',['keydown','l'],lambda rows:count(rows,'MOVE')>0)
    end=last_move(diagonal)
    require(end[2]>origin[2]+2 and end[3]<origin[3]-2,'Custom directions did not combine diagonally')
    motion+=diagonal
    motion+=stage('v4-custom-release',['keyup','l','keyup','i'],lambda rows:count(rows,'UP')==1)
    finish('v4-custom-direction-diagonal',motion,downs=1,peak=1)
    require(len({s[1] for s in samples(motion)})==1,'Custom joystick replaced its finger')

    motion=stage('v4-run-start',['keydown','l'],lambda rows:count(rows,'MOVE')>0,settle=.25)
    origin=samples(motion)[0];running=last_move(motion)
    walking=stage('v4-walk-modifier',['keydown','Shift_L'],lambda rows:count(rows,'MOVE')>0,settle=.2)
    slow=last_move(walking)
    require(0<slow[2]-origin[2]<(running[2]-origin[2])*.65,'Walk modifier did not reduce joystick radius')
    restored=stage('v4-run-modifier-release',['keyup','Shift_L'],lambda rows:count(rows,'MOVE')>0,settle=.2)
    fast=last_move(restored)
    require(abs(fast[2]-running[2])<=3,'Releasing modifier did not restore run radius')
    motion+=walking+restored
    motion+=stage('v4-run-release',['keyup','l'],lambda rows:count(rows,'UP')==1)
    finish('v4-walk-radius-modifier',motion,downs=1,peak=1)

    skill=point(key=34,kind=4,nx=32000,ny=32000,radius=5000,
                cancel_key=46,cancel_x=55000,cancel_y=12000)
    reload([skill],'v4-skill-release')
    quiet('v4-skill-seed',move(220,320))
    cast=stage('v4-skill-start',['keydown','g'],lambda rows:count(rows,'DOWN')==1)
    moved=stage('v4-skill-radius',move(400,400),lambda rows:count(rows,'MOVE')>0)
    cast+=moved
    origin=samples(cast)[0];target=last_move(moved);w,h=content_size()
    # A circle in shorter-edge pixels, rather than a normalized-coordinate
    # ellipse. Android view dimensions are observed, never inferred from Xvfb.
    distance=math.hypot(target[2]-origin[2],target[3]-origin[3])
    require(1<distance<=5000*min(w,h)/65535+3,'Skill moved outside its authored radius')
    require(not count(cast,'UP'),'Release-mode skill ended before key-up')
    cast+=stage('v4-skill-release',['keyup','g'],lambda rows:count(rows,'UP')==1)
    finish('v4-skill-radius-release',cast,downs=1,peak=1)

    cast=stage('v4-skill-cancel-start',['keydown','g'],lambda rows:count(rows,'DOWN')==1)
    cancelled=stage('v4-skill-cancel',['key','c'],lambda rows:count(rows,'UP')==1)
    cast+=cancelled
    terminal=[s for s in samples(cancelled) if s[0]=='UP'][-1]
    require(abs(terminal[2]-55000*w/65535)<=3 and abs(terminal[3]-12000*h/65535)<=3,
            'Skill cancellation did not release at authored cancel position')
    quiet('v4-skill-cancel-no-replay',['keyup','g'])
    finish('v4-skill-cancel-position',cast,downs=1,peak=1)

    toggle=point(key=34,kind=4,nx=32000,ny=32000,radius=5000,skill_mode=2)
    reload([toggle],'v4-skill-toggle')
    cast=stage('v4-skill-toggle-on',['keydown','g'],lambda rows:count(rows,'DOWN')==1)
    released=stage('v4-skill-toggle-keyup',['keyup','g'])
    require(not count(released,'UP') and not count(released,'CANCEL'),'Toggle skill ended on key-up')
    cast+=released
    cast+=stage('v4-skill-toggle-off',['key','g'],lambda rows:count(rows,'UP')==1)
    finish('v4-skill-toggle-lifetime',cast,downs=1,peak=1)

    immediate=point(key=34,kind=4,nx=32000,ny=32000,radius=5000,skill_mode=1)
    reload([immediate],'v4-skill-immediate')
    cast=stage('v4-skill-immediate-down',['keydown','g'],lambda rows:count(rows,'DOWN')==1 and count(rows,'UP')==1)
    quiet('v4-skill-immediate-no-repeat',['keyup','g'])
    finish('v4-skill-immediate-lifetime',cast,downs=1,peak=1)

    walk=point(key=273,source=1,kind=9,nx=18000,ny=40000,radius=8000)
    reload([walk],'v4-mouse-walk')
    quiet('v4-mouse-walk-position',move(400,320))
    motion=stage('v4-mouse-walk-start',['mousedown','3'],lambda rows:count(rows,'DOWN')==1 and count(rows,'MOVE')>0)
    origin=samples(motion)[0];target=last_move(motion)
    require(target[2]>origin[2]+2,'Right-click walk did not point joystick toward cursor')
    motion+=stage('v4-mouse-walk-release',['mouseup','3'],lambda rows:count(rows,'UP')==1)
    finish('v4-right-mouse-walk',motion,downs=1,peak=1)


def exercise_capture(c):
    """Require lock-dependent Android input and one pointer owner at a time."""
    require=c['require'];stage=c['v3_stage'];move=c['v3_move']
    reload=c['v3_reload'];finish=c['v3_finish'];point=c['v4_point']
    count=c['action_count'];samples=c['finger_samples'];observed=c['observed']
    # Escape cancels the complete mapper stream. The native sink may deliver
    # touchscreen/FINGER CANCEL instead of UP; the balanced oracle below still
    # requires every contact to end (this is not a framework-cancel exemption).
    def ended(rows):return count(rows,'UP')+count(rows,'CANCEL')==1
    camera=point(key=38,kind=8,nx=44000,ny=32000,look_width=12000,look_height=12000,
                 sensitivity_x=100,sensitivity_y=100,activation=1,suspend_key=56)
    fire=point(key=273,kind=0,nx=52000,ny=50000,source=1,end_mode=2,drag_shot=1)
    reload([camera,fire],'v4-capture')

    def no_finger(rows,reason):
        require(not any('source=0x1002' in row for row in observed(rows)) and
                not any('AnvilKeymap:' in row and 'touch package=' in row for row in rows),reason)

    contact=stage('v4-capture-start',['key','l'],lambda rows:count(rows,'DOWN')==1)
    contact+=stage('v4-capture-suspend',['keydown','Alt_L'],lambda rows:count(rows,'UP')==1)
    free=stage('v4-capture-suspended-motion',move(240,340))
    no_finger(free,'Camera consumed free-pointer movement during suspension')
    contact+=stage('v4-capture-resume',['keyup','Alt_L'],lambda rows:count(rows,'DOWN')==1)
    contact+=stage('v4-capture-resumed-motion',['mousemove_relative','--','6','0'],lambda rows:count(rows,'MOVE')>0)
    contact+=stage('v4-capture-resumed-escape',['key','Escape'],ended)
    finish('v4-capture-suspend-resume',contact,downs=2,peak=1)

    contact=stage('v4-drag-shot-camera',['key','l'],lambda rows:count(rows,'DOWN')==1)
    shot=stage('v4-drag-shot-down',['mousedown','3'],lambda rows:count(rows,'UP')==1 and count(rows,'DOWN')==1)
    contact+=shot
    start=[s for s in samples(shot) if s[0]=='DOWN'][-1]
    moved=stage('v4-drag-shot-motion',['mousemove_relative','--','8','0'],lambda rows:count(rows,'MOVE')>0)
    require(all(s[1]==start[1] for s in samples(moved)),'Camera also consumed Fire-owned pointer motion')
    require(any(s[2]>start[2]+1 for s in samples(moved)),'Drag shot did not move the firing finger')
    contact+=moved
    contact+=stage('v4-drag-shot-release',['mouseup','3'],lambda rows:count(rows,'UP')==1 and count(rows,'DOWN')==1)
    contact+=stage('v4-drag-shot-camera-restored',['mousemove_relative','--','6','0'],lambda rows:count(rows,'MOVE')>0)
    contact+=stage('v4-drag-shot-escape',['key','Escape'],ended)
    finish('v4-drag-shot-pointer-handoff',contact,downs=3,peak=1)

    contact=stage('v4-suspended-blur-start',['key','l'],lambda rows:count(rows,'DOWN')==1)
    contact+=stage('v4-suspended-blur-suspend',['keydown','Alt_L'],lambda rows:count(rows,'UP')==1)
    blurred=stage('v4-suspended-blur',['windowfocus','--sync',c['root_window'][1],'keyup','Alt_L'])
    no_finger(blurred,'Suspended camera resumed in another window')
    activate=['windowfocus','--sync',c['v3_window']()]+move(220,320)
    if c['nested']:activate+=['click','1']
    activation=stage('v4-suspended-blur-refocus',activate,settle=.3)
    no_finger(activation,'Suspended camera replayed on focus restoration')
    # Drain any compositor-held modifier state after refocus, before a fresh
    # toggle. Releasing an old modifier must never reactivate a camera.
    drained=stage('v4-suspended-blur-modifier-up',['keyup','Alt_L'])
    no_finger(drained,'Old modifier release replayed suspended camera')
    contact+=stage('v4-suspended-blur-fresh',['key','l'],lambda rows:count(rows,'DOWN')==1)
    contact+=stage('v4-suspended-blur-final-escape',['key','Escape'],ended)
    finish('v4-capture-suspended-focus-recovery',contact,downs=2,peak=1)
