"""App clones: naming, request validation and Android parsing; never starts Android."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('clones_controller', Path(__file__).with_name('runtime-controller.py'))
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
worker = controller.worker_module
android = controller.android_module

USERS = """3 users:

0: id=0, name=Owner, type=full.SYSTEM, flags=ADMIN|FULL|INITIALIZED|PRIMARY|SYSTEM (running) (current)
1: id=10, name=anvilclone:com.example.game, type=profile.CLONE, flags=PROFILE (parentId=0) (running)
2: id=11, name=Clone 2, type=profile.CLONE, flags=PROFILE (parentId=0)
3: id=12, name=anvilclone:com.example.game, type=profile.MANAGED, flags=PROFILE (parentId=0)
4: id=13, name=anvilclone:com.example.chat, type=profile.CLONE, flags=PROFILE (parentId=0)
"""


class Parsing(unittest.TestCase):
    def test_only_anvildroid_clone_profiles_are_clones(self):
        self.assertEqual(worker.parse_clone_profiles(USERS), {10: 'com.example.game', 13: 'com.example.chat'})

    def test_process_states_are_kept_per_android_user(self):
        dump = ('ACTIVITY MANAGER RUNNING PROCESSES\n'
                '  *APP* UID 10147 ProcessRecord{a 1:com.example.game/u0a147}\n'
                '    user #0 uid=10147 gids={}\n    packageList={com.example.game}\n'
                '  *APP* UID 1010147 ProcessRecord{b 2:com.example.game/u10a147}\n'
                '    user #10 uid=1010147 gids={}\n    packageList={com.example.game}\n'
                '    foregroundActivities=true\n')
        with patch.object(worker.linux, 'run_bounded', return_value=dump):
            states = worker.app_execution_states(Path('/nonexistent'), 'r-x')
        self.assertEqual(states, {'com.example.game': 'background', 'com.example.game@10': 'foreground'})

    def test_exit_info_is_filtered_by_user(self):
        output = ('ApplicationExitInfo #0:\n timestamp=2026-01-01 00:00:00.0 pid=7 realUid=1 user=10\n'
                  ' process=com.example.game reason=6 (CRASH_NATIVE) subreason=0 status=11\n')
        self.assertEqual(worker.parse_app_exits(output, 'com.example.game'), {})
        self.assertEqual(len(worker.parse_app_exits(output, 'com.example.game', 10)), 1)


class Requests(unittest.TestCase):
    def base(self, action, **extra):
        return dict({'op': 'app_action', 'id': 'r-' + '1' * 32, 'action': action, 'package': 'com.example.game'}, **extra)

    def test_valid_combinations(self):
        self.assertEqual(controller.validate_clone_request(self.base('launch')), (0, None))
        self.assertEqual(controller.validate_clone_request(self.base('launch', user=10)), (10, None))
        self.assertEqual(controller.validate_clone_request(self.base('clone', name='  My   alt ')), (0, 'My alt'))
        self.assertEqual(controller.validate_clone_request(self.base('clone_remove', user=10)), (10, None))
        self.assertEqual(controller.validate_clone_request(self.base('clone_rename', user=10, name='')), (10, ''))
        self.assertEqual(controller.validate_clone_request(self.base('clone', name='two\nlines')), (0, 'two lines'))

    def test_invalid_combinations(self):
        for request in (self.base('launch', user=0), self.base('uninstall', user=10), self.base('clone'),
                        self.base('clone', user=10, name=''), self.base('clone_remove'), self.base('clone_remove', user=10, name='x'),
                        self.base('launch', name='x'), self.base('launch', user=True), self.base('launch', user='10'),
                        self.base('clone', name='x' * 65), self.base('clone', name='bad\x00name'), self.base('clone', name=3)):
            with self.subTest(request=request), self.assertRaises(controller.RequestError):
                controller.validate_clone_request(request)


class Naming(unittest.TestCase):
    def test_default_names_use_smallest_free_number(self):
        clones = {'10': {'package': 'com.example.game', 'name': 'Game (2)'},
                  '11': {'package': 'com.example.game', 'name': 'Game (4)'},
                  '12': {'package': 'com.example.chat', 'name': 'Game (3)'}}
        self.assertEqual(controller.default_clone_name('Game', 'com.example.game', clones), 'Game (3)')
        # Renaming a clone back to its default must not count its own name.
        self.assertEqual(controller.default_clone_name('Game', 'com.example.game', clones, 10), 'Game (2)')

    def test_listing_names_clones_and_forgets_removed_ones(self):
        apps = [{'package': 'com.example.game', 'label': 'Game', 'launchable': True, 'third_party': True},
                {'package': 'com.example.game', 'label': 'Game', 'launchable': True, 'clone': True, 'user': 10, 'third_party': True},
                {'package': 'com.example.game', 'label': 'Game', 'launchable': True, 'clone': True, 'user': 14, 'third_party': True}]
        saved = {'10': {'package': 'com.example.game', 'name': 'Main alt'},
                 '11': {'package': 'com.example.game', 'name': 'Removed'}}
        named, kept = controller.name_app_clones(apps, saved)
        self.assertEqual([app['label'] for app in named], ['Game', 'Main alt', 'Game (2)'])
        self.assertEqual(named[1]['original_label'], 'Game')
        self.assertEqual(set(kept), {'10', '14'})

    def test_failed_inventory_keeps_saved_names(self):
        apps = [{'package': 'com.example.game', 'label': 'Game', 'launchable': True}]
        saved = {'10': {'package': 'com.example.game', 'name': 'Main alt'}}
        self.assertEqual(controller.name_app_clones(apps, saved)[1], saved)

    def test_malformed_saved_state_is_dropped_not_fatal(self):
        runtime = 'r-' + '1' * 32
        data = {runtime: {'10': {'package': 'com.example.game', 'name': 'Alt'}, '0': {'package': 'com.example.game', 'name': 'x'},
                          'x': {}, '11': {'package': '../etc', 'name': 'x'}},
                'r-' + '2' * 32: {'10': {'package': 'com.example.game', 'name': 'Gone runtime'}}, 'bad': []}
        self.assertEqual(controller.load_app_clones(data, {runtime}), {runtime: {'10': {'package': 'com.example.game', 'name': 'Alt'}}})
        self.assertEqual(controller.load_app_clones([], {runtime}), {})


class Overlay(unittest.TestCase):
    def test_overlay_is_copied_into_vendor_upper_and_refreshed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'AnvilDroidUsers.apk'
            vendor = root / 'vendor'
            vendor.mkdir()
            self.assertFalse(worker.install_clone_overlay(vendor, source))
            source.write_bytes(b'PK\x03\x04one')
            self.assertTrue(worker.install_clone_overlay(vendor, source))
            target = vendor / 'overlay/AnvilDroidUsers/AnvilDroidUsers.apk'
            self.assertEqual(target.read_bytes(), b'PK\x03\x04one')
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            source.write_bytes(b'PK\x03\x04two')
            self.assertTrue(worker.install_clone_overlay(vendor, source))
            self.assertEqual(target.read_bytes(), b'PK\x03\x04two')
            source.write_bytes(b'not an apk')
            self.assertFalse(worker.install_clone_overlay(vendor, source))

    def test_overlay_refuses_symlinked_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'AnvilDroidUsers.apk'
            source.write_bytes(b'PK\x03\x04')
            vendor = root / 'vendor'
            vendor.mkdir()
            (root / 'elsewhere').mkdir()
            (vendor / 'overlay').symlink_to(root / 'elsewhere')
            with self.assertRaises(RuntimeError):
                worker.install_clone_overlay(vendor, source)


class WorkerLifecycle(unittest.TestCase):
    def setUp(self):
        exits = patch.object(worker, 'app_exit_snapshot', return_value=None)
        exits.start(); self.addCleanup(exits.stop)

    def test_clone_lifecycle_uses_fixed_commands_and_only_third_party_apps(self):
        instance = Path('/store/instances/r-two')
        users = 'package:com.example.app\n__ANVIL_USERS__\n0: id=0, name=Owner, type=full.SYSTEM, flags=ADMIN (running)\n'
        slow = {('pm', 'create-user'): 'Success: created user id 12', ('am', 'start-user'): 'Success: user started',
                ('pm', 'install-existing'): 'Package com.example.app installed for user: 12', ('pm', 'remove-user'): 'Success: removed user'}
        def bounded(*args, **kwargs):
            return slow[(args[6].rsplit('/', 1)[1], args[7])]
        with patch.object(worker, 'run', side_effect=[users, 'com.example.app/.Main']), patch.object(worker.linux, 'run_bounded', side_effect=bounded) as calls:
            result = worker.app_action(instance, 'r-two', 'clone', 'com.example.app')
        self.assertEqual((result['action'], result['user']), ('clone', 12))
        self.assertEqual(calls.call_args_list[0].args[6:], ('/system/bin/pm', 'create-user', '--user-type', 'android.os.usertype.profile.CLONE', '--profileOf', '0', 'anvilclone:com.example.app'))
        self.assertEqual(calls.call_args_list[2].args[6:], ('/system/bin/pm', 'install-existing', '--user', '12', 'com.example.app'))
        # System apps are never cloned.
        with patch.object(worker, 'run', side_effect=['package:com.example.app\n__ANVIL_USERS__\n', 'com.android.settings/.Settings']), patch.object(worker.linux, 'run_bounded') as calls:
            with self.assertRaisesRegex(RuntimeError, 'third-party'): worker.app_action(instance, 'r-two', 'clone', 'com.android.settings')
            calls.assert_not_called()
        # A failed install discards the half-made profile.
        def failing(*args, **kwargs):
            if args[7] == 'install-existing': return 'Error: package not found'
            return bounded(*args, **kwargs)
        with patch.object(worker, 'run', side_effect=[users, 'com.example.app/.Main']), patch.object(worker.linux, 'run_bounded', side_effect=failing) as calls:
            with self.assertRaisesRegex(RuntimeError, 'could not add'): worker.app_action(instance, 'r-two', 'clone', 'com.example.app')
            self.assertEqual(calls.call_args_list[-1].args[6:], ('/system/bin/pm', 'remove-user', '12'))
        # Clone actions only touch AnvilDroid clone profiles of the same package.
        clone = users + '1: id=12, name=anvilclone:com.example.app, type=profile.CLONE, flags=PROFILE (parentId=0)\n'
        with patch.object(worker, 'run', side_effect=[clone]), patch.object(worker.linux, 'run_bounded', side_effect=bounded) as calls:
            self.assertEqual(worker.app_action(instance, 'r-two', 'clone_remove', 'com.example.app', user=12)['user'], 12)
            self.assertEqual(calls.call_args.args[6:], ('/system/bin/pm', 'remove-user', '12'))
        for target in (0, 11):
            with patch.object(worker, 'run', side_effect=[clone]), patch.object(worker.linux, 'run_bounded') as calls:
                with self.assertRaises(RuntimeError): worker.app_action(instance, 'r-two', 'clone_remove', 'com.example.app', user=target)
                calls.assert_not_called()
        with patch.object(worker, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'not available for app clones'): worker.app_action(instance, 'r-two', 'uninstall', 'com.example.app', user=12)
            run.assert_not_called()
        def launch(*args, **kwargs):
            command = args[6:]
            if command[:2] == ('/system/bin/sh', '-c') and 'pm list' in command[2]: return clone
            if command[:3] == ('/system/bin/cmd', 'package', 'resolve-activity'): return 'com.example.app/.Main'
            if command[:2] == ('/system/bin/am', 'start'): return 'Starting: Intent'
            return ''
        with patch.object(worker, 'run', side_effect=launch) as run, patch.object(worker.linux, 'run_bounded', side_effect=bounded) as calls:
            self.assertEqual(worker.app_action(instance, 'r-two', 'launch', 'com.example.app', user=12)['user'], 12)
            self.assertEqual(calls.call_args.args[6:], ('/system/bin/am', 'start-user', '-w', '12'))
            started = next(call for call in run.call_args_list if call.args[6:8] == ('/system/bin/am', 'start'))
            self.assertEqual(started.args[6:], ('/system/bin/am', 'start', '--user', '12', '-n', 'com.example.app/.Main'))


if __name__ == '__main__':
    unittest.main()
