#!/usr/bin/env python3
"""Directory-grant regression tests; optional --mount-test uses isolated mounts."""
import importlib.util
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('shares', Path(__file__).with_name('runtime-shares.py'))
shares = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shares)


class SharesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.instance = self.root / 'instance'; self.instance.mkdir()
        self.home = self.root / 'home'; self.home.mkdir()
        self.source = self.home / 'chosen'; self.source.mkdir()
        self.uid = os.getuid() or 1000
        self.account = types.SimpleNamespace(pw_dir=str(self.home), pw_gid=os.getgid())
    def tearDown(self):
        self.temp.cleanup()
    def opener(self, path, uid):
        with patch.object(shares.pwd, 'getpwuid', return_value=self.account):
            return shares._source(path, os.getuid())
    def configure(self, **kwargs):
        with patch.object(shares, 'open_source', side_effect=self.opener):
            return shares.configure(self.instance, self.uid, dict(name='Docs',source=str(self.source),read_only=True,**kwargs))
    def test_persist_remove_modes_and_scope(self):
        value = self.configure()
        self.assertFalse(value['shares'][0]['mounted'])
        self.assertTrue(value['shares'][0]['read_only'])
        self.assertEqual((self.instance/shares.FILE).stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RuntimeError, 'owner mismatch'):
            shares.list_shares(self.instance, self.uid + 1)
        self.assertEqual(shares.list_shares(self.root,self.uid)['shares'], [])
        shares.remove(self.instance,self.uid,'Docs')
        self.assertEqual(shares.list_shares(self.instance,self.uid)['shares'], [])
    def test_source_path_security(self):
        (self.home/'link').symlink_to(self.source)
        for path in [str(self.home), str(self.home/'link'), str(self.home/'chosen'/'..'/'chosen'), '/etc', str(self.home/'.ssh')]:
            with self.subTest(path=path), self.assertRaises((RuntimeError,OSError)):
                self.opener(path,self.uid)
    def test_missing_and_pinned_sources(self):
        with self.assertRaises(FileNotFoundError): self.opener(str(self.home/'missing'),self.uid)
        fd=self.opener(str(self.source),self.uid)
        try:
            before=os.fstat(fd)
            self.source.rename(self.home/'old')
            self.source.mkdir()
            self.assertEqual(os.fstat(fd).st_ino,before.st_ino)
            self.assertNotEqual(os.stat(self.source).st_ino,before.st_ino)
        finally: os.close(fd)
    def test_descriptor_transport_as_owner(self):
        if os.getuid()==0: self.skipTest('owner transport is covered by privileged mount test')
        with patch.object(shares.pwd,'getpwuid',return_value=self.account):
            fd=shares.open_source(str(self.source),self.uid)
        try: self.assertEqual(os.fstat(fd).st_ino,self.source.stat().st_ino)
        finally: os.close(fd)
    def test_bad_input_and_no_implicit_write(self):
        for payload in [dict(name='../x',source=str(self.source),read_only=True),dict(name='X',source=str(self.source)),dict(name='X',source=str(self.source),read_only='false')]:
            with self.assertRaises(RuntimeError):
                shares.configure(self.instance,self.uid,payload)
        self.assertFalse((self.instance/shares.FILE).exists())
    def test_atomic_failure_keeps_previous(self):
        self.configure()
        before=(self.instance/shares.FILE).read_bytes()
        with patch.object(shares.os,'replace',side_effect=OSError('interrupted')), self.assertRaises(OSError):
            shares.remove(self.instance,self.uid,'Docs')
        self.assertEqual((self.instance/shares.FILE).read_bytes(),before)
        self.assertFalse(list(self.instance.glob('.shares-*')))
    def test_symlink_config_rejected(self):
        (self.instance/shares.FILE).symlink_to(self.source/'anything')
        with self.assertRaises(OSError): shares.list_shares(self.instance,self.uid)
    def test_host_namespace_rejected_before_open(self):
        with patch.object(shares,'open_source',side_effect=AssertionError('opened')), self.assertRaisesRegex(RuntimeError,'private runtime'):
            shares.apply_preboot(self.instance,self.root,self.uid,lambda path: None,original_mnt=os.readlink('/proc/self/ns/mnt'))


