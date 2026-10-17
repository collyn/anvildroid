"""Explicit, owner-scoped directory grants, applied only before Android starts.

The controller serializes configuration with runtime start/stop. No host mounts
are performed here: apply_preboot requires the worker's private mount namespace.
"""
import array
import ctypes
import json
import os
from pathlib import Path
import pwd
import re
import socket
import signal
import stat
import subprocess
import tempfile

SCHEMA = 1
FILE = 'shared-folders.json'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z')
RESERVED = {'.ssh', '.gnupg', '.aws', '.config', '.local', '.codex', '.agents', '.mozilla', '.git'}
LIBC = ctypes.CDLL(None, use_errno=True)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def _source(path, uid):
    """Open every component as the owner; never follow even intermediate links."""
    home = pwd.getpwuid(uid).pw_dir
    require(isinstance(path, str) and len(path) <= 4096 and '\x00' not in path, 'Invalid shared source')
    parts = Path(path).parts
    require(Path(path).is_absolute() and '..' not in parts and str(Path(path)) == path, 'Source must be a canonical absolute directory')
    require(path.startswith(home.rstrip('/') + '/') and not any(p in RESERVED for p in parts),
            'Select a non-sensitive subdirectory of your home folder')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:]:
            other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = other
        info = os.fstat(fd)
        require(info.st_uid == uid, 'Shared directory must belong to the desktop user')
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_source(path, uid):
    """SCM_RIGHTS pins a directory opened with the desktop user's privileges."""
    require(type(uid) is int and uid > 0, 'A non-root desktop owner is required')
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    child = os.fork()
    if child == 0:
        left.close()
        try:
            user = pwd.getpwuid(uid)
            if os.geteuid() == 0:
                os.setgroups([])
                os.setgid(user.pw_gid)
                os.setuid(uid)
            require(os.geteuid() == uid, 'Desktop owner mismatch')
            fd = _source(path, uid)
            right.sendmsg([b'OK'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
            os.close(fd)
        except BaseException as error:
            right.send(str(error).encode()[:2048])
        finally:
            os._exit(0)
    right.close()
    try:
        left.settimeout(10)
        data, controls, flags, _ = left.recvmsg(2048, socket.CMSG_SPACE(4))
        require(data == b'OK' and not flags, data.decode(errors='replace') or 'Could not open shared source')
        fds = array.array('i')
        for level, kind, value in controls:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds.frombytes(value[:len(value) - len(value) % fds.itemsize])
        if len(fds) != 1:
            for received in fds:
                os.close(received)
            raise RuntimeError('Invalid source descriptor')
        os.set_inheritable(fds[0], False)
        return fds[0]
    finally:
        left.close()
        # A stalled filesystem must not hold the privileged service forever.
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os.waitpid(child, 0)


def _config(instance):
    path = Path(instance) / FILE
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077 and info.st_size <= 65536, 'Unsafe shared-folder configuration')
        value = json.load(stream)
    require(value.get('schema') == SCHEMA and isinstance(value.get('shares'), list) and len(value['shares']) <= 16, 'Invalid shared-folder configuration')
    for item in value['shares']:
        require(isinstance(item, dict) and NAME.fullmatch(item.get('name', '')) and type(item.get('read_only')) is bool and type(item.get('owner_uid')) is int and isinstance(item.get('source'), str) and type(item.get('device')) is int and type(item.get('inode')) is int, 'Invalid shared-folder entry')
    require(len({x['name'] for x in value['shares']}) == len(value['shares']), 'Duplicate shared-folder name')
    return value['shares']


def _save(instance, entries):
    fd, temp = tempfile.mkstemp(prefix='.shares-', dir=instance)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'schema': SCHEMA, 'shares': entries}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, Path(instance) / FILE)
        directory = os.open(instance, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def list_shares(instance, owner_uid, mounted=None):
    entries = _config(instance)
    require(all(x['owner_uid'] == owner_uid for x in entries), 'Shared-folder owner mismatch')
    return {'shares': [dict(x, target='/sdcard/AnvilDroid/' + x['name'], configured=True,
                             mounted=bool((mounted or {}).get(x['name'], {}).get('mounted')),
                             error=(mounted or {}).get(x['name'], {}).get('error')) for x in entries],
            'activation': 'restart', 'max_shares': 16}


def configured_owner(instance):
    """The controller exclusively writes this private grant manifest."""
    entries = _config(instance)
    if not entries:
        return None
    owner = entries[0]['owner_uid']
    require(owner > 0 and all(item['owner_uid'] == owner for item in entries), 'Shared-folder owner mismatch')
    return owner


def configure(instance, owner_uid, payload):
    """Controller must reject mutation unless runtime is fully stopped."""
    name, source, read_only = payload.get('name'), payload.get('source'), payload.get('read_only')
    require(isinstance(name, str) and NAME.fullmatch(name), 'Use a short alphanumeric share name')
    require(type(read_only) is bool, 'Read-only mode must be a boolean')
    entries = list_shares(instance, owner_uid)['shares']
    entries = [{k: x[k] for k in ('name', 'source', 'read_only', 'owner_uid', 'device', 'inode')} for x in entries]
    require(len(entries) < 16 or any(x['name'] == name for x in entries), 'At most 16 shared folders are supported')
    fd = open_source(source, owner_uid)
    try:
        info = os.fstat(fd)
        item = dict(name=name, source=source, read_only=read_only, owner_uid=owner_uid, device=info.st_dev, inode=info.st_ino)
    finally:
        os.close(fd)
    _save(instance, [x for x in entries if x['name'] != name] + [item])
    return list_shares(instance, owner_uid)


def remove(instance, owner_uid, name):
    entries = _config(instance)
    require(all(x['owner_uid'] == owner_uid for x in entries), 'Shared-folder owner mismatch')
    require(isinstance(name, str) and NAME.fullmatch(name), 'Invalid share name')
    _save(instance, [x for x in entries if x['name'] != name])
    return list_shares(instance, owner_uid)


def _directory(parent, name):
    try:
        os.mkdir(name, mode=0o755, dir_fd=parent)
    except FileExistsError:
        pass
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def apply_preboot(instance, data_root, owner_uid, mount, *, original_mnt):
    """Bind shares before LXC boot; cleanup callback registers stable paths for teardown.

    Caller guarantees no guest is running, startup/configuration serialization,
    private propagation, and a controller-owned data_root mount in this worker.
    Failures roll back the individual bind and are returned as truthful status.
    """
    require(isinstance(original_mnt, str) and re.fullmatch(r'mnt:\[[0-9]+\]', original_mnt) and os.readlink('/proc/self/ns/mnt') != original_mnt,
            'Shared folders require a private runtime mount namespace')
    with open('/proc/self/mountinfo') as stream:
        require(not any(any(field.startswith(('shared:', 'master:', 'propagate_from:')) for field in line.split(' - ')[0].split()[6:]) for line in stream), 'Shared folders require private mount propagation')
    result = {}
    entries = _config(instance)
    require(all(x['owner_uid'] == owner_uid for x in entries), 'Shared-folder owner mismatch')
    for item in entries:
        source = target = parent = None
        bound = False
        try:
            source = open_source(item['source'], owner_uid)
            info = os.fstat(source)
            require((info.st_dev, info.st_ino) == (item['device'], item['inode']), 'Shared source changed; select it again')
            # Reject nested mounts; a plain bind would reveal their underlying files.
            with open('/proc/self/mountinfo') as stream:
                for line in stream:
                    point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
                    require(not point.startswith(item['source'].rstrip('/') + '/'), 'Shared source contains nested mounts')
            # Plain bind deliberately excludes child mounts (never --rbind).
            parent = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for component in ('media', '0', 'AnvilDroid'):
                other = _directory(parent, component)
                os.close(parent)
                parent = other
            target = _directory(parent, item['name'])
            target_path = f'/proc/{os.getpid()}/fd/{parent}/{item["name"]}'
            subprocess.run(['mount', '--bind', f'/proc/{os.getpid()}/fd/{source}', target_path], check=True, capture_output=True)
            bound = True
            subprocess.run(['mount', '-o', 'remount,bind,nosuid,nodev,noexec' + (',ro' if item['read_only'] else ',rw'), target_path], check=True, capture_output=True)
            observed = os.statvfs(target_path).f_flag
            require(bool(observed & os.ST_RDONLY) == item['read_only'] and all(observed & flag for flag in (os.ST_NOSUID, os.ST_NODEV, os.ST_NOEXEC)), 'Shared-folder mount mode verification failed')
            # Callback bookkeeping must use a stable data path, not closed fds.
            cleanup_path = str(Path(data_root) / 'media' / '0' / 'AnvilDroid' / item['name'])
            mount(cleanup_path)
            result[item['name']] = {'mounted': True, 'error': None}
        except Exception as error:
            if bound:
                subprocess.run(['umount', target_path], check=True, capture_output=True)
            result[item['name']] = {'mounted': False, 'error': str(error)[:512]}
        finally:
            for fd in (source, target, parent):
                if fd is not None:
                    os.close(fd)
    return result


def process_start(pid):
    require(type(pid) is int and pid > 0, 'Invalid Android process')
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def _walk(directory, components):
    fd = os.dup(directory)
    try:
        for component in components:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd); raise


