#!/usr/bin/env python3
"""Alternate real editor/input operations between two disposable Android guests."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


probe=module(Path(__file__).with_name('probe-android-controls.py'),'controls')
require=probe.require


class Endpoint:
    def __init__(self,metadata,pid,output):
        root=metadata.parent
        require(root.parent==Path('/tmp') and root.name.startswith('anvil-live-') and root.resolve()==root and
                root.stat().st_uid==0 and not root.stat().st_mode&0o077,'Unsafe disposable root')
        require(metadata.name=='acceptance.json' and not metadata.is_symlink(),'Unexpected metadata')
        self.m=json.loads(metadata.read_text());self.pid=pid;self.output=output
        self.instance=Path(self.m['instance'])
        require(self.instance.parent==root/'instances' and self.instance.resolve()==self.instance,'Unexpected instance')
        self.command=Path(f'/proc/{pid}/cmdline').read_bytes()
        arguments=self.command.split(b'\0')
        require(str(self.instance).encode() in arguments and self.m['generation'].encode() in arguments and
                any(arg.endswith(b'/runtime-worker.py') for arg in arguments),'Worker identity mismatch')
        self.identity=self.state()
        require(self.identity[1]!=os.readlink('/proc/self/ns/mnt'),'Worker must have private mounts')
        self.prefix=['nsenter','-t',str(pid),'-m','-n','-p','--']
        self.guest=self.prefix+['lxc-attach','-P',str(self.instance/'android'),'-n',self.m['id'],'--']
        self.nested=self.m.get('input_transport',{}).get('kind')=='nested-weston-x11'
        self.transport=probe.validate_nested_transport(self.m) if self.nested else None
        self.input_prefix=self.prefix
        if self.nested:
            t=self.transport;matches=[]
            for p in Path('/proc').iterdir():
                if not p.name.isdecimal():continue
                try:
                    args=(p/'cmdline').read_bytes().split(b'\0')
                    if args and Path(os.fsdecode(args[0])).name=='weston' and ('--socket='+t['wayland_display']).encode() in args and p.stat().st_uid==self.m['owner_uid'] and os.readlink(p/'ns/mnt')==t['compositor_mount_namespace'] and os.readlink(p/'ns/pid')==t['compositor_pid_namespace']:matches.append(int(p.name))
                except OSError:pass
            require(len(matches)==1,'Private compositor identity ambiguous')
            self.compositor=matches[0]
            self.compositor_identity=self.compositor_state()
            require(t['compositor_mount_namespace']!=os.readlink('/proc/self/ns/mnt') and
                    os.readlink(f'/proc/{pid}/ns/pid')==t['compositor_pid_namespace'],'Unexpected compositor namespace')
            self.input_prefix=['nsenter','-t',str(self.compositor),'-m','-n','-p','--']
        else:require(self.m.get('x11_display')==':77','Require private Xvfb')
        self.env=['env','DISPLAY=:77','XAUTHORITY='+ (self.transport['authority'] if self.nested else '/run/anvil-x11-authority')]
        self.x=self.input_prefix+self.env+['xdotool']
        backend=module(probe.REPO/'services/runtime-android.py','android_'+str(pid))
        images=module(probe.REPO/'services/runtime-images.py','images_'+str(pid))
        self.backend=backend.AndroidBackend(images.ImageBackend(root,root/'source'))
        status=self.request('status')
        if status['state']=='Frozen':status=self.request('resume')
        require(status['state']=='Running','Android fixture must be Running')

    def state(self):
        p=Path('/proc')/str(self.pid)
        return ((p/'stat').read_text().rsplit(')',1)[1].split()[19],os.readlink(p/'ns/mnt'),(p/'cmdline').read_bytes())

    def compositor_state(self):
        p=Path('/proc')/str(self.compositor)
        return ((p/'stat').read_text().rsplit(')',1)[1].split()[19],os.readlink(p/'ns/mnt'),os.readlink(p/'ns/pid'),(p/'cmdline').read_bytes())

    def guard(self):
        require(self.state()==self.identity,'Worker changed')
        if self.nested:require(self.compositor_state()==self.compositor_identity,'Compositor changed')

    def run(self,command,**kwargs):
        self.guard()
        return subprocess.run(command,check=True,capture_output=True,timeout=10,**kwargs).stdout.decode()

    def request(self,op,**payload):
        self.guard()
        request=dict(op=op,**payload)
        if op not in ('status','resume'):request['generation']=self.m['generation']
        value=self.backend.status(self.m['id'],op,request)
        require(value.get('generation')==self.m['generation'] and not value.get('app_error'),'Scoped request failed')
        return value

    def logs(self):return self.run(self.guest+['/system/bin/logcat','-d','-s','AnvilTouchProbe:I','AnvilKeymap:I','AnvilKeymapOverlay:I','*:S']).splitlines()

    def profile(self):return self.run(self.guest+['/system/bin/cat','/data/waydroid_tmp/anvildroid/keymaps/'+probe.PACKAGE])

    def setup(self,nx):
        profile=probe.v3_profile(self.m['id'],[probe.v3_point(key=48,nx=nx,ny=50000,end_mode=2)])
        self.run(self.guest+['/system/bin/sh','-c','cat > /data/waydroid_tmp/anvildroid/keymaps/'+probe.PACKAGE],input=profile.encode())
        for action in ('force_stop','launch'):self.request('app_action',action=action,package=probe.PACKAGE)
        self.run(self.guest+['/system/bin/cmd','statusbar','collapse'])
        time.sleep(1)
        deadline=time.monotonic()+10
        while True:
            time.sleep(.3)
            try:
                windows=self.run(self.x+['search','--onlyvisible','--class','weston' if self.nested else r'\.org\.anvildroid\.touchprobe$']).splitlines()
                require(len(windows)==1,'Ambiguous scoped observer')
                self.window=windows[0];self.offset=(0,0)
                if self.nested:
                    self.offset=probe.scene_rectangle(self.scene(),self.m['id'])
                    require(self.window==self.transport['x11_window'],'Unexpected compositor output')
                self.run(self.x+['windowfocus','--sync',self.window]+self.move(220,320)+(['click','1'] if self.nested else []))
                time.sleep(.2)
                pid=self.run(self.guest+['/system/bin/pidof',probe.PACKAGE]).strip()
                require(pid.isdecimal(),'Observer PID ambiguous')
                ready=self.run(self.guest+['/system/bin/logcat','-d','--pid='+pid,'-s','AnvilTouchProbe:I','*:S']).splitlines()
                focus=[line for line in ready if ' FOCUS=' in line]
                require(focus and ' FOCUS=true active=0' in focus[-1] and any(' SIZE contentPx=' in line for line in ready),
                        'Observer not ready')
                break
            except (subprocess.CalledProcessError,RuntimeError):
                require(time.monotonic()<deadline,'Observer did not map')
        time.sleep(.3)
        require(self.profile()==profile,'Scoped profile setup failed')
        self.nx=nx

    def scene(self):return self.run(self.input_prefix+['env','XDG_RUNTIME_DIR=/run/user/'+str(self.m['owner_uid']),'WAYLAND_DISPLAY='+self.transport['wayland_display'],'weston-debug','scene-graph'])

    def move(self,x,y):return ['mousemove','--window',self.window,str(self.offset[0]+x),str(self.offset[1]+y)]

    def editor(self,new_key,during=None):
        before=self.logs();self.run(self.x+['key','F2']);deadline=time.monotonic()+5
        while True:
            time.sleep(.1);rows=self.logs()[len(before):]
            try:geometry=probe.editor_attachment(rows,self.m['id']);break
            except RuntimeError:require(time.monotonic()<deadline,'Editor did not attach')
        ox,oy,w,h=geometry
        if self.nested:
            scene=self.scene();self.offset=probe.scene_rectangle(scene,self.m['id'])
            section=next(p for p in scene.split('\n\tView ') if 'role xdg_toplevel' in p and ' of '+probe.surface_app_id(self.m['id'])+',' in p)
            bounds=list(map(int,re.search(r'position: \((-?\d+), (-?\d+)\) -> \((-?\d+), (-?\d+)\)',section).groups()))
            host_w,host_h=bounds[2]-bounds[0],bounds[3]-bounds[1]
        else:
            info=dict(line.split('=',1) for line in self.run(self.x+['getwindowgeometry','--shell',self.window]).splitlines() if '=' in line)
            host_w,host_h=int(info['WIDTH']),int(info['HEIGHT'])
        frame=probe.editor_frame(geometry,host_w,host_h,self.nested)
        def click(point):
            self.run(self.x+self.move(frame[0]+ox+point[0],frame[1]+oy+point[1])+['click','1']);time.sleep(.15)
        buttons=probe.editor_targets(w,h)
        click((round(self.nx*(w-1)/65535),round(getattr(self,'ny',50000)*(h-1)/65535)))
        click(buttons['bind_key']);self.run(self.x+['key',new_key]);time.sleep(.15)
        if during:during()
        old=self.profile();click(buttons['save']);deadline=time.monotonic()+5
        while self.profile()==old:
            require(time.monotonic()<deadline,'Editor did not persist isolated binding');time.sleep(.1)
        require(not any('AnvilTouchProbe:' in row and ' ACTION_' in row for row in self.logs()[len(before):]),'Editor leaked touch')
        time.sleep(.2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata_a',type=Path);parser.add_argument('pid_a',type=int)
    parser.add_argument('metadata_b',type=Path);parser.add_argument('pid_b',type=int);parser.add_argument('output',type=Path)
    args=parser.parse_args();require(os.geteuid()==0,'Root required')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    a=Endpoint(args.metadata_a,args.pid_a,output);b=Endpoint(args.metadata_b,args.pid_b,output)
    require(a.m['id']!=b.m['id'] and a.identity[1]!=b.identity[1],'Require distinct runtime namespaces')
    a.setup(16000);b.setup(49000);results=[]
    def isolated_input(active,peer,key,label):
        before=active.logs();peer_before=peer.logs();profile=peer.profile()
        active.run(active.x+['keydown',key,'sleep','.2','keyup',key]);deadline=time.monotonic()+5
        while True:
            time.sleep(.1);rows=active.logs()[len(before):]
            if probe.gesture_complete(rows):break
            require(time.monotonic()<deadline,'Scoped input did not complete')
        events=probe.touch_events(rows)
        require(sum(' ACTION_DOWN ' in r for r in events)==1,'Unexpected gesture count')
        sizes=[re.search(r' SIZE contentPx=(\d+)x(\d+)',line) for line in active.logs()]
        sizes=[match for match in sizes if match]
        require(sizes,'Observer content size unavailable')
        down=next(sample for sample in probe.finger_samples(rows) if sample[0]=='DOWN')
        expected=active.nx*(int(sizes[-1][1])-1)/65535
        require(abs(down[2]-expected)<=4,'Input used another runtime profile position')
        peer_rows=peer.logs()[len(peer_before):]
        probe.require_no_mapped_contact(peer_rows,'Input crossed runtime boundary')
        require(peer.profile()==profile,'Input changed peer profile')
        (output/(label+'.log')).write_text('\n'.join(rows)+'\n')
        (output/(label+'-peer.log')).write_text('\n'.join(peer_rows)+'\n')
        results.append(dict(scenario=label,passed=True,peer_contacts=0,down_x=down[2],expected_x=expected))
    isolated_input(a,b,'b','a-input');isolated_input(b,a,'b','b-input')
    for active,peer,key,code,peer_key,label in [(a,b,'v',47,'b','a-editor'),(b,a,'g',34,'v','b-editor')]:
        checkpoint=[peer.logs()];profile=peer.profile()
        def during():
            probe.require_no_mapped_contact(peer.logs()[len(checkpoint[0]):],'Opening editor injected into peer')
            isolated_input(peer,active,peer_key,label+'-peer-during-edit')
            checkpoint[0]=peer.logs()
        active.editor(key,during)
        require(peer.profile()==profile,'Editor modified peer saved bytes')
        probe.require_no_mapped_contact(peer.logs()[len(checkpoint[0]):],'Editor injected into peer')
        saved=active.profile();header=saved.splitlines()[0].split();row=list(map(int,saved.splitlines()[1].split()))
        require(header[2:4]==[active.m['id'],probe.PACKAGE] and row[0]==code and row[1]==active.nx,'Editor lost own profile scope')
        (output/(label+'-profile.txt')).write_text(saved)
        isolated_input(active,peer,key,label+'-playback')
        results.append(dict(scenario=label,passed=True,peer_profile_unchanged=True))
    summary=dict(runtimes=[a.m['id'],b.m['id']],generations=[a.m['generation'],b.m['generation']],
                 package_sha256=[a.m['package_sha256'],b.m['package_sha256']],results=results)
    (output/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
