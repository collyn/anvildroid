"""Management adapter for the system Waydroid installation (runtime default).

The system D-Bus service supplies the session identity. Caller-supplied paths,
UIDs and shell commands are never accepted by this adapter.
"""
import configparser
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


resources = module('runtime-resources')
gpu = module('runtime-gpu')
linux = module('runtime-linux')
storage = module('runtime-storage')
GOOGLE_PACKAGES = ('com.android.vending', 'com.google.android.gms', 'com.google.android.gsf')
RESOURCE_KEYS = ('lxc.cgroup2.memory.max', 'lxc.cgroup2.memory.swap.max', 'lxc.cgroup2.cpu.max')
ARM_KEYS = ('ro.product.cpu.abilist', 'ro.product.cpu.abilist32', 'ro.product.cpu.abilist64',
            'ro.dalvik.vm.native.bridge', 'ro.dalvik.vm.isa.arm', 'ro.dalvik.vm.isa.arm64',
            'ro.enable.native.bridge.exec', 'ro.vendor.enable.native.bridge.exec', 'ro.vendor.enable.native.bridge.exec64')


def session_info():
    try:
        import dbus
    except ImportError as error:
        raise RuntimeError('Waydroid management requires python3-dbus; run host setup first') from error
    try:
        bus = dbus.SystemBus()
        service = dbus.Interface(bus.get_object('id.waydro.Container', '/ContainerManager', introspect=False),
                                 'id.waydro.ContainerManager')
        return {str(key): str(value) for key, value in service.GetSession(timeout=2).items()}
    except dbus.DBusException as error:
        raise RuntimeError('Cannot verify the Waydroid system session; start its container service and retry.') from error


def properties(text):
    return {key.strip(): value.strip() for line in text.splitlines()
            if '=' in line and not line.lstrip().startswith('#')
            for key, value in [line.split('=', 1)]}


def replace_properties(text, values):
    lines = [line for line in text.splitlines() if line.split('=', 1)[0].strip() not in values]
    return '\n'.join(lines + [key + '=' + value for key, value in values.items() if value is not None]) + '\n'


