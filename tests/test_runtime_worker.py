"""Permission regression under the system service's private umask."""
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('runtime_worker', Path(__file__).resolve().parents[1] / 'services/runtime-worker.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerPermissions(unittest.TestCase):
    def test_control_receiver_reconnects_one_child_with_bounded_backoff(self):
        now = [0.0]; stopping = threading.Event()
        children = [Mock(), Mock(), Mock()]
        for child in children: child.poll.return_value = None
        start = Mock(side_effect=children)
        receiver = worker.ControlReceiver(start, stopping, lambda: now[0])
        receiver.check(True); receiver.check(True)
        self.assertEqual(start.call_count, 1)
        children[0].poll.return_value = 1
        receiver.check(True)
        self.assertIn('disconnected', receiver.error)
        children[0].wait.assert_called_once_with()
        now[0] = .9; receiver.check(True)
        self.assertEqual(start.call_count, 1)
        now[0] = 1; receiver.check(True)
        self.assertEqual(start.call_count, 2)
        self.assertIsNone(receiver.error)
        children[1].poll.return_value = 1
        receiver.check(True)
        now[0] = 2.9; receiver.check(True)
        self.assertEqual(start.call_count, 2)
        now[0] = 3; receiver.check(True)
        self.assertEqual(start.call_count, 3)
        now[0] = 13; receiver.check(True)
        self.assertEqual(receiver.delay, 1)
        receiver.close()
        children[2].terminate.assert_called_once_with()
        children[2].wait.assert_called_once_with(timeout=3)

    def test_control_receiver_preserves_frozen_and_stopping_guests(self):
        stopping = threading.Event(); start = Mock()
        receiver = worker.ControlReceiver(start, stopping, lambda: 0)
        receiver.check(False); start.assert_not_called()
        stopping.set(); receiver.check(True); start.assert_not_called()
        receiver.close()

    def test_control_receiver_retries_launch_failure_without_busy_loop(self):
        now = [0]; stopping = threading.Event(); start = Mock(side_effect=RuntimeError('framework restarting'))
        receiver = worker.ControlReceiver(start, stopping, lambda: now[0])
        for _ in range(50): receiver.check(True)
        self.assertEqual(start.call_count, 1)
        self.assertIn('framework restarting', receiver.error)
        for stamp in (1, 3, 7, 15, 23):
            now[0] = stamp; receiver.check(True)
        self.assertEqual(start.call_count, 6)
        self.assertEqual(receiver.delay, 8)
        stopping.set(); now[0] = 100; receiver.check(True)
        self.assertEqual(start.call_count, 6)

    def test_control_receiver_reaps_exited_child_and_kills_only_unresponsive_child(self):
        child = Mock(); child.poll.return_value = None
        receiver = worker.ControlReceiver(lambda: child, threading.Event(), lambda: 0)
        receiver.check(True)
        child.wait.side_effect = [worker.subprocess.TimeoutExpired('receiver', 3), None]
        receiver.close(); child.kill.assert_called_once_with()
        self.assertIsNone(receiver.process)

    def test_desktop_watchdog_uses_private_alias_and_keeps_disconnect_failure(self):
        import socket
        # Long managed storage paths cannot be passed to AF_UNIX.connect.
        path = '/tmp/anvil-live-controls-wayland-20261018/instances/r-' + 'a' * 32 + '/android/desktop-socket'
        with socket.socket(socket.AF_UNIX) as real:
            with self.assertRaisesRegex(OSError, 'AF_UNIX path too long'):
                real.connect(path)
        with patch.object(worker.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            worker.check_desktop_session()
            probe.connect.assert_called_once_with('/run/anvil-xdg/wayland-probe')
            probe.settimeout.assert_called_once_with(1)
            failure = ConnectionRefusedError('closed compositor')
            probe.connect.side_effect = failure
            with self.assertRaisesRegex(RuntimeError, 'Desktop session disconnected') as caught:
                worker.check_desktop_session()
            self.assertIs(caught.exception.__cause__, failure)

    def test_imported_full_ui_session_does_not_open_on_managed_start(self):
        inherited = ['waydroid.background_start=false', 'waydroid.active_apps=Waydroid',
                     'waydroid.background_start = false',
                     'waydroid.xdg_runtime_dir=/run/user/2000',
                     'waydroid.wayland_display=wayland-1',
                     'persist.waydroid.multi_windows=true', 'ro.hardware.egl=angle']
        props = worker.managed_session_properties(inherited)
        self.assertEqual(props.count('waydroid.background_start=true'), 1)
        self.assertIn('waydroid.active_apps=none', props)
        self.assertIn('waydroid.xdg_runtime_dir=/run/xdg', props)
        self.assertIn('waydroid.wayland_display=wayland-probe', props)
        self.assertNotIn('waydroid.background_start=false', props)
        self.assertNotIn('waydroid.background_start = false', props)
        self.assertIn('persist.waydroid.multi_windows=true', props)
        self.assertIn('ro.hardware.egl=angle', props)
        self.assertEqual(worker.managed_session_properties(props), props)

    def test_android16_host_desktop_overlay_is_scoped_and_reversible(self):
        with tempfile.TemporaryDirectory() as directory:
            instance=Path(directory)
            calls=[]
            values=iter(['36','true','','','false','123'])
            def shell(*args):
                calls.append(args)
                return '' if args[0]=='kill' else next(values)
            worker.configure_host_desktop(instance,shell,True)
            self.assertTrue((instance/'host-desktop-overlay').exists())
            self.assertIn(('kill','-TERM','123'),calls)
            calls.clear()
            values=iter(['36','false','[x] com.android.shell:AnvilDroidHostDesktop'])
            worker.configure_host_desktop(instance,shell,True)
            self.assertFalse(any(c[0]=='kill' for c in calls))
            values=iter(['36','','123'])
            worker.configure_host_desktop(instance,shell,False)
            self.assertFalse((instance/'host-desktop-overlay').exists())
            self.assertIn(('cmd','overlay','disable','--user','0','com.android.shell:AnvilDroidHostDesktop'),calls)

    def test_binder_filesystem_reports_kernel_support(self):
        with patch.object(worker.shutil, 'which', return_value=None), \
             patch.object(Path, 'read_text', return_value='nodev\tbinder\n'), \
             patch.object(Path, 'mkdir') as mkdir, \
             patch.object(worker.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(worker.ensure_binder_filesystem(), Path('/dev/binderfs'))
            mkdir.assert_called_once_with(mode=0o755, exist_ok=True)
            run.assert_called_once_with(['mount', '-t', 'binder', 'binder', '/dev/binderfs'],
                                        capture_output=True, text=True)

    def test_private_binder_accepts_prepopulated_kernel_devices(self):
        import errno, stat
        metadata = SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_uid=0,
                                   st_dev=7, st_rdev=os.makedev(511, 1))
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'binder-control').touch()
                with patch.object(worker.os, 'fstat', return_value=metadata), \
                     patch.object(worker.fcntl, 'ioctl', side_effect=FileExistsError(errno.EEXIST, 'exists') if existing else None), \
                     patch.object(Path, 'lstat', return_value=metadata), \
                     patch.object(Path, 'chmod') as chmod:
                    worker.prepare_binder_devices(root)
                    self.assertEqual(chmod.call_count, 3)
                    self.assertTrue(all(call.args == (0o666,) for call in chmod.call_args_list))

    def test_private_binder_rejects_invalid_existing_devices_and_other_errors(self):
        import errno, stat
        control = SimpleNamespace(st_mode=stat.S_IFCHR, st_uid=0, st_dev=7,
                                  st_rdev=os.makedev(511, 1))
        for changes in ({'st_mode': stat.S_IFREG}, {'st_mode': stat.S_IFLNK},
                        {'st_uid': 1000}, {'st_dev': 8}, {'st_rdev': os.makedev(1, 3)}):
            metadata = SimpleNamespace(**(vars(control) | changes))
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'binder-control').touch()
                with patch.object(worker.os, 'fstat', return_value=control), \
                     patch.object(worker.fcntl, 'ioctl', side_effect=FileExistsError(errno.EEXIST, 'exists')), \
                     patch.object(Path, 'lstat', return_value=metadata), \
                     patch.object(Path, 'chmod') as chmod:
                    with self.assertRaisesRegex(RuntimeError, 'Invalid private BinderFS device'):
                        worker.prepare_binder_devices(root)
                    chmod.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'binder-control').touch()
            with patch.object(worker.os, 'fstat', return_value=control), \
                 patch.object(worker.fcntl, 'ioctl', side_effect=PermissionError(errno.EPERM, 'denied')):
                with self.assertRaisesRegex(RuntimeError, 'Cannot create BinderFS device binder'):
                    worker.prepare_binder_devices(root)

    def test_android_roots_remain_traversable_under_private_service_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mask = os.umask(0o077)
            try:
                upper = root / 'vendor-upper'
                upper.mkdir()
                self.assertEqual(upper.stat().st_mode & 0o777, 0o700)
                worker.prepare_overlay_upper(upper)  # repairs existing failed starts too
                worker.prepare_overlay_upper(root / 'system-upper')
            finally:
                os.umask(mask)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(upper.stat().st_mode & 0o777, 0o755)
            self.assertEqual((root / 'system-upper').stat().st_mode & 0o777, 0o755)

    def test_desktop_socket_rejects_paths_outside_the_selected_user_session(self):
        for path,uid in [('/tmp/fake-wayland',1000),('/run/user/1000/wayland-0',0),('/run/user/2000/wayland-0',1000),('/run/user/1000/../../tmp/wayland-0',1000)]:
            with self.assertRaises(RuntimeError): worker.pin_desktop_socket(path,uid)

    def test_desktop_socket_requires_protocols_and_closes_pin_on_failure(self):
        import stat
        import struct
        metadata = SimpleNamespace(st_uid=1000, st_mode=stat.S_IFSOCK,
                                   st_ino=123, st_dev=1)
        path = Path('/run/user/1000/wayland-test')
        for protocols, allowed in [({'xdg_wm_base': 6}, False),
                                   ({'xdg_wm_base': 6, 'zxdg_decoration_manager_v1': 1}, True),
                                   (dict.fromkeys(('xdg_wm_base', 'wl_compositor', 'wl_subcompositor', 'wl_shm'), 1), True),
                                   (dict.fromkeys(('xdg_wm_base', 'wl_compositor', 'wl_shm'), 1), False)]:
            with self.subTest(protocols=protocols), \
                 patch.object(Path, 'resolve', return_value=path), \
                 patch.object(Path, 'stat', return_value=metadata), \
                 patch.object(worker.os, 'open', return_value=42), \
                 patch.object(worker.os, 'fstat', return_value=metadata), \
                 patch.object(worker.os, 'close') as close, \
                 patch.object(worker.socket, 'socket') as socket_factory, \
                 patch.object(worker.desktop_module, 'registry_protocols', return_value=protocols) as probe:
                connection = socket_factory.return_value.__enter__.return_value
                connection.getsockopt.return_value = struct.pack('3i', 123, 1000, 1000)
                if allowed:
                    self.assertEqual(worker.pin_desktop_socket(path, 1000), 42)
                    close.assert_not_called()  # ownership transferred to caller
                else:
                    with self.assertRaisesRegex(RuntimeError, 'CSD'):
                        worker.pin_desktop_socket(path, 1000)
                    close.assert_called_once_with(42)
                probe.assert_called_once_with(connection)

    def test_android_overlay_disables_large_synthetic_inodes(self):
        options = worker.android_overlay_options('/lower', '/upper', '/work')
        self.assertEqual(options, 'lowerdir=/lower,upperdir=/upper,workdir=/work,xino=off,'
                                 'index=off,metacopy=off,redirect_dir=off,nfs_export=off')

    def test_mount_environment_is_scoped_to_child(self):
        before = dict(os.environ)
        environment = dict(before, LIBMOUNT_FORCE_MOUNT2='always')
        self.assertEqual(worker.linux.run(sys.executable, '-c',
            'import os; print(os.environ["LIBMOUNT_FORCE_MOUNT2"])', env=environment), 'always')
        self.assertEqual(dict(os.environ), before)

    def test_audio_pin_missing_socket_and_wrong_owner(self):
        with patch.object(Path, 'exists', return_value=False):
            self.assertIsNone(worker.pin_audio_socket(1000))
        with self.assertRaises(RuntimeError): worker.pin_audio_socket(0)
        path = Path('/run/user/1000/pulse/native')
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'resolve', return_value=path), \
             patch.object(Path, 'stat', return_value=SimpleNamespace(st_uid=2000)):
            with self.assertRaisesRegex(RuntimeError, 'owner'): worker.pin_audio_socket(1000)

    def test_audio_pin_validates_peer_and_closes_failed_pin(self):
        import stat, struct
        path = Path('/run/user/1000/pulse/native')
        metadata = SimpleNamespace(st_uid=1000, st_mode=stat.S_IFSOCK, st_ino=123, st_dev=1)
        for peer in (1000, 2000):
            with self.subTest(peer=peer), \
                 patch.object(Path, 'exists', return_value=True), \
                 patch.object(Path, 'resolve', return_value=path), \
                 patch.object(Path, 'stat', return_value=metadata), \
                 patch.object(worker.os, 'open', return_value=42), \
                 patch.object(worker.os, 'fstat', return_value=metadata), \
                 patch.object(worker.os, 'close') as close, \
                 patch.object(worker.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.getsockopt.return_value = struct.pack('3i', 1, peer, peer)
                if peer == 1000:
                    self.assertEqual(worker.pin_audio_socket(1000), 42)
                    close.assert_not_called()
                else:
                    with self.assertRaisesRegex(RuntimeError, 'identity'): worker.pin_audio_socket(1000)
                    close.assert_called_once_with(42)

    def test_desktop_keyboard_selection_and_headless_restore(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            instance=Path(directory)
            apk=instance/'desktop-ime.apk';apk.write_bytes(b'apk')
            (instance/'desktop-ime.sha256').write_text(hashlib.sha256(b'apk').hexdigest())
            selected=['com.android.inputmethod.latin/.LatinIME']
            def shell(*args):
                if args[:3]==('pm','list','packages'): return 'package:org.anvildroid.ime'
                if args[:2]==('settings','get'): return selected[0]
                if args[:2]==('ime','list'): return 'org.anvildroid.ime/.BridgeIme'
                if args[:2]==('ime','set'): selected[0]=args[2]
                return ''
            worker.configure_desktop_ime(instance,instance/'android',shell,True)
            self.assertEqual(selected[0],'org.anvildroid.ime/.BridgeIme')
            worker.configure_desktop_ime(instance,instance/'android',shell,False)
            self.assertEqual(selected[0],'com.android.inputmethod.latin/.LatinIME')
            self.assertFalse((instance/'desktop-previous-ime').exists())

    def test_missing_ime_installs_without_pm_path_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = Path(directory)
            (instance / 'desktop-ime.apk').write_bytes(b'apk')
            selected = ['com.android.inputmethod.latin/.LatinIME']
            installed = []
            def shell(*args, **kwargs):
                if args[:2] == ('pm', 'path'):
                    raise RuntimeError('lxc-attach failed: ')
                if args[:3] == ('pm', 'list', 'packages'):
                    return 'package:org.anvildroid.ime.other'
                if args[:2] == ('pm', 'install'):
                    installed.append(args)
                    return 'Success'
                if args[:2] == ('settings', 'get'): return selected[0]
                if args[:2] == ('ime', 'list'): return 'org.anvildroid.ime/.BridgeIme'
                if args[:2] == ('ime', 'set'): selected[0] = args[2]
                return ''
            worker.configure_desktop_ime(instance, instance / 'android', shell, True)
            self.assertEqual(len(installed), 1)
            self.assertEqual(selected[0], 'org.anvildroid.ime/.BridgeIme')
            self.assertTrue((instance / 'desktop-ime.sha256').exists())

    def test_ime_registration_waits_before_enable(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            instance = Path(directory)
            (instance / 'desktop-ime.apk').write_bytes(b'apk')
            (instance / 'desktop-ime.sha256').write_text(hashlib.sha256(b'apk').hexdigest())
            calls = []; lists = iter(['', 'org.anvildroid.ime/.BridgeIme'])
            selected = ['com.android.inputmethod.latin/.LatinIME']
            def shell(*args):
                calls.append(args)
                if args[:3] == ('pm', 'list', 'packages'): return 'package:org.anvildroid.ime'
                if args[:2] == ('ime', 'list'): return next(lists)
                if args[:2] == ('ime', 'set'): selected[0] = args[2]
                if args[:2] == ('settings', 'get'): return selected[0]
                return ''
            with patch.object(worker.time, 'sleep') as sleep:
                worker.configure_desktop_ime(instance, instance / 'android', shell, True)
                sleep.assert_called_once_with(.25)
            self.assertEqual([c[:2] for c in calls if c[0] == 'ime'], [('ime', 'list'), ('ime', 'list'), ('ime', 'enable'), ('ime', 'set')])

    def test_lxc_child_mask_does_not_change_private_parent_mask(self):
        mask = os.umask(0o077)
        try:
            observed = worker.linux.run(sys.executable, '-c', 'import os; print(oct(os.umask(0)))', umask=0o022)
            self.assertEqual(observed, '0o22')
            self.assertEqual(os.umask(0o077), 0o077)
        finally:
            os.umask(mask)

    def test_workarea_module_is_loaded(self):
        self.assertTrue(callable(getattr(worker.workarea, 'boot_props', None)))

    def test_workarea_probe_failure_never_breaks_props(self):
        with patch.object(worker.workarea, 'probe_report', side_effect=RuntimeError('no compositor')):
            props, info = worker.workarea.boot_props(['a=b'], '/run/user/1000/wayland-0', 1000)
        self.assertEqual(props, ['a=b'])
        self.assertIn('skipped', info)

    def test_workarea_probe_appends_exactly_one_canvas_line(self):
        with patch.object(worker.workarea, 'probe_report', return_value={'capacity': [1920, 1036]}):
            props, info = worker.workarea.boot_props(['a=b'], '/run/user/1000/wayland-0', 1000)
        self.assertEqual(props, ['a=b', 'persist.anvildroid.canvas=1920x1036'])
        self.assertEqual(info, {'capacity': [1920, 1036]})

    def test_nested_weston_x11_argv_and_env(self):
        with patch.dict(os.environ, {'DISPLAY': ':1', 'WAYLAND_DISPLAY': 'wayland-1',
                                     'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/bus',
                                     'PULSE_SERVER': 'unix:/pulse', 'KEEP_ME': 'yes'}, clear=False):
            args, env = worker.nested_weston(Path('/run/anvil-xdg'), 1920, 1080, ':0',
                                             Path('/root/x11-authority'))
        self.assertEqual(args, ['weston', '--backend=x11', '--renderer=gl',
                                '--socket=wayland-probe', '--idle-time=0', '--no-config',
                                '--width=1920', '--height=1080'])
        self.assertEqual(env['XDG_RUNTIME_DIR'], '/run/anvil-xdg')
        self.assertEqual(env['DISPLAY'], ':0')  # from the token, never inherited
        self.assertEqual(env['XAUTHORITY'], '/root/x11-authority')
        for stripped in ('WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'PULSE_SERVER'):
            self.assertNotIn(stripped, env)
        self.assertEqual(env.get('KEEP_ME'), 'yes')

    def test_nested_weston_omits_authority_when_absent(self):
        args, env = worker.nested_weston(Path('/run/anvil-xdg'), 800, 600, ':0', None)
        self.assertNotIn('XAUTHORITY', env)
        self.assertIn('--backend=x11', args)

class WestonStartup(unittest.TestCase):
    def test_failed_gl_with_stale_socket_retries_and_tracks_pixman(self):
        from unittest.mock import Mock
        import socket
        with tempfile.TemporaryDirectory() as directory:
            xdg = Path(directory)
            path = xdg / 'wayland-probe'
            stale = socket.socket(socket.AF_UNIX)
            self.addCleanup(stale.close)
            stale.bind(str(path))
            (xdg / 'wayland-probe.lock').touch()
            failed = Mock(); failed.poll.return_value = 1
            live = Mock(); live.poll.return_value = None
            calls = []
            def spawn(args, **kwargs):
                calls.append(list(args))
                if len(calls) == 1:
                    return failed
                self.assertFalse(path.exists())
                self.assertFalse((xdg / 'wayland-probe.lock').exists())
                listener = socket.socket(socket.AF_UNIX)
                self.addCleanup(listener.close)
                listener.bind(str(path))
                return live
            processes = []
            with patch.object(worker.subprocess, 'Popen', side_effect=spawn):
                result = worker.start_weston(['weston', '--renderer=gl'], {}, None, xdg, processes, nested=True)
            self.assertIs(result, live)
            self.assertEqual(processes, [live])
            self.assertEqual(calls[1], ['weston', '--renderer=pixman'])

    def test_both_renderers_fail_without_further_retry(self):
        from unittest.mock import Mock
        failed = Mock(); failed.poll.return_value = 1
        with tempfile.TemporaryDirectory() as directory, patch.object(worker.subprocess, 'Popen', return_value=failed) as spawn:
            processes = []
            with self.assertRaisesRegex(RuntimeError, 'Nested Weston exited'):
                worker.start_weston(['weston', '--renderer=gl'], {}, None, Path(directory), processes, nested=True)
            self.assertEqual(spawn.call_count, 2)
            self.assertEqual(processes, [failed])

    def test_headless_does_not_retry_and_timeout_keeps_process_for_cleanup(self):
        from unittest.mock import Mock
        for exit_code, message in [(1, 'exited'), (None, 'socket missing')]:
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                process = Mock(); process.poll.return_value = exit_code
                with patch.object(worker.subprocess, 'Popen', return_value=process) as spawn, patch.object(worker.time, 'sleep'):
                    processes = []
                    with self.assertRaisesRegex(RuntimeError, message):
                        worker.start_weston(['weston', '--renderer=gl'], {}, None, Path(directory), processes)
                    spawn.assert_called_once()
                    self.assertEqual(processes, [process])


class DesktopGpuTests(unittest.TestCase):
    def test_nvidia_auto_and_explicit_selection_use_venus_on_x11_and_wayland(self):
        import json
        device = dict(node='/dev/dri/renderD128', driver='nvidia', vendor_id='0x10de', available=True)
        with tempfile.TemporaryDirectory() as directory:
            instance = Path(directory)
            for desktop in (('x11::0', 1000), ('wayland-0', 1000)):
                for selection in ({'node': 'auto'}, device):
                    with self.subTest(desktop=desktop, selection=selection):
                        (instance / 'gpu.json').write_text(json.dumps(selection))
                        with patch.object(worker.gpu, 'inventory', return_value=[device]), patch.object(worker.gpu, 'choose', return_value=device), patch.object(worker.gpu.nvidia, 'trusted_payload') as payload:
                            node = worker.desktop_gpu(instance, desktop)
                            self.assertEqual(node, device['node'])
                            self.assertTrue(worker.gpu.is_nvidia(node))
                            props = worker.gpu.properties([], node)
                            self.assertIn('ro.hardware.vulkan=virtio', props)
                            self.assertIn('mesa.vn.debug=vtest', props)
                            self.assertIn('ro.waydroid.software_rendering=0', props)
                            payload.assert_called_once_with()

    def test_x11_still_checks_payload_and_headless_reports_missing_desktop(self):
        with patch.object(worker.gpu, 'resolve', return_value='/dev/dri/renderD128'), patch.object(worker.gpu, 'is_nvidia', return_value=True), patch.object(worker.gpu.nvidia, 'trusted_payload', side_effect=RuntimeError('Invalid payload')) as payload:
            with self.assertRaisesRegex(RuntimeError, 'Invalid payload'):
                worker.desktop_gpu(Path('/unused'), ('x11::0', 1000))
            payload.assert_called_once_with()
            payload.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'requires a Wayland or X11 desktop'):
                worker.desktop_gpu(Path('/unused'), None)
            payload.assert_not_called()

    def test_cpu_and_mesa_do_not_require_venus_payload(self):
        for node in (None, '/dev/dri/renderD129'):
            with patch.object(worker.gpu, 'resolve', return_value=node), patch.object(worker.gpu, 'is_nvidia', return_value=False), patch.object(worker.gpu.nvidia, 'trusted_payload') as payload:
                self.assertEqual(worker.desktop_gpu(Path('/unused'), None), node)
                payload.assert_not_called()


class NestedX11Integration(unittest.TestCase):
    def test_real_weston_exposes_guest_presentation_protocols_on_x11(self):
        import select
        import shutil
        import socket
        import subprocess
        if not (shutil.which('weston') and shutil.which('Xvfb')):
            self.skipTest('Requires Weston and Xvfb')
        read_fd, write_fd = os.pipe()
        xvfb = subprocess.Popen(['Xvfb', '-displayfd', str(write_fd), '-screen', '0', '800x600x24', '-nolisten', 'tcp'], pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd)
        processes = []
        try:
            self.assertTrue(select.select([read_fd], [], [], 5)[0], 'Xvfb did not start')
            display = ':' + os.read(read_fd, 30).decode().strip()
            with tempfile.TemporaryDirectory() as directory:
                xdg = Path(directory)
                args, env = worker.nested_weston(xdg, 800, 600, display, None)
                with (xdg / 'weston.log').open('w') as log:
                    process = worker.start_weston(args, env, log, xdg, processes, nested=True)
                    self.assertIsNone(process.poll())
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.settimeout(3)
                        connection.connect(str(xdg / 'wayland-probe'))
                        protocols = worker.desktop_module.registry_protocols(connection)
                    for protocol in ('wl_compositor', 'wl_subcompositor', 'wl_shm', 'xdg_wm_base'):
                        self.assertIn(protocol, protocols)
        finally:
            os.close(read_fd)
            for process in processes + [xvfb]:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
