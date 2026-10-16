#!/usr/bin/env python3
"""Default X11 regression fixtures. Never starts a real Waydroid session."""
import importlib.util
import fcntl
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / 'services' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


helper = module('waydroid-session')
existing = module('runtime-existing')
x11 = module('runtime-x11')

PRESENTER = '''#!/usr/bin/python3
import os, socket, sys
from pathlib import Path
root=Path(os.environ['XDG_RUNTIME_DIR'])
(root/'presenter-command').write_text(' '.join(sys.argv[1:]))
if os.environ.get('ADAPTER_CRASH')=='1': sys.exit(7)
s=socket.socket(socket.AF_UNIX)
s.bind(sys.argv[sys.argv.index('--socket')+1]);s.listen(16)
while True:
 c,_=s.accept();c.close()
'''
WAYDROID = '''#!/usr/bin/python3
import os, socket, sys, time
from pathlib import Path
root=Path(os.environ['XDG_RUNTIME_DIR'])
if sys.argv[1:]==['session','start']:
 assert os.environ['WAYLAND_DISPLAY']=='anvildroid-default-x11'
 assert os.environ['DISPLAY'].startswith(':')
 with socket.socket(socket.AF_UNIX) as s: s.connect(str(root/os.environ['WAYLAND_DISPLAY']))
 (root/'ready').write_text('yes')
 if os.environ.get('HANDOFF')!='1':
  while not (root/'stop').exists(): time.sleep(.05)
elif sys.argv[1:]==['status']:
 print('Session: '+('RUNNING' if (root/'ready').exists() and not (root/'stop').exists() else 'STOPPED'))
elif sys.argv[1:]==['session','stop']: (root/'stop').touch()
else: sys.exit(1)
'''