def mount_child(original_mnt):
    if os.geteuid() != 0: raise RuntimeError('--mount-test requires root in a new mount namespace')
    assert os.getpid()==1, 'test must mirror the runtime PID1 worker'
    assert os.readlink('/proc/1/ns/mnt')==os.readlink('/proc/self/ns/mnt')
    subprocess.run(['mount','--make-rprivate','/'],check=True)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory); root.chmod(0o755)
        home=root/'home'; home.mkdir(); home.chmod(0o755)
        source=home/'chosen'; source.mkdir(); os.chown(source,1000,1000)
        (source/'hello').write_text('hello')
        instance=root/'instance'; instance.mkdir()
        data=root/'data'; data.mkdir()
        account=types.SimpleNamespace(pw_dir=str(home),pw_gid=1000)
        mounted=[]
        with patch.object(shares.pwd,'getpwuid',return_value=account):
            shares.configure(instance,1000,dict(name='Docs',source=str(source),read_only=True))
            status=shares.apply_preboot(instance,data,1000,mounted.append,original_mnt=original_mnt)
            assert status['Docs']['mounted'],status
            target=data/'media/0/AnvilDroid/Docs'
            assert (target/'hello').read_text()=='hello'
            try: (target/'write').write_text('bad')
            except OSError as error: assert error.errno==30,error
            else: raise AssertionError('read-only mount accepted write')
            subprocess.run(['umount',mounted.pop()],check=True)
            shares.configure(instance,1000,dict(name='Docs',source=str(source),read_only=False))
            status=shares.apply_preboot(instance,data,1000,mounted.append,original_mnt=original_mnt)
            assert status['Docs']['mounted'],status
            (target/'write').write_text('ok')
            assert (source/'write').read_text()=='ok'
            subprocess.run(['umount',mounted.pop()],check=True)
            # A remount failure must undo the just-created bind.
            real_run = shares.subprocess.run
            def fail_remount(args, **kwargs):
                if args[0] == 'mount' and '-o' in args:
                    raise RuntimeError('injected remount failure')
                return real_run(args, **kwargs)
            with patch.object(shares.subprocess, 'run', side_effect=fail_remount):
                status=shares.apply_preboot(instance,data,1000,mounted.append,original_mnt=original_mnt)
            assert not status['Docs']['mounted'] and not (target/'hello').exists(),status
            assert not mounted
            # Nested mounts are refused rather than exposing underlying files.
            nested=source/'nested'; nested.mkdir()
            other=root/'other'; other.mkdir()
            subprocess.run(['mount','--bind',str(other),str(nested)],check=True)
            try:
                status=shares.apply_preboot(instance,data,1000,mounted.append,original_mnt=original_mnt)
                assert not status['Docs']['mounted'] and 'nested mounts' in status['Docs']['error'],status
            finally:
                subprocess.run(['umount',str(nested)],check=True)
            # Replacing the source cannot redirect the persisted directory grant.
            source.rename(home/'old'); source.mkdir(); os.chown(source,1000,1000)
            status=shares.apply_preboot(instance,data,1000,mounted.append,original_mnt=original_mnt)
            assert not status['Docs']['mounted'] and 'changed' in status['Docs']['error'],status
            # Guest-controlled target symlinks are rejected before mounting.
            shares.configure(instance,1000,dict(name='Docs',source=str(source),read_only=True))
            target.rmdir(); target.symlink_to(root)
            status=shares.apply_preboot(instance,data,1000,mounted.append,original_mnt=original_mnt)
            assert not status['Docs']['mounted'],status
    print('PASS isolated namespace: real RO/RW, rollback, nested-mount rejection, cleanup, source replacement, guest target symlink')

if __name__=='__main__':
    if '--mount-child' in sys.argv: mount_child(sys.argv[-1])
    elif '--mount-test' in sys.argv:
        subprocess.run(['unshare','--mount','--pid','--fork','--mount-proc',sys.executable,__file__,'--mount-child',os.readlink('/proc/self/ns/mnt')],check=True)
    else: unittest.main()
