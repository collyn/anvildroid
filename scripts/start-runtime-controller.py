#!/usr/bin/python3
"""Idempotently install the bundled controller files, then start the controller
as a detached root process (setsid; no systemd unit). Intended to be run via
pkexec from the GUI, or manually with sudo. Start it again after rebooting.
"""
import importlib.util
import os
import socket
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = Path('/run/anvildroid')
STORE = Path('/var/lib/anvildroid-controller')
DESTINATION = Path('/usr/local/lib/anvildroid-controller')
SOCKET_PATH = RUN_DIR / 'control.sock'
CONTROLLER = DESTINATION / 'runtime-controller.py'
CONTROLLER_LOG = STORE / 'controller.log'


def load_installer():
    # install-runtime-controller.py has dashes in its name, so it cannot be
    # imported as a module; load it like a script (same pattern as
    # runtime-controller.py does for runtime-worker.py).
    spec = importlib.util.spec_from_file_location(
        'install_runtime_controller', ROOT / 'scripts' / 'install-runtime-controller.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_directory(path, mode):
    # Create the directory with the exact mode, or tighten an existing one.
    # Ownership is validated later by the controller's secure_directory().
    # chmod after mkdir: the process umask would otherwise narrow the mode.
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f'Not a directory: {path}')
    else:
        path.mkdir(mode=0o700)
    os.chmod(path, mode)


def ensure_binder():
    """Load the binder kernel module and expose the binderfs devices Waydroid
    expects. Idempotent; re-runs after every reboot when the GUI restarts the
    controller."""
    if shutil.which('modprobe') is None:
        return  # First-run setup installs kmod; missing tools must not hide GUI setup.
    subprocess.run(['modprobe', 'binder_linux'], capture_output=True)
    binderfs = Path('/dev/binderfs')
    mounted = False
    try:
        with open('/proc/mounts') as mounts:
            mounted = any(line.split()[1] == '/dev/binderfs' for line in mounts)
    except OSError:
        pass
    if not mounted:
        binderfs.mkdir(mode=0o755, exist_ok=True)
        try:
            subprocess.run(['mount', '-t', 'binder', 'binder', str(binderfs)], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            # Kernel without binder support: Android runtimes will fail at
            # start with a clear message; keep the controller available.
            pass
    for device in ('anbox-binder', 'anbox-vndbinder', 'anbox-hwbinder'):
        target = Path('/dev') / device
        source = binderfs / device
        if source.exists() and not target.is_symlink():
            try:
                target.symlink_to(source)
            except FileExistsError:
                pass


def socket_state(path):
    """Classify the controller socket path: absent, live, stale or unsafe.
    The controller's serve() unlinks a stale socket itself before binding."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(path))
        return 'live'
    except FileNotFoundError:
        return 'absent'
    except ConnectionRefusedError:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0:
            return 'unsafe'
        return 'stale'
    except OSError as error:
        raise RuntimeError(f'Cannot check controller socket {path}: {error}')


def spawn_controller():
    # Mirror the systemd unit's ExecStart: same interpreter, flags and paths.
    log = os.open(CONTROLLER_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    subprocess.Popen(['/usr/bin/python3', '-B', str(CONTROLLER),
                      '--store', str(STORE), '--socket', str(SOCKET_PATH)],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True, cwd='/',
                     env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'PYTHONNOUSERSITE': '1'})
    os.close(log)


def tail_log(maximum=2048):
    try:
        with open(CONTROLLER_LOG, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - maximum))
            return stream.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run this helper as root via pkexec.')
    os.umask(0o077)
    installer = load_installer()
    ensure_directory(RUN_DIR, 0o755)
    ensure_directory(STORE, 0o700)
    ensure_binder()
    state = socket_state(SOCKET_PATH)
    if state == 'live':
        print('Controller already running; nothing to do.')
        return
    if state == 'unsafe':
        raise SystemExit(f'Refusing to touch {SOCKET_PATH}: it exists but is not a root-owned socket.')
    installer.install_controller_files(ROOT)
    spawn_controller()
    try:
        installer.wait_for_ready_socket(
            SOCKET_PATH, time.monotonic() + 30,
            f'see {CONTROLLER_LOG}')
    except RuntimeError:
        log_tail = tail_log()
        if log_tail:
            print(log_tail, file=sys.stderr)
        raise
    print(f'Controller installed to {DESTINATION} and started as a background '
          f'root process (no system service). Log: {CONTROLLER_LOG}. '
          f'Start it again after rebooting.')


if __name__ == '__main__':
    main()
