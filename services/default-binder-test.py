#!/usr/bin/env python3
"""Host Binder preparation ordering/context tests; inert unless --mount-test."""
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('binder_system', Path(__file__).with_name('runtime-system.py'))
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


class HostBinderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dev = Path(temporary.name) / 'dev'; self.dev.mkdir()
        self.fs = self.dev / 'binderfs'; self.fs.mkdir()
        self.names = ['anbox-binder', 'anbox-vndbinder', 'anbox-hwbinder']
        self.cfg = dict(zip(('binder', 'vndbinder', 'hwbinder'), self.names))
        self.tools = SimpleNamespace(config=SimpleNamespace(load=lambda args: {'waydroid': self.cfg}, defaults={'lxc': '/fixture/lxc'}),
                                     helpers=SimpleNamespace(drivers=SimpleNamespace(allocBinderNodes=Mock(side_effect=self.allocate))))
        for attribute, name, alternative in zip(('BINDER_DRIVERS', 'VNDBINDER_DRIVERS', 'HWBINDER_DRIVERS'), self.names,
                                               ('binder', 'vndbinder', 'hwbinder')):
            setattr(self.tools.helpers.drivers, attribute, [name, alternative])
        self.characters = set()
        original = Path.stat
        def fake_stat(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if str(path.resolve()) in self.characters:
                values = list(value); values[0] = stat.S_IFCHR | 0o666
                return os.stat_result(values)
            return value
        p = patch.object(Path, 'stat', fake_stat); p.start(); self.addCleanup(p.stop)
        p = patch.object(system.existing.linux, 'run_bounded', return_value='STOPPED'); self.run = p.start(); self.addCleanup(p.stop)
        p = patch('builtins.open', side_effect=lambda *a, **k: io.StringIO(f'1 2 0:1 / {self.fs} rw - binder binder rw\n'))
        p.start(); self.addCleanup(p.stop)
    def node(self, path):
        path.touch(); self.characters.add(str(path.resolve()))
    def allocate(self, args, names):
        for name in names: self.node(self.fs / name)
    def prepare(self): system.prepare_host_binder(self.tools, SimpleNamespace(), self.dev)
    def test_existing_context_never_probed_or_remounted_even_while_running(self):
        for name in self.names:
            self.node(self.fs / name); (self.dev / name).symlink_to(self.fs / name)
        before = [(self.fs / n).stat().st_ino for n in self.names]
        self.run.return_value = 'RUNNING'; self.prepare()
        self.run.assert_not_called(); self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
        self.assertEqual(before, [(self.fs / n).stat().st_ino for n in self.names])
    def test_missing_only_allocated_in_existing_fs_without_mounting(self):
        self.node(self.fs / self.names[0]); (self.dev / self.names[0]).symlink_to(self.fs / self.names[0])
        (self.dev / self.names[1]).symlink_to(self.fs / self.names[1])
        first = (self.fs / self.names[0]).stat().st_ino
        self.prepare()
        self.tools.helpers.drivers.allocBinderNodes.assert_called_once_with(unittest.mock.ANY, self.names[1:])
        self.assertEqual(self.run.call_count, 1)
        self.assertEqual(first, (self.fs / self.names[0]).stat().st_ino)
        self.assertTrue(all((self.dev / n).is_symlink() for n in self.names))
    def test_existing_sources_need_only_aliases(self):
        for name in self.names: self.node(self.fs / name)
        self.prepare(); self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
        self.assertEqual(self.run.call_count, 1)
    def test_missing_nodes_refused_while_running_frozen_or_unknown(self):
        for status in ('RUNNING', 'FROZEN', '', 'BROKEN'):
            with self.subTest(status=status):
                self.run.return_value = status
                with self.assertRaisesRegex(RuntimeError, 'Stop Default'): self.prepare()
        self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
        self.assertEqual(list(self.fs.iterdir()), [])
    def test_status_failure_does_not_allocate(self):
        self.run.side_effect = RuntimeError('status unavailable')
        with self.assertRaisesRegex(RuntimeError, 'status unavailable'): self.prepare()
        self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
    def test_unrelated_existing_target_is_not_replaced(self):
        target = self.dev / self.names[0]; target.write_text('preserve')
        with self.assertRaisesRegex(RuntimeError, 'replace existing'): self.prepare()
        self.assertEqual(target.read_text(), 'preserve'); self.run.assert_not_called()
    def test_unrelated_symlink_is_not_replaced(self):
        target = self.dev / self.names[0]; target.symlink_to(self.dev / 'elsewhere')
        with self.assertRaisesRegex(RuntimeError, 'replace existing'): self.prepare()
        self.assertEqual(os.readlink(target), str(self.dev / 'elsewhere'))
    def test_unrelated_mount_is_not_replaced(self):
        with patch('builtins.open', return_value=io.StringIO(f'1 2 0:1 / {self.fs} rw - tmpfs tmpfs rw\n')):
            with self.assertRaisesRegex(RuntimeError, 'unrelated mount'): self.prepare()
        self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
    def test_unrelated_source_is_not_replaced(self):
        source = self.fs / self.names[0]; source.write_text('preserve')
        with self.assertRaisesRegex(RuntimeError, 'Invalid existing'): self.prepare()
        self.assertEqual(source.read_text(), 'preserve')
        self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
    def test_failed_allocation_never_creates_aliases_or_reports_success(self):
        self.tools.helpers.drivers.allocBinderNodes.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'did not create'): self.prepare()
        self.assertFalse(any((self.dev / name).is_symlink() for name in self.names))
    def test_nonempty_unmounted_directory_is_not_hidden(self):
        (self.fs / 'unrelated').touch()
        with patch('builtins.open', return_value=io.StringIO('')):
            with self.assertRaisesRegex(RuntimeError, 'must be empty'): self.prepare()
        self.assertEqual(self.run.call_count, 1)
    def test_empty_unmounted_directory_mounted_once_before_allocation(self):
        with patch('builtins.open', return_value=io.StringIO('')): self.prepare()
        commands = [call.args[:4] for call in self.run.call_args_list]
        self.assertEqual(commands[1], ('modprobe', 'binder_linux'))
        self.assertEqual(commands[2], ('mount', '-t', 'binder', 'binder'))
    def test_bad_configured_names_fail_before_actions(self):
        for bad in ('../binder', '/dev/binder', 'a/b', 'binder-control', '', None):
            with self.subTest(name=bad):
                self.cfg['binder'] = bad
                with self.assertRaisesRegex(RuntimeError, 'Invalid configured'): self.prepare()
        self.cfg['binder'] = self.cfg['vndbinder']
        with self.assertRaisesRegex(RuntimeError, 'Invalid configured'): self.prepare()
        self.run.assert_not_called()
    def test_first_initialization_prepares_host_defaults_before_names_saved(self):
        self.cfg.clear(); self.prepare()
        self.tools.helpers.drivers.allocBinderNodes.assert_called_once_with(unittest.mock.ANY, self.names)
        self.assertTrue(all((self.dev / n).is_symlink() for n in self.names))
        self.assertIn('--rcfile=/dev/null', self.run.call_args.args)
        # Later initialization saves the names, and subsequent prep is inert.
        self.cfg.update(dict(zip(('binder', 'vndbinder', 'hwbinder'), self.names)))
        self.run.reset_mock(); self.prepare(); self.run.assert_not_called()
    def test_first_initialization_reuses_existing_supported_contexts(self):
        self.cfg.clear()
        for name in ('binder', 'vndbinder', 'hwbinder'): self.node(self.dev / name)
        self.prepare(); self.run.assert_not_called()
        self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
    def test_first_initialization_does_not_create_context_while_running(self):
        self.cfg.clear(); self.run.return_value = 'RUNNING'
        with self.assertRaisesRegex(RuntimeError, 'Stop Default'): self.prepare()
        self.tools.helpers.drivers.allocBinderNodes.assert_not_called()
    def test_parent_prepares_before_unshare_without_stock_import(self):
        order = []
        self.run.side_effect = lambda *a, **k: order.append('prepare')
        with patch.object(system.os, 'geteuid', return_value=0), patch.object(system.os, 'unshare', side_effect=lambda *a: (_ for _ in ()).throw(RuntimeError('stop at unshare'))):
            with self.assertRaisesRegex(RuntimeError, 'stop at unshare'): system.main()
        self.assertEqual(order, ['prepare'])
        self.assertEqual(self.run.call_args.args[2], '--prepare-host-binder')


