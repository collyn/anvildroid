"""Explicit, owner-scoped directory grants, applied only before Android starts.

The controller serializes configuration with runtime start/stop. No host mounts
are performed here: apply_preboot requires the worker's private mount namespace.
"""
import array
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
