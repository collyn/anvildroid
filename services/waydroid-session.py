"""Run Default Waydroid with the shared native X11 surface adapter.

Embedded in the native launcher so installed packages and shortcuts use the
same session startup path without relying on a separate helper installation.
"""
import fcntl
import importlib.util
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time

SOCKET = 'anvildroid-default-x11'
ASSETS = Path('/usr/local/lib/anvildroid-controller')


def native_adapter():
    """Use only the installed root-owned adapter and its verified manifest."""
    path = ASSETS / 'runtime-x11.py'
    for entry in (path, *path.parents):
        info = entry.lstat()
        expected = stat.S_ISREG if entry == path else stat.S_ISDIR
        if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Unsafe native X11 installation: ' + str(entry))
    spec = importlib.util.spec_from_file_location('anvildroid_default_x11', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths, _ = module.load_payload(ASSETS / 'native-x11')
    return paths['anvildroid-x11-host'], module.presenter_environment()


def stop_process(process):
    if process is None:
        return
    # Only a process group created by this supervisor is ever terminated.
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
        result = subprocess.run(['waydroid', 'session', 'stop'], env=env, timeout=5)
        return result.returncode == 0 and not session_running(env)
    except (OSError, subprocess.SubprocessError):
        return False


def acquire_session_lock(lock, env, timeout=10):
    # Waydroid drops its session before this supervisor has finished tearing
    # down the presenter. Restart may already see STOPPED during that interval.
    # Serialize with that cleanup; never remove a held lock or start a second
    # presenter. A running session (including another in-progress start) wins.
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            if session_running(env):
                raise RuntimeError('Default runtime is already starting or running on X11.') from error
            if time.monotonic() >= deadline:
                raise RuntimeError('Previous Default X11 session is still shutting down; no new session was started.') from error
            if not announced:
                print('Waiting for previous Default X11 session cleanup…', flush=True)
                announced = True
            time.sleep(.1)


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
    # Separate GUI/shortcut processes must not start competing presenters.
    fd = os.open(xdg / (SOCKET + '.session.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    presenter = session = None
    with os.fdopen(fd, 'w') as lock:
        acquire_session_lock(lock, env)
        if connected(endpoint):
            raise RuntimeError('The Default runtime Wayland socket is already in use.')
        if session_running(env):
            raise RuntimeError('Default runtime already has a running Waydroid session.')
        executable, graphics = native_adapter()
        try:
            print('Starting native X11 app-window adapter for Default runtime…', flush=True)
            presenter_env = dict(os.environ)
            for name in ('WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'ANVILDROID_RUNTIME_ID',
                         'DRI_PRIME', '__NV_PRIME_RENDER_OFFLOAD', '__GLX_VENDOR_LIBRARY_NAME'):
                presenter_env.pop(name, None)
            presenter_env.update(graphics)
            presenter = subprocess.Popen([str(executable), '--socket', str(endpoint)],
                                         env=presenter_env, start_new_session=True)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and presenter.poll() is None:
                if connected(endpoint):
                    break
                time.sleep(.1)
            if presenter.poll() is not None or not connected(endpoint):
                raise RuntimeError('Native X11 adapter could not open the display; inspect its log above. Weston was not started.')
            env['WAYLAND_DISPLAY'] = SOCKET
            session = subprocess.Popen(['waydroid', 'session', 'start'], env=env, start_new_session=True)
            while session.poll() is None:
                if presenter.poll() is not None:
                    raise RuntimeError('Native X11 adapter exited while Android was running.')
                time.sleep(.1)
            code = session.returncode
            # Some Waydroid versions hand startup to their D-Bus service and
            # return successfully. Keep its presenter until that session stops.
            if code == 0:
                while session_running(env):
                    if presenter.poll() is not None:
                        raise RuntimeError('Native X11 adapter exited while Android was running.')
                    time.sleep(.5)
            return code
        finally:
            # Do not release a live guest's graphics if Android stop fails.
            if session is not None:
                try:
                    running = session_running(env)
                except (OSError, subprocess.SubprocessError):
                    running = True
                if running and not stop_session(env):
                    raise RuntimeError('Could not confirm Default runtime stopped; its X11 adapter was retained.')
            stop_process(session)
            stop_process(presenter)


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
