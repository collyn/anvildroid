#!/usr/bin/python3
"""GUI-authorized runtime tools setup. Persist the helper before starting a detached job.

Installs Waydroid and the host tools managed runtimes need, and repairs the
Waydroid LXC templates. It never initializes System Waydroid or downloads
Android images: those come from the Images library when a runtime is created.
Progress is reported in /run/anvildroid/waydroid-init.status. No dependency on the AppImage
FUSE mount or its short-lived privilege-escalation staging directory remains.
"""
import argparse
import fcntl
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

PYTHON = '/usr/bin/python3'
RUN_DIR = Path('/run/anvildroid')
STORE = Path('/var/lib/anvildroid-controller')
STATUS_FILE = RUN_DIR / 'waydroid-init.status'
INIT_LOG = STORE / 'waydroid-init.log'
# runtime-provision.py loads runtime-image-format.py and runtime-images.py.
SETUP_MODULES = ('runtime-host.py', 'runtime-provision.py', 'runtime-image-format.py', 'runtime-images.py')
ENV = {'PATH': '/usr/local/lib/anvildroid-controller/tools:/usr/sbin:/usr/bin:/sbin:/bin', 'PYTHONNOUSERSITE': '1'}
INSTALL_SCRIPT_URL = 'https://repo.waydro.id'
# Files written by the official repository script. apt reads them as the
# unprivileged _apt user, so they must be world-readable.
APT_SOURCE_FILES = (Path('/usr/share/keyrings/waydroid.gpg'), Path('/etc/apt/sources.list.d/waydroid.list'))


def system_umask():
    # This helper runs with umask 077 for its own state. Package tools and the
    # repository script must create normal system files (0644), or apt cannot
    # read the Waydroid key ("NO_PUBKEY") or source list.
    os.umask(0o022)


def repair_apt_sources(paths=APT_SOURCE_FILES):
    """Make Waydroid apt files written by an earlier run under umask 077 readable."""
    repaired = []
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            continue  # never follow or touch anything we did not expect
        if stat.S_IMODE(info.st_mode) != 0o644 and not info.st_mode & 0o022:
            os.chmod(path, 0o644)
            repaired.append(str(path))
    return repaired
HOST_MODULE = Path(__file__).resolve().parent.parent / 'services/runtime-host.py'
if not HOST_MODULE.is_file():
    HOST_MODULE = Path(__file__).with_name('runtime-host.py')
spec = importlib.util.spec_from_file_location('runtime_host', HOST_MODULE)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def ensure_directory(path, mode):
    # Create the directory with the exact mode, or tighten an existing one.
    # chmod after mkdir: the process umask would otherwise narrow the mode.
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f'Not a directory: {path}')
    else:
        path.mkdir(mode=0o700)
    os.chmod(path, mode)


def write_status(status_path, state, detail='', error=None, pid=None):
    payload = {'state': state, 'detail': detail, 'error': error, 'pid': pid}
    fd, temporary = tempfile.mkstemp(prefix='.waydroid-init.', dir=status_path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(payload, stream)
            os.fchmod(stream.fileno(), 0o644)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, status_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_status(status_path=STATUS_FILE):
    try:
        with open(status_path) as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def status_in_progress(status):
    if not isinstance(status, dict) or status.get('state') not in ('installing', 'initializing'):
        return False
    pid = status.get('pid')
    if pid is None:
        return True  # conservative: unknown pid may still be working
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # the detached init died without a final status
    except PermissionError:
        return True  # alive but owned by someone else (root)
    except OSError:
        return False
    return True


def tail(text, maximum=400):
    if len(text) <= maximum:
        return text
    return text[-maximum:]


def apt(*args):
    with open(INIT_LOG, 'ab') as log:
        repair_apt_sources()
        result = subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', *args], preexec_fn=system_umask,
                                stdout=log, stderr=subprocess.STDOUT,
                                env={**ENV, 'DEBIAN_FRONTEND': 'noninteractive'}, timeout=1800)
    if result.returncode:
        raise RuntimeError(f'Package installation failed (exit {result.returncode}); see {INIT_LOG}')


def ensure_host_packages():
    packages = host.missing_packages()
    if not packages and shutil.which('waydroid'):
        return
    os_release = Path('/etc/os-release').read_text()
    distro = dict(line.split('=', 1) for line in os_release.splitlines() if '=' in line)
    family = (distro.get('ID', '') + ' ' + distro.get('ID_LIKE', '')).replace('"', '').split()
    if not {'ubuntu', 'debian'}.intersection(family) or not shutil.which('apt-get'):
        raise RuntimeError('Automatic host setup requires Ubuntu/Debian. Install Waydroid and the missing runtime tools for your distribution, then refresh.')
    write_status(STATUS_FILE, 'installing', detail='Installing Android runtime tools…', pid=os.getpid())
    apt('update')
    apt('install', '-y', '--no-install-recommends', 'ca-certificates', *packages)
    if shutil.which('waydroid') is None:
        run_installer()
    missing = host.missing_commands()
    if missing or shutil.which('waydroid') is None:
        raise RuntimeError('Host setup incomplete: ' + ', '.join(missing + ([] if shutil.which('waydroid') else ['waydroid'])))