def clone_bind(source, target):
    """Attach a nonrecursive clone using pinned FDs, preserving mount flags.

    The exec child is not visible in Android's procfs after setns; /proc/PID/fd
    paths cannot safely address its descriptors. Linux's mount FD API avoids
    both pathname races and accidental remounts of an underlying mount.
    """
    tree = LIBC.syscall(428, source, ctypes.c_char_p(b''), 1 | 0x80000 | 0x1000)
    if tree < 0:
        code = ctypes.get_errno(); raise OSError(code, os.strerror(code))
    try:
        if LIBC.syscall(429, tree, ctypes.c_char_p(b''), target, ctypes.c_char_p(b''), 0x4 | 0x40) < 0:
            code = ctypes.get_errno(); raise OSError(code, os.strerror(code))
    finally: os.close(tree)


def reconcile_guest(instance, data_root, pid, start, generation, original_mnt, timeout=10):
    """One exec-child per managed generation; never switch a worker thread's NS.

    Vold nonrecursively binds /data/media for its FUSE backend, omitting shares
    mounted before Android boot. Mirror only verified grants into that backend.
    Guest shutdown disposes these mounts; no guest-renamable unmount paths.
    """
    import time
    instance = Path(instance)
    info = instance.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077 and instance.resolve() == instance,
            'Unsafe sharing runtime directory')
    proc = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY)
    instance_fd = os.open(instance, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    def identity():
        fd = os.open('worker-identity.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=instance_fd)
        with os.fdopen(fd) as stream:
            metadata = os.fstat(stream.fileno())
            require(metadata.st_uid == 0 and not metadata.st_mode & 0o077 and stat.S_ISREG(metadata.st_mode), 'Unsafe sharing generation')
            current = json.load(stream)
        require(current == {'runtime_id': instance.name, 'generation': generation}, 'Sharing runtime generation changed')
        fd = os.open(str(pid) + '/stat', os.O_RDONLY, dir_fd=proc)
        with os.fdopen(fd) as stream:
            observed_start = stream.read().rsplit(')', 1)[1].split()[19]
        require(observed_start == start, 'Android sharing process changed')
    identity()
    entries = _config(instance)
    require(Path(data_root) == instance / 'android' / instance.name / 'data', 'Sharing userdata must belong to this runtime')
    expected = os.stat(data_root, follow_symlinks=False)
    require(stat.S_ISDIR(expected.st_mode), 'Invalid sharing userdata')
    try:
        return reconcile_verified(entries, expected, pid, start, original_mnt, identity, proc, timeout)
    finally:
        os.close(proc); os.close(instance_fd)


def reconcile_verified(entries, expected, pid, start, original_mnt, identity, proc, timeout=10):
    """Shared FD-only child core; caller pins and authenticates lifecycle state."""
    import time
    identity()
    namespace = os.open(f'/proc/{pid}/ns/mnt', os.O_RDONLY)
    guest = os.open(f'/proc/{pid}/root', os.O_RDONLY | os.O_DIRECTORY)
    data = media = None
    result = {}
    try:
        token = os.readlink(f'/proc/self/fd/{namespace}')
        require(token not in (original_mnt, os.readlink('/proc/self/ns/mnt')), 'Sharing requires a distinct Android mount namespace')
        data = _walk(guest, ('data',))
        observed = os.fstat(data)
        require((observed.st_dev, observed.st_ino) == (expected.st_dev, expected.st_ino), 'Android sharing userdata identity changed')
        media = _walk(data, ('media',))
        media_info = os.fstat(media)
        identity()
        os.setns(namespace, os.CLONE_NEWNS)
        require(os.readlink('self/ns/mnt', dir_fd=proc) == token, 'Android sharing namespace changed')
        deadline = time.monotonic() + timeout
        backend = None
        while backend is None:
            identity()
            try:
                candidate = _walk(guest, ('mnt', 'pass_through', '0', 'emulated'))
                observed = os.fstat(candidate)
                require((observed.st_dev, observed.st_ino) == (media_info.st_dev, media_info.st_ino), 'Android FUSE backend is not verified userdata media')
                fd = os.open(str(pid) + '/mountinfo', os.O_RDONLY, dir_fd=proc)
                with os.fdopen(fd) as stream:
                    require(any(line.split()[4] == '/mnt/pass_through/0/emulated' for line in stream), 'Android FUSE backend is not a mount')
                backend = candidate
            except (OSError, RuntimeError) as error:
                if 'candidate' in locals() and candidate is not None: os.close(candidate); candidate = None
                require(time.monotonic() < deadline, 'Android FUSE backend unavailable: ' + str(error))
                time.sleep(.2)
        try:
            for item in entries:
                source = parent = target = observed_fd = None
                try:
                    source = _walk(data, ('media', '0', 'AnvilDroid', item['name']))
                    observed = os.fstat(source); flags = os.fstatvfs(source).f_flag
                    require((observed.st_dev, observed.st_ino) == (item['device'], item['inode']), 'Raw Android share identity changed')
                    require(bool(flags & os.ST_RDONLY) == item['read_only'] and all(flags & f for f in (os.ST_NOSUID, os.ST_NODEV, os.ST_NOEXEC)), 'Raw Android share mode changed')
                    parent = _walk(backend, ('0', 'AnvilDroid'))
                    target = os.open(item['name'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    clone_bind(source, target)
                    observed_fd = _walk(backend, ('0', 'AnvilDroid', item['name']))
                    observed = os.fstat(observed_fd); flags = os.fstatvfs(observed_fd).f_flag
                    require((observed.st_dev, observed.st_ino) == (item['device'], item['inode']) and bool(flags & os.ST_RDONLY) == item['read_only'] and all(flags & f for f in (os.ST_NOSUID, os.ST_NODEV, os.ST_NOEXEC)), 'Android FUSE share readback changed')
                    result[item['name']] = {'mounted': True, 'error': None}
                except Exception as error:
                    result[item['name']] = {'mounted': False, 'error': 'Android FUSE share unavailable: ' + str(error)[:400]}
                finally:
                    for fd in (source, parent, target, observed_fd):
                        if fd is not None: os.close(fd)
        finally: os.close(backend)
        identity()
        return result
    finally:
        for fd in (namespace, guest, data, media):
            if fd is not None: os.close(fd)


if __name__ == '__main__':
    import sys
    require(len(sys.argv) == 8 and sys.argv[1] == '--reconcile', 'Invalid sharing reconciliation invocation')
    _, _, instance, data, pid, start, generation, original_mnt = sys.argv
    print(json.dumps(reconcile_guest(instance, data, int(pid), start, generation, original_mnt)))
