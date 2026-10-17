#!/usr/bin/env python3
"""Real vold-style nonrecursive bind + slave-namespace share reconciliation."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

BASE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('shares',BASE/'runtime-shares.py');shares=importlib.util.module_from_spec(spec);spec.loader.exec_module(shares)


def mount(*args):subprocess.run(['mount',*map(str,args)],check=True)


def child(original):
    assert os.geteuid()==0 and os.getpid()==1
    mount('--make-rprivate','/')
    with tempfile.TemporaryDirectory(prefix='anvil-fuse-fixture-') as folder:
        root=Path(folder);root.chmod(0o755)
        home=root/'home';home.mkdir(mode=0o755)
        source=home/'chosen';source.mkdir(mode=0o777);source.chmod(0o777);os.chown(source,61001,61001)
        (source/'sentinel').write_text('preserve')
        instance=root/'runtime';instance.mkdir(mode=0o700)
        data=instance/'android/runtime/data';data.mkdir(parents=True)
        generation='a'*32
        (instance/'worker-identity.json').write_text(json.dumps({'runtime_id':'runtime','generation':generation}));(instance/'worker-identity.json').chmod(0o600)
        with patch.object(shares.pwd,'getpwuid',return_value=types.SimpleNamespace(pw_dir=str(home),pw_gid=61001)):
            shares.configure(instance,61001,dict(name='Docs',source=str(source),read_only=True))
            mounted=[]
            status=shares.apply_preboot(instance,data,61001,mounted.append,original_mnt=original)
        assert status['Docs']['mounted'],status
        guest=root/'guest';guest.mkdir();(guest/'data').mkdir()
        backend=guest/'mnt/pass_through/0/emulated';backend.mkdir(parents=True)
        ready_read,ready_write=os.pipe();stop_read,stop_write=os.pipe()
        pid=os.fork()
        if pid==0:
            os.close(ready_read);os.close(stop_write)
            os.unshare(os.CLONE_NEWNS);mount('--make-rprivate','/')
            mount('--rbind',data,guest/'data');mount('--make-rshared',guest/'data')
            mount('--bind',guest/'data/media',backend)
            # An existing zygote/provider-like slave namespace must receive the
            # later bind, not just the init namespace in which helper runs.
            observer=os.fork()
            if observer==0:
                os.unshare(os.CLONE_NEWNS);mount('--make-rslave','/')
                os.chroot(guest);os.chdir('/')
                os.write(ready_write,(str(os.getpid())+'\n').encode())
                os.read(stop_read,1);os._exit(0)
            os.chroot(guest);os.chdir('/')
            os.read(stop_read,1)
            os.waitpid(observer,0);os._exit(0)
        os.close(ready_write);os.close(stop_read)
        observer=int(os.read(ready_read,100));os.close(ready_read)
        target=Path(f'/proc/{pid}/root/mnt/pass_through/0/emulated/0/AnvilDroid/Docs')
        slave=Path(f'/proc/{observer}/root/mnt/pass_through/0/emulated/0/AnvilDroid/Docs')
        assert not (target/'sentinel').exists() and not (slave/'sentinel').exists()
        start=shares.process_start(pid)
        command=[sys.executable,str(BASE/'runtime-shares.py'),'--reconcile',str(instance),str(data),str(pid),start,generation,original]
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=15)
            assert result.returncode==0,result.stderr
            status=json.loads(result.stdout);assert status['Docs']['mounted'],status
            assert (target/'sentinel').read_text()=='preserve'
            assert (slave/'sentinel').read_text()=='preserve','existing slave did not receive share'
            for path in (target,slave):
                assert os.statvfs(path).f_flag & os.ST_RDONLY
                try:(path/'bad').write_text('no')
                except OSError as error:assert error.errno==30,error
                else:raise AssertionError('RO reconciliation accepted writes')
            # Stale generations fail before mounts and do not silently succeed.
            bad=command.copy();bad[-2]='b'*32
            assert subprocess.run(bad,capture_output=True,timeout=5).returncode!=0
        finally:
            os.write(stop_write,b'xx');os.close(stop_write);os.waitpid(pid,0)
            for point in mounted:subprocess.run(['umount',point],check=True)
        print('PASS vold nonrecursive backend: verified RO mirror, existing slave propagation, stale generation refusal')


if __name__=='__main__':
    if '--child' in sys.argv:child(sys.argv[-1])
    elif '--mount-test' in sys.argv:
        subprocess.run(['unshare','--mount','--pid','--fork','--mount-proc',sys.executable,__file__,'--child',os.readlink('/proc/self/ns/mnt')],check=True)
    else:raise SystemExit('Use --mount-test as root; disposable /tmp namespace only')
