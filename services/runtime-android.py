"""Root controller adapter for persistent isolated headless workers."""
import base64
import re
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid


import importlib.util
spec = importlib.util.spec_from_file_location('runtime_resources', Path(__file__).with_name('runtime-resources.py'))
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)

spec = importlib.util.spec_from_file_location('runtime_transfer', Path(__file__).with_name('runtime-transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)

spec = importlib.util.spec_from_file_location('runtime_provision', Path(__file__).with_name('runtime-provision.py'))
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)

ASSETS = Path('/usr/local/lib/anvildroid-controller')

spec = importlib.util.spec_from_file_location('runtime_linux', Path(__file__).with_name('runtime-linux.py'))
linux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linux)


def attach_app_icons(instance, identifier, apps):
    """Read only this instance's Android icon cache, without following Android links."""
    result = [dict(app) for app in apps]
    remaining = 2 * 1024 * 1024
    for app in result:
        # Fresh PackageManager resources take priority over Waydroid's stale cache.
        encoded = app.pop('icon_data', None)
        if isinstance(encoded, str) and 0 < len(encoded) <= min(48000, remaining):
            try:
                png = base64.b64decode(encoded, validate=True)
                if (len(png) >= 33 and png[:16] == b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                        and struct.unpack('>II', png[16:24]) == (96, 96)):
                    app['icon_data'] = encoded
                    remaining -= len(encoded)
                    continue
            except ValueError:
                pass
    directory = None
    try:
        directory = os.open(instance, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in ('android', identifier, 'data', 'icons'):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        for app in result:
            if 'icon_data' in app: continue
            package = app.get('package')
            if app.get('launchable') is not True or not isinstance(package, str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', package):
                continue
            try:
                fd = os.open(package + '.png', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 33 <= info.st_size <= 128 * 1024:
                        continue
                    data = stream.read(128 * 1024 + 1)
                if len(data) > 128 * 1024 or data[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
                    continue
                width, height = struct.unpack('>II', data[16:24])
                if not (0 < width <= 1024 and 0 < height <= 1024):
                    continue
                encoded = base64.b64encode(data).decode('ascii')
                if len(encoded) > remaining:
                    continue
                remaining -= len(encoded)
                app['icon_data'] = encoded
            except (OSError, ValueError, struct.error):
                continue  # Missing/bad icons must not hide an otherwise usable app.
    except OSError:
        pass
    finally:
        if directory is not None: os.close(directory)
    return result


class WorkerUnavailable(RuntimeError):
    pass


def abandoned_namespace_gone(instance, identifier, generation):
    """Prove cleanup after worker death; never detach mounts or kill host processes."""
    try:
        scope = resources.scope_path(identifier, generation)
        if scope.exists():
            events = dict(line.split() for line in (scope / 'cgroup.events').read_text().splitlines())
            if events.get('populated') != '0': return False
        mount_prefix = str(instance / 'android').replace(' ', r'\040') + '/'
        for process in Path('/proc').iterdir():
            if not process.name.isdecimal(): continue
            try:
                if mount_prefix in (process / 'mountinfo').read_text(): return False
                if resources.unit(identifier, generation) in (process / 'cgroup').read_text(): return False
            except (FileNotFoundError, ProcessLookupError): continue
        return True
    except (OSError, RuntimeError, ValueError, TypeError):
        return False


class AndroidBackend:
    def __init__(self, images):
        self.images = images
        self.children = []

    def instance(self, identifier):
        path = self.images.location(identifier)
        self.images.check_directory(path)
        if len(os.fsencode(path / 'worker.sock')) >= 108:
            raise RuntimeError('Controller store path is too long for worker socket')
        return path

    def quiescent(self, identifier):
        instance = self.instance(identifier)
        lock = os.open(instance / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return False
            return True
        finally:
            os.close(lock)

    def status(self, identifier, operation='status', payload=None, apk_fd=None):
        instance = self.instance(identifier)
        identity_path = instance / 'worker-identity.json'
        identity = json.loads(identity_path.read_text()) if identity_path.exists() else None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(330 if operation == 'install' else (45 if operation == 'app_action' else 3))
                connection.connect(str(instance / 'worker.sock'))
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != 0: raise RuntimeError('Untrusted runtime worker')
                if apk_fd is None:
                    connection.sendall(json.dumps(payload or {'op': operation}).encode() + b'\n')
                else:
                    transfer.send(connection, payload, apk_fd)
                data = bytearray()
                while not data.endswith(b'\n'):
                    part = connection.recv(4096)
                    if not part or len(data) > (1024 * 1024 if operation in ('apps','keymaps') else 8192): raise RuntimeError('Invalid worker response')
                    data.extend(part)
                report = json.loads(data)
        except (FileNotFoundError, ConnectionRefusedError):
            # Never infer that a missing endpoint permits a second worker.
            lock = os.open(instance / 'worker.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise WorkerUnavailable('Worker exists but endpoint is unavailable; retry reconciliation')
                result = instance / 'worker-result.json'
                report = json.loads(result.read_text()) if result.exists() else None
            finally:
                os.close(lock)
            if report is None:
                return {'state': 'Error' if identity else 'Stopped', 'runtime_id': identifier,
                        'error': 'Worker exited without cleanup acknowledgement' if identity else None}
        if not identity or report.get('runtime_id') != identifier or report.get('generation') != identity.get('generation'):
            raise RuntimeError('Runtime worker identity mismatch')
        if report.get('state') not in ('Starting', 'Running', 'Stopping', 'Stopped', 'Error'):
            raise RuntimeError('Invalid worker state')
        return report

    def keymaps(self, identifier):
        status=self.status(identifier)
        if status['state']!='Running' or 'keymap_editor' not in status.get('capabilities',[]):
            raise RuntimeError('Start an updated desktop runtime to read keymaps')
        report=self.status(identifier,'keymaps')
        if report.get('app_error'): raise RuntimeError(report['app_error'])
        if report.get('generation')!=status['generation'] or report['state']!='Running': raise RuntimeError('Runtime changed during keymap read')
        result=report.get('keymaps')
        if not isinstance(result,dict) or result.get('runtime_id')!=identifier: raise RuntimeError('Invalid keymap response')
        return result

    def app_states(self, identifier):
        status = self.status(identifier)
        if status['state'] != 'Running':
            return {'runtime_id': identifier, 'state': status['state'], 'states': None, 'checked_at': time.time()}
        if 'app_states' not in status.get('capabilities', []):
            raise RuntimeError('Stop and start this runtime to enable per-app process status')
        report = self.status(identifier, 'app_states')
        if report.get('generation') != status.get('generation') or report['state'] != 'Running':
            raise RuntimeError('Runtime changed during app status sampling')
        states = report.get('app_states')
        if states is not None and (not isinstance(states, dict) or len(states) > 10000 or
            any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', k) or
                v not in ('foreground', 'background') for k, v in states.items())):
            raise RuntimeError('Invalid app status snapshot')
        return {'runtime_id': identifier, 'state': 'Running', 'generation': report['generation'],
                'states': states, 'checked_at': time.time()}

    def apps(self, identifier):
        status = self.status(identifier)
        if status['state'] != 'Running':
            raise RuntimeError('Start this runtime before listing its packages')
        if 'apps' not in status.get('capabilities', []):
            raise RuntimeError('This worker predates app routing. Stop and start this runtime when convenient to load the updated worker.')
        report = self.status(identifier, 'apps')
        if report.get('app_error'): raise RuntimeError(report['app_error'])
        if report.get('generation') != status.get('generation') or report['state'] != 'Running':
            raise RuntimeError('Runtime changed while listing packages; refresh and retry')
        if not isinstance(report.get('apps'), list): raise RuntimeError('Worker did not return a package list')
        return {'runtime_id': identifier, 'generation': report['generation'], 'apps': attach_app_icons(self.instance(identifier), identifier, report['apps'])}

    def google_services(self, identifier):
        status = self.status(identifier)
        if status['state'] != 'Running': raise RuntimeError('Start this runtime before checking Google services')
        if 'google_services' not in status.get('capabilities', []):
            raise RuntimeError('Stop and start this runtime once to load Google services detection, then retry.')
        report = self.status(identifier, 'google_services')
        if report.get('app_error'): raise RuntimeError(report['app_error'])
        if report.get('generation') != status.get('generation') or report['state'] != 'Running':
            raise RuntimeError('Runtime changed during Google services check; refresh and retry')
        result = report.get('google_services')
        # Older running workers return the package map directly.
        packages = result.get('packages', result) if isinstance(result, dict) else result
        android_id = result.get('gsf_android_id') if isinstance(result, dict) else None
        expected = {'com.android.vending', 'com.google.android.gms', 'com.google.android.gsf'}
        if not isinstance(packages, dict) or set(packages) != expected or any(type(value) is not bool for value in packages.values()):
            raise RuntimeError('Invalid Google services result')
        if android_id is not None and (not isinstance(android_id, str) or not re.fullmatch(r'[0-9]{1,20}', android_id)):
            raise RuntimeError('Invalid GSF Android ID')
        id_error = str(result.get('gsf_id_error') or '')[:512]
        # Workers stay alive across package updates. Read their persistent data
        # here too, so older workers need not restart just to expose this ID.
        if android_id is None and packages['com.google.android.gsf']:
            try:
                android_id = linux.read_gsf_id(self.instance(identifier) / 'android' / identifier / 'data')
                id_error = '' if android_id else 'GSF has not created an Android ID. Open Play Store, then check again.'
            except RuntimeError as error:
                id_error = str(error)
            current = self.status(identifier)
            if current['state'] != 'Running' or current.get('generation') != report.get('generation'):
                raise RuntimeError('Runtime changed during Google services check; refresh and retry')
        return {'runtime_id': identifier, 'generation': report['generation'], 'packages': packages,
                'gsf_android_id': android_id,
                'gsf_id_error': id_error,
                'checked_at': int(time.time())}

    def install(self, identifier, fd):
        status = self.status(identifier)
        if status['state'] != 'Running': raise RuntimeError('Start this runtime before installing an APK')
        if 'install_apk' not in status.get('capabilities', []):
            raise RuntimeError('Stop and start this runtime once to load APK installation support, then retry.')
        report = self.status(identifier, 'install', {'op': 'install', 'generation': status['generation']}, apk_fd=fd)
        if report.get('app_error'): raise RuntimeError(report['app_error'])
        if report.get('generation') != status['generation'] or report['state'] != 'Running':
            raise RuntimeError('Worker changed during installation; outcome unknown. Check before retrying.')
        result = report.get('app_result')
        if not isinstance(result, dict) or result.get('action') != 'install' or result.get('success') is not True:
            raise RuntimeError('Invalid installation result; outcome unknown')
        return result

    def app_action(self, identifier, action, package):
        status = self.status(identifier)
        if status['state'] != 'Running': raise RuntimeError('Start this runtime before using app actions')
        if 'app_actions' not in status.get('capabilities', []):
            raise RuntimeError('Stop and start this runtime when convenient to load the updated app-action worker.')
        if action=='keymap_edit' and 'keymap_editor' not in status.get('capabilities',[]):
            raise RuntimeError('Update and restart this desktop runtime to enable overlay editing')
        if action == 'full_ui' and 'full_ui' not in status.get('capabilities', []):
            raise RuntimeError('Update the controller and restart this runtime in Desktop mode to open Waydroid.')
        if action == 'uninstall' and 'uninstall_app' not in status.get('capabilities', []):
            raise RuntimeError('Stop and start this runtime once to enable Uninstall, then retry.')
        report = self.status(identifier, 'app_action', {'op': 'app_action', 'generation': status['generation'], 'action': action, 'package': package})
        if report.get('app_error'): raise RuntimeError(report['app_error'])
        if report.get('generation') != status['generation'] or report['state'] != 'Running':
            raise RuntimeError('Worker changed during action; outcome unknown. Check before retrying.')
        result = report.get('app_result')
        if not isinstance(result, dict) or result.get('action') != action or result.get('package') != package:
            raise RuntimeError('Invalid app action response; outcome unknown')
        return result

    def snapshot_support(self, identifier):
        instance = self.instance(identifier)
        manifest = self.images.inspect(identifier)
        if not manifest: raise RuntimeError('Prepare runtime images first')
        support = instance / 'support'
        if support.exists():
            self.images.check_directory(support)
            if json.loads((support / 'images.json').read_text()) != manifest['images']:
                raise RuntimeError('Pinned Android support/image mismatch')
            return
        # A host patch snapshot may be paired only with the exact installed images.
        for name, expected in manifest['images'].items():
            import hashlib
            digest = hashlib.sha256()
            with (Path('/var/lib/waydroid/images') / name).open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
            if digest.hexdigest() != expected:
                raise RuntimeError('Images differ from the installed patch source; compatibility not verified')
        staging = Path(tempfile.mkdtemp(prefix='.support-', dir=instance))
        mounts = []
        try:
            shutil.copytree('/var/lib/waydroid/overlay', staging / 'overlay', symlinks=True)
            # The host overlay only carries the desktop bridge and patched init
            # on machines that ran the native host installer; rebuild them from
            # the installed assets so Desktop mode works from a stock Waydroid
            # install as well (the caption is skipped without Android build tools).
            system_mount = staging / 'system'; system_mount.mkdir()
            subprocess.run(['mount', '-o', 'loop,ro,noload', instance / 'prepared/system.img', str(system_mount)], check=True, capture_output=True)
            mounts.append(system_mount)
            provision.build_overlay(staging / 'overlay', system_mount, require_caption=False)
            for name, source in [('waydroid.prop', '/var/lib/waydroid/waydroid.prop'),
                                 ('config', '/var/lib/waydroid/lxc/waydroid/config'),
                                 ('waydroid.seccomp', '/var/lib/waydroid/lxc/waydroid/waydroid.seccomp')]:
                shutil.copyfile(source, staging / name)
            (staging / 'images.json').write_text(json.dumps(manifest['images']))
            for directory, _, files in os.walk(staging):
                for name in files:
                    file = Path(directory) / name
                    if not file.is_symlink():
                        with file.open('rb') as stream: os.fsync(stream.fileno())
                self.images.sync_directory(Path(directory))
            os.rename(staging, support)
            self.images.sync_directory(instance)
        finally:
            for mount in reversed(mounts):
                subprocess.run(['umount', str(mount)], capture_output=True)
            if staging.exists(): shutil.rmtree(staging)

    @staticmethod
    def check_desktop_bridge():
        path = Path('/usr/local/lib/anvildroid-controller/libanvildroid-window.so')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Install the root-owned desktop bridge with the runtime controller')
        ime = path.with_name('AnvilDroidIme.apk')
        info = ime.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Install the root-owned desktop IME companion')
        return path

    def pin_desktop_bridge(self, instance):
        source = self.check_desktop_bridge()
        fd, temporary = tempfile.mkstemp(prefix='.desktop-bridge-', dir=instance)
        try:
            with os.fdopen(fd, 'wb') as stream, source.open('rb') as original:
                shutil.copyfileobj(original, stream)
                os.fchmod(stream.fileno(), 0o644)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, instance / 'desktop-bridge.so')
            shutil.copyfile(source.with_name('AnvilDroidIme.apk'), instance / 'desktop-ime.apk')
            os.chmod(instance / 'desktop-ime.apk', 0o600)
            with (instance / 'desktop-ime.apk').open('rb') as ime: os.fsync(ime.fileno())
            tasks=source.parent/'provision/anvildroid-tasks.sh'
            info=tasks.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                raise RuntimeError('Unsafe desktop task companion')
            shutil.copyfile(tasks,instance/'desktop-tasks.sh')
            os.chmod(instance/'desktop-tasks.sh',0o644)  # Android shell UID must read the bind-mounted script.
            with (instance/'desktop-tasks.sh').open('rb') as companion: os.fsync(companion.fileno())
            self.images.sync_directory(instance)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def start(self, identifier, generation, desktop=None, internet=True):
        instance = self.instance(identifier)
        previous = self.status(identifier)
        if previous['state'] == 'Running': return previous
        if previous['state'] in ('Starting', 'Stopping'):
            raise RuntimeError('Worker transition already in progress')
        self.snapshot_support(identifier)
        helper = Path('/usr/local/lib/anvildroid-controller/anvildroid-shutdown.jar')
        if helper.exists():
            info = helper.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or not 0 < info.st_size <= 4 * 1024**2:
                raise RuntimeError('Unsafe Android shutdown helper')
            fd, temporary = tempfile.mkstemp(prefix='.shutdown-', dir=instance)
            try:
                with os.fdopen(fd, 'wb') as output, helper.open('rb') as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, instance / 'shutdown.jar')
                self.images.sync_directory(instance)
            finally: Path(temporary).unlink(missing_ok=True)
        if desktop: self.pin_desktop_bridge(instance)
        worker = Path(__file__).with_name('runtime-worker.py')
        self.children = [child for child in self.children if child.poll() is None]
        host_net_fd = os.open('/proc/self/ns/net', os.O_RDONLY) if internet else None
        try:
            with (instance / 'worker.log').open('ab') as log:
                child = subprocess.Popen(resources.prefix(identifier, generation, resources.load(instance)) + ['unshare', '--mount', '--net', '--pid', '--fork', '--mount-proc',
                sys.executable, str(worker), '--instance', str(instance), '--id', identifier,
                '--generation', generation, '--namespaces', os.readlink('/proc/self/ns/mnt'), os.readlink('/proc/self/ns/net')] + (['--desktop-display', str(desktop[0]), '--desktop-uid', str(desktop[1])] if desktop else []) + (['--host-net-fd', str(host_net_fd)] if internet else []),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                pass_fds=(host_net_fd,) if internet else ())
        finally:
            if host_net_fd is not None: os.close(host_net_fd)
        self.children.append(child)
        deadline = time.monotonic() + 210
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError('Android worker exited; inspect root-owned worker.log')
            try:
                state = self.status(identifier)
                if state.get('generation') == generation:
                    if state['state'] == 'Running': return state
                    if state['state'] == 'Error': raise RuntimeError(state.get('error') or 'Worker boot failed')
            except (ConnectionResetError, socket.timeout, WorkerUnavailable): pass
            time.sleep(.25)
        # A timed-out start must not leave a hidden live Android behind.
        self.stop(identifier)
        raise RuntimeError('Android worker startup timed out')

    def stop(self, identifier):
        report = self.status(identifier, 'stop')
        if report['state'] == 'Stopped' and self.quiescent(identifier): return report
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            report = self.status(identifier)
            if report['state'] == 'Stopped' and self.quiescent(identifier): return report
            if report['state'] == 'Error':
                if not self.quiescent(identifier):
                    time.sleep(.25)
                    continue
                recovered = not report.get('cleanup_complete') and abandoned_namespace_gone(
                    self.instance(identifier), identifier, report.get('generation'))
                if report.get('cleanup_complete') or recovered:
                    if recovered:
                        report = dict(report, cleanup_complete=True, shutdown_forced=True,
                                      shutdown_warning='Previous shutdown failed; worker, runtime scope and mounts have now exited. Recent unsaved app changes may be lost.')
                    report = dict(report, state='Stopped', error=None)
                    instance = self.instance(identifier)
                    fd, temporary = tempfile.mkstemp(prefix='.worker-result-', dir=instance)
                    try:
                        with os.fdopen(fd, 'w') as stream:
                            json.dump(report, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, instance / 'worker-result.json')
                        self.images.sync_directory(instance)
                    finally: Path(temporary).unlink(missing_ok=True)
                    return report
                raise RuntimeError(report.get('error') or 'Worker cleanup failed')
            time.sleep(.25)
        raise RuntimeError('Worker stop timed out; reconciliation required')
