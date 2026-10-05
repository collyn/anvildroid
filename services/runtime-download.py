"""Verified image archive cache. Never mounts, extracts or activates an image."""
import copy
from contextlib import ExitStack
from types import SimpleNamespace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
import importlib.util
_format_spec = importlib.util.spec_from_file_location('runtime_image_format', Path(__file__).with_name('runtime-image-format.py'))
image_format = importlib.util.module_from_spec(_format_spec)
_format_spec.loader.exec_module(image_format)


MARGIN = 256 * 1024**2
MAX_IMAGE = 16 * 1024**3
ACTIVE = ('Downloading', 'Verifying', 'Cancelling')


def trusted_url(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not (host in ('sourceforge.net', 'downloads.sourceforge.net')
                    or host.endswith('.dl.sourceforge.net'))):
        raise RuntimeError('Image download redirected outside the trusted HTTPS mirrors')
    return url


class MirrorRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return super().redirect_request(request, fp, code, message, headers, trusted_url(url))


class Cancelled(RuntimeError):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled('Download cancelled')


def archive_info(path, expected, kind, cancel):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024**2):
            check_cancel(cancel)
            digest.update(block)
    if path.stat().st_size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
        raise RuntimeError(f'{kind} archive size or SHA-256 does not match official OTA metadata')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != 1:
            raise RuntimeError(f'{kind} archive must contain exactly one image')
        item = entries[0]
        mode = item.external_attr >> 16
        if (item.filename != kind + '.img' or item.is_dir() or item.flag_bits & 1
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or not 0 < item.file_size <= MAX_IMAGE
                or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
            raise RuntimeError(f'Unsafe or unsupported {kind} image archive')
        return {'sha256': expected['sha256'], 'archive_bytes': expected['bytes'],
                'image_bytes': item.file_size, 'filename': expected['filename']}


def download_archive(entry, destination, cancel, progress):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), MirrorRedirect())
    request = urllib.request.Request(trusted_url(entry['url']), headers={'User-Agent': 'AnvilDroid/0.1'})
    deadline = time.monotonic() + 3600
    with opener.open(request, timeout=20) as response, destination.open('xb') as stream:
        os.chmod(destination, 0o600)
        if response.status != 200:
            raise RuntimeError('Image server did not return a complete archive')
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != entry['bytes']:
            raise RuntimeError('Image server returned an unexpected archive size')
        received = 0
        while True:
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise RuntimeError('Image download exceeded its time limit; retry')
            block = response.read1(min(1024**2, entry['bytes'] - received + 1))
            if not block:
                break
            received += len(block)
            if received > entry['bytes']:
                raise RuntimeError('Image server exceeded the expected archive size')
            stream.write(block)
            progress(received)
        if received != entry['bytes']:
            raise RuntimeError('Image download was interrupted; retry')
        stream.flush()
        os.fsync(stream.fileno())


