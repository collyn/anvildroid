#!/usr/bin/env python3
"""Contract checks for controller/worker integration, without starting Android."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('x11', Path(__file__).with_name('runtime-x11.py'))
x11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(x11)


class Integration(unittest.TestCase):
    def test_default_and_explicit_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(x11.selection(root)['backend'], 'auto')
            value = dict(backend='native-x11', presenter='intel')
            (root / 'display-backend.json').write_text(json.dumps(value))
            self.assertEqual(x11.selection(root), value)
            (root / 'display-backend.json').write_text('{"backend":"native-x11","presenter":"../../bin"}')
            with self.assertRaisesRegex(RuntimeError, 'Invalid display'):
                x11.selection(root)

    def test_auto_uses_native_on_x11_and_preserves_wayland(self):
        auto = dict(backend='auto', presenter='amd')
        self.assertEqual(x11.resolve_backend(auto, True)['backend'], 'native-x11')
        self.assertEqual(x11.resolve_backend(auto, False)['backend'], 'legacy')
        self.assertEqual(auto['backend'], 'auto')
        compatibility = dict(backend='legacy', presenter='intel')
        self.assertEqual(x11.resolve_backend(compatibility, True), compatibility)

    def test_unsupported_auto_does_not_launch_weston(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'support').mkdir()
            (root / 'support/compatibility.json').write_text('{"sdk":36}')
            backend = x11.resolve_backend(x11.selection(root), True)
            self.assertEqual(backend['backend'], 'native-x11')
            with patch.object(x11, 'load_payload') as load:
                with self.assertRaisesRegex(RuntimeError, 'composer contract.*SDK 36.*Weston was not started'):
                    x11.preflight(root, False)
                load.assert_not_called()

    def test_preflight_requires_supported_image_and_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'support').mkdir()
            path = root / 'support/compatibility.json'
            with patch.object(x11, 'load_payload', return_value=({}, 'verified')) as verify:
                path.write_text('{"sdk":36}')
                with self.assertRaisesRegex(RuntimeError, 'NVIDIA/Venus requires the Waydroid'):
                    x11.preflight(root, True)
                path.write_text('{"sdk":33}')
                with self.assertRaisesRegex(RuntimeError, 'composer contract'):
                    x11.preflight(root, False)
                verify.assert_not_called()
                self.assertEqual(x11.preflight(root, True), ({}, 'verified'))
                verify.assert_called_once_with(x11.INSTALL)

    def test_sdk36_mesa_uses_verified_app_composer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'support').mkdir()
            path = root / 'support/compatibility.json'
            report = dict(sdk=36, flavor='CUSTOM', desktop_bridge='anvildroid',
                          software_renderer='angle-pastel', app_window_contract='waydroid-hidl')
            path.write_text(json.dumps(report))
            with patch.object(x11, 'load_payload', return_value=({}, 'verified')) as verify:
                self.assertEqual(x11.preflight(root, False), ({}, 'verified'))
                self.assertEqual(x11.preflight(root, True), ({}, 'verified'))
                self.assertEqual(verify.call_count, 2)
                report['desktop_bridge'] = 'native-wayland'
                path.write_text(json.dumps(report))
                with self.assertRaisesRegex(RuntimeError, 'composer contract'):
                    x11.preflight(root, False)



class Authority(unittest.TestCase):
    def test_input_environment_is_scoped_to_desktop_user_and_display(self):
        import os
        spec = importlib.util.spec_from_file_location('desktop_input', Path(__file__).with_name('runtime-desktop.py'))
        desktop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(desktop)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number, environment in {
                '1': b'DISPLAY=:0\0LANG=vi_VN.UTF-8\0XMODIFIERS=@im=fcitx\0LD_PRELOAD=/unsafe.so\0PATH=/unsafe\0',
                '2': b'DISPLAY=:1\0LANG=C\0XMODIFIERS=@im=wrong-session\0',
                '3': b'DISPLAY=:0\0LC_ALL=bad\nlocale\0XMODIFIERS=' + b'x' * 257 + b'\0',
            }.items():
                process = root / number
                process.mkdir()
                (process / 'environ').write_bytes(environment)
            with patch.object(desktop, 'Path', return_value=root):
                self.assertEqual(desktop.x11_input_environment(os.getuid(), ':0'),
                                 {'LANG': 'vi_VN.UTF-8', 'XMODIFIERS': '@im=fcitx'})
                self.assertEqual(desktop.x11_input_environment(os.getuid() + 1, ':0'),
                                 {'LANG': 'C.UTF-8'})

    def test_cookie_read_rejects_links_and_nonregular_files(self):
        spec = importlib.util.spec_from_file_location('desktop', Path(__file__).with_name('runtime-desktop.py'))
        desktop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(desktop)
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cookie = root / 'cookie'
            cookie.write_bytes(b'cookie')
            cookie.chmod(0o600)
            self.assertEqual(desktop._authority_bytes(cookie, os.getuid()), b'cookie')
            with self.assertRaises(OSError): desktop._authority_bytes(cookie, os.getuid() + 1)
            link = root / 'link'
            link.symlink_to(cookie)
            with self.assertRaises(OSError): desktop._authority_bytes(link, os.getuid())
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            with self.assertRaises(OSError): desktop._authority_bytes(fifo, os.getuid())
            cookie.chmod(0o666)
            with self.assertRaises(OSError): desktop._authority_bytes(cookie, os.getuid())


class AppInventory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('worker_apps', Path(__file__).with_name('runtime-worker.py'))
        cls.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.worker)

    def test_app_windows_use_common_android_launcher_inventory(self):
        worker = self.worker
        packages = 'com.android.settings/.Settings\ncom.example.game/.Main\ncom.example.game/.Other\n'
        with patch.object(worker, 'run', return_value=packages) as run, \
             patch.object(worker, 'launcher_catalog', return_value={
                 'com.android.settings': {'label': 'Settings'},
                 'com.example.game': {'label': 'Game'}}):
            apps = worker.list_apps(Path('/unused'), 'test', single_display=False)
        self.assertEqual([a['package'] for a in apps], ['com.android.settings', 'com.example.game'])
        self.assertEqual([a['label'] for a in apps], ['Settings', 'Game'])
        self.assertTrue(all(a['launchable'] for a in apps))
        self.assertIn('query-activities', run.call_args.args)

    def test_android16_reactivation_preserves_existing_freeform_bounds(self):
        tasks = ('RootTask id=16 bounds=[0,32][448,736]\n'
                 ' configuration={winConfig={mWindowingMode=freeform}}\n'
                 ' taskId=16: com.android.settings/.Settings bounds=[0,32][448,736]\n'
                 'RootTask id=17 bounds=[0,0][1984,1280]\n'
                 ' configuration={winConfig={mWindowingMode=fullscreen}}\n'
                 ' taskId=17: com.example.game/.Main bounds=[0,0][1984,1280]\n')
        self.assertEqual(self.worker.freeform_launch_arguments(tasks, 'com.android.settings'), [])
        for package in ('com.example.game', 'com.example.new', 'com.android.setting'):
            self.assertEqual(self.worker.freeform_launch_arguments(tasks, package), ['--windowingMode', '5'])

    def test_desktop_power_timeout_is_restored_when_switching_headless(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shell = Mock(return_value='30000')
            self.worker.configure_desktop_power(root, shell, True)
            shell.assert_any_call('settings', '--user', '0', 'put', 'system', 'screen_off_timeout', '2147483647')
            # Repeated setup must keep the original timeout.
            shell.return_value = '2147483647'
            self.worker.configure_desktop_power(root, shell, True)
            self.worker.configure_desktop_power(root, shell, False)
            shell.assert_called_with('settings', '--user', '0', 'put', 'system', 'screen_off_timeout', '30000')
            self.assertFalse((root / 'x11-screen-timeout.json').exists())
            shell.reset_mock()
            self.worker.configure_desktop_power(root, shell, False)
            shell.assert_not_called()

    def test_input_recovery_does_not_touch_normal_app_focus(self):
        lxc = Mock(return_value='mCurrentFocus=Window{123 u0 com.example.app/.Main}')
        state = {}
        self.worker.recover_desktop_input(lxc, 'runtime-test', state)
        self.assertEqual(lxc.call_count, 1)
        self.assertEqual(state, {})

    def test_input_recovery_reports_only_verified_build_warning(self):
        for result, warned in [('ANVILDROID_BUILD_WARNING_ACKNOWLEDGED\n', True),
                               ('ANVILDROID_INPUT_NO_BUILD_WARNING\n', False)]:
            lxc = Mock(side_effect=['mCurrentFocus=Window{123 u0 Android System}', result])
            state = {}
            with patch.object(self.worker, 'shutdown_environment', return_value=['--clear-env']):
                self.worker.recover_desktop_input(lxc, 'runtime-test', state)
            self.assertEqual('desktop_input_warning' in state, warned)
            self.assertIn('acknowledge-build-warning', lxc.call_args.args)
            self.assertEqual(lxc.call_args.kwargs['timeout'], 8)

    def test_wayland_single_display_inventory_is_unchanged(self):
        with patch.object(self.worker, 'run') as run:
            apps = self.worker.list_apps(Path('/unused'), 'test', single_display=True)
        self.assertEqual(apps, [dict(package='org.anvildroid.desktop', label='Android',
                                     launchable=True, desktop_entry=True)])
        run.assert_not_called()

    def test_app_window_force_stop_uses_common_android_action(self):
        with patch.object(self.worker, 'run', side_effect=['com.example.game/.Main\n', '']) as run:
            result = self.worker.app_action(Path('/unused'), 'test', 'force_stop', 'com.example.game',
                                             desktop=True, single_display=False)
        self.assertEqual(result['action'], 'force_stop')
        self.assertEqual(run.call_args.args[-5:], ('/system/bin/am', 'force-stop', '--user', '0', 'com.example.game'))

    def test_app_window_uninstall_keeps_system_app_protection(self):
        with patch.object(self.worker, 'run', side_effect=['com.android.settings/.Settings\n', '']), \
             patch.object(self.worker.linux, 'run_bounded') as uninstall:
            with self.assertRaisesRegex(RuntimeError, 'system or protected'):
                self.worker.app_action(Path('/unused'), 'test', 'uninstall', 'com.android.settings',
                                       desktop=True, single_display=False)
        uninstall.assert_not_called()


if __name__ == '__main__':
    unittest.main()
