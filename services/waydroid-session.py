"""Run Default Waydroid in a supervised nested Weston on an X11 desktop.

Embedded in the native launcher so installed packages and shortcuts use the
same session startup path without relying on a separate helper installation.
"""
import fcntl
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time

SOCKET = 'anvildroid-default-wayland'


def stop_process(process):
    if process is None:
        return
    # Weston also launches its shell; give the entire private process group
    # the same lifetime as the Android session.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def connected(path):
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(.2)
            client.connect(str(path))
        return True
    except OSError:
        return False


def session_running(env):
    result = subprocess.run(['waydroid', 'status'], env=env, capture_output=True,
                            text=True, timeout=5, check=True)
    return any(line.partition(':')[0].strip() == 'Session'
               and line.partition(':')[2].strip() == 'RUNNING'
               for line in result.stdout.splitlines())


def stop_session(env):
    try:
        subprocess.run(['waydroid', 'session', 'stop'], env=env, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run():
    if not os.environ.get('DISPLAY'):
        raise RuntimeError('X11 DISPLAY is missing; start AnvilDroid from your desktop session.')
    directory = os.environ.get('XDG_RUNTIME_DIR')
    if not directory:
        raise RuntimeError('XDG_RUNTIME_DIR is missing; start AnvilDroid without sudo.')
    xdg = Path(directory)
    info = xdg.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise RuntimeError('XDG_RUNTIME_DIR must be a private directory owned by the desktop user.')
    env = dict(os.environ, LC_ALL='C')
    env.pop('WAYLAND_DISPLAY', None)
    env.pop('WAYLAND_SOCKET', None)
    endpoint = xdg / SOCKET
    # Separate GUI/shortcut processes must not start competing compositors.
    fd = os.open(xdg / (SOCKET + '.session.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    compositor = session = None
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Default runtime is already starting or running on X11.') from error
        if connected(endpoint):
            raise RuntimeError('The Default runtime Wayland socket is already in use.')
        try:
            for renderer in ('gl', 'pixman'):
                print('Starting nested Weston for Default runtime on X11 (' + renderer + ')…', flush=True)
                try:
                    compositor = subprocess.Popen([
                        'weston', '--backend=x11', '--renderer=' + renderer,
                        '--socket=' + SOCKET, '--idle-time=0', '--no-config',
                        '--width=1280', '--height=720',
                    ], env=env, start_new_session=True)
                except FileNotFoundError as error:
                    raise RuntimeError('X11 startup requires Weston. Install the weston package, then retry Start.') from error
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and compositor.poll() is None:
                    if connected(endpoint):
                        break
                    time.sleep(.1)
                if compositor.poll() is None and connected(endpoint):
                    break
                stop_process(compositor)
                compositor = None
            if compositor is None:
                raise RuntimeError('Nested Weston could not open the X11 display; check DISPLAY, XAUTHORITY and the compositor log above.')
            env['WAYLAND_DISPLAY'] = SOCKET
            session = subprocess.Popen(['waydroid', 'session', 'start'], env=env, start_new_session=True)
            while session.poll() is None:
                if compositor.poll() is not None:
                    raise RuntimeError('Nested Weston exited while Android was running.')
                time.sleep(.1)
            code = session.returncode
            # Some Waydroid versions hand startup to their D-Bus service and
            # return successfully. Keep its compositor until that session stops.
            if code == 0:
                while session_running(env):
                    if compositor.poll() is not None:
                        raise RuntimeError('Nested Weston exited while Android was running.')
                    time.sleep(.5)
            return code
        except BaseException:
            if session is not None:
                stop_session(env)
            raise
        finally:
            stop_process(session)
            stop_process(compositor)


def interrupted(signum, frame):
    raise InterruptedError('Default runtime session supervisor interrupted')


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        sys.exit(run())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr, flush=True)
        sys.exit(1)
