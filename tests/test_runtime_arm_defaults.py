"""Default/system Waydroid ARM defaults, using only temporary runtime files."""
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


existing = load('arm_defaults_existing', 'services/runtime-existing.py')
setup = load('arm_defaults_setup', 'scripts/setup-waydroid.py')


class SetupArmDefaultsTests(unittest.TestCase):
    def test_setup_uses_same_default_archive_as_prepare(self):
        archive = Path('/unused/houdini.zip')
        with patch.object(setup, 'write_status'), patch.object(setup.arm, 'ensure_default_archive', return_value=archive) as download:
            self.assertEqual(setup.ensure_arm_archive(), archive)
        download.assert_called_once_with()

    def test_setup_propagates_verification_failure(self):
        with patch.object(setup, 'write_status'), patch.object(setup.arm, 'ensure_default_archive', side_effect=ValueError('SHA-256 mismatch')):
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                setup.ensure_arm_archive()


@unittest.skipUnless(os.geteuid() == 0, 'System overlay staging requires root-owned fixtures')
class DefaultRuntimeArmTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'waydroid'
        store = self.root / 'controller'; store.mkdir()
        (self.work / 'lxc/waydroid').mkdir(parents=True)
        self.runtime = existing.ExistingRuntime(store, self.work)
        self.data = self.root / 'data'; self.data.mkdir()
        self.marker = self.data / 'app-data'; self.marker.write_bytes(b'previous app data')
        info = self.data.stat()
        saved = {'owner_uid': 1000, 'name': 'Default', 'data_path': str(self.data),
                 'data_identity': {'device': info.st_dev, 'inode': info.st_ino},
                 'arm_properties': {'ro.dalvik.vm.native.bridge': 'libndk_translation.so'}}
        existing.storage.atomic(self.runtime.metadata, saved)
        (self.work / 'waydroid.cfg').write_text('[waydroid]\n[properties]\nro.ndk_translation.version=0.2.3\n')
        for name in ('waydroid.prop', 'waydroid_base.prop'):
            (self.work / name).write_text('custom=keep\nro.dalvik.vm.native.bridge=libndk_translation.so\nro.ndk_translation.version=0.2.3\n')
        self.archive = self.root / 'houdini.zip'
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for lib in ('lib', 'lib64'):
                archive.writestr(f'system/{lib}/libhoudini.so', b'Houdini fixture')
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        for target, attribute, kwargs in (
            (existing, 'session_info', {'return_value': {}}),
            (self.runtime, 'state', {'return_value': 'STOPPED'}),
            (existing.platform, 'machine', {'return_value': 'x86_64'}),
            (existing.gpu, 'automatic_device', {'return_value': None}),
            (existing.arm, 'DEFAULT_SHA256', {'new': digest}),
            (existing.arm, 'ensure_default_archive', {'return_value': self.archive}),
        ):
            mocked = patch.object(target, attribute, **kwargs)
            mocked.start(); self.addCleanup(mocked.stop)

    def test_reinstall_selects_houdini_and_can_disable_restore(self):
        self.runtime.reinstall(1000, 'installed')
        self.assertFalse(self.marker.exists())
        self.assertNotIn('arm_properties', self.runtime.saved())
        props = existing.properties((self.work / 'waydroid.prop').read_text())
        self.assertEqual(props['ro.dalvik.vm.native.bridge'], 'libhoudini.so')
        self.assertNotIn('ro.ndk_translation.version', props)
        self.assertEqual(props['custom'], 'keep')
        self.assertEqual((self.work / 'overlay/system/lib64/libhoudini.so').read_bytes(), b'Houdini fixture')
        self.assertTrue(self.runtime.arm_info()['supported'])
        self.assertFalse(self.runtime.dispatch({'op': 'arm_set', 'id': 'default', 'enabled': False}, 1000)['enabled'])
        self.assertEqual(self.runtime.arm_info()['provider'], 'libhoudini.so')
        self.assertTrue(self.runtime.arm_info()['restore_available'])
        self.assertTrue(self.runtime.dispatch({'op': 'arm_set', 'id': 'default', 'enabled': True}, 1000)['enabled'])
        self.assertEqual(existing.properties((self.work / 'waydroid.prop').read_text()), props)

    def test_download_failure_preserves_data_and_properties(self):
        original = (self.work / 'waydroid.prop').read_bytes()
        with patch.object(existing.arm, 'ensure_default_archive', side_effect=RuntimeError('download failed')):
            with self.assertRaisesRegex(RuntimeError, 'download failed'):
                self.runtime.reinstall(1000, 'installed')
        self.assertEqual(self.marker.read_bytes(), b'previous app data')
        self.assertEqual((self.work / 'waydroid.prop').read_bytes(), original)

    def test_ordinary_setup_preserves_active_provider(self):
        with patch.object(existing.arm, 'ensure_default_archive') as download:
            self.runtime.apply_defaults()
        download.assert_not_called()
        self.assertEqual(existing.properties((self.work / 'waydroid.prop').read_text())['ro.dalvik.vm.native.bridge'], 'libndk_translation.so')

    def test_fresh_setup_installs_houdini(self):
        for name in ('waydroid.prop', 'waydroid_base.prop'):
            (self.work / name).write_text('custom=keep\n')
        self.runtime.apply_defaults()
        self.assertEqual(existing.properties((self.work / 'waydroid.prop').read_text())['ro.dalvik.vm.native.bridge'], 'libhoudini.so')


if __name__ == '__main__':
    unittest.main()
