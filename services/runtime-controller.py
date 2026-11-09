#!/usr/bin/python3
"""P5.3 local runtime control service. Prepares isolated image copies asynchronously; supports persistent desktop Android workers.
Run as root with --store ROOT_OWNED_DIR --socket ROOT_OWNED_DIR/control.sock.
Clients are authorized by kernel SO_PEERCRED, not by request-supplied user IDs.
"""
import argparse
import contextlib
import platform
import copy
import errno
import hashlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
import threading
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import stat
import struct
import tempfile
import uuid
import time
import re
import select
import subprocess
import sys

spec = importlib.util.spec_from_file_location('runtime_desktop', Path(__file__).with_name('runtime-desktop.py'))
desktop_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop_module)

spec = importlib.util.spec_from_file_location('runtime_transfer', Path(__file__).with_name('runtime-transfer.py'))
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)

spec = importlib.util.spec_from_file_location('runtime_images', Path(__file__).with_name('runtime-images.py'))
image_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_module)
spec = importlib.util.spec_from_file_location('runtime_android', Path(__file__).with_name('runtime-android.py'))
android_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android_module)
spec = importlib.util.spec_from_file_location('runtime_storage', Path(__file__).with_name('runtime-storage.py'))
storage_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage_module)
spec = importlib.util.spec_from_file_location('runtime_worker', Path(__file__).with_name('runtime-worker.py'))
worker_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker_module)
spec = importlib.util.spec_from_file_location('runtime_adb', Path(__file__).with_name('runtime-adb.py'))
adb_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adb_module)
MAX_REQUEST = 65536
spec = importlib.util.spec_from_file_location('runtime_catalog', Path(__file__).with_name('runtime-catalog.py'))
catalog_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog_module)
spec = importlib.util.spec_from_file_location('runtime_download', Path(__file__).with_name('runtime-download.py'))
download_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download_module)
spec = importlib.util.spec_from_file_location('runtime_extract', Path(__file__).with_name('runtime-extract.py'))
extract_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract_module)


spec_host = importlib.util.spec_from_file_location('runtime_host', Path(__file__).with_name('runtime-host.py'))
host_module = importlib.util.module_from_spec(spec_host)
spec_host.loader.exec_module(host_module)

spec_existing = importlib.util.spec_from_file_location('runtime_existing', Path(__file__).with_name('runtime-existing.py'))
existing_module = importlib.util.module_from_spec(spec_existing)
spec_existing.loader.exec_module(existing_module)

spec_features = importlib.util.spec_from_file_location('runtime_features', Path(__file__).with_name('runtime-features.py'))
features_module = importlib.util.module_from_spec(spec_features)
spec_features.loader.exec_module(features_module)

class RequestError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def require(condition, code, message):
    if not condition:
        raise RequestError(code, message)


def secure_directory(path, private):
    require(path.is_absolute(), 'CONFIG', 'Absolute service paths required')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
            not info.st_mode & (0o077 if private else 0o022),
            'CONFIG', 'Service directory must be root-owned with restricted permissions')
    require(path.resolve() == path, 'CONFIG', 'Symlink service paths are not allowed')
    for parent in path.parents:
        metadata = parent.stat()
        # Root-owned sticky /tmp is safe for a root-owned child directory.
        require(metadata.st_uid == 0 and (not metadata.st_mode & 0o022 or metadata.st_mode & stat.S_ISVTX),
                'CONFIG', 'Service path has an untrusted ancestor')


def read_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077
                and info.st_nlink == 1 and info.st_size <= 4 * 1024 * 1024,
                'CONFIG', 'Unsafe runtime metadata file')
        return json.load(stream)


