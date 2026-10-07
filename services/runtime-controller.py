#!/usr/bin/python3
"""P5.3 local runtime control service. Prepares isolated image copies asynchronously; supports experimental persistent headless Android workers.
Run as root with --store ROOT_OWNED_DIR --socket ROOT_OWNED_DIR/control.sock.
Clients are authorized by kernel SO_PEERCRED, not by request-supplied user IDs.
"""
import argparse
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
MAX_REQUEST = 16384
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
        self.catalog = catalog_module.Catalog()
        self.active_jobs = set()
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
        threading.Thread(target=self._start_on_boot, daemon=True).start()
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
            elif record['state'] in ('Starting', 'Running', 'Stopping'):
                self.reconcile(record)

    def _start_on_boot(self):
        time.sleep(2)
        for record in list(self.data['runtimes']):
            if record.get('start_on_boot') and record['state'] in ('Prepared', 'Stopped'):
                try:
                    self._dispatch({'op': 'start', 'id': record['id']}, record['owner_uid'])
                except (RequestError, OSError, RuntimeError):
                    pass

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
                    record['state'] in ('Allocated', 'Provisioning', 'Prepared', 'Starting', 'Running', 'Stopping', 'Stopped', 'Error'), 'CONFIG', 'Invalid runtime owner/state')
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
                            and job['status'] == {'Provisioning': 'Running', 'Prepared': 'Succeeded', 'Starting': 'Running', 'Running': 'Succeeded', 'Stopping': 'Running', 'Stopped': 'Succeeded', 'Error': 'Failed'}[record['state']]
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
                if selection.get('arm_default') and worker_module.arm.read_config(instance) is None:
                    self.prepare_phases[identifier] = 'Enabling default ARM app support…'
                    if not worker_module.arm.ARCHIVE.is_file():
                        raise RuntimeError('Default ARM support requires the controller ARM package. Install it, then retry Prepare.')
                    worker_module.arm.configure(instance, True, json.loads((instance / 'support/images.json').read_text()), storage_module.atomic)
            else:
                self.backend.prepare(identifier)
                if self.imports.get(identifier) == 'pending':
                    self.import_existing_data(identifier)
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
        require(request.get('confirmed_name') == self.existing.saved().get('name', 'Existing runtime'),
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
            if record['job']['operation'] == 'stop' and state in ('Starting', 'Running'):
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

    def app_action_job(self, identifier, job):
        try:
            result = self.android.app_action(identifier, job['action'], job['package'])
            finished = dict(job, status='Succeeded', result=result)
        except Exception as error:
            finished = dict(job, status='Failed', error=str(error)[-1024:])
        with self.mutex:
            try:
                self.app_jobs[identifier] = finished
                storage_module.atomic(self.app_jobs_path, self.app_jobs)
            finally: self.active_jobs.discard(identifier)

    def requirements(self, uid=None):
        images = self.backend.source_status()
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
        support = all(Path(path).exists() for path in (
            '/var/lib/waydroid/overlay', '/var/lib/waydroid/waydroid.prop',
            '/var/lib/waydroid/lxc/waydroid/config', '/var/lib/waydroid/lxc/waydroid/waydroid.seccomp'))
        desktop_info = desktop_module.capabilities(uid)
        checks = [
            {'id': 'waydroid', 'label': 'Waydroid', 'available': waydroid,
             'detail': 'Installed' if waydroid else 'Install Waydroid for your Linux distribution first.'},
            {'id': 'images', 'label': 'Android images', **images},
            {'id': 'tools', 'label': 'Runtime tools', 'available': not missing,
             'detail': 'Available' if not missing else 'Missing: ' + ', '.join(missing)},
            {'id': 'arm_payload', 'label': 'ARM app support package',
             'available': worker_module.arm.ARCHIVE.is_file(),
             'detail': 'Experimental; required for default ARM support in downloaded Android 13 runtimes. Use setup to download it.'},
            {'id': 'support', 'label': 'Waydroid setup', 'available': support,
             'detail': 'Configuration found' if support else 'Complete Waydroid initialization before starting an additional runtime.'},
            {'id': 'desktop', 'label': 'Desktop session', 'available': desktop_info['desktop'], 'detail': desktop_info['detail']}]
        return {'checks': checks, 'can_prepare': waydroid and images['available'],
                'can_start': waydroid and not missing and support,
                'image_bytes': images['bytes'], 'desktop': desktop_info}

    def resource_report(self, record):
        resource = worker_module.resources
        result = resource.info(self.backend.location(record['id']), record['id'], record['state'] == 'Running')
        others = []
        for other in self.data['runtimes']:
            if other['id'] == record['id'] or other['owner_uid'] != record['owner_uid'] or other['state'] not in ('Running', 'Starting', 'Stopping'):
                continue
            try:
                self.storage.ensure(other['id'])
                others.append(resource.info(self.backend.location(other['id']), other['id'], other['state'] == 'Running'))
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
            elif worker['state'] == 'Stopped':
                check('Android worker', 'ok', 'Worker status reports Android is stopped.')
                if worker.get('shutdown_forced'):
                    check('Last shutdown', 'warning', worker.get('shutdown_warning') or 'Android required forced cleanup.')
            else:
                check('Android worker', 'error' if worker['state'] == 'Error' else 'warning', worker.get('error') or worker['state'])
            if record['state'] in ('Running', 'Stopped', 'Starting', 'Stopping') and worker['state'] != record['state']:
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
                result.append(dict(metadata, id=digest, sha256=digest, bytes=info.st_size, installed=True))
            except OSError: continue
        if worker_module.arm.ARCHIVE.is_file():
            result = [item for item in result if item['sha256'] != worker_module.arm.SHA256]
            result.insert(0, {'id': worker_module.arm.SHA256, 'provider': 'libndk_translation 0.2.3', 'engine':'libndk', 'version':'0.2.3', 'installed':True,
                              'sha256': worker_module.arm.SHA256, 'bytes': worker_module.arm.ARCHIVE.stat().st_size,
                              'label': 'Built-in verified libndk_translation 0.2.3'})
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

    def dispatch(self, request, uid, apk_fd=None):
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
        selection = None
        if operation == 'existing_import':
            require(set(request) == {'op', 'id', 'name'} and request['id'] == 'default' and apk_fd is None,
                    'INVALID_REQUEST', 'Expected Existing runtime and a new runtime name')
            validate_name(request['name'])
            source = self.existing.import_source(uid)
            os.close(source)
            require(self.backend.source == Path('/var/lib/waydroid/images'), 'CONFIG', 'Import requires installed Waydroid images')
            require(self.existing.config().get('waydroid', 'images_path', fallback='') == str(self.backend.source),
                    'CONFIG', 'Existing runtime uses a different image directory; import cannot verify that image pair')
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
            require(not any(shutil.which(tool) is None for tool in ('aapt', 'apksigner', 'unshare')), 'SETUP_REQUIRED', 'Image preparation needs aapt, apksigner and unshare installed.')
            selection = {'flavor': cached['flavor'], 'images': cached['images'], 'image_bytes': cached['image_bytes'],
                         'arm_default': platform.machine() == 'x86_64' and bool(re.fullmatch(r'lineage-20\.0-[0-9]{8}-(?:GAPPS|VANILLA)-waydroid_x86_64-system\.zip', cached['images']['system'].get('filename', '')))}
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
        if operation == 'image_catalog':
            require(set(request) == {'op', 'refresh'} and type(request['refresh']) is bool and apk_fd is None,
                    'INVALID_REQUEST', 'Expected only a boolean refresh field')
            return self.catalog.request(request['refresh'])
        fields = {'resources_info': {'op', 'id'}, 'resources_set': {'op', 'id', 'memory_mib', 'cpu_count'}, 'start_on_boot': {'op', 'id', 'enabled'}, 'gpu_info': {'op', 'id'}, 'gpu_set': {'op', 'id', 'node'}, 'arm_info': {'op', 'id'}, 'arm_set': {'op', 'id', 'enabled'}, 'google_services': {'op', 'id'}, 'delete': {'op', 'id', 'confirmed_name'}, 'install': {'op', 'id'}, 'display_info': {'op', 'id'}, 'display_set': {'op', 'id', 'mode'}, 'app_action': {'op', 'id', 'action', 'package'}, 'app_job': {'op', 'id'}, 'apps': {'op', 'id'}, 'health': {'op', 'id'}, 'storage_info': {'op', 'id'}, 'move': {'op', 'id', 'destination'}, 'discard_previous': {'op', 'id', 'confirmed_name'}, 'requirements': {'op'}, 'list': {'op'}, 'create': {'op', 'name'}, 'inspect': {'op', 'id'},
                  'rename': {'op', 'id', 'name'}, 'prepare': {'op', 'id'}, 'reinstall': {'op', 'id', 'flavor'}, 'start': {'op', 'id'}, 'stop': {'op', 'id'}, 'refresh': {'op', 'id'}}
        fields['keymaps']={'op','id'}
        fields['app_states']={'op','id'}
        fields['existing_claim'] = {'op', 'id'}
        require(isinstance(operation, str) and operation in fields, 'UNSUPPORTED', 'Operation is not available')
        arm_fields = ({'op', 'id', 'enabled'}, {'op', 'id', 'enabled', 'archive_sha256'})
        reinstall_fields = ({'op', 'id', 'flavor', 'confirmed_name'}, {'op', 'id', 'flavor', 'confirmed_name', 'system_sha256', 'vendor_sha256'})
        require(set(request) in reinstall_fields if operation == 'reinstall' else set(request) in arm_fields if operation == 'arm_set' else
                (set(request) == fields[operation] or (operation == 'start' and set(request) == {'op', 'id', 'display'})),
                'INVALID_REQUEST', 'Unexpected or missing fields')
        require(apk_fd is None or operation == 'install', 'INVALID_REQUEST', 'Unexpected file descriptor')
        if request.get('id') == 'default':
            require(apk_fd is None, 'INVALID_REQUEST', 'Use the existing APK installation command for system Waydroid')
            require(operation not in ('resources_set', 'gpu_set', 'arm_set', 'existing_claim') or
                    not any(self.imports.get(identifier) == 'pending' for identifier in self.active_jobs),
                    'BUSY', 'Wait for Existing runtime import to finish')
            if operation == 'reinstall':
                return self.reinstall_existing(request, uid)
            return self.existing.dispatch(request, uid)
        require(operation != 'existing_claim', 'INVALID_REQUEST', 'Only Existing runtime can be registered for system management')
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
                selection = {'flavor': cached['flavor'], 'images': cached['images'], 'image_bytes': cached['image_bytes'],
                             'arm_default': platform.machine() == 'x86_64' and bool(re.fullmatch(r'lineage-20\.0-[0-9]{8}-(?:GAPPS|VANILLA)-waydroid_x86_64-system\.zip', cached['images']['system'].get('filename', '')))}
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
            require(record['state'] == 'Running', 'INVALID_STATE', 'Start this runtime before installing an APK')
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
            require(selected['node'] != 'software' or worker_module.gpu.software_supported(instance),
                    'UNSUPPORTED', 'This image does not advertise ANGLE/Pastel software rendering. Prepare a compatible image first.')
            storage_module.atomic(instance / 'gpu.json', selected)
            return worker_module.gpu.info(instance)
        if operation in ('arm_info', 'arm_set'):
            self.storage.ensure(record['id'])
            instance = self.backend.location(record['id'])
            selection = self.selections.get(record['id'])
            # Custom/newer images are allowed to opt in. Provisioning still
            # reports experimental compatibility; the bridge payload is
            # verified before it can be mounted into Android.
            supported = bool(platform.machine() == 'x86_64' and selection and
                             (instance / 'support/images.json').is_file())
            if operation == 'arm_info':
                return dict(worker_module.arm.info(instance, supported, self.arm_sources(uid)), default_enabled=bool(selection and selection.get('arm_default')))
            require(type(request['enabled']) is bool, 'INVALID_REQUEST', 'Expected boolean ARM setting')
            require(record['id'] not in self.active_jobs and self.storage.busy is None
                    and record['state'] in ('Prepared', 'Stopped', 'Error'), 'BUSY', 'Stop Android before changing ARM support')
            require(supported, 'UNSUPPORTED', 'Prepare a downloaded Android 13 Waydroid image first. Copied system runtimes keep their existing ARM setup.')
            require(self.android.quiescent(record['id']), 'BUSY', 'Android worker is still active')
            images = json.loads((instance / 'support/images.json').read_text())
            archive = None
            provider = 'libndk_translation 0.2.3'
            if request.get('archive_sha256'):
                digest = request['archive_sha256']
                require(isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest), 'INVALID_REQUEST', 'Invalid ARM source checksum')
                archive = self.arm_sources_root / str(uid) / (digest + '.zip')
                if digest == worker_module.arm.SHA256 and worker_module.arm.ARCHIVE.is_file(): archive = worker_module.arm.ARCHIVE
                require(archive.is_file(), 'NOT_FOUND', 'Import this ARM translation source before selecting it')
                require(hashlib.sha256(archive.read_bytes()).hexdigest() == digest, 'CONFIG', 'ARM source checksum changed; import again')
            worker_module.arm.configure(instance, request['enabled'], images, storage_module.atomic, archive, provider)
            return dict(worker_module.arm.info(instance, supported, self.arm_sources(uid)), default_enabled=bool(selection and selection.get('arm_default')))
        if operation == 'display_info':
            effective = None
            if record['state'] == 'Running' and record['id'] not in self.active_jobs:
                try: effective = self.android.status(record['id']).get('display_mode', 'headless')
                except (OSError, RuntimeError, ValueError): pass
            return {'mode': self.displays.get(record['id'], 'headless'), 'effective_mode': effective}
        if operation == 'start_on_boot':
            require(type(request['enabled']) is bool, 'INVALID_REQUEST', 'Expected boolean start-on-boot setting')
            require(record['id'] not in self.active_jobs and record['state'] not in ('Provisioning', 'Starting', 'Stopping'), 'BUSY', 'Wait for the runtime operation before changing start-on-boot')
            updated = dict(record, start_on_boot=request['enabled'])
            self.replace(updated)
            return updated
        if operation == 'display_set':
            require(request['mode'] in ('headless', 'desktop'), 'INVALID_REQUEST', 'Invalid display mode')
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
            require(record['state'] == 'Running', 'INVALID_STATE', 'Start this runtime before using app actions')
            require(request['action'] in ('launch', 'force_stop', 'uninstall', 'keymap_edit', 'full_ui'), 'INVALID_REQUEST', 'Unsupported app action')
            require(isinstance(request['package'], str) and len(request['package']) <= 255 and
                    re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', request['package']), 'INVALID_REQUEST', 'Invalid package')
            self.storage.ensure(record['id'])
            job = {'id': uuid.uuid4().hex, 'runtime_id': record['id'], 'action': request['action'],
                   'package': request['package'], 'status': 'Running', 'error': None}
            self.app_jobs[record['id']] = job
            storage_module.atomic(self.app_jobs_path, self.app_jobs)
            self.active_jobs.add(record['id'])
            self.pool.submit(self.app_action_job, record['id'], dict(job))
            return job
        if operation == 'google_services':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            require(record['state'] == 'Running', 'INVALID_STATE', 'Start this runtime before checking Google services')
            self.storage.ensure(record['id'])
            return self.android.google_services(record['id'])
        if operation == 'keymaps':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try: return self.android.keymaps(record['id'])
            except (OSError,RuntimeError,ValueError) as error: raise RequestError('KEYMAPS',str(error))
        if operation == 'app_states':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try: return self.android.app_states(record['id'])
            except (OSError, RuntimeError, ValueError) as error: raise RequestError('APP_STATES', str(error))
        if operation == 'apps':
            require(record['id'] not in self.active_jobs, 'BUSY', 'Wait for the runtime operation to finish')
            self.storage.ensure(record['id'])
            try:
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
                report.update(used_bytes=usage.total - usage.free, free_bytes=usage.free, total_bytes=usage.total, limit_bytes=None, limit_status='unavailable')
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
                    'INVALID_STATE', 'Finish importing Existing runtime before starting this copy')
            desktop = None
            if operation == 'start' and self.displays.get(record['id']) == 'desktop' and record['state'] != 'Running':
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
                try:
                    self.android.check_desktop_bridge()
                except (OSError, RuntimeError) as error:
                    raise RequestError('DESKTOP_SESSION', str(error))
                desktop = (display, uid)
            if operation == 'start':
                readiness = self.requirements(uid)
                require(readiness['can_start'], 'SETUP_REQUIRED', '; '.join(c['detail'] for c in readiness['checks'] if c['id'] != 'images' and not c['available']))
            require(record['state'] not in ('Provisioning', 'Starting', 'Stopping'), 'BUSY', 'Runtime operation in progress')
            require(record['state'] in ('Prepared', 'Stopped', 'Running', 'Error'), 'INVALID_STATE', 'Prepare runtime images first')
            require(sum(r['state'] in ('Provisioning', 'Starting', 'Stopping') for r in self.data['runtimes']) < 4,
                    'BUSY', 'Worker capacity reached')
            if operation == 'start' and record['state'] != 'Running':
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


