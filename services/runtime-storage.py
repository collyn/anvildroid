"""Transactional storage relocation behind stable, root-owned runtime anchors.
External directories are created by the controller under a caller-owned directory.
No arbitrary existing directory is overwritten or adopted as runtime data.
"""
import fcntl
import ctypes
import copy
import threading
import hashlib
import json
import os
import pwd
import re
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import uuid


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.storage-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def open_directory(path, caller_uid=None):
    """Pin every path component; reject symlink traversal, including ancestors."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or len(str(path)) > 3500:
        raise RuntimeError('Choose an absolute folder path without symlinks or ..')
    groups = set()
    if caller_uid not in (None, 0):
        account = pwd.getpwuid(caller_uid)
        groups = set(os.getgrouplist(account.pw_name, account.pw_gid))
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            if caller_uid not in (None, 0):
                info = os.fstat(next_fd)
                bit = stat.S_IXUSR if info.st_uid == caller_uid else stat.S_IXGRP if info.st_gid in groups else stat.S_IXOTH
                if not info.st_mode & bit:
                    os.close(next_fd)
                    raise RuntimeError('Choose a folder accessible to your Linux account')
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def check_filesystem(fd, allow_temporary=False):
    buffer = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(fd, ctypes.byref(buffer)) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot inspect storage filesystem')
    magic = ctypes.c_long.from_buffer(buffer).value & 0xffffffff
    allowed = {0xef53, 0x58465342, 0x9123683e}  # ext4, XFS, Btrfs
    if allow_temporary: allowed.add(0x01021994)  # root-only test harness
    if magic not in allowed:
        raise RuntimeError('Choose permanent local ext4, XFS or Btrfs storage; removable Windows-format, network, FUSE and RAM folders are not supported')


def identity(fd):
    info = os.fstat(fd)
    if info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('Runtime storage must remain a private root-owned directory')
    return {'device': info.st_dev, 'inode': info.st_ino, 'fsid': os.fstatvfs(fd).f_fsid}


def same_identity(actual, expected):
    # Linux device numbers can change when disks are enumerated after reboot.
    return all(actual[key] == expected[key] for key in ('fsid', 'inode'))


def directory_identity(path):
    fd = open_directory(path)
    try: return identity(fd)
    finally: os.close(fd)


def tree_manifest(root):
    """Verify content, ownership, modes, links and xattrs without following links."""
    result = {}
    hardlinks = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if relative == 'worker.sock': continue
            info = path.lstat()
            entry = [stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
            if stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
                group = hardlinks.setdefault((info.st_dev, info.st_ino), relative)
                entry += [info.st_size, digest.hexdigest(), group]
            elif stat.S_ISLNK(info.st_mode): entry += [os.readlink(path)]
            elif not stat.S_ISDIR(info.st_mode): entry += [info.st_rdev]
            attributes = sorted((name, os.getxattr(path, name, follow_symlinks=False).hex())
                                for name in os.listxattr(path, follow_symlinks=False))
            entry.append(attributes)
            result[relative] = entry
    return result


def is_mountpoint(path):
    # os.path.ismount misses same-filesystem bind mounts.
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
    return any(unescape(line.split()[4]) == str(path) for line in Path('/proc/self/mountinfo').read_text().splitlines())


class Storage:
    def __init__(self, root, allow_temporary=False):
        self.mutex = threading.RLock()
        self.busy = None
        self.allow_temporary = allow_temporary
        self.root = root
        self.anchors = root / 'instances'
        self.retired = root / 'retired-storage'
        self.retired.mkdir(mode=0o700, exist_ok=True)
        retired_fd = open_directory(self.retired)
        try: identity(retired_fd)
        finally: os.close(retired_fd)
        self.path = root / 'storage.json'
        self.entries = {}
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise RuntimeError('Unsafe storage metadata')
            data = json.loads(self.path.read_text())
            if data.get('version') != 1 or not isinstance(data.get('entries'), dict):
                raise RuntimeError('Unsupported storage metadata')
            self.entries = data['entries']
            for identifier, entry in self.entries.items():
                if not re.fullmatch(r'r-[0-9a-f]{32}', identifier) or not isinstance(entry, dict):
                    raise RuntimeError('Invalid storage runtime ID')
                if entry['job']['status'] not in ('Running', 'Succeeded', 'Failed') or entry['job']['phase'] not in ('copy', 'verify', 'switch', 'cleanup', 'complete'):
                    raise RuntimeError('Invalid storage transaction')
                for key in ('active', 'pending', 'previous'):
                    location = entry.get(key)
                    if not location: continue
                    path = Path(location['path'])
                    expected = identifier + '-' + entry['job']['id'] if location.get('anchor_original') else None
                    if not path.is_absolute() or '..' in path.parts or not all(type(location[k]) is int and location[k] >= 0 for k in ('device', 'inode', 'fsid')):
                        raise RuntimeError('Invalid storage location')
                    if expected:
                        if path != self.retired / expected: raise RuntimeError('Invalid retired storage location')
                    elif not re.fullmatch(r'\.anvildroid-' + identifier + r'-[0-9a-f]{8}', path.name):
                        raise RuntimeError('Invalid external storage location')
        # The controller does not accept requests until all interrupted cutovers
        # have either been reconciled or explicitly marked unavailable.
        self.unavailable = {}
        for identifier, entry in list(self.entries.items()):
            try:
                if entry['job']['status'] == 'Running' and entry['job']['phase'] in ('copy', 'verify'):
                    entry['job'].update(status='Failed', error='Move interrupted before verification. Original storage is unchanged; retry the move.')
                if entry.get('active'):
                    self.attach(identifier, entry)
                if entry['job']['phase'] == 'switch':
                    entry['job'].update(status='Succeeded', phase='complete', error=None)
                elif entry['job']['phase'] == 'cleanup':
                    self.discard_previous(identifier)
            except Exception as error:
                self.unavailable[identifier] = str(error)
                entry['job'].update(status='Failed', error=str(error))
        if self.entries: self.save()

    def save(self):
        with self.mutex:
            atomic(self.path, {'version': 1, 'entries': copy.deepcopy(self.entries)})

    def report(self, identifier):
        entry = self.entries.get(identifier)
        return {'path': entry['active']['path'] if entry and entry.get('active') else str(self.anchors / identifier),
                'job': entry['job'] if entry else None,
                'previous_path': (entry.get('previous') or entry.get('pending') or {}).get('path') if entry else None,
                'error': self.unavailable.get(identifier)}

    def ensure(self, identifier):
        entry = self.entries.get(identifier)
        if entry and entry.get('active'):
            try:
                was_unavailable = identifier in self.unavailable
                self.attach(identifier, entry)
                self.unavailable.pop(identifier, None)
                if entry['job']['phase'] == 'switch' or (was_unavailable and entry['job']['phase'] == 'complete'):
                    entry['job'].update(status='Succeeded', phase='complete', error=None)
                    self.save()
            except Exception as error:
                self.unavailable[identifier] = str(error)
                raise RuntimeError('Storage location unavailable: ' + str(error))

    def attach(self, identifier, entry):
        active = entry['active']
        fd = open_directory(active['path'])
        try:
            if not same_identity(identity(fd), active):
                raise RuntimeError('Storage disk/folder identity changed; refusing an empty or replacement location')
            anchor = self.anchors / identifier
            if anchor.exists() and not anchor.is_symlink() and same_identity(directory_identity(anchor), active): return
            if is_mountpoint(anchor):
                subprocess.run(['umount', str(anchor)], check=True, capture_output=True)
            if anchor.exists():
                old = entry.get('previous')
                info = anchor.lstat()
                if stat.S_ISLNK(info.st_mode): raise RuntimeError('Unsafe runtime anchor')
                if old and old.get('anchor_original') and same_identity(directory_identity(anchor), old):
                    os.rename(anchor, old['path'])
                    sync_directory(self.anchors)
                    sync_directory(self.retired)
                elif any(anchor.iterdir()):
                    raise RuntimeError('Unexpected data at runtime anchor; retained for recovery')
            anchor.mkdir(mode=0o700, exist_ok=True)
            subprocess.run(['mount', '--bind', f'/proc/self/fd/{fd}', str(anchor)], pass_fds=(fd,), check=True, capture_output=True)
        finally: os.close(fd)

    def begin(self, identifier, destination, uid):
        self.ensure(identifier)
        if self.busy is not None or any(entry['job']['status'] == 'Running' for entry in self.entries.values()):
            raise RuntimeError('Another storage move is in progress')
        current = self.entries.get(identifier, {})
        if current.get('pending'): raise RuntimeError('An interrupted copy is retained. Remove that copy before retrying.')
        if current.get('previous'): raise RuntimeError('A previous storage copy is retained. Remove it before moving again.')
        if current.get('active') and (Path(destination) == Path(current['active']['path']) or Path(current['active']['path']) in Path(destination).parents):
            raise RuntimeError('Destination cannot be inside this runtime')
        parent = open_directory(destination, uid)
        destination_fd = None
        try:
            check_filesystem(parent, self.allow_temporary)
            metadata = os.fstat(parent)
            if metadata.st_uid != uid or not metadata.st_mode & stat.S_IWUSR:
                raise RuntimeError('Choose a folder owned and writable by your Linux account')
            anchor = self.anchors / identifier
            # Nested destinations would copy a runtime into itself.
            if Path(destination) == anchor or anchor in Path(destination).parents or self.root == Path(destination) or self.root in Path(destination).parents:
                raise RuntimeError('Choose a folder outside the controller storage')
            token = uuid.uuid4().hex
            name = '.anvildroid-' + identifier + '-' + token[:8]
            os.mkdir(name, 0o700, dir_fd=parent)
            destination_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            check_filesystem(destination_fd, self.allow_temporary)
            target = dict(path=str(Path(destination) / name), **identity(destination_fd))
            current = self.entries.get(identifier, {})
            entry = {'active': current.get('active'),
                     'job': {'id': token, 'status': 'Running', 'phase': 'copy', 'error': None},
                     'pending': target}
            # Retain older copies explicitly; do not silently lose their paths.
            self.entries[identifier] = entry
            self.save()
            self.busy = identifier
            return destination_fd
        except Exception:
            if destination_fd is not None: os.close(destination_fd)
            raise
        finally: os.close(parent)

    def move(self, identifier, destination_fd, progress):
        entry = self.entries[identifier]
        anchor = self.anchors / identifier
        lock = None
        try:
            lock = os.open(anchor / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source_fd = open_directory(anchor)
            try:
                original = identity(source_fd)
                target = Path(f'/proc/self/fd/{destination_fd}')
                source = Path(f'/proc/self/fd/{source_fd}')
                size = sum(p.stat().st_size for p in anchor.rglob('*') if p.is_file() and not p.is_symlink())
                if os.fstatvfs(destination_fd).f_bavail * os.fstatvfs(destination_fd).f_frsize < size + 64 * 1024 * 1024:
                    raise RuntimeError('Not enough free space at the destination. Original storage is unchanged.')
                subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always', str(source) + '/.', str(target)],
                               pass_fds=(source_fd, destination_fd), check=True, capture_output=True)
                # Dead worker sockets are never copied into a new live endpoint.
                (target / 'worker.sock').unlink(missing_ok=True)
                progress('verify')
                if tree_manifest(source) != tree_manifest(target):
                    raise RuntimeError('Storage verification failed. Original storage is unchanged.')
                for directory, _, files in os.walk(target):
                    for name in files:
                        file = Path(directory) / name
                        if file.is_file() and not file.is_symlink():
                            with file.open('rb') as stream: os.fsync(stream.fileno())
                    sync_directory(Path(directory))
                os.fsync(destination_fd)
                # Reopen the published path before committing: a renamed parent
                # must not redirect the persisted destination to another inode.
                published = open_directory(entry['pending']['path'])
                try:
                    if not same_identity(identity(published), identity(destination_fd)): raise RuntimeError('Destination folder changed during copy')
                finally: os.close(published)
                previous = entry.get('active') or dict(path=str(self.retired / (identifier + '-' + entry['job']['id'])), anchor_original=True, **original)
                entry.update(previous=previous, active=entry.pop('pending'))
                entry['job']['phase'] = 'switch'
                self.save()  # durable commit: recovery completes this cutover
                self.attach(identifier, entry)
                entry['job'].update(status='Succeeded', phase='complete', error=None)
                self.unavailable.pop(identifier, None)
                self.save()
            finally: os.close(source_fd)
        except Exception as error:
            if entry.get('active') and entry['job']['phase'] == 'switch':
                self.unavailable[identifier] = str(error)
            entry['job'].update(status='Failed', error=str(error))
            self.save()
        finally:
            if lock is not None: os.close(lock)
            os.close(destination_fd)
            self.busy = None

    def begin_cleanup(self, identifier):
        if self.busy is not None or any(e['job']['status'] == 'Running' for e in self.entries.values()):
            raise RuntimeError('Wait for the current storage operation')
        self.ensure(identifier)
        entry = self.entries.get(identifier)
        if not entry or not (entry.get('previous') or entry.get('pending')):
            raise RuntimeError('No retained copy to remove')
        entry['job'].update(status='Running', phase='cleanup', error=None)
        self.save()
        self.busy = identifier

    def discard_previous(self, identifier):
        self.ensure(identifier)
        entry = self.entries.get(identifier)
        if not entry or not (entry.get('previous') or entry.get('pending')): raise RuntimeError('No previous storage copy')
        if self.busy is not None and not (self.busy == identifier and entry['job']['phase'] == 'cleanup'):
            raise RuntimeError('Wait for the storage operation to finish')
        if any(e['job']['status'] == 'Running' and not (key == identifier and e['job']['phase'] == 'cleanup') for key, e in self.entries.items()): raise RuntimeError('Wait for the storage move to finish')
        key = 'previous' if entry.get('previous') else 'pending'
        old = entry[key]
        path = Path(old['path'])
        parent = open_directory(path.parent)
        try:
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                if os.fstatvfs(parent).f_fsid != old['fsid']: raise RuntimeError('Previous disk is unavailable')
                del entry[key]
                entry['job'].update(status='Succeeded', phase='complete', error=None)
                self.save()
                return
            try:
                if not same_identity(identity(fd), old): raise RuntimeError('Previous location changed; refusing deletion')
                if entry.get('active') and same_identity(identity(fd), entry['active']): raise RuntimeError('Cannot delete active storage')
                # The caller owns the parent and can rename its children. Delete
                # only through the verified private directory descriptor, never
                # reopen the untrusted child name for recursive removal.
                for name in os.listdir(fd):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode): shutil.rmtree(name, dir_fd=fd)
                    else: os.unlink(name, dir_fd=fd)
                os.fsync(fd)
                published = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    if not same_identity(identity(published), old): raise RuntimeError('Retained directory was renamed during cleanup')
                finally: os.close(published)
                # rmdir cannot recursively affect a replacement directory.
                os.rmdir(path.name, dir_fd=parent)
            finally: os.close(fd)
            os.fsync(parent)
            del entry[key]
            entry['job'].update(status='Succeeded', phase='complete', error=None)
            self.save()
        finally:
            os.close(parent)
            if self.busy == identifier: self.busy = None


def deletion_plan(storage, identifier):
    """Capture only this runtime's known directory identities before removal."""
    storage.ensure(identifier)
    entry = storage.entries.get(identifier, {})
    paths = []
    anchor = storage.anchors / identifier
    if anchor.exists():
        expected = directory_identity(anchor)
        paths.append(dict(expected, path=str(anchor), detach=bool(entry.get('active'))))
    for key in ('active', 'previous', 'pending'):
        location = entry.get(key)
        if location:
            actual = directory_identity(location['path'])
            if not same_identity(actual, location): raise RuntimeError('Storage identity changed; deletion refused')
            paths.append(dict(location, detach=False))
    for location in paths:
        assert_no_child_mounts(location['path'])
    return paths


