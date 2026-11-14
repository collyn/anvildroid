"""Verified image archive cache. Never mounts, extracts or activates an image."""
import copy
from collections import deque
from contextlib import ExitStack
from types import SimpleNamespace
import hashlib
import http.client
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
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
CLI_IMPORT_MIN_BYTES = 64 * 1024**2
# Mirrors throttle each connection, so an archive is fetched as fixed-size
# chunks over several connections. Completed chunks are recorded beside the
# partial file, which lets a later attempt continue instead of starting over.
SEGMENTS = 8
MAX_SEGMENTS = 16
CHUNK = 8 * 1024**2
RETRIES = 5
BACKOFF = 1.0
STALL = 120
# Mirrors of the official image host. A name the host no longer knows falls
# back to its default mirror, which the comparison then counts once.
MIRRORS = ('twds', 'sitsa', 'phoenixnap', 'cytranet', 'pilotfiber', 'gigenet', 'netix', 'altushost-swe', 'unlimited',
           'cfhcable', 'psychz', 'yer', 'excellmedia', 'deac-riga', 'deac-fra', 'deac-ams', 'sinalbr', 'ixpeering')
PROBE_SECONDS = 3
PROBE_BYTES = 8 * 1024**2
MIRROR_POOL = 3
RESUME_SECONDS = 7 * 86400
ARCHIVE = re.compile(r'[0-9a-f]{64}\.zip')
RESUMABLE = re.compile(r'\.partial-[0-9a-f]{64}(\.json)?')


def waydroid_cli_import(images):
    """Let Waydroid initialize a private image tree without touching the host.

    Waydroid's public CLI accepts an image directory, not a bundle.  The
    caller has already validated and expanded the bundle; this step delegates
    the platform-specific initialization/configuration to Waydroid itself.
    """
    if shutil.which('waydroid') is None or shutil.which('unshare') is None:
        return
    if sum(path.stat().st_size for path in images.values()) < CLI_IMPORT_MIN_BYTES:
        # Tiny fixtures and development images are not meaningful Waydroid
        # images; avoid invoking the system initializer for them.
        return
    private = Path(tempfile.mkdtemp(prefix='.waydroid-import-', dir=next(iter(images.values())).parent))
    try:
        image_dir = private / 'images'
        image_dir.mkdir(mode=0o700)
        etc = private / 'etc'
        (etc / 'waydroid-extra' / 'images').mkdir(mode=0o755, parents=True)
        # The isolated namespace bind-mounts this directory over /etc. Keep
        # the NSS databases Waydroid's Python CLI needs for pwd.getpwuid().
        # Without passwd, running the importer as root fails with uid-not-found.
        for name in ('passwd', 'group', 'nsswitch.conf'):
            source = Path('/etc') / name
            if source.is_file():
                shutil.copyfile(source, etc / name)
        for kind, source in images.items():
            destination = image_dir / (kind + '.img')
            shutil.copyfile(source, destination)
            os.chmod(destination, 0o600)
        script = (
            'import os, pathlib, subprocess, sys\n'
            'root = pathlib.Path(sys.argv[1])\n'
            'images = pathlib.Path(sys.argv[2])\n'
            'subprocess.run(["mount", "--bind", str(root / "etc"), "/etc"], check=True)\n'
            'subprocess.run(["mount", "--bind", str(root), "/var/lib/waydroid"], check=True)\n'
            'target = pathlib.Path("/etc/waydroid-extra/images")\n'
            'subprocess.run(["mount", "--bind", str(images), str(target)], check=True)\n'
            'env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONNOUSERSITE": "1"}\n'
            'result = subprocess.run(["waydroid", "init", "-i", str(target), "-f"], env=env, text=True, capture_output=True)\n'
            'if result.returncode:\n'
            '    raise SystemExit((result.stderr or result.stdout)[-1600:])\n'
        )
        result = subprocess.run(
            ['unshare', '--mount', '--propagation', 'private', '--fork', '--kill-child',
             sys.executable, '-c', script, str(private), str(image_dir)],
            capture_output=True, text=True, timeout=300)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError('Waydroid image import failed' + (': ' + detail[-1200:] if detail else ''))
    finally:
        shutil.rmtree(private, ignore_errors=True)


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


class RangeUnsupported(RuntimeError):
    pass


class WriteFailed(RuntimeError):
    pass


