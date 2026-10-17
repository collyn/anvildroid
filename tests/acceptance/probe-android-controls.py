#!/usr/bin/env python3
"""Real private-X11 → production bridge/scrcpy → Android MotionEvent acceptance.

sudo python3 tests/acceptance/probe-android-controls.py ROOT/acceptance.json HOST_WORKER_PID OUTPUT_DIR

Installs the observe-only APK and a test profile ONLY in the disposable guest,
relaunches that app, sends physical XTest input to its private Xvfb window,
and asserts actual Android event sequences. Requires live-android.py --x11.
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

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata',type=Path);parser.add_argument('worker_pid',type=int);parser.add_argument('output',type=Path)
    args=parser.parse_args();require(os.geteuid()==0,'Root required')
    root=args.metadata.parent
    require(root.parent==Path('/tmp') and root.name.startswith('anvil-live-') and root.resolve()==root and root.stat().st_uid==0 and not root.stat().st_mode & 0o077,'Unsafe disposable root')
    require(args.metadata.name=='acceptance.json' and not args.metadata.is_symlink(),'Unexpected metadata')
    m=json.loads(args.metadata.read_text());instance=Path(m['instance'])
    require(instance.parent==args.metadata.parent/'instances' and instance.resolve()==instance,'Unexpected disposable path')
    require(m.get('x11_display')==':77','Require private acceptance X11 display')
    cmdline=Path(f'/proc/{args.worker_pid}/cmdline').read_bytes().split(b'\0')
    require(str(instance).encode() in cmdline and m['generation'].encode() in cmdline and any(v.endswith(b'/runtime-worker.py') for v in cmdline),'Worker identity mismatch')
    def process_start():return Path(f'/proc/{args.worker_pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    identity_start=process_start();identity_mnt=os.readlink(f'/proc/{args.worker_pid}/ns/mnt')
    def verify_worker():
        require(process_start()==identity_start and os.readlink(f'/proc/{args.worker_pid}/ns/mnt')==identity_mnt and Path(f'/proc/{args.worker_pid}/cmdline').read_bytes().split(b'\0')==cmdline,'Worker changed during acceptance')
    require(os.readlink(f'/proc/{args.worker_pid}/ns/mnt')!=os.readlink('/proc/self/ns/mnt'),'Worker needs private mounts')
    args.output.mkdir(parents=True,exist_ok=True)
    prefix=['nsenter','-t',str(args.worker_pid),'-m','-n','-p','--']
    guest=prefix+['lxc-attach','-P',str(instance/'android'),'-n',m['id']]
    def run(command,**kwargs):
        child=subprocess.Popen(command,stdin=subprocess.PIPE if 'input' in kwargs else subprocess.DEVNULL,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:stdout,stderr=child.communicate(kwargs.pop('input',None),timeout=45)
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
    x=prefix+['env','DISPLAY=:77','XAUTHORITY=/run/anvil-x11-authority','xdotool']
    windows=run(x+['search','--class',r'\.org\.anvildroid\.touchprobe$']).splitlines()
    require(len(windows)==1,'Expected one unambiguous observer window');window=windows[0]
    run(x+['windowfocus','--sync',window,'mousemove','--window',window,'220','320'])
    def logs():return run(guest+['--','/system/bin/logcat','-d','-s','AnvilTouchProbe:I','AnvilKeymap:I','*:S']).splitlines()
    tree=run(prefix+['env','DISPLAY=:77','XAUTHORITY=/run/anvil-x11-authority','xwininfo','-root'])
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
        verify_worker();before=logs();run(x+sequence);time.sleep(.2);after=logs();verify_worker()
        require(after[:len(before)]==before,'Android log buffer rotated during acceptance; rerun')
        lines=after[len(before):];text='\n'.join(lines)+'\n';(args.output/(name+'.log')).write_text(text)
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
    (args.output/'summary.json').write_text(json.dumps({'runtime':m['id'],'generation':m['generation'],'package_sha256':m.get('package_sha256'),'worker_start_time':identity_start,'worker_mount_namespace':identity_mnt,'results':results},indent=2))
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
