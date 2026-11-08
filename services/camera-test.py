"""Host camera discovery, selection and controller ops; no real devices."""
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('camera_under_test', HERE / 'runtime-camera.py')
camera = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera)

CAPTURE, META = camera.CAP_VIDEO_CAPTURE, 0x00800000


def fake_host(root, nodes, by_id=()):
    """nodes: {name: (capabilities, sysfs name)}; by_id: [(link, node)]."""
    dev, sysfs, links = root / 'dev', root / 'sys', root / 'dev/v4l/by-id'
    links.mkdir(parents=True)
    for node, (_, name) in nodes.items():
        (dev / node).write_text('')
        (sysfs / node).mkdir(parents=True)
        (sysfs / node / 'name').write_text(name + '\n')
    for link, node in by_id:
        (links / link).symlink_to(dev / node)
    query = lambda path: nodes[path.name][0]
    return dict(dev=dev, sysfs=sysfs, by_id=links, query=query)


class Discovery(unittest.TestCase):
    def test_lists_capture_nodes_with_stable_ids_and_skips_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            host = fake_host(Path(directory), {
                'video0': (CAPTURE, 'Integrated Camera: Integrated C'),
                'video1': (META, 'Integrated Camera: Integrated C'),
                'video10': (CAPTURE, 'USB Webcam'),
                'video2': (None, 'Broken node')},
                by_id=[('usb-Chicony_Integrated_Camera-video-index0', 'video0')])
            cameras = camera.discover(**host)
        self.assertEqual([c['node'] for c in cameras], ['video0', 'video10'])  # numeric order, capture only
        self.assertEqual(cameras[0]['id'], 'usb-Chicony_Integrated_Camera-video-index0')
        self.assertEqual(cameras[1]['id'], 'video10:USB_Webcam')                # no by-id link
        self.assertEqual(cameras[0]['name'], 'Integrated Camera: Integrated C')

    def test_no_cameras(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(camera.discover(**fake_host(Path(directory), {})), [])


class Selection(unittest.TestCase):
    cameras = [{'id': 'usb-A', 'name': 'A', 'node': 'video0'}, {'id': 'usb-B', 'name': 'B', 'node': 'video2'}]

    def test_off_by_default_and_damaged_files_stay_off(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / camera.POLICY
            self.assertEqual(camera.read_policy(path), {'enabled': False, 'devices': []})
            path.write_text('{not json')
            self.assertEqual(camera.read_policy(path)['enabled'], False)
            path.write_text(json.dumps({'enabled': 'yes'}))
            self.assertEqual(camera.read_policy(path)['enabled'], False)

    def test_selection_follows_ids_and_skips_unplugged_cameras(self):
        self.assertEqual(camera.selected_nodes({'enabled': False, 'devices': ['usb-A']}, self.cameras), [])
        self.assertEqual(camera.selected_nodes({'enabled': True, 'devices': []}, self.cameras), ['video0', 'video2'])
        self.assertEqual(camera.selected_nodes({'enabled': True, 'devices': ['usb-B']}, self.cameras), ['video2'])
        self.assertEqual(camera.selected_nodes({'enabled': True, 'devices': ['usb-gone']}, self.cameras), [])

    def test_policy_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / camera.POLICY
            saved = camera.write_policy(path, {'enabled': True, 'devices': ['usb-A', 'video3:Cam']})
            self.assertEqual(camera.read_policy(path), saved)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for bad in ({'enabled': True, 'devices': ['../etc']}, {'enabled': True, 'devices': ['a', 'a']},
                        {'enabled': True, 'devices': ['x'] * 9}, {'enabled': 1, 'devices': []}, {'other': True}):
                with self.assertRaises(ValueError):
                    camera.write_policy(path, bad)

    def test_android_hal_only_with_a_camera(self):
        props = ['ro.hardware.camera=legacy', 'ro.product.model=x']
        self.assertEqual(camera.boot_properties(props, ['video0']), ['ro.product.model=x', 'ro.hardware.camera=v4l2',
                                                                     'media.settings.xml=/vendor/etc/media_profiles_V1_0.xml'])
        self.assertEqual(camera.boot_properties(props, []), ['ro.product.model=x'])

    def test_camera_switches_mesa_gralloc_to_legacy_alias_only(self):
        mesa = ['ro.hardware.gralloc=minigbm_gbm_mesa', 'gralloc.gbm.device=/dev/dri/renderD128']
        self.assertIn('ro.hardware.gralloc=anvilgbm', camera.boot_properties(mesa, ['video0'], True))
        self.assertEqual(camera.boot_properties(mesa, [], True), mesa)               # no camera: untouched
        self.assertIn('ro.hardware.gralloc=minigbm_gbm_mesa', camera.boot_properties(mesa, ['video0'], False))
        software = ['ro.hardware.gralloc=default']
        self.assertIn('ro.hardware.gralloc=default', camera.boot_properties(software, ['video0'], True))

    def test_gralloc_alias_is_created_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            vendor = Path(directory)
            self.assertFalse(camera.legacy_gralloc_available(vendor))
            for folder in camera.HAL_DIRS:
                (vendor / folder).mkdir(parents=True)
                (vendor / folder / 'gralloc.gbm.so').write_text('elf')
            self.assertTrue(camera.legacy_gralloc_available(vendor))
            camera.gralloc_alias(vendor, True)
            camera.gralloc_alias(vendor, True)                                       # idempotent
            alias = vendor / 'lib64/hw/gralloc.anvilgbm.so'
            self.assertEqual((alias.readlink(), alias.read_text()), (Path('gralloc.gbm.so'), 'elf'))
            camera.gralloc_alias(vendor, False)
            self.assertFalse(alias.is_symlink())
            (vendor / 'lib/hw/gralloc.anvilgbm.so').write_text('not ours')
            with self.assertRaises(RuntimeError):
                camera.gralloc_alias(vendor, True)

CONFIG = """<ExternalCamera>
    <Provider>
        <ignore>
        </ignore>
    </Provider>
    <Device/>
</ExternalCamera>
"""
PROFILES = """<MediaSettings>
    <CamcorderProfiles cameraId="0">
        <EncoderProfile quality="720p " fileFormat="mp4" duration="60"/>
    </CamcorderProfiles>

    <CamcorderProfiles cameraId="1">
        <EncoderProfile quality="720p" fileFormat="mp4" duration="60"/>
    </CamcorderProfiles>
    <EncoderOutputFileFormat format="mp4" />
</MediaSettings>
"""


class VendorFiles(unittest.TestCase):
    def test_camera_ids_follow_video_nodes(self):
        self.assertEqual(camera.camera_ids(['video0', 'video12', 'bogus']), [0, 12])

    def test_external_cameras_are_numbered_from_zero_only_while_enabled(self):
        enabled = camera.external_config(CONFIG, True)
        self.assertIn('<CameraIdOffset>0</CameraIdOffset>\n    </Provider>', enabled)
        self.assertEqual(camera.external_config(enabled, True), enabled)            # idempotent
        self.assertEqual(camera.external_config(enabled, False), CONFIG)

    def test_recording_profiles_follow_the_camera_modes(self):
        webcam = {(1280, 720): 10, (640, 480): 30, (320, 240): 30, (160, 120): 30}
        self.assertEqual(camera.media_profiles(PROFILES, {}), PROFILES)           # no camera: image file untouched
        self.assertIn('cameraId="1"', camera.media_profiles(PROFILES, {0: {}}))  # no usable modes: image profiles kept
        self.assertNotIn('"720p "', camera.media_profiles(PROFILES, {5: webcam}))   # parser-safe quality names
        result = camera.media_profiles(PROFILES, {0: webcam})
        block = result[result.index('cameraId="0"'):result.index('</CamcorderProfiles>')]
        self.assertEqual(re.findall(r'EncoderProfile quality="([^"]+)"', block), ['qvga', '480p', '720p'])
        self.assertIn('width="1280" height="720" frameRate="10"', block)
        self.assertEqual(result.count('cameraId="0"'), 1)
        self.assertIn('<CamcorderProfiles cameraId="1">', result)                    # other ids untouched
        self.assertLess(result.index('cameraId="0"'), result.index('cameraId="1"'))
        added = camera.media_profiles(PROFILES, {7: webcam})
        self.assertLess(added.index('cameraId="7"'), added.index('cameraId="0"'))
        self.assertTrue(added.rstrip().endswith('</MediaSettings>'))

    def test_vendor_files_are_rebuilt_from_the_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lower, vendor = root / 'lower', root / 'vendor'
            for base in (lower, vendor):
                (base / 'etc').mkdir(parents=True)
            (lower / camera.EXTERNAL_CONFIG).write_text(CONFIG)
            (lower / camera.MEDIA_PROFILES).write_text(PROFILES)
            camera.vendor_files(vendor, [root / 'patches', lower], ['video4'], sizes=lambda _: {(640, 480): 30})
            self.assertIn('CameraIdOffset', (vendor / camera.EXTERNAL_CONFIG).read_text())
            self.assertIn('cameraId="4"', (vendor / camera.MEDIA_PROFILES).read_text())
            camera.vendor_files(vendor, [root / 'patches', lower], [], sizes=lambda _: {})
            self.assertEqual((vendor / camera.EXTERNAL_CONFIG).read_text(), CONFIG)
            self.assertEqual((vendor / camera.MEDIA_PROFILES).read_text(), PROFILES)

features_spec = importlib.util.spec_from_file_location('features_camera_test', HERE / 'runtime-features.py')
features = importlib.util.module_from_spec(features_spec)
features_spec.loader.exec_module(features)


class ControllerOps(unittest.TestCase):
    def store(self, root, state):
        record = {'id': 'r-' + 'a' * 32, 'owner_uid': 1000, 'state': state}
        store = types.SimpleNamespace(data={'runtimes': [record]}, deletions={}, active_jobs=set(),
                                      storage=types.SimpleNamespace(busy=None, ensure=lambda _: None), app_jobs={},
                                      backend=types.SimpleNamespace(location=lambda _: root),
                                      android=Mock(), existing=Mock())
        store.android.quiescent.return_value = True
        store.android.status.return_value = {'cameras': ['video0']}
        store.existing.authorize.return_value = ({}, {})   # System Waydroid session, saved settings
        return store, record['id']

    def test_set_needs_a_stopped_runtime_and_get_reports_active_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features.camera.discover = lambda: [{'id': 'usb-A', 'name': 'A', 'node': 'video0'}]
            store, identifier = self.store(root, 'Stopped')
            result = features.dispatch(store, {'op': 'camera_set', 'id': identifier, 'enabled': True, 'devices': ['usb-A']}, 1000)
            self.assertEqual((result['enabled'], result['devices'], result['active']), (True, ['usb-A'], None))
            self.assertEqual(result['available'][0]['node'], 'video0')
            store.data['runtimes'][0]['state'] = 'Running'
            with self.assertRaisesRegex(RuntimeError, 'Stop this runtime'):
                features.dispatch(store, {'op': 'camera_set', 'id': identifier, 'enabled': False, 'devices': []}, 1000)
            self.assertEqual(features.dispatch(store, {'op': 'camera_get', 'id': identifier}, 1000)['active'], ['video0'])
            with self.assertRaisesRegex(RuntimeError, 'managed runtimes'):
                features.dispatch(store, {'op': 'camera_get', 'id': 'default'}, 1000)


if __name__ == '__main__':
    unittest.main()
