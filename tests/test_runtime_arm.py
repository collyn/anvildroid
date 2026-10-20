"""Safety boundaries for the experimental, pinned ARM payload extractor."""
import hashlib
import importlib.util
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('arm', Path(__file__).resolve().parents[1] / 'services/runtime-arm.py')
arm = importlib.util.module_from_spec(spec); spec.loader.exec_module(arm)

class ArmPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'payload.zip'
        self.output = self.root / 'output'

    def make_archive(self, extra=()):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name in ('lib/libndk_translation.so', 'lib64/libndk_translation.so', 'etc/init/ndk_translation.rc', 'etc/binfmt_misc/arm_exe', *extra):
                archive.writestr(arm.PREFIX + name, b'fixture')
        checksum = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        mocked = patch.object(arm, 'SHA256', checksum); mocked.start(); self.addCleanup(mocked.stop)

    def test_rejects_unpinned_archive_before_publishing(self):
        self.archive.write_bytes(b'wrong')
        with self.assertRaisesRegex(RuntimeError, 'checksum'): arm.stage(self.archive, self.output)
        self.assertFalse(self.output.exists())

    def test_omits_host_binfmt_registration(self):
        self.make_archive()
        arm.stage(self.archive, self.output)
        self.assertTrue((self.output / 'system/lib/libndk_translation.so').is_file())
        self.assertTrue((self.output / 'system/lib64/libndk_translation.so').is_file())
        self.assertFalse((self.output / 'system/etc/init/ndk_translation.rc').exists())
        self.assertFalse((self.output / 'system/etc/binfmt_misc').exists())
        with self.assertRaisesRegex(RuntimeError, 'already exists'): arm.stage(self.archive, self.output)

    def test_traversal_rolls_back_unpublished_payload(self):
        self.make_archive(('../escaped',))
        with self.assertRaisesRegex(RuntimeError, 'Unsafe'): arm.stage(self.archive, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.arm-*')))
        self.assertFalse((self.root / 'escaped').exists())

    def test_houdini_payload_identifies_engine_and_drops_host_init(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            for name in ('lib/libhoudini.so', 'lib64/libhoudini.so', 'lib/arm/libc.so',
                         'etc/init/houdini.rc', 'etc/binfmt_misc/arm_exe'):
                archive.writestr('houdini-release/prebuilts/' + name, b'fixture')
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        arm.stage(self.archive, self.output, digest)
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['engine'], 'libhoudini')
        self.assertTrue((self.output / 'system/lib64/libhoudini.so').is_file())
        self.assertFalse((self.output / 'system/etc/init/houdini.rc').exists())
        self.assertFalse((self.output / 'system/etc/binfmt_misc').exists())

    def test_mixed_or_partial_engines_are_rejected(self):
        for names in [('system/lib/libhoudini.so',),
                      ('system/lib/libhoudini.so','system/lib64/libhoudini.so',
                       'system/lib/libndk_translation.so','system/lib64/libndk_translation.so')]:
            with self.subTest(names=names):
                with zipfile.ZipFile(self.archive, 'w') as archive:
                    for name in names: archive.writestr(name,b'fixture')
                digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
                with self.assertRaisesRegex(RuntimeError, 'Incomplete or mixed'):
                    arm.stage(self.archive,self.output,digest)
                self.assertFalse(self.output.exists())

    def variant_fixture(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('system/lib/libndk_translation.so', b'fixture32')
            archive.writestr('system/lib64/libndk_translation.so', b'old-library')
        blob = arm._source.mark_variant(self.archive.read_bytes(), 'fixture-patched')
        self.archive.write_bytes(blob)
        return {'sha256': hashlib.sha256(blob).hexdigest(), 'engine':'libndk',
                'version':'Fixture patched', 'label':'libndk / Fixture patched', 'library_version':'0.2.3',
                'patches':[(0, '6f6c64', '6e6577')], 'patch_sha256':hashlib.sha256(b'new-library').hexdigest()}

    def test_known_variant_stages_patched_bytes_and_records_their_hash(self):
        entry = self.variant_fixture()
        with patch.object(arm, 'CATALOG', [entry]):
            arm.stage(self.archive, self.output, entry['sha256'])
        self.assertEqual((self.output / 'system/lib64/libndk_translation.so').read_bytes(), b'new-library')
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['files']['system/lib64/libndk_translation.so'], entry['patch_sha256'])
        self.assertEqual(manifest['version'], entry['version'])

    def test_failed_patch_leaves_no_published_payload(self):
        entry = self.variant_fixture()
        for changes in ({'patches':[(0,'626164','6e6577')]}, {'patch_sha256':'0'*64}):
            with self.subTest(changes=changes), patch.object(arm, 'CATALOG', [dict(entry, **changes)]):
                with self.assertRaisesRegex(RuntimeError, 'patch'): arm.stage(self.archive, self.output, entry['sha256'])
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.root.glob('.arm-*')))

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_variant_verification_requires_the_pinned_patched_library_hash(self):
        entry = self.variant_fixture()
        with patch.object(arm, 'CATALOG', [entry]):
            arm.stage(self.archive, self.output, entry['sha256'])
            arm.verify(self.output, entry['sha256'])
            (self.output / 'system/lib64/libndk_translation.so').write_bytes(b'old-library')
            manifest_path = self.output / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['files']['system/lib64/libndk_translation.so'] = hashlib.sha256(b'old-library').hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, 'patched library checksum'): arm.verify(self.output)

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_failed_patch_switch_preserves_active_config_and_previous_version(self):
        (self.root / 'support').mkdir(); (self.root / 'support/images.json').write_text('{}')
        def atomic(path, value):
            path.write_text(json.dumps(value)); path.chmod(0o600)
        self.make_archive()
        arm.configure(self.root, True, {}, atomic, self.archive)
        old_config = (self.root / 'arm.json').read_bytes()
        old_payload = arm.active_layer(self.root)
        entry = self.variant_fixture()
        entry['patch_sha256'] = '0' * 64
        with patch.object(arm, 'CATALOG', [entry]):
            with self.assertRaisesRegex(RuntimeError, 'patch'): arm.configure(self.root, True, {}, atomic, self.archive)
        self.assertEqual((self.root / 'arm.json').read_bytes(), old_config)
        self.assertEqual(arm.active_layer(self.root), old_payload)

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_custom_ndk_does_not_claim_the_default_library_version(self):
        self.make_archive()
        (self.root / 'support').mkdir(); (self.root / 'support/images.json').write_text('{}')
        def atomic(path, value):
            path.write_text(json.dumps(value)); path.chmod(0o600)
        arm.configure(self.root, True, {}, atomic, self.archive)
        self.assertNotIn('ro.ndk_translation.version', arm.runtime_properties(self.root))

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_engine_switch_changes_boot_properties_and_disable_removes_arm(self):
        (self.root / 'support').mkdir(); (self.root / 'support/images.json').write_text('{}')
        def atomic(path, value):
            path.write_text(json.dumps(value)); path.chmod(0o600)
        for engine, library in [('libhoudini','libhoudini.so'), ('libndk','libndk_translation.so')]:
            archive_path = self.root / (engine + '.zip')
            with zipfile.ZipFile(archive_path,'w') as archive:
                for directory in ('lib','lib64'): archive.writestr('system/'+directory+'/'+library,b'fixture')
            arm.configure(self.root,True,{},atomic,archive_path)
            props = arm.runtime_properties(self.root)
            self.assertEqual(props['ro.dalvik.vm.native.bridge'],library)
            if engine == 'libhoudini': self.assertNotIn('ro.ndk_translation.version', props)
        arm.configure(self.root,False,{},atomic)
        props = arm.runtime_properties(self.root)
        self.assertEqual(props['ro.dalvik.vm.native.bridge'],'0')
        self.assertNotIn('arm', props['ro.product.cpu.abilist'])

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_verification_rejects_corruption_and_empty_manifest(self):
        self.make_archive()
        arm.stage(self.archive, self.output)
        arm.verify(self.output)
        library = self.output / 'system/lib/libndk_translation.so'
        library.write_bytes(b'corrupt')
        with self.assertRaisesRegex(RuntimeError, 'integrity'): arm.verify(self.output)
        manifest = self.output / 'manifest.json'
        manifest.write_text(json.dumps({'archive_sha256': arm.SHA256, 'files': {}}))
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'): arm.verify(self.output)

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_verification_rejects_directory_symlink(self):
        self.make_archive()
        arm.stage(self.archive, self.output)
        library_dir = self.output / 'system/lib'
        library_dir.rename(self.output / 'elsewhere')
        library_dir.symlink_to('../elsewhere', target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'directory'): arm.verify(self.output)

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned runtime fixtures required')
    def test_enable_disable_preserves_payload_and_checks_image_pin(self):
        self.make_archive()
        support = self.root / 'support'; support.mkdir()
        images = {'system.img': 'system-hash', 'vendor.img': 'vendor-hash'}
        (support / 'images.json').write_text(json.dumps(images))
        def atomic(path, value):
            path.write_text(json.dumps(value)); path.chmod(0o600)
        with patch.object(arm, 'ARCHIVE', self.archive):
            arm.configure(self.root, True, images, atomic)
            self.assertEqual(arm.active_layer(self.root), self.root / 'arm-payload')
            original = (self.root / 'arm-payload/manifest.json').read_bytes()
            arm.configure(self.root, False, images, atomic)
            self.assertIsNone(arm.active_layer(self.root))
            self.assertEqual((self.root / 'arm-payload/manifest.json').read_bytes(), original)
            arm.configure(self.root, True, images, atomic)
            self.assertIsNotNone(arm.active_layer(self.root))
            (support / 'images.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'match runtime images'): arm.active_layer(self.root)

    @unittest.skipUnless(os.geteuid() == 0, 'Root controller fixtures required')
    def test_controller_checks_owner_state_and_worker_before_changing_arm(self):
        spec = importlib.util.spec_from_file_location('arm_controller_test', Path(__file__).resolve().parents[1] / 'services/runtime-controller.py')
        controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
        store = controller.Store(self.root, self.root, allow_temporary_storage=True)
        self.addCleanup(store.close)
        record = store.dispatch({'op': 'create', 'name': 'ARM fixture'}, 1000)
        identifier = record['id']
        record.update(state='Prepared', job={'id': 'a'*32, 'operation': 'prepare', 'status': 'Succeeded', 'error': None})
        store.replace(record)
        instance = store.backend.location(identifier); instance.mkdir(mode=0o700)
        (instance / 'support').mkdir()
        (instance / 'support/images.json').write_text('{}')
        store.selections[identifier] = {'images': {'system': {'filename': 'lineage-20.0-20260403-GAPPS-waydroid_x86_64-system.zip'}}}
        request = {'op': 'arm_set', 'id': identifier, 'enabled': True}
        with patch.object(controller.worker_module.arm, 'configure') as configure, patch.object(store.android, 'quiescent', return_value=True) as quiet:
            with self.assertRaises(controller.RequestError): store.dispatch(request, 2000)
            for state in ('Running', 'Starting', 'Stopping'):
                store.data['runtimes'][0]['state'] = state
                with self.assertRaises(controller.RequestError): store.dispatch(request, 1000)
            store.data['runtimes'][0]['state'] = 'Prepared'
            store.active_jobs.add(identifier)
            with self.assertRaises(controller.RequestError): store.dispatch(request, 1000)
            store.active_jobs.clear(); quiet.return_value = False
            with self.assertRaises(controller.RequestError): store.dispatch(request, 1000)
            configure.assert_not_called()
            quiet.return_value = True
            store.dispatch(request, 1000)
            self.assertEqual(configure.call_args.args[:3], (instance, True, {}))
            with self.assertRaises(controller.RequestError): store.dispatch(dict(request, enabled='true'), 1000)

    @unittest.skipUnless(os.geteuid() == 0, 'Root controller fixtures required')
    def test_default_arm_preparation_retry_and_reinstall(self):
        spec = importlib.util.spec_from_file_location('default_arm_controller', Path(__file__).resolve().parents[1] / 'services/runtime-controller.py')
        controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
        store = controller.Store(self.root, self.root, allow_temporary_storage=True)
        self.addCleanup(store.close)
        record = store.dispatch({'op': 'create', 'name': 'Default ARM'}, 1000)
        identifier = record['id']
        job = {'id': 'b'*32, 'operation': 'prepare', 'status': 'Running', 'error': None}
        store.replace(dict(record, state='Provisioning', job=job))
        instance = store.backend.location(identifier); instance.mkdir(mode=0o700)
        (instance / 'support').mkdir(); (instance / 'support/images.json').write_text('{}')
        store.selections[identifier] = {'flavor': 'GAPPS', 'images': {}, 'arm_default': True}
        self.archive.write_bytes(b'available')
        with patch.object(store.backend, 'inspect', return_value={'images': {}}), patch.object(controller.subprocess, 'run') as provision, patch.object(controller.worker_module.arm, 'ARCHIVE', self.archive), patch.object(controller.worker_module.arm, 'configure') as configure, patch.object(controller.worker_module.arm, 'ensure_default_archive', return_value=self.archive) as download:
            provision.return_value.returncode = 0
            store.prepare_job(identifier, job['id'])
            configure.assert_called_once()
            self.assertTrue(configure.call_args.args[1])
            self.assertEqual(configure.call_args.kwargs['archive'], self.archive)
            self.assertEqual(store.data['runtimes'][0]['state'], 'Prepared')
            configure.reset_mock()
            for enabled in (False, True):
                controller.storage_module.atomic(instance / 'arm.json', {'enabled': enabled, 'archive_sha256': controller.worker_module.arm.SHA256, 'images': {}})
                store.prepare_job(identifier, job['id'])
                configure.assert_not_called()
                self.assertEqual(controller.worker_module.arm.read_config(instance)['enabled'], enabled)
            marker = instance / 'app-data'; marker.write_bytes(b'preserve until preflight passes')
            controller.storage_module.atomic(instance / 'reinstall.json', {'selection': store.selections[identifier]})
            download.side_effect = RuntimeError('ARM package download failed')
            store.prepare_job(identifier, job['id'])
            self.assertEqual(marker.read_bytes(), b'preserve until preflight passes')
            configure.assert_not_called()
            download.side_effect = None
            with patch.object(store.android, 'quiescent', return_value=True):
                store.prepare_job(identifier, job['id'])
            self.assertFalse(marker.exists())
            configure.assert_called_once()
            self.assertEqual(configure.call_args.kwargs['archive'], self.archive)
            self.assertEqual(store.data['runtimes'][0]['state'], 'Prepared')
            self.archive.unlink()
            download.side_effect = RuntimeError('ARM package download failed')
            store.prepare_job(identifier, job['id'])
            self.assertEqual(store.data['runtimes'][0]['state'], 'Error')
            self.assertIn('ARM package download failed', store.data['runtimes'][0]['job']['error'])

    @unittest.skipUnless(os.geteuid() == 0, 'Root controller fixtures required')
    def test_default_houdini_cache_is_listed_and_selectable_without_user_import(self):
        spec=importlib.util.spec_from_file_location('houdini_default_controller',Path(__file__).resolve().parents[1]/'services/runtime-controller.py')
        controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
        store=controller.Store(self.root,self.root,allow_temporary_storage=True);self.addCleanup(store.close)
        cache=self.root/'libhoudini-hpe-com1.3.zip'
        with zipfile.ZipFile(cache,'w') as package:
            for lib in ('lib','lib64'):package.writestr(f'system/{lib}/libhoudini.so',b'Houdini fixture')
        digest=hashlib.sha256(cache.read_bytes()).hexdigest()
        source=dict(controller.worker_module.arm.DEFAULT_SOURCE,sha256=digest)
        record=store.dispatch({'op':'create','name':'Houdini cache'},1000)
        job={'id':'c'*32,'operation':'prepare','status':'Succeeded','error':None}
        store.replace(dict(record,state='Prepared',job=job))
        instance=store.backend.location(record['id']);(instance/'support').mkdir(parents=True)
        (instance/'support/images.json').write_text('{}')
        with patch.object(controller.worker_module.arm,'ARCHIVE',self.root/'libndk.zip'),patch.object(controller.worker_module.arm,'DEFAULT_SHA256',digest),patch.object(controller.worker_module.arm,'CATALOG',[source]),patch.object(store.android,'quiescent',return_value=True):
            cached=next(item for item in store.arm_sources(1000) if item['sha256']==digest)
            self.assertEqual(cached['engine'],'libhoudini');self.assertTrue(cached['installed'])
            report=store.dispatch({'op':'arm_set','id':record['id'],'enabled':True,'archive_sha256':digest},1000)
            self.assertTrue(report['enabled']);self.assertEqual(report['archive_sha256'],digest)
            props=controller.worker_module.arm.runtime_properties(instance)
            self.assertEqual(props['ro.dalvik.vm.native.bridge'],'libhoudini.so')
            self.assertNotIn('ro.ndk_translation.version',props)

    @unittest.skipUnless(os.geteuid() == 0, 'Root-owned payload verification required')
    def test_switch_source_preserves_previous_payload_and_failed_switch(self):
        self.make_archive()
        (self.root / 'support').mkdir()
        (self.root / 'support/images.json').write_text('{}')
        def atomic(path, value):
            path.write_text(json.dumps(value)); path.chmod(0o600)
        with patch.object(arm, 'ARCHIVE', self.archive):
            arm.configure(self.root, True, {}, atomic)
            original = arm.active_layer(self.root)
            old_config = (self.root / 'arm.json').read_bytes()
            broken = self.root / 'broken.zip'
            broken.write_bytes(b'invalid ZIP')
            with self.assertRaises(RuntimeError):
                arm.configure(self.root, True, {}, atomic, broken, 'Custom')
            self.assertEqual((self.root / 'arm.json').read_bytes(), old_config)
            self.assertEqual(arm.active_layer(self.root), original)
            custom = self.root / 'custom.zip'
            with zipfile.ZipFile(custom, 'w') as archive:
                for name in ('system/lib/libndk_translation.so', 'system/lib64/libndk_translation.so'):
                    archive.writestr(name, b'custom fixture')
            arm.configure(self.root, True, {}, atomic, custom, 'Custom')
            selected = arm.active_layer(self.root)
            self.assertNotEqual(original, selected)
            self.assertTrue(original.is_dir())
            self.assertIn('custom', arm.info(self.root, True)['provider'])
            arm.configure(self.root, False, {}, atomic)
            self.assertIsNone(arm.active_layer(self.root))
            self.assertTrue(selected.is_dir())
            arm.configure(self.root, True, {}, atomic)
            self.assertEqual(arm.active_layer(self.root), original)

    @unittest.skipUnless(os.geteuid() == 0, 'Root controller fixtures required')
    def test_import_is_user_scoped_retains_caller_fd_and_can_be_selected(self):
        spec = importlib.util.spec_from_file_location('source_controller_test', Path(__file__).resolve().parents[1] / 'services/runtime-controller.py')
        controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
        store = controller.Store(self.root, self.root, allow_temporary_storage=True)
        self.addCleanup(store.close)
        custom = self.root / 'custom.zip'
        with zipfile.ZipFile(custom, 'w') as archive:
            for name in ('system/lib/libndk_translation.so', 'system/lib64/libndk_translation.so'):
                archive.writestr(name, b'custom fixture')
        with custom.open('rb') as stream:
            imported = store.dispatch({'op': 'arm_import'}, 1000, stream.fileno())
            os.fstat(stream.fileno())
        self.assertIn(imported['sha256'], [s['sha256'] for s in store.dispatch({'op': 'arm_sources'}, 1000)])
        self.assertNotIn(imported['sha256'], [s['sha256'] for s in store.dispatch({'op': 'arm_sources'}, 2000)])
        path = self.root / 'arm-sources/1000' / (imported['sha256'] + '.zip')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        record = store.dispatch({'op': 'create', 'name': 'Import test'}, 1000)
        record.update(state='Prepared', job={'id':'c'*32, 'operation':'prepare', 'status':'Succeeded', 'error':None})
        store.replace(record)
        instance = store.backend.location(record['id']); instance.mkdir(mode=0o700)
        (instance / 'support').mkdir(); (instance / 'support/images.json').write_text('{}')
        store.selections[record['id']] = {'images':{'system':{'filename':'custom-system.zip'}}}
        with patch.object(store.android, 'quiescent', return_value=True):
            result = store.dispatch({'op':'arm_set', 'id':record['id'], 'enabled':True,
                                     'archive_sha256':imported['sha256']}, 1000)
        self.assertEqual(result['archive_sha256'], imported['sha256'])
        self.assertTrue(result['enabled'])

    @unittest.skipUnless(os.geteuid() == 0, 'Root controller fixtures required')
    def test_catalog_metadata_refreshes_a_previously_cached_release_label(self):
        spec = importlib.util.spec_from_file_location('metadata_controller_test', Path(__file__).resolve().parents[1] / 'services/runtime-controller.py')
        controller = importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)
        store = controller.Store(self.root, self.root, allow_temporary_storage=True)
        self.addCleanup(store.close)
        self.make_archive()
        with self.archive.open('rb') as stream:
            imported = store.dispatch({'op':'arm_import'}, 1000, stream.fileno())
        known = dict(sha256=imported['sha256'], engine='libndk', version='Zork R136 (0.2.3)',
                     label='libndk / Zork R136 (0.2.3)', android_sdks=[33])
        with patch.object(controller.worker_module.arm, 'CATALOG', [known]):
            refreshed = next(item for item in store.arm_sources(1000) if item['sha256'] == imported['sha256'])
        self.assertEqual(refreshed['version'], known['version'])
        self.assertEqual(refreshed['android_sdks'], [33])
        self.assertTrue(refreshed['installed'])

