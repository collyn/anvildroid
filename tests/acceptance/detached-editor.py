"""Real detached-panel acceptance on the parent probe's authenticated display.

The native X11 fixture has no desktop WM. Its narrowly scoped EWMH helper
answers only the target panel's production moveresize request; XTest still
performs the actual header press, pointer movement and release.
"""
import json
import re
import subprocess
import time


def panel_identity(runtime, package):
    if not re.fullmatch(r'(?:default|r-[a-z0-9]+)', runtime):
        raise RuntimeError('Invalid scoped panel runtime')
    if not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', package):
        raise RuntimeError('Invalid scoped panel package')
    return f'waydroid.anvildroid.{runtime}.{package}.keymap-editor'


def scene_panel(scene, identity, missing=False):
    sections=[part for part in scene.split('\n\tView ')
              if 'role xdg_toplevel' in part and ' of '+identity+',' in part
              and '[view is not mapped!]' not in part]
    if missing and not sections:
        return None
    if len(sections)!=1:
        raise RuntimeError('Expected one unambiguous scoped editor panel')
    match=re.search(r'position: \((-?\d+), (-?\d+)\) -> \((-?\d+), (-?\d+)\)',sections[0])
    if not match:
        raise RuntimeError('Panel scene geometry unavailable')
    rect=tuple(map(int,match.groups()))
    if rect[2]-rect[0]<300 or rect[3]-rect[1]<453:
        raise RuntimeError('Panel scene geometry too small')
    return rect


def outside(rect, canvas):
    return rect[2]<=canvas[0] or rect[0]>=canvas[2] or rect[3]<=canvas[1] or rect[1]>=canvas[3]


def visible_canvas_header(canvas):
    left,top,right,_=canvas
    x,y=left+80,max(33,top-4)
    if not (left+32<=x<right-100 and 0<=x<1600 and 33<=y<1000 and top-33<=y<top):
        raise RuntimeError('Android CSD has no safe visible header below Weston panel')
    return x,y


def wm_focus(window):
    if not isinstance(window,str) or not re.fullmatch(r'(?:[1-9][0-9]*|0x[0-9a-fA-F]+)',window) or int(window,16 if window.startswith('0x') else 10)==0:
        raise RuntimeError('Private WM focus requires an exact nonzero XID')
    return ['windowfocus','--sync',window]


def settled_marker(read, guard, present, timeout=5):
    deadline=time.monotonic()+timeout
    while True:
        guard();text=read()
        if present or not text:return text
        if time.monotonic()>=deadline:
            raise RuntimeError('Orphan editor ownership marker after bounded cleanup wait')
        time.sleep(.1)


def native_panel_snapshot(find, inspect, missing=False):
    windows=find()
    if missing and not windows:return None,None
    if len(windows)!=1:raise RuntimeError('Expected one exact scoped native panel')
    window=windows[0]
    try:return window,inspect(window)
    except subprocess.CalledProcessError as error:
        stderr=error.stderr or b''
        if isinstance(stderr,bytes):stderr=stderr.decode(errors='replace')
        if not missing or not re.search(r'\bBadWindow\b',stderr):raise
        # Destruction can occur between search and XGetGeometry/xprop. Only
        # a fresh scoped search can establish absence; a geometry error alone
        # never proves successful teardown.
        windows=find()
        if not windows:return None,None
        if len(windows)!=1:raise RuntimeError('Expected one exact scoped native panel')
        if windows[0]==window:raise
        return windows[0],inspect(windows[0])


def destination(canvas, width, height):
    for x in (canvas[2]+24,canvas[0]-width-24):
        y=64
        if 8<=x and x+width<=1592 and y+height<=992:
            return x,y
    raise RuntimeError('No visible private-output space outside Android for panel drag')


