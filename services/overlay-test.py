#!/usr/bin/env python3
"""Android OverlayFS policy and isolated real mounts; never starts Android."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('overlay_worker', Path(__file__).with_name('runtime-worker.py'))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class OverlayTests(unittest.TestCase):
    def test_android_policy_does_not_inherit_kernel_feature_defaults(self):
        options = worker.android_overlay_options('/patch:/base', '/upper', '/work').split(',')
        self.assertEqual(options[:3], ['lowerdir=/patch:/base', 'upperdir=/upper', 'workdir=/work'])
        for feature in ('xino', 'index', 'metacopy', 'redirect_dir', 'nfs_export'):
            self.assertEqual([option for option in options if option.startswith(feature + '=')], [feature + '=off'])

    def test_mount_uses_one_explicit_policy_for_the_selected_target(self):
        calls = []
        before = dict(os.environ)
        worker.mount_android_overlay('/patch:/base', '/upper', '/work', '/rootfs',
                                     lambda *args, **kwargs: calls.append((args, kwargs)))
        self.assertEqual(calls[0][0], ('-t', 'overlay', 'overlay', '-o',
                                      worker.android_overlay_options('/patch:/base', '/upper', '/work'), '/rootfs'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['env'], dict(before, LIBMOUNT_FORCE_MOUNT2='always'))
        self.assertEqual(dict(os.environ), before)

    def test_upper_feature_scan_never_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            upper = root / 'upper'; upper.mkdir()
            (upper / 'outside').symlink_to('/proc', target_is_directory=True)
            (upper / 'file-link').symlink_to('/proc/self/mem')
            self.assertEqual(worker.overlay_upper_features(upper), (False, False))
            with self.assertRaises(OSError): worker.overlay_upper_features(upper / 'outside')

    def test_failed_mount_retains_cause_and_never_retries_or_deletes_layers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            upper = root / 'upper'
            upper.mkdir()
            sentinel = upper / 'app-data'
            sentinel.write_text('preserve')
            calls = []
            def fail(*args, **kwargs):
                calls.append(args)
                raise RuntimeError('wrong fs type, bad option, bad superblock on overlay')
            with self.assertRaisesRegex(RuntimeError, 'Android OverlayFS mount failed at /rootfs') as caught:
                worker.mount_android_overlay('/patch:/base', upper, root / 'work', '/rootfs', fail)
            self.assertIn('wrong fs type', str(caught.exception))
            self.assertIn('sudo dmesg', str(caught.exception))
            self.assertIn('metacopy=off', str(caught.exception))
            self.assertEqual(len(calls), 1)
            self.assertEqual(sentinel.read_text(), 'preserve')


def mount_child():
    assert os.geteuid() == 0 and os.getpid() == 1, 'use the isolated PID1 test launcher'
    subprocess.run(['mount', '--make-rprivate', '/'], check=True)
    with tempfile.TemporaryDirectory(prefix='anvil-overlay-', dir='/tmp') as folder:
        root = Path(folder)
        # A private tmpfs makes the test independent of the checkout filesystem.
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'size=16m', 'tmpfs', str(root)], check=True)
        try:
            for name in ('patch', 'base', 'upper', 'work', 'merged'):
                (root / name).mkdir()
            (root / 'base/file').write_text('base')
            (root / 'base/base-only').write_text('keep')
            (root / 'base/old-directory').mkdir()
            (root / 'base/old-directory/child').write_text('preserved child')
            (root / 'patch/file').write_text('patch')
            lower = str(root / 'patch') + ':' + str(root / 'base')
            # Prefix explicitly enabled features to emulate kernels that enable
            # them by default. The Android policy must override all five.
            options = ('index=on,metacopy=on,redirect_dir=on,nfs_export=on,xino=on,' +
                       worker.android_overlay_options(lower, root / 'upper', root / 'work'))
            subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o', options, str(root / 'merged')], check=True)
            try:
                merged = root / 'merged'
                assert (merged / 'file').read_text() == 'patch'
                (merged / 'file').write_text('copy-up')
                (merged / 'base-only').unlink()
                (merged / 'new').write_text('new')
                assert (root / 'base/file').read_text() == 'base'
                assert (root / 'patch/file').read_text() == 'patch'
                assert (root / 'base/base-only').read_text() == 'keep'
                assert (root / 'upper/file').read_text() == 'copy-up'
                assert (root / 'upper/new').read_text() == 'new'
                info = Path('/proc/self/mountinfo').read_text()
                actual = next(line for line in info.splitlines() if line.split()[4] == str(merged))
                assert 'metacopy=on' not in actual and 'index=on' not in actual, actual
            finally:
                subprocess.run(['umount', str(root / 'merged')], check=True)
            worker.mount_android_overlay(lower, root / 'upper', root / 'work', root / 'merged',
                                         lambda *args, **kwargs: subprocess.run(['mount', *map(str, args)], check=True, **kwargs))
            try:
                assert (root / 'merged/file').read_text() == 'copy-up'
                assert not (root / 'merged/base-only').exists()
                assert (root / 'merged/new').read_text() == 'new'
            finally:
                subprocess.run(['umount', str(root / 'merged')], check=True)
            # Preserve a pre-policy upper created under a host's metacopy and
            # redirect defaults. Verify reads as well as mount success: kernels
            # can defer rejection of metadata-only inodes until open().
            for name in ('migration-upper', 'migration-work', 'migration-merged'):
                (root / name).mkdir()
            old_options = (f'lowerdir={root}/base,upperdir={root}/migration-upper,'
                           f'workdir={root}/migration-work,metacopy=on,index=off,'
                           'redirect_dir=on,nfs_export=off,xino=off')
            migration = root / 'migration-merged'
            subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o', old_options, str(migration)], check=True)
            try:
                (migration / 'file').chmod(0o600)
                (migration / 'old-directory').rename(migration / 'renamed-directory')
                assert 'trusted.overlay.metacopy' in os.listxattr(root / 'migration-upper/file')
                assert 'trusted.overlay.redirect' in os.listxattr(root / 'migration-upper/renamed-directory')
            finally:
                subprocess.run(['umount', str(migration)], check=True)
            new_options = worker.android_overlay_options(root / 'base', root / 'migration-upper', root / 'migration-work')
            assert 'metacopy=on' in new_options and 'redirect_dir=on' in new_options
            subprocess.run(['mount', '-t', 'overlay', 'overlay', '-o', new_options, str(migration)], check=True)
            try:
                assert (migration / 'file').read_bytes() == b'base'
                assert (migration / 'file').stat().st_mode & 0o777 == 0o600
                assert (migration / 'renamed-directory/child').read_text() == 'preserved child'
                assert not (migration / 'old-directory').exists()
            finally:
                subprocess.run(['umount', str(migration)], check=True)
            print('PASS migration: legacy metacopy upper retains contents and metadata', flush=True)
            # Real controller paths plus ARM commonly exceed fsconfig's
            # 255-character string limit even when every path is valid.
            long_root = root / ('runtime-' + 'x' * 88)
            long_root.mkdir()
            for name in ('arm', 'patch', 'base', 'upper', 'work', 'merged'):
                (long_root / name).mkdir()
            for name in ('arm', 'patch', 'base'):
                (long_root / name / 'file').write_text(name)
            (long_root / 'base/base-only').write_text('preserve lower')
            long_lower = ':'.join(str(long_root / name) for name in ('arm', 'patch', 'base'))
            assert len(long_lower.encode()) >= 256
            assert all(len(path.encode()) < 256 for path in long_lower.split(':'))
            merged = long_root / 'merged'
            for cycle in range(2):
                worker.mount_android_overlay(long_lower, long_root / 'upper', long_root / 'work', merged,
                    lambda *args, **kwargs: subprocess.run(['mount', *map(str, args)], check=True, **kwargs))
                try:
                    assert (merged / 'file').read_text() == ('arm' if cycle == 0 else 'copy-up')
                    if cycle == 0:
                        (merged / 'file').write_text('copy-up')
                        (merged / 'base-only').unlink()
                    else:
                        assert not (merged / 'base-only').exists()
                    assert (long_root / 'arm/file').read_text() == 'arm'
                    assert (long_root / 'base/base-only').read_text() == 'preserve lower'
                finally:
                    subprocess.run(['umount', str(merged)], check=True)
            print('PASS long lowerdir: ARM priority, copy-up, whiteout and remount persistence', flush=True)
        finally:
            subprocess.run(['umount', str(root)], check=True)
    print('PASS isolated Android overlay: explicit feature overrides, patch priority, copy-up, whiteout, remount persistence')


if __name__ == '__main__':
    if '--mount-child' in sys.argv:
        mount_child()
    elif '--mount-test' in sys.argv:
        subprocess.run(['unshare', '--mount', '--pid', '--fork', '--mount-proc', sys.executable,
                        __file__, '--mount-child'], check=True)
    else:
        unittest.main()