class AutomaticArchiveTests(unittest.TestCase):
    @unittest.skipUnless(os.geteuid()==0,'Pinned system archive requires root ownership')
    def test_houdini_default_has_separate_verified_cache_and_preserves_ndk(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            ndk=Path(folder)/'libndk.zip';ndk.write_bytes(b'keep explicit libndk')
            blob=b'verified Houdini fixture';digest=hashlib.sha256(blob).hexdigest()
            def download(url,output,expected):
                self.assertEqual(url,arm.DEFAULT_SOURCE['url'])
                self.assertEqual(expected,digest)
                output.write(blob)
            self.assertEqual(arm.DEFAULT_SOURCE['engine'],'libhoudini')
            self.assertEqual(arm.DEFAULT_SOURCE['sha256'],arm.DEFAULT_SHA256)
            self.assertNotIn('variant',arm.DEFAULT_SOURCE)
            with patch.object(arm,'ARCHIVE',ndk),patch.object(arm,'DEFAULT_SHA256',digest),patch.object(arm._source,'download',side_effect=download) as fetch:
                archive=arm.ensure_default_archive()
                self.assertNotEqual(archive,ndk)
                self.assertEqual(archive.read_bytes(),blob)
                self.assertEqual(arm.ensure_default_archive(),archive)
                self.assertEqual(arm.cached_archive(digest),archive)
                fetch.assert_called_once()
            self.assertEqual(ndk.read_bytes(),b'keep explicit libndk')

    def test_failed_houdini_download_keeps_previous_cache(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            with patch.object(arm,'ARCHIVE',Path(folder)/'libndk.zip'):
                archive=arm.default_archive();archive.write_bytes(b'previous')
                def fail(url,output,digest):
                    output.write(b'unverified');raise ValueError('SHA mismatch')
                with patch.object(arm._source,'download',side_effect=fail):
                    with self.assertRaisesRegex(ValueError,'SHA mismatch'):arm.ensure_default_archive()
                self.assertEqual(archive.read_bytes(),b'previous')
                self.assertFalse(list(Path(folder).glob('.arm-download-*')))

    @unittest.skipUnless(os.geteuid()==0,'Pinned system archive requires root ownership')
    def test_existing_verified_package_is_reused_without_download(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            archive=Path(folder)/'libndk.zip';archive.write_bytes(b'cached');archive.chmod(0o644)
            with patch.object(arm,'ARCHIVE',archive),patch.object(arm,'SHA256',hashlib.sha256(b'cached').hexdigest()),patch.object(arm._source,'download') as download:
                self.assertEqual(arm.ensure_archive(),archive)
                self.assertEqual(arm.ensure_archive(),archive)
            download.assert_not_called()
    def test_failed_download_keeps_previous_archive(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            archive=Path(folder)/'libndk.zip';archive.write_bytes(b'previous')
            def fail(url,output,digest):output.write(b'unverified');raise ValueError('SHA mismatch')
            with patch.object(arm,'ARCHIVE',archive),patch.object(arm._source,'download',side_effect=fail):
                with self.assertRaises(ValueError):arm.ensure_archive()
            self.assertEqual(archive.read_bytes(),b'previous')
            self.assertFalse(list(Path(folder).glob('.arm-download-*')))
    @unittest.skipUnless(os.geteuid()==0,'ARM configuration is root-owned')
    def test_prepared_image_pin_works_before_support_snapshot(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            root=Path(folder);(root/'prepared').mkdir()
            images={'system.img':'a'*64,'vendor.img':'b'*64}
            (root/'prepared/manifest.json').write_text(json.dumps({'images':images}))
            config={'enabled':True,'archive_sha256':'c'*64,'images':images}
            (root/'arm.json').write_text(json.dumps(config));(root/'arm.json').chmod(0o600)
            self.assertEqual(arm.read_config(root),config)
            (root/'support').mkdir();(root/'support/images.json').write_text(json.dumps({'system.img':'d'*64}))
            with self.assertRaisesRegex(RuntimeError,'does not match'):arm.read_config(root)

if __name__ == '__main__': unittest.main()