def canvas_points(rows, width, height):
    """Choose empty rendered canvas space, avoiding marker/badge/end handles."""
    anchors=[]
    for row in rows:
        anchors.extend([(row[1]*(width-1)/65535,row[2]*(height-1)/65535),
                        (row[5]*(width-1)/65535,row[6]*(height-1)/65535)])
        if row[4]==1:
            anchors.append((row[1]*(width-1)/65535+row[11]*min(width,height)/65535,
                            row[2]*(height-1)/65535))
        if row[4]==8:
            anchors.append(((row[1]+row[13])*(width-1)/65535,
                            (row[2]+row[14])*(height-1)/65535))
    for fy in (.42,.55,.68,.80,.32):
        for fx in (.20,.35,.50,.65,.80):
            point=(round(width*fx),round(height*fy));end=(point[0]+20,point[1]+34)
            if end[0]>=width-36 or end[1]>=height-42:continue
            if all((p[0]-a[0])**2+(p[1]-a[1])**2>60**2 for p in (point,end) for a in anchors):
                return point,end
    raise RuntimeError('No empty canvas space for detached marker placement/drag')


MOVE_FIXTURE=r'''
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
int main(int argc,char **argv) {
 if(argc!=3)return 2;
 Display *d=XOpenDisplay(0);if(!d)return 3;
 Window target=strtoul(argv[1],0,0),root=DefaultRootWindow(d);
 XClassHint cls={0};
 if(!XGetClassHint(d,target,&cls)||!cls.res_name||!cls.res_class||
    strcmp(cls.res_name,argv[2])||strcmp(cls.res_class,argv[2]))return 4;
 XFree(cls.res_name);XFree(cls.res_class);
 Atom move=XInternAtom(d,"_NET_WM_MOVERESIZE",False);
 XSelectInput(d,root,SubstructureNotifyMask);XSync(d,False);
 puts("ready scoped-ewmh-move-fixture");fflush(stdout);
 struct timespec begin,now;clock_gettime(CLOCK_MONOTONIC,&begin);
 int active=0,sx=0,sy=0,wx=0,wy=0,moved=0;
 for(;;) {
  while(XPending(d)) {XEvent e;XNextEvent(d,&e);
   if(e.type==ClientMessage&&e.xclient.window==target&&e.xclient.message_type==move&&
      e.xclient.data.l[2]==8&&e.xclient.data.l[3]==1) {
    Window child;XWindowAttributes a;
    XGetWindowAttributes(d,target,&a);XTranslateCoordinates(d,target,root,0,0,&wx,&wy,&child);
    sx=e.xclient.data.l[0];sy=e.xclient.data.l[1];active=1;
    puts("production-move-request");fflush(stdout);
   }
  }
  if(active) {Window rr,child;int px,py,lx,ly;unsigned mask;
   if(!XQueryPointer(d,root,&rr,&child,&px,&py,&lx,&ly,&mask))return 5;
   if(!(mask&Button1Mask)) {puts(moved?"moved-and-released":"released-without-motion");fflush(stdout);return moved?0:6;}
   if(px!=sx||py!=sy){XMoveWindow(d,target,wx+px-sx,wy+py-sy);XFlush(d);moved=1;}
  }
  clock_gettime(CLOCK_MONOTONIC,&now);if(now.tv_sec-begin.tv_sec>20)return 7;
  usleep(10000);
 }
}
'''