class Store:
    def __init__(self, root, image_source=Path("/var/lib/waydroid/images"), allow_temporary_storage=False):
        self.mutex = threading.RLock()
        # Signalled on every registry commit; `watch` clients wait on it.
        self.changed = threading.Condition(threading.Lock())
        self.catalog = catalog_module.Catalog()
        self.active_jobs = set()
        # Shared, non-runtime resources reserved while slow I/O runs unlocked.
        self.reservations = set()
        # One RPC at a time per worker, without blocking other runtimes.
        self.rpc_locks = {}
        self.adb_relays = {}
        secure_directory(root, True)
        self.root = root
        self.existing = existing_module.ExistingRuntime(root)
        self.path = root / 'runtimes.json'
        self.lock = os.open(root / 'controller.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.lock)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077 and info.st_nlink == 1,
                'CONFIG', 'Unsafe service lock')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.downloads = download_module.Downloads(root)
        self.arm_sources_root = root / 'arm-sources'
        if not self.arm_sources_root.exists():
            self.arm_sources_root.mkdir(mode=0o700)
            os.chmod(self.arm_sources_root, 0o700)
        secure_directory(self.arm_sources_root, True)
        try:
            self.data = read_file(self.path)
        except FileNotFoundError:
            self.data = {'schema_version': 3, 'runtimes': []}
        self.validate(self.data)
        changed = False
        for runtime in self.data['runtimes']:
            if 'start_on_boot' not in runtime:
                runtime['start_on_boot'] = False
                changed = True
        if changed:
            self.save({'schema_version': 3, 'runtimes': self.data['runtimes']})
        if self.data['schema_version'] in (1, 2):
            self.validate(self.data)
            backup = root / ('runtimes.v' + str(self.data['schema_version']) + '.' + uuid.uuid4().hex + '.bak')
            with backup.open('xb') as stream:
                os.chmod(backup, 0o600)
                stream.write(self.path.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            image_module.ImageBackend.sync_directory(root)
            self.save({'schema_version': 3, 'runtimes': [dict(r, job=r.get('job')) for r in self.data['runtimes']]})
        self.validate(self.data)
        self.backend = image_module.ImageBackend(root, image_source)
        self.storage = storage_module.Storage(root, allow_temporary_storage)
        self.android = android_module.AndroidBackend(self.backend)
        self.selections_path = root / 'image-selections.json'
        self.selections = read_file(self.selections_path) if self.selections_path.exists() else {}
        require(isinstance(self.selections, dict), 'CONFIG', 'Invalid image selections')
        self.selections = {key: value for key, value in self.selections.items() if any(r['id'] == key for r in self.data['runtimes'])}
        for value in self.selections.values():
            require(isinstance(value, dict) and value.get('flavor') in ('VANILLA', 'GAPPS', 'CUSTOM') and set(value.get('images', {})) == {'system', 'vendor'}, 'CONFIG', 'Invalid image selection')
        self.prepare_phases = {}
        self.imports_path = root / 'existing-imports.json'
        self.imports = read_file(self.imports_path) if self.imports_path.exists() else {}
        require(isinstance(self.imports, dict), 'CONFIG', 'Invalid Existing import journal')
        self.imports = {key: value for key, value in self.imports.items() if any(r['id'] == key for r in self.data['runtimes'])}
        require(all(value in ('pending', 'complete') for value in self.imports.values()), 'CONFIG', 'Invalid Existing import phase')
        self.display_path = root / 'display.json'
        self.displays = read_file(self.display_path) if self.display_path.exists() else {}
        require(isinstance(self.displays, dict) and all(any(r['id'] == key for r in self.data['runtimes']) and value in ('headless', 'desktop') for key, value in self.displays.items()), 'CONFIG', 'Invalid display configuration')
        # Upgrade saved background runtimes without changing their Android data.
        if any(mode == 'headless' for mode in self.displays.values()):
            self.displays = {key: 'desktop' for key in self.displays}
            storage_module.atomic(self.display_path, self.displays)
        self.app_jobs_path = root / 'app-jobs.json'
        self.app_jobs = read_file(self.app_jobs_path) if self.app_jobs_path.exists() else {}
        require(isinstance(self.app_jobs, dict), 'CONFIG', 'Invalid app job metadata')
        for identifier, job in self.app_jobs.items():
            require(any(r['id'] == identifier for r in self.data['runtimes']) and isinstance(job, dict) and
                    job.get('status') in ('Running', 'Succeeded', 'Failed'), 'CONFIG', 'Invalid app job')
            if job['status'] == 'Running':
                job.update(status='Failed', error='Controller interrupted; action outcome is unknown. Check the app before retrying.')
        if self.app_jobs: storage_module.atomic(self.app_jobs_path, self.app_jobs)

        self.deletions_path = root / 'deletions.json'
        self.deletions = read_file(self.deletions_path) if self.deletions_path.exists() else {}
        require(isinstance(self.deletions, dict), 'CONFIG', 'Invalid deletion journal')
        self.deletions = {identifier: targets for identifier, targets in self.deletions.items() if any(r['id'] == identifier for r in self.data['runtimes'])}
        for identifier, targets in self.deletions.items():
            require(re.fullmatch(r'r-[0-9a-f]{32}', identifier) and isinstance(targets, list), 'CONFIG', 'Invalid deletion target')
            for target in targets:
                path = Path(target.get('path', '')) if isinstance(target, dict) else Path('.')
                require(path == self.backend.location(identifier) or (path.parent == self.storage.retired and re.fullmatch(re.escape(identifier) + r'-[0-9a-f]{32}', path.name)) or re.fullmatch(r'\.anvildroid-' + re.escape(identifier) + r'-[0-9a-f]{8}', path.name), 'CONFIG', 'Deletion location is not runtime-owned')
                require(not target.get('detach') or path == self.backend.location(identifier), 'CONFIG', 'Invalid deletion mount anchor')
                require(isinstance(target, dict) and isinstance(target.get('path'), str) and Path(target['path']).is_absolute()
                        and '..' not in Path(target['path']).parts and all(type(target.get(key)) is int for key in ('device', 'inode', 'fsid')), 'CONFIG', 'Invalid deletion location')

        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='runtime-prepare')
        # Reconcile actual published images or an authenticated worker, never saved PIDs.
        for record in list(self.data['runtimes']):
            if record['id'] in self.deletions: continue
            if record['state'] == 'Provisioning':
                try:
                    prepared = self.backend.inspect(record['id'])
                    if self.imports.get(record['id']) == 'pending' or (self.backend.location(record['id']) / 'reinstall.json').exists(): prepared = None
                    if record['id'] in self.selections:
                        support_pin = self.backend.location(record['id']) / 'support/images.json'
                        if not prepared or not support_pin.exists() or json.loads(support_pin.read_text()) != prepared['images']: prepared = None
                        if prepared and self.selections[record['id']].get('arm_default'):
                            instance = self.backend.location(record['id'])
                            if worker_module.arm.read_config(instance) is None: prepared = None
                            else: worker_module.arm.active_layer(instance)
                    self.finish(record['id'], record['job']['id'], None if prepared else 'Preparation interrupted; retry prepare')
                except Exception as error:
                    self.finish(record['id'], record['job']['id'], str(error))
            elif record['state'] in ('Starting', 'Running', 'Frozen', 'Stopping'):
                self.reconcile(record)

    def login_start(self, uid):
        """Start the caller's start-on-login runtimes in the caller's desktop.

        Requested by the user's session at login. The controller is socket
        activated, so it cannot do this when it happens to start: that would
        also restart runtimes the user stopped earlier in the same session."""
        started, failed = [], []
        records = [r for r in self.data['runtimes'] if r['owner_uid'] == uid and r.get('start_on_boot')
                   and r['state'] in ('Prepared', 'Stopped') and r['id'] not in self.active_jobs]
        if not records:
            return {'started': started, 'failed': failed}
        session = desktop_module.capabilities(uid)
        require(session['desktop'], 'NO_DESKTOP', 'No desktop session is available yet')
        display = (desktop_module.X11_TOKEN_PREFIX + session['display']
                   if session['session'] == 'x11' else session['socket'])
        for record in records:
            try:
                self._dispatch({'op': 'start', 'id': record['id'], 'display': display}, uid)
                started.append(record['id'])
            except (RequestError, OSError, RuntimeError) as error:
                failed.append({'id': record['id'], 'error': str(error)})
        return {'started': started, 'failed': failed}

    def idle(self):
        """True when nothing in this process needs the controller to stay up."""
        with self.mutex:
            if self.active_jobs or self.reservations or self.adb_relays or self.storage.busy is not None:
                return False
            if any(r['state'] in ('Provisioning', 'Starting', 'Running', 'Frozen', 'Stopping') for r in self.data['runtimes']):
                return False
        thread = self.downloads.thread
        return thread is None or not thread.is_alive()

    @staticmethod
    def validate(data):
        require(isinstance(data, dict) and set(data) == {'schema_version', 'runtimes'} and
                type(data['schema_version']) is int and data['schema_version'] in (1, 2, 3) and isinstance(data['runtimes'], list) and len(data['runtimes']) <= 1024,
                'CONFIG', 'Unsupported or invalid controller registry; file retained')
        ids = set()
        for record in data['runtimes']:
            require(isinstance(record, dict) and set(record) <= ({'id', 'owner_uid', 'name', 'state', 'start_on_boot'} | ({'job'} if data['schema_version'] >= 2 else set())) and {'id', 'owner_uid', 'name', 'state'} <= set(record),
                    'CONFIG', 'Invalid runtime record')
            if 'start_on_boot' in record:
                require(type(record['start_on_boot']) is bool, 'CONFIG', 'Invalid start-on-boot setting')
            identifier = record['id']
            require(isinstance(identifier, str) and len(identifier) == 34 and identifier.startswith('r-')
                    and all(c in '0123456789abcdef' for c in identifier[2:]) and identifier not in ids,
                    'CONFIG', 'Invalid or duplicate runtime ID')
            ids.add(identifier)
            require(type(record['owner_uid']) is int and record['owner_uid'] >= 0 and
                    record['state'] in ('Allocated', 'Provisioning', 'Prepared', 'Starting', 'Running', 'Frozen', 'Stopping', 'Stopped', 'Error'), 'CONFIG', 'Invalid runtime owner/state')
            if data['schema_version'] == 1:
                require(record['state'] == 'Allocated', 'CONFIG', 'Invalid legacy state')
            else:
                if data['schema_version'] == 2:
                    require(record['state'] in ('Allocated', 'Provisioning', 'Prepared', 'Error'), 'CONFIG', 'Invalid schema-2 state')
                job = record['job']
                if record['state'] == 'Allocated':
                    require(job is None, 'CONFIG', 'Allocated runtime cannot have a job')
                else:
                    require(isinstance(job, dict) and set(job) == {'id', 'operation', 'status', 'error'}
                            and isinstance(job['id'], str) and len(job['id']) == 32
                            and all(c in '0123456789abcdef' for c in job['id'])
                            and job['operation'] in (('prepare',) if data['schema_version'] < 3 else ('prepare', 'start', 'stop'))
                            and job['status'] == {'Provisioning': 'Running', 'Prepared': 'Succeeded', 'Starting': 'Running', 'Running': 'Succeeded', 'Frozen': 'Succeeded', 'Stopping': 'Running', 'Stopped': 'Succeeded', 'Error': 'Failed'}[record['state']]
                            and (job['error'] is None if record['state'] != 'Error' else isinstance(job['error'], str)),
                            'CONFIG', 'Invalid runtime operation')
            validate_name(record['name'])

    def save(self, data):
        self.validate(data)
        fd, temp = tempfile.mkstemp(prefix='.registry-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            self.data = data  # rename committed; retain coherence even if directory fsync fails
            with self.changed: self.changed.notify_all()
            directory = os.open(self.root, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.data = data

    def close(self):
        for uid, identifier in list(self.adb_relays):
            try: self.adb_request({'op':'adb_set', 'id':identifier, 'enabled':False}, uid)
            except (OSError, RuntimeError, RequestError):
                relay = self.adb_relays.pop((uid, identifier), None)
                if relay: relay.close()
        self.downloads.close()
        self.pool.shutdown(wait=True)
        os.close(self.lock)

    def replace(self, updated):
        self.save({'schema_version': 3, 'runtimes': [updated if r['id'] == updated['id'] else r for r in self.data['runtimes']]})

    def finish(self, identifier, job_id, error, success_state='Prepared'):
        with self.mutex:
            record = next(r for r in self.data['runtimes'] if r['id'] == identifier)
            if record['job']['id'] != job_id:
                return
            self.replace(dict(record, state=success_state if error is None else 'Error',
                job=dict(record['job'], status='Succeeded' if error is None else 'Failed', error=error[:512] if error else None)))

    def prepare_job(self, identifier, job_id):
        try:
            instance = self.backend.location(identifier)
            reset = instance / 'reinstall.json'
            if reset.exists():
                if platform.machine() == 'x86_64':
                    self.prepare_phases[identifier] = 'Checking ARM translation package…'
                    worker_module.arm.ensure_default_archive()
                self.storage.ensure(identifier)
                require(self.android.quiescent(identifier), 'BUSY', 'Android worker is still active')
                storage_module.assert_no_child_mounts(instance)
                selection = json.loads(reset.read_text())['selection']
                self.prepare_phases[identifier] = 'Removing previous Android images and data...'
                # Preserve the mount anchor/inode used by relocated storage.
                for child in instance.iterdir():
                    if child.name in ('reinstall.json', 'worker.lock'): continue
                    if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
                    else: child.unlink()
                with self.mutex:
                    if selection is None: self.selections.pop(identifier, None)
                    else: self.selections[identifier] = selection
                    storage_module.atomic(self.selections_path, self.selections)
                    self.imports.pop(identifier, None)
                    storage_module.atomic(self.imports_path, self.imports)
                    self.app_jobs.pop(identifier, None)
                    storage_module.atomic(self.app_jobs_path, self.app_jobs)
                reset.unlink()
                storage_module.sync_directory(instance)
            selection = self.selections.get(identifier)
            if selection:
                instance = self.backend.location(identifier)
                instance.mkdir(mode=0o700, exist_ok=True)
                self.backend.check_directory(instance)
                self.prepare_phases[identifier] = 'Verifying and extracting downloaded images…'
                if not self.backend.inspect(identifier):
                    for abandoned in instance.glob('.extract-*'):
                        self.backend.check_directory(abandoned)
                        shutil.rmtree(abandoned)
                    extract_module.extract_pair(self.downloads.root, selection['images'], instance / 'prepared', identifier)
                self.prepare_phases[identifier] = 'Building compatible Android support and caption…'
                result = subprocess.run(['unshare', '--mount', '--propagation', 'private', '--fork', '--kill-child', sys.executable,
                    str(Path(__file__).with_name('runtime-provision.py')), str(instance), selection['flavor']],
                    capture_output=True, text=True, timeout=300)
                if result.returncode: raise RuntimeError('Image compatibility preparation failed: ' + result.stderr[-450:])
            else:
                self.backend.prepare(identifier)
                if self.imports.get(identifier) == 'pending':
                    self.import_existing_data(identifier)
            # New/reinstalled instances receive defaults. Explicit saved choices
            # remain intact when Prepare is retried on an existing runtime.
            if not (instance / 'gpu.json').exists():
                self.prepare_phases[identifier] = 'Selecting compatible GPU…'
                device = worker_module.gpu.automatic_device()
                storage_module.atomic(instance / 'gpu.json', device or {'node':'auto'})
            if platform.machine() == 'x86_64' and (selection is None or selection.get('arm_default')) and worker_module.arm.read_config(instance) is None:
                self.prepare_phases[identifier] = 'Installing ARM translation…'
                archive = worker_module.arm.ensure_default_archive()
                manifest = self.backend.inspect(identifier)
                worker_module.arm.configure(instance, True, manifest['images'], storage_module.atomic, archive=archive)

        except Exception as error:
            self.finish(identifier, job_id, str(error) or 'Preparation failed')
        else:
            self.finish(identifier, job_id, None)
        finally:
            with self.mutex:
                self.active_jobs.discard(identifier)
                self.prepare_phases.pop(identifier, None)

    def import_existing_data(self, identifier):
        record = next(r for r in self.data['runtimes'] if r['id'] == identifier)
        uid = record['owner_uid']
        source_fd = self.existing.import_source(uid)
        instance = self.backend.location(identifier)
        staging = instance / 'import-data'
        destination = instance / 'android' / identifier / 'data'
        def stopped():
            self.existing.authorize(uid)
            self.existing.stopped()
        try:
            self.prepare_phases[identifier] = 'Copying and verifying existing Android apps and data…'
            if staging.exists():
                require(not staging.is_symlink(), 'CONFIG', 'Unsafe import staging directory')
                storage_module.assert_no_child_mounts(staging)
                shutil.rmtree(staging)
            existing_module.copy_data(source_fd, staging, stopped)
            stopped()
            # This is the same support snapshot used by installed-image runtimes.
            self.android.snapshot_support(identifier)
            limits = self.existing.resource_info()['configured']
            worker_module.resources.validate(limits)
            storage_module.atomic(instance / 'resources.json', limits)
            node = self.existing.gpu_info()['selection']
            storage_module.atomic(instance / 'gpu.json', worker_module.gpu.choose(node))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # A crash may have published data before committing the journal.
                fd = storage_module.open_directory(destination)
                try:
                    require(existing_module.data_manifest(fd) == existing_module.data_manifest(source_fd),
                            'CONFIG', 'An interrupted import already published different data; delete this copy and import again')
                finally: os.close(fd)
                shutil.rmtree(staging)
            else:
                existing_module.prepare_imported_profiles(staging, identifier)
                os.rename(staging, destination)
            image_module.ImageBackend.sync_directory(destination.parent)
            stopped()
            self.imports[identifier] = 'complete'
            storage_module.atomic(self.imports_path, self.imports)
        finally:
            os.close(source_fd)

    def reinstall_existing(self, request, uid):
        require(request.get('confirmed_name') == self.existing.saved().get('name', 'System Waydroid'),
                'INVALID_REQUEST', 'Runtime name changed; review reinstall again')
        flavor = request.get('flavor')
        require(flavor in ('installed', 'VANILLA', 'GAPPS', 'CUSTOM'), 'INVALID_REQUEST', 'Invalid image flavor')
        image_directory = None
        staging = None
        try:
            if flavor != 'installed':
                cached = self.downloads.status(uid)
                require(cached.get('status') == 'Ready' and cached.get('flavor') == flavor,
                        'IMAGE_NOT_READY', 'Download and verify the selected image pair first; refresh if the cache changed.')
                require(self.downloads.state.get('owner_uid') == uid,
                        'FORBIDDEN', 'Import or download the selected image as this user first')
                for kind in ('system', 'vendor'):
                    require(cached.get('images', {}).get(kind, {}).get('sha256') == request.get(kind + '_sha256'),
                            'IMAGE_NOT_READY', 'The selected image cache changed; refresh and choose it again.')
                staging = Path(tempfile.mkdtemp(prefix='.existing-reinstall-', dir=self.root))
                extract_module.extract_pair(self.downloads.root, cached['images'], staging / 'images', 'r-' + '0' * 32)
                image_directory = staging / 'images'
            return self.existing.reinstall(uid, flavor, image_directory)
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    def reconcile(self, record):
        if record['id'] in self.active_jobs: return dict(record)
        try:
            self.storage.ensure(record['id'])
            report = self.android.status(record['id'])
            state = report['state']
            if record['job']['operation'] == 'start' and state == 'Stopped':
                raise RuntimeError('Start interrupted; worker is stopped')
            if record['job']['operation'] == 'stop' and state in ('Starting', 'Running', 'Frozen'):
                raise RuntimeError('Stop interrupted; worker is still active, retry stop')
            self.replace(dict(record, state=state, job=dict(record['job'],
                status='Running' if state in ('Starting', 'Stopping') else ('Failed' if state == 'Error' else 'Succeeded'),
                error=report.get('error') if state == 'Error' else None)))
        except Exception as error:
            self.finish(record['id'], record['job']['id'], str(error) or 'Worker unavailable')
        return next(r for r in self.data['runtimes'] if r['id'] == record['id'])

    def android_job(self, identifier, job_id, operation, desktop=None):
        try:
            if operation == 'start':
                if desktop and desktop[0].startswith(desktop_module.X11_TOKEN_PREFIX):
                    instance = self.backend.location(identifier)
                    backend = worker_module.native_x11.resolve_backend(worker_module.native_x11.selection(instance), True)
                    if backend['backend'] == 'native-x11':
                        # Older pinned reports need a read-only image scan.
                        # Run it as part of this runtime's asynchronous Start,
                        # without blocking the UI or operations on other runtimes.
                        self.android.preflight_x11(identifier,
                            worker_module.gpu.is_nvidia(worker_module.gpu.resolve(instance)))
                if desktop: self.android.start(identifier, job_id, desktop=desktop)
                else: self.android.start(identifier, job_id)
            else: self.android.stop(identifier)
        except Exception as error:
            self.finish(identifier, job_id, str(error) or 'Android operation failed')
        else:
            self.finish(identifier, job_id, None, 'Running' if operation == 'start' else 'Stopped')
        finally:
            with self.mutex: self.active_jobs.discard(identifier)

    def move_storage_job(self, identifier, destination_fd):
        def progress(phase):
            self.storage.entries[identifier]['job']['phase'] = phase
            self.storage.save()
        try:
            self.storage.move(identifier, destination_fd, progress)
        finally:
            with self.mutex: self.active_jobs.discard(identifier)

    def cleanup_storage_job(self, identifier):
        try:
            self.storage.discard_previous(identifier)
        except Exception as error:
            self.storage.entries[identifier]['job'].update(status='Failed', error=str(error))
            self.storage.save()
        finally:
            if self.storage.busy == identifier: self.storage.busy = None
            with self.mutex: self.active_jobs.discard(identifier)

    def delete_job(self, identifier, job):
        try:
            entry = self.storage.entries.get(identifier, {})
            anchor = self.backend.location(identifier)
            if entry.get('active') and storage_module.is_mountpoint(anchor):
                storage_module.delete_location(dict(entry['active'], path=str(anchor), detach=True))
            while self.deletions[identifier]:
                target = self.deletions[identifier][0]
                storage_module.delete_location(target)
                with self.mutex:
                    self.deletions[identifier].pop(0)
                    storage_module.atomic(self.deletions_path, self.deletions)
            anchor = self.backend.location(identifier)
            if anchor.exists():
                require(not storage_module.is_mountpoint(anchor), 'BUSY', 'Storage anchor is still mounted')
                anchor.rmdir()  # Empty anchor only; never recursively remove an unverified directory.
            with self.mutex:
                self.storage.entries.pop(identifier, None)
                self.storage.unavailable.pop(identifier, None)
                self.storage.save()
                self.displays.pop(identifier, None)
                storage_module.atomic(self.display_path, self.displays)
                self.app_jobs.pop(identifier, None)
                storage_module.atomic(self.app_jobs_path, self.app_jobs)
                self.selections.pop(identifier, None)
                storage_module.atomic(self.selections_path, self.selections)
                self.imports.pop(identifier, None)
                storage_module.atomic(self.imports_path, self.imports)
                self.save({'schema_version': 3, 'runtimes': [r for r in self.data['runtimes'] if r['id'] != identifier]})
                self.deletions.pop(identifier)
                storage_module.atomic(self.deletions_path, self.deletions)
        except Exception as error:
            with self.mutex:
                if any(r['id'] == identifier for r in self.data['runtimes']):
                    self.app_jobs[identifier] = dict(job, status='Failed', error=str(error)[-1024:])
                    storage_module.atomic(self.app_jobs_path, self.app_jobs)
        finally:
            with self.mutex:
                self.active_jobs.discard(identifier)
                if self.storage.busy == identifier: self.storage.busy = None

    def install_job(self, identifier, job, fd):
        try:
            result = self.android.install(identifier, fd)
            finished = dict(job, status='Succeeded', result=result)
        except Exception as error:
            finished = dict(job, status='Failed', error=str(error)[-1024:])
        finally:
            os.close(fd)
        with self.mutex:
            try:
                self.app_jobs[identifier] = finished
                storage_module.atomic(self.app_jobs_path, self.app_jobs)
            finally: self.active_jobs.discard(identifier)

    def app_action_job(self, identifier, job, activation_token=None):
        try:
            result = self.android.app_action(identifier, job['action'], job['package'], activation_token=activation_token) if activation_token else self.android.app_action(identifier, job['action'], job['package'])
            finished = dict(job, status='Succeeded', result=result)
        except Exception as error:
            finished = dict(job, status='Failed', error=str(error)[-1024:])
        with self.mutex:
            try:
                self.app_jobs[identifier] = finished
                storage_module.atomic(self.app_jobs_path, self.app_jobs)
            finally: self.active_jobs.discard(identifier)

    def adb_request(self, request, uid):
        try:
            return self._adb_request(request, uid)
        except (RuntimeError, OSError, ValueError, RequestError):
            if request['op'] == 'adb_set' and request['enabled']:
                # A missing IP or failed bind must not strand a debug-enabled
                # guest without a controllable host endpoint.
                try: self._adb_request({'op':'adb_set', 'id':request['id'], 'enabled':False}, uid)
                except (RuntimeError, OSError, ValueError, RequestError): pass
            raise

    def _adb_request(self, request, uid):
        identifier = request['id']
        key = (uid, identifier)
        relay = self.adb_relays.get(key)
        if request['op'] == 'adb_set':
            enabled = request['enabled']
            if enabled and relay and relay.report()['enabled']:
                return dict(relay.report(), runtime_id=identifier)
            if relay:
                relay.close()
                del self.adb_relays[key]
            if identifier == 'default':
                if self.existing.state() == 'FROZEN':
                    worker_module.linux.run_bounded('gdbus', 'call', '--system', '--dest', 'id.waydro.Container',
                        '--object-path', '/ContainerManager', '--method', 'id.waydro.ContainerManager.Unfreeze',
                        timeout=3, operation='Resume System Waydroid for ADB')
                if self.existing.state() == 'RUNNING':
                    adb_module.configure(self.existing.android, enabled)
                elif enabled:
                    raise RuntimeError('Start System Waydroid before enabling ADB')
                lease = self.root / 'adb-default.lease'
                if not enabled: lease.unlink(missing_ok=True)
                if enabled:
                    address = adb_module.guest_address(worker_module.linux.run_bounded('lxc-info', '-P',
                        self.existing.work / 'lxc', '-n', 'waydroid', '-iH', timeout=2, full_output=True,
                        operation='Default ADB address'))
                    def init_pid():
                        return worker_module.linux.run_bounded('lxc-info', '-P', self.existing.work / 'lxc',
                            '-n', 'waydroid', '-pH', timeout=2, full_output=True, operation='Default ADB identity').strip()
                    generation = init_pid()
                    lease.touch(mode=0o600)
                    def alive():
                        valid = self.existing.state() == 'RUNNING' and init_pid() == generation
                        if valid: os.utime(lease, None)
                        else: lease.unlink(missing_ok=True)
                        return valid
                    relay = adb_module.Relay(lambda: adb_module.tcp_target(address), alive=alive)
            else:
                status = self.android.status(identifier)
                if status['state'] not in ('Running', 'Frozen'):
                    if enabled: raise RuntimeError('Start the runtime before enabling ADB')
                else:
                    if 'adb' not in status.get('capabilities', []):
                        raise RuntimeError('Restart this runtime once to enable ADB after updating')
                    reply = self.android.status(identifier, 'adb_set',
                        {'op': 'adb_set', 'enabled': enabled, 'generation': status['generation']})
                    if reply.get('app_error'): raise RuntimeError(reply['app_error'])
                    if enabled:
                        instance = self.android.instance(identifier)
                        generation = status['generation']
                        def alive():
                            try:
                                valid = (instance / 'adb.sock').exists() and read_file(instance / 'worker-identity.json')['generation'] == generation
                                if valid: os.utime(instance / 'adb.lease', None)
                                return valid
                            except (OSError, ValueError, KeyError, RequestError): return False
                        relay = adb_module.Relay(lambda: adb_module.unix_target(instance / 'adb.sock'), alive=alive)
            if enabled: self.adb_relays[key] = relay
            else: relay = None
        if relay and not relay.report()['enabled']:
            relay.close()
            self.adb_relays.pop(key, None)
            relay = None
        return dict(relay.report() if relay else {'enabled': False, 'endpoint': None, 'connections': 0, 'error': ''}, runtime_id=identifier)

    def requirements(self, uid=None):
        images = self.backend.source_status()
        installed_images = images['available']
        # Managed runtimes selected from the image library do not depend on the
        # host Waydroid image directory. Treat a ready custom/downloaded pair as
        # an available preparation source for this user.
        if uid is not None:
            cached = self.downloads.status(uid).get('available_images', [])
            if cached and not images['available']:
                images = {'available': True,
                          'bytes': sum(item.get('image_bytes', 0) for item in cached),
                          'detail': 'Managed image library available'}
        waydroid = shutil.which('waydroid') is not None
        missing = host_module.missing_commands()
        support = android_module.provision.managed_support_available()
        desktop_info = desktop_module.capabilities(uid)
        checks = [
            {'id': 'waydroid', 'label': 'Waydroid', 'available': waydroid,
             'detail': 'Installed' if waydroid else 'Install Waydroid for your Linux distribution first.'},
            {'id': 'images', 'label': 'Android images', **images},
            {'id': 'tools', 'label': 'Runtime tools', 'available': not missing,
             'detail': 'Available' if not missing else 'Missing: ' + ', '.join(missing)},
            {'id': 'arm_payload', 'label': 'ARM app support package',
             'available': worker_module.arm.ARCHIVE.is_file(), 'optional': True,
             'detail': 'Downloaded automatically during Prepare or setup.'},
            {'id': 'support', 'label': 'Waydroid runtime configuration', 'available': support,
             'detail': 'Configuration available' if support else 'Install or repair the Waydroid package to provide its LXC templates.'},
            {'id': 'desktop', 'label': 'Desktop session', 'available': desktop_info['desktop'], 'detail': desktop_info['detail']}]
        watches = host_module.inotify_check()
        checks.append(watches)
        return {'checks': checks, 'can_prepare': waydroid and images['available'],
                'can_start': waydroid and not missing and support and desktop_info['desktop'] and watches['available'],
                'image_bytes': images['bytes'], 'installed_images': installed_images, 'desktop': desktop_info,
                'system_runtime': self.existing.initialized()}

    def resource_report(self, record):
        resource = worker_module.resources
        result = resource.info(self.backend.location(record['id']), record['id'], record['state'] in ('Running', 'Frozen'))
        others = []
        for other in self.data['runtimes']:
            if other['id'] == record['id'] or other['owner_uid'] != record['owner_uid'] or other['state'] not in ('Running', 'Frozen', 'Starting', 'Stopping'):
                continue
            try:
                self.storage.ensure(other['id'])
                others.append(resource.info(self.backend.location(other['id']), other['id'], other['state'] in ('Running', 'Frozen')))
            except (OSError, RuntimeError, ValueError): others.append(None)
        result['presets'] = resource.presets(result['host'])
        result['budget'] = resource.budget(result['host'], others, (result.get('effective') or {}).get('memory_current_bytes', 0))
        result['admission'] = resource.admission(result['configured'], result['budget'], result['host'])
        return result

    def health(self, record):
        identifier = record['id']
        report = {'runtime_id': identifier, 'checked_at': int(time.time()),
                  'saved_state': record['state'], 'worker_state': None,
                  'storage_path': self.storage.report(identifier)['path'],
                  'free_bytes': None, 'total_bytes': None, 'checks': [],
                  'gpu_devices': worker_module.gpu.inventory(), 'graphics': None}
        def check(name, status, detail):
            report['checks'].append({'name': name, 'status': status, 'detail': detail})
        if identifier in self.active_jobs:
            check('Operation', 'warning', 'An operation is in progress. Check again after it completes.')
            return report
        try:
            self.storage.ensure(identifier)
            instance = self.backend.location(identifier)
            usage = shutil.disk_usage(instance if instance.exists() else self.backend.root)
            report.update(free_bytes=usage.free, total_bytes=usage.total)
            check('Storage', 'ok', 'Storage is accessible at the configured location.')
        except (OSError, RuntimeError) as error:
            check('Storage', 'error', str(error))
            return report  # Never inspect a worker through an unavailable anchor.
        if not instance.exists():
            check('Android worker', 'warning', 'Not prepared or started yet.')
            return report
        with self.worker_rpc(identifier):
            try:
                worker = self.android.status(identifier)
                report['worker_state'] = worker['state']
                report['gpu_node'] = worker.get('gpu_node')
                if worker['state'] == 'Running':
                    check('Android worker', 'ok', 'Authenticated worker reports Android boot completed.')
                    if 'graphics' in worker.get('capabilities', []):
                        try:
                            graphics = self.android.status(identifier, 'graphics')
                            if graphics.get('generation') != worker.get('generation') or graphics.get('state') != 'Running':
                                raise RuntimeError('Runtime changed during graphics inspection; retry.')
                            if graphics.get('graphics_error'): raise RuntimeError(graphics['graphics_error'])
                            report['graphics'] = graphics.get('graphics')
                            if not isinstance(report['graphics'], dict): raise RuntimeError('Renderer report unavailable.')
                            report['gpu_selection'] = worker_module.gpu.info(instance)['selection']
                            report['gpu_verification'] = worker_module.gpu.verification(
                                report['gpu_selection'], report.get('gpu_node'), report['graphics'])
                        except (OSError, RuntimeError, ValueError) as error:
                            check('Graphics', 'warning', str(error))
                    else:
                        check('Graphics', 'warning', 'Stop and start this runtime to enable renderer inspection.')

                    check('Network', 'ok' if worker.get('network') == 'private_uplink' else 'warning',
                          'Private IPv4 uplink and DNS are configured. Internet availability depends on the host connection.'
                          if worker.get('network') == 'private_uplink' else
                          'This worker has no Internet uplink. Stop and start it to load the updated network support.')
                elif worker['state'] == 'Frozen':
                    check('Android worker', 'ok', 'Android is suspended while idle; opening an app resumes it.')
                elif worker['state'] == 'Stopped':
                    check('Android worker', 'ok', 'Worker status reports Android is stopped.')
                    if worker.get('shutdown_forced'):
                        check('Last shutdown', 'warning', worker.get('shutdown_warning') or 'Android required forced cleanup.')
                else:
                    check('Android worker', 'error' if worker['state'] == 'Error' else 'warning', worker.get('error') or worker['state'])
                if record['state'] in ('Running', 'Frozen', 'Stopped', 'Starting', 'Stopping') and worker['state'] != record['state']:
                    check('State', 'warning', 'Worker state differs from the saved state. Refresh Settings to reconcile.')
            except (OSError, RuntimeError, ValueError) as error:
                check('Android worker', 'error', str(error))
        if record['job'] and record['job']['status'] == 'Failed':
            check('Last operation', 'warning', record['job']['error'] or 'The last operation failed.')
        return report

    def arm_sources(self, uid):
        directory = self.arm_sources_root / str(uid)
        result = []
        for path in sorted(directory.glob('*.zip')):
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 256 * 1024**2:
                    continue
                digest = path.stem
                if not re.fullmatch(r'[a-f0-9]{64}', digest): continue
                metadata_path = directory / (digest + '.json')
                metadata = read_file(metadata_path) if metadata_path.exists() else {'engine': 'libndk', 'version':'Custom ' + digest[:12], 'label':'libndk / custom ' + digest[:12]}
                known = next((item for item in worker_module.arm.CATALOG if item['sha256'] == digest), {})
                metadata = dict(metadata, **known)
                result.append(dict(metadata, id=digest, sha256=digest, bytes=info.st_size, installed=True))
            except OSError: continue
        for digest in (worker_module.arm.SHA256, worker_module.arm.DEFAULT_SHA256):
            archive = worker_module.arm.cached_archive(digest)
            if archive.is_file():
                source = next((item for item in worker_module.arm.CATALOG if item['sha256'] == digest), None)
                if source is None: continue
                result = [item for item in result if item['sha256'] != digest]
                result.insert(0, dict(source, id=digest, provider=source['label'], installed=True,
                                      bytes=archive.stat().st_size))
        for item in worker_module.arm.CATALOG:
            if not any(source['sha256'] == item['sha256'] for source in result):
                result.append(dict(item, id=item['sha256'], installed=False, bytes=0))
        return result

    def import_arm_source(self, uid, fd):
        require(fd is not None, 'INVALID_REQUEST', 'ARM translation archive descriptor required')
        directory = self.arm_sources_root / str(uid)
        if not directory.exists():
            directory.mkdir(mode=0o700)
            os.chmod(directory, 0o700)
        secure_directory(directory, True)
        temporary = directory / ('.import-' + uuid.uuid4().hex + '.zip')
        validation = directory / ('.validate-' + uuid.uuid4().hex)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 256 * 1024**2,
                    'INVALID_REQUEST', 'ARM translation archive must be a regular file up to 256 MiB')
            with os.fdopen(os.dup(fd), 'rb') as source, temporary.open('xb') as target:
                os.fchmod(target.fileno(), 0o600)
                source.seek(0)
                remaining = info.st_size
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    require(block, 'INVALID_REQUEST', 'ARM translation archive changed during import')
                    target.write(block); remaining -= len(block)
                target.flush(); os.fsync(target.fileno())
                after = os.fstat(source.fileno())
                require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                        (after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                        'INVALID_REQUEST', 'ARM translation archive changed during import')
            digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            worker_module.arm.stage(temporary, validation, digest)
            manifest = worker_module.arm.verify(validation, digest)
            metadata = worker_module.arm.describe(manifest['files'], digest)
            final = directory / (digest + '.zip')
            if not final.exists(): os.replace(temporary, final)
            storage_module.atomic(directory / (digest + '.json'), metadata)
            return dict(metadata, id=digest, sha256=digest, bytes=final.stat().st_size, installed=True)
        finally:
            temporary.unlink(missing_ok=True)
            if validation.exists(): shutil.rmtree(validation)

    @contextlib.contextmanager
    def unlocked(self):
        """Run slow I/O without the global mutex.

        The caller must first reserve every resource that I/O touches
        (reserve_runtime/reserve), so conflicting requests are refused while
        the lock is released. Inputs used during the I/O must be immutable
        snapshots; results are published after the mutex is reacquired."""
        if not self.mutex._is_owned():
            yield
            return
        state = self.mutex._release_save()
        try:
            yield
        finally:
            self.mutex._acquire_restore(state)

    @contextlib.contextmanager
    def reserve_runtime(self, identifier, message='Wait for the runtime operation to finish'):
        """Hold a runtime like a job (active_jobs) and release the mutex."""
        require(identifier not in self.active_jobs, 'BUSY', message)
        self.active_jobs.add(identifier)
        try:
            with self.unlocked():
                yield
        finally:
            self.active_jobs.discard(identifier)

    @contextlib.contextmanager
    def reserve(self, key, message):
        require(key not in self.reservations, 'BUSY', message)
        self.reservations.add(key)
        try:
            with self.unlocked():
                yield
        finally:
            self.reservations.discard(key)

    @contextlib.contextmanager
    def worker_rpc(self, identifier):
        """Serialize RPCs to one worker while other runtimes stay responsive."""
        lock = self.rpc_locks.setdefault(identifier, threading.Lock())
        with self.unlocked():
            with lock:
                yield

    def watch_token(self, uid):
        """Fingerprint of everything a runtime list shows for this user.

        Registry fields come from memory. Live runtimes also report their
        worker state (Running/Frozen/Stopped can change without a registry
        write, e.g. idle freeze), read outside the global lock."""
        with self.mutex:
            records = [r for r in self.data['runtimes'] if r['owner_uid'] == uid]
            summary = [[r['id'], r['name'], r['state'], r.get('start_on_boot'), (r.get('job') or {}).get('id'),
                        (r.get('job') or {}).get('status'), self.prepare_phases.get(r['id']), r['id'] in self.deletions,
                        r['id'] in self.active_jobs] for r in records]
            live = [r['id'] for r in records if r['state'] in ('Starting', 'Running', 'Frozen', 'Stopping') and r['id'] not in self.active_jobs]
        workers = {}
        for identifier in live:
            try: workers[identifier] = self.android.status(identifier).get('state')
            except Exception: workers[identifier] = 'unavailable'
        download = self.downloads.status(uid)
        summary.append(['download', download.get('status'), download.get('id')])
        summary.append(['workers', sorted(workers.items())])
        return hashlib.sha256(json.dumps(summary, sort_keys=True, default=str).encode()).hexdigest()[:32]

    def watch(self, request, uid, apk_fd=None):
        """Long-poll: answer when the caller's runtime view changes, or at the
        timeout. The GUI refreshes on change instead of re-reading on a timer."""
        require(set(request) == {'op', 'since', 'timeout'} and apk_fd is None, 'INVALID_REQUEST', 'Expected since and timeout')
        since, timeout = request['since'], request['timeout']
        require(since is None or (isinstance(since, str) and re.fullmatch(r'[0-9a-f]{32}', since)), 'INVALID_REQUEST', 'Invalid watch token')
        require(type(timeout) is int and 1 <= timeout <= WATCH_LIMIT, 'INVALID_REQUEST', 'Invalid watch timeout')
        deadline = time.monotonic() + timeout
        while True:
            token = self.watch_token(uid)
            remaining = deadline - time.monotonic()
            if token != since or remaining <= 0:
                return {'token': token, 'changed': since is not None and token != since}
            # Registry commits wake us at once; worker-only changes (freeze,
            # crash) are picked up by re-reading worker state every few seconds.
            with self.changed: self.changed.wait(min(WATCH_WORKER_INTERVAL, remaining))

    def dispatch(self, request, uid, apk_fd=None):
        if isinstance(request, dict) and request.get('op') == 'watch':
            return self.watch(request, uid, apk_fd)  # never holds the global lock while waiting
        with self.mutex:
            result = copy.deepcopy(self._dispatch(request, uid, apk_fd))
            for record in result if isinstance(result, list) else [result]:
                if isinstance(record, dict) and record.get('id') in self.imports:
                    record['import_source'] = 'default'
                    record['import_status'] = self.imports[record['id']]
                    record['prepare_phase'] = self.prepare_phases.get(record['id'])
                if isinstance(record, dict) and record.get('id') in self.selections:
                    record['image_selection'] = copy.deepcopy(self.selections[record['id']])
                    record['prepare_phase'] = self.prepare_phases.get(record['id'])
                    try:
                        record['compatibility'] = read_file(self.backend.location(record['id']) / 'support/compatibility.json')
                    except (OSError, ValueError, RequestError): pass
                if isinstance(record, dict) and record.get('id') in self.deletions: record['deletion_pending'] = True
                if isinstance(record, dict) and record.get('state') == 'Stopped' and record.get('id') != 'default' and record.get('id'):
                    try:
                        saved = read_file(self.backend.location(record['id']) / 'worker-result.json')
                        if isinstance(saved, dict) and saved.get('runtime_id') == record['id'] and saved.get('state') == 'Stopped' and saved.get('shutdown_forced') is True:
                            record['shutdown_warning'] = saved.get('shutdown_warning') or 'Android required forced cleanup. Recent unsaved changes may be lost.'
                    except (OSError, ValueError, RequestError): pass
            return result

    def _dispatch(self, request, uid, apk_fd=None):
        require(isinstance(request, dict), 'INVALID_REQUEST', 'Expected a JSON object')
        operation = request.get('op')
        if isinstance(operation, str) and operation in features_module.FIELDS:
            try:
                return features_module.dispatch(self, request, uid, apk_fd)
            except (OSError, RuntimeError, ValueError) as error:
                raise RequestError('FEATURE', str(error)) from error
        selection = None
        if operation == 'existing_import':
            require(set(request) == {'op', 'id', 'name'} and request['id'] == 'default' and apk_fd is None,
                    'INVALID_REQUEST', 'Expected System Waydroid and a new runtime name')
            validate_name(request['name'])
            source = self.existing.import_source(uid)
            os.close(source)
            require(self.backend.source == Path('/var/lib/waydroid/images'), 'CONFIG', 'Import requires installed Waydroid images')
            require(self.existing.config().get('waydroid', 'images_path', fallback='') == str(self.backend.source),
                    'CONFIG', 'System Waydroid uses a different image directory; import cannot verify that image pair')
            require(len(self.active_jobs) < 4 and self.storage.busy is None and
                    not any(self.imports.get(identifier) == 'pending' for identifier in self.active_jobs),
                    'BUSY', 'Wait for current operations to finish')
            record = self._dispatch({'op': 'create', 'name': request['name']}, uid)
            identifier = record['id']
            self.imports[identifier] = 'pending'
            storage_module.atomic(self.imports_path, self.imports)
            self.displays[identifier] = 'desktop'
            storage_module.atomic(self.display_path, self.displays)
            return self._dispatch({'op': 'prepare', 'id': identifier}, uid)
        if operation == 'image_select':
            require(set(request) == {'op', 'image_id'} and apk_fd is None, 'INVALID_REQUEST', 'Expected image ID')
            return self.downloads.select(uid, request['image_id'])
        if operation == 'image_rename':
            require(set(request) == {'op', 'image_id', 'name'} and isinstance(request['name'], str) and apk_fd is None,
                    'INVALID_REQUEST', 'Expected image ID and name')
            return self.downloads.rename(uid, request['image_id'], request['name'])
        if operation == 'image_import':
            require(set(request) == {'op', 'name'} and apk_fd is not None and isinstance(request['name'], str), 'INVALID_REQUEST', 'Custom import requires a ZIP file descriptor and name')
            return self.downloads.import_custom(uid, apk_fd, request['name'])
        if operation == 'image_import_folder':
            require(set(request) == {'op', 'name'} and isinstance(request['name'], str) and isinstance(apk_fd, tuple) and len(apk_fd) == 2,
                    'INVALID_REQUEST', 'Folder import requires system and vendor file descriptors')
            return self.downloads.import_custom(uid, apk_fd, request['name'])
        if operation == 'arm_import':
            require(set(request) == {'op'} and apk_fd is not None and not isinstance(apk_fd, tuple),
                    'INVALID_REQUEST', 'ARM translation import requires one archive descriptor')
            with self.reserve(('arm-import', uid), 'Another ARM translation import is in progress'):
                return self.import_arm_source(uid, apk_fd)
        if operation == 'arm_sources':
            require(set(request) == {'op'} and apk_fd is None, 'INVALID_REQUEST', 'Unexpected ARM source request')
            return self.arm_sources(uid)
        if operation == 'create_image':
            require(set(request) == {'op', 'name', 'flavor', 'system_sha256', 'vendor_sha256'} and apk_fd is None, 'INVALID_REQUEST', 'Unexpected image creation fields')
            cached = self.downloads.status(uid)
            if cached.get('flavor') == 'CUSTOM':
                require(self.downloads.state.get('owner_uid') == uid, 'FORBIDDEN', 'Import your own custom image ZIP before creating a runtime')
            require(cached.get('status') == 'Ready' and cached.get('flavor') == request['flavor']
                    and all(cached.get('images', {}).get(kind, {}).get('sha256') == request[kind + '_sha256'] for kind in ('system', 'vendor')),
                    'IMAGE_NOT_READY', 'Download and verify the selected image pair first; refresh if the cache changed.')
            missing = [tool for tool in ('aapt', 'apksigner', 'unshare', 'java') if shutil.which(tool) is None]
            require(not missing, 'SETUP_REQUIRED', 'Image preparation is missing tools in the controller PATH: '
                    + ', '.join(missing) + '. Install or repair the AnvilDroid package and its runtime dependencies.')
            selection = {'flavor': cached['flavor'], 'custom_name': cached.get('custom_name'),
                         'library_id': cached.get('library_id'), 'images': cached['images'], 'image_bytes': cached['image_bytes'],
                         'arm_default': platform.machine() == 'x86_64'}
            operation = 'create'
            request = {'op': 'create', 'name': request['name']}
        if operation in ('image_download', 'image_download_status', 'image_download_cancel'):
            expected = {'image_download': {'op', 'flavor', 'system_sha256', 'vendor_sha256'},
                        'image_download_status': {'op'}, 'image_download_cancel': {'op', 'job_id'}}[operation]
            require(set(request) == expected and apk_fd is None, 'INVALID_REQUEST', 'Unexpected image download fields')
            if operation == 'image_download_status': return self.downloads.status(uid)
            if operation == 'image_download_cancel': return self.downloads.cancel(uid, request['job_id'])
            variant = self.catalog.selection(request['flavor'], request['system_sha256'], request['vendor_sha256'])
            return self.downloads.start(uid, variant)
        if operation == 'login_start':
            require(set(request) == {'op'} and apk_fd is None, 'INVALID_REQUEST', 'Expected no fields')
            return self.login_start(uid)
        if operation == 'image_catalog':
            require(set(request) == {'op', 'refresh'} and type(request['refresh']) is bool and apk_fd is None,
                    'INVALID_REQUEST', 'Expected only a boolean refresh field')
            return self.catalog.request(request['refresh'])
        fields = {'resources_info': {'op', 'id'}, 'resources_set': {'op', 'id', 'memory_mib', 'cpu_count'}, 'start_on_boot': {'op', 'id', 'enabled'}, 'gpu_info': {'op', 'id'}, 'gpu_set': {'op', 'id', 'node'}, 'arm_info': {'op', 'id'}, 'arm_set': {'op', 'id', 'enabled'}, 'google_services': {'op', 'id'}, 'delete': {'op', 'id', 'confirmed_name'}, 'install': {'op', 'id'}, 'display_info': {'op', 'id'}, 'display_set': {'op', 'id', 'mode'}, 'app_action': {'op', 'id', 'action', 'package'}, 'app_job': {'op', 'id'}, 'apps': {'op', 'id'}, 'health': {'op', 'id'}, 'storage_info': {'op', 'id'}, 'move': {'op', 'id', 'destination'}, 'discard_previous': {'op', 'id', 'confirmed_name'}, 'requirements': {'op'}, 'list': {'op'}, 'create': {'op', 'name'}, 'inspect': {'op', 'id'},
                  'rename': {'op', 'id', 'name'}, 'prepare': {'op', 'id'}, 'reinstall': {'op', 'id', 'flavor'}, 'start': {'op', 'id'}, 'stop': {'op', 'id'}, 'refresh': {'op', 'id'}}
        fields['backend_set'] = {'op', 'id', 'backend', 'presenter'}
        fields['adb_info'] = {'op', 'id'}
        fields['adb_set'] = {'op', 'id', 'enabled'}
        fields['keymaps']={'op','id'}
        fields['keymap_scheme']={'op','id','package','action','scheme_id','name','revision','content'}
        fields['app_states']={'op','id'}
        fields['existing_claim'] = {'op', 'id'}
        fields['system_integration'] = {'op', 'id'}
        require(isinstance(operation, str) and operation in fields, 'UNSUPPORTED', 'Operation is not available')
        arm_fields = ({'op', 'id', 'enabled'}, {'op', 'id', 'enabled', 'archive_sha256'})
        reinstall_fields = ({'op', 'id', 'flavor', 'confirmed_name'}, {'op', 'id', 'flavor', 'confirmed_name', 'system_sha256', 'vendor_sha256'})
        require(set(request) in reinstall_fields if operation == 'reinstall' else set(request) in arm_fields if operation == 'arm_set' else
                (set(request) == fields[operation] or (operation == 'start' and set(request) == {'op', 'id', 'display'}) or (operation == 'app_action' and set(request) == fields[operation] | {'activation_token'})),
                'INVALID_REQUEST', 'Unexpected or missing fields')
        if 'activation_token' in request:
            token = request['activation_token']
            require(request.get('action') == 'full_ui' and isinstance(token, str) and 0 < len(token) <= 512 and all(32 < ord(c) < 127 for c in token), 'INVALID_REQUEST', 'Invalid activation token')
        require(apk_fd is None or operation == 'install', 'INVALID_REQUEST', 'Unexpected file descriptor')
        if operation == 'adb_set':
            require(type(request['enabled']) is bool, 'INVALID_REQUEST', 'Expected a boolean enabled')
        if request.get('id') == 'default':
            require(apk_fd is None, 'INVALID_REQUEST', 'Use the existing APK installation command for system Waydroid')
            require(operation not in ('resources_set', 'gpu_set', 'arm_set', 'existing_claim', 'system_integration') or
                    not any(self.imports.get(identifier) == 'pending' for identifier in self.active_jobs),
                    'BUSY', 'Wait for System Waydroid import to finish')
            if operation == 'refresh' and not self.existing.initialized():
                # `waydroid init` never ran: there is no System Waydroid to
                # manage. Report it without session checks so the GUI hides it.
                return {'id': 'default', 'name': 'System Waydroid', 'state': 'Uninitialized', 'managed': False}
            require('default' not in self.active_jobs or operation == 'refresh',
                    'BUSY', 'Wait for the System Waydroid operation to finish')
            if operation == 'reinstall':
                with self.reserve_runtime('default'):
                    return self.reinstall_existing(request, uid)
            if operation in ('health', 'system_integration'):
                with self.reserve_runtime('default'):
                    return self.existing.dispatch(request, uid)
            if operation in ('adb_info', 'adb_set'):
                self.existing.authorize(uid)
                return self.adb_request(request, uid)
            return self.existing.dispatch(request, uid)
        require(operation != 'existing_claim', 'INVALID_REQUEST', 'Only System Waydroid can be registered for system management')
        require(operation != 'system_integration', 'INVALID_REQUEST', 'System integration applies only to System Waydroid')
        if operation == 'requirements':
            return self.requirements(uid)
        if operation == 'list':
            return [dict(r) for r in self.data['runtimes'] if r['owner_uid'] == uid]
        if operation in ('create', 'rename'):
            validate_name(request['name'])
        if operation == 'create':
            require(len(self.data['runtimes']) < 1024, 'LIMIT', 'Runtime metadata capacity reached')
            require(sum(r['owner_uid'] == uid for r in self.data['runtimes']) < 32,
                    'LIMIT', 'Per-user runtime metadata capacity reached')
            record = {'id': 'r-' + uuid.uuid4().hex, 'owner_uid': uid,
                      'name': request['name'].strip(), 'state': 'Allocated', 'job': None, 'start_on_boot': False}
            if selection:
                self.selections[record['id']] = selection
                storage_module.atomic(self.selections_path, self.selections)
            self.save({'schema_version': 3, 'runtimes': self.data['runtimes'] + [record]})
            return record
        require(isinstance(request['id'], str), 'INVALID_REQUEST', 'Expected runtime ID')
        record = next((r for r in self.data['runtimes'] if r['id'] == request['id'] and r['owner_uid'] == uid), None)
        require(record is not None, 'NOT_FOUND', 'Runtime not found for this user')
        require(record['id'] not in self.deletions or operation in ('delete', 'inspect', 'refresh', 'app_job', 'storage_info'), 'BUSY', 'Deletion is incomplete. Retry Delete to finish; other operations are blocked.')
        if operation in ('adb_info', 'adb_set'):
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            return self.adb_request(request, uid)
        if operation == 'reinstall':
            require(request['confirmed_name'] == record['name'], 'INVALID_REQUEST', 'Runtime name changed; review reinstall again')
            self.storage.ensure(record['id'])
            require(len(self.active_jobs) < 4, 'BUSY', 'Controller operation capacity reached')
            require(record['state'] in ('Allocated', 'Prepared', 'Stopped', 'Error'), 'INVALID_STATE', 'Stop Android before reinstalling its images')
            require(record['id'] not in self.active_jobs and self.storage.busy is None, 'BUSY', 'Wait for the current runtime operation to finish')
            instance = self.backend.location(record['id'])
            if instance.exists():
                require(self.android.quiescent(record['id']), 'BUSY', 'Android worker is still active')
            flavor = request['flavor']
            if flavor == 'installed':
                require(self.backend.source_status()['available'], 'IMAGE_NOT_READY', 'Installed images are unavailable')
                selection = None
            else:
                require(flavor in ('VANILLA', 'GAPPS', 'CUSTOM'), 'INVALID_REQUEST', 'Invalid image flavor')
                cached = self.downloads.status(uid)
                require(cached.get('status') == 'Ready' and cached.get('flavor') == flavor,
                        'IMAGE_NOT_READY', 'Download and verify the selected image pair first; refresh if the cache changed.')
                require(flavor != 'CUSTOM' or self.downloads.state.get('owner_uid') == uid,
                        'FORBIDDEN', 'Import your own custom image before reinstalling this runtime')
                require(all(cached.get('images', {}).get(kind, {}).get('sha256') == request.get(kind + '_sha256') for kind in ('system', 'vendor')),
                        'IMAGE_NOT_READY', 'Selected image cache changed; refresh and retry.')
                selection = {'flavor': cached['flavor'], 'custom_name': cached.get('custom_name'),
                             'library_id': cached.get('library_id'), 'images': cached['images'], 'image_bytes': cached['image_bytes'],
                             'arm_default': platform.machine() == 'x86_64'}
            instance.mkdir(mode=0o700, exist_ok=True)
            self.backend.check_directory(instance)
            storage_module.assert_no_child_mounts(instance)
            storage_module.atomic(instance / 'reinstall.json', {'selection': selection})
            job = {'id': uuid.uuid4().hex, 'operation': 'prepare', 'status': 'Running', 'error': None}
            updated = dict(record, state='Provisioning', job=job)
            self.replace(updated)
            self.active_jobs.add(record['id'])
            self.pool.submit(self.prepare_job, record['id'], job['id'])
            return updated
        if operation == 'delete':
            identifier = record['id']
            require(request['confirmed_name'] == record['name'], 'INVALID_REQUEST', 'Type the current runtime name to delete it')
            require(identifier not in self.active_jobs and self.storage.busy is None and len(self.active_jobs) < 4, 'BUSY', 'Wait for current operations to finish')
            require(identifier in self.deletions or record['state'] in ('Allocated', 'Prepared', 'Stopped') or (record['state'] == 'Error' and record['job']['operation'] == 'prepare'), 'INVALID_STATE', 'Stop Android successfully before deleting this runtime')
            if identifier not in self.deletions:
                self.storage.ensure(identifier)
                instance = self.backend.location(identifier)
                require(not instance.exists() or self.android.quiescent(identifier), 'BUSY', 'Android worker is still active')
                targets = storage_module.deletion_plan(self.storage, identifier)
                self.deletions[identifier] = targets
                storage_module.atomic(self.deletions_path, self.deletions)
            job = {'id': uuid.uuid4().hex, 'runtime_id': identifier, 'action': 'delete', 'status': 'Running', 'error': None}
            self.app_jobs[identifier] = job
            storage_module.atomic(self.app_jobs_path, self.app_jobs)
            self.active_jobs.add(identifier)
            self.storage.busy = identifier
            self.pool.submit(self.delete_job, identifier, dict(job))
            return job
        if operation == 'install':
            require(record['state'] in ('Running', 'Frozen'), 'INVALID_STATE', 'Start this runtime before installing an APK')
            require(record['id'] not in self.active_jobs and len(self.active_jobs) < 4, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try: transfer.apk_size(apk_fd)
            except ValueError as error: raise RequestError('INVALID_APK', str(error))
            job = {'id': uuid.uuid4().hex, 'runtime_id': record['id'], 'action': 'install', 'status': 'Running', 'error': None}
            owned_fd = os.dup(apk_fd)
            try:
                self.app_jobs[record['id']] = job
                storage_module.atomic(self.app_jobs_path, self.app_jobs)
                self.active_jobs.add(record['id'])
                self.pool.submit(self.install_job, record['id'], dict(job), owned_fd)
            except Exception:
                self.active_jobs.discard(record['id'])
                os.close(owned_fd)
                raise
            return job
        if operation in ('resources_info', 'resources_set'):
            self.storage.ensure(record['id'])
            instance = self.backend.location(record['id'])
            if operation == 'resources_info':
                return self.resource_report(record)
            require(record['id'] not in self.active_jobs and self.storage.busy is None
                    and record['state'] in ('Prepared', 'Stopped', 'Error'), 'BUSY', 'Prepare images and stop Android before changing resource limits')
            require(instance.is_dir() and self.android.quiescent(record['id']), 'BUSY', 'Android worker is still active or images are not prepared')
            require(worker_module.resources.host()['supported'], 'UNSUPPORTED', 'Requires systemd and cgroup v2 CPU/memory controllers')
            limits = worker_module.resources.validate({key: request[key] for key in ('memory_mib', 'cpu_count')})
            storage_module.atomic(instance / 'resources.json', limits)
            return self.resource_report(record)
        if operation in ('gpu_info', 'gpu_set'):
            self.storage.ensure(record['id'])
            instance = self.backend.location(record['id'])
            if operation == 'gpu_info':
                return worker_module.gpu.info(instance)
            require(record['id'] not in self.active_jobs and self.storage.busy is None
                    and record['state'] in ('Prepared', 'Stopped', 'Error'), 'BUSY', 'Prepare images and stop Android before changing GPU')
            require(instance.is_dir() and self.android.quiescent(record['id']), 'BUSY', 'Android worker is still active or images are not prepared')
            selected = worker_module.gpu.choose(request['node'])
            storage_module.atomic(instance / 'gpu.json', selected)
            return worker_module.gpu.info(instance)
        if operation in ('arm_info', 'arm_set'):
            self.storage.ensure(record['id'])
            instance = self.backend.location(record['id'])
            selection = self.selections.get(record['id'])
            # Custom/newer images are allowed to opt in. Provisioning still
            # reports experimental compatibility; the bridge payload is
            # verified before it can be mounted into Android.
            supported = bool(platform.machine() == 'x86_64' and ((instance / 'support/images.json').is_file() or self.backend.inspect(record['id'])))
            if operation == 'arm_info':
                return dict(worker_module.arm.info(instance, supported, self.arm_sources(uid)), default_enabled=bool(selection and selection.get('arm_default')))
            require(type(request['enabled']) is bool, 'INVALID_REQUEST', 'Expected boolean ARM setting')
            require(record['id'] not in self.active_jobs and self.storage.busy is None
                    and record['state'] in ('Prepared', 'Stopped', 'Error'), 'BUSY', 'Stop Android before changing ARM support')
            require(supported, 'UNSUPPORTED', 'Prepare runtime images before changing ARM translation.')
            require(self.android.quiescent(record['id']), 'BUSY', 'Android worker is still active')
            support_pin = instance / 'support/images.json'
            images = json.loads(support_pin.read_text()) if support_pin.exists() else self.backend.inspect(record['id'])['images']
            archive = None
            provider = 'libndk_translation 0.2.3'
            digest = request.get('archive_sha256')
            if digest:
                require(isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest), 'INVALID_REQUEST', 'Invalid ARM source checksum')
                archive = self.arm_sources_root / str(uid) / (digest + '.zip')
                cached = worker_module.arm.cached_archive(digest)
                if cached is not None and cached.is_file(): archive = cached
                require(archive.is_file(), 'NOT_FOUND', 'Import this ARM translation source before selecting it')
            # Hashing and unpacking take seconds to minutes: hold only this runtime.
            with self.reserve_runtime(record['id'], 'Stop Android before changing ARM support'):
                if archive is not None:
                    require(hashlib.sha256(archive.read_bytes()).hexdigest() == digest, 'CONFIG', 'ARM source checksum changed; import again')
                worker_module.arm.configure(instance, request['enabled'], images, storage_module.atomic, archive, provider)
            return dict(worker_module.arm.info(instance, supported, self.arm_sources(uid)), default_enabled=bool(selection and selection.get('arm_default')))
        if operation == 'display_info':
            effective = None
            if record['state'] in ('Running', 'Frozen') and record['id'] not in self.active_jobs:
                try: effective = self.android.status(record['id']).get('display_mode')
                except (OSError, RuntimeError, ValueError): pass
            instance = self.backend.location(record['id'])
            return dict(mode=self.displays.get(record['id'], 'desktop'), effective_mode=effective,
                        **worker_module.native_x11.selection(instance))
        if operation == 'backend_set':
            require(request['backend'] in ('auto', 'native-x11') and request['presenter'] in ('amd', 'intel', 'nvidia'),
                    'INVALID_REQUEST', 'Invalid display backend or presenter GPU')
            self.storage.ensure(record['id'])
            instance = self.backend.location(record['id'])
            require(record['id'] not in self.active_jobs and self.storage.busy is None and
                    record['state'] in ('Prepared', 'Stopped', 'Error') and self.android.quiescent(record['id']),
                    'BUSY', 'Prepare images and stop Android before changing display backend')
            if request['backend'] == 'native-x11':
                if not (instance / 'support').exists(): self.android.snapshot_support(record['id'])
                node = worker_module.gpu.resolve(instance)
                worker_module.native_x11.preflight(instance, worker_module.gpu.is_nvidia(node), defer_contract=True)
                vendor = {'amd': '0x1002', 'intel': '0x8086', 'nvidia': '0x10de'}[request['presenter']]
                require(any(d['vendor_id'] == vendor and d['available'] for d in worker_module.gpu.inventory()),
                        'UNSUPPORTED', 'Selected presenter GPU is unavailable')
            storage_module.atomic(instance / 'display-backend.json', {key: request[key] for key in ('backend', 'presenter')})
            return dict(mode=self.displays.get(record['id'], 'desktop'), effective_mode=None,
                        **worker_module.native_x11.selection(instance))
        if operation == 'start_on_boot':
            require(type(request['enabled']) is bool, 'INVALID_REQUEST', 'Expected boolean start-on-boot setting')
            require(record['id'] not in self.active_jobs and record['state'] not in ('Provisioning', 'Starting', 'Stopping'), 'BUSY', 'Wait for the runtime operation before changing start-on-boot')
            updated = dict(record, start_on_boot=request['enabled'])
            self.replace(updated)
            return updated
        if operation == 'display_set':
            require(request['mode'] == 'desktop', 'INVALID_REQUEST', 'Invalid display mode')
            require(record['id'] not in self.active_jobs and (record['state'] in ('Allocated', 'Prepared', 'Stopped') or (record['state'] == 'Error' and record['job']['operation'] == 'prepare')), 'BUSY', 'Stop Android before changing display mode')
            self.storage.ensure(record['id'])
            if self.backend.location(record['id']).exists():
                require(self.android.quiescent(record['id']), 'BUSY', 'Worker still active')
            updated = dict(self.displays, **{record['id']: request['mode']})
            storage_module.atomic(self.display_path, updated)
            self.displays = updated
            return {'mode': request['mode'], 'effective_mode': None}
        if operation == 'app_job':
            return self.app_jobs.get(record['id'])
        if operation == 'app_action':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Runtime already has an operation')
            require(len(self.active_jobs) < 4, 'BUSY', 'Controller operation capacity reached; retry when a job completes')
            require(record['state'] in ('Running', 'Frozen'), 'INVALID_STATE', 'Start this runtime before using app actions')
            require(request['action'] in ('launch', 'force_stop', 'uninstall', 'keymap_edit', 'full_ui'), 'INVALID_REQUEST', 'Unsupported app action')
            require(isinstance(request['package'], str) and len(request['package']) <= 255 and
                    re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', request['package']), 'INVALID_REQUEST', 'Invalid package')
            self.storage.ensure(record['id'])
            job = {'id': uuid.uuid4().hex, 'runtime_id': record['id'], 'action': request['action'],
                   'package': request['package'], 'status': 'Running', 'error': None}
            self.app_jobs[record['id']] = job
            storage_module.atomic(self.app_jobs_path, self.app_jobs)
            self.active_jobs.add(record['id'])
            self.pool.submit(self.app_action_job, record['id'], dict(job), request.get('activation_token'))
            return job
        if operation == 'google_services':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            require(record['state'] in ('Running', 'Frozen'), 'INVALID_STATE', 'Start this runtime before checking Google services')
            self.storage.ensure(record['id'])
            with self.worker_rpc(record['id']):
                return self.android.google_services(record['id'])
        if operation == 'keymap_scheme':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            require(record['state'] in ('Running', 'Frozen'), 'INVALID_STATE', 'Start this runtime before managing schemes')
            self.storage.ensure(record['id'])
            try:
                with self.worker_rpc(record['id']): return self.android.keymap_scheme(record['id'], request)
            except (OSError, RuntimeError, ValueError) as error: raise RequestError('KEYMAP_SCHEME', str(error))
        if operation == 'keymaps':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try:
                with self.worker_rpc(record['id']): return self.android.keymaps(record['id'])
            except (OSError,RuntimeError,ValueError) as error: raise RequestError('KEYMAPS',str(error))
        if operation == 'app_states':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try:
                with self.worker_rpc(record['id']): return self.android.app_states(record['id'])
            except (OSError, RuntimeError, ValueError) as error: raise RequestError('APP_STATES', str(error))
        if operation == 'apps':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try:
                with self.worker_rpc(record['id']):
                    return self.android.apps(record['id'])
            except (OSError, RuntimeError, ValueError) as error:
                raise RequestError('APPS', str(error))
        if operation == 'health':
            return self.health(record)
        if operation == 'storage_info':
            if record['id'] in self.deletions: return self.storage.report(record['id'])
            if record['id'] not in self.active_jobs:
                try: self.storage.ensure(record['id'])
                except RuntimeError: pass
            report = copy.deepcopy(self.storage.report(record['id']))
            try:
                usage = shutil.disk_usage(report['path'])
                report.update(filesystem_used_bytes=usage.total - usage.free, free_bytes=usage.free, total_bytes=usage.total, limit_bytes=None, limit_status='unavailable')
                report.update(storage_module.runtime_usage(report['path']))
            except OSError as error: report['disk_error'] = str(error)
            return report
        if operation == 'inspect':
            return dict(record)
        if operation == 'refresh':
            if record['id'] in self.deletions: return dict(record)
            if record['job'] and record['job']['operation'] in ('start', 'stop'):
                return self.reconcile(record)
            return dict(record)
        require(record['id'] not in self.active_jobs, 'BUSY', 'Runtime operation is completing')
        if operation == 'move':
            failed_prepare = record['state'] == 'Error' and record['job']['operation'] == 'prepare'
            require(record['state'] in ('Allocated', 'Prepared', 'Stopped') or failed_prepare, 'INVALID_STATE', 'Stop Android successfully before changing its storage location')
            require(isinstance(request['destination'], str) and request['destination'], 'INVALID_REQUEST', 'Choose a destination folder')
            self.storage.ensure(record['id'])
            instance = self.backend.location(record['id'])
            instance.mkdir(mode=0o700, exist_ok=True)
            require(self.android.quiescent(record['id']), 'BUSY', 'Android worker is still active')
            try:
                destination_fd = self.storage.begin(record['id'], request['destination'], uid)
            except (OSError, RuntimeError) as error:
                raise RequestError('STORAGE', str(error))
            self.active_jobs.add(record['id'])
            self.pool.submit(self.move_storage_job, record['id'], destination_fd)
            return dict(record, storage=self.storage.report(record['id']))
        if operation == 'discard_previous':
            require(request['confirmed_name'] == record['name'], 'INVALID_REQUEST', 'Type the current runtime name to remove the old copy')
            try:
                self.storage.begin_cleanup(record['id'])
            except (OSError, RuntimeError) as error:
                raise RequestError('STORAGE', str(error))
            self.active_jobs.add(record['id'])
            self.pool.submit(self.cleanup_storage_job, record['id'])
            return dict(record, storage=self.storage.report(record['id']))
        if operation in ('prepare', 'start', 'stop'):
            try: self.storage.ensure(record['id'])
            except (OSError, RuntimeError) as error: raise RequestError('STORAGE', str(error))
        if operation == 'start':
            require(not (self.backend.location(record['id']) / 'reinstall.json').exists(), 'INVALID_STATE', 'Reinstall interrupted; retry preparation first')
        if operation in ('start', 'stop'):
            require(operation != 'start' or self.imports.get(record['id']) != 'pending',
                    'INVALID_STATE', 'Finish importing System Waydroid before starting this copy')
            desktop = None
            if operation == 'start' and record['state'] not in ('Running', 'Frozen'):
                display = request.get('display')
                require(isinstance(display, str) and len(display) <= 64, 'DESKTOP_SESSION',
                        'Desktop mode requires a Wayland or X11 graphical session. Open AnvilDroid from your desktop and retry.')
                if re.fullmatch(r'(?:wayland|anvildroid-gnome)-[A-Za-z0-9_.-]+', display):
                    socket_path = Path('/run/user') / str(uid) / display
                    try:
                        fd = worker_module.pin_desktop_socket(socket_path, uid)
                        os.close(fd)
                    except (OSError, RuntimeError) as error: raise RequestError('DESKTOP_SESSION', str(error))
                elif display.startswith(desktop_module.X11_TOKEN_PREFIX) and desktop_module.parse_x11_display(display[len(desktop_module.X11_TOKEN_PREFIX):]):
                    x11_display = display[len(desktop_module.X11_TOKEN_PREFIX):]
                    require(desktop_module.x11_reachable(x11_display), 'DESKTOP_SESSION',
                            f'X server for display {x11_display} is not reachable. Check your X session and retry.')
                else:
                    raise RequestError('DESKTOP_SESSION',
                                       'Desktop mode requires a Wayland or X11 graphical session. Open AnvilDroid from your desktop and retry.')
                backend = worker_module.native_x11.resolve_backend(
                    worker_module.native_x11.selection(self.backend.location(record['id'])),
                    display.startswith(desktop_module.X11_TOKEN_PREFIX))
                if backend['backend'] == 'native-x11':
                    try:
                        instance = self.backend.location(record['id'])
                        node = worker_module.gpu.resolve(instance)
                        worker_module.native_x11.preflight(instance, worker_module.gpu.is_nvidia(node), defer_contract=True)
                    except (OSError, RuntimeError, ValueError) as error:
                        raise RequestError('UNSUPPORTED', str(error))
                try:
                    self.android.check_desktop_bridge()
                except (OSError, RuntimeError) as error:
                    raise RequestError('DESKTOP_SESSION', str(error))
                desktop = (display, uid)
            if operation == 'start':
                readiness = self.requirements(uid)
                require(readiness['can_start'], 'SETUP_REQUIRED', '; '.join(c['detail'] for c in readiness['checks'] if c['id'] != 'images' and not c['available']))
            require(record['state'] not in ('Provisioning', 'Starting', 'Stopping'), 'BUSY', 'Runtime operation in progress')
            require(record['state'] in ('Prepared', 'Stopped', 'Running', 'Frozen', 'Error'), 'INVALID_STATE', 'Prepare runtime images first')
            require(sum(r['state'] in ('Provisioning', 'Starting', 'Stopping') for r in self.data['runtimes']) < 4,
                    'BUSY', 'Worker capacity reached')
            if operation == 'start' and record['state'] not in ('Running', 'Frozen'):
                budget = self.resource_report(record)
                require(not budget['admission']['memory_blocked'], 'RESOURCE_BUDGET', budget['admission']['detail'])
            job = {'id': uuid.uuid4().hex, 'operation': operation, 'status': 'Running', 'error': None}
            updated = dict(record, state='Starting' if operation == 'start' else 'Stopping', job=job)
            self.replace(updated)
            self.active_jobs.add(record['id'])
            self.pool.submit(self.android_job, record['id'], job['id'], operation, desktop)
            return updated
        if operation == 'prepare':
            require(record['state'] != 'Provisioning', 'BUSY', 'Runtime already has an operation')
            require(record['state'] in ('Allocated', 'Prepared', 'Error') or (record['state'] == 'Stopped' and (self.backend.location(record['id']) / 'reinstall.json').exists()), 'INVALID_STATE', 'Cannot replace images of a started runtime')
            if (self.backend.location(record['id']) / 'worker-identity.json').exists():
                require(self.android.quiescent(record['id']), 'BUSY', 'Stop the Android worker before preparing images')
            require(sum(r['state'] in ('Provisioning', 'Starting', 'Stopping') for r in self.data['runtimes']) < 4,
                    'BUSY', 'Image preparation capacity reached')
            job = {'id': uuid.uuid4().hex, 'operation': 'prepare', 'status': 'Running', 'error': None}
            updated = dict(record, state='Provisioning', job=job)
            self.replace(updated)
            self.active_jobs.add(record['id'])
            self.pool.submit(self.prepare_job, record['id'], job['id'])
            return updated
        require(record['state'] not in ('Provisioning', 'Starting', 'Stopping'), 'BUSY', 'Wait for the runtime operation')
        updated = dict(record, name=request['name'].strip())
        self.save({'schema_version': 3, 'runtimes': [updated if r['id'] == record['id'] else r for r in self.data['runtimes']]})
        return updated


def validate_name(name):
    require(isinstance(name, str) and 0 < len(name.strip()) <= 128 and
            all(ord(c) >= 32 and ord(c) != 127 for c in name), 'INVALID_REQUEST', 'Invalid runtime name')


# Requests that may hold a worker thread for seconds to minutes. They are capped
# so status polling (list, refresh, download status) always finds a free thread.
SLOW_OPERATIONS = frozenset({'arm_import', 'arm_set', 'components_set', 'diagnostics_collect', 'reinstall',
                             'existing_import', 'image_import', 'image_import_folder', 'health',
                             'system_integration', 'keymap_scheme', 'google_services',
                             'compatibility_set', 'compatibility_reset'})
IPC_WORKERS = 16
SLOW_SLOTS = 8
MAX_CONNECTIONS = 64
RECEIVE_TIMEOUT = 2
SEND_TIMEOUT = 10
IDLE_POLL = 5
WATCH_LIMIT = 25
WATCH_WORKER_INTERVAL = 3


def error_response(code, message):
    return {'ok': False, 'error': {'code': code, 'message': message}}


class IpcServer:
    """Bounded concurrent request handling.

    Each accepted connection is handled on a worker thread, so a slow client or
    a long operation no longer blocks accept(). Admission is bounded: beyond
    MAX_CONNECTIONS in flight, or SLOW_SLOTS concurrent slow operations, the
    request is answered BUSY instead of growing threads or queues. Peer
    credentials are read on the worker before any dispatch."""

    def __init__(self, store, workers=IPC_WORKERS, slow_slots=SLOW_SLOTS, max_connections=MAX_CONNECTIONS):
        self.store = store
        self.inflight = 0
        self.inflight_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='ipc')
        self.admission = threading.BoundedSemaphore(max_connections)
        self.slow = threading.BoundedSemaphore(slow_slots)

    def submit(self, connection):
        if not self.admission.acquire(blocking=False):
            self.reply(connection, error_response('BUSY', 'Controller is handling too many requests; retry shortly'))
            connection.close()
            return
        with self.inflight_lock: self.inflight += 1
        try:
            self.pool.submit(self.handle, connection)
        except BaseException:
            with self.inflight_lock: self.inflight -= 1
            self.admission.release()
            connection.close()
            raise

    @staticmethod
    def reply(connection, response):
        try:
            connection.settimeout(SEND_TIMEOUT)  # a client that never reads cannot pin a worker
            connection.sendall(json.dumps(response).encode() + b'\n')
        except OSError:
            pass

    def handle(self, connection):
        try:
            with connection:
                self.reply(connection, self.process(connection))
        finally:
            with self.inflight_lock: self.inflight -= 1
            self.admission.release()

    def busy(self):
        with self.inflight_lock: return self.inflight > 0

    def process(self, connection):
        apk_fd = None
        try:
            connection.settimeout(RECEIVE_TIMEOUT)
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            request, apk_fd = transfer.receive(connection, MAX_REQUEST)
            slow = isinstance(request, dict) and request.get('op') in SLOW_OPERATIONS
            if slow and not self.slow.acquire(blocking=False):
                return error_response('BUSY', 'Too many long-running operations; retry when one finishes')
            try:
                return {'ok': True, 'result': self.store.dispatch(request, uid, apk_fd)}
            finally:
                if slow: self.slow.release()
        except RequestError as error:
            return error_response(error.code, error.message)
        except (ValueError, UnicodeError, socket.timeout):
            return error_response('INVALID_REQUEST', 'Invalid or incomplete request')
        except RuntimeError as error:
            return error_response('STORAGE', str(error))
        except OSError:
            return error_response('IO_ERROR', 'Runtime metadata operation failed')
        except Exception as error:  # never let one request take down a worker silently
            print(f'Unhandled controller request error: {error!r}', file=sys.stderr, flush=True)
            return error_response('INTERNAL', 'Unexpected controller error')
        finally:
            if apk_fd is not None:
                for fd in apk_fd if isinstance(apk_fd, tuple) else (apk_fd,):
                    os.close(fd)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


def activation_socket(environ=os.environ, pid=None):
    """The listening socket systemd passed (sd_listen_fds protocol), or None."""
    if environ.get('LISTEN_PID') != str(os.getpid() if pid is None else pid) or environ.get('LISTEN_FDS') != '1':
        return None
    server = socket.socket(fileno=3)
    for name in ('LISTEN_PID', 'LISTEN_FDS', 'LISTEN_FDNAMES'):
        os.environ.pop(name, None)  # never leak activation to child processes
    require(server.family == socket.AF_UNIX and server.type == socket.SOCK_STREAM, 'CONFIG', 'Unexpected activation socket')
    os.set_inheritable(server.fileno(), False)
    return server


def service_processes(cgroup_root=Path('/sys/fs/cgroup'), proc=Path('/proc/self/cgroup')):
    """Other processes in this service's cgroup tree, or None when unknown.

    Workers, Android containers, downloads and helpers all live here
    (KillMode=control-group), so the controller must not exit while any do."""
    try:
        relative = next(line[3:] for line in proc.read_text().splitlines() if line.startswith('0::')).strip('/')
        if not relative:
            return None
        pids = set()
        for procs in (cgroup_root / relative).rglob('cgroup.procs'):
            pids.update(int(value) for value in procs.read_text().split())
    except (OSError, StopIteration, ValueError):
        return None
    pids.discard(os.getpid())
    return pids


TAKEOVER_WAIT = 30


def standalone_controllers(store_path, proc=Path('/proc'), self_pid=None, root=0):
    """PIDs of other root runtime-controller.py processes serving store_path."""
    me = os.getpid() if self_pid is None else self_pid
    found = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            if entry.stat().st_uid != root:
                continue
            args = [part.decode(errors='replace') for part in (entry / 'cmdline').read_bytes().split(b'\0') if part]
        except OSError:
            continue
        if not any(arg.endswith('/runtime-controller.py') for arg in args) or '--store' not in args:
            continue
        index = args.index('--store')
        if index + 1 < len(args) and Path(args[index + 1]) == Path(store_path):
            found.append(int(entry.name))
    return found


def store_locked(store_path):
    """True when another process holds the store lock (non-destructive probe)."""
    try:
        fd = os.open(Path(store_path) / 'controller.lock', os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)  # closing also releases a probe lock
    return False


def take_over_store(store_path, wait=TAKEOVER_WAIT, find=standalone_controllers, kill=os.kill,
                    sleep=time.sleep, clock=time.monotonic):
    """A socket-activated controller found the store locked by a controller
    started outside systemd (GUI start button, AppImage bootstrap). systemd
    now owns the endpoint, so that process never sees new clients and they
    queue unanswered (EAGAIN). Replace it: SIGKILL, because releases before
    0.1.10 unlink the shared endpoint path on a clean exit. Runtime workers
    are separate processes and keep running."""
    pids = find(store_path)
    for pid in pids:
        print(f'Taking over from controller process {pid} started outside systemd', flush=True)
        with contextlib.suppress(ProcessLookupError):
            kill(pid, signal.SIGKILL)
    deadline = clock() + wait
    while find(store_path) and clock() < deadline:
        sleep(0.2)
    return pids


class IdleExit:
    """Decides when a socket-activated controller may exit."""
    def __init__(self, store, ipc, seconds, clock=time.monotonic, processes=service_processes):
        self.store, self.ipc, self.seconds, self.clock, self.processes = store, ipc, seconds, clock, processes
        self.since = None

    def activity(self):
        self.since = None

    def due(self):
        if self.ipc.busy() or not self.store.idle() or self.processes() != set():
            self.since = None
            return False
        now = self.clock()
        if self.since is None:
            self.since = now
        return now - self.since >= self.seconds


def serve(store_path, socket_path, image_source, allow_temporary_storage=False, idle_exit=None):
    require(os.geteuid() == 0, 'CONFIG', 'Controller must run as root')
    inherited = activation_socket()
    secure_directory(socket_path.parent, False)
    if inherited and store_locked(store_path):
        take_over_store(store_path)
    store = Store(store_path, image_source, allow_temporary_storage)
    endpoint_lock = os.open(str(socket_path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    metadata = os.fstat(endpoint_lock)
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o077 and metadata.st_nlink == 1,
            'CONFIG', 'Unsafe endpoint lock')
    fcntl.flock(endpoint_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    server = inherited or socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    try:
        if inherited:
            pass  # systemd owns the endpoint; queued clients survive our exit
        elif socket_path.exists() or socket_path.is_symlink():
            metadata = socket_path.lstat()
            require(stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == 0, 'CONFIG', 'Unsafe existing endpoint')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                try:
                    probe.connect(str(socket_path))
                except OSError as error:
                    require(error.errno == errno.ECONNREFUSED, 'CONFIG', 'Cannot verify stale endpoint')
                    socket_path.unlink()
                else:
                    raise RequestError('CONFIG', 'Endpoint already in use')
        if not inherited:
            server.bind(str(socket_path))
            bound = socket_path.lstat().st_ino
            os.chmod(socket_path, 0o666)  # authorization is SO_PEERCRED + record ownership
            server.listen(64)
        print('ready', flush=True)
        ipc = IpcServer(store)
        # Only a socket-activated controller exits when idle: systemd keeps
        # listening and starts it again on the next client connection.
        idle = IdleExit(store, ipc, idle_exit) if inherited and idle_exit else None
        try:
            while True:
                if idle and not select.select([server], [], [], IDLE_POLL)[0]:
                    if idle.due():
                        print('Idle; exiting until the next client connects', flush=True)
                        break
                    continue
                connection, _ = server.accept()
                if idle: idle.activity()
                ipc.submit(connection)
        finally:
            ipc.close()
    finally:
        server.close()
        # Remove only our own endpoint: systemd may have bound a new socket
        # at this path while we were running.
        with contextlib.suppress(OSError):
            if bound and socket_path.lstat().st_ino == bound:
                socket_path.unlink()
        store.close()
        os.close(endpoint_lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--image-source', type=Path, default=Path('/var/lib/waydroid/images'), help='Administrator-only image source directory')
    parser.add_argument('--allow-temporary-storage', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--idle-exit', type=int, metavar='SECONDS',
                        help='When socket activated, exit after this long with no runtime, job or client')
    args = parser.parse_args()
    def shutdown(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    try:
        serve(args.store, args.socket, args.image_source, args.allow_temporary_storage, args.idle_exit)
    except KeyboardInterrupt:
        pass