def run_installer(status_path=STATUS_FILE, log_path=INIT_LOG):
    write_status(status_path, 'installing', detail='Adding the official Waydroid repository…', pid=os.getpid())
    fetched = subprocess.run(['curl', '-fsSL', '--proto', '=https', '--proto-redir', '=https',
                              '--connect-timeout', '15', '--max-time', '120', INSTALL_SCRIPT_URL],
                             capture_output=True, env=ENV, timeout=130)
    if fetched.returncode != 0 or not fetched.stdout.strip():
        raise RuntimeError('Cannot download the official Waydroid repository setup: ' + tail(fetched.stderr.decode('utf-8', 'replace')))
    # Overwriting an existing file keeps its old mode, so repair before and after.
    repair_apt_sources()
    with open(log_path, 'ab') as log:
        installed = subprocess.run(['bash', '-e', '-s'], input=fetched.stdout, preexec_fn=system_umask,
                                   stdout=log, stderr=subprocess.STDOUT, env=ENV, timeout=600)
    repair_apt_sources()
    if installed.returncode:
        raise RuntimeError(f'Waydroid repository setup failed; see {log_path}')
    # repo.waydro.id adds an apt source and updates metadata; it does NOT install
    # Waydroid. Without this step a clean host fails immediately at init.
    write_status(status_path, 'installing', detail='Installing Waydroid…', pid=os.getpid())
    apt('install', '-y', 'waydroid')


def ensure_managed_support():
    """Repair package templates without initializing System Waydroid images."""
    spec = importlib.util.spec_from_file_location('setup_provision', HOST_MODULE.with_name('runtime-provision.py'))
    provision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provision)
    if provision.managed_support_available():
        return
    distro = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    family = (distro.get('ID', '') + ' ' + distro.get('ID_LIKE', '')).replace('"', '').split()
    if not {'ubuntu', 'debian'}.intersection(family) or not shutil.which('apt-get'):
        raise RuntimeError('Reinstall the Waydroid package for your distribution to restore its LXC templates, then refresh.')
    write_status(STATUS_FILE, 'installing', detail='Repairing Waydroid runtime tools…', pid=os.getpid())
    apt('update')
    apt('install', '-y', '--no-install-recommends', '--reinstall', 'waydroid')
    if not provision.managed_support_available():
        raise RuntimeError('The Waydroid package did not provide the required LXC templates; see ' + str(INIT_LOG))


def run_init():
    # Body of the --run-init subcommand; runs detached as root.
    if os.geteuid() != 0:
        raise SystemExit('The init subcommand must run as root.')
    ensure_host_packages()
    ensure_managed_support()
    write_status(STATUS_FILE, 'done', detail='Runtime tools are ready. Choose an image in Images and create a runtime.', pid=os.getpid())


def spawn_init(lock_fd):
    # GUI removes its stage after pkexec returns. Persist BOTH this helper and
    # its dependency before spawn, so delayed exec/reopening the AppImage works.
    helper_dir = STORE / 'setup'
    ensure_directory(helper_dir, 0o700)
    # Read every dependency before publishing any helper files or spawning a job.
    sources = {'setup-waydroid.py': Path(__file__).resolve().read_bytes(),
               **{name: HOST_MODULE.with_name(name).read_bytes() for name in SETUP_MODULES}}
    for name, data in sources.items():
        compile(data, name, 'exec')
    for name, data in sources.items():
        fd, temporary = tempfile.mkstemp(prefix='.setup-', dir=helper_dir)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, helper_dir / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
    with open(INIT_LOG, 'ab') as log:
        process = subprocess.Popen([PYTHON, '-B', str(helper_dir / 'setup-waydroid.py'),
                                    '--run-init', '--lock-fd', str(lock_fd)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, cwd='/', env=ENV, pass_fds=(lock_fd,))
    # The child writes all progress, including its PID. The parent must not
    # overwrite a fast child's final status with a stale 'initializing'.
    return process


def main():
    parser = argparse.ArgumentParser(description='Install or repair the host tools for AnvilDroid runtimes.')
    parser.add_argument('--run-init', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--lock-fd', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run this helper as root via pkexec.')
    os.umask(0o077)
    ensure_directory(RUN_DIR, 0o755)
    ensure_directory(STORE, 0o700)
    descriptor = args.lock_fd if args.run_init and args.lock_fd is not None else os.open(
        RUN_DIR / 'waydroid-setup.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Android runtime setup is already in progress.')
            return
        if args.run_init:
            try:
                run_init()
            except Exception as error:
                write_status(STATUS_FILE, 'failed', detail=tail(str(error)), error=tail(str(error)), pid=os.getpid())
                raise SystemExit(str(error))
            return
        write_status(STATUS_FILE, 'initializing', detail='Starting runtime tools setup…', pid=os.getpid())
        try:
            spawn_init(descriptor)
        except Exception as error:
            write_status(STATUS_FILE, 'failed', error=tail(str(error)))
            raise
        print('Runtime tools setup started. Progress and any errors will appear in the app.')
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    main()
