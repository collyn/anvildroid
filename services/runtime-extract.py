"""Offline extraction of verified cached images into an unpublished directory."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import threading
import zipfile

spec = importlib.util.spec_from_file_location('image_download', Path(__file__).with_name('runtime-download.py'))
download = importlib.util.module_from_spec(spec)
spec.loader.exec_module(download)


def extract_pair(cache, images, destination, identifier, cancel=None):
    cancel = cancel or threading.Event()
    if destination.exists(): raise RuntimeError('Image destination already exists')
    verified = {}
    for kind in ('system', 'vendor'):
        entry = images[kind]
        checksum = entry['sha256']
        if len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum):
            raise RuntimeError('Invalid archive checksum')
        path = cache / (checksum + '.zip')
        download.Downloads.check_file(path)
        expected = {'sha256': checksum, 'bytes': entry['archive_bytes'], 'filename': entry['filename']}
        verified[kind] = download.archive_info(path, expected, kind, cancel)
    required = sum(info['image_bytes'] for info in verified.values()) + download.MARGIN
    if shutil.disk_usage(destination.parent).free < required:
        raise RuntimeError(f'Insufficient space for unpacked images: need {required / 1024**3:.2f} GiB')
    staging = Path(tempfile.mkdtemp(prefix='.extract-', dir=destination.parent))
    try:
        hashes = {}
        for kind, info in verified.items():
            path = cache / (info['sha256'] + '.zip')
            digest, written = hashlib.sha256(), 0
            with zipfile.ZipFile(path) as archive, archive.open(kind + '.img') as source, (staging / (kind + '.img')).open('xb') as output:
                os.fchmod(output.fileno(), 0o600)
                while block := source.read(1024**2):
                    download.check_cancel(cancel)
                    written += len(block)
                    if written > info['image_bytes']: raise RuntimeError('Image exceeds verified unpacked size')
                    output.write(block)
                    digest.update(block)
                if written != info['image_bytes']: raise RuntimeError('Incomplete image extraction')
                output.flush()
                os.fsync(output.fileno())
            hashes[kind + '.img'] = digest.hexdigest()
        manifest = {'version': 1, 'runtime_id': identifier, 'images': hashes}
        download.atomic(staging / 'manifest.json', manifest)
        download.check_cancel(cancel)
        os.rename(staging, destination)
        download.sync(destination.parent)
        return manifest
    finally:
        if staging.exists(): shutil.rmtree(staging)
