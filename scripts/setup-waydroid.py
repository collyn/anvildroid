#!/usr/bin/python3
"""GUI-authorized host setup. Persist the helper before starting a detached job.

Repository setup, package installation and Android image initialization report
progress in /run/anvildroid/waydroid-init.status. No dependency on the AppImage
FUSE mount or its short-lived privilege-escalation staging directory remains.
"""
import argparse
import configparser
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON = '/usr/bin/python3'
RUN_DIR = Path('/run/anvildroid')
STORE = Path('/var/lib/anvildroid-controller')
STATUS_FILE = RUN_DIR / 'waydroid-init.status'
INIT_LOG = STORE / 'waydroid-init.log'
# Exactly the set checked by runtime-controller.py requirements() and read by
# runtime-provision.py — do not deviate.
SUPPORT_PATHS = (Path('/var/lib/waydroid/overlay'),
                 Path('/var/lib/waydroid/images/system.img'),
                 Path('/var/lib/waydroid/images/vendor.img'),
                 Path('/var/lib/waydroid/waydroid.prop'),
                 Path('/var/lib/waydroid/lxc/waydroid/config'),
                 Path('/var/lib/waydroid/lxc/waydroid/waydroid.seccomp'))
BASE_PROP = Path('/var/lib/waydroid/waydroid_base.prop')
WAYDROID_CFG = Path('/var/lib/waydroid/waydroid.cfg')
WAYDROID_PROP = Path('/var/lib/waydroid/waydroid.prop')
FLAVORS = ('VANILLA', 'GAPPS')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'PYTHONNOUSERSITE': '1'}
INSTALL_SCRIPT_URL = 'https://repo.waydro.id'
HOST_MODULE = Path(__file__).resolve().parent.parent / 'services/runtime-host.py'
if not HOST_MODULE.is_file():
    HOST_MODULE = Path(__file__).with_name('runtime-host.py')
spec = importlib.util.spec_from_file_location('runtime_host', HOST_MODULE)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
ARM_MODULE = HOST_MODULE.with_name('runtime-arm.py')
spec_arm = importlib.util.spec_from_file_location('runtime_arm', ARM_MODULE)
arm = importlib.util.module_from_spec(spec_arm)
spec_arm.loader.exec_module(arm)


def ensure_arm_archive():
    """Supply the pinned payload required by newly downloaded Android 13 images."""
    if arm.ARCHIVE.is_file() and arm.ARCHIVE.stat().st_size <= 64 * 1024**2:
        if hashlib.sha256(arm.ARCHIVE.read_bytes()).hexdigest() == arm.SHA256:
            return
    write_status(STATUS_FILE, 'installing', detail='Downloading verified ARM app support…', pid=os.getpid())
    arm.ARCHIVE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.arm-download-', dir=arm.ARCHIVE.parent)
    os.close(fd)
    try:
        url = ('https://codeload.github.com/supremegamers/'
               'vendor_google_proprietary_ndk_translation-prebuilt/zip/' + arm.COMMIT)
        result = subprocess.run(['curl', '-fsSL', '--proto', '=https', '--proto-redir', '=https',
                                 '--connect-timeout', '15', '--max-time', '180',
                                 '--max-filesize', str(64 * 1024**2), '-o', temporary, url],
                                capture_output=True, env=ENV, timeout=190)
        if result.returncode or Path(temporary).stat().st_size > 64 * 1024**2:
            raise RuntimeError('ARM support download failed; retry setup when the connection is available.')
        if hashlib.sha256(Path(temporary).read_bytes()).hexdigest() != arm.SHA256:
            raise RuntimeError('ARM support checksum mismatch; nothing was installed.')
        os.chmod(temporary, 0o644)
        os.replace(temporary, arm.ARCHIVE)
    finally:
        Path(temporary).unlink(missing_ok=True)



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


def validate_flavor(flavor):
    if flavor not in FLAVORS:
        raise ValueError(f'Flavor must be one of {", ".join(FLAVORS)}')


def support_complete(paths=SUPPORT_PATHS):
    return all(p.exists() for p in paths)


def missing_support(paths=SUPPORT_PATHS):
    return [str(p) for p in paths if not p.exists()]


def write_status(status_path, state, flavor=None, detail='', error=None, pid=None):
    payload = {'state': state, 'flavor': flavor, 'detail': detail, 'error': error, 'pid': pid}
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


def ensure_waydroid_prop():
    """Generate /var/lib/waydroid/waydroid.prop when waydroid >= 1.6 has not
    created it. Newer waydroid writes this file only while mounting a session,
    so an init on a running container can finish with the file absent; the
    controller's requirements check and runtime provisioning both need it.
    Mirrors waydroid's own make_prop (images.py): base prop + host identity +
    fixed container paths."""
    if WAYDROID_PROP.exists() or not BASE_PROP.exists():
        return False
    props = BASE_PROP.read_text().splitlines()
    cfg = configparser.ConfigParser()
    try:
        cfg.read(WAYDROID_CFG)
    except configparser.Error:
        pass

    def add_prop(key, section, option):
        if cfg.has_option(section, option):
            value = cfg.get(section, option)
            if value and value != 'None':
                props.append(key + '=' + value.replace('/mnt/', '/mnt_extra/'))

    for key, option in (('waydroid.host.user', 'user_name'),
                        ('waydroid.host.uid', 'user_id'),
                        ('waydroid.host.gid', 'group_id'),
                        ('waydroid.host_data_path', 'waydroid_data'),
                        ('waydroid.background_start', 'background_start')):
        add_prop(key, 'waydroid', option)
    props.append('waydroid.xdg_runtime_dir=/run/xdg')
    props.append('waydroid.pulse_runtime_path=/run/xdg/pulse')
    props.append('waydroid.wayland_display=wayland-0')
    if shutil.which('waydroid-sensord') is None:
        props.append('waydroid.stub_sensors_hal=1')
    for section in ('properties', 'waydroid'):
        if cfg.has_option(section, 'lcd_density'):
            density = cfg.get(section, 'lcd_density')
            if density and density != '0':
                props.append('ro.sf.lcd_density=' + density)
    temporary = WAYDROID_PROP.with_name('.waydroid.prop.new')
    temporary.write_text('\n'.join(props) + '\n')
    os.chmod(temporary, 0o644)
    os.replace(temporary, WAYDROID_PROP)
    return True


