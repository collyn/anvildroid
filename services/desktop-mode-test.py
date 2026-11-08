"""Desktop-only startup and upgrade regressions; never starts Android."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('desktop_controller', Path(__file__).with_name('runtime-controller.py'))
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class DesktopRequirements(unittest.TestCase):
    def test_prepare_without_session_but_start_requires_desktop(self):
        store = controller.Store.__new__(controller.Store)
        store.backend = Mock()
        store.backend.source_status.return_value = {'available': True, 'bytes': 1}
        store.downloads = Mock()
        store.downloads.status.return_value = {'available_images': []}
        store.existing = Mock()
        with patch.object(controller.shutil, 'which', return_value='/usr/bin/tool'), \
                patch.object(controller.host_module, 'missing_commands', return_value=[]), \
                patch.object(controller.host_module, 'inotify_check', return_value={'id': 'inotify', 'available': True}), \
                patch.object(controller.android_module.provision, 'managed_support_available', return_value=True):
            for available in (False, True):
                with patch.object(controller.desktop_module, 'capabilities', return_value={'desktop': available, 'detail': 'fixture'}):
                    report = store.requirements(1000)
                self.assertTrue(report['can_prepare'])
                self.assertEqual(report['can_start'], available)

    def test_login_start_uses_the_callers_display_and_only_their_runtimes(self):
        store = controller.Store.__new__(controller.Store)
        store.active_jobs = set()
        store.data = {'runtimes': [{'id': 'fixture', 'owner_uid': 1000, 'state': 'Stopped', 'start_on_boot': True},
                                   {'id': 'other', 'owner_uid': 2000, 'state': 'Stopped', 'start_on_boot': True},
                                   {'id': 'off', 'owner_uid': 1000, 'state': 'Stopped', 'start_on_boot': False}]}
        store._dispatch = Mock()
        for session, display in (({'desktop': True, 'session': 'x11', 'display': ':1'}, 'x11::1'),
                                 ({'desktop': True, 'session': 'wayland', 'socket': 'wayland-1'}, 'wayland-1')):
            store._dispatch.reset_mock()
            with patch.object(controller.desktop_module, 'capabilities', return_value=session):
                self.assertEqual(store.login_start(1000), {'started': ['fixture'], 'failed': []})
            store._dispatch.assert_called_once_with({'op': 'start', 'id': 'fixture', 'display': display}, 1000)
        # The login entry retries while the session is still coming up.
        with patch.object(controller.desktop_module, 'capabilities', return_value={'desktop': False}):
            with self.assertRaises(controller.RequestError) as raised:
                store.login_start(1000)
        self.assertEqual(raised.exception.code, 'NO_DESKTOP')
        # Nothing to start: no session probe, nothing started.
        with patch.object(controller.desktop_module, 'capabilities', side_effect=AssertionError):
            self.assertEqual(store.login_start(3000), {'started': [], 'failed': []})

    def test_adapter_rejects_missing_session_before_preparation(self):
        backend = controller.android_module.AndroidBackend(Mock())
        with patch.object(backend, 'instance', return_value=Path('/unused')), \
                patch.object(backend, 'status', return_value={'state': 'Stopped'}), \
                patch.object(backend, 'snapshot_support') as prepare:
            with self.assertRaisesRegex(RuntimeError, 'desktop session is required'):
                backend.start('fixture', 'generation')
            prepare.assert_not_called()

    def test_missing_weston_does_not_require_installation(self):
        with patch.object(controller.host_module.shutil, 'which', side_effect=lambda name: None if name == 'weston' else '/bin/' + name):
            self.assertEqual(controller.host_module.missing_commands(), [])
            self.assertEqual(controller.host_module.missing_packages(), [])


@unittest.skipUnless(os.geteuid() == 0, 'Root-owned controller store requires root')
class DesktopStore(unittest.TestCase):
    def test_default_migration_and_rejected_removed_modes(self):
        with tempfile.TemporaryDirectory(prefix='anvil-desktop-mode-') as directory:
            root = Path(directory)
            store = controller.Store(root, allow_temporary_storage=True)
            try:
                record = store.dispatch({'op': 'create', 'name': 'Desktop migration'}, 1000)
                identifier = record['id']
                self.assertEqual(store.dispatch({'op': 'display_info', 'id': identifier}, 1000)['mode'], 'desktop')
                controller.storage_module.atomic(root / 'display.json', {identifier: 'headless'})
            finally:
                store.close()
            store = controller.Store(root, allow_temporary_storage=True)
            try:
                self.assertEqual(json.loads((root / 'display.json').read_text()), {identifier: 'desktop'})
                for request in ({'op': 'display_set', 'mode': 'headless'},
                                {'op': 'backend_set', 'backend': 'legacy', 'presenter': 'amd'}):
                    with self.assertRaises(controller.RequestError) as error:
                        store.dispatch(dict(request, id=identifier), 1000)
                    self.assertEqual(error.exception.code, 'INVALID_REQUEST')
                with patch.object(store.android, 'start') as start:
                    with self.assertRaises(controller.RequestError) as error:
                        store.dispatch({'op': 'start', 'id': identifier}, 1000)
                    self.assertEqual(error.exception.code, 'DESKTOP_SESSION')
                    start.assert_not_called()
                self.assertEqual(store.dispatch({'op': 'inspect', 'id': identifier}, 1000)['name'], record['name'])
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
