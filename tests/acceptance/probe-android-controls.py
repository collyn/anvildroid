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

def touch_events(lines):
    """Require complete balanced Android gestures, not merely a final zero."""
    require(not any('WARNING' in row for row in lines),'Observer detected stale contacts')
    events=[row for row in lines if 'AnvilTouchProbe:' in row and ' ACTION_' in row]
    active=0
    for row in events:
        require(re.search(r' source=0x1002(?:$|\s)',row) is not None and
                re.findall(r'tool=([^ ]+)',row) and
                all(tool=='FINGER(1)' for tool in re.findall(r'tool=([^ ]+)',row)),
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

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata',type=Path);parser.add_argument('worker_pid',type=int);parser.add_argument('output',type=Path)
    args=parser.parse_args();require(os.geteuid()==0,'Root required')
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
    windows=run(x+(['search','--onlyvisible','--class','weston'] if nested else ['search','--class',r'\.org\.anvildroid\.touchprobe$'])).splitlines()
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
    def logs():return run(guest+['--','/system/bin/logcat','-d','-s','AnvilTouchProbe:I','AnvilKeymap:I','*:S']).splitlines()
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
            before=logs();checkpoint=before;collected=[]
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
    (args.output/'summary.json').write_text(json.dumps({'runtime':m['id'],'generation':m['generation'],'package_sha256':m.get('package_sha256'),'guest_display_backend':'wayland' if nested else 'native-x11','input_transport':transport or {'kind':'private-xvfb'},'scene_graph_capture_ms':scene_elapsed_ms,'scene_graph_timeout_seconds':5 if nested else None,'worker_start_time':identity_start,'worker_mount_namespace':identity_mnt,'results':results},indent=2))
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
