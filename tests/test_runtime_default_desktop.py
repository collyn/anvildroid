"""Default Desktop install uses a real JSON backup and publishes trusted assets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('existing',Path(__file__).resolve().parents[1]/'services/runtime-existing.py')
existing=importlib.util.module_from_spec(spec);spec.loader.exec_module(existing)

class DefaultDesktopInstall(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'waydroid';self.store=self.root/'controller'
        self.store.mkdir();self.work.mkdir()
        self.runtime=existing.ExistingRuntime(self.store,self.work)
        state=patch.object(self.runtime,'state',return_value='STOPPED');state.start();self.addCleanup(state.stop)
        reader=patch.object(self.runtime,'read',side_effect=lambda p:p.read_text());reader.start();self.addCleanup(reader.stop)

    def test_desktop_install_persists_backup_manifest_and_overlay(self):
        root = self.work / 'rootfs'
        init = root / 'system/etc/init/init.waydroid.rc'; init.parent.mkdir(parents=True)
        init.write_text('service vendor.hwcomposer-2-1 /vendor/bin/hw/android.hardware.graphics.composer@2.1-service --desktop_file_hint=Waydroid.desktop\n')
        framework = root / 'system/framework/framework-res.apk'; framework.parent.mkdir(parents=True)
        framework.write_bytes(b'fixture framework')
        assets = self.root / 'assets'; (assets / 'provision').mkdir(parents=True)
        (assets / 'provision/framework-res.apk.sha256').write_text(hashlib.sha256(framework.read_bytes()).hexdigest())
        paths = {}
        for index, name in enumerate(existing.DESKTOP_ASSETS | existing.KEYMAP_ASSETS):
            path = assets / ('asset-' + str(index)); path.write_bytes(name.encode()); paths[name] = path
        bridge = self.work / 'overlay/vendor/lib64/libanvildroid-window.so'; bridge.parent.mkdir(parents=True)
        bridge.write_bytes(b'previous bridge')
        with patch.object(existing, 'ASSETS', assets), patch.object(existing, 'DESKTOP_ASSETS', paths),\
             patch.object(existing, 'KEYMAP_ASSETS', {}):
            result = self.runtime.enable_desktop()
        self.assertTrue(result['bridge_installed'])
        backups = list(self.store.glob('existing-desktop-backup-*')); self.assertEqual(len(backups), 1)
        manifest = json.loads((backups[0] / 'manifest.json').read_text())
        self.assertEqual(set(manifest['files']), set(paths) | {'system/etc/init/init.waydroid.rc'})
        self.assertIsInstance(manifest['created_at'], float)
        self.assertEqual((backups[0] / 'vendor/lib64/libanvildroid-window.so').read_bytes(), b'previous bridge')
        for name, source in paths.items():self.assertEqual((self.work / 'overlay' / name).read_bytes(), source.read_bytes())
        self.assertIn('    setenv ANVILDROID_RUNTIME_ID default\n', (self.work / 'overlay/system/etc/init/init.waydroid.rc').read_text())


if __name__=="__main__":unittest.main()