def serve(store_path, socket_path, image_source, allow_temporary_storage=False):
    require(os.geteuid() == 0, 'CONFIG', 'Controller must run as root')
    secure_directory(socket_path.parent, False)
    store = Store(store_path, image_source, allow_temporary_storage)
    endpoint_lock = os.open(str(socket_path) + '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    metadata = os.fstat(endpoint_lock)
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o077 and metadata.st_nlink == 1,
            'CONFIG', 'Unsafe endpoint lock')
    fcntl.flock(endpoint_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound = False
    try:
        if socket_path.exists() or socket_path.is_symlink():
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
        server.bind(str(socket_path))
        bound = True
        os.chmod(socket_path, 0o666)  # authorization is SO_PEERCRED + record ownership
        server.listen(16)
        print('ready', flush=True)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    request, apk_fd = transfer.receive(connection, MAX_REQUEST)
                    try:
                        response = {'ok': True, 'result': store.dispatch(request, uid, apk_fd)}
                    finally:
                        if apk_fd is not None:
                            for fd in apk_fd if isinstance(apk_fd, tuple) else (apk_fd,): os.close(fd)
                except RequestError as error:
                    response = {'ok': False, 'error': {'code': error.code, 'message': error.message}}
                except (ValueError, UnicodeError, socket.timeout):
                    response = {'ok': False, 'error': {'code': 'INVALID_REQUEST', 'message': 'Invalid or incomplete request'}}
                except RuntimeError as error:
                    response = {'ok': False, 'error': {'code': 'STORAGE', 'message': str(error)}}
                except OSError:
                    response = {'ok': False, 'error': {'code': 'IO_ERROR', 'message': 'Runtime metadata operation failed'}}
                try:
                    connection.sendall(json.dumps(response).encode() + b'\n')
                except OSError:
                    pass
    finally:
        server.close()
        if bound:
            socket_path.unlink(missing_ok=True)
        store.close()
        os.close(endpoint_lock)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--image-source', type=Path, default=Path('/var/lib/waydroid/images'), help='Administrator-only image source directory')
    parser.add_argument('--allow-temporary-storage', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    def shutdown(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, shutdown)
    try:
        serve(args.store, args.socket, args.image_source, args.allow_temporary_storage)
    except KeyboardInterrupt:
        pass
