#!/usr/bin/env python3
"""Host-control regressions. Never boots Android or writes a real SysRq file."""
import importlib.util
import errno
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


worker = module('isolated_worker', ROOT / 'services/runtime-worker.py')
policy = module('isolation_policy', ROOT / 'scripts/install-runtime-apparmor.py')


class HostIsolation(unittest.TestCase):
    def secure_fixture(self, root):
        for path in root.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)

    def test_sysrq_mask_is_mandatory_and_survives_writable_remount(self):
        entries = worker.host_control_mounts()
        self.assertEqual(len(entries), 1)
        fields = entries[0].split('=', 1)[1].split()
        self.assertEqual(fields[:4], ['/dev/null', 'proc/sysrq-trigger', 'none', 'bind,ro'])
        self.assertNotIn('optional', fields[3])
        self.assertTrue(stat.S_ISCHR(Path(fields[0]).stat().st_mode))
        self.assertEqual(Path(fields[0]).stat().st_rdev, os.makedev(1, 3))

    def test_upgrade_adds_deny_to_init_even_with_complain_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lxc').mkdir()
            (root / 'local').mkdir()
            (root / 'lxc/lxc-waydroid').write_bytes(
                b'profile lxc-waydroid flags=(complain) {\n#include <local/lxc-waydroid>\n}\n')
            (root / 'android_app').write_bytes(
                b'profile android_app {\n#include <local/android_app>\n}\n')
            previous = b'# User rules\n' + policy.RULE
            (root / 'local/android_app').write_bytes(previous)
            self.secure_fixture(root)
            run = Mock()
            policy.install(root, parser='/parser', run=run)
            for name in ('android_app', 'lxc-waydroid'):
                self.assertEqual((root / 'local' / name).read_bytes().count(policy.HOST_CONTROL_RULE), 1)
            self.assertTrue((root / 'local/android_app').read_bytes().startswith(previous))
            self.assertEqual(run.call_count, 4)
            policy.install(root, parser='/parser', run=run)
            self.assertEqual(run.call_count, 4)

    def test_init_policy_validation_failure_rolls_back_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lxc').mkdir()
            (root / 'local').mkdir()
            (root / 'lxc/lxc-waydroid').write_bytes(
                b'profile lxc-waydroid {\n#include <local/lxc-waydroid>\n}\n')
            override = root / 'local/lxc-waydroid'
            previous = b'# Keep existing policy\n'
            override.write_bytes(previous)
            self.secure_fixture(root)
            run = Mock(side_effect=subprocess.CalledProcessError(1, 'parser'))
            with self.assertRaises(subprocess.CalledProcessError):
                policy.install(root, parser='/parser', run=run)
            self.assertEqual(override.read_bytes(), previous)

    def test_reused_high_loop_devices_have_android_block_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysfs, destination = root / 'sys', root / 'block'
            for number in (0, 7, 22, 68, 257):
                entry = sysfs / ('loop' + str(number))
                entry.mkdir(parents=True)
                (entry / 'dev').write_text('7:' + str(number) + '\n')
            destination.mkdir()
            (destination / 'loop999').touch()  # Prior generation's stale node.
            def create(path, mode, device):
                self.assertEqual(mode, stat.S_IFBLK | 0o600)
                self.assertEqual(device, os.makedev(7, int(path.name[4:])))
                path.touch()  # Inert fixture, no host block devices opened.
            with patch.object(worker.os, 'mknod', side_effect=create):
                worker.prepare_loop_devices(destination, sysfs)
            self.assertEqual({p.name for p in destination.iterdir()},
                             {'loop0', 'loop7', 'loop22', 'loop68', 'loop257'})
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in destination.iterdir()))
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)

    def test_invalid_loop_identity_is_rejected_without_mknod(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / 'sys/loop22'
            entry.mkdir(parents=True)
            (entry / 'dev').write_text('8:0')
            with patch.object(worker.os, 'mknod') as create:
                with self.assertRaisesRegex(RuntimeError, 'Invalid loop device number'):
                    worker.prepare_loop_devices(root / 'block', root / 'sys')
                create.assert_not_called()

    def test_restart_cleans_android_block_aliases_without_following_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / 'block'
            (destination / 'vold').mkdir(parents=True)
            (destination / 'vold/stale').touch()
            outside = root / 'outside'
            outside.mkdir()
            sentinel = outside / 'keep'
            sentinel.write_text('unchanged')
            (destination / 'by-name').symlink_to(outside, target_is_directory=True)
            (destination / 'loop999').symlink_to(sentinel)
            (destination / 'dm-0').touch()
            worker.prepare_loop_devices(destination, root / 'empty-sys')
            self.assertEqual(list(destination.iterdir()), [])
            self.assertEqual(sentinel.read_text(), 'unchanged')

    def test_private_block_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'outside').mkdir()
            (root / 'block').symlink_to(root / 'outside', target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'Invalid private block directory'):
                worker.prepare_loop_devices(root / 'block', root / 'empty-sys')

    @unittest.skipUnless(os.environ.get('ANVILDROID_ISOLATION_MOUNTS') == '1',
                         'opt-in private mount namespace check')
    def test_mask_discards_commands_in_private_mount_namespace(self):
        self.assertEqual(os.geteuid(), 0)
        self.assertNotEqual(os.readlink('/proc/self/ns/mnt'), os.readlink('/proc/1/ns/mnt'))
        # Apply the production mount options to an INERT file under /tmp.
        # Never open or write the host's real /proc/sysrq-trigger, even on failure.
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'sysrq-trigger-fixture'
            target.write_text('untouched')
            fields = worker.host_control_mounts()[0].split('=', 1)[1].split()
            subprocess.run(['mount', '--bind', fields[0], str(target)], check=True)
            try:
                subprocess.run(['mount', '-o', 'remount,' + fields[3], str(target)], check=True)
                self.assertTrue(stat.S_ISCHR(target.stat().st_mode))
                self.assertEqual(target.stat().st_rdev, os.makedev(1, 3))
                # A read-only mount need not prohibit character-device I/O.
                # The replacement is /dev/null, so even allowed writes are
                # discarded rather than reaching the kernel SysRq handler.
                try:
                    with target.open('wb') as stream:
                        stream.write(b'ei')
                except OSError as error:
                    self.assertEqual(error.errno, errno.EROFS)
                self.assertEqual(target.read_bytes(), b'')
            finally:
                subprocess.run(['umount', str(target)], check=True)
            self.assertEqual(target.read_text(), 'untouched')


if __name__ == '__main__':
    unittest.main()