def atomic(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync(path):
    fd = os.open(path, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


class Downloads:
    def __init__(self, store):
        self.root = store / 'image-cache'
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or self.root.resolve() != self.root:
            raise RuntimeError('Unsafe image cache directory')
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.thread = None
        self.state_path = self.root / 'download.json'
        self.state = {'status': 'Idle', 'owner_uid': None}
        if self.state_path.exists():
            self.check_file(self.state_path)
            if self.state_path.stat().st_size > 65536: raise RuntimeError('Invalid image download state')
            self.state = json.loads(self.state_path.read_text())
            if self.state.get('status') in ACTIVE:
                self.state.update(status='Failed', error='Controller restarted during download. Retry to verify completed archives and download missing files.')
                atomic(self.state_path, self.state)
        # Only unpublished files in this root-owned cache, never runtime data.
        for path in self.root.glob('.partial-*'):
            self.check_file(path)
            path.unlink()

    @staticmethod
    def check_file(path):
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError('Unsafe image cache file')

    def status(self, uid):
        with self.lock:
            value = copy.deepcopy(self.state)
            value['can_cancel'] = value['status'] in ('Downloading', 'Verifying') and value['owner_uid'] == uid
            value.pop('owner_uid', None)
            value['cache_path'] = str(self.root)
            value['free_bytes'] = shutil.disk_usage(self.root).free
            return value

    def start(self, uid, variant):
        with self.lock:
            if self.state['status'] in ACTIVE: raise RuntimeError('An image download is already in progress')
            missing = sum(variant[kind]['bytes'] for kind in ('system', 'vendor')
                          if not (self.root / (variant[kind]['sha256'] + '.zip')).exists())
            free = shutil.disk_usage(self.root).free
            if free < missing + MARGIN:
                raise RuntimeError(f'Insufficient cache space: need {(missing + MARGIN) / 1024**3:.2f} GiB, available {free / 1024**3:.2f} GiB at {self.root}')
            self.cancelled.clear()
            self.state = {'id': uuid.uuid4().hex, 'owner_uid': uid, 'status': 'Downloading',
                          'flavor': variant['flavor'], 'download_bytes': variant['download_bytes'],
                          'received_bytes': 0, 'phase': 'Starting download', 'error': None, 'images': {}}
            atomic(self.state_path, self.state)
            self.thread = threading.Thread(target=self.run, args=(copy.deepcopy(variant),), daemon=True, name='image-download')
            self.thread.start()
            return self.status(uid)

    def import_custom(self, uid, fd):
        if fd is None: raise RuntimeError('Select a local ZIP containing system.img and vendor.img')
        descriptors = fd if isinstance(fd, tuple) else (fd,)
        sizes = []
        for item in descriptors:
            info = os.fstat(item)
            limit = MAX_IMAGE if isinstance(fd, tuple) else 2 * MAX_IMAGE
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise RuntimeError('Select regular image files up to 16 GiB each, or a ZIP up to 32 GiB')
            sizes.append(info.st_size)
        size = sum(sizes)
        with self.lock:
            if self.state['status'] in ACTIVE: raise RuntimeError('An image download or import is already in progress')
            if shutil.disk_usage(self.root).free < size + MARGIN:
                raise RuntimeError('Insufficient image cache space')
            self.cancelled.clear()
            self.state = {'id':uuid.uuid4().hex, 'owner_uid':uid, 'status':'Verifying',
                          'flavor':'CUSTOM', 'download_bytes':size, 'received_bytes':0,
                          'phase':'Inspecting custom images', 'error':None, 'images':{}}
            atomic(self.state_path, self.state)
            owned_files = []
            try:
                for item in descriptors: owned_files.append(os.dup(item))
            except Exception:
                for item in owned_files: os.close(item)
                raise
            owned = tuple(owned_files) if isinstance(fd, tuple) else owned_files[0]
            self.thread = threading.Thread(target=self.run_import, args=(owned,), daemon=True, name='image-import')
            try: self.thread.start()
            except Exception:
                for item in owned_files: os.close(item)
                self.state.update(status='Failed', error='Cannot start image import')
                atomic(self.state_path, self.state)
                raise
            return self.status(uid)

    def run_import(self, fd):
        temporary = None
        try:
            with ExitStack() as stack:
                if isinstance(fd, tuple):
                    sources = [stack.enter_context(os.fdopen(item, 'rb')) for item in fd]
                    snapshots = [os.fstat(item.fileno()) for item in sources]
                    entries = {kind + '.img': SimpleNamespace(file_size=info.st_size)
                               for kind, info in zip(('system', 'vendor'), snapshots)}
                    streams = dict(zip(('system.img', 'vendor.img'), sources))
                    open_image = lambda name: os.fdopen(os.dup(streams[name].fileno()), 'rb')
                else:
                    source = stack.enter_context(os.fdopen(fd, 'rb'))
                    archive = stack.enter_context(zipfile.ZipFile(source))
                    sources = [source]
                    snapshots = [os.fstat(source.fileno())]
                    entries = {}
                    for entry in archive.infolist():
                        parts = entry.filename.split('/')
                        if entry.filename.startswith('/') or '\\' in entry.filename or '..' in parts:
                            raise RuntimeError('Unsafe ZIP entry path')
                        if entry.is_dir(): continue
                        name = parts[-1]
                        if name not in ('system.img', 'vendor.img') or name in entries:
                            raise RuntimeError('Select a ZIP containing only system.img and vendor.img (folders are allowed)')
                        if (entry.flag_bits & 1 or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                                or not 0 < entry.file_size <= MAX_IMAGE):
                            raise RuntimeError('Unsupported custom image ZIP entry')
                        entries[name] = entry
                    if set(entries) != {'system.img', 'vendor.img'}:
                        raise RuntimeError('Custom ZIP must contain both system.img and vendor.img')
                    open_image = lambda name: archive.open(entries[name])
                total = sum(entry.file_size for entry in entries.values())
                if shutil.disk_usage(self.root).free < total + MARGIN:
                    raise RuntimeError('Insufficient space for custom image cache')
                self.update(download_bytes=total)
                completed, images = 0, {}
                for kind in ('system', 'vendor'):
                    check_cancel(self.cancelled)
                    temporary = self.root / ('.partial-' + uuid.uuid4().hex)
                    self.update(phase=f'Importing custom {kind} image')
                    with open_image(kind + '.img') as image, zipfile.ZipFile(temporary, 'w', zipfile.ZIP_STORED, allowZip64=True) as output:
                        os.chmod(temporary, 0o600)
                        expanded, blocks = image_format.image_stream(image, entries[kind + '.img'].file_size, MAX_IMAGE, lambda: check_cancel(self.cancelled))
                        total += expanded - entries[kind + '.img'].file_size
                        if shutil.disk_usage(self.root).free < expanded + MARGIN:
                            raise RuntimeError('Insufficient space for expanded image')
                        self.update(download_bytes=total)
                        with output.open(kind + '.img', 'w', force_zip64=True) as target:
                            written = 0
                            for block in blocks:
                                check_cancel(self.cancelled)
                                written += len(block)
                                if written > expanded: raise RuntimeError('Image exceeds declared size')
                                target.write(block)
                                self.update(received_bytes=completed + written)
                            if written != expanded: raise RuntimeError('Incomplete image')
                    digest = hashlib.sha256()
                    with temporary.open('rb') as stream:
                        while block := stream.read(1024**2):
                            check_cancel(self.cancelled)
                            digest.update(block)
                    checksum = digest.hexdigest()
                    expected = {'sha256':checksum, 'bytes':temporary.stat().st_size, 'filename':f'custom-{kind}.zip'}
                    images[kind] = archive_info(temporary, expected, kind, self.cancelled)
                    destination = self.root / (checksum + '.zip')
                    if destination.exists(): self.check_file(destination)
                    os.replace(temporary, destination)
                    temporary = None
                    completed += written
                for source, before in zip(sources, snapshots):
                    after = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise RuntimeError('Source images changed during import; select them again')
            with self.lock:
                check_cancel(self.cancelled)
                self.state.update(status='Ready', phase='Custom images imported; not installed or officially verified',
                                  images=images, image_bytes=total)
                sync(self.root)
                atomic(self.state_path, self.state)
        except Exception as error:
            with self.lock:
                self.state.update(status='Cancelled' if isinstance(error, Cancelled) else 'Failed', error=str(error)[-1024:])
                atomic(self.state_path, self.state)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    def close(self):
        # Hold the controller's exclusive store lock until all cache writes stop.
        self.cancelled.set()
        if self.thread is not None:
            self.thread.join()

    def cancel(self, uid, job_id):
        with self.lock:
            if self.state.get('id') != job_id or self.state['owner_uid'] != uid:
                raise RuntimeError('Download job does not belong to this user')
            if self.state['status'] not in ACTIVE: raise RuntimeError('Download is no longer running')
            self.cancelled.set()
            self.state.update(status='Cancelling', phase='Cancelling download…')
            return self.status(uid)

    def update(self, **fields):
        with self.lock:
            check_cancel(self.cancelled)
            self.state.update(fields)

    def run(self, variant):
        temporary = None
        try:
            completed, images = 0, {}
            for kind in ('system', 'vendor'):
                check_cancel(self.cancelled)
                entry = variant[kind]
                destination = self.root / (entry['sha256'] + '.zip')
                if destination.exists() or destination.is_symlink():
                    self.check_file(destination)
                    self.update(status='Verifying', phase=f'Verifying cached {kind} archive')
                    try:
                        images[kind] = archive_info(destination, entry, kind, self.cancelled)
                    except Cancelled: raise
                    except (RuntimeError, zipfile.BadZipFile):
                        destination.unlink()
                if kind not in images:
                    free = shutil.disk_usage(self.root).free
                    if free < entry['bytes'] + MARGIN: raise RuntimeError('Insufficient image cache space; free space and retry')
                    temporary = self.root / ('.partial-' + uuid.uuid4().hex)
                    self.update(status='Downloading', phase=f'Downloading {kind} archive')
                    download_archive(entry, temporary, self.cancelled,
                                     lambda size: self.update(received_bytes=completed + size))
                    self.update(status='Verifying', phase=f'Verifying {kind} SHA-256 and archive layout')
                    images[kind] = archive_info(temporary, entry, kind, self.cancelled)
                    check_cancel(self.cancelled)
                    os.replace(temporary, destination)
                    sync(self.root)
                    temporary = None
                completed += entry['bytes']
                self.update(received_bytes=completed)
            with self.lock:
                check_cancel(self.cancelled)
                self.state.update(status='Ready', phase='Images downloaded and verified; not installed', images=images,
                                  image_bytes=sum(image['image_bytes'] for image in images.values()))
                atomic(self.state_path, self.state)
        except Exception as error:
            with self.lock:
                self.state.update(status='Cancelled' if isinstance(error, Cancelled) else 'Failed', error=str(error)[-1024:])
                atomic(self.state_path, self.state)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