def sidecar_path(partial):
    return partial.with_name(partial.name + '.json')


def resume_state(partial, total):
    """What a previous attempt saved: finished chunks, and how far each
    unfinished chunk got. Both are empty when the download must start over."""
    sidecar = sidecar_path(partial)
    fresh = set(), {}
    try:
        for path in (partial, sidecar): Downloads.check_file(path)
        if partial.stat().st_size != total or sidecar.stat().st_size > 65536: return fresh
        state = json.loads(sidecar.read_text())
        done, started = state['done'], state['started']
        count = -(-total // CHUNK)
        if (state['bytes'] != total or state['chunk'] != CHUNK or not isinstance(done, list)
                or any(type(index) is not int or not 0 <= index < count for index in done)):
            return fresh
        done, reached = set(done), {}
        for key, position in started.items():
            index = int(key)
            if (index in done or not 0 <= index < count or type(position) is not int
                    or not index * CHUNK < position < min(total, (index + 1) * CHUNK)):
                return fresh
            reached[index] = position
        return done, reached
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return fresh


def chunk_bytes(index, total):
    return min(total, (index + 1) * CHUNK) - index * CHUNK


def resumed_bytes(partial, total):
    done, reached = resume_state(partial, total)
    return (sum(chunk_bytes(index, total) for index in done)
            + sum(position - index * CHUNK for index, position in reached.items()))


def discard_partial(partial):
    partial.unlink(missing_ok=True)
    sidecar_path(partial).unlink(missing_ok=True)


def open_range(opener, url, start, end, total, timeout=20):
    request = urllib.request.Request(trusted_url(url), headers={
        'User-Agent': 'AnvilDroid/0.1', 'Range': f'bytes={start}-{end}'})
    response = opener.open(request, timeout=timeout)
    try:
        if response.status == 200:
            raise RangeUnsupported('Image mirror does not support partial downloads')
        described = ' '.join((response.headers.get('Content-Range') or '').split())
        if response.status != 206 or described != f'bytes {start}-{end}/{total}':
            raise RuntimeError('Image mirror returned an unexpected byte range')
        trusted_url(response.geturl())
    except BaseException:
        response.close()
        raise
    return response


def mirror_candidates(url):
    """The official link as given, plus the same link pinned to each known mirror."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != 'sourceforge.net' or parsed.query: return [url]
    return [url] + [urllib.parse.urlunsplit(parsed._replace(query='use_mirror=' + name)) for name in MIRRORS]


def fastest_mirrors(opener, entry, cancel):
    """Direct mirror links worth using, fastest first; empty when nothing compares.

    The official site often hands every client the same mirror even when it
    is slow, so each known mirror serves the same short range for a moment
    and the download uses the ones that delivered the most.
    """
    candidates = mirror_candidates(entry['url'])
    if len(candidates) < 2: return []
    last = min(entry['bytes'], PROBE_BYTES) - 1
    lock, stop, speeds = threading.Lock(), threading.Event(), {}

    def probe(url):
        try:
            started = time.monotonic()
            with open_range(opener, url, 0, last, entry['bytes'], timeout=PROBE_SECONDS) as response:
                mirror, got = response.geturl(), 0
                while not stop.is_set():
                    block = response.read1(256 * 1024)
                    if not block: break
                    got += len(block)
                    with lock: speeds[mirror] = max(speeds.get(mirror, 0), got / max(time.monotonic() - started, 0.05))
        except (OSError, http.client.HTTPException, RuntimeError):
            pass  # an unreachable or odd mirror is simply not chosen

    threads = [threading.Thread(target=probe, args=(url,), daemon=True, name='image-mirror-probe') for url in candidates]
    for thread in threads: thread.start()
    deadline = time.monotonic() + PROBE_SECONDS
    try:
        while time.monotonic() < deadline and any(thread.is_alive() for thread in threads):
            check_cancel(cancel)
            stop.wait(0.1)
    finally:
        stop.set()
    for thread in threads: thread.join(PROBE_SECONDS + 1)
    with lock: ranked = sorted(speeds.items(), key=lambda item: -item[1])
    if not ranked: return []
    return [mirror for mirror, speed in ranked[:MIRROR_POOL] if speed * 2 >= ranked[0][1]]


def download_segmented(entry, partial, cancel, progress, segments=SEGMENTS):
    """Fetch an archive over several connections, resuming finished chunks.

    The partial file and its chunk record stay on disk on any failure so the
    next attempt continues. Bytes are only trusted after the caller's SHA-256
    check, so it does not matter which mirror served which chunk.
    """
    total = entry['bytes']
    count = -(-total // CHUNK)
    sidecar = sidecar_path(partial)
    done, reached = resume_state(partial, total)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), MirrorRedirect())
    fd = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not done and not reached:
            sidecar.unlink(missing_ok=True)
            os.ftruncate(fd, 0)
            os.ftruncate(fd, total)
        lock = threading.Lock()
        stop = threading.Event()
        pending = deque((index, reached.get(index, index * CHUNK)) for index in range(count) if index not in done)
        # Connections are shared out over the fastest mirrors; one that fails
        # leaves the list, and an empty list means the official link decides.
        mirrors = fastest_mirrors(opener, entry, cancel) if pending and total > CHUNK else []
        shared = {'received': resumed_bytes(partial, total) if done or reached else 0, 'mirror': None,
                  'error': None, 'fatal': None, 'moved': time.monotonic()}

        def write(block, offset):
            view = memoryview(block)
            try:
                while view:
                    written = os.pwrite(fd, view, offset)
                    view, offset = view[written:], offset + written
            except OSError as error:
                raise WriteFailed(f'Cannot write the image cache: {error}') from error

        def worker(slot):
            failures = 0
            while not stop.is_set():
                with lock:
                    if not pending: return
                    index, position = pending.popleft()
                end = min(total, (index + 1) * CHUNK)
                while position < end and not stop.is_set():
                    with lock: url = mirrors[slot % len(mirrors)] if mirrors else shared['mirror'] or entry['url']
                    got = 0
                    try:
                        with open_range(opener, url, position, end - 1, total) as response:
                            with lock: shared['mirror'] = shared['mirror'] or response.geturl()
                            while position < end and not stop.is_set():
                                block = response.read1(min(1024**2, end - position))
                                if not block: break
                                write(block, position)
                                position += len(block)
                                got += len(block)
                                with lock:
                                    shared['received'] += len(block)
                                    shared['moved'] = time.monotonic()
                                    reached[index] = position
                    except (RangeUnsupported, WriteFailed) as error:
                        with lock:
                            shared['fatal'] = error
                            pending.appendleft((index, position))
                        stop.set()
                        return
                    except (OSError, http.client.HTTPException, RuntimeError) as error:
                        # A failing or expired mirror link is dropped so the
                        # next request asks the official site for a mirror again.
                        with lock:
                            shared['error'] = error
                            if shared['mirror'] == url: shared['mirror'] = None
                            if not got and url in mirrors: mirrors.remove(url)
                    failures = 0 if got else failures + 1
                    if position < end and failures > RETRIES: break
                    if position < end and not got: stop.wait(min(BACKOFF * 2 ** failures, 15))
                with lock:
                    if position >= end:
                        done.add(index)
                        reached.pop(index, None)
                    else:
                        pending.appendleft((index, position))
                        return

        saved, saved_at = shared['received'], time.monotonic()

        def save():
            nonlocal saved, saved_at
            with lock:
                record = {'bytes': total, 'chunk': CHUNK, 'done': sorted(done),
                          'started': {str(index): position for index, position in reached.items()}}
                mark = shared['received']
            # Data reaches the disk before the record that vouches for it.
            os.fsync(fd)
            atomic(sidecar, record)
            saved, saved_at = mark, time.monotonic()

        threads = [threading.Thread(target=worker, args=(slot,), daemon=True, name='image-segment')
                   for slot in range(min(max(1, segments), len(pending)))]
        for thread in threads: thread.start()
        try:
            while True:
                stop.wait(0.2)
                check_cancel(cancel)
                with lock:
                    received, finished = shared['received'], len(done)
                    fatal, error, moved = shared['fatal'], shared['error'], shared['moved']
                if fatal is not None: raise fatal
                progress(received)
                now = time.monotonic()
                if received != saved and (finished == count or now - saved_at > 5): save()
                if finished == count: break
                if not any(thread.is_alive() for thread in threads):
                    raise RuntimeError('Image download was interrupted' + (f' ({error})' if error else '')
                                       + '; retry to resume')
                if now - moved > STALL:
                    raise RuntimeError('Image download stalled; retry to resume')
        finally:
            stop.set()
            for thread in threads: thread.join()
            if shared['received'] != saved: save()
    finally:
        os.close(fd)


def download_archive(entry, destination, cancel, progress):
    """Single-connection fallback for mirrors without byte-range support."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), MirrorRedirect())
    request = urllib.request.Request(trusted_url(entry['url']), headers={'User-Agent': 'AnvilDroid/0.1'})
    sidecar_path(destination).unlink(missing_ok=True)
    with opener.open(request, timeout=20) as response, destination.open('wb') as stream:
        os.chmod(destination, 0o600)
        if response.status != 200:
            raise RuntimeError('Image server did not return a complete archive')
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != entry['bytes']:
            raise RuntimeError('Image server returned an unexpected archive size')
        received = 0
        while True:
            check_cancel(cancel)
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
        # Set while the cache is moving or its relocated folder is missing.
        self.blocked = None
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
        self.library_path = self.root / 'library.json'
        self.library = {}
        if self.library_path.exists():
            self.check_file(self.library_path)
            self.library = json.loads(self.library_path.read_text())
            if not isinstance(self.library, dict): raise RuntimeError('Invalid image library')
        self.remember()
        # Only unpublished files in this root-owned cache, never runtime data.
        # Recent partial downloads stay so the next attempt can resume them.
        self.prune_partials(set(), time.time() - RESUME_SECONDS)

    def partial(self, entry):
        return self.root / ('.partial-' + entry['sha256'])

    def prune_partials(self, keep, older_than=None):
        for path in self.root.glob('.partial-*'):
            self.check_file(path)
            if RESUMABLE.fullmatch(path.name):
                if path.name[9:73] in keep: continue
                if older_than is not None and path.stat().st_mtime >= older_than: continue
            path.unlink(missing_ok=True)

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
            value['blocked'] = self.blocked
            value['available_images'] = [dict(copy.deepcopy(item), owner_uid=None) for item in self.library.values()
                                         if item['owner_uid'] == uid and self.available(item)]
            return value

    def available(self, item):
        return all((self.root / (image['sha256'] + '.zip')).is_file() for image in item['images'].values())

    def remember(self):
        if (self.state.get('status') != 'Ready' or not isinstance(self.state.get('images'), dict)
                or set(self.state['images']) != {'system', 'vendor'} or type(self.state.get('owner_uid')) is not int):
            return
        item = copy.deepcopy(self.state)
        key = hashlib.sha256((str(item['owner_uid']) + ':' + item['images']['system']['sha256'] + ':' +
                              item['images']['vendor']['sha256']).encode()).hexdigest()
        item['library_id'] = key
        self.library[key] = item
        atomic(self.library_path, self.library)

    def select(self, uid, identifier):
        with self.lock:
            self.check_writable()
            if self.state['status'] in ACTIVE: raise RuntimeError('Wait for the current image operation to finish')
            item = self.library.get(identifier) if isinstance(identifier, str) else None
            if not item or item['owner_uid'] != uid: raise RuntimeError('Image not found for this user')
            if not self.available(item): raise RuntimeError('Cached image files are missing; import or download again')
            self.state = copy.deepcopy(item)
            atomic(self.state_path, self.state)
            return self.status(uid)

    def rename(self, uid, identifier, name):
        with self.lock:
            item = self.library.get(identifier)
            if not item or item.get('owner_uid') != uid: raise RuntimeError('Image not found for this user')
            clean = str(name or '').strip()
            if not clean or len(clean) > 128: raise RuntimeError('Image name must be between 1 and 128 characters')
            item['custom_name'] = clean
            self.library[identifier] = item
            if self.state.get('library_id') == identifier: self.state['custom_name'] = clean
            atomic(self.library_path, self.library)
            atomic(self.state_path, self.state)
            return self.status(uid)

    def delete(self, uid, identifier, awaited):
        """Forget one image of this user; its archives go unless another image shares them."""
        with self.lock:
            item = self.library.get(identifier) if isinstance(identifier, str) else None
            if not item or item['owner_uid'] != uid: raise RuntimeError('Image not found for this user')
            self.discard({identifier}, awaited)
            return self.status(uid)

    def unfinished(self, awaited=()):
        """Files left by downloads that never completed: partial archives, and
        finished archives whose image pair was never completed."""
        listed = self.archives() | set(awaited)
        return [path for path in self.root.iterdir()
                if path.name.startswith('.partial-') or ARCHIVE.fullmatch(path.name) and path.name[:64] not in listed]

    def clear(self, uid, awaited):
        """Delete unfinished downloads. Complete images stay."""
        with self.lock:
            self.check_writable()
            if self.state['status'] in ACTIVE: raise RuntimeError('Wait for the current image download or import to finish')
            for path in self.unfinished(awaited): path.unlink(missing_ok=True)
            if self.state.get('owner_uid') == uid and self.state['status'] in ('Failed', 'Cancelled'):
                self.state = {'status': 'Idle', 'owner_uid': None}
                atomic(self.state_path, self.state)

    def archives(self):
        return {image['sha256'] for item in self.library.values() for image in item['images'].values()}

    def discard(self, identifiers, awaited):
        """`awaited` maps archive hashes to the device that still has to be set up from them."""
        self.check_writable()
        if self.state['status'] in ACTIVE: raise RuntimeError('Wait for the current image download or import to finish')
        kept = {image['sha256'] for key, item in self.library.items() if key not in identifiers
                for image in item['images'].values()}
        unused = self.archives() - kept
        waiting = sorted({awaited[sha] for sha in unused if sha in awaited})
        if waiting:
            raise RuntimeError(', '.join(waiting) + ' still has to be set up from this image. Set it up or delete it first.')
        # The record goes first: a leftover archive is harmless, a listed image without one is not.
        for key in identifiers: del self.library[key]
        atomic(self.library_path, self.library)
        if self.state.get('library_id') in identifiers:
            self.state = {'status': 'Idle', 'owner_uid': None}
            atomic(self.state_path, self.state)
        for sha in unused: (self.root / (sha + '.zip')).unlink(missing_ok=True)

    def block(self, reason):
        with self.lock:
            if self.state['status'] in ACTIVE: raise RuntimeError('Wait for the current image download or import to finish')
            if self.blocked: raise RuntimeError(self.blocked)
            self.blocked = reason

    def check_writable(self):
        if self.blocked: raise RuntimeError(self.blocked)

    def start(self, uid, variant, segments=SEGMENTS):
        with self.lock:
            self.check_writable()
            if self.state['status'] in ACTIVE: raise RuntimeError('An image download is already in progress')
            missing = sum(variant[kind]['bytes'] - resumed_bytes(self.partial(variant[kind]), variant[kind]['bytes'])
                          for kind in ('system', 'vendor')
                          if not (self.root / (variant[kind]['sha256'] + '.zip')).exists())
            free = shutil.disk_usage(self.root).free
            if free < missing + MARGIN:
                raise RuntimeError(f'Insufficient cache space: need {(missing + MARGIN) / 1024**3:.2f} GiB, available {free / 1024**3:.2f} GiB at {self.root}')
            self.cancelled.clear()
            self.state = {'id': uuid.uuid4().hex, 'owner_uid': uid, 'status': 'Downloading',
                          'flavor': variant['flavor'], 'download_bytes': variant['download_bytes'],
                          'received_bytes': 0, 'phase': 'Starting download', 'error': None, 'images': {}}
            atomic(self.state_path, self.state)
            self.thread = threading.Thread(target=self.run, args=(copy.deepcopy(variant), segments), daemon=True, name='image-download')
            self.thread.start()
            return self.status(uid)

    def import_custom(self, uid, fd, name=''):
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
            self.check_writable()
            if self.state['status'] in ACTIVE: raise RuntimeError('An image download or import is already in progress')
            if shutil.disk_usage(self.root).free < size + MARGIN:
                raise RuntimeError('Insufficient image cache space')
            self.cancelled.clear()
            clean_name = str(name or '').strip()[:128]
            self.state = {'id':uuid.uuid4().hex, 'owner_uid':uid, 'status':'Verifying',
                          'flavor':'CUSTOM', 'download_bytes':size, 'received_bytes':0,
                          'phase':'Inspecting custom images', 'error':None, 'images':{},
                          'custom_name': clean_name or 'Custom image'}
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
        published = []
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
                    allowed_checksum_files = {'sha256sum', 'sha256sum.txt', 'sha256sums', 'sha256sums.txt', 'SHA256SUMS'}
                    for entry in archive.infolist():
                        parts = entry.filename.split('/')
                        if entry.filename.startswith('/') or '\\' in entry.filename or '..' in parts:
                            raise RuntimeError('Unsafe ZIP entry path')
                        if entry.is_dir(): continue
                        name = parts[-1]
                        if name in allowed_checksum_files:
                            # Some Waydroid releases bundle a checksum manifest beside images.
                            # It is metadata only; image bytes still receive independent hashing.
                            continue
                        if name not in ('system.img', 'vendor.img') or name in entries:
                            raise RuntimeError('Select a ZIP containing system.img and vendor.img (folders and checksum manifests are allowed)')
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
                    else:
                        os.replace(temporary, destination)
                        published.append(destination)
                    temporary = None
                    completed += written
                # Ask Waydroid to initialize the validated pair in an isolated
                # namespace. This keeps image parsing and platform setup in the
                # Waydroid CLI instead of duplicating its behavior here.
                cli_directory = Path(tempfile.mkdtemp(prefix='.waydroid-images-', dir=self.root))
                try:
                    cli_images = {}
                    for kind, entry in images.items():
                        path = cli_directory / (kind + '.img')
                        with zipfile.ZipFile(self.root / (entry['sha256'] + '.zip')) as archive, \
                                archive.open(kind + '.img') as source, path.open('wb') as target:
                            shutil.copyfileobj(source, target, 1024 * 1024)
                        cli_images[kind] = path
                    self.update(phase='Initializing custom images with Waydroid CLI')
                    waydroid_cli_import(cli_images)
                finally:
                    shutil.rmtree(cli_directory, ignore_errors=True)
                for source, before in zip(sources, snapshots):
                    after = os.fstat(source.fileno())
                    # URL imports use a resumable cache file. Its filesystem
                    # timestamps may change without changing bytes; size is
                    # the relevant mutation check because the importer hashes
                    # every extracted image before publishing it.
                    if before.st_size != after.st_size:
                        raise RuntimeError('Source images changed during import; select them again')
            with self.lock:
                check_cancel(self.cancelled)
                self.state.update(status='Ready', phase='Custom images imported; not installed or officially verified',
                                  images=images, image_bytes=total)
                sync(self.root)
                atomic(self.state_path, self.state)
                self.remember()
        except Exception as error:
            for path in published:
                path.unlink(missing_ok=True)
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

    def run(self, variant, segments=SEGMENTS):
        try:
            completed, images = 0, {}
            # At most one unfinished image pair is kept for resuming.
            self.prune_partials({variant[kind]['sha256'] for kind in ('system', 'vendor')})
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
                    partial = self.partial(entry)
                    resumed = resumed_bytes(partial, entry['bytes'])
                    free = shutil.disk_usage(self.root).free
                    if free < entry['bytes'] - resumed + MARGIN: raise RuntimeError('Insufficient image cache space; free space and retry')
                    self.update(status='Downloading', received_bytes=completed + resumed,
                                phase=f'Resuming {kind} archive download' if resumed else f'Downloading {kind} archive')
                    report = lambda size: self.update(received_bytes=completed + size)
                    try:
                        download_segmented(entry, partial, self.cancelled, report, segments)
                    except RangeUnsupported:
                        self.update(phase=f'Downloading {kind} archive')
                        download_archive(entry, partial, self.cancelled, report)
                    self.update(status='Verifying', phase=f'Verifying {kind} SHA-256 and archive layout')
                    try:
                        images[kind] = archive_info(partial, entry, kind, self.cancelled)
                    except Cancelled: raise
                    except (RuntimeError, zipfile.BadZipFile):
                        # Corrupt bytes cannot be resumed; the next attempt starts clean.
                        discard_partial(partial)
                        raise
                    check_cancel(self.cancelled)
                    os.replace(partial, destination)
                    sidecar_path(partial).unlink(missing_ok=True)
                    sync(self.root)
                completed += entry['bytes']
                self.update(received_bytes=completed)
            with self.lock:
                check_cancel(self.cancelled)
                self.state.update(status='Ready', phase='Images downloaded and verified; not installed', images=images,
                                  image_bytes=sum(image['image_bytes'] for image in images.values()))
                atomic(self.state_path, self.state)
                self.remember()
        except Exception as error:
            with self.lock:
                self.state.update(status='Cancelled' if isinstance(error, Cancelled) else 'Failed', error=str(error)[-1024:])
                atomic(self.state_path, self.state)