class ExistingRuntime:
    def __init__(self, store, work=Path('/var/lib/waydroid')):
        self.store = store
        self.work = work
        self.metadata = store / 'existing-runtime.json'

    def read(self, path):
        # All configuration paths are fixed descendants of the root-owned work dir.
        for parent in (path.parent, *path.parent.parents):
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                if parent == Path('/tmp') and info.st_uid == 0 and info.st_mode & stat.S_ISVTX:
                    continue
                raise RuntimeError('Untrusted Waydroid configuration directory')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_nlink != 1 or info.st_size > 1024**2:
                raise RuntimeError('Untrusted Waydroid configuration file')
            return stream.read()

    def saved(self):
        try:
            value = json.loads(self.read(self.metadata))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict) or type(value.get('owner_uid')) is not int:
            raise RuntimeError('Invalid Existing runtime ownership record')
        return value

    def authorize(self, uid):
        session = session_info()
        saved = self.saved()
        if session:
            if session.get('user_id') != str(uid):
                raise RuntimeError('Existing runtime belongs to another desktop user')
            if saved and saved['owner_uid'] != uid:
                raise RuntimeError('Existing runtime management belongs to another user')
        elif not saved or saved['owner_uid'] != uid:
            raise RuntimeError('Start Existing runtime, then choose Enable management once before stopping it.')
        elif self.state() != 'STOPPED':
            raise RuntimeError('Waydroid container is active without an authenticated session; recover it before managing it')
        return session, saved

    def state(self):
        value = linux.run_bounded('lxc-info', '-P', self.work / 'lxc', '-n', 'waydroid', '-sH',
                                  timeout=1, full_output=True, output_limit=256, operation='Waydroid state')
        if value not in ('RUNNING', 'STOPPED', 'FROZEN'):
            raise RuntimeError('Unknown Waydroid container state')
        return value

    def stopped(self):
        if self.state() != 'STOPPED':
            raise RuntimeError('Stop the Waydroid container before changing this setting; stopping only a window is insufficient.')

    def android(self, *args):
        if self.state() != 'RUNNING':
            raise RuntimeError('Start Existing runtime before querying Android')
        return linux.run_bounded('lxc-attach', '-P', self.work / 'lxc', '-n', 'waydroid', '--', *args,
                                 timeout=2, full_output=True, output_limit=2 * 1024**2,
                                 operation='Existing Android query')

    def config(self):
        cfg = configparser.ConfigParser(interpolation=None)
        try:
            cfg.read_string(self.read(self.work / 'waydroid.cfg'))
        except configparser.Error as error:
            raise RuntimeError('Invalid Waydroid configuration; original file retained') from error
        if not cfg.has_section('waydroid'):
            raise RuntimeError('Waydroid configuration is not initialized')
        return cfg

    def write_transaction(self, changes):
        """Back up originals before publishing; roll back a failed multi-file change."""
        originals = {path: self.read(path) for path in changes}
        self.stopped()
        backup = self.store / ('existing-backup-' + str(time.time_ns()) + '.json')
        storage.atomic(backup, {str(path): text for path, text in originals.items()})
        written = []
        try:
            for path, text in changes.items():
                self.replace(path, text)
                written.append(path)
        except Exception:
            for path in reversed(written):
                self.replace(path, originals[path])
            raise

    @staticmethod
    def replace(path, text):
        mode = stat.S_IMODE(path.stat().st_mode)
        fd, temporary = tempfile.mkstemp(prefix='.anvildroid-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            storage.sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def record(self, session, saved):
        state = self.state()
        display_state = {'STOPPED': 'Stopped', 'RUNNING': 'Running', 'FROZEN': 'Frozen'}[state]
        if state == 'RUNNING':
            try:
                if self.android('/system/bin/getprop', 'sys.boot_completed') != '1': display_state = 'Starting'
            except (OSError, RuntimeError): display_state = 'Starting'
        return {'id': 'default', 'name': saved.get('name', 'Existing runtime'),
                'state': display_state,
                'managed': bool(saved), 'job': None, 'backend': 'waydroid-system',
                'display': {'mode': 'desktop', 'effective_mode': 'desktop' if state == 'RUNNING' else None}}

    def resource_info(self):
        values = properties(self.read(self.work / 'lxc/waydroid/config'))
        memory = values.get(RESOURCE_KEYS[0], 'max')
        cpu = values.get(RESOURCE_KEYS[2], 'max 100000').split()
        try:
            if len(cpu) != 2 or int(cpu[1]) <= 0: raise ValueError('Invalid CPU period')
            configured = {'memory_mib': 0 if memory == 'max' else int(memory) // 1024**2,
                          'cpu_count': 0 if cpu[0] == 'max' else int(cpu[0]) / int(cpu[1])}
            if configured['cpu_count'] == int(configured['cpu_count']):
                configured['cpu_count'] = int(configured['cpu_count'])
            if any(value < 0 for value in configured.values()): raise ValueError('Negative limit')
        except (ValueError, IndexError, ZeroDivisionError) as error:
            raise RuntimeError('Invalid Waydroid LXC limits; original configuration retained') from error
        capacity = resources.host()
        actual, error = None, None
        if self.state() in ('RUNNING', 'FROZEN'):
            try:
                def read(name):
                    return linux.run_bounded('lxc-cgroup', '-P', self.work / 'lxc', '-n', 'waydroid', name,
                                             timeout=.3, full_output=True, output_limit=4096, operation='Waydroid cgroup readback')
                def number(value): return None if value == 'max' else int(value)
                quota, period = read('cpu.max').split()
                stats = properties(read('cpu.stat').replace(' ', '='))
                events = properties(read('memory.events').replace(' ', '='))
                actual = {'memory_max_bytes': number(read('memory.max')), 'memory_current_bytes': int(read('memory.current')),
                          'swap_max_bytes': number(read('memory.swap.max')), 'swap_current_bytes': int(read('memory.swap.current')),
                          'cpu_quota_count': None if quota == 'max' else int(quota) / int(period),
                          'cpu_throttled_usec': int(stats.get('throttled_usec', 0)), 'oom_kills': int(events.get('oom_kill', 0)),
                          'cpu_usage_usec': int(stats.get('usage_usec', 0)),
                          'scope': linux.run_bounded('lxc-info', '-P', self.work / 'lxc', '-n', 'waydroid', '-pH',
                                                     timeout=.3, full_output=True, output_limit=256, operation='Waydroid identity')}
            except (OSError, RuntimeError, ValueError) as exc:
                error = str(exc)
        return {'configured': configured, 'host': capacity, 'effective': actual, 'error': error,
                'presets': resources.presets(capacity), 'scope': 'Android container; system Waydroid helpers are outside these limits.'}

    def gpu_info(self):
        result = gpu.info(self.store)
        result['selection'] = self.config().get('waydroid', 'drm_device', fallback='auto') or 'auto'
        return result

    def arm_info(self):
        props = properties(self.read(self.work / 'waydroid.prop'))
        bridge = props.get('ro.dalvik.vm.native.bridge', '')
        # Existing providers are preserved; never overwrite an unknown ARM stack.
        available = all((self.work / 'overlay/system' / lib / 'libndk_translation.so').is_file() for lib in ('lib', 'lib64'))
        return {'provider': bridge or 'Existing native bridge', 'enabled': bridge not in ('', '0'),
                'supported': available and bridge in ('', '0', 'libndk_translation.so'), 'available': available,
                'experimental': True, 'inherited': False, 'abis': ['armeabi-v7a', 'arm64-v8a'],
                'detail': 'Uses installed ARM libraries. AnvilDroid does not replace an unknown native bridge.'}

    def dispatch(self, request, uid):
        session, saved = self.authorize(uid)
        result = self._dispatch(request, uid, session, saved)
        current = session_info()
        if current.get('user_id') != session.get('user_id') or current.get('pid') != session.get('pid'):
            raise RuntimeError('Waydroid session changed during the operation; refresh before retrying')
        return result

    def _dispatch(self, request, uid, session, saved):
        op = request['op']
        if op == 'existing_claim':
            if not session or session.get('user_id') != str(uid):
                raise RuntimeError('Start Existing runtime under your desktop account before enabling management')
            source = session.get('waydroid_data')
            fd = storage.open_directory(source, uid)
            try:
                info = os.fstat(fd)
                if info.st_uid != uid: raise RuntimeError('Android data directory must belong to the desktop user')
                identity = {'device': info.st_dev, 'inode': info.st_ino}
            finally: os.close(fd)
            storage.atomic(self.metadata, {**saved, 'owner_uid': uid, 'name': saved.get('name', 'Existing runtime'),
                                           'data_path': source, 'data_identity': identity})
            return self.record(session, self.saved())
        if op in ('refresh', 'inspect'):
            return self.record(session, saved)
        if op == 'resources_info': return self.resource_info()
        if op == 'gpu_info': return self.gpu_info()
        if op == 'arm_info': return self.arm_info()
        if op == 'display_info': return self.record(session, saved)['display']
        if op == 'google_services':
            output = self.android('/system/bin/pm', 'list', 'packages', '--user', '0')
            lines = output.splitlines()
            if not lines or any(not re.fullmatch(r'package:[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', line) for line in lines):
                raise RuntimeError('Invalid Android package inventory')
            android_id = None
            id_error = None
            if 'package:com.google.android.gsf' in lines:
                try:
                    value = self.android('/system/bin/sh', '-c',
                        "for db in /data/data/com.google.android.gsf/databases/gservices.db; do [ -f \"$db\" ] && sqlite3 -readonly \"$db\" \"select value from main where name='android_id';\"; done").strip()
                    if re.fullmatch(r'[0-9]{1,20}', value): android_id = value
                except (RuntimeError, OSError, subprocess.TimeoutExpired):
                    pass
                if android_id is None:
                    try:
                        data_root = session.get('waydroid_data') or saved.get('data_path')
                        if not data_root: raise RuntimeError('Android data path is unavailable. Enable management, then check again.')
                        android_id = linux.read_gsf_id(data_root)
                        if android_id is None: id_error = 'GSF has not created an Android ID. Open Play Store, wait, then check again.'
                    except RuntimeError as error:
                        id_error = str(error)
            return {'runtime_id': 'default', 'generation': session.get('pid', 'system'), 'checked_at': time.time(),
                    'packages': {name: 'package:' + name in lines for name in GOOGLE_PACKAGES},
                    'gsf_android_id': android_id, 'gsf_id_error': id_error}
        if op == 'storage_info':
            path = session.get('waydroid_data') or saved.get('data_path')
            if not path or not Path(path).is_absolute(): raise RuntimeError('Android data location is unknown; enable management while running')
            # statvfs reads capacity only, never traverses Android data as root.
            usage = os.statvfs(path)
            return {'path': path, 'total_bytes': usage.f_blocks * usage.f_frsize,
                    'free_bytes': usage.f_bavail * usage.f_frsize,
                    'used_bytes': (usage.f_blocks - usage.f_bfree) * usage.f_frsize,
                    'limit_status': 'unavailable', 'job': None, 'previous_path': None, 'error': None}
        if op == 'health':
            report = {'runtime_id': 'default', 'checked_at': time.time(), 'checks': [], 'gpu_devices': gpu.inventory(),
                      'worker_state': self.state(), 'storage_path': session.get('waydroid_data') or saved.get('data_path')}
            report['checks'].append({'name': 'Waydroid container', 'status': 'ok', 'detail': report['worker_state']})
            if report['worker_state'] == 'RUNNING':
                try:
                    report['graphics'] = gpu.probe_report(self.android('/system/bin/sh', '-c', gpu.PROBE_COMMAND))
                    report['gpu_selection'] = self.gpu_info()['selection']
                    report['gpu_node'] = report['graphics'].get('android_node')
                    report['gpu_verification'] = gpu.verification(report['gpu_selection'], report['gpu_node'], report['graphics'])
                except (OSError, RuntimeError) as error:
                    report['checks'].append({'name': 'Graphics', 'status': 'warning', 'detail': str(error)})
            return report
        if not saved:
            raise RuntimeError('Choose Enable management while Existing runtime is running first')
        if op == 'rename':
            name = request['name']
            if not isinstance(name, str) or not 0 < len(name.strip()) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                raise RuntimeError('Invalid runtime name')
            storage.atomic(self.metadata, {**saved, 'name': name.strip()})
            return self.record(session, self.saved())
        if op == 'resources_set':
            self.stopped()
            if not resources.host()['supported']: raise RuntimeError('Resource limits require cgroup v2')
            limits = resources.validate({key: request[key] for key in ('memory_mib', 'cpu_count')})
            path = self.work / 'lxc/waydroid/config'
            values = dict(zip(RESOURCE_KEYS, (str(limits['memory_mib'] * 1024**2) if limits['memory_mib'] else 'max',
                                             '0' if limits['memory_mib'] else 'max',
                                             f"{limits['cpu_count'] * 100000} 100000" if limits['cpu_count'] else 'max 100000')))
            self.write_transaction({path: replace_properties(self.read(path), values)})
            return self.resource_info()
        if op in ('gpu_set', 'arm_set'):
            self.stopped()
            cfg = self.config()
            changes = {}
            if not cfg.has_section('properties'): cfg.add_section('properties')
            if op == 'gpu_set':
                if request['node'] == 'software':
                    raise RuntimeError('Software compatibility is available only for managed runtimes with a supported image')
                selected = gpu.choose(request['node'])
                node = selected['node']
                if node == 'auto':
                    node = next((d['node'] for d in self.gpu_info()['devices'] if d['selectable']), None)
                    if node is None: raise RuntimeError('No supported GPU is available')
                cfg.set('waydroid', 'drm_device', node)
                values = {'gralloc.gbm.device': node}
                nodes_path = self.work / 'lxc/waydroid/config_nodes'
                nodes = self.read(nodes_path).splitlines()
                nodes = [line for line in nodes if not re.match(r'^\s*lxc\.mount\.entry\s*=\s*/dev/dri/renderD\d+\s', line)]
                nodes.append(f'lxc.mount.entry = {node} {node[1:]} none bind,create=file 0 0')
                changes[nodes_path] = '\n'.join(nodes) + '\n'
            else:
                if type(request['enabled']) is not bool: raise RuntimeError('Expected boolean enabled')
                if not self.arm_info()['supported']: raise RuntimeError('Installed ARM provider is unavailable or unsupported; keep its existing setup')
                if request['enabled']:
                    values = saved.get('arm_properties')
                    if not values:
                        raise RuntimeError('No saved ARM configuration; enable the installed provider through its installer first')
                else:
                    current = properties(self.read(self.work / 'waydroid.prop'))
                    if current.get('ro.dalvik.vm.native.bridge') != 'libndk_translation.so':
                        raise RuntimeError('ARM translation is already disabled')
                    storage.atomic(self.metadata, {**saved, 'arm_properties': {key: current.get(key) for key in ARM_KEYS}})
                    values = {key: None for key in ARM_KEYS}
                    values.update({'ro.product.cpu.abilist': 'x86_64,x86', 'ro.product.cpu.abilist32': 'x86',
                                   'ro.product.cpu.abilist64': 'x86_64', 'ro.dalvik.vm.native.bridge': '0'})
            for key, value in values.items():
                if value is None: cfg.remove_option('properties', key)
                else: cfg.set('properties', key, value)
            stream = io.StringIO(); cfg.write(stream)
            changes[self.work / 'waydroid.cfg'] = stream.getvalue()
            for name in ('waydroid.prop', 'waydroid_base.prop'):
                path = self.work / name
                changes[path] = replace_properties(self.read(path), values)
            self.write_transaction(changes)
            return self.gpu_info() if op == 'gpu_set' else self.arm_info()
        raise RuntimeError('This operation requires an isolated runtime; Existing runtime remains owned by system Waydroid')

    def import_source(self, uid):
        _, saved = self.authorize(uid)
        self.stopped()
        if not saved.get('data_identity'):
            raise RuntimeError('Enable management while running once to verify the source data directory')
        fd = storage.open_directory(saved['data_path'], uid)
        try:
            info = os.fstat(fd)
            if info.st_uid != uid or saved['data_identity'] != {'device': info.st_dev, 'inode': info.st_ino}:
                raise RuntimeError('Android data directory changed since management was enabled')
            storage.assert_no_child_mounts(saved['data_path'])
            return fd
        except Exception:
            os.close(fd)
            raise


def data_manifest(fd, sync=False):
    """Walk pinned directories and open regular files without following Android links."""
    result = {}
    def walk(directory, prefix):
        for name in sorted(os.listdir(directory)):
            key = prefix + name
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            entry = [stat.S_IFMT(metadata.st_mode), stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid]
            if stat.S_ISLNK(metadata.st_mode):
                entry.append(os.readlink(name, dir_fd=directory))
            elif stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK |
                                (os.O_DIRECTORY if stat.S_ISDIR(metadata.st_mode) else 0), dir_fd=directory)
                try:
                    actual = os.fstat(child)
                    if (actual.st_dev, actual.st_ino, actual.st_mode) != (metadata.st_dev, metadata.st_ino, metadata.st_mode):
                        raise RuntimeError('Android data changed during import')
                    entry.append(sorted((attr, os.getxattr(child, attr).hex()) for attr in os.listxattr(child)))
                    if stat.S_ISDIR(actual.st_mode): walk(child, key + '/')
                    else:
                        digest = hashlib.sha256()
                        while True:
                            block = os.read(child, 1024**2)
                            if not block: break
                            digest.update(block)
                        entry.extend((actual.st_size, digest.hexdigest()))
                    if sync: os.fsync(child)
                finally: os.close(child)
            else:
                entry.append(metadata.st_rdev)
            result[key] = entry
    walk(fd, '')
    return result