def mount_child():
    """Only called inside a new mount namespace; no host Binder changes."""
    assert os.geteuid() == 0
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    launcher = Path('/usr/bin/waydroid').resolve(strict=True)
    sys.path.insert(0, str(launcher.parent))
    import tools
    # Imports finish before /dev is hidden. Binder context below is disposable.
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', '/dev'], check=True)
    os.mknod('/dev/null', stat.S_IFCHR | 0o666, os.makedev(1, 3))
    subprocess.run(['mount', '-t', 'tmpfs', 'tmpfs', '/run'], check=True)
    Path('/dev/binderfs').mkdir()
    subprocess.run(['mount', '-t', 'binder', 'binder', '/dev/binderfs'], check=True)
    temporary = tempfile.TemporaryDirectory(prefix='anvil-binder-first-init-')
    root = Path(temporary.name)
    args = SimpleNamespace(); tools.prep_args(args)
    args.work = str(root); args.config = str(root / 'waydroid.cfg'); args.log = str(root / 'waydroid.log')
    adapter = SimpleNamespace(config=SimpleNamespace(load=tools.config.load, defaults={'lxc': str(root / 'lxc')}), helpers=tools.helpers)
    # Real stock prep_args + cold config load and real LXC STOPPED query, with
    # no saved configuration and no preexisting Binder nodes in private /dev.
    system.prepare_host_binder(adapter, args)
    names = [group[0] for group in (tools.helpers.drivers.BINDER_DRIVERS,
             tools.helpers.drivers.VNDBINDER_DRIVERS, tools.helpers.drivers.HWBINDER_DRIVERS)]
    identity = [(Path('/dev') / n).stat() for n in names]
    args.vendor_type = 'MAINLINE'
    tools.helpers.drivers.setupBinderNodes(args)
    assert [args.BINDER_DRIVER, args.VNDBINDER_DRIVER, args.HWBINDER_DRIVER] == names
    for name, before in zip(names, identity):
        after = (Path('/dev') / name).stat()
        assert (before.st_dev, before.st_ino, before.st_rdev) == (after.st_dev, after.st_ino, after.st_rdev)
    Path(args.config).write_text('[waydroid]\n' + ''.join(key + '=' + name + '\n' for key, name in zip(('binder', 'vndbinder', 'hwbinder'), names)))
    pid = os.fork()
    if pid == 0:
        try:
            os.unshare(os.CLONE_NEWNS)
            subprocess.run(['mount', '--make-rprivate', '/'], check=True)
            # Idempotent prep cannot create/rebind an inherited running context.
            with patch.object(system.existing.linux, 'run_bounded', side_effect=AssertionError('must not probe or mount')):
                system.prepare_host_binder(adapter, args)
            for name, before in zip(names, identity):
                after = (Path('/dev') / name).stat()
                assert stat.S_ISCHR(after.st_mode)
                assert (before.st_dev, before.st_ino, before.st_rdev) == (after.st_dev, after.st_ino, after.st_rdev)
        except BaseException:
            import traceback; traceback.print_exc(); os._exit(1)
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0
    temporary.cleanup()
    print('PASS stock cold initialization and identical Binder context across private service namespace')


if __name__ == '__main__':
    if sys.argv[1:] == ['--mount-test']:
        subprocess.run(['unshare', '--mount', sys.executable, __file__, '--mount-child'], check=True, timeout=20)
    elif sys.argv[1:] == ['--mount-child']: mount_child()
    else: unittest.main()
