#!/usr/bin/env python3
"""Real two-UID share acceptance in a disposable PID/mount namespace only."""
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch


def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

shares=load('runtime-shares');default=load('runtime-default-shares')
ALICE,BOB=61001,61002


def probe(uid,path,write=False):
    pid=os.fork()
    if pid==0:
        try:
            os.setgroups([]);os.setgid(uid);os.setuid(uid)
            if write:Path(path).write_text('written by '+str(uid))
            else:assert Path(path).read_text()=='hello'
            os._exit(0)
        except OSError as error:os._exit(error.errno if error.errno<126 else 125)
        except BaseException:os._exit(125)
    _,status=os.waitpid(pid,0)
    assert os.WIFEXITED(status),status
    return os.WEXITSTATUS(status)


def acl(path,uid):
    # Linux POSIX ACL: owner rwx, named uid r-x, group none, mask r-x,
    # other none. Avoid an external setfacl/package dependency in CI.
    entries=[(1,7,0xffffffff),(2,5,uid),(4,0,0xffffffff),(16,5,0xffffffff),(32,0,0xffffffff)]
    os.setxattr(path,'system.posix_acl_access',struct.pack('<I',2)+b''.join(struct.pack('<HHI',*item) for item in entries))


def child(original):
    assert os.geteuid()==0 and os.getpid()==1
    subprocess.run(['mount','--make-rprivate','/'],check=True)
    with tempfile.TemporaryDirectory(prefix='anvil-two-uid-',dir='/tmp') as folder:
        root=Path(folder);root.chmod(0o755)
        homes={}
        for uid in (ALICE,BOB):
            home=root/str(uid);home.mkdir(mode=0o711);os.chown(home,uid,uid)
            source=home/'chosen';source.mkdir(mode=0o700);os.chown(source,uid,uid)
            (source/'hello').write_text('hello');os.chown(source/'hello',uid,uid)
            homes[uid]=home
        account=lambda uid:types.SimpleNamespace(pw_dir=str(homes[uid]),pw_gid=uid)
        for module in (shares,default.shares):
            # Each open_source still forks and drops to the real numeric UID;
            # only NSS home lookup is replaced, never ownership/stat/access.
            with patch.object(module.pwd,'getpwuid',side_effect=account):
                fd=module.open_source(str(homes[ALICE]/'chosen'),ALICE);os.close(fd)
                try:module.open_source(str(homes[ALICE]/'chosen'),BOB)
                except RuntimeError:pass
                else:raise AssertionError('other owner accepted source')
                alien=homes[ALICE]/'alien';alien.mkdir();os.chown(alien,BOB,BOB)
                try:module.open_source(str(alien),ALICE)
                except RuntimeError:pass
                else:raise AssertionError('foreign-owned source accepted')
                alien.rmdir()
                # UID ownership alone is insufficient: the source opener must
                # obey actual ancestor permissions, then an explicit ACL.
                parent=homes[ALICE]/'restricted';parent.mkdir(mode=0o700)
                nested=parent/'chosen';nested.mkdir();os.chown(nested,ALICE,ALICE)
                try:module.open_source(str(nested),ALICE)
                except RuntimeError:pass
                else:raise AssertionError('source opener bypassed ancestor permissions')
                acl(parent,ALICE)
                fd=module.open_source(str(nested),ALICE);os.close(fd)
                nested.rmdir();parent.rmdir()
        for owner,other in ((ALICE,BOB),(BOB,ALICE)):
            for kind in ('managed','default'):
                store=root/(str(owner)+'-'+kind+'-store');store.mkdir(mode=0o700)
                state=root/(str(owner)+'-'+kind+'-state');state.mkdir(mode=0o700)
                data=root/(str(owner)+'-'+kind+'-data');data.mkdir(mode=0o755)
                source=homes[owner]/'chosen'
                for readonly in (True,False):
                    mounts=[];context=None
                    with patch.object(shares.pwd,'getpwuid',side_effect=account):
                        if kind=='managed':
                            shares.configure(store,owner,dict(name='Docs',source=str(source),read_only=readonly))
                            try:shares.list_shares(store,other)
                            except RuntimeError:pass
                            else:raise AssertionError('other UID read managed grants')
                            status=shares.apply_preboot(store,data,owner,mounts.append,original_mnt=original)
                            assert status['Docs']['mounted'],status
                            target=data/'media/0/AnvilDroid/Docs'
                        else:
                            info=data.stat()
                            saved={'owner_uid':owner,'data_path':str(data),'data_owner_uid':info.st_uid,'data_identity':{'device':info.st_dev,'inode':info.st_ino}}
                            class Runtime:
                                def saved(self):return saved
                                def authorize(self,uid):
                                    if uid!=owner:raise RuntimeError('owner mismatch')
                                    return {},saved
                                def stopped(self):pass
                                def state(self):return 'STOPPED'
                            adapter=default.DefaultShares(store,Runtime(),state)
                            with patch.object(default,'integration_status',return_value={}):
                                adapter.set(owner,dict(name='Docs',source=str(source),read_only=readonly))
                                try:adapter.get(other)
                                except RuntimeError:pass
                                else:raise AssertionError('other UID read Default grants')
                            config=root/'config_session';config.write_text(f'lxc.mount.entry = {data} data none rbind 0 0\n');config.chmod(0o600)
                            context=default.SessionMounts(store,Runtime(),state,original)
                            context.begin({'user_id':str(owner),'waydroid_data':str(data),'pid':'123'})
                            context.prepare(config)
                            assert context.mounted['Docs']['mounted'],context.mounted
                            alias=root/'guest-data';alias.mkdir()
                            subprocess.run(['mount','--rbind',str(context.stage/'data'),str(alias)],check=True)
                            target=alias/'media/0/AnvilDroid/Docs'
                        assert probe(owner,target/'hello')==0
                        assert probe(other,target/'hello')==13,'source mode must deny second UID'
                        acl(source,other)
                        assert probe(other,target/'hello')==0,'read ACL was lost across bind'
                        assert probe(other,target/'bob-write',True) in (13,30)
                        outcome=probe(owner,target/'alice-write',True)
                        assert outcome==(30 if readonly else 0),(kind,readonly,outcome)
                        os.removexattr(source,'system.posix_acl_access');source.chmod(0o700)
                        if not readonly:
                            assert (source/'alice-write').read_text()=='written by '+str(owner)
                            (source/'alice-write').unlink()
                        if context:
                            # Unmount the test guest alias before returning to the
                            # baseline, just as LXC shutdown releases its mounts.
                            subprocess.run(['umount','-R',str(alias)],check=True);alias.rmdir()
                            context.close(True)
                        else:
                            for mount in reversed(mounts):subprocess.run(['umount',str(mount)],check=True)
                assert not (data/'media/0/AnvilDroid/Docs/hello').exists(),'revoked share still exposed'
    print('PASS two real desktop UIDs: source ownership, ancestor permissions, ACL, RO/RW, manifest isolation, namespace revocation for managed and Default')


if __name__=='__main__':
    if '--child' in sys.argv:child(sys.argv[-1])
    elif '--mount-test' in sys.argv:
        subprocess.run(['unshare','--mount','--pid','--fork','--mount-proc',sys.executable,__file__,'--child',os.readlink('/proc/self/ns/mnt')],check=True)
    else:raise SystemExit('Use sudo python3 services/share-identities-test.py --mount-test (only /tmp fixtures)')