def copy_data(source_fd, destination, stopped):
    stopped()
    before = data_manifest(source_fd)
    required = sum(entry[-2] for entry in before.values() if entry[0] == stat.S_IFREG) + 64 * 1024**2
    if shutil.disk_usage(destination.parent).free < required:
        raise RuntimeError('Not enough free space to import Android data; original data is unchanged')
    destination.mkdir(mode=0o700)
    subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always', '--',
                    f'/proc/self/fd/{source_fd}/.', str(destination)], pass_fds=(source_fd,),
                   check=True, capture_output=True, timeout=3600)
    target = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        stopped()
        if before != data_manifest(source_fd) or before != data_manifest(target, sync=True):
            raise RuntimeError('Android data changed or copy verification failed; original data is unchanged')
        os.fsync(target)
    finally: os.close(target)


def prepare_imported_profiles(data, identifier):
    # The staging tree is under a 0700 root-owned instance and not yet bootable.
    base = data
    for part in ('waydroid_tmp', 'anvildroid'):
        base = base / part
        if not base.exists(): return
        if base.is_symlink() or not base.is_dir(): raise RuntimeError('Unsafe imported companion directory')
    for item in base.iterdir():
        if item.name in ('keymaps', 'window-profiles'): continue
        if item.is_dir() and not item.is_symlink(): shutil.rmtree(item)
        else: item.unlink()
    for kind in ('keymaps', 'window-profiles'):
        directory = base / kind
        if not directory.exists(): continue
        if directory.is_symlink() or not directory.is_dir(): raise RuntimeError('Unsafe imported profile directory')
        for profile in directory.iterdir():
            info = profile.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096:
                raise RuntimeError('Unsafe imported profile')
            text = profile.read_text()
            if kind == 'keymaps':
                text = re.sub(r'\AANVIL_KEYMAP 1 (?:default|existing) ', 'ANVIL_KEYMAP 1 ' + identifier + ' ', text)
            else:
                text = re.sub(r'\A\{"version":1,"runtime":"(?:default|existing)"',
                              '{"version":1,"runtime":"' + identifier + '"', text)
            with profile.open('w') as stream:
                stream.write(text); stream.flush(); os.fsync(stream.fileno())
