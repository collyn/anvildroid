#!/usr/bin/env python3
"""Default grant/lifecycle fixtures; --mount-test uses a disposable namespace."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('default_shares', Path(__file__).with_name('runtime-default-shares.py'))
default = importlib.util.module_from_spec(spec); spec.loader.exec_module(default)
UID = 1000


class Runtime:
    def __init__(self, data):
        info = data.stat()
        self.metadata = {'owner_uid': UID, 'data_path': str(data), 'data_identity': {'device': info.st_dev, 'inode': info.st_ino}, 'data_owner_uid': info.st_uid}
        self.current = 'STOPPED'; self.session = {}
    def authorize(self, uid):
        if uid != UID: raise RuntimeError('owner mismatch')
        return self.session, self.metadata
    def state(self): return self.current
    def stopped(self):
        if self.current != 'STOPPED': raise RuntimeError('stop Default first')
    def saved(self): return self.metadata
    def verify_live_data(self, info): pass


class DefaultSharesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp'); self.root = Path(self.temp.name)
        self.store = self.root/'store'; self.store.mkdir(mode=0o700)
        self.state = self.root/'state'; self.state.mkdir(mode=0o700)
        self.data = self.root/'data'; self.data.mkdir()
        self.home = self.root/'home'; self.home.mkdir()
        self.source = self.home/'docs'; self.source.mkdir()
        self.runtime = Runtime(self.data)
        self.adapter = default.DefaultShares(self.store,self.runtime,self.state)
        self.uid_patch = patch.object(default,'TRUSTED_UID',os.getuid()); self.uid_patch.start()
        # The sandbox maps host root-owned ancestors to nobody; normalize only
        # that test-environment mapping, preserving all modes and file types.
        original_lstat=Path.lstat
        def mapped_lstat(path, *args, **kwargs):
            value=original_lstat(path,*args,**kwargs)
            if value.st_uid==65534:
                fields=list(value);fields[4]=0;return os.stat_result(fields)
            return value
        self.lstat_patch=patch.object(Path,'lstat',mapped_lstat);self.lstat_patch.start()
        self.marker_patch = patch.object(default,'integration_status',return_value={'pid':os.getpid(),'start_time':default.process_start(os.getpid()),'mount_namespace':os.readlink('/proc/self/ns/mnt')});self.marker_patch.start()
        self.source_patch = patch.object(default.shares,'open_source',side_effect=lambda path,uid:os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW));self.source_patch.start()
    def tearDown(self):
        self.source_patch.stop();self.marker_patch.stop();self.uid_patch.stop();self.lstat_patch.stop();self.temp.cleanup()
    def save(self, **changes):
        payload={'name':'Docs','source':str(self.source),'read_only':True};payload.update(changes)
        return self.adapter.set(UID,payload)
    def test_inert_read_and_atomic_configuration_remove(self):
        self.assertEqual(self.adapter.get(UID)['shares'],[])
        self.assertFalse((self.store/'default-shares').exists())
        report=self.save();self.assertTrue(report['editable']);self.assertFalse(report['shares'][0]['mounted'])
        self.assertEqual(report['runtime_id'],'default')
        directory=default.grant_directory(self.store,UID)
        self.assertEqual((directory/'data.json').stat().st_mode&0o777,0o600)
        self.assertEqual(self.adapter.remove(UID,'Docs')['shares'],[])
    def test_cross_user_denied_for_every_operation(self):
        for action in [lambda:self.adapter.get(UID+1),lambda:self.adapter.set(UID+1,{}),lambda:self.adapter.remove(UID+1,'Docs')]:
            with self.assertRaisesRegex(RuntimeError,'owner mismatch'):action()
    def test_old_or_missing_integration_keeps_configured_not_mounted(self):
        self.save()
        with patch.object(default,'integration_status',side_effect=RuntimeError('restart integration')):
            report=self.adapter.get(UID)
            self.assertFalse(report['editable']);self.assertFalse(report['integration_available']);self.assertFalse(report['shares'][0]['mounted'])
            self.assertIn('restart',report['unsupported_reason'])
            with self.assertRaises(RuntimeError):self.save()
            with self.assertRaises(RuntimeError):self.adapter.remove(UID,'Docs')
    def test_active_or_frozen_runtime_cannot_mutate(self):
        for state in ['RUNNING','FROZEN','STARTING']:
            self.runtime.current=state
            with self.assertRaises(RuntimeError):self.save()
            with self.assertRaises(RuntimeError):self.adapter.remove(UID,'Docs')
            self.assertFalse(self.adapter.get(UID)['editable'])
    def test_lifecycle_lock_serializes_boot_and_config(self):
        lock=default.lifecycle_lock(self.store)
        try:
            self.assertFalse(self.adapter.get(UID)['editable'])
            with self.assertRaises(BlockingIOError):self.save()
            with self.assertRaises(BlockingIOError):self.adapter.remove(UID,'Docs')
        finally:os.close(lock)
        self.assertTrue(self.adapter.get(UID)['editable'])
    def test_replaced_data_cannot_receive_grants(self):
        self.save();self.data.rename(self.root/'old-data');self.data.mkdir()
        with self.assertRaisesRegex(RuntimeError,'userdata changed'):self.save()
        self.assertEqual(self.adapter.remove(UID,'Docs')['shares'],[])
    def test_data_symlink_and_source_failure_leave_no_grant(self):
        self.data.rename(self.root/'old-data');self.data.symlink_to(self.root/'old-data')
        with self.assertRaises(OSError):self.save()
        self.assertEqual(self.adapter.get(UID)['shares'],[])
        self.data.unlink();(self.root/'old-data').rename(self.data)
        self.source.rmdir()
        with self.assertRaises(FileNotFoundError):self.save()
        self.assertEqual(self.adapter.get(UID)['shares'],[])
    def test_changed_management_binding_requires_removal(self):
        self.save();other=self.root/'other-data';other.mkdir();info=other.stat()
        self.runtime.metadata.update(data_path=str(other),data_identity={'device':info.st_dev,'inode':info.st_ino})
        with self.assertRaisesRegex(RuntimeError,'Remove previous'):self.save()
        self.adapter.remove(UID,'Docs');self.save()
    def test_old_marker_and_stale_pid_fail_closed(self):
        self.marker_patch.stop()
        for marker in [{'version':1,'pid':os.getpid()}, {'version':2,'sharing_version':1,'pid':999999999,'start_time':'1'}]:
            default.storage.atomic(self.state/'integration.json',marker)
            with self.assertRaises(RuntimeError):default.integration_status(self.state)
    def test_session_thread_and_namespace_guards(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        errors=[]
        def other():
            try:context.check_thread()
            except RuntimeError as error:errors.append(str(error))
        thread=threading.Thread(target=other);thread.start();thread.join()
        self.assertIn('owning service thread',errors[0])
        context.namespace='mnt:[0]'
        with self.assertRaisesRegex(RuntimeError,'namespace changed'):context.check_thread()
    def test_failed_stop_retains_namespace_and_lock(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        context.lock=default.lifecycle_lock(self.store)
        with self.assertRaisesRegex(RuntimeError,'still active'):context.close(False)
        self.assertTrue(context.active)
        with self.assertRaises(BlockingIOError):default.lifecycle_lock(self.store)
        context.close(True);self.assertFalse(context.active)
    def test_fuse_wait_never_claims_raw_share_is_app_visible(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        context.lock=default.lifecycle_lock(self.store)
        context.container_pid=os.getpid();context.fuse_deadline=default.time.monotonic()+300
        context.mounted={'Docs':{'mounted':False,'raw_mounted':True,'error':'Waiting for Android FUSE sharing readiness'}}
        context.session_record={'shares':context.mounted,'container_start_time':default.process_start(os.getpid())}
        with patch.object(default.shares,'_walk',side_effect=FileNotFoundError),patch.object(default.linux,'run_bounded') as run:
            context.reconcile()
            run.assert_not_called()
        self.assertFalse(context.mounted['Docs']['mounted'])
        context.fuse_deadline=0
        context.reconcile()
        self.assertIn('300 seconds',context.mounted['Docs']['error'])
        self.assertTrue(context.fuse_complete)
        os.close(context.lock);context.lock=None
    def test_empty_backend_directory_does_not_consume_retry_before_real_mount(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        context.lock=default.lifecycle_lock(self.store);context.container_pid=os.getpid()
        context.fuse_deadline=default.time.monotonic()+300
        context.mounted={'Docs':{'mounted':False,'error':'Waiting for Android FUSE sharing readiness'}}
        context.session_record={'shares':context.mounted,'container_start_time':default.process_start(os.getpid())}
        media=self.root/'media';media.mkdir();stub=self.root/'backend-stub';stub.mkdir()
        valid=False
        def walk(root,parts):
            path=media if parts==('data','media') or valid else stub
            return os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        with patch.object(default.shares,'_walk',side_effect=walk),patch('builtins.open',side_effect=lambda *a,**k:io.StringIO('1 2 0:1 / /mnt/pass_through/0/emulated rw - tmpfs tmpfs rw\n')),patch.object(default.linux,'run_bounded',return_value='{"Docs":{"mounted":true,"error":null}}') as run:
            context.reconcile();context.reconcile();context.reconcile();context.reconcile()
            self.assertEqual(context.fuse_attempts,0);self.assertFalse(context.fuse_complete);run.assert_not_called()
            valid=True
            context.reconcile()
            self.assertEqual(context.fuse_attempts,1);self.assertTrue(context.fuse_complete)
            self.assertTrue(context.mounted['Docs']['mounted']);run.assert_called_once()
        os.close(context.lock);context.lock=None
    def test_failed_namespace_open_releases_startup_lock(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        original_open=os.open
        def fail_namespace(path,*args,**kwargs):
            if str(path)=='/proc/self/ns/mnt':raise OSError('descriptor limit')
            return original_open(path,*args,**kwargs)
        with patch.object(default.os,'open',side_effect=fail_namespace):
            with self.assertRaisesRegex(OSError,'descriptor limit'):context.begin({})
        self.assertFalse(context.active)
        os.close(default.lifecycle_lock(self.store))
    def test_surviving_child_or_thread_retains_namespace_and_lock(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        context.lock=default.lifecycle_lock(self.store)
        with patch.object(context,'namespace_holders',return_value=['pid:123']),patch.object(default.time,'monotonic',side_effect=[0,4]),self.assertRaisesRegex(RuntimeError,'helpers still retain'):
            context.close(True)
        self.assertTrue(context.active)
        with self.assertRaises(BlockingIOError):default.lifecycle_lock(self.store)
        context.close(True)
    def test_inaccessible_process_retains_namespace_and_lock(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        context.lock=default.lifecycle_lock(self.store)
        context.namespace=os.readlink('/proc/self/ns/mnt')
        original_readlink=os.readlink
        def readlink(path):
            if str(path)=='/proc/123/ns/mnt':raise PermissionError('ptrace denied')
            return original_readlink(path)
        with patch.object(default.Path,'iterdir',return_value=iter([Path('/proc/123')])),patch.object(default.os,'readlink',side_effect=readlink),self.assertRaisesRegex(RuntimeError,'Cannot inspect mount namespace for pid 123'):
            context.close(True)
        self.assertTrue(context.active)
        with self.assertRaises(BlockingIOError):default.lifecycle_lock(self.store)
        context.namespace=None
        context.close(True)
    def test_config_rewrite_uses_only_private_path(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        path=self.root/'config_session';path.write_text('lxc.mount.entry = /home/user/raw\\040path data none rbind 0 0\nother = preserved\n');path.chmod(0o600)
        context.rewrite_config(path,self.state/'private-data')
        self.assertNotIn('/home/user',path.read_text());self.assertIn('other = preserved',path.read_text())
        self.assertIn(str(self.state/'private-data'),path.read_text())
        for content in ['', 'lxc.mount.entry = /a data none bind 0 0\nlxc.mount.entry = /b data none bind 0 0']:
            path.write_text(content)
            with self.assertRaises(RuntimeError):context.rewrite_config(path,self.state/'private-data')
    def test_symlink_and_fifo_configuration_rejected(self):
        context=default.SessionMounts(self.store,self.runtime,self.state,'mnt:[1]')
        path=self.root/'config_session';other=self.root/'other';other.write_text('safe')
        path.symlink_to(other)
        with self.assertRaises(OSError):context.rewrite_config(path,self.state/'data')
        path.unlink();os.mkfifo(path)
        with self.assertRaises(RuntimeError):context.rewrite_config(path,self.state/'data')
    def test_target_walk_never_follows_guest_symlinks(self):
        (self.data/'media').symlink_to(self.root)
        fd=os.open(self.data,os.O_RDONLY|os.O_DIRECTORY)
        try:
            with self.assertRaises(OSError):default.walk_target(fd,'Docs',create=True)
        finally:os.close(fd)



class DefaultSystemHookTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('default_shares_system',Path(__file__).with_name('runtime-system.py'))
        self.system=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.system)
        self.tools=types.SimpleNamespace(actions=types.SimpleNamespace(container_manager=types.SimpleNamespace(
            do_start=Mock(),freeze=Mock(),stop=Mock())),helpers=types.SimpleNamespace(
            images=types.SimpleNamespace(mount_rootfs=Mock(),make_prop=Mock()),lxc=types.SimpleNamespace(status=Mock(return_value='STOPPED'))),
            config=types.SimpleNamespace(load=Mock(return_value={'waydroid':{'drm_device':'auto'}}),defaults={'lxc':'/fixture/lxc'}))
        self.integration=self.system.Integration(self.tools,Mock())
        self.integration.sharing=Mock(active=True)
        self.integration.args=types.SimpleNamespace(hardware_manager=None)
    def test_mount_rejects_foreign_thread_before_stock_mount(self):
        self.integration.sharing.check_thread.side_effect=RuntimeError('owning service thread')
        with self.assertRaises(RuntimeError):self.integration.mount(None,None,None)
        self.tools.helpers.images.mount_rootfs.assert_not_called()
    def test_bounded_hardware_join_precedes_namespace_disposal(self):
        helper=Mock();helper.is_alive.side_effect=[True,False]
        self.integration.args.hardware_manager=helper
        self.integration.close()
        helper.join.assert_called_once_with(timeout=3)
        self.integration.sharing.close.assert_called_once_with(True)
    def test_live_hardware_or_guest_retains_namespace(self):
        helper=Mock();helper.is_alive.return_value=True
        self.integration.args.hardware_manager=helper
        with self.assertRaisesRegex(RuntimeError,'callbacks did not stop'):self.integration.close()
        self.integration.sharing.close.assert_not_called()
        self.integration.args.hardware_manager=None
        self.tools.helpers.lxc.status.return_value='RUNNING'
        with self.assertRaisesRegex(RuntimeError,'still active'):self.integration.close()
        self.integration.sharing.close.assert_not_called()
    def test_stop_failure_retains_active_guest_namespace(self):
        self.integration.original_stop.side_effect=RuntimeError('stop failed')
        self.tools.helpers.lxc.status.return_value='RUNNING'
        with self.assertRaises(RuntimeError):self.integration.stop(self.integration.args)
        self.integration.sharing.close.assert_not_called()
    def test_duplicate_start_never_replaces_session_namespace(self):
        self.integration.start({'session':{}},{})
        self.integration.sharing.begin.assert_not_called()
        self.integration.original_stop.assert_not_called()
    def test_untracked_live_container_is_not_stopped_by_start(self):
        self.tools.helpers.lxc.status.return_value='RUNNING'
        with patch.object(self.system.gpu,'is_nvidia',return_value=False),self.assertRaises(RuntimeError):
            self.integration.start({}, {'user_id':'1000'})
        self.integration.original_stop.assert_not_called()
        self.integration.sharing.begin.assert_not_called()
    def test_failed_start_stops_then_discards_session(self):
        runtime=Mock()
        module=types.SimpleNamespace(ExistingRuntime=Mock(return_value=runtime))
        self.integration.original_start.side_effect=RuntimeError('boot failed')
        with patch.object(self.system,'module',return_value=module),patch.object(self.system.gpu,'is_nvidia',return_value=False),self.assertRaisesRegex(RuntimeError,'boot failed'):
            self.integration.start({}, {'user_id':'1000'})
        self.integration.sharing.begin.assert_called_once()
        self.integration.original_stop.assert_called_once()
        self.integration.sharing.close.assert_called_once_with(True)
    def test_mesa_watchdog_cleans_unexpected_container_stop(self):
        self.assertFalse(self.integration.watch())
        self.integration.original_stop.assert_called_once()
        self.integration.sharing.close.assert_called_once_with(True)


def mount_child(original):
    assert os.geteuid()==0
    subprocess.run(['mount','--make-rprivate','/'],check=True)
    with tempfile.TemporaryDirectory(prefix='anvil-default-share-',dir='/tmp') as directory:
        root=Path(directory);root.chmod(0o755)
        store=root/'store';store.mkdir(mode=0o700)
        state=root/'state';state.mkdir(mode=0o700)
        data=root/'data';data.mkdir();os.chown(data,UID,UID)
        home=root/'home';home.mkdir();source=home/'docs';source.mkdir();os.chown(source,UID,UID)
        (source/'hello').write_text('hello')
        runtime=Runtime(data);session={'user_id':str(UID),'waydroid_data':str(data),'pid':'123'}
        adapter=default.DefaultShares(store,runtime,state)
        account=types.SimpleNamespace(pw_dir=str(home),pw_gid=UID)
        with patch.object(default,'integration_status',return_value={}),patch.object(default.shares.pwd,'getpwuid',return_value=account):
            adapter.set(UID,{'name':'Docs','source':str(source),'read_only':True})
            config=root/'config_session';config.write_text(f'lxc.mount.entry = {data} data none rbind 0 0\n');config.chmod(0o600)
            context=default.SessionMounts(store,runtime,state,original)
            baseline=os.readlink('/proc/self/ns/mnt')
            context.begin(session);context.prepare(config)
            assert context.mounted['Docs']['mounted'],context.mounted
            target=context.stage/'data/media/0/AnvilDroid/Docs'
            assert (target/'hello').read_text()=='hello'
            try:(target/'write').write_text('denied')
            except OSError as error:assert error.errno==30,error
            else:raise AssertionError('RO grant accepted write')
            # An ordinary recursive userdata bind inherits the grant, as LXC does.
            inherited=context.stage/'guest-data';inherited.mkdir()
            subprocess.run(['mount','--rbind',str(context.stage/'data'),str(inherited)],check=True)
            assert (inherited/'media/0/AnvilDroid/Docs/hello').read_text()=='hello'
            assert os.statvfs(inherited/'media/0/AnvilDroid/Docs').f_flag & os.ST_RDONLY
            with unittest.TestCase().assertRaises(BlockingIOError):adapter.remove(UID,'Docs')
            read_fd,write_fd=os.pipe();ready_read,ready_write=os.pipe()
            child=os.fork()
            if child==0:
                os.close(write_fd);os.close(ready_read)
                os.unshare(os.CLONE_NEWNS)
                subprocess.run(['mount','--make-rprivate','/'],check=True)
                backend=context.stage/'mnt/pass_through/0/emulated';backend.mkdir(parents=True)
                subprocess.run(['mount','--bind',str(context.stage/'data/media'),str(backend)],check=True)
                os.chroot(context.stage);os.chdir('/')
                os.write(ready_write,b'1');os.close(ready_write)
                os.read(read_fd,1);os._exit(0)
            os.close(read_fd);os.close(ready_write)
            assert os.read(ready_read,1)==b'1';os.close(ready_read)
            try:
                # /data in the synthetic guest is the pinned recursive bind.
                context.started(child)
                runtime.current='RUNNING';runtime.session=session
                marker={'pid':os.getpid(),'start_time':default.process_start(os.getpid()),'mount_namespace':context.namespace}
                default.storage.atomic(state/'integration.json',marker)
                with patch.object(default,'integration_status',return_value=marker):
                    pending=adapter.get(UID)
                assert not pending['shares'][0]['mounted'] and 'Waiting for Android FUSE' in pending['shares'][0]['error'],pending
                # Fixture service is this test process, so only its stock
                # command-line inventory check is synthesized. Session files,
                # PID/starttime, namespace, ownership and mounts remain real.
                code = ("import importlib.util,json,pathlib; p=pathlib.Path("+repr(str(Path(default.__file__)))+"); s=importlib.util.spec_from_file_location('d',p);d=importlib.util.module_from_spec(s);s.loader.exec_module(d); state=pathlib.Path("+repr(str(state))+"); d.integration_status=lambda state:d.read_json(state/'integration.json'); print(json.dumps(d.reconcile_session(state)))")
                result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True,timeout=5)
                context.mounted=json.loads(result.stdout);context.session_record['shares']=context.mounted
                default.storage.atomic(state/'shares-session.json',context.session_record)
                with patch.object(default,'integration_status',return_value=marker):
                    live=adapter.get(UID)
                assert live['shares'][0]['mounted'] and not live['editable'],live
            finally:
                runtime.current='STOPPED';runtime.session={}
                os.close(write_fd);os.waitpid(child,0)
            context.close(True)
            assert os.readlink('/proc/self/ns/mnt')==baseline
            assert not (data/'media/0/AnvilDroid/Docs/hello').exists()
            assert not list(state.glob('shares-*'))
            # RW is enforced on a fresh subsequent ordinary service session.
            adapter.set(UID,{'name':'Docs','source':str(source),'read_only':False})
            context.begin(session);context.prepare(config)
            target=context.stage/'data/media/0/AnvilDroid/Docs'
            (target/'write').write_text('ok');assert (source/'write').read_text()=='ok'
            context.close(True)
            # Desktop-owned guest path replacement fails without root path follow.
            target=data/'media/0/AnvilDroid/Docs';target.rmdir();target.symlink_to(root)
            context.begin(session);context.prepare(config)
            assert not context.mounted['Docs']['mounted'];assert (root/'store').is_dir()
            context.close(True)
            # Stale source identity is an error, never a new directory grant.
            target.unlink();target.mkdir();source.rename(home/'old');source.mkdir();os.chown(source,UID,UID)
            context.begin(session);context.prepare(config)
            assert not context.mounted['Docs']['mounted']
            context.close(True)
    print('PASS Default private session: RO/RW, recursive userdata inheritance, lifecycle lock, namespace revocation, guest symlink, stale source')


if __name__=='__main__':
    if '--mount-child' in sys.argv:mount_child(sys.argv[-1])
    elif '--mount-test' in sys.argv:
        subprocess.run(['unshare','--mount',sys.executable,__file__,'--mount-child',os.readlink('/proc/self/ns/mnt')],check=True)
    else:unittest.main()