class DefaultX11Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='anvil-default-x11-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / 'bin'; self.tools.mkdir()
        self.runtime = self.root / 'runtime'; self.runtime.mkdir(mode=0o700)
        self.env = dict(os.environ, DISPLAY=':999', XDG_RUNTIME_DIR=str(self.runtime),
                        PATH=str(self.tools) + ':/usr/bin:/bin', HANDOFF='1')
        self.env.pop('WAYLAND_DISPLAY', None)
        self.env.pop('WAYLAND_SOCKET', None)
        self.env.pop('XAUTHORITY', None)
        for name, text in (('presenter', PRESENTER), ('waydroid', WAYDROID),
                           ('weston', '#!/bin/sh\ntouch "$XDG_RUNTIME_DIR/weston-started"\nexit 91\n')):
            path = self.tools / name; path.write_text(text); path.chmod(0o755)

    def command(self, executable=None):
        # Execute the exact embedded helper, replacing only verified asset
        # discovery. PATH always pins Waydroid to this inert fixture.
        return ['python3', '-u', '-c',
                'import signal,sys\n'
                'scope={"__name__":"fixture"}\n'
                'exec(' + repr((ROOT / 'services/waydroid-session.py').read_text()) + ',scope)\n'
                'scope["native_adapter"]=lambda:(' + repr(str(executable or self.tools / 'presenter')) + ',{})\n'
                'signal.signal(signal.SIGTERM,scope["interrupted"])\n'
                'signal.signal(signal.SIGHUP,scope["interrupted"])\n'
                'sys.exit(scope["run"]())\n']

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.terminate()
        try: process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate(timeout=3)

    def launch(self, executable=None):
        process = subprocess.Popen(self.command(executable), env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self.cleanup_process, process)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and process.poll() is None:
            if (self.runtime / 'ready').exists(): return process
            time.sleep(.03)
        self.fail('Fixture failed to start: ' + process.communicate(timeout=3)[0])

    def test_native_handoff_duplicate_start_and_cleanup(self):
        process = self.launch()
        self.assertEqual((self.runtime / 'presenter-command').read_text(),
                         '--socket ' + str(self.runtime / helper.SOCKET))
        self.assertFalse((self.runtime / 'weston-started').exists())
        self.assertIsNone(process.poll())
        duplicate = subprocess.run(self.command(), env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn('already starting or running', duplicate.stderr)
        (self.runtime / 'stop').touch()
        output = process.communicate(timeout=8)[0]
        self.assertEqual(process.returncode, 0, output)
        self.assertFalse(helper.connected(self.runtime / helper.SOCKET))

    def test_start_waits_for_stopped_supervisor_to_release_its_lock(self):
        lock = self.runtime / (helper.SOCKET + '.session.lock')
        ready = self.runtime / 'old-lock-ready'
        old = subprocess.Popen(['python3', '-c',
            'import fcntl,sys,time\n'
            'from pathlib import Path\n'
            'with open(sys.argv[1],"w") as f:\n'
            ' fcntl.flock(f,fcntl.LOCK_EX)\n'
            ' Path(sys.argv[2]).touch()\n'
            ' time.sleep(.8)\n', str(lock), str(ready)])
        self.addCleanup(self.cleanup_process, old)
        deadline = time.monotonic() + 3
        while not ready.exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        process = self.launch()
        self.assertIsNotNone(old.poll(), 'new session started before old supervisor released its lock')
        (self.runtime / 'stop').touch()
        output = process.communicate(timeout=8)[0]
        self.assertEqual(process.returncode, 0, output)
        self.assertIn('Waiting for previous Default X11 session cleanup', output)

    def test_stuck_cleanup_times_out_without_removing_or_stealing_lock(self):
        path = self.runtime / (helper.SOCKET + '.session.lock')
        with path.open('w') as old, path.open('w') as new:
            fcntl.flock(old, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(helper, 'session_running', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'still shutting down; no new session'):
                    helper.acquire_session_lock(new, self.env, timeout=.1)
            self.assertTrue(path.exists())
            with self.assertRaises(BlockingIOError):
                fcntl.flock(new, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_interrupted_supervisor_stops_guest_before_presenter(self):
        self.env['HANDOFF'] = '0'
        process = self.launch()
        process.terminate(); process.communicate(timeout=8)
        self.assertTrue((self.runtime / 'stop').exists())
        self.assertFalse(helper.connected(self.runtime / helper.SOCKET))

    def test_adapter_failure_never_falls_back_to_weston_or_starts_guest(self):
        self.env['ADAPTER_CRASH'] = '1'
        result = subprocess.run(self.command(), env=self.env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Weston was not started', result.stderr)
        self.assertFalse((self.runtime / 'ready').exists())
        self.assertFalse((self.runtime / 'weston-started').exists())

    def test_failed_guest_stop_retains_graphics(self):
        presenter, session = Mock(), Mock()
        presenter.poll.return_value = None
        session.poll.return_value = session.returncode = 1
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(helper, 'native_adapter', return_value=(self.tools / 'presenter', {})), \
             patch.object(helper, 'connected', side_effect=[False, True, True]), \
             patch.object(helper, 'session_running', side_effect=[False, True]), \
             patch.object(helper, 'stop_session', return_value=False), \
             patch.object(helper.subprocess, 'Popen', side_effect=[presenter, session]), \
             patch.object(helper, 'stop_process') as stop:
            with self.assertRaisesRegex(RuntimeError, 'adapter was retained'):
                helper.run()
            stop.assert_not_called()

    def test_existing_session_and_unsafe_runtime_directory_are_rejected(self):
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(helper, 'session_running', return_value=True), \
             patch.object(helper, 'native_adapter') as adapter:
            with self.assertRaisesRegex(RuntimeError, 'running Waydroid session'):
                helper.run()
            adapter.assert_not_called()
        self.runtime.chmod(0o777)
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'private directory'):
                helper.run()

    def test_symlinked_native_module_is_rejected_before_import(self):
        path = self.root / 'runtime-x11.py'
        path.symlink_to(ROOT / 'services/runtime-x11.py')
        with patch.object(helper, 'ASSETS', self.root):
            with self.assertRaisesRegex(RuntimeError, 'Unsafe native X11 installation'):
                helper.native_adapter()

    @unittest.skipUnless(os.environ.get('ANVILDROID_DEFAULT_X11_SOFTWARE') == '1',
                         'opt-in isolated Xvfb + llvmpipe')
    def test_real_native_adapter_in_private_xvfb_without_android(self):
        executable = ROOT / 'target/x11-host/anvildroid-x11-host'
        self.assertTrue(executable.is_file())
        self.assertTrue(shutil.which('Xvfb'))
        read, write = os.pipe()
        server = subprocess.Popen(['Xvfb', '-displayfd', str(write), '-screen', '0',
                                   '1280x720x24', '-nolisten', 'tcp'], pass_fds=(write,),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write)
        self.addCleanup(self.cleanup_process, server)
        try:
            self.assertTrue(select.select([read], [], [], 5)[0])
            display = os.read(read, 64).decode().strip()
        finally:
            os.close(read)
        self.assertTrue(display.isdecimal())
        self.env.update(DISPLAY=':' + display, LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe')
        process = self.launch(executable)
        self.assertTrue(helper.connected(self.runtime / helper.SOCKET))
        self.assertFalse((self.runtime / 'weston-started').exists())
        (self.runtime / 'stop').touch()
        output = process.communicate(timeout=8)[0]
        self.assertEqual(process.returncode, 0, output)
        self.assertIn('x11_adapter_ready protocol=1', output)
        self.assertNotIn('x11_toplevel_created=', output)


class PresenterSelection(unittest.TestCase):
    def test_default_prefers_amd_then_intel_without_changing_explicit_selection(self):
        with tempfile.TemporaryDirectory(prefix='anvil-default-gpu-') as temporary:
            root = Path(temporary)
            for number, vendor, pci in ((128, '0x8086', '0000:00:02.0'),
                                        (129, '0x10de', '0000:01:00.0'),
                                        (130, '0x1002', '0000:03:00.0')):
                device = root / pci; device.mkdir(); (device / 'vendor').write_text(vendor)
                node = root / ('renderD' + str(number)); node.mkdir()
                (node / 'device').symlink_to(device)
            with patch.object(x11, 'Path', return_value=root):
                self.assertEqual(x11.presenter_environment(), {'DRI_PRIME': 'pci-0000_03_00_0'})
                self.assertEqual(x11.presenter_environment('intel'), {'DRI_PRIME': 'pci-0000_00_02_0'})
                self.assertEqual(x11.presenter_environment('nvidia')['__GLX_VENDOR_LIBRARY_NAME'], 'nvidia')
                shutil.rmtree(root / 'renderD130')
                self.assertEqual(x11.presenter_environment(), {'DRI_PRIME': 'pci-0000_00_02_0'})


class OverlayRefreshTests(unittest.TestCase):
    def fixture(self, mode, owner=0, symlink=False):
        temporary = tempfile.TemporaryDirectory(prefix='anvil-overlay-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        runtime = existing.ExistingRuntime(root / 'store', root / 'work')
        relative = 'vendor/lib64/libanvildroid-window.so'
        source = root / 'asset'; source.write_bytes(b'new')
        target = runtime.work / 'overlay' / relative
        target.parent.mkdir(parents=True); target.write_bytes(b'old')
        assets = {relative: source}
        for relative in ('vendor/bin/anvildroid-apps.sh', 'vendor/bin/anvildroid-tasks.sh'):
            path = runtime.work / 'overlay' / relative
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'old')
            assets[relative] = source
        init = runtime.work / 'overlay/system/etc/init/init.waydroid.rc'
        init.parent.mkdir(parents=True); init.write_text('fixture')
        overlay = runtime.work / 'overlay'; overlay.chmod(mode)
        real_lstat, real_fstat = Path.lstat, os.fstat
        identity = overlay.stat().st_ino
        def metadata(path, *args, **kwargs):
            values = list(real_lstat(path, *args, **kwargs)); values[4] = 0
            if path != overlay: values[0] &= ~0o022
            return os.stat_result(values)
        def descriptor(fd):
            values = list(real_fstat(fd)); values[4] = 0
            if values[1] == identity: values[4] = owner
            else: values[0] &= ~0o022
            return os.stat_result(values)
        for context in (patch.object(existing, 'DESKTOP_ASSETS', assets),
                        patch.object(existing, 'KEYMAP_ASSETS', {}),
                        patch.object(runtime, 'desktop_overlay', return_value=True),
                        patch.object(runtime, 'stopped'),
                        patch.object(runtime, 'keymap_init', side_effect=lambda text: text),
                        patch.object(Path, 'lstat', side_effect=metadata, autospec=True),
                        patch.object(os, 'fstat', side_effect=descriptor)):
            context.start(); self.addCleanup(context.stop)
        if symlink:
            outside = root / 'outside'; outside.mkdir(); (outside / 'sentinel').write_text('keep')
            overlay.rename(root / 'original-overlay'); overlay.symlink_to(outside)
        return runtime, overlay, target

    def test_legacy_root_owned_0775_overlay_is_tightened_and_refreshed(self):
        runtime, overlay, target = self.fixture(0o775)
        runtime.sync_desktop_assets()
        self.assertEqual(stat.S_IMODE(overlay.stat().st_mode), 0o755)
        self.assertEqual(target.read_bytes(), b'new')

    def test_world_writable_overlay_stays_rejected(self):
        runtime, overlay, target = self.fixture(0o777)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe Desktop overlay directory'):
            runtime.sync_desktop_assets()
        self.assertEqual(target.read_bytes(), b'old')
        self.assertEqual(stat.S_IMODE(overlay.stat().st_mode), 0o777)

    def test_user_owned_group_writable_overlay_stays_rejected(self):
        runtime, _, target = self.fixture(0o775, owner=12345)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe Desktop overlay directory'):
            runtime.sync_desktop_assets()
        self.assertEqual(target.read_bytes(), b'old')

    def test_symlinked_overlay_is_never_followed(self):
        runtime, overlay, _ = self.fixture(0o755, symlink=True)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe Desktop overlay target'):
            runtime.sync_desktop_assets()
        self.assertEqual((overlay / 'sentinel').read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