def apt(*args):
    with open(INIT_LOG, 'ab') as log:
        result = subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', *args],
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
    with open(log_path, 'ab') as log:
        installed = subprocess.run(['bash', '-e', '-s'], input=fetched.stdout,
                                   stdout=log, stderr=subprocess.STDOUT, env=ENV, timeout=600)
    if installed.returncode:
        raise RuntimeError(f'Waydroid repository setup failed; see {log_path}')
    # repo.waydro.id adds an apt source and updates metadata; it does NOT install
    # Waydroid. Without this step a clean host fails immediately at init.
    write_status(status_path, 'installing', detail='Installing Waydroid…', pid=os.getpid())
    apt('install', '-y', 'waydroid')


def run_init(flavor):
    # Body of the --run-init subcommand; runs detached as root.
    if os.geteuid() != 0:
        raise SystemExit('The init subcommand must run as root.')
    validate_flavor(flavor)
    ensure_host_packages()
    ensure_arm_archive()
    ensure_waydroid_prop()
    if support_complete():
        write_status(STATUS_FILE, 'done', flavor=flavor, detail='Android runtime setup is ready.', pid=os.getpid())
        return
    write_status(STATUS_FILE, 'initializing', flavor=flavor,
                 detail=f'waydroid init -s {flavor}', pid=os.getpid())
    waydroid = shutil.which('waydroid')
    if waydroid is None:
        write_status(STATUS_FILE, 'failed', flavor=flavor,
                     detail='waydroid binary not found after installation.',
                     error='waydroid binary not found after installation')
        raise SystemExit(1)
    process = subprocess.Popen([waydroid, 'init', '-s', flavor],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, env=ENV)
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        print(line)  # inherited fd: lands in INIT_LOG
        if line.strip():
            write_status(STATUS_FILE, 'initializing', flavor=flavor,
                         detail=tail(line, 400), pid=os.getpid())
    code = process.wait()
    # waydroid >= 1.6 may finish init without creating waydroid.prop (it is
    # only written while a session mounts); generate it from base prop + cfg.
    ensure_waydroid_prop()
    if code == 0 and support_complete():
        write_status(STATUS_FILE, 'done', flavor=flavor,
                     detail=f'Waydroid initialized ({flavor}).', pid=os.getpid())
        raise SystemExit(0)
    missing = missing_support()
    detail = f'waydroid init exited with {code}.'
    error = (f'waydroid init exited with {code} and configuration is still missing: '
             f'{", ".join(missing)}; see {INIT_LOG}')
    write_status(STATUS_FILE, 'failed', flavor=flavor,
                 detail=detail, error=error, pid=os.getpid())
    raise SystemExit(1)


def spawn_init(flavor, lock_fd):
    # GUI removes its stage after pkexec returns. Persist BOTH this helper and
    # its dependency before spawn, so delayed exec/reopening the AppImage works.
    helper_dir = STORE / 'setup'
    ensure_directory(helper_dir, 0o700)
    for source, name in ((Path(__file__).resolve(), 'setup-waydroid.py'),
                         (HOST_MODULE, 'runtime-host.py'), (ARM_MODULE, 'runtime-arm.py'),
                         (ARM_MODULE.with_name('runtime-arm-source.py'), 'runtime-arm-source.py')):
        fd, temporary = tempfile.mkstemp(prefix='.setup-', dir=helper_dir)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(source.read_bytes()); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, helper_dir / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
    with open(INIT_LOG, 'ab') as log:
        process = subprocess.Popen([PYTHON, '-B', str(helper_dir / 'setup-waydroid.py'),
                                    '--run-init', '--flavor', flavor, '--lock-fd', str(lock_fd)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, cwd='/', env=ENV, pass_fds=(lock_fd,))
    # The child writes all progress, including its PID. The parent must not
    # overwrite a fast child's final status with a stale 'initializing'.
    return process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-init', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--lock-fd', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--flavor', choices=FLAVORS, default='VANILLA')
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
                run_init(args.flavor)
            except Exception as error:
                write_status(STATUS_FILE, 'failed', flavor=args.flavor,
                             detail=tail(str(error)), error=tail(str(error)), pid=os.getpid())
                raise SystemExit(str(error))
            return
        write_status(STATUS_FILE, 'initializing', flavor=args.flavor,
                     detail='Starting Android runtime setup…', pid=os.getpid())
        try:
            spawn_init(args.flavor, descriptor)
        except Exception as error:
            write_status(STATUS_FILE, 'failed', error=tail(str(error)))
            raise
        print('Android runtime setup started. Progress and any errors will appear in the app.')
    finally:
        os.close(descriptor)


if __name__ == '__main__':
    main()
