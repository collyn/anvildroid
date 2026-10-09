"""Fixed-command helpers for the isolated Android worker."""
import os
import subprocess
import signal
import tempfile
import re
import sys
import sqlite3
import stat
from pathlib import Path


def read_gsf_id(data_root):
    """Query a private snapshot; never open Android-controlled paths with SQLite."""
    path = Path(data_root) / 'data/com.google.android.gsf/databases'
    require(path.is_absolute(), 'Android data path is unavailable')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            require(part not in ('.', '..'), 'Invalid Android data path')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        with tempfile.TemporaryDirectory(prefix='anvildroid-gsf-') as temporary:
            for name in ('gservices.db', 'gservices.db-wal', 'gservices.db-journal'):
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                except FileNotFoundError:
                    if name == 'gservices.db': raise RuntimeError('GSF database is not initialized. Open Play Store, then check again.')
                    continue
                with os.fdopen(fd, 'rb') as source:
                    before = os.fstat(source.fileno())
                    require(stat.S_ISREG(before.st_mode) and before.st_size <= 32 * 1024**2, 'Invalid GSF database snapshot')
                    with open(Path(temporary) / name, 'xb') as target:
                        remaining = before.st_size
                        while remaining:
                            chunk = source.read(min(remaining, 65536))
                            require(chunk, 'GSF database changed. Check again.')
                            target.write(chunk)
                            remaining -= len(chunk)
                    after = os.fstat(source.fileno())
                    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'GSF database changed. Check again.')
            connection = sqlite3.connect((Path(temporary) / 'gservices.db').as_uri() + '?mode=ro', uri=True, timeout=1)
            try:
                connection.execute('PRAGMA trusted_schema=OFF')
                connection.set_progress_handler(lambda: 1, 100000)
                rows = connection.execute("SELECT value FROM main WHERE name='android_id' LIMIT 2").fetchall()
                if not rows: return None
                require(len(rows) == 1 and isinstance(rows[0][0], (str, int)), 'Invalid GSF Android ID')
                value = str(rows[0][0])
                require(re.fullmatch(r'[0-9]{1,20}', value), 'Invalid GSF Android ID')
                return value
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError('Cannot read GSF database. Open Play Store and check again; if this persists, check runtime diagnostics.') from error
    finally:
        os.close(directory)


def android_pid_limit_is_private():
    """Bionic's 32-bit recursive mutex stores its owner TID in 16 bits."""
    version = re.match(r'^(\d+)\.(\d+)', os.uname().release)
    require(version is not None, 'Cannot determine kernel PID namespace support')
    if tuple(map(int, version.groups())) >= (6, 14):
        # Since 6.14 new PID namespaces do not inherit the host limit.
        return True
    require(int(Path('/proc/sys/kernel/pid_max').read_text()) <= 65535,
            'Android 32-bit processes require pid_max <= 65535. This kernel lacks a private PID limit; use Linux 6.14+ or configure the host limit explicitly.')
    return False  # Never write the host-global sysctl on older kernels.


def run(*args, timeout=20, umask=-1, include_stderr=False):
    args = android_command(args)
    process = subprocess.Popen(list(map(str, args)), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               start_new_session=True, umask=umask)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # lxc-attach can leave a shell/package-manager child behind. Kill the
        # whole session so a timed-out Android command cannot hold mounts or
        # keep the runtime worker stuck in Starting forever.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Do not call communicate() again here: a descendant can retain the
        # stdout/stderr pipe after the leader is dead, which would deadlock
        # the worker exactly when the timeout is meant to recover it.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        stdout = stderr = ''
        raise subprocess.TimeoutExpired(args, timeout, output=stdout, stderr=stderr)
    if process.returncode:
        raise RuntimeError(f'{args[0]} failed: {stderr} {stdout}')
    return (stdout + ('\n' + stderr if include_stderr else '')).strip()


def require(value, message):
    if not value:
        raise RuntimeError(message)


def ns(kind):
    return os.readlink('/proc/self/ns/' + kind)


def run_bounded(*args, timeout, umask=-1, stdin=subprocess.DEVNULL, operation="Android shutdown", output_limit=8192, full_output=False):
    """A timed-out attach must not leave children holding LXC control descriptors."""
    args = android_command(args)
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(list(map(str, args)), stdin=stdin,
                                   stdout=output, stderr=output, start_new_session=True, umask=umask)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait()
            raise RuntimeError(operation + ' command timed out')
        output.seek(0, os.SEEK_END)
        size=output.tell()
        require(not full_output or size<=output_limit, operation+' output exceeds limit')
        output.seek(max(0, size - output_limit))
        text = output.read().decode(errors='replace').strip()
        require(process.returncode == 0, operation + ' failed: ' + (text[:512] or 'command returned an error'))
        return text


def android_command(args):
    # Android shell scripts (settings, ime, uiautomator) invoke `cmd`/app_process
    # by name. lxc-attach otherwise inherits a host PATH without /system/bin.
    if args and str(args[0]) == 'lxc-attach':
        return (args[0], '--set-var', 'PATH=/system/bin:/system/xbin:/vendor/bin', *args[1:])
    return args


def limit_android_pids(pid):
    """Enter only the pinned child PID namespace; Android mounts sysctl read-only."""
    require(android_pid_limit_is_private(), 'Private PID limits require Linux 6.14+')
    require(type(pid) is int and pid > 0, 'Invalid Android init PID')
    namespace = Path('/proc') / str(pid) / 'ns/pid'
    fd = os.open(namespace, os.O_RDONLY)
    try:
        expected = os.readlink(f'/proc/self/fd/{fd}')
        require(expected != ns('pid'), 'Android must have a private PID namespace')
        code = ('import os,sys; from pathlib import Path; '
                'assert os.readlink("/proc/self/ns/pid") == sys.argv[1]; '
                'p=Path("/proc/sys/kernel/pid_max"); p.write_text("65535\\n"); '
                'assert int(p.read_text()) == 65535')
        # A procfs mounted for the parent PID namespace still exposes that
        # namespace's sysctls after setns. Mount proc privately *after* entering
        # the child; never remount Android's procfs or write the host sysctl.
        result = subprocess.run(['nsenter', '--pid=/proc/self/fd/' + str(fd), '--',
                                 'unshare', '--mount', '--propagation', 'private',
                                 '--mount-proc', '--', sys.executable, '-c', code, expected],
                                pass_fds=(fd,), capture_output=True, text=True, timeout=10)
        require(result.returncode == 0, 'Cannot set private Android PID limit: ' + result.stderr[-512:])
    finally:
        os.close(fd)
