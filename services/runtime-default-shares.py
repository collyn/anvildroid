"""Owner-scoped Default grants inside the Waydroid service's private session.

No source paths enter LXC configuration. Userdata and grant directories are
pinned; only root-owned staging paths are passed to LXC. Namespace disposal,
rather than a guest-renamable pathname, revokes mounts after verified stop.
"""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import threading
import time
import sys
import types


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


shares = module('runtime-shares')
storage = module('runtime-storage')
linux = module('runtime-linux')
STATE = Path('/run/anvildroid-system')
TRUSTED_UID = 0


def require(value, message):
    if not value: raise RuntimeError(message)


class NamespaceBusy(RuntimeError):
    """A stopped session still has live namespace holders; keep its lock."""


def private_directory(path, create=False):
    path = Path(path)
    if create: path.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = storage.open_directory(path)
    try:
        info = os.fstat(fd)
        require(info.st_uid == TRUSTED_UID and not info.st_mode & 0o077, 'Default sharing directory must be private and root-owned')
    finally: os.close(fd)
    return path


def read_json(path, public=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == TRUSTED_UID and info.st_nlink == 1 and
                not info.st_mode & (0o022 if public else 0o077) and info.st_size <= 65536, 'Unsafe Default sharing metadata')
        value = json.load(stream)
    require(isinstance(value, dict), 'Invalid Default sharing metadata')
    return value


