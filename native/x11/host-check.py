#!/usr/bin/env python3
"""Test the per-app endpoint without opening X11, EGL, Android or Weston."""
import os
import argparse
import stat
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
    # libX11's XIM_SERVERS filter closes, but never frees, the probe XIM it
    # opens before an instantiate callback (432 bytes per server start).
    suppressions = root / 'lsan.supp'
    suppressions.write_text('leak:_XimOpenIM\n')
    environment['LSAN_OPTIONS'] = ':'.join(filter(None, (
        environment.get('LSAN_OPTIONS'), 'suppressions=' + str(suppressions))))
    command = ['fcitx5', '-D', '--disable=all', '--enable=xcb,xim,keyboard,dbus,pinyin,punctuation']
    with (root / 'xim.log').open('w+') as log:
        method = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
        # The client kills this server and starts a replacement it owns, so a
        # restart is tested against the same adapter process.
        environment.update(ANVIL_TEST_FCITX_PID=str(method.pid),
                           ANVIL_TEST_FCITX_COMMAND=' '.join(command),
                           ANVIL_TEST_FCITX_LOG=str(root / 'xim.log'))
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


def output_checks(environment, root):
    """Two RandR 1.5 monitors and Xft.dpi scales on the private Xvfb."""
    check = str(OUT / 'output-client-check')
    try:
        for dpi, scale in ((192, 240), (144, 180)):
            subprocess.run([check, 'setup', '96'], env=environment, check=True, timeout=10)
            socket = root / f'wayland-output-{scale}'
            log_path = root / f'output-{scale}.log'
            with log_path.open('w+') as log:
                host = subprocess.Popen([str(OUT / 'anvildroid-x11-host'), '--socket', str(socket)],
                                        env=environment, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 5
                    while not socket.exists():
                        if host.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Output endpoint did not start')
                        time.sleep(.02)
                    # A desktop scale change before any client observed the
                    # scale is applied as if the adapter had started with it.
                    subprocess.run([check, 'setup', str(dpi)], env=environment, check=True, timeout=10)
                    applied = f'x11_desktop_scale={scale}/120 '
                    while True:
                        log.seek(0)
                        text = log.read()
                        if applied in text or time.monotonic() >= deadline + 5:
                            break
                        time.sleep(.02)
                    assert applied in text and 'restart_required=0' in text, text
                    subprocess.run([check, str(socket), str(scale)],
                                   env=dict(environment, ANVIL_TEST_HOST_LOG=str(log_path)),
                                   check=True, timeout=60)
                    assert host.poll() is None
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
                log.seek(0)
                text = log.read()
                assert code == 0, text
                for line in ('x11_output_added name=L', 'x11_output_added name=R',
                             'x11_output_removed name=R', f'x11_output_scale_latched={scale}/120'):
                    assert line in text, line
    finally:
        subprocess.run([check, 'reset'], env=environment, check=True, timeout=10)
    print('PASS per-monitor wl_outputs, hotplug, enter/leave and Xft.dpi scales 2 and 1.5')


def main(software_x11=False, xim=False):
    assert not xim or software_x11, 'XIM tests require a private Xvfb'
    if software_x11:
        missing = [name for name in ('Xvfb', 'xclip') if shutil.which(name) is None]
        if missing:
            raise RuntimeError('Software X11 test dependencies missing from PATH: ' + ', '.join(missing))
    subprocess.run([str(OUT / 'surface-tree-check')], check=True)
    subprocess.run([str(OUT / 'output-mode-check')], check=True)
    subprocess.run([str(OUT / 'window-positions-check')], check=True)
    subprocess.run([str(OUT / 'dmabuf-format-check')], check=True)
    subprocess.run([str(OUT / 'feature-state-check')], check=True)
    with tempfile.TemporaryDirectory(prefix='anvil-x11-host-', dir='/tmp') as directory, ExitStack() as cleanup:
        root = Path(directory)
        socket = root / 'wayland-test'
        environment = dict(os.environ, XDG_RUNTIME_DIR=directory,
                           # Remembered window positions stay in this disposable directory.
                           XDG_STATE_HOME=str(Path(directory) / 'state'))
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
                # Xvfb writes the number and its newline separately, and dies
                # if the second write hits a closed pipe: read through newline.
                data = b''
                deadline = time.monotonic() + 5
                while not data.endswith(b'\n'):
                    if not select.select([read], [], [], max(0, deadline - time.monotonic()))[0]:
                        raise RuntimeError('Isolated Xvfb did not start')
                    chunk = os.read(read, 64)
                    if not chunk:
                        break
                    data += chunk
                number = data.decode().strip()
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
        # Shorten only the inhibited reset period, so the check can observe it.
        environment['ANVIL_TEST_IDLE_RESET_MS'] = '300'
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
                for invalid in ('scale', 'viewport', 'cycle', 'dmabuf-planes', 'dmabuf-reuse'):
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
                assert 'x11_idle_inhibit=' not in output, 'Idle inhibited without an inhibitor'
                subprocess.run([str(OUT / 'host-client-check'), str(socket), 'idle-inhibit'],
                               env=dict(environment, ANVIL_TEST_HOST_LOG=str(root / 'host.log')),
                               check=True, timeout=30)
                deadline = time.monotonic() + 5
                while True:
                    log.seek(0)
                    states = [line for line in log.read().splitlines() if line.startswith('x11_idle_inhibit=')]
                    if states[-1] == 'x11_idle_inhibit=0' or time.monotonic() >= deadline:
                        break
                    time.sleep(.02)
                assert states[-1] == 'x11_idle_inhibit=0', 'Client teardown kept idle inhibited'
                assert len(states) == (12 if software_x11 else 8), states
                # Planar video buffers and non-fatal placeholders, with real
                # llvmpipe imports and pixel checks on the private Xvfb.
                subprocess.run([str(OUT / 'host-client-check'), str(socket), 'dmabuf-formats'],
                               env=environment, check=True, timeout=30)
                assert host.poll() is None
                log.seek(0)
                output = log.read()
                assert 'x11_dmabuf_import_failed' not in output, 'Advertised DMA-BUF format failed to import'
                for reason in ('unknown format', 'non-LINEAR modifier', 'plane stride below row size'):
                    assert 'reason=' + reason in output, reason
                if software_x11:
                    formats = [line for line in output.splitlines() if line.startswith('x11_adapter_dmabuf_formats=')]
                    assert formats and 'NV12' in formats[-1] and 'YV12' in formats[-1], formats
                if software_x11:
                    # First maps are centered on the workarea; reopened app
                    # windows return to the position saved when they closed.
                    state = Path(environment['XDG_STATE_HOME']) / 'anvildroid/x11-window-positions-default.tsv'
                    assert 'x11_window_positions=0 path=' + str(state) in output, output[:2000]
                    assert 'x11_toplevel_position_centered=' in output, 'New windows were not centered'
                    assert 'x11_toplevel_position_restored=' in output, 'Reopened windows did not restore'
                    saved = state.read_text().splitlines()
                    assert saved and all(len(line.split('\t')) == 3 for line in saved), saved
                    assert stat.S_IMODE(state.stat().st_mode) == 0o600
                    subprocess.run([str(OUT / 'keymap-client-check'), str(socket)], env=environment, check=True, timeout=30)
                    subprocess.run([str(OUT / 'feature-client-check'), str(socket)], env=environment, check=True, timeout=30)
                    assert host.poll() is None
                    output_checks(environment, root)
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