def exercise(c):
    # Validate/recheck the parent-owned disposable worker before any command.
    c['v3_guard']()
    require=c['require'];run=c['run'];stage=c['v3_stage'];observed=c['observed']
    output=c['args'].output;runtime=c['m']['id'];package=c['PACKAGE'];nested=c['nested']
    identity=panel_identity(runtime,package);x=c['x'];baseline=c['editor_bytes']()
    require('DISPLAY=:77' in c['input_env'],'Detached probe requires private XTest display')
    expected_authority=('XAUTHORITY='+c['transport']['authority'] if nested
                        else 'XAUTHORITY=/run/anvil-x11-authority')
    require(expected_authority in c['input_env'],'Detached probe requires private authority')
    def quiet(label,sequence):
        rows=stage(label,sequence,settle=.25)
        require(not observed(rows),label+': panel/editor leaked Android touch')
        return rows
    def scene(label):
        value=run(c['input_prefix']+['env','XDG_RUNTIME_DIR=/run/user/'+str(c['m']['owner_uid']),
                  'WAYLAND_DISPLAY='+c['transport']['wayland_display'],'weston-debug','scene-graph'],timeout=5)
        (output/(label+'-scene.txt')).write_text(value);return value
    def x_geometry(window):
        values=dict(line.split('=',1) for line in run(x+['getwindowgeometry','--shell',window]).splitlines() if '=' in line)
        a,b,w,h=(int(values[k]) for k in ('X','Y','WIDTH','HEIGHT'))
        return a,b,a+w,b+h
    panel_window=None
    def panel(label,missing=False):
        nonlocal panel_window
        c['v3_guard']()
        if nested:return scene_panel(scene(label),identity,missing)
        def find():
            c['v3_guard']()
            try:return run(x+['search','--onlyvisible','--class','^'+re.escape(identity)+'$']).splitlines()
            except subprocess.CalledProcessError as error:
                if error.returncode==1 and not (error.stdout or error.stderr):return []
                raise
        def inspect(window):
            cls=run(c['input_prefix']+c['input_env']+['xprop','-id',window,'WM_CLASS'])
            require(re.findall(r'"([^"]+)"',cls)==[identity,identity],'Panel WM_CLASS owner mismatch')
            return x_geometry(window)
        panel_window,rect=native_panel_snapshot(find,inspect,missing)
        return rect
    def wait_panel(label,present):
        deadline=time.monotonic()+5
        while True:
            rect=panel(label,missing=True)
            if bool(rect)==present:return rect
            require(time.monotonic()<deadline,label+': panel mapping did not settle');time.sleep(.1)
    def pointer(rect,point):
        px,py=rect[0]+point[0],rect[1]+point[1]
        if nested:return ['mousemove','--window',c['editor_window']()[0],str(px),str(py)]
        return ['mousemove',str(px),str(py)]
    def panel_click(label,point):
        rect=panel(label+'-before')
        # A desktop WM normally supplies X focus on click. This private
        # native fixture has none; focus only the exact class-verified XID.
        focus=[] if nested else wm_focus(panel_window)
        quiet(label,focus+pointer(rect,point)+['click','1'])
    def canvas_click(label,geometry,frame,point):
        if not nested:quiet(label+'-private-wm-focus',wm_focus(c['editor_window']()[0]))
        return c['editor_click'](label,geometry,frame,point)
    def bytes_unchanged(label,value):require(c['editor_bytes']()==value,label+': draft changed saved bytes')
    def marker(label,present,expected=None):
        def read():
            text=run(c['guest']+['--','/system/bin/sh','-c',
                     'if [ -f /data/waydroid_tmp/anvildroid/keymap-editing ]; then cat /data/waydroid_tmp/anvildroid/keymap-editing; fi'])
            (output/(label+'-editor-marker.txt')).write_text(text)
            return text
        # Native role teardown queues marker removal on the IO worker.
        text=settled_marker(read,c['v3_guard'],present)
        if present:
            fields=text.split()
            require(len(fields)==4 and fields[:2]==[c['m']['generation'],package] and
                    fields[2].isdecimal() and fields[3].isdecimal(),'Editor marker lost owning generation/package/process')
            if expected is not None:require(text==expected,'Detaching/docking replaced editor ownership marker')
        else:require(not text,'Orphan editor ownership marker after '+label)
        return text
    def rows(text):
        lines=text.splitlines();head=lines[0].split()
        require(head[:4]==['ANVIL_KEYMAP','4',runtime,package],'Detached Save lost V4 scope')
        value=[tuple(map(int,line.split())) for line in lines[1:]]
        require(len(value)==int(head[4]) and all(len(row)==31 for row in value),'Incomplete detached V4 profile')
        return value
    original=rows(baseline)
    claimed={row[i] for row in original for i in (0,19,20,21,22,23,25,28)}
    keys=[(name,code) for name,code in [('j',36),('k',37),('u',22),('y',21),('b',48),('n',49),('v',47),('c',46),('x',45)] if code not in claimed]
    require(len(keys)>=2 and len(original)<64,'Detached fixture needs two unused keys and one control slot')
    (key_a,code_a),(key_b,code_b)=keys[:2]
    geometry,frame,_=c['editor_open']('detached-open');ox,oy,w,h=geometry
    owner_marker=marker('detached-open',True)
    canvas_click('detached-explicit-detach',geometry,frame,(w-41,86))
    before=wait_panel('detached-initial',True)
    marker('detached-initial',True,owner_marker)
    canvas=(c['scene_bounds'](scene('detached-android-before'),runtime) if nested
            else x_geometry(c['editor_window']()[0]))
    width,height=before[2]-before[0],before[3]-before[1]
    require((width,height)==(300,737),'Unexpected initial detached panel size')
    target=destination(canvas,width,height);helper=None;helper_log=None
    try:
        if not nested:
            source=output/'scoped-panel-move.c';binary=output/'scoped-panel-move'
            source.write_text(MOVE_FIXTURE);run(['cc',str(source),'-lX11','-o',str(binary)])
            c['v3_guard']();helper_log=(output/'detached-scoped-ewmh-fixture.log').open('w')
            helper=subprocess.Popen(c['input_prefix']+c['input_env']+[str(binary),panel_window,identity],
                                    stdout=helper_log,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+5
            while 'ready scoped-ewmh' not in (output/'detached-scoped-ewmh-fixture.log').read_text():
                require(helper.poll() is None and time.monotonic()<deadline,'Scoped move fixture failed to initialize');time.sleep(.05)
        quiet('detached-header-drag',pointer(before,(100,16))+['mousedown','1','sleep','.2']+
              pointer((target[0],target[1],0,0),(100,16))+['sleep','.4','mouseup','1'])
        if helper:
            require(helper.wait(timeout=5)==0,'Production native header drag did not reach scoped EWMH fixture')
    finally:
        if helper and helper.poll() is None:helper.terminate();helper.wait(timeout=5)
        if helper_log:helper_log.close()
    after=panel('detached-moved')
    require(abs(after[0]-before[0])+abs(after[1]-before[1])>30,'Header drag did not move native panel')
    require(outside(after,canvas),'Detached panel still overlaps Android')
    canvas_after=(c['scene_bounds'](scene('detached-android-after'),runtime) if nested
                  else x_geometry(c['editor_window']()[0]))
    require(canvas_after==canvas,'Moving detached panel moved/resized Android')
    (output/'detached-geometry.json').write_text(json.dumps(dict(app_id=identity,before=before,after=after,android=canvas,
        movement='Weston compositor' if nested else 'private scoped EWMH WM fixture'),indent=2))
    run(c['input_prefix']+c['input_env']+['import','-window',c['editor_window']()[0] if nested else 'root',str(output/'detached-outside-android.png')])
    # Tools and palette are in the detached window; placement/drag stay on canvas.
    panel_click('detached-tools',(53,60));panel_click('detached-basic',(154,144))
    point,destination_point=canvas_points(original,w,h)
    canvas_click('detached-canvas-place',geometry,frame,point)
    quiet('detached-first-binding',['key',key_a])
    quiet('detached-canvas-marker-drag',c['v3_move'](frame[0]+ox+point[0],frame[1]+oy+point[1])+['mousedown','1']+
          c['v3_move'](frame[0]+ox+destination_point[0],frame[1]+oy+destination_point[1])+['sleep','.15','mouseup','1'])
    panel_click('detached-binding-field',(154,144));quiet('detached-rebind',['key',key_b])
    # Undo is witnessed through the prior key, Redo through the replacement key.
    panel_click('detached-undo',(119,60))
    def focus_canvas(label):
        if nested:
            rect=c['scene_bounds'](scene(label),runtime)
            quiet(label,pointer((0,0,0,0),visible_canvas_header(rect))+['click','1'])
        else:quiet(label,wm_focus(c['editor_window']()[0]))
    def trial(label,key):
        panel_click(label+'-try',(251,60))
        quiet(label+'-panel-keyboard-suppressed',['key',key])
        focus_canvas(label+'-focus')
        trial_rows=stage(label+'-down',['keydown',key],lambda r:c['action_count'](r,'DOWN')==1)
        trial_rows+=stage(label+'-up',['keyup',key],lambda r:c['gesture_complete'](trial_rows+r))
        c['v3_finish'](label,trial_rows,downs=1,peak=1)
        quiet(label+'-return',['key','Escape']);bytes_unchanged(label,baseline)
    trial('detached-undo-try',key_a)
    panel_click('detached-redo',(185,60));trial('detached-redo-try',key_b)
    panel_click('detached-duration-field',(154,200))
    quiet('detached-duration-number',['key','3','7','5','Return'])
    bytes_unchanged('detached-before-save',baseline)
    panel_click('detached-save',(185,90))
    deadline=time.monotonic()+5
    while c['editor_bytes']()==baseline:
        require(time.monotonic()<deadline,'Detached Save did not publish draft');time.sleep(.1)
    saved=c['editor_bytes']();authored=rows(saved);wait_panel('detached-saved-panel',False)
    marker('detached-saved',False)
    require(authored[:-1]==original and authored[-1][0]==code_b and authored[-1][7]==375,
            'Detached binding/numeric Save changed unrelated rows or lost authored values')
    require(abs(authored[-1][1]-round(destination_point[0]*65535/(w-1)))<=2 and
            abs(authored[-1][2]-round(destination_point[1]*65535/(h-1)))<=2,'Detached canvas marker drag was not persisted')
    (output/'detached-saved-profile.txt').write_text(saved)
    focus_canvas('detached-saved-focus')
    # Keep the physical key down until the authored timer ends the finger.
    # Releasing it at the first DOWN would shorten the 375 ms tap.
    playback=stage('detached-saved-down',['keydown',key_b],
                   lambda r:c['action_count'](r,'DOWN')==1 and c['gesture_complete'](r))
    playback+=quiet('detached-saved-up',['keyup',key_b])
    c['v3_finish']('detached-saved-playback',playback,downs=1,peak=1)
    event_times=[int(re.search(r' eventMs=(\d+)',row)[1]) for row in observed(playback)
                 if ' ACTION_DOWN ' in row or ' ACTION_UP ' in row]
    require(len(event_times)==2 and 325<=event_times[1]-event_times[0]<=900,
            'Detached numeric duration did not reach real Android tap timing')
    # Closing the separate window docks the same unsaved draft, then Cancel
    # must discard it without changing even one persisted byte.
    geometry,frame,_=c['editor_open']('detached-close-open')
    owner_marker=marker('detached-close-open',True)
    canvas_click('detached-close-detach',geometry,frame,(geometry[2]-41,86));wait_panel('detached-close-mapped',True)
    panel_click('detached-close-tools',(53,60));panel_click('detached-close-basic',(154,144))
    cancel_point,_=canvas_points(authored,w,h)
    canvas_click('detached-close-dirty',geometry,frame,cancel_point)
    panel_click('detached-close-header',(282,16));wait_panel('detached-close-docked',False)
    marker('detached-close-docked',True,owner_marker)
    bytes_unchanged('detached-close-dock',saved)
    canvas_click('detached-close-cancel',geometry,frame,c['editor_targets'](*geometry[2:])['cancel'])
    bytes_unchanged('detached-close-cancel',saved)
    marker('detached-close-cancel',False)
    # Destroy the actual Android task with a live panel. A fresh editor must
    # acquire its marker again; a stale lock/panel cannot satisfy this oracle.
    geometry,frame,_=c['editor_open']('detached-destroy-open')
    canvas_click('detached-destroy-detach',geometry,frame,(geometry[2]-41,86));wait_panel('detached-destroy-mapped',True)
    c['request']({'op':'app_action','action':'force_stop','package':package})
    wait_panel('detached-destroy-unmapped',False);bytes_unchanged('detached-task-destroy',saved)
    marker('detached-destroy-unmapped',False)
    c['request']({'op':'app_action','action':'launch','package':package});c['guest_ready']('detached-relaunch')
    geometry,frame,_=c['editor_open']('detached-reopen')
    canvas_click('detached-reopen-detach',geometry,frame,(geometry[2]-41,86));wait_panel('detached-reopen-mapped',True)
    panel_click('detached-final-cancel',(251,90));wait_panel('detached-final-unmapped',False)
    marker('detached-final-cancel',False)
    bytes_unchanged('detached-final-cancel',saved)
    c['v3_reload'](original,'detached-restore-fixture');bytes_unchanged('detached-restored',baseline)
    c['results'].append(dict(scenario='detached-native-editor',passed=True,app_id=identity,
        panel_outside_android=True,canvas_unchanged=True,shared_undo_redo_try=True,numeric_duration=375,
        save_cancel_exact=True,close_docks=True,task_destroy_reopen=True,
        move_backend='Weston compositor' if nested else 'private scoped EWMH WM fixture'))
