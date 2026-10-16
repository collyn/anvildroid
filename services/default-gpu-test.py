#!/usr/bin/python3
"""Default GPU device and renderer fixtures; never opens a GPU or starts Android."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('default_gpu_system',
                                             Path(__file__).with_name('runtime-system.py'))
system = importlib.util.module_from_spec(spec)
spec.loader.exec_module(system)


class DefaultDrmTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tools = SimpleNamespace(actions=SimpleNamespace(container_manager=SimpleNamespace(
            do_start=Mock(), freeze=Mock(), stop=Mock())), helpers=SimpleNamespace(
                images=SimpleNamespace(mount_rootfs=Mock(), make_prop=Mock())))
        self.integration = system.Integration(tools, Mock())
        state = patch.object(system, 'STATE', self.root)
        state.start()
        self.addCleanup(state.stop)
        self.node = '/dev/dri/renderD129'
        self.integration.dri_node = self.node
        original = Path.lstat
        self.device = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660,
                                      st_rdev=os.makedev(226, 129))
        metadata = patch.object(Path, 'lstat',
            lambda path, *a, **kw: self.device if str(path) == self.node else original(path, *a, **kw))
        metadata.start()
        self.addCleanup(metadata.stop)
        # Stand in for mknod without CAP_MKNOD; all other filesystem
        # operations, permissions and cleanup operate on real temp files.
        create = patch.object(system.os, 'mknod', side_effect=lambda path, mode, rdev: Path(path).touch())
        self.create = create.start()
        self.addCleanup(create.stop)
        self.addCleanup(self.integration.close)

    def test_selected_node_is_private_accessible_and_removed_on_close(self):
        previous = os.umask(0o077)
        try:
            directory = self.integration.private_dri(self.node)
        finally:
            os.umask(previous)
        target = directory / 'renderD129'
        self.assertEqual(list(directory.iterdir()), [target])
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o666)
        self.create.assert_called_once_with(target, stat.S_IFCHR | 0o666, os.makedev(226, 129))
        self.assertEqual(self.device.st_mode, stat.S_IFCHR | 0o660)
        self.integration.close()
        self.assertFalse(directory.exists())
        self.assertIsNone(self.integration.dri)
        self.integration.close()

    def test_wrong_device_and_symlink_are_rejected_before_creating_anything(self):
        for mode, rdev in ((stat.S_IFREG, 0), (stat.S_IFLNK, 0),
                           (stat.S_IFCHR, os.makedev(1, 3))):
            with self.subTest(mode=mode, rdev=rdev):
                self.device.st_mode, self.device.st_rdev = mode, rdev
                with self.assertRaisesRegex(RuntimeError, 'DRM character device'):
                    self.integration.private_dri(self.node)
        self.create.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_no_card_node_or_arbitrary_path_is_accepted(self):
        for node in ('/dev/dri/card0', '/dev/nvidia0', '/tmp/renderD129',
                     '/dev/dri/../renderD129', None):
            with self.subTest(node=node), self.assertRaisesRegex(RuntimeError, 'DRM render node'):
                self.integration.private_dri(node)
        self.create.assert_not_called()

    def test_nvidia_mount_supplies_missing_drm_beside_venus_in_session_config(self):
        container = self.root / 'lxc/waydroid'
        container.mkdir(parents=True)
        config = container / 'config_session'
        config.write_text('existing=session\n')
        self.integration.tools.config = SimpleNamespace(defaults={
            'rootfs': str(self.root / 'rootfs'), 'lxc': str(container.parent)})
        renderer = Mock()
        renderer.directory = self.root / 'venus'
        renderer.directory.mkdir()
        renderer.endpoint = renderer.directory / 'venus.sock'
        self.integration.renderer = renderer
        self.integration.nvidia = True
        with patch.object(system.gpu.nvidia, 'image_mounts', return_value=[]):
            self.integration.mount({}, 'images', {'user_id': '1000'})
        self.assertIn('existing=session\n', config.read_text())
        self.assertIn(f'{renderer.directory} dev/venus none bind,ro,create=dir 0 0\n', config.read_text())
        self.assertIn(f'{self.integration.dri} dev/dri none bind,ro,dev,create=dir 0 0\n', config.read_text())
        self.assertEqual([p.name for p in self.integration.dri.iterdir()], ['renderD129'])

    def test_mesa_mount_does_not_create_nvidia_devices(self):
        self.integration.mount({}, 'images', {'user_id': '1000'})
        self.integration.original_mount.assert_called_once()
        self.create.assert_not_called()
        self.assertIsNone(self.integration.dri)

    def test_failed_creation_is_cleaned_and_next_generation_gets_a_fresh_directory(self):
        self.create.side_effect = OSError('fixture mknod failure')
        with self.assertRaisesRegex(OSError, 'fixture mknod failure'):
            self.integration.private_dri(self.node)
        first = self.integration.dri
        self.integration.close()
        self.assertFalse(first.exists())
        self.integration.dri_node = self.node
        self.create.side_effect = lambda path, mode, rdev: Path(path).touch()
        second = self.integration.private_dri(self.node)
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            self.integration.private_dri(self.node)


class DefaultRendererSelectionTests(unittest.TestCase):
    """Exercise the persisted Default config, not just a generated prop list."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = system.existing.ExistingRuntime(self.root, self.root / 'waydroid')
        self.work = self.runtime.work
        (self.work / 'lxc/waydroid').mkdir(parents=True)
        self.node = '/dev/dri/renderD128'
        self.device = dict(node=self.node, driver='amdgpu', vendor_id='0x1002')
        self.stale = {
            'ro.hardware.egl': 'angle', 'ro.hardware.gralloc': 'default',
            'ro.hardware.vulkan': 'pastel', 'ro.waydroid.software_rendering': '1',
            'ro.waydroid.override_props': 'true', 'debug.hwui.renderer': 'skiavk',
            'mesa.vn.debug': 'vtest', 'mesa.vtest.socket.name': '/dev/venus/venus.sock',
            'debug.renderengine.backend': 'skiaglthreaded',
            'debug.angle.feature_overrides_disabled': 'old-features',
            'gralloc.gbm.device': '/dev/dri/renderD129',
            'ro.dalvik.vm.native.bridge': 'libhoudini.so', 'custom': 'keep',
        }
        cfg = system.existing.configparser.ConfigParser()
        cfg.read_dict({'waydroid': {'drm_device': '/dev/dri/renderD129'},
                       'properties': self.stale})
        with (self.work / 'waydroid.cfg').open('w') as stream:
            cfg.write(stream)
        for name in ('waydroid.prop', 'waydroid_base.prop'):
            (self.work / name).write_text(''.join(k + '=' + v + '\n' for k, v in self.stale.items()))
        (self.work / 'lxc/waydroid/config_nodes').write_text(
            'lxc.mount.entry = /dev/dri/renderD129 dev/dri/renderD129 none bind 0 0\n'
            'lxc.mount.entry = /dev/null dev/null none bind 0 0\n')
        for owner, name, kwargs in (
                (self.runtime, 'read', dict(side_effect=lambda path: path.read_text())),
                (self.runtime, 'stopped', {}),
                (self.runtime, 'gpu_info', dict(return_value={})),
                (system.existing.gpu, 'inventory', dict(return_value=[self.device])),
                (system.existing.gpu, 'choose', dict(return_value=self.device)),
                (system.existing.gpu, 'automatic_device', dict(return_value=self.device)),
                (system.existing.gpu.nvidia, 'system_integration_available', dict(return_value=True))):
            patcher = patch.object(owner, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_mesa_config(self, vulkan='radeon'):
        cfg = self.runtime.config()
        self.assertEqual(cfg['waydroid']['drm_device'], self.node)
        sets = [dict(cfg['properties'])] + [system.existing.properties((self.work / name).read_text())
                for name in ('waydroid.prop', 'waydroid_base.prop')]
        for values in sets:
            with self.subTest(values=values):
                for key, value in {
                        'ro.hardware.egl': 'mesa', 'ro.hardware.gralloc': 'minigbm_gbm_mesa',
                        'ro.hardware.vulkan': vulkan, 'ro.waydroid.software_rendering': '0',
                        'ro.waydroid.override_props': 'false', 'debug.hwui.renderer': 'skiagl',
                        'gralloc.gbm.device': self.node, 'custom': 'keep',
                        'ro.dalvik.vm.native.bridge': 'libhoudini.so'}.items():
                    self.assertEqual(values.get(key), value, key)
                for key in ('mesa.vn.debug', 'mesa.vtest.socket.name',
                            'debug.renderengine.backend', 'debug.angle.feature_overrides_disabled'):
                    self.assertNotIn(key, values)
        mounts = (self.work / 'lxc/waydroid/config_nodes').read_text()
        self.assertIn(self.node + ' dev/dri/renderD128', mounts)
        self.assertNotIn('renderD129', mounts)
        self.assertIn('/dev/null', mounts)

    def test_select_amd_clears_software_and_venus_in_all_property_sources(self):
        self.runtime._dispatch({'op': 'gpu_set', 'node': self.node}, 1000, {}, {'owner_uid': 1000})
        self.assert_mesa_config()

    def test_auto_select_intel_also_replaces_the_renderer(self):
        self.device.update(driver='i915', vendor_id='0x8086')
        self.runtime._dispatch({'op': 'gpu_set', 'node': 'auto'}, 1000, {}, {'owner_uid': 1000})
        self.assert_mesa_config('intel')

    def test_apply_defaults_replaces_renderer_and_keeps_unknown_arm_provider(self):
        self.runtime.apply_defaults()
        self.assert_mesa_config()

    def test_nvidia_renderer_remains_session_only(self):
        self.device.update(driver='nvidia', vendor_id='0x10de')
        self.runtime._dispatch({'op': 'gpu_set', 'node': self.node}, 1000, {}, {'owner_uid': 1000})
        cfg = self.runtime.config()
        self.assertEqual(cfg['properties']['gralloc.gbm.device'], self.node)
        for name in ('waydroid.prop', 'waydroid_base.prop'):
            values = system.existing.properties((self.work / name).read_text())
            for key, old in self.stale.items():
                self.assertEqual(values[key], self.node if key == 'gralloc.gbm.device' else old)


if __name__ == '__main__':
    unittest.main()
