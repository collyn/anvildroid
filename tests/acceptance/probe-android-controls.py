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
        activation=[]
        if nested and name=='held-refocus':
            # X output focus does not select a Wayland client in Weston.
            # Keep the real click-to-focus input separate from mapped touch
            # assertions; never count that ordinary mouse click as a finger.
            activate=['windowfocus','--sync',window,'mousemove','--window',window,
                      str(220+offset[0]),str(320+offset[1]),'click','1']
            run(x+activate);time.sleep(.1);before=logs()
            run(x+['keydown','w','windowfocus',root_window[1],'sleep','.1'])
            blurred=logs();require(blurred[:len(before)]==before,'Android log buffer rotated')
            run(x+activate);time.sleep(.1);focused=logs()
            require(focused[:len(blurred)]==blurred,'Android log buffer rotated')
            activation=focused[len(blurred):]
            require(not any('AnvilKeymap:' in v and 'touch package=' in v for v in activation),
                    'Refocus activation unexpectedly restarted mapped contact')
            clicks=[v for v in activation if 'AnvilTouchProbe:' in v and ' ACTION_' in v]
            require(len(clicks)==2 and ' ACTION_DOWN ' in clicks[0] and
                    ' ACTION_UP ' in clicks[-1] and 'activeAfter=0' in clicks[-1] and
                    all('source=0x2002' in v and 'tool=MOUSE(3)' in v for v in clicks),
                    'Refocus activation was not one ordinary mouse click')
            run(x+['keydown','w','sleep','.15']);held=logs()
            require(held[:len(focused)]==focused,'Android log buffer rotated')
            require(not any(' ACTION_DOWN ' in v for v in held[len(focused):]),
                    'Held key resumed on Wayland refocus before release')
            run(x+['keyup','w','sleep','.1','keydown','w','sleep','.15','keyup','w'])
            time.sleep(.2);after=logs()
            require(after[:len(focused)]==focused,'Android log buffer rotated')
            lines=blurred[len(before):]+after[len(focused):]
            (args.output/'held-refocus-activation.log').write_text('\n'.join(activation)+'\n')
            (args.output/'held-refocus-all.log').write_text('\n'.join(after[len(before):])+'\n')
        else:
            before=logs();run(x+sequence);time.sleep(.2);after=logs()
            require(after[:len(before)]==before,'Android log buffer rotated during acceptance; rerun')
            lines=after[len(before):]
        verify_worker()
        if nested:require(compositor_state()==compositor_identity,'Private compositor changed during acceptance')
        text='\n'.join(lines)+'\n';(args.output/(name+'.log')).write_text(text)
        events=[v for v in lines if 'AnvilTouchProbe:' in v and ' ACTION_' in v]
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
    (args.output/'summary.json').write_text(json.dumps({'runtime':m['id'],'generation':m['generation'],'package_sha256':m.get('package_sha256'),'guest_display_backend':'wayland' if nested else 'native-x11','input_transport':transport or {'kind':'private-xvfb'},'scene_graph_capture_ms':scene_elapsed_ms,'scene_graph_timeout_seconds':5 if nested else None,'worker_start_time':identity_start,'worker_mount_namespace':identity_mnt,'results':results},indent=2))
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
