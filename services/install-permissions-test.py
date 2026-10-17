#!/usr/bin/env python3
"""Regression checks for the legacy Debian 0775 controller installation."""
from contextlib import contextmanager
import os
from pathlib import Path
import runpy
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
secure = runpy.run_path(str(ROOT / 'packaging/deb/preinst.py'))['secure_install_directory']
x11 = runpy.run_path(str(ROOT / 'services/runtime-x11.py'))


class InstallationPermissions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-install-permissions-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = self.root / 'lib'
        self.parent.mkdir(mode=0o755)
        self.destination = self.parent / 'anvildroid-controller'
        self.destination.mkdir(mode=0o755)

    @contextmanager
    def root_metadata(self, user_owned=None):
        # Real directories, openat/O_NOFOLLOW and fchmod; only ownership and
        # ancestors outside the fixture are modeled so this also runs in CI
        # without root and in a UID-mapped filesystem sandbox.
        original = os.fstat
        user_identity = None
        if user_owned is not None:
            info = user_owned.stat()
            user_identity = info.st_dev, info.st_ino
        identities = {(p.stat().st_dev, p.stat().st_ino)
                      for p in (self.root, self.parent, self.destination, *self.destination.rglob('*'))
                      if p.exists()}

        def metadata(fd):
            info = original(fd)
            values = list(info)
            identity = info.st_dev, info.st_ino
            values[4] = 1000 if identity == user_identity else 0
            if identity not in identities:
                values[0] &= ~0o022
            return os.stat_result(values)

        with patch.object(os, 'fstat', side_effect=metadata):
            yield

    def test_legacy_0775_directory_is_repaired_without_changing_assets(self):
        asset = self.destination / 'sentinel'
        asset.write_bytes(b'keep')
        asset.chmod(0o644)
        before = asset.stat()
        self.destination.chmod(0o775)
        with self.root_metadata():
            secure(self.destination)
            secure(self.destination)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o755)
        self.assertEqual(asset.read_bytes(), b'keep')
        after = asset.stat()
        self.assertEqual((after.st_ino, after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns),
                         (before.st_ino, before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns))
        self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), 0o755)

    def test_secure_installation_is_unchanged(self):
        with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
            secure(self.destination)
        chmod.assert_not_called()

    def test_nested_legacy_directories_are_repaired_and_assets_preserved(self):
        nested = self.destination / 'provision' / 'vendor' / 'lib64'
        nested.mkdir(parents=True)
        script = self.destination / 'services'
        script.mkdir()
        asset = nested / 'libprobe.so'
        asset.write_bytes(b'unchanged')
        asset.chmod(0o644)
        before = asset.stat()
        for directory in (self.destination, nested.parent.parent, nested.parent, nested, script):
            directory.chmod(0o775)
        with self.root_metadata():
            secure(self.destination)
            secure(self.destination)
        for directory in (self.destination, nested.parent.parent, nested.parent, nested, script):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
        after = asset.stat()
        self.assertEqual((before.st_ino, before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns),
                         (after.st_ino, after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns))
        self.assertEqual(asset.read_bytes(), b'unchanged')

    def test_nested_unsafe_ownership_and_modes_fail_without_adoption(self):
        nested = self.destination / 'services'
        nested.mkdir()
        for mode, owner in ((0o777, None), (0o2775, None), (0o775, nested)):
            with self.subTest(mode=oct(mode), owner=owner):
                nested.chmod(mode)
                with self.root_metadata(user_owned=owner), patch.object(os, 'fchmod') as chmod:
                    with self.assertRaisesRegex(RuntimeError, str(nested)):
                        secure(self.destination)
                chmod.assert_not_called()
                self.assertEqual(stat.S_IMODE(nested.stat().st_mode), mode)

    def test_only_two_known_legacy_0664_resources_are_repaired_without_content_changes(self):
        resources = [self.destination / 'provision/AndroidManifest.xml',
                     self.destination / 'provision/res/layout/decor_caption.xml']
        before = {}
        for resource in resources:
            resource.parent.mkdir(parents=True, exist_ok=True)
            resource.write_bytes(b'legacy resource bytes')
            resource.chmod(0o664)
            before[resource] = resource.stat()
        with self.root_metadata():
            secure(self.destination)
            secure(self.destination)
        for resource in resources:
            after = resource.stat()
            prior = before[resource]
            self.assertEqual(stat.S_IMODE(after.st_mode), 0o644)
            self.assertEqual((prior.st_ino, prior.st_uid, prior.st_gid, prior.st_mtime_ns),
                             (after.st_ino, after.st_uid, after.st_gid, after.st_mtime_ns))
            self.assertEqual(resource.read_bytes(), b'legacy resource bytes')

    def test_known_legacy_resource_exception_rejects_unsafe_modes_and_owner(self):
        resource = self.destination / 'provision/AndroidManifest.xml'
        resource.parent.mkdir(mode=0o755)
        resource.write_bytes(b'keep')
        for mode, owner in ((0o666, None), (0o664, resource)):
            with self.subTest(mode=oct(mode), owner=owner):
                resource.chmod(mode)
                with self.root_metadata(user_owned=owner), patch.object(os, 'fchmod') as chmod:
                    with self.assertRaisesRegex(RuntimeError, str(resource)):
                        secure(self.destination)
                chmod.assert_not_called()

    def test_known_legacy_resource_hardlink_cannot_change_external_inode(self):
        external = self.root / 'external-resource'
        external.write_bytes(b'external must remain unchanged')
        external.chmod(0o664)
        resource = self.destination / 'provision/AndroidManifest.xml'
        resource.parent.mkdir(mode=0o755)
        os.link(external, resource)
        with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
            with self.assertRaisesRegex(RuntimeError, 'hard-linked legacy controller resource'):
                secure(self.destination)
        chmod.assert_not_called()
        self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o664)
        self.assertEqual(external.read_bytes(), b'external must remain unchanged')

    def test_nested_links_cannot_change_external_targets(self):
        external = self.root / 'external'
        external.mkdir(mode=0o775)
        external.chmod(0o775)
        asset = external / 'keep'
        asset.write_bytes(b'keep')
        (self.destination / 'services').symlink_to(external, target_is_directory=True)
        with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
            with self.assertRaisesRegex(RuntimeError, str(self.destination / 'services')):
                secure(self.destination)
        chmod.assert_not_called()
        self.assertEqual(stat.S_IMODE(external.stat().st_mode), 0o775)
        self.assertEqual(asset.read_bytes(), b'keep')

    def test_writable_user_owned_or_linked_assets_are_rejected(self):
        asset = self.destination / 'asset'
        asset.write_bytes(b'keep')
        for mode, owner in ((0o664, None), (0o644, asset)):
            with self.subTest(mode=oct(mode), owner=owner):
                asset.chmod(mode)
                with self.root_metadata(user_owned=owner), patch.object(os, 'fchmod') as chmod:
                    with self.assertRaisesRegex(RuntimeError, str(asset)):
                        secure(self.destination)
                chmod.assert_not_called()
        asset.unlink()
        asset.symlink_to(self.root / 'absent')
        with self.root_metadata(), self.assertRaisesRegex(RuntimeError, str(asset)):
            secure(self.destination)

    def test_missing_directory_is_left_for_dpkg(self):
        self.destination.rmdir()
        with self.root_metadata():
            secure(self.destination)
        self.assertFalse(self.destination.exists())

    def test_untrusted_modes_are_rejected_without_chmod(self):
        for mode in (0o777, 0o757, 0o2775):
            with self.subTest(mode=oct(mode)):
                self.destination.chmod(mode)
                with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
                    with self.assertRaisesRegex(RuntimeError, 'Unsafe controller installation directory'):
                        secure(self.destination)
                chmod.assert_not_called()
                self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), mode)

    def test_user_owned_0775_directory_is_never_adopted(self):
        self.destination.chmod(0o775)
        with self.root_metadata(user_owned=self.destination), patch.object(os, 'fchmod') as chmod:
            with self.assertRaisesRegex(RuntimeError, 'uid=1000'):
                secure(self.destination)
        chmod.assert_not_called()

    def test_writable_or_user_owned_ancestor_is_never_repaired(self):
        for mode, owner in ((0o775, None), (0o1777, None), (0o755, self.parent)):
            with self.subTest(mode=oct(mode), owner=owner):
                self.parent.chmod(mode)
                with self.root_metadata(user_owned=owner), patch.object(os, 'fchmod') as chmod:
                    with self.assertRaisesRegex(RuntimeError, str(self.parent)):
                        secure(self.destination)
                chmod.assert_not_called()
                self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), mode)

    def test_symlinks_and_nondirectories_are_rejected(self):
        for path in (self.destination, self.parent):
            for symlink in (True, False):
                with self.subTest(path=path, symlink=symlink):
                    saved = path.with_name(path.name + '-saved')
                    path.rename(saved)
                    try:
                        if symlink:
                            path.symlink_to(saved, target_is_directory=True)
                        else:
                            path.write_bytes(b'keep')
                        with self.root_metadata(), patch.object(os, 'fchmod') as chmod:
                            with self.assertRaisesRegex(RuntimeError, 'Unsafe controller installation directory'):
                                secure(self.destination)
                        chmod.assert_not_called()
                    finally:
                        path.unlink()
                        saved.rename(path)

    def test_runtime_error_identifies_offending_directory_and_mode(self):
        controller = Path('/usr/local/lib/anvildroid-controller')
        def metadata(path):
            return os.stat_result((stat.S_IFDIR | (0o775 if path == controller else 0o755),
                                   0, 0, 1, 0, 0, 0, 0, 0, 0))
        with patch.object(Path, 'lstat', autospec=True, side_effect=metadata):
            with self.assertRaisesRegex(RuntimeError, str(controller) + r' .*mode=0775'):
                x11['load_payload'](controller / 'native-x11')


if __name__ == '__main__':
    unittest.main()
