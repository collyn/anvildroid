#!/usr/bin/env python3
"""Test the per-app endpoint without opening X11, EGL, Android or Weston."""
import os
import argparse
from pathlib import Path
import subprocess
import tempfile
import time
import select
import shutil
import sys
from contextlib import contextmanager, ExitStack

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get('ANVILDROID_X11_BUILD_DIR', ROOT / 'target/x11-host')).resolve()


@contextmanager
def private_xim(environment, root, enabled):
    if not enabled:
        yield
        return
    # This entry point runs under dbus-run-session and a private Xvfb. Never
    # connect a test input method to the user's desktop or session bus.
    environment.update(XMODIFIERS='@im=fcitx',
                       XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_CACHE_HOME=str(root / 'cache'),
                       XDG_DATA_HOME=str(root / 'data'))
    profile = root / 'config/fcitx5/profile'
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text('[Groups/0]\nName=Default\nDefault Layout=us\nDefaultIM=keyboard-us\n\n[Groups/0/Items/0]\nName=keyboard-us\nLayout=\n\n[Groups/0/Items/1]\nName=pinyin\nLayout=\n\n[GroupOrder]\n0=Default\n')
    (profile.parent / 'conf').mkdir(exist_ok=True)
    (profile.parent / 'conf/xim.conf').write_text('UseOnTheSpot=True\n')
    environment['ANVIL_TEST_COMPOSITION'] = '1'
    with (root / 'xim.log').open('w+') as log:
        method = subprocess.Popen(
            ['fcitx5', '-D', '--disable=all', '--enable=xcb,xim,keyboard,dbus,pinyin,punctuation'],
            env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 5
            while True:
                state = subprocess.run(['xprop', '-root', 'XIM_SERVERS'],
                                       env=environment, capture_output=True,
                                       text=True, timeout=2)
                if '@server=fcitx' in state.stdout:
                    break
                if method.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Private Fcitx XIM did not start: ' + state.stdout + state.stderr)
                time.sleep(.02)
            yield
        except BaseException:
            log.seek(0)
            print(log.read())
            raise
        finally:
            if method.poll() is None:
                method.terminate()
            try:
                method.wait(timeout=3)
            except subprocess.TimeoutExpired:
                method.kill()
                method.wait(timeout=3)


def main(software_x11=False, xim=False):
    assert not xim or software_x11, 'XIM tests require a private Xvfb'
    if software_x11:
        missing = [name for name in ('Xvfb', 'xclip') if shutil.which(name) is None]
        if missing:
            raise RuntimeError('Software X11 test dependencies missing from PATH: ' + ', '.join(missing))
    subprocess.run([str(OUT / 'surface-tree-check')], check=True)
    subprocess.run([str(OUT / 'feature-state-check')], check=True)
    with tempfile.TemporaryDirectory(prefix='anvil-x11-host-', dir='/tmp') as directory, ExitStack() as cleanup:
        root = Path(directory)
        socket = root / 'wayland-test'
        environment = dict(os.environ, XDG_RUNTIME_DIR=directory)
        environment.pop('DISPLAY', None)
        environment.pop('WAYLAND_DISPLAY', None)
        environment.pop('XAUTHORITY', None)
        xserver = None
        if software_x11:
            read, write = os.pipe()
            try:
                xserver = subprocess.Popen(['Xvfb', '-displayfd', str(write), '-screen', '0',
                                            '1600x1000x24', '-nolisten', 'tcp', '-noreset'],
                                           pass_fds=(write,), env=environment,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                def stop_xserver():
                    if xserver.poll() is None:
                        xserver.terminate()
                    xserver.wait(timeout=3)
                cleanup.callback(stop_xserver)
            finally:
                os.close(write)
            try:
                if not select.select([read], [], [], 5)[0]:
                    raise RuntimeError('Isolated Xvfb did not start')
                number = os.read(read, 64).decode().strip()
                assert number.isdecimal()
                environment.update(DISPLAY=':' + number, LIBGL_ALWAYS_SOFTWARE='1',
                                   GALLIUM_DRIVER='llvmpipe', ANVIL_TEST_X11='1',
                                   LANG='C.UTF-8', XMODIFIERS='@im=none')
                environment.pop('LC_ALL', None)
                environment.pop('LC_CTYPE', None)
            finally:
                os.close(read)
        if software_x11:
            subprocess.run([str(OUT / 'feature-state-check'), '--x11-input-burst'],
                           env=environment, check=True, timeout=10)
        with private_xim(environment, root, xim), (root / 'host.log').open('w+') as log:
            host = subprocess.Popen([str(OUT / 'anvildroid-x11-host'),
                                     *([] if software_x11 else ['--test-headless']), '--socket', str(socket)],
                                    env=environment, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 5
                while not socket.exists():
                    if host.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Endpoint did not start')
                    time.sleep(.02)
                log.seek(0)
                assert 'toplevel_created' not in log.read(), 'Start opened a window'
                if os.environ.get('ANVIL_X11_FEATURES_ONLY'):
                    subprocess.run([str(OUT / 'feature-client-check'), str(socket)], env=environment, check=True, timeout=30)
                    return
                baseline = None
                environment['ANVIL_TEST_HOST_PID'] = str(host.pid)
                for index in range(5):
                    subprocess.run([str(OUT / 'host-client-check'), str(socket)], env=environment, check=True, timeout=10)
                    assert host.poll() is None
                    count = len(list(Path(f'/proc/{host.pid}/fd').iterdir()))
                    if index == 0:
                        baseline = count
                    else:
                        assert count <= baseline, 'Endpoint file descriptors grew across client teardown'
                subprocess.run([str(OUT / 'host-client-check'), str(socket), 'dmabuf-lifetime'],
                               env=environment, check=True, timeout=10)
                for invalid in ('scale', 'viewport', 'cycle'):
                    subprocess.run([str(OUT / 'host-client-check'), str(socket), invalid], env=environment, check=True, timeout=10)
                    assert host.poll() is None
                subprocess.run([str(OUT / 'host-client-check'), str(socket), 'calibration'],
                               env=environment, check=True, timeout=10)
                subprocess.run([str(OUT / 'host-client-check'), str(socket), 'chrome'],
                               env=environment, check=True, timeout=10)
                log.seek(0)
                output = log.read()
                assert output.count('x11_toplevel_created=') == 12
                assert output.count('x11_toplevel_mapped=') == 11
                assert output.count('x11_toplevel_unmapped=') == 5
                if software_x11:
                    subprocess.run([str(OUT / 'keymap-client-check'), str(socket)], env=environment, check=True, timeout=30)
                    subprocess.run([str(OUT / 'feature-client-check'), str(socket)], env=environment, check=True, timeout=30)
                    assert host.poll() is None
                print('PASS repeated client teardown and independent app windows; Start creates no window')
            except BaseException:
                log.seek(0)
                print(log.read())
                raise
            finally:
                host.terminate()
                try:
                    code = host.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    host.kill()
                    code = host.wait(timeout=3)
                if xserver is not None:
                    xserver.terminate()
                    xserver.wait(timeout=3)
                if code != 0:
                    log.seek(0)
                    raise RuntimeError('Endpoint teardown failed:\n' + log.read())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--software-x11', action='store_true', help='Use a private Xvfb and llvmpipe; never connect to the desktop X server')
    parser.add_argument('--xim', action='store_true', help='Test physical keys and UTF-8 commits with private Fcitx, D-Bus and Xvfb')
    parser.add_argument('--xim-session', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.xim:
        subprocess.run(['dbus-run-session', '--', sys.executable, __file__,
                        '--software-x11', '--xim-session'], check=True)
    else:
        main(args.software_x11, args.xim_session)
