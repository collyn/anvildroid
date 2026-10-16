"""Regression coverage for managed preparation on an uninitialized Waydroid host."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('provision', Path(__file__).with_name('runtime-provision.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class StandaloneSupportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = self.root / 'host'  # Deliberately absent, as on a fresh install.
        self.templates = self.root / 'templates'
        self.templates.mkdir()
        for name, text in {'config_base': 'lxc.arch = LXCARCH\nlxc.cap.keep = setuid setgid\n',
                           'config_3': 'lxc.apparmor.profile = unconfined\n',
                           'config_4': 'lxc.seccomp.allow_nesting = 1\n',
                           'waydroid.seccomp': '2\nblacklist\nreboot\n'}.items():
            (self.templates / name).write_text(text)
        for name, value in [('WAYDROID_ROOT', self.host), ('APPARMOR_PROFILES', self.root / 'loaded-profiles'), ('LXC_TEMPLATE_DIRS', (self.templates,))]:
            manager = patch.object(p, name, value)
            manager.start()
            self.addCleanup(manager.stop)

    def test_fresh_managed_properties_preserve_security_without_host_identity(self):
        props = p.host_properties(managed=True)
        self.assertIn('ro.adb.secure=1', props)
        self.assertIn('ro.debuggable=0', props)
        self.assertIn('ro.vndk.lite=true', props)
        self.assertFalse(any(line.startswith(('waydroid.host.uid=', 'gralloc.gbm.device=',
                                              'ro.dalvik.vm.native.bridge=')) for line in props))
        self.assertFalse(self.host.exists())
        with self.assertRaisesRegex(RuntimeError, 'Complete Waydroid initialization'):
            p.host_properties()  # Strict host mode still detects incomplete init.

    def test_per_app_contract_requires_both_framework_and_composer(self):
        system, vendor = self.root / 'system', self.root / 'vendor'
        sf, hwc = system / 'system/bin/surfaceflinger', vendor / 'lib64/hw/hwcomposer.waydroid.so'
        sf.parent.mkdir(parents=True)
        hwc.parent.mkdir(parents=True)
        tokens = (b'vendor.waydroid.display@1.0', b'vendor.waydroid.window@1.0',
                  b'persist.waydroid.multi_windows', b'wl_shm', b'zwp_linux_dmabuf_v1')
        sf.write_bytes(tokens[0])
        hwc.write_bytes(b'\0'.join(tokens))
        self.assertEqual(p.app_window_contract(system, vendor), 'waydroid-hidl')
        for token in tokens:
            hwc.write_bytes(b'\0'.join(t for t in tokens if t != token))
            self.assertIsNone(p.app_window_contract(system, vendor), token)
        hwc.write_bytes(b'\0'.join(tokens))
        sf.write_bytes(b'stock SurfaceFlinger')
        self.assertIsNone(p.app_window_contract(system, vendor))

    def test_existing_properties_take_precedence(self):
        self.host.mkdir()
        (self.host / 'waydroid.prop').write_text('custom.property=kept\n')
        self.assertEqual(p.host_properties(managed=True), ['custom.property=kept'])

    def test_base_properties_are_reconstructed_without_host_writes(self):
        self.host.mkdir()
        (self.host / 'waydroid_base.prop').write_text('custom.base=kept\n')
        self.assertIn('custom.base=kept', p.host_properties(managed=True))
        self.assertFalse((self.host / 'waydroid.prop').exists())

    def test_installed_templates_work_without_host_init(self):
        self.assertTrue(p.managed_support_available())
        for version in ('3.0.4', '6.0.5'):
            with self.subTest(version=version), patch.object(p, 'run', return_value=version), \
                 patch.object(p.platform, 'machine', return_value='x86_64'):
                target = self.root / version
                target.mkdir()
                p.copy_lxc_support(target)
                config = (target / 'config').read_text()
                self.assertIn('lxc.arch = x86_64', config)
                self.assertEqual('lxc.seccomp.allow_nesting' in config, version.startswith('6'))
                self.assertEqual((target / 'waydroid.seccomp').read_bytes(),
                                 (self.templates / 'waydroid.seccomp').read_bytes())
        self.assertFalse(self.host.exists())

    def test_loaded_waydroid_apparmor_profile_is_preserved(self):
        p.APPARMOR_PROFILES.write_text('lxc-waydroid (enforce)\n')
        target = self.root / 'support'
        target.mkdir()
        with patch.object(p, 'run', return_value='6.0.5'):
            p.copy_lxc_support(target)
        self.assertIn('lxc.apparmor.profile = lxc-waydroid', (target / 'config').read_text())
        self.assertNotIn('unconfined', (target / 'config').read_text())

    def test_existing_lxc_configuration_is_preserved(self):
        source = self.host / 'lxc/waydroid'
        source.mkdir(parents=True)
        for name in ('config', 'waydroid.seccomp'):
            (source / name).write_text('existing ' + name)
        target = self.root / 'support'
        target.mkdir()
        with patch.object(p, 'run') as command:
            p.copy_lxc_support(target)
            command.assert_not_called()
        self.assertEqual((target / 'config').read_text(), 'existing config')

    def test_missing_templates_and_unsupported_lxc_are_clear_errors(self):
        target = self.root / 'support'
        target.mkdir()
        with patch.object(p, 'LXC_TEMPLATE_DIRS', ()):
            self.assertFalse(p.managed_support_available())
            with self.assertRaisesRegex(RuntimeError, 'Reinstall the Waydroid package'):
                p.copy_lxc_support(target)
        with patch.object(p, 'run', return_value='2.0.0'):
            with self.assertRaisesRegex(RuntimeError, 'LXC 3 or newer'):
                p.copy_lxc_support(target)

    def test_controller_allows_start_with_packaged_templates_only(self):
        spec = importlib.util.spec_from_file_location('fresh_controller', Path(__file__).with_name('runtime-controller.py'))
        controller = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(controller)
        store = Mock()
        store.backend.source_status.return_value = {'available': True, 'bytes': 100, 'detail': 'Images ready'}
        support = controller.android_module.provision
        with patch.object(support, 'WAYDROID_ROOT', self.host), \
             patch.object(support, 'LXC_TEMPLATE_DIRS', (self.templates,)), \
             patch.object(controller.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(controller.host_module, 'missing_commands', return_value=[]), \
             patch.object(controller.desktop_module, 'capabilities', return_value={'desktop': True, 'detail': 'Ready'}):
            report = controller.Store.requirements(store)
        self.assertTrue(report['can_start'])
        self.assertTrue(next(item for item in report['checks'] if item['id'] == 'support')['available'])
        self.assertFalse(self.host.exists())

    def test_prepare_all_flavors_without_any_generated_host_files(self):
        for flavor in ('VANILLA', 'GAPPS', 'CUSTOM'):
            with self.subTest(flavor=flavor):
                instance = self.root / flavor
                instance.mkdir()
                with patch.object(p.os, 'geteuid', return_value=0), \
                     patch.object(p.os, 'readlink', side_effect=['private', 'host']), \
                     patch.object(p.images, 'ImageBackend') as backend, \
                     patch.object(p.image_format, 'mount_options', return_value=['-o', 'ro']), \
                     patch.object(p, 'run', return_value='6.0.5'), patch.object(p, 'build_overlay'):
                    backend.return_value.inspect.return_value = {'images': {'system.img': 'verified', 'vendor.img': 'verified'}}
                    p.build(instance, flavor)
                support = instance / 'support'
                self.assertIn('ro.adb.secure=1', (support / 'waydroid.prop').read_text())
                self.assertTrue((support / 'config').is_file())
                self.assertTrue((support / 'waydroid.seccomp').is_file())
                self.assertEqual(json.loads((support / 'images.json').read_text())['system.img'], 'verified')
                self.assertFalse(list(instance.glob('.support-build-*')))
        self.assertFalse(self.host.exists())


if __name__ == '__main__':
    unittest.main()
