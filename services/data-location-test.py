#!/usr/bin/env python3
"""Data folder and image cache relocation.
Mount tests run as root in a private namespace:
  sudo unshare --mount --propagation private python3 -B services/data-location-test.py
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, HERE / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storage = load('runtime_storage', 'runtime-storage.py')
download = load('runtime_download', 'runtime-download.py')


class DownloadBlockTest(unittest.TestCase):
    def test_block_rejects_writes_and_refuses_during_active_download(self):
        cache = download.Downloads.__new__(download.Downloads)
        cache.lock = __import__('threading').RLock()
        cache.state = {'status': 'Idle'}
        cache.blocked = None
        cache.block('Moving')
        with self.assertRaisesRegex(RuntimeError, 'Moving'): cache.check_writable()
        with self.assertRaisesRegex(RuntimeError, 'Moving'): cache.block('Again')
        cache.blocked = None
        cache.state = {'status': 'Downloading'}
        with self.assertRaisesRegex(RuntimeError, 'Wait for the current'): cache.block('Moving')
        self.assertIsNone(cache.blocked)


@unittest.skipUnless(os.geteuid() == 0, 'Mount integration requires root + private mount namespace')
class RelocationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-data-location-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = self.base / 'store'
        self.store.mkdir(mode=0o700)
        (self.store / 'instances').mkdir(mode=0o700)
        self.disk = self.base / 'disk'
        self.disk.mkdir(mode=0o755)
        self.other = self.base / 'other disk'
        self.other.mkdir(mode=0o755)
        self.identifier = 'r-' + 'a' * 32
        self.addCleanup(self.unmount_all)

    def unmount_all(self):
        for path in sorted(self.store.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            while storage.is_mountpoint(path): os.system(f"umount '{path}'")

    def test_new_runtime_storage_lives_in_data_folder_and_survives_restart(self):
        locations = storage.DataLocations(self.store, allow_temporary=True)
        location = locations.set(0, str(self.disk))
        runtimes = storage.Storage(self.store, allow_temporary=True)
        path = Path(runtimes.create_external(self.identifier, location['path'], 0, location))
        self.assertEqual(path.parent, self.disk)
        anchor = self.store / 'instances' / self.identifier
        self.assertTrue(storage.is_mountpoint(anchor))
        (anchor / 'system.img').write_bytes(b'image')
        self.assertEqual((path / 'system.img').read_bytes(), b'image')
        os.system(f"umount '{anchor}'")
        restarted = storage.Storage(self.store, allow_temporary=True)
        self.assertTrue(storage.is_mountpoint(anchor))
        self.assertEqual(restarted.report(self.identifier)['path'], str(path))
        for target in storage.deletion_plan(restarted, self.identifier): storage.delete_location(target)
        anchor.rmdir()
        self.assertFalse(path.exists())
        self.assertEqual(list(self.disk.iterdir()), [])

    def test_replaced_data_folder_is_refused_without_leftovers(self):
        locations = storage.DataLocations(self.store, allow_temporary=True)
        location = dict(locations.set(0, str(self.disk)), inode=1)
        runtimes = storage.Storage(self.store, allow_temporary=True)
        with self.assertRaisesRegex(RuntimeError, 'replaced'):
            runtimes.create_external(self.identifier, str(self.disk), 0, location)
        self.assertEqual(runtimes.entries, {})
        self.assertFalse((self.store / 'instances' / self.identifier).exists())
        self.assertEqual(list(self.disk.iterdir()), [])

    def test_data_folder_rejects_controller_store_and_resets(self):
        locations = storage.DataLocations(self.store, allow_temporary=True)
        with self.assertRaisesRegex(RuntimeError, 'outside the controller'):
            locations.set(0, str(self.store / 'instances'))
        locations.set(0, str(self.disk))
        self.assertEqual(storage.DataLocations(self.store, True).get(0)['path'], str(self.disk))
        self.assertTrue(locations.report(0)['available'])
        locations.set(0, None)
        self.assertIsNone(storage.DataLocations(self.store, True).get(0))

    def write_cache(self):
        cache = self.store / 'image-cache'
        cache.mkdir(mode=0o700)
        (cache / ('a' * 64 + '.zip')).write_bytes(b'zip' * 1000)
        (cache / 'library.json').write_text('{}')
        for file in cache.iterdir(): file.chmod(0o600)
        return cache

    def test_image_cache_moves_verified_and_old_copies_are_removed(self):
        cache = self.write_cache()
        images = storage.ImageCache(self.store, allow_temporary=True)
        phases = []
        images.move(str(self.disk), 0, phases.append)
        self.assertEqual(phases, ['copy', 'verify', 'switch', 'cleanup'])
        self.assertTrue(storage.is_mountpoint(cache))
        first = Path(images.state['active']['path'])
        self.assertEqual((first / 'library.json').read_text(), '{}')
        self.assertEqual(list((self.store / 'retired-storage').iterdir()), [])
        self.assertNotIn('previous', images.state)
        os.system(f"umount '{cache}'")
        self.assertIsNone(storage.ImageCache(self.store, allow_temporary=True).error)
        self.assertTrue(storage.is_mountpoint(cache))
        with self.assertRaisesRegex(RuntimeError, 'already in this folder'): images.move(str(self.disk), 0)
        images.move(str(self.other), 0)
        self.assertFalse(first.exists())
        self.assertEqual(((self.store / 'image-cache') / ('a' * 64 + '.zip')).read_bytes(), b'zip' * 1000)

    def test_interrupted_cutover_is_completed_on_restart(self):
        cache = self.write_cache()
        images = storage.ImageCache(self.store, allow_temporary=True)
        with patch.object(storage, 'bind_anchor', side_effect=RuntimeError('power loss')):
            with self.assertRaisesRegex(RuntimeError, 'power loss'): images.move(str(self.disk), 0)
        self.assertEqual(json.loads((self.store / 'image-cache-location.json').read_text())['phase'], 'switch')
        recovered = storage.ImageCache(self.store, allow_temporary=True)
        self.assertIsNone(recovered.error)
        self.assertTrue(storage.is_mountpoint(cache))
        self.assertEqual((cache / 'library.json').read_text(), '{}')
        self.assertEqual(list((self.store / 'retired-storage').iterdir()), [])

    def test_copy_failure_keeps_original_and_missing_disk_never_falls_back(self):
        cache = self.write_cache()
        images = storage.ImageCache(self.store, allow_temporary=True)
        with patch.object(storage, 'tree_manifest', side_effect=[{'a': 1}, {'b': 2}]):
            with self.assertRaisesRegex(RuntimeError, 'verification failed'): images.move(str(self.disk), 0)
        self.assertEqual(list(self.disk.iterdir()), [])
        self.assertFalse(storage.is_mountpoint(cache))
        self.assertEqual((cache / 'library.json').read_text(), '{}')
        images.move(str(self.disk), 0)
        os.system(f"umount '{cache}'")
        os.system(f"rm -rf '{images.state['active']['path']}'")
        missing = storage.ImageCache(self.store, allow_temporary=True)
        self.assertIsNotNone(missing.error)
        self.assertFalse(storage.is_mountpoint(cache))


@unittest.skipUnless(os.geteuid() == 0, 'Controller store integration requires root + private mount namespace')
class ControllerDataLocationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-data-controller-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.store_path = base / 'store'
        self.store_path.mkdir(mode=0o700)
        images = base / 'images'
        images.mkdir(mode=0o700)
        self.disk = base / 'disk'
        self.disk.mkdir(mode=0o755)
        controller = load('data_location_controller', 'runtime-controller.py')
        self.store = controller.Store(self.store_path, images, allow_temporary_storage=True)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.store.pool.shutdown(wait=True)
        for path in sorted(self.store_path.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            while storage.is_mountpoint(path): os.system(f"umount '{path}'")

    def test_new_devices_and_image_cache_use_the_data_folder(self):
        report = self.store.dispatch({'op': 'data_location_set', 'path': str(self.disk)}, 0)
        self.assertEqual(report['path'], str(self.disk))
        self.assertTrue(report['available'])
        record = self.store.dispatch({'op': 'create', 'name': 'Phone'}, 0)
        path = Path(self.store.dispatch({'op': 'storage_info', 'id': record['id']}, 0)['path'])
        self.assertEqual(path.parent, self.disk)
        self.assertTrue(storage.is_mountpoint(self.store_path / 'instances' / record['id']))
        self.store.dispatch({'op': 'image_cache_move'}, 0)
        self.store.pool.submit(lambda: None).result(timeout=30)
        for _ in range(300):
            report = self.store.dispatch({'op': 'data_location'}, 0)
            if report['cache']['job']['status'] != 'Running': break
            __import__('time').sleep(0.1)
        self.assertEqual(report['cache']['job']['status'], 'Succeeded', report)
        self.assertTrue(report['cache']['external'] and report['cache']['in_data_folder'])
        self.assertIsNone(self.store.downloads.blocked)
        self.store.dispatch({'op': 'data_location_set', 'path': None}, 0)
        second = self.store.dispatch({'op': 'create', 'name': 'Tablet'}, 0)
        self.assertEqual(self.store.dispatch({'op': 'storage_info', 'id': second['id']}, 0)['path'],
                         str(self.store_path / 'instances' / second['id']))

    def test_unavailable_data_folder_fails_create_without_a_record(self):
        self.store.dispatch({'op': 'data_location_set', 'path': str(self.disk)}, 0)
        self.disk.rmdir()
        self.disk.mkdir(mode=0o755)  # same path, different folder: never adopted
        with self.assertRaises(Exception) as failure:
            self.store.dispatch({'op': 'create', 'name': 'Phone'}, 0)
        self.assertEqual(failure.exception.code, 'STORAGE')
        self.assertEqual(self.store.dispatch({'op': 'list'}, 0), [])
        self.assertFalse(self.store.dispatch({'op': 'data_location'}, 0)['available'])
        with self.assertRaises(Exception):
            self.store.dispatch({'op': 'data_location_set', 'path': '/etc/../tmp'}, 0)


if __name__ == '__main__': unittest.main()