def assert_no_child_mounts(path):
    prefix = str(path).rstrip('/') + '/'
    def unescape(value):
        return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        if unescape(line.split()[4]).startswith(prefix):
            raise RuntimeError('Runtime storage still contains mounts; stop Android and retry')


def delete_location(location):
    path = Path(location['path'])
    parent = open_directory(path.parent)
    try:
        try: fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            if os.fstatvfs(parent).f_fsid != location['fsid']: raise RuntimeError('Storage disk is unavailable')
            return
        try:
            if location.get('detach') and not is_mountpoint(path):
                identity(fd)
                if os.listdir(fd): raise RuntimeError('Detached storage anchor is not empty')
                return  # A crash may occur after unmount and before journaling it.
            if not same_identity(identity(fd), location): raise RuntimeError('Storage identity changed; deletion refused')
            assert_no_child_mounts(path)
            if location.get('detach'):
                if not is_mountpoint(path): raise RuntimeError('Storage anchor is not the expected mount')
                os.close(fd)
                fd = None  # An open descriptor into the bind mount makes umount busy.
                subprocess.run(['umount', str(path)], check=True, capture_output=True)
                return  # Remove its now-empty anchor after all directories are deleted.
            if is_mountpoint(path): raise RuntimeError('Refusing to delete a mounted directory')
            for name in os.listdir(fd):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode): shutil.rmtree(name, dir_fd=fd)
                else: os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            current = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if not same_identity(identity(current), location): raise RuntimeError('Directory changed during deletion')
            finally: os.close(current)
            os.rmdir(path.name, dir_fd=parent)
        finally:
            if fd is not None: os.close(fd)
        os.fsync(parent)
    finally: os.close(parent)
