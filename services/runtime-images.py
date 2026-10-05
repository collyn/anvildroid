"""Controller-owned, offline image preparation. No mounts or Android start here."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


class ImageBackend:
    def __init__(self, store, source):
        self.root = store / 'instances'
        self.source = source
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.check_directory(self.root)

    @staticmethod
    def check_directory(path):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or path.resolve() != path:
            raise RuntimeError('Unsafe image directory')

    def location(self, identifier):
        if len(identifier) != 34 or not identifier.startswith('r-') or any(c not in '0123456789abcdef' for c in identifier[2:]):
            raise RuntimeError('Invalid runtime ID')
        return self.root / identifier

    def inspect(self, identifier):
        directory = self.location(identifier) / 'prepared'
        if not directory.exists():
            return None
        self.check_directory(directory.parent)
        self.check_directory(directory)
        manifest_path = directory / 'manifest.json'
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RuntimeError('Invalid image manifest')
        manifest = json.loads(manifest_path.read_text())
        if set(manifest) != {'version', 'runtime_id', 'images'} or manifest['version'] != 1 or manifest['runtime_id'] != identifier or set(manifest['images']) != {'system.img', 'vendor.img'}:
            raise RuntimeError('Image manifest mismatch')
        for name, expected in manifest['images'].items():
            path = directory / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_nlink != 1 or digest(path) != expected:
                raise RuntimeError('Prepared image failed integrity verification')
        return manifest

    def source_status(self):
        try:
            self.check_directory(self.source)
            total = 0
            for name in ('system.img', 'vendor.img'):
                info = (self.source / name).lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size == 0:
                    raise RuntimeError('Android images must be nonempty root-owned regular files')
                total += info.st_size
            return {'available': True, 'bytes': total, 'detail': 'Installed Android images found'}
        except FileNotFoundError:
            return {'available': False, 'bytes': 0, 'detail': 'Android images are missing. Install Waydroid and complete its initial image download first.'}
        except (OSError, RuntimeError):
            return {'available': False, 'bytes': 0, 'detail': 'Android images cannot be read safely. Check the Waydroid installation and image permissions.'}

    def prepare(self, identifier):
        current = self.inspect(identifier)
        if current:
            return current
        status = self.source_status()
        if not status['available']:
            raise RuntimeError(status['detail'])
        destination = self.location(identifier)
        destination.mkdir(mode=0o700, exist_ok=True)
        self.check_directory(destination)
        # Preparation never mounts anything. Reclaim only its private unpublished
        # staging directories left by a killed controller, never the published image.
        for abandoned in destination.glob('.prepare-*'):
            self.check_directory(abandoned)
            shutil.rmtree(abandoned)
        sources = [self.source / name for name in ('system.img', 'vendor.img')]
        for source in sources:
            info = source.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise RuntimeError('Source images must be root-owned regular files')
        needed = sum(p.stat().st_size for p in sources)
        free = shutil.disk_usage(destination).free
        required = needed + 64 * 1024 * 1024
        if free < required:
            raise RuntimeError(f'Insufficient space for independent image copies: need {required / 1024**3:.2f} GiB, available {free / 1024**3:.2f} GiB. Change storage location to a disk with more space, then retry preparation.')
        staging = Path(tempfile.mkdtemp(prefix='.prepare-', dir=destination))
        try:
            hashes = {}
            for source in sources:
                before = source.stat()
                with source.open('rb') as src, (staging / source.name).open('xb') as dst:
                    os.chmod(dst.name, 0o600)
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                after = source.stat()
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise RuntimeError('Source image changed during preparation')
                hashes[source.name] = digest(staging / source.name)
                if digest(source) != hashes[source.name]:
                    raise RuntimeError('Image copy verification failed')
            manifest = {'version': 1, 'runtime_id': identifier, 'images': hashes}
            with (staging / 'manifest.json').open('x') as stream:
                os.chmod(stream.name, 0o600)
                json.dump(manifest, stream)
                stream.flush()
                os.fsync(stream.fileno())
            self.sync_directory(staging)
            os.rename(staging, destination / 'prepared')
            self.sync_directory(destination)
            return manifest
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def sync_directory(path):
        fd = os.open(path, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
