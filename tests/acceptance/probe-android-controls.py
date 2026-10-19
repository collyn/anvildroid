#!/usr/bin/env python3
"""Private desktop input → production bridge/scrcpy → Android MotionEvent acceptance.

sudo python3 tests/acceptance/probe-android-controls.py ROOT/acceptance.json HOST_WORKER_PID OUTPUT_DIR

Installs the observe-only APK and a test profile ONLY in the disposable guest,
relaunches that app, sends physical XTest input to its private Xvfb window,
and asserts actual Android event sequences. Requires live-android.py --x11
or --wayland-x11 (native Wayland guest with private Weston X11 transport).
Does not start/stop the runtime, modify compatibility settings, or use fixtures.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

REPO = Path(__file__).resolve().parents[2]
PACKAGE = 'org.anvildroid.touchprobe'

def require(value, reason):
    if not value: raise RuntimeError(reason)

def validate_nested_transport(m):
    transport=m.get('input_transport',{})
    require(transport.get('kind')=='nested-weston-x11','Unexpected nested transport')
    require(transport.get('x11_display')==':77','Require private X11 transport')
    require(type(m.get('owner_uid')) is int and m['owner_uid']>0,'Invalid compositor owner')
    require(transport.get('authority')==f'/run/user/{m["owner_uid"]}/nested-authority','Unexpected private authority')
    require(transport.get('wayland_display')=='wayland-anvil-'+m['generation'],'Unexpected Wayland socket')
    require(type(transport.get('compositor_namespace_pid')) is int and transport['compositor_namespace_pid']>1,'Invalid compositor PID')
    require(transport.get('protocols',{}).get('wl_seat'),'Nested compositor has no input seat')
    return transport

def scene_rectangle(scene, runtime_id):
    marker=f'waydroid.anvildroid.{runtime_id}.{PACKAGE}'
    sections=scene.split('\n\tView ')
    matching=[part for part in sections if 'role xdg_toplevel' in part and ' of '+marker+',' in part]
    require(len(matching)==1,'Expected one unambiguous scoped Android Wayland surface')
    require('[view is not mapped!]' not in matching[0],'Android Wayland surface is not mapped')
    rect=re.search(r'position: \((-?\d+), (-?\d+)\) -> \((-?\d+), (-?\d+)\)',matching[0])
    require(rect is not None,'Wayland surface geometry unavailable')
    x,y,right,bottom=map(int,rect.groups())
    require(x>=0 and y>=0 and right-x>330 and bottom-y>480,'Wayland surface too small for probe')
    require(right<=1600 and bottom<=1000,'Wayland surface outside private output')
    return x,y

def touch_events(lines,allow_framework_cancel=False):
    """Require complete balanced Android gestures, not merely a final zero."""
    require(not any('WARNING' in row for row in lines),'Observer detected stale contacts')
    events=[row for row in lines if 'AnvilTouchProbe:' in row and ' ACTION_' in row]
    active=0
    for row in events:
        # Activity recreation can make Android dispatch its own device-0,
        # zero-coordinate CANCEL with UNKNOWN tool type. Only explicit
        # lifecycle scenarios may accept that terminal; all injected
        # DOWN/MOVE/UP events still require real touchscreen fingers.
        framework_cancel=(allow_framework_cancel and ' ACTION_CANCEL ' in row and
                          ' device=0 ' in row and ' raw=(0.0,0.0) ' in row and
                          re.findall(r'tool=([^ ]+)',row)==['UNKNOWN(0)'])
        require(re.search(r' source=0x1002(?:$|\s)',row) is not None and
                re.findall(r'tool=([^ ]+)',row) and
                (all(tool=='FINGER(1)' for tool in re.findall(r'tool=([^ ]+)',row)) or framework_cancel),
                'Not actual finger touch delivery')
        match=re.search(r' ACTION_(DOWN|UP|MOVE|CANCEL|POINTER_DOWN|POINTER_UP)(?:\(\d+\))? ',row)
        count=re.search(r' activeAfter=(\d+)(?:$|\s)',row)
        require(match is not None and count is not None,'Malformed Android touch event')
        action=match[1];after=int(count[1])
        if action=='DOWN':require(active==0 and after==1,'Unbalanced DOWN')
        elif action=='POINTER_DOWN':require(active>0 and after==active+1,'Unbalanced POINTER_DOWN')
        elif action=='POINTER_UP':require(active>1 and after==active-1,'Unbalanced POINTER_UP')
        elif action=='MOVE':require(active>0 and after==active,'Unbalanced MOVE')
        elif action=='UP':require(active==1 and after==0,'Unbalanced UP')
        else:require(active>0 and after==0,'Unbalanced CANCEL')
        active=after
    require(events and active==0,'Android contact remained held or no touch delivered')
    return events

def require_no_mapped_contact(lines,reason):
    require(not any(('AnvilKeymap:' in row and 'touch package=' in row) or
                    ('AnvilTouchProbe:' in row and ' ACTION_' in row and 'source=0x1002' in row)
                    for row in lines),reason)

def gesture_complete(lines,allow_framework_cancel=False):
    """A tap may finish before key-up; holds finish after it."""
    events=[row for row in lines if 'AnvilTouchProbe:' in row and ' ACTION_' in row]
    if not events or re.search(r' activeAfter=0(?:$|\s)',events[-1]) is None:return False
    touch_events(lines,allow_framework_cancel)
    return True

def v3_point(**changes):
    fields=dict(key=0,nx=32000,ny=32000,hold=1,kind=0,ex=32000,ey=32000,duration=120,repeats=1,
                source=0,end_mode=0,radius=0,ramp_ms=0,look_width=0,look_height=0,
                sensitivity_x=0,sensitivity_y=0,invert_y=0,activation=0)
    require(not set(changes)-set(fields),'Unknown V3 field')
    fields.update(changes)
    return tuple(fields.values())

def v3_profile(runtime,rows):
    require(rows and all(len(row)==19 and all(type(value) is int for value in row) for row in rows),
            'V3 requires nineteen integer fields per control')
    return f'ANVIL_KEYMAP 3 {runtime} {PACKAGE} {len(rows)}\n'+''.join(' '.join(map(str,row))+'\n' for row in rows)

def finger_samples(lines):
    samples=[]
    for row in lines:
        if 'AnvilTouchProbe:' not in row or ' ACTION_' not in row:continue
        action=re.search(r' ACTION_([A-Z_]+)',row)
        for point in re.finditer(r' id=(\d+)\((-?[\d.]+),(-?[\d.]+)\)',row):
            samples.append((action[1],int(point[1]),float(point[2]),float(point[3])))
    return samples

def assert_look_rebase(lines,center_x,center_y):
    samples=finger_samples(lines)
    actions=[sample[0] for sample in samples]
    require(actions.count('UP')==1 and actions.count('DOWN')==1,
            'Look edge requires exactly one lift and restart')
    up=actions.index('UP')
    require(up+1<len(actions) and actions[up+1]=='DOWN',
            'Look edge inserted motion between UP and center DOWN')
    require(all(action=='MOVE' for action in actions[:up]),
            'Look edge emitted unexpected events before lifting')
    require(up+2==len(actions),'Look edge emitted unexpected events after restart')
    require(abs(samples[up+1][2]-center_x)<=2 and abs(samples[up+1][3]-center_y)<=2,
            'Look did not restart at authored center')
    # This scenario crosses the right edge: any pre-lift movement must keep
    # moving right, never drag back to center before lifting.
    require(all(samples[index][2]>=samples[index-1][2]-1 for index in range(1,up+1)),
            'Look edge emitted reverse motion before lifting')

def editor_attachment(lines,runtime):
    # Android app_id can be the bare package; logs come only from the
    # already authenticated disposable runtime's guest logcat. Match either
    # production spelling exactly, never a package suffix or arbitrary app.
    markers=(PACKAGE,'waydroid.anvildroid.'+runtime+'.'+PACKAGE)
    pattern='(?:'+'|'.join(re.escape(marker) for marker in markers)+')'
    matches=[re.search(r' attached '+pattern+r' origin=(-?\d+),(-?\d+) size=(\d+)x(\d+)(?:$|\s)',row)
             for row in lines if 'AnvilKeymapOverlay:' in row]
    matches=[match for match in matches if match]
    require(matches,'Scoped editor attachment geometry unavailable')
    geometry=tuple(map(int,matches[-1].groups()))
    require(geometry[0]>=0 and geometry[1]>=0 and geometry[2]>=330 and geometry[3]>=420,
            'Unsupported editor geometry')
    return geometry

def editor_frame(geometry,host_w,host_h,nested):
    # Root Wayland views describe raw content; the native X window also
    # includes the known 5px resize margins and 33px title bar. Fitted editor
    # bounds are centered inside that raw surface, never used to infer chrome.
    frame=(0,0) if nested else (5,33)
    raw_w,raw_h=(host_w,host_h) if nested else (host_w-10,host_h-38)
    ox,oy,w,h=geometry
    require(raw_w>=330 and raw_h>=420 and w>0 and h>0 and ox>=0 and oy>=0 and
            ox+w<=raw_w and oy+h<=raw_h,'Editor rectangle outside raw content surface')
    require(abs(2*ox+w-raw_w)<=1 and abs(2*oy+h-raw_h)<=1,
            'Editor rectangle is not centered inside raw content surface')
    require((ox<=1 and raw_w-w<=1) or (oy<=1 and raw_h-h<=1),
            'Editor rectangle does not cover a raw content axis')
    return frame

def editor_targets(w,h):
    require(w>=330 and h>=420,'Unsupported editor layout')
    compact=w<600 or h<420
    if compact:
        unit=(w-28)//5
        targets={name:(14+unit*column+(unit-4)//2,h-30)
                 for name,column in [('add',0),('tap',1),('remove',2),('save',3),('cancel',4)]}
    else:
        targets=dict(add=(w-132,148),tap=(w-186,231),remove=(w-132,283),save=(w-132,h-48),cancel=(w-30,30))
    columns=2 if not compact and h>=540 else 4
    row_height=32 if columns==2 else 24
    px,py,pw=(12,12,w-88) if compact else (w-236,304,208)
    targets['bind_key']=(px+(4%columns)*pw//columns+(pw//columns-2)//2,
                         py+(4//columns)*row_height+(row_height-2)//2)
    return targets

def require_capture_protocols(protocols):
    require(all(type(protocols.get(name)) is int and protocols[name]>=1 for name in
                ('zwp_relative_pointer_manager_v1','zwp_pointer_constraints_v1')),
            'Actual private compositor lacks relative-pointer/lock capability; FPS capture is unavailable')

def require_relative_direction(lines,origin_x,origin_y):
    samples=[sample for sample in finger_samples(lines) if sample[0]=='MOVE']
    require(samples and any(sample[2]>origin_x+1 for sample in samples) and
            all(abs(sample[3]-origin_y)<=2 for sample in samples),
            'Captured relative motion did not produce positive horizontal Android displacement')

def capture_protocol_witness(trace,surface_id):
    """Correlate one compositor grant to its client, root surface and pointer."""
    rows=trace.splitlines()
    lock=None
    for index,row in enumerate(rows):
        client=re.search(r'client\s+(0x[0-9a-fA-F]+)',row)
        if not client or '.lock_pointer(' not in row:continue
        surfaces=re.findall(r'wl_surface[@#](\d+)',row)
        if not surfaces:continue
        requested_surface=int(surfaces[0]);parents={}
        for earlier in rows[:index]:
            identity=re.search(r'client\s+(0x[0-9a-fA-F]+)',earlier)
            if not identity or identity[1]!=client[1]:continue
            if '.get_subsurface(' in earlier:
                linked=re.findall(r'wl_surface[@#](\d+)',earlier)
                if len(linked)==2:parents[int(linked[0])]=int(linked[1])
            destroyed=re.search(r'wl_surface[@#](\d+)\.destroy\(',earlier)
            if destroyed:parents.pop(int(destroyed[1]),None)
        ancestry=[requested_surface]
        while ancestry[-1]!=surface_id and ancestry[-1] in parents and len(ancestry)<128:
            parent=parents[ancestry[-1]]
            if parent in ancestry:break
            ancestry.append(parent)
        if ancestry[-1]!=surface_id:continue
        locked=re.search(r'zwp_locked_pointer_v1[@#](\d+)',row)
        pointer=re.search(r'wl_pointer[@#](\d+)',row)
        if locked and pointer:
            lock=(index,client[1],int(locked[1]),int(pointer[1]),ancestry);break
    require(lock is not None,'No client/root-scoped lock_pointer request in compositor trace')
    request,client,locked,pointer,ancestry=lock
    belongs=lambda row: re.search(r'client\s+'+re.escape(client)+r'\b',row) is not None
    object_event=lambda row,kind,number,event: re.search(re.escape(kind)+r'[@#]'+str(number)+r'\.'+event+r'\(',row) is not None
    grants=[index for index in range(request+1,len(rows)) if belongs(rows[index]) and
            object_event(rows[index],'zwp_locked_pointer_v1',locked,'locked')]
    require(grants,'Compositor never granted the scoped pointer lock')
    granted=grants[0]
    endings=[index for index in range(granted+1,len(rows)) if belongs(rows[index]) and
             (object_event(rows[index],'zwp_locked_pointer_v1',locked,'destroy') or
              object_event(rows[index],'zwp_locked_pointer_v1',locked,'unlocked'))]
    require(endings,'Compositor trace lacks scoped lock release')
    ended=endings[0]
    relative_ids=[]
    for row in rows[:granted]:
        if belongs(row) and '.get_relative_pointer(' in row and re.search(r'wl_pointer[@#]'+str(pointer)+r'\b',row):
            match=re.search(r'zwp_relative_pointer_v1[@#](\d+)',row)
            if match:relative_ids.append(int(match[1]))
    require(relative_ids,'No relative object associated with the locked client pointer')
    locked_rows=[row for row in rows[granted+1:ended] if belongs(row)]
    require(any(object_event(row,'zwp_relative_pointer_v1',number,'relative_motion')
                for row in locked_rows for number in relative_ids),'No scoped relative motion during lock')
    require(not any(object_event(row,'wl_pointer',pointer,'motion') for row in locked_rows),
            'Client received absolute wl_pointer.motion while locked')
    require(any(belongs(row) and object_event(row,'wl_pointer',pointer,'motion') for row in rows[ended+1:]),
            'Client absolute pointer motion did not resume after releasing lock')
    return {'client':client,'surface_id':surface_id,'locked_surface_ancestry':ancestry,'pointer_id':pointer,'lock_id':locked,
            'grant_observed':True,'relative_observed':True,'absolute_motion_suppressed':True,
            'absolute_motion_restored':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata',type=Path);parser.add_argument('worker_pid',type=int);parser.add_argument('output',type=Path)
    parser.add_argument('--keymap-v3',action='store_true',help='Run V3 finger/joystick/swipe/look acceptance after the legacy thirteen cases')
    parser.add_argument('--editor-v3',action='store_true',help='With --keymap-v3, click the real editor Add/Bind/Save and edit/Cancel controls')
    parser.add_argument('--editor-lifecycle',action='store_true',help='With --editor-v3, verify drag/delete/conflicts, marker pixels and live geometry/task lifecycle')
    parser.add_argument('--capture-v3',action='store_true',help='With --keymap-v3, require actual FPS lock/relative motion and lifecycle acceptance')
    args=parser.parse_args();require(not args.capture_v3 or args.keymap_v3,'--capture-v3 requires --keymap-v3');require(not args.editor_v3 or args.keymap_v3,'--editor-v3 requires --keymap-v3');require(not args.editor_lifecycle or args.editor_v3,'--editor-lifecycle requires --editor-v3');require(os.geteuid()==0,'Root required')
    root=args.metadata.parent
    require(root.parent==Path('/tmp') and root.name.startswith('anvil-live-') and root.resolve()==root and root.stat().st_uid==0 and not root.stat().st_mode & 0o077,'Unsafe disposable root')
    require(args.metadata.name=='acceptance.json' and not args.metadata.is_symlink(),'Unexpected metadata')
    m=json.loads(args.metadata.read_text());instance=Path(m['instance'])
    require(instance.parent==args.metadata.parent/'instances' and instance.resolve()==instance,'Unexpected disposable path')
    nested=m.get('input_transport',{}).get('kind')=='nested-weston-x11'
    transport=validate_nested_transport(m) if nested else None
    require(nested or m.get('x11_display')==':77','Require private acceptance display')
    cmdline=Path(f'/proc/{args.worker_pid}/cmdline').read_bytes().split(b'\0')
    require(str(instance).encode() in cmdline and m['generation'].encode() in cmdline and any(v.endswith(b'/runtime-worker.py') for v in cmdline),'Worker identity mismatch')
    def process_start():return Path(f'/proc/{args.worker_pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    identity_start=process_start();identity_mnt=os.readlink(f'/proc/{args.worker_pid}/ns/mnt')
    def verify_worker():
        require(process_start()==identity_start and os.readlink(f'/proc/{args.worker_pid}/ns/mnt')==identity_mnt and Path(f'/proc/{args.worker_pid}/cmdline').read_bytes().split(b'\0')==cmdline,'Worker changed during acceptance')
    require(os.readlink(f'/proc/{args.worker_pid}/ns/mnt')!=os.readlink('/proc/self/ns/mnt'),'Worker needs private mounts')
    compositor_identity=None
    if nested:
        require(os.readlink(f'/proc/{args.worker_pid}/ns/pid')==transport['compositor_pid_namespace'],'Worker/compositor PID namespace mismatch')
        require(transport['compositor_mount_namespace']!=os.readlink('/proc/self/ns/mnt'),'Compositor needs private mounts')
        matches=[]
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal():continue
            try:
                command=(path/'cmdline').read_bytes().split(b'\0')
                if not command or Path(os.fsdecode(command[0])).name!='weston' or ('--socket='+transport['wayland_display']).encode() not in command:continue
                if path.stat().st_uid!=m['owner_uid'] or os.readlink(path/'ns/mnt')!=transport['compositor_mount_namespace'] or os.readlink(path/'ns/pid')!=transport['compositor_pid_namespace']:continue
                matches.append(path)
            except (OSError,ValueError):continue
        require(len(matches)==1,'Private compositor identity ambiguous or unavailable')
        compositor=matches[0]
        def compositor_state():
            return ((compositor/'stat').read_text().rsplit(')',1)[1].split()[19],os.readlink(compositor/'ns/mnt'),os.readlink(compositor/'ns/pid'),(compositor/'cmdline').read_bytes())
        compositor_identity=compositor_state()
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    prefix=['nsenter','-t',str(args.worker_pid),'-m','-n','-p','--']
    guest=prefix+['lxc-attach','-P',str(instance/'android'),'-n',m['id']]
    def run(command,**kwargs):
        timeout=kwargs.pop('timeout',45)
        child=subprocess.Popen(command,stdin=subprocess.PIPE if 'input' in kwargs else subprocess.DEVNULL,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:stdout,stderr=child.communicate(kwargs.pop('input',None),timeout=timeout)
        except BaseException:
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                child.communicate(timeout=2)
            raise
        if child.returncode:raise subprocess.CalledProcessError(child.returncode,command,stdout,stderr)
        return stdout.decode()

    def module(name):
        spec=importlib.util.spec_from_file_location(name.replace('-','_'),REPO/'services'/(name+'.py'))
        value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
    backend=module('runtime-android').AndroidBackend(module('runtime-images').ImageBackend(root,root/'source'))
    observed=backend.status(m['id'])
    require(observed.get('generation')==m['generation'],'Disposable generation changed')
    def request(action,fd=None):
        payload=action if action.get('op') in ('status','resume') else dict(action,generation=m['generation'])
        value=backend.status(m['id'],action['op'],payload,apk_fd=fd)
        require(not value.get('app_error'),str(value.get('app_error')))
        require(value.get('generation')==m['generation'],'Runtime generation changed')
        return value
    status=request({'op':'status'})
    if status['state']=='Frozen':status=request({'op':'resume'})
    require(status['state']=='Running','Actual Android must be Running')
    # The longer lifecycle matrix recreates Activities and rotates the test
    # display. Bound private log storage while retaining the strict prefix
    # oracle instead of silently accepting rotated evidence buffers.
    run(guest+['--','/system/bin/logcat','-G','16M'])
    if args.editor_lifecycle:
        run(guest+['--','/system/bin/wm','user-rotation','lock','0'])
        run(guest+['--','/system/bin/wm','density','reset'])
    apk=os.open(REPO/'target/touch-probe/AnvilDroidTouchProbe.apk',os.O_RDONLY)
    try:request({'op':'install'},apk)
    finally:os.close(apk)
    profile=(f'ANVIL_KEYMAP 2 {m["id"]} {PACKAGE} 7\n'
             '17 16000 40000 1 1 28000 40000 120 1\n'
             '19 10000 20000 1 2 50000 20000 300 1\n'
             '20 30000 30000 1 3 30000 30000 100 1\n'
             '34 40000 40000 1 4 50000 50000 120 1\n'
             '35 16000 16000 0 5 48000 48000 100 2\n'
             '36 32000 32000 1 6 48000 48000 120 1\n'
             '48 20000 45000 1 0 0 0 0 0\n')
    run(guest+['--uid','1000','--gid','1000','--','/system/bin/sh','-c',
               'mkdir -p /data/waydroid_tmp/anvildroid/keymaps && cat > /data/waydroid_tmp/anvildroid/keymaps/org.anvildroid.touchprobe'],input=profile.encode())
    for action in ['force_stop','launch']:request({'op':'app_action','action':action,'package':PACKAGE})
    time.sleep(1)
    input_prefix=['nsenter','-t',compositor.name,'-m','-n','-p','--'] if nested else prefix
    input_env=['env','DISPLAY=:77','XAUTHORITY='+transport['authority']] if nested else ['env','DISPLAY=:77','XAUTHORITY=/run/anvil-x11-authority']
    x=input_prefix+input_env+['xdotool']
    # A failed opt-in run may leave XTest keys held. Release only the probe's
    # known inputs on this authenticated private display before measuring.
    run(x+['keyup','w','a','s','d','b','r','t','g','h','j','l','z','x','c','v','n','Escape','F2','F3','F4',
           'mouseup','1','mouseup','3'])
    def mapped_windows():
        deadline=time.monotonic()+10
        previous=None
        while True:
            try:found=run(x+(['search','--onlyvisible','--class','weston'] if nested else ['search','--onlyvisible','--class',r'\.org\.anvildroid\.touchprobe$'])).splitlines()
            except subprocess.CalledProcessError:found=[]
            require(len(found)<=1,'Ambiguous scoped observer windows')
            if len(found)==1 and found==previous:return found
            require(time.monotonic()<deadline,'Scoped observer did not map within ten seconds')
            previous=found;time.sleep(.3)
    windows=mapped_windows()
    require(len(windows)==1,'Expected one unambiguous observer window');window=windows[0]
    if nested:require(window==transport['x11_window'],'Private Weston X11 output changed')
    offset=(0,0)
    scene_elapsed_ms=None
    if nested:
        started=time.monotonic()
        scene=run(input_prefix+['env','XDG_RUNTIME_DIR=/run/user/'+str(m['owner_uid']),'WAYLAND_DISPLAY='+transport['wayland_display'],'weston-debug','scene-graph'],timeout=5)
        scene_elapsed_ms=round((time.monotonic()-started)*1000)
        (args.output/'wayland-scene-graph.txt').write_text(scene)
        offset=scene_rectangle(scene,m['id'])
    run(x+['windowfocus','--sync',window,'mousemove','--window',window,str(220+offset[0]),str(320+offset[1])])
    def logs():return run(guest+['--','/system/bin/logcat','-d','-s','AnvilTouchProbe:I','AnvilKeymap:I','AnvilKeymapOverlay:I','*:S']).splitlines()
    def guest_ready(label):
        nonlocal offset
        deadline=time.monotonic()+10
        collapsed=False
        while True:
            try:
                pid=run(guest+['--','/system/bin/pidof',PACKAGE]).strip()
                require(pid.isdecimal(),'Observer PID ambiguous')
                rows=run(guest+['--','/system/bin/logcat','-d','--pid='+pid,'-s','AnvilTouchProbe:I','*:S']).splitlines()
                (args.output/(label+'-ready.log')).write_text('\n'.join(rows)+'\n')
                focus=[row for row in rows if ' FOCUS=' in row]
                if any(' SIZE contentPx=' in row for row in rows) and focus and ' FOCUS=true active=0' in focus[-1]:return
                if not collapsed:
                    # Fresh stock guests can leave NotificationShade focused
                    # after setup/configuration. Establish the diagnostic app's
                    # foreground state before measuring, only in this guest.
                    run(guest+['--','/system/bin/cmd','statusbar','collapse'])
                    collapsed=True;time.sleep(.15);continue
                if nested:
                    scene=run(input_prefix+['env','XDG_RUNTIME_DIR=/run/user/'+str(m['owner_uid']),
                              'WAYLAND_DISPLAY='+transport['wayland_display'],'weston-debug','scene-graph'],timeout=5)
                    offset=scene_rectangle(scene,m['id'])
                    run(x+['mousemove','--window',window,str(offset[0]+220),str(offset[1]+320),'sleep','.15','click','1'])
            except subprocess.CalledProcessError:pass
            require(time.monotonic()<deadline,'Actual observer did not become focused and ready')
            time.sleep(.1)
    guest_ready('initial-observer')
    tree=run(input_prefix+input_env+['xwininfo','-root'])
    root_window=re.search(r'Window id: (0x[0-9a-fA-F]+)',tree)
    require(root_window is not None,'Private X11 root unavailable')
    scenarios=[
        ('basic',['keydown','b','sleep','.2','keyup','b'],1,False,False),
        ('wasd',['keydown','w','keydown','d','sleep','.2','keyup','d','keyup','w'],1,True,False),
        ('swipe',['keydown','r','sleep','.4','keyup','r'],1,True,False),
        ('repeat',['keydown','t','sleep','.45','keyup','t'],2,False,False),
        ('skill',['keydown','g','mousemove','--window',window,'300','450','sleep','.15','keyup','g'],1,True,False),
        ('macro',['keydown','h','sleep','.12','keydown','h','sleep','.7','keyup','h'],2,False,False),
        ('aim-fire',['keydown','j','mousemove','--window',window,'330','480','mousedown','1','sleep','.15','mouseup','1','keyup','j'],1,True,True),
        ('simultaneous',['keydown','w','keydown','b','sleep','.15','keyup','b','keyup','w'],1,False,True),
        ('blur',['keydown','w','windowfocus',root_window[1],'sleep','.15','keyup','w'],1,False,False),
        ('held-refocus',['windowfocus','--sync',window,'keydown','w','windowfocus',root_window[1],'sleep','.1','windowfocus','--sync',window,'keydown','w','sleep','.15','keyup','w','sleep','.1','keydown','w','sleep','.15','keyup','w'],2,False,False),
    ]
    results=[]
    for name,sequence,minimum,move,multi in scenarios:
        if nested:
            require(compositor_state()==compositor_identity,'Private compositor changed during acceptance')
            sequence=sequence.copy()
            for index,value in enumerate(sequence):
                if value=='mousemove':
                    sequence[index+3]=str(int(sequence[index+3])+offset[0]);sequence[index+4]=str(int(sequence[index+4])+offset[1])
        verify_worker()
        if name in ('blur','held-refocus'):
            # X focus requests return before Android has consumed queued input.
            # Observe the first contact before moving focus, then independently
            # witness its cancellation and the fresh press after key release.
            activate=['windowfocus','--sync',window]
            if nested:
                activate+=['mousemove','--window',window,
                           str(220+offset[0]),str(320+offset[1]),'click','1']
            run(x+activate);time.sleep(.2)
            # Independent cases begin released. A previous root-window blur
            # can hide the physical UP from the nested X11 compositor. Drain
            # that stale key only after restoring real client focus; the
            # intentional held-through-refocus stage below remains unchanged.
            setup_before=logs();run(x+['keyup','w']);time.sleep(.2);setup_after=logs()
            (args.output/(name+'-setup-all.log')).write_text('\n'.join(setup_after)+'\n')
            require(setup_after[:len(setup_before)]==setup_before,'Android log buffer rotated during focus setup')
            setup=setup_after[len(setup_before):]
            (args.output/(name+'-setup.log')).write_text('\n'.join(setup)+'\n')
            require_no_mapped_contact(setup,name+': setup release unexpectedly delivered mapped contact')
            before=setup_after;checkpoint=before;collected=[]
            def capture(label):
                nonlocal checkpoint
                current=logs()
                # Preserve raw evidence even if the prefix/rotation check fails.
                (args.output/(name+'-'+label+'-all.log')).write_text('\n'.join(current)+'\n')
                require(current[:len(before)]==before,'Android log buffer rotated')
                (args.output/(name+'-all.log')).write_text('\n'.join(current[len(before):])+'\n')
                require(current[:len(checkpoint)]==checkpoint,'Android log buffer rotated')
                delta=current[len(checkpoint):]
                (args.output/(name+'-'+label+'.log')).write_text('\n'.join(delta)+'\n')
                return current,delta
            def wait_stage(label,predicate):
                deadline=time.monotonic()+5
                while True:
                    current,delta=capture(label)
                    if predicate(delta):
                        # Advance only after a witnessed acknowledgment.
                        return current,delta
                    require(time.monotonic()<deadline,name+': timed out waiting for '+label)
                    time.sleep(.05)
            def has_touch(rows,action):
                return any('AnvilTouchProbe:' in row and ' ACTION_'+action+' ' in row for row in rows)
            run(x+['keydown','w'])
            checkpoint,first=wait_stage('initial-down',lambda rows:has_touch(rows,'DOWN'))
            require(not has_touch(first,'UP') and not has_touch(first,'CANCEL'),
                    name+': initial gesture ended before blur')
            collected+=first
            run(x+['windowfocus','--sync',root_window[1]])
            checkpoint,cancelled=wait_stage('blur-terminal',lambda rows:has_touch(rows,'UP') or has_touch(rows,'CANCEL'))
            collected+=cancelled;touch_events(collected)
            if name=='held-refocus':
                run(x+activate);time.sleep(.25)
                current,activation=capture('refocus-activation')
                require(not any('AnvilKeymap:' in row and 'touch package=' in row for row in activation),
                        'Refocus activation unexpectedly restarted mapped contact')
                clicks=[row for row in activation if 'AnvilTouchProbe:' in row and ' ACTION_' in row]
                if nested:
                    require(len(clicks)==2 and ' ACTION_DOWN ' in clicks[0] and
                            ' ACTION_UP ' in clicks[-1] and 'activeAfter=0' in clicks[-1] and
                            all('source=0x2002' in row and 'tool=MOUSE(3)' in row for row in clicks),
                            'Refocus activation was not one ordinary mouse click')
                else:require(not clicks,'Native refocus unexpectedly delivered touch')
                checkpoint=current
                run(x+['keydown','w']);time.sleep(.25)
                checkpoint,held=capture('held-suppressed')
                require(not any('AnvilTouchProbe:' in row and ' ACTION_' in row for row in held),
                        'Held key resumed before release')
                collected+=held
            run(x+['keyup','w']);time.sleep(.2)
            checkpoint,released=capture('release-suppressed')
            require(not any('AnvilTouchProbe:' in row and ' ACTION_' in row for row in released),
                    name+': releasing cancelled key delivered extra touch')
            collected+=released
            if name=='held-refocus':
                run(x+['keydown','w'])
                checkpoint,fresh=wait_stage('fresh-down',lambda rows:has_touch(rows,'DOWN'))
                collected+=fresh
                run(x+['keyup','w'])
                checkpoint,terminal=wait_stage('fresh-terminal',lambda rows:has_touch(rows,'UP') or has_touch(rows,'CANCEL'))
                collected+=terminal
            lines=collected
        else:
            before=logs();run(x+sequence);time.sleep(.2);after=logs()
            require(after[:len(before)]==before,'Android log buffer rotated during acceptance; rerun')
            lines=after[len(before):]
        verify_worker()
        if nested:require(compositor_state()==compositor_identity,'Private compositor changed during acceptance')
        text='\n'.join(lines)+'\n';(args.output/(name+'.log')).write_text(text)
        events=touch_events(lines)
        downs=sum(' ACTION_DOWN ' in v for v in events)
        require(downs>=minimum,name+': missing actual Android DOWN')
        if name=='macro':require(downs==2,'Finite macro restarted or lost a cycle')
        if name=='held-refocus':require(downs==2,'Held key resumed before release or fresh press was lost')
        if move:require(any(' ACTION_MOVE ' in v for v in events),name+': missing MOVE')
        if multi:require(any('ACTION_POINTER_DOWN(1)' in v and 'activeAfter=2' in v for v in events),name+': missing second contact')
        require(events and 'activeAfter=0' in events[-1],name+': Android contact remained held')
        require(all('source=0x1002' in v and 'tool=FINGER(1)' in v for v in events),name+': not actual touch delivery')
        require(not any('WARNING' in v for v in lines),name+': observer detected stale contacts')
        results.append({'scenario':name,'passed':True,'down_count':downs,'event_count':len(events)})
    # Stage lifecycle transitions separately: a final balanced gesture alone
    # cannot prove that playback was suppressed while the editor was active.
    def stage(sequence,label):
        verify_worker()
        if nested:require(compositor_state()==compositor_identity,'Private compositor changed')
        before=logs();run(x+sequence);time.sleep(.35);after=logs()
        (args.output/(label+'-all.log')).write_text('\n'.join(after)+'\n')
        require(after[:len(before)]==before,'Android log buffer rotated')
        delta=after[len(before):]
        (args.output/(label+'.log')).write_text('\n'.join(delta)+'\n')
        return delta
    for name,toggle in [('playback-lifecycle','F3'),('editor-lifecycle','F2')]:
        run(x+['windowfocus','--sync',window])
        lines=stage(['keydown','w','sleep','.15','key',toggle,'sleep','.5'],name+'-cancel')
        # Opening/disable must finish the already held mapped contact.
        touch_events(lines)
        suppressed=stage(['keyup','w','key','b'],name+'-suppressed')
        require(not any('AnvilTouchProbe:' in row and ' ACTION_' in row for row in suppressed),
                name+': mapped touch leaked while suppressed')
        resumed=stage(['key',toggle,'sleep','.5','key','b'],name+'-resumed')
        touch_events(resumed)
        lines+=suppressed+resumed
        events=touch_events(lines)
        downs=sum(' ACTION_DOWN ' in row for row in events)
        require(downs==2,name+': lifecycle produced unexpected gesture count')
        (args.output/(name+'.log')).write_text('\n'.join(lines)+'\n')
        results.append({'scenario':name,'passed':True,'down_count':downs,'event_count':len(events)})
    marker=stage(['keydown','w','sleep','.15','key','F4','sleep','.15',
                  'key','F4','sleep','.15','keyup','w'],'marker-lifecycle')
    events=touch_events(marker)
    require(sum(' ACTION_DOWN ' in row for row in events)==1,
            'Marker visibility interrupted or restarted the mapped gesture')
    (args.output/'marker-lifecycle.log').write_text('\n'.join(marker)+'\n')
    results.append({'scenario':'marker-toggle-continuity','passed':True,'down_count':1,'event_count':len(events),
                    'visibility_observed':False})
    capture_result={'verified':False,'reason':'Capture acceptance was not requested'}
    if args.keymap_v3:
        def v3_guard():
            verify_worker()
            if nested:require(compositor_state()==compositor_identity,'Private compositor changed')
        def observed(rows):
            return [row for row in rows if 'AnvilTouchProbe:' in row and ' ACTION_' in row]
        def action_count(rows,action):
            return sum(' ACTION_'+action+' ' in row for row in observed(rows))
        def v3_stage(label,sequence,predicate=None,settle=.15):
            v3_guard();before=logs()
            if callable(sequence):sequence()
            else:run(x+sequence)
            deadline=time.monotonic()+5
            while True:
                time.sleep(settle);v3_guard();after=logs()
                (args.output/(label+'-all.log')).write_text('\n'.join(after)+'\n')
                require(after[:len(before)]==before,'Android log buffer rotated in '+label)
                rows=after[len(before):]
                (args.output/(label+'.log')).write_text('\n'.join(rows)+'\n')
                if predicate is None or predicate(rows):return rows
                require(time.monotonic()<deadline,label+': Android acknowledgment timed out')
        def v3_move(px,py):
            # Xdotool --sync may wait forever for a move to the current point.
            # XTest request order plus the Android/persistence oracle supplies
            # the acknowledgment, including stationary editor pixel captures.
            return ['mousemove','--window',window,str(px+offset[0]),str(py+offset[1])]
        def v3_reload(rows,label):
            nonlocal window,offset
            v3_guard()
            profile=v3_profile(m['id'],rows)
            (args.output/(label+'-profile.txt')).write_text(profile)
            run(guest+['--uid','1000','--gid','1000','--','/system/bin/sh','-c',
                       'cat > /data/waydroid_tmp/anvildroid/keymaps/org.anvildroid.touchprobe'],input=profile.encode())
            for action in ('force_stop','launch'):request({'op':'app_action','action':action,'package':PACKAGE})
            time.sleep(1)
            windows=mapped_windows()
            require(len(windows)==1,'V3 requires one scoped observer window');window=windows[0]
            if nested:
                require(window==transport['x11_window'],'Private Weston output changed')
                scene=run(input_prefix+['env','XDG_RUNTIME_DIR=/run/user/'+str(m['owner_uid']),
                          'WAYLAND_DISPLAY='+transport['wayland_display'],'weston-debug','scene-graph'],timeout=5)
                (args.output/(label+'-scene.txt')).write_text(scene);offset=scene_rectangle(scene,m['id'])
            # Establish focus before measuring. In pointer mode this activation
            # click can itself be a finger gesture, and is deliberately outside
            # the measured scenarios rather than counted as a mapped success.
            activate=['windowfocus','--sync',window]+v3_move(220,320)
            if nested:activate+=['click','1']
            v3_stage(label+'-activation',activate,settle=.3)
            guest_ready(label)
        def v3_finish(name,rows,downs=None,peak=None,framework_cancel=False):
            (args.output/(name+'.log')).write_text('\n'.join(rows)+'\n')
            events=touch_events(rows,framework_cancel);count=action_count(rows,'DOWN')
            if downs is not None:require(count==downs,name+': wrong gesture count')
            maximum=max(int(re.search(r'activeAfter=(\d+)',row)[1]) for row in events)
            if peak is not None:require(maximum==peak,name+': wrong simultaneous contact count')
            results.append({'scenario':name,'passed':True,'down_count':count,'event_count':len(events),'peak_contacts':maximum})
        pointer=v3_point(key=0,kind=7,source=2)
        v3_reload([pointer],'v3-pointer')
        hover=v3_stage('v3-hover',v3_move(240,340))
        require(not observed(hover),'Hover emitted Android contact')
        results.append({'scenario':'v3-hover','passed':True,'event_count':0})
        click=v3_stage('v3-mouse-click',['mousedown','1'],lambda rows:action_count(rows,'DOWN')==1)
        click+=v3_stage('v3-mouse-click-up',['mouseup','1'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-mouse-click',click,downs=1,peak=1)
        drag=v3_stage('v3-mouse-drag-down',['mousedown','1'],lambda rows:action_count(rows,'DOWN')==1)
        moved=v3_stage('v3-mouse-drag-move',v3_move(280,380),lambda rows:action_count(rows,'MOVE')>0)
        drag+=moved
        drag+=v3_stage('v3-mouse-drag-up',['mouseup','1'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-mouse-drag',drag,downs=1,peak=1)
        require(len({sample[1] for sample in finger_samples(drag)})==1,'Mouse drag replaced its finger')
        drag_samples=finger_samples(drag);origin=drag_samples[0]
        require(any(sample[0]=='MOVE' and sample[2]>origin[2]+1 and sample[3]>origin[3]+1 for sample in drag_samples),
                'Mouse drag did not follow the positive pointer displacement')
        joystick=v3_point(key=17,kind=1,nx=16000,ny=40000,radius=9000,ramp_ms=80)
        swipe=v3_point(key=19,kind=2,nx=10000,ny=20000,ex=26000,ey=20000,duration=200,end_mode=2)
        look=v3_point(key=38,kind=8,nx=44000,ny=32000,look_width=6000,look_height=6000,
                      sensitivity_x=100,sensitivity_y=100)
        fire=v3_point(key=272,kind=0,nx=52000,ny=50000,source=1,end_mode=2)
        v3_reload([pointer,joystick,swipe,look,fire],'v3-controls')
        fixed=v3_stage('v3-fixed-down',['mousedown','1'],lambda rows:action_count(rows,'DOWN')==1)
        fixed+=v3_stage('v3-fixed-held',v3_move(240,340))
        fixed+=v3_stage('v3-fixed-up',['mouseup','1'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-fixed-mouse-routing',fixed,downs=1,peak=1)
        require(not action_count(fixed,'MOVE'),'Fixed fire leaked free pointer drag')
        motion=v3_stage('v3-joystick-start',['keydown','w'],lambda rows:action_count(rows,'MOVE')>0)
        first=finger_samples(motion);require(first and first[0][0]=='DOWN','Joystick did not start DOWN')
        center=first[0][2:];require(any(sample[3]<center[1]-1 for sample in first if sample[0]=='MOVE'),
                                   'Joystick lacks center-first upward MOVE')
        changed=v3_stage('v3-joystick-direction',['keydown','s','keyup','w'],lambda rows:action_count(rows,'MOVE')>0,settle=.2)
        motion+=changed
        require(any(sample[3]>center[1]+1 for sample in finger_samples(changed) if sample[0]=='MOVE'),
                'Joystick direction did not cross its initial center')
        motion+=v3_stage('v3-joystick-up',['keyup','s'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-joystick-center-hold',motion,downs=1,peak=1)
        require(len({sample[1] for sample in finger_samples(motion)})==1,'Joystick replaced its finger')
        held=v3_stage('v3-swipe-start',['keydown','r'],lambda rows:action_count(rows,'DOWN')==1)
        endpoint=v3_stage('v3-swipe-endpoint',['sleep','.4'])
        held+=endpoint
        require(action_count(held,'MOVE')>0,'Held swipe never moved toward its endpoint')
        swipe_samples=finger_samples(held);swipe_origin=swipe_samples[0]
        swipe_last=swipe_samples[-1]
        require(swipe_last[2]>swipe_origin[2]+1 and abs(swipe_last[3]-swipe_origin[3])<=2,
                'Held swipe did not reach its horizontal endpoint direction')
        require(not action_count(held,'UP') and not action_count(held,'CANCEL'),'Held swipe ended at duration')
        held+=v3_stage('v3-swipe-release',['keyup','r'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-swipe-endpoint-hold',held,downs=1,peak=1)
        # Seed motion after activation, then inspect a positive delta. The host
        # cursor is intentionally far from the authored camera center.
        v3_stage('v3-look-position',v3_move(220,320))
        camera=v3_stage('v3-look-down',['keydown','l'],lambda rows:action_count(rows,'DOWN')==1)
        camera+=v3_stage('v3-look-seed',v3_move(221,320))
        delta=v3_stage('v3-look-delta',v3_move(241,320),lambda rows:action_count(rows,'MOVE')>0)
        camera+=delta
        base=finger_samples(camera)[0]
        require(any(sample[2]>base[2]+1 and abs(sample[3]-base[3])<=2 for sample in finger_samples(delta)),
                'Look did not apply positive horizontal displacement')
        edge=v3_stage('v3-look-edge',v3_move(360,320),lambda rows:action_count(rows,'UP')==1 and action_count(rows,'DOWN')==1)
        camera+=edge
        edge_samples=finger_samples(edge)
        assert_look_rebase(edge,base[2],base[3])
        camera+=v3_stage('v3-look-release',['keyup','l'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-look-delta-rebase',camera,downs=2,peak=1)
        v3_stage('v3-combined-position',v3_move(220,320))
        combined=v3_stage('v3-combined-joystick',['keydown','w'],lambda rows:action_count(rows,'DOWN')==1)
        combined+=v3_stage('v3-combined-look',['keydown','l'],lambda rows:any('activeAfter=2' in row for row in observed(rows)))
        combined+=v3_stage('v3-combined-fire',['mousedown','1'],lambda rows:any('activeAfter=3' in row for row in observed(rows)))
        combined+=v3_stage('v3-combined-seed',v3_move(221,320))
        combined+=v3_stage('v3-combined-motion',v3_move(241,320),lambda rows:action_count(rows,'MOVE')>0)
        combined+=v3_stage('v3-combined-fire-up',['mouseup','1'],lambda rows:any('activeAfter=2' in row for row in observed(rows)))
        combined+=v3_stage('v3-combined-look-up',['keyup','l'],lambda rows:any('activeAfter=1' in row for row in observed(rows)))
        combined+=v3_stage('v3-combined-joystick-up',['keyup','w'],lambda rows:action_count(rows,'UP')==1)
        v3_finish('v3-joystick-look-fire',combined,downs=1,peak=3)
        if args.editor_v3:
            profile_path='/data/waydroid_tmp/anvildroid/keymaps/'+PACKAGE
            def editor_bytes():
                v3_guard()
                return run(guest+['--uid','1000','--gid','1000','--','/system/bin/cat',profile_path])
            before_editor=editor_bytes()
            def editor_measure(label,rows=None):
                nonlocal window,offset
                if not nested:window=mapped_windows()[0]
                if rows is None:rows=logs()
                geometry=editor_attachment(rows,m['id'])
                ox,oy,ew,eh=geometry
                if nested:
                    scene=run(input_prefix+['env','XDG_RUNTIME_DIR=/run/user/'+str(m['owner_uid']),
                              'WAYLAND_DISPLAY='+transport['wayland_display'],'weston-debug','scene-graph'],timeout=5)
                    (args.output/(label+'-scene.txt')).write_text(scene)
                    offset=scene_rectangle(scene,m['id'])
                    section=next(part for part in scene.split('\n\tView ') if 'role xdg_toplevel' in part and ' of waydroid.anvildroid.'+m['id']+'.'+PACKAGE+',' in part)
                    bounds=tuple(map(int,re.search(r'position: \((-?\d+), (-?\d+)\) -> \((-?\d+), (-?\d+)\)',section).groups()))
                    host_w,host_h=bounds[2]-bounds[0],bounds[3]-bounds[1]
                else:
                    host=run(x+['getwindowgeometry','--shell',window])
                    (args.output/(label+'-window.txt')).write_text(host)
                    values=dict(line.split('=',1) for line in host.splitlines() if '=' in line)
                    host_w,host_h=int(values['WIDTH']),int(values['HEIGHT'])
                frame=editor_frame(geometry,host_w,host_h,nested)
                return geometry,frame,rows
            def editor_open(label):
                nonlocal window
                if not nested:
                    window=mapped_windows()[0]
                    run(x+['windowfocus','--sync',window])
                rows=v3_stage(label,['key','F2'],lambda lines:any('AnvilKeymapOverlay:' in line and 'attached ' in line for line in lines),settle=.2)
                require(not observed(rows),'Opening editor leaked Android contact')
                return editor_measure(label,rows)
            def editor_click(label,geometry,top,point):
                ox,oy,ew,eh=geometry;px,py=point
                require(0<=px<ew and 0<=py<eh,'Editor click outside content')
                rows=v3_stage(label,v3_move(top[0]+ox+px,top[1]+oy+py)+['click','1'],settle=.2)
                require(not observed(rows),label+': editor click leaked Android input')
                return rows
            geometry,top,editor_logs=editor_open('v3-editor-open')
            ox,oy,ew,eh=geometry;buttons=editor_targets(ew,eh)
            point=(int(ew*.45),int(eh*.78))
            editor_logs+=editor_click('v3-editor-add',geometry,top,buttons['add'])
            editor_logs+=editor_click('v3-editor-place',geometry,top,point)
            editor_logs+=editor_click('v3-editor-key-bind',geometry,top,buttons['bind_key'])
            binding=v3_stage('v3-editor-key-input',['key','z'])
            require(not observed(binding),'Binding capture leaked Android contact');editor_logs+=binding
            editor_logs+=editor_click('v3-editor-save',geometry,top,buttons['save'])
            deadline=time.monotonic()+5
            while True:
                saved=editor_bytes()
                (args.output/'v3-editor-saved-profile.txt').write_text(saved)
                if saved!=before_editor:break
                require(time.monotonic()<deadline,'Editor Save did not persist a changed profile');time.sleep(.1)
            header=saved.splitlines()[0].split()
            require(header[:4]==['ANVIL_KEYMAP','3',m['id'],PACKAGE],'Editor Save lost V3 scope')
            saved_rows=[tuple(map(int,line.split())) for line in saved.splitlines()[1:]]
            require(len(saved_rows)==len([pointer,joystick,swipe,look,fire])+1 and all(len(row)==19 for row in saved_rows),
                    'Editor Save lost existing V3 controls or new row')
            require(saved_rows[:5]==[pointer,joystick,swipe,look,fire],'Editor Save modified existing V3 controls')
            authored=[row for row in saved_rows if row[0]==44 and row[9]==0]
            require(len(authored)==1 and authored[0][4]==0,'Visible key binding did not save new Basic Z control')
            require(abs(authored[0][1]-round(point[0]*65535/(ew-1)))<=2 and
                    abs(authored[0][2]-round(point[1]*65535/(eh-1)))<=2,'Editor saved wrong authored position')
            # Fresh mapped input witnesses Save completion/removal of the
            # input-consuming overlay, not only the file write.
            delivery=v3_stage('v3-editor-saved-down',['keydown','z'],lambda rows:action_count(rows,'DOWN')==1)
            delivery+=v3_stage('v3-editor-saved-up',['keyup','z'],lambda rows:gesture_complete(delivery+rows))
            v3_finish('v3-editor-visible-save',delivery,downs=1,peak=1)
            # Reload persisted bytes through the production app profile path.
            v3_reload(saved_rows,'v3-editor-reload')
            require(editor_bytes()==saved,'Profile reload changed saved bytes')
            geometry,top,cancel_logs=editor_open('v3-editor-cancel-open')
            buttons=editor_targets(geometry[2],geometry[3])
            selected=(round(authored[0][1]*(geometry[2]-1)/65535),round(authored[0][2]*(geometry[3]-1)/65535))
            cancel_logs+=editor_click('v3-editor-cancel-select',geometry,top,selected)
            def button_pixels(label):
                # Compare actual rendered button pixels with the pointer moved
                # away in both captures. Raw RGB avoids PNG timestamp metadata
                # creating a false mutation witness.
                ox,oy,ew,eh=geometry;cx,cy=buttons['tap']
                v3_stage(label+'-pointer',v3_move(ox+top[0]+ew//10,oy+top[1]+eh//2))
                crop=f'60x26+{offset[0]+top[0]+ox+cx-30}+{offset[1]+top[1]+oy+cy-13}'
                target=args.output/(label+'.rgb')
                run(input_prefix+input_env+['import','-window',window,'-crop',crop,'+repage','-depth','8','rgb:'+str(target)])
                pixels=target.read_bytes()
                require(len(pixels)==60*26*3,'Incomplete graphical editor button capture')
                return pixels
            before_change=button_pixels('v3-editor-cancel-before')
            cancel_logs+=editor_click('v3-editor-cancel-change',geometry,top,buttons['tap'])
            after_change=button_pixels('v3-editor-cancel-after')
            require(after_change!=before_change,'Visible editor mode change had no rendered effect')
            cancel_logs+=editor_click('v3-editor-cancel-exit',geometry,top,buttons['cancel'])
            require(editor_bytes()==saved,'Editor Cancel changed exact saved bytes')
            recovery=v3_stage('v3-editor-cancel-down',['keydown','z'],lambda rows:action_count(rows,'DOWN')==1)
            recovery+=v3_stage('v3-editor-cancel-up',['keyup','z'],lambda rows:gesture_complete(recovery+rows))
            v3_finish('v3-editor-visible-cancel',recovery,downs=1,peak=1)
            require(editor_bytes()==saved,'Editor Cancel changed profile after playback recovery')
            if args.editor_lifecycle:
                spec=importlib.util.spec_from_file_location('editor_lifecycle',Path(__file__).with_name('editor-lifecycle.py'))
                lifecycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(lifecycle)
                editor_window=lambda:(window,offset)
                lifecycle.exercise({**globals(),**locals()})
        if args.capture_v3:
            trace_process=None;trace_files=[]
            trace_path=args.output/'v3-capture-protocol.log'
            def stop_capture_trace():
                nonlocal trace_process
                if trace_process is not None:
                    if trace_process.poll() is None:
                        try:os.killpg(trace_process.pid,signal.SIGTERM)
                        except ProcessLookupError:pass
                    try:trace_process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:os.killpg(trace_process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        trace_process.wait(timeout=2)
                    trace_process=None
                for output in trace_files:output.close()
                trace_files.clear()
            try:
                if nested:
                    v3_guard()
                    trace_files=[trace_path.open('w'),(args.output/'v3-capture-protocol-stderr.log').open('w')]
                    trace_process=subprocess.Popen(input_prefix+['prlimit','--fsize=8388608','--',
                        'timeout','--foreground','--kill-after=2','90','env',
                        'XDG_RUNTIME_DIR=/run/user/'+str(m['owner_uid']),
                        'WAYLAND_DISPLAY='+transport['wayland_display'],'weston-debug','proto'],
                        stdin=subprocess.DEVNULL,stdout=trace_files[0],stderr=trace_files[1],start_new_session=True)
                    time.sleep(.2)
                    require(trace_process.poll() is None,'Private compositor protocol trace could not start')
                registry_code=('import importlib.util,json,socket; '
                    's=importlib.util.spec_from_file_location("desktop","/usr/local/lib/anvildroid-controller/runtime-desktop.py"); '
                    'm=importlib.util.module_from_spec(s);s.loader.exec_module(m); '
                    'c=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);c.settimeout(3); '
                    'c.connect("/run/anvil-xdg/wayland-probe");print(json.dumps(m.registry_protocols(c)));c.close()')
                v3_guard()
                protocols=json.loads(run(prefix+['python3','-c',registry_code],timeout=5))
                (args.output/'v3-capture-protocols.json').write_text(json.dumps(protocols,indent=2))
                require_capture_protocols(protocols)
                captured=v3_point(key=38,kind=8,nx=44000,ny=32000,look_width=12000,look_height=12000,
                                  sensitivity_x=100,sensitivity_y=100,activation=1)
                # RMB fire leaves the ordinary LMB click available to activate a
                # nested Wayland client after host output focus is restored.
                captured_fire=v3_point(key=273,kind=0,nx=52000,ny=50000,source=1,end_mode=2)
                v3_reload([captured,captured_fire],'v3-capture')
                def pointer_position(label):
                    value=run(x+['getmouselocation','--shell'])
                    (args.output/(label+'.txt')).write_text(value)
                    fields=dict(line.split('=',1) for line in value.splitlines() if '=' in line)
                    return int(fields['X']),int(fields['Y'])
                contact=v3_stage('v3-capture-lock',['key','l'],lambda rows:action_count(rows,'DOWN')==1)
                # Production emits the activation finger only from its compositor
                # locked callback. Verify direction independently, then use client
                # protocol suppression on nested Wayland or host confinement on X11.
                initial=finger_samples(contact)[0]
                locked_at=pointer_position('v3-capture-pointer-before')
                relative=v3_stage('v3-capture-relative',['mousemove_relative','--','8','0'],lambda rows:action_count(rows,'MOVE')>0)
                contact+=relative
                pointer_after=pointer_position('v3-capture-pointer-after')
                if not nested:require(pointer_after==locked_at,'Native X11 capture did not confine the absolute host pointer')
                require_relative_direction(relative,initial[2],initial[3])
                contact+=v3_stage('v3-capture-fire',['mousedown','3'],lambda rows:any('activeAfter=2' in row for row in observed(rows)))
                contact+=v3_stage('v3-capture-fire-release',['mouseup','3'],lambda rows:any('activeAfter=1' in row for row in observed(rows)))
                contact+=v3_stage('v3-capture-escape',['key','Escape'],lambda rows:action_count(rows,'UP')==1 or action_count(rows,'CANCEL')==1)
                v3_finish('v3-capture-relative-fire-escape',contact,downs=1,peak=2)
                quiet=v3_stage('v3-capture-escape-suppressed',['mousemove_relative','--','5','0'])
                require(not any('source=0x1002' in row for row in observed(quiet)),'Escape left camera touch active')
                recovery=v3_stage('v3-capture-fresh-lock',['key','l'],lambda rows:action_count(rows,'DOWN')==1)
                recovery+=v3_stage('v3-capture-blur',['windowfocus','--sync',root_window[1]],
                                   lambda rows:action_count(rows,'UP')==1 or action_count(rows,'CANCEL')==1)
                touch_events(recovery)
                activate=['windowfocus','--sync',window]+v3_move(220,320)
                if nested:activate+=['click','1']
                activation=v3_stage('v3-capture-refocus',activate,settle=.3)
                require(not any('source=0x1002' in row for row in observed(activation)) and
                        not any('AnvilKeymap:' in row and 'touch package=' in row for row in activation),
                        'Capture restarted during refocus without fresh activation')
                quiet=v3_stage('v3-capture-refocus-suppressed',['mousemove_relative','--','5','0'])
                require(not any('source=0x1002' in row for row in observed(quiet)),
                        'Camera resumed on refocus before fresh toggle')
                recovery+=v3_stage('v3-capture-refocus-fresh',['key','l'],lambda rows:action_count(rows,'DOWN')==1)
                recovery+=v3_stage('v3-capture-final-escape',['key','Escape'],lambda rows:action_count(rows,'UP')==1 or action_count(rows,'CANCEL')==1)
                v3_finish('v3-capture-focus-recovery',recovery,downs=2,peak=1)
                lock_witness={'kind':'native-x11-physical-confinement','absolute_pointer_unchanged':True}
                if nested:
                    stop_capture_trace()
                    scene=(args.output/'v3-capture-scene.txt').read_text()
                    section=next(part for part in scene.split('\n\tView ') if 'role xdg_toplevel' in part and
                                 ' of waydroid.anvildroid.'+m['id']+'.'+PACKAGE+',' in part)
                    surface_id=int(re.search(r'surface ID (\d+)',section)[1])
                    lock_witness={'kind':'nested-wayland-client-protocol',
                                  **capture_protocol_witness(trace_path.read_text(),surface_id)}
                capture_result={'verified':True,'activation':'compositor-locked-dependent Android DOWN',
                                'relative_direction_verified':True,'lock_witness':lock_witness,
                                'outer_x11_pointer':{'before':locked_at,'after':pointer_after,'unchanged':pointer_after==locked_at},
                                'escape_release':True,'focus_recovery':True,'protocols':protocols,
                                'unlimited_movement_verified':False}
            finally:
                stop_capture_trace()
    (args.output/'summary.json').write_text(json.dumps({'runtime':m['id'],'generation':m['generation'],'package_sha256':m.get('package_sha256'),'guest_display_backend':'wayland' if nested else 'native-x11','input_transport':transport or {'kind':'private-xvfb'},'scene_graph_capture_ms':scene_elapsed_ms,'scene_graph_timeout_seconds':5 if nested else None,'editor_v3':args.editor_v3,'keymap_v3':args.keymap_v3,'capture_toggle':capture_result if args.keymap_v3 else None,'worker_start_time':identity_start,'worker_mount_namespace':identity_mnt,'results':results},indent=2))
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