def process_start(pid):
    require(type(pid) is int and pid > 0, 'Invalid service process')
    # comm may contain spaces or parentheses; starttime is field 22.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def integration_status(state_dir=STATE):
    try:
        private_directory(state_dir)
        value = read_json(Path(state_dir) / 'integration.json', public=True)
        pid = value['pid']
        require(value.get('version') == 2 and value.get('sharing_version') == 1 and type(pid) is int and pid > 1,
                'Restart the Waydroid container service to activate shared-folder integration')
        require(value['start_time'] == process_start(pid) and Path(f'/proc/{pid}').stat().st_uid == TRUSTED_UID,
                'Stale Waydroid integration process')
        expected = str(Path(__file__).with_name('runtime-system.py')).encode()
        require(expected in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'), 'Unexpected Waydroid integration process')
        observed = os.readlink(f'/proc/{pid}/ns/mnt')
        require(observed == value['mount_namespace'] and observed != value['original_mount_namespace'],
                'Waydroid sharing requires an isolated service mount namespace')
        return value
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise RuntimeError('Default shared folders are unavailable: ' + str(error)) from error


def lifecycle_lock(store_root, blocking=False):
    private_directory(store_root)
    fd = os.open(Path(store_root) / 'default-shares.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == TRUSTED_UID and not info.st_mode & 0o077 and info.st_nlink == 1,
                'Unsafe Default sharing lifecycle lock')
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return fd
    except BaseException:
        os.close(fd); raise


def grant_directory(store_root, uid, create=False):
    require(type(uid) is int and uid > 0, 'A non-root Default owner is required')
    base = Path(store_root) / 'default-shares'
    private_directory(base, create=create)
    return private_directory(base / str(uid), create=create)


def data_descriptor(saved, uid, session=None):
    require(saved.get('owner_uid') == uid and isinstance(saved.get('data_identity'), dict),
            'Enable Default management while running once to verify its data directory')
    path = saved.get('data_path')
    require(isinstance(path, str) and path.startswith('/') and path != '/', 'Invalid Default data directory')
    if session is not None:
        require(session.get('user_id') == str(uid) and session.get('waydroid_data') == path,
                'Default session does not match the authorized owner and data directory')
    fd = storage.open_directory(path)
    try:
        info = os.fstat(fd)
        require(info.st_uid in (0, uid) and info.st_uid == saved.get('data_owner_uid', uid) and
                saved['data_identity'] == {'device': info.st_dev, 'inode': info.st_ino},
                'Default userdata changed; enable management again before sharing')
        return fd
    except BaseException:
        os.close(fd); raise


def binding(saved):
    return {key: saved[key] for key in ('owner_uid', 'data_path', 'data_identity', 'data_owner_uid')}


def walk_target(data_fd, name, create=False):
    fd = os.dup(data_fd)
    try:
        for part in ('media', '0', 'AnvilDroid', name):
            if create:
                other = shares._directory(fd, part)
            else:
                other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = other
        return fd
    except BaseException:
        os.close(fd); raise


class DefaultShares:
    def __init__(self, store_root, runtime, state_dir=STATE):
        self.store_root, self.runtime, self.state_dir = Path(store_root), runtime, Path(state_dir)

    def _directory(self, uid, create=False):
        return grant_directory(self.store_root, uid, create)

    def get(self, uid):
        session, saved = self.runtime.authorize(uid)
        try: directory = self._directory(uid)
        except FileNotFoundError: directory = None
        entries = shares.list_shares(directory, uid) if directory else {'shares': [], 'activation': 'restart', 'max_shares': 16}
        available, reason, mounted = True, None, {}
        try: marker = integration_status(self.state_dir)
        except RuntimeError as error: available, reason = False, str(error)
        state = self.runtime.state()
        if available and session and state in ('RUNNING', 'FROZEN'):
            try:
                status = read_json(self.state_dir / 'shares-session.json')
                require(status.get('pid') == marker['pid'] and status.get('start_time') == marker['start_time'] and
                        status.get('mount_namespace') == marker['mount_namespace'] and status.get('owner_uid') == uid and
                        status.get('data_identity') == saved.get('data_identity') and status.get('session_pid') == session.get('pid'),
                        'Default sharing session identity changed')
                pid = status['container_pid']
                require(status['container_start_time'] == process_start(pid), 'Default Android process changed')
                fd = data_descriptor(saved, uid, session)
                try: self.runtime.verify_live_data(os.fstat(fd))
                finally: os.close(fd)
                data = os.open(f'/proc/{pid}/root/data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for item in entries['shares']:
                        observed = status.get('shares', {}).get(item['name'], {})
                        if not observed.get('mounted'):
                            mounted[item['name']] = observed
                            continue
                        root = os.open(f'/proc/{pid}/root', os.O_RDONLY | os.O_DIRECTORY)
                        backend = media = None
                        try:
                            backend = shares._walk(root, ('mnt', 'pass_through', '0', 'emulated'))
                            media = shares._walk(data, ('media',))
                            first, second = os.fstat(backend), os.fstat(media)
                            require((first.st_dev, first.st_ino) == (second.st_dev, second.st_ino), 'Default Android FUSE backend changed')
                            target = shares._walk(backend, ('0', 'AnvilDroid', item['name']))
                        finally:
                            for descriptor in (root, backend, media):
                                if descriptor is not None: os.close(descriptor)
                        try:
                            info = os.fstat(target); flags = os.fstatvfs(target).f_flag
                            require((info.st_dev, info.st_ino) == (item['device'], item['inode']) and
                                    bool(flags & os.ST_RDONLY) == item['read_only'] and
                                    all(flags & flag for flag in (os.ST_NOSUID, os.ST_NODEV, os.ST_NOEXEC)), 'Default guest FUSE share mount changed')
                            mounted[item['name']] = {'mounted': True, 'error': None}
                        finally: os.close(target)
                    require(status['container_start_time'] == process_start(pid), 'Default Android process changed during share readback')
                finally: os.close(data)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
                mounted = {item['name']: {'mounted': False, 'error': str(error)} for item in entries['shares']}
        editable = available and state == 'STOPPED'
        if editable:
            try:
                lock = lifecycle_lock(self.store_root); os.close(lock)
            except (OSError, RuntimeError):
                editable = False
                reason = 'Default startup or cleanup still holds the sharing lifecycle lock; stop the container completely and retry.'
        if directory: entries = shares.list_shares(directory, uid, mounted)
        return dict(entries, runtime_id='default', editable=editable, integration_available=available,
                    **({'unsupported_reason': reason} if reason else {}))

    def set(self, uid, payload):
        lock = lifecycle_lock(self.store_root)
        try:
            _, saved = self.runtime.authorize(uid); self.runtime.stopped(); integration_status(self.state_dir)
            fd = data_descriptor(saved, uid)
            os.close(fd)
            directory = self._directory(uid, create=True)
            try:
                old = read_json(directory / 'data.json')
                require(old == binding(saved) or not shares.list_shares(directory, uid)['shares'],
                        'Remove previous grants before changing Default userdata')
            except FileNotFoundError: pass
            storage.atomic(directory / 'data.json', binding(saved))
            shares.configure(directory, uid, payload)
        finally: os.close(lock)
        return self.get(uid)

    def remove(self, uid, name):
        lock = lifecycle_lock(self.store_root)
        try:
            self.runtime.authorize(uid); self.runtime.stopped(); integration_status(self.state_dir)
            directory = self._directory(uid, create=True)
            shares.remove(directory, uid, name)
        finally: os.close(lock)
        return self.get(uid)


class SessionMounts:
    """Called synchronously by the stock service's GLib start/mount/stop hooks."""
    def __init__(self, store_root, runtime, state_dir, original_mnt, publish=None):
        self.store_root, self.runtime, self.state_dir = Path(store_root), runtime, Path(state_dir)
        self.original_mnt, self.publish = original_mnt, publish or (lambda: None)
        self.lock = self.baseline = None
        self.owner_thread = threading.get_native_id()
        self.namespace = None; self.stage = None; self.owner = None; self.session = None
        self.mounted = {}; self.saved = None
        self.container_pid = None; self.fuse_complete = False; self.fuse_attempts = 0

    @property
    def active(self): return self.lock is not None

    def check_thread(self):
        require(threading.get_native_id() == self.owner_thread, 'Default sharing mount lifecycle must run on its owning service thread')
        if self.namespace is not None:
            require(os.readlink('/proc/self/ns/mnt') == self.namespace, 'Default sharing mount namespace changed')

    def begin(self, session):
        self.check_thread(); require(not self.active, 'Default sharing session is already active')
        self.lock = lifecycle_lock(self.store_root)
        try:
            self.baseline = os.open('/proc/self/ns/mnt', os.O_RDONLY)
            os.unshare(os.CLONE_NEWNS)
            subprocess.run(['mount', '--make-rprivate', '/'], check=True, capture_output=True)
            self.namespace = os.readlink('/proc/self/ns/mnt')
            require(self.namespace != self.original_mnt, 'Default shares cannot use the host mount namespace')
            self.session = dict(session)
            self.owner = int(session['user_id'])
            require(self.owner > 0 and str(self.owner) == session['user_id'], 'Invalid Default desktop owner')
            self.saved = self.runtime.saved()
            self.stage = Path(tempfile.mkdtemp(prefix='shares-', dir=self.state_dir))
            self.stage.chmod(0o700)
            self.publish()
        except BaseException:
            self.close(True); raise

    def prepare(self, config_path):
        self.check_thread(); require(self.active, 'Default sharing session is not active')
        try: directory = grant_directory(self.store_root, self.owner)
        except FileNotFoundError: return
        entries = shares.list_shares(directory, self.owner)['shares']
        if not entries: return
        require(read_json(directory / 'data.json') == binding(self.saved), 'Default sharing data grant no longer matches management')
        data_fd = data_descriptor(self.saved, self.owner, self.session)
        try:
            storage.assert_no_child_mounts(self.saved['data_path'])
            stage_data = self.stage / 'data'; stage_data.mkdir()
            subprocess.run(['mount', '--bind', f'/proc/{os.getpid()}/fd/{data_fd}', str(stage_data)], check=True, capture_output=True)
        finally: os.close(data_fd)
        grants = self.stage / 'grants'; grants.mkdir()
        self.mounted = shares.apply_preboot(directory, grants, self.owner, lambda _: None, original_mnt=self.original_mnt)
        data_fd = os.open(stage_data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for item in entries:
                if not self.mounted[item['name']]['mounted']: continue
                try:
                    target = walk_target(data_fd, item['name'], create=True)
                    try:
                        source = grants / 'media/0/AnvilDroid' / item['name']
                        # FD target stays pinned if the desktop user renames or
                        # replaces any guest-visible target path concurrently.
                        subprocess.run(['mount', '--bind', str(source), f'/proc/{os.getpid()}/fd/{target}'], check=True, capture_output=True)
                    finally: os.close(target)
                except Exception as error:
                    self.mounted[item['name']] = {'mounted': False, 'error': str(error)[:512]}
        finally: os.close(data_fd)
        self.rewrite_config(config_path, stage_data)

    def rewrite_config(self, path, stage_data):
        path = Path(path)
        # Session config is generated by stock Waydroid before mount_rootfs.
        # Never install caller-selected paths or edit the persistent base config.
        for parent in (path.parent, *path.parent.parents):
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, TRUSTED_UID) and
                    (not info.st_mode & 0o022 or parent == Path('/tmp') and info.st_mode & stat.S_ISVTX),
                    'Default sharing needs trusted root-owned LXC configuration directories')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == TRUSTED_UID and not info.st_mode & 0o022 and
                    info.st_nlink == 1 and info.st_size <= 1024 * 1024, 'Unsafe Default session LXC configuration')
            lines = stream.read().splitlines()
        found = []
        for index, line in enumerate(lines):
            fields = line.split()
            if len(fields) >= 8 and fields[:2] == ['lxc.mount.entry', '='] and fields[-5:-3] == ['data', 'none']:
                found.append(index)
        require(len(found) == 1, 'Default sharing requires exactly one generated userdata mount')
        require(not any(char.isspace() for char in str(stage_data)), 'Invalid private Default staging path')
        lines[found[0]] = f'lxc.mount.entry = {stage_data} data none rbind 0 0'
        fd, temporary = tempfile.mkstemp(prefix='.shares-config-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write('\n'.join(lines) + '\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def started(self, container_pid):
        self.check_thread()
        require(self.active, 'Default sharing session is not active')
        if self.mounted:
            data = os.open(f'/proc/{container_pid}/root/data', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(data)
                require({'device': info.st_dev, 'inode': info.st_ino} == self.saved['data_identity'],
                        'Default Android mounted unexpected userdata')
                directory = grant_directory(self.store_root, self.owner)
                for item in shares.list_shares(directory, self.owner)['shares']:
                    if not self.mounted.get(item['name'], {}).get('mounted'): continue
                    target = walk_target(data, item['name'])
                    try:
                        info = os.fstat(target); flags = os.fstatvfs(target).f_flag
                        require((info.st_dev, info.st_ino) == (item['device'], item['inode']) and
                                bool(flags & os.ST_RDONLY) == item['read_only'] and
                                all(flags & flag for flag in (os.ST_NOSUID, os.ST_NODEV, os.ST_NOEXEC)),
                                'Default Android did not inherit the verified shared-folder mount')
                    finally: os.close(target)
            finally: os.close(data)
        self.container_pid = container_pid
        self.fuse_complete = not bool(self.mounted); self.fuse_attempts = 0
        self.fuse_deadline = time.monotonic() + 300
        for item in self.mounted.values():
            if item.get('mounted'): item.update(mounted=False, raw_mounted=True, error='Waiting for Android FUSE sharing readiness')
        value = {'pid': os.getpid(), 'start_time': process_start(os.getpid()), 'mount_namespace': self.namespace,
                 'owner_uid': self.owner, 'session_pid': self.session.get('pid'),
                 'data_identity': self.saved.get('data_identity'), 'container_pid': container_pid,
                 'container_start_time': process_start(container_pid), 'shares': self.mounted,
                 'store_root': str(self.store_root), 'original_mnt': self.original_mnt}
        self.session_record = value
        storage.atomic(self.state_dir / 'shares-session.json', value)
        self.publish()

    def reconcile(self):
        """GLib watch: bounded retries once vold has created its backend."""
        self.check_thread()
        if not self.active or self.container_pid is None or self.fuse_complete: return
        if time.monotonic() < self.fuse_deadline:
            root = backend = media = None
            try:
                require(process_start(self.container_pid) == self.session_record['container_start_time'], 'Default Android sharing process changed')
                root = os.open(f'/proc/{self.container_pid}/root', os.O_RDONLY | os.O_DIRECTORY)
                media = shares._walk(root, ('data', 'media'))
                backend = shares._walk(root, ('mnt', 'pass_through', '0', 'emulated'))
                first, second = os.fstat(media), os.fstat(backend)
                require((first.st_dev, first.st_ino) == (second.st_dev, second.st_ino), 'Default Android FUSE backend is not ready')
                with open(f'/proc/{self.container_pid}/mountinfo') as stream:
                    require(any(line.split()[4] == '/mnt/pass_through/0/emulated' for line in stream), 'Default Android FUSE backend is not mounted')
                require(process_start(self.container_pid) == self.session_record['container_start_time'], 'Default Android sharing process changed')
            except (OSError, RuntimeError): return
            finally:
                for fd in (root, backend, media):
                    if fd is not None: os.close(fd)
            self.fuse_attempts += 1
            try:
                result = linux.run_bounded(sys.executable, __file__, '--reconcile', self.state_dir,
                    timeout=3, full_output=True, output_limit=16384, operation='Default Android FUSE sharing')
                self.mounted = json.loads(result)
                self.fuse_complete = all(item.get('mounted') for item in self.mounted.values())
            except (OSError, RuntimeError, ValueError) as error:
                self.mounted = {name: {'mounted': False, 'error': 'Default Android FUSE sharing unavailable: ' + str(error)[:400]}
                                for name in self.mounted}
            if self.fuse_attempts >= 3: self.fuse_complete = True
        else:
            self.mounted = {name: {'mounted': False, 'error': 'Android FUSE sharing backend did not become ready within 300 seconds'} for name in self.mounted}
            self.fuse_complete = True
        self.session_record['shares'] = self.mounted
        storage.atomic(self.state_dir / 'shares-session.json', self.session_record)

    def namespace_holders(self):
        """Do not discard a session still referenced by a helper or thread."""
        holders = []
        if self.namespace is None: return holders
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal() or int(path.name) == os.getpid(): continue
            try:
                if os.readlink(path / 'ns/mnt') == self.namespace: holders.append('pid:' + path.name)
            except (FileNotFoundError, ProcessLookupError): pass
            except PermissionError as error:
                raise RuntimeError('Cannot inspect mount namespace for pid ' + path.name +
                                   '; retaining shared-folder namespace and configuration lock') from error
        for path in Path('/proc/self/task').iterdir():
            if int(path.name) == self.owner_thread: continue
            try:
                if os.readlink(path / 'ns/mnt') == self.namespace: holders.append('thread:' + path.name)
            except (FileNotFoundError, ProcessLookupError): pass
        return holders

    def close(self, stopped, wait=True):
        self.check_thread()
        if not self.active: return
        require(stopped, 'Default Android is still active; retaining sharing namespace and configuration lock')
        deadline = time.monotonic() + 3
        while self.namespace_holders():
            if not wait or time.monotonic() >= deadline:
                raise NamespaceBusy('Default helpers still retain the sharing namespace; retaining configuration lock')
            time.sleep(0.05)
        # No lazy pathname unmount: guest/desktop may rename targets. Returning
        # to the baseline discards this session's mount namespace wholesale.
        if self.baseline is not None:
            os.setns(self.baseline, os.CLONE_NEWNS)
            os.close(self.baseline); self.baseline = None
        self.namespace = None
        if self.stage:
            # Staging mountpoints have vanished in the baseline. Remove only
            # empty directories; never recursively delete user data.
            for path in sorted(self.stage.rglob('*'), key=lambda item: len(item.parts), reverse=True):
                try: path.rmdir()
                except (FileNotFoundError, OSError): pass
            self.stage.rmdir(); self.stage = None
        (self.state_dir / 'shares-session.json').unlink(missing_ok=True)
        os.close(self.lock); self.lock = None
        self.owner = self.session = self.saved = None; self.mounted = {}
        self.container_pid = None; self.fuse_complete = False
        self.publish()


def reconcile_session(state_dir):
    """Exec-child authentication for Default's root-private live session."""
    state_dir = Path(state_dir)
    private_directory(state_dir)
    status = read_json(state_dir / 'shares-session.json')
    marker = integration_status(state_dir)
    require(all(status.get(key) == marker.get(key) for key in ('pid', 'start_time', 'mount_namespace')), 'Default sharing service identity changed')
    owner = status['owner_uid']; require(type(owner) is int and owner > 0, 'Invalid Default sharing owner')
    store = Path(status['store_root'])
    private_directory(store)
    directory = grant_directory(store, owner)
    entries = shares._config(directory)
    require(all(item['owner_uid'] == owner for item in entries), 'Default sharing owner changed')
    binding_record = read_json(directory / 'data.json')
    require(binding_record.get('data_identity') == status['data_identity'] and binding_record.get('owner_uid') == owner, 'Default sharing userdata binding changed')
    proc = os.open('/proc', os.O_RDONLY | os.O_DIRECTORY)
    state = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    def read_state(name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=state)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077, 'Unsafe Default sharing state')
            return json.load(stream)
    def start(pid):
        fd = os.open(str(pid) + '/stat', os.O_RDONLY, dir_fd=proc)
        with os.fdopen(fd) as stream: return stream.read().rsplit(')', 1)[1].split()[19]
    def identity():
        require(read_state('shares-session.json') == status and read_state('integration.json') == marker, 'Default sharing session changed')
        require(start(status['pid']) == status['start_time'] and start(status['container_pid']) == status['container_start_time'], 'Default sharing process changed')
        require(os.readlink(str(status['pid']) + '/ns/mnt', dir_fd=proc) == status['mount_namespace'], 'Default sharing namespace changed')
    try:
        expected = types.SimpleNamespace(st_dev=status['data_identity']['device'], st_ino=status['data_identity']['inode'])
        return shares.reconcile_verified(entries, expected, status['container_pid'], status['container_start_time'], status['original_mnt'], identity, proc, timeout=0)
    finally: os.close(proc); os.close(state)


if __name__ == '__main__':
    require(len(sys.argv) == 3 and sys.argv[1] == '--reconcile', 'Invalid Default sharing reconciliation invocation')
    print(json.dumps(reconcile_session(sys.argv[2])))
