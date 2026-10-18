"""Inert device identity acceptance: real configuration adapters, no host changes."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


device = module('runtime-device')
existing_module = module('runtime-existing')
features = module('runtime-features')
fixtures = module('features-test')
FIRST, SECOND, UID = fixtures.FIRST, fixtures.SECOND, fixtures.UID


class ManagedDeviceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anvil-device-test-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.instance = self.root / FIRST
        self.instance.mkdir()

    def save(self, profile, custom=None):
        revision = device.managed_info(self.instance, FIRST)['revision']
        return device.managed_set(self.instance, FIRST, profile, custom, revision)

    def test_all_catalog_profiles_generate_allowed_consistent_identity(self):
        rows = device.catalog()
        self.assertGreaterEqual(len(rows), 26)
        self.assertEqual(len(rows), len({row['id'] for row in rows}))
        for row in rows:
            with self.subTest(profile=row['id']):
                report = self.save(row['id'])
                props = device.overrides(device.load(self.instance))
                self.assertLessEqual(set(props), device.KEYS)
                for prefix in ('', *(part + '.' for part in device.PARTITIONS)):
                    self.assertEqual(props['ro.product.' + prefix + 'model'], row['values']['model'])
                    self.assertEqual(props['ro.product.' + prefix + 'name'], row['values']['product'])
                self.assertEqual(report['values'], row['values'])
                self.assertFalse(any('cpu' in key or 'version.sdk' in key or 'hardware' in key for key in props))

    def test_pixel_7_and_pro_use_distinct_correct_codenames(self):
        values = {row['id']: row['values'] for row in device.catalog()}
        self.assertEqual(values['pixel-7']['device'], 'panther')
        self.assertEqual(values['pixel-7-pro']['device'], 'cheetah')
        self.assertIn('/panther/panther:', values['pixel-7']['fingerprint'])

    def test_custom_identity_optional_fingerprint_and_strict_validation(self):
        custom = {**device.catalog()[0]['values'], 'model':'My test phone', 'fingerprint':''}
        report = self.save('custom', custom)
        self.assertEqual(report['custom'], custom)
        self.assertFalse(set(device.overrides(device.load(self.instance))) & device.FINGERPRINT_KEYS)
        for field, value in [('model','bad\nro.adb.secure=0'), ('brand',''), ('device','a'*92),
                             ('model','a\x00b'), ('manufacturer',' leading'), ('product',13),
                             ('model','bad\\escape'), ('fingerprint','not-a-fingerprint')]:
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                self.save('custom', {**custom, field:value})
        for profile, data in [('unknown',None), ('pixel-5',custom), ('custom',None),
                              ('custom',{**custom,'ro.adb.secure':'0'}), (True,None)]:
            with self.assertRaises(RuntimeError): self.save(profile, data)
        self.assertEqual(device.load(self.instance)['custom'], custom)

    def test_boot_isolation_switch_reset_and_unrelated_properties(self):
        sibling = self.root / SECOND; sibling.mkdir()
        original = ['ro.product.model=Host spoof', 'ro.product.vendor.model=Host spoof',
                    'ro.build.fingerprint=host/fingerprint', 'ro.product.cpu.abilist=x86_64,arm64-v8a',
                    'ro.hardware.gralloc=gbm', 'persist.example=keep']
        self.save('pixel-5')
        boot = device.boot_properties(self.instance, original)
        self.assertIn('ro.product.model=Pixel 5', boot)
        self.assertEqual(sum(line.startswith('ro.product.model=') for line in boot), 1)
        self.assertTrue(all(line in boot for line in original[3:]))
        self.assertFalse(any('Host spoof' in line for line in device.boot_properties(sibling, original)))
        self.save('galaxy-s24-ultra')
        boot = device.boot_properties(self.instance, original)
        self.assertIn('ro.product.model=SM-S928B', boot)
        self.assertFalse(any(line.startswith('ro.build.fingerprint=') for line in boot))
        self.save('default')
        self.assertEqual(device.boot_properties(self.instance, original), original[3:])
        self.assertEqual(device.load(sibling)['profile'], 'default')

    def test_revision_rejects_stale_or_cross_runtime_write_and_failed_atomic_write(self):
        old = device.managed_info(self.instance, FIRST)['revision']
        self.save('pixel-5')
        before = (self.instance / device.FILE).read_bytes()
        for rev in (old, device.managed_info(self.instance, SECOND)['revision'], None):
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                device.managed_set(self.instance, FIRST, 'pixel-7', None, rev)
        with patch.object(device.os, 'replace', side_effect=OSError('fixture write failed')):
            with self.assertRaises(OSError): self.save('pixel-7')
        self.assertEqual((self.instance / device.FILE).read_bytes(), before)
        self.assertFalse(list(self.instance.glob('.device-*')))

    def test_allocated_runtime_can_save_before_its_instance_is_prepared(self):
        self.instance.rmdir()
        self.save('pixel-7')
        self.assertEqual(device.load(self.instance)['profile'], 'pixel-7')

    def test_default_mutation_is_blocked_during_import(self):
        store = fixtures.Store(self.root)
        store.imports = {FIRST:'pending'}
        store.active_jobs = {FIRST:object()}
        with self.assertRaisesRegex(RuntimeError, 'import to finish'):
            features.dispatch(store, {'op':'device_set','id':'default','profile':'pixel-7',
                                     'custom':None,'revision':'invalid'}, UID)

    def test_unsafe_files_and_malformed_schema_are_rejected(self):
        self.save('pixel-5')
        path = self.instance / device.FILE
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'): device.load(self.instance)
        path.chmod(0o600)
        for text in ('[]', '{}', '{"schema":true,"profile":"default","custom":null}'):
            path.write_text(text)
            with self.assertRaises(RuntimeError): device.load(self.instance)
        path.unlink(); path.symlink_to(self.root / 'missing')
        with self.assertRaises(OSError): device.load(self.instance)

    def test_controller_authorizes_owner_and_requires_quiescent_stopped_worker(self):
        store = fixtures.Store(self.root)
        for row in store.data['runtimes']: row['state'] = 'Stopped'
        report = features.dispatch(store, {'op':'device_info','id':FIRST}, UID)
        request = {'op':'device_set','id':FIRST,'profile':'pixel-7','custom':None,'revision':report['revision']}
        features.dispatch(store, request, UID)
        self.assertEqual(device.load(self.instance)['profile'], 'pixel-7')
        for state, quiet in [('Running',True), ('Frozen',True), ('Starting',True), ('Stopped',False)]:
            store.data['runtimes'][0]['state'] = state; store.android.quiet = quiet
            with self.assertRaisesRegex(RuntimeError, 'Stop this runtime'):
                features.dispatch(store, request, UID)
        store.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'not found'):
            features.dispatch(store, request, UID + 1)
        self.assertFalse(store.calls)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
            features.dispatch(store, {**request,'uid':0}, UID)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
            features.dispatch(store, request, UID, descriptor=1)


class DefaultDeviceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='anvil-default-device-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store, self.work = root / 'controller', root / 'waydroid'
        self.store.mkdir(mode=0o700); self.work.mkdir(mode=0o700)
        self.adapter = existing_module.ExistingRuntime(self.store, self.work)
        self.session = patch.object(existing_module, 'session_info', return_value={'user_id':str(UID)})
        self.session.start(); self.addCleanup(self.session.stop)
        self.stopped = patch.object(self.adapter, 'state', return_value='STOPPED')
        self.state = self.stopped.start(); self.addCleanup(self.stopped.stop)
        self.adapter.metadata.write_text(json.dumps({'owner_uid':UID,'name':'Default','arm_properties':{'keep':'arm'}}))
        (self.work / 'waydroid.cfg').write_text('[waydroid]\narch = x86_64\n[properties]\nro.product.model = Config model\nro.hardware.gralloc = gbm\n')
        (self.work / 'waydroid.prop').write_text('ro.product.model=Generated model\nro.product.cpu.abilist=x86_64,arm64-v8a\nro.build.fingerprint=original/generated\n')
        (self.work / 'waydroid_base.prop').write_text('ro.product.model=Base model\nro.hardware.gralloc=gbm\nro.build.fingerprint=original/base\n')
        for path in (*self.work.iterdir(), self.adapter.metadata): path.chmod(0o600)
        self.initial = {path.name:device.parse(path.read_text()) for path in self.work.iterdir() if path.suffix == '.prop'}

    def save(self, profile, custom=None):
        revision = device.default_info(self.adapter, UID)['revision']
        return device.default_set(self.adapter, UID, profile, custom, revision)

    def test_switch_and_reset_restore_separate_originals_and_preserve_arm_gpu(self):
        self.save('pixel-5')
        self.assertEqual(self.adapter.config().get('properties','ro.product.model'), 'Pixel 5')
        self.assertIn('ro.product.vendor.model=Pixel 5', (self.work / 'waydroid.prop').read_text())
        self.save('galaxy-s24-ultra')
        for name, props in self.initial.items():
            self.assertEqual(device.parse((self.work / name).read_text())['ro.build.fingerprint'], props['ro.build.fingerprint'])
        self.save('pixel-7')
        # An unrelated change while spoofed must survive restoration.
        path = self.work / 'waydroid.prop'
        path.write_text(path.read_text() + 'persist.example=changed\n')
        self.save('default')
        for name, props in self.initial.items(): self.assertEqual(device.parse((self.work / name).read_text()), props)
        cfg = self.adapter.config()
        self.assertEqual(cfg.get('properties','ro.product.model'), 'Config model')
        self.assertEqual(cfg.get('properties','ro.hardware.gralloc'), 'gbm')
        self.assertIn('ro.product.cpu.abilist=x86_64,arm64-v8a', path.read_text())
        self.assertIn('persist.example=changed', path.read_text())
        self.assertNotIn('device_spoof', self.adapter.saved())
        self.assertEqual(self.adapter.saved()['arm_properties'], {'keep':'arm'})

    def test_new_baseline_is_captured_after_reset_and_survives_adapter_restart(self):
        self.save('pixel-5'); self.save('default')
        path = self.work / 'waydroid_base.prop'
        path.write_text('ro.product.model=New base\n')
        self.save('pixel-7')
        self.adapter = existing_module.ExistingRuntime(self.store, self.work)
        with patch.object(self.adapter, 'state', return_value='STOPPED'):
            self.save('default')
        self.assertEqual(device.parse(path.read_text()), {'ro.product.model':'New base'})

    def test_unmanaged_other_owner_and_running_default_are_not_mutated(self):
        before = {path:path.read_bytes() for path in self.work.iterdir()}
        with self.assertRaisesRegex(RuntimeError, 'another desktop user'):
            device.default_info(self.adapter, UID + 1)
        self.state.return_value = 'RUNNING'
        with self.assertRaisesRegex(RuntimeError, 'Stop the Waydroid'):
            self.save('pixel-5')
        self.state.return_value = 'STOPPED'
        self.adapter.metadata.unlink()
        with patch.object(existing_module, 'session_info', return_value={'user_id':str(UID)}):
            with self.assertRaisesRegex(RuntimeError, 'Enable management'):
                device.default_set(self.adapter, UID, 'pixel-5', None, 'invalid')
        self.assertEqual({path:path.read_bytes() for path in self.work.iterdir()}, before)

    def test_stale_revision_after_external_edit_does_not_write(self):
        revision = device.default_info(self.adapter, UID)['revision']
        path = self.work / 'waydroid.prop'
        path.write_text(path.read_text().replace('Generated model','External model'))
        before = path.read_text()
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            device.default_set(self.adapter, UID, 'pixel-7', None, revision)
        self.assertEqual(path.read_text(), before)

    def test_configuration_and_metadata_roll_back_together_on_write_failure(self):
        before = {path:path.read_bytes() for path in (*self.work.iterdir(), self.adapter.metadata)}
        real_replace = self.adapter.replace
        def replace(path, text):
            if path == self.adapter.metadata: raise OSError('fixture metadata write failure')
            real_replace(path, text)
        with patch.object(self.adapter, 'replace', side_effect=replace):
            with self.assertRaises(OSError): self.save('pixel-5')
        self.assertEqual({path:path.read_bytes() for path in before}, before)

    def test_missing_generated_file_uses_cfg_and_base_without_creating_it(self):
        (self.work / 'waydroid.prop').unlink()
        self.save('pixel-5'); self.save('default')
        self.assertFalse((self.work / 'waydroid.prop').exists())
        self.assertEqual(self.adapter.config().get('properties','ro.product.model'), 'Config model')


if __name__ == '__main__':
    unittest.main()
